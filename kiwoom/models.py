from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    access_token: str
    expires_at: float  # epoch seconds


@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str
    quantity: int
    avg_price: float
    current_price: float
    eval_amount: float
    profit_loss: float
    return_rate: float


@dataclass(frozen=True)
class Balance:
    total_eval: float
    total_purchase: float
    cash: float
    buyable: float       # 매수 가능 금액 (미체결 주문 제외)
    profit_loss: float
    return_rate: float
    holdings: list[Holding]


@dataclass(frozen=True)
class UnexecutedOrder:
    """매수 미체결 주문 1건."""
    order_no: str        # 원주문번호
    ticker: str
    name: str
    order_qty: int       # 최초 주문수량
    unexec_qty: int      # 미체결수량 (아직 안 채워진 잔량)
    order_price: int     # 주문단가


@dataclass(frozen=True)
class Transaction:
    """위탁종합거래내역(kt00015)의 거래 1건."""
    date: str            # 거래일자 (YYYYMMDD)
    ticker: str          # 종목코드 ("" 가능 — 입출금 등)
    name: str            # 종목명
    remark: str          # 적요명 (예: "수익분배금입금")
    amount: int          # 거래금액
    quantity: int        # 거래수량 (분배금은 0)
