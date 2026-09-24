"""Strategies propose entries and exits from completed daily bars.

The *same* classes are used by the backtester and the live engine, so a validated
rule is exactly the rule that trades. Strategies never size, never place orders and
never see account data: that is the risk and execution engines' job.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    """Wilder's RSI."""
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(100.0)


@dataclass
class Strategy:
    name: str = "base"
    version: str = "1"
    params: dict[str, Any] = field(default_factory=dict)
    holds_overnight: bool = True
    stop_pct: float | None = None
    max_hold_days: int | None = None
    history_needed: int = 1

    # Rules are written against precomputed indicator columns so the backtester can
    # evaluate them for every bar cheaply; ``entry``/``exit`` (used live) run the very
    # same functions on the last completed bar.
    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def entry_at(self, pre: pd.DataFrame, i: int) -> dict[str, Any] | None:
        raise NotImplementedError

    def exit_at(self, pre: pd.DataFrame, i: int, bars_held: int) -> str | None:
        raise NotImplementedError

    def entry(self, df: pd.DataFrame) -> dict[str, Any] | None:
        """Evidence dict if the entry rule is true on the last completed bar, else None."""
        return self.entry_at(self.precompute(df), len(df) - 1)

    def exit(self, df: pd.DataFrame, bars_held: int) -> str | None:
        """Exit reason if the exit rule is true on the last completed bar, else None."""
        return self.exit_at(self.precompute(df), len(df) - 1, bars_held)

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "params": self.params,
                "stop_pct": self.stop_pct, "max_hold_days": self.max_hold_days,
                "holds_overnight": self.holds_overnight}


class TrendSMA(Strategy):
    """Long while the close is above its n-day average; flat otherwise.

    Hypothesised edge: time-series momentum / avoiding prolonged bear markets. It
    usually lags turning points and whipsaws in sideways markets.
    """

    def __init__(self, n: int = 200, stop_pct: float = 0.10):
        super().__init__(name="trend_sma", version=f"1-n{n}", params={"n": n},
                         stop_pct=stop_pct, history_needed=n + 5)

    def precompute(self, df):
        return pd.DataFrame({"close": df.close, "sma": sma(df.close, self.params["n"])})

    def entry_at(self, pre, i):
        c, a = pre.close.iat[i], pre.sma.iat[i]
        if pd.notna(a) and c > a:
            return {"close": round(float(c), 4), f"sma{self.params['n']}": round(float(a), 4),
                    "rule": f"close > SMA{self.params['n']}"}
        return None

    def exit_at(self, pre, i, bars_held):
        a = pre.sma.iat[i]
        if pd.notna(a) and pre.close.iat[i] < a:
            return f"close below SMA{self.params['n']}"
        return None


class RSI2MeanReversion(Strategy):
    """Buy short-term oversold dips inside a long-term uptrend; exit on the bounce.

    Hypothesised edge: short-horizon overreaction / liquidity provision in broad index
    ETFs (Connors-style). Known to have weakened after publication; fails in crashes,
    when "oversold" keeps getting more oversold - hence the stop and time limit.
    """

    def __init__(self, entry_th: float = 10, trend_n: int = 200, exit_n: int = 5,
                 max_hold: int = 7, stop_pct: float = 0.06):
        super().__init__(name="rsi2_mr", version=f"1-e{entry_th:g}-h{max_hold}",
                         params={"rsi_n": 2, "entry_th": entry_th, "trend_n": trend_n, "exit_n": exit_n},
                         stop_pct=stop_pct, max_hold_days=max_hold, history_needed=trend_n + 5)

    def precompute(self, df):
        p = self.params
        return pd.DataFrame({"close": df.close, "rsi": rsi(df.close, p["rsi_n"]),
                             "trend": sma(df.close, p["trend_n"]), "exit_sma": sma(df.close, p["exit_n"])})

    def entry_at(self, pre, i):
        p = self.params
        r, t, c = pre.rsi.iat[i], pre.trend.iat[i], pre.close.iat[i]
        if pd.notna(t) and c > t and r < p["entry_th"]:
            return {"rsi2": round(float(r), 2), "close": round(float(c), 4),
                    f"sma{p['trend_n']}": round(float(t), 4),
                    "rule": f"RSI(2) < {p['entry_th']:g} and close > SMA{p['trend_n']}"}
        return None

    def exit_at(self, pre, i, bars_held):
        m = pre.exit_sma.iat[i]
        if pd.notna(m) and pre.close.iat[i] > m:
            return f"close above SMA{self.params['exit_n']} (bounce taken)"
        if self.max_hold_days and bars_held >= self.max_hold_days:
            return f"time stop after {bars_held} sessions"
        return None


class BuyAndHold(Strategy):
    """Baseline: always invested."""

    def __init__(self):
        super().__init__(name="buy_hold", version="1", history_needed=1)

    def entry_at(self, pre, i):
        return {"rule": "always invested (baseline)"}

    def exit_at(self, pre, i, bars_held):
        return None


def build(name: str, params: dict[str, Any] | None = None) -> Strategy:
    params = params or {}
    return {"trend_sma": TrendSMA, "rsi2_mr": RSI2MeanReversion, "buy_hold": BuyAndHold}[name](**params)


def bars_to_frame(bars) -> pd.DataFrame:
    return pd.DataFrame([{"day": b.day, "open": b.open, "high": b.high, "low": b.low,
                          "close": b.close, "volume": b.volume} for b in bars]).set_index("day")
