#!/usr/bin/env bash
# demo/sec_db.sh
# A local Postgres holding the SEC's reported figures for the 32 FinanceBench companies, so the same
# facts can be asked of a database (SQL) and of the companies' filings (documents): the linked
# finance test, and the database-against-document cross-check (docs/eval/external_datasets.md).
# Not part of StateSync: a separate container on localhost:5435 with a read-only login.
#
#   ./demo/sec_db.sh          (re)create it (needs scripts/fetch_external_eval.sh + the SEC snapshot)
#   ./demo/sec_db.sh stop     remove it
#
# Tables (schema sec):
#   companies(cik, ticker, name, financebench_name)
#   facts(cik, taxonomy, tag, label, unit, period_start, period_end, value, filing_fy, filing_fp,
#         form, filed, accession, frame)   -- every reported value, restatements included
#   metric_tags(metric, tag, priority)     -- friendly metric names over us-gaap tags
#   annual_metrics (view)                  -- one value per company, metric and fiscal period
#         AS FIRST REPORTED (the earliest 10-K carrying it), so it matches the filing documents:
#         durations of about a year (or a year-end instant for balance-sheet items), the
#         highest-priority tag that exists.
#   annual_metrics_restated (view)         -- the same, but the LATEST 10-K wins (restatements,
#         e.g. 3M's 2022 revenue is 34.2B as reported and 26.2B after the 2024 Solventum spin-off).
#   NOTE: facts.filing_fy is the fiscal year of the FILING, not of the value; the views read the
#   period dates.
#
# In StateSync (Library -> Databases -> Add database):
#   Host host.docker.internal   Port 5435   Database sec   User statesync_reader
# (Demo-only values for a throwaway local database; the reader password is below.)
set -euo pipefail
NAME=statesync-sec-db
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-/opt/anaconda3/bin/python3}"
if [[ "${1:-}" == "stop" ]]; then docker rm -f "$NAME" >/dev/null 2>&1 && echo "removed $NAME"; exit 0; fi
[[ -d "${REPO_ROOT}/data/external/sec/companyfacts" ]] || { echo "no SEC snapshot in data/external/sec" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
"$PY" "${REPO_ROOT}/demo/sec_load.py" "$TMP"

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p 127.0.0.1:5435:5432 \
  -e POSTGRES_DB=sec -e POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  postgres:16-alpine >/dev/null
echo -n "waiting for Postgres"
until docker exec "$NAME" pg_isready -U postgres -d sec >/dev/null 2>&1; do echo -n "."; sleep 1; done
sleep 2; echo

docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d sec <<'SQL'
CREATE SCHEMA sec;
CREATE TABLE sec.companies (cik bigint PRIMARY KEY, ticker text, name text NOT NULL, financebench_name text NOT NULL);
CREATE TABLE sec.facts (
  cik bigint NOT NULL REFERENCES sec.companies (cik), taxonomy text NOT NULL, tag text NOT NULL, label text,
  unit text NOT NULL, period_start date, period_end date NOT NULL, value numeric NOT NULL,
  filing_fy int, filing_fp text, form text, filed date, accession text, frame text);
COMMENT ON COLUMN sec.facts.filing_fy IS 'fiscal year of the FILING that reported the value, not of the value itself';
SQL
docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d sec -c "\\copy sec.companies FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')" < "$TMP/companies.tsv"
docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d sec -c "\\copy sec.facts FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')" < "$TMP/facts.tsv"

docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d sec <<'SQL'
CREATE INDEX facts_lookup ON sec.facts (cik, tag, period_end);
CREATE TABLE sec.metric_tags (metric text NOT NULL, tag text NOT NULL, priority int NOT NULL, PRIMARY KEY (metric, tag));
INSERT INTO sec.metric_tags VALUES
  ('revenue','Revenues',1), ('revenue','RevenueFromContractWithCustomerExcludingAssessedTax',2), ('revenue','SalesRevenueNet',3),
  ('cost_of_revenue','CostOfRevenue',1), ('cost_of_revenue','CostOfGoodsAndServicesSold',2),
  ('gross_profit','GrossProfit',1),
  ('operating_income','OperatingIncomeLoss',1),
  ('net_income','NetIncomeLoss',1),
  ('eps_diluted','EarningsPerShareDiluted',1),
  ('research_and_development','ResearchAndDevelopmentExpense',1),
  ('depreciation_and_amortization','DepreciationDepletionAndAmortization',1), ('depreciation_and_amortization','DepreciationAndAmortization',2),
  ('operating_cash_flow','NetCashProvidedByUsedInOperatingActivities',1),
  ('capital_expenditure','PaymentsToAcquirePropertyPlantAndEquipment',1),
  ('dividends_paid','PaymentsOfDividends',1), ('dividends_paid','PaymentsOfDividendsCommonStock',2),
  ('share_repurchases','PaymentsForRepurchaseOfCommonStock',1),
  ('total_assets','Assets',1), ('current_assets','AssetsCurrent',1),
  ('total_liabilities','Liabilities',1), ('current_liabilities','LiabilitiesCurrent',1),
  ('stockholders_equity','StockholdersEquity',1),
  ('cash_and_equivalents','CashAndCashEquivalentsAtCarryingValue',1),
  ('inventory','InventoryNet',1), ('accounts_receivable','AccountsReceivableNetCurrent',1),
  ('long_term_debt','LongTermDebt',1), ('long_term_debt','LongTermDebtNoncurrent',2);

CREATE VIEW sec.annual_facts AS
SELECT f.*, m.metric, m.priority
FROM sec.facts f JOIN sec.metric_tags m ON m.tag = f.tag
WHERE f.taxonomy = 'us-gaap' AND f.form IN ('10-K', '10-K/A')
  AND (f.period_start IS NULL OR f.period_end - f.period_start BETWEEN 350 AND 380);

CREATE VIEW sec.annual_metrics AS
SELECT c.financebench_name AS company, c.ticker, l.metric, l.period_start, l.period_end,
       EXTRACT(year FROM l.period_end)::int AS period_end_year, l.value, l.unit, l.tag, l.filed, l.accession
FROM (SELECT DISTINCT ON (cik, metric, period_end) * FROM sec.annual_facts
      ORDER BY cik, metric, period_end, priority, filed, accession) l
JOIN sec.companies c USING (cik);
COMMENT ON VIEW sec.annual_metrics IS 'annual figures as first reported in a 10-K (matches the filing documents)';

CREATE VIEW sec.annual_metrics_restated AS
SELECT c.financebench_name AS company, c.ticker, l.metric, l.period_start, l.period_end,
       EXTRACT(year FROM l.period_end)::int AS period_end_year, l.value, l.unit, l.tag, l.filed, l.accession
FROM (SELECT DISTINCT ON (cik, metric, period_end) * FROM sec.annual_facts
      ORDER BY cik, metric, period_end, priority, filed DESC, accession DESC) l
JOIN sec.companies c USING (cik);
COMMENT ON VIEW sec.annual_metrics_restated IS 'annual figures from the latest 10-K carrying them (restated values)';

CREATE ROLE statesync_reader LOGIN PASSWORD 'sec-reader-pass';
ALTER ROLE statesync_reader SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE sec TO statesync_reader;
GRANT USAGE ON SCHEMA sec TO statesync_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sec TO statesync_reader;
ANALYZE;
SQL
echo "loaded: $(docker exec "$NAME" psql -At -U postgres -d sec -c "SELECT count(*) FROM sec.companies") companies, $(docker exec "$NAME" psql -At -U postgres -d sec -c "SELECT count(*) FROM sec.facts") facts"
