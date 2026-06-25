"""
Dashboard Tab — Market overview with index cards, sector heatmap, top movers.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QColor
from ..theme import ACCENT, ACCENT2, SUCCESS, DANGER, WARNING, BG_CARD, BORDER, TEXT_SECONDARY, BG_PRIMARY


def _card(title, value="—", subtitle="", accent=ACCENT):
    """Create a stat card widget."""
    frame = QFrame()
    frame.setObjectName("card")
    frame.setMinimumHeight(100)
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(4)

    t = QLabel(title)
    t.setFont(QFont("Segoe UI", 9))
    t.setStyleSheet(f"color: {TEXT_SECONDARY};")

    v = QLabel(value)
    v.setObjectName("cardValue")
    v.setFont(QFont("Segoe UI", 20, QFont.Bold))
    v.setStyleSheet(f"color: {accent};")

    s = QLabel(subtitle)
    s.setObjectName("cardSub")
    s.setFont(QFont("Segoe UI", 9))
    s.setStyleSheet(f"color: {TEXT_SECONDARY};")

    lay.addWidget(t)
    lay.addWidget(v)
    lay.addWidget(s)
    frame.setStyleSheet(f"""
        QFrame#card {{
            background: {BG_CARD};
            border: 1px solid {BORDER};
            border-radius: 10px;
            border-top: 3px solid {accent};
        }}
    """)
    return frame, v, s


def _mover_table(title):
    """Create a movers table."""
    table = QTableWidget()
    table.setColumnCount(4)
    table.setHorizontalHeaderLabels(["Symbol", "Price", "Change %", "Volume"])
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    table.setSortingEnabled(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.setMaximumHeight(320)
    return table


class DashboardTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(16, 16, 16, 16)
        main.setSpacing(16)

        # --- Header ---
        hdr = QHBoxLayout()
        title = QLabel("MARKET DASHBOARD")
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT};")
        self.status_lbl = QLabel("Loading...")
        self.status_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.btn_refresh = QPushButton("⟳  Refresh UI")
        self.btn_refresh.setFixedWidth(110)
        self.btn_full_refresh = QPushButton("⚡  Full Refresh")
        self.btn_full_refresh.setFixedWidth(110)
        self.btn_full_refresh.setStyleSheet(f"background-color: {ACCENT2}; color: white;")
        
        from PySide6.QtWidgets import QProgressBar
        self.prog = QProgressBar()
        self.prog.setFixedWidth(200)
        self.prog.setVisible(False)
        self.prog.setStyleSheet(f"""
            QProgressBar {{ border: 1px solid {BORDER}; border-radius: 4px; text-align: center; color: white; background: #0D1117; }}
            QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 3px; }}
        """)
        
        # Fundamentals Tank
        self.tank_frame = QFrame()
        self.tank_frame.setObjectName("topBar")
        self.tank_frame.setFixedWidth(260)
        tl = QVBoxLayout(self.tank_frame)
        tl.setContentsMargins(10, 5, 10, 5)
        tl.setSpacing(2)
        
        self.tank_lbl = QLabel("Fundamentals Tank: 0/0")
        self.tank_lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
        self.tank_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.tank_bar = QProgressBar()
        self.tank_bar.setFixedHeight(12)
        self.tank_bar.setTextVisible(False)
        self.tank_bar.setStyleSheet(f"QProgressBar {{ border: 1px solid {BORDER}; border-radius: 6px; background: {BG_PRIMARY}; }} QProgressBar::chunk {{ background: {SUCCESS}; border-radius: 5px; }}")
        
        tl.addWidget(self.tank_lbl)
        tl.addWidget(self.tank_bar)

        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(self.tank_frame)
        hdr.addSpacing(10)
        hdr.addWidget(self.prog)
        hdr.addWidget(self.status_lbl)
        hdr.addWidget(self.btn_refresh)
        hdr.addWidget(self.btn_full_refresh)
        main.addLayout(hdr)

        # --- Index Cards Row ---
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self.card_sp500, self.val_sp500, self.sub_sp500 = _card("S&P 500", "—", "", ACCENT)
        self.card_nasdaq, self.val_nasdaq, self.sub_nasdaq = _card("NASDAQ", "—", "", "#A371F7")
        self.card_dow, self.val_dow, self.sub_dow = _card("DOW JONES", "—", "", ACCENT2)
        self.card_vix, self.val_vix, self.sub_vix = _card("VIX", "—", "", WARNING)
        cards_row.addWidget(self.card_sp500)
        cards_row.addWidget(self.card_nasdaq)
        cards_row.addWidget(self.card_dow)
        cards_row.addWidget(self.card_vix)
        main.addLayout(cards_row)

        # --- Sector Performance Row ---
        sec_label = QLabel("SECTOR PERFORMANCE")
        sec_label.setFont(QFont("Segoe UI", 12, QFont.Bold))
        sec_label.setStyleSheet(f"color: {ACCENT2};")
        main.addWidget(sec_label)

        self.sector_grid = QGridLayout()
        self.sector_grid.setSpacing(8)
        self.sector_tiles = {}
        main.addLayout(self.sector_grid)

        # --- Movers Row ---
        movers_row = QHBoxLayout()
        movers_row.setSpacing(16)

        # Gainers
        gain_frame = QFrame()
        gain_frame.setObjectName("card")
        gain_lay = QVBoxLayout(gain_frame)
        gain_title = QLabel("▲  TOP GAINERS")
        gain_title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        gain_title.setStyleSheet(f"color: {SUCCESS};")
        self.gainers_table = _mover_table("Gainers")
        gain_lay.addWidget(gain_title)
        gain_lay.addWidget(self.gainers_table)
        movers_row.addWidget(gain_frame)

        # Losers
        lose_frame = QFrame()
        lose_frame.setObjectName("card")
        lose_lay = QVBoxLayout(lose_frame)
        lose_title = QLabel("▼  TOP LOSERS")
        lose_title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        lose_title.setStyleSheet(f"color: {DANGER};")
        self.losers_table = _mover_table("Losers")
        lose_lay.addWidget(lose_title)
        lose_lay.addWidget(self.losers_table)
        movers_row.addWidget(lose_frame)

        main.addLayout(movers_row)
        main.addStretch()

    def update_indices(self, indices):
        mapping = {
            "^GSPC": (self.val_sp500, self.sub_sp500),
            "%5EGSPC": (self.val_sp500, self.sub_sp500),
            "^IXIC": (self.val_nasdaq, self.sub_nasdaq),
            "%5EIXIC": (self.val_nasdaq, self.sub_nasdaq),
            "^DJI": (self.val_dow, self.sub_dow),
            "%5EDJI": (self.val_dow, self.sub_dow),
            "^VIX": (self.val_vix, self.sub_vix),
            "%5EVIX": (self.val_vix, self.sub_vix),
        }
        for sym, data in indices.items():
            key = sym.replace("^", "%5E") if not sym.startswith("%") else sym
            if sym in mapping:
                val_lbl, sub_lbl = mapping[sym]
            elif key in mapping:
                val_lbl, sub_lbl = mapping[key]
            else:
                continue
            price = data.get("price", 0)
            chg = data.get("changePercentage", 0) or 0
            val_lbl.setText(f"{price:,.2f}")
            arrow = "▲" if chg >= 0 else "▼"
            color = SUCCESS if chg >= 0 else DANGER
            sub_lbl.setText(f"{arrow} {chg:+.2f}%")
            sub_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    def update_sectors(self, sectors):
        # Clear existing
        while self.sector_grid.count():
            item = self.sector_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.sector_tiles = {}
        for i, sec in enumerate(sectors):
            name = sec.get("sector", "Unknown")
            chg_str = sec.get("changesPercentage", "0%")
            try:
                chg = float(str(chg_str).replace("%", ""))
            except:
                chg = 0

            tile = QFrame()
            tile.setMinimumSize(140, 60)
            tile.setCursor(Qt.PointingHandCursor)
            t_lay = QVBoxLayout(tile)
            t_lay.setContentsMargins(10, 8, 10, 8)
            t_lay.setSpacing(2)

            n_lbl = QLabel(name)
            n_lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
            n_lbl.setAlignment(Qt.AlignCenter)

            c_lbl = QLabel(f"{chg:+.2f}%")
            c_lbl.setFont(QFont("Segoe UI", 12, QFont.Bold))
            c_lbl.setAlignment(Qt.AlignCenter)

            if chg > 1.5:
                bg = "#1B4332"
                fg = SUCCESS
            elif chg > 0:
                bg = "#1A2E1A"
                fg = "#56D364"
            elif chg > -1.5:
                bg = "#2E1A1A"
                fg = "#F0883E"
            else:
                bg = "#3B1219"
                fg = DANGER

            tile.setStyleSheet(f"background: {bg}; border-radius: 8px; border: 1px solid {BORDER};")
            n_lbl.setStyleSheet(f"color: {fg}; background: transparent;")
            c_lbl.setStyleSheet(f"color: {fg}; background: transparent;")
            t_lay.addWidget(n_lbl)
            t_lay.addWidget(c_lbl)

            row, col = divmod(i, 6)
            self.sector_grid.addWidget(tile, row, col)
            self.sector_tiles[name] = tile

    def update_movers(self, gainers, losers):
        self._fill_mover_table(self.gainers_table, gainers, SUCCESS)
        self._fill_mover_table(self.losers_table, losers, DANGER)

    def _fill_mover_table(self, table, data, color):
        table.setRowCount(0)
        for item in data[:10]:
            row = table.rowCount()
            table.insertRow(row)

            sym = QTableWidgetItem(item.get("symbol", ""))
            sym.setTextAlignment(Qt.AlignCenter)
            sym.setForeground(QColor(color))
            sym.setFont(QFont("Segoe UI", 9, QFont.Bold))

            price = QTableWidgetItem(f"${item.get('price', 0):,.2f}")
            price.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            chg = item.get("changesPercentage", 0) or 0
            chg_item = QTableWidgetItem(f"{chg:+.2f}%")
            chg_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            chg_item.setForeground(QColor(color))

            vol = item.get("volume", 0) or 0
            vol_item = QTableWidgetItem(f"{vol:,.0f}")
            vol_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            table.setItem(row, 0, sym)
            table.setItem(row, 1, price)
            table.setItem(row, 2, chg_item)
            table.setItem(row, 3, vol_item)

    def update_tank(self, count, total):
        pct = (count / total * 100) if total > 0 else 0
        self.tank_bar.setValue(int(pct))
        self.tank_lbl.setText(f"Fundamentals Tank: {count}/{total} stocks ({pct:.1f}%)")
        
        if pct < 30: color = DANGER
        elif pct < 70: color = WARNING
        else: color = SUCCESS
        self.tank_bar.setStyleSheet(f"QProgressBar {{ border: 1px solid {BORDER}; border-radius: 6px; background: {BG_PRIMARY}; }} QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}")
