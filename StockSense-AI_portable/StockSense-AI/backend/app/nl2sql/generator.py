"""LLM CALL 2 (and corrections): generate one PostgreSQL SELECT from the question and the pruned schema."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app import config
from app.llm import sarvam
from app.nl2sql.catalog import get_catalog

SYSTEM = """You write PostgreSQL SELECT queries for StockSense AI, a retail inventory assistant.

HARD RULES
- Output exactly ONE read-only SELECT statement (a WITH ... SELECT is fine). No INSERT/UPDATE/DELETE/DDL.
- Use ONLY the tables and columns listed in SCHEMA. Never invent tables, columns, values or joins.
- Use the VALID JOINS listed; do not join on anything else.
- Use the dates given under RESOLVED DATES exactly (inclusive). Never use CURRENT_DATE or NOW(): the data has a fixed end date.
- Give every computed column a readable alias (total_revenue, units_sold, ...). Use ROUND(...,2) for averages and percentages.
- If the question asks for a "top N", ORDER BY the metric DESC and LIMIT N. For rankings show the metric next to the name.
- Text filters on names: use ILIKE with % wildcards. Category and festival values must match the listed example values.
- If the question cannot be answered from the schema (data not present), do NOT guess: reply with exactly
  UNANSWERABLE: <short reason>

OUTPUT FORMAT: only the SQL inside a ```sql code block (or the UNANSWERABLE line). No explanation.

BUSINESS RULES
{rules}
"""


@dataclass
class GenResult:
    sql: str | None
    unanswerable: str | None = None


_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.I)


def parse_reply(text: str) -> GenResult:
    t = text.strip()
    m = re.search(r"UNANSWERABLE\s*:?\s*(.*)", t, re.I | re.S)
    if m and not re.search(r"\bSELECT\b", t.split("UNANSWERABLE")[0], re.I):
        return GenResult(None, m.group(1).strip().splitlines()[0][:300] if m.group(1).strip() else "Not answerable from the data.")
    b = _BLOCK.search(t)
    sql = (b.group(1) if b else t).strip()
    return GenResult(sql or None)


def generate(question: str, schema_text: str, period_text: str, examples: list[dict],
             prev_sql: str | None = None, prev_error: str | None = None) -> GenResult:
    cat = get_catalog()
    system = SYSTEM.format(rules="\n".join(f"- {r}" for r in cat.rules))
    ex = "\n\n".join(f"Question: {e['q']}\n```sql\n{e['sql'].strip()}\n```" for e in examples)
    user = f"SCHEMA\n{schema_text}\n\nRESOLVED DATES\n{period_text or 'No time period in the question.'}\n"
    if ex:
        user += f"\nSIMILAR SOLVED EXAMPLES (for style; the question may differ)\n{ex}\n"
    user += f"\nQUESTION\n{question}\n"
    if prev_sql:
        user += (f"\nYour previous SQL failed.\nPREVIOUS SQL:\n```sql\n{prev_sql}\n```\nERROR: {prev_error}\n"
                 "Write a corrected query that fixes this error, using only the listed schema.\n")
    return parse_reply(sarvam.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], label="sql", effort=config.SQL_REASONING_EFFORT))
