# Strategy validation results: `trading212_ucits` cost profile

Generated 2026-09-24T07:41:48.603015+00:00 from source `bundled`. Signals on SPY/QQQ, executed in London UCITS equivalents (VUSA/EQQQ) in GBP. The backtest uses US index prices, so it ignores GBP/USD moves and UCITS tracking differences that a GBP holder experiences; the 10 bps spread for London ETF lines is an assumption.

All figures are **net of modelled costs** and **out-of-sample** (walk-forward: parameters chosen on the prior 5 years, traded on the next year). They are historical simulations, not forecasts, and index-level proxies (see assumptions).

## Summary

| Symbol | Strategy | OOS period | Net CAGR | Sharpe | Max DD | Exposure | Trades | Avg trade | Trade-mean 90% CI | Buy&hold CAGR / Sharpe / Max DD | Backtest gate | Live eligible |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | trend_sma | 2005-01-03→2018-12-31 | 4.58% | 0.469 | -18.52% | 75.4% | 83 | 62.8 bps | -20.8…159.2 bps | 6.6% / 0.462 / -52.39% | FAIL | NO |
| SPY | rsi2_mr | 2005-01-03→2018-12-31 | 1.26% | 0.271 | -10.37% | 11.0% | 129 | 12.4 bps | -9.0…33.3 bps | 6.6% / 0.462 / -52.39% | FAIL | NO |
| QQQ | trend_sma | 2005-01-03→2018-12-31 | 5.96% | 0.529 | -29.59% | 69.1% | 93 | 93.4 bps | -3.1…203.2 bps | 8.92% / 0.538 / -53.41% | FAIL | NO |
| QQQ | rsi2_mr | 2005-01-03→2018-12-31 | 1.06% | 0.224 | -12.63% | 8.4% | 85 | 17.8 bps | -17.2…51.4 bps | 8.92% / 0.538 / -53.41% | FAIL | NO |

## SPY · trend_sma

Data 1999-01-04 → 2018-12-31. OOS 2005-01-03 → 2018-12-31 (13.98 years).

| Metric | Value |
|---|---|
| start | 2005-01-03 |
| end | 2018-12-31 |
| years | 13.98 |
| total_return_pct | 86.95 |
| cagr_pct | 4.58 |
| ann_vol_pct | 10.78 |
| sharpe | 0.469 |
| max_drawdown_pct | -18.52 |
| longest_drawdown_days | 675 |
| exposure_pct | 75.4 |
| trades | 83 |
| win_rate_pct | 28.9 |
| avg_trade_bps | 62.8 |
| median_trade_bps | -61.9 |
| worst_trade_pct | -5.08 |
| annual_turnover_x | 11.87 |
| losing_months_pct | 32.3 |
| worst_month_pct | -6.78 |
| skipped_below_min_size | 0 |
| trade_mean_ci90_bps | [-20.8, 159.2] |
| daily_mean_ci90_bps | [0.33, 3.8] |

**Stress tests (same OOS parameter path, one assumption changed):**

| Scenario | Total return | CAGR | Sharpe | Max DD | Trades | Avg trade bps |
|---|---|---|---|---|---|---|
| costs_3x | 48.22% | 2.85% | 0.315 | -20.77% | 83 | 34.7 |
| delay_1d | 80.91% | 4.33% | 0.448 | -19.49% | 72 | 67.7 |
| stop_gap_100bps | 86.95% | 4.58% | 0.469 | -18.52% | 83 | 62.8 |
| spread_25bps | 65.09% | 3.65% | 0.387 | -19.73% | 83 | 47.8 |

**Account size effects:**

| Start | Total return | CAGR | Avg trade bps | Trades | Skipped (below min order) |
|---|---|---|---|---|---|
| £10 | 86.95% | 4.58% | 62.8 | 83 | 0 |
| £100 | 86.95% | 4.58% | 62.8 | 83 | 0 |
| £1000 | 86.95% | 4.58% | 62.8 | 83 | 0 |

**Promotion checks:**

- ✅ `min_oos_trades`
- ✅ `oos_net_return_positive`
- ❌ `trade_mean_ci90_lower_bps_gt`
- ✅ `max_oos_drawdown_pct`
- ✅ `sharpe_at_least_buy_and_hold`
- ✅ `stress_3x_costs_positive`
- ✅ `stress_delay_1d_positive`
- ✅ `gbp10_net_return_positive`
- ❌ `validation_data_max_age_days`
- ❌ `forward_paper_min_sessions`
- ❌ `forward_paper_min_closed_trades`

**Walk-forward parameter choices:** 2005: {'n': 200}, 2006: {'n': 200}, 2007: {'n': 200}, 2008: {'n': 200}, 2009: {'n': 100}, 2010: {'n': 100}, 2011: {'n': 100}, 2012: {'n': 100}, 2013: {'n': 100}, 2014: {'n': 100}, 2015: {'n': 200}, 2016: {'n': 200}, 2017: {'n': 200}, 2018: {'n': 200}

## SPY · rsi2_mr

Data 1999-01-04 → 2018-12-31. OOS 2005-01-03 → 2018-12-31 (13.98 years).

| Metric | Value |
|---|---|
| start | 2005-01-03 |
| end | 2018-12-31 |
| years | 13.98 |
| total_return_pct | 19.14 |
| cagr_pct | 1.26 |
| ann_vol_pct | 5.1 |
| sharpe | 0.271 |
| max_drawdown_pct | -10.37 |
| longest_drawdown_days | 645 |
| exposure_pct | 11.0 |
| trades | 129 |
| win_rate_pct | 68.2 |
| avg_trade_bps | 12.4 |
| median_trade_bps | 40.7 |
| worst_trade_pct | -6.13 |
| annual_turnover_x | 18.45 |
| losing_months_pct | 22.2 |
| worst_month_pct | -6.1 |
| skipped_below_min_size | 0 |
| trade_mean_ci90_bps | [-9.0, 33.3] |
| daily_mean_ci90_bps | [-0.21, 1.32] |

**Stress tests (same OOS parameter path, one assumption changed):**

| Scenario | Total return | CAGR | Sharpe | Max DD | Trades | Avg trade bps |
|---|---|---|---|---|---|---|
| costs_3x | -16.97% | -1.32% | -0.233 | -19.65% | 129 | -15.6 |
| delay_1d | 7.91% | 0.55% | 0.13 | -14.63% | 123 | 4.5 |
| stop_gap_100bps | 15.6% | 1.04% | 0.226 | -12.15% | 129 | 10.2 |
| spread_25bps | -1.81% | -0.13% | 0.0 | -13.05% | 129 | -2.6 |

**Account size effects:**

| Start | Total return | CAGR | Avg trade bps | Trades | Skipped (below min order) |
|---|---|---|---|---|---|
| £10 | 19.14% | 1.26% | 12.4 | 129 | 0 |
| £100 | 19.14% | 1.26% | 12.4 | 129 | 0 |
| £1000 | 19.14% | 1.26% | 12.4 | 129 | 0 |

**Promotion checks:**

- ✅ `min_oos_trades`
- ✅ `oos_net_return_positive`
- ❌ `trade_mean_ci90_lower_bps_gt`
- ✅ `max_oos_drawdown_pct`
- ❌ `sharpe_at_least_buy_and_hold`
- ❌ `stress_3x_costs_positive`
- ✅ `stress_delay_1d_positive`
- ✅ `gbp10_net_return_positive`
- ❌ `validation_data_max_age_days`
- ❌ `forward_paper_min_sessions`
- ❌ `forward_paper_min_closed_trades`

**Walk-forward parameter choices:** 2005: {'entry_th': 10, 'max_hold': 5}, 2006: {'entry_th': 10, 'max_hold': 10}, 2007: {'entry_th': 10, 'max_hold': 10}, 2008: {'entry_th': 5, 'max_hold': 10}, 2009: {'entry_th': 5, 'max_hold': 10}, 2010: {'entry_th': 5, 'max_hold': 10}, 2011: {'entry_th': 5, 'max_hold': 10}, 2012: {'entry_th': 5, 'max_hold': 5}, 2013: {'entry_th': 15, 'max_hold': 5}, 2014: {'entry_th': 15, 'max_hold': 5}, 2015: {'entry_th': 15, 'max_hold': 5}, 2016: {'entry_th': 15, 'max_hold': 5}, 2017: {'entry_th': 15, 'max_hold': 5}, 2018: {'entry_th': 15, 'max_hold': 10}

## QQQ · trend_sma

Data 1999-01-04 → 2018-12-31. OOS 2005-01-03 → 2018-12-31 (13.98 years).

| Metric | Value |
|---|---|
| start | 2005-01-03 |
| end | 2018-12-31 |
| years | 13.98 |
| total_return_pct | 124.65 |
| cagr_pct | 5.96 |
| ann_vol_pct | 12.41 |
| sharpe | 0.529 |
| max_drawdown_pct | -29.59 |
| longest_drawdown_days | 491 |
| exposure_pct | 69.1 |
| trades | 93 |
| win_rate_pct | 31.2 |
| avg_trade_bps | 93.4 |
| median_trade_bps | -85.1 |
| worst_trade_pct | -7.02 |
| annual_turnover_x | 13.3 |
| losing_months_pct | 40.1 |
| worst_month_pct | -9.94 |
| skipped_below_min_size | 0 |
| trade_mean_ci90_bps | [-3.1, 203.2] |
| daily_mean_ci90_bps | [0.62, 4.76] |

**Stress tests (same OOS parameter path, one assumption changed):**

| Scenario | Total return | CAGR | Sharpe | Max DD | Trades | Avg trade bps |
|---|---|---|---|---|---|---|
| costs_3x | 73.17% | 4.01% | 0.378 | -32.68% | 93 | 65.2 |
| delay_1d | 133.39% | 6.25% | 0.546 | -33.52% | 83 | 109.6 |
| stop_gap_100bps | 124.65% | 5.96% | 0.529 | -29.59% | 93 | 93.4 |
| spread_25bps | 95.41% | 4.91% | 0.448 | -31.26% | 93 | 78.3 |

**Account size effects:**

| Start | Total return | CAGR | Avg trade bps | Trades | Skipped (below min order) |
|---|---|---|---|---|---|
| £10 | 124.65% | 5.96% | 93.4 | 93 | 0 |
| £100 | 124.65% | 5.96% | 93.4 | 93 | 0 |
| £1000 | 124.65% | 5.96% | 93.4 | 93 | 0 |

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

**Walk-forward parameter choices:** 2005: {'n': 50}, 2006: {'n': 200}, 2007: {'n': 50}, 2008: {'n': 50}, 2009: {'n': 50}, 2010: {'n': 100}, 2011: {'n': 100}, 2012: {'n': 100}, 2013: {'n': 100}, 2014: {'n': 100}, 2015: {'n': 50}, 2016: {'n': 200}, 2017: {'n': 200}, 2018: {'n': 200}

## QQQ · rsi2_mr

Data 1999-01-04 → 2018-12-31. OOS 2005-01-03 → 2018-12-31 (13.98 years).

| Metric | Value |
|---|---|
| start | 2005-01-03 |
| end | 2018-12-31 |
| years | 13.98 |
| total_return_pct | 15.86 |
| cagr_pct | 1.06 |
| ann_vol_pct | 5.33 |
| sharpe | 0.224 |
| max_drawdown_pct | -12.63 |
| longest_drawdown_days | 1214 |
| exposure_pct | 8.4 |
| trades | 85 |
| win_rate_pct | 69.4 |
| avg_trade_bps | 17.8 |
| median_trade_bps | 48.2 |
| worst_trade_pct | -6.31 |
| annual_turnover_x | 12.16 |
| losing_months_pct | 13.8 |
| worst_month_pct | -6.3 |
| skipped_below_min_size | 0 |
| trade_mean_ci90_bps | [-17.2, 51.4] |
| daily_mean_ci90_bps | [-0.28, 1.36] |

**Stress tests (same OOS parameter path, one assumption changed):**

| Scenario | Total return | CAGR | Sharpe | Max DD | Trades | Avg trade bps |
|---|---|---|---|---|---|---|
| costs_3x | -8.68% | -0.65% | -0.093 | -15.21% | 85 | -10.2 |
| delay_1d | 2.78% | 0.2% | 0.064 | -13.01% | 85 | 3.4 |
| stop_gap_100bps | 12.42% | 0.84% | 0.182 | -13.5% | 85 | 14.5 |
| spread_25bps | 1.99% | 0.14% | 0.053 | -13.54% | 85 | 2.8 |

**Account size effects:**

| Start | Total return | CAGR | Avg trade bps | Trades | Skipped (below min order) |
|---|---|---|---|---|---|
| £10 | 15.86% | 1.06% | 17.8 | 85 | 0 |
| £100 | 15.86% | 1.06% | 17.8 | 85 | 0 |
| £1000 | 15.86% | 1.06% | 17.8 | 85 | 0 |

**Promotion checks:**

- ✅ `min_oos_trades`
- ✅ `oos_net_return_positive`
- ❌ `trade_mean_ci90_lower_bps_gt`
- ✅ `max_oos_drawdown_pct`
- ❌ `sharpe_at_least_buy_and_hold`
- ❌ `stress_3x_costs_positive`
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
  "profile_note": "Signals on SPY/QQQ, executed in London UCITS equivalents (VUSA/EQQQ) in GBP. The backtest uses US index prices, so it ignores GBP/USD moves and UCITS tracking differences that a GBP holder experiences; the 10 bps spread for London ETF lines is an assumption.",
  "base_costs": {
    "spread_bps": 10.0,
    "slippage_bps": 2.0,
    "fx_bps": 0.0,
    "stop_extra_bps": 0.0,
    "fees": "none: no commission, GBP line so no FX fee, ETFs exempt from UK stamp duty"
  },
  "modelled_round_trip_bps": 14.0,
  "gbpusd_for_account_sizes": 1.3,
  "dividend_yield_accrual": {
    "SPY": 0.019,
    "QQQ": 0.01
  },
  "train_years": 5
}
```
