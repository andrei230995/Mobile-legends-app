"""GBP performance accounting.

Definitions (all in GBP):

* **Trading P&L for a period** = equity(end) - equity(start) - external cash flows
  (deposits and withdrawals). Deposits are never profit.
* It is decomposed into realised P&L (FIFO lots, net of broker fees, converted at the
  FX rate of each fill), the change in unrealised P&L on open positions (marked at the
  latest quote and FX rate - losing positions are always included), dividends/interest/
  broker cash fees, and an explicit **FX/other residual** (e.g. FX translation of idle
  USD cash) so the decomposition always adds up to the broker-reported equity change.
* **Net result after operating costs** = trading P&L - attributable server, data and AI
  costs for the period.
* Drawdown and daily-loss limits use a unitised NAV (like a fund): deposits buy units at
  the current NAV, so adding money neither hides nor creates a drawdown.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .db import Store, iso, parse_ts
from .models import D0, D


@dataclass
class Lot:
    qty: Decimal
    cost_ccy: Decimal
    cost_gbp: Decimal


@dataclass
class RealisedRow:
    ts: datetime
    symbol: str
    qty: Decimal
    realised_gbp: Decimal
    realised_ccy: Decimal
    fee_ccy: Decimal
    strategy: str | None
    ret_pct: Decimal


class Ledger:
    """FIFO lots rebuilt from the durable fills table (cheap at this scale, always consistent)."""

    def __init__(self, store: Store):
        self.store = store

    def build(self) -> tuple[dict[str, deque[Lot]], list[RealisedRow], list[dict[str, Any]]]:
        lots: dict[str, deque[Lot]] = defaultdict(deque)
        realised: list[RealisedRow] = []
        fills = self.store.query("SELECT * FROM fills ORDER BY ts, fill_id")
        for f in fills:
            qty, px, fee, fx = D(f["qty"]), D(f["price"]), D(f["fee"]), D(f["fx_to_gbp"])
            if f["side"] == "buy":
                c = qty * px + fee
                lots[f["symbol"]].append(Lot(qty, c, c * fx))
                continue
            proceeds_ccy = qty * px - fee
            remaining, cost_ccy, cost_gbp = qty, D0, D0
            q = lots[f["symbol"]]
            while remaining > 0 and q:
                lot = q[0]
                take = min(lot.qty, remaining)
                frac = take / lot.qty
                cost_ccy += lot.cost_ccy * frac
                cost_gbp += lot.cost_gbp * frac
                lot.qty -= take
                lot.cost_ccy -= lot.cost_ccy * frac if lot.qty > 0 else lot.cost_ccy
                lot.cost_gbp -= lot.cost_gbp * frac if lot.qty > 0 else lot.cost_gbp
                remaining -= take
                if lot.qty <= Decimal("1e-12"):
                    q.popleft()
            # a sell without matching lots (e.g. pre-existing position) has unknown cost
            matched = qty - remaining
            if matched <= 0:
                continue
            share = matched / qty
            realised.append(RealisedRow(
                ts=parse_ts(f["ts"]), symbol=f["symbol"], qty=matched,
                realised_gbp=proceeds_ccy * share * fx - cost_gbp,
                realised_ccy=proceeds_ccy * share - cost_ccy, fee_ccy=fee, strategy=f["strategy"],
                ret_pct=((proceeds_ccy * share / cost_ccy) - 1) * 100 if cost_ccy else D0))
        return lots, realised, fills

    def unrealised_gbp(self, marks: dict[str, Decimal], fx_to_gbp: Decimal) -> tuple[Decimal, dict[str, Decimal]]:
        lots, _, _ = self.build()
        per: dict[str, Decimal] = {}
        for sym, q in lots.items():
            qty = sum((l.qty for l in q), D0)
            if qty <= 0:
                continue
            cost = sum((l.cost_gbp for l in q), D0)
            if sym in marks:
                per[sym] = qty * marks[sym] * fx_to_gbp - cost
        return sum(per.values(), D0), per

    def open_qty(self) -> dict[str, Decimal]:
        lots, _, _ = self.build()
        return {s: sum((l.qty for l in q), D0) for s, q in lots.items() if sum((l.qty for l in q), D0) > 0}


class Nav:
    """Unitised NAV so deposits/withdrawals don't distort drawdown or daily-loss checks."""

    def __init__(self, store: Store):
        self.store = store

    def apply_flow(self, amount_gbp: Decimal, equity_gbp_before: Decimal) -> None:
        units = D(self.store.get("nav_units", "0"))
        nav = equity_gbp_before / units if units > 0 else Decimal(1)
        if nav <= 0:
            nav = Decimal(1)
        units += amount_gbp / nav
        self.store.set("nav_units", str(max(units, D0)))

    def update(self, equity_gbp: Decimal) -> tuple[Decimal, Decimal]:
        """Return (nav_per_unit, drawdown_pct) and maintain the high-water mark."""
        units = D(self.store.get("nav_units", "0"))
        if units <= 0:
            if equity_gbp > 0:
                self.store.set("nav_units", str(equity_gbp))
                units = equity_gbp
            else:
                return Decimal(1), D0
        nav = equity_gbp / units
        peak = D(self.store.get("nav_peak", "0"))
        if nav > peak:
            peak = nav
            self.store.set("nav_peak", str(peak))
        dd = (peak - nav) / peak * 100 if peak > 0 else D0
        return nav, dd


def daily_report(store: Store, report_date: str, start: datetime, end: datetime,
                 opex_per_day_gbp: Decimal, usd_to_gbp: Decimal) -> dict[str, Any]:
    """Build the inspectable daily report for the window [start, end)."""
    snap0 = store.one("SELECT * FROM equity_snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (iso(start),)) \
        or store.one("SELECT * FROM equity_snapshots WHERE ts >= ? ORDER BY ts ASC LIMIT 1", (iso(start),))
    snap1 = store.one("SELECT * FROM equity_snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (iso(end),))
    problems = store.query("SELECT ts, level, category, message FROM events WHERE ts >= ? AND ts < ? "
                           "AND level IN ('error','warning','critical') ORDER BY ts", (iso(start), iso(end)))
    if not snap0 or not snap1:
        return {"date": report_date, "status": "no equity data for this period", "operational_problems": problems}
    eq0, eq1 = D(snap0["equity_gbp"]), D(snap1["equity_gbp"])
    u0, u1 = D(snap0["unrealised_gbp"]), D(snap1["unrealised_gbp"])
    flows = store.query("SELECT * FROM cash_flows WHERE ts > ? AND ts <= ?", (snap0["ts"], snap1["ts"]))
    external = sum((D(f["amount"]) * D(f["fx_to_gbp"]) for f in flows if f["kind"] in ("deposit", "withdrawal")), D0)
    other_cash = sum((D(f["amount"]) * D(f["fx_to_gbp"]) for f in flows if f["kind"] not in ("deposit", "withdrawal")), D0)
    _, realised_rows, fills = Ledger(store).build()
    s0, s1 = parse_ts(snap0["ts"]), parse_ts(snap1["ts"])
    realised_win = [r for r in realised_rows if s0 < r.ts <= s1]
    realised = sum((r.realised_gbp for r in realised_win), D0)
    fills_win = [f for f in fills if s0 < parse_ts(f["ts"]) <= s1]
    fees_gbp = sum((D(f["fee"]) * D(f["fx_to_gbp"]) for f in fills_win), D0)
    # Round each component once (half-up, pence) and derive the residual and net from the
    # rounded figures, so the numbers shown to the user always add up exactly.
    q2 = lambda x: D(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # noqa: E731
    trading_pnl = q2(eq1 - eq0 - external)
    realised, d_unreal, other_cash = q2(realised), q2(u1 - u0), q2(other_cash)
    residual = trading_pnl - realised - d_unreal - other_cash
    ai = store.one("SELECT COALESCE(SUM(cost_usd),0) AS c FROM ai_assessments WHERE ts >= ? AND ts < ?",
                   (iso(start), iso(end)))["c"]
    ai_gbp = D(ai) * usd_to_gbp
    manual_opex = sum((D(r["amount_gbp"]) for r in store.query("SELECT amount_gbp FROM opex WHERE day=?",
                                                               (report_date,))), D0)
    opex_infra, ai_gbp, manual_opex = q2(opex_per_day_gbp), q2(ai_gbp), q2(manual_opex)
    opex = opex_infra + ai_gbp + manual_opex
    peak = D(store.get("nav_peak", "0"))
    units = D(store.get("nav_units", "0"))
    dd = ((peak - eq1 / units) / peak * 100) if peak > 0 and units > 0 else D0
    decisions = store.query("SELECT outcome, COUNT(*) AS n FROM decisions WHERE ts >= ? AND ts < ? GROUP BY outcome",
                            (iso(start), iso(end)))
    positions = store.query("SELECT * FROM managed_positions")
    q = lambda x: str(q2(x))  # noqa: E731
    return {
        "date": report_date, "window_utc": [iso(start), iso(end)],
        "equity_start_gbp": q(eq0), "equity_end_gbp": q(eq1),
        "deposits_withdrawals_gbp": q(external),
        "trading_pnl_gbp": q(trading_pnl),
        "realised_pnl_gbp": q(realised), "unrealised_change_gbp": q(d_unreal),
        "unrealised_open_gbp": q(u1),
        "dividends_interest_cash_fees_gbp": q(other_cash),
        "fx_and_other_residual_gbp": q(residual),
        "broker_fees_gbp": q(fees_gbp),
        "operating_costs_gbp": {"infrastructure_and_data": q(opex_infra), "ai": q(ai_gbp),
                                "other": q(manual_opex), "total": q(opex)},
        "net_after_operating_costs_gbp": q(trading_pnl - opex),
        "return_on_start_equity_pct": str(((trading_pnl / eq0) * 100).quantize(Decimal("0.01"))) if eq0 > 0 else None,
        "drawdown_from_peak_pct": str(dd.quantize(Decimal("0.01"))),
        "significant_trades": [{"ts": iso(r.ts), "symbol": r.symbol, "qty": str(r.qty),
                                "realised_gbp": q(r.realised_gbp), "return_pct": str(r.ret_pct.quantize(Decimal("0.01"))),
                                "strategy": r.strategy} for r in sorted(realised_win, key=lambda r: -abs(r.realised_gbp))[:10]],
        "fills": len(fills_win),
        "decisions": {d["outcome"]: d["n"] for d in decisions},
        "outstanding_exposure": [{"symbol": p["symbol"], "strategy": p["strategy"], "stop_mode": p["stop_mode"],
                                  "stop_price": p["stop_price"]} for p in positions],
        "fx_rate_usd_gbp": snap1["fx_to_gbp"],
        "operational_problems": problems[-50:],
        "notes": ["Deposits/withdrawals are excluded from P&L.",
                  "Unrealised P&L includes all open positions, winning or losing.",
                  "Broker fees on Alpaca are estimated from published regulatory fee rates; "
                  "any difference appears in the FX/other residual."],
    }
