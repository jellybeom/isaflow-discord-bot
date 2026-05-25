"""디스코드 첨부용 차트 생성.

세 가지 차트를 만든다:
  1. portfolio_donut       — 자산군(안쪽) + 종목(바깥쪽) 이중 도넛
  2. weight_comparison_bar — 목표 vs 실제 비중 비교 (괴리율)
  3. dividend_trend_bar    — 월별 분배금 추이
"""
import io
import math
import platform
from collections import OrderedDict

import matplotlib
matplotlib.use("Agg")  # GUI 없는 환경에서 안전
import matplotlib.pyplot as plt

# ── 공통 톤 ───────────────────────────────────────────────────────
BG = "#1e1e2e"
TEXT = "#ffffff"
TEXT_DIM = "#b8b8c4"
GRID = "#3a3a52"

# 종목별 색상 — 채도 높인 산뜻한 팔레트
TICKER_COLORS = {
    "273140": "#FFB74D",  # 따뜻한 오렌지
    "455660": "#FF8A65",  # 살구색
    "476800": "#4FC3F7",  # 밝은 하늘
    "352560": "#4DD0E1",  # 청록
    "466940": "#BA68C8",  # 라일락
    "0008S0": "#7E57C2",  # 인디고
}
FLOOR_COLORS = {  # 범례/내부 표시용
    "1F": "#FF8A65",
    "2F": "#4FC3F7",
    "3F": "#BA68C8",
}
POS_COLOR = "#4ADE80"  # 산뜻한 초록
NEG_COLOR = "#F87171"  # 산뜻한 빨강
NEUTRAL_COLOR = "#FBBF24"  # 노랑


def _setup_korean_font() -> None:
    """OS별 한글 폰트 설정. 한 번만 실행되면 충분."""
    if platform.system() == "Windows":
        plt.rcParams["font.family"] = "Malgun Gothic"
    elif platform.system() == "Darwin":
        plt.rcParams["font.family"] = "AppleGothic"
    else:
        plt.rcParams["font.family"] = "NanumGothic"
    plt.rcParams["axes.unicode_minus"] = False


def _style_axes(ax) -> None:
    """축·테두리·눈금 다크 톤 적용."""
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(colors=TEXT_DIM)
    ax.xaxis.label.set_color(TEXT)
    ax.yaxis.label.set_color(TEXT)


def _fig_to_bytes(fig) -> io.BytesIO:
    """그림을 PNG 바이트로 변환. 디스코드 첨부용."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


# ── 1. 포트폴리오 도넛 (종목 단일) ─────────────────────────────────
def portfolio_donut(portfolio: dict, total_eval: float | None = None) -> io.BytesIO:
    """종목 단일 도넛. 가운데 총 평가금액(또는 '포트폴리오') 텍스트.

    Args:
        portfolio: data/portfolio.json 전체 dict
        total_eval: 가운데 표시할 총 평가금액. None이면 '포트폴리오'만 표시.
    """
    _setup_korean_font()
    fig, ax = plt.subplots(figsize=(11, 9), facecolor=BG)
    ax.set_facecolor(BG)

    holdings = portfolio["holdings"]
    sizes = [h["weight"] for h in holdings]
    labels = [h["name"] for h in holdings]
    colors = [TICKER_COLORS.get(h["ticker"], FLOOR_COLORS.get(h["floor"], "#888"))
              for h in holdings]

    wedges, _ = ax.pie(
        sizes,
        radius=1.0,
        colors=colors,
        wedgeprops=dict(width=0.38, edgecolor=BG, linewidth=3),
        startangle=90, counterclock=False,
    )

    # 가운데 텍스트 — 크게
    if total_eval is not None:
        ax.text(0, 0.1, "총 평가금액", ha="center", va="center",
                color=TEXT_DIM, fontsize=14)
        ax.text(0, -0.08, f"{total_eval:,.0f}원", ha="center", va="center",
                color=TEXT, fontsize=22, fontweight="bold")
    else:
        ax.text(0, 0, "포트폴리오", ha="center", va="center",
                color=TEXT, fontsize=20, fontweight="bold")

    # 바깥 라벨 (종목명 + 비율) — 글자 크게
    for wedge, label, size in zip(wedges, labels, sizes):
        ang = (wedge.theta2 + wedge.theta1) / 2
        rad = math.radians(ang)
        x = math.cos(rad)
        y = math.sin(rad)
        ha = "left" if x > 0 else "right"
        short = label if len(label) <= 18 else label[:17] + "…"
        ax.annotate(
            f"{short}\n{size*100:.0f}%",
            xy=(x * 0.95, y * 0.95),
            xytext=(x * 1.25, y * 1.25),
            ha=ha, va="center", color=TEXT, fontsize=12,
            fontweight="bold",
            arrowprops=dict(arrowstyle="-", color=TEXT_DIM, lw=0.8),
        )

    ax.set_title(portfolio.get("title", "포트폴리오"),
                 color=TEXT, fontsize=20, fontweight="bold", pad=25)
    return _fig_to_bytes(fig)


# ── 2. 목표 vs 실제 비중 비교 (괴리율) ───────────────────────────────
def weight_comparison_bar(
    items: list[dict],
) -> io.BytesIO:
    """종목별 목표 비중 vs 현재 비중 비교.

    Args:
        items: [{ticker, name, target, current, floor}, ...]
               - target, current: 0~1 비율
    """
    _setup_korean_font()
    n = len(items)
    fig, ax = plt.subplots(figsize=(13, max(5, n * 0.7) + 2), facecolor=BG)
    _style_axes(ax)

    # 정렬: 괴리(target - current)가 큰(=부족한) 종목부터 위
    items = sorted(items, key=lambda x: -(x["target"] - x["current"]))

    y_pos = list(range(len(items)))
    names = [i["name"] if len(i["name"]) <= 20 else i["name"][:19] + "…" for i in items]
    targets = [i["target"] * 100 for i in items]
    currents = [i["current"] * 100 for i in items]

    bar_height = 0.38
    # 목표(연한 회색) — 배경 바
    ax.barh([y - bar_height/2 for y in y_pos], targets, height=bar_height,
            color="#7c7c8e", label="목표 비중", zorder=2)
    # 현재 — 종목 색상
    ax.barh([y + bar_height/2 for y in y_pos], currents, height=bar_height,
            color=[TICKER_COLORS.get(i["ticker"], "#888") for i in items],
            label="현재 비중", zorder=2)

    # 라벨: 막대 끝에 퍼센트 — 글자 크게
    max_val = max(max(targets), max(currents), 1)
    for y, t, c in zip(y_pos, targets, currents):
        ax.text(t + max_val*0.01, y - bar_height/2, f"{t:.1f}%",
                color=TEXT_DIM, fontsize=11, va="center")
        ax.text(c + max_val*0.01, y + bar_height/2, f"{c:.1f}%",
                color=TEXT, fontsize=11, va="center", fontweight="bold")

    # 괴리율 표시 (오른쪽) — |diff|<1: 노랑(거의 일치) / 양수: 초록 / 음수: 빨강
    for y, t, c in zip(y_pos, targets, currents):
        diff = c - t
        if abs(diff) < 1.0:
            color = NEUTRAL_COLOR
        elif diff > 0:
            color = POS_COLOR
        else:
            color = NEG_COLOR
        ax.text(max_val * 1.18, y, f"{diff:+.1f}%p",
                color=color, fontsize=13, va="center", fontweight="bold")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, color=TEXT, fontsize=12)
    ax.invert_yaxis()
    ax.set_xlim(0, max_val * 1.3)
    ax.set_xlabel("비중 (%)", color=TEXT, fontsize=12)
    ax.set_title("목표 비중 vs 현재 비중", color=TEXT, fontsize=18,
                 fontweight="bold", pad=20)
    # 범례를 차트 바깥 위쪽으로 — 막대를 가리지 않음
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=TEXT,
              loc="lower right", bbox_to_anchor=(1.0, 1.01),
              fontsize=11, ncol=2, framealpha=0.9)
    ax.grid(axis="x", color=GRID, alpha=0.3, zorder=1)
    ax.set_axisbelow(True)

    return _fig_to_bytes(fig)


# ── 3. 월별 분배금 추이 ─────────────────────────────────────────────
def dividend_trend_bar(monthly: dict[str, dict[str, int]],
                       name_map: dict[str, str]) -> io.BytesIO:
    """월별·종목별 분배금 스택 막대 차트.

    Args:
        monthly: {"YYYY-MM": {ticker: 금액, ...}, ...}
        name_map: {ticker: 종목명}
    """
    _setup_korean_font()
    if not monthly:
        fig, ax = plt.subplots(figsize=(10, 4), facecolor=BG)
        ax.set_facecolor(BG)
        ax.text(0.5, 0.5, "분배금 내역 없음", ha="center", va="center",
                color=TEXT_DIM, fontsize=14, transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        return _fig_to_bytes(fig)

    months = sorted(monthly.keys())
    # 모든 종목 추출
    all_tickers = sorted({t for m in monthly.values() for t in m.keys()})

    fig, ax = plt.subplots(figsize=(max(11, len(months) * 0.9), 7), facecolor=BG)
    _style_axes(ax)

    bottoms = [0] * len(months)
    for tk in all_tickers:
        vals = [monthly[m].get(tk, 0) for m in months]
        color = TICKER_COLORS.get(tk, "#888")
        label = name_map.get(tk, tk)
        if len(label) > 20:
            label = label[:19] + "…"
        ax.bar(months, vals, bottom=bottoms, color=color, label=label,
               edgecolor=BG, linewidth=2)
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    # 월별 총합 라벨 — 크게
    for i, m in enumerate(months):
        total = bottoms[i]
        if total > 0:
            ax.text(i, total * 1.02, f"{total:,}원",
                    ha="center", va="bottom", color=TEXT, fontsize=12, fontweight="bold")

    plt.setp(ax.get_xticklabels(), rotation=45 if len(months) > 6 else 0, ha="right",
             fontsize=11)
    plt.setp(ax.get_yticklabels(), fontsize=10)

    ax.set_ylabel("분배금 (원)", color=TEXT, fontsize=12)
    grand_total = sum(sum(v.values()) for v in monthly.values())
    ax.set_title(f"월별 분배금 추이  ·  총 {grand_total:,}원",
                 color=TEXT, fontsize=18, fontweight="bold", pad=20)
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=TEXT,
              loc="upper left", fontsize=10, ncol=1, framealpha=0.9)
    ax.grid(axis="y", color=GRID, alpha=0.3, zorder=0)
    ax.set_axisbelow(True)

    # Y축 천 단위 콤마
    from matplotlib.ticker import FuncFormatter
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x):,}"))

    return _fig_to_bytes(fig)
