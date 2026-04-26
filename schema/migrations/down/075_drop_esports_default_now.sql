-- Reversal of 075_esports_default_now.sql. Manual rollback only.
-- Drops the SQL DEFAULTs that the up migration installs.
--
-- DO NOT invoke as part of a standard rollback. The S195 fix at
-- bulk_upsert_team_aliases (commit 4f63ff5) sets created_at = NOW() explicitly
-- in the INSERT, so application code is no longer dependent on the SQL
-- DEFAULT. The DEFAULTs in 075 are belt-and-suspenders behind that explicit
-- value, NOT load-bearing. Rolling them back leaves both rails intact.
--
-- Use this script ONLY when intentionally returning the schema to its
-- pre-S195 state (e.g. cherry-picking 074 onto a different branch without
-- the 075 follow-up).
--
-- Python-side defaults on the SQLAlchemy models still apply at the ORM layer.

ALTER TABLE esports_unmatched_predictions
    ALTER COLUMN event_time DROP DEFAULT;

ALTER TABLE esports_team_aliases
    ALTER COLUMN created_at DROP DEFAULT;
