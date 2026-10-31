# T5b: the dependent hybrid's SQL half returns names, and titles match names that contain them

Parent: T5 (`docs/specs/partd_tasks/T5_hybrid_split.md`). Base: the head of branch `v3-phase3-workspaces`;
run `git reset --hard v3-phase3-workspaces` on your own worktree branch first. You implement; the lead
reviews by running the checks and probing past them.

## Why (hybrid end to end, commit 40d6f3d)

With `hybrid_entities = "on"`, the documents half narrowed to the right filings whenever the SQL half's
rows held a company *name* ("Coca-Cola" → `COCACOLA 2022 10K`). It failed in two ways:
- **Ticker only.** The SQL half returned only a code (`KO`), which no title contains.
- **Name longer than the title's name.** An entity `JPMORGAN CHASE & CO` does not occur in the title
  `JPMORGAN 2022 10K`, although the title's name (`JPMORGAN`) occurs in the entity.

This was designed after seeing those results, on the development use of the routing set; the paper says so.

## Requirement (only with `[pipeline] hybrid_entities = "on"`; off is unchanged)

1. **Ask for names.** In `hybrid_plan_node`, when `docs_depend_on_sql` is true, append to the SQL
   subtask, after one space, exactly:
   `Return each entity's full name as well as any code or identifier.`
   This applies whether the split came from the front door (`subtasks`) or the decomposer. It is
   not appended when the flag is off, when the documents half does not depend on the SQL half, or
   when the subtask already ends with that sentence.
2. **Match a title's name inside an entity.** `match_titles` keeps its current rule (the entity's
   letters and digits occur in the title's) and adds one more: a document matches when its **title
   name** occurs in the entity's letters and digits, and the title name is at least
   `[hybrid] title_name_min_chars` characters long (new key, default `4`, in `statesync.toml`, additive),
   or is equal to the entity.
   - **Title name:** the title's words before the first word that is a 4-digit year or starts with one
     (`2022`, `2023Q2`), reduced to lower-case letters and digits. `COCACOLA 2022 10K` → `cocacola`;
     `JOHNSON JOHNSON 2023 8K dated …` → `johnsonjohnson`; `3M 2022 10K` → `3m`. A title with no year
     word uses all its words.
   - Order and duplicates as today: documents in the order given, each once.
3. The trace (`hybrid_entities`) is unchanged in shape.

## Acceptance (the lead runs these)

- **F1.** Unit tests:
  - the sentence is appended exactly once, for both split sources, only when the flag is on and the
    documents depend on the SQL half;
  - title names: the three examples above, a title with no year word, a quarter word (`2023Q2`);
  - matching: `JPMORGAN CHASE & CO` matches `JPMORGAN 2022 10K`; `3M` matches `3M 2022 10K` (equality);
    a 3-character title name does not match by containment (`"AES"` title, entity `"AESOP CORP"`);
    the old rule still matches (`Coca-Cola` → `COCACOLA 2022 10K`);
  - flag off: the plan node and the documents half behave exactly as before.
- **F2.** `/opt/anaconda3/bin/python3 -m pytest -q tests` and `/tmp/t1venv/bin/python -m pytest -q tests`
  pass; `PATH=/opt/anaconda3/bin:$PATH scripts/lint-no-literals.sh` passes.
- **F3, the lead's.** The routing set's 24 hybrid questions are asked again on the entity arm
  (`scripts/eval_hybrid.py`), and compared with the T5 run.

## Rules

- **Allowed:** `orchestrator/`, `config/statesync.toml` (the new key only, in `[hybrid]`), `tests/`.
- **Not allowed:** `config/flags.toml`, `config/error_taxonomy*.json`, `mcp/`, `web/`, `scripts/`,
  `docs/eval/`, Docker.
- Config is additive only. Never `git stash`, never push, never touch another branch. Commit once, with a
  message starting `T5b:`.
- Report the files changed, the tests, the commands with their output tails, and anything you were unsure of.
