# KALSHI MAKER — HANDOFF 2026-07-30 (caps raised, batch 3 live, gate shelved, receipts start Jul 31)

**BOT IS LIVE** on `claude/maker-kalshi-live` @ `c23e2ac`; deployed files md5 == commit blob
(verified at deploy 02:55:34Z: maker_kalshi_quoter.py, maker_kalshi_ws_daemon.py,
kalshi_market_scores.py, kalshi_ws_feed.py). Panic stop unchanged:
`sudo touch /opt/pa2-maker-kalshi-live/STOP` (paced 1800s, maker-first; $40 halt needs 3
consecutive breach cycles).

## STATE AT HANDOFF (all API/telemetry reads 12:41-12:42Z 2026-07-30)
- Cash $320.39 + positions $1.43 ≈ $321.8. Committed $319.82/350. dd $15.76 vs $40 arm.
- Positions: dust only (all sub-1-ct). KXMUSKNW **exit-only for 07-30** (loss governor tripped
  10:53Z after realized -$14.7 that day) + re-entry cooldown fired.
- Operator deposited +$100 (landed 03:03Z, $336.59 cash then). Operator-named caps applied:
  **TOTAL=350, MARKET=75, INV_HARD_CT=80** (restarts done) and **HELD_MAX=140** (hot-applied).
  Halt stays **$40** (operator: keep). $5/day per-market governor + 3600s cooldown unchanged.
- NOTE: MAX_TOTAL / MAX_MARKET / INV_HARD are NOT hot-reload (restart needed);
  HELD_MAX / halt knobs ARE (registry maker_kalshi_quoter.py:2141).

## DAY 07-30 P&L STORY (fills API + plans jsonl, window 22:00Z 07-29 -> 12:42Z 07-30 = -$28.4)
- -$10.6 evening whipsaw (KXTOPMODEL 3x whale sweeps 700-1,200ct — we are 35ct slices, NOT
  targeted; + small MUSKNW/TRUMPTIME unwinds). Designed strand-cross behavior.
- -$2.5 overnight quiet (03:03-10:52Z: 7 fills in ~8h — night-is-cheap thesis held).
- **-$14.7 single event 10:52Z KXMUSKNW**: 65ct maker fill, book gapped 21c in 31s, strand-cross
  bought back. ZERO same-day warning jumps (candles verified) — no gate could catch it.
  Governor latched exit-only correctly (after the fact, as designed).

## BATCH 3 (J) — DONE + DEPLOYED (`c23e2ac`)
J1 close-cache neg-TTL 1h + 8192 bound · J2 silent_failures per-cycle delta (+ _total key) ·
J3 purge 6h-periodic + caprank-*.jsonl + ws_daemon_log 256MB rotation · J4 SCORES evict 7d/8192 ·
J5 blackout cancel backoff 30-600s · J6 create-fail ratchet 3->60-3600s (unwinds exempt,
in-memory) · J7 WS backoff carried across Feed rebuilds + KALSHI_WS_RESUB_MIN_S=120 pacing.
Suite 718/2xf; mutation 13/13 killed (disclosed gap: J7 main-loop pacing branch asserted by
formula, not by driving async main). Also fixed: test_operator_safety_0728 `_prog` fixture
date-rot (hardcoded end_date expired at midnight UTC; 3 tests failed at clean HEAD).

## SHELVED BY OPERATOR — DO NOT BUILD WITHOUT A NAMED DECISION
Operator ruling 07-30: "we can be big boys and take our lumps hold on changes."
- **Pre-entry toxicity gate: REFUTED by validation.** Jump-rate persistence day-over-day is
  weak (Spearman 0.19 n=68 1d; 0.15 n=15 7d; gas monthly averaged 13 jumps/day then had zero).
  Candle-history entry screening over-blocks and misses. Do not resurrect without new evidence.
- **Live jump-gate (all-out N hours after a >=2c-in-60s jump): validated but EV-negative on
  the reward MODEL.** Replay of 07-29/30 night: saves ~$7.05 whipsaw, forfeits ~$13-26 of
  MODELED rewards in out-hours. Numbers in session transcript; revisit ONLY with receipts.
- **Final-day exclusion: withdrawn** — would strangle the receipts experiment (operator caught).
- Graduated sizing: rejected on principle (operator: all-in or all-out).
- MUSKNW-class no-warning gaps: accepted cost at full size for now.

## STUDIES COMPLETED THIS SESSION (methods+numbers in transcript; all read-only)
- Night-hours: overnight ~$0 cost (4 zero-fill hours), all cost in busy hours.
- Venue rules compliance vs CFTC filing 2026-02-11 (read in full): no 1c/99c anywhere,
  two-sided at touch, all books >= Target Size both sides, $1 payout floor needs 0.12-1.73%
  presence vs 42-94% measured. Filing facts: side with best bid at max price = disqualified;
  snapshot excluded unless BOTH sides reach Target Size (whole book); $1.00/user/period min.
- Task I candle branch CLOSED: sibling jumps contemporaneous at 1-min (lag flat, lift 1.4x
  pooled, 1.0x gas) — no usable lead. WS seconds-branch stays open (telemetry accumulating).
- Positions API trap: field is `position_fp` (plain `position` absent). Orderbook nests under
  `orderbook_fp`. Candles: 24h fits one request (~676 rows), quiet minutes absent.

## NEXT (operator task order; letters canonical)
1. **K — THE RECEIPT**: KXMUSKNW window closes **Jul 31**; most windows **Aug 1-2** (rolling,
   credits post as each closes; first-credit precedent $20.39 per 07-27 UI read). Build
   receipt-vs-model table, set CAPRANK_CALIB, price incumbency X (0.25 PROVISIONAL), then
   present rank-flip + scaling options. Receipts also decide the shelved jump-gate question.
2. **F** — sweep->live-rank wiring: staleness data first; haircut + age cutoff in the SAME change.
3. **H** — index-family retry review (governors cap $5/mkt/day now).
4. Open audit tail unchanged (see 07-29 evening handoff §OPEN).

## BEHAVIOURAL (operator-demanded self-audit 07-30)
Session defects: resumed edits after "no changes" (scope misread); "Friday" shorthand for
rolling receipt dates repeated from own paraphrase; proposed designs before measuring (twice,
one self-refuted); one wrong finding shipped then corrected (orders side-field misread).
**RULE THIRTEEN candidate written** (`feedback_qa_tempo_discipline.md`, indexed): measure
before proposing · re-derive every date/number at speaking time · holds are GLOBAL ·
Q&A never lowers the evidence bar. **Awaiting operator ratification → then add to
`.claude/hooks/inject_verification_rules.py`** (shared checkout — needs the ratify word).

## TOOLING (reuse, do not rebuild)
VPS /tmp: resting.py, session_econ.py, full_review.py, episode_analysis.py, est_rewards.py,
funnel_audit.py, live_summary.py, verify_takers.py, night_study.py, minimums_check.py,
dollar_floor_check.py, sibling_jump_study.py, toxicity_validation.py, toxicity_validation7.py.
