"""
Strategy library — pre-canned strategies for the backtest engine.

Every strategy follows the contract in ``backtest_engine.Strategy``:
generate signals on the close of bar ``i``, execute on the open of bar
``i+1``. No look-ahead.

Strategies included
-------------------
* RSIMeanReversionStrategy   — buy oversold, sell overbought / time stop
* MACrossoverStrategy        — 20/50 (golden / death) crossover
* MACDStrategy               — MACD signal-line crossover
* BollingerReversionStrategy — buy lower band, sell middle band
* BreakoutStrategy           — 20-day high breakout with ATR trailing stop
* ScreenerStrategy           — buys when the StockAnalysis composite is
                                BUY/STRONG BUY, exits when it leaves the
                                allowed-ratings set or hits a stop / time exit.

All strategies expose a ``params`` dict so the UI can let the user tweak
thresholds at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import numpy as np  # type: ignore
    import pandas as pd  # type: ignore
    PANDAS_AVAILABLE = True
except Exception:  # pragma: no cover
    PANDAS_AVAILABLE = False

from backtest_engine import Strategy, Trade, Portfolio


# ===========================================================================
# RSI Mean-Reversion
# ===========================================================================
class RSIMeanReversionStrategy(Strategy):
    """Classic mean-reversion: enter when RSI is washed out, exit when it
    snaps back. Optional ATR stop and time stop."""

    name = "RSI Mean Reversion"

    def __init__(
        self,
        rsi_buy: float = 30.0,
        rsi_exit: float = 55.0,
        atr_stop_mult: float = 2.5,
        max_hold_days: int = 20,
    ) -> None:
        self.params = {
            "rsi_buy": rsi_buy,
            "rsi_exit": rsi_exit,
            "atr_stop_mult": atr_stop_mult,
            "max_hold_days": max_hold_days,
        }

    def should_enter(self, ticker, df, i, portfolio):
        rsi = df["RSI"].iloc[i]
        if pd.isna(rsi):
            return False, ""
        # Trend filter: only buy above the 200d MA
        sma200 = df["SMA200"].iloc[i]
        close = df["Close"].iloc[i]
        if pd.notna(sma200) and close < sma200:
            return False, ""
        if rsi < self.params["rsi_buy"]:
            return True, f"RSI {rsi:.1f} < {self.params['rsi_buy']}"
        return False, ""

    def should_exit(self, ticker, df, i, trade, portfolio):
        rsi = df["RSI"].iloc[i]
        close = df["Close"].iloc[i]
        atr = df["ATR"].iloc[i]
        # 1) Profit / momentum target
        if pd.notna(rsi) and rsi > self.params["rsi_exit"]:
            return True, f"RSI {rsi:.1f} > {self.params['rsi_exit']}"
        # 2) ATR stop
        if pd.notna(atr):
            stop = trade.entry_price - self.params["atr_stop_mult"] * atr
            if close < stop:
                return True, f"ATR stop ({self.params['atr_stop_mult']:.1f}x)"
        # 3) Time stop
        idx_dt = df.index[i].date()
        if (idx_dt - trade.entry_date).days >= self.params["max_hold_days"]:
            return True, f"Time stop ({self.params['max_hold_days']}d)"
        return False, ""


# ===========================================================================
# Moving-Average Crossover (20/50)
# ===========================================================================
class MACrossoverStrategy(Strategy):
    """Golden/death cross. Default is 20/50; configurable. Useful as a
    benchmark — it's a textbook trend-follower."""

    name = "MA Crossover (20/50)"

    def __init__(self, fast: int = 20, slow: int = 50) -> None:
        self.fast = fast
        self.slow = slow
        self.params = {"fast": fast, "slow": slow}

    def required_history(self) -> int:
        return max(self.slow + 5, 60)

    def _fs(self, df, i):
        f = df["Close"].rolling(self.fast).mean().iloc[i]
        s = df["Close"].rolling(self.slow).mean().iloc[i]
        f_prev = df["Close"].rolling(self.fast).mean().iloc[i - 1] if i > 0 else f
        s_prev = df["Close"].rolling(self.slow).mean().iloc[i - 1] if i > 0 else s
        return f, s, f_prev, s_prev

    def should_enter(self, ticker, df, i, portfolio):
        f, s, fp, sp = self._fs(df, i)
        if any(pd.isna(x) for x in (f, s, fp, sp)):
            return False, ""
        if fp <= sp and f > s:
            return True, f"{self.fast}/{self.slow} golden cross"
        return False, ""

    def should_exit(self, ticker, df, i, trade, portfolio):
        f, s, fp, sp = self._fs(df, i)
        if any(pd.isna(x) for x in (f, s, fp, sp)):
            return False, ""
        if fp >= sp and f < s:
            return True, f"{self.fast}/{self.slow} death cross"
        return False, ""


# ===========================================================================
# MACD signal cross
# ===========================================================================
class MACDStrategy(Strategy):
    """Buy when MACD crosses above signal line; sell when it crosses back."""

    name = "MACD Signal Cross"

    def __init__(self, require_zero_above: bool = False) -> None:
        # require_zero_above: only buy when MACD itself is also > 0 (stricter)
        self.params = {"require_zero_above": require_zero_above}

    def should_enter(self, ticker, df, i, portfolio):
        if i < 1:
            return False, ""
        macd = df["MACD"].iloc[i]
        sigl = df["Signal"].iloc[i]
        mp = df["MACD"].iloc[i - 1]
        sp = df["Signal"].iloc[i - 1]
        if any(pd.isna(x) for x in (macd, sigl, mp, sp)):
            return False, ""
        if mp <= sp and macd > sigl:
            if self.params["require_zero_above"] and macd <= 0:
                return False, ""
            return True, "MACD crossed above signal"
        return False, ""

    def should_exit(self, ticker, df, i, trade, portfolio):
        if i < 1:
            return False, ""
        macd = df["MACD"].iloc[i]
        sigl = df["Signal"].iloc[i]
        mp = df["MACD"].iloc[i - 1]
        sp = df["Signal"].iloc[i - 1]
        if any(pd.isna(x) for x in (macd, sigl, mp, sp)):
            return False, ""
        if mp >= sp and macd < sigl:
            return True, "MACD crossed below signal"
        return False, ""


# ===========================================================================
# Bollinger Reversion
# ===========================================================================
class BollingerReversionStrategy(Strategy):
    """Buy when price closes below the lower Bollinger band; exit when it
    closes back above the middle band (the 20-SMA)."""

    name = "Bollinger Mean Reversion"

    def __init__(self, max_hold_days: int = 15, atr_stop_mult: float = 2.0) -> None:
        self.params = {"max_hold_days": max_hold_days, "atr_stop_mult": atr_stop_mult}

    def should_enter(self, ticker, df, i, portfolio):
        c = df["Close"].iloc[i]
        bdn = df["BB_Dn"].iloc[i]
        if pd.isna(c) or pd.isna(bdn):
            return False, ""
        # Trend filter: stay above 200d
        sma200 = df["SMA200"].iloc[i]
        if pd.notna(sma200) and c < sma200:
            return False, ""
        if c < bdn:
            return True, "Close below lower Bollinger"
        return False, ""

    def should_exit(self, ticker, df, i, trade, portfolio):
        c = df["Close"].iloc[i]
        mid = df["BB_Mid"].iloc[i]
        atr = df["ATR"].iloc[i]
        if pd.notna(c) and pd.notna(mid) and c > mid:
            return True, "Close back above middle Bollinger"
        if pd.notna(atr):
            stop = trade.entry_price - self.params["atr_stop_mult"] * atr
            if c < stop:
                return True, "ATR stop"
        idx_dt = df.index[i].date()
        if (idx_dt - trade.entry_date).days >= self.params["max_hold_days"]:
            return True, "Time stop"
        return False, ""


# ===========================================================================
# Breakout (20-day high) with ATR trailing stop
# ===========================================================================
class BreakoutStrategy(Strategy):
    """Donchian-style breakout: enter on a new 20-day high, exit when
    price falls more than ``trail_atr_mult`` ATRs from the trade high."""

    name = "20-Day Breakout (ATR trail)"

    def __init__(
        self,
        breakout_lookback: int = 20,
        trail_atr_mult: float = 3.0,
        time_stop_days: int = 90,
    ) -> None:
        self.params = {
            "breakout_lookback": breakout_lookback,
            "trail_atr_mult": trail_atr_mult,
            "time_stop_days": time_stop_days,
        }

    def required_history(self) -> int:
        return max(self.params["breakout_lookback"] + 5, 200)

    def should_enter(self, ticker, df, i, portfolio):
        if i < self.params["breakout_lookback"]:
            return False, ""
        c = df["Close"].iloc[i]
        lookback_high = df["High"].iloc[i - self.params["breakout_lookback"]:i].max()
        if pd.isna(c) or pd.isna(lookback_high):
            return False, ""
        # Strict breakout
        if c > lookback_high * 1.001:
            sma200 = df["SMA200"].iloc[i]
            if pd.notna(sma200) and c < sma200:
                return False, ""
            return True, f"{self.params['breakout_lookback']}d breakout"
        return False, ""

    def should_exit(self, ticker, df, i, trade, portfolio):
        c = df["Close"].iloc[i]
        atr = df["ATR"].iloc[i]
        if pd.isna(c):
            return False, ""
        # Trail off the trade's highest favourable price so far
        trade_high = trade.entry_price * (1 + trade.mfe_pct / 100.0)
        if pd.notna(atr) and atr > 0:
            stop = trade_high - self.params["trail_atr_mult"] * atr
            if c < stop:
                return True, f"ATR trailing stop ({self.params['trail_atr_mult']:.1f}x)"
        idx_dt = df.index[i].date()
        if (idx_dt - trade.entry_date).days >= self.params["time_stop_days"]:
            return True, "Time stop"
        return False, ""


# ===========================================================================
# Screener strategy
# ===========================================================================
class ScreenerStrategy(Strategy):
    """Plays the screener's own ratings.

    On each bar, the strategy computes the multi-horizon score live using
    the same logic as the live screener (RSI/MACD/MA/etc). It enters when
    the chosen horizon's rating sits in the *allowed* set, and exits when
    the rating falls *out* of that set or hits a configured stop.

    Because the scoring engine in ``us_stock_analyzer`` mixes price-based
    technicals with point-in-time fundamentals (which yfinance only
    provides as current snapshots), the **backtest variant deliberately
    uses only the price-derived parts of each horizon** — so results are
    reproducible historically. The valuation and analyst-target inputs
    are intentionally omitted.
    """

    name = "Screener Rating"

    ALLOWED_DEFAULTS = ("STRONG BUY", "BUY")

    def __init__(
        self,
        horizon: str = "swing",
        allowed_ratings: tuple[str, ...] = ALLOWED_DEFAULTS,
        atr_stop_mult: float = 3.0,
        max_hold_days: int = 60,
    ) -> None:
        assert horizon in ("swing", "short", "medium", "long", "composite")
        self.params = {
            "horizon": horizon,
            "allowed_ratings": list(allowed_ratings),
            "atr_stop_mult": atr_stop_mult,
            "max_hold_days": max_hold_days,
        }

    # ---- scoring helpers (price-only versions of the live screener) ----
    @staticmethod
    def _score_to_rating(score: float) -> str:
        if score >= 75:
            return "STRONG BUY"
        if score >= 60:
            return "BUY"
        if score >= 45:
            return "HOLD"
        if score >= 30:
            return "SELL"
        return "STRONG SELL"

    @staticmethod
    def _swing_score(df, i) -> float:
        s = 50.0
        if i < 30:
            return s
        c = df["Close"].iloc[i]
        # 5-day momentum
        if i >= 5:
            m5 = (c / df["Close"].iloc[i - 5] - 1) * 100
            if m5 > 8:   s += 14
            elif m5 > 3: s += 7
            elif m5 < -8: s -= 14
            elif m5 < -3: s -= 7
        # 10-day momentum
        if i >= 10:
            m10 = (c / df["Close"].iloc[i - 10] - 1) * 100
            if m10 > 10:  s += 6
            elif m10 < -10: s -= 6
        # 10D MA proximity
        sma10 = df["Close"].iloc[max(0, i - 9):i + 1].mean()
        if sma10 > 0:
            d = (c / sma10 - 1) * 100
            if d > 5: s -= 4
            elif d > 1: s += 6
            elif d < -5: s -= 8
        rsi = df["RSI"].iloc[i]
        if pd.notna(rsi):
            if rsi < 30: s += 14
            elif rsi < 40: s += 5
            elif rsi > 75: s -= 12
            elif rsi > 65: s -= 4
        if i >= 1:
            h0 = df["Hist"].iloc[i - 1]
            h1 = df["Hist"].iloc[i]
            if pd.notna(h0) and pd.notna(h1):
                if h1 > 0 and h1 > h0: s += 8
                elif h1 < 0 and h1 < h0: s -= 8
        bbu = df["BB_Up"].iloc[i]
        bbd = df["BB_Dn"].iloc[i]
        if pd.notna(bbu) and pd.notna(bbd) and bbu > bbd:
            pos = (c - bbd) / (bbu - bbd)
            if pos > 1.0: s += 5
            elif pos > 0.9: s -= 3
            elif pos < 0.0: s += 8
            elif pos < 0.1: s += 6
        # 20-day high / low proximity
        if i >= 20:
            hi20 = df["High"].iloc[i - 19:i + 1].max()
            lo20 = df["Low"].iloc[i - 19:i + 1].min()
            if c >= hi20 * 0.995: s += 6
            elif c <= lo20 * 1.005: s -= 6
        return max(0.0, min(100.0, s))

    @staticmethod
    def _short_score(df, i) -> float:
        s = 50.0
        if i < 50:
            return s
        c = df["Close"].iloc[i]
        rsi = df["RSI"].iloc[i]
        if pd.notna(rsi):
            if rsi > 75: s -= 12
            elif rsi > 70: s -= 8
            elif rsi < 25: s += 18
            elif rsi < 30: s += 12
            elif rsi > 55: s += 4
            elif rsi < 45: s -= 4
        s20 = df["SMA20"].iloc[i]
        s50 = df["SMA50"].iloc[i]
        if pd.notna(s20) and pd.notna(s50):
            if c > s20 > s50: s += 14
            elif c < s20 < s50: s -= 14
            elif c > s20: s += 5
            elif c < s50: s -= 5
        macd = df["MACD"].iloc[i]
        sigl = df["Signal"].iloc[i]
        if pd.notna(macd) and pd.notna(sigl):
            if macd > sigl and macd > 0: s += 10
            elif macd < sigl and macd < 0: s -= 10
            elif macd > sigl: s += 3
            else: s -= 3
        if i >= 21:
            mom = (c / df["Close"].iloc[i - 21] - 1) * 100
            if mom > 12: s += 10
            elif mom > 5: s += 4
            elif mom < -12: s -= 10
            elif mom < -5: s -= 4
        return max(0.0, min(100.0, s))

    @staticmethod
    def _medium_score(df, i) -> float:
        s = 50.0
        if i < 200:
            return s
        c = df["Close"].iloc[i]
        sma200 = df["SMA200"].iloc[i]
        if pd.notna(sma200) and sma200 > 0:
            r = c / sma200
            if r > 1.10: s += 14
            elif r > 1.0: s += 8
            elif r < 0.90: s -= 14
            else: s -= 6
        if i >= 126:
            ret6 = (c / df["Close"].iloc[i - 126] - 1) * 100
            if ret6 > 25: s += 8
            elif ret6 > 10: s += 3
            elif ret6 < -20: s -= 8
        return max(0.0, min(100.0, s))

    @staticmethod
    def _long_score(df, i) -> float:
        # Long-term in the live screener relies on fundamentals that are
        # not bar-time-accurate; for the backtest, fall back to a slow
        # trend score so the strategy still works for the 'long' horizon.
        s = 50.0
        if i < 200:
            return s
        c = df["Close"].iloc[i]
        sma200 = df["SMA200"].iloc[i]
        if pd.notna(sma200):
            r = c / sma200
            if r > 1.15: s += 12
            elif r > 1.0: s += 6
            elif r < 0.85: s -= 12
            elif r < 1.0: s -= 4
        if i >= 252:
            ret1y = (c / df["Close"].iloc[i - 252] - 1) * 100
            if ret1y > 30: s += 8
            elif ret1y > 10: s += 3
            elif ret1y < -10: s -= 8
        return max(0.0, min(100.0, s))

    def _rating(self, df, i) -> str:
        h = self.params["horizon"]
        if h == "swing":     return self._score_to_rating(self._swing_score(df, i))
        if h == "short":     return self._score_to_rating(self._short_score(df, i))
        if h == "medium":    return self._score_to_rating(self._medium_score(df, i))
        if h == "long":      return self._score_to_rating(self._long_score(df, i))
        # composite = avg of short/medium/long (matches the live app's blend)
        sw = self._short_score(df, i)
        md = self._medium_score(df, i)
        lg = self._long_score(df, i)
        return self._score_to_rating((sw + md + lg) / 3.0)

    def should_enter(self, ticker, df, i, portfolio):
        rating = self._rating(df, i)
        if rating in self.params["allowed_ratings"]:
            return True, f"{self.params['horizon']} → {rating}"
        return False, ""

    def should_exit(self, ticker, df, i, trade, portfolio):
        rating = self._rating(df, i)
        if rating not in self.params["allowed_ratings"]:
            return True, f"rating fell to {rating}"
        atr = df["ATR"].iloc[i]
        c = df["Close"].iloc[i]
        if pd.notna(atr):
            stop = trade.entry_price - self.params["atr_stop_mult"] * atr
            if c < stop:
                return True, "ATR stop"
        idx_dt = df.index[i].date()
        if (idx_dt - trade.entry_date).days >= self.params["max_hold_days"]:
            return True, "Time stop"
        return False, ""



# ===========================================================================
# Custom-rule strategy — driven by the strategy_dsl expressions
# ===========================================================================
class CustomRuleStrategy(Strategy):
    """Backtest adapter for a ``strategy_dsl.CustomStrategy`` (a saved
    BUY + SELL expression pair). Builds the evaluation context per bar
    and routes ``should_enter`` / ``should_exit`` through the DSL.

    Caveat: fundamentals (ROE, PE, etc.) come from the yfinance ``info``
    dict which is a *current* snapshot, not point-in-time. For historical
    backtest validity, prefer expressions that reference only the price-
    derived variables.
    """

    def __init__(
        self,
        buy: str = "",
        sell: str = "",
        name: str = "Custom Rule",
        atr_stop_mult: float = 0.0,
        max_hold_days: int = 0,
    ) -> None:
        # Lazy-import to avoid hard dependency for sites that only want
        # the built-in strategies.
        from strategy_dsl import CustomStrategy as DslStrategy, build_context
        self._build_ctx = build_context
        self._dsl = DslStrategy(name=name, buy_expr=buy, sell_expr=sell)
        self.name = name
        # ``atr_stop_mult`` and ``max_hold_days`` are *optional* belt-and-
        # braces exits applied on top of the DSL sell rule. Both default
        # to 0 (= disabled).
        self.params = {
            "buy": buy,
            "sell": sell,
            "atr_stop_mult": atr_stop_mult,
            "max_hold_days": max_hold_days,
        }
        # Cache for the ticker's fundamentals dict (yfinance .info)
        self._info_cache: dict = {}

    def required_history(self) -> int:
        # 252 covers HIGH52W / LOW52W references; 200 SMA also needs it.
        return 252

    def _ctx(self, ticker: str, df, i: int) -> tuple[dict, dict | None]:
        info = self._info_cache.get(ticker)
        if info is None:
            try:
                import yfinance as yf  # type: ignore
                info = dict(yf.Ticker(ticker).info)
            except Exception:
                info = {}
            self._info_cache[ticker] = info
        ctx_now = self._build_ctx(df, i, info)
        ctx_prev = self._build_ctx(df, i - 1, info) if i > 0 else None
        return ctx_now, ctx_prev

    def should_enter(self, ticker, df, i, portfolio):
        ctx_now, ctx_prev = self._ctx(ticker, df, i)
        if self._dsl.evaluate_buy(ctx_now, ctx_prev):
            return True, "custom BUY rule"
        return False, ""

    def should_exit(self, ticker, df, i, trade, portfolio):
        # Optional belt-and-braces exits first
        c = df["Close"].iloc[i] if "Close" in df.columns else None
        if self.params["atr_stop_mult"] > 0 and "ATR" in df.columns and c is not None:
            atr = df["ATR"].iloc[i]
            if atr == atr and atr > 0:
                stop = trade.entry_price - self.params["atr_stop_mult"] * atr
                if c < stop:
                    return True, f"ATR stop ({self.params['atr_stop_mult']:.1f}x)"
        if self.params["max_hold_days"] > 0:
            idx_dt = df.index[i].date()
            if (idx_dt - trade.entry_date).days >= self.params["max_hold_days"]:
                return True, f"Time stop ({self.params['max_hold_days']}d)"
        # Then the DSL sell rule
        ctx_now, ctx_prev = self._ctx(ticker, df, i)
        if self._dsl.evaluate_sell(ctx_now, ctx_prev):
            return True, "custom SELL rule"
        return False, ""


def load_custom_strategies_from_vault(base_dir) -> dict[str, "CustomRuleStrategy"]:
    """Read every parseable strategy in the vault and return them as
    pre-built CustomRuleStrategy *instances* (not factories) keyed by
    'Custom: <name>' for the backtester dropdown. Returns {} on failure.
    """
    try:
        from strategy_vault import StrategyVault
    except Exception:
        return {}
    try:
        vault = StrategyVault(base_dir=base_dir)
    except Exception:
        return {}
    out: dict[str, CustomRuleStrategy] = {}
    for s in vault.all():
        try:
            inst = CustomRuleStrategy(
                buy=s.get("buy", ""),
                sell=s.get("sell", ""),
                name=s.get("name", "Custom"),
            )
        except Exception:
            continue
        marker = {"popular": "★", "imported": "↻"}.get(s.get("source", "custom"), "✎")
        out[f"{marker}  {s['name']}"] = inst
    return out


# ===========================================================================
# Registry — used by the UI to populate dropdowns
# ===========================================================================
def available_strategies() -> dict[str, type[Strategy]]:
    return {
        "RSI Mean Reversion": RSIMeanReversionStrategy,
        "MA Crossover (20/50)": MACrossoverStrategy,
        "MACD Signal Cross": MACDStrategy,
        "Bollinger Mean Reversion": BollingerReversionStrategy,
        "20-Day Breakout (ATR trail)": BreakoutStrategy,
        "Screener Rating": ScreenerStrategy,
    }
