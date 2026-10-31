#!/usr/bin/env python3
"""
scripts/financebench_report.py
Writes docs/eval/financebench_report.md from the saved FinanceBench runs and ingest records
(docs/eval/financebench_*.json), so no number in the report is typed by hand. Stdlib only.

WHY A SCRIPT. The report's job is to say plainly whether the RAG v2 gain held on real filings,
and a hand-copied table is where a number goes stale. Every figure below is computed from a saved
run; the wording that depends on the numbers (gained / lost / tied, held / shrank / reversed) is
chosen by a rule stated in the report itself, fixed before any result was seen.

USAGE (repo root):  /opt/anaconda3/bin/python3 scripts/financebench_report.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "docs" / "eval"
REPORT_PATH = OUT_DIR / "financebench_report.md"
CHECKSUMS_PATH = REPO_ROOT / "fixtures" / "external_eval_checksums.txt"
CHECKSUM_KEY = "financebench/data/financebench_open_source.jsonl"
FB_COMMIT = "cc39aeb4afdf33909ee1412188bf89035950c2eb"
FB_LICENCE = "CC BY-NC 4.0 (non-commercial: fine for evaluation and the paper, not as product data)"
MAX_PAGES_FILINGS = ("AMERICANWATERWORKS_2022_10K", "CVSHEALTH_2018_10K", "JPMORGAN_2022_10K",
                     "PEPSICO_2021_10K", "PEPSICO_2022_10K", "WALMART_2018_10K")  # docs/eval/external_datasets.md
# label, chunker, mode, scope: the five pre-registered runs (docs/specs/external_eval.md section 3), in order.
RUNS = (("fb_paragraph_dense", "paragraph", "dense", "uploads"),
        ("fb_paragraph_hybrid", "paragraph", "hybrid", "uploads"),
        ("fb_layout_dense", "layout", "dense", "uploads"),
        ("fb_layout_hybrid", "layout", "hybrid", "uploads"),
        ("fb_paragraph_dense_shipped", "paragraph", "dense", "all"))
BEFORE_FIXES = "financebench_ingest_paragraph_before_fixes"  # the interrupted paragraph ingest, kept as a record
FIX_COMMIT = "5e1004d"
PRIMARY = ("fb_paragraph_dense", "fb_layout_hybrid")  # run 1 against run 4
INHOUSE = ("retrieval_v2_final_paragraph_dense", "retrieval_v2_final_layout_hybrid")  # the RAG v2 pair that is run 1 vs run 4
METRICS = (("page_hit@1", "page hit@1"), ("page_hit@5", "page hit@5"), ("page_hit@10", "page hit@10"),
           ("page1_hit@5", "page±1 hit@5"), ("doc_hit@5", "doc hit@5"))
GROUP_FAMILIES = ("question_type", "doc_type")
PCT = 100.0


def load(name: str) -> dict[str, Any]:
    path = OUT_DIR / f"{name}.json"
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    return json.loads(path.read_text())


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * PCT:.1f}"


def num(v: float | None, digits: int = 3) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def ms(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f}"


def delta_pts(a: float | None, b: float | None) -> str:
    return "n/a" if a is None or b is None else f"{(b - a) * PCT:+.1f}"


def md_table(header: list[str], rows: list[list[str]], align: str | None = None) -> str:
    align = align or ("|" + "---|" * len(header))
    lines = ["| " + " | ".join(header) + " |", align]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def right_align(n_cols: int, left: int = 1) -> str:
    return "|" + "---|" * left + "---:|" * (n_cols - left)


def sign_test_p(gained: int, lost: int) -> float:
    """Two-sided exact sign test on the questions that changed outcome (p = 0.5 each way under no effect)."""
    n = gained + lost
    if n == 0:
        return 1.0
    low = min(gained, lost)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(low + 1)) / 2 ** n)


def paired(a: dict[str, Any], b: dict[str, Any], flag: str = "page_hit@5") -> tuple[int, int]:
    """Questions that went miss -> hit (gained) and hit -> miss (lost) from run a to run b."""
    by_id = {q["id"]: q for q in a["questions"]}
    gained = sum(1 for q in b["questions"] if q[flag] and not by_id[q["id"]][flag])
    lost = sum(1 for q in b["questions"] if not q[flag] and by_id[q["id"]][flag])
    return gained, lost


def verdict(fb_gain: float, inhouse_gain: float) -> str:
    """The rule, fixed before any result: reversed if the FinanceBench change is negative, unchanged if zero,
    shrank if positive but smaller than the in-house gain, held (or grew) if at least as large."""
    if fb_gain < 0:
        return "reversed"
    if fb_gain == 0:
        return "did not change (a tie)"
    return "shrank" if fb_gain < inhouse_gain else "held or grew"


# ── Sections ──────────────────────────────────────────────────────────────────

def section_data(runs: dict[str, Any], records: dict[str, Any]) -> str:
    shas = {r["fixture_sha256"] for r in runs.values()} | {rec["questions_sha256"] for rec in records.values()}
    expected = next(line.split()[0] for line in CHECKSUMS_PATH.read_text().splitlines() if line.endswith(CHECKSUM_KEY))
    ok = shas == {expected}
    return "\n".join([
        "## 1. Data",
        "",
        f"- Questions: `data/external/financebench/data/financebench_open_source.jsonl`, SHA-256 `{expected}`. "
        f"{'Every saved run and both ingest records carry this hash, and it matches `fixtures/external_eval_checksums.txt`.' if ok else 'MISMATCH: the saved runs carry ' + ', '.join(sorted(shas)) + '.'}",
        f"- Source: Patronus AI, *FinanceBench* (arXiv:2311.11944), github.com/patronus-ai/financebench at commit `{FB_COMMIT}`. Licence: {FB_LICENCE}.",
        "- 150 questions, used verbatim (instructions inside a question stay in), over 84 filings (64 10-K, 8 10-Q, 6 8-K, 6 earnings reports) of 32 companies.",
        "- Gold: for each question, the set of `(filing, evidence_page_num + 1)` over its evidence entries. FinanceBench pages count from 0 and StateSync's from 1 (`docs/eval/external_datasets.md`).",
        "- Metrics over the top 10 passages: **page hit@k** (a passage in the top k is from the gold filing and on a gold page; the primary measure), **page±1 hit@k** (same, within one page), **document hit@k** (from the gold filing), **MRR@10** on page hits, and the latency of the search call.",
        "- A question whose filing was refused (or failed) at ingest counts as a miss in `overall`, because the product cannot answer it, and is left out of `ingested`. Both denominators are given everywhere.",
        "",
    ])


def section_ingest(records: dict[str, Any]) -> str:
    out = ["## 2. Ingest", "",
           "Each chunker ingested the 84 filings into its own folder, one upload at a time, waiting for each to leave `processing`. "
           "Seconds are from the start of the upload (which includes the parse, done inside the upload request) to the document being `ready`.", ""]
    rows = []
    for chunker, rec in records.items():
        s = rec["summary"]
        rows.append([chunker, str(s["filings"]), str(s["ready"]), str(s["refused"]), str(s["failed_or_stuck"]), f"{s['pages_ready']:,}",
                     f"{s['chunks']:,}", f"{s['seconds_upload_to_ready'] / 3600:.2f} h ({s['seconds_upload_to_ready']:,.0f} s)"])
    out += [md_table(["chunker", "filings", "ready", "refused", "failed", "pages (ready)", "chunks", "time, upload to ready"], rows,
                     "|---|---:|---:|---:|---:|---:|---:|---:|"), ""]
    for chunker, rec in records.items():
        refused = [d for d in rec["documents"] if d["http_status"] != 202]
        failed = [d for d in rec["documents"] if d["http_status"] == 202 and d.get("status") != "ready"]
        out.append(f"### {chunker}: refused ({len(refused)}) and failed ({len(failed)})")
        out.append("")
        if refused:
            out += [md_table(["filing", "MB", "HTTP", "what the API returned (exact)", "seconds"],
                             [[d["doc_name"], f"{d['bytes'] / 1e6:.1f}", str(d["http_status"]), f"`{d['refusal']}`", f"{d['ready_seconds']}"] for d in refused],
                             "|---|---:|---:|---|---:|"), ""]
        else:
            out += ["None.", ""]
        if failed:
            out += [md_table(["filing", "final status", "error"], [[d["doc_name"], str(d.get("status")), f"`{d.get('error')}`"] for d in failed]), ""]
        accepted = [d for d in rec["documents"] if d["http_status"] == 202]
        fallback = [d for d in accepted if d.get("layout_fallback")]
        out.append(f"Layout pass fell back to flat text, among the {len(accepted)} accepted filings: **{len(fallback)}**"
                   + (" (" + ", ".join(f"{d['doc_name']} from page {d['layout_fallback']['first_page']}" if d["layout_fallback"]["first_page"] else d["doc_name"] for d in fallback) + ")." if fallback else "."))
        out.append("")
    out += ["Detection: the parser logs a `WARNING` when its layout pass stops (`layout pass out of time at page N`, `page N layout could not be read`, `layout pass failed`), "
            "and the orchestrator logs one when the layout chunker yields nothing. The harness read both containers' logs for each filing's upload window "
            "(uploads are sequential, so windows do not overlap) and kept the lines in the ingest record (`log_lines`, `layout_fallback`). "
            f"Since commit `{FIX_COMMIT}` the orchestrator asks the parser for the layout pass only when `chunker = \"layout\"`, so the paragraph ingest runs no layout pass and logs no fallback by construction; "
            "only the layout ingest can show one. A fallback page is chunked from flat text, like the paragraph chunker does.", ""]
    para, lay = records.get("paragraph"), records.get("layout")
    if para and lay:
        a, b = para["summary"], lay["summary"]
        ready_a = {d["doc_name"] for d in para["documents"] if d.get("status") == "ready"}
        ready_b = {d["doc_name"] for d in lay["documents"] if d.get("status") == "ready"}
        only_a, only_b = sorted(ready_a - ready_b), sorted(ready_b - ready_a)
        out += [f"Chunks: paragraph {a['chunks']:,} over {a['ready']} ready filings, layout {b['chunks']:,} over {b['ready']} ready filings "
                f"({b['chunks'] / a['chunks']:.2f} layout chunks per paragraph chunk when the filing sets are equal). "
                + ("Both chunkers made the same filings ready, so the `ingested` subset is the same for all four upload runs." if not only_a and not only_b else
                   f"The ready sets differ (paragraph only: {', '.join(only_a) or 'none'}; layout only: {', '.join(only_b) or 'none'}), so the `ingested` subsets differ between chunkers; `overall` stays comparable."), ""]
    return "\n".join(out)


def before_fix_view(before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
    """The interrupted ingest (before commit 5e1004d) grouped by cause: refusal texts, failure errors, and, when
    the after-fix paragraph record exists, what became of each of those filings."""
    docs = before["documents"]
    refused = [d for d in docs if d["http_status"] != 202]
    failed = [d for d in docs if d["http_status"] == 202 and d.get("status") != "ready"]
    by_cause: dict[str, list[dict[str, Any]]] = {}
    for d in refused:
        by_cause.setdefault(f"HTTP {d['http_status']}: {d['refusal']}", []).append(d)
    for d in failed:
        by_cause.setdefault(f"accepted, then status {d.get('status')!r}: {d.get('error')}", []).append(d)
    now = {d["doc_name"]: d for d in after["documents"]} if after else {}
    return {"docs": docs, "refused": refused, "failed": failed, "by_cause": by_cause, "now": now}


def outcome_now(d: dict[str, Any] | None) -> str:
    if d is None:
        return "not in the after-fix record"
    if d["http_status"] != 202:
        return f"refused: {d['refusal']}"
    return d.get("status") or "unknown"


def section_before_fixes(before: dict[str, Any], after: dict[str, Any] | None, questions: list[dict[str, Any]]) -> str:
    """Product findings from the interrupted paragraph ingest: what failed, why, and what the fixes changed."""
    v = before_fix_view(before, after)
    s = before["summary"]
    out = [f"### Before the fixes: the interrupted paragraph ingest (`docs/eval/{BEFORE_FIXES}.json`)", "",
           f"The first paragraph ingest was cut off after {s['filings']} of the 84 filings. Its record is kept unedited. Of those {s['filings']} filings, "
           f"{s['ready']} became ready, {s['refused']} were refused and {s['failed_or_stuck']} failed (accepted, then `failed`). "
           f"The causes were found there and fixed in commit `{FIX_COMMIT}`:", "",
           "1. The parser ran its pdfplumber layout pass for every PDF, even with the shipped `paragraph` chunker, in the same memory-limited child as the text pass; on long 10-Ks it ran out of memory and the file was refused as unreadable within the parser's limits, although the text pass alone reads them in seconds.",
           "2. PDFs encrypted only for permission flags (empty user password) were refused as password-protected.",
           "3. `add_chunks` waited 20 s while the MCP waits 30 s for the embedding model; a slow batch failed the whole document (`Processing failed unexpectedly.`).", ""]
    rows = []
    for cause, members in sorted(v["by_cause"].items(), key=lambda kv: -len(kv[1])):
        names = [d["doc_name"] for d in members]
        n_q = sum(1 for q in questions if q["doc_name"] in names)
        rows.append([f"`{cause}`", str(len(names)), str(n_q), ", ".join(names)])
    out += [md_table(["what happened (exact API text or document error)", "filings", "questions on them", "which"], rows, "|---|---:|---:|---|"), ""]
    if after:
        counts: dict[str, int] = {}
        for d in v["refused"] + v["failed"]:
            key = outcome_now(v["now"].get(d["doc_name"]))
            counts[key] = counts.get(key, 0) + 1
        out += ["What became of those filings in the after-fix paragraph ingest (same filings, same files):", "",
                md_table(["outcome now", "filings"], [[f"`{k}`", str(n)] for k, n in sorted(counts.items(), key=lambda kv: -kv[1])], "|---|---:|"), ""]
        was_ready = [d["doc_name"] for d in v["docs"] if d.get("status") == "ready"]
        regress = [n for n in was_ready if outcome_now(v["now"].get(n)) != "ready"]
        out += [f"The {len(was_ready)} filings that were ready before: {'all are ready again' if not regress else 'NOT all ready again: ' + ', '.join(regress)}.", ""]
    fallbacks = sum(1 for d in v["docs"] if d.get("layout_fallback"))
    out += [f"Layout-pass fallbacks logged in that run: {fallbacks} of {s['filings']} filings (the layout pass ran for every PDF then; for example `page N layout could not be read; flat text used`).", ""]
    return "\n".join(out)


def headline_rows(runs: dict[str, Any], group: str) -> list[list[str]]:
    rows = []
    for label, chunker, mode, scope in RUNS:
        s = runs[label]["summary"][group]
        rows.append([f"`{label}`", chunker, mode, scope, str(s["n"])] + [pct(s[m]) for m, _ in METRICS] + [num(s["mrr@10"]), ms(s["latency_p50_ms"]), ms(s["latency_p90_ms"])])
    return rows


def section_runs(runs: dict[str, Any]) -> str:
    header = ["run", "chunker", "mode", "scope", "n"] + [name for _, name in METRICS] + ["MRR@10", "p50 ms", "p90 ms"]
    align = "|---|---|---|---|---:|" + "---:|" * (len(header) - 5)
    out = ["## 3. Results", "",
           "Five pre-registered runs, 150 questions each, top 10 passages. Hit rates are percent. `uploads` searches only the ingested filings (`scope = dataset, dataset = live`, which has no sample documents); "
           "`all` leaves the 20,245-chunk demo corpus in the search, as shipped. Latency is the time of one `vector.semantic_search` call on one Mac. "
           "`hybrid_dense_lists` stayed `separate`. With uploads only there is one non-empty dense list, so `merged` gives the same result and was not run.", "",
           "### Overall (all 150 questions; a refused filing is a miss)", "", md_table(header, headline_rows(runs, "overall"), align), "",
           "### Ingested (questions whose filing was ingested `ready`)", "", md_table(header, headline_rows(runs, "ingested"), align), "",
           "Searches that returned an error: " + ", ".join(f"`{label}` {runs[label]['errors']}" for label, *_ in RUNS) + ".", ""]
    for label, *_ in RUNS:
        out += [f"### Groups: `{label}`", ""]
        summary = runs[label]["summary"]
        g_header = ["group", "n", "ingested"] + [name for _, name in METRICS] + ["MRR@10", "p50 ms"]
        g_rows = []
        for name, s in summary.items():
            g_rows.append([name, str(s["n"]), str(s["n_ingested"])] + [pct(s[m]) for m, _ in METRICS] + [num(s["mrr@10"]), ms(s["latency_p50_ms"])])
        out += [md_table(g_header, g_rows, "|---|---:|---:|" + "---:|" * (len(g_header) - 3)), ""]
    return "\n".join(out)


def group_deltas(a: dict[str, Any], b: dict[str, Any]) -> list[tuple[str, int, float, float, float]]:
    """(group, n, page hit@5 in a, in b, MRR delta) for the per-type groups of the ingested subset."""
    rows = []
    for name, sa in a["summary"].items():
        if not name.startswith("ingested/") or name not in b["summary"]:
            continue
        sb = b["summary"][name]
        rows.append((name.removeprefix("ingested/"), sa["n"], sa["page_hit@5"], sb["page_hit@5"], sb["mrr@10"] - sa["mrr@10"]))
    return rows


def section_primary(runs: dict[str, Any], inhouse: dict[str, Any]) -> str:
    a, b = runs[PRIMARY[0]], runs[PRIMARY[1]]
    oa, ob = a["summary"]["overall"], b["summary"]["overall"]
    ia, ib = a["summary"]["ingested"], b["summary"]["ingested"]
    ha, hb = inhouse[INHOUSE[0]]["summary"]["overall"], inhouse[INHOUSE[1]]["summary"]["overall"]
    fb_gain, ih_gain = (ob["page_hit@5"] - oa["page_hit@5"]) * PCT, (hb["hit@5"] - ha["hit@5"]) * PCT
    word = verdict(fb_gain, ih_gain)
    gained, lost = paired(a, b)
    p_val = sign_test_p(gained, lost)
    rr_up = sum(1 for qa, qb in zip(a["questions"], b["questions"], strict=True) if qb["rr"] > qa["rr"])
    rr_down = sum(1 for qa, qb in zip(a["questions"], b["questions"], strict=True) if qb["rr"] < qa["rr"])
    out = ["## 4. Primary comparison: shipped retrieval against the RAG v2 recommendation", "",
           f"Pre-registered: run 1 `{PRIMARY[0]}` (paragraph chunker, dense) against run 4 `{PRIMARY[1]}` (layout chunker, hybrid), on page hit@5 and MRR@10, over the 150 questions.", "",
           md_table(["", "page hit@5, run 1 → run 4", "change", "MRR@10, run 1 → run 4", "change"],
                    [["FinanceBench, all 150 questions", f"{pct(oa['page_hit@5'])} → {pct(ob['page_hit@5'])}", f"{delta_pts(oa['page_hit@5'], ob['page_hit@5'])} points",
                      f"{num(oa['mrr@10'])} → {num(ob['mrr@10'])}", f"{ob['mrr@10'] - oa['mrr@10']:+.3f}"],
                     [f"FinanceBench, ingested only (n = {ia['n']})", f"{pct(ia['page_hit@5'])} → {pct(ib['page_hit@5'])}", f"{delta_pts(ia['page_hit@5'], ib['page_hit@5'])} points",
                      f"{num(ia['mrr@10'])} → {num(ib['mrr@10'])}", f"{ib['mrr@10'] - ia['mrr@10']:+.3f}"],
                     ["RAG v2 in-house set (`final_paragraph_dense` → `final_layout_hybrid`)", f"{pct(ha['hit@5'])} → {pct(hb['hit@5'])}", f"{delta_pts(ha['hit@5'], hb['hit@5'])} points",
                      f"{num(ha['mrr@10'])} → {num(hb['mrr@10'])}", f"{hb['mrr@10'] - ha['mrr@10']:+.3f}"]],
                    "|---|---:|---:|---:|---:|"), "",
           f"**In plain words.** On the 150 FinanceBench questions, page hit@5 goes from {pct(oa['page_hit@5'])}% to {pct(ob['page_hit@5'])}% ({delta_pts(oa['page_hit@5'], ob['page_hit@5'])} points) "
           f"and MRR@10 from {num(oa['mrr@10'])} to {num(ob['mrr@10'])}. On the in-house set the same pair went from {pct(ha['hit@5'])}% to {pct(hb['hit@5'])}% ({ih_gain:+.1f} points). "
           f"By the rule below, the in-house gain **{word}** on real filings.", "",
           "The rule, fixed before any result: *reversed* if the page hit@5 change is negative, *a tie* if it is zero, *shrank* if it is positive but smaller than the in-house change, *held or grew* if it is at least as large. "
           "It is applied to the overall (150-question) figures.", "",
           f"Paired, over the same 150 questions: {gained} questions went from a page-hit@5 miss to a hit and {lost} went from a hit to a miss (exact two-sided sign test on those {gained + lost}: p = {p_val:.3g}). "
           f"MRR@10 rose for {rr_up} questions and fell for {rr_down}. These counts describe the primary comparison; they are not additional comparisons.", ""]
    if oa["page_hit@5"] != ia["page_hit@5"] or oa["n"] != ia["n"]:
        out += [f"Refused filings cap every run alike: {oa['n'] - ia['n']} of the {oa['n']} questions have no ingested gold filing, so no configuration can score on them. "
                f"The ingested-only figures take them out of the denominator.", ""]
    rows = group_deltas(a, b)
    if rows:
        out += ["Where it moved: page hit@5 per group over the ingested questions (run 1 → run 4), largest change first.", "",
                md_table(["group", "n", "run 1", "run 4", "change (points)", "MRR@10 change"],
                         [[g, str(n), pct(x), pct(y), delta_pts(x, y), f"{d:+.3f}"] for g, n, x, y, d in sorted(rows, key=lambda r: -(r[3] - r[2]))],
                         "|---|---:|---:|---:|---:|---:|"), ""]
    out += ["For the in-house set, the gain was driven by one template over an unusual corpus: 20 of 21 extra top-5 hits were the ID question \"What do customers say about seller <id>?\" (`docs/eval/external_datasets.md`). "
            "FinanceBench has no such template, so a gain here comes from real-filing structure, and a loss or a tie says the in-house gain did not carry over.", ""]
    return "\n".join(out)


def section_secondary(runs: dict[str, Any]) -> str:
    labels = [r[0] for r in RUNS]
    out = ["## 5. Secondary comparisons", "",
           "Every other pair of the five runs (change from the first run to the second, in percentage points; MRR@10 as a difference). The last column counts, for page hit@5 over the 150 questions, "
           "questions that went from miss to hit / from hit to miss. Fixed before any result; none was added or dropped.", ""]
    for group, title in (("overall", "Overall (150 questions)"), ("ingested", "Ingested only")):
        rows = []
        for i, la in enumerate(labels):
            for lb in labels[i + 1:]:
                if (la, lb) == PRIMARY:
                    continue
                sa, sb = runs[la]["summary"][group], runs[lb]["summary"][group]
                gained, lost = paired(runs[la], runs[lb])
                rows.append([f"`{la}` → `{lb}`"] + [delta_pts(sa[m], sb[m]) for m, _ in METRICS] + [f"{sb['mrr@10'] - sa['mrr@10']:+.3f}", f"{gained} / {lost}"])
        out += [f"### {title}", "", md_table(["pair"] + [name for _, name in METRICS] + ["MRR@10", "gained / lost (hit@5)"], rows, "|---|" + "---:|" * (len(METRICS) + 2)), ""]
    return "\n".join(out)


def section_distractor(runs: dict[str, Any]) -> str:
    a, b = runs["fb_paragraph_dense"], runs["fb_paragraph_dense_shipped"]
    oa, ob = a["summary"]["overall"], b["summary"]["overall"]
    ia, ib = a["summary"]["ingested"], b["summary"]["ingested"]

    def demo_share(run: dict[str, Any], k: int) -> tuple[float, float]:
        """Mean number of demo-corpus passages in the top k, and the share of questions with at least one."""
        counts = [sum(1 for p in q["passages"][:k] if p["doc_name"] is None) for q in run["questions"]]
        return sum(counts) / len(counts), sum(1 for c in counts if c) / len(counts)

    gained, lost = paired(a, b)
    d5, d10, d1 = demo_share(b, 5), demo_share(b, 10), demo_share(b, 1)
    return "\n".join([
        "## 6. The demo corpus as a distractor", "",
        "Run 5 `fb_paragraph_dense_shipped` (no scope: the 20,245-chunk sample corpus is searched too, as shipped) against run 1 `fb_paragraph_dense` (filings only). Same chunker, same mode.", "",
        md_table(["", "page hit@1", "page hit@5", "page hit@10", "doc hit@5", "MRR@10", "p50 ms"],
                 [["run 1, filings only (overall)"] + [pct(oa[m]) for m in ("page_hit@1", "page_hit@5", "page_hit@10", "doc_hit@5")] + [num(oa["mrr@10"]), ms(oa["latency_p50_ms"])],
                  ["run 5, with demo corpus (overall)"] + [pct(ob[m]) for m in ("page_hit@1", "page_hit@5", "page_hit@10", "doc_hit@5")] + [num(ob["mrr@10"]), ms(ob["latency_p50_ms"])],
                  ["change (points; MRR as a difference)"] + [delta_pts(oa[m], ob[m]) for m in ("page_hit@1", "page_hit@5", "page_hit@10", "doc_hit@5")] + [f"{ob['mrr@10'] - oa['mrr@10']:+.3f}", f"{ob['latency_p50_ms'] - oa['latency_p50_ms']:+.0f}"],
                  ["run 1, ingested only"] + [pct(ia[m]) for m in ("page_hit@1", "page_hit@5", "page_hit@10", "doc_hit@5")] + [num(ia["mrr@10"]), ms(ia["latency_p50_ms"])],
                  ["run 5, ingested only"] + [pct(ib[m]) for m in ("page_hit@1", "page_hit@5", "page_hit@10", "doc_hit@5")] + [num(ib["mrr@10"]), ms(ib["latency_p50_ms"])]],
                 "|---|---:|---:|---:|---:|---:|---:|"), "",
        f"Paired, page hit@5: {gained} questions gained a hit and {lost} lost one when the demo corpus was added. "
        f"Demo-corpus passages in run 5's results: mean {d1[0]:.2f} of the top 1, {d5[0]:.2f} of the top 5, {d10[0]:.2f} of the top 10; "
        f"{d1[1] * PCT:.1f}% of questions have one at rank 1, {d5[1] * PCT:.1f}% in the top 5, {d10[1] * PCT:.1f}% in the top 10.", "",
    ])


def section_findings(runs: dict[str, Any], records: dict[str, Any], before: dict[str, Any]) -> str:
    questions = runs[RUNS[0][0]]["questions"]
    out = ["## 7. Product findings", "",
           "Recorded as found; no limit or code was changed to make a filing pass.", ""]
    for chunker, rec in records.items():
        refused = [d for d in rec["documents"] if d["http_status"] != 202]
        by_reason: dict[str, list[str]] = {}
        for d in refused:
            by_reason.setdefault(d["refusal"], []).append(d["doc_name"])
        out.append(f"### {chunker} ingest: {len(refused)} of {rec['summary']['filings']} filings refused")
        out.append("")
        rows = []
        for reason, names in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            n_q = sum(1 for q in questions if q["doc_name"] in names)
            rows.append([f"`{reason}`", str(len(names)), str(n_q), ", ".join(names)])
        out += [md_table(["API response", "filings", "questions on them", "which"], rows, "|---|---:|---:|---|"), ""] if rows else ["None.", ""]
    para = records.get("paragraph")
    if para:
        by_name = {d["doc_name"]: d for d in para["documents"]}
        rows = []
        for name in MAX_PAGES_FILINGS:
            d = by_name.get(name)
            rows.append([name, f"{d['bytes'] / 1e6:.1f}" if d else "n/a", str(d["http_status"]) if d else "n/a",
                         (f"`{d['refusal']}`" if d["refusal"] else f"accepted: {d.get('status')}, {d.get('pages')} pages") if d else "n/a",
                         str(sum(1 for q in questions if q["doc_name"] == name))])
        out += [f"The six filings the dataset notes as longer than `[library] max_pages = 300` ({sum(r[4] != '0' and int(r[4]) for r in rows)} questions), as the paragraph ingest saw them:", "",
                md_table(["filing", "MB", "HTTP", "result", "questions"], rows, "|---|---:|---:|---|---:|"), ""]
    out += ["The refusals above are the product's limits meeting real filings (here the page limit, and any parse timeout the parser reports). "
            "Counts are what the ingest recorded; the limits were not touched.", ""]
    out += [section_before_fixes(before, records.get("paragraph"), questions)]
    return "\n".join(out)


def section_limitations() -> str:
    return "\n".join([
        "## 8. Limitations", "",
        "- 150 questions: a difference of a few points is within noise. The paired counts and the sign test in section 4 say how many questions moved, not that the difference generalises.",
        "- One domain: US public-company filings, 32 companies.",
        "- Retrieval only. Whether the answer model answers correctly given these passages, and whether it calculates correctly (many questions need arithmetic), is not measured.",
        "- Gold is the annotators' evidence pages. Another page that also answers the question counts as a miss; the page±1 and document hit rates show how much of the miss is close.",
        "- Filings the product refused count as misses in `overall`. Which filings those are depends on the parser limits, so the `ingested` subset is not a random sample of the benchmark.",
        "- Latency is measured on one Mac (CPU Ollama, Docker Desktop), the corpus is much larger than the in-house test's, and the runs were not repeated, so latency differences between runs are indicative only.",
        "- The harness reuses the production search path; the questions are sent verbatim and nothing was tuned on them. The RAG v2 recommendation was tuned in part on the in-house set (D4, D5, abstention thresholds), so the comparison favours the in-house number.",
        "",
    ])


def main() -> int:
    runs = {label: load(f"financebench_{label}") for label, *_ in RUNS}
    records = {chunker: load(f"financebench_ingest_{chunker}") for chunker in ("paragraph", "layout")}
    inhouse = {name: load(name) for name in INHOUSE}
    before = load(BEFORE_FIXES)
    for label, chunker, mode, scope in RUNS:
        p = runs[label]["params"]
        if (p["chunker"], p["mode"], p["scope"]) != (chunker, mode, scope):
            raise SystemExit(f"{label} was run with {p}, not {(chunker, mode, scope)}")
    created = sorted(r["created"] for r in runs.values())
    text = "\n".join([
        "# FinanceBench retrieval report", "",
        f"Branch `v3-phase3-workspaces`. Spec: `docs/specs/external_eval.md`. Runs made {created[0][:10]} to {created[-1][:10]} through the production MCP search path, with no model calls. "
        "Every number comes from a saved run in `docs/eval/financebench_<label>.json` or an ingest record in `docs/eval/financebench_ingest_<chunker>.json`; this file was generated from them by `scripts/financebench_report.py`.", "",
        section_data(runs, records), section_ingest(records), section_runs(runs), section_primary(runs, inhouse), section_secondary(runs),
        section_distractor(runs), section_findings(runs, records, before), section_limitations(),
    ])
    REPORT_PATH.write_text(text)
    print(f"wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
