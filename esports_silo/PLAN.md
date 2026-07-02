# esports_silo — Rebuild Plan

**Read `COMMANDMENTS.md` first. Everything here is bound by it.**

## Objective
A siloed, **pre-match** esports forecasting bot. Signal = sharp-book lines
(Pinnacle + Singbet + Thunderpick via OddsPapi; operator to ratify esports coverage — D3),
compared to the Polymarket price. Paper-first. Isolated: its own repo + its own DB, no ties
to the 15-bot system.

## The one thing to build from scratch
**A forecaster that beats the market price.** The prior bot failed because its model was a
strictly worse forecaster than the CLOB, and its bet rule (`model_prob − price ≥ edge`)
selected the model's *largest errors*. Everything else (data, calibration, sizing,
execution, resolver) is keep/repoint.

Failure evidence — 📄 DOC-SOURCED (`EB_MODEL_EDGE_PROPOSAL_2026-06-16.md`, sourced to
`prediction_log`; **re-verify**; P&L excluded per Cmd 1): model Brier **0.247** vs market
**0.181**; correlation with outcome **+0.19** vs **+0.53**; market wins in every game.

## Already built (branch `claude/blissful-davinci-twt397-n94qo6`; see `git log --oneline`)
- `db/schema.sql` — `matches`, `odds_raw` (append-only), `polymarket_snapshots`, `predictions`, `team_aliases`
- `COMMANDMENTS.md` — P&L-not-evidence · de-vig-doesn't-exist · surgical-cut · quarantine-by-default
- `scripts/validate_keys.py` (runs; reports UNREACHABLE/INVALID/VALID)
- `scripts/import_from_prior_bot.py` (matches + aliases; winner-map DEVIATION documented)
- `scripts/verify_data_quality.py` — Cmd-4 read-only master gate (item #1; awaits operator DB run)
- `collectors/odds_collector.py` (append-only + per-(game,book) coverage guard; odds-payload field mapping = SEAM)
- `config.py`, `.env.example`, `requirements.txt` (no shin/xgboost/catboost)

## Build list (verified)
| # | Component | Type | When |
|---|---|---|---|
| 1 | `verify_data_quality.py` — read-only battery | from-scratch | ✅ **BUILT** (Cmd-4 master gate) — awaits operator run on the box |
| 2 | Polymarket snapshot collector → `polymarket_snapshots` | build now | after gate (D2) |
| 3 | Market↔match matcher (aliases + two-team gate) | surgical-pull `esports_market_scanner` (verified clean `:180,313`) | after gate (D2) |
| 4 | Skill-eval harness (Brier/calibration/closing-line, **P&L-free**) | **from-scratch** — standard formulas *referenced* only; `metrics.py` NOT pulled (embeds `compute_pnl` Cmd 1 + shin CLV Cmd 2) | after gate (D2) |
| 5 | **The signal/model** (raw 3-book → `P(team_a)`, no de-vig, price-deferring rule) | from-scratch | design after gate, validate after odds |
| 6 | Bet-decision + Kelly sizing | **from-scratch** — textbook Kelly *referenced*; `esports_bankroll_manager` NOT pulled (embeds banned `edge>0` rule `:82-83`) | after #5 |
| 7 | Complete odds-collector field mapping | needs 1 live aggregator response | after coverage gate |
| 8 | Paper-execution + resolution lifecycle (track **skill**, not P&L) | lean rebuild | later |
| 9 | Scheduler/runner (systemd/cron) | small | later |

## Critical path
1. **#1 BUILT.** → 2. Operator runs the battery on the box → whatever passes leaves quarantine (**GATE**).
3. **Only after the gate clears** (D2 GATE-THEN-BUILD — nothing new built ahead of it): build
   **#2/#3/#4** (all from-scratch per D1 where noted) and design **#5** on the cleared inputs.
4. Operator: aggregator coverage gate + valid keys + **start forward-collecting odds**.
5. After ~2–4 wks of odds: validate the signal on forward data (skill, not P&L).

## Open decisions
- De-vig → **DECIDED: does not exist** (Cmd 2).
- Optional columns (`event_name`/`map`/`is_lan`/alias `source`/`match_quality`) → **DECIDED: skipped** (reviewed; `map` unusable at match grain).
- Asian / third sharp book → **RESEARCHED (operator to ratify).** Sharpest gaming books (web,
  2026): **1. Pinnacle** (benchmark sharp, ~2–3% esports margin, deepest coverage), **2. Singbet**
  (Asian sharp — the Asian-book pick), **3. Thunderpick** (esports-native, 2.5–4% majors, often
  sharper than Pinnacle on CS2/LoL/Dota). All three are carried by **OddsPapi** (already the wired
  aggregator) — free tier 250 req/mo, paid ~$49/mo. ⛔ Operator must confirm OddsPapi actually
  returns these three **for esports** via the collector's coverage guard before forward-collecting.
- Branch reconciliation (`eb/main` current code + `master` rebuild docs) → **RESOLVED on this
  branch**: `claude/blissful-davinci-twt397-n94qo6` carries the master rebuild docs
  (patch-equivalent) + the silo scaffold + data + gate. `eb/main`'s later legacy-bot commits are
  out of silo scope (Cmd 3 — source material only, quarantined).

## Blockers (operator-only — the silo has no network/DB)
Aggregator coverage (Pinnacle+Singbet+Thunderpick **for esports** via the OddsPapi coverage
guard) · valid keys · run the verification battery · forward-collect odds · ratify the book set.

## Phase-1 definition of done
Data verified out of quarantine · aggregator coverage confirmed · signal designed · forward
odds collection running. **No trading — paper or live — until skill gates pass on forward data.**
