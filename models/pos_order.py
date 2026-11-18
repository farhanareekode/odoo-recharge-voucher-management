# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    voucher_issue_ids = fields.One2many(
        "recharge.voucher.issue",
        "pos_order_id",
        string="Issued Vouchers",
        copy=False,
    )
    voucher_count = fields.Integer(
        string="Voucher Count",
        compute="_compute_voucher_count",
    )
    has_voucher = fields.Boolean(
        string="Contains Vouchers",
        compute="_compute_voucher_count",
        search="_search_has_voucher",
    )

    @api.depends("voucher_issue_ids")
    def _compute_voucher_count(self):
        for order in self:
            order.voucher_count = len(order.voucher_issue_ids)
            order.has_voucher = order.voucher_count > 0

    def _search_has_voucher(self, operator, value):
        if operator in ("=", "!="):
            issue_orders = self.env["recharge.voucher.issue"].search([]).mapped("pos_order_id.id")
            if (operator == "=" and value) or (operator == "!=" and not value):
                return [("id", "in", issue_orders)]
            else:
                return [("id", "not in", issue_orders)]
        return []

    def action_view_vouchers(self):
        self.ensure_one()
        return {
            "name": f"Issued Vouchers - {self.name}",
            "type": "ir.actions.act_window",
            "res_model": "recharge.voucher.issue",
            "view_mode": "tree,form",
            "domain": [("pos_order_id", "=", self.id)],
        }

    def _process_saved_order(self, draft):
        res = super()._process_saved_order(draft)
        for order in self:
            if not draft and order.state in ("paid", "done", "invoiced"):
                voucher_products = self.env["recharge.voucher.product"].sudo().search([
                    ("product_id", "in", order.lines.mapped("product_id").ids)
                ])
                if voucher_products:
                    try:
                        order.sudo().process_pos_voucher_issuance(order.id)
                    except Exception as e:
                        _logger.error("Error auto-issuing vouchers for order %s: %s", order.name, e)
        return res

    @api.model
    def check_voucher_availability(self, service_code):
        """
        Check real-time stock count for a given service code.
        Returns count of available, unexpired vouchers.
        """
        today = fields.Date.context_today(self)
        count = self.env["recharge.voucher"].sudo().search_count([
            ("service_code", "=", service_code),
            ("state", "=", "available"),
            ("expiry_date", ">=", today),
        ])
        return {"service_code": service_code, "available_count": count}

    @api.model
    def reserve_vouchers_for_pos(self, pos_reference, lines_data, config_id=False):
        """
        Reserve vouchers for lines added in POS.
        lines_data: list of dicts: [{'service_code': 'AIR-100', 'line_cid': 'c1', 'qty': 1}, ...]
        Uses SELECT ... FOR UPDATE SKIP LOCKED.
        """
        Voucher = self.env["recharge.voucher"].sudo()
        reserved = []
        errors = []

        for line in lines_data:
            service_code = line.get("service_code")
            line_cid = line.get("line_cid")
            qty = int(line.get("qty", 1))

            for i in range(qty):
                token = f"{pos_reference}_{line_cid}_{i}"
                # Check if this token is already reserved
                existing = Voucher.search([("reservation_token", "=", token)], limit=1)
                if existing:
                    reserved.append({
                        "service_code": service_code,
                        "line_cid": line_cid,
                        "token": token,
                        "voucher_id": existing.id,
                        "serial_number": existing.serial_number,
                    })
                    continue

                voucher = Voucher.allocate_and_reserve(
                    self.env, service_code, token, store_id=config_id
                )
                if not voucher:
                    errors.append(f"Insufficient stock for service code {service_code}")
                    break
                reserved.append({
                    "service_code": service_code,
                    "line_cid": line_cid,
                    "token": token,
                    "voucher_id": voucher.id,
                    "serial_number": voucher.serial_number,
                })

        if errors:
            # Rollback reservations made in this call
            for item in reserved:
                v = Voucher.browse(item["voucher_id"])
                v.release_reservation()
            return {"success": False, "error": "; ".join(errors)}

        return {"success": True, "reserved": reserved}

    @api.model
    def release_order_reservations(self, pos_reference):
        """Release all reservations for a cancelled/cleared POS order."""
        vouchers = self.env["recharge.voucher"].sudo().search([
            ("reservation_token", "=like", f"{pos_reference}%"),
            ("state", "=", "reserved"),
        ])
        if vouchers:
            vouchers.release_reservation()
        return True

    @api.model
    def process_pos_voucher_issuance(self, order_id_or_ref, lines_payload=None):
        """
        Critical backend endpoint:
        1. Verifies order is paid.
        2. Idempotently allocates or confirms voucher sale.
        3. Decrypts PIN strictly in memory for receipt generation.
        4. Returns voucher list to POS frontend.
        """
        if isinstance(order_id_or_ref, int):
            order = self.sudo().browse(order_id_or_ref)
        else:
            order = self.sudo().search([("pos_reference", "=", order_id_or_ref)], limit=1)

        if not order or not order.exists():
            raise UserError(_("POS Order not found for voucher issuance!"))

        # Payment verification: Order must be paid or done!
        if order.state not in ("paid", "done", "invoiced"):
            raise UserError(
                _("Payment verification failed! Order state is '%s'. Vouchers can only be issued after successful payment.") % order.state
            )

        Voucher = self.env["recharge.voucher"].sudo()
        VoucherProduct = self.env["recharge.voucher.product"].sudo()
        Issue = self.env["recharge.voucher.issue"].sudo()
        issued_results = []

        # Find voucher products in this order lines
        for line in order.lines:
            # Check if product is mapped to a voucher product
            v_prod = VoucherProduct.search([("product_id", "=", line.product_id.id)], limit=1)
            if not v_prod:
                continue

            service_code = v_prod.service_code
            qty = int(line.qty)
            if qty <= 0:
                continue

            for idx in range(qty):
                # Unique idempotency key per order line unit
                idempotency_key = f"{order.pos_reference}_{line.id}_{idx}"

                # Idempotency check: Has this issue already been processed?
                existing_issue = Issue.search([("idempotency_key", "=", idempotency_key)], limit=1)
                if existing_issue:
                    _logger.info(
                        "Idempotent hit for key %s (Voucher %s)",
                        idempotency_key,
                        existing_issue.voucher_id.serial_number,
                    )
                    pin = existing_issue.voucher_id.with_context(
                        pos_issuance_verified=True
                    ).decrypt_pin(context_reason="Idempotent POS Issuance Re-fetch")
                    issued_results.append({
                        "service_code": existing_issue.service_code,
                        "product_name": v_prod.name,
                        "serial_number": existing_issue.serial_number,
                        "pin": pin,
                        "face_value": existing_issue.face_value,
                        "expiry_date": str(existing_issue.voucher_id.expiry_date),
                    })
                    continue

                # Look for an existing reservation for this order
                reservation_token_prefix = f"{order.pos_reference}"
                voucher = Voucher.search([
                    ("reservation_token", "=like", f"{reservation_token_prefix}%"),
                    ("service_code", "=", service_code),
                    ("state", "=", "reserved"),
                ], limit=1)

                if not voucher:
                    # Allocate atomically using SKIP LOCKED
                    voucher = Voucher.allocate_and_reserve(
                        self.env,
                        service_code,
                        idempotency_key,
                        store_id=order.config_id.id,
                    )

                if not voucher:
                    raise UserError(
                        _("Critical Allocation Failure: No available voucher stock found for service code %s!") % service_code
                    )

                # Finalize sale
                voucher.write({
                    "state": "sold",
                    "issued_date": fields.Datetime.now(),
                    "pos_order_id": order.id,
                    "store_id": order.config_id.id,
                    "reservation_token": False,
                })

                # Create audit issue record
                issue = Issue.create({
                    "voucher_id": voucher.id,
                    "pos_order_id": order.id,
                    "pos_config_id": order.config_id.id,
                    "pos_session_id": order.session_id.id,
                    "user_id": order.user_id.id,
                    "issue_date": fields.Datetime.now(),
                    "idempotency_key": idempotency_key,
                    "state": "issued",
                })
                voucher.write({"issue_id": issue.id})

                # Securely decrypt PIN in memory for receipt printing
                pin = voucher.with_context(pos_issuance_verified=True).decrypt_pin(
                    context_reason=f"POS Sale: Order {order.pos_reference}"
                )

                issued_results.append({
                    "service_code": voucher.service_code,
                    "product_name": v_prod.name,
                    "serial_number": voucher.serial_number,
                    "pin": pin,
                    "face_value": voucher.face_value,
                    "expiry_date": str(voucher.expiry_date),
                })

        return issued_results

    def action_pos_voucher_reprint(self, reason):
        """Backend action to reprint vouchers from order form with reason."""
        self.ensure_one()
        if not self.voucher_issue_ids:
            raise UserError(_("This order does not contain any issued vouchers."))
        results = []
        for issue in self.voucher_issue_ids:
            res = issue.action_controlled_reprint(reason=reason, terminal_name="Backend")
            results.append(res)
        return results

