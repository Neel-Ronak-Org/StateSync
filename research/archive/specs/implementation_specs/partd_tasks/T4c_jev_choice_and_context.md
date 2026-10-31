# T4c: Jev as one choice question, and multi-turn context without an LLM call

Parent: T4b (`docs/specs/partd_tasks/T4b_jev_front_door.md`). Owner's direction, 2026-10-06: try Jev with a single
choice question; plan multi-turn context with no LLM call for both front doors; no normaliser (Hindi/Hinglish
handling was for ONDC and is out of scope here).

## Why

The T4b arms ask Jev independent yes/no questions. On the routing set's dev split a hybrid question and an
"either" question get the same probabilities (needs_database 0.94 vs 0.93, needs_documents 0.58 vs 0.54), so no
threshold separates them: 1-2 of 12 hybrid questions on test, 0 of 12 for the two-question replay. A single
choice question makes Jev weigh the sources against each other in one judgement.

## Part 1 (implement now): `pipeline.front_door = "jev_choice"`

- **No LLM call and no normaliser before routing.** The evidence is gathered as today (`gather_evidence`: the
  search text is the new question plus the previous turn's question; the earlier turns go into the state).
- **One Jev request** over `jev_state(evidence, question)` (the raw question; the conversation in the state)
  with two questions:
  - `route` (type `choice`), instructions "Which sources are needed to answer the new question?", criteria
    (fixed text, constants in `front_door.py`):
    - `sql`: "Only the database: the answer is a stored figure, or a calculation over the database's tables."
    - `docs`: "Only the documents: the answer is in what the filings say (explanations, risks, segments,
      or figures the database does not hold)."
    - `both`: "Both: one part of the answer comes from the database and another part from the documents."
    - `neither`: "Neither: the database and the documents do not hold it (other companies, the future, live
      data, or not about this data)."
  - `documents_depend_on_database` (type `noul`), the T4b text and `jev_depends` threshold.
- **Route = the chosen option**, `both` → hybrid. **No fitted threshold** (nothing is tuned on dev for this arm).
- The jev client gains choice support: a question may be `{"type": "choice", "instructions": ..., "criteria":
  {...}}`; the answer keeps `choice`, `probabilities` and `confidence`. Existing noul use is unchanged.
- `Parsed`: needs_database / needs_documents are derived from the route (sql → yes/no, docs → no/yes,
  both → yes/yes, neither → no/no); standalone_question = the raw question; uses_history = false
  (no detection); no subtasks.
- Jev error → `decide_llm` fallback, as T4b.
- Trace: arm `"jev_choice"`, `jev: {choice, probabilities, confidence, depends, model, ms, usage, calls: 1}`.
- Tests: the request carries one choice and one noul question with the fixed text; each choice maps to its
  route; no LLM call is made; the Jev error fallback; "jev", "jev_rw", "llm", "off" unchanged.
- Allowed: `orchestrator/`, `tests/`, `config/flags.toml` (the `front_door` comment only). Nothing else. Commit
  once, message starting `T4c:`.

**Evaluation (the lead).** `scripts/eval_routing.py --arm jev_choice` on dev, then test, with **raw earlier
questions** as history (`--history raw`; an arm that rewrites nothing never has gold standalone questions), and
the LLM front door re-run with raw history too, so the comparison is like for like. Primary: test route
accuracy vs the LLM front door, exact McNemar.

## Part 2 (plan; built after Part 1's result): context with no extra LLM call

Both front doors, and the downstream writers, get the conversation without a separate rewriter call:
1. **Routing.** Earlier turns (raw question, route, answered, the start of the answer) are in the front door's
   input: the LLM front door's one call already resolves follow-ups inside it; Jev gets them in its state.
2. **Search.** The document search text is the new question plus the previous turn's question (already so).
3. **Answering.** When the new question is a follow-up, the SQL writer and the documents writer get one line,
   "Earlier in this conversation: <previous question> (<route>)", so the model that writes the answer resolves
   "And in 2022?" itself. A follow-up is detected deterministically: no company, ticker or filing name from the
   alias table in the question, or an anaphor ("it", "its", "that company", "same", "what about", "and in").
4. **Carry-over (deterministic, a safety net).** A small session record of the last company, metric, year and
   filing (alias table built from the database's companies and metrics and the library's titles; years by
   regex) fills a follow-up's missing slots into the search text.

Measures, on the routing set's 50 conversation turns (gold standalone questions exist): follow-up detection
accuracy; slot recovery (company, year, metric of the gold standalone present in the carried state); turn route
accuracy per arm; and, end to end, turn answer accuracy against the same arms with the shipped rewriter.
