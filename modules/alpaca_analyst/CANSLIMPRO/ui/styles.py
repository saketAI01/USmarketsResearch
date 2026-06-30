STYLESHEET = """
QMainWindow, QDialog {
    background: #1c1c1e;
}
QWidget {
    background: #1c1c1e;
    color: #ebebf0;
    font-family: -apple-system, "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}
/* ── Tabs ── */
QTabWidget::pane {
    border: 1px solid #3a3a3c;
    background: #2c2c2e;
    border-radius: 6px;
}
QTabBar {
    background: #1c1c1e;
}
QTabBar::tab {
    background: #1c1c1e;
    color: #8e8e93;
    padding: 10px 22px;
    border: none;
    font-size: 13px;
    min-width: 100px;
}
QTabBar::tab:selected {
    color: #f5a623;
    border-bottom: 2px solid #f5a623;
    background: #2c2c2e;
}
QTabBar::tab:hover:!selected {
    color: #ebebf0;
    background: #2c2c2e;
}
/* ── Buttons ── */
QPushButton {
    background: #2c2c2e;
    color: #ebebf0;
    border: 1px solid #3a3a3c;
    border-radius: 6px;
    padding: 7px 18px;
    font-size: 13px;
}
QPushButton:hover {
    background: #3a3a3c;
    border-color: #555;
}
QPushButton:pressed {
    background: #1c1c1e;
}
QPushButton#primaryBtn {
    background: #f5a623;
    color: #1c1c1e;
    border: none;
    font-weight: bold;
    padding: 9px 24px;
}
QPushButton#primaryBtn:hover {
    background: #e09010;
}
QPushButton#primaryBtn:disabled {
    background: #3a3a3c;
    color: #555;
}
QPushButton#dangerBtn {
    color: #ff6b6b;
    border-color: #993c1d;
}
/* ── Inputs ── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #2c2c2e;
    color: #ebebf0;
    border: 1px solid #3a3a3c;
    border-radius: 5px;
    padding: 6px 10px;
    font-size: 13px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #f5a623;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #2c2c2e;
    border: 1px solid #3a3a3c;
    selection-background-color: #3a3a3c;
}
/* ── Tables ── */
QTableWidget {
    background: #2c2c2e;
    alternate-background-color: #242426;
    gridline-color: #3a3a3c;
    border: none;
    color: #ebebf0;
    font-size: 12px;
}
QTableWidget::item {
    padding: 4px 8px;
}
QTableWidget::item:selected {
    background: #3a3a3c;
    color: #f5a623;
}
QHeaderView::section {
    background: #1c1c1e;
    color: #8e8e93;
    border: none;
    border-right: 1px solid #3a3a3c;
    border-bottom: 1px solid #3a3a3c;
    padding: 6px 8px;
    font-size: 11px;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
/* ── Progress ── */
QProgressBar {
    background: #2c2c2e;
    border: 1px solid #3a3a3c;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: #f5a623;
    border-radius: 4px;
}
/* ── Labels ── */
QLabel#sectionHeader {
    font-size: 11px;
    font-weight: bold;
    color: #8e8e93;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
QLabel#metricValue {
    font-size: 28px;
    font-weight: bold;
    color: #f5a623;
}
QLabel#statusOk  { color: #1d9e75; }
QLabel#statusErr { color: #ff6b6b; }
/* ── GroupBox ── */
QGroupBox {
    border: 1px solid #3a3a3c;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 6px;
    color: #8e8e93;
    font-size: 11px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    top: -6px;
    padding: 0 4px;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
/* ── ScrollBar ── */
QScrollBar:vertical {
    background: #1c1c1e;
    width: 8px;
    border: none;
}
QScrollBar::handle:vertical {
    background: #3a3a3c;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
/* ── Status bar ── */
QStatusBar {
    background: #1c1c1e;
    color: #8e8e93;
    font-size: 11px;
    border-top: 1px solid #3a3a3c;
}
/* ── Splitter ── */
QSplitter::handle {
    background: #3a3a3c;
    width: 1px;
}
/* ── ToolTip ── */
QToolTip {
    background: #2c2c2e;
    color: #ebebf0;
    border: 1px solid #3a3a3c;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
}
"""

SCORE_COLORS = {
    "exceptional_plus": "#f5a623",
    "exceptional":      "#1d9e75",
    "strong":           "#185fa5",
    "above_average":    "#534ab7",
    "average":          "#8e8e93",
    "below_average":    "#993c1d",
}

def score_to_color(score: float) -> str:
    if score >= 90: return "#f5a623"
    if score >= 80: return "#1d9e75"
    if score >= 70: return "#185fa5"
    if score >= 60: return "#534ab7"
    if score >= 50: return "#8e8e93"
    return "#993c1d"

def component_score_color(score: int) -> str:
    if score >= 80: return "#f5a623"
    if score >= 60: return "#1d9e75"
    if score >= 40: return "#185fa5"
    return "#993c1d"
