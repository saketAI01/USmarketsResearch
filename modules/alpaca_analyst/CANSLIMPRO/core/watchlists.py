"""
Built-in and user-saved watchlist management.

Built-in lists live in data/watchlists/ (bundled with the project).
User-saved lists live in ~/.canslim_pro/watchlists/.
"""
from __future__ import annotations
import csv
import json
from pathlib import Path
from datetime import datetime

# Project root = parent of this file's parent (canslim_pro/)
_PROJECT_ROOT  = Path(__file__).parent.parent.resolve()
_BUILTIN_DIR   = _PROJECT_ROOT / "data" / "watchlists"
_USER_LIST_DIR = Path.home() / ".canslim_pro" / "watchlists"

# ── Built-in list registry ────────────────────────────────────────────────────

BUILTIN_LISTS = {
    "S&P 500 (Top 50)":    "sp500_top50.csv",
    "NASDAQ (Top 50)":     "nasdaq_top50.csv",
    "NSE Nifty 50":        "nse50.csv",
    "NSE 200 (Top 50)":    "nse200_top50.csv",
}


def load_builtin(name: str) -> tuple[list[str], str]:
    """
    Load a built-in watchlist by display name.
    Returns (tickers, error_message).  error_message is '' on success.
    """
    fname = BUILTIN_LISTS.get(name)
    if not fname:
        return [], f"Unknown built-in list: {name}"
    path = _BUILTIN_DIR / fname
    if not path.exists():
        return [], f"Built-in list file not found: {path}"
    return _read_csv(path)


def builtin_names() -> list[str]:
    return list(BUILTIN_LISTS.keys())


# ── User watchlist save / load ────────────────────────────────────────────────

def save_user_list(name: str, tickers: list[str]) -> str:
    """
    Save a user watchlist.  Name becomes the filename (sanitised).
    Returns '' on success, error string on failure.
    """
    if not name or not tickers:
        return "Name and tickers must not be empty."
    _USER_LIST_DIR.mkdir(parents=True, exist_ok=True)
    safe   = _safe_name(name)
    path   = _USER_LIST_DIR / f"{safe}.csv"
    try:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Ticker", "SavedAt"])
            for t in tickers:
                w.writerow([t, datetime.now().strftime("%Y-%m-%d %H:%M")])
        return ""
    except Exception as e:
        return str(e)


def list_user_lists() -> list[str]:
    """Return display names of all saved user watchlists."""
    _USER_LIST_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(p.stem for p in _USER_LIST_DIR.glob("*.csv"))


def load_user_list(name: str) -> tuple[list[str], str]:
    """Load a saved user watchlist by name.  Returns (tickers, error)."""
    _USER_LIST_DIR.mkdir(parents=True, exist_ok=True)
    path = _USER_LIST_DIR / f"{_safe_name(name)}.csv"
    if not path.exists():
        return [], f"Saved list not found: {name}"
    return _read_csv(path)


def delete_user_list(name: str) -> str:
    """Delete a saved user watchlist. Returns '' on success, error string on failure."""
    path = _USER_LIST_DIR / f"{_safe_name(name)}.csv"
    try:
        path.unlink(missing_ok=True)
        return ""
    except Exception as e:
        return str(e)


def rename_user_list(old_name: str, new_name: str) -> str:
    old_path = _USER_LIST_DIR / f"{_safe_name(old_name)}.csv"
    new_path = _USER_LIST_DIR / f"{_safe_name(new_name)}.csv"
    try:
        old_path.rename(new_path)
        return ""
    except Exception as e:
        return str(e)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    """Convert display name to safe filename stem."""
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name)
    return safe.strip().replace(" ", "_")[:60]


def _read_csv(path: Path) -> tuple[list[str], str]:
    """Read tickers from a CSV file (Ticker column or first column)."""
    try:
        tickers: list[str] = []
        seen: set[str]     = set()
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows   = list(reader)
        if not rows:
            return [], "File is empty."

        header = [h.strip().lower() for h in rows[0]]
        col = 0
        for candidate in ["ticker", "symbol", "scrip"]:
            if candidate in header:
                col = header.index(candidate)
                break

        start = 1 if any(h in header for h in ["ticker","symbol","scrip","name","sector"]) else 0

        for row in rows[start:]:
            if not row or col >= len(row):
                continue
            t = row[col].strip().upper()
            if t and t not in seen and not t.startswith("#"):
                seen.add(t)
                tickers.append(t)

        return tickers, ""
    except Exception as e:
        return [], str(e)
