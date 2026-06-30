import os
import pandas as pd
from PySide6.QtCore import QThread, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QLabel, QComboBox, QLineEdit,
    QFileDialog, QFrame, QGridLayout, QProgressBar, QMenu, QInputDialog,
    QMessageBox
)
from modules.stock_evaluate.database import DatabaseManager


# ── Filter mapping (8x5 grid) ────────────────────────────────────
FILTER_MAPPING = {
    "Exchange": {
        "col": 0, "category": "Descriptive",
        "options": {"Any": (None, None), "AMEX": ("AMEX", "sh_exch_amex"), "NASDAQ": ("NASDAQ", "sh_exch_nasd"), "NYSE": ("NYSE", "sh_exch_nyse")}
    },
    "Market Cap.": {
        "col": 0, "category": "Descriptive",
        "options": {
            "Any": (None, None),
            "Mega (200bln and more)": ("Mega (200bln and more)", "cap_mega"),
            "Large (10bln to 200bln)": ("Large (10bln to 200bln)", "cap_large"),
            "Mid (2bln to 10bln)": ("Mid (2bln to 10bln)", "cap_mid"),
            "Small (300mln to 2bln)": ("Small (300mln to 2bln)", "cap_small"),
            "+Large (10bln and more)": ("+Large (10bln and more)", "cap_largeover"),
            "+Mid (2bln and more)": ("+Mid (2bln and more)", "cap_midover"),
            "+Small (300mln and more)": ("+Small (300mln and more)", "cap_smallover")
        }
    },
    "Price": {
        "col": 0, "category": "Descriptive",
        "options": {"Any": (None, None), "Under $5": ("Under $5", "sh_price_u5"), "Under $10": ("Under $10", "sh_price_u10"), "Over $10": ("Over $10", "sh_price_o10"), "Over $50": ("Over $50", "sh_price_o50")}
    },
    "P/B": {
        "col": 0, "category": "Fundamental",
        "options": {"Any": (None, None), "Under 1": ("Under 1", "fa_pb_u1"), "Under 2": ("Under 2", "fa_pb_u2"), "Over 1": ("Over 1", "fa_pb_o1"), "Over 2": ("Over 2", "fa_pb_o2")}
    },
    "Dividend Yield": {
        "col": 0, "category": "Fundamental",
        "options": {"Any": (None, None), "Over 1%": ("Over 1%", "fa_div_o1"), "Over 3%": ("Over 3%", "fa_div_o3"), "Over 5%": ("Over 5%", "fa_div_o5")}
    },
    "Debt/Equity": {
        "col": 0, "category": "Fundamental",
        "options": {"Any": (None, None), "Under 0.5": ("Under 0.5", "fa_debteq_u0.5"), "Under 1": ("Under 1", "fa_debteq_u1"), "Over 1": ("Over 1", "fa_debteq_o1")}
    },
    "Insider Ownership": {
        "col": 0, "category": "Fundamental",
        "options": {"Any": (None, None), "Over 10%": ("Over 10%", "sh_instown_o10"), "Over 50%": ("Over 50%", "sh_instown_o50")}
    },
    "Return on Equity": {
        "col": 0, "category": "Fundamental",
        "options": {
            "Any": (None, None),
            ">0%": ("Positive (>0%)", "fa_roe_pos"),
            "Over+5%": ("Over +5%", "fa_roe_o5"),
            "Over+15%": ("Over +15%", "fa_roe_o15"),
            "Over +25%": ("Over +25%", "fa_roe_o25"),
            "<0%": ("Negative (<0%)", "fa_roe_neg"),
            "under-5%": ("Under -5%", "fa_roe_u-5"),
            "under-15%": ("Under -15%", "fa_roe_u-15")
        }
    },

    "Index": {
        "col": 1, "category": "Descriptive",
        "options": {"Any": (None, None), "S&P 500": ("S&P 500", "idx_sp500"), "NASDAQ 100": ("NASDAQ 100", "idx_ndx"), "DJIA": ("DJIA", "idx_dji"), "Russell 2000": ("RUSSELL 2000", "idx_rut")}
    },
    "P/E": {
        "col": 1, "category": "Fundamental",
        "options": {"Any": (None, None), "Under 10": ("Under 10", "fa_pe_u10"), "Under 15": ("Under 15", "fa_pe_u15"), "Under 20": ("Under 20", "fa_pe_u20"), "Over 20": ("Over 20", "fa_pe_o20")}
    },
    "Forward P/E": {
        "col": 1, "category": "Fundamental",
        "options": {"Any": (None, None), "Under 10": ("Under 10", "fa_fpe_u10"), "Under 20": ("Under 20", "fa_fpe_u20"), "Over 15": ("Over 15", "fa_fpe_o15")}
    },
    "PEG": {
        "col": 1, "category": "Fundamental",
        "options": {"Any": (None, None), "Under 1": ("Under 1", "fa_peg_u1"), "Under 2": ("Under 2", "fa_peg_u2"), "Over 1": ("Over 1", "fa_peg_o1")}
    },
    "P/S": {
        "col": 1, "category": "Fundamental",
        "options": {"Any": (None, None), "Under 1": ("Under 1", "fa_ps_u1"), "Under 2": ("Under 2", "fa_ps_u2"), "Over 1": ("Over 1", "fa_ps_o1")}
    },
    "Gross Margin": {
        "col": 1, "category": "Fundamental",
        "options": {"Any": (None, None), "Positive (>0%)": ("Positive (>0%)", "fa_grossmargin_pos"), "Over 30%": ("Over 30%", "fa_grossmargin_o30"), "Over 50%": ("Over 50%", "fa_grossmargin_o50")}
    },
    "Net Profit Margin": {
        "col": 1, "category": "Fundamental",
        "options": {"Any": (None, None), "Negative (<0%)": ("Negative (<0%)", "fa_netmargin_neg"), "Very Negative (<-20%)": ("Very Negative (<-20%)", "fa_netmargin_vn"), "High (>20%)": ("High (>20%)", "fa_netmargin_o20")}
    },
    "Return on Invested Capital": {
        "col": 1, "category": "Fundamental",
        "options": {
            "Any": (None, None), ">0%": ("Positive (>0%)", "fa_roic_pos"),
            "Over+5%": ("Over +5%", "fa_roic_o5"), "Over+15%": ("Over +15%", "fa_roic_o15"),
            "Over +25%": ("Over +25%", "fa_roic_o25"), "<0%": ("Negative (<0%)", "fa_roic_neg"),
            "under-5%": ("Under -5%", "fa_roe_u-5"), "under-15%": ("Under -15%", "fa_roe_u-15")
        }
    },

    "Sector": {
        "col": 2, "category": "Descriptive",
        "options": {
            "Any": (None, None), "Basic Materials": ("Basic Materials", "sec_basicmaterials"),
            "Communication Services": ("Communication Services", "sec_communicationservices"),
            "Consumer Cyclical": ("Consumer Cyclical", "sec_consumercyclical"),
            "Consumer Defensive": ("Consumer Defensive", "sec_consumerdefensive"),
            "Energy": ("Energy", "sec_energy"), "Financial": ("Financial", "sec_financial"),
            "Healthcare": ("Healthcare", "sec_healthcare"), "Industrials": ("Industrials", "sec_industrials"),
            "Technology": ("Technology", "sec_technology"), "Utilities": ("Utilities", "sec_utilities"),
        }
    },
    "Industry": {
        "col": 2, "category": "Descriptive",
        "options": {"Any": (None, None), "Stocks only (ex-ETFs)": ("Stocks only (ex-ETFs)", "ind_stocksonly")}
    },
    "Analyst Recom.": {
        "col": 2, "category": "Descriptive",
        "options": {
            "Any": (None, None), "Strong Buy": ("Strong Buy (1)", "fa_recom_strongbuy"),
            "Buy": ("Buy or better", "fa_recom_buy"), "Hold": ("Hold or better", "fa_recom_hold"),
            "Sell": ("Sell or worse", "fa_recom_sell"), "Strong Sell": ("Strong Sell (5)", "fa_recom_strongsell")
        }
    },
    "Insider Transactions": {
        "col": 2, "category": "Fundamental",
        "options": {"Any": (None, None), "Up": ("Up", "sh_insttrans_up"), "Down": ("Down", "sh_insttrans_down")}
    },
    "Institutional Ownership": {
        "col": 2, "category": "Fundamental",
        "options": {"Any": (None, None), "Over 10%": ("Over 10%", "sh_instown_o10"), "Over 50%": ("Over 50%", "sh_instown_o50"), "Over 90%": ("Over 90%", "sh_instown_o90")}
    },
    "EPS growth next 5 years": {
        "col": 2, "category": "Fundamental",
        "options": {"Any": (None, None), "Over 10%": ("Over 10%", "fa_estltgrowth_o10"), "Over 20%": ("Over 20%", "fa_estltgrowth_o20")}
    },
    "EPS Growth This Year": {
        "col": 2, "category": "Fundamental",
        "options": {
            "Any": (None, None), "Negative": ("Negative (<0%)", "fa_epsyoy_neg"),
            "Over 10%": ("Over +10%", "fa_epsyoy_o10"), "Over 20%": ("Over +20%", "fa_epsyoy_o20"),
            "Over 30%": ("Over +30%", "fa_epsyoy_o30")
        }
    },
    "Payout Ratio": {
        "col": 2, "category": "Fundamental",
        "options": {"Any": (None, None), "Under 50%": ("Under 50%", "fa_payout_u50"), "Over 50%": ("Over 50%", "fa_payout_o50")}
    },

    "Target Price": {
        "col": 3, "category": "Fundamental",
        "options": {
            "Any": (None, None), "20% Above Price": ("20% Above Price", "fa_targetprice_a20"),
            "40% Above Price": ("40% Above Price", "fa_targetprice_a40"),
            "20% Below Price": ("20% Below Price", "fa_targetprice_b20"),
            "40% Below Price": ("40% Below Price", "fa_targetprice_b40")
        }
    },
    "EV/EBITDA": {
        "col": 3, "category": "Fundamental",
        "options": {"Any": (None, None), "Negative(<0)": ("Negative (<0)", "fa_evebitda_neg"), "Low(<15)": ("Low (<15)", "fa_evebitda_low"), "High(>50)": ("High (>50)", "fa_evebitda_high")}
    },
    "RSI (14)": {
        "col": 3, "category": "Technical",
        "options": {"Any": (None, None), "Overbought (70)": ("Overbought (70)", "ta_rsi_ob70"), "Oversold (30)": ("Oversold (30)", "ta_rsi_os30")}
    },
    "Beta": {
        "col": 3, "category": "Technical",
        "options": {"Any": (None, None), "Under 1": ("Under 1", "ta_beta_u1"), "Over 1": ("Over 1", "ta_beta_o1"), "Over 2": ("Over 2", "ta_beta_o2")}
    },
    "Volatility": {
        "col": 3, "category": "Technical",
        "options": {"Any": (None, None), "Week - Over 3%": ("Week - Over 3%", "ta_volatility_wo3"), "Month - Over 5%": ("Month - Over 5%", "ta_volatility_mo5")}
    },
    "20-Day Simple Moving Average": {
        "col": 3, "category": "Technical",
        "options": {"Any": (None, None), "Price above SMA20": ("Price above SMA20", "ta_sma20_pa"), "Price below SMA20": ("Price below SMA20", "ta_sma20_pb")}
    },
    "50-Day Simple Moving Average": {
        "col": 3, "category": "Technical",
        "options": {"Any": (None, None), "Price above SMA50": ("Price above SMA50", "ta_sma50_pa"), "Price below SMA50": ("Price below SMA50", "ta_sma50_pb")}
    },
    "200-Day Simple Moving Average": {
        "col": 3, "category": "Technical",
        "options": {"Any": (None, None), "Price above SMA200": ("Price above SMA200", "ta_sma200_pa"), "Price below SMA200": ("Price below SMA200", "ta_sma200_pb")}
    },

    "52-Week High/Low": {
        "col": 4, "category": "Technical",
        "options": {"Any": (None, None), "New High": ("New High", "ta_highlow52w_nh"), "New Low": ("New Low", "ta_highlow52w_nl")}
    },
    "All-Time High/Low": {
        "col": 4, "category": "Technical",
        "options": {
            "Any": (None, None), "New High": ("New High", "ta_highlowall_nh"),
            "0-5% Below High": ("0-5% Below High", "ta_highlowall_db5h"),
            "0-5% above Low": ("0-5% Above Low", "ta_highlowall_da5l"),
            "New Low": ("New Low", "ta_highlowall_nl")
        }
    },
    "Pattern": {
        "col": 4, "category": "Technical",
        "options": {"Any": (None, None), "Double Bottom": ("Double Bottom", "ta_pattern_doublebottom"), "Double Top": ("Double Top", "ta_pattern_doubletop"), "Head & Shoulders": ("Head & Shoulders", "ta_pattern_headandshoulders")}
    },
    "Relative Volume": {
        "col": 4, "category": "Technical",
        "options": {"Any": (None, None), "Over 1": ("Over 1", "sh_relvol_o1"), "Over 2": ("Over 2", "sh_relvol_o2"), "Over 5": ("Over 5", "sh_relvol_o5")}
    },
    "Performance": {
        "col": 4, "category": "Technical",
        "options": {
            "Any": (None, None), "Up Week": ("Week Up", "ta_perf_uweek"),
            "Up 10% or more": ("Today +10%", "ta_perf_d10"), "Down Week": ("Week Down", "ta_perf_dweek")
        }
    },
    "Change": {
        "col": 4, "category": "Technical",
        "options": {
            "Any": (None, None), "Up5%": ("Up 5%", "ta_change_u5"), "Up10%": ("Up 10%", "ta_change_u10"),
            "Up15%": ("Up 15%", "ta_change_u15"), "Down 5%": ("Down 5%", "ta_change_d5"),
            "Down10%": ("Down 10%", "ta_change_d10"), "Down 15%": ("Down 15%", "ta_change_d15")
        }
    },
    "Gap": {
        "col": 4, "category": "Technical",
        "options": {
            "Any": (None, None), "Up5%": ("Up 5%", "ta_gap_u5"), "Up10%": ("Up 10%", "ta_gap_u10"),
            "Up15%": ("Up 15%", "ta_gap_u15"), "Down5%": ("Down 5%", "ta_gap_d5"),
            "Down10%": ("Down 10%", "ta_gap_d10")
        }
    },
    "Short Float": {
        "col": 4, "category": "Technical",
        "options": {"Any": (None, None), "Over 5%": ("Over 5%", "sh_short_o5"), "Over 15%": ("Over 15%", "sh_short_o15"), "Over 20%": ("Over 20%", "sh_short_o20")}
    }
}

PRESET_STRATEGIES = {
    "S&P 500 / Cheap P/E": {"Index": "S&P 500", "P/E": "Under 20"},
    "High Dividend / Mega Cap": {"Market Cap.": "Mega (200bln and more)", "Dividend Yield": "Over 5%"},
    "Undervalued Growth": {"P/E": "Under 15", "PEG": "Under 1", "EPS growth next 5 years": "Over 10%"},
    "High Momentum Tech": {"Sector": "Technology", "Performance": "Up 10% or more", "20-Day Simple Moving Average": "Price above SMA20"},
    "Oversold RSI Reversal": {"RSI (14)": "Oversold (30)", "Price": "Over $10"},
    "Institutional Accumulation": {"Institutional Ownership": "Over 50%", "Insider Transactions": "Up"},
    "Golden Cross Reversal": {"20-Day Simple Moving Average": "Price above SMA20", "50-Day Simple Moving Average": "Price above SMA50", "200-Day Simple Moving Average": "Price above SMA200"},
    "Short Squeeze Candidate": {"Short Float": "Over 20%", "Relative Volume": "Over 2", "Performance": "Up Week"},
    "High Volume Breakout": {"Relative Volume": "Over 2", "Performance": "Up 10% or more", "Price": "Over $10"},
    "High Growth / Low Debt (Bullish)": {"EPS growth next 5 years": "Over 20%", "Debt/Equity": "Under 0.5", "Return on Equity": ">0%"},
    "Mega Cap Value / Dividend": {"Market Cap.": "Mega (200bln and more)", "P/E": "Under 15", "Dividend Yield": "Over 3%"},
    "Oversold Bounce Play": {"RSI (14)": "Oversold (30)", "Performance": "Up Week", "Price": "Over $10"},
    "Strong Margin Tech Leaders": {"Sector": "Technology", "Gross Margin": "Over 50%", "Return on Invested Capital": "Over+15%"},
    "Insider buying with cheap P/S": {"Insider Transactions": "Up", "P/S": "Under 1", "Price": "Over $5"},
}


# ── Background Worker ─────────────────────────────────────────────
class FinvizWorker(QThread):
    success = Signal(pd.DataFrame, str)
    failure = Signal(str)

    def __init__(self, filters):
        super().__init__()
        self.filters = filters

    def run(self):
        ff_filters = {}
        for f_name, opt_val in self.filters.items():
            if opt_val and opt_val != "Any":
                ff_val = FILTER_MAPPING[f_name]["options"][opt_val][0]
                if ff_val is not None:
                    ff_filters[f_name] = ff_val

        orig_filters = []
        for f_name, opt_val in self.filters.items():
            if opt_val and opt_val != "Any":
                orig_val = FILTER_MAPPING[f_name]["options"][opt_val][1]
                if orig_val is not None:
                    orig_filters.append(orig_val)

        try:
            from finvizfinance.screener.overview import Overview
            foverview = Overview()
            if ff_filters:
                foverview.set_filter(filters_dict=ff_filters)
            df = foverview.screener_view()
            if df is not None and not df.empty:
                self.success.emit(df, "finvizfinance")
                return
        except Exception as e:
            print(f"finvizfinance failed: {str(e)}")

        try:
            from finviz.screener import Screener as OrigScreener
            stock_list = OrigScreener(filters=orig_filters)
            df = pd.DataFrame(list(stock_list))
            if df is not None and not df.empty:
                self.success.emit(df, "finviz (original)")
                return
        except Exception as e:
            print(f"finviz original failed: {str(e)}")

        self.failure.emit("Engine Error: Screen returned empty results or fallback modules failed.")


# ── Numeric table item ────────────────────────────────────────────
class NumericTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        if not isinstance(other, QTableWidgetItem):
            return super().__lt__(other)

        t_val = self.text().replace('%', '').replace('$', '').replace(',', '').strip()
        o_val = other.text().replace('%', '').replace('$', '').replace(',', '').strip()

        def parse_suffix(val_str):
            if not val_str or val_str == "-":
                return -999999999999.0
            multiplier = 1.0
            if val_str.endswith('B'):
                multiplier = 1e9; val_str = val_str[:-1]
            elif val_str.endswith('M'):
                multiplier = 1e6; val_str = val_str[:-1]
            elif val_str.endswith('K'):
                multiplier = 1e3; val_str = val_str[:-1]
            try:
                return float(val_str) * multiplier
            except ValueError:
                return val_str

        v1 = parse_suffix(t_val)
        v2 = parse_suffix(o_val)
        if isinstance(v1, float) and isinstance(v2, float):
            return v1 < v2
        return str(v1) < str(v2)


# ── Ported Finviz Screener Widget ─────────────────────────────────
class FinvizScreenerWidget(QWidget):
    watchlist_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("finvizScreener")

        self.raw_df = pd.DataFrame()
        self.worker = None
        self.builder_combos = {}
        self.builder_labels = {}
        self.current_active_tab = "All"
        self.db = DatabaseManager()

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── toolbar ────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setObjectName("fsToolbar")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(6, 4, 6, 4)
        tb.setSpacing(8)

        self.toggle_builder_btn = QPushButton("\u00AB Collapsible Builder")
        self.toggle_builder_btn.setObjectName("neonToggleBtn")
        self.toggle_builder_btn.clicked.connect(self.toggle_builder_pane)
        tb.addWidget(self.toggle_builder_btn)

        tb.addWidget(QLabel("Preset:"))
        self.preset_dropdown = QComboBox()
        self.preset_dropdown.addItem("Custom Builder (None)")
        self.preset_dropdown.addItems(list(PRESET_STRATEGIES.keys()))
        self.preset_dropdown.currentTextChanged.connect(self.load_preset_to_builder)
        self.preset_dropdown.setMaximumWidth(200)
        tb.addWidget(self.preset_dropdown)

        tb.addWidget(QLabel("Universe:"))
        self.universe_dropdown = QComboBox()
        self.universe_dropdown.addItems([
            "All Market Listed Stocks", "S&P 500 Giants Only",
            "Nasdaq 100 Innovators", "Dow Jones Bluechips",
            "Russell 2000 Small Caps", "Mega Caps ($200B+)",
            "Large Cap Powerhouses", "Mid Cap / Steady Growth",
            "High Growth Small Cap"
        ])
        self.universe_dropdown.currentTextChanged.connect(self.on_universe_changed)
        self.universe_dropdown.setMaximumWidth(150)
        tb.addWidget(self.universe_dropdown)

        tb.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximumWidth(100)
        self.progress_bar.hide()
        tb.addWidget(self.progress_bar)

        self.score_label = QLabel("")
        tb.addWidget(self.score_label)

        self.run_btn = QPushButton("Execute Screen")
        self.run_btn.setObjectName("runBtn")
        self.run_btn.clicked.connect(self.start_screener_task)
        tb.addWidget(self.run_btn)

        self.reset_btn = QPushButton("Reset All")
        self.reset_btn.setObjectName("resetBtn")
        self.reset_btn.clicked.connect(self.reset_screener_filters)
        tb.addWidget(self.reset_btn)

        layout.addWidget(toolbar)

        # ── builder card ────────────────────────────────────────────
        self.builder_card = QFrame()
        self.builder_card.setObjectName("fsBuilderCard")
        bcard = QVBoxLayout(self.builder_card)
        bcard.setContentsMargins(8, 6, 8, 6)
        bcard.setSpacing(4)

        tab_row = QHBoxLayout()
        tab_row.setSpacing(3)
        self.tab_buttons = {}
        for tab_name in ["Descriptive", "Fundamental", "Technical", "All"]:
            btn = QPushButton(tab_name)
            btn.setObjectName("tabBtn")
            btn.setCheckable(True)
            if tab_name == "All":
                btn.setChecked(True)
            btn.clicked.connect(self.change_builder_tab_view)
            self.tab_buttons[tab_name] = btn
            tab_row.addWidget(btn)
        tab_row.addStretch()
        bcard.addLayout(tab_row)

        grid_w = QWidget()
        grid_w.setObjectName("fsGrid")
        self.grid_layout = QGridLayout(grid_w)
        self.grid_layout.setSpacing(6)
        self.grid_layout.setContentsMargins(0, 4, 0, 4)

        col_indices = [0, 0, 0, 0, 0]
        for f_name, f_data in FILTER_MAPPING.items():
            target_col = f_data["col"]
            current_row = col_indices[target_col]

            container = QFrame()
            container.setObjectName("fsFilterContainer")
            h = QHBoxLayout(container)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)

            lbl = QLabel(f_name)
            lbl.setObjectName("fsFilterLabel")
            lbl.setMinimumWidth(80)
            lbl.setMaximumWidth(120)

            combo = QComboBox()
            combo.addItems(list(f_data["options"].keys()))
            combo.currentTextChanged.connect(self.on_filter_changed)
            combo.setMinimumWidth(85)
            combo.setMaximumWidth(150)

            h.addWidget(lbl)
            h.addWidget(combo)

            self.builder_combos[f_name] = combo
            self.builder_labels[f_name] = lbl
            self.grid_layout.addWidget(container, current_row, target_col)
            col_indices[target_col] += 1

        bcard.addWidget(grid_w)
        layout.addWidget(self.builder_card)

        # ── table ───────────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setObjectName("fsTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        layout.addWidget(self.table, 1)

        # ── status bar ──────────────────────────────────────────────
        self.status_bar = QFrame()
        self.status_bar.setObjectName("fsStatusBar")
        sb = QHBoxLayout(self.status_bar)
        sb.setContentsMargins(8, 2, 8, 2)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Filter ticker/sector...")
        self.search_bar.textChanged.connect(self.apply_client_filter)
        self.search_bar.setMaximumWidth(220)
        sb.addWidget(self.search_bar)

        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setObjectName("exportBtn")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_to_csv)
        sb.addWidget(self.export_btn)

        # Watchlist saving controls
        self.wl_name_input = QLineEdit()
        self.wl_name_input.setPlaceholderText("New Watchlist Name")
        self.wl_name_input.setMaximumWidth(180)
        self.wl_name_input.setEnabled(False)
        sb.addWidget(self.wl_name_input)

        self.save_wl_btn = QPushButton("Save Watchlist")
        self.save_wl_btn.setObjectName("saveWlBtn")
        self.save_wl_btn.setEnabled(False)
        self.save_wl_btn.clicked.connect(self.save_as_watchlist)
        sb.addWidget(self.save_wl_btn)

        self.status_label = QLabel("Engine initialization successful. System operational.")
        sb.addWidget(self.status_label, 1)

        layout.addWidget(self.status_bar)

    # ── methods ─────────────────────────────────────────────────────
    def toggle_builder_pane(self):
        if self.builder_card.isVisible():
            self.builder_card.hide()
            self.toggle_builder_btn.setText("\u00BB Expand Builder")
        else:
            self.builder_card.show()
            self.toggle_builder_btn.setText("\u00AB Collapsible Builder")

    def change_builder_tab_view(self):
        clicked = self.sender()
        active = clicked.text()
        for name, btn in self.tab_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(name == active)
            btn.blockSignals(False)
        self.current_active_tab = active

        for f_name, combo in self.builder_combos.items():
            cat = FILTER_MAPPING[f_name]["category"]
            lbl = self.builder_labels[f_name]
            if active == "All" or cat == active:
                combo.setEnabled(True)
                combo.setProperty("disabled_tab", False)
                lbl.setProperty("disabled_tab", False)
            else:
                combo.setEnabled(False)
                combo.setProperty("disabled_tab", True)
                lbl.setProperty("disabled_tab", True)
            combo.style().unpolish(combo)
            combo.style().polish(combo)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def on_filter_changed(self):
        active = []
        for name, combo in self.builder_combos.items():
            v = combo.currentText()
            if v != "Any":
                active.append(f"{name}={v}")
        if active:
            self.status_label.setText(f"Active Screener Query: {', '.join(active)}")
        else:
            self.status_label.setText("Custom filter states cleared. Operational.")

    def load_preset_to_builder(self, preset_name):
        if preset_name == "Custom Builder (None)":
            return
        data = PRESET_STRATEGIES.get(preset_name, {})
        for name, combo in self.builder_combos.items():
            combo.blockSignals(True)
            combo.setCurrentText("Any")
            combo.blockSignals(False)
        for f_name, f_val in data.items():
            if f_name in self.builder_combos:
                c = self.builder_combos[f_name]
                c.blockSignals(True)
                c.setCurrentText(f_val)
                c.blockSignals(False)
        self.on_filter_changed()

    def on_universe_changed(self, text):
        idx_c = self.builder_combos.get("Index")
        cap_c = self.builder_combos.get("Market Cap.")
        if not idx_c or not cap_c:
            return
        idx_c.blockSignals(True)
        cap_c.blockSignals(True)

        mapping = {
            "All Market Listed Stocks": ("Any", "Any"),
            "S&P 500 Giants Only": ("S&P 500", "Any"),
            "Nasdaq 100 Innovators": ("NASDAQ 100", "Any"),
            "Dow Jones Bluechips": ("DJIA", "Any"),
            "Russell 2000 Small Caps": ("Russell 2000", "Any"),
            "Mega Caps ($200B+)": ("Any", "Mega (200bln and more)"),
            "Large Cap Powerhouses": ("Any", "Large (10bln to 200bln)"),
            "Mid Cap / Steady Growth": ("Any", "Mid (2bln to 10bln)"),
            "High Growth Small Cap": ("Any", "Small (300mln to 2bln)"),
        }
        idx_val, cap_val = mapping.get(text, ("Any", "Any"))
        idx_c.setCurrentText(idx_val)
        cap_c.setCurrentText(cap_val)

        idx_c.blockSignals(False)
        cap_c.blockSignals(False)
        self.on_filter_changed()

    def reset_screener_filters(self):
        for name, combo in self.builder_combos.items():
            combo.blockSignals(True)
            combo.setCurrentText("Any")
            combo.blockSignals(False)
        self.universe_dropdown.blockSignals(True)
        self.universe_dropdown.setCurrentText("All Market Listed Stocks")
        self.universe_dropdown.blockSignals(False)
        self.preset_dropdown.blockSignals(True)
        self.preset_dropdown.setCurrentText("Custom Builder (None)")
        self.preset_dropdown.blockSignals(False)
        self.on_filter_changed()
        self.score_label.setText("")
        self.status_label.setText("Filters successfully cleared.")

    def start_screener_task(self):
        active = {}
        for name, combo in self.builder_combos.items():
            v = combo.currentText()
            if v != "Any":
                active[name] = v

        self.status_label.setText("Initiating scanner background worker...")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.score_label.setText("Scanning...")
        self.run_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.wl_name_input.setEnabled(False)
        self.save_wl_btn.setEnabled(False)
        self.search_bar.clear()
        self.table.setRowCount(0)

        self.worker = FinvizWorker(active)
        self.worker.success.connect(self.on_screener_success)
        self.worker.failure.connect(self.on_screener_failure)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.finished.connect(lambda: self.run_btn.setEnabled(True))
        self.worker.finished.connect(lambda: self.reset_btn.setEnabled(True))
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_worker_finished(self):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_bar.hide()

    @Slot(pd.DataFrame, str)
    def on_screener_success(self, df, lib):
        if 'Country' in df.columns:
            df = df.drop(columns=['Country'], errors='ignore')
        elif 'country' in df.columns:
            df = df.drop(columns=['country'], errors='ignore')

        col_rename = {}
        for col in df.columns:
            cl = col.lower()
            if cl == 'price':
                col_rename[col] = 'Price'
            elif cl == 'change':
                col_rename[col] = 'Change'
            elif cl in ('recom', 'recommendation', 'analyst recom', 'analyst recom.'):
                col_rename[col] = 'Analyst Recom.'
        df = df.rename(columns=col_rename)

        if 'Change' in df.columns:
            def fmt_pct(v):
                if pd.isna(v) or v == "-":
                    return "-"
                s = str(v).strip()
                hp = s.endswith('%')
                n = s[:-1] if hp else s
                try:
                    num = float(n)
                    return f"{'+' if num > 0 else ''}{num:.2f}%"
                except ValueError:
                    return s
            df['Change'] = df['Change'].apply(fmt_pct)

        pref = ['Ticker', 'Company', 'Sector', 'Industry', 'Market Cap', 'P/E', 'Price', 'Change', 'Analyst Recom.', 'Volume']
        order = [c for c in pref if c in df.columns]
        other = [c for c in df.columns if c not in pref]
        df = df[order + other]

        self.raw_df = df
        self.export_btn.setEnabled(True)
        self.wl_name_input.setEnabled(True)
        from datetime import datetime
        self.wl_name_input.setText(f"Finviz Screen {datetime.now().strftime('%d %b %H:%M')}")
        self.save_wl_btn.setEnabled(True)
        self.score_label.setText(f"Scanned: {len(df.index)}")
        self.status_label.setText(f"Loaded {len(df.index)} assets successfully via [{lib}].")
        self._populate_table(df)

    @Slot(str)
    def on_screener_failure(self, msg):
        self.score_label.setText("Error")
        self.status_label.setText(msg)
        self.wl_name_input.setEnabled(False)
        self.save_wl_btn.setEnabled(False)

    def _populate_table(self, target_df):
        self.table.setSortingEnabled(False)
        self.table.setColumnCount(len(target_df.columns))
        self.table.setRowCount(len(target_df.index))
        self.table.setHorizontalHeaderLabels(target_df.columns.astype(str).tolist())

        for r_idx, row in target_df.reset_index(drop=True).iterrows():
            for c_idx, val in enumerate(row):
                item = NumericTableWidgetItem(str(val) if pd.notna(val) else "-")
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                self.table.setItem(r_idx, c_idx, item)

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setSortingEnabled(True)

    def apply_client_filter(self):
        if self.raw_df.empty:
            return
        term = self.search_bar.text().lower()
        if not term:
            self._populate_table(self.raw_df)
            self.score_label.setText(f"Scanned: {len(self.raw_df.index)}")
            return
        mask = self.raw_df.astype(str).apply(lambda x: x.str.lower().str.contains(term)).any(axis=1)
        self._populate_table(self.raw_df[mask])
        self.score_label.setText(f"Scanned: {mask.sum()}")

    def export_to_csv(self):
        if self.raw_df.empty:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Data Export", "", "CSV Files (*.csv);;All Files (*)")
        if path:
            try:
                self.raw_df.to_csv(path, index=False)
                self.status_label.setText(f"Data saved to: {path}")
            except Exception as e:
                self.status_label.setText(f"Export Error: {str(e)}")

    def save_as_watchlist(self):
        if self.raw_df.empty:
            return
        name = self.wl_name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Please enter a watchlist name.")
            return

        # Find the ticker/symbol column
        ticker_col = None
        for col in self.raw_df.columns:
            if col.lower() in ('ticker', 'symbol'):
                ticker_col = col
                break
        
        if not ticker_col:
            QMessageBox.warning(self, "Error", "Could not find Ticker/Symbol column in the results.")
            return

        symbols = self.raw_df[ticker_col].dropna().unique().tolist()
        symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]

        if not symbols:
            QMessageBox.warning(self, "Empty List", "No valid symbols found to save.")
            return

        # DatabaseManager.create_watchlist automatically prepends "US_" if not present
        if not name.startswith("US_"):
            wl_name = "US_" + name
        else:
            wl_name = name

        try:
            self.db.create_watchlist(wl_name)
            for sym in symbols:
                self.db.add_to_watchlist(wl_name, sym)
            
            self.status_label.setText(f"Saved {len(symbols)} symbols to watchlist '{wl_name}'")
            QMessageBox.information(
                self, "Watchlist Saved",
                f"Successfully created watchlist '{wl_name}' with {len(symbols)} symbols."
            )
            self.watchlist_changed.emit()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save watchlist: {str(e)}")

    def show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        
        ticker_col_idx = None
        for col_idx in range(self.table.columnCount()):
            header_text = self.table.horizontalHeaderItem(col_idx).text().lower()
            if header_text in ('ticker', 'symbol'):
                ticker_col_idx = col_idx
                break
        
        if ticker_col_idx is None:
            return
            
        ticker_item = self.table.item(row, ticker_col_idx)
        if not ticker_item:
            return
            
        symbol = ticker_item.text().strip().upper()
        if not symbol:
            return

        menu = QMenu(self)
        add_submenu = menu.addMenu(f"Add {symbol} to Watchlist")
        
        try:
            watchlists = self.db.get_watchlists()
        except Exception:
            watchlists = []
            
        if watchlists:
            for wl in watchlists:
                wl_name = wl["name"]
                act = add_submenu.addAction(wl_name)
                # Capture variables properly in lambda
                act.triggered.connect(lambda checked=False, w=wl_name, s=symbol: self.add_symbol_to_watchlist(s, w))
        else:
            act_none = add_submenu.addAction("No watchlists found")
            act_none.setEnabled(False)
            
        add_submenu.addSeparator()
        act_new = add_submenu.addAction("New Watchlist...")
        act_new.triggered.connect(lambda checked=False, s=symbol: self.add_symbol_to_new_watchlist(s))
        
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def add_symbol_to_watchlist(self, symbol, wl_name):
        try:
            self.db.add_to_watchlist(wl_name, symbol)
            self.status_label.setText(f"Added {symbol} to watchlist '{wl_name}'")
            self.watchlist_changed.emit()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to add {symbol} to watchlist: {str(e)}")

    def add_symbol_to_new_watchlist(self, symbol):
        name, ok = QInputDialog.getText(self, "New Watchlist", "Enter name for new watchlist:")
        if ok and name.strip():
            wl_name = name.strip()
            if not wl_name.startswith("US_"):
                wl_name = "US_" + wl_name
            try:
                self.db.create_watchlist(wl_name)
                self.db.add_to_watchlist(wl_name, symbol)
                self.status_label.setText(f"Created watchlist '{wl_name}' and added {symbol}")
                self.watchlist_changed.emit()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to create watchlist: {str(e)}")
