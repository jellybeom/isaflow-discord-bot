"""리밸런싱 기반 매수 계획 산출.

UI·API와 독립적인 순수 계산 로직.
"""
from dataclasses import dataclass


@dataclass
class PlanItem:
    ticker: str
    name: str
    price: int        # 매도1호가 (주문단가)
    quantity: int     # 이번에 매수할 수량

    @property
    def cost(self) -> int:
        return self.price * self.quantity


def calculate_buy_plan(
    holdings: list[dict],
    invest_amount: int,
) -> tuple[list[PlanItem], int]:
    """기존 보유분을 고려해, 목표 비중에 가장 가까워지도록 매수 계획을 짠다.

    2단계로 동작한다.

    [1단계 — 리밸런싱] 매 스텝 1주씩 매수를 시뮬레이션하며, 후보는 두 조건을
    만족해야 한다.
      1. 잔여금으로 1주를 살 수 있을 것
      2. 1주를 사면 목표 비중에 '더 가까워질' 것 (improves)
    두 번째 조건이 핵심이다. 단가가 비싼 종목은 1주만 사도 목표를 크게
    초과할 수 있는데, 그럴 경우 안 사는 편이 목표에 더 가깝다. 그런 종목은
    이번 매수에서 제외되고, 누적 자산이 충분히 커진 뒤에야 매수된다.
    후보 중에서는 목표 대비 가장 부족한(괴리가 큰) 종목을 우선 매수한다.

    [2단계 — 잔여금 소진] 1단계에서 모든 종목이 목표를 채우면 잔여금이
    남는다. 이 돈을 끝까지 쓰기 위해, improves 조건을 떼고 살 수 있는 종목
    중 목표 대비 가장 덜 채워진 종목에 1주씩 배분한다. 가장 싼 종목조차
    살 수 없을 만큼 잔여금이 작아지면 종료한다.

    총자산 기준(기존 보유액 + 투입액)은 루프 전에 고정한다.
    루프 중 기준선이 바뀌면 비중 계산이 일관되지 않기 때문이다.

    Args:
        holdings: [{ticker, name, weight, price, hold_amt}, ...]
                  - weight   : 목표 비중 (0~1)
                  - price    : 매도1호가
                  - hold_amt : 현재 보유 평가액 (현재가 * 보유수량)
        invest_amount: 이번에 투입할 금액(원)

    Returns:
        (plan, remaining)
        - plan: PlanItem 리스트 (수량 0 포함 — 호출부에서 필터링)
        - remaining: 매수 후 잔여금(원)
    """
    items = [
        {
            "ticker": h["ticker"],
            "name": h["name"],
            "tgt": h["weight"],
            "price": h["price"],
            "hold_amt": h["hold_amt"],
            "buy_qty": 0,
        }
        for h in holdings
    ]

    total_asset = sum(i["hold_amt"] for i in items) + invest_amount
    remaining = invest_amount

    def current_weight(item: dict) -> float:
        """(보유 + 이번 매수분) 기준 현재 비중."""
        return (item["hold_amt"] + item["buy_qty"] * item["price"]) / total_asset

    def gap(item: dict) -> float:
        """목표 비중과의 괴리 (양수면 아직 부족)."""
        return item["tgt"] - current_weight(item)

    def improves(item: dict) -> bool:
        """1주 더 사면 목표 비중에 더 가까워지는가?

        지금 비중과 목표의 거리 vs 1주 산 뒤 비중과 목표의 거리를 비교한다.
        가까워지면 True. 비싼 종목이 목표를 크게 초과하는 매수를 걸러낸다.
        """
        now_dist = abs(gap(item))
        next_w = (item["hold_amt"] + (item["buy_qty"] + 1) * item["price"]) / total_asset
        next_dist = abs(item["tgt"] - next_w)
        return next_dist < now_dist

    # 1단계: 목표 비중에 가까워지는 매수
    while remaining > 0:
        candidates = [
            i for i in items
            if i["price"] and i["price"] <= remaining and improves(i)
        ]
        if not candidates:
            break
        target = max(candidates, key=gap)
        target["buy_qty"] += 1
        remaining -= target["price"]

    # 2단계: 잔여금 소진 — 목표를 다 채운 뒤 남은 돈을 끝까지 쓴다.
    # improves 조건을 떼고, 살 수 있는 종목 중 목표 대비 가장 덜 채워진
    # (gap이 가장 큰) 종목에 1주씩 배분한다. 가장 싼 종목도 못 살 때 종료.
    while remaining > 0:
        candidates = [i for i in items if i["price"] and i["price"] <= remaining]
        if not candidates:
            break
        target = max(candidates, key=gap)
        target["buy_qty"] += 1
        remaining -= target["price"]

    plan = [
        PlanItem(ticker=i["ticker"], name=i["name"], price=i["price"], quantity=i["buy_qty"])
        for i in items
    ]
    return plan, remaining
