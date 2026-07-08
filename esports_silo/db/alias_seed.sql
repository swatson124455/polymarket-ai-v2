-- esports_silo — curated team-alias seed for pinnodds↔Polymarket name gaps.
--
-- WHY: some teams are spelled differently on pinnodds vs Polymarket, so the two-team gate
-- fails to link the SAME match. These are NOT guesses — each was surfaced by link_report's
-- same-fixture triage where the OTHER team in the fixture matched exactly (same opponent +
-- same time slot ⇒ same match ⇒ same team). Diacritic-only gaps (e.g. Västerås↔Vasteras) are
-- handled in code (match_matcher.fold) and are NOT seeded here.
--
-- MECHANICS: the matcher keys alias_map by the name stored in `matches` (pinnodds side) and
-- looks for a `variant` inside the Polymarket question. So: canonical = the pinnodds spelling,
-- alias = the Polymarket spelling. build_alias_map lowercases + folds both.
--
-- OPERATOR: eyeball these before loading (team-identity data). Idempotent — safe to re-run.
--   psql "$SILO_DB" -f esports_silo/db/alias_seed.sql
-- Add new rows here as link_report flags more high-confidence same-fixture pairs.

INSERT INTO team_aliases (alias, canonical, game) VALUES
  -- pinnodds 'Keyd Stars' ↔ PM 'Keyd'  (same fixture vs MIBR Academy, triage score 100)
  ('Keyd',          'Keyd Stars',   'cs2'),
  -- pinnodds 'PVISION' ↔ PM 'PARIVISION'  (same fixture vs BIG, triage score 82)
  ('PARIVISION',    'PVISION',      'cs2'),
  -- pinnodds 'BB Team' ↔ PM 'BetBoom Team'  (same fixture vs FaZe, triage score 74)
  ('BetBoom Team',  'BB Team',      'cs2')
ON CONFLICT (alias, game) DO NOTHING;   -- team_aliases PK is (alias, game)
