@echo off
setlocal

:: Try running with venv python first, fallback to system python if venv doesn't exist
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe check_dependencies.py
) else (
    python check_dependencies.py
)

:: If dependency check failed or venv is missing, stop here
if %ERRORLEVEL% neq 0 (
    echo.
    pause
    exit /b 1
)

echo.
echo [+] Starting main.py...
echo ============================================================
echo.

.venv\Scripts\python.exe main.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Program exited with an error or was stopped.
    pause
)

