import sys, os, csv, traceback
from types import ModuleType
if 'numba' not in sys.modules:
    n = ModuleType('numba')
    n.njit = lambda *a, **k: (a[0] if len(a) == 1 and callable(a[0]) else (lambda f: f))
    n.jit = n.njit
    sys.modules['numba'] = n

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QTabWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QMessageBox, QCompleter, QInputDialog, QStatusBar, QApplication
)
from PySide6.QtCore import Qt, QTimer, Slot, Signal, QStringListModel

from .config import load_api_keys
from .database import DatabaseManager
from .workers import (
    UniverseRefreshWorker, ScreenerWorker, DashboardDataWorker,
    DeepDiveWorker, AIAnalysisWorker
)
from .tabs.dashboard import DashboardTab
from .tabs.screener import ScreenerTab
from .tabs.deep_dive import DeepDiveTab
from .tabs.ai_insights import AIInsightsTab
from .tabs.watchlist import WatchlistTab
from .theme import BG_PRIMARY, TEXT_PRIMARY, ACCENT, ACCENT2, BORDER, BG_SURFACE, TEXT_SECONDARY, BG_CARD
from .logger import get_logger

log = get_logger("StockEvaluate")


class StockEvaluateWidget(QWidget):
    watchlist_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StockEvaluateWidget")

        log.info("Initializing Stock Evaluate Widget")
        self.keys = load_api_keys()
        self.db = DatabaseManager()

        self.master_data = {}
        self.symbol_list = []
        self._active_workers = {}
        self._load_master_csv()

        self._build_ui()
        self._connect_signals()
        self._setup_completer()
        self._refresh_tank_gauge()

    def _load_master_csv(self):
        path = Path(__file__).resolve().parent.parent.parent / "USStockMaster.csv"
        if not path.exists():
            log.warning(f"USStockMaster.csv not found at {path}")
            return
        try:
            with open(str(path), mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sym = row['Symbol'].upper()
                    name = row['Company']
                    self.master_data[sym] = name
                    self.symbol_list.append(f"{sym} - {name}")
            log.info(f"Loaded {len(self.master_data)} symbols from master CSV")
        except Exception as e:
            log.error(f"Error loading master CSV: {e}")

    def _setup_completer(self):
        self.completer = QCompleter(self.symbol_list)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.setMaxVisibleItems(10)

        from PySide6.QtWidgets import QListView
        popup = QListView()
        popup.setStyleSheet(f"QListView {{ background-color: {BG_CARD}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; }} QListView::item {{ color: {TEXT_PRIMARY}; padding: 4px; }} QListView::item:selected {{ background-color: {ACCENT}; color: #ffffff; }}")
        self.completer.setPopup(popup)

        self.completer.activated[str].connect(self._on_completer_activated)

        if hasattr(self.deep_dive_tab, "sym_input"):
            self.deep_dive_tab.sym_input.setCompleter(self.completer)
        if hasattr(self.ai_tab, "sym_input"):
            self.ai_tab.sym_input.setCompleter(self.completer)
        if hasattr(self.watchlist_tab, "add_input"):
            self.watchlist_tab.add_input.setCompleter(self.completer)

        for tab in [self.watchlist_tab, self.screener_tab, self.deep_dive_tab]:
            if hasattr(tab, "set_master_data"):
                tab.set_master_data(self.master_data)

        QTimer.singleShot(500, self._on_startup)

    @Slot(str)
    def _on_completer_activated(self, text):
        idx = self.tabs.currentIndex()
        if idx == 2:
            self.deep_dive_tab.sym_input.setText(text)
            self._run_deep_dive()
        elif idx == 3:
            self.ai_tab.sym_input.setText(text)
            self._run_ai_analysis()

    def _build_ui(self):
        self.setStyleSheet(f"""
            QWidget {{ background-color: {BG_PRIMARY}; color: {TEXT_PRIMARY};
                        font-family: 'Segoe UI'; font-size: 10pt; }}
            QTabWidget::pane {{ border: 1px solid {BORDER}; top: -1px; }}
            QTabBar::tab {{ background: {BG_SURFACE}; color: {TEXT_SECONDARY};
                padding: 10px 20px; border: 1px solid {BORDER};
                border-bottom: none; border-top-left-radius: 6px;
                border-top-right-radius: 6px; margin-right: 4px; }}
            QTabBar::tab:selected {{ background: {BG_PRIMARY}; color: {TEXT_PRIMARY};
                border-bottom: 2px solid {ACCENT}; }}
            QTabBar::tab:hover {{ background: #1B253F; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.dashboard_tab = DashboardTab()
        self.screener_tab = ScreenerTab(db=self.db)
        self.deep_dive_tab = DeepDiveTab()
        self.ai_tab = AIInsightsTab()
        self.watchlist_tab = WatchlistTab(db=self.db)

        self.tabs.addTab(self.dashboard_tab, "DASHBOARD")
        self.tabs.addTab(self.screener_tab, "SCREENER")
        self.tabs.addTab(self.deep_dive_tab, "DEEP DIVE")
        self.tabs.addTab(self.ai_tab, "AI INSIGHTS")
        self.tabs.addTab(self.watchlist_tab, "WATCHLIST")

        layout.addWidget(self.tabs)

        self.status_bar = QFrame()
        self.status_bar.setObjectName("seStatusBar")
        sb = QHBoxLayout(self.status_bar)
        sb.setContentsMargins(8, 2, 8, 2)

        self.universe_count = QLabel("Universe: 0 symbols")
        self.status_label = QLabel("Ready")
        self.status_bar.setStyleSheet(
            f"background-color: {BG_SURFACE}; border-top: 1px solid {BORDER};"
        )
        self.universe_count.setStyleSheet(f"color: {TEXT_SECONDARY}; padding: 2px 8px;")
        self.status_label.setStyleSheet(f"color: {TEXT_SECONDARY}; padding: 2px 8px;")
        sb.addWidget(self.status_label, 1)
        sb.addWidget(self.universe_count)
        layout.addWidget(self.status_bar)

    def _connect_signals(self):
        self.dashboard_tab.btn_refresh.clicked.connect(self._refresh_dashboard)
        self.dashboard_tab.btn_full_refresh.clicked.connect(self._refresh_universe)

        self.screener_tab.btn_screen.clicked.connect(self._run_screener)
        self.screener_tab.open_deep_dive.connect(self._on_open_deep_dive)
        self.screener_tab.add_to_watchlist.connect(self._add_to_watchlist)
        self.screener_tab.watchlist_saved.connect(self._on_watchlist_saved)

        self.deep_dive_tab.btn_analyze.clicked.connect(self._run_deep_dive)
        self.deep_dive_tab.add_to_watchlist.connect(self._add_to_watchlist)
        self.deep_dive_tab.add_to_specific_watchlist.connect(
            self._add_to_watchlist_specific)
        self.deep_dive_tab.watchlist_changed.connect(
            self._on_deep_dive_watchlist_changed)
        self.deep_dive_tab.sym_input.returnPressed.connect(self._run_deep_dive)

        self.ai_tab.run_analysis.connect(self._run_ai_analysis)

        self.watchlist_tab.watchlist_selected.connect(self._on_watchlist_selected)
        self.watchlist_tab.create_list.connect(self._create_watchlist)
        self.watchlist_tab.delete_list.connect(self._delete_watchlist)
        self.watchlist_tab.rename_list.connect(self._rename_watchlist)
        self.watchlist_tab.duplicate_list.connect(self._duplicate_watchlist)
        self.watchlist_tab.add_symbol.connect(self._add_to_watchlist_specific)
        self.watchlist_tab.remove_symbol.connect(self._remove_from_watchlist_specific)
        self.watchlist_tab.update_item.connect(self._update_watchlist_item)
        self.watchlist_tab.double_click_list.connect(
            self._on_watchlist_double_clicked)
        self.watchlist_tab.open_deep_dive.connect(self._on_open_deep_dive)

    def _track_worker(self, worker, name="Worker"):
        wid = id(worker)
        self._active_workers[wid] = worker
        log.info(f"Started {name} (ID: {wid})")
        return wid

    def _cleanup_worker(self, worker_id):
        if worker_id in self._active_workers:
            del self._active_workers[worker_id]

    # ── startup ─────────────────────────────────────────────────────
    def _on_startup(self):
        log.info("Running startup checks")
        missing = [k for k, v in self.keys.items() if not v]
        if missing:
            log.warning(f"Missing API keys: {missing}")
            self.status_label.setText(
                f"\u26A0 Missing API keys: {', '.join(missing)}")

        self._refresh_navigator()

        # Load cached database watchlists and count cached symbols on startup
        # To comply with 'fetch only when asked for', we do NOT perform auto-refresh of indices/universe on startup.
        symbols = self.db.get_symbols()
        count = len(symbols)
        
        self.universe_count.setText(f"Universe: {count} symbols (cached)" if count > 0 else "Universe: 0 symbols")
        self._refresh_watchlist()
        self._refresh_tank_gauge()
        
        if count > 0:
            self.status_label.setText("Universe loaded from cache")
            self.dashboard_tab.status_lbl.setText("Market data cached. Click 'Refresh UI' to update.")
        else:
            self.status_label.setText("Database is empty")
            if not self.keys.get("fmp"):
                self.status_label.setText("FMP API key required. Add to ALLAPI/FMP_API_KEY.txt")
            else:
                self.dashboard_tab.status_lbl.setText("Database is empty. Click 'Full Refresh' to load.")

    # ── universe refresh ────────────────────────────────────────────
    def _refresh_universe(self):
        self.status_label.setText("Refreshing universe...")
        self.dashboard_tab.status_lbl.setText("Refreshing universe...")
        worker = UniverseRefreshWorker(self.db, self.keys)
        worker.progress.connect(self._on_universe_progress)
        worker.finished.connect(self._on_universe_done)
        wid = self._track_worker(worker, "UniverseRefresh")
        worker.finished.connect(lambda: self._cleanup_worker(wid))
        worker.start()

    @Slot(str, int)
    def _on_universe_progress(self, msg, pct):
        self.status_label.setText(msg)
        self.dashboard_tab.status_lbl.setText(msg)
        self.dashboard_tab.prog.setVisible(True)
        self.dashboard_tab.prog.setValue(pct)

    @Slot(str)
    def _on_universe_done(self, error):
        self.dashboard_tab.prog.setVisible(False)
        if error:
            log.error(f"Universe Refresh Failed: {error}")
            QMessageBox.warning(self, "Universe Refresh Error", error)
            self.status_label.setText(f"Error: {error}")
        else:
            count = len(self.db.get_symbols())
            log.info(f"Universe Refresh Complete. Symbols: {count}")
            self.universe_count.setText(f"Universe: {count} symbols")
            self.status_label.setText("Universe updated")
            self._refresh_dashboard()
            self._refresh_navigator()
            self._refresh_watchlist()
            self._refresh_tank_gauge()

    # ── dashboard ───────────────────────────────────────────────────
    def _refresh_dashboard(self):
        log.info("Refreshing dashboard data")
        self.dashboard_tab.status_lbl.setText("Updating indices...")
        worker = DashboardDataWorker(self.keys, self.db)
        worker.data_ready.connect(self._on_dashboard_data)
        worker.finished.connect(self._on_dashboard_done)
        wid = self._track_worker(worker, "DashboardData")
        worker.finished.connect(lambda: self._cleanup_worker(wid))
        worker.start()

    @Slot(dict)
    def _on_dashboard_data(self, data):
        if "indices" in data:
            self.dashboard_tab.update_indices(data["indices"])
        if "movers" in data:
            self.dashboard_tab.update_movers(data["movers"])
        if "sectors" in data:
            self.dashboard_tab.update_sectors(data["sectors"])

    @Slot(str)
    def _on_dashboard_done(self, error):
        if error:
            log.error(f"Dashboard Refresh Failed: {error}")
            self.dashboard_tab.status_lbl.setText(f"Error: {error}")
        else:
            self.dashboard_tab.status_lbl.setText("Market data updated")
            self._refresh_tank_gauge()

    def _refresh_tank_gauge(self):
        count, total = self.db.get_fundamentals_coverage()
        self.dashboard_tab.update_tank(count, total)
        self.screener_tab.update_tank(count, total)

    # ── screener ────────────────────────────────────────────────────
    def _run_screener(self, specific_symbols=None):
        filters = self.screener_tab.collect_filters()
        if specific_symbols:
            filters["specific_symbols"] = specific_symbols
        log.info(f"Running screener with filters: {filters}")
        self.screener_tab.btn_screen.setEnabled(False)
        self.screener_tab.prog.setValue(0)
        self.screener_tab.table.setRowCount(0)
        worker = ScreenerWorker(self.db, self.keys, filters)
        worker.progress.connect(self._on_screen_progress)
        worker.result_ready.connect(self._on_screen_results)
        worker.finished.connect(self._on_screen_done)
        wid = self._track_worker(worker, "Screener")
        worker.finished.connect(lambda: self._cleanup_worker(wid))
        worker.start()

    @Slot(str, int)
    def _on_screen_progress(self, msg, pct):
        self.screener_tab.prog.setValue(pct)
        self.screener_tab.prog.setFormat(msg)

    @Slot(list)
    def _on_screen_results(self, results):
        log.info(f"Screener returned {len(results)} results")
        self.screener_tab.populate_results(results)

    @Slot(str)
    def _on_screen_done(self, error):
        self.screener_tab.btn_screen.setEnabled(True)
        if error:
            log.error(f"Screener Failed: {error}")
            self.status_label.setText(f"Screening error: {error}")
            QMessageBox.warning(self, "Screening Error", error)
            self.screener_tab.prog.setFormat(f"Error: {error}")
        else:
            self.status_label.setText("Screening complete")

    # ── deep dive ───────────────────────────────────────────────────
    def _on_open_deep_dive(self, symbol, list_name="Main"):
        self.tabs.setCurrentIndex(2)
        if list_name:
            if self.deep_dive_tab.watchlist_combo.currentText() == list_name:
                self._on_deep_dive_watchlist_changed(list_name)
            else:
                self.deep_dive_tab.watchlist_combo.setCurrentText(list_name)
        self.deep_dive_tab.set_symbol(symbol)
        self._run_deep_dive()

    def _run_deep_dive(self):
        raw = self.deep_dive_tab.sym_input.text().upper()
        symbol = raw.split(" - ")[0].strip()
        if not symbol:
            return
        log.info(f"Running deep dive for {symbol}")
        self.deep_dive_tab.clear_data()
        self.deep_dive_tab.btn_analyze.setEnabled(False)
        self.deep_dive_tab.status_lbl.setText(f"Analyzing {symbol}...")
        # Always fetch at least 365 days to ensure SMA200 can be computed
        fetch_days = max(365, self.deep_dive_tab.get_period_days())
        worker = DeepDiveWorker(symbol, self.keys, self.db, fetch_days)
        worker.progress.connect(self.deep_dive_tab.status_lbl.setText)
        worker.data_ready.connect(self._on_deep_dive_data)
        worker.finished.connect(self._on_deep_dive_done)
        wid = self._track_worker(worker, f"DeepDive_{symbol}")
        worker.finished.connect(lambda: self._cleanup_worker(wid))
        worker.start()

    @Slot(dict)
    def _on_deep_dive_data(self, data):
        log.info("Deep dive data ready")
        self.deep_dive_tab.update_data(data)

    @Slot(str)
    def _on_deep_dive_done(self, error):
        self.deep_dive_tab.btn_analyze.setEnabled(True)
        if error:
            log.error(f"Deep Dive Failed: {error}")
            self.deep_dive_tab.status_lbl.setText(f"Error: {error}")
            QMessageBox.warning(self, "Analysis Error", error)
        else:
            self.deep_dive_tab.status_lbl.setText("Analysis complete")

    # ── watchlist ───────────────────────────────────────────────────
    def _add_to_watchlist(self, symbol):
        log.info(f"Adding {symbol} to watchlist")
        target = self.watchlist_tab.current_list if self.watchlist_tab.current_type == "User" else "My First List"
        self._add_to_watchlist_specific(symbol, target)

    def _on_watchlist_selected(self, name, type_):
        log.info(f"Loading watchlist: {name} ({type_})")
        if type_ == "Preset":
            p_type = "Index"
            p_val = name
            if ":" in name:
                p_type, p_val = name.split(":", 1)
                p_type = p_type.strip()
                p_val = p_val.strip()
            symbols = self.db.get_preset_symbols(p_type, p_val)
            items = [{"symbol": s, "notes": ""} for s in symbols]
            funds = self.db.get_fundamentals(symbols)
            self.watchlist_tab.load_watchlist(items, funds)
        else:
            items = self.db.get_watchlist_items(name)
            symbols = [i["symbol"] for i in items]
            funds = self.db.get_fundamentals(symbols)
            self.watchlist_tab.load_watchlist(items, funds)

    def _create_watchlist(self):
        name, ok = QInputDialog.getText(self, "New Watchlist", "Enter name:")
        if ok and name:
            self.db.create_watchlist(name)
            self._refresh_navigator()
            self.watchlist_changed.emit()

    def _delete_watchlist(self, name):
        if name == "My First List":
            QMessageBox.warning(self, "Delete Denied",
                                "Cannot delete the default watchlist.")
            return
        res = QMessageBox.question(self, "Delete",
                                   f"Delete watchlist '{name}'?",
                                   QMessageBox.Yes | QMessageBox.No)
        if res == QMessageBox.Yes:
            self.db.delete_watchlist(name)
            if self.watchlist_tab.current_list == name:
                self.watchlist_tab.current_list = "My First List"
            self._refresh_navigator()
            self.watchlist_changed.emit()

    def _rename_watchlist(self, name):
        new_name, ok = QInputDialog.getText(self, "Rename Watchlist",
                                            f"Rename '{name}' to:", text=name)
        if ok and new_name and new_name != name:
            self.db.rename_watchlist(name, new_name)
            self.watchlist_tab.current_list = new_name
            self._refresh_navigator()
            self.watchlist_changed.emit()

    def _duplicate_watchlist(self, name, type_):
        new_name, ok = QInputDialog.getText(
            self, "Duplicate Watchlist", "New list name:",
            text=f"Copy of {name}")
        if ok and new_name:
            self.db.create_watchlist(new_name)
            if type_ == "Preset":
                p_type = "Index"
                p_val = name
                if ":" in name:
                    p_type, p_val = name.split(":", 1)
                    p_type = p_type.strip()
                    p_val = p_val.strip()
                symbols = self.db.get_preset_symbols(p_type, p_val)
                for s in symbols:
                    self.db.add_to_watchlist(new_name, s)
            else:
                items = self.db.get_watchlist_items(name)
                for i in items:
                    self.db.add_to_watchlist(
                        new_name, i["symbol"], i["entry_price"],
                        i["target_price"], i["stop_loss"], i["notes"])
            self._refresh_navigator()
            self.watchlist_changed.emit()

    def _add_to_watchlist_specific(self, symbol, list_name):
        log.info(f"Adding {symbol} to {list_name}")
        self.db.add_to_watchlist(list_name, symbol)
        if self.watchlist_tab.current_list == list_name:
            self._on_watchlist_selected(list_name, "User")
        self.status_label.setText(f"Added {symbol} to {list_name}")
        self.watchlist_changed.emit()

    def _remove_from_watchlist_specific(self, symbol, list_name):
        log.info(f"Removing {symbol} from {list_name}")
        self.db.remove_from_watchlist(list_name, symbol)
        self._on_watchlist_selected(list_name, "User")
        self.watchlist_changed.emit()

    def _update_watchlist_item(self, list_name, symbol, field, value):
        self.db.update_watchlist_item_field(list_name, symbol, field, value)

    def _on_watchlist_saved(self, name):
        try:
            log.info(f"Watchlist saved signal received: {name}")
            self.watchlist_tab.current_list = name
            self.watchlist_tab.current_type = "User"
            self._refresh_navigator()
            self._on_watchlist_selected(name, "User")
            self.tabs.setCurrentIndex(4)
            self.status_label.setText(
                f"Screener results saved to '{name}'")
            QMessageBox.information(
                self, "Watchlist Saved",
                f"Successfully created watchlist '{name}' with filtered results.")
            self.watchlist_changed.emit()
        except Exception as e:
            log.error(f"Error in _on_watchlist_saved: {e}")
            traceback.print_exc()

    def _refresh_navigator(self):
        user_lists = self.db.get_watchlists()
        sectors = self.db.get_sectors()
        caps = ["Mega Cap", "Large Cap", "Mid Cap", "Small Cap", "Micro Cap"]
        self.watchlist_tab.update_navigator(user_lists, sectors, caps)
        all_names = ["S&P 500", "NASDAQ-100"] + \
            [f"Sector: {s}" for s in sectors] + \
            [f"Cap Segment: {c}" for c in caps] + \
            [ul["name"] for ul in user_lists]
        self.deep_dive_tab.update_watchlists(all_names)
        self.deep_dive_tab.update_watchlist_menu(
            [ul["name"] for ul in user_lists])

    def _refresh_watchlist(self):
        self._on_watchlist_selected(
            self.watchlist_tab.current_list,
            self.watchlist_tab.current_type)

    @Slot()
    def handle_external_watchlist_change(self):
        log.info("External watchlist change received, refreshing UI...")
        self._refresh_navigator()
        self._refresh_watchlist()

    def _on_watchlist_double_clicked(self, name, type_):
        log.info(f"Double-clicked watchlist {name}: Running screener...")
        if type_ == "Preset":
            p_type = "Index"
            p_val = name
            if ":" in name:
                p_type, p_val = name.split(":", 1)
                p_type = p_type.strip()
                p_val = p_val.strip()
            symbols = self.db.get_preset_symbols(p_type, p_val)
        else:
            items = self.db.get_watchlist_items(name)
            symbols = [i["symbol"] for i in items]
        if not symbols:
            QMessageBox.warning(
                self, "Empty List", f"Watchlist '{name}' has no symbols.")
            return
        self.tabs.setCurrentWidget(self.screener_tab)
        self.status_label.setText(
            f"Screening list: {name} ({len(symbols)} symbols)")
        self._run_screener(specific_symbols=symbols)

    def _on_deep_dive_watchlist_changed(self, name):
        if not name or name == "All Symbols":
            self.deep_dive_tab.sym_input.setCompleter(self.completer)
            self.deep_dive_tab.set_watchlist_symbols([])
            return
        symbols = []
        if name in ["S&P 500", "NASDAQ-100"]:
            symbols = self.db.get_preset_symbols("Index", name)
        elif name.startswith("Cap Segment: "):
            symbols = self.db.get_preset_symbols(
                "Cap Segment", name[13:].strip())
        elif name.startswith("Sector: "):
            symbols = self.db.get_preset_symbols(
                "Sector", name[8:].strip())
        else:
            items = self.db.get_watchlist_items(name)
            symbols = [i["symbol"] for i in items]
        if symbols:
            filtered = [f"{s} - {self.master_data.get(s, '')}"
                        for s in symbols]
            self.deep_dive_tab.set_watchlist_symbols(filtered)
            nc = QCompleter(filtered)
            nc.setCaseSensitivity(Qt.CaseInsensitive)
            nc.setFilterMode(Qt.MatchContains)
            self.deep_dive_tab.sym_input.setCompleter(nc)
            self.deep_dive_tab.sym_input.setText(filtered[0])
            self._run_deep_dive()
        else:
            self.deep_dive_tab.set_watchlist_symbols([])
            self.deep_dive_tab.sym_input.setCompleter(self.completer)

    # ── AI analysis ─────────────────────────────────────────────────
    def _run_ai_analysis(self, symbol, analysis_type, provider):
        log.info(f"Running AI analysis for {symbol} ({analysis_type})")
        funds = self.db.get_fundamentals([symbol])
        context = funds[0] if funds else {}
        worker = AIAnalysisWorker(symbol, analysis_type, context, self.keys)
        worker.progress.connect(self.ai_tab.status_lbl.setText)
        worker.finished.connect(
            lambda text, err: self._on_ai_data(
                symbol, analysis_type, text, err))
        wid = self._track_worker(worker, f"AI_{symbol}")
        worker.finished.connect(lambda: self._cleanup_worker(wid))
        worker.start()

    def _on_ai_data(self, symbol, analysis_type, text, error):
        self.ai_tab.btn_generate.setEnabled(True)
        if error:
            log.error(f"AI Analysis Failed: {error}")
            self.ai_tab.status_lbl.setText(f"Error: {error}")
            QMessageBox.warning(self, "AI Error", error)
        else:
            self.ai_tab.status_lbl.setText("Analysis complete")
            self.ai_tab.set_output(text, symbol, analysis_type)
