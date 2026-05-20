"""키움 REST API 클라이언트.

사용 API:
- kt00018 계좌평가잔고내역 / kt00001 예수금
- ka10001 주식기본정보 (현재가)
- ka10004 호가 (매도1호가)
- kt10000 주식 매수주문 / kt10003 주문 취소
- ka10075 미체결 내역
- kt00015 위탁종합거래내역
"""
from datetime import datetime, timedelta

import httpx

from core.config import Settings
from core.logger import get_logger
from kiwoom.auth import TokenManager
from kiwoom.models import Balance, Holding, Transaction, UnexecutedOrder

log = get_logger(__name__)


def _to_int(s: str | None) -> int:
    return int(s) if s else 0


def _to_float(s: str | None) -> float:
    return float(s) if s else 0.0


def _normalize_ticker(code: str) -> str:
    """키움 종목코드를 정규화한다.

    키움은 잔고 응답에서 종목코드 앞에 'A'를 붙여 줄 수 있다
    (예: "A005930"). 맨 앞이 'A'일 때만 한 글자를 제거한다.
    "0008S0" 처럼 'A'로 시작하지 않는 코드는 그대로 둔다.
    """
    return code.removeprefix("A")


class KiwoomClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http: httpx.AsyncClient | None = None
        self._tokens = TokenManager(
            base_url=settings.kiwoom_base_url,
            app_key=settings.kiwoom_app_key,
            app_secret=settings.kiwoom_app_secret,
        )

    def _client(self) -> httpx.AsyncClient:
        """현재 이벤트 루프에서 httpx 클라이언트를 lazy하게 생성/반환."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    async def ensure_token(self) -> None:
        await self._tokens.ensure_valid(self._client())

    def token_expires_at(self) -> datetime | None:
        token = self._tokens.current_token
        return datetime.fromtimestamp(token.expires_at) if token else None

    async def _call(self, path: str, api_id: str, body: dict) -> dict:
        """인증 헤더 + api-id 헤더 붙여서 POST 호출."""
        http = self._client()
        token = await self._tokens.ensure_valid(http)
        resp = await http.post(
            f"{self._settings.kiwoom_base_url}{path}",
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token.access_token}",
                "api-id": api_id,
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("return_code") not in (0, None):
            raise RuntimeError(f"[{api_id}] {data.get('return_msg')}")
        return data

    async def get_balance(self) -> Balance:
        """계좌 평가 잔고 + 예수금."""
        bal = await self._call(
            "/api/dostk/acnt", "kt00018",
            {"qry_tp": "1", "dmst_stex_tp": "KRX"},
        )
        dpst = await self._call(
            "/api/dostk/acnt", "kt00001",
            {"qry_tp": "2"},
        )

        holdings = [
            Holding(
                ticker=_normalize_ticker(item["stk_cd"]),  # "A005930" → "005930"
                name=item["stk_nm"],
                quantity=_to_int(item.get("rmnd_qty")),
                avg_price=_to_float(item.get("pur_pric")),
                current_price=_to_float(item.get("cur_prc")),
                eval_amount=_to_float(item.get("evlt_amt")),
                profit_loss=_to_float(item.get("evltv_prft")),
                return_rate=_to_float(item.get("prft_rt")),
            )
            for item in bal.get("acnt_evlt_remn_indv_tot", [])
        ]
        return Balance(
            total_eval=_to_float(bal.get("tot_evlt_amt")),
            total_purchase=_to_float(bal.get("tot_pur_amt")),
            cash=_to_float(dpst.get("entr")),
            buyable=_to_float(dpst.get("ord_alow_amt")),
            profit_loss=_to_float(bal.get("tot_evlt_pl")),
            return_rate=_to_float(bal.get("tot_prft_rt")),
            holdings=holdings,
        )

    async def get_current_price(self, ticker: str) -> int:
        data = await self._call(
            "/api/dostk/stkinfo", "ka10001",
            {"stk_cd": ticker},
        )
        return abs(_to_int(data.get("cur_prc")))

    async def get_ask_price(self, ticker: str) -> int:
        """매도최우선호가(매도1호가) 조회 (ka10004).

        지정가 매수 시 이 가격을 주문단가로 사용한다.
        조회 실패 또는 호가 없으면 0.
        """
        data = await self._call(
            "/api/dostk/mrkcond", "ka10004",
            {"stk_cd": ticker},
        )
        return abs(_to_int(data.get("sel_fpr_bid")))

    async def buy_limit(self, ticker: str, quantity: int, price: int) -> str:
        """지정가 매수 (kt10000, trde_tp=0). 주문번호 반환."""
        data = await self._call(
            "/api/dostk/ordr", "kt10000",
            {
                "dmst_stex_tp": "KRX",
                "stk_cd": ticker,
                "ord_qty": str(quantity),
                "ord_uv": str(price),
                "trde_tp": "0",  # 보통(지정가)
                "cond_uv": "",
            },
        )
        ord_no = data.get("ord_no", "")
        log.info("지정가 매수: %s x %d @ %d원 → 주문번호 %s", ticker, quantity, price, ord_no)
        return ord_no

    async def get_unexecuted(self) -> dict[str, int]:
        """매수 미체결 내역 조회 (ka10075).

        Returns:
            {주문번호: 미체결수량} 형태의 dict.
            미체결이 없으면 빈 dict.
        """
        data = await self._call(
            "/api/dostk/acnt", "ka10075",
            {
                "all_stk_tp": "0",
                "trde_tp": "2",   # 매수
                "stk_cd": "",
                "stex_tp": "0",
            },
        )
        result = {}
        for order in data.get("oso", []):
            ord_no = order.get("ord_no", "")
            if ord_no:
                result[ord_no] = _to_int(order.get("oso_qty"))  # 미체결수량
        return result

    async def get_unexecuted_orders(self) -> list[UnexecutedOrder]:
        """매수 미체결 내역을 종목 정보까지 포함해 조회 (ka10075).

        /미체결 명령어용. 재주문에 필요한 종목코드·수량·주문번호를 담는다.
        """
        data = await self._call(
            "/api/dostk/acnt", "ka10075",
            {
                "all_stk_tp": "0",
                "trde_tp": "2",   # 매수
                "stk_cd": "",
                "stex_tp": "0",
            },
        )
        orders = []
        for o in data.get("oso", []):
            ord_no = o.get("ord_no", "")
            unexec = _to_int(o.get("oso_qty"))
            if not ord_no or unexec <= 0:
                continue
            orders.append(UnexecutedOrder(
                order_no=ord_no,
                ticker=_normalize_ticker(o.get("stk_cd", "")),
                name=o.get("stk_nm", ""),
                order_qty=_to_int(o.get("ord_qty")),
                unexec_qty=unexec,
                order_price=_to_int(o.get("ord_pric")),
            ))
        return orders

    async def cancel_order(self, order_no: str, ticker: str) -> str:
        """미체결 매수 주문 취소 (kt10003). 미체결 잔량 전부 취소.

        Returns:
            취소 주문번호.
        """
        data = await self._call(
            "/api/dostk/ordr", "kt10003",
            {
                "dmst_stex_tp": "KRX",
                "orig_ord_no": order_no,
                "stk_cd": ticker,
                "cncl_qty": "0",  # 0 = 미체결 잔량 전부 취소
            },
        )
        cancel_no = data.get("ord_no", "")
        log.info("주문 취소: 원주문 %s (%s) → 취소주문 %s", order_no, ticker, cancel_no)
        return cancel_no

    async def get_unexecuted_orders(self) -> list[UnexecutedOrder]:
        """매수 미체결 내역을 상세 정보와 함께 조회 (ka10075).

        get_unexecuted()는 체결 확인용으로 {주문번호: 수량}만 주지만,
        이 메서드는 재주문에 필요한 종목코드·종목명·주문단가까지 반환한다.
        """
        data = await self._call(
            "/api/dostk/acnt", "ka10075",
            {
                "all_stk_tp": "0",
                "trde_tp": "2",   # 매수
                "stk_cd": "",
                "stex_tp": "0",
            },
        )
        orders = []
        for o in data.get("oso", []):
            ord_no = o.get("ord_no", "")
            unexec = _to_int(o.get("oso_qty"))
            if not ord_no or unexec <= 0:
                continue
            orders.append(UnexecutedOrder(
                order_no=ord_no,
                ticker=_normalize_ticker(o.get("stk_cd", "")),
                name=o.get("stk_nm", ""),
                order_qty=_to_int(o.get("ord_qty")),
                unexec_qty=unexec,
                order_price=_to_int(o.get("ord_pric")),
            ))
        return orders

    async def cancel_order(self, order_no: str, ticker: str) -> str:
        """미체결 주문을 전량 취소 (kt10003). 취소 주문번호 반환.

        cncl_qty="0"은 미체결 잔량 전부 취소를 의미한다.
        """
        data = await self._call(
            "/api/dostk/ordr", "kt10003",
            {
                "dmst_stex_tp": "KRX",
                "orig_ord_no": order_no,
                "stk_cd": ticker,
                "cncl_qty": "0",  # 0 = 미체결 잔량 전부
            },
        )
        cancel_no = data.get("ord_no", "")
        log.info("주문 취소: %s (원주문 %s) → 취소주문 %s", ticker, order_no, cancel_no)
        return cancel_no

    async def get_transactions(
        self, start_date: str, end_date: str, tp: str = "0"
    ) -> list[Transaction]:
        """위탁종합거래내역 조회 (kt00015).

        키움은 한 번에 1년치만 조회 가능하므로 1년 단위로 자동 분할 호출한다.
        각 구간 안에서는 연속조회(페이징)도 자동 처리한다.

        Args:
            start_date: 시작일자 (YYYYMMDD)
            end_date: 종료일자 (YYYYMMDD)
            tp: 거래구분 ("0":전체, "4":매수, "5":매도, "6":입금, "7":출금 등)
        """
        start = datetime.strptime(start_date, "%Y%m%d").date()
        end = datetime.strptime(end_date, "%Y%m%d").date()

        # 1년 단위로 구간을 쪼갠다.
        # 안전을 위해 364일(1년 미만)로 잡아 경계 케이스 회피.
        results: list[Transaction] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=364), end)
            chunk = await self._fetch_transactions_chunk(
                cursor.strftime("%Y%m%d"),
                chunk_end.strftime("%Y%m%d"),
                tp,
            )
            results.extend(chunk)
            cursor = chunk_end + timedelta(days=1)

        log.info(
            "거래내역 조회: %s ~ %s, tp=%s → %d건",
            start_date, end_date, tp, len(results),
        )
        return results

    async def _fetch_transactions_chunk(
        self, start_date: str, end_date: str, tp: str
    ) -> list[Transaction]:
        """단일 1년 이내 구간 조회 (연속조회 페이징 포함)."""
        http = self._client()
        url = f"{self._settings.kiwoom_base_url}/api/dostk/acnt"
        body = {
            "strt_dt": start_date,
            "end_dt": end_date,
            "tp": tp,
            "stk_cd": "",
            "crnc_cd": "",
            "gds_tp": "0",
            "frgn_stex_code": "",
            "dmst_stex_tp": "%",
        }
        results: list[Transaction] = []
        cont_yn, next_key = "N", ""

        while True:
            token = await self._tokens.ensure_valid(http)
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token.access_token}",
                "api-id": "kt00015",
                "cont-yn": cont_yn,
                "next-key": next_key,
            }
            resp = await http.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            if data.get("return_code") not in (0, None):
                raise RuntimeError(f"[kt00015] {data.get('return_msg')}")

            for tx in data.get("trst_ovrl_trde_prps_array", []):
                results.append(Transaction(
                    date=tx.get("trde_dt", ""),
                    ticker=_normalize_ticker(tx.get("stk_cd", "")),
                    name=tx.get("stk_nm", ""),
                    remark=tx.get("rmrk_nm", ""),
                    amount=_to_int(tx.get("trde_amt")),
                    quantity=_to_int(tx.get("trde_qty_jwa_cnt")),
                ))

            cont_yn = resp.headers.get("cont-yn", "N")
            next_key = resp.headers.get("next-key", "")
            if cont_yn != "Y":
                break

        return results
