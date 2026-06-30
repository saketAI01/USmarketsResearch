"""
Alpaca Markets Data API client.
Used for US stock price bars (OHLCV), latest quotes, and volume data.
Free tier (IEX feed) is used — no funded account required.
Falls back gracefully if keys are absent or requests fail.

Docs: https://docs.alpaca.markets/reference/stockbars
"""
from __future__ import annotations
import time
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

from config import ALPACA_DATA


class AlpacaClient:
    """Thin wrapper around the Alpaca v2 Data REST API."""

    _session: requests.Session | None = None

    def __init__(self, key_id: str, secret_key: str):
        self.key_id     = key_id.strip()
        self.secret_key = secret_key.strip()
        self._ok        = bool(self.key_id and self.secret_key)

    @property
    def available(self) -> bool:
        return self._ok

    def _session_get(self, url: str, params: dict) -> dict:
        headers = {
            "APCA-API-KEY-ID":     self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept":              "application/json",
        }
        r = requests.get(url, headers=headers, params=params, timeout=12)
        r.raise_for_status()
        return r.json()

    # ── Price bars ────────────────────────────────────────────────────────────

    def get_daily_bars(self, symbol: str, days: int = 380) -> pd.DataFrame:
        """
        Fetch daily OHLCV bars for a US stock.
        Returns a DataFrame indexed by date with columns: Open, High, Low, Close, Volume.
        Returns empty DataFrame on failure.
        """
        if not self._ok:
            return pd.DataFrame()

        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 10)   # extra buffer for weekends/holidays

        params = {
            "timeframe": "1Day",
            "start":     start.strftime("%Y-%m-%dT00:00:00Z"),
            "end":       end.strftime("%Y-%m-%dT00:00:00Z"),
            "feed":      "iex",      # free tier; use "sip" if you have a paid plan
            "limit":     1000,
            "sort":      "asc",
        }

        all_bars = []
        url      = f"{ALPACA_DATA}/stocks/{symbol}/bars"

        try:
            while url:
                data = self._session_get(url, params)
                bars = data.get("bars") or []
                all_bars.extend(bars)
                next_token = data.get("next_page_token")
                if next_token:
                    params = {"page_token": next_token, "feed": "iex"}
                else:
                    url = ""
        except Exception:
            return pd.DataFrame()

        if not all_bars:
            return pd.DataFrame()

        df = pd.DataFrame(all_bars)
        df["t"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
        df = df.rename(columns={
            "t": "Date", "o": "Open", "h": "High",
            "l": "Low",  "c": "Close", "v": "Volume",
        })
        df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]
        df = df.set_index("Date").sort_index()
        return df

    # ── Snapshot (latest price) ────────────────────────────────────────────────

    def get_snapshot(self, symbol: str) -> dict:
        """
        Fetch a snapshot dict for a single US stock symbol.
        Keys of interest: latestTrade.p (last price), dailyBar.h/l (today's high/low).
        Returns {} on failure.
        """
        if not self._ok:
            return {}
        try:
            data = self._session_get(
                f"{ALPACA_DATA}/stocks/{symbol}/snapshot",
                {"feed": "iex"},
            )
            return data
        except Exception:
            return {}

    # ── Multi-symbol snapshot ─────────────────────────────────────────────────

    def get_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        """
        Fetch snapshots for multiple symbols in one call (max 100 per request).
        Returns dict: symbol -> snapshot dict.
        """
        if not self._ok or not symbols:
            return {}
        results = {}
        # Alpaca allows up to 100 symbols per call
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i+100]
            try:
                data = self._session_get(
                    f"{ALPACA_DATA}/stocks/snapshots",
                    {"symbols": ",".join(chunk), "feed": "iex"},
                )
                results.update(data)
                if len(symbols) > 100:
                    time.sleep(0.2)
            except Exception:
                pass
        return results


def make_alpaca_client() -> AlpacaClient:
    """Construct an AlpacaClient from the auto-discovered keys."""
    from config import get_keys
    keys = get_keys()
    return AlpacaClient(
        key_id     = keys.get("ALPACA_KEY_ID", ""),
        secret_key = keys.get("ALPACA_SECRET_KEY", ""),
    )
