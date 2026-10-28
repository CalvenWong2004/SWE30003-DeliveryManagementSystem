"""Demonstration accounts, so markers can try every role straight away.

Only people and availability are added; no delivery, quote or payment is
invented (A2 section 6.3). All demo passwords are ``Password1``.
"""

from __future__ import annotations

from datetime import timedelta

from .app.dms import DeliveryManagementSystem

DEMO_PASSWORD = "Password1"


def seed_demo(dms: DeliveryManagementSystem) -> None:
    """Add demo users and two days of availability to an empty system."""
    if not dms.needs_owner_setup():
        return
    dms.setup_owner("Olivia Owner", "owner@yowd.com.au", "0390001000",
                    DEMO_PASSWORD)
    dms.login("owner@yowd.com.au", DEMO_PASSWORD)
    dms.add_staff("Office Staff", "Oscar Office", "office@yowd.com.au",
                  "0390001001", DEMO_PASSWORD)
    dana = dms.add_staff("Delivery Staff", "Dana Driver",
                         "dana@yowd.com.au", "0411000001", DEMO_PASSWORD)
    dev = dms.add_staff("Delivery Staff", "Dev Driver", "dev@yowd.com.au",
                        "0411000002", DEMO_PASSWORD)
    today = dms.clock.now().replace(hour=0, minute=0)
    for day in (0, 1):
        start = today + timedelta(days=day, hours=8)
        dms.add_availability(dana.user_id, start, start + timedelta(hours=10))
    # Dev works afternoons only, so allocation can show a conflict.
    for day in (0, 1):
        start = today + timedelta(days=day, hours=13)
        dms.add_availability(dev.user_id, start, start + timedelta(hours=5))
    dms.logout()
    dms.register_client("Chloe Client", "chloe@example.com", "0412345678",
                        DEMO_PASSWORD, "12 John St, Hawthorn VIC 3122")
