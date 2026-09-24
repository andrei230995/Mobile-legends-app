"""Daily-bar, long-only, unleveraged event backtester that reuses the live Strategy classes.

Execution assumptions (all explicit and stress-tested):
* Decisions use only bars completed at the decision time (close of day t).
* Orders execute at the *open* of day t+1+delay, paying half the quoted spread plus slippage.
* Stops are checked against each day's low; a gap below the stop fills at the open,
  not at the stop price (a stop does not cap the loss).
* Broker fees are computed per order with per-trade cent rounding, so tiny accounts
  bear the minimum-fee drag they would bear live.
* Dividends are approximated by accruing an annual yield while invested (index price
  data excludes them); this is applied equally to the strategy and to buy-and-hold.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..brokers.base import USRegulatoryFeeModel
from ..models import Side
from ..strategies import Strategy

_REG = USRegulatoryFeeModel()


def us_reg_fee(side: str, qty: float, price: float) -> float:
    return float(_REG.fee(Side(side), Decimal(str(round(qty, 9))), Decimal(str(round(price, 4)))))


@dataclass
class CostModel:
    spread_bps: float = 2.0          # full quoted spread; half is paid per side
    slippage_bps: float = 2.0        # per side, beyond the half-spread
    fx_bps: float = 0.0              # per side, e.g. 15 for Trading 212 on USD instruments
    fee_fn: Callable[[str, float, float], float] = us_reg_fee
    stop_extra_bps: float = 0.0      # extra adverse slippage on stop exits (stress)

    def scaled(self, k: float) -> "CostModel":
        return CostModel(self.spread_bps * k, self.slippage_bps * k, self.fx_bps, self.fee_fn,
                         self.stop_extra_bps * k)


@dataclass
class BacktestConfig:
    start_equity: float = 1000.0
    position_fraction: float = 1.0
    costs: CostModel = field(default_factory=CostModel)
    delay_days: int = 0
    dividend_yield: float = 0.0
    min_notional: float = 1.0


@dataclass
class Trade:
    entry_day: Any
    exit_day: Any
    entry_px: float
    exit_px: float
    qty: float
    net_ret: float
    bars_held: int
    reason: str


@dataclass
class Result:
    equity: pd.Series
    trades: list[Trade]
    exposure: pd.Series
    turnover_notional: float
    skipped_min_size: int = 0


def run(df: pd.DataFrame, strat: Strategy, cfg: BacktestConfig,
        trade_start=None, trade_end=None, close_at_end: bool = True) -> Result:
    """Simulate ``strat`` on ``df`` (index: date; columns open/high/low/close).

    Rows before ``trade_start`` are indicator warm-up only; no trading happens there.
    """
    c = cfg.costs
    idx = list(df.index)
    o, h, l, cl = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    start_i = max(strat.history_needed, idx.index(trade_start) if trade_start is not None else 0)
    end_i = idx.index(trade_end) if trade_end is not None else len(idx) - 1
    cash, qty = cfg.start_equity, 0.0
    entry_px = entry_cost = 0.0
    entry_i = -1
    stop = None
    pending: tuple[int, str, str] | None = None     # (execute_at_index, action, reason)
    pre = strat.precompute(df)
    eq, expo, trades = [], [], []
    turnover, skipped = 0.0, 0
    div_daily = cfg.dividend_yield / 252
    buy_mult = 1 + (c.spread_bps / 2 + c.slippage_bps + c.fx_bps) / 1e4
    sell_mult = 1 - (c.spread_bps / 2 + c.slippage_bps + c.fx_bps) / 1e4

    def sell(i: int, px: float, reason: str) -> None:
        nonlocal cash, qty, turnover, stop, entry_i
        fee = c.fee_fn("sell", qty, px)
        proceeds = qty * px - fee
        cash += proceeds
        turnover += qty * px
        trades.append(Trade(idx[entry_i], idx[i], entry_px, px, qty, proceeds / entry_cost - 1,
                            i - entry_i, reason))
        qty, stop, entry_i = 0.0, None, -1

    for i in range(start_i, end_i + 1):
        # 1) execute pending order at today's open
        if pending and pending[0] == i:
            _, action, reason = pending
            pending = None
            if action == "enter" and qty == 0:
                px = o[i] * buy_mult
                budget = cash * cfg.position_fraction
                fee_buy = c.fee_fn("buy", budget / px, px)
                q = (budget - fee_buy) / px
                if q * px >= cfg.min_notional:
                    qty, entry_px, entry_i = q, px, i
                    entry_cost = q * px + fee_buy
                    cash -= entry_cost
                    turnover += q * px
                    stop = o[i] * (1 - strat.stop_pct) if strat.stop_pct else None
                else:
                    skipped += 1
            elif action == "exit" and qty > 0:
                sell(i, o[i] * sell_mult, reason)
        # 2) intraday protective stop (gap-aware)
        if qty > 0 and stop is not None and l[i] <= stop:
            fill = min(o[i], stop) * (sell_mult - c.stop_extra_bps / 1e4)
            sell(i, fill, "stop")
        # 3) dividends accrue on held value
        if qty > 0 and div_daily:
            cash += qty * cl[i] * div_daily
        # 4) decide at the close using bars up to and including today
        if i < end_i and pending is None:
            exe = i + 1 + cfg.delay_days
            if exe <= end_i:
                if qty == 0:
                    ev = strat.entry_at(pre, i)
                    if ev is not None:
                        pending = (exe, "enter", ev.get("rule", ""))
                else:
                    r = strat.exit_at(pre, i, i - entry_i + 1)
                    if r:
                        pending = (exe, "exit", r)
        eq.append(cash + qty * cl[i])
        expo.append(1.0 if qty > 0 else 0.0)
    if close_at_end and qty > 0:
        sell(end_i, cl[end_i] * sell_mult, "end of test window")
        eq[-1] = cash
    days = idx[start_i:end_i + 1]
    return Result(pd.Series(eq, index=days), trades, pd.Series(expo, index=days), turnover, skipped)


def metrics(res: Result) -> dict[str, Any]:
    eq = res.equity
    rets = eq.pct_change().dropna()
    years = max(len(eq) / 252, 1e-9)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1.0
    vol = rets.std() * math.sqrt(252)
    sharpe = (rets.mean() * 252) / vol if vol > 0 else 0.0
    dd = eq / eq.cummax() - 1
    under = (dd < 0).astype(int)
    longest = int(under.groupby((under == 0).cumsum()).sum().max()) if len(under) else 0
    tr = np.array([t.net_ret for t in res.trades]) if res.trades else np.array([])
    monthly = eq.groupby(pd.to_datetime(pd.Series(eq.index, index=eq.index)).dt.to_period("M").values).last()
    mrets = monthly.pct_change().dropna()
    return {
        "start": str(eq.index[0]), "end": str(eq.index[-1]), "years": round(years, 2),
        "total_return_pct": round(float(total) * 100, 2), "cagr_pct": round(float(cagr) * 100, 2),
        "ann_vol_pct": round(float(vol) * 100, 2), "sharpe": round(float(sharpe), 3),
        "max_drawdown_pct": round(float(dd.min()) * 100, 2), "longest_drawdown_days": longest,
        "exposure_pct": round(float(res.exposure.mean()) * 100, 1),
        "trades": len(res.trades), "win_rate_pct": round(float((tr > 0).mean()) * 100, 1) if len(tr) else None,
        "avg_trade_bps": round(float(tr.mean()) * 1e4, 1) if len(tr) else None,
        "median_trade_bps": round(float(np.median(tr)) * 1e4, 1) if len(tr) else None,
        "worst_trade_pct": round(float(tr.min()) * 100, 2) if len(tr) else None,
        "annual_turnover_x": round(float(res.turnover_notional / float(eq.mean()) / years), 2),
        "losing_months_pct": round(float((mrets < 0).mean()) * 100, 1) if len(mrets) else None,
        "worst_month_pct": round(float(mrets.min()) * 100, 2) if len(mrets) else None,
        "skipped_below_min_size": res.skipped_min_size,
    }


def bootstrap_mean_ci(x: np.ndarray, n: int = 5000, alpha: float = 0.10, block: int = 1,
                      seed: int = 7) -> tuple[float, float]:
    """(Block) bootstrap confidence interval for the mean of ``x``."""
    if len(x) < 5:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    m = len(x)
    nb = math.ceil(m / block)
    means = np.empty(n)
    for k in range(n):
        starts = rng.integers(0, m - block + 1, nb)
        sample = np.concatenate([x[s:s + block] for s in starts])[:m]
        means[k] = sample.mean()
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))
