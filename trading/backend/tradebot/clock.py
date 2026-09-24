"""Clocks and the exchange calendar.

All internal timestamps are timezone-aware UTC. Session times come from the
``exchange_calendars`` NYSE calendar (holidays, early closes) and are converted with
zoneinfo, so US and UK daylight-saving transitions - which happen on different dates -
are handled without hard-coded offsets. When a broker exposes its own clock
(Alpaca ``/v2/clock``) the engine prefers it and uses this calendar as a cross-check.
"""
from __future__ import annotations

import functools
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from .models import MarketClock

NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep_hint(self, seconds: float) -> None:  # real clock: engine thread sleeps
        pass


class SimClock(Clock):
    def __init__(self, start: datetime):
        assert start.tzinfo is not None
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = dt.astimezone(timezone.utc)

    def advance(self, **kw: float) -> datetime:
        self._now += timedelta(**kw)
        return self._now


@functools.lru_cache(maxsize=4)
def _calendar(code: str):
    import exchange_calendars as xcals
    return xcals.get_calendar(code, start="2015-01-01")


class MarketCalendar:
    """Regular sessions of one exchange: NYSE (``XNYS``, default - signals and US execution)
    or London (``XLON`` - execution of UCITS ETFs on Trading 212). No extended hours."""

    def __init__(self, code: str = "XNYS"):
        self.code = code
        self.tz = NY if code == "XNYS" else LONDON

    def session_bounds(self, day: date) -> tuple[datetime, datetime] | None:
        cal = _calendar(self.code)
        ts = pd.Timestamp(day)
        if not cal.is_session(ts):
            return None
        o = cal.session_open(ts).to_pydatetime().astimezone(timezone.utc)
        c = cal.session_close(ts).to_pydatetime().astimezone(timezone.utc)
        return o, c

    def session_date(self, now: datetime) -> date:
        """The exchange-local calendar date of ``now``."""
        return now.astimezone(self.tz).date()

    def clock(self, now: datetime) -> MarketClock:
        today = self.session_date(now)
        bounds = self.session_bounds(today)
        is_open = bool(bounds and bounds[0] <= now < bounds[1])
        next_bounds = None
        d = today
        for _ in range(20):
            b = self.session_bounds(d)
            if b and b[0] > now:
                next_bounds = b
                break
            d += timedelta(days=1)
        assert next_bounds is not None, "calendar exhausted"
        return MarketClock(is_open=is_open, next_open=next_bounds[0],
                           next_close=bounds[1] if is_open else next_bounds[1],
                           session_open=bounds[0] if bounds else None,
                           session_close=bounds[1] if bounds else None)

    def previous_sessions(self, before: date, n: int) -> list[date]:
        cal = _calendar(self.code)
        sessions = cal.sessions_in_range(pd.Timestamp(before) - pd.Timedelta(days=n * 2 + 10),
                                         pd.Timestamp(before) - pd.Timedelta(days=1))
        return [s.date() for s in sessions[-n:]]

    def sessions_between(self, start: date, end: date) -> int:
        """Number of sessions in (start, end]."""
        cal = _calendar(self.code)
        if end <= start:
            return 0
        return len(cal.sessions_in_range(pd.Timestamp(start) + pd.Timedelta(days=1), pd.Timestamp(end)))

    def add_sessions(self, day: date, n: int) -> date:
        cal = _calendar(self.code)
        sessions = cal.sessions_in_range(pd.Timestamp(day) + pd.Timedelta(days=1),
                                         pd.Timestamp(day) + pd.Timedelta(days=n * 2 + 14))
        return sessions[n - 1].date()


def local_report_due(now: datetime, report_time_local: str, tz_name: str) -> tuple[date, datetime]:
    """Return (report_date, due_at_utc) for the report belonging to ``now``'s local date."""
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    hh, mm = (int(x) for x in report_time_local.split(":"))
    due_local = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return local.date(), due_local.astimezone(timezone.utc)
