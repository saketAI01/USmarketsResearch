"""
Strategy DSL — tokenizer, parser, evaluator.

A small, robust expression language designed for trading rules. Supports:

  * Identifiers (case-insensitive)        ROE, RSI14, LTP, EMA9, %CHG, …
  * Numeric literals                      15, 1.01, -0.5
  * Arithmetic                            + - * /  (with precedence)
  * Comparisons                           > < >= <= = != == (= and == both mean equality)
  * Crossover operators                   CROSSOVER  CROSSBELOW
                                          (also "crosses over" / "crosses below" sugar)
  * Logical                               AND  OR  NOT
  * Parentheses                           (RSI14 > 55 OR RSI14 CROSSOVER 50) AND %CHG > 2
  * Comments                              # everything to end-of-line
  * Optional prefix sugar                 BUY IF …  /  SELL IF …  /  WHEN …
  * Multi-word indicator names            AVG VOLUME → AVG_VOLUME, etc.

Grammar
-------
    expr        := or_expr
    or_expr     := and_expr ("OR" and_expr)*
    and_expr    := not_expr ("AND" not_expr)*
    not_expr    := "NOT" not_expr | cmp_expr
    cmp_expr    := arith (CMP arith)*       (CMP = > < >= <= = == != CROSSOVER CROSSBELOW)
    arith       := term (("+" | "-") term)*
    term        := factor (("*" | "/") factor)*
    factor      := "-" factor | NUMBER | IDENT | "(" expr ")"

Identifiers available at evaluation time
----------------------------------------
Price:        LTP CLOSE OPEN HIGH LOW VOLUME
Trend MAs:    SMA10 SMA20 SMA50 SMA200 EMA9 EMA12 EMA26
Momentum:    RSI RSI14 MACD SIGNAL HIST
Bands & vol: BB_UP BB_DN BB_MID ATR
Derived:     %CHG %CHG_5D %CHG_20D AVG_VOLUME HIGH20 LOW20 HIGH20_PREV
             LOW20_PREV HIGH52W LOW52W
Fundamentals (point-in-time snapshot, *not* backtest-correct):
             ROE ROA PE FORWARD_PE PEG MARKET_CAP REVENUE_GROWTH
             EARNINGS_GROWTH PROFIT_MARGIN OPERATING_MARGIN
             DEBT_TO_EQUITY DIVIDEND_YIELD BETA TARGET_PRICE FREE_CASHFLOW

All fundamental percentages are exposed as plain numbers (15 means 15%,
not 0.15) so that ROE > 15 reads naturally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Union

try:
    import numpy as np  # type: ignore
    import pandas as pd  # type: ignore
    PANDAS_AVAILABLE = True
except Exception:  # pragma: no cover
    PANDAS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Multi-word identifier / operator synonyms — normalised before tokenising
# ---------------------------------------------------------------------------
SYNONYMS: dict[str, str] = {
    # Multi-word indicators
    "AVG VOLUME": "AVG_VOLUME",
    "AVERAGE VOLUME": "AVG_VOLUME",
    "TARGET PRICE": "TARGET_PRICE",
    "FORWARD PE": "FORWARD_PE",
    "FORWARD P/E": "FORWARD_PE",
    "TRAILING PE": "PE",
    "TRAILING P/E": "PE",
    "FREE CASHFLOW": "FREE_CASHFLOW",
    "FREE CASH FLOW": "FREE_CASHFLOW",
    "MARKET CAP": "MARKET_CAP",
    "MARKET CAPITALIZATION": "MARKET_CAP",
    "PROFIT MARGIN": "PROFIT_MARGIN",
    "PROFIT MARGINS": "PROFIT_MARGIN",
    "REVENUE GROWTH": "REVENUE_GROWTH",
    "EARNINGS GROWTH": "EARNINGS_GROWTH",
    "OPERATING MARGIN": "OPERATING_MARGIN",
    "OPERATING MARGINS": "OPERATING_MARGIN",
    "DEBT TO EQUITY": "DEBT_TO_EQUITY",
    "DIVIDEND YIELD": "DIVIDEND_YIELD",
    # Multi-word crossover keywords
    "CROSSES OVER": "CROSSOVER",
    "CROSSES ABOVE": "CROSSOVER",
    "CROSS OVER": "CROSSOVER",
    "CROSSES BELOW": "CROSSBELOW",
    "CROSSES UNDER": "CROSSBELOW",
    "CROSS BELOW": "CROSSBELOW",
    "CROSS UNDER": "CROSSBELOW",
}

PREFIX_KEYWORDS = ("BUY IF ", "SELL IF ", "ENTRY IF ", "EXIT IF ", "WHEN ", "IF ")

KEYWORDS = {"AND", "OR", "NOT", "CROSSOVER", "CROSSBELOW"}


def preprocess(text: str) -> str:
    """Strip prefix sugar and substitute multi-word synonyms."""
    if not text:
        return ""
    s = text.strip()
    upper = s.upper()
    # Strip leading 'BUY IF', 'SELL IF', etc.
    for prefix in PREFIX_KEYWORDS:
        if upper.startswith(prefix):
            s = s[len(prefix):].strip()
            upper = s.upper()
            break
    # Trailing ';' (used to separate buy/sell when pasted as one line)
    s = s.rstrip(";").strip()
    # Multi-word synonyms (case-insensitive, whole-word match)
    for raw, canon in SYNONYMS.items():
        pattern = re.compile(r"\b" + re.escape(raw) + r"\b", re.IGNORECASE)
        s = pattern.sub(canon, s)
    return s


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
T_NUMBER = "NUMBER"
T_IDENT = "IDENT"
T_OP = "OP"
T_KEYWORD = "KEYWORD"
T_LPAREN = "LPAREN"
T_RPAREN = "RPAREN"
T_EOF = "EOF"


@dataclass
class Token:
    kind: str
    value: Any
    pos: int = 0


class LexError(ValueError):
    """Lexer / parser error with a helpful position hint."""


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------
class Lexer:
    """Two-pass: synonyms first (preprocess), then character-by-character scan."""

    TWO_CHAR_OPS = (">=", "<=", "!=", "==")
    ONE_CHAR_OPS = set("<>=+-*/")

    def __init__(self, text: str) -> None:
        self.text = preprocess(text)
        self.pos = 0

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        n = len(self.text)
        while self.pos < n:
            c = self.text[self.pos]
            if c.isspace():
                self.pos += 1
                continue
            if c == "#":
                while self.pos < n and self.text[self.pos] != "\n":
                    self.pos += 1
                continue
            if c.isdigit() or (c == "." and self.pos + 1 < n and self.text[self.pos + 1].isdigit()):
                tokens.append(self._read_number()); continue
            if c == "%":
                tokens.append(self._read_percent_ident()); continue
            if c.isalpha() or c == "_":
                tokens.append(self._read_ident_or_keyword()); continue
            two = self.text[self.pos:self.pos + 2]
            if two in self.TWO_CHAR_OPS:
                # normalise == to =
                value = "=" if two == "==" else two
                tokens.append(Token(T_OP, value, self.pos)); self.pos += 2; continue
            if c in self.ONE_CHAR_OPS:
                tokens.append(Token(T_OP, c, self.pos)); self.pos += 1; continue
            if c == "(":
                tokens.append(Token(T_LPAREN, c, self.pos)); self.pos += 1; continue
            if c == ")":
                tokens.append(Token(T_RPAREN, c, self.pos)); self.pos += 1; continue
            raise LexError(
                f"Unexpected character {c!r} at position {self.pos}: "
                f"'{self.text[max(0, self.pos - 5):self.pos + 6]}'"
            )
        tokens.append(Token(T_EOF, None, self.pos))
        return tokens

    def _read_number(self) -> Token:
        start = self.pos
        n = len(self.text)
        dot_seen = False
        while self.pos < n:
            ch = self.text[self.pos]
            if ch.isdigit():
                self.pos += 1
            elif ch == "." and not dot_seen:
                dot_seen = True
                self.pos += 1
            else:
                break
        return Token(T_NUMBER, float(self.text[start:self.pos]), start)

    def _read_percent_ident(self) -> Token:
        start = self.pos
        self.pos += 1  # skip %
        n = len(self.text)
        while self.pos < n and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self.pos += 1
        name = "%" + self.text[start + 1:self.pos].upper()
        return Token(T_IDENT, name, start)

    def _read_ident_or_keyword(self) -> Token:
        start = self.pos
        n = len(self.text)
        while self.pos < n and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self.pos += 1
        text = self.text[start:self.pos].upper()
        if text in KEYWORDS:
            return Token(T_KEYWORD, text, start)
        return Token(T_IDENT, text, start)


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------
@dataclass
class NumberNode:
    value: float


@dataclass
class IdentNode:
    name: str


@dataclass
class UnaryOpNode:
    op: str
    operand: Any


@dataclass
class BinaryOpNode:
    op: str
    left: Any
    right: Any


@dataclass
class LogicalOpNode:
    op: str  # AND | OR
    left: Any
    right: Any


@dataclass
class CrossoverNode:
    kind: str  # CROSSOVER | CROSSBELOW
    left: Any
    right: Any


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------
CMP_OPS = {">", "<", ">=", "<=", "=", "!="}


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0

    def _peek(self) -> Token:
        return self.tokens[self.i]

    def _advance(self) -> Token:
        t = self.tokens[self.i]; self.i += 1; return t

    def _expect(self, kind: str) -> Token:
        t = self._peek()
        if t.kind != kind:
            raise LexError(f"Expected {kind} but got {t.kind} ({t.value!r}) at pos {t.pos}")
        return self._advance()

    def parse(self) -> Any:
        node = self._or()
        if self._peek().kind != T_EOF:
            t = self._peek()
            raise LexError(f"Unexpected trailing token {t.value!r} at pos {t.pos}")
        return node

    def _or(self) -> Any:
        left = self._and()
        while self._peek().kind == T_KEYWORD and self._peek().value == "OR":
            self._advance()
            right = self._and()
            left = LogicalOpNode("OR", left, right)
        return left

    def _and(self) -> Any:
        left = self._not()
        while self._peek().kind == T_KEYWORD and self._peek().value == "AND":
            self._advance()
            right = self._not()
            left = LogicalOpNode("AND", left, right)
        return left

    def _not(self) -> Any:
        if self._peek().kind == T_KEYWORD and self._peek().value == "NOT":
            self._advance()
            return UnaryOpNode("NOT", self._not())
        return self._cmp()

    def _cmp(self) -> Any:
        left = self._arith()
        while True:
            tok = self._peek()
            if tok.kind == T_OP and tok.value in CMP_OPS:
                op = self._advance().value
                right = self._arith()
                left = BinaryOpNode(op, left, right)
            elif tok.kind == T_KEYWORD and tok.value in ("CROSSOVER", "CROSSBELOW"):
                kind = self._advance().value
                right = self._arith()
                left = CrossoverNode(kind, left, right)
            else:
                break
        return left

    def _arith(self) -> Any:
        left = self._term()
        while self._peek().kind == T_OP and self._peek().value in ("+", "-"):
            op = self._advance().value
            right = self._term()
            left = BinaryOpNode(op, left, right)
        return left

    def _term(self) -> Any:
        left = self._factor()
        while self._peek().kind == T_OP and self._peek().value in ("*", "/"):
            op = self._advance().value
            right = self._factor()
            left = BinaryOpNode(op, left, right)
        return left

    def _factor(self) -> Any:
        tok = self._peek()
        if tok.kind == T_OP and tok.value == "-":
            self._advance()
            return UnaryOpNode("-", self._factor())
        if tok.kind == T_OP and tok.value == "+":
            self._advance()
            return self._factor()
        if tok.kind == T_NUMBER:
            self._advance(); return NumberNode(tok.value)
        if tok.kind == T_IDENT:
            self._advance(); return IdentNode(tok.value)
        if tok.kind == T_LPAREN:
            self._advance()
            node = self._or()
            self._expect(T_RPAREN)
            return node
        raise LexError(f"Unexpected token {tok.kind} ({tok.value!r}) at pos {tok.pos}")


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------
def _truthy(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    try:
        return bool(v)
    except Exception:
        return False


def _arith(op: str, a: float, b: float) -> Optional[float]:
    try:
        if op == "+": return a + b
        if op == "-": return a - b
        if op == "*": return a * b
        if op == "/":
            return a / b if b != 0 else None
    except Exception:
        return None
    return None


def _cmp(op: str, a: float, b: float) -> bool:
    if op == ">":  return a > b
    if op == "<":  return a < b
    if op == ">=": return a >= b
    if op == "<=": return a <= b
    if op == "=":  return abs(a - b) <= max(1e-9, 1e-9 * max(abs(a), abs(b)))
    if op == "!=": return abs(a - b) > max(1e-9, 1e-9 * max(abs(a), abs(b)))
    return False


def evaluate(node: Any, ctx_now: dict, ctx_prev: Optional[dict] = None) -> Any:
    if isinstance(node, NumberNode):
        return node.value
    if isinstance(node, IdentNode):
        v = ctx_now.get(node.name)
        if v is None:
            return None
        try:
            f = float(v)
            return None if f != f else f  # filter NaN
        except (TypeError, ValueError):
            return None
    if isinstance(node, LogicalOpNode):
        left = evaluate(node.left, ctx_now, ctx_prev)
        if node.op == "AND":
            if not _truthy(left):
                return False
            return _truthy(evaluate(node.right, ctx_now, ctx_prev))
        # OR
        if _truthy(left):
            return True
        return _truthy(evaluate(node.right, ctx_now, ctx_prev))
    if isinstance(node, UnaryOpNode):
        v = evaluate(node.operand, ctx_now, ctx_prev)
        if node.op == "NOT":
            return not _truthy(v)
        if node.op == "-":
            return -v if isinstance(v, (int, float)) else None
        return v
    if isinstance(node, BinaryOpNode):
        a = evaluate(node.left, ctx_now, ctx_prev)
        b = evaluate(node.right, ctx_now, ctx_prev)
        if a is None or b is None:
            # arithmetic returns None; comparison fails False
            if node.op in ("+", "-", "*", "/"):
                return None
            return False
        if node.op in ("+", "-", "*", "/"):
            return _arith(node.op, float(a), float(b))
        return _cmp(node.op, float(a), float(b))
    if isinstance(node, CrossoverNode):
        if ctx_prev is None:
            return False
        l_now = evaluate(node.left, ctx_now, ctx_prev)
        r_now = evaluate(node.right, ctx_now, ctx_prev)
        l_prev = evaluate(node.left, ctx_prev, None)
        r_prev = evaluate(node.right, ctx_prev, None)
        if any(x is None for x in (l_now, r_now, l_prev, r_prev)):
            return False
        if node.kind == "CROSSOVER":
            return l_prev <= r_prev and l_now > r_now
        return l_prev >= r_prev and l_now < r_now
    return False


# ---------------------------------------------------------------------------
# Convenience: parse + evaluate
# ---------------------------------------------------------------------------
def parse(expr: str) -> Any:
    """Tokenize + parse the expression. Returns an AST root, or raises LexError."""
    if not expr or not expr.strip():
        return None
    tokens = Lexer(expr).tokenize()
    return Parser(tokens).parse()


def validate(expr: str) -> Optional[str]:
    """Return None if the expression parses cleanly, or an error message string."""
    try:
        parse(expr)
    except LexError as exc:
        return str(exc)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------
def _safe(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _pct(v: Any) -> Optional[float]:
    """Convert a fractional fundamental (0.15) to percent (15)."""
    s = _safe(v)
    return s * 100.0 if s is not None else None


def build_context(df, i: int, info: Optional[dict] = None) -> dict[str, Any]:
    """Build the evaluation context for bar i of a price+indicator DataFrame.

    The DataFrame is expected to have at minimum: Open / High / Low / Close /
    Volume plus the indicators added by ``backtest_engine.compute_indicators``
    (SMA20/50/200, EMA12/26, MACD, Signal, Hist, RSI, BB_*, ATR).
    ``info`` is yfinance-style fundamentals dict (point-in-time snapshot).
    """
    ctx: dict[str, Any] = {}
    if df is None or i < 0 or i >= len(df):
        return ctx
    row = df.iloc[i]
    prev = df.iloc[i - 1] if i > 0 else None

    # ---- Price ----
    close = _safe(row.get("Close"))
    ctx["LTP"] = ctx["CLOSE"] = close
    ctx["OPEN"] = _safe(row.get("Open"))
    ctx["HIGH"] = _safe(row.get("High"))
    ctx["LOW"] = _safe(row.get("Low"))
    ctx["VOLUME"] = _safe(row.get("Volume"))

    # ---- MAs ----
    for col in ("SMA10", "SMA20", "SMA50", "SMA200", "EMA12", "EMA26"):
        ctx[col] = _safe(row.get(col))

    # EMA9 is not in the standard pack; compute on the fly if missing
    if PANDAS_AVAILABLE and "EMA9" not in df.columns and i >= 1:
        try:
            ema9 = df["Close"].ewm(span=9, adjust=False).mean().iloc[i]
            ctx["EMA9"] = _safe(ema9)
        except Exception:
            ctx["EMA9"] = None
    else:
        ctx["EMA9"] = _safe(row.get("EMA9"))

    # ---- Momentum / bands / vol ----
    ctx["RSI"] = ctx["RSI14"] = _safe(row.get("RSI"))
    ctx["MACD"] = _safe(row.get("MACD"))
    ctx["SIGNAL"] = _safe(row.get("Signal"))
    ctx["HIST"] = _safe(row.get("Hist"))
    ctx["BB_UP"] = _safe(row.get("BB_Up"))
    ctx["BB_DN"] = _safe(row.get("BB_Dn"))
    ctx["BB_MID"] = _safe(row.get("BB_Mid"))
    ctx["ATR"] = _safe(row.get("ATR"))

    # ---- Derived: percent changes ----
    if prev is not None and close is not None:
        pc = _safe(prev.get("Close"))
        if pc and pc != 0:
            ctx["%CHG"] = (close / pc - 1.0) * 100.0
    if PANDAS_AVAILABLE and i >= 5:
        try:
            five_back = float(df["Close"].iloc[i - 5])
            if close is not None and five_back:
                ctx["%CHG_5D"] = (close / five_back - 1.0) * 100.0
        except Exception:
            pass
    if PANDAS_AVAILABLE and i >= 20:
        try:
            twenty_back = float(df["Close"].iloc[i - 20])
            if close is not None and twenty_back:
                ctx["%CHG_20D"] = (close / twenty_back - 1.0) * 100.0
        except Exception:
            pass

    # ---- Derived: rolling highs / lows / avg vol ----
    if PANDAS_AVAILABLE:
        try:
            if i >= 20:
                ctx["AVG_VOLUME"] = float(df["Volume"].iloc[i - 20:i].mean())
                ctx["HIGH20_PREV"] = float(df["High"].iloc[i - 20:i].max())
                ctx["LOW20_PREV"] = float(df["Low"].iloc[i - 20:i].min())
                ctx["HIGH20"] = float(df["High"].iloc[i - 19:i + 1].max())
                ctx["LOW20"] = float(df["Low"].iloc[i - 19:i + 1].min())
            if i >= 252:
                ctx["HIGH52W"] = float(df["High"].iloc[i - 252:i + 1].max())
                ctx["LOW52W"] = float(df["Low"].iloc[i - 252:i + 1].min())
        except Exception:
            pass

    # ---- Fundamentals (yfinance info dict) ----
    if info:
        ctx["ROE"] = _pct(info.get("returnOnEquity"))
        ctx["ROA"] = _pct(info.get("returnOnAssets"))
        ctx["PE"] = _safe(info.get("trailingPE"))
        ctx["FORWARD_PE"] = _safe(info.get("forwardPE"))
        ctx["PEG"] = _safe(info.get("pegRatio") or info.get("trailingPegRatio"))
        ctx["MARKET_CAP"] = _safe(info.get("marketCap"))
        ctx["REVENUE_GROWTH"] = _pct(info.get("revenueGrowth"))
        ctx["EARNINGS_GROWTH"] = _pct(info.get("earningsGrowth"))
        ctx["PROFIT_MARGIN"] = _pct(info.get("profitMargins"))
        ctx["OPERATING_MARGIN"] = _pct(info.get("operatingMargins"))
        de = info.get("debtToEquity")
        ctx["DEBT_TO_EQUITY"] = (_safe(de) / 100.0) if _safe(de) is not None else None
        ctx["DIVIDEND_YIELD"] = _pct(info.get("dividendYield"))
        ctx["BETA"] = _safe(info.get("beta"))
        ctx["TARGET_PRICE"] = _safe(info.get("targetMeanPrice"))
        ctx["FREE_CASHFLOW"] = _safe(info.get("freeCashflow"))
    return ctx


# ---------------------------------------------------------------------------
# Strategy: wraps a parsed BUY + SELL expression
# ---------------------------------------------------------------------------
@dataclass
class CustomStrategy:
    """A user-built rule strategy: BUY + SELL expressions in the DSL."""

    name: str
    buy_expr: str
    sell_expr: str
    description: str = ""
    _buy_ast: Any = None
    _sell_ast: Any = None
    _compiled: bool = False

    def __post_init__(self) -> None:
        self.compile()

    def compile(self) -> None:
        self._buy_ast = parse(self.buy_expr) if self.buy_expr and self.buy_expr.strip() else None
        self._sell_ast = parse(self.sell_expr) if self.sell_expr and self.sell_expr.strip() else None
        self._compiled = True

    def evaluate_buy(self, ctx_now: dict, ctx_prev: Optional[dict] = None) -> bool:
        if self._buy_ast is None:
            return False
        return _truthy(evaluate(self._buy_ast, ctx_now, ctx_prev))

    def evaluate_sell(self, ctx_now: dict, ctx_prev: Optional[dict] = None) -> bool:
        if self._sell_ast is None:
            return False
        return _truthy(evaluate(self._sell_ast, ctx_now, ctx_prev))


# ---------------------------------------------------------------------------
# Reference: all known identifiers, for the UI's helper panel
# ---------------------------------------------------------------------------
INDICATOR_REFERENCE: list[tuple[str, list[tuple[str, str]]]] = [
    ("Price & volume", [
        ("LTP / CLOSE", "Last traded price (today's close)"),
        ("OPEN", "Today's opening price"),
        ("HIGH / LOW", "Today's intraday high / low"),
        ("VOLUME", "Today's traded volume"),
        ("%CHG", "Today's percent change vs. previous close"),
        ("%CHG_5D / %CHG_20D", "Percent change over 5 / 20 trading days"),
        ("AVG_VOLUME", "20-day average volume (excluding today)"),
        ("HIGH20 / LOW20", "20-day high / low including today"),
        ("HIGH20_PREV / LOW20_PREV", "20-day high / low *excluding* today (for breakouts)"),
        ("HIGH52W / LOW52W", "52-week high / low"),
    ]),
    ("Moving averages", [
        ("SMA10 / SMA20 / SMA50 / SMA200", "Simple moving averages"),
        ("EMA9 / EMA12 / EMA26", "Exponential moving averages"),
    ]),
    ("Momentum & bands", [
        ("RSI / RSI14", "14-period Wilder RSI (0–100)"),
        ("MACD / SIGNAL / HIST", "MACD line / signal line / histogram"),
        ("BB_UP / BB_MID / BB_DN", "Upper / middle / lower Bollinger bands"),
        ("ATR", "14-period Average True Range"),
    ]),
    ("Fundamentals (snapshot)", [
        ("ROE", "Return on equity (percent)"),
        ("ROA", "Return on assets (percent)"),
        ("PE / FORWARD_PE", "Trailing / forward P/E"),
        ("PEG", "PEG ratio"),
        ("MARKET_CAP", "Market capitalisation ($)"),
        ("REVENUE_GROWTH / EARNINGS_GROWTH", "YoY growth (percent)"),
        ("PROFIT_MARGIN / OPERATING_MARGIN", "Margins (percent)"),
        ("DEBT_TO_EQUITY", "D/E ratio"),
        ("DIVIDEND_YIELD", "Dividend yield (percent)"),
        ("BETA", "5-year beta vs market"),
        ("TARGET_PRICE", "Analyst mean target price"),
        ("FREE_CASHFLOW", "Free cash flow ($)"),
    ]),
]

OPERATOR_REFERENCE: list[tuple[str, str]] = [
    ("AND  /  OR  /  NOT", "Boolean combiners"),
    (">  <  >=  <=  =  !=", "Numeric comparisons"),
    ("+  -  *  /", "Arithmetic"),
    ("CROSSOVER", "LHS was ≤ RHS yesterday and is > RHS today"),
    ("CROSSBELOW", "LHS was ≥ RHS yesterday and is < RHS today"),
    ("( … )", "Grouping for precedence"),
    ("BUY IF / SELL IF / WHEN", "Optional prefixes — stripped automatically"),
    ("#", "Line comment to end-of-line"),
]
