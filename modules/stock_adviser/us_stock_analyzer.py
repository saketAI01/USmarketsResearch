"""
US Stock Analyzer & Advisor — Sophisticated PySide6 desktop application.

Features
--------
* Sector-segmented watchlists for the S&P 500 and NASDAQ 100 (loaded from
  watchlists.json or an embedded fallback).
* Smart symbol-search combo box with autocomplete on both ticker and company
  name (filter as you type, arrow-key navigation, Enter to analyze).
* Sector tree with full keyboard navigation (Up / Down / Left / Right inside
  the tree; Ctrl+Down / Ctrl+Up cycle through tickers across the whole tree).
* Multi-horizon scoring engine — swing-trade (1 week-1 month), short-term
  (1-3 months, technical), medium-term (3-12 months, technical + valuation
  + analyst), long-term (1-5 years, fundamentals + capital structure).
* Composite Buy / Hold / Sell rating and a written executive summary.
* Embedded matplotlib chart (price with SMA20/50/200 + Bollinger overlay,
  RSI, MACD) on a dark Catppuccin-style theme.
* Tabbed report (Overview, Fundamentals, Technicals, Recommendation) rendered
  in QTextBrowser with inline-styled HTML. Export the full report to .html.

Dependencies
------------
    pip install PySide6 yfinance pandas numpy matplotlib

Run
---
    python us_stock_analyzer.py

Keyboard shortcuts
------------------
    Ctrl+F        focus the smart search box and select its contents
    Ctrl+R        run analysis on the currently-selected ticker
    Ctrl+S        export the rendered report to HTML
    Ctrl+Down     advance to the next ticker in the watchlist tree
    Ctrl+Up       advance to the previous ticker in the watchlist tree
    Enter         (inside the search box) analyze the typed/selected symbol
"""

from __future__ import annotations

import sys
from pathlib import Path
# Allow resolving sibling modules when imported from main app
sys.path.insert(0, str(Path(__file__).resolve().parent))

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ----------------------------------------------------------------------------
# Optional dependencies — degrade gracefully when missing
# ----------------------------------------------------------------------------
try:
    import yfinance as yf  # type: ignore

    YFINANCE_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    YFINANCE_AVAILABLE = False

try:
    import numpy as np  # type: ignore
    import pandas as pd  # type: ignore

    PANDAS_AVAILABLE = True
except Exception:  # pragma: no cover
    PANDAS_AVAILABLE = False

try:
    import matplotlib  # type: ignore

    matplotlib.use("QtAgg")
    from matplotlib.backends.backend_qtagg import (  # type: ignore
        FigureCanvasQTAgg as FigureCanvas,
    )
    from matplotlib.figure import Figure  # type: ignore

    MATPLOTLIB_AVAILABLE = True
except Exception:  # pragma: no cover
    MATPLOTLIB_AVAILABLE = False

# ----------------------------------------------------------------------------
# PySide6 (required)
# ----------------------------------------------------------------------------
from PySide6.QtCore import (
    QStringListModel,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeySequence,
    QPalette,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QFileDialog,
    QInputDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from modules.stock_evaluate.database import DatabaseManager


# ============================================================================
# Theme — Catppuccin Mocha inspired
# ============================================================================
COLOR_BG = "#1e1e2e"
COLOR_SURFACE = "#181825"
COLOR_PANEL = "#313244"
COLOR_TEXT = "#cdd6f4"
COLOR_MUTED = "#a6adc8"
COLOR_DIM = "#7f849c"
COLOR_BORDER = "#45475a"
COLOR_ACCENT = "#89b4fa"
COLOR_ACCENT_HOVER = "#74c7ec"
COLOR_GREEN = "#a6e3a1"
COLOR_RED = "#f38ba8"
COLOR_AMBER = "#f9e2af"
COLOR_ORANGE = "#fab387"
COLOR_PURPLE = "#cba6f7"
COLOR_TEAL = "#94e2d5"


# ============================================================================
# Watchlist loading
# ============================================================================
EMBEDDED_WATCHLISTS: dict[str, dict[str, list[list[str]]]] = {
    "S&P 500": {
        "Information Technology": [
            ["AAPL", "Apple Inc."],
            ["MSFT", "Microsoft Corp."],
            ["NVDA", "NVIDIA Corp."],
            ["AVGO", "Broadcom Inc."],
            ["ORCL", "Oracle Corp."],
            ["AMD", "Advanced Micro Devices"],
            ["INTC", "Intel Corp."],
            ["CRM", "Salesforce Inc."],
            ["ADBE", "Adobe Inc."],
            ["CSCO", "Cisco Systems"],
        ],
        "Financials": [
            ["JPM", "JPMorgan Chase"],
            ["V", "Visa Inc."],
            ["MA", "Mastercard Inc."],
            ["BAC", "Bank of America"],
            ["GS", "Goldman Sachs Group"],
        ],
        "Healthcare": [
            ["UNH", "UnitedHealth Group"],
            ["JNJ", "Johnson & Johnson"],
            ["LLY", "Eli Lilly and Co."],
            ["PFE", "Pfizer Inc."],
        ],
        "Consumer Discretionary": [
            ["AMZN", "Amazon.com Inc."],
            ["TSLA", "Tesla Inc."],
            ["HD", "Home Depot"],
        ],
    },
    "NASDAQ 100": {
        "Technology": [
            ["AAPL", "Apple Inc."],
            ["MSFT", "Microsoft Corp."],
            ["NVDA", "NVIDIA Corp."],
            ["GOOGL", "Alphabet Inc. Class A"],
            ["META", "Meta Platforms"],
        ],
    },
}


def load_watchlists() -> dict[str, dict[str, list[list[str]]]]:
    """Load watchlists from neighbouring JSON file, or embedded fallback."""
    here = Path(__file__).resolve().parent
    for candidate in [here / "watchlists.json", Path.cwd() / "watchlists.json"]:
        if candidate.exists():
            try:
                with candidate.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as exc:  # pragma: no cover
                print(f"[warn] failed to read {candidate}: {exc}", file=sys.stderr)
    return EMBEDDED_WATCHLISTS


# ============================================================================
# Analysis engine
# ============================================================================
def _fmt_money(value: Any, scale: str = "auto") -> str:
    if value is None:
        return "n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    abs_v = abs(v)
    if scale == "auto":
        if abs_v >= 1e12:
            return f"${v / 1e12:.2f}T"
        if abs_v >= 1e9:
            return f"${v / 1e9:.2f}B"
        if abs_v >= 1e6:
            return f"${v / 1e6:.1f}M"
        if abs_v >= 1e3:
            return f"${v / 1e3:.1f}K"
        return f"${v:.2f}"
    return f"${v:.2f}"


def _fmt_pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


class StockAnalysis:
    """Encapsulates one ticker's data, indicators and multi-horizon score."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker.upper()
        self.info: dict[str, Any] = {}
        self.history: Any = None
        self.df: Any = None
        self.swing_term: dict[str, Any] = {}
        self.short_term: dict[str, Any] = {}
        self.medium_term: dict[str, Any] = {}
        self.long_term: dict[str, Any] = {}
        self.composite: float = 50.0
        self.composite_rating: str = "HOLD"
        self.summary: str = ""

    # ------------------------------------------------------------------
    # Data acquisition
    # ------------------------------------------------------------------
    def fetch(self) -> "StockAnalysis":
        if not YFINANCE_AVAILABLE:
            raise RuntimeError(
                "yfinance is not installed. Run: pip install yfinance pandas numpy matplotlib"
            )
        if not PANDAS_AVAILABLE:
            raise RuntimeError(
                "pandas / numpy are required. Run: pip install pandas numpy"
            )
        tk = yf.Ticker(self.ticker)
        # ``Ticker.info`` is a property — some versions raise on bad tickers
        try:
            self.info = dict(tk.info) if tk.info else {}
        except Exception:
            self.info = {}
        self.history = tk.history(period="2y", auto_adjust=True)
        if self.history is None or self.history.empty:
            raise RuntimeError(
                f"No price history returned for '{self.ticker}'. "
                f"Check the symbol or your network connection."
            )
        return self

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------
    def compute_indicators(self) -> Any:
        df = self.history.copy()
        close = df["Close"]
        df["SMA20"] = close.rolling(20).mean()
        df["SMA50"] = close.rolling(50).mean()
        df["SMA200"] = close.rolling(200).mean()
        df["EMA12"] = close.ewm(span=12, adjust=False).mean()
        df["EMA26"] = close.ewm(span=26, adjust=False).mean()
        df["MACD"] = df["EMA12"] - df["EMA26"]
        df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["Hist"] = df["MACD"] - df["Signal"]

        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["RSI"] = 100 - (100 / (1 + rs))

        df["BB_Mid"] = close.rolling(20).mean()
        std = close.rolling(20).std()
        df["BB_Up"] = df["BB_Mid"] + 2 * std
        df["BB_Dn"] = df["BB_Mid"] - 2 * std

        h_l = df["High"] - df["Low"]
        h_c = (df["High"] - close.shift()).abs()
        l_c = (df["Low"] - close.shift()).abs()
        tr = pd.concat([h_l, h_c, l_c], axis=1).max(axis=1)
        df["ATR"] = tr.rolling(14).mean()

        self.df = df
        return df

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    @staticmethod
    def _score_to_rating(score: float) -> str:
        if score >= 75:
            return "STRONG BUY"
        if score >= 60:
            return "BUY"
        if score >= 45:
            return "HOLD"
        if score >= 30:
            return "SELL"
        return "STRONG SELL"

    def _score_swing_term(self) -> dict[str, Any]:
        """Ultra short-term swing-trade outlook (1 week - 1 month).

        Emphasises very recent price action: 5-day momentum, 10-day MA,
        MACD histogram acceleration, Bollinger band squeeze / breakout,
        RSI mean-reversion edges, and recent volume spikes.
        """
        if self.df is None or len(self.df) < 30:
            return {"score": 50.0, "rating": "HOLD",
                    "signals": [("Insufficient price history for swing scoring", "neutral")]}
        score = 50.0
        sig: list[tuple[str, str]] = []
        df = self.df
        last = df.iloc[-1]
        p = last["Close"]

        # 5-day momentum — the bread-and-butter swing-trade input
        if len(df) >= 6:
            mom5 = (p / df["Close"].iloc[-6] - 1) * 100
            if mom5 > 8:
                score += 14
                sig.append((f"Strong 5-day pop (+{mom5:.1f}%)", "positive"))
            elif mom5 > 3:
                score += 7
                sig.append((f"Constructive 5-day momentum (+{mom5:.1f}%)", "positive"))
            elif mom5 < -8:
                score -= 14
                sig.append((f"Sharp 5-day drop ({mom5:.1f}%)", "negative"))
            elif mom5 < -3:
                score -= 7
                sig.append((f"Weak 5-day action ({mom5:.1f}%)", "negative"))
            else:
                sig.append((f"5-day flat ({mom5:+.1f}%)", "neutral"))

        # 10-day momentum — confirms or fades the 5-day
        if len(df) >= 11:
            mom10 = (p / df["Close"].iloc[-11] - 1) * 100
            if mom10 > 10:
                score += 6
                sig.append((f"10-day uptrend (+{mom10:.1f}%)", "positive"))
            elif mom10 < -10:
                score -= 6
                sig.append((f"10-day downtrend ({mom10:.1f}%)", "negative"))

        # 10-day SMA proximity (the classic swing-trade trend filter)
        sma10 = df["Close"].rolling(10).mean().iloc[-1]
        if pd.notna(sma10) and sma10 > 0:
            dist10 = (p / sma10 - 1) * 100
            if dist10 > 5:
                score -= 4
                sig.append(
                    (f"Stretched {dist10:+.1f}% above 10D MA — pullback risk", "negative")
                )
            elif dist10 > 1:
                score += 6
                sig.append(
                    (f"Riding above 10D MA ({dist10:+.1f}%)", "positive")
                )
            elif dist10 < -5:
                score -= 8
                sig.append(
                    (f"Well below 10D MA ({dist10:+.1f}%) — momentum broken", "negative")
                )
            else:
                score -= 2
                sig.append((f"Hugging 10D MA ({dist10:+.1f}%)", "neutral"))

        # RSI — mean-reversion edge over a 1-4 week window
        rsi = last.get("RSI")
        if pd.notna(rsi):
            if rsi < 30:
                score += 14
                sig.append(
                    (f"RSI {rsi:.1f} — oversold bounce setup", "positive")
                )
            elif rsi < 40:
                score += 5
                sig.append((f"RSI {rsi:.1f} — washed out", "positive"))
            elif rsi > 75:
                score -= 12
                sig.append(
                    (f"RSI {rsi:.1f} — extreme; reversal risk", "negative")
                )
            elif rsi > 65:
                score -= 4
                sig.append((f"RSI {rsi:.1f} — hot but not extreme", "negative"))
            else:
                sig.append((f"RSI {rsi:.1f} — neutral", "neutral"))

        # MACD histogram acceleration (compare last two bars)
        if len(df) >= 2 and pd.notna(df["Hist"].iloc[-1]) and pd.notna(df["Hist"].iloc[-2]):
            h0, h1 = df["Hist"].iloc[-2], df["Hist"].iloc[-1]
            if h1 > 0 and h1 > h0:
                score += 8
                sig.append(("MACD histogram expanding bullish", "positive"))
            elif h1 < 0 and h1 < h0:
                score -= 8
                sig.append(("MACD histogram expanding bearish", "negative"))
            elif h1 > 0 and h1 < h0:
                score -= 3
                sig.append(("MACD histogram fading from bullish", "negative"))
            elif h1 < 0 and h1 > h0:
                score += 3
                sig.append(("MACD histogram improving toward zero", "positive"))

        # Bollinger band position — breakout vs squeeze for swing setups
        bb_up = last.get("BB_Up")
        bb_dn = last.get("BB_Dn")
        bb_mid = last.get("BB_Mid")
        if pd.notna(bb_up) and pd.notna(bb_dn) and bb_up > bb_dn:
            pos = (p - bb_dn) / (bb_up - bb_dn)
            if pos > 1.0:
                score += 5
                sig.append(("Breakout above upper Bollinger — momentum", "positive"))
            elif pos > 0.9:
                score -= 3
                sig.append(("Pressing upper Bollinger — risk of fade", "negative"))
            elif pos < 0.0:
                score += 8
                sig.append(("Below lower Bollinger — snap-back candidate", "positive"))
            elif pos < 0.1:
                score += 6
                sig.append(("Near lower Bollinger — bounce setup", "positive"))
            # Squeeze detection — narrow bands precede a move
            if pd.notna(bb_mid) and bb_mid > 0:
                width = (bb_up - bb_dn) / bb_mid
                if width < 0.06:
                    score += 4
                    sig.append(
                        (f"Bollinger squeeze (width {width * 100:.1f}%) — breakout imminent", "positive")
                    )

        # Volume surge — institutional footprints over the last 1-2 days
        if "Volume" in df.columns and len(df) >= 21:
            vol_last = float(df["Volume"].iloc[-1])
            vol_avg = float(df["Volume"].iloc[-21:-1].mean())
            if vol_avg > 0:
                ratio = vol_last / vol_avg
                ret_today = (p / df["Close"].iloc[-2] - 1) if len(df) >= 2 else 0.0
                if ratio > 1.8 and ret_today > 0:
                    score += 7
                    sig.append(
                        (f"Volume surge {ratio:.1f}x on up day — accumulation", "positive")
                    )
                elif ratio > 1.8 and ret_today < 0:
                    score -= 7
                    sig.append(
                        (f"Volume surge {ratio:.1f}x on down day — distribution", "negative")
                    )
                elif ratio < 0.6:
                    sig.append(
                        (f"Low volume ({ratio:.1f}x avg) — conviction lacking", "neutral")
                    )

        # ATR-relative move — sizing the recent thrust against typical volatility
        atr = last.get("ATR")
        if pd.notna(atr) and atr > 0 and len(df) >= 2:
            day_move = abs(p - df["Close"].iloc[-2])
            if day_move > 2.0 * atr:
                if p > df["Close"].iloc[-2]:
                    score += 5
                    sig.append(
                        (f"Outsized up-day ({day_move / atr:.1f}x ATR)", "positive")
                    )
                else:
                    score -= 5
                    sig.append(
                        (f"Outsized down-day ({day_move / atr:.1f}x ATR)", "negative")
                    )

        # Proximity to 20-day high / low — classic swing breakout / breakdown
        if len(df) >= 20:
            hi20 = float(df["High"].iloc[-20:].max())
            lo20 = float(df["Low"].iloc[-20:].min())
            if p >= hi20 * 0.995:
                score += 6
                sig.append(("At/near 20-day high — breakout territory", "positive"))
            elif p <= lo20 * 1.005:
                score -= 6
                sig.append(("At/near 20-day low — breakdown risk", "negative"))

        score = max(0.0, min(100.0, score))
        return {"score": score, "rating": self._score_to_rating(score), "signals": sig}

    def _score_short_term(self) -> dict[str, Any]:
        """1-3 month outlook: technical momentum, overbought/oversold."""
        if self.df is None or len(self.df) < 50:
            return {"score": 50.0, "rating": "HOLD",
                    "signals": [("Insufficient price history", "neutral")]}
        score = 50.0
        sig: list[tuple[str, str]] = []
        last = self.df.iloc[-1]

        rsi = last.get("RSI")
        if pd.notna(rsi):
            if rsi > 75:
                score -= 12
                sig.append((f"RSI severely overbought ({rsi:.1f})", "negative"))
            elif rsi > 70:
                score -= 8
                sig.append((f"RSI overbought ({rsi:.1f}) — consolidation risk", "negative"))
            elif rsi < 25:
                score += 18
                sig.append((f"RSI deeply oversold ({rsi:.1f}) — mean-reversion setup", "positive"))
            elif rsi < 30:
                score += 12
                sig.append((f"RSI oversold ({rsi:.1f})", "positive"))
            elif rsi > 55:
                score += 4
                sig.append((f"RSI bullish bias ({rsi:.1f})", "positive"))
            elif rsi < 45:
                score -= 4
                sig.append((f"RSI bearish bias ({rsi:.1f})", "negative"))
            else:
                sig.append((f"RSI neutral ({rsi:.1f})", "neutral"))

        p = last["Close"]
        s20 = last.get("SMA20")
        s50 = last.get("SMA50")
        if pd.notna(s20) and pd.notna(s50):
            if p > s20 > s50:
                score += 14
                sig.append(("Price above 20D and 50D — bullish MA alignment", "positive"))
            elif p < s20 < s50:
                score -= 14
                sig.append(("Price below 20D and 50D — bearish MA alignment", "negative"))
            elif p > s20:
                score += 5
                sig.append(("Price above 20D MA", "positive"))
            elif p < s50:
                score -= 5
                sig.append(("Price below 50D MA", "negative"))

        macd = last.get("MACD")
        sigl = last.get("Signal")
        if pd.notna(macd) and pd.notna(sigl):
            if macd > sigl and macd > 0:
                score += 10
                sig.append(("MACD positive and above signal — momentum bullish", "positive"))
            elif macd < sigl and macd < 0:
                score -= 10
                sig.append(("MACD negative and below signal — momentum bearish", "negative"))
            elif macd > sigl:
                score += 3
                sig.append(("MACD turning up", "positive"))
            else:
                score -= 3
                sig.append(("MACD turning down", "negative"))

        if len(self.df) >= 21:
            mom = (p / self.df["Close"].iloc[-21] - 1) * 100
            if mom > 12:
                score += 10
                sig.append((f"Strong 1-month momentum (+{mom:.1f}%)", "positive"))
            elif mom > 5:
                score += 4
                sig.append((f"Positive 1-month momentum (+{mom:.1f}%)", "positive"))
            elif mom < -12:
                score -= 10
                sig.append((f"Weak 1-month momentum ({mom:.1f}%)", "negative"))
            elif mom < -5:
                score -= 4
                sig.append((f"Negative 1-month momentum ({mom:.1f}%)", "negative"))
            else:
                sig.append((f"1-month flat ({mom:+.1f}%)", "neutral"))

        bb_up = last.get("BB_Up")
        bb_dn = last.get("BB_Dn")
        if pd.notna(bb_up) and pd.notna(bb_dn) and bb_up > bb_dn:
            pos = (p - bb_dn) / (bb_up - bb_dn)
            if pos > 0.97:
                score -= 4
                sig.append(("Near upper Bollinger — stretched", "negative"))
            elif pos < 0.03:
                score += 5
                sig.append(("Near lower Bollinger — coiled spring", "positive"))

        score = max(0.0, min(100.0, score))
        return {"score": score, "rating": self._score_to_rating(score), "signals": sig}

    def _score_medium_term(self) -> dict[str, Any]:
        """3-12 month outlook: trend, valuation, growth, analyst consensus."""
        if self.df is None:
            return {"score": 50.0, "rating": "HOLD",
                    "signals": [("Insufficient data", "neutral")]}
        score = 50.0
        sig: list[tuple[str, str]] = []
        last = self.df.iloc[-1]
        info = self.info

        s200 = last.get("SMA200")
        p = last["Close"]
        if pd.notna(s200) and s200 > 0:
            ratio = p / s200
            if ratio > 1.10:
                score += 14
                sig.append(("Price well above 200D MA — established uptrend", "positive"))
            elif ratio > 1.0:
                score += 8
                sig.append(("Price above 200D MA — uptrend", "positive"))
            elif ratio < 0.90:
                score -= 14
                sig.append(("Price well below 200D MA — established downtrend", "negative"))
            else:
                score -= 6
                sig.append(("Price below 200D MA", "negative"))

        if len(self.df) >= 126:
            ret6m = (p / self.df["Close"].iloc[-126] - 1) * 100
            if ret6m > 25:
                score += 8
                sig.append((f"Strong 6-month return (+{ret6m:.1f}%)", "positive"))
            elif ret6m > 10:
                score += 3
                sig.append((f"Positive 6-month return (+{ret6m:.1f}%)", "positive"))
            elif ret6m < -20:
                score -= 8
                sig.append((f"Weak 6-month return ({ret6m:.1f}%)", "negative"))
            else:
                sig.append((f"6-month return {ret6m:+.1f}%", "neutral"))

        pe = info.get("trailingPE") or info.get("forwardPE")
        if pe:
            if 0 < pe < 12:
                score += 12
                sig.append((f"Very attractive P/E ({pe:.1f})", "positive"))
            elif pe < 18:
                score += 6
                sig.append((f"Reasonable P/E ({pe:.1f})", "positive"))
            elif pe > 45:
                score -= 10
                sig.append((f"Premium P/E ({pe:.1f}) — valuation risk", "negative"))
            elif pe > 30:
                score -= 4
                sig.append((f"Elevated P/E ({pe:.1f})", "negative"))
            else:
                sig.append((f"Moderate P/E ({pe:.1f})", "neutral"))

        peg = info.get("pegRatio") or info.get("trailingPegRatio")
        if peg and peg > 0:
            if peg < 1.0:
                score += 6
                sig.append((f"PEG {peg:.2f} — growth at reasonable price", "positive"))
            elif peg > 2.5:
                score -= 5
                sig.append((f"PEG {peg:.2f} — growth expensive", "negative"))

        rg = info.get("revenueGrowth")
        if rg is not None:
            rg_pct = rg * 100
            if rg > 0.25:
                score += 12
                sig.append((f"Strong revenue growth (+{rg_pct:.1f}%)", "positive"))
            elif rg > 0.08:
                score += 5
                sig.append((f"Solid revenue growth (+{rg_pct:.1f}%)", "positive"))
            elif rg < -0.05:
                score -= 10
                sig.append((f"Revenue contraction ({rg_pct:.1f}%)", "negative"))
            elif rg < 0:
                score -= 5
                sig.append((f"Revenue declining ({rg_pct:.1f}%)", "negative"))
            else:
                sig.append((f"Modest revenue growth ({rg_pct:+.1f}%)", "neutral"))

        tgt = info.get("targetMeanPrice")
        cp = info.get("currentPrice") or p
        if tgt and cp:
            up = (tgt / cp - 1) * 100
            if up > 20:
                score += 9
                sig.append((f"Analyst upside +{up:.1f}% to ${tgt:.2f}", "positive"))
            elif up > 8:
                score += 4
                sig.append((f"Analyst upside +{up:.1f}% to ${tgt:.2f}", "positive"))
            elif up < -8:
                score -= 8
                sig.append((f"Analyst downside {up:+.1f}% to ${tgt:.2f}", "negative"))
            else:
                sig.append((f"Analyst target ${tgt:.2f} ({up:+.1f}%)", "neutral"))

        eg = info.get("earningsGrowth")
        if eg is not None:
            eg_pct = eg * 100
            if eg > 0.25:
                score += 8
                sig.append((f"Strong earnings growth (+{eg_pct:.1f}%)", "positive"))
            elif eg < -0.15:
                score -= 8
                sig.append((f"Earnings contraction ({eg_pct:.1f}%)", "negative"))

        score = max(0.0, min(100.0, score))
        return {"score": score, "rating": self._score_to_rating(score), "signals": sig}

    def _score_long_term(self) -> dict[str, Any]:
        """1-5 year outlook: profitability, capital structure, cash generation."""
        score = 50.0
        sig: list[tuple[str, str]] = []
        info = self.info

        roe = info.get("returnOnEquity")
        if roe is not None:
            if roe > 0.25:
                score += 14
                sig.append((f"Exceptional ROE ({roe * 100:.1f}%)", "positive"))
            elif roe > 0.15:
                score += 9
                sig.append((f"Strong ROE ({roe * 100:.1f}%)", "positive"))
            elif roe > 0.08:
                score += 3
                sig.append((f"Adequate ROE ({roe * 100:.1f}%)", "neutral"))
            elif roe > 0:
                score -= 5
                sig.append((f"Low ROE ({roe * 100:.1f}%)", "negative"))
            else:
                score -= 12
                sig.append(("Negative ROE — destroying capital", "negative"))

        roa = info.get("returnOnAssets")
        if roa is not None:
            if roa > 0.10:
                score += 5
                sig.append((f"Strong ROA ({roa * 100:.1f}%)", "positive"))
            elif roa < 0:
                score -= 5
                sig.append((f"Negative ROA ({roa * 100:.1f}%)", "negative"))

        pm = info.get("profitMargins")
        if pm is not None:
            pm_pct = pm * 100
            if pm > 0.25:
                score += 10
                sig.append((f"Excellent net margin ({pm_pct:.1f}%)", "positive"))
            elif pm > 0.10:
                score += 5
                sig.append((f"Healthy net margin ({pm_pct:.1f}%)", "positive"))
            elif pm < 0:
                score -= 12
                sig.append((f"Negative net margin ({pm_pct:.1f}%)", "negative"))
            elif pm < 0.03:
                score -= 5
                sig.append((f"Thin net margin ({pm_pct:.1f}%)", "negative"))

        om = info.get("operatingMargins")
        if om is not None:
            if om > 0.30:
                score += 7
                sig.append((f"Best-in-class operating margin ({om * 100:.1f}%)", "positive"))
            elif om > 0.15:
                score += 3
                sig.append((f"Solid operating margin ({om * 100:.1f}%)", "positive"))
            elif om < 0:
                score -= 8
                sig.append((f"Negative operating margin ({om * 100:.1f}%)", "negative"))

        de = info.get("debtToEquity")
        if de is not None:
            try:
                de_ratio = float(de) / 100.0
            except (TypeError, ValueError):
                de_ratio = None
            if de_ratio is not None:
                if de_ratio < 0.3:
                    score += 8
                    sig.append((f"Low leverage (D/E {de_ratio:.2f})", "positive"))
                elif de_ratio < 1.0:
                    score += 2
                    sig.append((f"Moderate leverage (D/E {de_ratio:.2f})", "neutral"))
                elif de_ratio > 2.5:
                    score -= 12
                    sig.append((f"High leverage (D/E {de_ratio:.2f})", "negative"))
                elif de_ratio > 1.5:
                    score -= 5
                    sig.append((f"Elevated leverage (D/E {de_ratio:.2f})", "negative"))

        fcf = info.get("freeCashflow")
        if fcf is not None:
            if fcf > 5e9:
                score += 10
                sig.append((f"Substantial FCF ({_fmt_money(fcf)})", "positive"))
            elif fcf > 1e9:
                score += 6
                sig.append((f"Strong FCF ({_fmt_money(fcf)})", "positive"))
            elif fcf > 0:
                score += 2
                sig.append((f"Positive FCF ({_fmt_money(fcf)})", "positive"))
            else:
                score -= 10
                sig.append(("Negative FCF — cash-flow risk", "negative"))

        cash = info.get("totalCash")
        debt = info.get("totalDebt")
        if cash is not None and debt is not None:
            net_cash = cash - debt
            if net_cash > 0:
                score += 4
                sig.append(
                    (f"Net cash position ({_fmt_money(net_cash)})", "positive")
                )
            elif net_cash < -50e9:
                score -= 4
                sig.append(
                    (f"Significant net debt ({_fmt_money(-net_cash)})", "negative")
                )

        dy = info.get("dividendYield")
        if dy and dy > 0:
            if dy > 0.04:
                score += 3
                sig.append((f"Attractive yield ({dy * 100:.2f}%)", "positive"))
            else:
                score += 1
                sig.append((f"Dividend yield {dy * 100:.2f}%", "neutral"))

        payout = info.get("payoutRatio")
        if payout is not None and payout > 0:
            if payout > 0.85:
                score -= 4
                sig.append(
                    (f"High payout ratio ({payout * 100:.1f}%) — dividend strain", "negative")
                )
            elif payout < 0.5:
                sig.append(
                    (f"Conservative payout ({payout * 100:.1f}%)", "positive")
                )

        beta = info.get("beta")
        if beta is not None:
            if beta > 1.6:
                sig.append((f"High beta ({beta:.2f}) — elevated volatility", "neutral"))
            elif beta < 0.7:
                sig.append((f"Low beta ({beta:.2f}) — defensive profile", "positive"))

        score = max(0.0, min(100.0, score))
        return {"score": score, "rating": self._score_to_rating(score), "signals": sig}

    # ------------------------------------------------------------------
    # Driver
    # ------------------------------------------------------------------
    def analyze(self) -> "StockAnalysis":
        self.fetch()
        self.compute_indicators()
        self.swing_term = self._score_swing_term()
        self.short_term = self._score_short_term()
        self.medium_term = self._score_medium_term()
        self.long_term = self._score_long_term()
        # Composite intentionally excludes the ultra-short swing score —
        # it covers a fundamentally different horizon and would drown out
        # the longer-term signal.
        self.composite = (
            self.short_term["score"]
            + self.medium_term["score"]
            + self.long_term["score"]
        ) / 3.0
        self.composite_rating = self._score_to_rating(self.composite)
        self.summary = self._build_summary()
        return self

    # ------------------------------------------------------------------
    # Narrative
    # ------------------------------------------------------------------
    def _build_summary(self) -> str:
        info = self.info
        name = info.get("longName") or info.get("shortName") or self.ticker
        sector = info.get("sector", "—")
        industry = info.get("industry", "—")
        rating = self.composite_rating

        last = self.df.iloc[-1]
        price = last["Close"]

        bull, bear = self._collect_signals()
        bull_line = "; ".join(bull[:4]) if bull else "—"
        bear_line = "; ".join(bear[:4]) if bear else "—"

        sw = self.swing_term.get("rating", "HOLD")
        st = self.short_term["rating"]
        mt = self.medium_term["rating"]
        lt = self.long_term["rating"]

        target = info.get("targetMeanPrice")
        upside = ((target / price - 1) * 100) if target else None
        upside_phrase = (
            f" Analyst consensus target ${target:.2f} ({upside:+.1f}%)."
            if upside is not None
            else ""
        )

        rev_growth = info.get("revenueGrowth")
        rev_phrase = (
            f" Revenue is {'growing' if rev_growth and rev_growth > 0 else 'contracting'} "
            f"at {_fmt_pct(rev_growth, 1)} year-on-year."
            if rev_growth is not None
            else ""
        )

        return (
            f"{name} ({self.ticker}) trades at ${price:.2f}. Sector: {sector} / {industry}. "
            f"Composite rating {rating} (score {self.composite:.0f}/100), with swing-trade {sw}, "
            f"short-term {st}, medium-term {mt}, and long-term {lt} outlooks.{upside_phrase}{rev_phrase} "
            f"Bullish drivers: {bull_line}. Bearish drivers: {bear_line}."
        )

    def _collect_signals(self) -> tuple[list[str], list[str]]:
        bull: list[str] = []
        bear: list[str] = []
        for bucket in (self.swing_term, self.short_term, self.medium_term, self.long_term):
            for text, polarity in bucket.get("signals", []):
                if polarity == "positive":
                    bull.append(text)
                elif polarity == "negative":
                    bear.append(text)
        return bull, bear


# ============================================================================
# Worker thread
# ============================================================================
class AnalysisWorker(QThread):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, ticker: str, parent=None) -> None:
        super().__init__(parent)
        self.ticker = ticker

    def run(self) -> None:  # type: ignore[override]
        try:
            self.progress.emit(f"Fetching market data for {self.ticker}…")
            analysis = StockAnalysis(self.ticker)
            analysis.fetch()
            self.progress.emit("Computing technical indicators…")
            analysis.compute_indicators()
            self.progress.emit("Scoring swing / short / medium / long-term horizons…")
            analysis.swing_term = analysis._score_swing_term()
            analysis.short_term = analysis._score_short_term()
            analysis.medium_term = analysis._score_medium_term()
            analysis.long_term = analysis._score_long_term()
            analysis.composite = (
                analysis.short_term["score"]
                + analysis.medium_term["score"]
                + analysis.long_term["score"]
            ) / 3.0
            analysis.composite_rating = StockAnalysis._score_to_rating(
                analysis.composite
            )
            analysis.summary = analysis._build_summary()
            self.finished.emit(analysis)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ============================================================================
# Smart search combo box
# ============================================================================
class SmartTickerCombo(QComboBox):
    """Editable combo with autocomplete on both symbol and company name."""

    ticker_chosen = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setMinimumWidth(360)
        self.setMaxVisibleItems(20)
        self._all: list[tuple[str, str]] = []
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self.setCompleter(self._completer)
        self.activated.connect(self._on_activated)
        self.lineEdit().returnPressed.connect(self._on_return)

    def set_universe(self, entries: list[tuple[str, str]]) -> None:
        self._all = sorted(set(entries), key=lambda t: t[0])
        self.blockSignals(True)
        self.clear()
        labels: list[str] = []
        for sym, name in self._all:
            label = f"{sym} — {name}"
            self.addItem(label, sym)
            labels.append(label)
        self.blockSignals(False)
        self._completer.setModel(QStringListModel(labels))
        self.setCurrentIndex(-1)
        self.setEditText("")

    def _on_activated(self, index: int) -> None:
        sym = self.itemData(index)
        if sym:
            self.ticker_chosen.emit(sym)

    def _on_return(self) -> None:
        text = self.currentText().strip().upper()
        if " — " in text:
            text = text.split(" — ", 1)[0].strip()
        elif " - " in text:
            text = text.split(" - ", 1)[0].strip()
        if text:
            self.ticker_chosen.emit(text)


# ============================================================================
# Screener — bulk scan and categorise a whole watchlist
# ============================================================================
@dataclass
class ScreenerRow:
    """One row of the bulk screener output."""

    ticker: str
    name: str
    sector: str
    price: float
    swing_score: float
    short_score: float
    medium_score: float
    long_score: float
    composite_score: float
    swing_rating: str
    short_rating: str
    medium_rating: str
    long_rating: str
    composite_rating: str
    bull_count: int
    bear_count: int
    # Indicator + fundamentals snapshot at scan time. Used by the
    # 'Strategy filter' dropdown to evaluate user-built rules.
    eval_context: dict = field(default_factory=dict)


class _NumericItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by an explicit numeric value."""

    def __init__(self, value: float, text: str) -> None:
        super().__init__(text)
        self._value = float(value)

    def __lt__(self, other: QTableWidgetItem) -> bool:  # type: ignore[override]
        if isinstance(other, _NumericItem):
            return self._value < other._value
        return super().__lt__(other)


class ScreenerWorker(QThread):
    """Runs the full analyzer against a list of tickers, emitting per-row results."""

    progress = Signal(int, int, str)        # done, total, current ticker
    row_ready = Signal(object)              # ScreenerRow
    row_failed = Signal(str, str)           # ticker, error message
    done = Signal(int, int)                 # success, failed

    def __init__(self, tickers: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self._tickers = list(tickers)
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # type: ignore[override]
        success = 0
        failed = 0
        total = len(self._tickers)
        for idx, (sym, name) in enumerate(self._tickers):
            if self._stop:
                break
            self.progress.emit(idx + 1, total, sym)
            try:
                a = StockAnalysis(sym)
                a.fetch()
                a.compute_indicators()
                a.swing_term = a._score_swing_term()
                a.short_term = a._score_short_term()
                a.medium_term = a._score_medium_term()
                a.long_term = a._score_long_term()
                a.composite = (
                    a.short_term["score"]
                    + a.medium_term["score"]
                    + a.long_term["score"]
                ) / 3.0
                a.composite_rating = StockAnalysis._score_to_rating(a.composite)
                bull, bear = a._collect_signals()
                last_price = float(a.df["Close"].iloc[-1]) if a.df is not None else 0.0
                # Build a per-bar evaluation context so the Strategy filter
                # dropdown can evaluate user rules against this row.
                eval_ctx: dict = {}
                try:
                    from strategy_dsl import build_context as _bc
                    eval_ctx = _bc(a.df, len(a.df) - 1, a.info) if a.df is not None else {}
                except Exception:
                    eval_ctx = {}
                row = ScreenerRow(
                    ticker=sym,
                    name=name or a.info.get("shortName", sym),
                    sector=(a.info.get("sector") or "—"),
                    price=last_price,
                    swing_score=float(a.swing_term.get("score", 50.0)),
                    short_score=float(a.short_term.get("score", 50.0)),
                    medium_score=float(a.medium_term.get("score", 50.0)),
                    long_score=float(a.long_term.get("score", 50.0)),
                    composite_score=float(a.composite),
                    swing_rating=a.swing_term.get("rating", "HOLD"),
                    short_rating=a.short_term.get("rating", "HOLD"),
                    medium_rating=a.medium_term.get("rating", "HOLD"),
                    long_rating=a.long_term.get("rating", "HOLD"),
                    composite_rating=a.composite_rating,
                    bull_count=len(bull),
                    bear_count=len(bear),
                    eval_context=eval_ctx,
                )
                self.row_ready.emit(row)
                success += 1
            except Exception as exc:  # pragma: no cover
                self.row_failed.emit(sym, f"{type(exc).__name__}: {exc}")
                failed += 1
        self.done.emit(success, failed)


class ScreenerTab(QWidget):
    """A watchlist screener: scans a universe, categorises every stock, filters by rating."""

    open_ticker = Signal(str)
    # Emitted when the user picks 'Open in Charts' from the screener context menu.
    open_in_chart = Signal(str)
    # Emitted when the user materialises the filtered results as a new
    # custom watchlist: (source_name, sector_name, [[ticker, name], ...])
    watchlist_added = Signal(str, str, list)
    RATINGS: list[str] = ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
    HORIZONS: list[tuple[str, str]] = [
        ("composite", "Composite (overall)"),
        ("swing", "Swing Trade (1 week - 1 month)"),
        ("short", "Short-Term (1-3 months)"),
        ("medium", "Medium-Term (3-12 months)"),
        ("long", "Long-Term (1-5 years)"),
    ]

    def __init__(self, watchlists: dict, parent=None) -> None:
        super().__init__(parent)
        self._watchlists = watchlists
        self._rows: list[ScreenerRow] = []
        self._horizon: str = "composite"
        self._active_ratings: set[str] = set(self.RATINGS)
        self._worker: ScreenerWorker | None = None
        self._failed: list[tuple[str, str]] = []
        self._custom_strategies: dict = {}
        self._build_ui()
        self._populate_universe_combo()
        self._update_counts()
        # Best-effort initial load of vault strategies; safe if vault missing
        try:
            self.refresh_strategies()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(8)

        # Row 1 — universe + horizon + actions
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QLabel("Watchlist:"))
        self.universe_combo = QComboBox()
        self.universe_combo.setMinimumWidth(320)
        row1.addWidget(self.universe_combo)

        row1.addSpacing(18)
        row1.addWidget(QLabel("Rate by:"))
        self.horizon_combo = QComboBox()
        for _key, label in self.HORIZONS:
            self.horizon_combo.addItem(label)
        self.horizon_combo.currentIndexChanged.connect(self._on_horizon_changed)
        self.horizon_combo.setMinimumWidth(260)
        row1.addWidget(self.horizon_combo)

        row1.addSpacing(12)
        row1.addWidget(QLabel("Strategy filter:"))
        self.strategy_filter_combo = QComboBox()
        self.strategy_filter_combo.setMinimumWidth(220)
        self.strategy_filter_combo.setToolTip(
            "Optional. When set, only rows where the strategy's BUY rule "
            "evaluates True against the latest bar are shown. Manage "
            "strategies in the Strategy Builder tab."
        )
        self.strategy_filter_combo.addItem("(none)", None)
        self.strategy_filter_combo.currentIndexChanged.connect(self._on_strategy_filter_changed)
        row1.addWidget(self.strategy_filter_combo)

        row1.addStretch(1)
        self.start_btn = QPushButton("▶  Start Scan")
        self.start_btn.clicked.connect(self._start_scan)
        row1.addWidget(self.start_btn)
        self.stop_btn = QPushButton("⏹  Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_scan)
        row1.addWidget(self.stop_btn)
        # Export CSV + 'Export filtered only' checkbox stacked vertically
        export_box = QVBoxLayout()
        export_box.setSpacing(2)
        export_box.setContentsMargins(0, 0, 0, 0)
        self.export_btn = QPushButton("Export CSV")
        self.export_btn.clicked.connect(self._export_csv)
        self.export_btn.setEnabled(False)
        export_box.addWidget(self.export_btn)
        self.export_filtered_check = QCheckBox("Export filtered only")
        self.export_filtered_check.setToolTip(
            "When checked, Export CSV only writes the rows currently visible "
            "in the table (after the rating filter chips have been applied)."
        )
        self.export_filtered_check.setStyleSheet(
            f"color:{COLOR_MUTED}; font-size:11px;"
        )
        export_box.addWidget(self.export_filtered_check)
        row1.addLayout(export_box)

        # 'Filtered → WL' — promote the currently-visible rows to a saved watchlist
        self.filtered_to_wl_btn = QPushButton("Filtered → WL")
        self.filtered_to_wl_btn.setToolTip(
            "Save the currently filtered tickers as a new custom watchlist. "
            "The new watchlist appears in the sidebar tree and the Screener "
            "dropdown, and is persisted to watchlists.json."
        )
        self.filtered_to_wl_btn.setEnabled(False)
        self.filtered_to_wl_btn.clicked.connect(self._filtered_to_watchlist)
        row1.addWidget(self.filtered_to_wl_btn)
        root.addLayout(row1)

        # Row 2 — rating filter chips + counts
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        flbl = QLabel("Show:")
        flbl.setStyleSheet("font-weight: 600;")
        row2.addWidget(flbl)
        self.filter_buttons: dict[str, QPushButton] = {}
        for rating in self.RATINGS:
            btn = QPushButton(rating)
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._chip_stylesheet(self._rating_color(rating)))
            btn.clicked.connect(self._on_filter_clicked)
            self.filter_buttons[rating] = btn
            row2.addWidget(btn)
        row2.addSpacing(16)
        self.counts_label = QLabel("")
        self.counts_label.setTextFormat(Qt.RichText)
        row2.addWidget(self.counts_label, 1)
        root.addLayout(row2)

        # Progress + status
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(0)
        self.scan_progress.setMaximumHeight(10)
        self.scan_progress.setTextVisible(False)
        root.addWidget(self.scan_progress)

        self.status_label = QLabel("Pick a watchlist and press Start Scan.")
        self.status_label.setStyleSheet(f"color:{COLOR_MUTED}; font-size:12px;")
        root.addWidget(self.status_label)

        # Results table
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["Ticker", "Company", "Sector", "Price",
             "Swing", "Short", "Medium", "Long", "Composite", "Rating"]
        )
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(False)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (0, 2, 3, 4, 5, 6, 7, 8, 9):
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemDoubleClicked.connect(self._on_row_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        root.addWidget(self.table, 1)

    def _populate_universe_combo(self) -> None:
        self.universe_combo.clear()
        # "All" entries first
        for source, sectors in self._watchlists.items():
            total = sum(len(t) for t in sectors.values())
            self.universe_combo.addItem(
                f"All {source}  ({total} tickers)", ("all", source)
            )
        # Then per-sector entries
        for source, sectors in self._watchlists.items():
            for sector, tickers in sectors.items():
                self.universe_combo.addItem(
                    f"    {source}  ·  {sector}  ({len(tickers)})",
                    ("sector", source, sector),
                )

    # ------------------------------------------------------------------
    # Universe resolution
    # ------------------------------------------------------------------
    def _selected_universe(self) -> list[tuple[str, str]]:
        data = self.universe_combo.currentData()
        if not data:
            return []
        kind = data[0]
        out: list[tuple[str, str]] = []
        seen: set[str] = set()

        def take(entry: Any) -> None:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                sym, name = entry[0], entry[1]
            else:
                sym = str(entry)
                name = sym
            if sym not in seen:
                seen.add(sym)
                out.append((sym, name))

        if kind == "all":
            source = data[1]
            for tickers in self._watchlists[source].values():
                for entry in tickers:
                    take(entry)
        elif kind == "sector":
            _, source, sector = data
            for entry in self._watchlists[source][sector]:
                take(entry)
        return out

    # ------------------------------------------------------------------
    # Scan lifecycle
    # ------------------------------------------------------------------
    def _start_scan(self) -> None:
        tickers = self._selected_universe()
        if not tickers:
            QMessageBox.information(
                self, "No tickers", "The selected watchlist is empty."
            )
            return
        if not YFINANCE_AVAILABLE:
            QMessageBox.critical(
                self,
                "Missing dependency",
                "yfinance is required for the screener.\n\n"
                "Install with:\n  pip install yfinance pandas numpy matplotlib",
            )
            return
        self._rows = []
        self._failed = []
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.scan_progress.setRange(0, len(tickers))
        self.scan_progress.setValue(0)
        self.scan_progress.setTextVisible(True)
        self.scan_progress.setFormat("%v / %m   %p%")
        self.start_btn.setEnabled(False)
        self.universe_combo.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.status_label.setText(
            f"Scanning {len(tickers)} tickers — results stream in as they finish."
        )
        self._worker = ScreenerWorker(tickers, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.row_ready.connect(self._on_row)
        self._worker.row_failed.connect(self._on_row_failed)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _stop_scan(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self.status_label.setText("Stopping after current ticker…")
            self.stop_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int, current: str) -> None:
        self.scan_progress.setMaximum(total)
        self.scan_progress.setValue(done)
        self.status_label.setText(f"Analyzing {current}   ·   {done}/{total}")

    def _on_row(self, row: ScreenerRow) -> None:
        self._rows.append(row)
        if self._row_matches_filter(row):
            self._append_row_to_table(row)
        self._update_counts()

    def _on_row_failed(self, sym: str, err: str) -> None:
        self._failed.append((sym, err))

    def _on_done(self, success: int, failed: int) -> None:
        self.start_btn.setEnabled(True)
        self.universe_combo.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(bool(self._rows))
        self.filtered_to_wl_btn.setEnabled(bool(self._rows))
        self.table.setSortingEnabled(True)
        fail_part = f", {failed} failed" if failed else ""
        self.status_label.setText(
            f"Done — {success} analyzed{fail_part}. Click headers to sort, "
            f"double-click a row to deep-dive."
        )

    # ------------------------------------------------------------------
    # Horizon + filter handlers
    # ------------------------------------------------------------------
    def _on_horizon_changed(self, idx: int) -> None:
        self._horizon = self.HORIZONS[idx][0]
        self._rerender_table()

    def _on_strategy_filter_changed(self, idx: int) -> None:
        self._rerender_table()

    def refresh_strategies(self, custom_strategies: dict | None = None) -> None:
        """Repopulate the Strategy filter dropdown from the vault.
        ``custom_strategies`` is the dict returned by
        ``load_custom_strategies_from_vault``; if None, we try to load it.
        """
        if custom_strategies is None:
            try:
                from pathlib import Path as _Path
                from backtest_strategies import load_custom_strategies_from_vault
                custom_strategies = load_custom_strategies_from_vault(
                    _Path(__file__).resolve().parent
                )
            except Exception:
                custom_strategies = {}
        self._custom_strategies = custom_strategies
        current = self.strategy_filter_combo.currentText()
        self.strategy_filter_combo.blockSignals(True)
        self.strategy_filter_combo.clear()
        self.strategy_filter_combo.addItem("(none)", None)
        for name in custom_strategies:
            self.strategy_filter_combo.addItem(name, name)
        self.strategy_filter_combo.blockSignals(False)
        idx = self.strategy_filter_combo.findText(current) if current else 0
        if idx >= 0:
            self.strategy_filter_combo.setCurrentIndex(idx)

    def _on_filter_clicked(self) -> None:
        self._active_ratings = {
            r for r, b in self.filter_buttons.items() if b.isChecked()
        }
        self._rerender_table()

    def _row_matches_filter(self, row: ScreenerRow) -> bool:
        # 1) Rating-chip filter (existing behaviour)
        if self._horizon_rating(row) not in self._active_ratings:
            return False
        # 2) Custom-strategy filter — must pass the BUY rule when selected
        sname = self.strategy_filter_combo.currentData() if hasattr(self, 'strategy_filter_combo') else None
        if sname and sname in self._custom_strategies:
            strat = self._custom_strategies[sname]
            ctx = getattr(row, 'eval_context', {}) or {}
            if not ctx:
                return False
            try:
                # No prior bar available on a single-row snapshot; pass ctx as both
                # so static (non-crossover) conditions still work cleanly.
                from strategy_dsl import evaluate as _eval, _truthy as _t
                ast = strat._dsl._buy_ast
                if ast is None:
                    return False
                return _t(_eval(ast, ctx, ctx))
            except Exception:
                return False
        return True

    def _horizon_rating(self, row: ScreenerRow) -> str:
        return {
            "swing": row.swing_rating,
            "short": row.short_rating,
            "medium": row.medium_rating,
            "long": row.long_rating,
            "composite": row.composite_rating,
        }[self._horizon]

    def _horizon_score(self, row: ScreenerRow) -> float:
        return {
            "swing": row.swing_score,
            "short": row.short_score,
            "medium": row.medium_score,
            "long": row.long_score,
            "composite": row.composite_score,
        }[self._horizon]

    # ------------------------------------------------------------------
    # Table rendering
    # ------------------------------------------------------------------
    def _rerender_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for row in self._rows:
            if self._row_matches_filter(row):
                self._append_row_to_table(row)
        self.table.setSortingEnabled(True)
        self._update_counts()

    def _append_row_to_table(self, row: ScreenerRow) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        rating = self._horizon_rating(row)
        rc = self._rating_color(rating)

        sym_item = QTableWidgetItem(row.ticker)
        sym_item.setForeground(QColor(COLOR_ACCENT))
        f = sym_item.font()
        f.setBold(True)
        sym_item.setFont(f)
        self.table.setItem(r, 0, sym_item)

        name_item = QTableWidgetItem(row.name)
        self.table.setItem(r, 1, name_item)

        sec_item = QTableWidgetItem(row.sector)
        sec_item.setForeground(QColor(COLOR_MUTED))
        self.table.setItem(r, 2, sec_item)

        price_item = _NumericItem(row.price, f"${row.price:,.2f}")
        price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.table.setItem(r, 3, price_item)

        for col, score in (
            (4, row.swing_score),
            (5, row.short_score),
            (6, row.medium_score),
            (7, row.long_score),
            (8, row.composite_score),
        ):
            cell = _NumericItem(score, f"{score:.0f}")
            cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            # Tint the active-horizon column for orientation
            if (
                (col == 4 and self._horizon == "swing")
                or (col == 5 and self._horizon == "short")
                or (col == 6 and self._horizon == "medium")
                or (col == 7 and self._horizon == "long")
                or (col == 8 and self._horizon == "composite")
            ):
                cell.setForeground(QColor(rc))
                fb = cell.font()
                fb.setBold(True)
                cell.setFont(fb)
            self.table.setItem(r, col, cell)

        rating_item = QTableWidgetItem(rating)
        rating_item.setTextAlignment(Qt.AlignCenter)
        rating_item.setForeground(QColor(rc))
        rf = rating_item.font()
        rf.setBold(True)
        rating_item.setFont(rf)
        self.table.setItem(r, 9, rating_item)

    def _update_counts(self) -> None:
        counts = {r: 0 for r in self.RATINGS}
        for row in self._rows:
            counts[self._horizon_rating(row)] = counts.get(
                self._horizon_rating(row), 0
            ) + 1
        parts = []
        for r in self.RATINGS:
            c = counts.get(r, 0)
            color = self._rating_color(r)
            parts.append(
                f"<span style='color:{color};font-weight:600;'>{r}: {c}</span>"
            )
        total = len(self._rows)
        if total:
            parts.append(
                f"<span style='color:{COLOR_MUTED};'>Total: {total}</span>"
            )
        self.counts_label.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts))

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def _on_row_double_clicked(self, item: QTableWidgetItem) -> None:
        r = item.row()
        sym_item = self.table.item(r, 0)
        if sym_item:
            self.open_ticker.emit(sym_item.text())

    def _on_table_context_menu(self, pos) -> None:
        """Right-click on a screener result row → Open in Overview / Charts."""
        item = self.table.itemAt(pos)
        if item is None:
            return
        r = item.row()
        sym_item = self.table.item(r, 0)
        if not sym_item:
            return
        ticker = sym_item.text().strip().upper()
        if not ticker:
            return
        # Build the menu
        menu = QMenu(self.table)
        act_charts = menu.addAction(f"\U0001F4C8  Open '{ticker}' in Charts")
        act_overview = menu.addAction(f"Open '{ticker}' in Overview")
        menu.addSeparator()
        # Bulk action — send all currently filtered symbols to charts is
        # offered as a sub-prompt for power users.
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_charts:
            self.open_in_chart.emit(ticker)
        elif chosen is act_overview:
            self.open_ticker.emit(ticker)

    def _filtered_rows(self) -> list[ScreenerRow]:
        """Rows currently passing the rating-chip filter."""
        return [r for r in self._rows if self._row_matches_filter(r)]

    def _export_csv(self) -> None:
        if not self._rows:
            return
        only_filtered = self.export_filtered_check.isChecked()
        rows_to_export = self._filtered_rows() if only_filtered else self._rows
        if not rows_to_export:
            QMessageBox.information(
                self, "Nothing to export",
                "No rows currently match the active rating filters.\n\n"
                "Toggle on the rating chips you want to include, or uncheck "
                "'Export filtered only' to export the full result set.",
            )
            return
        suffix = "_filtered" if only_filtered else ""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Screener Results",
            f"screener{suffix}_{datetime.now():%Y%m%d_%H%M}.csv",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(
                    [
                        "Ticker", "Company", "Sector", "Price",
                        "SwingScore", "ShortScore", "MediumScore", "LongScore", "CompositeScore",
                        "SwingRating", "ShortRating", "MediumRating", "LongRating", "CompositeRating",
                        "BullCount", "BearCount",
                    ]
                )
                for row in rows_to_export:
                    w.writerow(
                        [
                            row.ticker, row.name, row.sector, f"{row.price:.2f}",
                            f"{row.swing_score:.1f}", f"{row.short_score:.1f}",
                            f"{row.medium_score:.1f}", f"{row.long_score:.1f}",
                            f"{row.composite_score:.1f}",
                            row.swing_rating, row.short_rating, row.medium_rating,
                            row.long_rating, row.composite_rating,
                            row.bull_count, row.bear_count,
                        ]
                    )
            scope = "filtered" if only_filtered else "all"
            QMessageBox.information(
                self, "Exported",
                f"Saved {len(rows_to_export)} {scope} rows to:\n{path}",
            )
        except Exception as exc:  # pragma: no cover
            QMessageBox.warning(self, "Export failed", str(exc))

    def _filtered_to_watchlist(self) -> None:
        """Promote the currently visible rows to a new custom watchlist."""
        rows = self._filtered_rows()
        if not rows:
            QMessageBox.information(
                self, "No rows",
                "There are no filtered rows to save. Run a scan and adjust the "
                "rating filters first.",
            )
            return
        # Build a descriptive default name based on the active filters
        active_chips = sorted(self._active_ratings)
        chip_str = (
            "+".join(r.split()[0] for r in active_chips)
            if 0 < len(active_chips) < len(self.RATINGS) else "all"
        )
        horizon_label = next(
            (lbl for k, lbl in self.HORIZONS if k == self._horizon), self._horizon,
        ).split(" (")[0]
        default_name = (
            f"{horizon_label} · {chip_str} · {datetime.now():%Y-%m-%d %H:%M}"
        )
        name, ok = QInputDialog.getText(
            self, "Save filtered watchlist",
            f"Name this watchlist  ({len(rows)} ticker(s)):",
            text=default_name,
        )
        if not ok or not name.strip():
            return
        entries: list[list[str]] = [[r.ticker, r.name] for r in rows]
        # Top-level source is fixed; sector slot uses the chosen name
        self.watchlist_added.emit("Custom", name.strip(), entries)
        QMessageBox.information(
            self, "Watchlist saved",
            f"Saved {len(entries)} ticker(s) as 'Custom · {name.strip()}'.\n\n"
            "It's now available in the sidebar tree and the Screener dropdown.",
        )

    # ------------------------------------------------------------------
    # Styling helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _rating_color(rating: str) -> str:
        if "STRONG BUY" in rating:
            return COLOR_GREEN
        if rating == "BUY":
            return COLOR_TEAL
        if rating == "HOLD":
            return COLOR_AMBER
        if rating == "SELL":
            return COLOR_ORANGE
        return COLOR_RED

    @staticmethod
    def _chip_stylesheet(color: str) -> str:
        return f"""
            QPushButton {{
                background: transparent;
                color: {color};
                border: 1.5px solid {color};
                border-radius: 12px;
                padding: 3px 12px;
                font-weight: 600;
                font-size: 11px;
                min-height: 18px;
            }}
            QPushButton:checked {{
                background: {color};
                color: {COLOR_BG};
            }}
            QPushButton:hover {{
                background: rgba(137, 180, 250, 0.10);
            }}
        """


# ============================================================================
# Main window
# ============================================================================
# ============================================================================
# Main window
# ============================================================================
class StockAnalyzerApp(QWidget):
    watchlist_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("US Stock Analyzer & Advisor")
        self.resize(1500, 950)
        self.db = DatabaseManager()
        self.master_data = {}
        self._load_master_csv()
        self.watchlists = {}
        self.load_watchlists_from_db()
        self.current_ticker: str | None = None
        self.current_analysis: StockAnalysis | None = None
        self.worker: AnalysisWorker | None = None
        self._build_ui()
        self._populate_tree()
        self._populate_combo()
        self._wire_shortcuts()
        self._apply_dark_theme()
        self._render_welcome()
        self.status_bar.showMessage(
            "Ready. Pick a ticker from the tree or smart search; Ctrl+R analyzes."
        )

    def _load_master_csv(self):
        path = Path(__file__).resolve().parent.parent.parent / "USStockMaster.csv"
        if not path.exists():
            path = Path(__file__).resolve().parent.parent / "USStockMaster.csv"
        if not path.exists():
            path = Path("USStockMaster.csv")
        if not path.exists():
            return
        try:
            with open(str(path), mode="r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    sym = row["Symbol"].upper()
                    self.master_data[sym] = row.get("Company", row.get("Name", ""))
        except Exception:
            pass

    def load_watchlists_from_db(self):
        self.watchlists.clear()
        self.watchlists["S&P 500"] = {}
        self.watchlists["NASDAQ 100"] = {}
        self.watchlists["Custom"] = {}
        
        try:
            constituents = self.db.get_all_constituents()
            for c in constituents:
                sym = c["symbol"]
                name = c["company_name"] or sym
                sec = c["sector"] or "Other"
                if c["is_sp500"]:
                    self.watchlists["S&P 500"].setdefault(sec, []).append([sym, name])
                if c["is_nasdaq"]:
                    self.watchlists["NASDAQ 100"].setdefault(sec, []).append([sym, name])
        except Exception as e:
            print(f"Error loading constituents: {e}")
            
        if not self.watchlists["S&P 500"]:
            self.watchlists["S&P 500"] = dict(EMBEDDED_WATCHLISTS["S&P 500"])
        if not self.watchlists["NASDAQ 100"]:
            self.watchlists["NASDAQ 100"] = dict(EMBEDDED_WATCHLISTS["NASDAQ 100"])
            
        try:
            user_lists = self.db.get_watchlists()
            for wl in user_lists:
                wl_name = wl["name"]
                display_name = wl_name[3:] if wl_name.startswith("US_") else wl_name
                items = self.db.get_watchlist_items(wl_name)
                tickers = []
                for it in items:
                    sym = it["symbol"]
                    name = self.master_data.get(sym, sym)
                    tickers.append([sym, name])
                if tickers:
                    self.watchlists["Custom"][display_name] = tickers
        except Exception as e:
            print(f"Error loading custom watchlists: {e}")
            
        if not self.watchlists["Custom"]:
            self.watchlists.pop("Custom", None)
            
        return self.watchlists

    @Slot()
    def handle_external_watchlist_change(self):
        self.load_watchlists_from_db()
        self._refresh_watchlist_views()

    def statusBar(self):
        return self.status_bar

    # ------------------------------------------------------------------
    # UI assembly
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ---- top bar -------------------------------------------------
        topbar = QHBoxLayout()
        topbar.setSpacing(8)

        lbl = QLabel("Smart Symbol Search:")
        lbl.setStyleSheet("font-weight: 600;")
        topbar.addWidget(lbl)

        self.combo = SmartTickerCombo()
        self.combo.ticker_chosen.connect(self._on_combo_chosen)
        topbar.addWidget(self.combo)

        self.analyze_btn = QPushButton("Analyze (Ctrl+R)")
        self.analyze_btn.clicked.connect(self.run_analysis)
        topbar.addWidget(self.analyze_btn)

        self.export_btn = QPushButton("Export Report (Ctrl+S)")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_report)
        topbar.addWidget(self.export_btn)

        topbar.addStretch(1)
        self.headline = QLabel("")
        self.headline.setStyleSheet(
            f"color:{COLOR_TEAL}; font-weight: 700; font-size: 13px;"
        )
        topbar.addWidget(self.headline)

        # ---- Ancient-gold digital clock in the top-right corner ------
        # Subtle vertical divider before the clock so it reads as its own zone
        topbar.addSpacing(14)
        self.clock_label = QLabel("")
        self.clock_label.setObjectName("clockLabel")
        self.clock_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # 'Ancient gold' (#D4AF37) with a darker shadow / border for the
        # antiqued metallic look. Monospace digits keep it from jittering.
        self.clock_label.setStyleSheet(
            "QLabel#clockLabel {"
            "  color: #D4AF37;"                         # ancient gold
            "  background: #161219;"                    # parchment-dark backplate
            "  border: 1px solid #8C6A1A;"               # antique edge
            "  border-radius: 6px;"
            "  padding: 4px 12px;"
            "  font-family: 'Consolas', 'Menlo', 'Courier New', monospace;"
            "  font-weight: 700;"
            "  font-size: 13px;"
            "  letter-spacing: 1px;"
            "}"
        )
        topbar.addWidget(self.clock_label)

        # 1-second tick. Fired immediately on start so the label isn't blank.
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._update_clock()

        root.addLayout(topbar)

        # ---- main splitter ------------------------------------------
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)

        # Tree container — header row with navigation buttons + tree below
        tree_container = QWidget()
        tree_container.setMinimumWidth(290)
        tree_v = QVBoxLayout(tree_container)
        tree_v.setContentsMargins(0, 0, 0, 0)
        tree_v.setSpacing(4)

        tree_header = QHBoxLayout()
        tree_header.setContentsMargins(2, 0, 2, 0)
        tree_header.setSpacing(4)
        tree_caption = QLabel("Watchlists by Sector")
        tree_caption.setStyleSheet(
            f"color:{COLOR_TEAL}; font-weight: 700; font-size: 12px;"
        )
        tree_header.addWidget(tree_caption)
        tree_header.addStretch(1)

        self.nav_up_btn = QPushButton("▲ UP")
        self.nav_up_btn.setToolTip(
            "Move selection to previous ticker (does not analyze)"
        )
        self.nav_up_btn.setObjectName("navButton")
        self.nav_up_btn.setFixedHeight(24)
        self.nav_up_btn.setCursor(Qt.PointingHandCursor)
        self.nav_up_btn.clicked.connect(lambda: self._navigate_ticker(-1))
        tree_header.addWidget(self.nav_up_btn)

        self.nav_dn_btn = QPushButton("▼ DN")
        self.nav_dn_btn.setToolTip(
            "Move selection to next ticker (does not analyze)"
        )
        self.nav_dn_btn.setObjectName("navButton")
        self.nav_dn_btn.setFixedHeight(24)
        self.nav_dn_btn.setCursor(Qt.PointingHandCursor)
        self.nav_dn_btn.clicked.connect(lambda: self._navigate_ticker(+1))
        tree_header.addWidget(self.nav_dn_btn)

        self.nav_a_btn = QPushButton("A")
        self.nav_a_btn.setToolTip(
            "Analyze the currently-selected ticker"
        )
        self.nav_a_btn.setObjectName("navActionButton")
        self.nav_a_btn.setFixedHeight(24)
        self.nav_a_btn.setFixedWidth(28)
        self.nav_a_btn.setCursor(Qt.PointingHandCursor)
        self.nav_a_btn.clicked.connect(self.run_analysis)
        tree_header.addWidget(self.nav_a_btn)

        # ---- Neon-blue double-arrow toggle, level with the header ----
        tree_header.addSpacing(4)
        self.sidebar_toggle_btn = QPushButton("\u25C0\u25C0")  # ◀◀
        self.sidebar_toggle_btn.setObjectName("sidebarToggleBtn")
        self.sidebar_toggle_btn.setToolTip("Collapse sidebar")
        self.sidebar_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.sidebar_toggle_btn.setFixedSize(36, 24)
        self.sidebar_toggle_btn.clicked.connect(self._toggle_sidebar)
        self.sidebar_toggle_btn.setStyleSheet(
            "QPushButton#sidebarToggleBtn {"
            "  color: #00E5FF; background: #0A1822;"
            "  border: 1px solid #1FB8E0; border-radius: 6px;"
            "  font-weight: 800; font-size: 13px; letter-spacing: 1px;"
            "  padding: 0px;"
            "}"
            "QPushButton#sidebarToggleBtn:hover {"
            "  color: #FFFFFF; background: #0F2937;"
            "  border: 1px solid #00E5FF;"
            "}"
        )
        tree_header.addWidget(self.sidebar_toggle_btn)

        # Save handles for the collapse-to-strip behaviour
        self._tree_header_widgets = [
            tree_caption, self.nav_up_btn, self.nav_dn_btn, self.nav_a_btn,
        ]
        self._tree_container = tree_container

        tree_v.addLayout(tree_header)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Watchlists by Sector"])
        self.tree.itemActivated.connect(self._on_tree_activated)
        self.tree.itemClicked.connect(self._on_tree_clicked)
        self.tree.currentItemChanged.connect(self._on_tree_current_changed)
        # Right-click context menu — used to rename / delete custom watchlists.
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        tree_v.addWidget(self.tree, 1)

        splitter.addWidget(tree_container)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.overview = QTextBrowser()
        self.overview.setOpenExternalLinks(True)
        self.tabs.addTab(self.overview, "Overview")

        self.fundamentals = QTextBrowser()
        self.fundamentals.setOpenExternalLinks(True)
        self.tabs.addTab(self.fundamentals, "Fundamentals")

        # Technicals (chart + textual signals)
        tech_widget = QWidget()
        tech_layout = QVBoxLayout(tech_widget)
        tech_layout.setContentsMargins(0, 0, 0, 0)
        tech_layout.setSpacing(0)

        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(8, 6), facecolor=COLOR_BG)
            self.canvas = FigureCanvas(self.figure)
            tech_layout.addWidget(self.canvas, 3)
        else:  # pragma: no cover
            placeholder = QLabel(
                "matplotlib not installed — install it to see charts."
            )
            placeholder.setAlignment(Qt.AlignCenter)
            tech_layout.addWidget(placeholder, 3)

        self.tech_signals = QTextBrowser()
        tech_layout.addWidget(self.tech_signals, 2)
        self.tabs.addTab(tech_widget, "Technicals")

        # ---- Professional interactive chart ----
        try:
            from charting_module import ProfessionalChart
            self.charts_tab = ProfessionalChart()
            self.charts_tab.navigate_symbol.connect(self._navigate_chart_symbol)
            self.tabs.addTab(self.charts_tab, "Charts")
        except Exception as _c_exc:  # pragma: no cover
            print(f"[warn] Charts tab unavailable: {_c_exc}", file=sys.stderr)
            self.charts_tab = None

        self.report = QTextBrowser()
        self.report.setOpenExternalLinks(True)
        self.tabs.addTab(self.report, "Recommendation")

        # Screener — bulk scanner across a chosen watchlist
        self.screener = ScreenerTab(self.watchlists)
        self.screener.open_ticker.connect(self._open_from_screener)
        self.screener.open_in_chart.connect(self._open_in_chart)
        self.screener.watchlist_added.connect(self._on_watchlist_added)
        self.tabs.addTab(self.screener, "Screener")

        # Strategy Builder — DSL editor + vault + fetch-from-web
        try:
            from strategy_builder_tab import StrategyBuilderTab
            _here = Path(__file__).resolve().parent
            self.strategy_builder_tab = StrategyBuilderTab(_here)
            self.strategy_builder_tab.strategies_changed.connect(
                self._on_strategies_changed
            )
            self.tabs.addTab(self.strategy_builder_tab, "Strategy Builder")
        except Exception as _sb_exc:  # pragma: no cover
            print(f"[warn] Strategy Builder tab unavailable: {_sb_exc}", file=sys.stderr)
            self.strategy_builder_tab = None

        # Backtest — strategy testing on the watchlists
        try:
            from backtest_tab import BacktestTab
            self.backtest_tab = BacktestTab(
                self.watchlists,
                base_dir=str(Path(__file__).resolve().parent),
            )
            self.tabs.addTab(self.backtest_tab, "Backtest")
        except Exception as _bt_exc:  # pragma: no cover
            print(f"[warn] Backtest tab unavailable: {_bt_exc}", file=sys.stderr)
            self.backtest_tab = None

        # Paper trading — persistent simulated brokerage
        try:
            from paper_trading_tab import PaperTradingTab
            _paper_state = Path(__file__).resolve().parent / "paper_account.json"
            self.paper_trading_tab = PaperTradingTab(str(_paper_state))
            self.tabs.addTab(self.paper_trading_tab, "Paper Trading")
        except Exception as _pt_exc:  # pragma: no cover
            print(f"[warn] Paper Trading tab unavailable: {_pt_exc}", file=sys.stderr)

        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([310, 1180])
        self._splitter = splitter
        self._sidebar_collapsed = False
        self._sidebar_last_width = 310
        root.addWidget(splitter, 1)

        # Connect tab activation so the Charts tab auto-loads the
        # currently-selected ticker when the user switches to it.
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # ---- progress + status --------------------------------------
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumHeight(6)
        self.progress.hide()
        root.addWidget(self.progress)

        self.status_bar = QStatusBar()
        root.addWidget(self.status_bar)

    def _update_clock(self) -> None:
        """Refresh the ancient-gold clock label."""
        now = datetime.now()
        # e.g. 'Mon · 2026-05-15 · 14:32:08'
        self.clock_label.setText(
            now.strftime("%a  ·  %Y-%m-%d  ·  %H:%M:%S")
        )

    def _toggle_sidebar(self) -> None:
        """Collapse the sidebar to a thin strip (only the neon arrow stays
        visible) or restore it to its previous width.
        """
        if not hasattr(self, "_splitter") or self._splitter is None:
            return
        sizes = self._splitter.sizes()
        total = sum(sizes) if sizes else 1490
        STRIP_WIDTH = 48  # just enough to host the toggle button
        if not self._sidebar_collapsed:
            # Remember the width so we can restore it on expand
            if sizes and sizes[0] > STRIP_WIDTH:
                self._sidebar_last_width = sizes[0]
            # Hide every widget in the sidebar except the toggle button
            for w in getattr(self, "_tree_header_widgets", []):
                w.setVisible(False)
            if hasattr(self, "tree"):
                self.tree.setVisible(False)
            # Allow the splitter to shrink to the strip width
            if hasattr(self, "_tree_container") and self._tree_container is not None:
                self._tree_container.setMinimumWidth(STRIP_WIDTH)
            self._splitter.setSizes([STRIP_WIDTH, max(total - STRIP_WIDTH, 200)])
            self._sidebar_collapsed = True
            self.sidebar_toggle_btn.setText("\u25B6\u25B6")  # ▶▶
            self.sidebar_toggle_btn.setToolTip("Expand sidebar")
        else:
            # Show everything again
            for w in getattr(self, "_tree_header_widgets", []):
                w.setVisible(True)
            if hasattr(self, "tree"):
                self.tree.setVisible(True)
            if hasattr(self, "_tree_container") and self._tree_container is not None:
                self._tree_container.setMinimumWidth(290)
            w = max(self._sidebar_last_width or 310, 290)
            self._splitter.setSizes([w, max(total - w, 400)])
            self._sidebar_collapsed = False
            self.sidebar_toggle_btn.setText("\u25C0\u25C0")  # ◀◀
            self.sidebar_toggle_btn.setToolTip("Collapse sidebar")

    def _populate_tree(self) -> None:
        self.tree.clear()
        for source, sectors in self.watchlists.items():
            src_item = QTreeWidgetItem([source])
            f = src_item.font(0)
            f.setBold(True)
            src_item.setFont(0, f)
            src_item.setForeground(0, QColor(COLOR_ACCENT))
            total = 0
            for sector, tickers in sectors.items():
                sec_item = QTreeWidgetItem([f"{sector}  ({len(tickers)})"])
                sec_item.setForeground(0, QColor(COLOR_TEAL))
                # Tag custom-watchlist sector nodes so the right-click handler
                # can distinguish them from built-in watchlists.
                if source == "Custom":
                    sec_item.setData(0, Qt.UserRole + 1, sector)
                    # Give custom lists a distinctive purple tint
                    sec_item.setForeground(0, QColor(COLOR_PURPLE))
                for entry in tickers:
                    if isinstance(entry, list) and len(entry) >= 2:
                        sym, name = entry[0], entry[1]
                    else:
                        sym = str(entry)
                        name = sym
                    leaf = QTreeWidgetItem([f"{sym} — {name}"])
                    leaf.setData(0, Qt.UserRole, sym)
                    sec_item.addChild(leaf)
                src_item.addChild(sec_item)
                total += len(tickers)
            src_item.setText(0, f"{source}  ({total} tickers)")
            self.tree.addTopLevelItem(src_item)
        self.tree.expandToDepth(0)

    def _populate_combo(self) -> None:
        seen: set[str] = set()
        universe: list[tuple[str, str]] = []
        for sectors in self.watchlists.values():
            for tickers in sectors.values():
                for entry in tickers:
                    if isinstance(entry, list) and len(entry) >= 2:
                        sym, name = entry[0], entry[1]
                    else:
                        sym = str(entry)
                        name = sym
                    if sym not in seen:
                        seen.add(sym)
                        universe.append((sym, name))
        self.combo.set_universe(universe)

    def _wire_shortcuts(self) -> None:
        QShortcut(
            QKeySequence("Ctrl+F"),
            self,
            activated=lambda: (
                self.combo.setFocus(),
                self.combo.lineEdit().selectAll(),
            ),
        )
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.run_analysis)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.export_report)
        QShortcut(QKeySequence("Ctrl+Down"), self, activated=self._next_ticker)
        QShortcut(QKeySequence("Ctrl+Up"), self, activated=self._prev_ticker)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def _apply_dark_theme(self) -> None:
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(COLOR_BG))
        pal.setColor(QPalette.Base, QColor(COLOR_SURFACE))
        pal.setColor(QPalette.AlternateBase, QColor(COLOR_PANEL))
        pal.setColor(QPalette.Text, QColor(COLOR_TEXT))
        pal.setColor(QPalette.WindowText, QColor(COLOR_TEXT))
        pal.setColor(QPalette.Button, QColor(COLOR_PANEL))
        pal.setColor(QPalette.ButtonText, QColor(COLOR_TEXT))
        pal.setColor(QPalette.Highlight, QColor(COLOR_ACCENT))
        pal.setColor(QPalette.HighlightedText, QColor(COLOR_BG))
        pal.setColor(QPalette.ToolTipBase, QColor(COLOR_PANEL))
        pal.setColor(QPalette.ToolTipText, QColor(COLOR_TEXT))
        QApplication.instance().setPalette(pal)

        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{ background:{COLOR_BG}; color:{COLOR_TEXT};
                font-family: 'Segoe UI', 'SF Pro Text', 'Inter', sans-serif;
                font-size: 13px; }}
            QTreeWidget, QTextBrowser {{ background:{COLOR_SURFACE};
                color:{COLOR_TEXT}; border:1px solid {COLOR_BORDER};
                selection-background-color:{COLOR_ACCENT};
                selection-color:{COLOR_BG}; }}
            QTreeWidget::item {{ padding: 3px 2px; }}
            QTreeWidget::item:hover {{ background:{COLOR_PANEL}; }}
            QTreeWidget::item:selected {{ background:{COLOR_ACCENT}; color:{COLOR_BG}; }}
            QComboBox, QLineEdit {{ background:{COLOR_SURFACE};
                color:{COLOR_TEXT}; border:1px solid {COLOR_BORDER};
                padding:6px 8px; border-radius:4px; }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{ background:{COLOR_SURFACE};
                color:{COLOR_TEXT}; selection-background-color:{COLOR_ACCENT};
                selection-color:{COLOR_BG}; border:1px solid {COLOR_BORDER}; }}
            QPushButton {{ background:{COLOR_ACCENT}; color:{COLOR_BG};
                padding:6px 14px; border-radius:4px; font-weight:600; border: none; }}
            QPushButton:hover {{ background:{COLOR_ACCENT_HOVER}; }}
            QPushButton:disabled {{ background:{COLOR_PANEL};
                color:{COLOR_DIM}; }}
            QPushButton#navButton {{ background:{COLOR_PANEL}; color:{COLOR_TEXT};
                padding:2px 8px; border-radius:4px; font-weight:700; font-size:11px;
                border:1px solid {COLOR_BORDER}; }}
            QPushButton#navButton:hover {{ background:{COLOR_BORDER};
                color:{COLOR_ACCENT}; border-color:{COLOR_ACCENT}; }}
            QPushButton#navActionButton {{ background:{COLOR_GREEN}; color:{COLOR_BG};
                padding:2px 4px; border-radius:4px; font-weight:800; font-size:12px;
                border: none; }}
            QPushButton#navActionButton:hover {{ background:{COLOR_TEAL}; }}
            QTabWidget::pane {{ border: 1px solid {COLOR_BORDER};
                background:{COLOR_SURFACE}; top: -1px; }}
            QTabBar::tab {{ background:{COLOR_PANEL}; color:{COLOR_TEXT};
                padding:7px 16px; border:none; margin-right: 1px; }}
            QTabBar::tab:selected {{ background:{COLOR_ACCENT}; color:{COLOR_BG};
                font-weight: 600; }}
            QTabBar::tab:hover:!selected {{ background:{COLOR_BORDER}; }}
            QStatusBar {{ background:{COLOR_SURFACE}; color:{COLOR_MUTED};
                border-top: 1px solid {COLOR_BORDER}; }}
            QProgressBar {{ border: none; background:{COLOR_SURFACE}; }}
            QProgressBar::chunk {{ background:{COLOR_ACCENT}; }}
            QHeaderView::section {{ background:{COLOR_PANEL};
                color:{COLOR_TEXT}; padding:6px; border:none;
                border-bottom: 1px solid {COLOR_BORDER}; font-weight:600; }}
            QTableWidget {{ background:{COLOR_SURFACE}; color:{COLOR_TEXT};
                border: 1px solid {COLOR_BORDER}; gridline-color:{COLOR_BORDER};
                alternate-background-color:{COLOR_BG};
                selection-background-color:{COLOR_ACCENT};
                selection-color:{COLOR_BG}; }}
            QTableWidget::item {{ padding: 5px 8px; }}
            QTableWidget::item:selected {{ background:{COLOR_ACCENT}; color:{COLOR_BG}; }}
            QTableCornerButton::section {{ background:{COLOR_PANEL};
                border: none; border-bottom: 1px solid {COLOR_BORDER}; }}
            QScrollBar:vertical {{ background:{COLOR_BG}; width: 12px;
                border: none; }}
            QScrollBar::handle:vertical {{ background:{COLOR_BORDER};
                border-radius: 4px; min-height: 20px; }}
            QScrollBar::handle:vertical:hover {{ background:{COLOR_DIM}; }}
            QScrollBar:horizontal {{ background:{COLOR_BG}; height: 12px;
                border: none; }}
            QScrollBar::handle:horizontal {{ background:{COLOR_BORDER};
                border-radius: 4px; min-width: 20px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ background: none;
                border: none; height: 0; width: 0; }}
            """
        )

    # ------------------------------------------------------------------
    # Tree / combo handlers
    # ------------------------------------------------------------------
    def _collect_leaves(self) -> list[QTreeWidgetItem]:
        out: list[QTreeWidgetItem] = []

        def walk(item: QTreeWidgetItem) -> None:
            if item.data(0, Qt.UserRole):
                out.append(item)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return out

    def _next_ticker(self) -> None:
        self._move_ticker(+1)

    def _prev_ticker(self) -> None:
        self._move_ticker(-1)

    def _move_ticker(self, step: int) -> None:
        leaves = self._collect_leaves()
        if not leaves:
            return
        cur = self.tree.currentItem()
        idx = leaves.index(cur) if cur in leaves else -1
        idx = (idx + step) % len(leaves)
        target = leaves[idx]
        self.tree.setCurrentItem(target)
        self.tree.scrollToItem(target)
        self._on_tree_clicked(target, 0)
        self.run_analysis()

    def _navigate_ticker(self, step: int) -> None:
        """Move the selection to the next/previous leaf ticker WITHOUT
        kicking off an analysis. Used by the sidebar UP / DN buttons so
        the user can scan through symbols by mouse and then press A to
        analyze the one they want."""
        leaves = self._collect_leaves()
        if not leaves:
            return
        cur = self.tree.currentItem()
        idx = leaves.index(cur) if cur in leaves else -1
        idx = (idx + step) % len(leaves)
        target = leaves[idx]
        self.tree.setCurrentItem(target)
        self.tree.scrollToItem(target)
        self._on_tree_clicked(target, 0)
        self.tree.setFocus()

    def _on_tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        sym = item.data(0, Qt.UserRole)
        if sym:
            self.current_ticker = sym
            self.combo.setEditText(item.text(0))
            self.headline.setText(f"Selected  ›  {sym}")

    def _on_tree_current_changed(
        self, current: QTreeWidgetItem | None, _prev: QTreeWidgetItem | None
    ) -> None:
        if current is not None:
            self._on_tree_clicked(current, 0)

    def _on_tree_activated(self, item: QTreeWidgetItem, _col: int) -> None:
        sym = item.data(0, Qt.UserRole)
        if sym:
            self.current_ticker = sym
            self.run_analysis()

    def _on_combo_chosen(self, sym: str) -> None:
        self.current_ticker = sym.upper()
        self.headline.setText(f"Selected  ›  {self.current_ticker}")
        self.run_analysis()

    def _on_tab_changed(self, idx: int) -> None:
        """When the user switches tabs, auto-load the chart for the
        currently-selected ticker if the Charts tab just became active.
        Avoids re-fetching when it's already the same symbol.
        """
        try:
            label = self.tabs.tabText(idx)
        except Exception:
            return
        if label != "Charts":
            return
        charts = getattr(self, "charts_tab", None)
        if charts is None:
            return
        tk = (self.current_ticker or "").strip().upper()
        if not tk:
            return
        prev = getattr(charts, "_ticker", "")
        if prev == tk:
            return  # already loaded
        try:
            charts.set_ticker(tk)
        except Exception as exc:  # pragma: no cover
            print(f"[warn] auto-load chart failed: {exc}", file=sys.stderr)

    def _navigate_chart_symbol(self, step: int) -> None:
        """Move the Charts tab to the next (+1) or previous (-1) symbol
        from the sidebar tree's flat leaf order; keeps the sidebar
        selection + ``current_ticker`` in sync."""
        leaves = self._collect_leaves()
        if not leaves:
            return
        cur_ticker = ""
        charts = getattr(self, "charts_tab", None)
        if charts is not None:
            cur_ticker = (getattr(charts, "_ticker", "") or "").upper()
        if not cur_ticker:
            cur_ticker = (self.current_ticker or "").upper()
        cur_idx = -1
        for i, leaf in enumerate(leaves):
            sym = (leaf.data(0, Qt.UserRole) or "").upper()
            if sym == cur_ticker:
                cur_idx = i; break
        if cur_idx < 0:
            cur_idx = 0 if step > 0 else len(leaves) - 1
        else:
            cur_idx = (cur_idx + step) % len(leaves)
        new_leaf = leaves[cur_idx]
        new_ticker = new_leaf.data(0, Qt.UserRole)
        if not new_ticker:
            return
        self.tree.setCurrentItem(new_leaf)
        self.tree.scrollToItem(new_leaf)
        self._on_tree_clicked(new_leaf, 0)
        if charts is not None:
            try:
                charts.set_ticker(new_ticker)
            except Exception as exc:  # pragma: no cover
                print(f"[warn] navigate chart failed: {exc}", file=sys.stderr)

    def _open_in_chart(self, sym: str) -> None:
        """Switch to the Charts tab and load the symbol.

        Updates ``current_ticker`` first so the auto-load that fires from
        ``_on_tab_changed`` (when ``setCurrentIndex`` changes the active
        tab) uses the new symbol — otherwise a stale
        ``current_ticker`` value would race with the explicit
        ``set_ticker`` below.
        """
        if not getattr(self, "charts_tab", None):
            QMessageBox.information(
                self, "Charts unavailable",
                "The Charts tab failed to load (likely a missing dependency).\n"
                "Run:  pip install matplotlib yfinance pandas numpy",
            )
            return
        ticker = (sym or "").strip().upper()
        if not ticker:
            return
        # Critical: update the selected ticker BEFORE switching tabs so the
        # auto-load triggered by `_on_tab_changed` sees the right symbol.
        self.current_ticker = ticker
        try:
            self.headline.setText(f"Selected  \u203a  {ticker}")
        except Exception:
            pass
        # Find and activate the Charts tab
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "Charts":
                self.tabs.setCurrentIndex(i)
                break
        try:
            # set_ticker is safe to call repeatedly — _fetch now detaches
            # any stale worker before starting a new one.
            self.charts_tab.set_ticker(ticker)
            self.statusBar().showMessage(f"Loaded {ticker} in Charts.", 4000)
        except Exception as exc:  # pragma: no cover
            QMessageBox.warning(self, "Chart load failed", str(exc))

    def _open_from_screener(self, sym: str) -> None:
        """Bring a ticker from the Screener tab into the main analyzer."""
        self.current_ticker = sym.upper()
        self.combo.setEditText(self.current_ticker)
        self.headline.setText(f"Selected  ›  {self.current_ticker}")
        self.tabs.setCurrentIndex(0)  # jump to Overview
        self.run_analysis()

    # ------------------------------------------------------------------
    # Custom watchlists
    # ------------------------------------------------------------------
    def _on_watchlist_added(
        self, source: str, sector: str, entries: list[list[str]]
    ) -> None:
        """Merge a freshly-created watchlist into the live dict, refresh every
        widget that displays watchlists, and persist back to watchlists.json and SQLite.
        """
        if not entries:
            return
        bucket = self.watchlists.setdefault(source, {})
        bucket[sector] = entries
        self._refresh_watchlist_views()
        self._persist_watchlists()
        
        # SQLite Sync
        if source == "Custom" and self.db:
            try:
                wl_name = "US_" + sector
                self.db.create_watchlist(wl_name)
                with self.db._conn() as conn:
                    conn.execute("DELETE FROM watchlist_items WHERE watchlist_name=?", (wl_name,))
                for entry in entries:
                    sym = entry[0]
                    self.db.add_to_watchlist(wl_name, sym)
                self.watchlist_changed.emit()
            except Exception as e:
                print(f"SQLite Sync Error in _on_watchlist_added: {e}")
                
        self.statusBar().showMessage(
            f"Saved custom watchlist '{source} · {sector}' "
            f"({len(entries)} tickers).", 8000,
        )

    # ------------------------------------------------------------------
    # Sidebar context menu (Custom watchlists)
    # ------------------------------------------------------------------
    def _on_tree_context_menu(self, pos) -> None:
        """Right-click on the sidebar tree:
         • on a leaf ticker  → 'Open in Charts' / 'Analyze in Overview'
         • on a custom-watchlist sector → 'Rename / Delete'
         • on anything else  → no menu
        """
        item = self.tree.itemAt(pos)
        if item is None:
            return
        sym = item.data(0, Qt.UserRole)            # leaf ticker (e.g. 'AAPL')
        sector_name = item.data(0, Qt.UserRole + 1)  # tagged custom watchlist

        if sym:
            ticker = str(sym).upper()
            menu = QMenu(self.tree)
            act_charts = menu.addAction(f"\U0001F4C8  Open '{ticker}' in Charts")
            act_analyze = menu.addAction(f"Analyze '{ticker}' in Overview")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_charts:
                self._open_in_chart(ticker)
            elif chosen is act_analyze:
                self.current_ticker = ticker
                self.headline.setText(f"Selected  \u203a  {ticker}")
                self.run_analysis()
            return

        if sector_name:
            menu = QMenu(self.tree)
            rename_action = menu.addAction("Rename…")
            delete_action = menu.addAction("Delete")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is rename_action:
                self._rename_custom_watchlist(sector_name)
            elif chosen is delete_action:
                self._delete_custom_watchlist(sector_name)

    def _rename_custom_watchlist(self, old_name: str) -> None:
        bucket = self.watchlists.get("Custom", {})
        if old_name not in bucket:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename watchlist",
            f"New name for 'Custom · {old_name}':",
            text=old_name,
        )
        new_name = (new_name or "").strip()
        if not ok or not new_name or new_name == old_name:
            return
        if new_name in bucket:
            QMessageBox.warning(
                self, "Name already exists",
                f"A custom watchlist named '{new_name}' already exists.",
            )
            return
            
        # SQLite Sync
        if self.db:
            try:
                self.db.rename_watchlist("US_" + old_name, "US_" + new_name)
                self.watchlist_changed.emit()
            except Exception as e:
                print(f"SQLite Sync Error in _rename_custom_watchlist: {e}")
                
        # Preserve insertion order: rebuild the bucket dict
        new_bucket: dict = {}
        for k, v in bucket.items():
            new_bucket[new_name if k == old_name else k] = v
        self.watchlists["Custom"] = new_bucket
        self._refresh_watchlist_views()
        self._persist_watchlists()
        self.statusBar().showMessage(
            f"Renamed 'Custom · {old_name}' → 'Custom · {new_name}'.", 6000,
        )

    def _delete_custom_watchlist(self, name: str) -> None:
        bucket = self.watchlists.get("Custom", {})
        if name not in bucket:
            return
        count = len(bucket[name])
        reply = QMessageBox.question(
            self, "Delete watchlist?",
            f"Delete custom watchlist 'Custom · {name}' "
            f"({count} ticker(s))?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
            
        # SQLite Sync
        if self.db:
            try:
                self.db.delete_watchlist("US_" + name)
                self.watchlist_changed.emit()
            except Exception as e:
                print(f"SQLite Sync Error in _delete_custom_watchlist: {e}")
                
        del bucket[name]
        # If the Custom bucket is now empty, drop it from the tree entirely
        # so we don't leave an empty top-level node lying around.
        if not bucket:
            self.watchlists.pop("Custom", None)
        self._refresh_watchlist_views()
        self._persist_watchlists()
        self.statusBar().showMessage(
            f"Deleted 'Custom · {name}' ({count} ticker(s)).", 6000,
        )

    def _refresh_watchlist_views(self) -> None:
        """Rebuild every UI widget that surfaces the watchlist dict."""
        self._populate_tree()
        self._populate_combo()
        try:
            self.screener._populate_universe_combo()
        except Exception:
            pass

    def _persist_watchlists(self) -> None:
        """Write self.watchlists back to watchlists.json on disk."""
        try:
            here = Path(__file__).resolve().parent
            wl_path = here / "watchlists.json"
            with wl_path.open("w", encoding="utf-8") as fh:
                json.dump(self.watchlists, fh, indent=2, ensure_ascii=False)
        except Exception as exc:  # pragma: no cover
            self.statusBar().showMessage(
                f"Failed to save watchlists.json: {exc}", 8000,
            )

    # ------------------------------------------------------------------
    # Strategy vault broadcast
    # ------------------------------------------------------------------
    def _on_strategies_changed(self) -> None:
        """Strategy Builder tab persisted a change. Refresh every tab that
        shows or uses custom strategies."""
        if getattr(self, "backtest_tab", None) is not None:
            try:
                self.backtest_tab.refresh_strategies()
            except Exception:
                pass
        if getattr(self, "screener", None) is not None:
            try:
                self.screener.refresh_strategies()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Analysis flow
    # ------------------------------------------------------------------
    def run_analysis(self) -> None:
        if not self.current_ticker:
            QMessageBox.information(
                self,
                "Pick a ticker",
                "Select a ticker from the watchlist tree or type one into the "
                "search box, then press Enter or click Analyze.",
            )
            return
        if not YFINANCE_AVAILABLE:
            QMessageBox.critical(
                self,
                "Missing dependency",
                "yfinance is not installed.\n\n"
                "Install with:\n  pip install yfinance pandas numpy matplotlib",
            )
            return
        if self.worker and self.worker.isRunning():
            return
        self.analyze_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.progress.show()
        self.statusBar().showMessage(f"Analyzing {self.current_ticker}…")
        self.worker = AnalysisWorker(self.current_ticker, self)
        self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.finished.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_done(self, analysis: StockAnalysis) -> None:
        self.current_analysis = analysis
        self.progress.hide()
        self.analyze_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.headline.setText(
            f"{analysis.ticker}  ·  composite {analysis.composite:.0f}  ·  "
            f"{analysis.composite_rating}"
        )
        self._render_overview(analysis)
        self._render_fundamentals(analysis)
        self._render_technicals(analysis)
        self._render_recommendation(analysis)
        self.statusBar().showMessage(
            f"Done — {analysis.ticker} rated {analysis.composite_rating}."
        )

    def _on_failed(self, msg: str) -> None:
        self.progress.hide()
        self.analyze_btn.setEnabled(True)
        QMessageBox.warning(self, "Analysis failed", msg)
        self.statusBar().showMessage(f"Failed: {msg}")

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------
    def _render_welcome(self) -> None:
        html = f"""
        <html><body style='font-family:Segoe UI,Arial,sans-serif;
        color:{COLOR_TEXT}; background:{COLOR_SURFACE}; padding:24px;'>
        <h1 style='color:{COLOR_ACCENT}; margin-bottom:4px;'>
            US Stock Analyzer &amp; Advisor</h1>
        <p style='color:{COLOR_MUTED}; margin-top:0;'>
            Sophisticated multi-horizon equity research, in a desktop UI.</p>
        <h3 style='color:{COLOR_TEAL};'>How to use</h3>
        <ol>
            <li>Browse <b>S&amp;P 500</b> or <b>NASDAQ 100</b> tickers in the sector
                tree on the left, or type a symbol / company name into the smart
                search box at the top.</li>
            <li>Press <b>Enter</b> or <b>Ctrl+R</b> (or click <b>Analyze</b>) to
                fetch live data and run the multi-horizon scoring engine.</li>
            <li>Inspect the <b>Overview</b>, <b>Fundamentals</b>,
                <b>Technicals</b> and <b>Recommendation</b> tabs.</li>
            <li>Press <b>Ctrl+Down</b> / <b>Ctrl+Up</b> to walk through every
                ticker in the watchlists sequentially.</li>
            <li>Export the rendered report to HTML with <b>Ctrl+S</b>.</li>
            <li>Open the <b>Screener</b> tab to bulk-scan any S&amp;P 500 or
                NASDAQ 100 sector — every stock gets a STRONG BUY / BUY / HOLD /
                SELL / STRONG SELL tag, filterable by horizon. Double-click a
                row to deep-dive that ticker in the Overview tab.</li>
        </ol>
        <h3 style='color:{COLOR_TEAL};'>How the scoring works</h3>
        <ul>
            <li><b>Swing Trade (1 week - 1 month)</b> — 5-day &amp; 10-day
                momentum, 10D MA proximity, RSI mean-reversion edges, MACD
                histogram acceleration, Bollinger breakouts &amp; squeezes,
                volume surges, ATR-relative thrust, 20-day high/low.</li>
            <li><b>Short-term (1-3 months)</b> — RSI, MACD, moving-average
                alignment, 1-month momentum, Bollinger Band position.</li>
            <li><b>Medium-term (3-12 months)</b> — 200-day MA trend, 6-month
                return, P/E and PEG valuation, revenue growth, analyst
                consensus target, earnings growth.</li>
            <li><b>Long-term (1-5 years)</b> — ROE, ROA, net and operating
                margins, leverage, free cash flow, balance-sheet net cash,
                dividend &amp; payout ratio, beta.</li>
        </ul>
        <p style='color:{COLOR_MUTED}; font-size:12px;'>
            Data via Yahoo Finance through <code>yfinance</code>. This tool is
            informational only — not investment advice.</p>
        </body></html>
        """
        self.overview.setHtml(html)
        for view in (self.fundamentals, self.tech_signals, self.report):
            view.setHtml(
                f"<body style='background:{COLOR_SURFACE};color:{COLOR_MUTED};"
                f"font-family:Segoe UI;padding:24px;'>"
                f"Pick a ticker and click Analyze to populate this tab.</body>"
            )

    @staticmethod
    def _rating_color(rating: str) -> str:
        if "STRONG BUY" in rating:
            return COLOR_GREEN
        if rating == "BUY":
            return COLOR_TEAL
        if rating == "HOLD":
            return COLOR_AMBER
        if rating == "SELL":
            return COLOR_ORANGE
        return COLOR_RED

    def _render_overview(self, a: StockAnalysis) -> None:
        info = a.info
        last = a.df.iloc[-1]
        price = last["Close"]
        prev = a.df["Close"].iloc[-2] if len(a.df) >= 2 else price
        chg = price - prev
        chg_pct = (chg / prev * 100) if prev else 0.0
        chg_color = COLOR_GREEN if chg >= 0 else COLOR_RED

        name = info.get("longName") or info.get("shortName") or a.ticker
        sector = info.get("sector", "—")
        industry = info.get("industry", "—")
        country = info.get("country", "—")
        website = info.get("website", "")
        web_link = (
            f"<a href='{website}' style='color:{COLOR_ACCENT};'>{website}</a>"
            if website
            else "—"
        )
        summary_text = info.get("longBusinessSummary", "")
        if len(summary_text) > 900:
            summary_text = summary_text[:900].rsplit(" ", 1)[0] + " …"

        mc = info.get("marketCap")
        wk52_hi = info.get("fiftyTwoWeekHigh")
        wk52_lo = info.get("fiftyTwoWeekLow")
        ev = info.get("enterpriseValue")
        pe = info.get("trailingPE") or info.get("forwardPE")
        beta = info.get("beta")
        ytd = None
        if len(a.df) >= 252:
            ytd = (price / a.df["Close"].iloc[-252] - 1) * 100

        rating_color = self._rating_color(a.composite_rating)

        rows = [
            ("Sector / Industry", f"{sector} · {industry}"),
            ("Country", country),
            ("Website", web_link),
            ("Market Cap", _fmt_money(mc)),
            ("Enterprise Value", _fmt_money(ev)),
            ("P/E (ttm or fwd)", _fmt_num(pe, 1)),
            ("Beta", _fmt_num(beta, 2)),
            (
                "52-Week Range",
                f"${_fmt_num(wk52_lo, 2)} – ${_fmt_num(wk52_hi, 2)}"
                if wk52_lo and wk52_hi
                else "—",
            ),
            (
                "1-Year Return",
                f"{ytd:+.1f}%" if ytd is not None else "—",
            ),
        ]
        rows_html = "".join(
            f"<tr><td style='padding:6px 12px;color:{COLOR_MUTED};"
            f"border-bottom:1px solid {COLOR_BORDER};'>{k}</td>"
            f"<td style='padding:6px 12px;font-weight:600;"
            f"border-bottom:1px solid {COLOR_BORDER};'>{v}</td></tr>"
            for k, v in rows
        )

        html = f"""
        <html><body style='background:{COLOR_SURFACE};color:{COLOR_TEXT};
        font-family:Segoe UI,Arial,sans-serif; padding:20px;'>
        <h1 style='color:{COLOR_ACCENT}; margin-bottom:0;'>
            {name} <span style='color:{COLOR_MUTED}; font-weight:400;'>({a.ticker})</span></h1>
        <div style='display:flex; gap:24px; align-items:baseline; margin-top:6px;'>
          <div style='font-size:30px; font-weight:700;'>${price:.2f}</div>
          <div style='font-size:16px; color:{chg_color}; font-weight:600;'>
            {chg:+.2f}  ({chg_pct:+.2f}%) today</div>
          <div style='font-size:16px; color:{rating_color};
              border:1.5px solid {rating_color}; padding:3px 10px;
              border-radius:14px; font-weight:700;'>{a.composite_rating}</div>
          <div style='color:{COLOR_MUTED};'>composite score
              <b style='color:{COLOR_TEXT};'>{a.composite:.0f}</b> / 100</div>
        </div>

        <table style='margin-top:18px; border-collapse:collapse; width:100%;
            font-size:13px;'>
          {rows_html}
        </table>

        <h3 style='color:{COLOR_TEAL}; margin-top:24px;'>Business Description</h3>
        <p style='line-height:1.55; color:{COLOR_TEXT};'>
            {summary_text or '<i>No description available.</i>'}</p>

        <h3 style='color:{COLOR_TEAL}; margin-top:18px;'>Executive Summary</h3>
        <p style='line-height:1.55;'>{a.summary}</p>

        <p style='color:{COLOR_DIM}; font-size:11px; margin-top:24px;'>
            Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ·
            Data via Yahoo Finance (yfinance).</p>
        </body></html>
        """
        self.overview.setHtml(html)

    def _render_fundamentals(self, a: StockAnalysis) -> None:
        info = a.info

        def row(label: str, value: str, hint: str = "") -> str:
            hint_html = (
                f"<div style='color:{COLOR_DIM}; font-size:11px; margin-top:2px;'>{hint}</div>"
                if hint
                else ""
            )
            return (
                f"<tr><td style='padding:6px 14px;color:{COLOR_MUTED};"
                f"border-bottom:1px solid {COLOR_BORDER};vertical-align:top;'>{label}</td>"
                f"<td style='padding:6px 14px;font-weight:600;"
                f"border-bottom:1px solid {COLOR_BORDER};'>{value}{hint_html}</td></tr>"
            )

        valuation = [
            row("Market Cap", _fmt_money(info.get("marketCap"))),
            row("Enterprise Value", _fmt_money(info.get("enterpriseValue"))),
            row("Trailing P/E", _fmt_num(info.get("trailingPE"), 1)),
            row("Forward P/E", _fmt_num(info.get("forwardPE"), 1)),
            row("PEG Ratio", _fmt_num(info.get("pegRatio") or info.get("trailingPegRatio"), 2)),
            row("Price / Book", _fmt_num(info.get("priceToBook"), 2)),
            row("EV / Revenue", _fmt_num(info.get("enterpriseToRevenue"), 2)),
            row("EV / EBITDA", _fmt_num(info.get("enterpriseToEbitda"), 2)),
        ]
        profitability = [
            row("Gross Margins", _fmt_pct(info.get("grossMargins"))),
            row("Operating Margins", _fmt_pct(info.get("operatingMargins"))),
            row("Profit Margins", _fmt_pct(info.get("profitMargins"))),
            row("Return on Equity", _fmt_pct(info.get("returnOnEquity"))),
            row("Return on Assets", _fmt_pct(info.get("returnOnAssets"))),
        ]
        growth = [
            row("Revenue Growth (YoY)", _fmt_pct(info.get("revenueGrowth"))),
            row("Earnings Growth (YoY)", _fmt_pct(info.get("earningsGrowth"))),
            row("Quarterly Earnings Growth", _fmt_pct(info.get("earningsQuarterlyGrowth"))),
            row("Quarterly Revenue Growth", _fmt_pct(info.get("revenueQuarterlyGrowth"))),
        ]
        balance_sheet = [
            row("Total Cash", _fmt_money(info.get("totalCash"))),
            row("Total Debt", _fmt_money(info.get("totalDebt"))),
            row(
                "Net Cash / (Debt)",
                _fmt_money((info.get("totalCash") or 0) - (info.get("totalDebt") or 0)),
            ),
            row(
                "Debt-to-Equity",
                _fmt_num(
                    (info.get("debtToEquity") / 100) if info.get("debtToEquity") else None,
                    2,
                ),
            ),
            row("Current Ratio", _fmt_num(info.get("currentRatio"), 2)),
            row("Quick Ratio", _fmt_num(info.get("quickRatio"), 2)),
        ]
        cash_flow = [
            row("Operating Cash Flow", _fmt_money(info.get("operatingCashflow"))),
            row("Free Cash Flow", _fmt_money(info.get("freeCashflow"))),
            row("Capital Expenditure", _fmt_money(info.get("capitalExpenditures"))),
        ]
        dividends = [
            row("Dividend Yield", _fmt_pct(info.get("dividendYield"))),
            row(
                "Dividend Rate",
                f"${_fmt_num(info.get('dividendRate'), 2)}"
                if info.get("dividendRate")
                else "—",
            ),
            row("Payout Ratio", _fmt_pct(info.get("payoutRatio"))),
            row(
                "Ex-Dividend Date",
                datetime.fromtimestamp(info["exDividendDate"]).strftime("%Y-%m-%d")
                if info.get("exDividendDate")
                else "—",
            ),
        ]
        analyst = [
            row("Recommendation", str(info.get("recommendationKey", "—")).upper()),
            row("# of Analysts", str(info.get("numberOfAnalystOpinions", "—"))),
            row("Target Mean Price", _fmt_money(info.get("targetMeanPrice"), "fixed")),
            row("Target High Price", _fmt_money(info.get("targetHighPrice"), "fixed")),
            row("Target Low Price", _fmt_money(info.get("targetLowPrice"), "fixed")),
        ]

        def section(title: str, rows: list[str]) -> str:
            return (
                f"<h3 style='color:{COLOR_TEAL}; margin-top:18px; margin-bottom:8px;'>{title}</h3>"
                f"<table style='border-collapse:collapse; width:100%; font-size:13px;'>"
                f"{''.join(rows)}</table>"
            )

        html = (
            f"<html><body style='background:{COLOR_SURFACE};color:{COLOR_TEXT};"
            f"font-family:Segoe UI,Arial,sans-serif; padding:20px;'>"
            f"<h2 style='color:{COLOR_ACCENT}; margin-top:0;'>Fundamentals — {a.ticker}</h2>"
            f"{section('Valuation', valuation)}"
            f"{section('Profitability', profitability)}"
            f"{section('Growth', growth)}"
            f"{section('Balance Sheet', balance_sheet)}"
            f"{section('Cash Flow', cash_flow)}"
            f"{section('Capital Return', dividends)}"
            f"{section('Analyst Consensus', analyst)}"
            f"</body></html>"
        )
        self.fundamentals.setHtml(html)

    def _render_technicals(self, a: StockAnalysis) -> None:
        if MATPLOTLIB_AVAILABLE:
            self._draw_chart(a)

        def signal_row(text: str, polarity: str) -> str:
            color = (
                COLOR_GREEN if polarity == "positive"
                else COLOR_RED if polarity == "negative"
                else COLOR_MUTED
            )
            marker = "▲" if polarity == "positive" else "▼" if polarity == "negative" else "■"
            return (
                f"<li style='margin:4px 0; color:{color};'>"
                f"<span style='display:inline-block;width:18px;'>{marker}</span>{text}</li>"
            )

        def horizon_block(title: str, data: dict, horizon: str) -> str:
            score = data.get("score", 50.0)
            rating = data.get("rating", "HOLD")
            rc = self._rating_color(rating)
            signals = data.get("signals", [])
            sig_html = "".join(signal_row(t, p) for t, p in signals) or (
                f"<li style='color:{COLOR_MUTED};'>No signals</li>"
            )
            bar_width = int(score)
            return (
                f"<div style='margin-bottom:18px;'>"
                f"<div style='display:flex; align-items:center; gap:12px;'>"
                f"<h3 style='color:{COLOR_TEAL}; margin:0;'>{title}</h3>"
                f"<span style='color:{COLOR_MUTED}; font-size:12px;'>({horizon})</span>"
                f"<span style='color:{rc}; font-weight:700; border:1.5px solid {rc};"
                f"padding:2px 8px; border-radius:10px; font-size:12px;'>{rating}</span>"
                f"<span style='color:{COLOR_MUTED};'>score "
                f"<b style='color:{COLOR_TEXT};'>{score:.0f}</b>/100</span>"
                f"</div>"
                f"<div style='background:{COLOR_PANEL}; height:6px; border-radius:3px;"
                f"margin-top:6px; overflow:hidden;'>"
                f"<div style='background:{rc}; width:{bar_width}%; height:100%;'></div>"
                f"</div>"
                f"<ul style='list-style:none; padding-left:0; margin-top:8px;'>{sig_html}</ul>"
                f"</div>"
            )

        html = (
            f"<html><body style='background:{COLOR_SURFACE};color:{COLOR_TEXT};"
            f"font-family:Segoe UI,Arial,sans-serif; padding:18px;'>"
            f"<h2 style='color:{COLOR_ACCENT}; margin-top:0;'>Technical Signals — {a.ticker}</h2>"
            f"{horizon_block('Swing Trade Outlook', a.swing_term, '1 week - 1 month')}"
            f"{horizon_block('Short-Term Outlook', a.short_term, '1-3 months')}"
            f"</body></html>"
        )
        self.tech_signals.setHtml(html)

    def _draw_chart(self, a: StockAnalysis) -> None:
        df = a.df.tail(252)
        self.figure.clear()
        self.figure.patch.set_facecolor(COLOR_BG)
        gs = self.figure.add_gridspec(
            4, 1, hspace=0.08, left=0.07, right=0.97, top=0.94, bottom=0.07
        )
        ax_price = self.figure.add_subplot(gs[0:2])
        ax_rsi = self.figure.add_subplot(gs[2], sharex=ax_price)
        ax_macd = self.figure.add_subplot(gs[3], sharex=ax_price)

        ax_price.plot(df.index, df["Close"], color=COLOR_ACCENT, linewidth=1.6, label="Close")
        ax_price.plot(df.index, df["SMA20"], color=COLOR_ORANGE, linewidth=1.0, label="SMA 20")
        ax_price.plot(df.index, df["SMA50"], color=COLOR_AMBER, linewidth=1.0, label="SMA 50")
        ax_price.plot(df.index, df["SMA200"], color=COLOR_PURPLE, linewidth=1.0, label="SMA 200")
        ax_price.fill_between(df.index, df["BB_Up"], df["BB_Dn"], alpha=0.10, color=COLOR_TEAL)
        ax_price.scatter([df.index[-1]], [df["Close"].iloc[-1]], color=COLOR_GREEN, zorder=5, s=30)
        ax_price.set_title(
            f"{a.ticker}  ·  {a.info.get('longName', a.ticker)}  ·  52W chart",
            color=COLOR_TEXT, fontsize=11, loc="left",
        )
        legend = ax_price.legend(loc="upper left", fontsize=8, frameon=False, ncols=4)
        for text in legend.get_texts():
            text.set_color(COLOR_TEXT)
        self._style_axes(ax_price)

        ax_rsi.plot(df.index, df["RSI"], color=COLOR_RED, linewidth=1.2)
        ax_rsi.axhline(70, color=COLOR_AMBER, linestyle="--", alpha=0.5, linewidth=0.8)
        ax_rsi.axhline(30, color=COLOR_GREEN, linestyle="--", alpha=0.5, linewidth=0.8)
        ax_rsi.axhline(50, color=COLOR_DIM, linestyle=":", alpha=0.5, linewidth=0.6)
        ax_rsi.set_ylim(0, 100)
        ax_rsi.set_ylabel("RSI", color=COLOR_TEXT, fontsize=9)
        self._style_axes(ax_rsi)

        ax_macd.plot(df.index, df["MACD"], color=COLOR_ACCENT, linewidth=1.0, label="MACD")
        ax_macd.plot(df.index, df["Signal"], color=COLOR_ORANGE, linewidth=1.0, label="Signal")
        colors = [COLOR_GREEN if h > 0 else COLOR_RED for h in df["Hist"]]
        ax_macd.bar(df.index, df["Hist"], color=colors, alpha=0.5, width=1.0)
        ax_macd.axhline(0, color=COLOR_DIM, linestyle="-", alpha=0.3, linewidth=0.6)
        ax_macd.set_ylabel("MACD", color=COLOR_TEXT, fontsize=9)
        leg = ax_macd.legend(loc="upper left", fontsize=8, frameon=False, ncols=2)
        for text in leg.get_texts():
            text.set_color(COLOR_TEXT)
        self._style_axes(ax_macd)

        ax_price.tick_params(labelbottom=False)
        ax_rsi.tick_params(labelbottom=False)

        self.canvas.draw_idle()

    @staticmethod
    def _style_axes(ax) -> None:
        ax.set_facecolor(COLOR_SURFACE)
        ax.tick_params(colors=COLOR_MUTED, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(COLOR_BORDER)
        ax.grid(True, alpha=0.15, color=COLOR_BORDER, linestyle="-", linewidth=0.5)
        ax.yaxis.label.set_color(COLOR_TEXT)
        ax.xaxis.label.set_color(COLOR_TEXT)

    def _render_recommendation(self, a: StockAnalysis) -> None:
        def signal_row(text: str, polarity: str) -> str:
            color = (
                COLOR_GREEN if polarity == "positive"
                else COLOR_RED if polarity == "negative"
                else COLOR_MUTED
            )
            marker = "▲" if polarity == "positive" else "▼" if polarity == "negative" else "■"
            return (
                f"<li style='margin:4px 0; color:{color};'>"
                f"<span style='display:inline-block;width:18px;'>{marker}</span>{text}</li>"
            )

        def horizon_block(title: str, horizon: str, data: dict) -> str:
            score = data.get("score", 50.0)
            rating = data.get("rating", "HOLD")
            rc = self._rating_color(rating)
            sigs = data.get("signals", [])
            sig_html = "".join(signal_row(t, p) for t, p in sigs) or (
                f"<li style='color:{COLOR_MUTED};'>No signals</li>"
            )
            return f"""
            <div style='background:{COLOR_BG}; border:1px solid {COLOR_BORDER};
                border-radius:8px; padding:14px 18px; margin-bottom:14px;'>
              <div style='display:flex; align-items:baseline; gap:14px;'>
                <h3 style='margin:0; color:{COLOR_TEAL};'>{title}</h3>
                <span style='color:{COLOR_MUTED}; font-size:12px;'>{horizon}</span>
                <span style='margin-left:auto; color:{rc}; font-weight:700;
                    border:1.5px solid {rc}; padding:3px 12px; border-radius:14px;'>
                    {rating}</span>
                <span style='color:{COLOR_MUTED};'>score
                    <b style='color:{COLOR_TEXT};'>{score:.0f}</b> / 100</span>
              </div>
              <div style='background:{COLOR_PANEL}; height:8px; border-radius:4px;
                  margin-top:10px; overflow:hidden;'>
                <div style='background:{rc}; width:{int(score)}%; height:100%;'></div>
              </div>
              <ul style='list-style:none; padding-left:0; margin-top:12px;'>{sig_html}</ul>
            </div>
            """

        bull, bear = a._collect_signals()
        bull_html = (
            "".join(f"<li style='color:{COLOR_GREEN};'>▲ {b}</li>" for b in bull[:8])
            or f"<li style='color:{COLOR_MUTED};'>—</li>"
        )
        bear_html = (
            "".join(f"<li style='color:{COLOR_RED};'>▼ {b}</li>" for b in bear[:8])
            or f"<li style='color:{COLOR_MUTED};'>—</li>"
        )

        info = a.info
        target = info.get("targetMeanPrice")
        last_price = a.df.iloc[-1]["Close"]
        upside = ((target / last_price - 1) * 100) if target else None
        target_block = (
            f"<div style='color:{COLOR_MUTED};'>Analyst target "
            f"<b style='color:{COLOR_TEXT};'>${target:.2f}</b> "
            f"<span style='color:{COLOR_GREEN if (upside or 0) >= 0 else COLOR_RED};'>"
            f"({upside:+.1f}%)</span></div>"
            if target
            else ""
        )
        comp_color = self._rating_color(a.composite_rating)

        html = f"""
        <html><body style='background:{COLOR_SURFACE};color:{COLOR_TEXT};
        font-family:Segoe UI,Arial,sans-serif; padding:18px;'>

        <div style='background:{COLOR_BG}; border:1.5px solid {comp_color};
            border-radius:10px; padding:16px 20px; margin-bottom:20px;'>
          <div style='display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;'>
            <h1 style='margin:0; color:{COLOR_ACCENT};'>{a.ticker}</h1>
            <div style='font-size:24px; font-weight:700;'>${last_price:.2f}</div>
            <div style='color:{comp_color}; font-weight:800; font-size:18px;
                border:2px solid {comp_color}; padding:4px 14px; border-radius:16px;'>
                {a.composite_rating}</div>
            <div style='color:{COLOR_MUTED};'>composite
                <b style='color:{COLOR_TEXT}; font-size:18px;'>{a.composite:.0f}</b> / 100</div>
            {target_block}
          </div>
          <p style='margin-top:14px; line-height:1.55;'>{a.summary}</p>
        </div>

        {horizon_block('Swing Trade Outlook', '1 week - 1 month · ultra-short technical', a.swing_term)}
        {horizon_block('Short-Term Outlook', '1-3 months · technical', a.short_term)}
        {horizon_block('Medium-Term Outlook', '3-12 months · technical + valuation + analyst', a.medium_term)}
        {horizon_block('Long-Term Outlook', '1-5 years · fundamentals + balance sheet', a.long_term)}

        <div style='display:flex; gap:14px; margin-top:14px;'>
          <div style='flex:1; background:{COLOR_BG}; border:1px solid {COLOR_BORDER};
              border-radius:8px; padding:14px 18px;'>
            <h3 style='color:{COLOR_GREEN}; margin-top:0;'>Bullish drivers</h3>
            <ul style='list-style:none; padding-left:0;'>{bull_html}</ul>
          </div>
          <div style='flex:1; background:{COLOR_BG}; border:1px solid {COLOR_BORDER};
              border-radius:8px; padding:14px 18px;'>
            <h3 style='color:{COLOR_RED}; margin-top:0;'>Bearish drivers</h3>
            <ul style='list-style:none; padding-left:0;'>{bear_html}</ul>
          </div>
        </div>

        <p style='color:{COLOR_DIM}; font-size:11px; margin-top:24px;'>
          Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ·
          Data via Yahoo Finance ·
          Educational tool — not investment advice.</p>
        </body></html>
        """
        self.report.setHtml(html)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_report(self) -> None:
        if not self.current_analysis:
            QMessageBox.information(self, "Nothing to export", "Run an analysis first.")
            return
        a = self.current_analysis
        default_name = f"{a.ticker}_analysis_{datetime.now():%Y%m%d_%H%M}.html"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Report", default_name,
            "HTML files (*.html);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("<!DOCTYPE html><html><head><meta charset='utf-8'>")
                fh.write(f"<title>{a.ticker} — Stock Analysis</title></head><body>")
                fh.write(self.overview.toHtml())
                fh.write("<hr>")
                fh.write(self.fundamentals.toHtml())
                fh.write("<hr>")
                fh.write(self.report.toHtml())
                fh.write("</body></html>")
            self.statusBar().showMessage(f"Exported to {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))


# ============================================================================
# Entry point
# ============================================================================
def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("US Stock Analyzer & Advisor")
    app.setOrganizationName("Cowork Research")
    app.setStyle("Fusion")

    win = StockAnalyzerApp()
    # Honour --maximized / -m (set by Run.bat) by starting full-screen
    if any(flag in sys.argv for flag in ("--maximized", "-m")):
        win.showMaximized()
    else:
        win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
