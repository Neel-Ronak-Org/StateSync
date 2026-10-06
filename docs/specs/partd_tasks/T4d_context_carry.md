# T4d: multi-turn context with no extra LLM call (`pipeline.context_carry`)

Parent: T4c Part 2 (`docs/specs/partd_tasks/T4c_jev_choice_and_context.md`). Base: the head of branch
`v3-phase3-workspaces`; run `git reset --hard v3-phase3-workspaces` on your own worktree branch first. You implement;
the lead reviews by running the checks and probing past them. No normaliser anywhere in this task.

## What it is

A follow-up ("And in fiscal 2022?", "Has it reported any legal battles?") needs the conversation. Today that costs
a separate LLM rewriter call (shipped) or happens inside the LLM front door's one call. This task resolves it with
**no extra model call**, for the front-door arms that do not rewrite the question (`jev_choice`, and any arm whose
standalone question equals the raw one):

1. **Detect** a follow-up deterministically.
2. **Carry** the missing company and year from the previous turn, including its answer ("it" in turn 2 of
   "Which of Coca-Cola, PepsiCo and General Mills had the highest revenue…? / Has it reported…?" is PepsiCo, named
   only in turn 1's answer).
3. **Give** the answering models one context line, and add the carried slots to the document search text.

## Requirement (only with `[pipeline] context_carry = "on"`; off is byte-identical)

1. **`orchestrator/context_carry.py`, pure functions:**
   - `entities(text, names) -> list[str]`: the companies a text names, matched against `names` (a list of
     canonical names) after reducing both to lower-case letters and digits (as `_letters_digits`); longest match
     first, each once, in order of appearance.
   - `years(text) -> list[str]`: four-digit years 1990-2039, and `FY22` / `FY2022` / `fiscal 22` forms, as
     four-digit strings.
   - `is_follow_up(question, previous, names) -> tuple[bool, str]`: true, with the reason, when there is a previous
     turn and either the question has an anaphor (whole words, case-insensitive: it, its, it's, they, their, them,
     that company, this company, the company, same, those, these, which ones, and in, and for, what about,
     how about, also, then) or it names no company from `names` and the previous turn did (question or answer).
   - `carry(question, previous, names) -> Carry`: `follow_up`, `reason`, `company` (the question's own, else the
     previous answer's first, else the previous question's first), `year` (the question's own, else the previous
     question's, else the previous answer's), and `line`, exactly:
     `Earlier in this conversation: "<previous question>"; its answer began: "<first 200 characters of the answer>".`
     followed, when carried, by ` Carried from it: company <company>; year <year>.` (each part only if carried).
2. **`names`**: the workspace's document title names (`title_name`, T5b) plus, when the dataset has a column named
   like `company`, `name`, `ticker` or `financebench_name` in the front door's database card, nothing more: titles
   only for now. Built once per question from the documents the front door already lists or the workspace listing
   (`_workspace_documents`); cache per workspace for the process.
3. **Where it runs**: in `front_door_node`, after the decision, when the flag is on, the evidence has a previous
   turn, and the standalone question equals the raw question (whitespace-collapsed). It sets two new state fields:
   - `conversation_context` (the `line`) when `follow_up` is true, else unset;
   - `carried` = `{company, year, reason}`.
   The trace gains `context_carry: {follow_up, reason, company, year}`.
4. **Who reads it** (only when `conversation_context` is set):
   - the SQL writer and corrector prompts (`orchestrator/nodes/sql_agent.py`) get the line after the question;
   - the documents writer and the hybrid writer get it after the question;
   - the documents search text (the documents route's query and the hybrid documents half's query) gets
     ` <company> <year>` appended for whichever was carried.
   Nothing else changes; the stored turn keeps the raw question.
5. **Config**: `config/flags.toml` `[pipeline] context_carry = "off"` (new key, comment naming this spec);
   `config/statesync.toml` `[context_carry] answer_chars = 200`. Additive only.

## Acceptance (the lead runs these)

- **F1.** Unit tests (no network):
  - `entities`/`years` on the forms above, including `JPMORGAN CHASE & CO` against a title name `jpmorgan` (title
    name inside a longer mention matches; use T5b's rule: name ≥ 4 characters or equal);
  - `is_follow_up` true for "And in fiscal 2022?", "Has it reported any legal battles?", "Which ones?", "Who are its
    primary customers?"; false for a first turn and for "What was Nike's revenue in fiscal 2023?" after a 3M turn;
  - `carry` takes the company from the previous answer before the previous question (the PepsiCo case), the year
    from the previous question, and builds the line exactly;
  - with the flag on, a follow-up in the `jev_choice` arm sets `conversation_context`, and the SQL and documents
    prompts contain it; the search text gains the carried slots;
  - with the flag on, an arm that rewrote the question (standalone ≠ raw) sets nothing;
  - flag off: prompts, search text and trace unchanged for every arm.
- **F2.** Both suites and the literal lint pass.
- **F3, the lead's.** On the routing set's 50 conversation turns: follow-up detection against `depends_on_history`;
  carried company and year against the gold standalone question; then answers end to end on the 48 turns with gold
  answers, `jev_choice` + carry vs `jev_choice` without vs the LLM front door vs shipped (rewriter), graded by the
  FinanceBench grader.

## Rules

- **Allowed:** `orchestrator/`, `config/flags.toml` (the new key only), `config/statesync.toml` (the new section only),
  `tests/`.
- **Not allowed:** `mcp/`, `web/`, `scripts/`, `docs/eval/`, `secrets/`, Docker, `config/error_taxonomy*.json`.
- Never `git stash`, never push, never touch another branch. Commit once, message starting `T4d:`. Report the files
  changed, the tests, the commands with their output tails, and anything you were unsure of.
