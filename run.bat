@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  Starting AI-TV-COMPUTER-VISION...
echo ============================================================

:: Check if virtual environment exists
if not exist ".venv\Scripts\python.exe" (
    echo [!] Error: Virtual environment .venv not found!
    echo Please create the virtual environment first.
    pause
    exit /b 1
)

:: Run dependency checker
.venv\Scripts\python.exe check_dependencies.py

:: Check exit code of dependency checker
if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Dependency check failed.
    echo Please install the missing packages listed above before running the program.
    echo.
    pause
    exit /b 1
)

echo.
echo [+] Dependencies OK. Starting main.py...
echo ============================================================
echo.

.venv\Scripts\python.exe main.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Program exited with an error or was stopped.
    pause
)
