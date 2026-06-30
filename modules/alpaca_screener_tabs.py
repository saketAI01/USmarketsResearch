import os
import sys
import json
import csv
import traceback
import logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("alpaca_screener_tabs")

# Add PySide6 imports
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QStackedWidget, QFrame,
    QScrollArea, QComboBox, QButtonGroup, QSplitter, QMenu, QTabWidget,
    QSizePolicy, QAbstractItemView, QListWidget, QListWidgetItem, QProgressBar,
    QGroupBox, QTextEdit, QDoubleSpinBox, QSpinBox, QCheckBox, QGridLayout,
    QTextBrowser, QFileDialog, QMessageBox, QDialog, QRadioButton,
    QDialogButtonBox, QInputDialog, QMainWindow
)
from PySide6.QtCore import (
    Qt, QThreadPool, QRunnable, QObject, Signal, Slot, QTimer, QThread, QUrl
)
from PySide6.QtGui import QColor, QFont, QCursor, QPixmap, QAction

# Setup Matplotlib imports
try:
    import matplotlib
    matplotlib.use("QtAgg")
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as _FigCanvas
    from matplotlib.patches import Rectangle
    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker
    _QTAGG_OK = True
except Exception:
    _QTAGG_OK = False

# Setup yfinance
try:
    import yfinance as _yf
    _YF_OK = True
except Exception:
    _yf = None
    _YF_OK = False

# Setup QtWebEngine (optional PySide6 component)
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView as _QWebEngineView
    from PySide6.QtWebEngineCore import (
        QWebEngineSettings  as _QWebEngineSettings,
        QWebEngineProfile   as _QWebEngineProfile,
        QWebEnginePage      as _QWebEnginePage,
    )
    _WEBENGINE_OK = True
except ImportError:
    _WEBENGINE_OK = False
    _QWebEngineView = None     # type: ignore[assignment,misc]
    _QWebEnginePage = object   # harmless base for _LabConsolePage

# Add core data libraries
import pandas as pd
_PD_OK = True
import numpy as np
import requests

# Load Alpaca API keys from central configuration
try:
    from modules.stock_evaluate.config import load_api_keys
    _API_KEYS = load_api_keys()
    API_KEY = _API_KEYS.get("alpaca_key") or ""
    API_SECRET = _API_KEYS.get("alpaca_secret") or ""
except Exception:
    API_KEY = ""
    API_SECRET = ""

TRADING_URL  = "https://paper-api.alpaca.markets"
DATA_URL     = "https://data.alpaca.markets"
HDR          = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

# Theme Colors - Catppuccin Mocha inspired
C = {
    "bg":      "#0d1117",
    "card":    "#161b22",
    "hover":   "#21262d",
    "border":  "#30363d",
    "accent":  "#00d4ff",
    "green":   "#3fb950",
    "red":     "#f85149",
    "yellow":  "#ffd043",
    "purple":  "#bc8cff",
    "text":    "#ffffff",
    "sec":     "#b1bac4",
    "muted":   "#8b949e",
    "chart_bg":"#0d1117",
}

# Default StockLab.html path
_STOCKLAB_DEFAULT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "StockLab", "StockLab.html"))

# ── Reusable background workers and widgets ─────────────────────────────────

class _Signals(QObject):
    result = Signal(object)
    error  = Signal(str)
    done   = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        parent = kwargs.pop("parent", None)
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals(parent)
        self.setAutoDelete(True)

    @Slot()
    def run(self):
        log.debug(f"[WORKER] start  fn={self.fn}")
        try:
            result = self.fn(*self.args, **self.kwargs)
            log.debug(f"[WORKER] done   fn={self.fn}  result_type={type(result)}")
            self.signals.result.emit(result)
        except Exception:
            tb = traceback.format_exc()
            log.error(f"[WORKER] exception in fn={self.fn}:\n{tb}")
            self.signals.error.emit(tb)
        finally:
            self.signals.done.emit()


class Card(QFrame):
    """Rounded card container."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {C['card']};
                border: 1px solid {C['border']};
                border-radius: 10px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)


class MetricCard(QFrame):
    """Small KPI tile with title / value / subtitle."""
    def __init__(self, title: str, value: str = "—", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {C['card']};
                border: 1px solid {C['border']};
                border-radius: 8px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(f"color: {C['muted']}; font-size: 11px; font-weight: 600; text-transform: uppercase;")
        
        self.v_lbl = QLabel(value)
        self.v_lbl.setStyleSheet(f"color: {C['text']}; font-size: 20px; font-weight: bold;")
        
        self.s_lbl = QLabel(subtitle)
        self.s_lbl.setStyleSheet(f"color: {C['muted']}; font-size: 10px;")

        lay.addWidget(t_lbl)
        lay.addWidget(self.v_lbl)
        lay.addWidget(self.s_lbl)

    def setValue(self, val: str, sub: str = None):
        self.v_lbl.setText(val)
        if sub is not None:
            self.s_lbl.setText(sub)


def _colored_item(text: str, color: str, align=Qt.AlignCenter) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setForeground(QColor(color))
    it.setTextAlignment(align)
    return it


def _dry_btn(sym: str, side: str, callback) -> QPushButton:
    """Small green/red Dry Buy or Dry Sell button for watchlist/screener table cells."""
    is_buy = (side == "buy")
    bg  = "#1a7f37" if is_buy else "#b91c1c"
    hov = "#238636" if is_buy else "#dc2626"
    lbl = "Buy"     if is_buy else "Sell"
    b   = QPushButton(lbl)
    b.setStyleSheet(
        f"QPushButton{{background:{bg};color:#fff;border:none;"
        f"border-radius:3px;padding:2px 8px;font-size:10px;font-weight:700;margin:2px;}}"
        f"QPushButton:hover{{background:{hov};}}"
    )
    b.clicked.connect(lambda _checked=False, s=sym, sd=side: callback(s, sd))
    return b


_DRY_BUY_SS = (
    f"QPushButton{{background:#1a7f37;color:#fff;border:none;"
    f"border-radius:4px;padding:5px 14px;font-size:12px;font-weight:700;}}"
    f"QPushButton:hover{{background:#238636;}}"
)

# ── Load Company Name Lookup ──────────────────────────────────────────────────
COMPANY_NAMES = {}
try:
    import csv as _csv_master
    _paths_to_try = [
        Path(__file__).resolve().parent.parent / "USStockMaster.csv",
        Path(__file__).resolve().parent.parent / "SecurityMaster" / "USStockMaster.csv"
    ]
    for _p in _paths_to_try:
        if _p.exists():
            with open(_p, "r", encoding="utf-8") as _f_master:
                _reader = _csv_master.reader(_f_master)
                next(_reader, None)  # Skip header
                for _row in _reader:
                    if len(_row) >= 2:
                        COMPANY_NAMES[_row[0].strip().upper()] = _row[1].strip()
            break
except Exception as _e_master:
    log.warning(f"Could not load USStockMaster.csv company names: {_e_master}")

# ── Thin synchronous wrapper around Alpaca REST endpoints ────────────────────
class AlpacaAPI:
    @staticmethod
    def _get(base: str, path: str, params: dict = None, _retry: bool = True):
        try:
            r = requests.get(f"{base}{path}", headers=HDR, params=params, timeout=12)
            r.raise_for_status()
            return r.json(), None
        except requests.exceptions.HTTPError as e:
            if _retry and params and "feed" in params:
                p2 = {k: v for k, v in params.items() if k != "feed"}
                result, err2 = AlpacaAPI._get(base, path, p2, _retry=False)
                if result is not None:
                    return result, None
            try:
                msg = r.json().get("message", str(e))
            except Exception:
                msg = str(e)
            return None, msg
        except Exception as e:
            return None, str(e)

    def account(self):
        return self._get(TRADING_URL, "/v2/account")

    def positions(self):
        return self._get(TRADING_URL, "/v2/positions")

    def orders(self, status: str = "open", limit: int = 50):
        return self._get(TRADING_URL, "/v2/orders", {"status": status, "limit": limit})

    def cancel_order(self, order_id: str):
        try:
            r = requests.delete(f"{TRADING_URL}/v2/orders/{order_id}", headers=HDR, timeout=12)
            r.raise_for_status()
            return {"cancelled": order_id}, None
        except requests.exceptions.HTTPError as e:
            try:
                msg = r.json().get("message", str(e))
            except Exception:
                msg = str(e)
            return None, msg
        except Exception as e:
            return None, str(e)

    def replace_order(self, order_id: str, qty: str = None, limit_price: str = None, stop_price: str = None, time_in_force: str = None):
        body: dict = {}
        if qty:           body["qty"]           = qty
        if limit_price:   body["limit_price"]   = limit_price
        if stop_price:    body["stop_price"]    = stop_price
        if time_in_force: body["time_in_force"] = time_in_force
        try:
            r = requests.patch(
                f"{TRADING_URL}/v2/orders/{order_id}",
                headers={**HDR, "Content-Type": "application/json"},
                json=body, timeout=15)
            r.raise_for_status()
            return r.json(), None
        except requests.exceptions.HTTPError as e:
            try:
                msg = r.json().get("message", str(e))
            except Exception:
                msg = str(e)
            return None, msg
        except Exception as e:
            return None, str(e)

    def clock(self):
        return self._get(TRADING_URL, "/v2/clock")

    def bars(self, symbol: str, timeframe: str = "1Day", start: str = None, end: str = None, limit: int = 500):
        if not start:
            start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        params = {"timeframe": timeframe, "start": start, "limit": limit, "feed": "iex"}
        if end:
            params["end"] = end
        return self._get(DATA_URL, f"/v2/stocks/{symbol}/bars", params)

    def snapshot(self, symbols: list):
        syms = ",".join(symbols)
        return self._get(DATA_URL, "/v2/stocks/snapshots", {"symbols": syms, "feed": "iex"})

    def multi_bars(self, symbols: list, timeframe: str = "1Day", start: str = None, limit: int = 220):
        if not start:
            start = (datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d")
        params = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "start": start,
            "limit": limit,
            "feed": "iex",
        }
        return self._get(DATA_URL, "/v2/stocks/bars", params)

    def latest_trade(self, symbol: str):
        return self._get(DATA_URL, f"/v2/stocks/{symbol}/trades/latest", {"feed": "iex"})

    def news(self, symbols: list = None, limit: int = 30):
        params: dict = {"limit": limit}
        if symbols:
            params["symbols"] = ",".join(symbols)
        return self._get(DATA_URL, "/v1beta1/news", params)

    def assets(self):
        return self._get(TRADING_URL, "/v2/assets", {"status": "active", "asset_class": "us_equity"})

    def portfolio_history(self, period: str = "1M", timeframe: str = "1D"):
        return self._get(TRADING_URL, "/v2/account/portfolio/history", {"period": period, "timeframe": timeframe, "intraday_reporting": "market_hours"})

    def market_movers(self, top: int = 20):
        return self._get(DATA_URL, "/v1beta1/screener/stocks/movers", {"top": top})

    def most_active(self, top: int = 20, by: str = "volume"):
        return self._get(DATA_URL, "/v1beta1/screener/stocks/most-actives", {"top": top, "by": by})

API = AlpacaAPI()


SCREEN_SYMS = [
    "AAPL","MSFT","AMZN","GOOGL","META","TSLA","NVDA","BRK.B",
    "JPM","V","UNH","JNJ","WMT","XOM","MA","PG","HD","CVX",
    "LLY","ABBV","MRK","PEP","COST","KO","MCD","TMO","CSCO",
    "ACN","ABT","AVGO","CRM","DHR","NKE","TXN","PM","ORCL",
    "AMD","QCOM","INTC","IBM","NFLX","SPY","QQQ","DIA","IWM",
    "SHOP","PYPL","SQ","SNAP","UBER","LYFT","COIN","HOOD",
]


SIGNAL_OPTIONS = [
    "— Any —",
    "Gainers > +1%",
    "Losers < -1%",
    "Gap Up  > +2%",
    "Gap Down < -2%",
    "Breakout (50-day high)",
    "Golden Cross  MA20×MA50 ↑",
    "Death Cross  MA20×MA50 ↓",
    "RSI(14) Cross 50 ↑",
    "RSI(14) Cross 50 ↓",
    "Bollinger Squeeze",
]

# signals that require historical bars to compute
_TECH_SIGNALS = {
    "Breakout (50-day high)",
    "Golden Cross  MA20×MA50 ↑",
    "Death Cross  MA20×MA50 ↓",
    "RSI(14) Cross 50 ↑",
    "RSI(14) Cross 50 ↓",
    "Bollinger Squeeze",
}


class _MarketScreenerTab(QWidget):
    go_explore      = Signal(str)
    trade_requested = Signal(str, str)   # (symbol, side)

    def __init__(self, pool: QThreadPool, parent=None):
        super().__init__(parent)
        self.pool = pool
        self._snap_data: dict = {}
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # header
        hdr = QHBoxLayout()
        t = QLabel("Market Screener")
        t.setStyleSheet("font-size:24px; font-weight:700;")
        self._btn_scan = QPushButton("▶  Scan Market")
        self._btn_scan.setFixedWidth(140)
        self._btn_scan.clicked.connect(self._scan)
        hdr.addWidget(t)
        hdr.addStretch()
        hdr.addWidget(self._btn_scan)
        root.addLayout(hdr)

        # filter bar
        fc = Card()
        fl = QHBoxLayout()
        fc.layout().addLayout(fl)

        fl.addWidget(QLabel("Signal:"))
        self._signal = QComboBox()
        self._signal.addItems(SIGNAL_OPTIONS)
        self._signal.setMinimumWidth(210)
        fl.addWidget(self._signal)
        fl.addSpacing(18)

        fl.addWidget(QLabel("Sort by:"))
        self._sort = QComboBox()
        self._sort.addItems(["| Chg % |", "Price (High→Low)", "Volume (High→Low)"])
        fl.addWidget(self._sort)
        fl.addSpacing(18)

        fl.addWidget(QLabel("Min $:"))
        self._min_p = QLineEdit("0");    self._min_p.setFixedWidth(65)
        fl.addWidget(self._min_p)
        fl.addWidget(QLabel("Max $:"))
        self._max_p = QLineEdit("9999"); self._max_p.setFixedWidth(65)
        fl.addWidget(self._max_p)
        fl.addStretch()
        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{C['sec']}; font-size:12px;")
        fl.addWidget(self._status)
        root.addWidget(fc)

        # results table
        self._tbl = QTableWidget()
        self._tbl.setColumnCount(11)
        self._tbl.setHorizontalHeaderLabels(
            ["Symbol", "Price", "Chg %", "Chg $", "Open", "High", "Low", "Volume", "Signal",
             "Buy", "Sell"]
        )
        sh = self._tbl.horizontalHeader()
        sh.setSectionResizeMode(QHeaderView.Stretch)
        sh.setSectionResizeMode(9,  QHeaderView.Fixed)
        sh.setSectionResizeMode(10, QHeaderView.Fixed)
        self._tbl.setColumnWidth(9,  58)
        self._tbl.setColumnWidth(10, 58)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setSortingEnabled(True)
        self._tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tbl.customContextMenuRequested.connect(self._ctx)
        self._tbl.doubleClicked.connect(self._dbl)
        root.addWidget(self._tbl)

        hint = QLabel(
            "💡  Double-click or right-click a row · Green/Red buttons send to Dry Trader  ·  "
            "Technical signals fetch 220 days of bars and may take a few seconds"
        )
        hint.setStyleSheet(f"color:{C['muted']}; font-size:11px;")
        root.addWidget(hint)

    # ── scan orchestration ────────────────────────────────────────────────────

    def _scan(self):
        self._btn_scan.setEnabled(False)
        self._status.setText("Fetching snapshots…")
        batch = SCREEN_SYMS[:48]
        w = Worker(lambda: API.snapshot(batch))
        w.signals.result.connect(self._on_snapshot)
        self.pool.start(w)

    def _on_scan_done(self):
        self._btn_scan.setEnabled(True)

    def _on_snapshot(self, res):
        data, err = res
        if not data:
            self._status.setText(f"Error: {err}")
            self._on_scan_done()
            return
        self._snap_data = data
        sig = self._signal.currentText()
        if sig in _TECH_SIGNALS:
            self._status.setText("Fetching bars for technical analysis…")
            start = (datetime.now() - timedelta(days=310)).strftime("%Y-%m-%d")
            syms  = list(data.keys())
            w = Worker(lambda s=syms, st=start: API.multi_bars(s, start=st, limit=220))
            w.signals.result.connect(self._on_bars)
            w.signals.done.connect(self._on_scan_done)
            self.pool.start(w)
        else:
            self._apply_filters({})
            self._on_scan_done()

    def _on_bars(self, res):
        data, err = res
        bars_by_sym = (data or {}).get("bars", {})
        self._apply_filters(bars_by_sym)

    # ── filtering & display ───────────────────────────────────────────────────

    def _apply_filters(self, bars_by_sym: dict):
        try:
            lo = float(self._min_p.text() or 0)
            hi = float(self._max_p.text() or 99999)
        except ValueError:
            lo, hi = 0, 99999

        sig      = self._signal.currentText()
        sort_idx = self._sort.currentIndex()
        rows     = []

        for sym, snap in self._snap_data.items():
            daily  = snap.get("dailyBar",    {})
            prev   = snap.get("prevDailyBar",{})
            trade  = snap.get("latestTrade", {})
            price  = float(trade.get("p",  daily.get("c", 0)))
            prev_c = float(prev.get("c",   price))
            chg    = price - prev_c
            chgp   = (chg / prev_c * 100) if prev_c else 0
            op     = float(daily.get("o", 0))
            h      = float(daily.get("h", 0))
            l      = float(daily.get("l", 0))
            vol    = float(daily.get("v", 0))

            if not (lo <= price <= hi):
                continue

            # ── signal filter ─────────────────────────────────────────────────
            sig_label = ""
            if sig == "— Any —":
                sig_label = ""
            elif sig == "Gainers > +1%":
                if chgp < 1.0:
                    continue
                sig_label = f"+{chgp:.1f}%"
            elif sig == "Losers < -1%":
                if chgp > -1.0:
                    continue
                sig_label = f"{chgp:.1f}%"
            elif sig == "Gap Up  > +2%":
                gap = ((op - prev_c) / prev_c * 100) if prev_c else 0
                if gap < 2.0:
                    continue
                sig_label = f"Gap +{gap:.1f}%"
            elif sig == "Gap Down < -2%":
                gap = ((op - prev_c) / prev_c * 100) if prev_c else 0
                if gap > -2.0:
                    continue
                sig_label = f"Gap {gap:.1f}%"
            elif sig in _TECH_SIGNALS:
                bars = bars_by_sym.get(sym, [])
                result = self._compute_signal(sig, bars)
                if result is None:
                    continue
                sig_label = result

            rows.append((sym, price, chgp, chg, op, h, l, vol, sig_label))

        key_fn = {
            0: lambda r: abs(r[2]),
            1: lambda r: r[1],
            2: lambda r: r[7],
        }.get(sort_idx, lambda r: abs(r[2]))
        rows.sort(key=key_fn, reverse=True)

        self._tbl.setSortingEnabled(False)
        self._tbl.setRowCount(len(rows))
        for i, (sym, price, chgp, chg, op, hi2, lo2, vol, sig_lbl) in enumerate(rows):
            clr     = C["green"] if chgp >= 0 else C["red"]
            sign    = "+" if chgp >= 0 else ""
            vol_s   = f"{vol/1e6:.1f}M" if vol >= 1e6 else f"{vol/1e3:.0f}K"
            sig_clr = C["yellow"] if sig_lbl else C["sec"]
            for j, (v, c) in enumerate([
                (sym,                  C["accent"]),
                (f"${price:.2f}",      C["text"]),
                (f"{sign}{chgp:.2f}%", clr),
                (f"{sign}${chg:.2f}",  clr),
                (f"${op:.2f}",         C["text"]),
                (f"${hi2:.2f}",        C["green"]),
                (f"${lo2:.2f}",        C["red"]),
                (vol_s,                C["text"]),
                (sig_lbl or "—",       sig_clr),
            ]):
                self._tbl.setItem(i, j, _colored_item(v, c))
            self._tbl.setCellWidget(
                i, 9,  _dry_btn(sym, "buy",  lambda s, sd: self.trade_requested.emit(s, sd)))
            self._tbl.setCellWidget(
                i, 10, _dry_btn(sym, "sell", lambda s, sd: self.trade_requested.emit(s, sd)))
        self._tbl.setSortingEnabled(True)

        sort_labels = ["| Chg % |", "Price", "Volume"]
        self._status.setText(
            f"{len(rows)} stocks matched"
            + (f"  ·  {sig}" if sig != "— Any —" else "")
            + f"  ·  sorted by {sort_labels[sort_idx]}"
        )

    @staticmethod
    def _compute_signal(sig: str, bars: list):
        """Return display string if signal fires, None to exclude symbol."""
        if len(bars) < 22:
            return None
        closes = np.array([float(b["c"]) for b in bars])

        if sig == "Breakout (50-day high)":
            if len(closes) < 52:
                return None
            if closes[-1] > closes[-51:-1].max():
                return f"↑ Break ${closes[-1]:.2f}"
            return None

        elif sig == "Golden Cross  MA20×MA50 ↑":
            if len(closes) < 52:
                return None
            ma20 = np.convolve(closes, np.ones(20) / 20, "valid")
            ma50 = np.convolve(closes, np.ones(50) / 50, "valid")
            n    = min(len(ma20), len(ma50))
            a, b = ma20[-n:], ma50[-n:]
            for i in range(max(1, n - 6), n):
                if a[i] > b[i] and a[i - 1] <= b[i - 1]:
                    return f"✕ Gold ${ma20[-1]:.2f}"
            return None

        elif sig == "Death Cross  MA20×MA50 ↓":
            if len(closes) < 52:
                return None
            ma20 = np.convolve(closes, np.ones(20) / 20, "valid")
            ma50 = np.convolve(closes, np.ones(50) / 50, "valid")
            n    = min(len(ma20), len(ma50))
            a, b = ma20[-n:], ma50[-n:]
            for i in range(max(1, n - 6), n):
                if a[i] < b[i] and a[i - 1] >= b[i - 1]:
                    return f"✕ Death ${ma20[-1]:.2f}"
            return None

        elif sig in ("RSI(14) Cross 50 ↑", "RSI(14) Cross 50 ↓"):
            if len(closes) < 30:
                return None
            delta = np.diff(closes[-32:])
            gain  = np.mean(np.maximum(delta[:14], 0))
            loss  = np.mean(np.maximum(-delta[:14], 0))
            rsi_series = []
            for d in delta[14:]:
                gain = (gain * 13 + max(d,  0)) / 14
                loss = (loss * 13 + max(-d, 0)) / 14
                rs   = gain / loss if loss > 0 else 100
                rsi_series.append(100 - 100 / (1 + rs))
            if len(rsi_series) < 2:
                return None
            prev_rsi, cur_rsi = rsi_series[-2], rsi_series[-1]
            if sig == "RSI(14) Cross 50 ↑":
                return f"RSI {cur_rsi:.0f}" if prev_rsi < 50 <= cur_rsi else None
            else:
                return f"RSI {cur_rsi:.0f}" if prev_rsi > 50 >= cur_rsi else None

        elif sig == "Bollinger Squeeze":
            if len(closes) < 20:
                return None
            win   = closes[-20:]
            width = (4 * win.std()) / win.mean()   # (upper−lower) / mid, normalised
            return f"Squeeze {width * 100:.1f}%" if width < 0.08 else None

        return None

    def _ctx(self, pos):
        row = self._tbl.rowAt(pos.y())
        if row < 0:
            return
        it = self._tbl.item(row, 0)
        if not it:
            return
        sym  = it.text()
        menu = QMenu(self)
        a_ex   = menu.addAction(f"📈  Explore {sym}")
        menu.addSeparator()
        a_buy  = menu.addAction(f"🟢  (Dry) Buy {sym}")
        a_sell = menu.addAction(f"🔴  (Dry) Sell {sym}")
        act    = menu.exec_(self._tbl.viewport().mapToGlobal(pos))
        if act == a_ex:
            self.go_explore.emit(sym)
        elif act == a_buy:
            self.trade_requested.emit(sym, "buy")
        elif act == a_sell:
            self.trade_requested.emit(sym, "sell")

    def _dbl(self, idx):
        it = self._tbl.item(idx.row(), 0)
        if it:
            self.go_explore.emit(it.text())

# ══════════════════════════════════════════════════════════════════════════════
# 10b.  PATTERN SCREENER  — yfinance batch screener, US markets
# Ported from STOCKMATE / screener_widget.py + ScreenerWorker.
# Self-contained; no STOCKMATE dependency.
# ══════════════════════════════════════════════════════════════════════════════

_PS_COLS = ["Symbol", "Company Name", "Last Price", "RSI", "Patterns", "Pattern Date",
            "S/R Count", "Support", "Resistance"]


class _PSWorker(QThread):
    """Batch pattern screener over US symbols using yfinance."""
    progress  = Signal(int, str)   # pct, current symbol
    row_ready = Signal(dict)
    finished  = Signal(list)
    error     = Signal(str)

    def __init__(self, symbols: list, period: str = "1y", interval: str = "1d"):
        super().__init__()
        self.symbols  = symbols
        self.period   = period
        self.interval = interval
        self._cancel  = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            results = []
            total = len(self.symbols)
            for i, sym in enumerate(self.symbols):
                if self._cancel:
                    break
                pct = int(i / total * 100) if total else 0
                self.progress.emit(pct, sym)
                try:
                    item = self._analyse(sym)
                    if item:
                        results.append(item)
                        self.row_ready.emit(item)
                except Exception:
                    pass
            results.sort(key=lambda x: x.get("recent_pattern_date") or "", reverse=True)
            self.progress.emit(100, "Done")
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

    def _analyse(self, sym: str) -> dict:
        if _yf is None:
            return None
        import pandas as _pd2

        df = _yf.download(sym, period=self.period, interval=self.interval,
                          auto_adjust=True, progress=False)
        if df is None or df.empty or len(df) < 30:
            return None
        if isinstance(df.columns, _pd2.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        closes  = df["Close"].dropna().to_numpy(dtype=float)
        highs   = df["High"].dropna().to_numpy(dtype=float)
        lows    = df["Low"].dropna().to_numpy(dtype=float)
        volumes = df["Volume"].dropna().to_numpy(dtype=float)
        dates   = df.index
        n       = len(closes)
        if n < 30:
            return None
        last_price = float(closes[-1])

        # ── RSI(14) ──────────────────────────────────────────────────────────
        def _rsi14(c):
            delta = np.diff(c[-32:]) if len(c) >= 32 else np.diff(c)
            if len(delta) < 14:
                return None
            g = float(np.mean(np.maximum(delta[:14], 0)))
            l_ = float(np.mean(np.maximum(-delta[:14], 0)))
            for d in delta[14:]:
                g  = (g  * 13 + max(d,  0)) / 14
                l_ = (l_ * 13 + max(-d, 0)) / 14
            rs = g / l_ if l_ > 0 else 100.0
            return 100.0 - 100.0 / (1.0 + rs)

        rsi = _rsi14(closes)

        def _sma(c, p):
            return float(np.mean(c[-p:])) if len(c) >= p else None

        sma20  = _sma(closes, 20)
        sma50  = _sma(closes, 50)
        sma200 = _sma(closes, 200)

        # ── Pattern detection ─────────────────────────────────────────────────
        patterns    = []
        recent_date = str(dates[-1].date())

        # 52-week breakout
        if n >= 252 and closes[-1] > float(closes[-252:-1].max()):
            patterns.append("52W Breakout")

        # Near 52-week low (within 5%)
        if n >= 252 and closes[-1] <= float(closes[-252:].min()) * 1.05:
            patterns.append("Near 52W Low")

        # Golden / Death cross (MA20 vs MA50, within last 5 bars)
        if n >= 52 and sma20 is not None and sma50 is not None:
            for lag in range(1, 6):
                if n > lag + 50:
                    m20_cur  = float(np.mean(closes[-(20 + lag):-lag]))   if lag else sma20
                    m50_cur  = float(np.mean(closes[-(50 + lag):-lag]))   if lag else sma50
                    m20_prev = float(np.mean(closes[-(20 + lag + 1):-(lag + 1)]))
                    m50_prev = float(np.mean(closes[-(50 + lag + 1):-(lag + 1)]))
                    if m20_cur > m50_cur and m20_prev <= m50_prev:
                        patterns.append("Golden Cross")
                        break
                    if m20_cur < m50_cur and m20_prev >= m50_prev:
                        patterns.append("Death Cross")
                        break

        # RSI extremes
        if rsi is not None:
            if rsi >= 70:
                patterns.append(f"RSI Overbought ({rsi:.0f})")
            elif rsi <= 30:
                patterns.append(f"RSI Oversold ({rsi:.0f})")

        # SMA200 relationship
        if sma200 is not None:
            if last_price > sma200 * 1.08:
                patterns.append("Extended Above SMA200")
            elif last_price < sma200 * 0.92:
                patterns.append("Far Below SMA200")
            elif last_price > sma200:
                patterns.append("Above SMA200")

        # Bollinger Squeeze
        if n >= 20:
            win = closes[-20:]
            bw  = 4.0 * float(np.std(win)) / float(np.mean(win)) if np.mean(win) != 0 else 0
            if bw < 0.08:
                patterns.append(f"BB Squeeze ({bw * 100:.1f}%)")

        # Volume climax (today > 3× 50-day avg)
        if len(volumes) >= 50 and volumes[-1] > 3 * float(np.mean(volumes[-51:-1])):
            patterns.append("Volume Climax")

        # ── Support / Resistance (local extremes, last 60 bars) ──────────────
        supports, resistances = [], []
        lb    = min(60, n)
        h_arr = highs[-lb:]
        l_arr = lows[-lb:]
        for j in range(2, lb - 2):
            if (l_arr[j] < l_arr[j-1] and l_arr[j] < l_arr[j-2] and
                    l_arr[j] < l_arr[j+1] and l_arr[j] < l_arr[j+2]):
                supports.append(float(l_arr[j]))
            if (h_arr[j] > h_arr[j-1] and h_arr[j] > h_arr[j-2] and
                    h_arr[j] > h_arr[j+1] and h_arr[j] > h_arr[j+2]):
                resistances.append(float(h_arr[j]))

        below   = [s for s in supports    if s < last_price]
        above   = [r for r in resistances if r > last_price]
        lat_sup = max(below) if below else None
        lat_res = min(above) if above else None

        return {
            "symbol":             sym,
            "last_price":         round(last_price, 2),
            "rsi":                round(rsi, 2) if rsi is not None else None,
            "patterns":           patterns,
            "recent_pattern_date": recent_date if patterns else str(dates[-1].date()),
            "sr_count":           len(supports) + len(resistances),
            "latest_support":     round(lat_sup, 2) if lat_sup is not None else None,
            "latest_resistance":  round(lat_res, 2) if lat_res is not None else None,
        }


def _ps_item(text, align=Qt.AlignCenter):
    it = QTableWidgetItem(str(text) if text is not None else "")
    it.setTextAlignment(align | Qt.AlignVCenter)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    return it


class PatternScreenerPanel(QWidget):
    """
    US Pattern Screener — ported from STOCKMATE ScreenerWidget.
    Uses yfinance; no STOCKMATE dependency.
    """
    go_explore = Signal(str)
    send_to_wl = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._symbols: list = list(SCREEN_SYMS)
        self._worker  = None
        self._results = []
        self._build()

    def set_symbols(self, symbols: list):
        self._symbols = list(symbols) if symbols else list(SCREEN_SYMS)

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        hdr = QLabel("🔍  Pattern Screener")
        hdr.setStyleSheet(f"color:{C['text']}; font-size:18px; font-weight:700;")

        # Toolbar
        tbar = QHBoxLayout()

        def _mk_btn(txt, bg=C["card"]):
            b = QPushButton(txt)
            b.setStyleSheet(
                f"background:{bg}; color:{C['text']}; border:none;"
                f" border-radius:4px; padding:5px 12px;"
            )
            return b

        lbl_p = QLabel("Period:")
        lbl_p.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        self._period_cb = QComboBox()
        self._period_cb.addItems(["1y", "6mo", "3mo", "1mo"])
        self._period_cb.setStyleSheet(
            f"background:{C['card']}; color:{C['text']};"
            f" border:1px solid {C['border']}; border-radius:4px; padding:4px 8px;"
        )

        lbl_i = QLabel("Interval:")
        lbl_i.setStyleSheet(lbl_p.styleSheet())
        self._interval_cb = QComboBox()
        self._interval_cb.addItems(["1d", "1wk"])
        self._interval_cb.setStyleSheet(self._period_cb.styleSheet())

        self._btn_wl     = _mk_btn("📋  Screen Watchlist", "#238636")
        self._btn_sp500  = _mk_btn("⚡  Screen S&P 500",    "#1f6feb")
        self._btn_cancel = _mk_btn("⏹  Cancel",             "#b91c1c")
        self._btn_export = _mk_btn("📤  Export CSV")
        self._btn_cancel.setEnabled(False)

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Filter results…")
        self._search.setStyleSheet(
            f"background:{C['card']}; color:{C['text']};"
            f" border:1px solid {C['border']}; border-radius:4px; padding:5px 10px;"
        )
        self._search.textChanged.connect(self._filter)

        self._btn_wl.clicked.connect(self._screen_watchlist)
        self._btn_sp500.clicked.connect(self._screen_sp500)
        self._btn_cancel.clicked.connect(self._cancel)
        self._btn_export.clicked.connect(self._export)

        for w in (lbl_p, self._period_cb, lbl_i, self._interval_cb,
                  self._btn_wl, self._btn_sp500, self._btn_cancel,
                  self._btn_export, self._search):
            tbar.addWidget(w)
        tbar.addStretch()

        # Progress row
        prog_row = QHBoxLayout()
        self._pbar = QProgressBar()
        self._pbar.setRange(0, 100)
        self._pbar.setValue(0)
        self._pbar.setStyleSheet(
            f"QProgressBar{{border:none; background:{C['card']};"
            f" border-radius:4px; height:10px;}}"
            f"QProgressBar::chunk{{background:{C['accent']}; border-radius:4px;}}"
        )
        self._prog_lbl = QLabel("Ready.")
        self._prog_lbl.setStyleSheet(f"color:{C['muted']}; font-size:11px;")
        prog_row.addWidget(self._pbar)
        prog_row.addWidget(self._prog_lbl)

        # Table
        self._tbl = QTableWidget(0, len(_PS_COLS))
        self._tbl.setHorizontalHeaderLabels(_PS_COLS)
        self._tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._tbl.horizontalHeader().setStretchLastSection(True)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setSortingEnabled(True)
        self._tbl.doubleClicked.connect(self._open_sym)
        self._tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tbl.customContextMenuRequested.connect(self._show_context_menu)
        self._tbl.setStyleSheet(f"""
            QTableWidget{{background:{C['bg']};color:{C['text']};
                gridline-color:{C['card']};border:none;
                alternate-background-color:{C['card']};}}
            QTableWidget::item{{padding:4px 6px;}}
            QTableWidget::item:selected{{background:{C['accent']};}}
            QHeaderView::section{{background:{C['card']};color:{C['muted']};
                border:none;padding:6px;border-bottom:1px solid {C['border']};}}
        """)

        self._result_lbl = QLabel(
            "💡  Double-click a row to open in Stock Explorer  ·  "
            "Patterns detected: Golden/Death Cross, 52W Breakout, RSI extremes, "
            "BB Squeeze, Volume Climax, S/R levels"
        )
        self._result_lbl.setStyleSheet(f"color:{C['muted']}; font-size:11px;")

        root.addWidget(hdr)
        root.addLayout(tbar)
        root.addLayout(prog_row)
        root.addWidget(self._tbl, 1)
        root.addWidget(self._result_lbl)

    # ── Scan orchestration ────────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self._btn_wl.setEnabled(not running)
        self._btn_sp500.setEnabled(not running)
        self._btn_cancel.setEnabled(running)

    def _start_screen(self, symbols: list):
        if not _YF_OK:
            self._prog_lbl.setText("⚠  yfinance not installed (pip install yfinance).")
            return
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()
        self._results = []
        self._tbl.setRowCount(0)
        self._pbar.setValue(0)
        self._set_running(True)
        self._worker = _PSWorker(
            symbols,
            self._period_cb.currentText(),
            self._interval_cb.currentText(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.row_ready.connect(self._on_row)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _screen_watchlist(self):
        syms = self._symbols or list(SCREEN_SYMS)
        self._prog_lbl.setText(f"Screening {len(syms)} watchlist symbols…")
        self._start_screen(syms)

    def _screen_sp500(self):
        self._prog_lbl.setText(f"Screening {len(SCREEN_SYMS)} S&P 500 symbols…")
        self._start_screen(list(SCREEN_SYMS))

    def _cancel(self):
        if self._worker:
            self._worker.cancel()
        self._set_running(False)
        self._prog_lbl.setText("Cancelled.")

    # ── Worker callbacks ──────────────────────────────────────────────────────

    def _on_progress(self, pct: int, sym: str):
        self._pbar.setValue(pct)
        self._prog_lbl.setText(f"Screening {sym}… ({pct}%)")

    def _on_row(self, item: dict):
        self._results.append(item)
        self._add_row(item)

    def _add_row(self, item: dict):
        self._tbl.setSortingEnabled(False)
        r   = self._tbl.rowCount()
        self._tbl.insertRow(r)
        rsi = item.get("rsi")
        sup = item.get("latest_support")
        res = item.get("latest_resistance")

        self._tbl.setItem(r, 0, _ps_item(item["symbol"]))
        self._tbl.setItem(r, 1, _ps_item(COMPANY_NAMES.get(item["symbol"].upper(), "—"), Qt.AlignLeft))
        self._tbl.setItem(r, 2, _ps_item(
            f"${item['last_price']:.2f}" if item.get("last_price") else ""))
        rsi_it = _ps_item(f"{rsi:.1f}" if rsi is not None else "")
        if rsi is not None:
            rsi_it.setForeground(QColor(
                C["red"] if rsi > 70 else C["green"] if rsi < 30 else C["text"]))
        self._tbl.setItem(r, 3, rsi_it)
        self._tbl.setItem(r, 4,
            _ps_item(", ".join(item.get("patterns", [])[:3]), Qt.AlignLeft))
        self._tbl.setItem(r, 5, _ps_item(item.get("recent_pattern_date", "")))
        self._tbl.setItem(r, 6, _ps_item(str(item.get("sr_count", 0))))
        self._tbl.setItem(r, 7, _ps_item(f"${sup:.2f}" if sup else ""))
        self._tbl.setItem(r, 8, _ps_item(f"${res:.2f}" if res else ""))
        self._tbl.setSortingEnabled(True)

    def _filter(self):
        txt = self._search.text().lower()
        for r in range(self._tbl.rowCount()):
            sym_it = self._tbl.item(r, 0)
            pat_it = self._tbl.item(r, 3)
            show   = (not txt
                      or (sym_it and txt in sym_it.text().lower())
                      or (pat_it and txt in pat_it.text().lower()))
            self._tbl.setRowHidden(r, not show)

    def _on_finished(self, results: list):
        self._pbar.setValue(100)
        self._prog_lbl.setText(f"✓  {len(results)} symbols with patterns found.")
        self._set_running(False)
        self._result_lbl.setText(
            f"Found {len(results)} symbols with patterns. "
            "Double-click to open in Stock Explorer."
        )

    def _on_error(self, msg: str):
        self._prog_lbl.setText(f"⚠  Error: {msg[:120]}")
        self._set_running(False)

    def _open_sym(self):
        row = self._tbl.currentRow()
        if row < 0:
            return
        it = self._tbl.item(row, 0)
        if it:
            self.go_explore.emit(it.text().strip().upper())

    def _export(self):
        if not self._results:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", "PatternScreener.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            import csv as _csv2
            keys = ["symbol", "last_price", "rsi", "patterns",
                    "recent_pattern_date", "sr_count",
                    "latest_support", "latest_resistance"]
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = _csv2.DictWriter(f, fieldnames=keys, extrasaction="ignore")
                w.writeheader()
                for row in self._results:
                    row2 = dict(row)
                    row2["patterns"] = "; ".join(row2.get("patterns", []))
                    w.writerow(row2)
            QMessageBox.information(
                self, "Export", f"Exported {len(self._results)} rows to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        save_all_act = QAction("Save All Tickers to Watchlist", self)
        save_sel_act = QAction("Save Selected Ticker to Watchlist", self)
        save_all_act.triggered.connect(self._save_all_to_wl)
        save_sel_act.triggered.connect(self._save_selected_to_wl)
        menu.addAction(save_all_act)
        menu.addAction(save_sel_act)
        menu.exec(QCursor.pos())

    def _save_all_to_wl(self):
        symbols = []
        for r in range(self._tbl.rowCount()):
            if not self._tbl.isRowHidden(r):
                it = self._tbl.item(r, 0)
                if it and it.text():
                    symbols.append(it.text().strip().upper())
        if symbols:
            self.send_to_wl.emit(symbols)

    def _save_selected_to_wl(self):
        row = self._tbl.currentRow()
        if row >= 0:
            it = self._tbl.item(row, 0)
            if it and it.text():
                self.send_to_wl.emit([it.text().strip().upper()])


# ══════════════════════════════════════════════════════════════════════════════
# 10c.  RATIO SCANNER  — Multi-Timeframe Relative Performance vs SPY (US)
# Ported from STOCKMATE / ratio_scanner_widget.py + core/ratio_scanner.py.
# Benchmark changed from ^NSEI (NIFTY50) → SPY.  No .NS suffix normalization.
# ══════════════════════════════════════════════════════════════════════════════

_RS_BENCHMARK  = "SPY"
_RS_LOOKBACKS  = {"1M": 21,   "6M": 126,  "1Y": 252,  "3Y": 756,   "5Y": 1260}
_RS_TF_WEIGHTS = {"1M": 10,   "6M": 15,   "1Y": 20,   "3Y": 25,    "5Y": 30}
_RS_TF_NORM    = {"1M": 0.10, "6M": 0.15, "1Y": 0.20, "3Y": 0.25,  "5Y": 0.30}

_RS_TABLE_COLS = [
    ("Rank",                      "Rank",     60),
    ("Symbol",                    "Symbol",   90),
    ("Company_Name",              "Company Name", 140),
    ("Weighted_Score_Normalized", "W.Score",  75),
    ("Binary_Score_Normalized",   "B.Score",  65),
    ("Positive_TF_Count",         "+TFs",     50),
    ("RelPerf_1M",                "1M %",     68),
    ("RelPerf_6M",                "6M %",     68),
    ("RelPerf_1Y",                "1Y %",     68),
    ("RelPerf_3Y",                "3Y %",     68),
    ("RelPerf_5Y",                "5Y %",     68),
    ("Quartile",                  "Quartile", 80),
    ("Outperforming_All_5",       "All5",     44),
    ("LongTerm_Leader",           "LT",       38),
    ("NearTerm_Leader",           "NT",       38),
    ("History_Available_Years",   "Hist.Y",   58),
]


def _rs_extract_close(df) -> "object":
    """Pull sorted close-price Series from a yfinance DataFrame."""
    import pandas as _pd2
    if df is None or df.empty:
        return None
    if isinstance(df.columns, _pd2.MultiIndex):
        for candidate in ("adj close", "adjclose", "close"):
            for i, lbl in enumerate(
                    [str(c).lower() for c in df.columns.get_level_values(0)]):
                if lbl == candidate:
                    s = df.iloc[:, i].squeeze()
                    if isinstance(s, _pd2.DataFrame):
                        s = s.iloc[:, 0]
                    s = s.dropna()
                    return s.sort_index() if not s.empty else None
        return None
    col_map = {c.lower(): c for c in df.columns}
    for candidate in ("adj close", "adjclose", "close"):
        if candidate in col_map:
            s = df[col_map[candidate]].dropna()
            return s.sort_index() if not s.empty else None
    return None


def _rs_compute_return(series, n_days) -> "float | None":
    if series is None or len(series) < 2:
        return None
    s = series.sort_index().dropna()
    if len(s) < n_days:
        return None
    past_p = float(s.iloc[max(0, len(s) - 1 - n_days)])
    last_p = float(s.iloc[-1])
    if past_p == 0:
        return None
    import math
    if math.isnan(past_p) or math.isnan(last_p):
        return None
    return ((last_p / past_p) - 1.0) * 100.0


def _rs_score(rel_perfs: dict) -> dict:
    result     = {}
    bin_raw = wt_raw = 0.0
    valid_bin_max = valid_wt_max = 0
    for tf in _RS_LOOKBACKS:
        rp = rel_perfs.get(tf)
        if rp is None:
            b_pt = w_pt = float("nan")
        elif rp > 0:
            b_pt, w_pt = 1, _RS_TF_WEIGHTS[tf]
            bin_raw        += 1
            wt_raw         += _RS_TF_WEIGHTS[tf]
            valid_bin_max  += 1
            valid_wt_max   += _RS_TF_WEIGHTS[tf]
        else:
            b_pt, w_pt     = 0, 0
            valid_bin_max  += 1
            valid_wt_max   += _RS_TF_WEIGHTS[tf]
        result[f"Point_{tf}_Binary"]   = b_pt
        result[f"Point_{tf}_Weighted"] = w_pt
    total_wt = sum(_RS_TF_WEIGHTS.values())
    result["Binary_Score_Normalized"]   = (
        round((bin_raw / valid_bin_max) * len(_RS_LOOKBACKS), 4)
        if valid_bin_max > 0 else 0.0
    )
    result["Weighted_Score_Normalized"] = (
        round((wt_raw / valid_wt_max) * total_wt, 4)
        if valid_wt_max > 0 else 0.0
    )
    result["Positive_TF_Count"] = int(bin_raw)
    return result


def _rs_classify(rows: list) -> list:
    import math
    total = len(rows)
    if total == 0:
        return rows
    rows.sort(key=lambda r: r.get("Weighted_Score_Normalized", 0.0), reverse=True)
    for i, row in enumerate(rows):
        row["Rank"] = i + 1
        pct = (i + 1) / total
        row["Quartile"] = (
            "Top 10%"    if pct <= 0.10 else
            "Top 25%"    if pct <= 0.25 else
            "Middle"     if pct <= 0.75 else
            "Bottom 25%"
        )
        tfs = list(_RS_LOOKBACKS.keys())

        def _rp_ok(r_, tf_):
            v = r_.get(f"RelPerf_{tf_}")
            return isinstance(v, float) and not math.isnan(v) and v > 0

        row["Outperforming_All_5"] = all(_rp_ok(row, t) for t in tfs)
        row["LongTerm_Leader"]  = _rp_ok(row, "3Y") and _rp_ok(row, "5Y")
        row["NearTerm_Leader"]  = _rp_ok(row, "1M") and _rp_ok(row, "6M")
        hist_days = row.pop("_history_days", 0)
        row["History_Available_Years"] = round(hist_days / 365.25, 2)
    return rows


class _RSWorker(QThread):
    """
    Multi-Timeframe Relative Performance scanner vs SPY.
    Inline port of STOCKMATE RatioScannerWorker + core/ratio_scanner.py.
    US-only: no .NS suffix, SPY benchmark.
    """
    progress  = Signal(int, int, str)
    row_ready = Signal(dict)
    finished  = Signal(object)        # list[dict] final ranked rows
    error     = Signal(str)

    def __init__(self, symbols: list, parent=None):
        super().__init__(parent)
        self.symbols = [s.strip().upper() for s in symbols if s.strip()]

    def run(self):
        try:
            import pandas as _pd2
            total = len(self.symbols)
            STEPS = total + 4

            # ── 1. Benchmark ──────────────────────────────────────────────────
            self.progress.emit(0, STEPS, f"Downloading {_RS_BENCHMARK} benchmark…")
            bm_raw = _yf.download(
                _RS_BENCHMARK, period="7y", auto_adjust=True, progress=False)
            bm_series = _rs_extract_close(bm_raw)
            if bm_series is None or bm_series.empty:
                self.error.emit(
                    f"Failed to download benchmark {_RS_BENCHMARK}")
                return
            bench_returns = {
                tf: _rs_compute_return(bm_series, n)
                for tf, n in _RS_LOOKBACKS.items()
            }
            self.progress.emit(1, STEPS, f"Benchmark downloaded ({len(bm_series)} days).")

            # ── 2. Stock data ─────────────────────────────────────────────────
            self.progress.emit(2, STEPS,
                               f"Downloading {total} stock prices (batch)…")
            price_data: dict = {}
            failed: list     = []
            batch_size = 50
            batches    = [self.symbols[i:i + batch_size]
                          for i in range(0, total, batch_size)]
            for batch in batches:
                try:
                    raw = _yf.download(
                        batch, period="7y", auto_adjust=True,
                        progress=False, group_by="ticker")
                    if raw.empty:
                        failed.extend(batch)
                        continue
                    for sym in batch:
                        try:
                            if isinstance(raw.columns, _pd2.MultiIndex):
                                try:
                                    sub = raw.xs(sym, axis=1, level=0,
                                                 drop_level=True)
                                except KeyError:
                                    sub = raw
                            else:
                                sub = raw
                            s = _rs_extract_close(sub)
                            if s is not None and not s.empty:
                                price_data[sym] = s
                            else:
                                failed.append(sym)
                        except Exception:
                            failed.append(sym)
                except Exception:
                    failed.extend(batch)

            # Retry failed individually
            for sym in list(failed):
                try:
                    raw = _yf.download(sym, period="7y",
                                       auto_adjust=True, progress=False)
                    s = _rs_extract_close(raw)
                    if s is not None and not s.empty:
                        price_data[sym] = s
                        failed.remove(sym)
                except Exception:
                    pass

            self.progress.emit(
                3, STEPS,
                f"Downloaded {len(price_data)} stocks ({len(failed)} failed). Scoring…"
            )

            # ── 3. Score each stock ───────────────────────────────────────────
            rows: list = []
            for step_i, (sym, series) in enumerate(price_data.items()):
                self.progress.emit(
                    4 + step_i, STEPS,
                    f"Scoring {sym} ({step_i + 1}/{len(price_data)})…"
                )
                row = {
                    "Symbol": sym,
                    "Company_Name": COMPANY_NAMES.get(sym.upper(), "—")
                }
                hist_days = (
                    (series.index[-1] - series.index[0]).days
                    if len(series) > 1 else 0
                )
                row["_history_days"] = hist_days

                rel_perfs   = {}
                valid_count = 0
                for tf, n in _RS_LOOKBACKS.items():
                    stock_ret = _rs_compute_return(series, n)
                    bench_ret = bench_returns.get(tf)
                    rel_perf  = (
                        stock_ret - bench_ret
                        if stock_ret is not None and bench_ret is not None
                        else None
                    )
                    row[f"RelPerf_{tf}"] = rel_perf
                    rel_perfs[tf]        = rel_perf
                    if rel_perf is not None:
                        valid_count += 1

                if valid_count == 0:
                    continue
                scores = _rs_score(rel_perfs)
                row.update(scores)
                rows.append(row)
                self.row_ready.emit(dict(row))

            if not rows:
                self.error.emit(
                    "No stocks could be processed (all downloads failed).")
                return

            # ── 4. Rank + classify ────────────────────────────────────────────
            self.progress.emit(STEPS - 1, STEPS, "Ranking…")
            rows = _rs_classify(rows)
            self.progress.emit(STEPS, STEPS, "Done.")
            self.finished.emit(rows)

        except Exception:
            self.error.emit(traceback.format_exc())


def _rs_item(text, align=Qt.AlignRight):
    it = QTableWidgetItem(str(text) if text is not None else "—")
    it.setTextAlignment(align | Qt.AlignVCenter)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    return it


def _rs_rel_fg(v) -> str:
    import math
    try:
        f = float(v)
        if math.isnan(f):
            return C["muted"]
        return C["green"] if f > 0 else C["red"]
    except (TypeError, ValueError):
        return C["muted"]


def _rs_quartile_fg(q: str) -> str:
    return {
        "Top 10%":    "#58a6ff",
        "Top 25%":    C["green"],
        "Middle":     "#d4b72a",
        "Bottom 25%": C["red"],
    }.get(q, C["muted"])


class RatioScannerPanel(QWidget):
    """
    US Multi-Timeframe Relative Performance scanner vs SPY.
    Ported from STOCKMATE RatioScannerWidget.
    'Save Watchlist' replaced by 'Export CSV'.
    """
    send_to_wl = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._symbols: list      = list(SCREEN_SYMS)
        self._worker             = None
        self._result_rows: list  = []
        self._build()

    def set_symbols(self, symbols: list):
        self._symbols = list(symbols) if symbols else list(SCREEN_SYMS)
        self._sym_lbl.setText(f"  {len(self._symbols)} symbols")
        self._run_btn.setEnabled(len(self._symbols) > 0)

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Control bar
        bar = QFrame()
        bar.setFixedHeight(50)
        bar.setStyleSheet(
            f"QFrame{{background:{C['card']};"
            f" border-bottom:1px solid {C['border']};}}"
        )
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 12, 0)
        bl.setSpacing(8)

        def _cbtn(txt, bg=C["card"], fg=C["text"], hov=C["border"]):
            b = QPushButton(txt)
            b.setStyleSheet(
                f"QPushButton{{background:{bg};color:{fg};border:none;"
                f"border-radius:4px;padding:5px 12px;"
                f"font-size:12px;font-weight:600;}}"
                f"QPushButton:hover{{background:{hov};}}"
                f"QPushButton:disabled{{opacity:0.35;}}"
            )
            return b

        sec_lbl = QLabel(f"📉  RatioScanner  (vs {_RS_BENCHMARK})")
        sec_lbl.setStyleSheet(
            f"color:{C['accent']};font-size:14px;font-weight:800;background:transparent;"
        )

        def _sep():
            s = QFrame()
            s.setFrameShape(QFrame.VLine)
            s.setFixedSize(1, 24)
            s.setStyleSheet(f"background:{C['border']};border:none;")
            return s

        self._sym_lbl = QLabel(f"  {len(self._symbols)} symbols")
        self._sym_lbl.setStyleSheet(
            f"color:{C['muted']};font-size:11px;background:transparent;")

        self._run_btn  = _cbtn("▶  Run Scanner", "#1f6feb", "#ffffff", "#2d7de0")
        self._stop_btn = _cbtn("■  Stop",        "#b91c1c", "#ffffff", "#dc2626")
        self._stop_btn.setEnabled(False)

        self._btn_wl = _cbtn("📋  Screen Watchlist", C["card"], C["yellow"], C["hover"])
        self._btn_sp500 = _cbtn("⚡  Scan S&P 500")

        rank_lbl  = QLabel("Export ranks")
        rank_lbl.setStyleSheet(
            f"color:{C['muted']};font-size:12px;background:transparent;")
        from_lbl  = QLabel("from")
        from_lbl.setStyleSheet(
            f"color:{C['muted']};font-size:11px;background:transparent;")
        self._rank_from = QSpinBox()
        self._rank_from.setRange(1, 9999)
        self._rank_from.setValue(1)
        self._rank_from.setFixedWidth(55)
        _spin_ss = (
            f"QSpinBox{{background:{C['card']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;"
            f"padding:3px 5px;font-size:12px;}}"
        )
        self._rank_from.setStyleSheet(_spin_ss)
        to_lbl    = QLabel("to")
        to_lbl.setStyleSheet(from_lbl.styleSheet())
        self._rank_to = QSpinBox()
        self._rank_to.setRange(1, 9999)
        self._rank_to.setValue(20)
        self._rank_to.setFixedWidth(55)
        self._rank_to.setStyleSheet(_spin_ss)

        self._export_btn = _cbtn("📤  Export CSV", "#238636", "#ffffff", "#2ea043")
        self._export_btn.setEnabled(False)

        for w in (sec_lbl, _sep(), self._sym_lbl,
                  self._run_btn, self._stop_btn,
                  _sep(), self._btn_wl, self._btn_sp500,
                  _sep(), rank_lbl, from_lbl,
                  self._rank_from, to_lbl, self._rank_to,
                  self._export_btn):
            bl.addWidget(w)
        bl.addStretch()

        # Progress bar (4px)
        self._prog_bar = QProgressBar()
        self._prog_bar.setFixedHeight(4)
        self._prog_bar.setTextVisible(False)
        self._prog_bar.setRange(0, 100)
        self._prog_bar.setValue(0)
        self._prog_bar.setStyleSheet(
            f"QProgressBar{{background:{C['card']};border:none;}}"
            f"QProgressBar::chunk{{background:{C['accent']};}}"
        )
        self._prog_bar.setVisible(False)

        # Results table
        self._tbl = QTableWidget(0, len(_RS_TABLE_COLS))
        self._tbl.setHorizontalHeaderLabels([c[1] for c in _RS_TABLE_COLS])
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tbl.customContextMenuRequested.connect(self._show_context_menu)
        self._tbl.verticalHeader().setDefaultSectionSize(28)
        self._tbl.setStyleSheet(f"""
            QTableWidget{{background:{C['bg']};color:{C['text']};
                gridline-color:{C['card']};border:none;
                alternate-background-color:{C['card']};}}
            QTableWidget::item{{padding:2px 5px;}}
            QTableWidget::item:selected{{background:{C['accent']};color:#fff;}}
            QHeaderView::section{{background:{C['card']};color:{C['muted']};
                border:none;padding:5px;
                border-bottom:1px solid {C['border']};font-size:11px;}}
        """)
        hdr = self._tbl.horizontalHeader()
        for i, (_, _, w) in enumerate(_RS_TABLE_COLS):
            hdr.resizeSection(i, w)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setStretchLastSection(False)

        # Log panel
        self._log_box = QTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(80)
        self._log_box.setVisible(False)
        self._log_box.setStyleSheet(
            f"QTextEdit{{background:{C['bg']};color:{C['muted']};border:none;"
            f"border-top:1px solid {C['card']};"
            f"font-family:Consolas,'Courier New',monospace;"
            f"font-size:10px;padding:4px 8px;}}"
        )

        # Status strip
        self._status_lbl = QLabel(
            "Select symbols and click ▶ Run Scanner.")
        self._status_lbl.setFixedHeight(22)
        self._status_lbl.setStyleSheet(
            f"color:{C['muted']};font-size:11px;padding:0 12px;"
            f"background:{C['card']};border-top:1px solid {C['border']};"
        )

        root.addWidget(bar)
        root.addWidget(self._prog_bar)
        root.addWidget(self._tbl, 1)
        root.addWidget(self._log_box)
        root.addWidget(self._status_lbl)

        self._run_btn.clicked.connect(self._run_watchlist)
        self._stop_btn.clicked.connect(self._stop_scan)
        self._btn_wl.clicked.connect(self._run_watchlist)
        self._btn_sp500.clicked.connect(self._run_sp500)
        self._export_btn.clicked.connect(self._export_csv)

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _run_watchlist(self):
        self._start_scan(self._symbols)

    def _run_sp500(self):
        self._start_scan(list(SCREEN_SYMS))

    def _start_scan(self, symbols: list):
        if not _YF_OK:
            self._status_lbl.setText(
                "⚠  yfinance not installed (pip install yfinance).")
            return
        if self._worker and self._worker.isRunning():
            return
        self._tbl.setRowCount(0)
        self._result_rows = []
        self._export_btn.setEnabled(False)
        self._log_box.clear()
        self._log_box.setVisible(True)
        self._prog_bar.setVisible(True)
        self._prog_bar.setValue(0)
        self._run_btn.setEnabled(False)
        self._btn_wl.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._log(f"Starting scan on {len(symbols)} symbols vs {_RS_BENCHMARK}…")
        self._worker = _RSWorker(symbols)
        self._worker.progress.connect(self._on_progress)
        self._worker.row_ready.connect(self._on_row_ready)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _stop_scan(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(2000)
        self._scan_ended()
        self._log("Scan stopped by user.")
        self._status_lbl.setText("Scan stopped.")

    # ── Worker callbacks ──────────────────────────────────────────────────────

    def _on_progress(self, cur: int, total: int, msg: str):
        pct = int(cur / total * 100) if total > 0 else 0
        self._prog_bar.setValue(pct)
        self._log(msg)
        self._status_lbl.setText(f"[{cur}/{total}]  {msg}")

    def _on_row_ready(self, row: dict):
        self._result_rows.append(row)
        self._insert_row(row)

    def _on_finished(self, rows):
        self._result_rows = rows
        self._scan_ended()
        # Rebuild sorted table
        self._tbl.setRowCount(0)
        for row in rows:
            self._insert_row(row)
        n   = len(rows)
        top = rows[0]["Symbol"] if rows else "—"
        self._status_lbl.setText(
            f"Scan complete — {n} stocks ranked.  #1: {top}"
            f"  ·  Use 📤 Export CSV to save."
        )
        self._export_btn.setEnabled(n > 0)
        self._rank_to.setMaximum(n)
        self._log(f"Done. {n} symbols ranked.")
        self._prog_bar.setValue(100)

    def _on_error(self, msg: str):
        self._scan_ended()
        self._log(f"ERROR:\n{msg}")
        self._status_lbl.setText("⚠  Scan failed — see log panel.")
        QMessageBox.critical(
            self, "RatioScanner Error",
            "The scan encountered an error.\nSee the log panel for details."
        )

    def _scan_ended(self):
        self._worker = None
        self._stop_btn.setEnabled(False)
        self._run_btn.setEnabled(True)
        self._btn_wl.setEnabled(True)
        self._prog_bar.setVisible(False)

    # ── Table row ─────────────────────────────────────────────────────────────

    def _insert_row(self, row: dict):
        import math
        r = self._tbl.rowCount()
        self._tbl.insertRow(r)
        for col_i, (key, _, _) in enumerate(_RS_TABLE_COLS):
            raw = row.get(key, "")
            if key == "Rank":
                txt = str(int(raw)) if raw != "" and raw == raw else "—"
            elif key in ("Symbol", "Company_Name", "Quartile"):
                txt = str(raw) if raw else "—"
            elif key in ("Outperforming_All_5", "LongTerm_Leader", "NearTerm_Leader"):
                txt = "✓" if raw is True or raw == 1 else "—"
            elif key == "Positive_TF_Count":
                txt = str(int(raw)) if raw != "" and str(raw) != "nan" else "—"
            elif key in ("Weighted_Score_Normalized", "Binary_Score_Normalized"):
                try:   txt = f"{float(raw):.1f}"
                except: txt = "—"
            elif key == "History_Available_Years":
                try:   txt = f"{float(raw):.1f}y"
                except: txt = "—"
            elif key.startswith("RelPerf_"):
                try:
                    fv = float(raw)
                    txt = f"{fv:+.2f}%" if not math.isnan(fv) else "—"
                except: txt = "—"
            else:
                txt = str(raw) if raw != "" else "—"

            align = (Qt.AlignLeft if key in ("Symbol", "Company_Name", "Quartile")
                     else Qt.AlignRight)
            it    = _rs_item(txt, align)

            if key.startswith("RelPerf_"):
                try:
                    fv = float(raw)
                    if not math.isnan(fv):
                        it.setForeground(QColor(_rs_rel_fg(fv)))
                except: pass
            elif key == "Quartile":
                it.setForeground(QColor(_rs_quartile_fg(str(raw))))
            elif key in ("Outperforming_All_5", "LongTerm_Leader", "NearTerm_Leader"):
                it.setForeground(
                    QColor(C["green"] if txt == "✓" else C["muted"]))
            self._tbl.setItem(r, col_i, it)

    # ── Export CSV ────────────────────────────────────────────────────────────

    def _export_csv(self):
        if not self._result_rows:
            return
        rf, rt = self._rank_from.value(), self._rank_to.value()
        if rf > rt:
            QMessageBox.warning(self, "Invalid Range",
                                "'From' rank must be ≤ 'To' rank.")
            return
        subset = [r for r in self._result_rows
                  if rf <= r.get("Rank", 0) <= rt]
        if not subset:
            QMessageBox.information(
                self, "No Data",
                f"No symbols found in rank range {rf}–{rt}.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV",
            f"RatioScanner_R{rf}_R{rt}.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            import csv as _csv2
            keys = [c[0] for c in _RS_TABLE_COLS]
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = _csv2.DictWriter(f, fieldnames=keys, extrasaction="ignore")
                w.writeheader()
                w.writerows(subset)
            QMessageBox.information(
                self, "Exported",
                f"Saved {len(subset)} rows (ranks {rf}–{rt}) → {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    # ── Log helper ────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.append(f"[{ts}]  {msg}")

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        save_all_act = QAction("Save All Tickers to Watchlist", self)
        save_sel_act = QAction("Save Selected Ticker to Watchlist", self)
        save_all_act.triggered.connect(self._save_all_to_wl)
        save_sel_act.triggered.connect(self._save_selected_to_wl)
        menu.addAction(save_all_act)
        menu.addAction(save_sel_act)
        menu.exec(QCursor.pos())

    def _save_all_to_wl(self):
        symbols = []
        for r in range(self._tbl.rowCount()):
            if not self._tbl.isRowHidden(r):
                it = self._tbl.item(r, 1)
                if it and it.text():
                    symbols.append(it.text().strip().upper())
        if symbols:
            self.send_to_wl.emit(symbols)

    def _save_selected_to_wl(self):
        row = self._tbl.currentRow()
        if row >= 0:
            it = self._tbl.item(row, 1)
            if it and it.text():
                self.send_to_wl.emit([it.text().strip().upper()])



# ══════════════════════════════════════════════════════════════════════════════
# 10d.  MULTI BACKTESTER  — embeds StockLab.html in a QWebEngineView
# Ported from STOCKMATE / multibacktester_widget.py.
# STOCKMATE DB watchlist replaced by set_symbols() receiving Alpaca watchlist.
# ══════════════════════════════════════════════════════════════════════════════

# Guard for QtWebEngine (optional PySide6 component)
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView as _QWebEngineView
    from PySide6.QtWebEngineCore import (
        QWebEngineSettings  as _QWebEngineSettings,
        QWebEngineProfile   as _QWebEngineProfile,
        QWebEnginePage      as _QWebEnginePage,
    )
    _WEBENGINE_OK = True
except ImportError:
    _WEBENGINE_OK = False
    _QWebEngineView = None     # type: ignore[assignment,misc]
    _QWebEnginePage = object   # harmless base for _LabConsolePage

# Default StockLab.html path (STOCKMATE project folder)
_STOCKLAB_DEFAULT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "StockLab", "StockLab.html"))


# ── Silent web page (suppresses JS console spam) ─────────────────────────────

class _LabConsolePage(_QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceId):
        pass   # silence JS console; remove to debug


# ── DevTools window ───────────────────────────────────────────────────────────

class _DevToolsWin(QMainWindow):
    def __init__(self, dev_view, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MultiBacktester — DevTools")
        self.resize(960, 640)
        self.setCentralWidget(dev_view)


# ── Main Panel ────────────────────────────────────────────────────────────────

class MultiBacktesterPanel(QWidget):
    """
    Embeds StockLab.html (MultiBacktester) in a QWebEngineView.
    Symbols are provided via set_symbols() from the Alpaca watchlist.
    Ported from STOCKMATE MultiBacktesterWidget; DB dependency removed.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        from pathlib import Path as _Path
        self._symbols: list      = list(SCREEN_SYMS)
        self._html_path          = _Path(_STOCKLAB_DEFAULT)
        self._devtools_win       = None
        self._view               = None
        self._page               = None
        self._profile            = None

        if _WEBENGINE_OK:
            self._setup_engine()
        self._build()
        if _WEBENGINE_OK:
            self._load_html()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_symbols(self, symbols: list):
        """Called by ScreenerPanel.set_symbols() ← WatchlistPanel.symbols_changed."""
        self._symbols = list(symbols) if symbols else list(SCREEN_SYMS)
        count = len(self._symbols)
        self._sym_lbl.setText(f"  {count} symbol{'s' if count != 1 else ''} loaded")
        self._send_btn.setEnabled(count > 0)

    # ── Engine / profile setup ────────────────────────────────────────────────

    def _setup_engine(self):
        storage_base = os.environ.get("APPDATA", os.path.expanduser("~"))
        storage_path = os.path.join(storage_base, "AlpacaExplorer", "stocklab_cache")

        self._profile = _QWebEngineProfile("AlpacaExplorer_StockLab", self)
        self._profile.setPersistentStoragePath(storage_path)
        self._profile.setPersistentCookiesPolicy(
            _QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        s = self._profile.settings()
        s.setAttribute(_QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(_QWebEngineSettings.WebAttribute.AllowRunningInsecureContent,    True)
        s.setAttribute(_QWebEngineSettings.WebAttribute.LocalStorageEnabled,            True)
        s.setAttribute(_QWebEngineSettings.WebAttribute.JavascriptEnabled,              True)
        s.setAttribute(_QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,       True)
        s.setAttribute(_QWebEngineSettings.WebAttribute.PluginsEnabled,                 True)
        s.setAttribute(_QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled,          True)

        self._page = _LabConsolePage(self._profile, self)
        self._view = _QWebEngineView(self)
        self._view.setPage(self._page)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._page.setBackgroundColor(QColor("#07090f"))

        self._page.loadStarted.connect(
            lambda: self._bar_status.setText("Loading StockLab…") if hasattr(self, "_bar_status") else None
        )
        self._page.loadFinished.connect(self._on_load_finished)

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top control bar ───────────────────────────────────────────────────
        bar = QFrame()
        bar.setFixedHeight(46)
        bar.setStyleSheet(
            f"QFrame{{background:{C['card']};"
            f" border-bottom:1px solid {C['border']};}}"
        )
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 0, 12, 0)
        bl.setSpacing(8)

        def _sep():
            s = QFrame()
            s.setFrameShape(QFrame.VLine)
            s.setFixedSize(1, 24)
            s.setStyleSheet(f"background:{C['border']};border:none;")
            return s

        def _cbtn(txt, bg=C["card"], fg=C["text"], hov=C["border"]):
            b = QPushButton(txt)
            b.setStyleSheet(
                f"QPushButton{{background:{bg};color:{fg};border:none;"
                f"border-radius:4px;padding:5px 12px;"
                f"font-size:12px;font-weight:600;}}"
                f"QPushButton:hover{{background:{hov};}}"
                f"QPushButton:disabled{{opacity:0.35;}}"
            )
            return b

        sec_lbl = QLabel("⬡  MultiBacktester")
        sec_lbl.setStyleSheet(
            f"color:{C['accent']};font-size:14px;font-weight:800;background:transparent;"
        )

        self._sym_lbl = QLabel(f"  {len(self._symbols)} symbols loaded")
        self._sym_lbl.setStyleSheet(
            f"color:{C['muted']};font-size:11px;background:transparent;"
        )

        self._send_btn = _cbtn("▶  Send to Backtester", "#1a3a5c", C["accent"], "#1f4a75")
        self._send_btn.setEnabled(len(self._symbols) > 0)
        self._send_btn.clicked.connect(self._send_to_lab)

        self._reload_btn = _cbtn("↺", C["card"])
        self._reload_btn.setFixedSize(30, 30)
        self._reload_btn.setToolTip("Reload StockLab (F5)")
        if self._view:
            self._reload_btn.clicked.connect(self._view.reload)
        else:
            self._reload_btn.setEnabled(False)

        self._devtools_btn = _cbtn("🔧", C["card"])
        self._devtools_btn.setFixedSize(30, 30)
        self._devtools_btn.setCheckable(True)
        self._devtools_btn.setToolTip("Open Chromium DevTools")
        self._devtools_btn.toggled.connect(self._toggle_devtools)
        if not _WEBENGINE_OK:
            self._devtools_btn.setEnabled(False)

        self._path_btn = _cbtn("📂  Locate StockLab.html", C["card"])
        self._path_btn.setToolTip("Browse for StockLab.html if not found automatically")
        self._path_btn.clicked.connect(self._pick_html_path)

        self._bar_status = QLabel("Ready")
        self._bar_status.setStyleSheet(
            f"color:{C['muted']};font-size:10px;font-style:italic;background:transparent;"
        )
        self._bar_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        for w in (sec_lbl, _sep(), self._sym_lbl,
                  self._send_btn, _sep(),
                  self._reload_btn, self._devtools_btn,
                  self._path_btn):
            bl.addWidget(w)
        bl.addStretch()
        bl.addWidget(self._bar_status)

        root.addWidget(bar)

        if self._view:
            root.addWidget(self._view, 1)
            # F5 shortcut — lazy import to avoid touching global imports
            try:
                from PySide6.QtGui import QShortcut, QKeySequence as _KS
                QShortcut(_KS("F5"), self, self._view.reload)
            except Exception:
                pass
        else:
            # Fallback when QtWebEngine is not available
            no_web = QLabel(
                "⚠  QtWebEngine is not available in this PySide6 installation.\n\n"
                "The MultiBacktester tab requires PySide6 with QtWebEngine.\n\n"
                "Try:  pip install --upgrade PySide6\n\n"
                "On Windows, QtWebEngine is included in the standard PySide6 wheel."
            )
            no_web.setAlignment(Qt.AlignCenter)
            no_web.setStyleSheet(
                f"color:{C['red']};font-size:13px;background:{C['bg']};"
                f"padding:40px;border:1px solid {C['border']};border-radius:6px;"
            )
            root.addWidget(no_web, 1)

    # ── HTML loading ──────────────────────────────────────────────────────────

    def _load_html(self):
        from PySide6.QtCore import QUrl as _QUrl
        if not self._html_path.exists():
            self._view.setHtml(
                f"""<html><body style='background:#07090f;color:#ff3d57;
                    font-family:Segoe UI,sans-serif;padding:40px;font-size:14px;'>
                    <h2>⚠ StockLab.html not found</h2>
                    <p>Expected at:<br><code>{self._html_path}</code></p>
                    <p>Click <strong>📂 Locate StockLab.html</strong> in the toolbar
                    to browse for the file, or place <strong>StockLab.html</strong>
                    next to <em>alpaca_explorer.py</em> and reload.</p>
                    </body></html>"""
            )
            self._bar_status.setText("⚠ StockLab.html not found — use 📂 to locate it")
            return
        self._view.load(_QUrl.fromLocalFile(str(self._html_path)))

    def _on_load_finished(self, ok: bool):
        if ok:
            self._bar_status.setText(
                "StockLab ready  ·  click ▶ Send to Backtester to inject watchlist symbols"
            )
            # Auto-populate the Stock Universe with current watchlist on every load
            self._auto_inject_universe()
        else:
            self._bar_status.setText("⚠ StockLab failed to load")

    def _auto_inject_universe(self):
        """
        Silently inject current watchlist symbols into StockLab's Stock Universe
        (ensureImportedConfigs) and select them — runs automatically after page load
        so the universe shows US symbols without any manual button click.
        """
        if not self._page or not self._symbols:
            return
        import json as _json
        syms_json = _json.dumps(self._symbols)
        js = f"""
(function() {{
    var symsArr = {syms_json};
    if (typeof ensureImportedConfigs === 'function') {{
        ensureImportedConfigs(symsArr);
    }}
    if (typeof selectImported === 'function') {{
        selectImported();
    }}
    return 'auto-OK';
}})();
"""
        self._page.runJavaScript(js)

    # ── Symbol injection ──────────────────────────────────────────────────────

    def _send_to_lab(self):
        if not self._page:
            return
        syms = self._symbols
        if not syms:
            return
        sym_text  = "\\n".join(syms)   # JS-escaped newlines
        n         = len(syms)
        import json as _json
        syms_json = _json.dumps(syms)    # JS array literal, e.g. ["AAPL","MSFT"]
        js = f"""
(function() {{
    var ta = document.getElementById('sym-paste');
    if (!ta) {{ return 'ERR:no-textarea'; }}

    // 1. Fill the sym-paste textarea with US watchlist symbols
    ta.value = "{sym_text}";
    ta.dispatchEvent(new Event('input',  {{bubbles: true}}));
    ta.dispatchEvent(new Event('change', {{bubbles: true}}));

    // 2. Inject symbols into the Stock Universe so they appear as checkboxes
    var symsArr = {syms_json};
    if (typeof ensureImportedConfigs === 'function') {{
        ensureImportedConfigs(symsArr);
    }}

    // 3. Select (check) all imported symbols in the universe
    if (typeof selectImported === 'function') {{
        selectImported();
    }}

    ta.scrollIntoView({{behavior:'smooth', block:'center'}});
    ta.focus();
    return 'OK:{n}';
}})();
"""
        self._page.runJavaScript(js, self._on_inject_done)
        self._bar_status.setText(f"Sending {n} symbols…")

    def _on_inject_done(self, result):
        if isinstance(result, str) and result.startswith("OK:"):
            n   = result.split(":")[1]
            msg = f"✓ {n} symbols sent to backtester"
        else:
            msg = f"⚠ Inject failed ({result})"
        self._bar_status.setText(msg)

    # ── Path picker ───────────────────────────────────────────────────────────

    def _pick_html_path(self):
        from pathlib import Path as _Path
        start = str(self._html_path.parent) if self._html_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate StockLab.html", start, "HTML files (*.html *.htm)"
        )
        if path:
            self._html_path = _Path(path)
            if _WEBENGINE_OK and self._view:
                self._load_html()

    # ── DevTools ──────────────────────────────────────────────────────────────

    def _toggle_devtools(self, checked: bool):
        if not self._page:
            return
        if checked:
            if self._devtools_win is None:
                dev_view = _QWebEngineView()
                self._page.setDevToolsPage(dev_view.page())
                self._devtools_win = _DevToolsWin(dev_view, self)
            self._devtools_win.show()
            self._devtools_win.raise_()
        else:
            if self._devtools_win:
                self._devtools_win.hide()

# ══════════════════════════════════════════════════════════════════════════════
# 10.  SCREENER PANEL  — 3-subtab wrapper
#      📊 MARKET SCREENER   (Alpaca snapshot, existing)
#      🔍 PATTERN SCREENER  (yfinance, ported from STOCKMATE)
#      📉 RATIO SCANNER     (yfinance vs SPY, ported from STOCKMATE)
# ══════════════════════════════════════════════════════════════════════════════
