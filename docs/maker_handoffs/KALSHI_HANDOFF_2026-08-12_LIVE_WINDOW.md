# KALSHI MAKER — HANDOFF 2026-08-12. ⛔ SUPERSEDED SAME DAY: OPERATOR-NAMED WIND-DOWN (Option B) EXECUTED 19:50Z. BOT DOWN.

## ⛔ 0-A. WIND-DOWN RECORD (operator: "wind down as cleanly as possible", 2026-08-12 ~19:49Z)
- Sequence executed: STOP written 19:50:13Z (text names the operator order) → `systemctl stop`
  polymarket-maker-kalshi-ws (last plan row 19:50:03Z, dd 9.29 mark-basis — ~1 cycle from the
  $10 auto-halt) → `flatten_kalshi.py` 19:50:24Z: 11/11 resting cancelled HTTP 200, re-read
  **resting=0**, exit 0, balance $252.5294 → service **disabled** (reboot cannot relight).
  Re-verified 23:54:16Z after a session restart: inactive/disabled/STOP present/0 resting.
- **8 positions left to SETTLE by design** (cost sunk; held contracts can only pay or expire —
  no further cash at risk; taker-dumping fractional dust would only pay spread+fees):
  KXRAIN-26AUG13-BOS −3.00; gas 26AUG13 strikes +0.94/−0.29/−0.01/−1.83 (settle ~08-13T04:00Z);
  KXTRUMPTIME-26AUG15-H3 +0.30; KXTRUMPENDORSEMENTS-26AUG14-A20 −0.15;
  KXTOPMODEL-26AUG17-CLAUM −0.43. All conclude ≤ 08-17, inside credit observation.
- **Observation infra stays UP intentionally** (recorders, reward-pnl 07:30Z, scoreboard
  07:40Z, w16 14:00Z, gate-eval 08-18, tape-compress): credits still land and are recorded to
  2026-08-21T01:40:43Z; the window is scored ON THE RECORD on 2026-08-19 per §5 with whatever
  concluded. Window context at wind-down: drag −$20.1788 / credits counted $0 / identity_gap
  0.0000 (scoreboard run-1 19:37:48Z); the drag includes the open-inventory cost basis above,
  part of which returns via the settlements listed.
- Relight, if ever, is a fresh operator-named decision: re-enable + STOP clearing + governor
  baseline reset (§11 precedent) — nothing below this block is live guidance anymore.

# (below = the superseded live-window handoff, kept for the record)

Deep record: `KALSHI_HANDOFF_2026-08-10_POST_INCIDENT.md` §1–§13 (read §8–§13 for this arc).
Worktree: `git worktree list` → kalshi-wt (Temp scratchpad), branch `claude/maker-kalshi-live`
@ `a848b08`. Verify `git branch --show-current` before ANY repo write.

## 0. TRUST POSTURE (unchanged)
Verify, never inherit: md5-vs-git-blob, test EXIT CODES (never grep), timestamped venue reads,
pre-registered predictions. Plan-row keys are CONDITIONALLY EMITTED — no fixed count (08-12
rows measured 106–183 keys, 189 distinct across the day; the 106-key row is the halt row;
`daily_dd_carry`/`daily_dd_raw` appear only in post-relight fixed-build rows) — ENUMERATE the
row you are reading, never guess names or assume a count. The 13
hook-injected operator rules bind. The 2026-07-27 session stays quarantined.

## 1. LIVE STATE (verified 2026-08-12T17:49Z — re-verify; stale by definition)
- **BOT LIVE** since 14:22:30Z on the FIXED build: quoter md5 `57adab17` (= commit `122dd44`
  blob), backup `.bak-OBSHOLD-20260812`. dd was $5.77 of $10 at 17:49:15Z (mark basis).
- **Env armed (byte-verified at deploy): `KALSHI_OBS_HOLD=1`, `KALSHI_DD_CARRY=1`,
  `KALSHI_D3_RUNGS=5,10,25`.** OBS_HOLD verified binding live (`obs_hold_bound` in quote-row
  gates). EST_FEED stays 0.
- Governor: one-time OPERATOR-NAMED baseline reset applied at deploy (peak/start→262.86,
  carry 0.0) — §11 records why it was mandatory (DD_CARRY would have carried the halt debt
  into any restart forever).
- **⚠ BOX ≠ BRANCH HEAD:** commits `6cd6917` (cfg_stamp telemetry) and later are NOT deployed;
  they ride the next quoter restart. Deployed = `122dd44`'s quoter + the fixed offline tools
  (w16 `3a9c2897`, w17 `1f8b9d8c`, credit_feedback `c3d03712`, gate-eval `dab96a44`,
  reward_pnl `14948cc1`).
- Verify:
```
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@18.201.216.0 \
  "sudo -n bash -c 'cd /opt/pa2-maker-kalshi-live; ls STOP; md5sum maker_kalshi_quoter.py; \
   tail -1 plans-$(date -u +%Y%m%d).jsonl | head -c 400; tail -1 cash-202608.jsonl'"
```

## 2. THE MEASUREMENT (pre-registered §10 — do not move goalposts)
- **Window [2026-08-12T01:40:43Z → 2026-08-19T01:40:43Z]**, credits observed to
  **2026-08-21T01:40:43Z**. **PASS iff credits whose PROGRAM concluded in-window >
  |position-aware drag| (replay_fills basis).** Halts do NOT extend the window.
- Ledger so far: **day-1 drag −$9.9663** (auto-halt 06:43:34Z — 2nd KXTRUMPTIME incident,
  one 50ct sweep −$9.50 before the fixes; decomposed §11). Everything after 14:22:30Z runs on
  the build where that class is capped at 5ct (OBS_HOLD) / 25ct (rung trim).
- **LIP lifetime = $189.06 over 61 credits.** ($204.06 total credit_history includes a $15.00
  REFERRAL, 2026-07-24 — never count it as reward income. §12.)

## 3. AUTOMATION NOW RUNNING (all verified exit-0 on first run)
| what | when | notes |
|---|---|---|
| `kalshi-reward-pnl.timer` | daily 07:30Z | accrued-vs-paid by event; PENDING never = LEAKAGE; appends reward_pnl-YYYYMM.jsonl |
| `kalshi-obs-gate-eval.timer` | Tue 08:00Z (first 2026-08-18) | scores Pre-reg 2; first manual run = UNPOWERED (0 concluded paid post-T0) |
| w16+w17 (both run inside `kalshi-w16-report.service`; no separate w17 unit) | 14:00Z | NOW exit nonzero on any alarm (§13) — a `failed` unit means READ THE REPORT. EXIT-MASK FIXED 2026-08-12T18:52Z: the old `w16; w17` ExecStart chain returned only w17's exit, silently masking a w16 alarm; unit now exits max(ra,rb) (backup `kalshi-w16-report.service.bak-EXITMASK-20260812_185157`; verified: systemd argv byte-exact, real run exit 0 both logs appended, file-shipped negative test 1/1/0) |
| `kalshi-window-scoreboard.timer` (ADDED 08-12, operator-named) | daily 07:40Z | running window gauge: in-window-concluded credits vs position-aware replay_fills drag since T0; appends window_scoreboard-YYYYMM.jsonl. Script `kalshi_window_scoreboard.py` md5 `8f0f8883` (= `6bc46f5` blob); first run 19:37:48Z exit 0, identity_gap 0.0000 vs the T0 cash baseline. ⚠ READ IT RIGHT: drag includes the COST BASIS of currently-open inventory with zero credit for its value (same construction as the §5 verdict basis) — a live book overstates the running drag vs a flat one; `pass_now` is a trajectory hint, NEVER the verdict. Bucket check on run 1: pre_t0 $5.11/3 + unmapped $198.95/36 = $204.06 = exact credit_history lifetime |
| `kalshi-tape-compress.timer` | 02:30Z | gzips >7-day tapes; live consumers read newest only |
| estimates + cash recorders | 5 min | unchanged, healthy |
| logrotate | nightly | root-fixed §13; 0 failed units on the box at handoff |

**ONE-TIME CHECK DUE 2026-08-13 morning (operator-named 08-12):** first 00:00Z day-rollover on
the DD_CARRY build (deployed 14:22:30Z 08-12). Read the first plan rows of plans-20260813.jsonl:
expect `daily_dd` reset and `daily_dd_carry` 0.0 (no halt outstanding). Any carry ≠ 0 without a
halt = a DD_CARRY rollover bug — report before anything else.

**MONITOR-ON-EVERY-REVIEW (operator 08-12: "monitor the other 3 on all reviews"):** (a) 9a/9b
early-run option — default stays 08-19; (b) tape-compress gzip-failure non-propagation
(`find -exec {} \;` masks gzip exit; consequence = uncompressed tape retries nightly); (c)
plan-row schema drift — keys are legitimately conditional (106–183 measured 08-12), no alarm
buildable without a mandatory-key spec; cfg_stamp covers the two footguns once deployed.

## 4. IF THE BOT IS HALTED WHEN YOU ARRIVE
An auto daily-loss STOP is operator-reserved — do NOT clear without the operator naming a
restart. **DD_CARRY is LIVE: a restart after a halt CARRIES the open drawdown** (that is the
fix working, not a bug). Same-day relight therefore needs the operator to also name a governor
baseline reset (one-time state edit precedent + exact keys: §11 + deploy script in the 08-12
transcript). Never restart within ~60 min of 00:00Z. Decompose any loss with replay_fills
BEFORE narrating it (the mark-basis dd overstates on wide books; §2 of the 08-10 doc).

## 5. DAY-7 VERDICT PROCEDURE (2026-08-19, credits final 08-21T01:40:43Z)
1. Credits: `credit_history` rows whose event's PROGRAMS concluded in [T0, T0+7d] (program
   map `end_date`; the map is merge-only and survives program disappearance from the active
   list). 2. Drag: replay_fills over fills in [T0, T0+7d] + settlements. 3. PASS iff
   credits > |drag|. 4. Report accrued-but-unconcluded alongside, never counted. 5. Same day:
   read the Tue gate-eval output — if PASS, OBS_HOLD's floor is validated on a paid basis; if
   FAIL on fidelity, re-derive the $1.20 floor constant before trusting it further.

## 6. PARKED ON NAMED TRIGGERS (nothing demoted — triggers verbatim)
- **EST_FEED (+ M-9/M-6/M-10 guards):** only if the verdict shows floor-refusals of markets
  the venue says are paying. Arming without guards = the verified flat-activate hazard.
- **9a/9b studies:** revisit at the verdict; OBS_HOLD live telemetry may already answer them.
- **Halt-meter mark basis:** trigger = a halt where venue-basis loss < half the meter's dd.
- **Proposal B (per-event concurrency cap):** trigger = fresh-sizing bursts recurring
  DESPITE OBS_HOLD. Honest value range −$4.91/≈−$10.79/−$16.62 by ordering (§11).
- **cfg_stamp deploy (`6cd6917`):** rides the next quoter restart, whatever causes it.
- **Scale rungs ($350→…):** gated on the verdict (the ratified scale plan's own condition).

## 7. TRAPS (cost money or truth before — do not relearn)
- `incentive_programs?limit=1000` silently TRUNCATES (3,278 actual; no usable cursor).
  Use `limit=10000` — the quoter/recorders already do.
- **OBS_HOLD is inert unless `KALSHI_D3_RAMP=1`** (both currently set; cfg_stamp will alarm
  this once deployed). Malformed `KALSHI_D3_RUNGS` silently falls back to `5,10,25,50` —
  byte-verify after ANY env edit. `_envb` needs literal `1` ("true" = OFF).
- Fill direction is ACTION-ONLY yes-signed; fields are `*_fp`/`*_dollars`; credits are
  EVENT-level, never divisible to a ticker; a credit "reason" without "for event X" exists
  (the referral) — the reward tools bucket it as `?`, never attribute it.
- Recorder `cash` alone is NOT equity (resting reservations); the quoter's dd is MARK-basis
  (bid-marks long inventory). Headline the blindness, not the number (Rule Twelve).
