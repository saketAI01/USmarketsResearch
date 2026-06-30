@echo off
chcp 65001 >nul
title Technical Analyst Pro
echo.
echo +----------------------------------------------+
echo ^|       Technical Analyst Pro  v1.0           ^|
echo ^|   Professional Technical Analysis Platform  ^|
echo +----------------------------------------------+
echo.
echo Installing / verifying dependencies...
pip install PySide6 yfinance matplotlib mplfinance requests numpy pandas google-genai reportlab Pillow --break-system-packages --quiet 2>nul
if %errorlevel% neq 0 (
    pip install PySide6 yfinance matplotlib mplfinance requests numpy pandas google-genai reportlab Pillow --quiet 2>nul
)
echo Starting app...
echo.
cd /d "%~dp0"
python technical_analyst_pro.py
pause
