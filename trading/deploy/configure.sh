#!/usr/bin/env bash
# Interactive, on-server configuration. Secrets are typed here (hidden input) and written
# to deploy/.env with owner-only permissions. Never paste secrets into chats or tickets.
set -euo pipefail
cd "$(dirname "$0")"
umask 077

ask()    { local v; read -r -p "$1: " v; printf '%s' "$v"; }
secret() { local v; read -r -s -p "$1 (hidden, Enter to skip): " v; echo >&2; printf '%s' "$v"; }
q()      { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }   # single-quoted, literal for compose

echo "== Tradebot configuration =="
docker compose build tradebot >/dev/null
broker=$(ask "Broker [alpaca/trading212/sim] (default alpaca)"); broker=${broker:-alpaca}

echo "Paper/demo credentials are required. Live credentials are optional and can be added later."
if [[ $broker == alpaca ]]; then
  apk=$(secret "Alpaca PAPER key id"); aps=$(secret "Alpaca PAPER secret")
  alk=$(secret "Alpaca LIVE key id");  als=$(secret "Alpaca LIVE secret")
elif [[ $broker == trading212 ]]; then
  apk=$(secret "Alpaca PAPER key id (market data only)"); aps=$(secret "Alpaca PAPER secret")
  tdk=$(secret "Trading 212 DEMO API key"); tds=$(secret "Trading 212 DEMO API secret")
  tlk=$(secret "Trading 212 LIVE API key"); tls=$(secret "Trading 212 LIVE API secret")
fi

echo "Choose the control-panel password (min 12 characters)."
hash=$(docker compose run --rm -T tradebot python -m tradebot.cli hash-password </dev/tty | tail -n1)
totp=""
if [[ $(ask "Enable one-time codes (TOTP) for login and live activation? [Y/n]") != [nN]* ]]; then
  out=$(docker compose run --rm -T tradebot python -m tradebot.cli totp-setup)
  totp=$(echo "$out" | sed -n 2p)
  echo "Add this to your authenticator app (shown once):"; echo "$out" | sed -n 3p
fi
domain=$(ask "Public domain for HTTPS via Caddy (leave empty if using Tailscale)")
ntfy=$(secret "ntfy topic URL, e.g. https://ntfy.sh/<long-random-topic>")
hc=$(secret "Healthchecks.io ping URL")
ai=$(secret "Anthropic API key (optional)")

{
  echo "TRADEBOT_BROKER=$(q "$broker")"
  echo "ALPACA_PAPER_KEY_ID=$(q "${apk:-}")"; echo "ALPACA_PAPER_SECRET=$(q "${aps:-}")"
  echo "ALPACA_LIVE_KEY_ID=$(q "${alk:-}")";  echo "ALPACA_LIVE_SECRET=$(q "${als:-}")"
  echo "T212_DEMO_KEY_ID=$(q "${tdk:-}")";    echo "T212_DEMO_SECRET=$(q "${tds:-}")"
  echo "T212_LIVE_KEY_ID=$(q "${tlk:-}")";    echo "T212_LIVE_SECRET=$(q "${tls:-}")"
  echo "TRADEBOT_ADMIN_PASSWORD_HASH=$(q "$hash")"
  echo "TRADEBOT_TOTP_SECRET=$(q "$totp")"
  echo "TRADEBOT_DOMAIN=$(q "$domain")"
  [[ -n $domain ]] && echo "TRADEBOT_ALLOWED_ORIGIN=$(q "https://$domain")"
  echo "NTFY_URL=$(q "$ntfy")"
  echo "HEALTHCHECK_PING_URL=$(q "$hc")"
  echo "ANTHROPIC_API_KEY=$(q "$ai")"
} > .env
chmod 600 .env
echo "Wrote $(pwd)/.env (mode 600)."
if [[ -n $domain ]]; then docker compose --profile public up -d; else docker compose up -d; fi
sleep 5
docker compose exec -T tradebot python -m tradebot.cli check-broker --env paper || true
echo "Done. Open https://${domain:-<your-tailscale-name>.ts.net} on your iPhone, then Share > Add to Home Screen."
