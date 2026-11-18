# -*- coding: utf-8 -*-
from odoo import models, fields, api


class RechargeVoucherSupplier(models.Model):
    _name = "recharge.voucher.supplier"
    _description = "Recharge Voucher Supplier"
    _order = "name asc"

    name = fields.Char(string="Supplier Name", required=True)
    code = fields.Char(string="Supplier Code", required=True, copy=False)
    contact_name = fields.Char(string="Contact Person")
    email = fields.Char(string="Email")
    phone = fields.Char(string="Phone")
    address = fields.Text(string="Address")
    active = fields.Boolean(string="Active", default=True)

    batch_ids = fields.One2many(
        "recharge.voucher.batch", "supplier_id", string="Voucher Batches"
    )
    batch_count = fields.Integer(
        string="Total Batches", compute="_compute_batch_and_voucher_counts"
    )
    voucher_count = fields.Integer(
        string="Total Vouchers", compute="_compute_batch_and_voucher_counts"
    )

    _sql_constraints = [
        ("code_uniq", "unique(code)", "Supplier code must be unique!"),
    ]

    @api.depends("batch_ids", "batch_ids.total_vouchers")
    def _compute_batch_and_voucher_counts(self):
        for supplier in self:
            supplier.batch_count = len(supplier.batch_ids)
            supplier.voucher_count = sum(supplier.batch_ids.mapped("total_vouchers"))

    def action_view_batches(self):
        self.ensure_one()
        return {
            "name": "Voucher Batches",
            "type": "ir.actions.act_window",
            "res_model": "recharge.voucher.batch",
            "view_mode": "tree,form",
            "domain": [("supplier_id", "=", self.id)],
            "context": {"default_supplier_id": self.id},
        }

    def action_view_vouchers(self):
        self.ensure_one()
        return {
            "name": "Supplier Vouchers",
            "type": "ir.actions.act_window",
            "res_model": "recharge.voucher",
            "view_mode": "tree,form,pivot",
            "domain": [("supplier_id", "=", self.id)],
            "context": {"default_supplier_id": self.id},
        }
