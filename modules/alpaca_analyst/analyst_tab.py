import os
import sys
import json
import csv
import traceback
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Add core data libraries
import pandas as pd
_PD_OK = True

import numpy as np
import requests

log = logging.getLogger("analyst_tab")

# Add the local directory and subfolders to path so engines resolve
here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
sys.path.insert(0, str(here / "TechnicalAnalyst"))
sys.path.insert(0, str(here / "CANSLIMPRO"))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QStackedWidget, QFrame, QScrollArea, QComboBox,
    QButtonGroup, QSplitter, QMenu, QTabWidget, QSizePolicy,
    QAbstractItemView, QListWidget, QListWidgetItem, QProgressBar,
    QGroupBox, QTextEdit, QDoubleSpinBox, QSpinBox, QCheckBox,
    QGridLayout, QTextBrowser, QFileDialog, QMessageBox, QDialog,
    QRadioButton, QDialogButtonBox, QInputDialog
)
from PySide6.QtCore import (
    Qt, QThreadPool, QRunnable, QObject, Signal, Slot, QTimer, QThread, QUrl
)
from PySide6.QtGui import QColor, QFont, QCursor, QPixmap, QAction

# Setup Matplotlib QTAgg backends
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

# Setup DatabaseManager
from modules.stock_evaluate.database import DatabaseManager

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
    "accent":  "#00d4ff",  # Synced with USmarketsResearch Neon Blue
    "green":   "#3fb950",
    "red":     "#f85149",
    "yellow":  "#ffd043",
    "purple":  "#bc8cff",
    "text":    "#ffffff",
    "sec":     "#b1bac4",
    "muted":   "#8b949e",
    "chart_bg":"#0d1117",
}

# Directories and paths
_ALP_DIR       = str(here)
_TA_ENGINE_DIR = str(here / "TechnicalAnalyst")
_CS_ENGINE_DIR = str(here / "CANSLIMPRO")
_AN_DATA_DIR   = os.path.join(_ALP_DIR, "data", "analyst")
_AN_RPT_DIR    = os.path.join(_AN_DATA_DIR, "reports")
_AN_CHT_DIR    = os.path.join(_AN_DATA_DIR, "charts")
_AN_CRP_DIR    = os.path.join(_AN_DATA_DIR, "canslim_reports")
_AN_CACHE_DB   = os.path.join(_AN_DATA_DIR, "ta_cache.db")

for _d in [_AN_DATA_DIR, _AN_RPT_DIR, _AN_CHT_DIR, _AN_CRP_DIR]:
    os.makedirs(_d, exist_ok=True)

# ── Symbol management variables for Stock Lens ──
_SL_DIR     = os.path.join(_ALP_DIR, "SecurityMaster")
_SL_CSV     = os.path.join(_ALP_DIR, "SecurityMaster", "USStockMaster.csv")
# Fallback to root USStockMaster.csv if SecurityMaster one doesn't exist
if not os.path.exists(_SL_CSV):
    _SL_CSV = str(Path(__file__).resolve().parent.parent.parent / "USStockMaster.csv")

_SL_RPT_DIR = os.path.join(_AN_RPT_DIR, "stock_lens")
_SL_CHT_DIR = os.path.join(_SL_RPT_DIR, "charts")
for _d in (_SL_DIR, _SL_RPT_DIR, _SL_CHT_DIR):
    os.makedirs(_d, exist_ok=True)

def get_evaluator_presets() -> dict:
    presets = {}
    try:
        db = DatabaseManager()
        constituents = db.get_all_constituents()
        
        # Sector presets
        sectors = {}
        for c in constituents:
            sec = c.get("sector")
            if sec:
                sectors.setdefault(sec, []).append(c["symbol"])
        for sec, syms in sectors.items():
            presets[f"Sector: {sec}"] = syms
            
        # Index presets
        sp500 = [c["symbol"] for c in constituents if c.get("is_sp500")]
        if sp500:
            presets["Index: S&P 500"] = sp500
        nasdaq = [c["symbol"] for c in constituents if c.get("is_nasdaq")]
        if nasdaq:
            presets["Index: NASDAQ-100"] = nasdaq
            
        # Cap segment presets
        caps = {}
        for c in constituents:
            cap = c.get("cap_segment")
            if cap:
                caps.setdefault(cap, []).append(c["symbol"])
        for cap, syms in caps.items():
            presets[f"Cap: {cap}"] = syms
            
    except Exception as e:
        print(f"Error loading evaluator presets from DatabaseManager: {e}")
    return presets

_SL_SYMBOLS: dict = {}

def _sl_load_symbols() -> int:
    global _SL_SYMBOLS
    if not os.path.exists(_SL_CSV):
        return 0
    try:
        import csv as _csv_mod
        d: dict = {}
        with open(_SL_CSV, newline="", encoding="utf-8") as f:
            for row in _csv_mod.DictReader(f):
                tk = row.get("Symbol", row.get("ticker", "")).strip().upper()
                if tk:
                    name = row.get("Company", row.get("name", ""))
                    exch = row.get("Exchange", row.get("exchange", ""))
                    d[tk] = f"{name:<40}  {exch}"
        _SL_SYMBOLS = d
        return len(d)
    except Exception:
        return 0

def _sl_csv_needs_update() -> bool:
    if not os.path.exists(_SL_CSV):
        return True
    age = (datetime.now().timestamp() - os.path.getmtime(_SL_CSV)) / 86400
    return age >= 30

# Load engines
try:
    from ta_engine    import TechnicalAnalyzer, ChartGenerator, CacheManager, DataFetcher
    from ta_ai_engine import AIInsightsEngine
    from ta_reports   import ReportGenerator
    _TA_OK  = True
    _TA_ERR = ""
except ImportError as _e:
    _TA_OK  = False
    _TA_ERR = str(_e)

def _load_cs_engine():
    if not os.path.isdir(_CS_ENGINE_DIR):
        return False, f"CANSLIMPRO not found at:\n  {_CS_ENGINE_DIR}", {}
    if _CS_ENGINE_DIR not in sys.path:
        sys.path.insert(0, _CS_ENGINE_DIR)
    saved = {}
    for key in list(sys.modules):
        if key == "core" or key.startswith("core.") or key == "config":
            saved[key] = sys.modules.pop(key)
    try:
        from core.canslim_engine   import score_canslim, CANSLIMResult, ComponentResult
        from core.data_fetcher     import fetch_stock_data, StockData
        from core                  import cache as _cs_cache
        from core.report_generator import generate_report
        from config                import COMPONENT_LABELS, WEIGHTS, RATING_BANDS, get_rating
        return True, "", {
            "score_canslim":    score_canslim,
            "CANSLIMResult":    CANSLIMResult,
            "ComponentResult":  ComponentResult,
            "fetch_stock_data": fetch_stock_data,
            "StockData":        StockData,
            "_cs_cache":        _cs_cache,
            "generate_report":  generate_report,
            "COMPONENT_LABELS": COMPONENT_LABELS,
            "WEIGHTS":          WEIGHTS,
            "RATING_BANDS":     RATING_BANDS,
            "get_rating":       get_rating,
        }
    except Exception as exc:
        return False, str(exc), {}
    finally:
        for key in list(sys.modules):
            if key == "core" or key.startswith("core.") or key == "config":
                del sys.modules[key]
        sys.modules.update(saved)

_CS_LOADED, _CS_ERR, _cs_syms = _load_cs_engine()
_CS_OK            = _CS_LOADED

_cs_score         = _cs_syms.get("score_canslim")
_cs_fetch         = _cs_syms.get("fetch_stock_data")
_cs_cache_mod     = _cs_syms.get("_cs_cache")
_cs_report        = _cs_syms.get("generate_report")
_CS_COMP_KEYS     = list("CANSLIM")
_CS_COLS          = ["#", "Ticker", "Company", "Score", "Rating",
                     "C", "A", "N", "S", "L", "I", "M", "Buy?", "Quality"]


# ══════════════════════════════════════════════════════════════════════════════
# 14a.  TECHNALYZE  ─ Technical Analyst (US only, external TA engine)
# ══════════════════════════════════════════════════════════════════════════════

class _SignalBadge(QLabel):
    _COLORS = {
        "BULLISH": ("06d6a0", "0f2e26"), "UPTREND": ("06d6a0", "0f2e26"),
        "STRONG":  ("06d6a0", "0f2e26"), "CONFIRMING": ("06d6a0", "0f2e26"),
        "RISING":  ("06d6a0", "0f2e26"), "ABOVE": ("06d6a0", "0f2e26"),
        "INCREASING": ("06d6a0", "0f2e26"), "BULLISH_DIVERGENCE": ("06d6a0", "0f2e26"),
        "BEARISH": ("e94560", "2e0f1a"), "DOWNTREND": ("e94560", "2e0f1a"),
        "WEAK":    ("e94560", "2e0f1a"), "FALLING": ("e94560", "2e0f1a"),
        "BELOW":   ("e94560", "2e0f1a"), "DECREASING": ("e94560", "2e0f1a"),
        "BEARISH_DIVERGENCE": ("e94560", "2e0f1a"),
        "NEUTRAL": ("ffd700", "2e2a0f"), "SIDEWAYS": ("ffd700", "2e2a0f"),
        "MODERATE": ("ffd700", "2e2a0f"), "MIXED": ("ffd700", "2e2a0f"),
        "STABLE":  ("a0a0c0", "1a1a2e"), "FLAT": ("a0a0c0", "1a1a2e"),
        "UNKNOWN": ("a0a0c0", "1a1a2e"),
    }
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        key = text.upper().replace(" ", "_")
        fc, bc = self._COLORS.get(key, ("a0a0c0", "1a1a2e"))
        self.setStyleSheet(
            f"QLabel{{background:#{bc};color:#{fc};"
            f"border:1px solid #{fc}44;border-radius:4px;"
            f"padding:2px 8px;font-size:10px;font-weight:bold;}}"
        )
        self.setFixedHeight(20)


class _ScenarioCard(QFrame):
    def __init__(self, sc: dict, parent=None):
        super().__init__(parent)
        typ   = sc.get("type", "NEUTRAL")
        prob  = sc.get("probability", 0)
        name  = sc.get("name", "")
        desc  = sc.get("description", "")
        facts = sc.get("supporting_factors", [])
        tgts  = sc.get("target_levels", [])
        inv   = sc.get("invalidation_level", 0)
        color = {"BULLISH": "#06d6a0", "BEARISH": "#e94560",
                 "NEUTRAL": "#ffd700"}.get(typ, "#a0a0c0")
        self.setStyleSheet(
            f"_ScenarioCard{{background:#16213e;"
            f"border:1px solid {color}44;border-left:3px solid {color};"
            f"border-radius:6px;margin:3px;}}"
        )
        self.setFrameShape(QFrame.StyledPanel)
        vl = QVBoxLayout(self)
        vl.setSpacing(5); vl.setContentsMargins(10, 8, 10, 8)
        title_row = QHBoxLayout()
        icon = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(typ, "⚪")
        tl = QLabel(f"{icon}  {name}")
        tl.setFont(QFont("Segoe UI", 9, QFont.Bold))
        tl.setStyleSheet(f"color:{color};"); tl.setWordWrap(True)
        title_row.addWidget(tl); title_row.addStretch()
        pl = QLabel(f"{prob}%")
        pl.setFont(QFont("Segoe UI", 14, QFont.Bold))
        pl.setStyleSheet(f"color:{color};")
        title_row.addWidget(pl); vl.addLayout(title_row)
        bar = QProgressBar(); bar.setRange(0, 100); bar.setValue(prob)
        bar.setFixedHeight(5); bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar{{background:#0f1a2e;border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{color};border-radius:2px;}}"
        )
        vl.addWidget(bar)
        dl = QLabel(desc); dl.setWordWrap(True)
        dl.setStyleSheet("color:#c0c0d0;font-size:9px;"); vl.addWidget(dl)
        for f in facts[:3]:
            fl = QLabel(f"• {f}"); fl.setWordWrap(True)
            fl.setStyleSheet("color:#808090;font-size:8px;"); vl.addWidget(fl)
        meta = QHBoxLayout()
        if tgts:
            meta.addWidget(QLabel(
                f"Targets: {', '.join(f'${t:,.2f}' for t in tgts)}"
            ))
        meta.addStretch()
        inv_lbl = QLabel(f"Invalidation: ${inv:,.2f}")
        inv_lbl.setStyleSheet("color:#e94560;font-size:8px;")
        meta.addWidget(inv_lbl); vl.addLayout(meta)


class _TechWorker(QThread):
    """Fetch → TA → Chart → AI, off-thread."""
    progress = Signal(str)
    finished = Signal(dict, object, dict)
    error    = Signal(str)

    def __init__(self, symbol, interval, period, creds, use_ai, parent=None):
        super().__init__(parent)
        self.symbol   = symbol
        self.interval = interval
        self.period   = period
        self.creds    = creds
        self.use_ai   = use_ai

    def run(self):
        try:
            cache    = CacheManager(_AN_CACHE_DB)
            fetcher  = DataFetcher(cache, self.creds)
            analyzer = TechnicalAnalyzer()
            charter  = ChartGenerator()
            self.progress.emit(f"Fetching {self.symbol} ({self.interval}, {self.period})…")
            df = fetcher.fetch_ohlcv(self.symbol, interval=self.interval,
                                     period=self.period, source="auto")
            if df is None or df.empty:
                self.error.emit(f"No data for '{self.symbol}'. Check symbol or try a different period.")
                return
            self.progress.emit("Running technical analysis…")
            analysis = analyzer.analyze(df, symbol=self.symbol)
            if "error" in analysis:
                self.error.emit(analysis["error"]); return
            self.progress.emit("Generating chart…")
            cp  = analysis.get("current_price", 0)
            fig = charter.create(
                df, analysis=analysis,
                title=f"{self.symbol}  —  {self.interval}  |  ${cp:,.2f}",
                show_ma=True, show_vol=True, show_sr=True, show_bb=False,
            )
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            chart_path = os.path.join(_AN_CHT_DIR, f"{self.symbol}_{self.interval}_{ts}.png")
            charter.save(fig, chart_path)
            analysis["_chart_path"] = chart_path
            analysis["_df_len"]     = len(df)
            ai_result: dict = {}
            if self.use_ai and self.creds.get("gemini_key"):
                self.progress.emit("Generating AI insights (Gemini)…")
                try:
                    ai_eng   = AIInsightsEngine(self.creds)
                    ai_result = ai_eng.generate_insights(
                        analysis,
                        use_perplexity=bool(self.creds.get("perplexity_key")),
                        progress_cb=self.progress.emit,
                    )
                except Exception as ai_e:
                    ai_result = {"combined": f"AI error: {ai_e}"}
            elif self.use_ai:
                ai_result = {"combined": "No Gemini API key configured."}
            self.progress.emit("Saving report…")
            try:
                gen = ReportGenerator()
                gen.save_markdown(analysis, _AN_RPT_DIR, ai_result, chart_path)
            except Exception:
                pass
            self.finished.emit(analysis, fig, ai_result)
        except Exception as e:
            self.error.emit(f"Analysis failed: {e}\n\n{traceback.format_exc()}")


class TechnalyzePanel(QWidget):
    """TECHNALYZE sub-tab — US stocks, external TA engine."""

    trade_requested = Signal(str, str)   # (symbol, side)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._creds    = self._load_creds()
        self._analysis = None
        self._ai_result = None
        self._worker   = None
        self._regen_w  = None
        self._symbols: list = []
        self._current_sym  = ""
        if not _TA_OK:
            self._build_error_ui()
        else:
            self._build_ui()

    # ── credential loader ─────────────────────────────────────────────────────
    @staticmethod
    def _load_creds() -> dict:
        creds: dict = {"alpaca_key": API_KEY, "alpaca_secret": API_SECRET}
        allapi = os.path.join(_ALP_DIR, "ALLAPI")
        for fname, key in [
            ("GEMINI_API_KEY.txt",     "gemini_key"),
            ("PERPLEXITY_API_KEY.txt", "perplexity_key"),
        ]:
            p = os.path.join(allapi, fname)
            if os.path.exists(p):
                try:
                    creds[key] = open(p).read().strip()
                except Exception:
                    pass
        return creds

    # ── error fallback ────────────────────────────────────────────────────────
    def _build_error_ui(self):
        vl = QVBoxLayout(self)
        lbl = QLabel(
            f"⚠  TechnicalAnalyst engine not found.\n\n"
            f"Error: {_TA_ERR}\n\n"
            f"Expected project at:\n  {_TA_ENGINE_DIR}\n\n"
            f"Clone the TechnicalAnalyst project there to enable this tab."
        )
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{C['yellow']}; font-size:11px;")
        lbl.setWordWrap(True)
        vl.addWidget(lbl)

    # ── main layout ───────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_center())
        splitter.addWidget(self._build_right())
        splitter.setSizes([220, 780, 360])
        root.addWidget(splitter)

    # ── left panel ────────────────────────────────────────────────────────────
    def _build_left(self) -> QWidget:
        w = QWidget(); w.setFixedWidth(220)
        vl = QVBoxLayout(w)
        vl.setContentsMargins(6, 6, 6, 6); vl.setSpacing(6)

        title_bar = QHBoxLayout()
        lbl_title = QLabel("Technalyze")
        lbl_title.setStyleSheet("color:#ffffff; font-weight:600; font-size:11px;")
        title_bar.addWidget(lbl_title)

        toggle_btn = QPushButton("«")
        toggle_btn.setToolTip("Collapse/Expand Sidebar")
        toggle_btn.setCursor(Qt.PointingHandCursor)
        toggle_btn.setStyleSheet("QPushButton { color:#00f0ff; font-weight:bold; border:none; background:transparent; font-size:16px; min-width:24px; max-width:24px; } QPushButton:hover { color:#00a8ff; }")
        title_bar.addWidget(toggle_btn)
        vl.addLayout(title_bar)

        content_w = QWidget()
        content_w.setStyleSheet("background:transparent;")
        content_lay = QVBoxLayout(content_w)
        content_lay.setContentsMargins(0, 0, 0, 0); content_lay.setSpacing(6)
        vl.addWidget(content_w)

        def toggle_sidebar():
            if content_w.isVisible():
                content_w.hide()
                lbl_title.hide()
                w.setFixedWidth(35)
                toggle_btn.setText("»")
            else:
                content_w.show()
                lbl_title.show()
                w.setFixedWidth(220)
                toggle_btn.setText("«")
        toggle_btn.clicked.connect(toggle_sidebar)

        sym_grp = QGroupBox("Symbol")
        sym_vl  = QVBoxLayout(sym_grp); sym_vl.setSpacing(4)
        self._sym_input = QLineEdit()
        self._sym_input.setPlaceholderText("AAPL, MSFT, NVDA…")
        self._sym_input.setFixedHeight(30)
        self._sym_input.returnPressed.connect(self._trigger_analysis)
        sym_vl.addWidget(self._sym_input)
        content_lay.addWidget(sym_grp)

        combo_row = QHBoxLayout(); combo_row.setSpacing(4)
        self._interval_cb = QComboBox()
        self._interval_cb.addItems(["Daily", "Weekly", "Monthly"])
        self._interval_cb.setCurrentText("Weekly")
        self._period_cb = QComboBox()
        self._period_cb.addItems(["3 Months", "6 Months", "1 Year", "2 Years", "5 Years"])
        self._period_cb.setCurrentText("2 Years")
        combo_row.addWidget(self._interval_cb); combo_row.addWidget(self._period_cb)
        content_lay.addLayout(combo_row)

        self._analyze_btn = QPushButton("▶  ANALYZE")
        self._analyze_btn.setFixedHeight(36)
        self._analyze_btn.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self._analyze_btn.setStyleSheet(
            f"QPushButton{{background:{C['accent']};color:{C['bg']};border:none;"
            f"border-radius:6px;font-weight:bold;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['muted']};}}"
        )
        self._analyze_btn.clicked.connect(self._trigger_analysis)
        content_lay.addWidget(self._analyze_btn)

        _tna_dry = QHBoxLayout(); _tna_dry.setSpacing(4)
        self._btn_tna_buy = QPushButton("🟢  Buy")
        self._btn_tna_buy.setFixedHeight(26)
        self._btn_tna_buy.setEnabled(False)
        self._btn_tna_buy.setStyleSheet(
            "QPushButton{background:#1a7f37;color:#fff;border:none;border-radius:4px;"
            "padding:2px 8px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:#238636;}"
            "QPushButton:disabled{background:#555;color:#888;}")
        self._btn_tna_buy.clicked.connect(
            lambda: self.trade_requested.emit(self._current_sym, "buy"))
        self._btn_tna_sell = QPushButton("🔴  Sell")
        self._btn_tna_sell.setFixedHeight(26)
        self._btn_tna_sell.setEnabled(False)
        self._btn_tna_sell.setStyleSheet(
            "QPushButton{background:#b91c1c;color:#fff;border:none;border-radius:4px;"
            "padding:2px 8px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:#dc2626;}"
            "QPushButton:disabled{background:#555;color:#888;}")
        self._btn_tna_sell.clicked.connect(
            lambda: self.trade_requested.emit(self._current_sym, "sell"))
        _tna_dry.addWidget(self._btn_tna_buy)
        _tna_dry.addWidget(self._btn_tna_sell)
        content_lay.addLayout(_tna_dry)

        self._prog_bar = QProgressBar()
        self._prog_bar.setRange(0, 0); self._prog_bar.setFixedHeight(4)
        self._prog_bar.setTextVisible(False); self._prog_bar.hide()
        content_lay.addWidget(self._prog_bar)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{C['border']}"); content_lay.addWidget(sep)

        wl_hdr = QLabel("📋  Watchlist")
        wl_hdr.setStyleSheet(f"color:{C['accent']};font-weight:bold;font-size:10px;")
        content_lay.addWidget(wl_hdr)
        self._wl_list = QListWidget()
        self._wl_list.setAlternatingRowColors(False)
        self._wl_list.setMaximumHeight(200)
        self._wl_list.setStyleSheet(
            f"QListWidget{{background:{C['card']};border:1px solid {C['border']};"
            f"border-radius:4px;color:{C['text']};font-size:11px;}}"
            f"QListWidget::item:selected{{background:{C['hover']};color:{C['accent']};}}"
        )
        self._wl_list.itemDoubleClicked.connect(
            lambda item: (self._sym_input.setText(item.data(Qt.UserRole) or item.text().strip()),
                          QTimer.singleShot(50, self._trigger_analysis))
        )
        content_lay.addWidget(self._wl_list)

        self._ai_btn = QPushButton("🤖  AI Insights: ON")
        self._ai_btn.setCheckable(True); self._ai_btn.setChecked(True)
        self._ai_btn.setFixedHeight(28)
        self._ai_btn.toggled.connect(
            lambda on: self._ai_btn.setText(f"🤖  AI Insights: {'ON' if on else 'OFF'}")
        )
        content_lay.addWidget(self._ai_btn)
        content_lay.addStretch()
        rpt_btn = QPushButton("📂  Reports Folder")
        rpt_btn.setFixedHeight(28)
        rpt_btn.clicked.connect(lambda: __import__("subprocess").Popen(
            f'explorer "{_AN_RPT_DIR}"', shell=True))
        content_lay.addWidget(rpt_btn)
        return w
        return w

    # ── center panel ──────────────────────────────────────────────────────────
    def _build_center(self) -> QWidget:
        w = QWidget(); vl = QVBoxLayout(w)
        vl.setContentsMargins(4, 4, 4, 4); vl.setSpacing(4)
        self._ticker_strip = QLabel("—  Select a symbol and press ANALYZE")
        self._ticker_strip.setFont(QFont("Consolas", 10, QFont.Bold))
        self._ticker_strip.setAlignment(Qt.AlignCenter)
        self._ticker_strip.setStyleSheet(
            f"background:{C['card']};color:{C['accent']};padding:5px 10px;border-radius:4px;"
        )
        vl.addWidget(self._ticker_strip)
        self._prog_lbl = QLabel()
        self._prog_lbl.setAlignment(Qt.AlignCenter)
        self._prog_lbl.setStyleSheet(f"color:{C['sec']};font-size:10px;")
        self._prog_lbl.hide(); vl.addWidget(self._prog_lbl)
        self._canvas_container = QWidget()
        self._canvas_vl = QVBoxLayout(self._canvas_container)
        self._canvas_vl.setContentsMargins(0, 0, 0, 0)
        if _QTAGG_OK and _TA_OK:
            try:
                gen = ChartGenerator()
                empty = gen._empty("Select a symbol and press ANALYZE")
                self._canvas = _FigCanvas(empty)
                self._canvas_vl.addWidget(self._canvas)
            except Exception:
                self._canvas_vl.addWidget(QLabel("Chart will appear here."))
        else:
            ph = QLabel("Chart will appear here after analysis.")
            ph.setAlignment(Qt.AlignCenter)
            ph.setStyleSheet(f"color:{C['muted']};")
            self._canvas_vl.addWidget(ph)
        vl.addWidget(self._canvas_container)
        return w

    # ── right panel ───────────────────────────────────────────────────────────
    def _build_right(self) -> QWidget:
        w = QWidget(); w.setMinimumWidth(320)
        vl = QVBoxLayout(w); vl.setContentsMargins(4, 4, 4, 4); vl.setSpacing(4)
        tabs = QTabWidget()
        tabs.addTab(self._build_summary_tab(),   "📊 Summary")
        tabs.addTab(self._build_scenarios_tab(), "🎯 Scenarios")
        tabs.addTab(self._build_ai_tab(),        "🤖 AI")
        tabs.addTab(self._build_export_tab(),    "📤 Export")
        self._right_tabs = tabs
        vl.addWidget(tabs)
        return w

    def _build_summary_tab(self) -> QWidget:
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); scroll.setWidget(inner)
        vl = QVBoxLayout(inner); vl.setSpacing(8); vl.setContentsMargins(4, 4, 4, 4)
        self._grp_trend = self._make_grp("Trend")
        self._grp_ma    = self._make_grp("Moving Averages")
        self._grp_vol   = self._make_grp("Volume")
        self._grp_sr    = self._make_grp("Key Levels")
        for g in [self._grp_trend, self._grp_ma, self._grp_vol, self._grp_sr]:
            vl.addWidget(g)
        obs_grp = QGroupBox("Key Observations"); obs_vl = QVBoxLayout(obs_grp)
        self._obs_text = QTextEdit(); self._obs_text.setReadOnly(True)
        self._obs_text.setFixedHeight(100)
        self._obs_text.setStyleSheet(
            f"background:{C['card']};color:{C['text']};font-size:10px;border:none;"
        )
        obs_vl.addWidget(self._obs_text); vl.addWidget(obs_grp)
        ass_grp = QGroupBox("Assessment"); ass_vl = QVBoxLayout(ass_grp)
        self._ass_text = QTextEdit(); self._ass_text.setReadOnly(True)
        self._ass_text.setFixedHeight(80)
        self._ass_text.setStyleSheet(
            f"background:{C['card']};color:{C['text']};font-size:10px;border:none;"
        )
        ass_vl.addWidget(self._ass_text); vl.addWidget(ass_grp)
        vl.addStretch(); return scroll

    def _build_scenarios_tab(self) -> QWidget:
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._scen_inner = QWidget()
        self._scen_vl    = QVBoxLayout(self._scen_inner)
        self._scen_vl.setSpacing(4); self._scen_vl.setContentsMargins(4, 4, 4, 4)
        ph = QLabel("Run analysis to see scenario cards")
        ph.setAlignment(Qt.AlignCenter); ph.setStyleSheet(f"color:{C['muted']};font-size:12px;")
        self._scen_vl.addWidget(ph); self._scen_vl.addStretch()
        scroll.setWidget(self._scen_inner); return scroll

    def _build_ai_tab(self) -> QWidget:
        w = QWidget(); vl = QVBoxLayout(w); vl.setContentsMargins(4, 4, 4, 4)
        self._ai_text = QTextEdit(); self._ai_text.setReadOnly(True)
        self._ai_text.setPlaceholderText(
            "AI insights appear here after analysis.\n\nRequires: ALLAPI/GEMINI_API_KEY.txt"
        )
        self._ai_text.setStyleSheet(
            f"background:{C['card']};color:{C['text']};font-size:10px;border:none;"
        )
        vl.addWidget(self._ai_text)
        regen = QPushButton("↻  Regenerate AI Insights")
        regen.setFixedHeight(28); regen.clicked.connect(self._regen_ai)
        vl.addWidget(regen); return w

    def _build_export_tab(self) -> QWidget:
        w = QWidget(); vl = QVBoxLayout(w)
        vl.setContentsMargins(8, 8, 8, 8); vl.setSpacing(8)
        hdr = QLabel("Export Analysis Report")
        hdr.setFont(QFont("Segoe UI", 10, QFont.Bold))
        hdr.setStyleSheet(f"color:{C['accent']};"); vl.addWidget(hdr)
        for text, fmt in [("📄 Markdown (.md)", "md"), ("🌐 HTML (.html)", "html"),
                          ("📑 PDF (.pdf)", "pdf"), ("🗄 JSON (.json)", "json")]:
            btn = QPushButton(text); btn.setFixedHeight(34)
            btn.clicked.connect(lambda checked=False, f=fmt: self._export(f))
            vl.addWidget(btn)
        sep = QFrame(); sep.setFrameShape(QFrame.HLine); vl.addWidget(sep)
        opn = QPushButton("📂 Open Reports Folder"); opn.setFixedHeight(32)
        opn.clicked.connect(lambda: __import__("subprocess").Popen(
            f'explorer "{_AN_RPT_DIR}"', shell=True))
        vl.addWidget(opn); vl.addStretch(); return w

    # ── group box helpers ─────────────────────────────────────────────────────
    def _make_grp(self, title: str) -> QGroupBox:
        g = QGroupBox(title); g._gl = QVBoxLayout(g); g._gl.setSpacing(3)
        g._ph = QLabel("—"); g._ph.setStyleSheet(f"color:{C['muted']};font-size:10px;")
        g._gl.addWidget(g._ph); return g

    def _clear_grp(self, g: QGroupBox):
        while g._gl.count():
            item = g._gl.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        g._ph = QLabel("—"); g._ph.setStyleSheet(f"color:{C['muted']};font-size:10px;")
        g._gl.addWidget(g._ph)

    def _add_row(self, g: QGroupBox, label: str, value: str, badge: bool = False):
        row = QHBoxLayout()
        lbl = QLabel(label + ":"); lbl.setStyleSheet(f"color:{C['sec']};font-size:10px;")
        lbl.setFixedWidth(112); row.addWidget(lbl)
        if badge:
            val_w = _SignalBadge(value)
        else:
            val_w = QLabel(value); val_w.setStyleSheet(f"color:{C['text']};font-size:10px;")
            val_w.setWordWrap(True)
        row.addWidget(val_w); row.addStretch()
        cw = QWidget(); cw.setLayout(row)
        if g._gl.count() == 1 and g._gl.itemAt(0).widget() is g._ph:
            g._gl.removeWidget(g._ph); g._ph.deleteLater()
        g._gl.addWidget(cw)

    # ── watchlist integration ─────────────────────────────────────────────────
    def set_symbols(self, symbols: list):
        self._symbols = symbols
        self._wl_list.clear()
        for sym in symbols:
            item = QListWidgetItem(f"  {sym}")
            item.setData(Qt.UserRole, sym)
            self._wl_list.addItem(item)

    # ── analysis trigger ──────────────────────────────────────────────────────
    def _trigger_analysis(self):
        if not _TA_OK:
            QMessageBox.warning(self, "Engine Error",
                                f"TA engine not loaded:\n{_TA_ERR}\n\nPath: {_TA_ENGINE_DIR}")
            return
        symbol = self._sym_input.text().strip().upper()
        if not symbol: return
        if self._worker and self._worker.isRunning(): return
        self._current_sym = symbol
        self._btn_tna_buy.setEnabled(False)
        self._btn_tna_sell.setEnabled(False)
        self._set_busy(True)
        self._ticker_strip.setText(f"⏳  {symbol}  —  fetching…")
        self._ticker_strip.setStyleSheet(
            f"background:{C['card']};color:{C['sec']};padding:5px 10px;border-radius:4px;"
        )
        self._worker = _TechWorker(
            symbol   = symbol,
            interval = self._interval_cb.currentText(),
            period   = self._period_cb.currentText(),
            creds    = self._creds,
            use_ai   = self._ai_btn.isChecked(),
            parent   = self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _regen_ai(self):
        if not self._analysis or not _TA_OK: return
        if self._worker and self._worker.isRunning(): return
        if not self._creds.get("gemini_key"):
            self._ai_text.setPlainText("No Gemini key.\nAdd to: ALLAPI/GEMINI_API_KEY.txt")
            return

        class _AIRegen(QThread):
            progress = Signal(str); finished = Signal(dict); error = Signal(str)
            def __init__(self_, analysis, creds, parent=None):
                super().__init__(parent); self_.analysis = analysis; self_.creds = creds
            def run(self_):
                try:
                    ai = AIInsightsEngine(self_.creds)
                    self_.finished.emit(ai.generate_insights(
                        self_.analysis,
                        use_perplexity=bool(self_.creds.get("perplexity_key")),
                        progress_cb=self_.progress.emit,
                    ))
                except Exception as e:
                    self_.error.emit(str(e))

        self._set_busy(True)
        self._regen_w = _AIRegen(self._analysis, self._creds, self)
        self._regen_w.progress.connect(self._on_progress)
        self._regen_w.finished.connect(lambda r: (self._update_ai(r), self._set_busy(False)))
        self._regen_w.error.connect(self._on_error)
        self._regen_w.start()

    # ── result handlers ───────────────────────────────────────────────────────
    def _on_progress(self, msg: str):
        self._prog_lbl.setText(msg); self._prog_lbl.show()

    def _on_done(self, analysis: dict, fig, ai_result: dict):
        self._analysis = analysis; self._ai_result = ai_result
        self._update_chart(fig)
        self._update_ticker_strip(analysis)
        self._update_summary(analysis)
        self._update_scenarios(analysis)
        self._update_ai(ai_result)
        self._set_busy(False)
        self._btn_tna_buy.setEnabled(bool(self._current_sym))
        self._btn_tna_sell.setEnabled(bool(self._current_sym))
        bars = analysis.get("_df_len", 0)
        self._prog_lbl.setText(f"✅  {analysis.get('symbol','?')} — {bars} bars · report saved")
        self._prog_lbl.show()

    def _on_error(self, msg: str):
        self._set_busy(False)
        self._prog_lbl.setText(f"❌  {msg.splitlines()[0]}")
        self._prog_lbl.show()

    def _set_busy(self, busy: bool):
        self._analyze_btn.setEnabled(not busy)
        self._prog_bar.setVisible(busy)

    def _update_chart(self, fig):
        if not _QTAGG_OK or fig is None: return
        while self._canvas_vl.count():
            item = self._canvas_vl.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        canvas = _FigCanvas(fig)
        self._canvas_vl.addWidget(canvas); canvas.draw(); self._canvas = canvas

    def _update_ticker_strip(self, a: dict):
        sym  = a.get("symbol", "?"); cp = a.get("current_price", 0)
        chg1 = a.get("chg_1bar", 0); dir_ = a.get("trend", {}).get("direction", "?")
        icon = "▲" if chg1 >= 0 else "▼"
        col  = C["green"] if chg1 >= 0 else C["red"]
        self._ticker_strip.setText(f"{sym}   ${cp:,.2f}   {icon} {chg1:+.2f}%   ·   {dir_}")
        self._ticker_strip.setStyleSheet(
            f"background:{C['card']};color:{col};"
            f"padding:5px 10px;border-radius:4px;font-size:10px;font-weight:bold;"
        )

    def _update_summary(self, a: dict):
        td = a.get("trend", {}); ma = a.get("moving_averages", {})
        vd = a.get("volume", {}); sup = a.get("support_levels", [])
        res = a.get("resistance_levels", [])
        self._clear_grp(self._grp_trend)
        self._add_row(self._grp_trend, "Direction", td.get("direction","?"), badge=True)
        self._add_row(self._grp_trend, "Strength",  td.get("strength","?"),  badge=True)
        self._add_row(self._grp_trend, "Duration",  f"{td.get('duration_bars','?')} bars")
        self._add_row(self._grp_trend, "RSI",       f"{td.get('rsi',50):.0f}")
        self._clear_grp(self._grp_ma)
        self._add_row(self._grp_ma, "Alignment", ma.get("alignment","?"), badge=True)
        if ma.get("golden_cross"): self._add_row(self._grp_ma, "🟢 Signal", "Golden Cross")
        if ma.get("death_cross"):  self._add_row(self._grp_ma, "🔴 Signal", "Death Cross")
        for p in [20, 50, 200]:
            m = ma.get(f"ma{p}")
            if m:
                self._add_row(self._grp_ma, f"MA{p}",
                    f"${m['value']:,.2f}  {m['price_relation']}  "
                    f"{m['slope']}  ({m['distance_pct']:+.1f}%)")
        self._clear_grp(self._grp_vol)
        self._add_row(self._grp_vol, "Trend", vd.get("trend","?"), badge=True)
        self._add_row(self._grp_vol, "Confirmation",
                      vd.get("confirmation","?").replace("_"," "), badge=True)
        if vd.get("spike"):
            self._add_row(self._grp_vol, "⚡ Spike", f"{vd.get('spike_ratio',1):.1f}× avg")
        self._clear_grp(self._grp_sr)
        for s in sup[:3]:
            self._add_row(self._grp_sr, f"🟢 Sup {s['distance_pct']:.1f}%",
                          f"${s['price']:,.2f}  ({s['strength']})")
        for r in res[:3]:
            self._add_row(self._grp_sr, f"🔴 Res {r['distance_pct']:.1f}%",
                          f"${r['price']:,.2f}  ({r['strength']})")
        self._obs_text.setPlainText("\n".join(f"• {o}" for o in a.get("key_observations", [])))
        self._ass_text.setPlainText(a.get("assessment", ""))

    def _update_scenarios(self, a: dict):
        while self._scen_vl.count():
            item = self._scen_vl.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for sc in a.get("scenarios", []):
            self._scen_vl.addWidget(_ScenarioCard(sc))
        self._scen_vl.addStretch()
        self._right_tabs.setCurrentIndex(1)

    def _update_ai(self, ai: dict):
        if not ai:
            self._ai_text.setPlainText("AI insights not generated.")
            return
        text = ai.get("combined","") or ai.get("gemini","") or ai.get("raw","")
        self._ai_text.setMarkdown(text) if text else self._ai_text.setPlainText("Empty result.")

    def _export(self, fmt: str):
        if not self._analysis:
            QMessageBox.information(self, "Export", "Run an analysis first."); return
        gen   = ReportGenerator()
        chart = self._analysis.get("_chart_path", "")
        if   fmt == "md":   path = gen.save_markdown(self._analysis, _AN_RPT_DIR, self._ai_result, chart)
        elif fmt == "html": path = gen.save_html(self._analysis, _AN_RPT_DIR, self._ai_result, chart)
        elif fmt == "pdf":  path = gen.save_pdf(self._analysis, _AN_RPT_DIR, self._ai_result, chart)
        elif fmt == "json": path = gen.save_json(self._analysis, _AN_RPT_DIR, self._ai_result)
        else: return
        QMessageBox.information(self, "Export Complete", f"Saved:\n{path}")


# ══════════════════════════════════════════════════════════════════════════════
# 14b.  FUNDANALYZE  ─ US Fundamentals (self-contained, yfinance)
# ══════════════════════════════════════════════════════════════════════════════

class _FundWorker(QThread):
    """Fetch fundamentals for a list of US tickers via yfinance."""
    progress = Signal(int, int, str)
    result   = Signal(list)   # list of row-dicts
    error    = Signal(str)

    def __init__(self, symbols: list, parent=None):
        super().__init__(parent)
        self.symbols = symbols

    def run(self):
        if not _YF_OK:
            self.error.emit("yfinance not installed.\nRun: pip install yfinance")
            return
        rows = []
        total = len(self.symbols)
        for i, sym in enumerate(self.symbols):
            self.progress.emit(i + 1, total, sym)
            try:
                t    = _yf.Ticker(sym)
                info = t.info
                price  = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
                mcap   = (info.get("marketCap") or 0) / 1e9
                pe     = info.get("trailingPE") or info.get("forwardPE")
                peg    = info.get("trailingPegRatio") or info.get("pegRatio")
                roe    = info.get("returnOnEquity")
                roa    = info.get("returnOnAssets")
                de_raw = info.get("debtToEquity")        # yfinance gives this as %, not ratio
                de     = de_raw / 100 if isinstance(de_raw, (int, float)) else None
                bv     = info.get("bookValue") or 0
                eps    = info.get("trailingEps") or 0
                fcf    = info.get("freeCashflow") or 0
                t_assets = info.get("totalAssets") or 1

                # Graham Number
                if isinstance(eps, (int, float)) and eps > 0 and bv > 0:
                    graham = (22.5 * eps * bv) ** 0.5
                    mos    = (graham - price) / graham * 100 if graham > 0 and price > 0 else None
                else:
                    graham = None; mos = None

                # Simplified 5-point F-score from snapshot data
                fscore = 0
                if isinstance(roa, float) and roa > 0:                          fscore += 1
                if fcf > 0:                                                      fscore += 1
                if isinstance(roa, float) and (fcf / t_assets) > roa:           fscore += 1
                if isinstance(de, float) and de < 1.0:                          fscore += 1
                if isinstance(roe, float) and roe > 0.10:                       fscore += 1

                # Composite score
                score = 0
                if isinstance(roe, float) and roe > 0.15:                       score += 25
                if isinstance(pe,  float) and 0 < pe < 25:                      score += 20
                if isinstance(peg, float) and 0 < peg < 1.5:                    score += 20
                if isinstance(de,  float) and de < 1.0:                         score += 15
                if isinstance(mos, float) and mos > 20:                         score += 10
                if fscore >= 4:                                                  score += 10

                sig = ("Strong Buy" if score >= 75 else "Buy" if score >= 55
                       else "Hold" if score >= 40 else "Watch")

                def _pct(v):
                    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "N/A"

                rows.append({
                    "Symbol":  sym,
                    "Company": info.get("shortName", sym)[:24],
                    "MCap_B":  f"{mcap:.2f}" if mcap else "N/A",
                    "PE":      f"{pe:.1f}"   if isinstance(pe,  float) else "N/A",
                    "PEG":     f"{peg:.2f}"  if isinstance(peg, float) else "N/A",
                    "ROE":     _pct(roe),
                    "ROA":     _pct(roa),
                    "DE":      f"{de:.2f}"   if isinstance(de,  float) else "N/A",
                    "Graham":  f"${graham:.2f}" if isinstance(graham, float) else "N/A",
                    "MoS":     f"{mos:.1f}%" if isinstance(mos, float) else "N/A",
                    "FScore":  f"{fscore}/5",
                    "Score":   score,
                    "Signal":  sig,
                    "_roe_r":  roe, "_roa_r": roa, "_pe_r": pe,
                    "_peg_r":  peg, "_de_r":  de,  "_mos_r": mos,
                    "_fsc_r":  fscore, "_price": price, "_graham": graham,
                    "_company_full": info.get("longName", sym),
                    "_sector": info.get("sector", ""),
                    "_industry": info.get("industry", ""),
                })
            except Exception as e:
                rows.append({
                    "Symbol": sym, "Company": "Error", "MCap_B": "N/A",
                    "PE": "N/A", "PEG": "N/A", "ROE": "N/A", "ROA": "N/A",
                    "DE": "N/A", "Graham": "N/A", "MoS": "N/A",
                    "FScore": "N/A", "Score": 0, "Signal": "Error",
                    "_fsc_r": 0, "_roe_r": None, "_roa_r": None,
                    "_pe_r": None, "_peg_r": None, "_de_r": None,
                    "_mos_r": None, "_price": 0, "_graham": None,
                    "_company_full": str(e), "_sector": "", "_industry": "",
                })
        self.result.emit(rows)


class FundamentalAnalysisPanel(QWidget):
    """FUNDANALYZE sub-tab — US fundamentals via yfinance."""

    trade_requested = Signal(str, str)   # (symbol, side)
    send_to_wl      = Signal(list)       # list of symbols → WatchlistPanel

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows:    list = []
        self._symbols: list = []
        self._worker  = None
        self._detail_sym = ""
        self._setup_ui()

    def set_symbols(self, symbols: list):
        self._symbols = symbols

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 16)
        layout.setSpacing(8)

        # ── Toolbar ──────────────────────────────────────────────────────────
        cfg = QHBoxLayout()
        hdr = QLabel("📊  FUNDAMENTAL ANALYZER  —  US Stocks")
        hdr.setStyleSheet(f"font-size:13pt;font-weight:bold;color:{C['accent']};margin-right:16px;")
        cfg.addWidget(hdr)
        cfg.addWidget(QLabel("Filter:"))
        self._filter_cb = QComboBox()
        self._filter_cb.addItems(["All", "Strong Buy", "Buy", "High F-Score (4+)", "MoS > 20%"])
        self._filter_cb.setMinimumWidth(130)
        self._filter_cb.currentIndexChanged.connect(self._apply_filter)
        cfg.addWidget(self._filter_cb)
        self._run_btn = QPushButton("🚀  RUN ANALYSIS")
        self._run_btn.setFixedHeight(34)
        self._run_btn.setStyleSheet(
            f"QPushButton{{background:{C['accent']};color:{C['bg']};font-weight:bold;"
            f"border:none;border-radius:6px;padding:0 14px;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['muted']};}}"
        )
        self._run_btn.clicked.connect(self._run_analysis)
        cfg.addWidget(self._run_btn)
        _btn_wl = QPushButton("📋  SEND to WL")
        _btn_wl.setFixedHeight(34)
        _btn_wl.setStyleSheet(
            f"QPushButton{{background:{C['hover']};color:{C['text']};border:1px solid {C['yellow']};"
            f"border-radius:6px;padding:0 12px;font-size:11px;}}"
            f"QPushButton:hover{{background:{C['card']};}}")
        _btn_wl.setToolTip("Send selected symbol(s) to Watchlist")
        _btn_wl.clicked.connect(self._send_to_wl)
        cfg.addWidget(_btn_wl)
        cfg.addStretch()
        cfg.addWidget(QLabel("Evaluator Presets:"))
        self._eval_preset_cmb = QComboBox()
        self._eval_preset_cmb.setFixedWidth(200)
        self._eval_presets = get_evaluator_presets()
        self._eval_preset_cmb.addItem("— pick sector/index/cap —", None)
        for name in sorted(self._eval_presets.keys()):
            self._eval_preset_cmb.addItem(name, name)
        self._eval_preset_cmb.currentIndexChanged.connect(self._load_evaluator_preset)
        cfg.addWidget(self._eval_preset_cmb)
        layout.addLayout(cfg)

        # ── Progress ─────────────────────────────────────────────────────────
        self._prog_frame = QFrame()
        self._prog_frame.setFixedHeight(36)
        self._prog_frame.hide()
        self._prog_frame.setStyleSheet(f"QFrame{{background:{C['card']};border-radius:4px;}}")
        pfl = QHBoxLayout(self._prog_frame)
        self._status_lbl = QLabel("Initializing…")
        self._status_lbl.setStyleSheet(f"color:{C['sec']};font-size:9pt;")
        self._prog_bar = QProgressBar()
        self._prog_bar.setFixedHeight(8)
        self._prog_bar.setStyleSheet(
            f"QProgressBar{{border:1px solid {C['border']};border-radius:4px;"
            f"background:{C['card']};text-align:center;color:white;}}"
            f"QProgressBar::chunk{{background:{C['accent']};border-radius:3px;}}"
        )
        pfl.addWidget(self._status_lbl); pfl.addWidget(self._prog_bar)
        layout.addWidget(self._prog_frame)

        # ── Splitter: table + insights ────────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)
        _COLS = ["Symbol", "Company", "M-Cap ($B)", "P/E", "PEG",
                 "ROE%", "ROA%", "D/E", "Graham$", "MoS%",
                 "F-Score", "Score", "Signal"]
        self._tbl = QTableWidget(0, len(_COLS))
        self._tbl.setHorizontalHeaderLabels(_COLS)
        self._tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._tbl.horizontalHeader().setStretchLastSection(True)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setStyleSheet(
            f"QTableWidget{{background:{C['card']};alternate-background-color:{C['hover']};"
            f"gridline-color:{C['border']};border:none;color:{C['text']};font-size:11px;}}"
            f"QTableWidget::item{{padding:4px 6px;}}"
            f"QTableWidget::item:selected{{background:{C['hover']};color:{C['accent']};}}"
            f"QHeaderView::section{{background:{C['bg']};color:{C['sec']};border:none;"
            f"border-right:1px solid {C['border']};border-bottom:1px solid {C['border']};"
            f"padding:5px 6px;font-size:10px;font-weight:bold;}}"
        )
        self._tbl.setSortingEnabled(True)
        self._tbl.horizontalHeader().setSortIndicatorShown(True)
        self._tbl.currentCellChanged.connect(
            lambda r, _cc, _pr, _pc: self._on_row_selected(r)
        )
        splitter.addWidget(self._tbl)

        # Insights pane wrapped in a container with Buy/Sell buttons
        _ins_container = QWidget()
        _ins_vl = QVBoxLayout(_ins_container); _ins_vl.setContentsMargins(0, 0, 0, 0); _ins_vl.setSpacing(0)
        # ── Buy / Sell button bar ─────────────────────────────────────────────
        _btn_bar = QFrame(); _btn_bar.setFixedHeight(34)
        _btn_bar.setStyleSheet(f"background:{C['card']};border-bottom:1px solid {C['border']};")
        _bb_lay = QHBoxLayout(_btn_bar); _bb_lay.setContentsMargins(8, 4, 8, 4); _bb_lay.setSpacing(6)
        _bb_lay.addStretch()
        self._btn_fa_buy = QPushButton("🟢  (Dry) Buy")
        self._btn_fa_buy.setFixedHeight(24)
        self._btn_fa_buy.setEnabled(False)
        self._btn_fa_buy.setStyleSheet(
            f"QPushButton{{background:{C['green']};color:{C['bg']};border:none;"
            f"border-radius:4px;padding:2px 10px;font-size:11px;font-weight:600;}}"
            f"QPushButton:hover{{background:#238636;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['sec']};}}")
        self._btn_fa_buy.clicked.connect(
            lambda: self.trade_requested.emit(self._detail_sym, "buy"))
        self._btn_fa_sell = QPushButton("🔴  (Dry) Sell")
        self._btn_fa_sell.setFixedHeight(24)
        self._btn_fa_sell.setEnabled(False)
        self._btn_fa_sell.setStyleSheet(
            f"QPushButton{{background:{C['red']};color:#fff;border:none;"
            f"border-radius:4px;padding:2px 10px;font-size:11px;font-weight:600;}}"
            f"QPushButton:hover{{background:#b91c1c;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['sec']};}}")
        self._btn_fa_sell.clicked.connect(
            lambda: self.trade_requested.emit(self._detail_sym, "sell"))
        _bb_lay.addWidget(self._btn_fa_buy); _bb_lay.addWidget(self._btn_fa_sell)
        _ins_vl.addWidget(_btn_bar)
        # ── Scroll area ───────────────────────────────────────────────────────
        self._ins_scroll = QScrollArea(); self._ins_scroll.setWidgetResizable(True)
        self._ins_scroll.setStyleSheet(
            f"background:{C['card']};border:1px solid {C['border']};border-radius:6px;"
        )
        self._ins_content = QWidget(); self._ins_lay = QVBoxLayout(self._ins_content)
        self._ins_lay.setAlignment(Qt.AlignTop)
        self._ins_lbl = QLabel("Select a stock to view insights.")
        self._ins_lbl.setWordWrap(True)
        self._ins_lbl.setStyleSheet(f"color:{C['sec']};font-size:10pt;padding:10px;")
        self._ins_lay.addWidget(self._ins_lbl)
        self._ins_scroll.setWidget(self._ins_content)
        _ins_vl.addWidget(self._ins_scroll, 1)
        splitter.addWidget(_ins_container)
        splitter.setStretchFactor(0, 7); splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _ci(self, val: str, color: str = None, bold: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(str(val))
        item.setTextAlignment(Qt.AlignCenter)
        if color:  item.setForeground(QColor(color))
        if bold:
            f = item.font(); f.setBold(True); item.setFont(f)
        return item

    def _run_analysis(self):
        if not _YF_OK:
            QMessageBox.warning(self, "Dependency Missing",
                                "yfinance is required.\n\nRun: pip install yfinance")
            return
        if not self._symbols:
            QMessageBox.information(self, "No Symbols",
                                    "Add stocks to your Watchlist first.")
            return
        self._tbl.setRowCount(0)
        self._prog_frame.show()
        self._prog_bar.setRange(0, len(self._symbols))
        self._prog_bar.setValue(0)
        self._run_btn.setEnabled(False)
        self._status_lbl.setText(f"Analyzing {len(self._symbols)} symbols…")
        self._worker = _FundWorker(self._symbols, self)
        self._worker.progress.connect(self._on_prog)
        self._worker.result.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(
            lambda: (self._run_btn.setEnabled(True),
                     self._status_lbl.setText("Analysis complete."))
        )
        self._worker.start()

    def _on_prog(self, cur, total, sym):
        self._prog_bar.setValue(cur)
        self._status_lbl.setText(f"Fetching {sym}… ({cur}/{total})")

    def _on_error(self, msg: str):
        self._run_btn.setEnabled(True); self._prog_frame.hide()
        QMessageBox.warning(self, "Analysis Error", msg)

    def _on_results(self, rows: list):
        self._rows = sorted(rows, key=lambda r: r["Score"], reverse=True)
        self._apply_filter()

    def _apply_filter(self):
        f = self._filter_cb.currentText()
        rows = self._rows
        if f == "Strong Buy":    rows = [r for r in rows if r["Signal"] == "Strong Buy"]
        elif f == "Buy":         rows = [r for r in rows if r["Signal"] in ("Strong Buy","Buy")]
        elif f == "High F-Score (4+)": rows = [r for r in rows if isinstance(r["_fsc_r"],int) and r["_fsc_r"]>=4]
        elif f == "MoS > 20%":   rows = [r for r in rows if isinstance(r["_mos_r"],(int,float)) and r["_mos_r"]>20]
        self._populate_table(rows)

    def _populate_table(self, rows: list):
        self._tbl.setSortingEnabled(False)
        self._tbl.setRowCount(0)
        _SIG_COLORS = {"Strong Buy": C["green"], "Buy": C["accent"],
                       "Hold": C["yellow"], "Watch": C["muted"], "Error": C["red"]}
        for row in rows:
            r = self._tbl.rowCount(); self._tbl.insertRow(r)
            self._tbl.setItem(r, 0,  self._ci(row["Symbol"],  C["accent"], bold=True))
            self._tbl.setItem(r, 1,  self._ci(row["Company"]))
            self._tbl.setItem(r, 2,  self._ci(row["MCap_B"],  C["yellow"]))
            self._tbl.setItem(r, 3,  self._ci(row["PE"]))
            peg_c = C["green"] if isinstance(row["_peg_r"],(int,float)) and 0 < row["_peg_r"] < 1 else None
            self._tbl.setItem(r, 4,  self._ci(row["PEG"], peg_c))
            roe_c = C["green"] if isinstance(row["_roe_r"],(int,float)) and row["_roe_r"] > 0.15 else None
            self._tbl.setItem(r, 5,  self._ci(row["ROE"], roe_c))
            roa_c = C["green"] if isinstance(row["_roa_r"],(int,float)) and row["_roa_r"] > 0.05 else None
            self._tbl.setItem(r, 6,  self._ci(row["ROA"], roa_c))
            de_c  = C["red"]   if isinstance(row["_de_r"], (int,float)) and row["_de_r"] > 1.5 else None
            self._tbl.setItem(r, 7,  self._ci(row["DE"],  de_c))
            self._tbl.setItem(r, 8,  self._ci(row["Graham"]))
            mos_c = C["green"] if isinstance(row["_mos_r"],(int,float)) and row["_mos_r"] > 20 else None
            self._tbl.setItem(r, 9,  self._ci(row["MoS"], mos_c))
            fsc_c = C["green"] if isinstance(row["_fsc_r"],int) and row["_fsc_r"] >= 4 else (
                    C["red"]   if isinstance(row["_fsc_r"],int) and row["_fsc_r"] <= 1 else None)
            self._tbl.setItem(r, 10, self._ci(row["FScore"], fsc_c, bold=True))
            sc = row["Score"]
            sc_c = C["green"] if sc >= 75 else (C["yellow"] if sc >= 55 else C["muted"])
            self._tbl.setItem(r, 11, self._ci(str(sc), sc_c, bold=True))
            sig_c = _SIG_COLORS.get(row["Signal"], C["muted"])
            self._tbl.setItem(r, 12, self._ci(row["Signal"], sig_c, bold=True))
        self._tbl.setSortingEnabled(True)

    def _send_to_wl(self):
        """Emit all currently filtered symbol(s) in the table to Watchlist."""
        try:
            syms = []
            for r in range(self._tbl.rowCount()):
                it = self._tbl.item(r, 0)
                if it and it.text():
                    syms.append(it.text().strip().upper())
            if syms:
                self.send_to_wl.emit(syms)
            else:
                QMessageBox.information(self, "No Symbols", "No symbols currently in the filtered list to send.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to send to watchlist: {e}")

    def _load_evaluator_preset(self, index):
        if index <= 0:
            return
        name = self._eval_preset_cmb.itemData(index)
        if name:
            syms = self._eval_presets.get(name)
            if syms:
                self._symbols = list(syms)
                self._run_analysis()

    def _on_row_selected(self, row: int):
        if row < 0 or not self._rows: return
        sym = self._tbl.item(row, 0)
        if not sym: return
        symbol = sym.text()
        self._detail_sym = symbol
        self._btn_fa_buy.setEnabled(True)
        self._btn_fa_sell.setEnabled(True)
        data = next((r for r in self._rows if r["Symbol"] == symbol), None)
        if data: self._render_insights(data)

    def _render_insights(self, row: dict):
        while self._ins_lay.count():
            child = self._ins_lay.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        hdr = QLabel(f"🔍  {row['Symbol']}  —  {row['_company_full']}")
        hdr.setStyleSheet(f"font-size:11pt;font-weight:bold;color:{C['accent']};margin-bottom:4px;")
        self._ins_lay.addWidget(hdr)
        meta = QLabel(f"{row['_sector']}  {('· ' + row['_industry']) if row['_industry'] else ''}")
        meta.setStyleSheet(f"color:{C['sec']};font-size:9pt;")
        self._ins_lay.addWidget(meta)
        lines = []
        if row.get("_price"):   lines.append(f"Current Price:  ${row['_price']:,.2f}")
        if row.get("_graham"):  lines.append(f"Graham Number:  ${row['_graham']:,.2f}  (MoS {row['MoS']})")
        if isinstance(row.get("_roe_r"),(int,float)):
            lines.append(f"Return on Equity:  {row['_roe_r']*100:.1f}%"
                         + ("  ✅" if row["_roe_r"] > 0.15 else "  ⚠"))
        if isinstance(row.get("_roa_r"),(int,float)):
            lines.append(f"Return on Assets:  {row['_roa_r']*100:.1f}%")
        if isinstance(row.get("_de_r"), (int,float)):
            lines.append(f"Debt / Equity:  {row['_de_r']:.2f}x"
                         + ("  ⚠ HIGH" if row["_de_r"] > 1.5 else ""))
        if isinstance(row.get("_pe_r"), (int,float)):
            lines.append(f"P/E Ratio:  {row['_pe_r']:.1f}"
                         + ("  ✅ Reasonable" if row["_pe_r"] < 25 else "  ⚠ Elevated"))
        if isinstance(row.get("_peg_r"),(int,float)):
            lines.append(f"PEG Ratio:  {row['_peg_r']:.2f}"
                         + ("  ✅ Undervalued" if row["_peg_r"] < 1 else ""))
        lbl = QLabel("\n".join(lines))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{C['text']};font-size:10pt;line-height:160%;margin-top:8px;")
        self._ins_lay.addWidget(lbl)
        score_row = QHBoxLayout()
        score_lbl = QLabel(f"Composite Score:  {row['Score']}")
        score_lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
        col = C["green"] if row["Score"] >= 75 else (C["yellow"] if row["Score"] >= 55 else C["muted"])
        score_lbl.setStyleSheet(f"color:{col};margin-top:8px;")
        score_row.addWidget(score_lbl); score_row.addStretch()
        sig_lbl = QLabel(row["Signal"])
        sig_lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
        sig_c = {
            "Strong Buy": C["green"], "Buy": C["accent"],
            "Hold": C["yellow"]
        }.get(row["Signal"], C["muted"])
        sig_lbl.setStyleSheet(f"color:{sig_c};margin-top:8px;")
        score_row.addWidget(sig_lbl)
        cw = QWidget(); cw.setLayout(score_row)
        self._ins_lay.addWidget(cw)
        self._ins_lay.addStretch()


# ══════════════════════════════════════════════════════════════════════════════
# 14c.  CANSLIMPRO  ─ O'Neil Screening (US only, external CANSLIMPRO engine)
# ══════════════════════════════════════════════════════════════════════════════

def _cs_score_color(score: float) -> str:
    if score >= 90: return "#f5a623"
    if score >= 80: return "#1d9e75"
    if score >= 70: return "#185fa5"
    if score >= 60: return "#534ab7"
    if score >= 50: return "#8e8e93"
    return "#993c1d"


def _cs_comp_color(score: int) -> str:
    if score >= 80: return "#f5a623"
    if score >= 60: return "#1d9e75"
    if score >= 40: return "#185fa5"
    return "#993c1d"


class _CSComponentRow(QFrame):
    def __init__(self, key: str, cr, parent=None):
        super().__init__(parent)
        color = _cs_comp_color(cr.score)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4); layout.setSpacing(2)
        hdr = QHBoxLayout()
        key_lbl = QLabel(f"<b>{key}</b>")
        key_lbl.setStyleSheet(f"color:{color};font-size:13px;min-width:16px;")
        hdr.addWidget(key_lbl)
        name_lbl = QLabel(cr.label)
        name_lbl.setStyleSheet(f"color:{C['text']};font-size:10px;")
        hdr.addWidget(name_lbl, 1)
        wt_lbl = QLabel(f"{int(cr.weight * 100)}%")
        wt_lbl.setStyleSheet(f"color:{C['muted']};font-size:9px;")
        hdr.addWidget(wt_lbl)
        sc_lbl = QLabel(f"<b>{cr.score}</b>")
        sc_lbl.setStyleSheet(f"color:{color};font-size:13px;min-width:28px;")
        sc_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hdr.addWidget(sc_lbl); layout.addLayout(hdr)
        bar = QProgressBar(); bar.setRange(0, 100); bar.setValue(cr.score)
        bar.setFixedHeight(5); bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar{{background:{C['hover']};border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{color};border-radius:2px;}}"
        )
        layout.addWidget(bar)
        metric_lbl = QLabel(cr.key_metric)
        metric_lbl.setStyleSheet(f"color:{color};font-family:Consolas,monospace;font-size:9px;")
        layout.addWidget(metric_lbl)
        rat_lbl = QLabel(cr.rationale)
        rat_lbl.setStyleSheet(f"color:{C['sec']};font-size:9px;"); rat_lbl.setWordWrap(True)
        layout.addWidget(rat_lbl)
        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{C['border']}"); layout.addWidget(sep)


class _CSDetailPanel(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(f"background:{C['card']};border:none;")
        self._container = QWidget()
        self._layout    = QVBoxLayout(self._container)
        self._layout.setContentsMargins(8, 8, 8, 8); self._layout.setSpacing(3)
        self.setWidget(self._container)
        self._show_placeholder()

    def _clear(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _show_placeholder(self):
        self._clear()
        lbl = QLabel("Select a result\nto view detail")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{C['muted']};font-size:13px;")
        self._layout.addWidget(lbl); self._layout.addStretch()

    def load(self, result):
        self._clear()
        sc    = result.composite_score
        color = _cs_score_color(sc)
        tkr = QLabel(f"<b>{result.ticker}</b>")
        tkr.setStyleSheet(f"color:{C['text']};font-size:17px;"); self._layout.addWidget(tkr)
        co  = QLabel(result.company_name)
        co.setStyleSheet(f"color:{C['sec']};font-size:11px;"); co.setWordWrap(True)
        self._layout.addWidget(co)
        sc_lbl = QLabel(f"<b>{sc:.1f}</b>")
        sc_lbl.setStyleSheet(f"color:{color};font-size:30px;"); self._layout.addWidget(sc_lbl)
        rt = QLabel(result.rating)
        rt.setStyleSheet(f"color:{color};font-size:11px;font-weight:bold;"); self._layout.addWidget(rt)
        sc_bar = QProgressBar(); sc_bar.setRange(0, 100); sc_bar.setValue(int(sc))
        sc_bar.setFixedHeight(7); sc_bar.setTextVisible(False)
        sc_bar.setStyleSheet(
            f"QProgressBar{{background:{C['hover']};border:none;border-radius:3px;}}"
            f"QProgressBar::chunk{{background:{color};border-radius:3px;}}"
        )
        self._layout.addWidget(sc_bar)
        meta_lbl = QLabel(f"Sector: {result.sector or '—'}  ·  Quality: {result.data_quality}")
        meta_lbl.setStyleSheet(f"color:{C['sec']};font-size:10px;"); meta_lbl.setWordWrap(True)
        self._layout.addWidget(meta_lbl)
        buy_lbl = QLabel("✓  BUY CANDIDATE" if result.buy_candidate else "—  Watch Only")
        buy_lbl.setStyleSheet(
            f"color:{C['green']};font-weight:bold;font-size:11px;" if result.buy_candidate
            else f"color:{C['sec']};font-size:11px;"
        )
        self._layout.addWidget(buy_lbl)
        _s = QFrame(); _s.setFrameShape(QFrame.HLine)
        _s.setStyleSheet(f"color:{C['border']};margin:4px 0;"); self._layout.addWidget(_s)
        comp_hdr = QLabel("COMPONENT BREAKDOWN")
        comp_hdr.setStyleSheet(f"color:{C['sec']};font-size:9px;font-weight:bold;letter-spacing:1px;")
        self._layout.addWidget(comp_hdr)
        for key in _CS_COMP_KEYS:
            cr = result.components.get(key)
            if cr: self._layout.addWidget(_CSComponentRow(key, cr))
        self._layout.addStretch()


class _CSSettingsPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:{C['hover']};border:1px solid {C['border']};border-radius:6px;}}"
            f"QLabel{{color:{C['text']};font-size:11px;background:transparent;border:none;}}"
            f"QLineEdit,QSpinBox,QDoubleSpinBox{{background:{C['card']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;padding:4px 6px;font-size:11px;}}"
            f"QCheckBox{{color:{C['text']};font-size:11px;}}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(5)

        def _sec(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color:{C['muted']};font-size:9px;font-weight:bold;"
                               "letter-spacing:1px;background:transparent;border:none;")
            layout.addWidget(lbl)

        _sec("API KEYS  (auto-loaded)")
        self._fmp_edit = QLineEdit(); self._fmp_edit.setPlaceholderText("FMP API Key")
        self._fmp_edit.setEchoMode(QLineEdit.Password); layout.addWidget(self._fmp_edit)
        self._alp_id   = QLineEdit(); self._alp_id.setPlaceholderText("Alpaca Key ID")
        self._alp_id.setEchoMode(QLineEdit.Password)
        self._alp_id.setText(API_KEY); layout.addWidget(self._alp_id)
        self._alp_sec  = QLineEdit(); self._alp_sec.setPlaceholderText("Alpaca Secret")
        self._alp_sec.setEchoMode(QLineEdit.Password)
        self._alp_sec.setText(API_SECRET); layout.addWidget(self._alp_sec)

        _sec("SCREENING")
        row = QHBoxLayout()
        row.addWidget(QLabel("Min Score:"))
        self._min_score = QSpinBox(); self._min_score.setRange(0, 100)
        self._min_score.setValue(60); self._min_score.setFixedWidth(56)
        row.addWidget(self._min_score); row.addSpacing(8)
        row.addWidget(QLabel("Delay (s):"))
        self._delay = QDoubleSpinBox(); self._delay.setRange(0.1, 5.0)
        self._delay.setSingleStep(0.1); self._delay.setValue(0.5); self._delay.setFixedWidth(58)
        row.addWidget(self._delay); row.addStretch(); layout.addLayout(row)

        row2 = QHBoxLayout(); row2.addWidget(QLabel("Cache TTL (h):"))
        self._ttl = QDoubleSpinBox(); self._ttl.setRange(0.5, 48.0)
        self._ttl.setSingleStep(0.5); self._ttl.setValue(12.0); self._ttl.setFixedWidth(60)
        row2.addWidget(self._ttl); row2.addStretch(); layout.addLayout(row2)

        self._buy_only_cb  = QCheckBox("Buy Candidates Only"); layout.addWidget(self._buy_only_cb)
        self._use_cache_cb = QCheckBox("Use Cache"); self._use_cache_cb.setChecked(True)
        layout.addWidget(self._use_cache_cb)

        # Load FMP key if present
        fmp_p = os.path.join(_ALP_DIR, "ALLAPI", "FMP_API_KEY.txt")
        if os.path.exists(fmp_p):
            try: self._fmp_edit.setText(open(fmp_p).read().strip())
            except Exception: pass

    def get_settings(self) -> dict:
        return {
            "fmp_key":       self._fmp_edit.text().strip(),
            "alpaca_key_id": self._alp_id.text().strip(),
            "alpaca_secret": self._alp_sec.text().strip(),
            "min_score":     self._min_score.value(),
            "delay":         self._delay.value(),
            "cache_ttl":     self._ttl.value(),
            "buy_only":      self._buy_only_cb.isChecked(),
            "use_cache":     self._use_cache_cb.isChecked(),
        }


class _CSWorker(QThread):
    progress     = Signal(int, int, str)
    log_message  = Signal(str)
    finished_all = Signal(list)
    error_signal = Signal(str)

    def __init__(self, tickers: list, settings: dict, parent=None):
        super().__init__(parent)
        self.tickers  = tickers
        self.settings = settings
        self._abort   = False

    def abort(self): self._abort = True

    def run(self):
        if not _CS_OK:
            self.error_signal.emit(f"CANSLIM engine unavailable:\n{_CS_ERR}"); return
        results = []; s = self.settings
        total   = len(self.tickers)
        for i, ticker in enumerate(self.tickers):
            if self._abort:
                self.log_message.emit("⚠  Aborted."); break
            self.progress.emit(i + 1, total, ticker)
            self.log_message.emit(f"[{i+1}/{total}] Fetching {ticker}…")
            try:
                sd = _cs_fetch(
                    ticker,
                    fmp_api_key=s.get("fmp_key",""),
                    alpaca_key_id=s.get("alpaca_key_id",""),
                    alpaca_secret=s.get("alpaca_secret",""),
                    delay=float(s.get("delay", 0.5)),
                    use_cache=bool(s.get("use_cache", True)),
                    cache_ttl_hrs=float(s.get("cache_ttl", 12.0)),
                )
                if not sd.valid:
                    self.log_message.emit("  ⚠  No price data — skipping"); continue
                if sd.from_cache:
                    self.log_message.emit(f"  📦 Cache hit ({sd.cached_at})")
                result = _cs_score(sd)
                if result.composite_score < float(s.get("min_score", 60)):
                    self.log_message.emit(
                        f"  ✗  Score {result.composite_score:.1f} < {s['min_score']} — filtered"
                    ); continue
                if bool(s.get("buy_only")) and not result.buy_candidate:
                    self.log_message.emit("  ✗  Not a buy candidate — filtered"); continue
                results.append(result)
                flag = "✓ BUY" if result.buy_candidate else "     "
                self.log_message.emit(
                    f"  ✅ {ticker:12s}  {result.composite_score:.1f}  {result.rating:16s}  {flag}"
                )
            except Exception as exc:
                self.log_message.emit(f"  ❌ {ticker}: {exc}")
        results.sort(key=lambda r: r.composite_score, reverse=True)
        self.log_message.emit(f"\n✅ Done — {len(results)} candidate(s).")
        self.finished_all.emit(results)


class CANSLIMProPanel(QWidget):
    """CANSLIMPRO sub-tab — US stocks, external CANSLIMPRO engine."""

    trade_requested = Signal(str, str)   # (symbol, side)
    send_to_wl      = Signal(list)       # list of symbols → WatchlistPanel

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results:  list = []
        self._worker  = None
        self._symbols: list = []
        self._detail_sym = ""
        if not _CS_OK:
            self._build_error_ui()
        else:
            self._build_ui()

    def set_symbols(self, symbols: list):
        self._symbols = symbols
        if _CS_OK:
            self._symbol_list.clear()
            for sym in symbols:
                item = QListWidgetItem(sym)
                item.setCheckState(Qt.Checked)
                self._symbol_list.addItem(item)
            self._sym_count_lbl.setText(f"{len(symbols)} symbol(s)")

    def _build_error_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24)
        lbl = QLabel(
            f"⚠  CANSLIM engine not found.\n\n"
            f"Error: {_CS_ERR}\n\n"
            f"Expected project at:\n  {_CS_ENGINE_DIR}\n\n"
            f"Clone the CANSLIMPRO project there to enable this tab."
        )
        lbl.setStyleSheet(f"color:{C['yellow']};font-size:12px;"); lbl.setWordWrap(True)
        layout.addWidget(lbl); layout.addStretch()

    def _build_ui(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._build_toolbar())
        splitter = QSplitter(Qt.Horizontal); splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_center())
        splitter.addWidget(self._build_right())
        splitter.setSizes([265, 580, 370]); root.addWidget(splitter, 1)
        sb = QHBoxLayout(); sb.setContentsMargins(10, 3, 10, 3)
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setStyleSheet(f"color:{C['muted']};font-size:10px;")
        sb.addWidget(self._status_lbl, 1)
        self._inline_prog = QProgressBar(); self._inline_prog.setFixedSize(160, 4)
        self._inline_prog.setTextVisible(False)
        self._inline_prog.setStyleSheet(
            f"QProgressBar{{background:{C['hover']};border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:#f5a623;border-radius:2px;}}"
        )
        self._inline_prog.setVisible(False); sb.addWidget(self._inline_prog)
        root.addLayout(sb)

    def _build_toolbar(self) -> QFrame:
        frame = QFrame(); frame.setFixedHeight(48)
        frame.setStyleSheet(f"background:{C['hover']};border-bottom:1px solid {C['border']};")
        layout = QHBoxLayout(frame); layout.setContentsMargins(12, 4, 12, 4); layout.setSpacing(8)
        title = QLabel("CANSLIM PRO")
        title.setStyleSheet("color:#f5a623;font-size:14px;font-weight:bold;letter-spacing:1px;")
        layout.addWidget(title)
        sub = QLabel("O'Neil Seven-Factor Screening Engine — US Stocks")
        sub.setStyleSheet(f"color:{C['sec']};font-size:11px;"); layout.addWidget(sub)
        layout.addStretch()
        layout.addWidget(QLabel("Evaluator Presets:"))
        self._eval_preset_cmb = QComboBox()
        self._eval_preset_cmb.setFixedWidth(200)
        self._eval_presets = get_evaluator_presets()
        self._eval_preset_cmb.addItem("— pick sector/index/cap —", None)
        for name in sorted(self._eval_presets.keys()):
            self._eval_preset_cmb.addItem(name, name)
        self._eval_preset_cmb.currentIndexChanged.connect(self._load_evaluator_preset)
        layout.addWidget(self._eval_preset_cmb)
        layout.addWidget(QLabel("Watchlist:"))
        self._wl_display = QLabel("—")
        self._wl_display.setStyleSheet(f"color:{C['accent']};font-size:11px;")
        layout.addWidget(self._wl_display)
        self._run_btn = QPushButton("▶  Run CANSLIM")
        self._run_btn.setFixedHeight(32)
        self._run_btn.setStyleSheet(
            "QPushButton{background:#f5a623;color:#1c1c1e;border:none;"
            "border-radius:5px;font-weight:bold;font-size:12px;padding:4px 18px;}"
            "QPushButton:hover{background:#e09010;}"
            "QPushButton:disabled{background:#3a3a3c;color:#555;}"
        )
        self._run_btn.clicked.connect(self._run_screening); layout.addWidget(self._run_btn)
        self._abort_btn = QPushButton("■  Stop"); self._abort_btn.setFixedHeight(32)
        self._abort_btn.setStyleSheet(
            f"QPushButton{{color:{C['red']};border:1px solid #993c1d;border-radius:5px;"
            f"font-size:12px;padding:4px 14px;background:{C['hover']};}}"
        )
        self._abort_btn.setVisible(False)
        self._abort_btn.clicked.connect(self._abort_screening); layout.addWidget(self._abort_btn)
        return frame

    def _build_left(self) -> QWidget:
        panel = QWidget(); layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(6)
        sym_hdr = QLabel("SYMBOLS")
        sym_hdr.setStyleSheet(f"color:{C['muted']};font-size:9px;font-weight:bold;letter-spacing:1px;")
        layout.addWidget(sym_hdr)
        self._symbol_list = QListWidget(); self._symbol_list.setAlternatingRowColors(True)
        self._symbol_list.setStyleSheet(
            f"QListWidget{{background:{C['hover']};alternate-background-color:{C['card']};"
            f"border:1px solid {C['border']};border-radius:4px;font-size:12px;color:{C['text']};}}"
            f"QListWidget::item{{padding:3px 8px;}}"
            f"QListWidget::item:selected{{background:{C['card']};color:#f5a623;}}"
        )
        layout.addWidget(self._symbol_list, 1)
        sym_row = QHBoxLayout()
        self._sym_count_lbl = QLabel("0 symbols")
        self._sym_count_lbl.setStyleSheet(f"color:{C['muted']};font-size:10px;")
        sym_row.addWidget(self._sym_count_lbl, 1)
        for txt, state in [("All", Qt.Checked), ("None", Qt.Unchecked)]:
            btn = QPushButton(txt); btn.setFixedSize(36, 20)
            btn.setStyleSheet(f"font-size:9px;padding:1px 4px;"
                               f"background:{C['hover']};color:{C['text']};"
                               f"border:1px solid {C['border']};border-radius:3px;")
            st = state
            btn.clicked.connect(lambda _, s=st: [
                self._symbol_list.item(i).setCheckState(s)
                for i in range(self._symbol_list.count())
            ])
            sym_row.addWidget(btn)
        layout.addLayout(sym_row)
        self._settings_panel = _CSSettingsPanel(); layout.addWidget(self._settings_panel)
        cache_row = QHBoxLayout()
        self._cache_info_lbl = QLabel("")
        self._cache_info_lbl.setStyleSheet(f"color:{C['muted']};font-size:9px;")
        cache_row.addWidget(self._cache_info_lbl, 1)
        clr_btn = QPushButton("Clear Cache"); clr_btn.setFixedHeight(22)
        clr_btn.setStyleSheet(f"font-size:9px;padding:2px 7px;"
                               f"background:{C['hover']};color:{C['muted']};"
                               f"border:1px solid {C['border']};border-radius:3px;")
        clr_btn.clicked.connect(self._clear_cache); cache_row.addWidget(clr_btn)
        layout.addLayout(cache_row)
        self._update_cache_info(); return panel

    def _build_center(self) -> QWidget:
        panel = QWidget(); outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        vsplit = QSplitter(Qt.Vertical); vsplit.setChildrenCollapsible(False)
        tbl_w = QWidget(); tbl_l = QVBoxLayout(tbl_w)
        tbl_l.setContentsMargins(0, 0, 0, 0); tbl_l.setSpacing(0)
        self._result_table = QTableWidget(0, len(_CS_COLS))
        self._result_table.setHorizontalHeaderLabels(_CS_COLS)
        self._result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._result_table.horizontalHeader().setStretchLastSection(True)
        self._result_table.setAlternatingRowColors(True)
        self._result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.setStyleSheet(
            f"QTableWidget{{background:{C['hover']};alternate-background-color:{C['card']};"
            f"gridline-color:{C['border']};border:none;color:{C['text']};font-size:12px;}}"
            f"QTableWidget::item{{padding:4px 6px;}}"
            f"QTableWidget::item:selected{{background:{C['card']};color:#f5a623;}}"
            f"QHeaderView::section{{background:{C['bg']};color:{C['sec']};border:none;"
            f"border-right:1px solid {C['border']};border-bottom:1px solid {C['border']};"
            f"padding:5px 6px;font-size:10px;font-weight:bold;}}"
        )
        self._result_table.setSortingEnabled(True)
        self._result_table.horizontalHeader().setSortIndicatorShown(True)
        self._result_table.currentCellChanged.connect(
            lambda r, _cc, _pr, _pc: self._on_row_changed(r)
        )
        tbl_l.addWidget(self._result_table)
        act = QHBoxLayout(); act.setContentsMargins(8, 4, 8, 4)
        self._res_count_lbl = QLabel("No results")
        self._res_count_lbl.setStyleSheet(f"color:{C['muted']};font-size:10px;")
        act.addWidget(self._res_count_lbl, 1)
        csv_btn = QPushButton("Export CSV"); csv_btn.setFixedHeight(24)
        csv_btn.setStyleSheet(f"font-size:10px;padding:2px 10px;background:{C['hover']};"
                               f"color:{C['text']};border:1px solid {C['border']};border-radius:4px;")
        csv_btn.clicked.connect(self._export_csv); act.addWidget(csv_btn)
        wl_btn = QPushButton("📋  SEND to WL"); wl_btn.setFixedHeight(24)
        wl_btn.setStyleSheet(f"font-size:10px;padding:2px 10px;background:{C['hover']};"
                              f"color:{C['text']};border:1px solid {C['yellow']};border-radius:4px;")
        wl_btn.setToolTip("Send selected symbol(s) to Watchlist")
        wl_btn.clicked.connect(self._send_to_wl); act.addWidget(wl_btn)
        tbl_l.addLayout(act); vsplit.addWidget(tbl_w)
        log_w = QWidget(); log_l = QVBoxLayout(log_w)
        log_l.setContentsMargins(6, 4, 6, 4); log_l.setSpacing(2)
        log_hdr = QHBoxLayout()
        log_hdr.addWidget(QLabel("LOG")); log_hdr.addStretch()
        clr_log = QPushButton("Clear"); clr_log.setFixedHeight(18)
        clr_log.setStyleSheet(f"font-size:9px;padding:1px 6px;background:{C['hover']};"
                               f"color:{C['muted']};border:1px solid {C['border']};border-radius:3px;")
        clr_log.clicked.connect(lambda: self._log_edit.clear()); log_hdr.addWidget(clr_log)
        log_l.addLayout(log_hdr)
        self._log_edit = QTextEdit(); self._log_edit.setReadOnly(True)
        self._log_edit.setFont(QFont("Consolas", 9))
        self._log_edit.setStyleSheet(f"background:{C['card']};color:{C['sec']};border:none;border-radius:4px;")
        log_l.addWidget(self._log_edit); vsplit.addWidget(log_w)
        vsplit.setSizes([380, 160]); outer.addWidget(vsplit); return panel

    def _build_right(self) -> QWidget:
        panel = QWidget(); layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        hdr = QFrame(); hdr.setFixedHeight(34)
        hdr.setStyleSheet(f"background:{C['hover']};border-bottom:1px solid {C['border']};")
        hdr_row = QHBoxLayout(hdr); hdr_row.setContentsMargins(10, 0, 6, 0); hdr_row.setSpacing(6)
        lbl = QLabel("DETAIL")
        lbl.setStyleSheet(f"color:{C['muted']};font-size:9px;font-weight:bold;letter-spacing:1px;")
        hdr_row.addWidget(lbl)
        hdr_row.addStretch()
        self._btn_cs_buy = QPushButton("🟢  (Dry) Buy")
        self._btn_cs_buy.setFixedHeight(22)
        self._btn_cs_buy.setEnabled(False)
        self._btn_cs_buy.setStyleSheet(
            f"QPushButton{{background:{C['green']};color:{C['bg']};border:none;"
            f"border-radius:4px;padding:1px 8px;font-size:10px;font-weight:600;}}"
            f"QPushButton:hover{{background:#238636;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['sec']};}}")
        self._btn_cs_buy.clicked.connect(
            lambda: self.trade_requested.emit(self._detail_sym, "buy"))
        self._btn_cs_sell = QPushButton("🔴  (Dry) Sell")
        self._btn_cs_sell.setFixedHeight(22)
        self._btn_cs_sell.setEnabled(False)
        self._btn_cs_sell.setStyleSheet(
            f"QPushButton{{background:{C['red']};color:#fff;border:none;"
            f"border-radius:4px;padding:1px 8px;font-size:10px;font-weight:600;}}"
            f"QPushButton:hover{{background:#b91c1c;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['sec']};}}")
        self._btn_cs_sell.clicked.connect(
            lambda: self.trade_requested.emit(self._detail_sym, "sell"))
        hdr_row.addWidget(self._btn_cs_buy); hdr_row.addWidget(self._btn_cs_sell)
        layout.addWidget(hdr)
        self._detail_panel = _CSDetailPanel(); layout.addWidget(self._detail_panel, 1)
        return panel

    def _get_selected_tickers(self) -> list:
        return [
            self._symbol_list.item(i).text()
            for i in range(self._symbol_list.count())
            if self._symbol_list.item(i).checkState() == Qt.Checked
        ]

    def _run_screening(self):
        if self._worker and self._worker.isRunning(): return
        tickers = self._get_selected_tickers()
        if not tickers:
            QMessageBox.warning(self, "No Symbols",
                                "Tick at least one symbol in the list."); return
        self._results.clear()
        self._result_table.setSortingEnabled(False); self._result_table.setRowCount(0)
        self._detail_sym = ""; self._btn_cs_buy.setEnabled(False); self._btn_cs_sell.setEnabled(False)
        self._detail_panel._show_placeholder(); self._log_edit.clear()
        self._res_count_lbl.setText("Screening…"); self._run_btn.setVisible(False)
        self._abort_btn.setVisible(True)
        self._inline_prog.setRange(0, len(tickers)); self._inline_prog.setValue(0)
        self._inline_prog.setVisible(True)
        self._worker = _CSWorker(tickers, self._settings_panel.get_settings(), self)
        self._worker.progress.connect(self._on_cs_progress)
        self._worker.log_message.connect(self._on_cs_log)
        self._worker.finished_all.connect(self._on_cs_finished)
        self._worker.error_signal.connect(self._on_cs_error)
        self._worker.start()

    def _abort_screening(self):
        if self._worker: self._worker.abort()
        self._abort_btn.setVisible(False); self._run_btn.setVisible(True)
        self._inline_prog.setVisible(False)

    def _on_cs_progress(self, cur: int, total: int, ticker: str):
        self._inline_prog.setRange(0, total); self._inline_prog.setValue(cur)
        self._status_lbl.setText(f"Analyzing {ticker}  ({cur}/{total})")

    def _on_cs_log(self, msg: str):
        self._log_edit.append(msg)
        sb = self._log_edit.verticalScrollBar(); sb.setValue(sb.maximum())

    def _on_cs_finished(self, results: list):
        self._results = results; self._populate_cs_table(results)
        n_buy = sum(1 for r in results if r.buy_candidate)
        self._res_count_lbl.setText(f"{len(results)} result(s)  ·  {n_buy} buy candidate(s)")
        self._status_lbl.setText(f"Done — {len(results)} result(s), {n_buy} buy")
        self._run_btn.setVisible(True); self._abort_btn.setVisible(False)
        self._inline_prog.setVisible(False); self._update_cache_info()

    def _on_cs_error(self, msg: str):
        self._log_edit.append(f"❌ ENGINE ERROR:\n{msg}"); self._status_lbl.setText("Error — see log")
        self._run_btn.setVisible(True); self._abort_btn.setVisible(False)
        self._inline_prog.setVisible(False)

    def _populate_cs_table(self, results: list):
        self._result_table.setSortingEnabled(False)
        self._result_table.setRowCount(0); self._result_table.setRowCount(len(results))
        for row, r in enumerate(results):
            sc    = r.composite_score; color = _cs_score_color(sc); comp = r.components
            vals = [
                str(row + 1), r.ticker,
                r.company_name[:26] + ("…" if len(r.company_name) > 26 else ""),
                f"{sc:.1f}", r.rating,
                *[str(comp[k].score) if k in comp else "—" for k in _CS_COMP_KEYS],
                "✓" if r.buy_candidate else "—", r.data_quality,
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val); item.setTextAlignment(Qt.AlignCenter)
                if col == 2: item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                elif col == 3: item.setForeground(QColor(color)); item.setFont(QFont("",  -1, QFont.Bold))
                elif col == 12 and val == "✓": item.setForeground(QColor(C["green"])); item.setFont(QFont("", -1, QFont.Bold))
                elif 5 <= col <= 11 and val != "—":
                    try: item.setForeground(QColor(_cs_comp_color(int(val))))
                    except ValueError: pass
                self._result_table.setItem(row, col, item)
            # Store result object so detail lookup works correctly after sorting
            self._result_table.item(row, 1).setData(Qt.UserRole, r)
        self._result_table.resizeColumnsToContents()
        self._result_table.setSortingEnabled(True)

    def _send_to_wl(self):
        """Emit selected row ticker(s) to Watchlist."""
        try:
            rows = self._result_table.selectionModel().selectedRows()
            syms = []
            for idx in rows:
                it = self._result_table.item(idx.row(), 1)
                if it and it.text():
                    syms.append(it.text().strip().upper())
            if not syms:
                r = self._result_table.currentRow()
                if r >= 0:
                    it = self._result_table.item(r, 1)
                    if it and it.text():
                        syms = [it.text().strip().upper()]
            if not syms:
                # Fallback: send all visible symbols in the table
                for r in range(self._result_table.rowCount()):
                    it = self._result_table.item(r, 1)
                    if it and it.text():
                        syms.append(it.text().strip().upper())
            if syms:
                self.send_to_wl.emit(syms)
            else:
                QMessageBox.information(self, "No Symbols", "No symbols available to send.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to send to watchlist: {e}")

    def _load_evaluator_preset(self, index):
        if index <= 0:
            return
        name = self._eval_preset_cmb.itemData(index)
        if name:
            syms = self._eval_presets.get(name)
            if syms:
                self.set_symbols(syms)
                # Automatically run the screening
                self._run_screening()

    def _on_row_changed(self, row: int):
        if row < 0: return
        it = self._result_table.item(row, 1)  # ticker col holds UserRole result
        if not it: return
        r = it.data(Qt.UserRole)
        if r:
            self._detail_sym = r.ticker
            self._btn_cs_buy.setEnabled(True)
            self._btn_cs_sell.setEnabled(True)
            self._detail_panel.load(r)

    def _export_csv(self):
        if not self._results:
            QMessageBox.information(self, "No Results", "Run the screener first."); return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", os.path.join(_AN_CRP_DIR, f"canslim_{ts}.csv"), "CSV (*.csv)")
        if not path: return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(_CS_COLS)
                for i, r in enumerate(self._results, 1):
                    comp = r.components
                    w.writerow([
                        i, r.ticker, r.company_name, f"{r.composite_score:.1f}", r.rating,
                        *[comp[k].score if k in comp else "" for k in _CS_COMP_KEYS],
                        "Yes" if r.buy_candidate else "No", r.data_quality,
                    ])
            self._status_lbl.setText(f"CSV saved: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _clear_cache(self):
        try:
            _cs_cache_mod.clear_all(); self._update_cache_info()
            self._status_lbl.setText("Cache cleared.")
        except Exception as e:
            self._status_lbl.setText(f"Cache error: {e}")

    def _update_cache_info(self):
        try:
            st = _cs_cache_mod.stats()
            self._cache_info_lbl.setText(f"Cache: {st['count']} items · {st['size_mb']} MB")
        except Exception:
            self._cache_info_lbl.setText("Cache: —")


# ══════════════════════════════════════════════════════════════════════════════
# 14d.  ANALYST PANEL  ─ container (3 subtabs)
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# 14d.  TECH ANALYST  —  Weekly chart + technical analysis (US, yfinance)
# ══════════════════════════════════════════════════════════════════════════════
# Ported from StockMate / TechnicalAnalystWidget.
# US-only; self-contained: yfinance for data, inline SMA/S&R computation.
# ══════════════════════════════════════════════════════════════════════════════

_TA2_RPT_DIR = os.path.join(_AN_RPT_DIR, "tech_analyst")
os.makedirs(_TA2_RPT_DIR, exist_ok=True)


# ── Helper: compute indicators inline ────────────────────────────────────────

def _sma(series, period: int):
    """pandas rolling SMA, requires _PD_OK."""
    return series.rolling(period, min_periods=1).mean()


def _find_sr(df, n_pivots: int = 5, window: int = 10):
    """Identify support/resistance from local highs and lows."""
    if not _PD_OK:
        return {"support": [], "resistance": []}
    highs = df["high"].rolling(window, center=True).max()
    lows  = df["low"].rolling(window, center=True).min()
    current = float(df["close"].iloc[-1])

    resist, support = [], []
    seen_r, seen_s = [], []

    for i in range(len(df) - 1, -1, -1):
        h = float(highs.iloc[i])
        l = float(lows.iloc[i])
        if h > current and all(abs(h - x) / current > 0.015 for x in seen_r):
            resist.append({"price": round(h, 4)})
            seen_r.append(h)
        if l < current and all(abs(l - x) / current > 0.015 for x in seen_s):
            support.append({"price": round(l, 4)})
            seen_s.append(l)
        if len(resist) >= n_pivots and len(support) >= n_pivots:
            break

    return {"support": support[:n_pivots], "resistance": resist[:n_pivots]}


def _detect_trend(df):
    """Return trend dict from SMA alignment."""
    if not _PD_OK or len(df) < 5:
        return {"direction": "UNKNOWN", "strength": "UNKNOWN", "ma_alignment": "UNKNOWN"}
    close = df["close"]
    price = float(close.iloc[-1])
    sma20  = float(_sma(close, 20).iloc[-1])
    sma50  = float(_sma(close, 50).iloc[-1]) if len(df) >= 50 else sma20
    sma200 = float(_sma(close, 200).iloc[-1]) if len(df) >= 200 else sma50

    above20  = price > sma20
    above50  = price > sma50
    above200 = price > sma200
    aligned  = sma20 > sma50 > sma200

    if above20 and above50 and above200 and aligned:
        return {"direction": "UPTREND",    "strength": "STRONG",   "ma_alignment": "BULLISH"}
    elif above50 and above200:
        return {"direction": "UPTREND",    "strength": "MODERATE", "ma_alignment": "MIXED"}
    elif above200:
        return {"direction": "SIDEWAYS",   "strength": "WEAK",     "ma_alignment": "NEUTRAL"}
    elif not above20 and not above50 and not above200:
        return {"direction": "DOWNTREND",  "strength": "STRONG",   "ma_alignment": "BEARISH"}
    else:
        return {"direction": "DOWNTREND",  "strength": "MODERATE", "ma_alignment": "MIXED"}


def _build_scenarios(trend: dict, sr: dict, price: float):
    """Generate 4 simple scenarios from trend state."""
    direction = trend.get("direction", "SIDEWAYS")
    strength  = trend.get("strength",  "MODERATE")
    supports  = sr.get("support", [])
    resists   = sr.get("resistance", [])
    s1  = f"${supports[0]['price']:,.2f}"  if supports  else "recent lows"
    r1  = f"${resists[0]['price']:,.2f}"   if resists   else "recent highs"

    if direction == "UPTREND" and strength == "STRONG":
        scenarios = [
            {"name": "Continuation",    "probability": 55,
             "description": "Price holds above all MAs and continues higher.",
             "outperformers": "Growth / Momentum", "underperformers": "Defensive / Bonds",
             "catalysts": "Strong earnings, sector rotation into risk assets"},
            {"name": "Pullback & Base", "probability": 25,
             "description": f"Healthy pullback to {s1} before next leg up.",
             "outperformers": "Value / Dividend", "underperformers": "High-beta growth",
             "catalysts": "Profit-taking, short-term rate concern"},
            {"name": "Breakout Fail",   "probability": 12,
             "description": f"Breaks below 20w SMA; tests {s1}.",
             "outperformers": "Defensive / Cash", "underperformers": "Cyclical",
             "catalysts": "Macro shock, earnings miss"},
            {"name": "Distribution",    "probability": 8,
             "description": "Volume divergence precedes trend reversal.",
             "outperformers": "Cash / Bonds", "underperformers": "Equities broadly",
             "catalysts": "Fed pivot, credit event"},
        ]
    elif direction == "DOWNTREND":
        scenarios = [
            {"name": "Continued Decline", "probability": 50,
             "description": f"Remains below {r1}; selling pressure ongoing.",
             "outperformers": "Cash / Defensive", "underperformers": "High-beta",
             "catalysts": "Weak earnings, deteriorating macro"},
            {"name": "Dead Cat Bounce",   "probability": 25,
             "description": f"Short rally to {r1} then rejection.",
             "outperformers": "Short sellers", "underperformers": "Longs buying bounce",
             "catalysts": "Oversold conditions, short squeeze"},
            {"name": "Base Building",     "probability": 15,
             "description": "Consolidation at current levels; trend neutralises.",
             "outperformers": "Value stocks", "underperformers": "Growth",
             "catalysts": "Stabilising macro, value buyers step in"},
            {"name": "Trend Reversal",    "probability": 10,
             "description": f"Breaks above {r1}; new uptrend begins.",
             "outperformers": "Cyclical / Growth", "underperformers": "Defensive",
             "catalysts": "Surprise catalyst, policy shift"},
        ]
    else:
        scenarios = [
            {"name": "Range Breakout ↑", "probability": 40,
             "description": f"Clears {r1}; trend turns up.",
             "outperformers": "Momentum / Growth", "underperformers": "Defensive",
             "catalysts": "Earnings beat, macro improvement"},
            {"name": "Range Breakdown ↓","probability": 30,
             "description": f"Breaks {s1}; downtrend resumes.",
             "outperformers": "Cash / Bonds", "underperformers": "Cyclical",
             "catalysts": "Earnings miss, macro deterioration"},
            {"name": "Range Continuation","probability": 20,
             "description": "Continues consolidating between S/R.",
             "outperformers": "Sector neutral", "underperformers": "Trend followers",
             "catalysts": "No clear catalyst, mixed data"},
            {"name": "Volatility Spike", "probability": 10,
             "description": "Sudden move driven by macro event.",
             "outperformers": "Options holders", "underperformers": "Carry traders",
             "catalysts": "Fed surprise, geopolitical event"},
        ]
    return scenarios


def _make_ta2_html(analysis: dict) -> str:
    """Generate a rich HTML analysis report from the analysis dict."""
    sym   = analysis.get("symbol", "?")
    price = analysis.get("price", 0)
    trend = analysis.get("trend", {})
    sr    = analysis.get("sr", {"support": [], "resistance": []})
    scenarios = analysis.get("scenarios", [])
    sma20  = analysis.get("sma20", 0)
    sma50  = analysis.get("sma50", 0)
    sma200 = analysis.get("sma200", 0)
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M")

    dir_  = trend.get("direction", "—")
    str_  = trend.get("strength", "—")
    ma_   = trend.get("ma_alignment", "—")
    dir_c = (C["green"] if "UP" in dir_ else C["red"] if "DOWN" in dir_ else C["yellow"])

    sup_rows = "".join(
        f"<tr><td style='padding:4px 10px;color:{C['sec']};'>S{i+1}</td>"
        f"<td style='color:{C['green']};font-weight:600;'>${r['price']:,.4f}</td></tr>"
        for i, r in enumerate(sr.get("support", [])[:4])
    )
    res_rows = "".join(
        f"<tr><td style='padding:4px 10px;color:{C['sec']};'>R{i+1}</td>"
        f"<td style='color:{C['red']};font-weight:600;'>${r['price']:,.4f}</td></tr>"
        for i, r in enumerate(sr.get("resistance", [])[:4])
    )

    sc_colors = [C["green"], C["accent"], C["yellow"], C["red"]]
    sc_html = ""
    for i, sc in enumerate(scenarios):
        c  = sc_colors[i % len(sc_colors)]
        pb = sc.get("probability", 25)
        sc_html += f"""
<div style='background:{C["card"]};border-radius:8px;padding:10px 14px;
  margin:8px 0;border-left:3px solid {c};'>
  <div style='color:{c};font-size:13px;font-weight:700;'>{sc["name"]}
    <span style='color:{C["sec"]};font-size:11px;font-weight:400;'>({pb}%)</span></div>
  <div style='background:{C["hover"]};border-radius:3px;height:6px;margin:4px 0 8px;'>
    <div style='width:{pb}%;background:{c};height:6px;border-radius:3px;'></div></div>
  <div style='color:{C["sec"]};font-size:11px;margin-bottom:4px;'>{sc.get("description","")}</div>
  <div style='font-size:11px;'><span style='color:{C["green"]};'>▲ </span>
    <b>Out:</b> {sc.get("outperformers","—")}</div>
  <div style='font-size:11px;'><span style='color:{C["red"]};'>▼ </span>
    <b>Under:</b> {sc.get("underperformers","—")}</div>
  <div style='font-size:11px;color:{C["sec"]};margin-top:3px;'>
    <b>Catalysts:</b> {sc.get("catalysts","—")}</div>
</div>"""

    return f"""<!DOCTYPE html><html>
<head><meta charset='utf-8'>
<style>
  body{{background:{C["bg"]};color:{C["text"]};font-family:'Segoe UI',Arial,sans-serif;
    font-size:12px;padding:14px 18px;margin:0;}}
  h2{{color:{C["accent"]};font-size:15px;margin:0 0 4px;}}
  h3{{color:{C["yellow"]};font-size:13px;margin:14px 0 6px;}}
  table{{border-collapse:collapse;width:100%;}}
</style></head>
<body>
<h2>📊 {sym} — Weekly Technical Analysis</h2>
<div style='color:{C["sec"]};font-size:10px;margin-bottom:10px;'>Generated {ts} · US Market · Weekly bars</div>

<table style='background:{C["card"]};border-radius:6px;margin-bottom:12px;'>
  <tr><td style='padding:5px 10px;color:{C["sec"]};'>Price</td>
      <td style='font-weight:700;font-size:14px;'>${price:,.4f}</td>
      <td style='padding:5px 10px;color:{C["sec"]};'>Trend</td>
      <td style='color:{dir_c};font-weight:700;'>{dir_} ({str_})</td></tr>
  <tr><td style='padding:5px 10px;color:{C["sec"]};'>20w SMA</td>
      <td>${sma20:,.4f}</td>
      <td style='padding:5px 10px;color:{C["sec"]};'>MA Alignment</td>
      <td style='color:{dir_c};'>{ma_}</td></tr>
  <tr><td style='padding:5px 10px;color:{C["sec"]};'>50w SMA</td>
      <td>${sma50:,.4f}</td>
      <td style='padding:5px 10px;color:{C["sec"]};'>vs 20w SMA</td>
      <td style='color:{"#3fb950" if price > sma20 else "#f85149"};'>
        {"Above" if price > sma20 else "Below"}</td></tr>
  <tr><td style='padding:5px 10px;color:{C["sec"]};'>200w SMA</td>
      <td>${sma200:,.4f}</td>
      <td style='padding:5px 10px;color:{C["sec"]};'>vs 200w SMA</td>
      <td style='color:{"#3fb950" if price > sma200 else "#f85149"};'>
        {"Above" if price > sma200 else "Below"}</td></tr>
</table>

<h3>Support / Resistance</h3>
<div style='display:flex;gap:12px;'>
  <table style='background:{C["card"]};border-radius:6px;flex:1;'>{sup_rows}</table>
  <table style='background:{C["card"]};border-radius:6px;flex:1;'>{res_rows}</table>
</div>

<h3>Scenarios</h3>
{sc_html}
</body></html>"""


# ── Background worker ─────────────────────────────────────────────────────────

class _TAWorker(QThread):
    """Fetch weekly data + compute indicators for TechAnalystPanel."""

    progress  = Signal(int, str)          # pct, message
    result    = Signal(dict)              # analysis dict
    error     = Signal(str)

    def __init__(self, symbol: str, years: int = 5, parent=None):
        super().__init__(parent)
        self._symbol = symbol.upper()
        self._years  = years
        self._abort  = False

    def abort(self):
        self._abort = True

    def run(self):
        sym = self._symbol
        try:
            if not _YF_OK:
                self.error.emit("yfinance not installed (pip install yfinance)")
                return
            if not _PD_OK:
                self.error.emit("pandas not installed (pip install pandas)")
                return

            self.progress.emit(10, f"Fetching {sym} weekly data…")
            period_map = {1: "1y", 2: "2y", 3: "3y", 5: "5y", 10: "10y"}
            period = period_map.get(self._years, "5y")
            ticker = _yf.Ticker(sym)
            df     = ticker.history(period=period, interval="1wk")

            if self._abort:
                return
            if df is None or df.empty:
                self.error.emit(f"No weekly data returned for {sym}")
                return

            self.progress.emit(30, "Normalising columns…")
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            if len(df) < 5:
                self.error.emit(f"Insufficient data for {sym} ({len(df)} bars)")
                return

            self.progress.emit(50, "Computing indicators…")
            close  = df["close"]
            price  = float(close.iloc[-1])
            sma20  = float(_sma(close, 20).iloc[-1])
            sma50  = float(_sma(close, 50).iloc[-1])
            sma200 = float(_sma(close, 200).iloc[-1])
            trend  = _detect_trend(df)

            self.progress.emit(70, "Finding S/R levels…")
            sr = _find_sr(df)

            self.progress.emit(85, "Building scenarios…")
            scenarios = _build_scenarios(trend, sr, price)
            most_likely = scenarios[0]["name"]   if scenarios else "—"
            most_prob   = scenarios[0]["probability"] if scenarios else 0

            analysis = {
                "symbol":    sym,
                "market":    "US",
                "currency_sym": "$",
                "df":        df,
                "price":     price,
                "sma20":     sma20,
                "sma50":     sma50,
                "sma200":    sma200,
                "trend":     trend,
                "sr":        sr,
                "scenarios": scenarios,
                "most_likely_scenario":     most_likely,
                "most_likely_probability":  most_prob,
            }
            analysis["html"] = _make_ta2_html(analysis)

            self.progress.emit(100, "Done.")
            self.result.emit(analysis)

        except Exception as exc:
            self.error.emit(f"{sym}: {exc}")


# ── Chart canvas ──────────────────────────────────────────────────────────────

class _TAChartCanvas(QWidget):
    """Embeds a matplotlib Figure: weekly candles + SMAs + S/R + volume."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._canvas = None
        self._fig    = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._layout = lay
        self._placeholder = QLabel("Run an analysis to see the chart.")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(
            f"color:{C['muted']};font-size:13px;background:{C['bg']};"
        )
        lay.addWidget(self._placeholder)

    def plot(self, analysis: dict):
        if not _QTAGG_OK or not _PD_OK:
            self._placeholder.setText(
                "Chart unavailable — matplotlib Qt backend not installed.\n"
                "pip install matplotlib --break-system-packages"
            )
            return

        df = analysis.get("df")
        if df is None or df.empty:
            return

        if self._canvas:
            self._layout.removeWidget(self._canvas)
            self._canvas.deleteLater()
            self._canvas = None
        self._placeholder.hide()

        trend  = analysis.get("trend", {})
        sr     = analysis.get("sr", {})
        sym    = analysis.get("symbol", "")
        price  = analysis.get("price", 0)
        sma20  = analysis.get("sma20", 0)
        sma50  = analysis.get("sma50", 0)
        sma200 = analysis.get("sma200", 0)

        dfd = df.tail(104).copy().reset_index()
        n   = len(dfd)
        x   = list(range(n))

        fig       = Figure(figsize=(10, 7), facecolor=C["bg"])
        ax_price  = fig.add_subplot(2, 1, 1, facecolor=C["bg"])
        ax_vol    = fig.add_subplot(2, 1, 2, facecolor=C["bg"], sharex=ax_price)
        fig.subplots_adjust(left=0.07, right=0.97, top=0.93, bottom=0.06, hspace=0.05)

        # Candlesticks
        for i, row in dfd.iterrows():
            xi  = x[i]
            o, h, lo, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            col = C["green"] if c >= o else C["red"]
            ax_price.plot([xi, xi], [lo, h], color=col, linewidth=0.8, zorder=1)
            body_bot = min(o, c)
            body_h   = abs(c - o) or (h - lo) * 0.01
            ax_price.add_patch(Rectangle(
                (xi - 0.3, body_bot), 0.6, body_h,
                facecolor=col, edgecolor=col, linewidth=0.4, zorder=2
            ))

        # SMA lines
        close_full = df["close"]
        for period, color, lw, label in (
            (20,  C["accent"],  1.2, f"20w SMA ${sma20:,.2f}"),
            (50,  C["yellow"],  1.2, f"50w SMA ${sma50:,.2f}"),
            (200, C["red"],     1.5, f"200w SMA ${sma200:,.2f}"),
        ):
            sma_vals = _sma(close_full, period).tail(104).values
            valid = [(xi, float(v)) for xi, v in zip(x, sma_vals)
                     if v == v]  # NaN-safe
            if valid:
                xs_, ys_ = zip(*valid)
                ax_price.plot(xs_, ys_, color=color, linewidth=lw, label=label,
                              alpha=0.85, zorder=3)

        # S/R levels
        for lvl in sr.get("support", [])[:3]:
            p = lvl["price"]
            ax_price.axhline(p, color=C["green"], linewidth=0.8, linestyle="--", alpha=0.6)
            ax_price.text(n - 1, p, f"  S ${p:,.2f}", color=C["green"], fontsize=7, va="center")
        for lvl in sr.get("resistance", [])[:3]:
            p = lvl["price"]
            ax_price.axhline(p, color=C["red"], linewidth=0.8, linestyle="--", alpha=0.6)
            ax_price.text(n - 1, p, f"  R ${p:,.2f}", color=C["red"], fontsize=7, va="center")

        dir_  = trend.get("direction", "")
        str_  = trend.get("strength", "")
        ma_   = trend.get("ma_alignment", "")
        title = f"{sym}  —  Weekly  |  ${price:,.4f}  |  {dir_} ({str_})  |  MA: {ma_}"
        ax_price.set_title(title, color=C["text"], fontsize=10, pad=6)
        ax_price.tick_params(colors=C["sec"], labelsize=8)
        ax_price.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _: f"${v:,.2f}")
        )
        for sp in ax_price.spines.values():
            sp.set_color(C["border"])
        ax_price.grid(True, color=C["card"], linewidth=0.5)
        ax_price.legend(loc="upper left", fontsize=7, facecolor=C["card"],
                        labelcolor=C["text"], framealpha=0.8)
        import matplotlib.pyplot as plt
        plt.setp(ax_price.get_xticklabels(), visible=False)

        # Volume bars + 20w vol SMA
        vol_full = df["volume"]
        vol_sma  = _sma(vol_full, 20).tail(104).values
        for i, row in dfd.iterrows():
            xi  = x[i]
            col = C["green"] if float(row["close"]) >= float(row["open"]) else C["red"]
            ax_vol.bar(xi, float(row["volume"]), color=col, alpha=0.5, width=0.8, zorder=2)
        valid_v = [(xi, float(v)) for xi, v in zip(x, vol_sma) if v == v]
        if valid_v:
            xs_v, ys_v = zip(*valid_v)
            ax_vol.plot(xs_v, ys_v, color=C["yellow"], linewidth=1.0, zorder=3)

        ax_vol.set_ylabel("Volume", color=C["sec"], fontsize=8)
        ax_vol.tick_params(colors=C["sec"], labelsize=7)
        ax_vol.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _: f"{v/1e6:.1f}M" if v >= 1e6 else f"{v:.0f}")
        )
        for sp in ax_vol.spines.values():
            sp.set_color(C["border"])
        ax_vol.grid(True, color=C["card"], linewidth=0.5)

        # X-axis date labels
        step = max(1, n // 12)
        ticks, labels = [], []
        for tp in range(0, n, step):
            try:
                dt = dfd.iloc[tp].get("Date", dfd.index[tp])
                labels.append(str(dt)[:7])
            except Exception:
                labels.append("")
            ticks.append(tp)
        ax_vol.set_xticks(ticks)
        ax_vol.set_xticklabels(labels, rotation=30, ha="right",
                               color=C["sec"], fontsize=7)

        self._fig    = fig
        self._canvas = _FigCanvas(fig)
        self._canvas.setStyleSheet(f"background:{C['bg']};")
        self._layout.addWidget(self._canvas)
        self._canvas.draw()

    def clear(self):
        if self._canvas:
            self._layout.removeWidget(self._canvas)
            self._canvas.deleteLater()
            self._canvas = None
        self._placeholder.show()


# ── Main panel ────────────────────────────────────────────────────────────────

class TechAnalystPanel(QWidget):
    """TECH ANALYST sub-tab of the Analyst panel.

    Left panel  : symbol input, watchlist picker, timeframe, Analyse/Save/Cancel
    Right panel : Chart tab · Analysis tab · Saved Reports tab

    Public API
    ----------
    set_symbols(symbols: list) — populate watchlist dropdown from WatchlistPanel
    """

    trade_requested = Signal(str, str)   # (symbol, side)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _TAWorker | None   = None
        self._analysis: dict | None      = None
        self._symbols: list              = []
        self._current_sym                = ""
        self._setup_ui()

    def set_symbols(self, symbols: list):
        self._symbols = symbols
        self._wl_sym_cb.blockSignals(True)
        self._wl_sym_cb.clear()
        self._wl_sym_cb.addItem("— pick from watchlist —", None)
        for s in symbols:
            self._wl_sym_cb.addItem(s, s)
        self._wl_sym_cb.blockSignals(False)

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_left())

        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{C['border']};")
        root.addWidget(sep)
        root.addWidget(self._build_right(), 1)

    def _build_left(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(265)
        panel.setStyleSheet(f"background:{C['card']};")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 14, 12, 12)
        lay.setSpacing(4)

        title_bar = QHBoxLayout()
        title = QLabel("📊 Tech Analyst")
        title.setStyleSheet(f"color:{C['accent']};font-size:14px;font-weight:700;")
        title_bar.addWidget(title)

        toggle_btn = QPushButton("«")
        toggle_btn.setToolTip("Collapse/Expand Sidebar")
        toggle_btn.setCursor(Qt.PointingHandCursor)
        toggle_btn.setStyleSheet("QPushButton { color:#00f0ff; font-weight:bold; border:none; background:transparent; font-size:16px; min-width:24px; max-width:24px; } QPushButton:hover { color:#00a8ff; }")
        title_bar.addWidget(toggle_btn)
        lay.addLayout(title_bar)

        content_w = QWidget()
        content_w.setStyleSheet("background:transparent;")
        content_lay = QVBoxLayout(content_w)
        content_lay.setContentsMargins(0, 0, 0, 0); content_lay.setSpacing(4)
        lay.addWidget(content_w)

        def toggle_sidebar():
            if content_w.isVisible():
                content_w.hide()
                title.hide()
                panel.setFixedWidth(35)
                toggle_btn.setText("»")
            else:
                content_w.show()
                title.show()
                panel.setFixedWidth(265)
                toggle_btn.setText("«")
        toggle_btn.clicked.connect(toggle_sidebar)

        self._div(content_lay)

        # Symbol
        self._sec_lbl(content_lay, "SYMBOL")
        self._sym_input = QLineEdit()
        self._sym_input.setPlaceholderText("e.g. AAPL")
        self._sym_input.setStyleSheet(
            f"background:{C['hover']};color:{C['text']};border:1px solid {C['border']};"
            f"border-radius:4px;padding:5px 10px;font-size:12px;"
        )
        self._sym_input.returnPressed.connect(self._run_analysis)
        content_lay.addWidget(self._sym_input)

        # Watchlist picker
        self._sec_lbl(content_lay, "FROM WATCHLIST")
        self._wl_sym_cb = QComboBox()
        self._wl_sym_cb.setStyleSheet(self._combo_ss())
        self._wl_sym_cb.addItem("— pick from watchlist —", None)
        self._wl_sym_cb.currentIndexChanged.connect(self._on_wl_sym_selected)
        content_lay.addWidget(self._wl_sym_cb)

        # Timeframe
        self._sec_lbl(content_lay, "HISTORY (YEARS)")
        self._years_spin = QSpinBox()
        self._years_spin.setRange(1, 10)
        self._years_spin.setValue(5)
        self._years_spin.setStyleSheet(
            f"QSpinBox{{background:{C['hover']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;padding:4px 8px;}}"
        )
        content_lay.addWidget(self._years_spin)

        content_lay.addSpacing(8)
        self._div(content_lay)
        content_lay.addSpacing(4)

        # Buttons
        self._btn_analyse = QPushButton("🔬 Analyse")
        self._btn_analyse.setStyleSheet(
            f"QPushButton{{background:{C['accent']};color:#0d1117;border:none;"
            f"border-radius:4px;padding:6px 12px;font-size:12px;font-weight:700;}}"
            f"QPushButton:hover{{background:{C['accent']}cc;}}"
            f"QPushButton:disabled{{background:{C['muted']};color:{C['sec']};}}"
        )
        self._btn_analyse.clicked.connect(self._run_analysis)
        content_lay.addWidget(self._btn_analyse)

        self._btn_save = QPushButton("💾 Save Report")
        self._btn_save.setStyleSheet(
            f"QPushButton{{background:#238636;color:#fff;border:none;"
            f"border-radius:4px;padding:5px 12px;font-size:12px;}}"
            f"QPushButton:hover{{background:#2ea043;}}"
            f"QPushButton:disabled{{background:{C['muted']};color:{C['sec']};}}"
        )
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._save_report)
        content_lay.addWidget(self._btn_save)

        self._btn_cancel = QPushButton("⏹ Cancel")
        self._btn_cancel.setStyleSheet(
            f"QPushButton{{background:#b91c1c;color:#fff;border:none;"
            f"border-radius:4px;padding:5px 12px;font-size:12px;}}"
            f"QPushButton:disabled{{background:{C['muted']};color:{C['sec']};}}"
        )
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._cancel)
        content_lay.addWidget(self._btn_cancel)

        content_lay.addSpacing(8)
        self._div(content_lay)
        content_lay.addSpacing(4)

        # Progress
        self._sec_lbl(content_lay, "PROGRESS")
        self._prog = QProgressBar()
        self._prog.setRange(0, 100)
        self._prog.setValue(0)
        self._prog.setTextVisible(True)
        self._prog.setStyleSheet(
            f"QProgressBar{{background:{C['hover']};border:1px solid {C['border']};"
            f"border-radius:4px;text-align:center;color:{C['text']};font-size:10px;height:14px;}}"
            f"QProgressBar::chunk{{background:{C['accent']};border-radius:3px;}}"
        )
        content_lay.addWidget(self._prog)

        self._status_lbl = QLabel("Ready.")
        self._status_lbl.setStyleSheet(
            f"color:{C['sec']};font-size:10px;"
        )
        self._status_lbl.setWordWrap(True)
        content_lay.addWidget(self._status_lbl)
        content_lay.addStretch()
        return panel

    def _build_right(self) -> QWidget:
        panel = QWidget()
        lay   = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            f"QTabWidget::pane{{border:1px solid {C['border']};background:{C['bg']};}}"
            f"QTabBar::tab{{background:{C['card']};color:{C['sec']};"
            f"border:1px solid {C['border']};border-bottom:none;"
            f"padding:5px 14px;font-size:12px;}}"
            f"QTabBar::tab:hover{{background:{C['hover']};color:{C['text']};}}"
            f"QTabBar::tab:selected{{background:{C['bg']};color:{C['text']};font-weight:700;}}"
        )

        # Chart tab
        self._chart_canvas = _TAChartCanvas()
        self._tabs.addTab(self._chart_canvas, "📈 Chart")

        # Analysis tab — with Dry Buy/Sell header bar
        _ana_w = QWidget()
        _ana_lay = QVBoxLayout(_ana_w)
        _ana_lay.setContentsMargins(0, 4, 4, 0)
        _ana_lay.setSpacing(4)
        _ta_btn_row = QHBoxLayout()
        _ta_btn_row.addStretch()
        self._btn_ta_buy = QPushButton("🟢  (Dry) Buy")
        self._btn_ta_buy.setFixedHeight(28)
        self._btn_ta_buy.setEnabled(False)
        self._btn_ta_buy.setStyleSheet(
            "QPushButton{background:#1a7f37;color:#fff;border:none;border-radius:4px;"
            "padding:3px 12px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#238636;}"
            "QPushButton:disabled{background:#555;color:#888;}")
        self._btn_ta_buy.clicked.connect(
            lambda: self.trade_requested.emit(self._current_sym, "buy"))
        self._btn_ta_sell = QPushButton("🔴  (Dry) Sell")
        self._btn_ta_sell.setFixedHeight(28)
        self._btn_ta_sell.setEnabled(False)
        self._btn_ta_sell.setStyleSheet(
            "QPushButton{background:#b91c1c;color:#fff;border:none;border-radius:4px;"
            "padding:3px 12px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#dc2626;}"
            "QPushButton:disabled{background:#555;color:#888;}")
        self._btn_ta_sell.clicked.connect(
            lambda: self.trade_requested.emit(self._current_sym, "sell"))
        _ta_btn_row.addWidget(self._btn_ta_buy)
        _ta_btn_row.addWidget(self._btn_ta_sell)
        _ana_lay.addLayout(_ta_btn_row)
        self._report_view = QTextEdit()
        self._report_view.setReadOnly(True)
        self._report_view.setStyleSheet(
            f"QTextEdit{{background:{C['bg']};color:{C['text']};"
            f"border:none;padding:8px;font-family:'Segoe UI',Arial,sans-serif;font-size:12px;}}"
        )
        self._report_view.setHtml(self._placeholder_html())
        _ana_lay.addWidget(self._report_view)
        self._tabs.addTab(_ana_w, "📄 Analysis")

        # Saved Reports tab
        self._tabs.addTab(self._build_history_tab(), "🗂️ Saved Reports")

        lay.addWidget(self._tabs)
        return panel

    def _build_history_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        tb = QHBoxLayout()
        lbl = QLabel("Saved analysis reports:")
        lbl.setStyleSheet(f"color:{C['sec']};font-size:11px;")
        tb.addWidget(lbl)
        tb.addStretch()
        btn_ref = QPushButton("🔄 Refresh")
        btn_ref.setStyleSheet(
            f"QPushButton{{background:{C['hover']};color:{C['text']};"
            f"border:none;border-radius:4px;padding:4px 10px;font-size:11px;}}"
        )
        btn_ref.clicked.connect(self._load_history)
        tb.addWidget(btn_ref)
        lay.addLayout(tb)

        self._hist_list = QListWidget()
        self._hist_list.setMaximumHeight(160)
        self._hist_list.setStyleSheet(
            f"QListWidget{{background:{C['card']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;}}"
            f"QListWidget::item:selected{{background:{C['accent']}40;}}"
            f"QListWidget::item:hover{{background:{C['hover']};}}"
        )
        self._hist_list.itemDoubleClicked.connect(self._open_history_item)
        lay.addWidget(self._hist_list)

        self._hist_view = QTextEdit()
        self._hist_view.setReadOnly(True)
        self._hist_view.setStyleSheet(
            f"QTextEdit{{background:{C['bg']};color:{C['text']};"
            f"border:none;padding:8px;font-size:11px;}}"
        )
        lay.addWidget(self._hist_view, 1)
        self._load_history()
        return w

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _div(self, lay):
        d = QFrame()
        d.setFixedHeight(1)
        d.setStyleSheet(f"background:{C['border']};margin:4px 0;")
        lay.addWidget(d)

    def _sec_lbl(self, lay, text: str):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{C['sec']};font-size:11px;font-weight:600;margin-top:6px;"
        )
        lay.addWidget(lbl)

    def _combo_ss(self) -> str:
        return (
            f"QComboBox{{background:{C['hover']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;padding:4px 8px;}}"
            f"QComboBox::drop-down{{border:none;}}"
            f"QComboBox QAbstractItemView{{background:{C['hover']};color:{C['text']};"
            f"selection-background-color:{C['accent']};border:1px solid {C['border']};}}"
        )

    def _placeholder_html(self) -> str:
        return (
            f"<html><body style='background:{C['bg']};color:{C['muted']};"
            f"font-family:Segoe UI,sans-serif;font-size:13px;'>"
            f"<p style='text-align:center;margin-top:60px;'>"
            f"Enter a symbol and click <b style='color:{C['accent']};'>Analyse</b> "
            f"to generate a weekly technical analysis report.</p></body></html>"
        )

    # ── Watchlist integration ─────────────────────────────────────────────────

    def _on_wl_sym_selected(self):
        sym = self._wl_sym_cb.currentData()
        if sym:
            self._sym_input.setText(sym)

    # ── Analysis flow ─────────────────────────────────────────────────────────

    def _run_analysis(self):
        sym = self._sym_input.text().strip().upper()
        if not sym:
            QMessageBox.warning(self, "No Symbol", "Enter a ticker symbol.")
            return
        if self._worker and self._worker.isRunning():
            return

        self._current_sym = sym
        self._btn_ta_buy.setEnabled(False)
        self._btn_ta_sell.setEnabled(False)
        self._btn_analyse.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._btn_save.setEnabled(False)
        self._prog.setValue(0)
        self._status_lbl.setText(f"Fetching {sym}…")
        self._report_view.setHtml(self._placeholder_html())
        self._chart_canvas.clear()
        self._analysis = None

        self._worker = _TAWorker(sym, self._years_spin.value(), parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(3000)
        self._on_finished()
        self._status_lbl.setText("Cancelled.")

    def _on_progress(self, pct: int, msg: str):
        self._prog.setValue(pct)
        self._status_lbl.setText(msg)

    def _on_result(self, analysis: dict):
        self._analysis = analysis
        self._chart_canvas.plot(analysis)
        self._report_view.setHtml(analysis.get("html", ""))
        self._tabs.setCurrentIndex(1)
        self._btn_save.setEnabled(True)
        self._btn_ta_buy.setEnabled(bool(self._current_sym))
        self._btn_ta_sell.setEnabled(bool(self._current_sym))
        ml  = analysis.get("most_likely_scenario", "")
        pct = analysis.get("most_likely_probability", 0)
        self._status_lbl.setText(
            f"Done — most likely: {ml} ({pct}%)"
        )

    def _on_error(self, msg: str):
        QMessageBox.critical(self, "Analysis Error", msg)
        self._status_lbl.setText("Error.")

    def _on_finished(self):
        self._btn_analyse.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        if self._worker:
            self._worker.deleteLater()
            self._worker = None

    # ── Save / History ────────────────────────────────────────────────────────

    def _save_report(self):
        if not self._analysis:
            return
        try:
            sym = self._analysis.get("symbol", "SYM")
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(_TA2_RPT_DIR, f"{sym}_{ts}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._analysis.get("html", ""))
            self._load_history()
            QMessageBox.information(self, "Report Saved",
                                    f"Saved to:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Save Error", str(exc))

    def _load_history(self):
        self._hist_list.clear()
        if not os.path.isdir(_TA2_RPT_DIR):
            return
        files = sorted(
            [f for f in os.listdir(_TA2_RPT_DIR) if f.endswith(".html")],
            reverse=True
        )
        for fname in files:
            item = QListWidgetItem(fname)
            item.setData(Qt.UserRole, os.path.join(_TA2_RPT_DIR, fname))
            self._hist_list.addItem(item)

    def _open_history_item(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        try:
            with open(path, encoding="utf-8") as f:
                html = f.read()
            self._hist_view.setHtml(html)
        except Exception as exc:
            self._hist_view.setPlainText(f"Error: {exc}")

    # ── Public: pre-load symbol ───────────────────────────────────────────────

    def load_symbol(self, symbol: str):
        self._sym_input.setText(symbol.upper())
        self._run_analysis()


# ══════════════════════════════════════════════════════════════════════════════
# 14e.  SECTOR ANALYST  —  US sector rotation (yfinance ETF data)
# ══════════════════════════════════════════════════════════════════════════════
# Ported from StockMate / SectorWidget.
# US-only; self-contained — fetches 11 SPDR sector ETFs vs SPY via yfinance.
# ══════════════════════════════════════════════════════════════════════════════

_US_SECTORS = [
    {"sector": "Technology",        "symbol": "XLK",  "group": "cyclical"},
    {"sector": "Financials",        "symbol": "XLF",  "group": "cyclical"},
    {"sector": "Consumer Discret.", "symbol": "XLY",  "group": "cyclical"},
    {"sector": "Industrials",       "symbol": "XLI",  "group": "cyclical"},
    {"sector": "Communication Svcs","symbol": "XLC",  "group": "cyclical"},
    {"sector": "Real Estate",       "symbol": "XLRE", "group": "cyclical"},
    {"sector": "Energy",            "symbol": "XLE",  "group": "commodity"},
    {"sector": "Materials",         "symbol": "XLB",  "group": "commodity"},
    {"sector": "Health Care",       "symbol": "XLV",  "group": "defensive"},
    {"sector": "Consumer Staples",  "symbol": "XLP",  "group": "defensive"},
    {"sector": "Utilities",         "symbol": "XLU",  "group": "defensive"},
]
_BENCHMARK_SYM = "SPY"

# Sector leadership by cycle phase (US)
_US_CYCLE_PHASES = {
    "early":     {"leaders": ["Financials", "Consumer Discret.", "Industrials",
                               "Materials", "Real Estate"],
                  "laggards": ["Utilities", "Consumer Staples", "Health Care"]},
    "mid":       {"leaders": ["Technology", "Industrials", "Energy", "Financials"],
                  "laggards": ["Utilities", "Consumer Staples"]},
    "late":      {"leaders": ["Energy", "Materials", "Health Care",
                               "Consumer Staples", "Utilities"],
                  "laggards": ["Technology", "Consumer Discret.", "Financials"]},
    "recession": {"leaders": ["Utilities", "Consumer Staples", "Health Care"],
                  "laggards": ["Energy", "Financials", "Industrials",
                                "Technology", "Consumer Discret."]},
}

_SECTOR_GROUP_COLORS = {
    "cyclical":  C["accent"],
    "defensive": "#a5d6a7",
    "commodity": "#ffa726",
}
_REGIME_COLORS = {
    "strong risk-on":  C["green"],
    "risk-on":         C["accent"],
    "balanced":        "#ffa726",
    "defensive tilt":  "#ef9a9a",
    "strong risk-off": C["red"],
}
_PHASE_COLORS = {
    "early":     C["accent"],
    "mid":       C["green"],
    "late":      "#ffa726",
    "recession": C["red"],
}

# Table column order
_S_COLS = ["Rank", "Sector", "Group", "RS",
           "UTR%", "1W%", "1M%", "3M%", "1Y%",
           "vsBM 1M", "vsBM 3M", "Trend", "Status"]
(_S_RANK, _S_SEC, _S_GRP, _S_RS,
 _S_UTR, _S_1W, _S_1M, _S_3M, _S_1Y,
 _S_VBM1M, _S_VBM3M, _S_TREND, _S_STATUS) = range(13)


# ── Worker ────────────────────────────────────────────────────────────────────

class _SectorWorker(QThread):
    """Fetch US sector ETF data via yfinance and compute rotation metrics."""

    progress    = Signal(int, str)
    log_message = Signal(str)
    result      = Signal(dict)
    error       = Signal(str)

    def run(self):
        try:
            if not _YF_OK:
                self.error.emit("yfinance not installed (pip install yfinance)")
                return
            if not _PD_OK:
                self.error.emit("pandas not installed (pip install pandas)")
                return

            all_syms = [_BENCHMARK_SYM] + [s["symbol"] for s in _US_SECTORS]
            self.progress.emit(5, "Downloading ETF data…")
            self.log_message.emit("Downloading 1Y daily data for sector ETFs…")

            raw = _yf.download(
                all_syms, period="1y", interval="1d",
                auto_adjust=True, progress=False, group_by="ticker"
            )

            self.progress.emit(40, "Computing returns…")

            def closes(sym: str):
                try:
                    if isinstance(raw.columns, pd.MultiIndex):
                        return raw[sym]["Close"].dropna()
                    return raw["Close"].dropna()
                except Exception:
                    return pd.Series(dtype=float)

            def safe_ret(s, days: int):
                if len(s) < days + 1:
                    return None
                base = float(s.iloc[-(days + 1)])
                if base == 0:
                    return None
                return round((float(s.iloc[-1]) / base - 1) * 100, 2)

            bm_s  = closes(_BENCHMARK_SYM)
            bm_1m = safe_ret(bm_s, 21)
            bm_3m = safe_ret(bm_s, 63)
            bm_1y = safe_ret(bm_s, 252)

            # Weekly resampled series for 20w SMA trend check
            def weekly_sma20(s):
                if s.empty:
                    return None, None
                w = s.resample("W").last().dropna()
                if len(w) < 3:
                    return None, None
                sma = w.rolling(20, min_periods=3).mean()
                return float(w.iloc[-1]), float(sma.iloc[-1])

            ranking = []
            total   = len(_US_SECTORS)
            for i, sec_def in enumerate(_US_SECTORS):
                sym = sec_def["symbol"]
                self.progress.emit(40 + int(i / total * 45),
                                   f"Processing {sym}…")
                self.log_message.emit(f"  {sym} — {sec_def['sector']}")
                s = closes(sym)
                if s.empty:
                    continue

                r1w = safe_ret(s, 5)
                r1m = safe_ret(s, 21)
                r3m = safe_ret(s, 63)
                r1y = safe_ret(s, 252)

                # RS score: sector 1Y return ranked vs benchmark, scaled 0-100
                # Simplified: (r1y - bm_1y) / (|bm_1y| + 1) * 25 + 50, clamp 0-100
                rs_raw = ((r1y or 0) - (bm_1y or 0)) / (abs(bm_1y or 1) + 0.1) * 25 + 50
                rs     = round(max(0, min(100, rs_raw)), 1)

                vs_1m  = round((r1m or 0) - (bm_1m or 0), 2) if r1m is not None else None
                vs_3m  = round((r3m or 0) - (bm_3m or 0), 2) if r3m is not None else None

                px, sma20v = weekly_sma20(s)
                if px is not None and sma20v is not None:
                    trend_dir = "up" if px > sma20v else "down"
                    utr_pct   = 100.0 if px > sma20v else 0.0
                    dist_pct  = (px - sma20v) / sma20v * 100
                    if trend_dir == "up":
                        status = "overbought" if dist_pct > 10 else "uptrend"
                    else:
                        status = "oversold" if dist_pct < -10 else "downtrend"
                else:
                    trend_dir = "—"
                    utr_pct   = None
                    status    = "no data"

                ranking.append({
                    "sector":     sec_def["sector"],
                    "symbol":     sym,
                    "group":      sec_def["group"],
                    "rs_score":   rs,
                    "uptrend_pct": utr_pct,
                    "ret_1w":     r1w,
                    "ret_1m":     r1m,
                    "ret_3m":     r3m,
                    "ret_1y":     r1y,
                    "vs_bm_1m":   vs_1m,
                    "vs_bm_3m":   vs_3m,
                    "trend":      trend_dir,
                    "status":     status,
                })

            # Sort by RS score descending and add rank
            ranking.sort(key=lambda r: r["rs_score"], reverse=True)
            for i, r in enumerate(ranking):
                r["rank"] = i + 1

            self.progress.emit(90, "Analysing cycle phase…")

            # Groups
            cyc = [r["ret_1m"] or 0 for r in ranking if r["group"] == "cyclical"]
            dfn = [r["ret_1m"] or 0 for r in ranking if r["group"] == "defensive"]
            cyc_avg = round(sum(cyc) / len(cyc), 2) if cyc else 0
            def_avg = round(sum(dfn) / len(dfn), 2) if dfn else 0
            spread  = round(cyc_avg - def_avg, 2)
            score   = int(max(0, min(100, 50 + spread * 3)))

            if   score >= 70: regime = "strong risk-on"
            elif score >= 55: regime = "risk-on"
            elif score >= 45: regime = "balanced"
            elif score >= 30: regime = "defensive tilt"
            else:             regime = "strong risk-off"

            # Cycle phase from top-ranked sector groups
            top5_grps = [r["group"] for r in ranking[:5]]
            cyc_cnt = top5_grps.count("cyclical")
            def_cnt = top5_grps.count("defensive")
            top_sec = ranking[0]["sector"] if ranking else ""

            if cyc_cnt >= 4:
                phase, conf = "early", "high"
                if top_sec in ["Technology", "Communication Svcs"]:
                    phase = "mid"
            elif def_cnt >= 3:
                phase = "recession" if score < 40 else "late"
                conf  = "moderate"
            else:
                phase = "mid"
                conf  = "moderate"

            phase_labels = {
                "early": "Early Bull — cyclicals lead",
                "mid":   "Mid Cycle — broadening rally",
                "late":  "Late Cycle — defensives strengthening",
                "recession": "Risk-Off / Recession — defensives dominate",
            }
            evidence = [
                f"Cyclical avg 1M: {cyc_avg:+.1f}%  vs  Defensive avg: {def_avg:+.1f}%",
                f"Spread (Cyc–Def): {spread:+.1f}pp",
                f"Top sector: {top_sec}",
                f"Regime score: {score}/100",
            ]

            up_ct = sum(1 for r in ranking if r["trend"] == "up")
            dn_ct = sum(1 for r in ranking if r["trend"] == "down")

            overbought = [r for r in ranking if r["status"] == "overbought"]
            oversold   = [r for r in ranking if r["status"] == "oversold"]

            scenarios = self._make_scenarios(phase, regime, ranking[:3])

            # Simple text report
            report_lines = [
                f"=== US Sector Analysis  ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===",
                f"Regime: {regime.upper()}  |  Score: {score}/100",
                f"Cycle Phase: {phase_labels.get(phase, phase)}",
                f"Trend Breadth: {up_ct} up / {dn_ct} down",
                "",
                "SECTOR RANKINGS",
                "-" * 60,
            ]
            for r in ranking:
                r1m_s = f"{r['ret_1m']:+.1f}%" if r['ret_1m'] is not None else "  N/A"
                report_lines.append(
                    f"{r['rank']:2d}. {r['sector']:<22} RS={r['rs_score']:5.1f}  "
                    f"1M={r1m_s:>7}  {r['trend'].upper():<5}  {r['status']}"
                )

            freshness_date = datetime.now().strftime("%Y-%m-%d")

            self.progress.emit(100, "Done.")
            self.result.emit({
                "market":   "us",
                "source":   "yfinance/SPDR ETF",
                "ranking":  ranking,
                "groups": {
                    "regime": regime, "score": score,
                    "cyclical_avg_pct": cyc_avg,
                    "defensive_avg_pct": def_avg,
                    "difference_pct": spread,
                },
                "cycle": {
                    "phase": phase,
                    "phase_label": phase_labels.get(phase, phase),
                    "confidence": conf,
                    "evidence": evidence,
                },
                "trends":  {"uptrend_count": up_ct, "downtrend_count": dn_ct},
                "overbought": overbought,
                "oversold":   oversold,
                "scenarios":  scenarios,
                "report":     "\n".join(report_lines),
                "freshness": {"date": freshness_date, "warning": ""},
            })

        except Exception as exc:
            import traceback as _tb
            self.error.emit(str(exc) + "\n" + _tb.format_exc()[-500:])

    @staticmethod
    def _make_scenarios(phase: str, regime: str, top3: list) -> list:
        tops = ", ".join(r["sector"] for r in top3) if top3 else "—"
        if phase == "early":
            return [
                {"name": "Broadening Cyclical Rally", "probability": 45,
                 "description": f"Early-cycle rotation continues; {tops} extend gains.",
                 "outperformers": "Financials, Industrials, Consumer Discret.",
                 "underperformers": "Utilities, Bonds",
                 "catalysts": "Improving earnings, yield curve steepening"},
                {"name": "Pause & Consolidation", "probability": 30,
                 "description": "Rally pauses; rotation into value and quality.",
                 "outperformers": "Value stocks, Health Care",
                 "underperformers": "High-beta growth",
                 "catalysts": "Mixed economic data, profit-taking"},
                {"name": "Risk-Off Shock",          "probability": 15,
                 "description": "Macro shock triggers defensive rotation.",
                 "outperformers": "Consumer Staples, Utilities, Gold",
                 "underperformers": "Cyclicals broadly",
                 "catalysts": "Fed surprise, credit event"},
                {"name": "Late-Cycle Skip",          "probability": 10,
                 "description": "Energy/commodities take leadership.",
                 "outperformers": "Energy, Materials",
                 "underperformers": "Tech, Consumer Discret.",
                 "catalysts": "Commodity supply shock, geopolitics"},
            ]
        elif phase in ("late", "recession"):
            return [
                {"name": "Defensive Continuation",  "probability": 45,
                 "description": "Defensive rotation deepens; risk appetite fades.",
                 "outperformers": "Utilities, Consumer Staples, Health Care",
                 "underperformers": "Technology, Consumer Discret.",
                 "catalysts": "Earnings revisions down, Fed on hold"},
                {"name": "Stimulus-Led Recovery",   "probability": 25,
                 "description": "Policy pivot sparks cyclical re-rating.",
                 "outperformers": "Financials, Industrials",
                 "underperformers": "Defensives on rotation out",
                 "catalysts": "Rate cuts, fiscal stimulus"},
                {"name": "Stagflation Hedge",        "probability": 20,
                 "description": "Commodity sectors hold as inflation stays sticky.",
                 "outperformers": "Energy, Materials",
                 "underperformers": "Rate-sensitive sectors",
                 "catalysts": "Supply disruptions, sticky CPI"},
                {"name": "Rapid Recovery",           "probability": 10,
                 "description": "V-shaped recovery; cyclicals surge.",
                 "outperformers": "Consumer Discret., Technology",
                 "underperformers": "Cash, Bonds",
                 "catalysts": "Surprise positive macro data"},
            ]
        else:  # mid
            return [
                {"name": "Mid-Cycle Broadening",    "probability": 40,
                 "description": "Rally broadens; value and small-caps participate.",
                 "outperformers": f"{tops}",
                 "underperformers": "Utilities, Consumer Staples",
                 "catalysts": "Solid earnings, moderate growth"},
                {"name": "Growth Leadership",        "probability": 30,
                 "description": "Tech/growth re-accelerates on AI/innovation themes.",
                 "outperformers": "Technology, Communication Svcs",
                 "underperformers": "Value, Cyclical",
                 "catalysts": "AI capex cycle, earnings beats"},
                {"name": "Late-Cycle Rotation",      "probability": 20,
                 "description": "Energy and defensives take leadership.",
                 "outperformers": "Energy, Health Care, Consumer Staples",
                 "underperformers": "Tech, Consumer Discret.",
                 "catalysts": "Inflation re-acceleration, geopolitical risk"},
                {"name": "Correction & Reset",       "probability": 10,
                 "description": "Broad market pullback resets valuations.",
                 "outperformers": "Cash, Short-vol strategies",
                 "underperformers": "High-multiple growth",
                 "catalysts": "Valuation concern, rate spike"},
            ]


# ── Main panel ────────────────────────────────────────────────────────────────

class SectorAnalystPanel(QWidget):
    """SECTOR ANALYST sub-tab of the Analyst panel.

    Shows US sector rotation analysis using SPDR ETFs vs SPY benchmark.
    Ported from StockMate SectorWidget; India market removed; US-only.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _SectorWorker | None = None
        self._result: dict                 = {}
        self._current_tab: str             = "Heatmap"
        self._setup_ui()

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_progress_bar())

        # Summary cards strip
        self._cards_frame = QFrame()
        self._cards_frame.setFixedHeight(90)
        self._cards_frame.setStyleSheet(
            f"background:{C['bg']};border-bottom:1px solid {C['card']};"
        )
        self._cards_lay = QHBoxLayout(self._cards_frame)
        self._cards_lay.setContentsMargins(12, 8, 12, 8)
        self._cards_lay.setSpacing(12)
        root.addWidget(self._cards_frame)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet(
            f"QSplitter::handle{{background:{C['border']};width:1px;}}"
        )
        splitter.addWidget(self._build_table_panel())
        splitter.addWidget(self._build_analysis_panel())
        splitter.setSizes([720, 480])
        root.addWidget(splitter, 1)

    def _build_header(self) -> QFrame:
        h = QFrame()
        h.setFixedHeight(56)
        h.setStyleSheet(
            f"background:{C['card']};border-bottom:1px solid {C['border']};"
        )
        lay = QHBoxLayout(h)
        lay.setContentsMargins(16, 0, 16, 0)

        title = QLabel("📊  Sector Analyst — US Markets")
        title.setStyleSheet(
            "color:#ffa726;font-size:17px;font-weight:700;"
        )
        lay.addWidget(title)
        lay.addStretch()

        info = QLabel("11 SPDR ETFs  ·  Benchmark: SPY")
        info.setStyleSheet(f"color:{C['sec']};font-size:11px;margin-right:12px;")
        lay.addWidget(info)

        self._btn_refresh = QPushButton("🔄 Refresh Data")
        self._btn_refresh.setFixedHeight(34)
        self._btn_refresh.setStyleSheet(
            "QPushButton{background:#ffa726;color:#0d1117;border:none;"
            "border-radius:4px;padding:0 16px;font-size:13px;font-weight:700;}"
            "QPushButton:hover{background:#ffb74d;}"
            "QPushButton:disabled{background:#21262d;color:#3d4449;}"
        )
        self._btn_refresh.clicked.connect(self._run_analysis)
        lay.addWidget(self._btn_refresh)

        self._btn_export = QPushButton("Export CSV")
        self._btn_export.setFixedHeight(34)
        self._btn_export.setStyleSheet(
            f"QPushButton{{background:{C['hover']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;"
            f"padding:0 12px;font-size:12px;}}"
            f"QPushButton:hover{{background:{C['border']};}}"
        )
        self._btn_export.clicked.connect(self._export_csv)
        lay.addWidget(self._btn_export)
        return h

    def _build_progress_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(32)
        bar.setStyleSheet(f"background:{C['bg']};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 12, 4)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(12)
        self._progress.setStyleSheet(
            f"QProgressBar{{background:{C['hover']};border:none;border-radius:6px;}}"
            "QProgressBar::chunk{background:#ffa726;border-radius:6px;}"
        )
        self._log_lbl = QLabel("")
        self._log_lbl.setStyleSheet(f"color:{C['sec']};font-size:11px;")
        lay.addWidget(self._progress, 1)
        lay.addWidget(self._log_lbl)
        return bar

    def _build_table_panel(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lbl = QLabel("  Sector Rankings")
        lbl.setFixedHeight(28)
        lbl.setStyleSheet(
            f"background:{C['card']};color:{C['sec']};font-size:11px;"
            f"font-weight:600;border-bottom:1px solid {C['border']};"
        )
        lay.addWidget(lbl)

        self._table = QTableWidget(0, 13)
        self._table.setHorizontalHeaderLabels(_S_COLS)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.verticalHeader().setDefaultSectionSize(26)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(_S_SEC, QHeaderView.Stretch)
        hdr.setDefaultSectionSize(70)
        hdr.resizeSection(_S_RANK,   38)
        hdr.resizeSection(_S_GRP,    80)
        hdr.resizeSection(_S_RS,     42)
        hdr.resizeSection(_S_UTR,    46)
        hdr.resizeSection(_S_STATUS, 82)
        self._table.setStyleSheet(
            f"QTableWidget{{background:{C['bg']};color:{C['text']};"
            f"border:none;gridline-color:{C['card']};font-size:12px;"
            f"selection-background-color:{C['hover']};}}"
            f"QHeaderView::section{{background:{C['card']};color:{C['sec']};"
            f"border:none;border-bottom:1px solid {C['border']};"
            f"padding:3px;font-size:11px;font-weight:600;}}"
        )
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        lay.addWidget(self._table, 1)
        return w

    def _build_analysis_panel(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        tab_bar = QFrame()
        tab_bar.setFixedHeight(32)
        tab_bar.setStyleSheet(
            f"background:{C['card']};border-bottom:1px solid {C['border']};"
        )
        tlay = QHBoxLayout(tab_bar)
        tlay.setContentsMargins(8, 0, 8, 0)
        tlay.setSpacing(4)

        self._tab_buttons = {}
        for name in ("Heatmap", "Scenarios", "Report", "Detail"):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{C['sec']};"
                f"border:none;padding:0 10px;font-size:12px;border-radius:3px;}}"
                f"QPushButton:checked{{background:{C['hover']};color:#ffa726;font-weight:700;}}"
                f"QPushButton:hover:!checked{{color:{C['text']};}}"
            )
            btn.clicked.connect(lambda _, n=name: self._switch_tab(n))
            self._tab_buttons[name] = btn
            tlay.addWidget(btn)
        tlay.addStretch()
        lay.addWidget(tab_bar)

        self._analysis_view = QTextEdit()
        self._analysis_view.setReadOnly(True)
        self._analysis_view.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:none;"
            f"font-size:12px;padding:4px;"
        )
        lay.addWidget(self._analysis_view, 1)
        self._switch_tab("Heatmap")
        return w

    def _switch_tab(self, name: str):
        for n, b in self._tab_buttons.items():
            b.setChecked(n == name)
        self._current_tab = name
        self._refresh_view()

    # ── Analysis flow ─────────────────────────────────────────────────────────

    def _run_analysis(self):
        self._result = {}
        self._table.setRowCount(0)
        self._analysis_view.clear()
        self._clear_cards()
        self._progress.setValue(0)
        self._btn_refresh.setEnabled(False)

        self._worker = _SectorWorker(parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._on_log)
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(lambda: self._btn_refresh.setEnabled(True))
        self._worker.start()

    def _on_progress(self, pct, msg):
        self._progress.setValue(pct)
        self._log_lbl.setText(msg)

    def _on_log(self, msg):
        self._log_lbl.setText(msg)

    def _on_result(self, result: dict):
        self._result = result
        self._populate_cards(result)
        self._populate_table(result.get("ranking", []))
        self._refresh_view()
        src = result.get("source", "")
        self._log_lbl.setText(f"Done — {src}")

    def _on_error(self, msg: str):
        self._log_lbl.setText(f"ERROR: {msg[:100]}")
        QMessageBox.critical(self, "Sector Analysis Error", msg[:600])

    # ── Summary cards ─────────────────────────────────────────────────────────

    def _clear_cards(self):
        while self._cards_lay.count():
            item = self._cards_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _populate_cards(self, result: dict):
        self._clear_cards()
        groups    = result.get("groups", {})
        cycle     = result.get("cycle", {})
        trends    = result.get("trends", {})
        freshness = result.get("freshness", {})

        regime    = groups.get("regime", "—")
        score     = groups.get("score", 0)
        phase_lbl = cycle.get("phase_label", cycle.get("phase", "—"))
        conf      = cycle.get("confidence", "—")
        up_ct     = trends.get("uptrend_count", 0)
        dn_ct     = trends.get("downtrend_count", 0)
        cyc_pct   = groups.get("cyclical_avg_pct")
        def_pct   = groups.get("defensive_avg_pct")

        rc = _REGIME_COLORS.get(regime, "#ffa726")
        pc = _PHASE_COLORS.get(cycle.get("phase", "mid"), "#ffa726")

        cards = [
            ("Risk Regime",   regime.upper(),            f"{score}/100",     rc),
            ("Cycle Phase",   phase_lbl,                 f"{conf} confidence", pc),
            ("Trend Breadth", f"{up_ct} Up / {dn_ct} Down", "",              C["accent"]),
        ]
        if cyc_pct is not None and def_pct is not None:
            diff = round(groups.get("difference_pct", 0), 1)
            sign = "+" if diff >= 0 else ""
            cards.append(("Cyc vs Def",
                           f"Cyc {cyc_pct:+.1f}% / Def {def_pct:+.1f}%",
                           f"Spread {sign}{diff}pp",
                           C["green"] if diff > 0 else C["red"]))

        for title, val, sub, color in cards:
            self._cards_lay.addWidget(self._make_card(title, val, sub, color))
        self._cards_lay.addStretch()

    def _make_card(self, title, value, sub, color) -> QFrame:
        card = QFrame()
        card.setFixedHeight(72)
        card.setMinimumWidth(140)
        card.setMaximumWidth(220)
        card.setStyleSheet(
            f"QFrame{{background:{C['card']};border:1px solid {color}40;"
            f"border-radius:6px;}}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"color:{C['sec']};font-size:10px;font-weight:600;")
        v = QLabel(value)
        v.setStyleSheet(f"color:{color};font-size:13px;font-weight:700;")
        v.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(v)
        if sub:
            s = QLabel(sub)
            s.setStyleSheet(f"color:{C['sec']};font-size:10px;")
            lay.addWidget(s)
        return card

    # ── Table ─────────────────────────────────────────────────────────────────

    def _populate_table(self, ranking: list):
        self._table.setRowCount(0)
        for row in ranking:
            r   = self._table.rowCount()
            self._table.insertRow(r)
            grp    = row.get("group", "")
            grp_fg = _SECTOR_GROUP_COLORS.get(grp, C["text"])
            trend  = row.get("trend", "")
            status = row.get("status", "")

            def cell(val, align=Qt.AlignCenter, fg=None):
                it = QTableWidgetItem("" if val is None else str(val))
                it.setTextAlignment(align)
                if fg:
                    it.setForeground(QColor(fg))
                return it

            def ret_cell(val):
                s = f"{val:+.1f}" if val is not None else "—"
                c = C["green"] if (val or 0) > 0 else C["red"] if (val or 0) < 0 else C["sec"]
                return cell(s, fg=c)

            def rs_cell(val):
                it = QTableWidgetItem(str(int(val or 0)))
                it.setTextAlignment(Qt.AlignCenter)
                v  = val or 0
                c  = C["green"] if v >= 70 else "#ffa726" if v >= 50 else "#ef9a9a"
                it.setForeground(QColor(c))
                it.setFont(QFont("Segoe UI", 10, QFont.Bold))
                return it

            utr = row.get("uptrend_pct")
            sc_map = {
                "uptrend":   C["green"], "overbought": "#ffa726",
                "oversold":  C["accent"], "downtrend": C["red"], "no data": C["muted"],
            }
            td_fg = C["green"] if trend == "up" else C["red"] if trend == "down" else C["sec"]

            self._table.setItem(r, _S_RANK,   cell(row.get("rank", "")))
            self._table.setItem(r, _S_SEC,    cell(row.get("sector", ""), Qt.AlignLeft|Qt.AlignVCenter))
            self._table.setItem(r, _S_GRP,    cell(grp, fg=grp_fg))
            self._table.setItem(r, _S_RS,     rs_cell(row.get("rs_score", 0)))
            self._table.setItem(r, _S_UTR,    cell(f"{utr:.0f}" if utr is not None else "—"))
            self._table.setItem(r, _S_1W,     ret_cell(row.get("ret_1w")))
            self._table.setItem(r, _S_1M,     ret_cell(row.get("ret_1m")))
            self._table.setItem(r, _S_3M,     ret_cell(row.get("ret_3m")))
            self._table.setItem(r, _S_1Y,     ret_cell(row.get("ret_1y")))
            self._table.setItem(r, _S_VBM1M,  ret_cell(row.get("vs_bm_1m")))
            self._table.setItem(r, _S_VBM3M,  ret_cell(row.get("vs_bm_3m")))
            self._table.setItem(r, _S_TREND,  cell(trend, fg=td_fg))
            self._table.setItem(r, _S_STATUS, cell(status, fg=sc_map.get(status, C["text"])))

            self._table.item(r, _S_RANK).setData(Qt.UserRole, row)

    # ── Analysis panel views ──────────────────────────────────────────────────

    def _on_row_selected(self):
        if self._current_tab == "Detail":
            row_idx = self._table.currentRow()
            if row_idx >= 0:
                item = self._table.item(row_idx, _S_RANK)
                if item:
                    row = item.data(Qt.UserRole)
                    if row:
                        self._show_sector_detail(row)

    def _refresh_view(self):
        t = self._current_tab
        if t == "Heatmap":
            self._show_heatmap()
        elif t == "Scenarios":
            self._show_scenarios()
        elif t == "Report":
            self._show_report()
        elif t == "Detail":
            self._analysis_view.setHtml(
                f"<div style='color:{C['sec']};padding:20px;font-family:Segoe UI;'>"
                "Click a sector in the table to see details.</div>"
            )

    def _show_heatmap(self):
        ranking = self._result.get("ranking", [])
        groups  = self._result.get("groups", {})
        if not ranking:
            self._analysis_view.setHtml(
                f"<div style='color:{C['sec']};padding:20px;font-family:Segoe UI;'>"
                "Press <b>🔄 Refresh Data</b> to load US sector analysis.</div>"
            )
            return

        max_rs = max((r["rs_score"] for r in ranking), default=100) or 100
        grp_order = ["cyclical", "commodity", "defensive"]
        by_group: dict = {g: [] for g in grp_order}
        for row in ranking:
            by_group.setdefault(row.get("group", "cyclical"), []).append(row)

        bars_html = ""
        for grp in grp_order:
            rows = by_group.get(grp, [])
            if not rows:
                continue
            fg = _SECTOR_GROUP_COLORS.get(grp, C["text"])
            bars_html += (
                f"<div style='color:{fg};font-size:11px;font-weight:700;"
                f"margin:10px 0 4px;text-transform:uppercase;'>{grp}</div>"
            )
            for row in sorted(rows, key=lambda x: x["rs_score"], reverse=True):
                rs    = row["rs_score"]
                pct   = int(rs / max_rs * 100)
                v1m   = row.get("ret_1m")
                v1m_s = f"{v1m:+.1f}%" if v1m is not None else "—"
                v1m_c = C["green"] if (v1m or 0) > 0 else C["red"]
                vs_bm = row.get("vs_bm_1m")
                vs_s  = f"{vs_bm:+.1f}pp" if vs_bm is not None else ""
                td_c  = C["green"] if row.get("trend") == "up" else C["red"]
                bars_html += f"""
<div style='margin:3px 0;display:flex;align-items:center;'>
  <div style='width:120px;color:{C["text"]};font-size:12px;white-space:nowrap;overflow:hidden;'>{row['sector']}</div>
  <div style='flex:1;background:{C["hover"]};border-radius:3px;height:14px;margin:0 8px;'>
    <div style='width:{pct}%;background:{fg};height:14px;border-radius:3px;'></div>
  </div>
  <div style='width:32px;text-align:right;color:{fg};font-weight:700;font-size:11px;'>{rs:.0f}</div>
  <div style='width:52px;text-align:right;color:{v1m_c};font-size:11px;'>{v1m_s}</div>
  <div style='width:54px;text-align:right;color:{C["sec"]};font-size:10px;'>{vs_s}</div>
  <div style='width:16px;text-align:right;font-size:10px;color:{td_c};'>{"▲" if row.get("trend")=="up" else "▼"}</div>
</div>"""

        regime   = groups.get("regime", "—").upper()
        regime_c = _REGIME_COLORS.get(groups.get("regime", "balanced"), "#ffa726")
        cycle    = self._result.get("cycle", {})
        phase_lbl = cycle.get("phase_label", "—")
        phase_c   = _PHASE_COLORS.get(cycle.get("phase", "mid"), "#ffa726")

        ob = self._result.get("overbought", [])
        os_ = self._result.get("oversold", [])
        ob_html = "".join(
            f"<span style='background:#ffa72620;color:#ffa726;padding:2px 6px;"
            f"border-radius:3px;margin:2px;'>{s['sector']}</span>" for s in ob
        )
        os_html = "".join(
            f"<span style='background:{C['accent']}20;color:{C['accent']};padding:2px 6px;"
            f"border-radius:3px;margin:2px;'>{s['sector']}</span>" for s in os_
        )

        html = f"""<div style='font-family:Segoe UI,Arial;padding:14px;'>
<div style='display:flex;gap:16px;margin-bottom:12px;'>
  <div style='background:{C["card"]};border-radius:6px;padding:8px 14px;'>
    <div style='color:{C["sec"]};font-size:10px;'>RISK REGIME</div>
    <div style='color:{regime_c};font-size:15px;font-weight:700;'>{regime}</div>
  </div>
  <div style='background:{C["card"]};border-radius:6px;padding:8px 14px;'>
    <div style='color:{C["sec"]};font-size:10px;'>CYCLE PHASE</div>
    <div style='color:{phase_c};font-size:13px;font-weight:700;'>{phase_lbl}</div>
  </div>
</div>
<div style='color:#ffa726;font-size:13px;font-weight:700;margin-bottom:8px;'>
  Sector RS Scores — SPDR ETFs vs SPY benchmark</div>
{bars_html}
{"<div style='margin-top:10px;color:" + C["sec"] + ";font-size:11px;'>Overbought: " + ob_html + "</div>" if ob else ""}
{"<div style='margin-top:4px;color:" + C["sec"] + ";font-size:11px;'>Oversold: " + os_html + "</div>" if os_ else ""}
</div>"""
        self._analysis_view.setHtml(html)

    def _show_scenarios(self):
        scenarios = self._result.get("scenarios", [])
        cycle     = self._result.get("cycle", {})
        if not scenarios:
            self._analysis_view.setHtml(
                f"<div style='color:{C['sec']};padding:20px;'>No data yet.</div>"
            )
            return
        phase_c = _PHASE_COLORS.get(cycle.get("phase", "mid"), "#ffa726")
        html = (
            f"<div style='font-family:Segoe UI,Arial;padding:14px;'>"
            f"<div style='color:{phase_c};font-size:15px;font-weight:700;margin-bottom:4px;'>"
            f"{cycle.get('phase_label', '—')}"
            f"<span style='color:{C['sec']};font-size:12px;font-weight:400;'>"
            f" &mdash; {cycle.get('confidence', '?')} confidence</span></div>"
        )
        ev = cycle.get("evidence", [])
        if ev:
            html += f"<div style='background:{C['card']};border-radius:6px;padding:8px 12px;margin:8px 0;'>"
            for e in ev:
                html += f"<div style='color:{C['sec']};font-size:11px;'>&#x2022; {e}</div>"
            html += "</div>"

        html += f"<div style='color:#ffa726;font-size:13px;font-weight:700;margin:12px 0 6px;'>Scenarios</div>"
        colors = [C["green"], C["accent"], "#ffa726", "#ef9a9a"]
        for i, sc in enumerate(scenarios):
            c  = colors[i % len(colors)]
            pb = sc.get("probability", 30)
            html += f"""
<div style='background:{C["card"]};border-radius:8px;padding:10px 14px;
  margin:8px 0;border-left:3px solid {c};'>
  <div style='color:{c};font-size:13px;font-weight:700;'>{sc["name"]}
    <span style='color:{C["sec"]};font-size:11px;font-weight:400;'>({pb}%)</span></div>
  <div style='background:{C["hover"]};border-radius:3px;height:6px;margin:4px 0 8px;'>
    <div style='width:{pb}%;background:{c};height:6px;border-radius:3px;'></div></div>
  <div style='color:{C["sec"]};font-size:11px;margin-bottom:4px;'>{sc.get("description","")}</div>
  <div style='font-size:11px;'><span style='color:{C["green"]};'>▲ </span>
    <b>Out:</b> {sc.get("outperformers","—")}</div>
  <div style='font-size:11px;'><span style='color:{C["red"]};'>▼ </span>
    <b>Under:</b> {sc.get("underperformers","—")}</div>
  <div style='font-size:11px;color:{C["sec"]};margin-top:3px;'>
    <b>Catalysts:</b> {sc.get("catalysts","—")}</div>
</div>"""
        html += "</div>"
        self._analysis_view.setHtml(html)

    def _show_report(self):
        report = self._result.get("report", "")
        if not report:
            self._analysis_view.setHtml(
                f"<div style='color:{C['sec']};padding:20px;'>No report yet.</div>"
            )
            return
        html = (
            f"<div style='font-family:monospace;font-size:11px;padding:12px;"
            f"color:{C['text']};white-space:pre;'>"
            + report.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            + "</div>"
        )
        self._analysis_view.setHtml(html)

    def _show_sector_detail(self, row: dict):
        sec = row.get("sector", "")
        sym = row.get("symbol", "")
        grp = row.get("group", "")
        fg  = _SECTOR_GROUP_COLORS.get(grp, C["text"])

        def pct(v): return f"{v:+.1f}%" if v is not None else "—"
        def rs_colored(v):
            c = C["green"] if (v or 0) >= 70 else "#ffa726" if (v or 0) >= 50 else C["red"]
            return f'<span style="color:{c};font-weight:700;">{int(v or 0)}</span>'

        cycle = self._result.get("cycle", {})
        phase = cycle.get("phase", "")
        defs  = _US_CYCLE_PHASES.get(phase, {})
        is_leader  = sec in defs.get("leaders", [])
        is_laggard = sec in defs.get("laggards", [])
        role_html  = ""
        if is_leader:
            role_html = f"<span style='color:{C['green']};background:{C['green']}20;padding:2px 6px;border-radius:3px;'>CYCLE LEADER</span>"
        elif is_laggard:
            role_html = f"<span style='color:{C['red']};background:{C['red']}20;padding:2px 6px;border-radius:3px;'>CYCLE LAGGARD</span>"

        td_c = C["green"] if row.get("trend") == "up" else C["red"]
        html = f"""<div style='font-family:Segoe UI,Arial;padding:14px;'>
<div style='color:{fg};font-size:16px;font-weight:700;'>{sec}
  <span style='color:{C["sec"]};font-size:12px;font-weight:400;'>({sym})</span></div>
<div style='margin:4px 0 10px;'>
  <span style='color:{fg};background:{fg}20;padding:2px 8px;border-radius:3px;font-size:11px;'>{grp}</span>
  &nbsp; {role_html}</div>
<table width="100%" cellspacing="0" style='background:{C["card"]};border-radius:6px;margin-bottom:10px;'>
  <tr><td style='padding:5px 10px;color:{C["sec"]};'>RS Score</td>
      <td>{rs_colored(row.get("rs_score"))}/100</td>
      <td style='padding:5px 10px;color:{C["sec"]};'>Trend</td>
      <td style='color:{td_c};font-weight:700;'>{row.get("trend","—").upper()}</td></tr>
  <tr><td style='padding:5px 10px;color:{C["sec"]};'>Status</td>
      <td colspan='3'>{row.get("status","—")}</td></tr>
</table>
<div style='color:{C["accent"]};font-size:12px;font-weight:700;margin:8px 0 2px;'>Performance</div>
<table width="100%" cellspacing="0" style='background:{C["card"]};border-radius:6px;margin-bottom:10px;'>
  <tr><td style='padding:4px 10px;color:{C["sec"]};'>1 Week</td>
      <td style='color:{"#3fb950" if (row.get("ret_1w") or 0)>0 else "#f85149"};'>{pct(row.get("ret_1w"))}</td>
      <td style='padding:4px 10px;color:{C["sec"]};'>vs BM 1M</td>
      <td style='color:{"#3fb950" if (row.get("vs_bm_1m") or 0)>0 else "#f85149"};'>{pct(row.get("vs_bm_1m"))}</td></tr>
  <tr><td style='padding:4px 10px;color:{C["sec"]};'>1 Month</td>
      <td style='color:{"#3fb950" if (row.get("ret_1m") or 0)>0 else "#f85149"};'>{pct(row.get("ret_1m"))}</td>
      <td style='padding:4px 10px;color:{C["sec"]};'>vs BM 3M</td>
      <td style='color:{"#3fb950" if (row.get("vs_bm_3m") or 0)>0 else "#f85149"};'>{pct(row.get("vs_bm_3m"))}</td></tr>
  <tr><td style='padding:4px 10px;color:{C["sec"]};'>3 Months</td>
      <td style='color:{"#3fb950" if (row.get("ret_3m") or 0)>0 else "#f85149"};'>{pct(row.get("ret_3m"))}</td>
      <td style='padding:4px 10px;color:{C["sec"]};'>1 Year</td>
      <td style='color:{"#3fb950" if (row.get("ret_1y") or 0)>0 else "#f85149"};'>{pct(row.get("ret_1y"))}</td></tr>
</table>
</div>"""
        self._analysis_view.setHtml(html)

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_csv(self):
        ranking = self._result.get("ranking", [])
        if not ranking:
            QMessageBox.information(self, "No Data", "Run analysis first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Sector Data", "sector_analysis.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        cols = ["rank", "sector", "symbol", "group", "rs_score", "uptrend_pct",
                "ret_1w", "ret_1m", "ret_3m", "ret_1y",
                "vs_bm_1m", "vs_bm_3m", "trend", "status"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            import csv as _csv
            w = _csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(ranking)
        QMessageBox.information(self, "Exported",
                                f"Saved to:\n{os.path.basename(path)}")



# ══════════════════════════════════════════════════════════════════════════════
# 14f.  VCP PANEL  —  Volatility Contraction Pattern screener (US, yfinance)
# ══════════════════════════════════════════════════════════════════════════════
# Ported from StockMate / VCPWidget + VCPWorker.
# US-only; self-contained inline VCP engine (no STOCKMATE dependency).
# ══════════════════════════════════════════════════════════════════════════════

_VCP_RPT_DIR = os.path.join(_AN_RPT_DIR, "vcp")
_VCP_SCAN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "analyst", "vcp_scans")
for _d in (_VCP_RPT_DIR, _VCP_SCAN_DIR):
    os.makedirs(_d, exist_ok=True)

# ── US stock lists ────────────────────────────────────────────────────────────
_SP500_SAMPLE = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","UNH","LLY",
    "JPM","V","XOM","AVGO","PG","MA","HD","COST","JNJ","MRK","ABBV","CVX",
    "CRM","KO","PEP","ACN","TMO","ADBE","BAC","MCD","WMT","ORCL","CSCO",
    "DHR","ABT","NEE","LIN","DIS","AMGN","PM","TXN","MS","BMY","ISRG","SPGI",
    "GS","UPS","BKNG","RTX","NOW",
]

_QQQ_SAMPLE = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AVGO","ASML","ADBE",
    "AMD","QCOM","NFLX","COST","INTC","PYPL","INTU","SBUX","GILD","ISRG",
    "ADP","REGN","VRTX","PANW","LRCX","KLAC","MRVL","CDNS","SNPS","FTNT",
]

# VCP rating colours  (fg, bg)
_VCP_RATING_COLORS = {
    "Textbook VCP":   ("#00d4aa", "#0a2520"),
    "Strong VCP":     ("#58a6ff", "#0a1628"),
    "Good VCP":       ("#a5d6a7", "#0a1e0a"),
    "Developing VCP": ("#ffa726", "#1e1200"),
    "Weak VCP":       ("#ef9a9a", "#1e0a0a"),
    "No VCP":         ("#8b949e", "#161b22"),
}

_VCP_STATE_FG = {
    "Pre-breakout":        "#00d4aa",
    "Breakout":            "#58a6ff",
    "Early-post-breakout": "#ffa726",
    "Extended":            "#ef9a9a",
    "Overextended":        "#f85149",
    "Damaged":             "#8b949e",
    "Invalid":             "#3d4449",
}

# ── Inline VCP engine ─────────────────────────────────────────────────────────

def _vcp_sma(prices: list, period: int):
    if len(prices) < period:
        return None
    return sum(prices[:period]) / period

def _vcp_trend_template(closes: list, price: float,
                         year_high: float, year_low: float) -> dict:
    """Evaluate Minervini 7-point Trend Template.  Returns score 0-100."""
    criteria = {}
    sma50  = _vcp_sma(closes, 50)
    sma150 = _vcp_sma(closes, 150)
    sma200 = _vcp_sma(closes, 200)
    sma200_22ago = _vcp_sma(closes[22:], 200) if len(closes) >= 222 else None

    def c(key, passed, detail):
        criteria[key] = {"passed": bool(passed), "detail": detail}

    c("c1_price_above_sma150_200",
      sma150 and sma200 and price > sma150 and price > sma200,
      f"Price ${price:.2f} vs SMA150 ${sma150:.2f}" + (f" / SMA200 ${sma200:.2f}" if sma200 else ""))

    c("c2_sma150_above_sma200",
      sma150 and sma200 and sma150 > sma200,
      f"SMA150 ${sma150:.2f} vs SMA200 ${sma200:.2f}" if sma150 and sma200 else "Insufficient data")

    c("c3_sma200_trending_up",
      sma200 and sma200_22ago and sma200 > sma200_22ago,
      f"SMA200 ${sma200:.2f} vs 22d ago ${sma200_22ago:.2f}" if sma200 and sma200_22ago else "Need 222+ days")

    c("c4_price_above_sma50",
      sma50 and price > sma50,
      f"Price ${price:.2f} vs SMA50 ${sma50:.2f}" if sma50 else "Insufficient data")

    if year_low and year_low > 0:
        pct_above = (price - year_low) / year_low * 100
        c("c5_25pct_above_52w_low", pct_above >= 25,
          f"{pct_above:.1f}% above 52w low ${year_low:.2f}")
    else:
        c("c5_25pct_above_52w_low", False, "52w low unavailable")

    if year_high and year_high > 0:
        pct_below = (year_high - price) / year_high * 100
        c("c6_within_25pct_52w_high", pct_below <= 25,
          f"{pct_below:.1f}% below 52w high ${year_high:.2f}")
    else:
        c("c6_within_25pct_52w_high", False, "52w high unavailable")

    # C7: estimated RS (placeholder – filled after RS calc)
    c("c7_rs_rank_above_70", False, "RS rank calculated after scan")

    passed_count = sum(1 for v in criteria.values() if v["passed"])
    raw_score = min(100.0, passed_count * 14.3)

    # Extended penalty
    penalty = 0
    sma50_dist = None
    sma200_dist = None
    if sma50:
        sma50_dist = (price - sma50) / sma50 * 100
        if sma50_dist > 8:
            excess = sma50_dist - 8
            penalty += -20 if excess >= 17 else -15 if excess >= 10 else -10 if excess >= 4 else -5
    if sma200:
        sma200_dist = (price - sma200) / sma200 * 100

    score = max(0.0, raw_score + penalty)
    return {
        "score": score, "raw_score": raw_score, "passed": score >= 85,
        "criteria": criteria, "criteria_passed": passed_count, "criteria_total": 7,
        "sma50": round(sma50, 2) if sma50 else None,
        "sma150": round(sma150, 2) if sma150 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "sma50_distance_pct":  round(sma50_dist, 2)  if sma50_dist  is not None else None,
        "sma200_distance_pct": round(sma200_dist, 2) if sma200_dist is not None else None,
        "extended_penalty": penalty,
    }


def _vcp_find_contractions(highs: list, lows: list, closes: list,
                            dates: list, lookback: int = 120) -> dict:
    """Simplified ATR-based VCP contraction detection.
    Works on chronological (oldest-first) data slices."""
    n = min(len(highs), lookback)
    H, L, C = highs[-n:], lows[-n:], closes[-n:]
    D = dates[-n:] if dates else [str(i) for i in range(n)]

    if n < 30:
        return {"valid_vcp": False, "contractions": [], "pivot_price": None,
                "score": 0, "wide_and_loose": False, "last_contraction_low": None}

    # ATR(14)
    def _atr(h, l, c_arr, period=14):
        if len(h) < period + 1:
            return sum(h[i] - l[i] for i in range(len(h))) / len(h) if h else 1.0
        trs = []
        for i in range(1, len(h)):
            trs.append(max(h[i]-l[i], abs(h[i]-c_arr[i-1]), abs(l[i]-c_arr[i-1])))
        return sum(trs[-period:]) / period

    atr = _atr(H, L, C)
    threshold = atr * 1.5

    # ZigZag swing point detection
    swings = []  # (idx, price, 'H'|'L')
    direction = None
    last_ext = 0

    for i in range(1, n):
        if direction is None:
            if H[i] - L[i] > threshold:
                direction = 'H' if H[i] > H[last_ext] else 'L'
                last_ext = i
        elif direction == 'H':
            if H[i] > H[last_ext]:
                last_ext = i
            elif H[last_ext] - L[i] > threshold:
                swings.append((last_ext, H[last_ext], 'H'))
                direction = 'L'
                last_ext = i
        else:
            if L[i] < L[last_ext]:
                last_ext = i
            elif H[i] - L[last_ext] > threshold:
                swings.append((last_ext, L[last_ext], 'L'))
                direction = 'H'
                last_ext = i

    if direction:
        typ = direction
        val = H[last_ext] if typ == 'H' else L[last_ext]
        swings.append((last_ext, val, typ))

    # Extract alternating H/L pairs as contractions
    contractions = []
    swing_highs = [s for s in swings if s[2] == 'H']
    swing_lows  = [s for s in swings if s[2] == 'L']

    for i, sh in enumerate(swing_highs[:-1]):
        # Find lowest between this high and next high
        lows_between = [sl for sl in swing_lows
                        if sh[0] < sl[0] < swing_highs[i+1][0]]
        if not lows_between:
            continue
        sl = min(lows_between, key=lambda x: x[1])
        depth = (sh[1] - sl[1]) / sh[1] * 100
        if depth < 4:
            continue
        contractions.append({
            "high_idx": sh[0], "low_idx": sl[0],
            "high_price": round(sh[1], 4), "low_price": round(sl[1], 4),
            "depth_pct": round(depth, 2),
            "duration_days": sl[0] - sh[0],
        })

    if not contractions:
        return {"valid_vcp": False, "contractions": [], "pivot_price": None,
                "score": 0, "wide_and_loose": False, "last_contraction_low": None}

    # Validate: each contraction tighter than previous
    tightening = all(
        contractions[i]["depth_pct"] > contractions[i+1]["depth_pct"]
        for i in range(len(contractions)-1)
    )
    valid_vcp = len(contractions) >= 2 and tightening

    # Pivot = high of last contraction
    pivot = contractions[-1]["high_price"] if contractions else None
    last_low = contractions[-1]["low_price"] if contractions else None

    # Wide-and-loose check
    final = contractions[-1]
    wide_and_loose = final["depth_pct"] > 15 and final["duration_days"] < 10

    # Score
    nc = len(contractions)
    score = 0
    if nc >= 2: score += 40
    if nc >= 3: score += 20
    if nc >= 4: score += 10
    if tightening: score += 20
    if valid_vcp:  score += 10
    if final["depth_pct"] < 8: score += 10
    if wide_and_loose: score -= 20
    score = max(0, min(100, score))

    return {
        "valid_vcp": valid_vcp,
        "contractions": contractions,
        "num_contractions": nc,
        "pivot_price": round(pivot, 4) if pivot else None,
        "last_contraction_low": round(last_low, 4) if last_low else None,
        "score": score,
        "wide_and_loose": wide_and_loose,
        "atr_value": round(atr, 4),
    }


def _vcp_volume_pattern(volumes: list, pivot_idx_from_end: int = 20) -> dict:
    """Simple volume pattern: dry-up ratio + accumulation days."""
    if len(volumes) < 30:
        return {"score": 50, "dry_up_ratio": None, "avg_volume_50d": 0,
                "breakout_volume_detected": False, "net_accumulation": 0}
    v = volumes
    avg50 = sum(v[-50:]) / min(50, len(v))
    # Recent 10-day avg vs 50-day avg
    recent10 = sum(v[-10:]) / 10
    dry_up_ratio = round(recent10 / avg50, 3) if avg50 > 0 else None
    # Accumulation: more up-vol than down-vol last 20 sessions
    closes_proxy = v  # volume is directional proxy
    net_acc = sum(1 for i in range(2, min(21, len(v))) if v[-i] > avg50 * 1.2) - \
              sum(1 for i in range(2, min(21, len(v))) if v[-i] < avg50 * 0.7)
    # Breakout: last session volume > 1.5x avg
    bvol = v[-1] > avg50 * 1.5 if v else False
    # Score
    score = 50
    if dry_up_ratio and dry_up_ratio < 0.8: score += 25
    if dry_up_ratio and dry_up_ratio < 0.6: score += 15
    if net_acc > 3: score += 10
    return {
        "score": min(100, max(0, score)),
        "dry_up_ratio": dry_up_ratio,
        "avg_volume_50d": int(avg50),
        "breakout_volume_detected": bvol,
        "net_accumulation": net_acc,
    }


def _vcp_pivot_proximity(price: float, pivot, last_low,
                          stop_buffer_pct: float = 1.5) -> dict:
    """Distance from pivot, stop calculation."""
    if not pivot:
        return {"score": 0, "distance_from_pivot_pct": None,
                "stop_loss_price": None, "risk_pct": None}
    dist = (price - pivot) / pivot * 100
    stop = round(last_low * (1 - stop_buffer_pct / 100), 4) if last_low else None
    risk = round((price - stop) / price * 100, 2) if stop and stop < price else None

    if dist < 0:   score = 30   # below pivot
    elif dist < 2: score = 100  # ideal buy zone
    elif dist < 5: score = 75
    elif dist < 10: score = 50
    else:          score = 20   # extended
    return {
        "score": score,
        "distance_from_pivot_pct": round(dist, 2),
        "stop_loss_price": stop,
        "risk_pct": risk,
    }


def _vcp_relative_strength(closes: list, bm_closes: list) -> dict:
    """Multi-period RS vs benchmark (SPY)."""
    def _ret(c, p):
        return (c[-1] / c[-p] - 1) * 100 if len(c) >= p and c[-p] > 0 else None

    periods = [(63, "3mo"), (126, "6mo"), (189, "9mo"), (252, "12mo")]
    weights = [0.4, 0.3, 0.2, 0.1]
    period_details = []
    weighted_rs = 0.0
    w_total = 0.0

    for (p, label), w in zip(periods, weights):
        r_sym = _ret(closes,  p)
        r_bm  = _ret(bm_closes, p)
        if r_sym is not None and r_bm is not None:
            rel = r_sym - r_bm
            period_details.append({"period_days": p, "label": label,
                                    "symbol_ret": round(r_sym, 2),
                                    "bm_ret": round(r_bm, 2),
                                    "relative_pct": round(rel, 2)})
            weighted_rs += rel * w
            w_total += w

    if not w_total:
        return {"score": 50, "weighted_rs": None, "period_details": [],
                "rs_rank_estimate": None}

    weighted_rs /= w_total
    # Score: outperform = high score
    score = 50 + min(50, max(-50, weighted_rs * 2))
    # RS rank estimate (0-99)
    rs_rank = int(min(99, max(0, 50 + weighted_rs * 2)))

    return {
        "score": round(score, 1),
        "weighted_rs": round(weighted_rs, 2),
        "period_details": period_details,
        "rs_rank_estimate": rs_rank,
    }


def _vcp_composite_score(T: float, C: float, V: float, P: float, R: float,
                          valid_vcp: bool, exec_state: str,
                          wide_and_loose: bool) -> dict:
    """Weighted composite score → rating band."""
    # Weights: T=25%, C=25%, V=20%, P=15%, R=15%
    raw = T*0.25 + C*0.25 + V*0.20 + P*0.15 + R*0.15

    # Bonus/penalty
    if valid_vcp:         raw += 5
    if exec_state == "Pre-breakout": raw += 3
    if exec_state in ("Breakout", "Early-post-breakout"): raw += 1
    if exec_state in ("Extended", "Overextended"): raw -= 10
    if wide_and_loose:    raw -= 15

    score = round(max(0, min(100, raw)), 1)

    if score >= 80:   rating = "Textbook VCP"
    elif score >= 65: rating = "Strong VCP"
    elif score >= 50: rating = "Good VCP"
    elif score >= 35: rating = "Developing VCP"
    elif score >= 20: rating = "Weak VCP"
    else:             rating = "No VCP"

    return {"composite_score": score, "rating": rating}


def _vcp_exec_state(price: float, pivot, sma50, sma200, last_low) -> str:
    """Determine execution state from price position."""
    if not pivot:
        return "Invalid"
    dist = (price - pivot) / pivot * 100 if pivot else 0
    if sma50 and price < sma50:
        return "Damaged"
    if dist < -2:
        return "Pre-breakout"
    if dist < 2:
        return "Breakout"
    if dist < 5:
        return "Early-post-breakout"
    if dist < 15:
        return "Extended"
    return "Overextended"


# ── VCP Worker ────────────────────────────────────────────────────────────────

class _VCPWorker(QThread):
    progress    = Signal(int, str)
    row_ready   = Signal(dict)
    log_message = Signal(str)
    finished    = Signal(list)
    error       = Signal(str)

    def __init__(self, symbols: list, parent=None):
        super().__init__(parent)
        self.symbols    = symbols
        self._cancelled = False
        self._results   = []

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self._run()
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()[:800]}")

    def _run(self):
        if not _yf:
            self.error.emit("yfinance not installed — cannot run VCP scan.")
            return

        total = len(self.symbols)
        self.log_message.emit(f"Downloading SPY benchmark…")
        try:
            bm_df = _yf.download("SPY", period="2y", interval="1d",
                                  auto_adjust=True, progress=False)
            if bm_df is None or bm_df.empty:
                raise RuntimeError("SPY download returned empty data")
            if _PD_OK:
                import pandas as _pd
                if isinstance(bm_df.columns, _pd.MultiIndex):
                    bm_df.columns = bm_df.columns.get_level_values(0)
            bm_closes = list(reversed(list(bm_df["Close"].dropna())))
        except Exception as e:
            self.error.emit(f"Benchmark download failed: {e}")
            return

        self.log_message.emit(f"Benchmark ready ({len(bm_closes)} days). Scanning {total} symbols…")

        for i, sym in enumerate(self.symbols):
            if self._cancelled:
                break
            pct = int(i / total * 100)
            self.progress.emit(pct, sym)
            self.log_message.emit(f"[{i+1}/{total}] {sym}…")
            row = self._analyse(sym, bm_closes)
            self._results.append(row)
            self.row_ready.emit(row)

        # Re-rank RS across universe
        self._rank_rs()

        self.progress.emit(100, "Done")
        self.finished.emit(self._results)

    def _analyse(self, symbol: str, bm_closes: list) -> dict:
        row = {
            "symbol": symbol, "market": "us", "company": symbol,
            "price": None, "score": 0, "rating": "No VCP",
            "exec_state": "—", "pattern_type": "—", "contractions": 0,
            "pivot": None, "stop": None, "risk_pct": None,
            "T": 0, "C": 0, "V": 0, "P": 0, "R": 0,
            "T_detail": {}, "C_detail": {}, "V_detail": {},
            "P_detail": {}, "R_detail": {},
            "error": None, "phase_skipped": None,
        }
        try:
            tk = _yf.Ticker(symbol)
            info = tk.info or {}
            price = (info.get("currentPrice") or info.get("regularMarketPrice")
                     or info.get("previousClose"))
            if not price:
                row["error"] = "No price data"; return row
            if price < 5.0:
                row["phase_skipped"] = f"Price ${price:.2f} < $5 floor"; return row

            avg_vol = info.get("averageVolume") or info.get("averageDailyVolume10Day") or 0
            if avg_vol < 200_000:
                row["phase_skipped"] = f"AvgVol {avg_vol:,} < 200k"; return row

            row["company"]   = info.get("longName") or info.get("shortName") or symbol
            row["price"]     = price
            year_high        = info.get("fiftyTwoWeekHigh") or 0
            year_low         = info.get("fiftyTwoWeekLow")  or 0

            # Download OHLCV
            df = _yf.download(symbol, period="2y", interval="1d",
                               auto_adjust=True, progress=False)
            if df is None or df.empty or len(df) < 50:
                row["error"] = "Insufficient OHLCV data"; return row
            if _PD_OK:
                import pandas as _pd
                if isinstance(df.columns, _pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

            df = df.dropna(subset=["Close"])
            dates   = [str(d)[:10] for d in df.index]
            closes  = list(df["Close"].astype(float))
            highs   = list(df["High"].astype(float))
            lows    = list(df["Low"].astype(float))
            volumes = list(df["Volume"].astype(float))
            # newest-first for SMA helpers
            closes_rev = list(reversed(closes))

            # Phase 2: Trend Template gate
            T_res = _vcp_trend_template(closes_rev, price, year_high, year_low)
            row["T"]        = T_res["score"]
            row["T_detail"] = T_res
            if T_res["score"] < 40:
                row["phase_skipped"] = f"Trend score {T_res['score']:.0f} < 40"
                return row

            # VCP pattern (chronological)
            C_res = _vcp_find_contractions(highs, lows, closes, dates, lookback=120)
            row["C"]          = C_res["score"]
            row["C_detail"]   = C_res
            row["contractions"] = C_res.get("num_contractions", 0)
            pivot  = C_res.get("pivot_price")
            last_low = C_res.get("last_contraction_low")
            row["pivot"] = pivot

            # Volume pattern
            V_res = _vcp_volume_pattern(volumes)
            row["V"]        = V_res["score"]
            row["V_detail"] = V_res

            # Pivot proximity
            P_res = _vcp_pivot_proximity(price, pivot, last_low)
            row["P"]        = P_res["score"]
            row["P_detail"] = P_res
            row["stop"]     = P_res.get("stop_loss_price")
            row["risk_pct"] = P_res.get("risk_pct")

            # Relative strength
            sym_closes_rev = closes_rev
            R_res = _vcp_relative_strength(sym_closes_rev, bm_closes)
            row["R"]        = R_res["score"]
            row["R_detail"] = R_res

            # Update T_detail c7 with RS rank
            rs_rank = R_res.get("rs_rank_estimate")
            if rs_rank is not None:
                c7 = {"passed": rs_rank > 70, "detail": f"RS Rank est: {rs_rank}"}
                row["T_detail"]["criteria"]["c7_rs_rank_above_70"] = c7
                if c7["passed"]:
                    row["T_detail"]["criteria_passed"] = T_res.get("criteria_passed", 0) + 1
                    row["T"] = min(100, T_res["score"] + 14.3)

            # Execution state
            sma50  = T_res.get("sma50")
            sma200 = T_res.get("sma200")
            exec_state = _vcp_exec_state(price, pivot, sma50, sma200, last_low)
            row["exec_state"] = exec_state

            # Pattern type
            nc = C_res.get("num_contractions", 0)
            wl = C_res.get("wide_and_loose", False)
            if not C_res.get("valid_vcp"):
                ptype = "No VCP"
            elif nc >= 4:
                ptype = "Classic VCP (4T)" if not wl else "Wide-Loose VCP"
            elif nc == 3:
                ptype = "Standard VCP (3T)" if not wl else "Wide-Loose VCP"
            else:
                ptype = "Developing VCP (2T)"
            row["pattern_type"] = ptype

            # Composite score
            comp = _vcp_composite_score(
                row["T"], row["C"], row["V"], row["P"], row["R"],
                C_res.get("valid_vcp", False), exec_state, wl)
            row["score"]  = comp["composite_score"]
            row["rating"] = comp["rating"]

        except Exception as e:
            row["error"] = str(e)[:120]
        return row

    def _rank_rs(self):
        """Re-rank RS within universe (percentile-based)."""
        rs_vals = [(r, r.get("R_detail", {}).get("weighted_rs"))
                   for r in self._results if r.get("R_detail", {}).get("weighted_rs") is not None]
        if not rs_vals:
            return
        sorted_rs = sorted(rs_vals, key=lambda x: x[1])
        n = len(sorted_rs)
        for rank_idx, (row, _) in enumerate(sorted_rs):
            pct = int(rank_idx / max(n - 1, 1) * 99)
            row["R_detail"]["rs_rank_estimate"] = pct
            row["R"] = round(50 + pct * 0.5, 1)
            # Recalculate composite
            C_res = row.get("C_detail", {})
            comp = _vcp_composite_score(
                row["T"], row["C"], row["V"], row["P"], row["R"],
                C_res.get("valid_vcp", False),
                row.get("exec_state", "Pre-breakout"),
                C_res.get("wide_and_loose", False))
            row["score"]  = comp["composite_score"]
            row["rating"] = comp["rating"]


# ── VCP Panel UI ──────────────────────────────────────────────────────────────

# Table column indices
_VC_RANK, _VC_SYM, _VC_CO, _VC_PRICE  = 0, 1, 2, 3
_VC_SCORE, _VC_RATING, _VC_STATE       = 4, 5, 6
_VC_PAT, _VC_NC, _VC_PIVOT, _VC_STOP   = 7, 8, 9, 10
_VC_RISK, _VC_T, _VC_C, _VC_V, _VC_P, _VC_R = 11, 12, 13, 14, 15, 16
_VC_NUM_COLS = 17


class VCPPanel(QWidget):
    plan_requested  = Signal(list)       # → TradePlannerPanel.receive_vcp_results
    trade_requested = Signal(str, str)   # (symbol, side)
    send_to_wl      = Signal(list)       # list of symbols → WatchlistPanel

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker  = None
        self._results = []
        self._symbols_from_watchlist = []
        self._detail_sym = ""
        self._setup_ui()

    # ── Public ────────────────────────────────────────────────────────────────

    def set_symbols(self, symbols: list):
        """Receive symbols from watchlist."""
        self._symbols_from_watchlist = [s for s in symbols if s]

    # ── UI build ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_ctrl_bar())
        root.addWidget(self._build_progress_bar())
        root.addWidget(self._build_filter_bar())
        spl = QSplitter(Qt.Horizontal)
        spl.setStyleSheet(f"QSplitter::handle{{background:{C['border']};width:1px;}}")
        spl.addWidget(self._build_table())
        spl.addWidget(self._build_detail())
        spl.setSizes([860, 380])
        root.addWidget(spl, 1)

    def _build_header(self) -> QFrame:
        h = QFrame(); h.setFixedHeight(52)
        h.setStyleSheet(f"background:{C['card']}; border-bottom:1px solid {C['border']};")
        lay = QHBoxLayout(h); lay.setContentsMargins(16, 0, 16, 0)
        t = QLabel("🌀  VCP Screener — US"); t.setStyleSheet(
            f"color:{C['yellow']}; font-size:16px; font-weight:700;")
        lay.addWidget(t); lay.addStretch()
        info = QLabel("Minervini Volatility Contraction Pattern  |  yfinance data  |  US only")
        info.setStyleSheet(f"color:{C['sec']}; font-size:11px;")
        lay.addWidget(info)
        return h

    def _build_ctrl_bar(self) -> QFrame:
        bar = QFrame(); bar.setFixedHeight(48)
        bar.setStyleSheet(f"background:{C['bg']}; border-bottom:1px solid {C['hover']};")
        lay = QHBoxLayout(bar); lay.setContentsMargins(12, 0, 12, 0); lay.setSpacing(8)

        self._btn_sp500 = QPushButton("S&P 500 (50)")
        self._btn_qqq   = QPushButton("QQQ (30)")
        for b, lst in [(self._btn_sp500, _SP500_SAMPLE), (self._btn_qqq, _QQQ_SAMPLE)]:
            b.setFixedHeight(30); b.setStyleSheet(self._btn_ss(C['hover'], C['accent']))
            b.clicked.connect(lambda _, l=lst: self._sym_input.setText(", ".join(l)))
            lay.addWidget(b)

        self._btn_watchlist = QPushButton("From Watchlist")
        self._btn_watchlist.setFixedHeight(30)
        self._btn_watchlist.setStyleSheet(self._btn_ss(C['hover'], C['yellow']))
        self._btn_watchlist.clicked.connect(self._load_watchlist)
        lay.addWidget(self._btn_watchlist)

        lay.addStretch()

        self._sym_input = QLineEdit()
        self._sym_input.setPlaceholderText("Custom symbols (comma-separated)…")
        self._sym_input.setFixedHeight(30); self._sym_input.setFixedWidth(300)
        self._sym_input.setStyleSheet(
            f"QLineEdit{{background:{C['card']};border:1px solid {C['border']};"
            f"color:{C['text']};border-radius:4px;padding:0 8px;font-size:12px;}}")
        lay.addWidget(self._sym_input)

        self._btn_run  = QPushButton("Run VCP Scan")
        self._btn_stop = QPushButton("Stop")
        self._btn_run.setFixedHeight(30)
        self._btn_stop.setFixedHeight(30)
        self._btn_run.setStyleSheet(self._btn_ss(C['green'], C['green'], text=C['bg'], bold=True))
        self._btn_stop.setStyleSheet(self._btn_ss(C['red'], C['red'], text="#fff", bold=True))
        self._btn_stop.setEnabled(False)
        self._btn_run.clicked.connect(self._run_scan)
        self._btn_stop.clicked.connect(self._stop_scan)
        lay.addWidget(self._btn_run); lay.addWidget(self._btn_stop)

        self._btn_plan = QPushButton("▶  Plan Trades")
        self._btn_plan.setFixedHeight(30)
        self._btn_plan.setEnabled(False)
        self._btn_plan.setStyleSheet(self._btn_ss(C['yellow'], C['yellow'], text=C['bg'], bold=True))
        self._btn_plan.setToolTip("Send VCP results to Trade Planner tab")
        self._btn_plan.clicked.connect(self._send_to_planner)
        lay.addWidget(self._btn_plan)
        return bar

    def _build_progress_bar(self) -> QFrame:
        bar = QFrame(); bar.setFixedHeight(32)
        bar.setStyleSheet(f"background:{C['bg']};")
        lay = QHBoxLayout(bar); lay.setContentsMargins(12, 4, 12, 4)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100); self._progress.setValue(0)
        self._progress.setFixedHeight(12)
        self._progress.setStyleSheet(
            f"QProgressBar{{background:{C['hover']};border:none;border-radius:6px;}}"
            f"QProgressBar::chunk{{background:{C['yellow']};border-radius:6px;}}")
        self._log_lbl = QLabel("")
        self._log_lbl.setStyleSheet(f"color:{C['sec']};font-size:11px;min-width:340px;")
        lay.addWidget(self._progress, 1); lay.addWidget(self._log_lbl)
        return bar

    def _build_filter_bar(self) -> QFrame:
        bar = QFrame(); bar.setFixedHeight(32)
        bar.setStyleSheet(f"background:{C['bg']};border-bottom:1px solid {C['hover']};")
        lay = QHBoxLayout(bar); lay.setContentsMargins(12, 0, 12, 0); lay.setSpacing(8)
        lbl = QLabel("Filter:"); lbl.setStyleSheet(f"color:{C['sec']};font-size:12px;")
        lay.addWidget(lbl)
        self._filter_rating = QComboBox()
        self._filter_rating.addItems(["All Ratings","Textbook VCP","Strong VCP",
                                       "Good VCP","Developing VCP","Weak VCP","No VCP"])
        self._filter_rating.setFixedHeight(24)
        self._filter_rating.setStyleSheet(self._combo_ss())
        self._filter_rating.currentTextChanged.connect(self._apply_filter)
        lay.addWidget(self._filter_rating)
        self._filter_state = QComboBox()
        self._filter_state.addItems(["All States","Pre-breakout","Breakout",
                                      "Early-post-breakout","Extended","Overextended",
                                      "Damaged","Invalid"])
        self._filter_state.setFixedHeight(24)
        self._filter_state.setStyleSheet(self._combo_ss())
        self._filter_state.currentTextChanged.connect(self._apply_filter)
        lay.addWidget(self._filter_state)
        lay.addStretch()
        lay.addWidget(QLabel("Results:"))
        self._count_lbl = QLabel("0")
        self._count_lbl.setStyleSheet(f"color:{C['text']};font-size:12px;font-weight:700;")
        lay.addWidget(self._count_lbl)
        lay.addSpacing(12)
        btn_exp = QPushButton("Export CSV")
        btn_exp.setFixedHeight(24)
        btn_exp.setStyleSheet(self._btn_ss(C['hover'], C['accent']))
        btn_exp.clicked.connect(self._export_csv)
        lay.addWidget(btn_exp)
        btn_wl = QPushButton("📋  SEND to WL")
        btn_wl.setFixedHeight(24)
        btn_wl.setStyleSheet(self._btn_ss(C['hover'], C['yellow']))
        btn_wl.setToolTip("Send selected symbol(s) to Watchlist")
        btn_wl.clicked.connect(self._send_to_wl)
        lay.addWidget(btn_wl)
        return bar

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, _VC_NUM_COLS)
        self._table.setHorizontalHeaderLabels([
            "#","Symbol","Company","Price","Score","Rating","State",
            "Pattern","NC","Pivot","Stop","Risk%","T","C","V","P","R"])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.verticalHeader().setDefaultSectionSize(26)
        h = self._table.horizontalHeader()
        h.setDefaultSectionSize(72)
        h.setSectionResizeMode(_VC_CO, QHeaderView.Stretch)
        for col, w in [(_VC_RANK,36),(_VC_SYM,76),(_VC_PRICE,84),
                        (_VC_SCORE,52),(_VC_NC,36),
                        (_VC_T,38),(_VC_C,38),(_VC_V,38),(_VC_P,38),(_VC_R,38)]:
            h.resizeSection(col, w)
        self._table.setStyleSheet(
            f"QTableWidget{{background:{C['bg']};color:{C['text']};"
            f"border:1px solid {C['border']};gridline-color:{C['hover']};font-size:12px;}}"
            f"QTableWidget::item:selected{{background:{C['accent']};}}")
        self._table.setSortingEnabled(True)
        h.setSortIndicatorShown(True)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        return self._table

    def _build_detail(self) -> QWidget:
        container = QWidget(); container.setMinimumWidth(320)
        vl = QVBoxLayout(container); vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(0)
        # ── Buy / Sell button bar ─────────────────────────────────────────────
        btn_bar = QFrame(); btn_bar.setFixedHeight(34)
        btn_bar.setStyleSheet(
            f"background:{C['card']};border-bottom:1px solid {C['border']};")
        bb_lay = QHBoxLayout(btn_bar)
        bb_lay.setContentsMargins(8, 4, 8, 4); bb_lay.setSpacing(6)
        bb_lay.addStretch()
        self._btn_vcp_buy = QPushButton("🟢  (Dry) Buy")
        self._btn_vcp_buy.setFixedHeight(24)
        self._btn_vcp_buy.setEnabled(False)
        self._btn_vcp_buy.setStyleSheet(
            f"QPushButton{{background:{C['green']};color:{C['bg']};border:none;"
            f"border-radius:4px;padding:2px 10px;font-size:11px;font-weight:600;}}"
            f"QPushButton:hover{{background:#238636;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['sec']};}}")
        self._btn_vcp_buy.clicked.connect(
            lambda: self.trade_requested.emit(self._detail_sym, "buy"))
        self._btn_vcp_sell = QPushButton("🔴  (Dry) Sell")
        self._btn_vcp_sell.setFixedHeight(24)
        self._btn_vcp_sell.setEnabled(False)
        self._btn_vcp_sell.setStyleSheet(
            f"QPushButton{{background:{C['red']};color:#fff;border:none;"
            f"border-radius:4px;padding:2px 10px;font-size:11px;font-weight:600;}}"
            f"QPushButton:hover{{background:#b91c1c;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['sec']};}}")
        self._btn_vcp_sell.clicked.connect(
            lambda: self.trade_requested.emit(self._detail_sym, "sell"))
        bb_lay.addWidget(self._btn_vcp_buy)
        bb_lay.addWidget(self._btn_vcp_sell)
        vl.addWidget(btn_bar)
        # ── Detail text area ──────────────────────────────────────────────────
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setStyleSheet(
            f"background:{C['bg']};color:{C['text']};border:none;font-size:12px;")
        vl.addWidget(self._detail, 1)
        return container

    # ── Actions ───────────────────────────────────────────────────────────────

    def _load_watchlist(self):
        syms = self._symbols_from_watchlist
        if syms:
            self._sym_input.setText(", ".join(syms))
        else:
            self._sym_input.setPlaceholderText("No watchlist symbols — add symbols in Watchlist tab")

    def _get_symbols(self) -> list:
        raw = self._sym_input.text().strip()
        if not raw: return []
        return [s.strip().upper() for s in raw.replace(";",",").split(",") if s.strip()]

    def _run_scan(self):
        symbols = self._get_symbols()
        if not symbols:
            QMessageBox.warning(self, "No Symbols", "Enter or load symbols first."); return
        if not _yf:
            QMessageBox.critical(self, "Missing Dependency",
                "yfinance is required for VCP scanning.\n\nInstall: pip install yfinance"); return

        self._results = []; self._table.setSortingEnabled(False); self._table.setRowCount(0)
        self._detail.clear(); self._detail_sym = ""
        self._btn_vcp_buy.setEnabled(False); self._btn_vcp_sell.setEnabled(False)
        self._count_lbl.setText("0"); self._progress.setValue(0)

        self._worker = _VCPWorker(symbols)
        self._worker.progress.connect(lambda p, s: self._progress.setValue(p))
        self._worker.row_ready.connect(self._on_row_ready)
        self._worker.log_message.connect(lambda m: self._log_lbl.setText(m[:80]))
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()
        self._btn_run.setEnabled(False); self._btn_stop.setEnabled(True)

    def _stop_scan(self):
        if self._worker: self._worker.cancel()
        self._btn_run.setEnabled(True); self._btn_stop.setEnabled(False)
        self._log_lbl.setText("Scan cancelled.")

    def _on_row_ready(self, row: dict):
        if row.get("phase_skipped") or not row.get("price"): return
        self._results.append(row)
        self._insert_row(row)
        self._count_lbl.setText(str(self._table.rowCount()))

    def _on_finished(self, results: list):
        self._btn_run.setEnabled(True); self._btn_stop.setEnabled(False)
        self._progress.setValue(100)
        self._results = sorted(results, key=lambda r: r.get("score", 0), reverse=True)
        self._apply_filter()
        n_plan = len([r for r in self._results if r.get("pivot")])
        self._btn_plan.setEnabled(n_plan > 0)
        self._log_lbl.setText(f"Scan complete — {len(self._results)} results, {n_plan} plannable")
        self._save_scan_json()

    def _on_error(self, msg: str):
        self._btn_run.setEnabled(True); self._btn_stop.setEnabled(False)
        self._log_lbl.setText(f"ERROR: {msg[:100]}")

    def _send_to_planner(self):
        if not self._results: return
        self._save_scan_json()
        self.plan_requested.emit(self._results)

    def _save_scan_json(self):
        if not self._results: return
        import json as _json
        from datetime import datetime as _dt
        ts   = _dt.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(_VCP_SCAN_DIR, f"vcp_{ts}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(self._results, f, indent=2, default=str)
        except Exception:
            pass

    # ── Table helpers ─────────────────────────────────────────────────────────

    def _apply_filter(self):
        rf = self._filter_rating.currentText()
        sf = self._filter_state.currentText()
        filt = [r for r in self._results
                if (rf == "All Ratings" or r.get("rating") == rf)
                and (sf == "All States"  or r.get("exec_state") == sf)]
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for row in sorted(filt, key=lambda r: r.get("score", 0), reverse=True):
            self._insert_row(row)
        self._count_lbl.setText(str(self._table.rowCount()))
        self._table.setSortingEnabled(True)

    def _insert_row(self, row: dict):
        r = self._table.rowCount(); self._table.insertRow(r)
        rating  = row.get("rating", "No VCP")
        state   = row.get("exec_state", "—")
        fg, bg  = _VCP_RATING_COLORS.get(rating, (C['text'], C['bg']))
        price   = row.get("price"); pivot = row.get("pivot"); stop = row.get("stop")
        risk    = row.get("risk_pct")

        def cell(v, align=Qt.AlignCenter):
            it = QTableWidgetItem("" if v is None else str(v))
            it.setTextAlignment(align); return it

        def sc(v):
            it = QTableWidgetItem(f"{int(v)}")
            it.setTextAlignment(Qt.AlignCenter)
            col = C['green'] if v >= 70 else C['yellow'] if v >= 50 else C['red'] if v >= 30 else C['sec']
            it.setForeground(QColor(col)); return it

        self._table.setItem(r, _VC_RANK,  cell(r+1))
        self._table.setItem(r, _VC_SYM,   cell(row.get("symbol",""), Qt.AlignLeft|Qt.AlignVCenter))
        self._table.setItem(r, _VC_CO,    cell(row.get("company",""), Qt.AlignLeft|Qt.AlignVCenter))
        self._table.setItem(r, _VC_PRICE, cell(f"${price:,.2f}" if price else "—"))

        si = QTableWidgetItem(f"{row.get('score',0):.1f}" if not row.get("error") else "ERR")
        si.setTextAlignment(Qt.AlignCenter)
        si.setBackground(QColor(bg)); si.setForeground(QColor(fg))
        si.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self._table.setItem(r, _VC_SCORE, si)

        ri = QTableWidgetItem(rating if not row.get("error") else f"Err:{row['error'][:20]}")
        ri.setForeground(QColor(fg)); ri.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(r, _VC_RATING, ri)

        sti = QTableWidgetItem(state); sti.setTextAlignment(Qt.AlignCenter)
        sti.setForeground(QColor(_VCP_STATE_FG.get(state, C['text'])))
        self._table.setItem(r, _VC_STATE, sti)

        self._table.setItem(r, _VC_PAT,   cell(row.get("pattern_type","—")))
        self._table.setItem(r, _VC_NC,    cell(row.get("contractions",0)))
        self._table.setItem(r, _VC_PIVOT, cell(f"${pivot:,.2f}" if pivot else "—"))
        self._table.setItem(r, _VC_STOP,  cell(f"${stop:,.2f}"  if stop  else "—"))
        self._table.setItem(r, _VC_RISK,  cell(f"{risk:.1f}%"   if risk  else "—"))

        for col, key in [(_VC_T,"T"),(_VC_C,"C"),(_VC_V,"V"),(_VC_P,"P"),(_VC_R,"R")]:
            self._table.setItem(r, col, sc(row.get(key, 0)))

        self._table.item(r, _VC_RANK).setData(Qt.UserRole, row)

    def _on_row_selected(self):
        r = self._table.currentRow()
        if r < 0: return
        it = self._table.item(r, _VC_RANK)
        if it:
            row = it.data(Qt.UserRole)
            if row: self._show_detail(row)

    def _send_to_wl(self):
        """Emit selected row symbol(s) to Watchlist."""
        rows = self._table.selectionModel().selectedRows()
        syms = []
        for idx in rows:
            it = self._table.item(idx.row(), _VC_SYM)
            if it and it.text(): syms.append(it.text())
        if not syms:
            r = self._table.currentRow()
            it = self._table.item(r, _VC_SYM)
            if it and it.text(): syms = [it.text()]
        if syms:
            self.send_to_wl.emit(syms)

    def _show_detail(self, row: dict):
        sym   = row.get("symbol",""); co = row.get("company","")
        self._detail_sym = sym
        self._btn_vcp_buy.setEnabled(bool(sym))
        self._btn_vcp_sell.setEnabled(bool(sym))
        price = row.get("price");   score = row.get("score",0)
        rating = row.get("rating","No VCP"); state = row.get("exec_state","—")
        fg, _  = _VCP_RATING_COLORS.get(rating, (C['text'], C['bg']))
        pivot  = row.get("pivot");  stop = row.get("stop");  risk = row.get("risk_pct")
        T_d    = row.get("T_detail", {}); C_d = row.get("C_detail", {})
        V_d    = row.get("V_detail", {}); R_d = row.get("R_detail", {})

        def pf(v): return f"${v:,.2f}" if v is not None else "—"
        def pct(v): return f"{v:.1f}%" if v is not None else "—"
        def sc_span(v):
            col = C['green'] if v >= 70 else C['yellow'] if v >= 50 else C['red'] if v >= 30 else C['sec']
            return f'<span style="color:{col};font-weight:700;">{int(v)}</span>'

        crit_rows = "".join(
            f"<tr><td>{'✅' if cv['passed'] else '❌'}</td>"
            f"<td style='color:{C['sec']};font-size:10px;padding:1px 4px;'>{cv['detail']}</td></tr>"
            for cv in T_d.get("criteria", {}).values()
        )
        contrac_rows = "".join(
            f"<tr><td style='color:{C['sec']};padding:2px 4px;'>T{i}</td>"
            f"<td style='padding:2px 4px;'>{c.get('depth_pct',0):.1f}%</td>"
            f"<td style='padding:2px 4px;'>{c.get('duration_days',0)}d</td></tr>"
            for i, c in enumerate(C_d.get("contractions",[]), 1)
        ) or f"<tr><td colspan='3' style='color:{C['sec']};padding:4px;'>No contractions</td></tr>"
        rs_rows = "".join(
            f"<tr><td style='color:{C['sec']};padding:2px 4px;'>{pd.get('label','')}</td>"
            f"<td style='color:{'#3fb950' if pd.get('relative_pct',0)>=0 else '#f85149'};padding:2px 4px;'>"
            f"{pd.get('relative_pct',0):+.1f}%</td></tr>"
            for pd in R_d.get("period_details", [])
        )
        html = f"""<div style='font-family:Segoe UI,Arial;padding:10px;background:{C['bg']};'>
<div style='color:{fg};font-size:15px;font-weight:700;'>{sym} — {co}</div>
<div style='color:{C['sec']};font-size:11px;margin:2px 0 8px;'>
  Price: {pf(price)} | Score: {score:.1f} | {rating}</div>
<table width='100%' style='background:{C['card']};border-radius:5px;margin-bottom:8px;'>
  <tr><td style='padding:4px 8px;color:{C['sec']};'>State</td>
      <td style='color:{_VCP_STATE_FG.get(state,C['text'])};font-weight:700;'>{state}</td>
      <td style='padding:4px 8px;color:{C['sec']};'>Pattern</td>
      <td>{row.get('pattern_type','—')}</td></tr>
  <tr><td style='padding:4px 8px;color:{C['sec']};'>Pivot</td><td>{pf(pivot)}</td>
      <td style='padding:4px 8px;color:{C['sec']};'>Stop</td>
      <td style='color:{C['red']};'>{pf(stop)}</td></tr>
  <tr><td style='padding:4px 8px;color:{C['sec']};'>Risk</td>
      <td style='color:{C['red']};'>{pct(risk)}</td>
      <td style='padding:4px 8px;color:{C['sec']};'>Contracs</td>
      <td>{row.get('contractions',0)}</td></tr>
</table>
<div style='color:{C['accent']};font-size:11px;font-weight:700;margin:6px 0 2px;'>Component Scores</div>
<table width='100%' style='background:{C['card']};border-radius:5px;margin-bottom:8px;'>
  <tr><td style='padding:3px 8px;color:{C['sec']};'>T — Trend Template</td><td>{sc_span(row.get('T',0))}/100</td></tr>
  <tr><td style='padding:3px 8px;color:{C['sec']};'>C — Contraction Quality</td><td>{sc_span(row.get('C',0))}/100</td></tr>
  <tr><td style='padding:3px 8px;color:{C['sec']};'>V — Volume Pattern</td><td>{sc_span(row.get('V',0))}/100</td></tr>
  <tr><td style='padding:3px 8px;color:{C['sec']};'>P — Pivot Proximity</td><td>{sc_span(row.get('P',0))}/100</td></tr>
  <tr><td style='padding:3px 8px;color:{C['sec']};'>R — Relative Strength</td><td>{sc_span(row.get('R',0))}/100</td></tr>
</table>
<div style='color:{C['accent']};font-size:11px;font-weight:700;margin:6px 0 2px;'>Trend Template (7-Point)</div>
<table style='background:{C['card']};border-radius:5px;width:100%;margin-bottom:8px;'>{crit_rows}</table>
<div style='color:{C['accent']};font-size:11px;font-weight:700;margin:6px 0 2px;'>VCP Contractions</div>
<table style='background:{C['card']};border-radius:5px;width:100%;margin-bottom:8px;'>{contrac_rows}</table>
<div style='color:{C['accent']};font-size:11px;font-weight:700;margin:6px 0 2px;'>Relative Strength vs SPY</div>
<table style='background:{C['card']};border-radius:5px;width:100%;margin-bottom:4px;'>{rs_rows}</table>
</div>"""
        self._detail.setHtml(html)

    def _export_csv(self):
        if not self._results: return
        path, _ = QFileDialog.getSaveFileName(self, "Export VCP", "vcp_results.csv", "CSV (*.csv)")
        if not path: return
        import csv as _csv
        cols = ["symbol","company","price","score","rating","exec_state",
                "pattern_type","contractions","pivot","stop","risk_pct","T","C","V","P","R","error"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader(); w.writerows(self._results)

    # ── Style helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _btn_ss(bg, accent, text=None, bold=False):
        t = text or C['text']; w = "700" if bold else "400"
        return (f"QPushButton{{background:{bg};color:{t};border:1px solid {accent};"
                f"border-radius:4px;padding:0 12px;font-size:12px;font-weight:{w};}}"
                f"QPushButton:hover:!disabled{{background:{accent};color:#fff;}}"
                f"QPushButton:disabled{{color:{C['sec']};}}")

    @staticmethod
    def _combo_ss():
        return (f"QComboBox{{background:{C['card']};color:{C['text']};"
                f"border:1px solid {C['border']};border-radius:4px;padding:2px 6px;}}"
                f"QComboBox::drop-down{{border:none;}}"
                f"QComboBox QAbstractItemView{{background:{C['card']};color:{C['text']};"
                f"selection-background-color:{C['accent']};border:1px solid {C['border']};}}")


# ══════════════════════════════════════════════════════════════════════════════
# 14g.  TRADE PLANNER PANEL  —  Breakout trade planner (US / Alpaca)
# ══════════════════════════════════════════════════════════════════════════════

_TP_SCAN_DIR = _VCP_SCAN_DIR   # shared scan directory

_TP_DEFAULT_SETTINGS = {
    "account_size":           100_000.0,
    "base_risk_pct":          0.5,
    "max_position_pct":       10.0,
    "max_sector_pct":         30.0,
    "max_portfolio_heat_pct":  6.0,
    "target_r_multiple":       2.0,
    "stop_buffer_pct":         1.0,
    "max_chase_pct":           2.0,
    "pivot_buffer_pct":        0.1,
}

# ── Inline planning engine ────────────────────────────────────────────────────

def _tp_rating_band(rating: str) -> str:
    """Map VCP rating to trade band."""
    return {
        "Textbook VCP":   "textbook",
        "Strong VCP":     "strong",
        "Good VCP":       "good",
        "Developing VCP": "developing",
        "Weak VCP":       "weak",
    }.get(rating, "weak")


def _tp_sizing_multiplier(band: str) -> float:
    return {"textbook": 1.5, "strong": 1.25, "good": 1.0,
            "developing": 0.75, "weak": 0.5}.get(band, 0.5)


def _tp_derive_prices(price: float, pivot, last_low,
                       stop_buffer_pct: float, max_chase_pct: float,
                       pivot_buffer_pct: float) -> dict:
    """Derive signal_entry, worst_entry, stop_loss, from pivot + last_low."""
    if not pivot or not last_low:
        return {}
    signal_entry = round(pivot * (1 + pivot_buffer_pct / 100), 4)
    worst_entry  = round(pivot * (1 + max_chase_pct  / 100), 4)
    stop_loss    = round(last_low * (1 - stop_buffer_pct / 100), 4)
    return {
        "signal_entry": signal_entry,
        "worst_entry":  worst_entry,
        "stop_loss":    stop_loss,
    }


def _tp_size_position(account: float, base_risk_pct: float,
                       worst_entry: float, stop_loss: float,
                       sizing_mult: float, max_pos_pct: float) -> dict:
    """Calculate share count and position metrics."""
    if worst_entry <= stop_loss or stop_loss <= 0:
        return {"shares": 0, "position_value": 0, "risk_dollars": 0,
                "risk_pct_worst": 0, "sizing_multiplier": sizing_mult}
    risk_per_share = worst_entry - stop_loss
    risk_dollars_budget = account * (base_risk_pct / 100) * sizing_mult
    shares = int(risk_dollars_budget / risk_per_share)
    if shares < 1:
        return {"shares": 0, "position_value": 0, "risk_dollars": 0,
                "risk_pct_worst": 0, "sizing_multiplier": sizing_mult}
    # Cap to max position pct
    max_shares = int(account * max_pos_pct / 100 / worst_entry)
    shares = min(shares, max_shares)
    pos_val    = round(shares * worst_entry, 2)
    risk_dol   = round(shares * risk_per_share, 2)
    risk_pct   = round((worst_entry - stop_loss) / worst_entry * 100, 2)
    return {"shares": shares, "position_value": pos_val,
            "risk_dollars": risk_dol, "risk_pct_worst": risk_pct,
            "sizing_multiplier": sizing_mult}


def _tp_build_order_template(symbol: str, shares: int,
                              signal_entry: float, stop_loss: float,
                              target: float) -> dict:
    """Build Alpaca bracket order template."""
    return {
        "pre_place": {
            "symbol":      symbol,
            "qty":         shares,
            "side":        "buy",
            "type":        "stop",
            "time_in_force":"day",
            "stop_price":  round(signal_entry, 2),
            "order_class": "bracket",
            "stop_loss":   {"stop_price": round(stop_loss, 2)},
            "take_profit": {"limit_price": round(target, 2)},
        },
        "post_confirm": {
            "action": "monitor_and_adjust",
            "move_stop_to_breakeven_at": round(signal_entry * 1.02, 2),
            "take_partial_at_1r":        round(signal_entry + (signal_entry - stop_loss), 2),
        },
    }


def _tp_plan_trades(vcp_rows: list, settings: dict) -> dict:
    """Core planning engine: classify rows → actionable / revalidation / watchlist / rejected."""
    s = settings
    account       = s.get("account_size", 100_000)
    base_risk     = s.get("base_risk_pct", 0.5)
    max_pos_pct   = s.get("max_position_pct", 10.0)
    max_heat      = s.get("max_portfolio_heat_pct", 6.0)
    target_r      = s.get("target_r_multiple", 2.0)
    stop_buf      = s.get("stop_buffer_pct", 1.0)
    max_chase     = s.get("max_chase_pct", 2.0)
    pivot_buf     = s.get("pivot_buffer_pct", 0.1)

    actionable    = []
    revalidation  = []
    watchlist_out = []
    rejected      = []
    cumulative_heat = 0.0
    sector_heat: dict = {}

    for row in sorted(vcp_rows, key=lambda r: r.get("score", 0), reverse=True):
        if row.get("phase_skipped") or row.get("error") or not row.get("price"):
            rejected.append({"symbol": row.get("symbol","?"), "market": "us",
                             "reason": row.get("error") or row.get("phase_skipped","No data")})
            continue

        price  = row["price"]
        pivot  = row.get("pivot")
        stop_p = row.get("stop")
        rating = row.get("rating", "No VCP")
        state  = row.get("exec_state", "Pre-breakout")
        score  = row.get("score", 0)
        band   = _tp_rating_band(rating)

        # Need a pivot to plan
        if not pivot:
            rejected.append({"symbol": row.get("symbol","?"), "market": "us",
                             "reason": "No pivot detected"}); continue

        # Derive last_low from stop (reverse the buffer)
        last_low = stop_p / (1 - stop_buf/100) if stop_p else pivot * 0.95

        prices = _tp_derive_prices(price, pivot, last_low, stop_buf, max_chase, pivot_buf)
        if not prices:
            rejected.append({"symbol": row.get("symbol","?"), "market": "us",
                             "reason": "Price derivation failed"}); continue

        sig = prices["signal_entry"]; worst = prices["worst_entry"]
        stop = prices["stop_loss"]

        risk_pct_trade = (worst - stop) / worst * 100 if worst > stop else 999
        if risk_pct_trade > 8.0:
            rejected.append({"symbol": row.get("symbol","?"), "market": "us",
                             "reason": f"Risk {risk_pct_trade:.1f}% > 8% max"}); continue

        mult = _tp_sizing_multiplier(band)
        sizing = _tp_size_position(account, base_risk, worst, stop, mult, max_pos_pct)
        if sizing["shares"] < 1:
            rejected.append({"symbol": row.get("symbol","?"), "market": "us",
                             "reason": "Position size < 1 share"}); continue

        target = round(sig + (sig - stop) * target_r, 4)
        heat_add = sizing["risk_dollars"] / account * 100

        # Watchlist: pre-breakout or developing
        if state == "Pre-breakout" and band in ("developing","weak"):
            watchlist_out.append({
                "symbol": row.get("symbol","?"), "company_name": row.get("company",""),
                "market": "us", "composite_score": score, "rating_band": band,
                "execution_state": state, "pivot_price": pivot, "stop_loss": stop,
                "alert_trigger": f"Price > ${sig:.2f} on 1.5x+ RVOL",
            }); continue

        # Revalidation: breakout but need to verify
        if state == "Breakout":
            max_entry = round(pivot * (1 + max_chase/100), 2)
            revalidation.append({
                "symbol": row.get("symbol","?"), "company_name": row.get("company",""),
                "market": "us", "composite_score": score, "rating_band": band,
                "execution_state": state, "pivot": pivot, "current_price": price,
                "max_entry_price": max_entry, "stop_loss": stop,
            }); continue

        # Skip if heat would be exceeded
        if cumulative_heat + heat_add > max_heat:
            rejected.append({"symbol": row.get("symbol","?"), "market": "us",
                             "reason": f"Portfolio heat {cumulative_heat+heat_add:.1f}% > {max_heat}% limit"}); continue

        cumulative_heat += heat_add
        tmpl = _tp_build_order_template(row.get("symbol","?"), sizing["shares"],
                                         sig, stop, target)

        actionable.append({
            "symbol":          row.get("symbol","?"),
            "company_name":    row.get("company",""),
            "market":          "us",
            "sector":          row.get("sector","—"),
            "composite_score": score,
            "rating_band":     band,
            "execution_state": state,
            "plan_valid_for":  "Today's session only",
            "trade_plan": {
                "signal_entry":      sig,
                "worst_entry":       worst,
                "stop_loss":         stop,
                "target_price":      target,
                "risk_pct_worst":    risk_pct_trade,
                "reward_risk_ratio": target_r,
                "shares":            sizing["shares"],
                "position_value":    sizing["position_value"],
                "risk_dollars":      sizing["risk_dollars"],
                "cumulative_heat_pct": round(cumulative_heat, 2),
                "sizing_multiplier": mult,
            },
            "order_templates": tmpl,
        })

    summary = {
        "actionable_count":    len(actionable),
        "revalidation_count":  len(revalidation),
        "watchlist_count":     len(watchlist_out),
        "rejected_count":      len(rejected),
        "cumulative_heat_pct": round(cumulative_heat, 2),
        "total_risk_dollars":  round(sum(o["trade_plan"]["risk_dollars"] for o in actionable), 2),
        "total_risk_pct":      round(cumulative_heat, 2),
    }
    return {
        "market":           "us",
        "actionable_orders": actionable,
        "revalidation":      revalidation,
        "watchlist":         watchlist_out,
        "rejected":          rejected,
        "summary":           summary,
    }


class _TPWorker(QThread):
    progress     = Signal(int, str)
    log_message  = Signal(str)
    result_ready = Signal(dict)
    error        = Signal(str)

    def __init__(self, vcp_rows=None, json_path=None, settings=None, parent=None):
        super().__init__(parent)
        self._rows     = vcp_rows or []
        self._path     = json_path
        self._settings = settings or {}

    def run(self):
        try:
            import json as _json
            if self._path:
                self.log_message.emit(f"Loading {self._path}…")
                with open(self._path, encoding="utf-8") as f:
                    self._rows = _json.load(f)
            self.progress.emit(10, "Planning…")
            self.log_message.emit(f"Planning {len(self._rows)} VCP rows…")
            plans = _tp_plan_trades(self._rows, self._settings)
            self.progress.emit(100, "Done")
            self.result_ready.emit(plans)
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()[:600]}")


# ── Settings frame ────────────────────────────────────────────────────────────

class _TPSettingsFrame(QFrame):
    settings_changed = Signal()

    _SPIN = (f"QDoubleSpinBox{{background:{C['card']};color:{C['text']};"
             f"border:1px solid {C['border']};border-radius:4px;padding:2px 6px;font-size:12px;}}"
             f"QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{{background:{C['hover']};border:none;width:14px;}}")
    _BTN  = (f"QPushButton{{background:{C['card']};color:{C['text']};"
             f"border:1px solid {C['border']};border-radius:4px;padding:4px 8px;font-size:12px;}}"
             f"QPushButton:hover{{background:{C['hover']};}}")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(230)
        self.setStyleSheet(f"QFrame{{background:{C['card']};border-right:1px solid {C['border']};}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(8)

        title_bar = QHBoxLayout()
        lbl_title = QLabel("Settings")
        lbl_title.setStyleSheet("color:#ffffff; font-weight:600; font-size:11px;")
        title_bar.addWidget(lbl_title)

        toggle_btn = QPushButton("«")
        toggle_btn.setToolTip("Collapse/Expand Sidebar")
        toggle_btn.setCursor(Qt.PointingHandCursor)
        toggle_btn.setStyleSheet("QPushButton { color:#00f0ff; font-weight:bold; border:none; background:transparent; font-size:16px; min-width:24px; max-width:24px; } QPushButton:hover { color:#00a8ff; }")
        title_bar.addWidget(toggle_btn)
        lay.addLayout(title_bar)

        content_w = QWidget()
        content_w.setStyleSheet("background:transparent;")
        content_lay = QVBoxLayout(content_w)
        content_lay.setContentsMargins(0, 0, 0, 0); content_lay.setSpacing(8)
        lay.addWidget(content_w)

        def toggle_sidebar():
            if content_w.isVisible():
                content_w.hide()
                lbl_title.hide()
                self.setFixedWidth(35)
                toggle_btn.setText("»")
            else:
                content_w.show()
                lbl_title.show()
                self.setFixedWidth(230)
                toggle_btn.setText("«")
        toggle_btn.clicked.connect(toggle_sidebar)

        def grp(title):
            gb = QGroupBox(title)
            gb.setStyleSheet(
                f"QGroupBox{{color:{C['sec']};font-size:11px;font-weight:600;"
                f"border:1px solid {C['border']};border-radius:4px;"
                f"margin-top:8px;padding-top:8px;background:{C['card']};}}"
                f"QGroupBox::title{{subcontrol-origin:margin;left:8px;}}")
            QVBoxLayout(gb).setContentsMargins(6,6,6,6)
            return gb

        def spin(label, lo, hi, default, dec, parent_gb):
            row_w = QWidget(); row_w.setStyleSheet("background:transparent;")
            rl = QHBoxLayout(row_w); rl.setContentsMargins(0,0,0,0)
            lb = QLabel(label); lb.setStyleSheet(f"color:{C['sec']};font-size:11px;")
            sp = QDoubleSpinBox(); sp.setRange(lo, hi); sp.setValue(default)
            sp.setDecimals(dec); sp.setSingleStep(0.1 if dec>0 else 1000)
            sp.setStyleSheet(self._SPIN); sp.setFixedWidth(88)
            rl.addWidget(lb); rl.addStretch(); rl.addWidget(sp)
            parent_gb.layout().addWidget(row_w)
            return sp

        # Account
        acct_gb = grp("Account (US)")
        self.account_size = spin("Account Size", 1_000, 100_000_000, 100_000, 0, acct_gb)
        self.base_risk    = spin("Base Risk %",  0.1,   5.0,          0.5,    2, acct_gb)
        content_lay.addWidget(acct_gb)

        # Position limits
        pos_gb = grp("Position Limits")
        self.max_pos_pct    = spin("Max Pos %",    1.0,  50.0, 10.0, 1, pos_gb)
        self.max_sector_pct = spin("Max Sector %", 5.0, 100.0, 30.0, 1, pos_gb)
        self.max_heat_pct   = spin("Max Heat %",   1.0,  20.0,  6.0, 1, pos_gb)
        content_lay.addWidget(pos_gb)

        # Trade params
        trade_gb = grp("Trade Parameters")
        self.target_r     = spin("Target R",       0.5, 10.0, 2.0, 1, trade_gb)
        self.stop_buffer  = spin("Stop Buffer %",  0.1,  5.0, 1.0, 1, trade_gb)
        self.max_chase    = spin("Max Chase %",    0.5,  5.0, 2.0, 1, trade_gb)
        self.pivot_buffer = spin("Pivot Buffer %", 0.0,  2.0, 0.1, 2, trade_gb)
        content_lay.addWidget(trade_gb)

        content_lay.addStretch()

    def get_settings(self) -> dict:
        return {
            "account_size":           self.account_size.value(),
            "base_risk_pct":          self.base_risk.value(),
            "max_position_pct":       self.max_pos_pct.value(),
            "max_sector_pct":         self.max_sector_pct.value(),
            "max_portfolio_heat_pct": self.max_heat_pct.value(),
            "target_r_multiple":      self.target_r.value(),
            "stop_buffer_pct":        self.stop_buffer.value(),
            "max_chase_pct":          self.max_chase.value(),
            "pivot_buffer_pct":       self.pivot_buffer.value(),
            "market": "us", "currency_symbol": "$",
        }


# ── Detail frame ──────────────────────────────────────────────────────────────

class _TPDetailFrame(QFrame):
    submit_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(290); self.setMaximumWidth(380)
        self.setStyleSheet(f"QFrame{{background:{C['card']};border-left:1px solid {C['border']};}}")
        self._order_tmpl = None
        lay = QVBoxLayout(self); lay.setContentsMargins(10,10,10,10); lay.setSpacing(6)

        title = QLabel("Trade Detail")
        title.setStyleSheet(f"color:{C['accent']};font-size:13px;font-weight:700;")
        lay.addWidget(title)

        self._summary = QTextEdit(); self._summary.setReadOnly(True)
        self._summary.setMaximumHeight(180)
        self._summary.setStyleSheet(
            f"QTextEdit{{background:{C['bg']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;font-size:12px;}}")
        lay.addWidget(self._summary)

        tmpl_lbl = QLabel("Order Template")
        tmpl_lbl.setStyleSheet(f"color:{C['sec']};font-size:11px;font-weight:600;")
        lay.addWidget(tmpl_lbl)

        self._tmpl_tabs = QTabWidget()
        self._tmpl_tabs.setStyleSheet(
            f"QTabWidget::pane{{border:1px solid {C['border']};background:{C['bg']};}}"
            f"QTabBar::tab{{background:{C['card']};color:{C['sec']};padding:4px 10px;font-size:11px;}}"
            f"QTabBar::tab:selected{{background:{C['bg']};color:{C['text']};"
            f"border-bottom:2px solid {C['yellow']};}}")
        self._pre_edit  = self._json_view()
        self._post_edit = self._json_view()
        self._tmpl_tabs.addTab(self._pre_edit,  "Pre-Place")
        self._tmpl_tabs.addTab(self._post_edit, "Post-Confirm")
        lay.addWidget(self._tmpl_tabs, 1)

        btn_row = QHBoxLayout()
        self._copy_btn = QPushButton("Copy JSON")
        self._copy_btn.setStyleSheet(
            f"QPushButton{{background:{C['card']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;padding:4px 10px;font-size:11px;}}"
            f"QPushButton:hover{{background:{C['hover']};}}")
        self._copy_btn.clicked.connect(self._copy_json)
        self._submit_btn = QPushButton("Submit to Alpaca")
        self._submit_btn.setStyleSheet(
            f"QPushButton{{background:{C['hover']};color:{C['accent']};"
            f"border:1px solid {C['accent']};border-radius:4px;padding:4px 10px;font-size:11px;}}"
            f"QPushButton:hover{{background:{C['accent']};color:#fff;}}"
            f"QPushButton:disabled{{background:{C['card']};color:{C['sec']};"
            f"border-color:{C['border']};}}")
        self._submit_btn.setEnabled(False)
        self._submit_btn.clicked.connect(self._on_submit)
        btn_row.addWidget(self._copy_btn); btn_row.addWidget(self._submit_btn)
        lay.addLayout(btn_row)
        self.clear()

    def _json_view(self):
        te = QTextEdit(); te.setReadOnly(True)
        te.setFont(QFont("Consolas,Courier New", 10))
        te.setStyleSheet(f"QTextEdit{{background:{C['bg']};color:{C['accent']};"
                         f"border:none;font-size:11px;}}")
        return te

    def clear(self):
        self._summary.setPlainText("Select a row to view trade detail.")
        self._pre_edit.setPlainText(""); self._post_edit.setPlainText("")
        self._order_tmpl = None; self._submit_btn.setEnabled(False)

    def show_actionable(self, order: dict, paper: bool = True):
        tp = order.get("trade_plan", {})
        lines = [
            f"{'='*34}",
            f"  {order['symbol']}  —  {order.get('company_name','')}",
            f"  {order.get('rating_band','').title()} ({order.get('composite_score',0):.0f})  |  {order.get('execution_state','')}",
            f"{'='*34}",
            f"  Signal Entry : ${tp.get('signal_entry',0):.2f}",
            f"  Worst Entry  : ${tp.get('worst_entry',0):.2f}",
            f"  Stop Loss    : ${tp.get('stop_loss',0):.2f}",
            f"  Risk (worst) : {tp.get('risk_pct_worst',0):.1f}%",
            f"  Target ({tp.get('reward_risk_ratio',2)}R): ${tp.get('target_price',0):.2f}",
            f"  —",
            f"  Shares       : {tp.get('shares',0):,}",
            f"  Position     : ${tp.get('position_value',0):,.2f}",
            f"  Risk $       : ${tp.get('risk_dollars',0):,.2f}",
            f"  Heat (cumul) : {tp.get('cumulative_heat_pct',0):.2f}%",
            f"  Sizing mult  : {tp.get('sizing_multiplier',1.0)}x",
            f"{'='*34}",
            f"  Mode: {'PAPER' if paper else 'LIVE ⚠️'}",
        ]
        self._summary.setPlainText("\n".join(lines))
        tmpl = order.get("order_templates", {})
        self._order_tmpl = tmpl
        import json as _json
        self._pre_edit.setPlainText(_json.dumps(tmpl.get("pre_place",{}), indent=2))
        self._post_edit.setPlainText(_json.dumps(tmpl.get("post_confirm",{}), indent=2))
        self._submit_btn.setEnabled(True)

    def show_watchlist(self, item: dict):
        lines = [
            f"{'='*34}",
            f"  {item.get('symbol','')}  —  {item.get('company_name','')}",
            f"  Score: {item.get('composite_score',0):.0f}  Band: {item.get('rating_band','').title()}",
            f"  State: {item.get('execution_state','')}",
            f"{'='*34}",
            f"  Pivot  : ${item.get('pivot_price',0):.2f}",
            f"  Stop   : ${item.get('stop_loss',0):.2f}",
            f"  Alert  : {item.get('alert_trigger','')}",
            "",
            "  WATCHLIST — wait for breakout above pivot",
            "  on 1.5x+ relative volume before entering.",
        ]
        self._summary.setPlainText("\n".join(lines))
        self._pre_edit.setPlainText(""); self._post_edit.setPlainText("")
        self._order_tmpl = None; self._submit_btn.setEnabled(False)

    def show_revalidation(self, item: dict):
        pivot   = item.get("pivot") or item.get("pivot_price", 0)
        current = item.get("current_price", item.get("price", 0))
        lines = [
            f"{'='*34}",
            f"  {item.get('symbol','')}  REVALIDATION",
            f"  Score: {item.get('composite_score',0):.0f}  State: Breakout",
            f"{'='*34}",
            f"  Pivot        : ${pivot:.2f}",
            f"  Current      : ${current:.2f}",
            f"  Max Entry    : ${item.get('max_entry_price',0):.2f}",
            "",
            "  Confirm 5-min bar close > pivot,",
            "  RVOL ≥ 1.5x, price ≤ max entry.",
            "  Then place limit at max entry price.",
        ]
        self._summary.setPlainText("\n".join(lines))
        self._pre_edit.setPlainText(""); self._post_edit.setPlainText("")
        self._order_tmpl = None; self._submit_btn.setEnabled(False)

    def _copy_json(self):
        idx  = self._tmpl_tabs.currentIndex()
        edit = self._pre_edit if idx == 0 else self._post_edit
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.clipboard().setText(edit.toPlainText())

    def _on_submit(self):
        if self._order_tmpl:
            self.submit_requested.emit(self._order_tmpl.get("pre_place", {}))


# ── Trade Planner Panel ───────────────────────────────────────────────────────

# Actionable table columns
_AC_RANK, _AC_SYM, _AC_CO    = 0, 1, 2
_AC_SCORE, _AC_BAND, _AC_STATE = 3, 4, 5
_AC_SIG, _AC_WORST, _AC_STOP = 6, 7, 8
_AC_RISK, _AC_TARGET, _AC_SHARES, _AC_POSVAL, _AC_RISKD, _AC_HEAT = 9,10,11,12,13,14
_AC_COLS = 15
_AC_HDRS = ["#","Symbol","Company","Score","Band","State",
            "Signal","Worst","Stop","Risk%","Target","Shares","Pos$","Risk$","Heat%"]

_WL_SYM, _WL_CO, _WL_SCORE, _WL_BAND, _WL_PIVOT, _WL_STOP, _WL_ALERT = 0,1,2,3,4,5,6
_WL_COLS = 7
_WL_HDRS = ["Symbol","Company","Score","Band","Pivot","Stop","Alert / Note"]

_BAND_FG = {"textbook":"#00d4aa","strong":"#58a6ff","good":"#a5d6a7",
            "developing":"#e3b341","weak":"#8b949e"}


class TradePlannerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._plans    = None
        self._vcp_rows = []
        self._worker   = None
        self._build_ui()

    # ── Public ────────────────────────────────────────────────────────────────

    def receive_vcp_results(self, rows: list):
        self._vcp_rows = rows
        n = len([r for r in rows if r.get("pivot")])
        self._vcp_lbl.setText(f"{len(rows)} VCP rows loaded ({n} with pivot)")
        self._vcp_lbl.setStyleSheet(f"color:{C['green']};font-size:11px;")
        self._plan_btn.setEnabled(True)

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        self._settings = _TPSettingsFrame()
        root.addWidget(self._settings)
        spl = QSplitter(Qt.Horizontal)
        spl.setStyleSheet(f"QSplitter::handle{{background:{C['border']};width:2px;}}")
        spl.addWidget(self._build_centre())
        spl.addWidget(self._build_detail())
        spl.setSizes([860, 310])
        root.addWidget(spl, 1)

    def _build_centre(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{C['bg']};")
        lay = QVBoxLayout(w); lay.setContentsMargins(12,12,12,8); lay.setSpacing(8)

        # Title row
        tr = QHBoxLayout()
        tl = QLabel("Breakout Trade Planner")
        tl.setStyleSheet(f"color:{C['yellow']};font-size:16px;font-weight:700;")
        tr.addWidget(tl); tr.addStretch()
        self._vcp_lbl = QLabel("No VCP results loaded")
        self._vcp_lbl.setStyleSheet(f"color:{C['sec']};font-size:11px;")
        tr.addWidget(self._vcp_lbl)
        lay.addLayout(tr)

        # Control bar
        ctrl = QHBoxLayout(); ctrl.setSpacing(8)
        self._plan_btn = QPushButton("Plan from VCP Results")
        self._plan_btn.setEnabled(False); self._plan_btn.setFixedHeight(30)
        self._plan_btn.setStyleSheet(
            f"QPushButton{{background:{C['hover']};color:{C['yellow']};"
            f"border:1px solid {C['yellow']};border-radius:4px;padding:0 14px;"
            f"font-size:13px;font-weight:600;}}"
            f"QPushButton:hover:!disabled{{background:{C['yellow']};color:{C['bg']};}}"
            f"QPushButton:disabled{{background:{C['card']};color:{C['sec']};"
            f"border-color:{C['border']};}}")
        self._plan_btn.clicked.connect(self._run_planning)
        ctrl.addWidget(self._plan_btn)

        # Scan file row
        self._scan_combo = QComboBox(); self._scan_combo.setFixedHeight(30)
        self._scan_combo.setStyleSheet(
            f"QComboBox{{background:{C['card']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;padding:0 8px;font-size:12px;}}"
            f"QComboBox::drop-down{{border:none;width:20px;}}"
            f"QComboBox QAbstractItemView{{background:{C['card']};color:{C['text']};"
            f"border:1px solid {C['border']};selection-background-color:{C['hover']};}}")
        ctrl.addWidget(self._scan_combo, 1)

        for label, tip, slot in [
            ("⟳", "Refresh scan list",     self._refresh_scans),
            ("Load Latest",  "Load most recent scan", self._load_latest),
            ("Load Selected","Load selected scan",    self._load_selected),
            ("Browse…",      "Browse for JSON",       lambda: self._load_json()),
        ]:
            b = QPushButton(label); b.setFixedHeight(30)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton{{background:{C['card']};color:{C['text']};"
                f"border:1px solid {C['border']};border-radius:4px;padding:0 8px;font-size:12px;}}"
                f"QPushButton:hover{{background:{C['hover']};}}")
            b.clicked.connect(slot); ctrl.addWidget(b)

        ctrl.addStretch()
        self._exp_json_btn = QPushButton("Export JSON")
        self._exp_csv_btn  = QPushButton("Export CSV")
        for b in (self._exp_json_btn, self._exp_csv_btn):
            b.setEnabled(False); b.setFixedHeight(26)
            b.setStyleSheet(
                f"QPushButton{{background:{C['card']};color:{C['text']};"
                f"border:1px solid {C['border']};border-radius:4px;padding:2px 10px;font-size:11px;}}"
                f"QPushButton:hover:!disabled{{background:{C['hover']};}}"
                f"QPushButton:disabled{{color:{C['sec']};}}")
        self._exp_json_btn.clicked.connect(self._export_json)
        self._exp_csv_btn.clicked.connect(self._export_csv)
        ctrl.addWidget(self._exp_json_btn); ctrl.addWidget(self._exp_csv_btn)
        lay.addLayout(ctrl)

        # Progress bar
        self._progress = QProgressBar(); self._progress.setRange(0,100)
        self._progress.setValue(0); self._progress.setFixedHeight(4)
        self._progress.setStyleSheet(
            f"QProgressBar{{background:{C['hover']};border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{C['yellow']};border-radius:2px;}}")
        self._progress.setVisible(False)
        lay.addWidget(self._progress)

        # Summary cards
        card_row = QHBoxLayout(); card_row.setSpacing(8)
        self._cards = {}
        for key, label, col in [
            ("actionable","Actionable",C['green']),
            ("revalidation","Revalidation",C['accent']),
            ("watchlist","Watchlist",C['yellow']),
            ("rejected","Rejected",C['sec']),
            ("heat","Portfolio Heat",C['red']),
            ("total_risk","Total Risk$",C['text']),
        ]:
            card = QWidget(); card.setFixedHeight(52)
            card.setStyleSheet(
                f"background:{C['card']};border:1px solid {C['border']};border-radius:5px;")
            cl = QVBoxLayout(card); cl.setContentsMargins(8,3,8,3); cl.setSpacing(1)
            lb = QLabel(label); lb.setStyleSheet(f"color:{C['sec']};font-size:10px;font-weight:600;")
            vl = QLabel("—"); vl.setObjectName("val")
            vl.setStyleSheet(f"color:{col};font-size:17px;font-weight:700;")
            cl.addWidget(lb); cl.addWidget(vl)
            self._cards[key] = card; card_row.addWidget(card)
        lay.addLayout(card_row)

        # Plan tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            f"QTabWidget::pane{{border:1px solid {C['border']};background:{C['bg']};}}"
            f"QTabBar::tab{{background:{C['card']};color:{C['sec']};padding:6px 14px;font-size:12px;}}"
            f"QTabBar::tab:hover{{background:{C['hover']};color:{C['text']};}}"
            f"QTabBar::tab:selected{{background:{C['bg']};color:{C['text']};"
            f"border-bottom:2px solid {C['yellow']};}}")
        self._act_tbl   = self._make_actionable_table()
        self._reval_tbl = self._make_wl_table()
        self._wl_tbl    = self._make_wl_table()
        self._rej_tbl   = self._make_rej_table()

        def wrapped(t):
            w2 = QWidget(); w2.setStyleSheet(f"background:{C['bg']};")
            QVBoxLayout(w2).setContentsMargins(0,0,0,0)
            w2.layout().addWidget(t); return w2

        self._tabs.addTab(wrapped(self._act_tbl),  "Actionable (0)")
        self._tabs.addTab(wrapped(self._reval_tbl),"Revalidation (0)")
        self._tabs.addTab(wrapped(self._wl_tbl),   "Watchlist (0)")
        self._tabs.addTab(wrapped(self._rej_tbl),  "Rejected (0)")
        self._act_tbl.itemSelectionChanged.connect(
            lambda: self._on_select(self._act_tbl, "actionable"))
        self._reval_tbl.itemSelectionChanged.connect(
            lambda: self._on_select(self._reval_tbl, "revalidation"))
        self._wl_tbl.itemSelectionChanged.connect(
            lambda: self._on_select(self._wl_tbl, "watchlist"))
        lay.addWidget(self._tabs, 1)

        # Log
        self._log = QTextEdit(); self._log.setReadOnly(True); self._log.setFixedHeight(64)
        self._log.setStyleSheet(
            f"QTextEdit{{background:{C['card']};color:{C['sec']};"
            f"border:1px solid {C['border']};border-radius:4px;"
            f"font-size:11px;font-family:'Consolas','Courier New';}}")
        lay.addWidget(self._log)

        self._refresh_scans()
        return w

    def _build_detail(self) -> QWidget:
        self._detail = _TPDetailFrame()
        self._detail.submit_requested.connect(self._submit_order)
        return self._detail

    # ── Table builders ────────────────────────────────────────────────────────

    def _make_actionable_table(self) -> QTableWidget:
        t = QTableWidget(0, _AC_COLS)
        t.setHorizontalHeaderLabels(_AC_HDRS)
        t.setStyleSheet(
            f"QTableWidget{{background:{C['bg']};color:{C['text']};"
            f"gridline-color:{C['hover']};border:none;selection-background-color:{C['card']};}}"
            f"QTableWidget::item{{padding:2px 6px;}}"
            f"QTableWidget::item:selected{{background:{C['card']};color:{C['text']};}}")
        t.horizontalHeader().setStyleSheet(
            f"QHeaderView::section{{background:{C['card']};color:{C['sec']};"
            f"font-size:11px;font-weight:600;border:none;"
            f"border-bottom:1px solid {C['border']};padding:4px 6px;}}")
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setSelectionMode(QTableWidget.SingleSelection)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.verticalHeader().setVisible(False); t.setShowGrid(False)
        h = t.horizontalHeader()
        h.setSectionResizeMode(_AC_CO, QHeaderView.Stretch)
        for col in (_AC_RANK,_AC_SYM,_AC_SCORE,_AC_BAND,_AC_STATE,_AC_SIG,_AC_WORST,
                    _AC_STOP,_AC_RISK,_AC_TARGET,_AC_SHARES,_AC_POSVAL,_AC_RISKD,_AC_HEAT):
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        return t

    def _make_wl_table(self) -> QTableWidget:
        t = QTableWidget(0, _WL_COLS)
        t.setHorizontalHeaderLabels(_WL_HDRS)
        t.setStyleSheet(
            f"QTableWidget{{background:{C['bg']};color:{C['text']};"
            f"gridline-color:{C['hover']};border:none;}}"
            f"QTableWidget::item{{padding:2px 6px;}}"
            f"QTableWidget::item:selected{{background:{C['card']};}}")
        t.horizontalHeader().setStyleSheet(
            f"QHeaderView::section{{background:{C['card']};color:{C['sec']};"
            f"font-size:11px;font-weight:600;border:none;"
            f"border-bottom:1px solid {C['border']};padding:4px 6px;}}")
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.verticalHeader().setVisible(False); t.setShowGrid(False)
        h = t.horizontalHeader()
        h.setSectionResizeMode(_WL_CO,    QHeaderView.Stretch)
        h.setSectionResizeMode(_WL_ALERT, QHeaderView.Stretch)
        return t

    def _make_rej_table(self) -> QTableWidget:
        t = QTableWidget(0, 2)
        t.setHorizontalHeaderLabels(["Symbol","Reason"])
        t.setStyleSheet(
            f"QTableWidget{{background:{C['bg']};color:{C['text']};"
            f"gridline-color:{C['hover']};border:none;}}"
            f"QTableWidget::item{{padding:2px 6px;}}")
        t.horizontalHeader().setStyleSheet(
            f"QHeaderView::section{{background:{C['card']};color:{C['sec']};"
            f"font-size:11px;border:none;border-bottom:1px solid {C['border']};padding:4px;}}")
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.verticalHeader().setVisible(False); t.setShowGrid(False)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        return t

    # ── Planning ──────────────────────────────────────────────────────────────

    def _run_planning(self):
        if self._worker and self._worker.isRunning(): return
        settings = self._settings.get_settings()
        self._log_msg(f"Planning — Acct: ${settings['account_size']:,.0f} | Risk: {settings['base_risk_pct']}%")
        self._progress.setValue(0); self._progress.setVisible(True)
        self._plan_btn.setEnabled(False)
        self._worker = _TPWorker(vcp_rows=self._vcp_rows, settings=settings)
        self._worker.progress.connect(lambda p, m: (self._progress.setValue(p), self._log_msg(m)))
        self._worker.log_message.connect(self._log_msg)
        self._worker.result_ready.connect(self._on_plans_ready)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _refresh_scans(self):
        import glob as _glob
        self._scan_combo.blockSignals(True); self._scan_combo.clear()
        files = sorted(_glob.glob(os.path.join(_TP_SCAN_DIR, "vcp_*.json")), reverse=True)
        if files:
            self._scan_combo.addItem("— select saved VCP scan —")
            for f in files:
                self._scan_combo.addItem(os.path.basename(f), userData=f)
        else:
            self._scan_combo.addItem("— no saved scans found —")
        self._scan_combo.blockSignals(False)

    def _load_latest(self):
        import glob as _glob
        files = sorted(_glob.glob(os.path.join(_TP_SCAN_DIR, "vcp_*.json")), reverse=True)
        if not files: self._log_msg("No saved scans found."); return
        self._refresh_scans()
        self._scan_combo.setCurrentIndex(1 if self._scan_combo.count() > 1 else 0)
        self._load_json(files[0])

    def _load_selected(self):
        idx  = self._scan_combo.currentIndex()
        path = self._scan_combo.itemData(idx)
        if not path: self._log_msg("Select a scan from the dropdown first."); return
        self._load_json(path)

    def _load_json(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open VCP JSON", _TP_SCAN_DIR, "JSON (*.json)")
        if not path: return
        settings = self._settings.get_settings()
        self._progress.setValue(0); self._progress.setVisible(True)
        self._plan_btn.setEnabled(False)
        self._worker = _TPWorker(json_path=path, settings=settings)
        self._worker.progress.connect(lambda p, m: (self._progress.setValue(p), self._log_msg(m)))
        self._worker.log_message.connect(self._log_msg)
        self._worker.result_ready.connect(self._on_plans_ready)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_plans_ready(self, plans: dict):
        self._plans = plans; self._progress.setValue(100); self._progress.setVisible(False)
        self._plan_btn.setEnabled(True); self._populate(plans)
        self._exp_json_btn.setEnabled(True); self._exp_csv_btn.setEnabled(True)
        s = plans["summary"]
        self._log_msg(f"Done — {s['actionable_count']} actionable | "
                      f"{s['revalidation_count']} reval | {s['watchlist_count']} watchlist | "
                      f"Heat: {s['cumulative_heat_pct']:.1f}%")

    def _on_error(self, msg: str):
        self._progress.setVisible(False); self._plan_btn.setEnabled(True)
        self._log_msg(f"ERROR: {msg[:120]}")

    # ── Table population ──────────────────────────────────────────────────────

    def _update_card(self, key: str, val: str):
        card = self._cards.get(key)
        if card:
            vl = card.findChild(QLabel, "val")
            if vl: vl.setText(val)

    def _populate(self, plans: dict):
        s = plans["summary"]
        self._update_card("actionable",   str(s["actionable_count"]))
        self._update_card("revalidation", str(s["revalidation_count"]))
        self._update_card("watchlist",    str(s["watchlist_count"]))
        self._update_card("rejected",     str(s["rejected_count"]))
        self._update_card("heat",         f"{s['cumulative_heat_pct']:.1f}%")
        self._update_card("total_risk",   f"${s['total_risk_dollars']:,.0f}")

        self._fill_actionable(plans.get("actionable_orders",[]))
        self._fill_wl(self._reval_tbl, plans.get("revalidation",[]))
        self._fill_wl(self._wl_tbl,    plans.get("watchlist",[]))
        self._fill_rej(plans.get("rejected",[]))

        self._tabs.setTabText(0, f"Actionable ({s['actionable_count']})")
        self._tabs.setTabText(1, f"Revalidation ({s['revalidation_count']})")
        self._tabs.setTabText(2, f"Watchlist ({s['watchlist_count']})")
        self._tabs.setTabText(3, f"Rejected ({s['rejected_count']})")
        self._detail.clear()

    def _ci(self, text, fg=None, bg=None, align=Qt.AlignLeft|Qt.AlignVCenter):
        it = QTableWidgetItem(str(text))
        it.setForeground(QColor(fg or C['text']))
        if bg: it.setBackground(QColor(bg))
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        it.setTextAlignment(align)
        return it

    def _ni(self, val, fmt="{:.2f}", fg=None):
        try: txt = fmt.format(float(val)) if val is not None else "—"
        except: txt = str(val) if val is not None else "—"
        it = QTableWidgetItem(txt)
        it.setForeground(QColor(fg or C['text']))
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        it.setTextAlignment(Qt.AlignRight|Qt.AlignVCenter)
        return it

    def _fill_actionable(self, orders: list):
        t = self._act_tbl; t.setRowCount(0)
        for i, o in enumerate(orders):
            t.insertRow(i); tp = o.get("trade_plan",{})
            band = o.get("rating_band","")
            fg   = _BAND_FG.get(band, C['text'])
            state_fg = _VCP_STATE_FG.get(o.get("execution_state",""), C['text'])
            t.setItem(i, _AC_RANK,  self._ci(i+1, C['sec'], align=Qt.AlignCenter))
            t.setItem(i, _AC_SYM,   self._ci(o["symbol"], C['accent']))
            t.setItem(i, _AC_CO,    self._ci(o.get("company_name","")))
            t.setItem(i, _AC_SCORE, self._ni(o.get("composite_score",0), "{:.0f}", C['yellow']))
            t.setItem(i, _AC_BAND,  self._ci(band.title(), fg, align=Qt.AlignCenter))
            t.setItem(i, _AC_STATE, self._ci(o.get("execution_state",""), state_fg, align=Qt.AlignCenter))
            t.setItem(i, _AC_SIG,   self._ni(tp.get("signal_entry"), "${:.2f}"))
            t.setItem(i, _AC_WORST, self._ni(tp.get("worst_entry"),  "${:.2f}"))
            t.setItem(i, _AC_STOP,  self._ni(tp.get("stop_loss"),    "${:.2f}", C['red']))
            t.setItem(i, _AC_RISK,  self._ni(tp.get("risk_pct_worst"), "{:.1f}%", C['red']))
            t.setItem(i, _AC_TARGET,self._ni(tp.get("target_price"), "${:.2f}", C['green']))
            t.setItem(i, _AC_SHARES,self._ni(tp.get("shares"),       "{:,.0f}"))
            t.setItem(i, _AC_POSVAL,self._ni(tp.get("position_value"),"{:,.0f}"))
            t.setItem(i, _AC_RISKD, self._ni(tp.get("risk_dollars"), "{:,.0f}", C['red']))
            t.setItem(i, _AC_HEAT,  self._ni(tp.get("cumulative_heat_pct"),"{:.1f}%"))
            t.setRowHeight(i, 25)
            t.item(i, _AC_RANK).setData(Qt.UserRole, o)
        t.resizeColumnsToContents()

    def _fill_wl(self, t: QTableWidget, items: list):
        t.setRowCount(0)
        for i, item in enumerate(items):
            t.insertRow(i)
            band  = item.get("rating_band",""); fg = _BAND_FG.get(band, C['text'])
            pivot = item.get("pivot_price") or item.get("pivot") or 0
            stop  = item.get("stop_loss") or item.get("stop") or 0
            alert = item.get("alert_trigger") or f"Price > ${pivot:.2f} on 1.5x RVOL"
            t.setItem(i, _WL_SYM,   self._ci(item.get("symbol",""), C['accent']))
            t.setItem(i, _WL_CO,    self._ci(item.get("company_name","")))
            t.setItem(i, _WL_SCORE, self._ni(item.get("composite_score",0),"{:.0f}",C['yellow']))
            t.setItem(i, _WL_BAND,  self._ci(band.title(), fg, align=Qt.AlignCenter))
            t.setItem(i, _WL_PIVOT, self._ni(pivot, "${:.2f}", C['green']))
            t.setItem(i, _WL_STOP,  self._ni(stop,  "${:.2f}", C['red']))
            t.setItem(i, _WL_ALERT, self._ci(alert, C['sec']))
            t.setRowHeight(i, 25)
            t.item(i, _WL_SYM).setData(Qt.UserRole, item)

    def _fill_rej(self, items: list):
        t = self._rej_tbl; t.setRowCount(0)
        for i, item in enumerate(items):
            t.insertRow(i)
            t.setItem(i, 0, self._ci(item.get("symbol",""), C['sec']))
            t.setItem(i, 1, self._ci(item.get("reason",""), "#6e7681"))
            t.setRowHeight(i, 24)

    # ── Row selection ─────────────────────────────────────────────────────────

    def _on_select(self, table: QTableWidget, kind: str):
        if not self._plans: return
        r = table.currentRow()
        if r < 0: return
        if kind == "actionable":
            orders = self._plans.get("actionable_orders",[])
            if r < len(orders): self._detail.show_actionable(orders[r])
        elif kind == "revalidation":
            items = self._plans.get("revalidation",[])
            if r < len(items): self._detail.show_revalidation(items[r])
        else:
            items = self._plans.get("watchlist",[])
            if r < len(items): self._detail.show_watchlist(items[r])

    # ── Alpaca submission ─────────────────────────────────────────────────────

    def _submit_order(self, template: dict):
        sym  = template.get("symbol","?")
        qty  = template.get("qty", 0)
        ret  = QMessageBox.question(
            self, "Confirm Paper Order",
            f"Submit PAPER order:\n  {sym}  {qty} shares\n"
            f"  Type: {template.get('type','?')}\n"
            f"  Stop: {template.get('stop_price','?')}\n"
            f"  TP:   {template.get('take_profit',{}).get('limit_price','?')}\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No)
        if ret != QMessageBox.Yes: return
        try:
            import requests as _req
            r = _req.post(
                f"{TRADING_URL}/v2/orders",
                headers={**HDR, "Content-Type": "application/json"},
                json=template, timeout=15)
            if r.status_code in (200, 201):
                data = r.json()
                QMessageBox.information(self, "Order Submitted",
                    f"Order ID: {data.get('id','?')}\n"
                    f"Status: {data.get('status','?')}")
                self._log_msg(f"Order submitted: {sym} × {qty} → {data.get('status','?')}")
            else:
                QMessageBox.critical(self, "Order Failed",
                    f"HTTP {r.status_code}: {r.text[:200]}")
                self._log_msg(f"Order FAILED {r.status_code}: {r.text[:80]}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self._log_msg(f"Order error: {e}")

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_json(self):
        if not self._plans: return
        path, _ = QFileDialog.getSaveFileName(self, "Save Plan JSON","","JSON (*.json)")
        if not path: return
        import json as _json
        with open(path,"w") as f: _json.dump(self._plans, f, indent=2, default=str)
        self._log_msg(f"Exported JSON: {path}")

    def _export_csv(self):
        if not self._plans: return
        path, _ = QFileDialog.getSaveFileName(self,"Save CSV","","CSV (*.csv)")
        if not path: return
        import csv as _csv
        with open(path,"w",newline="") as f:
            w = _csv.writer(f)
            w.writerow(["Symbol","Company","Score","Band","State",
                        "Signal","Worst","Stop","Risk%","Target",
                        "Shares","Position$","Risk$","Heat%"])
            for o in self._plans.get("actionable_orders",[]):
                tp = o.get("trade_plan",{})
                w.writerow([o["symbol"],o.get("company_name",""),
                            o.get("composite_score",0),o.get("rating_band",""),
                            o.get("execution_state",""),
                            tp.get("signal_entry",0),tp.get("worst_entry",0),
                            tp.get("stop_loss",0),tp.get("risk_pct_worst",0),
                            tp.get("target_price",0),tp.get("shares",0),
                            tp.get("position_value",0),tp.get("risk_dollars",0),
                            tp.get("cumulative_heat_pct",0)])
        self._log_msg(f"Exported CSV: {path}")

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log_msg(self, msg: str):
        from datetime import datetime as _dt
        self._log.append(f"[{_dt.now().strftime('%H:%M:%S')}] {msg}")
        self._log.ensureCursorVisible()


# ══════════════════════════════════════════════════════════════════════════════
# STOCK LENS  —  US single-stock deep dive  (ported from BreezePro)
# US only · yfinance data · AI via Gemini → Perplexity cascade
# ══════════════════════════════════════════════════════════════════════════════

_SL_DIR     = os.path.join(_ALP_DIR, "SecurityMaster")
_SL_CSV     = os.path.join(_SL_DIR,  "USStockMaster.csv")
_SL_RPT_DIR = os.path.join(_AN_RPT_DIR, "stock_lens")
_SL_CHT_DIR = os.path.join(_SL_RPT_DIR, "charts")
for _d in (_SL_DIR, _SL_RPT_DIR, _SL_CHT_DIR):
    os.makedirs(_d, exist_ok=True)

_SL_SYMBOLS: dict = {}   # {TICKER: "Name  Exchange"}


# ── Symbol management ─────────────────────────────────────────────────────────

def _sl_load_symbols() -> int:
    """Load USStockMaster.csv into _SL_SYMBOLS.  Returns count."""
    global _SL_SYMBOLS
    if not os.path.exists(_SL_CSV):
        return 0
    try:
        import csv as _csv_mod
        d: dict = {}
        with open(_SL_CSV, newline="", encoding="utf-8") as f:
            for row in _csv_mod.DictReader(f):
                tk = row.get("ticker", "").strip().upper()
                if tk:
                    d[tk] = f"{row.get('name', ''):<40}  {row.get('exchange', '')}"
        _SL_SYMBOLS = d
        return len(d)
    except Exception:
        return 0


def _sl_csv_needs_update() -> bool:
    if not os.path.exists(_SL_CSV):
        return True
    age = (datetime.now().timestamp() - os.path.getmtime(_SL_CSV)) / 86400
    return age >= 30


class _SLSymbolDownloadWorker(QThread):
    """Download SEC EDGAR company_tickers_exchange.json → USStockMaster.csv."""
    progress = Signal(str)
    finished = Signal(int, str)   # count, path_or_error

    def run(self):
        try:
            import requests as _req
            import csv as _csv_mod

            self.progress.emit("Downloading SEC EDGAR tickers…")
            url = "https://www.sec.gov/files/company_tickers_exchange.json"
            r = _req.get(url, timeout=30,
                         headers={"User-Agent": "AlpacaExplorer/1.0 (research)"})
            r.raise_for_status()
            data = r.json()

            keep_exchanges = {"Nasdaq", "NYSE", "NYSE MKT", "NYSE Arca", "CBOE"}
            fields_raw = data.get("fields", [])
            rows = []
            for item in data.get("data", []):
                d = dict(zip(fields_raw, item))
                exch = d.get("exchange", "") or ""
                if exch in keep_exchanges:
                    rows.append({
                        "ticker":   (d.get("ticker", "") or "").upper(),
                        "name":     d.get("name", ""),
                        "exchange": exch,
                        "cik":      d.get("cik", ""),
                    })

            self.progress.emit(f"Writing {len(rows):,} symbols…")
            os.makedirs(_SL_DIR, exist_ok=True)
            with open(_SL_CSV, "w", newline="", encoding="utf-8") as f:
                w = _csv_mod.DictWriter(f, fieldnames=["ticker", "name", "exchange", "cik"])
                w.writeheader()
                w.writerows(rows)

            _sl_load_symbols()
            self.finished.emit(len(rows), _SL_CSV)
        except Exception as e:
            self.finished.emit(-1, str(e))


# ── Technical analysis helpers ─────────────────────────────────────────────────

def _sf(x):
    """Safe float — returns None for NaN / Inf / None."""
    try:
        v = float(x)
        return None if (v != v or v == float("inf") or v == float("-inf")) else v
    except Exception:
        return None


def _fmt_money_sl(x) -> str:
    v = _sf(x)
    if v is None: return "—"
    if abs(v) >= 1e12: return f"${v / 1e12:.2f} T"
    if abs(v) >= 1e9:  return f"${v / 1e9:.2f} B"
    if abs(v) >= 1e6:  return f"${v / 1e6:.2f} M"
    return f"${v:,.2f}"


def _fmt_pct_sl(x, dp: int = 2) -> str:
    v = _sf(x)
    return "—" if v is None else f"{v * 100:.{dp}f}%"


def _fmt_num_sl(x, dp: int = 2) -> str:
    v = _sf(x)
    if v is None: return "—"
    if abs(v) >= 1e9: return f"{v / 1e9:.2f}B"
    if abs(v) >= 1e6: return f"{v / 1e6:.2f}M"
    if abs(v) >= 1e3: return f"{v / 1e3:.2f}K"
    return f"{v:.{dp}f}"


def _sl_compute_technicals(hist) -> dict:
    """Compute SMA/EMA/MACD/BB/RSI from a yfinance history DataFrame."""
    if hist is None or hist.empty:
        return {}
    try:
        close_col = "Close" if "Close" in hist.columns else "close"
        high_col  = "High"  if "High"  in hist.columns else "high"
        low_col   = "Low"   if "Low"   in hist.columns else "low"
        vol_col   = "Volume"if "Volume"in hist.columns else "volume"
        close = hist[close_col].astype(float)
        high  = hist[high_col].astype(float)  if high_col  in hist.columns else close
        low   = hist[low_col].astype(float)   if low_col   in hist.columns else close
        n     = len(close)
        last  = float(close.iloc[-1])

        def sma_last(p):
            return _sf(close.rolling(p, min_periods=1).mean().iloc[-1]) if n >= 1 else None

        def ema_s(p):
            return close.ewm(span=p, adjust=False).mean()

        sma20 = sma_last(20); sma50 = sma_last(50); sma200 = sma_last(200)
        ema9  = _sf(ema_s(9).iloc[-1])
        ema21 = _sf(ema_s(21).iloc[-1])

        # MACD 12 / 26 / 9
        ema12 = ema_s(12); ema26 = ema_s(26)
        macd_line = ema12 - ema26
        macd_sig  = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - macd_sig
        macd_v    = _sf(macd_line.iloc[-1])
        macd_sv   = _sf(macd_sig.iloc[-1])
        macd_hv   = _sf(macd_hist.iloc[-1])

        # Bollinger Bands ±2σ (20-period)
        bb_mid   = close.rolling(20, min_periods=1).mean()
        bb_std   = close.rolling(20, min_periods=1).std(ddof=0)
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_mid_v   = _sf(bb_mid.iloc[-1])
        bb_upper_v = _sf(bb_upper.iloc[-1])
        bb_lower_v = _sf(bb_lower.iloc[-1])
        if bb_upper_v and bb_lower_v and bb_upper_v != bb_lower_v:
            bb_sig = ("overbought" if last >= bb_upper_v else
                      "oversold"   if last <= bb_lower_v else "neutral")
        else:
            bb_sig = "neutral"

        # RSI-14
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14, min_periods=1).mean()
        loss  = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi_s = 100 - 100 / (1 + rs)
        rsi14 = _sf(rsi_s.iloc[-1])
        rsi_sig = ("overbought" if rsi14 and rsi14 > 70 else
                   "oversold"   if rsi14 and rsi14 < 30 else "neutral")

        # 52-week range
        hi52 = _sf(high.rolling(252, min_periods=1).max().iloc[-1])
        lo52 = _sf(low.rolling(252, min_periods=1).min().iloc[-1])
        pct_hi = ((last - hi52) / hi52 * 100) if hi52 else None
        pct_lo = ((last - lo52) / lo52 * 100) if lo52 else None

        def ret(bars):
            if n <= bars: return None
            prev = _sf(close.iloc[-(bars + 1)])
            return ((last - prev) / prev * 100) if prev else None

        # Trend classification
        a20  = sma20  and last > sma20
        a50  = sma50  and last > sma50
        a200 = sma200 and last > sma200
        if a20 and a50 and a200:
            trend = "Uptrend";   detail = "Above SMA20/50/200"; tc = C["green"]
        elif a50 and a200:
            trend = "Uptrend";   detail = "Above SMA50/200";    tc = "#a5d6a7"
        elif not a20 and not a50 and not a200:
            trend = "Downtrend"; detail = "Below SMA20/50/200"; tc = C["red"]
        elif not a50 and not a200:
            trend = "Downtrend"; detail = "Below SMA50/200";    tc = "#ef9a9a"
        else:
            trend = "Sideways";  detail = "Mixed MA alignment";  tc = C["yellow"]

        return {
            "last": last, "sma20": sma20, "sma50": sma50, "sma200": sma200,
            "ema9": ema9, "ema21": ema21,
            "macd": macd_v, "macd_signal": macd_sv, "macd_hist": macd_hv,
            "bb_mid": bb_mid_v, "bb_upper": bb_upper_v, "bb_lower": bb_lower_v,
            "bb_signal": bb_sig,
            "rsi14": rsi14, "rsi_signal": rsi_sig,
            "hi_52w": hi52, "lo_52w": lo52,
            "pct_from_hi52": pct_hi, "pct_from_lo52": pct_lo,
            "ret_1d": ret(1), "ret_1w": ret(5), "ret_1m": ret(21),
            "ret_3m": ret(63), "ret_6m": ret(126), "ret_1y": ret(252),
            "trend": trend, "trend_detail": detail, "trend_color": tc,
        }
    except Exception as e:
        return {"_error": str(e)}


def _sl_build_bull_bear(info: dict, tech: dict):
    """Return (bull_points, bear_points)."""
    bull, bear = [], []

    rev_g = _sf(info.get("revenueGrowth"))
    if rev_g is not None:
        (bull if rev_g > 0.08 else bear).append(
            f"Revenue growth {rev_g * 100:.1f}%{'  — accelerating' if rev_g > 0.20 else ''}")
    eps_g = _sf(info.get("earningsGrowth"))
    if eps_g is not None:
        (bull if eps_g > 0.05 else bear).append(f"EPS growth {eps_g * 100:.1f}%")
    gm = _sf(info.get("grossMargins"))
    if gm is not None:
        (bull if gm > 0.40 else (bear if gm < 0.15 else None) or []).append(
            f"Gross margin {gm * 100:.1f}%")
    nm = _sf(info.get("profitMargins"))
    if nm is not None:
        (bull if nm > 0.10 else (bear if nm < 0 else None) or []).append(
            f"Net margin {nm * 100:.1f}%")
    roe = _sf(info.get("returnOnEquity"))
    if roe is not None:
        (bull if roe > 0.15 else bear).append(f"ROE {roe * 100:.1f}%")
    de = _sf(info.get("debtToEquity"))
    if de is not None:
        (bear if de > 100 else (bull if de < 40 else None) or []).append(f"D/E {de:.1f}")
    fcf = _sf(info.get("freeCashflow"))
    if fcf is not None:
        (bull if fcf > 0 else bear).append(f"Free cash flow {_fmt_money_sl(fcf)}")
    pe = _sf(info.get("trailingPE"))
    if pe is not None:
        (bull if pe < 15 else (bear if pe > 40 else None) or []).append(f"P/E {pe:.1f}x")
    peg = _sf(info.get("trailingPegRatio") or info.get("pegRatio"))
    if peg is not None:
        (bull if 0 < peg < 1 else (bear if peg > 2 else None) or []).append(
            f"PEG {peg:.2f}")
    si = _sf(info.get("shortPercentOfFloat"))
    if si and si > 0.15:
        bear.append(f"High short interest: {si * 100:.1f}% of float")

    trend = tech.get("trend", "")
    if "Uptrend" in trend:
        bull.append(f"Technical: {trend} — {tech.get('trend_detail', '')}")
    elif "Downtrend" in trend:
        bear.append(f"Technical: {trend} — {tech.get('trend_detail', '')}")

    rsi = tech.get("rsi14"); rs = tech.get("rsi_signal", "")
    if rsi is not None:
        (bear if rs == "overbought" else (bull if rs == "oversold" else None) or []).append(
            f"RSI-14 {rsi:.1f} — {rs}")

    mh = tech.get("macd_hist")
    if mh is not None:
        (bull if mh > 0 else bear).append(
            f"MACD histogram {'positive' if mh > 0 else 'negative'} ({mh:.3f})")

    pct_hi = tech.get("pct_from_hi52")
    if pct_hi is not None:
        if pct_hi > -10:
            bull.append(f"Near 52w high ({pct_hi:.1f}% from peak)")
        elif pct_hi < -40:
            bear.append(f"Far from 52w high ({pct_hi:.1f}%)")

    return bull[:8], bear[:8]


def _sl_what_to_watch(info: dict) -> list:
    items = []
    sector = info.get("sector", "")
    if sector:
        items.append(f"Sector: {sector} — watch macro / rates impact")
    beta = _sf(info.get("beta"))
    if beta is not None:
        items.append(f"Beta {beta:.2f} — "
                     f"{'high' if beta > 1.5 else 'moderate' if beta > 0.8 else 'low'} volatility")
    rec = info.get("recommendationKey", "")
    if rec:
        items.append(f"Analyst consensus: {rec.replace('_', ' ').title()}")
    target = _sf(info.get("targetMeanPrice"))
    last   = _sf(info.get("currentPrice") or info.get("regularMarketPrice"))
    if target and last:
        upside = (target - last) / last * 100
        items.append(f"Analyst price target ${target:.2f} ({upside:+.1f}% upside)")
    div = _sf(info.get("dividendYield"))
    if div and div > 0:
        items.append(f"Dividend yield {div * 100:.2f}%")
    items.append("Watch volume on breakouts / breakdowns for conviction signals")
    return items[:6]


def _sl_compute_rating(data: dict):
    """
    Up to 13 signals (8 technical + 5 fundamental) → BUY / HOLD / EXIT.
    Returns (rating_str, score_int, color_hex).
    """
    info  = data.get("info", {})
    tech  = data.get("tech", {})
    score = 0

    # — Technical (8 signals) —
    if "Uptrend" in tech.get("trend", ""):                          score += 1
    if tech.get("macd_hist") and tech["macd_hist"] > 0:            score += 1
    if tech.get("rsi_signal") == "oversold":                       score += 1
    elif tech.get("rsi_signal") == "overbought":                   score -= 1
    if tech.get("bb_signal") == "oversold":                        score += 1
    elif tech.get("bb_signal") == "overbought":                    score -= 1
    last = tech.get("last")
    if last and tech.get("sma20") and last > tech["sma20"]:        score += 1
    if last and tech.get("sma50") and last > tech["sma50"]:        score += 1
    if last and tech.get("ema9") and tech.get("ema21"):
        if tech["ema9"] > tech["ema21"]:                           score += 1
    r1m = tech.get("ret_1m")
    if r1m is not None and r1m > 5:                                score += 1

    # — Fundamental (5 signals) —
    roe = _sf(info.get("returnOnEquity"))
    if roe is not None:
        if roe > 0.15:   score += 1
        elif roe < 0:    score -= 1
    gm = _sf(info.get("grossMargins"))
    if gm is not None:
        if gm > 0.40:    score += 1
        elif gm < 0.10:  score -= 1
    fcf = _sf(info.get("freeCashflow"))
    if fcf is not None:
        if fcf > 0:      score += 1
        else:            score -= 1
    rev_g = _sf(info.get("revenueGrowth"))
    if rev_g is not None:
        if rev_g > 0.08:      score += 1
        elif rev_g < -0.05:   score -= 1
    de = _sf(info.get("debtToEquity"))
    if de is not None:
        if de < 40:      score += 1
        elif de > 100:   score -= 1

    if score >= 5:
        return "BUY",  score, C["green"]
    elif score <= -5:
        return "EXIT", score, C["red"]
    else:
        return "HOLD", score, C["yellow"]


def _sl_render_chart(data: dict, out_path: str) -> bool:
    """Render 3-panel PNG chart (Price+SMAs+BB, Volume, RSI) via Agg backend."""
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        import matplotlib.ticker as _mticker

        hist   = data.get("history")
        tech   = data.get("tech", {})
        ticker = data.get("ticker", "?")
        if hist is None or hist.empty:
            return False

        h = hist.copy()
        h.columns = [c.lower() for c in h.columns]
        if "close" not in h.columns:
            return False

        cl   = h["close"].astype(float)
        n    = len(cl)
        x    = list(range(n))
        last = float(cl.iloc[-1])

        fig = Figure(figsize=(12, 8), facecolor="#0d1117")
        ax1 = fig.add_subplot(3, 1, 1, facecolor="#0d1117")
        ax2 = fig.add_subplot(3, 1, 2, facecolor="#0d1117", sharex=ax1)
        ax3 = fig.add_subplot(3, 1, 3, facecolor="#0d1117", sharex=ax1)
        fig.subplots_adjust(left=0.08, right=0.97, top=0.93, bottom=0.07, hspace=0.08)

        # ── Price + SMAs + BB ─────────────────────────────────────────────────
        ax1.plot(x, cl.values, color=C["accent"], linewidth=1.2, label="Close", zorder=3)
        for period, color, lw, lbl in [
            (20,  C["yellow"], 1.0, "SMA20"),
            (50,  "#e3b341",   1.0, "SMA50"),
            (200, C["red"],    1.3, "SMA200"),
        ]:
            if n >= period:
                vals = cl.rolling(period, min_periods=1).mean().values
                ax1.plot(x, vals, color=color, linewidth=lw,
                         label=lbl, alpha=0.85, zorder=2)

        bb_mid   = cl.rolling(20, min_periods=1).mean()
        bb_std   = cl.rolling(20, min_periods=1).std(ddof=0)
        bb_up    = (bb_mid + 2 * bb_std).values
        bb_lo    = (bb_mid - 2 * bb_std).values
        ax1.fill_between(x, bb_lo, bb_up, color=C["accent"], alpha=0.07)
        ax1.plot(x, bb_up, color=C["accent"], linewidth=0.6, linestyle="--", alpha=0.5)
        ax1.plot(x, bb_lo, color=C["accent"], linewidth=0.6, linestyle="--", alpha=0.5)

        rsi14 = tech.get("rsi14")
        trend = tech.get("trend", "")
        title = (f"{ticker}  |  ${last:,.2f}  |  {trend}  |  RSI-14: {rsi14:.1f}"
                 if rsi14 else f"{ticker}  |  ${last:,.2f}  |  {trend}")
        ax1.set_title(title, color=C["text"], fontsize=10, pad=5)
        ax1.tick_params(colors=C["sec"], labelsize=7)
        ax1.yaxis.set_major_formatter(
            _mticker.FuncFormatter(lambda v, _: f"${v:,.2f}"))
        for sp in ax1.spines.values(): sp.set_color(C["border"])
        ax1.grid(True, color="#161b22", linewidth=0.5)
        ax1.legend(loc="upper left", fontsize=7, facecolor=C["card"],
                   labelcolor=C["text"], framealpha=0.8)
        import matplotlib.pyplot as _plt
        _plt.setp(ax1.get_xticklabels(), visible=False)

        # ── Volume ────────────────────────────────────────────────────────────
        if "volume" in h.columns:
            vol = h["volume"].astype(float).values
            vcols = []
            for i in range(n):
                if i == 0:
                    vcols.append(C["accent"])
                else:
                    vcols.append(C["green"] if cl.iloc[i] >= cl.iloc[i - 1] else C["red"])
            ax2.bar(x, vol, color=vcols, alpha=0.55, width=0.8)
            vol_sma = h["volume"].astype(float).rolling(20, min_periods=1).mean().values
            ax2.plot(x, vol_sma, color=C["yellow"], linewidth=0.8)
        ax2.set_ylabel("Volume", color=C["sec"], fontsize=8)
        ax2.tick_params(colors=C["sec"], labelsize=7)
        ax2.yaxis.set_major_formatter(
            _mticker.FuncFormatter(lambda v, _: f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v:.0f}"))
        for sp in ax2.spines.values(): sp.set_color(C["border"])
        ax2.grid(True, color="#161b22", linewidth=0.5)
        _plt.setp(ax2.get_xticklabels(), visible=False)

        # ── RSI-14 ────────────────────────────────────────────────────────────
        delta  = cl.diff()
        gain   = delta.clip(lower=0).rolling(14, min_periods=1).mean()
        loss   = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
        rs_v   = gain / loss.replace(0, float("nan"))
        rsi_v  = (100 - 100 / (1 + rs_v)).values
        ax3.plot(x, rsi_v, color="#b36bff", linewidth=1.0, label="RSI-14")
        ax3.axhline(70, color=C["red"],   linewidth=0.7, linestyle="--", alpha=0.7)
        ax3.axhline(30, color=C["green"], linewidth=0.7, linestyle="--", alpha=0.7)
        ax3.fill_between(x, rsi_v, 70,
                         where=[v > 70 if v == v else False for v in rsi_v],
                         color=C["red"],   alpha=0.15)
        ax3.fill_between(x, rsi_v, 30,
                         where=[v < 30 if v == v else False for v in rsi_v],
                         color=C["green"], alpha=0.15)
        ax3.set_ylabel("RSI", color=C["sec"], fontsize=8)
        ax3.set_ylim(0, 100)
        ax3.tick_params(colors=C["sec"], labelsize=7)
        for sp in ax3.spines.values(): sp.set_color(C["border"])
        ax3.grid(True, color="#161b22", linewidth=0.5)

        # X-axis date labels
        step = max(1, n // 10)
        ticks, labels = [], []
        for tp in range(0, n, step):
            try:
                labels.append(str(h.index[tp])[:10])
            except Exception:
                labels.append("")
            ticks.append(tp)
        ax3.set_xticks(ticks)
        ax3.set_xticklabels(labels, rotation=30, ha="right",
                             color=C["sec"], fontsize=7)

        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        fig.savefig(out_path, facecolor=fig.get_facecolor(),
                    dpi=100, bbox_inches="tight")
        return True
    except Exception:
        return False


def _sl_build_ai_prompt(data: dict) -> str:
    ticker = data.get("ticker", "?")
    name   = data.get("name", ticker)
    info   = data.get("info", {})
    tech   = data.get("tech", {})
    bull   = data.get("bull_case", [])
    bear   = data.get("bear_case", [])
    rating, score, _ = _sl_compute_rating(data)
    last   = tech.get("last")
    return (
        f"You are a professional US equity analyst. Write a concise (300–400 word) "
        f"investment note for {name} ({ticker}). "
        f"Trade rating: {rating} (score {score}/13).\n\n"
        f"Key data:\n"
        f"• Sector: {info.get('sector', '—')}  |  Industry: {info.get('industry', '—')}\n"
        f"• Price: ${last:,.2f}" if last else f"• Price: —"
        f"  |  Trend: {tech.get('trend', '—')}\n"
        f"• P/E: {_sf(info.get('trailingPE'))}  "
        f"| ROE: {_fmt_pct_sl(info.get('returnOnEquity'))}  "
        f"| Rev Growth: {_fmt_pct_sl(info.get('revenueGrowth'))}\n"
        f"• Gross Margin: {_fmt_pct_sl(info.get('grossMargins'))}  "
        f"| FCF: {_fmt_money_sl(info.get('freeCashflow'))}\n\n"
        f"Bull case:\n" + "\n".join(f"• {b}" for b in bull) + "\n\n"
        f"Bear case:\n" + "\n".join(f"• {b}" for b in bear) + "\n\n"
        f"Cover: business summary, financial health, technicals, key risks, 12-month outlook. "
        f"End with a single investment thesis sentence."
    )


# ── Background workers ────────────────────────────────────────────────────────

class _SLWorker(QThread):
    """Fetch all data for a single US stock via yfinance."""
    progress = Signal(str)
    result   = Signal(dict)
    error    = Signal(str)

    def __init__(self, ticker: str, peers: list, lookback_days: int = 365,
                 parent=None):
        super().__init__(parent)
        self._ticker   = ticker.strip().upper()
        self._peers    = peers or []
        self._lookback = lookback_days
        self._abort    = False

    def abort(self): self._abort = True

    def run(self):
        sym = self._ticker
        try:
            if not _YF_OK:
                self.error.emit("yfinance not installed — pip install yfinance"); return
            import yfinance as _yf_mod

            self.progress.emit(f"Fetching {sym} info…")
            tkr  = _yf_mod.Ticker(sym)
            info = {}
            try:
                info = tkr.info or {}
            except Exception:
                pass
            if self._abort: return

            self.progress.emit(f"Fetching {sym} history ({self._lookback}d)…")
            hist = None
            try:
                hist = tkr.history(period=f"{self._lookback}d", interval="1d")
                if hist is None or hist.empty:
                    hist = tkr.history(period="1y", interval="1d")
            except Exception:
                pass
            if self._abort: return

            self.progress.emit("Computing technicals…")
            tech = (_sl_compute_technicals(hist)
                    if (hist is not None and not hist.empty) else {})
            if self._abort: return

            self.progress.emit("Fetching news…")
            news = []
            try:
                for item in (tkr.news or [])[:8]:
                    news.append({
                        "title":  item.get("title", ""),
                        "source": item.get("publisher", ""),
                        "url":    item.get("link", ""),
                    })
            except Exception:
                pass
            if self._abort: return

            self.progress.emit("Building analysis…")
            bull, bear = _sl_build_bull_bear(info, tech)
            wtw        = _sl_what_to_watch(info)

            self.result.emit({
                "ticker":        sym,
                "base_ticker":   sym,
                "name":          info.get("shortName") or info.get("longName") or sym,
                "market":        "US",
                "currency":      info.get("currency", "USD"),
                "exchange":      info.get("exchange", ""),
                "sector":        info.get("sector", ""),
                "industry":      info.get("industry", ""),
                "as_of":         datetime.now().strftime("%Y-%m-%d %H:%M"),
                "info":          info,
                "history":       hist,
                "news":          news,
                "tech":          tech,
                "peers":         self._peers,
                "bull_case":     bull,
                "bear_case":     bear,
                "what_to_watch": wtw,
            })
        except Exception as exc:
            import traceback
            self.error.emit(f"{sym}: {exc}\n{traceback.format_exc()[:500]}")


class _SLAIWorker(QThread):
    """Generate AI commentary via Gemini → Perplexity cascade."""
    commentary_ready = Signal(str, str)   # commentary, model_name
    error            = Signal(str)

    def __init__(self, data: dict, gemini_key: str, perplexity_key: str,
                 parent=None):
        super().__init__(parent)
        self._data     = data
        self._gem_key  = gemini_key
        self._pplx_key = perplexity_key

    def run(self):
        prompt = _sl_build_ai_prompt(self._data)

        if self._gem_key:
            for model in (
                "gemini-2.5-flash",
                "gemini-2.5-flash-lite-preview-06-17",
                "gemini-2.0-flash",
                "gemini-1.5-flash",
            ):
                try:
                    import google.generativeai as _genai
                    _genai.configure(api_key=self._gem_key)
                    resp = _genai.GenerativeModel(model).generate_content(prompt)
                    text = resp.text.strip()
                    if text:
                        self.commentary_ready.emit(text, model); return
                except Exception:
                    continue

        if self._pplx_key:
            try:
                import requests as _req
                r = _req.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={"Authorization": f"Bearer {self._pplx_key}",
                             "Content-Type": "application/json"},
                    json={"model": "sonar-pro",
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=60)
                r.raise_for_status()
                text = (r.json().get("choices", [{}])[0]
                         .get("message", {}).get("content", "").strip())
                if text:
                    self.commentary_ready.emit(text, "perplexity/sonar-pro"); return
            except Exception as e:
                self.error.emit(f"Perplexity error: {e}"); return

        self.error.emit(
            "No AI keys available — add GEMINI_API_KEY.txt or "
            "PERPLEXITY_API_KEY.txt to the ALLAPI/ folder.")


# ── Main Panel ─────────────────────────────────────────────────────────────────

class StockLensPanel(QWidget):
    """
    US Stock Lens — single-stock deep dive.
    Ported from BreezePro / breeze_stock_lens.py (US path only).
    Integrates with WatchlistPanel via set_symbols() and
    emits go_explore(str) to open a ticker in ExplorerPanel.
    """
    go_explore      = Signal(str)
    trade_requested = Signal(str, str)   # (symbol, side)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data:      dict | None             = None
        self._worker:    "_SLWorker | None"      = None
        self._ai_worker: "_SLAIWorker | None"    = None
        self._dl_worker: "_SLSymbolDownloadWorker | None" = None
        self._symbols:   list                    = []
        self._gem_key    = ""
        self._pplx_key   = ""
        self._load_creds()
        _sl_load_symbols()
        self._setup_ui()
        if _sl_csv_needs_update():
            self._start_symbol_download(silent=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_symbols(self, symbols: list):
        """Receive watchlist symbols."""
        self._symbols = symbols
        self._wl_cb.blockSignals(True)
        self._wl_cb.clear()
        self._wl_cb.addItem("— pick from watchlist —", None)
        for s in symbols:
            self._wl_cb.addItem(s, s)
        self._wl_cb.blockSignals(False)

    # ── Credentials ──────────────────────────────────────────────────────────

    def _load_creds(self):
        allapi = os.path.join(_ALP_DIR, "ALLAPI")
        for fname, attr in [("GEMINI_API_KEY.txt",     "_gem_key"),
                             ("PERPLEXITY_API_KEY.txt", "_pplx_key")]:
            p = os.path.join(allapi, fname)
            if os.path.exists(p):
                try:
                    setattr(self, attr, open(p).read().strip())
                except Exception:
                    pass

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_left_panel())
        sep = QFrame(); sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{C['border']};")
        root.addWidget(sep)
        root.addWidget(self._build_right_panel(), 1)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(280)
        panel.setStyleSheet(f"background:{C['card']};")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(6)

        title_bar = QHBoxLayout()
        title = QLabel("🔭  Stock Lens — US")
        title.setStyleSheet(f"color:{C['accent']};font-size:14px;font-weight:700;")
        title_bar.addWidget(title)

        toggle_btn = QPushButton("«")
        toggle_btn.setToolTip("Collapse/Expand Sidebar")
        toggle_btn.setCursor(Qt.PointingHandCursor)
        toggle_btn.setStyleSheet("QPushButton { color:#00f0ff; font-weight:bold; border:none; background:transparent; font-size:16px; min-width:24px; max-width:24px; } QPushButton:hover { color:#00a8ff; }")
        title_bar.addWidget(toggle_btn)
        lay.addLayout(title_bar)

        content_w = QWidget()
        content_w.setStyleSheet("background:transparent;")
        content_lay = QVBoxLayout(content_w)
        content_lay.setContentsMargins(0, 0, 0, 0); content_lay.setSpacing(6)
        lay.addWidget(content_w)

        def toggle_sidebar():
            if content_w.isVisible():
                content_w.hide()
                title.hide()
                panel.setFixedWidth(35)
                toggle_btn.setText("»")
            else:
                content_w.show()
                title.show()
                panel.setFixedWidth(280)
                toggle_btn.setText("«")
        toggle_btn.clicked.connect(toggle_sidebar)

        self._div(content_lay)

        self._sec(content_lay, "TICKER SYMBOL")
        self._sym_edit = QLineEdit()
        self._sym_edit.setPlaceholderText("e.g. AAPL")
        self._sym_edit.setStyleSheet(self._input_ss())
        self._sym_edit.returnPressed.connect(self._run_analysis)
        content_lay.addWidget(self._sym_edit)

        self._sec(content_lay, "FROM WATCHLIST")
        self._wl_cb = QComboBox()
        self._wl_cb.setStyleSheet(self._combo_ss())
        self._wl_cb.addItem("— pick from watchlist —", None)
        self._wl_cb.currentIndexChanged.connect(self._on_wl_pick)
        content_lay.addWidget(self._wl_cb)

        self._sec(content_lay, "HISTORY (DAYS)")
        self._lb_spin = QSpinBox()
        self._lb_spin.setRange(90, 1095)
        self._lb_spin.setValue(365)
        self._lb_spin.setSingleStep(30)
        self._lb_spin.setStyleSheet(
            f"QSpinBox{{background:{C['hover']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;padding:4px 8px;}}"
            f"QSpinBox::up-button,QSpinBox::down-button{{background:{C['card']};border:none;}}")
        content_lay.addWidget(self._lb_spin)

        self._sec(content_lay, "PEER TICKERS (comma-separated)")
        self._peers_edit = QLineEdit()
        self._peers_edit.setPlaceholderText("e.g. MSFT,GOOGL,AMZN")
        self._peers_edit.setStyleSheet(self._input_ss())
        content_lay.addWidget(self._peers_edit)

        content_lay.addSpacing(4); self._div(content_lay); content_lay.addSpacing(4)

        self._btn_analyse = QPushButton("🔬  Analyse")
        self._btn_analyse.setStyleSheet(
            f"QPushButton{{background:{C['accent']};color:#0d1117;border:none;"
            f"border-radius:4px;padding:7px;font-size:13px;font-weight:700;}}"
            f"QPushButton:hover{{background:{C['accent']}cc;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['sec']};}}")
        self._btn_analyse.clicked.connect(self._run_analysis)
        content_lay.addWidget(self._btn_analyse)

        self._btn_ai = QPushButton("✨  AI Commentary")
        self._btn_ai.setEnabled(False)
        self._btn_ai.setStyleSheet(
            f"QPushButton{{background:#238636;color:#fff;border:none;"
            f"border-radius:4px;padding:6px;font-size:13px;}}"
            f"QPushButton:hover{{background:#2ea043;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['sec']};}}")
        self._btn_ai.clicked.connect(self._run_ai)
        content_lay.addWidget(self._btn_ai)

        self._btn_explore = QPushButton("📈  Open in Explorer")
        self._btn_explore.setEnabled(False)
        self._btn_explore.setStyleSheet(
            f"QPushButton{{background:{C['hover']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;padding:6px;font-size:12px;}}"
            f"QPushButton:hover{{background:{C['card']};}}"
            f"QPushButton:disabled{{color:{C['sec']};}}")
        self._btn_explore.clicked.connect(self._open_explorer)
        content_lay.addWidget(self._btn_explore)

        self._btn_cancel = QPushButton("⏹  Cancel")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setStyleSheet(
            f"QPushButton{{background:#b91c1c;color:#fff;border:none;"
            f"border-radius:4px;padding:6px;font-size:12px;}}"
            f"QPushButton:disabled{{background:{C['hover']};color:{C['sec']};}}")
        self._btn_cancel.clicked.connect(self._cancel)
        content_lay.addWidget(self._btn_cancel)

        content_lay.addSpacing(4); self._div(content_lay); content_lay.addSpacing(4)

        self._sec(content_lay, "STATUS")
        self._prog = QProgressBar()
        self._prog.setRange(0, 0)
        self._prog.setFixedHeight(5)
        self._prog.setTextVisible(False)
        self._prog.setStyleSheet(
            f"QProgressBar{{background:{C['hover']};border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{C['accent']};border-radius:2px;}}")
        self._prog.hide()
        content_lay.addWidget(self._prog)
        self._status_lbl = QLabel("Ready.")
        self._status_lbl.setStyleSheet(f"color:{C['sec']};font-size:10px;")
        self._status_lbl.setWordWrap(True)
        content_lay.addWidget(self._status_lbl)

        content_lay.addSpacing(4); self._div(content_lay); content_lay.addSpacing(4)
        self._sec(content_lay, "SYMBOL DATABASE")
        self._sym_count_lbl = QLabel("—")
        self._sym_count_lbl.setStyleSheet(f"color:{C['muted']};font-size:10px;")
        content_lay.addWidget(self._sym_count_lbl)
        btn_dl = QPushButton("⟳  Refresh Symbols")
        btn_dl.setFixedHeight(24)
        btn_dl.setStyleSheet(
            f"QPushButton{{background:{C['hover']};color:{C['text']};"
            f"border:1px solid {C['border']};border-radius:4px;"
            f"padding:2px 8px;font-size:10px;}}"
            f"QPushButton:hover{{background:{C['card']};}}")
        btn_dl.clicked.connect(lambda: self._start_symbol_download(silent=False))
        content_lay.addWidget(btn_dl)
        self._update_sym_count_lbl()

        content_lay.addStretch()
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        lay   = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            f"QTabWidget::pane{{border:1px solid {C['border']};background:{C['bg']};}}"
            f"QTabBar::tab{{background:{C['card']};color:{C['sec']};"
            f"border:1px solid {C['border']};border-bottom:none;"
            f"padding:5px 16px;font-size:12px;}}"
            f"QTabBar::tab:hover{{background:{C['hover']};color:{C['text']};}}"
            f"QTabBar::tab:selected{{background:{C['bg']};color:{C['text']};"
            f"font-weight:700;border-bottom:2px solid {C['accent']};}}")

        self._tabs.addTab(self._build_overview_tab(),  "📊  Overview")
        self._tabs.addTab(self._build_chart_tab(),     "📈  Chart")
        self._tabs.addTab(self._build_analysis_tab(),  "⚖️  Bull/Bear")
        self._tabs.addTab(self._build_ai_tab(),        "✨  AI Commentary")
        self._tabs.addTab(self._build_news_tab(),      "📰  News")
        lay.addWidget(self._tabs)
        return panel

    # ── Tab builders ──────────────────────────────────────────────────────────

    def _build_overview_tab(self) -> QWidget:
        w = QWidget()
        self._overview_scroll = QScrollArea()
        self._overview_scroll.setWidgetResizable(True)
        self._overview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._overview_scroll.setStyleSheet(
            f"QScrollArea{{background:{C['bg']};border:none;}}")
        self._overview_content = QWidget()
        self._overview_lay = QVBoxLayout(self._overview_content)
        self._overview_lay.setContentsMargins(8, 8, 8, 8)
        self._overview_lay.setSpacing(6)
        self._overview_scroll.setWidget(self._overview_content)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 4, 0)
        lay.setSpacing(4)
        _sl_btn_row = QHBoxLayout()
        _sl_btn_row.addStretch()
        self._btn_sl_buy = QPushButton("🟢  (Dry) Buy")
        self._btn_sl_buy.setFixedHeight(28)
        self._btn_sl_buy.setEnabled(False)
        self._btn_sl_buy.setStyleSheet(
            "QPushButton{background:#1a7f37;color:#fff;border:none;border-radius:4px;"
            "padding:3px 12px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#238636;}"
            "QPushButton:disabled{background:#555;color:#888;}")
        self._btn_sl_buy.clicked.connect(
            lambda: self.trade_requested.emit(
                self._data["ticker"] if self._data else "", "buy"))
        self._btn_sl_sell = QPushButton("🔴  (Dry) Sell")
        self._btn_sl_sell.setFixedHeight(28)
        self._btn_sl_sell.setEnabled(False)
        self._btn_sl_sell.setStyleSheet(
            "QPushButton{background:#b91c1c;color:#fff;border:none;border-radius:4px;"
            "padding:3px 12px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#dc2626;}"
            "QPushButton:disabled{background:#555;color:#888;}")
        self._btn_sl_sell.clicked.connect(
            lambda: self.trade_requested.emit(
                self._data["ticker"] if self._data else "", "sell"))
        _sl_btn_row.addWidget(self._btn_sl_buy)
        _sl_btn_row.addWidget(self._btn_sl_sell)
        lay.addLayout(_sl_btn_row)
        lay.addWidget(self._overview_scroll)
        self._overview_lay.addWidget(self._placeholder("Run analysis to see overview"))
        return w

    def _build_chart_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self._chart_lbl = QLabel()
        self._chart_lbl.setAlignment(Qt.AlignCenter)
        self._chart_lbl.setStyleSheet(f"background:{C['bg']};")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea{{background:{C['bg']};border:none;}}")
        scroll.setWidget(self._chart_lbl)
        lay.addWidget(scroll)
        self._chart_lbl.setText(
            f"<span style='color:{C['muted']};font-size:13px;'>"
            f"Run analysis to generate chart</span>")
        return w

    def _build_analysis_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self._analysis_view = QTextEdit()
        self._analysis_view.setReadOnly(True)
        self._analysis_view.setStyleSheet(
            f"QTextEdit{{background:{C['bg']};color:{C['text']};"
            f"border:none;padding:10px;font-family:'Segoe UI',Arial;font-size:12px;}}")
        self._analysis_view.setHtml(self._placeholder_html("Bull/Bear analysis appears here"))
        lay.addWidget(self._analysis_view)
        return w

    def _build_ai_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(4)
        hdr = QHBoxLayout()
        self._ai_model_lbl = QLabel("Model: —")
        self._ai_model_lbl.setStyleSheet(f"color:{C['muted']};font-size:10px;")
        hdr.addWidget(self._ai_model_lbl); hdr.addStretch()
        lay.addLayout(hdr)
        self._ai_view = QTextEdit()
        self._ai_view.setReadOnly(True)
        self._ai_view.setStyleSheet(
            f"QTextEdit{{background:{C['bg']};color:{C['text']};"
            f"border:none;padding:8px;font-family:'Segoe UI',Arial;"
            f"font-size:12px;line-height:155%;}}")
        self._ai_view.setHtml(
            self._placeholder_html("Run analysis then click ✨ AI Commentary"))
        lay.addWidget(self._ai_view, 1)
        return w

    def _build_news_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        self._news_list = QListWidget()
        self._news_list.setStyleSheet(
            f"QListWidget{{background:{C['bg']};color:{C['text']};"
            f"border:none;font-size:12px;}}"
            f"QListWidget::item{{padding:8px 6px;"
            f"border-bottom:1px solid {C['hover']};}}"
            f"QListWidget::item:selected{{background:{C['card']};}}"
            f"QListWidget::item:hover{{background:{C['hover']};}}")
        self._news_list.itemDoubleClicked.connect(self._open_news)
        lay.addWidget(self._news_list)
        return w

    # ── Style helpers ─────────────────────────────────────────────────────────

    def _div(self, lay):
        d = QFrame(); d.setFixedHeight(1)
        d.setStyleSheet(f"background:{C['border']};margin:2px 0;")
        lay.addWidget(d)

    def _sec(self, lay, text: str):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{C['sec']};font-size:10px;font-weight:600;margin-top:4px;")
        lay.addWidget(lbl)

    def _input_ss(self) -> str:
        return (f"QLineEdit{{background:{C['hover']};color:{C['text']};"
                f"border:1px solid {C['border']};border-radius:4px;"
                f"padding:5px 8px;font-size:12px;}}")

    def _combo_ss(self) -> str:
        return (f"QComboBox{{background:{C['hover']};color:{C['text']};"
                f"border:1px solid {C['border']};border-radius:4px;padding:4px 8px;}}"
                f"QComboBox::drop-down{{border:none;}}"
                f"QComboBox QAbstractItemView{{background:{C['hover']};color:{C['text']};"
                f"selection-background-color:{C['accent']}40;"
                f"border:1px solid {C['border']};}}")

    def _placeholder(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{C['muted']};font-size:13px;margin:40px;")
        return lbl

    def _placeholder_html(self, text: str) -> str:
        return (f"<html><body style='background:{C['bg']};color:{C['muted']};"
                f"font-family:Segoe UI,sans-serif;font-size:13px;text-align:center;'>"
                f"<p style='margin-top:60px;'>{text}</p></body></html>")

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_wl_pick(self):
        sym = self._wl_cb.currentData()
        if sym:
            self._sym_edit.setText(sym)

    def _open_explorer(self):
        if self._data:
            self.go_explore.emit(self._data["ticker"])

    def _open_news(self, item: QListWidgetItem):
        url = item.data(Qt.UserRole)
        if url:
            import webbrowser; webbrowser.open(url)

    # ── Analysis flow ─────────────────────────────────────────────────────────

    def _run_analysis(self):
        sym = self._sym_edit.text().strip().upper()
        if not sym:
            QMessageBox.warning(self, "No Symbol", "Enter a ticker symbol."); return
        if self._worker and self._worker.isRunning():
            return

        raw = self._peers_edit.text().strip()
        peers = ([p.strip().upper() for p in raw.replace(";", ",").split(",") if p.strip()]
                 if raw else [])

        self._data = None
        self._btn_analyse.setEnabled(False)
        self._btn_ai.setEnabled(False)
        self._btn_explore.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._prog.show()
        self._status_lbl.setText(f"Fetching {sym}…")
        self._clear_overview()
        self._news_list.clear()
        self._chart_lbl.setText(
            f"<span style='color:{C['muted']};font-size:12px;'>Rendering…</span>")

        self._worker = _SLWorker(sym, peers, self._lb_spin.value(), parent=self)
        self._worker.progress.connect(self._status_lbl.setText)
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_done)
        self._worker.start()

    def _cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.abort(); self._worker.wait(3000)
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.terminate(); self._ai_worker.wait(2000)
        self._on_worker_done()
        self._status_lbl.setText("Cancelled.")

    def _on_worker_done(self):
        self._btn_analyse.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._prog.hide()
        if self._worker:
            self._worker.deleteLater(); self._worker = None

    def _on_result(self, data: dict):
        self._data = data
        has_ai = bool(self._gem_key or self._pplx_key)
        self._btn_ai.setEnabled(has_ai)
        self._btn_explore.setEnabled(True)
        self._btn_sl_buy.setEnabled(True)
        self._btn_sl_sell.setEnabled(True)
        self._status_lbl.setText(
            f"Done — {data['name']} ({data['ticker']})  ·  {data['as_of']}")
        self._render_overview(data)
        self._render_chart(data)
        self._render_bull_bear(data)
        self._render_news(data)
        self._tabs.setCurrentIndex(0)

    def _on_error(self, msg: str):
        self._status_lbl.setText(f"Error — see dialog")
        QMessageBox.critical(self, "Stock Lens Error", msg)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _clear_overview(self):
        while self._overview_lay.count():
            it = self._overview_lay.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def _render_overview(self, data: dict):
        self._clear_overview()
        info  = data.get("info", {})
        tech  = data.get("tech", {})
        wtw   = data.get("what_to_watch", [])
        rating, score, rcolor = _sl_compute_rating(data)

        # ── Header card ───────────────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet(
            f"QFrame{{background:{C['card']};border-radius:6px;"
            f"border:1px solid {C['border']};}}")
        hl = QHBoxLayout(header); hl.setContentsMargins(12, 10, 12, 10)
        lv = QVBoxLayout()
        name_lbl = QLabel(f"<b>{data['name']}</b>")
        name_lbl.setStyleSheet(
            f"color:{C['text']};font-size:14px;background:transparent;border:none;")
        sub_lbl = QLabel(
            f"{data['ticker']}  ·  {data.get('exchange', '')}  "
            f"·  {data.get('sector', '')}  ·  {data.get('industry', '')}")
        sub_lbl.setStyleSheet(
            f"color:{C['sec']};font-size:10px;background:transparent;border:none;")
        sub_lbl.setWordWrap(True)
        lv.addWidget(name_lbl); lv.addWidget(sub_lbl)
        hl.addLayout(lv, 1)
        rv = QVBoxLayout(); rv.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        r_lbl = QLabel(rating)
        r_lbl.setStyleSheet(
            f"color:{rcolor};font-size:22px;font-weight:700;"
            f"background:transparent;border:none;")
        r_lbl.setAlignment(Qt.AlignRight)
        s_lbl = QLabel(f"Signal: {score}/13")
        s_lbl.setStyleSheet(
            f"color:{C['sec']};font-size:10px;background:transparent;border:none;")
        s_lbl.setAlignment(Qt.AlignRight)
        rv.addWidget(r_lbl); rv.addWidget(s_lbl)
        hl.addLayout(rv)
        self._overview_lay.addWidget(header)

        # ── KPI grid ──────────────────────────────────────────────────────────
        last  = tech.get("last")
        sma20 = tech.get("sma20"); sma50 = tech.get("sma50"); sma200 = tech.get("sma200")
        rsi   = tech.get("rsi14")
        r1m   = tech.get("ret_1m"); r1y = tech.get("ret_1y")

        kpis = [
            ("Price",     f"${last:,.2f}"   if last  else "—",     None),
            ("SMA 20",    f"${sma20:,.2f}"  if sma20 else "—",
             C["green"] if (last and sma20  and last > sma20)  else C["red"]),
            ("SMA 50",    f"${sma50:,.2f}"  if sma50 else "—",
             C["green"] if (last and sma50  and last > sma50)  else C["red"]),
            ("SMA 200",   f"${sma200:,.2f}" if sma200 else "—",
             C["green"] if (last and sma200 and last > sma200) else C["red"]),
            ("RSI-14",    f"{rsi:.1f}" if rsi else "—",
             C["red"]   if (rsi and rsi > 70) else
             C["green"] if (rsi and rsi < 30) else None),
            ("Trend",     tech.get("trend", "—"), tech.get("trend_color")),
            ("1M Return", f"{r1m:+.1f}%" if r1m is not None else "—",
             C["green"] if (r1m or 0) >= 0 else C["red"]),
            ("1Y Return", f"{r1y:+.1f}%" if r1y is not None else "—",
             C["green"] if (r1y or 0) >= 0 else C["red"]),
            ("Mkt Cap",   _fmt_money_sl(info.get("marketCap")),      C["yellow"]),
            ("P/E",       f"{_sf(info.get('trailingPE')):.1f}x"
                          if _sf(info.get("trailingPE")) else "—",   None),
            ("ROE",       _fmt_pct_sl(info.get("returnOnEquity")),
             C["green"] if (_sf(info.get("returnOnEquity")) or 0) > 0.15 else None),
            ("Gross Mgn", _fmt_pct_sl(info.get("grossMargins")),
             C["green"] if (_sf(info.get("grossMargins")) or 0) > 0.40 else None),
            ("Rev Grwth", _fmt_pct_sl(info.get("revenueGrowth")),
             C["green"] if (_sf(info.get("revenueGrowth")) or 0) > 0.08 else None),
            ("FCF",       _fmt_money_sl(info.get("freeCashflow")),
             C["green"] if (_sf(info.get("freeCashflow")) or 0) > 0 else C["red"]),
            ("Beta",      f"{_sf(info.get('beta')):.2f}"
                          if _sf(info.get("beta")) else "—",          None),
            ("52w High",  f"${tech.get('hi_52w'):,.2f}"
                          if tech.get("hi_52w") else "—",             None),
        ]

        grid_w = QWidget()
        grid   = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0); grid.setSpacing(6)
        for i, (klabel, kval, kcolor) in enumerate(kpis):
            card = QFrame()
            card.setStyleSheet(
                f"QFrame{{background:{C['card']};border-radius:5px;"
                f"border:1px solid {C['border']};}}")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 6, 8, 6); cl.setSpacing(1)
            lbl_w = QLabel(klabel)
            lbl_w.setStyleSheet(
                f"color:{C['muted']};font-size:9px;font-weight:600;"
                f"background:transparent;border:none;")
            val_w = QLabel(str(kval))
            val_w.setStyleSheet(
                f"color:{kcolor or C['text']};font-size:13px;font-weight:700;"
                f"background:transparent;border:none;")
            cl.addWidget(lbl_w); cl.addWidget(val_w)
            r_idx, c_idx = divmod(i, 4)
            grid.addWidget(card, r_idx, c_idx)
        self._overview_lay.addWidget(grid_w)

        # ── What to watch ─────────────────────────────────────────────────────
        if wtw:
            wtw_hdr = QLabel("📌  What to Watch")
            wtw_hdr.setStyleSheet(
                f"color:{C['accent']};font-size:11px;font-weight:700;margin-top:8px;")
            self._overview_lay.addWidget(wtw_hdr)
            for wtw_item in wtw:
                row_lbl = QLabel(f"• {wtw_item}")
                row_lbl.setStyleSheet(f"color:{C['sec']};font-size:11px;")
                row_lbl.setWordWrap(True)
                self._overview_lay.addWidget(row_lbl)

        self._overview_lay.addStretch()

    def _render_chart(self, data: dict):
        sym = data.get("ticker", "SYM")
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = os.path.join(_SL_CHT_DIR, f"{sym}_{ts}.png")
        ok  = _sl_render_chart(data, out)
        if ok and os.path.exists(out):
            pix = QPixmap(out)
            if not pix.isNull():
                self._chart_lbl.setPixmap(pix); return
        self._chart_lbl.setText(
            f"<span style='color:{C['muted']};font-size:12px;'>"
            f"Chart unavailable — check matplotlib installation</span>")

    def _render_bull_bear(self, data: dict):
        bull = data.get("bull_case", [])
        bear = data.get("bear_case", [])
        tech = data.get("tech", {})

        def table_rows(items, color):
            if not items:
                return f"<tr><td colspan='2' style='color:{C['muted']};padding:8px;'>—</td></tr>"
            return "".join(
                f"<tr><td style='padding:4px 6px;color:{color};font-size:14px;'>●</td>"
                f"<td style='padding:4px 6px;color:{C['text']};font-size:12px;'>{it}</td></tr>"
                for it in items)

        rsi_str   = f"{tech['rsi14']:.1f}" if tech.get("rsi14") is not None else "—"
        macd_str  = f"{tech['macd']:.4f}"  if tech.get("macd")  is not None else "—"
        msig_str  = f"{tech['macd_signal']:.4f}" if tech.get("macd_signal") is not None else "—"
        hi52_str  = f"${tech['hi_52w']:,.2f}" if tech.get("hi_52w") is not None else "—"
        lo52_str  = f"${tech['lo_52w']:,.2f}" if tech.get("lo_52w") is not None else "—"

        # pre-assign colour values (Python 3.10 f-string compat — no same-quote nesting)
        _bg      = C['bg'];   _green  = C['green']; _red    = C['red']
        _yellow  = C['yellow']; _card = C['card'];  _sec    = C['sec']
        _t_color = tech.get("trend_color", C["text"])
        _trend   = tech.get("trend", "—")
        _tdetail = tech.get("trend_detail", "")
        _rsi_sig = tech.get("rsi_signal", "—")
        _bb_sig  = tech.get("bb_signal", "—")

        # Use triple-single-quoted f-string so double-quoted HTML attrs are unambiguous
        html = f'''<html><body style="background:{_bg};font-family:Segoe UI,Arial;margin:12px;">
<h3 style="color:{_green};font-size:13px;margin:0 0 4px;">\U0001f7e2  Bull Case</h3>
<table width="100%" style="background:{_card};border-radius:5px;margin-bottom:12px;">
  {table_rows(bull, _green)}
</table>
<h3 style="color:{_red};font-size:13px;margin:0 0 4px;">\U0001f534  Bear Case</h3>
<table width="100%" style="background:{_card};border-radius:5px;margin-bottom:12px;">
  {table_rows(bear, _red)}
</table>
<h3 style="color:{_yellow};font-size:13px;margin:0 0 4px;">\U0001f4ca  Technical Snapshot</h3>
<table width="100%" style="background:{_card};border-radius:5px;">
  <tr><td style="padding:4px 10px;color:{_sec};width:110px;">Trend</td>
      <td style="color:{_t_color};font-weight:700;">{_trend} — {_tdetail}</td></tr>
  <tr><td style="padding:4px 10px;color:{_sec};">RSI-14</td>
      <td>{rsi_str} ({_rsi_sig})</td></tr>
  <tr><td style="padding:4px 10px;color:{_sec};">MACD</td>
      <td>{macd_str} / signal {msig_str}</td></tr>
  <tr><td style="padding:4px 10px;color:{_sec};">BB signal</td>
      <td>{_bb_sig}</td></tr>
  <tr><td style="padding:4px 10px;color:{_sec};">52w Hi / Lo</td>
      <td>{hi52_str} / {lo52_str}</td></tr>
</table>
</body></html>'''
        self._analysis_view.setHtml(html)

    def _render_news(self, data: dict):
        self._news_list.clear()
        for item in data.get("news", []):
            title  = item.get("title", "No title")
            source = item.get("source", "")
            url    = item.get("url", "")
            display = f"{title}\n   — {source}" if source else title
            it = QListWidgetItem(display)
            it.setData(Qt.UserRole, url)
            it.setToolTip(url or "No URL")
            it.setForeground(QColor(C["text"]))
            self._news_list.addItem(it)

    # ── AI commentary ─────────────────────────────────────────────────────────

    def _run_ai(self):
        if not self._data: return
        if self._ai_worker and self._ai_worker.isRunning(): return
        if not self._gem_key and not self._pplx_key:
            QMessageBox.warning(self, "No AI Keys",
                "Add GEMINI_API_KEY.txt or PERPLEXITY_API_KEY.txt to the ALLAPI/ folder.")
            return
        self._btn_ai.setEnabled(False)
        self._ai_model_lbl.setText("Model: generating…")
        self._ai_view.setHtml(self._placeholder_html("Generating AI commentary…"))
        self._prog.show()
        self._ai_worker = _SLAIWorker(
            self._data, self._gem_key, self._pplx_key, parent=self)
        self._ai_worker.commentary_ready.connect(self._on_ai_result)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.finished.connect(
            lambda: (self._btn_ai.setEnabled(True), self._prog.hide()))
        self._ai_worker.start()
        self._tabs.setCurrentIndex(3)

    def _on_ai_result(self, text: str, model: str):
        self._ai_model_lbl.setText(f"Model: {model}")
        lines = text.strip().split("\n")
        body  = "".join(
            f"<p style='margin:4px 0;'>{ln}</p>" if ln.strip() else "<br>"
            for ln in lines)
        self._ai_view.setHtml(
            f"<html><body style='background:{C['bg']};color:{C['text']};"
            f"font-family:Segoe UI,Arial;font-size:12px;"
            f"padding:8px;line-height:155%;'>{body}</body></html>")

    def _on_ai_error(self, msg: str):
        self._ai_model_lbl.setText("Model: error")
        self._ai_view.setHtml(self._placeholder_html(f"AI error: {msg}"))

    # ── Symbol download ───────────────────────────────────────────────────────

    def _start_symbol_download(self, silent: bool = False):
        if self._dl_worker and self._dl_worker.isRunning(): return
        if not silent:
            self._status_lbl.setText("Downloading US symbol list from SEC EDGAR…")
        self._dl_worker = _SLSymbolDownloadWorker(parent=self)
        self._dl_worker.progress.connect(
            lambda m: self._status_lbl.setText(m) if not silent else None)
        self._dl_worker.finished.connect(self._on_dl_finished)
        self._dl_worker.start()

    def _on_dl_finished(self, count: int, path_or_err: str):
        if count >= 0:
            n = _sl_load_symbols()
            self._update_sym_count_lbl()
            self._status_lbl.setText(f"Symbol list updated — {n:,} US stocks")
        else:
            self._status_lbl.setText(f"Symbol download error: {path_or_err[:60]}")

    def _update_sym_count_lbl(self):
        n = len(_SL_SYMBOLS)
        if n:
            self._sym_count_lbl.setText(f"{n:,} symbols loaded")
        elif os.path.exists(_SL_CSV):
            self._sym_count_lbl.setText("CSV found — click Refresh to load")
        else:
            self._sym_count_lbl.setText("No symbol DB — click Refresh to download")





class AnalystPanel(QWidget):
    """
    ANALYST top-level panel.
    Sub-tabs (horizontal): TECHNALYZE | TECH ANALYST | VCP | TRADE PLANNER | SECTOR ANALYST | FUNDANALYZE | CANSLIMPRO | STOCK LENS
    Integrated with USmarketsResearch database and watchlists.
    """

    go_explore      = Signal(str)
    trade_requested = Signal(str, str)   # (symbol, side)
    watchlist_changed = Signal()         # Emitted when a watchlist is modified

    def __init__(self, pool: QThreadPool, parent=None):
        super().__init__(parent)
        self._pool = pool
        self.db = DatabaseManager()
        self._build()
        self.refresh_watchlists_combo()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── Watchlist Selection Top Bar ──
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 4)
        top_bar.setSpacing(10)

        wl_lbl = QLabel("Analyze Watchlist:")
        wl_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #00d4ff;")
        top_bar.addWidget(wl_lbl)

        self.wl_combo = QComboBox()
        self.wl_combo.setStyleSheet("""
            QComboBox {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 4px;
                padding: 4px 12px;
                min-width: 220px;
                color: #ffffff;
            }
            QComboBox::drop-down {
                border: none;
            }
        """)
        self.wl_combo.currentIndexChanged.connect(self._on_watchlist_selected)
        top_bar.addWidget(self.wl_combo)

        refresh_btn = QPushButton("🔄 Refresh lists")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 1px solid #30363d;
                border-radius: 4px;
                padding: 4px 12px;
                color: #b1bac4;
            }
            QPushButton:hover {
                background-color: #21262d;
                color: #ffffff;
            }
        """)
        refresh_btn.clicked.connect(self.refresh_watchlists_combo)
        top_bar.addWidget(refresh_btn)
        top_bar.addStretch()

        root.addLayout(top_bar)

        # ── Tab widget ──
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {C['border']};
                border-radius: 8px;
                margin-top: -1px;
            }}
            QTabBar::tab {{
                padding: 9px 24px;
                color: {C['sec']};
                background: {C['card']};
                border-radius: 6px 6px 0 0;
                margin-right: 3px;
                font-size: 13px;
                font-weight: 600;
            }}
            QTabBar::tab:selected {{
                color: {C['text']};
                background: {C['hover']};
                border-bottom: 2px solid {C['accent']};
            }}
        """)
        self._tech         = TechnalyzePanel()
        self._tech_analyst = TechAnalystPanel()
        self._vcp          = VCPPanel()
        self._trade_plan   = TradePlannerPanel()
        self._sector       = SectorAnalystPanel()
        self._fund         = FundamentalAnalysisPanel()
        self._canslim      = CANSLIMProPanel()
        self._stock_lens   = StockLensPanel()

        tabs.addTab(self._tech,         "📈  TECHNALYZE")
        tabs.addTab(self._tech_analyst, "📊  TECH ANALYST")
        tabs.addTab(self._vcp,          "🌀  VCP")
        tabs.addTab(self._trade_plan,   "📋  TRADE PLANNER")
        tabs.addTab(self._sector,       "🌍  SECTOR ANALYST")
        tabs.addTab(self._fund,         "📉  FUNDANALYZE")
        tabs.addTab(self._canslim,      "🏆  CANSLIMPRO")
        tabs.addTab(self._stock_lens,   "🔭  STOCK LENS")
        
        self._stock_lens.go_explore.connect(self.go_explore)
        self._tech.trade_requested.connect(self.trade_requested)
        self._tech_analyst.trade_requested.connect(self.trade_requested)
        self._stock_lens.trade_requested.connect(self.trade_requested)
        self._vcp.trade_requested.connect(self.trade_requested)
        self._fund.trade_requested.connect(self.trade_requested)
        self._canslim.trade_requested.connect(self.trade_requested)
        
        # Connect send_to_wl signals to our custom handler
        self._vcp.send_to_wl.connect(self.handle_send_to_wl)
        self._fund.send_to_wl.connect(self.handle_send_to_wl)
        self._canslim.send_to_wl.connect(self.handle_send_to_wl)
        
        self._tabs = tabs
        self._vcp.plan_requested.connect(self._on_plan_requested)
        root.addWidget(tabs)

    def _on_plan_requested(self, rows: list):
        self._trade_plan.receive_vcp_results(rows)
        self._tabs.setCurrentWidget(self._trade_plan)

    def set_symbols(self, symbols: list):
        self._tech.set_symbols(symbols)
        self._tech_analyst.set_symbols(symbols)
        self._vcp.set_symbols(symbols)
        self._fund.set_symbols(symbols)
        self._canslim.set_symbols(symbols)
        self._stock_lens.set_symbols(symbols)

    def refresh(self):
        self.refresh_watchlists_combo()

    @Slot()
    def refresh_watchlists_combo(self):
        self.wl_combo.blockSignals(True)
        self.wl_combo.clear()
        
        # Add index presets
        self.wl_combo.addItem("S&P 500", ("preset", "S&P 500"))
        self.wl_combo.addItem("NASDAQ 100", ("preset", "NASDAQ 100"))
        
        # Query custom watchlists
        try:
            user_lists = self.db.get_watchlists()
            for wl in user_lists:
                name = wl["name"]
                display = name[3:] if name.startswith("US_") else name
                self.wl_combo.addItem(f"Watchlist: {display}", ("custom", name))
        except Exception as e:
            print(f"Error loading watchlists in Analyst: {e}")
            
        self.wl_combo.blockSignals(False)
        self._on_watchlist_selected(self.wl_combo.currentIndex())

    def _on_watchlist_selected(self, index):
        if index < 0:
            return
        data = self.wl_combo.itemData(index)
        if not data:
            return
        
        typ, val = data
        symbols = []
        if typ == "preset":
            # Load index constituents from DatabaseManager
            try:
                constituents = self.db.get_all_constituents()
                flag = "is_sp500" if val == "S&P 500" else "is_nasdaq"
                symbols = [c["symbol"] for c in constituents if c.get(flag)]
            except Exception as e:
                print(f"Error loading constituents for preset {val}: {e}")
        elif typ == "custom":
            # Load custom watchlist symbols
            try:
                items = self.db.get_watchlist_items(val)
                symbols = [it["symbol"] for it in items]
            except Exception as e:
                print(f"Error loading custom watchlist items for {val}: {e}")

        # Update all subtabs
        self.set_symbols(symbols)

    @Slot(list)
    def handle_send_to_wl(self, symbols: list):
        if not symbols:
            return
        name, ok = QInputDialog.getText(
            self, "Send to Watchlist",
            f"Enter watchlist name to save {len(symbols)} symbols:",
            text="Analyst Output"
        )
        if ok and name.strip():
            wl_name = name.strip()
            if not wl_name.startswith("US_"):
                wl_name = "US_" + wl_name
            try:
                self.db.create_watchlist(wl_name)
                for sym in symbols:
                    self.db.add_to_watchlist(wl_name, sym)
                QMessageBox.information(
                    self, "Success",
                    f"Saved {len(symbols)} symbols to watchlist '{wl_name}'."
                )
                self.watchlist_changed.emit()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to save watchlist: {e}")
