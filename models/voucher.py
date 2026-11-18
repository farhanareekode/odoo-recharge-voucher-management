# -*- coding: utf-8 -*-
from datetime import timedelta
import logging
import psycopg2
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError, AccessError
from .voucher_crypto import VoucherCryptoService

_logger = logging.getLogger(__name__)

# Reservation timeout in minutes
RESERVATION_TIMEOUT_MINUTES = 10


class RechargeVoucher(models.Model):
    _name = "recharge.voucher"
    _description = "Prepaid Recharge Voucher"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "expiry_date asc, id asc"

    name = fields.Char(
        string="Voucher Reference", compute="_compute_name", store=True
    )
    service_code = fields.Char(
        string="Service Code", required=True, index=True, tracking=True
    )
    product_tmpl_name = fields.Char(
        string="Product Name", compute="_compute_product_info", store=True
    )
    face_value = fields.Monetary(
        string="Denomination / Face Value",
        required=True,
        currency_field="currency_id",
        tracking=True,
    )
    purchase_cost = fields.Monetary(
        string="Purchase Cost",
        required=True,
        currency_field="currency_id",
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        required=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", string="Currency", store=True
    )

    serial_number = fields.Char(
        string="Serial Number", required=True, copy=False, index=True
    )
    encrypted_pin = fields.Text(
        string="Encrypted PIN",
        required=True,
        copy=False,
        help="AES-256-GCM encrypted PIN. Decryption restricted to verified issuance.",
    )
    pin_fingerprint = fields.Char(
        string="PIN Fingerprint",
        required=True,
        copy=False,
        index=True,
        size=64,
        help="HMAC-SHA256 hash for duplicate PIN detection without plaintext exposure.",
    )
    masked_pin = fields.Char(
        string="PIN (Masked)",
        required=True,
        copy=False,
        help="Safe masked representation (e.g. ******1234) for UI display and receipts.",
    )

    batch_id = fields.Many2one(
        "recharge.voucher.batch",
        string="Batch Reference",
        required=True,
        ondelete="restrict",
        index=True,
    )
    supplier_id = fields.Many2one(
        "recharge.voucher.supplier",
        related="batch_id.supplier_id",
        string="Supplier",
        store=True,
        index=True,
    )
    expiry_date = fields.Date(
        string="Expiry Date", required=True, index=True, tracking=True
    )

    state = fields.Selection(
        [
            ("available", "Available"),
            ("reserved", "Reserved"),
            ("sold", "Sold"),
            ("blocked", "Blocked"),
            ("expired", "Expired"),
        ],
        string="Status",
        default="available",
        required=True,
        index=True,
        tracking=True,
    )

    # Store / POS Allocation
    store_id = fields.Many2one(
        "pos.config",
        string="Store / Terminal",
        index=True,
        help="POS store where the voucher was reserved or issued.",
    )
    reservation_token = fields.Char(
        string="Reservation Token",
        copy=False,
        index=True,
        help="Unique identifier for POS cart reservation (e.g. POS Order line UID).",
    )
    reservation_time = fields.Datetime(
        string="Reservation Time", copy=False
    )

    # Issuance References
    issued_date = fields.Datetime(
        string="Issued Date", copy=False, tracking=True
    )
    pos_order_id = fields.Many2one(
        "pos.order",
        string="POS Order",
        copy=False,
        index=True,
        tracking=True,
    )
    issue_id = fields.Many2one(
        "recharge.voucher.issue",
        string="Issue Record",
        copy=False,
    )
    block_reason = fields.Text(string="Reason for Block / Refund")

    _sql_constraints = [
        (
            "serial_number_uniq",
            "unique(serial_number)",
            "Voucher Serial Number must be unique across the entire database!",
        ),
        (
            "pin_fingerprint_uniq",
            "unique(pin_fingerprint)",
            "Voucher PIN fingerprint already exists! The same PIN cannot be imported or registered twice.",
        ),
        (
            "purchase_cost_non_negative",
            "CHECK(purchase_cost >= 0)",
            "Purchase cost cannot be negative!",
        ),
        (
            "face_value_positive",
            "CHECK(face_value > 0)",
            "Denomination / Face value must be strictly positive!",
        ),
    ]

    @api.depends("serial_number", "service_code")
    def _compute_name(self):
        for voucher in self:
            voucher.name = f"{voucher.service_code or ''}-{voucher.serial_number or ''}"

    @api.depends("service_code")
    def _compute_product_info(self):
        for voucher in self:
            v_prod = self.env["recharge.voucher.product"].search(
                [("service_code", "=", voucher.service_code)], limit=1
            )
            voucher.product_tmpl_name = v_prod.name if v_prod else voucher.service_code

    @api.constrains("expiry_date")
    def _check_expiry_date(self):
        for voucher in self:
            if voucher.state == "available" and voucher.expiry_date < fields.Date.context_today(voucher):
                raise ValidationError(
                    _("Cannot set expiry date in the past for an available voucher (%s)!", voucher.serial_number)
                )

    def decrypt_pin(self, context_reason=""):
        """
        Secure decryption method.
        Plaintext PIN is decrypted in memory only.
        Enforces server-side security checks and logs audit events.
        """
        self.ensure_one()
        # Security check:
        # Cashiers must NOT access backend decryption directly (only via verified POS payment issuance)
        user = self.env.user
        is_pos_internal = self.env.context.get("pos_issuance_verified", False)
        is_operations = user.has_group("recharge_voucher_management.group_voucher_operations")
        is_system = user.has_group("base.group_system")

        if not (is_pos_internal or is_operations or is_system):
            raise AccessError(
                _("Access Denied: You do not have permission to decrypt or view voucher PINs.")
            )

        _logger.info(
            "PIN Decryption requested for Voucher ID %s (Serial %s) by User %s (Reason: %s)",
            self.id,
            self.serial_number,
            user.login,
            context_reason or "POS Issuance / Audit",
        )

        return VoucherCryptoService.decrypt_pin(self.encrypted_pin)

    @classmethod
    def allocate_and_reserve(cls, env, service_code, reservation_token, store_id=False):
        """
        Atomically reserve 1 voucher for a given service code using
        PostgreSQL 'SELECT ... FOR UPDATE SKIP LOCKED'.
        Returns voucher record or False.
        """
        cr = env.cr
        today = fields.Date.context_today(env.user)
        query = """
            SELECT id FROM recharge_voucher
            WHERE service_code = %s
              AND state = 'available'
              AND expiry_date >= %s
            ORDER BY expiry_date ASC, id ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """
        cr.execute(query, (service_code, today))
        res = cr.fetchone()
        if not res:
            return False

        voucher_id = res[0]
        now = fields.Datetime.now()
        cr.execute("""
            UPDATE recharge_voucher
            SET state = 'reserved',
                reservation_token = %s,
                reservation_time = %s,
                store_id = %s
            WHERE id = %s
        """, (reservation_token, now, store_id or None, voucher_id))

        return env["recharge.voucher"].sudo().browse(voucher_id)

    def release_reservation(self):
        """Releases a reserved voucher back to available stock."""
        for v in self:
            if v.state == "reserved":
                v.write({
                    "state": "available",
                    "reservation_token": False,
                    "reservation_time": False,
                })
                _logger.info("Released voucher reservation ID %s (Serial %s)", v.id, v.serial_number)

    @api.model
    def cron_cleanup_expired_reservations(self):
        """
        Cron job to release abandoned or timed-out reservations back to available stock.
        """
        timeout_threshold = fields.Datetime.now() - timedelta(minutes=RESERVATION_TIMEOUT_MINUTES)
        stale_vouchers = self.search([
            ("state", "=", "reserved"),
            ("reservation_time", "<", timeout_threshold),
        ])
        if stale_vouchers:
            _logger.warning("Releasing %d stale voucher reservations", len(stale_vouchers))
            stale_vouchers.release_reservation()

    @api.model
    def cron_mark_expired_vouchers(self):
        """
        Cron job to mark past-expiry vouchers as expired.
        """
        today = fields.Date.context_today(self)
        expired_vouchers = self.search([
            ("state", "in", ["available", "reserved"]),
            ("expiry_date", "<", today),
        ])
        if expired_vouchers:
            _logger.info("Marking %d vouchers as expired", len(expired_vouchers))
            expired_vouchers.write({
                "state": "expired",
                "reservation_token": False,
            })

    def action_block(self):
        """Block voucher (for refund, recall, or investigation)."""
        self.ensure_one()
        self.write({"state": "blocked"})

    def action_unblock(self):
        """Unblock voucher if verified."""
        self.ensure_one()
        if self.state == "blocked":
            self.write({"state": "available"})
