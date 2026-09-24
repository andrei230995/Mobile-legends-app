# Tradebot: autonomous, risk-limited trading engine controlled from an iPhone

A server-side engine that looks for trades, decides whether to take them, sizes them, places and manages the orders, exits, reconciles against the broker and reports daily GBP results. You control it from an installable iPhone web app. It keeps running when your phone is locked, as long as it's deployed on a server.

> **Broker: Trading 212** (chosen). Signals on SPY/QQQ are executed in London UCITS ETFs (VUSA/EQQQ) in GBP.
>
> **Status in one line:** the engineering is complete and tested in paper mode (76 tests, replay demo, Docker). The broker adapters are implemented but **not yet verified against real broker servers**. **Live trading is blocked** because no strategy passes the evidence gate. Nothing is deployed continuously yet: that needs your server and API keys. See [capability status](docs/07-capability-status.md).

## Documents
1. [Broker research and decision](docs/01-broker-research.md): Trading 212 chosen (FCA/FSCS, GBP, ISA-capable); what it implies; the Alpaca comparison
2. [£10 feasibility and operating costs](docs/02-feasibility-and-costs.md): possible but uneconomic; roughly £1–2k to break even on a £4/month server
3. [Architecture and strategy specification](docs/03-architecture-and-strategy.md)
4. [Risk controls and failure policy](docs/04-risk-and-failure-policy.md)
5. [Secure setup and deployment (iPhone only)](docs/05-setup-and-deployment.md)
6. [Test results](docs/06-test-results.md) and strategy validation results for [Trading 212](docs/validation/trading212_ucits/RESULTS.md) and [Alpaca](docs/validation/alpaca/RESULTS.md) costs
7. [Capability status: completed / simulated / unverified / blocked](docs/07-capability-status.md)

## Quick local demo (no keys needed)
```bash
cd trading/backend
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                                # 76 tests
export TRADEBOT_ADMIN_PASSWORD_HASH="$(.venv/bin/python -c 'from tradebot.auth import hash_password as h; print(h(input()))')"
TRADEBOT_DATA_DIR=var/demo .venv/bin/python -m tradebot.cli demo --days 25   # replayed Feb 2018, simulated broker
TRADEBOT_DATA_DIR=var/demo TRADEBOT_MARKET_DATA=replay TRADEBOT_COOKIE_SECURE=false \
  .venv/bin/uvicorn tradebot.api:app --port 8000                # open http://localhost:8000
```
The demo is labelled in the app as replayed history on a simulated clock. It is not live market data.

## Layout
```
trading/
  backend/tradebot/   engine, brokers (alpaca, trading212, sim), data, strategies, risk, execution,
                      accounting, AI news assessor, API, web app (web/), backtest + validation
  backend/tests/      failure-mode, accounting, adapter, API-security, AI and backtest tests
  deploy/             docker-compose, Caddy, cloud-init, configure.sh (secrets entry), backups
  docs/               research, specs, validation results, status
```
Dependencies are pinned in `backend/requirements.txt` (Python 3.11: FastAPI 0.141.1, uvicorn 0.53.0, httpx 0.28.1, pydantic 2.13.5, pandas 3.0.6, exchange_calendars 4.13.2, anthropic 1.8.0, arch 8.0.0, …).

**Risk warning:** trading can lose money. Stop orders don't guarantee a maximum loss. Past or simulated results don't predict future results. This software is not financial advice.
