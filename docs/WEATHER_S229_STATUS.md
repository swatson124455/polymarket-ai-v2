# HANDOFF — WeatherBot S229 (S222 verification FAIL → EMOS unit-soup ROOT FIX deployed; +end-date +CancelledError fixes; EV research; automation)

**Session:** WeatherBot (WB) silo · first LOCAL session (has the VPS deploy key; runs ssh/scp/psql directly) · **Branch:** `claude/new-whiteboard-session-9b23tq`
**HEAD at handoff:** `bf3e09b` · **Deployed release:** `20260714_003205` (code from `9dc6d59`; verified live, service active, S229 markers=11 on box)
**Date:** 2026-07-13 → 07-14 · **Mode:** PAPER (`SIMULATION_MODE=true`) — treated as production per CLAUDE.md
**Scope:** WeatherBot only (`bots/weather_bot.py`, vendored `probability_engine.py`, tests, docs, WB deploy, VPS research crons). No shared-module or other-bot edits.

> **Canonical always-current status is `docs/WEATHER_STATUS.md`** (OPEN DECISIONS 1–4, WHAT IS LIVE, CHANGELOG). This file is the archival S229 deep-dive. Resume path: `bash scripts/wb_resume_check.sh` → read `WEATHER_STATUS.md` → this doc for detail.

---

## 0. TL;DR for the next session

S229 opened to run the time-gated S222 verification (gate had reached 77 resolved ≥50). It **RAN and FAILED every criterion** — but the failure's ROOT CAUSE was found, numerically proven, fixed, and DEPLOYED the same day, so the FAIL measured the bug, not the S222 fixes. **The single most important fact: the S222 verification clock RESTARTED at 2026-07-13 16:02:29Z (the EMOS-fix deploy). The 07-11→07-13 window is now void for verification — it traded on a poisoned corrector. Re-run at ≥50 resolved from 16:02:29Z (~07-16/17).**

Three code fixes shipped this session, all defect-test-first, all deployed:
1. **EMOS unit-soup (`24b2847`, THE root cause):** the global SAMOS→raw conversion pooled climatology across mixed °C/°F stations → one corrector displaced every non-EMOS-ready station's forecast (KORD 97°F read as 86°F "certainty"; phantom NO edges everywhere; 0.90+ conf bin realized ~17%). Fixed by per-station de-normalization.
2. **end_date_iso persistence (`4fa67a3`):** WB discovery dropped Gamma's `endDate`; markets stored NULL and resolved ~2 days late. Now persisted + NULL rows healed on rediscovery.
3. **CancelledError scan-abort (`9dc6d59`):** `isinstance(result, Exception)` missed `CancelledError` (a BaseException since 3.8) → tuple-unpack TypeError aborted whole scans (69× over 3 days). Fixed at both gather sites. **Verified in prod: zero occurrences since the 00:32 deploy.**

Plus: EV research scoreboard (what the bot can/can't make money on — §4), two automation crons on the VPS, and hygiene (wallet.txt relocated out of repo; rotation is a STANDING operator reminder).

Two DON'Ts carried forward: **do NOT naively fix the cold-start bootstrap date-bind** (§5, dormant landmine — re-poisons EMOS training); **do NOT touch the calibrator** (hands off until ~08-07).

---

## 1. What is LIVE right now

- **Deployed:** release **`20260714_003205`**, effective restart **2026-07-14 00:32:41Z** (clean stop — no SIGKILL). Carries: S229 EMOS per-station fix + end-date fix + CancelledError fix + `scripts/wb_research/` + the S228 latency package (still flag-OFF / default cadence).
- **Rollback:** `sudo ln -sfn /opt/pa2-weather-releases/20260713_160143 /opt/polymarket-ai-v2-weather && sudo systemctl restart polymarket-weather`
- **Verified live (07-14):** service active; S229 markers=11 in the box's `weather_bot.py`; 3 calibration reloads + 3 `weather_global_emos_by_station_loaded` (106 stations) since deploy; `reload_failed`/`cal_fit_failed`=0; **`avg_clim_mean` tripwire = 0** (the unit-soup regression guard); zero `CancelledError` scan aborts.
- **S222 clean-window gate:** 3/50 resolved (211 predicted) as of handoff — early; ETA ~07-16/17.
- All S222 containment gates still ON. V28 calibrated-edge gate OFF. Calibrator untouched.
- Migrations 079+080 idempotent-reapplied at each cut.

## 2. What was done (commit-by-commit)

| Commit | Type | One line |
|--------|------|----------|
| `9cc067e` | manifest | repaired stale S228 fingerprint counts so the resume check passed at open |
| `4fa67a3` | **fix** | end_date_iso persisted for WB-discovered markets; NULL rows healed on rediscovery (5+1 tests). Disproved kickoff's "database.py backport" premise (box db = repo blob `08c0b06`, already had the fix). Side effect: activates the S172-D10 dynamic exit-cooldown (key finally populated). |
| `24b2847` | **fix (ROOT)** | global SAMOS/EMOS mixed-unit pooling → per-station de-normalization + unit-partitioned fallback + engine `load_global_emos_by_station()` (6 tests). §3. |
| `9664f50` | docs | S222 FAIL verdict recorded; OPEN DECISIONS 2/2b rewritten; verification prompt re-pointed to 16:02:29Z |
| `8de176d` | docs | purged cross-bot vendor bleed from WB docs (operator directive — [[no-cross-bot-nag-bleed]]) |
| `a2ae5a1` | docs+tools | EV research scoreboard (OPEN DECISION 2c) + `scripts/wb_research/{brier_duel,race_study}.py` + README |
| `9dc6d59` | **fix** | CancelledError escapes Exception checks on gather results → whole-scan aborts (1 test). §6. |
| `b1bf61c`,`326f56a`,`e74a454`,`bf3e09b` | docs | night-pass sweep, NEXT QUEUE table (2d/2e), deploy records, operator decisions, wallet-rotation standing reminder |

Deploys this session: `20260713_160143` (EMOS+end-date, restart 16:02:29Z) → `20260714_003205` (+CancelledError, restart 00:32:41Z).

## 3. The EMOS unit-soup root cause (detail)

`_learn_calibration` fits global SAMOS in ANOMALY space, then converts to raw-space EMOS `(a,b,σ)` for stations without local EMOS. The pre-fix conversion averaged climatology across **all** stations in one pool — °C and °F mixed (journal `avg_clim_mean=43.6` = nonsense midpoint) — so one corrector `(a=9.28, b=0.79, σ=2.10)` was applied to every cold station. Effect: °F forecasts dragged ~10°F cold, °C ~3°C warm, spread pinned. Numerically proven: KORD 2026-07-14 forecast 97.2°F → corrected 86.4°F → P(≤93.5°F)=0.9996 (= the logged 0.999), market ~5%. ~90% of bucket families carried <40% total model mass → phantom NO edges at 0.95 confidence. **The raw ensemble is GOOD** (independent refetch of the bot's own Open-Meteo call agrees with the market); the correction layer manufactured the displacement. It surfaced only when S227 made reloads actually run again (post-cutoff left just 4 stations with local EMOS, pushing everything onto the poisoned global).

Fix: SAMOS stays in anomaly space, de-normalized **per station with its own climatology** (`_samos_global_by_station`); `probability_engine.load_global_emos_by_station()` consulted after local EMOS, before the legacy pooled tuple; no-climatology fallback fits per temperature unit; shrinkage blends toward the station's own tuple. 106 stations materialized live. **Regression tripwire:** if `avg_clim_mean` ever reappears in `weatherbot_global_samos_fitted` (new logs show `method=per_station`), the defect is back.

## 4. EV research scoreboard (read-only market-structure studies)

Terminology: **"ensemble" = the bot's raw INPUT** (public GFS/ECMWF members), not the bot. Measured ladder: **market > raw ensemble > old-bot**; new-bot-vs-market is the open question. All on primary sources (CLOB price-history/books, IEM METAR, CLOB-verified outcomes, matched information time). Harnesses: `scripts/wb_research/` (README has run instructions + numbers).

- **DEAD — day-ahead directional:** market Brier 0.195 vs raw ensemble 0.243 (n=186, 24h); still dead morning-of (0.191 vs 0.250, n=230, 8h). Never trade pre-afternoon direction on raw signal.
- **DEAD — family Dutch-book (taker):** best-ask family sums 0.997–1.062, 3–8 share best-ask depth on tail legs; the ~2% mid "underround" is a spread artifact.
- **ALIVE — resolution-day leader-following (the ONE confirmed edge):** buy the bucket holding the METAR running max at local hour H, hold to resolution, losers included → monotone +EV, H=15 +0.037/68% → H=17 +0.085/89% per $1 at mid, pre-cost; winners jump 0.54→0.72 within 15 min of the deciding ob, then drift ~0.75→1.00 over hours. Caveat: one summer week, MID prices — DO NOT trade yet.
- **OPEN (time-gated) — station wedge:** does the FIXED bot beat the market anywhere? Re-run `brier_duel.py` with `prediction_log` probs on the clean window, per (station×lead×side) cell; whitelist only cells beating the market by >2×(half-spread+slippage).
- **OPEN (needs live data → now collecting) — maker economics + executable capture:** the **shadow-book logger** is LIVE (§7) collecting exactly this. Review at handoff: does the +8¢ leader-following edge survive real ask depth?

Timezone audit: PASS (Open-Meteo `timezone=auto` local-day seam verified at Seoul; per-station targets; date-string matching not index).

## 5. DORMANT LANDMINE — cold-start bootstrap (DO NOT naively fix)

`_maybe_bootstrap_cold_station` binds `target_date_str` (str) into a date column → asyncpg DataError → ERA5 bootstrap inserts have ALWAYS silently failed (why `weather_calibration` has zero `era5_bootstrap` rows). This failure is **accidentally protective**: the EMOS fit has NO `actual_source` filter, so fixing the bind alone would inject ERA5-ground-truth pairs into the clean training window — the exact contamination S224's WS-3 cutoff removed. The root fix is TWO coupled halves in ONE commit: (a) date-bind, (b) WU-only / `actual_source` training filter. Half (b) changes EMOS training inputs → changes predictions → restarts the S222 clock, so it is sequenced POST-verdict (queue item 5). Cold stations already have a better prior now: the per-station SAMOS fallback (§3).

## 6. CancelledError scan-abort (detail)

`asyncio.CancelledError` inherits `BaseException` (not `Exception`) since Py3.8. The analyze-phase `gather(return_exceptions=True)` result loop checked `isinstance(result, Exception)`; a cancelled group task fell through to `opps, model_probs = result` → `TypeError('cannot unpack non-iterable CancelledError object')` → the ENTIRE scan aborted and hit the base failure counter (69× in the 07-11→13 journal; pre-existing, NOT an S229 regression). Same blind spot silently dropped cancelled trade tasks. Fix: `isinstance(result, BaseException)` at both sites — a cancelled group is logged and skipped, the scan continues. **Prod-verified: zero occurrences since the 00:32:41Z deploy** (was ~hourly before).

## 7. Automation LIVE (VPS ubuntu crontab; rollback = remove the crontab line)

- `17 9 * * * /home/ubuntu/wb_research/nightly.sh` — (a) drains the NULL-end market pool 2k/night from CLOB (fill-NULL-only UPDATEs; whole-pool 17.8k→13.6k so far; the recent-window WB-predicted subset was explicitly backfilled to 0, but ~1.7k older already-ended WB-predicted markets remain NULL and drain with the pool), (b) re-runs the race study to accrue leader-following samples. Logs `~/wb_research/nightly_*.log` (30-day self-prune).
- `*/10 * * * * /home/ubuntu/wb_research/shadow_book.sh` — **shadow-book logger** (queue item 3, built early per operator): for every active US highest-temp family in its local 10:00–20:00 window, records METAR running max + leader + 3-deep CLOB books both sides → `shadow_books_YYYYMMDD.jsonl` (84 lines day 1). Read-only. THE canonical executable-capture dataset for the leader-following review.

## 8. Hygiene state

- **wallet.txt — RELOCATED out of repo (07-14)** to a private local path. It holds the private key of the ACTIVE trading wallet `0xd6a5…627F` (referenced in `/opt/pa2-shared/.env`; nonce 10, ~9.3 POL). Key sat in-repo since May 15 + weeks of world-readable VPS copies. **ROTATION = STANDING OPERATOR REMINDER (echo every handoff until done)** — cheapest now (paper mode, no live positions): new wallet → move POL/tokens → update `/opt/pa2-shared/.env` → restart all 4 services (shared env).
- **TEMP tarballs — DELETED (07-14)**, all 10 verified secret-free first.
- **VPS release pruning — DEFERRED, REVIEW EVERY HANDOFF:** 19 releases / 43G, disk 61% (no pressure). After the S222 verdict, keep live `20260714_003205` + rollback `20260713_160143`, delete the other 17 (~40G). Gated as forensic insurance until the verdict.

## 9. PENDING WORK — NEXT QUEUE (WEATHER_STATUS OPEN DECISION 2e is canonical)

1. **S222 re-run + station-wedge duel** — trigger: clean window ≥50 (~07-16/17). A local scheduled task `wb-s222-gate-check` fires 09:00 daily 07-16→19 to do this automatically (app must be open); the VPS work runs regardless.
2. Gate retirement IF PASS (A1/A3 → dampeners → caps; C0 Kelly last, calibrator-gated).
3. Shadow-book review — does leader-following survive real asks? (data collecting now).
4. Latency package activation (OPEN DECISION 3a) — decide WITH #3 (priority-wake is the nowcast weapon).
5. Bootstrap landmine proper fix (date-bind + `actual_source` filter, same commit) — post-verdict.
6. Ops debt: real deploy mechanism for the main ingestion tree; VPS release prune.
7. **STANDING operator reminders (echo every handoff):** rotate wallet `0xd6a5…627F`; review release pruning.

## 10. Rules honored / carried forward

- WB silo; sanctioned shared-layer touches only (WB engine copies, WB deploy). No other-bot code/env/branches. Local WB+EB SHARE the main checkout → this session works in a linked worktree `.claude/worktrees/wb-whiteboard`; the main tree is held by an EB branch. Verify `git branch --show-current` before every write ([[no-cross-bot-nag-bleed]]).
- NEVER quote P&L (Forbidden Pattern #11) — calibration metrics only.
- One fix per commit; defect-test-first (red→green); manifest in sync (resume check FAILs on drift); tag S229.
- Do NOT touch the calibrator before S222 + re-learn (~08-07).
- Never track/echo other bots' vendors/secrets/nags.
