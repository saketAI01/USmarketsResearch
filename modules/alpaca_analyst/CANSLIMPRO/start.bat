@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

title CANSLIM Screener Pro
set "PROJECT=C:\Users\abend\CLAUDEBOX\PROJECTS\CANSLIMPRO"

echo.
echo  ============================================================
echo   CANSLIM Screener Pro  -  US + Indian Markets
echo   yFinance / FMP API / Alpaca Markets
echo  ============================================================
echo.

cd /d "%PROJECT%"
if errorlevel 1 (
    echo ERROR: Project folder not found: %PROJECT%
    pause & exit /b 1
)

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    pause & exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Python %PYVER% detected.

if not exist "venv\Scripts\activate.bat" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 ( echo ERROR: venv creation failed. & pause & exit /b 1 )
    call venv\Scripts\activate.bat
    echo Installing dependencies...
    pip install -r requirements.txt --quiet
    if errorlevel 1 ( echo ERROR: pip install failed. & pause & exit /b 1 )
    echo Done.
) else (
    call venv\Scripts\activate.bat
    pip install -r requirements.txt --quiet --upgrade 2>nul
)

echo Virtual environment active.
echo.
python check_keys.py
echo.
echo -- Launching CANSLIM Screener Pro...
echo.
python main.py
set EXIT_CODE=%errorlevel%
if %EXIT_CODE% neq 0 (
    echo.
    echo Application exited with code %EXIT_CODE%.
    pause
)
endlocal
