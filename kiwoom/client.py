"""키움 REST API 클라이언트.

사용 API:
- kt00018 계좌평가잔고내역 / kt00001 예수금
- ka10001 주식기본정보 (현재가)
- ka10004 호가 (매도1호가)
- kt10000 주식 매수주문 / kt10003 주문 취소
- ka10075 미체결 내역
- kt00015 위탁종합거래내역
"""

import asyncio
import random
import time
from datetime import date, datetime, timedelta

import httpx

from core.config import Settings
from core.logger import get_logger
from kiwoom.auth import TokenManager
from kiwoom.models import Balance, Holding, Transaction, UnexecutedOrder

log = get_logger(__name__)

# ── 호출 간격 / 재시도 ────────────────────────────────────────────
# 키움은 호출 빈도가 높으면 429를 돌려준다. 개인용 봇이라 응답이 조금 느려도
# 상관없으므로, 한도에 다시 걸리지 않도록 넉넉하게 잡는다.
MIN_INTERVAL_SEC = 0.6  # 연속 호출 사이 최소 간격
MAX_RETRIES = 5  # 429/5xx 재시도 횟수
BACKOFF_BASE_SEC = 2.0  # 2 → 4 → 8 → 16 → 32초
MAX_BACKOFF_SEC = 60.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# 주문 계열 API. 5xx는 서버가 이미 주문을 접수했을 수도 있어 재시도하면
# 중복 주문이 된다. 429는 요청 자체가 거절된 것이므로 재시도해도 안전하다.
ORDER_API_IDS = frozenset({"kt10000", "kt10003"})

# ── 과거 이력 역방향 탐색 ─────────────────────────────────────────
EMPTY_CHUNK_LIMIT = 2  # 빈 구간이 이만큼 연속되면 그 앞은 안 본다
MAX_SCAN_CHUNKS = 10  # 최악의 경우에도 호출 수를 묶어 둔다
HISTORY_FLOOR = date(2015, 1, 1)  # 이보다 과거는 조회하지 않는다


class KiwoomRateLimitError(RuntimeError):
    """재시도를 모두 소진하고도 호출 한도에 걸린 경우."""


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
        # 모든 호출을 한 줄로 세워 최소 간격을 강제한다 (429 예방)
        self._gate = asyncio.Lock()
        self._last_call_at = 0.0
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

    async def _throttle(self) -> None:
        """직전 호출로부터 MIN_INTERVAL_SEC이 지나도록 기다린다."""
        wait = self._last_call_at + MIN_INTERVAL_SEC - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call_at = time.monotonic()

    async def _request(
        self, path: str, api_id: str, body: dict, extra_headers: dict | None = None
    ) -> tuple[dict, httpx.Headers]:
        """모든 키움 호출이 지나는 단일 경로.

        간격 제어 · 429/5xx 재시도 · 공통 에러 처리를 여기 한 곳에서 한다.
        응답 헤더까지 돌려주므로 연속조회(cont-yn/next-key)도 이 경로를 쓴다.
        """
        http = self._client()
        url = f"{self._settings.kiwoom_base_url}{path}"

        for attempt in range(MAX_RETRIES + 1):
            async with self._gate:
                await self._throttle()
                token = await self._tokens.ensure_valid(http)
                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {token.access_token}",
                    "api-id": api_id,
                    **(extra_headers or {}),
                }
                resp = await http.post(url, headers=headers, json=body)

            if resp.status_code in RETRY_STATUSES:
                # 주문 API + 5xx는 접수 여부가 불확실하다. 중복 주문을 내느니
                # 실패로 올려 사용자가 /미체결로 직접 확인하게 한다.
                if api_id in ORDER_API_IDS and resp.status_code != 429:
                    log.error(
                        "[%s] HTTP %d — 주문 API라 재시도하지 않습니다. "
                        "체결 여부를 직접 확인하세요.",
                        api_id,
                        resp.status_code,
                    )
                    resp.raise_for_status()
                if attempt >= MAX_RETRIES:
                    if resp.status_code == 429:
                        raise KiwoomRateLimitError(
                            f"[{api_id}] 호출 한도 초과 (재시도 {MAX_RETRIES}회 실패)"
                        )
                    resp.raise_for_status()
                delay = self._retry_delay(resp, attempt)
                log.warning(
                    "[%s] HTTP %d — %.1f초 후 재시도 (%d/%d)",
                    api_id,
                    resp.status_code,
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                continue

            resp.raise_for_status()
            data = resp.json()
            if data.get("return_code") not in (0, None):
                raise RuntimeError(f"[{api_id}] {data.get('return_msg')}")
            return data, resp.headers

        raise KiwoomRateLimitError(f"[{api_id}] 재시도 한도 초과")

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        """Retry-After 헤더를 우선 존중하고, 없으면 지수 백오프."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), MAX_BACKOFF_SEC)
            except ValueError:
                pass
        # 지터를 섞어 재시도가 한 시점에 몰리지 않게 한다
        delay = BACKOFF_BASE_SEC * (2**attempt)
        return min(delay, MAX_BACKOFF_SEC) + random.uniform(0, 0.5)

    async def _call(self, path: str, api_id: str, body: dict) -> dict:
        data, _ = await self._request(path, api_id, body)
        return data

    async def get_balance(self) -> Balance:
        """계좌 평가 잔고 + 예수금."""
        bal = await self._call(
            "/api/dostk/acnt",
            "kt00018",
            {"qry_tp": "1", "dmst_stex_tp": "KRX"},
        )
        dpst = await self._call(
            "/api/dostk/acnt",
            "kt00001",
            {"qry_tp": "2"},
        )

        return Balance(
            total_eval=_to_float(bal.get("tot_evlt_amt")),
            total_purchase=_to_float(bal.get("tot_pur_amt")),
            cash=_to_float(dpst.get("entr")),
            buyable=_to_float(dpst.get("ord_alow_amt")),
            profit_loss=_to_float(bal.get("tot_evlt_pl")),
            return_rate=_to_float(bal.get("tot_prft_rt")),
            holdings=self._parse_holdings(bal),
        )

    @staticmethod
    def _parse_holdings(bal: dict) -> list[Holding]:
        return [
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

    async def get_current_price(self, ticker: str) -> int:
        data = await self._call(
            "/api/dostk/stkinfo",
            "ka10001",
            {"stk_cd": ticker},
        )
        return abs(_to_int(data.get("cur_prc")))

    async def get_ask_price(self, ticker: str) -> int:
        """매도최우선호가(매도1호가) 조회 (ka10004).

        지정가 매수 시 이 가격을 주문단가로 사용한다.
        조회 실패 또는 호가 없으면 0.
        """
        data = await self._call(
            "/api/dostk/mrkcond",
            "ka10004",
            {"stk_cd": ticker},
        )
        return abs(_to_int(data.get("sel_fpr_bid")))

    async def buy_limit(self, ticker: str, quantity: int, price: int) -> str:
        """지정가 매수 (kt10000, trde_tp=0). 주문번호 반환."""
        data = await self._call(
            "/api/dostk/ordr",
            "kt10000",
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
        log.info(
            "지정가 매수: %s x %d @ %d원 → 주문번호 %s", ticker, quantity, price, ord_no
        )
        return ord_no

    async def get_unexecuted(self) -> dict[str, int]:
        """매수 미체결 내역 조회 (ka10075).

        Returns:
            {주문번호: 미체결수량} 형태의 dict.
            미체결이 없으면 빈 dict.
        """
        data = await self._call(
            "/api/dostk/acnt",
            "ka10075",
            {
                "all_stk_tp": "0",
                "trde_tp": "2",  # 매수
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
            "/api/dostk/acnt",
            "ka10075",
            {
                "all_stk_tp": "0",
                "trde_tp": "2",  # 매수
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
            orders.append(
                UnexecutedOrder(
                    order_no=ord_no,
                    ticker=_normalize_ticker(o.get("stk_cd", "")),
                    name=o.get("stk_nm", ""),
                    order_qty=_to_int(o.get("ord_qty")),
                    unexec_qty=unexec,
                    order_price=_to_int(o.get("ord_pric")),
                )
            )
        return orders

    async def cancel_order(self, order_no: str, ticker: str) -> str:
        """미체결 매수 주문 취소 (kt10003). 미체결 잔량 전부 취소.

        Returns:
            취소 주문번호.
        """
        data = await self._call(
            "/api/dostk/ordr",
            "kt10003",
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

    async def get_transactions(
        self, start_date: str | None, end_date: str, tp: str = "0"
    ) -> list[Transaction]:
        """위탁종합거래내역 조회 (kt00015).

        키움은 한 번에 1년치만 조회 가능하므로 1년 단위로 자동 분할 호출한다.
        각 구간 안에서는 연속조회(페이징)도 자동 처리한다.

        Args:
            start_date: 시작일자 (YYYYMMDD). None이면 과거로 거슬러 올라가며
                빈 구간이 EMPTY_CHUNK_LIMIT회 연속될 때까지만 조회한다
                (계좌 개설 이전까지 헛되이 호출하지 않기 위함).
            end_date: 종료일자 (YYYYMMDD)
            tp: 거래구분 ("0":전체, "4":매수, "5":매도, "6":입금, "7":출금 등)
        """
        end = datetime.strptime(end_date, "%Y%m%d").date()

        if start_date is None:
            results = await self._scan_back(end, tp)
            log.info(
                "거래내역 역방향 조회: ~%s, tp=%s → %d건", end_date, tp, len(results)
            )
            return results

        start = datetime.strptime(start_date, "%Y%m%d").date()

        # 1년 단위로 구간을 쪼갠다.
        # 안전을 위해 364일(1년 미만)로 잡아 경계 케이스 회피.
        results: list[Transaction] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=364), end)
            results.extend(
                await self._fetch_transactions_chunk(
                    cursor.strftime("%Y%m%d"),
                    chunk_end.strftime("%Y%m%d"),
                    tp,
                )
            )
            cursor = chunk_end + timedelta(days=1)

        log.info(
            "거래내역 조회: %s ~ %s, tp=%s → %d건",
            start_date,
            end_date,
            tp,
            len(results),
        )
        return results

    async def _scan_back(self, end: date, tp: str) -> list[Transaction]:
        """최신 구간부터 과거로 1년씩 훑되, 빈 구간이 연속되면 멈춘다.

        계좌 개설 전 구간까지 매번 조회하면 호출 수만 늘고 429를 부른다.
        거래가 없는 해가 연속으로 나오면 그 앞은 볼 필요가 없다고 본다.
        """
        results: list[Transaction] = []
        cursor_end = end
        empty_streak = 0
        chunks = 0

        while chunks < MAX_SCAN_CHUNKS and cursor_end >= HISTORY_FLOOR:
            chunk_start = max(cursor_end - timedelta(days=364), HISTORY_FLOOR)
            chunk = await self._fetch_transactions_chunk(
                chunk_start.strftime("%Y%m%d"),
                cursor_end.strftime("%Y%m%d"),
                tp,
            )
            chunks += 1
            results.extend(chunk)

            if chunk:
                empty_streak = 0
            else:
                empty_streak += 1
                if empty_streak >= EMPTY_CHUNK_LIMIT:
                    log.info(
                        "빈 구간 %d회 연속 — 과거 조회 중단 (%s 이전)",
                        empty_streak,
                        chunk_start,
                    )
                    break

            cursor_end = chunk_start - timedelta(days=1)

        log.info("역방향 조회 구간 수: %d", chunks)
        return results

    async def _fetch_transactions_chunk(
        self, start_date: str, end_date: str, tp: str
    ) -> list[Transaction]:
        """단일 1년 이내 구간 조회 (연속조회 페이징 포함)."""
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
            data, headers = await self._request(
                "/api/dostk/acnt",
                "kt00015",
                body,
                extra_headers={"cont-yn": cont_yn, "next-key": next_key},
            )

            for tx in data.get("trst_ovrl_trde_prps_array", []):
                results.append(
                    Transaction(
                        date=tx.get("trde_dt", ""),
                        ticker=_normalize_ticker(tx.get("stk_cd", "")),
                        name=tx.get("stk_nm", ""),
                        remark=tx.get("rmrk_nm", ""),
                        amount=_to_int(tx.get("trde_amt")),
                        quantity=_to_int(tx.get("trde_qty_jwa_cnt")),
                    )
                )

            cont_yn = headers.get("cont-yn", "N")
            next_key = headers.get("next-key", "")
            if cont_yn != "Y":
                break

        return results
