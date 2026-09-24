"""Plain data types shared by the broker, data, risk, execution and accounting layers.

Money and quantities use Decimal on the live path so that sizing, fee rounding and
P&L are exact and reproducible. The backtester works in floats for speed.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any

D0 = Decimal("0")
CENT = Decimal("0.01")


def D(x: Any) -> Decimal:
    """Convert to Decimal without float artefacts (str() first)."""
    if isinstance(x, Decimal):
        return x
    if x is None:
        return D0
    return Decimal(str(x))


def round_down(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def round_up(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_UP) * step


class Mode(str, enum.Enum):
    READ_ONLY = "read_only"   # monitor only: no orders of any kind are sent
    PAPER = "paper"           # autonomous decisions against a simulator or broker paper account
    LIVE = "live"             # real orders; requires explicit authorisation


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, enum.Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class Purpose(str, enum.Enum):
    ENTRY = "entry"
    EXIT = "exit"
    PROTECTIVE_STOP = "protective_stop"


class OrderStatus(str, enum.Enum):
    PENDING_SUBMIT = "pending_submit"   # written locally, request not yet confirmed sent
    UNKNOWN = "unknown"                 # request sent, outcome unknown (timeout) -> must reconcile
    NOT_SENT = "not_sent"               # confirmed the broker never received it
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)

    @property
    def is_unresolved(self) -> bool:
        return self in (OrderStatus.PENDING_SUBMIT, OrderStatus.UNKNOWN)

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED,
                        OrderStatus.EXPIRED, OrderStatus.NOT_SENT)


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    ts: datetime                 # exchange/provider timestamp of the quote
    received_at: datetime        # when we received it
    source: str                  # e.g. "alpaca", "sim", "replay:sp500-1999-2018"
    feed: str = ""               # e.g. "iex", "sip"
    delayed: bool = False        # provider marks data as delayed
    bid_size: Decimal = D0
    ask_size: Decimal = D0

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> Decimal:
        if self.mid <= 0:
            return Decimal("Infinity")
        return (self.ask - self.bid) / self.mid * Decimal(10000)

    def age_seconds(self, now: datetime) -> float:
        return (now - self.ts).total_seconds()

    @property
    def valid(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid


@dataclass(frozen=True)
class Bar:
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = ""


@dataclass(frozen=True)
class Asset:
    symbol: str
    tradable: bool
    fractionable: bool
    currency: str = "USD"
    min_notional: Decimal = Decimal("1")
    qty_increment: Decimal = Decimal("0.000000001")
    broker_symbol: str = ""       # e.g. Trading 212 "SPY_US_EQ"


@dataclass(frozen=True)
class Account:
    currency: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    trading_blocked: bool = False
    status: str = "ACTIVE"


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: Decimal
    avg_entry_price: Decimal
    market_price: Decimal
    currency: str = "USD"

    @property
    def market_value(self) -> Decimal:
        return self.qty * self.market_price


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    type: OrderType
    qty: Decimal | None = None
    notional: Decimal | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    tif: str = "day"


@dataclass
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: Side
    type: OrderType
    status: OrderStatus
    qty: Decimal | None
    notional: Decimal | None
    filled_qty: Decimal = D0
    filled_avg_price: Decimal | None = None
    fees: Decimal = D0
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    reject_reason: str = ""


@dataclass(frozen=True)
class CashFlow:
    flow_id: str
    ts: datetime
    kind: str          # deposit | withdrawal | dividend | fee | interest
    amount: Decimal    # signed, account currency: deposits +, withdrawals -
    currency: str


@dataclass(frozen=True)
class MarketClock:
    is_open: bool
    next_open: datetime
    next_close: datetime
    session_open: datetime | None = None    # today's session if one exists
    session_close: datetime | None = None


@dataclass(frozen=True)
class FxRate:
    base: str
    quote: str
    rate: Decimal          # 1 base = rate quote
    ts: datetime
    source: str

    def age_hours(self, now: datetime) -> float:
        return (now - self.ts).total_seconds() / 3600


@dataclass(frozen=True)
class NewsItem:
    news_id: str
    headline: str
    summary: str
    source: str
    symbols: tuple[str, ...]
    created_at: datetime        # publication time per provider
    updated_at: datetime
    url: str = ""


@dataclass
class Signal:
    """A strategy's proposal. It is never an order: the risk engine decides."""
    strategy: str
    strategy_version: str
    symbol: str
    action: str                      # "enter" | "exit"
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    stop_pct: float | None = None    # planned stop distance below entry (fraction)
    max_hold_days: int | None = None
    expected_edge_bps: float | None = None  # validated OOS mean net trade return, if any
    modelled_cost_bps: float | None = None  # round-trip spread+slippage assumed in that validation


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}
