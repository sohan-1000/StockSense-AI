"""Expiry dashboard data. A plain parameterised query on the v_batch_expiry view through the READ-ONLY role.
No LLM involved, and no user text is ever concatenated into SQL.

days_to_expiry is measured from the inventory snapshot date (same definition the chatbot uses), so the tab and the
chat always agree. Status cut-offs follow the shop's own rules (document): <=3 days critical, <=7 days warning.
"""
from __future__ import annotations

import psycopg2

from app import config
from app.db.connection import readonly_dsn
from app.nl2sql.executor import _jsonable

CRITICAL_DAYS = 3
WARNING_DAYS = 7

_COLS = ["batch_id", "product_id", "product_name", "category", "quantity_remaining",
         "received_date", "expiry_date", "days_to_expiry", "value_at_risk"]


def status_for(days: int) -> str:
    if days < 0:
        return "expired"
    if days <= CRITICAL_DAYS:
        return "critical"
    if days <= WARNING_DAYS:
        return "warning"
    return "ok"


def get_expiry(days: int | None = None, category: str | None = None, search: str | None = None) -> dict:
    conn = psycopg2.connect(readonly_dsn(), connect_timeout=10)
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {int(config.SQL_TIMEOUT_MS)}")

            # summary over ALL batches (independent of the filters, so the cards stay stable)
            cur.execute("""
                SELECT count(*),
                       count(*) FILTER (WHERE days_to_expiry < 0),
                       count(*) FILTER (WHERE days_to_expiry <= %(c)s),
                       count(*) FILTER (WHERE days_to_expiry <= %(w)s),
                       count(*) FILTER (WHERE days_to_expiry <= 14),
                       count(*) FILTER (WHERE days_to_expiry <= 30),
                       COALESCE(sum(value_at_risk) FILTER (WHERE days_to_expiry <= %(w)s), 0),
                       COALESCE(sum(value_at_risk), 0),
                       (SELECT max(snapshot_date) FROM v_inventory_status)
                FROM v_batch_expiry""", {"c": CRITICAL_DAYS, "w": WARNING_DAYS})
            r = cur.fetchone()
            summary = {"total_batches": r[0], "expired": r[1], "within_3_days": r[2], "within_7_days": r[3],
                       "within_14_days": r[4], "within_30_days": r[5],
                       "value_at_risk_7_days": _jsonable(r[6]), "value_at_risk_total": _jsonable(r[7])}
            as_of = _jsonable(r[8])

            cur.execute("SELECT DISTINCT category FROM v_batch_expiry ORDER BY 1")
            categories = [x[0] for x in cur.fetchall()]

            cur.execute(f"""
                SELECT {', '.join(_COLS)} FROM v_batch_expiry
                WHERE (%(days)s::int IS NULL OR days_to_expiry <= %(days)s::int)
                  AND (%(cat)s::text IS NULL OR category = %(cat)s::text)
                  AND (%(q)s::text IS NULL OR product_name ILIKE %(like)s::text)
                ORDER BY days_to_expiry, product_name, batch_id""",
                {"days": days, "cat": category or None, "q": (search or None),
                 "like": f"%{(search or '').replace('%', '').replace('_', '')}%"})
            items = []
            for row in cur.fetchall():
                d = {c: _jsonable(v) for c, v in zip(_COLS, row)}
                d["status"] = status_for(d["days_to_expiry"])
                items.append(d)
    finally:
        conn.close()
    return {"as_of": as_of, "summary": summary, "categories": categories, "count": len(items), "items": items}
