"""isaflow-bot 진입점.

기본: 트레이 아이콘 모드 (콘솔 없음). start.vbs 또는 시작프로그램에서 실행.
  uv run pythonw main.py

디버깅: 콘솔 모드. 기존과 동일하게 터미널에 로그가 흐른다.
  uv run python main.py --console
"""

import asyncio
import sys

from core.logger import get_logger, setup_logging

setup_logging()
log = get_logger(__name__)


async def _run_console() -> None:
    from bot.client import IsaflowBot
    from core.config import load_settings
    from kiwoom.client import KiwoomClient

    settings = load_settings()
    kiwoom = KiwoomClient(settings)
    bot = IsaflowBot(settings, kiwoom)

    log.info("봇 시작 중... (콘솔 모드)")
    try:
        await bot.start(settings.discord_bot_token)
    finally:
        await bot.close()
        log.info("봇 종료")


def main() -> None:
    if "--console" in sys.argv:
        try:
            asyncio.run(_run_console())
        except KeyboardInterrupt:
            print("\n사용자 중지 (Ctrl+C). 봇을 종료합니다.")
            sys.exit(0)
        return

    from app import single_instance
    from app.tray import run_tray

    if not single_instance.acquire():
        single_instance.notify_already_running()
        sys.exit(0)

    run_tray()


if __name__ == "__main__":
    main()
