# T2: SQL "not answerable", plain failures, zero rows, route recovery

Parent spec: `docs/specs/routing_docs_fixes.md` (flaws S1, S3, S4, R3; fixes F-S1, F-S3, F-S4, F-RR).
Base: the head of branch `v3-phase3-workspaces` when you start (it contains this file, and `abf7e15`), which already has F-S2: the `no_data_source` category, terminal, used for Layer
1's "statement reads no table". You implement; the lead reviews by running the checks and probing past
them.

## Flags

Add each flag to `config/flags.toml` under `[pipeline]`, next to the existing flags and in their style,
**default `"off"`**. With every flag off, the shipped behaviour must stay byte-identical: the same
prompts, contexts, routes and categories.

| Flag | Values | Fix |
|---|---|---|
| `sql_no_data` | `"off"` \| `"on"` | F-S1, and showing F-S2 to the rest of the pipeline |
| `plain_failures` | `"off"` \| `"on"` | F-S3, F-S4 |
| `route_recovery` | `"off"` \| `"on"` | F-RR |

## Requirements

### 1. `sql_no_data` (F-S1)

**On.** `sql_agent.sql_system_prompt` appends this rule, exactly, after the template's last rule, to the
writer's and the corrector's prompts:

```
8. If the tables described cannot answer the question -- the data it asks about is not in them --
   reply with exactly NOT_ANSWERABLE: <one short reason> instead of SQL. Never write a query that
   returns a constant, or reads an unrelated table, to stand in for data the database does not hold.
```

- A model reply whose text, after fences and whitespace are stripped, starts with `NOT_ANSWERABLE`
  (case-insensitive) is **not** sent to the MCP.
- It becomes an `ErrorRecord` with `category="no_data_source"`, `message` set to the reply's first 300
  characters, `engine_code=None`, `offending_sql=None`, and `remediation_hint` set to the taxonomy's
  no_data_source hint (read it through the classifier, do not copy it).
- It is terminal, so the existing correction loop stops.
- Put the parser in a small pure function and unit-test it.
- Apply it wherever the main writer and corrector replies are turned into SQL. List any path you leave
  alone (fast path, templates, self-consistency) and why.

**Off.** No rule, no parsing. A `no_data_source` returned by `classify_error` for a Layer 1 "reads no
table" rejection is recorded as `permission_denied` with the taxonomy's generic Layer 1 hint, exactly
as before `abf7e15`. Do this mapping where the SQL agent builds its `ErrorRecord`.

### 2. `plain_failures` (F-S3, F-S4)

These are changes to the writer's context, in `synthesizer.py`.

**On, a failed query.** The context replaces the block that tells the model to name the category. The
new block is:

```
The data query did not produce an answer: {plain}
Explain this to the user plainly in one or two sentences. Do not mention internal labels, error
categories or codes. Do not invent a number or fact that was never computed.
```

`{plain}` comes from this mapping, kept in one dict:
- `no_data_source`: "the database in this workspace does not hold the data this question asks about."
- `permission_denied`: "the request was refused, because this system only runs single, read-only
  queries."
- `timeout`: "the query took too long and was stopped."
- `empty_result`: "no records matched."
- Anything else: "the query could not be completed."

The category stays in `state.errors` and the trace.

**On, zero rows.** The line `The SQL query returned zero rows. The answer may be 'none' or 'zero'.`
becomes:

```
The SQL query returned zero rows: no records matched it. Say that no matching records were found. Do not turn this into a figure about something the query did not measure.
```

**Off.** Both texts are unchanged.

### 3. `route_recovery` (F-RR)

When the router chose `sql` and the SQL run ends terminal with last category `no_data_source`, and the
workspace has documents, the graph runs the documents route **once** and then the writer. "Has
documents" means `state.library_titles` is non-empty or `target_schema` is in
`cfg.pipeline.document_schemas`.
- Use `unstructured_knowledge_node` itself, unchanged.
- Add a state field `recovered_from: str | None` (A2 last-write-wins, default None). Set it to `"sql"`
  when recovery runs, and include it in `main.py`'s `state_trace`.
- In the writer's context, when `recovered_from == "sql"` and there are passages, add before the
  passages:

  ```
  The database did not hold this, so the answer comes from the document passages below. Say so in one short clause.
  ```

- Hybrid routes are not affected. A question that has already recovered never recovers again.
- Put the decision in a pure function, for example `graph._recovery_target(state) -> str`, and
  unit-test it. Test the flag, the category, the route, documents present or absent, and that it
  cannot recur.

**Off.** The graph's edges and decisions are exactly as before.

## Acceptance (the lead runs these)

- **B1, new tests.**
  - With each flag off, the SQL prompt (by equality with the base output), the writer contexts, the
    graph decisions and the `ErrorRecord` categories are unchanged. That includes "reads no table" →
    `permission_denied` when `sql_no_data` is off.
  - With each flag on, the exact texts above, the parser, the mapping, the recovery decision and the
    trace field.
- **B2.** `/opt/anaconda3/bin/python3 -m pytest -q tests` passes. So does the venv run of the tests the
  host skips (reuse `/tmp/t1venv` if it exists, or build one). List every existing test you changed and
  why.
- **B3.** `PATH=/opt/anaconda3/bin:$PATH scripts/lint-no-literals.sh` passes. Report ruff on the files
  you changed only; the repo has pre-existing ruff errors.

## Rules

- **Allowed:** `orchestrator/`, `config/flags.toml` (new flags only, default off; no existing value
  changes), `tests/`.
- **Not allowed:** `mcp/`, `web/`, `scripts/`, `config/error_taxonomy.json`, `docs/eval/`, Docker.
- Never `git stash`, never push, never touch another branch. If your worktree is not at `abf7e15`,
  reset your own worktree branch to it. Commit once, with a message starting `T2:`.
- Match the surrounding code's style. Every numeric literal is config or carries `# noqa: literals -
  <reason>`.
- Report the files changed, the tests added or changed, the commands run with their output tails, and
  anything you were unsure of.
