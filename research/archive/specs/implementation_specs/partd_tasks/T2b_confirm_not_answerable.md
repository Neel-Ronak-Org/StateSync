# T2b: "not answerable" only after the agent has seen the whole schema

Parent: T2 (`docs/specs/partd_tasks/T2_sql_outcomes_recovery.md`, F-S1). Base: the head of branch
`v3-phase3-workspaces`; run `git reset --hard v3-phase3-workspaces` on your own worktree branch first.
You implement; the lead reviews by running the checks and probing past them.

## Why (gate G1 failed)

With `sql_no_data = "on"`, 2 of 150 answerable held-out BIRD questions got a false `NOT_ANSWERABLE`
(`docs/eval/partd_g1_bird.json`, commit 68e9d0b): "missing edhrecRank column in schema" and "No
nationality column in available tables". Both columns exist.

The agent saw only the tables and columns that table selection gave it, and concluded that the data was
absent. `NOT_ANSWERABLE` is terminal, so the correction loop never ran. That loop is what would have
shown it the full table, as it does for an `unknown_column` error today. The bar is 1% or less.

## Requirement (only with `sql_no_data = "on"`; off is unchanged)

1. **A partial view.** On a `NOT_ANSWERABLE` reply, the view was partial when the tables the writer was
   shown are fewer than the dataset's tables (`schema_list_tables` for the question's schema), or when
   any shown table's description was cut.
   - Partial view: record a **retriable** outcome, a new taxonomy category `not_answerable_unconfirmed`
     (in `config/error_taxonomy_overrides.json`, which only the new classifier reads; add it to the
     `ErrorCategory` literal). It must not be terminal, so the existing correction loop runs.
   - Full view already: the reply is final, `no_data_source`, as now.
2. **The next attempt's context** adds every table of the dataset, described as `table_descriptions`
   are today (`schema_describe_table`), cut to a new `[sql] full_schema_max_chars` (default `12000`,
   in `statesync.toml`, additive). Its remediation hint (the overrides rule's `hint`, exactly):
   `You replied NOT_ANSWERABLE after seeing only part of the schema. Every table is described below:
   write the SQL if any table holds what the question asks; reply NOT_ANSWERABLE again only if none does.`
3. **A second reply.** A second `NOT_ANSWERABLE` from the same question, after that full view, is final:
   `no_data_source`, terminal, as T2 records it.
4. **Bounded.** The confirmation uses at most one attempt of the existing `sql.retry_max` budget.
   Nothing loops.
5. **Trace.** `state.errors` keeps both records, so the trace shows the confirmation happened.

## Acceptance (the lead runs these)

- **F1.** Unit tests:
  - a partial view gives `not_answerable_unconfirmed` and is retriable;
  - a full view gives `no_data_source`;
  - the next attempt's context holds every table and is cut at the cap;
  - a second `NOT_ANSWERABLE` gives terminal `no_data_source`;
  - with `sql_no_data` off, everything is unchanged (existing T2 tests still pass).
- **F2.** `/opt/anaconda3/bin/python3 -m pytest -q tests` and `/tmp/t1venv/bin/python -m pytest -q tests`
  pass. The literal lint passes.
- **F3, the lead's.** Gate G1 is rerun: false `NOT_ANSWERABLE` must be 1% or less of 150, and there must
  be no significant accuracy drop.

## Rules

- **Allowed:** `orchestrator/`, `config/error_taxonomy_overrides.json` (add the rule only),
  `config/statesync.toml` (the new key only), `tests/`.
- **Not allowed:** `config/error_taxonomy.json` (old images read it live), `mcp/`, `web/`, `scripts/`,
  `docs/eval/`, Docker.
- Config is additive only. Never `git stash`, never push, never touch another branch. Commit once, with
  a message starting `T2b:`.
- Report the files changed, the tests, the commands with their output tails, and anything you were
  unsure of.
