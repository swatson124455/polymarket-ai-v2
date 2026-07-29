# KALSHI MAKER — HANDOFF 2026-07-29 EVENING (halt event, root fixes, stickiness slate LIVE)

**BOT IS LIVE** on `claude/maker-kalshi-live` @ `4fe3f3e`, all deployed files md5 == commit
blob (verified per deploy). Last health check 23:33:34Z: retention gauge 85.0% and climbing,
quoted 5, zero fails, zero silent failures, equity mark $248.64, dd $8.89 vs the $40 arm.
Panic stop unchanged: `sudo touch /opt/pa2-maker-kalshi-live/STOP` (now paced: repeats every
1800s, maker-first; first invocation immediate).

## THE DAY IN ONE PARAGRAPH
Day started $295.78 (mark). The $40 mark-drawdown halt SELF-FIRED at 18:18:29Z (dd $40.79 vs
day-peak $296.57; STOP auto-written; maker-first flatten clean). Attribution (session_econ
18:26Z, fees incl.): index-family pre-exclusion tail −$10.14, KXMUSKNW churn loop −$11.02,
halt+late-fill crystallization −$4.87, structural maker cost −$10.60 (operator: acceptable).
Operator directive followed: NO market bans — root-fixed the LOGIC (per-market loss governors).
Operator re-armed at 18:43Z (peak re-baselined to $257.09, TOTAL capital 280→250). Evening:
two audit agents (~30 findings), my same-day regressions fixed same-session, audit batches 1+2
shipped, halt got 3-cycle confirmation, and the operator-named STICKINESS SLATE went live
23:29:34Z. Post-flatten equity basis ~$257 → $248.64 at 23:33Z (mark; includes evening spread
marks/fills — NO reward credit has landed yet, receipts due Aug 1-2).

## COMMITS THIS SESSION (each tested + mutation-checked + deployed byte-exact)
- `58db036` capital-aware ranking TELEMETRY (shadow rank, caprank-*.jsonl)
- `8718125` offline capture sweep + risk-aversion knobs (shadow-only)
- `ba5e525` sweep orderbook_fp fix (own regression, caught live 17:46Z)
- `4667d84` multi-variant shadow (env/lean/averse per cycle)
- `2c1a0b7` **loss governors**: per-mkt $5/day realized-loss exit-only + 1h re-entry cooldown
- `0f73f5c` sweep next_cursor (own latent bug, audit A1-F2)
- `5a0c115` self-audit regressions: F18 dry-run crash, F19 fail-open stamp, F6a/b governor
  bypasses, F5 cooldown feed (settle+preclose paths)
- `8056c90` audit batch 1: STOP escalation naked-only (was de-pairing ladders), preclose
  pacing/prune/market-cap, STOP-flatten 1800s pacing, fill_count_fp fallback
- `811f169` halt 3-cycle confirmation + batch 2: env-file hot-reload of safety knobs
  (KALSHI_ENV_FILE set), corrupt-state loud+preserved, kill-script paginated
- `4fe3f3e` **stickiness slate A/D/E/G** (see below)

## THE STICKINESS SLATE (operator-named, letters canonical)
Root cause of the measured churn (134 mkts/day, median presence <1%, 5,642 transitions):
`SCORE_UNKNOWN_BONUS=1.0` scored unmeasured markets at 100% of pool while measured median
share = **0.0615** (n=8,925 rows, quotes-20260729) — "measurement demotes 16×" (deep-dive
agent, algebra verified). SCORE_EXPLORE was NOT the driver (sim: retention ~21% at 10/2/0).
- **A LIVE** incumbency bonus 0.25 (PROVISIONAL — price a seat from Aug 1-2 receipts; sunk
  losses buy no loyalty, only standing presence does; feed = prev_standing_tickers)
- **B LIVE** KALSHI_SCORE_UNKNOWN_BONUS=0.06 (the measured median share)
- **C LIVE** KALSHI_SCORE_EXPLORE=3
- **D LIVE** plan.fp_retained_pct gauge (first readings 58.8→70.0→85.0%)
- **E LIVE** KALSHI_EXPLORE_PROBE_CT=5 (probe-sized exploration, unwinds never shrunk)
- **G LIVE** ref_move 600s gap guard (drift ≠ volatility)
- **F NEXT SESSION**: sweep→live-rank wiring — get staleness data first; requires haircut AND
  age cutoff in the same change (sweep captures are optimistic by construction)
- **H NEXT SESSION**: index-family retry review (governors now cap any retry at $5/mkt/day)
- **I NEXT SESSION**: sibling jump-correlation study — today's telemetry had ZERO qualifying
  ≥2¢ co-window jumps (churn + quiet books); needs candle history on jumpy ladder families
- **J NEXT SESSION (operator-approved to proceed)**: clerical batch 3 — close-time cache
  expiry/bound (permanent-negative-cache hole re-admits far markets), _SILENT per-cycle delta,
  telemetry purge cadence (runs once at daemon start; ws_daemon_log never purged), SCORES
  eviction, blackout cancel backoff, per-ticker create-fail ratchet, WS resubscribe hysteresis
- **K**: receipt-vs-model calibration — **operator: "lets test 8-2"**; windows close Aug 1-2
  (only KXMUSKNW closes Jul 31). Ballot model line $26.63/day is a MODEL until credited.

## SAFETY STACK NOW LIVE (all verified on box)
$40 mark-dd halt with 3-cycle confirmation + $120 cost ratchet · per-market $5/day realized
exit-only governor (latching, receipt-based) · 1h re-entry cooldown after ANY taker exit
(strand/settle/preclose; fails closed on corrupt stamps) · STOP flatten maker-first, naked-only
escalation, 1800s pacing · preclose taker paced+pruned+market-capped · safety knobs hot-reload
from live.env each cycle (changes PRINTED) · corrupt quoter_state preserved aside + loud ·
kill-script paginated · fill_count_fp fallback (GET-orders already migrated, probe 18:50Z).

## OPEN AUDIT FINDINGS NOT YET SHIPPED (RULE NINE: none demoted)
From the two audit agents, still open beyond J: WS delta price-key `_fp` probe (fails safe but
silently degrades Stage C), settlement `value` field probe (cash recorder), fill-cost feed
field-presence counter, attribution-ledger watermark `or ""` rename hazard, per-cycle budgets
calibrated for the timer era (TAKER_MAX_MKTS etc. at daemon cadence), NETEV/PRESENCE table
import-once staleness (NETEV gate off; PRESENCE gate ON live), dd-peak cost-fallback mark
mixing (SUSPECT, F22/F23 — the halt-confirmation change reduces but does not remove it).

## WATCH
- fp_retained_pct trend (want sustained >70-80% with earners parked)
- reentry_cooldown / loss_exitonly counts (governors firing = data, not alarm)
- explore_probe_capped in qstats (E working)
- First reward credits Aug 1-2 → build the receipt-vs-model table → set CAPRANK_CALIB and the
  real incumbency X → THEN consider rank flip (capital-aware cap_score) and scaling.
- Two dust positions remain (KXMAMDANIEO −0.45ct, KXDXYDUD +0.92ct) — sub-minimum, settle out.

## TOOLING (reuse, do not rebuild)
VPS /tmp: resting.py, session_econ.py, full_review.py, episode_analysis.py, est_rewards.py,
funnel_audit.py, live_summary.py, verify_takers.py. New in repo: kalshi_capture_sweep.py
(offline prospective capture), kalshi_fill_costs.py (per-mkt realized cost feed),
kalshi_capital_rank.py (shadow score). caprank-*.jsonl = shadow-vs-actual per cycle.
