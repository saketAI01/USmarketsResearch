import sys
import os
import json
import requests
import traceback
import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QProgressBar,
    QSizePolicy, QAbstractItemView, QMessageBox
)
from PySide6.QtCore import (
    Qt, QThreadPool, QRunnable, QObject, Signal, Slot, QTimer, QUrl
)
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtGui import QColor, QFont

from modules.stock_evaluate.config import load_api_keys

log = logging.getLogger("livestream")

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

# ── Load API Credentials ──────────────────────────────────────────────────────
try:
    KEYS = load_api_keys()
    API_KEY = KEYS.get("alpaca_key") or "PKROFY5THVZHNWVBBPBSCGAYFX"
    API_SECRET = KEYS.get("alpaca_secret") or "AYFGMJup2QqquisvfkaSnfjNKXvASYf6saJtJ9Tp5J33"
except Exception:
    API_KEY = "PKROFY5THVZHNWVBBPBSCGAYFX"
    API_SECRET = "AYFGMJup2QqquisvfkaSnfjNKXvASYf6saJtJ9Tp5J33"

TRADING_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
HDR = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

# ── Load Company Name Lookup ──────────────────────────────────────────────────
COMPANY_NAMES = {}
try:
    import csv as _csv_master
    _root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _p = os.path.join(_root_path, "USStockMaster.csv")
    if os.path.exists(_p):
        with open(_p, "r", encoding="utf-8") as _f_master:
            _reader = _csv_master.reader(_f_master)
            next(_reader, None)  # Skip header
            for _row in _reader:
                if len(_row) >= 2:
                    COMPANY_NAMES[_row[0].strip().upper()] = _row[1].strip()
except Exception as _e_master:
    log.warning(f"Could not load USStockMaster.csv company names: {_e_master}")


# ── Reusable Thread Worker ──────────────────────────────────────────────────
class _Signals(QObject):
    result = Signal(object)
    error  = Signal(str)
    done   = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)


class Worker(QRunnable):
    def __init__(self, fn, parent=None, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals(parent)
        self.setAutoDelete(True)

    @Slot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit(result)
        except Exception:
            tb = traceback.format_exc()
            self.signals.error.emit(tb)
        finally:
            self.signals.done.emit()


class NumericTableWidgetItem(QTableWidgetItem):
    """Decouples DisplayRole from EditRole so we can display '—' while storing a sortable numeric value."""
    def __init__(self, display_val="", edit_val=0.0):
        super().__init__()
        self._display_val = display_val
        self._edit_val = edit_val

    def data(self, role):
        if role == Qt.DisplayRole:
            return self._display_val
        if role == Qt.EditRole:
            return self._edit_val
        return super().data(role)

    def setData(self, role, value):
        if role == Qt.DisplayRole:
            self._display_val = value
            super().setData(role, value)
        elif role == Qt.EditRole:
            self._edit_val = value
        else:
            super().setData(role, value)

    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            v1 = self.data(Qt.EditRole)
            v2 = other.data(Qt.EditRole)
            try:
                return float(v1) < float(v2)
            except (ValueError, TypeError):
                try:
                    return float(self.text()) < float(other.text())
                except (ValueError, TypeError):
                    return self.text() < other.text()
        return super().__lt__(other)


# ── Alpaca REST API Snapshot Client ──────────────────────────────────────────
class AlpacaAPI:
    @staticmethod
    def _get(base: str, path: str, params: dict = None, _retry: bool = True):
        try:
            r = requests.get(f"{base}{path}", headers=HDR, params=params, timeout=12)
            r.raise_for_status()
            return r.json(), None
        except requests.exceptions.HTTPError as e:
            if _retry and params and "feed" in params:
                p2 = {k: v for k, v in params.items() if k != "feed"}
                result, err2 = AlpacaAPI._get(base, path, p2, _retry=False)
                if result is not None:
                    return result, None
            try:
                msg = r.json().get("message", str(e))
            except Exception:
                msg = str(e)
            return None, msg
        except Exception as e:
            return None, str(e)

    def snapshot(self, symbols: list):
        if not symbols:
            return {}, None
        chunk_size = 100
        chunks = [symbols[i:i+chunk_size] for i in range(0, len(symbols), chunk_size)]
        merged = {}
        for c in chunks:
            syms_str = ",".join(c)
            path = f"/v2/stocks/snapshots?symbols={syms_str}&feed=iex"
            res, err = self._get(DATA_URL, path)
            if err:
                for sym in c:
                    path_ind = f"/v2/stocks/{sym}/snapshot?feed=iex"
                    r_ind, err_ind = self._get(DATA_URL, path_ind)
                    if r_ind:
                        merged[sym] = r_ind.get("snapshot", {})
            else:
                if res and isinstance(res, dict):
                    merged.update(res)
        return merged, None

API = AlpacaAPI()


# ── Reusable Custom UI Components ────────────────────────────────────────────
class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "—", subtitle: str = "", color: str = None, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {C['card']};
                border: 1px solid {C['border']};
                border-radius: 8px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)

        lbl_t = QLabel(title)
        lbl_t.setStyleSheet(f"color:{C['sec']}; font-size:10px; font-weight:700; letter-spacing:0.5px;")
        self._val = QLabel(value)
        self._val.setStyleSheet(f"color:{color or C['text']}; font-size:18px; font-weight:700;")
        self._sub = QLabel(subtitle)
        self._sub.setStyleSheet(f"color:{C['sec']}; font-size:10px;")

        lay.addWidget(lbl_t)
        lay.addWidget(self._val)
        lay.addWidget(self._sub)

    def update_data(self, value: str, subtitle: str = "", color: str = None):
        self._val.setText(value)
        if color:
            self._val.setStyleSheet(f"color:{color}; font-size:18px; font-weight:700;")
        self._sub.setText(subtitle)


class RangeBarWidget(QWidget):
    def __init__(self, low, price, high, parent=None):
        super().__init__(parent)
        self.low = low
        self.price = price
        self.high = high

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(3)

        self.lbl_low = QLabel(f"${low:,.2f}")
        self.lbl_low.setStyleSheet("color:#D4B896; font-size:11px; font-weight:600; background:transparent;")
        self.lbl_low.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_low.setFixedWidth(58)

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
            f"QProgressBar {{border:1px solid {C['accent']}; border-radius:3px; background:{C['bg']};}}"
            f"QProgressBar::chunk {{background:#C9A84C; border-radius:2px;}}"
        )
        self.bar.setToolTip(f"Low ${low:,.2f}  •  Price ${price:,.2f}  •  High ${high:,.2f}")

        self.lbl_high = QLabel(f"${high:,.2f}")
        self.lbl_high.setStyleSheet("color:#D4B896; font-size:11px; font-weight:600; background:transparent;")
        self.lbl_high.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_high.setFixedWidth(58)

        lay.addWidget(self.lbl_low)
        lay.addWidget(self.bar)
        lay.addWidget(self.lbl_high)

    def update_values(self, low, price, high):
        self.low = low
        self.price = price
        self.high = high
        self.lbl_low.setText(f"${low:,.2f}")
        self.lbl_high.setText(f"${high:,.2f}")
        if high != low:
            norm = int(((price - low) / (high - low)) * 1000)
            norm = max(0, min(1000, norm))
        else:
            norm = 500
        self.bar.setValue(norm)
        self.bar.setToolTip(f"Low ${low:,.2f}  •  Price ${price:,.2f}  •  High ${high:,.2f}")


def _colored_item(text: str, color: str, align=Qt.AlignCenter) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setForeground(QColor(color))
    it.setTextAlignment(align)
    return it


# ── Alpaca WebSocket Client ──────────────────────────────────────────────────
class AlpacaWebSocketClient(QObject):
    connected = Signal()
    disconnected = Signal()
    authenticated = Signal()
    auth_failed = Signal(str)
    message_received = Signal(dict)
    error_occurred = Signal(str)

    def __init__(self, api_key, api_secret, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws = QWebSocket()
        self.ws.connected.connect(self._on_connected)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.textMessageReceived.connect(self._on_message)
        self.ws.errorOccurred.connect(self._on_error)
        self.is_authenticated = False
        self.should_reconnect = False
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._do_reconnect)

    def connect_to_stream(self):
        self.should_reconnect = True
        self.is_authenticated = False
        log.info("Opening WebSocket connection to wss://stream.data.alpaca.markets/v2/iex")
        self.ws.open(QUrl("wss://stream.data.alpaca.markets/v2/iex"))

    def disconnect_stream(self):
        self.should_reconnect = False
        self._reconnect_timer.stop()
        self.ws.close()

    def _do_reconnect(self):
        if self.should_reconnect:
            log.info("Attempting WebSocket auto-reconnect...")
            self.ws.open(QUrl("wss://stream.data.alpaca.markets/v2/iex"))

    def _on_connected(self):
        log.info("WebSocket connected! Authenticating...")
        self.connected.emit()
        self._reconnect_timer.stop()
        auth_msg = {
            "action": "auth",
            "key": self.api_key,
            "secret": self.api_secret
        }
        self.ws.sendTextMessage(json.dumps(auth_msg))

    def _on_disconnected(self):
        log.info("WebSocket disconnected.")
        self.is_authenticated = False
        self.disconnected.emit()
        if self.should_reconnect:
            log.info("Scheduling reconnect in 5 seconds...")
            self._reconnect_timer.start(5000)

    def _on_error(self, *args, **kwargs):
        err_msg = self.ws.errorString()
        log.error(f"WebSocket error: {err_msg}")
        self.error_occurred.emit(err_msg)

    def _on_message(self, message):
        try:
            data = json.loads(message)
            if isinstance(data, list):
                for msg in data:
                    self._handle_msg(msg)
            elif isinstance(data, dict):
                self._handle_msg(data)
        except Exception as e:
            log.error(f"Error parsing WS message: {e}")

    def _handle_msg(self, msg):
        t = msg.get("T")
        if t == "success":
            status = msg.get("msg")
            if status == "authenticated":
                self.is_authenticated = True
                log.info("WebSocket authenticated successfully.")
                self.authenticated.emit()
        elif t == "error":
            code = msg.get("code")
            error_msg = msg.get("msg", "")
            log.error(f"WebSocket server error: {error_msg} (code {code})")
            if code == 401:
                self.should_reconnect = False
                self.auth_failed.emit(error_msg)
            elif code == 406:
                self.should_reconnect = False
                self._reconnect_timer.stop()
                self.error_occurred.emit("Connection limit exceeded (code 406). Please wait 90 seconds before trying again.")
            else:
                self.error_occurred.emit(f"Error {code}: {error_msg}")
        else:
            self.message_received.emit(msg)

    def subscribe(self, symbols):
        if not self.is_authenticated:
            return
        if not symbols:
            return
        sub_msg = {
            "action": "subscribe",
            "trades": symbols,
            "quotes": symbols
        }
        log.info(f"WebSocket sending subscribe for {symbols}")
        self.ws.sendTextMessage(json.dumps(sub_msg))

    def unsubscribe(self, symbols):
        if not self.is_authenticated:
            return
        if not symbols:
            return
        unsub_msg = {
            "action": "unsubscribe",
            "trades": symbols,
            "quotes": symbols
        }
        log.info(f"WebSocket sending unsubscribe for {symbols}")
        self.ws.sendTextMessage(json.dumps(unsub_msg))


# ── Livestream Page Widget ───────────────────────────────────────────────────
class LiveFeedPage(QWidget):
    go_explore = Signal(str)

    def __init__(self, watchlist_panel, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self.watchlist_panel = watchlist_panel
        self.ws_client = AlpacaWebSocketClient(API_KEY, API_SECRET, self)

        self.symbols = []
        self.all_symbols = []
        self._items = {}
        self._rest_request_times = []
        self._ticks_received = 0
        self._stream_start_time = None

        self._build()

        # Connect WebSocket signals
        self.ws_client.connected.connect(self._on_ws_connected)
        self.ws_client.disconnected.connect(self._on_ws_disconnected)
        self.ws_client.authenticated.connect(self._on_ws_authenticated)
        self.ws_client.auth_failed.connect(self._on_ws_auth_failed)
        self.ws_client.message_received.connect(self._on_ws_message_received)
        self.ws_client.error_occurred.connect(self._on_ws_error)

        # Wire Watchlist sync signals
        if self.watchlist_panel:
            self.watchlist_panel.symbols_changed.connect(self.on_watchlist_symbols_changed)
            # Fetch current symbols on startup
            QTimer.singleShot(600, lambda: self.on_watchlist_symbols_changed(self.watchlist_panel.wl_edited_symbols))

        # Stats timer
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats_display)
        self._stats_timer.start(1000)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # Header Title Row
        hdr_title_lay = QHBoxLayout()
        title_lbl = QLabel("📡  LIVESTREAM")
        title_lbl.setStyleSheet(f"font-size:20px; font-weight:700; color:{C['accent']};")
        hdr_title_lay.addWidget(title_lbl)

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"font-size: 18px; color: {C['red']}; margin-left: 6px;")
        hdr_title_lay.addWidget(self._status_dot)

        sub_lbl = QLabel("Real-time IEX feed via WebSockets (Free Tier)")
        sub_lbl.setStyleSheet(f"color: {C['sec']}; font-size: 12px; margin-left: 10px;")
        hdr_title_lay.addWidget(sub_lbl)
        hdr_title_lay.addStretch()
        root.addLayout(hdr_title_lay)

        # Metric cards
        metrics_lay = QHBoxLayout()
        metrics_lay.setSpacing(12)
        self._card_status = MetricCard("STREAM STATUS", "DISCONNECTED", "Not connected", C["red"])
        self._card_symbols = MetricCard("STREAMED SYMBOLS", "0 / 15", "Capped at 15 max", C["muted"])
        self._card_rate = MetricCard("REST API RATE", "0 / 200 RPM", "Safe usage", C["green"])
        self._card_ticks = MetricCard("TOTAL TICK UPDATES", "0", "0.0 ticks/s", C["accent"])
        metrics_lay.addWidget(self._card_status)
        metrics_lay.addWidget(self._card_symbols)
        metrics_lay.addWidget(self._card_rate)
        metrics_lay.addWidget(self._card_ticks)
        root.addLayout(metrics_lay)

        # Warning banner
        self._warning_banner = QLabel()
        self._warning_banner.setStyleSheet(f"""
            background-color: rgba(255, 208, 67, 0.1);
            color: {C['yellow']};
            border: 1px solid {C['yellow']};
            border-radius: 6px;
            padding: 8px 12px;
            font-weight: 600;
            font-size: 11px;
        """)
        self._warning_banner.setVisible(False)
        root.addWidget(self._warning_banner)

        # Controls Row
        ctrl_lay = QHBoxLayout()
        ctrl_lay.setSpacing(8)

        self._wl_name_lbl = QLabel("Active List: —")
        self._wl_name_lbl.setStyleSheet(f"color: {C['accent']}; font-weight: bold; font-size: 13px;")
        ctrl_lay.addWidget(self._wl_name_lbl)

        self._btn_toggle_conn = QPushButton("⚡ Connect Stream")
        self._btn_toggle_conn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['green']};
                color: #0B1628;
                font-weight: bold;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #38bdf8;
            }}
        """)
        self._btn_toggle_conn.clicked.connect(self._toggle_connection)
        ctrl_lay.addWidget(self._btn_toggle_conn)

        self._btn_clear_stats = QPushButton("🧹 Clear Stats")
        self._btn_clear_stats.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['card']};
                color: {C['text']};
                border: 1px solid {C['border']};
                padding: 6px 12px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: {C['hover']};
            }}
        """)
        self._btn_clear_stats.clicked.connect(self._clear_stats)
        ctrl_lay.addWidget(self._btn_clear_stats)

        self._status_lbl = QLabel("Ready")
        self._status_lbl.setStyleSheet(f"color: {C['muted']}; font-style: italic; font-size: 11px;")
        ctrl_lay.addWidget(self._status_lbl)
        ctrl_lay.addStretch()

        ctrl_lay.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Search symbol or name…")
        self._filter_edit.setFixedWidth(160)
        self._filter_edit.setStyleSheet(f"""
            background-color: {C['card']};
            border: 1px solid {C['border']};
            color: {C['text']};
            border-radius: 4px;
            padding: 4px 8px;
        """)
        self._filter_edit.textChanged.connect(self._filter_table)
        ctrl_lay.addWidget(self._filter_edit)
        root.addLayout(ctrl_lay)

        # Table
        self._tbl = QTableWidget()
        self._tbl.setColumnCount(11)
        self._tbl.setHorizontalHeaderLabels([
            "Symbol", "Company Name", "LTP", "Chg %", "Volume", "Price Range",
            "Bid Price", "Bid Size", "Ask Price", "Ask Size", "Ticks"
        ])
        hh = self._tbl.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self._tbl.setColumnWidth(0, 80)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (2, 3, 4, 6, 7, 8, 9, 10):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            self._tbl.setColumnWidth(col, 85)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        self._tbl.setColumnWidth(5, 220)

        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setSortingEnabled(True)
        self._tbl.sortByColumn(0, Qt.AscendingOrder)
        self._tbl.doubleClicked.connect(self._dbl_click)
        self._tbl.setStyleSheet(f"""
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
        """)
        root.addWidget(self._tbl)

        hint = QLabel("💡  Double-click a row to open Stock Sense evaluate · Live price cells flash green/red on updates")
        hint.setStyleSheet(f"color:{C['muted']}; font-size:11px;")
        root.addWidget(hint)

    def on_watchlist_symbols_changed(self, symbols):
        if self.watchlist_panel:
            wl_name = self.watchlist_panel.wl_combo.currentText()
        else:
            wl_name = "Watchlist"
        self._wl_name_lbl.setText(f"Active List: {wl_name}")

        if self.symbols and self.ws_client.is_authenticated:
            self.ws_client.unsubscribe(self.symbols)

        self._items.clear()
        self._tbl.setRowCount(0)

        self.all_symbols = [s.strip().upper() for s in symbols if s.strip()]
        total_len = len(self.all_symbols)

        if total_len > 15:
            self.symbols = self.all_symbols[:15]
            self._warning_banner.setText(
                f"⚠️ Active watchlist has {total_len} symbols. "
                "WebSocket is capped at the first 15 symbols due to Alpaca's free tier limits (30 channels total)."
            )
            self._warning_banner.setVisible(True)
        else:
            self.symbols = self.all_symbols
            self._warning_banner.setVisible(False)

        self._card_symbols.update_data(
            f"{len(self.symbols)} / 15",
            f"Uncapped: {total_len}" if total_len > 15 else "Within limits",
            C["yellow"] if total_len > 15 else C["text"]
        )

        if not self.symbols:
            self._status_lbl.setText("No symbols to stream.")
            return

        self._setup_table_rows()
        self._fetch_initial_snapshots(self.symbols)

    def _setup_table_rows(self):
        self._tbl.setSortingEnabled(False)
        self._tbl.setRowCount(len(self.symbols))
        self._items = {}
        for i, sym in enumerate(self.symbols):
            self._tbl.setRowHeight(i, 32)
            sym_item = QTableWidgetItem(sym)
            sym_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
            sym_item.setForeground(QColor(C["accent"]))
            sym_item.setTextAlignment(Qt.AlignCenter)

            name_item = QTableWidgetItem(COMPANY_NAMES.get(sym, "—"))
            name_item.setForeground(QColor(C["sec"]))
            name_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            bid_p = NumericTableWidgetItem("—", -1.0)
            bid_s = NumericTableWidgetItem("—", -1)
            ask_p = NumericTableWidgetItem("—", -1.0)
            ask_s = NumericTableWidgetItem("—", -1)
            last_p = NumericTableWidgetItem("—", -1.0)
            chg_pct = NumericTableWidgetItem("—", -9999.0)
            last_s = NumericTableWidgetItem("—", -1)
            ticks = NumericTableWidgetItem("0", 0)

            for col, it in [
                (0, sym_item), (1, name_item), (2, last_p), (3, chg_pct),
                (4, last_s), (6, bid_p), (7, bid_s), (8, ask_p), (9, ask_s),
                (10, ticks)
            ]:
                it.setTextAlignment(Qt.AlignCenter if col != 1 else Qt.AlignLeft | Qt.AlignVCenter)
                self._tbl.setItem(i, col, it)

            range_bar = RangeBarWidget(0.0, 0.0, 0.0)
            self._tbl.setCellWidget(i, 5, range_bar)

            self._items[sym] = {
                "row": i,
                "ticks": ticks,
                "tick_count": 0,
                "last_p": last_p,
                "last_s": last_s,
                "chg_pct": chg_pct,
                "bid_p": bid_p,
                "bid_s": bid_s,
                "ask_p": ask_p,
                "ask_s": ask_s,
                "range_bar": range_bar,
                "high": 0.0,
                "low": 0.0,
                "prev_close": 0.0,
                "last_price": 0.0,
                "last_bid": 0.0,
                "last_ask": 0.0,
            }
        self._tbl.setSortingEnabled(True)

    def _fetch_initial_snapshots(self, symbols):
        self._status_lbl.setText("Fetching snapshots...")
        self._rest_request_times.append(datetime.now())
        
        w = Worker(lambda: API.snapshot(symbols), self)
        w.signals.result.connect(lambda res: self._on_snapshot_data(res, symbols))
        w.signals.done.connect(w.signals.deleteLater)
        self.pool.start(w)

    def _on_snapshot_data(self, result, symbols):
        data, err = result
        self._status_lbl.setText("Snapshots loaded.")
        log.info(f"[_on_snapshot_data] Received snapshot results. Data keys: {list(data.keys()) if data else 'None'}, Error: {err}")
        if err:
            log.error(f"Snapshot load error: {err}")
            return
        if not data:
            log.warning("[_on_snapshot_data] Snapshot data is empty.")
            return

        sorting = self._tbl.isSortingEnabled()
        self._tbl.setSortingEnabled(False)

        for sym in symbols:
            try:
                snap = data.get(sym)
                if not snap or not isinstance(snap, dict):
                    log.warning(f"[_on_snapshot_data] No valid snapshot dict for symbol: {sym}")
                    continue
                item_dict = self._items.get(sym)
                if not item_dict:
                    log.warning(f"[_on_snapshot_data] Symbol {sym} not found in _items map")
                    continue

                daily = snap.get("dailyBar") or {}
                prev = snap.get("prevDailyBar") or {}
                trade = snap.get("latestTrade") or {}
                quote = snap.get("latestQuote") or {}

                # Null-safe price extraction
                trade_p = trade.get("p") if isinstance(trade, dict) else None
                daily_c = daily.get("c") if isinstance(daily, dict) else None
                if trade_p is not None:
                    price = float(trade_p)
                elif daily_c is not None:
                    price = float(daily_c)
                else:
                    price = 0.0

                # Null-safe prev close extraction
                prev_c_val = prev.get("c") if isinstance(prev, dict) else None
                if prev_c_val is not None:
                    prev_c = float(prev_c_val)
                else:
                    prev_c = price

                item_dict["prev_close"] = prev_c
                item_dict["last_price"] = price
                log.info(f"[_on_snapshot_data] Symbol {sym}: price={price}, prev_c={prev_c}")

                if price > 0:
                    item_dict["last_p"].setText(f"${price:.2f}")
                    item_dict["last_p"].setData(Qt.EditRole, price)
                    chg = price - prev_c
                    chgp = round((chg / prev_c * 100), 2) if prev_c else 0.0
                    clr = C["green"] if chg >= 0 else C["red"]
                    sign = "+" if chg >= 0 else ""
                    item_dict["chg_pct"].setText(f"{sign}{chgp:.2f}%")
                    item_dict["chg_pct"].setData(Qt.EditRole, chgp)
                    item_dict["chg_pct"].setForeground(QColor(clr))

                daily_v = daily.get("v") if isinstance(daily, dict) else None
                vol = float(daily_v) if daily_v is not None else 0.0
                if vol > 0:
                    vol_s = f"{vol/1e6:.1f}M" if vol >= 1e6 else f"{vol/1e3:.0f}K"
                    item_dict["last_s"].setText(vol_s)
                    item_dict["last_s"].setData(Qt.EditRole, vol)

                hi_val = daily.get("h") if isinstance(daily, dict) else None
                lo_val = daily.get("l") if isinstance(daily, dict) else None
                hi = float(hi_val) if hi_val is not None else price
                lo = float(lo_val) if lo_val is not None else price
                item_dict["high"] = hi
                item_dict["low"] = lo
                item_dict["range_bar"].update_values(lo, price, hi)

                bp_val = quote.get("bp") if isinstance(quote, dict) else None
                bs_val = quote.get("bs") if isinstance(quote, dict) else None
                ap_val = quote.get("ap") if isinstance(quote, dict) else None
                as_val = quote.get("as") if isinstance(quote, dict) else None

                bp = float(bp_val) if bp_val is not None else 0.0
                bs = int(bs_val) if bs_val is not None else 0
                ap = float(ap_val) if ap_val is not None else 0.0
                as_ = int(as_val) if as_val is not None else 0

                item_dict["last_bid"] = bp
                item_dict["last_ask"] = ap

                if bp > 0:
                    item_dict["bid_p"].setText(f"${bp:.2f}")
                    item_dict["bid_p"].setData(Qt.EditRole, bp)
                    item_dict["bid_s"].setText(str(bs))
                    item_dict["bid_s"].setData(Qt.EditRole, bs)
                if ap > 0:
                    item_dict["ask_p"].setText(f"${ap:.2f}")
                    item_dict["ask_p"].setData(Qt.EditRole, ap)
                    item_dict["ask_s"].setText(str(as_))
                    item_dict["ask_s"].setData(Qt.EditRole, as_)
            except Exception as sym_ex:
                log.error(f"[_on_snapshot_data] Error parsing symbol {sym}: {sym_ex}", exc_info=True)

        self._tbl.setSortingEnabled(sorting)

        if self.ws_client.is_authenticated:
            self.ws_client.subscribe(symbols)

    def _toggle_connection(self):
        if self.ws_client.should_reconnect or self.ws_client.is_authenticated:
            self.ws_client.disconnect_stream()
            self._status_lbl.setText("Disconnecting...")
        else:
            self._stream_start_time = datetime.now()
            self._status_lbl.setText("Connecting...")
            self.ws_client.connect_to_stream()

    def _clear_stats(self):
        self._ticks_received = 0
        self._stream_start_time = datetime.now()
        for item_dict in self._items.values():
            item_dict["tick_count"] = 0
            item_dict["ticks"].setText("0")

    def _filter_table(self, query):
        q = query.strip().upper()
        for r in range(self._tbl.rowCount()):
            sym_item = self._tbl.item(r, 0)
            name_item = self._tbl.item(r, 1)
            sym_text = sym_item.text().upper() if sym_item else ""
            name_text = name_item.text().upper() if name_item else ""
            show = (not q) or (q in sym_text) or (q in name_text)
            self._tbl.setRowHidden(r, not show)

    def _update_stats_display(self):
        now = datetime.now()
        self._rest_request_times = [t for t in self._rest_request_times if (now - t).total_seconds() < 60]
        usage = len(self._rest_request_times)
        usage_color = C["green"] if usage < 100 else (C["yellow"] if usage < 170 else C["red"])
        self._card_rate.update_data(f"{usage} / 200 RPM", "REST API usage", usage_color)

        if self.ws_client.is_authenticated or self.ws_client.should_reconnect:
            self._btn_toggle_conn.setText("🛑 Stop Stream")
            self._btn_toggle_conn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {C['red']};
                    color: white;
                    border: none;
                    padding: 6px 12px;
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: #bd3a30;
                }}
            """)
            if self.ws_client.is_authenticated:
                self._status_dot.setStyleSheet(f"font-size: 18px; color: {C['green']}; margin-left: 6px;")
                self._card_status.update_data("CONNECTED", "Streaming IEX feed", C["green"])
                elapsed = (now - self._stream_start_time).total_seconds() if self._stream_start_time else 0
                if elapsed > 1 and self._ticks_received > 0:
                    tps = self._ticks_received / elapsed
                    self._card_ticks.update_data(f"{self._ticks_received:,}", f"{tps:.1f} ticks/s", C["accent"])
                else:
                    self._card_ticks.update_data(f"{self._ticks_received:,}", "0.0 ticks/s", C["accent"])
            else:
                self._status_dot.setStyleSheet(f"font-size: 18px; color: {C['yellow']}; margin-left: 6px;")
                self._card_status.update_data("RECONNECTING", "Reconnecting shortly...", C["yellow"])
                self._card_ticks.update_data(f"{self._ticks_received:,}", "Reconnecting...", C["accent"])
        else:
            self._btn_toggle_conn.setText("⚡ Connect Stream")
            self._btn_toggle_conn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {C['green']};
                    color: #0B1628;
                    font-weight: bold;
                    border: none;
                    padding: 6px 12px;
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: #38bdf8;
                }}
            """)
            self._card_ticks.update_data(f"{self._ticks_received:,}", "Paused", C["accent"])
            self._status_dot.setStyleSheet(f"font-size: 18px; color: {C['red']}; margin-left: 6px;")
            self._card_status.update_data("DISCONNECTED", "Stream offline", C["red"])

    def _on_ws_connected(self):
        self._status_lbl.setText("WebSocket connected. Authenticating...")

    def _on_ws_disconnected(self):
        self._status_lbl.setText("WebSocket disconnected.")

    def _on_ws_authenticated(self):
        self._status_lbl.setText("Authenticated. Subscribing to feed...")
        self._stream_start_time = datetime.now()
        if self.symbols:
            self.ws_client.subscribe(self.symbols)

    def _on_ws_auth_failed(self, reason):
        self._status_lbl.setText(f"Authentication failed: {reason}")
        QMessageBox.critical(self, "Auth Failed", f"Alpaca WebSocket authentication failed:\n{reason}")

    def _on_ws_error(self, err_msg):
        self._status_lbl.setText(f"WS Error: {err_msg}")

    def _on_ws_message_received(self, msg):
        t = msg.get("T")
        S = msg.get("S")
        if not S or S not in self._items:
            return
            
        item_dict = self._items[S]

        sorting = self._tbl.isSortingEnabled()
        self._tbl.setSortingEnabled(False)

        item_dict["tick_count"] += 1
        item_dict["ticks"].setText(str(item_dict["tick_count"]))
        item_dict["ticks"].setData(Qt.EditRole, item_dict["tick_count"])
        self._ticks_received += 1

        if t == "t":
            p = float(msg.get("p", 0))
            s = int(msg.get("s", 0))
            if p <= 0:
                self._tbl.setSortingEnabled(sorting)
                return

            item_dict["last_p"].setText(f"${p:.2f}")
            item_dict["last_p"].setData(Qt.EditRole, p)
            item_dict["last_s"].setText(f"{s}")
            item_dict["last_s"].setData(Qt.EditRole, s)

            old_p = item_dict["last_price"]
            if old_p > 0 and p != old_p:
                flash_clr = QColor(63, 185, 80, 80) if p > old_p else QColor(248, 81, 73, 80)
                self._flash_item(item_dict["last_p"], flash_clr)
            item_dict["last_price"] = p

            prev_c = item_dict["prev_close"]
            if prev_c > 0:
                chg = p - prev_c
                chgp = round((chg / prev_c * 100), 2)
                clr = C["green"] if chg >= 0 else C["red"]
                sign = "+" if chg >= 0 else ""
                item_dict["chg_pct"].setText(f"{sign}{chgp:.2f}%")
                item_dict["chg_pct"].setData(Qt.EditRole, chgp)
                item_dict["chg_pct"].setForeground(QColor(clr))

            if item_dict["high"] == 0 or p > item_dict["high"]:
                item_dict["high"] = p
            if item_dict["low"] == 0 or p < item_dict["low"]:
                item_dict["low"] = p
            item_dict["range_bar"].update_values(item_dict["low"], p, item_dict["high"])

        elif t == "q":
            bp = float(msg.get("bp", 0))
            bs = int(msg.get("bs", 0))
            ap = float(msg.get("ap", 0))
            as_ = int(msg.get("as", 0))

            item_dict["bid_p"].setText(f"${bp:.2f}")
            item_dict["bid_p"].setData(Qt.EditRole, bp)
            item_dict["bid_s"].setText(str(bs))
            item_dict["bid_s"].setData(Qt.EditRole, bs)
            item_dict["ask_p"].setText(f"${ap:.2f}")
            item_dict["ask_p"].setData(Qt.EditRole, ap)
            item_dict["ask_s"].setText(str(as_))
            item_dict["ask_s"].setData(Qt.EditRole, as_)

            old_bp = item_dict["last_bid"]
            if old_bp > 0 and bp != old_bp:
                flash_clr = QColor(63, 185, 80, 80) if bp > old_bp else QColor(248, 81, 73, 80)
                self._flash_item(item_dict["bid_p"], flash_clr)
            item_dict["last_bid"] = bp

            old_ap = item_dict["last_ask"]
            if old_ap > 0 and ap != old_ap:
                flash_clr = QColor(63, 185, 80, 80) if ap > old_ap else QColor(248, 81, 73, 80)
                self._flash_item(item_dict["ask_p"], flash_clr)
            item_dict["last_ask"] = ap

        self._tbl.setSortingEnabled(sorting)

    def _flash_item(self, item, color):
        item.setBackground(color)
        QTimer.singleShot(250, lambda: item.setBackground(QColor(Qt.transparent)))

    def _dbl_click(self, idx):
        it = self._tbl.item(idx.row(), 0)
        if it:
            self.go_explore.emit(it.text())
