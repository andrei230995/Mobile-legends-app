"""Configuration.

Two kinds of configuration exist and are deliberately kept apart:

* ``Settings`` - deployment configuration and secrets, read once from the environment
  (or from ``<NAME>_FILE`` paths, e.g. Docker secrets). Secrets are held as
  ``SecretStr`` so they never appear in reprs, logs or API responses.
* ``RiskLimits`` - the user's risk preferences. Stored versioned in the database and
  changed only through the authenticated UI. The engine never changes them.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


def _env(name: str, default: str | None = None) -> str | None:
    """Read NAME, or the contents of the file at NAME_FILE (Docker/systemd secrets)."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        return Path(file_path).read_text().strip()
    return os.environ.get(name, default)


def _secret(name: str) -> SecretStr | None:
    value = _env(name)
    return SecretStr(value) if value else None


@dataclass(frozen=True)
class BrokerCredentials:
    key_id: SecretStr
    secret: SecretStr


# Trading 212 UK clients cannot buy US-domiciled ETFs (no PRIIPs/KID), so signals computed on
# SPY/QQQ are executed in London-listed UCITS equivalents quoted in GBP. ISINs to verify
# against Trading 212's instrument list (the adapter resolves the ticker by ISIN at runtime).
DEFAULT_T212_EXECUTION: dict[str, dict[str, str]] = {
    "SPY": {"isin": "IE00B3XXRP09", "label": "Vanguard S&P 500 UCITS ETF (VUSA)", "reference_symbol": "VUSA.L"},
    "QQQ": {"isin": "IE0032077012", "label": "Invesco EQQQ Nasdaq-100 UCITS ETF (EQQQ)", "reference_symbol": "EQQQ.L"},
}


class Settings(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    data_dir: Path = Path("./var")
    broker: str = "sim"                     # sim | alpaca | trading212
    market_data: str = "auto"               # auto | alpaca | replay
    replay_dataset: str = "sp500"           # for market_data=replay (sandbox/demo only)

    alpaca_paper: BrokerCredentials | None = None
    alpaca_live: BrokerCredentials | None = None
    alpaca_data_feed: str = "iex"           # iex is free; sip needs a paid subscription
    t212_demo: BrokerCredentials | None = None
    t212_live: BrokerCredentials | None = None

    finnhub_api_key: SecretStr | None = None      # delayed London prices for sizing (Trading 212)
    execution_map: dict[str, dict[str, str]] | None = None   # signal symbol -> execution instrument
    anthropic_api_key: SecretStr | None = None
    ai_model: str = "claude-opus-5"
    ai_daily_budget_usd: float = 0.50
    ai_max_calls_per_day: int = 60

    ntfy_url: str | None = None             # e.g. https://ntfy.sh/<long-random-topic>
    ntfy_token: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    healthcheck_ping_url: SecretStr | None = None   # e.g. https://hc-ping.com/<uuid>

    admin_password_hash: SecretStr | None = None    # argon2 hash, never the password
    totp_secret: SecretStr | None = None
    session_ttl_hours: int = 12
    cookie_secure: bool = True
    allowed_origin: str | None = None       # e.g. https://bot.example.com

    infra_cost_gbp_month: float = 4.50      # server; attributed daily in reports
    data_cost_gbp_month: float = 0.0        # paid market-data subscriptions
    tick_seconds: int = 20
    sim_starting_cash_usd: float = 12.50    # simulator only (≈ £10)

    @classmethod
    def from_env(cls) -> "Settings":
        def creds(prefix: str) -> BrokerCredentials | None:
            k, s = _secret(f"{prefix}_KEY_ID"), _secret(f"{prefix}_SECRET")
            return BrokerCredentials(k, s) if k and s else None

        return cls(
            data_dir=Path(_env("TRADEBOT_DATA_DIR", "./var")),
            broker=_env("TRADEBOT_BROKER", "sim"),
            market_data=_env("TRADEBOT_MARKET_DATA", "auto"),
            replay_dataset=_env("TRADEBOT_REPLAY_DATASET", "sp500"),
            alpaca_paper=creds("ALPACA_PAPER"),
            alpaca_live=creds("ALPACA_LIVE"),
            alpaca_data_feed=_env("ALPACA_DATA_FEED", "iex"),
            finnhub_api_key=_secret("FINNHUB_API_KEY"),
            execution_map=json.loads(_env("TRADEBOT_EXECUTION_MAP")) if _env("TRADEBOT_EXECUTION_MAP") else None,
            t212_demo=creds("T212_DEMO"),
            t212_live=creds("T212_LIVE"),
            anthropic_api_key=_secret("ANTHROPIC_API_KEY"),
            ai_model=_env("TRADEBOT_AI_MODEL", "claude-opus-5"),
            ai_daily_budget_usd=float(_env("TRADEBOT_AI_DAILY_BUDGET_USD", "0.50")),
            ai_max_calls_per_day=int(_env("TRADEBOT_AI_MAX_CALLS_PER_DAY", "60")),
            ntfy_url=_env("NTFY_URL"),
            ntfy_token=_secret("NTFY_TOKEN"),
            telegram_bot_token=_secret("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
            healthcheck_ping_url=_secret("HEALTHCHECK_PING_URL"),
            admin_password_hash=_secret("TRADEBOT_ADMIN_PASSWORD_HASH"),
            totp_secret=_secret("TRADEBOT_TOTP_SECRET"),
            session_ttl_hours=int(_env("TRADEBOT_SESSION_TTL_HOURS", "12")),
            cookie_secure=_env("TRADEBOT_COOKIE_SECURE", "true").lower() != "false",
            allowed_origin=_env("TRADEBOT_ALLOWED_ORIGIN"),
            infra_cost_gbp_month=float(_env("TRADEBOT_INFRA_COST_GBP_MONTH", "4.50")),
            data_cost_gbp_month=float(_env("TRADEBOT_DATA_COST_GBP_MONTH", "0")),
            tick_seconds=int(_env("TRADEBOT_TICK_SECONDS", "20")),
            sim_starting_cash_usd=float(_env("TRADEBOT_SIM_STARTING_CASH_USD", "12.50")),
        )

    def live_credentials_present(self) -> bool:
        return {"alpaca": self.alpaca_live, "trading212": self.t212_live}.get(self.broker) is not None

    def paper_credentials_present(self) -> bool:
        return {"alpaca": self.alpaca_paper, "trading212": self.t212_demo}.get(self.broker) is not None

    def effective_execution_map(self) -> dict[str, dict[str, str]]:
        if self.execution_map is not None:
            return self.execution_map
        return DEFAULT_T212_EXECUTION if self.broker == "trading212" else {}

    @property
    def validation_profile(self) -> str:
        return "trading212_ucits" if self.broker == "trading212" else "alpaca"

    def missing_requirements(self) -> list[str]:
        """Human-readable list of what is not configured, for the UI and docs."""
        missing = []
        if not self.admin_password_hash:
            missing.append("TRADEBOT_ADMIN_PASSWORD_HASH (UI login disabled until set)")
        if self.broker == "sim":
            missing.append("Broker credentials: running the built-in simulator (TRADEBOT_BROKER=sim)")
        elif not self.paper_credentials_present():
            missing.append(f"{self.broker} paper/demo API credentials")
        if self.broker != "sim" and not self.live_credentials_present():
            missing.append(f"{self.broker} live API credentials (live mode unavailable)")
        if not (self.ntfy_url or self.telegram_bot_token):
            missing.append("Phone notifications (NTFY_URL or TELEGRAM_BOT_TOKEN)")
        if not self.healthcheck_ping_url:
            missing.append("HEALTHCHECK_PING_URL (external dead-man's switch)")
        if self.broker == "trading212":
            if not (self.alpaca_paper or self.alpaca_live):
                missing.append("Alpaca data-only keys (free paper account, no funding): real-time US index "
                               "prices for signals and stops - Trading 212 has no price feed")
            if not self.finnhub_api_key:
                missing.append("FINNHUB_API_KEY (delayed London ETF price for order sizing; without it, "
                               "first entries are refused until a position price is known)")
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY (optional; AI news assessment is off)")
        return missing


# Hard ceilings enforced in code. The UI cannot configure beyond these.
HARD_MAX_EXPOSURE_PCT = 100.0     # never more than account equity: no leverage
HARD_MAX_RISK_PER_TRADE_PCT = 35.0   # raised from 5 at the user's explicit request (24 Sep 2026)
HARD_MAX_DAILY_LOSS_PCT = 20.0
HARD_MAX_DRAWDOWN_PCT = 50.0


class RiskLimits(BaseModel):
    """User risk preferences. Percentages are of current account equity in GBP."""

    max_position_pct: float = Field(50.0, gt=0, le=100)
    max_positions: int = Field(2, ge=1, le=20)
    max_total_exposure_pct: float = Field(100.0, gt=0, le=HARD_MAX_EXPOSURE_PCT)
    risk_per_trade_pct: float = Field(35.0, gt=0, le=HARD_MAX_RISK_PER_TRADE_PCT)   # user choice; was 1.0
    daily_loss_limit_pct: float = Field(3.0, gt=0, le=HARD_MAX_DAILY_LOSS_PCT)
    max_drawdown_pct: float = Field(15.0, gt=0, le=HARD_MAX_DRAWDOWN_PCT)
    on_drawdown_breach: str = Field("pause_entries")     # pause_entries | flatten
    max_entries_per_day: int = Field(2, ge=0, le=50)
    max_orders_per_day: int = Field(12, ge=1, le=200)
    max_daily_turnover_pct: float = Field(250.0, gt=0, le=2000)
    max_spread_bps: float = Field(15.0, gt=0, le=500)
    max_slippage_bps: float = Field(25.0, gt=0, le=500)
    max_quote_age_seconds: float = Field(30.0, gt=0, le=3600)
    max_bar_age_days: int = Field(4, ge=1, le=10)
    max_cost_to_edge_ratio: float = Field(0.5, gt=0, le=1)  # expected costs must be < this x edge
    cash_buffer_pct: float = Field(2.0, ge=0, le=50)        # never deploy the last x% of cash
    entry_window_minutes_after_open: tuple[int, int] = (5, 60)
    flat_by_close: bool = False            # True = close everything before the session ends
    flat_minutes_before_close: int = Field(15, ge=5, le=120)
    report_time_local: str = "21:30"       # Europe/London
    report_timezone: str = "Europe/London"
    symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"])

    @field_validator("on_drawdown_breach")
    @classmethod
    def _breach(cls, v: str) -> str:
        if v not in ("pause_entries", "flatten"):
            raise ValueError("on_drawdown_breach must be pause_entries or flatten")
        return v

    @field_validator("report_time_local")
    @classmethod
    def _time(cls, v: str) -> str:
        hh, mm = v.split(":")
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError("report_time_local must be HH:MM")
        return f"{int(hh):02d}:{int(mm):02d}"

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        out = [s.strip().upper() for s in v if s.strip()]
        if not out or any(not s.replace(".", "").isalnum() or len(s) > 12 for s in out):
            raise ValueError("symbols must be 1-12 character tickers")
        return out

    @model_validator(mode="after")
    def _consistent(self) -> "RiskLimits":
        if self.max_position_pct > self.max_total_exposure_pct:
            raise ValueError("max_position_pct cannot exceed max_total_exposure_pct")
        lo, hi = self.entry_window_minutes_after_open
        if not (0 <= lo < hi <= 390):
            raise ValueError("entry window must satisfy 0 <= start < end <= 390")
        return self

    def loosened_fields(self, other: "RiskLimits") -> list[str]:
        """Fields where ``other`` takes more risk than self (used to demand re-authentication)."""
        more_is_riskier = ["max_position_pct", "max_positions", "max_total_exposure_pct",
                           "risk_per_trade_pct", "daily_loss_limit_pct", "max_drawdown_pct",
                           "max_entries_per_day", "max_orders_per_day", "max_daily_turnover_pct",
                           "max_spread_bps", "max_slippage_bps", "max_quote_age_seconds",
                           "max_bar_age_days", "max_cost_to_edge_ratio"]
        out = [f for f in more_is_riskier if getattr(other, f) > getattr(self, f)]
        if other.cash_buffer_pct < self.cash_buffer_pct:
            out.append("cash_buffer_pct")
        if set(other.symbols) - set(self.symbols):
            out.append("symbols")
        return out

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
