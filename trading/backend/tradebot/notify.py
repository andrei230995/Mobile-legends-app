"""Phone notifications.

Backends: ntfy (free iPhone app; use a long random topic or a token-protected server)
and Telegram. Every notification is also stored in the database and shown in the web
app, so nothing is lost if a push backend fails. Messages never contain credentials.
"""
from __future__ import annotations

import logging

import httpx

from .config import Settings
from .db import Store, iso

log = logging.getLogger("tradebot.notify")

PRIORITY = {"fill": "default", "daily_report": "default", "failure": "high", "risk": "high",
            "critical": "urgent", "info": "low"}


class Notifier:
    def __init__(self, settings: Settings, store: Store, transport: httpx.BaseTransport | None = None):
        self.s, self.store = settings, store
        self._http = httpx.Client(timeout=8.0, transport=transport)

    def send(self, kind: str, title: str, body: str) -> None:
        delivered = []
        if self.s.ntfy_url:
            try:
                headers = {"Title": title[:120], "Priority": PRIORITY.get(kind, "default"), "Tags": kind}
                if self.s.ntfy_token:
                    headers["Authorization"] = f"Bearer {self.s.ntfy_token.get_secret_value()}"
                self._http.post(self.s.ntfy_url, content=body.encode()[:3500], headers=headers).raise_for_status()
                delivered.append("ntfy")
            except httpx.HTTPError as e:
                log.warning("ntfy delivery failed: %s", type(e).__name__)
        if self.s.telegram_bot_token and self.s.telegram_chat_id:
            try:
                url = f"https://api.telegram.org/bot{self.s.telegram_bot_token.get_secret_value()}/sendMessage"
                self._http.post(url, json={"chat_id": self.s.telegram_chat_id,
                                           "text": f"{title}\n\n{body}"[:4000]}).raise_for_status()
                delivered.append("telegram")
            except httpx.HTTPError as e:
                # never log the URL: it contains the bot token
                log.warning("telegram delivery failed: %s", type(e).__name__)
        self.store.execute("INSERT INTO notifications(ts,kind,title,body,delivered) VALUES(?,?,?,?,?)",
                           (iso(self.store.now()), kind, title, body, ",".join(delivered) or "in-app only"))

    def ping_health(self, ok: bool, detail: str = "") -> None:
        """Dead-man's switch: an external monitor alerts if these pings stop."""
        if not self.s.healthcheck_ping_url:
            return
        url = self.s.healthcheck_ping_url.get_secret_value().rstrip("/")
        try:
            self._http.post(url if ok else url + "/fail", content=detail.encode()[:1000])
        except httpx.HTTPError:
            log.warning("health ping failed")
