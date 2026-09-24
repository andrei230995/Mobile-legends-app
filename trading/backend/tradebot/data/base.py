"""Market-data abstraction. Every datum carries provenance (source, feed, timestamps)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import Bar, NewsItem, Quote


class DataUnavailable(Exception):
    pass


class MarketData(ABC):
    name: str = "unknown"
    is_replay: bool = False       # replayed history, never to be presented as live data

    @abstractmethod
    def latest_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    def daily_bars(self, symbol: str, n: int, now: datetime) -> list[Bar]:
        """The last ``n`` *completed* regular sessions as of ``now`` (oldest first).
        A session counts only once its close has passed - no look-ahead."""

    def news(self, symbols: list[str], since: datetime) -> list[NewsItem]:
        return []
