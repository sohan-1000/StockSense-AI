"""Executes validated SQL through the read-only PostgreSQL role with a timeout and a row cap."""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from decimal import Decimal

import psycopg2
from psycopg2 import errors as pgerr

from app import config
from app.db.connection import readonly_dsn


class SQLExecutionError(Exception):
    """Sanitized database error (no connection details or internals)."""


@dataclass
class ExecResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    elapsed_ms: int


def _jsonable(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def execute(sql: str, max_rows: int | None = None) -> ExecResult:
    max_rows = max_rows or config.SQL_MAX_ROWS
    conn = None
    t0 = time.time()
    try:
        conn = psycopg2.connect(readonly_dsn(), connect_timeout=10)
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {int(config.SQL_TIMEOUT_MS)}")
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            fetched = cur.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = [[_jsonable(v) for v in r] for r in fetched[:max_rows]]
        return ExecResult(cols, rows, len(rows), truncated, int((time.time() - t0) * 1000))
    except pgerr.QueryCanceled:
        raise SQLExecutionError("The query took too long and was cancelled.") from None
    except psycopg2.Error as e:
        first = (e.pgerror or str(e)).splitlines()[0]
        raise SQLExecutionError(first.replace("ERROR:", "").strip()[:250]) from None
    finally:
        if conn is not None:
            conn.close()
