"""User hierarchy: the people the DMS identifies and what each role may do."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import Enum

from .common import ValidationError, require_text

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_PATTERN = re.compile(r"^(0[2-9]\d{8}|\+61[2-9]\d{8})$")
MIN_PASSWORD_LENGTH = 8
MAX_FAILED_LOGINS = 5                       # SRS 7.1 (assumed target)
LOCKOUT_PERIOD = timedelta(minutes=15)      # SRS 7.1 (assumed target)


class Operation(Enum):
    """Business operations whose use is controlled by role (SRS 7.1, 8.2)."""

    REQUEST_DELIVERY = "request a delivery"
    REQUEST_ON_BEHALF = "log a request for a client"
    CANCEL_REQUEST = "cancel a delivery request"
    PREPARE_QUOTE = "prepare a quote"
    RESPOND_TO_QUOTE = "respond to a quote"
    MAKE_PAYMENT = "make a payment"
    ALLOCATE_STAFF = "allocate delivery staff"
    MANAGE_AVAILABILITY = "manage staff availability"
    VIEW_ALL_DELIVERIES = "view all deliveries"
    PERFORM_DELIVERY = "record pickup and hand-over"
    VIEW_STATISTICS = "view delivery statistics"
    MANAGE_STAFF = "add or deactivate staff"
    MANAGE_CLIENTS = "update or deactivate client records"
    MANAGE_PAYMENTS = "view and update payment records"


class AccountState(Enum):
    """Lifecycle state of an account (A14: accounts are never deleted)."""

    ACTIVE = "Active"
    DEACTIVATED = "Deactivated"


def validate_email(email: str) -> str:
    """Return a normalised email address or raise ``ValidationError``."""
    cleaned = require_text(email, "Email").lower()
    if not EMAIL_PATTERN.match(cleaned):
        raise ValidationError("Email must look like name@example.com.")
    return cleaned


def validate_phone(phone: str) -> str:
    """Return an Australian phone number without spaces, or raise."""
    cleaned = re.sub(r"[\s-]", "", require_text(phone, "Phone"))
    if not PHONE_PATTERN.match(cleaned):
        raise ValidationError(
            "Phone must be an Australian number, e.g. 0412 345 678."
        )
    return cleaned


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash ``password`` with PBKDF2-SHA256; return ``(salt, hash)``."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 100_000
    )
    return salt, digest.hex()


class User(ABC):
    """Abstract base for every account holder (A2 CRC 4.2).

    Holds identity, contact details, credentials and account state. Each
    subclass states which operations its role permits.
    """

    role_name = "User"

    def __init__(self, user_id: str, name: str, email: str, phone: str,
                 password_salt: str = "", password_hash: str = "") -> None:
        self.user_id = user_id
        self.name = require_text(name, "Name", 80)
        self.email = validate_email(email)
        self.phone = validate_phone(phone)
        self._password_salt = password_salt
        self._password_hash = password_hash
        self.state = AccountState.ACTIVE
        self.failed_logins = 0
        self.locked_until: datetime | None = None

    # -- credentials ------------------------------------------------------
    def set_password(self, password: str) -> None:
        """Replace the stored credential with a hash of ``password``."""
        self._password_salt, self._password_hash = hash_password(password)

    def has_password(self) -> bool:
        """Return True once the account has a credential set."""
        return bool(self._password_hash)

    def verify_password(self, password: str) -> bool:
        """Return True if ``password`` matches the stored credential."""
        if not self._password_hash or len(password or "") < 1:
            return False
        try:
            _, digest = hash_password(password, self._password_salt)
        except ValidationError:
            return False
        return hmac.compare_digest(digest, self._password_hash)

    # -- contact details (A2 CRC 4.8, 4.11) -------------------------------
    def update_contact(self, name: str | None = None,
                       phone: str | None = None) -> None:
        """Change name and/or phone; the email stays the login identity."""
        new_name = require_text(name, "Name", 80) if name is not None \
            else self.name
        new_phone = validate_phone(phone) if phone is not None \
            else self.phone
        self.name, self.phone = new_name, new_phone

    # -- account state ----------------------------------------------------
    def is_locked(self, now: datetime) -> bool:
        """Return True while a lockout from failed logins is in force."""
        return self.locked_until is not None and now < self.locked_until

    def record_failed_login(self, now: datetime) -> None:
        """Count a failed login and lock the account at the threshold."""
        self.failed_logins += 1
        if self.failed_logins >= MAX_FAILED_LOGINS:
            self.locked_until = now + LOCKOUT_PERIOD

    def record_successful_login(self) -> None:
        """Clear the failure count after a successful login."""
        self.failed_logins = 0
        self.locked_until = None

    @property
    def is_active(self) -> bool:
        """Return True unless the account has been deactivated."""
        return self.state is AccountState.ACTIVE

    def deactivate(self) -> None:
        """Deactivate the account; history that refers to it is kept."""
        self.state = AccountState.DEACTIVATED

    # -- permissions ------------------------------------------------------
    @abstractmethod
    def permitted_operations(self) -> frozenset[Operation]:
        """Return the operations this role may perform."""

    def can(self, operation: Operation) -> bool:
        """Return True if this user's role permits ``operation``."""
        return self.is_active and operation in self.permitted_operations()

    def credential_fields(self) -> tuple[str, str]:
        """Expose the stored salt and hash for the persistence mapper only."""
        return self._password_salt, self._password_hash

    def __str__(self) -> str:
        return f"{self.name} ({self.role_name}, {self.email})"


class Client(User):
    """A customer who requests deliveries, answers quotes and pays."""

    role_name = "Client"

    def __init__(self, *args, default_address: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.default_address = default_address

    def update_address(self, address: str) -> None:
        """Change the client's address on record ("" clears it)."""
        self.default_address = require_text(address, "Address", 120) \
            if address.strip() else ""

    def permitted_operations(self) -> frozenset[Operation]:
        return frozenset({
            Operation.REQUEST_DELIVERY, Operation.CANCEL_REQUEST,
            Operation.RESPOND_TO_QUOTE, Operation.MAKE_PAYMENT,
        })


class OfficeStaff(User):
    """Internal staff who log requests, quote and allocate deliveries."""

    role_name = "Office Staff"

    def permitted_operations(self) -> frozenset[Operation]:
        return frozenset({
            Operation.REQUEST_DELIVERY, Operation.REQUEST_ON_BEHALF,
            Operation.CANCEL_REQUEST, Operation.PREPARE_QUOTE,
            Operation.ALLOCATE_STAFF, Operation.MANAGE_AVAILABILITY,
            Operation.VIEW_ALL_DELIVERIES, Operation.VIEW_STATISTICS,
            Operation.MANAGE_CLIENTS, Operation.MANAGE_PAYMENTS,
        })


class BusinessOwner(OfficeStaff):
    """Owner: every office permission plus staff management (A13, F-4).

    Changed in Assignment 3 to inherit from OfficeStaff (was a direct User
    subclass), so office permissions are not duplicated.
    """

    role_name = "Business Owner"

    def permitted_operations(self) -> frozenset[Operation]:
        return super().permitted_operations() | {Operation.MANAGE_STAFF}


class DeliveryStaff(User):
    """A driver who collects parcels, takes cash and completes deliveries."""

    role_name = "Delivery Staff"

    def permitted_operations(self) -> frozenset[Operation]:
        return frozenset({Operation.PERFORM_DELIVERY})


ROLE_CLASSES: dict[str, type[User]] = {
    cls.role_name: cls
    for cls in (Client, OfficeStaff, BusinessOwner, DeliveryStaff)
}
