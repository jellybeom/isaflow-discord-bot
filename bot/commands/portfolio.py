import asyncio
import json
from pathlib import Path

import discord
from discord import app_commands

from bot.charts import portfolio_donut
from bot.permissions import check_access
from core.config import Settings
from kiwoom.client import KiwoomClient

PORTFOLIO_PATH = Path(__file__).resolve().parents[2] / "data" / "portfolio.json"

FLOOR_EMOJI = {"1F": "🛡️", "2F": "🏢", "3F": "💰"}

FLOOR_COLOR = {"1F": "🟧", "2F": "🟩", "3F": "🟪"}
BAR_EMPTY = "⬛"
BAR_LENGTH = 10  # 100% 기준 막대 길이 (이모지는 한 칸이 크니까 짧게)


def _bar(floor_key: str, weight: float) -> str:
    """0.0~1.0 비율을 BAR_LENGTH 칸의 이모지 막대로 변환."""
    filled = round(weight * BAR_LENGTH)
    color = FLOOR_COLOR.get(floor_key, "⬜")
    return color * filled + BAR_EMPTY * (BAR_LENGTH - filled)


def _portfolio_embed(data: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 {data['title']}",
        description=data["subtitle"],
        color=0x1976D2,
    )

    # 비율 차트 (층별 합계)
    chart_lines = []
    for floor_key, structure in data["structure"].items():
        chart_lines.append(
            f"`{floor_key}` **{structure['name']}** · {structure['weight']*100:.0f}%\n"
            f"{_bar(floor_key, structure['weight'])}"
        )
    embed.add_field(name="비율 차트", value="\n\n".join(chart_lines), inline=False)

    # 종목 목록 — 층(floor)별로 그룹핑
    holdings_by_floor: dict[str, list[dict]] = {}
    for item in data["holdings"]:
        holdings_by_floor.setdefault(item["floor"], []).append(item)

    for floor_key, structure in data["structure"].items():
        items = holdings_by_floor.get(floor_key, [])
        emoji = FLOOR_EMOJI.get(floor_key, "")
        header = f"{emoji} **{floor_key} {structure['name']}** · 합계 {structure['weight']*100:.0f}%"
        lines = [header, f"_{structure['detail']}_", ""]
        for h in items:
            lines.append(
                f"• **{h['name']}** ({h['ticker']})\n"
                f"   {h['role']} — {h['weight']*100:.0f}% · 분배율 {h['yield']:.2f}%"
            )
        embed.add_field(name="\u200b", value="\n".join(lines), inline=False)

    # 핵심 수치 — name을 zero-width space로 두면 위 필드들과 동일한 간격
    embed.add_field(
        name="\u200b",
        value=(
            f"📊 **핵심 수치**\n"
            f"가중평균 분배율: **{data['weighted_yield']:.2f}%**\n"
            f"가중평균 보수: **{data['weighted_fee']:.2f}%**\n"
            f"종목 수: **{len(data['holdings'])}개** (전부 월배당)"
        ),
        inline=False,
    )
    return embed


def register(tree: app_commands.CommandTree, settings: Settings, kiwoom: KiwoomClient) -> None:
    @tree.command(name="포트폴리오", description="현재 설정된 포트폴리오 구성 조회")
    async def _cmd(interaction: discord.Interaction) -> None:
        if not await check_access(interaction, settings):
            return

        await interaction.response.defer(thinking=True)
        data = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))

        # 도넛 차트는 CPU 작업 — 별도 스레드에서 생성
        try:
            buf = await asyncio.to_thread(portfolio_donut, data, None)
            chart_file = discord.File(buf, filename="portfolio_donut.png")
        except Exception:
            chart_file = None

        embed = _portfolio_embed(data)
        if chart_file is not None:
            embed.set_image(url="attachment://portfolio_donut.png")
            await interaction.followup.send(embed=embed, file=chart_file)
        else:
            await interaction.followup.send(embed=embed)
