# T2c: every first NOT_ANSWERABLE is confirmed once, with near-matching column names

Parent: T2b (`docs/specs/partd_tasks/T2b_confirm_not_answerable.md`, commit 29f3bef). Base: the head of branch
`v3-phase3-workspaces`; run `git reset --hard v3-phase3-workspaces` on your own worktree branch first.
You implement; the lead reviews by running the checks and probing past them.

## Why (gate G1 failed again, commit af36c9e)

T2b confirms a NOT_ANSWERABLE only after a *partial* schema view. Part D runs the direct schema retriever,
which shows every table in full, so the confirmation never ran (0 of 150). The three false replies all came
with the whole schema in view:

- q522 "edhrecRank column not present in schema": the column is `cards.edhrecrank` (the Postgres copy
  lowercased every identifier); the question's hint names `edhrecRank`.
- q173 "required information about statement requests is not present": the answer is `account.frequency`.
- q1524 "no nationality column in available tables": BIRD's gold reads `gasstations.country`.

## Requirement (only with `[pipeline] sql_no_data = "on"`; off is unchanged)

1. **The first `NOT_ANSWERABLE` of a question is always unconfirmed**, whatever the view: the retriable
   `not_answerable_unconfirmed`. The partial-view condition is dropped from the decision. Keep
   `view_is_partial` only if something else still uses it; otherwise remove it and its tests.
2. **The confirming attempt** keeps T2b's full view ("Schema (every table of the dataset):", retrieved
   tables first, cut at `[sql] full_schema_max_chars`) and adds, after it, a block:
   `Columns whose names resemble words in the question or your reply:` followed by one `table.column`
   per line.
   - Words: from the question text (including any hint it carries) and the NOT_ANSWERABLE reason. A word
     is a run of letters, digits and underscores, at least 4 characters.
   - Normalise a word and a column name by lowercasing and removing `_`. A column matches when the
     normalised forms are equal, or when a normalised word of at least 5 characters is a substring of the
     normalised column name, or the column name of the word.
   - Look across every table of the dataset (the descriptions the full view already fetched, including
     tables the cap cut from the view). List matches once each, in table then column order, at most
     `[sql] near_match_max` lines (new key, default `20`, in `statesync.toml`, additive).
   - If nothing matches, the block is left out.
3. **The hint** (the overrides rule `not_answerable_unconfirmed`'s `hint`, replace the T2b text exactly):
   `You replied NOT_ANSWERABLE. Every table is described below, followed by the columns whose names
   resemble words in the question or your reply: write the SQL if any column holds what the question asks,
   even under a different name or letter case; reply NOT_ANSWERABLE again only if none does.`
   (`config/error_taxonomy_overrides.json` is read only by the new classifier, so editing this rule's hint
   is allowed. `config/error_taxonomy.json` is still not.)
4. Unchanged from T2b: a second `NOT_ANSWERABLE` is final `no_data_source`; the confirmation uses one
   attempt of `sql.retry_max`; `state.errors` keeps both records; with no attempt left, the run ends on
   `not_answerable_unconfirmed` (gate G1 counts that as a false NOT_ANSWERABLE).

## Acceptance (the lead runs these)

- **F1.** Unit tests:
  - a first NOT_ANSWERABLE with a full view gives `not_answerable_unconfirmed` (this inverts a T2b test;
    change it, and say so in the report);
  - the near-match block: `edhrecRank` in the question lists `cards.edhrecrank`; `client_id` matches
    `clientid`; a 4-character word matches only by equality; a column in a table cut from the view by the
    cap is still listed; at most `near_match_max` lines; no block when nothing matches;
  - the hint text exactly;
  - a second NOT_ANSWERABLE gives terminal `no_data_source`; flag off is unchanged.
- **F2.** `/opt/anaconda3/bin/python3 -m pytest -q tests` and `/tmp/t1venv/bin/python -m pytest -q tests`
  pass; `scripts/lint-no-literals.sh` passes.
- **F3, the lead's.** Gate G1 is rerun: false NOT_ANSWERABLE at most 1% of 150, and no significant accuracy
  drop against the off arm's measured run-to-run variance.

## Rules

- **Allowed:** `orchestrator/`, `config/error_taxonomy_overrides.json` (this rule's hint only),
  `config/statesync.toml` (the new key only), `tests/`.
- **Not allowed:** `config/error_taxonomy.json`, `mcp/`, `web/`, `scripts/`, `docs/eval/`, Docker.
- Config is additive only (old images read `config/` live). Never `git stash`, never push, never touch another
  branch. Commit once, with a message starting `T2c:`.
- Report the files changed, the tests, the commands with their output tails, and anything you were unsure of.
