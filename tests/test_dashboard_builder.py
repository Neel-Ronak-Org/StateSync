"""
tests/test_dashboard_builder.py
Pins the rules of "Build with AI" (orchestrator/dashboard_builder.py,
docs/V3_PLAN.md Phase 2b, D-v3-9).

WHY THIS EXISTS. The planner is a model and its reply is untrusted: parse_plan
must keep only well-formed insights with a known visualization, and fail the
build (not half-build it) when too few survive. After that, a build must
never lose track of an item: every planned insight ends added or skipped
with a reason, one failure does not stop the rest, skipped items leave no
hole in the grid, and every question is asked in a session of its own. The
live behaviour (a real plan, real answers) is checked by
scripts/verify_dashboards.py --build.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import dashboard_builder as db  # noqa: E402


def plan_json(items: list[dict]) -> str:
    return json.dumps({"insights": items})


GOOD = [
    {"title": "Revenue, last hour", "question": "What was revenue in the last hour and the hour before?", "viz": "kpi"},
    {"title": "Orders, last hour", "question": "How many orders in the last hour and the hour before?", "viz": "kpi"},
    {"title": "Conversion rate today", "question": "What is the conversion rate today?", "viz": "kpi"},
    {"title": "Orders per hour", "question": "Orders per hour over the last 24 hours?", "viz": "area"},
    {"title": "Revenue by category", "question": "Revenue by category over the last 7 days?", "viz": "bar"},
]


def parse(text: str):
    return db.parse_plan(text, min_items=4, max_items=8, title_max=120, question_max=300)


# ── parse_plan ────────────────────────────────────────────────────────────────

def test_a_good_plan_parses_in_order():
    plan = parse(plan_json(GOOD))
    assert [p.viz for p in plan] == ["kpi", "kpi", "kpi", "area", "bar"]
    assert plan[0].title == "Revenue, last hour"


def test_code_fences_and_trailing_periods_are_tolerated():
    items = [dict(GOOD[0], title="Revenue, last hour.")] + GOOD[1:]
    plan = parse("```json\n" + plan_json(items) + "\n```")
    assert plan[0].title == "Revenue, last hour"


def test_off_contract_items_are_dropped_one_by_one():
    items = GOOD + [
        {"title": "Map", "question": "Orders on a map?", "viz": "geoshape"},       # unknown viz
        {"title": "", "question": "Blank title?", "viz": "kpi"},
        {"title": "No question", "viz": "kpi"},
        {"title": "Dup", "question": GOOD[0]["question"].upper(), "viz": "kpi"},  # duplicate question
        {"title": "x" * 121, "question": "Too long a title?", "viz": "kpi"},
        "not an object",
    ]
    assert len(parse(plan_json(items))) == len(GOOD)


def test_the_plan_is_capped_at_max_items():
    items = [{"title": f"T{i}", "question": f"Question {i}?", "viz": "kpi"} for i in range(12)]
    assert len(parse(plan_json(items))) == 8


def test_too_few_usable_insights_fail_the_plan():
    assert isinstance(parse(plan_json(GOOD[:3])), str)
    assert isinstance(parse("I think you should look at revenue."), str)
    assert isinstance(parse(json.dumps({"ideas": GOOD})), str)


# ── summarize_schema ──────────────────────────────────────────────────────────

def test_the_summary_lists_tables_then_columns_and_cuts_on_whole_lines():
    tables = [{"name": "orders", "row_estimate": 150000, "comment": "One row per order."}]
    columns = {"orders": [{"name": "amount", "type": "numeric", "comment": "Order value in INR."},
                          {"name": "status", "type": "text", "comment": None}]}
    summary = db.summarize_schema("live", tables, columns, max_chars=10_000)
    assert summary.splitlines() == [
        "Dataset: live",
        "TABLE live.orders (~150000 rows): One row per order.",
        "  - amount numeric: Order value in INR.",
        "  - status text",
    ]
    cut = db.summarize_schema("live", tables, columns, max_chars=70)
    assert cut.splitlines() == ["Dataset: live", "TABLE live.orders (~150000 rows): One row per order."]


# ── GridPacker ────────────────────────────────────────────────────────────────

def test_kpis_share_a_row_and_charts_wrap_below():
    packer = db.GridPacker(columns=12)
    slots = [packer.place(v) for v in ("kpi", "kpi", "kpi", "kpi", "area", "bar", "donut")]
    assert slots == [
        {"x": 0, "y": 0, "w": 3, "h": 4}, {"x": 3, "y": 0, "w": 3, "h": 4},
        {"x": 6, "y": 0, "w": 3, "h": 4}, {"x": 9, "y": 0, "w": 3, "h": 4},
        {"x": 0, "y": 4, "w": 6, "h": 8}, {"x": 6, "y": 4, "w": 6, "h": 8},
        {"x": 0, "y": 12, "w": 4, "h": 8},
    ]


# ── skip_reason ───────────────────────────────────────────────────────────────

def answer(**trace) -> dict:
    base = {"route": "sql", "executed_sql": "SELECT 1", "sql_rows_count": 3, "turn_index": 1}
    base.update(trace)
    return {"state_trace": base}


def test_skip_reasons_are_specific():
    assert db.skip_reason(answer()) is None
    assert db.skip_reason(answer(route="docs")) == "Answered from documents, not the database."
    assert db.skip_reason(answer(executed_sql=None)) == "No database query could be run for it."
    assert db.skip_reason(answer(sql_rows_count=0)) == "The query returned no rows."
    assert db.skip_reason(answer(turn_index=None)) == "The answer could not be saved."
    assert db.skip_reason(None) == "The question could not be answered."


# ── run_build ─────────────────────────────────────────────────────────────────

class HTTPishError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def run(plan_result, answers: dict, pin_fail: set[str] | None = None):
    """Run a build with fakes. `answers` maps question -> response dict or an exception."""
    job = db.new_job("p_a", "dash-1", "live", "sales")
    asked, pinned = [], []

    async def plan(goal):
        assert goal == "sales"
        return plan_result

    async def ask(question, session_id):
        asked.append(session_id)
        result = answers[question]
        if isinstance(result, Exception):
            raise result
        return result

    async def pin(session_id, turn_index, insight, slot):
        pinned.append((insight.title, slot))
        if pin_fail and insight.title in pin_fail:
            return {"ok": False, "error": "limit_reached", "message": "That dashboard already has 24 tiles."}
        return {"ok": True, "tile": {"tile_id": f"tile-{len(pinned)}"}}

    asyncio.run(db.run_build(job, plan, ask, pin, columns=12))
    return job, asked, pinned


def test_a_failed_plan_fails_the_build_with_the_reason():
    job, asked, _ = run("The planner proposed only 2 usable insights.", {})
    assert job.state == "failed" and job.error == "The planner proposed only 2 usable insights."
    assert asked == [] and job.finished_at


def test_every_item_ends_added_or_skipped_and_one_failure_does_not_stop_the_rest():
    plan = [db.PlannedInsight(**{k: g[k] for k in ("title", "question", "viz")}) for g in GOOD]
    answers = {
        GOOD[0]["question"]: answer(),
        GOOD[1]["question"]: HTTPishError("The model provider is rate-limiting us. Try again in a minute."),
        GOOD[2]["question"]: answer(route="docs"),
        GOOD[3]["question"]: answer(),
        GOOD[4]["question"]: answer(sql_rows_count=0),
    }
    job, asked, pinned = run(plan, answers)
    assert job.state == "done" and job.finished_at
    assert [i.state for i in job.items] == ["added", "skipped", "skipped", "added", "skipped"]
    assert job.items[1].reason == "The model provider is rate-limiting us. Try again in a minute."
    assert job.items[2].reason == "Answered from documents, not the database."
    assert job.items[0].tile_id == "tile-1"
    # Skipped items leave no hole: the area chart goes right after the one KPI.
    assert pinned == [("Revenue, last hour", {"x": 0, "y": 0, "w": 3, "h": 4}),
                      ("Orders per hour", {"x": 3, "y": 0, "w": 6, "h": 8})]


def test_each_question_gets_its_own_build_session():
    plan = [db.PlannedInsight(**{k: g[k] for k in ("title", "question", "viz")}) for g in GOOD]
    _, asked, _ = run(plan, {g["question"]: answer() for g in GOOD})
    assert len(set(asked)) == len(GOOD)
    assert all(s.startswith(db.BUILD_SESSION_PREFIX) for s in asked)


def test_a_refused_pin_is_skipped_with_the_servers_reason_and_frees_its_slot():
    plan = [db.PlannedInsight(**{k: g[k] for k in ("title", "question", "viz")}) for g in GOOD[:4]]
    job, _, pinned = run(plan, {g["question"]: answer() for g in GOOD[:4]}, pin_fail={"Orders, last hour"})
    assert [i.state for i in job.items] == ["added", "skipped", "added", "added"]
    assert job.items[1].reason == "That dashboard already has 24 tiles."
    assert [slot["x"] for _, slot in pinned] == [0, 3, 3, 6]


def test_the_view_is_what_the_web_app_reads():
    job, _, _ = run([db.PlannedInsight("A", "Q?", "kpi")] * 1, {"Q?": answer()})
    view = job.view()
    assert set(view) == {"job_id", "dashboard_id", "state", "goal", "items", "error", "started_at", "finished_at"}
    assert set(view["items"][0]) == {"title", "question", "viz", "state", "reason", "tile_id"}


# ── BuildRegistry ─────────────────────────────────────────────────────────────

def test_jobs_are_per_principal():
    async def scenario():
        registry = db.BuildRegistry()
        gate = asyncio.Event()

        async def work(job):
            await gate.wait()
            job.state = "done"

        a = registry.start(db.new_job("p_a", "d1", "live", None), work)
        assert registry.active("p_a") is a and registry.active("p_b") is None
        assert registry.latest("p_b", "d1") is None           # another principal never sees it
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert registry.active("p_a") is None and registry.latest("p_a", "d1") is a

    asyncio.run(scenario())


# ── Workspace knowledge (2026-10-02: Build with AI ignored it) ────────────────
# A build used neither the owner's definitions nor their approved examples, so a drafted dashboard
# could compute "revenue" differently from the chat. pick_workspace decides which workspace's
# knowledge a build uses; a wrong-dataset workspace would mislead both the planner and the SQL agent.

def _w(wid, schema):
    return {"workspace_id": wid, "target_schema": schema}


def test_a_requested_workspace_must_be_the_owners_and_on_the_dataset():
    owned = [_w("w1", "public"), _w("w2", "olist")]
    assert db.pick_workspace(owned, "public", "w1") == (owned[0], None)
    assert db.pick_workspace(owned, "public", "w2") == (None, "mismatch")
    assert db.pick_workspace(owned, "public", "someone_elses") == (None, "not_found")


def test_without_a_request_only_an_unambiguous_workspace_is_used():
    assert db.pick_workspace([_w("w1", "public"), _w("w2", "olist")], "public", None) == (_w("w1", "public"), None)
    assert db.pick_workspace([_w("w1", "public"), _w("w3", "public")], "public", None) == (None, None)
    assert db.pick_workspace([_w("w2", "olist")], "public", None) == (None, None)


def test_the_planner_knowledge_section_is_labelled_and_empty_without_knowledge():
    assert db.planner_knowledge("") == ""
    assert db.planner_knowledge("Business definitions (approved by the owner):\n  revenue: x").startswith(db.KNOWLEDGE_HEADER)
    # The prompt still formats (the new rule added no stray braces).
    db.PLANNER_SYSTEM_PROMPT.format(min_items=1, max_items=2, time_rule="r")
