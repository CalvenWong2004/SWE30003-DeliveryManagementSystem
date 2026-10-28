"""NotificationService: tells clients and recipients about progress (A12).

Implements the Observer pattern: each DeliveryRequest calls
``on_status_change`` after a committed transition. Email and SMS providers
are out of scope, so messages are written to an outbox file the demo shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..domain.common import format_datetime
from ..domain.delivery import DeliveryRequest, DeliveryStatus, StatusChange

NOTIFIED_STATUSES = frozenset({
    DeliveryStatus.REQUESTED, DeliveryStatus.QUOTED, DeliveryStatus.ACCEPTED,
    DeliveryStatus.PICKED_UP, DeliveryStatus.DELIVERED,
    DeliveryStatus.DECLINED, DeliveryStatus.EXPIRED,
    DeliveryStatus.CANCELLED,
})


@dataclass(frozen=True)
class Notification:
    """One message placed in the outbox."""

    at: datetime
    channel: str
    to: str
    text: str


class NotificationService:
    """Observer that turns status changes into email and SMS messages."""

    def __init__(self, outbox_path: Path | None,
                 email_lookup: Callable[[str], str]) -> None:
        self._outbox_path = outbox_path
        self._email_lookup = email_lookup
        self.sent: list[Notification] = []

    def on_status_change(self, request: DeliveryRequest,
                         change: StatusChange) -> None:
        """Notify the client (email) and, once on the road, the recipient."""
        if change.status not in NOTIFIED_STATUSES:
            return
        text = (f"{request.request_id} is now {change.status.value}"
                + (f": {change.note}" if change.note else "") + ".")
        self._send(change.at, "Email", self._email_lookup(request.client_id),
                   text)
        if change.status in (DeliveryStatus.ACCEPTED,
                             DeliveryStatus.PICKED_UP,
                             DeliveryStatus.DELIVERED):
            self._send(change.at, "SMS", request.recipient.phone,
                       f"Parcel for {request.recipient.name}: "
                       f"{change.status.value}. Track with code "
                       f"{request.tracking_code}.")

    def notify_new_request(self, request: DeliveryRequest) -> None:
        """Send the confirmation for a request that has just been created."""
        self.on_status_change(request, request.history[0])

    def _send(self, at: datetime, channel: str, to: str, text: str) -> None:
        message = Notification(at, channel, to, text)
        self.sent.append(message)
        if self._outbox_path is not None:
            with self._outbox_path.open("a", encoding="utf-8") as outbox:
                outbox.write(f"{format_datetime(at)} | {channel:<5} | "
                             f"{to} | {text}\n")
