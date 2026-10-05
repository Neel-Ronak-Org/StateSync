"""
orchestrator/graph.py
LangGraph: entry -> Normalize -> Rewrite -> Supervisor -> Specialist (routed)
-> [correction loop] -> Synthesizer.

Graph structure (Phase B + C2 + D + E1 + cycle 007 Workstream A):
  entry_node
      |
  normalize_node    (D1: language detection + entity-preserving rewrite;
      |               ALWAYS runs before Supervisor -- Callout 1: Supervisor
      |               reads normalized_query only, never raw_query.)
  rewrite_node      (cycle 007: contextual query rewriting for multi-turn,
      |               V2_STRATEGY.md §3b. ALWAYS runs, direct edge, same
      |               structural technique as normalize_node -- it
      |               OVERWRITES normalized_query with a follow-up's
      |               standalone form (or leaves it unchanged) rather than
      |               adding a second field, so Callout 1's guarantee above
      |               is unmodified: Supervisor still reads exactly one
      |               field and still never sees raw_query.)
  supervisor_node   (LLM classification: sql | docs | hybrid)
      |
  [routing dispatch] -> sql_agent_node                (route = "sql")
                     -> unstructured_knowledge_node    (route = "docs")
                     -> hybrid_plan_node               (route = "hybrid": splits the question,
                                                         then sql_agent_node runs the SQL half
                                                         with its correction loop, then
                                                         hybrid_docs_node the documents half)
      |
  sql_agent_node -> [check_result, C2] -> success:            synthesizer_node
                                        -> retriable error:    sql_agent_node (self-loop,
                                                                bounded by attempt < sql.retry_max)
                                        -> terminal error, or
                                           retry_max exhausted: error_exit_node -> synthesizer_node
      |
  sql route, terminal no_data_source, [pipeline] route_recovery on, workspace has documents:
                                        error_exit_node -> recover_docs (the docs node, once) -> synthesizer_node
  docs (unstructured_knowledge_node) -------------------------> synthesizer_node
  hybrid: hybrid_plan_node -> sql_agent_node [C2 loop] -> hybrid_docs_node -> synthesizer_node
      |
  synthesizer_node  (always runs; generates answer_text)

With [pipeline] front_door on ("llm"), build_graph() wires entry -> front_door -> [routing dispatch]
instead of entry -> normalize -> rewrite -> supervisor -> [routing dispatch]: one model call that
reads the database card and the best-matching documents (front_door.py, T4a). A question neither
source can answer (state.out_of_scope) goes straight from the front door to the synthesizer, which
answers with a fixed message and no model call. With "off" (shipped) the graph is exactly the one above.
route_graph is the same entry as far as the routing decision only, for the evaluation hook
debug_route_only.

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

from config_loader import cfg, flags as config_flags
from classifiers.error_classifier import TERMINAL_CATEGORIES
from state import StateSyncState, merge, reset_lww_writers
from front_door import front_door_enabled, front_door_node
from nodes.entry import entry_node
from nodes.error_exit import error_exit_node
from nodes.normalize import normalize_node
from nodes.rewrite import rewrite_node
from nodes.supervisor import supervisor_node
from nodes.sql_agent import sql_agent_node
from nodes.unstructured_knowledge import hybrid_docs_node, hybrid_plan_node, unstructured_knowledge_node
from nodes.synthesizer import synthesizer_node
from routing.registry import register, resolve, validate_routing_table

logger = logging.getLogger(__name__)


# ── Register specialist nodes ─────────────────────────────────────────────────
# This is the "registration function that is actually called" per A0.5.
# All three routes must be registered before validate_routing_table() is called.
register("sql", sql_agent_node)
register("docs", unstructured_knowledge_node)      # Part E1
register("hybrid", hybrid_plan_node)               # D7: split, then the SQL agent, then hybrid_docs_node

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


def _route_after_front_door(state: StateSyncState) -> str:
    """After the front door: out of scope goes to the writer; otherwise its route dispatches exactly as
    the supervisor's does. A front door that set neither is a defect, never a default to SQL."""
    if state.out_of_scope:
        return "synthesizer"
    if state.route is None:
        raise RuntimeError("The front door set neither a route nor out_of_scope.")
    return _route_after_supervisor(state)


def _timeout_retry_max() -> int:
    """sql.timeout_retry_max as an accessor so tests can lower it (cfg is read-only)."""
    return int(cfg.sql.timeout_retry_max)


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

    if last_error.category == "timeout":
        timeouts = sum(1 for e in state.errors if e.category == "timeout")
        if timeouts > _timeout_retry_max():
            logger.info(
                "sql_agent timeout retry bound (sql.timeout_retry_max=%d) exceeded "
                "at attempt=%d — exiting the loop.",
                _timeout_retry_max(), state.attempt,
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


async def _rewrite(state: StateSyncState) -> dict[str, Any]:
    update = await rewrite_node(state.model_dump())
    return _merge_with_scratch(state, update, "rewrite")


def _front_door_step(mode: str):
    """The front door node for one pipeline.front_door setting: the strategy is chosen here, when the
    graph is built, not read back from the state."""
    async def _front_door(state: StateSyncState) -> dict[str, Any]:
        update = await front_door_node(state.model_dump(), mode)
        return _merge_with_scratch(state, update, "front_door")
    return _front_door


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


async def _recover_docs(state: StateSyncState) -> dict[str, Any]:
    """Route recovery: the documents route, run once, in place of a SQL route whose data the database lacks.
    unstructured_knowledge_node is unchanged; it is shown the route it is about to run."""
    node_fn = resolve("docs")
    update = await node_fn({**state.model_dump(), "route": "docs"})
    update["recovered_from"] = "sql"
    return _merge_with_scratch(state, update, "recover_docs")


async def _hybrid_docs(state: StateSyncState) -> dict[str, Any]:
    update = await hybrid_docs_node(state.model_dump())
    return _merge_with_scratch(state, update, "hybrid_docs")


def _after_sql(state: StateSyncState) -> str:
    """Where the SQL agent's run goes next: the correction-cycle decision (_route_after_sql), with
    the hybrid route continuing to its documents half instead of straight to the synthesizer."""
    outcome = _route_after_sql(state)
    if outcome == "retry":
        return "retry"
    if outcome == "terminal":
        return "terminal"
    return "hybrid_docs" if state.route == "hybrid" else "success"


def _has_documents(state: StateSyncState) -> bool:
    """The workspace has documents the docs route can search: uploaded ones, or a source whose sample
    corpus has documents."""
    return bool(getattr(state, "library_titles", None)) or (
        getattr(state, "target_schema", None) in cfg.pipeline.document_schemas
    )


def _recovery_target(state: StateSyncState) -> str:
    """Where the run goes after the SQL loop's terminal exit (route recovery, routing_docs_fixes.md F-RR).
    A hybrid question still gets its documents half (as before 2026-10-02). With [pipeline] route_recovery
    on, a SQL route whose last error is no_data_source, in a workspace with documents, runs the documents
    route once ("recover_docs"); a question that already recovered never does again. Anything else goes
    straight to the writer, as it always did."""
    if state.route == "hybrid":
        return "hybrid_docs"
    flags = getattr(state, "flags_snapshot", None) or {}
    if flags.get("pipeline.route_recovery", "off") != "on":
        return "synthesizer"
    errors = getattr(state, "errors", None) or []
    if (
        state.route == "sql"
        and getattr(state, "recovered_from", None) is None
        and errors and errors[-1].category == "no_data_source"
        and _has_documents(state)
    ):
        return "recover_docs"
    return "synthesizer"


def _after_error_exit(state: StateSyncState) -> str:
    """After the loop's terminal exit: the hybrid route's documents half, route recovery, or the writer."""
    return _recovery_target(state)


async def _synthesizer(state: StateSyncState) -> dict[str, Any]:
    update = await synthesizer_node(state.model_dump())
    return _merge_with_scratch(state, update, "synthesizer")


async def _error_exit(state: StateSyncState) -> dict[str, Any]:
    update = await error_exit_node(state.model_dump())
    return _merge_with_scratch(state, update, "error_exit")


# ── Graph builder ─────────────────────────────────────────────────────────────
def _front_door_mode(front_door: str | None) -> str:
    """The front door setting a graph is built for: the argument, else [pipeline] front_door."""
    return str(front_door if front_door is not None else config_flags.pipeline.get("front_door", "off"))


def build_graph(front_door: str | None = None) -> StateGraph:
    """
    Build and compile the LangGraph (Phase B skeleton + C2 correction loop).
    `front_door` is [pipeline] front_door ("off" | "llm") unless given. Returns the compiled graph
    ready for .ainvoke().
    """
    mode = _front_door_mode(front_door)
    use_front_door = front_door_enabled(mode)
    builder = StateGraph(StateSyncState)

    # Add nodes
    builder.add_node("entry", _entry)
    if use_front_door:
        builder.add_node("front_door", _front_door_step(mode))
    else:
        builder.add_node("normalize", _normalize)
        builder.add_node("rewrite", _rewrite)
        builder.add_node("supervisor", _supervisor)
    builder.add_node("sql", _sql_specialist)
    builder.add_node("docs", _docs_specialist)
    builder.add_node("hybrid", _hybrid_specialist)
    builder.add_node("hybrid_docs", _hybrid_docs)
    builder.add_node("recover_docs", _recover_docs)
    builder.add_node("error_exit", _error_exit)
    builder.add_node("synthesizer", _synthesizer)

    # Entry point
    builder.set_entry_point("entry")

    if use_front_door:
        # entry → front_door → routing dispatch (or, out of scope, the writer). Normalise, rewrite and
        # the supervisor do not run: the front door returns the standalone question and the route.
        builder.add_edge("entry", "front_door")
        builder.add_conditional_edges(
            "front_door",
            _route_after_front_door,
            {
                "sql":         "sql",
                "docs":        "docs",
                "hybrid":      "hybrid",
                "synthesizer": "synthesizer",
            },
        )
    else:
        # entry → normalize → rewrite → supervisor. D1: normalize always runs
        # first, direct non-conditional edge, so normalized_query survives intact
        # (Callout 1). Cycle 007 (Workstream A) inserts rewrite the same way,
        # for the same structural reason: it always runs, always sets
        # normalized_query (possibly unchanged), and supervisor's existing
        # "reads normalized_query only" assertion needs no change.
        builder.add_edge("entry", "normalize")
        builder.add_edge("normalize", "rewrite")
        builder.add_edge("rewrite", "supervisor")

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
        _after_sql,
        {
            "success":     "synthesizer",
            "retry":       "sql",
            "terminal":    "error_exit",
            "hybrid_docs": "hybrid_docs",
        },
    )
    builder.add_conditional_edges(
        "error_exit",
        _after_error_exit,
        {"synthesizer": "synthesizer", "hybrid_docs": "hybrid_docs", "recover_docs": "recover_docs"},
    )
    # route_recovery: the documents route's one run in place of a SQL route whose data was not there.
    builder.add_edge("recover_docs", "synthesizer")

    # docs never fails → synthesizer. hybrid: plan → the SQL agent (with its correction loop) →
    # hybrid_docs → synthesizer (2026-10-02; it used to run its own single-shot SQL).
    builder.add_edge("docs",        "synthesizer")
    builder.add_edge("hybrid",      "sql")
    builder.add_edge("hybrid_docs", "synthesizer")

    # synthesizer → END
    builder.add_edge("synthesizer", END)

    return builder.compile()


def build_route_graph(front_door: str | None = None) -> StateGraph:
    """The graph as far as the routing decision and no further: entry → front door, or entry → normalize
    → rewrite → supervisor. For the evaluation hook debug_route_only (pipeline.debug_hooks), which
    measures every routing arm, the shipped router included. No specialist runs and nothing is written."""
    builder = StateGraph(StateSyncState)
    builder.add_node("entry", _entry)
    builder.set_entry_point("entry")
    mode = _front_door_mode(front_door)
    if front_door_enabled(mode):
        builder.add_node("front_door", _front_door_step(mode))
        builder.add_edge("entry", "front_door")
        builder.add_edge("front_door", END)
    else:
        builder.add_node("normalize", _normalize)
        builder.add_node("rewrite", _rewrite)
        builder.add_node("supervisor", _supervisor)
        builder.add_edge("entry", "normalize")
        builder.add_edge("normalize", "rewrite")
        builder.add_edge("rewrite", "supervisor")
        builder.add_edge("supervisor", END)
    return builder.compile()


# ── Module-level compiled graphs (singletons) ─────────────────────────────────
graph = build_graph()
route_graph = build_route_graph()
