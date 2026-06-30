"""
CANSLIM scoring engine — all 7 components with O'Neil weights.
Each scorer returns (score: int 0-100, key_metric: str, rationale: str).
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

from config import WEIGHTS, get_rating, COMPONENT_LABELS
from core.data_fetcher import StockData


@dataclass
class ComponentResult:
    key: str
    label: str
    score: int
    key_metric: str
    rationale: str
    weight: float


@dataclass
class CANSLIMResult:
    ticker: str
    company_name: str
    sector: str
    market: str
    currency: str
    composite_score: float
    rating: str
    components: dict[str, ComponentResult]
    buy_candidate: bool
    data_quality: str        # "Good" / "Partial" / "Poor"
    errors: list[str]
    warnings: list[str]

    def component_scores(self) -> dict[str, int]:
        return {k: v.score for k, v in self.components.items()}


# ─── helpers ──────────────────────────────────────────────────────────────────

def _clamp(v: float, lo=0.0, hi=100.0) -> int:
    return int(max(lo, min(hi, v)))


def _pct_change(new: float, old: float) -> Optional[float]:
    if old is None or old == 0:
        return None
    return (new - old) / abs(old)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


# ─── C: Current Quarterly Earnings ───────────────────────────────────────────

def score_c(sd: StockData) -> ComponentResult:
    eps = sd.quarterly_eps
    rev = sd.quarterly_revenue

    if len(eps) >= 5:
        recent, year_ago = eps[0], eps[4]
        g = _pct_change(recent, year_ago)
        if g is None:
            score, metric = 30, f"EPS: {recent:.3f} (no comparison)"
            rationale = "Cannot calculate YoY growth — missing year-ago EPS."
        else:
            if year_ago < 0 and recent > 0:
                score, metric = 80, f"EPS turned positive ({recent:.3f})"
                rationale = "EPS moved from loss to profit — strong C signal."
            elif g >= 1.0:
                score = _clamp(90 + (g - 1.0) * 10)
                metric = f"Q EPS {g*100:+.0f}% YoY"
                rationale = f"Exceptional quarterly earnings acceleration of {g*100:.0f}% — top-tier C signal."
            elif g >= 0.50:
                score = _clamp(75 + (g - 0.5) * 30)
                metric = f"Q EPS {g*100:+.0f}% YoY"
                rationale = f"Strong EPS growth of {g*100:.0f}% — meets O'Neil's 25%+ threshold comfortably."
            elif g >= 0.25:
                score = _clamp(60 + (g - 0.25) * 60)
                metric = f"Q EPS {g*100:+.0f}% YoY"
                rationale = f"EPS growth of {g*100:.0f}% meets the minimum 25% CANSLIM threshold."
            elif g >= 0:
                score = _clamp(35 + g * 100)
                metric = f"Q EPS {g*100:+.0f}% YoY"
                rationale = f"EPS growing but below O'Neil's 25% minimum threshold ({g*100:.0f}%)."
            else:
                score = _clamp(30 + g * 60)
                metric = f"Q EPS {g*100:+.0f}% YoY"
                rationale = f"Earnings contracting — negative C signal. O'Neil requires accelerating growth."

        # Revenue acceleration bonus
        if len(rev) >= 5 and rev[4] > 0:
            rev_g = _pct_change(rev[0], rev[4])
            if rev_g and rev_g > 0.15:
                score = _clamp(score + 5)
                rationale += f" Revenue also up {rev_g*100:.0f}% — growth is organic."
    elif len(eps) >= 2:
        g = _pct_change(eps[0], eps[1])
        score = 35 if g is None else _clamp(40 + (g or 0) * 60)
        metric = f"EPS: {eps[0]:.3f} (limited history)"
        rationale = "Only sequential EPS comparison available — limited confidence."
    else:
        score, metric = 20, "EPS data unavailable"
        rationale = "No quarterly EPS data — C component scored conservatively."

    return ComponentResult("C", COMPONENT_LABELS["C"], score, metric, rationale, WEIGHTS["C"])


# ─── A: Annual EPS Growth ─────────────────────────────────────────────────────

def score_a(sd: StockData) -> ComponentResult:
    eps = sd.annual_eps

    if len(eps) >= 3:
        recent, oldest = eps[0], eps[min(3, len(eps)-1)]
        years = min(3, len(eps) - 1)
        had_loss = any(e < 0 for e in eps[:years+1])

        if oldest <= 0:
            if eps[0] > 0:
                score, metric = 72, "Profitable from unprofitable base"
                rationale = "Company turned profitable — strong improvement but CAGR undefined from negative base."
            else:
                score, metric = 15, "Persistent losses"
                rationale = "Company running multi-year losses — fails A component."
        else:
            cagr = (recent / oldest) ** (1 / years) - 1
            if cagr >= 0.35:
                score = _clamp(88 + (cagr - 0.35) * 30)
            elif cagr >= 0.25:
                score = _clamp(75 + (cagr - 0.25) * 130)
            elif cagr >= 0.15:
                score = _clamp(58 + (cagr - 0.15) * 170)
            elif cagr >= 0.05:
                score = _clamp(38 + (cagr - 0.05) * 200)
            else:
                score = _clamp(20 + max(cagr, -0.5) * 36)
            metric = f"{years}yr EPS CAGR {cagr*100:.1f}%"
            meets = "meets O'Neil 25%+ target." if cagr >= 0.25 else "below 25% minimum threshold."
            rationale = f"{years}-year EPS CAGR of {cagr*100:.1f}% — {meets}"
            if had_loss:
                score = _clamp(score - 10)
                rationale += " Penalised: at least one loss year in lookback period."
    else:
        score, metric = 25, "Insufficient annual data"
        rationale = "Less than 3 years of annual EPS data available."

    return ComponentResult("A", COMPONENT_LABELS["A"], score, metric, rationale, WEIGHTS["A"])


# ─── N: Newness / Near New Highs ─────────────────────────────────────────────

def score_n(sd: StockData) -> ComponentResult:
    if sd.current_price <= 0 or sd.high_52w <= 0:
        return ComponentResult("N", COMPONENT_LABELS["N"], 20,
                               "Price data missing", "Cannot assess proximity to highs.", WEIGHTS["N"])

    pct_below = (sd.high_52w - sd.current_price) / sd.high_52w

    if pct_below <= 0.03:
        score = _clamp(92 + (0.03 - pct_below) / 0.03 * 8)
        metric = f"At/near 52wk high ({pct_below*100:.1f}% below)"
        rationale = "Stock at or very near 52-week high — ideal N setup for a breakout entry."
    elif pct_below <= 0.10:
        score = _clamp(75 + (0.10 - pct_below) / 0.07 * 17)
        metric = f"{pct_below*100:.1f}% below 52wk high"
        rationale = f"Within 10% of highs — approaching buy zone. O'Neil favours entries near the pivot."
    elif pct_below <= 0.20:
        score = _clamp(55 + (0.20 - pct_below) / 0.10 * 20)
        metric = f"{pct_below*100:.1f}% below 52wk high"
        rationale = f"10–20% below highs — extended from ideal entry. Wait for base to form."
    elif pct_below <= 0.35:
        score = _clamp(30 + (0.35 - pct_below) / 0.15 * 25)
        metric = f"{pct_below*100:.1f}% below 52wk high"
        rationale = f"Deep base — {pct_below*100:.0f}% off highs. Needs significant work before O'Neil buy point."
    else:
        score = _clamp(max(5, 30 - (pct_below - 0.35) * 60))
        metric = f"{pct_below*100:.1f}% below 52wk high"
        rationale = f"Far from highs ({pct_below*100:.0f}% below) — laggard by N definition. Do not chase."

    return ComponentResult("N", COMPONENT_LABELS["N"], score, metric, rationale, WEIGHTS["N"])


# ─── S: Supply / Demand (Volume Accumulation) ─────────────────────────────────

def score_s(sd: StockData) -> ComponentResult:
    hist = sd.price_history
    if hist is None or hist.empty or len(hist) < 20:
        return ComponentResult("S", COMPONENT_LABELS["S"], 35,
                               "Insufficient price history", "Cannot calculate volume pattern.", WEIGHTS["S"])

    tail = hist.tail(50).copy()
    tail["up"] = tail["Close"] >= tail["Open"]
    up_vol   = tail.loc[tail["up"],  "Volume"].sum()
    down_vol = tail.loc[~tail["up"], "Volume"].sum()
    total    = up_vol + down_vol

    if total <= 0:
        return ComponentResult("S", COMPONENT_LABELS["S"], 35,
                               "Volume data missing", "Cannot assess accumulation/distribution.", WEIGHTS["S"])

    acc_ratio = up_vol / total  # > 0.5 = accumulation

    if acc_ratio >= 0.65:
        score = _clamp(80 + (acc_ratio - 0.65) * 100)
        sentiment = "Strong accumulation"
    elif acc_ratio >= 0.55:
        score = _clamp(62 + (acc_ratio - 0.55) * 180)
        sentiment = "Moderate accumulation"
    elif acc_ratio >= 0.45:
        score = _clamp(40 + (acc_ratio - 0.45) * 220)
        sentiment = "Neutral volume"
    else:
        score = _clamp(max(5, 40 + (acc_ratio - 0.45) * 200))
        sentiment = "Distribution pattern"

    metric = f"Acc/Dist ratio {acc_ratio:.2f} ({sentiment})"

    # Float size adjustment
    if sd.shares_float > 0:
        float_bn = sd.shares_float / 1e9
        if float_bn > 3:
            score = _clamp(score - 8)
            rationale = f"{sentiment} over 50 days. Large float ({float_bn:.1f}B shares) limits explosive upside per O'Neil."
        elif float_bn < 0.5:
            score = _clamp(score + 8)
            rationale = f"{sentiment} with tight float ({float_bn*1000:.0f}M shares) — ideal supply setup."
        else:
            rationale = f"{sentiment} pattern over past 50 trading days. Float {float_bn:.1f}B shares — manageable."
    else:
        rationale = f"{sentiment} over 50 trading days."

    return ComponentResult("S", COMPONENT_LABELS["S"], score, metric, rationale, WEIGHTS["S"])


# ─── L: Leadership / Relative Strength ────────────────────────────────────────

def score_l(sd: StockData) -> ComponentResult:
    ph  = sd.price_history
    bh  = sd.benchmark_history
    bench_name = "Nifty 50" if sd.market == "IN" else "S&P 500"

    if ph is None or bh is None or ph.empty or bh.empty:
        return ComponentResult("L", COMPONENT_LABELS["L"], 30,
                               "Price history missing", f"Cannot calculate RS vs {bench_name}.", WEIGHTS["L"])

    try:
        ph_ret  = ph["Close"].iloc[-1] / ph["Close"].iloc[0] - 1
        bh_ret  = bh["Close"].iloc[-1] / bh["Close"].iloc[0] - 1

        if bh_ret == -1:
            rs = 0.0
        elif bh_ret == 0:
            rs = 1.0 if ph_ret >= 0 else 0.0
        else:
            rs = (1 + ph_ret) / (1 + bh_ret)

        metric = f"12mo RS {rs:.2f}x vs {bench_name} (stock {ph_ret*100:+.1f}% / bench {bh_ret*100:+.1f}%)"

        if rs >= 2.0:
            score = _clamp(92 + (rs - 2.0) * 4)
            rationale = f"Dominant leader — {rs:.1f}x benchmark return. Exactly what O'Neil looks for."
        elif rs >= 1.5:
            score = _clamp(78 + (rs - 1.5) * 28)
            rationale = f"Strong leader at {rs:.1f}x — outperforming {bench_name} meaningfully."
        elif rs >= 1.2:
            score = _clamp(62 + (rs - 1.2) * 53)
            rationale = f"Moderate outperformance ({rs:.1f}x). Qualifies as leader but not standout."
        elif rs >= 1.0:
            score = _clamp(46 + (rs - 1.0) * 80)
            rationale = f"Marginally outperforming ({rs:.2f}x). Near the borderline — watch for RS improvement."
        elif rs >= 0.8:
            score = _clamp(25 + (rs - 0.8) * 105)
            rationale = f"Underperforming {bench_name} at {rs:.2f}x. Laggard by CANSLIM definition."
        else:
            score = _clamp(max(5, 25 + (rs - 0.8) * 80))
            rationale = f"Significant underperformer ({rs:.2f}x). O'Neil rule: avoid laggards."
    except Exception as e:
        return ComponentResult("L", COMPONENT_LABELS["L"], 25,
                               "RS calculation failed", str(e), WEIGHTS["L"])

    return ComponentResult("L", COMPONENT_LABELS["L"], score, metric, rationale, WEIGHTS["L"])


# ─── I: Institutional Sponsorship ────────────────────────────────────────────

def score_i(sd: StockData) -> ComponentResult:
    pct = sd.institutional_pct

    if pct <= 0:
        return ComponentResult("I", COMPONENT_LABELS["I"], 30,
                               "Institutional data unavailable",
                               "No institutional ownership data found.", WEIGHTS["I"])

    if pct >= 75:
        score = _clamp(80 + (pct - 75) / 25 * 20)
        level = "Very high"
    elif pct >= 55:
        score = _clamp(62 + (pct - 55) / 20 * 18)
        level = "High"
    elif pct >= 35:
        score = _clamp(42 + (pct - 35) / 20 * 20)
        level = "Moderate"
    else:
        score = _clamp(max(10, 42 - (35 - pct) * 1.5))
        level = "Low"

    count_note = f", {sd.institutional_count} holders" if sd.institutional_count > 0 else ""
    metric = f"Institutional ownership {pct:.1f}%{count_note}"
    rationale = (
        f"{level} institutional ownership at {pct:.1f}%. "
        + ("Institutions have validated the investment thesis." if pct >= 55
           else "O'Neil prefers stocks with growing, significant institutional backing.")
    )

    return ComponentResult("I", COMPONENT_LABELS["I"], score, metric, rationale, WEIGHTS["I"])


# ─── M: Market Direction ─────────────────────────────────────────────────────

def score_m(sd: StockData) -> ComponentResult:
    bh = sd.benchmark_history
    bench_name = "Nifty 50" if sd.market == "IN" else "S&P 500"

    if bh is None or bh.empty or len(bh) < 55:
        return ComponentResult("M", COMPONENT_LABELS["M"], 50,
                               "Benchmark data insufficient",
                               "Cannot determine market direction from available data.", WEIGHTS["M"])

    close  = bh["Close"]
    ema50  = _ema(close, 50).iloc[-1]
    ema200 = _ema(close, 200).iloc[-1] if len(close) >= 200 else None
    last   = close.iloc[-1]

    pct_vs_50 = (last - ema50) / ema50

    # Recent trend: last 20 vs prior 20
    trend = (close.iloc[-1] / close.iloc[-20] - 1) if len(close) >= 20 else 0

    metric = (
        f"{bench_name} {pct_vs_50*100:+.1f}% vs 50d EMA"
        + (f", {trend*100:+.1f}% 20d trend" if trend else "")
    )

    if pct_vs_50 >= 0.04 and trend > 0:
        score = _clamp(82 + pct_vs_50 * 200)
        condition = "Confirmed uptrend"
        rationale = f"{bench_name} well above 50-day EMA and trending higher — ideal M environment."
    elif pct_vs_50 >= 0.01:
        score = _clamp(65 + pct_vs_50 * 300)
        condition = "Uptrend — some pressure"
        rationale = f"{bench_name} above 50-day EMA but momentum moderate. Proceed with selectivity."
    elif pct_vs_50 >= -0.02:
        score = _clamp(50 + pct_vs_50 * 600)
        condition = "Choppy / under pressure"
        rationale = f"{bench_name} near 50-day EMA — indecisive market. Raise cash threshold, reduce position sizing."
    elif pct_vs_50 >= -0.06:
        score = _clamp(30 + pct_vs_50 * 300)
        condition = "Correction"
        rationale = f"{bench_name} below 50-day EMA in correction. O'Neil: wait for follow-through day before new entries."
    else:
        score = _clamp(max(5, 15 + pct_vs_50 * 150))
        condition = "Bear market"
        rationale = f"{bench_name} in bear market ({pct_vs_50*100:.1f}% below 50d EMA). CANSLIM rule: raise cash, do not buy."

    metric = f"{condition} — {metric}"
    return ComponentResult("M", COMPONENT_LABELS["M"], score, metric, rationale, WEIGHTS["M"])


# ─── Composite scorer ────────────────────────────────────────────────────────

def score_canslim(sd: StockData) -> CANSLIMResult:
    scorers = [score_c, score_a, score_n, score_s, score_l, score_i, score_m]
    components: dict[str, ComponentResult] = {}

    for scorer in scorers:
        try:
            result = scorer(sd)
        except Exception as e:
            key = scorer.__name__.split("_")[1].upper()
            result = ComponentResult(
                key, COMPONENT_LABELS.get(key, key), 20,
                "Scoring error", str(e), WEIGHTS.get(key, 0.0)
            )
        components[result.key] = result

    composite = sum(r.score * r.weight for r in components.values())
    composite = round(composite, 1)
    rating = get_rating(composite)

    error_count = len(sd.errors)
    if error_count == 0:
        quality = "Good"
    elif error_count <= 2:
        quality = "Partial"
    else:
        quality = "Poor"

    return CANSLIMResult(
        ticker=sd.ticker,
        company_name=sd.company_name,
        sector=sd.sector,
        market=sd.market,
        currency=sd.currency,
        composite_score=composite,
        rating=rating,
        components=components,
        buy_candidate=(composite >= 70 and components["L"].score >= 60 and components["N"].score >= 55),
        data_quality=quality,
        errors=sd.errors,
        warnings=sd.warnings,
    )
