"""키움 토큰 발급/갱신 (au10001)."""
import time
from datetime import datetime

import httpx

from core.logger import get_logger
from kiwoom.models import Token

log = get_logger(__name__)

_REFRESH_MARGIN_SEC = 60


class TokenManager:
    def __init__(self, base_url: str, app_key: str, app_secret: str) -> None:
        self._base_url = base_url
        self._app_key = app_key
        self._app_secret = app_secret
        self._token: Token | None = None

    @property
    def current_token(self) -> Token | None:
        return self._token

    def _is_valid(self) -> bool:
        if self._token is None:
            return False
        return time.time() + _REFRESH_MARGIN_SEC < self._token.expires_at

    async def ensure_valid(self, http: httpx.AsyncClient) -> Token:
        if self._is_valid() and self._token is not None:
            return self._token
        return await self._issue(http)

    async def _issue(self, http: httpx.AsyncClient) -> Token:
        log.info("키움 토큰 발급 요청")
        resp = await http.post(
            f"{self._base_url}/oauth2/token",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "secretkey": self._app_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("return_code") != 0:
            raise RuntimeError(f"토큰 발급 실패: {data.get('return_msg')}")

        # expires_dt: "20241107083713" 형식
        expires_at = datetime.strptime(data["expires_dt"], "%Y%m%d%H%M%S").timestamp()
        self._token = Token(access_token=data["token"], expires_at=expires_at)
        log.info("토큰 발급 완료. 만료: %s", data["expires_dt"])
        return self._token
