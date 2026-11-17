import asyncio

import discord
from discord import app_commands

from bot.charts import weight_comparison_bar
from bot.permissions import check_access
from core.config import Settings
from core.logger import get_logger
from core.portfolio import load_portfolio, managed_tickers, split_by_managed
from kiwoom.client import KiwoomClient
from kiwoom.models import Balance, Holding

log = get_logger(__name__)


def _strategy_stats(managed: list[Holding]) -> tuple[float, float, float, float]:
    """관리 대상 종목만의 (평가액, 매입액, 손익, 수익률).

    Balance의 total_eval·return_rate는 계좌 전체 기준이라 전략 외 종목의
    성과가 섞인다. 전략 성과는 보유 종목에서 직접 합산해야 한다.
    """
    eval_amt = sum(h.eval_amount for h in managed)
    purchase = sum(h.avg_price * h.quantity for h in managed)
    pnl = sum(h.profit_loss for h in managed)
    rate = (pnl / purchase * 100) if purchase else 0.0
    return eval_amt, purchase, pnl, rate


def _balance_embed(
    balance: Balance, managed: list[Holding], outside: list[Holding]
) -> discord.Embed:
    eval_amt, purchase, pnl, rate = _strategy_stats(managed)
    color = 0x00C853 if pnl >= 0 else 0xD32F2F

    embed = discord.Embed(
        title="📊 계좌 현황",
        description="아래 수치는 **ISA 월배당 전략** 기준입니다." if outside else None,
        color=color,
    )
    embed.add_field(name="전략 평가금액", value=f"{eval_amt:,.0f}원", inline=True)
    embed.add_field(name="전략 매입금액", value=f"{purchase:,.0f}원", inline=True)
    embed.add_field(
        name="매수 가능 금액", value=f"{balance.buyable:,.0f}원", inline=True
    )
    embed.add_field(name="전략 평가손익", value=f"{pnl:,.0f}원", inline=True)
    embed.add_field(name="전략 수익률", value=f"{rate:+.2f}%", inline=True)

    if managed:
        embed.add_field(
            name="보유 종목 (전략)",
            value="\n".join(
                f"• {h.name} ({h.ticker}) — {h.quantity}주, {h.return_rate:+.2f}%"
                for h in managed
            ),
            inline=False,
        )

    # 전략 외는 참고용으로만. 비중·수익률 계산에는 들어가지 않는다.
    if outside:
        out_eval = sum(h.eval_amount for h in outside)
        out_pnl = sum(h.profit_loss for h in outside)
        lines = [
            f"• {h.name} ({h.ticker}) — {h.quantity}주, {h.return_rate:+.2f}%"
            for h in outside[:12]
        ]
        if len(outside) > 12:
            lines.append(f"…외 {len(outside) - 12}종목")
        lines.append(f"\n소계 {out_eval:,.0f}원 · 손익 {out_pnl:,.0f}원")
        embed.add_field(
            name=f"👀 전략 외 보유 {len(outside)}종목 — 참고",
            value="\n".join(lines),
            inline=False,
        )
        embed.add_field(
            name="계좌 전체",
            value=(
                f"평가 {balance.total_eval:,.0f}원 · "
                f"손익 {balance.profit_loss:,.0f}원 · "
                f"수익률 {balance.return_rate:+.2f}%"
            ),
            inline=False,
        )
    return embed


def _build_comparison_items(managed: list[Holding], portfolio: dict) -> list[dict]:
    """차트 입력용 데이터 — 포트폴리오 각 종목의 목표/현재 비중.

    분모는 계좌 전체가 아니라 **관리 대상 평가액 합**이다. 계좌 전체를 쓰면
    전략 외 종목이 분모만 키워 모든 종목이 목표에 미달하는 것처럼 보인다.
    """
    held = {h.ticker: h for h in managed}
    total = sum(h.eval_amount for h in managed) or 1.0  # 0 나눗셈 방지
    return [
        {
            "ticker": p["ticker"],
            "name": p["name"],
            "target": p["weight"],
            "current": (held[p["ticker"]].eval_amount if p["ticker"] in held else 0.0)
            / total,
            "floor": p["floor"],
        }
        for p in portfolio["holdings"]
    ]


def register(
    tree: app_commands.CommandTree, settings: Settings, kiwoom: KiwoomClient
) -> None:
    @tree.command(
        name="계좌현황", description="현재 잔고, 수익률, 보유 종목 + 비중 비교 차트"
    )
    async def _cmd(interaction: discord.Interaction) -> None:
        if not await check_access(interaction, settings):
            return
        await interaction.response.defer(thinking=True)
        try:
            balance = await kiwoom.get_balance()
        except Exception as e:
            log.exception("계좌현황 조회 실패")
            await interaction.followup.send(f"❌ 조회 실패: {e}", ephemeral=True)
            return

        # portfolio.json을 못 읽어도 계좌 조회 결과는 보여준다.
        # (관리 대상을 모르면 전부 전략 종목으로 간주 — 예전과 같은 화면)
        try:
            portfolio = load_portfolio()
        except (OSError, ValueError):
            log.exception("portfolio.json 읽기 실패 — 전략 구분 없이 표시합니다.")
            portfolio = None

        if portfolio is None:
            managed, outside = balance.holdings, []
        else:
            managed, outside = split_by_managed(
                balance.holdings, managed_tickers(portfolio)
            )
        embed = _balance_embed(balance, managed, outside)

        # 괴리율 비교 차트
        chart_file = None
        try:
            if portfolio is None:
                raise RuntimeError("포트폴리오 정의 없음")
            items = _build_comparison_items(managed, portfolio)
            buf = await asyncio.to_thread(weight_comparison_bar, items)
            chart_file = discord.File(buf, filename="weight_compare.png")
            embed.set_image(url="attachment://weight_compare.png")
        except Exception:
            log.exception("비중 비교 차트 생성 실패")

        if chart_file is not None:
            await interaction.followup.send(embed=embed, file=chart_file)
        else:
            await interaction.followup.send(embed=embed)
