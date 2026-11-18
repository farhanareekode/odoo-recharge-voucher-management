# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class RechargeVoucherProduct(models.Model):
    _name = "recharge.voucher.product"
    _description = "Recharge Voucher Product Configuration"
    _order = "service_code asc"

    name = fields.Char(string="Voucher Product Name", required=True)
    service_code = fields.Char(
        string="Service Code", required=True, copy=False, index=True
    )
    product_id = fields.Many2one(
        "product.product",
        string="Linked POS Product",
        required=True,
        ondelete="restrict",
        help="Underlying product used for POS sales and accounting.",
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
    face_value = fields.Monetary(
        string="Face Value / Denomination",
        required=True,
        currency_field="currency_id",
        help="Nominal prepaid value loaded onto the customer SIM or service account.",
    )
    selling_price = fields.Monetary(
        string="Selling Price",
        required=True,
        currency_field="currency_id",
        help="Actual price charged to the customer at POS checkout.",
    )
    min_stock_alert = fields.Integer(
        string="Minimum Stock Alert",
        default=10,
        help="Alert threshold for low inventory.",
    )
    active = fields.Boolean(string="Active", default=True)
    notes = fields.Text(string="Description / Terms")

    # Inventory Counts
    voucher_count = fields.Integer(
        string="Total Inventory", compute="_compute_voucher_stock"
    )
    available_count = fields.Integer(
        string="Available Stock", compute="_compute_voucher_stock", search="_search_available_count"
    )
    reserved_count = fields.Integer(
        string="Reserved Stock", compute="_compute_voucher_stock"
    )
    sold_count = fields.Integer(
        string="Sold Count", compute="_compute_voucher_stock"
    )

    _sql_constraints = [
        ("service_code_uniq", "unique(service_code)", "Service Code must be unique!"),
        (
            "product_id_uniq",
            "unique(product_id)",
            "A POS Product can only be mapped to one Voucher Service Code!",
        ),
        (
            "face_value_positive",
            "CHECK(face_value > 0)",
            "Face value must be greater than zero!",
        ),
        (
            "selling_price_positive",
            "CHECK(selling_price >= 0)",
            "Selling price cannot be negative!",
        ),
    ]

    def _search_available_count(self, operator, value):
        if operator not in ("=", "!=", "<", "<=", ">", ">="):
            return []
        try:
            val = int(value)
        except (ValueError, TypeError):
            return []
        self.env.cr.execute(f"""
            SELECT vp.id
            FROM recharge_voucher_product vp
            LEFT JOIN recharge_voucher v ON v.service_code = vp.service_code AND v.state = 'available'
            GROUP BY vp.id
            HAVING COUNT(v.id) {operator} %s
        """, (val,))
        res = [r[0] for r in self.env.cr.fetchall()]
        return [("id", "in", res)]

    def _compute_voucher_stock(self):
        Voucher = self.env["recharge.voucher"]
        for record in self:
            domain = [("service_code", "=", record.service_code)]
            record.voucher_count = Voucher.search_count(domain)
            record.available_count = Voucher.search_count(
                domain + [("state", "=", "available")]
            )
            record.reserved_count = Voucher.search_count(
                domain + [("state", "=", "reserved")]
            )
            record.sold_count = Voucher.search_count(
                domain + [("state", "=", "sold")]
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            record._sync_pos_product_price()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "selling_price" in vals or "name" in vals:
            for record in self:
                record._sync_pos_product_price()
        return res

    def _sync_pos_product_price(self):
        """Ensure linked product.product has selling price and POS flag active."""
        if self.product_id:
            self.product_id.sudo().write(
                {
                    "lst_price": self.selling_price,
                    "available_in_pos": True,
                    "type": "service",
                }
            )

    def action_view_vouchers(self):
        self.ensure_one()
        return {
            "name": f"Vouchers - {self.name} ({self.service_code})",
            "type": "ir.actions.act_window",
            "res_model": "recharge.voucher",
            "view_mode": "tree,form,pivot",
            "domain": [("service_code", "=", self.service_code)],
            "context": {
                "default_service_code": self.service_code,
                "default_face_value": self.face_value,
            },
        }

    def action_view_available_vouchers(self):
        self.ensure_one()
        return {
            "name": f"Available Vouchers - {self.service_code}",
            "type": "ir.actions.act_window",
            "res_model": "recharge.voucher",
            "view_mode": "tree,form",
            "domain": [
                ("service_code", "=", self.service_code),
                ("state", "=", "available"),
            ],
            "context": {
                "default_service_code": self.service_code,
                "default_face_value": self.face_value,
            },
        }
