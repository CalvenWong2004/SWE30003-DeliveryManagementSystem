"""StaffRoster: active delivery staff and when they can work (CRC 4.7)."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from ..domain.common import IdGenerator, NotFound, ValidationError
from ..domain.staff import StaffAvailability
from ..domain.users import DeliveryStaff
from ..persistence import mappers
from ..persistence.json_store import JsonStore
from .user_directory import UserDirectory

COLLECTION = "availability"

# (start, end, request_id) of each delivery a driver is committed to.
Commitment = tuple[datetime, datetime, str]
CommitmentSource = Callable[[str], list[Commitment]]
# (start, end) of a stretch of time.
Period = tuple[datetime, datetime]


class StaffRoster:
    """Answers "who can take this delivery?" and keeps availability."""

    def __init__(self, store: JsonStore, directory: UserDirectory) -> None:
        self._store = store
        self._directory = directory
        self._availability: dict[str, StaffAvailability] = {}
        self._ids = IdGenerator("AV")
        self._commitments: CommitmentSource = lambda staff_id: []

    # -- bootstrap ------------------------------------------------------------
    def load(self) -> None:
        """Restore availability for staff that UserDirectory restored."""
        known = {s.user_id for s in self._directory.delivery_staff()}
        for record in self._store.load(COLLECTION):
            item = mappers.availability_from_dict(record)
            if item.staff_id not in known:
                raise ValidationError(
                    f"Availability {item.availability_id} refers to unknown "
                    f"staff {item.staff_id}; startup stopped.")
            self._availability[item.availability_id] = item
        self._ids.reseed(list(self._availability))

    def save(self) -> None:
        """Write availability back to storage."""
        self._store.save(COLLECTION, [mappers.availability_to_dict(a)
                                      for a in self._availability.values()])

    def set_commitment_source(self, source: CommitmentSource) -> None:
        """Wire in DeliveryRegistry's view of allocated work (bootstrap)."""
        self._commitments = source

    # -- staff ----------------------------------------------------------------
    def active_staff(self) -> list[DeliveryStaff]:
        """Return delivery staff whose accounts are active."""
        return [s for s in self._directory.delivery_staff() if s.is_active]

    def staff(self, staff_id: str) -> DeliveryStaff:
        """Return the active delivery staff member ``staff_id``."""
        for member in self.active_staff():
            if member.user_id == staff_id:
                return member
        raise NotFound(f"No active delivery staff member {staff_id}.")

    # -- availability (SRS 4.8) -----------------------------------------------
    def availability_for(self, staff_id: str) -> list[StaffAvailability]:
        """Return ``staff_id``'s periods in start order."""
        return sorted((a for a in self._availability.values()
                       if a.staff_id == staff_id), key=lambda a: a.start)

    def add_availability(self, staff_id: str, start: datetime,
                         end: datetime) -> StaffAvailability:
        """Add a period; it may not overlap the staff member's others."""
        self.staff(staff_id)
        for existing in self.availability_for(staff_id):
            if existing.overlaps(start, end):
                raise ValidationError(
                    f"Overlaps existing availability {existing}.")
        if end <= start:
            raise ValidationError("Availability must end after it starts.")
        item = StaffAvailability(self._ids.next_id(), staff_id, start, end)
        self._availability[item.availability_id] = item
        return item

    def conflicts_if_removed(self, availability_id: str) -> list[str]:
        """Return request ids that would lose cover if a period went."""
        item = self._get(availability_id)
        return [request_id for start, end, request_id
                in self._commitments(item.staff_id)
                if item.overlaps(start, end)]

    def remove_availability(self, availability_id: str) -> StaffAvailability:
        """Remove a period (the caller first warns about conflicts)."""
        return self._availability.pop(self._get(availability_id)
                                      .availability_id)

    # -- minimum cover (A2 CRC 4.7 responsibility 3) --------------------------
    def gaps_if_removed(self, availability_id: str,
                        now: datetime) -> list[Period]:
        """Return future times no other driver covers if a period goes."""
        item = self._get(availability_id)
        return self._cover_gaps(item.staff_id, [(item.start, item.end)], now)

    def gaps_if_staff_leaves(self, staff_id: str,
                             now: datetime) -> list[Period]:
        """Return future times left with no driver if ``staff_id`` leaves."""
        return self._cover_gaps(staff_id, [
            (a.start, a.end) for a in self.availability_for(staff_id)], now)

    def _cover_gaps(self, staff_id: str, periods: list[Period],
                    now: datetime) -> list[Period]:
        """Subtract other active drivers' availability from ``periods``."""
        active = {s.user_id for s in self.active_staff()} - {staff_id}
        others = [a for a in self._availability.values()
                  if a.staff_id in active]
        gaps: list[Period] = []
        for start, end in periods:
            segments = [(max(start, now), end)] if end > now else []
            for other in others:
                remaining = []
                for seg_start, seg_end in segments:
                    if not other.overlaps(seg_start, seg_end):
                        remaining.append((seg_start, seg_end))
                        continue
                    if seg_start < other.start:
                        remaining.append((seg_start, other.start))
                    if other.end < seg_end:
                        remaining.append((other.end, seg_end))
                segments = remaining
            gaps.extend(segments)
        return sorted(gaps)

    def _get(self, availability_id: str) -> StaffAvailability:
        try:
            return self._availability[availability_id.strip().upper()]
        except KeyError as exc:
            raise NotFound(f"No availability {availability_id}.") from exc

    # -- allocation support (SRS 4.7) -----------------------------------------
    def is_free(self, staff_id: str, start: datetime, end: datetime) -> bool:
        """Return True if covered by availability and not already booked."""
        covered = any(a.covers(start, end)
                      for a in self.availability_for(staff_id))
        clash = any(s < end and start < e
                    for s, e, _ in self._commitments(staff_id))
        return covered and not clash

    def available_staff(self, start: datetime,
                        end: datetime) -> list[DeliveryStaff]:
        """Return active staff free for the whole of ``start``-``end``."""
        return [s for s in self.active_staff()
                if self.is_free(s.user_id, start, end)]

    def committed_count(self, start: datetime, end: datetime) -> int:
        """Return how many allocated deliveries overlap a period."""
        return sum(1 for member in self.active_staff()
                   for s, e, _ in self._commitments(member.user_id)
                   if s < end and start < e)

    def free_staff_count(self, start: datetime, end: datetime) -> int:
        """Return how many staff could still take work in a period."""
        return len(self.available_staff(start, end))
