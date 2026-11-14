"""Verify the database matches the CSV files exactly.   Run from backend/:  python scripts/verify_ingest.py"""
import csv
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402

from app import config  # noqa: E402
from app.db.connection import owner_conn, readonly_dsn  # noqa: E402

TABLES = ["suppliers", "products", "calendar", "inventory", "batches", "sales"]
failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def csv_rows(t):
    with open(config.DATASET_DIR / f"{t}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


with owner_conn() as conn:
    cur = conn.cursor()
    # 1. row counts
    for t in TABLES:
        cur.execute(f"SELECT count(*) FROM {t}")
        db_n, csv_n = cur.fetchone()[0], len(csv_rows(t))
        check(f"row count {t}", db_n == csv_n, f"db={db_n} csv={csv_n}")

    # 2. aggregate checks computed independently from the raw CSV
    sales = csv_rows("sales")
    csv_qty = sum(int(r["quantity_sold"]) for r in sales)
    csv_rev = sum(Decimal(r["quantity_sold"]) * Decimal(r["selling_price"]) for r in sales)
    cur.execute("SELECT sum(quantity_sold), sum(quantity_sold*selling_price) FROM sales")
    q, rev = cur.fetchone()
    check("total quantity sold", q == csv_qty, f"{q}")
    check("total revenue", Decimal(rev) == csv_rev, f"{rev}")
    cur.execute("SELECT sum(current_stock) FROM inventory")
    check("total stock", cur.fetchone()[0] == sum(int(r["current_stock"]) for r in csv_rows("inventory")))
    cur.execute("SELECT min(date), max(date) FROM sales")
    lo, hi = cur.fetchone()
    check("sales date range", (str(lo), str(hi)) == ("2025-01-01", "2026-10-31"), f"{lo}..{hi}")
    cur.execute("SELECT count(*) FROM sales WHERE promotion_applied")
    check("promo rows", cur.fetchone()[0] == sum(r["promotion_applied"] == "1" for r in sales))
    cur.execute("SELECT count(*) FROM calendar WHERE festival IS NOT NULL")
    check("festival days", cur.fetchone()[0] == sum(r["festival"] != "" for r in csv_rows("calendar")))

    # 3. relationship integrity (FKs enforce this, double-check explicitly)
    cur.execute("""SELECT count(*) FROM sales s LEFT JOIN products p USING(product_id) WHERE p.product_id IS NULL""")
    check("no orphan sales->products", cur.fetchone()[0] == 0)
    cur.execute("""SELECT count(*) FROM inventory i JOIN (SELECT product_id, sum(quantity_remaining) q FROM batches GROUP BY 1) b
                   USING(product_id) WHERE i.current_stock <> b.q""")
    check("inventory stock == sum(batches)", cur.fetchone()[0] == 0)

    # 4. views
    for v, n in [("v_sales_enriched", len(sales)), ("v_inventory_status", 30), ("v_batch_expiry", 51)]:
        cur.execute(f"SELECT count(*) FROM {v}")
        check(f"view {v} rows", cur.fetchone()[0] == n)
    cur.execute("SELECT sum(revenue) FROM v_sales_enriched")
    check("view revenue == table revenue", Decimal(cur.fetchone()[0]) == csv_rev)

# 5. read-only role: can read retail data, cannot write, cannot see app tables
try:
    ro = psycopg2.connect(readonly_dsn())
    ro.autocommit = True
    c = ro.cursor()
    c.execute("SELECT count(*) FROM v_sales_enriched")
    check("ro role can SELECT", c.fetchone()[0] == len(sales))
    for label, stmt in [("INSERT", "INSERT INTO suppliers VALUES ('X','x','x',1,1)"),
                        ("DELETE", "DELETE FROM sales"),
                        ("DROP", "DROP TABLE sales"),
                        ("read doc_chunks", "SELECT * FROM doc_chunks"),
                        ("read chat_log", "SELECT * FROM chat_log")]:
        try:
            c.execute(stmt)
            check(f"ro role blocked: {label}", False)
        except psycopg2.Error:
            check(f"ro role blocked: {label}", True)
except Exception as e:  # pragma: no cover
    check("ro role connect", False, str(e))

print("\nALL CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)
