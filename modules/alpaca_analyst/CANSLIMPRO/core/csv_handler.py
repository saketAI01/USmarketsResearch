"""CSV import/export for watchlists and results."""
from __future__ import annotations
import csv
import io
from pathlib import Path
from typing import List

from config import COMPONENT_LABELS


def load_watchlist(path: str) -> tuple[list[str], list[str]]:
    """
    Load tickers from a CSV file.
    Accepts:
      - single column of tickers
      - multi-column with 'Ticker' or 'Symbol' header
    Returns (tickers, warnings).
    """
    tickers, warnings = [], []
    p = Path(path)
    if not p.exists():
        return [], [f"File not found: {path}"]

    text = p.read_text(encoding="utf-8-sig").strip()
    if not text:
        return [], ["File is empty"]

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], ["No rows found"]

    # Detect header
    header = [h.strip().lower() for h in rows[0]]
    ticker_col = 0
    if "ticker" in header:
        ticker_col = header.index("ticker")
    elif "symbol" in header:
        ticker_col = header.index("symbol")
    elif "scrip" in header:
        ticker_col = header.index("scrip")

    start = 1 if any(h in header for h in ["ticker", "symbol", "scrip", "name"]) else 0

    seen = set()
    for i, row in enumerate(rows[start:], start=start + 1):
        if not row or ticker_col >= len(row):
            continue
        t = row[ticker_col].strip().upper()
        if not t or t.startswith("#"):
            continue
        if len(t) > 20:
            warnings.append(f"Row {i}: '{t}' looks invalid — skipped")
            continue
        if t in seen:
            warnings.append(f"Duplicate ticker '{t}' — skipped")
            continue
        seen.add(t)
        tickers.append(t)

    if not tickers:
        warnings.append("No valid tickers found in file")

    return tickers, warnings


def export_results_csv(results: list, path: str) -> str:
    """Export CANSLIM results to CSV. Returns path written."""
    if not results:
        return ""

    fieldnames = [
        "Ticker", "Company", "Market", "Composite", "Rating", "BuyCandidate",
        "C_Score", "A_Score", "N_Score", "S_Score", "L_Score", "I_Score", "M_Score",
        "C_Metric", "A_Metric", "N_Metric", "S_Metric", "L_Metric", "I_Metric", "M_Metric",
        "DataQuality", "Errors",
    ]

    p = Path(path)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            comp = r.components
            row = {
                "Ticker":       r.ticker,
                "Company":      r.company_name,
                "Market":       r.market,
                "Composite":    f"{r.composite_score:.1f}",
                "Rating":       r.rating,
                "BuyCandidate": "Yes" if r.buy_candidate else "No",
                "DataQuality":  r.data_quality,
                "Errors":       "; ".join(r.errors),
            }
            for k in ["C", "A", "N", "S", "L", "I", "M"]:
                cr = comp.get(k)
                row[f"{k}_Score"]  = cr.score if cr else ""
                row[f"{k}_Metric"] = cr.key_metric if cr else ""
            w.writerow(row)

    return str(p)
