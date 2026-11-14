"""SQL safety validation with SQLGlot. LLM-generated SQL is NEVER executed without passing every check here.

Checks: single statement, SELECT-only (CTEs / UNION allowed), no write/DDL/command nodes, no unknown functions,
only allow-listed tables and columns, only the public schema, forced row limit.
The database role is read-only as a second, independent layer of protection.
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.qualify import qualify

from app import config


class SQLValidationError(Exception):
    """Message is safe to show to the user / feed back to the SQL corrector."""


_FORBIDDEN_NODE_NAMES = {
    "Insert", "Update", "Delete", "Drop", "Alter", "AlterColumn", "Create", "Command", "Copy", "Merge",
    "TruncateTable", "Into", "Lock", "Set", "Use", "Grant", "Revoke", "Transaction", "Commit", "Rollback",
    "Analyze", "Pragma", "Declare", "Execute", "Show", "Describe", "LoadData", "Kill",
}
_BAD_TEXT = re.compile(r"\b(pg_[a-z_]+|information_schema|pg_catalog|lo_import|lo_export|dblink|set_config|current_setting)\b", re.I)


def validate(sql: str, allowed: dict[str, set[str]], max_rows: int | None = None) -> str:
    """Return the safe (re-generated, LIMIT-enforced) SQL string, or raise SQLValidationError."""
    max_rows = max_rows or config.SQL_MAX_ROWS
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise SQLValidationError("Empty query.")
    if len(sql) > 6000:
        raise SQLValidationError("Query is too long.")
    if _BAD_TEXT.search(sql):
        raise SQLValidationError("Query references a system object or function that is not allowed.")

    try:
        statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
    except ParseError as e:
        raise SQLValidationError(f"SQL syntax error: {str(e).splitlines()[0][:200]}") from None
    if len(statements) != 1:
        raise SQLValidationError("Exactly one SQL statement is allowed.")
    root = statements[0]

    if not isinstance(root, (exp.Select, exp.SetOperation)):
        raise SQLValidationError("Only SELECT queries are allowed.")

    for node in root.walk():
        n = node[0] if isinstance(node, tuple) else node
        if type(n).__name__ in _FORBIDDEN_NODE_NAMES:
            raise SQLValidationError(f"{type(n).__name__.upper()} is not allowed; only SELECT queries are permitted.")
        if isinstance(n, exp.Anonymous):
            raise SQLValidationError(f"Function '{n.name}' is not allowed.")

    cte_names = {c.alias for c in root.find_all(exp.CTE)}
    for t in root.find_all(exp.Table):
        if not t.name:
            raise SQLValidationError("Table-valued expressions are not allowed.")
        if t.name in cte_names and not t.db:
            continue
        if t.catalog or (t.db and t.db.lower() != "public"):
            raise SQLValidationError(f"Schema '{t.db or t.catalog}' is not allowed; use only the listed tables.")
        if t.name not in allowed:
            raise SQLValidationError(f"Table '{t.name}' is not available. Allowed tables: {', '.join(sorted(allowed))}.")

    schema = {t: {c: "text" for c in cols} for t, cols in allowed.items()}
    try:
        qualify(root.copy(), schema=schema, dialect="postgres", validate_qualify_columns=True, quote_identifiers=False)
    except Exception as e:  # noqa: BLE001  (OptimizeError and friends)
        msg = str(e).splitlines()[0][:200]
        raise SQLValidationError(f"Invalid column or reference: {msg}") from None

    # force a row limit
    lim = root.args.get("limit")
    n = None
    if lim is not None and isinstance(lim.expression, exp.Literal) and lim.expression.is_int:
        n = int(lim.expression.name)
    if n is None or n > max_rows:
        root = root.limit(max_rows, copy=False)
    return root.sql(dialect="postgres")
