# R3 PART-1 + THE FIX — TARGET RULE CONFIRMED, CAPTURE GATE ARMED (2026-08-25)

Follow-on to `KALSHI_R2_SHARE_ECONOMICS_2026-08-25.md`. Operator: "do it" (C-then-A:
rules-first, then fix the gauge). Outcome: the R2 mechanism is settled from canon +
venue + our own tape, and the fix turned out to be ARMING AN EXISTING GATE (Tier-2
env change), not new code.

## 1. Mechanism — SETTLED (three independent sources)
1. **Filing canon** (CFTC filing 2026-02-11, read in full per
   `KALSHI_HANDOFF_2026-07-30_CAPS_BATCH3.md` studies §: "snapshot excluded unless
   BOTH sides reach Target Size (whole book)"; also "no 1c/99c anywhere", "side with
   best bid at max price = disqualified". LIP params verbatim in
   `KALSHI_AUDITOR_REBUTTAL_2026-07-22.md` §A1.)
2. **Venue** (13:48:09Z read): program e0269fe5 (KXAAAGASW-26AUG31-3.900)
   target_size_fp **1000.00**, DF 5000bps, $100/day pool, start 08-24T14:15Z.
3. **Our own telemetry** (quotes tape 08-24T14:15Z→08-25T13:49Z, 1,336 rows):
   `n_qual` **False in 1,313/1,336 rows (98.3%)** — the NO side of 3.900 reached the
   1,000ct Target only in the program's first ~20 minutes (22 rows, 14:16-14:36Z).
   DIESELW-26AUG24 T5.64 (2,280 rows): **both sides qualified** through its accrual
   days — the positive control that DID earn (est-feed 26→1,270cc).

**Conclusion (ESTABLISHED)**: gas paid $0 because the book's thin side never reached
Target — the whole snapshot is excluded for every participant. The R2 doc's
time-priority/wall hypothesis is SUPERSEDED as the explanation of the zero (the wall
still dilutes share when a book does qualify; not needed to explain $0).

## 2. Why the bot was in gas anyway — root cause
`_prospective_capture` (quoter :2695) implements the both-sides rule CORRECTLY
(:2712 returns $0 when either side fails) — but every consumer of it was disarmed in
live.env (read 13:5xZ pre-change): CAPTURE_GATE=0, NETEV_GATE=0, PRESENCE_GATE=0,
QUALIFIABLE_GATE=0, STANDDOWN=0. MIN_CREDIT_USD=1.50 is consumed only behind
PRESENCE_GATE. Same finding as `KALSHI_INTENT_VS_ACTUAL_2026-07-26.md` §3 ("the
market-quality brain never runs") — still true under the cliff config. Selection
(F9 allowlist) admits by pool/close/runway/shape; nothing live asked "will WE get
paid on THIS book".

## 3. The change (LIVE, 2026-08-25T13:53Z)
- `live.env`: **KALSHI_CAPTURE_GATE=1**, **KALSHI_CAPTURE_MIN_USD_DAY=1.00**
  (floor deliberately below the $5 code default so DIESELW-T5.44 at model $1.53/day
  is NOT collaterally cut; gas sits at exactly $0.000. Raising to $5 = operator call.)
- Backup: `live.env.bak-CAPTUREARM-20260825_135322`. Service restarted 13:53:28Z
  (clear of the 00:00Z window), active.
- **Rollback**: restore the .bak over live.env + `sudo systemctl restart
  polymarket-maker-kalshi-ws` (or just set KALSHI_CAPTURE_GATE=0 and restart).

### Tier-2 disclosure — what is now blocked/allowed
- BLOCKED (flat entry): any two-sided book whose prospective R4 capture < $1/day —
  today that is ALL gas markets (tape replay 10Z+: 6 markets, both_qual≈0, pc $0.000).
  A gas book that later genuinely two-sides at Target with pc ≥ $1 re-admits itself
  automatically — this gate is self-correcting where the static allowlist is not.
- UNCHANGED: held inventory → reduce-only (exits NEVER blocked — verified live:
  3.900 exit resting after restart); diesel T5.44/T5.82 still quoted two-sided
  (verified live 13:5xZ, sizes 40/32 unchanged); T5.42 stays loss-governor-stripped
  (pre-existing, separate mechanism); void/activate path scoped out of the gate.

### Verified / NOT yet verified (honest split)
- VERIFIED: service active; env loaded (grep post-restart); cycles `fails=0`;
  resting set = 1 gas exit + 4 diesel quotes (venue read 13:5xZ); diesels unchanged.
- NOT YET VERIFIED: an actual `capture_skipped` firing (today's flat gas books are
  currently refused earlier by `gate_one_sided_book`; capture bites when one
  transiently two-sides under Target — the state that admitted 3.900 on 08-24).
  Watch: `journalctl -u polymarket-maker-kalshi-ws -f | grep capture` and the
  plan-row `capture_skipped` counter.

## 4. Same-day live context (reads 13:56:30Z)
- KXAAAGASD-26AUG25-4.0600 finalized **YES** → the 5 NO expired: −$0.05 realized
  (the pre-known max downside of that leg).
- Balance $316.0418. Remaining position: −40 NO 3.900 (exit resting).
- Pre-change fills 12:01–13:04Z: three cheap-fill→strand-taker-exit roundtrips
  (T5.42 ×1 tripping its loss governor, AAAGASD-26AUG26 ×2) — the R1 churn shape,
  live; motivates the R1 re-pair/exit-economics design still pending signoff.

## 5. Adversarial review (incl. EV)
- *Does arming risk Bug-14-class blanket cuts?* The gate is book-state-conditional
  and self-reversing (re-admits when the book qualifies), unlike a static
  series/class ban; exits are structurally exempt. Floor $1.00 chosen against the
  measured pc distribution (gas $0.000 vs diesel $1.53+) — maximal separation,
  minimal collateral.
- *Model risk*: pc is the M7 model (over-predicts 2–6×). Over-prediction makes the
  gate ADMIT too much, not block too much — safe direction for a $1 floor.
- *EV*: stops committing cap + fill-risk to books that pay $0 by construction; the
  freed cap flows to qualifying books under existing caps. Cost: a genuinely-about-
  to-qualify book is refused one cycle late at worst (gate re-evaluates per cycle).
- *Numbers*: every figure above carries its read timestamp or tape/doc citation.
