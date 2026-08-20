"""
tests/test_report_provenance_coverage.py
Enumerates every committed measurement report under docs/*.json and asserts
it carries flag_provenance.llm -- with an EXPLICIT grandfather list naming
each file that does not, and why, so the residue is visible in CI rather than
outstanding silently.

WHY THIS EXISTS. Audit 001 F-001-4 found three committed multilingual reports
with no flag_provenance at all, and cycle 001's A2 annotated exactly those
three plus added tests/test_flag_provenance.py -- which pins that a FUTURE
call to require_shipped_flags() always returns a populated llm block, but
never looks at a file on disk. Audit 002 F-002-4 re-swept every docs/*.json
and found fourteen more, including files holding today's published 44% / 36%
/ 74% / 0.983 and the evidence behind the memory.enabled=false SETTLED
decision. Cycle 002's A4(a) annotated the four tied to currently-published
numbers, to the same standard as cycle 001's three (`_reconstructed: true`,
each field citing the commit it was recovered from, no measured value
altered -- see the commit that adds this test). This file is what closes the
gap A2's own test left: annotating three examples is not the same as making
the CLASS visible, and nothing on disk or in CI could see the other eleven.

THE GRANDFATHER LIST IS NOT A PERMANENT EXEMPTION. Every entry names a file
and a reason; the reason for all current entries is the same one cycle 001's
three had -- the report's content predates shipped_flags.py's introduction
(0711c28, 2026-08-16 21:38:41), so no run captured the block because none
existed yet. The list can only SHRINK: annotate a file to the standard shown
in the four A4(a) reports, then remove its entry here. If a file is removed
from the grandfather list without gaining flag_provenance, this test fails
loudly (see test_grandfather_list_matches_reality) rather than silently
widening what CI accepts.

Run from the repo root: pytest tests/test_report_provenance_coverage.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"

# file stem -> one-line reason. Every current entry predates shipped_flags.py
# (0711c28, 2026-08-16 21:38:41); verified per-file via `git log --format="%H
# %ai" -- docs/<file>.json | tail -1` (the first, i.e. earliest, commit that
# touched the path) during audit 002's write-up -- every one of the ten
# below has its earliest commit before 21:38:41 on 2026-08-16.
GRANDFATHERED: dict[str, str] = {
    "bench_correction_report": "predates shipped_flags.py (first committed 2026-08-14 20:26)",
    "bench_grounding_report": "predates shipped_flags.py (first committed 2026-08-15 07:34)",
    "c2v8_report": "predates shipped_flags.py (first committed 2026-08-14 20:26)",
    "consistency_report": "predates shipped_flags.py (first committed 2026-08-14 16:31)",
    "e2_v1_generated_fv3_20260815T225054Z": "predates shipped_flags.py (first committed 2026-08-16 04:35)",
    "e2_v1_generated_local": "predates shipped_flags.py (first committed 2026-08-16 01:53)",
    "e2_v1_generated_local_postjsonfix": "predates shipped_flags.py (first committed 2026-08-16 02:04)",
    "f_v2_report": "predates shipped_flags.py (first committed 2026-08-15 20:47); "
                   "superseded by f_v2_report_postfix.json, annotated cycle 002 A4(a)",
    "m4_diagnosis": "predates shipped_flags.py (first committed 2026-08-16 20:32, "
                    "~66 min before the guard)",
    "m4_diagnosis_projfix": "predates shipped_flags.py (first committed 2026-08-16 20:32, "
                            "same run batch as m4_diagnosis.json)",
}


def _all_report_files() -> list[Path]:
    return sorted(DOCS.glob("*.json"))


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def test_every_non_grandfathered_report_carries_flag_provenance_llm():
    missing: list[str] = []
    for path in _all_report_files():
        stem = path.stem
        if stem in GRANDFATHERED:
            continue
        data = _load(path)
        if data is None:
            continue  # not a report-shaped JSON file; out of scope
        prov = data.get("flag_provenance")
        if not prov or not prov.get("llm"):
            missing.append(stem)
    assert not missing, (
        f"docs/*.json report(s) with no flag_provenance.llm and NOT in "
        f"GRANDFATHERED: {missing}. Either annotate to the standard in "
        f"docs/m4_diagnosis_v2_corrected.json (cycle 002 A4a) or add an "
        f"entry to GRANDFATHERED above stating why.")


def test_grandfather_list_matches_reality():
    """The list can only shrink. If a file WAS grandfathered but now carries
    flag_provenance.llm (annotated in a later cycle and the entry simply
    never removed), fail loudly rather than let the exemption silently
    outlive its reason."""
    stale: list[str] = []
    for stem, _reason in GRANDFATHERED.items():
        path = DOCS / f"{stem}.json"
        if not path.exists():
            continue
        data = _load(path)
        if data and data.get("flag_provenance", {}).get("llm"):
            stale.append(stem)
    assert not stale, (
        f"GRANDFATHERED entries that now HAVE flag_provenance.llm and should "
        f"be removed from the list: {stale}")


def test_grandfather_list_only_names_files_that_actually_exist_and_lack_it():
    """Catches a typo'd stem or an entry that outlived its file."""
    for stem in GRANDFATHERED:
        path = DOCS / f"{stem}.json"
        assert path.exists(), f"GRANDFATHERED names {stem}.json, which does not exist"


@pytest.mark.parametrize("stem", sorted(GRANDFATHERED))
def test_each_grandfathered_file_is_genuinely_missing_provenance(stem):
    data = _load(DOCS / f"{stem}.json")
    assert data is not None, f"{stem}.json is not report-shaped JSON"
    prov = data.get("flag_provenance")
    assert not prov or not prov.get("llm"), (
        f"{stem}.json IS carrying flag_provenance.llm -- remove it from "
        f"GRANDFATHERED (see test_grandfather_list_matches_reality)")
