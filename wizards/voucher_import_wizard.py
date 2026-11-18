# -*- coding: utf-8 -*-
import base64
import csv
import io
import logging
from datetime import datetime
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from ..models.voucher_crypto import VoucherCryptoService

_logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "service_code",
    "serial_number",
    "pin_code",
    "batch_ref",
    "expiry_date",
    "purchase_cost",
]


class RechargeVoucherImportWizard(models.TransientModel):
    _name = "recharge.voucher.import.wizard"
    _description = "Recharge Voucher CSV Import Wizard"

    data_file = fields.Binary(string="CSV File", required=True)
    filename = fields.Char(string="File Name")
    supplier_id = fields.Many2one(
        "recharge.voucher.supplier",
        string="Voucher Supplier",
        required=True,
        help="Supplier from whom the vouchers were purchased.",
    )
    import_mode = fields.Selection(
        [
            ("strict", "Strict (Reject Entire Batch if any row is invalid)"),
            ("skip_invalid", "Permissive (Import valid rows, log rejected rows)"),
        ],
        string="Validation Policy",
        default="strict",
        required=True,
    )
    state = fields.Selection(
        [
            ("upload", "Upload File"),
            ("preview", "Preview & Validation"),
            ("done", "Import Summary"),
        ],
        string="Step",
        default="upload",
    )

    total_rows = fields.Integer(string="Total Rows Read", readonly=True)
    valid_rows = fields.Integer(string="Valid Rows", readonly=True)
    error_rows = fields.Integer(string="Rejected Rows", readonly=True)
    imported_count = fields.Integer(string="Successfully Imported", readonly=True)

    preview_html = fields.Html(string="Data Preview", readonly=True)
    summary_html = fields.Html(string="Import Summary", readonly=True)
    error_line_ids = fields.One2many(
        "recharge.voucher.import.error",
        "wizard_id",
        string="Validation Errors / Rejected Rows",
    )

    def _parse_csv_file(self):
        """Decode and parse the uploaded CSV file."""
        if not self.data_file:
            raise UserError(_("Please upload a CSV file first."))
        try:
            csv_data = base64.b64decode(self.data_file).decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                csv_data = base64.b64decode(self.data_file).decode("latin-1")
            except Exception as e:
                raise UserError(_("Could not decode CSV file. Please ensure it is UTF-8 encoded."))

        csv_reader = csv.DictReader(io.StringIO(csv_data))
        if not csv_reader.fieldnames:
            raise UserError(_("The uploaded file is empty or missing headers."))

        # Validate required headers
        fieldnames = [f.strip().lower() for f in csv_reader.fieldnames if f]
        missing = [col for col in REQUIRED_COLUMNS if col not in fieldnames]
        if missing:
            raise UserError(
                _("The CSV file is missing required columns: %s\nExpected headers: %s")
                % (", ".join(missing), ", ".join(REQUIRED_COLUMNS))
            )

        rows = []
        for idx, row in enumerate(csv_reader, start=2):  # line 2 is first data row
            cleaned_row = {k.strip().lower(): (v.strip() if v else "") for k, v in row.items() if k}
            cleaned_row["_row_num"] = idx
            rows.append(cleaned_row)

        return rows

    def action_preview_and_validate(self):
        """Parse, validate rows, and prepare preview."""
        self.ensure_one()
        rows = self._parse_csv_file()
        if not rows:
            raise UserError(_("No data rows found in the uploaded CSV file."))

        # Clear existing error lines
        self.error_line_ids.unlink()

        # Load active service codes from database
        voucher_products = self.env["recharge.voucher.product"].search([("active", "=", True)])
        valid_service_codes = {vp.service_code: vp for vp in voucher_products}

        # Sets for tracking in-file duplicates
        seen_serials = set()
        seen_pins = set()
        file_duplicate_serials = set()
        file_duplicate_pins = set()

        for r in rows:
            sn = r.get("serial_number", "")
            pin = r.get("pin_code", "")
            if sn:
                if sn in seen_serials:
                    file_duplicate_serials.add(sn)
                else:
                    seen_serials.add(sn)
            if pin:
                if pin in seen_pins:
                    file_duplicate_pins.add(pin)
                else:
                    seen_pins.add(pin)

        # Database checks for duplicates
        db_serials = set(
            self.env["recharge.voucher"]
            .search([("serial_number", "in", list(seen_serials))])
            .mapped("serial_number")
        )

        # Precompute fingerprints for database duplicate check
        pin_to_fp = {}
        for pin in seen_pins:
            pin_to_fp[pin] = VoucherCryptoService.compute_pin_fingerprint(pin)

        db_fps = set(
            self.env["recharge.voucher"]
            .search([("pin_fingerprint", "in", list(pin_to_fp.values()))])
            .mapped("pin_fingerprint")
        )

        today = fields.Date.context_today(self)
        errors = []
        valid_count = 0
        preview_rows = []

        for r in rows:
            row_num = r["_row_num"]
            sn = r.get("serial_number", "")
            pin = r.get("pin_code", "")
            code = r.get("service_code", "")
            batch_ref = r.get("batch_ref", "")
            expiry_str = r.get("expiry_date", "")
            cost_str = r.get("purchase_cost", "")

            row_errors = []

            # 1. Missing mandatory values
            if not code:
                row_errors.append("Missing service_code")
            if not sn:
                row_errors.append("Missing serial_number")
            if not pin:
                row_errors.append("Missing pin_code")
            if not batch_ref:
                row_errors.append("Missing batch_ref")
            if not expiry_str:
                row_errors.append("Missing expiry_date")
            if not cost_str:
                row_errors.append("Missing purchase_cost")

            # 2. Unknown service code
            if code and code not in valid_service_codes:
                row_errors.append(f"Unknown service code '{code}'")

            # 3. Duplicate serial in file
            if sn and sn in file_duplicate_serials:
                row_errors.append("Duplicate serial number within uploaded file")

            # 4. Duplicate PIN in file
            if pin and pin in file_duplicate_pins:
                row_errors.append("Duplicate PIN within uploaded file")

            # 5. Serial already in DB
            if sn and sn in db_serials:
                row_errors.append("Serial number already exists in database")

            # 6. PIN already in DB
            if pin and pin_to_fp.get(pin) in db_fps:
                row_errors.append("PIN code already registered in database (fingerprint collision)")

            # 7. Expiry date validation
            parsed_date = None
            if expiry_str:
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
                    try:
                        parsed_date = datetime.strptime(expiry_str, fmt).date()
                        break
                    except ValueError:
                        continue
                if not parsed_date:
                    row_errors.append(f"Invalid date format for '{expiry_str}' (expected YYYY-MM-DD)")
                elif parsed_date < today:
                    row_errors.append(f"Expiry date {parsed_date} is in the past")

            # 8. Purchase cost validation
            try:
                cost = float(cost_str)
                if cost < 0:
                    row_errors.append("Purchase cost cannot be negative")
            except (ValueError, TypeError):
                row_errors.append(f"Invalid numeric purchase cost '{cost_str}'")

            if row_errors:
                errors.append({
                    "wizard_id": self.id,
                    "row_number": row_num,
                    "serial_number": sn or "N/A",
                    "service_code": code or "N/A",
                    "error_message": "; ".join(row_errors),
                })
            else:
                valid_count += 1

            # Keep first 5 rows for HTML preview table
            if len(preview_rows) < 5:
                preview_rows.append({
                    "row": row_num,
                    "code": code,
                    "sn": sn,
                    "masked_pin": VoucherCryptoService.mask_pin(pin),
                    "batch": batch_ref,
                    "expiry": expiry_str,
                    "cost": cost_str,
                    "status": "Invalid" if row_errors else "Valid",
                })

        # Save errors
        if errors:
            self.env["recharge.voucher.import.error"].create(errors)

        # Generate HTML Preview
        preview_table = """
        <table class="table table-sm table-bordered table-striped">
            <thead class="table-dark">
                <tr>
                    <th>Row</th><th>Service Code</th><th>Serial Number</th>
                    <th>PIN (Masked)</th><th>Batch</th><th>Expiry</th><th>Cost</th><th>Status</th>
                </tr>
            </thead>
            <tbody>
        """
        for pr in preview_rows:
            badge_class = "badge bg-success text-white" if pr["status"] == "Valid" else "badge bg-danger text-white"
            preview_table += f"""
                <tr>
                    <td>{pr['row']}</td><td>{pr['code']}</td><td>{pr['sn']}</td>
                    <td><code>{pr['masked_pin']}</code></td><td>{pr['batch']}</td>
                    <td>{pr['expiry']}</td><td>₹{pr['cost']}</td>
                    <td><span class="{badge_class}">{pr['status']}</span></td>
                </tr>
            """
        preview_table += "</tbody></table>"

        self.write({
            "state": "preview",
            "total_rows": len(rows),
            "valid_rows": valid_count,
            "error_rows": len(errors),
            "preview_html": preview_table,
        })

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_confirm_import(self):
        """Execute the import according to policy."""
        self.ensure_one()
        if self.import_mode == "strict" and self.error_rows > 0:
            raise UserError(
                _("Strict Mode Enforcement: Cannot import batch because %d rows contain validation errors. "
                  "Please review the rejected rows below or switch to Permissive mode.")
                % self.error_rows
            )

        rows = self._parse_csv_file()
        error_row_nums = set(self.error_line_ids.mapped("row_number"))

        voucher_products = {
            vp.service_code: vp
            for vp in self.env["recharge.voucher.product"].search([("active", "=", True)])
        }
        batches = {}
        vouchers_to_create = []

        for r in rows:
            if r["_row_num"] in error_row_nums:
                continue

            code = r["service_code"]
            sn = r["serial_number"]
            pin = r["pin_code"]
            batch_ref = r["batch_ref"]
            expiry_str = r["expiry_date"]
            cost = float(r["purchase_cost"])

            v_prod = voucher_products[code]

            # Parse date
            parsed_date = None
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
                try:
                    parsed_date = datetime.strptime(expiry_str, fmt).date()
                    break
                except ValueError:
                    pass

            # Find or create batch
            if batch_ref not in batches:
                batch = self.env["recharge.voucher.batch"].search([
                    ("name", "=", batch_ref),
                    ("supplier_id", "=", self.supplier_id.id),
                ], limit=1)
                if not batch:
                    batch = self.env["recharge.voucher.batch"].create({
                        "name": batch_ref,
                        "supplier_id": self.supplier_id.id,
                        "date_received": fields.Date.context_today(self),
                        "state": "active",
                    })
                batches[batch_ref] = batch
            else:
                batch = batches[batch_ref]

            # Cryptographic operations
            enc_pin = VoucherCryptoService.encrypt_pin(pin)
            fp = VoucherCryptoService.compute_pin_fingerprint(pin)
            masked = VoucherCryptoService.mask_pin(pin)

            vouchers_to_create.append({
                "service_code": code,
                "face_value": v_prod.face_value,
                "purchase_cost": cost,
                "serial_number": sn,
                "encrypted_pin": enc_pin,
                "pin_fingerprint": fp,
                "masked_pin": masked,
                "batch_id": batch.id,
                "expiry_date": parsed_date,
                "state": "available",
            })

        # Multi-create vouchers inside atomic transaction
        created_vouchers = self.env["recharge.voucher"].create(vouchers_to_create)

        summary_msg = f"""
        <div class="alert alert-success">
            <h4>Import Completed Successfully!</h4>
            <p><strong>Total File Rows:</strong> {self.total_rows}</p>
            <p><strong>Successfully Created Vouchers:</strong> {len(created_vouchers)}</p>
            <p><strong>Rejected / Skipped Rows:</strong> {self.error_rows}</p>
            <p><strong>Batches Created/Updated:</strong> {', '.join(batches.keys())}</p>
        </div>
        """

        self.write({
            "state": "done",
            "imported_count": len(created_vouchers),
            "summary_html": summary_msg,
        })

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class RechargeVoucherImportError(models.TransientModel):
    _name = "recharge.voucher.import.error"
    _description = "Voucher Import Rejected Row"
    _order = "row_number asc"

    wizard_id = fields.Many2one("recharge.voucher.import.wizard", required=True, ondelete="cascade")
    row_number = fields.Integer(string="Line #", required=True)
    serial_number = fields.Char(string="Serial Number")
    service_code = fields.Char(string="Service Code")
    error_message = fields.Char(string="Rejection Reason", required=True)

