# Spec: table selection for large databases (pre-registered 2026-10-02)

## Why

The SQL agent is shown every table of the dataset. On FIBEN (152 tables, `demo/fiben_db.sh`)
that came to 8,300–9,000 tokens per SQL call, above Groq's free-tier 8,000 tokens per minute,
so every question was refused before it ran (`docs/eval/external_datasets.md`). A real
enterprise database is larger still. State-of-the-art text-to-SQL systems link the question
to a few relevant tables first ("schema linking").

## Design

- **Provider.** A second schema-retriever provider, `[schema_retriever] provider = "linked"`.
  Shipped stays `"direct"`. It uses the existing seam (`orchestrator/schema_retriever.py`, A8),
  so the SQL agent does not change.
- **Small schemas are unchanged.** With at most `max_tables_full` tables (20), `linked` shows
  every table, exactly like `direct`.
- **Large schemas: a table card per table,** built once per (schema, fingerprint) and cached
  in process like the column profiles. It is made of:
  - the table name and column names, split into words (snake_case and camelCase);
  - column comments;
  - the example values from `column_profiles` (so "Alphabet" can point to `listedsecurity`).
- **Ranking.** BM25 (the same formula as the RAG keyword leg, k1 = 1.2, b = 0.75) over the
  cards, scored against the question's words.
  - Tokens are the words plus their character 4-grams (for words of 4 or more characters).
    Legacy and ontology schemas run words together (`haslasttradedvalue`), so whole-word
    matching alone cannot connect "traded" to them. This was added before any measurement.
  - No model call, no new MCP tool (the tool ceiling is 20/20), deterministic.
- **The selection.**
  - The top `top_tables` (8);
  - plus every table one foreign-key hop away from them, so join paths survive;
  - plus every table used by the workspace's matched approved examples;
  - capped at `max_tables_selected` (14). The ranked tables come first, then the joined
    neighbours by rank.
- **Retries** (`attempt` > 0) widen the selection by one more foreign-key hop, still capped,
  instead of today's "full schema on retry".
- **A shown-table note.** The prompt says how many tables exist and that only the most
  relevant are shown, so the model does not assume the list is complete.

## Measurement (pre-registered)

1. **Offline, no model calls.** For every runnable FIBEN gold query (264), and for the Olist
   and ONDC gold SQL in `fixtures/golden`, does the selection contain every table the gold SQL
   uses? Reported:
   - gold-table recall: all gold tables present, per question;
   - the share of gold tables present;
   - the average number of tables shown;
   - the size of the schema text in characters, against the full schema.

   Values are fixed: `top_tables` = 8, `max_tables_selected` = 14, one foreign-key hop. If
   recall is below 90%, report it as measured; do not tune on FIBEN.
2. **Live, Groq.** The 5-question FIBEN trial (`research/verifier`'s `trial` pattern) through
   the app, with `provider = "linked"`: do questions now run within the free-tier limit, and
   how many are correct?

## Tests

- tokenising snake_case and camelCase;
- BM25 ranking on a toy schema;
- one-hop foreign-key expansion and the cap;
- the small-schema passthrough is identical to `direct`;
- approved-example tables are always included;
- retry widening;
- a refused describe is still skipped (`tests/test_identifiers_and_schema_skip.py`).

## Results and post-hoc changes

1. **v1, as pre-registered** (`docs/eval/table_selection_fiben_v1.json`), on 153 runnable FIBEN gold
   queries:
   - 13.4 of 152 tables shown;
   - schema text 4,593 characters against 40,270 for the full schema (89% smaller);
   - **every gold table selected for 46% of questions; 72% of gold tables selected.**

   Cause: the misses are join intermediaries (corporation, person, financialreport,
   monetaryamount). Character 4-grams match widely and favour FIBEN's many short, empty
   concept tables. Neighbours were added in global rank order, so the best table's partners
   could fall past the cap: listedsecurity ranked 1st, but monetaryamount was cut.
2. **Post-hoc v2, decided after seeing v1 on this same set:**
   - empty tables (no sampled rows) rank after every table with rows;
   - foreign-key neighbours are expanded best table first.

   Both are general rules, but they were chosen after looking at FIBEN, so v2's number is
   in-sample. A second large schema is needed before claiming it generalises.
3. **v2 result** (`docs/eval/table_selection_fiben_v2.json`; 111 of FIBEN's 152 tables are empty):
   - 14 tables shown;
   - 4,946 characters of schema;
   - **every gold table selected for 60% of questions (v1: 46%); 81% of gold tables selected
     (v1: 72%).**

   Still below the 90% target. The offline test does not use the two live-path aids, which both
   still need measuring:
   - the workspace's approved-example tables, which are always included;
   - retry widening.

## v3 protocol (pre-registered 2026-10-02, before any v3 code or per-question look at the split)

v2 is at 60% all-gold recall against the 90% target, and further changes chosen on FIBEN would
be tuned to it. So:
- **Split.** A FIBEN question is **dev** when the SHA-256 of its `qid` (as a string) ends in an
  even hex digit, **held-out** otherwise. v3 is designed by looking only at dev questions.
- **Held-out is measured once,** after v3 is frozen and committed, with v2 measured on the
  same split for comparison. The held-out number is the one reported. A second run after
  any change is reported as a deviation.
- **Caveat stated up front.** v1 and v2 were shaped by the whole set in aggregate (the
  "join intermediaries" diagnosis), so held-out is not pristine: it is free of per-question
  tuning, not of all influence. A second large schema (BIRD, once downloaded with the owner's
  permission) is still the real test.
- **Constraints.** No model call, no new MCP tool, deterministic; the cap
  (`max_tables_selected` = 14) and `top_tables` stay; only general mechanisms (nothing that
  names a FIBEN table or column).

### v3 development log (dev half only; recorded before the held-out run)

1. **First design** (value index fused with the card ranking by RRF, k = 60, plus join-path
   completion between the top 3 tables): dev all-gold **19.5%**, against v2's 59.7%. Components
   measured separately on dev: path completion changed nothing (59.7%); the value ranking alone
   caused the drop (18.2%).
2. **Diagnosis on dev:** prose columns (disclosures, descriptions) put ordinary English words in
   the index; question words the schema already names ("value" inside `haslasttradedvalue`) and
   numbers matched; the 1,000-value cap cut entity columns (MSFT and IBM fell outside it); and
   fusion let value matches displace the card ranking's top tables.
3. **Frozen v3** (general rules, nothing naming a FIBEN table): index only values of at most 4
   words; skip question words whose first 5 letters occur in a table or column name, and pure
   numbers; keep up to 10,000 values per column; ADD the best 2 value-matched tables after the
   top 8 instead of fusing; no path completion (it added nothing). Dev all-gold **72.7%**, gold
   share 88.6% (v2: 59.7%, 82.9%). Choices tried on dev and recorded: values of ≤2 words also
   gave 72.7%; adding 1 or 3 tables gave 67.5–72.7%.
