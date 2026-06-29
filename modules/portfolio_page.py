"""
PortfolioPage — full-width table with slim bottom detail bar.

Columns:
  Symbol | Company | Price | %Change | Price Range (Low ▐bar▌ High) |
  QTY (edit) | Cost/Share (edit) | P&L $ (calc) | P&L % (calc, colour-coded)
"""
import csv
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QSize
from PySide6.QtGui import QFont, QColor, QBrush, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QProgressBar, QFrame,
    QPushButton, QMessageBox, QSizePolicy, QAbstractItemView,
    QDoubleSpinBox, QStyledItemDelegate,
)

from modules.stock_evaluate.database import DatabaseManager

# ── Theme ────────────────────────────────────────────────────────────
BG_PRIMARY   = "#0B1628"
BG_SURFACE   = "#12192C"
BG_CARD      = "#162240"
BORDER       = "#1E3050"
ACCENT       = "#00D4FF"   # neon blue
ACCENT2      = "#00F5D4"
SUCCESS      = "#22c55e"
DANGER       = "#ef4444"
WARNING      = "#f59e0b"
SAPPHIRE_GRN = "#1B7A3D"
ANCIENT_GOLD = "#C9A84C"   # ancient gold bar fill
TEXT_PRIMARY = "#E2E8F0"
TEXT_SECONDARY = "#94A3B8"

PORTFOLIO_WL = "US_Portfolio"

# Column indices
COL_SYM  = 0
COL_COMP = 1
COL_PRC  = 2
COL_CHG  = 3
COL_RNG  = 4   # bracketed range bar
COL_QTY  = 5
COL_COST = 6
COL_INV  = 7   # Invested  = QTY × Cost
COL_CUR  = 8   # Cur Value = QTY × Price
COL_PNL  = 9
COL_PNLP = 10

HEADERS = ["Symbol", "Company", "Price", "% Chg",
           "Price Range", "QTY", "Cost/Share",
           "Invested", "Cur Value", "P&L $", "P&L %"]


# ── Bracketed range-bar cell widget ─────────────────────────────────
class RangeBarWidget(QWidget):
    """Shows:  $12.34 ▐██████░░░▌ $15.00"""

    def __init__(self, low, price, high, parent=None):
        super().__init__(parent)
        self.low = low
        self.price = price
        self.high = high

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)   # tighter padding
        lay.setSpacing(3)

        lbl_low = QLabel(f"${low:,.2f}")
        lbl_low.setStyleSheet(
            f"color:#D4B896; font-size:11px; font-weight:600; background:transparent;")
        lbl_low.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl_low.setFixedWidth(58)

        self.bar = QProgressBar()
        self.bar.setMinimum(0)
        self.bar.setMaximum(1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(14)
        self.bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        if high != low:
            norm = int(((price - low) / (high - low)) * 1000)
            norm = max(0, min(1000, norm))
        else:
            norm = 500
        self.bar.setValue(norm)
        self.bar.setStyleSheet(
            f"QProgressBar {{border:1px solid {ACCENT}; border-radius:3px;"
            f"background:{BG_PRIMARY};}}"
            f"QProgressBar::chunk {{background:{ANCIENT_GOLD}; border-radius:2px;}}"
        )
        self.bar.setToolTip(f"Low ${low:,.2f}  •  Price ${price:,.2f}  •  High ${high:,.2f}")

        lbl_high = QLabel(f"${high:,.2f}")
        lbl_high.setStyleSheet(
            f"color:#D4B896; font-size:11px; font-weight:600; background:transparent;")
        lbl_high.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lbl_high.setFixedWidth(58)

        lay.addWidget(lbl_low)
        lay.addWidget(self.bar)
        lay.addWidget(lbl_high)

        self.setStyleSheet(f"background:{BG_SURFACE};")


# ── Spin-box delegate for QTY and Cost columns ───────────────────────
class SpinDelegate(QStyledItemDelegate):
    """Shows a QDoubleSpinBox when editing QTY or Cost cells."""

    def __init__(self, decimals=2, max_val=1_000_000, parent=None):
        super().__init__(parent)
        self.decimals = decimals
        self.max_val = max_val

    def createEditor(self, parent, option, index):
        sb = QDoubleSpinBox(parent)
        sb.setDecimals(self.decimals)
        sb.setMinimum(0)
        sb.setMaximum(self.max_val)
        sb.setButtonSymbols(QDoubleSpinBox.NoButtons)
        sb.setStyleSheet(
            f"QDoubleSpinBox {{background:{BG_CARD}; color:{TEXT_PRIMARY};"
            f"border:1px solid {ACCENT}; padding:2px 4px;}}"
        )
        return sb

    def setEditorData(self, editor, index):
        val = index.data(Qt.UserRole)
        try:
            editor.setValue(float(val))
        except Exception:
            editor.setValue(0.0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.UserRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


# ── Background fetch worker ──────────────────────────────────────────
class PortfolioFetchWorker(QThread):
    progress = Signal(int, str)
    result   = Signal(dict)
    error    = Signal(str)

    def __init__(self, symbols, master_data=None):
        super().__init__()
        self.symbols = symbols
        self.master_data = master_data or {}

    def run(self):
        import yfinance as yf
        results = {}
        total = len(self.symbols)
        for i, sym in enumerate(self.symbols):
            try:
                self.progress.emit(int((i / total) * 100),
                                   f"Fetching {sym} ({i+1}/{total})…")
                ticker = yf.Ticker(sym)
                hist = ticker.history(period="5d", interval="1d",
                                      auto_adjust=True, timeout=10)
                if hist.empty:
                    continue
                today = hist.iloc[-1]
                prev_close = hist['Close'].iloc[-2] if len(hist) >= 2 else today['Close']
                pct = ((today['Close'] - prev_close) / prev_close) * 100 if prev_close else 0
                results[sym] = {
                    "symbol":     sym,
                    "company":    self.master_data.get(sym, ""),
                    "price":      round(float(today['Close']), 2),
                    "pct_change": round(float(pct), 2),
                    "low":        round(float(today['Low']), 2),
                    "high":       round(float(today['High']), 2),
                }
            except Exception as e:
                print(f"PortfolioFetch error {sym}: {e}")
        self.progress.emit(100, "Done")
        self.result.emit(results)


# ── Main Page ────────────────────────────────────────────────────────
class PortfolioPage(QWidget):
    go_explore = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = DatabaseManager()
        self.master_data: dict = {}
        self._fetch_worker = None
        self._market_data: dict = {}   # sym -> live data dict
        self._load_master_csv()
        self._setup_ui()
        QTimer.singleShot(500, self.refresh)

    # ── CSV ──────────────────────────────────────────────────────────
    def _load_master_csv(self):
        path = Path(__file__).resolve().parent.parent / "USStockMaster.csv"
        if not path.exists():
            return
        try:
            with open(str(path), mode="r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    sym = row["Symbol"].upper()
                    self.master_data[sym] = row.get("Company", row.get("Name", ""))
        except Exception:
            pass

    # ── UI ───────────────────────────────────────────────────────────
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ──────────────────────────────────────────────────
        top_bar = QFrame()
        top_bar.setFixedHeight(48)
        top_bar.setStyleSheet(
            f"background:{BG_SURFACE}; border-bottom:1px solid {BORDER};")
        tl = QHBoxLayout(top_bar)
        tl.setContentsMargins(16, 0, 16, 0)

        title = QLabel("Portfolio")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        title.setStyleSheet(f"color:{ACCENT}; border:none;")
        tl.addWidget(title)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color:{TEXT_SECONDARY}; border:none;")
        tl.addWidget(self.status_lbl)
        tl.addStretch()

        self.refresh_btn = QPushButton("⟳  Refresh")
        self.refresh_btn.setFixedHeight(30)
        self.refresh_btn.setStyleSheet(
            f"background:{ACCENT}; color:{BG_PRIMARY}; font-weight:bold;"
            f"padding:0 16px; border-radius:4px; border:none;")
        self.refresh_btn.clicked.connect(self.refresh)
        tl.addWidget(self.refresh_btn)

        root.addWidget(top_bar)

        # ── Thin progress stripe ──────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setMaximumHeight(3)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.progress.setStyleSheet(
            f"QProgressBar{{border:none;background:{BG_SURFACE};}}"
            f"QProgressBar::chunk{{background:qlineargradient("
            f"x1:0,y1:0,x2:1,y2:0,stop:0 {ACCENT},stop:1 {ACCENT2});}}")
        root.addWidget(self.progress)

        # ── Full-width table ─────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked |
                                   QAbstractItemView.SelectedClicked)

        hdr = self.table.horizontalHeader()
        # fixed-width cols; COL_CHG widened to avoid clipping
        for col, w in [(COL_SYM, 80), (COL_CHG, 92), (COL_QTY, 80),
                       (COL_COST, 92), (COL_INV, 96), (COL_CUR, 96),
                       (COL_PNL, 92), (COL_PNLP, 82)]:
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            self.table.setColumnWidth(col, w)
        # company: fixed narrow; range bar + price: stretch/fixed
        hdr.setSectionResizeMode(COL_COMP, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_COMP, 155)
        hdr.setSectionResizeMode(COL_PRC, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_PRC, 82)
        hdr.setSectionResizeMode(COL_RNG, QHeaderView.Stretch)
        hdr.setMinimumSectionSize(30)

        self.table.setStyleSheet(f"""
            QTableWidget {{
                border:none; gridline-color:{BORDER};
                background:{BG_SURFACE}; color:{TEXT_PRIMARY}; font-size:12px;
            }}
            QTableWidget::item {{ padding:4px 6px; }}
            QTableWidget::item:selected {{ background:{BG_CARD}; color:#fff; }}
            QTableWidget::item:alternate {{ background:{BG_PRIMARY}; }}
            QHeaderView::section {{
                background:{BG_CARD}; color:{ACCENT}; padding:6px 4px;
                font-weight:bold; font-size:11px; border:none;
                border-right:1px solid {BORDER}; border-bottom:2px solid {BORDER};
            }}
        """)

        # Delegates for editable columns
        self._qty_delegate  = SpinDelegate(decimals=0, max_val=1_000_000)
        self._cost_delegate = SpinDelegate(decimals=2, max_val=1_000_000)
        self.table.setItemDelegateForColumn(COL_QTY,  self._qty_delegate)
        self.table.setItemDelegateForColumn(COL_COST, self._cost_delegate)

        # Recalculate P&L whenever QTY or Cost is edited
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.doubleClicked.connect(self._on_double_clicked)

        # ── Summary cards row ────────────────────────────────────────
        cards_bar = QFrame()
        cards_bar.setFixedHeight(56)
        cards_bar.setStyleSheet(
            f"background:{BG_CARD}; border-bottom:1px solid {BORDER};")
        cl = QHBoxLayout(cards_bar)
        cl.setContentsMargins(12, 6, 12, 6)
        cl.setSpacing(10)

        def _make_card(label):
            card = QFrame()
            card.setStyleSheet(
                f"background:{BG_SURFACE}; border:1px solid {BORDER};"
                f"border-radius:6px;")
            card.setMinimumWidth(140)
            vl = QVBoxLayout(card)
            vl.setContentsMargins(10, 4, 10, 4)
            vl.setSpacing(1)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"color:{TEXT_SECONDARY}; font-size:9px; font-weight:600;"
                f"letter-spacing:1px; border:none;")
            val = QLabel("—")
            val.setFont(QFont("Segoe UI", 12, QFont.Bold))
            val.setStyleSheet(f"color:{TEXT_PRIMARY}; border:none;")
            vl.addWidget(lbl)
            vl.addWidget(val)
            return card, val

        card1, self.card_invested = _make_card("TOTAL INVESTED")
        card2, self.card_current  = _make_card("TOTAL CURRENT")
        card3, self.card_pnl      = _make_card("TOTAL P&L")
        card4, self.card_pnlp     = _make_card("% P&L")

        for c in (card1, card2, card3, card4):
            cl.addWidget(c)
        cl.addStretch()

        root.addWidget(cards_bar)
        root.addWidget(self.table, 1)

        # ── Slim bottom detail bar ───────────────────────────────────
        self.bottom_bar = QFrame()
        self.bottom_bar.setFixedHeight(34)
        self.bottom_bar.setStyleSheet(
            f"background:{BG_CARD}; border-top:1px solid {BORDER};")
        bl = QHBoxLayout(self.bottom_bar)
        bl.setContentsMargins(12, 0, 12, 0)
        bl.setSpacing(20)

        def _detail_lbl(text="", accent=False):
            l = QLabel(text)
            l.setStyleSheet(
                f"color:{''+ACCENT if accent else TEXT_SECONDARY}; "
                f"font-size:11px; border:none;")
            return l

        self.det_sym     = _detail_lbl("—", accent=True)
        self.det_sym.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.det_company = _detail_lbl()
        self.det_price   = _detail_lbl()
        self.det_change  = _detail_lbl()
        self.det_range   = _detail_lbl()
        self.det_pnl     = _detail_lbl()

        for w in (self.det_sym, self.det_company, self.det_price,
                  self.det_change, self.det_range, self.det_pnl):
            bl.addWidget(w)
        bl.addStretch()

        hint = _detail_lbl("Select a row to view details")
        bl.addWidget(hint)
        self.det_hint = hint

        root.addWidget(self.bottom_bar)

    # ── Data helpers ─────────────────────────────────────────────────
    def _get_portfolio_symbols(self):
        try:
            return [i["symbol"] for i in self.db.get_watchlist_items(PORTFOLIO_WL)]
        except Exception:
            return []

    # ── Refresh ──────────────────────────────────────────────────────
    def refresh(self):
        symbols = self._get_portfolio_symbols()
        if not symbols:
            self.status_lbl.setText(
                "Portfolio empty — copy symbols from the Watchlist tab.")
            self.table.setRowCount(0)
            return
        if self._fetch_worker and self._fetch_worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status_lbl.setText("Fetching market data…")
        self._fetch_worker = PortfolioFetchWorker(symbols, self.master_data)
        self._fetch_worker.progress.connect(self._on_progress)
        self._fetch_worker.result.connect(self._on_result)
        self._fetch_worker.error.connect(
            lambda m: QMessageBox.warning(self, "Error", m))
        self._fetch_worker.finished.connect(self._on_finished)
        self._fetch_worker.start()

    def _on_progress(self, v, msg):
        self.progress.setValue(v)
        self.status_lbl.setText(msg)

    def _on_result(self, results):
        self._market_data = results
        self._populate(results)

    def _on_finished(self):
        self.refresh_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.status_lbl.setText(f"{self.table.rowCount()} symbols loaded")

    # ── Table population ─────────────────────────────────────────────
    def _populate(self, results: dict):
        try:
            self.table.itemChanged.disconnect(self._on_item_changed)
        except RuntimeError:
            pass
        self.table.setSortingEnabled(False)

        # Preserve any QTY / Cost the user already entered
        saved = {}
        for r in range(self.table.rowCount()):
            si = self.table.item(r, COL_SYM)
            qi = self.table.item(r, COL_QTY)
            ci = self.table.item(r, COL_COST)
            if si:
                sym = si.text()
                saved[sym] = {
                    "qty":  qi.data(Qt.UserRole) if qi else 0.0,
                    "cost": ci.data(Qt.UserRole) if ci else 0.0,
                }

        data_list = list(results.values())
        self.table.setRowCount(len(data_list))

        bold = QFont("Segoe UI", 11, QFont.Bold)
        norm = QFont("Segoe UI", 11)

        for row, d in enumerate(data_list):
            sym   = d["symbol"]
            price = d.get("price", 0.0)
            pct   = d.get("pct_change", 0.0)
            low   = d.get("low", 0.0)
            high  = d.get("high", 0.0)
            comp  = d.get("company", "")

            # helper to make a non-editable centred item
            def _ro(text, sort_val=None, font=norm):
                it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignCenter)
                it.setFont(font)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if sort_val is not None:
                    it.setData(Qt.UserRole, sort_val)
                return it

            # Symbol
            si = _ro(sym, font=bold)
            si.setForeground(QBrush(QColor(ACCENT)))
            self.table.setItem(row, COL_SYM, si)

            # Company (left-aligned, stretchable)
            ci_item = QTableWidgetItem(comp)
            ci_item.setFont(norm)
            ci_item.setFlags(ci_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, COL_COMP, ci_item)

            # Price
            pi = _ro(f"${price:,.2f}", price, font=bold)
            self.table.setItem(row, COL_PRC, pi)

            # % Change
            sign = "+" if pct > 0 else ""
            chg_it = _ro(f"{sign}{pct:.2f}%", pct, font=bold)
            chg_it.setForeground(
                QBrush(QColor(SUCCESS if pct > 0 else (DANGER if pct < 0 else TEXT_SECONDARY))))
            self.table.setItem(row, COL_CHG, chg_it)

            # Price Range widget (bracketed bar)
            rng_placeholder = QTableWidgetItem()
            rng_placeholder.setFlags(rng_placeholder.flags() & ~Qt.ItemIsEditable)
            if high != low:
                rng_placeholder.setData(Qt.UserRole, (price - low) / (high - low))
            self.table.setItem(row, COL_RNG, rng_placeholder)
            self.table.setCellWidget(row, COL_RNG,
                                     RangeBarWidget(low, price, high))

            # QTY (editable)
            prev = saved.get(sym, {})
            qty_val  = prev.get("qty", 0.0)
            cost_val = prev.get("cost", 0.0)

            qty_it = QTableWidgetItem()
            qty_it.setTextAlignment(Qt.AlignCenter)
            qty_it.setFont(norm)
            qty_it.setData(Qt.UserRole, float(qty_val))
            qty_it.setData(Qt.DisplayRole, f"{int(qty_val)}" if qty_val == int(qty_val)
                           else f"{qty_val:.2f}")
            qty_it.setBackground(QBrush(QColor(BG_CARD)))
            self.table.setItem(row, COL_QTY, qty_it)

            # Cost/Share (editable)
            cost_it = QTableWidgetItem()
            cost_it.setTextAlignment(Qt.AlignCenter)
            cost_it.setFont(norm)
            cost_it.setData(Qt.UserRole, float(cost_val))
            cost_it.setData(Qt.DisplayRole, f"${cost_val:,.2f}")
            cost_it.setBackground(QBrush(QColor(BG_CARD)))
            self.table.setItem(row, COL_COST, cost_it)

            # P&L & %P&L (calculated)
            self._update_pnl_row(row, price, qty_val, cost_val)

        self.table.setSortingEnabled(True)
        self.table.resizeRowsToContents()
        self.table.itemChanged.connect(self._on_item_changed)
        self._update_summary()

    # ── P&L helpers ──────────────────────────────────────────────────
    def _update_pnl_row(self, row: int, price: float,
                        qty: float, cost: float):
        bold = QFont("Segoe UI", 11, QFont.Bold)

        def _ro_item(text, val, color=None):
            it = QTableWidgetItem(text)
            it.setTextAlignment(Qt.AlignCenter)
            it.setFont(bold)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            it.setData(Qt.UserRole, val)
            if color:
                it.setForeground(QBrush(QColor(color)))
            return it

        if qty > 0 and cost > 0:
            invested   = qty * cost
            cur_value  = qty * price
            pnl_dollar = cur_value - invested
            pnl_pct    = (pnl_dollar / invested) * 100
        else:
            invested = cur_value = pnl_dollar = pnl_pct = 0.0

        pnl_col = SUCCESS if pnl_dollar >= 0 else DANGER

        # Invested
        self.table.setItem(row, COL_INV,
            _ro_item(f"${invested:,.2f}", invested, TEXT_SECONDARY))

        # Current Value
        self.table.setItem(row, COL_CUR,
            _ro_item(f"${cur_value:,.2f}", cur_value, TEXT_PRIMARY))

        # P&L $
        sign = '+' if pnl_dollar >= 0 else ''
        self.table.setItem(row, COL_PNL,
            _ro_item(f"{sign}${pnl_dollar:,.2f}", pnl_dollar, pnl_col))

        # P&L %
        sign = '+' if pnl_pct >= 0 else ''
        self.table.setItem(row, COL_PNLP,
            _ro_item(f"{sign}{pnl_pct:.2f}%", pnl_pct, pnl_col))

    def _on_item_changed(self, item: QTableWidgetItem):
        col = item.column()
        if col not in (COL_QTY, COL_COST):
            return
        row = item.row()
        sym_it  = self.table.item(row, COL_SYM)
        prc_it  = self.table.item(row, COL_PRC)
        qty_it  = self.table.item(row, COL_QTY)
        cost_it = self.table.item(row, COL_COST)
        if not all([sym_it, prc_it, qty_it, cost_it]):
            return

        price = prc_it.data(Qt.UserRole) or 0.0

        qty_raw  = qty_it.data(Qt.UserRole)
        cost_raw = cost_it.data(Qt.UserRole)
        try:
            qty  = float(qty_raw)
            cost = float(cost_raw)
        except (TypeError, ValueError):
            return

        # Update display text neatly
        self.table.blockSignals(True)
        qty_it.setData(Qt.DisplayRole,
                       f"{int(qty)}" if qty == int(qty) else f"{qty:.2f}")
        cost_it.setData(Qt.DisplayRole, f"${cost:,.2f}")
        self._update_pnl_row(row, price, qty, cost)
        self.table.blockSignals(False)
        self._update_summary()

    # ── Summary cards ─────────────────────────────────────────────────
    def _update_summary(self):
        total_inv = total_cur = 0.0
        for r in range(self.table.rowCount()):
            inv_it = self.table.item(r, COL_INV)
            cur_it = self.table.item(r, COL_CUR)
            if inv_it:
                v = inv_it.data(Qt.UserRole)
                if v:
                    total_inv += float(v)
            if cur_it:
                v = cur_it.data(Qt.UserRole)
                if v:
                    total_cur += float(v)

        total_pnl  = total_cur - total_inv
        total_pnlp = (total_pnl / total_inv * 100) if total_inv else 0.0
        pnl_col    = SUCCESS if total_pnl >= 0 else DANGER
        sign       = '+' if total_pnl >= 0 else ''

        self.card_invested.setText(f"${total_inv:,.2f}")
        self.card_invested.setStyleSheet(f"color:{TEXT_PRIMARY}; border:none;")

        self.card_current.setText(f"${total_cur:,.2f}")
        self.card_current.setStyleSheet(f"color:{TEXT_PRIMARY}; border:none;")

        self.card_pnl.setText(f"{sign}${total_pnl:,.2f}")
        self.card_pnl.setStyleSheet(
            f"color:{pnl_col}; font-size:12px; font-weight:bold; border:none;")
        self.card_pnl.setFont(QFont("Segoe UI", 12, QFont.Bold))

        sign2 = '+' if total_pnlp >= 0 else ''
        self.card_pnlp.setText(f"{sign2}{total_pnlp:.2f}%")
        self.card_pnlp.setStyleSheet(
            f"color:{pnl_col}; font-size:12px; font-weight:bold; border:none;")
        self.card_pnlp.setFont(QFont("Segoe UI", 12, QFont.Bold))

    # ── Bottom detail bar update ──────────────────────────────────────
    def _on_row_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()

        def _txt(col):
            it = self.table.item(row, col)
            return it.text() if it else "—"

        def _val(col):
            it = self.table.item(row, col)
            return it.data(Qt.UserRole) if it else 0.0

        sym  = _txt(COL_SYM)
        comp = _txt(COL_COMP)
        prc  = _txt(COL_PRC)
        pct  = _val(COL_CHG)
        sign = "+" if pct >= 0 else ""
        inv  = _txt(COL_INV)
        cur  = _txt(COL_CUR)
        pnl  = _txt(COL_PNL)
        pnlp = _txt(COL_PNLP)

        d = self._market_data.get(sym, {})
        low  = d.get("low",  0.0)
        high = d.get("high", 0.0)

        self.det_sym.setText(sym)
        self.det_company.setText(comp)
        self.det_price.setText(f"Price: {prc}")
        self.det_change.setText(f"{sign}{pct:.2f}%")
        col = SUCCESS if pct >= 0 else DANGER
        self.det_change.setStyleSheet(
            f"color:{col}; font-size:11px; font-weight:bold; border:none;")
        self.det_range.setText(f"Range: ${low:,.2f} – ${high:,.2f}")
        self.det_pnl.setText(f"Invested: {inv}  •  Value: {cur}  •  P&L: {pnl} ({pnlp})")
        pnl_val = _val(COL_PNL)
        pcol = SUCCESS if pnl_val >= 0 else DANGER
        self.det_pnl.setStyleSheet(
            f"color:{pcol}; font-size:11px; font-weight:bold; border:none;")

    def _on_double_clicked(self, idx):
        if idx.column() == COL_SYM:
            it = self.table.item(idx.row(), COL_SYM)
            if it:
                symbol = it.text().strip().upper()
                if symbol:
                    self.go_explore.emit(symbol)
        self.det_hint.setVisible(False)
