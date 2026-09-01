import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parents[1] / "logs" / "bot.log"


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)

    # 콘솔용: 짧고 깔끔하게 (시간:분:초 + 레벨 + 메시지)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )
    # 파일용: 디버깅 위해 자세히 (날짜 + 모듈명 유지)
    file_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(file_fmt)
    file_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)

    # pythonw.exe로 실행하면 stdout/stderr이 None이라 StreamHandler가 예외를 낸다.
    # 콘솔이 있을 때만 붙인다.
    if sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_fmt)
        console_handler.setLevel(logging.INFO)
        root.addHandler(console_handler)

    # discord 라이브러리 내부 로그 노이즈 줄이기
    #  - 음성 관련 WARNING(PyNaCl 등) 차단
    #  - Gateway 세션 ID 같은 INFO 차단
    #  - 진짜 문제(ERROR)만 보이게
    logging.getLogger("discord").setLevel(logging.ERROR)
    logging.getLogger("discord.client").setLevel(logging.ERROR)
    logging.getLogger("discord.gateway").setLevel(logging.ERROR)

    # 콘솔이 없으면 미처리 예외가 흔적 없이 사라지므로 파일에 남긴다.
    _install_excepthook()


def _install_excepthook() -> None:
    def _hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("uncaught").critical(
            "처리되지 않은 예외", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _hook


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
