# Architecture and strategy specification

## Why a server plus an iPhone web app

* The engine must keep running when your phone is locked or the app is closed. iOS suspends background apps, so the trading engine runs on a **server** and the phone is only a remote control.
* A native iOS app needs a Mac or a paid build service plus an Apple Developer subscription (£79/year). An **installable web app (PWA)** needs neither: open the HTTPS address in Safari, then *Share → Add to Home Screen*. It opens full-screen like an app. Push notifications go through **ntfy** or **Telegram** (free iPhone apps), which avoids the Apple web-push setup and still works when the web app is closed.

```
 iPhone (Safari PWA) ──HTTPS──► Caddy (TLS) or Tailscale ──► FastAPI (auth, CSRF, controls)
                                                                   │
                                           background engine thread (tick every ~20 s)
     ┌───────────────────────────┬───────────────┬─────────────────┼──────────────────┐
  MarketData (Alpaca IEX)   Strategies      RiskEngine      ExecutionEngine      Accounting
  bars/quotes/news with     (same code as   (deterministic,  (durable, idempotent  (GBP ledger,
  provenance & freshness    the backtest)   every order)     order lifecycle)      NAV, reports)
                                                                   │
                                                  Broker adapter (Alpaca | Trading 212 | Sim)
  SQLite (WAL): control.db (mode, limits, authorisation) · paper.db · live.db (orders, fills, audit)
  ntfy/Telegram notifications · Healthchecks.io dead-man's switch
```

| Module | Responsibility |
|---|---|
| `brokers/base.py` | Broker interface; errors classified by what is safe next (timeout = unknown outcome) |
| `brokers/alpaca.py`, `brokers/trading212.py`, `brokers/sim.py` | Replaceable adapters |
| `data/alpaca_data.py`, `data/replay.py`, `data/fx.py` | Quotes, completed daily bars, news, ECB FX; every datum labelled with source/feed/timestamps |
| `strategies/__init__.py` | Rules on completed bars only; used unchanged by backtests and the live engine |
| `risk.py` | ~25 pre-trade checks and sizing; AI can only add a veto |
| `execution.py` | Write-ahead order rows, deterministic client ids, reconciliation of unknown outcomes, fill recording |
| `engine.py` | The tick loop: reconcile → mark → limits → manage exits → entries → AI → reports |
| `accounting.py` | FIFO lots in GBP, unrealised P&L, cash flows, NAV units, daily report |
| `api.py`, `auth.py`, `web/` | Authenticated control API and the PWA |

## Signal symbol vs execution instrument (Trading 212)

| | Signal / stop monitoring | Order placed in | Hours orders can execute |
|---|---|---|---|
| Alpaca / simulator | SPY, QQQ (real-time IEX) | the same ETF | NYSE 14:30–21:00 UK |
| **Trading 212** | SPY, QQQ (real-time IEX via a free Alpaca data account) | **VUSA / EQQQ** (London, GBP) | London 08:00–16:30 UK |

* Entries run only while **both** markets are open (about 14:35–15:30 UK), matching the validated "next US open" execution.
* Strategy exits also wait for that overlap. Stop, manual and emergency exits run as soon as London is open.
* Order quantity = size in GBP ÷ (reference London price × 1.01). The reference is Trading 212's price for a held position, else a delayed Finnhub quote no older than 20 minutes. Otherwise the entry is refused.
* The stop level is expressed in the signal symbol (e.g. SPY dollars), so it tracks the index move and ignores GBP/USD moves.

## Order lifecycle (implemented)

1. Fresh data is verified: quote age ≤ `max_quote_age_seconds`, the last daily bar equals the previous session, and the feed is neither delayed nor replayed in live.
2. Checks: cash (non-marginable), account not blocked, market session and entry window, instrument tradable/fractionable.
3. Size = min(position cap, exposure room ≤ 100%, risk-per-trade ÷ (stop + 1% gap allowance + spread), cash × (1 − buffer), turnover room), rounded **down** to the broker increment. If that is below the broker minimum, the order is **impossible** and the reason says which limit bound it.
4. Risk checks (listed below). Expected round-trip cost must be ≤ `max_cost_to_edge_ratio` × the strategy's validated gross edge.
5. Submit: the row is written as `pending_submit` *before* sending, with id `tb-<strategy>-<symbol>-<purpose>-<session>`.
6–7. The broker response is mapped. Partial fills are recorded incrementally, keyed by (id, cumulative qty). Entries not fully filled within 10 minutes have the remainder cancelled.
8. Every tick reconciles broker positions against the local FIFO ledger. A mismatch blocks entries and alerts you.
9. Exits: broker-held stop where the broker supports it (sim/Alpaca), otherwise a software stop. Strategy exits are evaluated after the close and executed after the next open; there are also manual/emergency closes and flat-by-close. Before any exit, other orders for the symbol are cancelled **and confirmed cancelled** so a stop and a sell can't both fill.
10. Outcomes go to `decisions`, `orders`, `fills`, `events` and the daily report.

**Unknown outcomes:** a timeout or 5xx on submit marks the order `unknown`. It is looked up by client id; after a grace period (30 s, or 120 s for Trading 212 without client ids) with no broker record it becomes `not_sent`, and only then may the same id be sent again. This is covered by tests for a timeout after acceptance, a timeout before acceptance, a crash between write and send, and a restart with an unknown order.

**Broker-held vs software exits:** a broker stop works while the server is down, but it is a *stop-market* order. It triggers at the stop and fills at the next available price, which after a gap can be far below the stop (the backtest and simulator both model this). **A stop does not guarantee a maximum loss.** Alpaca fractional stops are DAY orders, so between sessions (overnight and weekends) no broker stop exists, and gap risk is fully present. Software stops only work while the server and data feed are up.

**Calendars:** NYSE sessions come from `exchange_calendars` (holidays, early closes). Conversion goes through zoneinfo, so the weeks when US and UK clocks change on different dates are correct (tested). The broker's own clock is cross-checked. Corporate actions: bars are split/dividend-adjusted (`adjustment=all`), and positions are always taken from the broker. A split creates a ledger mismatch that blocks entries until you reconcile it (see [risk policy](04-risk-and-failure-policy.md)).

## Daily profit objective, precisely

* **Trading P&L (GBP)** = equity_end − equity_start − deposits + withdrawals. Deposits are never profit.
* It is decomposed into realised (FIFO, net of fees, at each fill's FX), change in unrealised (all open positions, marked to the latest price and FX), dividends/interest/cash fees, and an explicit **FX/other residual**. The displayed components always add up exactly.
* **Net after operating costs** = trading P&L − (server + data subscription, pro rata per day) − actual AI spend.
* Drawdown and daily-loss limits use **unitised NAV**, so deposits and withdrawals can't mask a loss.
* The reporting day runs from report time to report time (default 21:30 Europe/London, after the US close; configurable). **A report does not force liquidation.** Positions may stay open overnight unless you set `flat_by_close`. Strategies validated with overnight holds are then refused, because their evidence no longer applies.
* The engine seeks the best net result by trading only when validated edge exceeds expected costs. **Staying in cash is a normal outcome** and is logged as `no_signal` or `rejected` with reasons.

## Candidate strategies

| | 200-day trend (`trend_sma`) | RSI(2) mean reversion (`rsi2_mr`) | Opening-range breakout | AI news/earnings drift |
|---|---|---|---|---|
| Proposed edge | Time-series momentum: avoid long bear markets | Short-term overreaction in index ETFs inside an uptrend | Intraday continuation | Post-announcement drift / event avoidance |
| Data | Daily bars | Daily bars | Minute bars, real-time SIP | Licensed news, timestamps, history |
| Frequency | ~6 trades/yr | ~9 trades/yr, 10% exposure | Daily | Event-driven |
| Entry / exit | Close > SMA(n) / close < SMA(n); 10% stop | RSI(2) < t and close > SMA200 / close > SMA5 or time stop; 6% stop | – | – |
| Sizing | Risk-per-trade ÷ stop distance, capped by position/exposure/cash | same | – | – |
| Fails when | Choppy sideways markets (whipsaw); sudden crashes | Crashes where "oversold" keeps falling; post-publication decay | High costs, latency | Headline noise; look-ahead in historical news |
| Cost/latency sensitivity | Low | High (small edge per trade) | Very high | Medium |
| Small balance | OK at ≥ £100 | Poor below ~£100 (min fee) | Unsuitable | Unsuitable (AI + data cost) |
| Status | Implemented, validated | Implemented, validated | **Not implemented**: can't be validated without intraday data | **Shadow only**: assessments are logged, never trade |

Baselines: buy-and-hold on the same period and costs, and cash.

## Validation method (see results for [Trading 212](validation/trading212_ucits/RESULTS.md) and [Alpaca](validation/alpaca/RESULTS.md) cost profiles)

* Chronological **walk-forward**: parameters chosen on the prior 5 years only, traded on the next year; 2005–2018 are out of sample.
* No look-ahead: signal at close t, fill at open t+1 (tested). Live code refuses the current session's incomplete bar (tested).
* Realistic costs: half-spread + slippage per side, cent-rounded regulatory fees, optional 0.15% FX. Stress tests: 3× costs, one-day delay, +100 bps stop slippage, Trading 212 FX. Stresses reuse the same parameter path.
* Sample and uncertainty: trade count, bootstrap 90% CI of mean trade return, 20-day block-bootstrap CI of daily return. Also reported: drawdown, longest drawdown, exposure, turnover, losing-months share, worst month.
* **Survivorship and data bias:** the data are S&P 500 and NASDAQ Composite *index* levels bundled with the `arch` package (1999–2018), used as proxies for SPY/QQQ. Indices have survivorship built into their construction, which is realistic when trading the index ETF. The NASDAQ Composite is **not** the index QQQ tracks. Dividends are approximated (1.9%/1.0% a year accrued while invested) for strategy and baseline alike. **The data end in 2018**, so recent markets are not covered. On the server, `python -m tradebot.backtest.validate --source alpaca` re-runs everything on Alpaca daily bars (2016 onwards).
* **Disclosure:** the `sharpe_at_least_buy_and_hold` criterion was added after the first validation run. It is stricter, not looser: it turned RSI(2)/SPY from a backtest *pass* into a *fail*.

### Promotion gate (enforced in code: `backtest/validate.py`, `api.py`)
All must hold before a strategy can be approved for live, and approval also needs your password:
≥ 40 OOS trades; positive OOS net return; lower bound of the 90% CI of the mean trade > 0; OOS max drawdown ≤ 25%; Sharpe ≥ buy-and-hold; positive under 3× costs and under a one-day delay; positive at £10; validation data ending within 120 days; **forward paper trading ≥ 20 sessions and ≥ 5 closed trades**.

**Current result: no strategy passes under either cost profile, so live trading is blocked.** With Trading 212 costs, SPY trend comes closest: Sharpe 0.469 vs buy-and-hold 0.462, max drawdown 18.5%. It fails only because the 90% CI of its mean trade includes zero (−20.8…159 bps), plus the recent-data and forward-paper requirements. Paper trading continues, for strategies with positive OOS net returns only, to collect forward evidence. Paper fills are not treated as live fills: Alpaca paper ignores market impact, queue position, latency slippage, fees and dividends.

## AI usage

* **Where:** assessing news for instrument-specific event risk (Alpaca/Benzinga news with publication timestamps), using Claude (`TRADEBOT_AI_MODEL`, default `claude-opus-5`) with JSON-schema-constrained output.
* **Safety:** news text is untrusted. It is escaped and delimited; the model has no tools, credentials or account data; the output is limited to enums and re-validated; refusals and malformed output are discarded. The output can **only veto** an entry, and only in `veto` mode. The default is `shadow`, which logs assessments without affecting trades. AI can never originate, size or approve an order, change limits, or trigger an exit.
* **Testable benefit:** shadow assessments are stored with the article's publication time, so a veto rule can be tested against later prices before it is switched on. The script for that evaluation is **not written yet**, so veto mode stays off: there's no evidence of benefit.
* **No AI "confidence" is shown as a probability of profit.** The only edge figure used is the validated out-of-sample average net trade return.
