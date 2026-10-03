"""
orchestrator/self_consistency.py
Self-consistency, the study's model-based baseline (docs/research/evidence_tiers_study.md v2 §4).

Flag [pipeline] self_consistency_k = 0 (shipped: off). With k > 0, after the SQL agent's final
query succeeds, the SAME SQL prompt is sampled k more times; each sample is prepared as the answer's
SQL was (fences stripped, script repair), run with the same row cap, and its result compared with
the answer's. Risk = the share of samples whose result differs; a sample that fails to come back or
to run counts as differing.

Measurement only: the answer, its SQL and its rows never change. The samples' tokens are recorded in
this record, not added to the pipeline's metrics, so the pipeline's own cost stays comparable with
runs that had the flag off (the call recorder still lists every call).

"Same result" = the same SET of rows, each row taken as the sorted multiset of its values (column
names and order ignored), numbers to SIG_DIGITS significant digits. Stricter than the study's relaxed
label in one way: a sample that adds a column differs.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Awaitable, Callable

SIG_DIGITS = 5  # noqa: literals - as the study's v2 label comparison


def _value(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float, Decimal)):
        return float(f"{float(v):.{SIG_DIGITS}g}")
    if isinstance(v, str):
        try:
            return float(f"{float(v):.{SIG_DIGITS}g}")
        except ValueError:
            return v
    return str(v)


def result_key(rows: list[dict]) -> frozenset[tuple[str, ...]]:
    """A result as a set of rows, each row as its sorted values (see the module docstring)."""
    return frozenset(tuple(sorted(repr(_value(v)) for v in row.values())) for row in rows)


async def sample(complete: Callable[[], Awaitable[Any]], run: Callable[[str], Awaitable[dict]],
                 prepare: Callable[[str], str], answer_rows: list[dict], k: int) -> dict[str, Any]:
    """k samples of the SQL call (concurrently), each run and compared with the answer's rows."""
    replies = await asyncio.gather(*[complete() for _ in range(k)], return_exceptions=True)
    target = result_key(answer_rows)
    agree = failed = tokens_in = tokens_out = 0
    samples: list[dict[str, Any]] = []
    for reply in replies:
        if isinstance(reply, BaseException):
            failed += 1
            samples.append({"ok": False, "error": type(reply).__name__})
            continue
        tokens_in += int(getattr(reply, "tokens_in", 0) or 0)
        tokens_out += int(getattr(reply, "tokens_out", 0) or 0)
        sql = prepare(reply.text or "")
        try:
            result = await run(sql)
        except Exception as exc:  # noqa: BLE001 - a sample that cannot run differs; never fail the answer
            result = {"ok": False, "error": {"message": type(exc).__name__}}
        if not result.get("ok"):
            failed += 1
            samples.append({"sql": sql, "ok": False})
            continue
        same = result_key(result.get("rows") or []) == target
        agree += int(same)
        samples.append({"sql": sql, "ok": True, "same": same, "rows": len(result.get("rows") or [])})
    return {"k": k, "agree": agree, "failed": failed, "risk": round(1 - agree / k, 4) if k else None,
            "tokens_in": tokens_in, "tokens_out": tokens_out, "samples": samples}
