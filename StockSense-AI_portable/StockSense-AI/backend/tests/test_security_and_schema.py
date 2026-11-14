"""Security + schema tests (no LLM needed).  Run from backend/:  python -m pytest tests -q"""
import datetime as dt

import pytest

from app.nl2sql import dates
from app.nl2sql.catalog import get_catalog
from app.nl2sql.executor import SQLExecutionError, execute
from app.nl2sql.schema_builder import build
from app.nl2sql.validator import SQLValidationError, validate

CAT = get_catalog()
FULL = build(CAT, [], full=True)


# ---------------- catalog / schema builder ----------------
def test_catalog_reads_keys_from_database():
    assert CAT.tables["sales"].primary_key == ["date", "product_id"]
    assert {(f.column, f.ref_table) for f in CAT.tables["sales"].foreign_keys} == {("date", "calendar"), ("product_id", "products")}
    assert CAT.tables["products"].foreign_keys[0].ref_table == "suppliers"
    assert CAT.data_end_date == dt.date(2026, 10, 31)
    assert "Dairy" in CAT.tables["products"].columns["category"].sample_values


def test_join_closure_adds_bridge_table():
    s = build(CAT, ["sales", "suppliers"], {"sales": ["quantity_sold"], "suppliers": ["supplier_name"]})
    assert "products" in s.tables and "products" in s.added_tables
    assert "sales.product_id = products.product_id" in s.text
    assert "products.supplier_id = suppliers.supplier_id" in s.text
    assert "PRIMARY KEY: (date, product_id)" in s.text


def test_key_columns_always_included():
    s = build(CAT, ["batches"], {"batches": ["expiry_date"]})
    assert "batch_id" in s.allowed["batches"]


def test_unknown_tables_are_dropped_and_fall_back_to_full():
    s = build(CAT, ["customers"], {})
    assert set(s.tables) == set(CAT.tables)


# ---------------- validator: must pass ----------------
@pytest.mark.parametrize("sql", [
    "SELECT product_name, SUM(revenue) r FROM v_sales_enriched WHERE date >= '2026-10-01' GROUP BY 1 ORDER BY r DESC LIMIT 5",
    "WITH x AS (SELECT product_id, SUM(quantity_sold) q FROM sales GROUP BY 1) SELECT * FROM x WHERE q > 3",
    "SELECT p.product_name FROM products p JOIN suppliers s ON s.supplier_id = p.supplier_id",
    "SELECT category FROM products UNION SELECT category FROM v_batch_expiry",
])
def test_valid_queries_pass(sql):
    out = validate(sql, FULL.allowed)
    assert "LIMIT" in out.upper()


def test_limit_is_forced_and_capped():
    assert "LIMIT 500" in validate("SELECT * FROM products", FULL.allowed)
    assert "LIMIT 500" in validate("SELECT * FROM products LIMIT 100000", FULL.allowed)
    assert "LIMIT 10" in validate("SELECT * FROM products LIMIT 10", FULL.allowed)


# ---------------- validator: must be rejected ----------------
@pytest.mark.parametrize("sql", [
    "DROP TABLE sales",
    "DELETE FROM sales",
    "UPDATE products SET unit_cost = 0",
    "INSERT INTO suppliers VALUES ('X','x','x',1,1)",
    "ALTER TABLE sales ADD COLUMN x int",
    "TRUNCATE sales",
    "CREATE TABLE t AS SELECT * FROM sales",
    "SELECT * INTO newt FROM sales",
    "SELECT 1; DROP TABLE sales",
    "SELECT * FROM sales; SELECT * FROM products",
    "SELECT pg_sleep(10)",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM documents",
    "SELECT * FROM doc_chunks",
    "SELECT * FROM chat_log",
    "SELECT * FROM customers",
    "SELECT nonexistent_col FROM sales",
    "SELECT revenue FROM sales",
    "COPY sales TO '/tmp/x'",
    "SELECT current_setting('server_version')",
    "SELECT * FROM public.sales FOR UPDATE",
    "",
])
def test_unsafe_or_invalid_rejected(sql):
    with pytest.raises(SQLValidationError):
        validate(sql, FULL.allowed)


def test_pruned_schema_rejects_column_outside_selection():
    s = build(CAT, ["products"], {"products": ["product_name"]})
    with pytest.raises(SQLValidationError):
        validate("SELECT unit_cost FROM products", s.allowed)


# ---------------- executor: defence in depth ----------------
def test_executor_runs_select():
    r = execute(validate("SELECT count(*) AS n FROM sales", FULL.allowed))
    assert r.rows == [[20070]] and r.columns == ["n"]


def test_executor_readonly_role_blocks_writes_even_if_validator_bypassed():
    for bad in ["DELETE FROM sales", "UPDATE products SET unit_cost = 1", "DROP TABLE sales", "SELECT * FROM doc_chunks"]:
        with pytest.raises(SQLExecutionError):
            execute(bad)


def test_executor_timeout():
    with pytest.raises(SQLExecutionError):
        execute("SELECT count(*) FROM sales a, sales b, sales c")  # cross join, must hit the 5s timeout


# ---------------- date resolution ----------------
A = dt.date(2026, 10, 31)


def R(**kw):
    return dates.resolve(dates.Period(**kw), A)


def test_month_without_year_uses_latest_occurrence():
    r = R(type="month", month=10)
    assert (r.start, r.end) == (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    r = R(type="month", month=11)  # November 2026 is after the data ends
    assert r.start == dt.date(2025, 11, 1) and r.note


def test_relative_periods_anchor_to_data_end_not_today():
    assert R(type="relative", unit="month", offset=-1).start == dt.date(2026, 9, 1)
    assert R(type="relative", unit="month", offset=-1).end == dt.date(2026, 9, 30)
    assert R(type="relative", unit="month", offset=0).end == A
    w = R(type="relative", unit="week", offset=-1)
    assert (w.start, w.end) == (dt.date(2026, 10, 19), dt.date(2026, 10, 25))
    assert R(type="last_n_days", n=7).start == dt.date(2026, 10, 25)
    q = R(type="quarter", quarter=3)
    assert (q.start, q.end) == (dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    assert R(type="relative", unit="year", offset=-1).start == dt.date(2025, 1, 1)


# ---------------- deterministic relative-date rules ----------------
def _norm(q, llm=()):
    return [(r.label, str(r.start), str(r.end)) for r in dates.resolve_all(dates.normalize_periods(q, list(llm)), A)[0]]


def test_last_month_ignores_llm_guess():
    bad = dates.Period(type="month", month=1)  # what the no-thinking LLM produced for "last month"
    assert _norm("What was the revenue last month?", [bad]) == [("September 2026", "2026-09-01", "2026-09-30")]


def test_named_month_is_kept_and_hallucinated_month_dropped():
    assert _norm("Revenue in October", [dates.Period(type="month", month=10)])[0][1] == "2026-10-01"
    assert _norm("Top products", [dates.Period(type="month", month=3)]) == []


def test_other_relative_phrases():
    assert _norm("sales yesterday")[0][1:] == ("2026-10-30", "2026-10-30")
    assert _norm("revenue in the last 7 days")[0][1:] == ("2026-10-25", "2026-10-31")
    assert _norm("profit in the last 3 months")[0][1:] == ("2026-08-01", "2026-10-31")
    assert _norm("revenue this week")[0][1:] == ("2026-10-26", "2026-10-31")
