#!/usr/bin/env python3
"""
scripts/make_r1_author_check.py
Rerun R1's human check (docs/research/paperb_reruns_prereg.md): the same 30 kept items of docs/eval/page_set_r1.json,
drawn with seed 20261008, for each author to judge independently: does the page answer the question, and is the
question specific? Writes docs/eval/page_set_r1_author_check.md (one copy; each author fills their own copy).

    /opt/anaconda3/bin/python scripts/make_r1_author_check.py
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_page_set as bp  # noqa: E402
import paperb_docs as pd  # noqa: E402
from eval_writer_window import TENANT  # noqa: E402

SET = REPO / "docs" / "eval" / "page_set_r1.json"
SHEET = REPO / "docs" / "eval" / "page_set_r1_author_check.md"
SEED = 20261008
N = 30                      # noqa: literals - pre-registered


def main() -> int:
    items = json.loads(SET.read_text())["items"]
    pick = random.Random(SEED).sample(items, N)
    ingest = json.loads(pd.INGEST.read_text())
    ids = {d["doc_name"]: d["document_id"] for d in ingest["documents"] if d.get("document_id")}
    res = subprocess.run(["docker", "exec", "-i", "statesync-mcp-paperb", "python", "-c", bp._PAGES],  # noqa: SLF001
                         input=json.dumps({"ids": sorted({ids[i["doc_name"]] for i in pick}), "tenant": TENANT}),
                         capture_output=True, text=True, timeout=600)
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    name = {v: k for k, v in ids.items()}
    text = {(name[d], int(p)): t for d, p, t in json.loads(res.stdout.strip().splitlines()[-1])}
    lines = ["# R1 page set: authors' check (30 items)", "",
             "Fill this in yourself, without an AI tool and without looking at the other author's copy. For each item, "
             "read the page and the question, then answer two things:", "",
             "- **Answers:** yes if this page contains the answer to the question (not just the topic), else no.",
             "- **Specific:** yes if the question names the company and the period and has one clear answer, else no.", ""]
    for k, it in enumerate(pick):
        lines += [f"## P{k + 1:02d}", "", f"**Question:** {it['question']}", "", f"**Filing:** {it['doc_name']}, page {it['page']}", "",
                  "<details><summary>Page text</summary>", "", "```", text[(it["doc_name"], int(it["page"]))][:bp.PAGE_CHARS], "```",
                  "", "</details>", "", "Answers: ", "", "Specific: ", ""]
    SHEET.write_text("\n".join(lines))
    (SHEET.with_suffix(".key.json")).write_text(json.dumps({f"P{k + 1:02d}": it["id"] for k, it in enumerate(pick)}, indent=1) + "\n")
    print(f"wrote {SHEET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
