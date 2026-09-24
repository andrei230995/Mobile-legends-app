"""Control-panel security and live-activation gating."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tradebot.api import create_app
from tradebot.auth import hash_password
from tradebot.clock import SimClock
from tradebot.config import Settings
from tradebot.runtime import Runtime

PW = "correct horse battery staple"


@pytest.fixture
def client(tmp_path):
    s = Settings(data_dir=tmp_path, broker="sim", market_data="replay", cookie_secure=False,
                 admin_password_hash=SecretStr(hash_password(PW)), sim_starting_cash_usd=1000)
    rt = Runtime(s, clock=SimClock(datetime(2018, 6, 4, 13, 45, tzinfo=timezone.utc)))
    app = create_app(rt, start_engine=False)
    with TestClient(app) as c:
        rt.tick_once()
        yield c, rt


def login(c):
    r = c.post("/api/login", json={"password": PW})
    assert r.status_code == 200
    return {"X-CSRF-Token": r.json()["csrf"]}


def test_requires_login(client):
    c, _ = client
    assert c.get("/api/state").status_code == 401
    assert c.post("/api/control/start", json={}).status_code == 401


def test_wrong_password_and_throttle(client):
    c, _ = client
    assert c.post("/api/login", json={"password": "nope"}).status_code == 401
    r = c.post("/api/login", json={"password": PW})
    assert r.status_code == 401 and "too many attempts" in r.json()["detail"]


def test_csrf_required_for_state_changes(client):
    c, _ = client
    login(c)
    assert c.post("/api/control/pause_entries", json={}).status_code == 403
    assert c.post("/api/control/pause_entries", json={}, headers={"X-CSRF-Token": "bad"}).status_code == 403


def test_state_and_controls(client):
    c, rt = client
    hdr = login(c)
    st = c.get("/api/state").json()
    assert st["mode"] == "paper" and st["clock"] == "replay"
    assert "Broker credentials: running the built-in simulator (TRADEBOT_BROKER=sim)" in st["missing_configuration"]
    assert c.post("/api/control/pause_entries", json={}, headers=hdr).json()["ok"]
    assert c.get("/api/state").json()["entries_paused"] is True
    assert c.post("/api/control/unknown", json={}, headers=hdr).status_code == 404
    assert "set-cookie" not in {k.lower() for k in c.get("/api/state").headers}
    assert c.get("/api/state").headers["cache-control"] == "no-store"


def test_live_activation_blocked_with_reasons(client):
    c, _ = client
    hdr = login(c)
    r = c.post("/api/mode", json={"mode": "live", "password": PW, "confirm": "ENABLE LIVE TRADING"}, headers=hdr)
    assert r.status_code == 409
    blocked = r.json()["detail"]["blocked"]
    assert any("no real broker" in b for b in blocked)
    assert any("promotion gate" in b for b in blocked)
    assert any("replayed history" in b for b in blocked)


def test_live_activation_requires_password(client):
    c, _ = client
    hdr = login(c)
    r = c.post("/api/mode", json={"mode": "live", "password": "wrong", "confirm": "ENABLE LIVE TRADING"}, headers=hdr)
    assert r.status_code == 403


def test_loosening_limits_needs_password_tightening_does_not(client):
    c, _ = client
    hdr = login(c)
    r = c.put("/api/risk-limits", json={"limits": {"daily_loss_limit_pct": 2.0}}, headers=hdr)
    assert r.status_code == 200 and r.json()["loosened"] == []
    r = c.put("/api/risk-limits", json={"limits": {"daily_loss_limit_pct": 10.0}}, headers=hdr)
    assert r.status_code == 403
    r = c.put("/api/risk-limits", json={"limits": {"daily_loss_limit_pct": 10.0}, "password": PW}, headers=hdr)
    assert r.status_code == 200 and "daily_loss_limit_pct" in r.json()["loosened"]


def test_hard_ceilings_cannot_be_configured(client):
    c, _ = client
    hdr = login(c)
    r = c.put("/api/risk-limits", json={"limits": {"max_total_exposure_pct": 150}, "password": PW}, headers=hdr)
    assert r.status_code == 422


def test_strategy_cannot_be_approved_when_gate_fails(client):
    c, rt = client
    hdr = login(c)
    key = rt.engine().slots[0].key
    r = c.post(f"/api/strategies/{key}/approve-live", json={"password": PW}, headers=hdr)
    assert r.status_code == 409 and "forward_paper_min_sessions" in r.json()["detail"]["failed"]


def test_no_secrets_in_state(client, tmp_path):
    c, rt = client
    login(c)
    body = c.get("/api/state").text
    assert rt.s.admin_password_hash.get_secret_value() not in body


def test_security_headers_and_health(client):
    c, _ = client
    r = c.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert c.get("/healthz").status_code in (200, 503)
