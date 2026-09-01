"""중복 실행 방지.

실계좌에 주문을 내는 봇이므로, 인스턴스가 둘 뜨면 `/매수` 한 번에 주문이
두 번 나갈 수 있다. Windows 명명된 뮤텍스로 두 번째 실행을 차단한다.
(pywin32 없이 ctypes만 사용 — 의존성 추가 없음)
"""

import ctypes
import sys
from ctypes import wintypes

from core.logger import get_logger

log = get_logger(__name__)

MUTEX_NAME = "isaflow-bot-single-instance"
ERROR_ALREADY_EXISTS = 183

_handle = None  # 프로세스가 살아있는 동안 핸들을 붙잡아 둔다


def acquire() -> bool:
    """이 프로세스가 유일한 인스턴스면 True, 이미 실행 중이면 False."""
    global _handle

    if sys.platform != "win32":
        log.warning("Windows가 아니므로 중복 실행 검사를 건너뜁니다.")
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    _handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not _handle:
        # 뮤텍스 생성 자체가 실패하면 막지 않고 진행한다 (기동 실패보다 낫다)
        log.warning("뮤텍스 생성 실패 — 중복 실행 검사를 건너뜁니다.")
        return True

    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        log.warning("이미 실행 중인 인스턴스가 있어 종료합니다.")
        return False

    return True


def notify_already_running() -> None:
    """이미 실행 중임을 알리는 풍선 알림. 트레이 아이콘 없이 단독으로 띄운다."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            "isaflow-bot이 이미 실행 중입니다.\n트레이 아이콘을 확인하세요.",
            "isaflow-bot",
            0x40,  # MB_ICONINFORMATION
        )
    except Exception:  # noqa: BLE001 - 알림 실패가 종료를 막으면 안 됨
        pass
