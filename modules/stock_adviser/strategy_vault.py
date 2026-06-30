"""
Strategy vault — load, save, merge, and fetch indicator-based strategies.

The vault keeps three sources side-by-side:

* **popular**   — bundled defaults shipped with the app
                  (file: ``popular_strategies.json``)
* **custom**    — strategies the user creates / edits in the Strategy
                  Builder tab (file: ``custom_strategies.json``)
* **imported**  — strategies fetched from a remote JSON endpoint
                  (file: ``imported_strategies.json``)

Each strategy is a dict in the shape::

    {
        "id":          "rsi_oversold_bounce",      # stable identifier
        "name":        "RSI Oversold Bounce",
        "category":    "Mean Reversion",
        "source":      "popular" | "custom" | "imported",
        "author":      "...",
        "description": "Free-form description",
        "tags":        ["RSI", "mean-reversion"],
        "buy":         "RSI14 CROSSOVER 30 AND LTP > SMA200",
        "sell":        "RSI14 > 70 OR RSI14 CROSSBELOW 50",
        "created_at":  "ISO timestamp",
        "updated_at":  "ISO timestamp",
        "fetched_from": "https://…",            # imported only
    }

The fetch endpoint is configurable; the default points at a placeholder
that may not resolve. The vault then falls back to the bundled file.
A user can host their own JSON of identical shape on any HTTPS URL.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# Default URL for "Fetch popular strategies from web". This is a
# convention — the user can override in the UI. We deliberately use a
# raw-GitHub-style URL pattern so anyone can self-host an identical
# JSON file and point the app at it. If the URL fails for any reason
# (offline, 404, malformed JSON), the bundled defaults remain in place.
DEFAULT_FETCH_URL = (
    "https://raw.githubusercontent.com/cowork-quant/strategies/main/"
    "popular_strategies.json"
)

POPULAR_FILE = "popular_strategies.json"
CUSTOM_FILE = "custom_strategies.json"
IMPORTED_FILE = "imported_strategies.json"


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_id(name: str) -> str:
    """Slugify a strategy name into a stable id."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or f"strategy_{int(datetime.now().timestamp())}"


# ===========================================================================
# Vault
# ===========================================================================
@dataclass
class StrategyVault:
    """Manages the three on-disk strategy files."""

    base_dir: Path
    fetch_url: str = DEFAULT_FETCH_URL
    timeout_sec: float = 8.0

    # In-memory caches
    _popular: list[dict] = field(default_factory=list)
    _custom: list[dict] = field(default_factory=list)
    _imported: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir)
        self.reload()

    # ---------- File paths -------------------------------------------------
    @property
    def popular_path(self) -> Path:
        return self.base_dir / POPULAR_FILE

    @property
    def custom_path(self) -> Path:
        return self.base_dir / CUSTOM_FILE

    @property
    def imported_path(self) -> Path:
        return self.base_dir / IMPORTED_FILE

    # ---------- Load / reload ---------------------------------------------
    def reload(self) -> None:
        self._popular = self._read_file(self.popular_path, default_source="popular")
        self._custom = self._read_file(self.custom_path, default_source="custom")
        self._imported = self._read_file(self.imported_path, default_source="imported")

    @staticmethod
    def _read_file(path: Path, default_source: str = "popular") -> list[dict]:
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return []
        items = data.get("strategies") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        cleaned: list[dict] = []
        for s in items:
            if not isinstance(s, dict) or "name" not in s:
                continue
            s.setdefault("id", _safe_id(s["name"]))
            s.setdefault("source", default_source)
            s.setdefault("category", "Uncategorised")
            s.setdefault("description", "")
            s.setdefault("tags", [])
            s.setdefault("author", "")
            s.setdefault("buy", "")
            s.setdefault("sell", "")
            cleaned.append(s)
        return cleaned

    # ---------- Read-only accessors ---------------------------------------
    @property
    def popular(self) -> list[dict]:
        return list(self._popular)

    @property
    def custom(self) -> list[dict]:
        return list(self._custom)

    @property
    def imported(self) -> list[dict]:
        return list(self._imported)

    def all(self) -> list[dict]:
        return self._popular + self._custom + self._imported

    def by_id(self, sid: str) -> Optional[dict]:
        for bucket in (self._custom, self._imported, self._popular):
            for s in bucket:
                if s.get("id") == sid:
                    return s
        return None

    def by_name(self, name: str) -> Optional[dict]:
        for s in self.all():
            if s.get("name", "").strip().lower() == name.strip().lower():
                return s
        return None

    # ---------- Mutation: custom strategies --------------------------------
    def save_custom(
        self,
        name: str,
        buy: str,
        sell: str,
        category: str = "Uncategorised",
        description: str = "",
        tags: Optional[list[str]] = None,
        author: str = "",
        sid: Optional[str] = None,
    ) -> dict:
        """Create or update a custom strategy. ``sid`` lets you target an
        existing entry by id (for renames / edits)."""
        ts = _iso_now()
        if sid:
            for i, s in enumerate(self._custom):
                if s.get("id") == sid:
                    s.update({
                        "name": name.strip(),
                        "category": category.strip() or "Uncategorised",
                        "description": description.strip(),
                        "tags": list(tags or []),
                        "author": author.strip(),
                        "buy": buy.strip(),
                        "sell": sell.strip(),
                        "updated_at": ts,
                    })
                    self._custom[i] = s
                    self._write_custom()
                    return s
        # New
        new_id = _safe_id(name)
        existing_ids = {s.get("id") for s in self._custom}
        if new_id in existing_ids:
            # de-dupe with a numeric suffix
            n = 2
            while f"{new_id}_{n}" in existing_ids:
                n += 1
            new_id = f"{new_id}_{n}"
        entry = {
            "id": new_id,
            "name": name.strip(),
            "category": category.strip() or "Uncategorised",
            "source": "custom",
            "author": author.strip(),
            "tags": list(tags or []),
            "description": description.strip(),
            "buy": buy.strip(),
            "sell": sell.strip(),
            "created_at": ts,
            "updated_at": ts,
        }
        self._custom.append(entry)
        self._write_custom()
        return entry

    def delete_custom(self, sid: str) -> bool:
        before = len(self._custom)
        self._custom = [s for s in self._custom if s.get("id") != sid]
        if len(self._custom) < before:
            self._write_custom()
            return True
        return False

    def duplicate(self, sid: str, new_name: Optional[str] = None) -> Optional[dict]:
        """Clone any strategy (popular / custom / imported) into the
        custom bucket so the user can edit it."""
        src = self.by_id(sid)
        if src is None:
            return None
        return self.save_custom(
            name=new_name or f"{src['name']} (copy)",
            buy=src.get("buy", ""),
            sell=src.get("sell", ""),
            category=src.get("category", "Uncategorised"),
            description=src.get("description", ""),
            tags=list(src.get("tags", [])),
            author=src.get("author", ""),
        )

    def _write_custom(self) -> None:
        payload = {
            "version": 1,
            "updated_at": _iso_now(),
            "strategies": self._custom,
        }
        self.custom_path.parent.mkdir(parents=True, exist_ok=True)
        with self.custom_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    # ---------- Fetch from web --------------------------------------------
    def fetch_from_web(self, url: Optional[str] = None) -> tuple[bool, str, int]:
        """Download a JSON strategy list and merge it into the imported
        bucket. Returns ``(success, message, n_added)``.

        Designed to be safe: on any failure (DNS, 404, bad JSON, schema
        mismatch) we leave the existing imported store untouched and
        return a clear error message for the UI.
        """
        target = (url or self.fetch_url).strip()
        if not target:
            return False, "No fetch URL configured.", 0
        try:
            import urllib.request, urllib.error  # stdlib
            req = urllib.request.Request(
                target,
                headers={"User-Agent": "USStockScreener-StrategyVault/1.0"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return False, f"Network error: {type(exc).__name__}: {exc}", 0
        try:
            data = json.loads(payload)
        except Exception as exc:
            return False, f"Response is not valid JSON: {exc}", 0
        items = data.get("strategies") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return False, "Response does not contain a 'strategies' list.", 0
        # Validate & normalise each item
        try:
            from strategy_dsl import validate as dsl_validate
        except Exception:
            dsl_validate = lambda _: None  # type: ignore
        accepted: list[dict] = []
        rejected = 0
        for raw in items:
            if not isinstance(raw, dict) or "name" not in raw:
                rejected += 1; continue
            buy = (raw.get("buy") or "").strip()
            sell = (raw.get("sell") or "").strip()
            be = dsl_validate(buy) if buy else None
            se = dsl_validate(sell) if sell else None
            if be or se:
                rejected += 1; continue
            entry = {
                "id": raw.get("id") or _safe_id(raw["name"]),
                "name": raw["name"].strip(),
                "category": (raw.get("category") or "Uncategorised").strip(),
                "source": "imported",
                "author": (raw.get("author") or "").strip(),
                "tags": list(raw.get("tags", [])),
                "description": (raw.get("description") or "").strip(),
                "buy": buy,
                "sell": sell,
                "created_at": raw.get("created_at") or _iso_now(),
                "updated_at": _iso_now(),
                "fetched_from": target,
            }
            accepted.append(entry)
        if not accepted:
            return False, f"No valid strategies in response (rejected {rejected}).", 0
        # Merge: replace any existing imports with same id
        index = {s.get("id"): i for i, s in enumerate(self._imported)}
        for s in accepted:
            if s["id"] in index:
                self._imported[index[s["id"]]] = s
            else:
                self._imported.append(s)
        self._write_imported(target)
        msg = (
            f"Fetched {len(accepted)} strategies from {target}"
            + (f" ({rejected} rejected)" if rejected else "")
        )
        return True, msg, len(accepted)

    def _write_imported(self, source_url: str) -> None:
        payload = {
            "version": 1,
            "fetched_at": _iso_now(),
            "fetched_from": source_url,
            "strategies": self._imported,
        }
        self.imported_path.parent.mkdir(parents=True, exist_ok=True)
        with self.imported_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    # ---------- Compiled strategy lookup ----------------------------------
    def as_custom_strategies(self) -> dict[str, "CustomStrategy"]:  # type: ignore[name-defined]
        """Compile every strategy in the vault into a CustomStrategy.
        Returns ``{display_name: CustomStrategy}``. Skips entries whose
        BUY or SELL expression fails to parse (logged via a print).
        """
        from strategy_dsl import CustomStrategy, validate
        out: dict[str, "CustomStrategy"] = {}
        for s in self.all():
            buy_err = validate(s.get("buy", ""))
            sell_err = validate(s.get("sell", ""))
            if buy_err or sell_err:
                continue
            label_src = {"popular": "★", "imported": "↻", "custom": "✎"}.get(
                s.get("source", "custom"), "?"
            )
            label = f"{label_src}  {s['name']}"
            try:
                out[label] = CustomStrategy(
                    name=s["name"],
                    buy_expr=s.get("buy", ""),
                    sell_expr=s.get("sell", ""),
                    description=s.get("description", ""),
                )
            except Exception:
                continue
        return out
