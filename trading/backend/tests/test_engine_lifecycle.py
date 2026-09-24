"""Autonomous cycle and execution-failure tests on the simulator + real replayed prices."""
from __future__ import annotations

from decimal import Decimal

from tradebot.models import Mode

from .conftest import Harness, generous_limits


def test_full_cycle_entry_stop_exit(h):
    h.tick(10)                                   # 13:40 UTC, 10 min after the open
    entries = h.orders("entry")
    assert len(entries) == 1 and entries[0]["status"] == "filled"
    approved = h.decisions("approved")
    assert approved and "size_meets_broker_minimum" in approved[0]["checks"]
    h.tick(1)                                    # protective stop placed once the entry is complete
    stops = h.orders("protective_stop")
    assert len(stops) == 1 and stops[0]["status"] == "accepted"
    assert Decimal(stops[0]["qty"]) == Decimal(entries[0]["filled_qty"])
    mp = h.store.one("SELECT * FROM managed_positions WHERE symbol='SPY'")
    assert mp["stop_mode"] == "broker"
    # strategy exit signal is evaluated after the close and executed after the next open
    h.strategy.exit_now = True
    h.strategy.enter = False
    h.clock.set(h.clock.now().replace(hour=20, minute=5))
    h.tick()
    assert h.store.one("SELECT exit_pending_reason FROM managed_positions")["exit_pending_reason"] == "test exit"
    h.clock.set(h.clock.now().replace(day=5, hour=13, minute=35))
    h.tick()
    exits = h.orders("exit")
    assert len(exits) == 1 and exits[0]["status"] == "filled"
    assert h.store.one("SELECT status FROM orders WHERE purpose='protective_stop'")["status"] == "canceled"
    h.tick(1)
    assert h.store.query("SELECT * FROM managed_positions") == []
    assert h.broker.get_positions() == []


def test_one_entry_attempt_per_session_and_idempotent_ids(h):
    h.tick(10)
    h.tick(1)
    h.tick(1)
    assert len(h.orders("entry")) == 1
    cid = h.orders("entry")[0]["client_order_id"]
    assert cid == "tb-always-SPY-entry-2018-06-04"


def test_no_entries_outside_window_or_when_closed(tmp_path):
    h = Harness(tmp_path, limits=generous_limits())
    h.tick(2)                                    # 2 minutes after open: before the 5-minute window
    assert h.orders("entry") == []
    h.clock.set(h.clock.now().replace(day=2))    # Saturday
    h.tick()
    assert h.orders("entry") == []


def test_stale_quote_blocks_entry_and_is_retried(tmp_path):
    h = Harness(tmp_path, limits=generous_limits())
    h.data.stale_symbols.add("SPY")
    h.tick(10)
    rej = h.decisions("rejected")
    assert rej and "quote_fresh" in rej[-1]["summary"]
    assert h.orders("entry") == []
    h.data.stale_symbols.clear()                 # transient failure: retried within the window
    h.tick(1)
    assert len(h.orders("entry")) == 1


def test_missing_prices_block_entry(tmp_path):
    h = Harness(tmp_path, limits=generous_limits())
    h.data.missing_symbols.add("SPY")
    h.tick(10)
    assert h.orders() == []
    assert any("bars unavailable" in d["summary"] or "quote_available" in d["summary"] for d in h.decisions("rejected"))


def test_ten_pound_account_sizes_or_explains(tmp_path):
    # £10 ≈ $12.50. Default limits: 1% risk -> tiny order, costs dominate -> explained rejection.
    h = Harness(tmp_path, cash="12.50")
    h.engine.slots[0].expected_edge_bps = 21.7    # RSI(2)/SPY validated OOS average trade
    h.tick(10)
    rej = h.decisions("rejected")[-1]
    assert "costs_small_vs_edge" in rej["summary"]
    assert "min fee 0.02" in rej["checks"]
    assert h.orders() == []


def test_order_below_broker_minimum_is_impossible_with_explanation(tmp_path):
    h = Harness(tmp_path, cash="3.00", limits=generous_limits(max_position_pct=20))
    h.tick(10)
    rej = h.decisions("rejected")[-1]
    assert "Order impossible" in rej["summary"] and "broker minimum" in rej["summary"]


def test_ten_pound_account_can_trade_when_limits_allow(tmp_path):
    h = Harness(tmp_path, cash="12.50", limits=generous_limits(max_position_pct=100, risk_per_trade_pct=5))
    h.tick(10)
    e = h.orders("entry")
    assert len(e) == 1 and e[0]["status"] == "filled"
    value = Decimal(e[0]["filled_qty"]) * Decimal(e[0]["filled_avg_price"])
    assert Decimal("1") <= value <= Decimal("12.50")


def test_partial_fill_then_ttl_cancel_and_stop_sized_to_fill(h):
    h.broker.partial_next = Decimal("0.4")
    h.tick(10)
    e = h.orders("entry")[0]
    assert e["status"] == "partially_filled"
    h.broker.partial_next = Decimal("0")
    # stop the simulator from completing the remainder so the TTL applies
    h.broker.data.stale_symbols.add("SPY")
    orig = h.broker.process
    h.broker.process = lambda: None
    h.tick(11)                                  # beyond the 10-minute entry TTL
    e = h.store.get_order(e["client_order_id"])
    assert e["status"] == "canceled"
    h.broker.process = orig
    h.broker.data.stale_symbols.clear()
    h.tick(1)
    stop = h.orders("protective_stop")[0]
    assert Decimal(stop["qty"]) == Decimal(e["filled_qty"])
    fills = h.store.query("SELECT * FROM fills WHERE purpose='entry'")
    assert sum(Decimal(f["qty"]) for f in fills) == Decimal(e["filled_qty"])


def test_rejected_order_recorded_and_notified(h):
    h.broker.inject.append("reject")
    h.tick(10)
    e = h.orders("entry")[0]
    assert e["status"] == "rejected"
    assert h.store.query("SELECT * FROM managed_positions") == []
    assert any("rejected" in n["title"] for n in h.store.query("SELECT title FROM notifications"))


def test_timeout_after_accept_reconciles_without_duplicate(h):
    h.broker.inject.append("timeout_after_accept")
    h.tick(10)
    e = h.orders("entry")[0]
    assert e["status"] == "unknown"
    assert len(h.broker.orders) == 1             # the broker did receive it
    h.tick(1)
    e = h.store.get_order(e["client_order_id"])
    assert e["status"] == "filled"
    assert len(h.broker.orders) == 1 + len(h.orders("protective_stop"))
    assert len([o for o in h.broker.orders.values() if o.client_order_id.startswith("tb-always-SPY-entry")]) == 1


def test_timeout_before_accept_waits_for_grace_then_marks_not_sent(h):
    h.broker.inject.append("timeout_before_accept")
    h.tick(10)
    cid = h.orders("entry")[0]["client_order_id"]
    assert h.store.get_order(cid)["status"] == "unknown"
    h.clock.advance(seconds=10)
    h.engine.exec.reconcile_unresolved()
    assert h.store.get_order(cid)["status"] == "unknown"      # still inside the grace period
    h.clock.advance(seconds=30)
    h.engine.exec.reconcile_unresolved()
    assert h.store.get_order(cid)["status"] == "not_sent"
    assert h.broker.orders == {}


def test_duplicate_submission_suppressed(h):
    from tradebot.models import OrderRequest, OrderType, Purpose, Side
    req = OrderRequest("dup-1", "SPY", Side.BUY, OrderType.MARKET, qty=Decimal("1"))
    h.clock.advance(minutes=10)
    h.engine.exec.submit(req, Purpose.ENTRY, "always:SPY")
    h.engine.exec.submit(req, Purpose.ENTRY, "always:SPY")
    assert len([o for o in h.broker.orders.values() if o.client_order_id == "dup-1"]) == 1


def test_rate_limit_is_not_sent_and_safe_to_retry(h):
    from tradebot.models import OrderRequest, OrderType, Purpose, Side
    h.clock.advance(minutes=10)
    h.broker.inject.append("rate_limit")
    req = OrderRequest("rl-1", "SPY", Side.BUY, OrderType.MARKET, qty=Decimal("1"))
    assert h.engine.exec.submit(req, Purpose.ENTRY, "x")["status"] == "not_sent"
    assert h.engine.exec.submit(req, Purpose.ENTRY, "x")["status"] == "filled"
    assert len(h.broker.orders) == 1


def test_restart_with_unknown_order_reconciles(tmp_path):
    h = Harness(tmp_path, limits=generous_limits())
    h.broker.inject.append("timeout_after_accept")
    h.tick(10)
    assert h.orders("entry")[0]["status"] == "unknown"
    h.restart()                                  # process dies and comes back
    h.tick(1)
    assert h.orders("entry")[0]["status"] == "filled"
    assert len([o for o in h.broker.orders.values() if "entry" in o.client_order_id]) == 1
    h.tick(1)
    assert h.orders("protective_stop")[0]["status"] == "accepted"


def test_crash_between_write_and_send_is_resolved(tmp_path):
    """Row written as PENDING_SUBMIT but the process died before sending."""
    h = Harness(tmp_path, limits=generous_limits())
    h.clock.advance(minutes=10)
    h.store.insert_order({"client_order_id": "tb-always-SPY-entry-2018-06-04", "broker": "sim", "mode": "paper",
                          "symbol": "SPY", "side": "buy", "type": "market", "tif": "day", "qty": "1",
                          "purpose": "entry", "strategy": "always:SPY", "status": "pending_submit",
                          "created_at": h.clock.now().isoformat(), "updated_at": h.clock.now().isoformat()})
    h.clock.advance(minutes=1)
    h.restart()
    h.engine.exec.reconcile_unresolved()
    assert h.store.get_order("tb-always-SPY-entry-2018-06-04")["status"] == "not_sent"
    assert h.broker.orders == {}
    # only now - with the broker confirming it never saw the order - may the id be resubmitted
    h.tick()
    sent = [o for o in h.broker.orders.values() if o.client_order_id == "tb-always-SPY-entry-2018-06-04"]
    assert len(sent) == 1
    assert h.store.get_order("tb-always-SPY-entry-2018-06-04")["status"] == "filled"


def test_reconciliation_mismatch_blocks_entries(h):
    h.tick(10)
    # someone sells half the position outside the bot
    pos = h.broker.positions["SPY"]
    pos["qty"] = pos["qty"] / 2
    h.clock.set(h.clock.now().replace(day=5, hour=13, minute=40))
    h.tick()
    rec = h.store.get("reconciliation")
    assert not rec["ok"] and "SPY" in rec["issues"][0]
    assert any(n["title"].startswith("Position mismatch") for n in h.store.query("SELECT title FROM notifications"))


def test_credential_failure_halts(h):
    h.broker.inject.append("auth")
    out = h.tick(10)
    assert out["error"] == "auth"
    assert "credentials" in h.control.get("halted_reason")


def test_connectivity_loss_alerts_after_three_failures_and_recovers(h):
    for _ in range(3):
        h.broker.inject.append("unavailable")
        assert h.tick(1)["error"] == "broker_unavailable"
    titles = [n["title"] for n in h.store.query("SELECT title FROM notifications")]
    assert "Broker connection lost" in titles
    h.tick(1)
    assert h.store.get("broker_status")["ok"]


def test_daily_loss_limit_pauses_entries_but_keeps_managing(tmp_path):
    h = Harness(tmp_path, limits=generous_limits(daily_loss_limit_pct=0.5, max_position_pct=100, max_entries_per_day=1))
    h.tick(10)
    assert h.orders("entry")[0]["status"] == "filled"
    h.broker.cash -= Decimal("200")              # simulate a 2% loss
    h.tick(1)
    assert h.control.get("entries_paused") and h.control.get("entries_paused_reason") == "daily_loss"
    # exits still work while paused
    res = h.engine.command("close_position", symbol="SPY")
    assert res["results"]["SPY"] == "filled"


def test_drawdown_flatten_policy(tmp_path):
    h = Harness(tmp_path, limits=generous_limits(max_drawdown_pct=1, on_drawdown_breach="flatten", daily_loss_limit_pct=20))
    h.tick(10)
    h.broker.cash -= Decimal("300")
    h.tick(3)
    assert h.control.get("halted_reason", "").startswith("max drawdown")
    assert h.orders("exit")[0]["status"] == "filled"


def test_pause_does_not_disable_protective_management(h):
    h.tick(10)
    h.engine.command("pause_entries")
    h.tick(1)
    assert h.orders("protective_stop")[0]["status"] == "accepted"


def test_read_only_mode_never_sends_orders(tmp_path):
    h = Harness(tmp_path, limits=generous_limits())
    h.control.set("mode", Mode.READ_ONLY.value)
    h.tick(10)
    assert h.orders() == [] and h.broker.orders == {}
    assert any(c for c in h.decisions() if c["outcome"] == "rejected" and "mode_allows_orders" in c["summary"])


def test_cancel_pending_entries_control(h):
    h.broker.process = lambda: None             # keep the entry working
    h.tick(10)
    assert h.orders("entry")[0]["status"] == "accepted"
    h.engine.command("cancel_pending_entries")
    assert h.orders("entry")[0]["status"] == "canceled"


def test_software_stop_when_broker_cannot_hold_stop(tmp_path):
    h = Harness(tmp_path, limits=generous_limits())
    from dataclasses import replace
    h.broker.capabilities = replace(h.broker.capabilities, order_types=("market",))
    h.tick(10)
    h.tick(1)
    mp = h.store.one("SELECT * FROM managed_positions")
    assert mp["stop_mode"] == "software" and h.orders("protective_stop") == []
    h.store.execute("UPDATE managed_positions SET stop_price='100000'")   # force the stop to trigger
    h.tick(1)
    assert h.orders("exit")[0]["status"] == "filled"
    assert "software stop" in h.store.query("SELECT summary FROM decisions WHERE action='exit'")[0]["summary"]


def test_data_outage_with_software_stop_exits_after_grace(tmp_path):
    h = Harness(tmp_path, limits=generous_limits())
    from dataclasses import replace
    h.broker.capabilities = replace(h.broker.capabilities, order_types=("market",))
    h.tick(10)
    h.tick(1)
    h.data.stale_symbols.add("SPY")
    h.tick(1)
    assert any("No fresh prices" in n["title"] for n in h.store.query("SELECT title FROM notifications"))
    assert h.orders("exit") == []
    h.tick(16)
    assert len(h.orders("exit")) == 1
