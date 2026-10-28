"""Screens for Delivery Staff: pickup, cash and hand-over (F-2)."""

from __future__ import annotations

from ..app.dms import DeliveryManagementSystem
from ..domain.delivery import DeliveryStatus
from ..domain.payment import PaymentStatus
from .account_screens import my_details
from .console_io import ConsoleIO, money, non_blank
from .views import show_detail, show_list


class DriverScreens:
    """Menu and tasks for a logged-in driver."""

    menu = {"1": "My deliveries", "2": "Record pickup",
            "3": "Complete a delivery (cash and hand-over)",
            "4": "My details", "0": "Log out"}

    def __init__(self, io: ConsoleIO, dms: DeliveryManagementSystem) -> None:
        self.io = io
        self.dms = dms

    def handle(self, choice: str) -> None:
        """Run the task for a menu choice."""
        {"1": self.my_deliveries, "2": self.pickup,
         "3": self.complete,
         "4": lambda: my_details(self.io, self.dms)}[choice]()

    def my_deliveries(self) -> None:
        """List open allocations and show one in full."""
        self.io.heading("My deliveries")
        mine = self.dms.my_assignments()
        show_list(self.io, self.dms, mine)
        if mine:
            show_detail(self.io, self.dms,
                        self.dms.view_delivery(self.io.ask("Delivery ID")))

    def pickup(self) -> None:
        """Confirm every parcel has been collected."""
        io = self.io
        io.heading("Record pickup")
        ready = [r for r in self.dms.my_assignments()
                 if r.status is DeliveryStatus.ALLOCATED]
        show_list(io, self.dms, ready)
        if not ready:
            return
        request = self.dms.view_delivery(io.ask("Delivery ID"))
        for parcel in request.parcels:
            io.say(f"   {parcel.label}: {parcel.description}, "
                   f"{parcel.weight_kg:g} kg")
        if io.confirm("All parcels listed above collected?"):
            self.dms.record_pickup(request.request_id)
            io.success(f"{request.request_id} picked up; you now hold "
                       f"{len(request.parcels)} parcel(s).")

    def complete(self) -> None:
        """Collect cash if due, then record who received the parcels."""
        io = self.io
        io.heading("Complete a delivery")
        on_road = [r for r in self.dms.my_assignments()
                   if r.status is DeliveryStatus.PICKED_UP]
        show_list(io, self.dms, on_road)
        if not on_road:
            return
        request = self.dms.view_delivery(io.ask("Delivery ID"))
        payment = request.payment
        if payment and payment.collected_by_driver and \
                payment.status is PaymentStatus.DUE:
            io.say(f"  Cash due: ${payment.amount:.2f}")
            while True:
                amount = io.ask("Cash received", money, "$")
                try:
                    self.dms.collect_cash(request.request_id, amount)
                    io.success("Cash recorded; payment is Paid.")
                    break
                except Exception as exc:
                    io.error(str(exc))
        received_by = io.ask("Received by (name)", non_blank("Name"),
                             default=request.recipient.name)
        self.dms.record_handover(request.request_id, received_by)
        io.success(f"{request.request_id} delivered to {received_by}.")
