# esports_silo — Session Handoff

## Where things stand
A scaffold of a siloed esports rebuild is committed and pushed. **No data has been touched;
nothing trades.** The next session builds the data-quality gate (`verify_data_quality.py`),
then designs the signal. See `PLAN.md`.

## Repo state
- Branch: `claude/blissful-davinci-twt397`
- HEAD: `dd425f1`
- Get it: `git fetch origin claude/blissful-davinci-twt397 && git checkout claude/blissful-davinci-twt397`
- All work is in `esports_silo/` — self-contained, designed to extract to its own repo + DB.

## Committed files
`COMMANDMENTS.md` · `PLAN.md` · `HANDOFF.md` · `START_PROMPT.md` · `README.md` · `config.py` ·
`.env.example` · `requirements.txt` · `db/schema.sql` · `collectors/odds_collector.py` ·
`scripts/validate_keys.py` · `scripts/import_from_prior_bot.py`

## VERIFIABILITY FRAME — do not skip
The prior EB effort logged **186+ errors** from "confident before verified." Carry these tags:
- **✅ VERIFIED from the repo** (trust): all code shapes, schemas, which loaders populate which
  fields, the commandments, the winner-map deviation, `esports_match_maps` has no writer.
- **📄 DOC-SOURCED** (from prior-bot docs, sourced to live queries — **RE-VERIFY, not fact**):
  all row counts, the failure Brier/corr numbers, every quarantine landmine below.
- **⛔ UNVERIFIABLE from the silo** (needs the operator's box): all live data quality,
  aggregator coverage, key validity, network. Silo has **no VPS/DB/API/data-file access**
  (`curl` → 403). Never claim you ran anything that needs the box.

## Commandments (full text: `COMMANDMENTS.md`)
1. **P&L is not evidence.** 2. **De-vig does not exist.** 3. **Surgical cut.** 4. **Quarantine by default.**

## Known landmines — 📄 DOC-SOURCED (quarantine per Cmd 4 until re-verified)
- `esports_predictions` model-vs-market orientation broken (corr 0.07)
- `shadow_fills` microstructure garbage (86¢ spreads)
- `category='esports'` ~60% politics-polluted → filter by content, never the tag
- `model_version='v2-trinity-contaminated'` (35 rows deleted)
- `pinnacle_odds` empty (0 rows) → no historical CLV backtest; forward-collect
- `esports_match_maps` empty (no writer) → per-map data only partial, in `esports_training_data.game_state_json->>'map_name'`
- Ratings model (Trinity/Glicko/per-game ML) dead — no edge

## Surgical-pull sources (✅ verified present in repo)
- winner mapping → `esports_v2/data/normalizer.py`
- match/alias shapes → migrations `072`/`074`, `esports_v2/scripts/load_matches_to_db.py`, `esports_v2/data/oracle_loader.py`
- market matcher → `esports/markets/esports_market_scanner.py` (two-team gate — logic assessed correct 📄)
- metrics (strip de-vig CLV + mis-oriented Brier B3) → `esports_v2/backtest/metrics.py`
- Kelly sizing → `esports/kelly/esports_bankroll_manager.py`
- prior-bot postmortems → `EB_REBUILD_CARRYFORWARD.md` (master), `EB_MODEL_EDGE_PROPOSAL_2026-06-16.md` (eb/main)

## Open decisions & blockers
See `PLAN.md` (§Open decisions, §Blockers). Devig=dead, optional-cols=skipped are settled;
Asian-book pick and branch reconciliation are open; all data/network work is operator-run.

## Next action
Build `esports_silo/scripts/verify_data_quality.py` — the Cmd-4 master gate. No carried
data may be used until it passes on the operator's box.
