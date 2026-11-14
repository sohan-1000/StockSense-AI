"""Idempotent CSV -> PostgreSQL ingestion.   Run from backend/:  python -m app.db.ingest

Re-running upserts on primary keys, so it never creates duplicates.
"""
from __future__ import annotations

import csv
from pathlib import Path

from psycopg2.extras import execute_values

from app import config
from app.db.connection import RO_ROLE, owner_conn

HERE = Path(__file__).parent


def _b(v: str) -> bool:
    return v.strip() in ("1", "True", "true")


def _n(v: str):
    v = v.strip()
    return v if v != "" else None


# (table, columns exactly as in the CSV header, converters, conflict key)
SPEC = [
    ("suppliers", ["supplier_id", "supplier_name", "city", "average_lead_time_days", "minimum_order_value"], {}, ["supplier_id"]),
    ("products", ["product_id", "product_name", "category", "selling_price", "unit_cost", "shelf_life_days",
                  "supplier_lead_time_days", "minimum_order_quantity", "base_daily_demand", "supplier_id",
                  "reorder_target_days"], {}, ["product_id"]),
    ("calendar", ["date", "day_of_week", "is_weekend", "month", "festival", "is_festival"],
     {"is_weekend": _b, "is_festival": _b, "festival": _n}, ["date"]),
    ("inventory", ["product_id", "current_stock", "snapshot_date", "avg_daily_sales_last_30d", "reorder_point"], {}, ["product_id"]),
    ("batches", ["batch_id", "product_id", "quantity_remaining", "received_date", "expiry_date"], {}, ["batch_id"]),
    ("sales", ["date", "product_id", "quantity_sold", "promotion_applied", "selling_price", "festival"],
     {"promotion_applied": _b, "festival": _n}, ["date", "product_id"]),
]

RETAIL_OBJECTS = ["suppliers", "products", "calendar", "inventory", "batches", "sales",
                  "v_sales_enriched", "v_inventory_status", "v_batch_expiry"]


def load_table(cur, table, cols, conv, key) -> int:
    path = config.DATASET_DIR / f"{table}.csv"
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = set(cols) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name}: missing columns {missing}")
        rows = [tuple(conv.get(c, lambda x: x)(r[c]) for c in cols) for r in reader]
    update = [c for c in cols if c not in key]
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s ON CONFLICT ({', '.join(key)}) DO UPDATE SET "
           + ", ".join(f"{c}=EXCLUDED.{c}" for c in update))
    execute_values(cur, sql, rows, page_size=2000)
    return len(rows)


def setup_roles(cur) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (RO_ROLE,))
    if not cur.fetchone():
        cur.execute(f"CREATE ROLE {RO_ROLE} LOGIN PASSWORD 'chatbot_ro_pw'")
    cur.execute(f"ALTER ROLE {RO_ROLE} SET default_transaction_read_only = on")
    cur.execute(f"ALTER ROLE {RO_ROLE} SET statement_timeout = {config.SQL_TIMEOUT_MS}")
    cur.execute("SELECT current_database()")
    db = cur.fetchone()[0]
    cur.execute(f'GRANT CONNECT ON DATABASE "{db}" TO {RO_ROLE}')
    cur.execute(f"GRANT USAGE ON SCHEMA public TO {RO_ROLE}")
    cur.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {RO_ROLE}")
    # documents / doc_chunks / chat_* are deliberately NOT granted
    cur.execute(f"GRANT SELECT ON {', '.join(RETAIL_OBJECTS)} TO {RO_ROLE}")


def main() -> None:
    with owner_conn() as conn:
        cur = conn.cursor()
        cur.execute((HERE / "schema.sql").read_text(encoding="utf-8"))
        for table, cols, conv, key in SPEC:
            n = load_table(cur, table, cols, conv, key)
            print(f"{table:10s} upserted {n} rows")
        cur.execute((HERE / "views.sql").read_text(encoding="utf-8"))
        setup_roles(cur)
    print("ingestion complete")


if __name__ == "__main__":
    main()
