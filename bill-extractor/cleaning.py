"""Turn messy OCR-ish values into clean amounts, dates, GSTINs and payment modes."""
import re
from datetime import datetime

GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d",
    "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %y",
    "%b %d, %Y", "%B %d, %Y",
]


def clean_amount(value):
    """'₹1,234.50', 'Rs. 99/-', 12 -> float (2dp). None if unreadable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = re.sub(r"(?i)rs\.?|inr|₹|/-", "", text)
    text = text.replace(",", "").replace(" ", "")
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group())
    return round(-number if negative else number, 2)


def clean_date(value):
    """Return an ISO date (YYYY-MM-DD) or None. Ambiguous dates are day-first."""
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    # drop a trailing time, e.g. '25/12/2024 14:32'
    text = re.sub(r"[ T]\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?$", "", text, flags=re.I)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def clean_gstin(value):
    """Upper-case and strip a GSTIN. Returns (gstin_or_None, is_valid)."""
    if not value:
        return None, True
    gstin = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
    if not gstin:
        return None, True
    return gstin, bool(GSTIN_RE.match(gstin))


def clean_payment_mode(value):
    if not value:
        return None
    text = str(value).lower()
    if any(k in text for k in ("upi", "gpay", "google pay", "phonepe", "paytm", "bhim")):
        return "upi"
    if any(k in text for k in ("card", "visa", "master", "rupay", "debit")):
        return "card"
    if "credit" in text or "udhar" in text or "due" in text:
        return "credit"
    if "cash" in text:
        return "cash"
    return "other"


def clean_text(value):
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None
