"""포트폴리오 정의 로드 및 '관리 대상' 경계.

ISA 계좌에는 이 봇이 관리하는 월배당 포트폴리오 종목 외에, 별도 전략으로
직접 매수한 종목이 함께 들어 있을 수 있다. 그 둘을 섞으면

  - 비중 계산의 분모가 커져 포트폴리오 종목이 전부 '부족'하게 보이고
  - 수익률·분배금 집계에 다른 전략의 성과가 섞이며
  - /미체결이 다른 전략의 지정가 주문을 취소·재주문해 버린다

따라서 portfolio.json에 적힌 종목만을 '관리 대상'으로 보고, 그 밖의 종목은
표시만 하되 어떤 주문도 걸지 않는다. 이 모듈이 그 경계를 한 곳에서 정의한다.
"""

import json
from pathlib import Path
from typing import TypeVar

from core.logger import get_logger

log = get_logger(__name__)

PORTFOLIO_PATH = Path(__file__).resolve().parents[1] / "data" / "portfolio.json"

# 전략 외 종목을 표시할 때 쓰는 공통 문구
OUTSIDE_LABEL = "전략 외"

T = TypeVar("T")


def load_portfolio() -> dict:
    """portfolio.json 전체를 읽어 돌려준다."""
    return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def managed_tickers(portfolio: dict | None = None) -> set[str]:
    """관리 대상 종목코드 집합.

    portfolio.json에 종목을 추가하면 그 종목은 곧바로 관리 대상이 된다.
    (= 리밸런싱·재주문·비중 계산에 포함된다)

    읽기에 실패하면 빈 집합을 돌려준다. 호출부는 이 경우 '관리 대상 없음'으로
    보고 주문 성격의 동작을 하지 않아야 한다. 잘못 읽은 목록으로 남의 전략
    주문을 건드리느니 아무것도 안 하는 편이 낫다.
    """
    try:
        pf = portfolio if portfolio is not None else load_portfolio()
        return {h["ticker"] for h in pf["holdings"]}
    except (OSError, ValueError, KeyError, TypeError):
        log.exception(
            "portfolio.json을 읽지 못했습니다 — 관리 대상을 빈 집합으로 둡니다."
        )
        return set()


def name_map(portfolio: dict | None = None) -> dict[str, str]:
    """관리 대상 종목코드 → 표시용 종목명."""
    try:
        pf = portfolio if portfolio is not None else load_portfolio()
        return {h["ticker"]: h["name"] for h in pf["holdings"]}
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("portfolio.json을 읽지 못해 종목명 매핑을 비웁니다.")
        return {}


def split_by_managed(
    rows: list[T], tickers: set[str], key=lambda r: r.ticker
) -> tuple[list[T], list[T]]:
    """ticker를 가진 객체 목록을 (관리 대상, 전략 외)로 가른다.

    Args:
        rows: Holding / UnexecutedOrder / Transaction 등 ticker를 가진 객체들
        tickers: managed_tickers()의 결과
        key: 객체에서 종목코드를 꺼내는 함수
    """
    managed = [r for r in rows if key(r) in tickers]
    outside = [r for r in rows if key(r) not in tickers]
    return managed, outside
