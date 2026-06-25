"""
Watchlist Tab — Multi-list management system.
"""
import csv
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox, QTextEdit, QSplitter, QMenu,
    QFileDialog, QMessageBox, QListWidget, QListWidgetItem, QInputDialog,
    QApplication
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont, QColor
from ..theme import ACCENT, ACCENT2, SUCCESS, DANGER, WARNING, TEXT_SECONDARY, BG_CARD, BORDER, BG_SURFACE


class WatchlistTab(QWidget):
    open_deep_dive = Signal(str)
    watchlist_selected = Signal(str, str)  # name, type (Preset/User)
    create_list = Signal()
    delete_list = Signal(str)
    rename_list = Signal(str)
    duplicate_list = Signal(str, str)
    add_symbol = Signal(str, str)  # symbol, list_name
    remove_symbol = Signal(str, str) # symbol, list_name
    update_item = Signal(str, str, str, object) # list_name, symbol, field, value
    double_click_list = Signal(str, str) # name, type


    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.master_data = {}
        self.current_list = "My First List"
        self.current_type = "User"
        self._build_ui()

    def set_master_data(self, data):
        self.master_data = data


    def _build_ui(self):
        main_lay = QHBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        self.splitter = QSplitter(Qt.Horizontal)

        # --- Sidebar ---
        sidebar = QFrame()
        sidebar.setFixedWidth(240)
        sidebar.setStyleSheet(f"background: {BG_SURFACE}; border-right: 1px solid {BORDER};")
        slay = QVBoxLayout(sidebar)
        slay.setContentsMargins(10, 14, 10, 14)
        slay.setSpacing(10)

        lbl = QLabel("NAVIGATOR")
        lbl.setFont(QFont("Segoe UI", 10, QFont.Bold))
        lbl.setStyleSheet(f"color: {ACCENT2};")
        slay.addWidget(lbl)

        self.list_nav = QListWidget()
        self.list_nav.setStyleSheet(f"""
            QListWidget {{ background: transparent; border: none; }}
            QListWidget::item {{ padding: 8px; border-radius: 4px; color: #CCC; }}
            QListWidget::item:selected {{ background: {ACCENT}; color: white; font-weight: bold; }}
            QListWidget::item:hover {{ background: rgba(255,255,255,0.05); }}
        """)
        self.list_nav.itemSelectionChanged.connect(self._on_nav_change)
        self.list_nav.itemDoubleClicked.connect(self._on_nav_double_click)
        slay.addWidget(self.list_nav)

        # Sidebar Buttons
        sbtns = QHBoxLayout()
        self.btn_new = QPushButton("+ New")
        self.btn_new.setToolTip("Create new watchlist")
        self.btn_new.clicked.connect(self.create_list.emit)
        
        self.btn_ren = QPushButton("Rename")
        self.btn_ren.setProperty("class", "secondary")
        self.btn_ren.clicked.connect(lambda: self.rename_list.emit(self.current_list))

        self.btn_del = QPushButton("Delete")
        self.btn_del.setProperty("class", "danger")
        self.btn_del.clicked.connect(lambda: self.delete_list.emit(self.current_list))
        
        sbtns.addWidget(self.btn_new)
        sbtns.addWidget(self.btn_ren)
        sbtns.addWidget(self.btn_del)
        slay.addLayout(sbtns)

        self.splitter.addWidget(sidebar)

        # --- Right Panel ---
        right_panel = QWidget()
        rlay = QVBoxLayout(right_panel)
        rlay.setContentsMargins(14, 14, 14, 14)
        rlay.setSpacing(10)

        # Toolbar
        tbar = QHBoxLayout()
        self.list_title = QLabel("Watchlist: My First List")
        self.list_title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        self.list_title.setStyleSheet(f"color: {ACCENT};")
        tbar.addWidget(self.list_title)
        tbar.addSpacing(20)

        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText("Add symbol...")
        self.add_input.setFixedWidth(120)
        self.add_input.returnPressed.connect(self._on_add)
        tbar.addWidget(self.add_input)

        self.btn_add = QPushButton("+")
        self.btn_add.setFixedWidth(40)
        self.btn_add.clicked.connect(self._on_add)
        tbar.addWidget(self.btn_add)

        tbar.addSpacing(10)
        self.btn_duplicate = QPushButton("Duplicate")
        self.btn_duplicate.setProperty("class", "secondary")
        self.btn_duplicate.clicked.connect(lambda: self.duplicate_list.emit(self.current_list, self.current_type))
        tbar.addWidget(self.btn_duplicate)

        self.btn_import = QPushButton("Import")
        self.btn_import.setProperty("class", "secondary")
        self.btn_import.clicked.connect(self._import_csv)
        tbar.addWidget(self.btn_import)

        self.btn_export = QPushButton("Export")
        self.btn_export.setProperty("class", "secondary")
        self.btn_export.clicked.connect(self._export)
        tbar.addWidget(self.btn_export)

        self.btn_save_changes = QPushButton("Save")
        self.btn_save_changes.setStyleSheet(f"background: {SUCCESS}; color: white; font-weight: bold;")
        self.btn_save_changes.clicked.connect(self._on_save)
        tbar.addWidget(self.btn_save_changes)

        tbar.addStretch()
        self.count_lbl = QLabel("0 symbols")
        self.count_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        tbar.addWidget(self.count_lbl)
        rlay.addLayout(tbar)

        # Table
        self.table_splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget()
        self.table.setColumnCount(11)
        self.table.setHorizontalHeaderLabels([
            "Symbol", "Company", "Price", "Daily Chg%",
            "SMA20", "SMA50", "SMA200", "P&L %",
            "Score", "Notes", "Added"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.cellChanged.connect(self._on_cell_edit)

        widths = [70, 150, 80, 80, 80, 80, 80, 75, 55, 150, 90]
        for i, w in enumerate(widths):
            self.table.setColumnWidth(i, w)

        self.table_splitter.addWidget(self.table)

        # Notes Panel
        notes_frame = QFrame()
        notes_frame.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 8px;")
        nlay = QVBoxLayout(notes_frame)
        nlay.setContentsMargins(10, 8, 10, 8)
        
        self.notes_label = QLabel("Notes — select a symbol")
        self.notes_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        nlay.addWidget(self.notes_label)

        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(80)
        self.notes_edit.setStyleSheet(f"background: {BG_SURFACE}; border: 1px solid {BORDER}; border-radius: 4px; color: white;")
        self.notes_edit.setPlaceholderText("Enter notes for selected symbol...")
        self.notes_edit.textChanged.connect(self._on_notes_changed)
        nlay.addWidget(self.notes_edit)

        self.table_splitter.addWidget(notes_frame)
        self.table_splitter.setStretchFactor(0, 4)
        self.table_splitter.setStretchFactor(1, 1)

        rlay.addWidget(self.table_splitter)
        self.splitter.addWidget(right_panel)
        main_lay.addWidget(self.splitter)

        self.table.itemSelectionChanged.connect(self._on_selection)
        self._current_notes_symbol = None

    # --- Data Loading ---
    def update_navigator(self, user_lists, sectors, caps):
        self.list_nav.blockSignals(True)
        self.list_nav.clear()

        # Presets
        p_item = QListWidgetItem("—— PRESETS ——")
        p_item.setFlags(Qt.NoItemFlags)
        p_item.setForeground(QColor(TEXT_SECONDARY))
        self.list_nav.addItem(p_item)

        presets = ["S&P 500", "NASDAQ-100"] 
        presets += [f"Sector: {s}" for s in sectors]
        presets += [f"Cap Segment: {c}" for c in caps]
        
        for p in presets:
            item = QListWidgetItem(p)
            item.setData(Qt.UserRole, "Preset")
            self.list_nav.addItem(item)


        # User Lists
        u_item = QListWidgetItem("\n—— MY LISTS ——")
        u_item.setFlags(Qt.NoItemFlags)
        u_item.setForeground(QColor(TEXT_SECONDARY))
        self.list_nav.addItem(u_item)

        for ul in user_lists:
            if ul.get("is_preset"): continue # Skip presets in user list loop
            
            item = QListWidgetItem(ul["name"])
            item.setData(Qt.UserRole, "User")
            self.list_nav.addItem(item)
            if ul["name"] == self.current_list:
                item.setSelected(True)
                self.list_nav.scrollToItem(item)

        self.list_nav.blockSignals(False)

    def load_watchlist(self, watchlist_data, fundamentals=None):
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        fund_map = {f["symbol"]: f for f in (fundamentals or [])}

        for w in watchlist_data:
            row = self.table.rowCount()
            self.table.insertRow(row)
            sym = w.get("symbol", "")
            fund = fund_map.get(sym, {})

            # Symbol
            item = QTableWidgetItem(sym)
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(QColor(ACCENT))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)

            # Company
            item = QTableWidgetItem(fund.get("company_name") or self.master_data.get(sym, "—"))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, item)


            # Price
            price = fund.get("price")
            item = QTableWidgetItem(f"${price:,.2f}" if price else "—")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 2, item)

            # Chg%
            chg = fund.get("change_pct")
            item = QTableWidgetItem(f"{chg:+.2f}%" if chg is not None else "—")
            if chg is not None:
                item.setForeground(QColor(SUCCESS if chg >= 0 else DANGER))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 3, item)

            # Technicals
            for i, field in enumerate(["price_avg20", "price_avg50", "price_avg200"]):
                val = fund.get(field)
                item = QTableWidgetItem(f"${val:,.2f}" if val else "—")
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, 4 + i, item)

            # P&L (Placeholder or calc)
            item = QTableWidgetItem("—")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 7, item)

            # Score
            score = fund.get("score", 0)
            item = QTableWidgetItem(str(int(score)) if score else "—")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 8, item)

            # Notes
            notes = w.get("notes", "")
            item = QTableWidgetItem(notes)
            self.table.setItem(row, 9, item)

            # Added
            added = w.get("added_date", "")[:10]
            item = QTableWidgetItem(added)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 10, item)

        self.count_lbl.setText(f"{len(watchlist_data)} symbols")
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)

    # --- Events ---
    def _on_nav_change(self):
        items = self.list_nav.selectedItems()
        if not items: return
        item = items[0]
        name = item.text()
        type_ = item.data(Qt.UserRole)
        if not type_: return

        self.current_list = name
        self.current_type = type_
        self.list_title.setText(f"Watchlist: {name}")
        
        # Disable edit/delete for presets
        is_user = (type_ == "User")
        self.btn_ren.setEnabled(is_user)
        self.btn_del.setEnabled(is_user)
        self.btn_add.setEnabled(is_user)
        self.add_input.setEnabled(is_user)

        self.watchlist_selected.emit(name, type_)

    def _on_add(self):
        sym = self.add_input.text().strip().upper()
        if sym:
            self.add_symbol.emit(sym, self.current_list)
            self.add_input.clear()

    def _on_selection(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.notes_label.setText("Notes — select a symbol")
            self.notes_edit.clear()
            self._current_notes_symbol = None
            return

        row = rows[0].row()
        sym = self.table.item(row, 0).text()
        notes = self.table.item(row, 9).text()
        
        self._current_notes_symbol = sym
        self.notes_label.setText(f"Notes for {sym}")
        self.notes_edit.blockSignals(True)
        self.notes_edit.setPlainText(notes)
        self.notes_edit.blockSignals(False)

    def _on_notes_changed(self):
        if not self._current_notes_symbol: return
        notes = self.notes_edit.toPlainText()
        # Find row and update table item silently
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == self._current_notes_symbol:
                self.table.blockSignals(True)
                self.table.item(r, 9).setText(notes)
                self.table.blockSignals(False)
                break
        self.update_item.emit(self.current_list, self._current_notes_symbol, "notes", notes)

    def _on_cell_edit(self, row, col):
        if col == 9: # Notes column
            sym = self.table.item(row, 0).text()
            notes = self.table.item(row, 9).text()
            if sym == self._current_notes_symbol:
                self.notes_edit.blockSignals(True)
                self.notes_edit.setPlainText(notes)
                self.notes_edit.blockSignals(False)
            self.update_item.emit(self.current_list, sym, "notes", notes)

    def _on_save(self):
        """Visual confirmation that changes are committed."""
        self.btn_save_changes.setText("Saved!")
        self.btn_save_changes.setEnabled(False)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: (self.btn_save_changes.setText("Save"), self.btn_save_changes.setEnabled(True)))

    def _on_double_click(self, index):
        if index.column() == 0:
            sym = self.table.item(index.row(), 0).text()
            self.open_deep_dive.emit(sym)

    def _context_menu(self, pos):
        menu = QMenu()
        rows = self.table.selectionModel().selectedRows()
        if not rows: return

        sym = self.table.item(rows[0].row(), 0).text()
        
        dive = menu.addAction("🔍 Deep Dive")
        dive.triggered.connect(lambda: self.open_deep_dive.emit(sym))
        
        if self.current_type == "User":
            rem = menu.addAction("❌ Remove from List")
            rem.triggered.connect(lambda: self.remove_symbol.emit(sym, self.current_list))
        
        menu.addSeparator()
        copy = menu.addAction("📋 Copy Symbol")
        copy.triggered.connect(lambda: QApplication.clipboard().setText(sym))
        
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    # --- Import / Export ---
    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Watchlist", f"{self.current_list}.csv", "CSV (*.csv)")
        if not path: return
        try:
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
                writer.writerow(headers)
                for r in range(self.table.rowCount()):
                    row_data = [self.table.item(r, c).text() for c in range(self.table.columnCount())]
                    writer.writerow(row_data)
            QMessageBox.information(self, "Export Success", f"Watchlist exported to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _import_csv(self):
        if self.current_type != "User":
            QMessageBox.warning(self, "Import Denied", "Cannot import into a Preset list. Create a User list first.")
            return

        path, _ = QFileDialog.getOpenFileName(self, "Import Watchlist", "", "CSV (*.csv)")
        if not path: return
        
        try:
            symbols = []
            with open(path, 'r') as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                if not headers: return
                
                # Try to find symbol column
                sym_idx = 0
                for i, h in enumerate(headers):
                    if "symbol" in h.lower() or "ticker" in h.lower():
                        sym_idx = i
                        break
                
                for row in reader:
                    if len(row) > sym_idx:
                        s = row[sym_idx].strip().upper()
                        if s: symbols.append(s)
            
            if symbols:
                for s in symbols:
                    self.add_symbol.emit(s, self.current_list)
                QMessageBox.information(self, "Import", f"Imported {len(symbols)} symbols.")
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    def _on_nav_double_click(self, item):
        name = item.text()
        type_ = item.data(Qt.UserRole)
        if type_:
            self.double_click_list.emit(name, type_)

