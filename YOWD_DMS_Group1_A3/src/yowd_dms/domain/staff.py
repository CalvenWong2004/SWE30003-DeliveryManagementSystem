"""Delivery-staff availability (A2 CRC 4.6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .common import ValidationError, format_datetime


@dataclass(frozen=True)
class StaffAvailability:
    """A period in which one delivery staff member can take work."""

    availability_id: str
    staff_id: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValidationError("Availability must end after it starts.")

    def covers(self, start: datetime, end: datetime) -> bool:
        """Return True if ``start``-``end`` lies wholly inside this period."""
        return self.start <= start and end <= self.end

    def overlaps(self, start: datetime, end: datetime) -> bool:
        """Return True if this period overlaps ``start``-``end`` at all."""
        return self.start < end and start < self.end

    def __str__(self) -> str:
        return f"{format_datetime(self.start)} to {format_datetime(self.end)}"
