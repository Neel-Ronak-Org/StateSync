#!/usr/bin/env bash
# scripts/fetch_olist.sh
# Fetches the Brazilian E-Commerce Public Dataset by Olist -- REAL transactional
# data (~1.45M rows) -- and verifies it byte-for-byte against committed
# checksums before anything loads it.
#
# WHY CHECKSUMS RATHER THAN COMMITTING THE DATA. 121MB is too large to commit,
# and this project's whole claim is that a number traces to a run. A number
# measured over data nobody can reproduce is not traceable, so the exact bytes
# are pinned here instead. If the mirror changes, this FAILS rather than
# silently measuring different data.
#
# SOURCE: the mara/mara-olist-ecommerce-data mirror, which documents its own
# provenance (Kaggle: olistbr/brazilian-ecommerce) and licence. Kaggle itself
# requires auth, which a script cannot assume.
#
# LICENCE: CC BY-NC-SA 4.0 -- NON-COMMERCIAL. Fine for this capstone and for
# benchmarking. NOT usable as the data behind a commercial deployment. See
# docs/limitations.md.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${REPO_ROOT}/data/olist"
BASE="https://raw.githubusercontent.com/mara/mara-olist-ecommerce-data/master/data/olist-ecommerce"
FILES=(olist_customers_dataset.csv olist_orders_dataset.csv olist_order_items_dataset.csv
       olist_order_payments_dataset.csv olist_order_reviews_dataset.csv olist_products_dataset.csv
       olist_sellers_dataset.csv olist_geolocation_dataset.csv product_category_name_translation.csv)

mkdir -p "${DEST}"
for f in "${FILES[@]}"; do
  if [[ -f "${DEST}/${f}" ]]; then echo "  have ${f}"; else
    echo "  fetching ${f} ..."; curl -sSL --max-time 600 -o "${DEST}/${f}" "${BASE}/${f}"; fi
done

echo "==> verifying against fixtures/olist_checksums.txt"
cd "${DEST}"
if shasum -a 256 -c "${REPO_ROOT}/fixtures/olist_checksums.txt" >/dev/null 2>&1; then
  echo "==> PASS: all 9 files match the committed checksums."
else
  echo "==> FAIL: checksum mismatch. The mirror has changed, or a download is truncated." >&2
  shasum -a 256 -c "${REPO_ROOT}/fixtures/olist_checksums.txt" || true
  echo "    Refusing to proceed: measuring over unverified data is worse than not measuring." >&2
  exit 1
fi
echo ""
echo "Next:"
echo "  docker compose exec -T postgres psql -U statesync_migrator -d statesync -f - < migrations/007_olist_real_data.sql"
echo "  bash scripts/load_olist.sh"
