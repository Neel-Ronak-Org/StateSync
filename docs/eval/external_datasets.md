# External evaluation datasets

Why: the RAG v2 numbers (`docs/eval/retrieval_v2_report.md`) were measured on a test set
and documents the project made itself. Some decisions (D4, D5, the abstention thresholds)
were taken after looking at that set, and its headline gain is driven by one question
template over an unusual corpus: 20 of the 21 extra top-5 hits are "What do customers say
about seller <id>?". These datasets are the out-of-sample check. They are real company
filings and data, with questions written by people outside the project.

Domain: US public companies. That is the same kind of material StateSync is built for: a
company's numbers in a database, and the same company's reports as documents.

Fetch and verify: `PATH=/opt/anaconda3/bin:$PATH bash scripts/fetch_external_eval.sh`.
Both sources are pinned to a commit and checked against
`fixtures/external_eval_checksums.txt` (242 files). The data lives in `data/external/`,
which git ignores.

## FinanceBench (documents / retrieval)

- **Source.** Patronus AI, *FinanceBench: A New Benchmark for Financial Question Answering*,
  arXiv:2311.11944 (2023). [github.com/patronus-ai/financebench](https://github.com/patronus-ai/financebench)
  at commit `cc39aeb4`.
- **Licence.** CC BY-NC 4.0, so non-commercial use only: fine for evaluation and the
  paper, not as product data.
- **What is used.** The open sample of 150 questions, plus the 84 filings they cite (of the
  368 in the repository). The filings are 64 10-Ks, 8 10-Qs, 6 8-Ks and 6 earnings
  reports from 32 companies, covering fiscal 2015–2024 (51 of the 84 are from 2021 or
  later). 158 MB, 12,013 pages; the median filing has 130 pages and the largest 549.
- **Questions.** There are three types, 50 each: `metrics-generated`, `domain-relevant` and
  `novel-generated`. Many require calculation. Each question has 1–3 evidence entries, all
  in the question's own filing. An entry has `evidence_text` (67–6,362 characters, median
  1,271) and `evidence_page_num`.
- **Page numbering is 0-based.** The evidence text appears on PDF page `evidence_page_num`
  counted from 0 in 37 of the first 40 questions, on page − 1 in 0 of them, and on neither
  in 3 (text-extraction differences). StateSync chunk pages count from 1, so the gold page
  is `evidence_page_num + 1`.
- **Product limits these filings hit.** Six filings exceed `[library] max_pages = 300`:
  AMERICANWATERWORKS_2022_10K, CVSHEALTH_2018_10K, JPMORGAN_2022_10K, PEPSICO_2021_10K,
  PEPSICO_2022_10K and WALMART_2018_10K, the largest at 549 pages. As shipped, the upload is
  refused, and 10 of the 150 questions are on these filings. No filing exceeds the 20 MiB
  size limit; the largest is 9.3 MB. The 60-second parse limit is untested on filings this
  long.

## FIBEN (database / text-to-SQL)

- **Source.** IBM, Sen et al., *ATHENA++: Natural Language Querying for Complex Nested SQL
  Queries*, PVLDB 13(11) 2020. [github.com/IBM/fiben-benchmark](https://github.com/IBM/fiben-benchmark)
  at commit `96eaffc6`.
- **Licence.** Apache-2.0.
- **What it is.** 300 English questions, each with gold SQL (237 distinct; 130 non-nested,
  170 nested). The database is a 152-table data mart modelled on the FIBO ontology:
  companies, officers, securities, and XBRL financial-statement elements. The DDL is
  `FIBEN.sql` (Postgres-compatible), and the data is 152 CSVs without headers (382 MB).
- **Coverage.**
  - 2,323 corporations.
  - Financial elements dated 2013–2019 (calendar years; 2019 partial). Rows per year: 2014
    80,598, 2015 79,851, 2016 76,418, 2017 72,659, 2018 67,873 and 2019 46,314.
- **Overlap with FinanceBench.** About 24 of the 32 FinanceBench companies appear in FIBEN.
  Amazon, Amcor, CVS Health, Costco, PayPal, Block/Square, Ulta Beauty and Walmart do not.
  The years overlap only for FinanceBench's 2015–2019 filings (24 of the 84), so FIBEN is a
  standard SQL benchmark in the same domain, not a direct link to the documents.
- **Running it needs model calls.** It costs about 3,400 tokens per question on the SQL
  model, so the 300 questions take about 1M tokens. That waits for the owner's decision on
  the Groq quota or API credits.

## SEC company facts for the FinanceBench companies (fetched 2026-10-02)

- **Source:** the SEC's XBRL "company facts" (`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`)
  for all 32 FinanceBench companies, plus `www.sec.gov/files/company_tickers.json`. It is
  public US-government data.
- **Size:** 124 MB, with 340–918 us-gaap tags per company.
- **Request header:** sec.gov refuses requests without a contact in the User-Agent. The
  owner approved using their name and email for this.
- **CIKs:** company → CIK is in `data/external/sec/financebench_companies.json`. Activision
  Blizzard (718877) and Foot Locker (850209) are no longer in the ticker list, so their CIKs
  were entered from EDGAR.
- **The link works.** 3M's FY2018 `PaymentsToAcquirePropertyPlantAndEquipment` in the 10-K
  facts is 1,577,000,000, which is exactly FinanceBench's gold answer ($1,577M) for its first
  question.
- **Snapshot, not a pinned version.** These files grow with every new filing, so they are a
  dated snapshot: checksums are in `fixtures/sec_companyfacts_2026-10-02.sha256`. A later
  fetch will differ.

## Plan

1. **FinanceBench retrieval** (no model calls): `docs/specs/external_eval.md` Part 1.
2. **FIBEN text-to-SQL** (needs model calls), loaded as a connected Postgres database
   through the product's own "connect your database" path: Part 2, not yet specified.
3. **Linked SEC database** and cross-source agreement: data fetched; not yet specified.
