"""Provider-neutral payment contracts. Credits never depend on provider payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from stitchforge_storage.accounts import AccountError

# This first release uses only currencies with two decimal places; no implicit FX conversion.
CURRENCIES = frozenset({"CNY", "USD", "EUR", "GBP", "HKD", "SGD", "AUD", "CAD"})


class PaymentError(AccountError):
    def __init__(self, message: str, code: str = "PAYMENT_UNAVAILABLE", status: int = 503):
        super().__init__(code, message, status)


@dataclass(frozen=True)
class PaymentReceipt:
    merchant_trade_no: str
    transaction_id: str
    amount_minor: int
    currency: str

    def __post_init__(self):
        if (
            not isinstance(self.merchant_trade_no, str)
            or not self.merchant_trade_no
            or not isinstance(self.transaction_id, str)
            or not self.transaction_id
            or type(self.amount_minor) is not int
            or self.amount_minor <= 0
            or self.currency not in CURRENCIES
        ):
            raise PaymentError("支付结果字段无效。", "INVALID_PAYMENT", 400)


class PaymentProvider(Protocol):
    id: str
    label: str
    currencies: frozenset[str]
    modes: tuple[str, ...]

    def checkout(self, order: dict, attempt: dict, mode: str, client_ip: str) -> dict: ...
    def query(self, order: dict, attempt: dict) -> PaymentReceipt | None: ...
    def notification(self, payload: bytes, headers: dict[str, str]) -> PaymentReceipt | None: ...
