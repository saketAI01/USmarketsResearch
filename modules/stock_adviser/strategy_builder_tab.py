"""
Strategy Builder tab — vault + editor + fetch.

Three columns:

  ┌───────────────────────┬──────────────────────────────┬──────────────────────┐
  │ Vault                 │ Editor                        │ Reference            │
  │ ─────                 │ ──────                        │ ─────────            │
  │ [Search] [chips]      │ Name / Category               │ Indicators table     │
  │                       │ Description                   │ Operators table      │
  │  ★ Popular            │ BUY conditions                │ Example buttons      │
  │    • RSI Bounce…      │ SELL conditions               │                      │
  │    • MACD Cross…      │ [Validate] [Save] [Delete]    │                      │
  │  ↻ Imported           │                               │                      │
  │  ✎ Custom             │                               │                      │
  └───────────────────────┴──────────────────────────────┴──────────────────────┘

Below the columns: the fetch URL bar and the "Fetch from web" button.

Emits ``strategies_changed`` whenever the vault is mutated so the main
window can refresh the Backtest and Screener dropdowns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTextBrowser,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from strategy_dsl import (
    INDICATOR_REFERENCE, OPERATOR_REFERENCE, parse, preprocess, validate,
)
from strategy_vault import StrategyVault, DEFAULT_FETCH_URL


COLOR_BG = "#1e1e2e"
COLOR_SURFACE = "#181825"
COLOR_PANEL = "#313244"
COLOR_TEXT = "#cdd6f4"
COLOR_MUTED = "#a6adc8"
COLOR_DIM = "#7f849c"
COLOR_BORDER = "#45475a"
COLOR_ACCENT = "#89b4fa"
COLOR_GREEN = "#a6e3a1"
COLOR_RED = "#f38ba8"
COLOR_AMBER = "#f9e2af"
COLOR_ORANGE = "#fab387"
COLOR_PURPLE = "#cba6f7"
COLOR_TEAL = "#94e2d5"


# ===========================================================================
# Fetch worker — runs the network request off the UI thread
# ===========================================================================
class FetchWorker(QThread):
    finished_ok = Signal(int, str)         # n_added, msg
    failed = Signal(str)                    # msg

    def __init__(self, vault: StrategyVault, url: str, parent=None) -> None:
        super().__init__(parent)
        self.vault = vault
        self.url = url

    def run(self) -> None:  # type: ignore[override]
        ok, msg, n = self.vault.fetch_from_web(self.url)
        if ok:
            self.finished_ok.emit(n, msg)
        else:
            self.failed.emit(msg)


# ===========================================================================
# Strategy Builder Tab
# ===========================================================================
class StrategyBuilderTab(QWidget):
    """The full Strategy Builder + Vault tab."""

    strategies_changed = Signal()

    def __init__(self, base_dir: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.vault = StrategyVault(base_dir=self.base_dir)
        self._current_id: Optional[str] = None
        self._dirty = False
        self._show_sources: set[str] = {"popular", "custom", "imported"}
        self._search_filter: str = ""
        self._fetch_worker: Optional[FetchWorker] = None
        self._build_ui()
        self._refresh_list()
        self._clear_editor()

    # =====================================================================
    # UI assembly
    # =====================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        # ---- Top splitter: 3 columns -------------------------------------
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)

        # ---- Column 1: vault --------------------------------------------
        vault_col = QWidget()
        vc_layout = QVBoxLayout(vault_col)
        vc_layout.setContentsMargins(0, 0, 0, 0)
        vc_layout.setSpacing(6)

        title = QLabel("Vault")
        title.setStyleSheet(f"color:{COLOR_ACCENT}; font-weight:700; font-size:14px;")
        vc_layout.addWidget(title)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by name, tag, or category…")
        self.search_edit.textChanged.connect(self._on_search_changed)
        vc_layout.addWidget(self.search_edit)

        # Source chips
        chips_row = QHBoxLayout()
        chips_row.setSpacing(4)
        self._chip_buttons: dict[str, QPushButton] = {}
        for src, label, color in [
            ("popular", "★ Popular", COLOR_AMBER),
            ("custom", "✎ Custom", COLOR_PURPLE),
            ("imported", "↻ Imported", COLOR_TEAL),
        ]:
            btn = QPushButton(label)
            btn.setCheckable(True); btn.setChecked(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._chip_css(color))
            btn.toggled.connect(self._on_chip_toggled)
            self._chip_buttons[src] = btn
            chips_row.addWidget(btn)
        chips_row.addStretch(1)
        vc_layout.addLayout(chips_row)

        # Strategy tree
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemClicked.connect(self._on_tree_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        vc_layout.addWidget(self.tree, 1)

        # New + Refresh buttons
        btn_row = QHBoxLayout()
        self.new_btn = QPushButton("＋ New")
        self.new_btn.clicked.connect(self._on_new)
        btn_row.addWidget(self.new_btn)
        self.refresh_btn = QPushButton("↻ Reload")
        self.refresh_btn.setToolTip("Re-read vault files from disk")
        self.refresh_btn.clicked.connect(self._on_reload)
        btn_row.addWidget(self.refresh_btn)
        vc_layout.addLayout(btn_row)

        splitter.addWidget(vault_col)

        # ---- Column 2: editor --------------------------------------------
        editor_col = QWidget()
        ec = QVBoxLayout(editor_col)
        ec.setContentsMargins(0, 0, 0, 0)
        ec.setSpacing(6)

        ed_title = QLabel("Editor")
        ed_title.setStyleSheet(f"color:{COLOR_ACCENT}; font-weight:700; font-size:14px;")
        ec.addWidget(ed_title)

        meta_form = QFormLayout()
        self.name_edit = QLineEdit(); self.name_edit.textChanged.connect(self._mark_dirty)
        meta_form.addRow("Name:", self.name_edit)
        self.category_edit = QLineEdit(); self.category_edit.textChanged.connect(self._mark_dirty)
        self.category_edit.setPlaceholderText("e.g. Momentum, Mean Reversion, Quality…")
        meta_form.addRow("Category:", self.category_edit)
        self.author_edit = QLineEdit(); self.author_edit.textChanged.connect(self._mark_dirty)
        meta_form.addRow("Author:", self.author_edit)
        self.tags_edit = QLineEdit(); self.tags_edit.textChanged.connect(self._mark_dirty)
        self.tags_edit.setPlaceholderText("comma-separated, e.g. RSI, momentum, short-term")
        meta_form.addRow("Tags:", self.tags_edit)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setMaximumHeight(60)
        self.desc_edit.textChanged.connect(self._mark_dirty)
        meta_form.addRow("Description:", self.desc_edit)
        ec.addLayout(meta_form)

        # BUY/SELL editors
        rules_title = QLabel("Rules")
        rules_title.setStyleSheet(f"color:{COLOR_TEAL}; font-weight:700; font-size:13px;")
        ec.addWidget(rules_title)

        buy_label = QLabel("<b>BUY IF</b>  (entry condition)")
        buy_label.setTextFormat(Qt.RichText)
        buy_label.setStyleSheet(f"color:{COLOR_GREEN}; font-size:12px;")
        ec.addWidget(buy_label)
        self.buy_edit = self._make_code_editor()
        self.buy_edit.setPlaceholderText(
            "e.g. RSI14 CROSSOVER 50 AND VOLUME > 1.5 * AVG_VOLUME"
        )
        ec.addWidget(self.buy_edit)

        sell_label = QLabel("<b>SELL IF</b>  (exit condition)")
        sell_label.setTextFormat(Qt.RichText)
        sell_label.setStyleSheet(f"color:{COLOR_RED}; font-size:12px;")
        ec.addWidget(sell_label)
        self.sell_edit = self._make_code_editor()
        self.sell_edit.setPlaceholderText(
            "e.g. RSI14 > 75 OR RSI14 CROSSBELOW 50"
        )
        ec.addWidget(self.sell_edit)

        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet(f"color:{COLOR_MUTED}; font-size:12px;")
        ec.addWidget(self.status_label)

        # Editor buttons
        ed_btn_row = QHBoxLayout()
        self.validate_btn = QPushButton("✓ Validate")
        self.validate_btn.clicked.connect(self._on_validate)
        ed_btn_row.addWidget(self.validate_btn)
        self.save_btn = QPushButton("💾 Save")
        self.save_btn.clicked.connect(self._on_save)
        ed_btn_row.addWidget(self.save_btn)
        self.save_as_btn = QPushButton("Save As…")
        self.save_as_btn.clicked.connect(self._on_save_as)
        ed_btn_row.addWidget(self.save_as_btn)
        self.duplicate_btn = QPushButton("Duplicate")
        self.duplicate_btn.clicked.connect(self._on_duplicate)
        ed_btn_row.addWidget(self.duplicate_btn)
        self.delete_btn = QPushButton("🗑 Delete")
        self.delete_btn.clicked.connect(self._on_delete)
        ed_btn_row.addWidget(self.delete_btn)
        ed_btn_row.addStretch(1)
        ec.addLayout(ed_btn_row)

        splitter.addWidget(editor_col)

        # ---- Column 3: reference ----------------------------------------
        ref_col = QWidget()
        rc = QVBoxLayout(ref_col)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.setSpacing(6)

        rc_title = QLabel("Reference")
        rc_title.setStyleSheet(f"color:{COLOR_ACCENT}; font-weight:700; font-size:14px;")
        rc.addWidget(rc_title)

        self.ref_browser = QTextBrowser()
        self.ref_browser.setOpenExternalLinks(False)
        self.ref_browser.anchorClicked.connect(self._on_ref_anchor)
        self._render_reference()
        rc.addWidget(self.ref_browser, 1)

        # Example insert buttons
        ex_label = QLabel("Examples")
        ex_label.setStyleSheet(f"color:{COLOR_TEAL}; font-weight:700; font-size:12px;")
        rc.addWidget(ex_label)
        ex_row = QHBoxLayout(); ex_row.setSpacing(4)
        for label, buy, sell in [
            (
                "User example",
                "ROE>15 AND LTP>1.01*EMA9 AND %CHG>2 AND VOLUME>1.5*AVG_VOLUME AND RSI14 CROSSOVER 50 OR RSI14 >55",
                "%CHG>7 AND RSI14>75 OR RSI CROSSBELOW 50",
            ),
            (
                "MACD cross",
                "MACD CROSSOVER SIGNAL AND LTP > SMA50",
                "MACD CROSSBELOW SIGNAL",
            ),
            (
                "Breakout",
                "CLOSE > HIGH20_PREV AND VOLUME > 1.5 * AVG_VOLUME",
                "CLOSE < SMA20",
            ),
        ]:
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, B=buy, S=sell, L=label: self._insert_example(L, B, S))
            ex_row.addWidget(b)
        ex_row.addStretch(1)
        rc.addLayout(ex_row)

        splitter.addWidget(ref_col)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([320, 520, 420])
        root.addWidget(splitter, 1)

        # ---- Bottom row: fetch from web ----------------------------------
        fetch_box = QGroupBox("Fetch popular strategies from the web")
        fb = QHBoxLayout(); fetch_box.setLayout(fb)
        self.fetch_url_edit = QLineEdit(DEFAULT_FETCH_URL)
        self.fetch_url_edit.setToolTip(
            "Any HTTPS URL serving a JSON of the same shape as "
            "popular_strategies.json. Imported entries are stored in "
            "imported_strategies.json and merged into the vault."
        )
        fb.addWidget(self.fetch_url_edit, 1)
        self.fetch_btn = QPushButton("↓ Fetch")
        self.fetch_btn.clicked.connect(self._on_fetch)
        fb.addWidget(self.fetch_btn)
        self.fetch_status = QLabel("")
        self.fetch_status.setStyleSheet(f"color:{COLOR_DIM}; font-size:11px;")
        fb.addWidget(self.fetch_status, 2)
        root.addWidget(fetch_box)

    # =====================================================================
    # Helpers
    # =====================================================================
    @staticmethod
    def _chip_css(color: str) -> str:
        return (
            "QPushButton {"
            f"  background: transparent; color: {color};"
            f"  border: 1.5px solid {color};"
            "  border-radius: 12px; padding: 3px 10px;"
            "  font-weight: 600; font-size: 11px;"
            "}"
            "QPushButton:checked {"
            f"  background: {color}; color: {COLOR_BG};"
            "}"
        )

    def _make_code_editor(self) -> QPlainTextEdit:
        ed = QPlainTextEdit()
        f = QFont("Consolas")
        if not f.exactMatch():
            f = QFont("Menlo")
        if not f.exactMatch():
            f = QFont("Courier New")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(11)
        ed.setFont(f)
        # 4 lines minimum, 8 max — keep the column compact
        fm = QFontMetrics(f)
        ed.setMinimumHeight(fm.lineSpacing() * 4 + 12)
        ed.setMaximumHeight(fm.lineSpacing() * 8 + 12)
        ed.setStyleSheet(
            f"QPlainTextEdit {{ background:{COLOR_BG}; color:{COLOR_TEXT}; "
            f"border:1px solid {COLOR_BORDER}; border-radius:4px; padding:6px; }}"
        )
        ed.setTabChangesFocus(True)
        ed.textChanged.connect(self._mark_dirty)
        return ed

    def _mark_dirty(self, *a: Any) -> None:
        if not self._dirty:
            self._dirty = True
            self.status_label.setText("● Unsaved changes")
            self.status_label.setStyleSheet(f"color:{COLOR_AMBER}; font-size:12px;")

    def _render_reference(self) -> None:
        # Build a compact HTML reference table
        html = [f"<body style='background:{COLOR_SURFACE}; color:{COLOR_TEXT}; "
                f"font-family:Segoe UI,Arial; padding:6px;'>"]
        # Operators first — short and useful
        html.append(f"<h3 style='color:{COLOR_TEAL}; margin:6px 0;'>Operators</h3>")
        html.append(f"<table style='border-collapse:collapse; font-size:12px;'>")
        for op, doc in OPERATOR_REFERENCE:
            html.append(
                f"<tr><td style='padding:3px 8px; color:{COLOR_AMBER}; "
                f"font-family:Consolas,monospace; white-space:nowrap;'>{op}</td>"
                f"<td style='padding:3px 8px; color:{COLOR_TEXT};'>{doc}</td></tr>"
            )
        html.append("</table>")
        # Indicators by category
        for cat, items in INDICATOR_REFERENCE:
            html.append(f"<h3 style='color:{COLOR_TEAL}; margin:10px 0 4px 0;'>{cat}</h3>")
            html.append(f"<table style='border-collapse:collapse; font-size:12px;'>")
            for name, doc in items:
                # Make the identifier a clickable anchor that inserts it
                token = name.split()[0]
                html.append(
                    f"<tr><td style='padding:3px 8px; white-space:nowrap;'>"
                    f"<a href='insert:{token}' style='color:{COLOR_ACCENT}; "
                    f"font-family:Consolas,monospace; text-decoration:none;'>{name}</a>"
                    f"</td>"
                    f"<td style='padding:3px 8px; color:{COLOR_MUTED};'>{doc}</td></tr>"
                )
            html.append("</table>")
        html.append("</body>")
        self.ref_browser.setHtml("".join(html))

    def _on_ref_anchor(self, url) -> None:
        """When the user clicks an indicator name in the reference panel,
        insert it at the cursor of whichever rule editor has focus."""
        s = url.toString() if hasattr(url, "toString") else str(url)
        if not s.startswith("insert:"):
            return
        token = s.split(":", 1)[1]
        target = self.buy_edit if self.buy_edit.hasFocus() else (
            self.sell_edit if self.sell_edit.hasFocus() else self.buy_edit
        )
        target.insertPlainText(token + " ")
        target.setFocus()

    # =====================================================================
    # List / filter
    # =====================================================================
    def _on_search_changed(self, txt: str) -> None:
        self._search_filter = txt.strip().lower()
        self._refresh_list()

    def _on_chip_toggled(self, _checked: bool) -> None:
        self._show_sources = {
            src for src, btn in self._chip_buttons.items() if btn.isChecked()
        }
        self._refresh_list()

    def _strategy_matches_filter(self, s: dict) -> bool:
        if s.get("source") not in self._show_sources:
            return False
        if not self._search_filter:
            return True
        q = self._search_filter
        hay = " ".join([
            s.get("name", ""), s.get("category", ""),
            " ".join(s.get("tags", [])), s.get("description", ""),
            s.get("author", ""),
        ]).lower()
        return q in hay

    def _refresh_list(self) -> None:
        self.tree.clear()
        # Group: source → category → strategies
        groups: dict[str, dict[str, list[dict]]] = {
            "popular": {},
            "custom": {},
            "imported": {},
        }
        for s in self.vault.all():
            if not self._strategy_matches_filter(s):
                continue
            src = s.get("source", "custom")
            cat = s.get("category", "Uncategorised") or "Uncategorised"
            groups.setdefault(src, {}).setdefault(cat, []).append(s)

        for src, label, color in [
            ("popular", "★ Popular",   COLOR_AMBER),
            ("custom",  "✎ Custom",    COLOR_PURPLE),
            ("imported", "↻ Imported", COLOR_TEAL),
        ]:
            cats = groups.get(src, {})
            total = sum(len(v) for v in cats.values())
            if total == 0:
                continue
            top = QTreeWidgetItem([f"{label}  ({total})"])
            f = top.font(0); f.setBold(True); top.setFont(0, f)
            top.setForeground(0, QColor(color))
            for cat, items in sorted(cats.items()):
                cat_item = QTreeWidgetItem([f"{cat}  ({len(items)})"])
                cat_item.setForeground(0, QColor(COLOR_MUTED))
                for s in sorted(items, key=lambda x: x.get("name", "").lower()):
                    leaf = QTreeWidgetItem([s["name"]])
                    leaf.setData(0, Qt.UserRole, s["id"])
                    cat_item.addChild(leaf)
                top.addChild(cat_item)
            self.tree.addTopLevelItem(top)
        self.tree.expandAll()

    def _on_tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        sid = item.data(0, Qt.UserRole)
        if not sid:
            return
        if self._dirty and self._current_id != sid:
            reply = QMessageBox.question(
                self, "Discard changes?",
                "You have unsaved edits. Discard and load the new strategy?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                # Reselect the current item if any
                return
        s = self.vault.by_id(sid)
        if s is None:
            return
        self._load_into_editor(s)

    def _on_tree_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        sid = item.data(0, Qt.UserRole)
        if not sid:
            return
        s = self.vault.by_id(sid)
        if s is None:
            return
        menu = QMenu(self.tree)
        act_open = menu.addAction("Open in editor")
        menu.addSeparator()
        act_dup = menu.addAction("Duplicate to Custom…")
        if s.get("source") == "custom":
            menu.addSeparator()
            act_delete = menu.addAction("Delete")
        else:
            act_delete = None
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self._load_into_editor(s)
        elif chosen is act_dup:
            self._duplicate_strategy(s)
        elif act_delete is not None and chosen is act_delete:
            self._delete_strategy(s)

    # =====================================================================
    # Editor state
    # =====================================================================
    def _clear_editor(self) -> None:
        self._current_id = None
        self.name_edit.clear()
        self.category_edit.clear()
        self.author_edit.clear()
        self.tags_edit.clear()
        self.desc_edit.clear()
        self.buy_edit.clear()
        self.sell_edit.clear()
        self._dirty = False
        self.status_label.setText("New strategy — fill in name + rules, then Save.")
        self.status_label.setStyleSheet(f"color:{COLOR_MUTED}; font-size:12px;")
        self._set_editor_locked(False)

    def _load_into_editor(self, s: dict) -> None:
        self._current_id = s.get("id")
        # Block textChanged signals so _dirty doesn't flip
        for w in (self.name_edit, self.category_edit, self.author_edit, self.tags_edit):
            w.blockSignals(True)
        self.desc_edit.blockSignals(True)
        self.buy_edit.blockSignals(True)
        self.sell_edit.blockSignals(True)
        try:
            self.name_edit.setText(s.get("name", ""))
            self.category_edit.setText(s.get("category", ""))
            self.author_edit.setText(s.get("author", ""))
            self.tags_edit.setText(", ".join(s.get("tags", [])))
            self.desc_edit.setPlainText(s.get("description", ""))
            self.buy_edit.setPlainText(s.get("buy", ""))
            self.sell_edit.setPlainText(s.get("sell", ""))
        finally:
            for w in (self.name_edit, self.category_edit, self.author_edit, self.tags_edit):
                w.blockSignals(False)
            self.desc_edit.blockSignals(False)
            self.buy_edit.blockSignals(False)
            self.sell_edit.blockSignals(False)
        self._dirty = False
        src = s.get("source", "custom")
        if src in ("popular", "imported"):
            self._set_editor_locked(True)
            badge = "★ Popular" if src == "popular" else "↻ Imported"
            self.status_label.setText(
                f"Viewing {badge} strategy (read-only). "
                f"Use Duplicate to make a custom copy you can edit."
            )
            self.status_label.setStyleSheet(f"color:{COLOR_AMBER}; font-size:12px;")
        else:
            self._set_editor_locked(False)
            self.status_label.setText("Loaded.")
            self.status_label.setStyleSheet(f"color:{COLOR_MUTED}; font-size:12px;")

    def _set_editor_locked(self, locked: bool) -> None:
        ro = bool(locked)
        for w in (self.name_edit, self.category_edit, self.author_edit, self.tags_edit):
            w.setReadOnly(ro)
        self.desc_edit.setReadOnly(ro)
        self.buy_edit.setReadOnly(ro)
        self.sell_edit.setReadOnly(ro)
        self.save_btn.setEnabled(not ro)
        self.delete_btn.setEnabled(not ro)

    # =====================================================================
    # Editor actions
    # =====================================================================
    def _editor_payload(self) -> dict:
        tags = [
            t.strip() for t in self.tags_edit.text().split(",")
            if t.strip()
        ]
        return {
            "name": self.name_edit.text().strip(),
            "category": self.category_edit.text().strip() or "Uncategorised",
            "author": self.author_edit.text().strip(),
            "tags": tags,
            "description": self.desc_edit.toPlainText().strip(),
            "buy": self.buy_edit.toPlainText().strip(),
            "sell": self.sell_edit.toPlainText().strip(),
        }

    def _on_new(self) -> None:
        if self._dirty:
            reply = QMessageBox.question(
                self, "Discard changes?",
                "You have unsaved edits. Discard and start a new strategy?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._clear_editor()
        self.name_edit.setFocus()

    def _on_reload(self) -> None:
        self.vault.reload()
        self._refresh_list()
        self.status_label.setText("Vault reloaded from disk.")
        self.status_label.setStyleSheet(f"color:{COLOR_MUTED}; font-size:12px;")

    def _on_validate(self) -> None:
        p = self._editor_payload()
        msgs: list[str] = []
        for which in ("buy", "sell"):
            if not p[which]:
                continue
            err = validate(p[which])
            if err:
                msgs.append(f"{which.upper()}: {err}")
            else:
                # Show the preprocessed form back to the user so they see what
                # the parser actually saw (synonyms applied, prefixes stripped)
                msgs.append(f"{which.upper()}: ✓ parses cleanly")
        if not p["buy"] and not p["sell"]:
            self.status_label.setText("Nothing to validate — both rules empty.")
            self.status_label.setStyleSheet(f"color:{COLOR_AMBER}; font-size:12px;")
            return
        bad = any(":" in m and "✓" not in m for m in msgs)
        color = COLOR_RED if bad else COLOR_GREEN
        self.status_label.setText(" · ".join(msgs))
        self.status_label.setStyleSheet(f"color:{color}; font-size:12px;")

    def _on_save(self) -> None:
        p = self._editor_payload()
        if not p["name"]:
            QMessageBox.warning(self, "Need a name", "Give the strategy a name.")
            return
        if not p["buy"] and not p["sell"]:
            QMessageBox.warning(
                self, "Need a rule",
                "At least one of BUY / SELL must contain an expression.",
            )
            return
        for which in ("buy", "sell"):
            if p[which]:
                err = validate(p[which])
                if err:
                    QMessageBox.warning(
                        self, f"{which.upper()} doesn't parse", err,
                    )
                    return
        sid = self._current_id
        # If we have a sid but it belongs to popular/imported, that means
        # the user loaded a read-only entry — saving creates a new custom.
        if sid:
            s = self.vault.by_id(sid)
            if s and s.get("source") != "custom":
                sid = None
        saved = self.vault.save_custom(
            sid=sid, **p,
        )
        self._current_id = saved["id"]
        self._dirty = False
        self.status_label.setText(f"Saved · id={saved['id']}")
        self.status_label.setStyleSheet(f"color:{COLOR_GREEN}; font-size:12px;")
        self._refresh_list()
        self.strategies_changed.emit()

    def _on_save_as(self) -> None:
        p = self._editor_payload()
        name, ok = QInputDialog.getText(
            self, "Save As", "New strategy name:",
            text=p["name"] + " (copy)" if p["name"] else "",
        )
        if not ok or not name.strip():
            return
        p["name"] = name.strip()
        if not p["buy"] and not p["sell"]:
            QMessageBox.warning(self, "Need a rule", "Add at least one rule first.")
            return
        for which in ("buy", "sell"):
            if p[which]:
                err = validate(p[which])
                if err:
                    QMessageBox.warning(self, f"{which.upper()} doesn't parse", err)
                    return
        saved = self.vault.save_custom(sid=None, **p)
        self._current_id = saved["id"]
        self._dirty = False
        self.status_label.setText(f"Saved as new · id={saved['id']}")
        self.status_label.setStyleSheet(f"color:{COLOR_GREEN}; font-size:12px;")
        self._refresh_list()
        self.strategies_changed.emit()

    def _on_duplicate(self) -> None:
        if not self._current_id:
            return
        s = self.vault.by_id(self._current_id)
        if s is None:
            return
        self._duplicate_strategy(s)

    def _duplicate_strategy(self, s: dict) -> None:
        new_name, ok = QInputDialog.getText(
            self, "Duplicate strategy", "New name:",
            text=f"{s['name']} (copy)",
        )
        if not ok or not new_name.strip():
            return
        clone = self.vault.duplicate(s["id"], new_name.strip())
        if clone:
            self._refresh_list()
            self._load_into_editor(clone)
            self.strategies_changed.emit()

    def _on_delete(self) -> None:
        if not self._current_id:
            return
        s = self.vault.by_id(self._current_id)
        if s is None or s.get("source") != "custom":
            return
        self._delete_strategy(s)

    def _delete_strategy(self, s: dict) -> None:
        reply = QMessageBox.question(
            self, "Delete strategy?",
            f"Delete custom strategy '{s['name']}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok = self.vault.delete_custom(s["id"])
        if ok:
            if self._current_id == s["id"]:
                self._clear_editor()
            self._refresh_list()
            self.strategies_changed.emit()
            self.status_label.setText(f"Deleted '{s['name']}'")
            self.status_label.setStyleSheet(f"color:{COLOR_AMBER}; font-size:12px;")

    def _insert_example(self, label: str, buy: str, sell: str) -> None:
        if self._dirty:
            reply = QMessageBox.question(
                self, "Discard changes?",
                "You have unsaved edits. Replace with the example?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._clear_editor()
        self.name_edit.setText(f"Example · {label}")
        self.category_edit.setText("Examples")
        self.buy_edit.setPlainText(buy)
        self.sell_edit.setPlainText(sell)
        self._dirty = True
        self.status_label.setText(
            "Example loaded. Rename & Save to keep it in your Custom bucket."
        )
        self.status_label.setStyleSheet(f"color:{COLOR_TEAL}; font-size:12px;")

    # =====================================================================
    # Fetch from web
    # =====================================================================
    def _on_fetch(self) -> None:
        if self._fetch_worker and self._fetch_worker.isRunning():
            return
        url = self.fetch_url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "No URL", "Enter a URL to fetch strategies from.")
            return
        self.fetch_btn.setEnabled(False)
        self.fetch_status.setText("Fetching…")
        self.fetch_status.setStyleSheet(f"color:{COLOR_AMBER}; font-size:11px;")
        self._fetch_worker = FetchWorker(self.vault, url, self)
        self._fetch_worker.finished_ok.connect(self._on_fetch_ok)
        self._fetch_worker.failed.connect(self._on_fetch_failed)
        self._fetch_worker.start()

    def _on_fetch_ok(self, n: int, msg: str) -> None:
        self.fetch_btn.setEnabled(True)
        self.fetch_status.setText(f"✓ {msg}")
        self.fetch_status.setStyleSheet(f"color:{COLOR_GREEN}; font-size:11px;")
        self.vault.reload()
        self._refresh_list()
        self.strategies_changed.emit()

    def _on_fetch_failed(self, msg: str) -> None:
        self.fetch_btn.setEnabled(True)
        self.fetch_status.setText(f"✗ {msg}")
        self.fetch_status.setStyleSheet(f"color:{COLOR_RED}; font-size:11px;")
