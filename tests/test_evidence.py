"""
tests/test_evidence.py
The evidence tier rule (orchestrator/evidence.py, docs/V3_PLAN.md Phase 3c).

WHY THIS EXISTS: the tier is shown to readers as how far to trust an answer, so
each rung must be earned: "checked" only when the checks ran AND found nothing,
and an answer that ran no SQL must carry no tier at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

from evidence import evidence_tier  # noqa: E402

ON = {"pipeline.answer_checks": "on"}
OFF = {"pipeline.answer_checks": "off"}


def test_an_approved_example_is_verified_whatever_the_checks_say():
    assert evidence_tier(True, "SELECT 1", OFF, ["a caveat"]) == "verified"


def test_new_sql_that_passed_the_checks_is_checked():
    assert evidence_tier(False, "SELECT 1", ON, []) == "checked"


def test_new_sql_with_a_caveat_is_unchecked():
    assert evidence_tier(False, "SELECT 1", ON, ["join fan-out"]) == "unchecked"


def test_new_sql_with_the_checks_off_or_absent_is_unchecked():
    assert evidence_tier(False, "SELECT 1", OFF, []) == "unchecked"
    assert evidence_tier(False, "SELECT 1", {}, []) == "unchecked"


def test_no_sql_means_no_tier():
    assert evidence_tier(False, None, ON, []) is None
    assert evidence_tier(False, "", ON, []) is None


def test_a_compiled_template_is_compiled_unless_verified():
    assert evidence_tier(False, "SELECT 1", ON, [], True) == "compiled"
    assert evidence_tier(False, "SELECT 1", OFF, ["a caveat"], True) == "compiled"
    assert evidence_tier(True, "SELECT 1", ON, [], True) == "verified"
    assert evidence_tier(False, None, ON, [], True) is None
