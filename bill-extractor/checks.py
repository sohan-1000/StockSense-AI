"""Sanity checks on a cleaned bill. Returns a list of human-readable issues."""


def _close(a, b):
    return abs(a - b) <= max(1.0, 0.005 * max(abs(a), abs(b)))


def run_checks(bill):
    issues = []

    if not bill.get("merchant_name"):
        issues.append("Merchant name is missing")
    if bill.get("total_amount") is None:
        issues.append("Total amount is missing")
    if not bill.get("bill_date"):
        issues.append("Bill date is missing or unreadable")
    if not bill.get("gstin_valid", True):
        issues.append(f"GSTIN '{bill.get('merchant_gstin')}' is not a valid GSTIN format")

    total = bill.get("total_amount")
    subtotal = bill.get("subtotal")
    if total is not None and subtotal is not None:
        expected = subtotal + (bill.get("tax_amount") or 0) - (bill.get("discount") or 0)
        if not _close(expected, total) and not _close(subtotal, total):
            issues.append(
                f"Totals do not add up: subtotal {subtotal} + tax "
                f"{bill.get('tax_amount') or 0} - discount {bill.get('discount') or 0} "
                f"= {expected:.2f}, but total is {total}"
            )

    amounts = [i["amount"] for i in bill.get("line_items", []) if i.get("amount") is not None]
    if amounts:
        items_sum = sum(amounts)
        targets = [t for t in (subtotal, total) if t is not None]
        if targets and not any(_close(items_sum, t) for t in targets):
            if subtotal is not None and total is not None:
                against = f"neither the subtotal ({subtotal}) nor the total ({total})"
            else:
                against = f"the bill total ({targets[0]})"
            issues.append(f"Line items add up to {items_sum:.2f}, which does not match {against}")
    return issues
