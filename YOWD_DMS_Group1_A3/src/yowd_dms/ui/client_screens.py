"""Screens for the Client role (SRS 4.1, 4.3, 4.4, 4.6)."""

from __future__ import annotations

from ..app.dms import DeliveryManagementSystem
from ..domain.delivery import DeliveryStatus
from ..domain.payment import (OnlinePayment, PaymentDeclined, PaymentMethod,
                              PaymentStatus)
from .account_screens import my_details
from .console_io import ConsoleIO, non_blank
from .request_form import RequestForm
from .views import show_detail, show_invoice, show_list


class ClientScreens:
    """Menu and tasks available to a logged-in client."""

    def __init__(self, io: ConsoleIO, dms: DeliveryManagementSystem,
                 demo_mode: bool = False) -> None:
        self.io = io
        self.dms = dms
        self.demo_mode = demo_mode

    @property
    def menu(self) -> dict[str, str]:
        """Return the client menu (demo mode adds a test-card switch)."""
        items = {"1": "Request a delivery", "2": "My deliveries",
                 "3": "Respond to a quote", "4": "Pay for a delivery",
                 "5": "Cancel a request", "6": "View an invoice",
                 "7": "My details"}
        if self.demo_mode:
            items["T"] = "Demo only: decline the next card payment"
        items["0"] = "Log out"
        return items

    def handle(self, choice: str) -> None:
        """Run the task for a menu choice."""
        {"1": lambda: RequestForm(self.io, self.dms).run(),
         "2": self.my_deliveries, "3": self.respond_to_quote,
         "4": self.pay, "5": self.cancel, "6": self.invoice,
         "7": lambda: my_details(self.io, self.dms),
         "T": self.decline_next_card}[choice]()

    def my_deliveries(self) -> None:
        """List the client's requests and open one."""
        self.io.heading("My deliveries")
        requests = self.dms.my_requests()
        show_list(self.io, self.dms, requests)
        if requests:
            request_id = self.io.ask("Delivery ID to view", default="none",
                                     hint="or Enter to go back")
            if request_id != "none":
                show_detail(self.io, self.dms,
                            self.dms.view_delivery(request_id))

    def respond_to_quote(self) -> None:
        """Accept or decline an open quote (SRS 4.6)."""
        io = self.io
        io.heading("Respond to a quote")
        quoted = [r for r in self.dms.my_requests()
                  if r.status is DeliveryStatus.QUOTED]
        show_list(io, self.dms, quoted)
        if not quoted:
            io.say("  You have no quotes waiting for an answer.")
            return
        request = self.dms.view_delivery(io.ask("Delivery ID"))
        show_detail(io, self.dms, request)
        choice = io.choose("Your answer", {"A": "Accept the quote",
                                           "D": "Decline the quote"})
        if choice == "D":
            if io.confirm("Decline and close this request?"):
                self.dms.respond_to_quote(request.request_id, False)
                io.success(f"{request.request_id} declined and closed.")
            return
        method = io.choose("Payment method", {
            "1": "Pay online now by card",
            "2": "Cash on delivery to the driver"})
        method = (PaymentMethod.ONLINE if method == "1"
                  else PaymentMethod.CASH_ON_DELIVERY)
        self.dms.respond_to_quote(request.request_id, True, method)
        io.success(f"Quote accepted. {request.request_id} is waiting for a "
                   "driver to be allocated.")
        if method is PaymentMethod.ONLINE and \
                io.confirm("Pay the online amount now?"):
            self._pay_online(request.request_id)

    def pay(self) -> None:
        """Settle or change the payment for an accepted delivery."""
        io = self.io
        io.heading("Pay for a delivery")
        due = [r for r in self.dms.my_requests() if r.payment and
               r.payment.status is PaymentStatus.DUE and
               not r.status.is_closed]
        show_list(io, self.dms, due)
        if not due:
            io.say("  Nothing is waiting for payment.")
            return
        request = self.dms.view_delivery(io.ask("Delivery ID"))
        if request.payment is None:
            io.error("That delivery has no payment yet.")
            return
        io.say(f"  Amount due: ${request.payment.amount:.2f} by "
               f"{request.payment.method.value}")
        if isinstance(request.payment, OnlinePayment):
            choice = io.choose("Choose", {"P": "Pay now by card",
                                          "C": "Switch to cash on delivery"})
            if choice == "P":
                self._pay_online(request.request_id)
                return
            self.dms.change_payment_method(request.request_id,
                                           PaymentMethod.CASH_ON_DELIVERY)
            io.success("Payment changed to cash on delivery.")
        elif io.confirm("Switch to paying online now instead?"):
            self.dms.change_payment_method(request.request_id,
                                           PaymentMethod.ONLINE)
            self._pay_online(request.request_id)

    def _pay_online(self, request_id: str) -> None:
        """Charge the card; after a decline offer retry or cash (A2 7.2)."""
        io = self.io
        while True:
            io.say("  [Simulation] Card details are entered on the payment "
                   "provider's page; the DMS never sees them.")
            try:
                reference = self.dms.pay_online(request_id)
                break
            except PaymentDeclined as exc:
                io.error(str(exc))
                choice = io.choose("Choose", {
                    "R": "Try the card again",
                    "C": "Switch to cash on delivery",
                    "L": "Decide later (stays Due)"})
                if choice == "C":
                    self.dms.change_payment_method(
                        request_id, PaymentMethod.CASH_ON_DELIVERY)
                    io.success("Payment changed to cash on delivery; pay "
                               "the driver at drop-off.")
                    return
                if choice == "L":
                    io.say("  Payment is still due. Use 'Pay for a "
                           "delivery' when ready.")
                    return
        payment = self.dms.view_delivery(request_id).payment
        card = f" by {payment.card_type}" if payment else ""
        io.success(f"Payment processed (simulated){card}. Reference "
                   f"{reference}.")

    def invoice(self) -> None:
        """Show the invoice for one of the client's deliveries."""
        io = self.io
        io.heading("View an invoice")
        billed = [r for r in self.dms.my_requests() if r.payment]
        show_list(io, self.dms, billed)
        if not billed:
            io.say("  An invoice is available once you accept a quote.")
            return
        show_invoice(io, self.dms.invoice(io.ask("Delivery ID")))

    def decline_next_card(self) -> None:
        """Demo only: make the simulated provider refuse the next card."""
        self.dms.simulate_card_decline()
        self.io.success("Demo: the next card payment will be declined.")

    def cancel(self) -> None:
        """Cancel a request before pickup."""
        io = self.io
        io.heading("Cancel a request")
        open_requests = [r for r in self.dms.my_requests()
                         if not r.status.is_closed and
                         r.status is not DeliveryStatus.PICKED_UP]
        show_list(io, self.dms, open_requests)
        if not open_requests:
            return
        request_id = io.ask("Delivery ID")
        reason = io.ask("Reason", non_blank("Reason", 200))
        if io.confirm(f"Cancel {request_id.upper()}?"):
            self.dms.cancel_request(request_id, reason)
            io.success(f"{request_id.upper()} cancelled. It stays in your "
                       "history.")
