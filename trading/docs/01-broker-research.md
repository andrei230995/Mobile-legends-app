# Broker research and recommendation (UK resident, automated trading)

Researched **24 September 2026**. Brokers' own websites (alpaca.markets, docs.trading212.com, interactivebrokers.co.uk, developer.saxo) were **blocked by the network policy of the build environment**, so pages could not be opened directly. The facts below come from search-engine extracts of those official pages, each linked. Anything marked **(verify)** should be checked on the live page before funding an account. The adapters report broker capabilities at runtime, and `python -m tradebot.cli check-broker` confirms connectivity from your server.

## Individual trading APIs vs brokerage-infrastructure products

| Product | What it is | Usable by you? |
|---|---|---|
| Alpaca **Trading API** | Individual/business brokerage account with a REST/WebSocket API | **Yes.** This is what the bot uses. |
| Alpaca **Broker API** | Infrastructure for fintechs that open accounts for *their* customers | No. It's for businesses. |
| Interactive Brokers **TWS API / Client Portal (Web) API** | APIs on your own IBKR account | Yes, but retail access runs through a gateway that needs manual 2FA re-login |
| IBKR **OAuth Web API** | Token-based access without the gateway | Institutional/third-party vendors only |
| Trading 212 **Public API (beta)** | API on your own Invest / Stocks ISA account | Yes, with limitations |
| Saxo **OpenAPI** | API on your own Saxo account; live app credentials for direct retail clients | Yes (not implemented, see below) |
| eToro **Public API** | Launched late 2025 for "select users" | Unclear for UK retail (verify) |

## Comparison

| | **Alpaca** (Trading API) | **Trading 212** (Public API beta) | **Interactive Brokers UK** |
|---|---|---|---|
| Entity for a UK client | Alpaca Securities LLC (US), member FINRA/SIPC. International clients are "non-solicited" and sign a non-solicitation agreement [1][2] | Trading 212 UK Ltd, FCA-regulated [8] | Interactive Brokers (U.K.) Ltd, FCA register no. 208159; accounts are cleared/carried by Interactive Brokers LLC (US) [12][13] |
| UK eligibility | UK is selectable; UTR or NINO accepted [3] | Yes (UK entity) | Yes |
| Regulator / investor protection | **Not FCA-authorised** [4]. SIPC (US) applies, **not FSCS**; no Financial Ombudsman route | FCA; FSCS applies to eligible claims (verify limits for your account) | FCA; FSCS up to £85,000 for assets held with the UK entity, SIPC for assets carried by IBKR LLC [12][14] |
| Automated trading allowed | Yes. It's the product's purpose | Yes. The API is for automation; keys are scoped [9] | Yes |
| Official individual API | REST + WebSocket, paper and live | REST, demo and live; **beta** [6] | TWS API / Client Portal API via a local Java gateway |
| Recurring manual login | **None** (API keys) | None (API key + secret; optional IP allow-list) [9] | **Yes.** Gateway needs interactive login, and full re-authentication with 2FA is reported to be forced **weekly** [15]. That breaks unattended operation |
| Minimum deposit | None; non-US users can fund from $1 [3] | £1 minimum investment in Invest/ISA [8] | None [16] |
| Fractional | Yes; `qty` or `notional`. Fractional orders: market/limit/stop/stop-limit, **DAY only** [17] | Yes (from £1) [8] | US/EU stocks & ETFs from $1 (needs permission) [16] |
| Commission | $0 on US stocks/ETFs; **SEC fee $0.0000206×value and FINRA TAF $0.000195/share on sells, each rounded *up* to the cent** [18][19] | $0 commission; **0.15% FX fee** on trades in a non-account currency [8][10] | IBKR Pro fixed US: $0.005/share, $1 min, max 1% of value (verify) [16]. UK shares are higher (verify) |
| Funding / FX | USD account only. Local-currency funding through Currencycloud: **1.5% fee each way, capped at $40**. Wire withdrawals $25 domestic / $50 international [5] | GBP account; FX on USD trades 0.15% | Multi-currency; low FX spreads (verify) |
| Market data via API | Free "Basic": real-time **IEX only** (a slice of US volume), 15-min-delayed SIP via REST, 200 req/min. SIP real-time: **$99/month** "Algo Trader Plus" [20]. News (Benzinga) via API | **No market-data endpoint** in the public API. Quotes must come from elsewhere [6] | Real-time data needs paid exchange subscriptions (verify) |
| Order types (live) | market, limit, stop, stop-limit, trailing, bracket/OCO (whole shares) | Documented at research date as **market orders only in live**; limit/stop in demo [6] (search extracts conflicted; verify) | Full set |
| Broker-held protective stop | Yes. Fractional stops must be DAY, so they're re-placed each session and don't cover overnight gaps | Not in live (per the above) → exits are software-managed | Yes |
| Idempotency | `client_order_id` (unique; lookup endpoint) | **None.** Timeouts must be reconciled by ticker/qty/time matching | Order refs |
| Rate limits | 200 requests/min on the free data plan [20] | Per-endpoint limits per account, with `x-ratelimit-*` headers; e.g. order history 6/min [7][11] | Pacing limits |
| Paper trading | Yes, separate keys/host. Paper fills ignore market impact, queue position, slippage from latency, **regulatory fees and dividends** [21] | Yes (demo environment) | Yes |
| Day-trading rules | FINRA removed the $25k "pattern day trader" rule (SEC approval 14 Apr 2026, effective 4 Jun 2026, broker phase-in until 20 Oct 2027) [22]. The bot caps margin at 1× and doesn't depend on intraday round trips | UK account: no PDT | IBKR UK clients trading US stocks: phase-in timing varies (verify) |
| Settlement | US equities T+1 | T+1/T+2 per market | per market |

### Also considered
* **Saxo OpenAPI**: available to direct retail clients, and live app credentials are usually issued automatically [23]. Not implemented because of Saxo's minimum commissions per trade (verify) and more complex OAuth for an MVP. It would be a good third adapter.
* **IG** (REST API): its products for UK retail are spread bets and CFDs, which are **leveraged**, and your requirements rule out leverage in the first version. Excluded.
* **Freetrade, Hargreaves Lansdown, AJ Bell, Vanguard UK**: no public trading API for individuals.
* **eToro Public API**: launched to "select users" (Oct 2025) [24]. UK retail availability and order-level guarantees are unclear, so not implemented.

## Suitability at £10, £100 and £1,000

Round trip = buy then sell one position of the stated size, excluding the bid/ask spread (roughly 1–3 bps on SPY/QQQ; wider on the free IEX quote).

| Balance | Alpaca | Trading 212 (USD ETF) | IBKR Pro fixed (US ETF) |
|---|---|---|---|
| £10 (~$12.50) | Fees $0.02 on the sale ≈ **16 bps** (≈32 bps on a half-size position). Funding FX 1.5% in, 1.5% out | 0.15% × 2 = **30 bps** | 1% cap each side = **200 bps** |
| £100 | $0.02–0.03 ≈ **2 bps** | **30 bps** | $1 min each side ≈ **160 bps** |
| £1,000 | ≈ **0.4 bps** | **30 bps** | ≈ **16 bps** |

Validated edges are small: 20–100 bps per trade for the strategies tested (see [validation](validation/RESULTS.md)), so the cost column decides feasibility.

## Recommendation

**Alpaca (Trading API) is the primary broker for this MVP**, for engineering reasons you can verify:

1. The only candidate with **unattended operation** (no recurring manual login), an **idempotency key** (`client_order_id`) for safe retry after timeouts, **free real-time quotes** and news via API, broker-held stops, and a separate paper environment with its own keys.
2. The lowest per-trade costs at £100–£1,000, and the only one where a £10 trade isn't mostly eaten by fees.

**Trade-offs you must accept for Alpaca live:** a US broker, **not FCA-authorised, no FSCS, no Financial Ombudsman**, USD-only (1.5% FX each way on funding), W-8BEN (15% US withholding on dividends), UK CGT reporting is your responsibility, and the free IEX quote is not the full market.

**Trading 212 is the FCA-regulated alternative.** The adapter is implemented, but the live API (market orders only, no market data, no client order ids, beta) forces software-managed exits and needs an external data feed. Its 0.15% FX fee makes the RSI(2) strategy **lose money** in validation, while the trend strategy stays positive. **IBKR** has the strongest regulatory profile but is not recommended for an *unattended* bot, because of the weekly 2FA re-login and the $1 minimum commission.

Broker integrations are replaceable: strategy, risk, execution and accounting only use `tradebot/brokers/base.py`.

## Sources (accessed 24 Sep 2026)
1. Alpaca: Who can apply for a brokerage account. https://alpaca.markets/support/requirements-alpaca-brokerage-account
2. Alpaca: Non-US live trading (non-solicitation). https://alpaca.markets/blog/non-us-live-trading-beta/
3. Alpaca: How to open a live account as a non-US resident (updated 11 Mar 2026). https://alpaca.markets/learn/live-trading-account-non-us
4. BrokerChooser: Alpaca and UK (not FCA-authorised). https://brokerchooser.com/broker-reviews/alpaca-trading-review/alpaca-trading-uk
5. Alpaca: International funding (Currencycloud 1.5%, cap $40). https://alpaca.markets/support/international-use-fund-account
6. Trading 212 API: General information / API environments. https://docs.trading212.com/api/section/general-information
7. Trading 212 API: Rate limiting. https://docs.trading212.com/api/section/rate-limiting/how-it-works
8. Trading 212: Fees in Invest, ISAs and SIPP. https://helpcentre.trading212.com/hc/en-us/articles/11471996799517
9. Trading 212: API key (scopes, IP restriction). https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key
10. Trading 212: FX fee. https://helpcentre.trading212.com/hc/en-us/articles/360018909758
11. Trading 212 API: Historical events (history endpoint limits). https://docs.trading212.com/api/historical-events
12. FCA register: Interactive Brokers (U.K.) Limited. https://register.fca.org.uk/s/firm?id=001b000000MfKjCAAV
13. IBKR UK: Disclaimers. https://www.interactivebrokers.co.uk/en/general/disclaimers.php
14. IBKR UK: Customer asset protection disclosure. https://www.lynxbroker.com/media/doc/IBUK/IBUK_Interactive_Brokers_UK_DISCLOSURE_REGARDING_CUSTOMER_ASSET_PROTECTION_ENG.pdf
15. IBKR Campus: Web API / gateway authentication; weekly re-auth discussion. https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/ and https://github.com/nautechsystems/nautilus_trader/issues/5069
16. IBKR UK: Commissions, stocks. https://www.interactivebrokers.co.uk/en/pricing/commissions-stocks.php
17. Alpaca: Fractional trading. https://docs.alpaca.markets/us/docs/fractional-trading
18. Alpaca: Regulatory fees. https://docs.alpaca.markets/us/docs/regulatory-fees
19. Alpaca: Brokerage fee schedule. https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf
20. Alpaca: About Market Data API / plans. https://docs.alpaca.markets/us/docs/about-market-data-api
21. Alpaca: Paper trading. https://docs.alpaca.markets/us/docs/paper-trading
22. FINRA Regulatory Notice 26-10; SEC release 34-105226. https://www.finra.org/rules-guidance/notices/26-10
23. Saxo: Direct clients, live application credentials. https://www.developer.saxo/openapi/learn/direct-clients-request-for-openapi-application-credentials-for-the-live-environ
24. eToro: Public APIs launch. https://www.etoro.com/news-and-analysis/press-releases/etoro-marks-15-years-of-social-investing-with-launch-of-public-apis-and-expansion-of-copytradertm-to-the-us/
