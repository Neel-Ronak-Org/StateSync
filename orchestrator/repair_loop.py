"""
orchestrator/repair_loop.py
The flag-and-repair loop (docs/research/evidence_tiers_study.md, RQ4/H4; owner, 2026-10-03).
Flag [pipeline] repair_loop = "off" (shipped) | "evidence" | "plain".

After an answer is written, figure grounding (number_grounding.py) checks that every figure
in it traces to the result. When some do not:
  1. ONE repair: the synthesizer writes the answer again, told what failed. With "evidence"
     the note names the untraced figures; with "plain" it only says "check your answer"
     (the study's control: self-checking without new information).
  2. The checks run again. Passing → the repaired answer is used, marked as corrected.
     Still flagged → the ORIGINAL answer is kept with its "might be wrong" caveat. Never a
     second repair: a loop that retries until something passes learns to hide problems.
The SQL-side repair already exists: a join fan-out found by answer_checks triggers one
corrected retry (sql_agent.py); with "plain" its message is replaced by a generic one.
Every outcome is recorded in the answer's trace ("repair"), so the study can count fixed,
broken and unchanged answers.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from number_grounding import ground

VARIANTS = frozenset({"off", "evidence", "plain"})
PLAIN_NOTE = ("Check your previous answer against the data context above and write it again. "
              "Keep it if it is right.")
PLAIN_SQL_MESSAGE = "The result of this query may be wrong. Check the SQL and correct it if needed."


def evidence_note(unmatched: list[str]) -> str:
    listed = ", ".join(unmatched)
    return (f"A check found figures in your previous answer that do not appear in the data context: "
            f"{listed}. Write the answer again using only figures that appear in the data context "
            "(or that the question itself states). If a figure must be computed, show the inputs.")


def note_for(variant: str, unmatched: list[str]) -> str:
    return evidence_note(unmatched) if variant == "evidence" else PLAIN_NOTE


async def repair(state: dict[str, Any], variant: str,
                 synthesize: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(update for the final state or None, trace record or None). No-op unless the variant is
    on and grounding finds untraced figures in a database answer."""
    if variant not in ("evidence", "plain") or not state.get("answer_text") or not state.get("sql_columns"):
        return None, None
    question = state.get("normalized_query") or state.get("raw_query", "")
    passages = [r.get("content", "") for r in state.get("retrievals") or [] if isinstance(r, dict)]
    before = ground(state["answer_text"], state.get("sql_rows") or [], state.get("sql_columns") or [], question, passages)
    if not before["unmatched"]:
        return None, None
    record: dict[str, Any] = {"trigger": "figure_grounding", "variant": variant, "unmatched_before": before["unmatched"],
                              "previous_answer": state["answer_text"]}
    out = await synthesize({**state, "repair_note": note_for(variant, before["unmatched"]),
                            "previous_answer": state["answer_text"]})
    new_text = (out or {}).get("answer_text") or ""
    after = ground(new_text, state.get("sql_rows") or [], state.get("sql_columns") or [], question, passages) if new_text else None
    if after is not None and not after["unmatched"]:
        record.update(outcome="fixed", unmatched_after=[])
        update = {"answer_text": new_text}
        if out.get("vega_spec") is not None:
            update["vega_spec"] = out["vega_spec"]
        return update, record
    record.update(outcome="still_flagged", unmatched_after=(after or {}).get("unmatched"), repaired_answer=new_text)
    return None, record
