# WORST-CASE SHEET + STANDING VERIFICATION PROTOCOL — 2026-09-06

Operator question this answers: "how do we verify you will stop losing me money with
your bugs, misreads, mislabels, gaps and flaws." The answer is structural: the bound
must hold even when the agent is wrong. Three layers, ordered by what has to fail
before money moves. Every number cites its source; env values re-read from
`/opt/pa2-maker-kalshi-live/live.env` 2026-09-06 (session reads ~16:4xZ).

## LAYER 0 — ARITHMETIC (holds even if ALL software is dead or buggy)

The next session runs RAMP-FLOORED (A3b marker, code-enforced, pinned): every
accumulating quote ≤ D3_RUNGS[0] = **5ct** per side per market.

- Resting at any instant: FOOTPRINT_TOP=5 markets × 2 sides × 5ct.
- Worst fillable NAKED wave (only losing sides fill, everything settles against us,
  no guard fires): 5 mkts × 5ct × MAX_PRICE_DOLLARS 0.985 = **−$24.63 ceiling**.
  [ESTABLISHED — pure arithmetic from env keys FOOTPRINT_TOP / D3_RUNGS / MAX_PRICE]
- Both-sides fills pair to $1 redemptions (pairs ≈ riskless; loss = spread+fees only).
- Re-accumulation after a fill is blocked at INV_TOLERANCE=1 (exit-only strip,
  quoter :3538); a burned market cools 3600s (REENTRY_COOLDOWN_S). A repeat wave
  needs new markets AND the daemon alive — a dead daemon quotes nothing.
- Activate/anchor paths ride the same D3 ramp floor (ramp clamps every reason except
  unwind/macro_probe, ACDG review B2(iv) — verified code).

**Absolute ceiling for the first watched session ≈ one naked wave, −$24.63.**
Compare: the 09-01 GO window realized −$19.9517 at 100ct sizing (venue fills, cent-
exact recon) — an agent-defect event per the standing decomposition; the floor alone
makes that event's shape ~20x smaller (5ct vs 100ct).

## LAYER 1 — MACHINE HALTS (fire without any agent watching; all box-verified 09-06)

- Daemon day-loss halt: DAILY_LOSS_HALT_USD=10, mark-based, writes STOP + flattens.
  MEASURED overshoot on the one live firing: triggered at $16.29 vs $10 armed
  (gap-through, 09-01 18:35:37Z STOP text). So the realistic realized bound when this
  is the binding guard is ~$10-16, not $10 exactly. [MEASURED, n=1]
- Continuous fill-watch (A4ii systemd unit, starts/stops with the daemon,
  Restart=always): auto-STOP at realized_est ≤ −$5 — the TRUE-loss gauge
  (equity-at-cost; a benign open fill does NOT trip it — the raw balance gauge moves
  −$29.00 on one 100ct open, measured 09-01 18:32→18:37Z rows). Replay-verified:
  banner fires on the GO window, STOP write only on true loss.
  Blind spot stated (Rule 12): an OPEN position marked against us reads ~0 on this
  gauge until exit/settle — that risk belongs to the mark-based daemon halt above.
- PRECLOSE_FLATTEN=1: no naked ride into settlement. Settlement of held inventory is
  the one channel not halt-bounded (K5 canon) — at the 5ct floor its ceiling is
  inside the Layer-0 number.
- Start gating: systemd ExecStartPre preflight (bare `systemctl start` refused on
  STOP / rails drift / unacked config change — live-tested refusal 09-06), StartLimit
  fails a crash-loop closed, safe_start fail-closes if the watcher won't run.

## LAYER 2 — INDEPENDENT RE-MEASUREMENT (catches the agent's claim errors)

This is the layer that catches what Layers 0-1 can't: wrong claims, mislabels, gaps.
It is the thing that ACTUALLY WORKED this week — the 09-06 blind review re-measured
every claim of the 09-01→06 session and caught: 2 refuted claims (fill-watch replay
"alarm" that never fired; "zero receipts feedback" while D2_FEEDBACK=1+ALLOC_KEY=1
were live), 6 unclaimed holes (N1-N5 + the crash-path over-correction), and 17
date-rotted pins. None of those were caught by the session that made them.

**STANDING PROTOCOL (codified here):**
1. Any session that ships code, config, or money-relevant claims ends with a
   claim-listed handoff (the KALSHI_BLIND_REVIEW_HANDOFF pattern).
2. Before the next GO — and after any such session — a FRESH session re-verifies
   every claim from primary sources (venue API + sudo box reads + md5 + code), with
   the standing instruction that a non-reproducing number IS the finding.
3. Money is the test, not agent reports: the pre-registered credited-$ rule stands
   (daily 07:30Z credit_history read → ONE absolute number; 7×$0 → stop-or-change).
   Credits come from the venue's ledger, not from anything the agent computes.
4. The operator can check the guard state personally, no agent in the loop:
   - gate + rails + STOP:  `sudo /opt/pa2-maker-kalshi-live/venv/bin/python /opt/pa2-maker-kalshi-live/kalshi_preflight.py` (exit 0 = clean)
   - watcher armed:        `systemctl status polymarket-maker-kalshi-fillwatch`
   - credited truth:       the daily credit read (venue ledger, timestamped)

## WHAT THIS DOES NOT DO

No layer makes the agent right. Layer 0 caps how much a wrong agent can cost in one
session (−$24.63 arithmetic ceiling at the floor); Layer 1 caps realized bleed
(~$10-16 measured-class) without anyone watching; Layer 2 catches wrong claims before
they compound into the next session's config. Size-ups move the Layer-0 number and
therefore go back through this sheet: any change to D3_RUNGS floor, FOOTPRINT_TOP,
MAX_PRICE, or the A3b marker lifecycle re-computes the ceiling in the change's
approval line.
