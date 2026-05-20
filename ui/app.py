"""customtkinter 컨트롤 패널. 버튼 1개로 봇 시작/중지 토글."""
import threading
from pathlib import Path

import customtkinter as ctk

from core.config import load_settings
from core.lifecycle import BotLifecycle
from core.logger import get_logger, setup_logging
from kiwoom.client import KiwoomClient
from ui import styles

log = get_logger(__name__)


class ControlPanelApp(ctk.CTk):
    def __init__(self) -> None:
        setup_logging()
        super().__init__()

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        self.title(styles.WINDOW_TITLE)
        self.geometry(styles.WINDOW_SIZE)
        self.resizable(False, False)

        # 윈도우 아이콘 — Windows에선 .ico, 그 외엔 .png 사용
        icon_dir = Path(__file__).resolve().parents[1] / "assets"
        try:
            ico = icon_dir / "icon.ico"
            if ico.exists():
                self.iconbitmap(default=str(ico))
        except Exception:
            pass  # 일부 환경에서 ico 미지원 — 무시

        try:
            self.settings = load_settings()
        except Exception as e:
            self._show_fatal(f"설정 로드 실패:\n{e}")
            return

        self.kiwoom = KiwoomClient(self.settings)
        self.lifecycle = BotLifecycle(self.settings, self.kiwoom)
        self.lifecycle.set_state_change_callback(
            lambda running: self.after(0, lambda: self._apply_state(running))
        )

        self._build_widgets()
        self.after(1000, self._tick)

    def _build_widgets(self) -> None:
        ctk.CTkLabel(self, text="isaflow-bot", font=styles.FONT_TITLE).pack(pady=(24, 4))

        env_text = "실계좌" if not self.settings.kiwoom_is_mock else "모의투자"
        env_color = "#D32F2F" if not self.settings.kiwoom_is_mock else "#1976D2"
        ctk.CTkLabel(
            self, text=f"환경: {env_text}", text_color=env_color, font=styles.FONT_BODY
        ).pack(pady=(0, 12))

        self.status_label = ctk.CTkLabel(
            self, text="● 중지됨", text_color=styles.COLOR_STOPPED, font=styles.FONT_BODY
        )
        self.status_label.pack(pady=4)

        self.token_label = ctk.CTkLabel(self, text="토큰 만료일: -", font=styles.FONT_BODY)
        self.token_label.pack(pady=4)

        self.toggle_button = ctk.CTkButton(
            self,
            text="봇 서버 실행",
            font=styles.FONT_BUTTON,
            width=240,
            height=56,
            fg_color=styles.COLOR_BUTTON_START,
            command=self._on_toggle,
        )
        self.toggle_button.pack(pady=20)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _show_fatal(self, msg: str) -> None:
        ctk.CTkLabel(self, text=msg, text_color="#D32F2F", wraplength=380).pack(padx=20, pady=20)

    def _on_toggle(self) -> None:
        if self.lifecycle.is_running:
            self.toggle_button.configure(state="disabled", text="중지 중...")
            threading.Thread(target=self.lifecycle.stop, daemon=True).start()
        else:
            self.toggle_button.configure(state="disabled", text="시작 중...")
            threading.Thread(target=self._start_bot, daemon=True).start()

    def _start_bot(self) -> None:
        # 토큰 발급은 봇 스레드의 이벤트 루프에서 첫 API 호출 시 자동으로 일어남.
        # 여기서 별도로 호출하지 않음 (이벤트 루프 충돌 방지).
        self.lifecycle.start()

    def _apply_state(self, running: bool) -> None:
        if running:
            self.toggle_button.configure(
                text="봇 서버 중지", fg_color=styles.COLOR_BUTTON_STOP, state="normal"
            )
            self.status_label.configure(text="● 실행 중", text_color=styles.COLOR_RUNNING)
        else:
            self.toggle_button.configure(
                text="봇 서버 실행", fg_color=styles.COLOR_BUTTON_START, state="normal"
            )
            self.status_label.configure(text="● 중지됨", text_color=styles.COLOR_STOPPED)
        self._refresh_token_label()

    def _refresh_token_label(self) -> None:
        expires = self.kiwoom.token_expires_at()
        text = f"토큰 만료일: {expires:%Y-%m-%d %H:%M:%S}" if expires else "토큰 만료일: -"
        self.token_label.configure(text=text)

    def _tick(self) -> None:
        self._refresh_token_label()
        self.after(1000, self._tick)

    def _on_close(self) -> None:
        if self.lifecycle.is_running:
            self.lifecycle.stop()
        self.destroy()
