"""Deterministic pre-trade risk engine.

Every entry order is the output of ``RiskEngine.evaluate_entry``; nothing else can
create one. Each check is recorded (pass/fail with the numbers used) so the UI can
show exactly why a trade was taken or rejected. AI output can only *add* a veto here;
it can never approve, size or originate a trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from .brokers.base import Capabilities, FeeModel
from .config import HARD_MAX_EXPOSURE_PCT, RiskLimits
from .models import (D0, Account, Asset, BrokerOrder, Check, D, Mode, Position, Quote, Side, Signal,
                     round_down)

GAP_ALLOWANCE = Decimal("0.01")      # extra loss assumed beyond the stop (gaps, slippage)
MODELLED_COST_BPS = Decimal("6")      # round-trip spread+slippage assumed in validation (2x(1+2))
EST_SLIPPAGE_BPS = Decimal("2")       # per side, as in validation; realised slippage is monitored
MAX_REFERENCE_AGE_S = 1200            # delayed reference price for sizing (15-min delayed feeds)
REFERENCE_BUFFER = Decimal("0.01")    # size as if the price were 1% higher than the delayed reference


@dataclass
class RiskContext:
    now: datetime
    mode: Mode
    limits: RiskLimits
    caps: Capabilities
    fee_model: FeeModel
    account: Account
    positions: list[Position]
    open_orders: list[BrokerOrder]
    trading_enabled: bool
    entries_paused: bool
    halted_reason: str | None
    strategy_status: str                  # research | paper | approved_live
    market_open: bool
    minutes_since_open: float | None
    minutes_to_close: float | None
    day_pnl_pct: Decimal                  # today's P&L incl. unrealised, flows excluded
    drawdown_pct: Decimal                 # from NAV high-water mark
    entries_today: int
    orders_today: int
    turnover_today_pct: Decimal
    data_is_replay: bool = False
    ai_veto: str | None = None
    reconciliation_ok: bool = True
    venue_open: bool = True               # execution exchange (e.g. London for UCITS ETFs) open
    exec_spread_bps: Decimal = Decimal("10")   # assumed when the execution instrument has no live quote


@dataclass
class EntryDecision:
    approved: bool
    qty: Decimal = D0
    est_price: Decimal = D0
    notional: Decimal = D0
    checks: list[Check] = field(default_factory=list)
    summary: str = ""
    est_cost_bps: Decimal | None = None
    stop_price: Decimal | None = None


class RiskEngine:
    def evaluate_entry(self, sig: Signal, ctx: RiskContext, quote: Quote | None, asset: Asset | None,
                       exec_quote: Quote | None = None, separate_execution: bool = False) -> EntryDecision:
        """``quote`` is the real-time quote of the signal symbol (freshness, spread, stop level).
        With ``separate_execution`` the order is placed in a different instrument (e.g. a London
        UCITS ETF) and ``exec_quote`` is its - possibly delayed - reference price for sizing."""
        L = ctx.limits
        checks: list[Check] = []

        def chk(name: str, ok: bool, detail: str = "") -> bool:
            checks.append(Check(name, bool(ok), detail))
            return bool(ok)

        # -- permissions & state ----------------------------------------------------
        chk("mode_allows_orders", ctx.mode != Mode.READ_ONLY, f"mode={ctx.mode.value}")
        chk("trading_enabled", ctx.trading_enabled, "trading switched on by user")
        chk("entries_not_paused", not ctx.entries_paused, "user/automatic entry pause")
        chk("not_halted", not ctx.halted_reason, ctx.halted_reason or "")
        chk("reconciliation_ok", ctx.reconciliation_ok, "broker and local state agree")
        needed = "approved_live" if ctx.mode == Mode.LIVE else "paper"
        chk("strategy_validated_for_mode",
            ctx.strategy_status == "approved_live" or (needed == "paper" and ctx.strategy_status == "paper"),
            f"strategy status={ctx.strategy_status}, required={needed}")
        chk("live_uses_live_data", not (ctx.mode == Mode.LIVE and ctx.data_is_replay),
            "replayed/simulated data can never drive live orders")
        chk("symbol_allowed", sig.symbol in L.symbols, f"allowed={L.symbols}")
        chk("strategy_holding_rule_compatible",
            not (L.flat_by_close and sig.max_hold_days not in (None, 0)),
            "flat-by-close is on; this strategy was validated holding overnight" if L.flat_by_close else "")
        chk("ai_no_event_veto", ctx.ai_veto is None, ctx.ai_veto or "no high-severity adverse event flagged")

        # -- market session -------------------------------------------------------------
        lo, hi = L.entry_window_minutes_after_open
        in_window = (ctx.market_open and ctx.minutes_since_open is not None
                     and lo <= ctx.minutes_since_open <= hi
                     and (ctx.minutes_to_close or 0) > L.flat_minutes_before_close)
        chk("within_entry_window", in_window,
            f"minutes since open={_fmt(ctx.minutes_since_open)}, window={lo}-{hi}, "
            f"minutes to close={_fmt(ctx.minutes_to_close)}")

        # -- data quality ---------------------------------------------------------------
        q_ok = quote is not None and quote.valid
        chk("quote_available", q_ok, "" if q_ok else "no valid bid/ask")
        if q_ok:
            age = quote.age_seconds(ctx.now)
            chk("quote_fresh", age <= L.max_quote_age_seconds,
                f"age={age:.1f}s limit={L.max_quote_age_seconds}s source={quote.source}/{quote.feed}")
            chk("quote_not_delayed", not quote.delayed, f"feed={quote.feed}")
            chk("spread_ok", quote.spread_bps <= D(L.max_spread_bps),
                f"spread={quote.spread_bps:.1f}bps limit={L.max_spread_bps}bps")
        a_ok = asset is not None and asset.tradable
        chk("instrument_tradable", a_ok, "" if a_ok else "asset not tradable/unknown")
        chk("execution_venue_open", ctx.venue_open, "execution exchange open")
        if separate_execution:
            e_ok = exec_quote is not None and exec_quote.valid
            age = exec_quote.age_seconds(ctx.now) if e_ok else None
            e_ok = e_ok and age <= MAX_REFERENCE_AGE_S
            chk("execution_price_reference", e_ok,
                (f"{exec_quote.ask} from {exec_quote.source} ({'delayed' if exec_quote.delayed else 'live'}), "
                 f"age {age:.0f}s, limit {MAX_REFERENCE_AGE_S}s; used for sizing only") if exec_quote
                else "no price for the execution instrument (Trading 212 has no price feed)")
            q_ok = q_ok and e_ok

        # -- loss limits & activity limits ----------------------------------------------
        chk("daily_loss_within_limit", ctx.day_pnl_pct > -D(L.daily_loss_limit_pct),
            f"today={ctx.day_pnl_pct:.2f}% limit=-{L.daily_loss_limit_pct}%")
        chk("drawdown_within_limit", ctx.drawdown_pct < D(L.max_drawdown_pct),
            f"drawdown={ctx.drawdown_pct:.2f}% limit={L.max_drawdown_pct}%")
        chk("entries_per_day", ctx.entries_today < L.max_entries_per_day,
            f"{ctx.entries_today}/{L.max_entries_per_day}")
        chk("orders_per_day", ctx.orders_today + 2 <= L.max_orders_per_day,
            f"{ctx.orders_today}+2 (entry+exit) / {L.max_orders_per_day}")
        held = {p.symbol for p in ctx.positions if p.qty > 0}
        pending = {o.symbol for o in ctx.open_orders if o.status.is_open}
        chk("no_existing_position_or_order", sig.symbol not in held and sig.symbol not in pending,
            "no pyramiding or averaging down")
        chk("max_positions", len(held | pending) < L.max_positions, f"{len(held | pending)}/{L.max_positions}")
        chk("account_not_blocked", not ctx.account.trading_blocked, ctx.account.status)

        if not (q_ok and a_ok):
            return self._finish(checks, D0, D0, D0)

        # -- sizing ------------------------------------------------------------------------
        eq = ctx.account.equity
        px = exec_quote.ask * (1 + REFERENCE_BUFFER) if separate_execution else quote.ask
        exposure = sum((p.market_value for p in ctx.positions), D0) + sum(
            ((o.notional or (o.qty or D0) * px) for o in ctx.open_orders if o.status.is_open and o.side == Side.BUY), D0)
        max_expo = min(D(L.max_total_exposure_pct), D(HARD_MAX_EXPOSURE_PCT)) / 100
        stop_pct = D(sig.stop_pct) if sig.stop_pct else Decimal("1")   # no stop => assume total loss
        loss_per_unit = stop_pct + GAP_ALLOWANCE + quote.spread_bps / 10000
        caps = {
            "position_cap": eq * D(L.max_position_pct) / 100,
            "exposure_room": eq * max_expo - exposure,
            "risk_cap": eq * D(L.risk_per_trade_pct) / 100 / loss_per_unit,
            "cash_cap": min(ctx.account.cash, ctx.account.buying_power) * (1 - D(L.cash_buffer_pct) / 100),
            "turnover_room": eq * (D(L.max_daily_turnover_pct) - ctx.turnover_today_pct) / 100 / 2,
        }
        notional = max(min(caps.values()), D0)
        binding = min(caps, key=caps.get)
        step = asset.qty_increment if (asset.fractionable and ctx.caps.supports_fractional) else Decimal(1)
        qty = round_down(notional / px, step)
        value = qty * px
        detail = ", ".join(f"{k}={v:.2f}" for k, v in caps.items())
        size_ok = chk("size_meets_broker_minimum", value >= asset.min_notional and qty > 0,
                      f"order value {value:.2f} {ctx.account.currency} vs minimum {asset.min_notional}; "
                      f"binding limit={binding}; {detail}"
                      + ("" if asset.fractionable else "; whole shares only"))
        if not size_ok:
            return self._finish(checks, D0, px, D0, summary_extra=(
                f"Order impossible: largest permitted order is {value:.2f} {ctx.account.currency} "
                f"(limited by {binding}), below the broker minimum of {asset.min_notional}."))

        # -- expected cost versus validated edge ---------------------------------------
        fee = ctx.fee_model.fee(Side.SELL, qty, px if separate_execution else quote.bid)
        spread = ctx.exec_spread_bps if separate_execution else quote.spread_bps
        fx_bps = D(2 * ctx.caps.fx_fee_bps) if asset.currency != ctx.account.currency else D0
        cost_bps = spread + 2 * EST_SLIPPAGE_BPS + fee / value * 10000 + fx_bps
        if sig.expected_edge_bps is None:
            chk("validated_edge_available", False, "strategy has no validated out-of-sample edge estimate")
        else:
            modelled = D(sig.modelled_cost_bps) if sig.modelled_cost_bps is not None else MODELLED_COST_BPS
            gross = D(sig.expected_edge_bps) + modelled
            chk("costs_small_vs_edge", gross > 0 and cost_bps <= gross * D(L.max_cost_to_edge_ratio),
                f"est. round-trip cost={cost_bps:.1f}bps (spread {spread:.1f}"
                f"{' assumed' if separate_execution else ''}, fee {fee} on exit, FX {fx_bps}bps), "
                f"gross validated edge={gross:.1f}bps, max ratio={L.max_cost_to_edge_ratio}")
        stop_price = (quote.ask * (1 - stop_pct)).quantize(Decimal("0.01"), rounding=ROUND_DOWN) if sig.stop_pct else None
        return self._finish(checks, qty, px, value, cost_bps, stop_price)

    @staticmethod
    def _finish(checks, qty, px, value, cost_bps=None, stop_price=None, summary_extra: str = "") -> EntryDecision:
        failed = [c.name for c in checks if not c.passed]
        ok = not failed
        summary = ("approved" if ok else "rejected: " + ", ".join(failed))
        if summary_extra:
            summary += ". " + summary_extra
        return EntryDecision(ok, qty if ok else D0, px, value if ok else D0, checks, summary, cost_bps, stop_price)


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0f}"
