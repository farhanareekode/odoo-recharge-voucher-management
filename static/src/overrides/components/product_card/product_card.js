/** @odoo-modules */

import { ProductCard } from "@point_of_sale/app/generic_components/product_card/product_card";
import { patch } from "@web/core/utils/patch";

patch(ProductCard.prototype, {
    get voucherStock() {
        const pos = this.env.services?.pos;
        if (!pos || !pos.recharge_voucher_product) {
            return undefined;
        }
        const vp = pos.recharge_voucher_product.find((v) => {
            const pid = Array.isArray(v.product_id) ? v.product_id[0] : v.product_id;
            return pid === this.props.productId;
        });
        return vp ? vp.available_count : undefined;
    },
});

