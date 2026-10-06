#!/usr/bin/env python3
"""
scripts/eval_context_carry.py
T4d's offline check (docs/specs/partd_tasks/T4d_context_carry.md, F3) on the routing set's conversation turns, with
the product's own pure functions (orchestrator/context_carry.py) and names from the library's document titles, as
the front door builds them:
  - follow-up detection vs the gold depends_on_history;
  - for gold follow-ups, the company and year carry() resolves vs those the gold standalone question names.
The previous turn is given as the user typed it, with its gold answer (what the product stores as the answer).

    python3 scripts/eval_context_carry.py        prints the summary and writes docs/eval/context_carry_offline.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "orchestrator"))
import context_carry as cc  # noqa: E402
from nodes.unstructured_knowledge import title_words  # noqa: E402

SET = REPO / "docs" / "eval" / "routing_set.json"
OUT = REPO / "docs" / "eval" / "context_carry_offline.json"


def main() -> int:
    ingest = json.loads((REPO / "docs" / "eval" / "financebench_ingest_paragraph.json").read_text())
    names = list(dict.fromkeys(n for n in (title_words(str(d.get("doc_name") or "").replace("_", " "))
                                           for d in ingest["documents"] if d.get("status") == "ready") if n))
    rs = json.loads(SET.read_text())
    rows = []
    for conv in rs["conversations"]:
        for t in conv["turns"]:
            if t["turn"] == 1:
                continue
            prev = next(p for p in conv["turns"] if p["turn"] == t["turn"] - 1)
            previous = {"question": prev["question"], "answer": str(prev.get("gold_answer") or "")}
            earlier = [{"question": p["question"], "answer": str(p.get("gold_answer") or "")}
                       for p in conv["turns"] if p["turn"] < t["turn"] - 1]
            c = cc.carry(t["question"], previous, names, earlier=earlier)
            gold_companies = cc.entities(t["standalone"], names)
            gold_years = cc.years(t["standalone"])
            norm = lambda x: "".join(ch for ch in (x or "").lower() if ch.isalnum())
            rows.append({"turn": f"{conv['id']}|{t['turn']}", "split": conv["split"], "question": t["question"],
                         "gold_follow_up": t["depends_on_history"], "follow_up": c.follow_up, "reason": c.reason,
                         "company": c.company, "gold_companies": gold_companies, "year": c.year, "gold_years": gold_years,
                         "company_ok": (not gold_companies and not c.company) or norm(c.company) in {norm(g) for g in gold_companies},
                         "year_ok": (not gold_years and not c.year) or (c.year in gold_years)})
    n = len(rows)
    fu = [r for r in rows if r["gold_follow_up"]]
    summary = {"turns": n, "detection_accuracy": round(sum(r["follow_up"] == r["gold_follow_up"] for r in rows) / n, 3),
               "follow_ups": len(fu), "detected": sum(r["follow_up"] for r in fu),
               "false_alarms": sum(r["follow_up"] and not r["gold_follow_up"] for r in rows),
               "company_right_on_follow_ups": f"{sum(r['company_ok'] for r in fu)}/{len(fu)}",
               "year_right_on_follow_ups": f"{sum(r['year_ok'] for r in fu)}/{len(fu)}",
               "both_right_on_follow_ups": f"{sum(r['company_ok'] and r['year_ok'] for r in fu)}/{len(fu)}"}
    OUT.write_text(json.dumps({"names": len(names), "summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary))
    for r in fu:
        if not (r["company_ok"] and r["year_ok"] and r["follow_up"]):
            print("  miss", r["turn"], "|", r["question"][:60], "| got", r["company"], r["year"], "| gold", r["gold_companies"], r["gold_years"], "|", r["reason"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
