"""Final answer for SQL results. The LLM only *explains* the rows; numbers must come from the rows.

A post-check verifies that every number in the explanation exists in the result (or the question/dates).
If not, a deterministic template answer is used instead - the LLM can never inject an invented value.
"""
from __future__ import annotations

import re

from app.i18n import LANG_NAME, has_script, t
from app.llm import sarvam
from app.nl2sql.executor import ExecResult

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")

SYSTEM = """You explain database query results to a busy shopkeeper in simple English.
- Use ONLY the numbers and names in RESULT. Never calculate new numbers (no totals, differences, percentages) unless they are columns in RESULT.
- Write 1-3 short sentences. Write money as rupees with the ₹ symbol and the exact figure (no k/lakh/million abbreviations).
- Mention the time period if one is given. If the result is a table with many rows, summarise the key point and say the table has the details.
- Do not mention SQL, columns, or databases. No markdown tables, no preamble.
- Write the explanation in <<LANG>>. Keep product, supplier and festival names exactly as they appear in RESULT (do not translate them).
  Write all numbers with digits 0-9 (never Telugu or Devanagari numerals) and keep the ₹ symbol."""


def _numbers_in(value) -> set[float]:
    out: set[float] = set()
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out.add(float(value))
        for d in (0, 1, 2):
            out.add(round(float(value), d))
    else:
        for m in _NUM.findall(str(value)):
            try:
                out.add(float(m.replace(",", "")))
            except ValueError:
                pass
    return out


def _allowed_numbers(result: ExecResult, extra_text: str) -> set[float]:
    allowed = {float(result.row_count)}
    for row in result.rows:
        for v in row:
            allowed |= _numbers_in(v)
    for m in _NUM.findall(extra_text):
        try:
            allowed.add(float(m.replace(",", "")))
        except ValueError:
            pass
    return allowed


def numbers_grounded(answer: str, result: ExecResult, extra_text: str) -> bool:
    allowed = _allowed_numbers(result, extra_text)
    for m in _NUM.findall(answer):
        n = float(m.replace(",", "").rstrip("."))
        if n in allowed or n <= result.row_count:
            continue
        if any(abs(n - a) <= max(0.011, abs(a) * 0.0005) for a in allowed):
            continue
        return False
    return True


def _fmt(v) -> str:
    return f"{v:,}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def template_answer(result: ExecResult, lang: str = "en") -> str:
    """Deterministic fallback (localised); used when there is nothing to explain or the LLM text is not grounded."""
    if result.row_count == 0:
        return t("no_records", lang)
    if result.row_count == 1 and len(result.columns) <= 4:
        return t("result", lang) + ": " + ", ".join(f"{c.replace('_', ' ')} = {_fmt(v)}" for c, v in zip(result.columns, result.rows[0])) + "."
    return t("found_n", lang, n=result.row_count, more=t("more_rows", lang) if result.truncated else "")


def write_answer(question: str, question_en: str, result: ExecResult, period_text: str, lang: str = "en") -> tuple[str, bool]:
    """Return (answer, llm_used). The answer is written in `lang`. Falls back to the localised template on empty
    results, LLM errors, ungrounded numbers, or (for non-English) a reply that is not in the requested script."""
    if result.row_count == 0:
        return template_answer(result, lang), False
    shown = result.rows[:40]
    table = "\n".join([" | ".join(result.columns)] + [" | ".join(_fmt(v) for v in r) for r in shown])
    user = (f"QUESTION: {question}\n(English: {question_en})\nTIME PERIOD: {period_text or 'not specified'}\n"
            f"RESULT ({result.row_count} rows{', showing first 40' if result.row_count > 40 else ''}):\n{table}")
    system = SYSTEM.replace("<<LANG>>", LANG_NAME.get(lang, "English"))
    try:
        text = sarvam.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], label="answer")
    except sarvam.LLMError:
        return template_answer(result, lang), False
    if not numbers_grounded(text, result, f"{question} {question_en} {period_text}"):
        return template_answer(result, lang), False
    if lang != "en" and not has_script(text, lang, min_chars=4):  # model answered in the wrong language
        return template_answer(result, lang), False
    return text.strip(), True
