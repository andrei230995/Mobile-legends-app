"""Single-user authentication for the control panel.

* Password verified against an argon2 hash from the environment (the password itself
  is never stored). Optional TOTP second factor (any authenticator app).
* Session token in an HttpOnly, Secure, SameSite=Strict cookie; only its SHA-256 is
  stored server-side. Every state-changing request also needs the per-session CSRF
  token in the ``X-CSRF-Token`` header.
* Failed logins are throttled with exponential backoff.
* Sensitive actions (going live, loosening limits, approving a strategy for live)
  require the password (and TOTP when configured) again.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .config import Settings
from .db import Store, iso, parse_ts, utcnow

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class AuthError(Exception):
    pass


class Auth:
    def __init__(self, settings: Settings, store: Store):
        self.s, self.store = settings, store
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.s.admin_password_hash is not None

    def _throttle(self, key: str) -> None:
        with self._lock:
            n, until = self._fails.get(key, (0, 0.0))
            if time.monotonic() < until:
                raise AuthError(f"too many attempts; retry in {int(until - time.monotonic()) + 1}s")

    def _failed(self, key: str) -> None:
        with self._lock:
            n, _ = self._fails.get(key, (0, 0.0))
            n += 1
            self._fails[key] = (n, time.monotonic() + min(2 ** n, 900))

    def verify_credentials(self, password: str, totp: str | None, client_key: str) -> None:
        if not self.configured:
            raise AuthError("login disabled: TRADEBOT_ADMIN_PASSWORD_HASH is not configured")
        self._throttle(client_key)
        try:
            _ph.verify(self.s.admin_password_hash.get_secret_value(), password)
        except (VerificationError, InvalidHashError):
            self._failed(client_key)
            self.store.event("warning", "auth", "failed login/verification")
            raise AuthError("invalid credentials") from None
        if self.s.totp_secret:
            import pyotp
            if not totp or not pyotp.TOTP(self.s.totp_secret.get_secret_value()).verify(totp, valid_window=1):
                self._failed(client_key)
                self.store.event("warning", "auth", "failed TOTP verification")
                raise AuthError("invalid one-time code")
        with self._lock:
            self._fails.pop(client_key, None)

    def create_session(self) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = utcnow()
        self.store.execute("INSERT INTO sessions(token_hash,csrf,created_at,expires_at) VALUES(?,?,?,?)",
                           (_sha(token), csrf, iso(now), iso(now + timedelta(hours=self.s.session_ttl_hours))))
        self.store.execute("DELETE FROM sessions WHERE expires_at < ?", (iso(now),))
        self.store.event("info", "auth", "login")
        return token, csrf

    def session(self, token: str | None) -> dict | None:
        if not token:
            return None
        row = self.store.one("SELECT * FROM sessions WHERE token_hash=?", (_sha(token),))
        if not row or parse_ts(row["expires_at"]) < utcnow():
            return None
        return row

    def check_csrf(self, session: dict, header: str | None) -> bool:
        return bool(header) and hmac.compare_digest(session["csrf"], header)

    def logout(self, token: str) -> None:
        self.store.execute("DELETE FROM sessions WHERE token_hash=?", (_sha(token),))


def now_utc() -> datetime:
    return utcnow()
