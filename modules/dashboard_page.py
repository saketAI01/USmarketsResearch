import os
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, time, date
import pytz
from typing import List, Dict, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QProgressBar, QSizePolicy, QScrollArea, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QSplitter, QComboBox, QMenu
)
from PySide6.QtCore import Qt, QThreadPool, QRunnable, QObject, Signal, Slot, QTimer, QUrl, QThread
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QCursor, QDesktopServices

# ── Color Palette ────────────────────────────────────────────────────────────
C = {
    "bg":      "#0B1628",
    "card":    "#162240",
    "hover":   "#1E3050",
    "border":  "#1E3050",
    "accent":  "#00D4FF",
    "green":   "#22c55e",
    "red":     "#ef4444",
    "yellow":  "#f59e0b",
    "text":    "#E2E8F0",
    "sec":     "#94A3B8",
    "muted":   "#64748B",
}

# ── Dataclasses for Snapshots ───────────────────────────────────────────
class IndexData:
    def __init__(self, symbol: str, name: str, price: float, change: float, percent_change: float, sparkline_data: List[float]):
        self.symbol = symbol
        self.name = name
        self.price = price
        self.change = change
        self.percent_change = percent_change
        self.sparkline_data = sparkline_data

class SectorData:
    def __init__(self, symbol: str, name: str, price: float, change: float, percent_change: float):
        self.symbol = symbol
        self.name = name
        self.price = price
        self.change = change
        self.percent_change = percent_change

class MoverData:
    def __init__(self, symbol: str, name: str, price: float, change: float, percent_change: float, volume: float, volume_multiplier: float = 0.0):
        self.symbol = symbol
        self.name = name
        self.price = price
        self.change = change
        self.percent_change = percent_change
        self.volume = volume
        self.volume_multiplier = volume_multiplier

class NewsArticle:
    def __init__(self, title: str, url: str, source: str, description: str, sentiment_score: float, sentiment_label: str, published_at: str):
        self.title = title
        self.url = url
        self.source = source
        self.description = description
        self.sentiment_score = sentiment_score
        self.sentiment_label = sentiment_label
        self.published_at = published_at

class DashboardSnapshot:
    def __init__(self, timestamp: datetime, market_open: bool, status_text: str, indices: Dict[str, IndexData], sectors: List[SectorData], gainers: List[MoverData], losers: List[MoverData], actives: List[MoverData], surgers: List[MoverData], news: List[NewsArticle]):
        self.timestamp = timestamp
        self.market_open = market_open
        self.status_text = status_text
        self.indices = indices
        self.sectors = sectors
        self.gainers = gainers
        self.losers = losers
        self.actives = actives
        self.surgers = surgers
        self.news = news

# ── Market Hours Checking ───────────────────────────────────────────────────
MARKET_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents' Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (Observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}

def get_ny_time() -> datetime:
    tz = pytz.timezone('America/New_York')
    return datetime.now(tz)

def is_market_holiday(d: date) -> bool:
    if d in MARKET_HOLIDAYS_2026:
        return True
    if d.year != 2026:
        if d.month == 1 and d.day == 1: return True
        if d.month == 7 and d.day == 4: return True
        if d.month == 12 and d.day == 25: return True
    return False

def get_market_status() -> tuple[bool, str]:
    ny_now = get_ny_time()
    current_date = ny_now.date()
    current_time = ny_now.time()
    weekday = ny_now.weekday()
    if weekday >= 5:
        return False, "Market Closed (Weekend)"
    if is_market_holiday(current_date):
        return False, "Market Closed (Holiday)"
    
    pre_market_start = time(4, 0)
    regular_start = time(9, 30)
    regular_end = time(16, 0)
    after_hours_end = time(20, 0)
    
    if regular_start <= current_time < regular_end:
        return True, "Market Open"
    elif pre_market_start <= current_time < regular_start:
        return False, "Pre-Market Hours"
    elif regular_end <= current_time < after_hours_end:
        return False, "After-Hours"
    else:
        return False, "Market Closed"

# ── Sparkline & Cards Widgets ───────────────────────────────────────────────
class SparklineWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: List[float] = []
        self.color = QColor(C["muted"])
        self.setMinimumSize(80, 30)

    def set_data(self, data: List[float], positive: bool):
        self.data = data
        self.color = QColor(C["green"]) if positive else QColor(C["red"])
        self.update()

    def paintEvent(self, event):
        if not self.data or len(self.data) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        min_val = min(self.data)
        max_val = max(self.data)
        val_range = max_val - min_val
        if val_range == 0:
            val_range = 1.0

        w = self.width()
        h = self.height()
        padding = 2
        
        points = []
        for i, val in enumerate(self.data):
            x = padding + (i / (len(self.data) - 1)) * (w - 2 * padding)
            y = (h - padding) - ((val - min_val) / val_range) * (h - 2 * padding)
            points.append((x, y))

        pen = QPen(self.color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        for i in range(len(points) - 1):
            painter.drawLine(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])

class IndexCard(QFrame):
    def __init__(self, symbol: str, name: str, parent=None):
        super().__init__(parent)
        self.symbol = symbol
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {C['card']};
                border: 1px solid {C['border']};
                border-radius: 8px;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        
        self.title_lbl = QLabel(name, self)
        self.title_lbl.setStyleSheet(f"font-weight: bold; color: {C['sec']}; font-size: 11px;")
        
        self.price_lbl = QLabel("0.00", self)
        self.price_lbl.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {C['text']};")
        
        self.change_lbl = QLabel("0.00 (0.00%)", self)
        self.change_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {C['sec']};")
        
        self.sparkline = SparklineWidget(self)
        
        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(self.change_lbl)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.sparkline)
        
        layout.addWidget(self.title_lbl)
        layout.addWidget(self.price_lbl)
        layout.addLayout(bottom_layout)

    def update_data(self, data: IndexData):
        self.price_lbl.setText(f"{data.price:,.2f}")
        sign = "+" if data.change >= 0 else ""
        self.change_lbl.setText(f"{sign}{data.change:,.2f} ({sign}{data.percent_change:.2f}%)")
        
        is_positive = data.change >= 0
        if self.symbol == "^VIX":
            is_positive = not is_positive
            
        color = C["green"] if is_positive else C["red"]
        self.change_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {color};")
        
        if data.sparkline_data:
            self.sparkline.set_data(data.sparkline_data, is_positive)

class IndicesPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        
        symbols = [("^GSPC", "S&P 500"), ("^DJI", "DOW 30"), ("^IXIC", "NASDAQ"), ("^VIX", "CBOE VIX"), ("^RUT", "RUSSELL 2000")]
        self.cards: Dict[str, IndexCard] = {}
        for sym, name in symbols:
            card = IndexCard(sym, name, self)
            self.cards[sym] = card
            layout.addWidget(card)
            
    def update_indices(self, indices: Dict[str, IndexData]):
        for sym, card in self.cards.items():
            if sym in indices:
                card.update_data(indices[sym])

# ── Sector Badges Panel ────────────────────────────────────────────────────
class SectorBadge(QFrame):
    def __init__(self, symbol: str, name: str, parent=None):
        super().__init__(parent)
        self.symbol = symbol
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {C['card']};
                border: 1px solid {C['border']};
                border-radius: 6px;
            }}
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)
        
        short_name = name.replace("Consumer Discretionary", "Cons. Disc.").replace("Consumer Staples", "Cons. Staples").replace("Communication Services", "Comm. Serv.")
        
        self.name_lbl = QLabel(short_name, self)
        self.name_lbl.setStyleSheet(f"font-weight: 600; font-size: 11px; color: {C['text']};")
        
        self.pct_lbl = QLabel("0.00%", self)
        self.pct_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        
        layout.addWidget(self.name_lbl)
        layout.addWidget(self.pct_lbl)

    def update_data(self, data: SectorData):
        sign = "+" if data.percent_change >= 0 else ""
        self.pct_lbl.setText(f"{sign}{data.percent_change:.2f}%")
        color = C["green"] if data.percent_change >= 0 else C["red"]
        self.pct_lbl.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {color};")

class SectorsPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: transparent; border: none;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }}")
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        self.scroll_layout = QHBoxLayout(scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(8)
        
        SECTOR_MAP = {
            "XLK": "Technology", "XLV": "Health Care", "XLF": "Financials",
            "XLE": "Energy", "XLY": "Consumer Discretionary", "XLI": "Industrials",
            "XLB": "Materials", "XLRE": "Real Estate", "XLP": "Consumer Staples",
            "XLU": "Utilities", "XLC": "Communication Services"
        }
        self.badges: dict[str, SectorBadge] = {}
        for etf, name in SECTOR_MAP.items():
            badge = SectorBadge(etf, name, self)
            self.badges[etf] = badge
            self.scroll_layout.addWidget(badge)
            
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

    def update_sectors(self, sectors: List[SectorData]):
        for data in sectors:
            if data.symbol in self.badges:
                self.badges[data.symbol].update_data(data)

# ── Movers Table & Panel ───────────────────────────────────────────────────
def format_volume(volume: float) -> str:
    if volume >= 1e9: return f"{volume / 1e9:.2f}B"
    elif volume >= 1e6: return f"{volume / 1e6:.2f}M"
    elif volume >= 1e3: return f"{volume / 1e3:.2f}K"
    return str(int(volume))

class NumericTableWidgetItem(QTableWidgetItem):
    def __init__(self, text: str, value: float):
        super().__init__(text)
        self.setData(Qt.UserRole, value)

    def __lt__(self, other):
        val1 = self.data(Qt.UserRole)
        val2 = other.data(Qt.UserRole)
        if val1 is not None and val2 is not None:
            try: return float(val1) < float(val2)
            except (ValueError, TypeError): pass
        return self.text() < other.text()

class MoversTable(QTableWidget):
    double_click_symbol = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(["Symbol", "Name", "Price", "Change", "Volume"])
        
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.SingleSelection)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.doubleClicked.connect(self.on_double_clicked)
        self.setSortingEnabled(True)
        
        self.setStyleSheet(f"""
            QTableWidget {{
                background-color: {C['card']};
                gridline-color: {C['border']};
                border: 1px solid {C['border']};
                border-radius: 6px;
                color: {C['text']};
            }}
            QHeaderView::section {{
                background-color: {C['card']};
                color: {C['accent']};
                padding: 6px;
                font-weight: bold;
                border: none;
                border-bottom: 1px solid {C['border']};
            }}
            QTableWidget::item:selected {{
                background-color: #5F258F;
                color: #ffffff;
            }}
        """)

    def show_context_menu(self, pos):
        item = self.itemAt(pos)
        if item is None: return
        row = item.row()
        sym_item = self.item(row, 0)
        if sym_item is None: return
        symbol = sym_item.text().strip().upper()
        if not symbol or "NO DATA" in symbol: return
            
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {C['card']};
                color: {C['text']};
                border: 1px solid {C['border']};
            }}
            QMenu::item:selected {{
                background-color: {C['hover']};
            }}
        """)
        
        tv_act = menu.addAction(f"Open {symbol} Chart in TradingView")
        yf_act = menu.addAction(f"Open {symbol} Chart in Yahoo Finance")
        fv_act = menu.addAction(f"Open {symbol} Chart in Finviz")
        
        tv_act.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://www.tradingview.com/chart/?symbol={symbol}")))
        yf_act.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://finance.yahoo.com/chart/{symbol}")))
        fv_act.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://finviz.com/quote.ashx?t={symbol}")))
        menu.exec(self.mapToGlobal(pos))

    def on_double_clicked(self, index):
        row = index.row()
        symbol_item = self.item(row, 0)
        if symbol_item is None: return
        symbol = symbol_item.text().strip().upper()
        if symbol and "NO DATA" not in symbol:
            self.double_click_symbol.emit(symbol)

    def populate(self, data_list: List[MoverData], placeholder_text: str = "No data available"):
        self.setSortingEnabled(False)
        self.setRowCount(0)
        if not data_list:
            self.setRowCount(1)
            item = QTableWidgetItem(placeholder_text)
            item.setTextAlignment(Qt.AlignCenter)
            self.setItem(0, 0, item)
            self.setSpan(0, 0, 1, 5)
            self.setSortingEnabled(True)
            return

        self.setRowCount(len(data_list))
        for row, data in enumerate(data_list):
            sym_item = QTableWidgetItem(data.symbol)
            sym_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
            sym_item.setForeground(QColor(C["accent"]))
            sym_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            
            name_item = QTableWidgetItem(data.name)
            name_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            name_item.setForeground(QColor(C["sec"]))
            
            price_item = NumericTableWidgetItem(f"${data.price:,.2f}", data.price)
            price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            
            sign = "+" if data.change >= 0 else ""
            change_item = NumericTableWidgetItem(f"{sign}{data.percent_change:.2f}%", data.percent_change)
            change_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            color = C["green"] if data.change >= 0 else C["red"]
            change_item.setForeground(QColor(color))
            
            vol_text = f"{format_volume(data.volume)} ({data.volume_multiplier:.1f}x)" if data.volume_multiplier > 0.0 else format_volume(data.volume)
            vol_item = NumericTableWidgetItem(vol_text, data.volume)
            vol_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            vol_item.setForeground(QColor(C["sec"]))
            
            self.setItem(row, 0, sym_item)
            self.setItem(row, 1, name_item)
            self.setItem(row, 2, price_item)
            self.setItem(row, 3, change_item)
            self.setItem(row, 4, vol_item)
            
        self.setSortingEnabled(True)

class MoversPanel(QWidget):
    double_click_symbol = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        
        header_layout = QHBoxLayout()
        title = QLabel("📊 Market Movers", self)
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {C['accent']};")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        self.universe_combo = QComboBox(self)
        self.universe_combo.addItems(["S&P 500", "NASDAQ", "Russell 2000"])
        self.universe_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {C['card']};
                border: 1px solid {C['border']};
                color: {C['text']};
                border-radius: 4px;
                padding: 4px 8px;
            }}
        """)
        header_layout.addWidget(self.universe_combo)
        layout.addLayout(header_layout)
        
        self.tabs = QTabWidget(self)
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {C['border']}; top: -1px; }}
            QTabBar::tab {{ background: {C['card']}; color: {C['sec']}; padding: 6px 12px; border: 1px solid {C['border']}; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
            QTabBar::tab:selected {{ background: {C['bg']}; color: {C['text']}; border-bottom: 2px solid {C['accent']}; }}
        """)
        
        self.gainers_table = MoversTable(self)
        self.losers_table = MoversTable(self)
        self.actives_table = MoversTable(self)
        self.surgers_table = MoversTable(self)
        
        for t in [self.gainers_table, self.losers_table, self.actives_table, self.surgers_table]:
            t.double_click_symbol.connect(self.double_click_symbol.emit)
            
        self.tabs.addTab(self.gainers_table, "Top Gainers")
        self.tabs.addTab(self.losers_table, "Top Losers")
        self.tabs.addTab(self.actives_table, "Most Active")
        self.tabs.addTab(self.surgers_table, "Vol Surgers")
        
        layout.addWidget(self.tabs)

    def update_movers(self, gainers: List[MoverData], losers: List[MoverData], actives: List[MoverData], surgers: List[MoverData]):
        self.gainers_table.populate(gainers)
        self.losers_table.populate(losers)
        self.actives_table.populate(actives)
        self.surgers_table.populate(surgers, "No volume surgers found (Volume > 1.5x 20d Avg)")

# ── News Feed Widgets ───────────────────────────────────────────────────────
class NewsItemWidget(QFrame):
    def __init__(self, article: NewsArticle, parent=None):
        super().__init__(parent)
        self.url = article.url
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {C['card']};
                border: 1px solid {C['border']};
                border-radius: 8px;
            }}
            QFrame:hover {{
                background-color: {C['hover']};
                border: 1px solid {C['accent']};
            }}
        """)
        
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        
        meta_lbl = QLabel(f"{article.source} • {article.published_at}", self)
        meta_lbl.setStyleSheet(f"font-size: 11px; color: {C['sec']}; font-weight: 500;")
        text_layout.addWidget(meta_lbl)
        
        title_lbl = QLabel(article.title, self)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {C['text']};")
        text_layout.addWidget(title_lbl)
        
        desc_lbl = QLabel(article.description, self)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"font-size: 12px; color: {C['sec']};")
        text_layout.addWidget(desc_lbl)
        
        main_layout.addLayout(text_layout, stretch=1)
        
        sentiment_layout = QVBoxLayout()
        sentiment_layout.setAlignment(Qt.AlignCenter)
        
        badge = QLabel(article.sentiment_label, self)
        badge.setAlignment(Qt.AlignCenter)
        
        if article.sentiment_label == "Positive":
            bg_color = f"background-color: rgba(34, 197, 94, 0.15); color: {C['green']};"
        elif article.sentiment_label == "Negative":
            bg_color = f"background-color: rgba(239, 68, 68, 0.15); color: {C['red']};"
        else:
            bg_color = f"background-color: rgba(148, 163, 184, 0.15); color: {C['sec']};"
            
        badge.setStyleSheet(f"""
            QLabel {{
                {bg_color}
                font-weight: bold;
                font-size: 10px;
                padding: 4px 8px;
                border-radius: 4px;
                min-width: 60px;
            }}
        """)
        
        score_lbl = QLabel(f"Score: {article.sentiment_score:+.2f}", self)
        score_lbl.setAlignment(Qt.AlignCenter)
        score_lbl.setStyleSheet(f"font-size: 10px; color: {C['sec']}; font-weight: 600;")
        
        sentiment_layout.addWidget(badge)
        sentiment_layout.addWidget(score_lbl)
        main_layout.addLayout(sentiment_layout)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.url:
            QDesktopServices.openUrl(QUrl(self.url))
        super().mousePressEvent(event)

class NewsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        
        title = QLabel("📰 Market News Feed", self)
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {C['accent']}; margin-bottom: 2px;")
        layout.addWidget(title)
        
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background-color: transparent; }}")
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: transparent;")
        self.news_layout = QVBoxLayout(scroll_content)
        self.news_layout.setContentsMargins(0, 0, 0, 0)
        self.news_layout.setSpacing(8)
        self.news_layout.addStretch()
        
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

    def update_news(self, news: List[NewsArticle]):
        while self.news_layout.count() > 1:
            item = self.news_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        for art in reversed(news):
            w = NewsItemWidget(art, self)
            self.news_layout.insertWidget(0, w)

# ── Data Fetching Worker ────────────────────────────────────────────────────
class DashboardDataWorker(QThread):
    finished = Signal(object)
    status_msg = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.universe = "S&P 500"

    def set_universe(self, universe):
        self.universe = universe

    def run(self):
        import yfinance as yf
        import pandas as pd
        import requests
        from datetime import datetime
        import xml.etree.ElementTree as ET
        import re

        # 1. Fetch indices
        self.status_msg.emit("Fetching market indices...")
        indices = {}
        symbols = ["^GSPC", "^DJI", "^IXIC", "^VIX", "^RUT"]
        symbol_names = {
            "^GSPC": "S&P 500",
            "^DJI": "DOW 30",
            "^IXIC": "NASDAQ",
            "^VIX": "CBOE VIX",
            "^RUT": "RUSSELL 2000"
        }
        try:
            df = yf.download(tickers=symbols, period="1d", interval="5m", progress=False)
            df_daily = yf.download(tickers=symbols, period="5d", interval="1d", progress=False)
            for sym in symbols:
                price, change, pct_change, sparkline = 0.0, 0.0, 0.0, []
                if not df_daily.empty and 'Close' in df_daily:
                    try:
                        if isinstance(df_daily['Close'], pd.DataFrame):
                            sym_daily = df_daily['Close'][sym].dropna()
                        else:
                            sym_daily = df_daily['Close'].dropna()
                        if len(sym_daily) >= 2:
                            prev_close = sym_daily.iloc[-2]
                            current_close = sym_daily.iloc[-1]
                            price = current_close
                            change = current_close - prev_close
                            pct_change = (change / prev_close) * 100.0 if prev_close else 0.0
                    except Exception: pass
                if not df.empty and 'Close' in df:
                    try:
                        if isinstance(df['Close'], pd.DataFrame):
                            sym_intraday = df['Close'][sym].dropna()
                        else:
                            sym_intraday = df['Close'].dropna()
                        if not sym_intraday.empty:
                            price = sym_intraday.iloc[-1]
                            if 'prev_close' in locals() and prev_close:
                                change = price - prev_close
                                pct_change = (change / prev_close) * 100.0
                            elif len(sym_intraday) >= 2:
                                start_p = sym_intraday.iloc[0]
                                change = price - start_p
                                pct_change = (change / start_p) * 100.0 if start_p else 0.0
                            vals = sym_intraday.tolist()
                            if len(vals) > 15:
                                step = len(vals) // 15
                                sparkline = vals[::step][:15]
                            else:
                                sparkline = vals
                    except Exception: pass
                
                indices[sym] = IndexData(
                    symbol=sym,
                    name=symbol_names[sym],
                    price=price,
                    change=change,
                    percent_change=pct_change,
                    sparkline_data=sparkline
                )
        except Exception as e:
            print(f"Indices fetch error: {e}")

        # 2. Fetch GICS sectors
        self.status_msg.emit("Fetching sector performance...")
        sectors = []
        SECTOR_MAP = {
            "XLK": "Technology", "XLV": "Health Care", "XLF": "Financials",
            "XLE": "Energy", "XLY": "Consumer Discretionary", "XLI": "Industrials",
            "XLB": "Materials", "XLRE": "Real Estate", "XLP": "Consumer Staples",
            "XLU": "Utilities", "XLC": "Communication Services"
        }
        try:
            sector_etfs = list(SECTOR_MAP.keys())
            df_sec = yf.download(tickers=sector_etfs, period="5d", interval="1d", progress=False)
            if not df_sec.empty and 'Close' in df_sec:
                for etf in sector_etfs:
                    try:
                        if isinstance(df_sec['Close'], pd.DataFrame):
                            series = df_sec['Close'][etf].dropna()
                        else:
                            series = df_sec['Close'].dropna()
                        if len(series) >= 2:
                            prev_close = series.iloc[-2]
                            price = series.iloc[-1]
                            change = price - prev_close
                            pct_change = (change / prev_close) * 100.0
                            sectors.append(SectorData(
                                symbol=etf,
                                name=SECTOR_MAP[etf],
                                price=price,
                                change=change,
                                percent_change=pct_change
                            ))
                    except Exception: pass
        except Exception as e:
            print(f"Sectors fetch error: {e}")

        # 3. Fetch movers
        self.status_msg.emit("Fetching market movers...")
        gainers, losers, actives, surgers = [], [], [], []
        PRESETS = {
            "S&P 500 Majors": [
                "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "JPM", "V", "XOM",
                "PG", "JNJ", "TSLA", "AVGO", "HD", "COST", "UNH", "MA", "CRM", "BAC"
            ],
            "NASDAQ 100": [
                "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "CSCO"
            ],
            "Russell 2000 (subset)": [
                "AEO", "AMBA", "APPS", "ARRY", "BILL", "BLNK", "BOOT", "BOX", "CALM", "GME", "HOOD", "PLUG", "SOFI"
            ],
            "Sector Leaders": [
                "AAPL", "MSFT", "NVDA", "LLY", "UNH", "JNJ", "JPM", "BAC", "MS", "AMZN", "TSLA", "HD"
            ]
        }
        
        if self.universe == "S&P 500":
            raw_tickers = PRESETS["S&P 500 Majors"] + PRESETS["Sector Leaders"]
        elif self.universe == "NASDAQ":
            raw_tickers = PRESETS["NASDAQ 100"]
        else:
            raw_tickers = PRESETS["Russell 2000 (subset)"]

        major_tickers = sorted(list(set(raw_tickers)))
        try:
            df_movers = yf.download(tickers=major_tickers, period="35d", interval="1d", progress=False, group_by='ticker')
            mover_candidates = []
            for ticker in major_tickers:
                try:
                    ticker_df = None
                    if len(major_tickers) > 1:
                        if ticker in df_movers.columns.levels[0]:
                            ticker_df = df_movers[ticker].dropna()
                    else:
                        ticker_df = df_movers.dropna()
                    if ticker_df is not None and not ticker_df.empty and len(ticker_df) >= 2:
                        price = float(ticker_df['Close'].iloc[-1])
                        prev_close = float(ticker_df['Close'].iloc[-2])
                        change = price - prev_close
                        pct_change = (change / prev_close) * 100.0 if prev_close else 0.0
                        volume = float(ticker_df['Volume'].iloc[-1]) if 'Volume' in ticker_df.columns else 0.0
                        
                        history_df = ticker_df.iloc[:-1]
                        avg_volume = 0.0
                        if len(history_df) >= 1:
                            avg_volume = float(history_df['Volume'].tail(20).mean())
                        
                        multiplier = volume / avg_volume if avg_volume > 0.0 else 0.0
                        mover = MoverData(
                            symbol=ticker,
                            name=ticker,
                            price=price,
                            change=change,
                            percent_change=pct_change,
                            volume=volume,
                            volume_multiplier=multiplier
                        )
                        mover_candidates.append(mover)
                except Exception: pass

            if mover_candidates:
                gainers = sorted(mover_candidates, key=lambda x: x.percent_change, reverse=True)[:10]
                losers = sorted(mover_candidates, key=lambda x: x.percent_change)[:10]
                actives = sorted(mover_candidates, key=lambda x: x.volume, reverse=True)[:10]
                surgers = sorted([m for m in mover_candidates if m.volume_multiplier > 1.5], key=lambda x: x.volume_multiplier, reverse=True)[:10]
        except Exception as e:
            print(f"Movers fetch error: {e}")

        # 4. Fetch news
        self.status_msg.emit("Fetching latest news...")
        news = []
        
        def calculate_simple_sentiment(text):
            pos_words = ["soar", "gain", "surge", "upbeat", "rally", "profit", "beat", "upgrade", "growth", "jump", "success", "bull", "higher", "record", "advance", "optimism", "strong", "win"]
            neg_words = ["plunge", "tumble", "drop", "fell", "loss", "miss", "downgrade", "decline", "warn", "slump", "bear", "fear", "lower", "crater", "sink", "crash", "worry", "slipped", "debt"]
            text_lower = text.lower()
            pos_count = sum(1 for w in pos_words if w in text_lower)
            neg_count = sum(1 for w in neg_words if w in text_lower)
            total = pos_count + neg_count
            if total == 0: return 0.0
            return (pos_count - neg_count) / total

        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
            r_news = requests.get("https://finance.yahoo.com/rss/topstories", headers=headers, timeout=10)
            if r_news.status_code == 200:
                root_xml = ET.fromstring(r_news.text)
                for item in root_xml.findall("./channel/item")[:15]:
                    title = item.find("title").text if item.find("title") is not None else ""
                    url = item.find("link").text if item.find("link") is not None else ""
                    desc = item.find("description").text if item.find("description") is not None else ""
                    pub = item.find("pubDate").text if item.find("pubDate") is not None else ""
                    
                    desc_clean = re.sub(r'<[^>]*>', '', desc)
                    sentiment = calculate_simple_sentiment(title + " " + desc_clean)
                    label = "Neutral"
                    if sentiment > 0.15: label = "Positive"
                    elif sentiment < -0.15: label = "Negative"
                    
                    news.append(NewsArticle(
                        title=title,
                        url=url,
                        source="Yahoo Finance",
                        description=desc_clean[:180] + "..." if len(desc_clean) > 180 else desc_clean,
                        sentiment_score=sentiment,
                        sentiment_label=label,
                        published_at=pub
                    ))
        except Exception as e:
            print(f"News fetch error: {e}")

        # Final snapshot
        snapshot = DashboardSnapshot(
            timestamp=datetime.now(),
            market_open=False,
            status_text="",
            indices=indices,
            sectors=sectors,
            gainers=gainers,
            losers=losers,
            actives=actives,
            surgers=surgers,
            news=news
        )
        self.status_msg.emit("Done.")
        self.finished.emit(snapshot)

# ── Main Dashboard Page Widget ──────────────────────────────────────────────
class DashboardPage(QWidget):
    go_explore = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = DashboardDataWorker(self)
        self.worker.finished.connect(self._on_data_ready)
        self.worker.status_msg.connect(self._on_status_msg)
        
        self._build_ui()
        
        # Initial fetch
        self.refresh()
        
        # Timer for auto-refresh every 3 minutes
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(180 * 1000)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        
        # 1. Header toolbar
        hdr = QHBoxLayout()
        title = QLabel("📊 US Markets Dashboard")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {C['accent']};")
        hdr.addWidget(title)
        
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"font-size: 16px; color: {C['red']}; margin-left: 6px;")
        hdr.addWidget(self.status_dot)
        
        self.market_status_lbl = QLabel("Closed")
        self.market_status_lbl.setStyleSheet(f"color: {C['sec']}; font-weight: 600; font-size: 12px; margin-left: 4px;")
        hdr.addWidget(self.market_status_lbl)
        hdr.addStretch()
        
        self.last_updated_lbl = QLabel("Last Updated: Never")
        self.last_updated_lbl.setStyleSheet(f"color: {C['muted']}; font-size: 12px;")
        hdr.addWidget(self.last_updated_lbl)
        
        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['card']};
                color: {C['text']};
                border: 1px solid {C['border']};
                padding: 5px 12px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {C['hover']};
            }}
        """)
        self.refresh_btn.clicked.connect(self.refresh)
        hdr.addWidget(self.refresh_btn)
        layout.addLayout(hdr)
        
        # 2. Indices Panel
        self.indices_panel = IndicesPanel(self)
        layout.addWidget(self.indices_panel)
        
        # 3. Sectors Panel
        self.sectors_panel = SectorsPanel(self)
        layout.addWidget(self.sectors_panel)
        
        # 4. Content split (Movers on left, News on right)
        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setHandleWidth(8)
        self.splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {C['border']}; }}")
        
        self.movers_panel = MoversPanel(self)
        self.movers_panel.double_click_symbol.connect(self.go_explore.emit)
        self.movers_panel.universe_combo.currentTextChanged.connect(self.refresh)
        
        self.news_panel = NewsPanel(self)
        
        self.splitter.addWidget(self.movers_panel)
        self.splitter.addWidget(self.news_panel)
        self.splitter.setSizes([400, 600])
        
        layout.addWidget(self.splitter, 1)

    def refresh(self):
        if self.worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.worker.set_universe(self.movers_panel.universe_combo.currentText())
        self.worker.start()

    def _on_status_msg(self, msg):
        self.last_updated_lbl.setText(msg)

    def _on_data_ready(self, snapshot: DashboardSnapshot):
        # Update sub panels
        self.indices_panel.update_indices(snapshot.indices)
        self.sectors_panel.update_sectors(snapshot.sectors)
        self.movers_panel.update_movers(snapshot.gainers, snapshot.losers, snapshot.actives, snapshot.surgers)
        self.news_panel.update_news(snapshot.news)
        
        # Update Market hours status
        is_open, status_text = get_market_status()
        self.market_status_lbl.setText(status_text)
        if is_open:
            self.status_dot.setStyleSheet(f"font-size: 16px; color: {C['green']}; margin-left: 6px;")
        else:
            if "Hours" in status_text:
                self.status_dot.setStyleSheet(f"font-size: 16px; color: {C['yellow']}; margin-left: 6px;")
            else:
                self.status_dot.setStyleSheet(f"font-size: 16px; color: {C['red']}; margin-left: 6px;")

        self.last_updated_lbl.setText(f"Last Updated: {snapshot.timestamp.strftime('%H:%M:%S')}")
        self.refresh_btn.setEnabled(True)
