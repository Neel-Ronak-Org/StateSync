# T5: the hybrid split, with the documents half narrowed by the database half's entities

Parent spec: `docs/specs/routing_docs_fixes.md` §5 (H1, H2). Base: the head of branch
`v3-phase3-workspaces` (with T4a merged); run `git reset --hard v3-phase3-workspaces` on your own
worktree branch first. You implement; the lead reviews by running the checks and probing past them.

## Why

Today the hybrid route can use the database result in its documents half only through ONDC's
`seller_id`/`provider_id` columns (`_extract_party_ids`). For any other data, the documents half
searches blind: "which of these companies had the lowest margin, and what does its 10-K say drove it"
searches every filing for "what drove it".

The front door (T4a) already returns the split and says whether the documents half depends on the
database half.

## Flag

`[pipeline] hybrid_entities = "off" | "on"`, default `"off"`. Config changes are additive only.

## Requirements

1. **State.** The front door's `documents_depend_on_database` reaches the state as a new
   `docs_depend_on_sql: bool = False` (A2 last-write-wins).
   - Set it in `front_door_node` from the decision.
   - The legacy path (front door off) never sets it.

2. **Entity narrowing.** With `hybrid_entities = "on"` and `docs_depend_on_sql` true, the hybrid
   documents half (`hybrid_docs_node`) runs this step after the SQL half. It runs whenever the profile
   has no party-id columns or the SQL result has none of them.
   - **Entities:** the distinct non-empty string values of the SQL result's text columns, taken from the
     first `cfg.hybrid.entity_rows` rows (new `[hybrid]` section in `statesync.toml`, additive;
     `entity_rows = 5`, `max_entities = 5`). Up to `max_entities` values, in row order.
   - **Documents:** the workspace's ready uploaded documents whose title contains an entity. Compare
     after lower-casing and removing everything but letters and digits on both sides, so "Coca-Cola"
     matches "COCACOLA 2022 10K". Fetch the titles as `main._library_titles` does today, through a
     helper the node can call, **without** the router's 20-title cap: add a `limit` argument to the
     MCP call only if the MCP already supports one; otherwise list them through the same
     `library.manage` action with its cap raised for this call (state which you did).
   - **Search:** when at least one document matches, run the documents half's second-round search
     restricted to those `document_ids`, with `top_k = cfg.retrieval.semantic_top_k_max`. Keep its
     mode, scope, re-rank and passage-order flags. Skip the first round.
   - When none match, run the two-round search exactly as today.
   - **Trace:** `hybrid_entities: {"entities": [...], "matched_documents": [...titles], "narrowed": bool}`.

3. **Off.** Byte-identical behaviour. ONDC's party-id path is unchanged in all cases.

## Acceptance (the lead runs these)

- **E1.** Unit tests:
  - entity extraction (text columns only, row cap, entity cap, empty values);
  - title matching (normalisation, no match, several matches);
  - the narrowing decision (flag, dependency, party ids present, none matched);
  - that the narrowed search passes `document_ids` and skips the first round.
- **E2.** With the flag off, `hybrid_docs_node` makes exactly the same MCP calls as at the base commit
  (a fake session records them).
- **E3.** `/opt/anaconda3/bin/python3 -m pytest -q tests` and `/tmp/t1venv/bin/python -m pytest -q tests`
  pass. `PATH=/opt/anaconda3/bin:$PATH scripts/lint-no-literals.sh` passes. Report ruff on the files you
  changed only.

## Rules

- **Allowed:** `orchestrator/`, `mcp/` only if the title listing truly needs a parameter (backward
  compatible, optional), `config/flags.toml` (new key only), `config/statesync.toml` (new `[hybrid]`
  section only), `tests/`.
- **Not allowed:** `web/`, `scripts/`, `config/error_taxonomy*.json`, `docs/eval/`, Docker.
- Never `git stash`, never push, never touch another branch. Commit once, with a message starting
  `T5:`.
- Every numeric literal is config or carries `# noqa: literals - <reason>`. Report the files changed,
  the tests, the commands with their output tails, and anything you were unsure of.
