"""
orchestrator/graph.py
4-node LangGraph: entry → Supervisor → Specialist (routed) → Synthesizer.

Graph structure (Phase B):
  entry_node
      ↓
  supervisor_node   (LLM classification: sql | docs | hybrid)
      ↓
  [routing dispatch] → sql_agent_node  (route = "sql")
                     → stub_node       (route = "docs" | "hybrid")
      ↓
  synthesizer_node  (always runs; generates answer_text, chart: null)

The routing table registry (routing/registry.py) is consulted at graph
build time and at runtime for dispatch. No if-branches on route names (A8).
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import StateGraph, END

from state import StateSyncState, merge, reset_lww_writers
from nodes.entry import entry_node
from nodes.supervisor import supervisor_node
from nodes.sql_agent import sql_agent_node
from nodes.stub_node import stub_node
from nodes.synthesizer import synthesizer_node
from routing.registry import register, resolve, validate_routing_table

logger = logging.getLogger(__name__)


# ── Register specialist nodes ─────────────────────────────────────────────────
# This is the "registration function that is actually called" per A0.5.
# All three routes must be registered before validate_routing_table() is called.
register("sql", sql_agent_node)
register("docs", stub_node)      # Phase B stub; real impl in Phase E1
register("hybrid", stub_node)    # Phase B stub; real impl in Phase E1

# Validate at module load time — unresolvable name = startup failure (A8).
validate_routing_table()


# ── Routing condition (called by LangGraph after supervisor) ──────────────────
def _route_after_supervisor(state: StateSyncState) -> str:
    """
    Routing function: maps state.route → node name.
    Consults the routing table registry; no if-branches.
    Returns the LangGraph node name to dispatch to.
    """
    route = state.route or "sql"
    # Validate the route is registered (defense in depth; validate_routing_table
    # already checked this at startup, but route comes from LLM output at runtime).
    try:
        resolve(route)
    except RuntimeError:
        logger.warning(
            "Supervisor produced unregistered route %r; falling back to 'sql'.",
            route,
        )
        route = "sql"

    # LangGraph node names match route names here by convention.
    return route


# ── Node wrappers (adapt async callables to LangGraph's dict-in/dict-out) ─────
# LangGraph's own default per-key merge for a plain (non-Annotated-reducer)
# Pydantic state schema is last-write-wins on every field it sees returned --
# it has no idea metrics should SUM or errors should APPEND. Each wrapper
# therefore runs the node's partial update through state.merge() itself
# (using state.py's A2-correct SUM/APPEND/LWW/immutable dispatch) and returns
# the FULL resulting state as the "update": since every field is then present,
# LangGraph's last-write-wins default degenerates into a full replacement
# with the already-correctly-merged state, rather than a raw per-key
# overwrite of the node's partial output.


def _merge_with_scratch(state: StateSyncState, update: dict[str, Any], node_name: str) -> dict[str, Any]:
    """
    Apply state.merge() for the typed A2 fields, then pass through any
    underscore-prefixed scratch keys (_sql_error, _stub_message) unmerged.
    merge() deliberately drops these -- they are not part of the A2 typed
    schema -- but sql_agent_node/stub_node use them to hand a value to the
    very next node (synthesizer_node reads them). Dropping them here would
    silently break that handoff.
    """
    merged = merge(state, update, node_name=node_name).model_dump()
    for key, value in update.items():
        if key.startswith("_"):
            merged[key] = value
    return merged


async def _entry(state: StateSyncState) -> dict[str, Any]:
    update = await entry_node(state.model_dump())
    return _merge_with_scratch(state, update, "entry")


async def _supervisor(state: StateSyncState) -> dict[str, Any]:
    update = await supervisor_node(state.model_dump())
    return _merge_with_scratch(state, update, "supervisor")


async def _sql_specialist(state: StateSyncState) -> dict[str, Any]:
    node_fn = resolve("sql")
    # Reset LWW writer tracking at start of sequential dispatch (not parallel here)
    update = await node_fn(state.model_dump())
    return _merge_with_scratch(state, update, "sql")


async def _docs_specialist(state: StateSyncState) -> dict[str, Any]:
    node_fn = resolve("docs")
    update = await node_fn(state.model_dump())
    return _merge_with_scratch(state, update, "docs")


async def _hybrid_specialist(state: StateSyncState) -> dict[str, Any]:
    node_fn = resolve("hybrid")
    update = await node_fn(state.model_dump())
    return _merge_with_scratch(state, update, "hybrid")


async def _synthesizer(state: StateSyncState) -> dict[str, Any]:
    update = await synthesizer_node(state.model_dump())
    return _merge_with_scratch(state, update, "synthesizer")


# ── Graph builder ─────────────────────────────────────────────────────────────
def build_graph() -> StateGraph:
    """
    Build and compile the Phase B LangGraph.
    Returns the compiled graph ready for .ainvoke().
    """
    builder = StateGraph(StateSyncState)

    # Add nodes
    builder.add_node("entry", _entry)
    builder.add_node("supervisor", _supervisor)
    builder.add_node("sql", _sql_specialist)
    builder.add_node("docs", _docs_specialist)
    builder.add_node("hybrid", _hybrid_specialist)
    builder.add_node("synthesizer", _synthesizer)

    # Entry point
    builder.set_entry_point("entry")

    # entry → supervisor
    builder.add_edge("entry", "supervisor")

    # supervisor → routing dispatch
    builder.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        {
            "sql":    "sql",
            "docs":   "docs",
            "hybrid": "hybrid",
        },
    )

    # All specialist paths → synthesizer
    builder.add_edge("sql",    "synthesizer")
    builder.add_edge("docs",   "synthesizer")
    builder.add_edge("hybrid", "synthesizer")

    # synthesizer → END
    builder.add_edge("synthesizer", END)

    return builder.compile()


# ── Module-level compiled graph (singleton) ───────────────────────────────────
graph = build_graph()
