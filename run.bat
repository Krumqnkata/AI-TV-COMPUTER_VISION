@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] Липсва .venv. Създайте я и изпълнете:
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

.venv\Scripts\python.exe main.py
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
    echo.
    echo [!] Сървърът не стартира. Проверете зависимостите и изпълнете:
    echo     .venv\Scripts\python.exe -m alembic upgrade head
    echo.
    pause
)
exit /b %APP_EXIT%

