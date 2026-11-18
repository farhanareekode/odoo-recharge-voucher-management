# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import AccessError, UserError
from ..models.voucher_crypto import VoucherCryptoService


class TestVoucherSecurity(TransactionCase):
    """
    Test Cases for Voucher Security and Permissions:
    - T09: Cashier attempts unsold PIN export / browse -> Access Denied.
    - PIN Encryption & Integrity: AES-256-GCM authentication tag catches tampering.
    """

    def setUp(self):
        super().setUp()
        self.supplier = self.env["recharge.voucher.supplier"].create({
            "name": "Security Test Supplier",
            "code": "SEC-SUP-01",
        })
        self.batch = self.env["recharge.voucher.batch"].create({
            "name": "BATCH-SEC-01",
            "supplier_id": self.supplier.id,
            "date_received": "2026-11-01",
        })
        # Create an available voucher
        pin = "998877665544"
        self.voucher = self.env["recharge.voucher"].create({
            "service_code": "AIR-100",
            "face_value": 100.0,
            "purchase_cost": 95.0,
            "serial_number": "SEC-SN-001",
            "encrypted_pin": VoucherCryptoService.encrypt_pin(pin),
            "pin_fingerprint": VoucherCryptoService.compute_pin_fingerprint(pin),
            "masked_pin": VoucherCryptoService.mask_pin(pin),
            "batch_id": self.batch.id,
            "expiry_date": "2027-12-31",
            "state": "available",
        })

        # Create Cashier user
        cashier_group = self.env.ref("recharge_voucher_management.group_voucher_cashier")
        self.cashier_user = self.env["res.users"].create({
            "name": "POS Cashier Test",
            "login": "cashier_test_user",
            "groups_id": [(6, 0, [cashier_group.id, self.env.ref("base.group_user").id])],
        })

    def test_t09_cashier_attempts_unsold_pin_access(self):
        """T09: Cashier attempts to browse, export, or decrypt unsold vouchers -> Access Denied."""
        # 1. Cashier searching vouchers table: Record rule restricts to state='sold' linked to cashier's orders
        cashier_vouchers = self.env["recharge.voucher"].with_user(self.cashier_user).search([
            ("id", "=", self.voucher.id)
        ])
        self.assertFalse(
            cashier_vouchers,
            "Cashier must NOT be able to view or browse unsold available vouchers!",
        )

        # 2. Cashier attempting data export on unsold vouchers -> Denied with UserError
        with self.assertRaises(UserError):
            self.env["recharge.voucher"].with_user(self.cashier_user).export_data(
                ["serial_number", "masked_pin", "state"]
            )

        # 3. Cashier calling backend decrypt_pin directly without POS payment verification context
        with self.assertRaises(AccessError):
            self.voucher.with_user(self.cashier_user).decrypt_pin(context_reason="Direct Backend Call")

    def test_crypto_tampering_detection(self):
        """Test AES-256-GCM detects ciphertext tampering and raises error."""
        pin = "123456789012"
        enc = VoucherCryptoService.encrypt_pin(pin)

        # Tamper with encrypted string
        raw_b64 = list(enc)
        raw_b64[15] = "A" if raw_b64[15] != "A" else "B"
        tampered_enc = "".join(raw_b64)

        with self.assertRaises(ValueError):
            VoucherCryptoService.decrypt_pin(tampered_enc)

