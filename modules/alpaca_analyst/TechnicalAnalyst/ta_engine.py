#!/usr/bin/env python3
"""
ta_engine.py — Technical Analyst Pro
Data fetching (yFinance / Alpaca / FMP), SQLite caching,
full TA calculation engine, and matplotlib chart generator.
"""

from __future__ import annotations

import os, sys, json, sqlite3, time, warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
warnings.filterwarnings("ignore")

# ── pandas (required) ────────────────────────────────────────────────────────
try:
    import pandas as pd
except ImportError:
    print("CRITICAL: pandas missing. Run: pip install pandas")
    sys.exit(1)

# ── optional data sources ────────────────────────────────────────────────────
try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ── matplotlib (required for charts) ────────────────────────────────────────
try:
    import matplotlib
    matplotlib.use("QtAgg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.patches import Rectangle
    from matplotlib.lines import Line2D
    MATPLOTLIB_OK = True
except ImportError:
    MATPLOTLIB_OK = False
    print("WARNING: matplotlib not available – charts disabled.")


# ═══════════════════════════════════════════════════════════════════════════
#  CACHE MANAGER
# ═══════════════════════════════════════════════════════════════════════════
class CacheManager:
    """SQLite-backed key/value cache with per-entry TTL."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS cache(
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                ts   REAL NOT NULL,
                ttl  INTEGER NOT NULL DEFAULT 3600
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ts ON cache(ts)")
            c.commit()

    def get(self, key: str) -> Optional[Any]:
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT data, ts, ttl FROM cache WHERE key=?", (key,)
            ).fetchone()
            if row:
                data, ts, ttl = row
                if time.time() - ts < ttl:
                    return json.loads(data)
                c.execute("DELETE FROM cache WHERE key=?", (key,))
        return None

    def set(self, key: str, data: Any, ttl: int = 3600):
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                "INSERT OR REPLACE INTO cache(key,data,ts,ttl) VALUES(?,?,?,?)",
                (key, json.dumps(data, default=str), time.time(), ttl),
            )
            c.commit()

    def clear(self, symbol: Optional[str] = None):
        with sqlite3.connect(self.db_path) as c:
            if symbol:
                c.execute("DELETE FROM cache WHERE key LIKE ?", (f"%{symbol}%",))
            else:
                c.execute("DELETE FROM cache")
            c.commit()

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as c:
            n = c.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {"entries": n, "size_kb": size // 1024}


# ═══════════════════════════════════════════════════════════════════════════
#  DATA FETCHER
# ═══════════════════════════════════════════════════════════════════════════
class DataFetcher:
    """Unified OHLCV fetcher: yFinance → Alpaca → FMP, with caching."""

    _YF_INTERVAL = {"Daily": "1d", "Weekly": "1wk", "Monthly": "1mo"}
    _YF_PERIOD   = {
        "3 Months": "3mo", "6 Months": "6mo",
        "1 Year": "1y",    "2 Years": "2y",
        "5 Years": "5y",   "10 Years": "10y",
    }
    _PERIOD_DAYS = {
        "3 Months": 90,  "6 Months": 180, "1 Year": 365,
        "2 Years": 730,  "5 Years": 1825, "10 Years": 3650,
    }

    def __init__(self, cache: CacheManager, credentials: dict = None):
        self.cache = cache
        self.creds = credentials or {}

    # ── public API ────────────────────────────────────────────────────────
    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str = "Weekly",
        period: str = "2 Years",
        source: str = "auto",
    ) -> Optional[pd.DataFrame]:
        ck = f"ohlcv|{symbol}|{interval}|{period}"
        hit = self.cache.get(ck)
        if hit:
            df = pd.DataFrame(hit)
            df.index = pd.to_datetime(df.index)
            return df

        df = None
        if source in ("auto", "yfinance") and YFINANCE_OK:
            df = self._yf(symbol, interval, period)
        if df is None and source in ("auto", "alpaca"):
            df = self._alpaca(symbol, interval, period)
        if df is None and source in ("auto", "fmp"):
            df = self._fmp(symbol, interval, period)

        if df is not None and not df.empty:
            tmp = df.copy()
            tmp.index = tmp.index.astype(str)
            self.cache.set(ck, tmp.to_dict(), ttl=3600)
        return df

    def get_info(self, symbol: str) -> dict:
        ck = f"info|{symbol}"
        hit = self.cache.get(ck)
        if hit:
            return hit
        info = {"name": symbol, "sector": "Unknown", "exchange": "", "currency": "USD"}
        if YFINANCE_OK:
            try:
                t = yf.Ticker(symbol)
                d = t.info
                info.update({
                    "name":     d.get("longName", symbol),
                    "sector":   d.get("sector", "Unknown"),
                    "exchange": d.get("exchange", ""),
                    "currency": d.get("currency", "USD"),
                    "market_cap": d.get("marketCap", 0),
                    "description": (d.get("longBusinessSummary") or "")[:300],
                })
            except Exception:
                pass
        self.cache.set(ck, info, ttl=86400)
        return info

    # ── yFinance ─────────────────────────────────────────────────────────
    def _yf(self, symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(
                period=self._YF_PERIOD.get(period, "2y"),
                interval=self._YF_INTERVAL.get(interval, "1wk"),
                auto_adjust=True,
            )
            if df is None or df.empty:
                return None
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy().dropna()
            df.index = pd.to_datetime(df.index)
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            return df
        except Exception as e:
            print(f"[yFinance] {symbol}: {e}")
            return None

    # ── Alpaca ────────────────────────────────────────────────────────────
    def _alpaca(self, symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
        key    = self.creds.get("alpaca_key", "")
        secret = self.creds.get("alpaca_secret", "")
        if not (key and secret and REQUESTS_OK):
            return None
        try:
            days  = self._PERIOD_DAYS.get(period, 730)
            start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
            end   = datetime.now().strftime("%Y-%m-%dT00:00:00Z")
            tf_map = {"Daily": "1Day", "Weekly": "1Week", "Monthly": "1Month"}
            url  = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
            resp = requests.get(url, params={
                "timeframe": tf_map.get(interval, "1Week"),
                "start": start, "end": end,
                "limit": 1000, "adjustment": "split",
            }, headers={
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
            }, timeout=30)
            if resp.status_code != 200:
                return None
            bars = resp.json().get("bars", [])
            if not bars:
                return None
            df = pd.DataFrame(bars)
            df["t"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
            df = df.set_index("t").rename(columns={
                "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"
            })[["Open", "High", "Low", "Close", "Volume"]]
            return df
        except Exception as e:
            print(f"[Alpaca] {symbol}: {e}")
            return None

    # ── FMP ───────────────────────────────────────────────────────────────
    def _fmp(self, symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
        key = self.creds.get("fmp_key", "")
        if not (key and REQUESTS_OK):
            return None
        try:
            days = self._PERIOD_DAYS.get(period, 730)
            url  = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}"
            resp = requests.get(url, params={"apikey": key, "timeseries": days}, timeout=30)
            if resp.status_code != 200:
                return None
            hist = resp.json().get("historical", [])
            if not hist:
                return None
            df = pd.DataFrame(hist)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index().rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })[["Open", "High", "Low", "Close", "Volume"]]
            # Resample to requested interval
            freq_map = {"Weekly": "W", "Monthly": "ME", "Daily": "B"}
            freq = freq_map.get(interval, "W")
            if interval != "Daily":
                df = df.resample(freq).agg({
                    "Open": "first", "High": "max",
                    "Low": "min",   "Close": "last",
                    "Volume": "sum",
                }).dropna()
            return df
        except Exception as e:
            print(f"[FMP] {symbol}: {e}")
            return None


# ═══════════════════════════════════════════════════════════════════════════
#  TECHNICAL ANALYZER
# ═══════════════════════════════════════════════════════════════════════════
class TechnicalAnalyzer:
    """Full technical analysis: trend, S/R, MAs, volume, patterns, scenarios."""

    # ── entry point ──────────────────────────────────────────────────────
    def analyze(self, df: pd.DataFrame, symbol: str = "") -> dict:
        if df is None or df.empty or len(df) < 15:
            return {"error": "Insufficient data (need ≥ 15 bars)"}

        df = self._add_indicators(df)

        result: dict = {
            "symbol":         symbol,
            "analysis_date":  datetime.now().strftime("%Y-%m-%d"),
            "current_price":  float(df["Close"].iloc[-1]),
            "chg_1bar":       self._pct(df, 1),
            "chg_4bar":       self._pct(df, 4),
            "chg_52bar":      self._pct(df, 52),
            "bars_total":     len(df),
        }

        result["trend"]             = self._trend(df)
        result["support_levels"]    = self._supports(df)
        result["resistance_levels"] = self._resistances(df)
        result["moving_averages"]   = self._moving_averages(df)
        result["volume"]            = self._volume(df)
        result["patterns"]          = self._patterns(df)
        result["assessment"]        = self._assessment(result)
        result["scenarios"]         = self._scenarios(result, df)
        result["key_observations"]  = self._key_obs(result)

        return result

    # ── indicator calculation ─────────────────────────────────────────────
    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for p in [20, 50, 200]:
            if len(df) >= p:
                df[f"MA{p}"]  = df["Close"].rolling(p).mean()
                df[f"EMA{p}"] = df["Close"].ewm(span=p, adjust=False).mean()

        # ATR
        hi, lo, cl = df["High"], df["Low"], df["Close"].shift(1)
        df["ATR"] = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - cl).abs(),
            (df["Low"]  - cl).abs(),
        ], axis=1).max(axis=1).rolling(14).mean()

        # RSI
        d = df["Close"].diff()
        g = d.where(d > 0, 0).rolling(14).mean()
        l = (-d.where(d < 0, 0)).rolling(14).mean()
        df["RSI"] = 100 - 100 / (1 + g / l.replace(0, np.inf))

        # Volume MA & ratio
        df["VolMA20"] = df["Volume"].rolling(20).mean()
        df["VolRatio"] = df["Volume"] / df["VolMA20"].replace(0, np.nan)

        # Bollinger Bands
        if len(df) >= 20:
            ma = df["Close"].rolling(20).mean()
            sd = df["Close"].rolling(20).std()
            df["BB_Upper"] = ma + 2 * sd
            df["BB_Lower"] = ma - 2 * sd
            df["BB_Width"]  = (df["BB_Upper"] - df["BB_Lower"]) / ma

        return df

    def _pct(self, df: pd.DataFrame, n: int) -> float:
        if len(df) > n:
            return round((df["Close"].iloc[-1] / df["Close"].iloc[-n-1] - 1) * 100, 2)
        return 0.0

    # ── trend ────────────────────────────────────────────────────────────
    def _trend(self, df: pd.DataFrame) -> dict:
        highs  = df["High"].values
        lows   = df["Low"].values
        closes = df["Close"].values
        current = closes[-1]

        sh = self._swings(highs, "high")
        sl = self._swings(lows,  "low")
        sh, sl = sh[-8:], sl[-8:]

        hh = sum(1 for i in range(1, len(sh)) if sh[i] > sh[i-1])
        hl = sum(1 for i in range(1, len(sl)) if sl[i] > sl[i-1])
        lh = sum(1 for i in range(1, len(sh)) if sh[i] < sh[i-1])
        ll = sum(1 for i in range(1, len(sl)) if sl[i] < sl[i-1])

        # MA alignment score
        ma_bull = 0
        for p in [20, 50, 200]:
            col = f"MA{p}"
            if col in df.columns:
                v = df[col].iloc[-1]
                if not np.isnan(v) and current > v:
                    ma_bull += 1
        if "MA20" in df.columns and "MA50" in df.columns:
            v20, v50 = df["MA20"].iloc[-1], df["MA50"].iloc[-1]
            if not (np.isnan(v20) or np.isnan(v50)) and v20 > v50:
                ma_bull += 1
        if "MA50" in df.columns and "MA200" in df.columns:
            v50, v200 = df["MA50"].iloc[-1], df["MA200"].iloc[-1]
            if not (np.isnan(v50) or np.isnan(v200)) and v50 > v200:
                ma_bull += 1

        n_sh = max(len(sh)-1, 1);  n_sl = max(len(sl)-1, 1)
        bull_score = (hh/n_sh + hl/n_sl + ma_bull/5) / 3
        bear_score = (lh/n_sh + ll/n_sl + (5-ma_bull)/5) / 3

        if   bull_score > 0.60: direction, strength = "UPTREND",   ("STRONG" if bull_score > 0.75 else "MODERATE")
        elif bear_score > 0.60: direction, strength = "DOWNTREND", ("STRONG" if bear_score > 0.75 else "MODERATE")
        else:                   direction, strength = "SIDEWAYS",  "WEAK"

        rsi = float(df["RSI"].iloc[-1]) if "RSI" in df.columns and not np.isnan(df["RSI"].iloc[-1]) else 50.0
        exhaustion = []
        if direction == "UPTREND"   and rsi > 70: exhaustion.append(f"RSI overbought ({rsi:.0f})")
        if direction == "DOWNTREND" and rsi < 30: exhaustion.append(f"RSI oversold ({rsi:.0f})")

        # Volume divergence
        vtrend = self._vol_trend_pct(df, 8)
        if direction == "UPTREND"   and vtrend < -10: exhaustion.append("Volume declining on rallies")
        if direction == "DOWNTREND" and vtrend < -10: exhaustion.append("Volume declining on drops (exhaustion possible)")

        dur = self._trend_dur(closes, direction)
        desc = (
            f"{strength} {direction.lower()} — {hh} HH, {hl} HL pattern over ~{dur} bars."
            if direction == "UPTREND" else
            f"{strength} {direction.lower()} — {lh} LH, {ll} LL pattern over ~{dur} bars."
            if direction == "DOWNTREND" else
            f"Sideways / range-bound price action over ~{dur} bars. No clear HH/HL or LH/LL chain."
        )

        return {
            "direction": direction, "strength": strength, "duration_bars": dur,
            "hh": hh, "hl": hl, "lh": lh, "ll": ll,
            "bull_score": round(bull_score, 2), "bear_score": round(bear_score, 2),
            "ma_bullish_signals": ma_bull, "rsi": round(rsi, 1),
            "exhaustion_signals": exhaustion, "description": desc,
        }

    def _swings(self, prices: np.ndarray, kind: str, w: int = 3) -> np.ndarray:
        pts = []
        for i in range(w, len(prices)-w):
            window = prices[i-w:i+w+1]
            if kind == "high" and prices[i] == window.max(): pts.append(prices[i])
            elif kind == "low" and prices[i] == window.min(): pts.append(prices[i])
        return np.array(pts) if pts else np.array([prices[0]])

    def _trend_dur(self, closes: np.ndarray, direction: str) -> int:
        for i in range(len(closes)-1, 0, -1):
            if direction == "UPTREND"   and closes[i] < closes[i-1] * 0.94: return len(closes)-i
            if direction == "DOWNTREND" and closes[i] > closes[i-1] * 1.06: return len(closes)-i
        return len(closes) // 2

    # ── support / resistance ──────────────────────────────────────────────
    def _pivot_levels(self, df: pd.DataFrame, kind: str) -> List[dict]:
        prices = df["High"].values if kind == "resistance" else df["Low"].values
        closes = df["Close"].values
        current = closes[-1]
        cands = []
        for i in range(3, len(prices)-3):
            if kind == "resistance" and prices[i] == max(prices[i-3:i+4]): cands.append(prices[i])
            if kind == "support"    and prices[i] == min(prices[i-3:i+4]): cands.append(prices[i])

        # Add dynamic MA levels
        for p in [20, 50, 200]:
            col = f"MA{p}"
            if col in df.columns:
                v = df[col].iloc[-1]
                if not np.isnan(v): cands.append(v)

        clusters = self._cluster(cands, tol=0.015)
        result = []
        for price, count in clusters:
            above = price > current * 0.995
            below = price < current * 1.005
            if (kind == "resistance" and above) or (kind == "support" and below):
                dist = (price - current) / current * 100
                result.append({
                    "price":    round(price, 2),
                    "strength": "STRONG" if count >= 3 else ("MODERATE" if count >= 2 else "WEAK"),
                    "touches":  count,
                    "distance_pct": round(abs(dist), 1),
                })
        result.sort(key=lambda x: x["distance_pct"])
        return result[:5]

    def _supports(self, df): return self._pivot_levels(df, "support")
    def _resistances(self, df): return self._pivot_levels(df, "resistance")

    def _cluster(self, vals: List[float], tol: float = 0.015) -> List[Tuple]:
        if not vals: return []
        s = sorted(vals)
        used = [False]*len(s)
        out  = []
        for i, v in enumerate(s):
            if used[i]: continue
            grp = [v]
            for j in range(i+1, len(s)):
                if not used[j] and abs(s[j]-v)/v < tol:
                    grp.append(s[j]); used[j] = True
            out.append((float(np.mean(grp)), len(grp))); used[i] = True
        return out

    # ── moving averages ───────────────────────────────────────────────────
    def _moving_averages(self, df: pd.DataFrame) -> dict:
        current = df["Close"].iloc[-1]
        result  = {}
        for p in [20, 50, 200]:
            col = f"MA{p}"
            if col in df.columns:
                v = df[col].iloc[-1]
                if np.isnan(v): continue
                recent = df[col].dropna().iloc[-5:]
                slope_pct = ((recent.iloc[-1]-recent.iloc[0])/recent.iloc[0]*100) if len(recent) >= 2 else 0
                slope = "RISING" if slope_pct > 0.3 else ("FALLING" if slope_pct < -0.3 else "FLAT")
                rel   = "ABOVE" if current > v*1.005 else ("BELOW" if current < v*0.995 else "AT")
                result[f"ma{p}"] = {
                    "value": round(v, 2),
                    "slope": slope,
                    "price_relation": rel,
                    "distance_pct": round((current-v)/v*100, 2),
                }
        # Alignment
        vs = {p: result.get(f"ma{p}", {}).get("value") for p in [20,50,200]}
        if all(vs.values()):
            result["alignment"] = (
                "BULLISH" if vs[20]>vs[50]>vs[200] else
                "BEARISH" if vs[20]<vs[50]<vs[200] else "MIXED"
            )
        elif vs[20] and vs[50]:
            result["alignment"] = "BULLISH" if vs[20]>vs[50] else "BEARISH"
        else:
            result["alignment"] = "UNKNOWN"

        # Golden / Death cross (last 10 bars)
        if "MA20" in df.columns and "MA50" in df.columns:
            rec = df[["MA20","MA50"]].dropna().iloc[-10:]
            if len(rec) >= 3:
                result["golden_cross"] = bool(rec["MA20"].iloc[-1] > rec["MA50"].iloc[-1] and rec["MA20"].iloc[-3] < rec["MA50"].iloc[-3])
                result["death_cross"]  = bool(rec["MA20"].iloc[-1] < rec["MA50"].iloc[-1] and rec["MA20"].iloc[-3] > rec["MA50"].iloc[-3])
        return result

    # ── volume ────────────────────────────────────────────────────────────
    def _volume(self, df: pd.DataFrame) -> dict:
        vols   = df["Volume"].values
        closes = df["Close"].values
        if len(vols) < 5:
            return {"trend": "UNKNOWN", "description": "Insufficient data"}

        vtrend_pct = self._vol_trend_pct(df, 10)
        vol_trend  = "INCREASING" if vtrend_pct > 5 else ("DECREASING" if vtrend_pct < -5 else "STABLE")

        avg_vol  = float(np.mean(vols[-20:])) if len(vols) >= 20 else float(np.mean(vols))
        cur_vol  = float(vols[-1])
        ratio    = cur_vol / avg_vol if avg_vol > 0 else 1.0
        is_spike = ratio > 1.8

        price_up = closes[-1] > closes[-5]
        vol_up   = vtrend_pct > 0
        confirmation = (
            "CONFIRMING" if price_up == vol_up else
            "BEARISH_DIVERGENCE" if price_up and not vol_up else
            "BULLISH_DIVERGENCE"
        )

        desc = (
            f"Volume is {vol_trend.lower()}. "
            + (f"Current bar is {ratio:.1f}× the 20-bar average — spike detected. " if is_spike else "")
            + {
                "CONFIRMING":         "Volume confirms the current price move.",
                "BEARISH_DIVERGENCE": "Price rising on declining volume — weak conviction (bearish divergence).",
                "BULLISH_DIVERGENCE": "Price declining on falling volume — selling exhaustion possible (bullish divergence).",
            }[confirmation]
        )

        return {
            "trend": vol_trend, "trend_pct": round(vtrend_pct, 1),
            "avg_volume": int(avg_vol), "recent_volume": int(cur_vol),
            "spike": is_spike, "spike_ratio": round(ratio, 1),
            "confirmation": confirmation, "description": desc,
        }

    def _vol_trend_pct(self, df: pd.DataFrame, n: int = 10) -> float:
        v = df["Volume"].values
        if len(v) < n*2: return 0.0
        r = np.mean(v[-n:]);  p = np.mean(v[-2*n:-n])
        return (r-p)/p*100 if p else 0.0

    # ── patterns ──────────────────────────────────────────────────────────
    def _patterns(self, df: pd.DataFrame) -> List[dict]:
        pats = []
        if len(df) < 5: return pats
        o,h,l,c = df["Open"].values, df["High"].values, df["Low"].values, df["Close"].values
        i = len(c)-1

        body = abs(c[i]-o[i])
        rng  = h[i]-l[i]
        if rng < 1e-9: return pats
        upper = h[i]-max(c[i],o[i])
        lower = min(c[i],o[i])-l[i]
        br    = body/rng

        # Single-bar
        if br < 0.10:
            pats.append({"name":"Doji","type":"REVERSAL_SIGNAL","direction":"NEUTRAL","confidence":"MODERATE","description":"Indecision — buyer/seller equilibrium; possible reversal."})
        elif lower > body*2 and upper < body*0.5 and c[i]>o[i]:
            pats.append({"name":"Hammer","type":"REVERSAL","direction":"BULLISH","confidence":"HIGH","description":"Long lower shadow — buyers rejected lower prices."})
        elif upper > body*2 and lower < body*0.5 and c[i]<o[i]:
            pats.append({"name":"Shooting Star","type":"REVERSAL","direction":"BEARISH","confidence":"HIGH","description":"Long upper shadow — sellers rejected higher prices."})
        elif br > 0.85 and c[i]>o[i]:
            pats.append({"name":"Bullish Marubozu","type":"CONTINUATION","direction":"BULLISH","confidence":"MODERATE","description":"Strong full-body green candle — bullish momentum."})
        elif br > 0.85 and c[i]<o[i]:
            pats.append({"name":"Bearish Marubozu","type":"CONTINUATION","direction":"BEARISH","confidence":"MODERATE","description":"Strong full-body red candle — bearish momentum."})

        # Two-bar engulfing
        if i >= 1:
            pb = abs(c[i-1]-o[i-1]); cb = abs(c[i]-o[i])
            if c[i]>o[i] and c[i-1]<o[i-1] and c[i]>o[i-1] and o[i]<c[i-1] and cb>pb:
                pats.append({"name":"Bullish Engulfing","type":"REVERSAL","direction":"BULLISH","confidence":"HIGH","description":"Green candle engulfs prior red — strong bullish reversal signal."})
            elif c[i]<o[i] and c[i-1]>o[i-1] and c[i]<o[i-1] and o[i]>c[i-1] and cb>pb:
                pats.append({"name":"Bearish Engulfing","type":"REVERSAL","direction":"BEARISH","confidence":"HIGH","description":"Red candle engulfs prior green — strong bearish reversal signal."})

        # Chart patterns (20-bar)
        if len(c) >= 20:
            early, late = c[-20:-10], c[-10:]
            em = (early[-1]-early[0])/early[0]*100
            lr = (max(late)-min(late))/late[0]*100
            if em > 8 and lr < 5:
                pats.append({"name":"Bull Flag","type":"CONTINUATION","direction":"BULLISH","confidence":"MODERATE","description":f"Strong +{em:.1f}% thrust followed by tight {lr:.1f}% consolidation."})
            elif em < -8 and lr < 5:
                pats.append({"name":"Bear Flag","type":"CONTINUATION","direction":"BEARISH","confidence":"MODERATE","description":f"Strong {em:.1f}% decline followed by tight {lr:.1f}% consolidation."})

        return pats

    # ── assessment ───────────────────────────────────────────────────────
    def _assessment(self, r: dict) -> str:
        td = r.get("trend",{})
        ma = r.get("moving_averages",{})
        vd = r.get("volume",{})
        dir_= td.get("direction","SIDEWAYS"); str_= td.get("strength","WEAK")
        aln = ma.get("alignment","UNKNOWN");  cnf = vd.get("confirmation","")

        bull = 0; bear = 0
        if dir_=="UPTREND":   bull += (2 if str_=="STRONG" else 1)
        elif dir_=="DOWNTREND": bear += (2 if str_=="STRONG" else 1)
        if aln=="BULLISH": bull+=2
        elif aln=="BEARISH": bear+=2
        if "CONFIRMING" in cnf and dir_=="UPTREND": bull+=1
        if "BEARISH" in cnf: bear+=1
        if td.get("exhaustion_signals"): (bear if dir_=="UPTREND" else bull).__add__(1)

        bias = "NEUTRAL"
        if bull > bear*1.5: bias="BULLISH"
        elif bear > bull*1.5: bias="BEARISH"
        elif bull or bear: bias="MIXED"

        tbl = {
            "BULLISH":  f"The chart presents a bullish technical structure. The {str_.lower()} {dir_.lower()} is supported by {aln.lower()} MA alignment. Volume is {cnf.lower().replace('_',' ')}. The overall bias favors continued upside with pullbacks to support as entry opportunities.",
            "BEARISH":  f"The chart shows bearish technical characteristics. The {str_.lower()} {dir_.lower()} is reinforced by {aln.lower()} MA alignment. Selling pressure dominates. The overall bias favors continued downside.",
            "MIXED":    f"The chart presents mixed signals. The {dir_.lower()} ({str_.lower()}) lacks full MA confirmation ({aln.lower()} alignment). Key price levels will be decisive for the next directional move.",
            "NEUTRAL":  f"Price is in a sideways/consolidation phase with no clear directional bias. Oscillation between support and resistance is likely until a catalyst drives a breakout.",
        }
        return tbl.get(bias, tbl["NEUTRAL"])

    # ── scenarios ─────────────────────────────────────────────────────────
    def _scenarios(self, r: dict, df: pd.DataFrame) -> List[dict]:
        td   = r.get("trend",{})
        sup  = r.get("support_levels",[])
        res  = r.get("resistance_levels",[])
        ma   = r.get("moving_averages",{})
        vd   = r.get("volume",{})
        cur  = r.get("current_price",0)
        dir_ = td.get("direction","SIDEWAYS"); str_= td.get("strength","WEAK")
        aln  = ma.get("alignment","UNKNOWN")
        cnf  = vd.get("confirmation","")

        # Base probabilities
        if   dir_=="UPTREND"   and str_=="STRONG" and aln=="BULLISH":  bp,np_,brp = 60,25,15
        elif dir_=="DOWNTREND" and str_=="STRONG" and aln=="BEARISH":  bp,np_,brp = 15,25,60
        elif dir_=="UPTREND":   bp,np_,brp = 45,30,25
        elif dir_=="DOWNTREND": bp,np_,brp = 20,30,50
        else:                   bp,np_,brp = 30,45,25

        if td.get("exhaustion_signals"):
            if dir_=="UPTREND":   bp-=10; brp+=10
            elif dir_=="DOWNTREND": brp-=10; bp+=10
        if "CONFIRMING"  in cnf and dir_=="UPTREND":   bp = min(bp+5,70)
        if "BEARISH"     in cnf: brp = min(brp+5,65); bp = max(bp-5,10)
        if "BULLISH_DIV" in cnf: bp  = min(bp+5,65)

        tot = bp+np_+brp
        bp  = round(bp/tot*100); brp = round(brp/tot*100); np_ = 100-bp-brp

        s1 = sup[0]["price"]  if sup  else cur*0.95
        s2 = sup[1]["price"]  if len(sup)>1  else cur*0.90
        r1 = res[0]["price"]  if res  else cur*1.05
        r2 = res[1]["price"]  if len(res)>1 else cur*1.10
        ma50  = ma.get("ma50",{}).get("value", cur*0.93)
        ma200 = ma.get("ma200",{}).get("value", cur*0.85)

        bull_f = []
        if dir_=="UPTREND": bull_f.append(f"{str_.lower()} uptrend (HH/HL structure intact)")
        if aln=="BULLISH":  bull_f.append("Bullish MA alignment (MA20 > MA50 > MA200)")
        if "CONFIRMING" in cnf: bull_f.append("Volume confirming price action")
        bull_f.append(f"Support at ${s1:,.2f} providing downside floor")
        if not bull_f: bull_f = ["Price holding above key moving averages"]

        bear_f = []
        if dir_=="DOWNTREND": bear_f.append("Established downtrend (LH/LL structure)")
        if aln in ("BEARISH","MIXED"): bear_f.append(f"Moving averages in {aln.lower()} configuration")
        bear_f += td.get("exhaustion_signals",[])
        bear_f.append(f"Resistance at ${r1:,.2f} capping upside")
        if not bear_f: bear_f = ["Overhead resistance may limit gains", "Potential trend exhaustion"]

        return [
            {
                "name": "Bull Case: Continuation & Breakout",
                "type": "BULLISH", "probability": bp,
                "description": (
                    f"Price sustains the {'uptrend' if dir_!='DOWNTREND' else 'recovery'}, "
                    f"clears resistance at ${r1:,.2f} on expanding volume, "
                    f"and targets ${r2:,.2f}. MA alignment supports further upside."
                ),
                "supporting_factors": bull_f[:4],
                "target_levels": [round(r1,2), round(r2,2)],
                "invalidation_level": round(s1,2),
            },
            {
                "name": "Base Case: Range / Consolidation",
                "type": "NEUTRAL", "probability": np_,
                "description": (
                    f"Price oscillates between support (${s1:,.2f}) and "
                    f"resistance (${r1:,.2f}) without a decisive breakout. "
                    f"Mixed signals suggest a pause before the next trend leg."
                ),
                "supporting_factors": [
                    f"Price bounded between ${s1:,.2f} and ${r1:,.2f}",
                    "Conflicting bullish/bearish signals — no clear catalyst",
                    "MA compression / convergence phase possible",
                ],
                "target_levels": [round(s1,2), round(r1,2)],
                "invalidation_level": round(min(s1*0.97, s2), 2),
            },
            {
                "name": "Bear Case: Support Breakdown",
                "type": "BEARISH", "probability": brp,
                "description": (
                    f"Price breaks below support at ${s1:,.2f}, triggering further "
                    f"decline toward ${s2:,.2f} or the MA50 at ${ma50:,.2f}. "
                    f"A high-volume breakdown would confirm the move."
                ),
                "supporting_factors": bear_f[:4],
                "target_levels": [round(s1,2), round(s2,2)],
                "invalidation_level": round(r1, 2),
            },
        ]

    # ── key observations ──────────────────────────────────────────────────
    def _key_obs(self, r: dict) -> List[str]:
        obs = []
        td, ma, vd = r.get("trend",{}), r.get("moving_averages",{}), r.get("volume",{})
        sup, res, pats = r.get("support_levels",[]), r.get("resistance_levels",[]), r.get("patterns",[])

        obs.append(f"Primary trend: {td.get('direction','?')} ({td.get('strength','?').lower()} strength)")
        aln = ma.get("alignment","")
        if aln in ("BULLISH","BEARISH"): obs.append(f"Moving averages in {aln.lower()} alignment")
        if ma.get("golden_cross"): obs.append("Golden Cross detected (MA20 crossed above MA50) — bullish signal")
        if ma.get("death_cross"):  obs.append("Death Cross detected (MA20 crossed below MA50) — bearish signal")
        if sup:  obs.append(f"Nearest support:    ${sup[0]['price']:,.2f} ({sup[0]['strength'].lower()})")
        if res:  obs.append(f"Nearest resistance: ${res[0]['price']:,.2f} ({res[0]['strength'].lower()})")
        obs.append(f"Volume: {vd.get('trend','?').lower()} — {vd.get('confirmation','?').lower().replace('_',' ')}")
        if pats: obs.append(f"Pattern: {pats[0]['name']} ({pats[0]['direction'].lower()} {pats[0]['type'].lower()})")
        for ex in td.get("exhaustion_signals",[]): obs.append(f"⚠ Exhaustion: {ex}")
        return obs[:7]


# ═══════════════════════════════════════════════════════════════════════════
#  CHART GENERATOR
# ═══════════════════════════════════════════════════════════════════════════
class ChartGenerator:
    """Dark-themed, annotated candlestick chart with TA overlays."""

    C = {
        "bg":      "#1a1a2e", "panel":   "#16213e", "grid":    "#2d2d44",
        "text":    "#e0e0e0", "up":      "#06d6a0", "down":    "#e94560",
        "ma20":    "#00b4d8", "ma50":    "#ffd700", "ma200":   "#ff6b6b",
        "vup":     "#06d6a045","vdown":  "#e9456045",
        "sup":     "#06d6a0", "res":     "#e94560",
        "price":   "#ffffff", "bb":      "#8888aa",
    }

    def __init__(self):
        if MATPLOTLIB_OK:
            plt.rcParams.update({
                "axes.facecolor":   self.C["bg"],
                "figure.facecolor": self.C["bg"],
                "text.color":       self.C["text"],
                "axes.labelcolor":  self.C["text"],
                "xtick.color":      self.C["text"],
                "ytick.color":      self.C["text"],
                "axes.edgecolor":   self.C["grid"],
                "grid.color":       self.C["grid"],
                "grid.alpha":       0.4,
            })

    def create(
        self,
        df: pd.DataFrame,
        analysis: dict = None,
        title: str = "",
        show_ma:  bool = True,
        show_vol: bool = True,
        show_sr:  bool = True,
        show_bb:  bool = False,
    ) -> "Figure":
        if not MATPLOTLIB_OK or df is None or df.empty:
            return self._empty(title)

        fig = Figure(figsize=(13, 7.5), facecolor=self.C["bg"])
        gs  = fig.add_gridspec(2, 1, height_ratios=[4, 1], hspace=0.04) if show_vol else None

        ax  = fig.add_subplot(gs[0] if gs else 111)
        axv = fig.add_subplot(gs[1], sharex=ax) if show_vol and gs else None

        for a in [ax, axv]:
            if a: a.set_facecolor(self.C["bg"])

        x = np.arange(len(df))
        self._candles(ax, df, x)
        if show_ma:  self._mas(ax, df, x)
        if show_bb:  self._bb(ax, df, x)
        if show_sr and analysis: self._sr(ax, df, analysis)
        self._price_line(ax, df)
        if show_vol and axv: self._volume(axv, df, x)

        self._style(ax, axv, df, title)
        self._legend(ax, df, show_ma)
        fig.tight_layout(pad=0.5)
        return fig

    def _candles(self, ax, df, x):
        w = 0.65
        for i, (_, row) in enumerate(df.iterrows()):
            up    = row["Close"] >= row["Open"]
            col   = self.C["up"] if up else self.C["down"]
            bot   = min(row["Open"], row["Close"])
            ht    = max(abs(row["Close"]-row["Open"]), 1e-9)
            ax.add_patch(Rectangle((i-w/2, bot), w, ht, fc=col, ec=col, lw=0.5, alpha=0.92))
            ax.plot([i,i], [row["Low"], bot],          color=col, lw=0.8, alpha=0.8)
            ax.plot([i,i], [bot+ht, row["High"]],      color=col, lw=0.8, alpha=0.8)

        n = min(12, len(df))
        ticks = np.linspace(0, len(df)-1, n, dtype=int)
        ax.set_xticks(ticks)
        ax.set_xticklabels(
            [df.index[i].strftime("%b '%y") if hasattr(df.index[i],"strftime") else str(df.index[i])
             for i in ticks], rotation=40, ha="right", fontsize=7
        )
        ax.set_xlim(-1, len(df)+1)
        lo, hi = df["Low"].min(), df["High"].max()
        pad = (hi-lo)*0.06
        ax.set_ylim(lo-pad, hi+pad*3.5)

    def _mas(self, ax, df, x):
        for col, clr, lw, ls in [
            ("MA20",  self.C["ma20"],  1.4, "--"),
            ("MA50",  self.C["ma50"],  1.8, "-"),
            ("MA200", self.C["ma200"], 2.0, "-"),
        ]:
            if col in df.columns:
                v = df[col].values; m = ~np.isnan(v)
                if m.any(): ax.plot(x[m], v[m], color=clr, lw=lw, ls=ls, alpha=0.85)

    def _bb(self, ax, df, x):
        for col in ("BB_Upper","BB_Lower"):
            if col in df.columns:
                v = df[col].values; m = ~np.isnan(v)
                if m.any(): ax.plot(x[m], v[m], color=self.C["bb"], lw=0.8, ls=":", alpha=0.6)
        if "BB_Upper" in df.columns and "BB_Lower" in df.columns:
            u = df["BB_Upper"].values; l = df["BB_Lower"].values
            m = ~(np.isnan(u)|np.isnan(l))
            ax.fill_between(x[m], u[m], l[m], alpha=0.04, color=self.C["bb"])

    def _sr(self, ax, df, analysis):
        mx = len(df)-1
        for lvl in analysis.get("support_levels",[]):
            p   = lvl["price"]; st = lvl.get("strength","WEAK")
            alp = 0.85 if st=="STRONG" else (0.6 if st=="MODERATE" else 0.4)
            lw  = 1.4 if st=="STRONG" else 0.9
            ax.axhline(y=p, color=self.C["sup"], ls="--", lw=lw, alpha=alp)
            ax.text(mx*0.01, p, f"S ${p:,.2f}", color=self.C["sup"], fontsize=7, va="bottom", alpha=alp)
        for lvl in analysis.get("resistance_levels",[]):
            p   = lvl["price"]; st = lvl.get("strength","WEAK")
            alp = 0.85 if st=="STRONG" else (0.6 if st=="MODERATE" else 0.4)
            lw  = 1.4 if st=="STRONG" else 0.9
            ax.axhline(y=p, color=self.C["res"], ls="--", lw=lw, alpha=alp)
            ax.text(mx*0.01, p, f"R ${p:,.2f}", color=self.C["res"], fontsize=7, va="top", alpha=alp)

    def _price_line(self, ax, df):
        cp = df["Close"].iloc[-1]
        ax.axhline(y=cp, color=self.C["price"], ls="--", lw=0.7, alpha=0.6)
        ax.text(len(df)-1, cp, f"  ${cp:,.2f}", va="center",
                color=self.C["price"], fontsize=9, fontweight="bold")

    def _volume(self, axv, df, x):
        for i, (_, row) in enumerate(df.iterrows()):
            col = self.C["vup"] if row["Close"]>=row["Open"] else self.C["vdown"]
            axv.bar(i, row["Volume"], color=col, width=0.75, ec="none")
        if "VolMA20" in df.columns:
            v = df["VolMA20"].values; m = ~np.isnan(v)
            axv.plot(x[m], v[m], color="#9090b0", lw=1.0, alpha=0.7)
        axv.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v,_: f"{v/1e6:.1f}M" if v>=1e6 else f"{v/1e3:.0f}K")
        )
        axv.set_ylabel("Volume", color=self.C["text"], fontsize=8)

    def _style(self, ax, axv, df, title):
        for a in [ax, axv]:
            if not a: continue
            a.set_facecolor(self.C["bg"])
            a.grid(True, color=self.C["grid"], alpha=0.3, lw=0.5)
            for sp in a.spines.values(): sp.set_color(self.C["grid"])
            a.tick_params(colors=self.C["text"], labelsize=8)
        ax.set_ylabel("Price", color=self.C["text"], fontsize=9)
        if title:
            ax.set_title(title, color=self.C["text"], fontsize=12, fontweight="bold", pad=8)
        if axv: plt.setp(ax.get_xticklabels(), visible=False)

    def _legend(self, ax, df, show_ma):
        els = []
        if show_ma:
            if "MA20"  in df.columns: els.append(Line2D([0],[0], color=self.C["ma20"],  lw=1.5, label="MA20",  ls="--"))
            if "MA50"  in df.columns: els.append(Line2D([0],[0], color=self.C["ma50"],  lw=1.8, label="MA50"))
            if "MA200" in df.columns: els.append(Line2D([0],[0], color=self.C["ma200"], lw=2.0, label="MA200"))
        if els:
            ax.legend(handles=els, loc="upper left", fontsize=8, framealpha=0.25,
                      facecolor=self.C["panel"], edgecolor=self.C["grid"], labelcolor=self.C["text"])

    def _empty(self, title="") -> "Figure":
        fig = Figure(figsize=(13, 7.5), facecolor=self.C["bg"])
        ax  = fig.add_subplot(111); ax.set_facecolor(self.C["bg"])
        ax.text(0.5, 0.5, "No chart data\nEnter a symbol and press Analyze",
                transform=ax.transAxes, ha="center", va="center",
                color=self.C["text"], fontsize=15, alpha=0.45)
        ax.axis("off")
        if title: ax.set_title(title, color=self.C["text"], fontsize=12)
        return fig

    def save(self, fig, path: str):
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=self.C["bg"])
