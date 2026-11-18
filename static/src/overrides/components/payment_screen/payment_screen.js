/** @odoo-modules */

import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(PaymentScreen.prototype, {
    async _isOrderValid(isForceValidate) {
        if (this.currentOrder.hasVoucherLines()) {
            // Requirement: Disallow offline voucher issuance
            if (!navigator.onLine || (this.pos.synch && this.pos.synch.status === "disconnected")) {
                this.popup.add(ErrorPopup, {
                    title: _t("Offline Sale Blocked"),
                    body: _t(
                        "Recharge Vouchers cannot be sold offline! An active, secure server connection is required to allocate unique encrypted PINs."
                    ),
                });
                return false;
            }

            // Real-time Stock Availability Check before taking payment
            const voucherProducts = this.pos.recharge_voucher_product || [];
            const prodMap = {};
            for (const vp of voucherProducts) {
                const pid = Array.isArray(vp.product_id) ? vp.product_id[0] : vp.product_id;
                prodMap[pid] = vp;
            }

            for (const line of this.currentOrder.get_orderlines()) {
                const vp = prodMap[line.product.id];
                if (vp) {
                    try {
                        const stockCheck = await this.orm.call(
                            "pos.order",
                            "check_voucher_availability",
                            [vp.service_code]
                        );
                        if (stockCheck && stockCheck.available_count < line.get_quantity()) {
                            this.popup.add(ErrorPopup, {
                                title: _t("Insufficient Voucher Stock"),
                                body: _t(
                                    `Only ${stockCheck.available_count} voucher(s) available in stock for "${vp.name}" (${vp.service_code}), but you are requesting ${line.get_quantity()}! Please reduce the quantity or import more stock.`
                                ),
                            });
                            return false;
                        }
                    } catch (e) {
                        console.error("Stock check error:", e);
                    }
                }
            }
        }
        return super._isOrderValid(...arguments);
    },

    async _postPushOrderResolve(order, server_ids) {
        if (order.hasVoucherLines() && server_ids && server_ids.length > 0) {
            try {
                const issuedVouchers = await this.orm.call(
                    "pos.order",
                    "process_pos_voucher_issuance",
                    [server_ids[0]]
                );
                order.voucher_issued_details = issuedVouchers || [];
            } catch (error) {
                const errorMsg = (error && error.data && error.data.message) || (error && error.message) || _t("Unknown error");
                this.popup.add(ErrorPopup, {
                    title: _t("Voucher Issuance Error"),
                    body: _t(
                        "The order was paid but an error occurred while retrieving voucher PINs: " + errorMsg
                    ),
                });
                return false;
            }
        }
        return super._postPushOrderResolve(...arguments);
    },
});

