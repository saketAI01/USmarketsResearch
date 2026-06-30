#!/usr/bin/env python3
"""
Technical Analyst Pro — Launcher
Professional PySide6 technical analysis platform.

Usage:
    python technical_analyst_pro.py
"""

import sys
import os

# ── Ensure app directory is in path ──────────────────────────────────────
APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# ── Check Python version ─────────────────────────────────────────────────
if sys.version_info < (3, 10):
    print(f"ERROR: Python 3.10+ required (found {sys.version})")
    sys.exit(1)

# ── Check PySide6 ────────────────────────────────────────────────────────
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette, QColor, QFont
except ImportError:
    print(
        "ERROR: PySide6 not found.\n"
        "Install with:  pip install PySide6  --break-system-packages"
    )
    sys.exit(1)

APP_NAME    = "Technical Analyst Pro"
APP_VERSION = "1.0.0"
APP_ORG     = "TechnicalAnalystPro"

# ═══════════════════════════════════════════════════════════════════════════
#  DARK THEME STYLESHEET
# ═══════════════════════════════════════════════════════════════════════════
DARK_QSS = """
/* ── Base ─────────────────────────────────────────── */
QMainWindow, QDialog, QWidget {
    background-color: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 10px;
}

/* ── Tabs ─────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #2d2d44;
    background-color: #16213e;
    border-radius: 0 4px 4px 4px;
}
QTabBar::tab {
    background-color: #0f1a2e;
    color: #808090;
    padding: 6px 14px;
    margin-right: 2px;
    border: 1px solid #2d2d44;
    border-bottom: none;
    border-radius: 4px 4px 0 0;
}
QTabBar::tab:selected {
    background-color: #16213e;
    color: #00b4d8;
    border-top: 2px solid #00b4d8;
}
QTabBar::tab:hover:!selected { background-color: #16213e; color: #c0c0e0; }

/* ── GroupBox ─────────────────────────────────────── */
QGroupBox {
    border: 1px solid #2d2d44;
    border-radius: 6px;
    margin-top: 14px;
    padding: 8px 6px 6px 6px;
    color: #00b4d8;
    font-weight: bold;
    font-size: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
}

/* ── Buttons ──────────────────────────────────────── */
QPushButton {
    background-color: #0f3460;
    color: #d0d0e8;
    border: 1px solid #2d2d44;
    border-radius: 5px;
    padding: 5px 14px;
    font-weight: 600;
}
QPushButton:hover   { background-color: #1a4880; color: #ffffff; border-color: #00b4d8; }
QPushButton:pressed { background-color: #0a2a50; }
QPushButton:disabled{ background-color: #1a1a2e; color: #404050; border-color: #2a2a3a; }
QPushButton#primary {
    background-color: #e94560;
    color: #ffffff;
    border: none;
}
QPushButton#primary:hover   { background-color: #ff5c7a; }
QPushButton#primary:pressed { background-color: #c73652; }
QPushButton#danger  { background-color: #5a1020; color: #e94560; }
QPushButton#danger:hover { background-color: #7a1530; }

/* ── Inputs ───────────────────────────────────────── */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #0f1a2e;
    color: #e0e0e0;
    border: 1px solid #2d2d44;
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #0f3460;
}
QLineEdit:focus, QComboBox:focus { border-color: #00b4d8; }
QComboBox::drop-down { border: none; width: 22px; background: transparent; }
QComboBox QAbstractItemView {
    background-color: #16213e;
    color: #e0e0e0;
    selection-background-color: #0f3460;
    border: 1px solid #2d2d44;
}

/* ── Lists & Tables ───────────────────────────────── */
QListWidget, QTreeWidget, QTableWidget {
    background-color: #0f1a2e;
    color: #e0e0e0;
    border: 1px solid #2d2d44;
    border-radius: 4px;
    alternate-background-color: #121828;
    gridline-color: #2d2d44;
}
QListWidget::item:selected, QTableWidget::item:selected {
    background-color: #0f3460;
    color: #00b4d8;
}
QListWidget::item:hover { background-color: #16213e; }
QHeaderView::section {
    background-color: #0f3460;
    color: #00b4d8;
    border: 1px solid #2d2d44;
    padding: 4px;
    font-weight: bold;
}

/* ── ScrollBar ────────────────────────────────────── */
QScrollBar:vertical { background: #0f1a2e; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #2d2d44; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #0f3460; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #0f1a2e; height: 8px; border-radius: 4px; }
QScrollBar::handle:horizontal { background: #2d2d44; border-radius: 4px; }

/* ── Text / Edit ──────────────────────────────────── */
QTextEdit, QPlainTextEdit {
    background-color: #0f1a2e;
    color: #d0d0e0;
    border: 1px solid #2d2d44;
    border-radius: 4px;
    selection-background-color: #0f3460;
}

/* ── Splitter ─────────────────────────────────────── */
QSplitter::handle          { background: #2d2d44; }
QSplitter::handle:horizontal { width: 3px; }
QSplitter::handle:vertical   { height: 3px; }
QSplitter::handle:hover    { background: #00b4d8; }

/* ── Progress bar ─────────────────────────────────── */
QProgressBar {
    background-color: #0f1a2e;
    border: none;
    border-radius: 2px;
}
QProgressBar::chunk { background-color: #00b4d8; border-radius: 2px; }

/* ── Status bar / Menu / Toolbar ──────────────────── */
QStatusBar             { background-color: #0f1a2e; color: #808090; }
QMenuBar               { background-color: #0f1a2e; color: #d0d0e0; }
QMenuBar::item:selected { background-color: #0f3460; }
QMenu { background-color: #16213e; color: #d0d0e0; border: 1px solid #2d2d44; }
QMenu::item:selected   { background-color: #0f3460; color: #00b4d8; }
QMenu::separator       { background-color: #2d2d44; height: 1px; }
QToolBar               { background-color: #0f1a2e; border-bottom: 1px solid #2d2d44; spacing: 2px; }
QToolBar QToolButton   { background: transparent; color: #c0c0d0; padding: 4px 10px; border-radius: 4px; }
QToolBar QToolButton:hover   { background: #0f3460; color: #ffffff; }
QToolBar QToolButton:pressed { background: #08244a; }

/* ── CheckBox / RadioButton ───────────────────────── */
QCheckBox, QRadioButton { color: #d0d0e0; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 14px; height: 14px;
    border: 1px solid #2d2d44; border-radius: 3px;
    background: #0f1a2e;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: #00b4d8; border-color: #00b4d8;
}

/* ── Label variants ───────────────────────────────── */
QLabel#header  { color: #00b4d8; font-size: 13px; font-weight: bold; }
QLabel#section { color: #808090; font-size: 9px; }

/* ── Frame ────────────────────────────────────────── */
QFrame[frameShape="4"] { color: #2d2d44; }  /* HLine */
QFrame[frameShape="5"] { color: #2d2d44; }  /* VLine */

/* ── ScrollArea ───────────────────────────────────── */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""


def apply_dark_palette(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window,          QColor(26,  26,  46))
    p.setColor(QPalette.WindowText,      QColor(224, 224, 224))
    p.setColor(QPalette.Base,            QColor(15,  26,  46))
    p.setColor(QPalette.AlternateBase,   QColor(22,  33,  62))
    p.setColor(QPalette.Text,            QColor(224, 224, 224))
    p.setColor(QPalette.Button,          QColor(15,  52,  96))
    p.setColor(QPalette.ButtonText,      QColor(224, 224, 224))
    p.setColor(QPalette.Highlight,       QColor(0,   180, 216))
    p.setColor(QPalette.HighlightedText, QColor(26,  26,  46))
    p.setColor(QPalette.ToolTipBase,     QColor(22,  33,  62))
    p.setColor(QPalette.ToolTipText,     QColor(224, 224, 224))
    p.setColor(QPalette.PlaceholderText, QColor(80,  80,  100))
    p.setColor(QPalette.Mid,             QColor(45,  45,  68))
    p.setColor(QPalette.Dark,            QColor(15,  15,  30))
    app.setPalette(p)


def check_and_install_packages():
    """Attempt silent pip installs for missing packages."""
    required = {
        "yfinance":     "yfinance>=0.2.36",
        "matplotlib":   "matplotlib>=3.8.0",
        "mplfinance":   "mplfinance",
        "requests":     "requests>=2.31.0",
        "numpy":        "numpy>=1.26.0",
        "pandas":       "pandas>=2.1.0",
    }
    optional = {
        "google.genai": "google-genai>=0.8.0",
        "reportlab":    "reportlab>=4.0.0",
        "PIL":          "Pillow>=10.0.0",
    }
    missing = []
    for mod, pkg in required.items():
        try: __import__(mod)
        except ImportError: missing.append(pkg)

    if missing:
        print(f"Installing missing packages: {missing}")
        import subprocess
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--break-system-packages", "--quiet"
        ] + missing)

    for mod, pkg in optional.items():
        try: __import__(mod)
        except ImportError:
            try:
                import subprocess
                subprocess.check_call([
                    sys.executable, "-m", "pip", "install", "--break-system-packages", "--quiet", pkg
                ], capture_output=True)
            except Exception:
                pass


def main():
    # ── Install deps if needed ────────────────────────────────────────────
    try:
        check_and_install_packages()
    except Exception as e:
        print(f"Warning: package install failed: {e}")

    # ── Create Qt app ─────────────────────────────────────────────────────
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ORG)

    apply_dark_palette(app)
    app.setStyleSheet(DARK_QSS)

    # Default font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # ── Main window ───────────────────────────────────────────────────────
    try:
        from ta_main_window import MainWindow
        window = MainWindow()
        window.showMaximized()   # Start in maximized mode
        window.raise_()
        window.activateWindow()
    except Exception as e:
        import traceback
        QMessageBox.critical(
            None, "Startup Error",
            f"Failed to launch Technical Analyst Pro:\n\n{e}\n\n{traceback.format_exc()}"
        )
        sys.exit(1)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
