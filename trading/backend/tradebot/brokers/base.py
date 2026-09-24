"""Broker abstraction.

The strategy, risk and execution engines only talk to this interface, so a broker can
be replaced without touching them. Errors are classified by *what the caller may
safely do next*:

* ``BrokerTimeout`` - the request may or may not have reached the broker. The order
  outcome is UNKNOWN and must be reconciled before any resubmission.
* ``BrokerRejected`` - the broker definitively refused (validation, funds, permissions).
* ``BrokerRateLimited`` / ``BrokerUnavailable`` - definitively not processed; retry later.
* ``BrokerAuthError`` - credentials invalid/expired; stop and alert.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..models import (CENT, D0, Account, Asset, BrokerOrder, CashFlow, MarketClock, OrderRequest,
                      Position, Side, round_up)


class BrokerError(Exception):
    pass


class BrokerTimeout(BrokerError):
    pass


class BrokerRejected(BrokerError):
    pass


class BrokerRateLimited(BrokerError):
    def __init__(self, msg: str = "rate limited", retry_after: float = 5.0):
        super().__init__(msg)
        self.retry_after = retry_after


class BrokerUnavailable(BrokerError):
    pass


class BrokerAuthError(BrokerError):
    pass


@dataclass(frozen=True)
class Capabilities:
    name: str
    is_paper: bool
    account_currency: str
    supports_fractional: bool
    supports_notional_orders: bool
    supports_client_order_id: bool          # idempotency key stored by the broker
    protective_stop_for_fractional: bool    # can a broker-held stop cover a fractional qty?
    protective_stop_tif: str                # "day" means it must be re-placed every session
    order_types: tuple[str, ...] = ("market",)
    fx_fee_bps: float = 0.0                 # charged by the broker on non-account-currency trades
    notes: tuple[str, ...] = field(default_factory=tuple)


class FeeModel:
    """Per-order transaction costs charged by the broker (not spread/slippage)."""

    def fee(self, side: Side, qty: Decimal, price: Decimal) -> Decimal:
        return D0


class USRegulatoryFeeModel(FeeModel):
    """SEC Section 31 fee + FINRA TAF on US equity sells, each rounded *up* to the cent.

    Rates as published in Alpaca's regulatory fee documentation (searched 2026-09-24).
    The per-trade cent rounding matters for tiny orders: a $5 sale pays at least $0.02
    (0.4%) even though the proportional fee is a fraction of a cent.
    """

    SEC_RATE = Decimal("0.0000206")
    TAF_PER_SHARE = Decimal("0.000195")
    TAF_MAX = Decimal("9.79")

    def fee(self, side: Side, qty: Decimal, price: Decimal) -> Decimal:
        if side != Side.SELL or qty <= 0:
            return D0
        sec = round_up(qty * price * self.SEC_RATE, CENT)
        taf = min(round_up(qty * self.TAF_PER_SHARE, CENT), self.TAF_MAX)
        return sec + taf


class Broker(ABC):
    capabilities: Capabilities
    fee_model: FeeModel = FeeModel()

    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def list_open_orders(self) -> list[BrokerOrder]: ...

    @abstractmethod
    def get_order(self, broker_order_id: str) -> BrokerOrder: ...

    @abstractmethod
    def find_order_by_client_id(self, client_order_id: str,
                                hint: OrderRequest | None = None,
                                since: datetime | None = None) -> BrokerOrder | None:
        """Look up an order by our idempotency key. Brokers without client ids must
        match on ``hint`` (symbol/side/qty) within ``since`` and be conservative."""

    @abstractmethod
    def submit_order(self, req: OrderRequest) -> BrokerOrder: ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> None: ...

    @abstractmethod
    def get_asset(self, symbol: str) -> Asset: ...

    @abstractmethod
    def get_clock(self) -> MarketClock | None:
        """Broker's market clock, or None if the broker does not provide one."""

    def get_cash_flows(self, since: datetime) -> list[CashFlow]:
        """Deposits/withdrawals since ``since``. Default: not available from this broker."""
        return []

    def prepare_account(self) -> list[str]:
        """Apply safety settings at the broker (e.g. disable shorting/margin). Returns notes."""
        return []
