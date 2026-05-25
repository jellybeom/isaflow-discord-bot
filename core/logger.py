import logging
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

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_fmt)
    console_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # discord 라이브러리 내부 로그 노이즈 줄이기
    #  - 음성 관련 WARNING(PyNaCl 등) 차단
    #  - Gateway 세션 ID 같은 INFO 차단
    #  - 진짜 문제(ERROR)만 보이게
    logging.getLogger("discord").setLevel(logging.ERROR)
    logging.getLogger("discord.client").setLevel(logging.ERROR)
    logging.getLogger("discord.gateway").setLevel(logging.ERROR)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
