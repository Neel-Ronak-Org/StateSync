"""
orchestrator/graph.py
LangGraph: entry -> Normalize -> Supervisor -> Specialist (routed) ->
[correction loop] -> Synthesizer.

Graph structure (Phase B + C2 + D + E1):
  entry_node
      |
  normalize_node    (D1: language detection + entity-preserving rewrite;
      |               ALWAYS runs before Supervisor -- Callout 1: Supervisor
      |               reads normalized_query only, never raw_query. This is
      |               what makes that guarantee structural: normalize_node
      |               always sets normalized_query, by a direct (non-
      |               conditional) edge, before supervisor_node can run.)
  supervisor_node   (LLM classification: sql | docs | hybrid)
      |
  [routing dispatch] -> sql_agent_node                (route = "sql")
                     -> unstructured_knowledge_node    (route = "docs" | "hybrid";
                                                         same node, decomposition
                                                         gated inside to hybrid only, D7)
      |
  sql_agent_node -> [check_result, C2] -> success:            synthesizer_node
                                        -> retriable error:    sql_agent_node (self-loop,
                                                                bounded by attempt < sql.retry_max)
                                        -> terminal error, or
                                           retry_max exhausted: error_exit_node -> synthesizer_node
      |
  docs/hybrid (unstructured_knowledge_node) ------------------> synthesizer_node
      |
  synthesizer_node  (always runs; generates answer_text)

The correction cycle (C2 Deliverable 2) is a real, visible graph edge --
not a loop or try/except hidden inside sql_agent_node -- so the Supervisor
(and anyone reading the graph) can see a failing SQL sub-task retry and
terminate. _route_after_sql() below is that edge's decision function; it
reads only typed StateSyncState fields (len(state.errors) vs state.attempt,
and the last error's category) because conditional edge functions receive
the merged, typed state, not a node's raw scratch-key update dict.

The routing table registry (routing/registry.py) is consulted at graph
build time and at runtime for dispatch. No if-branches on route names (A8).

Per D1 Callout 2, DigipinResolver (spatial_resolver.py) is NOT a graph node
and is not registered here -- it is called inline inside sql_agent_node, a
deterministic pre-step with no LLM/MCP round-trip of its own. The routing
table below still resolves to exactly 3 routes (D-V7).
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import StateGraph, END

from config_loader import cfg
from classifiers.error_classifier import TERMINAL_CATEGORIES
from state import StateSyncState, merge, reset_lww_writers
from nodes.entry import entry_node
from nodes.error_exit import error_exit_node
from nodes.normalize import normalize_node
from nodes.supervisor import supervisor_node
from nodes.sql_agent import sql_agent_node
from nodes.unstructured_knowledge import unstructured_knowledge_node
from nodes.synthesizer import synthesizer_node
from routing.registry import register, resolve, validate_routing_table

logger = logging.getLogger(__name__)


# ── Register specialist nodes ─────────────────────────────────────────────────
# This is the "registration function that is actually called" per A0.5.
# All three routes must be registered before validate_routing_table() is called.
register("sql", sql_agent_node)
register("docs", unstructured_knowledge_node)      # Part E1
register("hybrid", unstructured_knowledge_node)    # Part E1; decomposition gated inside (D7)

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


# ── Routing condition (called by LangGraph after sql_agent_node) — C2 ─────────
def _route_after_sql(state: StateSyncState) -> str:
    """
    Correction-cycle routing decision (C2 Deliverable 2).

    A sql_agent_node invocation always reports `attempt = attempt_on_entry`
    (the attempt index that just ran) and appends exactly one ErrorRecord
    when -- and only when -- that invocation failed (including a first
    empty_result; a repeat empty_result is accepted as success and appends
    nothing). So `len(state.errors) > state.attempt` is true iff the
    invocation that just ran failed; there is no scratch-key or extra state
    field needed to know this.
    """
    just_failed = len(state.errors) > state.attempt
    if not just_failed:
        return "success"

    last_error = state.errors[-1]
    if last_error.category in TERMINAL_CATEGORIES:
        logger.info(
            "sql_agent terminal category '%s' at attempt=%d — exiting the loop.",
            last_error.category, state.attempt,
        )
        return "terminal"

    if state.attempt >= cfg.sql.retry_max:
        logger.info(
            "sql_agent retry bound (sql.retry_max=%d) reached at attempt=%d — exiting the loop.",
            cfg.sql.retry_max, state.attempt,
        )
        return "terminal"

    return "retry"


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
    underscore-prefixed scratch keys unmerged. merge() deliberately drops
    these -- they are not part of the A2 typed schema -- but a node can use
    one to hand a value to the very next node over a *direct* (non-
    conditional) edge only; see C2's scratch-key-through-conditional-edge
    finding (docs/phase_c2_log.md) for why that hop-limit is load-bearing.
    No node currently returns one (C2 removed the last, _sql_error, in
    favor of deriving state from state.errors/state.attempt directly), but
    the mechanism stays available for a future node that needs it.
    """
    merged = merge(state, update, node_name=node_name).model_dump()
    for key, value in update.items():
        if key.startswith("_"):
            merged[key] = value
    return merged


async def _entry(state: StateSyncState) -> dict[str, Any]:
    update = await entry_node(state.model_dump())
    return _merge_with_scratch(state, update, "entry")


async def _normalize(state: StateSyncState) -> dict[str, Any]:
    update = await normalize_node(state.model_dump())
    return _merge_with_scratch(state, update, "normalize")


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


async def _error_exit(state: StateSyncState) -> dict[str, Any]:
    update = await error_exit_node(state.model_dump())
    return _merge_with_scratch(state, update, "error_exit")


# ── Graph builder ─────────────────────────────────────────────────────────────
def build_graph() -> StateGraph:
    """
    Build and compile the LangGraph (Phase B skeleton + C2 correction loop).
    Returns the compiled graph ready for .ainvoke().
    """
    builder = StateGraph(StateSyncState)

    # Add nodes
    builder.add_node("entry", _entry)
    builder.add_node("normalize", _normalize)
    builder.add_node("supervisor", _supervisor)
    builder.add_node("sql", _sql_specialist)
    builder.add_node("docs", _docs_specialist)
    builder.add_node("hybrid", _hybrid_specialist)
    builder.add_node("error_exit", _error_exit)
    builder.add_node("synthesizer", _synthesizer)

    # Entry point
    builder.set_entry_point("entry")

    # entry → normalize → supervisor (D1: gate always runs first, direct
    # non-conditional edges so normalized_query survives intact -- Callout 1)
    builder.add_edge("entry", "normalize")
    builder.add_edge("normalize", "supervisor")

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

    # sql → [correction-cycle dispatch] — C2 Deliverable 2, the real graph
    # edge replacing "no retry at all" (Phase B). "retry" targets "sql"
    # itself: this is the self-loop, bounded by _route_after_sql checking
    # attempt < sql.retry_max before ever returning it.
    builder.add_conditional_edges(
        "sql",
        _route_after_sql,
        {
            "success":  "synthesizer",
            "retry":    "sql",
            "terminal": "error_exit",
        },
    )
    builder.add_edge("error_exit", "synthesizer")

    # docs/hybrid never fail (static stub) → synthesizer directly.
    builder.add_edge("docs",   "synthesizer")
    builder.add_edge("hybrid", "synthesizer")

    # synthesizer → END
    builder.add_edge("synthesizer", END)

    return builder.compile()


# ── Module-level compiled graph (singleton) ───────────────────────────────────
graph = build_graph()
