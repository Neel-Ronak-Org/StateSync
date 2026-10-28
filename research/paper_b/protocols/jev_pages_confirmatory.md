# Jev as a passage re-ranker: confirmatory test (pre-registration)

Status: fixed 2026-10-07, before any question of the held-out set exists. The exploratory run on FinanceBench
(`docs/eval/jev_pages.json`, commit 8fd7ac8) motivated it; FinanceBench was the development set for the Part D
fixes, so that run cannot confirm anything.

## Question

Does re-ordering the documents route's retrieved passages by Jev's answer to "Does this passage contain the
information needed to answer the question?" put the right page first more often than the evaluated retrieval
(dense search, bge re-ranker reading titles, relevance order)?

## Held-out set (built by `scripts/build_page_set.py`, seed 20261007)

- **Pages.** From the 84 filings of the FinanceBench library (folder `fd_6478612a7d314c91`), pages with at least
  600 characters of text, sampled at random, at most 4 per document; excluded: every FinanceBench evidence page
  and the pages next to it (±1).
- **Questions.** One per page, written by `gemini-3.8-flash` (the grader's model, never Jev) from the page's text
  and the filing's name: a question a financial analyst would ask, answerable from that page alone, naming the
  company and the fiscal year or period as FinanceBench questions do, not copying the page's sentences; with a
  short answer.
- **Check.** A second model (`gpt-oss-120b`, Cheaper Inference) reads the page and the question and says whether the
  page answers it, specifically; items it rejects, and items whose writer reply does not parse, are dropped
  before any retrieval. Target 200 kept items; the first 200 kept in sampling order are used.
- **Gold.** The source page `(document, page)`. Other pages may also answer a question; that biases every arm the
  same way (all re-order the same candidates).

## Arms (the candidates are identical across arms; only their order differs)

For each question, the documents route's two-round search (as `scripts/eval_writer_window.py` runs it) returns up
to 25 passages, twice:
- **D**: dense search, relevance order (config `rel_off`);
- **B**: bge re-ranker reading titles, relevance order (`rel_bge_titles`, the evaluated setting);
- **JD**: D's 25 re-ordered by Jev's P(yes);
- **JB**: B's 25 re-ordered by Jev's P(yes).

Jev: `jev-1.13` (OpenCode Zen), one noul question per passage, with exactly the exploratory run's state text
(question, passage title, page, first 3,000 characters) and instructions; ties keep the recorded order.

## Measures

- **page first**: the gold page is the first passage;
- **window**: the gold page is among the first `semantic_top_k` = 8 (what the answer writer reads);
- reported, not tested: document first, page anywhere in the 25.

## Hypotheses and tests (exact McNemar, two-sided, alpha 0.05)

- **Primary, H-P1:** JB vs B on *page first*.
- **Secondary (Holm over the three):** H-S1 JB vs B on *window*; H-S2 JD vs B on *page first*; H-S3 JD vs B on
  *window* (Jev without the GPU re-ranker vs the GPU re-ranker).
- Items where a search failed in either configuration are dropped from every comparison and counted.

## Fixed now, not after

The seed, the filters, the writer and checker prompts (in the script, committed before it runs), Jev's text and
model, the arms, the measures, the hypotheses and the tests. Any change after the set exists is logged below with
its reason, and the paper reports it.

## Change log

(none)

## Results (2026-10-07; nothing above was changed after the set existed)

Set: 200 questions (278 of 280 sampled pages kept; the checker dropped 2, both unparsed replies, so it filtered
little). Searches: 200 of 200 in both configurations, 400 re-ranked searches, 0 failed. Jev: 6,220 passages, $0.17.

| Arm | page first | window (8) | document first | page anywhere (25) |
|---|---|---|---|---|
| D dense | 0.105 | 0.565 | 0.580 | 0.725 |
| JD Jev over dense | 0.510 | 0.710 | 0.880 | 0.725 |
| B bge + titles (evaluated setting) | 0.130 | 0.750 | 0.875 | 0.785 |
| JB Jev over bge + titles | **0.545** | 0.760 | 0.895 | 0.785 |

- **H-P1 (primary), JB vs B, page first: confirmed.** 0.545 vs 0.130, +85 / -2, exact McNemar p = 4.9e-23.
- H-S1, JB vs B, window: not confirmed (0.760 vs 0.750, +4 / -2, Holm p = 0.69).
- H-S2, JD vs B, page first: confirmed (0.510 vs 0.130, +81 / -5, p = 9.6e-19, Holm p < 3e-18).
- H-S3, JD vs B, window: not significant (0.710 vs 0.750, +5 / -13, Holm p = 0.19); a difference this test cannot
  rule out, so "Jev replaces the GPU re-ranker for the writer's window" is not claimed.
