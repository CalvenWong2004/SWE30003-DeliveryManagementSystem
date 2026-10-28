"""Acceptance-style unit tests traced to SRS Section 10.3 criteria.

Run from the project root with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yowd_dms.app.dms import DeliveryManagementSystem  # noqa: E402
from yowd_dms.demo_data import DEMO_PASSWORD, seed_demo  # noqa: E402
from yowd_dms.domain.common import (Clock, IntegrityError,  # noqa: E402
                                    PermissionDenied, StateError,
                                    ValidationError)
from yowd_dms.domain.delivery import (Address, DeliveryStatus,  # noqa: E402
                                      Parcel, ParcelSize, ServiceLevel)
from yowd_dms.domain.payment import (SIMULATED_CARD_TYPES,  # noqa: E402
                                     PaymentDeclined, PaymentMethod,
                                     PaymentStatus, SimulatedPaymentGateway)
from yowd_dms.registry.delivery_registry import Period  # noqa: E402

START = datetime(2026, 10, 27, 8, 0)
HAWTHORN = Address("12 John St", "Hawthorn", "3122")
BOX_HILL = Address("5 Station St", "Box Hill", "3128")


class DmsTestCase(unittest.TestCase):
    """Starts a fresh, seeded system in a temporary folder per test."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.clock = Clock(lambda: START)
        self.gateway = SimulatedPaymentGateway()
        self.dms = self._start()
        seed_demo(self.dms)

    def tearDown(self) -> None:
        DeliveryManagementSystem.reset()
        self.folder.cleanup()

    def _start(self) -> DeliveryManagementSystem:
        DeliveryManagementSystem.reset()
        return DeliveryManagementSystem.start(Path(self.folder.name),
                                              self.clock, self.gateway)

    def login(self, who: str) -> None:
        self.dms.logout()
        self.dms.login(f"{who}@" + ("example.com" if who == "chloe"
                                    else "yowd.com.au"), DEMO_PASSWORD)

    def new_request(self, kg: float = 2.0) -> str:
        self.login("chloe")
        request = self.dms.request_delivery(
            HAWTHORN, BOX_HILL, "Ravi Recipient", "0422 111 222",
            [Parcel("P1", "Documents", ParcelSize.SMALL, kg)],
            ServiceLevel.STANDARD)
        return request.request_id

    def quoted_request(self, pickup_hour: int = 9) -> str:
        request_id = self.new_request()
        self.login("office")
        self.dms.issue_quote(request_id, START.replace(hour=pickup_hour),
                             None)
        return request_id

    def accepted_request(self, method=PaymentMethod.CASH_ON_DELIVERY,
                         pickup_hour: int = 9) -> str:
        request_id = self.quoted_request(pickup_hour)
        self.login("chloe")
        self.dms.respond_to_quote(request_id, True, method)
        return request_id

    def allocated_request(self, driver_id: str = "U-0003") -> str:
        request_id = self.accepted_request()
        self.login("office")
        self.dms.allocate(request_id, driver_id)
        return request_id


class SecurityTests(DmsTestCase):
    """SRS 7.1: lockout and role-based access."""

    def test_five_failures_lock_account_for_15_minutes(self) -> None:
        for _ in range(5):
            with self.assertRaises(PermissionDenied):
                self.dms.login("chloe@example.com", "wrong-password")
        with self.assertRaisesRegex(PermissionDenied, "locked"):
            self.dms.login("chloe@example.com", DEMO_PASSWORD)
        self.clock.advance(timedelta(minutes=15))
        self.assertEqual(
            self.dms.login("chloe@example.com", DEMO_PASSWORD).name,
            "Chloe Client")

    def test_client_cannot_prepare_quotes_or_see_others(self) -> None:
        request_id = self.new_request()
        with self.assertRaises(PermissionDenied):
            self.dms.requests_to_quote()
        self.dms.register_client("Other Person", "other@example.com",
                                 "0400000009", DEMO_PASSWORD)
        self.dms.logout()
        self.dms.login("other@example.com", DEMO_PASSWORD)
        with self.assertRaises(Exception):
            self.dms.view_delivery(request_id)

    def test_driver_cannot_allocate(self) -> None:
        self.login("dana")
        with self.assertRaises(PermissionDenied):
            self.dms.awaiting_allocation()


class RequestTests(DmsTestCase):
    """SRS 4.3 and 4.1."""

    def test_valid_request_is_requested_with_unique_ids(self) -> None:
        first = self.dms.view_delivery(self.new_request())
        second = self.dms.view_delivery(self.new_request())
        self.assertEqual(first.status, DeliveryStatus.REQUESTED)
        self.assertNotEqual(first.request_id, second.request_id)
        self.assertNotEqual(first.tracking_code, second.tracking_code)

    def test_out_of_area_address_rejected_at_entry(self) -> None:
        self.login("chloe")
        with self.assertRaisesRegex(ValidationError, "outside"):
            self.dms.request_delivery(
                HAWTHORN, Address("1 George St", "Sydney", "2000", "NSW"),
                "Ravi", "0422111222",
                [Parcel("P1", "Box", ParcelSize.SMALL, 1)],
                ServiceLevel.STANDARD)

    def test_suburb_must_match_postcode(self) -> None:
        self.assertFalse(self.dms.check_address(
            Address("1 A St", "Richmond", "3122")).accepted)

    def test_overweight_parcel_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Parcel("P1", "Piano", ParcelSize.LARGE, 31)

    def test_search_by_name_phone_and_address(self) -> None:
        request_id = self.new_request()
        self.login("office")
        for text in ("chloe", "0412345678", "box hill", request_id):
            self.assertEqual(
                [r.request_id for r in self.dms.search_deliveries(text)],
                [request_id])
        self.assertEqual(self.dms.search_deliveries("nobody"), [])


class QuoteTests(DmsTestCase):
    """SRS 4.5 and 4.6."""

    def test_adjusted_fee_needs_reason(self) -> None:
        request_id = self.new_request()
        self.login("office")
        with self.assertRaisesRegex(ValidationError, "reason"):
            self.dms.issue_quote(request_id, START.replace(hour=9), 99.0)
        quote = self.dms.issue_quote(request_id, START.replace(hour=9),
                                     99.0, "Stairs at drop-off")
        self.assertEqual(quote.fee, 99.0)

    def test_unanswered_quote_expires_after_24_hours(self) -> None:
        request_id = self.quoted_request()
        self.clock.advance(timedelta(hours=24, minutes=1))
        self.login("chloe")
        self.assertEqual(self.dms.view_delivery(request_id).status,
                         DeliveryStatus.EXPIRED)

    def test_decline_closes_but_keeps_request(self) -> None:
        request_id = self.quoted_request()
        self.login("chloe")
        self.dms.respond_to_quote(request_id, False)
        self.assertEqual(self.dms.view_delivery(request_id).status,
                         DeliveryStatus.DECLINED)

    def test_unquotable_request_declined_with_reason(self) -> None:
        request_id = self.new_request()
        self.login("office")
        self.dms.decline_request(request_id, "Needs a truck")
        self.assertEqual(self.dms.view_delivery(request_id).history[-1].note,
                         "Needs a truck")


class PaymentTests(DmsTestCase):
    """SRS 4.4 (simulated)."""

    def test_payment_amount_equals_quote_and_online_settles(self) -> None:
        request_id = self.accepted_request(PaymentMethod.ONLINE)
        request = self.dms.view_delivery(request_id)
        self.assertEqual(request.payment.amount, request.quote.fee)
        self.dms.pay_online(request_id)
        self.assertEqual(request.payment.status, PaymentStatus.PAID)

    def test_declined_card_can_switch_to_cash(self) -> None:
        request_id = self.accepted_request(PaymentMethod.ONLINE)
        self.gateway.decline_all = True
        with self.assertRaises(PaymentDeclined):
            self.dms.pay_online(request_id)
        self.dms.change_payment_method(request_id,
                                       PaymentMethod.CASH_ON_DELIVERY)
        request = self.dms.view_delivery(request_id)
        self.assertTrue(request.payment.collected_by_driver)
        self.assertEqual(request.payment.status, PaymentStatus.DUE)


class AllocationTests(DmsTestCase):
    """SRS 4.7 and 4.8."""

    def test_only_available_driver_offered(self) -> None:
        request_id = self.accepted_request(pickup_hour=9)
        self.login("office")
        names = [s.name for s in self.dms.available_staff_for(request_id)]
        self.assertEqual(names, ["Dana Driver"])  # Dev works afternoons
        with self.assertRaises(StateError):
            self.dms.allocate(request_id, "U-0004")

    def test_double_booking_refused(self) -> None:
        self.allocated_request("U-0003")
        second = self.accepted_request(pickup_hour=9)
        self.login("office")
        self.assertEqual(self.dms.available_staff_for(second), [])

    def test_removing_availability_releases_allocation(self) -> None:
        request_id = self.allocated_request("U-0003")
        period = self.dms.availability_of("U-0003")[0].availability_id
        self.assertEqual(self.dms.availability_conflicts(period),
                         [request_id])
        # Dana's morning has no other cover, so removal must be confirmed.
        with self.assertRaisesRegex(StateError, "No other delivery staff"):
            self.dms.remove_availability(period)
        self.dms.remove_availability(period, allow_no_cover=True)
        self.assertEqual(self.dms.view_delivery(request_id).status,
                         DeliveryStatus.ACCEPTED)

    def test_overlapping_availability_refused(self) -> None:
        self.login("office")
        with self.assertRaises(ValidationError):
            self.dms.add_availability("U-0003", START, START +
                                      timedelta(hours=1))


class DeliveryTests(DmsTestCase):
    """F-2: custody, cash collection and completion."""

    def test_full_cash_delivery_records_custody(self) -> None:
        request_id = self.allocated_request("U-0003")
        self.login("dana")
        self.dms.record_pickup(request_id)
        request = self.dms.view_delivery(request_id)
        self.assertEqual(request.current_holder("P1"), "Dana Driver")
        with self.assertRaisesRegex(StateError, "Payment"):
            self.dms.record_handover(request_id, "Ravi Recipient")
        with self.assertRaises(StateError):
            self.dms.collect_cash(request_id, 1.00)
        self.dms.collect_cash(request_id, request.quote.fee)
        self.dms.record_handover(request_id, "Ravi Recipient")
        self.assertEqual(request.status, DeliveryStatus.DELIVERED)
        self.assertEqual(request.current_holder("P1"), "Ravi Recipient")

    def test_other_driver_cannot_record_pickup(self) -> None:
        request_id = self.allocated_request("U-0003")
        self.login("dev")
        with self.assertRaises(Exception):
            self.dms.record_pickup(request_id)


class StaffRecordTests(DmsTestCase):
    """F-4: staff join and leave."""

    def test_leaver_holding_parcels_cannot_be_deactivated(self) -> None:
        request_id = self.allocated_request("U-0003")
        self.login("dana")
        self.dms.record_pickup(request_id)
        self.login("owner")
        with self.assertRaisesRegex(StateError, "holds parcels"):
            self.dms.deactivate_staff("U-0003")

    def test_leaver_allocations_return_for_reallocation(self) -> None:
        request_id = self.allocated_request("U-0003")
        self.login("owner")
        with self.assertRaisesRegex(StateError, "No other delivery staff"):
            self.dms.deactivate_staff("U-0003")
        self.assertEqual(self.dms.deactivate_staff("U-0003",
                                                   allow_no_cover=True),
                         [request_id])
        self.assertEqual(self.dms.view_delivery(request_id).status,
                         DeliveryStatus.ACCEPTED)

    def test_office_staff_cannot_manage_staff(self) -> None:
        self.login("office")
        with self.assertRaises(PermissionDenied):
            self.dms.add_staff("Delivery Staff", "X", "x@yowd.com.au",
                               "0400000001", DEMO_PASSWORD)


class StatisticsTests(DmsTestCase):
    """SRS 4.2."""

    def _deliver_one(self) -> None:
        request_id = self.allocated_request("U-0003")
        self.login("dana")
        self.dms.record_pickup(request_id)
        fee = self.dms.view_delivery(request_id).quote.fee
        self.dms.collect_cash(request_id, fee)
        self.dms.record_handover(request_id, "Ravi")

    def test_counts_by_period_and_driver(self) -> None:
        self._deliver_one()
        self.login("owner")
        for period in Period:
            self.assertEqual(
                self.dms.statistics(period, START.date()).delivered, 1)
        self.assertEqual(self.dms.statistics(Period.DAY, START.date(),
                                             "U-0004").delivered, 0)
        self.assertEqual(self.dms.statistics(
            Period.DAY, date(2026, 10, 28)).delivered, 0)

    def test_week_bounds_start_on_monday(self) -> None:
        self.assertEqual(Period.WEEK.bounds(date(2026, 10, 29)),
                         (date(2026, 10, 26), date(2026, 11, 1)))


class PersistenceTests(DmsTestCase):
    """Bootstrap restores state without replaying notifications."""

    def test_restart_restores_requests_and_ids(self) -> None:
        request_id = self.allocated_request("U-0003")
        sent_before = len(self.dms.notifications)
        self.assertGreater(sent_before, 0)
        self.dms = self._start()
        self.assertEqual(self.dms.notifications, [])
        self.login("office")
        restored = self.dms.view_delivery(request_id)
        self.assertEqual(restored.status, DeliveryStatus.ALLOCATED)
        self.assertEqual(restored.assigned_staff_id, "U-0003")
        self.assertNotEqual(self.new_request(), request_id)


class UserMaintenanceTests(DmsTestCase):
    """A2 CRC 4.8 #2, 4.11 #1, 4.13 #4: viewing and updating users."""

    def test_client_updates_own_details_and_they_persist(self) -> None:
        self.login("chloe")
        with self.assertRaises(ValidationError):
            self.dms.update_my_details(phone="12345")
        self.assertEqual(self.dms.current_user.phone, "0412345678")
        self.dms.update_my_details("Chloe Chen", "0499 888 777",
                                   "1 Main St, Kew VIC 3101")
        self.dms = self._start()
        self.login("chloe")
        user = self.dms.current_user
        self.assertEqual((user.name, user.phone, user.default_address),
                         ("Chloe Chen", "0499888777",
                          "1 Main St, Kew VIC 3101"))

    def test_change_password_needs_current_password(self) -> None:
        self.login("dana")
        with self.assertRaises(PermissionDenied):
            self.dms.change_my_password("wrong-pass", "NewPassword2")
        with self.assertRaises(ValidationError):
            self.dms.change_my_password(DEMO_PASSWORD, "short")
        self.dms.change_my_password(DEMO_PASSWORD, "NewPassword2")
        self.dms.logout()
        self.dms.login("dana@yowd.com.au", "NewPassword2")

    def test_office_updates_clients_but_only_owner_updates_staff(self
                                                                 ) -> None:
        self.login("office")
        self.dms.update_user_details("U-0005", phone="0400 111 222")
        self.assertEqual(self.dms.find_user("U-0005").phone, "0400111222")
        with self.assertRaises(PermissionDenied):
            self.dms.update_user_details("U-0003", name="Dana D")
        self.login("owner")
        self.dms.update_user_details("U-0003", name="Dana D")
        self.assertEqual(self.dms.find_user("U-0003").name, "Dana D")
        with self.assertRaises(PermissionDenied):
            self.login("chloe")
            self.dms.update_user_details("U-0005", name="X")

    def test_client_with_open_delivery_cannot_be_deactivated(self) -> None:
        request_id = self.new_request()
        self.login("office")
        with self.assertRaisesRegex(StateError, "open deliveries"):
            self.dms.deactivate_client("U-0005")
        self.dms.cancel_request(request_id, "Client closed the account")
        self.dms.deactivate_client("U-0005")
        self.assertEqual(self.dms.view_delivery(request_id).status,
                         DeliveryStatus.CANCELLED)
        self.dms.logout()
        with self.assertRaises(PermissionDenied):
            self.dms.login("chloe@example.com", DEMO_PASSWORD)


class PaymentRecordTests(DmsTestCase):
    """A2 CRC 4.13 #6, 4.14 #2, 4.15 #2, 4.16 #1 and scenario 7.2."""

    def test_card_type_and_invoice_recorded_and_restored(self) -> None:
        request_id = self.accepted_request(PaymentMethod.ONLINE)
        self.dms.pay_online(request_id)
        invoice = self.dms.invoice(request_id)
        self.assertEqual(invoice.invoice_number, f"INV-{request_id}")
        self.assertEqual(invoice.status, "Paid")
        self.assertIn(invoice.card_type, SIMULATED_CARD_TYPES)
        self.assertAlmostEqual(sum(a for _, a in invoice.lines),
                               invoice.total)
        self.dms = self._start()
        self.login("chloe")
        restored = self.dms.view_delivery(request_id).payment
        self.assertEqual(restored.card_type, invoice.card_type)

    def test_invoice_shows_fee_adjustment(self) -> None:
        request_id = self.new_request()
        self.login("office")
        self.dms.issue_quote(request_id, START.replace(hour=9), 30.00,
                             "Regular client rate")
        self.login("chloe")
        with self.assertRaisesRegex(StateError, "no payment"):
            self.dms.invoice(request_id)
        self.dms.respond_to_quote(request_id, True,
                                  PaymentMethod.CASH_ON_DELIVERY)
        invoice = self.dms.invoice(request_id)
        self.assertEqual(len(invoice.lines), 2)
        self.assertIn("Regular client rate", invoice.lines[1][0])
        self.assertAlmostEqual(sum(a for _, a in invoice.lines), 30.00)

    def test_office_records_payment_settled_outside_dms(self) -> None:
        request_id = self.accepted_request(PaymentMethod.ONLINE)
        with self.assertRaises(PermissionDenied):
            self.dms.record_payment_manually(request_id, "paid")
        self.login("office")
        self.assertEqual(
            [r.request_id for r in
             self.dms.payment_records(PaymentStatus.DUE)], [request_id])
        with self.assertRaises(ValidationError):
            self.dms.record_payment_manually(request_id, " ")
        self.dms.record_payment_manually(request_id, "Paid at the office")
        payment = self.dms.view_delivery(request_id).payment
        self.assertEqual(payment.status, PaymentStatus.PAID)
        self.assertEqual(payment.reference, "MANUAL-U-0002")
        self.assertEqual(self.dms.payment_records(PaymentStatus.DUE), [])
        with self.assertRaises(StateError):
            self.dms.record_payment_manually(request_id, "again")

    def test_simulated_decline_affects_next_card_only(self) -> None:
        request_id = self.accepted_request(PaymentMethod.ONLINE)
        self.dms.simulate_card_decline()
        with self.assertRaises(PaymentDeclined):
            self.dms.pay_online(request_id)
        self.assertEqual(self.dms.view_delivery(request_id).payment.status,
                         PaymentStatus.DUE)
        self.dms.pay_online(request_id)
        self.assertEqual(self.dms.view_delivery(request_id).payment.status,
                         PaymentStatus.PAID)


class StaffCoverTests(DmsTestCase):
    """A2 CRC 4.7 #3: at least one delivery staff member stays available."""

    def test_cover_gaps_found_only_where_no_one_else_works(self) -> None:
        self.login("office")
        dana = self.dms.availability_of("U-0003")[0]
        dev = self.dms.availability_of("U-0004")[0]
        # Dana covers Dev's afternoon, but nobody covers Dana's morning.
        self.assertEqual(self.dms.availability_cover_gaps(
            dev.availability_id), [])
        self.assertEqual(self.dms.availability_cover_gaps(
            dana.availability_id), [(START, START.replace(hour=13))])
        self.dms.remove_availability(dev.availability_id)

    def test_past_time_is_not_a_gap(self) -> None:
        self.clock.advance(timedelta(hours=3))
        self.login("office")
        dana = self.dms.availability_of("U-0003")[0]
        self.assertEqual(self.dms.availability_cover_gaps(
            dana.availability_id), [(START.replace(hour=11),
                                     START.replace(hour=13))])


class IntegrityTests(DmsTestCase):
    """A2 section 6.3: inconsistent saved records stop startup."""

    def _tamper(self, change) -> None:
        path = Path(self.folder.name) / "requests.json"
        records = json.loads(path.read_text(encoding="utf-8"))
        change(records)
        path.write_text(json.dumps(records), encoding="utf-8")

    def test_double_booked_driver_stops_startup(self) -> None:
        self.allocated_request("U-0003")
        later = self.accepted_request(pickup_hour=14)
        self.login("office")
        self.dms.allocate(later, "U-0003")

        def overlap(records: list) -> None:
            records[1]["quote"]["estimated_pickup"] = \
                records[0]["quote"]["estimated_pickup"]
        self._tamper(overlap)
        with self.assertRaisesRegex(IntegrityError, "double-booked"):
            self._start()

    def test_payment_not_matching_quote_stops_startup(self) -> None:
        self.accepted_request()

        def change_amount(records: list) -> None:
            records[0]["payment"]["amount"] = 1.0
        self._tamper(change_amount)
        with self.assertRaisesRegex(IntegrityError, "does not match"):
            self._start()


if __name__ == "__main__":
    unittest.main()
