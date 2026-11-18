# -*- coding: utf-8 -*-
{
    "name": "Recharge Voucher Management System",
    "version": "17.0.1.0.0",
    "category": "Point of Sale",
    "summary": "Prepaid Mobile & Gift Card Voucher Inventory Management with POS Issuance, AES-256-GCM PIN Encryption, and Concurrency Control",
    "description": """
Recharge Voucher Management System for Retail Chains
=====================================================
- Multi-Store Prepaid Voucher Management (Bengaluru, Hyderabad, Chennai)
- Configurable Voucher Denominations (Face Value vs Selling Price)
- AES-256-GCM Authenticated Encryption for PIN storage
- Keyed HMAC-SHA256 Fingerprint for O(1) duplicate PIN detection without plaintext exposure
- Strict CSV Import Manifest Wizard with row-level validation & preview
- Concurrency-safe atomic voucher allocation via PostgreSQL SELECT ... FOR UPDATE SKIP LOCKED
- Seamless Odoo POS (Owl 2) integration: voucher allocation, payment verification, and receipt printing
- Exception handling: controlled reprint audit trail, refund voucher blocking, offline sale blocking
- Role-based security for Cashier, Store Manager, Finance, Auditor, and Operations
- Comprehensive Inventory Reconciliation & Sales Reporting
    """,
    "author": "Antigravity Senior Odoo Architect",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "point_of_sale",
        "stock",
    ],
    "data": [
        "security/groups.xml",
        "security/voucher_security_rules.xml",
        "security/ir.model.access.csv",
        "data/voucher_sequence.xml",
        "data/ir_cron.xml",
        "data/voucher_product_data.xml",
        "views/voucher_action_wizard_views.xml",
        "views/voucher_import_wizard_views.xml",
        "views/voucher_product_views.xml",
        "views/voucher_supplier_views.xml",
        "views/voucher_batch_views.xml",
        "views/voucher_views.xml",
        "views/voucher_issue_views.xml",
        "views/voucher_reconciliation_views.xml",
        "views/pos_order_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "recharge_voucher_management/static/src/**/*",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}

