# Strategy validation results: `alpaca` cost profile

Generated 2026-09-24T07:41:06.488924+00:00 from source `bundled`. US ETFs (SPY/QQQ) in USD at Alpaca.

All figures are **net of modelled costs** and **out-of-sample** (walk-forward: parameters chosen on the prior 5 years, traded on the next year). They are historical simulations, not forecasts, and index-level proxies (see assumptions).

## Summary

| Symbol | Strategy | OOS period | Net CAGR | Sharpe | Max DD | Exposure | Trades | Avg trade | Trade-mean 90% CI | Buy&hold CAGR / Sharpe / Max DD | Backtest gate | Live eligible |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | trend_sma | 2005-01-03→2018-12-31 | 3.94% | 0.407 | -29.28% | 77.0% | 93 | 46.7 bps | -28.8…135.8 bps | 6.6% / 0.463 / -52.39% | FAIL | NO |
| SPY | rsi2_mr | 2005-01-03→2018-12-31 | 2.15% | 0.446 | -8.47% | 10.8% | 130 | 21.7 bps | 1.3…41.7 bps | 6.6% / 0.463 / -52.39% | FAIL | NO |
| QQQ | trend_sma | 2005-01-03→2018-12-31 | 6.5% | 0.57 | -28.75% | 69.1% | 93 | 101.2 bps | 4.6…211.1 bps | 8.93% / 0.538 / -53.41% | FAIL | NO |
| QQQ | rsi2_mr | 2005-01-03→2018-12-31 | 1.53% | 0.313 | -12.21% | 8.4% | 85 | 25.5 bps | -9.6…59.1 bps | 8.93% / 0.538 / -53.41% | FAIL | NO |

## SPY · trend_sma

Data 1999-01-04 → 2018-12-31. OOS 2005-01-03 → 2018-12-31 (13.98 years).

| Metric | Value |
|---|---|
| start | 2005-01-03 |
| end | 2018-12-31 |
| years | 13.98 |
| total_return_pct | 71.61 |
| cagr_pct | 3.94 |
| ann_vol_pct | 10.99 |
| sharpe | 0.407 |
| max_drawdown_pct | -29.28 |
| longest_drawdown_days | 856 |
| exposure_pct | 77.0 |
| trades | 93 |
| win_rate_pct | 29.0 |
| avg_trade_bps | 46.7 |
| median_trade_bps | -71.0 |
| worst_trade_pct | -5.01 |
| annual_turnover_x | 13.3 |
| losing_months_pct | 35.3 |
| worst_month_pct | -6.67 |
| skipped_below_min_size | 0 |
| trade_mean_ci90_bps | [-28.8, 135.8] |
| daily_mean_ci90_bps | [0.07, 3.56] |

**Stress tests (same OOS parameter path, one assumption changed):**

| Scenario | Total return | CAGR | Sharpe | Max DD | Trades | Avg trade bps |
|---|---|---|---|---|---|---|
| costs_3x | 53.5% | 3.11% | 0.334 | -30.96% | 93 | 34.7 |
| delay_1d | 76.41% | 4.14% | 0.426 | -25.2% | 81 | 56.7 |
| stop_gap_100bps | 71.61% | 3.94% | 0.407 | -29.28% | 93 | 46.7 |
| trading212_fx_15bps | 30.24% | 1.91% | 0.227 | -33.36% | 93 | 16.9 |

**Account size effects:**

| Start | Total return | CAGR | Avg trade bps | Trades | Skipped (below min order) |
|---|---|---|---|---|---|
| £10 | 48.8% | 2.88% | 31.3 | 93 | 0 |
| £100 | 69.63% | 3.85% | 45.5 | 93 | 0 |
| £1000 | 71.61% | 3.94% | 46.7 | 93 | 0 |

**Promotion checks:**

- ✅ `min_oos_trades`
- ✅ `oos_net_return_positive`
- ❌ `trade_mean_ci90_lower_bps_gt`
- ❌ `max_oos_drawdown_pct`
- ❌ `sharpe_at_least_buy_and_hold`
- ✅ `stress_3x_costs_positive`
- ✅ `stress_delay_1d_positive`
- ✅ `gbp10_net_return_positive`
- ❌ `validation_data_max_age_days`
- ❌ `forward_paper_min_sessions`
- ❌ `forward_paper_min_closed_trades`

**Walk-forward parameter choices:** 2005: {'n': 200}, 2006: {'n': 200}, 2007: {'n': 200}, 2008: {'n': 50}, 2009: {'n': 100}, 2010: {'n': 100}, 2011: {'n': 100}, 2012: {'n': 100}, 2013: {'n': 100}, 2014: {'n': 100}, 2015: {'n': 200}, 2016: {'n': 200}, 2017: {'n': 200}, 2018: {'n': 200}

## SPY · rsi2_mr

Data 1999-01-04 → 2018-12-31. OOS 2005-01-03 → 2018-12-31 (13.98 years).

| Metric | Value |
|---|---|
| start | 2005-01-03 |
| end | 2018-12-31 |
| years | 13.98 |
| total_return_pct | 34.63 |
| cagr_pct | 2.15 |
| ann_vol_pct | 5.05 |
| sharpe | 0.446 |
| max_drawdown_pct | -8.47 |
| longest_drawdown_days | 644 |
| exposure_pct | 10.8 |
| trades | 130 |
| win_rate_pct | 72.3 |
| avg_trade_bps | 21.7 |
| median_trade_bps | 47.7 |
| worst_trade_pct | -6.06 |
| annual_turnover_x | 18.6 |
| losing_months_pct | 18.0 |
| worst_month_pct | -6.03 |
| skipped_below_min_size | 0 |
| trade_mean_ci90_bps | [1.3, 41.7] |
| daily_mean_ci90_bps | [0.14, 1.66] |

**Stress tests (same OOS parameter path, one assumption changed):**

| Scenario | Total return | CAGR | Sharpe | Max DD | Trades | Avg trade bps |
|---|---|---|---|---|---|---|
| costs_3x | 15.18% | 1.02% | 0.225 | -9.01% | 130 | 9.7 |
| delay_1d | 17.83% | 1.18% | 0.252 | -12.87% | 123 | 11.7 |
| stop_gap_100bps | 30.63% | 1.93% | 0.398 | -9.38% | 130 | 19.5 |
| trading212_fx_15bps | -8.49% | -0.63% | -0.099 | -13.78% | 130 | -8.0 |

**Account size effects:**

| Start | Total return | CAGR | Avg trade bps | Trades | Skipped (below min order) |
|---|---|---|---|---|---|
| £10 | 10.77% | 0.73% | 6.7 | 130 | 0 |
| £100 | 32.52% | 2.03% | 20.5 | 130 | 0 |
| £1000 | 34.63% | 2.15% | 21.7 | 130 | 0 |

**Promotion checks:**

- ✅ `min_oos_trades`
- ✅ `oos_net_return_positive`
- ✅ `trade_mean_ci90_lower_bps_gt`
- ✅ `max_oos_drawdown_pct`
- ❌ `sharpe_at_least_buy_and_hold`
- ✅ `stress_3x_costs_positive`
- ✅ `stress_delay_1d_positive`
- ✅ `gbp10_net_return_positive`
- ❌ `validation_data_max_age_days`
- ❌ `forward_paper_min_sessions`
- ❌ `forward_paper_min_closed_trades`

**Walk-forward parameter choices:** 2005: {'entry_th': 10, 'max_hold': 5}, 2006: {'entry_th': 10, 'max_hold': 10}, 2007: {'entry_th': 10, 'max_hold': 10}, 2008: {'entry_th': 10, 'max_hold': 10}, 2009: {'entry_th': 5, 'max_hold': 10}, 2010: {'entry_th': 5, 'max_hold': 10}, 2011: {'entry_th': 5, 'max_hold': 10}, 2012: {'entry_th': 5, 'max_hold': 5}, 2013: {'entry_th': 15, 'max_hold': 5}, 2014: {'entry_th': 15, 'max_hold': 5}, 2015: {'entry_th': 15, 'max_hold': 5}, 2016: {'entry_th': 15, 'max_hold': 5}, 2017: {'entry_th': 15, 'max_hold': 5}, 2018: {'entry_th': 15, 'max_hold': 5}

## QQQ · trend_sma

Data 1999-01-04 → 2018-12-31. OOS 2005-01-03 → 2018-12-31 (13.98 years).

| Metric | Value |
|---|---|
| start | 2005-01-03 |
| end | 2018-12-31 |
| years | 13.98 |
| total_return_pct | 141.29 |
| cagr_pct | 6.5 |
| ann_vol_pct | 12.41 |
| sharpe | 0.57 |
| max_drawdown_pct | -28.75 |
| longest_drawdown_days | 475 |
| exposure_pct | 69.1 |
| trades | 93 |
| win_rate_pct | 31.2 |
| avg_trade_bps | 101.2 |
| median_trade_bps | -77.5 |
| worst_trade_pct | -6.95 |
| annual_turnover_x | 13.3 |
| losing_months_pct | 40.1 |
| worst_month_pct | -9.84 |
| skipped_below_min_size | 0 |
| trade_mean_ci90_bps | [4.6, 211.1] |
| daily_mean_ci90_bps | [0.84, 4.94] |

**Stress tests (same OOS parameter path, one assumption changed):**

| Scenario | Total return | CAGR | Sharpe | Max DD | Trades | Avg trade bps |
|---|---|---|---|---|---|---|
| costs_3x | 115.82% | 5.66% | 0.506 | -30.08% | 93 | 89.1 |
| delay_1d | 148.76% | 6.74% | 0.582 | -32.85% | 83 | 117.3 |
| stop_gap_100bps | 141.29% | 6.5% | 0.57 | -28.75% | 93 | 101.2 |
| trading212_fx_15bps | 83.1% | 4.42% | 0.411 | -32.03% | 93 | 71.2 |

**Account size effects:**

| Start | Total return | CAGR | Avg trade bps | Trades | Skipped (below min order) |
|---|---|---|---|---|---|
| £10 | 109.57% | 5.43% | 85.9 | 93 | 0 |
| £100 | 138.55% | 6.42% | 100.0 | 93 | 0 |
| £1000 | 141.29% | 6.5% | 101.2 | 93 | 0 |

**Promotion checks:**

- ✅ `min_oos_trades`
- ✅ `oos_net_return_positive`
- ✅ `trade_mean_ci90_lower_bps_gt`
- ❌ `max_oos_drawdown_pct`
- ✅ `sharpe_at_least_buy_and_hold`
- ✅ `stress_3x_costs_positive`
- ✅ `stress_delay_1d_positive`
- ✅ `gbp10_net_return_positive`
- ❌ `validation_data_max_age_days`
- ❌ `forward_paper_min_sessions`
- ❌ `forward_paper_min_closed_trades`

**Walk-forward parameter choices:** 2005: {'n': 50}, 2006: {'n': 200}, 2007: {'n': 50}, 2008: {'n': 50}, 2009: {'n': 50}, 2010: {'n': 100}, 2011: {'n': 100}, 2012: {'n': 100}, 2013: {'n': 100}, 2014: {'n': 100}, 2015: {'n': 50}, 2016: {'n': 200}, 2017: {'n': 200}, 2018: {'n': 200}

## QQQ · rsi2_mr

Data 1999-01-04 → 2018-12-31. OOS 2005-01-03 → 2018-12-31 (13.98 years).

| Metric | Value |
|---|---|
| start | 2005-01-03 |
| end | 2018-12-31 |
| years | 13.98 |
| total_return_pct | 23.69 |
| cagr_pct | 1.53 |
| ann_vol_pct | 5.32 |
| sharpe | 0.313 |
| max_drawdown_pct | -12.21 |
| longest_drawdown_days | 1176 |
| exposure_pct | 8.4 |
| trades | 85 |
| win_rate_pct | 71.8 |
| avg_trade_bps | 25.5 |
| median_trade_bps | 55.9 |
| worst_trade_pct | -6.24 |
| annual_turnover_x | 12.16 |
| losing_months_pct | 13.2 |
| worst_month_pct | -6.22 |
| skipped_below_min_size | 0 |
| trade_mean_ci90_bps | [-9.6, 59.1] |
| daily_mean_ci90_bps | [-0.1, 1.55] |

**Stress tests (same OOS parameter path, one assumption changed):**

| Scenario | Total return | CAGR | Sharpe | Max DD | Trades | Avg trade bps |
|---|---|---|---|---|---|---|
| costs_3x | 11.7% | 0.79% | 0.175 | -12.89% | 85 | 13.5 |
| delay_1d | 9.73% | 0.67% | 0.151 | -12.74% | 85 | 11.1 |
| stop_gap_100bps | 20.02% | 1.31% | 0.268 | -13.09% | 85 | 22.2 |
| trading212_fx_15bps | -3.9% | -0.28% | -0.026 | -14.42% | 85 | -4.2 |

**Account size effects:**

| Start | Total return | CAGR | Avg trade bps | Trades | Skipped (below min order) |
|---|---|---|---|---|---|
| £10 | 8.81% | 0.61% | 10.4 | 85 | 0 |
| £100 | 22.41% | 1.46% | 24.3 | 85 | 0 |
| £1000 | 23.69% | 1.53% | 25.5 | 85 | 0 |

**Promotion checks:**

- ✅ `min_oos_trades`
- ✅ `oos_net_return_positive`
- ❌ `trade_mean_ci90_lower_bps_gt`
- ✅ `max_oos_drawdown_pct`
- ❌ `sharpe_at_least_buy_and_hold`
- ✅ `stress_3x_costs_positive`
- ✅ `stress_delay_1d_positive`
- ✅ `gbp10_net_return_positive`
- ❌ `validation_data_max_age_days`
- ❌ `forward_paper_min_sessions`
- ❌ `forward_paper_min_closed_trades`

**Walk-forward parameter choices:** 2005: {'entry_th': 10, 'max_hold': 10}, 2006: {'entry_th': 5, 'max_hold': 10}, 2007: {'entry_th': 5, 'max_hold': 10}, 2008: {'entry_th': 5, 'max_hold': 10}, 2009: {'entry_th': 5, 'max_hold': 10}, 2010: {'entry_th': 5, 'max_hold': 10}, 2011: {'entry_th': 5, 'max_hold': 10}, 2012: {'entry_th': 5, 'max_hold': 10}, 2013: {'entry_th': 10, 'max_hold': 5}, 2014: {'entry_th': 10, 'max_hold': 5}, 2015: {'entry_th': 10, 'max_hold': 5}, 2016: {'entry_th': 10, 'max_hold': 5}, 2017: {'entry_th': 10, 'max_hold': 5}, 2018: {'entry_th': 15, 'max_hold': 10}

## Assumptions

```json
{
  "execution": "signal at close t, fill at open t+1 (+half spread +slippage)",
  "profile_note": "US ETFs (SPY/QQQ) in USD at Alpaca.",
  "base_costs": {
    "spread_bps": 2.0,
    "slippage_bps": 2.0,
    "fx_bps": 0.0,
    "stop_extra_bps": 0.0,
    "fees": "US SEC + FINRA TAF on sells, each rounded up to the cent"
  },
  "modelled_round_trip_bps": 6.0,
  "gbpusd_for_account_sizes": 1.3,
  "dividend_yield_accrual": {
    "SPY": 0.019,
    "QQQ": 0.01
  },
  "train_years": 5
}
```
