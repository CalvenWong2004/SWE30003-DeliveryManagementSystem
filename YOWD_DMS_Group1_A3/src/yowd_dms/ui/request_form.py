"""The delivery request form with review, edit and cancel (SRS 4.3)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..app.dms import DeliveryManagementSystem
from ..domain.delivery import Address, Parcel, ParcelSize, ServiceLevel
from ..domain.users import validate_phone
from .console_io import Cancelled, ConsoleIO, non_blank, weight


@dataclass
class RequestDraft:
    """The values entered so far, kept until the user submits."""

    pickup: Address | None = None
    dropoff: Address | None = None
    recipient_name: str = ""
    recipient_phone: str = ""
    parcels: list[Parcel] = field(default_factory=list)
    level: ServiceLevel = ServiceLevel.STANDARD


class RequestForm:
    """Collects a request, lets the user review and change it, then submits."""

    def __init__(self, io: ConsoleIO, dms: DeliveryManagementSystem) -> None:
        self.io = io
        self.dms = dms

    def run(self, client_id: str | None = None) -> None:
        """Run the form; ``client_id`` is set when office staff log it."""
        io = self.io
        io.heading("Request a delivery   (type q at any prompt to cancel)")
        draft = RequestDraft()
        try:
            draft.pickup = self._address("Pickup")
            draft.dropoff = self._address("Drop-off")
            self._recipient(draft)
            self._parcels(draft)
            self._level(draft)
            while True:
                self._review(draft)
                choice = io.choose("Choose", {
                    "S": "Submit request", "E": "Edit a section",
                    "C": "Cancel without saving"})
                if choice == "C":
                    io.say("  Request discarded. Nothing was saved.")
                    return
                if choice == "E":
                    self._edit(draft)
                    continue
                try:
                    request = self.dms.request_delivery(
                        draft.pickup, draft.dropoff, draft.recipient_name,
                        draft.recipient_phone, draft.parcels, draft.level,
                        client_id)
                except Exception as exc:  # shown, then the user edits
                    io.error(str(exc))
                    continue
                io.success(f"Request {request.request_id} recorded with "
                           f"status {request.status.value}.")
                io.say(f"  Recipient tracking code: {request.tracking_code}")
                io.say("  Office staff will send a quote shortly.")
                return
        except Cancelled:
            io.say("  Request cancelled. Nothing was saved.")

    # -- sections -------------------------------------------------------------
    def _address(self, role: str) -> Address:
        io = self.io
        io.say(f"\n  {role} address (Greater Melbourne only)")
        while True:
            street = io.ask("Street", non_blank("Street", 100),
                            "e.g. 12 John St")
            suburb = io.ask("Suburb", non_blank("Suburb", 60),
                            "e.g. Hawthorn")
            postcode = io.ask("Postcode", self._postcode, "4 digits")
            address = Address(street, suburb.title(), postcode)
            result = self.dms.check_address(address)
            if result.accepted:
                io.success(f"{role}: {address}")
                return address
            io.error(f"{role} address: {result.message} Please re-enter.")

    @staticmethod
    def _postcode(raw: str) -> str:
        if not (raw.isdigit() and len(raw) == 4):
            raise ValueError("Postcode must be exactly 4 digits.")
        return raw

    def _recipient(self, draft: RequestDraft) -> None:
        self.io.say("\n  Recipient")
        name = self.io.ask("Recipient name", non_blank("Recipient name"))
        phone = self.io.ask("Recipient phone", validate_phone,
                            "e.g. 0412 345 678")
        draft.recipient_name, draft.recipient_phone = name, phone

    def _parcels(self, draft: RequestDraft) -> None:
        io = self.io
        parcels: list[Parcel] = []
        io.say("\n  Parcels (all travel together with one driver)")
        while True:
            label = f"P{len(parcels) + 1}"
            description = io.ask(f"{label} description",
                                 non_blank("Description"), "e.g. Documents")
            size = ParcelSize(io.choose(f"{label} size", {
                "S": "Small (fits a satchel)", "M": "Medium (shoebox)",
                "L": "Large (up to 60 cm)"}))
            while True:
                kg = io.ask(f"{label} weight", weight, "kg, max 30")
                try:
                    parcels.append(Parcel(label, description, size, kg))
                    break
                except Exception as exc:
                    io.error(str(exc))
            if len(parcels) >= 10 or not io.confirm("Add another parcel?"):
                draft.parcels = parcels
                return

    def _level(self, draft: RequestDraft) -> None:
        choice = self.io.choose("Service level", {
            "1": "Standard (same day)", "2": "Express (priority, dearer)"})
        draft.level = (ServiceLevel.STANDARD if choice == "1"
                       else ServiceLevel.EXPRESS)

    # -- review and edit ------------------------------------------------------
    def _review(self, draft: RequestDraft) -> None:
        io = self.io
        io.heading("Please check these details before submitting")
        io.say(f"  1. Pickup   : {draft.pickup}")
        io.say(f"  2. Drop-off : {draft.dropoff}")
        io.say(f"  3. Recipient: {draft.recipient_name}, "
               f"{draft.recipient_phone}")
        io.say("  4. Parcels  : " + "; ".join(
            f"{p.label} {p.description} {p.size.value} {p.weight_kg:g} kg"
            for p in draft.parcels))
        io.say(f"  5. Service  : {draft.level.value}")

    def _edit(self, draft: RequestDraft) -> None:
        section = self.io.choose("Section to change", {
            "1": "Pickup", "2": "Drop-off", "3": "Recipient",
            "4": "Parcels", "5": "Service level"})
        try:
            if section == "1":
                draft.pickup = self._address("Pickup")
            elif section == "2":
                draft.dropoff = self._address("Drop-off")
            elif section == "3":
                self._recipient(draft)
            elif section == "4":
                self._parcels(draft)
            else:
                self._level(draft)
        except Cancelled:
            self.io.say("  Edit abandoned; previous values kept.")
