"""Re-run and re-label saved candidates with the current data.label (no LLM calls)."""
import json, sys
from data import DB, OUT, label

db = DB()
items = {(i["qid"], i["question"]): i for part in json.loads((OUT / "items.json").read_text()).values() for i in part}
for path in sys.argv[1:]:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    for r in rows:
        if r.get("runs", True):
            lab = label(db, items[(r["qid"], r["question"])], r["sql"])
            if lab:
                r.update({k: lab[k] for k in ("result", "correct", "correct_strict", "rows")})
    with open(path, "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    ok = [r for r in rows if r.get("runs", True)]
    print(path, len(ok), "correct", sum(r["correct"] for r in ok), "strict", sum(r.get("correct_strict", False) for r in ok))
