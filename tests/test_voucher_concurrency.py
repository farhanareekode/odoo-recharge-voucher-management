# -*- coding: utf-8 -*-
import concurrent.futures
import logging
import time
import psycopg2
from odoo.tests.common import TransactionCase, tagged
import odoo
from odoo.addons.recharge_voucher_management.models.voucher_crypto import VoucherCryptoService

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestVoucherConcurrency(TransactionCase):
    """
    Automated Concurrency Testing using True Independent Database Connections:
    - T04: Two simultaneous sales of the last voucher -> Only one succeeds.
    - 100 concurrent cashiers competing for the final 20 vouchers.
    """

    def setUp(self):
        super().setUp()
        self.reg = self.env.registry
        self.db = self.reg._db
        with self.db.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            sup = env["recharge.voucher.supplier"].search([("code", "=", "NTDD-IND")], limit=1)
            if not sup:
                sup = env["recharge.voucher.supplier"].create({
                    "name": "Test Telecom Distributors",
                    "code": "NTDD-IND",
                })
            batch = env["recharge.voucher.batch"].search([("name", "=", "BATCH-CONCUR-AUTO")], limit=1)
            if not batch:
                batch = env["recharge.voucher.batch"].create({
                    "name": "BATCH-CONCUR-AUTO",
                    "supplier_id": sup.id,
                    "date_received": "2026-11-01",
                })
            self.batch_id = batch.id
            cr.execute("DELETE FROM recharge_voucher WHERE serial_number LIKE 'CONCUR-%'")
            cr.commit()

    def tearDown(self):
        with self.db.cursor() as cr:
            cr.execute("DELETE FROM recharge_voucher WHERE serial_number LIKE 'CONCUR-%'")
            cr.commit()
        super().tearDown()

    def test_t04_two_simultaneous_sales_of_last_voucher(self):
        """T04: Two simultaneous sales of the last voucher -> Exactly one succeeds, one fails."""
        service_code = f"CONCUR-T04-{int(time.time()*1000)%100000}"
        pin = f"999{int(time.time() * 1000) % 1000000000:09d}"
        with self.db.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            env["recharge.voucher"].create({
                "service_code": service_code,
                "face_value": 199.0,
                "purchase_cost": 190.0,
                "serial_number": "CONCUR-T04-LAST",
                "encrypted_pin": VoucherCryptoService.encrypt_pin(pin),
                "pin_fingerprint": VoucherCryptoService.compute_pin_fingerprint(pin),
                "masked_pin": VoucherCryptoService.mask_pin(pin),
                "batch_id": self.batch_id,
                "expiry_date": "2027-12-31",
                "state": "available",
            })
            cr.commit()

        orig_test_cr = self.reg.test_cr
        self.reg.test_cr = None
        try:
            def worker_purchase(token):
                with self.db.cursor() as cr:
                    cr.connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)
                    env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                    v = env["recharge.voucher"].allocate_and_reserve(
                        env, service_code, token, store_id=False
                    )
                    if v:
                        cr.commit()
                        return {"success": True, "token": token, "voucher_id": v.id}
                    else:
                        cr.rollback()
                        return {"success": False, "token": token, "voucher_id": None}

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(worker_purchase, "CONCUR_T04_ORDER_A")
                f2 = executor.submit(worker_purchase, "CONCUR_T04_ORDER_B")
                results = [f1.result(), f2.result()]

            success_count = sum(1 for r in results if r["success"])
            fail_count = sum(1 for r in results if not r["success"])

            self.assertEqual(success_count, 1, "Exactly one of two simultaneous sales must succeed")
            self.assertEqual(fail_count, 1, "The second concurrent purchase must fail gracefully")
        finally:
            self.reg.test_cr = orig_test_cr

    def test_100_concurrent_cashiers_purchasing_final_20_vouchers(self):
        """
        Stress test: 100 concurrent cashier requests competing for the final 20 vouchers.
        Verifies:
        - Exactly 20 successful allocations.
        - Exactly 80 rejections.
        - Zero duplicate allocations.
        - Real PostgreSQL FOR UPDATE SKIP LOCKED row concurrency.
        """
        timestamp_prefix = int(time.time() * 100) % 100000
        service_code = f"CONCUR-STRESS-{timestamp_prefix}"
        total_vouchers = 20
        total_requests = 100

        with self.db.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            vals = []
            for i in range(1, total_vouchers + 1):
                pin = f"5{timestamp_prefix:05d}{i:04d}"
                vals.append({
                    "service_code": service_code,
                    "face_value": 500.0,
                    "purchase_cost": 480.0,
                    "serial_number": f"CONCUR-STRESS-{timestamp_prefix}-{i:03d}",
                    "encrypted_pin": VoucherCryptoService.encrypt_pin(pin),
                    "pin_fingerprint": VoucherCryptoService.compute_pin_fingerprint(pin),
                    "masked_pin": VoucherCryptoService.mask_pin(pin),
                    "batch_id": self.batch_id,
                    "expiry_date": "2027-12-31",
                    "state": "available",
                })
            env["recharge.voucher"].create(vals)
            cr.commit()

        orig_test_cr = self.reg.test_cr
        self.reg.test_cr = None
        try:
            def cashier_attempt(worker_id):
                token = f"CONCUR_CASHIER_TOKEN_{worker_id}"
                with self.db.cursor() as cr:
                    cr.connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)
                    env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                    v = env["recharge.voucher"].allocate_and_reserve(
                        env, service_code, token, store_id=False
                    )
                    if v:
                        v_id = v.id
                        cr.commit()
                        return {"worker_id": worker_id, "success": True, "voucher_id": v_id}
                    else:
                        cr.rollback()
                        return {"worker_id": worker_id, "success": False, "voucher_id": None}

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(cashier_attempt, i) for i in range(1, total_requests + 1)]
                results = [f.result() for f in futures]

            successful_workers = [r for r in results if r["success"]]
            failed_workers = [r for r in results if not r["success"]]

            _logger.info(
                "Concurrency Stress Test Finished: %d Successes, %d Failures",
                len(successful_workers),
                len(failed_workers),
            )

            # Assertions
            self.assertEqual(
                len(successful_workers),
                total_vouchers,
                f"Exactly {total_vouchers} allocations must succeed!",
            )
            self.assertEqual(
                len(failed_workers),
                total_requests - total_vouchers,
                f"Exactly {total_requests - total_vouchers} requests must fail gracefully!",
            )

            # Check for uniqueness: Ensure no voucher was allocated twice!
            allocated_voucher_ids = [r["voucher_id"] for r in successful_workers]
            unique_allocated_ids = set(allocated_voucher_ids)
            self.assertEqual(
                len(unique_allocated_ids),
                total_vouchers,
                "CRITICAL: Zero duplicate voucher allocations allowed! Every allocated voucher must be unique.",
            )
        finally:
            self.reg.test_cr = orig_test_cr
