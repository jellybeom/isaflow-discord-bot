@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [콘솔 모드] 디버깅용입니다. 평소에는 start.vbs(트레이 모드)를 사용하세요.
echo.
uv run python main.py --console
echo.
echo Bot terminated. Press any key to close.
pause >nul