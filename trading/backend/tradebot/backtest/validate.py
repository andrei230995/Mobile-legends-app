"""Walk-forward strategy validation and the live-promotion gate.

Usage:
    python -m tradebot.backtest.validate --source bundled --out ../docs/validation
    python -m tradebot.backtest.validate --source alpaca --out var/validation   # on the server

Procedure per strategy family and symbol:
1. Chronological walk-forward: choose parameters on the previous ``train_years`` only
   (highest net Sharpe with >= 10 trades), trade the next calendar year with them.
   The stitched test years are the out-of-sample (OOS) record.
2. Compare with buy-and-hold and cash over the identical OOS years and costs.
3. Stress the OOS record: 3x spread/slippage, one-day execution delay, extra stop slippage.
4. Re-run at £10 / £100 / £1,000 to expose minimum-fee and minimum-order effects.
5. Apply the promotion criteria below. Failing any criterion keeps the strategy in paper.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..strategies import BuyAndHold, RSI2MeanReversion, TrendSMA
from .engine import BacktestConfig, CostModel, bootstrap_mean_ci, metrics, run

GBPUSD = 1.30          # assumption used only to express account sizes in USD
GRIDS: dict[str, list[dict[str, Any]]] = {
    "trend_sma": [{"n": n} for n in (50, 100, 150, 200)],
    "rsi2_mr": [{"entry_th": e, "max_hold": h} for e in (5, 10, 15) for h in (5, 10)],
}
FACTORY = {"trend_sma": TrendSMA, "rsi2_mr": RSI2MeanReversion}
DIVIDEND_YIELD = {"SPY": 0.019, "QQQ": 0.010}   # approximate averages, 1999-2018

def no_fee(side: str, qty: float, price: float) -> float:
    return 0.0


# Execution-cost profiles. Spreads are full quoted spreads; half is paid per side.
PROFILES: dict[str, dict[str, Any]] = {
    "alpaca": {
        "costs": CostModel(spread_bps=2.0, slippage_bps=2.0),
        "fees": "US SEC + FINRA TAF on sells, each rounded up to the cent",
        "note": "US ETFs (SPY/QQQ) in USD at Alpaca.",
        "extra_stress": {"trading212_fx_15bps": CostModel(fx_bps=15, fee_fn=no_fee)},
    },
    "trading212_ucits": {
        "costs": CostModel(spread_bps=10.0, slippage_bps=2.0, fee_fn=no_fee),
        "fees": "none: no commission, GBP line so no FX fee, ETFs exempt from UK stamp duty",
        "note": ("Signals on SPY/QQQ, executed in London UCITS equivalents (VUSA/EQQQ) in GBP. The backtest "
                 "uses US index prices, so it ignores GBP/USD moves and UCITS tracking differences that a "
                 "GBP holder experiences; the 10 bps spread for London ETF lines is an assumption."),
        "extra_stress": {"spread_25bps": CostModel(spread_bps=25.0, slippage_bps=2.0, fee_fn=no_fee)},
    },
}

CRITERIA = {
    "min_oos_trades": 40,
    "oos_net_return_positive": True,
    "trade_mean_ci90_lower_bps_gt": 0.0,
    "max_oos_drawdown_pct": 25.0,
    "sharpe_at_least_buy_and_hold": True,
    "stress_3x_costs_positive": True,
    "stress_delay_1d_positive": True,
    "gbp10_net_return_positive": True,
    "validation_data_max_age_days": 120,      # evidence must include recent markets
    "forward_paper_min_sessions": 20,
    "forward_paper_min_closed_trades": 5,
}


def load_bundled() -> dict[str, pd.DataFrame]:
    from ..data.replay import load_proxy
    return {s: load_proxy(s) for s in ("SPY", "QQQ")}


def load_alpaca(symbols: list[str]) -> dict[str, pd.DataFrame]:
    from ..config import Settings
    from ..data.alpaca_data import AlpacaMarketData
    from ..strategies import bars_to_frame
    s = Settings.from_env()
    creds = s.alpaca_live or s.alpaca_paper
    if not creds:
        raise SystemExit("Alpaca credentials required for --source alpaca")
    md = AlpacaMarketData(creds, feed=s.alpaca_data_feed)
    now = datetime.now(timezone.utc)
    return {sym: bars_to_frame(md.daily_bars(sym, 2600, now)) for sym in symbols}


def _years(df: pd.DataFrame) -> list[int]:
    return sorted({d.year for d in df.index})


def _slice_bounds(df: pd.DataFrame, y0: int, y1: int):
    days = [d for d in df.index if y0 <= d.year <= y1]
    return (days[0], days[-1]) if days else (None, None)


def walk_forward(df: pd.DataFrame, family: str, cfg: BacktestConfig, train_years: int = 5,
                 fixed: list[dict[str, Any]] | None = None) -> tuple[pd.Series, list, list[dict[str, Any]], pd.Series]:
    """Walk-forward OOS run. ``fixed`` replays previously chosen parameters (used for
    stress tests, so only the stressed assumption changes - not the parameter path)."""
    fixed_by_year = {c["test_year"]: c for c in fixed} if fixed else None
    years = _years(df)
    oos_rets, trades, choices, expo = [], [], [], []
    for y in years[train_years + 1:]:
        tr0, tr1 = _slice_bounds(df, y - train_years, y - 1)
        te0, te1 = _slice_bounds(df, y, y)
        if te0 is None or tr0 is None:
            continue
        best, best_score = None, -1e9
        if fixed_by_year is not None:
            if y in fixed_by_year:
                best, best_score = fixed_by_year[y]["params"], fixed_by_year[y]["train_sharpe"]
        for params in ([] if fixed_by_year is not None else GRIDS[family]):
            strat = FACTORY[family](**params)
            if df.index.get_loc(tr0) < strat.history_needed:
                continue
            r = run(df.loc[:tr1], strat, cfg, trade_start=tr0, trade_end=tr1)
            m = metrics(r)
            score = m["sharpe"] if m["trades"] >= 10 else -1e6
            if score > best_score:
                best, best_score = params, score
        if best is None:
            continue
        strat = FACTORY[family](**best)
        r = run(df.loc[:te1], strat, cfg, trade_start=te0, trade_end=te1)
        oos_rets.append(r.equity.pct_change().fillna(0.0))
        expo.append(r.exposure)
        trades.extend(r.trades)
        choices.append({"test_year": y, "params": best, "train_sharpe": round(best_score, 3)})
    return pd.concat(oos_rets), trades, choices, pd.concat(expo)


def oos_metrics(rets: pd.Series, trades: list, expo: pd.Series, start_equity: float) -> dict[str, Any]:
    from .engine import Result
    eq = start_equity * (1 + rets).cumprod()
    m = metrics(Result(eq, trades, expo, turnover_notional=0.0))
    # each trade buys and sells ~100% of equity (position_fraction=1)
    m["annual_turnover_x"] = round(2 * len(trades) / max(len(eq) / 252, 1e-9), 2)
    tr = np.array([t.net_ret for t in trades])
    lo, hi = bootstrap_mean_ci(tr) if len(tr) else (float("nan"), float("nan"))
    dlo, dhi = bootstrap_mean_ci(rets.to_numpy(), block=20)
    m["trade_mean_ci90_bps"] = [round(lo * 1e4, 1), round(hi * 1e4, 1)]
    m["daily_mean_ci90_bps"] = [round(dlo * 1e4, 2), round(dhi * 1e4, 2)]
    return m


def baseline(df: pd.DataFrame, cfg: BacktestConfig, start, end) -> dict[str, Any]:
    r = run(df.loc[:end], BuyAndHold(), cfg, trade_start=start, trade_end=end)
    return metrics(r)


def evaluate(frames: dict[str, pd.DataFrame], source: str, train_years: int = 5,
             profile: str = "alpaca") -> dict[str, Any]:
    prof = PROFILES[profile]
    base_costs: CostModel = prof["costs"]
    usd = profile == "alpaca"
    out: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "source": source,
                           "profile": profile, "criteria": CRITERIA, "assumptions": {
                               "execution": "signal at close t, fill at open t+1 (+half spread +slippage)",
                               "profile_note": prof["note"],
                               "base_costs": {k: v for k, v in asdict(base_costs).items() if k != "fee_fn"}
                               | {"fees": prof["fees"]},
                               "modelled_round_trip_bps": base_costs.spread_bps + 2 * base_costs.slippage_bps,
                               "gbpusd_for_account_sizes": GBPUSD,
                               "dividend_yield_accrual": DIVIDEND_YIELD,
                               "train_years": train_years}, "results": []}
    for sym, df in frames.items():
        div = DIVIDEND_YIELD.get(sym, 0.0)
        base_cfg = BacktestConfig(start_equity=1000 * (GBPUSD if usd else 1), dividend_yield=div, costs=base_costs)
        for family in GRIDS:
            rets, trades, choices, expo = walk_forward(df, family, base_cfg, train_years)
            oos_start, oos_end = rets.index[0], rets.index[-1]
            m = oos_metrics(rets, trades, expo, base_cfg.start_equity)
            bh = baseline(df, base_cfg, oos_start, oos_end)
            stress = {}
            scenarios = {
                "costs_3x": base_costs.scaled(3),
                "delay_1d": base_costs,
                "stop_gap_100bps": replace(base_costs, stop_extra_bps=100),
                **prof["extra_stress"],
            }
            for label, costs in scenarios.items():
                cfg = BacktestConfig(start_equity=base_cfg.start_equity, dividend_yield=div, costs=costs,
                                     delay_days=1 if label == "delay_1d" else 0)
                r2, t2, _, e2 = walk_forward(df, family, cfg, train_years, fixed=choices)
                sm = oos_metrics(r2, t2, e2, cfg.start_equity)
                stress[label] = {k: sm[k] for k in ("total_return_pct", "cagr_pct", "sharpe",
                                                    "max_drawdown_pct", "trades", "avg_trade_bps")}
            sizes = {}
            for gbp in (10, 100, 1000):
                cfg = BacktestConfig(start_equity=gbp * (GBPUSD if usd else 1), dividend_yield=div, costs=base_costs)
                r3, t3, _, e3 = walk_forward(df, family, cfg, train_years, fixed=choices)
                sm = oos_metrics(r3, t3, e3, cfg.start_equity)
                sizes[f"gbp_{gbp}"] = {k: sm[k] for k in ("total_return_pct", "cagr_pct", "avg_trade_bps",
                                                         "trades", "skipped_below_min_size")}
            data_end = pd.Timestamp(df.index[-1]).date()
            age_days = (date.today() - data_end).days
            checks = {
                "min_oos_trades": m["trades"] >= CRITERIA["min_oos_trades"],
                "oos_net_return_positive": m["total_return_pct"] > 0,
                "trade_mean_ci90_lower_bps_gt": m["trade_mean_ci90_bps"][0] > CRITERIA["trade_mean_ci90_lower_bps_gt"],
                "max_oos_drawdown_pct": abs(m["max_drawdown_pct"]) <= CRITERIA["max_oos_drawdown_pct"],
                "sharpe_at_least_buy_and_hold": m["sharpe"] >= bh["sharpe"],
                "stress_3x_costs_positive": stress["costs_3x"]["total_return_pct"] > 0,
                "stress_delay_1d_positive": stress["delay_1d"]["total_return_pct"] > 0,
                "gbp10_net_return_positive": sizes["gbp_10"]["total_return_pct"] > 0,
                "validation_data_max_age_days": age_days <= CRITERIA["validation_data_max_age_days"],
                "forward_paper_min_sessions": False,      # evaluated by the running engine
                "forward_paper_min_closed_trades": False,
            }
            backtest_pass = all(v for k, v in checks.items() if not k.startswith("forward_paper")
                                and k != "validation_data_max_age_days")
            out["results"].append({
                "symbol": sym, "strategy": family, "data_start": str(df.index[0]), "data_end": str(data_end),
                "oos": m, "buy_and_hold_same_period": bh, "stress": stress, "account_sizes": sizes,
                "param_choices": choices, "checks": checks, "backtest_criteria_pass": backtest_pass,
                "eligible_for_live": all(checks.values()),
                "expected_edge_bps": m["avg_trade_bps"],
            })
    return out


def to_markdown(rep: dict[str, Any]) -> str:
    lines = [f"# Strategy validation results: `{rep.get('profile', 'alpaca')}` cost profile\n",
             f"Generated {rep['generated_at']} from source `{rep['source']}`. "
             f"{rep['assumptions'].get('profile_note', '')}\n",
             "All figures are **net of modelled costs** and **out-of-sample** (walk-forward: parameters "
             "chosen on the prior 5 years, traded on the next year). They are historical simulations, "
             "not forecasts, and index-level proxies (see assumptions).\n",
             "## Summary\n",
             "| Symbol | Strategy | OOS period | Net CAGR | Sharpe | Max DD | Exposure | Trades | Avg trade | Trade-mean 90% CI | Buy&hold CAGR / Sharpe / Max DD | Backtest gate | Live eligible |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep["results"]:
        m, b = r["oos"], r["buy_and_hold_same_period"]
        lines.append(
            f"| {r['symbol']} | {r['strategy']} | {m['start']}→{m['end']} | {m['cagr_pct']}% | {m['sharpe']} | "
            f"{m['max_drawdown_pct']}% | {m['exposure_pct']}% | {m['trades']} | {m['avg_trade_bps']} bps | "
            f"{m['trade_mean_ci90_bps'][0]}…{m['trade_mean_ci90_bps'][1]} bps | {b['cagr_pct']}% / {b['sharpe']} / {b['max_drawdown_pct']}% | "
            f"{'PASS' if r['backtest_criteria_pass'] else 'FAIL'} | {'yes' if r['eligible_for_live'] else 'NO'} |")
    for r in rep["results"]:
        m = r["oos"]
        lines += [f"\n## {r['symbol']} · {r['strategy']}\n",
                  f"Data {r['data_start']} → {r['data_end']}. OOS {m['start']} → {m['end']} ({m['years']} years).\n",
                  "| Metric | Value |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in m.items()]
        lines += ["\n**Stress tests (same OOS parameter path, one assumption changed):**\n", "| Scenario | Total return | CAGR | Sharpe | Max DD | Trades | Avg trade bps |",
                  "|---|---|---|---|---|---|---|"]
        for k, s in r["stress"].items():
            lines.append(f"| {k} | {s['total_return_pct']}% | {s['cagr_pct']}% | {s['sharpe']} | "
                         f"{s['max_drawdown_pct']}% | {s['trades']} | {s['avg_trade_bps']} |")
        lines += ["\n**Account size effects:**\n", "| Start | Total return | CAGR | Avg trade bps | Trades | Skipped (below min order) |",
                  "|---|---|---|---|---|---|"]
        for k, s in r["account_sizes"].items():
            lines.append(f"| £{k.split('_')[1]} | {s['total_return_pct']}% | {s['cagr_pct']}% | {s['avg_trade_bps']} | "
                         f"{s['trades']} | {s['skipped_below_min_size']} |")
        lines += ["\n**Promotion checks:**\n"]
        lines += [f"- {'✅' if v else '❌'} `{k}`" for k, v in r["checks"].items()]
        lines += ["\n**Walk-forward parameter choices:** " +
                  ", ".join(f"{c['test_year']}: {c['params']}" for c in r["param_choices"])]
    lines += ["\n## Assumptions\n", "```json", json.dumps(rep["assumptions"], indent=2, default=str), "```"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["bundled", "alpaca"], default="bundled")
    ap.add_argument("--symbols", default="SPY,QQQ")
    ap.add_argument("--out", default="var/validation")
    ap.add_argument("--profile", choices=[*PROFILES, "all"], default="all")
    a = ap.parse_args()
    frames = load_bundled() if a.source == "bundled" else load_alpaca(a.symbols.split(","))
    for profile in (PROFILES if a.profile == "all" else [a.profile]):
        rep = evaluate(frames, a.source, profile=profile)
        out = Path(a.out) / profile
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(rep, indent=2, default=str))
        (out / "RESULTS.md").write_text(to_markdown(rep))
        for r in rep["results"]:
            print(profile, r["symbol"], r["strategy"], "backtest", "PASS" if r["backtest_criteria_pass"] else "FAIL",
                  "live-eligible" if r["eligible_for_live"] else "not live-eligible", r["oos"]["cagr_pct"])


if __name__ == "__main__":
    main()
