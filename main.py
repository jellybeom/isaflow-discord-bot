"""isaflow-bot 진입점.

터미널에서 직접 실행하거나 run.bat 더블클릭으로 시작한다.
Ctrl+C로 중지.
"""

import asyncio
import sys

from bot.client import IsaflowBot
from core.config import load_settings
from core.logger import get_logger, setup_logging
from kiwoom.client import KiwoomClient

setup_logging()
log = get_logger(__name__)


async def run_bot() -> None:
    settings = load_settings()
    kiwoom = KiwoomClient(settings)
    bot = IsaflowBot(settings, kiwoom)

    log.info("봇 시작 중...")
    try:
        await bot.start(settings.discord_bot_token)
    finally:
        await bot.close()
        log.info("봇 종료")


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n사용자 중지 (Ctrl+C). 봇을 종료합니다.")
        sys.exit(0)
