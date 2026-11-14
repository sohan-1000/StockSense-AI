"""Schema catalog = semantic_layer.yaml (business meaning) + live DB introspection (types, PK, FK).

Provides:
  * brief_text()      - compact table/column list for the schema-linking LLM call
  * join_path()       - FK/relationship graph search (used to add bridge tables)
  * Catalog.tables    - full metadata used by the schema builder and the SQL validator allowlist
"""
from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.db.connection import owner_conn

YAML_PATH = Path(__file__).parent / "semantic_layer.yaml"


@dataclass
class Column:
    name: str
    type: str
    description: str
    synonyms: list[str] = field(default_factory=list)
    sample_values: list[str] = field(default_factory=list)


@dataclass
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    kind: str  # "table" | "view"
    description: str
    columns: dict[str, Column]
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)


@dataclass
class Edge:
    a: str
    b: str
    condition: str


@dataclass
class Catalog:
    tables: dict[str, Table]
    rules: list[str]
    edges: list[Edge]
    data_end_date: dt.date

    # ---- text for the LLM -------------------------------------------------
    def brief_text(self) -> str:
        """Compact catalog for call 1 (schema linking): tables, purpose, column names with short meaning."""
        out = []
        for t in self.tables.values():
            out.append(f"- {t.name} ({t.kind}): {t.description}")
            for c in t.columns.values():
                out.append(f"    * {c.name}: {c.description}")
        return "\n".join(out)

    # ---- join graph -------------------------------------------------------
    def join_path(self, src: str, dst: str) -> list[Edge]:
        """Shortest chain of edges between two tables (BFS), [] if none / same table."""
        if src == dst:
            return []
        adj: dict[str, list[tuple[str, Edge]]] = {}
        for e in self.edges:
            adj.setdefault(e.a, []).append((e.b, e))
            adj.setdefault(e.b, []).append((e.a, e))
        seen, q = {src}, deque([(src, [])])
        while q:
            node, path = q.popleft()
            for nxt, e in adj.get(node, []):
                if nxt in seen:
                    continue
                if nxt == dst:
                    return path + [e]
                seen.add(nxt)
                q.append((nxt, path + [e]))
        return []

    def all_columns(self) -> dict[str, set[str]]:
        return {t.name: set(t.columns) for t in self.tables.values()}


def _introspect(cur, names: list[str]):
    cur.execute(
        """SELECT table_name, column_name, data_type FROM information_schema.columns
           WHERE table_schema='public' AND table_name = ANY(%s) ORDER BY table_name, ordinal_position""",
        (names,),
    )
    cols: dict[str, dict[str, str]] = {}
    for t, c, d in cur.fetchall():
        cols.setdefault(t, {})[c] = d

    cur.execute(
        """SELECT tc.table_name, kcu.column_name
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
           WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public' AND tc.table_name = ANY(%s)
           ORDER BY tc.table_name, kcu.ordinal_position""",
        (names,),
    )
    pks: dict[str, list[str]] = {}
    for t, c in cur.fetchall():
        pks.setdefault(t, []).append(c)

    cur.execute(
        """SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
           JOIN information_schema.constraint_column_usage ccu
             ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
           WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public' AND tc.table_name = ANY(%s)""",
        (names,),
    )
    fks: dict[str, list[ForeignKey]] = {}
    for t, c, rt, rc in cur.fetchall():
        fks.setdefault(t, []).append(ForeignKey(c, rt, rc))
    return cols, pks, fks


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    raw = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    names = list(raw["tables"])
    with owner_conn() as conn:
        cur = conn.cursor()
        db_cols, pks, fks = _introspect(cur, names)
        tables: dict[str, Table] = {}
        edges: list[Edge] = []
        for name, spec in raw["tables"].items():
            ycols = spec["columns"]
            if set(ycols) != set(db_cols.get(name, {})):
                diff = set(ycols) ^ set(db_cols.get(name, {}))
                raise RuntimeError(f"semantic_layer.yaml and database disagree for '{name}': {sorted(diff)}")
            columns = {}
            for cname, cdef in ycols.items():
                samples: list[str] = []
                if cdef.get("sample_values") == "auto":
                    cur.execute(f'SELECT DISTINCT "{cname}"::text FROM "{name}" WHERE "{cname}" IS NOT NULL ORDER BY 1 LIMIT 40')
                    samples = [r[0] for r in cur.fetchall()]
                columns[cname] = Column(cname, db_cols[name][cname], cdef["description"], cdef.get("synonyms", []), samples)
            tables[name] = Table(name, spec["kind"], spec["description"], columns, pks.get(name, []), fks.get(name, []))
            for fk in fks.get(name, []):
                edges.append(Edge(name, fk.ref_table, f"{name}.{fk.column} = {fk.ref_table}.{fk.ref_column}"))
            for rel in spec.get("relationships", []) or []:
                edges.append(Edge(name, rel["to"], rel["join"]))
        cur.execute("SELECT max(date) FROM sales")
        end = cur.fetchone()[0]
    return Catalog(tables, raw["business_rules"], edges, end)
