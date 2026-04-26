-- Reversal of 074_esports_team_aliases.sql. Manual rollback only.

DROP INDEX IF EXISTS idx_eup_recent;
DROP INDEX IF EXISTS idx_eup_match_dedup;
DROP TABLE IF EXISTS esports_unmatched_predictions;

DROP INDEX IF EXISTS idx_eta_canonical;
DROP INDEX IF EXISTS idx_eta_alias_lower;
DROP INDEX IF EXISTS idx_eta_alias_lc;
DROP TABLE IF EXISTS esports_team_aliases;
