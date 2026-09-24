"""Trading 212 Public API (beta) adapter - alternative, FCA-regulated execution venue.

Implemented from docs.trading212.com (researched 2026-09-24 via search; the docs host
is blocked from this build environment, so field names follow the published v0
reference and are UNVERIFIED against the live service). Material limitations that the
engine accounts for through ``Capabilities``:

* Only Invest and Stocks ISA accounts; orders only in the account's primary currency.
* At the research date the docs stated that the *live* environment supports market
  orders only, so exits on a live account are software-managed (no broker-held stop).
* No client order id: after a timeout the adapter can only match orders by
  ticker/quantity/time, so it is conservative and the engine waits before resubmitting.
* No market-data endpoint: a separate data provider (e.g. Alpaca IEX) is required and
  its tickers are mapped to Trading 212 instrument codes (``SPY`` -> ``SPY_US_EQ``).
* 0.15% FX fee on trades in instruments not denominated in the account currency.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from ..config import BrokerCredentials
from ..models import (D0, Account, Asset, BrokerOrder, CashFlow, D, MarketClock, OrderRequest,
                      OrderStatus, OrderType, Position, Side)
from .base import (Broker, BrokerAuthError, BrokerRateLimited, BrokerRejected, BrokerTimeout,
                   BrokerUnavailable, Capabilities)

_STATUS = {
    "LOCAL": OrderStatus.ACCEPTED, "UNCONFIRMED": OrderStatus.ACCEPTED, "CONFIRMED": OrderStatus.ACCEPTED,
    "NEW": OrderStatus.ACCEPTED, "CANCELLING": OrderStatus.ACCEPTED, "REPLACING": OrderStatus.ACCEPTED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED, "FILLED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELED, "REPLACED": OrderStatus.CANCELED, "REJECTED": OrderStatus.REJECTED,
}


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


class Trading212Broker(Broker):
    def __init__(self, creds: BrokerCredentials, demo: bool, account_currency: str = "GBP",
                 timeout: float = 10.0, transport: httpx.BaseTransport | None = None,
                 live_order_types: tuple[str, ...] = ("market",), ticker_map: dict[str, str] | None = None):
        types = ("market", "limit", "stop") if demo else live_order_types
        self.capabilities = Capabilities(
            name="trading212", is_paper=demo, account_currency=account_currency,
            supports_fractional=True, supports_notional_orders=False, supports_client_order_id=False,
            protective_stop_for_fractional="stop" in types, protective_stop_tif="gtc",
            order_types=types, fx_fee_bps=15.0,
            notes=("No client order ids: post-timeout reconciliation matches ticker/quantity/time.",
                   "No market-data API: quotes come from a separate provider.",
                   "Live API documented as market-orders-only at research date."))
        token = base64.b64encode(
            f"{creds.key_id.get_secret_value()}:{creds.secret.get_secret_value()}".encode()).decode()
        self._http = httpx.Client(
            base_url=f"https://{'demo' if demo else 'live'}.trading212.com/api/v0", timeout=timeout,
            transport=transport, headers={"Authorization": f"Basic {token}"})
        self.ticker_map = ticker_map or {}
        self._reverse: dict[str, str] = {}

    def _t212(self, symbol: str) -> str:
        t = self.ticker_map.get(symbol, f"{symbol}_US_EQ")
        self._reverse[t] = symbol
        return t

    def _sym(self, ticker: str) -> str:
        return self._reverse.get(ticker, ticker.split("_")[0])

    def _req(self, method: str, path: str, *, submit: bool = False, **kw) -> Any:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.TimeoutException as e:
            if submit:
                raise BrokerTimeout(f"{path} timed out; outcome unknown") from e
            raise BrokerUnavailable(f"{path} timed out") from e
        except httpx.TransportError as e:
            if submit and not isinstance(e, httpx.ConnectError):
                raise BrokerTimeout(f"{path}: {type(e).__name__}; outcome unknown") from e
            raise BrokerUnavailable(f"{path}: {type(e).__name__}") from e
        if r.status_code in (401, 403):
            raise BrokerAuthError(f"Trading 212 auth/permission error {r.status_code} "
                                  "(check key scopes and IP allow-list)")
        if r.status_code == 429:
            reset = r.headers.get("x-ratelimit-reset")
            raise BrokerRateLimited("Trading 212 rate limit", float(reset) if reset and reset.isdigit() else 5.0)
        if r.status_code >= 500:
            if submit:
                raise BrokerTimeout(f"Trading 212 {r.status_code} on submit; outcome unknown")
            raise BrokerUnavailable(f"Trading 212 {r.status_code}")
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise BrokerRejected(f"Trading 212 {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def _order(self, o: dict[str, Any], cid: str = "") -> BrokerOrder:
        qty = D(o.get("quantity") or 0)
        side = Side.SELL if qty < 0 else Side.BUY
        otype = {"MARKET": OrderType.MARKET, "LIMIT": OrderType.LIMIT, "STOP": OrderType.STOP}.get(
            o.get("type", "MARKET"), OrderType.MARKET)
        filled = abs(D(o.get("filledQuantity") or 0))
        value = D(o.get("filledValue") or 0)
        avg = D(o["fillPrice"]) if o.get("fillPrice") else ((abs(value) / filled) if filled and value else None)
        return BrokerOrder(broker_order_id=str(o["id"]), client_order_id=cid, symbol=self._sym(o["ticker"]),
                           side=side, type=otype, status=_STATUS.get(o.get("status", ""), OrderStatus.ACCEPTED),
                           qty=abs(qty), notional=None, filled_qty=filled, filled_avg_price=avg,
                           limit_price=D(o["limitPrice"]) if o.get("limitPrice") else None,
                           stop_price=D(o["stopPrice"]) if o.get("stopPrice") else None,
                           submitted_at=_ts(o.get("creationTime") or o.get("dateCreated")))

    def get_account(self) -> Account:
        c = self._req("GET", "/equity/account/cash")
        free = D(c.get("free", 0))
        return Account(currency=self.capabilities.account_currency, cash=free, equity=D(c.get("total", 0)),
                       buying_power=free)

    def get_positions(self) -> list[Position]:
        return [Position(self._sym(p["ticker"]), D(p["quantity"]), D(p["averagePrice"]),
                         D(p["currentPrice"]), "USD" if p["ticker"].endswith("_US_EQ") else "GBP")
                for p in self._req("GET", "/equity/portfolio") or []]

    def list_open_orders(self) -> list[BrokerOrder]:
        return [self._order(o) for o in self._req("GET", "/equity/orders") or []]

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        o = self._req("GET", f"/equity/orders/{broker_order_id}")
        if o is not None:
            return self._order(o)
        hist = self._req("GET", "/equity/history/orders", params={"limit": 50}) or {}
        for h in hist.get("items", []):
            if str(h.get("id")) == str(broker_order_id):
                return self._order(h)
        raise BrokerRejected(f"order {broker_order_id} not found")

    def find_order_by_client_id(self, client_order_id, hint=None, since=None):
        if hint is None:
            return None
        ticker = self._t212(hint.symbol)
        want = hint.qty if hint.side == Side.BUY else -(hint.qty or D0)
        since = since or datetime.min
        candidates = list(self._req("GET", "/equity/orders") or [])
        hist = self._req("GET", "/equity/history/orders", params={"ticker": ticker, "limit": 50}) or {}
        candidates += hist.get("items", [])
        for o in candidates:
            created = _ts(o.get("creationTime") or o.get("dateCreated"))
            if (o.get("ticker") == ticker and D(o.get("quantity") or 0) == want
                    and (created is None or created >= since - timedelta(seconds=5))):
                return self._order(o, cid=client_order_id)
        return None

    def submit_order(self, req: OrderRequest) -> BrokerOrder:
        if req.type.value not in self.capabilities.order_types:
            raise BrokerRejected(f"{req.type.value} orders are not available on this Trading 212 environment")
        if req.qty is None:
            raise BrokerRejected("Trading 212 requires a share quantity (no notional orders)")
        qty = req.qty if req.side == Side.BUY else -req.qty
        body: dict[str, Any] = {"ticker": self._t212(req.symbol), "quantity": float(qty)}
        path = "/equity/orders/market"
        if req.type == OrderType.STOP:
            path, body["stopPrice"] = "/equity/orders/stop", float(req.stop_price)
            body["timeValidity"] = "GOOD_TILL_CANCEL"
        elif req.type == OrderType.LIMIT:
            path, body["limitPrice"] = "/equity/orders/limit", float(req.limit_price)
            body["timeValidity"] = "DAY"
        else:
            body["extendedHours"] = False
        return self._order(self._req("POST", path, json=body, submit=True), cid=req.client_order_id)

    def cancel_order(self, broker_order_id: str) -> None:
        self._req("DELETE", f"/equity/orders/{broker_order_id}")

    def get_asset(self, symbol: str) -> Asset:
        ticker = self._t212(symbol)
        for i in self._req("GET", "/equity/metadata/instruments") or []:
            if i.get("ticker") == ticker:
                return Asset(symbol, tradable=True, fractionable=True, currency=i.get("currencyCode", "USD"),
                             min_notional=Decimal("1"), broker_symbol=ticker,
                             qty_increment=Decimal("0.00000001"))
        return Asset(symbol, tradable=False, fractionable=False, broker_symbol=ticker)

    def get_clock(self) -> MarketClock | None:
        return None      # use the exchange calendar

    def get_cash_flows(self, since: datetime) -> list[CashFlow]:
        data = self._req("GET", "/history/transactions", params={"limit": 50}) or {}
        out = []
        for t in data.get("items", []):
            ts = _ts(t.get("dateTime"))
            kind = {"DEPOSIT": "deposit", "WITHDRAW": "withdrawal"}.get(t.get("type"))
            if kind and ts and ts >= since:
                out.append(CashFlow(str(t.get("reference")), ts, kind, D(t["amount"]),
                                    self.capabilities.account_currency))
        return out
