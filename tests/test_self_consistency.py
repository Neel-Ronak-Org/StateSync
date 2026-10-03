"""
tests/test_self_consistency.py
The study's self-consistency baseline (orchestrator/self_consistency.py; protocol v2 §4).

WHY THIS EXISTS. Risk = the share of k resampled queries whose result differs from the answer's.
Pinned: results compare as sets of rows whatever the column names and order, numbers to 5
significant digits; a sample that errors, fails to run or returns another result counts as
differing; samples' tokens are summed into the record; the flag ships at 0 and the state field
is last-write-wins.
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import self_consistency as sc  # noqa: E402

ANSWER = [{"driver": "Alain Prost", "n": 9}]


def test_results_compare_as_row_sets_ignoring_column_names_and_order():
    assert sc.result_key(ANSWER) == sc.result_key([{"count": 9.0, "full_name": "Alain Prost"}])
    assert sc.result_key([{"x": 866.25}]) == sc.result_key([{"y": Decimal("866.2500610351562")}])
    assert sc.result_key([{"a": 1}, {"a": 2}]) == sc.result_key([{"a": 2}, {"a": 1}, {"a": 2}])
    assert sc.result_key(ANSWER) != sc.result_key([{"driver": "Alain Prost", "n": 23295}])
    assert sc.result_key(ANSWER) != sc.result_key([{"id": 117, "driver": "Alain Prost", "n": 9}])   # an added column differs


def _run(results: dict[str, dict]):
    async def run(sql: str) -> dict:
        return results[sql]
    return run


def test_risk_counts_differing_failing_and_erroring_samples():
    replies = iter([SimpleNamespace(text="```sql\nA\n```", tokens_in=10, tokens_out=2),
                    SimpleNamespace(text="B", tokens_in=10, tokens_out=2),
                    SimpleNamespace(text="C", tokens_in=10, tokens_out=2),
                    RuntimeError("429"),
                    SimpleNamespace(text="A", tokens_in=10, tokens_out=2)])

    async def complete():
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r
    results = {"A": {"ok": True, "rows": [{"n": 9, "driver": "Alain Prost"}]},
               "B": {"ok": True, "rows": [{"driver": "Alain Prost", "n": 23295}]},
               "C": {"ok": False, "rows": []}}
    record = asyncio.run(sc.sample(complete, _run(results), lambda t: t.strip().removeprefix("```sql").removesuffix("```").strip(),
                                   ANSWER, k=5))
    assert record["k"] == 5 and record["agree"] == 2 and record["failed"] == 2 and record["risk"] == 0.6
    assert record["tokens_in"] == 40 and record["tokens_out"] == 8
    assert [x.get("same") for x in record["samples"]] == [True, False, None, None, True]


def test_the_flag_ships_off_and_the_field_is_last_write_wins():
    from shipped_flags import SHIPPED_DEFAULTS
    from state import _LAST_WRITE_WINS_FIELDS, StateSyncState
    assert SHIPPED_DEFAULTS[("pipeline", "self_consistency_k")] == "0"
    assert "self_consistency" in _LAST_WRITE_WINS_FIELDS and "self_consistency" in StateSyncState.model_fields
