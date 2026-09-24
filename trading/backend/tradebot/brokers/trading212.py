"""Trading 212 Public API (beta) adapter - the chosen live broker (FCA-regulated, GBP account).

Implemented from docs.trading212.com (researched 2026-09-24 via search; the docs host is
blocked from this build environment, so field names follow the published v0 reference
and are UNVERIFIED against the live service - run ``tradebot.cli check-broker``).

How it is used:
* UK retail clients cannot buy US-domiciled ETFs such as SPY/QQQ (no PRIIPs KID). Signals
  are computed on SPY/QQQ and executed in London-listed UCITS equivalents quoted in GBP
  (``execution_map``), resolved to Trading 212 tickers by ISIN from the instrument list.
  GBP lines carry no FX fee; UK stamp duty does not apply to ETFs.
* The live API supports market orders only (at the research date), so there are no
  broker-held stops: exits are software-managed and need the server running. Demo is
  configured the same way so paper trading behaves like live.
* No client order ids: after a timeout, orders are matched by ticker/quantity/time and
  the engine waits a longer grace period before treating an order as never sent.
* Strict per-endpoint rate limits: reads are cached briefly and the instrument list for
  hours; caches are invalidated after any order action.
* Prices from the API may be in pence (GBX) for some London lines; they are converted to
  GBP here so the rest of the system only sees account-currency prices.
"""
from __future__ import annotations

import base64
import time
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
# seconds each read may be served from cache (documented limits are per account, per endpoint)
_TTL = {"/equity/account/cash": 5, "/equity/portfolio": 5, "/equity/orders": 5,
        "/equity/metadata/instruments": 6 * 3600, "/history/transactions": 600, "/equity/history/orders": 10}


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


class Trading212Broker(Broker):
    def __init__(self, creds: BrokerCredentials, demo: bool, account_currency: str = "GBP",
                 timeout: float = 10.0, transport: httpx.BaseTransport | None = None,
                 execution_map: dict[str, dict[str, str]] | None = None, allow_stop_orders: bool = False):
        types = ("market", "stop") if allow_stop_orders else ("market",)
        self.capabilities = Capabilities(
            name="trading212", is_paper=demo, account_currency=account_currency,
            supports_fractional=True, supports_notional_orders=False, supports_client_order_id=False,
            protective_stop_for_fractional="stop" in types, protective_stop_tif="gtc",
            order_types=types, fx_fee_bps=15.0,
            notes=("Live API: market orders only, so exits are software-managed (server must be running).",
                   "No client order ids: post-timeout reconciliation matches ticker/quantity/time.",
                   "No price feed: signals/stops use real-time US index quotes; sizing uses a delayed London price.",
                   "0.15% FX fee applies only to instruments not quoted in GBP."))
        token = base64.b64encode(
            f"{creds.key_id.get_secret_value()}:{creds.secret.get_secret_value()}".encode()).decode()
        self._http = httpx.Client(
            base_url=f"https://{'demo' if demo else 'live'}.trading212.com/api/v0", timeout=timeout,
            transport=transport, headers={"Authorization": f"Basic {token}"})
        self.execution_map = execution_map or {}
        self.venue_calendar = "XLON" if self.execution_map else "XNYS"
        self._cache: dict[str, tuple[float, Any]] = {}
        self._by_symbol: dict[str, dict[str, Any]] = {}
        self._by_ticker: dict[str, str] = {}

    # -- transport ------------------------------------------------------------------
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

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        key = path + repr(sorted((params or {}).items()))
        ttl = _TTL.get(path, 0)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        val = self._req("GET", path, params=params)
        self._cache[key] = (time.monotonic(), val)
        return val

    def _invalidate(self) -> None:
        keep = "/equity/metadata/instruments"
        self._cache = {k: v for k, v in self._cache.items() if k.startswith(keep)}

    # -- instruments ------------------------------------------------------------------
    def _instrument(self, symbol: str) -> dict[str, Any] | None:
        if symbol in self._by_symbol:
            return self._by_symbol[symbol]
        instruments = self._get("/equity/metadata/instruments") or []
        spec = self.execution_map.get(symbol)
        found = None
        if spec:
            cands = [i for i in instruments if i.get("isin") == spec["isin"]]
            # prefer the London GBP/GBX line
            cands.sort(key=lambda i: (i.get("currencyCode") not in ("GBP", "GBX"), "l_EQ" not in i.get("ticker", "")))
            found = cands[0] if cands else None
        else:
            found = next((i for i in instruments if i.get("ticker") == f"{symbol}_US_EQ"), None)
        if found:
            self._by_symbol[symbol] = found
            self._by_ticker[found["ticker"]] = symbol
        return found

    def _ticker(self, symbol: str) -> str:
        inst = self._instrument(symbol)
        if not inst:
            raise BrokerRejected(f"no Trading 212 instrument for {symbol}")
        return inst["ticker"]

    def _sym(self, ticker: str) -> str:
        if ticker not in self._by_ticker:
            for s in self.execution_map:
                self._instrument(s)
        return self._by_ticker.get(ticker, ticker.split("_")[0])

    def _scale(self, ticker: str) -> Decimal:
        """Prices for pence-quoted (GBX) lines are divided by 100 to get GBP."""
        sym = self._sym(ticker)
        inst = self._by_symbol.get(sym)
        return Decimal("0.01") if inst and inst.get("currencyCode") == "GBX" else Decimal(1)

    def _ccy(self, ticker: str) -> str:
        inst = self._by_symbol.get(self._sym(ticker))
        c = (inst or {}).get("currencyCode", "USD" if ticker.endswith("_US_EQ") else "GBP")
        return "GBP" if c == "GBX" else c

    def _order(self, o: dict[str, Any], cid: str = "") -> BrokerOrder:
        qty = D(o.get("quantity") or 0)
        side = Side.SELL if qty < 0 else Side.BUY
        otype = {"MARKET": OrderType.MARKET, "LIMIT": OrderType.LIMIT, "STOP": OrderType.STOP}.get(
            o.get("type", "MARKET"), OrderType.MARKET)
        k = self._scale(o["ticker"])
        filled = abs(D(o.get("filledQuantity") or 0))
        value = abs(D(o.get("filledValue") or 0))          # account currency
        if o.get("fillPrice"):
            avg = D(o["fillPrice"]) * k
        else:
            avg = (value / filled) if filled and value else None
        return BrokerOrder(broker_order_id=str(o["id"]), client_order_id=cid, symbol=self._sym(o["ticker"]),
                           side=side, type=otype, status=_STATUS.get(o.get("status", ""), OrderStatus.ACCEPTED),
                           qty=abs(qty), notional=None, filled_qty=filled, filled_avg_price=avg,
                           stop_price=D(o["stopPrice"]) * k if o.get("stopPrice") else None,
                           submitted_at=_ts(o.get("creationTime") or o.get("dateCreated")),
                           updated_at=_ts(o.get("dateModified") or o.get("dateExecuted")))

    # -- Broker interface ------------------------------------------------------------------
    def get_account(self) -> Account:
        c = self._get("/equity/account/cash")
        free = D(c.get("free", 0))
        return Account(currency=self.capabilities.account_currency, cash=free, equity=D(c.get("total", 0)),
                       buying_power=free)

    def get_positions(self) -> list[Position]:
        out = []
        for p in self._get("/equity/portfolio") or []:
            k = self._scale(p["ticker"])
            out.append(Position(self._sym(p["ticker"]), D(p["quantity"]), D(p["averagePrice"]) * k,
                                D(p["currentPrice"]) * k, self._ccy(p["ticker"])))
        return out

    def list_open_orders(self) -> list[BrokerOrder]:
        return [self._order(o) for o in self._get("/equity/orders") or []]

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        o = self._req("GET", f"/equity/orders/{broker_order_id}")
        if o is not None:
            return self._order(o)
        hist = self._get("/equity/history/orders", {"limit": 50}) or {}
        for h in hist.get("items", []):
            if str(h.get("id")) == str(broker_order_id):
                return self._order(h)
        raise BrokerRejected(f"order {broker_order_id} not found")

    def find_order_by_client_id(self, client_order_id, hint=None, since=None):
        if hint is None:
            return None
        ticker = self._ticker(hint.symbol)
        want = hint.qty if hint.side == Side.BUY else -(hint.qty or D0)
        since = since or datetime.min
        self._invalidate()
        candidates = list(self._get("/equity/orders") or [])
        hist = self._get("/equity/history/orders", {"ticker": ticker, "limit": 50}) or {}
        candidates += hist.get("items", [])
        for o in candidates:
            created = _ts(o.get("creationTime") or o.get("dateCreated"))
            if (o.get("ticker") == ticker and D(o.get("quantity") or 0) == want
                    and (created is None or created >= since - timedelta(seconds=5))):
                return self._order(o, cid=client_order_id)
        return None

    def submit_order(self, req: OrderRequest) -> BrokerOrder:
        if req.type.value not in self.capabilities.order_types:
            raise BrokerRejected(f"{req.type.value} orders are not available via the Trading 212 API")
        if req.qty is None:
            raise BrokerRejected("Trading 212 requires a share quantity (no notional orders)")
        ticker = self._ticker(req.symbol)
        qty = req.qty if req.side == Side.BUY else -req.qty
        body: dict[str, Any] = {"ticker": ticker, "quantity": float(qty)}
        path = "/equity/orders/market"
        if req.type == OrderType.STOP:
            path, body["stopPrice"] = "/equity/orders/stop", float(req.stop_price / self._scale(ticker))
            body["timeValidity"] = "GOOD_TILL_CANCEL"
        else:
            body["extendedHours"] = False
        try:
            return self._order(self._req("POST", path, json=body, submit=True), cid=req.client_order_id)
        finally:
            self._invalidate()

    def cancel_order(self, broker_order_id: str) -> None:
        try:
            self._req("DELETE", f"/equity/orders/{broker_order_id}")
        finally:
            self._invalidate()

    def get_asset(self, symbol: str) -> Asset:
        inst = self._instrument(symbol)
        if not inst:
            return Asset(symbol, tradable=False, fractionable=False)
        ccy = "GBP" if inst.get("currencyCode") == "GBX" else inst.get("currencyCode", "GBP")
        return Asset(symbol, tradable=True, fractionable=True, currency=ccy, min_notional=Decimal("1"),
                     broker_symbol=inst["ticker"], qty_increment=Decimal("0.00000001"))

    def get_clock(self) -> MarketClock | None:
        return None      # the engine uses exchange calendars (XNYS for signals, XLON for execution)

    def get_cash_flows(self, since: datetime) -> list[CashFlow]:
        data = self._get("/history/transactions", {"limit": 50}) or {}
        out = []
        for t in data.get("items", []):
            ts = _ts(t.get("dateTime"))
            kind = {"DEPOSIT": "deposit", "WITHDRAW": "withdrawal"}.get(t.get("type"))
            if kind and ts and ts >= since:
                out.append(CashFlow(str(t.get("reference")), ts, kind, D(t["amount"]),
                                    self.capabilities.account_currency))
        return out
