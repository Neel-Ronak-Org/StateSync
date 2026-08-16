-- migrations/006_revert_column_semantics.sql
--
-- REVERTS migration 005. The column comments were a measured regression.
--
-- 005's reasoning was sound and its spot-checks looked good: the model was
-- filtering `locations.city_local` (local-script) for English city names,
-- which cannot match, and `describe_table` had been surfacing an empty
-- `comment` field since Phase B. Populating it fixed exactly that -- verified
-- live: `city = 'Bengaluru'` instead of `city_local = 'Bengaluru'`, and
-- delivery delay measured against `promised_at` instead of `placed_at`.
--
-- Then it was measured properly, and the full picture was worse:
--
--                        before 005     after 005
--   D-V1 control          40.0%          50.0%
--   D-V1 treatment        65.0%          55.0%     <-- the arm that ships
--   D-V1 lift            +25.0pp         +5.0pp
--   D-V2 entity pres.     81.25%         62.5%     <-- the serious one
--   English reference     60.0%          55.0%
--   M4 cold (independent) 28.0%          28.0%     (no improvement)
--   M4 warm               32.0%          24.0%
--
-- MECHANISM, visible in the D-V2 detail: दिल्ली, बेंगलुरु and चेन्नई all flipped
-- to preserved=False. The comment "an English city name will never match
-- city_local" reads, to the model, as "prefer the English column" -- so for a
-- Hindi query naming दिल्ली it transliterates to 'Delhi' and filters `city`.
-- That destroys the preserved entity, which is precisely what D-V2 measures
-- and precisely what Phase D's normalization design exists to protect. The
-- change improved the English half of the fixture by biasing against the
-- multilingual half. Net negative on the arm that actually ships.
--
-- Reverted rather than re-tuned: revising the wording to be
-- script-neutral is a plausible fix, but it is a hypothesis, and validating
-- it costs another full 28-minute measurement. Shipping an unvalidated
-- revision of a change already measured as harmful would repeat the mistake.
-- Recorded as an open item with the hypothesis stated, so the next attempt
-- starts from evidence instead of from scratch.
--
-- D4 SAFETY: comments are not fingerprint inputs (verified in both
-- directions -- cf4caa5c... before 005, after 005, and after this revert), so
-- neither applying nor reverting invalidates a stored trajectory.

COMMENT ON COLUMN locations.city IS NULL;
COMMENT ON COLUMN locations.city_local IS NULL;
COMMENT ON COLUMN locations.digipin IS NULL;
COMMENT ON COLUMN locations.pincode IS NULL;

COMMENT ON COLUMN fulfillments.promised_at IS NULL;
COMMENT ON COLUMN fulfillments.delivered_at IS NULL;
COMMENT ON COLUMN fulfillments.attempt_count IS NULL;
COMMENT ON COLUMN fulfillments.status IS NULL;

COMMENT ON COLUMN sellers.category IS NULL;
COMMENT ON COLUMN catalog_items.category IS NULL;
COMMENT ON COLUMN disputes.category IS NULL;

COMMENT ON COLUMN sellers.contracted_sla_hours IS NULL;

COMMENT ON COLUMN orders.placed_at IS NULL;
COMMENT ON COLUMN orders.order_channel IS NULL;
COMMENT ON COLUMN orders.order_value IS NULL;

COMMENT ON COLUMN disputes.resolved_at IS NULL;

COMMENT ON COLUMN delivery_events.event_type IS NULL;
COMMENT ON COLUMN delivery_events.note IS NULL;

COMMENT ON COLUMN sellers.display_name IS NULL;
COMMENT ON COLUMN sellers.display_name_local IS NULL;
COMMENT ON COLUMN catalog_items.title IS NULL;
COMMENT ON COLUMN catalog_items.title_local IS NULL;
