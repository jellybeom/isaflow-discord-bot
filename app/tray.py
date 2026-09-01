"""윈도우 트레이 아이콘.

메인 스레드에서 pystray 메시지 루프를 돌리고, 봇은 BotRunner가 별도
스레드에서 돌린다. 상태 폴링은 데몬 스레드 하나로 처리한다.

주의: pystray(win32)는 메뉴 항목의 동작을 윈도우 프로시저 안에서 동기로
호출한다. 즉 메뉴 콜백에서 오래 걸리는 일을 하면 메시지 루프 전체가 멈추고
아이콘이 사라지지도, 반응하지도 않는다. 콜백은 즉시 반환해야 한다.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageEnhance

from app.runner import BotRunner, BotState
from core.logger import LOG_FILE, get_logger

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ICON_PATH = PROJECT_ROOT / "assets" / "icon_256.png"

POLL_INTERVAL = 2.0

# 상태 표시등 색. 원본 아이콘 위에 점으로 얹는다.
# (원본이 꽉 찬 사각형이라 알파 실루엣으로 칠하면 단색 블록이 되어버린다)
STATE_DOTS: dict[BotState, tuple[int, int, int]] = {
    BotState.STOPPED: (130, 130, 140),  # 회색
    BotState.NETWORK_WAIT: (240, 160, 40),  # 주황
    BotState.STARTING: (130, 130, 140),  # 회색
    BotState.RUNNING: (60, 200, 100),  # 초록
    BotState.RECONNECTING: (240, 160, 40),  # 주황
    BotState.ERROR: (215, 60, 60),  # 빨강
}


# ── 아이콘 ────────────────────────────────────────────────────────
def _load_base() -> Image.Image:
    if ICON_PATH.exists():
        return Image.open(ICON_PATH).convert("RGBA")
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((16, 16, 240, 240), fill=(90, 160, 250, 255))
    return img


def _with_dot(
    base: Image.Image, rgb: tuple[int, int, int], active: bool
) -> Image.Image:
    """원본 아이콘 우하단에 상태 점을 얹는다. 비활성 상태는 채도를 낮춘다."""
    img = base if active else ImageEnhance.Color(base).enhance(0.2)
    img = img.copy()

    w, h = img.size
    d = max(w // 3, 8)  # 16px로 줄여도 5px 이상 남는 크기
    pad = w // 32
    ring = max(w // 48, 2)
    box = (w - d - pad, h - d - pad, w - pad, h - pad)

    draw = ImageDraw.Draw(img)
    # 흰 테두리를 둘러 어떤 배경에서도 점이 구분되게 한다
    draw.ellipse(box, fill=(*rgb, 255), outline=(255, 255, 255, 255), width=ring)
    return img


def _build_icons() -> dict[BotState, Image.Image]:
    base = _load_base()
    return {
        state: _with_dot(base, rgb, active=(state is BotState.RUNNING))
        for state, rgb in STATE_DOTS.items()
    }


# ── 메뉴 동작 ──────────────────────────────────────────────────────
def _open_log() -> None:
    """로그를 메모장으로 연다. 실시간 갱신은 없고, 다시 열면 최신 내용이 보인다."""
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        LOG_FILE.touch(exist_ok=True)
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", str(LOG_FILE)])
        else:
            os.startfile(str(LOG_FILE))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 뷰어 실패로 봇이 죽으면 안 된다
        log.exception("로그 열기 실패")


def _open_log_dir() -> None:
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", str(LOG_FILE.parent)])
        else:
            os.startfile(str(LOG_FILE.parent))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        log.exception("로그 폴더 열기 실패")


# ── 실행 ──────────────────────────────────────────────────────────
def run_tray() -> None:
    runner = BotRunner()
    icons = _build_icons()
    stop_event = threading.Event()

    def _restart(icon: pystray.Icon, _item: object) -> None:
        # 메뉴 콜백은 메시지 루프를 막으므로 반드시 스레드로 넘긴다
        threading.Thread(target=runner.restart, daemon=True).start()

    def _quit(icon: pystray.Icon, _item: object) -> None:
        # 여기서 runner.stop()을 부르면 메시지 루프가 최대 수십 초 멈춰
        # 아이콘이 트레이에서 사라지지 않는다. 루프부터 끝내고, 봇 정리는
        # icon.run()이 반환된 뒤 finally에서 한다.
        log.info("트레이에서 종료 요청")
        stop_event.set()
        icon.visible = False  # 트레이에서 즉시 제거
        icon.stop()  # 메시지 루프 종료 (즉시 반환)

    menu = pystray.Menu(
        pystray.MenuItem("isaflow-bot", None, enabled=False),
        pystray.MenuItem(
            lambda _: f"상태: {runner.status().text}", None, enabled=False
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("로그 보기", lambda i, it: _open_log()),
        pystray.MenuItem("로그 폴더 열기", lambda i, it: _open_log_dir()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("봇 재시작", _restart),
        pystray.MenuItem("종료", _quit),
    )

    icon = pystray.Icon("isaflow-bot", icons[BotState.STOPPED], "isaflow-bot", menu)

    def _poll() -> None:
        last_state: BotState | None = None
        last_title = ""
        while not stop_event.wait(POLL_INTERVAL):
            try:
                status = runner.status()
                if status.state is not last_state:
                    icon.icon = icons[status.state]
                    icon.update_menu()
                    if status.state is BotState.ERROR:
                        _notify(
                            icon,
                            "봇이 중지되었습니다",
                            f"{status.text} — 로그를 확인하세요.",
                        )
                    elif status.state is BotState.RUNNING and last_state in (
                        BotState.STARTING,
                        BotState.RECONNECTING,
                    ):
                        _notify(
                            icon, "봇 준비 완료", "디스코드에서 / 를 입력해 사용하세요."
                        )
                    last_state = status.state

                parts = ["isaflow-bot", status.text]
                if status.uptime_text:
                    parts.append(status.uptime_text)
                title = " · ".join(parts)
                if title != last_title:
                    icon.title = title
                    last_title = title
            except Exception:  # noqa: BLE001 - 폴링이 죽으면 상태가 얼어붙는다
                log.exception("상태 갱신 실패")

    def _on_start(_icon: pystray.Icon) -> None:
        _icon.visible = True
        runner.start()
        threading.Thread(target=_poll, name="tray-poll", daemon=True).start()

    try:
        icon.run(setup=_on_start)
    finally:
        stop_event.set()
        runner.stop()


def _notify(icon: pystray.Icon, title: str, message: str) -> None:
    try:
        icon.notify(message, title)
    except Exception:  # noqa: BLE001 - 알림 미지원 환경에서도 계속 돈다
        pass
