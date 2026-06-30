from PySide6.QtCore import Qt, Slot, QThreadPool
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QStackedWidget,
    QLabel, QFrame, QSizePolicy, QSpacerItem, QComboBox, QInputDialog,
    QMessageBox
)

from modules.finviz_screener import FinvizScreenerWidget
from modules.stock_comparator import StockComparator
from modules.alpaca_screener_tabs import (
    _MarketScreenerTab, PatternScreenerPanel, RatioScannerPanel, MultiBacktesterPanel
)
from modules.stock_evaluate.database import DatabaseManager


class SubtabButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("subtabBtn")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)


class ScreenerPage(QWidget):
    SUBTABS = [
        "Finviz Screener",
        "Stocks Compare",
        "Market Screener",
        "Pattern Scanner",
        "Ratio Scanner",
        "Multi Backtester",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("screenerPage")
        self.db = DatabaseManager()

        self._subtab_widgets = {}
        self._subtab_buttons = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── sub-tab bar ─────────────────────────────────────────────
        bar = QFrame()
        bar.setObjectName("subtabBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 4, 8, 4)
        bar_layout.setSpacing(2)

        for name in self.SUBTABS:
            btn = SubtabButton(name)
            btn.clicked.connect(self._switch_subtab)
            self._subtab_buttons[name] = btn
            bar_layout.addWidget(btn)

        bar_layout.addStretch()
        layout.addWidget(bar)

        # ── Watchlist Selection Top Bar (Context-Sensitive) ─────────
        self.top_bar_widget = QWidget()
        self.top_bar_widget.setStyleSheet("background-color: #0d1117; border-bottom: 1px solid #30363d;")
        top_bar = QHBoxLayout(self.top_bar_widget)
        top_bar.setContentsMargins(12, 6, 12, 6)
        top_bar.setSpacing(10)

        wl_lbl = QLabel("Scan Watchlist:")
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

        layout.addWidget(self.top_bar_widget)
        self.top_bar_widget.setVisible(False)

        # ── stacked content ─────────────────────────────────────────
        self.stack = QStackedWidget()
        self.stack.setObjectName("subtabStack")
        layout.addWidget(self.stack, 1)

        # ── build subtabs ───────────────────────────────────────────
        self._build_finviz_screener()
        self._build_stock_comparator()
        self._build_alpaca_screener_tabs()

        self.refresh_watchlists_combo()

        # default: first subtab active
        if self.SUBTABS:
            self._subtab_buttons[self.SUBTABS[0]].setChecked(True)

    def _build_finviz_screener(self):
        widget = FinvizScreenerWidget()
        self._subtab_widgets["Finviz Screener"] = widget
        self.stack.addWidget(widget)

    def _build_stock_comparator(self):
        widget = StockComparator()
        self._subtab_widgets["Stocks Compare"] = widget
        self.stack.addWidget(widget)

    def _build_alpaca_screener_tabs(self):
        self._mkt = _MarketScreenerTab(QThreadPool.globalInstance())
        self._pattern = PatternScreenerPanel()
        self._ratio = RatioScannerPanel()
        self._backtest = MultiBacktesterPanel()

        self._subtab_widgets["Market Screener"] = self._mkt
        self._subtab_widgets["Pattern Scanner"] = self._pattern
        self._subtab_widgets["Ratio Scanner"]  = self._ratio
        self._subtab_widgets["Multi Backtester"] = self._backtest

        self.stack.addWidget(self._mkt)
        self.stack.addWidget(self._pattern)
        self.stack.addWidget(self._ratio)
        self.stack.addWidget(self._backtest)

        # Connect send_to_wl signals
        self._pattern.send_to_wl.connect(self.handle_send_to_wl)
        self._ratio.send_to_wl.connect(self.handle_send_to_wl)

    def _switch_subtab(self):
        clicked = self.sender()
        name = clicked.text()

        for n, btn in self._subtab_buttons.items():
            btn.setChecked(n == name)

        widget = self._subtab_widgets.get(name)
        if widget:
            self.stack.setCurrentWidget(widget)

        # Show top bar only for Alpaca screener tabs
        is_alpaca_tab = name in ("Market Screener", "Pattern Scanner", "Ratio Scanner", "Multi Backtester")
        self.top_bar_widget.setVisible(is_alpaca_tab)

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
            print(f"Error loading watchlists in Screener: {e}")
            
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
            try:
                constituents = self.db.get_all_constituents()
                flag = "is_sp500" if val == "S&P 500" else "is_nasdaq"
                symbols = [c["symbol"] for c in constituents if c.get(flag)]
            except Exception as e:
                print(f"Error loading constituents for preset {val}: {e}")
        elif typ == "custom":
            try:
                items = self.db.get_watchlist_items(val)
                symbols = [it["symbol"] for it in items]
            except Exception as e:
                print(f"Error loading custom watchlist items for {val}: {e}")

        # Update all Alpaca screener tabs
        if hasattr(self, "_mkt") and hasattr(self._mkt, "set_symbols"):
            self._mkt.set_symbols(symbols)
        if hasattr(self, "_pattern") and hasattr(self._pattern, "set_symbols"):
            self._pattern.set_symbols(symbols)
        if hasattr(self, "_ratio") and hasattr(self._ratio, "set_symbols"):
            self._ratio.set_symbols(symbols)
        if hasattr(self, "_backtest") and hasattr(self._backtest, "set_symbols"):
            self._backtest.set_symbols(symbols)

    @Slot(list)
    def handle_send_to_wl(self, symbols: list):
        if not symbols:
            return
        name, ok = QInputDialog.getText(
            self, "Send to Watchlist",
            f"Enter watchlist name to save {len(symbols)} symbols:",
            text="Screener Output"
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
                self.refresh_watchlists_combo()
                
                # Emit watchlist_changed from FinvizScreenerWidget to trigger app-wide refresh
                fv = self._subtab_widgets.get("Finviz Screener")
                if fv:
                    fv.watchlist_changed.emit()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to save watchlist: {e}")
