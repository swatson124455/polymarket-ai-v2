# esports_silo — Rebuild Plan

**Read `COMMANDMENTS.md` first. Everything here is bound by it.**

## Objective
A siloed, **pre-match** esports forecasting bot. Signal = sharp-book lines
(Pinnacle + Circa + one Asian book) via ONE aggregator, compared to the Polymarket
price. Paper-first. Isolated: its own repo + its own DB, no ties to the 15-bot system.

## The one thing to build from scratch
**A forecaster that beats the market price.** The prior bot failed because its model was a
strictly worse forecaster than the CLOB, and its bet rule (`model_prob − price ≥ edge`)
selected the model's *largest errors*. Everything else (data, calibration, sizing,
execution, resolver) is keep/repoint.

Failure evidence — 📄 DOC-SOURCED (`EB_MODEL_EDGE_PROPOSAL_2026-06-16.md`, sourced to
`prediction_log`; **re-verify**; P&L excluded per Cmd 1): model Brier **0.247** vs market
**0.181**; correlation with outcome **+0.19** vs **+0.53**; market wins in every game.

## Already built (branch `claude/blissful-davinci-twt397`, HEAD `dd425f1`)
- `db/schema.sql` — `matches`, `odds_raw` (append-only), `polymarket_snapshots`, `predictions`, `team_aliases`
- `COMMANDMENTS.md` — P&L-not-evidence · de-vig-doesn't-exist · surgical-cut · quarantine-by-default
- `scripts/validate_keys.py` (runs; reports UNREACHABLE/INVALID/VALID)
- `scripts/import_from_prior_bot.py` (matches + aliases; winner-map DEVIATION documented)
- `collectors/odds_collector.py` (append-only + per-(game,book) coverage guard; odds-payload field mapping = SEAM)
- `config.py`, `.env.example`, `requirements.txt` (no shin/xgboost/catboost)

## Build list (verified)
| # | Component | Type | When |
|---|---|---|---|
| 1 | `verify_data_quality.py` — read-only battery | from-scratch | ✅ **BUILT** (Cmd-4 master gate) — awaits operator run on the box |
| 2 | Polymarket snapshot collector → `polymarket_snapshots` | build now | now (silo) |
| 3 | Market↔match matcher (aliases + two-team gate) | surgical-pull `esports_market_scanner` | now (silo) |
| 4 | Skill-eval harness (Brier/calibration/closing-line, **P&L-free**) | surgical-pull `esports_v2/backtest/metrics.py`, **strip** de-vig CLV + mis-oriented Brier | now (silo) |
| 5 | **The signal/model** (raw 3-book → `P(team_a)`, no de-vig, price-deferring rule) | from-scratch | design now, validate after odds |
| 6 | Bet-decision + Kelly sizing | sizing surgical-pull `esports_bankroll_manager`; rule from-scratch | after #5 |
| 7 | Complete odds-collector field mapping | needs 1 live aggregator response | after coverage gate |
| 8 | Paper-execution + resolution lifecycle (track **skill**, not P&L) | lean rebuild | later |
| 9 | Scheduler/runner (systemd/cron) | small | later |

## Critical path
1. Build **#1**. → 2. Operator runs it on the box → whatever passes leaves quarantine.
3. Design **#5** on clean inputs. 4. **#2/#3/#4** proceed in parallel (no data-battery dep).
5. Operator: aggregator coverage gate + valid keys + **start forward-collecting odds**.
6. After ~2–4 wks of odds: validate the signal on forward data (skill, not P&L).

## Open decisions
- De-vig → **DECIDED: does not exist** (Cmd 2).
- Optional columns (`event_name`/`map`/`is_lan`/alias `source`/`match_quality`) → **DECIDED: skipped** (reviewed; `map` unusable at match grain).
- Asian book → **OPEN** (pick one; verify aggregator carries it for esports).
- Branch reconciliation (`eb/main` current code + `master` rebuild docs) → **OPEN**.

## Blockers (operator-only — the silo has no network/DB)
Aggregator coverage (Pinnacle+Circa+Asian **for esports**) · valid keys · run the
verification battery · forward-collect odds · pick the Asian book.

## Phase-1 definition of done
Data verified out of quarantine · aggregator coverage confirmed · signal designed · forward
odds collection running. **No trading — paper or live — until skill gates pass on forward data.**
