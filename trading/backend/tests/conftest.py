from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tradebot.brokers.sim import SimBroker
from tradebot.clock import SimClock
from tradebot.config import RiskLimits, Settings
from tradebot.data.fx import FixedFx
from tradebot.data.replay import ReplayMarketData
from tradebot.db import Store, iso
from tradebot.engine import Engine, StrategySlot
from tradebot.notify import Notifier
from tradebot.strategies import Strategy

# Monday 4 June 2018: NYSE opens 13:30 UTC (EDT)
OPEN = datetime(2018, 6, 4, 13, 30, tzinfo=timezone.utc)


class AlwaysEnter(Strategy):
    """Test strategy: entry signal every session, exit when told via ``exit_now``."""

    def __init__(self, stop_pct: float | None = 0.05):
        super().__init__(name="always", version="t1", stop_pct=stop_pct, history_needed=5)
        self.exit_now = False
        self.enter = True

    def entry_at(self, pre, i):
        return {"rule": "test entry"} if self.enter else None

    def exit_at(self, pre, i, bars_held):
        return "test exit" if self.exit_now else None


class Harness:
    def __init__(self, tmp: Path, cash: str = "10000", limits: RiskLimits | None = None,
                 strategy: Strategy | None = None, start: datetime = OPEN):
        self.tmp = tmp
        self.clock = SimClock(start)
        self.data = ReplayMarketData(self.clock)
        self.broker = SimBroker(self.data, self.clock, starting_cash=Decimal(cash), state_path=tmp / "sim.json",
                                slippage_bps=0)
        self.settings = Settings(data_dir=tmp, broker="sim", market_data="replay")
        self.control = Store(tmp / "control.db")
        self.store = Store(tmp / "paper.db")
        self.strategy = strategy or AlwaysEnter()
        self.engine = self._engine()
        if limits:
            self.set_limits(limits)
        self.engine.command("start")

    def _engine(self) -> Engine:
        eng = Engine(self.settings, self.store, self.broker, self.data, FixedFx(Decimal("0.75")), self.clock,
                     Notifier(self.settings, self.store), None, [], control=self.control)
        eng.slots = [StrategySlot("always:SPY", self.strategy, "SPY", "paper", 200.0)]
        return eng

    def restart(self) -> None:
        """Simulate a process restart: new broker object from persisted state, new engine."""
        self.broker = SimBroker(self.data, self.clock, starting_cash=Decimal("0"), state_path=self.tmp / "sim.json",
                                slippage_bps=0)
        self.store = Store(self.tmp / "paper.db")
        self.control = Store(self.tmp / "control.db")
        self.engine = self._engine()

    def set_limits(self, limits: RiskLimits) -> None:
        self.control.execute("INSERT INTO risk_limits_versions(ts,body,note) VALUES(?,?,?)",
                             (iso(self.clock.now()), limits.model_dump_json(), "test"))

    def tick(self, minutes: float = 0) -> dict:
        if minutes:
            self.clock.advance(minutes=minutes)
        return self.engine.tick()

    def orders(self, purpose: str | None = None):
        q = "SELECT * FROM orders" + (" WHERE purpose=?" if purpose else "") + " ORDER BY created_at"
        return self.store.query(q, (purpose,) if purpose else ())

    def decisions(self, outcome: str | None = None):
        q = "SELECT * FROM decisions" + (" WHERE outcome=?" if outcome else "") + " ORDER BY id"
        return self.store.query(q, (outcome,) if outcome else ())


def generous_limits(**kw) -> RiskLimits:
    base = dict(max_position_pct=50, risk_per_trade_pct=5, max_entries_per_day=5, max_positions=3,
                max_cost_to_edge_ratio=1.0)
    base.update(kw)
    return RiskLimits(**base)


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path, limits=generous_limits())
