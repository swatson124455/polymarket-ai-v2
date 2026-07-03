# esports_silo — Session Handoff

## Where things stand
The siloed esports scaffold **and** the Cmd-4 data-quality gate (`verify_data_quality.py`) are
committed and pushed. **Nothing trades; no data has cleared quarantine.** The gate is BUILT and
its `--jsonl` mode has been run read-only over the carried data; next is the operator running the
DB gate on the box (D2 GATE-THEN-BUILD), then signal design. See `PLAN.md`.

## Repo state
- Branch: `claude/blissful-davinci-twt397-n94qo6` (current working branch)
- HEAD: tip of that branch (see `git log --oneline` — do not pin a stale SHA here)
- Get it: `git fetch origin claude/blissful-davinci-twt397-n94qo6 && git checkout claude/blissful-davinci-twt397-n94qo6`
- All silo work is in `esports_silo/` — self-contained, designed to extract to its own repo + DB.

## Committed files
`COMMANDMENTS.md` · `PLAN.md` · `HANDOFF.md` · `START_PROMPT.md` · `README.md` · `config.py` ·
`.env.example` · `requirements.txt` · `db/schema.sql` · `collectors/odds_collector.py` ·
`scripts/validate_keys.py` · `scripts/import_from_prior_bot.py` · `scripts/verify_data_quality.py`
Plus carried data now IN GIT (commit `369606b`): `data/esports_matches_bulk.jsonl`,
`data/cs2/pandascore_cs2.json` (other data — LoL CSVs, the 3.3G paper log — stays box/VPS only).

## VERIFIABILITY FRAME — do not skip
The prior EB effort logged **186+ errors** from "confident before verified." Carry these tags:
- **✅ VERIFIED from the repo** (trust): all code shapes, schemas, which loaders populate which
  fields, the commandments, the winner-map deviation, `esports_match_maps` has no writer.
- **📄 DOC-SOURCED** (from prior-bot docs, sourced to live queries — **RE-VERIFY, not fact**):
  all row counts, the failure Brier/corr numbers, every quarantine landmine below.
- **⛔ UNVERIFIABLE from the silo** (needs the operator's box): all live data quality,
  aggregator coverage, key validity, network. Silo has **no VPS/DB/API/network access**
  (`curl` → 403). The two carried data files (`data/esports_matches_bulk.jsonl`,
  `data/cs2/pandascore_cs2.json`) ARE in git and locally readable, so `verify_data_quality.py
  --jsonl` runs in-session; the DB gate still needs the box. Never claim you ran anything that
  needs the box.

## Commandments (full text: `COMMANDMENTS.md`)
1. **P&L is not evidence.** 2. **De-vig does not exist.** 3. **Surgical cut.** 4. **Quarantine by default.**

## Binding session directives (D1–D4)
Newer than the original scaffold docs; where older text conflicts, these win. Cited across the
silo as D1–D4 — defined here so a fresh session has them without reading commit history.
- **D1 — No broken pulls.** Extract only clean, self-contained logic. Never carry source that
  embeds banned behaviour — P&L (Cmd 1), Shin/de-vig (Cmd 2), the `edge>0` bet rule, or
  mis-oriented data axes. Standard formulas may be *referenced*; such code is rebuilt
  from-scratch. Adaptations are labelled DEVIATION, never "port" (Cmd 3). (Consequence:
  build items #4 metrics + #6 Kelly are from-scratch, not surgical-pulls.)
- **D2 — Gate-then-build.** Nothing new is built until the operator runs
  `scripts/verify_data_quality.py` on the box and data clears quarantine. No parallel build
  ahead of the gate.
- **D3 — Sharp books decided.** Pinnacle, Singbet, Sbobet via OddsPapi — LIVE-VERIFIED
  2026-07-03 against `/v4/bookmakers`: slugs `pinnacle`/`singbet`/`sbobet` exist; `thunderpick`
  (the original third pick) is NOT carried, so sbobet (the researched Asian fallback) replaced it.
  `polymarket` is itself a slug in their feed; `ps3838`/`pin88` are `cloneOf` pinnacle — never
  double-count clones. Same-day live probes also verified: the full v4 API contract
  (camelCase params, `from`/`to` ≤10 days on /fixtures, ≤3 books on /historical-odds, market
  "171"=match winner with outcome 171=participant1/172=participant2), AND archive depth —
  pinnacle history reaches ≥6 months back (January BLAST fixture returned full line history;
  softs age out ~2 weeks; old fixtures may be down-sampled to one pre-start tick). The
  calibrator backfill is BUILT: `scripts/backfill_historical_odds.py` (see GO_LIVE_CHECKLIST).
- **D4 — Data in git.** `data/esports_matches_bulk.jsonl` + `data/cs2/pandascore_cs2.json` ARE
  committed (`369606b`) and locally readable; other data (LoL CSVs, the 3.3G paper log) stays
  source-machine/VPS only. All carried data remains quarantined until the gate passes.

## Known landmines — 📄 DOC-SOURCED (quarantine per Cmd 4 until re-verified)
Master reference: **`EB_CLEAN_DATA_QUARANTINE.md`** (authoritative clean-vs-dirty table list —
e.g. "do NOT compute model-vs-market on `esports_predictions`; use `prediction_log` esports_*,
orientation valid, tiny n").
- `esports_predictions` model-vs-market orientation broken (corr 0.07)
- `shadow_fills` microstructure garbage (86¢ spreads)
- `category='esports'` ~60% politics-polluted → filter by content, never the tag
- `model_version='v2-trinity-contaminated'` (35 rows deleted)
- `esports_odds` empty (0 rows) → no historical CLV backtest; forward-collect (table name per migration 072 / manifest; `pinnacle_odds` is a *column* on `esports_predictions`, not a table)
- `esports_match_maps` empty (no writer) → per-map data only partial, in `esports_training_data.game_state_json->>'map_name'`
- Ratings model (Trinity/Glicko/per-game ML) dead — no edge

## Surgical-pull sources (✅ verified present in repo)
- winner mapping → `esports_v2/data/normalizer.py`
- match/alias shapes → migrations `072`/`074`, `esports_v2/scripts/load_matches_to_db.py`, `esports_v2/data/oracle_loader.py`
- market matcher → `esports/markets/esports_market_scanner.py` (two-team gate — logic verified correct ✅ `:180,313`)
- metrics — ⚠ **NOT a code pull** (D1). `esports_v2/backtest/metrics.py` embeds `compute_pnl`
  (`:160`, banned Cmd 1) and a shin-devigged CLV (`clv.py:35`, banned Cmd 2). #4 is written
  from-scratch; standard formulas (Brier `mean((p−y)²)`, log-loss, ECE, closing-line delta) may
  be *referenced*, not carried. ("mis-oriented Brier" was wrong: `compute_brier:92` is standard;
  the mis-orientation is a *data-axis* issue in `esports_predictions`, not the formula.)
- Kelly — ⚠ **sizing NOT a code pull** (D1). `esports/kelly/esports_bankroll_manager.py:82-83`
  embeds the banned `edge>0` bet rule inside the sizing fn. #6 is from-scratch; textbook Kelly
  `f*=edge/odds` may be referenced; caps/graduation re-specified as requirements, not code.
- prior-bot postmortems → `EB_REBUILD_CARRYFORWARD.md` (master), `EB_MODEL_EDGE_PROPOSAL_2026-06-16.md` (eb/main)

## Open decisions & blockers
See `PLAN.md` (§Open decisions, §Blockers). Devig=dead, optional-cols=skipped are settled.
Sharp books are DECIDED (Pinnacle, Singbet, Thunderpick via OddsPapi) pending operator
ratification of live esports coverage; branch reconciliation is done on this branch. All
data/network work is operator-run.

## Next action
✅ `esports_silo/scripts/verify_data_quality.py` is BUILT (Cmd-4 master gate) — read-only. Its
`--jsonl` mode has been run over the carried `data/esports_matches_bulk.jsonl` (which surfaced
and fixed a cross-source false-FAIL, commit `140fbb7`); on that data the gate currently reports
QUARANTINE on **1/28205** rows whose winner contradicts its score (operator excludes/fixes that
row, re-runs). The **DB gate** still needs the operator's box. No carried data leaves quarantine
until the gate passes there.

**Operator, run on the box** (both read-only):
- `DATABASE_URL=postgresql://…/silo python -m esports_silo.scripts.verify_data_quality`
  → the master gate over `matches` + `team_aliases` (+ any populated forward tables).
- `python -m esports_silo.scripts.verify_data_quality --jsonl data/esports_matches_bulk.jsonl`
  → pre-import vetting of the carried NDJSON before it reaches the DB.
Exit 0 = gate PASS (data may leave quarantine); 1 = QUARANTINE; 2 = could-not-run. Add
`--json` for machine output. Thresholds are `VDQ_*` env vars (see the script header).

Then, per `PLAN.md` and **D2 GATE-THEN-BUILD** — ONLY after the operator has run the battery on
the box and data clears quarantine: build #2 (Polymarket snapshot collector), #3 (market↔match
matcher), #4 (P&L-free skill-eval harness, from-scratch per D1); design #5 (the signal) on
whatever clears the battery. Nothing new is built ahead of the gate.
