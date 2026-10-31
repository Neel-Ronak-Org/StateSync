"""
orchestrator/graph.py
The pipeline as a LangGraph state machine: which step runs after which.

  entry ──► router (front_door.py)
              │
              ├─ sql ────► SQL agent ──┬─ success ─────────────────────────► answer writer
              │              ▲   │     ├─ retriable error ─► SQL agent (again, up to sql.retry_max)
              │              └───┘     └─ final error ─► error exit ─┬─────► answer writer
              │                                                     └─ the database lacks the data and the
              │                                                        workspace has documents:
              │                                                        documents route, once ──► answer writer
              ├─ docs ───► documents (nodes/documents.py) ────────────────────► answer writer
              ├─ hybrid ─► split the question ─► SQL agent (same loop) ─► documents part ─► answer writer
              └─ neither ─────────────────────────────────────────────────────► answer writer (fixed reply)

The retry loop is a visible edge from the SQL agent back to itself, not a loop hidden in a function, so
the graph shows every retry. _route_after_sql decides it from typed state only: each failed attempt adds
one ErrorRecord, so len(errors) > attempt means the attempt that just ran failed.

Every node returns a partial update; _merge (state.merge) applies it with each field's rule (sum the
metrics, append errors and passages, otherwise last write wins).
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from classifiers.error_classifier import TERMINAL_CATEGORIES
from config_loader import cfg
from front_door import front_door_node
from nodes.documents import documents_after_sql_node, documents_node, split_question_node
from nodes.entry import entry_node
from nodes.error_exit import error_exit_node
from nodes.sql_agent import sql_agent_node
from nodes.synthesizer import synthesizer_node
from state import StateSyncState, merge

logger = logging.getLogger(__name__)

ROUTES = ("sql", "docs", "hybrid")


def _route_after_front_door(state: StateSyncState) -> str:
    """After the router: a question neither source can answer goes to the writer; otherwise its route.
    A router that set neither is a defect, never a default to SQL."""
    if state.out_of_scope:
        return "synthesizer"
    if state.route not in ROUTES:
        raise RuntimeError(f"The router set no usable route ({state.route!r}).")
    return state.route


def _route_after_sql(state: StateSyncState) -> str:
    """The retry decision: "success", "retry" or "terminal". A final error is one whose category never
    retries (permission_denied, no_data_source, unclassified), a timeout past sql.timeout_retry_max, or
    any error once sql.retry_max attempts have run."""
    if len(state.errors) <= state.attempt:
        return "success"
    last = state.errors[-1]
    if last.category in TERMINAL_CATEGORIES:
        return "terminal"
    if last.category == "timeout" and sum(e.category == "timeout" for e in state.errors) > int(cfg.sql.timeout_retry_max):
        return "terminal"
    if state.attempt >= cfg.sql.retry_max:
        return "terminal"
    return "retry"


def _after_sql(state: StateSyncState) -> str:
    """The retry decision, with a two-source question continuing to its documents part on success."""
    outcome = _route_after_sql(state)
    if outcome == "success" and state.route == "hybrid":
        return "hybrid_docs"
    return outcome


def _after_error_exit(state: StateSyncState) -> str:
    """After the SQL loop's final error. A two-source question still gets its documents part. A database
    question whose data the database does not hold (no_data_source), in a workspace with documents, runs
    the documents route once instead ("route recovery"). Anything else goes to the writer, which explains
    the failure in plain words."""
    if state.route == "hybrid":
        return "hybrid_docs"
    if (state.route == "sql" and state.recovered_from is None and state.library_titles
            and state.errors and state.errors[-1].category == "no_data_source"):
        return "recover_docs"
    return "synthesizer"


def _step(name: str, node):
    """A graph node: run `node` on the state as a dict and merge its update."""
    async def run(state: StateSyncState) -> dict[str, Any]:
        update = await node(state.model_dump())
        return merge(state, update, node_name=name).model_dump()
    return run


async def _recover_docs(state_dict: dict[str, Any]) -> dict[str, Any]:
    """Route recovery: the documents route, once, for a database question the database could not answer."""
    update = await documents_node({**state_dict, "route": "docs"})
    update["recovered_from"] = "sql"
    return update


def build_graph() -> StateGraph:
    builder = StateGraph(StateSyncState)
    for name, node in (("entry", entry_node), ("front_door", front_door_node), ("sql", sql_agent_node),
                       ("docs", documents_node), ("hybrid", split_question_node),
                       ("hybrid_docs", documents_after_sql_node), ("recover_docs", _recover_docs),
                       ("error_exit", error_exit_node), ("synthesizer", synthesizer_node)):
        builder.add_node(name, _step(name, node))
    builder.set_entry_point("entry")
    builder.add_edge("entry", "front_door")
    builder.add_conditional_edges("front_door", _route_after_front_door,
                                  {"sql": "sql", "docs": "docs", "hybrid": "hybrid", "synthesizer": "synthesizer"})
    builder.add_conditional_edges("sql", _after_sql, {"success": "synthesizer", "retry": "sql",
                                                      "terminal": "error_exit", "hybrid_docs": "hybrid_docs"})
    builder.add_conditional_edges("error_exit", _after_error_exit, {
        "synthesizer": "synthesizer", "hybrid_docs": "hybrid_docs", "recover_docs": "recover_docs"})
    builder.add_edge("hybrid", "sql")
    builder.add_edge("docs", "synthesizer")
    builder.add_edge("hybrid_docs", "synthesizer")
    builder.add_edge("recover_docs", "synthesizer")
    builder.add_edge("synthesizer", END)
    return builder.compile()


graph = build_graph()
