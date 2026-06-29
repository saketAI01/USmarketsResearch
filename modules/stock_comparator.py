import sys
import time
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path
import requests

import yfinance as yf
from PySide6.QtCharts import QChartView, QLineSeries, QChart, QDateTimeAxis, QValueAxis
from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker, Qt, QDateTime, QMargins, Slot
from PySide6.QtGui import QPainter, QFont, QColor, QPen, QBrush
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTabWidget,
    QMessageBox,
    QFileDialog,
    QInputDialog,
    QCheckBox,
    QCompleter,
    QApplication,
    QMenu,
)
import pandas as pd
import numpy as np

from modules.stock_evaluate.database import DatabaseManager
from modules.stock_evaluate.config import load_api_keys
from modules.finviz_screener import FILTER_MAPPING, PRESET_STRATEGIES

# Try loading FMP API key
try:
    KEYS = load_api_keys()
    FMP_KEY = KEYS.get("fmp")
except Exception:
    FMP_KEY = None

# --- Path Configuration ---
APP_DIR = Path(__file__).resolve().parent.parent
MASTER_FILE = APP_DIR / "USStockMaster.csv"

CACHE_DIR = Path.home() / ".us_stock_cache"
CACHE_DIR.mkdir(exist_ok=True)

WL_CACHE_DIR = Path.home() / ".us_stock_cache" / "watchlist_data"
WL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

BENCHMARK_SYMBOL = "^GSPC"
BENCHMARKS = {
    "S&P 500": "^GSPC",
    "NASDAQ": "^IXIC"
}

PERIODS = {
    "1 Week": 7,
    "1 Month": 30,
    "6 Months": 180,
    "1 Year": 365,
    "2 Years": 730,
}

MTF_PERIODS = {
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
    "3Y": 1095,
    "5Y": 1825,
}

mutex = QMutex()

# --- Integrated Theme Colors ---
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


def get_cache_path(symbol, period_days, benchmark="^GSPC"):
    period_name = f"{period_days}d"
    b_sym = benchmark.replace("^", "")
    cache_file = CACHE_DIR / f"{symbol}_{b_sym}_{period_name}.parquet"
    return cache_file


def save_to_cache(df, symbol, period_days, benchmark="^GSPC"):
    cache_path = get_cache_path(symbol, period_days, benchmark)
    with QMutexLocker(mutex):
        df.to_parquet(cache_path)


def load_us_master():
    if not MASTER_FILE.exists():
        return []
    try:
        df = pd.read_csv(MASTER_FILE)
        if 'Symbol' in df.columns and 'Company' in df.columns:
            return [f"{row['Symbol']} - {row['Company']}" for _, row in df.iterrows()]
        elif 'Symbol' in df.columns and 'Name' in df.columns:
            return [f"{row['Symbol']} - {row['Name']}" for _, row in df.iterrows()]
    except Exception:
        pass
    return []


def fetch_single_data(symbol, period_days, benchmark="^GSPC", retries=3):
    for attempt in range(retries):
        try:
            cache_path = get_cache_path(symbol, period_days, benchmark)
            
            if cache_path.exists():
                cache_age = time.time() - cache_path.stat().st_mtime
                if cache_age < 3600:
                    with QMutexLocker(mutex):
                        df = pd.read_parquet(cache_path)
                    if 'Symbol_stock' not in df.columns and 'Symbol' in df.columns:
                        df['Symbol_stock'] = symbol
                    if 'Volume' in df.columns:
                        return df
            
            off_market = not is_market_hours()
            timeout_val = 5 if off_market else 15
            
            ticker = yf.Ticker(symbol)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=period_days + 30)
            
            df = ticker.history(start=start_date, end=end_date, interval="1d", auto_adjust=True, timeout=timeout_val)
            
            if df.empty:
                return None
            
            bench_ticker = yf.Ticker(benchmark)
            bench_df = bench_ticker.history(start=start_date, end=end_date, interval="1d", auto_adjust=True, timeout=timeout_val)
            
            df = df.reset_index()
            df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
            
            bench_df = bench_df.reset_index()
            bench_df['Date'] = pd.to_datetime(bench_df['Date']).dt.tz_localize(None)
            
            df['Symbol'] = symbol
            bench_df['Symbol'] = 'SP500'
            
            df['Stock_Return'] = df['Close'].pct_change()
            bench_df['Bench_Return'] = bench_df['Close'].pct_change()
            
            df['Stock_Cumulative'] = (1 + df['Stock_Return']).cumprod() - 1
            bench_df['Bench_Cumulative'] = (1 + bench_df['Bench_Return']).cumprod() - 1
            
            merged = pd.merge(
                df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'Stock_Return', 'Stock_Cumulative', 'Symbol']], 
                bench_df[['Date', 'Close', 'Bench_Return', 'Bench_Cumulative']], 
                on='Date', how='inner', suffixes=('_stock', '_bench')
            )
            
            save_to_cache(merged, symbol, period_days, benchmark)
            
            return merged
        except Exception:
            if attempt < retries - 1:
                time.sleep(1)
            continue
    return None


def get_wl_cache_path(symbol):
    return WL_CACHE_DIR / f"{symbol}.parquet"


def save_wl_cache(df, symbol):
    cache_path = get_wl_cache_path(symbol)
    with QMutexLocker(mutex):
        df_clean = df.drop(columns=['is_cached'], errors='ignore')
        df_clean.to_parquet(cache_path)


def load_wl_cache(symbol):
    cache_path = get_wl_cache_path(symbol)
    if cache_path.exists():
        cache_age = time.time() - cache_path.stat().st_mtime
        if cache_age < 3600:
            with QMutexLocker(mutex):
                return pd.read_parquet(cache_path)
    return None


def is_market_hours():
    try:
        from zoneinfo import ZoneInfo
        now_ny = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: estimate Eastern Time using UTC time.
        # Eastern Time is UTC-5 (EST) or UTC-4 (EDT).
        # We can approximate EDT (UTC-4) between second Sunday in March and first Sunday in November,
        # otherwise EST (UTC-5).
        now_utc = datetime.utcnow()
        month = now_utc.month
        day = now_utc.day
        if 3 < month < 11:
            offset = -4
        elif month == 3 and day >= 14:
            offset = -4
        elif month == 11 and day < 7:
            offset = -4
        else:
            offset = -5
        now_ny = now_utc + timedelta(hours=offset)
        
    if now_ny.weekday() >= 5: # Saturday=5, Sunday=6
        return False
    
    market_start = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    market_end = now_ny.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return market_start <= now_ny <= market_end


def fetch_fmp_historical(symbol, api_key):
    try:
        url = f"https://financialmodelingprep.com/stable/historical-price-full/{symbol}"
        params = {"apikey": api_key}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if "historical" in data and data["historical"]:
            df = pd.DataFrame(data["historical"])
            # Reverse to ascending order
            df = df.iloc[::-1].reset_index(drop=True)
            # Rename columns to match yfinance format
            df = df.rename(columns={
                'open': 'Open',
                'high': 'High',
                'low': 'Low',
                'close': 'Close',
                'volume': 'Volume'
            })
            return df
    except Exception as e:
        print(f"FMP historical fetch error for {symbol}: {e}")
    return None


def calculate_metrics_from_df(symbol, df):
    try:
        if df.empty or len(df) < 2:
            return None
            
        close = df['Close']
        volume = df['Volume']
        current_price = close.iloc[-1]
        prev_close = close.iloc[-2]
        pct_change = ((current_price - prev_close) / prev_close) * 100
        avg_volume = volume.mean()
        
        sma20 = close.rolling(window=20).mean().iloc[-1] if len(close) >= 20 else None
        sma50 = close.rolling(window=50).mean().iloc[-1] if len(close) >= 50 else None
        sma200 = close.rolling(window=200).mean().iloc[-1] if len(close) >= 200 else None
        
        low_20 = df['Low'].rolling(window=20).min().iloc[-1] if len(df) >= 20 else None
        high_20 = df['High'].rolling(window=20).max().iloc[-1] if len(df) >= 20 else None
        
        atr = (df['High'] - df['Low']).rolling(window=14).mean().iloc[-1] if len(df) >= 14 else 0
        support = low_20 - atr if low_20 is not None else None
        resistance = high_20 + atr if high_20 is not None else None
        
        result = pd.DataFrame([{
            'symbol': symbol,
            'price': round(current_price, 2),
            'pct_change': round(pct_change, 2),
            'volume': round(avg_volume, 0),
            'support': round(support, 2) if support is not None else None,
            'resistance': round(resistance, 2) if resistance is not None else None,
            'sma20': round(sma20, 2) if sma20 is not None else None,
            'sma50': round(sma50, 2) if sma50 is not None else None,
            'sma200': round(sma200, 2) if sma200 is not None else None,
        }])
        return result
    except Exception as e:
        print(f"Error calculating metrics for {symbol}: {e}")
        return None


def populate_watchlist_combobox(combobox, db_names, finviz_presets):
    combobox.blockSignals(True)
    current_text = combobox.currentText()
    combobox.clear()
    
    # Custom/database watchlists keep on top
    combobox.addItems(db_names)
    
    separator_index = -1
    if finviz_presets:
        separator_index = combobox.count()
        combobox.addItem("── Finviz Presets ──")
        combobox.addItems(finviz_presets)
        
    if separator_index != -1:
        model = combobox.model()
        item = model.item(separator_index)
        if item:
            item.setEnabled(False)
            
    # Restore selection if possible, skipping separator
    if current_text and current_text != "── Finviz Presets ──" and combobox.findText(current_text) != -1:
        combobox.setCurrentText(current_text)
    else:
        # Default selection logic to avoid selecting the separator
        if combobox.count() > 0:
            if combobox.currentIndex() == separator_index or combobox.currentText() == "── Finviz Presets ──":
                if separator_index + 1 < combobox.count():
                    combobox.setCurrentIndex(separator_index + 1)
                elif separator_index - 1 >= 0:
                    combobox.setCurrentIndex(separator_index - 1)
                else:
                    combobox.setCurrentIndex(0)
                    
    combobox.blockSignals(False)


def fetch_wl_symbol_data(symbol, retries=3):
    cache_path = get_wl_cache_path(symbol)
    if cache_path.exists():
        cache_age = time.time() - cache_path.stat().st_mtime
        if cache_age < 3600:
            try:
                with QMutexLocker(mutex):
                    cached = pd.read_parquet(cache_path)
                if cached is not None and not cached.empty:
                    cached['is_cached'] = True
                    return cached
            except Exception as e:
                print(f"Error reading fresh cache for {symbol}: {e}")
                
    off_market = not is_market_hours()
    
    # If off-market and FMP key is present, try FMP historical first (extremely fast, 24/7)
    if off_market and FMP_KEY:
        df_fmp = fetch_fmp_historical(symbol, FMP_KEY)
        if df_fmp is not None and not df_fmp.empty:
            result = calculate_metrics_from_df(symbol, df_fmp)
            if result is not None:
                result['is_cached'] = False
                save_wl_cache(result, symbol)
                return result
                
    # Fallback to yfinance
    timeout_val = 5 if off_market else 15
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1y", interval="1d", auto_adjust=True, timeout=timeout_val)
            
            if hist.empty:
                continue
                
            result = calculate_metrics_from_df(symbol, hist)
            if result is not None:
                result['is_cached'] = False
                save_wl_cache(result, symbol)
                return result
        except Exception as e:
            print(f"yfinance fetch error for {symbol} (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                time.sleep(1)
            continue
            
    # If yfinance failed and FMP key is present, try FMP historical as a final fallback
    if FMP_KEY and not (off_market and FMP_KEY):
        df_fmp = fetch_fmp_historical(symbol, FMP_KEY)
        if df_fmp is not None and not df_fmp.empty:
            result = calculate_metrics_from_df(symbol, df_fmp)
            if result is not None:
                result['is_cached'] = False
                save_wl_cache(result, symbol)
                return result
                
    # Final fallback: network failed, try to load stale cache if it exists
    if cache_path.exists():
        try:
            with QMutexLocker(mutex):
                cached = pd.read_parquet(cache_path)
            if cached is not None and not cached.empty:
                cached['is_cached'] = True
                print(f"Network failed for {symbol}. Falling back to stale cache.")
                return cached
        except Exception as e:
            print(f"Error reading stale cache fallback for {symbol}: {e}")
            
    return None



# --- Finviz Preset Symbols Worker ---
class FinvizPresetSymbolsWorker(QThread):
    progress = Signal(str)
    result = Signal(list)
    error = Signal(str)
    
    def __init__(self, preset_name):
        super().__init__()
        self.preset_name = preset_name
        
    def run(self):
        try:
            self.progress.emit("Fetching strategy filters...")
            filters = PRESET_STRATEGIES.get(self.preset_name)
            if not filters:
                self.error.emit(f"Preset '{self.preset_name}' not found.")
                return
            
            ff_filters = {}
            for f_name, opt_val in filters.items():
                if opt_val and opt_val != "Any":
                    ff_val = FILTER_MAPPING[f_name]["options"][opt_val][0]
                    if ff_val is not None:
                        ff_filters[f_name] = ff_val

            orig_filters = []
            for f_name, opt_val in filters.items():
                if opt_val and opt_val != "Any":
                    orig_val = FILTER_MAPPING[f_name]["options"][opt_val][1]
                    if orig_val is not None:
                        orig_filters.append(orig_val)

            self.progress.emit("Querying Finviz engine...")
            symbols = []
            try:
                from finvizfinance.screener.overview import Overview
                foverview = Overview()
                if ff_filters:
                    foverview.set_filter(filters_dict=ff_filters)
                df = foverview.screener_view()
                if df is not None and not df.empty:
                    for col in df.columns:
                        if col.lower() in ('ticker', 'symbol'):
                            symbols = df[col].dropna().tolist()
                            break
            except Exception as e:
                print(f"finvizfinance failed in worker: {e}")

            if not symbols:
                self.progress.emit("Querying alternate Finviz fallback...")
                try:
                    from finviz.screener import Screener as OrigScreener
                    stock_list = OrigScreener(filters=orig_filters)
                    df = pd.DataFrame(list(stock_list))
                    if df is not None and not df.empty:
                        for col in df.columns:
                            if col.lower() in ('ticker', 'symbol'):
                                symbols = df[col].dropna().tolist()
                                break
                except Exception as e:
                    print(f"finviz original failed in worker: {e}")

            if symbols:
                self.result.emit(symbols)
            else:
                self.error.emit("No tickers returned by the Finviz screen.")
        except Exception as e:
            self.error.emit(str(e))


class WLFetchWorker(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    error = Signal(str)
    
    def __init__(self, symbols):
        super().__init__()
        self.symbols = symbols
    
    def run(self):
        try:
            results = {}
            total = len(self.symbols)
            
            for i, symbol in enumerate(self.symbols):
                self.progress.emit(int((i / total) * 100), f"Fetching {symbol}... ({i+1}/{total})")
                
                try:
                    df = fetch_wl_symbol_data(symbol)
                    if df is not None and not df.empty:
                        results[symbol] = df.iloc[0].to_dict()
                    else:
                        results[symbol] = None
                except Exception:
                    results[symbol] = None
            
            self.progress.emit(100, "Complete!")
            self.result.emit(results)
            
        except Exception as e:
            self.error.emit(str(e))


class FetchWorker(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    error = Signal(str)
    
    def __init__(self, symbol, period_days, benchmark="^GSPC"):
        super().__init__()
        self.symbol = symbol
        self.period_days = period_days
        self.benchmark = benchmark
    
    def run(self):
        try:
            df = fetch_single_data(self.symbol, self.period_days, self.benchmark)
            
            if df is None or df.empty:
                self.error.emit(f"No data found for {self.symbol}")
                return
            
            self.progress.emit(100, "Complete!")
            self.result.emit(df)
            
        except Exception as e:
            self.error.emit(str(e))


class ScreenerWorker(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    error = Signal(str)
    
    def __init__(self, symbols, period_days, benchmark="^GSPC"):
        super().__init__()
        self.symbols = symbols
        self.period_days = period_days
        self.benchmark = benchmark
    
    def run(self):
        try:
            results = []
            total = len(self.symbols)
            
            for i, symbol in enumerate(self.symbols):
                self.progress.emit(int((i / total) * 100), f"Scanning {symbol}... ({i+1}/{total})")
                
                try:
                    df = fetch_single_data(symbol, self.period_days, self.benchmark)
                    
                    if df is not None and not df.empty:
                        df_clean = df.dropna()
                        if len(df_clean) > 10:
                            stock_return = df_clean['Stock_Cumulative'].iloc[-1] * 100
                            bench_return = df_clean['Bench_Cumulative'].iloc[-1] * 100
                            outperformance = stock_return - bench_return
                            
                            stock_vol = df_clean['Stock_Return'].std() * np.sqrt(252) * 100
                            bench_vol = df_clean['Bench_Return'].std() * np.sqrt(252) * 100
                            
                            beta = df_clean['Stock_Return'].cov(df_clean['Bench_Return']) / df_clean['Bench_Return'].var() if df_clean['Bench_Return'].var() != 0 else 1
                            
                            results.append({
                                'symbol': symbol,
                                'stock_return': stock_return,
                                'bench_return': bench_return,
                                'outperformance': outperformance,
                                'beta': beta,
                                'stock_vol': stock_vol,
                                'bench_vol': bench_vol,
                            })
                
                except Exception:
                    continue
            
            results.sort(key=lambda x: x['outperformance'], reverse=True)
            self.progress.emit(100, "Complete!")
            self.result.emit(results)
            
        except Exception as e:
            self.error.emit(str(e))


class MTFWorker(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    error = Signal(str)
    
    def __init__(self, symbols, enabled_periods=None, benchmark="^GSPC"):
        super().__init__()
        self.symbols = symbols
        self.enabled_periods = enabled_periods or list(MTF_PERIODS.keys())
        self.benchmark = benchmark
    
    def run(self):
        try:
            results = []
            total = len(self.symbols)
            
            for i, symbol in enumerate(self.symbols):
                self.progress.emit(int((i / total) * 100), f"Scanning {symbol}... ({i+1}/{total})")
                
                try:
                    period_scores = {}
                    total_score = 0
                    
                    for period_name, period_days in MTF_PERIODS.items():
                        if period_name not in self.enabled_periods:
                            continue
                        df = fetch_single_data(symbol, period_days, self.benchmark)
                        
                        if df is not None and not df.empty:
                            df_clean = df.dropna()
                            if len(df_clean) > 10:
                                stock_return = df_clean['Stock_Cumulative'].iloc[-1]
                                bench_return = df_clean['Bench_Cumulative'].iloc[-1]
                                
                                if bench_return != 0:
                                    ratio = stock_return / bench_return
                                else:
                                    ratio = 0 if stock_return == 0 else (1 if stock_return > 0 else -1)
                                
                                outperformance = stock_return - bench_return
                                
                                if outperformance > 0.01:
                                    score = 1
                                elif outperformance < -0.01:
                                    score = -1
                                else:
                                    score = 0
                                
                                period_scores[period_name] = {
                                    'score': score,
                                    'ratio': ratio,
                                    'stock_return': stock_return * 100,
                                    'bench_return': bench_return * 100,
                                    'outperformance': outperformance * 100,
                                }
                                total_score += score
                            else:
                                for pn in MTF_PERIODS:
                                    if pn not in period_scores:
                                        period_scores[pn] = {'score': 0, 'ratio': 0, 'stock_return': 0, 'bench_return': 0, 'outperformance': 0}
                        else:
                            for pn in MTF_PERIODS:
                                if pn not in period_scores:
                                    period_scores[pn] = {'score': 0, 'ratio': 0, 'stock_return': 0, 'bench_return': 0, 'outperformance': 0}
                    
                    results.append({
                        'symbol': symbol,
                        'period_scores': period_scores,
                        'total_score': total_score,
                    })
                
                except Exception:
                    continue
            
            results.sort(key=lambda x: x['total_score'], reverse=True)
            self.progress.emit(100, "Complete!")
            self.result.emit(results)
            
        except Exception as e:
            self.error.emit(str(e))


class StockComparator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = DatabaseManager()
        self.worker = None
        self.screener_worker = None
        self.mtf_worker = None
        self.preset_worker = None
        self.mtf_preset_worker = None
        self.current_data = None
        self.current_watchlist = []
        self.mtf_results_data = []
        self.finviz_preset_cache = {}
        self.us_master_list = load_us_master()
        self.setup_ui()
        self.setStyleSheet(self.get_styles())
        self.refresh_watchlists()
    
    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        search_layout = QHBoxLayout()
        search_layout.setSpacing(8)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter US stock symbol (e.g., AAPL, MSFT, GOOGL)")
        self.search_input.setMinimumWidth(300)
        self.search_input.returnPressed.connect(self.search_stock)
        
        if self.us_master_list:
            self.search_completer = QCompleter(self.us_master_list)
            self.search_completer.setCaseSensitivity(Qt.CaseInsensitive)
            self.search_completer.setFilterMode(Qt.MatchContains)
            self.search_completer.setCompletionMode(QCompleter.PopupCompletion)
            self.search_completer.setMaxVisibleItems(12)
            self.search_input.setCompleter(self.search_completer)
            self.search_completer.activated.connect(self.on_search_completer_activated)
        
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.search_stock)
        
        self.period_combo = QComboBox()
        self.period_combo.addItems(PERIODS.keys())
        self.period_combo.setCurrentText("1 Year")
        
        self.benchmark_combo = QComboBox()
        for name, sym in BENCHMARKS.items():
            self.benchmark_combo.addItem(name, sym)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(150)
        self.progress_bar.setVisible(False)
        
        self.status_label = QLabel()
        self.status_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        
        search_layout.addWidget(QLabel("Symbol:"))
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_btn)
        search_layout.addSpacing(15)
        search_layout.addWidget(QLabel("Compare To:"))
        search_layout.addWidget(self.benchmark_combo)
        search_layout.addWidget(QLabel("Period:"))
        search_layout.addWidget(self.period_combo)
        search_layout.addWidget(self.progress_bar)
        search_layout.addWidget(self.status_label)
        search_layout.addStretch()
        
        main_layout.addLayout(search_layout)
        
        self.tabs = QTabWidget()
        
        # 1. Performance Chart Tab
        chart_widget = QWidget()
        chart_layout = QVBoxLayout(chart_widget)
        chart_layout.setContentsMargins(6, 6, 6, 6)
        
        self.chart_view = QChartView()
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        self.chart_view.setRenderHint(QPainter.SmoothPixmapTransform)
        self.chart_view.setBackgroundBrush(QColor(BG_SURFACE))
        self.chart_view.setMinimumHeight(350)
        chart_layout.addWidget(self.chart_view)
        
        self.tabs.addTab(chart_widget, "Performance Chart")
        
        # 2. Statistics Tab
        stats_widget = QWidget()
        stats_layout = QVBoxLayout(stats_widget)
        stats_layout.setContentsMargins(6, 6, 6, 6)
        
        self.stats_table = QTableWidget()
        self.stats_table.setColumnCount(7)
        self.stats_table.setHorizontalHeaderLabels([
            "Metric", "Stock", "S&P 500", "Difference", "Stock (%)", "S&P 500 (%)", "Outperformance"
        ])
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stats_table.setMinimumHeight(350)
        stats_layout.addWidget(self.stats_table)
        
        self.tabs.addTab(stats_widget, "Statistics")
        
        # 3. Daily Returns Tab
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)
        history_layout.setContentsMargins(6, 6, 6, 6)
        
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(5)
        self.history_table.setHorizontalHeaderLabels(["Date", "Stock Close", "S&P 500 Close", "Stock Return", "S&P 500 Return"])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        history_layout.addWidget(self.history_table)
        
        self.tabs.addTab(history_widget, "Daily Returns")
        
        # 4. Price-Volume-RSI Tab
        pvr_widget = QWidget()
        pvr_layout = QVBoxLayout(pvr_widget)
        pvr_layout.setContentsMargins(6, 6, 6, 6)
        
        self.pvr_price_chart_view = QChartView()
        self.pvr_price_chart_view.setRenderHint(QPainter.Antialiasing)
        self.pvr_price_chart_view.setBackgroundBrush(QColor(BG_SURFACE))
        pvr_layout.addWidget(self.pvr_price_chart_view, stretch=2)
        
        self.pvr_volume_chart_view = QChartView()
        self.pvr_volume_chart_view.setRenderHint(QPainter.Antialiasing)
        self.pvr_volume_chart_view.setBackgroundBrush(QColor(BG_SURFACE))
        pvr_layout.addWidget(self.pvr_volume_chart_view, stretch=1)
        
        self.pvr_rsi_chart_view = QChartView()
        self.pvr_rsi_chart_view.setRenderHint(QPainter.Antialiasing)
        self.pvr_rsi_chart_view.setBackgroundBrush(QColor(BG_SURFACE))
        pvr_layout.addWidget(self.pvr_rsi_chart_view, stretch=1)
        
        self.tabs.addTab(pvr_widget, "Price-Volume-RSI")
        
        # 5. Screener Tab
        screener_widget = QWidget()
        screener_layout = QVBoxLayout(screener_widget)
        screener_layout.setContentsMargins(6, 6, 6, 6)
        
        scan_layout = QHBoxLayout()
        scan_layout.setSpacing(8)
        
        self.watchlist_combo = QComboBox()
        self.watchlist_combo.setMinimumWidth(220)
        self.watchlist_combo.currentTextChanged.connect(self.on_watchlist_changed)
        
        self.scan_btn = QPushButton("SCAN & RANK")
        self.scan_btn.clicked.connect(self.start_screener)
        self.scan_btn.setMinimumWidth(120)
        
        self.scan_progress = QProgressBar()
        self.scan_progress.setMaximumWidth(250)
        self.scan_progress.setVisible(False)
        
        self.scan_status = QLabel()
        self.scan_status.setStyleSheet(f"color: {TEXT_SECONDARY};")
        
        scan_layout.addWidget(QLabel("Watchlist:"))
        scan_layout.addWidget(self.watchlist_combo)
        scan_layout.addWidget(self.scan_btn)
        scan_layout.addWidget(self.scan_progress)
        scan_layout.addWidget(self.scan_status)
        scan_layout.addStretch()
        
        screener_layout.addLayout(scan_layout)
        
        self.screener_table = QTableWidget()
        self.screener_table.setColumnCount(7)
        self.screener_table.setHorizontalHeaderLabels([
            "Rank", "Symbol", "Stock Return", "S&P 500 Return", "Outperformance", "Beta", "Volatility"
        ])
        self.screener_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.screener_table.setMinimumHeight(350)
        self.screener_table.cellClicked.connect(self.on_screener_row_clicked)
        
        screener_layout.addWidget(self.screener_table)
        
        self.tabs.addTab(screener_widget, "Screener")
        
        # 6. MTF Screener Tab
        mtf_widget = QWidget()
        mtf_layout = QVBoxLayout(mtf_widget)
        mtf_layout.setContentsMargins(6, 6, 6, 6)
        
        mtf_control_layout = QHBoxLayout()
        mtf_control_layout.setSpacing(8)
        
        self.mtf_watchlist_combo = QComboBox()
        self.mtf_watchlist_combo.setMinimumWidth(220)
        self.mtf_watchlist_combo.currentTextChanged.connect(self.on_mtf_watchlist_changed)
        
        self.mtf_scan_btn = QPushButton("Scan MTF")
        self.mtf_scan_btn.clicked.connect(self.start_mtf_screener)
        self.mtf_scan_btn.setMinimumWidth(120)
        
        self.mtf_export_btn = QPushButton("Export CSV")
        self.mtf_export_btn.clicked.connect(self.export_mtf_results)
        self.mtf_export_btn.setMinimumWidth(100)
        self.mtf_export_btn.setProperty("class", "secondary")
        
        self.mtf_progress = QProgressBar()
        self.mtf_progress.setMaximumWidth(250)
        self.mtf_progress.setVisible(False)
        
        self.mtf_status = QLabel()
        self.mtf_status.setStyleSheet(f"color: {TEXT_SECONDARY};")
        
        self.mtf_checkboxes = {}
        mtf_period_layout = QHBoxLayout()
        mtf_period_layout.setSpacing(10)
        mtf_period_layout.addWidget(QLabel("Periods:"))
        for period_name in MTF_PERIODS:
            cb = QCheckBox(period_name)
            cb.setChecked(True)
            self.mtf_checkboxes[period_name] = cb
            mtf_period_layout.addWidget(cb)
        mtf_period_layout.addSpacing(15)
        
        mtf_control_layout.addWidget(QLabel("Watchlist:"))
        mtf_control_layout.addWidget(self.mtf_watchlist_combo)
        mtf_control_layout.addWidget(self.mtf_scan_btn)
        mtf_control_layout.addWidget(self.mtf_export_btn)
        mtf_control_layout.addWidget(self.mtf_progress)
        mtf_control_layout.addWidget(self.mtf_status)
        mtf_control_layout.addStretch()
        
        mtf_layout.addLayout(mtf_control_layout)
        mtf_layout.addLayout(mtf_period_layout)
        
        self.mtf_table = QTableWidget()
        self.mtf_table.setColumnCount(10)
        self.mtf_table.setHorizontalHeaderLabels([
            "Rank", "Symbol", "1W", "1M", "3M", "6M", "1Y", "3Y", "5Y", "Total"
        ])
        self.mtf_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.mtf_table.setMinimumHeight(350)
        self.mtf_table.cellClicked.connect(self.on_mtf_row_clicked)
        for j in range(2, 9):
            self.mtf_table.horizontalHeaderItem(j).setToolTip("Score: +1 if stock outperforms S&P 500 by >1%, 0 if within ±1%, -1 if underperforms by >1%")
        self.mtf_table.horizontalHeaderItem(9).setToolTip("Sum of enabled period scores. Higher = stronger consistent outperformance.")
        
        mtf_layout.addWidget(self.mtf_table)
        
        self.tabs.addTab(mtf_widget, "MTF Screener")
        
        main_layout.addWidget(self.tabs)
        
        self._create_empty_chart()
    
    def on_search_completer_activated(self, text):
        self.search_stock()
    
    def get_styles(self):
        return f"""
            QWidget {{
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 11px;
                background-color: {BG_PRIMARY};
                color: {TEXT_PRIMARY};
            }}
            QLineEdit {{
                padding: 5px 8px;
                border: 1px solid {BORDER};
                border-radius: 4px;
                font-size: 12px;
                background: {BG_SURFACE};
                color: {TEXT_PRIMARY};
            }}
            QLineEdit:focus {{
                border-color: {ACCENT};
            }}
            QPushButton {{
                background: {ACCENT};
                color: {BG_PRIMARY};
                padding: 6px 12px;
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: #66E6FF;
            }}
            QPushButton:disabled {{
                background: #21262D;
                color: {TEXT_SECONDARY};
            }}
            QPushButton[class="secondary"] {{
                background: {BG_SURFACE};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
            }}
            QPushButton[class="secondary"]:hover {{
                background: {BG_CARD};
            }}
            QPushButton[class="danger"] {{
                background: {DANGER};
                color: white;
            }}
            QPushButton[class="danger"]:hover {{
                background: #ff6666;
            }}
            QComboBox {{
                padding: 4px 8px;
                border: 1px solid {BORDER};
                border-radius: 4px;
                font-size: 11px;
                background: {BG_SURFACE};
                color: {TEXT_PRIMARY};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 16px;
            }}
            QComboBox QAbstractItemView {{
                background: {BG_SURFACE};
                border: 1px solid {BORDER};
                color: {TEXT_PRIMARY};
                selection-background-color: {ACCENT};
                selection-color: {BG_PRIMARY};
            }}
            QTableWidget {{
                border: 1px solid {BORDER};
                gridline-color: {BORDER};
                background: {BG_SURFACE};
            }}
            QTableWidget::item {{
                padding: 6px;
            }}
            QTableWidget::item:selected {{
                background: {BG_CARD};
                color: #ffffff;
            }}
            QHeaderView::section {{
                background: {BG_CARD};
                color: {ACCENT};
                padding: 8px;
                font-weight: bold;
                font-size: 11px;
                border: none;
                border-right: 1px solid {BORDER};
                border-bottom: 2px solid {BORDER};
            }}
            QTabWidget::pane {{
                border: 1px solid {BORDER};
                background: {BG_PRIMARY};
            }}
            QTabBar::tab {{
                padding: 8px 16px;
                background: {BG_SURFACE};
                color: {TEXT_SECONDARY};
                border: 1px solid {BORDER};
                border-bottom: none;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            QTabBar::tab:hover {{
                color: {TEXT_PRIMARY};
                background: {BG_CARD};
            }}
            QTabBar::tab:selected {{
                background: {BG_PRIMARY};
                color: {ACCENT};
                border-bottom: 2px solid {ACCENT};
            }}
            QProgressBar {{
                border: 1px solid {BORDER};
                border-radius: 4px;
                text-align: center;
                background: {BG_SURFACE};
                color: {TEXT_PRIMARY};
                font-size: 10px;
                height: 14px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {ACCENT}, stop:1 #66E6FF);
            }}
            QCheckBox {{
                color: {TEXT_PRIMARY};
                spacing: 5px;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {BORDER};
                border-radius: 3px;
                background: {BG_SURFACE};
            }}
            QCheckBox::indicator:checked {{
                background: {ACCENT};
                border-color: {ACCENT};
            }}
            QLabel {{
                color: {TEXT_PRIMARY};
            }}
        """
    
    def _create_empty_chart(self):
        chart = QChart()
        chart.setTitle("Enter a stock symbol and click Search to compare with S&P 500")
        chart.setTitleFont(QFont("Segoe UI", 12, QFont.Bold))
        chart.setTitleBrush(QColor(ACCENT))
        chart.setBackgroundBrush(QColor(BG_PRIMARY))
        self.chart_view.setChart(chart)
        
        self.pvr_axis_x = None
        self.pvr_axis_x_vol = None
        self.pvr_axis_x_rsi = None
        
        empty_price = QChart()
        empty_price.setTitle("Price chart will appear here after searching")
        empty_price.setTitleFont(QFont("Segoe UI", 10, QFont.Bold))
        empty_price.setTitleBrush(QColor(ACCENT))
        empty_price.setBackgroundBrush(QColor(BG_PRIMARY))
        self.pvr_price_chart_view.setChart(empty_price)
        
        empty_vol = QChart()
        empty_vol.setTitle("Volume chart will appear here after searching")
        empty_vol.setTitleFont(QFont("Segoe UI", 10, QFont.Bold))
        empty_vol.setTitleBrush(QColor(ACCENT))
        empty_vol.setBackgroundBrush(QColor(BG_PRIMARY))
        self.pvr_volume_chart_view.setChart(empty_vol)
        
        empty_rsi = QChart()
        empty_rsi.setTitle("RSI chart will appear here after searching")
        empty_rsi.setTitleFont(QFont("Segoe UI", 10, QFont.Bold))
        empty_rsi.setTitleBrush(QColor(ACCENT))
        empty_rsi.setBackgroundBrush(QColor(BG_PRIMARY))
        self.pvr_rsi_chart_view.setChart(empty_rsi)
    
    def get_watchlist_names(self):
        try:
            return [wl["name"] for wl in self.db.get_watchlists()]
        except Exception as e:
            print(f"Error getting watchlist names: {e}")
            return []

    def load_watchlist(self, name):
        try:
            items = self.db.get_watchlist_items(name)
            return [item["symbol"] for item in items]
        except Exception as e:
            print(f"Error loading watchlist {name}: {e}")
            return []

    def refresh_watchlists(self):
        db_names = sorted(self.get_watchlist_names())
        finviz_presets = ["Finviz: " + name for name in sorted(PRESET_STRATEGIES.keys())]
        
        populate_watchlist_combobox(self.watchlist_combo, db_names, finviz_presets)
        populate_watchlist_combobox(self.mtf_watchlist_combo, db_names, finviz_presets)
        
        self.on_watchlist_changed(self.watchlist_combo.currentText())
        self.on_mtf_watchlist_changed(self.mtf_watchlist_combo.currentText())
    
    def on_watchlist_changed(self, name):
        if name and not name.startswith("Finviz: ") and name != "── Finviz Presets ──":
            self.current_watchlist = self.load_watchlist(name)
        else:
            self.current_watchlist = []
            
    def on_mtf_watchlist_changed(self, name):
        pass
    
    def search_stock(self):
        raw = self.search_input.text().strip()
        if not raw:
            return
        
        symbol = raw.split(" - ")[0].strip().upper()
        if not symbol:
            return
        
        if self.worker and self.worker.isRunning():
            return
        
        self.search_input.setText(symbol)
        
        self.search_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText(f"Loading {symbol}...")
        
        period_name = self.period_combo.currentText()
        period_days = PERIODS[period_name]
        benchmark = self.benchmark_combo.currentData()
        
        self.worker = FetchWorker(symbol, period_days, benchmark)
        self.worker.progress.connect(self.on_progress)
        self.worker.result.connect(self.on_data_ready)
        self.worker.error.connect(self.on_error)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
    
    def on_progress(self, value, message):
        self.progress_bar.setValue(value)
        self.status_label.setText(message)
    
    def on_data_ready(self, df):
        self.current_data = df
        self.update_chart(df)
        self.update_statistics(df)
        self.update_history(df)
        self.update_pvr_chart(df)
    
    def on_error(self, error_msg):
        QMessageBox.warning(self, "Error", error_msg)
    
    def on_finished(self):
        self.search_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
    
    # --- Dynamic Finviz Preset Screener Run ---
    def start_screener(self):
        watchlist_name = self.watchlist_combo.currentText()
        if not watchlist_name or watchlist_name == "── Finviz Presets ──":
            QMessageBox.warning(self, "Error", "No watchlist selected")
            return
        
        if watchlist_name.startswith("Finviz: "):
            preset_name = watchlist_name[len("Finviz: "):]
            if preset_name in self.finviz_preset_cache:
                self.run_screener_for_symbols(self.finviz_preset_cache[preset_name])
            else:
                self.scan_btn.setEnabled(False)
                self.scan_progress.setVisible(True)
                self.scan_progress.setValue(0)
                self.scan_progress.setRange(0, 0)
                self.scan_status.setText("Fetching Finviz symbols...")
                
                self.preset_worker = FinvizPresetSymbolsWorker(preset_name)
                self.preset_worker.progress.connect(self.scan_status.setText)
                self.preset_worker.error.connect(self.on_screener_preset_error)
                self.preset_worker.result.connect(self.on_screener_preset_result)
                self.preset_worker.start()
        else:
            self.run_screener_for_symbols(self.current_watchlist)
            
    def on_screener_preset_error(self, error_msg):
        QMessageBox.warning(self, "Error", f"Failed to fetch preset: {error_msg}")
        self.scan_btn.setEnabled(True)
        self.scan_progress.setVisible(False)
        self.scan_status.setText("")
        
    def on_screener_preset_result(self, symbols):
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(0)
        watchlist_name = self.watchlist_combo.currentText()
        if watchlist_name.startswith("Finviz: "):
            preset_name = watchlist_name[len("Finviz: "):]
            self.finviz_preset_cache[preset_name] = symbols
        self.run_screener_for_symbols(symbols)

    def run_screener_for_symbols(self, symbols):
        if not symbols:
            QMessageBox.warning(self, "Error", "Watchlist has no symbols")
            return
        
        if self.screener_worker and self.screener_worker.isRunning():
            return
        
        self.scan_btn.setEnabled(False)
        self.scan_progress.setVisible(True)
        self.scan_progress.setValue(0)
        self.scan_status.setText("Starting scan...")
        
        period_name = self.period_combo.currentText()
        period_days = PERIODS[period_name]
        benchmark = self.benchmark_combo.currentData()
        
        self.screener_worker = ScreenerWorker(symbols, period_days, benchmark)
        self.screener_worker.progress.connect(self.on_screener_progress)
        self.screener_worker.result.connect(self.on_screener_result)
        self.screener_worker.error.connect(self.on_screener_error)
        self.screener_worker.finished.connect(self.on_screener_finished)
        self.screener_worker.start()
    
    def on_screener_progress(self, value, message):
        self.scan_progress.setValue(value)
        self.scan_status.setText(message)
    
    def on_screener_result(self, results):
        self.screener_table.setRowCount(len(results))
        for i, r in enumerate(results):
            self.screener_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.screener_table.item(i, 0).setTextAlignment(Qt.AlignCenter)
            self.screener_table.item(i, 0).setFont(QFont("Segoe UI", 10, QFont.Bold))
            
            self.screener_table.setItem(i, 1, QTableWidgetItem(r['symbol']))
            self.screener_table.item(i, 1).setTextAlignment(Qt.AlignCenter)
            self.screener_table.item(i, 1).setFont(QFont("Segoe UI", 10, QFont.Bold))
            
            def _color_item(val, unit=""):
                item = QTableWidgetItem(f"{val:.2f}{unit}")
                item.setTextAlignment(Qt.AlignCenter)
                if val > 0:
                    item.setForeground(QColor(SUCCESS))
                    item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                elif val < 0:
                    item.setForeground(QColor(DANGER))
                return item
            
            self.screener_table.setItem(i, 2, _color_item(r['stock_return'], "%"))
            self.screener_table.setItem(i, 3, _color_item(r['bench_return'], "%"))
            self.screener_table.setItem(i, 4, _color_item(r['outperformance'], "%"))
            
            beta_item = QTableWidgetItem(f"{r['beta']:.2f}")
            beta_item.setTextAlignment(Qt.AlignCenter)
            self.screener_table.setItem(i, 5, beta_item)
            
            vol_item = QTableWidgetItem(f"{r['stock_vol']:.2f}%")
            vol_item.setTextAlignment(Qt.AlignCenter)
            self.screener_table.setItem(i, 6, vol_item)
    
    def on_screener_error(self, error_msg):
        QMessageBox.warning(self, "Error", error_msg)
    
    def on_screener_finished(self):
        self.scan_btn.setEnabled(True)
        self.scan_progress.setVisible(False)
        self.scan_status.setText("")
    
    def on_screener_row_clicked(self, row, col):
        symbol = self.screener_table.item(row, 1).text()
        self.search_input.setText(symbol)
        self.tabs.setCurrentIndex(0)
        self.search_stock()
    
    # --- Dynamic Finviz Preset MTF Screener Run ---
    def start_mtf_screener(self):
        watchlist_name = self.mtf_watchlist_combo.currentText()
        if not watchlist_name or watchlist_name == "── Finviz Presets ──":
            QMessageBox.warning(self, "Error", "No watchlist selected")
            return
        
        enabled_periods = [name for name, cb in self.mtf_checkboxes.items() if cb.isChecked()]
        if not enabled_periods:
            QMessageBox.warning(self, "Error", "At least one period must be enabled")
            return
        
        if watchlist_name.startswith("Finviz: "):
            preset_name = watchlist_name[len("Finviz: "):]
            if preset_name in self.finviz_preset_cache:
                self.run_mtf_screener_for_symbols(self.finviz_preset_cache[preset_name], enabled_periods)
            else:
                self.mtf_scan_btn.setEnabled(False)
                self.mtf_progress.setVisible(True)
                self.mtf_progress.setValue(0)
                self.mtf_progress.setRange(0, 0)
                self.mtf_status.setText("Fetching Finviz symbols...")
                
                self.mtf_preset_worker = FinvizPresetSymbolsWorker(preset_name)
                self.mtf_preset_worker.progress.connect(self.mtf_status.setText)
                self.mtf_preset_worker.error.connect(self.on_mtf_preset_error)
                self.mtf_preset_worker.result.connect(self.on_mtf_preset_result)
                self.mtf_preset_worker.start()
        else:
            symbols = self.load_watchlist(watchlist_name)
            self.run_mtf_screener_for_symbols(symbols, enabled_periods)

    def on_mtf_preset_error(self, error_msg):
        QMessageBox.warning(self, "Error", f"Failed to fetch preset: {error_msg}")
        self.mtf_scan_btn.setEnabled(True)
        self.mtf_progress.setVisible(False)
        self.mtf_status.setText("")
        
    def on_mtf_preset_result(self, symbols):
        self.mtf_progress.setRange(0, 100)
        self.mtf_progress.setValue(0)
        watchlist_name = self.mtf_watchlist_combo.currentText()
        if watchlist_name.startswith("Finviz: "):
            preset_name = watchlist_name[len("Finviz: "):]
            self.finviz_preset_cache[preset_name] = symbols
        enabled_periods = [name for name, cb in self.mtf_checkboxes.items() if cb.isChecked()]
        self.run_mtf_screener_for_symbols(symbols, enabled_periods)

    def run_mtf_screener_for_symbols(self, symbols, enabled_periods):
        if not symbols:
            QMessageBox.warning(self, "Error", "Watchlist has no symbols")
            return
        
        if self.mtf_worker and self.mtf_worker.isRunning():
            return
        
        self.mtf_scan_btn.setEnabled(False)
        self.mtf_progress.setVisible(True)
        self.mtf_progress.setValue(0)
        self.mtf_status.setText("Starting MTF scan...")
        benchmark = self.benchmark_combo.currentData()
        
        self.mtf_worker = MTFWorker(symbols, enabled_periods, benchmark)
        self.mtf_worker.progress.connect(self.on_mtf_progress)
        self.mtf_worker.result.connect(self.on_mtf_result)
        self.mtf_worker.error.connect(self.on_mtf_error)
        self.mtf_worker.finished.connect(self.on_mtf_finished)
        self.mtf_worker.start()
    
    def on_mtf_progress(self, value, message):
        self.mtf_progress.setValue(value)
        self.mtf_status.setText(message)
    
    def on_mtf_result(self, results):
        self.mtf_results_data = results
        self.mtf_table.setRowCount(len(results))
        for i, r in enumerate(results):
            self.mtf_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.mtf_table.item(i, 0).setTextAlignment(Qt.AlignCenter)
            self.mtf_table.item(i, 0).setFont(QFont("Segoe UI", 10, QFont.Bold))
            
            self.mtf_table.setItem(i, 1, QTableWidgetItem(r['symbol']))
            self.mtf_table.item(i, 1).setTextAlignment(Qt.AlignCenter)
            self.mtf_table.item(i, 1).setFont(QFont("Segoe UI", 10, QFont.Bold))
            
            ps = r['period_scores']
            
            for j, period in enumerate(["1W", "1M", "3M", "6M", "1Y", "3Y", "5Y"]):
                score = ps.get(period, {}).get('score', 0)
                item = QTableWidgetItem(str(score))
                item.setTextAlignment(Qt.AlignCenter)
                
                if score > 0:
                    item.setForeground(QColor(SUCCESS))
                    item.setFont(QFont("Segoe UI", 10, QFont.Bold))
                elif score < 0:
                    item.setForeground(QColor(DANGER))
                else:
                    item.setForeground(QColor("#95A5A6"))
                
                self.mtf_table.setItem(i, j + 2, item)
            
            total_item = QTableWidgetItem(str(r['total_score']))
            total_item.setTextAlignment(Qt.AlignCenter)
            total_item.setFont(QFont("Segoe UI", 11, QFont.Bold))
            
            if r['total_score'] > 0:
                total_item.setForeground(QColor(SUCCESS))
            elif r['total_score'] < 0:
                total_item.setForeground(QColor(DANGER))
            else:
                total_item.setForeground(QColor("#95A5A6"))
            
            self.mtf_table.setItem(i, 9, total_item)
    
    def on_mtf_error(self, error_msg):
        QMessageBox.warning(self, "Error", error_msg)
    
    def on_mtf_finished(self):
        self.mtf_scan_btn.setEnabled(True)
        self.mtf_progress.setVisible(False)
        self.mtf_status.setText("")
    
    def on_mtf_row_clicked(self, row, col):
        symbol = self.mtf_table.item(row, 1).text()
        self.search_input.setText(symbol)
        self.tabs.setCurrentIndex(0)
        self.search_stock()
    
    def export_mtf_results(self):
        if not self.mtf_results_data:
            QMessageBox.warning(self, "Error", "No MTF results to export. Run a scan first.")
            return
        
        filepath, _ = QFileDialog.getSaveFileName(self, "Export MTF Results", "", "CSV Files (*.csv)")
        if filepath:
            try:
                with open(filepath, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Rank", "Symbol", "1W", "1M", "3M", "6M", "1Y", "3Y", "5Y", "Total Score"])
                    
                    for i, r in enumerate(self.mtf_results_data):
                        ps = r['period_scores']
                        row_data = [
                            i + 1,
                            r['symbol'],
                            ps.get("1W", {}).get('score', 0),
                            ps.get("1M", {}).get('score', 0),
                            ps.get("3M", {}).get('score', 0),
                            ps.get("6M", {}).get('score', 0),
                            ps.get("1Y", {}).get('score', 0),
                            ps.get("3Y", {}).get('score', 0),
                            ps.get("5Y", {}).get('score', 0),
                            r['total_score'],
                        ]
                        writer.writerow(row_data)
                
                QMessageBox.information(self, "Success", f"Exported {len(self.mtf_results_data)} results to {filepath}")
            
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to export: {str(e)}")
    
    def update_chart(self, df):
        chart = QChart()
        
        symbol_col = 'Symbol_stock' if 'Symbol_stock' in df.columns else 'Symbol'
        title_symbol = df[symbol_col].iloc[0] if symbol_col in df.columns else "Stock"
        
        chart.setTitle(f"{title_symbol} vs S&P 500")
        chart.setTitleFont(QFont("Segoe UI", 14, QFont.Bold))
        chart.setTitleBrush(QColor(ACCENT))
        chart.setBackgroundBrush(QColor(BG_PRIMARY))
        chart.setPlotAreaBackgroundBrush(QColor(BG_SURFACE))
        chart.setPlotAreaBackgroundVisible(True)
        chart.setDropShadowEnabled(False)
        
        stock_series = QLineSeries()
        stock_series.setName(f"  {title_symbol}  ")
        stock_series.setColor(QColor("#60a5fa"))
        stock_series.setPen(QPen(QColor("#60a5fa"), 2.5))
        
        bench_series = QLineSeries()
        bench_series.setName("  S&P 500  ")
        bench_series.setColor(QColor("#34d399"))
        bench_series.setPen(QPen(QColor("#34d399"), 2.5))
        
        dates = []
        for _, row in df.iterrows():
            ts = pd.Timestamp(row['Date'])
            qdatetime = QDateTime(ts.year, ts.month, ts.day, 0, 0, 0)
            dates.append(qdatetime)
            
            stock_val = float(row['Stock_Cumulative'] * 100)
            bench_val = float(row['Bench_Cumulative'] * 100)
            stock_series.append(qdatetime.toMSecsSinceEpoch(), stock_val)
            bench_series.append(qdatetime.toMSecsSinceEpoch(), bench_val)
        
        chart.addSeries(stock_series)
        chart.addSeries(bench_series)
        
        axis_x = QDateTimeAxis()
        axis_x.setFormat("MMM yyyy")
        axis_x.setLabelsFont(QFont("Segoe UI", 9))
        axis_x.setTitleFont(QFont("Segoe UI", 10, QFont.Bold))
        axis_x.setTitleText("Date")
        axis_x.setTitleBrush(QColor(ACCENT))
        axis_x.setLabelsBrush(QColor(TEXT_SECONDARY))
        axis_x.setGridLineColor(QColor(BORDER))
        axis_x.setLinePen(QPen(QColor(BORDER), 1))
        
        min_date = dates[0]
        max_date = dates[-1]
        axis_x.setRange(min_date, max_date)
        
        axis_y = QValueAxis()
        chart.addAxis(axis_y, Qt.AlignRight)
        
        axis_y.setTitleFont(QFont("Segoe UI", 10, QFont.Bold))
        axis_y.setTitleText("Cumulative Return (%)")
        axis_y.setTitleBrush(QColor(ACCENT))
        axis_y.setLabelsFont(QFont("Segoe UI", 9))
        axis_y.setLabelsBrush(QColor(TEXT_SECONDARY))
        axis_y.setGridLineColor(QColor(BORDER))
        axis_y.setLinePen(QPen(QColor(BORDER), 1))
        
        min_val = float(df[['Stock_Cumulative', 'Bench_Cumulative']].min().min()) * 100
        max_val = float(df[['Stock_Cumulative', 'Bench_Cumulative']].max().max()) * 100
        
        padding = max(10.0, (max_val - min_val) * 0.1)
        axis_y.setMin(min(min_val - padding, -10.0))
        axis_y.setMax(max(max_val + padding, 20.0))
        axis_y.setMinorTickCount(4)
        axis_y.setLabelFormat("%.1f%%")
        
        chart.addAxis(axis_x, Qt.AlignBottom)
        stock_series.attachAxis(axis_x)
        bench_series.attachAxis(axis_x)
        stock_series.attachAxis(axis_y)
        bench_series.attachAxis(axis_y)
        
        legend = chart.legend()
        legend.setVisible(True)
        legend.setAlignment(Qt.AlignTop)
        legend.setFont(QFont("Segoe UI", 10, QFont.Bold))
        legend.setBrush(QColor(BG_SURFACE))
        legend.setPen(QPen(QColor(BORDER)))
        
        self.chart_view.setChart(chart)
        self.chart_view.setRubberBand(QChartView.RubberBand.RectangleRubberBand)
        self.chart_view.setInteractive(True)
    
    def update_statistics(self, df):
        df_clean = df.dropna()
        
        stock_final = df_clean['Stock_Cumulative'].iloc[-1] * 100
        bench_final = df_clean['Bench_Cumulative'].iloc[-1] * 100
        
        stock_vol = df_clean['Stock_Return'].std() * np.sqrt(252) * 100
        bench_vol = df_clean['Bench_Return'].std() * np.sqrt(252) * 100
        
        stock_max = df_clean['Stock_Cumulative'].max() * 100
        stock_min = df_clean['Stock_Cumulative'].min() * 100
        bench_max = df_clean['Bench_Cumulative'].max() * 100
        bench_min = df_clean['Bench_Cumulative'].min() * 100
        
        pos_days = (df_clean['Stock_Return'] > 0).sum()
        total_days = len(df_clean)
        
        beta = df_clean['Stock_Return'].cov(df_clean['Bench_Return']) / df_clean['Bench_Return'].var() if df_clean['Bench_Return'].var() != 0 else 1
        
        sharpe = (stock_final / 100) / (stock_vol / 100) if stock_vol != 0 else 0
        
        metrics = [
            ("Total Return", f"{stock_final:.2f}%", f"{bench_final:.2f}%", 
             f"{stock_final - bench_final:.2f}%", stock_final, bench_final, stock_final > bench_final),
            ("Annualized Volatility", f"{stock_vol:.2f}%", f"{bench_vol:.2f}%",
             f"{stock_vol - bench_vol:.2f}%", stock_vol, bench_vol, stock_vol < bench_vol),
            ("Max Gain", f"{stock_max:.2f}%", f"{bench_max:.2f}%",
             f"{stock_max - bench_max:.2f}%", stock_max, bench_max, stock_max > bench_max),
            ("Max Loss", f"{stock_min:.2f}%", f"{bench_min:.2f}%",
             f"{stock_min - bench_min:.2f}%", stock_min, bench_min, stock_min < bench_min),
            ("Positive Days", f"{pos_days}/{total_days}", f"{(df_clean['Bench_Return'] > 0).sum()}/{total_days}",
             "", 0, 0, pos_days > (df_clean['Bench_Return'] > 0).sum()),
            ("Beta (vs S&P 500)", f"{beta:.2f}", "1.00", f"{beta - 1:.2f}", beta, 1, beta < 1),
            ("Sharpe Ratio", f"{sharpe:.2f}", "-", "-", sharpe, 0, sharpe > 0),
        ]
        
        self.stats_table.setRowCount(len(metrics))
        for i, (metric, stock_val, bench_val, diff, stock_num, bench_num, outperformance) in enumerate(metrics):
            self.stats_table.setItem(i, 0, QTableWidgetItem(metric))
            self.stats_table.item(i, 0).setFont(QFont("Segoe UI", 10, QFont.Bold))
            
            stock_item = QTableWidgetItem(stock_val)
            stock_item.setTextAlignment(Qt.AlignCenter)
            self.stats_table.setItem(i, 1, stock_item)
            
            bench_item = QTableWidgetItem(bench_val)
            bench_item.setTextAlignment(Qt.AlignCenter)
            self.stats_table.setItem(i, 2, bench_item)
            
            diff_item = QTableWidgetItem(diff)
            diff_item.setTextAlignment(Qt.AlignCenter)
            self.stats_table.setItem(i, 3, diff_item)
            
            bar_stock = QTableWidgetItem("" * min(int(abs(stock_num) / 2), 25))
            bar_stock.setTextAlignment(Qt.AlignCenter)
            self.stats_table.setItem(i, 4, bar_stock)
            
            bar_bench = QTableWidgetItem("" * min(int(abs(bench_num) / 2), 25))
            bar_bench.setTextAlignment(Qt.AlignCenter)
            self.stats_table.setItem(i, 5, bar_bench)
            
            out_item = QTableWidgetItem(" OUTPERFORMS" if outperformance else " UNDERPERFORMS")
            out_item.setTextAlignment(Qt.AlignCenter)
            if outperformance:
                out_item.setBackground(QColor("#2e7d32"))  # dark green
            else:
                out_item.setBackground(QColor("#c62828"))  # dark red
            self.stats_table.setItem(i, 6, out_item)
    
    def update_history(self, df):
        recent = df.tail(60).copy()
        recent['Date'] = pd.to_datetime(recent['Date']).dt.strftime('%Y-%m-%d')
        recent['Stock_Return'] = (recent['Stock_Return'] * 100).round(2).astype(str) + '%'
        recent['Bench_Return'] = (recent['Bench_Return'] * 100).round(2).astype(str) + '%'
        recent['Close_stock'] = recent['Close_stock'].round(2).astype(str)
        recent['Close_bench'] = recent['Close_bench'].round(2).astype(str)
        
        self.history_table.setRowCount(len(recent))
        for i, (_, row) in enumerate(recent.iterrows()):
            self.history_table.setItem(i, 0, QTableWidgetItem(str(row['Date'])))
            self.history_table.setItem(i, 1, QTableWidgetItem(str(row['Close_stock'])))
            self.history_table.setItem(i, 2, QTableWidgetItem(str(row['Close_bench'])))
            self.history_table.setItem(i, 3, QTableWidgetItem(str(row['Stock_Return'])))
            self.history_table.setItem(i, 4, QTableWidgetItem(str(row['Sector_bench'] if 'Sector_bench' in row else row['Bench_Return'])))
    
    def update_pvr_chart(self, df):
        symbol_col = 'Symbol_stock' if 'Symbol_stock' in df.columns else 'Symbol'
        title_symbol = df[symbol_col].iloc[0] if symbol_col in df.columns else "Stock"
        
        df_sorted = df.sort_values('Date').reset_index(drop=True)
        
        dates = [QDateTime(pd.Timestamp(row['Date']).year, pd.Timestamp(row['Date']).month, pd.Timestamp(row['Date']).day, 0, 0, 0) for _, row in df_sorted.iterrows()]
        prices = [float(row['Close_stock']) for _, row in df_sorted.iterrows()]
        
        has_volume = 'Volume' in df_sorted.columns
        volumes = [float(row['Volume']) for _, row in df_sorted.iterrows()] if has_volume else [0.0] * len(dates)
        
        close_series = df_sorted['Close_stock']
        delta = close_series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=14, min_periods=14).mean()
        avg_loss = loss.rolling(window=14, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, float('inf'))
        rsi_values = 100 - (100 / (1 + rs))
        rsi_list = rsi_values.tolist()
        
        def make_axis_x():
            ax = QDateTimeAxis()
            ax.setFormat("MMM yyyy")
            ax.setLabelsFont(QFont("Segoe UI", 8))
            ax.setGridLineColor(QColor(BORDER))
            ax.setLinePen(QPen(QColor(BORDER), 1))
            ax.setRange(dates[0], dates[-1])
            return ax
        
        price_series = QLineSeries()
        price_series.setColor(QColor(ACCENT))
        price_series.setPen(QPen(QColor(ACCENT), 2.5))
        for i in range(len(dates)):
            price_series.append(dates[i].toMSecsSinceEpoch(), prices[i])
        
        price_chart = QChart()
        price_chart.setBackgroundBrush(QColor(BG_PRIMARY))
        price_chart.setPlotAreaBackgroundBrush(QColor(BG_SURFACE))
        price_chart.setPlotAreaBackgroundVisible(True)
        price_chart.setDropShadowEnabled(False)
        price_chart.legend().setVisible(False)
        price_chart.setMargins(QMargins(0, 0, 0, 0))
        price_chart.addSeries(price_series)
        
        axis_x_price = make_axis_x()
        axis_y_price = QValueAxis()
        axis_y_price.setTitleText("Price")
        axis_y_price.setTitleFont(QFont("Segoe UI", 8, QFont.Bold))
        axis_y_price.setTitleBrush(QColor(ACCENT))
        axis_y_price.setLabelsFont(QFont("Segoe UI", 8))
        axis_y_price.setLabelsBrush(QColor(TEXT_SECONDARY))
        axis_y_price.setGridLineColor(QColor(BORDER))
        axis_y_price.setLinePen(QPen(QColor(BORDER), 1))
        price_min = min(prices)
        price_max = max(prices)
        price_pad = max(5.0, (price_max - price_min) * 0.05)
        axis_y_price.setMin(price_min - price_pad)
        axis_y_price.setMax(price_max + price_pad)
        
        price_chart.addAxis(axis_x_price, Qt.AlignBottom)
        price_chart.addAxis(axis_y_price, Qt.AlignLeft)
        price_series.attachAxis(axis_x_price)
        price_series.attachAxis(axis_y_price)
        self.pvr_price_chart_view.setChart(price_chart)
        self.pvr_price_chart_view.setRubberBand(QChartView.RubberBand.RectangleRubberBand)
        self.pvr_price_chart_view.setInteractive(True)
        
        vol_series = QLineSeries()
        vol_series.setColor(QColor("#60a5fa"))
        vol_series.setPen(QPen(QColor("#60a5fa"), 1.5))
        valid_vols = [v for v in volumes if v > 0]
        for i in range(len(dates)):
            if volumes[i] > 0:
                vol_series.append(dates[i].toMSecsSinceEpoch(), volumes[i])
        
        vol_chart = QChart()
        vol_chart.setBackgroundBrush(QColor(BG_PRIMARY))
        vol_chart.setPlotAreaBackgroundBrush(QColor(BG_SURFACE))
        vol_chart.setPlotAreaBackgroundVisible(True)
        vol_chart.setDropShadowEnabled(False)
        vol_chart.legend().setVisible(False)
        vol_chart.setMargins(QMargins(0, 0, 0, 0))
        vol_chart.addSeries(vol_series)
        
        axis_x_vol = make_axis_x()
        axis_y_vol = QValueAxis()
        axis_y_vol.setTitleText("Vol")
        axis_y_vol.setTitleFont(QFont("Segoe UI", 8, QFont.Bold))
        axis_y_vol.setTitleBrush(QColor(ACCENT))
        axis_y_vol.setLabelsFont(QFont("Segoe UI", 8))
        axis_y_vol.setLabelsBrush(QColor(TEXT_SECONDARY))
        axis_y_vol.setGridLineColor(QColor(BORDER))
        axis_y_vol.setLinePen(QPen(QColor(BORDER), 1))
        if valid_vols:
            vol_max = max(valid_vols)
            axis_y_vol.setMin(0)
            axis_y_vol.setMax(vol_max * 1.15)
            axis_y_vol.setLabelFormat("%.0f")
        else:
            axis_y_vol.setMin(0)
            axis_y_vol.setMax(100)
        
        vol_chart.addAxis(axis_x_vol, Qt.AlignBottom)
        vol_chart.addAxis(axis_y_vol, Qt.AlignLeft)
        vol_series.attachAxis(axis_x_vol)
        vol_series.attachAxis(axis_y_vol)
        self.pvr_volume_chart_view.setChart(vol_chart)
        self.pvr_volume_chart_view.setRubberBand(QChartView.RubberBand.RectangleRubberBand)
        self.pvr_volume_chart_view.setInteractive(True)
        
        rsi_series = QLineSeries()
        rsi_series.setColor(QColor("#f59e0b"))
        rsi_series.setPen(QPen(QColor("#f59e0b"), 2))
        for i in range(len(dates)):
            if not pd.isna(rsi_list[i]) and 0 <= rsi_list[i] <= 100:
                rsi_series.append(dates[i].toMSecsSinceEpoch(), rsi_list[i])
        
        rsi_chart = QChart()
        rsi_chart.setBackgroundBrush(QColor(BG_PRIMARY))
        rsi_chart.setPlotAreaBackgroundBrush(QColor(BG_SURFACE))
        rsi_chart.setPlotAreaBackgroundVisible(True)
        rsi_chart.setDropShadowEnabled(False)
        rsi_chart.legend().setVisible(False)
        rsi_chart.setMargins(QMargins(0, 0, 0, 0))
        rsi_chart.addSeries(rsi_series)
        
        axis_x_rsi = make_axis_x()
        axis_y_rsi = QValueAxis()
        axis_y_rsi.setTitleText("RSI")
        axis_y_rsi.setTitleFont(QFont("Segoe UI", 8, QFont.Bold))
        axis_y_rsi.setTitleBrush(QColor(ACCENT))
        axis_y_rsi.setLabelsFont(QFont("Segoe UI", 8))
        axis_y_rsi.setLabelsBrush(QColor(TEXT_SECONDARY))
        axis_y_rsi.setGridLineColor(QColor(BORDER))
        axis_y_rsi.setLinePen(QPen(QColor(BORDER), 1))
        axis_y_rsi.setMin(0)
        axis_y_rsi.setMax(100)
        axis_y_rsi.setTickCount(5)
        
        rsi_chart.addAxis(axis_x_rsi, Qt.AlignBottom)
        rsi_chart.addAxis(axis_y_rsi, Qt.AlignLeft)
        rsi_series.attachAxis(axis_x_rsi)
        rsi_series.attachAxis(axis_y_rsi)
        
        for level, color in [(70, "#ef4444"), (50, "#6b7280"), (30, "#22c55e")]:
            ref_line = QLineSeries()
            ref_line.append(dates[0].toMSecsSinceEpoch(), level)
            ref_line.append(dates[-1].toMSecsSinceEpoch(), level)
            ref_line.setColor(QColor(color))
            ref_line.setPen(QPen(QColor(color), 1, Qt.DashLine))
            rsi_chart.addSeries(ref_line)
            ref_line.attachAxis(axis_x_rsi)
            ref_line.attachAxis(axis_y_rsi)
        
        self.pvr_rsi_chart_view.setChart(rsi_chart)
        self.pvr_rsi_chart_view.setRubberBand(QChartView.RubberBand.RectangleRubberBand)
        self.pvr_rsi_chart_view.setInteractive(True)
        
        self.pvr_axis_x = axis_x_price
        self.pvr_axis_x_vol = axis_x_vol
        self.pvr_axis_x_rsi = axis_x_rsi
        
        price_chart.plotAreaChanged.connect(self._sync_pvr_x_axis)
        vol_chart.plotAreaChanged.connect(self._sync_pvr_x_axis)
        rsi_chart.plotAreaChanged.connect(self._sync_pvr_x_axis)
    
    def _sync_pvr_x_axis(self):
        if not hasattr(self, 'pvr_axis_x') or not hasattr(self, 'pvr_axis_x_vol') or not hasattr(self, 'pvr_axis_x_rsi'):
            return
        
        source = self.sender()
        if source is None:
            return
        
        axis_x = source.axisX()
        if axis_x is None:
            return
        
        min_val = axis_x.min()
        max_val = axis_x.max()
        
        for ax in [self.pvr_axis_x, self.pvr_axis_x_vol, self.pvr_axis_x_rsi]:
            if ax is not None and ax is not axis_x:
                ax.setRange(min_val, max_val)
    
    def on_error(self, error_msg):
        QMessageBox.warning(self, "Error", error_msg)
    
    def on_finished(self):
        self.search_btn.setEnabled(True)
        self.progress_bar.setVisible(False)


# --- Centralized Watchlist Widget for Sidebar ---
class CentralWatchlistWidget(QWidget):
    watchlist_changed = Signal()  # Emit this whenever watchlists are modified
    symbols_changed = Signal(list)  # Emit this whenever symbols list is updated
    go_explore = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = DatabaseManager()
        self.wl_fetch_worker = None
        self.preset_worker = None
        self.wl_edited_symbols = []
        self.wl_fetched_data = {}
        self.finviz_preset_cache = {}
        self.setup_ui()
        self.setStyleSheet(self.get_styles())
        self.refresh_watchlists()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        
        header = QLabel("Watchlist Manager")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header.setStyleSheet(f"color: {ACCENT};")
        layout.addWidget(header)
        
        wl_top_layout = QHBoxLayout()
        wl_top_layout.setSpacing(6)
        
        self.wl_combo = QComboBox()
        self.wl_combo.setMinimumWidth(220)
        self.wl_combo.currentTextChanged.connect(self.on_wl_tab_changed)
        
        self.wl_symbol_count = QLabel("0 symbols")
        self.wl_symbol_count.setStyleSheet(f"color: {TEXT_SECONDARY};")
        
        self.wl_import_btn = QPushButton("Import CSV")
        self.wl_import_btn.clicked.connect(self.import_csv)
        self.wl_import_btn.setProperty("class", "secondary")
        
        self.wl_new_btn = QPushButton("New Watchlist")
        self.wl_new_btn.clicked.connect(self.new_watchlist)
        self.wl_new_btn.setProperty("class", "secondary")
        
        self.wl_delete_btn = QPushButton("Delete Watchlist")
        self.wl_delete_btn.clicked.connect(self.delete_current_watchlist)
        self.wl_delete_btn.setProperty("class", "danger")
        
        self.wl_add_symbol_btn = QPushButton("+ Symbol")
        self.wl_add_symbol_btn.clicked.connect(self.wl_add_symbol)
        
        self.wl_remove_symbol_btn = QPushButton("- Symbol")
        self.wl_remove_symbol_btn.clicked.connect(self.wl_remove_checked_symbols)
        self.wl_remove_symbol_btn.setProperty("class", "danger")
        
        self.wl_fetch_btn = QPushButton("Fetch/Refresh Data")
        self.wl_fetch_btn.clicked.connect(self.wl_fetch_data)
        
        self.wl_save_btn = QPushButton("Save")
        self.wl_save_btn.clicked.connect(self.wl_save)
        
        self.wl_save_as_btn = QPushButton("Save As")
        self.wl_save_as_btn.clicked.connect(self.wl_save_as)
        self.wl_save_as_btn.setProperty("class", "secondary")
        
        self.wl_export_btn = QPushButton("Export CSV")
        self.wl_export_btn.clicked.connect(self.wl_export_csv)
        self.wl_export_btn.setProperty("class", "secondary")
        
        wl_top_layout.addWidget(QLabel("Watchlist:"))
        wl_top_layout.addWidget(self.wl_combo)
        wl_top_layout.addWidget(self.wl_symbol_count)
        wl_top_layout.addWidget(self.wl_import_btn)
        wl_top_layout.addWidget(self.wl_new_btn)
        wl_top_layout.addWidget(self.wl_delete_btn)
        wl_top_layout.addWidget(self.wl_add_symbol_btn)
        wl_top_layout.addWidget(self.wl_remove_symbol_btn)
        wl_top_layout.addWidget(self.wl_fetch_btn)
        wl_top_layout.addWidget(self.wl_save_btn)
        wl_top_layout.addWidget(self.wl_save_as_btn)
        wl_top_layout.addWidget(self.wl_export_btn)
        wl_top_layout.addStretch()
        
        layout.addLayout(wl_top_layout)
        
        self.wl_progress = QProgressBar()
        self.wl_progress.setMaximumWidth(300)
        self.wl_progress.setVisible(False)
        
        self.wl_status = QLabel()
        self.wl_status.setStyleSheet(f"color: {TEXT_SECONDARY};")
        
        wl_progress_layout = QHBoxLayout()
        wl_progress_layout.addWidget(self.wl_progress)
        wl_progress_layout.addWidget(self.wl_status)
        wl_progress_layout.addStretch()
        layout.addLayout(wl_progress_layout)
        
        self.wl_symbol_table = QTableWidget()
        self.wl_symbol_table.setColumnCount(10)
        self.wl_symbol_table.setHorizontalHeaderLabels([
            "", "Symbol", "Price", "% Change", "Volume", "Support", "Resistance", "SMA20", "SMA50", "SMA200"
        ])
        self.wl_symbol_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for i in range(1, 10):
            self.wl_symbol_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Stretch)
        self.wl_symbol_table.setMinimumHeight(450)
        self.wl_symbol_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.wl_symbol_table.customContextMenuRequested.connect(self._on_wl_context_menu)
        self.wl_symbol_table.doubleClicked.connect(self._on_wl_double_clicked)
        
        layout.addWidget(self.wl_symbol_table)

    def get_styles(self):
        return f"""
            QWidget {{
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 11px;
                background-color: {BG_PRIMARY};
                color: {TEXT_PRIMARY};
            }}
            QLineEdit {{
                padding: 5px 8px;
                border: 1px solid {BORDER};
                border-radius: 4px;
                font-size: 12px;
                background: {BG_SURFACE};
                color: {TEXT_PRIMARY};
            }}
            QLineEdit:focus {{
                border-color: {ACCENT};
            }}
            QPushButton {{
                background: {ACCENT};
                color: {BG_PRIMARY};
                padding: 6px 12px;
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: #66E6FF;
            }}
            QPushButton:disabled {{
                background: #21262D;
                color: {TEXT_SECONDARY};
            }}
            QPushButton[class="secondary"] {{
                background: {BG_SURFACE};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
            }}
            QPushButton[class="secondary"]:hover {{
                background: {BG_CARD};
            }}
            QPushButton[class="danger"] {{
                background: {DANGER};
                color: white;
            }}
            QPushButton[class="danger"]:hover {{
                background: #ff6666;
            }}
            QComboBox {{
                padding: 4px 8px;
                border: 1px solid {BORDER};
                border-radius: 4px;
                font-size: 11px;
                background: {BG_SURFACE};
                color: {TEXT_PRIMARY};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 16px;
            }}
            QComboBox QAbstractItemView {{
                background: {BG_SURFACE};
                border: 1px solid {BORDER};
                color: {TEXT_PRIMARY};
                selection-background-color: {ACCENT};
                selection-color: {BG_PRIMARY};
            }}
            QTableWidget {{
                border: 1px solid {BORDER};
                gridline-color: {BORDER};
                background: {BG_SURFACE};
                font-size: 13px;
            }}
            QTableWidget::item {{
                padding: 6px;
            }}
            QTableWidget::item:selected {{
                background: {BG_CARD};
                color: #ffffff;
            }}
            QHeaderView::section {{
                background: {BG_CARD};
                color: {ACCENT};
                padding: 8px;
                font-weight: bold;
                font-size: 13px;
                border: none;
                border-right: 1px solid {BORDER};
                border-bottom: 2px solid {BORDER};
            }}
            QProgressBar {{
                border: 1px solid {BORDER};
                border-radius: 4px;
                text-align: center;
                background: {BG_SURFACE};
                color: {TEXT_PRIMARY};
                font-size: 10px;
                height: 14px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {ACCENT}, stop:1 #66E6FF);
            }}
            QCheckBox {{
                color: {TEXT_PRIMARY};
                spacing: 5px;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {BORDER};
                border-radius: 3px;
                background: {BG_SURFACE};
            }}
            QCheckBox::indicator:checked {{
                background: {ACCENT};
                border-color: {ACCENT};
            }}
            QLabel {{
                color: {TEXT_PRIMARY};
            }}
        """

    def get_watchlist_names(self):
        try:
            return [wl["name"] for wl in self.db.get_watchlists()]
        except Exception as e:
            print(f"Error getting watchlist names: {e}")
            return []

    def load_watchlist(self, name):
        try:
            items = self.db.get_watchlist_items(name)
            return [item["symbol"] for item in items]
        except Exception as e:
            print(f"Error loading watchlist {name}: {e}")
            return []

    def save_watchlist(self, name, symbols):
        try:
            if not name.startswith("US_"):
                name = "US_" + name
            self.db.create_watchlist(name)
            items = self.db.get_watchlist_items(name)
            existing_symbols = {item["symbol"].upper() for item in items}
            new_symbols = {sym.upper().strip() for sym in symbols if sym.strip()}
            
            for sym in existing_symbols:
                if sym not in new_symbols:
                    self.db.remove_from_watchlist(name, sym)
            for sym in new_symbols:
                if sym not in existing_symbols:
                    self.db.add_to_watchlist(name, sym)
        except Exception as e:
            print(f"Error saving watchlist {name}: {e}")

    def delete_watchlist(self, name):
        try:
            self.db.delete_watchlist(name)
        except Exception as e:
            print(f"Error deleting watchlist {name}: {e}")

    def refresh_watchlists(self):
        db_names = sorted(self.get_watchlist_names())
        finviz_presets = ["Finviz: " + name for name in sorted(PRESET_STRATEGIES.keys())]
        
        populate_watchlist_combobox(self.wl_combo, db_names, finviz_presets)
        
        current_wl = self.wl_combo.currentText()
        if current_wl and current_wl != "── Finviz Presets ──":
            self.on_wl_tab_changed(current_wl)
        else:
            self.wl_edited_symbols = []
            self.wl_fetched_data = {}
            self.wl_symbol_count.setText("0 symbols")
            self.wl_symbol_table.setRowCount(0)

    def on_wl_tab_changed(self, name):
        self.wl_fetched_data = {}
        if not name or name == "── Finviz Presets ──":
            self.wl_edited_symbols = []
            self.populate_wl_symbol_table()
            return
            
        if name.startswith("Finviz: "):
            self.set_editing_enabled(False)
            preset_name = name[len("Finviz: "):]
            if preset_name in self.finviz_preset_cache:
                self.wl_edited_symbols = self.finviz_preset_cache[preset_name]
            else:
                self.wl_edited_symbols = []
        else:
            self.set_editing_enabled(True)
            self.wl_edited_symbols = self.load_watchlist(name)

        # Pre-load cache from disk if available
        for symbol in self.wl_edited_symbols:
            cache_path = get_wl_cache_path(symbol)
            if cache_path.exists():
                try:
                    with QMutexLocker(mutex):
                        df = pd.read_parquet(cache_path)
                    if df is not None and not df.empty:
                        row_dict = df.iloc[0].to_dict()
                        row_dict['is_cached'] = True
                        self.wl_fetched_data[symbol] = row_dict
                except Exception as e:
                    print(f"Error loading initial cache for {symbol}: {e}")
                    
        self.populate_wl_symbol_table()

    def set_editing_enabled(self, enabled):
        self.wl_import_btn.setEnabled(True)
        self.wl_new_btn.setEnabled(True)
        self.wl_save_as_btn.setEnabled(True)
        self.wl_delete_btn.setEnabled(enabled)
        self.wl_add_symbol_btn.setEnabled(enabled)
        self.wl_remove_symbol_btn.setEnabled(enabled)
        self.wl_save_btn.setEnabled(enabled)

    def on_preset_fetch_error(self, error_msg):
        QMessageBox.warning(self, "Error", f"Failed to fetch Finviz preset symbols: {error_msg}")
        self.wl_progress.setVisible(False)
        self.wl_status.setText("")
        self.wl_fetch_btn.setEnabled(True)
        
    def on_preset_fetch_result(self, symbols):
        self.wl_progress.setRange(0, 100)
        self.wl_progress.setValue(0)
        self.wl_progress.setVisible(False)
        self.wl_status.setText("")
        
        name = self.wl_combo.currentText()
        if name.startswith("Finviz: "):
            preset_name = name[len("Finviz: "):]
            self.finviz_preset_cache[preset_name] = symbols
            self.wl_edited_symbols = symbols
            self.populate_wl_symbol_table()
            
            # Trigger optimized fetch worker now that we have the symbols
            self._start_wl_fetch_worker()

    def populate_wl_symbol_table(self):
        # Scan for existing checked symbols to preserve check states
        checked_symbols = set()
        for r in range(self.wl_symbol_table.rowCount()):
            chk_item = self.wl_symbol_table.item(r, 0)
            if chk_item and chk_item.checkState() == Qt.Checked:
                sym_item = self.wl_symbol_table.item(r, 1)
                if sym_item:
                    checked_symbols.add(sym_item.text().strip().upper())

        # Disable sorting to prevent row scrambling during insertions
        self.wl_symbol_table.setSortingEnabled(False)
        
        self.wl_symbol_table.setRowCount(len(self.wl_edited_symbols))
        
        cell_font = QFont("Segoe UI", 12)
        bold_cell_font = QFont("Segoe UI", 12, QFont.Bold)
        
        for i, symbol in enumerate(self.wl_edited_symbols):
            # Native checkbox item
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if symbol in checked_symbols:
                chk_item.setCheckState(Qt.Checked)
            else:
                chk_item.setCheckState(Qt.Unchecked)
            self.wl_symbol_table.setItem(i, 0, chk_item)
            
            # Symbol
            sym_item = QTableWidgetItem(symbol)
            sym_item.setTextAlignment(Qt.AlignCenter)
            sym_item.setFont(bold_cell_font)
            self.wl_symbol_table.setItem(i, 1, sym_item)
            
            fd = self.wl_fetched_data.get(symbol)
            if fd:
                # Price
                price = fd.get('price')
                price_item = QTableWidgetItem()
                price_item.setTextAlignment(Qt.AlignCenter)
                if price is not None:
                    price_item.setData(Qt.DisplayRole, f"{price:.2f}")
                    price_item.setData(Qt.EditRole, float(price))
                else:
                    price_item.setData(Qt.DisplayRole, "N/A")
                    price_item.setData(Qt.EditRole, -1.0)
                
                # Light amber tint if cached (subtle, not overpowering)
                if fd.get('is_cached'):
                    price_item.setBackground(QColor("#3D2E0A"))
                    price_item.setForeground(QColor("#FCD34D"))
                    price_item.setFont(bold_cell_font)
                else:
                    price_item.setFont(cell_font)
                self.wl_symbol_table.setItem(i, 2, price_item)
                
                # % Change
                pct = fd.get('pct_change')
                pct_item = QTableWidgetItem()
                pct_item.setTextAlignment(Qt.AlignCenter)
                pct_item.setFont(bold_cell_font)
                if pct is not None:
                    sign = "+" if pct > 0 else ""
                    pct_item.setData(Qt.DisplayRole, f"{sign}{pct:.2f}%")
                    pct_item.setData(Qt.EditRole, float(pct))
                    if pct > 0:
                        pct_item.setForeground(QColor(SUCCESS))
                    elif pct < 0:
                        pct_item.setForeground(QColor(DANGER))
                else:
                    pct_item.setData(Qt.DisplayRole, "N/A")
                    pct_item.setData(Qt.EditRole, -999.0)
                self.wl_symbol_table.setItem(i, 3, pct_item)
                
                # Volume
                vol = fd.get('volume')
                vol_item = QTableWidgetItem()
                vol_item.setTextAlignment(Qt.AlignCenter)
                vol_item.setFont(cell_font)
                if vol is not None:
                    vol_item.setData(Qt.DisplayRole, f"{vol:,.0f}")
                    vol_item.setData(Qt.EditRole, int(vol))
                else:
                    vol_item.setData(Qt.DisplayRole, "N/A")
                    vol_item.setData(Qt.EditRole, -1)
                self.wl_symbol_table.setItem(i, 4, vol_item)
                
                # Support, Resistance, SMAs
                def populate_num_cell(col, val):
                    item = QTableWidgetItem()
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setFont(cell_font)
                    if val is not None and not (isinstance(val, float) and np.isnan(val)):
                        item.setData(Qt.DisplayRole, f"{val:.2f}")
                        item.setData(Qt.EditRole, float(val))
                    else:
                        item.setData(Qt.DisplayRole, "N/A")
                        item.setData(Qt.EditRole, -1.0)
                    self.wl_symbol_table.setItem(i, col, item)
                
                populate_num_cell(5, fd.get('support'))
                populate_num_cell(6, fd.get('resistance'))
                populate_num_cell(7, fd.get('sma20'))
                populate_num_cell(8, fd.get('sma50'))
                populate_num_cell(9, fd.get('sma200'))
            else:
                # Price N/A
                p_item = QTableWidgetItem("N/A")
                p_item.setTextAlignment(Qt.AlignCenter)
                p_item.setFont(cell_font)
                p_item.setData(Qt.EditRole, -1.0)
                self.wl_symbol_table.setItem(i, 2, p_item)
                
                # % Change N/A
                pct_item = QTableWidgetItem("N/A")
                pct_item.setTextAlignment(Qt.AlignCenter)
                pct_item.setFont(cell_font)
                pct_item.setData(Qt.EditRole, -999.0)
                self.wl_symbol_table.setItem(i, 3, pct_item)
                
                # Volume N/A
                v_item = QTableWidgetItem("N/A")
                v_item.setTextAlignment(Qt.AlignCenter)
                v_item.setFont(cell_font)
                v_item.setData(Qt.EditRole, -1)
                self.wl_symbol_table.setItem(i, 4, v_item)
                
                for col in range(5, 10):
                    item = QTableWidgetItem("N/A")
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setFont(cell_font)
                    item.setData(Qt.EditRole, -1.0)
                    self.wl_symbol_table.setItem(i, col, item)
                    
        # Re-enable sorting
        self.wl_symbol_table.setSortingEnabled(True)
        self.wl_symbol_count.setText(f"{len(self.wl_edited_symbols)} symbols")
        self.symbols_changed.emit(self.wl_edited_symbols)
    
    def wl_add_symbol(self):
        symbol, ok = QInputDialog.getText(self, "Add Symbol", "Enter US stock symbol:")
        if ok and symbol:
            symbol = symbol.strip().upper()
            if symbol and symbol not in self.wl_edited_symbols:
                self.wl_edited_symbols.append(symbol)
                self.wl_fetched_data.pop(symbol, None)
                self.populate_wl_symbol_table()
            else:
                QMessageBox.warning(self, "Error", "Symbol is empty or already exists in this watchlist")
    
    def wl_remove_checked_symbols(self):
        symbols_to_remove = []
        for i in range(self.wl_symbol_table.rowCount()):
            chk_item = self.wl_symbol_table.item(i, 0)
            if chk_item and chk_item.checkState() == Qt.Checked:
                sym_item = self.wl_symbol_table.item(i, 1)
                if sym_item:
                    symbols_to_remove.append(sym_item.text().strip().upper())
        
        if not symbols_to_remove:
            QMessageBox.information(self, "Info", "No symbols selected for removal")
            return
        
        self.wl_edited_symbols = [s for s in self.wl_edited_symbols if s not in symbols_to_remove]
        self.populate_wl_symbol_table()
    
    def wl_save(self):
        name = self.wl_combo.currentText()
        if not name:
            QMessageBox.warning(self, "Error", "No watchlist selected")
            return
        self.save_watchlist(name, self.wl_edited_symbols)
        self.watchlist_changed.emit()
        self.refresh_watchlists()
        QMessageBox.information(self, "Success", f"Saved {len(self.wl_edited_symbols)} symbols to '{name}'")
    
    def wl_save_as(self):
        name, ok = QInputDialog.getText(self, "Save Watchlist As", "Enter new watchlist name:")
        if ok and name:
            if not name.startswith("US_"):
                name = "US_" + name
            self.save_watchlist(name, self.wl_edited_symbols)
            self.watchlist_changed.emit()
            self.refresh_watchlists()
            self.wl_combo.setCurrentText(name)
            QMessageBox.information(self, "Success", f"Saved {len(self.wl_edited_symbols)} symbols to '{name}'")
    
    def wl_fetch_data(self):
        name = self.wl_combo.currentText()
        if not name or name == "── Finviz Presets ──":
            QMessageBox.warning(self, "Error", "No watchlist selected")
            return
            
        # Collect checked symbols
        checked_symbols = []
        for i in range(self.wl_symbol_table.rowCount()):
            chk_item = self.wl_symbol_table.item(i, 0)
            if chk_item and chk_item.checkState() == Qt.Checked:
                sym_item = self.wl_symbol_table.item(i, 1)
                if sym_item:
                    checked_symbols.append(sym_item.text().strip().upper())
                        
        if name.startswith("Finviz: "):
            preset_name = name[len("Finviz: "):]
            if preset_name not in self.finviz_preset_cache or not self.finviz_preset_cache[preset_name]:
                if self.preset_worker and self.preset_worker.isRunning():
                    return
                self.wl_fetch_btn.setEnabled(False)
                self.wl_progress.setVisible(True)
                self.wl_progress.setValue(0)
                self.wl_progress.setRange(0, 0)
                self.wl_status.setText("Fetching Finviz symbols...")
                
                self.preset_worker = FinvizPresetSymbolsWorker(preset_name)
                self.preset_worker.progress.connect(self.wl_status.setText)
                self.preset_worker.error.connect(self.on_preset_fetch_error)
                self.preset_worker.result.connect(self.on_preset_fetch_result)
                self.preset_worker.start()
                return
            else:
                self.wl_edited_symbols = self.finviz_preset_cache[preset_name]
        
        symbols_to_fetch = checked_symbols if checked_symbols else self.wl_edited_symbols
        self._start_wl_fetch_worker(symbols_to_fetch)

    def _start_wl_fetch_worker(self, symbols=None):
        if symbols is None:
            symbols = self.wl_edited_symbols
            
        if not symbols:
            QMessageBox.warning(self, "Error", "No symbols in watchlist")
            self.wl_fetch_btn.setEnabled(True)
            return
            
        if self.wl_fetch_worker and self.wl_fetch_worker.isRunning():
            return
            
        self.wl_fetch_btn.setEnabled(False)
        self.wl_progress.setVisible(True)
        self.wl_progress.setValue(0)
        self.wl_progress.setRange(0, 100)
        self.wl_status.setText("Fetching data...")
        
        self.wl_fetch_worker = WLFetchWorker(symbols)
        self.wl_fetch_worker.progress.connect(self.on_wl_fetch_progress)
        self.wl_fetch_worker.result.connect(self.on_wl_fetch_result)
        self.wl_fetch_worker.error.connect(self.on_wl_fetch_error)
        self.wl_fetch_worker.finished.connect(self.on_wl_fetch_finished)
        self.wl_fetch_worker.start()
    
    def on_wl_fetch_progress(self, value, message):
        self.wl_progress.setValue(value)
        self.wl_status.setText(message)
    
    def on_wl_fetch_result(self, results):
        self.wl_fetched_data.update(results)
        self.populate_wl_symbol_table()
    
    def on_wl_fetch_error(self, error_msg):
        QMessageBox.warning(self, "Error", error_msg)
    
    def on_wl_fetch_finished(self):
        self.wl_fetch_btn.setEnabled(True)
        self.wl_progress.setVisible(False)
        self.wl_status.setText("")
    
    def wl_export_csv(self):
        if not self.wl_edited_symbols:
            QMessageBox.warning(self, "Error", "No symbols to export")
            return
        
        filepath, _ = QFileDialog.getSaveFileName(self, "Export Watchlist CSV", "", "CSV Files (*.csv)")
        if filepath:
            try:
                with open(filepath, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Symbol", "Price", "% Change", "Volume", "Support", "Resistance", "SMA20", "SMA50", "SMA200"])
                    
                    for symbol in self.wl_edited_symbols:
                        fd = self.wl_fetched_data.get(symbol)
                        if fd:
                            writer.writerow([
                                symbol,
                                fd.get('price', ''),
                                fd.get('pct_change', ''),
                                fd.get('volume', ''),
                                fd.get('support', ''),
                                fd.get('resistance', ''),
                                fd.get('sma20', ''),
                                fd.get('sma50', ''),
                                fd.get('sma200', ''),
                            ])
                        else:
                            writer.writerow([symbol] + [''] * 8)
                
                QMessageBox.information(self, "Success", f"Exported {len(self.wl_edited_symbols)} symbols to {filepath}")
            
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to export: {str(e)}")
    
    def import_csv(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Import Watchlist CSV", "", "CSV Files (*.csv);;All Files (*)")
        if filepath:
            try:
                symbols = []
                with open(filepath, 'r') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if row:
                            symbol = row[0].strip().upper()
                            if symbol and symbol not in symbols:
                                symbols.append(symbol)
                
                if symbols:
                    name, ok = QInputDialog.getText(self, "Save Watchlist", "Enter watchlist name:")
                    if ok and name:
                        if not name.startswith("US_"):
                            name = "US_" + name
                        self.save_watchlist(name, symbols)
                        self.watchlist_changed.emit()
                        self.refresh_watchlists()
                        self.wl_combo.setCurrentText(name)
                        QMessageBox.information(self, "Success", f"Imported {len(symbols)} symbols to '{name}'")
                else:
                    QMessageBox.warning(self, "Error", "No valid symbols found in CSV")
            
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to import CSV: {str(e)}")
    
    def new_watchlist(self):
        name, ok = QInputDialog.getText(self, "New Watchlist", "Enter watchlist name:")
        if ok and name:
            if not name.startswith("US_"):
                name = "US_" + name
            symbols_text, ok2 = QInputDialog.getMultiLineText(self, "Add Symbols", "Enter symbols (one per line):")
            if ok2 and symbols_text:
                symbols = [s.strip().upper() for s in symbols_text.split('\n') if s.strip()]
                if symbols:
                    self.save_watchlist(name, symbols)
                    self.watchlist_changed.emit()
                    self.refresh_watchlists()
                    self.wl_combo.setCurrentText(name)
                    QMessageBox.information(self, "Success", f"Created watchlist '{name}' with {len(symbols)} symbols")
    
    def delete_current_watchlist(self):
        name = self.wl_combo.currentText()
        if name:
            reply = QMessageBox.question(self, "Delete Watchlist", f"Delete '{name}'?")
            if reply == QMessageBox.Yes:
                self.delete_watchlist(name)
                self.watchlist_changed.emit()
                self.refresh_watchlists()

    # ── Right-click context menu ─────────────────────────────────────
    def _get_selected_symbols(self):
        """Return list of symbols from selected rows in the watchlist table."""
        selected_rows = set()
        for idx in self.wl_symbol_table.selectedIndexes():
            selected_rows.add(idx.row())
        symbols = []
        for row in sorted(selected_rows):
            sym_item = self.wl_symbol_table.item(row, 1)
            if sym_item:
                sym = sym_item.text().strip().upper()
                if sym and sym not in symbols:
                    symbols.append(sym)
        return symbols

    def _on_wl_context_menu(self, pos):
        item = self.wl_symbol_table.itemAt(pos)
        if item:
            # If the right-clicked row is not part of the current selection, select it
            if item.row() not in [idx.row() for idx in self.wl_symbol_table.selectedIndexes()]:
                self.wl_symbol_table.selectRow(item.row())

        symbols = self._get_selected_symbols()
        if not symbols:
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {BG_SURFACE}; color: {TEXT_PRIMARY};
                border: 1px solid {BORDER}; padding: 4px;
            }}
            QMenu::item {{ padding: 6px 24px; }}
            QMenu::item:selected {{ background: {ACCENT}; color: {BG_PRIMARY}; }}
            QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
        """)

        label_text = symbols[0] if len(symbols) == 1 else f"{len(symbols)} symbols"

        # --- Copy to New Watchlist ---
        act_new = menu.addAction(f"📋 Copy to New Watchlist")
        act_new.triggered.connect(lambda: self._copy_to_new_wl(symbols))

        # --- Copy to Existing Watchlist (submenu) ---
        existing_names = self.get_watchlist_names()
        if existing_names:
            sub = menu.addMenu("📂 Copy to Existing WL")
            sub.setStyleSheet(menu.styleSheet())
            for wl_name in sorted(existing_names):
                act = sub.addAction(wl_name)
                act.triggered.connect(lambda checked=False, n=wl_name: self._copy_to_existing_wl(symbols, n))

        menu.addSeparator()

        # --- Copy to Portfolio ---
        act_port = menu.addAction("💼 Copy to Portfolio")
        act_port.triggered.connect(lambda: self._copy_to_portfolio(symbols))

        # --- External Charts (if single symbol selected) ---
        if len(symbols) == 1:
            menu.addSeparator()
            symbol = symbols[0]
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            
            tv_act = menu.addAction(f"📈 Open {symbol} Chart in TradingView")
            tv_act.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://www.tradingview.com/chart/?symbol={symbol}")))
            
            yf_act = menu.addAction(f"📊 Open {symbol} Chart in Yahoo Finance")
            yf_act.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://finance.yahoo.com/chart/{symbol}")))
            
            fv_act = menu.addAction(f"🔍 Open {symbol} Chart in Finviz")
            fv_act.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://finviz.com/quote.ashx?t={symbol}")))

        menu.exec(self.wl_symbol_table.viewport().mapToGlobal(pos))

    def _copy_to_new_wl(self, symbols):
        name, ok = QInputDialog.getText(self, "New Watchlist", "Enter new watchlist name:")
        if ok and name:
            if not name.startswith("US_"):
                name = "US_" + name
            self.save_watchlist(name, symbols)
            self.watchlist_changed.emit()
            self.refresh_watchlists()
            QMessageBox.information(self, "Success",
                f"Copied {len(symbols)} symbol(s) to new watchlist '{name}'.")

    def _copy_to_existing_wl(self, symbols, target_name):
        existing = self.load_watchlist(target_name)
        merged = list(existing)
        added = 0
        for s in symbols:
            if s not in merged:
                merged.append(s)
                added += 1
        self.save_watchlist(target_name, merged)
        self.watchlist_changed.emit()
        self.refresh_watchlists()
        QMessageBox.information(self, "Success",
            f"Added {added} new symbol(s) to '{target_name}' ({len(merged)} total).")

    def _copy_to_portfolio(self, symbols):
        portfolio_name = "US_Portfolio"
        existing = self.load_watchlist(portfolio_name)
        merged = list(existing)
        added = 0
        for s in symbols:
            if s not in merged:
                merged.append(s)
                added += 1
        self.save_watchlist(portfolio_name, merged)
        self.watchlist_changed.emit()
        self.refresh_watchlists()
        QMessageBox.information(self, "Success",
            f"Added {added} symbol(s) to Portfolio ({len(merged)} total).")

    def _on_wl_double_clicked(self, idx):
        # Column 1 is "Symbol" in wl_symbol_table
        it = self.wl_symbol_table.item(idx.row(), 1)
        if it:
            symbol = it.text().strip().upper()
            if symbol and symbol != "N/A":
                self.go_explore.emit(symbol)
