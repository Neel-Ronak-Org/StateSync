"""
orchestrator/state.py
The state of one question as it moves through the graph: one typed object, read by every step.

Each step returns only the fields it changed, and merge() applies them with one rule per field:
  - identity fields (who asked, which workspace, the question) never change after the start;
  - lists that build up (errors, passages, samples, caveats) are appended to;
  - metrics (tokens, model calls, time) are added up;
  - every other field takes the latest value.
Nothing passes between steps except through this object.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Route = Literal["sql", "docs", "hybrid"]
ErrorCategory = Literal[
    "syntax", "unknown_column", "unknown_table", "ambiguous_column",
    "type_mismatch", "join_cardinality", "aggregation_misuse",
    "empty_result", "timeout", "permission_denied", "spatial_invalid",
    "vector_dim_mismatch", "no_data_source", "not_answerable_unconfirmed", "unclassified",
]


class ErrorRecord(BaseModel):
    """One failed SQL attempt, classified by code (classifiers/error_classifier.py), never by the model."""
    attempt:          int
    category:         ErrorCategory
    engine_code:      str | None      # the Postgres SQLSTATE, e.g. "42703"
    message:          str
    offending_sql:    str | None
    remediation_hint: str             # from config/error_taxonomy.json


class Provenance(BaseModel):
    """Where a passage comes from, for its citation."""
    document_id: str
    chunk_id:    str
    page:        int | None
    score:       float                # cosine similarity to the search text
    title:       str | None = None
    section:     str | None = None
    jev_score:   float | None = None  # Jev's P(the passage contains what the question needs)


class RetrievalResult(BaseModel):
    content:    str
    provenance: Provenance


class Metrics(BaseModel):
    tokens_in:        int = 0
    tokens_out:       int = 0
    llm_calls:        int = 0
    trajectory_steps: int = 0         # MCP tool calls
    latency_ms:       int = 0


_IMMUTABLE_FIELDS = frozenset({"query_id", "tenant_id", "principal_id", "session_id", "raw_query",
                               "target_schema", "workspace_id"})
_APPEND_FIELDS = frozenset({"errors", "retrievals", "interrogation_samples", "caveats"})


class StateSyncState(BaseModel):
    # --- who asked what, where (set at the start, never changed) ---
    query_id:      str
    tenant_id:     str
    principal_id:  str
    session_id:    str
    raw_query:     str
    target_schema: str                 # the workspace's database (cn_<16 hex>)
    workspace_id:  str | None = None
    library_titles: list[str] = Field(default_factory=list)   # the workspace's ready documents

    # --- the router (front_door.py) ---
    normalized_query: str | None = None   # the question as answered (the LLM fallback may rewrite a follow-up)
    turn_index:       int = 1
    rewrite_applied:  bool = False
    route:            Route | None = None
    subtasks:         list[str] = Field(default_factory=list)   # a two-source question: [database part, documents part]
    out_of_scope:     bool = False        # "neither": the writer replies with a fixed message
    docs_depend_on_sql: bool = False      # the documents part needs the database's answer first
    front_door:       dict | None = None  # the router's trace
    conversation_context: str | None = None   # a follow-up's context line (context_carry.py)
    carried:          dict | None = None      # {company, year, reason} carried from the previous turn

    # --- the SQL agent ---
    candidate_sql:         str | None = None
    executed_sql:          str | None = None
    sql_rows:              list[dict] = Field(default_factory=list)
    sql_columns:           list[str] = Field(default_factory=list)
    interrogation_samples: list[dict] = Field(default_factory=list)
    caveats:               list[str] = Field(default_factory=list)
    errors:                list[ErrorRecord] = Field(default_factory=list)
    attempt:               int = 0
    verified_example:      bool = False   # the SQL is an approved example, run as is

    # --- the documents engine ---
    retrievals:      list[RetrievalResult] = Field(default_factory=list)
    retrieval_trace: dict | None = None   # the search stage by stage, for the Pipeline Inspector
    hybrid_entities: dict | None = None   # the narrowing step's trace, when it ran
    recovered_from:  str | None = None    # "sql" when route recovery ran the documents route instead

    # --- the answer ---
    answer_text: str | None = None
    vega_spec:   dict | None = None

    metrics:        Metrics = Field(default_factory=Metrics)
    flags_snapshot: dict[str, Any] = Field(default_factory=dict)


def merge(state: StateSyncState, update: dict[str, Any], *, node_name: str = "unknown") -> StateSyncState:
    """The state after one step's update (see the module docstring for the rule per field)."""
    data = state.model_dump()
    for field, value in update.items():
        if field in _IMMUTABLE_FIELDS:
            assert data[field] == value, f"step '{node_name}' tried to change '{field}'"
        elif field in _APPEND_FIELDS:
            if value is not None:
                data[field] = (data.get(field) or []) + (value if isinstance(value, list) else [value])
        elif field == "metrics":
            if value is not None:
                delta = value if isinstance(value, dict) else value.model_dump()
                data["metrics"] = {k: data["metrics"].get(k, 0) + int(delta.get(k, 0)) for k in Metrics.model_fields}
        else:
            data[field] = value
    return StateSyncState(**data)
