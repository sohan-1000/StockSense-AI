"""Match bill line items to retail products and convert pack sizes into units.

Pure string logic, no LLM and no database. Anything it is not sure about is left
unmatched, and the review screen lets the user pick the product.
"""
import re
from difflib import SequenceMatcher

AUTO_MATCH_SCORE = 0.8

# (regex alias, base unit, multiplier): kg -> grams, l -> millilitres
SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(kgs?|kilograms?|gms?|grams?|g|ltrs?|litres?|liters?|l|ml)\b",
    re.IGNORECASE,
)
UNIT_BASE = {
    "kg": ("g", 1000), "kgs": ("g", 1000), "kilogram": ("g", 1000), "kilograms": ("g", 1000),
    "g": ("g", 1), "gm": ("g", 1), "gms": ("g", 1), "gram": ("g", 1), "grams": ("g", 1),
    "l": ("ml", 1000), "ltr": ("ml", 1000), "ltrs": ("ml", 1000), "litre": ("ml", 1000),
    "litres": ("ml", 1000), "liter": ("ml", 1000), "liters": ("ml", 1000), "ml": ("ml", 1),
}


def parse_size(text):
    """'Basmati Rice 5kg' -> (5000.0, 'g'); None when there is no weight/volume."""
    match = SIZE_RE.search(text or "")
    if not match:
        return None
    base, mult = UNIT_BASE[match.group(2).lower()]
    return float(match.group(1)) * mult, base


def _name(text):
    text = SIZE_RE.sub(" ", (text or "").lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())


def _score(a, b):
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0
    return 0.6 * ratio + 0.4 * jaccard


def match_product(description, products):
    """Return (product, score) for the best match, or (None, score) if unsure."""
    name = _name(description)
    if not name:
        return None, 0.0
    scored = sorted(((_score(name, _name(p["product_name"])), p) for p in products),
                    key=lambda s: s[0], reverse=True)
    if not scored:
        return None, 0.0
    best, runner_up = scored[0], (scored[1][0] if len(scored) > 1 else 0)
    if best[0] >= AUTO_MATCH_SCORE and best[0] - runner_up >= 0.05:
        return best[1], best[0]
    return None, best[0]


def pack_factor(description, product_name):
    """How many catalogue units one bought pack is (5kg bill / 1kg product = 5).

    Returns 1 when sizes are missing or equal, and None when they can't be
    converted cleanly (the user should check the units).
    """
    bill, prod = parse_size(description), parse_size(product_name)
    if not bill or not prod:
        return 1
    if bill[1] != prod[1]:
        return None
    factor = bill[0] / prod[0]
    return factor if factor >= 1 and abs(factor - round(factor)) < 1e-9 else None


def apply(items, products):
    """Fill product_id and units on each line item. Mutates and returns items."""
    for item in items:
        item.setdefault("product_id", None)
        item.setdefault("units", None)
        if item.get("product_id") or not item.get("description"):
            continue
        product, _ = match_product(item["description"], products)
        if not product:
            continue
        item["product_id"] = product["product_id"]
        qty = item.get("quantity")
        factor = pack_factor(item["description"], product["product_name"])
        if qty is not None:
            item["units"] = round(qty * (factor or 1), 3)
    return items
