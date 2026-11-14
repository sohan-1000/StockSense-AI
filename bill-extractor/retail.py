"""Access to the shared StockSense retail database (RETAIL_DATABASE_URL).

This app only READS `products` and `suppliers`, and only WRITES new rows to
`batches` and adds to `inventory.current_stock`. It never changes the structure
of any retail table and never touches sales, calendar or the chatbot's tables.
"""
import os
import re
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

# Optional schema to work in (tests use a throwaway one). None = default.
SCHEMA_NAME = None


class RetailError(Exception):
    pass


def configured():
    return bool(os.environ.get("RETAIL_DATABASE_URL"))


@contextmanager
def connect():
    url = os.environ.get("RETAIL_DATABASE_URL")
    if not url:
        raise RetailError("RETAIL_DATABASE_URL is not set (put it in .env)")
    try:
        with psycopg.connect(url, row_factory=dict_row) as conn:
            if SCHEMA_NAME:
                conn.execute(f'SET search_path TO "{SCHEMA_NAME}"')
            yield conn
    except psycopg.OperationalError as e:
        raise RetailError(f"Cannot reach the retail database: {e}")


def list_products():
    with connect() as conn:
        return conn.execute(
            "SELECT product_id, product_name, shelf_life_days, supplier_id "
            "FROM products ORDER BY product_id"
        ).fetchall()


def find_supplier(name):
    """Best-effort match of a bill's merchant name to suppliers.supplier_name."""
    if not name:
        return None
    with connect() as conn:
        return conn.execute(
            "SELECT supplier_id, supplier_name FROM suppliers "
            "WHERE lower(supplier_name) = lower(%s) "
            "OR lower(%s) LIKE '%%' || lower(supplier_name) || '%%' "
            "OR lower(supplier_name) LIKE '%%' || lower(%s) || '%%' "
            "ORDER BY supplier_id LIMIT 1",
            (name, name, name),
        ).fetchone()


def add_stock(items, received_date):
    """Add purchased stock in ONE transaction.

    For each product: insert a new `batches` row (expiry = received + shelf life)
    and add the units to `inventory.current_stock`. `items` is a list of
    {"product_id", "units"}; lines for the same product are combined.
    Returns the list of created batches.
    """
    totals = {}
    for item in items:
        pid = item["product_id"]
        units = Decimal(str(item["units"]))
        if units != units.to_integral_value() or units <= 0:
            raise RetailError(f"{pid}: units must be a positive whole number, got {item['units']}")
        totals[pid] = totals.get(pid, Decimal(0)) + units

    created = []
    try:
        with connect() as conn:
            for pid, units in sorted(totals.items()):
                product = conn.execute(
                    "SELECT shelf_life_days FROM products WHERE product_id = %s FOR UPDATE",
                    (pid,),
                ).fetchone()
                if not product:
                    raise RetailError(f"Product {pid} does not exist in the retail database")

                # batch ids look like B + product digits + 2-digit sequence (B00101)
                prefix = "B" + re.sub(r"\D", "", pid)
                seqs = [
                    int(r["batch_id"][len(prefix):])
                    for r in conn.execute(
                        "SELECT batch_id FROM batches WHERE product_id = %s", (pid,)
                    ).fetchall()
                    if r["batch_id"].startswith(prefix) and r["batch_id"][len(prefix):].isdigit()
                ]
                batch_id = f"{prefix}{max(seqs, default=0) + 1:02d}"

                shelf = product["shelf_life_days"]
                expiry = received_date + timedelta(days=shelf) if shelf is not None else None

                conn.execute(
                    "INSERT INTO batches (batch_id, product_id, quantity_remaining, "
                    "received_date, expiry_date) VALUES (%s, %s, %s, %s, %s)",
                    (batch_id, pid, int(units), received_date, expiry),
                )
                # snapshot_date is the dataset's single "as of" date (the expiry views and the
                # chatbot measure from it), so an existing row keeps it and a new row copies it.
                conn.execute(
                    "INSERT INTO inventory (product_id, current_stock, snapshot_date, "
                    "avg_daily_sales_last_30d, reorder_point) "
                    "VALUES (%s, %s, COALESCE((SELECT max(snapshot_date) FROM inventory), %s), 0, 0) "
                    "ON CONFLICT (product_id) DO UPDATE SET "
                    "current_stock = inventory.current_stock + EXCLUDED.current_stock",
                    (pid, int(units), received_date),
                )
                created.append({
                    "batch_id": batch_id, "product_id": pid, "units": int(units),
                    "received_date": received_date.isoformat(),
                    "expiry_date": expiry.isoformat() if expiry else None,
                })
    except psycopg.Error as e:
        raise RetailError(f"Retail database rejected the stock update: {e}")
    return created
