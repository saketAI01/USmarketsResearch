"""
AI Insights Tab — AI-powered research using Gemini cascade + Perplexity.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QLineEdit, QComboBox, QTextBrowser, QSplitter,
    QListWidget, QListWidgetItem, QApplication
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor
from ..theme import ACCENT, ACCENT2, SUCCESS, DANGER, TEXT_SECONDARY, BG_CARD, BORDER, BG_SURFACE
from datetime import datetime


class AIInsightsTab(QWidget):
    run_analysis = Signal(str, str, str)  # symbol, type, provider

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history = []
        self._build_ui()

    def _build_ui(self):
        main = QHBoxLayout(self)
        main.setContentsMargins(14, 14, 14, 14)
        main.setSpacing(12)

        # --- Left Panel: Controls ---
        left = QFrame()
        left.setObjectName("sidebar")
        left.setFixedWidth(340)
        l_lay = QVBoxLayout(left)
        l_lay.setContentsMargins(14, 14, 14, 14)
        l_lay.setSpacing(10)
        l_lay.setAlignment(Qt.AlignTop)

        title = QLabel("AI INSIGHTS")
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title.setStyleSheet(f"color: {ACCENT2};")
        l_lay.addWidget(title)

        subtitle = QLabel("Powered by Gemini + Perplexity")
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        l_lay.addWidget(subtitle)
        l_lay.addSpacing(8)

        l_lay.addWidget(QLabel("Symbol"))
        self.sym_input = QLineEdit()
        self.sym_input.setPlaceholderText("e.g. AAPL, NVDA, MSFT")
        self.sym_input.setFont(QFont("Segoe UI", 11))
        l_lay.addWidget(self.sym_input)

        l_lay.addWidget(QLabel("Analysis Type"))
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "Comprehensive Analysis",
            "News & Sentiment",
            "Investment Thesis",
            "Peer Comparison",
        ])
        l_lay.addWidget(self.type_combo)

        l_lay.addWidget(QLabel("AI Provider"))
        self.provider_combo = QComboBox()
        self.provider_combo.addItems([
            "Auto (Gemini → Perplexity)",
            "Gemini Only",
            "Perplexity Only",
        ])
        l_lay.addWidget(self.provider_combo)

        l_lay.addSpacing(8)

        self.btn_generate = QPushButton("⚡  GENERATE ANALYSIS")
        self.btn_generate.setMinimumHeight(44)
        self.btn_generate.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.btn_generate.clicked.connect(self._on_generate)
        l_lay.addWidget(self.btn_generate)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.status_lbl.setWordWrap(True)
        l_lay.addWidget(self.status_lbl)

        l_lay.addSpacing(12)
        l_lay.addWidget(QLabel("Analysis History"))
        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(250)
        self.history_list.setStyleSheet(f"""
            QListWidget {{ background: {BG_SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; }}
            QListWidget::item {{ padding: 6px; border-bottom: 1px solid {BORDER}; }}
            QListWidget::item:selected {{ background: rgba(88,166,255,0.15); }}
        """)
        self.history_list.itemClicked.connect(self._load_history)
        l_lay.addWidget(self.history_list)
        l_lay.addStretch()

        main.addWidget(left)

        # --- Right Panel: Output ---
        right = QFrame()
        right.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px;")
        r_lay = QVBoxLayout(right)
        r_lay.setContentsMargins(12, 12, 12, 12)
        r_lay.setSpacing(8)

        top_bar = QHBoxLayout()
        self.output_title = QLabel("Analysis Output")
        self.output_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.output_title.setStyleSheet(f"color: {ACCENT}; border: none;")
        top_bar.addWidget(self.output_title)
        top_bar.addStretch()

        self.btn_copy = QPushButton("📋 Copy")
        self.btn_copy.setProperty("class", "secondary")
        self.btn_copy.setFixedWidth(80)
        self.btn_copy.clicked.connect(self._copy_output)
        top_bar.addWidget(self.btn_copy)
        r_lay.addLayout(top_bar)

        self.output_browser = QTextBrowser()
        self.output_browser.setOpenExternalLinks(True)
        self.output_browser.setPlaceholderText("AI analysis results will appear here...")
        self.output_browser.setFont(QFont("Segoe UI", 10))
        r_lay.addWidget(self.output_browser)

        main.addWidget(right, stretch=1)

    def _on_generate(self):
        raw_text = self.sym_input.text().strip().upper()
        sym = raw_text.split(" - ")[0].strip()
        if not sym:

            self.status_lbl.setText("Enter a symbol first.")
            return
        self.run_analysis.emit(sym, self.type_combo.currentText(), self.provider_combo.currentText())
        self.btn_generate.setEnabled(False)
        self.status_lbl.setText(f"Initializing AI agents for {sym}...")

    def set_output(self, text, symbol="", analysis_type=""):
        """Display analysis result and add to history."""
        self.output_browser.setMarkdown(text)
        self.output_title.setText(f"{symbol} — {analysis_type}")

        if text and symbol:
            entry = {
                "symbol": symbol,
                "type": analysis_type,
                "time": datetime.now().strftime("%H:%M"),
                "text": text,
            }
            self._history.insert(0, entry)
            item = QListWidgetItem(f"{entry['time']}  {symbol} — {analysis_type}")
            self.history_list.insertItem(0, item)

    def _load_history(self, item):
        idx = self.history_list.row(item)
        if 0 <= idx < len(self._history):
            entry = self._history[idx]
            self.output_browser.setMarkdown(entry["text"])
            self.output_title.setText(f"{entry['symbol']} — {entry['type']}")

    def _copy_output(self):
        text = self.output_browser.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.status_lbl.setText("Copied to clipboard!")
