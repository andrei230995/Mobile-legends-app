"""The autonomous trading loop.

One ``tick`` (every ~20 s on the server) performs, in order:

1. Apply queued user commands (pause, cancel, close ...).
2. Read account and positions from the broker (the source of truth). On failure: no
   new entries; broker-held stops keep working; the user is alerted after repeated
   failures; credential failures halt the engine.
3. Reconcile orders whose outcome is unknown, then sync open orders and record fills.
4. Record deposits/withdrawals, mark to market in GBP, update NAV, daily P&L, drawdown.
5. Enforce loss limits (pause entries / flatten per configuration) and reconcile
   local positions against the broker (mismatch -> entries blocked, user alerted).
6. Manage open positions: protective stops, software stops, strategy exits, time
   exits, data-outage exits, flat-by-close. These run even when entries are paused.
7. Look for entries during the entry window (once per session per strategy/symbol);
   every candidate - taken or not - is recorded with the reasons.
8. Optional AI news assessment (shadow/veto), daily report, heartbeat.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .accounting import Ledger, Nav, daily_report
from .ai.news import NewsAssessor
from .brokers.base import Broker, BrokerAuthError, BrokerError
from .clock import Clock, MarketCalendar, local_report_due
from .config import RiskLimits, Settings
from .data.base import DataUnavailable, MarketData
from .data.fx import FxProvider, FxUnavailable
from .db import Store, dumps, iso, parse_ts
from .execution import ExecutionEngine, make_client_order_id
from .models import (D0, Account, D, Mode, OrderRequest, OrderStatus, OrderType, Position, Purpose, Quote,
                     Side, Signal)
from .notify import Notifier
from .risk import RiskContext, RiskEngine
from .strategies import Strategy, bars_to_frame, build

log = logging.getLogger("tradebot.engine")

TRANSIENT_CHECKS = {"quote_available", "quote_fresh", "spread_ok", "within_entry_window",
                    "reconciliation_ok", "quote_not_delayed"}
ENTRY_ORDER_TTL = timedelta(minutes=10)
EXIT_AFTER_OPEN_MIN = 2
DATA_OUTAGE_EXIT = timedelta(minutes=15)


@dataclass
class StrategySlot:
    key: str                      # e.g. "rsi2_mr:SPY"
    strategy: Strategy
    symbol: str
    status: str                   # rejected | paper | approved_live
    expected_edge_bps: float | None
    validation: dict[str, Any] = field(default_factory=dict)


def load_registry(results_path: Path) -> list[dict[str, Any]]:
    if not results_path.exists():
        return []
    rep = json.loads(results_path.read_text())
    out = []
    for r in rep.get("results", []):
        params = r["param_choices"][-1]["params"] if r.get("param_choices") else {}
        out.append({"key": f"{r['strategy']}:{r['symbol']}", "family": r["strategy"], "symbol": r["symbol"],
                    "params": params, "backtest_pass": r["backtest_criteria_pass"],
                    "checks": r["checks"], "expected_edge_bps": r.get("expected_edge_bps"),
                    "source": rep.get("source"), "data_end": r.get("data_end"),
                    "generated_at": rep.get("generated_at")})
    return out


class Engine:
    def __init__(self, settings: Settings, store: Store, broker: Broker, data: MarketData, fx: FxProvider,
                 clock: Clock, notifier: Notifier, assessor: NewsAssessor | None = None,
                 registry: list[dict[str, Any]] | None = None, control: Store | None = None):
        self.ctl = control or store          # global control state (mode, switches)
        self.s, self.store, self.broker, self.data, self.fx = settings, store, broker, data, fx
        store.now = self.ctl.now = clock.now
        self.clock, self.notifier, self.assessor = clock, notifier, assessor
        self.cal = MarketCalendar()
        self.risk = RiskEngine()
        self.lock = threading.RLock()
        self.exec = ExecutionEngine(store, broker, clock, self.mode, self._fx_rate)
        self.ledger, self.nav = Ledger(store), Nav(store)
        self._quotes: dict[str, Quote] = {}
        self._fx_cache: tuple[Decimal, str, datetime] | None = None
        self.slots: list[StrategySlot] = []
        self._init_state(registry or [])

    # ------------------------------------------------------------------ state
    def _init_state(self, registry: list[dict[str, Any]]) -> None:
        st = self.store
        if self.ctl.get("mode") is None:
            self.ctl.set("env", "paper")
            self.ctl.set("mode", Mode.PAPER.value)          # always start in paper
            self.ctl.set("trading_enabled", False)
            self.ctl.set("entries_paused", False)
            self.ctl.set("halted_reason", None)
            st.set("paper_started_at", iso(self.clock.now()))
            st.event("info", "engine", "state initialised in PAPER mode, trading disabled")
        if st.get("paper_started_at") is None and self.broker.capabilities.is_paper:
            st.set("paper_started_at", iso(self.clock.now()))
        if self.ctl.one("SELECT 1 FROM risk_limits_versions") is None:
            self.ctl.execute("INSERT INTO risk_limits_versions(ts,body,note) VALUES(?,?,?)",
                       (iso(self.clock.now()), RiskLimits().model_dump_json(), "defaults"))
        for r in registry:
            existing = st.one("SELECT status FROM strategy_registry WHERE name=?", (r["key"],))
            # Paper trading is for gathering forward evidence, so any strategy with a positive
            # out-of-sample net return may paper trade. Live needs every promotion check.
            status = "paper" if r["checks"].get("oos_net_return_positive") else "rejected"
            if existing and existing["status"] == "approved_live" and r["backtest_pass"]:
                status = "approved_live"
            st.execute("INSERT INTO strategy_registry(name,version,status,validation,updated_at) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(name) DO UPDATE SET version=excluded.version, status=excluded.status, "
                       "validation=excluded.validation, updated_at=excluded.updated_at",
                       (r["key"], dumps(r["params"]), status, dumps(r), iso(self.clock.now())))
        self.reload_slots()

    def reload_slots(self) -> None:
        self.slots = []
        for row in self.store.query("SELECT * FROM strategy_registry ORDER BY name"):
            v = json.loads(row["validation"] or "{}")
            family, symbol = row["name"].split(":")
            strat = build(family, v.get("params") or {})
            self.slots.append(StrategySlot(row["name"], strat, symbol, row["status"],
                                           v.get("expected_edge_bps"), v))

    def mode(self) -> Mode:
        return Mode(self.ctl.get("mode", Mode.PAPER.value))

    def limits(self) -> RiskLimits:
        row = self.ctl.one("SELECT body FROM risk_limits_versions ORDER BY version DESC LIMIT 1")
        return RiskLimits.model_validate_json(row["body"])

    def _fx_rate(self, currency: str) -> tuple[Decimal, str]:
        r = self.fx.to_gbp(currency)
        return r.rate, f"{r.source}@{r.ts.isoformat()}"

    def usd_to_gbp(self) -> Decimal:
        try:
            return self.fx.to_gbp("USD").rate
        except FxUnavailable:
            return Decimal("0.75")

    def _decision(self, action: str, outcome: str, summary: str, strategy: str | None = None,
                  symbol: str | None = None, checks: list | None = None, evidence: Any = None) -> int:
        cur = self.store.execute(
            "INSERT INTO decisions(ts,mode,strategy,symbol,action,outcome,summary,checks,evidence) VALUES(?,?,?,?,?,?,?,?,?)",
            (iso(self.clock.now()), self.mode().value, strategy, symbol, action, outcome, summary,
             dumps([c.as_dict() for c in checks]) if checks else None, dumps(evidence) if evidence else None))
        return cur.lastrowid

    def _alert_once(self, key: str, kind: str, title: str, body: str, level: str = "warning") -> None:
        """Notify once per distinct condition until it clears."""
        active = self.store.get("active_alerts", {})
        if key in active:
            return
        active[key] = iso(self.clock.now())
        self.store.set("active_alerts", active)
        self.store.event(level, "alert", title, {"body": body})
        self.notifier.send(kind, title, body)

    def _clear_alert(self, key: str, note: str | None = None) -> None:
        active = self.store.get("active_alerts", {})
        if key in active:
            active.pop(key)
            self.store.set("active_alerts", active)
            if note:
                self.store.event("info", "alert", f"resolved: {note}")

    # ------------------------------------------------------------------ user commands
    def command(self, name: str, **kw: Any) -> dict[str, Any]:
        """Entry point for UI controls. Runs under the engine lock."""
        with self.lock:
            now = self.clock.now()
            self.store.event("info", "control", f"user command: {name}", kw)
            if name == "pause_entries":
                self.ctl.set("entries_paused", True)
                return {"ok": True, "note": "New entries paused. Open positions stay protected and managed."}
            if name == "resume_entries":
                self.ctl.set("entries_paused", False)
                return {"ok": True}
            if name == "start":
                self.ctl.set("trading_enabled", True)
                self.ctl.set("halted_reason", None)
                return {"ok": True}
            if name == "stop":
                self.ctl.set("trading_enabled", False)
                return {"ok": True, "note": "No new entries. Exits and protective orders continue."}
            if name == "cancel_pending_entries":
                n = sum(self.exec.cancel(r["client_order_id"], "user cancelled pending entries")
                        for r in self.exec.open_rows(Purpose.ENTRY) if r["broker_order_id"])
                return {"ok": True, "cancelled": n}
            if name in ("close_position", "close_all"):
                symbols = [kw["symbol"]] if name == "close_position" else [
                    p["symbol"] for p in self.store.query("SELECT symbol FROM managed_positions")]
                if name == "close_all":
                    symbols += [p.symbol for p in self._safe_positions() if p.symbol not in symbols]
                results = {}
                for sym in symbols:
                    results[sym] = self._request_exit(sym, "manual close by user", now, immediate=True)
                return {"ok": True, "results": results}
            raise ValueError(f"unknown command {name}")

    def _safe_positions(self) -> list[Position]:
        try:
            return self.broker.get_positions()
        except BrokerError:
            return []

    # ------------------------------------------------------------------ main loop
    def tick(self) -> dict[str, Any]:
        with self.lock:
            return self._tick()

    def _tick(self) -> dict[str, Any]:
        now = self.clock.now()
        mode = self.mode()
        L = self.limits()
        summary: dict[str, Any] = {"ts": iso(now), "mode": mode.value}
        # -- broker state ---------------------------------------------------------
        try:
            account = self.broker.get_account()
            positions = self.broker.get_positions()
            self.exec.reconcile_unresolved()
            self.exec.sync_open()
            open_orders = self.broker.list_open_orders()
        except BrokerAuthError as e:
            self.ctl.set("halted_reason", f"broker credentials rejected: {e}")
            self.store.set("broker_status", {"ok": False, "error": str(e), "at": iso(now)})
            self._alert_once("auth", "critical", "Trading halted: broker credentials rejected",
                             "The broker rejected the API credentials. No orders can be placed or managed "
                             "by the bot. Broker-held stop orders remain active. Rotate the keys on the server.",
                             level="critical")
            self.notifier.ping_health(False, "broker auth failure")
            return {**summary, "error": "auth"}
        except BrokerError as e:
            fails = self.store.get("broker_failures", 0) + 1
            self.store.set("broker_failures", fails)
            self.store.set("broker_status", {"ok": False, "error": f"{type(e).__name__}: {e}", "at": iso(now),
                                             "consecutive_failures": fails})
            self.store.event("warning", "broker", f"broker unreachable ({fails}): {e}")
            if fails >= 3:
                self._alert_once("broker_down", "failure", "Broker connection lost",
                                 f"{fails} consecutive failures ({type(e).__name__}). No new entries. "
                                 "Software-managed exits cannot run until the connection returns; "
                                 "broker-held stop orders remain active at the broker.")
                self.notifier.ping_health(False, "broker unreachable")
            return {**summary, "error": "broker_unavailable"}
        self.store.set("broker_failures", 0)
        self.store.set("broker_status", {"ok": True, "at": iso(now), "broker": self.broker.capabilities.name,
                                         "paper": self.broker.capabilities.is_paper})
        self._clear_alert("broker_down", "broker connection restored")
        self._clear_alert("auth")

        # -- FX, flows, marks ---------------------------------------------------------
        try:
            fx, fx_src = self._fx_rate(account.currency)
            self.store.set("fx_status", {"ok": True, "rate": str(fx), "source": fx_src})
        except FxUnavailable as e:
            self.store.set("fx_status", {"ok": False, "error": str(e)})
            fx, fx_src = (Decimal(1) if account.currency == "GBP" else self.usd_to_gbp()), "fallback"
        equity_gbp = account.equity * fx
        self._sync_cash_flows(now, fx, equity_gbp)
        self._refresh_quotes(L, positions, now)
        marks = {p.symbol: p.market_price for p in positions}
        unreal, _ = self.ledger.unrealised_gbp(marks, fx)
        nav, dd = self.nav.update(equity_gbp)
        anchor = self._day_anchor(now, L, nav, equity_gbp)
        day_pnl_pct = (nav / D(anchor["nav"]) - 1) * 100 if D(anchor["nav"]) > 0 else D0
        self._snapshot(now, account, fx, equity_gbp, unreal)
        summary.update(equity_gbp=str(equity_gbp.quantize(Decimal("0.01"))), drawdown_pct=str(dd.quantize(Decimal("0.01"))),
                       day_pnl_pct=str(day_pnl_pct.quantize(Decimal("0.01"))))

        # -- limits -----------------------------------------------------------------
        self._enforce_limits(L, day_pnl_pct, dd, now, anchor)
        recon_ok = self._reconcile_positions(positions, open_orders)

        # -- sessions ------------------------------------------------------------------
        mclock = self.cal.clock(now)
        broker_clock = None
        try:
            broker_clock = self.broker.get_clock()
        except BrokerError:
            pass
        market_open = mclock.is_open and (broker_clock is None or broker_clock.is_open)
        since_open = (now - mclock.session_open).total_seconds() / 60 if market_open else None
        to_close = (mclock.session_close - now).total_seconds() / 60 if market_open else None
        session = self.cal.session_date(now).isoformat()

        # -- manage positions (always, even when paused) ----------------------------------
        self._manage_positions(L, positions, market_open, since_open, to_close, session, now, mclock)

        # -- entries --------------------------------------------------------------------
        if market_open and mode != Mode.READ_ONLY:
            self._expire_stale_entries(now)
        if market_open:
            ctx_base = dict(now=now, mode=mode, limits=L, caps=self.broker.capabilities,
                            fee_model=self.broker.fee_model, account=account, positions=positions,
                            open_orders=open_orders, trading_enabled=bool(self.ctl.get("trading_enabled")),
                            entries_paused=bool(self.ctl.get("entries_paused")),
                            halted_reason=self.ctl.get("halted_reason"), market_open=market_open,
                            minutes_since_open=since_open, minutes_to_close=to_close, day_pnl_pct=day_pnl_pct,
                            drawdown_pct=dd, entries_today=self._entries_today(anchor),
                            orders_today=self.exec.orders_today(parse_ts(anchor["since"])),
                            turnover_today_pct=self._turnover_today_pct(anchor, equity_gbp, fx),
                            data_is_replay=self.data.is_replay, reconciliation_ok=recon_ok)
            self._look_for_entries(L, ctx_base, session, now, since_open)

        # -- AI, reports, heartbeat ----------------------------------------------------------
        self._ai_news(L, now, anchor)
        self._daily_report_if_due(L, now)
        self.store.set("heartbeat", iso(now))
        self.notifier.ping_health(True)
        return summary

    # ------------------------------------------------------------------ helpers: accounting
    def _sync_cash_flows(self, now: datetime, fx: Decimal, equity_gbp: Decimal) -> None:
        since = parse_ts(self.store.get("cash_flow_cursor")) or (now - timedelta(days=30))
        try:
            flows = self.broker.get_cash_flows(since)
        except BrokerError:
            return
        newest = since
        for f in flows:
            if self.store.one("SELECT 1 FROM cash_flows WHERE flow_id=?", (f.flow_id,)):
                continue
            amt_gbp = f.amount * fx
            self.store.execute("INSERT INTO cash_flows(flow_id,ts,kind,amount,currency,fx_to_gbp,source) VALUES(?,?,?,?,?,?,?)",
                               (f.flow_id, iso(f.ts), f.kind, str(f.amount), f.currency, str(fx), "broker"))
            if f.kind in ("deposit", "withdrawal"):
                # units are issued/redeemed at NAV *before* the flow landed
                self.nav.apply_flow(amt_gbp, equity_gbp - amt_gbp)
                self.store.event("info", "cash", f"{f.kind} of {f.amount} {f.currency} recorded (not profit)")
            newest = max(newest, f.ts)
        self.store.set("cash_flow_cursor", iso(newest))

    def _snapshot(self, now: datetime, account: Account, fx: Decimal, equity_gbp: Decimal, unreal: Decimal) -> None:
        last = parse_ts(self.store.get("last_snapshot"))
        if last and now - last < timedelta(minutes=5):
            return
        self.store.execute("INSERT OR REPLACE INTO equity_snapshots VALUES(?,?,?,?,?,?,?)",
                           (iso(now), account.currency, str(account.equity), str(account.cash), str(fx),
                            str(equity_gbp), str(unreal)))
        self.store.set("last_snapshot", iso(now))

    def _day_anchor(self, now: datetime, L: RiskLimits, nav: Decimal, equity_gbp: Decimal) -> dict[str, Any]:
        """The reporting day runs from one report time to the next (Europe/London by default)."""
        rdate, due = local_report_due(now, L.report_time_local, L.report_timezone)
        day_key = (rdate if now < due else rdate + timedelta(days=1)).isoformat()
        anchor = self.store.get("day_anchor")
        if not anchor or anchor["day"] != day_key:
            anchor = {"day": day_key, "nav": str(nav), "equity_gbp": str(equity_gbp), "since": iso(now)}
            self.store.set("day_anchor", anchor)
            if self.ctl.get("entries_paused_reason") == "daily_loss":
                self.ctl.set("entries_paused", False)
                self.ctl.set("entries_paused_reason", None)
                self.store.event("info", "risk", "daily loss pause lifted at start of new reporting day")
                self._clear_alert("daily_loss")
        return anchor

    def _entries_today(self, anchor: dict[str, Any]) -> int:
        return self.store.one("SELECT COUNT(*) AS n FROM orders WHERE purpose='entry' AND created_at >= ? "
                              "AND status NOT IN ('not_sent','rejected')", (anchor["since"],))["n"]

    def _turnover_today_pct(self, anchor: dict[str, Any], equity_gbp: Decimal, fx: Decimal) -> Decimal:
        rows = self.store.query("SELECT qty, price FROM fills WHERE ts >= ?", (anchor["since"],))
        traded = sum((D(r["qty"]) * D(r["price"]) for r in rows), D0) * fx
        return traded / equity_gbp * 100 if equity_gbp > 0 else D0

    # ------------------------------------------------------------------ helpers: risk state
    def _enforce_limits(self, L: RiskLimits, day_pnl_pct: Decimal, dd: Decimal, now: datetime,
                        anchor: dict[str, Any]) -> None:
        if day_pnl_pct <= -D(L.daily_loss_limit_pct):
            if not self.ctl.get("entries_paused"):
                self.ctl.set("entries_paused", True)
                self.ctl.set("entries_paused_reason", "daily_loss")
            self._cancel_pending_entries("daily loss limit")
            self._alert_once("daily_loss", "risk", "Daily loss limit reached",
                             f"Today's result {day_pnl_pct:.2f}% (incl. open positions) breached -{L.daily_loss_limit_pct}%. "
                             "New entries paused until the next reporting day; pending entries cancelled; "
                             "open positions keep their stops and exits.")
        if dd >= D(L.max_drawdown_pct):
            if not self.ctl.get("halted_reason"):
                self.ctl.set("halted_reason", f"max drawdown {dd:.2f}% >= {L.max_drawdown_pct}%")
            self._cancel_pending_entries("max drawdown")
            flatten = L.on_drawdown_breach == "flatten"
            self._alert_once("drawdown", "risk", "Maximum drawdown reached - entries halted",
                             f"Drawdown {dd:.2f}% from the NAV peak. New entries halted until you restart trading. "
                             + ("All positions are being closed (configured: flatten)." if flatten
                                else "Open positions remain managed by their stops/exits."))
            if flatten:
                for p in self.store.query("SELECT symbol FROM managed_positions"):
                    self._request_exit(p["symbol"], "max drawdown flatten", now, immediate=True)

    def _cancel_pending_entries(self, reason: str) -> None:
        for r in self.exec.open_rows(Purpose.ENTRY):
            if r["broker_order_id"]:
                self.exec.cancel(r["client_order_id"], reason)

    def _reconcile_positions(self, positions: list[Position], open_orders) -> bool:
        broker_qty = {p.symbol: p.qty for p in positions}
        local_qty = self.ledger.open_qty()
        issues = []
        for sym in set(broker_qty) | set(local_qty):
            b, l = broker_qty.get(sym, D0), local_qty.get(sym, D0)
            if abs(b - l) > Decimal("0.000001"):
                managed = self.store.one("SELECT 1 FROM managed_positions WHERE symbol=?", (sym,))
                if managed or l > 0:
                    issues.append(f"{sym}: broker {b} vs bot ledger {l}")
                else:
                    self.store.set(f"external_position:{sym}", str(b))   # held outside the bot
        self.store.set("reconciliation", {"ok": not issues, "issues": issues, "at": iso(self.clock.now())})
        if issues:
            self._alert_once("recon", "failure", "Position mismatch - entries blocked",
                             "Broker and bot records disagree: " + "; ".join(issues) +
                             ". New entries are blocked until they agree. Exits remain available.")
        else:
            self._clear_alert("recon", "positions reconciled")
        return not issues

    # ------------------------------------------------------------------ helpers: data
    def _refresh_quotes(self, L: RiskLimits, positions: list[Position], now: datetime) -> None:
        status = {}
        for sym in sorted(set(L.symbols) | {p.symbol for p in positions}):
            try:
                q = self.data.latest_quote(sym)
                self._quotes[sym] = q
                status[sym] = {"ok": q.valid, "age_s": round(q.age_seconds(now), 1), "source": q.source,
                               "feed": q.feed, "delayed": q.delayed, "replay": self.data.is_replay,
                               "bid": str(q.bid), "ask": str(q.ask), "spread_bps": round(float(q.spread_bps), 2),
                               "quote_ts": iso(q.ts)}
            except DataUnavailable as e:
                self._quotes.pop(sym, None)
                status[sym] = {"ok": False, "error": str(e)}
        self.store.set("data_status", {"at": iso(now), "provider": self.data.name, "symbols": status})

    def _fresh_quote(self, sym: str, L: RiskLimits, now: datetime) -> Quote | None:
        q = self._quotes.get(sym)
        if q and q.valid and q.age_seconds(now) <= L.max_quote_age_seconds:
            return q
        return None

    def _bars(self, sym: str, n: int, now: datetime):
        bars = self.data.daily_bars(sym, n, now)
        if not bars:
            raise DataUnavailable(f"no daily bars for {sym}")
        return bars

    # ------------------------------------------------------------------ positions & exits
    def _manage_positions(self, L, positions, market_open, since_open, to_close, session, now, mclock) -> None:
        held = {p.symbol: p for p in positions}
        mode = self.mode()
        for mp in self.store.query("SELECT * FROM managed_positions"):
            sym = mp["symbol"]
            pos = held.get(sym)
            entry = self.store.get_order(mp["entry_cid"])
            entry_open = entry and not OrderStatus(entry["status"]).is_terminal
            if (pos is None or pos.qty <= 0) and not entry_open:
                self._on_position_closed(mp, now)
                continue
            if pos is None:
                continue
            slot = next((s for s in self.slots if s.key == mp["strategy"]), None)
            # strategy exit evaluation on completed daily bars (after the close)
            if slot and not mp["exit_pending_reason"] and (not market_open) and mclock.session_close \
                    and now >= mclock.session_close and self.store.get(f"exit_eval:{sym}") != session:
                try:
                    df = bars_to_frame(self._bars(sym, slot.strategy.history_needed + 10, now))
                    held_sessions = self.cal.sessions_between(date.fromisoformat(mp["session_date"]),
                                                              self.cal.session_date(now)) + 1
                    reason = slot.strategy.exit(df, held_sessions)
                    self.store.set(f"exit_eval:{sym}", session)
                    self._decision("exit_check", "exit_signal" if reason else "hold",
                                   reason or "exit rule not met; holding", mp["strategy"], sym,
                                   evidence={"bars_held": held_sessions, "last_close": df.close.iloc[-1]})
                    if reason:
                        self.store.execute("UPDATE managed_positions SET exit_pending_reason=? WHERE symbol=?",
                                           (reason, sym))
                        mp["exit_pending_reason"] = reason
                except DataUnavailable as e:
                    self.store.event("warning", "data", f"exit evaluation skipped for {sym}: {e}")
            if not market_open:
                continue
            q = self._fresh_quote(sym, L, now)
            # data outage while relying on a software stop
            if q is None and mp["stop_mode"] == "software":
                since = self.store.get(f"no_data_since:{sym}") or iso(now)
                self.store.set(f"no_data_since:{sym}", since)
                self._alert_once(f"nodata:{sym}", "failure", f"No fresh prices for {sym}",
                                 f"The software stop for {sym} cannot be monitored. The position will be closed "
                                 f"at market if prices do not return within {int(DATA_OUTAGE_EXIT.total_seconds() // 60)} minutes.")
                if now - parse_ts(since) >= DATA_OUTAGE_EXIT:
                    self._request_exit(sym, "price data unavailable; software stop unmonitored", now)
                continue
            self.store.set(f"no_data_since:{sym}", None)
            self._clear_alert(f"nodata:{sym}")
            reason = mp["exit_pending_reason"]
            if not reason and q and mp["stop_price"] and mp["stop_mode"] == "software" and q.bid <= D(mp["stop_price"]):
                reason = f"software stop: bid {q.bid} <= stop {mp['stop_price']}"
            if not reason and L.flat_by_close and to_close is not None and to_close <= L.flat_minutes_before_close:
                reason = "flat-by-close setting"
            if reason and (since_open or 0) >= EXIT_AFTER_OPEN_MIN:
                self._request_exit(sym, reason, now)
                continue
            # protective stop at the broker once the entry is complete
            if not entry_open and mode != Mode.READ_ONLY and mp["stop_price"]:
                self._ensure_protective_stop(mp, pos, session, now)

    def _ensure_protective_stop(self, mp: dict[str, Any], pos: Position, session: str, now: datetime) -> None:
        caps = self.broker.capabilities
        whole = pos.qty == pos.qty.to_integral_value()
        if "stop" not in caps.order_types or not (whole or caps.protective_stop_for_fractional):
            if mp["stop_mode"] != "software":
                self.store.execute("UPDATE managed_positions SET stop_mode='software' WHERE symbol=?", (mp["symbol"],))
            return
        cid = mp["protective_cid"]
        row = self.store.get_order(cid) if cid else None
        if row and not OrderStatus(row["status"]).is_terminal:
            return
        if row and row["status"] == OrderStatus.FILLED.value:
            return
        tif = caps.protective_stop_tif
        sess_tag = session if tif == "day" else mp["session_date"]
        attempt = self.store.one("SELECT COUNT(*) AS n FROM orders WHERE symbol=? AND purpose='protective_stop' "
                                 "AND client_order_id LIKE ?", (mp["symbol"], f"%{sess_tag}%"))["n"]
        new_cid = make_client_order_id(mp["strategy"].split(":")[0], mp["symbol"], Purpose.PROTECTIVE_STOP,
                                       sess_tag, attempt)
        req = OrderRequest(new_cid, mp["symbol"], Side.SELL, OrderType.STOP, qty=pos.qty,
                           stop_price=D(mp["stop_price"]), tif=tif)
        res = self.exec.submit(req, Purpose.PROTECTIVE_STOP, mp["strategy"])
        ok = res["status"] in (OrderStatus.ACCEPTED.value, OrderStatus.PARTIALLY_FILLED.value, OrderStatus.FILLED.value)
        self.store.execute("UPDATE managed_positions SET protective_cid=?, stop_mode=? WHERE symbol=?",
                           (new_cid, "broker" if ok else "software", mp["symbol"]))
        if not ok:
            self.store.event("warning", "execution", f"protective stop for {mp['symbol']} not accepted "
                             f"({res['status']}); software stop active")

    def _request_exit(self, sym: str, reason: str, now: datetime, immediate: bool = False) -> str:
        mode = self.mode()
        mp = self.store.one("SELECT * FROM managed_positions WHERE symbol=?", (sym,))
        strategy = mp["strategy"] if mp else "manual"
        if mode == Mode.READ_ONLY:
            self._decision("exit", "not_executed", f"read-only mode: would exit {sym} ({reason})", strategy, sym)
            return "read-only: not executed"
        if mp:
            self.store.execute("UPDATE managed_positions SET exit_pending_reason=? WHERE symbol=?", (reason, sym))
        mclock = self.cal.clock(now)
        if not mclock.is_open:
            self._decision("exit", "queued", f"exit queued for next session open: {reason}", strategy, sym)
            return "queued for next session (market closed)"
        # cancel every other open order for the symbol first (protective stop included)
        for r in self.exec.open_rows(symbol=sym):
            if r["purpose"] == Purpose.EXIT.value:
                return "exit already working"
            if r["broker_order_id"] and not self.exec.cancel(r["client_order_id"], f"making way for exit: {reason}"):
                self.store.event("warning", "execution", f"could not confirm cancel of {r['client_order_id']}; exit deferred")
                return "deferred: waiting for cancel confirmation"
            if not r["broker_order_id"]:
                return "deferred: unresolved order outstanding"
        pos = next((p for p in self._safe_positions() if p.symbol == sym), None)
        if pos is None or pos.qty <= 0:
            return "no position at broker"
        session = self.cal.session_date(now).isoformat()
        attempt = self.store.one("SELECT COUNT(*) AS n FROM orders WHERE symbol=? AND purpose='exit' AND "
                                 "client_order_id LIKE ?", (sym, f"%{session}%"))["n"]
        cid = make_client_order_id(strategy.split(":")[0], sym, Purpose.EXIT, session, attempt)
        did = self._decision("exit", "submitted", reason, strategy, sym,
                             evidence={"qty": str(pos.qty), "quote": self._quote_ev(sym)})
        res = self.exec.submit(OrderRequest(cid, sym, Side.SELL, OrderType.MARKET, qty=pos.qty),
                               Purpose.EXIT, strategy, did)
        return res["status"]

    def _on_position_closed(self, mp: dict[str, Any], now: datetime) -> None:
        for r in self.exec.open_rows(symbol=mp["symbol"]):
            if r["broker_order_id"]:
                self.exec.cancel(r["client_order_id"], "position closed")
        _, realised, _ = self.ledger.build()
        last = next((r for r in reversed(realised) if r.symbol == mp["symbol"]), None)
        self.store.execute("DELETE FROM managed_positions WHERE symbol=?", (mp["symbol"],))
        entry = self.store.get_order(mp["entry_cid"])
        if entry and D(entry["filled_qty"]) == 0:
            self._decision("entry", "not_filled", f"entry order {entry['status']} with no fill", mp["strategy"], mp["symbol"])
            return
        msg = f"{mp['symbol']} position closed" + (
            f": realised £{last.realised_gbp:.2f} ({last.ret_pct:.2f}%)" if last else "")
        self.store.event("info", "position", msg)
        self.notifier.send("fill", msg, f"Strategy {mp['strategy']}. Reason: {mp['exit_pending_reason'] or 'stop/exit fill'}")

    # ------------------------------------------------------------------ entries
    def _expire_stale_entries(self, now: datetime) -> None:
        for r in self.exec.open_rows(Purpose.ENTRY):
            if r["broker_order_id"] and now - parse_ts(r["created_at"]) > ENTRY_ORDER_TTL:
                self.exec.cancel(r["client_order_id"], "entry not completely filled within TTL")

    def _quote_ev(self, sym: str) -> dict[str, Any] | None:
        q = self._quotes.get(sym)
        return None if q is None else {"bid": str(q.bid), "ask": str(q.ask), "ts": iso(q.ts), "source": q.source,
                                       "feed": q.feed, "spread_bps": round(float(q.spread_bps), 2)}

    def _look_for_entries(self, L: RiskLimits, ctx_base: dict[str, Any], session: str, now: datetime,
                          since_open: float | None) -> None:
        lo, hi = L.entry_window_minutes_after_open
        if since_open is None or since_open < lo or since_open > hi:
            return
        for slot in self.slots:
            if slot.symbol not in L.symbols or slot.status == "rejected":
                continue
            done_key = f"entry_eval:{session}:{slot.key}"
            if self.store.get(done_key):
                continue
            if self.store.one("SELECT 1 FROM managed_positions WHERE symbol=?", (slot.symbol,)):
                continue      # one position per symbol (checked again by the risk engine)
            try:
                bars = self._bars(slot.symbol, slot.strategy.history_needed + 10, now)
            except DataUnavailable as e:
                self._decision("entry", "rejected", f"daily bars unavailable: {e}", slot.key, slot.symbol)
                self.store.set(done_key, True)
                continue
            prev = self.cal.previous_sessions(self.cal.session_date(now), 1)[0]
            if bars[-1].day != prev:
                self._decision("entry", "rejected", f"daily bars stale: last bar {bars[-1].day}, expected {prev}",
                               slot.key, slot.symbol, evidence={"source": bars[-1].source})
                self.store.set(done_key, True)
                continue
            df = bars_to_frame(bars)
            ev = slot.strategy.entry(df)
            if ev is None:
                self._decision("entry", "no_signal", "entry rule not met; staying in cash", slot.key, slot.symbol,
                               evidence={"last_close": df.close.iloc[-1], "last_bar": str(bars[-1].day),
                                         "bar_source": bars[-1].source})
                self.store.set(done_key, True)
                continue
            sig = Signal(slot.key, slot.strategy.version, slot.symbol, "enter", ev.get("rule", ""), ev,
                         stop_pct=slot.strategy.stop_pct, max_hold_days=slot.strategy.max_hold_days,
                         expected_edge_bps=slot.expected_edge_bps)
            quote = self._quotes.get(slot.symbol)
            try:
                asset = self.broker.get_asset(slot.symbol)
            except BrokerError:
                asset = None
            veto = self.assessor.veto_for(slot.symbol, now) if self.assessor else None
            ctx = RiskContext(**ctx_base, strategy_status=slot.status, ai_veto=veto)
            dec = self.risk.evaluate_entry(sig, ctx, quote, asset)
            evidence = {"signal": ev, "quote": self._quote_ev(slot.symbol), "expected_edge_bps": slot.expected_edge_bps,
                        "est_cost_bps": str(dec.est_cost_bps) if dec.est_cost_bps is not None else None,
                        "qty": str(dec.qty), "est_value": str(dec.notional), "stop_price": str(dec.stop_price)}
            failed = {c.name for c in dec.checks if not c.passed}
            if not dec.approved:
                self._decision("entry", "rejected", dec.summary, slot.key, slot.symbol, dec.checks, evidence)
                if not failed <= TRANSIENT_CHECKS:
                    self.store.set(done_key, True)
                continue
            did = self._decision("entry", "approved", f"entering: {sig.reason}", slot.key, slot.symbol, dec.checks, evidence)
            cid = make_client_order_id(slot.strategy.name, slot.symbol, Purpose.ENTRY, session)
            self.store.set(done_key, True)
            if self.mode() == Mode.READ_ONLY:
                continue
            res = self.exec.submit(OrderRequest(cid, slot.symbol, Side.BUY, OrderType.MARKET, qty=dec.qty),
                                   Purpose.ENTRY, slot.key, did)
            if res["status"] in (OrderStatus.REJECTED.value, OrderStatus.NOT_SENT.value):
                self.notifier.send("failure", f"Entry order {res['status']}: {slot.symbol}", res.get("last_error") or "")
                if res["status"] == OrderStatus.NOT_SENT.value:
                    self.store.set(done_key, False)       # safe to retry within the window
                continue
            self.store.execute(
                "INSERT OR REPLACE INTO managed_positions(symbol,strategy,strategy_version,opened_at,session_date,stop_price,"
                "stop_mode,protective_cid,max_hold_until,entry_cid,exit_pending_reason) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                (slot.symbol, slot.key, slot.strategy.version, iso(now), session,
                 str(dec.stop_price) if dec.stop_price else None, "software" if dec.stop_price else "none", None,
                 None, cid))
            self.notifier.send("fill", f"Entry submitted: {slot.symbol}",
                               f"{slot.key}: buy {dec.qty} (~{dec.notional:.2f} {ctx.account.currency}). "
                               f"Stop {dec.stop_price}. Reason: {sig.reason}")

    # ------------------------------------------------------------------ AI & reporting
    def _ai_news(self, L: RiskLimits, now: datetime, anchor: dict[str, Any]) -> None:
        if not self.assessor or not self.assessor.enabled:
            return
        last = parse_ts(self.store.get("news_cursor"))
        if last and now - last < timedelta(minutes=10):
            return
        since = last or now - timedelta(hours=12)
        try:
            items = self.data.news(L.symbols, since)
        except DataUnavailable as e:
            self.store.event("warning", "data", f"news unavailable: {e}")
            return
        for it in items:
            for sym in [s for s in it.symbols if s in L.symbols][:2]:
                self.assessor.assess(it, sym, now, parse_ts(anchor["since"]))
        self.store.set("news_cursor", iso(now))

    def _daily_report_if_due(self, L: RiskLimits, now: datetime) -> None:
        rdate, due = local_report_due(now, L.report_time_local, L.report_timezone)
        if now < due or self.store.one("SELECT 1 FROM daily_reports WHERE report_date=?", (rdate.isoformat(),)):
            return
        rep = self.build_report(rdate, due - timedelta(days=1), due)
        self.store.execute("INSERT OR REPLACE INTO daily_reports VALUES(?,?,?)", (rdate.isoformat(), iso(now), dumps(rep)))
        if "trading_pnl_gbp" in rep:
            body = (f"Trading P&L £{rep['trading_pnl_gbp']} (realised £{rep['realised_pnl_gbp']}, unrealised change "
                    f"£{rep['unrealised_change_gbp']}); operating costs £{rep['operating_costs_gbp']['total']}; "
                    f"net £{rep['net_after_operating_costs_gbp']}. Equity £{rep['equity_end_gbp']}. "
                    f"Drawdown {rep['drawdown_from_peak_pct']}%. Open: {len(rep['outstanding_exposure'])}. "
                    f"Problems: {len(rep['operational_problems'])}.")
        else:
            body = rep.get("status", "")
        quiet = (self.cal.session_bounds(rdate) is None and rep.get("fills", 0) == 0
                 and not rep.get("operational_problems") and rep.get("trading_pnl_gbp") in (None, "0.00"))
        if not quiet:      # no push for non-trading days with nothing to report (still stored)
            self.notifier.send("daily_report", f"Daily report {rdate} ({self.mode().value})", body)

    def build_report(self, rdate: date, start: datetime, end: datetime) -> dict[str, Any]:
        per_day = D(self.s.infra_cost_gbp_month + self.s.data_cost_gbp_month) * 12 / 365
        rep = daily_report(self.store, rdate.isoformat(), start, end, per_day, self.usd_to_gbp())
        rep["mode"] = self.mode().value
        rep["data_provider"] = self.data.name + (" (REPLAYED HISTORY - not live data)" if self.data.is_replay else "")
        rep["broker"] = self.broker.capabilities.name + (" (paper/simulated)" if self.broker.capabilities.is_paper else " (LIVE)")
        return rep

    # ------------------------------------------------------------------ forward-paper gate
    def forward_paper_stats(self, key: str) -> dict[str, Any]:
        started = parse_ts(self.store.get("paper_started_at"))
        sessions = self.cal.sessions_between(started.date(), self.cal.session_date(self.clock.now())) if started else 0
        _, realised, _ = self.ledger.build()
        closed = [r for r in realised if r.strategy == key]
        return {"sessions": sessions, "closed_trades": len(closed),
                "realised_gbp": str(sum((r.realised_gbp for r in closed), D0).quantize(Decimal("0.01")))}
