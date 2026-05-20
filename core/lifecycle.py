"""봇 시작/중지 관리. UI는 메인 스레드, 봇은 별도 스레드의 asyncio 루프에서 실행."""
import asyncio
import threading
from typing import Callable

from bot.client import IsaflowBot
from core.config import Settings
from core.logger import get_logger
from kiwoom.client import KiwoomClient

log = get_logger(__name__)


class BotLifecycle:
    def __init__(self, settings: Settings, kiwoom: KiwoomClient) -> None:
        self._settings = settings
        self._kiwoom = kiwoom
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bot: IsaflowBot | None = None
        self._on_state_change: Callable[[bool], None] | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_state_change_callback(self, cb: Callable[[bool], None]) -> None:
        self._on_state_change = cb

    def _notify(self, running: bool) -> None:
        if self._on_state_change:
            self._on_state_change(running)

    def start(self) -> None:
        if self.is_running:
            return
        log.info("봇 스레드 시작")
        self._thread = threading.Thread(target=self._run_bot, daemon=True, name="DiscordBot")
        self._thread.start()
        self._notify(True)

    def stop(self) -> None:
        if not self.is_running:
            return
        log.info("봇 중지 요청")
        if self._loop and self._bot:
            asyncio.run_coroutine_threadsafe(self._bot.close(), self._loop)
        if self._thread:
            self._thread.join(timeout=10)
        self._thread = None
        self._loop = None
        self._bot = None
        self._notify(False)
        log.info("봇 중지 완료")

    def _run_bot(self) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._bot = IsaflowBot(self._settings, self._kiwoom)
            self._loop.run_until_complete(self._bot.start(self._settings.discord_bot_token))
        except Exception:
            log.exception("봇 실행 중 오류")
        finally:
            self._shutdown_loop()
            self._notify(False)

    def _shutdown_loop(self) -> None:
        """루프를 닫기 전에 아직 끝나지 않은 태스크들을 마저 정리한다."""
        loop = self._loop
        if loop is None:
            return
        try:
            # 아직 pending 상태인 태스크 수집
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            if pending:
                # 각 태스크에 취소 신호를 보내고, 끝날 때까지 기다림
                for task in pending:
                    task.cancel()
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            # 비동기 제너레이터 정리
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            log.exception("이벤트 루프 정리 중 오류")
        finally:
            loop.close()
