-- down/072_esports_v2.sql
-- Rollback: Drop all Phase 5v2 esports tables

DROP TABLE IF EXISTS esports_odds CASCADE;
DROP TABLE IF EXISTS esports_predictions CASCADE;
DROP TABLE IF EXISTS esports_features CASCADE;
DROP TABLE IF EXISTS esports_ratings CASCADE;
DROP TABLE IF EXISTS esports_players CASCADE;
DROP TABLE IF EXISTS esports_matches CASCADE;
