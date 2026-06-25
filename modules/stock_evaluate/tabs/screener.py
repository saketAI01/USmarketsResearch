"""
Screener Tab — Multi-factor filter engine with preset strategies + custom criteria.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QDoubleSpinBox, QSpinBox, QGroupBox, QCheckBox,
    QProgressBar, QScrollArea, QSizePolicy, QFileDialog, QMenu,
    QLineEdit
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor, QAction
import logging
import traceback
from ..config import SCREENER_PRESETS, GICS_SECTORS, MARKET_CAP_SEGMENTS
from ..theme import ACCENT, ACCENT2, SUCCESS, DANGER, TEXT_SECONDARY, BG_CARD, BORDER, BG_PRIMARY, WARNING


class ScreenerTab(QWidget):
    open_deep_dive = Signal(str, str)   # symbol, list_name
    add_to_watchlist = Signal(str)
    watchlist_saved = Signal(str)

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.log = logging.getLogger(__name__)
        self.master_data = {}
        self._build_ui()

    def set_master_data(self, data):
        self.master_data = data

    def _build_ui(self):

        main = QHBoxLayout(self)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(12)

        # ===== LEFT SIDEBAR — Filters =====
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(330)
        self.scroll = QScrollArea()
        self.scroll.setWidget(sidebar)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFixedWidth(350)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        main.addWidget(self.scroll)

        # Toggle Button
        self.btn_toggle_sidebar = QPushButton("«")
        self.btn_toggle_sidebar.setFixedWidth(20)
        self.btn_toggle_sidebar.setFixedHeight(60)
        self.btn_toggle_sidebar.setStyleSheet(f"""
            QPushButton {{
                background: {BG_CARD};
                color: {ACCENT};
                border: 1px solid {BORDER};
                border-radius: 4px;
                font-weight: bold;
                font-size: 14pt;
            }}
            QPushButton:hover {{ background: {BG_PRIMARY}; }}
        """)
        self.btn_toggle_sidebar.clicked.connect(self._toggle_sidebar)
        main.addWidget(self.btn_toggle_sidebar)

        s_lay = QVBoxLayout(sidebar)
        s_lay.setAlignment(Qt.AlignTop)
        s_lay.setContentsMargins(12, 12, 12, 12)
        s_lay.setSpacing(8)

        title = QLabel("STOCK SCREENER")
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        s_lay.addWidget(title)

        # Preset selector
        preset_grp = QGroupBox("Strategy Presets")
        pg = QVBoxLayout(preset_grp)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(SCREENER_PRESETS.keys()))
        self.preset_combo.currentTextChanged.connect(self._apply_preset)
        pg.addWidget(self.preset_combo)
        s_lay.addWidget(preset_grp)
        
        # Fundamentals Tank
        self.tank_lbl = QLabel("Fundamentals Tank: 0/0")
        self.tank_lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
        self.tank_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.tank_bar = QProgressBar()
        self.tank_bar.setFixedHeight(12)
        self.tank_bar.setTextVisible(False)
        self.tank_bar.setStyleSheet(f"QProgressBar {{ border: 1px solid {BORDER}; border-radius: 6px; background: {BG_PRIMARY}; }} QProgressBar::chunk {{ background: {SUCCESS}; border-radius: 5px; }}")
        s_lay.addWidget(self.tank_lbl)
        s_lay.addWidget(self.tank_bar)
        s_lay.addSpacing(10)

        # Universe
        uni_grp = QGroupBox("Universe")
        ug = QVBoxLayout(uni_grp)
        self.uni_combo = QComboBox()
        self.uni_combo.addItems(["Combined", "S&P 500", "NASDAQ-100"])
        ug.addWidget(self.uni_combo)
        s_lay.addWidget(uni_grp)

        # Market Cap
        cap_grp = QGroupBox("Market Cap Segment")
        cg = QVBoxLayout(cap_grp)
        self.cap_checks = {}
        for seg in MARKET_CAP_SEGMENTS:
            cb = QCheckBox(seg)
            cb.setChecked(True)
            self.cap_checks[seg] = cb
            cg.addWidget(cb)
        s_lay.addWidget(cap_grp)

        # Sector
        sec_grp = QGroupBox("Sector")
        sg = QVBoxLayout(sec_grp)
        self.sector_combo = QComboBox()
        self.sector_combo.addItem("All Sectors")
        self.sector_combo.addItems(GICS_SECTORS)
        sg.addWidget(self.sector_combo)
        s_lay.addWidget(sec_grp)

        # Fundamental Filters
        fund_grp = QGroupBox("Fundamental Filters")
        self.fund_enabled = QCheckBox("Enable Fundamental Criteria")
        self.fund_enabled.setChecked(True)
        self.fund_enabled.setStyleSheet("font-weight: bold; color: white;")
        fg = QGridLayout(fund_grp)
        fg.addWidget(self.fund_enabled, 0, 0, 1, 3)
        row = 1

        def _add_range(label, attr_min, attr_max, lo, hi, step, dec, r):
            fg.addWidget(QLabel(label), r, 0)
            sp_min = QDoubleSpinBox()
            sp_min.setRange(lo, hi); sp_min.setSingleStep(step); sp_min.setDecimals(dec)
            sp_min.setSpecialValueText("—"); sp_min.setValue(lo)
            sp_max = QDoubleSpinBox()
            sp_max.setRange(lo, hi); sp_max.setSingleStep(step); sp_max.setDecimals(dec)
            sp_max.setSpecialValueText("—"); sp_max.setValue(hi)
            fg.addWidget(sp_min, r, 1); fg.addWidget(sp_max, r, 2)
            setattr(self, attr_min, sp_min); setattr(self, attr_max, sp_max)
            return r + 1

        row = _add_range("P/E Ratio", "pe_min", "pe_max", 0, 200, 1, 1, row)
        row = _add_range("P/B Ratio", "pb_min", "pb_max", 0, 50, 0.5, 1, row)
        row = _add_range("PEG Ratio", "peg_min", "peg_max", 0, 10, 0.1, 2, row)

        fg.addWidget(QLabel("ROE Min %"), row, 0)
        self.roe_min = QDoubleSpinBox()
        self.roe_min.setRange(0, 100); self.roe_min.setDecimals(1)
        self.roe_min.setSpecialValueText("—"); self.roe_min.setValue(0)
        fg.addWidget(self.roe_min, row, 1, 1, 2); row += 1

        fg.addWidget(QLabel("D/E Max"), row, 0)
        self.de_max = QDoubleSpinBox()
        self.de_max.setRange(0, 20); self.de_max.setDecimals(1)
        self.de_max.setSpecialValueText("—"); self.de_max.setValue(20)
        fg.addWidget(self.de_max, row, 1, 1, 2); row += 1

        fg.addWidget(QLabel("Rev Growth Min %"), row, 0)
        self.rev_growth_min = QDoubleSpinBox()
        self.rev_growth_min.setRange(-50, 200); self.rev_growth_min.setDecimals(1)
        self.rev_growth_min.setSpecialValueText("—"); self.rev_growth_min.setValue(-50)
        fg.addWidget(self.rev_growth_min, row, 1, 1, 2); row += 1

        fg.addWidget(QLabel("EPS Growth Min %"), row, 0)
        self.eps_growth_min = QDoubleSpinBox()
        self.eps_growth_min.setRange(-50, 200); self.eps_growth_min.setDecimals(1)
        self.eps_growth_min.setSpecialValueText("—"); self.eps_growth_min.setValue(-50)
        fg.addWidget(self.eps_growth_min, row, 1, 1, 2); row += 1

        fg.addWidget(QLabel("Div Yield Min %"), row, 0)
        self.div_min = QDoubleSpinBox()
        self.div_min.setRange(0, 20); self.div_min.setDecimals(1)
        self.div_min.setSpecialValueText("—"); self.div_min.setValue(0)
        fg.addWidget(self.div_min, row, 1, 1, 2); row += 1

        s_lay.addWidget(fund_grp)

        # Technical Filters
        tech_grp = QGroupBox("Technical Filters")
        self.tech_enabled = QCheckBox("Enable Technical Criteria")
        self.tech_enabled.setChecked(True)
        self.tech_enabled.setStyleSheet("font-weight: bold; color: white;")
        tg = QGridLayout(tech_grp)
        tg.addWidget(self.tech_enabled, 0, 0, 1, 3)
        tr = 1
        tr = _add_range.__wrapped__(self, tg, "RSI(14)", "rsi_min_sp", "rsi_max_sp", 0, 100, 1, 0, tr) if False else tr

        tg.addWidget(QLabel("RSI(14) Range"), tr, 0)
        self.rsi_min_sp = QDoubleSpinBox()
        self.rsi_min_sp.setRange(0, 100); self.rsi_min_sp.setDecimals(0)
        self.rsi_min_sp.setSpecialValueText("—"); self.rsi_min_sp.setValue(0)
        self.rsi_max_sp = QDoubleSpinBox()
        self.rsi_max_sp.setRange(0, 100); self.rsi_max_sp.setDecimals(0)
        self.rsi_max_sp.setSpecialValueText("—"); self.rsi_max_sp.setValue(100)
        tg.addWidget(self.rsi_min_sp, tr, 1); tg.addWidget(self.rsi_max_sp, tr, 2); tr += 1

        tg.addWidget(QLabel("Change % Range"), tr, 0)
        self.chg_min_sp = QDoubleSpinBox()
        self.chg_min_sp.setRange(-30, 30); self.chg_min_sp.setDecimals(1)
        self.chg_min_sp.setSpecialValueText("—"); self.chg_min_sp.setValue(-30)
        self.chg_max_sp = QDoubleSpinBox()
        self.chg_max_sp.setRange(-30, 30); self.chg_max_sp.setDecimals(1)
        self.chg_max_sp.setSpecialValueText("—"); self.chg_max_sp.setValue(30)
        tg.addWidget(self.chg_min_sp, tr, 1); tg.addWidget(self.chg_max_sp, tr, 2); tr += 1

        tg.addWidget(QLabel("Price vs SMA200"), tr, 0)
        self.sma200_combo = QComboBox()
        self.sma200_combo.addItems(["Any", "Above", "Below"])
        tg.addWidget(self.sma200_combo, tr, 1, 1, 2); tr += 1

        tg.addWidget(QLabel("Price vs SMA50"), tr, 0)
        self.sma50_combo = QComboBox()
        self.sma50_combo.addItems(["Any", "Above", "Below"])
        tg.addWidget(self.sma50_combo, tr, 1, 1, 2); tr += 1

        tg.addWidget(QLabel("MACD Histogram"), tr, 0)
        self.macd_combo = QComboBox()
        self.macd_combo.addItems(["Any", "Positive", "Negative"])
        tg.addWidget(self.macd_combo, tr, 1, 1, 2); tr += 1

        tg.addWidget(QLabel("ADX(14) Min"), tr, 0)
        self.adx_min_sp = QDoubleSpinBox()
        self.adx_min_sp.setRange(0, 100); self.adx_min_sp.setDecimals(0)
        self.adx_min_sp.setSpecialValueText("—"); self.adx_min_sp.setValue(0)
        tg.addWidget(self.adx_min_sp, tr, 1, 1, 2); tr += 1

        tg.addWidget(QLabel("Volume Min"), tr, 0)
        self.vol_min_sp = QSpinBox()
        self.vol_min_sp.setRange(0, 100_000_000); self.vol_min_sp.setSingleStep(500_000)
        self.vol_min_sp.setSpecialValueText("—"); self.vol_min_sp.setValue(0)
        tg.addWidget(self.vol_min_sp, tr, 1, 1, 2); tr += 1

        s_lay.addWidget(tech_grp)

        # Action buttons
        self.btn_screen = QPushButton("🔍  SCREEN NOW")
        self.btn_screen.setMinimumHeight(42)
        self.btn_screen.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.btn_screen.clicked.connect(self._update_logic_bar)
        s_lay.addWidget(self.btn_screen)

        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setProperty("class", "secondary")
        self.btn_reset.clicked.connect(self._reset_filters)
        self.btn_export = QPushButton("Export CSV")
        self.btn_export.setProperty("class", "secondary")
        self.btn_export.clicked.connect(self._export_csv)
        btn_row.addWidget(self.btn_reset)
        btn_row.addWidget(self.btn_export)
        s_lay.addLayout(btn_row)

        s_lay.addStretch()
        # main.addWidget(scroll) # Moved above

        # ===== RIGHT — Results =====
        right = QVBoxLayout()
        right.setSpacing(8)

        self.prog = QProgressBar()
        self.prog.setFixedHeight(14)
        self.prog.setTextVisible(True)
        self.prog.setValue(0)
        right.addWidget(self.prog)

        self.result_count = QLabel("Run a screen to see results")
        self.result_count.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt;")
        right.addWidget(self.result_count)

        self.logic_bar = QLabel("Strategy Logic: None")
        self.logic_bar.setWordWrap(True)
        self.logic_bar.setStyleSheet(f"""
            background: {BG_CARD};
            border: 1px solid {BORDER};
            border-radius: 4px;
            padding: 8px;
            color: {ACCENT2};
            font-size: 9pt;
            font-family: 'Segoe UI Semibold';
        """)
        right.addWidget(self.logic_bar)

        # Watchlist Creation Bar
        wl_row = QHBoxLayout()
        wl_row.setSpacing(10)
        
        self.wl_name_input = QLineEdit()
        self.wl_name_input.setPlaceholderText("New Watchlist Name")
        self.wl_name_input.setMinimumWidth(200)
        self.wl_name_input.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; color: white; padding: 5px;")
        
        wl_row.addWidget(QLabel("Cut-off Score:"))
        self.wl_cutoff_sp = QSpinBox()
        self.wl_cutoff_sp.setRange(0, 100)
        self.wl_cutoff_sp.setValue(50)
        self.wl_cutoff_sp.setStyleSheet(f"background: {BG_CARD}; color: white;")
        
        self.btn_save_wl = QPushButton("📁 Save as Watchlist")
        self.btn_save_wl.setProperty("class", "secondary")
        self.btn_save_wl.setToolTip("Create a new watchlist with stocks meeting the score cutoff")
        self.btn_save_wl.clicked.connect(self._save_to_watchlist)
        
        wl_row.addWidget(self.wl_name_input)
        wl_row.addWidget(self.wl_cutoff_sp)
        wl_row.addWidget(self.btn_save_wl)
        wl_row.addStretch()
        right.addLayout(wl_row)

        self.table = QTableWidget()
        self.table.setColumnCount(13)
        self.table.setHorizontalHeaderLabels([
            "#", "Symbol", "Company", "Sector", "Industry", "Price",
            "Mkt Cap", "P/E", "ROE", "Rev Gr%", "RSI",
            "vs SMA200", "Score"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._on_double_click)

        # Set default column widths
        widths = [40, 70, 180, 120, 140, 80, 100, 60, 60, 65, 50, 75, 60]
        for i, w in enumerate(widths):
            self.table.setColumnWidth(i, w)

        right.addWidget(self.table)
        main.addLayout(right, stretch=1)

        self._connect_signals()
        self._update_logic_bar()

    def collect_filters(self):
        """Gather current filter values into a dict."""
        f = {}
        f["universe"] = self.uni_combo.currentText()
        segs = [s for s, cb in self.cap_checks.items() if cb.isChecked()]
        if len(segs) < len(self.cap_checks):
            f["cap_segments"] = segs
        sec = self.sector_combo.currentText()
        if sec != "All Sectors":
            f["sectors"] = [sec]

        # Fundamentals
        if self.fund_enabled.isChecked():
            if self.pe_min.value() > 0: f["pe_min"] = self.pe_min.value()
            if self.pe_max.value() < 200: f["pe_max"] = self.pe_max.value()
            if self.pb_max.value() < 50: f["pb_max"] = self.pb_max.value()
            if self.peg_max.value() < 10: f["peg_max"] = self.peg_max.value()
            if self.roe_min.value() > 0: f["roe_min"] = self.roe_min.value()
            if self.de_max.value() < 20: f["debt_equity_max"] = self.de_max.value()
            if self.rev_growth_min.value() > -50: f["rev_growth_min"] = self.rev_growth_min.value()
            if self.eps_growth_min.value() > -50: f["eps_growth_min"] = self.eps_growth_min.value()
            if self.div_min.value() > 0: f["div_yield_min"] = self.div_min.value()

        # Technicals
        if self.tech_enabled.isChecked():
            if self.rsi_min_sp.value() > 0: f["rsi_min"] = self.rsi_min_sp.value()
            if self.rsi_max_sp.value() < 100: f["rsi_max"] = self.rsi_max_sp.value()
            if self.chg_min_sp.value() > -30: f["change_pct_min"] = self.chg_min_sp.value()
            if self.chg_max_sp.value() < 30: f["change_pct_max"] = self.chg_max_sp.value()
            
            sma200 = self.sma200_combo.currentText()
            if sma200 != "Any": f["price_vs_sma200"] = sma200
            sma50 = self.sma50_combo.currentText()
            if sma50 != "Any": f["price_vs_sma50"] = sma50
            macd = self.macd_combo.currentText()
            if macd != "Any": f["macd_signal"] = macd
            if self.vol_min_sp.value() > 0: f["volume_min"] = self.vol_min_sp.value()

        return f

    def populate_results(self, results):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        
        if not results:
            self.result_count.setText("0 stocks passed filters")
            return

        # Update average score for watchlist cutoff
        scores = [r.get("score", 0) for r in results if "score" in r]
        avg_score = sum(scores) / len(scores) if scores else 50
        self.wl_cutoff_sp.setValue(int(avg_score))
        from datetime import datetime
        self.wl_name_input.setText(f"Screen {datetime.now().strftime('%d %b %H:%M')}")

        for i, res in enumerate(results):
            row = self.table.rowCount()
            self.table.insertRow(row)

            def _item(val, fmt=None, align=Qt.AlignRight | Qt.AlignVCenter, color=None):
                if val is None:
                    it = QTableWidgetItem("—")
                elif fmt:
                    it = QTableWidgetItem(fmt.format(val))
                else:
                    it = QTableWidgetItem(str(val))
                it.setTextAlignment(align)
                if color:
                    it.setForeground(QColor(color))
                return it

            self.table.setItem(row, 0, _item(i + 1, align=Qt.AlignCenter))
            sym_item = _item(res.get("symbol"), align=Qt.AlignCenter, color=ACCENT)
            sym_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            self.table.setItem(row, 1, sym_item)
            comp_name = res.get("company_name") or self.master_data.get(res.get("symbol"), "—")
            self.table.setItem(row, 2, _item(comp_name, align=Qt.AlignLeft))
            self.table.setItem(row, 3, _item(res.get("sector"), align=Qt.AlignLeft))

            self.table.setItem(row, 4, _item(res.get("industry"), align=Qt.AlignLeft))
            self.table.setItem(row, 5, _item(res.get("price"), "${:,.2f}"))

            mc = res.get("market_cap")
            mc_str = "—"
            if mc:
                if mc >= 1e12: mc_str = f"${mc/1e12:.1f}T"
                elif mc >= 1e9: mc_str = f"${mc/1e9:.1f}B"
                elif mc >= 1e6: mc_str = f"${mc/1e6:.0f}M"
            self.table.setItem(row, 6, _item(mc_str))

            pe = res.get("pe_ratio")
            self.table.setItem(row, 7, _item(pe, "{:.1f}" if pe else None))

            roe = res.get("roe")
            roe_color = SUCCESS if roe and roe > 15 else DANGER if roe and roe < 5 else None
            self.table.setItem(row, 8, _item(roe, "{:.1f}%" if roe else None, color=roe_color))

            rg = res.get("revenue_growth")
            rg_color = SUCCESS if rg and rg > 10 else DANGER if rg and rg < 0 else None
            self.table.setItem(row, 9, _item(rg, "{:.1f}" if rg else None, color=rg_color))

            rsi = res.get("rsi_14")
            rsi_color = DANGER if rsi and rsi > 70 else SUCCESS if rsi and rsi < 30 else None
            self.table.setItem(row, 10, _item(rsi, "{:.0f}" if rsi else None, color=rsi_color))

            dist = res.get("dist_sma200")
            dist_color = SUCCESS if dist and dist > 0 else DANGER if dist and dist < 0 else None
            self.table.setItem(row, 11, _item(dist, "{:+.1f}%" if dist else None, color=dist_color))

            score = res.get("score", 0)
            score_item = _item(score, "{:.0f}")
            score_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            if score >= 70: score_item.setForeground(QColor(SUCCESS))
            elif score >= 50: score_item.setForeground(QColor(ACCENT))
            elif score >= 35: score_item.setForeground(QColor(ACCENT2))
            else: score_item.setForeground(QColor(DANGER))
            self.table.setItem(row, 12, score_item)

        self.table.setSortingEnabled(True)
        self.result_count.setText(f"{len(results)} stocks passed filters")

    def _apply_preset(self, name):
        if name not in SCREENER_PRESETS or "---" in name: return
        self._reset_filters()
        preset = SCREENER_PRESETS[name]
        
        # Determine if we should disable broad categories
        has_fund = any(k in preset for k in ["pe_min", "pe_max", "pb_max", "roe_min", "rev_growth_min", "eps_growth_min", "div_yield_min"])
        has_tech = any(k in preset for k in ["rsi_min", "rsi_max", "price_vs_sma200", "price_vs_sma50", "macd_signal", "change_pct_min", "change_pct_max", "volume_min"])
        
        if name != "Custom":
            self.fund_enabled.setChecked(has_fund)
            self.tech_enabled.setChecked(has_tech)

        if "pe_min" in preset: self.pe_min.setValue(preset["pe_min"])
        if "pe_max" in preset: self.pe_max.setValue(preset["pe_max"])
        if "pb_max" in preset: self.pb_max.setValue(preset["pb_max"])
        if "peg_max" in preset: self.peg_max.setValue(preset["peg_max"])
        if "roe_min" in preset: self.roe_min.setValue(preset["roe_min"])
        if "rev_growth_min" in preset: self.rev_growth_min.setValue(preset["rev_growth_min"])
        if "eps_growth_min" in preset: self.eps_growth_min.setValue(preset["eps_growth_min"])
        if "div_yield_min" in preset: self.div_min.setValue(preset["div_yield_min"])
        
        if "rsi_min" in preset: self.rsi_min_sp.setValue(preset["rsi_min"])
        if "rsi_max" in preset: self.rsi_max_sp.setValue(preset["rsi_max"])
        if "change_pct_min" in preset: self.chg_min_sp.setValue(preset["change_pct_min"])
        if "change_pct_max" in preset: self.chg_max_sp.setValue(preset["change_pct_max"])
        if "price_vs_sma200" in preset: self.sma200_combo.setCurrentText(preset["price_vs_sma200"])
        if "price_vs_sma50" in preset: self.sma50_combo.setCurrentText(preset["price_vs_sma50"])
        if "macd_signal" in preset: self.macd_combo.setCurrentText(preset["macd_signal"])
        if "volume_min" in preset: self.vol_min_sp.setValue(preset["volume_min"])
        
        if "cap_segments" in preset:
            for seg, cb in self.cap_checks.items():
                cb.setChecked(seg in preset["cap_segments"])

    def _reset_filters(self):
        self.uni_combo.setCurrentIndex(0)
        for cb in self.cap_checks.values(): cb.setChecked(True)
        self.sector_combo.setCurrentIndex(0)
        self.pe_min.setValue(0); self.pe_max.setValue(200)
        self.pb_min.setValue(0); self.pb_max.setValue(50)
        self.peg_min.setValue(0); self.peg_max.setValue(10)
        self.roe_min.setValue(0); self.de_max.setValue(20)
        self.rev_growth_min.setValue(-50); self.eps_growth_min.setValue(-50)
        self.div_min.setValue(0)
        self.fund_enabled.setChecked(True)
        self.tech_enabled.setChecked(True)
        self.rsi_min_sp.setValue(0); self.rsi_max_sp.setValue(100)
        self.chg_min_sp.setValue(-30); self.chg_max_sp.setValue(30)
        self.sma200_combo.setCurrentIndex(0)
        self.sma50_combo.setCurrentIndex(0)
        self.macd_combo.setCurrentIndex(0)
        self.vol_min_sp.setValue(0)
        self.table.setRowCount(0)
        self.result_count.setText("Run a screen to see results")
        self.prog.setValue(0)
        self.prog.setFormat("")

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Results", "screener_results.csv", "CSV (*.csv)")
        if not path: return
        import csv
        headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in range(self.table.rowCount()):
                writer.writerow([
                    self.table.item(row, col).text() if self.table.item(row, col) else ""
                    for col in range(self.table.columnCount())
                ])

    def _context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0: return
        sym_item = self.table.item(row, 1)
        if not sym_item: return
        sym = sym_item.text()
        menu = QMenu(self)
        act_dive = menu.addAction(f"Deep Dive: {sym}")
        act_watch = menu.addAction(f"Add {sym} to Watchlist")
        action = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if action == act_dive:
            self.open_deep_dive.emit(sym, "Screener Results")
        elif action == act_watch:
            self.add_to_watchlist.emit(sym)

    def _on_double_click(self, index):
        row = index.row()
        sym_item = self.table.item(row, 1)
        if sym_item:
            self.open_deep_dive.emit(sym_item.text(), "Screener Results")

    def _connect_signals(self):
        """Connect all filter widgets to the logic bar update."""
        for w in [self.pe_min, self.pe_max, self.pb_min, self.pb_max, 
                  self.peg_min, self.peg_max, self.roe_min, self.de_max, 
                  self.rev_growth_min, self.eps_growth_min, self.div_min,
                  self.rsi_min_sp, self.rsi_max_sp, self.vol_min_sp, self.adx_min_sp]:
            w.valueChanged.connect(self._update_logic_bar)
        
        for w in [self.uni_combo, self.sector_combo, self.sma200_combo, 
                  self.sma50_combo, self.macd_combo]:
            w.currentTextChanged.connect(self._update_logic_bar)
            
        for cb in self.cap_checks.values():
            cb.toggled.connect(self._update_logic_bar)
            
        self.fund_enabled.toggled.connect(self._update_logic_bar)
        self.tech_enabled.toggled.connect(self._update_logic_bar)

    def _update_logic_bar(self):
        """Build a human-readable summary of the current filter criteria."""
        parts = []
        
        # Universe & Sectors
        uni = self.uni_combo.currentText()
        if uni != "Combined": parts.append(f"<b>{uni}</b>")
        
        sec = self.sector_combo.currentText()
        if sec != "All Sectors": parts.append(f"Sector: {sec}")
        
        # Cap Segments
        segs = [s for s, cb in self.cap_checks.items() if cb.isChecked()]
        if len(segs) < len(self.cap_checks):
            parts.append(f"Cap: {', '.join(segs)}")

        # Fundamentals
        if self.fund_enabled.isChecked():
            f_parts = []
            if self.pe_min.value() > 0 or self.pe_max.value() < 200:
                f_parts.append(f"P/E: {self.pe_min.value()}-{self.pe_max.value()}")
            if self.pb_max.value() < 50: f_parts.append(f"P/B < {self.pb_max.value()}")
            if self.peg_max.value() < 10: f_parts.append(f"PEG < {self.peg_max.value()}")
            if self.roe_min.value() > 0: f_parts.append(f"ROE > {self.roe_min.value()}%")
            if self.de_max.value() < 20: f_parts.append(f"D/E < {self.de_max.value()}")
            if self.rev_growth_min.value() > -50: f_parts.append(f"Rev Gr > {self.rev_growth_min.value()}%")
            if self.eps_growth_min.value() > -50: f_parts.append(f"EPS Gr > {self.eps_growth_min.value()}%")
            if self.div_min.value() > 0: f_parts.append(f"Div > {self.div_min.value()}%")
            if f_parts: parts.append(" | ".join(f_parts))

        # Technicals
        if self.tech_enabled.isChecked():
            t_parts = []
            if self.rsi_min_sp.value() > 0 or self.rsi_max_sp.value() < 100:
                t_parts.append(f"RSI: {int(self.rsi_min_sp.value())}-{int(self.rsi_max_sp.value())}")
            
            s200 = self.sma200_combo.currentText()
            if s200 != "Any": t_parts.append(f"Price vs SMA200: {s200}")
            
            s50 = self.sma50_combo.currentText()
            if s50 != "Any": t_parts.append(f"Price vs SMA50: {s50}")
            
            macd = self.macd_combo.currentText()
            if macd != "Any": t_parts.append(f"MACD: {macd}")
            
            if self.vol_min_sp.value() > 0:
                v = self.vol_min_sp.value()
                t_parts.append(f"Vol > {v/1e6:.1f}M" if v >= 1e6 else f"Vol > {v/1e3:.0f}K")
            
            if t_parts: parts.append(" | ".join(t_parts))

        if not parts:
            text = "Showing all stocks (no filters applied)"
        else:
            text = " • ".join(parts)
            
        self.logic_bar.setText(f"<b>Strategy:</b> {text}")

    def _toggle_sidebar(self):
        is_hidden = self.scroll.isHidden()
        self.scroll.setHidden(not is_hidden)
        self.btn_toggle_sidebar.setText("»" if not is_hidden else "«")

    def _save_to_watchlist(self):
        try:
            name = self.wl_name_input.text().strip()
            if not name: return

            cutoff = self.wl_cutoff_sp.value()
            symbols_to_add = []
            
            self.log.info(f"Saving watchlist '{name}' with cutoff {cutoff}")

            for row in range(self.table.rowCount()):
                score_item = self.table.item(row, 12)
                sym_item = self.table.item(row, 1)
                if score_item and sym_item:
                    try:
                        score_text = score_item.text().split('.')[0]
                        score = int(score_text) if score_text else 0
                        if score >= cutoff:
                            symbols_to_add.append(sym_item.text())
                    except:
                        continue
            
            if not symbols_to_add:
                self.log.warning("No symbols meet the score cutoff")
                self.result_count.setText("No symbols meet the score cutoff!")
                return
                
            if self.db:
                self.db.create_watchlist(name)
                for sym in symbols_to_add:
                    self.db.add_to_watchlist(name, sym)
                
                self.log.info(f"Successfully saved {len(symbols_to_add)} symbols to '{name}'")
                self.result_count.setText(f"Saved {len(symbols_to_add)} symbols to '{name}'")
                self.watchlist_saved.emit(name)
        except Exception as e:
            self.log.error(f"Error in _save_to_watchlist: {e}")
            traceback.print_exc()

    def update_tank(self, count, total):
        pct = (count / total * 100) if total > 0 else 0
        self.tank_bar.setValue(int(pct))
        self.tank_lbl.setText(f"Fundamentals Tank: {count}/{total} stocks ({pct:.1f}%)")
        
        if pct < 30: color = DANGER
        elif pct < 70: color = WARNING
        else: color = SUCCESS
        self.tank_bar.setStyleSheet(f"QProgressBar {{ border: 1px solid {BORDER}; border-radius: 6px; background: {BG_PRIMARY}; }} QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}")
