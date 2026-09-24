"""A small stateful fake of the Trading 212 v0 API, following the documented shapes.

It is a test double, not evidence that the real API behaves this way.
"""
from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal

import httpx

INSTRUMENTS = [
    {"ticker": "VUSDl_EQ", "isin": "IE00B3XXRP09", "currencyCode": "USD", "type": "ETF", "name": "Vanguard S&P 500 (USD)"},
    {"ticker": "VUSAl_EQ", "isin": "IE00B3XXRP09", "currencyCode": "GBP", "type": "ETF", "name": "Vanguard S&P 500"},
    {"ticker": "EQQQl_EQ", "isin": "IE0032077012", "currencyCode": "GBP", "type": "ETF", "name": "Invesco EQQQ"},
    {"ticker": "AAPL_US_EQ", "isin": "US0378331005", "currencyCode": "USD", "type": "STOCK", "name": "Apple"},
]


class FakeT212:
    def __init__(self, prices: dict[str, Decimal] | None = None, gbx: set[str] | None = None):
        self.prices = prices or {}               # in the instrument's quote unit (GBX for pence lines)
        self.gbx = gbx or set()
        self.cash = Decimal("0")
        self.positions: dict[str, dict] = {}
        self.history: list[dict] = []
        self.transactions: list[dict] = []
        self.calls: Counter = Counter()
        self.next_id = 1
        self.timeout_next_submit = False
        self.last_body = None
        self.last_auth = ""
        self.clock = None

    def _gbp(self, ticker: str, px: Decimal) -> Decimal:
        return px / 100 if ticker in self.gbx else px

    def _handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls[path] += 1
        self.last_auth = req.headers.get("Authorization", "")
        if path.endswith("/equity/metadata/instruments"):
            return httpx.Response(200, json=[{**i, "currencyCode": "GBX" if i["ticker"] in self.gbx else i["currencyCode"]}
                                             for i in INSTRUMENTS])
        if path.endswith("/equity/account/cash"):
            mv = sum(self._gbp(t, self.prices[t]) * p["quantity"] for t, p in self.positions.items())
            return httpx.Response(200, json={"free": float(self.cash), "total": float(self.cash + mv)})
        if path.endswith("/equity/portfolio"):
            return httpx.Response(200, json=[{"ticker": t, "quantity": float(p["quantity"]),
                                              "averagePrice": float(p["avg"]), "currentPrice": float(self.prices[t])}
                                             for t, p in self.positions.items() if p["quantity"] > 0])
        if path.endswith("/equity/orders") and req.method == "GET":
            return httpx.Response(200, json=[])
        if "/equity/orders/" in path and req.method == "GET":
            return httpx.Response(404)
        if path.endswith("/equity/history/orders"):
            return httpx.Response(200, json={"items": list(reversed(self.history))})
        if path.endswith("/history/transactions"):
            return httpx.Response(200, json={"items": self.transactions})
        if path.endswith("/equity/orders/market"):
            body = json.loads(req.content)
            self.last_body = body
            t, q = body["ticker"], Decimal(str(body["quantity"]))
            px = self.prices[t]
            gbp = self._gbp(t, px)
            pos = self.positions.setdefault(t, {"quantity": Decimal(0), "avg": px})
            if q > 0:
                if gbp * q > self.cash:
                    return httpx.Response(400, json={"code": "InsufficientFreeForStocksException"})
                pos["avg"] = (pos["avg"] * pos["quantity"] + px * q) / (pos["quantity"] + q)
                pos["quantity"] += q
                self.cash -= gbp * q
            else:
                if -q > pos["quantity"]:
                    return httpx.Response(400, json={"code": "SellingEquityNotOwned"})
                pos["quantity"] += q
                self.cash += gbp * -q
            now = (self.clock.now().isoformat() if self.clock else "2026-09-24T14:40:00+00:00")
            order = {"id": self.next_id, "ticker": t, "quantity": float(q), "filledQuantity": float(q),
                     "fillPrice": float(px), "status": "FILLED", "type": "MARKET", "creationTime": now,
                     "dateCreated": now}
            self.next_id += 1
            self.history.append(order)
            if self.timeout_next_submit:
                self.timeout_next_submit = False
                raise httpx.ReadTimeout("lost response", request=req)
            return httpx.Response(200, json=order)
        if req.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)
