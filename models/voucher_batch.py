# -*- coding: utf-8 -*-
from odoo import models, fields, api


class RechargeVoucherBatch(models.Model):
    _name = "recharge.voucher.batch"
    _description = "Recharge Voucher Batch"
    _order = "date_received desc, id desc"

    name = fields.Char(string="Batch Reference", required=True, copy=False)
    supplier_id = fields.Many2one(
        "recharge.voucher.supplier", string="Supplier", required=True
    )
    date_received = fields.Date(
        string="Date Received", default=fields.Date.context_today, required=True
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
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("closed", "Closed"),
        ],
        string="Status",
        default="active",
        required=True,
    )
    voucher_ids = fields.One2many(
        "recharge.voucher", "batch_id", string="Vouchers in Batch"
    )

    # Statistics computed dynamically (store=False prevents parent row locking during high concurrency)
    total_vouchers = fields.Integer(
        string="Total Vouchers", compute="_compute_voucher_statistics"
    )
    available_count = fields.Integer(
        string="Available", compute="_compute_voucher_statistics"
    )
    reserved_count = fields.Integer(
        string="Reserved", compute="_compute_voucher_statistics"
    )
    sold_count = fields.Integer(
        string="Sold", compute="_compute_voucher_statistics"
    )
    blocked_count = fields.Integer(
        string="Blocked", compute="_compute_voucher_statistics"
    )
    expired_count = fields.Integer(
        string="Expired", compute="_compute_voucher_statistics"
    )
    total_purchase_cost = fields.Monetary(
        string="Total Purchase Cost",
        compute="_compute_voucher_statistics",
        currency_field="currency_id",
    )
    total_face_value = fields.Monetary(
        string="Total Face Value",
        compute="_compute_voucher_statistics",
        currency_field="currency_id",
    )
    notes = fields.Text(string="Notes")

    _sql_constraints = [
        ("name_uniq", "unique(name)", "Batch reference must be unique!"),
    ]

    def _compute_voucher_statistics(self):
        Voucher = self.env["recharge.voucher"]
        for batch in self:
            vouchers = batch.voucher_ids
            batch.total_vouchers = len(vouchers)
            batch.available_count = len(vouchers.filtered(lambda v: v.state == "available"))
            batch.reserved_count = len(vouchers.filtered(lambda v: v.state == "reserved"))
            batch.sold_count = len(vouchers.filtered(lambda v: v.state == "sold"))
            batch.blocked_count = len(vouchers.filtered(lambda v: v.state == "blocked"))
            batch.expired_count = len(vouchers.filtered(lambda v: v.state == "expired"))
            batch.total_purchase_cost = sum(vouchers.mapped("purchase_cost"))
            batch.total_face_value = sum(vouchers.mapped("face_value"))

    def action_view_vouchers(self):
        self.ensure_one()
        return {
            "name": f"Vouchers - {self.name}",
            "type": "ir.actions.act_window",
            "res_model": "recharge.voucher",
            "view_mode": "tree,form,pivot",
            "domain": [("batch_id", "=", self.id)],
            "context": {"default_batch_id": self.id, "default_supplier_id": self.supplier_id.id},
        }

    def action_close_batch(self):
        self.write({"state": "closed"})

    def action_reopen_batch(self):
        self.write({"state": "active"})
