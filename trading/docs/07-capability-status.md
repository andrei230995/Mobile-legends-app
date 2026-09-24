# Capability status (as of 24 Sep 2026)

**Engineering readiness and evidence of profitability are separate questions.** The engineering is largely complete and tested. The profitability evidence currently does **not** support live trading.

## Completed and verified in this repository
* Autonomous engine: research (bars, quotes, optional news) → decide → size → risk-check → submit → confirm/partial fills → reconcile → manage exits → record → daily report. Verified with the simulator on replayed real prices (tests and a 25-day replay).
* Idempotent, durable order handling for timeouts, rate limits, rejections, partial fills, crash/restart and duplicates (tests).
* Risk engine with configurable, versioned limits, hard no-leverage/no-short ceilings, daily-loss/drawdown enforcement using open positions, a cost-versus-edge gate, and an explanation for every rejection (tests).
* GBP accounting that separates realised, unrealised, fees, operating costs, FX and deposits/withdrawals, with NAV units (tests).
* Walk-forward validation with stress tests, account-size effects, baselines and a promotion gate; results in `docs/validation/`.
* iPhone web app (PWA) with every requested panel and control; login, TOTP, CSRF, CSP, re-authentication for risky actions (tests and a browser run at iPhone size).
* Docker image: builds, runs as non-root, healthy, and survives a restart with durable state (verified locally).

## Simulated (works, but not against real markets)
* **Paper trading here** used the built-in simulator with **replayed 2018 prices** on a simulated clock. The UI labels this "DEMO: replayed historical data".
* Simulator fills are optimistic: no market impact or queue position, and partial fills only when injected.

## Implemented but UNVERIFIED against the real services
The build environment's network policy blocked alpaca.markets, trading212.com and the ECB.
* **Alpaca Trading and Market Data adapters** (paper and live): unit-tested against mocked responses in the documented format. First real verification: `docker compose exec tradebot python -m tradebot.cli check-broker --env paper` on your server (read-only).
* **Trading 212 adapter**: written from the v0 docs as extracted by search. Field names, live order-type limits and fee behaviour need checking against the live docs and your demo account.
* **ECB FX reference rates**, **ntfy/Telegram delivery**, **Healthchecks pings**, **Anthropic news assessment**: code paths tested with fakes; not called for real.
* Alpaca account safety configuration (`no_shorting`, `max_margin_multiplier=1`) via `PATCH /v2/account/configurations`: field names unverified. The bot's own sizing never uses margin either way.
* The `configure.sh` / cloud-init flow has not been run on a real VM.

## Blocked: what you need to provide or decide
1. **A server** (≈ £4/month, or a free tier) plus Tailscale or a domain. **Nothing is running continuously today.** No deployment or health check has been demonstrated on a persistent host, so I'm not claiming the bot runs 24/7.
2. **Alpaca paper API keys** (then later live keys), entered only via `configure.sh` on the server.
3. **ntfy topic / Telegram bot** and a **Healthchecks.io** check for phone alerts.
4. Optional **Anthropic API key**.
5. **Live trading is blocked by design.** No strategy passes the promotion gate:
   * `trend_sma:SPY`: CI of mean trade includes 0; max drawdown 29% > 25%; Sharpe below buy-and-hold
   * `rsi2_mr:SPY`: Sharpe 0.446 < buy-and-hold 0.463
   * `trend_sma:QQQ`: max drawdown 28.8% > 25%
   * `rsi2_mr:QQQ`: CI includes 0; Sharpe below buy-and-hold

   In addition, all four fail because the validation data end in 2018 and there are no forward paper sessions yet.

## Not implemented
* Opening-range/intraday strategies and news-driven *entries* (they can't be validated with the data available).
* The AI veto-benefit evaluation script (assessments are stored; veto mode stays off).
* Automatic corporate-action (split) reconciliation, handled manually as described in the risk policy.
* Interactive Brokers and Saxo adapters.
* Native iOS app and Apple web push (ntfy/Telegram used instead).
* Tax reporting.

## Decisions for you
* **Live broker:** Alpaca (full automation; US-regulated, no FSCS, USD) or Trading 212 (FCA/FSCS; more limited beta API, 0.15% FX, software-only exits).
* **Risk preferences:** defaults are 1% risk per trade, 3% daily loss, 15% drawdown, 50% max position, overnight holds allowed, report at 21:30 UK.
* **Funding:** see the [feasibility estimate](02-feasibility-and-costs.md). At £10 the bot will mostly and correctly stay in cash.
