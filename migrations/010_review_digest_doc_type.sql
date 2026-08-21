-- migrations/010_review_digest_doc_type.sql
--
-- Adds 'review_digest' to documents.doc_type's CHECK constraint, so a
-- per-seller review-digest document can be ingested without violating it.
-- Cycle 005 Workstream C (audit 005; owner direction 2026-08-21: "the review
-- ingest, redesigned").
--
-- WHY THIS IS SAFE FOR A5. The schema fingerprint (mcp/fingerprint.py,
-- migrations/003_fingerprint.sql) hashes table names, column names, column
-- data types, column nullability, and foreign keys -- it does not read
-- pg_constraint's CHECK definitions at all. Dropping and re-adding a CHECK
-- constraint changes none of those five inputs. Measured, not just argued:
-- the fingerprint was computed immediately before this migration
-- (cf4caa5cd33e028624b3c8eaa98c75f7350b2b10761ca631d5eaa79e7096b11a, matching
-- the live schema_versions row) and is re-verified immediately after, both
-- values recorded in docs/cycles/005.md §4. Per the frozen C1 criterion, an
-- unequal fingerprint halts Workstream C and asks the owner before anything
-- else in C proceeds -- migrations 005/006 changed a real column and DID
-- move the fingerprint, which is why that revert is an owner decision
-- (§4.3) and this migration is designed specifically not to repeat that
-- shape.
--
-- 'review_digest' is a new document TYPE, not a new document SOURCE --
-- documents.tenant_id already exists and is what makes this reversible: the
-- ingest for this corpus uses a tenant_id distinct from t_capstone_default
-- (C3), so the whole corpus can be removed with one
-- `DELETE ... WHERE tenant_id = ...` (C5) without touching this constraint
-- or any other migration.

ALTER TABLE documents DROP CONSTRAINT documents_doc_type_check;
ALTER TABLE documents ADD CONSTRAINT documents_doc_type_check
  CHECK (doc_type IN ('policy', 'sop', 'supplier_agreement', 'sla_annexure', 'review_digest'));
