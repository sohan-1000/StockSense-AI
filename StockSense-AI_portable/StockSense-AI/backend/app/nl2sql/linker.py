"""LLM CALL 1: routing + schema linking.

One call decides (a) which pipeline handles the question, (b) which tables/columns are needed, and
(c) the time periods mentioned (as a structured object - dates are resolved by code, not by the LLM).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.i18n import detect_language
from app.llm import sarvam
from app.nl2sql.catalog import Catalog, get_catalog
from app.nl2sql.dates import Period


class LinkResult(BaseModel):
    route: Literal["sql", "rag", "unsupported"]
    reasoning: str = ""
    tables: list[str] = Field(default_factory=list)
    columns: dict[str, list[str]] = Field(default_factory=dict)
    periods: list[Period] = Field(default_factory=list)
    unsupported_reason: str = ""
    question_en: str = ""  # English version of the question (the LLM translates; set to the original if already English)
    language: str = "en"  # set by code from the script of the question, never by the LLM


SYSTEM = """You are the routing and schema-linking component of StockSense AI, an assistant for small retail (kirana) shop owners.

STEP A - choose a route:
- "sql": the question can be answered from the shop's own data described in the CATALOG below (sales, revenue, profit, products, categories, stock, reorder, expiry, suppliers, sales trends over time of THEIR data).
- "rag": the question is NOT answerable from the data tables: information ABOUT the shop itself (name, owner, location, opening/operating hours, business profile, goals, preferences), policies, guidelines, how-to, business advice, general or market knowledge, or anything unrelated to the data tables (even nonsense or general-knowledge questions). A search of the shop's documents decides later whether an answer exists.
- "unsupported": ONLY when the user asks for specific transaction records, counts or amounts that the catalog does not contain (customers, bills, number of returns, store/branch comparisons, wastage amounts, purchase orders, staff performance). Never use "unsupported" for questions about the shop's profile, hours, policies or advice: those are "rag".
"Trend" of the shop's own sales/stock/revenue => "sql". Trend of the market or general retail advice => "rag".

STEP B - only for route "sql": pick the minimum set of tables, and inside each table the columns needed to answer
(columns to SELECT, filter, group, sort, aggregate or join on). Be generous: include any column that might be needed.
- Prefer the views (v_sales_enriched, v_inventory_status, v_batch_expiry) over joining raw tables when a view has what is needed.
- Use only table and column names that appear in the CATALOG, spelled exactly.

STEP C - "periods": list every time period the question mentions, using these JSON shapes (copy the user's meaning, do NOT compute dates):
  {"type":"month","month":10,"year":null}              a named month (year only if the user said it)
  {"type":"year","year":2026}
  {"type":"quarter","quarter":3,"year":null}
  {"type":"relative","unit":"month","offset":-1}       unit: day|week|month|quarter|year ; offset 0 = current, -1 = previous
  {"type":"last_n_days","n":30}
  {"type":"range","start":"2026-07-01","end":"2026-07-31"}   only when the user gives explicit dates
Use [] when the question has no time period. A comparison of two months has two periods.

STEP D - "question_en": the user may write in English, Hindi or Telugu. Give the question in clear English.
If it is already English, repeat it unchanged. When translating, translate only the sentence: keep product names, supplier names,
festival names, numbers, and dates exactly as the user wrote them (transliterate native-script names to their usual English spelling,
for example "सनफ्लावर ऑयल" -> "Sunflower Oil", "అక్టోబర్" -> "October"). Keep month and year words so dates stay explicit.
Apply Steps A-C to the meaning of the question regardless of its language.

Reply with ONLY one JSON object:
{"route":"sql|rag|unsupported","reasoning":"one short sentence","tables":["..."],"columns":{"table":["col","..."]},"periods":[...],"unsupported_reason":"only for unsupported, in English: what data is missing","question_en":"the question in English"}

CATALOG (tables and views with columns):
"""


def _sanitize(res: LinkResult, cat: Catalog) -> LinkResult:
    """Drop anything the LLM invented: unknown tables / columns."""
    res.tables = [t for t in dict.fromkeys(res.tables) if t in cat.tables]
    clean: dict[str, list[str]] = {}
    for t, cols in res.columns.items():
        if t in cat.tables:
            valid = [c for c in cols if c in cat.tables[t].columns]
            if valid:
                clean[t] = valid
                if t not in res.tables:
                    res.tables.append(t)
    res.columns = clean
    return res


def link(question: str) -> LinkResult:
    cat = get_catalog()
    msgs = [
        {"role": "system", "content": SYSTEM + cat.brief_text()},
        {"role": "user", "content": question},
    ]
    res = _sanitize(sarvam.chat_json(msgs, LinkResult, label="link"), cat)
    res.language = detect_language(question)  # deterministic: by script
    # English questions are never rewritten by the model; others use its translation (fallback: original text)
    res.question_en = question if res.language == "en" else (res.question_en.strip() or question)
    return res
