"""
tests/test_scorer_copies_identical.py
Pins that orchestrator/scorers.py and evaluator/scorers.py stay byte-identical
apart from the self-referential path in their header comment.

WHY THIS EXISTS. Audit 001, F-001-5 (the row_recall de-duplication defect) had
to be fixed in BOTH files by hand, because they are two independent copies of
the same ~420 lines rather than one shared module -- the file's own header
comment calls out BD4/BD5/BD7 as instances of exactly this shape of drift
(two hand-maintained lists of one fact), and this is a third, in the scorer
itself.

scripts/verify_deployed.py's "Scorer registries agree" check compares
registered NAMES across the two running containers, not the implementations
behind those names -- so "the evaluator scores a run the same way the
published metric was scored" was a claim with nothing checking it (audit 001
checklist class 10). This test is the thing that checks it: if the two files
ever diverge in behaviour, this fails BEFORE that divergence reaches a
published number, rather than being caught by two numbers quietly disagreeing
later.

NOT a proposal to unify them into one shared module -- that changes both
Dockerfiles' build context and was not examined by the audit that found this
gap. This is the cheaper fix: keep the duplication, stop it from being able to
drift silently.

Run from the repo root: pytest tests/test_scorer_copies_identical.py
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR_SCORERS = REPO_ROOT / "orchestrator" / "scorers.py"
EVALUATOR_SCORERS = REPO_ROOT / "evaluator" / "scorers.py"

# The only difference this test permits: each file names itself in its own
# header comment. Normalizing BOTH self-references to a placeholder before
# comparing means the test does not care which line the path is on, only that
# it is the sole difference -- so a header reformat does not need this test
# rewritten, but a real behavioural divergence still fails it.
_SELF_REFERENCES = ("orchestrator/scorers.py", "evaluator/scorers.py")


def _normalized(text: str) -> str:
    for ref in _SELF_REFERENCES:
        text = text.replace(ref, "<SELF_PATH>")
    return text


def test_orchestrator_and_evaluator_scorers_are_the_same_file():
    orch = ORCHESTRATOR_SCORERS.read_text()
    ev = EVALUATOR_SCORERS.read_text()

    assert _normalized(orch) == _normalized(ev), (
        "orchestrator/scorers.py and evaluator/scorers.py have diverged in "
        "something other than their self-referential header path. These are "
        "two independent copies of the same scoring logic (not a shared "
        "module) -- a fix applied to one and not the other means the "
        "orchestrator and the evaluator score identically-named metrics "
        "differently. Apply the change to both files identically."
    )


def test_the_permitted_difference_is_exactly_the_self_reference():
    """
    Guards the guard: if BOTH files somehow lost their self-reference (e.g. a
    careless find-and-replace), the test above would still pass -- comparing
    two files that no longer differ at all in the permitted way is not the
    same claim as comparing two correctly self-labelled copies. This asserts
    each file actually names itself, so the normalization above is checking
    something real.
    """
    orch = ORCHESTRATOR_SCORERS.read_text()
    ev = EVALUATOR_SCORERS.read_text()
    assert "orchestrator/scorers.py" in orch
    assert "evaluator/scorers.py" in ev
    assert "evaluator/scorers.py" not in orch, (
        "orchestrator/scorers.py references evaluator/scorers.py's path -- "
        "check for a bad copy-paste")
    assert "orchestrator/scorers.py" not in ev, (
        "evaluator/scorers.py references orchestrator/scorers.py's path -- "
        "check for a bad copy-paste")
