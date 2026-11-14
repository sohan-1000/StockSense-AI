"""PostgreSQL storage: one `transactions` row per bill plus its `line_items`.

Connection comes from the DATABASE_URL environment variable (see .env.example).
"""
import os
from contextlib import contextmanager
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# statuses a person can set by hand; the app itself also uses "posting" and "posted"
STATUSES = ("verified", "pending_review", "rejected")

# Optional Postgres schema to work in (tests use a throwaway one). None = default.
SCHEMA_NAME = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    file_hash TEXT NOT NULL UNIQUE,
    filename TEXT,
    image_path TEXT,
    language TEXT,
    merchant_name TEXT,
    merchant_gstin TEXT,
    bill_number TEXT,
    bill_date DATE,
    payment_mode TEXT,
    subtotal NUMERIC(12, 2),
    tax_amount NUMERIC(12, 2),
    discount NUMERIC(12, 2),
    total_amount NUMERIC(12, 2),
    status TEXT NOT NULL,
    issues JSONB NOT NULL DEFAULT '[]',
    raw_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS line_items (
    id SERIAL PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    description TEXT,
    quantity NUMERIC(12, 3),
    unit_price NUMERIC(12, 2),
    amount NUMERIC(12, 2)
);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS manually_edited BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS posted_batches JSONB;
ALTER TABLE line_items ADD COLUMN IF NOT EXISTS product_id TEXT;
ALTER TABLE line_items ADD COLUMN IF NOT EXISTS units NUMERIC(12, 3);
"""

BILL_COLUMNS = (
    "merchant_name", "merchant_gstin", "bill_number", "bill_date", "payment_mode",
    "subtotal", "tax_amount", "discount", "total_amount",
)


def database_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Put it in .env, e.g. "
            "postgresql://postgres:<password>@localhost:5432/billextractor"
        )
    return url


@contextmanager
def connect():
    """Yield a connection that commits on success, rolls back on error, always closes."""
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        if SCHEMA_NAME:
            conn.execute(f'SET search_path TO "{SCHEMA_NAME}"')
        yield conn


def init_db():
    with connect() as conn:
        if SCHEMA_NAME:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_NAME}"')
            conn.execute(f'SET search_path TO "{SCHEMA_NAME}"')
        conn.execute(SCHEMA)


def drop_schema(name):
    """Used by tests to remove their throwaway schema."""
    with psycopg.connect(database_url()) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


LINE_INSERT = (
    "INSERT INTO line_items (transaction_id, description, quantity, unit_price, amount, "
    "product_id, units) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)


def _item_row(txn_id, i):
    return (txn_id, i.get("description"), i.get("quantity"), i.get("unit_price"),
            i.get("amount"), i.get("product_id"), i.get("units"))


def find_by_hash(file_hash):
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM transactions WHERE file_hash = %s", (file_hash,)
        ).fetchone()
    return row["id"] if row else None


def save_bill(bill, file_hash, filename, image_path, language, raw, status, issues):
    """Insert a bill and its line items; returns the new transaction id."""
    cols = ("file_hash", "filename", "image_path", "language", *BILL_COLUMNS,
            "status", "issues", "raw_json")
    values = (file_hash, filename, image_path, language,
              *(bill.get(c) for c in BILL_COLUMNS),
              status, Jsonb(issues), Jsonb(raw))
    with connect() as conn:
        txn_id = conn.execute(
            f"INSERT INTO transactions ({','.join(cols)}) "
            f"VALUES ({','.join(['%s'] * len(cols))}) RETURNING id",
            values,
        ).fetchone()["id"]
        with conn.cursor() as cur:
            cur.executemany(
                LINE_INSERT, [_item_row(txn_id, i) for i in bill.get("line_items", [])],
            )
    return txn_id


def _plain(row):
    """Make a DB row JSON-friendly: Decimal -> float, dates -> ISO strings."""
    out = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            value = float(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        out[key] = value
    return out


def list_bills():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM transactions ORDER BY id DESC").fetchall()
    return [_plain(r) for r in rows]


def get_bill(txn_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE id = %s", (txn_id,)).fetchone()
        if not row:
            return None
        items = conn.execute(
            "SELECT description, quantity, unit_price, amount, product_id, units FROM line_items "
            "WHERE transaction_id = %s ORDER BY id", (txn_id,)
        ).fetchall()
    bill = _plain(row)
    bill["line_items"] = [_plain(i) for i in items]
    return bill


def update_bill(txn_id, bill, status, issues):
    """Overwrite a bill's fields and line items after a manual edit.

    `raw_json` (Sarvam's original output) is left untouched.
    Returns False if the bill does not exist.
    """
    sets = ", ".join(f"{c} = %s" for c in BILL_COLUMNS)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE transactions SET {sets}, status = %s, issues = %s, manually_edited = true "
            f"WHERE id = %s",
            (*(bill.get(c) for c in BILL_COLUMNS), status, Jsonb(issues), txn_id),
        )
        if cur.rowcount == 0:
            return False
        conn.execute("DELETE FROM line_items WHERE transaction_id = %s", (txn_id,))
        with conn.cursor() as c2:
            c2.executemany(
                LINE_INSERT, [_item_row(txn_id, i) for i in bill.get("line_items", [])],
            )
    return True


def update_status(txn_id, status):
    """Set a bill's status. Returns False if the bill does not exist."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with connect() as conn:
        cur = conn.execute("UPDATE transactions SET status = %s WHERE id = %s", (status, txn_id))
        return cur.rowcount > 0


def claim_for_posting(txn_id):
    """Atomically move a bill to 'posting'. Returns its previous status, or None
    if it is missing, rejected, or already posted/being posted."""
    with connect() as conn:
        row = conn.execute(
            "WITH old AS (SELECT status FROM transactions WHERE id = %s FOR UPDATE) "
            "UPDATE transactions t SET status = 'posting' FROM old "
            "WHERE t.id = %s AND old.status IN ('verified', 'pending_review') "
            "RETURNING old.status AS prev",
            (txn_id, txn_id),
        ).fetchone()
    return row["prev"] if row else None


def release_posting(txn_id, previous_status):
    with connect() as conn:
        conn.execute("UPDATE transactions SET status = %s WHERE id = %s AND status = 'posting'",
                     (previous_status, txn_id))


def mark_posted(txn_id, batches):
    with connect() as conn:
        conn.execute(
            "UPDATE transactions SET status = 'posted', posted_at = now(), posted_batches = %s "
            "WHERE id = %s", (Jsonb(batches), txn_id))
