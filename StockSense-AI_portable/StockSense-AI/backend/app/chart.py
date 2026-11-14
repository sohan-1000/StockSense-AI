"""Deterministic chart suggestion from the result shape (no LLM, so charts always match the data)."""
from __future__ import annotations

import re

_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?")


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def suggest(columns: list[str], rows: list[list]) -> dict | None:
    if len(columns) < 2 or not (2 <= len(rows) <= 60):
        return None
    numeric = [i for i in range(1, len(columns)) if all(_is_num(r[i]) or r[i] is None for r in rows) and any(r[i] is not None for r in rows)]
    if not numeric:
        return None
    first = [r[0] for r in rows]
    if all(isinstance(v, str) and _DATE.match(v) for v in first):
        return {"type": "line", "x": columns[0], "y": [columns[i] for i in numeric[:2]]}
    if all(isinstance(v, str) for v in first) and len(rows) <= 25:
        return {"type": "bar", "x": columns[0], "y": [columns[numeric[0]]]}
    return None
