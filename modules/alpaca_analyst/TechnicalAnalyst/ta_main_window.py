#!/usr/bin/env python3
"""
ta_main_window.py — Technical Analyst Pro
Full PySide6 main window: image mode, data mode, embedded chart,
analysis panel with scenarios, AI insights, and export.
"""

from __future__ import annotations

import os, sys, json, tempfile
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox,
    QTextEdit, QProgressBar, QFrame, QFileDialog, QScrollArea,
    QGroupBox, QFormLayout, QStatusBar, QToolBar, QSizePolicy,
    QMessageBox, QApplication, QButtonGroup, QRadioButton,
    QListWidget, QListWidgetItem, QSpacerItem,
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QSize, QTimer, QMimeData, QUrl,
)
from PySide6.QtGui import (
    QFont, QPixmap, QDragEnterEvent, QDropEvent, QPainter,
    QAction, QIcon, QColor, QTextCursor,
)

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    MATPLOTLIB_OK = True
except ImportError:
    MATPLOTLIB_OK = False

# Local modules
from ta_engine   import DataFetcher, TechnicalAnalyzer, ChartGenerator, CacheManager
from ta_ai_engine import AIInsightsEngine
from ta_reports  import ReportGenerator
from ta_settings import ConfigManager, SettingsDialog
from ta_watchlist import WatchlistWidget


# ═══════════════════════════════════════════════════════════════════════════
#  WORKER THREADS
# ═══════════════════════════════════════════════════════════════════════════
class DataAnalysisWorker(QThread):
    """Fetch data → run TA → generate AI (off-thread)."""
    progress  = Signal(str)
    finished  = Signal(dict, object, dict)   # analysis, figure, ai_insights
    error     = Signal(str)

    def __init__(self, symbol, interval, period, config, parent=None):
        super().__init__(parent)
        self.symbol   = symbol
        self.interval = interval
        self.period   = period
        self.config   = config

    def run(self):
        try:
            creds   = self.config.credentials
            data_dir= os.path.join(os.path.dirname(__file__), "data")
            cache   = CacheManager(os.path.join(data_dir, "cache.db"))
            fetcher = DataFetcher(cache, creds)
            analyzer= TechnicalAnalyzer()
            charter = ChartGenerator()
            ai_eng  = AIInsightsEngine(creds)

            self.progress.emit(f"Fetching {self.symbol} ({self.interval}, {self.period})…")
            df = fetcher.fetch_ohlcv(
                self.symbol,
                interval=self.interval,
                period=self.period,
                source=self.config.get("data_source","auto"),
            )
            if df is None or df.empty:
                self.error.emit(f"No data returned for '{self.symbol}'. Check symbol and data source.")
                return

            self.progress.emit("Running technical analysis…")
            analysis = analyzer.analyze(df, symbol=self.symbol)
            if "error" in analysis:
                self.error.emit(analysis["error"])
                return

            self.progress.emit("Generating chart…")
            fig = charter.create(
                df, analysis=analysis,
                title=f"{self.symbol}  —  {self.interval} Chart  |  ${analysis['current_price']:,.2f}",
                show_ma =self.config.get("show_ma", True),
                show_vol=self.config.get("show_volume", True),
                show_sr =self.config.get("show_sr", True),
                show_bb =self.config.get("show_bb", False),
            )

            # Save chart image for reports
            chart_dir = os.path.join(data_dir, "charts")
            os.makedirs(chart_dir, exist_ok=True)
            chart_path = os.path.join(
                chart_dir,
                f"{self.symbol}_{self.interval}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            )
            charter.save(fig, chart_path)
            analysis["_chart_path"] = chart_path
            analysis["_df_len"]     = len(df)

            ai_result = {}
            if self.config.get("ai_enabled", True):
                self.progress.emit("Generating AI insights…")
                ai_result = ai_eng.generate_insights(
                    analysis,
                    use_perplexity=self.config.get("use_perplexity", True),
                    progress_cb=self.progress.emit,
                )

            # Auto-save markdown
            if self.config.get("auto_save_md", True):
                self.progress.emit("Saving markdown report…")
                reports_dir = self.config.get("reports_dir","") or os.path.join(data_dir, "reports")
                os.makedirs(reports_dir, exist_ok=True)
                gen = ReportGenerator()
                gen.save_markdown(analysis, reports_dir, ai_result, chart_path)

            self.finished.emit(analysis, fig, ai_result)

        except Exception as e:
            import traceback
            self.error.emit(f"Analysis failed: {e}\n{traceback.format_exc()}")


class ImageAnalysisWorker(QThread):
    """Send image to Gemini vision (off-thread)."""
    progress = Signal(str)
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, image_path: str, config, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.config     = config

    def run(self):
        try:
            ai_eng = AIInsightsEngine(self.config.credentials)
            self.progress.emit("Sending chart to Gemini Vision…")
            result = ai_eng.analyze_image(self.image_path, progress_cb=self.progress.emit)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(f"Image analysis failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  DRAG-DROP IMAGE WIDGET
# ═══════════════════════════════════════════════════════════════════════════
class ImageDropZone(QLabel):
    """Chart image drop zone with click-to-browse."""
    image_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(200)
        self._reset()
        self.setCursor(Qt.PointingHandCursor)

    def _reset(self):
        self.setText(
            "📂  Drop chart image here\n\nor click to browse\n\n"
            "Supported: PNG, JPG, JPEG, WEBP"
        )
        self.setStyleSheet(
            "QLabel{background:#0f1a2e;border:2px dashed #2d2d44;border-radius:10px;"
            "color:#606080;font-size:13px;}"
            "QLabel:hover{border-color:#00b4d8;color:#00b4d8;}"
        )
        self._path = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Chart Image", "",
                "Images (*.png *.jpg *.jpeg *.webp);;All Files (*)"
            )
            if path: self._load(path)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path): self._load(path)

    def _load(self, path: str):
        self._path = path
        pix = QPixmap(path).scaled(
            self.width()-20, self.height()-20,
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setPixmap(pix)
        self.setStyleSheet(
            "QLabel{background:#0f1a2e;border:2px solid #00b4d8;border-radius:10px;}"
        )
        self.image_dropped.emit(path)

    @property
    def image_path(self): return self._path

    def clear_image(self): self._reset()


# ═══════════════════════════════════════════════════════════════════════════
#  SIGNAL BADGE WIDGET
# ═══════════════════════════════════════════════════════════════════════════
class SignalBadge(QLabel):
    COLOR_MAP = {
        "BULLISH": ("#06d6a0","#0f2e26"), "UPTREND": ("#06d6a0","#0f2e26"),
        "STRONG":  ("#06d6a0","#0f2e26"), "CONFIRMING": ("#06d6a0","#0f2e26"),
        "BEARISH": ("#e94560","#2e0f1a"), "DOWNTREND": ("#e94560","#2e0f1a"),
        "WEAK":    ("#e94560","#2e0f1a"), "BEARISH_DIVERGENCE": ("#e94560","#2e0f1a"),
        "NEUTRAL": ("#ffd700","#2e2a0f"), "SIDEWAYS": ("#ffd700","#2e2a0f"),
        "MODERATE":("#ffd700","#2e2a0f"), "MIXED": ("#ffd700","#2e2a0f"),
        "RISING":  ("#06d6a0","#0f2e26"), "FALLING": ("#e94560","#2e0f1a"),
        "FLAT":    ("#a0a0c0","#1a1a2e"), "ABOVE": ("#06d6a0","#0f2e26"),
        "BELOW":   ("#e94560","#2e0f1a"), "AT": ("#ffd700","#2e2a0f"),
        "UNKNOWN": ("#a0a0c0","#1a1a2e"), "STABLE": ("#a0a0c0","#1a1a2e"),
        "INCREASING": ("#06d6a0","#0f2e26"), "DECREASING": ("#e94560","#2e0f1a"),
        "BULLISH_DIVERGENCE": ("#06d6a0","#0f2e26"),
    }

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        fc, bc = self.COLOR_MAP.get(text.upper(), ("#a0a0c0","#1a1a2e"))
        self.setStyleSheet(
            f"QLabel{{background:{bc};color:{fc};border:1px solid {fc}44;"
            f"border-radius:4px;padding:2px 8px;font-size:10px;font-weight:bold;}}"
        )
        self.setFixedHeight(20)


# ═══════════════════════════════════════════════════════════════════════════
#  SCENARIO CARD
# ═══════════════════════════════════════════════════════════════════════════
class ScenarioCard(QFrame):
    def __init__(self, scenario: dict, parent=None):
        super().__init__(parent)
        typ   = scenario.get("type","NEUTRAL")
        prob  = scenario.get("probability", 0)
        name  = scenario.get("name","")
        desc  = scenario.get("description","")
        facts = scenario.get("supporting_factors",[])
        tgts  = scenario.get("target_levels",[])
        inv   = scenario.get("invalidation_level", 0)

        color_map = {"BULLISH":"#06d6a0","BEARISH":"#e94560","NEUTRAL":"#ffd700"}
        color = color_map.get(typ,"#a0a0c0")

        self.setStyleSheet(
            f"ScenarioCard{{background:#16213e;border:1px solid {color}44;"
            f"border-left:3px solid {color};border-radius:6px;margin:3px;}}"
        )
        self.setFrameShape(QFrame.StyledPanel)

        vl = QVBoxLayout(self)
        vl.setSpacing(6); vl.setContentsMargins(10,8,10,8)

        # Title row
        title_row = QHBoxLayout()
        icon = {"BULLISH":"🟢","BEARISH":"🔴","NEUTRAL":"🟡"}.get(typ,"⚪")
        title_lbl = QLabel(f"{icon}  {name}")
        title_lbl.setFont(QFont("Segoe UI", 9, QFont.Bold))
        title_lbl.setStyleSheet(f"color:{color};")
        title_lbl.setWordWrap(True)
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        prob_lbl = QLabel(f"{prob}%")
        prob_lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))
        prob_lbl.setStyleSheet(f"color:{color};")
        title_row.addWidget(prob_lbl)
        vl.addLayout(title_row)

        # Probability bar
        bar = QProgressBar()
        bar.setRange(0, 100); bar.setValue(prob)
        bar.setFixedHeight(6); bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar{{background:#0f1a2e;border:none;border-radius:3px;}}"
            f"QProgressBar::chunk{{background:{color};border-radius:3px;}}"
        )
        vl.addWidget(bar)

        # Description
        desc_lbl = QLabel(desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color:#c0c0d0;font-size:9px;")
        vl.addWidget(desc_lbl)

        # Factors
        for f in facts[:3]:
            fl = QLabel(f"• {f}")
            fl.setWordWrap(True)
            fl.setStyleSheet("color:#808090;font-size:8px;")
            vl.addWidget(fl)

        # Targets / Invalidation
        meta_row = QHBoxLayout()
        if tgts:
            tgt_str = ", ".join(f"${t:,.2f}" for t in tgts)
            t_lbl = QLabel(f"Targets: {tgt_str}")
            t_lbl.setStyleSheet(f"color:{color};font-size:8px;")
            meta_row.addWidget(t_lbl)
        meta_row.addStretch()
        inv_lbl = QLabel(f"Invalidation: ${inv:,.2f}")
        inv_lbl.setStyleSheet("color:#e94560;font-size:8px;")
        meta_row.addWidget(inv_lbl)
        vl.addLayout(meta_row)


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):

    APP_VERSION = "1.0.0"

    def __init__(self):
        super().__init__()
        # Core paths
        self._app_dir  = os.path.dirname(os.path.abspath(__file__))
        self._data_dir = os.path.join(self._app_dir, "data")
        os.makedirs(self._data_dir, exist_ok=True)
        os.makedirs(os.path.join(self._data_dir, "reports"), exist_ok=True)
        os.makedirs(os.path.join(self._data_dir, "charts"),  exist_ok=True)

        self.config   = ConfigManager(os.path.join(self._data_dir, "config"))
        self._analysis: Optional[dict] = None
        self._ai_result: Optional[dict] = None
        self._worker:   Optional[QThread] = None
        self._img_worker: Optional[QThread] = None
        self._mode      = "data"   # "data" | "image"
        self._current_fig = None

        self.setWindowTitle(f"Technical Analyst Pro  v{self.APP_VERSION}")
        self.resize(1400, 860)
        self.setMinimumSize(1100, 720)

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._status_bar()
        self._connect_signals()
        self._apply_mode("data")

        self._status("Ready — enter a symbol or drop a chart image")

    # ═══════════════════════════════════════════════════════════════════════
    #  UI CONSTRUCTION
    # ═══════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root_vl = QVBoxLayout(central); root_vl.setContentsMargins(0,0,0,0); root_vl.setSpacing(0)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)
        root_vl.addWidget(main_splitter)

        main_splitter.addWidget(self._build_left_panel())
        main_splitter.addWidget(self._build_center_panel())
        main_splitter.addWidget(self._build_right_panel())
        main_splitter.setSizes([240, 780, 380])
        main_splitter.setStretchFactor(1, 4)

    # ── Left panel ────────────────────────────────────────────────────────
    def _build_left_panel(self) -> QWidget:
        w = QWidget(); w.setFixedWidth(240)
        vl = QVBoxLayout(w); vl.setContentsMargins(8,8,8,8); vl.setSpacing(8)

        # Mode toggle
        mode_grp = QGroupBox("Analysis Mode")
        mode_hl  = QHBoxLayout(mode_grp)
        self.data_radio  = QRadioButton("📊 Data")
        self.image_radio = QRadioButton("🖼 Image")
        self.data_radio.setChecked(True)
        self.data_radio.toggled.connect(lambda c: self._apply_mode("data")  if c else None)
        self.image_radio.toggled.connect(lambda c: self._apply_mode("image") if c else None)
        mode_hl.addWidget(self.data_radio); mode_hl.addWidget(self.image_radio)
        vl.addWidget(mode_grp)

        # Symbol input
        sym_grp = QGroupBox("Symbol")
        sym_vl  = QVBoxLayout(sym_grp)
        self.sym_input = QLineEdit()
        self.sym_input.setPlaceholderText("AAPL, MSFT, BTC-USD…")
        self.sym_input.setFixedHeight(32)
        self.sym_input.returnPressed.connect(self._trigger_analysis)
        sym_vl.addWidget(self.sym_input)

        # Interval + Period
        row1 = QHBoxLayout()
        self.interval_combo = QComboBox()
        self.interval_combo.addItems(["Daily","Weekly","Monthly"])
        self.interval_combo.setCurrentText(self.config.get("default_interval","Weekly"))
        self.period_combo = QComboBox()
        self.period_combo.addItems(["3 Months","6 Months","1 Year","2 Years","5 Years","10 Years"])
        self.period_combo.setCurrentText(self.config.get("default_period","2 Years"))
        row1.addWidget(self.interval_combo); row1.addWidget(self.period_combo)
        sym_vl.addLayout(row1)
        vl.addWidget(sym_grp)

        # Analyze button
        self.analyze_btn = QPushButton("▶  ANALYZE")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.setFixedHeight(38)
        self.analyze_btn.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.analyze_btn.clicked.connect(self._trigger_analysis)
        vl.addWidget(self.analyze_btn)

        # Progress bar (hidden until working)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0,0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        vl.addWidget(self.progress_bar)

        # Separator
        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setStyleSheet("color:#2d2d44")
        vl.addWidget(sep)

        # Watchlist widget
        self.watchlist = WatchlistWidget(self.config)
        self.watchlist.symbol_selected.connect(self._load_symbol)
        vl.addWidget(self.watchlist)

        # Add current symbol to watchlist
        add_wl_btn = QPushButton("☆  Add to Watchlist")
        add_wl_btn.setFixedHeight(28)
        add_wl_btn.clicked.connect(self._add_to_watchlist)
        vl.addWidget(add_wl_btn)

        vl.addStretch()

        # Settings button
        settings_btn = QPushButton("⚙  Settings")
        settings_btn.setFixedHeight(30)
        settings_btn.clicked.connect(self._open_settings)
        vl.addWidget(settings_btn)

        return w

    # ── Center panel ──────────────────────────────────────────────────────
    def _build_center_panel(self) -> QWidget:
        w = QWidget(); vl = QVBoxLayout(w); vl.setContentsMargins(4,4,4,4); vl.setSpacing(4)

        # Chart toolbar
        chart_toolbar = QHBoxLayout()
        self.show_ma_chk  = QCheckBox("MA");     self.show_ma_chk.setChecked(self.config.get("show_ma",True))
        self.show_vol_chk = QCheckBox("Volume"); self.show_vol_chk.setChecked(self.config.get("show_volume",True))
        self.show_sr_chk  = QCheckBox("S/R");    self.show_sr_chk.setChecked(self.config.get("show_sr",True))
        self.show_bb_chk  = QCheckBox("BB");     self.show_bb_chk.setChecked(self.config.get("show_bb",False))
        for chk in [self.show_ma_chk, self.show_vol_chk, self.show_sr_chk, self.show_bb_chk]:
            chk.stateChanged.connect(self._redraw_chart)
            chart_toolbar.addWidget(chk)
        chart_toolbar.addStretch()
        save_chart_btn = QPushButton("💾 Save Chart")
        save_chart_btn.setFixedHeight(24)
        save_chart_btn.clicked.connect(self._save_chart)
        chart_toolbar.addWidget(save_chart_btn)
        vl.addLayout(chart_toolbar)

        # Chart area (matplotlib canvas / image drop)
        self.chart_stack = QWidget()
        chart_stack_vl   = QVBoxLayout(self.chart_stack); chart_stack_vl.setContentsMargins(0,0,0,0)

        # Matplotlib canvas
        self._chart_container = QWidget()
        self._canvas_vl = QVBoxLayout(self._chart_container)
        self._canvas_vl.setContentsMargins(0,0,0,0)
        if MATPLOTLIB_OK:
            from ta_engine import ChartGenerator
            gen = ChartGenerator()
            self._canvas = FigureCanvas(gen._empty("Select a symbol and press Analyze"))
            self._canvas_vl.addWidget(self._canvas)
        else:
            no_chart = QLabel("matplotlib not available\nInstall with: pip install matplotlib")
            no_chart.setAlignment(Qt.AlignCenter)
            self._canvas_vl.addWidget(no_chart)

        # Image drop zone (for image mode)
        self._img_drop = ImageDropZone()
        self._img_drop.image_dropped.connect(self._on_image_dropped)

        chart_stack_vl.addWidget(self._chart_container)
        chart_stack_vl.addWidget(self._img_drop)
        vl.addWidget(self.chart_stack)

        # Image analyze button (image mode)
        self.img_analyze_btn = QPushButton("🔍  Analyze Chart Image with AI")
        self.img_analyze_btn.setObjectName("primary")
        self.img_analyze_btn.setFixedHeight(34)
        self.img_analyze_btn.hide()
        self.img_analyze_btn.clicked.connect(self._trigger_image_analysis)
        vl.addWidget(self.img_analyze_btn)

        # Progress label
        self.progress_label = QLabel()
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.progress_label.setStyleSheet("color:#a0a0c0;font-size:10px;")
        self.progress_label.hide()
        vl.addWidget(self.progress_label)

        return w

    # ── Right panel ───────────────────────────────────────────────────────
    def _build_right_panel(self) -> QWidget:
        w = QWidget(); w.setMinimumWidth(340)
        vl = QVBoxLayout(w); vl.setContentsMargins(4,4,4,4); vl.setSpacing(4)

        # Price ticker strip
        self.ticker_strip = QLabel("—")
        self.ticker_strip.setFont(QFont("Consolas", 11, QFont.Bold))
        self.ticker_strip.setStyleSheet(
            "background:#0f1a2e;color:#00b4d8;padding:6px 10px;border-radius:4px;"
        )
        self.ticker_strip.setAlignment(Qt.AlignCenter)
        vl.addWidget(self.ticker_strip)

        # Analysis tabs
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self._summary_tab(),  "📊 Summary")
        self.right_tabs.addTab(self._scenarios_tab(),"🎯 Scenarios")
        self.right_tabs.addTab(self._ai_tab(),       "🤖 AI Insights")
        self.right_tabs.addTab(self._export_tab(),   "📤 Export")
        vl.addWidget(self.right_tabs)

        return w

    # ── Summary tab ───────────────────────────────────────────────────────
    def _summary_tab(self) -> QWidget:
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); scroll.setWidget(inner)
        vl = QVBoxLayout(inner); vl.setSpacing(8); vl.setContentsMargins(4,4,4,4)

        # Trend section
        self.trend_grp = self._make_signal_group("Trend")
        vl.addWidget(self.trend_grp)

        # Moving Averages section
        self.ma_grp = self._make_signal_group("Moving Averages")
        vl.addWidget(self.ma_grp)

        # Volume section
        self.vol_grp = self._make_signal_group("Volume")
        vl.addWidget(self.vol_grp)

        # Support / Resistance
        self.sr_grp = self._make_signal_group("Key Levels")
        vl.addWidget(self.sr_grp)

        # Key Observations
        obs_grp = QGroupBox("Key Observations")
        obs_vl  = QVBoxLayout(obs_grp)
        self.obs_text = QTextEdit()
        self.obs_text.setReadOnly(True)
        self.obs_text.setFixedHeight(120)
        self.obs_text.setStyleSheet(
            "background:#0f1a2e;color:#c0c0d0;font-size:10px;border:none;"
        )
        obs_vl.addWidget(self.obs_text)
        vl.addWidget(obs_grp)

        # Assessment
        ass_grp = QGroupBox("Market Assessment")
        ass_vl  = QVBoxLayout(ass_grp)
        self.assessment_text = QTextEdit()
        self.assessment_text.setReadOnly(True)
        self.assessment_text.setFixedHeight(90)
        self.assessment_text.setStyleSheet(
            "background:#0f1a2e;color:#c0c0d0;font-size:10px;border:none;"
        )
        ass_vl.addWidget(self.assessment_text)
        vl.addWidget(ass_grp)

        vl.addStretch()
        return scroll

    def _make_signal_group(self, title: str) -> QGroupBox:
        grp = QGroupBox(title)
        grp._layout = QVBoxLayout(grp)
        grp._layout.setSpacing(4)
        grp._placeholder = QLabel("No data yet")
        grp._placeholder.setStyleSheet("color:#505060;font-size:10px;")
        grp._layout.addWidget(grp._placeholder)
        return grp

    def _clear_group(self, grp: QGroupBox):
        while grp._layout.count():
            item = grp._layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        grp._placeholder = QLabel("—")
        grp._placeholder.setStyleSheet("color:#505060;font-size:10px;")
        grp._layout.addWidget(grp._placeholder)

    def _add_row_to_group(self, grp: QGroupBox, label: str, value: str, badge: bool = False):
        row = QHBoxLayout()
        lbl = QLabel(label + ":")
        lbl.setStyleSheet("color:#808090;font-size:10px;")
        lbl.setFixedWidth(110)
        row.addWidget(lbl)
        if badge:
            val_w = SignalBadge(value)
        else:
            val_w = QLabel(value)
            val_w.setStyleSheet("color:#e0e0e0;font-size:10px;")
        row.addWidget(val_w)
        row.addStretch()
        w = QWidget(); w.setLayout(row)
        # Remove placeholder on first real add
        if grp._layout.count() == 1 and grp._layout.itemAt(0).widget() == grp._placeholder:
            grp._layout.removeWidget(grp._placeholder)
            grp._placeholder.deleteLater()
        grp._layout.addWidget(w)

    # ── Scenarios tab ─────────────────────────────────────────────────────
    def _scenarios_tab(self) -> QWidget:
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        self._scenarios_inner = QWidget()
        self._scenarios_vl    = QVBoxLayout(self._scenarios_inner)
        self._scenarios_vl.setSpacing(6); self._scenarios_vl.setContentsMargins(4,4,4,4)
        placeholder = QLabel("Run an analysis to see scenarios")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("color:#505060;font-size:12px;")
        self._scenarios_vl.addWidget(placeholder)
        self._scenarios_vl.addStretch()
        scroll.setWidget(self._scenarios_inner)
        return scroll

    # ── AI Insights tab ───────────────────────────────────────────────────
    def _ai_tab(self) -> QWidget:
        w = QWidget(); vl = QVBoxLayout(w); vl.setContentsMargins(4,4,4,4)
        self.ai_text = QTextEdit()
        self.ai_text.setReadOnly(True)
        self.ai_text.setPlaceholderText(
            "AI insights will appear here after analysis.\n\n"
            "Configure Gemini and/or Perplexity API keys in Settings."
        )
        self.ai_text.setStyleSheet(
            "background:#0f1a2e;color:#c0c0d0;font-size:10px;border:none;"
        )
        vl.addWidget(self.ai_text)

        regen_btn = QPushButton("↻  Regenerate AI Insights")
        regen_btn.clicked.connect(self._regen_ai)
        vl.addWidget(regen_btn)
        return w

    # ── Export tab ────────────────────────────────────────────────────────
    def _export_tab(self) -> QWidget:
        w = QWidget(); vl = QVBoxLayout(w); vl.setContentsMargins(8,8,8,8); vl.setSpacing(10)

        lbl = QLabel("Export Analysis Report")
        lbl.setFont(QFont("Segoe UI",11,QFont.Bold))
        lbl.setStyleSheet("color:#00b4d8;")
        vl.addWidget(lbl)

        btn_md   = QPushButton("📄  Export Markdown (.md)")
        btn_html = QPushButton("🌐  Export HTML (.html)")
        btn_pdf  = QPushButton("📑  Export PDF (.pdf)")
        btn_json = QPushButton("🗄  Export JSON (.json)")
        btn_chart= QPushButton("🖼  Save Chart Image")

        for btn in [btn_md, btn_html, btn_pdf, btn_json, btn_chart]:
            btn.setFixedHeight(36)
            vl.addWidget(btn)

        btn_md.clicked.connect(lambda: self._export("md"))
        btn_html.clicked.connect(lambda: self._export("html"))
        btn_pdf.clicked.connect(lambda: self._export("pdf"))
        btn_json.clicked.connect(lambda: self._export("json"))
        btn_chart.clicked.connect(self._save_chart)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        vl.addWidget(sep)

        open_dir_btn = QPushButton("📂  Open Reports Folder")
        open_dir_btn.setFixedHeight(34)
        open_dir_btn.clicked.connect(self._open_reports_dir)
        vl.addWidget(open_dir_btn)

        vl.addStretch()
        return w

    # ── Menu / Toolbar / Status ───────────────────────────────────────────
    def _build_menu(self):
        mb = self.menuBar()
        file_m = mb.addMenu("File")
        file_m.addAction("⚙  Settings", self._open_settings)
        file_m.addSeparator()
        file_m.addAction("📂  Open Reports Folder", self._open_reports_dir)
        file_m.addSeparator()
        file_m.addAction("Exit", self.close)

        analysis_m = mb.addMenu("Analysis")
        analysis_m.addAction("▶  Analyze", self._trigger_analysis)
        analysis_m.addAction("↻  Re-analyze (no cache)", self._reanalyze_nocache)
        analysis_m.addSeparator()
        analysis_m.addAction("🤖  Regenerate AI Insights", self._regen_ai)

        export_m = mb.addMenu("Export")
        export_m.addAction("📄  Markdown", lambda: self._export("md"))
        export_m.addAction("🌐  HTML",     lambda: self._export("html"))
        export_m.addAction("📑  PDF",      lambda: self._export("pdf"))
        export_m.addAction("🗄  JSON",     lambda: self._export("json"))

        help_m = mb.addMenu("Help")
        help_m.addAction("About", self._about)

    def _build_toolbar(self):
        tb = QToolBar("Main"); tb.setMovable(False)
        tb.setIconSize(QSize(16,16)); tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        def btn(text, slot, tip=""):
            a = QAction(text, self); a.setToolTip(tip); a.triggered.connect(slot)
            tb.addAction(a); return a

        btn("▶  Analyze",    self._trigger_analysis, "Fetch data & run analysis")
        tb.addSeparator()
        btn("⚙  Settings",   self._open_settings,    "Open settings dialog")
        btn("📂  Reports",   self._open_reports_dir, "Open reports folder")
        tb.addSeparator()
        btn("↻  Clear Cache",self._clear_cache,       "Clear cached data")

    def _status_bar(self):
        sb = QStatusBar(); self.setStatusBar(sb)
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setStyleSheet("color:#a0a0c0;")
        sb.addWidget(self._status_lbl, 1)
        self._cache_lbl = QLabel("")
        self._cache_lbl.setStyleSheet("color:#505060;font-size:9px;")
        sb.addPermanentWidget(self._cache_lbl)
        self._update_cache_label()

    # ═══════════════════════════════════════════════════════════════════════
    #  SIGNALS
    # ═══════════════════════════════════════════════════════════════════════
    def _connect_signals(self):
        pass  # signals connected inline during construction

    # ═══════════════════════════════════════════════════════════════════════
    #  MODE SWITCHING
    # ═══════════════════════════════════════════════════════════════════════
    def _apply_mode(self, mode: str):
        self._mode = mode
        data_mode  = (mode == "data")
        self._chart_container.setVisible(data_mode)
        self.sym_input.setEnabled(data_mode)
        self.interval_combo.setEnabled(data_mode)
        self.period_combo.setEnabled(data_mode)
        self._img_drop.setVisible(not data_mode)
        self.img_analyze_btn.setVisible(not data_mode)
        self.analyze_btn.setVisible(data_mode)

    # ═══════════════════════════════════════════════════════════════════════
    #  ANALYSIS TRIGGERS
    # ═══════════════════════════════════════════════════════════════════════
    def _trigger_analysis(self):
        if self._mode == "image":
            self._trigger_image_analysis(); return

        symbol = self.sym_input.text().strip().upper()
        if not symbol:
            self._status("⚠  Enter a symbol first"); return
        if self._worker and self._worker.isRunning():
            self._status("Analysis in progress…"); return

        self._set_busy(True)
        self._status(f"Analyzing {symbol}…")
        self.ticker_strip.setText(f"⏳  {symbol}  —  fetching data…")

        self._worker = DataAnalysisWorker(
            symbol,
            self.interval_combo.currentText(),
            self.period_combo.currentText(),
            self.config,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_analysis_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _trigger_image_analysis(self):
        if not self._img_drop.image_path:
            self._status("⚠  Drop or select a chart image first"); return
        if self._img_worker and self._img_worker.isRunning():
            return

        self._set_busy(True)
        self._img_worker = ImageAnalysisWorker(self._img_drop.image_path, self.config, self)
        self._img_worker.progress.connect(self._on_progress)
        self._img_worker.finished.connect(self._on_image_analysis_done)
        self._img_worker.error.connect(self._on_error)
        self._img_worker.start()

    def _load_symbol(self, symbol: str):
        self.sym_input.setText(symbol)
        self.data_radio.setChecked(True)
        QTimer.singleShot(100, self._trigger_analysis)

    def _reanalyze_nocache(self):
        sym = self.sym_input.text().strip().upper()
        if sym:
            cache = CacheManager(os.path.join(self._data_dir, "cache.db"))
            cache.clear(sym)
            self._trigger_analysis()

    def _regen_ai(self):
        if not self._analysis:
            self._status("⚠  Run an analysis first"); return
        if self._worker and self._worker.isRunning(): return

        self._set_busy(True)
        self._status("Regenerating AI insights…")

        class AIWorker(QThread):
            progress = Signal(str); finished = Signal(dict); error = Signal(str)
            def __init__(self, analysis, config, parent=None):
                super().__init__(parent); self.analysis=analysis; self.config=config
            def run(self):
                try:
                    ai = AIInsightsEngine(self.config.credentials)
                    r  = ai.generate_insights(self.analysis, use_perplexity=self.config.get("use_perplexity",True), progress_cb=self.progress.emit)
                    self.finished.emit(r)
                except Exception as e: self.error.emit(str(e))

        self._ai_worker = AIWorker(self._analysis, self.config, self)
        self._ai_worker.progress.connect(self._on_progress)
        self._ai_worker.finished.connect(lambda r: (self._update_ai_panel(r), self._set_busy(False)))
        self._ai_worker.error.connect(self._on_error)
        self._ai_worker.start()

    # ═══════════════════════════════════════════════════════════════════════
    #  RESULT HANDLERS
    # ═══════════════════════════════════════════════════════════════════════
    def _on_progress(self, msg: str):
        self._status(msg)
        self.progress_label.setText(msg); self.progress_label.show()

    def _on_analysis_done(self, analysis: dict, fig, ai_result: dict):
        self._analysis  = analysis
        self._ai_result = ai_result
        self._current_fig = fig

        self._update_chart(fig)
        self._update_ticker_strip(analysis)
        self._update_summary_panel(analysis)
        self._update_scenarios_panel(analysis)
        self._update_ai_panel(ai_result)

        sym  = analysis.get("symbol","?")
        bars = analysis.get("_df_len", 0)
        self._status(f"✅  Analysis complete — {sym}  ({bars} bars)")
        self.progress_label.hide()
        self._set_busy(False)
        self._update_cache_label()

    def _on_image_analysis_done(self, result: dict):
        self._ai_result = result
        self.ai_text.setMarkdown(result.get("combined","No result"))
        self.right_tabs.setCurrentIndex(2)  # Switch to AI tab
        self._status("✅  Image analysis complete")
        self.progress_label.hide()
        self._set_busy(False)

    def _on_error(self, msg: str):
        self._set_busy(False)
        self.progress_label.hide()
        self._status(f"❌  {msg.splitlines()[0]}")
        QMessageBox.warning(self, "Analysis Error", msg)

    # ═══════════════════════════════════════════════════════════════════════
    #  PANEL UPDATES
    # ═══════════════════════════════════════════════════════════════════════
    def _update_chart(self, fig):
        if not MATPLOTLIB_OK or fig is None: return
        while self._canvas_vl.count():
            item = self._canvas_vl.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        canvas = FigureCanvas(fig)
        self._canvas_vl.addWidget(canvas)
        canvas.draw()
        self._canvas = canvas

    def _redraw_chart(self):
        """Re-generate chart with current overlay toggles."""
        if not self._analysis or not hasattr(self,"_last_df"): return
        gen = ChartGenerator()
        sym  = self._analysis.get("symbol","")
        cp   = self._analysis.get("current_price",0)
        fig  = gen.create(
            self._last_df, analysis=self._analysis,
            title=f"{sym}  —  {self.interval_combo.currentText()} Chart  |  ${cp:,.2f}",
            show_ma =self.show_ma_chk.isChecked(),
            show_vol=self.show_vol_chk.isChecked(),
            show_sr =self.show_sr_chk.isChecked(),
            show_bb =self.show_bb_chk.isChecked(),
        )
        self._current_fig = fig
        self._update_chart(fig)

    def _update_ticker_strip(self, a: dict):
        sym  = a.get("symbol","?")
        cp   = a.get("current_price",0)
        chg1 = a.get("chg_1bar",0)
        chg4 = a.get("chg_4bar",0)
        dir_ = a.get("trend",{}).get("direction","?")
        icon = "▲" if chg1>=0 else "▼"
        col  = "#06d6a0" if chg1>=0 else "#e94560"
        self.ticker_strip.setText(
            f"{sym}   ${cp:,.2f}   {icon} {chg1:+.2f}%  (4W: {chg4:+.1f}%)   ·   {dir_}"
        )
        self.ticker_strip.setStyleSheet(
            f"background:#0f1a2e;color:{col};padding:6px 10px;border-radius:4px;font-size:11px;font-weight:bold;"
        )

    def _update_summary_panel(self, a: dict):
        td  = a.get("trend",{})
        ma  = a.get("moving_averages",{})
        vd  = a.get("volume",{})
        sup = a.get("support_levels",[])
        res = a.get("resistance_levels",[])
        obs = a.get("key_observations",[])
        ass = a.get("assessment","")

        # Trend
        self._clear_group(self.trend_grp)
        self._add_row_to_group(self.trend_grp, "Direction", td.get("direction","?"), badge=True)
        self._add_row_to_group(self.trend_grp, "Strength",  td.get("strength","?"),  badge=True)
        self._add_row_to_group(self.trend_grp, "Duration",  f"{td.get('duration_bars','?')} bars")
        self._add_row_to_group(self.trend_grp, "RSI",       f"{td.get('rsi',50):.0f}")
        self._add_row_to_group(self.trend_grp, "HH/HL/LH/LL",
                               f"{td.get('hh',0)}/{td.get('hl',0)}/{td.get('lh',0)}/{td.get('ll',0)}")
        for ex in td.get("exhaustion_signals",[]):
            self._add_row_to_group(self.trend_grp, "⚠ Exhaustion", ex)

        # Moving Averages
        self._clear_group(self.ma_grp)
        self._add_row_to_group(self.ma_grp, "Alignment", ma.get("alignment","?"), badge=True)
        if ma.get("golden_cross"): self._add_row_to_group(self.ma_grp, "🟢 Signal", "Golden Cross")
        if ma.get("death_cross"):  self._add_row_to_group(self.ma_grp, "🔴 Signal", "Death Cross")
        for p in [20,50,200]:
            m = ma.get(f"ma{p}")
            if m:
                self._add_row_to_group(
                    self.ma_grp, f"MA{p}",
                    f"${m['value']:,.2f}  {m['price_relation']}  {m['slope']}  ({m['distance_pct']:+.1f}%)"
                )

        # Volume
        self._clear_group(self.vol_grp)
        self._add_row_to_group(self.vol_grp, "Trend",        vd.get("trend","?"),         badge=True)
        self._add_row_to_group(self.vol_grp, "Confirmation", vd.get("confirmation","?").replace("_"," "), badge=True)
        if vd.get("spike"):
            self._add_row_to_group(self.vol_grp, "⚡ Spike", f"{vd.get('spike_ratio',1):.1f}× avg")

        # S/R
        self._clear_group(self.sr_grp)
        for s in sup[:3]:
            self._add_row_to_group(
                self.sr_grp, f"🟢 Sup {s['distance_pct']:.1f}%",
                f"${s['price']:,.2f}  ({s['strength']})"
            )
        for r in res[:3]:
            self._add_row_to_group(
                self.sr_grp, f"🔴 Res {r['distance_pct']:.1f}%",
                f"${r['price']:,.2f}  ({r['strength']})"
            )

        # Observations
        self.obs_text.setPlainText("\n".join(f"• {o}" for o in obs))

        # Assessment
        self.assessment_text.setPlainText(ass)

    def _update_scenarios_panel(self, a: dict):
        # Clear
        while self._scenarios_vl.count():
            item = self._scenarios_vl.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        for sc in a.get("scenarios",[]):
            card = ScenarioCard(sc)
            self._scenarios_vl.addWidget(card)

        self._scenarios_vl.addStretch()
        self.right_tabs.setCurrentIndex(1)  # Switch to Scenarios tab

    def _update_ai_panel(self, ai: dict):
        if ai:
            text = ai.get("combined","") or ai.get("raw","")
            self.ai_text.setMarkdown(text) if text else self.ai_text.setPlainText("No AI insights generated.")
        else:
            self.ai_text.setPlainText("No AI insights available.")

    # ═══════════════════════════════════════════════════════════════════════
    #  EXPORT
    # ═══════════════════════════════════════════════════════════════════════
    def _export(self, fmt: str):
        if not self._analysis:
            QMessageBox.information(self, "Export", "Run an analysis first."); return

        reports_dir = self.config.get("reports_dir","") or os.path.join(self._data_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        gen   = ReportGenerator()
        chart = self._analysis.get("_chart_path","")

        if fmt == "md":
            path = gen.save_markdown(self._analysis, reports_dir, self._ai_result, chart)
        elif fmt == "html":
            path = gen.save_html(self._analysis, reports_dir, self._ai_result, chart)
        elif fmt == "pdf":
            path = gen.save_pdf(self._analysis, reports_dir, self._ai_result, chart)
        elif fmt == "json":
            path = gen.save_json(self._analysis, reports_dir, self._ai_result)
        else:
            return

        self._status(f"✅  Saved: {os.path.basename(path)}")
        if QMessageBox.question(
            self, "Export Complete",
            f"Report saved:\n{path}\n\nOpen folder?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            import subprocess
            subprocess.Popen(f'explorer "{os.path.dirname(path)}"', shell=True)

    def _save_chart(self):
        if not self._current_fig:
            self._status("⚠  No chart to save"); return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Chart", "", "PNG (*.png);;PDF (*.pdf)"
        )
        if path:
            from ta_engine import ChartGenerator
            ChartGenerator().save(self._current_fig, path)
            self._status(f"Chart saved: {os.path.basename(path)}")

    # ═══════════════════════════════════════════════════════════════════════
    #  SETTINGS / HELPERS
    # ═══════════════════════════════════════════════════════════════════════
    def _open_settings(self):
        dlg = SettingsDialog(self.config, self)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.exec()

    def _on_settings_changed(self):
        self.interval_combo.setCurrentText(self.config.get("default_interval","Weekly"))
        self.period_combo.setCurrentText(self.config.get("default_period","2 Years"))
        self.show_ma_chk.setChecked(self.config.get("show_ma",True))
        self.show_vol_chk.setChecked(self.config.get("show_volume",True))
        self.show_sr_chk.setChecked(self.config.get("show_sr",True))
        self.show_bb_chk.setChecked(self.config.get("show_bb",False))
        self._status("Settings saved")

    def _add_to_watchlist(self):
        sym = self.sym_input.text().strip().upper()
        if sym:
            self.watchlist.add_symbol_external(sym)
            self._status(f"Added {sym} to watchlist")

    def _clear_cache(self):
        cache = CacheManager(os.path.join(self._data_dir, "cache.db"))
        cache.clear()
        self._update_cache_label()
        self._status("Cache cleared")

    def _open_reports_dir(self):
        reports_dir = self.config.get("reports_dir","") or os.path.join(self._data_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        import subprocess
        subprocess.Popen(f'explorer "{reports_dir}"', shell=True)

    def _update_cache_label(self):
        try:
            cache = CacheManager(os.path.join(self._data_dir, "cache.db"))
            st = cache.stats()
            self._cache_lbl.setText(f"Cache: {st['entries']} entries, {st['size_kb']} KB")
        except Exception:
            pass

    def _set_busy(self, busy: bool):
        self.analyze_btn.setEnabled(not busy)
        self.img_analyze_btn.setEnabled(not busy)
        if busy:
            self.progress_bar.show()
        else:
            self.progress_bar.hide()
            self.progress_label.hide()
        QApplication.processEvents()

    def _status(self, msg: str):
        self._status_lbl.setText(msg)
        QApplication.processEvents()

    def _on_image_dropped(self, path: str):
        self._status(f"Image loaded: {os.path.basename(path)}")
        self.img_analyze_btn.show()

    def _about(self):
        QMessageBox.about(
            self, "Technical Analyst Pro",
            f"<b>Technical Analyst Pro v{self.APP_VERSION}</b><br><br>"
            "Professional technical analysis platform.<br><br>"
            "Data: yFinance · Alpaca · FMP<br>"
            "AI: Google Gemini · Perplexity<br><br>"
            "<i>For educational purposes only. Not investment advice.</i>"
        )
