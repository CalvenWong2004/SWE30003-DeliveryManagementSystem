"""Payments: the abstract Payment, its two subtypes and the gateway adapter.

Real card processing is out of scope for Assignment 3, so the gateway is a
simulation that returns a transaction reference and never sees card data.
Each payment can also issue an invoice (A2 CRC 4.15) and office staff can
record a payment settled outside the DMS (A2 CRC 4.13).
"""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .common import StateError, require_text

SIMULATED_CARD_TYPES = ("Visa", "Mastercard", "American Express")


class PaymentMethod(Enum):
    """How the client pays (A11)."""

    ONLINE = "Online card"
    CASH_ON_DELIVERY = "Cash on delivery"


class PaymentStatus(Enum):
    """Progress of a payment (SRS 6.0: due, paid, cancelled)."""

    DUE = "Due"
    PAID = "Paid"
    CANCELLED = "Cancelled"


class PaymentDeclined(Exception):
    """Raised by a gateway when the provider refuses a charge."""


@dataclass(frozen=True)
class ChargeResult:
    """«Data-Holder» What the card provider reports for a charge.

    The provider tells the DMS the card type, so the DMS can record it
    (A2 CRC 4.16) without ever seeing the card number (A11).
    """

    reference: str
    card_type: str


@dataclass(frozen=True)
class Invoice:
    """«Data-Holder» An invoice for one delivery (A2 CRC 4.15)."""

    invoice_number: str
    issued_at: datetime
    request_id: str
    bill_to: str
    lines: tuple[tuple[str, float], ...]
    total: float
    method: str
    status: str
    reference: str
    card_type: str
    paid_at: datetime | None


class PaymentGateway(ABC):
    """«Adapter» target interface for an external card provider."""

    @abstractmethod
    def charge(self, payment_id: str, amount: float) -> ChargeResult:
        """Charge ``amount``; return the provider's reference and card."""


class SimulatedPaymentGateway(PaymentGateway):
    """Stand-in adapter used because no banking system is available.

    ``decline_next`` makes the next charge fail once, so the declined-card
    scenario (A2 section 7.2) can be shown in a demonstration.
    """

    def __init__(self, decline_all: bool = False) -> None:
        self.decline_all = decline_all
        self.decline_next = False

    def charge(self, payment_id: str, amount: float) -> ChargeResult:
        if self.decline_all or self.decline_next:
            self.decline_next = False
            raise PaymentDeclined("The card provider declined the payment.")
        return ChargeResult("SIM-" + secrets.token_hex(4).upper(),
                            secrets.choice(SIMULATED_CARD_TYPES))


class Payment(ABC):
    """Abstract payment for one delivery, always for the quoted amount."""

    method: PaymentMethod

    def __init__(self, payment_id: str, amount: float,
                 created_at: datetime) -> None:
        self.payment_id = payment_id
        self.amount = round(amount, 2)
        self.created_at = created_at
        self.status = PaymentStatus.DUE
        self.paid_at: datetime | None = None
        self.reference = ""
        self.card_type = ""
        self.recorded_by = ""
        self.note = ""

    @property
    def invoice_number(self) -> str:
        """Return the invoice number (one payment per request, A11)."""
        return self.payment_id.replace("PAY-", "INV-", 1)

    def _mark_paid(self, at: datetime, reference: str) -> None:
        if self.status is not PaymentStatus.DUE:
            raise StateError(f"Payment is already {self.status.value}.")
        self.status = PaymentStatus.PAID
        self.paid_at = at
        self.reference = reference

    def record_manual(self, at: datetime, by_user_id: str,
                      note: str) -> None:
        """Office staff record a payment settled outside the DMS.

        For example a client who paid at the office, or a card payment the
        provider confirmed after an outage. A note is required so the change
        can be audited (A2 CRC 4.13 responsibility 6).
        """
        note = require_text(note, "Note", 200)
        self._mark_paid(at, f"MANUAL-{by_user_id}")
        self.recorded_by = by_user_id
        self.note = note

    def issue_invoice(self, request_id: str, bill_to: str,
                      lines: list[tuple[str, float]],
                      issued_at: datetime) -> Invoice:
        """Return an invoice for this payment (A2 CRC 4.15 resp. 2)."""
        return Invoice(self.invoice_number, issued_at, request_id, bill_to,
                       tuple(lines), self.amount, self.method.value,
                       self.status.value, self.reference, self.card_type,
                       self.paid_at)

    def cancel(self, at: datetime) -> None:
        """Cancel an unpaid payment; a paid one would need a refund (A3-6)."""
        if self.status is PaymentStatus.DUE:
            self.status = PaymentStatus.CANCELLED

    @property
    @abstractmethod
    def collected_by_driver(self) -> bool:
        """Return True if the driver collects this payment at hand-over."""


class OnlinePayment(Payment):
    """Card payment settled through the PaymentGateway adapter."""

    method = PaymentMethod.ONLINE

    @property
    def collected_by_driver(self) -> bool:
        """An online payment is settled before pickup, not by the driver."""
        return False

    def settle(self, gateway: PaymentGateway, at: datetime) -> str:
        """Charge through ``gateway``; status stays Due if declined."""
        if self.status is not PaymentStatus.DUE:
            raise StateError(f"Payment is already {self.status.value}.")
        result = gateway.charge(self.payment_id, self.amount)
        self._mark_paid(at, result.reference)
        self.card_type = result.card_type
        return result.reference


class CashOnDelivery(Payment):
    """Cash collected in person by the allocated driver at drop-off."""

    method = PaymentMethod.CASH_ON_DELIVERY

    @property
    def collected_by_driver(self) -> bool:
        """The allocated driver collects cash at drop-off."""
        return True

    def record_collection(self, amount: float, staff_id: str,
                          at: datetime) -> None:
        """Record cash received; it must equal the quoted amount."""
        if round(amount, 2) != self.amount:
            raise StateError(
                f"Cash collected (${amount:.2f}) must equal the quoted "
                f"amount (${self.amount:.2f})."
            )
        self._mark_paid(at, f"CASH-{staff_id}")
