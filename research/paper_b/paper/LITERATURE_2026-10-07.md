# Paper B: literature check (2026-10-07)

Question: after the audit (`AUDIT_2026-10-07.md`), what in our work is new, and would a hybrid SQL + documents
benchmark be new? Searched arXiv (API, all titles with "jev"; targeted queries) and the web. Abstracts read for
the closest papers; full text for none yet, so every "nobody has done X" below means "we found nothing", not
"it does not exist".

## 1. Typed decision models (Jev and similar)

Jev was released on 15 Sep 2026. By 7 Oct there are about 80 arXiv papers on it. The general claim "a typed
decision model can replace LLM calls that only decide" is **already made**, many times:

| Paper | What it shows | Bearing on us |
|---|---|---|
| Deußer et al., *Evaluating and Benchmarking the System One Model Jev*, arXiv:2609.37647 | 37 datasets incl. routing; beats Qwen3.8-27B on 27; calibrated; binary probabilities rank well but are poor at a fixed 0.5 threshold | General benchmark; our 0.5-threshold trigger result is consistent |
| *Fast Models, Slow Evidence*, arXiv:2610.02267 | 11 agent decision points; Jev ties Laya on **RAG relevance gating**; neither beats chance on **model routing**; self-audit of their own analysis errors | Closest to our re-ranking and routing roles, but gating (relevant or not), not ranking pages in long filings, and model routing, not database-vs-documents routing |
| *JEV-as-a-Judge: Accept When Confident, Escalate When Unsure*, arXiv:2609.26550 | Jev within 3 points of a frontier judge where a verdict can be read off the text; falls behind where it must be **derived (math, code, logic)**; a confidence cascade matches the frontier judge at 41% of the fee | Same cascade idea as our "Jev triggers the SQL fallback"; SQL is exactly the "derived" case they flag as hard, and they do not test it |
| *Do System One Decisions Add Up? Probabilistic Coherence*, arXiv:2609.33971 | Breaking one decision into smaller steps makes Jev disagree with itself; reconstruction costs up to 22.9 points | Explains our finding that separate yes/no routing questions fail where one joint choice works |
| *Beyond Calibration: Probability Axioms*, arXiv:2609.33209 | Jev over-endorses single-label statements (three single-label probabilities sum to 1.14) | Same mechanism: independent "needs the database?" and "needs the documents?" both run high |
| *Labels Override Definitions*, arXiv:2610.02586; *Type-Safe Is Not Error-Free*, arXiv:2609.26758 | Typed decision models follow option labels more than their written definitions | Our routing options' labels and descriptions; our wording change helped documents-only questions, so this needs care in the write-up |
| *When the Right Answer Is Missing*, arXiv:2609.39496 | Jev rarely rejects when no option is right (7%), but Boolean verification works (99%) | Relevant to the answerability gate (use a yes/no question, not a "none" option) |
| *Same-Number Citation Swaps: Jev as a Financial Evidence Judge*, arXiv:2610.08675 | Jev verifying financial calculation citations | Close to our weak "document answer checker"; we should drop ours or cite this |
| *SearchJev*, arXiv:2610.05107 | A trained System-1 model for six search decisions (routing, relevance, sufficiency…); Jev as baseline | Relevance judgments in search agents; no SQL, no databases, no page finding |
| *Decision-Oriented Recommendation Reranking*, arXiv:2609.40241 | Jev re-ranking recommendations | Re-ranking, but recommendations, not passages |

**Not found:** any Jev (or typed decision model) paper on text-to-SQL (answer verification, fallback
triggering or answerability), on re-ranking passages of long documents against a cross-encoder, or on routing
between a database and a document collection.

## 2. Text-to-SQL verification and fallback (unchanged from the protocol, §10)

LEVER (ICML 2023), CHASE-SQL (ICLR 2025), GradeSQL (arXiv:2509.01308), Richardson 2026 (arXiv:2607.06799: judge
AUROC 0.72–0.78, two-provider ensemble 0.82), FrugalGPT, Jin et al. 2026 (corrected BIRD labels). Our cascade is
not a new method. What is new is the trigger being a zero-shot typed decision model and the measurement on
corrected labels.

## 3. Routing between a database and documents

Bai et al. 2025 (arXiv:2510.02388, rule-driven routing over hybrid sources); an XGBoost router with threshold
refusal over structured and unstructured sources (Electronics 15(9):1845, 2026; full text not accessible);
semantic and logical routers in practice; LLM-model routers (RouteLLM, LLMRouter) route between models, not
sources. A typed decision model as the source router, compared with an LLM router and a k-NN router on a
held-out set with "both" and "neither" classes, was not found.

## 4. Re-ranking

Pointwise LLM re-ranking by P(yes) is an established method (e.g. Zhuang et al. 2023; arXiv:2508.16757 compares
LLM re-rankers with cross-encoders). Our contribution there can only be the measurement: a hosted typed decision
model against a GPU cross-encoder for finding the right page in long financial filings.

## 5. Hybrid database + documents benchmarks

| Benchmark | Sources | What it tests |
|---|---|---|
| FDABench (arXiv:2509.02473) | databases + 1,600 documents and media, 2,007 tasks | data agents' answers over heterogeneous data |
| HybridDeepResearch (arXiv:2609.09410, Sep 2026) | LiveSQLBench databases + the open web, 380 tasks | passing constraints between SQL and search; 50–54% Pass@8 for the best models |
| TAG / TAG-Bench (CIDR 2025), SWAN (CIDR 2025) | BIRD databases + world knowledge | questions beyond the database |
| HybridQA, TAT-QA, FinQA, SUQL | tables + text, usually within one document or page | reasoning over a table and its text |
| UniDataBench, DataSpace, DI-Bench, WorkSurface-Bench | enterprise tables, documents, metrics | agent answers |
| ConflictQA (doi:10.1145/3805712.3809650), ConflictBank, WikiContradict | text vs knowledge graph, text vs text | knowledge conflicts |

A generic SQL + RAG benchmark **is not new**. Two angles were not found: (1) the database and the documents
disagreeing about the same fact (an XBRL value against a restated figure in a later 10-K), and (2) whether a
system's own "this might be wrong" warnings on hybrid answers are right. Building that credibly needs
two people labelling independently and about 3–5 weeks (`docs/research/hybrid_trust_benchmark_proposal.md`).

## 6. Where we can contribute

1. **Typed decision models for text-to-SQL** (no prior paper found): zero-shot Jev as a detector of wrong SQL
   answers against an LLM judge and fine-tuned verifiers on corrected BIRD labels, and as the trigger of a
   detect-then-fallback cascade. It also tests JEV-as-a-Judge's claim that derived verdicts are hard.
2. **Database-vs-documents routing with a typed decision model**: one joint choice against separate yes/no
   questions, against an LLM router and a k-NN router, on a fresh held-out set; the failure of the decomposed
   form is predicted by the coherence papers, which we can test in a new setting.
3. **Page finding in long filings**: Jev against a cross-encoder and dense search, measured fairly.
4. **Answerability with hard negatives**, as a yes/no question (the form the rejection paper says works).
5. A hybrid benchmark with database-vs-document conflicts is a credible **next** paper, not this one.
