"""
Professional charting module — industry-grade interactive chart.

Features
--------
* Multi-pane layout: price (candlesticks) / volume / RSI / MACD,
  panes added or removed dynamically based on the checkbox toggles.
* Toggle-able overlays: EMA9 · EMA21 · Bollinger Bands · RSI14 · MACD.
* Volume bars coloured by up/down day, with a 20-day average-volume line.
* Mouse interactions:
    - Mouse-wheel scroll → zoom in/out, centred on the cursor's x.
    - Click & drag      → pan the visible window.
    - Double-click      → re-centre the visible window on the click x.
    - Mouse hover       → data-aware crosshair with an OHLCV/indicator
                          tooltip in a corner of the chart.
* "Expand to left" button that loads additional history beyond the
  initial timeframe.
* Pattern identification:
    - Pivot highs / lows
    - Support / resistance horizontal lines
    - 50/200 SMA crosses (golden / death)
    - Candlestick patterns (doji, hammer, shooting star, engulfing)
  Each detected pattern is annotated on the chart.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

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
        NavigationToolbar2QT as NavigationToolbar,
    )
    from matplotlib.figure import Figure  # type: ignore
    from matplotlib.patches import Rectangle  # type: ignore
    MATPLOTLIB_AVAILABLE = True
except Exception:  # pragma: no cover
    MATPLOTLIB_AVAILABLE = False

try:
    import yfinance as yf  # type: ignore
    YFINANCE_AVAILABLE = True
except Exception:  # pragma: no cover
    YFINANCE_AVAILABLE = False

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)


# Catppuccin Mocha palette (matches the rest of the app)
C_BG = "#1e1e2e"
C_SURFACE = "#181825"
C_PANEL = "#313244"
C_TEXT = "#cdd6f4"
C_MUTED = "#a6adc8"
C_DIM = "#7f849c"
C_BORDER = "#45475a"
C_ACCENT = "#89b4fa"
C_GREEN = "#a6e3a1"
C_RED = "#f38ba8"
C_AMBER = "#f9e2af"
C_ORANGE = "#fab387"
C_PURPLE = "#cba6f7"
C_TEAL = "#94e2d5"
C_CYAN = "#00E5FF"
# Bright candle palette — pop on the dark background
C_UP_BRIGHT = "#00FF85"
C_DN_BRIGHT = "#FF2D55"
# Bollinger more saturated than the default teal so it reads at a glance
C_BOLL_LINE = "#5CE1E6"
C_BOLL_FILL = "#5CE1E6"
# Supertrend trend colours
C_STREND_UP = "#00FF85"
C_STREND_DN = "#FF2D55"
# VWAP — bright magenta
C_VWAP = "#FF61F6"


# ===========================================================================
# Pattern detection helpers
# ===========================================================================
def find_pivots(highs, lows, lookback: int = 5) -> tuple[list[int], list[int]]:
    """Return indices of pivot highs and pivot lows.

    A pivot high at index i means highs[i] is strictly greater than every
    high in [i-lookback, i+lookback], symmetrically for pivot lows.
    """
    n = len(highs)
    pivot_highs: list[int] = []
    pivot_lows: list[int] = []
    for i in range(lookback, n - lookback):
        h_window = highs[i - lookback:i + lookback + 1]
        l_window = lows[i - lookback:i + lookback + 1]
        if highs[i] == max(h_window) and list(h_window).count(highs[i]) == 1:
            pivot_highs.append(i)
        if lows[i] == min(l_window) and list(l_window).count(lows[i]) == 1:
            pivot_lows.append(i)
    return pivot_highs, pivot_lows


def cluster_levels(prices: list[float], tolerance_pct: float = 0.5,
                   max_levels: int = 6) -> list[float]:
    """Cluster nearby pivot prices into discrete support / resistance levels.
    ``tolerance_pct`` is the % distance below which two prices fold into
    the same cluster.
    """
    if not prices:
        return []
    prices = sorted(prices)
    clusters: list[list[float]] = [[prices[0]]]
    for p in prices[1:]:
        last = clusters[-1][-1]
        if abs(p - last) / max(last, 1e-9) * 100.0 < tolerance_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    # Sort clusters by population (more touches = more important level)
    clusters.sort(key=lambda c: len(c), reverse=True)
    levels = [sum(c) / len(c) for c in clusters[:max_levels]]
    return sorted(levels)


def detect_ma_cross(sma_fast, sma_slow) -> Optional[str]:
    """Detect a 50/200 cross on the most recent bar. Returns 'golden',
    'death', or None."""
    if len(sma_fast) < 2 or len(sma_slow) < 2:
        return None
    f0, f1 = sma_fast[-2], sma_fast[-1]
    s0, s1 = sma_slow[-2], sma_slow[-1]
    if any(np.isnan([f0, f1, s0, s1])):
        return None
    if f0 <= s0 and f1 > s1:
        return "golden"
    if f0 >= s0 and f1 < s1:
        return "death"
    return None


def detect_candlestick(o: float, h: float, l: float, c: float,
                       prev_o: Optional[float] = None,
                       prev_c: Optional[float] = None) -> Optional[str]:
    """Identify a single-bar (or two-bar with prev_*) candlestick pattern.
    Returns a label string or None."""
    if any(np.isnan([o, h, l, c])):
        return None
    body = abs(c - o)
    rng = h - l
    if rng <= 0:
        return None
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l
    body_pct = body / rng

    # Doji: very small body relative to range
    if body_pct < 0.10:
        return "Doji"
    # Hammer: small body near top, long lower shadow, no/short upper
    if body_pct < 0.35 and lower_shadow > 2 * body and upper_shadow < body:
        return "Hammer"
    # Shooting Star: small body near bottom, long upper shadow
    if body_pct < 0.35 and upper_shadow > 2 * body and lower_shadow < body:
        return "Shooting Star"
    # Engulfing (needs previous bar)
    if prev_o is not None and prev_c is not None and not any(
        np.isnan([prev_o, prev_c])
    ):
        prev_body = abs(prev_c - prev_o)
        if prev_body > 0 and body > prev_body * 1.2:
            # Bullish engulfing: prev red, today green, engulfs prev body
            if (prev_c < prev_o) and (c > o) and (o <= prev_c) and (c >= prev_o):
                return "Bullish Engulfing"
            # Bearish engulfing
            if (prev_c > prev_o) and (c < o) and (o >= prev_c) and (c <= prev_o):
                return "Bearish Engulfing"
    return None


# ===========================================================================
# Data-fetch worker
# ===========================================================================
class ChartDataWorker(QThread):
    """Pulls OHLCV history off the UI thread."""

    finished_ok = Signal(object, str)   # df, ticker
    failed = Signal(str)

    def __init__(self, ticker: str, period: str, interval: str = "1d",
                 parent=None) -> None:
        super().__init__(parent)
        self.ticker = ticker
        self.period = period
        self.interval = interval

    def run(self) -> None:  # type: ignore[override]
        if not YFINANCE_AVAILABLE:
            self.failed.emit("yfinance is not installed."); return
        try:
            df = yf.Ticker(self.ticker).history(
                period=self.period, interval=self.interval, auto_adjust=True,
            )
            if df is None or df.empty:
                self.failed.emit(f"No data for {self.ticker}"); return
            # Strip any tz info on index for cleaner matplotlib formatting
            try:
                df.index = df.index.tz_localize(None)
            except Exception:
                pass
            self.finished_ok.emit(df, self.ticker)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ===========================================================================
# The chart widget
# ===========================================================================
class ProfessionalChart(QWidget):
    """Pro-grade interactive chart. Drop into any QTabWidget."""

    # Emitted when user wants the next (+1) or previous (-1) sidebar symbol
    navigate_symbol = Signal(int)

    # (yfinance period, UI label, yfinance interval) — interval picks itself
    # so intraday timeframes get suitably fine bars and longer windows get
    # daily / weekly bars.
    PERIODS = [
        ("1d",  "1D",  "5m"),
        ("5d",  "1W",  "30m"),
        ("1mo", "1M",  "1d"),
        ("3mo", "3M",  "1d"),
        ("6mo", "6M",  "1d"),
        ("1y",  "1Y",  "1d"),
        ("2y",  "2Y",  "1d"),
        ("5y",  "5Y",  "1wk"),
        ("max", "MAX", "1wk"),
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._df: Optional["pd.DataFrame"] = None
        self._ticker: str = ""
        self._period: str = "1y"
        self._interval: str = "1d"
        self._worker: Optional[ChartDataWorker] = None

        # View state (x-range as integer bar indices)
        self._x_min: int = 0
        self._x_max: int = 0
        # Pan tracking
        self._pan_anchor_x: Optional[float] = None
        self._pan_anchor_range: Optional[tuple[int, int]] = None
        # Crosshair artists, by axis
        self._crosshair: dict = {}
        self._tooltip = None  # text artist
        # Pattern annotation cache
        self._annotations: list = []
        # Connection ids so we can disconnect cleanly
        self._cids: list[int] = []

        self._build_ui()

    # =====================================================================
    # UI assembly
    # =====================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 4)
        root.setSpacing(6)

        # ----- Toolbar row 1: ticker controls + overlays --------------
        ctl_row = QHBoxLayout(); ctl_row.setSpacing(8)

        # Big prev / next symbol arrows. Walk through the sidebar tree's
        # flat leaf order. Also bound to Up / Down keys when the chart
        # itself has focus (scoped so it doesn't fight the tree).
        nav_btn_css = (
            "QPushButton#chartNavArrow {"
            "  color: #00E5FF; background: #0A1822;"
            "  border: 1px solid #1FB8E0; border-radius: 6px;"
            "  font-weight: 800; font-size: 18px;"
            "}"
            "QPushButton#chartNavArrow:hover {"
            "  color: #FFFFFF; background: #0F2937;"
            "  border: 1px solid #00E5FF;"
            "}"
        )
        self.prev_btn = QPushButton("\u25C0")  # ◀
        self.prev_btn.setObjectName("chartNavArrow")
        self.prev_btn.setFixedSize(42, 32)
        self.prev_btn.setToolTip("Previous symbol in sidebar (Up arrow)")
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.setStyleSheet(nav_btn_css)
        self.prev_btn.clicked.connect(lambda: self.navigate_symbol.emit(-1))
        ctl_row.addWidget(self.prev_btn)
        self.next_btn = QPushButton("\u25B6")  # ▶
        self.next_btn.setObjectName("chartNavArrow")
        self.next_btn.setFixedSize(42, 32)
        self.next_btn.setToolTip("Next symbol in sidebar (Down arrow)")
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.setStyleSheet(nav_btn_css)
        self.next_btn.clicked.connect(lambda: self.navigate_symbol.emit(+1))
        ctl_row.addWidget(self.next_btn)
        ctl_row.addSpacing(10)
        # Keyboard: Up / Down navigate symbols when this chart has focus
        self._sc_prev = QShortcut(QKeySequence("Up"), self)
        self._sc_prev.setContext(Qt.WidgetWithChildrenShortcut)
        self._sc_prev.activated.connect(lambda: self.navigate_symbol.emit(-1))
        self._sc_next = QShortcut(QKeySequence("Down"), self)
        self._sc_next.setContext(Qt.WidgetWithChildrenShortcut)
        self._sc_next.activated.connect(lambda: self.navigate_symbol.emit(+1))

        ctl_row.addWidget(QLabel("Period:"))
        self.period_combo = QComboBox()
        for v, lbl, ival in self.PERIODS:
            self.period_combo.addItem(lbl, (v, ival))
        # Default to 1Y (index 5 after adding 1D and 1W)
        for i, t in enumerate(self.PERIODS):
            if t[0] == "1y":
                self.period_combo.setCurrentIndex(i); break
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        ctl_row.addWidget(self.period_combo)

        self.expand_left_btn = QPushButton("⟵ Expand left")
        self.expand_left_btn.setToolTip(
            "Load more history. Doubles the lookback period."
        )
        self.expand_left_btn.clicked.connect(self._on_expand_left)
        ctl_row.addWidget(self.expand_left_btn)

        self.reset_btn = QPushButton("Reset view")
        self.reset_btn.clicked.connect(self._on_reset_view)
        ctl_row.addWidget(self.reset_btn)

        ctl_row.addSpacing(14)
        ctl_row.addWidget(QLabel("Overlays:"))
        self.chk_ema9 = self._make_check("EMA 9", C_ORANGE, True)
        ctl_row.addWidget(self.chk_ema9)
        self.chk_ema21 = self._make_check("EMA 21", C_AMBER, True)
        ctl_row.addWidget(self.chk_ema21)
        self.chk_bb = self._make_check("Bollinger", C_TEAL, True)
        ctl_row.addWidget(self.chk_bb)
        self.chk_sma50 = self._make_check("SMA 50", C_PURPLE, False)
        ctl_row.addWidget(self.chk_sma50)
        self.chk_sma200 = self._make_check("SMA 200", C_CYAN, False)
        ctl_row.addWidget(self.chk_sma200)
        self.chk_supertrend = self._make_check("Supertrend", C_STREND_UP, False)
        self.chk_supertrend.setToolTip("ATR(10) · multiplier 3 Supertrend")
        ctl_row.addWidget(self.chk_supertrend)
        self.chk_vwap = self._make_check("VWAP", C_VWAP, False)
        self.chk_vwap.setToolTip("Anchored Volume-Weighted Average Price")
        ctl_row.addWidget(self.chk_vwap)

        ctl_row.addSpacing(12)
        ctl_row.addWidget(QLabel("Panes:"))
        self.chk_volume = self._make_check("Volume", C_DIM, True)
        ctl_row.addWidget(self.chk_volume)
        self.chk_rsi = self._make_check("RSI 14", C_RED, True)
        ctl_row.addWidget(self.chk_rsi)
        self.chk_macd = self._make_check("MACD", C_ACCENT, True)
        ctl_row.addWidget(self.chk_macd)

        ctl_row.addSpacing(12)
        self.chk_patterns = self._make_check("Patterns", C_GREEN, True)
        self.chk_patterns.setToolTip(
            "Auto-detect support / resistance, MA crosses, candlestick patterns"
        )
        ctl_row.addWidget(self.chk_patterns)

        ctl_row.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
        ctl_row.addWidget(self.status_label)
        root.addLayout(ctl_row)

        # ----- Canvas -------------------------------------------------
        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(10, 7), facecolor=C_BG)
            self.canvas = FigureCanvas(self.figure)
            self.canvas.setMinimumHeight(420)
            self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.canvas.setFocusPolicy(Qt.StrongFocus)
            self.canvas.setCursor(Qt.OpenHandCursor)
            self.nav_toolbar = NavigationToolbar(self.canvas, self)
            # We hide matplotlib's default toolbar; our own controls cover it
            self.nav_toolbar.hide()
            root.addWidget(self.canvas, 1)
            # Wire mouse events
            self._cids.append(self.canvas.mpl_connect("motion_notify_event", self._on_motion))
            self._cids.append(self.canvas.mpl_connect("scroll_event", self._on_scroll))
            self._cids.append(self.canvas.mpl_connect("button_press_event", self._on_press))
            self._cids.append(self.canvas.mpl_connect("button_release_event", self._on_release))
            self._cids.append(self.canvas.mpl_connect("axes_leave_event", self._on_leave))
        else:
            root.addWidget(QLabel("matplotlib not installed — chart unavailable."))

        # ----- Bottom info bar ----------------------------------------
        self.info_label = QLabel(
            "Wheel: zoom from left   ·   Click+drag: pan   ·   "
            "Double-click: centre   ·   \u25C0 \u25B6 / \u2191 \u2193: prev/next symbol"
        )
        self.info_label.setStyleSheet(f"color:{C_DIM}; font-size:11px; padding:2px 6px;")
        root.addWidget(self.info_label)

        # Wire overlay toggles
        for chk in (
            self.chk_ema9, self.chk_ema21, self.chk_bb, self.chk_sma50,
            self.chk_sma200, self.chk_supertrend, self.chk_vwap,
            self.chk_volume, self.chk_rsi, self.chk_macd,
            self.chk_patterns,
        ):
            chk.toggled.connect(self._redraw)

        # Empty-state hint so the user sees *why* the canvas is blank
        if MATPLOTLIB_AVAILABLE:
            self._draw_placeholder()

    @staticmethod
    def _make_check(label: str, color: str, checked: bool) -> QCheckBox:
        c = QCheckBox(label)
        c.setChecked(checked)
        c.setStyleSheet(
            f"QCheckBox {{ color:{color}; font-weight:600; font-size:11px; }}"
            f"QCheckBox::indicator {{ width:13px; height:13px; }}"
        )
        return c

    # =====================================================================
    # Public API
    # =====================================================================
    def set_ticker(self, ticker: str) -> None:
        """Load (or reload) the chart for the given symbol. Safe to call
        repeatedly — a pending fetch is interrupted in favour of the new
        symbol.
        """
        if not ticker:
            return
        self._ticker = ticker.upper().strip()
        data = self.period_combo.currentData()
        if isinstance(data, tuple):
            self._period, self._interval = data
        else:
            self._period, self._interval = data, "1d"
        # Paint an obvious 'loading' state in the canvas so the user knows
        # something is happening even if the status_label scrolls off-screen.
        self._draw_placeholder(
            f"\u23F3   Loading {self._ticker}\n\n"
            f"Period: {self._period.upper()}   ·   Bar interval: {self._interval}"
        )
        self._fetch()

    def _fetch(self) -> None:
        # If a fetch is already in flight for a different ticker we still
        # want to start a new one. Detach the stale worker's signals so we
        # don't render the wrong symbol's data when it finishes later.
        if self._worker is not None:
            try:
                self._worker.finished_ok.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                self._worker.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.status_label.setText(
            f"Loading {self._ticker} ({self._period.upper()} · {self._interval})…"
        )
        self.status_label.setStyleSheet(f"color:{C_AMBER}; font-size:11px;")
        self._worker = ChartDataWorker(
            self._ticker, self._period, self._interval, self,
        )
        self._worker.finished_ok.connect(self._on_data_ready)
        self._worker.failed.connect(self._on_fetch_failed)
        self._worker.start()

    def _on_data_ready(self, df: "pd.DataFrame", ticker: str) -> None:
        # Late-arriving worker for a now-stale ticker? Ignore it.
        if ticker.upper() != self._ticker.upper():
            return
        try:
            self._df = self._compute_indicators(df)
            self._ticker = ticker
            self._x_min = 0
            self._x_max = len(self._df) - 1
            self.status_label.setText(
                f"{ticker} · {len(self._df)} bars · "
                f"{self._df.index[0].date()} \u2192 {self._df.index[-1].date()}"
            )
            self.status_label.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")
            self._redraw()
        except Exception as exc:
            import traceback
            from datetime import datetime as _dt
            from pathlib import Path as _Path
            trace = traceback.format_exc()
            print(f"[chart] redraw failed for {ticker}:\n{trace}")
            # Persist the traceback to a log file in the workspace folder.
            # When launched via Run.bat with pythonw there's no terminal,
            # so this is the only place the user can read it.
            log_path = None
            try:
                here = _Path(__file__).resolve().parent
                log_path = here / "chart_errors.log"
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        f"\n=== {_dt.now():%Y-%m-%d %H:%M:%S}  {ticker} ===\n"
                        f"{trace}\n"
                    )
            except Exception:
                pass
            self._df = None  # so _redraw doesn't loop on broken data
            log_hint = (
                f"Full traceback appended to:\n{log_path}"
                if log_path else ""
            )
            self._draw_placeholder(
                f"\u26A0   Rendering failed for {ticker}\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                + log_hint
            )

    def _on_fetch_failed(self, msg: str) -> None:
        self.status_label.setText(f"Failed: {msg}")
        self.status_label.setStyleSheet(f"color:{C_RED}; font-size:11px;")
        # Persist a record so it's recoverable later.
        try:
            from datetime import datetime as _dt
            from pathlib import Path as _Path
            log_path = _Path(__file__).resolve().parent / "chart_errors.log"
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"\n=== {_dt.now():%Y-%m-%d %H:%M:%S}  fetch fail "
                    f"{self._ticker} ===\n{msg}\n"
                )
        except Exception:
            pass
        self._draw_placeholder(
            f"\u274C   Failed to load {self._ticker}\n\n"
            f"{msg}\n\n"
            f"Tip: try a longer period or check the symbol exists."
        )

    # =====================================================================
    # Indicators
    # =====================================================================
    @staticmethod
    def _compute_indicators(df: "pd.DataFrame") -> "pd.DataFrame":
        df = df.copy()
        close = df["Close"]
        df["EMA9"]   = close.ewm(span=9,  adjust=False).mean()
        df["EMA21"]  = close.ewm(span=21, adjust=False).mean()
        df["SMA20"]  = close.rolling(20).mean()
        df["SMA50"]  = close.rolling(50).mean()
        df["SMA200"] = close.rolling(200).mean()
        std20 = close.rolling(20).std()
        df["BB_MID"] = df["SMA20"]
        df["BB_UP"]  = df["BB_MID"] + 2 * std20
        df["BB_DN"]  = df["BB_MID"] - 2 * std20
        # RSI 14
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["RSI14"] = 100 - (100 / (1 + rs))
        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["MACD"]   = ema12 - ema26
        df["SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["HIST"]   = df["MACD"] - df["SIGNAL"]
        # Average volume (20D)
        if "Volume" in df.columns:
            df["AVG_VOL"] = df["Volume"].rolling(20).mean()
        # ---- VWAP (anchored from the start of the loaded data) ----
        if "Volume" in df.columns:
            tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
            cum_pv = (tp * df["Volume"]).cumsum()
            cum_v = df["Volume"].cumsum().replace(0, np.nan)
            df["VWAP"] = cum_pv / cum_v
        # ---- Supertrend (10, 3) ----
        st_period = 10
        st_mult = 3.0
        hl2 = (df["High"] + df["Low"]) / 2.0
        tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - df["Close"].shift()).abs(),
            (df["Low"]  - df["Close"].shift()).abs(),
        ], axis=1).max(axis=1)
        atr_st = tr.ewm(alpha=1 / st_period, adjust=False, min_periods=st_period).mean()
        upper_basic = hl2 + st_mult * atr_st
        lower_basic = hl2 - st_mult * atr_st
        upper = upper_basic.copy()
        lower = lower_basic.copy()
        # Modern pandas hands back read-only views from .values, which
        # break the in-place assignments below. ``to_numpy(copy=True)``
        # guarantees a writable buffer.
        closes = df["Close"].to_numpy(copy=True)
        ub = upper.to_numpy(copy=True)
        lb = lower.to_numpy(copy=True)
        ub_basic = upper_basic.to_numpy(copy=True)
        lb_basic = lower_basic.to_numpy(copy=True)
        n = len(df)
        for i in range(1, n):
            if np.isnan(ub[i]) or np.isnan(closes[i - 1]):
                continue
            if closes[i - 1] <= ub[i - 1]:
                ub[i] = min(ub_basic[i], ub[i - 1])
            if closes[i - 1] >= lb[i - 1]:
                lb[i] = max(lb_basic[i], lb[i - 1])
        trend = np.full(n, 1, dtype=int)
        st = np.full(n, np.nan)
        for i in range(1, n):
            if np.isnan(ub[i]) or np.isnan(lb[i]):
                trend[i] = trend[i - 1]; continue
            if closes[i] > ub[i - 1]:
                trend[i] = 1
            elif closes[i] < lb[i - 1]:
                trend[i] = -1
            else:
                trend[i] = trend[i - 1]
            st[i] = lb[i] if trend[i] == 1 else ub[i]
        df["SUPERTREND"] = st
        df["ST_TREND"] = trend
        return df

    # =====================================================================
    # Draw
    # =====================================================================
    def _draw_placeholder(self, msg: Optional[str] = None) -> None:
        """Paint a centred hint message so the empty canvas tells the user
        what to do next, instead of looking like a broken widget.
        """
        if not MATPLOTLIB_AVAILABLE:
            return
        self.figure.clear()
        ax = self.figure.add_axes([0.06, 0.07, 0.91, 0.86])
        ax.set_facecolor(C_SURFACE)
        for spine in ax.spines.values():
            spine.set_color(C_BORDER)
        ax.set_xticks([]); ax.set_yticks([])
        if msg is None:
            msg = (
                "\u26A1   No symbol loaded\n\n"
                "Right-click a ticker in the sidebar or Screener\n"
                "and choose 'Open in Charts' to load it here."
            )
        ax.text(
            0.5, 0.55, msg,
            ha="center", va="center", transform=ax.transAxes,
            color=C_MUTED, fontsize=12, linespacing=1.6,
        )
        ax.text(
            0.5, 0.18,
            "Tip: you can also switch tabs after a regular Analyze run "
            "\u2014 the chart auto-loads the selected ticker.",
            ha="center", va="center", transform=ax.transAxes,
            color=C_DIM, fontsize=10, fontstyle="italic",
        )
        self.canvas.draw_idle()

    def _redraw(self) -> None:
        if not MATPLOTLIB_AVAILABLE:
            return
        if self._df is None or self._df.empty:
            self._draw_placeholder()
            return
        self.figure.clear()
        self._crosshair = {}
        self._tooltip = None
        self._annotations = []

        # Decide how many panes are visible
        show_vol  = self.chk_volume.isChecked()
        show_rsi  = self.chk_rsi.isChecked()
        show_macd = self.chk_macd.isChecked()

        # Pane heights (relative)
        ratios: list[int] = [5]          # main price pane always shown
        if show_vol:  ratios.append(1)
        if show_rsi:  ratios.append(1)
        if show_macd: ratios.append(1)
        gs = self.figure.add_gridspec(
            len(ratios), 1, height_ratios=ratios,
            hspace=0.06, left=0.06, right=0.97, top=0.96, bottom=0.07,
        )
        axes: list = [self.figure.add_subplot(gs[0])]
        for k in range(1, len(ratios)):
            axes.append(self.figure.add_subplot(gs[k], sharex=axes[0]))
        ax_price = axes[0]
        ax_vol = axes[1] if show_vol else None
        ax_rsi = (
            axes[2] if (show_vol and show_rsi) else
            axes[1] if (not show_vol and show_rsi) else None
        )
        # Determine the macd index dynamically
        idx = 1
        if show_vol: idx += 1
        if show_rsi: idx += 1
        ax_macd = axes[idx] if show_macd and idx < len(axes) else None

        # Apply view limits (integer bar indices map to x)
        df = self._df
        x = np.arange(len(df))
        xlo, xhi = self._x_min, self._x_max

        # ---- Main pane: candlesticks + overlays --------------------------
        self._draw_candles(ax_price, df, x)
        if self.chk_ema9.isChecked():
            ax_price.plot(x, df["EMA9"].values, color=C_ORANGE, linewidth=1.0, label="EMA 9")
        if self.chk_ema21.isChecked():
            ax_price.plot(x, df["EMA21"].values, color=C_AMBER, linewidth=1.0, label="EMA 21")
        if self.chk_sma50.isChecked():
            ax_price.plot(x, df["SMA50"].values, color=C_PURPLE, linewidth=1.0, label="SMA 50")
        if self.chk_sma200.isChecked():
            ax_price.plot(x, df["SMA200"].values, color=C_CYAN, linewidth=1.0, label="SMA 200")
        if self.chk_bb.isChecked():
            ax_price.plot(x, df["BB_UP"].values, color=C_BOLL_LINE, linewidth=1.1, alpha=0.9, label="BB")
            ax_price.plot(x, df["BB_DN"].values, color=C_BOLL_LINE, linewidth=1.1, alpha=0.9)
            ax_price.plot(x, df["BB_MID"].values, color=C_BOLL_LINE, linewidth=0.8,
                          alpha=0.55, linestyle="--")
            ax_price.fill_between(
                x, df["BB_DN"].values, df["BB_UP"].values,
                color=C_BOLL_FILL, alpha=0.10,
            )
        # ---- Supertrend (segmented by trend direction) ----
        if self.chk_supertrend.isChecked() and "SUPERTREND" in df.columns:
            st = df["SUPERTREND"].values
            tr_ = df["ST_TREND"].values if "ST_TREND" in df.columns else None
            if tr_ is not None:
                # Split into long/short segments so colour can switch
                long_y = np.where(tr_ == 1, st, np.nan)
                short_y = np.where(tr_ == -1, st, np.nan)
                ax_price.plot(x, long_y, color=C_STREND_UP, linewidth=1.5,
                              label="Supertrend ↑")
                ax_price.plot(x, short_y, color=C_STREND_DN, linewidth=1.5,
                              label="Supertrend ↓")
        # ---- VWAP ----
        if self.chk_vwap.isChecked() and "VWAP" in df.columns:
            ax_price.plot(x, df["VWAP"].values, color=C_VWAP, linewidth=1.3,
                          alpha=0.95, linestyle="-", label="VWAP")

        # Title
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else None
        chg = (last["Close"] - prev["Close"]) if prev is not None else 0.0
        chg_pct = (chg / prev["Close"] * 100.0) if (prev is not None and prev["Close"]) else 0.0
        chg_color = C_GREEN if chg >= 0 else C_RED
        ax_price.set_title(
            f"{self._ticker}  ·  ${last['Close']:.2f}  "
            f"({chg:+.2f} / {chg_pct:+.2f}%)",
            color=chg_color, loc="left", fontsize=11, fontweight="bold",
        )

        # Pattern detection annotations
        if self.chk_patterns.isChecked():
            self._annotate_patterns(ax_price, df, x)

        # Legend (small, top-left, transparent)
        if any([self.chk_ema9.isChecked(), self.chk_ema21.isChecked(),
                self.chk_sma50.isChecked(), self.chk_sma200.isChecked()]):
            leg = ax_price.legend(loc="upper left", fontsize=8, frameon=False, ncols=4)
            for t in leg.get_texts():
                t.set_color(C_TEXT)
        self._style_axes(ax_price)
        ax_price.set_ylabel("Price ($)", color=C_TEXT, fontsize=9)

        # ---- Volume ------------------------------------------------------
        if ax_vol is not None:
            vol = df["Volume"].values if "Volume" in df else np.zeros(len(df))
            colors = []
            for i in range(len(df)):
                if i == 0:
                    colors.append(C_DIM)
                else:
                    colors.append(C_GREEN if df["Close"].iloc[i] >= df["Close"].iloc[i - 1] else C_RED)
            ax_vol.bar(x, vol, color=colors, alpha=0.6, width=0.9)
            if "AVG_VOL" in df.columns:
                ax_vol.plot(x, df["AVG_VOL"].values, color=C_AMBER, linewidth=1.0,
                            label="20D avg")
                leg = ax_vol.legend(loc="upper left", fontsize=8, frameon=False)
                for t in leg.get_texts():
                    t.set_color(C_TEXT)
            ax_vol.set_ylabel("Vol", color=C_TEXT, fontsize=9)
            self._style_axes(ax_vol)

        # ---- RSI 14 ------------------------------------------------------
        if ax_rsi is not None:
            ax_rsi.plot(x, df["RSI14"].values, color=C_RED, linewidth=1.0)
            ax_rsi.axhline(70, color=C_AMBER, linestyle="--", alpha=0.5, linewidth=0.7)
            ax_rsi.axhline(50, color=C_DIM, linestyle=":", alpha=0.4, linewidth=0.6)
            ax_rsi.axhline(30, color=C_GREEN, linestyle="--", alpha=0.5, linewidth=0.7)
            ax_rsi.fill_between(x, 70, df["RSI14"].values, where=df["RSI14"].values > 70,
                                color=C_RED, alpha=0.12, interpolate=True)
            ax_rsi.fill_between(x, 30, df["RSI14"].values, where=df["RSI14"].values < 30,
                                color=C_GREEN, alpha=0.12, interpolate=True)
            ax_rsi.set_ylim(0, 100)
            ax_rsi.set_ylabel("RSI 14", color=C_TEXT, fontsize=9)
            self._style_axes(ax_rsi)

        # ---- MACD --------------------------------------------------------
        if ax_macd is not None:
            ax_macd.plot(x, df["MACD"].values, color=C_ACCENT, linewidth=1.0, label="MACD")
            ax_macd.plot(x, df["SIGNAL"].values, color=C_ORANGE, linewidth=1.0, label="Signal")
            hist = df["HIST"].values
            colors = [C_GREEN if h >= 0 else C_RED for h in hist]
            ax_macd.bar(x, hist, color=colors, alpha=0.5, width=0.9)
            ax_macd.axhline(0, color=C_DIM, linestyle="-", alpha=0.3, linewidth=0.6)
            leg = ax_macd.legend(loc="upper left", fontsize=8, frameon=False, ncols=2)
            for t in leg.get_texts():
                t.set_color(C_TEXT)
            ax_macd.set_ylabel("MACD", color=C_TEXT, fontsize=9)
            self._style_axes(ax_macd)

        # Hide x labels on all but the bottom pane
        for a in axes[:-1]:
            a.tick_params(labelbottom=False)
        # Format bottom axis x ticks as dates by index→date lookup
        self._format_x_ticks(axes[-1], df)

        # Apply zoom/pan view limits with a small right-edge offset so the
        # newest candle isn't flush against the axis spine.
        right_pad = max(2, (xhi - xlo) * 0.025)
        for a in axes:
            a.set_xlim(xlo - 0.5, xhi + right_pad)

        # Persist refs for the crosshair handler
        self._axes = axes
        self._ax_price = ax_price
        self._ax_vol = ax_vol
        self._ax_rsi = ax_rsi
        self._ax_macd = ax_macd
        self._x = x

        self.canvas.draw_idle()

    # ---------------------------------------------------------------------
    # Sub-helpers
    # ---------------------------------------------------------------------
    @staticmethod
    def _style_axes(ax) -> None:
        ax.set_facecolor(C_SURFACE)
        ax.tick_params(colors=C_MUTED, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(C_BORDER)
        ax.grid(True, alpha=0.12, color=C_BORDER, linewidth=0.5)
        ax.yaxis.label.set_color(C_TEXT)
        ax.xaxis.label.set_color(C_TEXT)

    @staticmethod
    def _format_x_ticks(ax, df: "pd.DataFrame") -> None:
        n = len(df)
        if n == 0:
            return
        # Decide format based on the bar cadence: if first two bars are < 24h
        # apart, this is an intraday chart and we show time-of-day, otherwise
        # date-only.
        is_intraday = False
        if n >= 2:
            delta = df.index[1] - df.index[0]
            try:
                seconds = delta.total_seconds()
            except Exception:
                seconds = 86400
            is_intraday = seconds < 60 * 60 * 23  # < 23h apart
        step = max(1, n // 8)
        positions = list(range(0, n, step))
        fmt = "%m-%d %H:%M" if is_intraday else "%Y-%m-%d"
        labels = [df.index[i].strftime(fmt) for i in positions]
        ax.set_xticks(positions)
        ax.set_xticklabels(labels, rotation=0, fontsize=8)

    def _draw_candles(self, ax, df: "pd.DataFrame", x) -> None:
        """Hand-drawn candlesticks. Brighter palette + thinner bodies for the
        'taller, slimmer' look. Up = #00FF85, Down = #FF2D55.
        """
        opens  = df["Open"].values
        highs  = df["High"].values
        lows   = df["Low"].values
        closes = df["Close"].values
        # Adaptive width: tighter when there are many bars, never wider than 0.45
        n_visible = max(self._x_max - self._x_min + 1, 1)
        if n_visible > 240:
            width = 0.30
        elif n_visible > 120:
            width = 0.38
        else:
            width = 0.45
        for i in range(len(df)):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            if np.isnan([o, h, l, c]).any():
                continue
            color = C_UP_BRIGHT if c >= o else C_DN_BRIGHT
            # Wick — slightly thicker so it reads against the dark background
            ax.vlines(x[i], l, h, color=color, linewidth=0.9, alpha=0.95)
            body_lo = min(o, c)
            body_hi = max(o, c)
            body_h = max(body_hi - body_lo, (h - l) * 0.001 if h != l else 0.001)
            rect = Rectangle(
                (x[i] - width / 2, body_lo), width, body_h,
                facecolor=color, edgecolor=color, alpha=1.0, linewidth=0.6,
            )
            ax.add_patch(rect)

    def _annotate_patterns(self, ax, df: "pd.DataFrame", x) -> None:
        """Detect & annotate the most-recent pivots, S/R levels, candlestick
        patterns and MA crosses."""
        highs = df["High"].values
        lows = df["Low"].values
        closes = df["Close"].values
        n = len(df)
        if n < 20:
            return

        # Pivots
        ph, pl = find_pivots(highs, lows, lookback=5)
        # Draw the last few pivots as small markers
        for i in ph[-6:]:
            ax.plot(x[i], highs[i], marker="v", color=C_RED, markersize=6, alpha=0.85)
        for i in pl[-6:]:
            ax.plot(x[i], lows[i], marker="^", color=C_GREEN, markersize=6, alpha=0.85)

        # Support / resistance horizontal lines (clustered pivots)
        pivot_high_prices = [float(highs[i]) for i in ph[-20:]]
        pivot_low_prices = [float(lows[i]) for i in pl[-20:]]
        resistance = cluster_levels(pivot_high_prices, tolerance_pct=0.6, max_levels=3)
        support = cluster_levels(pivot_low_prices, tolerance_pct=0.6, max_levels=3)
        x_end = len(df) - 1
        for lvl in resistance:
            ax.axhline(lvl, color=C_RED, alpha=0.18, linewidth=0.9, linestyle="--")
            ax.text(x_end + 0.5, lvl, f" R ${lvl:.2f}", color=C_RED,
                    fontsize=8, va="center")
        for lvl in support:
            ax.axhline(lvl, color=C_GREEN, alpha=0.18, linewidth=0.9, linestyle="--")
            ax.text(x_end + 0.5, lvl, f" S ${lvl:.2f}", color=C_GREEN,
                    fontsize=8, va="center")

        # MA crosses (50/200)
        cross = detect_ma_cross(df["SMA50"].values, df["SMA200"].values)
        if cross == "golden":
            ax.annotate(
                "Golden Cross",
                xy=(x[-1], closes[-1]),
                xytext=(x[-1] - 6, closes[-1] * 1.03),
                fontsize=9, color=C_GREEN, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_GREEN, lw=1.2),
            )
        elif cross == "death":
            ax.annotate(
                "Death Cross",
                xy=(x[-1], closes[-1]),
                xytext=(x[-1] - 6, closes[-1] * 0.97),
                fontsize=9, color=C_RED, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.2),
            )

        # Candlestick patterns on the most-recent 30 bars
        opens = df["Open"].values
        recent_start = max(0, n - 30)
        for i in range(recent_start, n):
            prev_o = opens[i - 1] if i > 0 else None
            prev_c = closes[i - 1] if i > 0 else None
            pat = detect_candlestick(opens[i], highs[i], lows[i], closes[i], prev_o, prev_c)
            if not pat:
                continue
            color = C_GREEN if pat in ("Hammer", "Bullish Engulfing") else (
                C_RED if pat in ("Shooting Star", "Bearish Engulfing") else C_AMBER
            )
            ax.annotate(
                pat, xy=(x[i], lows[i]),
                xytext=(x[i], lows[i] - (highs[i] - lows[i]) * 0.8),
                fontsize=7, color=color, ha="center",
                arrowprops=dict(arrowstyle="-", color=color, lw=0.6, alpha=0.6),
            )

    # =====================================================================
    # Period / view-state controls
    # =====================================================================
    def _on_period_changed(self, _idx: int) -> None:
        if self._ticker:
            data = self.period_combo.currentData()
            if isinstance(data, tuple):
                self._period, self._interval = data
            else:
                self._period, self._interval = data, "1d"
            self._fetch()

    def _on_expand_left(self) -> None:
        # Bump the period one step up (1y → 2y → 5y → max)
        idx = self.period_combo.currentIndex()
        if idx < self.period_combo.count() - 1:
            self.period_combo.setCurrentIndex(idx + 1)

    def _on_reset_view(self) -> None:
        if self._df is None:
            return
        self._x_min = 0
        self._x_max = len(self._df) - 1
        self._redraw()

    # =====================================================================
    # Mouse interactions
    # =====================================================================
    def _on_motion(self, event) -> None:
        if self._df is None or event.inaxes is None:
            return
        # Pan in progress?
        if self._pan_anchor_x is not None and event.xdata is not None:
            self._do_pan(event.xdata)
            return
        self._draw_crosshair(event)

    def _on_scroll(self, event) -> None:
        """Wheel zoom — anchored on the *right edge*. x_max stays pinned
        to whichever bar is currently at the right; only x_min moves so
        the chart expands or contracts from the left.
        """
        if self._df is None or event.inaxes is None:
            return
        xlo, xhi = self._x_min, self._x_max
        width = xhi - xlo
        if width <= 0:
            return
        factor = 0.85 if event.button == "up" else 1.18
        new_width = max(20, int(width * factor))
        new_xhi = xhi                              # right edge pinned
        new_xlo = max(0, new_xhi - new_width)
        if new_xhi - new_xlo < 20:
            return
        self._x_min = new_xlo
        self._x_max = new_xhi
        self._redraw()

    def _on_press(self, event) -> None:
        if event.button == 1 and event.inaxes is not None and event.xdata is not None:
            if getattr(event, "dblclick", False):
                self._centre_on(event.xdata)
            else:
                self._pan_anchor_x = event.xdata
                self._pan_anchor_range = (self._x_min, self._x_max)
                try:
                    self.canvas.setCursor(Qt.ClosedHandCursor)
                except Exception:
                    pass

    def _on_release(self, event) -> None:
        self._pan_anchor_x = None
        self._pan_anchor_range = None
        try:
            self.canvas.setCursor(Qt.OpenHandCursor)
        except Exception:
            pass

    def _on_leave(self, event) -> None:
        # Hide crosshair when cursor leaves the axes
        for ax, lines in self._crosshair.items():
            for ln in lines:
                ln.set_visible(False)
        if self._tooltip is not None:
            self._tooltip.set_visible(False)
        self.canvas.draw_idle()

    # ---- Pan + centre helpers ---------------------------------------
    def _do_pan(self, xdata: float) -> None:
        if self._pan_anchor_x is None or self._pan_anchor_range is None or self._df is None:
            return
        dx = self._pan_anchor_x - xdata
        new_xlo = int(round(self._pan_anchor_range[0] + dx))
        new_xhi = int(round(self._pan_anchor_range[1] + dx))
        n = len(self._df) - 1
        width = new_xhi - new_xlo
        # Clamp into bounds while preserving width
        if new_xlo < 0:
            new_xlo, new_xhi = 0, width
        if new_xhi > n:
            new_xhi, new_xlo = n, n - width
        new_xlo = max(0, new_xlo)
        new_xhi = min(n, new_xhi)
        if new_xhi - new_xlo < 5:
            return
        self._x_min = new_xlo
        self._x_max = new_xhi
        # Quick xlim update instead of a full redraw for smooth panning
        right_pad = max(2, (new_xhi - new_xlo) * 0.025)
        for a in getattr(self, "_axes", []):
            a.set_xlim(new_xlo - 0.5, new_xhi + right_pad)
        self.canvas.draw_idle()

    def _centre_on(self, xdata: float) -> None:
        if self._df is None:
            return
        width = self._x_max - self._x_min
        half = width // 2
        cx = int(round(xdata))
        new_xlo = max(0, cx - half)
        new_xhi = min(len(self._df) - 1, cx + half)
        if new_xhi - new_xlo < 5:
            return
        self._x_min, self._x_max = new_xlo, new_xhi
        self._redraw()

    # ---- Crosshair + tooltip ----------------------------------------
    def _draw_crosshair(self, event) -> None:
        if self._df is None or event.xdata is None:
            return
        xi = int(round(event.xdata))
        if xi < 0 or xi >= len(self._df):
            return
        # Build / update vertical lines on every axis, horizontal on the price axis
        for ax in getattr(self, "_axes", []):
            lines = self._crosshair.get(ax, [])
            if not lines:
                v = ax.axvline(xi, color=C_CYAN, linewidth=0.6, alpha=0.5, linestyle="-")
                lines = [v]
                if ax is getattr(self, "_ax_price", None):
                    h = ax.axhline(event.ydata if event.ydata is not None else 0,
                                   color=C_CYAN, linewidth=0.6, alpha=0.5, linestyle="-")
                    lines.append(h)
                self._crosshair[ax] = lines
            else:
                lines[0].set_xdata([xi, xi])
                lines[0].set_visible(True)
                if len(lines) > 1 and event.ydata is not None and ax is event.inaxes:
                    lines[1].set_ydata([event.ydata, event.ydata])
                    lines[1].set_visible(True)
        # Tooltip text on the price pane
        row = self._df.iloc[xi]
        date_str = self._df.index[xi].strftime("%Y-%m-%d")
        parts = [
            f"{date_str}",
            f"O {row['Open']:.2f}   H {row['High']:.2f}   L {row['Low']:.2f}   C {row['Close']:.2f}",
        ]
        if "Volume" in self._df.columns and not np.isnan(row["Volume"]):
            parts.append(f"Vol {row['Volume']:,.0f}")
        if not np.isnan(row.get("RSI14", np.nan)):
            parts.append(f"RSI14 {row['RSI14']:.1f}")
        if not np.isnan(row.get("MACD", np.nan)):
            parts.append(f"MACD {row['MACD']:+.3f}")
        msg = "   ·   ".join(parts)
        ax = self._ax_price
        if self._tooltip is None:
            self._tooltip = ax.text(
                0.005, 0.985, msg, transform=ax.transAxes,
                color=C_TEXT, fontsize=9, fontfamily="monospace",
                verticalalignment="top",
                bbox=dict(boxstyle="round,pad=0.4", fc=C_BG, ec=C_BORDER, alpha=0.92),
            )
        else:
            self._tooltip.set_text(msg)
            self._tooltip.set_visible(True)
        self.canvas.draw_idle()
