"""End-to-end engine behaviour on the Trading 212 path: signals and stops on SPY (real replayed
prices), orders in the London UCITS line (VUSA) on a fake Trading 212 API, London hours."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import SecretStr

from tradebot.brokers.trading212 import Trading212Broker
from tradebot.clock import SimClock
from tradebot.config import DEFAULT_T212_EXECUTION, BrokerCredentials, Settings
from tradebot.data.fx import FixedFx
from tradebot.data.reference import ReferencePrices
from tradebot.data.replay import ReplayMarketData
from tradebot.db import Store
from tradebot.engine import Engine, StrategySlot
from tradebot.notify import Notifier

from .conftest import AlwaysEnter, generous_limits
from .fake_t212 import FakeT212

# Monday 4 June 2018 13:40 UTC: NYSE open 10 min, London open (07:00-15:30 UTC)
T0 = datetime(2018, 6, 4, 13, 40, tzinfo=timezone.utc)
CREDS = BrokerCredentials(SecretStr("k"), SecretStr("s"))


def build(tmp_path, reference: bool = True):
    clock = SimClock(T0)
    fake = FakeT212(prices={"VUSAl_EQ": Decimal("50.00")})
    fake.clock = clock
    fake.cash = Decimal("1000")
    broker = Trading212Broker(CREDS, demo=True, transport=fake.transport(),
                              execution_map={"SPY": DEFAULT_T212_EXECUTION["SPY"]})
    refs = ReferencePrices(broker.execution_map, clock)
    if reference:
        refs.fixed["SPY"] = Decimal("50.00")
    s = Settings(data_dir=tmp_path, broker="trading212", market_data="replay")
    ctl, st = Store(tmp_path / "control.db"), Store(tmp_path / "paper.db")
    strat = AlwaysEnter(stop_pct=0.05)
    eng = Engine(s, st, broker, ReplayMarketData(clock), FixedFx(Decimal("0.75")), clock, Notifier(s, st),
                 None, [], control=ctl, exec_prices=refs)
    eng.slots = [StrategySlot("always:SPY", strat, "SPY", "paper", 60.0, {"modelled_cost_bps": 14})]
    ctl.execute("INSERT INTO risk_limits_versions(ts,body,note) VALUES(?,?,?)",
                (T0.isoformat(), generous_limits(symbols=["SPY"]).model_dump_json(), "t"))
    eng.command("start")
    return eng, fake, clock, strat


def test_entry_executes_in_ucits_line_sized_from_reference(tmp_path):
    eng, fake, clock, _ = build(tmp_path)
    eng.tick()
    entry = eng.store.query("SELECT * FROM orders WHERE purpose='entry'")[0]
    assert entry["status"] == "filled" and fake.last_body["ticker"] == "VUSAl_EQ"
    qty = Decimal(entry["qty"])
    assert qty * Decimal("50") <= Decimal("500")                   # 50% position cap on £1,000, GBP prices
    mp = eng.store.one("SELECT * FROM managed_positions")
    assert mp["stop_mode"] == "software"                            # no broker-held stops on Trading 212
    assert Decimal(mp["stop_price"]) > 200                          # stop level is in SPY (USD) terms
    clock.advance(minutes=1)
    eng.tick()
    assert eng.store.query("SELECT * FROM orders WHERE purpose='protective_stop'") == []
    dec = eng.store.one("SELECT checks, evidence FROM decisions WHERE outcome='approved'")
    assert "execution_price_reference" in dec["checks"] and "Vanguard S&P 500" in dec["evidence"]
    assert eng.store.get("reconciliation")["ok"]


def test_no_reference_price_means_no_entry(tmp_path):
    eng, fake, clock, _ = build(tmp_path, reference=False)
    eng.tick()
    assert eng.store.query("SELECT * FROM orders") == []
    rej = eng.store.query("SELECT summary FROM decisions WHERE outcome='rejected'")[-1]["summary"]
    assert "execution_price_reference" in rej


def test_stop_after_london_close_exits_at_next_london_open(tmp_path):
    eng, fake, clock, strat = build(tmp_path)
    eng.tick()
    strat.enter = False
    eng.store.execute("UPDATE managed_positions SET stop_price='100000'")   # force the software stop
    clock.set(datetime(2018, 6, 4, 16, 0, tzinfo=timezone.utc))          # NYSE open, London closed
    eng.tick()
    assert eng.store.query("SELECT * FROM orders WHERE purpose='exit'") == []
    assert "software stop" in eng.store.one("SELECT exit_pending_reason FROM managed_positions")["exit_pending_reason"]
    clock.set(datetime(2018, 6, 5, 7, 5, tzinfo=timezone.utc))           # London open, NYSE closed
    eng.tick()
    ex = eng.store.query("SELECT * FROM orders WHERE purpose='exit'")
    assert len(ex) == 1 and ex[0]["status"] == "filled" and fake.last_body["quantity"] < 0
    clock.advance(minutes=1)
    eng.tick()
    assert eng.store.query("SELECT * FROM managed_positions") == []


def test_strategy_exit_waits_for_us_session_overlap(tmp_path):
    eng, fake, clock, strat = build(tmp_path)
    eng.tick()
    strat.enter, strat.exit_now = False, True
    clock.set(datetime(2018, 6, 4, 20, 10, tzinfo=timezone.utc))          # after the US close: evaluate
    eng.tick()
    clock.set(datetime(2018, 6, 5, 8, 0, tzinfo=timezone.utc))            # London open, US closed: wait
    eng.tick()
    assert eng.store.query("SELECT * FROM orders WHERE purpose='exit'") == []
    clock.set(datetime(2018, 6, 5, 13, 35, tzinfo=timezone.utc))          # both open: execute
    eng.tick()
    assert eng.store.query("SELECT status FROM orders WHERE purpose='exit'")[0]["status"] == "filled"


def test_slippage_far_above_reference_pauses_entries(tmp_path):
    eng, fake, clock, _ = build(tmp_path)
    fake.prices["VUSAl_EQ"] = Decimal("52.00")                            # fills 4% above the reference
    eng.tick()
    clock.advance(minutes=1)
    eng.tick()
    assert eng.ctl.get("entries_paused") and eng.ctl.get("entries_paused_reason") == "slippage"
