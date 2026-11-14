"""extract -> clean -> check -> save, for one bill."""
import hashlib
import os
from datetime import date

import cleaning
import checks
import db
import matching
import retail
import sarvam_client

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
LANGUAGES = ["en-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "mr-IN", "bn-IN", "gu-IN"]


class DuplicateBill(Exception):
    def __init__(self, existing_id):
        super().__init__(f"This bill was already uploaded (bill #{existing_id})")
        self.existing_id = existing_id


class InvalidFile(Exception):
    pass


class PostError(Exception):
    """The bill cannot be added to inventory (see the message)."""


class InvalidEdit(Exception):
    """A manually edited value could not be understood (e.g. a date or amount)."""


def normalize(raw):
    """Clean the raw Sarvam fields into the shape we store."""
    gstin, gstin_valid = cleaning.clean_gstin(raw.get("merchant_gstin"))
    items = []
    for item in raw.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        clean = {
            "description": cleaning.clean_text(item.get("description")),
            "quantity": cleaning.clean_amount(item.get("quantity")),
            "unit_price": cleaning.clean_amount(item.get("unit_price")),
            "amount": cleaning.clean_amount(item.get("amount")),
            "product_id": cleaning.clean_text(item.get("product_id")),
            "units": cleaning.clean_amount(item.get("units")),
        }
        if any(clean[k] is not None for k in ("description", "quantity", "unit_price", "amount")):
            items.append(clean)
    return {
        "merchant_name": cleaning.clean_text(raw.get("merchant_name")),
        "merchant_gstin": gstin,
        "gstin_valid": gstin_valid,
        "bill_number": cleaning.clean_text(raw.get("bill_number")),
        "bill_date": cleaning.clean_date(raw.get("bill_date")),
        "payment_mode": cleaning.clean_payment_mode(raw.get("payment_mode")),
        "subtotal": cleaning.clean_amount(raw.get("subtotal")),
        "tax_amount": cleaning.clean_amount(raw.get("tax_amount")),
        "discount": cleaning.clean_amount(raw.get("discount")),
        "total_amount": cleaning.clean_amount(raw.get("total_amount")),
        "line_items": items,
    }


def _products():
    """Catalogue products for matching; empty if the retail DB is not reachable."""
    try:
        return retail.list_products()
    except retail.RetailError:
        return []


def process_bill(content, filename, language="en-IN", extract=None):
    """Run one bill through the whole pipeline and return its transaction id.

    Raises InvalidFile, DuplicateBill or sarvam_client.SarvamError.
    `extract` lets tests swap in a fake Sarvam call.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise InvalidFile("Please upload a JPG, PNG or PDF file")
    if not content:
        raise InvalidFile("The uploaded file is empty")
    if language not in LANGUAGES:
        language = "en-IN"

    file_hash = hashlib.sha256(content).hexdigest()
    existing = db.find_by_hash(file_hash)
    if existing:
        raise DuplicateBill(existing)

    raw = (extract or sarvam_client.extract_bill)(filename, content, language)
    bill = normalize(raw)
    bill["line_items"] = matching.apply(bill["line_items"], _products())
    issues = checks.run_checks(bill)
    status = "pending_review" if issues else "verified"

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    image_name = file_hash[:16] + ext
    with open(os.path.join(UPLOAD_DIR, image_name), "wb") as f:
        f.write(content)

    return db.save_bill(bill, file_hash, os.path.basename(filename), image_name,
                        language, raw, status, issues)


EDITABLE_AMOUNTS = ("subtotal", "tax_amount", "discount", "total_amount")


def update_bill(txn_id, fields):
    """Apply a manual edit: clean the values, re-run the checks, save.

    `fields` has the same keys as the Sarvam output (strings are fine).
    Raises LookupError if the bill is missing, InvalidEdit for unreadable values.
    """
    existing = db.get_bill(txn_id)
    if not existing:
        raise LookupError("Bill not found")
    if existing["status"] in ("posted", "posting"):
        raise InvalidEdit("This bill was already added to inventory and can no longer be edited")

    problems = []
    date = fields.get("bill_date")
    if date and not cleaning.clean_date(date):
        problems.append(f"Date '{date}' is not a readable date (try DD/MM/YYYY)")
    for key in EDITABLE_AMOUNTS:
        value = fields.get(key)
        if value not in (None, "") and cleaning.clean_amount(value) is None:
            problems.append(f"{key.replace('_', ' ').capitalize()} '{value}' is not a number")
    for n, item in enumerate(fields.get("line_items") or [], start=1):
        for key in ("quantity", "unit_price", "amount", "units"):
            value = item.get(key)
            if value not in (None, "") and cleaning.clean_amount(value) is None:
                problems.append(f"Item {n} {key.replace('_', ' ')} '{value}' is not a number")
    if problems:
        raise InvalidEdit("; ".join(problems))

    bill = normalize(fields)
    issues = checks.run_checks(bill)
    if existing["status"] == "rejected":
        status = "rejected"
    else:
        status = "pending_review" if issues else "verified"
    db.update_bill(txn_id, bill, status, issues)
    return status


def post_to_inventory(txn_id):
    """Add a reviewed bill's matched items to the retail `batches` and `inventory`.

    Items with no product, or no units, are skipped.
    Returns (created_batches, skipped_item_count).
    """
    bill = db.get_bill(txn_id)
    if not bill:
        raise LookupError("Bill not found")
    if bill["status"] in ("posted", "posting"):
        raise PostError("This bill was already added to inventory")
    if bill["status"] == "rejected":
        raise PostError("This bill is rejected, so it cannot be added to inventory")
    if not bill["bill_date"]:
        raise PostError("The bill needs a date before it can be added (it becomes the received date)")

    ready = [i for i in bill["line_items"]
             if i.get("product_id") and i.get("units") and i["units"] > 0]
    if not ready:
        raise PostError("No line item is matched to a product with units to add. "
                        "Pick a product for each item and save first.")

    for i in ready:
        if i["units"] != int(i["units"]):
            raise PostError(f"'{i.get('description') or i['product_id']}' has {i['units']} units. "
                            "Stock is counted in whole units, so edit it to a whole number and save.")

    previous = db.claim_for_posting(txn_id)
    if previous is None:
        raise PostError("This bill is already being added to inventory")
    try:
        created = retail.add_stock(ready, date.fromisoformat(bill["bill_date"]))
    except Exception as e:
        db.release_posting(txn_id, previous)
        if isinstance(e, retail.RetailError):
            raise PostError(str(e))
        raise
    db.mark_posted(txn_id, created)
    return created, len(bill["line_items"]) - len(ready)
