"""Watchlist tab — built-in lists, CSV import, drag-and-drop, save/load."""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QFileDialog, QLineEdit,
    QHeaderView, QAbstractItemView, QGroupBox, QMessageBox,
    QComboBox, QInputDialog, QSplitter, QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QColor, QFont

from core.csv_handler import load_watchlist
from core.data_fetcher import detect_market
from core.watchlists import (
    builtin_names, load_builtin,
    save_user_list, list_user_lists, load_user_list, delete_user_list,
)


class WatchlistTab(QWidget):
    watchlist_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tickers: list[str] = []
        self.setAcceptDrops(True)
        self._build_ui()

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        hdr = QLabel("Watchlist")
        hdr.setStyleSheet("font-size:20px;font-weight:bold;color:#ebebf0;")
        root.addWidget(hdr)

        # Split: left=controls, right=ticker table
        splitter = QSplitter(Qt.Horizontal)

        # ── Left panel ────────────────────────────────────────────────────────
        left = QWidget()
        llay = QVBoxLayout(left)
        llay.setSpacing(10)
        llay.setContentsMargins(0, 0, 8, 0)

        # Built-in lists
        bi_grp = QGroupBox("Built-in Watchlists")
        bi_lay = QVBoxLayout(bi_grp)
        bi_lay.setSpacing(6)
        for name in builtin_names():
            btn = QPushButton(name)
            btn.setToolTip(f"Load {name} (replaces current list)")
            btn.clicked.connect(lambda checked=False, n=name: self._load_builtin(n))
            bi_lay.addWidget(btn)
        llay.addWidget(bi_grp)

        # Saved watchlists
        sv_grp = QGroupBox("Saved Watchlists")
        sv_lay = QVBoxLayout(sv_grp)
        self.saved_combo = QComboBox()
        self.saved_combo.setPlaceholderText("-- select saved list --")
        sv_lay.addWidget(self.saved_combo)

        sv_btn_row = QHBoxLayout()
        self.btn_load_saved  = QPushButton("Load")
        self.btn_save_cur    = QPushButton("Save Current As…")
        self.btn_del_saved   = QPushButton("Delete")
        self.btn_del_saved.setObjectName("dangerBtn")
        sv_btn_row.addWidget(self.btn_load_saved)
        sv_btn_row.addWidget(self.btn_del_saved)
        sv_lay.addLayout(sv_btn_row)
        sv_lay.addWidget(self.btn_save_cur)

        self.btn_load_saved.clicked.connect(self._load_saved)
        self.btn_save_cur.clicked.connect(self._save_current)
        self.btn_del_saved.clicked.connect(self._delete_saved)
        llay.addWidget(sv_grp)

        # Drop zone
        self.drop_zone = _DropZone()
        self.drop_zone.file_dropped.connect(self._load_csv)
        llay.addWidget(self.drop_zone)

        io_row = QHBoxLayout()
        self.btn_browse = QPushButton("Browse CSV…")
        self.btn_browse.clicked.connect(self._browse_csv)
        self.btn_clear  = QPushButton("Clear All")
        self.btn_clear.setObjectName("dangerBtn")
        self.btn_clear.clicked.connect(self._clear)
        io_row.addWidget(self.btn_browse)
        io_row.addWidget(self.btn_clear)
        llay.addLayout(io_row)

        # Manual add
        man_grp = QGroupBox("Add Ticker Manually")
        man_lay = QHBoxLayout(man_grp)
        self.manual_input = QLineEdit()
        self.manual_input.setPlaceholderText("NVDA  or  RELIANCE.NS  (comma/space separated)")
        self.manual_input.returnPressed.connect(self._add_manual)
        btn_add = QPushButton("Add")
        btn_add.clicked.connect(self._add_manual)
        man_lay.addWidget(self.manual_input)
        man_lay.addWidget(btn_add)
        llay.addWidget(man_grp)
        llay.addStretch()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#8e8e93;font-size:11px;")
        self.status_label.setWordWrap(True)
        llay.addWidget(self.status_label)

        splitter.addWidget(left)

        # ── Right panel — ticker table ─────────────────────────────────────────
        right = QWidget()
        rlay  = QVBoxLayout(right)
        rlay.setContentsMargins(8, 0, 0, 0)
        rlay.setSpacing(6)

        self.count_label = QLabel("0 tickers")
        self.count_label.setStyleSheet("color:#8e8e93;font-size:12px;")
        rlay.addWidget(self.count_label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Ticker", "Market", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 56)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        rlay.addWidget(self.table)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter)

        self._refresh_saved_combo()

    # ── Built-in list loading ─────────────────────────────────────────────────

    def _load_builtin(self, name: str):
        tickers, err = load_builtin(name)
        if err:
            QMessageBox.warning(self, "Load Error", err)
            return
        if self.tickers:
            reply = QMessageBox.question(
                self, "Replace?",
                f"Replace current {len(self.tickers)} tickers with {name} ({len(tickers)} tickers)?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.tickers = tickers
        self._refresh_table()
        self.status_label.setText(f"Loaded {name}: {len(tickers)} tickers")
        self.watchlist_changed.emit(self.tickers)

    # ── User watchlist save/load ──────────────────────────────────────────────

    def _save_current(self):
        if not self.tickers:
            QMessageBox.warning(self, "Nothing to Save", "Add some tickers first.")
            return
        name, ok = QInputDialog.getText(
            self, "Save Watchlist", "Watchlist name:",
            text=f"My List {len(list_user_lists()) + 1}"
        )
        if not ok or not name.strip():
            return
        err = save_user_list(name.strip(), self.tickers)
        if err:
            QMessageBox.critical(self, "Save Error", err)
        else:
            self.status_label.setText(f"Saved '{name}' ({len(self.tickers)} tickers)")
            self._refresh_saved_combo()

    def _refresh_saved_combo(self):
        self.saved_combo.clear()
        for name in list_user_lists():
            self.saved_combo.addItem(name)

    def _load_saved(self):
        name = self.saved_combo.currentText()
        if not name:
            return
        tickers, err = load_user_list(name)
        if err:
            QMessageBox.warning(self, "Load Error", err)
            return
        self.tickers = tickers
        self._refresh_table()
        self.status_label.setText(f"Loaded '{name}': {len(tickers)} tickers")
        self.watchlist_changed.emit(self.tickers)

    def _delete_saved(self):
        name = self.saved_combo.currentText()
        if not name:
            return
        if QMessageBox.question(
            self, "Delete?", f"Delete saved list '{name}'?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            err = delete_user_list(name)
            if err:
                QMessageBox.critical(self, "Delete Error", err)
            else:
                self._refresh_saved_combo()
                self.status_label.setText(f"Deleted '{name}'")

    # ── CSV I/O ───────────────────────────────────────────────────────────────

    def _browse_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Watchlist CSV", "", "CSV Files (*.csv);;All Files (*)"
        )
        if path:
            self._load_csv(path)

    def _load_csv(self, path: str):
        tickers, warnings = load_watchlist(path)
        if not tickers:
            QMessageBox.warning(self, "No Tickers Found",
                                "\n".join(warnings) or "No valid tickers in file.")
            return
        added = sum(1 for t in tickers if t not in self.tickers)
        for t in tickers:
            if t not in self.tickers:
                self.tickers.append(t)
        self._refresh_table()
        msg = f"Loaded {added} tickers from {path.split('/')[-1].split(chr(92))[-1]}"
        if warnings:
            msg += f"  ({len(warnings)} warnings)"
        self.status_label.setText(msg)
        self.watchlist_changed.emit(self.tickers)

    def _add_manual(self):
        raw = self.manual_input.text().strip().upper()
        if not raw:
            return
        added = 0
        for t in raw.replace(",", " ").split():
            if t and t not in self.tickers:
                self.tickers.append(t)
                added += 1
        self.manual_input.clear()
        if added:
            self._refresh_table()
            self.status_label.setText(f"Added {added} ticker(s)")
            self.watchlist_changed.emit(self.tickers)

    def _clear(self):
        if self.tickers and QMessageBox.question(
            self, "Clear?", "Remove all tickers from the current list?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.tickers.clear()
            self._refresh_table()
            self.watchlist_changed.emit(self.tickers)

    def _remove_ticker(self, ticker: str):
        if ticker in self.tickers:
            self.tickers.remove(ticker)
            self._refresh_table()
            self.watchlist_changed.emit(self.tickers)

    # ── Table refresh ─────────────────────────────────────────────────────────

    def _refresh_table(self):
        self.table.setRowCount(len(self.tickers))
        for i, t in enumerate(self.tickers):
            market = detect_market(t)
            color  = "#1d9e75" if market == "IN" else "#185fa5"

            ti = QTableWidgetItem(t)
            ti.setFont(QFont("Courier", 11))
            ti.setFlags(ti.flags() & ~Qt.ItemIsEditable)

            mi = QTableWidgetItem(market)
            mi.setTextAlignment(Qt.AlignCenter)
            mi.setForeground(QColor(color))
            mi.setFlags(mi.flags() & ~Qt.ItemIsEditable)

            btn = QPushButton("x")
            btn.setFixedSize(34, 24)
            btn.setStyleSheet("color:#993c1d;border:none;background:transparent;font-weight:bold;")
            btn.clicked.connect(lambda _, tick=t: self._remove_ticker(tick))

            self.table.setItem(i, 0, ti)
            self.table.setItem(i, 1, mi)
            self.table.setCellWidget(i, 2, btn)
            self.table.setRowHeight(i, 26)

        us  = sum(1 for t in self.tickers if detect_market(t) == "US")
        ind = len(self.tickers) - us
        parts = []
        if us:  parts.append(f"{us} US")
        if ind: parts.append(f"{ind} Indian")
        self.count_label.setText(
            f"{len(self.tickers)} ticker{'s' if len(self.tickers) != 1 else ''}"
            + (f"  ({', '.join(parts)})" if parts else "")
        )

    # ── Drag-and-drop ─────────────────────────────────────────────────────────

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p.endswith(".csv"):
                self._load_csv(p)
                break


# ── Drop zone widget ──────────────────────────────────────────────────────────

class _DropZone(QWidget):
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(60)
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        self.lbl = QLabel("Drop a CSV file here  or  use Browse")
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setStyleSheet("color:#8e8e93;font-size:12px;")
        lay.addWidget(self.lbl)
        self._style(False)

    def _style(self, hover: bool):
        self.setStyleSheet(
            f"border:{'2px solid #f5a623' if hover else '1px dashed #3a3a3c'};"
            f"border-radius:6px;background:{'#2c2c2e' if hover else '#1a1a1c'};"
        )

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self._style(True); e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._style(False)

    def dropEvent(self, e):
        self._style(False)
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p.endswith(".csv"):
                self.file_dropped.emit(p); break
