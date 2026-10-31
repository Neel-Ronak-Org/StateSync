# Retrieval baseline, 2026-10-01 (scripts/eval_retrieval.py, no LLM calls)

Labelled questions already in the repo: 15 from fixtures/golden/phase_e1.json that name
expected chunks (supplier agreements, SLA annexures, policies) and 20 olist review-digest
probes (fixtures/golden/review_probe_set.json, "What do customers say about seller <id>?").
Corpus: public.document_chunks, tenant t_capstone_default (20,245 chunks), exact search,
top 8 (= retrieval.semantic_top_k). Document-level hit@k / MRR@8; chunk recall@8 on e1.

| Condition | all hit@1 | all MRR | e1 hit@1 | e1 chunk recall@8 | reviews hit@8 |
|---|---|---|---|---|---|
| dense (today) | 40.0% | 0.410 | 93.3% | 76.7% | 0% |
| dense + query instruction | 34.3% | 0.369 | 80.0% | 80.0% | 0% |
| keyword (Postgres ts_rank_cd) | 37.1% | 0.455 | 86.7% | 76.7% | 100% (rank 8, by luck) |
| hybrid (RRF k=60, dense+instruction & keyword) | 48.6% | 0.499 | 86.7% | 86.7% | 20% |

Findings:
- Dense retrieval never finds a document by an ID it contains (0/20 seller-ID probes).
- Postgres ts_rank_cd has no term rarity (IDF): for a seller-ID probe the any-word query
  matched 2,577 documents and ranked the right one 8th; the ID counted no more than "seller".
- Hybrid fusion helps overall (MRR 0.41 -> 0.50, e1 chunk recall 77% -> 87%) even with that
  weak keyword leg; a rarity-weighted (BM25-like) keyword leg is the next step.
- The mxbai query instruction did not help on this set (e1 hit@1 93% -> 80%); not adopted.
- Small sets (15 + 20): directional only. A 100-question labelled set including uploaded
  documents is needed before any change ships.
