"""Durable state in SQLite (WAL mode).

Every order is written here *before* it is sent to a broker, so a crash or timeout
can always be reconciled against the broker using the deterministic client order id.
The ``events`` table is an append-only, timestamped audit trail.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .models import OrderStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, level TEXT NOT NULL, category TEXT NOT NULL,
    message TEXT NOT NULL, data TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    broker TEXT NOT NULL, mode TEXT NOT NULL, broker_order_id TEXT,
    symbol TEXT NOT NULL, side TEXT NOT NULL, type TEXT NOT NULL, tif TEXT NOT NULL,
    qty TEXT, notional TEXT, limit_price TEXT, stop_price TEXT,
    purpose TEXT NOT NULL, strategy TEXT, decision_id INTEGER,
    status TEXT NOT NULL, filled_qty TEXT NOT NULL DEFAULT '0', filled_avg_price TEXT,
    fees TEXT NOT NULL DEFAULT '0',
    created_at TEXT NOT NULL, submitted_at TEXT, updated_at TEXT NOT NULL,
    last_error TEXT, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL, symbol TEXT NOT NULL,
    side TEXT NOT NULL, qty TEXT NOT NULL, price TEXT NOT NULL, currency TEXT NOT NULL,
    fee TEXT NOT NULL, fx_to_gbp TEXT NOT NULL, fx_source TEXT NOT NULL, ts TEXT NOT NULL,
    purpose TEXT NOT NULL, strategy TEXT
);
CREATE INDEX IF NOT EXISTS fills_ts ON fills(ts);
CREATE TABLE IF NOT EXISTS managed_positions (
    symbol TEXT PRIMARY KEY, strategy TEXT NOT NULL, strategy_version TEXT NOT NULL,
    opened_at TEXT NOT NULL, session_date TEXT NOT NULL,
    stop_price TEXT, stop_mode TEXT NOT NULL,            -- broker | software | none
    protective_cid TEXT, max_hold_until TEXT, entry_cid TEXT NOT NULL,
    exit_pending_reason TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, mode TEXT NOT NULL,
    strategy TEXT, symbol TEXT, action TEXT NOT NULL, outcome TEXT NOT NULL,
    summary TEXT NOT NULL, checks TEXT, evidence TEXT
);
CREATE INDEX IF NOT EXISTS decisions_ts ON decisions(ts);
CREATE TABLE IF NOT EXISTS cash_flows (
    flow_id TEXT PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, amount TEXT NOT NULL,
    currency TEXT NOT NULL, fx_to_gbp TEXT NOT NULL, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    ts TEXT PRIMARY KEY, currency TEXT NOT NULL, equity TEXT NOT NULL, cash TEXT NOT NULL,
    fx_to_gbp TEXT NOT NULL, equity_gbp TEXT NOT NULL, unrealised_gbp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_reports (
    report_date TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_limits_versions (
    version INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, body TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS strategy_registry (
    name TEXT PRIMARY KEY, version TEXT NOT NULL, status TEXT NOT NULL,
    validation TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, news_id TEXT NOT NULL,
    symbol TEXT, model TEXT NOT NULL, result TEXT NOT NULL, cost_usd REAL NOT NULL,
    news_created_at TEXT, UNIQUE(news_id, model)
);
CREATE TABLE IF NOT EXISTS opex (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL,
    kind TEXT NOT NULL, amount_gbp TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL,
    title TEXT NOT NULL, body TEXT NOT NULL, delivered TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def parse_ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _jsonable(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "value"):
        return o.value
    raise TypeError(f"not JSON serialisable: {type(o)}")


def dumps(o: Any) -> str:
    return json.dumps(o, default=_jsonable, sort_keys=True)


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.now = utcnow          # replaced by the engine's clock (replay/tests)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    # -- generic helpers -------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- key/value state ---------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                     (key, dumps(value), iso(utcnow())))

    # -- audit ---------------------------------------------------------------
    def event(self, level: str, category: str, message: str, data: Any = None,
              ts: datetime | None = None) -> None:
        self.execute("INSERT INTO events(ts,level,category,message,data) VALUES(?,?,?,?,?)",
                     (iso(ts or self.now()), level, category, message,
                      dumps(data) if data is not None else None))

    # -- orders ----------------------------------------------------------------
    def insert_order(self, row: dict[str, Any]) -> None:
        cols = ",".join(row)
        self.execute(f"INSERT INTO orders({cols}) VALUES({','.join('?' * len(row))})",
                     [str(v) if isinstance(v, Decimal) else v for v in row.values()])

    def update_order(self, cid: str, **fields: Any) -> None:
        fields.setdefault("updated_at", iso(self.now()))
        sets = ",".join(f"{k}=?" for k in fields)
        vals = [(v.value if isinstance(v, OrderStatus) else str(v) if isinstance(v, Decimal) else v)
                for v in fields.values()]
        self.execute(f"UPDATE orders SET {sets} WHERE client_order_id=?", [*vals, cid])

    def get_order(self, cid: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM orders WHERE client_order_id=?", (cid,))

    def orders_with_status(self, *statuses: OrderStatus) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(statuses))
        return self.query(f"SELECT * FROM orders WHERE status IN ({marks}) ORDER BY created_at",
                          [s.value for s in statuses])
