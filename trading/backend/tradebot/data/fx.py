"""FX rates for converting account-currency values into GBP for reporting.

The ECB euro reference rates are free and public but are published once per
business day (around 16:00 CET), so they are *reference* rates, not the rate your
broker converts at. Reports therefore label FX-derived GBP figures with the rate,
its source and its timestamp. Broker-side conversions (deposits/withdrawals) are
recorded at the amounts the broker reports.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from ..models import D, FxRate

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


class FxUnavailable(Exception):
    pass


class FxProvider:
    def to_gbp(self, currency: str) -> FxRate:
        raise NotImplementedError


class FixedFx(FxProvider):
    """Constant rate, for tests and replay demos. Always labelled as an assumption."""

    def __init__(self, gbp_per_usd: Decimal = Decimal("0.75"), ts: datetime | None = None):
        self.rate = gbp_per_usd
        self.ts = ts

    def to_gbp(self, currency: str) -> FxRate:
        now = self.ts or datetime.now(timezone.utc)
        if currency == "GBP":
            return FxRate("GBP", "GBP", Decimal(1), now, "identity")
        if currency != "USD":
            raise FxUnavailable(currency)
        return FxRate("USD", "GBP", self.rate, now, "fixed-assumption")


class EcbFx(FxProvider):
    def __init__(self, timeout: float = 10.0, transport: httpx.BaseTransport | None = None,
                 max_age: timedelta = timedelta(hours=12)):
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._cache: tuple[datetime, dict[str, Decimal], datetime] | None = None
        self.max_age = max_age

    def _refresh(self) -> None:
        now = datetime.now(timezone.utc)
        if self._cache and now - self._cache[2] < self.max_age:
            return
        try:
            r = self._http.get(ECB_URL)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except (httpx.HTTPError, ET.ParseError) as e:
            if self._cache:
                return          # keep serving the last known rate; age is reported to the UI
            raise FxUnavailable(f"ECB rates unavailable: {e}") from e
        rates: dict[str, Decimal] = {"EUR": Decimal(1)}
        day = None
        for el in root.iter():
            if el.get("time"):
                day = el.get("time")
            if el.get("currency") and el.get("rate"):
                rates[el.get("currency")] = D(el.get("rate"))
        published = datetime.fromisoformat(f"{day}T15:00:00+00:00") if day else now
        self._cache = (published, rates, now)

    def to_gbp(self, currency: str) -> FxRate:
        if currency == "GBP":
            return FxRate("GBP", "GBP", Decimal(1), datetime.now(timezone.utc), "identity")
        self._refresh()
        published, rates, _ = self._cache
        if currency not in rates or "GBP" not in rates:
            raise FxUnavailable(currency)
        # rates are "1 EUR = x CCY": GBP per CCY = GBP/EUR / CCY/EUR
        return FxRate(currency, "GBP", (rates["GBP"] / rates[currency]).quantize(Decimal("0.000001")),
                      published, "ecb-reference")
