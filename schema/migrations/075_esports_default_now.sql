-- S195 follow-up: lift the manual ALTER TABLE hot-patch from 2026-04-25 into
-- version control so fresh installs no longer require operator intervention.
--
-- Context: ORM Base.metadata.create_all runs at first DB session use and
-- creates tables based on SQLAlchemy Column(...) defs, but DOES NOT emit
-- SQL DEFAULTs (Python-side default= is not the same as a SQL default).
-- Migration 074 runs AFTER the ORM has already created the tables; its
-- CREATE TABLE IF NOT EXISTS is therefore a no-op, and the DEFAULT NOW()
-- declared in 074 never reaches the actual columns.
--
-- Symptom: bulk_upsert_team_aliases raw INSERT without explicit created_at
-- hit NotNullViolationError on prod. Same shape for event_time on the
-- esports_unmatched_predictions table when the matcher logged candidates.
--
-- Fix combination (both needed for defence in depth):
--   1. bulk_upsert_team_aliases sets created_at = NOW() explicitly in commit
--      4f63ff5 — survives drift on any future ORM-vs-migration ordering.
--   2. This migration lifts the ALTER TABLE that was applied directly to
--      prod, so the columns themselves carry the DEFAULT going forward.
--
-- Idempotent: ALTER TABLE ... SET DEFAULT is naturally repeatable.
-- Reversibility: see schema/migrations/down/075_drop_esports_default_now.sql.

ALTER TABLE esports_team_aliases
    ALTER COLUMN created_at SET DEFAULT NOW();

ALTER TABLE esports_unmatched_predictions
    ALTER COLUMN event_time SET DEFAULT NOW();
