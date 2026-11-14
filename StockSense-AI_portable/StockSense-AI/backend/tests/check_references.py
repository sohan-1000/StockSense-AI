"""Run every reference SQL and print row counts + top rows, so the references themselves can be sanity-checked
(non-empty where intended, no ties for 'top 1' questions).   python tests/check_references.py"""
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nl2sql.executor import execute  # noqa: E402

cases = yaml.safe_load((Path(__file__).parent / "eval_cases.yaml").read_text(encoding="utf-8"))
bad = 0
for c in cases:
    if "ref" not in c:
        continue
    try:
        r = execute(c["ref"])
    except Exception as e:  # noqa: BLE001
        print(f"{c['id']}  REFERENCE FAILED: {e}")
        bad += 1
        continue
    note = ""
    if c.get("ordered") and "LIMIT 1" in c["ref"].upper() and r.row_count:
        ties = execute(c["ref"].upper().replace("LIMIT 1", "LIMIT 2"))
        if ties.row_count == 2 and ties.rows[0][-1] == ties.rows[1][-1]:
            note = "  <-- TIE for top row"
    print(f"{c['id']}  rows={r.row_count:<3} first={r.rows[:1]}{note}")
print("reference failures:", bad)
