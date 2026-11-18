# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
from ..models.voucher_crypto import VoucherCryptoService


class TestVoucherAllocation(TransactionCase):
    """
    Test Cases for POS Voucher Issuance, Idempotency, and Exception Scenarios:
    - T05: Payment failure -> No PIN revealed.
    - T06: Repeat same POS request -> No second voucher issued (Idempotency).
    - T07: Network outage / offline validation.
    - T08: Printer failure after payment -> Controlled reprint logs audit and returns same voucher.
    - T10: Refund of revealed PIN -> Voucher marked BLOCKED, never returns to available stock.
    """

    def setUp(self):
        super().setUp()
        self.supplier = self.env["recharge.voucher.supplier"].create({
            "name": "Allocation Supplier",
            "code": "ALLOC-SUP-01",
        })
        self.batch = self.env["recharge.voucher.batch"].create({
            "name": "BATCH-ALLOC-01",
            "supplier_id": self.supplier.id,
            "date_received": "2026-11-01",
        })
        self.v_prod_air = self.env["recharge.voucher.product"].search(
            [("service_code", "=", "AIR-100")], limit=1
        )

        # Create 2 available vouchers
        self.pin1 = "111122223333"
        self.voucher1 = self.env["recharge.voucher"].create({
            "service_code": "AIR-100",
            "face_value": 100.0,
            "purchase_cost": 95.0,
            "serial_number": "ALLOC-SN-001",
            "encrypted_pin": VoucherCryptoService.encrypt_pin(self.pin1),
            "pin_fingerprint": VoucherCryptoService.compute_pin_fingerprint(self.pin1),
            "masked_pin": VoucherCryptoService.mask_pin(self.pin1),
            "batch_id": self.batch.id,
            "expiry_date": "2027-12-31",
            "state": "available",
        })

        self.pin2 = "444455556666"
        self.voucher2 = self.env["recharge.voucher"].create({
            "service_code": "AIR-100",
            "face_value": 100.0,
            "purchase_cost": 95.0,
            "serial_number": "ALLOC-SN-002",
            "encrypted_pin": VoucherCryptoService.encrypt_pin(self.pin2),
            "pin_fingerprint": VoucherCryptoService.compute_pin_fingerprint(self.pin2),
            "masked_pin": VoucherCryptoService.mask_pin(self.pin2),
            "batch_id": self.batch.id,
            "expiry_date": "2027-12-31",
            "state": "available",
        })

        # Set up POS Config & Session
        self.pos_config = self.env["pos.config"].create({
            "name": "Bengaluru Store #1",
        })
        self.pos_session = self.env["pos.session"].create({
            "config_id": self.pos_config.id,
            "user_id": self.env.user.id,
        })

    def _create_test_pos_order(self, state="paid", pos_reference="POS/TEST/001"):
        """Helper to create a POS order with 1 voucher product line."""
        order = self.env["pos.order"].create({
            "name": pos_reference,
            "pos_reference": pos_reference,
            "session_id": self.pos_session.id,
            "config_id": self.pos_config.id,
            "user_id": self.env.user.id,
            "amount_tax": 0.0,
            "amount_total": 100.0,
            "amount_paid": 100.0 if state == "paid" else 0.0,
            "amount_return": 0.0,
            "state": state,
            "lines": [
                (
                    0,
                    0,
                    {
                        "product_id": self.v_prod_air.product_id.id,
                        "qty": 1,
                        "price_unit": 100.0,
                        "price_subtotal": 100.0,
                        "price_subtotal_incl": 100.0,
                    },
                )
            ],
        })
        return order

    def test_t05_payment_failure_no_pin_revealed(self):
        """T05: Payment failure / order in draft -> Voucher is not sold, PIN is never revealed."""
        order = self._create_test_pos_order(state="draft", pos_reference="POS/FAIL/001")

        # Attempting voucher issuance on unpaid order raises UserError
        with self.assertRaises(UserError) as cm:
            self.env["pos.order"].process_pos_voucher_issuance(order.id)
        self.assertIn("Payment verification failed", str(cm.exception))

        # Check voucher state remains available
        self.env.invalidate_all()
        self.assertEqual(self.voucher1.state, "available", "Voucher must remain available if payment fails")
        self.assertFalse(self.voucher1.pos_order_id)

    def test_t06_idempotent_voucher_issuance(self):
        """T06: Repeat same POS request -> Exactly one voucher issued, no second voucher consumed."""
        order = self._create_test_pos_order(state="paid", pos_reference="POS/IDEMP/001")

        # First issuance call
        res1 = self.env["pos.order"].process_pos_voucher_issuance(order.id)
        self.assertEqual(len(res1), 1)
        issued_sn_1 = res1[0]["serial_number"]
        issued_pin_1 = res1[0]["pin"]

        # Second issuance call (simulating network retry or repeated client sync)
        res2 = self.env["pos.order"].process_pos_voucher_issuance(order.id)
        self.assertEqual(len(res2), 1)
        issued_sn_2 = res2[0]["serial_number"]
        issued_pin_2 = res2[0]["pin"]

        # Must return the SAME serial and PIN
        self.assertEqual(issued_sn_1, issued_sn_2, "Idempotent call must return the same voucher serial")
        self.assertEqual(issued_pin_1, issued_pin_2, "Idempotent call must return the same PIN")

        # Verify exactly one issue record exists
        issues = self.env["recharge.voucher.issue"].search([("pos_order_id", "=", order.id)])
        self.assertEqual(len(issues), 1, "Only one voucher issue record must be created")

        # Verify second voucher is untouched and still available
        self.env.invalidate_all()
        self.assertEqual(self.voucher2.state, "available", "Second voucher must NOT be consumed")

    def test_t07_offline_voucher_issuance_blocked_and_reservation_lifecycle(self):
        """T07: Offline/unverified voucher sale is blocked, reservations time out safely."""
        # 1. Ensure availability check reports real count
        avail = self.env["pos.order"].check_voucher_availability("AIR-100")
        self.assertGreaterEqual(avail["available_count"], 1)

        # 2. Reserve a voucher with token
        lines_data = [{"service_code": "AIR-100", "line_cid": "c1", "qty": 1}]
        pos_reference = "OFFLINE_TEST_ORDER_001"
        res = self.env["pos.order"].reserve_vouchers_for_pos(pos_reference, lines_data)
        self.assertTrue(res["success"])
        reserved_list = res["reserved"]
        self.assertEqual(len(reserved_list), 1)

        voucher_id = reserved_list[0]["voucher_id"]
        self.env.invalidate_all()
        voucher = self.env["recharge.voucher"].browse(voucher_id)
        self.assertEqual(voucher.state, "reserved")

        # 3. Simulate client network disconnection / cancelled cart -> Release reservations
        self.env["pos.order"].release_order_reservations(pos_reference)
        self.env.invalidate_all()
        self.assertEqual(voucher.state, "available", "Cancelled reservation must be safely restored to available stock")

    def test_t08_printer_failure_controlled_reprint(self):
        """T08: Printer failure after payment -> Controlled reprint returns same voucher and logs reason."""
        order = self._create_test_pos_order(state="paid", pos_reference="POS/PRINT/001")
        res = self.env["pos.order"].process_pos_voucher_issuance(order.id)
        original_sn = res[0]["serial_number"]
        original_pin = res[0]["pin"]

        issue = self.env["recharge.voucher.issue"].search([("pos_order_id", "=", order.id)], limit=1)
        self.assertTrue(issue)
        self.assertEqual(issue.reprint_count, 0)

        # Execute controlled reprint with mandatory justification reason
        reprint_res = issue.action_controlled_reprint(
            reason="Thermal paper jam during receipt output", terminal_name="POS Terminal 01"
        )
        self.assertEqual(reprint_res["serial_number"], original_sn, "Reprint must yield the exact same serial number")
        self.assertEqual(reprint_res["pin"], original_pin, "Reprint must yield the exact same PIN")
        self.assertEqual(reprint_res["reprint_count"], 1)

        # Verify audit log record
        self.assertEqual(len(issue.reprint_log_ids), 1)
        log = issue.reprint_log_ids[0]
        self.assertEqual(log.user_id, self.env.user)
        self.assertIn("Thermal paper jam", log.reason)
        self.assertEqual(issue.state, "reprinted")

    def test_t10_refund_of_revealed_pin(self):
        """T10: Refund of revealed PIN -> Voucher marked BLOCKED, never returns to available stock."""
        order = self._create_test_pos_order(state="paid", pos_reference="POS/REFUND/001")
        self.env["pos.order"].process_pos_voucher_issuance(order.id)

        issue = self.env["recharge.voucher.issue"].search([("pos_order_id", "=", order.id)], limit=1)
        issued_voucher = issue.voucher_id
        self.assertEqual(issued_voucher.state, "sold")

        # Process refund
        refund_reason = "Customer disputed charge and cancelled transaction"
        issue.action_process_refund(reason=refund_reason)

        # Verify issue status
        self.assertEqual(issue.state, "refunded")
        self.assertEqual(issue.refund_reason, refund_reason)

        # Critical security check: Voucher MUST be blocked, NEVER available
        self.env.invalidate_all()
        self.assertEqual(issued_voucher.state, "blocked", "Refunded voucher must be BLOCKED")
        self.assertIn("Refunded for POS Order", issued_voucher.block_reason)

        # Verify availability count
        avail_count = self.env["recharge.voucher"].search_count([
            ("id", "=", issued_voucher.id),
            ("state", "=", "available"),
        ])
        self.assertEqual(avail_count, 0, "Blocked voucher must NEVER appear in available stock query")
