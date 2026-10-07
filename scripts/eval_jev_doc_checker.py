#!/usr/bin/env python3
"""
scripts/eval_jev_doc_checker.py
Paper B, Jev experiment D (planned 2026-10-07, fixed in this docstring and committed before any Jev call; FinanceBench
is the development set, so this is DESCRIPTIVE): Jev as a checker of document answers, Part S's checker idea on the
documents route.

Answers: the fixed system's FinanceBench answers that did not decline (v3_dense and v3_bge, docs/eval/
paperb_docs_answers_v3_*.json), labelled by the registered grader (docs/eval/paperb_docs_grades_v3.json): correct = 1,
partly correct or incorrect = 0 ("refused" answers are left out: a decline needs no checker).
State: the question, the answer, and the passages the answer cites (up to 5, read from the library database by chunk
id, title and page, the first 1,500 characters each). Jev (jev-1.13), one noul question: "Is the answer correct and
supported by the passages?"
Measures: AUROC of P(yes) for correct vs not, per arm and pooled; at the untuned 0.5, the share of wrong answers
caught (P < 0.5) and of correct answers wrongly flagged; and selective accuracy when answers with P < 0.5 are
withheld.

    python3 scripts/eval_jev_doc_checker.py         writes docs/eval/jev_doc_checker.json and prints the summary
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import paperb_docs as pd  # noqa: E402
from eval_jev_pages import _FETCH  # noqa: E402
from eval_writer_window import TENANT  # noqa: E402

OUT = REPO / "docs" / "eval" / "jev_doc_checker.json"
URL = "https://opencode.ai/zen/v1/systemone"
MODEL = "jev-1.13"
INSTRUCTIONS = "Is the answer correct and supported by the passages?"
MAX_PASSAGES = 5            # noqa: literals - cited passages given to Jev
PASSAGE_CHARS = 1500        # noqa: literals - characters of each
UNTUNED = 0.5               # noqa: literals - reference threshold


def auroc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    return round(sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg)), 4)


def main() -> int:
    grades = json.loads((REPO / "docs" / "eval" / "paperb_docs_grades_v3.json").read_text())["verdicts"]
    qs = {q["financebench_id"]: q for q in pd.questions()}
    rows = []
    for arm in ("v3_dense", "v3_bge"):
        for fid, a in json.loads(pd.answers_path(arm).read_text())["answers"].items():
            verdict = (grades.get(f"{arm}|{fid}") or {}).get("verdict")
            if verdict in (None, "refused"):
                continue
            refs = [c["ref"] for c in (a.get("citations") or []) if c.get("kind") == "chunk"][:MAX_PASSAGES]
            rows.append({"arm": arm, "fid": fid, "correct": verdict == "correct", "verdict": verdict, "refs": refs,
                         "question": qs[fid]["question"], "answer": a.get("answer_text") or ""})
    ids = sorted({r for row in rows for r in row["refs"]})
    res = subprocess.run(["docker", "exec", "-i", "statesync-mcp-paperb", "python", "-c", _FETCH],
                         input=json.dumps({"ids": ids, "tenant": TENANT}), capture_output=True, text=True, timeout=600)
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    chunks = json.loads(res.stdout.strip().splitlines()[-1])
    key = (REPO / "secrets" / "opencode_api_key").read_text().strip()

    def one(row: dict) -> float:
        passages = "\n\n".join(f"[{chunks[r]['title']}, page {chunks[r]['page']}]\n{(chunks[r]['text'] or '')[:PASSAGE_CHARS]}"
                               for r in row["refs"] if r in chunks) or "(no passage cited)"
        state = f"Question: {row['question']}\n\nAnswer: {row['answer']}\n\nPassages the answer cites:\n{passages}"
        body = json.dumps({"model": MODEL, "state": state,
                           "questions": {"supported": {"type": "noul", "instructions": INSTRUCTIONS}}}).encode()
        req = urllib.request.Request(URL, data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                                              "User-Agent": "statesync-research/1.0"})
        return float(json.load(urllib.request.urlopen(req, timeout=60))["answers"]["supported"]["noul"])

    with ThreadPoolExecutor(4) as ex:
        for row, p in zip(rows, ex.map(one, rows)):
            row["p"] = p

    def stats(sel: list[dict]) -> dict:
        pos = [r["p"] for r in sel if r["correct"]]
        neg = [r["p"] for r in sel if not r["correct"]]
        kept = [r for r in sel if r["p"] >= UNTUNED]
        return {"answers": len(sel), "correct": len(pos), "auroc": auroc(pos, neg),
                "wrong_caught_at_0.5": f"{sum(p < UNTUNED for p in neg)}/{len(neg)}",
                "correct_flagged_at_0.5": f"{sum(p < UNTUNED for p in pos)}/{len(pos)}",
                "accuracy_all": round(len(pos) / len(sel), 4),
                "accuracy_of_kept_at_0.5": round(sum(r["correct"] for r in kept) / len(kept), 4) if kept else None,
                "kept_share": round(len(kept) / len(sel), 4)}

    summary = {"pooled": stats(rows), "v3_dense": stats([r for r in rows if r["arm"] == "v3_dense"]),
               "v3_bge": stats([r for r in rows if r["arm"] == "v3_bge"]), "chunks_missing": len([i for i in ids if i not in chunks])}
    OUT.write_text(json.dumps({"model": MODEL, "instructions": INSTRUCTIONS, "descriptive": True, "summary": summary,
                               "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
