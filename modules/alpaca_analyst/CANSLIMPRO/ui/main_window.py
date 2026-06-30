"""Main application window."""
from __future__ import annotations
from PySide6.QtWidgets import QMainWindow, QTabWidget, QStatusBar, QLabel
from PySide6.QtCore import Qt

from config import APP_NAME, APP_VERSION, key_status
from ui.watchlist_tab import WatchlistTab
from ui.screener_tab  import ScreenerTab
from ui.results_tab   import ResultsTab
from ui.report_tab    import ReportTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION}")
        self.setMinimumSize(1100, 700)
        self.resize(1320, 840)
        self._build_ui()
        self._wire_signals()
        self._show_key_status()

    def _build_ui(self):
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.North)
        self.setCentralWidget(self.tabs)

        self.watchlist_tab = WatchlistTab()
        self.screener_tab  = ScreenerTab()
        self.results_tab   = ResultsTab()
        self.report_tab    = ReportTab()

        self.tabs.addTab(self.watchlist_tab, "  Watchlist  ")
        self.tabs.addTab(self.screener_tab,  "  Screener  ")
        self.tabs.addTab(self.results_tab,   "  Results  ")
        self.tabs.addTab(self.report_tab,    "  PDF Report  ")

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self._key_label = QLabel("")
        self._key_label.setStyleSheet("color: #555; font-size: 11px; padding-right: 8px;")
        self.status_bar.addPermanentWidget(self._key_label)

    def _wire_signals(self):
        self.watchlist_tab.watchlist_changed.connect(self.screener_tab.set_tickers)
        self.watchlist_tab.watchlist_changed.connect(
            lambda t: self.status_bar.showMessage(f"{len(t)} tickers in watchlist", 3000)
        )
        self.screener_tab.screening_complete.connect(self.results_tab.load_results)
        self.screener_tab.screening_complete.connect(
            lambda r: self.tabs.setCurrentIndex(2)
        )
        self.screener_tab.screening_complete.connect(
            lambda r: self.status_bar.showMessage(
                f"Screening complete — {len(r)} candidates found", 5000)
        )
        self.results_tab.results_updated.connect(self.report_tab.load_results)

    def _show_key_status(self):
        ks = key_status()
        parts = []
        parts.append(f"FMP: {'✓' if ks['fmp'] else '–'}")
        parts.append(f"Alpaca: {'✓' if ks['alpaca'] else '–'}")
        parts.append("yFinance: ✓ (always)")
        parts.append("US + IN markets")
        self._key_label.setText("  |  ".join(parts))
