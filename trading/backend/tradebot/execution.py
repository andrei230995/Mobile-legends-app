"""Order lifecycle with durable, idempotent submission.

Protocol for every order:
1. A deterministic client order id is derived from (strategy, symbol, purpose, session
   date, attempt). The row is written as PENDING_SUBMIT *before* the request is sent.
2. Broker accepted -> ACCEPTED/FILLED...; definitive refusal -> REJECTED; rate limit or
   connection refused -> NOT_SENT (safe to retry); timeout/5xx on submit -> UNKNOWN.
3. PENDING_SUBMIT (crash mid-send) and UNKNOWN rows are reconciled by looking the order
   up at the broker. Only when the broker confirms it has no such order (after a grace
   period) does the row become NOT_SENT, and only then may the same id be resubmitted.
   An uncertain response therefore can never produce a duplicate order.
4. Fills are recorded from the broker's cumulative filled quantity/average price, keyed
   by (client id, cumulative qty) so re-polling never double-counts a fill.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from .brokers.base import (Broker, BrokerAuthError, BrokerError, BrokerRateLimited, BrokerRejected,
                           BrokerTimeout, BrokerUnavailable)
from .clock import Clock
from .db import Store, iso, parse_ts
from .models import D0, BrokerOrder, D, Mode, OrderRequest, OrderStatus, OrderType, Purpose, Side


def make_client_order_id(strategy: str, symbol: str, purpose: Purpose, session: str, attempt: int = 0) -> str:
    base = f"tb-{strategy}-{symbol}-{purpose.value}-{session}".replace("_", "")
    return base if attempt == 0 else f"{base}-r{attempt}"


class ExecutionEngine:
    def __init__(self, store: Store, broker: Broker, clock: Clock, mode_fn: Callable[[], Mode],
                 fx_fn: Callable[[str], tuple[Decimal, str]]):
        self.store, self.broker, self.clock = store, broker, clock
        self.mode_fn = mode_fn
        self.fx_fn = fx_fn          # currency -> (gbp per unit, source label)
        self.grace = timedelta(seconds=30 if broker.capabilities.supports_client_order_id else 120)

    # -- submission ------------------------------------------------------------------
    def submit(self, req: OrderRequest, purpose: Purpose, strategy: str | None,
               decision_id: int | None = None) -> dict[str, Any]:
        mode = self.mode_fn()
        if mode == Mode.READ_ONLY:
            raise RuntimeError("read-only mode: orders are never sent")
        existing = self.store.get_order(req.client_order_id)
        now = self.clock.now()
        if existing and existing["status"] != OrderStatus.NOT_SENT.value:
            self.store.event("info", "execution", f"duplicate submit suppressed for {req.client_order_id}",
                             {"status": existing["status"]})
            return existing
        row = {"client_order_id": req.client_order_id, "broker": self.broker.capabilities.name,
               "mode": mode.value, "symbol": req.symbol, "side": req.side.value, "type": req.type.value,
               "tif": req.tif, "qty": req.qty, "notional": req.notional, "limit_price": req.limit_price,
               "stop_price": req.stop_price, "purpose": purpose.value, "strategy": strategy,
               "decision_id": decision_id, "status": OrderStatus.PENDING_SUBMIT.value,
               "created_at": iso(now), "updated_at": iso(now), "attempts": 1}
        if existing:
            self.store.update_order(req.client_order_id, status=OrderStatus.PENDING_SUBMIT,
                                    attempts=existing["attempts"] + 1, last_error=None, created_at=iso(now))
        else:
            self.store.insert_order(row)
        try:
            bo = self.broker.submit_order(req)
        except BrokerTimeout as e:
            self.store.update_order(req.client_order_id, status=OrderStatus.UNKNOWN, last_error=str(e),
                                    submitted_at=iso(now))
            self.store.event("warning", "execution", f"order outcome unknown, will reconcile: {req.client_order_id}",
                             {"error": str(e)})
        except BrokerRejected as e:
            self.store.update_order(req.client_order_id, status=OrderStatus.REJECTED, last_error=str(e))
            self.store.event("warning", "execution", f"order rejected: {req.client_order_id}", {"error": str(e)})
        except (BrokerRateLimited, BrokerUnavailable) as e:
            self.store.update_order(req.client_order_id, status=OrderStatus.NOT_SENT, last_error=str(e))
            self.store.event("warning", "execution", f"order not sent ({type(e).__name__}): {req.client_order_id}",
                             {"error": str(e)})
        except BrokerAuthError as e:
            self.store.update_order(req.client_order_id, status=OrderStatus.NOT_SENT, last_error=str(e))
            raise
        else:
            self.store.update_order(req.client_order_id, broker_order_id=bo.broker_order_id,
                                    status=bo.status, submitted_at=iso(bo.submitted_at or now))
            self._apply(self.store.get_order(req.client_order_id), bo)
        return self.store.get_order(req.client_order_id)

    # -- reconciliation --------------------------------------------------------------
    def reconcile_unresolved(self) -> list[str]:
        """Resolve PENDING_SUBMIT/UNKNOWN orders against the broker. Returns notes."""
        notes = []
        now = self.clock.now()
        for row in self.store.orders_with_status(OrderStatus.PENDING_SUBMIT, OrderStatus.UNKNOWN):
            hint = OrderRequest(row["client_order_id"], row["symbol"], Side(row["side"]), OrderType(row["type"]),
                                qty=D(row["qty"]) if row["qty"] else None,
                                notional=D(row["notional"]) if row["notional"] else None)
            created = parse_ts(row["created_at"])
            bo = self.broker.find_order_by_client_id(row["client_order_id"], hint=hint, since=created)
            if bo is not None:
                self.store.update_order(row["client_order_id"], broker_order_id=bo.broker_order_id, status=bo.status,
                                        submitted_at=iso(bo.submitted_at or created))
                self._apply(self.store.get_order(row["client_order_id"]), bo)
                msg = f"reconciled {row['client_order_id']}: broker has it as {bo.status.value}"
                self.store.event("info", "reconcile", msg)
                notes.append(msg)
            elif now - created >= self.grace:
                self.store.update_order(row["client_order_id"], status=OrderStatus.NOT_SENT,
                                        last_error="broker confirmed no such order after grace period")
                msg = f"confirmed {row['client_order_id']} never reached the broker; eligible for resubmission"
                self.store.event("warning", "reconcile", msg)
                notes.append(msg)
        return notes

    def sync_open(self) -> None:
        for row in self.store.orders_with_status(OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
            if not row["broker_order_id"]:
                continue
            bo = self.broker.get_order(row["broker_order_id"])
            if bo.status.value != row["status"] or bo.filled_qty != D(row["filled_qty"]):
                self.store.update_order(row["client_order_id"], status=bo.status)
                self._apply(self.store.get_order(row["client_order_id"]), bo)

    def _apply(self, row: dict[str, Any], bo: BrokerOrder) -> None:
        prev_qty = D(row["filled_qty"])
        prev_avg = D(row["filled_avg_price"]) if row["filled_avg_price"] else D0
        delta = bo.filled_qty - prev_qty
        if delta > 0 and bo.filled_avg_price is not None:
            price = (bo.filled_avg_price * bo.filled_qty - prev_avg * prev_qty) / delta
            if bo.fees > D(row["fees"]):
                fee = bo.fees - D(row["fees"])
            else:
                fee = self.broker.fee_model.fee(bo.side, delta, price)
            currency = self.broker.capabilities.account_currency
            rate, src = self.fx_fn(currency)
            ts = bo.updated_at or self.clock.now()
            self.store.execute(
                "INSERT OR IGNORE INTO fills(fill_id,client_order_id,symbol,side,qty,price,currency,fee,fx_to_gbp,"
                "fx_source,ts,purpose,strategy) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{row['client_order_id']}:{bo.filled_qty}", row["client_order_id"], row["symbol"], bo.side.value,
                 str(delta), str(price.quantize(Decimal("0.000001"))), currency, str(fee), str(rate), src,
                 iso(ts), row["purpose"], row["strategy"]))
            self.store.update_order(row["client_order_id"], filled_qty=bo.filled_qty,
                                    filled_avg_price=bo.filled_avg_price, fees=D(row["fees"]) + fee)
            self.store.event("info", "fill", f"{bo.side.value} {delta} {row['symbol']} @ {price:.4f} "
                             f"({row['purpose']}, {bo.status.value})",
                             {"cid": row["client_order_id"], "fee": str(fee)}, ts=ts)
        if bo.status.value != row["status"]:
            self.store.update_order(row["client_order_id"], status=bo.status,
                                    last_error=bo.reject_reason or row["last_error"])

    # -- cancellation ------------------------------------------------------------------
    def cancel(self, cid: str, reason: str) -> bool:
        row = self.store.get_order(cid)
        if not row or not row["broker_order_id"] or OrderStatus(row["status"]).is_terminal:
            return False
        try:
            self.broker.cancel_order(row["broker_order_id"])
        except BrokerRejected as e:
            self.store.event("warning", "execution", f"cancel refused for {cid}: {e}")
        bo = self.broker.get_order(row["broker_order_id"])
        self.store.update_order(cid, status=bo.status)
        self._apply(self.store.get_order(cid), bo)
        self.store.event("info", "execution", f"cancel {cid}: {reason} -> {bo.status.value}")
        return not bo.status.is_open

    def open_rows(self, purpose: Purpose | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.orders_with_status(OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED,
                                             OrderStatus.PENDING_SUBMIT, OrderStatus.UNKNOWN)
        return [r for r in rows if (purpose is None or r["purpose"] == purpose.value)
                and (symbol is None or r["symbol"] == symbol)]

    def orders_today(self, since: datetime) -> int:
        return self.store.one("SELECT COUNT(*) AS n FROM orders WHERE created_at >= ? AND status != ?",
                              (iso(since), OrderStatus.NOT_SENT.value))["n"]


__all__ = ["ExecutionEngine", "make_client_order_id", "BrokerError"]
