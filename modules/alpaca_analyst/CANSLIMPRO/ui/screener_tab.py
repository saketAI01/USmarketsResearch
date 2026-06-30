"""Screener tab — configure and run CANSLIM screening in a background thread."""
from __future__ import annotations
import time
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QGroupBox, QLineEdit,
    QSpinBox, QDoubleSpinBox, QProgressBar, QTextEdit,
    QCheckBox,
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont

from config import load_settings, save_settings, key_status, get_keys, invalidate_key_cache
import core.cache as cache_module
from core.data_fetcher import fetch_stock_data
from core.canslim_engine import score_canslim


class ScreenerThread(QThread):
    progress     = Signal(int, str)
    log_message  = Signal(str)
    finished_all = Signal(list)

    def __init__(self, tickers: list, settings: dict, parent=None):
        super().__init__(parent)
        self.tickers  = tickers
        self.settings = settings
        self._stop    = False

    def stop(self):
        self._stop = True

    def run(self):
        results = []
        n       = len(self.tickers)
        delay   = float(self.settings.get("delay", 0.4))
        self.log_message.emit(f"Starting screen of {n} tickers…")

        keys = get_keys()
        self.log_message.emit(
            f"  FMP: {'active' if keys.get('FMP_API_KEY') else 'not set (yFinance fallback)'}  |  "
            f"Alpaca: {'active' if (keys.get('ALPACA_KEY_ID') and keys.get('ALPACA_SECRET_KEY')) else 'not set (yFinance fallback)'}"
        )

        for i, ticker in enumerate(self.tickers):
            if self._stop:
                self.log_message.emit("Screening stopped by user.")
                break

            pct = int((i / n) * 100)
            self.progress.emit(pct, ticker)
            self.log_message.emit(f"[{i+1}/{n}] {ticker}…")

            try:
                use_cache = self.settings.get("use_cache", True)
                cache_ttl = float(self.settings.get("cache_ttl_hrs", 12.0))
                sd     = fetch_stock_data(ticker, delay=delay, use_cache=use_cache, cache_ttl_hrs=cache_ttl)
                result = score_canslim(sd)
                src    = f"price:{sd.price_source} | fund:{sd.fundamental_source}"
                cache_note = f" [cached {sd.cached_at}]" if sd.from_cache else ""
                self.log_message.emit(
                    f"  OK  {ticker:12s}  score={result.composite_score:.1f}  "
                    f"({result.rating})  [{src}]{cache_note}"
                )
                for e in sd.errors:
                    self.log_message.emit(f"      ! {e}")
                results.append(result)
            except Exception as ex:
                self.log_message.emit(f"  ERR {ticker}: {ex}")

        self.progress.emit(100, "Complete")
        self.log_message.emit(f"\nDone — {len(results)}/{n} stocks scored.")
        self.finished_all.emit(results)


class ScreenerTab(QWidget):
    screening_complete = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tickers: list[str] = []
        self.settings = load_settings()
        self._thread  = None
        self._build_ui()
        self._refresh_key_status()

    def set_tickers(self, tickers: list[str]):
        self.tickers = tickers
        self._update_ticker_count()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        hdr = QLabel("Screener")
        hdr.setStyleSheet("font-size: 20px; font-weight: bold; color: #ebebf0;")
        layout.addWidget(hdr)

        grid = QGridLayout()
        grid.setSpacing(10)

        # ── API Keys panel ───────────────────────────────────────────────────
        api_grp = QGroupBox("API Keys (auto-loaded from keys.env — edit here to override)")
        api_layout = QVBoxLayout(api_grp)

        # FMP
        fmp_row = QHBoxLayout()
        fmp_row.addWidget(QLabel("FMP API Key:"))
        self.fmp_key_input = QLineEdit(self.settings.get("fmp_api_key", ""))
        self.fmp_key_input.setPlaceholderText("Auto-loaded from keys.env — paste here to override")
        self.fmp_key_input.setEchoMode(QLineEdit.Password)
        fmp_row.addWidget(self.fmp_key_input)
        self.fmp_status = QLabel("")
        self.fmp_status.setFixedWidth(110)
        fmp_row.addWidget(self.fmp_status)
        api_layout.addLayout(fmp_row)

        # Alpaca key ID
        ak_row = QHBoxLayout()
        ak_row.addWidget(QLabel("Alpaca Key ID:"))
        self.alpaca_key_input = QLineEdit(self.settings.get("alpaca_key_id", ""))
        self.alpaca_key_input.setPlaceholderText("Auto-loaded from keys.env")
        self.alpaca_key_input.setEchoMode(QLineEdit.Password)
        ak_row.addWidget(self.alpaca_key_input)
        self.alpaca_status = QLabel("")
        self.alpaca_status.setFixedWidth(110)
        ak_row.addWidget(self.alpaca_status)
        api_layout.addLayout(ak_row)

        # Alpaca secret
        as_row = QHBoxLayout()
        as_row.addWidget(QLabel("Alpaca Secret: "))
        self.alpaca_secret_input = QLineEdit(self.settings.get("alpaca_secret_key", ""))
        self.alpaca_secret_input.setPlaceholderText("Auto-loaded from keys.env")
        self.alpaca_secret_input.setEchoMode(QLineEdit.Password)
        as_row.addWidget(self.alpaca_secret_input)
        api_layout.addLayout(as_row)

        api_layout.addWidget(QLabel(
            "Indian stocks always use yFinance. Alpaca and FMP are used for US stocks only.",
            styleSheet="color: #555; font-size: 11px;",
        ))
        grid.addWidget(api_grp, 0, 0)

        # ── Filter panel ─────────────────────────────────────────────────────
        filt_grp = QGroupBox("Filter Settings")
        filt_layout = QVBoxLayout(filt_grp)

        score_row = QHBoxLayout()
        score_row.addWidget(QLabel("Min composite score:"))
        self.min_score_spin = QSpinBox()
        self.min_score_spin.setRange(0, 100)
        self.min_score_spin.setValue(self.settings.get("min_score", 60))
        self.min_score_spin.setFixedWidth(70)
        score_row.addWidget(self.min_score_spin)
        score_row.addStretch()
        filt_layout.addLayout(score_row)

        max_row = QHBoxLayout()
        max_row.addWidget(QLabel("Max stocks to analyse:"))
        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 500)
        self.max_spin.setValue(self.settings.get("max_stocks", 50))
        self.max_spin.setFixedWidth(80)
        max_row.addWidget(self.max_spin)
        max_row.addStretch()
        filt_layout.addLayout(max_row)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("Request delay (s):"))
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0.1, 5.0)
        self.delay_spin.setSingleStep(0.1)
        self.delay_spin.setValue(self.settings.get("delay", 0.4))
        self.delay_spin.setFixedWidth(80)
        delay_row.addWidget(self.delay_spin)
        delay_row.addStretch()
        filt_layout.addLayout(delay_row)

        self.buy_only_cb = QCheckBox("Buy candidates only in results")
        filt_layout.addWidget(self.buy_only_cb)

        self.use_cache_cb = QCheckBox("Use cached data (avoids re-fetching)")
        self.use_cache_cb.setChecked(True)
        filt_layout.addWidget(self.use_cache_cb)

        ttl_row = QHBoxLayout()
        ttl_row.addWidget(QLabel("Cache TTL (hours):"))
        self.cache_ttl_spin = QDoubleSpinBox()
        self.cache_ttl_spin.setRange(0.5, 72.0)
        self.cache_ttl_spin.setSingleStep(0.5)
        self.cache_ttl_spin.setValue(self.settings.get("cache_ttl_hrs", 12.0))
        self.cache_ttl_spin.setFixedWidth(80)
        ttl_row.addWidget(self.cache_ttl_spin)
        self.btn_clear_cache = QPushButton("Clear Cache")
        self.btn_clear_cache.clicked.connect(self._clear_cache)
        ttl_row.addWidget(self.btn_clear_cache)
        ttl_row.addStretch()
        filt_layout.addLayout(ttl_row)

        grid.addWidget(filt_grp, 0, 1)
        layout.addLayout(grid)

        # Ticker count
        count_row = QHBoxLayout()
        self.ticker_count_label = QLabel("No tickers — go to Watchlist tab first")
        self.ticker_count_label.setStyleSheet("color: #8e8e93; font-size: 12px;")
        count_row.addWidget(self.ticker_count_label)
        count_row.addStretch()
        self.btn_save = QPushButton("Save Settings")
        self.btn_save.clicked.connect(self._save_settings)
        count_row.addWidget(self.btn_save)
        layout.addLayout(count_row)

        # Run / Stop
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Run CANSLIM Screen")
        self.btn_run.setObjectName("primaryBtn")
        self.btn_run.setFixedHeight(40)
        self.btn_run.clicked.connect(self._run_screen)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setFixedHeight(40)
        self.btn_stop.clicked.connect(self._stop_screen)
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_stop)
        layout.addLayout(btn_row)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #8e8e93; font-size: 12px;")
        layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        layout.addWidget(self.progress_bar)

        log_lbl = QLabel("SCREENING LOG")
        log_lbl.setObjectName("sectionHeader")
        layout.addWidget(log_lbl)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Courier", 11))
        self.log.setStyleSheet(
            "background:#0f0f11;color:#8e8e93;border:1px solid #3a3a3c;"
            "border-radius:6px;padding:8px;"
        )
        layout.addWidget(self.log)

    def _refresh_key_status(self):
        ks = key_status()
        if ks["fmp"]:
            self.fmp_status.setText("Active")
            self.fmp_status.setStyleSheet("color:#1d9e75;font-size:11px;font-weight:bold;")
        else:
            self.fmp_status.setText("Not set")
            self.fmp_status.setStyleSheet("color:#555;font-size:11px;")

        if ks["alpaca"]:
            self.alpaca_status.setText("Active")
            self.alpaca_status.setStyleSheet("color:#1d9e75;font-size:11px;font-weight:bold;")
        else:
            self.alpaca_status.setText("Not set")
            self.alpaca_status.setStyleSheet("color:#555;font-size:11px;")

    def _update_ticker_count(self):
        n = len(self.tickers)
        m = self.max_spin.value()
        if n == 0:
            self.ticker_count_label.setText("No tickers — go to Watchlist tab first")
        else:
            self.ticker_count_label.setText(
                f"{n} tickers in watchlist — will analyse {min(n, m)}"
            )

    def _save_settings(self):
        override_fmp    = self.fmp_key_input.text().strip()
        override_ak_id  = self.alpaca_key_input.text().strip()
        override_ak_sec = self.alpaca_secret_input.text().strip()

        self.settings.update({
            "fmp_api_key":       override_fmp,
            "alpaca_key_id":     override_ak_id,
            "alpaca_secret_key": override_ak_sec,
            "delay":             self.delay_spin.value(),
            "min_score":         self.min_score_spin.value(),
            "max_stocks":        self.max_spin.value(),
            "use_cache":         self.use_cache_cb.isChecked(),
            "cache_ttl_hrs":     self.cache_ttl_spin.value(),
        })
        save_settings(self.settings)
        invalidate_key_cache()
        self._refresh_key_status()
        self.progress_label.setText("Settings saved.")

    def _clear_cache(self):
        from PySide6.QtWidgets import QMessageBox
        st = cache_module.stats()
        reply = QMessageBox.question(
            self, "Clear Cache",
            f"Clear {st['count']} cached ticker files ({st['size_mb']} MB)?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            cache_module.clear_all()
            self.progress_label.setText("Cache cleared.")

    def _run_screen(self):
        if not self.tickers:
            self.log.append("No tickers loaded. Add tickers in the Watchlist tab first.")
            return
        self._save_settings()
        tickers = self.tickers[:self.max_spin.value()]

        self.log.clear()
        self.progress_bar.setValue(0)
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)

        self._thread = ScreenerThread(tickers, self.settings, parent=self)
        self._thread.progress.connect(self._on_progress)
        self._thread.log_message.connect(self.log.append)
        self._thread.finished_all.connect(self._on_finished)
        self._thread.start()

    def _stop_screen(self):
        if self._thread:
            self._thread.stop()
        self.btn_stop.setEnabled(False)

    def _on_progress(self, pct: int, ticker: str):
        self.progress_bar.setValue(pct)
        self.progress_label.setText(f"Analysing {ticker}… ({pct}%)")

    def _on_finished(self, results: list):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_label.setText(f"Complete — {len(results)} stocks scored.")

        min_score = self.min_score_spin.value()
        buy_only  = self.buy_only_cb.isChecked()
        filtered  = [r for r in results if r.composite_score >= min_score]
        if buy_only:
            filtered = [r for r in filtered if r.buy_candidate]
        filtered.sort(key=lambda r: r.composite_score, reverse=True)

        self.log.append(
            f"\n{'─'*60}\n"
            f"Threshold ≥{min_score}: {len(filtered)}/{len(results)} candidates"
        )
        self.screening_complete.emit(filtered)
