"""Data Mappers between domain objects and JSON-ready dictionaries.

Keeping conversion here means the domain classes know nothing about storage,
which is the "database design in an object-oriented manner" for this release:
each aggregate (user, availability, delivery request with everything it
owns) maps to one JSON record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..domain.delivery import (Address, CustodyRecord, DeliveryRequest,
                               DeliveryStatus, Parcel, ParcelSize, Quote,
                               Recipient, ServiceLevel, StatusChange)
from ..domain.payment import (CashOnDelivery, OnlinePayment, Payment,
                              PaymentMethod, PaymentStatus)
from ..domain.staff import StaffAvailability
from ..domain.users import ROLE_CLASSES, AccountState, Client, User


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# -- users ---------------------------------------------------------------
def user_to_dict(user: User) -> dict[str, Any]:
    """Convert a user to a record."""
    salt, digest = user.credential_fields()
    record = {
        "user_id": user.user_id, "role": user.role_name, "name": user.name,
        "email": user.email, "phone": user.phone, "salt": salt,
        "hash": digest, "state": user.state.value,
        "failed_logins": user.failed_logins,
        "locked_until": _dt(user.locked_until),
    }
    if isinstance(user, Client):
        record["default_address"] = user.default_address
    return record


def user_from_dict(record: dict[str, Any]) -> User:
    """Rebuild the concrete user subclass named by ``record['role']``."""
    cls = ROLE_CLASSES[record["role"]]
    extra = {}
    if cls is Client:
        extra["default_address"] = record.get("default_address", "")
    user = cls(record["user_id"], record["name"], record["email"],
               record["phone"], record["salt"], record["hash"], **extra)
    user.state = AccountState(record["state"])
    user.failed_logins = record["failed_logins"]
    user.locked_until = _parse(record["locked_until"])
    return user


# -- availability --------------------------------------------------------
def availability_to_dict(item: StaffAvailability) -> dict[str, Any]:
    """Convert an availability period to a record."""
    return {"availability_id": item.availability_id,
            "staff_id": item.staff_id, "start": _dt(item.start),
            "end": _dt(item.end)}


def availability_from_dict(record: dict[str, Any]) -> StaffAvailability:
    """Rebuild an availability period."""
    return StaffAvailability(record["availability_id"], record["staff_id"],
                             _parse(record["start"]), _parse(record["end"]))


# -- delivery requests ---------------------------------------------------
def _address(record: dict[str, str]) -> Address:
    return Address(record["street"], record["suburb"], record["postcode"],
                   record["state"])


def _payment_to_dict(payment: Payment | None) -> dict[str, Any] | None:
    if payment is None:
        return None
    return {"method": payment.method.value, "payment_id": payment.payment_id,
            "amount": payment.amount, "created_at": _dt(payment.created_at),
            "status": payment.status.value, "paid_at": _dt(payment.paid_at),
            "reference": payment.reference, "card_type": payment.card_type,
            "recorded_by": payment.recorded_by, "note": payment.note}


def _payment_from_dict(record: dict[str, Any] | None) -> Payment | None:
    if record is None:
        return None
    cls = (OnlinePayment if PaymentMethod(record["method"])
           is PaymentMethod.ONLINE else CashOnDelivery)
    payment = cls(record["payment_id"], record["amount"],
                  _parse(record["created_at"]))
    payment.status = PaymentStatus(record["status"])
    payment.paid_at = _parse(record["paid_at"])
    payment.reference = record["reference"]
    # Fields added in A3 after the first data files; older files lack them.
    payment.card_type = record.get("card_type", "")
    payment.recorded_by = record.get("recorded_by", "")
    payment.note = record.get("note", "")
    return payment


def _quote_to_dict(quote: Quote | None) -> dict[str, Any] | None:
    if quote is None:
        return None
    return {"suggested_fee": quote.suggested_fee, "fee": quote.fee,
            "adjustment_reason": quote.adjustment_reason,
            "distance_km": quote.distance_km,
            "estimated_pickup": _dt(quote.estimated_pickup),
            "estimated_delivery": _dt(quote.estimated_delivery),
            "issued_at": _dt(quote.issued_at),
            "issued_by_user_id": quote.issued_by_user_id}


def _quote_from_dict(record: dict[str, Any] | None) -> Quote | None:
    if record is None:
        return None
    return Quote(record["suggested_fee"], record["fee"],
                 record["adjustment_reason"], record["distance_km"],
                 _parse(record["estimated_pickup"]),
                 _parse(record["estimated_delivery"]),
                 _parse(record["issued_at"]), record["issued_by_user_id"])


def request_to_dict(request: DeliveryRequest) -> dict[str, Any]:
    """Convert a request and everything it owns to one record."""
    return {
        "request_id": request.request_id,
        "tracking_code": request.tracking_code,
        "client_id": request.client_id,
        "created_by_id": request.created_by_id,
        "created_at": _dt(request.created_at),
        "pickup": vars(request.pickup).copy(),
        "dropoff": vars(request.dropoff).copy(),
        "recipient": {"name": request.recipient.name,
                      "phone": request.recipient.phone},
        "parcels": [{"label": p.label, "description": p.description,
                     "size": p.size.value, "weight_kg": p.weight_kg}
                    for p in request.parcels],
        "service_level": request.service_level.value,
        "quote": _quote_to_dict(request.quote),
        "payment": _payment_to_dict(request.payment),
        "assigned_staff_id": request.assigned_staff_id,
        "history": [{"status": h.status.value, "at": _dt(h.at),
                     "by": h.by_user_id, "note": h.note}
                    for h in request.history],
        "custody": [{"parcel": c.parcel_label, "holder": c.holder,
                     "at": _dt(c.at), "event": c.event}
                    for c in request.custody],
    }


def request_from_dict(record: dict[str, Any]) -> DeliveryRequest:
    """Rebuild a request without re-running business transitions."""
    parcels = [Parcel(p["label"], p["description"], ParcelSize(p["size"]),
                      p["weight_kg"]) for p in record["parcels"]]
    request = DeliveryRequest(
        record["request_id"], record["tracking_code"], record["client_id"],
        record["created_by_id"], _address(record["pickup"]),
        _address(record["dropoff"]),
        Recipient(record["recipient"]["name"], record["recipient"]["phone"]),
        parcels, ServiceLevel(record["service_level"]),
        _parse(record["created_at"]))
    request.quote = _quote_from_dict(record["quote"])
    request.payment = _payment_from_dict(record["payment"])
    request.assigned_staff_id = record["assigned_staff_id"]
    request.history = [StatusChange(DeliveryStatus(h["status"]),
                                    _parse(h["at"]), h["by"], h["note"])
                       for h in record["history"]]
    request.custody = [CustodyRecord(c["parcel"], c["holder"],
                                     _parse(c["at"]), c["event"])
                       for c in record["custody"]]
    return request
