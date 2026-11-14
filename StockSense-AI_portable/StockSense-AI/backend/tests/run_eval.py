"""NL2SQL evaluation against hand-written reference SQL.

   python tests/run_eval.py                 # all cases
   python tests/run_eval.py S01 I01 X02     # selected cases
   python tests/run_eval.py --workers 4     # parallelism (default 4)

Metrics
  routing accuracy      : route chosen == expected (sql / unsupported / rag)
  execution accuracy    : generated SQL validated + executed without error (SQL cases)
  result accuracy       : generated result == reference result (column names/extra columns/row order ignored
                          unless 'ordered'; numbers compared at 2/1/0 decimals)
  first-attempt rate    : correct on the first generation with the pruned schema (proxy for schema-linking quality)
  grounded-answer rate  : final text was LLM-written AND passed the number-grounding check (vs template fallback)
"""
import argparse
import itertools
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chat  # noqa: E402
from app.i18n import has_script  # noqa: E402
from app.llm import sarvam  # noqa: E402
from app.nl2sql import linker, pipeline  # noqa: E402
from app.nl2sql.executor import execute  # noqa: E402

HERE = Path(__file__).parent


def _key(v, nd):
    if v is None:
        return ("N",)
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return ("n", round(float(v), nd))
    if isinstance(v, (date, datetime)):
        return ("s", v.isoformat()[:10])
    return ("s", str(v).strip()[:10] if len(str(v)) >= 10 and str(v)[4:5] == "-" else str(v).strip().lower())


def _is_num(v) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if _is_num(a) and _is_num(b):
        fa, fb = float(a), float(b)
        return abs(fa - fb) <= max(0.051, 0.001 * max(abs(fa), abs(fb)))  # tolerates ROUND(..,1) vs ROUND(..,2)
    return _key(a, 2) == _key(b, 2)


def results_match(gen_rows, ref_rows, ordered) -> bool:
    """Same rows as the reference, ignoring column names, extra generated columns and (unless ordered) row order.
    Numbers match within a small tolerance; row counts must be equal."""
    if not ref_rows:
        return not gen_rows
    if not gen_rows or len(gen_rows) != len(ref_rows):
        return False
    ncol, gcols = len(ref_rows[0]), len(gen_rows[0])
    for combo in itertools.islice(itertools.permutations(range(gcols), ncol), 3000):
        proj = [tuple(r[i] for i in combo) for r in gen_rows]
        if ordered:
            ok = all(all(_close(a, b) for a, b in zip(g, r)) for g, r in zip(proj, ref_rows))
        else:
            used, ok = [False] * len(proj), True
            for r in ref_rows:
                for j, g in enumerate(proj):
                    if not used[j] and all(_close(a, b) for a, b in zip(g, r)):
                        used[j] = True
                        break
                else:
                    ok = False
                    break
        if ok:
            return True
    return False


def _results_match_rounded(gen_rows, ref_rows, ordered) -> bool:  # superseded; kept for reference
    if not ref_rows:
        return not gen_rows
    if not gen_rows:
        return False
    ncol = len(ref_rows[0])
    gcols = list(zip(*gen_rows))
    rcols = list(zip(*ref_rows))
    for nd in (2, 1, 0):
        cand = []
        for rc in rcols:
            rk = sorted(_key(v, nd) for v in rc)
            cand.append([i for i, gc in enumerate(gcols) if sorted(_key(v, nd) for v in gc) == rk])
        if any(not c for c in cand):
            continue
        for combo in itertools.islice(itertools.product(*cand), 50):
            if len(set(combo)) < ncol:
                continue
            g = [tuple(_key(r[i], nd) for i in combo) for r in gen_rows]
            r_ = [tuple(_key(v, nd) for v in r) for r in ref_rows]
            if (g == r_) if ordered else (sorted(g) == sorted(r_)):
                return True
    return False


def run_case_full(c):
    """Multilingual cases go through the real chat entry point, so reply language and refusals are tested too."""
    sarvam.reset_stats()
    t0 = time.time()
    out = {"id": c["id"], "cat": c["cat"], "q": c["q"], "expect": c["expect"], "lang": c["lang"]}
    res = chat.answer_question(c["q"])
    out.update(route=res["route"], status=res["status"], sql=res["sql"], row_count=res["row_count"], answer=res["answer"],
               question_en=res["meta"].get("question_en"), notes=res["meta"].get("notes", ""))
    out["route_ok"] = res["route"] == c["expect"] or (c["expect"] == "unsupported" and res["status"] == "unsupported")
    out["lang_ok"] = has_script(res["answer"], c["lang"])
    if c["expect"] == "sql":
        attempts = res["meta"].get("attempts", [])
        out["attempts"] = len(attempts)
        out["attempt_log"] = attempts
        out["executed"] = res["status"] in ("success", "no_data")
        if out["executed"]:
            ref = execute(c["ref"])
            out["correct"] = results_match(res["data"], ref.rows, bool(c.get("ordered")))
            out["first_attempt"] = bool(out["correct"] and len(attempts) == 1)
            out["answer_llm"] = res["meta"].get("answer_llm_used")
            if not out["correct"]:
                out["got_first_rows"] = res["data"][:3]
                out["ref_first_rows"] = ref.rows[:3]
    out["seconds"] = round(time.time() - t0, 1)
    out["llm_calls"] = sarvam.call_stats()
    return out


def run_case(c):
    if c.get("lang"):
        return run_case_full(c)
    sarvam.reset_stats()
    t0 = time.time()
    out = {"id": c["id"], "cat": c["cat"], "q": c["q"], "expect": c["expect"]}
    try:
        link = linker.link(c["q"])
    except Exception as e:  # noqa: BLE001
        out.update(error=f"link failed: {e}", route=None)
        return out
    out["route"] = link.route
    out["link_tables"] = link.tables
    out["route_ok"] = link.route == c["expect"]
    if link.route == "sql":
        res = pipeline.run_sql(c["q"], link)
        out.update(status=res["status"], sql=res["sql"], row_count=res["row_count"], answer=res["answer"],
                   attempts=len(res["meta"]["attempts"]), notes=res["meta"].get("notes", ""),
                   attempt_log=res["meta"]["attempts"], answer_llm=res["meta"].get("answer_llm_used"))
        if c["expect"] == "unsupported" and res["status"] == "unsupported":
            out["route_ok"] = True  # linker said sql, generator correctly declined
        if c["expect"] == "sql":
            out["executed"] = res["status"] in ("success", "no_data")
            if out["executed"]:
                ref = execute(c["ref"])
                out["correct"] = results_match(res["data"], ref.rows, bool(c.get("ordered")))
                out["first_attempt"] = bool(out["correct"] and out["attempts"] == 1)
                if not out["correct"]:
                    out["got_first_rows"] = res["data"][:3]
                    out["ref_first_rows"] = ref.rows[:3]
    out["seconds"] = round(time.time() - t0, 1)
    out["llm_calls"] = sarvam.call_stats()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--file", default="eval_cases.yaml")
    a = ap.parse_args()
    cases = yaml.safe_load((HERE / a.file).read_text(encoding="utf-8"))
    stem = Path(a.file).stem.replace("eval_cases", "eval")
    base = {b["id"]: b for b in yaml.safe_load((HERE / "eval_cases.yaml").read_text(encoding="utf-8"))}
    for c in cases:
        if "like" in c:
            c.setdefault("ref", base[c["like"]].get("ref"))
            c.setdefault("ordered", base[c["like"]].get("ordered"))
    if a.ids:
        cases = [c for c in cases if c["id"] in a.ids]
    from app.warmup import warm

    warm()
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        results = list(ex.map(run_case, cases))
    wall = time.time() - t0

    sqlc = [r for r in results if r["expect"] == "sql"]
    n = len(results)
    routing = sum(bool(r.get("route_ok")) for r in results)
    executed = sum(bool(r.get("executed")) for r in sqlc)
    correct = sum(bool(r.get("correct")) for r in sqlc)
    first = sum(bool(r.get("first_attempt")) for r in sqlc)
    llm_ans = [r for r in sqlc if r.get("executed") and r.get("row_count")]
    grounded = sum(bool(r.get("answer_llm")) for r in llm_ans)
    lines = [
        f"# NL2SQL evaluation [{a.file}]  ({datetime.now():%Y-%m-%d %H:%M})",
        f"- cases: {n} ({len(sqlc)} SQL, {n - len(sqlc)} routing/unsupported) | wall time {wall:.0f}s | avg {sum(r.get('seconds', 0) for r in results) / n:.1f}s per case",
        f"- routing accuracy:      {routing}/{n} = {routing / n:.0%}",
        f"- execution accuracy:    {executed}/{len(sqlc)} = {executed / max(len(sqlc), 1):.0%}",
        f"- result accuracy:       {correct}/{len(sqlc)} = {correct / max(len(sqlc), 1):.0%}",
        f"- first-attempt correct: {first}/{len(sqlc)} = {first / max(len(sqlc), 1):.0%}  (schema-linking proxy)",
        f"- LLM-written answers that passed the number-grounding check: {grounded}/{len(llm_ans)}",
        *([f"- reply written in the requested language: {sum(bool(r.get('lang_ok')) for r in results if r.get('lang'))}/{sum(1 for r in results if r.get('lang'))}"]
          if any(r.get("lang") for r in results) else []),
        "",
        "## By category (result accuracy for SQL cases / routing for others)",
    ]
    for cat in dict.fromkeys(r["cat"] for r in results):
        rs = [r for r in results if r["cat"] == cat]
        ok = sum(bool(r.get("correct")) if r["expect"] == "sql" else bool(r.get("route_ok")) for r in rs)
        lines.append(f"- {cat}: {ok}/{len(rs)}")
    lines += ["", "## Failures"]
    for r in results:
        bad = (r["expect"] == "sql" and not r.get("correct")) or (r["expect"] != "sql" and not r.get("route_ok")) or r.get("error") or (r.get("lang") and not r.get("lang_ok"))
        if bad:
            lines.append(f"### {r['id']} [{r['cat']}] {r['q']}")
            lines.append(f"- expected `{r['expect']}`, route `{r.get('route')}`, status `{r.get('status')}`, attempts {r.get('attempts')}")
            if r.get("error"):
                lines.append(f"- error: {r['error']}")
            if r.get("lang"):
                lines.append(f"- language {r['lang']}, reply in that script: {r.get('lang_ok')}, English translation: {r.get('question_en')!r}")
                lines.append(f"- reply: {str(r.get('answer'))[:200]!r}")
            if r.get("sql"):
                lines.append(f"- sql: `{' '.join(r['sql'].split())[:400]}`")
            if r.get("got_first_rows") is not None:
                lines.append(f"- got: {r['got_first_rows']}  | reference: {r['ref_first_rows']}")
            errs = [x["error"] for x in r.get("attempt_log", []) if x.get("error")]
            if errs:
                lines.append(f"- attempt errors: {errs}")
    report = "\n".join(lines)
    (HERE / f"{stem}_report.md").write_text(report, encoding="utf-8")
    (HERE / f"{stem}_results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
