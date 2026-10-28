"""ConsoleApp: the text user interface's main loop and role routing."""

from __future__ import annotations

from ..app.dms import DeliveryManagementSystem
from ..domain.common import DomainError, format_datetime
from ..domain.payment import PaymentDeclined
from ..domain.users import (BusinessOwner, Client, DeliveryStaff,
                            OfficeStaff, validate_email, validate_phone)
from .client_screens import ClientScreens
from .console_io import Cancelled, ConsoleIO, non_blank, password
from .driver_screens import DriverScreens
from .office_screens import OfficeScreens
from .views import show_tracking


class ConsoleApp:
    """Shows the start menu, logs users in and routes to role menus."""

    def __init__(self, dms: DeliveryManagementSystem,
                 demo_mode: bool = False) -> None:
        self.io = ConsoleIO()
        self.dms = dms
        self.demo_mode = demo_mode

    def run(self) -> None:
        """Run until the user exits or input ends."""
        io = self.io
        try:
            if self.dms.needs_owner_setup():
                self._guard(self._setup_owner)
            while True:
                now = format_datetime(self.dms.clock.now())
                io.heading("Your Order We Deliver - Delivery Management "
                           f"System   [{now}]")
                choice = io.choose("Choose", {
                    "1": "Log in", "2": "Register as a new client",
                    "3": "Track a parcel (recipients)", "0": "Exit"})
                if choice == "0":
                    io.say("  Goodbye.")
                    return
                self._guard({"1": self._login, "2": self._register,
                             "3": self._track}[choice])
                if self.dms.current_user is not None:
                    self._role_menu()
        except Cancelled:
            io.say("\n  Input ended. Goodbye.")

    def _guard(self, task) -> None:
        """Run ``task``; report business errors instead of crashing."""
        try:
            task()
        except Cancelled:
            self.io.say("  Cancelled; returning to the menu.")
        except (DomainError, PaymentDeclined) as exc:
            self.io.error(str(exc))

    def _role_menu(self) -> None:
        user = self.dms.current_user
        if isinstance(user, Client):
            screens = ClientScreens(self.io, self.dms, self.demo_mode)
        elif isinstance(user, (OfficeStaff, BusinessOwner)):
            screens = OfficeScreens(self.io, self.dms, self.demo_mode)
        elif isinstance(user, DeliveryStaff):
            screens = DriverScreens(self.io, self.dms)
        else:
            raise DomainError("Unknown role.")
        while True:
            self.io.heading(f"{user.role_name} menu - {user.name}")
            try:
                choice = self.io.choose("Choose", screens.menu)
            except Cancelled:
                choice = "0"
            if choice == "0":
                self.dms.logout()
                self.io.success("Logged out.")
                return
            self._guard(lambda: screens.handle(choice))

    # -- start-menu tasks -----------------------------------------------------
    def _setup_owner(self) -> None:
        io = self.io
        io.heading("First run: create the business owner account")
        user = self.dms.setup_owner(
            io.ask("Name", non_blank("Name")),
            io.ask("Email", validate_email),
            io.ask("Phone", validate_phone, "e.g. 0412 345 678"),
            io.ask("Password", password, "8+ characters", secret=True))
        io.success(f"Owner account created for {user.name}.")

    def _login(self) -> None:
        io = self.io
        io.heading("Log in")
        user = self.dms.login(io.ask("Email", non_blank("Email")),
                              io.ask("Password", secret=True))
        io.success(f"Welcome, {user.name} ({user.role_name}).")

    def _register(self) -> None:
        io = self.io
        io.heading("Register as a client   (type q to cancel)")
        while True:
            name = io.ask("Full name", non_blank("Name"))
            email = io.ask("Email", validate_email, "name@example.com")
            phone = io.ask("Phone", validate_phone, "e.g. 0412 345 678")
            secret = io.ask("Password", password, "8+ characters",
                            secret=True)
            try:
                user = self.dms.register_client(name, email, phone, secret)
                break
            except DomainError as exc:
                io.error(f"{exc} Please re-enter your details.")
        io.success(f"Account {user.user_id} created. You can now log in.")

    def _track(self) -> None:
        io = self.io
        io.heading("Track a parcel")
        while True:
            code = io.ask("Tracking code", non_blank("Tracking code"),
                          "8 characters from your SMS")
            try:
                show_tracking(io, self.dms.track(code))
                return
            except DomainError as exc:
                io.error(f"{exc} Check the code and try again.")
