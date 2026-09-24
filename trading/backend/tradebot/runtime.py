"""Wiring: builds brokers/data/engines from Settings and runs the engine loop in a
background thread inside the web server process.

Paper and live are separate environments with separate databases
(``paper.db`` / ``live.db``) and separate credentials; global switches (mode, risk
limits, live authorisation) live in ``control.db``. Only the active environment's
engine ticks.
"""
from __future__ import annotations

import logging
import threading
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any

from .ai.news import NewsAssessor
from .brokers.base import Broker
from .brokers.sim import SimBroker
from .clock import Clock, SimClock
from .config import Settings
from .data.base import MarketData
from .data.fx import EcbFx, FixedFx, FxProvider
from .data.replay import ReplayMarketData, replay_start
from .db import Store, iso
from .engine import Engine, load_registry
from .models import Mode
from .notify import Notifier

log = logging.getLogger("tradebot.runtime")

VALIDATION_DIR = Path(__file__).resolve().parents[2] / "docs" / "validation"


class Runtime:
    def __init__(self, settings: Settings, clock: Clock | None = None, results_path: Path | None = None,
                 data: MarketData | None = None, fx: FxProvider | None = None,
                 brokers: dict[str, Broker] | None = None):
        self.s = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.control = Store(settings.data_dir / "control.db")
        self.replay = settings.market_data == "replay"
        if clock is None and self.replay:
            clock = SimClock(replay_start(self.control.get("replay_clock") or "2018-06-01", 13, 0))
        self.clock = clock or Clock()
        self.results_path = results_path or self._results_path()
        self._data, self._fx, self._brokers = data, fx, brokers or {}
        self.notifier = Notifier(settings, self.control)
        self.engines: dict[str, Engine] = {}
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_error: str | None = None

    def _results_path(self) -> Path:
        """Validation for this broker's cost profile; a server-side re-run with fresh data wins."""
        profile = self.s.validation_profile
        local = self.s.data_dir / "validation" / profile / "results.json"
        return local if local.exists() else VALIDATION_DIR / profile / "results.json"

    # -- construction --------------------------------------------------------------
    def data(self) -> MarketData:
        if self._data is None:
            if self.replay or (self.s.market_data == "auto" and not (self.s.alpaca_paper or self.s.alpaca_live)):
                self._data = ReplayMarketData(self.clock)
            else:
                from .data.alpaca_data import AlpacaMarketData
                creds = self.s.alpaca_paper or self.s.alpaca_live
                self._data = AlpacaMarketData(creds, feed=self.s.alpaca_data_feed, clock=self.clock)
        return self._data

    def fx(self) -> FxProvider:
        if self._fx is None:
            self._fx = FixedFx(Decimal("0.75")) if self.data().is_replay else EcbFx()
        return self._fx

    def broker(self, env: str) -> Broker:
        if env in self._brokers:
            return self._brokers[env]
        b: Broker
        if self.s.broker == "sim":
            if env == "live":
                raise RuntimeError("the simulator has no live environment")
            b = SimBroker(self.data(), self.clock, starting_cash=Decimal(str(self.s.sim_starting_cash_usd)),
                          state_path=self.s.data_dir / "sim_broker.json")
        elif self.s.broker == "alpaca":
            from .brokers.alpaca import AlpacaBroker
            creds = self.s.alpaca_paper if env == "paper" else self.s.alpaca_live
            if not creds:
                raise RuntimeError(f"Alpaca {env} credentials are not configured")
            b = AlpacaBroker(creds, paper=env == "paper")
        elif self.s.broker == "trading212":
            from .brokers.trading212 import Trading212Broker
            creds = self.s.t212_demo if env == "paper" else self.s.t212_live
            if not creds:
                raise RuntimeError(f"Trading 212 {env} credentials are not configured")
            b = Trading212Broker(creds, demo=env == "paper", execution_map=self.s.effective_execution_map())
        else:
            raise RuntimeError(f"unknown broker {self.s.broker}")
        for note in b.prepare_account():
            self.control.event("info", "broker", note)
        self._brokers[env] = b
        return b

    def engine(self, env: str | None = None) -> Engine:
        env = env or self.control.get("env", "paper")
        if env not in self.engines:
            store = Store(self.s.data_dir / f"{env}.db")
            assessor = NewsAssessor(store, self.s.anthropic_api_key.get_secret_value() if self.s.anthropic_api_key else None,
                                    self.s.ai_model, mode=self.control.get("ai_mode", "shadow"),
                                    daily_budget_usd=self.s.ai_daily_budget_usd,
                                    max_calls_per_day=self.s.ai_max_calls_per_day)
            broker = self.broker(env)
            exec_prices = None
            if getattr(broker, "execution_map", None):
                from .data.reference import ReferencePrices
                exec_prices = ReferencePrices(broker.execution_map, self.clock,
                                              self.s.finnhub_api_key.get_secret_value() if self.s.finnhub_api_key else None)
            self.engines[env] = Engine(self.s, store, broker, self.data(), self.fx(), self.clock,
                                       Notifier(self.s, store), assessor, load_registry(self.results_path),
                                       control=self.control, exec_prices=exec_prices)
        return self.engines[env]

    @property
    def env(self) -> str:
        return self.control.get("env", "paper")

    # -- mode switching ------------------------------------------------------------------
    def set_mode(self, mode: Mode, authorisation: dict[str, Any] | None = None) -> dict[str, Any]:
        current = Mode(self.control.get("mode", "paper"))
        cur_engine = self.engine()
        if current == Mode.LIVE and mode != Mode.LIVE:
            open_live = cur_engine.store.query("SELECT symbol FROM managed_positions")
            working = cur_engine.exec.open_rows()
            if open_live or working:
                return {"ok": False, "error": "Live positions or orders are open. Close them (or use 'Stop' to "
                        "block new entries while exits continue) before leaving live mode."}
        if mode == Mode.LIVE:
            self.control.set("env", "live")
            self.control.set("live_authorisation", authorisation)
        elif mode == Mode.PAPER:
            self.control.set("env", "paper")
        self.control.set("mode", mode.value)
        self.control.event("warning" if mode == Mode.LIVE else "info", "control", f"mode changed {current.value} -> {mode.value}",
                           authorisation)
        self.notifier.send("info" if mode != Mode.LIVE else "risk", f"Mode: {mode.value.upper()}",
                           f"Changed from {current.value}.")
        return {"ok": True}

    # -- loop -------------------------------------------------------------------------------
    def tick_once(self) -> dict[str, Any]:
        eng = self.engine()
        try:
            out = eng.tick()
            self.last_error = None
            return out
        except Exception as e:  # noqa: BLE001 - the loop must survive any single failure
            self.last_error = f"{type(e).__name__}: {e}"
            eng.store.event("error", "engine", f"tick failed: {self.last_error}",
                            {"trace": traceback.format_exc()[-2000:]})
            eng._alert_once("tick_error", "failure", "Engine error", self.last_error)
            eng.notifier.ping_health(False, self.last_error)
            return {"error": self.last_error}

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick_once()
            if isinstance(self.clock, SimClock):
                self.clock.advance(minutes=5)
                self.control.set("replay_clock", self.clock.now().date().isoformat())
                self._stop.wait(0.5)
            else:
                self._stop.wait(self.s.tick_seconds)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name="tradebot-engine", daemon=True)
        self.thread.start()
        self.control.event("info", "engine", "engine loop started")

    def stop(self) -> None:
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=30)

    def health(self) -> dict[str, Any]:
        eng = self.engine()
        hb = eng.store.get("heartbeat")
        from .db import parse_ts
        age = (self.clock.now() - parse_ts(hb)).total_seconds() if hb else None
        limit = 900 if isinstance(self.clock, SimClock) else max(self.s.tick_seconds * 4, 90)
        return {"engine_thread_alive": bool(self.thread and self.thread.is_alive()),
                "heartbeat": hb, "heartbeat_age_s": age,
                "healthy": bool(self.thread and self.thread.is_alive() and age is not None and age < limit
                                and not self.last_error),
                "last_error": self.last_error, "clock": "replay" if isinstance(self.clock, SimClock) else "real",
                "now": iso(self.clock.now())}
