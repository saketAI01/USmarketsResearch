"""
Paper Trading tab — PySide6 UI for the simulated broker.

Layout
------
* Top: account summary (cash, equity, P&L, total return).
* Left column: order entry form (ticker, side, qty, type, limit/stop).
* Middle: tabbed views — Positions, Open Orders, Trade History, Equity Curve.
* Auto-refresh timer ticks every 15s to pull fresh quotes and fill working
  orders.

Persistent state lives in ``paper_account.json`` in the workspace folder,
so the account survives between sessions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

try:
    import matplotlib  # type: ignore
    matplotlib.use("QtAgg")
    from matplotlib.backends.backend_qtagg import (  # type: ignore
        FigureCanvasQTAgg as FigureCanvas,
    )
    from matplotlib.figure import Figure  # type: ignore
    MATPLOTLIB_AVAILABLE = True
except Exception:  # pragma: no cover
    MATPLOTLIB_AVAILABLE = False

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout, QWidget,
)

from paper_trading import PaperBroker

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


# ---------------------------------------------------------------------------
# Quote-fetch worker (off the UI thread)
# ---------------------------------------------------------------------------
class QuoteWorker(QThread):
    """Pulls quotes for held + watched tickers and updates the broker."""

    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, broker: PaperBroker, watch_tickers: list[str], parent=None) -> None:
        super().__init__(parent)
        self.broker = broker
        self.watch_tickers = watch_tickers

    def run(self) -> None:  # type: ignore[override]
        try:
            tks: set[str] = set(self.broker.account.positions.keys())
            for o in self.broker.account.open_orders:
                tks.add(o.ticker)
            for t in self.watch_tickers:
                if t:
                    tks.add(t.upper())
            if not tks:
                self.done.emit({})
                return
            quotes = self.broker.quotes_batch(sorted(tks))
            self.broker.check_pending_fills(quotes)
            self.broker.mark_to_market(quotes)
            self.done.emit(quotes)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Tab
# ---------------------------------------------------------------------------
class PaperTradingTab(QWidget):
    """The full paper-trading tab."""

    def __init__(self, state_path: str, parent=None) -> None:
        super().__init__(parent)
        self.broker = PaperBroker(state_path)
        self._quote_worker: Optional[QuoteWorker] = None
        self._watch_tickers: list[str] = []
        self._build_ui()
        self._refresh_ui()
        # Auto-refresh
        self._timer = QTimer(self)
        self._timer.setInterval(15_000)
        self._timer.timeout.connect(self._refresh_quotes)
        self._timer.start()
        self._refresh_quotes()  # kick off an immediate refresh

    # =====================================================================
    # UI assembly
    # =====================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        # ---- Account summary -------------------------------------------------
        self.summary_box = QGroupBox("Paper Trading Account")
        sb_layout = QHBoxLayout()
        sb_layout.setSpacing(20)
        self.summary_box.setLayout(sb_layout)

        def kpi(label: str, key: str, color: str = COLOR_TEXT) -> tuple[QLabel, QLabel]:
            wrap = QVBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color:{COLOR_MUTED}; font-size:11px;")
            val = QLabel("—")
            val.setStyleSheet(f"color:{color}; font-weight:700; font-size:18px;")
            wrap.addWidget(lbl); wrap.addWidget(val)
            holder = QWidget(); holder.setLayout(wrap)
            sb_layout.addWidget(holder)
            return lbl, val

        _, self.kpi_cash = kpi("Cash", "cash")
        _, self.kpi_equity = kpi("Total Equity", "equity", COLOR_ACCENT)
        _, self.kpi_positions = kpi("Open Positions", "positions")
        _, self.kpi_unrealized = kpi("Unrealised P&L", "unrealized")
        _, self.kpi_realized = kpi("Realised P&L", "realized")
        _, self.kpi_total_ret = kpi("Total Return", "total_ret", COLOR_ACCENT)

        sb_layout.addStretch(1)

        controls = QVBoxLayout()
        ctl_row = QHBoxLayout()
        self.refresh_btn = QPushButton("↻ Refresh quotes")
        self.refresh_btn.clicked.connect(self._refresh_quotes)
        ctl_row.addWidget(self.refresh_btn)
        self.reset_btn = QPushButton("Reset account…")
        self.reset_btn.clicked.connect(self._reset_account)
        ctl_row.addWidget(self.reset_btn)
        controls.addLayout(ctl_row)
        self.last_refresh_lbl = QLabel("")
        self.last_refresh_lbl.setStyleSheet(f"color:{COLOR_DIM}; font-size:11px;")
        controls.addWidget(self.last_refresh_lbl)
        holder = QWidget(); holder.setLayout(controls)
        sb_layout.addWidget(holder)

        root.addWidget(self.summary_box)

        # ---- Split: order entry on left, tabs on right ----------------------
        split = QSplitter(Qt.Horizontal)

        # Order entry
        order_box = QGroupBox("Place Order")
        of = QFormLayout()
        order_box.setLayout(of)
        self.order_ticker = QLineEdit()
        self.order_ticker.setPlaceholderText("e.g. AAPL")
        of.addRow("Ticker:", self.order_ticker)
        self.order_side = QComboBox()
        self.order_side.addItems(["BUY", "SELL"])
        of.addRow("Side:", self.order_side)
        self.order_type = QComboBox()
        self.order_type.addItems(["MARKET", "LIMIT", "STOP", "STOP_LIMIT"])
        self.order_type.currentTextChanged.connect(self._on_order_type_changed)
        of.addRow("Type:", self.order_type)
        self.order_qty = QSpinBox()
        self.order_qty.setRange(1, 1_000_000)
        self.order_qty.setValue(100)
        of.addRow("Quantity:", self.order_qty)
        self.order_limit = QDoubleSpinBox()
        self.order_limit.setRange(0.0, 1_000_000.0)
        self.order_limit.setDecimals(2); self.order_limit.setPrefix("$ ")
        self.order_limit.setEnabled(False)
        of.addRow("Limit price:", self.order_limit)
        self.order_stop = QDoubleSpinBox()
        self.order_stop.setRange(0.0, 1_000_000.0)
        self.order_stop.setDecimals(2); self.order_stop.setPrefix("$ ")
        self.order_stop.setEnabled(False)
        of.addRow("Stop price:", self.order_stop)
        self.order_note = QLineEdit()
        of.addRow("Note:", self.order_note)
        self.place_btn = QPushButton("Place Order")
        self.place_btn.clicked.connect(self._place_order)
        of.addRow(self.place_btn)

        # Quick quote
        self.quote_label = QLabel("Enter a ticker to see the live quote.")
        self.quote_label.setStyleSheet(f"color:{COLOR_MUTED}; font-size:12px;")
        of.addRow(self.quote_label)
        self.order_ticker.textChanged.connect(self._on_ticker_typed)

        split.addWidget(order_box)

        # Tabs (positions / orders / history / chart)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # Positions
        self.positions_table = QTableWidget(0, 8)
        self.positions_table.setHorizontalHeaderLabels([
            "Ticker", "Qty", "Avg Cost", "Last", "Mkt Value",
            "Unr. P&L $", "Unr. P&L %", "Opened",
        ])
        self.positions_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.positions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.positions_table.setAlternatingRowColors(True)
        self.positions_table.verticalHeader().setVisible(False)
        self.positions_table.horizontalHeader().setStretchLastSection(True)
        self.positions_table.itemDoubleClicked.connect(self._on_position_double_clicked)
        self.tabs.addTab(self.positions_table, "Positions")

        # Open orders
        self.orders_table = QTableWidget(0, 9)
        self.orders_table.setHorizontalHeaderLabels([
            "#", "Ticker", "Side", "Type", "Qty", "Limit", "Stop",
            "Submitted", "Cancel",
        ])
        self.orders_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.orders_table.verticalHeader().setVisible(False)
        self.orders_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.orders_table, "Open Orders")

        # Trade history
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels([
            "Ticker", "Qty", "Entry $", "Exit $", "P&L $", "P&L %", "Closed",
        ])
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.history_table, "Trade History")

        # Equity curve
        chart_widget = QWidget()
        chart_v = QVBoxLayout(chart_widget)
        chart_v.setContentsMargins(0, 0, 0, 0)
        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(8, 4), facecolor=COLOR_BG)
            self.canvas = FigureCanvas(self.figure)
            chart_v.addWidget(self.canvas)
        else:
            chart_v.addWidget(QLabel("matplotlib not installed."))
        self.tabs.addTab(chart_widget, "Equity Curve")

        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        root.addWidget(split, 1)

    def _on_order_type_changed(self, t: str) -> None:
        self.order_limit.setEnabled(t in ("LIMIT", "STOP_LIMIT"))
        self.order_stop.setEnabled(t in ("STOP", "STOP_LIMIT"))

    def _on_ticker_typed(self, txt: str) -> None:
        # Lightweight async check would be nice; for now just hint the user
        sym = txt.strip().upper()
        if not sym:
            self.quote_label.setText("Enter a ticker to see the live quote.")
            return
        self.quote_label.setText(f"Live quote for {sym} will appear after refresh.")

    # =====================================================================
    # Actions
    # =====================================================================
    def _reset_account(self) -> None:
        reply = QMessageBox.question(
            self, "Reset paper-trading account?",
            "This wipes all positions, orders, history, and equity curve, "
            "and starts you fresh with $100,000 cash. Continue?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.broker.reset(
            starting_cash=100_000.0,
            commission_per_trade=self.broker.account.commission_per_trade,
            slippage_bps=self.broker.account.slippage_bps,
        )
        self._refresh_ui()

    def _place_order(self) -> None:
        try:
            order = self.broker.place_order(
                ticker=self.order_ticker.text().strip(),
                side=self.order_side.currentText(),
                quantity=int(self.order_qty.value()),
                order_type=self.order_type.currentText(),
                limit_price=(
                    float(self.order_limit.value())
                    if self.order_type.currentText() in ("LIMIT", "STOP_LIMIT")
                    else None
                ),
                stop_price=(
                    float(self.order_stop.value())
                    if self.order_type.currentText() in ("STOP", "STOP_LIMIT")
                    else None
                ),
                note=self.order_note.text().strip(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Order rejected", str(exc))
            return
        # Quick toast in the quote line
        if order.status == "FILLED":
            self.quote_label.setText(
                f"✓ Filled {order.side} {order.quantity} {order.ticker} @ "
                f"${order.fill_price:,.2f}"
            )
        elif order.status == "REJECTED":
            self.quote_label.setText(
                f"✗ Rejected: {order.note or 'unknown reason'}"
            )
        else:
            self.quote_label.setText(
                f"… {order.order_type} {order.side} {order.quantity} "
                f"{order.ticker} resting on the book"
            )
        self.order_note.clear()
        self._refresh_ui()
        self._refresh_quotes()

    def _on_position_double_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        tk_item = self.positions_table.item(row, 0)
        if not tk_item:
            return
        ticker = tk_item.text()
        pos = self.broker.account.positions.get(ticker)
        if not pos:
            return
        # Pre-fill a SELL order for the full position
        self.order_ticker.setText(ticker)
        self.order_side.setCurrentText("SELL")
        self.order_type.setCurrentText("MARKET")
        self.order_qty.setValue(pos.quantity)

    def _refresh_quotes(self) -> None:
        if self._quote_worker and self._quote_worker.isRunning():
            return
        tickers = [self.order_ticker.text().strip()] if self.order_ticker.text() else []
        self._quote_worker = QuoteWorker(self.broker, tickers, self)
        self._quote_worker.done.connect(self._on_quotes)
        self._quote_worker.failed.connect(self._on_quote_failed)
        self.last_refresh_lbl.setText("Refreshing…")
        self._quote_worker.start()

    def _on_quotes(self, quotes: dict) -> None:
        from datetime import datetime
        self.last_refresh_lbl.setText(
            f"Last refresh: {datetime.now().strftime('%H:%M:%S')}  ·  "
            f"{len(quotes)} symbol(s)"
        )
        sym = self.order_ticker.text().strip().upper()
        if sym and sym in quotes:
            self.quote_label.setText(
                f"<b>{sym}</b> · last <b style='color:{COLOR_TEAL};'>"
                f"${quotes[sym]:,.2f}</b>"
            )
        self._refresh_ui()

    def _on_quote_failed(self, msg: str) -> None:
        self.last_refresh_lbl.setText(f"Refresh failed: {msg}")

    # =====================================================================
    # Rendering
    # =====================================================================
    def _refresh_ui(self) -> None:
        acct = self.broker.account
        self.kpi_cash.setText(f"${acct.cash:,.2f}")
        eq = acct.total_equity()
        self.kpi_equity.setText(f"${eq:,.2f}")
        self.kpi_positions.setText(str(len(acct.positions)))
        unr = acct.total_unrealized()
        self.kpi_unrealized.setText(f"${unr:+,.2f}")
        self.kpi_unrealized.setStyleSheet(
            f"color:{COLOR_GREEN if unr >= 0 else COLOR_RED};"
            f"font-weight:700;font-size:18px;"
        )
        rl = acct.realized_pnl_total
        self.kpi_realized.setText(f"${rl:+,.2f}")
        self.kpi_realized.setStyleSheet(
            f"color:{COLOR_GREEN if rl >= 0 else COLOR_RED};"
            f"font-weight:700;font-size:18px;"
        )
        tr = acct.total_return_pct()
        self.kpi_total_ret.setText(f"{tr:+.2f}%")
        self.kpi_total_ret.setStyleSheet(
            f"color:{COLOR_GREEN if tr >= 0 else COLOR_RED};"
            f"font-weight:700;font-size:18px;"
        )

        # Positions table
        self.positions_table.setRowCount(0)
        for tk, pos in sorted(acct.positions.items()):
            r = self.positions_table.rowCount()
            self.positions_table.insertRow(r)
            tk_item = QTableWidgetItem(tk); tk_item.setForeground(QColor(COLOR_ACCENT))
            self.positions_table.setItem(r, 0, tk_item)
            self.positions_table.setItem(r, 1, QTableWidgetItem(str(pos.quantity)))
            self.positions_table.setItem(r, 2, QTableWidgetItem(f"${pos.avg_cost:,.2f}"))
            self.positions_table.setItem(r, 3, QTableWidgetItem(f"${pos.last_price:,.2f}"))
            self.positions_table.setItem(r, 4, QTableWidgetItem(f"${pos.market_value:,.2f}"))
            pnl_item = QTableWidgetItem(f"${pos.unrealized_pnl:+,.2f}")
            pct_item = QTableWidgetItem(f"{pos.unrealized_pnl_pct:+.2f}%")
            c = COLOR_GREEN if pos.unrealized_pnl >= 0 else COLOR_RED
            pnl_item.setForeground(QColor(c)); pct_item.setForeground(QColor(c))
            self.positions_table.setItem(r, 5, pnl_item)
            self.positions_table.setItem(r, 6, pct_item)
            self.positions_table.setItem(r, 7, QTableWidgetItem(pos.opened_at))
        self.positions_table.resizeColumnsToContents()

        # Open orders
        self.orders_table.setRowCount(0)
        for o in acct.open_orders:
            r = self.orders_table.rowCount()
            self.orders_table.insertRow(r)
            self.orders_table.setItem(r, 0, QTableWidgetItem(str(o.order_id)))
            self.orders_table.setItem(r, 1, QTableWidgetItem(o.ticker))
            self.orders_table.setItem(r, 2, QTableWidgetItem(o.side))
            self.orders_table.setItem(r, 3, QTableWidgetItem(o.order_type))
            self.orders_table.setItem(r, 4, QTableWidgetItem(str(o.quantity)))
            self.orders_table.setItem(r, 5, QTableWidgetItem(
                f"${o.limit_price:,.2f}" if o.limit_price else "—"
            ))
            self.orders_table.setItem(r, 6, QTableWidgetItem(
                f"${o.stop_price:,.2f}" if o.stop_price else "—"
            ))
            self.orders_table.setItem(r, 7, QTableWidgetItem(o.submitted_at))
            btn = QPushButton("Cancel")
            btn.clicked.connect(lambda _, oid=o.order_id: self._cancel(oid))
            self.orders_table.setCellWidget(r, 8, btn)
        self.orders_table.resizeColumnsToContents()

        # Trade history (most recent first)
        self.history_table.setRowCount(0)
        for t in reversed(acct.closed_trades[-200:]):
            r = self.history_table.rowCount()
            self.history_table.insertRow(r)
            tk_item = QTableWidgetItem(t.ticker); tk_item.setForeground(QColor(COLOR_ACCENT))
            self.history_table.setItem(r, 0, tk_item)
            self.history_table.setItem(r, 1, QTableWidgetItem(str(t.quantity)))
            self.history_table.setItem(r, 2, QTableWidgetItem(f"${t.entry_price:,.2f}"))
            self.history_table.setItem(r, 3, QTableWidgetItem(f"${t.exit_price:,.2f}"))
            c = COLOR_GREEN if t.realized_pnl >= 0 else COLOR_RED
            pnl_item = QTableWidgetItem(f"${t.realized_pnl:+,.2f}")
            pct_item = QTableWidgetItem(f"{t.realized_pnl_pct:+.2f}%")
            pnl_item.setForeground(QColor(c)); pct_item.setForeground(QColor(c))
            self.history_table.setItem(r, 4, pnl_item)
            self.history_table.setItem(r, 5, pct_item)
            self.history_table.setItem(r, 6, QTableWidgetItem(t.closed_at))
        self.history_table.resizeColumnsToContents()

        # Equity chart
        if MATPLOTLIB_AVAILABLE and acct.equity_history:
            self.figure.clear()
            self.figure.patch.set_facecolor(COLOR_BG)
            ax = self.figure.add_subplot(111)
            try:
                import pandas as pd  # type: ignore
                xs = [pd.to_datetime(d) for d, _ in acct.equity_history]
                ys = [v for _, v in acct.equity_history]
                ax.plot(xs, ys, color=COLOR_ACCENT, linewidth=1.5, label="Equity")
                ax.axhline(acct.starting_cash, color=COLOR_DIM, linestyle="--",
                           linewidth=0.8, alpha=0.6, label="Starting capital")
            except Exception:
                pass
            ax.set_facecolor(COLOR_SURFACE)
            ax.tick_params(colors=COLOR_MUTED, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(COLOR_BORDER)
            ax.grid(True, alpha=0.15, color=COLOR_BORDER)
            leg = ax.legend(loc="upper left", fontsize=9, frameon=False)
            for t in leg.get_texts():
                t.set_color(COLOR_TEXT)
            self.canvas.draw_idle()

    def _cancel(self, order_id: int) -> None:
        if self.broker.cancel_order(order_id):
            self._refresh_ui()
