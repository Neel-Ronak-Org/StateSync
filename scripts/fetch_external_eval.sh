#!/usr/bin/env bash
# scripts/fetch_external_eval.sh
# Fetches the two public datasets used to evaluate StateSync on data nobody on the
# project wrote (docs/eval/external_datasets.md), and verifies them byte-for-byte
# against fixtures/external_eval_checksums.txt before anything uses them.
#
# WHY. The RAG v2 numbers (docs/eval/retrieval_v2_report.md) were measured on a
# test set and documents the project made itself, and some decisions were taken
# after looking at that set. These datasets are the out-of-sample check: real SEC
# filings with questions written by others.
#
#   FinanceBench (Patronus AI, arXiv:2311.11944): the 150-question open sample and
#     the 84 filings (10-K, 10-Q, 8-K, earnings) it cites. Licence CC BY-NC 4.0 --
#     NON-COMMERCIAL: fine for evaluation and the paper, not as product data.
#   FIBEN (IBM, Sen et al., PVLDB 13(11) 2020): 300 English questions with gold SQL
#     over a 152-table database of public-company financials (SEC XBRL, 2013-2019).
#     Licence Apache-2.0.
#
# Both sources are pinned to a commit, so the bytes cannot drift under the checksums.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${REPO_ROOT}/data/external"
FB_COMMIT="cc39aeb4afdf33909ee1412188bf89035950c2eb"
FIBEN_COMMIT="96eaffc60cc824b8f545cd29adffc2a170e08d04"
FB_RAW="https://raw.githubusercontent.com/patronus-ai/financebench/${FB_COMMIT}"
FIBEN_RAW="https://raw.githubusercontent.com/IBM/fiben-benchmark/${FIBEN_COMMIT}"

mkdir -p "${DEST}/financebench/data" "${DEST}/financebench/pdfs" "${DEST}/fiben"

fetch() {  # fetch <url> <dest>: skip a file that is already there
  if [[ -s "$2" ]]; then return; fi
  curl -sSfL --retry 3 --max-time 600 -o "$2" "$1"
}

echo "==> FinanceBench (open sample)"
fetch "${FB_RAW}/README.md" "${DEST}/financebench/README.md"
for f in financebench_open_source.jsonl financebench_document_information.jsonl; do
  fetch "${FB_RAW}/data/${f}" "${DEST}/financebench/data/${f}"
done
# Only the filings the 150 questions cite (84 of the 368 in the repository).
python3 - "${DEST}/financebench/data/financebench_open_source.jsonl" > "${DEST}/financebench/used_docs.txt" <<'EOF'
import json, sys
print("\n".join(sorted({json.loads(line)["doc_name"] for line in open(sys.argv[1])})))
EOF
while read -r doc; do
  encoded="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "${doc}")"
  fetch "${FB_RAW}/pdfs/${encoded}.pdf" "${DEST}/financebench/pdfs/${doc}.pdf"
done < "${DEST}/financebench/used_docs.txt"

echo "==> FIBEN"
for f in FIBEN.sql FIBEN_Queries.json LICENSE; do
  fetch "${FIBEN_RAW}/${f}" "${DEST}/fiben/${f}"
done
if [[ ! -d "${DEST}/fiben/data" ]]; then
  fetch "${FIBEN_RAW}/data.zip" "${DEST}/fiben/data.zip"
  (cd "${DEST}/fiben" && unzip -q -o data.zip -x '__MACOSX/*' && rm data.zip)
fi

echo "==> verifying against fixtures/external_eval_checksums.txt"
cd "${DEST}"
if shasum -a 256 -c "${REPO_ROOT}/fixtures/external_eval_checksums.txt" >/dev/null 2>&1; then
  echo "==> PASS: all $(wc -l < "${REPO_ROOT}/fixtures/external_eval_checksums.txt" | tr -d ' ') files match the committed checksums."
else
  echo "==> FAIL: checksum mismatch (a truncated download, or the source changed)." >&2
  shasum -a 256 -c "${REPO_ROOT}/fixtures/external_eval_checksums.txt" | grep -v ': OK$' || true
  echo "    Refusing to proceed: measuring over unverified data is worse than not measuring." >&2
  exit 1
fi
