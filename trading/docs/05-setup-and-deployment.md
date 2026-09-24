# Secure setup (iPhone only; no Mac needed)

You need an iPhone with Safari and, for maintenance, either the cloud provider's **web console** (it works in Safari) or an SSH app such as Termius. **Never paste API keys or passwords into chats, tickets or this repository.** Secrets are typed only into the server-side `configure.sh`, which hides the input and writes `deploy/.env` with owner-only permissions (600).

## 1. Accounts (do these in Safari)
1. **Trading 212** (your broker): in the app, switch to the **Practice** account, then *Settings → API (Beta) → Generate key*.
   * Grant only: account data, portfolio, orders (read + execute), history.
   * Restrict the key to your server's IP address.
   * The secret is shown once. Create the live key later, in your Invest or Stocks ISA account, only when live trading is allowed.
   * Check that **VUSA** and **EQQQ** are available to you.
2. **Alpaca** (data only, no money): create a free **paper** account and paper API keys. The bot uses them only for real-time US prices, because Trading 212 has no price feed.
3. **Finnhub** (free key): delayed London ETF prices for order sizing. Whether the free plan includes London quotes is **unverified**. Without it, the first entry per symbol is refused until Trading 212 reports a price.
4. **Healthchecks.io** (free): create a check with a 2-minute period and 5-minute grace, and enable email/push alerts. This alerts you when the bot **stops**, which the bot itself can't do.
5. **ntfy** app (free, App Store): subscribe to a long random topic, e.g. `tb-` + 24 random characters. Anyone who knows the topic can read the messages, so keep it secret, or use a self-hosted ntfy with a token. Telegram is the alternative (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`).
6. Optional (default off): **Anthropic API key** for AI news assessment (shadow mode, daily $ cap).
7. **Tailscale** (free, your choice) on the server and iPhone, which gives private HTTPS access with **no public ports**.

## 2. Server
1. Create an Ubuntu 24.04 VM (e.g. Hetzner CX22 in Germany, close to Trading 212 and London). In "Cloud config", paste `trading/deploy/cloud-init.yaml`. It installs Docker, a firewall, unattended security upgrades and nightly backups, and clones this repository.
2. Open the provider's web console and run:
   ```
   sudo /opt/tradebot/trading/deploy/configure.sh
   ```
   It asks for the broker, API keys (hidden), a control-panel password (stored only as an argon2 hash), an optional TOTP secret (add it to your authenticator app), a domain (or leave empty for Tailscale), the ntfy URL, the Healthchecks URL and the AI key. It then starts the containers and runs a **read-only** broker check (`check-broker`: account, clock, assets, a quote; no orders).
3. HTTPS access:
   * **Tailscale (recommended):** `curl -fsSL https://tailscale.com/install.sh | sh`, then `sudo tailscale up && sudo tailscale serve --bg 8000`, then open `https://<server>.<tailnet>.ts.net` on the iPhone (with Tailscale connected).
   * **Public domain:** set `TRADEBOT_DOMAIN` (configure.sh does this) and run `docker compose --profile public up -d`. Caddy obtains a Let's Encrypt certificate.
4. In Safari: open the address, then *Share → Add to Home Screen*. Sign in.

## 3. First run (paper)
* The system starts in **PAPER** mode with trading **off**. Review and **Confirm** the risk limits (Risk tab), then press **Start trading**.
* On the server, refresh the validation with current data:
  `docker compose exec tradebot python -m tradebot.backtest.validate --source alpaca --out /data/validation`
  (`--source alpaca` is the free data account; this works with Trading 212 as the broker.) Then `docker compose restart tradebot`. The engine reads `/data/validation/<profile>/results.json` if present, otherwise the bundled 1999–2018 results.
* Daily reports and alerts arrive via ntfy/Telegram. The Reports tab keeps the full breakdown.

## 4. Going live (only if the evidence supports it)
Live stays blocked until **all** of these are true (the System tab lists what's missing):
1. live API keys configured on the server
2. live (non-replay) market data
3. current risk limits confirmed
4. at least one strategy passes every promotion check, including ≥ 20 paper sessions and ≥ 5 closed paper trades, and you approve it with your password
5. you enter your password (+TOTP) and type `ENABLE LIVE TRADING`

Once authorised, qualifying trades execute automatically without per-trade confirmation. Paper and live use **separate keys, hosts and databases**.

## Security measures (implemented)
* Secrets only in `deploy/.env` (600) or `*_FILE` Docker secrets. Held as `SecretStr` (never in reprs, logs or API responses; tested), never sent to the browser, never in browser storage (the CSRF token is kept in memory only).
* **Least-privilege API keys:** Trading 212 lets you scope keys (for paper, use account + orders + portfolio only) and restrict them to the server's IP. Alpaca keys are account-wide: keep live keys unset until needed, and rotate them from the dashboard if exposed.
* Login: argon2 password hash, optional TOTP, exponential backoff on failures, HttpOnly + Secure + SameSite=Strict session cookie (server stores only a SHA-256 of the token), CSRF header on every state change, Origin check, strict Content-Security-Policy (no inline scripts), no-store caching for API responses, `X-Frame-Options: DENY`.
* Re-authentication for going live, loosening limits and approving strategies.
* The container runs as a non-root user and is published only on 127.0.0.1 (Caddy/Tailscale in front). Firewall: SSH, 80 and 443 only.
* Durable state in SQLite WAL with `synchronous=FULL`, nightly consistent backups (`deploy/backup.sh`, 14 days kept), and a timestamped append-only audit trail (`events`).

## Recovery
* **Restart:** `docker compose restart tradebot`. On start the engine reconciles unknown and pending orders by client id, re-reads positions from the broker, and re-places missing protective stops.
* **Restore:** stop the container, copy the latest `/var/backups/tradebot/*-<stamp>.db` files into the volume as `control.db` / `paper.db` / `live.db`, then start it. The broker remains the source of truth for positions; reconciliation flags any difference.
* **Rotate keys:** create new keys at the broker, re-run `configure.sh`, then revoke the old keys.
