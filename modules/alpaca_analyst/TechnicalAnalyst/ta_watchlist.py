#!/usr/bin/env python3
"""
ta_watchlist.py — Technical Analyst Pro
Watchlist management widget: named lists, add/remove symbols, quick-analyze.
"""

from __future__ import annotations
from typing import List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QComboBox, QLabel, QMenu, QInputDialog,
    QMessageBox, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QColor, QAction


class WatchlistWidget(QWidget):
    """
    Sidebar watchlist panel.
    Emits  symbol_selected(str)  when user double-clicks or clicks Analyze.
    """

    symbol_selected = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._build()
        self._refresh_lists()
        self._refresh_symbols()

    # ── Layout ────────────────────────────────────────────────────────────
    def _build(self):
        vl = QVBoxLayout(self)
        vl.setContentsMargins(4, 4, 4, 4)
        vl.setSpacing(6)

        # Header
        hdr = QLabel("📋 Watchlists")
        hdr.setFont(QFont("Segoe UI", 10, QFont.Bold))
        hdr.setStyleSheet("color:#00b4d8;")
        vl.addWidget(hdr)

        # Watchlist selector
        top = QHBoxLayout()
        self.list_combo = QComboBox()
        self.list_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.list_combo.currentTextChanged.connect(self._refresh_symbols)
        top.addWidget(self.list_combo)

        # New / delete list buttons
        new_btn = QPushButton("+")
        new_btn.setFixedSize(26, 26)
        new_btn.setToolTip("New Watchlist")
        new_btn.clicked.connect(self._new_list)
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(26, 26)
        del_btn.setToolTip("Delete Watchlist")
        del_btn.clicked.connect(self._delete_list)
        del_btn.setObjectName("danger")
        top.addWidget(new_btn); top.addWidget(del_btn)
        vl.addLayout(top)

        # Symbol list
        self.sym_list = QListWidget()
        self.sym_list.setAlternatingRowColors(False)
        self.sym_list.setMinimumHeight(140)
        self.sym_list.setMaximumHeight(260)
        self.sym_list.itemDoubleClicked.connect(self._on_double_click)
        self.sym_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sym_list.customContextMenuRequested.connect(self._context_menu)
        vl.addWidget(self.sym_list)

        # Add symbol row
        add_row = QHBoxLayout()
        self.sym_input = QLineEdit()
        self.sym_input.setPlaceholderText("Symbol (e.g. AAPL)")
        self.sym_input.returnPressed.connect(self._add_symbol)
        self.sym_input.setFixedHeight(28)
        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(46)
        add_btn.setFixedHeight(28)
        add_btn.clicked.connect(self._add_symbol)
        add_row.addWidget(self.sym_input); add_row.addWidget(add_btn)
        vl.addLayout(add_row)

        # Analyze selected button
        self.analyze_btn = QPushButton("▶  Analyze Selected")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.setFixedHeight(30)
        self.analyze_btn.clicked.connect(self._analyze_selected)
        vl.addWidget(self.analyze_btn)

    # ── Refresh ───────────────────────────────────────────────────────────
    def _refresh_lists(self):
        self.list_combo.blockSignals(True)
        self.list_combo.clear()
        for name in self.config.watchlists.keys():
            self.list_combo.addItem(name)
        active = self.config.get("active_watchlist","")
        if active and self.list_combo.findText(active) >= 0:
            self.list_combo.setCurrentText(active)
        self.list_combo.blockSignals(False)

    def _refresh_symbols(self):
        name = self.list_combo.currentText()
        if not name: return
        self.config.set("active_watchlist", name)
        self.sym_list.clear()
        for sym in self.config.watchlists.get(name, []):
            item = QListWidgetItem(f"  {sym}")
            item.setData(Qt.UserRole, sym)
            item.setSizeHint(QSize(0, 28))
            self.sym_list.addItem(item)

    # ── Actions ───────────────────────────────────────────────────────────
    def _add_symbol(self):
        sym  = self.sym_input.text().strip().upper()
        name = self.list_combo.currentText()
        if not sym or not name: return
        self.config.add_to_watchlist(name, sym)
        self.sym_input.clear()
        self._refresh_symbols()

    def _new_list(self):
        text, ok = QInputDialog.getText(self, "New Watchlist", "Watchlist name:")
        if ok and text.strip():
            self.config.add_watchlist(text.strip())
            self._refresh_lists()
            self.list_combo.setCurrentText(text.strip())

    def _delete_list(self):
        name = self.list_combo.currentText()
        if not name: return
        reply = QMessageBox.question(
            self, "Delete Watchlist",
            f"Delete '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.config.remove_watchlist(name)
            self._refresh_lists()
            self._refresh_symbols()

    def _on_double_click(self, item: QListWidgetItem):
        sym = item.data(Qt.UserRole)
        if sym: self.symbol_selected.emit(sym)

    def _analyze_selected(self):
        items = self.sym_list.selectedItems()
        if items:
            sym = items[0].data(Qt.UserRole)
            if sym: self.symbol_selected.emit(sym)

    def _context_menu(self, pos):
        item = self.sym_list.itemAt(pos)
        if not item: return
        sym  = item.data(Qt.UserRole)
        name = self.list_combo.currentText()

        menu = QMenu(self)
        act_analyze = QAction(f"▶  Analyze {sym}", self)
        act_analyze.triggered.connect(lambda: self.symbol_selected.emit(sym))
        act_remove  = QAction(f"✕  Remove {sym}", self)
        act_remove.triggered.connect(lambda: self._remove_symbol(name, sym))
        menu.addAction(act_analyze)
        menu.addSeparator()
        menu.addAction(act_remove)
        menu.exec(self.sym_list.mapToGlobal(pos))

    def _remove_symbol(self, list_name: str, sym: str):
        self.config.remove_from_watchlist(list_name, sym)
        self._refresh_symbols()

    # ── Public ────────────────────────────────────────────────────────────
    def add_symbol_external(self, sym: str):
        """Called from main window to add current symbol to active watchlist."""
        name = self.list_combo.currentText()
        if name:
            self.config.add_to_watchlist(name, sym.upper())
            self._refresh_symbols()

    def current_symbols(self) -> List[str]:
        name = self.list_combo.currentText()
        return self.config.watchlists.get(name, [])
