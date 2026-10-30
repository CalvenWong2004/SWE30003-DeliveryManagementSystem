"""Screens for Office Staff and the Business Owner (SRS 4.1-4.8, F-4)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..app.dms import DeliveryManagementSystem, Period, no_cover_message
from ..domain.common import DomainError, format_datetime, parse_datetime
from ..domain.payment import PaymentStatus
from ..domain.users import BusinessOwner, validate_email, validate_phone
from .account_screens import edit_contact, my_details, show_contact
from .console_io import ConsoleIO, money, non_blank, password
from .request_form import RequestForm
from .views import show_detail, show_invoice, show_list


def _date(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Date must be YYYY-MM-DD.") from exc


class OfficeScreens:
    """Menu and tasks for office staff; owners get staff management too."""

    def __init__(self, io: ConsoleIO, dms: DeliveryManagementSystem,
                 demo_mode: bool = False) -> None:
        self.io = io
        self.dms = dms
        self.demo_mode = demo_mode

    @property
    def menu(self) -> dict[str, str]:
        """Return the menu for the logged-in role."""
        items = {"1": "Log a request for a client", "2": "Prepare quotes",
                 "3": "Allocate delivery staff",
                 "4": "Manage staff availability", "5": "Find a delivery",
                 "6": "Delivery statistics", "7": "Cancel a request"}
        if isinstance(self.dms.current_user, BusinessOwner):
            items["8"] = "Manage staff (join / leave / update)"
        items["9"] = "Client records (view / update / deactivate)"
        items["10"] = "Payment records and invoices"
        items["11"] = "My details"
        if self.demo_mode:
            items["T"] = "Demo only: advance the clock"
        items["0"] = "Log out"
        return items

    def handle(self, choice: str) -> None:
        """Run the task for a menu choice."""
        {"1": self.log_for_client, "2": self.prepare_quotes,
         "3": self.allocate, "4": self.availability, "5": self.find,
         "6": self.statistics, "7": self.cancel, "8": self.manage_staff,
         "9": self.client_records, "10": self.payment_records,
         "11": lambda: my_details(self.io, self.dms),
         "T": self.advance_clock}[choice]()

    # -- 4.3 variant: logging on a caller's behalf ----------------------------
    def log_for_client(self) -> None:
        """Find or create the caller's client record, then run the form."""
        io = self.io
        io.heading("Log a request for a client")
        matches = self.dms.find_client(io.ask(
            "Find client by name, email or phone", non_blank("Search")))
        io.table(["ID", "Name", "Email", "Phone"],
                 [[c.user_id, c.name, c.email, c.phone] for c in matches])
        choice = io.choose("Choose", {"U": "Use a client listed above",
                                      "N": "Create a new client record"})
        if choice == "U" and matches:
            client_id = io.ask("Client ID").upper()
        else:
            client = self.dms.create_client_on_behalf(
                io.ask("Client name", non_blank("Name")),
                io.ask("Client email", validate_email),
                io.ask("Client phone", validate_phone, "e.g. 0412 345 678"))
            io.success(f"Client record {client.user_id} created.")
            client_id = client.user_id
        RequestForm(io, self.dms).run(client_id)

    # -- 4.5 ------------------------------------------------------------------
    def prepare_quotes(self) -> None:
        """Price a waiting request, or decline it with a reason."""
        io = self.io
        io.heading("Prepare delivery quotes")
        waiting = self.dms.requests_to_quote()
        show_list(io, self.dms, waiting)
        if not waiting:
            io.say("  No requests are waiting for a quote.")
            return
        request = self.dms.view_delivery(io.ask("Delivery ID to quote"))
        show_detail(io, self.dms, request)
        action = io.choose("Action", {"Q": "Prepare a quote",
                                      "D": "Decline (cannot be carried)"})
        if action == "D":
            reason = io.ask("Reason", non_blank("Reason", 200))
            self.dms.decline_request(request.request_id, reason)
            io.success(f"{request.request_id} declined: {reason}")
            return
        default_pickup = format_datetime(
            self.dms.clock.now() + timedelta(hours=2))
        while True:
            pickup = io.ask("Estimated pickup", parse_datetime,
                            "YYYY-MM-DD HH:MM", default=default_pickup)
            try:
                suggestion = self.dms.suggest_quote(request.request_id,
                                                    pickup)
                break
            except Exception as exc:
                io.error(str(exc))
        io.say(f"  Distance          : {suggestion.distance_km} km")
        io.say(f"  Suggested fee     : ${suggestion.suggested_fee:.2f} "
               f"({request.service_level.value})")
        io.say(f"  Delivery by       : "
               f"{format_datetime(pickup + suggestion.window)}")
        io.say(f"  Already committed : {suggestion.committed_deliveries} "
               f"deliveries; {suggestion.free_staff} driver(s) free then")
        if suggestion.free_staff == 0:
            io.error("No driver is free for that window; consider a later "
                     "pickup time.")
        fee = None
        reason = ""
        if io.confirm("Change the suggested fee?"):
            fee = io.ask("Fee", money, "$")
            reason = io.ask("Reason for the change", non_blank("Reason"))
        quote = self.dms.issue_quote(request.request_id, pickup, fee, reason)
        io.success(f"Quote of ${quote.fee:.2f} sent to the client; valid "
                   f"until {format_datetime(quote.valid_until)}.")

    # -- 4.7 ------------------------------------------------------------------
    def allocate(self) -> None:
        """Allocate an available driver to an accepted request."""
        io = self.io
        io.heading("Allocate delivery staff")
        waiting = self.dms.awaiting_allocation()
        show_list(io, self.dms, waiting)
        if not waiting:
            io.say("  No accepted deliveries are waiting for a driver.")
            return
        request = self.dms.view_delivery(io.ask("Delivery ID"))
        period = request.committed_period()
        if period is None:
            io.error(f"{request.request_id} has no quoted window yet.")
            return
        start, end = period
        io.say(f"  Window: {format_datetime(start)} to "
               f"{format_datetime(end)}")
        free = self.dms.available_staff_for(request.request_id)
        io.table(["ID", "Driver", "Open deliveries"],
                 [[s.user_id, s.name,
                   str(len(self.dms.assignments_of(s.user_id)))]
                  for s in free])
        if not free:
            io.error("No delivery staff are available for this window. "
                     "Add availability or re-quote a later time.")
            return
        staff_id = io.ask("Driver ID")
        self.dms.allocate(request.request_id, staff_id)
        io.success(f"{request.request_id} allocated to "
                   f"{self.dms.user_name(staff_id.upper())}.")

    # -- 4.8 ------------------------------------------------------------------
    def availability(self) -> None:
        """View, add or remove a driver's availability."""
        io = self.io
        io.heading("Manage staff availability")
        drivers = self.dms.active_drivers()
        io.table(["ID", "Driver", "Availability periods"],
                 [[d.user_id, d.name,
                   str(len(self.dms.availability_of(d.user_id)))]
                  for d in drivers])
        if not drivers:
            return
        staff_id = io.ask("Driver ID").upper()
        io.say(f"\n  Availability of {self.dms.user_name(staff_id)}:")
        io.table(["ID", "Available"],
                 [[a.availability_id, str(a)]
                  for a in self.dms.availability_of(staff_id)])
        io.say("  Open deliveries:")
        show_list(io, self.dms, self.dms.assignments_of(staff_id))
        action = io.choose("Action", {"A": "Add a period",
                                      "R": "Remove a period",
                                      "B": "Back"})
        if action == "A":
            while True:
                start = io.ask("From", parse_datetime, "YYYY-MM-DD HH:MM")
                end = io.ask("To", parse_datetime, "YYYY-MM-DD HH:MM")
                try:
                    item = self.dms.add_availability(staff_id, start, end)
                    break
                except DomainError as exc:
                    io.error(f"{exc} Please re-enter the period.")
            io.success(f"Added {item.availability_id}: {item}.")
        elif action == "R":
            availability_id = io.ask("Availability ID").upper()
            conflicts = self.dms.availability_conflicts(availability_id)
            if conflicts:
                io.error("Warning - scheduling conflict: "
                         f"{', '.join(conflicts)} would lose its driver "
                         "and need re-allocation.")
            gaps = self.dms.availability_cover_gaps(availability_id)
            if gaps:
                io.error(f"Warning - no cover: {no_cover_message(gaps)}")
            if io.confirm("Remove this period?"):
                released = self.dms.remove_availability(
                    availability_id, allow_no_cover=True)
                io.success(f"{availability_id} removed."
                           + (f" Returned for re-allocation: "
                              f"{', '.join(released)}." if released else ""))

    # -- 4.1 ------------------------------------------------------------------
    def find(self) -> None:
        """Search by ID, name, phone or address and open one result."""
        io = self.io
        io.heading("Find a delivery")
        while True:
            results = self.dms.search_deliveries(io.ask(
                "Search", non_blank("Search"),
                "ID, client name, phone or address"))
            if results:
                break
            io.error("No matching delivery exists. Try another search.")
        show_list(io, self.dms, results)
        request_id = (results[0].request_id if len(results) == 1
                      else io.ask("Delivery ID to view"))
        show_detail(io, self.dms, self.dms.view_delivery(request_id))

    # -- 4.2 ------------------------------------------------------------------
    def statistics(self) -> None:
        """Show counts for a period, for everyone or one driver."""
        io = self.io
        io.heading("Delivery statistics")
        periods = {str(i + 1): p.value for i, p in enumerate(Period)}
        period = list(Period)[int(io.choose("Period", periods)) - 1]
        reference = io.ask("Reference date", _date, "YYYY-MM-DD",
                           default=self.dms.clock.now().date().isoformat())
        io.table(["ID", "Driver", "Account"],
                 [[d.user_id, d.name, d.state.value]
                  for d in self.dms.all_drivers()])
        staff_id = io.ask("Driver ID", default="all",
                          hint="or Enter for all")
        stats = self.dms.statistics(period, reference,
                                    None if staff_id == "all" else staff_id)
        who = "all staff" if staff_id == "all" else \
            self.dms.user_name(staff_id.upper())
        io.say(f"\n  {period.value} {stats.first_day} to {stats.last_day}, "
               f"{who}")
        rows = [["Delivered", str(stats.delivered)]]
        if staff_id == "all":
            rows += [["Declined", str(stats.declined)],
                     ["Expired quotes", str(stats.expired)],
                     ["Cancelled", str(stats.cancelled)]]
        rows.append(["Revenue (delivered)", f"${stats.revenue:.2f}"])
        io.table(["Measure", "Value"], rows)
        if stats.delivered == 0:
            io.say("  No deliveries were completed in this period.")

    def cancel(self) -> None:
        """Cancel any request before pickup."""
        io = self.io
        io.heading("Cancel a request")
        request_id = io.ask("Delivery ID")
        show_detail(io, self.dms, self.dms.view_delivery(request_id))
        reason = io.ask("Reason", non_blank("Reason", 200))
        if io.confirm("Cancel this request?"):
            self.dms.cancel_request(request_id, reason)
            io.success(f"{request_id.upper()} cancelled.")

    # -- F-4 (owner only) -----------------------------------------------------
    def manage_staff(self) -> None:
        """Add a staff member who joins, or deactivate one who leaves."""
        io = self.io
        io.heading("Manage staff")
        io.table(["ID", "Name", "Role", "Email", "Account"],
                 [[u.user_id, u.name, u.role_name, u.email, u.state.value]
                  for u in self.dms.staff_list()])
        action = io.choose("Action", {"A": "Add a staff member",
                                      "U": "Update a staff member's details",
                                      "D": "Deactivate a leaver",
                                      "B": "Back"})
        if action == "A":
            role = io.choose("Role", {"1": "Office Staff",
                                      "2": "Delivery Staff"})
            user = self.dms.add_staff(
                "Office Staff" if role == "1" else "Delivery Staff",
                io.ask("Name", non_blank("Name")),
                io.ask("Email", validate_email),
                io.ask("Phone", validate_phone, "e.g. 0412 345 678"),
                io.ask("Temporary password", password, "8+ characters",
                       secret=True))
            io.success(f"{user.name} added as {user.role_name} "
                       f"({user.user_id}).")
        elif action == "U":
            user = self.dms.find_user(io.ask("Staff ID"))
            show_contact(io, user)
            change = edit_contact(io, user)
            if change is not None:
                self.dms.update_user_details(user.user_id, *change)
                io.success(f"{user.name}'s details updated.")
        elif action == "D":
            user_id = io.ask("Staff ID").upper()
            gaps = self.dms.staff_cover_gaps(user_id)
            if gaps:
                io.error(f"Warning - no cover: {no_cover_message(gaps)}")
            if io.confirm(f"Deactivate {self.dms.user_name(user_id)}?"):
                released = self.dms.deactivate_staff(user_id,
                                                     allow_no_cover=True)
                io.success("Account deactivated; history kept for "
                           "statistics.")
                if released:
                    io.say("  Returned for re-allocation: "
                           + ", ".join(released))

    # -- A2 CRC 4.8 #2 and 4.13 #4 --------------------------------------------
    def client_records(self) -> None:
        """Find a client, then update or deactivate their record."""
        io = self.io
        io.heading("Client records")
        search = io.ask("Find client", str, "name, email, phone or ID",
                        default="all")
        clients = self.dms.client_records("" if search == "all" else search)
        io.table(["ID", "Name", "Email", "Phone", "Account"],
                 [[c.user_id, c.name, c.email, c.phone, c.state.value]
                  for c in clients])
        if not clients:
            io.say("  No client matches that search.")
            return
        action = io.choose("Action", {"U": "Update a client's details",
                                      "D": "Deactivate a client",
                                      "B": "Back"})
        if action == "B":
            return
        user = self.dms.find_user(io.ask("Client ID"))
        show_contact(io, user)
        if action == "U":
            change = edit_contact(io, user)
            if change is not None:
                self.dms.update_user_details(user.user_id, *change)
                io.success(f"{user.name}'s details updated.")
        elif io.confirm(f"Deactivate {user.name}? History is kept."):
            self.dms.deactivate_client(user.user_id)
            io.success(f"{user.name} deactivated; they can no longer log "
                       "in.")
        else:
            io.say(f"  {user.name} stays active.")

    # -- A2 CRC 4.13 #6, 4.14 #2, 4.15 #2 -------------------------------------
    def payment_records(self) -> None:
        """List payments, show an invoice or record an outside payment."""
        io = self.io
        io.heading("Payment records")
        filters = {"1": "All", "2": "Due", "3": "Paid", "4": "Cancelled"}
        choice = io.choose("Show", filters)
        status = None if choice == "1" else PaymentStatus(filters[choice])
        records = [(r, r.payment) for r in self.dms.payment_records(status)
                   if r.payment is not None]
        io.table(["Delivery", "Client", "Method", "Amount", "Status",
                  "Reference", "Paid at"],
                 [[r.request_id, self.dms.client_name(r.client_id),
                   p.method.value
                   + (f" ({p.card_type})" if p.card_type else ""),
                   f"${p.amount:.2f}", p.status.value, p.reference or "-",
                   format_datetime(p.paid_at) if p.paid_at else "-"]
                  for r, p in records])
        paid = sum(p.amount for _, p in records
                   if p.status is PaymentStatus.PAID)
        io.say(f"  {len(records)} payment(s); ${paid:.2f} received.")
        if not records:
            return
        action = io.choose("Action", {"I": "View an invoice",
                                      "M": "Mark a due payment as paid",
                                      "B": "Back"})
        if action == "I":
            show_invoice(io, self.dms.invoice(io.ask("Delivery ID")))
        elif action == "M":
            request = self.dms.view_delivery(io.ask("Delivery ID"))
            io.say("  Use this only for a payment settled outside the DMS, "
                   "e.g. paid at the office.")
            note = io.ask("Note", non_blank("Note", 200),
                          "how and when it was paid")
            if io.confirm(f"Mark {request.request_id} as paid?"):
                self.dms.record_payment_manually(request.request_id, note)
                io.success(f"{request.request_id} payment recorded as "
                           "paid.")
            else:
                io.say("  Not recorded; the payment is still Due.")

    def advance_clock(self) -> None:
        """Demo only: move time on to show quote expiry and lockout."""
        hours = self.io.ask("Advance by hours", int, "e.g. 25")
        self.dms.clock.advance(timedelta(hours=hours))
        self.io.success(f"Clock is now "
                        f"{format_datetime(self.dms.clock.now())}.")
