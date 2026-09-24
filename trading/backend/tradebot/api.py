"""HTTP API and static iPhone web app (PWA).

Run:  uvicorn tradebot.api:app --host 127.0.0.1 --port 8000   (behind Caddy for HTTPS)
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import Auth, AuthError
from .config import RiskLimits, Settings
from .db import dumps, iso
from .models import D, Mode
from .runtime import Runtime

log = logging.getLogger("tradebot.api")
WEB = Path(__file__).parent / "web"
COOKIE = "tb_session"
LIVE_PHRASE = "ENABLE LIVE TRADING"


class LoginBody(BaseModel):
    password: str = Field(max_length=256)
    totp: str | None = Field(default=None, max_length=12)


class Cmd(BaseModel):
    symbol: str | None = Field(default=None, max_length=12)


class ModeBody(BaseModel):
    mode: Mode
    password: str | None = Field(default=None, max_length=256)
    totp: str | None = Field(default=None, max_length=12)
    confirm: str | None = Field(default=None, max_length=64)


class LimitsBody(BaseModel):
    limits: dict[str, Any]
    password: str | None = Field(default=None, max_length=256)
    totp: str | None = Field(default=None, max_length=12)
    confirm_only: bool = False


class ApproveBody(BaseModel):
    password: str = Field(max_length=256)
    totp: str | None = Field(default=None, max_length=12)


class FlowBody(BaseModel):
    kind: str = Field(pattern="^(deposit|withdrawal)$")
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(pattern="^(GBP|USD)$")


class Ctx:
    runtime: Runtime
    auth: Auth


ctx = Ctx()


def create_app(runtime: Runtime | None = None, start_engine: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx.runtime = runtime or Runtime(Settings.from_env())
        ctx.auth = Auth(ctx.runtime.s, ctx.runtime.control)
        if start_engine:
            ctx.runtime.start()
        yield
        ctx.runtime.stop()

    app = FastAPI(title="Tradebot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                                   "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                                                   "base-uri 'none'; form-action 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------ auth plumbing
    def client_key(request: Request) -> str:
        return request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]

    def require_session(request: Request) -> dict:
        sess = ctx.auth.session(request.cookies.get(COOKIE))
        if not sess:
            raise HTTPException(401, "login required")
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            allowed = ctx.runtime.s.allowed_origin
            if allowed and origin and origin != allowed:
                raise HTTPException(403, "origin not allowed")
            if not ctx.auth.check_csrf(sess, request.headers.get("x-csrf-token")):
                raise HTTPException(403, "missing or invalid CSRF token")
        return sess

    def reauth(request: Request, password: str | None, totp: str | None) -> None:
        if not password:
            raise HTTPException(403, "password required for this action")
        try:
            ctx.auth.verify_credentials(password or "", totp, client_key(request))
        except AuthError as e:
            raise HTTPException(403, str(e)) from None


    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response):
        try:
            ctx.auth.verify_credentials(body.password, body.totp, client_key(request))
        except AuthError as e:
            raise HTTPException(401, str(e)) from None
        token, csrf = ctx.auth.create_session()
        response.set_cookie(COOKIE, token, httponly=True, secure=ctx.runtime.s.cookie_secure, samesite="strict",
                            max_age=ctx.runtime.s.session_ttl_hours * 3600, path="/")
        return {"ok": True, "csrf": csrf}

    @app.post("/api/logout")
    def logout(request: Request, response: Response, sess: dict = Depends(require_session)):
        ctx.auth.logout(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/session")
    def session_info(request: Request):
        sess = ctx.auth.session(request.cookies.get(COOKIE))
        return {"authenticated": bool(sess), "csrf": sess["csrf"] if sess else None,
                "login_configured": ctx.auth.configured, "totp_required": ctx.runtime.s.totp_secret is not None}

    # ------------------------------------------------------------------ health
    @app.get("/healthz")
    def healthz():
        h = ctx.runtime.health()
        return JSONResponse({"ok": h["healthy"]}, status_code=200 if h["healthy"] else 503)

    # ------------------------------------------------------------------ state
    @app.get("/api/state")
    def state(sess: dict = Depends(require_session)):
        return build_state()

    @app.get("/api/decisions")
    def decisions(limit: int = 50, include_holds: bool = False, sess: dict = Depends(require_session)):
        where = "" if include_holds else "WHERE outcome != 'hold' "
        rows = ctx.runtime.engine().store.query(f"SELECT * FROM decisions {where}ORDER BY id DESC LIMIT ?",
                                                (min(limit, 500),))
        for r in rows:
            r["checks"] = json.loads(r["checks"]) if r["checks"] else []
            r["evidence"] = json.loads(r["evidence"]) if r["evidence"] else None
        return rows

    @app.get("/api/reports")
    def reports(sess: dict = Depends(require_session)):
        rows = ctx.runtime.engine().store.query("SELECT report_date, body FROM daily_reports ORDER BY report_date DESC LIMIT 60")
        return [{"date": r["report_date"], **json.loads(r["body"])} for r in rows]

    @app.get("/api/events")
    def events(limit: int = 100, sess: dict = Depends(require_session)):
        eng = ctx.runtime.engine()
        rows = eng.store.query("SELECT ts, level, category, message FROM events ORDER BY id DESC LIMIT ?", (min(limit, 1000),))
        rows += ctx.runtime.control.query("SELECT ts, level, category, message FROM events ORDER BY id DESC LIMIT ?",
                                          (min(limit, 1000),))
        return sorted(rows, key=lambda r: r["ts"], reverse=True)[:limit]

    @app.get("/api/orders")
    def orders(sess: dict = Depends(require_session)):
        return ctx.runtime.engine().store.query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 200")

    # ------------------------------------------------------------------ controls

    @app.post("/api/control/{name}")
    def control(name: str, body: Cmd, sess: dict = Depends(require_session)):
        allowed = {"start", "stop", "pause_entries", "resume_entries", "cancel_pending_entries",
                   "close_position", "close_all"}
        if name not in allowed:
            raise HTTPException(404, "unknown control")
        if name == "close_position" and not body.symbol:
            raise HTTPException(422, "symbol required")
        if name == "start" and ctx.runtime.control.get("mode") == Mode.LIVE.value and not ctx.runtime.control.get(
                "live_authorisation"):
            raise HTTPException(409, "live mode is not authorised")
        kw = {"symbol": body.symbol.upper()} if body.symbol else {}
        return ctx.runtime.engine().command(name, **kw)


    @app.post("/api/mode")
    def set_mode(body: ModeBody, request: Request, sess: dict = Depends(require_session)):
        if body.mode == Mode.LIVE:
            reauth(request, body.password, body.totp)
            problems = live_blockers()
            if body.confirm != LIVE_PHRASE:
                problems.append(f'type the phrase "{LIVE_PHRASE}" to confirm')
            if problems:
                raise HTTPException(409, {"blocked": problems})
            auth = {"at": iso(ctx.runtime.clock.now()), "limits_version": current_limits_version(),
                    "approved_strategies": [s["name"] for s in approved_live()]}
            res = ctx.runtime.set_mode(Mode.LIVE, auth)
            if res["ok"]:
                ctx.runtime.engine("live").command("start")
            return res
        res = ctx.runtime.set_mode(body.mode)
        if not res["ok"]:
            raise HTTPException(409, res["error"])
        return res


    @app.put("/api/risk-limits")
    def put_limits(body: LimitsBody, request: Request, sess: dict = Depends(require_session)):
        eng = ctx.runtime.engine()
        current = eng.limits()
        try:
            new = RiskLimits.model_validate({**current.as_dict(), **body.limits})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, str(e)) from None
        loosened = current.loosened_fields(new)
        if loosened or body.confirm_only:
            reauth(request, body.password, body.totp)
        ctx.runtime.control.execute("INSERT INTO risk_limits_versions(ts,body,note) VALUES(?,?,?)",
                                    (iso(ctx.runtime.clock.now()), new.model_dump_json(),
                                     "confirmed by user" if body.confirm_only else f"changed by user; loosened={loosened}"))
        ctx.runtime.control.set("limits_confirmed_version", current_limits_version())
        ctx.runtime.control.event("warning" if loosened else "info", "risk", "risk limits updated",
                                  {"loosened": loosened})
        return {"ok": True, "version": current_limits_version(), "loosened": loosened}


    @app.post("/api/strategies/{key}/approve-live")
    def approve_live(key: str, body: ApproveBody, request: Request, sess: dict = Depends(require_session)):
        reauth(request, body.password, body.totp)
        gate = promotion_gate(key)
        if not gate["eligible"]:
            raise HTTPException(409, {"failed": gate["failed"]})
        for env in ("paper", "live"):
            try:
                eng = ctx.runtime.engine(env)
            except RuntimeError:
                continue
            eng.store.execute("UPDATE strategy_registry SET status='approved_live', updated_at=? WHERE name=?",
                              (iso(ctx.runtime.clock.now()), key))
            eng.reload_slots()
        ctx.runtime.control.event("warning", "strategy", f"{key} approved for live by user", gate)
        return {"ok": True}


    @app.post("/api/cash-flows")
    def add_flow(body: FlowBody, sess: dict = Depends(require_session)):
        """Manual record for brokers that don't expose deposits/withdrawals via API."""
        eng = ctx.runtime.engine()
        rate, _ = eng._fx_rate(body.currency)
        amt = body.amount if body.kind == "deposit" else -body.amount
        now = ctx.runtime.clock.now()
        fid = f"manual-{int(now.timestamp() * 1000)}"
        eng.store.execute("INSERT INTO cash_flows(flow_id,ts,kind,amount,currency,fx_to_gbp,source) VALUES(?,?,?,?,?,?,?)",
                          (fid, iso(now), body.kind, str(amt), body.currency, str(rate), "manual"))
        snap = eng.store.one("SELECT equity_gbp FROM equity_snapshots ORDER BY ts DESC LIMIT 1")
        eq = D(snap["equity_gbp"]) if snap else D(0)
        eng.nav.apply_flow(amt * rate, eq)
        eng.store.event("info", "cash", f"manual {body.kind} {body.amount} {body.currency} recorded")
        return {"ok": True}

    # ------------------------------------------------------------------ static PWA
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(WEB / "icons" / "icon-192.png", media_type="image/png")

    @app.get("/sw.js")
    def sw():
        return FileResponse(WEB / "sw.js", media_type="text/javascript")

    return app


# ---------------------------------------------------------------------- state helpers
def current_limits_version() -> int:
    return ctx.runtime.control.one("SELECT MAX(version) AS v FROM risk_limits_versions")["v"]


def approved_live() -> list[dict]:
    return ctx.runtime.engine().store.query("SELECT name FROM strategy_registry WHERE status='approved_live'")


def promotion_gate(key: str) -> dict[str, Any]:
    eng = ctx.runtime.engine("paper")
    row = eng.store.one("SELECT * FROM strategy_registry WHERE name=?", (key,))
    if not row:
        return {"eligible": False, "failed": ["unknown strategy"]}
    v = json.loads(row["validation"] or "{}")
    checks = dict(v.get("checks", {}))
    from .backtest.validate import CRITERIA
    fwd = eng.forward_paper_stats(key)
    checks["forward_paper_min_sessions"] = fwd["sessions"] >= CRITERIA["forward_paper_min_sessions"]
    checks["forward_paper_min_closed_trades"] = fwd["closed_trades"] >= CRITERIA["forward_paper_min_closed_trades"]
    failed = [k for k, ok in checks.items() if not ok]
    return {"eligible": not failed, "failed": failed, "checks": checks, "forward_paper": fwd,
            "validation_source": v.get("source"), "validation_data_end": v.get("data_end")}


def live_blockers() -> list[str]:
    s = ctx.runtime.s
    out = []
    if s.broker == "sim":
        out.append("no real broker configured (TRADEBOT_BROKER=sim)")
    elif not s.live_credentials_present():
        out.append(f"{s.broker} live credentials are not configured on the server")
    if ctx.runtime.data().is_replay:
        out.append("market data is replayed history; live trading needs a live data feed")
    if ctx.runtime.control.get("limits_confirmed_version") != current_limits_version():
        out.append("confirm the current risk limits first")
    if not approved_live():
        out.append("no strategy has passed the promotion gate and been approved for live")
    return out


def build_state() -> dict[str, Any]:
    rt = ctx.runtime
    eng = rt.engine()
    st, ctl = eng.store, rt.control
    L = eng.limits()
    snap = st.one("SELECT * FROM equity_snapshots ORDER BY ts DESC LIMIT 1")
    anchor = st.get("day_anchor") or {}
    today = None
    if snap and anchor:
        from .clock import local_report_due
        now = rt.clock.now()
        rdate, due = local_report_due(now, L.report_time_local, L.report_timezone)
        from datetime import timedelta
        start = due if now >= due else due - timedelta(days=1)
        today = eng.build_report(rdate, start, now + timedelta(seconds=1))
    positions = []
    try:
        for p in eng.broker.get_positions():
            mp = st.one("SELECT * FROM managed_positions WHERE symbol=?", (p.symbol,)) or {}
            positions.append({"symbol": p.symbol, "qty": str(p.qty), "avg_price": str(p.avg_entry_price),
                              "price": str(p.market_price), "value": str(p.market_value.quantize(Decimal("0.01"))),
                              "currency": p.currency, "strategy": mp.get("strategy", "external/manual"),
                              "stop_price": mp.get("stop_price"), "stop_mode": mp.get("stop_mode", "none"),
                              "opened_at": mp.get("opened_at"), "exit_pending": mp.get("exit_pending_reason")})
    except Exception as e:  # noqa: BLE001
        positions = [{"error": f"positions unavailable: {type(e).__name__}"}]
    pending = [{k: r[k] for k in ("client_order_id", "symbol", "side", "type", "qty", "stop_price", "status",
                                   "purpose", "created_at", "filled_qty")} for r in eng.exec.open_rows()]
    decisions = st.query("SELECT id, ts, strategy, symbol, action, outcome, summary FROM decisions "
                         "WHERE outcome != 'hold' ORDER BY id DESC LIMIT 25")
    strategies = []
    for row in st.query("SELECT name, status, validation FROM strategy_registry ORDER BY name"):
        v = json.loads(row["validation"] or "{}")
        strategies.append({"key": row["name"], "status": row["status"], "params": v.get("params"),
                           "expected_edge_bps": v.get("expected_edge_bps"),
                           "failed_checks": [k for k, ok in (v.get("checks") or {}).items() if not ok],
                           "forward_paper": eng.forward_paper_stats(row["name"])})
    nav_peak = D(st.get("nav_peak", "0"))
    units = D(st.get("nav_units", "0"))
    eq_gbp = D(snap["equity_gbp"]) if snap else D(0)
    dd = ((nav_peak - eq_gbp / units) / nav_peak * 100) if nav_peak > 0 and units > 0 else D(0)
    day_pnl_pct = D(today["return_on_start_equity_pct"]) if today and today.get("return_on_start_equity_pct") else D(0)
    return json.loads(dumps({
        "server_time": iso(rt.clock.now()), "clock": rt.health()["clock"],
        "mode": ctl.get("mode"), "env": rt.env, "trading_enabled": ctl.get("trading_enabled"),
        "entries_paused": ctl.get("entries_paused"), "entries_paused_reason": ctl.get("entries_paused_reason"),
        "halted_reason": ctl.get("halted_reason"), "live_authorisation": ctl.get("live_authorisation"),
        "live_blockers": live_blockers(),
        "broker": {"name": eng.broker.capabilities.name, "paper": eng.broker.capabilities.is_paper,
                   "status": st.get("broker_status"), "notes": list(eng.broker.capabilities.notes)},
        "account": {"currency": snap["currency"] if snap else None, "equity": snap["equity"] if snap else None,
                    "cash": snap["cash"] if snap else None, "equity_gbp": snap["equity_gbp"] if snap else None,
                    "fx_to_gbp": snap["fx_to_gbp"] if snap else None, "as_of": snap["ts"] if snap else None},
        "today": today, "positions": positions, "pending_orders": pending, "decisions": decisions,
        "strategies": strategies,
        "risk": {"limits": L.as_dict(), "version": current_limits_version(),
                 "confirmed": ctl.get("limits_confirmed_version") == current_limits_version(),
                 "drawdown_pct": str(dd.quantize(Decimal("0.01"))),
                 "day_pnl_pct": str(day_pnl_pct),
                 "daily_loss_budget_remaining_pct": str((D(L.daily_loss_limit_pct) + min(day_pnl_pct, D(0))).quantize(Decimal("0.01"))),
                 "drawdown_budget_remaining_pct": str((D(L.max_drawdown_pct) - dd).quantize(Decimal("0.01")))},
        "data": st.get("data_status"), "fx": st.get("fx_status"), "reconciliation": st.get("reconciliation"),
        "health": rt.health(), "alerts": st.get("active_alerts", {}),
        "notifications": st.query("SELECT ts, kind, title, body, delivered FROM notifications ORDER BY id DESC LIMIT 15"),
        "missing_configuration": rt.s.missing_requirements(),
        "ai": {"enabled": bool(eng.assessor and eng.assessor.enabled), "mode": ctl.get("ai_mode", "shadow"),
               "model": rt.s.ai_model},
    }))


app = create_app()
