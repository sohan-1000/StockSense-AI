"""Deterministic date resolution. LLMs are unreliable with relative dates, so the schema-linking call only
returns a *structured period* and this module turns it into exact dates.

All relative expressions are anchored to the LAST DAY OF DATA (not today's calendar date), because the
dataset ends on a fixed date; "last month" must mean the month before the data ends.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass

from pydantic import BaseModel


class Period(BaseModel):
    type: str  # month | year | quarter | relative | last_n_days | range
    month: int | None = None
    year: int | None = None
    quarter: int | None = None
    unit: str | None = None  # day | week | month | quarter | year   (for type=relative)
    offset: int | None = None  # 0 = current, -1 = previous ...
    n: int | None = None
    start: str | None = None
    end: str | None = None


@dataclass
class ResolvedPeriod:
    start: dt.date
    end: dt.date
    label: str
    note: str = ""  # English, goes into the SQL prompt
    no_year: bool = False  # language-neutral flag: the year was inferred (the UI shows a translated note)


def _month_end(y: int, m: int) -> dt.date:
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def resolve(p: Period, anchor: dt.date, data_start: dt.date = dt.date(2025, 1, 1)) -> ResolvedPeriod:
    t = p.type
    if t == "month":
        if not p.month or not 1 <= p.month <= 12:
            raise ValueError("invalid month")
        note = ""
        year = p.year
        if year is None:  # latest occurrence of that month that is not after the anchor
            year = anchor.year if dt.date(anchor.year, p.month, 1) <= anchor else anchor.year - 1
            note = f"No year given, so the most recent {calendar.month_name[p.month]} in the data ({year}) was used."
        start, end = dt.date(year, p.month, 1), _month_end(year, p.month)
        return ResolvedPeriod(start, min(end, anchor) if start <= anchor <= end else end, f"{calendar.month_name[p.month]} {year}", note, bool(note))
    if t == "year":
        y = p.year or anchor.year
        return ResolvedPeriod(dt.date(y, 1, 1), dt.date(y, 12, 31), str(y))
    if t == "quarter":
        if not p.quarter or not 1 <= p.quarter <= 4:
            raise ValueError("invalid quarter")
        y, note = p.year, ""
        if y is None:
            y = anchor.year if dt.date(anchor.year, 3 * p.quarter - 2, 1) <= anchor else anchor.year - 1
            note = f"No year given, so Q{p.quarter} {y} was used."
        sm = 3 * p.quarter - 2
        return ResolvedPeriod(dt.date(y, sm, 1), _month_end(y, sm + 2), f"Q{p.quarter} {y}", note, bool(note))
    if t == "last_n_months":
        n = max(1, p.n or 1)
        y, m = _shift_month(anchor.year, anchor.month, -(n - 1))
        return ResolvedPeriod(dt.date(y, m, 1), anchor, f"last {n} months (to {anchor})")
    if t == "last_n_days":
        n = max(1, p.n or 30)
        return ResolvedPeriod(anchor - dt.timedelta(days=n - 1), anchor, f"last {n} days (ending {anchor})")
    if t == "relative":
        off = p.offset or 0
        unit = p.unit or "month"
        if unit == "day":
            d = anchor + dt.timedelta(days=off)
            return ResolvedPeriod(d, d, str(d))
        if unit == "week":  # ISO weeks (Mon-Sun); current week is cut at the anchor
            monday = anchor - dt.timedelta(days=anchor.weekday()) + dt.timedelta(weeks=off)
            end = monday + dt.timedelta(days=6)
            return ResolvedPeriod(monday, min(end, anchor), f"week of {monday}")
        if unit == "month":
            y, m = _shift_month(anchor.year, anchor.month, off)
            end = _month_end(y, m)
            return ResolvedPeriod(dt.date(y, m, 1), min(end, anchor), f"{calendar.month_name[m]} {y}")
        if unit == "quarter":
            q0 = (anchor.month - 1) // 3
            y, m = _shift_month(anchor.year, q0 * 3 + 1, 3 * off)
            return ResolvedPeriod(dt.date(y, m, 1), min(_month_end(y, m + 2), anchor), f"Q{(m - 1) // 3 + 1} {y}")
        if unit == "year":
            y = anchor.year + off
            return ResolvedPeriod(dt.date(y, 1, 1), min(dt.date(y, 12, 31), anchor), str(y))
        raise ValueError(f"unknown unit {unit}")
    if t == "range":
        s, e = dt.date.fromisoformat(p.start or ""), dt.date.fromisoformat(p.end or "")
        if s > e:
            s, e = e, s
        return ResolvedPeriod(s, e, f"{s} to {e}")
    raise ValueError(f"unknown period type {t}")


def resolve_all(periods: list[Period], anchor: dt.date) -> tuple[list[ResolvedPeriod], list[str]]:
    """Resolve each period; invalid ones are skipped and reported as warnings (never crash the pipeline)."""
    ok, warnings = [], []
    for p in periods:
        try:
            ok.append(resolve(p, anchor))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Could not interpret period {p.model_dump(exclude_none=True)}: {e}")
    return ok, warnings


# ---------------------------------------------------------------------------------------------------------
# Relative phrases ("last month", "yesterday", ...) are resolved by rules, never trusted to the LLM: without
# thinking and without today's date the model guesses calendar months. Named months/years must be justified
# by the question text, otherwise they are dropped as hallucinations.
_REL = [
    (r"\b(?:last|previous|prior)\s+month\b", dict(type="relative", unit="month", offset=-1)),
    (r"\b(?:this|current)\s+month\b", dict(type="relative", unit="month", offset=0)),
    (r"\b(?:last|previous|prior)\s+week\b", dict(type="relative", unit="week", offset=-1)),
    (r"\b(?:this|current)\s+week\b", dict(type="relative", unit="week", offset=0)),
    (r"\byesterday\b", dict(type="relative", unit="day", offset=-1)),
    (r"\btoday\b", dict(type="relative", unit="day", offset=0)),
    (r"\b(?:last|previous|prior)\s+year\b", dict(type="relative", unit="year", offset=-1)),
    (r"\b(?:this|current)\s+year\b", dict(type="relative", unit="year", offset=0)),
    (r"\b(?:last|previous|prior)\s+quarter\b", dict(type="relative", unit="quarter", offset=-1)),
    (r"\b(?:this|current)\s+quarter\b", dict(type="relative", unit="quarter", offset=0)),
]
_MONTHS = ("jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|"
           "august|september|october|november|december")


def _detect_relative(q: str) -> list[Period]:
    out = [Period(**d) for pat, d in _REL if re.search(pat, q, re.I)]
    for m in re.finditer(r"\b(?:last|past|previous)\s+(\d+)\s+(day|week|month)s?\b", q, re.I):
        n, unit = int(m.group(1)), m.group(2).lower()
        out.append(Period(type="last_n_months", n=n) if unit == "month"
                   else Period(type="last_n_days", n=n * (7 if unit == "week" else 1)))
    return out


def normalize_periods(question: str, llm_periods: list[Period]) -> list[Period]:
    rel = _detect_relative(question)
    has_month = bool(re.search(rf"\b(?:{_MONTHS})\b", question, re.I))
    has_year = bool(re.search(r"\b20\d\d\b", question))
    has_quarter = bool(re.search(r"\bq[1-4]\b|\bquarter\b", question, re.I))
    has_digits = bool(re.search(r"\d", question))
    kept = []
    for p in llm_periods:
        if p.type in ("relative", "last_n_days", "last_n_months"):
            if not rel:  # no rule matched: trust the LLM's relative expression (e.g. "past fortnight")
                kept.append(p)
        elif p.type == "month" and has_month:
            kept.append(p)
        elif p.type == "year" and has_year:
            kept.append(p)
        elif p.type == "quarter" and has_quarter:
            kept.append(p)
        elif p.type == "range" and has_digits:
            kept.append(p)
    return rel + kept
