# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError


class RechargeVoucherIssue(models.Model):
    _name = "recharge.voucher.issue"
    _description = "Recharge Voucher Issuance Audit Record"
    _order = "issue_date desc, id desc"

    name = fields.Char(
        string="Issue Reference",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
    )
    voucher_id = fields.Many2one(
        "recharge.voucher",
        string="Voucher",
        required=True,
        ondelete="restrict",
        index=True,
    )
    serial_number = fields.Char(
        string="Serial Number", related="voucher_id.serial_number", store=True
    )
    service_code = fields.Char(
        string="Service Code", related="voucher_id.service_code", store=True
    )
    face_value = fields.Monetary(
        string="Denomination",
        related="voucher_id.face_value",
        currency_field="currency_id",
        store=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        required=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", string="Currency"
    )

    pos_order_id = fields.Many2one(
        "pos.order",
        string="POS Order",
        required=True,
        ondelete="cascade",
        index=True,
    )
    pos_config_id = fields.Many2one(
        "pos.config",
        string="Store / Terminal",
        required=True,
        index=True,
    )
    pos_session_id = fields.Many2one(
        "pos.session",
        string="POS Session",
        required=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Cashier",
        required=True,
        default=lambda self: self.env.user,
    )
    issue_date = fields.Datetime(
        string="Issued At",
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    idempotency_key = fields.Char(
        string="Idempotency Key",
        index=True,
        copy=False,
        help="Unique key from POS (Order UID + Line CID) preventing duplicate allocation.",
    )
    state = fields.Selection(
        [
            ("issued", "Issued"),
            ("reprinted", "Reprinted"),
            ("refunded", "Refunded"),
            ("voided", "Voided"),
        ],
        string="Issue Status",
        default="issued",
        required=True,
    )

    reprint_log_ids = fields.One2many(
        "recharge.voucher.reprint.log",
        "issue_id",
        string="Reprint History",
    )
    reprint_count = fields.Integer(
        string="Reprint Count",
        compute="_compute_reprint_count",
        store=True,
    )

    refund_reason = fields.Text(string="Refund Reason")
    refund_date = fields.Datetime(string="Refunded At")
    refund_user_id = fields.Many2one("res.users", string="Refunded By")

    _sql_constraints = [
        (
            "idempotency_key_uniq",
            "unique(idempotency_key)",
            "An issue with this Idempotency Key has already been created!",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "recharge.voucher.issue"
                ) or _("ISSUE/%s") % fields.Datetime.now().strftime("%Y%m%d%H%M%S")
        return super().create(vals_list)

    @api.depends("reprint_log_ids")
    def _compute_reprint_count(self):
        for issue in self:
            issue.reprint_count = len(issue.reprint_log_ids)

    def action_controlled_reprint(self, reason, terminal_name="POS"):
        """
        Executes a controlled reprint of an already-issued voucher.
        Logs the user, timestamp, and justification reason.
        Decodes and returns the PIN strictly for receipt reprinting.
        Never allocates a second voucher!
        """
        self.ensure_one()
        if self.state == "refunded":
            raise UserError(_("Cannot reprint a voucher that has been refunded!"))

        # Create audit log record
        self.env["recharge.voucher.reprint.log"].create({
            "issue_id": self.id,
            "user_id": self.env.user.id,
            "reason": reason,
            "terminal": terminal_name,
        })
        self.write({"state": "reprinted"})

        # Decrypt PIN for receipt reprint
        pin = self.voucher_id.with_context(pos_issuance_verified=True).decrypt_pin(
            context_reason=f"Controlled Reprint: {reason}"
        )

        return {
            "service_code": self.voucher_id.service_code,
            "product_name": self.voucher_id.product_tmpl_name,
            "serial_number": self.voucher_id.serial_number,
            "pin": pin,
            "face_value": self.voucher_id.face_value,
            "expiry_date": str(self.voucher_id.expiry_date),
            "reprint_count": self.reprint_count,
        }

    def action_process_refund(self, reason):
        """
        Processes a refund for a revealed PIN.
        Critical rule: The voucher is BLOCKED, NEVER returned to available stock!
        """
        self.ensure_one()
        if self.state == "refunded":
            raise UserError(_("This voucher issue has already been marked as refunded!"))

        self.write({
            "state": "refunded",
            "refund_reason": reason,
            "refund_date": fields.Datetime.now(),
            "refund_user_id": self.env.user.id,
        })

        # Block the underlying voucher record
        self.voucher_id.write({
            "state": "blocked",
            "block_reason": f"Refunded for POS Order {self.pos_order_id.name}. Reason: {reason}",
        })


class RechargeVoucherReprintLog(models.Model):
    _name = "recharge.voucher.reprint.log"
    _description = "Voucher Reprint Audit Log"
    _order = "reprint_date desc"

    issue_id = fields.Many2one(
        "recharge.voucher.issue",
        string="Issue Record",
        required=True,
        ondelete="cascade",
    )
    reprint_date = fields.Datetime(
        string="Reprint Time",
        required=True,
        default=fields.Datetime.now,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Reprinted By",
        required=True,
        default=lambda self: self.env.user,
    )
    reason = fields.Char(string="Reprint Reason / Justification", required=True)
    terminal = fields.Char(string="Terminal / POS Station")
