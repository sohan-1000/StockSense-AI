"""NL2SQL pipeline (after routing):
 resolve dates -> build pruned schema -> LLM call 2 (SQL) -> validate -> execute
 on failure: sanitized error -> corrector (<= SQL_MAX_CORRECTIONS), every attempt re-validated; the first
 correction widens to the full schema in case schema linking missed a table/column.

Multilingual: SQL generation, date rules and few-shot retrieval all use the ENGLISH version of the question
(link.question_en); only the final explanation and fixed messages are written in the user's language (link.language).
"""
from __future__ import annotations

import time

from app import config
from app.i18n import t
from app.nl2sql import answer, dates, examples, generator
from app.nl2sql.catalog import get_catalog
from app.nl2sql.executor import SQLExecutionError, execute
from app.nl2sql.linker import LinkResult
from app.nl2sql.schema_builder import build
from app.nl2sql.validator import SQLValidationError, validate


def _period_text(resolved: list[dates.ResolvedPeriod], warnings: list[str], anchor) -> tuple[str, str]:
    if not resolved and not warnings:
        return "", ""
    lines = [f"{r.label}: from {r.start} to {r.end} (inclusive)" for r in resolved]
    notes = [r.note for r in resolved if r.note] + warnings
    header = f"(The data ends on {anchor}; all relative periods are relative to that date.)"
    return header + "\n" + "\n".join(lines), " ".join(notes)


def run_sql(question: str, link: LinkResult) -> dict:
    t0 = time.time()
    lang = link.language
    q_en = link.question_en or question
    cat = get_catalog()
    resolved, warns = dates.resolve_all(dates.normalize_periods(q_en, link.periods), cat.data_end_date)
    period_text, _ = _period_text(resolved, warns, cat.data_end_date)
    shown_notes = " ".join(t("note_no_year", lang, label=r.label) for r in resolved if r.no_year)  # translated, for the UI
    shots = examples.top_k(q_en, 5)

    schema = build(cat, link.tables, link.columns)
    meta = {"tables": schema.tables, "added_tables": schema.added_tables, "periods": [r.label for r in resolved],
            "notes": shown_notes, "attempts": [], "language": lang, "question_en": q_en}

    sql_prev, err_prev = None, None
    safe_sql, result = None, None
    for attempt in range(config.SQL_MAX_CORRECTIONS + 1):
        if attempt == 1:
            schema = build(cat, [], full=True)  # widen on first correction
        gen = generator.generate(q_en, schema.text, period_text, shots, sql_prev, err_prev)
        if gen.sql is None:
            meta["attempts"].append({"sql": None, "error": f"UNANSWERABLE: {gen.unanswerable}"})
            reason = f" {gen.unanswerable}" if (lang == "en" and gen.unanswerable) else ""  # reason is English-only
            return _finish("unsupported", None, None, meta, t0, message=t("cannot_answer", lang) + reason)
        entry = {"sql": gen.sql, "error": None}
        meta["attempts"].append(entry)
        try:
            safe_sql = validate(gen.sql, schema.allowed)
            result = execute(safe_sql)
            break
        except (SQLValidationError, SQLExecutionError) as e:
            entry["error"] = str(e)
            sql_prev, err_prev = gen.sql, str(e)
            safe_sql, result = None, None

    if result is None:
        return _finish("error", None, None, meta, t0, message=t("bad_query", lang))
    text, llm_used = answer.write_answer(question, q_en, result, period_text, lang)
    meta["answer_llm_used"] = llm_used
    return _finish("success" if result.row_count else "no_data", safe_sql, result, meta, t0, message=text)


def _finish(status, sql, result, meta, t0, message) -> dict:
    meta["elapsed_ms"] = int((time.time() - t0) * 1000)
    return {
        "route": "sql",
        "status": status,
        "answer": message,
        "sql": sql,
        "columns": result.columns if result else [],
        "data": result.rows if result else [],
        "row_count": result.row_count if result else 0,
        "truncated": result.truncated if result else False,
        "sources": [],
        "meta": meta,
    }
