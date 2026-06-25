"""
Technical Analysis and Composite Scoring engines.
"""
import sys
from types import ModuleType
if 'numba' not in sys.modules:
    n = ModuleType('numba')
    n.njit = lambda *a, **k: (a[0] if len(a) == 1 and callable(a[0]) else (lambda f: f))
    n.jit = n.njit
    sys.modules['numba'] = n

import numpy as np
import pandas as pd
HAS_PANDAS_TA = False


class TechnicalEngine:
    """Compute technical indicators from OHLCV DataFrame."""

    @staticmethod
    def compute(df):
        """Takes df with open/high/low/close/volume columns. Returns dict of indicators."""
        if df.empty or len(df) < 30:
            return {}
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)
        last_close = float(close.iloc[-1])

        indicators = {}

        if HAS_PANDAS_TA:
            try:
                rsi = pta.rsi(close, length=14)
                indicators["rsi_14"] = float(rsi.iloc[-1]) if rsi is not None and not rsi.empty else None
            except: indicators["rsi_14"] = None

            try:
                macd = pta.macd(close, fast=12, slow=26, signal=9)
                if macd is not None and not macd.empty:
                    indicators["macd_line"] = float(macd.iloc[-1, 0])
                    indicators["macd_signal"] = float(macd.iloc[-1, 1])
                    indicators["macd_hist"] = float(macd.iloc[-1, 2])
                else:
                    indicators["macd_line"] = indicators["macd_signal"] = indicators["macd_hist"] = None
            except: indicators["macd_line"] = indicators["macd_signal"] = indicators["macd_hist"] = None

            for p in [20, 50, 200]:
                try:
                    sma = pta.sma(close, length=p)
                    indicators[f"sma_{p}"] = float(sma.iloc[-1]) if sma is not None and not sma.empty and len(df)>=p else None
                except: indicators[f"sma_{p}"] = None

            try:
                ema20 = pta.ema(close, length=20)
                indicators["ema_20"] = float(ema20.iloc[-1]) if ema20 is not None and not ema20.empty else None
            except: indicators["ema_20"] = None

            try:
                bb = pta.bbands(close, length=20, std=2)
                if bb is not None and not bb.empty:
                    indicators["bb_upper"] = float(bb.iloc[-1, 2])   # BBU
                    indicators["bb_mid"] = float(bb.iloc[-1, 1])    # BBM
                    indicators["bb_lower"] = float(bb.iloc[-1, 0])  # BBL
                    indicators["bb_width"] = ((indicators["bb_upper"] - indicators["bb_lower"]) / indicators["bb_mid"]) * 100
                else:
                    indicators["bb_upper"] = indicators["bb_mid"] = indicators["bb_lower"] = indicators["bb_width"] = None
            except: indicators["bb_upper"] = indicators["bb_mid"] = indicators["bb_lower"] = indicators["bb_width"] = None

            try:
                adx = pta.adx(high, low, close, length=14)
                indicators["adx_14"] = float(adx.iloc[-1, 0]) if adx is not None and not adx.empty else None
            except: indicators["adx_14"] = None

            try:
                atr = pta.atr(high, low, close, length=14)
                indicators["atr_14"] = float(atr.iloc[-1]) if atr is not None and not atr.empty else None
            except: indicators["atr_14"] = None
        else:
            # ── classic pandas/numpy fallback ────────────────────
            length = len(close)
            # SMA
            for p in [20, 50, 200]:
                indicators[f"sma_{p}"] = float(close.rolling(p).mean().iloc[-1]) if length >= p else None
            # EMA 20
            if length >= 20:
                ema = close.copy()
                span = 20
                alpha = 2 / (span + 1)
                ema.iloc[:span] = close.iloc[:span].mean()
                for i in range(span, length):
                    ema.iloc[i] = close.iloc[i] * alpha + ema.iloc[i - 1] * (1 - alpha)
                indicators["ema_20"] = float(ema.iloc[-1])
            else:
                indicators["ema_20"] = None

            # RSI (14) — Wilder's smoothing
            if length >= 15:
                delta = close.diff()
                gain = delta.where(delta > 0, 0.0)
                loss = (-delta).where(delta < 0, 0.0)
                avg_gain = gain.rolling(14).mean()
                avg_loss = loss.rolling(14).mean()
                rs = avg_gain / avg_loss.replace(0, np.nan)
                rsi_series = 100 - (100 / (1 + rs))
                indicators["rsi_14"] = float(rsi_series.iloc[-1]) if pd.notna(rsi_series.iloc[-1]) else None
            else:
                indicators["rsi_14"] = None

            # MACD (12, 26, 9)
            if length >= 26:
                ema12 = close.ewm(span=12, adjust=False).mean()
                ema26 = close.ewm(span=26, adjust=False).mean()
                macd_line = ema12 - ema26
                macd_signal = macd_line.ewm(span=9, adjust=False).mean()
                macd_hist = macd_line - macd_signal
                indicators["macd_line"] = float(macd_line.iloc[-1])
                indicators["macd_signal"] = float(macd_signal.iloc[-1])
                indicators["macd_hist"] = float(macd_hist.iloc[-1])
            else:
                indicators["macd_line"] = indicators["macd_signal"] = indicators["macd_hist"] = None

            # Bollinger Bands (20, 2)
            if length >= 20:
                sma20 = close.rolling(20).mean()
                std20 = close.rolling(20).std(ddof=0)
                indicators["bb_mid"] = float(sma20.iloc[-1])
                indicators["bb_upper"] = float((sma20 + 2 * std20).iloc[-1])
                indicators["bb_lower"] = float((sma20 - 2 * std20).iloc[-1])
                bbw = ((indicators["bb_upper"] - indicators["bb_lower"]) / indicators["bb_mid"]) * 100 if indicators["bb_mid"] else None
                indicators["bb_width"] = bbw
            else:
                indicators["bb_upper"] = indicators["bb_mid"] = indicators["bb_lower"] = indicators["bb_width"] = None

            # ATR (14)
            if length >= 15:
                prev_close = close.shift(1)
                tr = pd.concat([
                    (high - low).abs(),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()
                ], axis=1).max(axis=1)
                atr_series = tr.rolling(14).mean()
                indicators["atr_14"] = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else None
            else:
                indicators["atr_14"] = None

            # ADX (14)
            if length >= 28:
                tr = pd.concat([
                    (high - low).abs(),
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()
                ], axis=1).max(axis=1)
                atr14 = tr.rolling(14).mean()

                up = high - high.shift(1)
                down = low.shift(1) - low
                plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=close.index)
                minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=close.index)

                pdi = (plus_dm.rolling(14).mean() / atr14.replace(0, np.nan)) * 100
                ndi = (minus_dm.rolling(14).mean() / atr14.replace(0, np.nan)) * 100
                dx = ((pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)) * 100
                adx_series = dx.rolling(14).mean()
                indicators["adx_14"] = float(adx_series.iloc[-1]) if pd.notna(adx_series.iloc[-1]) else None
            else:
                indicators["adx_14"] = None

        # 52-week High/Low
        try:
            indicators["high_52w"] = float(high.tail(252).max())
            indicators["low_52w"] = float(low.tail(252).min())
            indicators["dist_high52_pct"] = ((last_close - indicators["high_52w"]) / indicators["high_52w"]) * 100
        except:
            indicators["high_52w"] = indicators["low_52w"] = indicators["dist_high52_pct"] = None

        # 20-day High (Donchian)
        try:
            indicators["high_20d"] = float(high.tail(20).max())
        except: indicators["high_20d"] = None

        # Volume analysis
        indicators["volume_avg20"] = float(volume.tail(20).mean()) if len(volume) >= 20 else None
        indicators["volume_latest"] = float(volume.iloc[-1])
        if indicators["volume_avg20"] and indicators["volume_avg20"] > 0:
            indicators["relative_volume"] = (indicators["volume_latest"] / indicators["volume_avg20"]) * 100
        else:
            indicators["relative_volume"] = None

        # Price position
        indicators["last_close"] = last_close
        for p in [20, 50, 200]:
            sma_val = indicators.get(f"sma_{p}")
            if sma_val and sma_val > 0:
                indicators[f"dist_sma{p}_pct"] = ((last_close - sma_val) / sma_val) * 100
            else:
                indicators[f"dist_sma{p}_pct"] = None

        return indicators

    @staticmethod
    def passes_technical_filters(indicators, filters):
        """Check if indicators pass the technical portion of screening filters."""
        # RSI
        rsi = indicators.get("rsi_14")
        if rsi is not None:
            if filters.get("rsi_min") is not None and rsi < filters["rsi_min"]: return False
            if filters.get("rsi_max") is not None and rsi > filters["rsi_max"]: return False

        # MACD
        macd_sig = filters.get("macd_signal")
        hist = indicators.get("macd_hist") or 0
        line = indicators.get("macd_line") or 0
        if macd_sig == "Positive" and hist <= 0: return False
        if macd_sig == "Negative" and hist >= 0: return False
        if filters.get("macd_line_min") is not None and line < filters["macd_line_min"]: return False

        # Price vs SMAs
        last = indicators.get("last_close") or 0
        for p in [20, 50, 200]:
            key = f"price_vs_sma{p}"
            sma = indicators.get(f"sma_{p}")
            if sma and filters.get(key):
                mode = filters[key]
                if mode == "Above" and last <= sma: return False
                if mode == "Below" and last >= sma: return False
                if mode == "Pullback":
                    if last <= sma or last > sma * 1.02: return False

        # SMA Crossovers
        if filters.get("sma50_vs_sma200") == "Above":
            s50, s200 = indicators.get("sma_50"), indicators.get("sma_200")
            if not (s50 and s200 and s50 > s200): return False
        
        if filters.get("sma20_vs_sma50") == "Above":
            s20, s50 = indicators.get("sma_20"), indicators.get("sma_50")
            if not (s20 and s50 and s20 > s50): return False

        # Breakouts
        if filters.get("price_vs_high20") == "Above":
            h20 = indicators.get("high_20d")
            if not (h20 and last >= h20): return False
        
        if filters.get("dist_high52_max") is not None:
            dist = indicators.get("dist_high52_pct")
            if dist is None or dist < -filters["dist_high52_max"]: return False

        # Volume
        rel_vol = indicators.get("relative_volume")
        if filters.get("volume_vs_avg_min") is not None:
            if rel_vol is None or rel_vol < filters["volume_vs_avg_min"]: return False

        # Volatility
        bbw = indicators.get("bb_width")
        if filters.get("bb_width_max") is not None:
            if bbw is None or bbw > filters["bb_width_max"]: return False

        return True


class ScoringEngine:
    """Compute composite score (0-100) from fundamentals + technicals."""

    WEIGHTS = {
        "valuation": 0.25,
        "growth": 0.20,
        "quality": 0.20,
        "momentum": 0.20,
        "risk": 0.15,
    }

    @staticmethod
    def _normalize(val, low, high, invert=False):
        if val is None: return 50
        val = max(low, min(high, val))
        score = (val - low) / (high - low) * 100 if high != low else 50
        return 100 - score if invert else score

    @classmethod
    def compute(cls, fund, tech=None):
        """fund: dict of fundamental data, tech: dict of technical indicators."""
        scores = {}

        # Valuation (lower is better for PE, PB)
        pe = fund.get("pe_ratio")
        pb = fund.get("pb_ratio")
        ps = fund.get("ps_ratio")
        val_scores = []
        if pe and 0 < pe < 200: val_scores.append(cls._normalize(pe, 5, 60, invert=True))
        if pb and 0 < pb < 50: val_scores.append(cls._normalize(pb, 0.5, 15, invert=True))
        if ps and 0 < ps < 50: val_scores.append(cls._normalize(ps, 0.3, 20, invert=True))
        scores["valuation"] = sum(val_scores) / len(val_scores) if val_scores else 50

        # Growth
        rg = fund.get("revenue_growth")
        eg = fund.get("eps_growth")
        growth_scores = []
        if rg is not None: growth_scores.append(cls._normalize(rg, -10, 50))
        if eg is not None: growth_scores.append(cls._normalize(eg, -20, 60))
        scores["growth"] = sum(growth_scores) / len(growth_scores) if growth_scores else 50

        # Quality
        roe = fund.get("roe")
        de = fund.get("debt_equity")
        qual_scores = []
        if roe is not None: qual_scores.append(cls._normalize(roe, 0, 40))
        if de is not None and de >= 0: qual_scores.append(cls._normalize(de, 0, 3, invert=True))
        scores["quality"] = sum(qual_scores) / len(qual_scores) if qual_scores else 50

        # Momentum (from technicals)
        mom_scores = []
        if tech:
            rsi = tech.get("rsi_14")
            if rsi is not None:
                if 40 <= rsi <= 70: mom_scores.append(80)
                elif rsi > 70: mom_scores.append(40)
                else: mom_scores.append(30)
            dist200 = tech.get("dist_sma200_pct")
            if dist200 is not None:
                mom_scores.append(cls._normalize(dist200, -20, 30))
            macd = tech.get("macd_hist")
            if macd is not None:
                mom_scores.append(80 if macd > 0 else 30)
        scores["momentum"] = sum(mom_scores) / len(mom_scores) if mom_scores else 50

        # Risk (beta, volatility)
        risk_scores = []
        beta = fund.get("beta")
        if beta is not None and beta > 0:
            risk_scores.append(cls._normalize(beta, 0.3, 2.5, invert=True))
        scores["risk"] = sum(risk_scores) / len(risk_scores) if risk_scores else 50

        # Weighted composite
        composite = sum(scores[k] * cls.WEIGHTS[k] for k in cls.WEIGHTS)
        return {
            "composite": round(composite, 1),
            "valuation": round(scores["valuation"], 1),
            "growth": round(scores["growth"], 1),
            "quality": round(scores["quality"], 1),
            "momentum": round(scores["momentum"], 1),
            "risk": round(scores["risk"], 1),
        }

    @staticmethod
    def verdict(score):
        if score >= 75: return "STRONG BUY", "#3FB950"
        if score >= 60: return "BUY", "#56D364"
        if score >= 45: return "HOLD", "#F0883E"
        if score >= 30: return "SELL", "#F85149"
        return "STRONG SELL", "#DA3633"
