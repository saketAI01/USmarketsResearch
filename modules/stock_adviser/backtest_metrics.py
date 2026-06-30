"""
Performance metrics for backtested strategies.

Computes the full pro-grade analytics suite:
 - Total return, CAGR
 - Volatility, Sharpe, Sortino
 - Max drawdown, drawdown duration, underwater duration
 - Win rate, avg win, avg loss, profit factor, expectancy
 - Exposure (% time in market)
 - Trade-level MAE/MFE distributions
 - Rolling Sharpe
 - Monthly returns matrix
 - Alpha / Beta / Information ratio / Correlation vs a benchmark

All inputs are kept dependency-light: pandas + numpy only. No vendor
libraries — the metrics are computed from first principles so they
match what an institutional backtest report would show.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

try:
    import numpy as np  # type: ignore
    import pandas as pd  # type: ignore
    PANDAS_AVAILABLE = True
except Exception:  # pragma: no cover
    PANDAS_AVAILABLE = False


TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Per-trade analytics
# ---------------------------------------------------------------------------
@dataclass
class TradeStats:
    """Aggregate statistics from a list of closed trades."""

    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_win_dollars: float = 0.0
    avg_loss_dollars: float = 0.0
    largest_win_pct: float = 0.0
    largest_loss_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy_pct: float = 0.0
    avg_hold_days: float = 0.0
    median_hold_days: float = 0.0
    avg_mae_pct: float = 0.0
    avg_mfe_pct: float = 0.0


def trade_stats(trades: list[Any]) -> TradeStats:
    """Compute aggregate statistics from a list of Trade objects.

    Each Trade is expected to have: pnl, pnl_pct, hold_days, mae_pct,
    mfe_pct (all floats). Trades with pnl is None are skipped (still open).
    """
    closed = [t for t in trades if getattr(t, "pnl", None) is not None]
    if not closed:
        return TradeStats()

    pnls = [float(t.pnl) for t in closed]
    pnl_pcts = [float(t.pnl_pct) for t in closed]
    holds = [float(getattr(t, "hold_days", 0)) for t in closed]
    maes = [float(getattr(t, "mae_pct", 0)) for t in closed]
    mfes = [float(getattr(t, "mfe_pct", 0)) for t in closed]

    wins_pct = [p for p in pnl_pcts if p > 0]
    losses_pct = [p for p in pnl_pcts if p <= 0]
    wins_d = [d for d, p in zip(pnls, pnl_pcts) if p > 0]
    losses_d = [d for d, p in zip(pnls, pnl_pcts) if p <= 0]

    total = len(closed)
    win_n = len(wins_pct)
    loss_n = len(losses_pct)
    avg_win_pct = sum(wins_pct) / win_n if win_n else 0.0
    avg_loss_pct = sum(losses_pct) / loss_n if loss_n else 0.0
    avg_win_d = sum(wins_d) / win_n if win_n else 0.0
    avg_loss_d = sum(losses_d) / loss_n if loss_n else 0.0

    total_wins_d = sum(wins_d)
    total_losses_d = abs(sum(losses_d))
    profit_factor = (total_wins_d / total_losses_d) if total_losses_d > 0 else (
        float("inf") if total_wins_d > 0 else 0.0
    )

    win_rate = win_n / total if total else 0.0
    # Expectancy = P(win) * avg_win - P(loss) * |avg_loss|
    expectancy = win_rate * avg_win_pct + (1 - win_rate) * avg_loss_pct

    return TradeStats(
        total_trades=total,
        winners=win_n,
        losers=loss_n,
        win_rate=win_rate,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        avg_win_dollars=avg_win_d,
        avg_loss_dollars=avg_loss_d,
        largest_win_pct=max(pnl_pcts) if pnl_pcts else 0.0,
        largest_loss_pct=min(pnl_pcts) if pnl_pcts else 0.0,
        profit_factor=profit_factor,
        expectancy_pct=expectancy,
        avg_hold_days=sum(holds) / total if total else 0.0,
        median_hold_days=float(np.median(holds)) if total else 0.0,
        avg_mae_pct=sum(maes) / total if total else 0.0,
        avg_mfe_pct=sum(mfes) / total if total else 0.0,
    )


# ---------------------------------------------------------------------------
# Equity-curve analytics
# ---------------------------------------------------------------------------
@dataclass
class EquityStats:
    """Performance stats computed from the daily equity curve."""

    start_equity: float = 0.0
    end_equity: float = 0.0
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    annual_vol_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    max_dd_duration_days: int = 0
    underwater_pct: float = 0.0
    best_day_pct: float = 0.0
    worst_day_pct: float = 0.0
    positive_days_pct: float = 0.0
    skewness: float = 0.0
    kurtosis_excess: float = 0.0


def equity_stats(equity_curve: "pd.Series", rf_annual: float = 0.0) -> EquityStats:
    """Compute equity-curve metrics. ``equity_curve`` is a pandas Series
    indexed by date with the portfolio's total value at each step.
    ``rf_annual`` is the annualised risk-free rate (default 0).
    """
    if equity_curve is None or len(equity_curve) < 2:
        return EquityStats()

    ec = equity_curve.dropna()
    if len(ec) < 2:
        return EquityStats()

    rets = ec.pct_change().dropna()
    start = float(ec.iloc[0])
    end = float(ec.iloc[-1])
    total_ret = (end / start - 1.0) if start > 0 else 0.0

    # Years between first and last bar
    delta_days = (ec.index[-1] - ec.index[0]).days
    years = max(delta_days / 365.25, 1 / 365.25)
    cagr = (end / start) ** (1.0 / years) - 1.0 if start > 0 else 0.0

    ann_vol = float(rets.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    rf_daily = rf_annual / TRADING_DAYS_PER_YEAR
    excess = rets - rf_daily
    sharpe = (
        float(excess.mean() / rets.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
        if rets.std() > 0 else 0.0
    )

    downside = rets[rets < 0]
    dd_dev = float(downside.std()) if len(downside) > 0 else 0.0
    sortino = (
        float(excess.mean() / dd_dev * np.sqrt(TRADING_DAYS_PER_YEAR))
        if dd_dev > 0 else 0.0
    )

    # Drawdown
    running_max = ec.cummax()
    dd = ec / running_max - 1.0
    max_dd = float(dd.min())
    # Max DD duration: longest consecutive run where dd < 0
    underwater = dd < -1e-9
    max_dur = cur = 0
    for v in underwater:
        if v:
            cur += 1
            max_dur = max(max_dur, cur)
        else:
            cur = 0
    underwater_pct = float(underwater.mean()) if len(underwater) else 0.0

    pos_days = float((rets > 0).mean())
    skew_val = float(rets.skew()) if len(rets) >= 3 else 0.0
    kurt_val = float(rets.kurt()) if len(rets) >= 4 else 0.0

    return EquityStats(
        start_equity=start,
        end_equity=end,
        total_return_pct=total_ret * 100.0,
        cagr_pct=cagr * 100.0,
        annual_vol_pct=ann_vol * 100.0,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd * 100.0,
        max_dd_duration_days=int(max_dur),
        underwater_pct=underwater_pct * 100.0,
        best_day_pct=float(rets.max()) * 100.0,
        worst_day_pct=float(rets.min()) * 100.0,
        positive_days_pct=pos_days * 100.0,
        skewness=skew_val,
        kurtosis_excess=kurt_val,
    )


# ---------------------------------------------------------------------------
# Exposure
# ---------------------------------------------------------------------------
def exposure_pct(positions_series: "pd.Series") -> float:
    """Given a series of daily position counts (or boolean in-market), return
    the % of days with at least one position open."""
    if positions_series is None or len(positions_series) == 0:
        return 0.0
    return float((positions_series > 0).mean()) * 100.0


# ---------------------------------------------------------------------------
# Benchmark comparison
# ---------------------------------------------------------------------------
@dataclass
class BenchmarkStats:
    """Strategy vs benchmark: alpha, beta, information ratio, correlation."""

    benchmark_total_return_pct: float = 0.0
    benchmark_cagr_pct: float = 0.0
    benchmark_sharpe: float = 0.0
    beta: float = 0.0
    alpha_annual_pct: float = 0.0
    correlation: float = 0.0
    tracking_error_pct: float = 0.0
    information_ratio: float = 0.0
    excess_return_pct: float = 0.0


def benchmark_stats(
    strategy_equity: "pd.Series",
    benchmark_equity: "pd.Series",
    rf_annual: float = 0.0,
) -> BenchmarkStats:
    """Compute benchmark-relative metrics. Both series are aligned by date."""
    if strategy_equity is None or benchmark_equity is None:
        return BenchmarkStats()
    joined = pd.concat(
        [strategy_equity.rename("s"), benchmark_equity.rename("b")], axis=1
    ).dropna()
    if len(joined) < 5:
        return BenchmarkStats()

    s_ret = joined["s"].pct_change().dropna()
    b_ret = joined["b"].pct_change().dropna()
    common = s_ret.index.intersection(b_ret.index)
    s_ret = s_ret.loc[common]
    b_ret = b_ret.loc[common]
    if len(common) < 5:
        return BenchmarkStats()

    # Beta via covariance / variance
    var_b = float(b_ret.var())
    cov = float(np.cov(s_ret.values, b_ret.values, ddof=1)[0, 1])
    beta = cov / var_b if var_b > 0 else 0.0
    rf_daily = rf_annual / TRADING_DAYS_PER_YEAR
    # Annualised alpha = (mean(s) - rf) - beta * (mean(b) - rf), then * 252
    alpha_daily = (s_ret.mean() - rf_daily) - beta * (b_ret.mean() - rf_daily)
    alpha_annual = float(alpha_daily * TRADING_DAYS_PER_YEAR)

    corr = float(s_ret.corr(b_ret)) if s_ret.std() > 0 and b_ret.std() > 0 else 0.0

    # Tracking error & info ratio
    active = s_ret - b_ret
    te_ann = float(active.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    ir = (
        float(active.mean() / active.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
        if active.std() > 0 else 0.0
    )

    b_start = float(joined["b"].iloc[0])
    b_end = float(joined["b"].iloc[-1])
    b_total = (b_end / b_start - 1.0) if b_start > 0 else 0.0
    years = max((joined.index[-1] - joined.index[0]).days / 365.25, 1 / 365.25)
    b_cagr = (b_end / b_start) ** (1.0 / years) - 1.0 if b_start > 0 else 0.0
    b_excess = b_ret - rf_daily
    b_sharpe = (
        float(b_excess.mean() / b_ret.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
        if b_ret.std() > 0 else 0.0
    )

    s_start = float(joined["s"].iloc[0])
    s_end = float(joined["s"].iloc[-1])
    s_total = (s_end / s_start - 1.0) if s_start > 0 else 0.0

    return BenchmarkStats(
        benchmark_total_return_pct=b_total * 100.0,
        benchmark_cagr_pct=b_cagr * 100.0,
        benchmark_sharpe=b_sharpe,
        beta=beta,
        alpha_annual_pct=alpha_annual * 100.0,
        correlation=corr,
        tracking_error_pct=te_ann * 100.0,
        information_ratio=ir,
        excess_return_pct=(s_total - b_total) * 100.0,
    )


# ---------------------------------------------------------------------------
# Rolling metrics
# ---------------------------------------------------------------------------
def rolling_sharpe(equity_curve: "pd.Series", window: int = 60) -> "pd.Series":
    """Rolling Sharpe over a window of trading days (annualised)."""
    if equity_curve is None or len(equity_curve) < window + 2:
        return pd.Series(dtype=float)
    rets = equity_curve.pct_change()
    roll_mean = rets.rolling(window).mean()
    roll_std = rets.rolling(window).std()
    return (roll_mean / roll_std) * np.sqrt(TRADING_DAYS_PER_YEAR)


def rolling_drawdown(equity_curve: "pd.Series") -> "pd.Series":
    """Drawdown series in % terms (negative numbers)."""
    if equity_curve is None or len(equity_curve) == 0:
        return pd.Series(dtype=float)
    rm = equity_curve.cummax()
    return (equity_curve / rm - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Monthly returns matrix
# ---------------------------------------------------------------------------
def monthly_returns_matrix(equity_curve: "pd.Series") -> "pd.DataFrame":
    """Returns a (years × months) DataFrame of monthly returns in %.
    Used to render a heatmap in the UI."""
    if equity_curve is None or len(equity_curve) < 2:
        return pd.DataFrame()
    eq = equity_curve.dropna()
    # Last value of each month
    m = eq.resample("ME").last()
    rets = m.pct_change().dropna() * 100.0
    if rets.empty:
        return pd.DataFrame()
    df = rets.to_frame("r")
    df["Year"] = df.index.year
    df["Month"] = df.index.month
    mat = df.pivot_table(index="Year", columns="Month", values="r")
    # Add YTD column at the end
    yearly = mat.apply(lambda row: (1 + row.fillna(0) / 100.0).prod() * 100.0 - 100.0, axis=1)
    mat["YTD"] = yearly
    return mat


# ---------------------------------------------------------------------------
# Walk-forward stability
# ---------------------------------------------------------------------------
@dataclass
class WalkForwardSummary:
    """Aggregate stability metrics from a walk-forward run."""

    n_windows: int = 0
    avg_return_pct: float = 0.0
    std_return_pct: float = 0.0
    avg_sharpe: float = 0.0
    pct_profitable_windows: float = 0.0
    worst_window_return_pct: float = 0.0
    best_window_return_pct: float = 0.0


def walk_forward_summary(window_stats: list[EquityStats]) -> WalkForwardSummary:
    if not window_stats:
        return WalkForwardSummary()
    rets = [s.total_return_pct for s in window_stats]
    sharpes = [s.sharpe for s in window_stats]
    profitable = [r for r in rets if r > 0]
    return WalkForwardSummary(
        n_windows=len(window_stats),
        avg_return_pct=float(np.mean(rets)),
        std_return_pct=float(np.std(rets)),
        avg_sharpe=float(np.mean(sharpes)),
        pct_profitable_windows=len(profitable) / len(rets) * 100.0,
        worst_window_return_pct=float(np.min(rets)),
        best_window_return_pct=float(np.max(rets)),
    )
