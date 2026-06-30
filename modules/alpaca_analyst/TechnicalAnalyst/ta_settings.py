#!/usr/bin/env python3
"""
ta_settings.py — Technical Analyst Pro
Settings & credentials wallet dialog + persistent config manager.
"""

from __future__ import annotations
import json, os
from typing import Any, Dict

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
    QComboBox, QCheckBox, QSpinBox, QMessageBox, QFileDialog, QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QIcon


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG MANAGER
# ═══════════════════════════════════════════════════════════════════════════
class ConfigManager:
    """JSON-backed persistent settings & credentials wallet."""

    DEFAULTS: Dict[str, Any] = {
        # Credentials
        "credentials": {
            "alpaca_key":    "",
            "alpaca_secret": "",
            "fmp_key":       "",
            "gemini_key":    "",
            "perplexity_key":"",
        },
        # Data preferences
        "data_source":   "auto",
        "default_interval": "Weekly",
        "default_period":   "2 Years",
        # Display
        "show_ma":       True,
        "show_volume":   True,
        "show_sr":       True,
        "show_bb":       False,
        # AI
        "ai_enabled":    True,
        "use_perplexity":True,
        # Reports
        "reports_dir":   "",
        "auto_save_md":  True,
        # Watchlists
        "watchlists":    {"My Watchlist": ["AAPL","MSFT","NVDA","TSLA","SPY"]},
        "active_watchlist": "My Watchlist",
    }

    def __init__(self, config_dir: str):
        self.config_dir  = config_dir
        self.config_path = os.path.join(config_dir, "settings.json")
        os.makedirs(config_dir, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    saved = json.load(f)
                merged = json.loads(json.dumps(self.DEFAULTS))
                self._deep_merge(merged, saved)
                return merged
            except Exception:
                pass
        # Pre-populate with known API keys from ALLAPI folder
        data = json.loads(json.dumps(self.DEFAULTS))
        self._try_load_api_keys(data)
        return data

    def _try_load_api_keys(self, data: dict):
        """Auto-load API keys from ALLAPI folder if present."""
        proj_dir = os.path.dirname(self.config_dir)
        allapi   = os.path.join(proj_dir, "ALLAPI")
        if not os.path.isdir(allapi):
            return
        key_files = {
            "FMP_API_KEY.txt":       "fmp_key",
            "GEMINI_API_KEY.txt":    "gemini_key",
            "PERPLEXITY_API_KEY.txt":"perplexity_key",
        }
        for fname, cred_key in key_files.items():
            path = os.path.join(allapi, fname)
            if os.path.exists(path):
                try:
                    data["credentials"][cred_key] = open(path).read().strip()
                except Exception:
                    pass
        # Alpaca has two lines
        alpaca_path = os.path.join(allapi, "ALPACA_APISECRET.txt")
        if os.path.exists(alpaca_path):
            try:
                lines = open(alpaca_path).read().strip().splitlines()
                if len(lines) >= 2:
                    data["credentials"]["alpaca_key"]    = lines[0].strip()
                    data["credentials"]["alpaca_secret"] = lines[1].strip()
            except Exception:
                pass

    def _deep_merge(self, base: dict, override: dict):
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v

    def save(self):
        with open(self.config_path, "w") as f:
            json.dump(self._data, f, indent=2)

    # ── accessors ─────────────────────────────────────────────────────────
    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value
        self.save()

    @property
    def credentials(self) -> dict:
        return self._data.get("credentials", {})

    def set_credential(self, key: str, value: str):
        self._data["credentials"][key] = value
        self.save()

    @property
    def watchlists(self) -> dict:
        return self._data.get("watchlists", {})

    def add_watchlist(self, name: str):
        if name not in self._data["watchlists"]:
            self._data["watchlists"][name] = []
            self.save()

    def remove_watchlist(self, name: str):
        self._data["watchlists"].pop(name, None)
        self.save()

    def add_to_watchlist(self, name: str, symbol: str):
        sym = symbol.upper().strip()
        if name in self._data["watchlists"] and sym not in self._data["watchlists"][name]:
            self._data["watchlists"][name].append(sym)
            self.save()

    def remove_from_watchlist(self, name: str, symbol: str):
        sym = symbol.upper().strip()
        lst = self._data["watchlists"].get(name, [])
        if sym in lst:
            lst.remove(sym)
            self.save()


# ═══════════════════════════════════════════════════════════════════════════
#  SETTINGS DIALOG
# ═══════════════════════════════════════════════════════════════════════════
class SettingsDialog(QDialog):
    """Tabbed settings dialog: API Keys | Data | Display | Reports."""

    settings_changed = Signal()

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings & Credentials Wallet")
        self.setMinimumSize(560, 480)
        self.setModal(True)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header
        hdr = QLabel("⚙  Settings & Credentials Wallet")
        hdr.setObjectName("header")
        hdr.setFont(QFont("Segoe UI", 13, QFont.Bold))
        layout.addWidget(hdr)

        # Tabs
        tabs = QTabWidget()
        tabs.addTab(self._api_tab(),     "🔑  API Keys")
        tabs.addTab(self._data_tab(),    "📊  Data")
        tabs.addTab(self._display_tab(), "🎨  Display")
        tabs.addTab(self._reports_tab(), "📄  Reports")
        layout.addWidget(tabs)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ── API Keys tab ──────────────────────────────────────────────────────
    def _api_tab(self) -> QWidget:
        w = QWidget(); vl = QVBoxLayout(w); vl.setSpacing(16)
        creds = self.config.credentials

        self._fields: dict[str, QLineEdit] = {}
        info_lbl = QLabel("🔒 Keys are stored locally in settings.json — never transmitted except to their respective APIs.")
        info_lbl.setWordWrap(True); info_lbl.setStyleSheet("color:#888; font-size:10px;")
        vl.addWidget(info_lbl)

        # Alpaca group
        alp = QGroupBox("Alpaca Markets")
        alp_f = QFormLayout(alp)
        self._fields["alpaca_key"]    = self._secret_field(creds.get("alpaca_key",""))
        self._fields["alpaca_secret"] = self._secret_field(creds.get("alpaca_secret",""))
        alp_f.addRow("API Key:",    self._fields["alpaca_key"])
        alp_f.addRow("API Secret:", self._fields["alpaca_secret"])
        vl.addWidget(alp)

        # FMP group
        fmp = QGroupBox("Financial Modeling Prep (FMP)")
        fmp_f = QFormLayout(fmp)
        self._fields["fmp_key"] = self._secret_field(creds.get("fmp_key",""))
        fmp_f.addRow("API Key:", self._fields["fmp_key"])
        vl.addWidget(fmp)

        # AI group
        ai = QGroupBox("AI Providers")
        ai_f = QFormLayout(ai)
        self._fields["gemini_key"]     = self._secret_field(creds.get("gemini_key",""))
        self._fields["perplexity_key"] = self._secret_field(creds.get("perplexity_key",""))
        ai_f.addRow("Gemini API Key:",     self._fields["gemini_key"])
        ai_f.addRow("Perplexity API Key:", self._fields["perplexity_key"])
        vl.addWidget(ai)

        # Test button
        test_btn = QPushButton("🔍  Test API Connections")
        test_btn.clicked.connect(self._test_apis)
        vl.addWidget(test_btn)
        vl.addStretch()
        return w

    def _secret_field(self, value: str = "") -> QLineEdit:
        from PySide6.QtGui import QAction as _QAction
        le = QLineEdit(value)
        le.setEchoMode(QLineEdit.Password)
        le.setPlaceholderText("Enter API key…")

        # Toggle visibility button — use text action (avoids broken SP_DialogNoButton)
        act = _QAction("👁", le)
        act.setToolTip("Show / hide key")
        def toggle():
            le.setEchoMode(
                QLineEdit.Normal if le.echoMode() == QLineEdit.Password
                else QLineEdit.Password
            )
        act.triggered.connect(toggle)
        le.addAction(act, QLineEdit.TrailingPosition)
        return le

    # ── Data tab ──────────────────────────────────────────────────────────
    def _data_tab(self) -> QWidget:
        w = QWidget(); f = QFormLayout(w)

        self._src_combo = QComboBox()
        self._src_combo.addItems(["auto","yfinance","alpaca","fmp"])
        self._src_combo.setCurrentText(self.config.get("data_source","auto"))
        f.addRow("Primary Data Source:", self._src_combo)

        self._interval_combo = QComboBox()
        self._interval_combo.addItems(["Daily","Weekly","Monthly"])
        self._interval_combo.setCurrentText(self.config.get("default_interval","Weekly"))
        f.addRow("Default Interval:", self._interval_combo)

        self._period_combo = QComboBox()
        self._period_combo.addItems(["3 Months","6 Months","1 Year","2 Years","5 Years","10 Years"])
        self._period_combo.setCurrentText(self.config.get("default_period","2 Years"))
        f.addRow("Default Period:", self._period_combo)

        note = QLabel("'auto' tries yFinance first, then Alpaca, then FMP.")
        note.setStyleSheet("color:#888; font-size:10px;")
        f.addRow("", note)
        return w

    # ── Display tab ───────────────────────────────────────────────────────
    def _display_tab(self) -> QWidget:
        w = QWidget(); vl = QVBoxLayout(w)

        overlays = QGroupBox("Chart Overlays")
        fl = QFormLayout(overlays)
        self._chk_ma  = QCheckBox("Show Moving Averages (MA20/50/200)"); self._chk_ma.setChecked(self.config.get("show_ma",True))
        self._chk_vol = QCheckBox("Show Volume Panel");                  self._chk_vol.setChecked(self.config.get("show_volume",True))
        self._chk_sr  = QCheckBox("Show Support / Resistance Lines");    self._chk_sr.setChecked(self.config.get("show_sr",True))
        self._chk_bb  = QCheckBox("Show Bollinger Bands");               self._chk_bb.setChecked(self.config.get("show_bb",False))
        for chk in [self._chk_ma, self._chk_vol, self._chk_sr, self._chk_bb]:
            fl.addRow(chk)
        vl.addWidget(overlays)
        vl.addStretch()
        return w

    # ── Reports tab ───────────────────────────────────────────────────────
    def _reports_tab(self) -> QWidget:
        w = QWidget(); fl = QFormLayout(w)

        # Reports directory
        dir_row = QHBoxLayout()
        self._reports_dir = QLineEdit(self.config.get("reports_dir",""))
        self._reports_dir.setPlaceholderText("(same folder as app if blank)")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_reports_dir)
        dir_row.addWidget(self._reports_dir); dir_row.addWidget(browse_btn)
        fl.addRow("Reports Folder:", dir_row)

        self._chk_auto_md = QCheckBox("Auto-save Markdown after each analysis")
        self._chk_auto_md.setChecked(self.config.get("auto_save_md",True))
        fl.addRow("", self._chk_auto_md)

        self._chk_ai = QCheckBox("Enable AI Insights (Gemini / Perplexity)")
        self._chk_ai.setChecked(self.config.get("ai_enabled",True))
        fl.addRow("", self._chk_ai)

        self._chk_pp = QCheckBox("Also query Perplexity for second opinion")
        self._chk_pp.setChecked(self.config.get("use_perplexity",True))
        fl.addRow("", self._chk_pp)
        return w

    # ── helpers ───────────────────────────────────────────────────────────
    def _browse_reports_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Reports Folder")
        if d: self._reports_dir.setText(d)

    def _test_apis(self):
        results = []
        key = self._fields.get("fmp_key","").text().strip() if "fmp_key" in self._fields else ""
        # Quick FMP check
        try:
            import requests
            r = requests.get(
                f"https://financialmodelingprep.com/api/v3/quote/AAPL",
                params={"apikey": key}, timeout=8
            )
            results.append(f"FMP: {'✅ OK' if r.status_code==200 else f'❌ {r.status_code}'}")
        except Exception as e:
            results.append(f"FMP: ❌ {e}")

        try:
            import yfinance as yf
            t = yf.Ticker("AAPL")
            p = t.fast_info
            results.append("yFinance: ✅ OK" if p else "yFinance: ❌ No data")
        except Exception as e:
            results.append(f"yFinance: ❌ {e}")

        QMessageBox.information(self, "API Connection Test", "\n".join(results))

    def _save(self):
        # Credentials
        for key, le in self._fields.items():
            self.config.set_credential(key, le.text().strip())
        # Data
        self.config.set("data_source",       self._src_combo.currentText())
        self.config.set("default_interval",  self._interval_combo.currentText())
        self.config.set("default_period",    self._period_combo.currentText())
        # Display
        self.config.set("show_ma",    self._chk_ma.isChecked())
        self.config.set("show_volume",self._chk_vol.isChecked())
        self.config.set("show_sr",    self._chk_sr.isChecked())
        self.config.set("show_bb",    self._chk_bb.isChecked())
        # Reports
        self.config.set("reports_dir",   self._reports_dir.text().strip())
        self.config.set("auto_save_md",  self._chk_auto_md.isChecked())
        self.config.set("ai_enabled",    self._chk_ai.isChecked())
        self.config.set("use_perplexity",self._chk_pp.isChecked())

        self.settings_changed.emit()
        self.accept()
