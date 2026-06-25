from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QStackedWidget,
    QLabel, QFrame, QSizePolicy, QSpacerItem
)

from modules.finviz_screener import FinvizScreenerWidget
from modules.stock_comparator import StockComparator


class SubtabButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("subtabBtn")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)


class ScreenerPage(QWidget):
    SUBTABS = [
        "Finviz Screener",
        "Stocks Compare",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("screenerPage")

        self._subtab_widgets = {}
        self._subtab_buttons = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── sub-tab bar ─────────────────────────────────────────────
        bar = QFrame()
        bar.setObjectName("subtabBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 4, 8, 4)
        bar_layout.setSpacing(2)

        for name in self.SUBTABS:
            btn = SubtabButton(name)
            btn.clicked.connect(self._switch_subtab)
            self._subtab_buttons[name] = btn
            bar_layout.addWidget(btn)

        bar_layout.addStretch()
        layout.addWidget(bar)

        # ── stacked content ─────────────────────────────────────────
        self.stack = QStackedWidget()
        self.stack.setObjectName("subtabStack")
        layout.addWidget(self.stack, 1)

        # ── build subtabs ───────────────────────────────────────────
        self._build_finviz_screener()
        self._build_stock_comparator()

        # default: first subtab active
        if self.SUBTABS:
            self._subtab_buttons[self.SUBTABS[0]].setChecked(True)

    def _build_finviz_screener(self):
        widget = FinvizScreenerWidget()
        self._subtab_widgets["Finviz Screener"] = widget
        self.stack.addWidget(widget)

    def _build_stock_comparator(self):
        widget = StockComparator()
        self._subtab_widgets["Stocks Compare"] = widget
        self.stack.addWidget(widget)

    def _switch_subtab(self):
        clicked = self.sender()
        name = clicked.text()

        for n, btn in self._subtab_buttons.items():
            btn.setChecked(n == name)

        widget = self._subtab_widgets.get(name)
        if widget:
            self.stack.setCurrentWidget(widget)
