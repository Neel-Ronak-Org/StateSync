#!/usr/bin/env python3
"""
scripts/eval_jev_answerable.py
Paper B, Jev experiment C (planned 2026-10-07, fixed in this docstring and committed before any Jev call): Jev as a
"not answerable" gate for the SQL route, the job F-S1's NOT_ANSWERABLE reply failed (2/150 false refusals, bar 1%).

Pairs (seed 20261007):
  - answerable: gate G1's 150 held-out BIRD questions (as asked: question and hint), each with its OWN database's
    schema card;
  - unanswerable: the same 150 questions, each with a DIFFERENT BIRD database's schema card, drawn at random
    (seeded). These negatives are easier than real ones (another domain, and the hint often names columns the
    card lacks); the paper says so.
Schema card: the database's tables and their columns (name and type), from the BIRD PostgreSQL copy, in
table order, cut at 6,000 characters (whole tables).
Jev (jev-1.13, OpenCode Zen), one noul question: "Does the database described contain the data needed to answer
the question?" over the state "Question: ...\n\nDatabase <name>:\n<card>".
Split: the 150 questions are split at random (seeded) into two halves of 75, keeping each question's two pairs
together. On the FIT half the threshold t is the LARGEST value with at most 1% of answerable pairs below it (false
refusals; a pair is refused when P < t); it is applied unchanged to the TEST half.
Measures (test half): false refusal rate on answerable pairs (bar: at most 1%, as G1), share of unanswerable pairs
refused, AUROC over all 300 pairs; the same at the untuned t = 0.5 for reference.

    python3 scripts/eval_jev_answerable.py          writes docs/eval/jev_answerable.json and prints the summary
"""

from __future__ import annotations

import json
import random
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import eval_bird as eb  # noqa: E402

SEED = 20261007
OUT = REPO / "docs" / "eval" / "jev_answerable.json"
SOURCE = REPO / "docs" / "eval" / "bird_answers_partd_v3_off_heldout150.json"   # G1's 150, as asked
URL = "https://opencode.ai/zen/v1/systemone"
MODEL = "jev-1.13"
INSTRUCTIONS = "Does the database described contain the data needed to answer the question?"
CARD_CHARS = 6000           # noqa: literals - the schema card's cap
FALSE_REFUSAL_BAR = 0.01    # noqa: literals - G1's bar
UNTUNED = 0.5               # noqa: literals - reference threshold


def cards(db_ids: list[str]) -> dict[str, str]:
    conn = eb.bird_connection()
    out = {}
    for db in db_ids:
        rows = conn.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                            "WHERE table_schema = %s ORDER BY table_name, ordinal_position", (db,)).fetchall()
        tables: dict[str, list[str]] = {}
        for t, c, ty in rows:
            tables.setdefault(t, []).append(f"{c} {ty}")
        text = ""
        for t, cols in tables.items():
            block = f"TABLE {t}: " + ", ".join(cols) + "\n"
            if len(text) + len(block) > CARD_CHARS:
                break
            text += block
        out[db] = text
    return out


def auroc(pos: list[float], neg: list[float]) -> float:
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def main() -> int:
    answers = json.loads(SOURCE.read_text())["answers"]
    qs = sorted(answers.items(), key=lambda kv: int(kv[0]))
    db_ids = sorted({a["db_id"] for _, a in qs})
    card = cards(db_ids)
    rng = random.Random(SEED)
    pairs = []
    for qid, a in qs:
        other = rng.choice([d for d in db_ids if d != a["db_id"]])
        pairs.append({"qid": qid, "db": a["db_id"], "answerable": True, "question": a["question_asked"]})
        pairs.append({"qid": qid, "db": other, "answerable": False, "question": a["question_asked"]})
    ids = [qid for qid, _ in qs]
    rng.shuffle(ids)
    fit = set(ids[: len(ids) // 2])
    key = (REPO / "secrets" / "opencode_api_key").read_text().strip()

    def one(p: dict) -> float:
        state = f"Question: {p['question']}\n\nDatabase {p['db']}:\n{card[p['db']]}"
        body = json.dumps({"model": MODEL, "state": state,
                           "questions": {"has_data": {"type": "noul", "instructions": INSTRUCTIONS}}}).encode()
        req = urllib.request.Request(URL, data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                                              "User-Agent": "statesync-research/1.0"})
        return float(json.load(urllib.request.urlopen(req, timeout=60))["answers"]["has_data"]["noul"])

    with ThreadPoolExecutor(4) as ex:
        for p, s in zip(pairs, ex.map(one, pairs)):
            p["p"] = s
            p["half"] = "fit" if p["qid"] in fit else "test"

    fit_pos = sorted(p["p"] for p in pairs if p["half"] == "fit" and p["answerable"])
    allowed = int(FALSE_REFUSAL_BAR * len(fit_pos))           # refusals allowed on the fit half
    t = fit_pos[allowed]                                        # refuse when P < t: at most `allowed` below it

    def rates(half: str, thr: float) -> dict:
        pos = [p for p in pairs if p["half"] == half and p["answerable"]]
        neg = [p for p in pairs if p["half"] == half and not p["answerable"]]
        return {"threshold": thr, "false_refusals": f"{sum(p['p'] < thr for p in pos)}/{len(pos)}",
                "false_refusal_rate": round(sum(p["p"] < thr for p in pos) / len(pos), 4),
                "unanswerable_refused": f"{sum(p['p'] < thr for p in neg)}/{len(neg)}",
                "unanswerable_refused_rate": round(sum(p["p"] < thr for p in neg) / len(neg), 4)}

    summary = {"auroc_all": round(auroc([p["p"] for p in pairs if p["answerable"]], [p["p"] for p in pairs if not p["answerable"]]), 4),
               "fitted_on_fit_half": rates("fit", t), "test_half_at_fitted": rates("test", t),
               "test_half_at_0.5": rates("test", UNTUNED), "meets_bar_on_test": None}
    summary["meets_bar_on_test"] = summary["test_half_at_fitted"]["false_refusal_rate"] <= FALSE_REFUSAL_BAR
    OUT.write_text(json.dumps({"seed": SEED, "model": MODEL, "instructions": INSTRUCTIONS, "summary": summary, "pairs": pairs},
                              indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
