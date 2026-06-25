import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = APP_DIR / "universe.db"
API_DIR = APP_DIR / "ALLAPI"

TTL_CONSTITUENTS = 168
TTL_FUNDAMENTALS = 24
TTL_BARS = 4

MARKET_CAP_SEGMENTS = {
    "Mega Cap":  200_000_000_000,
    "Large Cap":  10_000_000_000,
    "Mid Cap":     2_000_000_000,
    "Small Cap":     300_000_000,
    "Micro Cap":               0,
}

def classify_market_cap(market_cap):
    if market_cap is None or market_cap <= 0:
        return "Unknown"
    for label, threshold in MARKET_CAP_SEGMENTS.items():
        if market_cap >= threshold:
            return label
    return "Micro Cap"

GICS_SECTORS = [
    "Communication Services", "Consumer Discretionary", "Consumer Staples",
    "Energy", "Financials", "Health Care", "Industrials",
    "Information Technology", "Materials", "Real Estate", "Utilities",
]

SCREENER_PRESETS = {
    "Custom": {},
    "Value Picks": {
        "pe_min": 1, "pe_max": 18, "pb_max": 2.5,
        "div_yield_min": 1.5, "price_vs_sma200": "Above",
    },
    "Growth Momentum": {
        "rev_growth_min": 12, "eps_growth_min": 12,
        "rsi_min": 45, "rsi_max": 72,
        "price_vs_sma50": "Above", "macd_signal": "Positive",
    },
    "GARP (Growth at Reasonable Price)": {
        "pe_max": 25, "peg_max": 1.8,
        "rev_growth_min": 8, "roe_min": 12,
    },
    "Quality Large Caps": {
        "cap_segments": ["Mega Cap", "Large Cap"],
        "roe_min": 18, "debt_equity_max": 1.0,
        "price_vs_sma200": "Above",
    },
    "--- Technical Strategies ---": {},
    "RSI Mean Revision (Buy)": {
        "rsi_max": 30, "volume_min": 1_000_000,
    },
    "RSI Oversold Bounce": {
        "rsi_max": 30, "change_pct_min": 0.5, "volume_min": 2_000_000,
    },
    "RSI Momentum Continuation": {
        "rsi_min": 60, "rsi_max": 80, "price_vs_sma50": "Above",
    },
    "MACD Bullish Cross": {
        "macd_signal": "Positive", "price_vs_sma200": "Above",
    },
    "MACD Zero Line Pullback": {
        "macd_line_min": 0, "macd_signal": "Positive", "rsi_min": 45,
    },
    "Golden Cross (50/200)": {
        "sma50_vs_sma200": "Above", "price_vs_sma50": "Above",
    },
    "Death Cross (Sell Signal)": {
        "sma50_vs_sma200": "Below", "price_vs_sma50": "Below",
    },
    "20/50 SMA Crossover": {
        "sma20_vs_sma50": "Above", "price_vs_sma20": "Above",
    },
    "SMA200 + RSI Pullback": {
        "price_vs_sma200": "Above", "rsi_min": 30, "rsi_max": 45,
    },
    "EMA20 Pullback in Uptrend": {
        "price_vs_sma200": "Above", "price_vs_sma20": "Pullback",
    },
    "Donchian 20D Breakout": {
        "price_vs_high20": "Above", "volume_vs_avg_min": 120,
    },
    "Bollinger Squeeze Breakout": {
        "bb_width_max": 4, "change_pct_min": 2.0, "volume_min": 2_000_000,
    },
    "Bollinger Mean Reversion": {
        "rsi_max": 30, "change_pct_min": 0, "volume_min": 1_000_000,
    },
    "52-Week High Breakout": {
        "dist_high52_max": 1, "volume_vs_avg_min": 150, "rsi_min": 60,
    },
    "PEG Value + Trend Combo": {
        "peg_max": 1.2, "rsi_min": 50, "price_vs_sma50": "Above",
    },
    "High-ROE Momentum": {
        "roe_min": 20, "rev_growth_min": 20, "rsi_min": 60, "price_vs_sma50": "Above",
    },
    "Dividend Quality Filter": {
        "div_yield_min": 2.5, "debt_equity_max": 0.8, "market_cap_min": 10_000_000_000,
    },
}

def load_api_keys():
    keys = {
        "fmp": None, "alpaca_key": None, "alpaca_secret": None,
        "alphavantage": None, "gemini": None, "perplexity": None,
    }
    api_dir = API_DIR
    if not api_dir.exists():
        parent_api = APP_DIR.parent / "ALLAPI"
        if parent_api.exists():
            api_dir = parent_api
        else:
            return keys

    def _read(fname):
        p = api_dir / fname
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        return None

    keys["fmp"] = _read("FMP_API_KEY.txt")
    keys["alphavantage"] = _read("ALPHAVANTAGE.txt")
    keys["gemini"] = _read("GEMINI_API_KEY.txt")
    keys["perplexity"] = _read("PERPLEXITY_API_KEY.txt")

    alpaca_raw = _read("ALPACA_APISECRET.txt")
    if alpaca_raw:
        lines = alpaca_raw.strip().splitlines()
        keys["alpaca_key"] = lines[0].strip() if len(lines) > 0 else None
        keys["alpaca_secret"] = lines[1].strip() if len(lines) > 1 else None
    return keys
