"""
Paper trading engine — simulated broker with persistent state.

Features
--------
* Persistent account (JSON file in the workspace folder).
* Cash, open positions, working orders, complete trade history.
* Order types: MARKET, LIMIT, STOP, STOP_LIMIT (long-only positions
  with the option to flat-out via SELL).
* Cost model identical to the backtester: configurable commission +
  slippage in bps.
* Live mark-to-market via yfinance.
* Realised & unrealised P&L; per-position cost basis; daily equity
  snapshot for the equity-curve chart.

The engine deliberately does *not* support shorting or margin. This
keeps the simulator honest for the retail use case it's targeted at.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import yfinance as yf  # type: ignore
    YFINANCE_AVAILABLE = True
except Exception:  # pragma: no cover
    YFINANCE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
DEFAULT_STATE_FILENAME = "paper_account.json"


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------
@dataclass
class Order:
    """A working or filled order."""

    order_id: int
    ticker: str
    side: str                    # 'BUY' or 'SELL'
    order_type: str              # 'MARKET' | 'LIMIT' | 'STOP' | 'STOP_LIMIT'
    quantity: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    status: str = "OPEN"         # 'OPEN' | 'FILLED' | 'CANCELED' | 'REJECTED'
    submitted_at: str = field(default_factory=_iso_now)
    filled_at: Optional[str] = None
    fill_price: Optional[float] = None
    commission_paid: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Order":
        return cls(**d)


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """A long position currently held in the paper account."""

    ticker: str
    quantity: int
    avg_cost: float
    opened_at: str = field(default_factory=_iso_now)
    last_price: float = 0.0
    last_marked_at: Optional[str] = None

    @property
    def cost_basis(self) -> float:
        return self.avg_cost * self.quantity

    @property
    def market_value(self) -> float:
        return self.last_price * self.quantity if self.last_price else self.cost_basis

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        return (self.unrealized_pnl / self.cost_basis * 100.0) if self.cost_basis else 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Position":
        return cls(**d)


# ---------------------------------------------------------------------------
# Closed trade record (for trade history)
# ---------------------------------------------------------------------------
@dataclass
class ClosedTrade:
    ticker: str
    quantity: int
    entry_price: float
    exit_price: float
    opened_at: str
    closed_at: str
    realized_pnl: float
    realized_pnl_pct: float
    commission_paid: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ClosedTrade":
        return cls(**d)


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
@dataclass
class Account:
    """The full paper-trading account state. Serialised to JSON."""

    starting_cash: float = 100_000.0
    cash: float = 100_000.0
    commission_per_trade: float = 0.0
    slippage_bps: float = 5.0
    positions: dict[str, Position] = field(default_factory=dict)
    open_orders: list[Order] = field(default_factory=list)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    equity_history: list[tuple[str, float]] = field(default_factory=list)
    next_order_id: int = 1
    created_at: str = field(default_factory=_iso_now)
    realized_pnl_total: float = 0.0

    # ---------- Equity / valuation -----------------------------------------
    def total_equity(self) -> float:
        equity = self.cash
        for p in self.positions.values():
            equity += p.market_value
        return equity

    def total_unrealized(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())

    def total_return_pct(self) -> float:
        if self.starting_cash <= 0:
            return 0.0
        return (self.total_equity() / self.starting_cash - 1.0) * 100.0

    # ---------- Serialisation ----------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "starting_cash": self.starting_cash,
            "cash": self.cash,
            "commission_per_trade": self.commission_per_trade,
            "slippage_bps": self.slippage_bps,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "open_orders": [o.to_dict() for o in self.open_orders],
            "closed_trades": [t.to_dict() for t in self.closed_trades],
            "equity_history": list(self.equity_history),
            "next_order_id": self.next_order_id,
            "created_at": self.created_at,
            "realized_pnl_total": self.realized_pnl_total,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Account":
        acct = cls(
            starting_cash=d.get("starting_cash", 100_000.0),
            cash=d.get("cash", d.get("starting_cash", 100_000.0)),
            commission_per_trade=d.get("commission_per_trade", 0.0),
            slippage_bps=d.get("slippage_bps", 5.0),
            next_order_id=d.get("next_order_id", 1),
            created_at=d.get("created_at", _iso_now()),
            realized_pnl_total=d.get("realized_pnl_total", 0.0),
        )
        acct.positions = {
            k: Position.from_dict(v) for k, v in d.get("positions", {}).items()
        }
        acct.open_orders = [Order.from_dict(o) for o in d.get("open_orders", [])]
        acct.closed_trades = [
            ClosedTrade.from_dict(t) for t in d.get("closed_trades", [])
        ]
        acct.equity_history = [tuple(x) for x in d.get("equity_history", [])]
        return acct


# ---------------------------------------------------------------------------
# Broker — the execution engine
# ---------------------------------------------------------------------------
class PaperBroker:
    """Simulates an execution venue: holds the Account, fills orders against
    a price feed, persists state to disk."""

    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)
        self.account = self._load()

    # ---------- Persistence -----------------------------------------------------
    def _load(self) -> Account:
        if self.state_path.exists():
            try:
                with self.state_path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return Account.from_dict(data)
            except Exception:
                pass
        return Account()

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("w", encoding="utf-8") as fh:
            json.dump(self.account.to_dict(), fh, indent=2)

    def reset(
        self,
        starting_cash: float,
        commission_per_trade: float,
        slippage_bps: float,
    ) -> None:
        """Wipe and re-create the account with new defaults."""
        self.account = Account(
            starting_cash=starting_cash,
            cash=starting_cash,
            commission_per_trade=commission_per_trade,
            slippage_bps=slippage_bps,
        )
        self.save()

    # ---------- Quotes ----------------------------------------------------------
    def latest_quote(self, ticker: str) -> Optional[float]:
        """Fetch the most recent close. Used both for marking and for
        immediate market-order fills."""
        if not YFINANCE_AVAILABLE:
            return None
        try:
            t = yf.Ticker(ticker.upper())
            # ``fast_info`` is the lightweight, low-latency yfinance path
            fi = getattr(t, "fast_info", None)
            if fi is not None:
                px = getattr(fi, "last_price", None)
                if px:
                    return float(px)
            hist = t.history(period="2d", auto_adjust=True)
            if hist is not None and not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            return None
        return None

    def quotes_batch(self, tickers: list[str]) -> dict[str, float]:
        """Best-effort batch quote. Falls back to per-ticker on failure."""
        out: dict[str, float] = {}
        if not YFINANCE_AVAILABLE or not tickers:
            return out
        try:
            data = yf.download(
                tickers, period="2d", auto_adjust=True, progress=False, threads=True,
            )
            if data is None or data.empty:
                raise RuntimeError("empty download")
            # yf returns nested columns for >1 ticker
            if len(tickers) == 1:
                px = float(data["Close"].iloc[-1])
                out[tickers[0].upper()] = px
            else:
                for t in tickers:
                    try:
                        px = float(data["Close"][t].iloc[-1])
                        if px == px:  # filter NaN
                            out[t.upper()] = px
                    except Exception:
                        pass
        except Exception:
            for t in tickers:
                px = self.latest_quote(t)
                if px is not None:
                    out[t.upper()] = px
        return out

    # ---------- Cost model -----------------------------------------------------
    def _buy_fill(self, price: float) -> float:
        return price * (1.0 + self.account.slippage_bps / 10_000.0)

    def _sell_fill(self, price: float) -> float:
        return price * (1.0 - self.account.slippage_bps / 10_000.0)

    # ---------- Order management -----------------------------------------------
    def place_order(
        self,
        ticker: str,
        side: str,
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        note: str = "",
    ) -> Order:
        """Submit a new order. Market orders attempt to fill immediately
        against the latest quote. Others go on the working-orders book
        until ``check_pending_fills()`` is called."""
        ticker = ticker.upper()
        side = side.upper()
        order_type = order_type.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        if order_type not in ("MARKET", "LIMIT", "STOP", "STOP_LIMIT"):
            raise ValueError(f"Unsupported order type {order_type}")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if order_type in ("LIMIT", "STOP_LIMIT") and limit_price is None:
            raise ValueError("limit_price required for LIMIT / STOP_LIMIT orders")
        if order_type in ("STOP", "STOP_LIMIT") and stop_price is None:
            raise ValueError("stop_price required for STOP / STOP_LIMIT orders")
        if side == "SELL":
            held = self.account.positions.get(ticker)
            if held is None or held.quantity < quantity:
                raise ValueError(
                    f"Cannot sell {quantity} of {ticker}: only "
                    f"{held.quantity if held else 0} held."
                )

        order = Order(
            order_id=self.account.next_order_id,
            ticker=ticker,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            stop_price=stop_price,
            note=note,
        )
        self.account.next_order_id += 1

        if order_type == "MARKET":
            self._try_fill_market(order)
        else:
            self.account.open_orders.append(order)

        self.save()
        return order

    def cancel_order(self, order_id: int) -> bool:
        for o in self.account.open_orders:
            if o.order_id == order_id and o.status == "OPEN":
                o.status = "CANCELED"
                self.account.open_orders = [
                    x for x in self.account.open_orders if x.order_id != order_id
                ]
                self.save()
                return True
        return False

    # ---------- Internal fill logic --------------------------------------------
    def _try_fill_market(self, order: Order) -> None:
        px = self.latest_quote(order.ticker)
        if px is None:
            order.status = "REJECTED"
            order.note = (order.note + " | no quote available").strip(" |")
            self.account.open_orders.append(order)
            return
        self._fill(order, px)

    def _try_fill_limit_or_stop(self, order: Order, current_px: float) -> bool:
        # Returns True if filled.
        if order.order_type == "LIMIT":
            if order.side == "BUY" and current_px <= (order.limit_price or 0):
                self._fill(order, min(current_px, order.limit_price))
                return True
            if order.side == "SELL" and current_px >= (order.limit_price or 0):
                self._fill(order, max(current_px, order.limit_price))
                return True
            return False
        if order.order_type == "STOP":
            # Stop becomes a market once breached
            if order.side == "BUY" and current_px >= (order.stop_price or 0):
                self._fill(order, current_px)
                return True
            if order.side == "SELL" and current_px <= (order.stop_price or 0):
                self._fill(order, current_px)
                return True
            return False
        if order.order_type == "STOP_LIMIT":
            # Stop becomes a limit once breached
            if order.side == "BUY" and current_px >= (order.stop_price or 0):
                if current_px <= (order.limit_price or 0):
                    self._fill(order, min(current_px, order.limit_price))
                    return True
            if order.side == "SELL" and current_px <= (order.stop_price or 0):
                if current_px >= (order.limit_price or 0):
                    self._fill(order, max(current_px, order.limit_price))
                    return True
            return False
        return False

    def _fill(self, order: Order, raw_price: float) -> None:
        side = order.side
        qty = order.quantity
        if side == "BUY":
            fill_px = self._buy_fill(raw_price)
            gross = fill_px * qty
            cost = gross + self.account.commission_per_trade
            if cost > self.account.cash:
                order.status = "REJECTED"
                order.note = (order.note + " | insufficient cash").strip(" |")
                self.account.open_orders.append(order)
                return
            self.account.cash -= cost
            pos = self.account.positions.get(order.ticker)
            if pos is None:
                self.account.positions[order.ticker] = Position(
                    ticker=order.ticker, quantity=qty, avg_cost=fill_px,
                    last_price=fill_px, last_marked_at=_iso_now(),
                )
            else:
                new_qty = pos.quantity + qty
                pos.avg_cost = (pos.avg_cost * pos.quantity + fill_px * qty) / new_qty
                pos.quantity = new_qty
                pos.last_price = fill_px
                pos.last_marked_at = _iso_now()
            order.fill_price = fill_px
            order.commission_paid = self.account.commission_per_trade
        else:  # SELL
            fill_px = self._sell_fill(raw_price)
            proceeds = fill_px * qty - self.account.commission_per_trade
            pos = self.account.positions.get(order.ticker)
            if pos is None or pos.quantity < qty:
                order.status = "REJECTED"
                order.note = (order.note + " | no position").strip(" |")
                self.account.open_orders.append(order)
                return
            # Realised P&L on the closed slice
            realized = (fill_px - pos.avg_cost) * qty - self.account.commission_per_trade
            realized_pct = (
                ((fill_px / pos.avg_cost) - 1.0) * 100.0 if pos.avg_cost > 0 else 0.0
            )
            self.account.realized_pnl_total += realized
            self.account.closed_trades.append(ClosedTrade(
                ticker=order.ticker,
                quantity=qty,
                entry_price=pos.avg_cost,
                exit_price=fill_px,
                opened_at=pos.opened_at,
                closed_at=_iso_now(),
                realized_pnl=realized,
                realized_pnl_pct=realized_pct,
                commission_paid=self.account.commission_per_trade,
            ))
            self.account.cash += proceeds
            pos.quantity -= qty
            if pos.quantity <= 0:
                self.account.positions.pop(order.ticker, None)
            order.fill_price = fill_px
            order.commission_paid = self.account.commission_per_trade
        order.status = "FILLED"
        order.filled_at = _iso_now()

    # ---------- Public update step ---------------------------------------------
    def check_pending_fills(self, prices: Optional[dict[str, float]] = None) -> int:
        """Walk the working-orders book against current quotes.
        Returns number of orders newly filled."""
        if prices is None:
            tickers = [o.ticker for o in self.account.open_orders if o.status == "OPEN"]
            prices = self.quotes_batch(list(set(tickers))) if tickers else {}
        filled = 0
        survivors: list[Order] = []
        for o in self.account.open_orders:
            if o.status != "OPEN":
                continue
            px = prices.get(o.ticker)
            if px is None:
                survivors.append(o)
                continue
            if self._try_fill_limit_or_stop(o, px):
                filled += 1
            else:
                survivors.append(o)
        self.account.open_orders = survivors
        if filled:
            self.save()
        return filled

    def mark_to_market(self, prices: Optional[dict[str, float]] = None) -> float:
        """Refresh ``last_price`` for every position and record the equity
        snapshot. Returns the new total equity."""
        if prices is None:
            tickers = list(self.account.positions.keys())
            prices = self.quotes_batch(tickers) if tickers else {}
        for tk, pos in self.account.positions.items():
            if tk in prices:
                pos.last_price = prices[tk]
                pos.last_marked_at = _iso_now()
        eq = self.account.total_equity()
        self.account.equity_history.append((_iso_now(), eq))
        # Keep the history reasonable
        if len(self.account.equity_history) > 5000:
            self.account.equity_history = self.account.equity_history[-5000:]
        self.save()
        return eq
