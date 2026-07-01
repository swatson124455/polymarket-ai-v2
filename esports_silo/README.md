# esports_silo — clean-rebuild esports forecasting bot (siloed)

Self-contained rebuild of the esports bot, designed to be lifted out into its **own
repository and its own database**. Nothing here imports from the 15-bot system, and
nothing in the 15-bot system imports from here. That isolation is the point — the
prior bot's worst failures (DB storms, schema collisions, contaminated shared tables)
came from shared state.

## Status: scaffold

This is the foundation, not a finished bot. What is present and what is deliberately
left as a marked seam:

| Piece | State |
|---|---|
| `db/schema.sql` | Complete. Clean schema from zero, append-only raw. |
| `scripts/validate_keys.py` | Complete + runnable. Verifies the 3 API keys. |
| `collectors/odds_collector.py` | Pipeline + append-only writes + coverage guard complete. The exact **odds-payload field mapping** is a marked seam — it logs the first raw response so you confirm field names against the live aggregator (could not be verified from a network-isolated session). |
| `config.py` | Complete. Env-driven. |
| The forecasting model | **Not built.** This is the one thing that must be built from the ground up (the prior model lost to the market on every game). |

## Non-negotiable commandments (baked into the design)

1. **De-vig does not exist.** No Shin-devig, no overround-stripping, anywhere. Sharp-book
   odds are stored and used **raw** (`odds_raw.team_a_odds` / `team_b_odds` are decimal
   odds as the book showed them). The old `esports_v2/model/clv.py` Shin path is dead.
2. **P&L is not evidence.** No realized-dollar figure feeds a modeling or go/no-go
   decision. The bot is judged on forecasting skill vs the market (Brier, calibration,
   closing-line agreement) — never on P&L.
3. **Append-only raw.** `odds_raw` is INSERT-only — never UPDATE, never DELETE. This
   kills the class of bug that contaminated the old tables (rows changing between
   queries, flipped orientation, injected phantom rows). Derived views are rebuilt
   from raw, never edited in place.
4. **Every fact row carries `event_time` AND `ingest_time`.** The old "matcher failures"
   were markets created *after* the prediction — a look-ahead trap. Two timestamps make
   look-ahead detectable and filterable.
5. **Never trust a `category` tag.** The old `category='esports'` was ~60% politics.
   Game membership is explicit (`matches.game`), decided by content, not a tag.
6. **Defer to the market by default.** The old bet rule (bet when `p_model − price` is
   large) selected for the model's *worst* errors. The new rule must require proven
   out-of-sample skill before deviating from price. See `predictions.decision`.

## The one open risk that gates everything

The signal is sharp-book lines (Pinnacle + Circa + one Asian book) via a single
aggregator. **It is unverified whether any one aggregator actually carries all three
books for esports.** Verify book+esports coverage against the live aggregator BEFORE
trusting the collector — esports coverage on odds aggregators is thin. `odds_collector`
logs per-`(game, book)` coverage every run so a gap can never pass silently.

## Setup

```bash
cp .env.example .env          # fill in real values (keys live in your prod .env, NOT git)
pip install -r requirements.txt
psql "$DATABASE_URL" -f db/schema.sql
python scripts/validate_keys.py          # confirm keys work + Riot key not expired
python -m collectors.odds_collector --once --dry-run   # confirm aggregator coverage
```

## Data to migrate in (from the prior bot, where it lives — not in git)

`matches` ← historical results (esports_matches / esports_matches_bulk.jsonl / Oracle CSVs),
`team_aliases` ← the 1,777-row resolver (the hardest piece to rebuild — carry it).
Do **not** carry: the ratings model, its weights, or any quarantined substrate
(`shadow_fills`, `esports_predictions` orientation, contaminated `model_version` rows).

Use `scripts/import_from_prior_bot.py` — it maps the prior `esports_matches` +
`esports_team_aliases` into the silo schema (winner-name → team_a/team_b transform
ported from the prior normalizer). Runs on your box against your DBs:
```bash
SOURCE_DATABASE_URL=postgresql://…prior  DATABASE_URL=postgresql://…silo \
    python -m esports_silo.scripts.import_from_prior_bot \
        --matches-from-db --aliases-from-db --dry-run   # drop --dry-run to write
```
