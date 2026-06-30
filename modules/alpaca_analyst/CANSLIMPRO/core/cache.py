"""
Disk-based cache for StockData objects.
Location: ~/.canslim_pro/cache/{TICKER}.pkl
Default TTL: 12 hours (configurable per call).
Thread-safe for reads; writes use atomic temp-file rename.
"""
from __future__ import annotations
import os
import pickle
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DIR       = Path.home() / ".canslim_pro" / "cache"
DEFAULT_TTL_HRS = 12.0


def _path(ticker: str) -> Path:
    safe = ticker.replace(".", "_").replace("/", "_").replace("&", "n").upper()
    return CACHE_DIR / f"{safe}.pkl"


def get(ticker: str, max_age_hrs: float = DEFAULT_TTL_HRS):
    """
    Return cached StockData if it exists and is younger than max_age_hrs.
    Returns None if cache is missing, expired, or corrupt.
    """
    p = _path(ticker)
    if not p.exists():
        return None
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
        if datetime.now() - mtime > timedelta(hours=max_age_hrs):
            return None
        with p.open("rb") as f:
            return pickle.load(f)
    except Exception:
        _safe_unlink(p)
        return None


def put(ticker: str, stock_data) -> bool:
    """Persist StockData to cache atomically. Returns True on success."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(ticker)
    try:
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".pkl.tmp")
        with os.fdopen(fd, "wb") as f:
            pickle.dump(stock_data, f)
        os.replace(tmp, p)          # atomic on POSIX; near-atomic on Windows
        return True
    except Exception:
        return False


def invalidate(ticker: str):
    """Remove cached data for one ticker."""
    _safe_unlink(_path(ticker))


def clear_all():
    """Wipe all cached files."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for f in CACHE_DIR.glob("*.pkl"):
        _safe_unlink(f)


def age_str(ticker: str) -> str:
    """Human-readable age of cached data, e.g. '2h 14m ago'. '' if no cache."""
    p = _path(ticker)
    if not p.exists():
        return ""
    try:
        delta = datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)
        total = int(delta.total_seconds())
        if total < 60:
            return f"{total}s ago"
        if total < 3600:
            return f"{total//60}m ago"
        h, m = divmod(total // 60, 60)
        return f"{h}h {m}m ago" if m else f"{h}h ago"
    except Exception:
        return ""


def stats() -> dict:
    """Return summary stats: count, total_size_mb, oldest_ticker."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files  = list(CACHE_DIR.glob("*.pkl"))
    total  = sum(f.stat().st_size for f in files)
    oldest = None
    oldest_time = None
    for f in files:
        t = f.stat().st_mtime
        if oldest_time is None or t < oldest_time:
            oldest_time = t
            oldest = f.stem
    return {
        "count":    len(files),
        "size_mb":  round(total / 1_048_576, 2),
        "oldest":   oldest or "",
    }


def _safe_unlink(p: Path):
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass
