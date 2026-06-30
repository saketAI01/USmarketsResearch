"""CANSLIM Screener Pro — entry point."""
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from ui.styles import STYLESHEET
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CANSLIM Screener Pro")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    window = MainWindow()
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
