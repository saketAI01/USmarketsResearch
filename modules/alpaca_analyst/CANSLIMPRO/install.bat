@echo off
setlocal enabledelayedexpansion

title CANSLIM Screener Pro — Setup
set "PROJECT=C:\Users\abend\CLAUDEBOX\PROJECTS\CANSLIMPRO"

echo.
echo  ================================================================
echo   CANSLIM Screener Pro — First-Time Setup
echo  ================================================================
echo.

cd /d "%PROJECT%"
if errorlevel 1 (
    echo ERROR: Project folder not found: %PROJECT%
    pause & exit /b 1
)

REM ── Python check ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo        Download from: https://www.python.org/downloads/
    echo        During install, check "Add Python to PATH"
    pause & exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER%

REM ── Virtual environment ───────────────────────────────────────────────────
if exist "venv\" (
    echo [OK] Virtual environment already exists.
) else (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 ( echo ERROR: venv creation failed. & pause & exit /b 1 )
    echo [OK] Virtual environment created.
)

call venv\Scripts\activate.bat

REM ── Install dependencies ──────────────────────────────────────────────────
echo.
echo Installing Python dependencies...
pip install --upgrade pip --quiet
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Dependency installation failed.
    echo        Check your internet connection and try again.
    pause & exit /b 1
)
echo [OK] Dependencies installed.

REM ── Verify imports ────────────────────────────────────────────────────────
echo.
echo Verifying imports...
python -c "import PySide6; import yfinance; import reportlab; import pandas; print('[OK] All core libraries loaded')"
if errorlevel 1 (
    echo ERROR: Import verification failed.
    pause & exit /b 1
)

REM ── keys.env check ────────────────────────────────────────────────────────
echo.
if exist "keys.env" (
    echo [OK] keys.env found.
    python -c "
import sys; sys.path.insert(0,'.')
from config import key_status
ks = key_status()
print(f'  FMP:    {\"Active\" if ks[\"fmp\"] else \"Not set\"}')
print(f'  Alpaca: {\"Active\" if ks[\"alpaca\"] else \"Not set\"}')
"
) else (
    echo [WARN] keys.env not found — creating template...
    (
        echo # CANSLIM Screener Pro - API Keys
        echo # Fill in your keys below
        echo.
        echo FMP_API_KEY=
        echo ALPACA_KEY_ID=
        echo ALPACA_SECRET_KEY=
    ) > keys.env
    echo        Edit keys.env in the project folder and add your API keys.
)

REM ── Run self-test ─────────────────────────────────────────────────────────
echo.
echo Running self-test (fetching AAPL sample data from yFinance)...
python test_install.py
if errorlevel 1 (
    echo [WARN] Self-test had issues — see above. App may still work.
) else (
    echo [OK] Self-test passed.
)

echo.
echo  ================================================================
echo   Setup complete! Run start.bat to launch the application.
echo  ================================================================
echo.
pause
endlocal
