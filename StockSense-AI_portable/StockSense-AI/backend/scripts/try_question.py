"""Run the NL2SQL flow for one question and print every stage.  python scripts/try_question.py "top 5 products by revenue in October" """
import json
import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nl2sql import linker, pipeline  # noqa: E402

from app.llm import sarvam  # noqa: E402

sarvam.reset_stats()
q = " ".join(sys.argv[1:])
link = linker.link(q)
print("LINK:", link.model_dump_json(indent=1, exclude_none=True))
if link.route == "sql":
    out = pipeline.run_sql(q, link)
    print("SQL:\n", out["sql"])
    print("ANSWER:", out["answer"])
    print("STATUS:", out["status"], "rows:", out["row_count"])
    print("COLUMNS:", out["columns"])
    for r in out["data"][:10]:
        print("  ", r)
    print("META:", json.dumps(out["meta"], default=str)[:1500])

print("LLM CALLS:", sarvam.call_stats())
