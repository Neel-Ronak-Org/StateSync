"""
tests/test_fixture_corpus_guard.py
Pins that `require_shipped_flags(fixture=...)` refuses a fixture/corpus
mismatch UNCONDITIONALLY -- audit 002 F-002-3.

WHY THIS EXISTS. Cycle 001's A1 (audit 001 F-001-1) guards
`schema_retriever.target_schema` against SHIPPED_DEFAULTS, in isolation. That
does not catch 80d35c8: the SQL agent was shown the SYNTHETIC (`public`)
corpus while scored against `olist` ground truth, and `target_schema="public"`
IS the shipped default, so a guard that only compares the flag to its own
default sees nothing wrong. Demonstrated directly below: calling
`require_shipped_flags` with the `olist_hard.json` fixture named, against the
REAL committed `config/flags.toml` (target_schema="public"), reproduces
80d35c8's exact configuration and must refuse.

The refusal is deliberately UNCONDITIONAL -- `--allow-nonshipped-flags` does
not bypass it, unlike an ordinary flag deviation (e.g. disabling the
normalizer for a documented reason). A fixture/corpus mismatch is never the
point of an experiment; nothing scored against the wrong corpus is salvageable
by recording the deviation, the way a deliberately-varied flag is.

Run from the repo root: pytest tests/test_fixture_corpus_guard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from shipped_flags import FlagStateError, fixture_corpus, require_shipped_flags  # noqa: E402

OLIST_EASY = REPO_ROOT / "fixtures" / "golden" / "olist.json"
OLIST_HARD = REPO_ROOT / "fixtures" / "golden" / "olist_hard.json"
PUBLIC_FIXTURE = REPO_ROOT / "fixtures" / "golden" / "phase_c2.json"


@pytest.mark.parametrize("fixture,expected", [
    (OLIST_EASY, "olist"),
    (OLIST_HARD, "olist"),
    (PUBLIC_FIXTURE, "public"),
])
def test_fixture_corpus_reads_the_about_block(fixture, expected):
    assert fixture_corpus(fixture) == expected


def test_fixture_corpus_absence_of_the_field_means_public():
    """Every fixture in this repo except the two olist*.json ones declares no
    `_about.corpus` at all. That absence must resolve to "public" (the
    shipped default), not to None or a guess -- a silently-wrong default here
    would make the guard below vacuous for every fixture that predates this
    field's existence."""
    assert fixture_corpus(PUBLIC_FIXTURE) == "public"


def test_reproduces_80d35c8_against_the_real_committed_flags_toml():
    """The shipped config/flags.toml has target_schema="public" -- this is
    not a synthetic flip, it is 80d35c8's exact configuration reproduced
    against whatever is actually committed right now."""
    with pytest.raises(FlagStateError, match="fixture/corpus mismatch"):
        require_shipped_flags(
            "test_fixture_corpus_guard.py", allow_nonshipped=False,
            check_live=False, fixture=OLIST_HARD)


def test_the_refusal_is_unconditional_allow_nonshipped_does_not_bypass_it():
    with pytest.raises(FlagStateError, match="fixture/corpus mismatch"):
        require_shipped_flags(
            "test_fixture_corpus_guard.py", allow_nonshipped=True,
            check_live=False, fixture=OLIST_HARD)


def test_a_public_fixture_does_not_trip_the_guard_against_the_real_shipped_state():
    """The negative control: this must NOT raise for the fixture/corpus
    reason (it may raise for other reasons if flags.toml itself is somehow
    non-shipped, which is not this test's concern)."""
    record = require_shipped_flags(
        "test_fixture_corpus_guard.py", allow_nonshipped=True,
        check_live=False, fixture=PUBLIC_FIXTURE)
    assert record["fixture_corpus_check"] is None


def test_no_fixture_argument_skips_the_check_entirely():
    """Backward compatibility: every pre-existing call site that does not
    pass `fixture=` must be unaffected by this guard's existence."""
    record = require_shipped_flags(
        "test_fixture_corpus_guard.py", allow_nonshipped=True, check_live=False)
    assert record["fixture_corpus_check"] is None
