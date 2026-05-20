import asyncio
import json
from pathlib import Path

import discord
from discord import app_commands

from bot.charts import weight_comparison_bar
from bot.permissions import check_access
from core.config import Settings
from core.logger import get_logger
from kiwoom.client import KiwoomClient
from kiwoom.models import Balance

log = get_logger(__name__)

PORTFOLIO_PATH = Path(__file__).resolve().parents[2] / "data" / "portfolio.json"


def _balance_embed(balance: Balance) -> discord.Embed:
    color = 0x00C853 if balance.profit_loss >= 0 else 0xD32F2F
    embed = discord.Embed(title="📊 계좌 현황", color=color)
    embed.add_field(name="총 평가금액", value=f"{balance.total_eval:,.0f}원", inline=True)
    embed.add_field(name="총 매입금액", value=f"{balance.total_purchase:,.0f}원", inline=True)
    embed.add_field(name="매수 가능 금액", value=f"{balance.buyable:,.0f}원", inline=True)
    embed.add_field(name="평가손익", value=f"{balance.profit_loss:,.0f}원", inline=True)
    embed.add_field(name="수익률", value=f"{balance.return_rate:.2f}%", inline=True)

    if balance.holdings:
        lines = [
            f"• {h.name} ({h.ticker}) — {h.quantity}주, {h.return_rate:+.2f}%"
            for h in balance.holdings
        ]
        embed.add_field(name="보유 종목", value="\n".join(lines), inline=False)
    return embed


def _build_comparison_items(balance: Balance, portfolio: dict) -> list[dict]:
    """차트 입력용 데이터 — 포트폴리오의 각 종목별 목표/현재 비중을 계산."""
    held = {h.ticker: h for h in balance.holdings}
    total = balance.total_eval or 1.0  # 0 나눗셈 방지
    items = []
    for p in portfolio["holdings"]:
        h = held.get(p["ticker"])
        cur_amt = h.eval_amount if h else 0.0
        items.append({
            "ticker": p["ticker"],
            "name": p["name"],
            "target": p["weight"],
            "current": cur_amt / total,
            "floor": p["floor"],
        })
    return items


def register(tree: app_commands.CommandTree, settings: Settings, kiwoom: KiwoomClient) -> None:
    @tree.command(name="계좌현황", description="현재 잔고, 수익률, 보유 종목 + 비중 비교 차트")
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

        embed = _balance_embed(balance)

        # 괴리율 비교 차트
        chart_file = None
        try:
            portfolio = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
            items = _build_comparison_items(balance, portfolio)
            buf = await asyncio.to_thread(weight_comparison_bar, items)
            chart_file = discord.File(buf, filename="weight_compare.png")
            embed.set_image(url="attachment://weight_compare.png")
        except Exception:
            log.exception("비중 비교 차트 생성 실패")

        if chart_file is not None:
            await interaction.followup.send(embed=embed, file=chart_file)
        else:
            await interaction.followup.send(embed=embed)
