"""Report tab — select stocks and generate PDF report."""
from __future__ import annotations
import os
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget,
    QListWidgetItem, QGroupBox, QSpinBox, QProgressBar,
    QMessageBox, QCheckBox,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor

from core.report_generator import generate_report
from core.canslim_engine import CANSLIMResult
from config import load_settings


class _PdfThread(QThread):
    done    = Signal(str)
    error   = Signal(str)

    def __init__(self, results, path, settings, parent=None):
        super().__init__(parent)
        self.results  = results
        self.path     = path
        self.settings = settings

    def run(self):
        try:
            out = generate_report(self.results, self.path, self.settings)
            self.done.emit(out)
        except Exception as e:
            self.error.emit(str(e))


class ReportTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.results: list[CANSLIMResult] = []
        self._thread = None
        self._build_ui()

    def load_results(self, results: list[CANSLIMResult]):
        self.results = results
        self._refresh_list()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 4, 8, 6)



        # Stock selection
        sel_grp = QGroupBox("Stocks to Include")
        sel_layout = QVBoxLayout(sel_grp)

        sel_btn_row = QHBoxLayout()
        self.btn_select_all  = QPushButton("Select All")
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_select_none = QPushButton("Select None")
        self.btn_select_none.clicked.connect(self._select_none)
        self.btn_select_buy  = QPushButton("Buy Candidates Only")
        self.btn_select_buy.clicked.connect(self._select_buy)
        sel_btn_row.addWidget(self.btn_select_all)
        sel_btn_row.addWidget(self.btn_select_none)
        sel_btn_row.addWidget(self.btn_select_buy)
        sel_btn_row.addStretch()
        sel_layout.addLayout(sel_btn_row)

        self.stock_list = QListWidget()
        self.stock_list.setAlternatingRowColors(True)
        self.stock_list.setStyleSheet(
            "background: #2c2c2e; alternate-background-color: #242426;"
        )
        sel_layout.addWidget(self.stock_list)
        layout.addWidget(sel_grp)

        # Output options
        opt_grp = QGroupBox("Output Options")
        opt_layout = QVBoxLayout(opt_grp)
        self.open_after_cb = QCheckBox("Open PDF after generation")
        self.open_after_cb.setChecked(True)
        opt_layout.addWidget(self.open_after_cb)
        layout.addWidget(opt_grp)

        # Generate button
        gen_row = QHBoxLayout()
        self.btn_generate = QPushButton("Generate PDF Report")
        self.btn_generate.setObjectName("primaryBtn")
        self.btn_generate.setFixedHeight(40)
        self.btn_generate.clicked.connect(self._generate)
        gen_row.addWidget(self.btn_generate)
        layout.addLayout(gen_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)   # indeterminate
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #8e8e93; font-size: 12px;")
        layout.addWidget(self.status_label)
        layout.addStretch()

    # ── List management ───────────────────────────────────────────────────────

    def _refresh_list(self):
        self.stock_list.clear()
        if not self.results:
            self.stock_list.addItem("No results yet — run the screener first")
            return
        for r in self.results:
            label = (f"{r.ticker}  ·  {r.composite_score:.1f}  ·  {r.rating}"
                     + ("  ✓ Buy" if r.buy_candidate else ""))
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, r.ticker)
            self.stock_list.addItem(item)

    def _select_all(self):
        for i in range(self.stock_list.count()):
            self.stock_list.item(i).setCheckState(Qt.Checked)

    def _select_none(self):
        for i in range(self.stock_list.count()):
            self.stock_list.item(i).setCheckState(Qt.Unchecked)

    def _select_buy(self):
        buy_tickers = {r.ticker for r in self.results if r.buy_candidate}
        for i in range(self.stock_list.count()):
            item = self.stock_list.item(i)
            ticker = item.data(Qt.UserRole)
            item.setCheckState(Qt.Checked if ticker in buy_tickers else Qt.Unchecked)

    def _selected_results(self) -> list[CANSLIMResult]:
        checked_tickers = set()
        for i in range(self.stock_list.count()):
            item = self.stock_list.item(i)
            if item.checkState() == Qt.Checked:
                ticker = item.data(Qt.UserRole)
                if ticker:
                    checked_tickers.add(ticker)
        return [r for r in self.results if r.ticker in checked_tickers]

    # ── PDF generation ────────────────────────────────────────────────────────

    def _generate(self):
        selected = self._selected_results()
        if not selected:
            QMessageBox.warning(self, "No Stocks Selected",
                                "Select at least one stock to include in the report.")
            return

        date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
        default  = f"canslim_report_{date_str}.pdf"

        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF Report", default, "PDF Files (*.pdf)"
        )
        if not path:
            return

        settings = load_settings()
        settings.update({
            "n_analyzed": len(self.results),
            "min_score":  settings.get("min_score", 60),
            "markets":    "US + IN",
        })

        self.btn_generate.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText(f"Generating report for {len(selected)} stocks…")

        self._thread = _PdfThread(selected, path, settings, parent=self)
        self._thread.done.connect(self._on_done)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _on_done(self, path: str):
        self.btn_generate.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Report saved: {path}")

        if self.open_after_cb.isChecked():
            try:
                os.startfile(path)    # Windows
            except AttributeError:
                import subprocess, sys
                if sys.platform == "darwin":
                    subprocess.call(["open", path])
                else:
                    subprocess.call(["xdg-open", path])

    def _on_error(self, msg: str):
        self.btn_generate.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Error: {msg}")
        QMessageBox.critical(self, "PDF Generation Failed", msg)
