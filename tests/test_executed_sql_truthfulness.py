"""
tests/test_executed_sql_truthfulness.py
Pins the truthful reporting of executed_sql and exposure of candidate_sql.

WHY THIS EXISTS:
`executed_sql` previously named a statement that never ran, which is a misreport
in the field a measurement harness reads. When the Layer-1 gate rejected a
statement (e.g. constant projections or table-less queries), the failure branch
in sql_agent.py still assigned `update["executed_sql"] = candidate_sql`. This
caused measurement harnesses—such as the adversarial credential detector—to
report false positives because the fabricated payload echoed in executed_sql
even though it was never executed by the database.

Simultaneously, `candidate_sql` was tracked on StateSyncState and set in the
update dict on every path, but orchestrator/main.py's `state_trace` never
emitted it. Downstream consumers and benchmarks (e.g. scripts/bench_multilingual.py)
relying on candidate_sql as a fallback could never inspect the rejected SQL.

These tests verify structural guarantees without database connectivity:
1. orchestrator/main.py's state_trace contains BOTH "executed_sql" and "candidate_sql".
2. orchestrator/nodes/sql_agent.py contains exactly TWO assignments of
   update["executed_sql"] = candidate_sql (down from three).
3. The failure branch that sets update["sql_rows"] = [] and update["sql_columns"] = []
   and records engine_code does NOT set executed_sql.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = REPO_ROOT / "orchestrator" / "main.py"
SQL_AGENT_PY = REPO_ROOT / "orchestrator" / "nodes" / "sql_agent.py"


def _extract_state_trace_keys(tree: ast.AST) -> list[str]:
    """Extract dictionary keys assigned to state_trace in main.py."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "state_trace":
                    if isinstance(node.value, ast.Dict):
                        return [
                            k.value
                            for k in node.value.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)
                        ]
    return []


def test_main_state_trace_surfaces_executed_and_candidate_sql():
    """orchestrator/main.py's state_trace must contain BOTH 'executed_sql' and 'candidate_sql' keys."""
    assert MAIN_PY.exists(), f"Source file missing: {MAIN_PY}"
    content = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(MAIN_PY))

    keys = _extract_state_trace_keys(tree)
    assert keys, "Could not locate 'state_trace = {...}' dictionary assignment in orchestrator/main.py"

    assert "executed_sql" in keys, (
        "orchestrator/main.py state_trace missing 'executed_sql' key; "
        "callers cannot inspect executed statements"
    )
    assert "candidate_sql" in keys, (
        "orchestrator/main.py state_trace missing 'candidate_sql' key; "
        "when executed_sql is null on rejection, candidate_sql must be surfaced"
    )

    idx_exec = keys.index("executed_sql")
    idx_cand = keys.index("candidate_sql")
    assert idx_cand == idx_exec + 1, (
        f"'candidate_sql' should immediately follow 'executed_sql' in state_trace, "
        f"got indices executed_sql={idx_exec}, candidate_sql={idx_cand}"
    )


def test_sql_agent_has_exactly_two_executed_sql_assignments():
    """orchestrator/nodes/sql_agent.py must contain exactly TWO update['executed_sql'] = candidate_sql assignments."""
    assert SQL_AGENT_PY.exists(), f"Source file missing: {SQL_AGENT_PY}"
    content = SQL_AGENT_PY.read_text(encoding="utf-8")

    exact_assignment = 'update["executed_sql"] = candidate_sql'
    count = content.count(exact_assignment)
    assert count == 2, (
        f"orchestrator/nodes/sql_agent.py must contain exactly TWO assignments of "
        f"'{exact_assignment}' (down from three; the failure branch must not assign it), "
        f"but found {count}."
    )

    tree = ast.parse(content, filename=str(SQL_AGENT_PY))
    ast_assignment_lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "update"
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "executed_sql"
                ):
                    if isinstance(node.value, ast.Name) and node.value.id == "candidate_sql":
                        ast_assignment_lines.append(node.lineno)

    assert len(ast_assignment_lines) == 2, (
        f"AST inspection found {len(ast_assignment_lines)} assignments to update['executed_sql'] = candidate_sql "
        f"(expected 2), at lines: {ast_assignment_lines}"
    )


def test_failure_branch_does_not_set_executed_sql():
    """
    The failure branch that sets update['sql_rows'] = [] together with
    update['sql_columns'] = [] and appends an error with 'engine_code'
    must NOT set executed_sql.
    """
    assert SQL_AGENT_PY.exists(), f"Source file missing: {SQL_AGENT_PY}"
    content = SQL_AGENT_PY.read_text(encoding="utf-8")

    match = re.search(
        r"(else:\s+error_obj = exec_result\.get\(\"error\"\).*?)(update\[\"metrics\"\])",
        content,
        re.DOTALL,
    )
    assert match is not None, (
        "Could not locate the not-ok failure branch (else: ... error_obj = exec_result.get('error')) "
        "in orchestrator/nodes/sql_agent.py"
    )
    failure_block = match.group(1)

    assert 'update["sql_rows"] = []' in failure_block, (
        "Failure branch does not set update['sql_rows'] = [] as expected"
    )
    assert 'update["sql_columns"] = []' in failure_block, (
        "Failure branch does not set update['sql_columns'] = [] as expected"
    )
    assert '"engine_code": sqlstate' in failure_block, (
        "Failure branch does not record error with engine_code as expected"
    )

    assert 'update["executed_sql"]' not in failure_block and "update['executed_sql']" not in failure_block, (
        "Regressed: the failure branch in orchestrator/nodes/sql_agent.py sets executed_sql. "
        "When a query fails or is rejected before execution, executed_sql must remain unset (None)."
    )


def test_remaining_assignments_are_executed_branches():
    """
    Confirm the two remaining assignments are strictly in the executed branches:
    1) ok and (rows or repeat_empty_accepted)
    2) ok and not rows
    """
    content = SQL_AGENT_PY.read_text(encoding="utf-8")

    # Match the if/elif structure in step 5 of sql_agent.py
    exec_branch = re.search(
        r"if ok and \(rows or repeat_empty_accepted\):.*?(?=elif ok and not rows:)",
        content,
        re.DOTALL,
    )
    assert exec_branch is not None, "Could not find 'if ok and (rows or repeat_empty_accepted):' branch"
    assert 'update["executed_sql"] = candidate_sql' in exec_branch.group(0), (
        "The 'ok and (rows or repeat_empty_accepted)' branch must set executed_sql"
    )

    empty_branch = re.search(
        r"elif ok and not rows:.*?(?=else:)",
        content,
        re.DOTALL,
    )
    assert empty_branch is not None, "Could not find 'elif ok and not rows:' branch"
    assert 'update["executed_sql"] = candidate_sql' in empty_branch.group(0), (
        "The 'ok and not rows' branch must set executed_sql (query executed and returned 0 rows)"
    )
