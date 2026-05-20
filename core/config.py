import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    kiwoom_app_key: str
    kiwoom_app_secret: str
    kiwoom_account_no: str   # 표시용 — API 호출에는 사용 안 함 (토큰=계좌 매핑)
    kiwoom_is_mock: bool
    discord_bot_token: str
    discord_owner_id: int
    discord_allowed_channel_id: int

    @property
    def kiwoom_base_url(self) -> str:
        if self.kiwoom_is_mock:
            return "https://mockapi.kiwoom.com"
        return "https://api.kiwoom.com"


def _required(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"환경변수 {key}가 설정되지 않았습니다. .env 파일을 확인하세요.")
    return value


def load_settings() -> Settings:
    load_dotenv(ENV_PATH)
    return Settings(
        kiwoom_app_key=_required("KIWOOM_APP_KEY"),
        kiwoom_app_secret=_required("KIWOOM_APP_SECRET"),
        kiwoom_account_no=_required("KIWOOM_ACCOUNT_NO"),
        kiwoom_is_mock=os.getenv("KIWOOM_IS_MOCK", "false").lower() == "true",
        discord_bot_token=_required("DISCORD_BOT_TOKEN"),
        discord_owner_id=int(_required("DISCORD_OWNER_ID")),
        discord_allowed_channel_id=int(_required("DISCORD_ALLOWED_CHANNEL_ID")),
    )
