# -*- coding: utf-8 -*-
from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _pos_ui_models_to_load(self):
        models_to_load = super()._pos_ui_models_to_load()
        if "recharge.voucher.product" not in models_to_load:
            models_to_load.append("recharge.voucher.product")
        return models_to_load

    def _loader_params_recharge_voucher_product(self):
        return {
            "search_params": {
                "domain": [("active", "=", True)],
                "fields": [
                    "name",
                    "service_code",
                    "product_id",
                    "face_value",
                    "selling_price",
                    "available_count",
                ],
            }
        }

    def _get_pos_ui_recharge_voucher_product(self, params):
        return self.env["recharge.voucher.product"].search_read(
            **params["search_params"]
        )

