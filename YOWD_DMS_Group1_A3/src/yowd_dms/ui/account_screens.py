"""Account maintenance screens shared by every role (A2 CRC 4.8, 4.11).

Users correct their own contact details and password here; office staff and
the owner reuse ``edit_contact`` to update other people's records.
"""

from __future__ import annotations

from ..app.dms import DeliveryManagementSystem
from ..domain.common import PermissionDenied
from ..domain.users import Client, User, validate_phone
from .console_io import ConsoleIO, non_blank, password

ContactChange = tuple[str, str, str | None]


def show_contact(io: ConsoleIO, user: User) -> None:
    """Print a user's current contact details."""
    io.say(f"  ID      : {user.user_id}   ({user.role_name}, "
           f"{user.state.value})")
    io.say(f"  Name    : {user.name}")
    io.say(f"  Email   : {user.email}   (login identity; cannot change)")
    io.say(f"  Phone   : {user.phone}")
    if isinstance(user, Client):
        io.say(f"  Address : {user.default_address or '(none)'}")


def edit_contact(io: ConsoleIO, user: User) -> ContactChange | None:
    """Ask for new details (Enter keeps each one); None if not confirmed."""
    io.say("  Press Enter to keep a value, or type a new one.")
    name = io.ask("Name", non_blank("Name"), default=user.name)
    phone = io.ask("Phone", validate_phone, "e.g. 0412 345 678",
                   default=user.phone)
    address = None
    address_changed = False
    if isinstance(user, Client):
        address = io.ask("Address", str,
                         "street, suburb, postcode, or - to clear",
                         default=user.default_address or "-")
        address = "" if address.strip() == "-" else address
        address_changed = address != user.default_address
    changed = name != user.name or phone != user.phone or address_changed
    if not changed:
        io.say("  Nothing changed.")
        return None
    if not io.confirm("Save these changes?"):
        io.say("  Changes discarded; previous details kept.")
        return None
    return name, phone, address


def my_details(io: ConsoleIO, dms: DeliveryManagementSystem) -> None:
    """View and update the logged-in user's own details."""
    user = dms.current_user
    if user is None:
        raise PermissionDenied("Please log in first.")
    io.heading("My details")
    show_contact(io, user)
    action = io.choose("Action", {"D": "Update my contact details",
                                  "W": "Change my password",
                                  "B": "Back"})
    if action == "D":
        change = edit_contact(io, user)
        if change is not None:
            dms.update_my_details(*change)
            io.success("Your details have been updated.")
    elif action == "W":
        current = io.ask("Current password", secret=True)
        new = io.ask("New password", password, "8+ characters", secret=True)
        if io.ask("Repeat new password", secret=True) != new:
            io.error("The new passwords do not match; nothing changed.")
            return
        dms.change_my_password(current, new)
        io.success("Password changed.")
