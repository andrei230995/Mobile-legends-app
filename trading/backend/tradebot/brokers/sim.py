"""In-process simulated broker for paper trading without broker credentials, and for
failure-injection tests.

Fills are taken from the current quote (buys at the ask, sells at the bid) plus a
configurable slippage, US regulatory fees are charged on sells with per-trade cent
rounding, stops trigger on the bid and fill at the bid (so gaps fill *below* the stop,
as in real markets), DAY orders expire at the close, shorting and leverage are refused.
Simulated fills are still optimistic versus live execution: no queue position, no
market impact, no partial liquidity beyond the configured partial-fill hook.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..clock import Clock, MarketCalendar
from ..data.base import DataUnavailable, MarketData
from ..models import (D0, Account, Asset, BrokerOrder, CashFlow, D, MarketClock, OrderRequest,
                      OrderStatus, OrderType, Position, Side)
from .base import (Broker, BrokerAuthError, BrokerRateLimited, BrokerRejected, BrokerTimeout,
                   BrokerUnavailable, Capabilities, USRegulatoryFeeModel)

_ORDER_DEC = ("qty", "notional", "filled_qty", "filled_avg_price", "fees", "limit_price", "stop_price")


class SimBroker(Broker):
    def __init__(self, data: MarketData, clock: Clock, starting_cash: Decimal = Decimal("12.50"),
                 state_path: Path | None = None, slippage_bps: float = 2.0,
                 min_notional: Decimal = Decimal("1"), currency: str = "USD"):
        self.data, self.clock, self.cal = data, clock, MarketCalendar()
        self.state_path = state_path
        self.slippage = D(slippage_bps) / Decimal(10000)
        self.min_notional = min_notional
        self.fee_model = USRegulatoryFeeModel()
        self.capabilities = Capabilities(
            name="sim", is_paper=True, account_currency=currency, supports_fractional=True,
            supports_notional_orders=True, supports_client_order_id=True,
            protective_stop_for_fractional=True, protective_stop_tif="gtc",
            order_types=("market", "limit", "stop"),
            notes=("Simulated fills from quotes; optimistic versus live execution.",))
        # failure injection (tests): consumed one per call
        self.inject: list[str] = []
        self.partial_next = Decimal("0")     # fraction to fill on the next fill event
        self.untradable: set[str] = set()
        self.cash = D0
        self.positions: dict[str, dict[str, Decimal]] = {}
        self.orders: dict[str, BrokerOrder] = {}
        self.cash_flows: list[CashFlow] = []
        if state_path and state_path.exists():
            self._load()
        elif starting_cash > 0:
            self.deposit(starting_cash, note="initial simulated deposit")

    # -- persistence ------------------------------------------------------------
    def _save(self) -> None:
        if not self.state_path:
            return
        state = {
            "cash": str(self.cash),
            "positions": {s: {k: str(v) for k, v in p.items()} for s, p in self.positions.items()},
            "orders": [{**{k: (str(getattr(o, k)) if getattr(o, k) is not None else None) for k in _ORDER_DEC},
                        "broker_order_id": o.broker_order_id, "client_order_id": o.client_order_id,
                        "symbol": o.symbol, "side": o.side.value, "type": o.type.value,
                        "status": o.status.value, "reject_reason": o.reject_reason,
                        "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
                        "updated_at": o.updated_at.isoformat() if o.updated_at else None}
                       for o in self.orders.values()],
            "cash_flows": [{"flow_id": f.flow_id, "ts": f.ts.isoformat(), "kind": f.kind,
                            "amount": str(f.amount), "currency": f.currency} for f in self.cash_flows],
        }
        tmp = self.state_path.with_suffix(".tmp")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state))
        os.replace(tmp, self.state_path)

    def _load(self) -> None:
        st = json.loads(self.state_path.read_text())
        self.cash = D(st["cash"])
        self.positions = {s: {k: D(v) for k, v in p.items()} for s, p in st["positions"].items()}
        for o in st["orders"]:
            bo = BrokerOrder(
                broker_order_id=o["broker_order_id"], client_order_id=o["client_order_id"],
                symbol=o["symbol"], side=Side(o["side"]), type=OrderType(o["type"]),
                status=OrderStatus(o["status"]), qty=None, notional=None,
                reject_reason=o.get("reject_reason", ""),
                submitted_at=datetime.fromisoformat(o["submitted_at"]) if o["submitted_at"] else None,
                updated_at=datetime.fromisoformat(o["updated_at"]) if o["updated_at"] else None)
            for k in _ORDER_DEC:
                setattr(bo, k, D(o[k]) if o[k] is not None else None)
            bo.filled_qty = bo.filled_qty or D0
            bo.fees = bo.fees or D0
            self.orders[bo.broker_order_id] = bo
        self.cash_flows = [CashFlow(f["flow_id"], datetime.fromisoformat(f["ts"]), f["kind"],
                                    D(f["amount"]), f["currency"]) for f in st["cash_flows"]]

    # -- helpers -------------------------------------------------------------------
    def _maybe_fail(self, stage: str) -> str | None:
        if not self.inject:
            return None
        kind = self.inject[0]
        if stage == "any" and kind in ("rate_limit", "unavailable", "auth"):
            self.inject.pop(0)
            if kind == "rate_limit":
                raise BrokerRateLimited("simulated 429", retry_after=1)
            if kind == "unavailable":
                raise BrokerUnavailable("simulated 503")
            raise BrokerAuthError("simulated 401")
        if stage == "submit" and kind in ("timeout_before_accept", "timeout_after_accept", "reject"):
            return self.inject.pop(0)
        return None

    def deposit(self, amount: Decimal, note: str = "", ts: datetime | None = None) -> None:
        self.cash += amount
        kind = "deposit" if amount > 0 else "withdrawal"
        self.cash_flows.append(CashFlow(f"sim-{uuid.uuid4().hex[:12]}", ts or self.clock.now(),
                                        kind, amount, self.capabilities.account_currency))
        self._save()

    def _quote(self, symbol: str):
        try:
            return self.data.latest_quote(symbol)
        except DataUnavailable as e:
            raise BrokerUnavailable(str(e)) from e

    # -- Broker interface ---------------------------------------------------------
    def get_account(self) -> Account:
        self._maybe_fail("any")
        self.process()
        mv = sum((p["qty"] * self._quote(s).bid for s, p in self.positions.items()), D0)
        return Account(currency=self.capabilities.account_currency, cash=self.cash,
                       equity=self.cash + mv, buying_power=self.cash)

    def get_positions(self) -> list[Position]:
        self._maybe_fail("any")
        out = []
        for s, p in self.positions.items():
            if p["qty"] > 0:
                q = self._quote(s)
                out.append(Position(s, p["qty"], p["avg"], q.bid, self.capabilities.account_currency))
        return out

    def list_open_orders(self) -> list[BrokerOrder]:
        self._maybe_fail("any")
        self.process()
        return [o for o in self.orders.values() if o.status.is_open]

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        self._maybe_fail("any")
        self.process()
        if broker_order_id not in self.orders:
            raise BrokerRejected(f"order {broker_order_id} not found")
        return self.orders[broker_order_id]

    def find_order_by_client_id(self, client_order_id, hint=None, since=None):
        self._maybe_fail("any")
        for o in self.orders.values():
            if o.client_order_id == client_order_id:
                return o
        return None

    def get_asset(self, symbol: str) -> Asset:
        self._maybe_fail("any")
        return Asset(symbol=symbol, tradable=symbol not in self.untradable, fractionable=True,
                     currency=self.capabilities.account_currency, min_notional=self.min_notional)

    def get_clock(self) -> MarketClock:
        return self.cal.clock(self.clock.now())

    def get_cash_flows(self, since: datetime) -> list[CashFlow]:
        return [f for f in self.cash_flows if f.ts >= since]

    def submit_order(self, req: OrderRequest) -> BrokerOrder:
        self._maybe_fail("any")
        fail = self._maybe_fail("submit")
        if fail == "timeout_before_accept":
            raise BrokerTimeout("simulated timeout; request never reached broker")
        if any(o.client_order_id == req.client_order_id for o in self.orders.values()):
            raise BrokerRejected("client_order_id must be unique")
        now = self.clock.now()
        o = BrokerOrder(broker_order_id=f"sim-{uuid.uuid4().hex[:16]}", client_order_id=req.client_order_id,
                        symbol=req.symbol, side=req.side, type=req.type, status=OrderStatus.ACCEPTED,
                        qty=req.qty, notional=req.notional, limit_price=req.limit_price,
                        stop_price=req.stop_price, submitted_at=now, updated_at=now)
        reason = self._validate(req) if fail != "reject" else "simulated rejection"
        if reason:
            o.status, o.reject_reason = OrderStatus.REJECTED, reason
            self.orders[o.broker_order_id] = o
            self._save()
            raise BrokerRejected(reason)
        self.orders[o.broker_order_id] = o
        self.process()
        self._save()
        if fail == "timeout_after_accept":
            raise BrokerTimeout("simulated timeout after the broker accepted the order")
        return o

    def _validate(self, req: OrderRequest) -> str:
        if req.symbol in self.untradable:
            return f"{req.symbol} is not tradable"
        if (req.qty is None) == (req.notional is None):
            return "exactly one of qty or notional is required"
        clock = self.get_clock()
        if req.type == OrderType.MARKET and not clock.is_open:
            return "market is closed"
        q = self._quote(req.symbol)
        px = q.ask if req.side == Side.BUY else q.bid
        qty = req.qty if req.qty is not None else req.notional / px
        if qty <= 0:
            return "quantity must be positive"
        if qty * px < self.min_notional:
            return f"order value below minimum {self.min_notional}"
        if req.side == Side.BUY:
            reserved = sum((oo.notional or (oo.qty or D0) * px) for oo in self.orders.values()
                           if oo.status.is_open and oo.side == Side.BUY)
            if qty * px * (1 + self.slippage) + reserved > self.cash:
                return "insufficient buying power (cash account, no margin)"
        else:
            held = self.positions.get(req.symbol, {}).get("qty", D0)
            committed = sum(((oo.qty or D0) - oo.filled_qty) for oo in self.orders.values()
                            if oo.status.is_open and oo.side == Side.SELL and oo.symbol == req.symbol)
            if qty > held - committed + Decimal("1e-9"):
                return "insufficient position quantity (short selling not permitted)"
        return ""

    def cancel_order(self, broker_order_id: str) -> None:
        self._maybe_fail("any")
        o = self.orders.get(broker_order_id)
        if not o:
            raise BrokerRejected("order not found")
        if o.status.is_open:
            o.status, o.updated_at = OrderStatus.CANCELED, self.clock.now()
            self._save()

    # -- matching -------------------------------------------------------------------
    def process(self) -> None:
        now = self.clock.now()
        clock = self.cal.clock(now)
        changed = False
        for o in list(self.orders.values()):
            if not o.status.is_open:
                continue
            if not clock.is_open:
                # DAY orders still open outside the session belong to an ended session.
                if o.type != OrderType.STOP:   # stops are GTC in the simulator
                    o.status, o.updated_at, changed = OrderStatus.EXPIRED, now, True
                continue
            try:
                q = self.data.latest_quote(o.symbol)
            except DataUnavailable:
                continue
            px = None
            if o.type == OrderType.MARKET:
                px = q.ask * (1 + self.slippage) if o.side == Side.BUY else q.bid * (1 - self.slippage)
            elif o.type == OrderType.LIMIT:
                if o.side == Side.BUY and q.ask <= o.limit_price:
                    px = q.ask
                elif o.side == Side.SELL and q.bid >= o.limit_price:
                    px = q.bid
            elif o.type == OrderType.STOP and o.side == Side.SELL and q.bid <= o.stop_price:
                px = q.bid * (1 - self.slippage)      # gap-through fills below the stop
            if px is None:
                continue
            self._fill(o, px.quantize(Decimal("0.0001")), now)
            changed = True
        if changed:
            self._save()

    def _fill(self, o: BrokerOrder, px: Decimal, now: datetime) -> None:
        total_qty = o.qty if o.qty is not None else (o.notional / px).quantize(Decimal("0.000000001"))
        if o.qty is None:
            o.qty = total_qty
        remaining = total_qty - o.filled_qty
        if o.side == Side.SELL:
            remaining = min(remaining, self.positions.get(o.symbol, {}).get("qty", D0))
        qty = remaining
        if self.partial_next > 0:
            qty = (remaining * self.partial_next).quantize(Decimal("0.000000001"))
            self.partial_next = D0
        if qty <= 0:
            return
        fee = self.fee_model.fee(o.side, qty, px)
        pos = self.positions.setdefault(o.symbol, {"qty": D0, "avg": D0})
        if o.side == Side.BUY:
            cost = qty * px
            pos["avg"] = (pos["avg"] * pos["qty"] + cost) / (pos["qty"] + qty)
            pos["qty"] += qty
            self.cash -= cost + fee
        else:
            pos["qty"] -= qty
            self.cash += qty * px - fee
            if pos["qty"] <= Decimal("1e-9"):
                self.positions.pop(o.symbol)
        prev = o.filled_qty
        o.filled_avg_price = px if not prev else ((o.filled_avg_price * prev + px * qty) / (prev + qty))
        o.filled_qty = prev + qty
        o.fees += fee
        o.status = OrderStatus.FILLED if o.filled_qty >= total_qty - Decimal("1e-9") else OrderStatus.PARTIALLY_FILLED
        o.updated_at = now
