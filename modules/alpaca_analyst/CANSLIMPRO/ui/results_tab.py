"""Results tab — sortable table of all CANSLIM candidates with detail panel."""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFileDialog, QComboBox,
    QScrollArea, QFrame, QGroupBox, QProgressBar,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QBrush

from core.canslim_engine import CANSLIMResult
from core.csv_handler import export_results_csv
from ui.styles import component_score_color, score_to_color
from config import COMPONENT_LABELS


class ResultsTab(QWidget):
    results_updated = Signal(list)   # for Report tab

    COLS = ["#", "Ticker", "Company", "Mkt", "Score", "Rating",
            "C", "A", "N", "S", "L", "I", "M", "Buy?", "Quality"]
    KEY_COLS = {"C": 6, "A": 7, "N": 8, "S": 9, "L": 10, "I": 11, "M": 12}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.results: list[CANSLIMResult] = []
        self._build_ui()

    def load_results(self, results: list[CANSLIMResult]):
        self.results = results
        self._populate_table(results)
        self.results_updated.emit(results)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        hdr = QLabel("Results")
        hdr.setStyleSheet("font-size: 20px; font-weight: bold; color: #ebebf0;")
        layout.addWidget(hdr)

        # Toolbar
        toolbar = QHBoxLayout()
        self.count_label = QLabel("No results yet — run the screener first")
        self.count_label.setStyleSheet("color: #8e8e93; font-size: 12px;")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch()

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All ratings", "Exceptional+ / Exceptional",
                                    "Strong", "Above Average"])
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self.filter_combo)

        self.btn_export = QPushButton("Export CSV")
        self.btn_export.clicked.connect(self._export_csv)
        toolbar.addWidget(self.btn_export)
        layout.addLayout(toolbar)

        # Splitter: table left, detail panel right
        splitter = QSplitter(Qt.Horizontal)

        # Table
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 32)   # #
        self.table.setColumnWidth(1, 75)   # ticker
        self.table.setColumnWidth(2, 170)  # company
        self.table.setColumnWidth(3, 40)   # market
        self.table.setColumnWidth(4, 55)   # score
        self.table.setColumnWidth(5, 110)  # rating
        for col in range(6, 13):           # C-M
            self.table.setColumnWidth(col, 38)
        self.table.setColumnWidth(13, 38)  # buy
        self.table.setColumnWidth(14, 65)  # quality
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.currentItemChanged.connect(self._on_row_change)
        splitter.addWidget(self.table)

        # Detail panel
        self.detail_panel = _DetailPanel()
        splitter.addWidget(self.detail_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    # ── Table population ──────────────────────────────────────────────────────

    def _populate_table(self, results: list[CANSLIMResult]):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(results))

        for i, r in enumerate(results):
            sc = int(r.composite_score)
            sc_color = QColor(score_to_color(sc))

            def cell(text, align=Qt.AlignLeft, bold=False, color=None):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(align | Qt.AlignVCenter)
                if bold:
                    f = item.font(); f.setBold(True); item.setFont(f)
                if color:
                    item.setForeground(QBrush(QColor(color)))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                return item

            self.table.setItem(i, 0, cell(str(i + 1), Qt.AlignCenter))
            self.table.setItem(i, 1, cell(r.ticker, bold=True))
            self.table.setItem(i, 2, cell(r.company_name))
            mkt_item = cell(r.market, Qt.AlignCenter,
                            color="#1d9e75" if r.market == "IN" else "#185fa5")
            self.table.setItem(i, 3, mkt_item)

            sc_item = QTableWidgetItem()
            sc_item.setData(Qt.DisplayRole, f"{r.composite_score:.1f}")
            sc_item.setData(Qt.UserRole, r.composite_score)
            sc_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            sc_item.setForeground(QBrush(sc_color))
            f = sc_item.font(); f.setBold(True); sc_item.setFont(f)
            sc_item.setFlags(sc_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 4, sc_item)

            self.table.setItem(i, 5, cell(r.rating, color=score_to_color(sc)))

            for key, col in self.KEY_COLS.items():
                cr = r.components.get(key)
                s  = cr.score if cr else 0
                ci = cell(str(s), Qt.AlignCenter,
                          color=component_score_color(s))
                self.table.setItem(i, col, ci)

            buy_item = cell("✓" if r.buy_candidate else "—", Qt.AlignCenter,
                            color="#1d9e75" if r.buy_candidate else "#555")
            self.table.setItem(i, 13, buy_item)
            self.table.setItem(i, 14, cell(r.data_quality, Qt.AlignCenter))

        self.table.setSortingEnabled(True)
        self.count_label.setText(
            f"{len(results)} candidate{'s' if len(results) != 1 else ''} "
            f"— click a row for full breakdown"
        )

    def _apply_filter(self):
        sel = self.filter_combo.currentText()
        if sel == "All ratings":
            filtered = self.results
        elif "Exceptional" in sel:
            filtered = [r for r in self.results if r.rating in ("Exceptional+", "Exceptional")]
        elif sel == "Strong":
            filtered = [r for r in self.results if r.rating == "Strong"]
        else:
            filtered = [r for r in self.results if r.rating == "Above Average"]
        self._populate_table(filtered)

    def _on_row_change(self, current, previous):
        if current is None:
            return
        row = current.row()
        idx_item = self.table.item(row, 0)
        if idx_item is None:
            return
        try:
            idx = int(idx_item.text()) - 1
            if 0 <= idx < len(self.results):
                self.detail_panel.show_result(self.results[idx])
        except (ValueError, IndexError):
            pass

    def _export_csv(self):
        if not self.results:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Results", "canslim_results.csv", "CSV Files (*.csv)"
        )
        if path:
            export_results_csv(self.results, path)


# ── Detail panel ──────────────────────────────────────────────────────────────

class _DetailPanel(QScrollArea):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("border: none; background: #2c2c2e;")
        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        self._layout.setSpacing(8)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self.setWidget(self._inner)
        placeholder = QLabel("Select a stock to see breakdown")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("color: #555; font-size: 13px;")
        self._layout.addWidget(placeholder)

    def show_result(self, r: CANSLIMResult):
        # Clear
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Ticker + score header
        hdr_row = QHBoxLayout()
        ticker_lbl = QLabel(f"<b>{r.ticker}</b>")
        ticker_lbl.setStyleSheet("font-size: 18px; color: #ebebf0;")
        hdr_row.addWidget(ticker_lbl)
        hdr_row.addStretch()
        score_lbl = QLabel(f"<span style='color:{score_to_color(r.composite_score)};font-size:22px;font-weight:bold;'>{r.composite_score:.1f}</span>")
        score_lbl.setTextFormat(Qt.RichText)
        hdr_row.addWidget(score_lbl)
        self._layout.addLayout(hdr_row)

        name_lbl = QLabel(r.company_name)
        name_lbl.setStyleSheet("color: #8e8e93; font-size: 11px;")
        self._layout.addWidget(name_lbl)

        # Rating badge row
        badge_row = QHBoxLayout()
        rating_lbl = QLabel(r.rating)
        rating_lbl.setStyleSheet(
            f"background: {_rating_bg(r.rating)}; color: {score_to_color(r.composite_score)};"
            f"border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: bold;"
        )
        badge_row.addWidget(rating_lbl)
        if r.buy_candidate:
            buy_lbl = QLabel("Buy Candidate")
            buy_lbl.setStyleSheet(
                "background: #0f3d2e; color: #1d9e75; border-radius: 10px;"
                "padding: 2px 10px; font-size: 11px; font-weight: bold;"
            )
            badge_row.addWidget(buy_lbl)
        badge_row.addStretch()
        self._layout.addLayout(badge_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #3a3a3c;")
        self._layout.addWidget(sep)

        # Component bars
        comp_lbl = QLabel("COMPONENTS")
        comp_lbl.setObjectName("sectionHeader")
        self._layout.addWidget(comp_lbl)

        for key in ["C", "A", "N", "S", "L", "I", "M"]:
            cr = r.components.get(key)
            if not cr:
                continue
            self._layout.addWidget(_ComponentRow(cr))

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #3a3a3c;")
        self._layout.addWidget(sep2)

        # Data quality
        if r.errors:
            err_lbl = QLabel("DATA WARNINGS")
            err_lbl.setObjectName("sectionHeader")
            self._layout.addWidget(err_lbl)
            for e in r.errors:
                el = QLabel(f"⚠ {e}")
                el.setStyleSheet("color: #ff6b6b; font-size: 11px;")
                el.setWordWrap(True)
                self._layout.addWidget(el)

        self._layout.addStretch()


class _ComponentRow(QWidget):
    def __init__(self, cr, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(3)
        layout.setContentsMargins(0, 4, 0, 4)

        color = component_score_color(cr.score)

        # Header row: key + label + score
        top_row = QHBoxLayout()
        key_lbl = QLabel(f"<b style='color:{color};font-size:13px;'>{cr.key}</b>")
        key_lbl.setTextFormat(Qt.RichText)
        key_lbl.setFixedWidth(18)
        top_row.addWidget(key_lbl)
        lbl = QLabel(f"<span style='color:#ebebf0;font-size:11px;'>{cr.label}</span>"
                     f"<span style='color:#555;font-size:10px;'> {int(cr.weight*100)}%</span>")
        lbl.setTextFormat(Qt.RichText)
        top_row.addWidget(lbl)
        top_row.addStretch()
        score_lbl = QLabel(f"<b style='color:{color};font-size:13px;'>{cr.score}</b>")
        score_lbl.setTextFormat(Qt.RichText)
        top_row.addWidget(score_lbl)
        layout.addLayout(top_row)

        # Progress bar
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(cr.score)
        bar.setFixedHeight(5)
        bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar {{ background: #3a3a3c; border-radius: 2px; border: none; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}"
        )
        layout.addWidget(bar)

        # Metric
        metric_lbl = QLabel(cr.key_metric)
        metric_lbl.setStyleSheet(f"color: {color}; font-family: Courier; font-size: 10px;")
        layout.addWidget(metric_lbl)

        # Rationale
        rat_lbl = QLabel(cr.rationale)
        rat_lbl.setStyleSheet("color: #8e8e93; font-size: 10px;")
        rat_lbl.setWordWrap(True)
        layout.addWidget(rat_lbl)


def _rating_bg(rating: str) -> str:
    m = {
        "Exceptional+": "#3d2e00",
        "Exceptional":  "#0f3d2e",
        "Strong":       "#0c2442",
        "Above Average":"#1e1a3d",
    }
    return m.get(rating, "#2c2c2e")
