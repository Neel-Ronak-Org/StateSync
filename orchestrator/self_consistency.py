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

"Same result" = the study's relaxed rule, applied both ways: some choice of distinct columns of the
wider result, one per column of the narrower, gives the same SET of rows; column names, order and
extra columns are ignored, numbers compared to SIG_DIGITS significant digits. (A first version
compared whole rows; on a dev question it called five samples that returned the answer's 58 schools
with other identifier columns "different", protocol v2 §10.)
"""

from __future__ import annotations

import asyncio
import itertools
from decimal import Decimal
from typing import Any, Awaitable, Callable

SIG_DIGITS = 5  # noqa: literals - as the study's v2 label comparison
MAX_COLUMN_CHOICES = 10000  # noqa: literals - bound on the column matching; beyond it, compare whole rows


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


def _columns(rows: list[dict]) -> list[tuple[Any, ...]]:
    keys = list(rows[0].keys())
    return [tuple(_value(r.get(k)) for r in rows) for k in keys]


def same_result(a: list[dict], b: list[dict]) -> bool:
    """The same answer under the relaxed rule, either way round (see the module docstring)."""
    if not a or not b:
        return not a and not b
    ca, cb = _columns(a), _columns(b)
    narrow, wide = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    target = set(zip(*narrow))
    candidates = [[j for j, w in enumerate(wide) if set(w) == set(n)] for n in narrow]
    if any(not c for c in candidates):
        return False
    if _choices(candidates) > MAX_COLUMN_CHOICES:
        return set(zip(*narrow)) == set(zip(*wide))
    for choice in itertools.product(*candidates):
        if len(set(choice)) == len(choice) and set(zip(*(wide[j] for j in choice))) == target:
            return True
    return False


def _choices(candidates: list[list[int]]) -> int:
    size = 1
    for c in candidates:
        size *= len(c)
    return size


async def sample(complete: Callable[[], Awaitable[Any]], run: Callable[[str], Awaitable[dict]],
                 prepare: Callable[[str], str], answer_rows: list[dict], k: int) -> dict[str, Any]:
    """k samples of the SQL call (concurrently), each run and compared with the answer's rows."""
    replies = await asyncio.gather(*[complete() for _ in range(k)], return_exceptions=True)
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
        same = same_result(result.get("rows") or [], answer_rows)
        agree += int(same)
        samples.append({"sql": sql, "ok": True, "same": same, "rows": len(result.get("rows") or [])})
    return {"k": k, "agree": agree, "failed": failed, "risk": round(1 - agree / k, 4) if k else None,
            "tokens_in": tokens_in, "tokens_out": tokens_out, "samples": samples}
