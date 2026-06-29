"""
Deep Dive Tab — Single stock analysis with chart, indicators, scoring.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout,
    QPushButton, QLineEdit, QComboBox, QGroupBox, QSizePolicy, QScrollArea
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from ..theme import (ACCENT, ACCENT2, SUCCESS, DANGER, WARNING,
                     BG_CARD, BG_SURFACE, BORDER, TEXT_SECONDARY, BG_PRIMARY)
from ..engines import ScoringEngine


PERIOD_MAP = {
    "1 Week": 7, "1 Month": 30, "3 Months": 90, 
    "6 Months": 180, "1 Year": 365, "2 Years": 730, "5 Years": 1825
}


def _metric_card(label, value="—", color=ACCENT):
    frame = QFrame()
    frame.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 8px;")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(8, 4, 8, 4)
    lay.setSpacing(6)
    l = QLabel(label)
    l.setFont(QFont("Segoe UI", 8))
    l.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
    v = QLabel(value)
    v.setObjectName("metricVal")
    v.setFont(QFont("Segoe UI", 11, QFont.Bold))
    v.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    lay.addWidget(l)
    lay.addStretch()
    lay.addWidget(v)
    return frame, v


class DeepDiveTab(QWidget):
    add_to_watchlist = Signal(str)
    add_to_specific_watchlist = Signal(str, str) # symbol, watchlist_name
    watchlist_changed = Signal(str)

    def __init__(self, parent=None):

        super().__init__(parent)
        self.master_data = {}
        self.setFocusPolicy(Qt.StrongFocus)
        self._build_ui()

    def set_master_data(self, data):
        self.master_data = data

    def clear_data(self):
        """Reset all UI fields to blank/placeholder states."""
        self.lbl_company.setText("—")
        self.lbl_detail.setText("—")
        self.lbl_price.setText("—")
        self.lbl_change.setText("—")
        self.lbl_score.setText("—")
        self.lbl_verdict.setText("—")
        
        # Reset all metric card labels
        for attr in dir(self):
            if attr.startswith("mv_") or attr.startswith("sv_"):
                widget = getattr(self, attr)
                if hasattr(widget, "setText"):
                    widget.setText("—")
        
        self.figure.clear()
        self.canvas.draw()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(14, 14, 14, 14)
        main.setSpacing(12)

        # --- Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        lbl = QLabel("DEEP DIVE")
        lbl.setFont(QFont("Segoe UI", 16, QFont.Bold))
        lbl.setStyleSheet(f"color: {ACCENT2};")
        toolbar.addWidget(lbl)

        self.sym_input = QLineEdit()
        self.sym_input.setPlaceholderText("Enter symbol (e.g. AAPL)")
        self.sym_input.setFixedWidth(280)
        self.sym_input.setFont(QFont("Segoe UI", 11))
        toolbar.addWidget(self.sym_input)

        # Watchlist Picker
        self.watchlist_combo = QComboBox()
        self.watchlist_combo.setFixedWidth(180)
        self.watchlist_combo.addItem("All Symbols")
        toolbar.addWidget(self.watchlist_combo)

        # Prev/Next Arrows
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedSize(32, 32)
        self.btn_prev.setToolTip("Previous Symbol")
        self.btn_prev.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 16px; font-weight: bold;")
        
        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedSize(32, 32)
        self.btn_next.setToolTip("Next Symbol")
        self.btn_next.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 16px; font-weight: bold;")
        
        self.btn_prev.clicked.connect(self._on_prev)
        self.btn_next.clicked.connect(self._on_next)
        self.watchlist_combo.currentTextChanged.connect(self.watchlist_changed.emit)

        toolbar.addWidget(self.btn_prev)
        toolbar.addWidget(self.btn_next)


        self._current_watchlist_symbols = []
        self._current_index = -1


        self.period_combo = QComboBox()
        self.period_combo.addItems(list(PERIOD_MAP.keys()))
        self.period_combo.setCurrentText("1 Year")
        self.period_combo.setFixedWidth(110)
        toolbar.addWidget(self.period_combo)

        self.btn_analyze = QPushButton("ANALYZE")
        self.btn_analyze.setFixedWidth(120)
        self.btn_analyze.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.sym_input.returnPressed.connect(self.btn_analyze.click)
        self.period_combo.currentIndexChanged.connect(self.btn_analyze.click)
        toolbar.addWidget(self.btn_analyze)

        self.btn_watchlist = QPushButton("+ Watchlist")
        self.btn_watchlist.setProperty("class", "secondary")
        self.btn_watchlist.setFixedWidth(110)
        
        from PySide6.QtWidgets import QMenu
        self.wl_menu = QMenu(self)
        self.btn_watchlist.setMenu(self.wl_menu)
        toolbar.addWidget(self.btn_watchlist)


        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        toolbar.addWidget(self.status_lbl)
        toolbar.addStretch()
        main.addLayout(toolbar)

        # --- Company Header ---
        self.header_frame = QFrame()
        self.header_frame.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px;")
        h_lay = QHBoxLayout(self.header_frame)
        h_lay.setContentsMargins(16, 10, 16, 10)

        self.lbl_company = QLabel("—")
        self.lbl_company.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.lbl_company.setStyleSheet(f"color: {ACCENT}; border: none;")

        self.lbl_detail = QLabel("")
        self.lbl_detail.setStyleSheet(f"color: {TEXT_SECONDARY}; border: none;")

        self.lbl_price = QLabel("—")
        self.lbl_price.setFont(QFont("Segoe UI", 18, QFont.Bold))
        self.lbl_price.setStyleSheet(f"border: none;")

        self.lbl_change = QLabel("")
        self.lbl_change.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.lbl_change.setStyleSheet(f"border: none;")

        h_lay.addWidget(self.lbl_company)
        h_lay.addWidget(self.lbl_detail)
        h_lay.addStretch()
        h_lay.addWidget(self.lbl_price)
        h_lay.addWidget(self.lbl_change)
        main.addWidget(self.header_frame)

        # --- Metric Cards Row ---
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(8)
        self.m_pe, self.mv_pe = _metric_card("P/E")
        self.m_pb, self.mv_pb = _metric_card("P/B")
        self.m_peg, self.mv_peg = _metric_card("PEG")
        self.m_roe, self.mv_roe = _metric_card("ROE")
        self.m_de, self.mv_de = _metric_card("D/E")
        self.m_div, self.mv_div = _metric_card("Div Yield")
        self.m_beta, self.mv_beta = _metric_card("Beta")
        self.m_mcap, self.mv_mcap = _metric_card("Mkt Cap")
        for w in [self.m_pe, self.m_pb, self.m_peg, self.m_roe,
                  self.m_de, self.m_div, self.m_beta, self.m_mcap]:
            metrics_row.addWidget(w)
        main.addLayout(metrics_row)

        # --- Chart + Sidebar ---
        body = QHBoxLayout()
        body.setSpacing(12)

        # Chart
        chart_frame = QFrame()
        chart_frame.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px;")
        chart_lay = QVBoxLayout(chart_frame)
        chart_lay.setContentsMargins(8, 8, 8, 8)

        # Chart controls
        ctrl_row = QHBoxLayout()
        self.chart_type = QComboBox()
        self.chart_type.addItems(["Line", "Candlestick"])
        self.chart_type.setFixedWidth(100)
        ctrl_row.addWidget(QLabel("Chart:"))
        ctrl_row.addWidget(self.chart_type)
        ctrl_row.addSpacing(10)
        from PySide6.QtWidgets import QCheckBox
        self.chk_sma20 = QCheckBox("SMA20"); self.chk_sma20.setChecked(True)
        self.chk_sma50 = QCheckBox("SMA50"); self.chk_sma50.setChecked(True)
        self.chk_sma200 = QCheckBox("SMA200"); self.chk_sma200.setChecked(True)
        self.chk_bb = QCheckBox("Bollinger"); self.chk_bb.setChecked(False)
        self.chk_vol = QCheckBox("Volume"); self.chk_vol.setChecked(True)
        self.chk_rsi = QCheckBox("RSI"); self.chk_rsi.setChecked(False)
        for cb in [self.chk_sma20, self.chk_sma50, self.chk_sma200, self.chk_bb, self.chk_vol, self.chk_rsi]:
            ctrl_row.addWidget(cb)
        ctrl_row.addStretch()
        chart_lay.addLayout(ctrl_row)

        self.figure = Figure(figsize=(8, 5), dpi=100)
        self.figure.patch.set_facecolor(BG_PRIMARY)
        self.canvas = FigureCanvas(self.figure)
        chart_lay.addWidget(self.canvas)
        body.addWidget(chart_frame, stretch=65)

        # Sidebar
        side_scroll = QScrollArea()
        side_widget = QWidget()
        side_lay = QVBoxLayout(side_widget)
        side_lay.setAlignment(Qt.AlignTop)
        side_lay.setSpacing(10)
        side_scroll.setWidget(side_widget)
        side_scroll.setWidgetResizable(True)
        side_scroll.setFixedWidth(280)
        side_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        # Technical Snapshot
        tech_grp = QGroupBox("Technical Snapshot")
        tg = QGridLayout(tech_grp)
        self.tv_rsi, self.tv_macd, self.tv_adx, self.tv_atr = QLabel("—"), QLabel("—"), QLabel("—"), QLabel("—")
        self.tv_dist50, self.tv_dist200 = QLabel("—"), QLabel("—")
        labels = ["RSI(14)", "MACD Hist", "ADX(14)", "ATR(14)", "Dist SMA50", "Dist SMA200"]
        vals = [self.tv_rsi, self.tv_macd, self.tv_adx, self.tv_atr, self.tv_dist50, self.tv_dist200]
        for i, (l, v) in enumerate(zip(labels, vals)):
            tg.addWidget(QLabel(l), i, 0)
            v.setAlignment(Qt.AlignRight)
            v.setFont(QFont("Segoe UI", 10, QFont.Bold))
            v.setStyleSheet(f"color: white;")
            tg.addWidget(v, i, 1)
        side_lay.addWidget(tech_grp)

        # Score + Verdict
        score_grp = QGroupBox("Composite Score")
        sg = QVBoxLayout(score_grp)
        self.lbl_score = QLabel("—")
        self.lbl_score.setFont(QFont("Segoe UI", 28, QFont.Bold))
        self.lbl_score.setAlignment(Qt.AlignCenter)
        self.lbl_verdict = QLabel("—")
        self.lbl_verdict.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.lbl_verdict.setAlignment(Qt.AlignCenter)

        # Sub-scores
        self.score_grid = QGridLayout()
        self.sv_val = QLabel("—"); self.sv_grow = QLabel("—")
        self.sv_qual = QLabel("—"); self.sv_mom = QLabel("—"); self.sv_risk = QLabel("—")
        sub_labels = ["Valuation", "Growth", "Quality", "Momentum", "Risk"]
        sub_vals = [self.sv_val, self.sv_grow, self.sv_qual, self.sv_mom, self.sv_risk]
        for i, (l, v) in enumerate(zip(sub_labels, sub_vals)):
            self.score_grid.addWidget(QLabel(l), i, 0)
            v.setAlignment(Qt.AlignRight); v.setFont(QFont("Segoe UI", 9, QFont.Bold))
            self.score_grid.addWidget(v, i, 1)

        sg.addWidget(self.lbl_score)
        sg.addWidget(self.lbl_verdict)
        sg.addLayout(self.score_grid)
        side_lay.addWidget(score_grp)
        side_lay.addStretch()

        body.addWidget(side_scroll, stretch=35)
        main.addLayout(body, stretch=1)

    def load_symbol(self, symbol, list_name="All Symbols"):
        """Entry point from other tabs."""
        self.watchlist_combo.setCurrentText(list_name)
        self.sym_input.setText(symbol)
        self.btn_analyze.click()

    def set_symbol(self, symbol):
        self.sym_input.setText(symbol)
        self.btn_analyze.click()

    def get_period_days(self):
        return PERIOD_MAP.get(self.period_combo.currentText(), 365)

    def update_data(self, data):
        """Populate all fields from DeepDiveWorker result dict."""
        if not data:
            self.clear_data()
            return
        
        # Track current symbol in watchlist list
        # Track current symbol in watchlist list (handle "SYM - Company" format)
        sym = data.get("symbol", "").upper()
        for i, item in enumerate(self._current_watchlist_symbols):
            if item.startswith(sym):
                self._current_index = i
                break

        profile = data.get("profile", {})
        fund = data.get("fundamentals", {})
        tech = data.get("technicals", {})
        score = data.get("score", {})
        bars = data.get("bars")

        # Header
        name = profile.get("companyName") or self.master_data.get(sym, sym)
        self.lbl_company.setText(f"{sym} — {name}")


        sector = profile.get("sector", "")
        industry = profile.get("industry", "")
        exchange = profile.get("exchangeShortName", "")
        self.lbl_detail.setText(f"{exchange}  ·  {sector}  ·  industry")

        price = fund.get("price") or profile.get("price")
        self.lbl_price.setText(f"${price:,.2f}" if price else "—")
        
        chg = fund.get("change_pct") or profile.get("changes")
        if chg is not None:
            color = SUCCESS if chg >= 0 else DANGER
            self.lbl_change.setText(f"{'▲' if chg >= 0 else '▼'} {chg:+.2f}%")
            self.lbl_change.setStyleSheet(f"color: {color}; border: none;")
        else:
            self.lbl_change.setText("—")
            self.lbl_change.setStyleSheet(f"color: {TEXT_SECONDARY}; border: none;")

        # Metric cards
        def _set(widget, val, fmt="{:.1f}", color=ACCENT):
            if val is not None:
                widget.setText(fmt.format(val))
                widget.setStyleSheet(f"color: {color}; background: transparent; border: none;")
            else:
                widget.setText("—")
                widget.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")

        _set(self.mv_pe, fund.get("pe_ratio"))
        _set(self.mv_pb, fund.get("pb_ratio"))
        _set(self.mv_peg, fund.get("peg_ratio"), "{:.2f}")
        roe = fund.get("roe")
        _set(self.mv_roe, roe, "{:.1f}%", SUCCESS if roe and roe > 15 else DANGER if roe and roe < 5 else ACCENT)
        _set(self.mv_de, fund.get("debt_equity"), "{:.2f}")
        _set(self.mv_div, fund.get("dividend_yield"), "{:.2f}%")
        _set(self.mv_beta, fund.get("beta"), "{:.2f}")

        mc = fund.get("market_cap") or profile.get("mktCap")
        if mc:
            if mc >= 1e12: self.mv_mcap.setText(f"${mc/1e12:.1f}T")
            elif mc >= 1e9: self.mv_mcap.setText(f"${mc/1e9:.1f}B")
            elif mc >= 1e6: self.mv_mcap.setText(f"${mc/1e6:.0f}M")
        else:
            self.mv_mcap.setText("—")

        # Technical sidebar
        def _set_tech(widget, val, fmt="{:.1f}", good_range=None):
            if val is None:
                widget.setText("—")
                widget.setStyleSheet(f"color: {TEXT_SECONDARY};")
                return
            widget.setText(fmt.format(val))
            if good_range:
                lo, hi = good_range
                c = DANGER if val > hi else SUCCESS if val < lo else ACCENT2
                widget.setStyleSheet(f"color: {c};")

        _set_tech(self.tv_rsi, tech.get("rsi_14"), "{:.1f}", (30, 70))
        _set_tech(self.tv_macd, tech.get("macd_hist"), "{:.3f}")
        macd_h = tech.get("macd_hist")
        if macd_h is not None:
            self.tv_macd.setStyleSheet(f"color: {SUCCESS if macd_h > 0 else DANGER};")
        
        _set_tech(self.tv_adx, tech.get("adx_14"), "{:.1f}")
        _set_tech(self.tv_atr, tech.get("atr_14"), "{:.2f}")
        _set_tech(self.tv_dist50, tech.get("dist_sma50_pct"), "{:+.2f}%")
        d50 = tech.get("dist_sma50_pct")
        if d50 is not None:
            self.tv_dist50.setStyleSheet(f"color: {SUCCESS if d50 > 0 else DANGER};")
        
        _set_tech(self.tv_dist200, tech.get("dist_sma200_pct"), "{:+.2f}%")
        d200 = tech.get("dist_sma200_pct")
        if d200 is not None:
            self.tv_dist200.setStyleSheet(f"color: {SUCCESS if d200 > 0 else DANGER};")

        # Score
        comp = score.get("composite", 0)
        self.lbl_score.setText(f"{comp:.0f}")
        sc_color = SUCCESS if comp >= 60 else ACCENT if comp >= 40 else DANGER
        self.lbl_score.setStyleSheet(f"color: {sc_color};")
        self.lbl_verdict.setText(score.get("verdict", "Neutral"))
        self.lbl_verdict.setStyleSheet(f"color: {sc_color};")

        # Sub-scores
        score_map = {
            "value": self.sv_val, "growth": self.sv_grow,
            "quality": self.sv_qual, "momentum": self.sv_mom, "risk": self.sv_risk
        }
        for key, lbl in score_map.items():
            v = score.get(key, 0)
            lbl.setText(f"{v:.0f}")
            c = SUCCESS if v >= 60 else ACCENT2 if v >= 40 else DANGER
            lbl.setStyleSheet(f"color: {c};")


        # Chart
        if bars is not None and not bars.empty:
            self._draw_chart(data.get("symbol", ""), bars, tech)

    def _draw_chart(self, symbol, df, tech):
        self.figure.clear()
        show_vol = self.chk_vol.isChecked()
        show_rsi = self.chk_rsi.isChecked()

        if show_vol and show_rsi:
            ax = self.figure.add_axes([0.08, 0.38, 0.88, 0.55])
            ax_vol = self.figure.add_axes([0.08, 0.23, 0.88, 0.13], sharex=ax)
            ax_rsi = self.figure.add_axes([0.08, 0.08, 0.88, 0.13], sharex=ax)
        elif show_vol:
            ax = self.figure.add_axes([0.08, 0.28, 0.88, 0.65])
            ax_vol = self.figure.add_axes([0.08, 0.08, 0.88, 0.18], sharex=ax)
            ax_rsi = None
        elif show_rsi:
            ax = self.figure.add_axes([0.08, 0.28, 0.88, 0.65])
            ax_rsi = self.figure.add_axes([0.08, 0.08, 0.88, 0.18], sharex=ax)
            ax_vol = None
        else:
            ax = self.figure.add_subplot(111)
            ax_vol = None
            ax_rsi = None

        for a in [ax] + ([ax_vol] if ax_vol else []) + ([ax_rsi] if ax_rsi else []):
            a.set_facecolor(BG_PRIMARY)
            a.tick_params(colors='#8B949E', labelsize=8)
            for spine in a.spines.values():
                spine.set_color(BORDER)
            a.grid(True, alpha=0.08, color='white')

        full_close = df["close"].astype(float)
        sma20 = full_close.rolling(window=20).mean() if len(full_close) >= 20 else None
        sma50 = full_close.rolling(window=50).mean() if len(full_close) >= 50 else None
        sma200 = full_close.rolling(window=200).mean() if len(full_close) >= 200 else None

        rsi = None
        if ax_rsi is not None:
            full_delta = full_close.diff()
            full_gain = full_delta.where(full_delta > 0, 0.0)
            full_loss = (-full_delta).where(full_delta < 0, 0.0)
            full_avg_gain = full_gain.ewm(com=13, adjust=False).mean()
            full_avg_loss = full_loss.ewm(com=13, adjust=False).mean()
            full_rs = full_avg_gain / full_avg_loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + full_rs))

        std20 = None
        if self.chk_bb.isChecked() and len(full_close) >= 20:
            std20 = full_close.rolling(window=20).std()

        period = self.get_period_days()
        if len(df) > period:
            # We approximate trading days vs calendar days by taking period (or slightly fewer).
            # To be precise, 1 Month (30 days) is ~21 trading days, but tail(period) is fine.
            df = df.tail(period).copy()
            if sma20 is not None: sma20 = sma20.tail(period)
            if sma50 is not None: sma50 = sma50.tail(period)
            if sma200 is not None: sma200 = sma200.tail(period)
            if rsi is not None: rsi = rsi.tail(period)
            if std20 is not None: std20 = std20.tail(period)

        dates = df["date"]
        close = df["close"].astype(float)

        if self.chart_type.currentText() == "Candlestick":
            import pandas as pd
            up = df[df["close"] >= df["open"]]
            dn = df[df["close"] < df["open"]]
            ax.bar(up["date"], up["close"]-up["open"], bottom=up["open"], color=SUCCESS, width=0.8, alpha=0.9)
            ax.bar(up["date"], up["high"]-up["close"], bottom=up["close"], color=SUCCESS, width=0.15)
            ax.bar(up["date"], up["low"]-up["open"], bottom=up["open"], color=SUCCESS, width=0.15)
            ax.bar(dn["date"], dn["close"]-dn["open"], bottom=dn["open"], color=DANGER, width=0.8, alpha=0.9)
            ax.bar(dn["date"], dn["high"]-dn["open"], bottom=dn["open"], color=DANGER, width=0.15)
            ax.bar(dn["date"], dn["low"]-dn["close"], bottom=dn["close"], color=DANGER, width=0.15)
        else:
            ax.plot(dates, close, color=ACCENT, linewidth=1.5, label="Close")

        # Overlays
        if self.chk_sma20.isChecked() and sma20 is not None:
            ax.plot(dates, sma20, color="#F0883E", linewidth=1, alpha=0.8, label="SMA20")
        if self.chk_sma50.isChecked() and sma50 is not None:
            ax.plot(dates, sma50, color="#A371F7", linewidth=1, alpha=0.8, label="SMA50")
        if self.chk_sma200.isChecked() and sma200 is not None:
            ax.plot(dates, sma200, color="#D29922", linewidth=1.2, alpha=0.8, label="SMA200")
        if self.chk_bb.isChecked() and sma20 is not None and std20 is not None:
            bb_lower = sma20 - 2 * std20
            bb_upper = sma20 + 2 * std20
            ax.fill_between(dates, bb_lower, bb_upper, alpha=0.06, color=ACCENT)
            ax.plot(dates, bb_lower, color=ACCENT, linewidth=0.5, alpha=0.4)
            ax.plot(dates, bb_upper, color=ACCENT, linewidth=0.5, alpha=0.4)

        ax.set_title(f"{symbol} — Price Chart", color='white', fontsize=12, pad=10)
        ax.legend(facecolor=BG_CARD, edgecolor=BORDER, labelcolor='white', fontsize=8, loc='upper left')

        # Volume
        if ax_vol is not None:
            vol = df["volume"].astype(float)
            colors = [SUCCESS if df["close"].iloc[i] >= df["open"].iloc[i] else DANGER for i in range(len(df))]
            ax_vol.bar(dates, vol, color=colors, alpha=0.6, width=0.8)
            ax_vol.set_ylabel("Vol", color='#8B949E', fontsize=8)
            if not ax_rsi:
                ax_vol.tick_params(labelbottom=True)
            else:
                ax_vol.tick_params(labelbottom=False)
            ax.tick_params(labelbottom=False)

        # RSI
        if ax_rsi is not None and rsi is not None:
            ax_rsi.plot(dates, rsi, color=ACCENT2, linewidth=1)
            ax_rsi.axhline(70, color=DANGER, linestyle='--', alpha=0.3, linewidth=0.8)
            ax_rsi.axhline(50, color=TEXT_SECONDARY, linestyle='--', alpha=0.2, linewidth=0.8)
            ax_rsi.axhline(30, color=SUCCESS, linestyle='--', alpha=0.3, linewidth=0.8)
            ax_rsi.set_ylim(0, 100)
            ax_rsi.set_yticks([30, 50, 70])
            ax_rsi.set_ylabel("RSI", color='#8B949E', fontsize=8)
            ax.tick_params(labelbottom=False)
            if ax_vol:
                ax_vol.tick_params(labelbottom=False)

        self.figure.subplots_adjust(left=0.08, right=0.96, top=0.93, bottom=0.08)
        self.canvas.draw()

    def _on_prev(self):
        if not self._current_watchlist_symbols: return
        if self._current_index == -1: self._current_index = 0
        else: self._current_index = (self._current_index - 1) % len(self._current_watchlist_symbols)
        self._load_current()

    def _on_next(self):
        if not self._current_watchlist_symbols: return
        if self._current_index == -1: self._current_index = 0
        else: self._current_index = (self._current_index + 1) % len(self._current_watchlist_symbols)
        self._load_current()

    def _load_current(self):
        if 0 <= self._current_index < len(self._current_watchlist_symbols):
            sym = self._current_watchlist_symbols[self._current_index]
            self.sym_input.setText(sym)
            self.btn_analyze.click()

    def set_watchlist_symbols(self, symbols_with_names):
        self._current_watchlist_symbols = symbols_with_names
        self._current_index = 0 if symbols_with_names else -1

    def update_watchlists(self, names):

        current = self.watchlist_combo.currentText()
        self.watchlist_combo.blockSignals(True)
        self.watchlist_combo.clear()
        self.watchlist_combo.addItem("All Symbols")
        self.watchlist_combo.addItems(names)
        if current in names:
            self.watchlist_combo.setCurrentText(current)
        self.watchlist_combo.blockSignals(False)

    def update_watchlist_menu(self, user_lists):
        self.wl_menu.clear()
        if not user_lists:
            self.wl_menu.addAction("No User Watchlists")
            return
            
        for name in user_lists:
            action = self.wl_menu.addAction(name)
            action.triggered.connect(lambda checked=False, n=name: 
                self.add_to_specific_watchlist.emit(self.sym_input.text().split(" - ")[0].strip().upper(), n))
    def keyPressEvent(self, event):
        """Handle arrow keys for symbol and period navigation."""
        if self.sym_input.hasFocus():
            # If typing in symbol box, only handle Up/Down for period
            if event.key() == Qt.Key_Up:
                self._change_period(-1)
                event.accept()
                return
            elif event.key() == Qt.Key_Down:
                self._change_period(1)
                event.accept()
                return
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key_Left:
            self._on_prev()
        elif event.key() == Qt.Key_Right:
            self._on_next()
        elif event.key() == Qt.Key_Up:
            self._change_period(-1)
        elif event.key() == Qt.Key_Down:
            self._change_period(1)
        else:
            super().keyPressEvent(event)

    def _change_period(self, delta):
        idx = self.period_combo.currentIndex()
        new_idx = max(0, min(self.period_combo.count() - 1, idx + delta))
        if new_idx != idx:
            self.period_combo.setCurrentIndex(new_idx)
            self.btn_analyze.click()


