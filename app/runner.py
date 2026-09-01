"""봇을 백그라운드 스레드에서 돌리고 상태를 관리한다.

트레이 아이콘이 메인 스레드(윈도우 메시지 루프)를 점유해야 하므로,
discord.py의 asyncio 루프는 별도 스레드에서 돌린다.
"""

import asyncio
import socket
import threading
import time
from dataclasses import dataclass
from enum import Enum

from bot.client import IsaflowBot
from core.config import load_settings
from core.logger import get_logger
from kiwoom.client import KiwoomClient

log = get_logger(__name__)

# 네트워크 확인 대상 — 디스코드 게이트웨이와 같은 경로를 탄다
PROBE_HOST = "discord.com"
PROBE_PORT = 443
PROBE_TIMEOUT = 5.0

# 재시도 간격(초). 마지막 값으로 고정된다.
RETRY_DELAYS = (3, 5, 10, 20, 30)


class BotState(Enum):
    STOPPED = "중지됨"
    NETWORK_WAIT = "네트워크 대기"
    STARTING = "시작 중"
    RUNNING = "정상"
    RECONNECTING = "재연결 중"
    ERROR = "오류"


@dataclass(frozen=True)
class Status:
    state: BotState
    detail: str = ""
    uptime_sec: float | None = None

    @property
    def text(self) -> str:
        return (
            f"{self.state.value} ({self.detail})" if self.detail else self.state.value
        )

    @property
    def uptime_text(self) -> str:
        if self.uptime_sec is None:
            return ""
        total = int(self.uptime_sec)
        h, m = total // 3600, (total % 3600) // 60
        return f"가동 {h}시간 {m}분" if h else f"가동 {m}분"


def _network_available() -> bool:
    try:
        with socket.create_connection((PROBE_HOST, PROBE_PORT), timeout=PROBE_TIMEOUT):
            return True
    except OSError:
        return False


class BotRunner:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bot: IsaflowBot | None = None
        self._state = BotState.STOPPED
        self._detail = ""
        self._ready_at: float | None = None
        self._stopping = False
        self._lock = threading.Lock()

    # ── 상태 ──────────────────────────────────────────────────────
    def _set(self, state: BotState, detail: str = "") -> None:
        with self._lock:
            self._state = state
            self._detail = detail

    def status(self) -> Status:
        with self._lock:
            state, detail = self._state, self._detail
            ready_at = self._ready_at
        bot = self._bot

        # 로그인 후 연결이 끊기면 discord.py가 자동 재연결을 시도한다.
        if state is BotState.RUNNING and bot is not None and not bot.is_ready():
            state, detail = BotState.RECONNECTING, ""

        uptime = (
            time.monotonic() - ready_at
            if (ready_at and state is BotState.RUNNING)
            else None
        )
        return Status(state, detail, uptime)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── 제어 ──────────────────────────────────────────────────────
    def start(self) -> None:
        if self.alive:
            return
        self._stopping = False
        self._ready_at = None
        self._thread = threading.Thread(
            target=self._thread_main, name="bot", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stopping = True
        loop, bot = self._loop, self._bot
        if loop is not None and bot is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(bot.close(), loop).result(
                    timeout=timeout
                )
            except Exception as exc:  # noqa: BLE001 - 종료 경로는 무슨 일이 있어도 진행
                log.warning("봇 종료 중 예외: %s", exc)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._set(BotState.STOPPED)
        self._thread = None
        self._loop = None
        self._bot = None
        self._ready_at = None

    def restart(self) -> None:
        log.info("봇 재시작 요청")
        self.stop()
        self.start()

    # ── 내부 ──────────────────────────────────────────────────────
    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except Exception as exc:  # noqa: BLE001 - 스레드에서 죽으면 흔적이 안 남는다
            log.exception("봇 스레드 종료 (예외)")
            reason = str(exc).strip() or type(exc).__name__
            self._set(BotState.ERROR, reason[:40])
        else:
            if not self._stopping:
                self._set(BotState.ERROR, "예기치 않은 종료")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
            if self._stopping:
                self._set(BotState.STOPPED)
            log.info("봇 스레드 종료")

    async def _wait_for_network(self) -> None:
        """부팅 직후 네트워크가 아직 안 붙은 상태를 견딘다."""
        loop = asyncio.get_running_loop()
        attempt = 0
        while not self._stopping:
            if await loop.run_in_executor(None, _network_available):
                if attempt:
                    log.info("네트워크 연결 확인 (%d회 대기 후)", attempt)
                return
            attempt += 1
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            self._set(BotState.NETWORK_WAIT, f"{attempt}회차")
            log.info("네트워크 대기 중... (%d회차, %d초 후 재시도)", attempt, delay)
            await asyncio.sleep(delay)

    async def _run(self) -> None:
        settings = load_settings()  # .env 누락 시 여기서 RuntimeError
        await self._wait_for_network()
        if self._stopping:
            return

        kiwoom = KiwoomClient(settings)
        bot = IsaflowBot(settings, kiwoom)
        self._bot = bot
        self._set(BotState.STARTING)
        log.info("봇 시작 중...")

        async def _mark_ready() -> None:
            await bot.wait_until_ready()
            self._ready_at = time.monotonic()
            self._set(BotState.RUNNING)

        ready_task = asyncio.create_task(_mark_ready())
        try:
            await bot.start(settings.discord_bot_token)
        finally:
            ready_task.cancel()
            await bot.close()
