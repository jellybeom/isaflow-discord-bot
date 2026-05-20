"""/매수 — 포트폴리오 비중과 현재 보유량을 비교해 리밸런싱 매수."""
import asyncio
import json
from pathlib import Path

import discord
from discord import app_commands

from bot.permissions import check_access
from bot.views import BuyConfirmView
from core.config import Settings
from core.logger import get_logger
from core.planner import PlanItem, calculate_buy_plan
from kiwoom.client import KiwoomClient

log = get_logger(__name__)

PORTFOLIO_PATH = Path(__file__).resolve().parents[2] / "data" / "portfolio.json"


async def _gather_holdings(kiwoom: KiwoomClient) -> tuple[list[dict], float]:
    """포트폴리오 종목별로 목표비중·매도1호가·현재 보유액을 모은다.

    Returns:
        (holdings, buyable)
        - holdings: calculate_buy_plan에 넘길 dict 리스트
        - buyable: 계좌의 매수 가능 금액
    """
    portfolio = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    balance = await kiwoom.get_balance()

    # 보유 종목을 ticker로 빠르게 찾도록 매핑
    held = {h.ticker: h for h in balance.holdings}

    holdings = []
    for item in portfolio["holdings"]:
        ticker = item["ticker"]
        price = await kiwoom.get_ask_price(ticker)
        h = held.get(ticker)
        hold_amt = (h.current_price * h.quantity) if h else 0.0
        holdings.append({
            "ticker": ticker,
            "name": item["name"],
            "weight": item["weight"],
            "price": price,
            "hold_amt": hold_amt,
        })
    return holdings, balance.buyable


def _plan_embed(
    plan: list[PlanItem], amount: int, remaining: int, buyable: float
) -> discord.Embed:
    buyable_items = [p for p in plan if p.quantity > 0]
    skipped = [p for p in plan if p.quantity == 0]
    total_cost = sum(p.cost for p in buyable_items)

    lines = [
        f"• **{p.name}** ({p.ticker})\n"
        f"   {p.quantity}주 × {p.price:,}원 (매도1호가) = {p.cost:,}원"
        for p in buyable_items
    ]
    embed = discord.Embed(
        title="🛒 매수 확인 — 리밸런싱",
        description="\n".join(lines) if lines else "매수할 종목이 없습니다.",
        color=0xFFA000,
    )
    embed.add_field(name="요청 금액", value=f"{amount:,}원", inline=True)
    embed.add_field(name="실제 매수 금액", value=f"{total_cost:,}원", inline=True)
    embed.add_field(name="잔여", value=f"{remaining:,}원", inline=True)
    if skipped:
        embed.add_field(
            name="이번 미매수",
            value=", ".join(p.name for p in skipped),
            inline=False,
        )
    # 입력 금액이 매수 가능 금액을 넘으면 경고
    if amount > buyable:
        embed.add_field(
            name="⚠️ 경고",
            value=(
                f"요청 금액({amount:,}원)이 매수 가능 금액({buyable:,.0f}원)을 "
                f"초과합니다. 일부 주문이 증거금 부족으로 거부될 수 있습니다."
            ),
            inline=False,
        )
    return embed


def register(tree: app_commands.CommandTree, settings: Settings, kiwoom: KiwoomClient) -> None:
    @tree.command(name="매수", description="포트폴리오 비중에 맞춰 리밸런싱 매수")
    @app_commands.describe(amount="매수 금액 (원)")
    async def _cmd(interaction: discord.Interaction, amount: int) -> None:
        if not await check_access(interaction, settings):
            return
        if amount <= 0:
            await interaction.response.send_message("❌ 금액은 0보다 커야 합니다.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # 1) 보유 현황 + 매도1호가 수집
        try:
            holdings, buyable = await _gather_holdings(kiwoom)
        except Exception as e:
            log.exception("매수 준비 실패")
            await interaction.followup.send(f"❌ 계좌/시세 조회 실패: {e}", ephemeral=True)
            return

        # 2) 리밸런싱 계획 계산
        plan, remaining = calculate_buy_plan(holdings, amount)
        buyable_items = [p for p in plan if p.quantity > 0]
        embed = _plan_embed(plan, amount, remaining, buyable)

        if not buyable_items:
            await interaction.followup.send(embed=embed)
            return

        # 3) 확인 → 매수 실행 → 체결 확인
        async def on_confirm(inter: discord.Interaction) -> None:
            # 3-1) 종목별 주문 접수
            ordered = []   # [(PlanItem, 주문번호), ...]
            order_lines = []
            for p in buyable_items:
                try:
                    ord_no = await kiwoom.buy_limit(p.ticker, p.quantity, p.price)
                    ordered.append((p, ord_no))
                    order_lines.append(f"✅ {p.name} {p.quantity}주 @ {p.price:,}원 (주문 {ord_no})")
                except Exception as e:
                    log.exception("매수 주문 실패: %s", p.ticker)
                    order_lines.append(f"❌ {p.name}: 주문 실패 — {e}")

            order_embed = discord.Embed(
                title="📨 주문 접수 완료",
                description="\n".join(order_lines),
                color=0x42A5F5,
            )
            if not ordered:
                order_embed.title = "❌ 주문 실패"
                order_embed.color = 0xEF5350
                await inter.followup.send(embed=order_embed)
                return

            order_embed.set_footer(text="3초 후 체결 결과를 확인합니다…")
            await inter.followup.send(embed=order_embed)

            # 3-2) 체결 대기
            await asyncio.sleep(3)

            # 3-3) 미체결 조회 → 체결 판정
            try:
                unexecuted = await kiwoom.get_unexecuted()
            except Exception as e:
                log.exception("미체결 조회 실패")
                await inter.followup.send(
                    f"⚠️ 주문은 접수됐으나 체결 확인에 실패했습니다: {e}\n"
                    f"키움 앱에서 직접 확인해 주세요.",
                    ephemeral=True,
                )
                return

            result_lines = []
            for p, ord_no in ordered:
                left = unexecuted.get(ord_no)
                if left is None or left == 0:
                    result_lines.append(f"✅ {p.name} — {p.quantity}주 전량 체결")
                elif left < p.quantity:
                    done = p.quantity - left
                    result_lines.append(
                        f"⚠️ {p.name} — {done}주 체결 / {left}주 미체결"
                    )
                else:
                    result_lines.append(
                        f"⏳ {p.name} — {p.quantity}주 미체결 (호가 대기 중)"
                    )

            result_embed = discord.Embed(
                title="📊 체결 결과",
                description="\n".join(result_lines),
                color=0x66BB6A,
            )
            if any(o[1] in unexecuted and unexecuted[o[1]] > 0 for o in ordered):
                result_embed.set_footer(
                    text="미체결 주문은 키움 앱에서 확인·정정·취소할 수 있습니다."
                )
            await inter.followup.send(embed=result_embed)

        view = BuyConfirmView(owner_id=settings.discord_owner_id, on_confirm=on_confirm)
        await interaction.followup.send(embed=embed, view=view)
