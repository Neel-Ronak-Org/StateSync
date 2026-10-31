"""
tests/test_evidence.py
The evidence label on an answer (orchestrator/evidence.py).

The label tells a reader how far to trust an answer, so "verified" must be earned: only an approved example
run as is. New SQL is "unchecked" (shown as "New query"), and an answer that ran no SQL carries no label.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

from evidence import evidence_tier  # noqa: E402


def test_an_approved_example_is_verified():
    assert evidence_tier(True, "SELECT 1") == "verified"


def test_new_sql_is_unchecked():
    assert evidence_tier(False, "SELECT 1") == "unchecked"


def test_no_sql_means_no_label():
    assert evidence_tier(False, None) is None
    assert evidence_tier(False, "") is None
