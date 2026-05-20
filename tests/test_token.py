import asyncio
from core.config import load_settings
from core.logger import setup_logging
from kiwoom.client import KiwoomClient


async def main():
    setup_logging()
    settings = load_settings()
    print(f"환경: {'모의투자' if settings.kiwoom_is_mock else '실계좌'}")
    print(f"BASE URL: {settings.kiwoom_base_url}")

    kiwoom = KiwoomClient(settings)
    await kiwoom.ensure_token()
    print(f"✅ 토큰 발급 성공! 만료: {kiwoom.token_expires_at()}")


asyncio.run(main())
