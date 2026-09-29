"""
orchestrator/state.py
Full implementation of A2 — Typed global state, reducer machinery,
and the concurrency assertion.

One StateSyncState object per query. Every node reads it; every node's
return value is merged into it by the declared reducer. No side channels
between nodes.

The merge() function is the single entry point for state updates.
It enforces reducer semantics and raises AssertionError if a concurrent
write targets a last-write-wins field from more than one node — active
in all environments including production (A2 spec requirement).
"""

from __future__ import annotations

import threading
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Literal types (A2) ────────────────────────────────────────────────────────
Language      = Literal["en", "hi", "hinglish", "other"]
Route         = Literal["sql", "docs", "hybrid"]
ErrorCategory = Literal[
    "syntax", "unknown_column", "unknown_table", "ambiguous_column",
    "type_mismatch", "join_cardinality", "aggregation_misuse",
    "empty_result", "timeout", "permission_denied", "spatial_invalid",
    "vector_dim_mismatch", "unclassified",
]


# ── Sub-models ────────────────────────────────────────────────────────────────
class ErrorRecord(BaseModel):
    attempt:           int
    category:          ErrorCategory
    engine_code:       str | None      # e.g. Postgres SQLSTATE "42703"
    message:           str
    offending_sql:     str | None
    remediation_hint:  str             # from the taxonomy registry, not the LLM


class Provenance(BaseModel):
    document_id:  str
    chunk_id:     str
    page:         int | None
    score:        float
    # An uploaded document's title (Phase 3c slice 5); None for the sample corpus,
    # whose ids the web app already turns into labels.
    title:        str | None = None


class RetrievalResult(BaseModel):
    content:    str
    provenance: Provenance


class SpatialContext(BaseModel):
    digipin:  str | None = None
    lat:      float | None = None
    lon:      float | None = None
    radius_m: int | None = None


class Metrics(BaseModel):
    tokens_in:         int = 0
    tokens_out:        int = 0
    llm_calls:         int = 0
    trajectory_steps:  int = 0
    latency_ms:        int = 0


# ── Fields whose reducer is "last-write-wins" ─────────────────────────────────
# A concurrent write to any of these from >1 node is a defect (A2 spec).
_LAST_WRITE_WINS_FIELDS: frozenset[str] = frozenset({
    "detected_language",
    "normalized_query",
    "preserved_entities",
    "turn_index",
    "rewrite_applied",
    "context_guessed",
    "route",
    "subtasks",
    "schema_fingerprint",
    "memory_hit",
    "memory_trace_id",
    "candidate_sql",
    "executed_sql",
    "sql_rows",
    "sql_columns",
    "spatial",
    "attempt",      # last-write-wins semantically, but only one node increments
    "answer_text",
    "vega_spec",
    "flags_snapshot",
    "debug_candidate_sql",
    "allow_fast_path",
    "verified_example",
    "compiled_template",
})

# Fields whose reducer is "append" (list concatenation)
_APPEND_FIELDS: frozenset[str] = frozenset({
    "errors",
    "retrievals",
    "route_history",
    "interrogation_samples",
    "script_repairs",
    "caveats",
})

# Fields whose reducer is "field-wise sum"
_SUM_FIELDS: frozenset[str] = frozenset({
    "metrics",
})

# Fields that are never mutated after entry (identity fields)
_IMMUTABLE_FIELDS: frozenset[str] = frozenset({
    "query_id",
    "tenant_id",
    "principal_id",
    "session_id",
    "raw_query",
    "target_schema",
    "workspace_id",
})


# ── Main state object ─────────────────────────────────────────────────────────
class StateSyncState(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    # --- identity (set once at entry, never mutated) ---
    query_id:     str                      # uuid4
    tenant_id:    str                      # derived from authenticated principal (C1)
    principal_id: str                      # authenticated caller (C1); scopes conversation history
    session_id:   str
    # Chosen once at entry and must never change mid-query — a query that switched
    # corpus halfway would produce an answer whose provenance is unknowable.
    target_schema: str
    # The workspace the question was asked in (docs/V3_PLAN.md Phase 3), or None.
    # With one, the SQL agent adds the workspace's approved knowledge to its
    # prompt, and an exact match of an approved example runs its SQL directly.
    workspace_id: str | None = None
    # Titles of the READY uploaded documents this workspace searches (Phase 3c
    # slice 5), loaded at entry; the router sees them so a question about one
    # of them goes to the docs route.
    library_titles: list[str] = Field(default_factory=list)

    # --- input & normalization (Part D) ---
    raw_query:    str
    detected_language:   Language = "en"
    normalized_query:    str | None = None   # None until the gate runs
    preserved_entities:  dict[str, str] = Field(default_factory=dict)

    # --- multi-turn (cycle 007, Workstream A) ---
    # rewrite_node runs after normalize, before supervisor, and OVERWRITES
    # normalized_query with the standalone form of a follow-up (or leaves it
    # unchanged on turn 1 / a context-free question) -- it does not add a
    # second query field, so D1 Callout 1's structural guarantee ("Supervisor
    # reads normalized_query only, never raw_query") stays true unmodified.
    # These three fields are provenance ABOUT that rewrite, read by nobody
    # downstream in the graph -- only by main.py's history write and by
    # bench_multiturn.py's scoring.
    turn_index:       int = 1       # expected number; MCP assigns the stored one (V3_PLAN 0.1)
    rewrite_applied:  bool = False  # normalized_query changed from the pre-rewrite text
    context_guessed:  bool = False  # rewrite required resolving an ambiguous reference
                                     # with no clarification path available (A5 cut-line)

    # --- routing (Part B) ---
    route:         Route | None = None
    subtasks:      list[str] = Field(default_factory=list)
    route_history: list[str] = Field(default_factory=list)   # ACCUMULATE

    # --- structured path (Parts B, C) ---
    schema_fingerprint:      str | None = None
    memory_hit:              bool = False
    memory_trace_id:         str | None = None
    interrogation_samples:   list[dict] = Field(default_factory=list)  # ACCUMULATE (Part C2)
    # Pre-execution mechanical script/column repairs (B1) -- auditable from trace rather than only logs
    script_repairs:          list[str] = Field(default_factory=list)   # ACCUMULATE
    # Non-blocking warnings about a result that ran but may mislead (pipeline.answer_checks):
    # the synthesizer mentions them and the UI shows them under the answer.
    caveats:                 list[str] = Field(default_factory=list)   # ACCUMULATE
    candidate_sql:           str | None = None
    executed_sql:            str | None = None
    sql_rows:                list[dict] = Field(default_factory=list)
    sql_columns:             list[str] = Field(default_factory=list)

    # --- spatial (Part D) ---
    spatial: SpatialContext = Field(default_factory=SpatialContext)

    # --- unstructured path (Part E1) ---
    retrievals: list[RetrievalResult] = Field(default_factory=list)   # ACCUMULATE

    # --- correction loop (Part C2) ---
    errors:  list[ErrorRecord] = Field(default_factory=list)          # ACCUMULATE
    attempt: int = 0                                                   # INCREMENT

    # --- test harness only (C2-V1/V2): seeds the attempt-0 candidate SQL,
    # bypassing LLM generation, so the classifier+correction path is
    # deterministically testable. Never set by production request paths.
    # Consumed once by sql_agent_node at attempt 0; ignored on any retry.
    debug_candidate_sql: str | None = None

    # --- workspace fast path (Phase 3) ---
    # allow_fast_path is False for the setup test, which must measure the
    # pipeline rather than replay answers it approved earlier.
    allow_fast_path:  bool = True
    verified_example: bool = False   # the SQL is an approved example, run as-is
    compiled_template: bool = False  # the SQL is an approved example's template filled in code (Phase 3c)

    # --- output (Part E2) ---
    answer_text: str | None = None
    vega_spec:   dict | None = None

    # --- instrumentation (all Parts) ---
    metrics:        Metrics = Field(default_factory=Metrics)
    flags_snapshot: dict[str, Any] = Field(default_factory=dict)

    # --- concurrency tracking (not part of the public state schema) ---
    # Records which node last wrote each LWW field during a merge cycle.
    # Used by the concurrency assertion. Not serialized.
    _lww_writers: dict[str, str] = {}

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "_lww_writers", {})


# ── Merge / reducer machinery ─────────────────────────────────────────────────
_merge_lock = threading.Lock()


def _sum_metrics(base: Metrics, delta: Metrics) -> Metrics:
    """Field-wise sum of two Metrics objects (A2 reducer: SUM)."""
    return Metrics(
        tokens_in=base.tokens_in + delta.tokens_in,
        tokens_out=base.tokens_out + delta.tokens_out,
        llm_calls=base.llm_calls + delta.llm_calls,
        trajectory_steps=base.trajectory_steps + delta.trajectory_steps,
        latency_ms=base.latency_ms + delta.latency_ms,
    )


def merge(
    state: StateSyncState,
    update: dict[str, Any],
    *,
    node_name: str = "unknown",
) -> StateSyncState:
    """
    Apply a node's return value (update dict) into the current state.

    Reducer dispatch per A2:
    - IMMUTABLE fields: raises AssertionError if the update attempts to change them.
    - APPEND fields: append each new item; never truncate.
    - SUM fields (metrics): field-wise sum.
    - LWW fields: last write wins, but if a *different* node already wrote
      this field in the same concurrent dispatch cycle, raise AssertionError
      (A2 concurrency rule: "Enforce with an assertion in the merge path,
      active in all environments including production").

    Args:
        state:     The current StateSyncState (will be copied, not mutated).
        update:    Dict of field_name -> new_value from a node's return.
        node_name: The name of the node that produced the update (for
                   the concurrency assertion error message).

    Returns:
        A new StateSyncState with the update applied.
    """
    with _merge_lock:
        data = state.model_dump()
        lww_writers: dict[str, str] = dict(state._lww_writers)

        for field, value in update.items():
            if field.startswith("_"):
                continue  # skip internal fields

            if field in _IMMUTABLE_FIELDS:
                assert data[field] == value, (
                    f"Concurrency violation: node '{node_name}' attempted to mutate "
                    f"immutable field '{field}' (original={data[field]!r}, "
                    f"attempted={value!r})."
                )
                continue

            if field in _APPEND_FIELDS:
                if value is None:
                    continue
                if not isinstance(value, list):
                    value = [value]
                existing = data.get(field, []) or []
                data[field] = existing + value
                continue

            if field == "metrics":
                if value is None:
                    continue
                if isinstance(value, dict):
                    delta = Metrics(**value)
                elif isinstance(value, Metrics):
                    delta = value
                else:
                    raise TypeError(f"metrics must be a Metrics instance or dict, got {type(value)}")
                base = Metrics(**data["metrics"]) if isinstance(data["metrics"], dict) else data["metrics"]
                data["metrics"] = _sum_metrics(base, delta)
                continue

            if field in _LAST_WRITE_WINS_FIELDS:
                # Concurrency assertion: if a different node already wrote this
                # field in this merge cycle, that is a defect (A2).
                prior_writer = lww_writers.get(field)
                assert prior_writer is None or prior_writer == node_name, (
                    f"Concurrency violation: field '{field}' was written by "
                    f"node '{prior_writer}' and again by node '{node_name}' in the "
                    f"same concurrent dispatch. Last-write-wins fields may not be "
                    f"written by more than one concurrent node (A2)."
                )
                lww_writers[field] = node_name
                data[field] = value
                continue

            # Unknown field — treat as LWW but do not assert (future fields
            # added before this file is updated should not break silently).
            data[field] = value

        new_state = StateSyncState(**{k: v for k, v in data.items() if not k.startswith("_")})
        object.__setattr__(new_state, "_lww_writers", lww_writers)
        return new_state


def reset_lww_writers(state: StateSyncState) -> StateSyncState:
    """
    Call this at the start of each parallel dispatch batch to reset the
    per-cycle LWW writer tracking. Without this, a sequential node that
    writes the same field twice would incorrectly trigger the assertion.
    """
    data = state.model_dump()
    new_state = StateSyncState(**{k: v for k, v in data.items() if not k.startswith("_")})
    object.__setattr__(new_state, "_lww_writers", {})
    return new_state
