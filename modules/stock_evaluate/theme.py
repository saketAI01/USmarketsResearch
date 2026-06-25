from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication

BG_PRIMARY = "#0B1628"
BG_SURFACE = "#12192C"
BG_CARD = "#162240"
BORDER = "#1E3050"
ACCENT = "#00D4FF"
ACCENT2 = "#00F5D4"
SUCCESS = "#22c55e"
DANGER = "#ef4444"
WARNING = "#f59e0b"
TEXT_PRIMARY = "#E2E8F0"
TEXT_SECONDARY = "#94A3B8"

STYLESHEET = f"""
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {BG_PRIMARY};
    border-radius: 4px;
}}
QTabBar::tab {{
    background: {BG_SURFACE};
    color: {TEXT_SECONDARY};
    padding: 10px 22px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-weight: 600;
    min-width: 120px;
}}
QTabBar::tab:selected {{
    background: {ACCENT};
    color: {BG_PRIMARY};
    font-weight: 700;
}}
QTabBar::tab:hover:!selected {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 12px;
    padding: 16px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {ACCENT};
}}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
    padding: 7px 10px;
    background: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
}}
QPushButton {{
    background: {ACCENT};
    color: {BG_PRIMARY};
    font-weight: 700;
    border: none;
    padding: 9px 18px;
    border-radius: 6px;
    font-size: 10pt;
}}
QPushButton:hover {{
    background: #66E6FF;
}}
QPushButton:pressed {{
    background: #00A0CC;
}}
QPushButton:disabled {{
    background: #21262D;
    color: {TEXT_SECONDARY};
}}
QTableWidget {{
    background: {BG_SURFACE};
    alternate-background-color: {BG_CARD};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: rgba(0,212,255,0.15);
    selection-color: {TEXT_PRIMARY};
}}
QHeaderView::section {{
    background: {BG_CARD};
    color: {ACCENT};
    padding: 6px 8px;
    border: none;
    border-bottom: 2px solid {BORDER};
    border-right: 1px solid {BORDER};
    font-weight: 700;
    font-size: 9pt;
}}
QScrollBar:vertical {{
    background: {BG_SURFACE};
    width: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_SECONDARY};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {BG_SURFACE};
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 5px;
}}
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    color: {TEXT_PRIMARY};
    background: {BG_SURFACE};
    font-size: 9pt;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT}, stop:1 #66E6FF);
    border-radius: 5px;
}}
QTextBrowser {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 10px;
    color: {TEXT_PRIMARY};
    font-size: 10pt;
    selection-background-color: {ACCENT};
}}
QLabel {{
    color: {TEXT_PRIMARY};
}}
QCheckBox {{
    color: {TEXT_PRIMARY};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {BG_SURFACE};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QFrame#sidebar, QFrame#topBar {{
    background: {BG_SURFACE};
    border-radius: 8px;
    border: 1px solid {BORDER};
}}
QFrame#card {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 12px;
}}
"""


def apply_palette():
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG_PRIMARY))
    palette.setColor(QPalette.WindowText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Base, QColor(BG_SURFACE))
    palette.setColor(QPalette.AlternateBase, QColor(BG_CARD))
    palette.setColor(QPalette.Text, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Button, QColor(BG_SURFACE))
    palette.setColor(QPalette.ButtonText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor(BG_PRIMARY))
    palette.setColor(QPalette.ToolTipBase, QColor(BG_CARD))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.Link, QColor(ACCENT))
    QApplication.setPalette(palette)
