# -*- coding: utf-8 -*-
from odoo import models, fields, tools


class RechargeVoucherReconciliationReport(models.Model):
    _name = "recharge.voucher.reconciliation.report"
    _description = "Voucher Inventory Reconciliation Report"
    _auto = False
    _order = "service_code asc, batch_id desc"

    service_code = fields.Char(string="Service Code", readonly=True)
    batch_id = fields.Many2one("recharge.voucher.batch", string="Batch Reference", readonly=True)
    supplier_id = fields.Many2one("recharge.voucher.supplier", string="Supplier", readonly=True)
    store_id = fields.Many2one("pos.config", string="Store", readonly=True)
    company_id = fields.Many2one("res.company", string="Company", readonly=True)
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True)

    total_imported = fields.Integer(string="Total Imported", readonly=True)
    available_qty = fields.Integer(string="Available", readonly=True)
    reserved_qty = fields.Integer(string="Reserved", readonly=True)
    sold_qty = fields.Integer(string="Sold", readonly=True)
    blocked_qty = fields.Integer(string="Blocked", readonly=True)
    expired_qty = fields.Integer(string="Expired", readonly=True)

    total_purchase_cost = fields.Monetary(
        string="Total Purchase Cost", currency_field="currency_id", readonly=True
    )
    total_face_value = fields.Monetary(
        string="Total Face Value", currency_field="currency_id", readonly=True
    )
    sold_face_value = fields.Monetary(
        string="Sold Face Value", currency_field="currency_id", readonly=True
    )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    MIN(v.id) AS id,
                    v.service_code,
                    v.batch_id,
                    v.supplier_id,
                    v.store_id,
                    v.company_id,
                    c.currency_id,
                    COUNT(v.id) AS total_imported,
                    COUNT(CASE WHEN v.state = 'available' THEN 1 END) AS available_qty,
                    COUNT(CASE WHEN v.state = 'reserved' THEN 1 END) AS reserved_qty,
                    COUNT(CASE WHEN v.state = 'sold' THEN 1 END) AS sold_qty,
                    COUNT(CASE WHEN v.state = 'blocked' THEN 1 END) AS blocked_qty,
                    COUNT(CASE WHEN v.state = 'expired' THEN 1 END) AS expired_qty,
                    SUM(v.purchase_cost) AS total_purchase_cost,
                    SUM(v.face_value) AS total_face_value,
                    SUM(CASE WHEN v.state = 'sold' THEN v.face_value ELSE 0 END) AS sold_face_value
                FROM recharge_voucher v
                LEFT JOIN res_company c ON c.id = v.company_id
                GROUP BY
                    v.service_code,
                    v.batch_id,
                    v.supplier_id,
                    v.store_id,
                    v.company_id,
                    c.currency_id
            )
        """)
