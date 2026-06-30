"""
Backtest tab — PySide6 UI for the backtest engine.

Layout
------
* Top configuration row: strategy, mode (single / portfolio / walk-forward),
  universe selector, date range, cost model.
* Run / Stop buttons + live progress bar.
* Three result tabs: Summary, Trades, Equity & Drawdown.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional, Sequence

try:
    import numpy as np  # type: ignore
    import pandas as pd  # type: ignore
    PANDAS_AVAILABLE = True
except Exception:  # pragma: no cover
    PANDAS_AVAILABLE = False

try:
    import matplotlib  # type: ignore
    matplotlib.use("QtAgg")
    from matplotlib.backends.backend_qtagg import (  # type: ignore
        FigureCanvasQTAgg as FigureCanvas,
    )
    from matplotlib.figure import Figure  # type: ignore
    MATPLOTLIB_AVAILABLE = True
except Exception:  # pragma: no cover
    MATPLOTLIB_AVAILABLE = False

from PySide6.QtCore import Qt, QThread, Signal, QDate
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget,
)

from backtest_engine import (
    BacktestConfig, BacktestResult, Backtester, CostModel, SizingRule,
    WalkForwardConfig, run_walk_forward,
)
from backtest_strategies import available_strategies, load_custom_strategies_from_vault
import backtest_metrics as M

# Re-use the colour palette from the main app (same Catppuccin scheme)
COLOR_BG = "#1e1e2e"
COLOR_SURFACE = "#181825"
COLOR_PANEL = "#313244"
COLOR_TEXT = "#cdd6f4"
COLOR_MUTED = "#a6adc8"
COLOR_DIM = "#7f849c"
COLOR_BORDER = "#45475a"
COLOR_ACCENT = "#89b4fa"
COLOR_GREEN = "#a6e3a1"
COLOR_RED = "#f38ba8"
COLOR_AMBER = "#f9e2af"
COLOR_ORANGE = "#fab387"
COLOR_PURPLE = "#cba6f7"
COLOR_TEAL = "#94e2d5"


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------
class BacktestWorker(QThread):
    """Runs the backtest off the UI thread."""

    progress = Signal(str, float)
    finished_single = Signal(object)            # BacktestResult
    finished_walk = Signal(list)                # list[BacktestResult]
    failed = Signal(str)

    def __init__(
        self,
        mode: str,
        strategy_factory,
        tickers: list[str],
        cfg: Any,                                # BacktestConfig or WalkForwardConfig
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.mode = mode
        self.strategy_factory = strategy_factory
        self.tickers = tickers
        self.cfg = cfg

    def run(self) -> None:  # type: ignore[override]
        try:
            strategy = self.strategy_factory()
            if self.mode == "walk_forward":
                cfg: WalkForwardConfig = self.cfg
                results = run_walk_forward(
                    strategy, self.tickers, cfg,
                    progress_cb=lambda msg, pct: self.progress.emit(msg, pct),
                )
                self.finished_walk.emit(results)
                return

            cfg: BacktestConfig = self.cfg
            cfg.progress_cb = lambda msg, pct: self.progress.emit(msg, pct)
            bt = Backtester(strategy, cfg)
            if self.mode == "single":
                res = bt.run_single(self.tickers[0])
            else:
                res = bt.run_portfolio(self.tickers)
            self.finished_single.emit(res)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------
class BacktestTab(QWidget):
    """The full Backtest tab — config + results."""

    def __init__(self, watchlists: dict, base_dir: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self._watchlists = watchlists
        from pathlib import Path as _Path
        self._base_dir = _Path(base_dir) if base_dir else _Path(__file__).resolve().parent
        self._custom_strategies: dict = {}
        self._worker: Optional[BacktestWorker] = None
        self._last_result: Optional[BacktestResult] = None
        self._last_walk: list[BacktestResult] = []
        self._build_ui()
        self._populate_universe_combo()
        self.refresh_strategies()
        self._on_mode_changed(0)

    def refresh_strategies(self) -> None:
        """Re-read vault from disk and rebuild the strategy dropdown.
        Called on construction and again whenever the Strategy Builder
        emits a ``strategies_changed`` signal."""
        try:
            self._custom_strategies = load_custom_strategies_from_vault(self._base_dir)
        except Exception:
            self._custom_strategies = {}
        self._populate_strategy_combo()

    # =====================================================================
    # UI assembly
    # =====================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(8)

        # ----- Config row 1 ------------------------------------------------
        cfg_box = QGroupBox("Configuration")
        cfg_layout = QFormLayout()
        cfg_layout.setLabelAlignment(Qt.AlignRight)
        cfg_box.setLayout(cfg_layout)

        self.strategy_combo = QComboBox()
        self.strategy_combo.currentIndexChanged.connect(self._on_strategy_changed)
        cfg_layout.addRow("Strategy:", self.strategy_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Single ticker", "single")
        self.mode_combo.addItem("Portfolio (watchlist)", "portfolio")
        self.mode_combo.addItem("Walk-forward (robustness)", "walk_forward")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        cfg_layout.addRow("Mode:", self.mode_combo)

        # Ticker line (used in single mode)
        self.ticker_edit = QLineEdit("AAPL")
        cfg_layout.addRow("Ticker:", self.ticker_edit)

        # Universe combo (used in portfolio / walk-forward modes)
        self.universe_combo = QComboBox()
        cfg_layout.addRow("Universe:", self.universe_combo)

        # Date range
        date_row = QHBoxLayout()
        self.start_edit = QDateEdit()
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDate(QDate(date.today().year - 5, 1, 1))
        self.end_edit = QDateEdit()
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDate(QDate.currentDate())
        date_row.addWidget(self.start_edit)
        date_row.addWidget(QLabel("to"))
        date_row.addWidget(self.end_edit)
        date_row.addStretch(1)
        cfg_layout.addRow("Date range:", date_row)

        # Capital + sizing
        capital_row = QHBoxLayout()
        self.cash_spin = QDoubleSpinBox()
        self.cash_spin.setRange(1_000, 100_000_000)
        self.cash_spin.setDecimals(0)
        self.cash_spin.setSingleStep(10_000)
        self.cash_spin.setValue(100_000)
        self.cash_spin.setPrefix("$ ")
        self.cash_spin.setGroupSeparatorShown(True)
        capital_row.addWidget(self.cash_spin)
        capital_row.addWidget(QLabel("·"))
        self.sizing_combo = QComboBox()
        self.sizing_combo.addItem("Fixed fraction", "fixed_fraction")
        self.sizing_combo.addItem("Fixed dollar", "fixed_dollar")
        self.sizing_combo.addItem("Equal weight", "equal_weight")
        self.sizing_combo.currentIndexChanged.connect(self._on_sizing_changed)
        capital_row.addWidget(self.sizing_combo)
        self.sizing_value = QDoubleSpinBox()
        self.sizing_value.setRange(0.001, 10_000_000)
        self.sizing_value.setValue(0.10)
        self.sizing_value.setDecimals(3)
        capital_row.addWidget(self.sizing_value)
        self.max_positions_spin = QSpinBox()
        self.max_positions_spin.setRange(1, 200)
        self.max_positions_spin.setValue(10)
        self.max_positions_spin.setPrefix("max ")
        capital_row.addWidget(self.max_positions_spin)
        cfg_layout.addRow("Capital / sizing:", capital_row)

        # Cost model
        cost_row = QHBoxLayout()
        self.commission_spin = QDoubleSpinBox()
        self.commission_spin.setRange(0, 100)
        self.commission_spin.setDecimals(2)
        self.commission_spin.setValue(0.0)
        self.commission_spin.setPrefix("$ ")
        cost_row.addWidget(QLabel("Commission/trade:"))
        cost_row.addWidget(self.commission_spin)
        self.slippage_spin = QDoubleSpinBox()
        self.slippage_spin.setRange(0, 200)
        self.slippage_spin.setDecimals(1)
        self.slippage_spin.setValue(5.0)
        self.slippage_spin.setSuffix(" bps")
        cost_row.addWidget(QLabel("Slippage:"))
        cost_row.addWidget(self.slippage_spin)
        cost_row.addStretch(1)
        cfg_layout.addRow("Costs:", cost_row)

        # Benchmark
        bench_row = QHBoxLayout()
        self.benchmark_edit = QLineEdit("SPY")
        bench_row.addWidget(self.benchmark_edit)
        bench_row.addWidget(QLabel("Walk-forward windows:"))
        self.wf_windows_spin = QSpinBox()
        self.wf_windows_spin.setRange(2, 20)
        self.wf_windows_spin.setValue(4)
        bench_row.addWidget(self.wf_windows_spin)
        bench_row.addStretch(1)
        cfg_layout.addRow("Benchmark / WF:", bench_row)

        # Strategy parameters (dynamic — repopulated when strategy changes)
        self.strategy_params_group = QGroupBox("Strategy parameters")
        self.strategy_params_layout = QFormLayout()
        self.strategy_params_group.setLayout(self.strategy_params_layout)
        self._param_widgets: dict[str, Any] = {}

        # Buttons
        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("▶  Run Backtest")
        self.run_btn.clicked.connect(self._run)
        btn_row.addWidget(self.run_btn)
        self.stop_btn = QPushButton("⏹  Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)

        # Layout the top half
        top_split = QSplitter(Qt.Horizontal)
        left_box = QWidget()
        left_v = QVBoxLayout(left_box)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.addWidget(cfg_box)
        left_v.addLayout(btn_row)

        right_box = QWidget()
        right_v = QVBoxLayout(right_box)
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.addWidget(self.strategy_params_group)
        right_v.addStretch(1)

        top_split.addWidget(left_box)
        top_split.addWidget(right_box)
        top_split.setStretchFactor(0, 3)
        top_split.setStretchFactor(1, 2)
        root.addWidget(top_split)

        # Progress + status
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMaximumHeight(10)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)
        self.status_label = QLabel("Configure a backtest and click Run.")
        self.status_label.setStyleSheet(f"color:{COLOR_MUTED}; font-size:12px;")
        root.addWidget(self.status_label)

        # ----- Results tabs ------------------------------------------------
        self.result_tabs = QTabWidget()
        self.result_tabs.setDocumentMode(True)

        self.summary_view = QTextBrowser()
        self.summary_view.setOpenExternalLinks(False)
        self.result_tabs.addTab(self.summary_view, "Summary")

        # Trades table
        self.trades_table = QTableWidget(0, 11)
        self.trades_table.setHorizontalHeaderLabels([
            "#", "Ticker", "Entry", "Entry $", "Exit", "Exit $",
            "Qty", "P&L $", "P&L %", "Hold (d)", "Exit Reason",
        ])
        self.trades_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.trades_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.trades_table.setAlternatingRowColors(True)
        self.trades_table.verticalHeader().setVisible(False)
        self.trades_table.horizontalHeader().setStretchLastSection(True)
        self.result_tabs.addTab(self.trades_table, "Trades")

        # Equity / drawdown chart
        chart_widget = QWidget()
        chart_v = QVBoxLayout(chart_widget)
        chart_v.setContentsMargins(0, 0, 0, 0)
        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(8, 6), facecolor=COLOR_BG)
            self.canvas = FigureCanvas(self.figure)
            chart_v.addWidget(self.canvas)
        else:
            chart_v.addWidget(QLabel("matplotlib not installed — install for charts."))
        self.result_tabs.addTab(chart_widget, "Equity & Drawdown")

        # Walk-forward stability
        self.wf_view = QTextBrowser()
        self.result_tabs.addTab(self.wf_view, "Walk-Forward")

        root.addWidget(self.result_tabs, 1)

        # Initialize empty summary
        self._render_empty_summary()

    # =====================================================================
    # Populate combos
    # =====================================================================
    def _populate_universe_combo(self) -> None:
        self.universe_combo.clear()
        for source, sectors in self._watchlists.items():
            total = sum(len(t) for t in sectors.values())
            self.universe_combo.addItem(
                f"All {source}  ({total})", ("all", source),
            )
        for source, sectors in self._watchlists.items():
            for sector, tickers in sectors.items():
                self.universe_combo.addItem(
                    f"    {source}  ·  {sector}  ({len(tickers)})",
                    ("sector", source, sector),
                )

    def _populate_strategy_combo(self) -> None:
        # Remember the current selection so we don't snap back to index 0 on refresh
        current = self.strategy_combo.currentText() if self.strategy_combo.count() else ""
        self.strategy_combo.blockSignals(True)
        self.strategy_combo.clear()
        # Built-ins first
        for name in available_strategies().keys():
            self.strategy_combo.addItem(name)
        # Then custom-rule strategies from the vault. Their display names already
        # start with ★ / ↻ / ✎ so the dropdown stays readable.
        if self._custom_strategies:
            self.strategy_combo.insertSeparator(self.strategy_combo.count())
            for name in self._custom_strategies:
                self.strategy_combo.addItem(name)
        self.strategy_combo.blockSignals(False)
        # Restore prior selection if it still exists
        idx = self.strategy_combo.findText(current) if current else 0
        if idx >= 0:
            self.strategy_combo.setCurrentIndex(idx)
        if self.strategy_combo.count() > 0:
            self._on_strategy_changed(self.strategy_combo.currentIndex())

    def _on_strategy_changed(self, idx: int) -> None:
        # Clear and rebuild parameter widgets
        while self.strategy_params_layout.rowCount() > 0:
            self.strategy_params_layout.removeRow(0)
        self._param_widgets.clear()

        name = self.strategy_combo.currentText()
        # ---- Vault (custom-rule) strategies ----
        if name in self._custom_strategies:
            inst = self._custom_strategies[name]
            buy = (inst.params or {}).get("buy", "")
            sell = (inst.params or {}).get("sell", "")
            note = QLabel(
                "This is a vault strategy. Edit in the <b>Strategy Builder</b> tab."
            )
            note.setTextFormat(Qt.RichText)
            note.setStyleSheet(f"color:{COLOR_AMBER}; font-size:11px;")
            note.setWordWrap(True)
            self.strategy_params_layout.addRow(note)
            for label, expr in (("BUY IF", buy), ("SELL IF", sell)):
                edit = QPlainTextEdit(expr)
                edit.setReadOnly(True)
                edit.setMaximumHeight(60)
                edit.setStyleSheet(
                    f"QPlainTextEdit {{ background:{COLOR_BG}; color:{COLOR_TEXT}; "
                    f"border:1px solid {COLOR_BORDER}; padding:4px; font-family:Consolas,monospace; }}"
                )
                self.strategy_params_layout.addRow(label + ":", edit)
            return

        # ---- Built-in strategies (editable params) ----
        registry = available_strategies()
        if name not in registry:
            return
        cls = registry[name]
        try:
            sample = cls()
            params = getattr(sample, "params", {})
        except Exception:
            params = {}
        for key, val in params.items():
            if isinstance(val, bool):
                w = QCheckBox()
                w.setChecked(val)
            elif isinstance(val, int):
                w = QSpinBox()
                w.setRange(-1_000_000, 1_000_000)
                w.setValue(val)
            elif isinstance(val, float):
                w = QDoubleSpinBox()
                w.setRange(-1_000_000.0, 1_000_000.0)
                w.setDecimals(3)
                w.setValue(val)
            elif isinstance(val, (list, tuple)):
                w = QLineEdit(", ".join(str(x) for x in val))
            else:
                w = QLineEdit(str(val))
            self.strategy_params_layout.addRow(key.replace("_", " ").title() + ":", w)
            self._param_widgets[key] = w

    def _on_mode_changed(self, idx: int) -> None:
        mode = self.mode_combo.currentData()
        is_single = (mode == "single")
        self.ticker_edit.setEnabled(is_single)
        self.universe_combo.setEnabled(not is_single)
        self.wf_windows_spin.setEnabled(mode == "walk_forward")

    def _on_sizing_changed(self, idx: int) -> None:
        mode = self.sizing_combo.currentData()
        if mode == "fixed_fraction":
            self.sizing_value.setRange(0.001, 1.0)
            self.sizing_value.setSingleStep(0.05)
            self.sizing_value.setValue(0.10)
            self.sizing_value.setSuffix("")
        elif mode == "fixed_dollar":
            self.sizing_value.setRange(100, 10_000_000)
            self.sizing_value.setSingleStep(1_000)
            self.sizing_value.setValue(10_000)
            self.sizing_value.setSuffix(" $")
        else:
            self.sizing_value.setRange(0.001, 1.0)
            self.sizing_value.setValue(1.0)
            self.sizing_value.setSuffix("  (not used)")

    # =====================================================================
    # Universe resolution
    # =====================================================================
    def _selected_tickers(self) -> list[str]:
        mode = self.mode_combo.currentData()
        if mode == "single":
            t = self.ticker_edit.text().strip().upper()
            return [t] if t else []
        data = self.universe_combo.currentData()
        if not data:
            return []
        out: list[str] = []
        seen: set[str] = set()
        kind = data[0]
        if kind == "all":
            source = data[1]
            for sec_tickers in self._watchlists[source].values():
                for entry in sec_tickers:
                    sym = entry[0] if isinstance(entry, list) else str(entry)
                    if sym not in seen:
                        seen.add(sym); out.append(sym)
        else:
            _, source, sector = data
            for entry in self._watchlists[source][sector]:
                sym = entry[0] if isinstance(entry, list) else str(entry)
                if sym not in seen:
                    seen.add(sym); out.append(sym)
        return out

    # =====================================================================
    # Run / stop
    # =====================================================================
    def _build_strategy_factory(self):
        name = self.strategy_combo.currentText()
        # Vault entry? Return a factory that yields the prebuilt instance.
        if name in self._custom_strategies:
            inst = self._custom_strategies[name]
            def factory():
                return inst
            return factory
        cls = available_strategies().get(name)
        if cls is None:
            raise RuntimeError(f"Unknown strategy: {name}")
        # Pull current param widget values
        kwargs: dict[str, Any] = {}
        for key, w in self._param_widgets.items():
            if isinstance(w, QCheckBox):
                kwargs[key] = w.isChecked()
            elif isinstance(w, QSpinBox):
                kwargs[key] = int(w.value())
            elif isinstance(w, QDoubleSpinBox):
                kwargs[key] = float(w.value())
            elif isinstance(w, QLineEdit):
                raw = w.text().strip()
                if "," in raw:
                    kwargs[key] = tuple(x.strip() for x in raw.split(",") if x.strip())
                else:
                    kwargs[key] = raw
        def factory():
            try:
                return cls(**kwargs)
            except TypeError:
                # Strategy doesn't accept these kwargs; fall back to defaults
                return cls()
        return factory

    def _build_cfg(self) -> tuple[str, Any, list[str]]:
        mode = self.mode_combo.currentData()
        start = self.start_edit.date().toString("yyyy-MM-dd")
        end = self.end_edit.date().toString("yyyy-MM-dd")
        costs = CostModel(
            commission_per_trade=float(self.commission_spin.value()),
            slippage_bps=float(self.slippage_spin.value()),
        )
        sizing_mode = self.sizing_combo.currentData()
        sizing = SizingRule(
            mode=sizing_mode,
            amount=float(self.sizing_value.value()),
            max_positions=int(self.max_positions_spin.value()),
        )
        tickers = self._selected_tickers()
        if not tickers:
            raise RuntimeError("Pick a ticker or watchlist first.")
        if mode == "walk_forward":
            cfg = WalkForwardConfig(
                start=start, end=end,
                n_windows=int(self.wf_windows_spin.value()),
                starting_cash=float(self.cash_spin.value()),
                costs=costs, sizing=sizing,
                max_positions=int(self.max_positions_spin.value()),
                benchmark=self.benchmark_edit.text().strip() or "SPY",
            )
        else:
            cfg = BacktestConfig(
                start=start, end=end,
                starting_cash=float(self.cash_spin.value()),
                costs=costs, sizing=sizing,
                max_positions=int(self.max_positions_spin.value()),
                benchmark=self.benchmark_edit.text().strip() or "SPY",
            )
        return mode, cfg, tickers

    def _run(self) -> None:
        try:
            mode, cfg, tickers = self._build_cfg()
            strategy_factory = self._build_strategy_factory()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid configuration", str(exc))
            return
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setValue(0)
        self.status_label.setText("Running…")
        self._worker = BacktestWorker(mode, strategy_factory, tickers, cfg, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_single.connect(self._on_finished_single)
        self._worker.finished_walk.connect(self._on_finished_walk)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _stop(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()  # ungraceful but acceptable for compute-bound work
            self.status_label.setText("Stopped.")
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

    def _on_progress(self, msg: str, pct: float) -> None:
        self.status_label.setText(msg)
        self.progress.setValue(int(pct * 100))

    def _on_failed(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setValue(0)
        QMessageBox.critical(self, "Backtest failed", msg)
        self.status_label.setText(f"Failed: {msg}")

    def _on_finished_single(self, result: BacktestResult) -> None:
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setValue(100)
        self._last_result = result
        self._render_summary(result)
        self._render_trades(result)
        self._render_chart(result)
        self.wf_view.setHtml(
            f"<body style='background:{COLOR_SURFACE};color:{COLOR_MUTED};"
            f"font-family:Segoe UI;padding:18px;'>"
            f"Walk-forward results show here when you run in walk-forward mode."
            f"</body>"
        )
        self.status_label.setText(
            f"Done — {len(result.trades)} trades · final equity "
            f"${result.equity_curve.iloc[-1] if len(result.equity_curve) else 0:,.0f}"
        )

    def _on_finished_walk(self, results: list[BacktestResult]) -> None:
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setValue(100)
        self._last_walk = results
        if results:
            # Pick the longest window as the "primary" for the other tabs
            longest = max(results, key=lambda r: len(r.equity_curve))
            self._last_result = longest
            self._render_summary(longest, walk_count=len(results))
            self._render_trades(longest)
            self._render_chart(longest)
        self._render_walk_forward(results)
        self.status_label.setText(f"Walk-forward done — {len(results)} windows.")

    # =====================================================================
    # Rendering
    # =====================================================================
    def _render_empty_summary(self) -> None:
        self.summary_view.setHtml(
            f"<body style='background:{COLOR_SURFACE};color:{COLOR_MUTED};"
            f"font-family:Segoe UI,Arial,sans-serif; padding:20px;'>"
            f"<h2 style='color:{COLOR_ACCENT}; margin-top:0;'>Backtest</h2>"
            f"<p>Pick a strategy, universe, and date range, then click "
            f"<b>Run Backtest</b>. Results stream in on completion.</p>"
            f"<p style='color:{COLOR_MUTED};'>Pro tips, per the backtest-expert "
            f"methodology: spend 80% of your time trying to break a strategy, "
            f"not finding the parameter set that maximises return. Use the "
            f"<i>Walk-Forward</i> mode to test stability across multiple "
            f"non-overlapping windows. Add realistic slippage — strategies "
            f"that survive 10+ bps of slippage are far more likely to work "
            f"in live trading.</p>"
            f"</body>"
        )

    def _render_summary(self, r: BacktestResult, walk_count: int = 0) -> None:
        eq = r.equity_curve
        bench = r.benchmark_curve
        es = M.equity_stats(eq)
        ts = M.trade_stats(r.trades)
        bs = M.benchmark_stats(eq, bench) if bench is not None else None
        exp = M.exposure_pct(r.position_count)

        def row(label: str, val: str, val_color: str = COLOR_TEXT) -> str:
            return (
                f"<tr><td style='padding:4px 14px;color:{COLOR_MUTED};"
                f"border-bottom:1px solid {COLOR_BORDER};'>{label}</td>"
                f"<td style='padding:4px 14px;color:{val_color};font-weight:600;"
                f"border-bottom:1px solid {COLOR_BORDER};'>{val}</td></tr>"
            )

        def color_for(v: float, good_above: float = 0) -> str:
            return COLOR_GREEN if v >= good_above else COLOR_RED

        # Format the monthly heatmap as a small HTML table
        heat = M.monthly_returns_matrix(eq)
        heat_html = ""
        if not heat.empty:
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "YTD"]
            heat_html = (
                f"<h3 style='color:{COLOR_TEAL}; margin-top:18px;'>"
                f"Monthly Returns (%)</h3>"
                f"<table style='border-collapse:collapse; font-size:11px;'>"
                f"<tr><td style='padding:3px 8px;color:{COLOR_MUTED};'></td>"
                + "".join(
                    f"<td style='padding:3px 8px;color:{COLOR_MUTED};text-align:center;'>{m}</td>"
                    for m in months
                ) + "</tr>"
            )
            for year in heat.index:
                heat_html += f"<tr><td style='padding:3px 8px;color:{COLOR_MUTED};'>{year}</td>"
                for j in range(1, 13):
                    v = heat.loc[year, j] if j in heat.columns else None
                    if v is None or (isinstance(v, float) and v != v):
                        heat_html += f"<td style='padding:3px 8px;color:{COLOR_DIM};text-align:center;'>—</td>"
                    else:
                        c = COLOR_GREEN if v >= 0 else COLOR_RED
                        heat_html += (
                            f"<td style='padding:3px 8px;color:{c};text-align:right;'>"
                            f"{v:+.1f}</td>"
                        )
                if "YTD" in heat.columns:
                    v = heat.loc[year, "YTD"]
                    if isinstance(v, float) and v == v:
                        c = COLOR_GREEN if v >= 0 else COLOR_RED
                        heat_html += (
                            f"<td style='padding:3px 8px;color:{c};text-align:right;font-weight:700;'>"
                            f"{v:+.1f}</td>"
                        )
                heat_html += "</tr>"
            heat_html += "</table>"

        ret_rows = (
            row("Start equity", f"${es.start_equity:,.0f}") +
            row("End equity", f"${es.end_equity:,.0f}", color_for(es.end_equity - es.start_equity)) +
            row("Total return", f"{es.total_return_pct:+.2f}%", color_for(es.total_return_pct)) +
            row("CAGR", f"{es.cagr_pct:+.2f}%", color_for(es.cagr_pct)) +
            row("Annualised vol", f"{es.annual_vol_pct:.2f}%") +
            row("Sharpe (rf=0)", f"{es.sharpe:.2f}", color_for(es.sharpe - 1.0)) +
            row("Sortino", f"{es.sortino:.2f}", color_for(es.sortino - 1.0)) +
            row("Max drawdown", f"{es.max_drawdown_pct:.2f}%", COLOR_RED) +
            row("Max DD duration", f"{es.max_dd_duration_days} days") +
            row("Underwater %", f"{es.underwater_pct:.1f}%") +
            row("Best / worst day", f"{es.best_day_pct:+.2f}% / {es.worst_day_pct:+.2f}%") +
            row("Skew / excess kurt", f"{es.skewness:+.2f} / {es.kurtosis_excess:+.2f}") +
            row("Exposure (time in market)", f"{exp:.1f}%")
        )

        trade_rows = (
            row("Total trades", str(ts.total_trades)) +
            row("Win rate", f"{ts.win_rate * 100:.1f}%", color_for(ts.win_rate - 0.5)) +
            row("Winners / losers", f"{ts.winners} / {ts.losers}") +
            row("Avg win", f"{ts.avg_win_pct:+.2f}% (${ts.avg_win_dollars:+,.0f})", COLOR_GREEN) +
            row("Avg loss", f"{ts.avg_loss_pct:+.2f}% (${ts.avg_loss_dollars:+,.0f})", COLOR_RED) +
            row("Largest win / loss", f"{ts.largest_win_pct:+.1f}% / {ts.largest_loss_pct:+.1f}%") +
            row(
                "Profit factor",
                f"{ts.profit_factor:.2f}" if ts.profit_factor != float("inf") else "∞",
                color_for(ts.profit_factor - 1.0),
            ) +
            row("Expectancy / trade", f"{ts.expectancy_pct:+.2f}%", color_for(ts.expectancy_pct)) +
            row("Avg hold (days)", f"{ts.avg_hold_days:.1f}  (median {ts.median_hold_days:.0f})") +
            row("Avg MAE / MFE", f"{ts.avg_mae_pct:+.2f}% / {ts.avg_mfe_pct:+.2f}%")
        )

        bench_html = ""
        if bs is not None:
            bench_html = (
                f"<h3 style='color:{COLOR_TEAL}; margin-top:18px;'>"
                f"vs Benchmark ({r.config.benchmark})</h3>"
                f"<table style='border-collapse:collapse; width:100%;'>"
                + row("Benchmark total return", f"{bs.benchmark_total_return_pct:+.2f}%")
                + row("Benchmark CAGR", f"{bs.benchmark_cagr_pct:+.2f}%")
                + row("Benchmark Sharpe", f"{bs.benchmark_sharpe:.2f}")
                + row("Excess return (strat − bench)",
                      f"{bs.excess_return_pct:+.2f}%", color_for(bs.excess_return_pct))
                + row("Beta", f"{bs.beta:.2f}")
                + row("Alpha (annualised)", f"{bs.alpha_annual_pct:+.2f}%",
                      color_for(bs.alpha_annual_pct))
                + row("Correlation", f"{bs.correlation:+.2f}")
                + row("Tracking error", f"{bs.tracking_error_pct:.2f}%")
                + row("Information ratio", f"{bs.information_ratio:.2f}")
                + "</table>"
            )

        walk_html = ""
        if walk_count:
            walk_html = (
                f"<p style='color:{COLOR_AMBER};'>Showing the longest of "
                f"{walk_count} walk-forward windows. See the Walk-Forward "
                f"tab for stability stats across all windows.</p>"
            )

        html = (
            f"<html><body style='background:{COLOR_SURFACE};color:{COLOR_TEXT};"
            f"font-family:Segoe UI,Arial,sans-serif; padding:18px;'>"
            f"<h2 style='color:{COLOR_ACCENT}; margin-top:0;'>"
            f"{r.strategy_name} — {', '.join(r.tickers[:5])}"
            f"{' …' if len(r.tickers) > 5 else ''}</h2>"
            f"<p style='color:{COLOR_MUTED};'>"
            f"{r.config.start} → {r.config.end} · "
            f"{len(r.tickers)} ticker(s) · commission "
            f"${r.config.costs.commission_per_trade:.2f}/trade · slippage "
            f"{r.config.costs.slippage_bps:.1f} bps</p>"
            f"{walk_html}"
            f"<h3 style='color:{COLOR_TEAL};'>Returns & Risk</h3>"
            f"<table style='border-collapse:collapse; width:100%; font-size:13px;'>"
            f"{ret_rows}</table>"
            f"<h3 style='color:{COLOR_TEAL}; margin-top:18px;'>Trade Stats</h3>"
            f"<table style='border-collapse:collapse; width:100%; font-size:13px;'>"
            f"{trade_rows}</table>"
            f"{bench_html}"
            f"{heat_html}"
            f"</body></html>"
        )
        self.summary_view.setHtml(html)

    def _render_trades(self, r: BacktestResult) -> None:
        self.trades_table.setRowCount(0)
        for i, t in enumerate(r.trades):
            row = self.trades_table.rowCount()
            self.trades_table.insertRow(row)
            self.trades_table.setItem(row, 0, QTableWidgetItem(str(i + 1)))
            tk_item = QTableWidgetItem(t.ticker)
            tk_item.setForeground(QColor(COLOR_ACCENT))
            self.trades_table.setItem(row, 1, tk_item)
            self.trades_table.setItem(row, 2, QTableWidgetItem(str(t.entry_date)))
            self.trades_table.setItem(row, 3, QTableWidgetItem(f"${t.entry_price:,.2f}"))
            self.trades_table.setItem(row, 4, QTableWidgetItem(str(t.exit_date) if t.exit_date else "—"))
            self.trades_table.setItem(row, 5, QTableWidgetItem(
                f"${t.exit_price:,.2f}" if t.exit_price is not None else "—"
            ))
            self.trades_table.setItem(row, 6, QTableWidgetItem(str(t.size)))

            pnl_item = QTableWidgetItem(f"${t.pnl:+,.2f}" if t.pnl is not None else "—")
            pnl_pct_item = QTableWidgetItem(f"{t.pnl_pct:+.2f}%" if t.pnl_pct is not None else "—")
            if t.pnl is not None:
                c = COLOR_GREEN if t.pnl > 0 else COLOR_RED
                pnl_item.setForeground(QColor(c))
                pnl_pct_item.setForeground(QColor(c))
            self.trades_table.setItem(row, 7, pnl_item)
            self.trades_table.setItem(row, 8, pnl_pct_item)
            self.trades_table.setItem(row, 9, QTableWidgetItem(str(t.hold_days)))
            self.trades_table.setItem(row, 10, QTableWidgetItem(t.exit_reason))
        self.trades_table.resizeColumnsToContents()

    def _render_chart(self, r: BacktestResult) -> None:
        if not MATPLOTLIB_AVAILABLE:
            return
        self.figure.clear()
        self.figure.patch.set_facecolor(COLOR_BG)
        gs = self.figure.add_gridspec(3, 1, hspace=0.12, left=0.08, right=0.97, top=0.95, bottom=0.06)
        ax_eq = self.figure.add_subplot(gs[0:2])
        ax_dd = self.figure.add_subplot(gs[2], sharex=ax_eq)

        eq = r.equity_curve.dropna()
        if not eq.empty:
            ax_eq.plot(eq.index, eq.values, color=COLOR_ACCENT, linewidth=1.5, label="Strategy")
        if r.benchmark_curve is not None:
            b = r.benchmark_curve.dropna()
            if not b.empty:
                ax_eq.plot(b.index, b.values, color=COLOR_PURPLE, linewidth=1.0,
                           label=r.config.benchmark, alpha=0.85)
        ax_eq.set_title(f"{r.strategy_name} — equity curve", color=COLOR_TEXT, loc="left", fontsize=11)
        ax_eq.set_ylabel("Equity ($)", color=COLOR_TEXT)
        leg = ax_eq.legend(loc="upper left", fontsize=9, frameon=False)
        for t in leg.get_texts():
            t.set_color(COLOR_TEXT)
        self._style_axes(ax_eq)

        dd = M.rolling_drawdown(eq)
        ax_dd.fill_between(dd.index, dd.values, 0, color=COLOR_RED, alpha=0.45)
        ax_dd.set_ylabel("Drawdown (%)", color=COLOR_TEXT)
        self._style_axes(ax_dd)
        ax_eq.tick_params(labelbottom=False)
        self.canvas.draw_idle()

    def _render_walk_forward(self, results: list[BacktestResult]) -> None:
        if not results:
            self.wf_view.setHtml(
                f"<body style='background:{COLOR_SURFACE};color:{COLOR_MUTED};"
                f"font-family:Segoe UI;padding:18px;'>No walk-forward results.</body>"
            )
            return
        eq_stats_list = [M.equity_stats(r.equity_curve) for r in results]
        ts_list = [M.trade_stats(r.trades) for r in results]
        summary = M.walk_forward_summary(eq_stats_list)

        rows = ""
        for i, (r, es, ts) in enumerate(zip(results, eq_stats_list, ts_list)):
            c = COLOR_GREEN if es.total_return_pct >= 0 else COLOR_RED
            rows += (
                f"<tr>"
                f"<td style='padding:5px 10px;'>{i + 1}</td>"
                f"<td style='padding:5px 10px;color:{COLOR_MUTED};'>"
                f"{r.config.start} → {r.config.end}</td>"
                f"<td style='padding:5px 10px;color:{c};font-weight:600;'>{es.total_return_pct:+.2f}%</td>"
                f"<td style='padding:5px 10px;'>{es.sharpe:.2f}</td>"
                f"<td style='padding:5px 10px;color:{COLOR_RED};'>{es.max_drawdown_pct:.2f}%</td>"
                f"<td style='padding:5px 10px;'>{ts.total_trades}</td>"
                f"<td style='padding:5px 10px;'>{ts.win_rate * 100:.1f}%</td>"
                f"</tr>"
            )

        stab_c = (
            COLOR_GREEN if summary.pct_profitable_windows >= 75
            else COLOR_AMBER if summary.pct_profitable_windows >= 50
            else COLOR_RED
        )

        html = (
            f"<html><body style='background:{COLOR_SURFACE};color:{COLOR_TEXT};"
            f"font-family:Segoe UI,Arial,sans-serif; padding:18px;'>"
            f"<h2 style='color:{COLOR_ACCENT}; margin-top:0;'>Walk-Forward Stability</h2>"
            f"<p style='color:{COLOR_MUTED};'>"
            f"Per the backtest-expert methodology: a strategy that's truly "
            f"robust should be profitable in the majority of independent "
            f"windows, with low variability of returns across them.</p>"
            f"<table style='border-collapse:collapse; width:100%; font-size:13px;'>"
            f"<tr style='color:{COLOR_TEAL};'>"
            f"<th style='padding:6px 10px; text-align:left;'>#</th>"
            f"<th style='padding:6px 10px; text-align:left;'>Window</th>"
            f"<th style='padding:6px 10px; text-align:left;'>Return</th>"
            f"<th style='padding:6px 10px; text-align:left;'>Sharpe</th>"
            f"<th style='padding:6px 10px; text-align:left;'>Max DD</th>"
            f"<th style='padding:6px 10px; text-align:left;'>Trades</th>"
            f"<th style='padding:6px 10px; text-align:left;'>Win %</th>"
            f"</tr>{rows}</table>"
            f"<h3 style='color:{COLOR_TEAL}; margin-top:18px;'>Aggregate</h3>"
            f"<ul>"
            f"<li>Windows: <b>{summary.n_windows}</b></li>"
            f"<li>Avg return: <b>{summary.avg_return_pct:+.2f}%</b> "
            f"(σ {summary.std_return_pct:.2f}%)</li>"
            f"<li>Avg Sharpe: <b>{summary.avg_sharpe:.2f}</b></li>"
            f"<li style='color:{stab_c};'><b>Profitable windows: "
            f"{summary.pct_profitable_windows:.0f}%</b></li>"
            f"<li>Worst window: <span style='color:{COLOR_RED};'>"
            f"{summary.worst_window_return_pct:+.2f}%</span></li>"
            f"<li>Best window: <span style='color:{COLOR_GREEN};'>"
            f"{summary.best_window_return_pct:+.2f}%</span></li>"
            f"</ul>"
            f"</body></html>"
        )
        self.wf_view.setHtml(html)

    @staticmethod
    def _style_axes(ax) -> None:
        ax.set_facecolor(COLOR_SURFACE)
        ax.tick_params(colors=COLOR_MUTED, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(COLOR_BORDER)
        ax.grid(True, alpha=0.15, color=COLOR_BORDER, linestyle="-", linewidth=0.5)
        ax.yaxis.label.set_color(COLOR_TEXT)
        ax.xaxis.label.set_color(COLOR_TEXT)
