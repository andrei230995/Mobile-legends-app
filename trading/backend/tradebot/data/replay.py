"""Historical replay market data for demos and tests.

Quotes are synthesised from real daily OHLC bars (the S&P 500 and NASDAQ Composite
index histories bundled with the ``arch`` package, 1999-2018), scaled to ETF-like
prices. Everything produced here is labelled ``replay:...`` and ``is_replay=True`` so
the UI and reports can never present it as live market data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd

from ..clock import Clock, MarketCalendar
from ..models import Bar, D, Quote
from .base import DataUnavailable, MarketData

# symbol -> (arch dataset, scale). Scales approximate 2018 ETF/index price ratios.
PROXIES = {"SPY": ("sp500", 0.1), "QQQ": ("nasdaq", 0.0235)}


def load_index(dataset: str) -> pd.DataFrame:
    if dataset == "sp500":
        from arch.data import sp500 as mod
    elif dataset == "nasdaq":
        from arch.data import nasdaq as mod
    else:
        raise ValueError(dataset)
    df = mod.load()[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df.index).date
    return df


def load_proxy(symbol: str) -> pd.DataFrame:
    dataset, scale = PROXIES[symbol]
    df = load_index(dataset)
    df[["open", "high", "low", "close"]] *= scale
    return df


class ReplayMarketData(MarketData):
    is_replay = True

    def __init__(self, clock: Clock, frames: dict[str, pd.DataFrame] | None = None,
                 spread_bps: float = 2.0, stale_symbols: set[str] | None = None):
        self.clock = clock
        self.cal = MarketCalendar()
        self.frames = frames or {s: load_proxy(s) for s in PROXIES}
        self.spread_bps = spread_bps
        self.stale_symbols = stale_symbols or set()     # test hook: simulate a frozen feed
        self.missing_symbols: set[str] = set()          # test hook: simulate no data
        self.name = "replay:" + "+".join(sorted(self.frames))

    def _frame(self, symbol: str) -> pd.DataFrame:
        if symbol in self.missing_symbols or symbol not in self.frames:
            raise DataUnavailable(f"no replay data for {symbol}")
        return self.frames[symbol]

    def _price_at(self, row: pd.Series, f: float) -> float:
        """Deterministic intraday path open -> first extreme -> second extreme -> close."""
        o, h, l, c = row.open, row.high, row.low, row.close
        pts = [o, l, h, c] if c >= o else [o, h, l, c]
        f = min(max(f, 0.0), 1.0) * 3
        i = min(int(f), 2)
        return pts[i] + (pts[i + 1] - pts[i]) * (f - i)

    def latest_quote(self, symbol: str) -> Quote:
        now = self.clock.now()
        df = self._frame(symbol)
        day = self.cal.session_date(now)
        bounds = self.cal.session_bounds(day)
        if day in df.index and bounds:
            o, c = bounds
            if now < o:
                prior = df.loc[df.index < day]
                px, ts = float(prior.iloc[-1].close), o - timedelta(hours=1)
            else:
                f = (now - o).total_seconds() / (c - o).total_seconds()
                px, ts = self._price_at(df.loc[day], f), min(now, c)
        else:
            prior = df.loc[df.index <= day]
            if prior.empty:
                raise DataUnavailable(f"{symbol}: replay has no data before {day}")
            px, ts = float(prior.iloc[-1].close), now - timedelta(hours=2)
        if symbol in self.stale_symbols:
            ts = now - timedelta(minutes=30)
        half = px * self.spread_bps / 20000
        return Quote(symbol=symbol, bid=D(round(px - half, 4)), ask=D(round(px + half, 4)),
                     ts=ts, received_at=now, source=self.name, feed="replay", delayed=False,
                     bid_size=Decimal(100), ask_size=Decimal(100))

    def daily_bars(self, symbol: str, n: int, now: datetime) -> list[Bar]:
        df = self._frame(symbol)
        day = self.cal.session_date(now)
        bounds = self.cal.session_bounds(day)
        include_today = bool(bounds and now >= bounds[1])
        sel = df.loc[df.index <= day] if include_today else df.loc[df.index < day]
        sel = sel.tail(n)
        return [Bar(symbol, d, float(r.open), float(r.high), float(r.low), float(r.close),
                    float(r.volume), self.name) for d, r in sel.iterrows()]


def replay_start(day: str = "2018-01-02", hh: int = 14, mm: int = 0) -> datetime:
    return datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00").replace(tzinfo=timezone.utc)
