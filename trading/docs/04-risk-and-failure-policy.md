# Risk controls and failure policy

## Configurable limits (Risk tab; versioned in `control.db`)

| Limit | Default | Hard ceiling in code |
|---|---|---|
| Max position (% equity) | 50 | 100 |
| Max positions | 2 | 20 |
| Max total exposure (% equity) | 100 | **100: no leverage, ever** |
| Planned risk per trade (% equity, loss at stop + 1% gap allowance) | 1 | 5 |
| Daily loss (% NAV, **includes open positions**) | 3 | 20 |
| Max drawdown from NAV peak | 15 | 50 |
| Entries / orders per day | 2 / 12 | 50 / 200 |
| Daily turnover (% equity) | 250 | 2000 |
| Max spread / slippage tolerance (bps) | 15 / 25 | 500 |
| Max quote age (s) / daily-bar staleness | 30 / previous session | – |
| Expected cost ÷ validated edge | 0.5 | 1.0 |
| Cash buffer | 2% | – |
| Flat by close | off | – |
| Daily report time | 21:30 Europe/London | – |

* **Tightening** a limit needs only a logged-in session. **Loosening** one, or confirming the limits before going live, needs your password (and TOTP if configured). Every change is stored as a new version in the audit trail.
* The engine **never** changes limits itself. There is no martingale or averaging down (one position per symbol, no adding), no shorting (simulator refuses; Alpaca `no_shorting=true`), no margin (Alpaca `max_margin_multiplier=1`, and sizing uses non-marginable buying power).

## What happens when…

| Event | New entries | Pending **entry** orders | Open positions / protective orders | You are told |
|---|---|---|---|---|
| You press **Pause entries** or **Stop** | blocked | left as they are (use *Cancel pending entries*) | **still managed**: stops, exits, time exits continue | UI state |
| **Daily loss limit** hit (P&L incl. unrealised ≤ −limit) | blocked until the next reporting day | **cancelled** | kept, still managed | push (once) |
| **Max drawdown** hit | halted. *Start* clears the halt only if drawdown is back inside the limit, or after you raise the limit (password required) | **cancelled** | `pause_entries` (default): kept and managed. `flatten`: all closed at market | push (once) |
| **Stale/missing quote** for a symbol | blocked for that symbol (retried within the entry window) | unchanged | broker stop keeps working. A **software** stop can't be monitored: you're alerted, and if prices don't return within 15 min in market hours the position is **closed at market** (the broker executes without our quote) | push |
| **Stale daily bars** | that strategy/symbol skipped for the session | – | – | decision log |
| **Broker unreachable** (timeouts, 5xx) | blocked | can't be changed | **broker-held stops remain active at the broker**; software exits can't run until the connection returns | push after 3 consecutive failures; Healthchecks alert if the engine stops |
| **Credentials rejected** (401/403) | halted | can't be changed | broker-held stops remain | critical push |
| **Rate limited (429)** | order marked `not_sent`, retried later | – | – | audit log |
| **Uncertain submit** (timeout/5xx) | no resubmission until the broker lookup confirms the order doesn't exist | reconciled every tick | – | audit log |
| **Position mismatch** (broker vs bot ledger) | blocked until they agree | unchanged | exits still allowed | push (once) |
| **Engine exception** | the tick is skipped; the next tick runs | unchanged | broker-held stops remain | push + Healthchecks `/fail` |
| **Server down** | none (nothing runs) | stay at the broker (DAY orders expire at the close) | broker-held stops only (Alpaca fractional stops are DAY: **none overnight**) | Healthchecks.io alerts when pings stop |
| **Restart** | resumes from the database | reconciled by client id | protective stops re-placed if missing | audit log |
| **Trading 212: stop hit while London is closed** (16:30–21:00 UK) | – | – | exit queued; sold at market shortly after the next London open (gap risk overnight) | decision log |
| **Trading 212: no reference price** for sizing | blocked for that symbol | – | – | decision log |
| **Fill much worse than reference** (> slippage limit + 50 bps allowance for a delayed reference) | paused until you resume | – | kept, managed | push |
| **Corporate action (split)** | blocked by the reconciliation mismatch | – | the broker position is correct; the local ledger is not | push. **Manual step: close and reopen the position, or wait for a fix. Automatic corporate-action adjustment is not implemented** |
| **Leaving live mode** with open live positions/orders | – | – | **refused**, so management isn't silently switched off. Use *Stop* (exits continue) or close positions first | API error |

## Emergency controls
* **Pause entries / Resume entries**
* **Stop new trades** (the trading switch)
* **Cancel pending entries**: cancels working entry orders only; stops stay
* **Close [symbol]**: cancels that symbol's orders (with confirmation), then sells the broker-reported quantity at market. If the market is closed, it is queued for the next open, and the UI says so.
* **Close all positions**: the same for every position, including ones opened outside the bot

Market sells can execute far from the last price in fast markets or at the open. Closing is only possible while the broker accepts orders.
