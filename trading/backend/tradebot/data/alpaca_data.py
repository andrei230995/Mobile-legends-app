"""Alpaca Market Data API v2 (stocks) and News API v1beta1.

The free "Basic" plan provides real-time quotes from the IEX exchange only (a small
share of US volume, so IEX bid/ask can be wider than the consolidated NBBO) and
historical bars. ``feed`` is recorded on every quote so the UI shows exactly which
feed a decision used. News is provided by Benzinga through Alpaca under Alpaca's
licence; each item carries its publication timestamp.

Unit-tested against mocked responses only (see brokers/alpaca.py for the reason).
"""
from __future__ import annotations

import re

from datetime import datetime, timedelta, timezone
import httpx

from ..clock import MarketCalendar
from ..config import BrokerCredentials
from ..models import Bar, D, NewsItem, Quote
from .base import DataUnavailable, MarketData

DATA_URL = "https://data.alpaca.markets"


def _ts(s: str) -> datetime:
    """Parse RFC3339 with up to nanosecond precision (Python accepts microseconds)."""
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.*?T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", s)
    if m and m.group(2):
        s = m.group(1) + m.group(2)[:7] + m.group(3)
    return datetime.fromisoformat(s)


class AlpacaMarketData(MarketData):
    def __init__(self, creds: BrokerCredentials, feed: str = "iex", timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None, clock=None):
        self.feed = feed
        self.name = f"alpaca:{feed}"
        self.cal = MarketCalendar()
        self.clock = clock
        self._http = httpx.Client(
            base_url=DATA_URL, timeout=timeout, transport=transport,
            headers={"APCA-API-KEY-ID": creds.key_id.get_secret_value(),
                     "APCA-API-SECRET-KEY": creds.secret.get_secret_value()})

    def _get(self, path: str, **params) -> dict:
        try:
            r = self._http.get(path, params=params)
        except httpx.HTTPError as e:
            raise DataUnavailable(f"{path}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise DataUnavailable(f"{path}: HTTP {r.status_code}")
        return r.json()

    def _now(self) -> datetime:
        return self.clock.now() if self.clock else datetime.now(timezone.utc)

    def latest_quote(self, symbol: str) -> Quote:
        q = self._get(f"/v2/stocks/{symbol}/quotes/latest", feed=self.feed).get("quote")
        if not q:
            raise DataUnavailable(f"no quote for {symbol}")
        return Quote(symbol=symbol, bid=D(q["bp"]), ask=D(q["ap"]), ts=_ts(q["t"]),
                     received_at=self._now(), source="alpaca", feed=self.feed,
                     delayed=self.feed == "delayed_sip",
                     bid_size=D(q.get("bs", 0)), ask_size=D(q.get("as", 0)))

    def daily_bars(self, symbol: str, n: int, now: datetime) -> list[Bar]:
        start = (now - timedelta(days=int(n * 1.6) + 10)).date()
        params = {"timeframe": "1Day", "start": start.isoformat(), "adjustment": "all",
                  "feed": self.feed, "limit": 10000}
        bars, token = [], None
        while True:
            if token:
                params["page_token"] = token
            data = self._get(f"/v2/stocks/{symbol}/bars", **params)
            bars.extend(data.get("bars") or [])
            token = data.get("next_page_token")
            if not token:
                break
        today = self.cal.session_date(now)
        bounds = self.cal.session_bounds(today)
        include_today = bool(bounds and now >= bounds[1])
        out = []
        for b in bars:
            d = _ts(b["t"]).astimezone(timezone.utc)
            day = self.cal.session_date(d)   # daily bars are stamped at New York midnight
            if day > today or (day == today and not include_today):
                continue       # the current session's bar is incomplete: never use it
            out.append(Bar(symbol, day, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]),
                           float(b["v"]), self.name))
        return out[-n:]

    def news(self, symbols: list[str], since: datetime) -> list[NewsItem]:
        data = self._get("/v1beta1/news", symbols=",".join(symbols), start=since.isoformat(),
                         limit=50, sort="asc", include_content="false")
        return [NewsItem(news_id=str(n["id"]), headline=n.get("headline", "")[:500],
                         summary=(n.get("summary") or "")[:2000], source=n.get("source", "benzinga"),
                         symbols=tuple(n.get("symbols") or ()), created_at=_ts(n["created_at"]),
                         updated_at=_ts(n.get("updated_at") or n["created_at"]), url=n.get("url", ""))
                for n in data.get("news", [])]
