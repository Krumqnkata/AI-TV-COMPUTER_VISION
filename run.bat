@echo off
setlocal
color b

:: Try running with venv python first, fallback to system python if venv doesn't exist
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe check_dependencies.py
) else (
    python check_dependencies.py
)

:: If dependency check failed or venv is missing, stop here
if %ERRORLEVEL% equ 0 goto :dependencies_ok

echo [!] Dependency check failed. Run: .venv\Scripts\pip install -r requirements.txt
pause
exit /b 1

:dependencies_ok

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


