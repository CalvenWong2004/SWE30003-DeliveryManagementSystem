"""Read-only views of a delivery shared by several screens."""

from __future__ import annotations

from ..app.dms import DeliveryManagementSystem
from ..domain.common import format_datetime
from ..domain.delivery import DeliveryRequest, DeliveryStatus
from ..domain.payment import Invoice
from .console_io import ConsoleIO


def request_rows(dms: DeliveryManagementSystem,
                 requests: list[DeliveryRequest]) -> list[list[str]]:
    """Return summary rows for a list of requests."""
    rows = []
    for request in requests:
        fee = f"${request.quote.fee:.2f}" if request.quote else "-"
        rows.append([request.request_id, request.status.value,
                     dms.client_name(request.client_id),
                     request.pickup.suburb, request.dropoff.suburb, fee])
    return rows


def show_list(io: ConsoleIO, dms: DeliveryManagementSystem,
              requests: list[DeliveryRequest]) -> None:
    """Print a summary table of requests."""
    io.table(["ID", "Status", "Client", "From", "To", "Fee"],
             request_rows(dms, requests))


def show_detail(io: ConsoleIO, dms: DeliveryManagementSystem,
                request: DeliveryRequest) -> None:
    """Print everything an authorised user may see about a delivery."""
    io.heading(f"Delivery {request.request_id}  -  {request.status.value}")
    io.say(f"  Client       : {dms.client_name(request.client_id)}")
    io.say(f"  Pickup       : {request.pickup}")
    io.say(f"  Drop-off     : {request.dropoff}")
    io.say(f"  Recipient    : {request.recipient.name} "
           f"({request.recipient.phone})")
    io.say(f"  Service      : {request.service_level.value}")
    io.say(f"  Tracking code: {request.tracking_code}")
    io.say("  Parcels:")
    for parcel in request.parcels:
        io.say(f"    {parcel.label}: {parcel.description}, size "
               f"{parcel.size.value}, {parcel.weight_kg:g} kg - held by "
               f"{request.current_holder(parcel.label)}")
    if request.quote:
        quote = request.quote
        io.say(f"  Quote        : ${quote.fee:.2f} for {quote.distance_km} "
               f"km; pickup {format_datetime(quote.estimated_pickup)}, "
               f"delivery by {format_datetime(quote.estimated_delivery)}")
        if quote.adjustment_reason:
            io.say(f"                 adjusted from ${quote.suggested_fee:.2f}"
                   f": {quote.adjustment_reason}")
        if request.status is DeliveryStatus.QUOTED:
            io.say(f"                 valid until "
                   f"{format_datetime(quote.valid_until)}")
    if request.payment:
        payment = request.payment
        io.say(f"  Payment      : {payment.method.value}, "
               f"${payment.amount:.2f}, {payment.status.value}"
               + (f" (ref {payment.reference})" if payment.reference else "")
               + (f", {payment.card_type}" if payment.card_type else ""))
        if payment.note:
            io.say(f"                 recorded by "
                   f"{dms.user_name(payment.recorded_by)}: {payment.note}")
    io.say(f"  Driver       : {dms.user_name(request.assigned_staff_id)}"
           if request.assigned_staff_id else "  Driver       : not allocated")
    io.say("  History:")
    for change in request.history:
        note = f" - {change.note}" if change.note else ""
        io.say(f"    {format_datetime(change.at)}  {change.status.value:<10}"
               f" by {dms.user_name(change.by_user_id)}{note}")


def show_invoice(io: ConsoleIO, invoice: Invoice) -> None:
    """Print an invoice (A2 CRC 4.15)."""
    io.heading(f"Invoice {invoice.invoice_number}")
    io.say(f"  Issued    : {format_datetime(invoice.issued_at)}")
    io.say(f"  Bill to   : {invoice.bill_to}")
    io.say(f"  Delivery  : {invoice.request_id}")
    io.table(["Description", "Amount"],
             [[text, f"${amount:.2f}"] for text, amount in invoice.lines]
             + [["TOTAL", f"${invoice.total:.2f}"]])
    paid = (f", paid {format_datetime(invoice.paid_at)}"
            if invoice.paid_at else "")
    card = f" ({invoice.card_type})" if invoice.card_type else ""
    io.say(f"  Payment   : {invoice.method}{card} - {invoice.status}{paid}")
    if invoice.reference:
        io.say(f"  Reference : {invoice.reference}")


def show_tracking(io: ConsoleIO, request: DeliveryRequest) -> None:
    """Print the limited view a recipient sees (F-1): no client data."""
    io.heading(f"Tracking {request.tracking_code}")
    io.say(f"  For          : {request.recipient.name}")
    io.say(f"  Delivering to: {request.dropoff.suburb} "
           f"{request.dropoff.postcode}")
    io.say(f"  Status       : {request.status.value}")
    if request.quote and not request.status.is_closed:
        io.say(f"  Expected by  : "
               f"{format_datetime(request.quote.estimated_delivery)}")
    io.say(f"  Last update  : {format_datetime(request.history[-1].at)}")
    if request.payment and request.payment.collected_by_driver and \
            not request.status.is_closed:
        io.say(f"  Cash due on delivery: ${request.payment.amount:.2f}")
