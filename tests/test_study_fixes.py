"""
tests/test_study_fixes.py
The two dev fixes of the evidence-tiers study, protocol v2 (docs/research/evidence_tiers_study.md §4).

WHY THIS EXISTS. Both were found on the BIRD pilot (docs/eval/bird_pilot_report.md) and ship off:
- F1, [pipeline] sql_row_cap: the SQL prompt's "Apply LIMIT 1000" replaced the LIMIT 1 of "which
  is the highest" questions. "executor" drops that instruction (data.run_select caps rows anyway);
  "prompt" must leave both SQL prompts exactly as shipped.
- F3, [pipeline] grounding_version: every pilot grounding flag came from the pipeline's own
  wording. v2 traces the rows the answer writer was shown ("the first 50", "remaining 610" of
  660) and skips "(1) (2)" enumerators; v1 must behave exactly as before, and v2 must still flag
  a figure that is genuinely not in the result.
The texts below are the pilot's own answers (questions 145, 327, 28), shortened.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import number_grounding as g  # noqa: E402
import repair_loop as rl  # noqa: E402
from nodes import sql_agent  # noqa: E402

OWN = "cn_0123456789abcdef"
ROWS_660 = [{"client_id": 1000 + i} for i in range(660)]
FOUR = [{"n": 7}, {"n": 9}, {"n": 11}, {"n": 13}]   # count 4, sum 40, mean 10: nothing near 1, 2, 3 or 5


# ── F1: the row-cap rule ──────────────────────────────────────────────────────

def test_prompt_mode_leaves_both_sql_prompts_as_shipped():
    for template in (sql_agent._SYSTEM_PROMPT, sql_agent._CORRECTION_SYSTEM_PROMPT):
        for flags in ({}, {"pipeline.sql_row_cap": "prompt"}):
            assert sql_agent.sql_system_prompt(template, 1000, "public", flags) == template.format(max_rows=1000)


def test_executor_mode_drops_the_limit_instruction_in_both_prompts():
    for template in (sql_agent._SYSTEM_PROMPT, sql_agent._CORRECTION_SYSTEM_PROMPT):
        assert sql_agent._PROMPT_LIMIT_RULE in template
        prompt = sql_agent.sql_system_prompt(template, 1000, "public", {"pipeline.sql_row_cap": "executor"})
        assert "LIMIT 1000" not in prompt and sql_agent._EXECUTOR_LIMIT_RULE in prompt


def test_executor_mode_keeps_the_dataset_framing():
    prompt = sql_agent.sql_system_prompt(sql_agent._SYSTEM_PROMPT, 1000, OWN, {"pipeline.sql_row_cap": "executor"})
    assert "ONDC" not in prompt and "owner's own PostgreSQL database" in prompt


# ── F3: grounding v2 ──────────────────────────────────────────────────────────

def test_v1_flags_the_pipelines_own_wording_as_before():
    text = "The query returned 660 client_ids. The first 50 are listed; remaining 610 client_id values not listed."
    assert g.ground(text, ROWS_660, ["client_id"])["unmatched"] == ["50", "610"]
    assert g.ground("You need: (1) Enrollment, (2) Ages 5-17, and (3) funding type.", FOUR, ["n"])["unmatched"] == ["1", "2", "3"]


def test_v2_traces_the_rows_shown_and_their_remainder():
    text = "The query returned 660 client_ids. The first 50 are listed; remaining 610 client_id values not listed."
    result = g.ground(text, ROWS_660, ["client_id"], version=2, shown_rows=50)
    assert result["unmatched"] == []
    assert [f["source"]["kind"] for f in result["figures"]] == ["aggregate", "shown", "derived"]


def test_v2_traces_a_remainder_even_when_the_shown_count_is_unsaid():
    text = "(Total rows returned: 660; remaining 610 client_id values not listed here.)"
    assert g.ground(text, ROWS_660, ["client_id"])["unmatched"] == ["610"]
    assert g.ground(text, ROWS_660, ["client_id"], version=2, shown_rows=50)["unmatched"] == []


def test_v2_shown_count_only_applies_when_rows_were_cut():
    # 40 rows, all shown: a "50" in the text is not something the writer was told.
    rows = [{"x": i} for i in range(40)]
    assert g.ground("Showing 50 rows.", rows, ["x"], version=2, shown_rows=40)["unmatched"] == ["50"]


def test_v2_skips_enumerators_only_in_a_sequence():
    assert g.ground("You need: (1) Enrollment, (2) Ages 5-17, and (3) funding type.", FOUR, ["n"],
                    version=2)["unmatched"] == []
    # A lone parenthesised number is still a figure.
    assert g.ground("Schools with magnets (5) were counted.", FOUR, ["n"], version=2)["unmatched"] == ["5"]


def test_v2_still_flags_a_figure_that_is_not_in_the_result():
    text = "The first 50 are listed. The total enrollment is 12,350."
    assert g.ground(text, ROWS_660, ["client_id"], version=2, shown_rows=50)["unmatched"] == ["12,350"]


def test_grounding_options_follow_the_flag_and_the_synth_style():
    assert rl.grounding_options({}, 660) == {}
    assert rl.grounding_options({"pipeline.grounding_version": "v1"}, 660) == {}
    full = rl.grounding_options({"pipeline.grounding_version": "v2"}, 660)
    assert full == {"version": 2, "shown_rows": int(rl.cfg.sql.synthesizer_preview_rows)}
    short = rl.grounding_options({"pipeline.grounding_version": "v2", "pipeline.synth_style": "short"}, 660)
    assert short == {"version": 2, "shown_rows": int(rl.cfg.pipeline.synth_short_preview_rows)}
    assert rl.grounding_options({"pipeline.grounding_version": "v2"}, 3) == {"version": 2, "shown_rows": 3}
