"""/배당 — ISA 계좌 분배금 내역 조회."""

import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import discord
import httpx
from discord import app_commands

from bot.charts import dividend_trend_bar
from bot.permissions import check_access
from core.config import Settings
from core.logger import get_logger
from kiwoom.client import KiwoomClient, KiwoomRateLimitError

log = get_logger(__name__)

PORTFOLIO_PATH = Path(__file__).resolve().parents[2] / "data" / "portfolio.json"

# 키움 적요명에서 분배금 입금을 식별하는 키워드
DIVIDEND_REMARK = "수익분배금입금"

# 종목별로 펼쳐 보여줄 최근 개월 수.
# 디스코드 임베드는 필드 25개·전체 6000자가 한도라 전체 조회 시 그냥 넘긴다.
DETAIL_MONTHS = 10
FIELD_VALUE_LIMIT = 1024


def _parse_period(arg: str | None) -> tuple[str | None, str, str]:
    """사용자 입력을 (시작일, 종료일, 기간 라벨)로 변환.

    지원하는 형식:
      - None         → 이번 달
      - "전체"       → 시작일 None (거래가 끊길 때까지 과거로 역탐색)
      - "2026-04"    → 그 달
      - "2026-01~2026-04" → 그 기간 (시작월~종료월)
    """
    today = date.today()

    if arg is None or arg.strip() == "":
        # 이번 달
        start = today.replace(day=1)
        return (
            start.strftime("%Y%m%d"),
            today.strftime("%Y%m%d"),
            f"{today.year}년 {today.month}월",
        )

    arg = arg.strip()

    if arg == "전체":
        return None, today.strftime("%Y%m%d"), "전체 기간"

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


def _fit(lines: list[str]) -> str:
    """임베드 필드값 1024자 한도에 맞게 줄을 잘라 붙인다."""
    out: list[str] = []
    used = 0
    for i, line in enumerate(lines):
        remain = len(lines) - i
        tail = f"\n…외 {remain}건" if remain > 1 else ""
        if used + len(line) + 1 + len(tail) > FIELD_VALUE_LIMIT:
            out.append(f"…외 {remain}건")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out) or "—"


def _group_by_month(divs: list) -> dict:
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


def _friendly_error(exc: Exception) -> str:
    """키움 예외를 사용자가 읽을 만한 한 줄로 바꾼다.

    원문에 URL이 섞이면 디스코드가 링크 카드를 펼쳐 화면을 어지럽힌다.
    상세 원인은 로그에만 남긴다.
    """
    if isinstance(exc, KiwoomRateLimitError):
        return "❌ 키움 API 호출 한도에 걸렸습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return "❌ 키움 API 호출 한도에 걸렸습니다. 잠시 후 다시 시도해 주세요."
        if code in (401, 403):
            return "❌ 키움 인증에 실패했습니다. APP KEY/SECRET을 확인해 주세요."
        return f"❌ 키움 서버 오류 (HTTP {code}). 잠시 후 다시 시도해 주세요."
    if isinstance(exc, httpx.TimeoutException):
        return "❌ 키움 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요."
    return f"❌ 조회 실패: {exc}"


def register(
    tree: app_commands.CommandTree, settings: Settings, kiwoom: KiwoomClient
) -> None:
    @tree.command(
        name="배당",
        description="분배금 내역 조회 (예: /배당, /배당 2026-04, /배당 전체)",
    )
    @app_commands.describe(
        기간="조회 기간 (생략=이번달, '전체', 'YYYY-MM', 'YYYY-MM~YYYY-MM')"
    )
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
            await interaction.followup.send(
                _friendly_error(e),
                ephemeral=True,
                suppress_embeds=True,  # 에러 문구의 URL이 카드로 펼쳐지지 않게
            )
            return

        # 3) 분배금만 필터
        divs = [t for t in txs if DIVIDEND_REMARK in t.remark and t.ticker]

        # 4) 종목명 표시용 매핑 (포트폴리오 우선, 없으면 응답의 stk_nm)
        name_map: dict[str, str] = {}
        try:
            portfolio = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
            name_map = {h["ticker"]: h["name"] for h in portfolio["holdings"]}
        except (OSError, ValueError, KeyError):
            log.warning("portfolio.json을 읽지 못해 응답의 종목명을 사용합니다.")
        for tx in divs:
            name_map.setdefault(tx.ticker, tx.name)

        # 5) 월별·종목별 집계 (한 번만 계산해서 임베드와 차트가 함께 쓴다)
        grouped = _group_by_month(divs)

        total = sum(t.amount for t in divs)
        embed = discord.Embed(
            title=f"💰 분배금 내역 — {period_label}",
            description=(
                f"총 {len(divs)}건 · **{total:,}원**"
                if divs
                else "분배금 입금이 없습니다."
            ),
            color=0x00C853 if divs else 0x9E9E9E,
        )

        # 최신 월부터. 전체 조회는 월 수가 많아 임베드 한도(필드 25개, 6000자)를
        # 넘기므로, 최근 N개월만 종목별로 펼치고 나머지는 한 필드로 접는다.
        months = sorted(grouped.keys(), reverse=True)
        for ym in months[:DETAIL_MONTHS]:
            month_total = sum(grouped[ym].values())
            lines = [
                f"• {name_map.get(tk, tk)} — **{amt:,}원**"
                for tk, amt in sorted(grouped[ym].items(), key=lambda x: -x[1])
            ]
            embed.add_field(
                name=f"📅 {ym}  ·  {month_total:,}원",
                value=_fit(lines),
                inline=False,
            )

        rest = months[DETAIL_MONTHS:]
        if rest:
            rest_total = sum(sum(grouped[ym].values()) for ym in rest)
            embed.add_field(
                name=f"📦 그 이전 {len(rest)}개월  ·  {rest_total:,}원",
                value=_fit([f"{ym} · {sum(grouped[ym].values()):,}원" for ym in rest]),
                inline=False,
            )

        # 6) 월별 추이 차트 (2개월 이상 분배 데이터가 있을 때만)
        chart_file = None
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
