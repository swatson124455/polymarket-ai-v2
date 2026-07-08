-- esports_silo — READ-ONLY: what do the CS2 alias-gap keys currently map to?
-- Run before deciding whether alias_seed.sql's rows need an explicit UPDATE (the seed's plain
-- INSERT no-ops because these (alias, game) keys already exist in the imported data).
--   psql "$SILO_DB" -f esports_silo/db/alias_inspect.sql
SELECT alias, canonical, game
  FROM team_aliases
 WHERE game IN ('cs2', '')
   AND (lower(alias)     IN ('keyd', 'parivision', 'betboom team', 'bb team')
     OR lower(canonical) IN ('keyd stars', 'pvision', 'bb team', 'betboom team'))
 ORDER BY alias;
