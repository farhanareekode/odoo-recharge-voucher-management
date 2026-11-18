# -*- coding: utf-8 -*-
import base64
from datetime import date, timedelta
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, ValidationError
from ..models.voucher_crypto import VoucherCryptoService


class TestVoucherImport(TransactionCase):
    """
    Test Cases for Voucher CSV Import and Validation:
    - T01: Import 10 valid rows -> 10 available vouchers with AES-256-GCM encrypted PINs.
    - T02: Duplicate serial or PIN in upload / DB -> Rejection with row details.
    - T03: Unknown service code -> Rejection without partial corruption.
    """

    def setUp(self):
        super().setUp()
        self.supplier = self.env["recharge.voucher.supplier"].create({
            "name": "Test Supplier Ltd",
            "code": "TEST-SUP-01",
        })
        self.product_airtel = self.env["recharge.voucher.product"].search(
            [("service_code", "=", "AIR-100")], limit=1
        )
        self.product_jio = self.env["recharge.voucher.product"].search(
            [("service_code", "=", "JIO-239")], limit=1
        )

    def test_t01_import_10_valid_rows(self):
        """T01: Import 10 valid rows -> 10 available vouchers created with encrypted PINs."""
        csv_content = """service_code,serial_number,pin_code,batch_ref,expiry_date,purchase_cost
AIR-100,T01-SN-001,111122223333,BATCH-T01,2027-12-31,95.00
AIR-100,T01-SN-002,111122223334,BATCH-T01,2027-12-31,95.00
AIR-100,T01-SN-003,111122223335,BATCH-T01,2027-12-31,95.00
AIR-100,T01-SN-004,111122223336,BATCH-T01,2027-12-31,95.00
AIR-100,T01-SN-005,111122223337,BATCH-T01,2027-12-31,95.00
JIO-239,T01-SN-006,222233334441,BATCH-T01,2027-12-31,230.00
JIO-239,T01-SN-007,222233334442,BATCH-T01,2027-12-31,230.00
JIO-239,T01-SN-008,222233334443,BATCH-T01,2027-12-31,230.00
JIO-239,T01-SN-009,222233334444,BATCH-T01,2027-12-31,230.00
JIO-239,T01-SN-010,222233334445,BATCH-T01,2027-12-31,230.00"""

        wizard = self.env["recharge.voucher.import.wizard"].create({
            "data_file": base64.b64encode(csv_content.encode("utf-8")),
            "filename": "test_10.csv",
            "supplier_id": self.supplier.id,
            "import_mode": "strict",
        })

        # 1. Preview & Validate
        wizard.action_preview_and_validate()
        self.assertEqual(wizard.total_rows, 10)
        self.assertEqual(wizard.valid_rows, 10)
        self.assertEqual(wizard.error_rows, 0, "No errors expected for valid 10 rows")

        # 2. Confirm Import
        wizard.action_confirm_import()
        self.assertEqual(wizard.imported_count, 10)

        # 3. Verify in database
        vouchers = self.env["recharge.voucher"].search([("serial_number", "=like", "T01-SN-%")])
        self.assertEqual(len(vouchers), 10)
        for v in vouchers:
            self.assertEqual(v.state, "available")
            # Plaintext PIN must NOT be stored
            self.assertNotIn("111122223333", v.encrypted_pin)
            self.assertTrue(v.pin_fingerprint)
            self.assertEqual(len(v.pin_fingerprint), 64)
            # Masked PIN format check
            self.assertTrue(v.masked_pin.startswith("********"))
            # Decryption test with authorized context
            decrypted = v.with_context(pos_issuance_verified=True).decrypt_pin()
            self.assertTrue(decrypted.isdigit())

    def test_t02_duplicate_serial_or_pin(self):
        """T02: Duplicate serial or PIN -> Rejected with row details; Re-importing same file creates 0 duplicates."""
        csv_initial = """service_code,serial_number,pin_code,batch_ref,expiry_date,purchase_cost
AIR-100,T02-SN-001,999900001111,BATCH-T02,2027-12-31,95.00"""
        wiz1 = self.env["recharge.voucher.import.wizard"].create({
            "data_file": base64.b64encode(csv_initial.encode("utf-8")),
            "filename": "init.csv",
            "supplier_id": self.supplier.id,
            "import_mode": "strict",
        })
        wiz1.action_preview_and_validate()
        wiz1.action_confirm_import()
        self.assertEqual(wiz1.imported_count, 1)

        # Attempt to import identical file again
        wiz2 = self.env["recharge.voucher.import.wizard"].create({
            "data_file": base64.b64encode(csv_initial.encode("utf-8")),
            "filename": "init_dup.csv",
            "supplier_id": self.supplier.id,
            "import_mode": "strict",
        })
        wiz2.action_preview_and_validate()
        self.assertEqual(wiz2.error_rows, 1, "Duplicate row must be detected")
        self.assertEqual(len(wiz2.error_line_ids), 1)
        err = wiz2.error_line_ids[0]
        self.assertIn("already exists", err.error_message)

        # In strict mode, confirming import raises UserError
        with self.assertRaises(UserError):
            wiz2.action_confirm_import()

        # In-file duplicate test
        csv_infile_dup = """service_code,serial_number,pin_code,batch_ref,expiry_date,purchase_cost
AIR-100,T02-SN-NEW1,888800001111,BATCH-T02,2027-12-31,95.00
AIR-100,T02-SN-NEW1,777700001111,BATCH-T02,2027-12-31,95.00"""
        wiz3 = self.env["recharge.voucher.import.wizard"].create({
            "data_file": base64.b64encode(csv_infile_dup.encode("utf-8")),
            "filename": "infile_dup.csv",
            "supplier_id": self.supplier.id,
            "import_mode": "strict",
        })
        wiz3.action_preview_and_validate()
        self.assertTrue(wiz3.error_rows >= 1, "In-file duplicate serial number must be rejected")

    def test_t03_unknown_service_code(self):
        """T03: Unknown service code -> Rejected without partial corruption."""
        csv_unknown = """service_code,serial_number,pin_code,batch_ref,expiry_date,purchase_cost
INVALID-999,T03-SN-001,555544443333,BATCH-T03,2027-12-31,95.00"""
        wiz = self.env["recharge.voucher.import.wizard"].create({
            "data_file": base64.b64encode(csv_unknown.encode("utf-8")),
            "filename": "unknown.csv",
            "supplier_id": self.supplier.id,
            "import_mode": "strict",
        })
        wiz.action_preview_and_validate()
        self.assertEqual(wiz.error_rows, 1)
        self.assertIn("Unknown service code", wiz.error_line_ids[0].error_message)

        with self.assertRaises(UserError):
            wiz.action_confirm_import()

        # Verify nothing was created in database
        v = self.env["recharge.voucher"].search([("serial_number", "=", "T03-SN-001")])
        self.assertFalse(v, "No corrupted voucher record should be persisted")

