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

# 종료 시 최대 대기 시간. 트레이가 이미 닫힌 뒤이므로 짧게 잡는다.
CLOSE_TIMEOUT = 5.0
JOIN_TIMEOUT = 5.0

# 이 시간을 넘겨도 준비가 안 되면 로그에 경고를 남긴다 (커맨드 동기화 지연 등)
SLOW_START_WARN_SEC = 60


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
        self._phase = BotState.STOPPED
        self._detail = ""
        self._connect_started: float | None = None
        self._ready_at: float | None = None
        self._slow_warned = False
        self._stopping = False
        self._lock = threading.Lock()

    # ── 상태 ──────────────────────────────────────────────────────
    def _set(self, phase: BotState, detail: str = "") -> None:
        with self._lock:
            changed = self._phase is not phase
            self._phase = phase
            self._detail = detail
        if changed:
            log.info("상태: %s%s", phase.value, f" ({detail})" if detail else "")

    def status(self) -> Status:
        """준비 여부는 태스크가 아니라 bot.is_ready()에서 직접 읽는다.

        예전에는 wait_until_ready()를 기다리는 태스크로 판정했는데, 그 태스크가
        조용히 죽으면 상태가 '시작 중'에 영원히 갇혔다.
        """
        with self._lock:
            phase, detail = self._phase, self._detail
        bot = self._bot

        if phase is BotState.STARTING and bot is not None:
            if bot.is_ready():
                if self._ready_at is None:
                    self._ready_at = time.monotonic()
                    log.info("봇 준비 완료")
                return Status(BotState.RUNNING, "", time.monotonic() - self._ready_at)

            if self._ready_at is not None:
                return Status(BotState.RECONNECTING)

            # 아직 한 번도 준비된 적 없음 — 경과 시간을 보여줘 멈춤/지연을 구분한다
            if self._connect_started is not None:
                elapsed = int(time.monotonic() - self._connect_started)
                if elapsed >= SLOW_START_WARN_SEC and not self._slow_warned:
                    self._slow_warned = True
                    log.warning(
                        "%d초째 준비되지 않았습니다. 슬래시 커맨드 동기화 지연이나 "
                        "레이트리밋일 수 있습니다.",
                        elapsed,
                    )
                detail = f"{elapsed}초"

        return Status(phase, detail)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── 제어 ──────────────────────────────────────────────────────
    def start(self) -> None:
        if self.alive:
            return
        self._stopping = False
        self._ready_at = None
        self._connect_started = None
        self._slow_warned = False
        self._thread = threading.Thread(
            target=self._thread_main, name="bot", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """봇을 정리한다. 트레이 메시지 루프에서 직접 부르지 말 것 (블로킹)."""
        self._stopping = True
        loop, bot = self._loop, self._bot
        if loop is not None and bot is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(bot.close(), loop).result(
                    timeout=CLOSE_TIMEOUT
                )
            except Exception as exc:  # noqa: BLE001 - 종료 경로는 무슨 일이 있어도 진행
                log.warning("봇 종료 중 예외: %s", exc)
        if self._thread is not None:
            self._thread.join(timeout=JOIN_TIMEOUT)
            if self._thread.is_alive():
                log.warning(
                    "봇 스레드가 시간 내에 끝나지 않았습니다 (데몬이므로 함께 종료됨)."
                )
        self._set(BotState.STOPPED)
        self._thread = None
        self._loop = None
        self._bot = None
        self._ready_at = None
        self._connect_started = None

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

    async def _sleep_interruptible(self, seconds: float) -> None:
        """종료 요청에 빠르게 반응하도록 잘게 나눠 잔다."""
        deadline = time.monotonic() + seconds
        while not self._stopping and time.monotonic() < deadline:
            await asyncio.sleep(min(0.5, deadline - time.monotonic()))

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
            await self._sleep_interruptible(delay)

    async def _run(self) -> None:
        settings = load_settings()  # .env 누락 시 여기서 RuntimeError
        await self._wait_for_network()
        if self._stopping:
            return

        kiwoom = KiwoomClient(settings)
        bot = IsaflowBot(settings, kiwoom)
        self._bot = bot
        self._connect_started = time.monotonic()
        self._set(BotState.STARTING)
        log.info("봇 시작 중...")

        try:
            await bot.start(settings.discord_bot_token)
        finally:
            await bot.close()
