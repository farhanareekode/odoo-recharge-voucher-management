/** @odoo-modules */

import { Order, Orderline } from "@point_of_sale/app/store/models";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { parseFloat as oParseFloat } from "@web/views/fields/parsers";

patch(PosStore.prototype, {
    async _processData(loadedData) {
        await super._processData(...arguments);
        this.recharge_voucher_product = loadedData["recharge.voucher.product"] || [];
    },
});

patch(Orderline.prototype, {
    set_quantity(quantity, keep_price) {
        const quant =
            typeof quantity === "number" ? quantity : oParseFloat("" + (quantity ? quantity : 0));

        // Real-time stock validation at quantity addition / numpad entry
        const voucherProducts = this.pos.recharge_voucher_product || [];
        const vp = voucherProducts.find((v) => {
            const pid = Array.isArray(v.product_id) ? v.product_id[0] : v.product_id;
            return pid === this.product.id;
        });

        if (vp && quant > 0) {
            const otherQty = this.order
                .get_orderlines()
                .filter((l) => l !== this && l.product.id === this.product.id)
                .reduce((sum, l) => sum + l.get_quantity(), 0);

            const totalRequested = otherQty + quant;
            if (totalRequested > vp.available_count) {
                this.env.services.popup.add(ErrorPopup, {
                    title: _t("Insufficient Voucher Stock"),
                    body: _t(
                        `Only ${vp.available_count} voucher(s) available in stock for "${vp.name}" (${vp.service_code}). You cannot add ${totalRequested}!`
                    ),
                });
                return false;
            }
        }
        return super.set_quantity(...arguments);
    },
});

patch(Order.prototype, {
    setup() {
        super.setup(...arguments);
        this.voucher_issued_details = this.voucher_issued_details || [];
    },

    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.voucher_issued_details = json.voucher_issued_details || [];
    },

    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.voucher_issued_details = this.voucher_issued_details || [];
        return json;
    },

    hasVoucherLines() {
        const voucherProducts = this.pos.recharge_voucher_product || [];
        const voucherProductIds = new Set(
            voucherProducts.map((vp) => (Array.isArray(vp.product_id) ? vp.product_id[0] : vp.product_id))
        );
        return this.get_orderlines().some((line) =>
            voucherProductIds.has(line.product.id)
        );
    },

    wait_for_push_order() {
        if (this.hasVoucherLines()) {
            return true;
        }
        return super.wait_for_push_order(...arguments);
    },

    export_for_printing() {
        const result = super.export_for_printing(...arguments);
        result.voucher_issued_details = this.voucher_issued_details || [];
        return result;
    },
});



