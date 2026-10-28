"""The delivery lifecycle: DeliveryRequest, its Quote and its data holders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol

from .common import StateError, ValidationError, require_text
from .payment import (CashOnDelivery, Invoice, OnlinePayment, Payment,
                      PaymentMethod, PaymentStatus)

QUOTE_VALIDITY = timedelta(hours=24)        # A8 (assumed value)
MAX_PARCEL_WEIGHT_KG = 30.0                 # A3-1 (new assumption)
MAX_PARCELS_PER_REQUEST = 10


class DeliveryStatus(Enum):
    """States of a delivery request (SRS 8.1 statuses, refined in A2/A3)."""

    REQUESTED = "Requested"
    QUOTED = "Quoted"
    ACCEPTED = "Accepted"
    ALLOCATED = "Allocated"
    PICKED_UP = "Picked up"
    DELIVERED = "Delivered"
    DECLINED = "Declined"
    EXPIRED = "Expired"
    CANCELLED = "Cancelled"

    @property
    def is_closed(self) -> bool:
        """Return True for the terminal states."""
        return self in CLOSED_STATUSES


CLOSED_STATUSES = frozenset({
    DeliveryStatus.DELIVERED, DeliveryStatus.DECLINED,
    DeliveryStatus.EXPIRED, DeliveryStatus.CANCELLED,
})

ALLOWED_TRANSITIONS: dict[DeliveryStatus, frozenset[DeliveryStatus]] = {
    DeliveryStatus.REQUESTED: frozenset({
        DeliveryStatus.QUOTED, DeliveryStatus.DECLINED,
        DeliveryStatus.CANCELLED}),
    DeliveryStatus.QUOTED: frozenset({
        DeliveryStatus.ACCEPTED, DeliveryStatus.DECLINED,
        DeliveryStatus.EXPIRED, DeliveryStatus.CANCELLED}),
    DeliveryStatus.ACCEPTED: frozenset({
        DeliveryStatus.ALLOCATED, DeliveryStatus.CANCELLED}),
    # ALLOCATED -> ACCEPTED is the hand-back used when a driver leaves (F-4).
    DeliveryStatus.ALLOCATED: frozenset({
        DeliveryStatus.PICKED_UP, DeliveryStatus.ACCEPTED,
        DeliveryStatus.CANCELLED}),
    DeliveryStatus.PICKED_UP: frozenset({DeliveryStatus.DELIVERED}),
}


class ServiceLevel(Enum):
    """Service levels offered (A9)."""

    STANDARD = "Standard"
    EXPRESS = "Express"


class ParcelSize(Enum):
    """Coarse parcel size bands used for pricing."""

    SMALL = "S"
    MEDIUM = "M"
    LARGE = "L"


@dataclass(frozen=True)
class Address:
    """«Data-Holder» A street address in Victoria."""

    street: str
    suburb: str
    postcode: str
    state: str = "VIC"

    def __post_init__(self) -> None:
        require_text(self.street, "Street", 100)
        require_text(self.suburb, "Suburb", 60)
        if not (self.postcode.isdigit() and len(self.postcode) == 4):
            raise ValidationError("Postcode must be exactly 4 digits.")

    def __str__(self) -> str:
        return f"{self.street}, {self.suburb} {self.state} {self.postcode}"


@dataclass(frozen=True)
class Parcel:
    """«Data-Holder» One parcel within a request."""

    label: str
    description: str
    size: ParcelSize
    weight_kg: float

    def __post_init__(self) -> None:
        require_text(self.description, "Parcel description", 80)
        if not 0 < self.weight_kg <= MAX_PARCEL_WEIGHT_KG:
            raise ValidationError(
                f"Parcel weight must be more than 0 and at most "
                f"{MAX_PARCEL_WEIGHT_KG:g} kg (the most one driver can carry)."
            )


@dataclass(frozen=True)
class Recipient:
    """«Data-Holder» The person receiving the parcels (A5, F-1).

    Changed in Assignment 3 from a User subclass to a contact record: a
    recipient never logs in, so it has no credentials or permissions.
    """

    name: str
    phone: str


@dataclass(frozen=True)
class StatusChange:
    """«Data-Holder» One entry in a request's status history."""

    status: DeliveryStatus
    at: datetime
    by_user_id: str
    note: str = ""


@dataclass(frozen=True)
class CustodyRecord:
    """«Data-Holder» Who held one parcel from when (new in A3, F-2)."""

    parcel_label: str
    holder: str
    at: datetime
    event: str


@dataclass
class Quote:
    """A priced offer for one request (A2 CRC 4.10, refined in A3)."""

    suggested_fee: float
    fee: float
    adjustment_reason: str
    distance_km: float
    estimated_pickup: datetime
    estimated_delivery: datetime
    issued_at: datetime
    issued_by_user_id: str
    valid_until: datetime = field(init=False)

    def __post_init__(self) -> None:
        if self.fee <= 0:
            raise ValidationError("Fee must be more than $0.")
        if abs(self.fee - self.suggested_fee) >= 0.005 and \
                not self.adjustment_reason.strip():
            raise ValidationError(
                "A reason is required when the fee differs from the "
                "suggested fee (SRS 4.5)."
            )
        if self.estimated_delivery <= self.estimated_pickup:
            raise ValidationError("Delivery time must be after pickup time.")
        self.valid_until = self.issued_at + QUOTE_VALIDITY

    def is_valid_at(self, now: datetime) -> bool:
        """Return True while the quote may still be accepted."""
        return now <= self.valid_until


class DeliveryObserver(Protocol):
    """Anything that wants to hear about committed status changes."""

    def on_status_change(self, request: "DeliveryRequest",
                         change: StatusChange) -> None:
        """React to ``change`` on ``request``."""


class DeliveryRequest:
    """One delivery from request to completion (A2 CRC 4.1).

    Owns its parcels, quote, payment, allocation, status history and parcel
    custody, and enforces the order in which the delivery may progress.
    """

    def __init__(self, request_id: str, tracking_code: str, client_id: str,
                 created_by_id: str, pickup: Address, dropoff: Address,
                 recipient: Recipient, parcels: list[Parcel],
                 service_level: ServiceLevel, created_at: datetime) -> None:
        if not parcels:
            raise ValidationError("A request needs at least one parcel.")
        if len(parcels) > MAX_PARCELS_PER_REQUEST:
            raise ValidationError(
                f"At most {MAX_PARCELS_PER_REQUEST} parcels per request."
            )
        self.request_id = request_id
        self.tracking_code = tracking_code
        self.client_id = client_id
        self.created_by_id = created_by_id
        self.pickup = pickup
        self.dropoff = dropoff
        self.recipient = recipient
        self.parcels = list(parcels)
        self.service_level = service_level
        self.created_at = created_at
        self.quote: Quote | None = None
        self.payment: Payment | None = None
        self.assigned_staff_id: str | None = None
        self.history: list[StatusChange] = [
            StatusChange(DeliveryStatus.REQUESTED, created_at, created_by_id)
        ]
        self.custody: list[CustodyRecord] = []
        self._observers: list[DeliveryObserver] = []

    # -- observers (Observer pattern) --------------------------------------
    def attach(self, observer: DeliveryObserver) -> None:
        """Subscribe ``observer`` to future status changes."""
        if observer not in self._observers:
            self._observers.append(observer)

    def _change_status(self, new: DeliveryStatus, at: datetime,
                       by_user_id: str, note: str = "") -> StatusChange:
        allowed = ALLOWED_TRANSITIONS.get(self.status, frozenset())
        if new not in allowed:
            raise StateError(
                f"{self.request_id} is {self.status.value}; it cannot "
                f"become {new.value}."
            )
        change = StatusChange(new, at, by_user_id, note)
        self.history.append(change)
        for observer in self._observers:
            observer.on_status_change(self, change)
        return change

    # -- queries ------------------------------------------------------------
    @property
    def status(self) -> DeliveryStatus:
        """Return the current status (the latest history entry)."""
        return self.history[-1].status

    @property
    def total_weight_kg(self) -> float:
        """Return the combined weight of all parcels."""
        return sum(p.weight_kg for p in self.parcels)

    @property
    def largest_size(self) -> ParcelSize:
        """Return the largest parcel size band in the request."""
        order = [ParcelSize.SMALL, ParcelSize.MEDIUM, ParcelSize.LARGE]
        return max((p.size for p in self.parcels), key=order.index)

    def status_time(self, status: DeliveryStatus) -> datetime | None:
        """Return when the request last entered ``status``, if ever."""
        for change in reversed(self.history):
            if change.status is status:
                return change.at
        return None

    def current_holder(self, parcel_label: str) -> str:
        """Return who holds ``parcel_label`` now, derived from custody."""
        for record in reversed(self.custody):
            if record.parcel_label == parcel_label:
                return record.holder
        return f"Sender at {self.pickup.suburb}"

    def committed_period(self) -> tuple[datetime, datetime] | None:
        """Return the quoted pickup-to-delivery window, if quoted."""
        if self.quote is None:
            return None
        return self.quote.estimated_pickup, self.quote.estimated_delivery

    # -- quotation (SRS 4.5, 4.6) -----------------------------------------
    def issue_quote(self, quote: Quote) -> None:
        """Attach ``quote`` and move to Quoted."""
        if self.quote is not None:
            raise StateError("This request already has a quote (A7).")
        if quote.estimated_pickup < quote.issued_at:
            raise ValidationError("Pickup time cannot be in the past.")
        self.quote = quote
        self._change_status(DeliveryStatus.QUOTED, quote.issued_at,
                            quote.issued_by_user_id,
                            f"Quoted ${quote.fee:.2f}")

    def decline_request(self, reason: str, at: datetime,
                        by_user_id: str) -> None:
        """Close an unquotable request as Declined with a reason."""
        if self.status is not DeliveryStatus.REQUESTED:
            raise StateError("Only an unquoted request can be declined.")
        reason = require_text(reason, "Reason", 200)
        self._change_status(DeliveryStatus.DECLINED, at, by_user_id, reason)

    def accept_quote(self, method: PaymentMethod, at: datetime,
                     by_user_id: str) -> Payment:
        """Accept the quote, choose a payment method and create the payment."""
        self._require_open_quote(at)
        self._change_status(DeliveryStatus.ACCEPTED, at, by_user_id,
                            f"Payment by {method.value}")
        self.payment = self.create_payment(method, at)
        return self.payment

    def decline_quote(self, at: datetime, by_user_id: str) -> None:
        """Decline the quote; the request closes but stays retrievable."""
        self._require_open_quote(at)
        self._change_status(DeliveryStatus.DECLINED, at, by_user_id,
                            "Client declined the quote")

    def expire_if_lapsed(self, now: datetime) -> bool:
        """Close the request as Expired if its quote lapsed unanswered."""
        if self.status is DeliveryStatus.QUOTED and self.quote and \
                not self.quote.is_valid_at(now):
            self._change_status(DeliveryStatus.EXPIRED, self.quote.valid_until,
                                "system", "Quote not answered in 24 hours")
            return True
        return False

    def _require_open_quote(self, at: datetime) -> None:
        if self.status is not DeliveryStatus.QUOTED or self.quote is None:
            raise StateError(f"{self.request_id} has no open quote.")
        if not self.quote.is_valid_at(at):
            self.expire_if_lapsed(at)
            raise StateError("The quote has expired; please submit a new "
                             "request.")

    # -- payment (Factory Method) -------------------------------------------
    def create_payment(self, method: PaymentMethod, at: datetime) -> Payment:
        """Create the Payment subtype for ``method`` at the quoted amount."""
        assert self.quote is not None
        payment_class = {PaymentMethod.ONLINE: OnlinePayment,
                         PaymentMethod.CASH_ON_DELIVERY: CashOnDelivery}
        return payment_class[method](f"PAY-{self.request_id}",
                                     self.quote.fee, at)

    def change_payment_method(self, method: PaymentMethod,
                              at: datetime) -> Payment:
        """Switch an unpaid payment to ``method`` (A2 scenario 7.2)."""
        if self.payment is None or self.status.is_closed:
            raise StateError("There is no open payment to change.")
        if self.payment.status is not PaymentStatus.DUE:
            raise StateError("A settled payment cannot be changed.")
        if self.payment.method is not method:
            self.payment = self.create_payment(method, at)
        return self.payment

    def invoice(self, bill_to: str, at: datetime) -> Invoice:
        """Return the invoice for this delivery's payment (A2 CRC 4.15)."""
        if self.payment is None or self.quote is None:
            raise StateError(f"{self.request_id} has no payment to invoice "
                             "yet; the quote must be accepted first.")
        quote = self.quote
        count = len(self.parcels)
        lines = [(f"{self.service_level.value} delivery, "
                  f"{self.pickup.suburb} to {self.dropoff.suburb}, "
                  f"{count} parcel{'s' if count > 1 else ''}, "
                  f"{quote.distance_km} km", quote.suggested_fee)]
        if quote.adjustment_reason:
            lines.append((f"Fee adjustment: {quote.adjustment_reason}",
                          round(quote.fee - quote.suggested_fee, 2)))
        return self.payment.issue_invoice(self.request_id, bill_to, lines,
                                          at)

    # -- allocation (SRS 4.7) -----------------------------------------------
    def allocate(self, staff_id: str, at: datetime, by_user_id: str,
                 staff_name: str) -> None:
        """Record the allocated driver and move to Allocated."""
        if self.status is not DeliveryStatus.ACCEPTED:
            raise StateError("Only an accepted request can be allocated.")
        self.assigned_staff_id = staff_id
        self._change_status(DeliveryStatus.ALLOCATED, at, by_user_id,
                            f"Allocated to {staff_name}")

    def release_allocation(self, at: datetime, by_user_id: str,
                           reason: str) -> None:
        """Return an allocated, not yet collected request to Accepted."""
        if self.status is not DeliveryStatus.ALLOCATED:
            raise StateError("Only an allocated request can be released.")
        self.assigned_staff_id = None
        self._change_status(DeliveryStatus.ACCEPTED, at, by_user_id, reason)

    # -- pickup and hand-over (F-2) -----------------------------------------
    def record_pickup(self, staff_id: str, staff_name: str,
                      at: datetime) -> None:
        """Record that the allocated driver has collected every parcel."""
        self._require_assigned(staff_id)
        self._change_status(DeliveryStatus.PICKED_UP, at, staff_id,
                            f"Collected by {staff_name}")
        for parcel in self.parcels:
            self.custody.append(CustodyRecord(parcel.label, staff_name, at,
                                              "Picked up"))

    def record_handover(self, staff_id: str, received_by: str,
                        at: datetime) -> None:
        """Record hand-over of every parcel and complete the delivery.

        A cash-on-delivery payment must have been collected first, so a
        parcel is never released unpaid.
        """
        self._require_assigned(staff_id)
        received_by = require_text(received_by, "Received by", 80)
        if self.payment is None or \
                self.payment.status is not PaymentStatus.PAID:
            raise StateError("Payment must be collected before hand-over.")
        for parcel in self.parcels:
            self.custody.append(CustodyRecord(parcel.label, received_by, at,
                                              "Handed over"))
        self._change_status(DeliveryStatus.DELIVERED, at, staff_id,
                            f"Received by {received_by}")

    def _require_assigned(self, staff_id: str) -> None:
        if staff_id != self.assigned_staff_id:
            raise StateError("Only the allocated driver can record this.")

    # -- cancellation --------------------------------------------------------
    def cancel(self, at: datetime, by_user_id: str, reason: str) -> None:
        """Cancel the request before pickup; it stays retrievable (A14)."""
        if self.status in (DeliveryStatus.PICKED_UP,) or self.status.is_closed:
            raise StateError(
                f"A {self.status.value.lower()} request cannot be cancelled."
            )
        if self.payment is not None:
            self.payment.cancel(at)
        self.assigned_staff_id = None
        self._change_status(DeliveryStatus.CANCELLED, at, by_user_id,
                            require_text(reason, "Reason", 200))
