"""UserDirectory: owns every account and authenticates users (CRC 4.8)."""

from __future__ import annotations

from ..domain.common import (Clock, IdGenerator, NotFound, PermissionDenied,
                             ValidationError, format_datetime)
from ..domain.users import (ROLE_CLASSES, BusinessOwner, Client,
                            DeliveryStaff, OfficeStaff, User)
from ..persistence import mappers
from ..persistence.json_store import JsonStore

COLLECTION = "users"


class UserDirectory:
    """Creates, finds, authenticates and deactivates accounts."""

    def __init__(self, store: JsonStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock
        self._users: dict[str, User] = {}
        self._ids = IdGenerator("U")

    # -- persistence ----------------------------------------------------------
    def load(self) -> None:
        """Restore accounts from storage (bootstrap step 4)."""
        for record in self._store.load(COLLECTION):
            user = mappers.user_from_dict(record)
            self._users[user.user_id] = user
        self._ids.reseed(list(self._users))

    def save(self) -> None:
        """Write every account back to storage."""
        self._store.save(COLLECTION, [mappers.user_to_dict(u)
                                      for u in self._users.values()])

    # -- Factory Method -------------------------------------------------------
    def create_user(self, role_name: str, name: str, email: str, phone: str,
                    password: str | None, **extra: str) -> User:
        """Create the concrete subclass for ``role_name`` and register it.

        ``password`` may be None for a client created by office staff on a
        caller's behalf (A4); such an account cannot log in until activated.
        """
        cls = ROLE_CLASSES.get(role_name)
        if cls is None:
            raise ValidationError(f"Unknown role: {role_name}.")
        user = cls("pending", name, email, phone, **extra)
        if self.find_by_email(user.email) is not None:
            raise ValidationError(f"{user.email} is already registered.")
        if password is not None:
            user.set_password(password)
        user.user_id = self._ids.next_id()
        self._users[user.user_id] = user
        return user

    # -- authentication (SRS 7.1) ---------------------------------------------
    def authenticate(self, email: str, password: str) -> User:
        """Return the user for valid credentials; apply the lockout rule."""
        now = self._clock.now()
        user = self.find_by_email(email.strip().lower())
        if user is None or not user.is_active or not user.has_password():
            raise PermissionDenied("Email or password is incorrect.")
        if user.is_locked(now):
            raise PermissionDenied(
                "This account is locked after 5 failed attempts. Try again "
                f"after {format_datetime(user.locked_until)}.")
        if not user.verify_password(password):
            user.record_failed_login(now)
            self.save()
            if user.is_locked(now):
                raise PermissionDenied(
                    "5 failed attempts: the account is locked for 15 "
                    "minutes.")
            raise PermissionDenied("Email or password is incorrect.")
        user.record_successful_login()
        self.save()
        return user

    # -- maintenance (A2 CRC 4.8 responsibilities 2 and 4) -------------------
    def update_details(self, user: User, name: str | None = None,
                       phone: str | None = None,
                       default_address: str | None = None) -> User:
        """Update a user's contact details; email stays the identity."""
        user.update_contact(name, phone)
        if default_address is not None:
            if not isinstance(user, Client):
                raise ValidationError("Only clients have an address on "
                                      "record.")
            user.update_address(default_address)
        return user

    def change_password(self, user: User, current: str, new: str) -> None:
        """Replace a password after checking the current one."""
        if not user.verify_password(current):
            raise PermissionDenied("The current password is incorrect.")
        user.set_password(new)

    # -- queries --------------------------------------------------------------
    def find(self, user_id: str) -> User:
        """Return the user with ``user_id`` or raise ``NotFound``."""
        try:
            return self._users[user_id]
        except KeyError as exc:
            raise NotFound(f"No user {user_id}.") from exc

    def find_by_email(self, email: str) -> User | None:
        """Return the user registered with ``email``, if any."""
        email = email.strip().lower()
        return next((u for u in self._users.values() if u.email == email),
                    None)

    def clients(self) -> list[Client]:
        """Return every client account."""
        return [u for u in self._users.values() if isinstance(u, Client)]

    def delivery_staff(self) -> list[DeliveryStaff]:
        """Return every delivery staff account, active or not."""
        return [u for u in self._users.values()
                if isinstance(u, DeliveryStaff)]

    def internal_staff(self) -> list[User]:
        """Return office staff, owners and delivery staff."""
        return [u for u in self._users.values()
                if isinstance(u, (OfficeStaff, BusinessOwner, DeliveryStaff))]

    def email_of(self, user_id: str) -> str:
        """Return the email for ``user_id`` (used by notifications)."""
        return self.find(user_id).email

    def has_owner(self) -> bool:
        """Return True once an active business owner exists."""
        return any(isinstance(u, BusinessOwner) and u.is_active
                   for u in self._users.values())
