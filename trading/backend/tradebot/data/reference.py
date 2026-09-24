"""Reference prices for the *execution* instrument when it differs from the signal symbol.

With Trading 212, signals and stops use real-time US index-ETF quotes (SPY/QQQ), but orders
are placed in London-listed UCITS ETFs (e.g. VUSA/EQQQ) whose real-time quotes are not
available from Trading 212's API and are generally paid elsewhere. The reference price is
used only to size an order (quantity = value / price), so a delayed price is acceptable
with a safety margin; it is always labelled as delayed and its age is checked.

Sources, in order:
1. Trading 212's own current price for a position we hold (as reported by the broker).
2. Finnhub ``/quote`` for the London line (``FINNHUB_API_KEY``), 15-minute delayed on the
   free tier according to Finnhub's site (UNVERIFIED from this build environment).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from ..clock import Clock
from ..models import D, Quote

FINNHUB_URL = "https://finnhub.io/api/v1/quote"


class ReferencePrices:
    def __init__(self, execution_map: dict[str, dict[str, str]], clock: Clock,
                 finnhub_key: str | None = None, transport: httpx.BaseTransport | None = None):
        self.map, self.clock, self.key = execution_map, clock, finnhub_key
        self._http = httpx.Client(timeout=8.0, transport=transport)
        self._position_px: dict[str, Decimal] = {}
        self.fixed: dict[str, Decimal] = {}          # tests / replay demos

    def applies(self, symbol: str) -> bool:
        return symbol in self.map

    def label(self, symbol: str) -> str:
        return self.map.get(symbol, {}).get("label", symbol)

    def update_from_positions(self, positions) -> None:
        for p in positions:
            if p.symbol in self.map and p.market_price > 0:
                self._position_px[p.symbol] = p.market_price

    def quote(self, symbol: str) -> Quote | None:
        now = self.clock.now()
        if symbol in self.fixed:
            px = self.fixed[symbol]
            return Quote(symbol, px, px, now, now, "fixed-reference", "reference", delayed=True)
        if symbol in self._position_px:
            px = self._position_px[symbol]
            return Quote(symbol, px, px, now, now, "trading212-position", "broker", delayed=True)
        if not self.key:
            return None
        ref = self.map[symbol].get("reference_symbol")
        try:
            r = self._http.get(FINNHUB_URL, params={"symbol": ref, "token": self.key})
            r.raise_for_status()
            j = r.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not j.get("c") or not j.get("t"):
            return None
        # set "reference_scale": "0.01" in the execution map if the source quotes this line in pence
        px = D(j["c"]) * D(self.map[symbol].get("reference_scale", "1"))
        ts = datetime.fromtimestamp(int(j["t"]), tz=timezone.utc)
        return Quote(symbol, px, px, ts, now, "finnhub", "delayed", delayed=True)
