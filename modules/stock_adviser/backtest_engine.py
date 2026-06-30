"""
Backtest engine — Position / Portfolio / Strategy / Backtester abstractions.

Design goals
------------
* Strict bar-by-bar simulation, no look-ahead. A strategy may only consult
  bars up to and including ``df.iloc[i]`` when generating signals for the
  *next* bar's open.
* Realistic execution: configurable commission (flat $/trade) and slippage
  (bps of fill price). Slippage is applied against the direction of trade
  — buys fill higher, sells fill lower.
* Tracks MAE (max adverse excursion) and MFE (max favourable excursion)
  for every trade. Essential pro-grade analytics.
* Portfolio-level simulation: multiple concurrent positions sized by
  fixed-fraction or fixed-dollar rules.
* Walk-forward harness: split a date range into N folds and run the
  strategy on each, returning stability stats.

The engine fetches prices from Yahoo Finance via yfinance (with a small
LRU cache) to keep things self-contained.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Callable, Optional, Sequence

try:
    import numpy as np  # type: ignore
    import pandas as pd  # type: ignore
    PANDAS_AVAILABLE = True
except Exception:  # pragma: no cover
    PANDAS_AVAILABLE = False

try:
    import yfinance as yf  # type: ignore
    YFINANCE_AVAILABLE = True
except Exception:  # pragma: no cover
    YFINANCE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data acquisition (with caching)
# ---------------------------------------------------------------------------
_PRICE_CACHE: dict[tuple[str, str, str], "pd.DataFrame"] = {}


def fetch_prices(ticker: str, start: str, end: str) -> "pd.DataFrame":
    """Fetch OHLCV for ``ticker`` between ``start`` and ``end`` (YYYY-MM-DD).
    Cached in-process so repeated requests don't re-hit Yahoo."""
    if not YFINANCE_AVAILABLE:
        raise RuntimeError("yfinance is not installed.")
    key = (ticker.upper(), start, end)
    if key in _PRICE_CACHE:
        return _PRICE_CACHE[key].copy()
    df = yf.download(
        ticker, start=start, end=end,
        auto_adjust=True, progress=False, threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"No price data for {ticker} between {start} and {end}.")
    # Flatten MultiIndex columns yfinance sometimes returns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df.index = pd.to_datetime(df.index)
    _PRICE_CACHE[key] = df
    return df.copy()


def compute_indicators(df: "pd.DataFrame") -> "pd.DataFrame":
    """Attach the standard indicator columns most strategies need."""
    close = df["Close"]
    df = df.copy()
    df["SMA10"] = close.rolling(10).mean()
    df["SMA20"] = close.rolling(20).mean()
    df["SMA50"] = close.rolling(50).mean()
    df["SMA200"] = close.rolling(200).mean()
    df["EMA12"] = close.ewm(span=12, adjust=False).mean()
    df["EMA26"] = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = df["EMA12"] - df["EMA26"]
    df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["Hist"] = df["MACD"] - df["Signal"]
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))
    df["BB_Mid"] = close.rolling(20).mean()
    std = close.rolling(20).std()
    df["BB_Up"] = df["BB_Mid"] + 2 * std
    df["BB_Dn"] = df["BB_Mid"] - 2 * std
    h_l = df["High"] - df["Low"]
    h_c = (df["High"] - close.shift()).abs()
    l_c = (df["Low"] - close.shift()).abs()
    tr = pd.concat([h_l, h_c, l_c], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["High20"] = df["High"].rolling(20).max()
    df["Low20"] = df["Low"].rolling(20).min()
    return df


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------
@dataclass
class CostModel:
    """Commissions + slippage applied at execution.

    * ``commission_per_trade``: flat $ per executed order (default 0 — most
      US retail brokers are commission-free).
    * ``slippage_bps``: basis points of fill price applied against you. 10
      bps = 0.10%. Default 5 bps is reasonable for liquid US large caps.
    """

    commission_per_trade: float = 0.0
    slippage_bps: float = 5.0

    def buy_fill(self, price: float) -> float:
        return price * (1.0 + self.slippage_bps / 10_000.0)

    def sell_fill(self, price: float) -> float:
        return price * (1.0 - self.slippage_bps / 10_000.0)


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    """A completed (or open) round-trip trade."""

    ticker: str
    entry_date: date
    entry_price: float
    size: int
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    hold_days: int = 0
    mae_pct: float = 0.0          # max adverse excursion during the hold
    mfe_pct: float = 0.0          # max favourable excursion during the hold
    entry_reason: str = ""
    exit_reason: str = ""
    commission_paid: float = 0.0

    def close(
        self,
        exit_date: date,
        exit_price: float,
        commission: float,
        reason: str = "",
    ) -> None:
        self.exit_date = exit_date
        self.exit_price = exit_price
        gross = (exit_price - self.entry_price) * self.size
        self.commission_paid += commission
        self.pnl = gross - self.commission_paid
        self.pnl_pct = (
            ((exit_price / self.entry_price) - 1.0) * 100.0
            if self.entry_price > 0 else 0.0
        )
        self.hold_days = (exit_date - self.entry_date).days
        self.exit_reason = reason


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------
@dataclass
class Portfolio:
    """Tracks cash, open positions, closed trades, and the equity curve.

    Position-sizing is handled by the Backtester; the Portfolio just
    executes buy/sell instructions against a price stream.
    """

    starting_cash: float
    costs: CostModel = field(default_factory=CostModel)
    cash: float = 0.0
    open_trades: dict[str, Trade] = field(default_factory=dict)
    closed_trades: list[Trade] = field(default_factory=list)
    equity_history: list[tuple[date, float]] = field(default_factory=list)
    position_count_history: list[tuple[date, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = self.starting_cash

    # ---------- Trade execution -------------------------------------------------
    def buy(self, ticker: str, dt: date, price: float, size: int, reason: str = "") -> bool:
        if size <= 0:
            return False
        fill = self.costs.buy_fill(price)
        cost = fill * size + self.costs.commission_per_trade
        if cost > self.cash + 1e-6:
            # Reduce size to fit available cash
            max_size = int((self.cash - self.costs.commission_per_trade) // fill)
            if max_size <= 0:
                return False
            size = max_size
            cost = fill * size + self.costs.commission_per_trade
        self.cash -= cost
        if ticker in self.open_trades:
            # Already long — average up (rarely used; strategies generally avoid this)
            t = self.open_trades[ticker]
            new_size = t.size + size
            t.entry_price = (t.entry_price * t.size + fill * size) / new_size
            t.size = new_size
            t.commission_paid += self.costs.commission_per_trade
        else:
            self.open_trades[ticker] = Trade(
                ticker=ticker, entry_date=dt, entry_price=fill, size=size,
                entry_reason=reason, commission_paid=self.costs.commission_per_trade,
            )
        return True

    def sell(self, ticker: str, dt: date, price: float, reason: str = "") -> bool:
        if ticker not in self.open_trades:
            return False
        t = self.open_trades.pop(ticker)
        fill = self.costs.sell_fill(price)
        proceeds = fill * t.size - self.costs.commission_per_trade
        self.cash += proceeds
        t.close(dt, fill, self.costs.commission_per_trade, reason=reason)
        self.closed_trades.append(t)
        return True

    # ---------- Mark-to-market --------------------------------------------------
    def update_excursions(self, prices_today: dict[str, tuple[float, float]]) -> None:
        """Update MAE/MFE for each open trade. ``prices_today`` maps
        ticker -> (low, high) for the current bar."""
        for tk, trade in self.open_trades.items():
            if tk not in prices_today:
                continue
            lo, hi = prices_today[tk]
            adv_pct = (lo / trade.entry_price - 1.0) * 100.0  # most adverse
            fav_pct = (hi / trade.entry_price - 1.0) * 100.0  # most favourable
            if adv_pct < trade.mae_pct:
                trade.mae_pct = adv_pct
            if fav_pct > trade.mfe_pct:
                trade.mfe_pct = fav_pct

    def mark_to_market(self, dt: date, prices_close: dict[str, float]) -> float:
        equity = self.cash
        for tk, trade in self.open_trades.items():
            if tk in prices_close:
                equity += trade.size * prices_close[tk]
            else:
                equity += trade.size * trade.entry_price  # fallback
        self.equity_history.append((dt, equity))
        self.position_count_history.append((dt, len(self.open_trades)))
        return equity

    # ---------- Convenience -----------------------------------------------------
    def total_equity(self, prices_close: dict[str, float]) -> float:
        equity = self.cash
        for tk, trade in self.open_trades.items():
            if tk in prices_close:
                equity += trade.size * prices_close[tk]
            else:
                equity += trade.size * trade.entry_price
        return equity

    def equity_series(self) -> "pd.Series":
        if not self.equity_history:
            return pd.Series(dtype=float)
        idx = [pd.Timestamp(d) for d, _ in self.equity_history]
        vals = [v for _, v in self.equity_history]
        return pd.Series(vals, index=idx, name="Equity")

    def position_count_series(self) -> "pd.Series":
        if not self.position_count_history:
            return pd.Series(dtype=int)
        idx = [pd.Timestamp(d) for d, _ in self.position_count_history]
        vals = [v for _, v in self.position_count_history]
        return pd.Series(vals, index=idx, name="OpenPositions")


# ---------------------------------------------------------------------------
# Strategy ABC
# ---------------------------------------------------------------------------
class Strategy:
    """Strategy contract.

    Subclasses override ``should_enter`` and ``should_exit``. They receive
    the *full history up to and including bar i*, never the future.
    """

    name: str = "AbstractStrategy"

    def required_history(self) -> int:
        """Minimum number of bars of history needed before signals fire."""
        return 200  # enough for the longest SMA in the indicator pack

    def should_enter(
        self, ticker: str, df: "pd.DataFrame", i: int, portfolio: Portfolio
    ) -> tuple[bool, str]:
        """Return (enter?, reason). Called on the *close* of bar i; entry
        executes at the *open* of bar i+1."""
        return False, ""

    def should_exit(
        self, ticker: str, df: "pd.DataFrame", i: int, trade: Trade, portfolio: Portfolio
    ) -> tuple[bool, str]:
        """Return (exit?, reason). Called on the *close* of bar i; exit
        executes at the *open* of bar i+1."""
        return False, ""

    def position_size(
        self, ticker: str, df: "pd.DataFrame", i: int, portfolio: Portfolio,
        target_dollars: float,
    ) -> int:
        """Default sizing: round down to whole shares against ``target_dollars``."""
        price = float(df["Close"].iloc[i])
        if price <= 0:
            return 0
        return int(target_dollars // price)


# ---------------------------------------------------------------------------
# Sizing rules
# ---------------------------------------------------------------------------
@dataclass
class SizingRule:
    """How much capital to allocate per new position.

    * ``mode="fixed_dollar"``: every new entry uses exactly ``amount`` USD.
    * ``mode="fixed_fraction"``: every new entry uses ``amount`` fraction
      of *current* total equity (0.10 = 10%).
    * ``mode="equal_weight"``: divide equity across ``max_positions`` slots.
    """

    mode: str = "fixed_fraction"
    amount: float = 0.10
    max_positions: int = 10

    def target_dollars(self, total_equity: float, open_positions: int) -> float:
        if self.mode == "fixed_dollar":
            return float(self.amount)
        if self.mode == "fixed_fraction":
            return total_equity * float(self.amount)
        if self.mode == "equal_weight":
            slots = max(self.max_positions, 1)
            return total_equity / slots
        return total_equity * 0.10


# ---------------------------------------------------------------------------
# Single-ticker backtester
# ---------------------------------------------------------------------------
@dataclass
class BacktestConfig:
    start: str                    # 'YYYY-MM-DD'
    end: str                      # 'YYYY-MM-DD'
    starting_cash: float = 100_000.0
    costs: CostModel = field(default_factory=CostModel)
    sizing: SizingRule = field(default_factory=lambda: SizingRule("fixed_fraction", 0.95, 1))
    max_positions: int = 10
    benchmark: str = "SPY"
    progress_cb: Optional[Callable[[str, float], None]] = None


@dataclass
class BacktestResult:
    """Container for everything the UI / metrics module needs."""

    config: BacktestConfig
    strategy_name: str
    equity_curve: "pd.Series"
    position_count: "pd.Series"
    trades: list[Trade]
    benchmark_curve: Optional["pd.Series"] = None
    tickers: list[str] = field(default_factory=list)


class Backtester:
    """Drives the bar-by-bar simulation for one or many tickers."""

    def __init__(self, strategy: Strategy, config: BacktestConfig) -> None:
        self.strategy = strategy
        self.config = config

    # ---------- Single-ticker ---------------------------------------------------
    def run_single(self, ticker: str) -> BacktestResult:
        cfg = self.config
        df = compute_indicators(fetch_prices(ticker, cfg.start, cfg.end))
        return self._simulate({ticker: df})

    # ---------- Portfolio (multi-ticker) ----------------------------------------
    def run_portfolio(self, tickers: Sequence[str]) -> BacktestResult:
        cfg = self.config
        data: dict[str, "pd.DataFrame"] = {}
        for t in tickers:
            try:
                data[t.upper()] = compute_indicators(fetch_prices(t, cfg.start, cfg.end))
                if cfg.progress_cb:
                    cfg.progress_cb(f"Loaded {t}", 0.0)
            except Exception as exc:  # pragma: no cover
                if cfg.progress_cb:
                    cfg.progress_cb(f"Skipped {t}: {exc}", 0.0)
        if not data:
            raise RuntimeError("No tickers successfully loaded.")
        return self._simulate(data)

    # ---------- Core simulator --------------------------------------------------
    def _simulate(self, data: dict[str, "pd.DataFrame"]) -> BacktestResult:
        cfg = self.config
        portfolio = Portfolio(starting_cash=cfg.starting_cash, costs=cfg.costs)

        # Build the union calendar of all tickers
        all_dates: "pd.DatetimeIndex" = pd.DatetimeIndex([])
        for df in data.values():
            all_dates = all_dates.union(df.index)
        all_dates = all_dates.sort_values()
        warmup = self.strategy.required_history()

        # Pending signals: enter/exit at the *next* bar's open
        pending_entries: list[tuple[str, str]] = []  # (ticker, reason)
        pending_exits: list[tuple[str, str]] = []

        for k, dt in enumerate(all_dates):
            # 1. Execute pending entries / exits at this bar's open
            for ticker, reason in pending_exits:
                if ticker in data and dt in data[ticker].index:
                    open_px = float(data[ticker].loc[dt, "Open"])
                    portfolio.sell(ticker, dt.date(), open_px, reason=reason)
            pending_exits.clear()

            for ticker, reason in pending_entries:
                if ticker in data and dt in data[ticker].index:
                    open_px = float(data[ticker].loc[dt, "Open"])
                    open_pos = len(portfolio.open_trades)
                    if open_pos >= cfg.max_positions:
                        continue
                    closes_today = self._closes_at(data, dt)
                    eq = portfolio.total_equity(closes_today)
                    tgt = cfg.sizing.target_dollars(eq, open_pos)
                    df_t = data[ticker]
                    i = df_t.index.get_loc(dt)
                    size = self.strategy.position_size(ticker, df_t, i, portfolio, tgt)
                    if size > 0:
                        portfolio.buy(ticker, dt.date(), open_px, size, reason=reason)
            pending_entries.clear()

            # 2. Update MAE/MFE using this bar's high/low for open positions
            today_hl: dict[str, tuple[float, float]] = {}
            for tk, trade in list(portfolio.open_trades.items()):
                if tk in data and dt in data[tk].index:
                    row = data[tk].loc[dt]
                    today_hl[tk] = (float(row["Low"]), float(row["High"]))
            portfolio.update_excursions(today_hl)

            # 3. Generate signals for tomorrow's open (only if we have warmup)
            if k >= warmup:
                # Exits first
                for tk in list(portfolio.open_trades.keys()):
                    df_t = data.get(tk)
                    if df_t is None or dt not in df_t.index:
                        continue
                    i = df_t.index.get_loc(dt)
                    exit_now, reason = self.strategy.should_exit(
                        tk, df_t, i, portfolio.open_trades[tk], portfolio,
                    )
                    if exit_now:
                        pending_exits.append((tk, reason))
                # Then entries
                for tk, df_t in data.items():
                    if tk in portfolio.open_trades:
                        continue
                    if dt not in df_t.index:
                        continue
                    if len(portfolio.open_trades) + len(pending_entries) >= cfg.max_positions:
                        break
                    i = df_t.index.get_loc(dt)
                    enter_now, reason = self.strategy.should_enter(
                        tk, df_t, i, portfolio,
                    )
                    if enter_now:
                        pending_entries.append((tk, reason))

            # 4. Mark to market on close
            closes = self._closes_at(data, dt)
            portfolio.mark_to_market(dt.date(), closes)

            if cfg.progress_cb and k % 25 == 0:
                cfg.progress_cb(
                    f"{dt.date()}  ·  eq ${portfolio.equity_history[-1][1]:,.0f}",
                    k / max(len(all_dates), 1),
                )

        # End: liquidate any remaining open trades at last available close
        for tk in list(portfolio.open_trades.keys()):
            df_t = data[tk]
            last_dt = df_t.index[-1]
            last_close = float(df_t["Close"].iloc[-1])
            portfolio.sell(tk, last_dt.date(), last_close, reason="end-of-backtest liquidation")

        # Benchmark
        bench_curve: Optional["pd.Series"] = None
        if cfg.benchmark:
            try:
                bdf = fetch_prices(cfg.benchmark, cfg.start, cfg.end)
                # Normalise to same starting cash
                first = float(bdf["Close"].iloc[0])
                bench_curve = (bdf["Close"] / first) * cfg.starting_cash
                bench_curve.index = pd.to_datetime(bench_curve.index)
                bench_curve.name = cfg.benchmark
            except Exception:  # pragma: no cover
                bench_curve = None

        return BacktestResult(
            config=cfg,
            strategy_name=self.strategy.name,
            equity_curve=portfolio.equity_series(),
            position_count=portfolio.position_count_series(),
            trades=portfolio.closed_trades,
            benchmark_curve=bench_curve,
            tickers=list(data.keys()),
        )

    @staticmethod
    def _closes_at(data: dict[str, "pd.DataFrame"], dt: "pd.Timestamp") -> dict[str, float]:
        out: dict[str, float] = {}
        for tk, df in data.items():
            if dt in df.index:
                out[tk] = float(df.loc[dt, "Close"])
        return out


# ---------------------------------------------------------------------------
# Walk-forward harness
# ---------------------------------------------------------------------------
@dataclass
class WalkForwardConfig:
    start: str
    end: str
    n_windows: int = 4
    window_overlap_pct: float = 0.0  # 0 = consecutive, 0.5 = 50% overlap
    starting_cash: float = 100_000.0
    costs: CostModel = field(default_factory=CostModel)
    sizing: SizingRule = field(default_factory=lambda: SizingRule("fixed_fraction", 0.95, 1))
    max_positions: int = 10
    benchmark: str = "SPY"


def run_walk_forward(
    strategy: Strategy,
    tickers: Sequence[str],
    wf_config: WalkForwardConfig,
    progress_cb: Optional[Callable[[str, float], None]] = None,
) -> list[BacktestResult]:
    """Split [start, end] into ``n_windows`` non-overlapping (or partly
    overlapping) sub-windows and run the strategy on each. Useful for
    stability / robustness checks per the backtest-expert methodology."""
    start = pd.to_datetime(wf_config.start)
    end = pd.to_datetime(wf_config.end)
    total_days = (end - start).days
    if total_days <= 0 or wf_config.n_windows <= 0:
        return []

    if wf_config.window_overlap_pct <= 0:
        win_days = total_days // wf_config.n_windows
        ranges = []
        for i in range(wf_config.n_windows):
            ws = start + timedelta(days=i * win_days)
            we = ws + timedelta(days=win_days)
            if i == wf_config.n_windows - 1:
                we = end
            ranges.append((ws, we))
    else:
        # Overlapping windows: still n_windows, each spans (total/n_windows) * (1 + overlap)
        base = total_days // wf_config.n_windows
        win_days = int(base * (1 + wf_config.window_overlap_pct))
        step = base
        ranges = []
        for i in range(wf_config.n_windows):
            ws = start + timedelta(days=i * step)
            we = ws + timedelta(days=win_days)
            if we > end:
                we = end
            ranges.append((ws, we))

    results: list[BacktestResult] = []
    for i, (ws, we) in enumerate(ranges):
        cfg = BacktestConfig(
            start=ws.strftime("%Y-%m-%d"),
            end=we.strftime("%Y-%m-%d"),
            starting_cash=wf_config.starting_cash,
            costs=wf_config.costs,
            sizing=wf_config.sizing,
            max_positions=wf_config.max_positions,
            benchmark=wf_config.benchmark,
        )
        if progress_cb:
            progress_cb(f"Window {i+1}/{len(ranges)}: {ws.date()} → {we.date()}", i / len(ranges))
        bt = Backtester(strategy, cfg)
        try:
            if len(tickers) == 1:
                res = bt.run_single(tickers[0])
            else:
                res = bt.run_portfolio(tickers)
            results.append(res)
        except Exception as exc:  # pragma: no cover
            if progress_cb:
                progress_cb(f"Window {i+1} failed: {exc}", i / len(ranges))
    return results
