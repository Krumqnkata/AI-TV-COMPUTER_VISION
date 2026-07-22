@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] Липсва .venv. Създайте я и изпълнете:
    echo     .venv\Scripts\python.exe -m pip install -r requirements-node.txt
    pause
    exit /b 1
)

.venv\Scripts\python.exe client_qr_node.py
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" pause
exit /b %APP_EXIT%
