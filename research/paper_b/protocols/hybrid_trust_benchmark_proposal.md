# Proposal: a hybrid trust benchmark (database + documents)

Status: **proposal, 2026-10-03.** Next project, after the evidence-tiers study
(`evidence_tiers_study.md`). Not a pre-registration yet: the literature check below was a quick
search, and a proper review comes first (see Prerequisites).

## Why

StateSync answers from a database and from documents. Existing hybrid benchmarks grade the
**answer**:
- FDABench: 2,007 tasks, databases plus PDFs and other media, every task needing both sources;
- DataSpace: 410 verifiable tabular tasks;
- UniDataBench, DI-Bench and WorkSurface-Bench: enterprise agents over tables, documents and metrics;
- EnterpriseRAG-Bench: conflicting information, but between documents only;
- TAG and SWAN: database questions that need knowledge from outside the database.

From what we read, none of them tests:
1. **The two sources disagreeing about the same fact.** A database value against a filing's
   (restated) figure: does the system notice, and does it know which source governs?
2. **Whether the system's own warnings ("this might be wrong") are right.** That is the current
   study's question, carried over to hybrid answers.

## Research questions

- **RQ1.** How often do hybrid systems answer confidently when the database and the documents
  disagree, and how often do they detect the conflict?
- **RQ2.** Which warning signals catch wrong hybrid answers, at the same flag rate?
  - model-free data checks;
  - an independent-source cross-check (database vs filing);
  - LLM judges;
  - self-consistency.
- **RQ3.** Do systems abstain on unanswerable hybrid questions, or invent an answer?

## Design

**Sources (primary): SEC.** Both are US-government public domain:
- XBRL company facts → PostgreSQL (`demo/sec_load.py`, which already keeps first-reported and
  restated values: `annual_metrics`, `annual_metrics_restated`);
- the same companies' 10-K filings.

Natural conflicts:
- restatements;
- tag-mapping differences;
- segment vs consolidated figures;
- fiscal vs calendar years.

**Sources (optional): a fictional company,** with generated tables and documents and conflicts
planted on purpose. Exact labels and no contamination, but less realistic; used only for a small
stress-test section, reported separately.

**Item types** (target mix, about 200 items):

| type | share | example |
|---|---|---|
| database only | 20% | Revenue in FY2021 from the facts table |
| documents only | 20% | What reason did management give for margin decline? |
| needs both | 25% | Revenue growth, and the reason the filing gives for it |
| sources disagree | 20% | Database first-reported revenue ≠ restated figure in a later 10-K |
| unanswerable | 15% | A figure neither source contains |

**Answer key per item:**
- the answer;
- the gold SQL (if structured);
- the evidence span(s): document, page, text;
- the item type;
- for conflicts: both values, which source governs, and why;
- for unanswerables: why.

**Metrics:**
- answer accuracy (execution match / span-supported);
- conflict detection (precision and recall);
- abstention on unanswerables;
- warning quality at a matched flag rate, as in the current study;
- cost and latency.

## How it is built (quality is the whole value)

1. Items are drafted from rules fixed in advance (sampling of companies, years and metrics; how
   conflicts are found), not by browsing for convenient cases.
2. Every item is checked by two people independently. Agreement (kappa) is reported; disagreements
   are resolved and logged.
3. Gold SQL is executed. Evidence spans are verified against the filing text. Conflict items are
   verified in both sources.
4. Frozen with a hash before any system is run. Published with a datasheet and the build scripts.

## Baselines

- StateSync, shipped and with its checks on;
- StateSync with the database-vs-filing cross-check (`scripts/eval_sec_crosscheck.py`) as a
  verifier;
- a documents-only RAG baseline;
- an LLM judge;
- self-consistency.

At least two answer models.

## Effort and output

About 3–5 weeks:
- literature review (1);
- sources and build rules (1);
- items and two-person checking (1–2);
- baselines and write-up (1).

Output: a workshop dataset/benchmark paper and an open dataset.

## Risks

- **Novelty:** a 2026 benchmark may already test database-vs-document conflicts. The literature
  review decides; if so, we narrow to the warning-quality angle or drop.
- **Label errors:** the main threat (BIRD's original labels were wrong about half the time). Hence
  two-person checking and a published agreement figure.
- **Too few natural conflicts** in the chosen companies and years. Mitigation: sample companies
  with known restatements first, and top up with the synthetic section, reported separately.

## Prerequisites

- **The current study finished.** Its tooling (matched-flag-rate analysis, blind annotation,
  bootstrap) is reused.
- **A proper literature review** of the closest 5–6 papers, before any build work.
- **The owner's contact for the SEC User-Agent header,** needed to download filings beyond the 84 we
  have.
- **A check of each source's licence:** SEC data is public domain; FinanceBench's own files are not
  reused.

## Sources of the quick check (2026-10-03)

- FDABench: https://arxiv.org/html/2509.02473
- DataSpace: https://arxiv.org/html/2608.03451
- UniDataBench: https://arxiv.org/html/2511.01625
- DI-Bench: https://arxiv.org/html/2609.05776
- WorkSurface-Bench: https://pith.science/paper/2607.25765
- EnterpriseRAG-Bench: https://arxiv.org/html/2605.05253v1
- TAG: https://arxiv.org/html/2408.14717v1
- SWAN: https://vldb.org/cidrdb/papers/2025/p10-zhao.pdf
