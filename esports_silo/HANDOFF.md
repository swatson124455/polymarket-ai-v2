# esports_silo — Session Handoff

## ⚡ SOURCE PIVOT + LIVE ON THE BOX (2026-07-06) — read this first
The build is no longer box-blocked; it is RUNNING on the box (`/home/ubuntu/esports_silo_src`,
silo DB `esports_silo`, gate PASSED, 32,369 matches imported). Two source decisions changed and
are LIVE-VERIFIED:

- **Odds source = pinnodds (Pinnacle), NOT OddsPapi.** OddsPapi gated Pinnacle to B2B. pinnodds
  is a self-serve Pinnacle wrapper: base `https://pinnodds.com/kit/v1`, header `x-portal-apikey`,
  **esports = sport_id 11**, one `/markets?sport_id=11&event_type=prematch` call returns every
  esports event with raw money_line odds (num_0 period = match winner; ~20 req/window limit).
  `collectors/pinnodds_collector.py`. Auto-pull is ON: `deploy/enable_autopull.sh` wrote
  `/opt/esports_silo/.env`, one real write landed 18 odds_raw rows, systemd timer installed
  (15 min, collect-only, HALT stays true). OddsPapi collector/scripts are legacy (unused).
- **Polymarket pairing = tag-based, LIVE-VERIFIED.** Esports lives under numeric TAG IDs queried
  via `/events?tag_id=`; each match is an event titled `Game: A vs B (BOx) - League` whose
  match-winner market's question == the title (or is a prefix of it) with two team outcomes.
  `collectors/polymarket_collector.py` resolves tag IDs at RUNTIME from `/tags` by exact slug
  (never baked in; drift-warned against recorded values). VERIFIED slug→id 2026-07-06/08:
  **cs2: counter-strike-2=100780 (carries the match events) + cs2=100677** · league-of-legends=65
  lol-worlds=401 lec=102164 · dota-2=102366 · valorant=101672 vct=101682 (legacy
  counter-strike=100602/csgo=100635 kept as harmless extras — topic tags, props only).

**Keys:** old OddsPapi/PandaScore/Riot keys in `/opt/pa2-shared/.env` are all INVALID (need
rotation). pinnodds key is live (rotate — it was shared in chat). Both `.env` files are box-local.

**Pairing: DONE + verified.** `collectors/polymarket_collector.py` (tag-based) + `scripts/link_report.py`
(two-sided reconciliation) prove pinnodds↔Polymarket linking works (LoL 6/6, Dota2 1/1 of real
overlaps; unpaired = low-tier not on PM, or in-play). `results_collector` now ATTACHES PandaScore
winners to the pinnodds rows (orientation-aware) so matches actually resolve. fit_calibrator +
skill_report SQL repointed `oddspapi`→`pinnodds`.

**No backfill exists — FORWARD-ONLY.** LIVE-VERIFIED 2026-07-07 (`scripts/probe_pinnodds_history.py`):
pinnodds has NO archive (all history endpoints 404; only live/prematch snapshots + a rolling
`/api/drops` buffer). OddsPapi is abandoned. So `backfill_historical_odds.py` is DEAD; the
calibrator fits on forward-collected lines only (~2–4 wks of the running timer). Optional paid
`bettingiscool` sells Pinnacle history (de-vigged — raw only) if a backfill is ever wanted.

**Blocking input:** a VALID PandaScore key (current one is INVALID) — without it matches never
settle and the whole proving chain starves. Then: fit → `--predict` → weekly skill gate. Halt stays on.

**CS2 tag — CLOSED (operator was right; my bug, now fixed + confirmed).** VERIFIED 2026-07-08 on a
live dry-run: adding `counter-strike-2` (id **100780**) + `cs2` (100677) to
`POLYMARKET_TAG_SLUGS['cs2']` took CS2 coverage **0 → 26 match markets** (TYLOO vs 9z, FaZe vs
BetBoom, PARIVISION vs BIG — the exact pinnodds fixtures). All four games now have live PM match
coverage (cs2=26, lol=21, dota2=16, valorant=29). counter-strike-2=100780 is now recorded in
`POLYMARKET_TAG_IDS_VERIFIED`. Legacy counter-strike(100602)/csgo(100635) kept as harmless extras.
No CS2 work remains.

**Pairing FULLY verified 2026-07-08:** cs2 **9/9**, lol **6/6**, valorant 2/2-on-PM (all real
overlaps paired; "no-PM-market" = genuinely not listed). Two last fixes got CS2 to 9/9:
`match_matcher.fold()` (NFKD diacritic folding — Västerås↔Vasteras etc., general), and
`db/alias_seed.sql` (converts inert `canonical==alias` identity rows to the 3 CS2 aliases —
Keyd↔Keyd Stars, PVISION↔PARIVISION, BB Team↔BetBoom Team; guarded to touch ONLY self-refs).
⚠ On a FRESH silo DB, load `db/alias_seed.sql` once to reproduce full CS2 pairing.

**Next action for a fresh session:** none on pairing — it's DONE. It's now just keys + time:
rotate PandaScore (INVALID) → timer accrues (odds, price, result) → after ~2–4 wks fit the
calibrator → `--predict` → weekly `skill_report`. No engineering remains; do NOT rebuild the
collectors/runner/gate.

## Where things stand (one line)
The full odds→price→pair→resolve→fit→predict→gate chain is BUILT, wired, and RUNNING on the box;
the DB gate PASSED; nothing trades (`SILO_ENTRY_HALT=true`). The only thing not done is FORWARD
DATA accrual, blocked on a valid PandaScore key. Detail below + in "Current state (2026-07-08)".
(Older "signal design / run the gate next" text in this file or PLAN.md is historical — done.)

## Repo state
- Branch: `claude/blissful-davinci-twt397-n94qo6` (current working branch)
- HEAD: tip of that branch (see `git log --oneline` — do not pin a stale SHA here)
- Get it: `git fetch origin claude/blissful-davinci-twt397-n94qo6 && git checkout claude/blissful-davinci-twt397-n94qo6`
- All silo work is in `esports_silo/` — self-contained, designed to extract to its own repo + DB.

## Committed files
Full inventory is in "Full script/tool inventory" near the end of this doc (kept current).
Carried data IN GIT (commit `369606b`): `data/esports_matches_bulk.jsonl`,
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
- **D3 — Odds source FINAL (2026-07-06): Pinnacle via pinnodds.** ⚠ SUPERSEDES the earlier
  OddsPapi decisions — do not act on any "Singbet/Sbobet via OddsPapi" text elsewhere; that whole
  path is ABANDONED. History: OddsPapi carried pinnacle on B2B-only (operator-ruled 2026-07-05),
  so pinnacle was dropped and singbet/sbobet were the interim pick — then `pinnodds` (self-serve
  Pinnacle wrapper) was found and adopted, restoring pinnacle as the single sharp benchmark. Now:
  base `https://pinnodds.com/kit/v1`, header `x-portal-apikey`, esports=sport_id 11, raw
  money_line (num_0=match winner), ~20 req/window. `SHARP_BOOKS=pinnacle`. The OddsPapi collector
  + `backfill_historical_odds.py` are DEAD legacy (pinnodds has no archive → forward-only).
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
Devig=dead, optional-cols=skipped are settled. Odds source SETTLED: **Pinnacle via pinnodds**
(D3 above — ignore PLAN.md's older "Singbet/Sbobet via OddsPapi" text, superseded). The ONLY
open blocker is a **valid PandaScore key** (+ ~2–4 wks forward accrual). All data/network work
is operator-run.

## Box specifics (where everything lives — the /opt/* dirs are release copies, NOT git)
- **Repo clone (git):** `/home/ubuntu/esports_silo_src` — the ONLY git checkout on the box.
- **Silo DB:** `esports_silo` (own role/db; gate PASSED; 32,409 matches, 30,881 with winners).
- **Env:** `/opt/esports_silo/.env` (DATABASE_URL + keys; `SILO_ENTRY_HALT=true`). chmod 600.
- **Venv python (has aiohttp/asyncpg):** `/opt/pa2-esports-shared/venv/bin/python`. System `python3`
  LACKS the deps — always use the venv path.
- **Timer:** `esports-silo-collect.timer` (systemd), every 15 min, collect-only.
- **Operator runs box commands as:**
  `ssh -t -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "cd ~/esports_silo_src && git pull && <cmd>"`.

## Keys (all box-local; NEVER commit)
- **PandaScore = INVALID (HTTP 401) — THE blocker.** Rotate at pandascore.co → `/opt/esports_silo/.env`.
  Without it results never settle (41 forward matches collected, 0 with winners).
- **Riot = INVALID** (personal keys expire 24h) — only for LoL patch context; low priority.
- **pinnodds = live but was SHARED IN CHAT → rotate.** `PINNODDS_API_KEY` in the .env.
- **OddsPapi = ABANDONED** — its MISSING/INVALID lines in `validate_keys` are expected/irrelevant.

## Current state (2026-07-08) — BUILT, RUNNING, VERIFIED; nothing trades
Everything below is done and confirmed on live box data. Do NOT rebuild it.
- Odds in (pinnodds Pinnacle) → `odds_raw`; PM prices (all 4 games, tag-based) → `polymarket_snapshots`;
  both on the 15-min timer, accumulating (verified fresh timestamps).
- Pairing DONE + verified: **cs2 9/9, lol 6/6, valorant 2/2-on-PM**. Diacritic fold + CS2 alias seed applied.
- `results_collector` attaches PandaScore winners to pinnodds rows (orientation-aware) — waits on a valid key.
- `fit_calibrator` + `skill_report` read `aggregator='pinnodds'`. Calibrator UNFITTED (forward-only, needs accrual).
- `predictions` ledger empty (fills once calibrator fits + `--predict` is on). Decisions hardcoded `no_bet`.

## The remaining path (all HALTED; see `VERIFY_AND_PROCEED.md` for exact commands)
1. **Rotate PandaScore** → validate → results start settling (watch `forward winner_set` climb).
2. **Accrue ~2–4 wks** of (odds, price, result) — no backfill exists.
3. `fit_calibrator` (refusal-until-enough is EXPECTED) → writes the calibrator artifact.
4. Add `--predict` to the timer → forward ledger fills (all `no_bet`).
5. `skill_report` weekly (≥200 resolved) — exit 0 = PASS. **The only verdict.**
6. A PASS does NOT flip `SILO_ENTRY_HALT` — that's a separate deliberate human step.

## Verify before trusting these claims
`verify_state.sh` (read-only) proves every line above against the live box; `VERIFY_AND_PROCEED.md`
Part A explains what each section must show. Run it first in any fresh session.

## Full script/tool inventory (all committed, `esports_silo/`)
- **Collectors:** `pinnodds_collector.py` (odds), `polymarket_collector.py` (PM prices, tag-based),
  `results_collector.py` (winners), `odds_collector.py`+`polymarket`… legacy note: `odds_collector.py`
  is the DEAD OddsPapi one.
- **Pipeline:** `markets/match_matcher.py` (two-team gate + diacritic fold), `signal/sharp_consensus.py`,
  `betting/decision.py`, `pipeline.py`, `eval/skill_metrics.py`, `execution/resolution.py`+`paper.py`,
  `run/runner.py` (+`--predict`), `run/selftest.py` (14 modules).
- **Scripts:** `verify_data_quality.py` (gate, PASSED), `import_from_prior_bot.py`, `validate_keys.py`,
  `fit_calibrator.py`, `skill_report.py`, `link_report.py` (two-sided pairing), `verify_state.sh`,
  and probes: `probe_pinnodds.py`, `probe_pinnodds_history.py`, `probe_polymarket_tags.py`,
  `probe_polymarket_esports.py`, `probe_polymarket_cs2.py`, `probe_market_microstructure.py`,
  `backfill_historical_odds.py` (DEAD/legacy).
- **Deploy:** `enable_autopull.sh` (the on-switch), `run_steps_0_4.sh`/`run_steps_5_7.sh` (setup),
  `esports-silo-collect.service/.timer`, `crontab.example`.
- **DB:** `db/schema.sql`, `db/alias_seed.sql` (CS2 aliases — load on a fresh DB), `db/alias_inspect.sql`.
- **Docs:** `COMMANDMENTS.md`, `PLAN.md`, `HANDOFF.md`, `START_PROMPT.md`, `VERIFY_AND_PROCEED.md`,
  `GO_LIVE_CHECKLIST.md`, `README.md`. ⚠ PLAN.md + GO_LIVE still carry some OddsPapi-era text in
  spots — this HANDOFF's D3 + current-state sections are authoritative where they conflict.

Artifacts in `esports_silo/artifacts/` are box-local + gitignored. `SILO_ENTRY_HALT=true` throughout.
