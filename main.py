import sys
from datetime import datetime, timezone, timedelta

from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, Property, QThreadPool
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QFrame, QSizePolicy, QStackedWidget
)

from modules.screener_page import ScreenerPage
from modules.stock_sense_page import StockSensePage
from modules.stock_evaluate import StockEvaluateWidget
from modules.stock_comparator import CentralWatchlistWidget
from modules.portfolio_page import PortfolioPage
from modules.livestream_page import LiveFeedPage
from modules.dashboard_page import DashboardPage


# ── colour palette ──────────────────────────────────────────────────
NAVY       = "#0B1628"
NAVY_LIGHT = "#162240"
SAPPHIRE   = "#1B7A3D"
GOLD       = "#C5A55A"
NEON_BLUE  = "#00D4FF"
WHITE      = "#E8E8E8"
DARK_BG    = "#091120"


class Sidebar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._width = 200
        self.setObjectName("sidebar")

        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(0, 0, 0, 0)
        self.vbox.setSpacing(0)

        self.content = QWidget()
        self.content.setObjectName("sidebarContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 16, 12, 16)
        self.content_layout.setSpacing(8)

        self.nav_buttons = {}
        for title in ("Dashboard", "Stock Sense", "Screener", "Watchlist", "Live Feed", "Backtest", "Portfolio", "Settings"):
            btn = QPushButton(title)
            btn.setObjectName("sidebarBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.nav_buttons[title] = btn
            self.content_layout.addWidget(btn)
        self.content_layout.addStretch()

        self.vbox.addWidget(self.content)

        self.toggle_btn = QPushButton("\u25C0\u25C0")
        self.toggle_btn.setObjectName("toggleBtn")
        self.toggle_btn.setFixedSize(28, 56)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggle)

        self._anim = QPropertyAnimation(self, b"sidebar_width", self)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.setDuration(220)
        self._collapsed = False

    def _get_sidebar_width(self):
        return self._width

    def _set_sidebar_width(self, w):
        self._width = w
        self.setFixedWidth(w)

    sidebar_width = Property(int, _get_sidebar_width, _set_sidebar_width)

    def toggle(self):
        if self._collapsed:
            self.expand()
        else:
            self.collapse()

    def expand(self):
        self._collapsed = False
        self.content.show()
        self._anim.stop()
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(200)
        self._anim.start()
        self.toggle_btn.setText("\u25C0\u25C0")

    def collapse(self):
        self._collapsed = True
        self.content.show()
        self._anim.stop()
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(0)
        self._anim.start()
        self.toggle_btn.setText("\u25B6\u25B6")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        x = self.width() - self.toggle_btn.width()
        y = (self.height() - self.toggle_btn.height()) // 2
        self.toggle_btn.move(x, y)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("USmarketsResearch")
        self.setMinimumSize(1100, 680)
        self.resize(1400, 850)
        self.setObjectName("mainWindow")

        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── top bar ─────────────────────────────────────────────────
        self.top_bar = QFrame()
        self.top_bar.setObjectName("topBar")
        self.top_bar.setFixedHeight(42)
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(16, 0, 16, 0)

        self.brand = QLabel("Markets Research")
        self.brand.setObjectName("brand")
        sf = QFont("Georgia", 14, QFont.Bold)
        sf.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        self.brand.setFont(sf)
        top_layout.addWidget(self.brand)
        top_layout.addStretch()

        now = datetime.now(timezone.utc)
        et_now = now.astimezone(timezone(timedelta(hours=-4)))
        ist_now = now.astimezone(timezone(timedelta(hours=5, minutes=30)))

        gf = QFont("Consolas", 10)
        gf.setBold(True)

        self.date_lbl = QLabel(et_now.strftime("%a %b %d, %Y"))
        self.date_lbl.setObjectName("dateLbl")
        self.date_lbl.setFont(gf)

        self.et_lbl = QLabel(f"US ET: {et_now.strftime('%I:%M:%S %p').lstrip('0')}")
        self.et_lbl.setObjectName("timeLbl")
        self.et_lbl.setFont(gf)

        self.ist_lbl = QLabel(f"IST: {ist_now.strftime('%I:%M:%S %p').lstrip('0')}")
        self.ist_lbl.setObjectName("timeLbl")
        self.ist_lbl.setFont(gf)

        sep1 = QLabel("|")
        sep1.setObjectName("sep")
        sep1.setFont(gf)

        sep2 = QLabel("|")
        sep2.setObjectName("sep")
        sep2.setFont(gf)

        top_layout.addWidget(self.date_lbl)
        top_layout.addWidget(sep1)
        top_layout.addWidget(self.et_lbl)
        top_layout.addWidget(sep2)
        top_layout.addWidget(self.ist_lbl)

        main_layout.addWidget(self.top_bar)

        # ── body ─────────────────────────────────────────────────────
        body = QWidget()
        body.setObjectName("body")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.setFixedWidth(200)
        body_layout.addWidget(self.sidebar)

        # content pane
        self.content_pane = QStackedWidget()
        self.content_pane.setObjectName("contentPane")

        self._pages = {}
        for name in ("Dashboard", "Stock Sense", "Screener", "Watchlist", "Live Feed", "Backtest", "Portfolio", "Settings"):
            if name == "Dashboard":
                page = DashboardPage()
            elif name == "Screener":
                page = ScreenerPage()
            elif name == "Stock Sense":
                evaluate_widget = StockEvaluateWidget()
                page = StockSensePage(evaluate_widget)
            elif name == "Watchlist":
                page = CentralWatchlistWidget()
            elif name == "Live Feed":
                watchlist_page = self._pages.get("Watchlist")
                page = LiveFeedPage(watchlist_page)
            elif name == "Portfolio":
                page = PortfolioPage()
            else:
                page = QLabel(f"{name} — modules will populate this pane.")
                page.setObjectName("placeholder")
                page.setAlignment(Qt.AlignCenter)
            self._pages[name] = page
            self.content_pane.addWidget(page)

        body_layout.addWidget(self.content_pane, 1)
        main_layout.addWidget(body, 1)

        # ── wire watchlist sync signals ──────────────────────────────
        screener_page = self._pages.get("Screener")
        stock_sense_page = self._pages.get("Stock Sense")
        central_wl_page = self._pages.get("Watchlist")
        if screener_page and stock_sense_page and central_wl_page:
            stock_comp = screener_page._subtab_widgets.get("Stocks Compare")
            stock_eval = stock_sense_page._stock_evaluate_widget
            finviz_screener = screener_page._subtab_widgets.get("Finviz Screener")
            stock_adviser = getattr(stock_sense_page, "stock_adviser_widget", None)
            
            if stock_comp and stock_eval:
                # When stock_eval changes watchlists -> refresh central_wl and stock_comp
                stock_eval.watchlist_changed.connect(central_wl_page.refresh_watchlists)
                stock_eval.watchlist_changed.connect(stock_comp.refresh_watchlists)
                if stock_adviser:
                    stock_eval.watchlist_changed.connect(stock_adviser.handle_external_watchlist_change)
                
                # When central_wl changes watchlists -> refresh stock_eval and stock_comp
                central_wl_page.watchlist_changed.connect(stock_eval.handle_external_watchlist_change)
                central_wl_page.watchlist_changed.connect(stock_comp.refresh_watchlists)
                if stock_adviser:
                    central_wl_page.watchlist_changed.connect(stock_adviser.handle_external_watchlist_change)
            
            if finviz_screener and stock_eval:
                # When finviz_screener changes watchlists -> refresh central_wl, stock_comp, and stock_eval
                finviz_screener.watchlist_changed.connect(central_wl_page.refresh_watchlists)
                finviz_screener.watchlist_changed.connect(stock_comp.refresh_watchlists)
                finviz_screener.watchlist_changed.connect(stock_eval.handle_external_watchlist_change)
                if stock_adviser:
                    finviz_screener.watchlist_changed.connect(stock_adviser.handle_external_watchlist_change)
                    
            if stock_adviser:
                # When stock_adviser changes watchlists -> refresh central_wl, stock_comp, and stock_eval
                stock_adviser.watchlist_changed.connect(central_wl_page.refresh_watchlists)
                stock_adviser.watchlist_changed.connect(stock_comp.refresh_watchlists)
                stock_adviser.watchlist_changed.connect(stock_eval.handle_external_watchlist_change)

        # Wire go_explore signals to evaluate stock in Stock Sense tab
        dashboard_page = self._pages.get("Dashboard")
        if dashboard_page:
            dashboard_page.go_explore.connect(self._open_stock_sense)

        live_feed_page = self._pages.get("Live Feed")
        if live_feed_page:
            live_feed_page.go_explore.connect(self._open_stock_sense)
            
        watchlist_page = self._pages.get("Watchlist")
        if watchlist_page:
            watchlist_page.go_explore.connect(self._open_stock_sense)
            
        portfolio_page = self._pages.get("Portfolio")
        if portfolio_page:
            portfolio_page.go_explore.connect(self._open_stock_sense)

        # ── wire sidebar nav ────────────────────────────────────────
        self._nav_map = {btn: name for name, btn in self.sidebar.nav_buttons.items()}
        for btn, name in self._nav_map.items():
            btn.clicked.connect(lambda checked=False, n=name: self._navigate(n))

        # default: show Dashboard
        self._navigate("Dashboard")

        # ── clock ────────────────────────────────────────────────────
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()

    def _navigate(self, name):
        for btn, n in self._nav_map.items():
            btn.setProperty("active", n == name)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        page = self._pages.get(name)
        if page:
            self.content_pane.setCurrentWidget(page)

    def _tick_clock(self):
        now = datetime.now(timezone.utc)
        et_now = now.astimezone(timezone(timedelta(hours=-4)))
        ist_now = now.astimezone(timezone(timedelta(hours=5, minutes=30)))
        self.date_lbl.setText(et_now.strftime("%a %b %d, %Y"))
        self.et_lbl.setText(f"US ET: {et_now.strftime('%I:%M:%S %p').lstrip('0')}")
        self.ist_lbl.setText(f"IST: {ist_now.strftime('%I:%M:%S %p').lstrip('0')}")

    def _open_stock_sense(self, symbol):
        self._navigate("Stock Sense")
        stock_sense_page = self._pages.get("Stock Sense")
        if stock_sense_page:
            stock_sense_page._stock_evaluate_widget._on_open_deep_dive(symbol)

    def closeEvent(self, event):
        print("Closing main window, cleanly disconnecting Alpaca WebSocket stream...")
        try:
            live_feed_page = self._pages.get("Live Feed")
            if live_feed_page:
                live_feed_page.ws_client.disconnect_stream()
        except Exception as e:
            print(f"Failed to disconnect stream on window close: {e}")
        event.accept()


# ── stylesheet ──────────────────────────────────────────────────────
STYLE = f"""
QMainWindow, #centralWidget, #body {{
    background-color: {NAVY};
}}
QWidget {{
    color: {WHITE};
    font-family: "Segoe UI", "Roboto", sans-serif;
    font-size: 12px;
}}

/* ── completer dropdown popup ───────────────────── */
QListView {{
    background-color: #162240;
    color: #E2E8F0;
    border: 1px solid #1E3050;
    selection-background-color: #00D4FF;
    selection-color: #ffffff;
}}
QListView::item {{
    color: #E2E8F0;
    padding: 4px 8px;
}}
QListView::item:selected {{
    background-color: #00D4FF;
    color: #ffffff;
}}
QListView::item:hover {{
    background-color: #1B253F;
    color: #E2E8F0;
}}

/* ── top bar ─────────────────────────────────────── */
#topBar {{
    background-color: {DARK_BG};
    border-bottom: 1px solid {NAVY_LIGHT};
}}
#brand {{
    color: {SAPPHIRE};
}}
#dateLbl, #timeLbl, #sep {{
    color: {GOLD};
}}
#sep {{
    padding: 0 6px;
    color: #4A5568;
}}

/* ── sidebar ─────────────────────────────────────── */
#sidebar {{
    background-color: {NAVY_LIGHT};
    border-right: 1px solid #1E3050;
}}
#sidebarContent {{
    background-color: transparent;
}}
#sidebarBtn {{
    background-color: transparent;
    color: {WHITE};
    border: none;
    border-radius: 6px;
    padding: 10px 14px;
    text-align: left;
    font-size: 13px;
}}
#sidebarBtn:hover {{
    background-color: #1F3A5F;
    color: {NEON_BLUE};
}}
#sidebarBtn:pressed {{
    background-color: #2A4A78;
}}
#sidebarBtn[active="true"] {{
    background-color: #1F3A5F;
    color: {NEON_BLUE};
    border-left: 3px solid {NEON_BLUE};
}}

/* ── toggle button ───────────────────────────────── */
#toggleBtn {{
    background-color: #1A2D4A;
    color: {NEON_BLUE};
    border: 1px solid {NEON_BLUE};
    border-radius: 4px;
    font-size: 10px;
    font-weight: bold;
}}
#toggleBtn:hover {{
    background-color: #2A4A78;
    color: #66E6FF;
}}

/* ── content pane ────────────────────────────────── */
#contentPane {{
    background-color: {NAVY};
    border: none;
}}
#placeholder {{
    color: #4A6A8A;
    font-size: 18px;
}}

/* ── sub-tab bar ─────────────────────────────────── */
#subtabBar {{
    background-color: {DARK_BG};
    border-bottom: 1px solid #1E3050;
}}
#subtabBtn {{
    background-color: transparent;
    color: #6A7A9A;
    border: none;
    border-radius: 4px;
    padding: 6px 16px;
    font-size: 12px;
    font-weight: bold;
}}
#subtabBtn:hover {{
    background-color: #1F3A5F;
    color: {WHITE};
}}
#subtabBtn:checked {{
    background-color: #1F3A5F;
    color: {NEON_BLUE};
    border-bottom: 2px solid {NEON_BLUE};
}}

/* ── finviz screener ─────────────────────────────── */
#finvizScreener {{
    background-color: {NAVY};
}}
#fsToolbar {{
    background-color: {DARK_BG};
    border: 1px solid #1E3050;
    border-radius: 4px;
}}
#fsToolbar QLabel {{
    color: #8A9AB8;
    font-weight: bold;
    background: transparent;
}}
#fsToolbar QComboBox, #fsToolbar QLineEdit {{
    background-color: #0D1B2A;
    border: 1px solid #2A3A5A;
    border-radius: 4px;
    padding: 3px 6px;
    color: {WHITE};
    min-height: 20px;
}}
#fsToolbar QComboBox:hover, #fsToolbar QLineEdit:hover {{
    border-color: #58A6FF;
}}
QPushButton#runBtn {{
    background-color: #1B7A3D;
    color: #FFFFFF;
    border: none;
    border-radius: 4px;
    padding: 5px 14px;
    font-weight: bold;
}}
QPushButton#runBtn:hover {{
    background-color: #23994A;
}}
QPushButton#runBtn:disabled {{
    background-color: #2A3A5A;
    color: #6A7A9A;
}}
QPushButton#resetBtn {{
    background-color: #B33A3A;
    color: #FFFFFF;
    border: none;
    border-radius: 4px;
    padding: 5px 14px;
    font-weight: bold;
}}
QPushButton#resetBtn:hover {{
    background-color: #D14545;
}}
QPushButton#resetBtn:disabled {{
    background-color: #2A3A5A;
    color: #6A7A9A;
}}
QPushButton#neonToggleBtn {{
    background-color: transparent;
    color: {NEON_BLUE};
    border: 1px solid {NEON_BLUE};
    border-radius: 4px;
    padding: 4px 10px;
    font-weight: bold;
    font-size: 11px;
}}
QPushButton#neonToggleBtn:hover {{
    background-color: {NEON_BLUE};
    color: {DARK_BG};
}}
QPushButton#exportBtn, QPushButton#saveWlBtn {{
    background-color: transparent;
    color: {NEON_BLUE};
    border: 1px solid {NEON_BLUE};
    border-radius: 4px;
    padding: 4px 10px;
    font-weight: bold;
}}
QPushButton#exportBtn:hover, QPushButton#saveWlBtn:hover {{
    background-color: {NEON_BLUE};
    color: {DARK_BG};
}}
QPushButton#exportBtn:disabled, QPushButton#saveWlBtn:disabled {{
    border-color: #2A3A5A;
    color: #4A5A7A;
}}

/* ── builder card ────────────────────────────────── */
#fsBuilderCard {{
    background-color: {NAVY_LIGHT};
    border: 1px solid {NEON_BLUE};
    border-radius: 6px;
}}
QPushButton#tabBtn {{
    background-color: transparent;
    color: #6A7A9A;
    border: 1px solid #2A3A5A;
    border-radius: 4px;
    font-weight: bold;
    padding: 4px 12px;
    font-size: 11px;
}}
QPushButton#tabBtn:checked {{
    background-color: #1F3A5F;
    color: {NEON_BLUE};
    border-color: {NEON_BLUE};
}}
#fsFilterContainer {{
    background-color: transparent;
    border: none;
}}
#fsFilterLabel {{
    color: #8A9AB8;
    font-size: 10px;
    font-weight: normal;
}}
#fsFilterContainer QComboBox {{
    background-color: #0D1B2A;
    border: 1px solid #2A3A5A;
    border-radius: 3px;
    padding: 2px 4px;
    color: {WHITE};
    font-size: 10px;
    min-height: 18px;
}}
#fsFilterContainer QComboBox:hover {{
    border-color: #58A6FF;
}}
#fsFilterContainer QComboBox:disabled {{
    background-color: #1A2333;
    border-color: #1A2333;
    color: #3A4A6A;
}}
#fsFilterLabel[disabled_tab="true"] {{
    color: #2A3A5A;
}}

/* ── table ───────────────────────────────────────── */
#fsTable {{
    background-color: #0D1B2A;
    border: 1px solid #1E3050;
    gridline-color: #1E3050;
    border-radius: 4px;
    color: {WHITE};
    font-size: 11px;
}}
#fsTable QHeaderView::section {{
    background-color: {NAVY_LIGHT};
    color: {NEON_BLUE};
    padding: 5px;
    border: 1px solid #1E3050;
    font-weight: bold;
}}
#fsTable::item:selected {{
    background-color: #1F3A5F;
    color: {WHITE};
}}

/* ── status bar ──────────────────────────────────── */
#fsStatusBar {{
    background-color: {DARK_BG};
    border-top: 1px solid #1E3050;
    border-radius: 0;
}}
#fsStatusBar QLineEdit {{
    background-color: #0D1B2A;
    border: 1px solid #2A3A5A;
    border-radius: 4px;
    padding: 3px 6px;
    color: {WHITE};
    font-size: 11px;
}}
#fsStatusBar QLabel {{
    color: #8A9AB8;
    font-size: 11px;
}}

/* ── progress bar ────────────────────────────────── */
QProgressBar {{
    border: 1px solid #2A3A5A;
    border-radius: 3px;
    background-color: #0D1B2A;
    text-align: center;
    color: {NEON_BLUE};
    font-weight: bold;
    font-size: 9px;
    height: 16px;
}}
QProgressBar::chunk {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {NEON_BLUE}, stop:1 #1F6FEB);
    border-radius: 2px;
}}

/* ── scrollbar ───────────────────────────────────── */
QScrollBar:vertical {{
    background-color: #0D1B2A;
    width: 10px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: #2A3A5A;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: #3A5A8A;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""


def main():
    # Suppress UserWarning about missing glyphs in Matplotlib (e.g. Emoji glyphs)
    import warnings
    warnings.filterwarnings("ignore", message=".*Glyph.*missing from font.*")

    # Suppress repeating QFont::setPointSize warnings in Qt console output
    from PySide6.QtCore import qInstallMessageHandler, QtMsgType
    def qt_message_handler(msg_type, context, msg):
        if "QFont::setPointSize" in msg:
            return
        if msg_type in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            sys.stderr.write(f"QtMsg: {msg}\n")
    qInstallMessageHandler(qt_message_handler)

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("USmarketsResearch")
    except Exception:
        pass

    win = MainWindow()
    win.showMaximized()
    win.sidebar.toggle_btn.raise_()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
