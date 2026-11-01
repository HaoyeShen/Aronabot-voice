@echo off
chcp 65001 >nul
setlocal
cd /d "C:\Users\86181\Desktop\WecVoice-auto"
set "PY=C:\Users\86181\Desktop\ARONA bot windows\.venv\Scripts\python.exe"
set "LOG=%~dp0wecvoice-test.log"

echo ============================================
echo  WecVoice-auto test voice message
echo ============================================
echo.
echo 1. Open the WeChat chat named "test".
echo 2. Press any key here.
echo 3. You have 5 seconds to click the WeChat window.
echo.
pause

echo Click the WeChat window now. Sending in 5 seconds...
timeout /t 5 /nobreak >nul

echo === integrity / environment === > "%LOG%"
whoami /groups | findstr /i "S-1-16" >> "%LOG%" 2>&1
"%PY%" -c "import wecvoice_auto, sys; print(sys.executable); print(wecvoice_auto.__version__); print(wecvoice_auto.__file__)" >> "%LOG%" 2>&1
echo === send === >> "%LOG%"
"%PY%" "%~dp0send_test_voice.py" >> "%LOG%" 2>&1
type "%LOG%"

echo.
echo Log saved to: %LOG%
echo If the message was sent, check the "test" chat in WeChat.
pause
