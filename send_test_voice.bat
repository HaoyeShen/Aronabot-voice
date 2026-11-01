@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo Sends one test voice bubble to the WeChat group named test.
echo WeChat must be logged in. This source tool requires installed dependencies.
echo.
pause
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "send_test_voice.py"
) else (
    py -3.12 "send_test_voice.py"
)
echo.
pause
