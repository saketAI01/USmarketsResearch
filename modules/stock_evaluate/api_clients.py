"""
API clients for FMP (stable), Alpaca, AlphaVantage, and Wikipedia constituent lists.
"""
import time
import io
import requests
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf
from .logger import get_logger


class FMPClient:
    """FMP Stable API client (post-Aug 2025 migration)."""
    BASE = "https://financialmodelingprep.com/stable"
    _BLOCKED = False

    def __init__(self, api_key):
        self.key = api_key
        self.session = requests.Session()

    def _get(self, endpoint, params=None):
        if FMPClient._BLOCKED: return []
        params = params or {}
        params["apikey"] = self.key
        try:
            r = self.session.get(f"{self.BASE}/{endpoint}", params=params, timeout=15)
            if r.status_code == 429:
                get_logger("FMPClient").warning(f"429 Rate Limit Hit on {endpoint}. Sleeping 2s...")
                time.sleep(2)
                r = self.session.get(f"{self.BASE}/{endpoint}", params=params, timeout=15)
                if r.status_code == 429:
                    FMPClient._BLOCKED = True
                    return []
            if r.status_code == 402: return []
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        except Exception as e:
            get_logger("FMPClient").error(f"FMP Request Error on {endpoint}: {e}")
            return []

    def _get_single(self, endpoint, params=None):
        if FMPClient._BLOCKED: return []
        params = params or {}
        params["apikey"] = self.key
        try:
            r = requests.get(f"{self.BASE}/{endpoint}", params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(1)
                r = requests.get(f"{self.BASE}/{endpoint}", params=params, timeout=15)
                if r.status_code == 429:
                    FMPClient._BLOCKED = True
                    get_logger("FMPClient").warning("FMP 429 Limit Reach. Blocking further requests.")
                    return []
            if r.status_code == 402: return []
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        except Exception as e:
            get_logger("FMPClient").error(f"FMP Single Error on {endpoint}: {e}")
            return []

    def get_batch_quotes(self, symbols, progress_cb=None, detailed_symbols=None):
        """Fetch quotes. If detailed_symbols is provided, only fetch supplemental data for those."""
        import concurrent.futures
        import threading
        log = get_logger("FMPClient")
        FMPClient._BLOCKED = False 

        results = []
        lock = threading.Lock()
        total = len(symbols)
        completed = [0]
        stop_all = [False]
        detailed_set = set(detailed_symbols or [])

        def _fetch_one(sym):
            if stop_all[0] or FMPClient._BLOCKED: 
                with lock:
                    completed[0] += 1
                    if progress_cb: progress_cb(completed[0], total, sym)
                return None
            
            # 1. Basic Quote
            data = self._get_single("quote", {"symbol": sym})
            if not data:
                with lock:
                    completed[0] += 1
                    if progress_cb: progress_cb(completed[0], total, sym)
                return None
            
            quote = data[0]
            
            # 2. Supplemental data (only if requested, not blocked, and basic quote is missing PE)
            if quote.get("price") and sym in detailed_set and not FMPClient._BLOCKED:
                if quote.get("pe") is None:
                    for ep in ["ratios-ttm", "key-metrics-ttm", "financial-growth"]:
                        if FMPClient._BLOCKED: break
                        params = {"symbol": sym}
                        if ep == "financial-growth": params["limit"] = "1"
                        
                        supp = self._get_single(ep, params)
                        if supp:
                            s = supp[0]
                            if ep == "ratios-ttm":
                                quote.update({
                                    "pe": s.get("priceToEarningsRatioTTM"),
                                    "pb": s.get("priceToBookRatioTTM"),
                                    "de": s.get("debtToEquityRatioTTM"),
                                    "div": s.get("dividendYieldTTM"),
                                    "eps": s.get("netIncomePerShareTTM")
                                })
                            elif ep == "key-metrics-ttm":
                                quote["roe"] = s.get("returnOnEquityTTM")
                            elif ep == "financial-growth":
                                quote["rev_growth"] = (s.get("revenueGrowth") or 0) * 100
                                quote["eps_growth"] = (s.get("epsgrowth") or 0) * 100
                        
                        time.sleep(0.4)
                else:
                    # We already have PE from quote, maybe just get ROE if missing
                    pass
            
            with lock:
                completed[0] += 1
                results.append(quote)
                if progress_cb:
                    progress_cb(completed[0], total, sym)
            return quote

        # Using only 2 workers for FMP to avoid overwhelming the free tier
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(_fetch_one, symbols))

        log.info(f"FMP Batch Complete: Fetched {len(results)}/{total} symbols")
        return results

    def get_profile(self, symbol):
        data = self._get("profile", {"symbol": symbol})
        return data[0] if data else {}

    def get_key_metrics_ttm(self, symbol):
        data = self._get("key-metrics-ttm", {"symbol": symbol})
        return data[0] if data else {}

    def get_ratios_ttm(self, symbol):
        data = self._get("ratios-ttm", {"symbol": symbol})
        return data[0] if data else {}

    def get_financial_growth(self, symbol):
        data = self._get("financial-growth", {"symbol": symbol, "limit": "1"})
        return data[0] if data else {}

    def get_index_quotes(self):
        """Fetch major index quotes with fallbacks for free tier."""
        # Primary indices (often blocked on free tier)
        primary = {"^GSPC": "S&P 500", "^IXIC": "NASDAQ", "^DJI": "DOW JONES", "^VIX": "VIX"}
        # Proxy ETFs (reliable on free tier)
        proxies = {"SPY": "S&P 500", "QQQ": "NASDAQ", "DIA": "DOW JONES", "VIXY": "VIX"}
        
        results = {}
        # Try primary first
        for sym, name in primary.items():
            data = self._get("quote", {"symbol": sym})
            if data and data[0].get("price"):
                results[sym] = data[0]
            else:
                # Try without ^
                data = self._get("quote", {"symbol": sym.replace("^", "")})
                if data and data[0].get("price"):
                    results[sym] = data[0]
                else:
                    # Fallback to proxy
                    proxy_sym = [k for k, v in proxies.items() if v == name][0]
                    data = self._get("quote", {"symbol": proxy_sym})
                    if data and data[0].get("price"):
                        # Map back to index symbol for UI
                        results[sym] = data[0]
            time.sleep(0.1)
        return results

    def get_movers(self, type_="gainers"):
        """Fetch top gainers/losers from FMP."""
        backup_base = self.BASE
        self.BASE = "https://financialmodelingprep.com/api/v3"
        try:
            data = self._get(f"stock_market/{type_}", {})
            return data if isinstance(data, list) else []
        finally:
            self.BASE = backup_base




class WikipediaConstituents:
    """Fetch S&P 500 and NASDAQ-100 constituent lists from Wikipedia."""

    _HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    @classmethod
    def _fetch_html(cls, url):
        r = requests.get(url, headers=cls._HEADERS, timeout=15)
        r.raise_for_status()
        return r.text

    @classmethod
    def get_sp500(cls):
        """Scrape S&P 500 tickers from Wikipedia."""
        try:
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            html = cls._fetch_html(url)
            tables = pd.read_html(io.StringIO(html), header=0)
            df = tables[0]
            results = []
            for _, row in df.iterrows():
                sym = str(row.get("Symbol", "")).strip().replace(".", "-")
                results.append({
                    "symbol": sym,
                    "company_name": str(row.get("Security", "")),
                    "sector": str(row.get("GICS Sector", "")),
                    "industry": str(row.get("GICS Sub-Industry", "")),
                    "exchange": "NYSE",
                })
            print(f"[Wiki] S&P 500: {len(results)} symbols")
            return results
        except Exception as e:
            print(f"[Wiki] SP500 error: {e}")
            return []

    @classmethod
    def get_nasdaq100(cls):
        """Scrape NASDAQ-100 tickers from Wikipedia."""
        try:
            url = "https://en.wikipedia.org/wiki/Nasdaq-100"
            html = cls._fetch_html(url)
            tables = pd.read_html(io.StringIO(html), header=0)
            # Find the table with 'Ticker' or 'Symbol' column
            df = None
            for t in tables:
                cols = [c.lower() for c in t.columns]
                if "ticker" in cols or "symbol" in cols:
                    df = t
                    break
            if df is None and tables:
                df = tables[0]
            if df is None:
                return []

            sym_col = None
            name_col = None
            sector_col = None
            for c in df.columns:
                cl = c.lower()
                if cl in ("ticker", "symbol"):
                    sym_col = c
                elif cl in ("company", "security", "name"):
                    name_col = c
                elif "sector" in cl or "industry" in cl:
                    sector_col = c

            if not sym_col:
                return []

            results = []
            for _, row in df.iterrows():
                sym = str(row.get(sym_col, "")).strip().replace(".", "-")
                if not sym or len(sym) > 6:
                    continue
                results.append({
                    "symbol": sym,
                    "company_name": str(row.get(name_col, "")) if name_col else "",
                    "sector": str(row.get(sector_col, "")) if sector_col else "",
                    "industry": "",
                    "exchange": "NASDAQ",
                })
            print(f"[Wiki] NASDAQ-100: {len(results)} symbols")
            return results
        except Exception as e:
            print(f"[Wiki] NASDAQ-100 error: {e}")
            return []


class AlpacaClient:
    BASE = "https://data.alpaca.markets/v2"

    def __init__(self, api_key, secret_key):
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        })

    def get_bars(self, symbols, start=None, end=None, timeframe="1Day", limit=1000):
        """Fetch daily bars for multiple symbols. Returns dict of symbol->DataFrame."""
        if not start:
            start = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%dT00:00:00Z")
        if not end:
            end = datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z")

        all_bars = {}
        for i in range(0, len(symbols), 30):
            batch = symbols[i:i+30]
            params = {
                "symbols": ",".join(batch),
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "limit": limit,
                "feed": "iex",
            }
            try:
                next_token = None
                while True:
                    if next_token:
                        params["page_token"] = next_token
                    r = self.session.get(f"{self.BASE}/stocks/bars", params=params, timeout=30)
                    r.raise_for_status()
                    data = r.json()
                    bars = data.get("bars", {})
                    for sym, bar_list in bars.items():
                        if sym not in all_bars:
                            all_bars[sym] = []
                        all_bars[sym].extend(bar_list)
                    next_token = data.get("next_page_token")
                    if not next_token:
                        break
            except Exception as e:
                print(f"[Alpaca] Error batch {i}: {e}")
            time.sleep(0.3)

        result = {}
        for sym, bars in all_bars.items():
            if not bars:
                continue
            df = pd.DataFrame(bars)
            df = df.rename(columns={"t": "date", "o": "open", "h": "high",
                                     "l": "low", "c": "close", "v": "volume"})
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df[["date", "open", "high", "low", "close", "volume"]]
            df = df.sort_values("date").reset_index(drop=True)
            result[sym] = df
        return result

    def get_snapshot(self, symbol):
        try:
            r = self.session.get(f"{self.BASE}/stocks/{symbol}/snapshot",
                                params={"feed": "iex"}, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[Alpaca] Snapshot error {symbol}: {e}")
            return {}

    def get_batch_snapshots(self, symbols):
        """Fetch snapshots for multiple symbols. Returns list of FMP-compatible
        quote dicts."""
        log = get_logger("AlpacaClient")
        
        def _parse_snap(sym, snap):
            if not snap: return None
            daily = snap.get("dailyBar", {})
            prev = snap.get("prevDailyBar", {})
            trade = snap.get("latestTrade", {})
            price = trade.get("p") or daily.get("c", 0)
            prev_close = prev.get("c", 0)
            chg_pct = ((price - prev_close) / prev_close * 100 if prev_close else 0)
            return {
                "symbol": sym.replace("/", "-").replace(".", "-"),
                "price": price,
                "changePercentage": round(chg_pct, 4),
                "volume": daily.get("v", 0),
                "yearHigh": daily.get("h", 0),
                "yearLow": daily.get("l", 0),
                "marketCap": 0,
            }

        results = []
        for i in range(0, len(symbols), 50):
            batch = symbols[i:i+50]
            sanitized = [s.replace("-", "/").replace(".", "/") for s in batch]
            if not sanitized: continue
            
            try:
                r = self.session.get(
                    f"{self.BASE}/stocks/snapshots",
                    params={"symbols": ",".join(sanitized), "feed": "iex"},
                    timeout=20)
                
                if r.status_code == 400:
                    log.warning(f"Alpaca batch 400. Trying symbols individually...")
                    for s_one in sanitized:
                        try:
                            s_data = self.get_snapshot(s_one)
                            parsed = _parse_snap(s_one, s_data)
                            if parsed: results.append(parsed)
                        except: pass
                    continue

                r.raise_for_status()
                data = r.json()
                for sym, snap in data.items():
                    parsed = _parse_snap(sym, snap)
                    if parsed: results.append(parsed)
            except Exception as e:
                log.error(f"Alpaca Batch Error: {e}")
            time.sleep(0.3)
        return results


class YFinanceClient:
    """Free fallback for fundamental data."""
    
    @staticmethod
    def get_info(symbol):
        """Fetch stock info (fundamentals)."""
        # Convert BRK-B or BRK.B to BRK-B (yfinance preference)
        sym = symbol.replace("/", "-").replace(".", "-")
        try:
            ticker = yf.Ticker(sym)
            return ticker.info
        except Exception as e:
            get_logger("YFinance").error(f"Error fetching info for {sym}: {e}")
            return {}

class AlphaVantageClient:
    BASE = "https://www.alphavantage.co/query"

    def __init__(self, api_key):
        self.key = api_key
        self.session = requests.Session()

    def _get(self, function, symbol, extra=None):
        params = {"function": function, "symbol": symbol, "apikey": self.key}
        if extra:
            params.update(extra)
        try:
            r = self.session.get(self.BASE, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[AV] Error {function}/{symbol}: {e}")
            return {}

    def get_overview(self, symbol):
        return self._get("OVERVIEW", symbol)

    def get_daily(self, symbol, outputsize="compact"):
        data = self._get("TIME_SERIES_DAILY", symbol, {"outputsize": outputsize})
        ts = data.get("Time Series (Daily)", {})
        if not ts:
            return pd.DataFrame()
        df = pd.DataFrame.from_dict(ts, orient="index")
        df = df.rename(columns={"1. open": "open", "2. high": "high",
                                 "3. low": "low", "4. close": "close",
                                 "5. volume": "volume"}).astype(float)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df = df.reset_index().rename(columns={"index": "date"})
        df["date"] = df["date"].dt.date
        return df
