"""Alpaca Trading API v2 adapter (paper and live).

Implemented from Alpaca's public documentation (docs.alpaca.markets, researched
2026-09-24). It is unit-tested against mocked HTTP responses in the documented
format; it has NOT been exercised against Alpaca's servers from this repository's
build environment (the network egress policy blocks alpaca.markets). Run
``python -m tradebot.cli check-broker`` on the deployment host to verify it.

Paper and live use different hosts *and* different key pairs, so a paper key can
never place a live order.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from ..config import BrokerCredentials
from ..models import (D0, Account, Asset, BrokerOrder, CashFlow, D, MarketClock, OrderRequest,
                      OrderStatus, OrderType, Position, Side)
from .base import (Broker, BrokerAuthError, BrokerError, BrokerRateLimited, BrokerRejected,
                   BrokerTimeout, BrokerUnavailable, Capabilities, USRegulatoryFeeModel)

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"

_STATUS = {
    "new": OrderStatus.ACCEPTED, "accepted": OrderStatus.ACCEPTED, "pending_new": OrderStatus.ACCEPTED,
    "accepted_for_bidding": OrderStatus.ACCEPTED, "held": OrderStatus.ACCEPTED,
    "pending_cancel": OrderStatus.ACCEPTED, "pending_replace": OrderStatus.ACCEPTED,
    "calculated": OrderStatus.ACCEPTED, "stopped": OrderStatus.ACCEPTED, "suspended": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED, "filled": OrderStatus.FILLED,
    "done_for_day": OrderStatus.EXPIRED, "expired": OrderStatus.EXPIRED,
    "canceled": OrderStatus.CANCELED, "replaced": OrderStatus.CANCELED,
    "rejected": OrderStatus.REJECTED,
}


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _dec(v: Any) -> Decimal | None:
    return None if v in (None, "") else D(v)


class AlpacaBroker(Broker):
    def __init__(self, creds: BrokerCredentials, paper: bool, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None):
        self.paper = paper
        self.fee_model = USRegulatoryFeeModel()
        self.capabilities = Capabilities(
            name="alpaca", is_paper=paper, account_currency="USD", supports_fractional=True,
            supports_notional_orders=True, supports_client_order_id=True,
            protective_stop_for_fractional=True, protective_stop_tif="day",
            order_types=("market", "limit", "stop", "stop_limit"),
            notes=("Fractional orders must use time_in_force=day, so broker-held stops on "
                   "fractional positions are re-placed each session and do not cover overnight gaps.",
                   "Paper fills ignore market impact, queue position and regulatory fees."))
        self._http = httpx.Client(
            base_url=PAPER_URL if paper else LIVE_URL, timeout=timeout, transport=transport,
            headers={"APCA-API-KEY-ID": creds.key_id.get_secret_value(),
                     "APCA-API-SECRET-KEY": creds.secret.get_secret_value()})

    # -- transport --------------------------------------------------------------
    def _req(self, method: str, path: str, *, submit: bool = False, **kw) -> Any:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.TimeoutException as e:
            if submit:
                raise BrokerTimeout(f"{method} {path} timed out; outcome unknown") from e
            raise BrokerUnavailable(f"{method} {path} timed out") from e
        except httpx.TransportError as e:
            # connection errors before sending are safe; after sending they are not knowable
            if submit and not isinstance(e, httpx.ConnectError):
                raise BrokerTimeout(f"{method} {path} transport error; outcome unknown") from e
            raise BrokerUnavailable(f"{method} {path}: {type(e).__name__}") from e
        if r.status_code == 401:
            raise BrokerAuthError("Alpaca rejected the API credentials (401)")
        if r.status_code == 429:
            raise BrokerRateLimited("Alpaca rate limit (429)", float(r.headers.get("retry-after", 5)))
        if r.status_code >= 500:
            if submit:
                raise BrokerTimeout(f"Alpaca {r.status_code} on submit; outcome unknown")
            raise BrokerUnavailable(f"Alpaca {r.status_code}")
        if r.status_code == 404:
            return None
        if r.status_code in (403, 422, 400):
            msg = _message(r)
            if r.status_code == 403 and not submit and "order" not in path:
                raise BrokerAuthError(f"Alpaca forbidden: {msg}")
            raise BrokerRejected(f"Alpaca {r.status_code}: {msg}")
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # -- mapping ------------------------------------------------------------------
    @staticmethod
    def _order(o: dict[str, Any]) -> BrokerOrder:
        otype = {"market": OrderType.MARKET, "limit": OrderType.LIMIT,
                 "stop": OrderType.STOP}.get(o.get("type") or o.get("order_type"), OrderType.MARKET)
        return BrokerOrder(
            broker_order_id=o["id"], client_order_id=o.get("client_order_id", ""),
            symbol=o["symbol"], side=Side(o["side"]), type=otype,
            status=_STATUS.get(o["status"], OrderStatus.ACCEPTED),
            qty=_dec(o.get("qty")), notional=_dec(o.get("notional")),
            filled_qty=_dec(o.get("filled_qty")) or D0, filled_avg_price=_dec(o.get("filled_avg_price")),
            limit_price=_dec(o.get("limit_price")), stop_price=_dec(o.get("stop_price")),
            submitted_at=_ts(o.get("submitted_at")), updated_at=_ts(o.get("updated_at")),
            reject_reason=o.get("status") if o.get("status") == "rejected" else "")

    # -- Broker interface -------------------------------------------------------------
    def get_account(self) -> Account:
        a = self._req("GET", "/v2/account")
        # Use non-marginable buying power / cash so sizing can never rely on margin.
        cash = D(a["cash"])
        nmbp = _dec(a.get("non_marginable_buying_power"))
        bp = min(cash, nmbp) if nmbp is not None else cash
        return Account(currency=a.get("currency", "USD"), cash=cash, equity=D(a["equity"]),
                       buying_power=bp,
                       trading_blocked=bool(a.get("trading_blocked") or a.get("account_blocked")),
                       status=a.get("status", ""))

    def get_positions(self) -> list[Position]:
        return [Position(p["symbol"], D(p["qty"]), D(p["avg_entry_price"]),
                         D(p.get("current_price") or p["avg_entry_price"]), "USD")
                for p in self._req("GET", "/v2/positions") or []]

    def list_open_orders(self) -> list[BrokerOrder]:
        return [self._order(o) for o in self._req("GET", "/v2/orders",
                                                  params={"status": "open", "limit": 500}) or []]

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        o = self._req("GET", f"/v2/orders/{broker_order_id}")
        if o is None:
            raise BrokerRejected(f"order {broker_order_id} not found")
        return self._order(o)

    def find_order_by_client_id(self, client_order_id, hint=None, since=None):
        o = self._req("GET", "/v2/orders:by_client_order_id",
                      params={"client_order_id": client_order_id})
        return self._order(o) if o else None

    def submit_order(self, req: OrderRequest) -> BrokerOrder:
        body: dict[str, Any] = {"symbol": req.symbol, "side": req.side.value, "type": req.type.value,
                                "time_in_force": req.tif, "client_order_id": req.client_order_id}
        if req.qty is not None:
            body["qty"] = str(req.qty)
        if req.notional is not None:
            body["notional"] = str(req.notional.quantize(Decimal("0.01")))
        if req.limit_price is not None:
            body["limit_price"] = str(req.limit_price)
        if req.stop_price is not None:
            body["stop_price"] = str(req.stop_price.quantize(Decimal("0.01")))
        return self._order(self._req("POST", "/v2/orders", json=body, submit=True))

    def cancel_order(self, broker_order_id: str) -> None:
        self._req("DELETE", f"/v2/orders/{broker_order_id}")

    def get_asset(self, symbol: str) -> Asset:
        a = self._req("GET", f"/v2/assets/{symbol}")
        if a is None:
            return Asset(symbol, tradable=False, fractionable=False)
        return Asset(symbol=symbol, tradable=bool(a.get("tradable")) and a.get("status") == "active",
                     fractionable=bool(a.get("fractionable")), currency="USD",
                     min_notional=Decimal("1"))

    def get_clock(self) -> MarketClock:
        c = self._req("GET", "/v2/clock")
        return MarketClock(is_open=bool(c["is_open"]), next_open=_ts(c["next_open"]),
                           next_close=_ts(c["next_close"]))

    def get_cash_flows(self, since: datetime) -> list[CashFlow]:
        rows = self._req("GET", "/v2/account/activities",
                         params={"activity_types": "CSD,CSW,DIV,FEE,INT",
                                 "after": since.isoformat(), "direction": "asc"}) or []
        kinds = {"CSD": "deposit", "CSW": "withdrawal", "DIV": "dividend", "FEE": "fee", "INT": "interest"}
        out = []
        for r in rows:
            amt = D(r.get("net_amount") or r.get("amount") or 0)
            raw = r.get("transaction_time") or r.get("date")
            ts = _ts(raw if "T" in raw else raw + "T00:00:00+00:00")
            out.append(CashFlow(r["id"], ts, kinds.get(r["activity_type"], r["activity_type"].lower()),
                                amt, "USD"))
        return out

    def prepare_account(self) -> list[str]:
        """Disable shorting and cap the margin multiplier at 1 (cash-only behaviour)."""
        try:
            self._req("PATCH", "/v2/account/configurations",
                      json={"no_shorting": True, "max_margin_multiplier": "1"})
            return ["Alpaca account configured: no_shorting=true, max_margin_multiplier=1"]
        except BrokerError as e:
            return [f"Could not apply Alpaca account safety configuration: {e}"]


def _message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("message", r.text))[:300]
    except ValueError:
        return r.text[:300]
