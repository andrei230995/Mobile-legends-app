# £10 feasibility and operating-cost estimate

## Technically possible vs economically practical

**A £10 trade is technically possible** at Alpaca: fractional orders from $1 notional, with no account minimum. The bot sizes to the real balance, and a unit test shows a £10 account placing a correctly sized, valid order when your limits allow it (`test_ten_pound_account_can_trade_when_limits_allow`).

**A £10 *trading system* is not economically practical.** Four costs don't shrink with the account:

| Fixed or semi-fixed cost | Amount | As % of a £10 account |
|---|---|---|
| Server (Hetzner CX22, 2 vCPU/4 GB, from 1 Apr 2026: €4.49/month ≈ £3.85) [1] | ≈ £46/year | **≈ 460% per year** |
| Alpaca funding FX via Currencycloud, 1.5% in and 1.5% out [2] | £0.15 + £0.15 | 3% round trip |
| Minimum US regulatory fee on each sale (SEC + TAF, each rounded up to $0.01) | $0.02 per exit | 16 bps on a $12.50 position, 32 bps on $6.25 |
| AI news assessment (optional; defaults to Claude Opus 5, $5 / $25 per M tokens) | ≈ $0.01–0.02 per article, capped by `TRADEBOT_AI_DAILY_BUDGET_USD` (default $0.50/day) | up to ~£11/month if the cap is reached |

What the validation shows at £10, out of sample 2005–2018, same parameter path ([results](validation/RESULTS.md)):

| Strategy (SPY proxy) | Avg net trade at £1,000 | Avg net trade at £10 | CAGR £1,000 → £10 |
|---|---|---|---|
| RSI(2) mean reversion | 21.7 bps | **6.7 bps** | 2.15% → **0.73%** |
| 200-day trend | 46.7 bps | 31.3 bps | 3.94% → 2.88% |

At £10, the two SPY strategies *historically* earned about **£0.07–0.29 a year before operating costs**, against about **£46 a year** of server cost. The expected net result is clearly negative. With default limits, the risk engine therefore **refuses** most £10 entries (`costs_small_vs_edge`) and records why. Tested in `test_ten_pound_account_sizes_or_explains`.

## Practical funding estimate (explicit assumptions)

Assumptions:
* Expected net strategy return *r* before operating costs = 2–4% a year. This is roughly the validated out-of-sample range, which may not persist.
* Operating costs *C* = £46/year (server only, AI off, free IEX data).
* "Practical" means operating costs take at most a fraction *k* of expected gross profit: balance ≥ C / (k · r).

| Target | r = 2% | r = 4% |
|---|---|---|
| Break-even (k = 100%) | £2,300 | £1,150 |
| Costs ≤ 50% of profit | £4,600 | £2,300 |
| Costs ≤ 10% of profit | £23,000 | £11,500 |

Adding real-time SIP data ($99/month ≈ £76) raises every figure about 20-fold. The budget has **not** been silently increased and **no leverage** is used: the system runs at whatever balance you fund. Below roughly £1,000–£2,000 it will mostly (and correctly) stay in cash. Ways to cut *C*:
* a free-tier VM such as Oracle Cloud Always Free (≈ £0; verify availability)
* turning the server off (not possible for an autonomous bot)
* leaving AI off

## Monthly operating-cost estimate

| Item | Minimal | Recommended | Notes |
|---|---|---|---|
| Server | £0 (free tier) – £3.85 | £3.85 | Hetzner CX22; the Ashburn (US) location is closest to US brokers |
| Domain | £0 (Tailscale) | £0–1 | Tailscale gives a private HTTPS name with no public exposure |
| Market data | £0 (IEX) | £0 | SIP $99/month optional |
| Notifications | £0 (ntfy / Telegram) | £0 | |
| Uptime monitor | £0 (Healthchecks.io free tier) | £0 | Alerts you if the bot stops pinging |
| AI | £0 (off) | ≤ £11 (capped) | Keep off below ~£5k; evaluate in shadow mode first |
| **Total** | **£0–4** | **≈ £4–15** | Reports attribute `TRADEBOT_INFRA_COST_GBP_MONTH` + `TRADEBOT_DATA_COST_GBP_MONTH` pro rata daily, plus actual AI spend |

[1] Hetzner CX22 pricing, 2026. https://vpsfor.dev/posts/hetzner-cx22-pricing-2026/ (verify at hetzner.com)
[2] Alpaca international funding. https://alpaca.markets/support/international-use-fund-account
