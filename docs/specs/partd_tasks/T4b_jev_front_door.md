# T4b: the Jev front door (`pipeline.front_door = "jev"`)

Parent: T4a (`docs/specs/partd_tasks/T4a_front_door.md`) and `docs/specs/routing_docs_fixes.md` §2b variant
(b). Base: the head of branch `v3-phase3-workspaces`; run `git reset --hard v3-phase3-workspaces` on your own
worktree branch first. You implement; the lead reviews by running the checks and probing past them.

## What it is

Jev (TypeSafe's decision model) answers typed questions about an application state with probabilities. It
writes no text. In this arm **Jev makes every routing decision**; the existing LLM front door
(`decide_llm`) is called only when text must be written: to resolve a follow-up into a standalone question,
to split a two-source question, or for a non-English question.

Access (owner's choice, 2026-10-06): OpenCode Zen, `POST https://opencode.ai/zen/v1/systemone`,
`Authorization: Bearer <key>`, body
`{"model": "<model>", "state": "<text>", "questions": {"<name>": {"type": "noul", "instructions": "<text>"}, ...}}`;
the reply's `answers.<name>.noul` is P(yes) in [0, 1]. A working call is `scripts/paperb_hosted.py` (read it).
The key file is `/run/secrets/opencode_api_key` inside the container (path overridable by the environment
variable `JEV_API_KEY_FILE`); never log it.

## Requirement

1. **A Jev client**, `orchestrator/jev_client.py`, pure apart from one async HTTP call (httpx, as the rest of
   the orchestrator uses): `ask(state: str, questions: dict[str, str]) -> JevAnswer` with the probabilities,
   the served model (the reply's `model` field if present, else the requested one), milliseconds and any
   error. Timeout `[front_door] jev_timeout_s`. One retry on HTTP 429 or 5xx after `jev_retry_wait_s`; then an
   error. Never raises to the caller: an error is a value.
2. **The state Jev sees** (§2b): one JSON text with `question`, `conversation` (the evidence's earlier turns:
   standalone question, route, answered, the start of the answer, as the LLM front door gets them),
   `database` (the evidence's card text) and `documents` (library size if known, the evidence's best
   documents: title, score, excerpt). Build it in a pure function.
3. **Four noul questions**, fixed text (put them in `front_door.py` as constants):
   - `uses_history`: "Does the new question depend on the earlier conversation turns to be understood?"
   - `needs_database`: "Does answering the new question need facts stored in the database described?"
   - `needs_documents`: "Does answering the new question need what the uploaded documents say?"
   - `out_of_scope`: "Is the new question about something neither the database nor the documents could answer?"
4. **From probabilities to the routing table.** `need(p)` is `"yes"` when p ≥ `[front_door] jev_yes`
   (default 0.6), `"no"` when p ≤ `jev_no` (default 0.4), else `"unsure"`. `out_of_scope` is
   p ≥ `jev_out_of_scope` (default 0.5); `uses_history` is p ≥ `jev_history` (default 0.5). The route is
   `route_for(...)`, unchanged. Pure function, unit-tested at the boundaries.
5. **`decide_jev(evidence, question) -> Decision`**, registered as `DECIDERS["jev"]`:
   - one Jev request with the four questions;
   - if `uses_history`, or the route is hybrid, or the existing language gate says the question is not
     English: call `decide_llm` for the text only (standalone question, language, the split and
     `documents_depend_on_database`), keeping Jev's needs, out-of-scope and route;
   - if that LLM call changed the question (standalone ≠ question), ask Jev's three needs/out-of-scope
     questions once more with the standalone question in the state (no new search), and route on that;
   - **Jev failure** (an error from the client): fall back to `decide_llm` entirely; the trace records
     `jev_error` and `fallback: "llm"`.
   - Token and call counts: `llm_calls` counts LLM calls only; add `jev_calls`.
6. **Trace.** `build_trace` adds, for this arm, `jev: {probabilities (first and, if asked, re-check),
   model, ms, calls, rechecked, llm_used, fallback?, jev_error?}`. The shape of everything else is unchanged.
7. **Config, additive only.** `config/statesync.toml` `[front_door]`: `jev_url`
   (`"https://opencode.ai/zen/v1/systemone"`), `jev_model` (`"jev-1.13-free"`), `jev_timeout_s` (30),
   `jev_retry_wait_s` (5), `jev_yes`, `jev_no`, `jev_history`, `jev_out_of_scope`. `config/flags.toml`: the
   `front_door` comment lists `"jev"`; the default stays `"off"`.

## Acceptance (the lead runs these)

- **F1.** Unit tests (no network; fake the client):
  - the state text holds the question, turns, card and documents; the request body has the four questions;
  - `need()` at 0.4, 0.4001, 0.5999, 0.6; out-of-scope and history thresholds;
  - first-turn single-source question: no LLM call, route from Jev;
  - `uses_history` true: one LLM call, Jev's route kept, re-check asked with the standalone question;
  - hybrid: one LLM call, the split comes from the LLM, route stays hybrid;
  - Jev error: `decide_llm` decides, trace says `fallback: "llm"`;
  - the client: a 429 then 200 succeeds; two 429s is an error value; the key is never in a log line.
- **F2.** `/opt/anaconda3/bin/python3 -m pytest -q tests` and `/tmp/t1venv/bin/python -m pytest -q tests` pass;
  `PATH=/opt/anaconda3/bin:$PATH scripts/lint-no-literals.sh` passes; `front_door = "llm"` and `"off"` behave
  exactly as before.
- **F3, the lead's.** The routing set (dev, then test) through `scripts/eval_routing.py --arm jev`; thresholds
  refitted on dev from the recorded probabilities only.

## Rules

- **Allowed:** `orchestrator/`, `config/statesync.toml` (new `[front_door]` keys only), `config/flags.toml`
  (the `front_door` comment only), `tests/`.
- **Not allowed:** `mcp/`, `web/`, `scripts/`, `docs/eval/`, `secrets/`, Docker, `config/error_taxonomy*.json`.
- Config is additive only. Never `git stash`, never push, never touch another branch. Commit once, with a
  message starting `T4b:`.
- Report the files changed, the tests, the commands with their output tails, and anything you were unsure of.
