# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class RechargeVoucherReprintWizard(models.TransientModel):
    _name = "recharge.voucher.reprint.wizard"
    _description = "Controlled Voucher Reprint Wizard"

    issue_id = fields.Many2one(
        "recharge.voucher.issue",
        string="Issue Record",
        required=True,
        default=lambda self: self.env.context.get("active_id"),
    )
    serial_number = fields.Char(related="issue_id.serial_number", readonly=True)
    reason = fields.Selection(
        [
            ("printer_paper_jam", "Printer Paper Jam / Out of Paper"),
            ("printer_offline", "Printer Hardware Error / Offline"),
            ("customer_request", "Customer Damaged / Unreadable Receipt"),
            ("manager_override", "Manager Authorized Duplicate Copy"),
        ],
        string="Reprint Justification Reason",
        required=True,
    )
    notes = fields.Text(string="Additional Notes / Explanation", required=True)
    reprinted_pin = fields.Char(string="Revealed PIN (Copyable)", readonly=True)
    reprinted_receipt_html = fields.Html(string="Reprinted Voucher Ticket", readonly=True)
    state = fields.Selection([("draft", "Input Reason"), ("done", "Receipt Generated")], default="draft")

    def action_confirm_reprint(self):
        self.ensure_one()
        if not self.issue_id:
            raise UserError(_("No active voucher issue found to reprint."))

        full_reason = f"{dict(self._fields['reason'].selection).get(self.reason)} - {self.notes}"
        reprint_data = self.issue_id.action_controlled_reprint(
            reason=full_reason, terminal_name="Backend / Manager Station"
        )

        ticket_html = f"""
        <div style="font-family: monospace; border: 2px dashed #333; padding: 20px; max-width: 400px; background: #fffbe6; margin: 10px auto;">
            <div style="text-align: center; font-weight: bold; font-size: 16px;">*** DUPLICATE REPRINT COPY ***</div>
            <div style="text-align: center; margin-bottom: 10px;">RECHARGE VOUCHER</div>
            <hr style="border-top: 1px dashed #333;"/>
            <div><strong>Product:</strong> {reprint_data['product_name']}</div>
            <div><strong>Service Code:</strong> {reprint_data['service_code']}</div>
            <div><strong>Denomination:</strong> ₹{reprint_data['face_value']:.2f}</div>
            <div><strong>Serial Number:</strong> {reprint_data['serial_number']}</div>
            <div style="font-size: 18px; margin: 10px 0; background: #fff; padding: 8px; border: 1px solid #ccc; text-align: center;">
                <strong>PIN: {reprint_data['pin']}</strong>
            </div>
            <div><strong>Expiry Date:</strong> {reprint_data['expiry_date']}</div>
            <div style="font-size: 11px; margin-top: 10px; color: #555;">
                Reprinted By: {self.env.user.name}<br/>
                Reprint Count: {reprint_data['reprint_count']}<br/>
                Reason: {full_reason}
            </div>
        </div>
        """

        self.write({
            "state": "done",
            "reprinted_pin": reprint_data["pin"],
            "reprinted_receipt_html": ticket_html,
        })

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class RechargeVoucherRefundWizard(models.TransientModel):
    _name = "recharge.voucher.refund.wizard"
    _description = "Voucher Refund and Invalidation Wizard"

    issue_id = fields.Many2one(
        "recharge.voucher.issue",
        string="Issue Record",
        required=True,
        default=lambda self: self.env.context.get("active_id"),
    )
    serial_number = fields.Char(related="issue_id.serial_number", readonly=True)
    refund_reason = fields.Selection(
        [
            ("customer_return", "Customer Returned (PIN was already exposed)"),
            ("wrong_product", "Cashier Selected Wrong Product"),
            ("service_failure", "Telecom Operator Service Failure"),
            ("chargeback", "Bank Chargeback / Disputed Transaction"),
        ],
        string="Refund Reason",
        required=True,
    )
    explanation = fields.Text(string="Audit Justification", required=True)

    def action_confirm_refund(self):
        self.ensure_one()
        if not self.issue_id:
            raise UserError(_("No active voucher issue found."))

        full_reason = f"{dict(self._fields['refund_reason'].selection).get(self.refund_reason)} - {self.explanation}"
        self.issue_id.action_process_refund(reason=full_reason)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Refund Processed"),
                "message": _("Voucher %s has been BLOCKED and cannot be re-issued.") % self.serial_number,
                "type": "warning",
                "sticky": False,
            },
        }

