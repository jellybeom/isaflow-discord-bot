"""/배당 — ISA 계좌 분배금 내역 조회 및 다음달 예상 배당."""
import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands

from bot.charts import dividend_trend_bar
from bot.permissions import check_access
from core.config import Settings
from core.logger import get_logger
from kiwoom.client import KiwoomClient

log = get_logger(__name__)

PORTFOLIO_PATH = Path(__file__).resolve().parents[2] / "data" / "portfolio.json"

# 키움 적요명에서 분배금 입금을 식별하는 키워드
DIVIDEND_REMARK = "수익분배금입금"

# 전체 조회 시 시작일 (키움 보관 한도 안에서 충분히 과거)
ALL_START_DATE = "20200101"


def _parse_period(arg: str | None) -> tuple[str, str, str]:
    """사용자 입력을 (시작일, 종료일, 기간 라벨)로 변환.

    지원하는 형식:
      - None         → 이번 달
      - "전체"       → 2020년부터 오늘
      - "2026-04"    → 그 달
      - "2026-01~2026-04" → 그 기간 (시작월~종료월)
    """
    today = date.today()

    if arg is None or arg.strip() == "":
        # 이번 달
        start = today.replace(day=1)
        return start.strftime("%Y%m%d"), today.strftime("%Y%m%d"), f"{today.year}년 {today.month}월"

    arg = arg.strip()

    if arg == "전체":
        return ALL_START_DATE, today.strftime("%Y%m%d"), "전체 기간"

    # "2026-04" 또는 "2026-01~2026-04"
    if "~" in arg:
        a, b = [s.strip() for s in arg.split("~", 1)]
        s = datetime.strptime(a, "%Y-%m").date().replace(day=1)
        # 종료월의 마지막 날
        eb = datetime.strptime(b, "%Y-%m").date()
        next_month = (eb.replace(day=28) + timedelta(days=4)).replace(day=1)
        e = next_month - timedelta(days=1)
        return s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), f"{a} ~ {b}"

    # 단일 월
    s = datetime.strptime(arg, "%Y-%m").date().replace(day=1)
    next_month = (s.replace(day=28) + timedelta(days=4)).replace(day=1)
    e = next_month - timedelta(days=1)
    return s.strftime("%Y%m%d"), e.strftime("%Y%m%d"), arg


def _group_by_month(divs: list, name_map: dict) -> dict:
    """분배금 거래를 월별·종목별로 집계."""
    # {YYYY-MM: {ticker: 합계금액}}
    result: dict[str, dict[str, int]] = {}
    for tx in divs:
        if len(tx.date) != 8:
            continue
        ym = f"{tx.date[:4]}-{tx.date[4:6]}"
        result.setdefault(ym, {})
        # 같은 달에 같은 종목 분배금이 여러 건이면 합산
        result[ym][tx.ticker] = result[ym].get(tx.ticker, 0) + tx.amount
    return result


def _estimate_next_month(divs: list, holdings_qty: dict[str, int]) -> dict[str, int]:
    """종목별 직전 1회 분배금 입금액을 그대로 다음달 예상치로 사용.

    분배금은 매월 변동하므로 어디까지나 참고치.
    Returns: {ticker: 예상금액}
    """
    # 종목별 가장 최근 분배금 거래 찾기
    latest: dict[str, int] = {}
    for tx in sorted(divs, key=lambda t: t.date, reverse=True):
        if tx.ticker and tx.ticker not in latest:
            latest[tx.ticker] = tx.amount

    # 현재 보유 중인 종목만 예상치 계산
    # (직전 분배 시점과 현재 보유수량이 달라도 일단 단순화: 직전 금액 그대로 사용)
    # 더 정교하게 하려면 직전 시점 보유수량을 역산해야 하지만, 참고치이므로 간단히 처리.
    return {t: amt for t, amt in latest.items() if holdings_qty.get(t, 0) > 0}


def register(tree: app_commands.CommandTree, settings: Settings, kiwoom: KiwoomClient) -> None:
    @tree.command(name="배당", description="분배금 내역 조회 (예: /배당, /배당 2026-04, /배당 전체)")
    @app_commands.describe(기간="조회 기간 (생략=이번달, '전체', 'YYYY-MM', 'YYYY-MM~YYYY-MM')")
    async def _cmd(interaction: discord.Interaction, 기간: str | None = None) -> None:
        if not await check_access(interaction, settings):
            return

        # 1) 기간 파싱
        try:
            start_date, end_date, period_label = _parse_period(기간)
        except Exception:
            await interaction.response.send_message(
                "❌ 기간 형식이 올바르지 않습니다. 예) `/배당`, `/배당 2026-04`, "
                "`/배당 2026-01~2026-04`, `/배당 전체`",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        # 2) 거래내역 조회 (입금만)
        try:
            txs = await kiwoom.get_transactions(start_date, end_date, tp="6")
        except Exception as e:
            log.exception("거래내역 조회 실패")
            await interaction.followup.send(f"❌ 조회 실패: {e}", ephemeral=True)
            return

        # 3) 분배금만 필터
        divs = [t for t in txs if DIVIDEND_REMARK in t.remark and t.ticker]

        # 4) 종목명 표시용 매핑 (포트폴리오 우선, 없으면 응답의 stk_nm)
        portfolio = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
        name_map = {h["ticker"]: h["name"] for h in portfolio["holdings"]}
        for t in divs:
            name_map.setdefault(t.ticker, t.name)

        # 5) 임베드 구성
        total = sum(t.amount for t in divs)
        embed = discord.Embed(
            title=f"💰 분배금 내역 — {period_label}",
            description=(f"총 {len(divs)}건 · **{total:,}원**" if divs else "분배금 입금이 없습니다."),
            color=0x00C853 if divs else 0x9E9E9E,
        )

        if divs:
            # 월별·종목별 집계
            grouped = _group_by_month(divs, name_map)
            # 최신 월부터
            for ym in sorted(grouped.keys(), reverse=True):
                month_total = sum(grouped[ym].values())
                lines = []
                # 종목별 큰 금액 순
                for tk, amt in sorted(grouped[ym].items(), key=lambda x: -x[1]):
                    lines.append(f"• {name_map.get(tk, tk)} — **{amt:,}원**")
                embed.add_field(
                    name=f"📅 {ym}  ·  {month_total:,}원",
                    value="\n".join(lines),
                    inline=False,
                )

        # 6) 다음달 예상 배당 (참고용)
        try:
            balance = await kiwoom.get_balance()
            holdings_qty = {h.ticker: h.quantity for h in balance.holdings}
        except Exception:
            holdings_qty = {}

        # 예상치는 항상 최근 데이터 기준이어야 하므로 별도 조회 (최근 60일)
        if holdings_qty:
            try:
                today = date.today()
                recent_start = (today - timedelta(days=60)).strftime("%Y%m%d")
                recent_txs = await kiwoom.get_transactions(
                    recent_start, today.strftime("%Y%m%d"), tp="6"
                )
                recent_divs = [
                    t for t in recent_txs if DIVIDEND_REMARK in t.remark and t.ticker
                ]
                est = _estimate_next_month(recent_divs, holdings_qty)
                if est:
                    est_total = sum(est.values())
                    est_lines = [
                        f"• {name_map.get(t, t)} — 약 {amt:,}원"
                        for t, amt in sorted(est.items(), key=lambda x: -x[1])
                    ]
                    embed.add_field(
                        name=f"🔮 다음달 예상 분배금 · 약 {est_total:,}원",
                        value="\n".join(est_lines)
                              + "\n\n_직전 1회 분배금 기준 추정치. 실제 금액은 매월 변동됩니다._",
                        inline=False,
                    )
            except Exception:
                log.exception("예상 분배금 계산 실패")

        # 7) 월별 추이 차트 (2개월 이상 분배 데이터가 있을 때만)
        chart_file = None
        if divs:
            grouped = _group_by_month(divs, name_map)
            if len(grouped) >= 2:
                try:
                    buf = await asyncio.to_thread(dividend_trend_bar, grouped, name_map)
                    chart_file = discord.File(buf, filename="dividend_trend.png")
                    embed.set_image(url="attachment://dividend_trend.png")
                except Exception:
                    log.exception("분배금 추이 차트 생성 실패")

        if chart_file is not None:
            await interaction.followup.send(embed=embed, file=chart_file)
        else:
            await interaction.followup.send(embed=embed)
