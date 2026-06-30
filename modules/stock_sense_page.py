from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QStackedWidget,
    QFrame, QSizePolicy
)

from modules.stock_adviser.us_stock_analyzer import StockAnalyzerApp


class SubtabButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("subtabBtn")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)


class StockSensePage(QWidget):
    def __init__(self, stock_evaluate_widget, parent=None):
        super().__init__(parent)
        self.setObjectName("stockSensePage")
        self.SUBTABS = ["Stock Evaluate", "Stock Adviser"]
        self._subtab_widgets = {}
        self._subtab_buttons = {}
        self._stock_evaluate_widget = stock_evaluate_widget
        self.stock_adviser_widget = StockAnalyzerApp()

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

        self._subtab_widgets["Stock Evaluate"] = stock_evaluate_widget
        self.stack.addWidget(stock_evaluate_widget)

        self._subtab_widgets["Stock Adviser"] = self.stock_adviser_widget
        self.stack.addWidget(self.stock_adviser_widget)

        if self.SUBTABS:
            self._subtab_buttons[self.SUBTABS[0]].setChecked(True)

    def _switch_subtab(self):
        clicked = self.sender()
        name = clicked.text()
        for n, btn in self._subtab_buttons.items():
            btn.setChecked(n == name)
        widget = self._subtab_widgets.get(name)
        if widget:
            self.stack.setCurrentWidget(widget)
