"""Broker adapters against mocked HTTP in the documented formats (no network)."""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from tradebot.brokers.alpaca import AlpacaBroker
from tradebot.brokers.base import (BrokerAuthError, BrokerRateLimited, BrokerRejected, BrokerTimeout,
                                   BrokerUnavailable, USRegulatoryFeeModel)
from tradebot.brokers.trading212 import Trading212Broker
from tradebot.config import BrokerCredentials
from tradebot.data.alpaca_data import AlpacaMarketData
from tradebot.models import OrderRequest, OrderStatus, OrderType, Side

CREDS = BrokerCredentials(SecretStr("KEYID"), SecretStr("SECRETVALUE"))

ORDER = {"id": "o1", "client_order_id": "c1", "symbol": "SPY", "side": "buy", "type": "market",
         "status": "partially_filled", "qty": "0.5", "notional": None, "filled_qty": "0.2",
         "filled_avg_price": "500.10", "submitted_at": "2026-09-24T13:40:00.123456789Z",
         "updated_at": "2026-09-24T13:40:01Z"}


def alpaca(handler) -> AlpacaBroker:
    return AlpacaBroker(CREDS, paper=True, transport=httpx.MockTransport(handler))


def test_alpaca_auth_headers_paper_host_and_order_mapping():
    seen = {}

    def h(req: httpx.Request):
        seen["host"] = req.url.host
        seen["key"] = req.headers["APCA-API-KEY-ID"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=ORDER)

    o = alpaca(h).submit_order(OrderRequest("c1", "SPY", Side.BUY, OrderType.MARKET, qty=Decimal("0.5")))
    assert seen["host"] == "paper-api.alpaca.markets" and seen["key"] == "KEYID"
    assert seen["body"] == {"symbol": "SPY", "side": "buy", "type": "market", "time_in_force": "day",
                            "client_order_id": "c1", "qty": "0.5"}
    assert o.status == OrderStatus.PARTIALLY_FILLED and o.filled_qty == Decimal("0.2")


def test_alpaca_live_uses_live_host():
    b = AlpacaBroker(CREDS, paper=False, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert str(b._http.base_url).startswith("https://api.alpaca.markets")


@pytest.mark.parametrize("status,exc", [(401, BrokerAuthError), (429, BrokerRateLimited),
                                        (403, BrokerRejected), (422, BrokerRejected), (503, BrokerTimeout)])
def test_alpaca_submit_error_classification(status, exc):
    b = alpaca(lambda r: httpx.Response(status, json={"message": "insufficient buying power"}))
    with pytest.raises(exc):
        b.submit_order(OrderRequest("c1", "SPY", Side.BUY, OrderType.MARKET, qty=Decimal("1")))


def test_alpaca_timeout_on_submit_is_unknown_but_on_read_is_unavailable():
    def h(req):
        raise httpx.ReadTimeout("timeout", request=req)
    b = alpaca(h)
    with pytest.raises(BrokerTimeout):
        b.submit_order(OrderRequest("c1", "SPY", Side.BUY, OrderType.MARKET, qty=Decimal("1")))
    with pytest.raises(BrokerUnavailable):
        b.get_account()


def test_alpaca_connect_error_on_submit_is_safe_not_sent():
    def h(req):
        raise httpx.ConnectError("refused", request=req)
    with pytest.raises(BrokerUnavailable):
        alpaca(h).submit_order(OrderRequest("c1", "SPY", Side.BUY, OrderType.MARKET, qty=Decimal("1")))


def test_alpaca_lookup_by_client_id_and_missing():
    def h(req):
        if req.url.params.get("client_order_id") == "c1":
            return httpx.Response(200, json=ORDER)
        return httpx.Response(404, json={"message": "not found"})
    b = alpaca(h)
    assert b.find_order_by_client_id("c1").broker_order_id == "o1"
    assert b.find_order_by_client_id("nope") is None


def test_alpaca_account_never_uses_margin_buying_power():
    acct = {"cash": "12.50", "equity": "12.50", "buying_power": "50.00", "non_marginable_buying_power": "12.50",
            "currency": "USD", "status": "ACTIVE", "trading_blocked": False}
    a = alpaca(lambda r: httpx.Response(200, json=acct)).get_account()
    assert a.buying_power == Decimal("12.50")


def test_alpaca_data_quote_and_bars_exclude_incomplete_session():
    from datetime import datetime, timezone

    def h(req):
        if "quotes/latest" in req.url.path:
            return httpx.Response(200, json={"quote": {"bp": 500.0, "ap": 500.1, "t": "2026-09-24T14:00:00.5Z",
                                                       "bs": 1, "as": 2}})
        return httpx.Response(200, json={"bars": [
            {"t": "2026-09-22T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
            {"t": "2026-09-23T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.6, "v": 10},
            {"t": "2026-09-24T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.7, "v": 10}], "next_page_token": None})
    md = AlpacaMarketData(CREDS, transport=httpx.MockTransport(h))
    q = md.latest_quote("SPY")
    assert q.feed == "iex" and q.source == "alpaca" and q.spread_bps > 0
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)        # session in progress
    bars = md.daily_bars("SPY", 10, now)
    assert [str(b.day) for b in bars] == ["2026-09-22", "2026-09-23"]


def test_t212_basic_auth_market_only_live_and_sell_sign():
    seen = {}

    def h(req):
        seen["auth"] = req.headers["Authorization"]
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content) if req.content else None
        return httpx.Response(200, json={"id": 42, "ticker": "SPY_US_EQ", "quantity": -0.5, "filledQuantity": 0,
                                         "status": "NEW", "type": "MARKET", "creationTime": "2026-09-24T13:40:00Z"})
    b = Trading212Broker(CREDS, demo=False, transport=httpx.MockTransport(h))
    o = b.submit_order(OrderRequest("c1", "SPY", Side.SELL, OrderType.MARKET, qty=Decimal("0.5")))
    assert seen["auth"].startswith("Basic ") and seen["path"].endswith("/equity/orders/market")
    assert seen["body"]["quantity"] == -0.5 and o.side == Side.SELL
    assert not b.capabilities.protective_stop_for_fractional       # live: market orders only
    with pytest.raises(BrokerRejected):
        b.submit_order(OrderRequest("c2", "SPY", Side.SELL, OrderType.STOP, qty=Decimal("0.5"),
                                    stop_price=Decimal("400")))


def test_t212_reconciles_without_client_id_by_matching():
    from datetime import datetime, timezone

    def h(req):
        if req.url.path.endswith("/equity/orders"):
            return httpx.Response(200, json=[{"id": 7, "ticker": "SPY_US_EQ", "quantity": 0.5, "status": "FILLED",
                                              "filledQuantity": 0.5, "fillPrice": 500, "type": "MARKET",
                                              "creationTime": "2026-09-24T13:40:01Z"}])
        return httpx.Response(200, json={"items": []})
    b = Trading212Broker(CREDS, demo=True, transport=httpx.MockTransport(h))
    hint = OrderRequest("c1", "SPY", Side.BUY, OrderType.MARKET, qty=Decimal("0.5"))
    found = b.find_order_by_client_id("c1", hint=hint, since=datetime(2026, 9, 24, 13, 40, tzinfo=timezone.utc))
    assert found and found.broker_order_id == "7" and found.client_order_id == "c1"
    assert b.find_order_by_client_id("c1", hint=OrderRequest("c1", "SPY", Side.BUY, OrderType.MARKET,
                                                              qty=Decimal("0.6"))) is None


def test_t212_auth_error():
    b = Trading212Broker(CREDS, demo=True, transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(BrokerAuthError):
        b.get_account()


def test_regulatory_fee_cent_rounding_hits_small_orders():
    f = USRegulatoryFeeModel()
    assert f.fee(Side.BUY, Decimal("1"), Decimal("500")) == 0
    assert f.fee(Side.SELL, Decimal("0.01"), Decimal("500")) == Decimal("0.02")   # $5 sale -> 2 cents (0.4%)
    big = f.fee(Side.SELL, Decimal("100"), Decimal("500"))
    assert big == Decimal("1.03") + Decimal("0.02")


def test_secrets_not_in_repr():
    from tradebot.config import Settings
    s = Settings(alpaca_paper=CREDS)
    assert "SECRETVALUE" not in repr(s) and "SECRETVALUE" not in str(s.model_dump())
