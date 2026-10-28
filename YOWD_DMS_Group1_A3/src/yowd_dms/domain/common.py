"""Shared domain types: errors, the clock abstraction and identifiers."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from typing import Callable

DATETIME_FORMAT = "%Y-%m-%d %H:%M"


class DomainError(Exception):
    """Base class for every business-rule failure raised by the domain."""


class ValidationError(DomainError):
    """Raised when user-supplied data breaks a validation rule."""


class PermissionDenied(DomainError):
    """Raised when the current user's role does not permit an operation."""


class NotFound(DomainError):
    """Raised when a requested record does not exist."""


class StateError(DomainError):
    """Raised when an operation is not valid in the record's current state."""


class IntegrityError(DomainError):
    """Raised at startup when saved records contradict each other (A2 6.3).

    The system refuses to start rather than silently discarding records.
    """


class Clock:
    """Supplies the current time; replaceable for repeatable tests and demos.

    Introduced in Assignment 3: quote expiry (A8) and account lockout (SRS 7.1)
    both depend on time, and could not be tested against the real clock.
    """

    def __init__(self, now_fn: Callable[[], datetime] | None = None) -> None:
        self._now_fn = now_fn or datetime.now
        self._offset = timedelta(0)

    def now(self) -> datetime:
        """Return the current time, rounded down to the minute."""
        return (self._now_fn() + self._offset).replace(second=0, microsecond=0)

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward (used by tests and the demo menu only)."""
        self._offset += delta


class IdGenerator:
    """Generates readable, unique identifiers such as ``DR-0007``."""

    def __init__(self, prefix: str, start: int = 1) -> None:
        self._prefix = prefix
        self._counter = itertools.count(start)

    def next_id(self) -> str:
        """Return the next identifier in sequence."""
        return f"{self._prefix}-{next(self._counter):04d}"

    def reseed(self, existing_ids: list[str]) -> None:
        """Continue numbering after the highest identifier already stored."""
        numbers = [
            int(i.split("-")[-1]) for i in existing_ids
            if i.startswith(self._prefix + "-")
        ]
        self._counter = itertools.count(max(numbers, default=0) + 1)


def parse_datetime(text: str) -> datetime:
    """Parse ``YYYY-MM-DD HH:MM`` text or raise ``ValidationError``."""
    try:
        return datetime.strptime(text.strip(), DATETIME_FORMAT)
    except ValueError as exc:
        raise ValidationError(
            "Date/time must be in the form YYYY-MM-DD HH:MM, "
            "for example 2026-10-27 09:30."
        ) from exc


def format_datetime(value: datetime | None) -> str:
    """Format a datetime for display, or ``-`` when it is missing."""
    return value.strftime(DATETIME_FORMAT) if value else "-"


def require_text(value: str, field: str, max_length: int = 120) -> str:
    """Return ``value`` stripped, or raise if it is blank or too long."""
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValidationError(f"{field} cannot be blank.")
    if len(cleaned) > max_length:
        raise ValidationError(
            f"{field} must be at most {max_length} characters."
        )
    return cleaned
