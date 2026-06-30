"""
Dual-source data fetcher with disk caching.

Source strategy:
  US  — Alpaca (price/OHLCV) -> yFinance fallback
        FMP (fundamentals + institutional) -> yFinance fallback
  IN  — yFinance (all data; FMP India coverage is thin)
        Benchmark: ^NSEI (Nifty 50)

Caching:
  StockData objects are cached to ~/.canslim_pro/cache/.
  Default TTL is 12 hours.  Pass use_cache=False for a forced refresh.
"""
from __future__ import annotations
import time
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from dataclasses import dataclass, field
from typing import Optional

from config import US_BENCHMARK, IN_BENCHMARK, FMP_BASE, get_keys
import core.cache as cache_module


def detect_market(ticker: str) -> str:
    return "IN" if ticker.upper().endswith((".NS", ".BO")) else "US"


def get_benchmark(market: str) -> str:
    return IN_BENCHMARK if market == "IN" else US_BENCHMARK


def _safe(info: dict, *keys, default=0.0):
    for k in keys:
        v = info.get(k)
        if v is not None and v == v:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return default


def _eps_from_fin(fin_df, shares: float) -> list:
    if fin_df is None or fin_df.empty or shares <= 0:
        return []
    try:
        for label in ["Net Income", "Net Income Common Stockholders",
                      "Net Income Applicable To Common Shares"]:
            if label in fin_df.index:
                return (fin_df.loc[label].dropna() / shares).tolist()
        return []
    except Exception:
        return []


def _rev_from_fin(fin_df) -> list:
    if fin_df is None or fin_df.empty:
        return []
    try:
        for label in ["Total Revenue", "Revenue"]:
            if label in fin_df.index:
                return fin_df.loc[label].dropna().tolist()
        return []
    except Exception:
        return []


@dataclass
class StockData:
    ticker: str
    market: str
    company_name: str = ""
    sector: str = ""
    current_price: float = 0.0
    high_52w: float = 0.0
    low_52w: float = 0.0
    market_cap: float = 0.0
    shares_float: float = 0.0
    shares_outstanding: float = 0.0
    avg_volume: float = 0.0

    quarterly_eps: list = field(default_factory=list)
    annual_eps: list = field(default_factory=list)
    quarterly_revenue: list = field(default_factory=list)
    annual_revenue: list = field(default_factory=list)

    price_history: Optional[pd.DataFrame] = None
    benchmark_history: Optional[pd.DataFrame] = None

    institutional_pct: float = 0.0
    institutional_count: int = 0
    currency: str = "USD"
    price_source: str = "yfinance"
    fundamental_source: str = "yfinance"
    institutional_source: str = "yfinance"
    from_cache: bool = False
    cached_at: str = ""

    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.current_price > 0


# ── FMP ───────────────────────────────────────────────────────────────────────

def _fmp_get(endpoint: str, api_key: str, params: dict = None):
    if not api_key:
        return []
    url = f"{FMP_BASE}/{endpoint}"
    p   = {"apikey": api_key}
    if params:
        p.update(params)
    try:
        r = requests.get(url, params=p, timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def _fmp_fundamentals(ticker: str, api_key: str) -> dict:
    out = {"quarterly_eps": [], "quarterly_rev": [],
           "annual_eps":    [], "annual_rev":    [],
           "institutional_pct": 0.0, "institutional_count": 0}
    if not api_key:
        return out

    ann = _fmp_get(f"income-statement/{ticker}", api_key, {"limit": 5, "period": "annual"})
    if isinstance(ann, list) and ann:
        out["annual_eps"] = [float(x.get("eps") or 0) for x in ann]
        out["annual_rev"] = [float(x.get("revenue") or 0) for x in ann]

    qtr = _fmp_get(f"income-statement/{ticker}", api_key, {"limit": 8, "period": "quarter"})
    if isinstance(qtr, list) and qtr:
        out["quarterly_eps"] = [float(x.get("eps") or 0) for x in qtr]
        out["quarterly_rev"] = [float(x.get("revenue") or 0) for x in qtr]

    inst = _fmp_get(f"institutional-holder/{ticker}", api_key)
    if isinstance(inst, list) and inst:
        out["institutional_pct"]   = float(inst[0].get("ownedPercent") or 0) * 100
        out["institutional_count"] = len(inst)

    return out


# ── Alpaca ────────────────────────────────────────────────────────────────────

def _alpaca_price_history(ticker: str) -> tuple[pd.DataFrame, str, float]:
    try:
        from core.alpaca_client import make_alpaca_client
        client = make_alpaca_client()
        if not client.available:
            return pd.DataFrame(), "yfinance", 0.0
        df = client.get_daily_bars(ticker, days=380)
        if df is None or df.empty:
            return pd.DataFrame(), "yfinance", 0.0
        return df, "alpaca", float(df["Close"].iloc[-1])
    except Exception:
        return pd.DataFrame(), "yfinance", 0.0


# ── Main fetch ────────────────────────────────────────────────────────────────

def fetch_stock_data(ticker: str,
                     fmp_api_key: str = "",
                     alpaca_key_id: str = "",
                     alpaca_secret: str = "",
                     delay: float = 0.4,
                     use_cache: bool = True,
                     cache_ttl_hrs: float = 12.0) -> StockData:
    """
    Fetch all CANSLIM data for one ticker.
    Keys are auto-discovered from config.get_keys() if not passed explicitly.
    Set use_cache=False to force a fresh fetch even if cached data is available.
    """
    keys = get_keys()
    if not fmp_api_key:
        fmp_api_key   = keys.get("FMP_API_KEY", "")
    if not alpaca_key_id:
        alpaca_key_id = keys.get("ALPACA_KEY_ID", "")
    if not alpaca_secret:
        alpaca_secret = keys.get("ALPACA_SECRET_KEY", "")

    # ── Cache check ───────────────────────────────────────────────────────────
    if use_cache:
        cached = cache_module.get(ticker, max_age_hrs=cache_ttl_hrs)
        if cached is not None:
            cached.from_cache = True
            cached.cached_at  = cache_module.age_str(ticker)
            return cached

    market = detect_market(ticker)
    sd     = StockData(ticker=ticker, market=market)
    sd.currency = "INR" if market == "IN" else "USD"

    # ── yFinance baseline ─────────────────────────────────────────────────────
    try:
        yt   = yf.Ticker(ticker)
        info = yt.info or {}

        sd.company_name       = info.get("longName") or info.get("shortName") or ticker
        sd.sector             = info.get("sector", "")
        sd.current_price      = _safe(info, "currentPrice", "regularMarketPrice", "previousClose")
        sd.high_52w           = _safe(info, "fiftyTwoWeekHigh")
        sd.low_52w            = _safe(info, "fiftyTwoWeekLow")
        sd.market_cap         = _safe(info, "marketCap")
        sd.shares_outstanding = _safe(info, "sharesOutstanding", "impliedSharesOutstanding") or 1
        sd.shares_float       = _safe(info, "floatShares") or sd.shares_outstanding
        sd.avg_volume         = _safe(info, "averageVolume", "averageDailyVolume10Day")
        sd.institutional_pct  = _safe(info, "heldPercentInstitutions") * 100

        try:
            ann_fin = yt.financials
            qtr_fin = yt.quarterly_financials
            sd.annual_eps        = _eps_from_fin(ann_fin, sd.shares_outstanding)
            sd.quarterly_eps     = _eps_from_fin(qtr_fin, sd.shares_outstanding)
            sd.annual_revenue    = _rev_from_fin(ann_fin)
            sd.quarterly_revenue = _rev_from_fin(qtr_fin)
        except Exception as e:
            sd.errors.append(f"yF financials: {e}")

        try:
            ph = yt.history(period="1y", auto_adjust=True)
            if ph is not None and not ph.empty:
                ph.index = pd.to_datetime(ph.index).tz_localize(None)
                sd.price_history = ph
                sd.price_source  = "yfinance"
        except Exception as e:
            sd.errors.append(f"yF price history: {e}")

    except Exception as e:
        sd.errors.append(f"yFinance: {e}")

    # ── Alpaca upgrade (US only) ───────────────────────────────────────────────
    if market == "US":
        alp_hist, alp_src, alp_price = _alpaca_price_history(ticker)
        if not alp_hist.empty:
            sd.price_history = alp_hist
            sd.price_source  = "alpaca"
            if alp_price > 0:
                sd.current_price = alp_price
            sd.high_52w = float(alp_hist["High"].max())
            sd.low_52w  = float(alp_hist["Low"].min())

    # ── FMP upgrade (US only) ─────────────────────────────────────────────────
    if market == "US" and fmp_api_key:
        try:
            fmp = _fmp_fundamentals(ticker, fmp_api_key)
            if fmp["annual_eps"]:
                sd.annual_eps        = fmp["annual_eps"]
                sd.annual_revenue    = fmp["annual_rev"]
                sd.fundamental_source = "fmp"
            if fmp["quarterly_eps"]:
                sd.quarterly_eps     = fmp["quarterly_eps"]
                sd.quarterly_revenue = fmp["quarterly_rev"]
            if fmp["institutional_pct"] > 0:
                sd.institutional_pct   = fmp["institutional_pct"]
                sd.institutional_count = fmp["institutional_count"]
                sd.institutional_source = "fmp"
        except Exception as e:
            sd.errors.append(f"FMP: {e}")

    # ── Benchmark ─────────────────────────────────────────────────────────────
    try:
        bh = yf.Ticker(get_benchmark(market)).history(period="1y", auto_adjust=True)
        if bh is not None and not bh.empty:
            bh.index = pd.to_datetime(bh.index).tz_localize(None)
            sd.benchmark_history = bh
    except Exception as e:
        sd.errors.append(f"Benchmark: {e}")

    # ── Save to cache ─────────────────────────────────────────────────────────
    cache_module.put(ticker, sd)

    time.sleep(delay)
    return sd
