"""DeliveryRegistry: creates, finds and reports on requests (CRC 4.9)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

from ..domain.common import (Clock, IdGenerator, IntegrityError, NotFound,
                             ValidationError)
from ..domain.delivery import (Address, DeliveryRequest, DeliveryStatus,
                               Parcel, Recipient, ServiceLevel)
from ..domain.pricing import PricingStrategy
from ..domain.users import DeliveryStaff
from ..persistence import mappers
from ..persistence.json_store import JsonStore
from ..services.notification import NotificationService
from ..services.service_area import ServiceArea
from .staff_roster import Commitment, StaffRoster
from .user_directory import UserDirectory

COLLECTION = "requests"
TRACKING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class Period(Enum):
    """Reporting periods required by SRS 4.2."""

    DAY = "Day"
    WEEK = "Week"
    MONTH = "Month"
    YEAR = "Year"
    YEAR_TO_DATE = "Year to date"

    def bounds(self, reference: date) -> tuple[date, date]:
        """Return the inclusive first and last day of the period."""
        if self is Period.DAY:
            return reference, reference
        if self is Period.WEEK:
            start = reference - timedelta(days=reference.weekday())
            return start, start + timedelta(days=6)
        if self is Period.MONTH:
            start = reference.replace(day=1)
            following = (start + timedelta(days=32)).replace(day=1)
            return start, following - timedelta(days=1)
        if self is Period.YEAR:
            return reference.replace(month=1, day=1), \
                reference.replace(month=12, day=31)
        return reference.replace(month=1, day=1), reference


@dataclass(frozen=True)
class Statistics:
    """Counts for one reporting period (SRS 4.2, A15)."""

    first_day: date
    last_day: date
    delivered: int
    declined: int
    expired: int
    cancelled: int
    revenue: float


@dataclass(frozen=True)
class QuoteSuggestion:
    """What the system proposes before office staff issue a quote."""

    distance_km: float
    suggested_fee: float
    window: timedelta
    committed_deliveries: int
    free_staff: int


class DeliveryRegistry:
    """Owns the collection of DeliveryRequest objects."""

    def __init__(self, store: JsonStore, directory: UserDirectory,
                 roster: StaffRoster, service_area: ServiceArea,
                 pricing: dict[ServiceLevel, PricingStrategy],
                 notifier: NotificationService, clock: Clock) -> None:
        self._store = store
        self._directory = directory
        self._roster = roster
        self._area = service_area
        self._pricing = pricing
        self._notifier = notifier
        self._clock = clock
        self._requests: dict[str, DeliveryRequest] = {}
        self._ids = IdGenerator("DR")

    # -- bootstrap ------------------------------------------------------------
    def load(self) -> None:
        """Restore requests and check their references (bootstrap step 6)."""
        for record in self._store.load(COLLECTION):
            request = mappers.request_from_dict(record)
            self._directory.find(request.client_id)
            if request.assigned_staff_id:
                self._directory.find(request.assigned_staff_id)
            self._requests[request.request_id] = request
        self._ids.reseed(list(self._requests))

    def verify_integrity(self) -> None:
        """Check restored allocations, payments and custody (bootstrap 7).

        Raises ``IntegrityError`` naming the record, so a damaged data file
        stops startup instead of being silently repaired (A2 section 6.3).
        """
        active = (DeliveryStatus.ALLOCATED, DeliveryStatus.PICKED_UP)
        for request in self._requests.values():
            rid = request.request_id
            if request.status in active:
                if not request.assigned_staff_id:
                    raise IntegrityError(f"{rid} is {request.status.value} "
                                         "but has no driver.")
                if not isinstance(self._directory.find(
                        request.assigned_staff_id), DeliveryStaff):
                    raise IntegrityError(f"{rid} is allocated to "
                                         f"{request.assigned_staff_id}, who "
                                         "is not delivery staff.")
            if request.payment is not None and (
                    request.quote is None or
                    request.payment.amount != round(request.quote.fee, 2)):
                raise IntegrityError(f"{rid} has a payment that does not "
                                     "match its quote.")
            if request.status is DeliveryStatus.PICKED_UP:
                for parcel in request.parcels:
                    events = [c.event for c in request.custody
                              if c.parcel_label == parcel.label]
                    if not events or events[-1] != "Picked up":
                        raise IntegrityError(
                            f"{rid} parcel {parcel.label} has no pickup "
                            "custody record.")
        for staff in self._directory.delivery_staff():
            booked = sorted(self.commitments_for(staff.user_id))
            for (_, end, first), (start, _, second) in zip(booked,
                                                           booked[1:]):
                if start < end:
                    raise IntegrityError(
                        f"{staff.name} is double-booked on {first} and "
                        f"{second}.")

    def attach_observers(self) -> None:
        """Subscribe notifications only after history is rebuilt."""
        for request in self._requests.values():
            request.attach(self._notifier)

    def save(self) -> None:
        """Write every request back to storage."""
        self._store.save(COLLECTION, [mappers.request_to_dict(r)
                                      for r in self._requests.values()])

    # -- creation (SRS 4.3) ---------------------------------------------------
    def create_request(self, client_id: str, created_by_id: str,
                       pickup: Address, dropoff: Address,
                       recipient: Recipient, parcels: list[Parcel],
                       service_level: ServiceLevel) -> DeliveryRequest:
        """Validate addresses first, then create and register a request.

        Changed in A3: an out-of-area address is refused at entry (SRS 10.3
        for 4.3), so no Declined record is created for a typing mistake.
        """
        self._area.validate(pickup, "Pickup")
        self._area.validate(dropoff, "Drop-off")
        if pickup == dropoff:
            raise ValidationError("Pickup and drop-off cannot be the same.")
        request = DeliveryRequest(
            self._ids.next_id(), self._new_tracking_code(), client_id,
            created_by_id, pickup, dropoff, recipient, parcels,
            service_level, self._clock.now())
        request.attach(self._notifier)
        self._requests[request.request_id] = request
        self._notifier.notify_new_request(request)
        return request

    def _new_tracking_code(self) -> str:
        existing = {r.tracking_code for r in self._requests.values()}
        while True:
            code = "".join(secrets.choice(TRACKING_ALPHABET)
                           for _ in range(8))
            if code not in existing:
                return code

    # -- quotation support (SRS 4.5) -----------------------------------------
    def suggest_quote(self, request: DeliveryRequest,
                      pickup_time: datetime) -> QuoteSuggestion:
        """Price a request and show committed work for the window."""
        strategy = self._pricing[request.service_level]
        distance = self._area.distance_km(request.pickup, request.dropoff)
        window = strategy.delivery_window(distance)
        end = pickup_time + window
        return QuoteSuggestion(
            distance, strategy.suggest_fee(request, distance), window,
            self._roster.committed_count(pickup_time, end),
            self._roster.free_staff_count(pickup_time, end))

    def expire_lapsed_quotes(self) -> list[DeliveryRequest]:
        """Close every quote left unanswered past its validity (A8)."""
        now = self._clock.now()
        return [r for r in self._requests.values() if r.expire_if_lapsed(now)]

    # -- queries --------------------------------------------------------------
    def find(self, request_id: str) -> DeliveryRequest:
        """Return a request by id or raise ``NotFound``."""
        try:
            return self._requests[request_id.strip().upper()]
        except KeyError as exc:
            raise NotFound(f"No delivery {request_id.strip()} exists.") \
                from exc

    def find_by_tracking(self, code: str) -> DeliveryRequest:
        """Return the request for a recipient's tracking code (F-1)."""
        code = code.strip().upper()
        for request in self._requests.values():
            if request.tracking_code == code:
                return request
        raise NotFound("No delivery matches that tracking code.")

    def search(self, text: str) -> list[DeliveryRequest]:
        """Match id, client name or phone, or either address (SRS 4.1)."""
        needle = text.strip().lower()
        if not needle:
            raise ValidationError("Enter something to search for.")
        results = []
        for request in self._requests.values():
            client = self._directory.find(request.client_id)
            haystack = " ".join([
                request.request_id, client.name, client.phone,
                str(request.pickup), str(request.dropoff),
                request.recipient.name, request.recipient.phone]).lower()
            if needle in haystack:
                results.append(request)
        return sorted(results, key=lambda r: r.request_id)

    def all(self) -> list[DeliveryRequest]:
        """Return every request in id order."""
        return sorted(self._requests.values(), key=lambda r: r.request_id)

    def with_status(self, status: DeliveryStatus) -> list[DeliveryRequest]:
        """Return requests currently in ``status``."""
        return [r for r in self.all() if r.status is status]

    def for_client(self, client_id: str) -> list[DeliveryRequest]:
        """Return a client's own requests."""
        return [r for r in self.all() if r.client_id == client_id]

    def for_staff(self, staff_id: str) -> list[DeliveryRequest]:
        """Return open requests allocated to ``staff_id``."""
        return [r for r in self.all() if r.assigned_staff_id == staff_id
                and not r.status.is_closed]

    def commitments_for(self, staff_id: str) -> list[Commitment]:
        """Return the quoted windows of a driver's open allocations."""
        commitments = []
        for request in self.for_staff(staff_id):
            period = request.committed_period()
            if period is not None:
                commitments.append((period[0], period[1],
                                    request.request_id))
        return commitments

    # -- statistics (SRS 4.2) -------------------------------------------------
    def statistics(self, period: Period, reference: date,
                   staff_id: str | None = None) -> Statistics:
        """Count outcomes whose closing date falls in the period."""
        first, last = period.bounds(reference)
        counts = {status: 0 for status in (
            DeliveryStatus.DELIVERED, DeliveryStatus.DECLINED,
            DeliveryStatus.EXPIRED, DeliveryStatus.CANCELLED)}
        revenue = 0.0
        for request in self._requests.values():
            if request.status not in counts:
                continue
            closed_at = request.status_time(request.status)
            if closed_at is None or not first <= closed_at.date() <= last:
                continue
            if staff_id is not None and (
                    request.status is not DeliveryStatus.DELIVERED
                    or request.assigned_staff_id != staff_id):
                continue
            counts[request.status] += 1
            if request.status is DeliveryStatus.DELIVERED and request.payment:
                revenue += request.payment.amount
        return Statistics(first, last, counts[DeliveryStatus.DELIVERED],
                          counts[DeliveryStatus.DECLINED],
                          counts[DeliveryStatus.EXPIRED],
                          counts[DeliveryStatus.CANCELLED], round(revenue, 2))
