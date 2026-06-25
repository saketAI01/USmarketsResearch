@echo off
cd /d "%~dp0"
echo.
echo  ============================================
echo     US Markets Research - Smart Launcher
echo  ============================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Please install Python 3.10 or later.
    echo.
    pause
    exit /b 1
)

:: Check / install dependencies
echo [1/4] Checking dependencies...
python -c "import PySide6" 2>nul
if %errorlevel% neq 0 (
    echo        Installing PySide6...
    pip install PySide6 --quiet
)

python -c "import pandas" 2>nul
if %errorlevel% neq 0 (
    echo        Installing pandas...
    pip install pandas --quiet
)

python -c "import finvizfinance" 2>nul
if %errorlevel% neq 0 (
    echo        Installing finvizfinance...
    pip install finvizfinance --quiet
)

python -c "import finviz" 2>nul
if %errorlevel% neq 0 (
    echo        Installing finviz...
    pip install finviz --quiet
)

python -c "import yfinance" 2>nul
if %errorlevel% neq 0 (
    echo        Installing yfinance...
    pip install yfinance --quiet
)



python -c "import requests" 2>nul
if %errorlevel% neq 0 (
    echo        Installing requests...
    pip install requests --quiet
)

python -c "import matplotlib" 2>nul
if %errorlevel% neq 0 (
    echo        Installing matplotlib...
    pip install matplotlib --quiet
)

python -c "import numpy" 2>nul
if %errorlevel% neq 0 (
    echo        Installing numpy...
    pip install numpy --quiet
)

:: Check API keys
echo [2/4] Checking API keys...
set KEY_COUNT=0
if exist ALLAPI\FMP_API_KEY.txt set /a KEY_COUNT+=1
if exist ALLAPI\ALPACA_APISECRET.txt set /a KEY_COUNT+=1
if exist ALLAPI\GEMINI_API_KEY.txt set /a KEY_COUNT+=1
if exist ALLAPI\PERPLEXITY_API_KEY.txt set /a KEY_COUNT+=1
if exist ALLAPI\marketaux_API_Token.txt set /a KEY_COUNT+=1
if exist ALLAPI\ALPHAVANTAGE.txt set /a KEY_COUNT+=1
echo        Found %KEY_COUNT% API key files in ALLAPI folder.

:: Check master CSV
echo [3/4] Checking data files...
if exist USStockMaster.csv (
    echo        USStockMaster.csv found.
) else (
    echo        [WARN] USStockMaster.csv not found.
)

:: Launch
echo [4/4] Launching USmarketsResearch...
echo.
python main.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Application exited with code %errorlevel%.
    pause
)
