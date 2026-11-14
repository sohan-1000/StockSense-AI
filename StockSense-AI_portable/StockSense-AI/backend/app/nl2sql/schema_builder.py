"""Turns the tables/columns picked by the schema-linking LLM call into the exact schema text for SQL generation.

This step is deterministic code (no LLM): it validates the picks against the live catalog, adds bridge tables
needed to join the picked tables (FK graph), always adds key columns, and prints types, primary keys, foreign
keys, join conditions, sample values and descriptions for what remains.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.nl2sql.catalog import Catalog


@dataclass
class BuiltSchema:
    text: str
    allowed: dict[str, set[str]]  # table -> columns the SQL may reference (used by the validator)
    tables: list[str]
    added_tables: list[str]  # bridge tables added by join closure


def build(cat: Catalog, tables: list[str], columns: dict[str, list[str]] | None = None, full: bool = False) -> BuiltSchema:
    columns = columns or {}
    if full:
        tables = list(cat.tables)
        columns = {}
    picked = [t for t in dict.fromkeys(tables) if t in cat.tables]
    if not picked:
        picked = list(cat.tables)
        full = True

    # --- join closure: add intermediate tables so every picked table is connected -----------------------
    final = list(picked)
    added: list[str] = []
    edges_used = []
    for i, a in enumerate(picked):
        for b in picked[i + 1 :]:
            for e in cat.join_path(a, b):
                edges_used.append(e)
                for node in (e.a, e.b):
                    if node not in final:
                        final.append(node)
                        added.append(node)

    # --- columns per table ----------------------------------------------------------------------------
    key_cols: dict[str, set[str]] = {t: set(cat.tables[t].primary_key) for t in final}
    for e in edges_used:  # join columns of the connecting edges (base tables' FK/PK columns)
        for part in e.condition.split("="):
            tbl, _, col = part.strip().partition(".")
            if tbl in key_cols and col in cat.tables[tbl].columns:
                key_cols[tbl].add(col)
    for t in final:
        for fk in cat.tables[t].foreign_keys:
            if fk.ref_table in final:
                key_cols[t].add(fk.column)

    allowed: dict[str, set[str]] = {}
    for t in final:
        tbl = cat.tables[t]
        wanted = {c for c in columns.get(t, []) if c in tbl.columns}
        if full or (t in picked and not wanted):
            wanted = set(tbl.columns)  # nothing specific picked -> be generous
        wanted |= key_cols[t]
        allowed[t] = wanted

    # --- text -------------------------------------------------------------------------------------------
    lines: list[str] = []
    for t in final:
        tbl = cat.tables[t]
        lines.append(f"TABLE {t}  [{tbl.kind}]  -- {tbl.description}")
        if tbl.primary_key:
            lines.append(f"  PRIMARY KEY: ({', '.join(tbl.primary_key)})")
        for fk in tbl.foreign_keys:
            lines.append(f"  FOREIGN KEY: {t}.{fk.column} -> {fk.ref_table}.{fk.ref_column}")
        lines.append("  COLUMNS:")
        for cname, col in tbl.columns.items():
            if cname not in allowed[t]:
                continue
            extra = f"  e.g. values: {', '.join(col.sample_values)}" if col.sample_values else ""
            syn = f"  (also called: {', '.join(col.synonyms)})" if col.synonyms else ""
            lines.append(f"    {cname} {col.type.upper()} -- {col.description}{syn}{extra}")
        lines.append("")

    joins = []
    seen = set()
    for e in cat.edges:
        if e.a in final and e.b in final and e.condition not in seen:
            seen.add(e.condition)
            joins.append(f"  {e.condition}")
    if joins:
        lines.append("VALID JOINS between these tables:")
        lines.extend(joins)

    return BuiltSchema("\n".join(lines).strip(), allowed, final, added)
