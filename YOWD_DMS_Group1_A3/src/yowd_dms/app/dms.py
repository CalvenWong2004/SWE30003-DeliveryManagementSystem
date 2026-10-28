"""DeliveryManagementSystem: the single entry point (Singleton + Facade).

The UI calls only this class. It checks the session's permissions and then
delegates the business work to the object that owns the information, so it
holds no delivery, pricing or allocation rules of its own.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from ..domain.common import (Clock, NotFound, PermissionDenied, StateError,
                             ValidationError, format_datetime)
from ..domain.delivery import (Address, DeliveryRequest, DeliveryStatus,
                               Parcel, Quote, Recipient, ServiceLevel)
from ..domain.payment import (CashOnDelivery, Invoice, OnlinePayment,
                              PaymentGateway, PaymentMethod, PaymentStatus,
                              SimulatedPaymentGateway)
from ..domain.pricing import ExpressPricing, StandardPricing
from ..domain.staff import StaffAvailability
from ..domain.users import (BusinessOwner, Client, DeliveryStaff, Operation,
                            User, validate_phone)
from ..persistence.json_store import JsonStore
from ..registry.delivery_registry import (DeliveryRegistry, Period,
                                          QuoteSuggestion, Statistics)
from ..registry.staff_roster import StaffRoster
from ..registry.user_directory import UserDirectory
from ..services.notification import NotificationService
from ..services.service_area import ServiceArea


class DeliveryManagementSystem:
    """«Singleton» «Facade» Starts the system and fronts every use case."""

    _instance: DeliveryManagementSystem | None = None

    @classmethod
    def start(cls, data_dir: Path, clock: Clock | None = None,
              gateway: PaymentGateway | None = None
              ) -> DeliveryManagementSystem:
        """Create and bootstrap the one instance (or return it)."""
        if cls._instance is None:
            cls._instance = cls(data_dir, clock or Clock(),
                                gateway or SimulatedPaymentGateway())
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Discard the instance (tests and demo resets only)."""
        cls._instance = None

    def __init__(self, data_dir: Path, clock: Clock,
                 gateway: PaymentGateway) -> None:
        if DeliveryManagementSystem._instance is not None:
            raise RuntimeError("Use DeliveryManagementSystem.start().")
        self.clock = clock
        self._session: User | None = None
        # Bootstrap, in the order justified in report section 1.4.
        store = JsonStore(data_dir)                                # 0
        self._area = ServiceArea()                                 # 1
        pricing = {ServiceLevel.STANDARD: StandardPricing(),       # 2
                   ServiceLevel.EXPRESS: ExpressPricing()}
        self._gateway = gateway                                    # 3
        self._directory = UserDirectory(store, clock)              # 4
        self._directory.load()
        self._notifier = NotificationService(                      # 3b
            store.outbox_path, self._directory.email_of)
        self._roster = StaffRoster(store, self._directory)         # 5
        self._roster.load()
        self._registry = DeliveryRegistry(                         # 6
            store, self._directory, self._roster, self._area, pricing,
            self._notifier, clock)
        self._registry.load()
        self._roster.set_commitment_source(                        # 7
            self._registry.commitments_for)
        self._registry.verify_integrity()
        self._registry.attach_observers()
        self._registry.expire_lapsed_quotes()                      # 8
        self._save()

    # -- infrastructure -------------------------------------------------------
    def _save(self) -> None:
        self._directory.save()
        self._roster.save()
        self._registry.save()

    def _tick(self) -> None:
        """Run time-based housekeeping before each use case (A8)."""
        if self._registry.expire_lapsed_quotes():
            self._registry.save()

    def _require(self, operation: Operation) -> User:
        if self._session is None:
            raise PermissionDenied("Please log in first.")
        if not self._session.can(operation):
            raise PermissionDenied(
                f"A {self._session.role_name} cannot {operation.value}.")
        self._tick()
        return self._session

    @property
    def current_user(self) -> User | None:
        """Return the logged-in user, if any."""
        return self._session

    @property
    def notifications(self):
        """Return messages sent during this run (for the demo screens)."""
        return self._notifier.sent

    # -- accounts -------------------------------------------------------------
    def needs_owner_setup(self) -> bool:
        """Return True on a new installation with no business owner."""
        return not self._directory.has_owner()

    def setup_owner(self, name: str, email: str, phone: str,
                    password: str) -> User:
        """Create the first owner account on a new installation."""
        if not self.needs_owner_setup():
            raise PermissionDenied("An owner account already exists.")
        user = self._directory.create_user("Business Owner", name, email,
                                           phone, password)
        self._save()
        return user

    def register_client(self, name: str, email: str, phone: str,
                        password: str, default_address: str = "") -> User:
        """Let a member of the public create a client account."""
        user = self._directory.create_user("Client", name, email, phone,
                                           password,
                                           default_address=default_address)
        self._save()
        return user

    def login(self, email: str, password: str) -> User:
        """Start a session for valid credentials."""
        self._session = self._directory.authenticate(email, password)
        self._tick()
        return self._session

    def logout(self) -> None:
        """End the current session."""
        self._session = None

    # -- own details (A2 CRC 4.11 resp. 1; every role) ------------------------
    def _logged_in(self) -> User:
        if self._session is None:
            raise PermissionDenied("Please log in first.")
        self._tick()
        return self._session

    def update_my_details(self, name: str | None = None,
                          phone: str | None = None,
                          default_address: str | None = None) -> User:
        """Let any logged-in user correct their own contact details."""
        user = self._logged_in()
        self._directory.update_details(user, name, phone, default_address)
        self._save()
        return user

    def change_my_password(self, current: str, new: str) -> None:
        """Change the logged-in user's password."""
        self._directory.change_password(self._logged_in(), current, new)
        self._save()

    # -- client records (A2 CRC 4.8, 4.13 resp. 4) ----------------------------
    def client_records(self, text: str = "") -> list[Client]:
        """Return clients matching ``text`` (all when blank)."""
        self._require(Operation.MANAGE_CLIENTS)
        needle = text.strip().lower()
        return [c for c in self._directory.clients() if needle in
                f"{c.user_id} {c.name} {c.email} {c.phone}".lower()]

    def find_user(self, user_id: str) -> User:
        """Return any account by ID, for staff maintenance screens."""
        self._require(Operation.MANAGE_CLIENTS)
        return self._directory.find(user_id.strip().upper())

    def update_user_details(self, user_id: str, name: str | None = None,
                            phone: str | None = None,
                            default_address: str | None = None) -> User:
        """Office staff update a client; the owner may also update staff."""
        actor = self._require(Operation.MANAGE_CLIENTS)
        user = self._directory.find(user_id.strip().upper())
        if not isinstance(user, Client):
            if not actor.can(Operation.MANAGE_STAFF):
                raise PermissionDenied("Only the business owner can update "
                                       "staff records.")
            if isinstance(user, BusinessOwner) and user is not actor:
                raise PermissionDenied("Owners update their own details "
                                       "from 'My details'.")
        self._directory.update_details(user, name, phone, default_address)
        self._save()
        return user

    def deactivate_client(self, user_id: str) -> None:
        """Deactivate a client with no open deliveries (A14: not deleted)."""
        self._require(Operation.MANAGE_CLIENTS)
        user = self._directory.find(user_id.strip().upper())
        if not isinstance(user, Client):
            raise ValidationError(f"{user.user_id} is not a client.")
        if not user.is_active:
            raise StateError(f"{user.name} is already deactivated.")
        open_ids = [r.request_id for r in
                    self._registry.for_client(user.user_id)
                    if not r.status.is_closed]
        if open_ids:
            raise StateError(f"{user.name} still has open deliveries "
                             f"({', '.join(open_ids)}); close or cancel "
                             "them first.")
        user.deactivate()
        self._save()

    # -- staff records (owner, F-4) -------------------------------------------
    def add_staff(self, role_name: str, name: str, email: str, phone: str,
                  password: str) -> User:
        """Add an office or delivery staff member who joins the business."""
        self._require(Operation.MANAGE_STAFF)
        if role_name not in ("Office Staff", "Delivery Staff"):
            raise ValidationError("Role must be Office Staff or Delivery "
                                  "Staff.")
        user = self._directory.create_user(role_name, name, email, phone,
                                           password)
        self._save()
        return user

    def staff_list(self) -> list[User]:
        """Return internal staff accounts for the owner."""
        self._require(Operation.MANAGE_STAFF)
        return self._directory.internal_staff()

    def staff_cover_gaps(self, user_id: str) -> list[tuple[datetime,
                                                           datetime]]:
        """Return future times with no driver if ``user_id`` left."""
        self._require(Operation.MANAGE_STAFF)
        user = self._directory.find(user_id.strip().upper())
        if not isinstance(user, DeliveryStaff):
            return []
        return self._roster.gaps_if_staff_leaves(user.user_id,
                                                 self.clock.now())

    def deactivate_staff(self, user_id: str,
                         allow_no_cover: bool = False) -> list[str]:
        """Deactivate a leaver; return requests handed back for allocation.

        Refused while a driver still holds parcels, and, unless
        ``allow_no_cover`` confirms it, when no other driver would be
        available for some of the leaver's future periods (CRC 4.7 #3).
        Allocated work not yet collected returns to Accepted so it can be
        re-allocated.
        """
        actor = self._require(Operation.MANAGE_STAFF)
        user = self._directory.find(user_id.strip().upper())
        if user is actor:
            raise ValidationError("You cannot deactivate your own account.")
        if not user.is_active:
            raise StateError(f"{user.name} is already deactivated.")
        released = []
        if isinstance(user, DeliveryStaff):
            holding = [r.request_id for r in self._registry.for_staff(
                user.user_id) if r.status is DeliveryStatus.PICKED_UP]
            if holding:
                raise StateError(
                    f"{user.name} still holds parcels for "
                    f"{', '.join(holding)}; complete those deliveries first.")
            gaps = self._roster.gaps_if_staff_leaves(user.user_id,
                                                     self.clock.now())
            if gaps and not allow_no_cover:
                raise StateError(no_cover_message(gaps))
            for request in self._registry.for_staff(user.user_id):
                request.release_allocation(self.clock.now(), actor.user_id,
                                           f"{user.name} left the business")
                released.append(request.request_id)
            for item in self._roster.availability_for(user.user_id):
                self._roster.remove_availability(item.availability_id)
        user.deactivate()
        self._save()
        return released

    # -- requests (SRS 4.3, 4.1) ----------------------------------------------
    def create_client_on_behalf(self, name: str, email: str,
                                phone: str) -> User:
        """Office staff create a client record for a caller (A4)."""
        self._require(Operation.REQUEST_ON_BEHALF)
        user = self._directory.create_user("Client", name, email, phone,
                                           None)
        self._save()
        return user

    def check_address(self, address: Address):
        """Check one address at the point of entry (SRS 4.3 step 2)."""
        return self._area.check(address)

    def find_client(self, text: str) -> list[Client]:
        """Office staff look up existing clients by name, email or phone."""
        self._require(Operation.REQUEST_ON_BEHALF)
        needle = text.strip().lower()
        return [c for c in self._directory.clients() if needle in
                f"{c.name} {c.email} {c.phone}".lower()]

    def request_delivery(self, pickup: Address, dropoff: Address,
                         recipient_name: str, recipient_phone: str,
                         parcels: list[Parcel], level: ServiceLevel,
                         client_id: str | None = None) -> DeliveryRequest:
        """Record a new request for the client or, by office, on behalf."""
        actor = self._require(Operation.REQUEST_DELIVERY)
        if isinstance(actor, Client):
            client_id = actor.user_id
        elif client_id is None or not isinstance(
                self._directory.find(client_id), Client):
            raise ValidationError("Choose the client this request is for.")
        recipient = Recipient(recipient_name.strip(),
                              validate_phone(recipient_phone))
        if not recipient.name:
            raise ValidationError("Recipient name cannot be blank.")
        request = self._registry.create_request(
            client_id, actor.user_id, pickup, dropoff, recipient, parcels,
            level)
        self._save()
        return request

    def cancel_request(self, request_id: str, reason: str) -> None:
        """Cancel a request before pickup (client: own requests only)."""
        actor = self._require(Operation.CANCEL_REQUEST)
        request = self._visible_request(request_id)
        request.cancel(self.clock.now(), actor.user_id, reason)
        self._save()

    def view_delivery(self, request_id: str) -> DeliveryRequest:
        """Return one delivery the current user is allowed to see."""
        if self._session is None:
            raise PermissionDenied("Please log in first.")
        self._tick()
        return self._visible_request(request_id)

    def _visible_request(self, request_id: str) -> DeliveryRequest:
        request = self._registry.find(request_id)
        user = self._session
        if user is None:
            raise PermissionDenied("Please log in first.")
        if user.can(Operation.VIEW_ALL_DELIVERIES):
            return request
        if isinstance(user, Client) and request.client_id == user.user_id:
            return request
        if isinstance(user, DeliveryStaff) and \
                request.assigned_staff_id == user.user_id:
            return request
        raise NotFound(f"No delivery {request_id.strip()} exists.")

    def search_deliveries(self, text: str) -> list[DeliveryRequest]:
        """Search every delivery (office and owner, SRS 4.1)."""
        self._require(Operation.VIEW_ALL_DELIVERIES)
        return self._registry.search(text)

    def my_requests(self) -> list[DeliveryRequest]:
        """Return the logged-in client's requests."""
        user = self._require(Operation.REQUEST_DELIVERY)
        return self._registry.for_client(user.user_id)

    def track(self, tracking_code: str) -> DeliveryRequest:
        """Recipient tracking without login (F-1); caller limits fields."""
        self._tick()
        return self._registry.find_by_tracking(tracking_code)

    def client_name(self, client_id: str) -> str:
        """Return a client's name for display."""
        return self._directory.find(client_id).name

    def user_name(self, user_id: str | None) -> str:
        """Return any user's name for display."""
        if not user_id or user_id == "system":
            return "System"
        return self._directory.find(user_id).name

    # -- quotes (SRS 4.5, 4.6) ------------------------------------------------
    def requests_to_quote(self) -> list[DeliveryRequest]:
        """Return requests waiting for a quote."""
        self._require(Operation.PREPARE_QUOTE)
        return self._registry.with_status(DeliveryStatus.REQUESTED)

    def suggest_quote(self, request_id: str,
                      pickup_time: datetime) -> QuoteSuggestion:
        """Return the suggested fee, window and committed work."""
        self._require(Operation.PREPARE_QUOTE)
        if pickup_time < self.clock.now():
            raise ValidationError("Pickup time cannot be in the past.")
        return self._registry.suggest_quote(self._registry.find(request_id),
                                            pickup_time)

    def issue_quote(self, request_id: str, pickup_time: datetime,
                    fee: float | None, reason: str = "") -> Quote:
        """Issue a quote; a fee other than the suggestion needs a reason."""
        actor = self._require(Operation.PREPARE_QUOTE)
        request = self._registry.find(request_id)
        suggestion = self.suggest_quote(request_id, pickup_time)
        quote = Quote(suggestion.suggested_fee,
                      suggestion.suggested_fee if fee is None else fee,
                      reason.strip(), suggestion.distance_km, pickup_time,
                      pickup_time + suggestion.window, self.clock.now(),
                      actor.user_id)
        request.issue_quote(quote)
        self._save()
        return quote

    def decline_request(self, request_id: str, reason: str) -> None:
        """Close a request that cannot be quoted, with a reason."""
        actor = self._require(Operation.PREPARE_QUOTE)
        self._registry.find(request_id).decline_request(
            reason, self.clock.now(), actor.user_id)
        self._save()

    def respond_to_quote(self, request_id: str, accept: bool,
                         method: PaymentMethod | None = None) -> None:
        """Client accepts (choosing a payment method) or declines."""
        actor = self._require(Operation.RESPOND_TO_QUOTE)
        request = self._visible_request(request_id)
        if accept:
            if method is None:
                raise ValidationError("Choose a payment method.")
            request.accept_quote(method, self.clock.now(), actor.user_id)
        else:
            request.decline_quote(self.clock.now(), actor.user_id)
        self._save()

    # -- payment (SRS 4.4; simulated) -----------------------------------------
    def simulate_card_decline(self) -> None:
        """Demo only: make the simulated provider decline the next card."""
        if not isinstance(self._gateway, SimulatedPaymentGateway):
            raise StateError("Only the simulated card provider can do this.")
        self._gateway.decline_next = True

    def invoice(self, request_id: str) -> Invoice:
        """Return the invoice for a delivery the user may see."""
        self._logged_in()
        request = self._visible_request(request_id)
        if isinstance(self._session, DeliveryStaff):
            raise PermissionDenied("Invoices are for clients and the office.")
        return request.invoice(self.client_name(request.client_id),
                               self.clock.now())

    # -- payment records (A2 CRC 4.13 resp. 6, 4.14 resp. 2) ------------------
    def payment_records(self, status: PaymentStatus | None = None
                        ) -> list[DeliveryRequest]:
        """Return requests that have a payment, optionally by status."""
        self._require(Operation.MANAGE_PAYMENTS)
        return [r for r in self._registry.all() if r.payment is not None
                and (status is None or r.payment.status is status)]

    def record_payment_manually(self, request_id: str, note: str) -> None:
        """Mark a due payment as paid when it was settled outside the DMS."""
        actor = self._require(Operation.MANAGE_PAYMENTS)
        request = self._registry.find(request_id)
        if request.payment is None or request.status.is_closed:
            raise StateError(f"{request.request_id} has no open payment.")
        request.payment.record_manual(self.clock.now(), actor.user_id, note)
        self._save()

    def pay_online(self, request_id: str) -> str:
        """Settle an online payment through the (simulated) gateway."""
        self._require(Operation.MAKE_PAYMENT)
        request = self._visible_request(request_id)
        payment = request.payment
        if not isinstance(payment, OnlinePayment) or \
                request.status.is_closed:
            raise StateError("There is no online payment due for this "
                             "delivery.")
        reference = payment.settle(self._gateway, self.clock.now())
        self._save()
        return reference

    def change_payment_method(self, request_id: str,
                              method: PaymentMethod) -> None:
        """Switch an unpaid payment, e.g. after a declined card."""
        self._require(Operation.MAKE_PAYMENT)
        self._visible_request(request_id).change_payment_method(
            method, self.clock.now())
        self._save()

    # -- allocation and availability (SRS 4.7, 4.8) ---------------------------
    def awaiting_allocation(self) -> list[DeliveryRequest]:
        """Return accepted requests with no driver yet."""
        self._require(Operation.ALLOCATE_STAFF)
        return self._registry.with_status(DeliveryStatus.ACCEPTED)

    def available_staff_for(self, request_id: str) -> list[DeliveryStaff]:
        """Return drivers free for the request's whole quoted window."""
        self._require(Operation.ALLOCATE_STAFF)
        period = self._registry.find(request_id).committed_period()
        if period is None:
            raise StateError("The request has no quoted window.")
        return self._roster.available_staff(*period)

    def allocate(self, request_id: str, staff_id: str) -> None:
        """Allocate a driver, re-checking availability at confirmation."""
        actor = self._require(Operation.ALLOCATE_STAFF)
        request = self._registry.find(request_id)
        staff = self._roster.staff(staff_id.strip().upper())
        period = request.committed_period()
        if period is None or not self._roster.is_free(staff.user_id,
                                                      *period):
            raise StateError(f"{staff.name} is no longer available for "
                             "that window. Choose another driver.")
        request.allocate(staff.user_id, self.clock.now(), actor.user_id,
                         staff.name)
        self._save()

    def active_drivers(self) -> list[DeliveryStaff]:
        """Return active delivery staff (for availability screens)."""
        self._require(Operation.MANAGE_AVAILABILITY)
        return self._roster.active_staff()

    def availability_of(self, staff_id: str) -> list[StaffAvailability]:
        """Return a driver's availability periods."""
        self._require(Operation.MANAGE_AVAILABILITY)
        return self._roster.availability_for(staff_id.strip().upper())

    def assignments_of(self, staff_id: str) -> list[DeliveryRequest]:
        """Return a driver's open allocations."""
        self._require(Operation.MANAGE_AVAILABILITY)
        return self._registry.for_staff(staff_id.strip().upper())

    def add_availability(self, staff_id: str, start: datetime,
                         end: datetime) -> StaffAvailability:
        """Add an availability period for a driver."""
        self._require(Operation.MANAGE_AVAILABILITY)
        item = self._roster.add_availability(staff_id.strip().upper(),
                                             start, end)
        self._save()
        return item

    def availability_conflicts(self, availability_id: str) -> list[str]:
        """Return requests that removing a period would leave uncovered."""
        self._require(Operation.MANAGE_AVAILABILITY)
        return self._roster.conflicts_if_removed(availability_id)

    def availability_cover_gaps(self, availability_id: str
                                ) -> list[tuple[datetime, datetime]]:
        """Return future times with no driver if a period were removed."""
        self._require(Operation.MANAGE_AVAILABILITY)
        return self._roster.gaps_if_removed(availability_id,
                                            self.clock.now())

    def remove_availability(self, availability_id: str,
                            allow_no_cover: bool = False) -> list[str]:
        """Remove a period; uncovered allocations return to Accepted.

        Refused, unless ``allow_no_cover`` confirms it, when no other driver
        would be available for part of the period (A2 CRC 4.7 #3).
        """
        actor = self._require(Operation.MANAGE_AVAILABILITY)
        gaps = self._roster.gaps_if_removed(availability_id,
                                            self.clock.now())
        if gaps and not allow_no_cover:
            raise StateError(no_cover_message(gaps))
        conflicts = self._roster.conflicts_if_removed(availability_id)
        self._roster.remove_availability(availability_id)
        for request_id in conflicts:
            self._registry.find(request_id).release_allocation(
                self.clock.now(), actor.user_id,
                "Driver no longer available; needs re-allocation")
        self._save()
        return conflicts

    # -- delivery (F-2) -------------------------------------------------------
    def my_assignments(self) -> list[DeliveryRequest]:
        """Return the logged-in driver's open deliveries."""
        user = self._require(Operation.PERFORM_DELIVERY)
        return self._registry.for_staff(user.user_id)

    def record_pickup(self, request_id: str) -> None:
        """Driver confirms every parcel has been collected."""
        user = self._require(Operation.PERFORM_DELIVERY)
        self._visible_request(request_id).record_pickup(
            user.user_id, user.name, self.clock.now())
        self._save()

    def collect_cash(self, request_id: str, amount: float) -> None:
        """Driver records cash received at drop-off."""
        user = self._require(Operation.PERFORM_DELIVERY)
        request = self._visible_request(request_id)
        if request.status is not DeliveryStatus.PICKED_UP or \
                not isinstance(request.payment, CashOnDelivery):
            raise StateError("No cash is due for this delivery now.")
        request.payment.record_collection(amount, user.user_id,
                                          self.clock.now())
        self._save()

    def record_handover(self, request_id: str, received_by: str) -> None:
        """Driver hands every parcel over and completes the delivery."""
        user = self._require(Operation.PERFORM_DELIVERY)
        self._visible_request(request_id).record_handover(
            user.user_id, received_by, self.clock.now())
        self._save()

    # -- statistics (SRS 4.2) -------------------------------------------------
    def statistics(self, period: Period, reference: date,
                   staff_id: str | None = None) -> Statistics:
        """Return counts for a period, optionally for one driver."""
        self._require(Operation.VIEW_STATISTICS)
        if staff_id:
            staff_id = staff_id.strip().upper()
            if not isinstance(self._directory.find(staff_id), DeliveryStaff):
                raise NotFound(f"{staff_id} is not a delivery staff member.")
        return self._registry.statistics(period, reference, staff_id)

    def all_drivers(self) -> list[DeliveryStaff]:
        """Return every driver, including leavers, for statistics."""
        self._require(Operation.VIEW_STATISTICS)
        return self._directory.delivery_staff()


def no_cover_message(gaps: list[tuple[datetime, datetime]]) -> str:
    """Describe times that would be left with no delivery staff."""
    return ("No other delivery staff member is available "
            + "; ".join(f"{format_datetime(s)} to {format_datetime(e)}"
                        for s, e in gaps) + ".")
