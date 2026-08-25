# R1 — HOLD-STATE MACHINE MAP + RE-PAIR-AFTER-CHEAP-FILL DESIGN (2026-08-25)

Overhaul-review item R1 (`KALSHI_HANDOFF_2026-08-25_OVERHAUL_REVIEW.md`). Study only —
NO code change. The design in §3 requires operator signoff before implementation.

Sources: deployed quoter blob (md5 `6753e6c3` = HEAD blob, verified 03:4xZ), live.env
(read 03:47Z), live journal 08-24/25, venue authed reads 03:46:54Z, D4 raw tape,
est-feed history. All line numbers = `kalshi_live/maker_kalshi_quoter.py` @ `1d25545`.

## 1. The state machine (per ticker)

```
                 +--------------------------------------------------------------+
                 v                                                              |
 [GATED-OUT FLAT] --gates pass--> [QUOTING-PAIRED] --any fill >= INV_TOL--> [HOLDING/EXIT-ONLY]
   (no quotes)                      (join 40ct both                             |
                                     sides at touch)         +------------------+-----------------+
                                                             | maker exit fill  | taker exits     | settlement
                                                             v                  v                 v
                                                          [FLAT]          [REENTRY-COOL 3600s] [market gone]
                                                             |                  |
                                                             +--- gates --------+--> back to QUOTING-PAIRED
```

| State | Emits | Earns (venue-actual, this window) | Code |
|---|---|---|---|
| GATED-OUT FLAT | nothing | $0 | gates at :3234-:3251 + selection |
| QUOTING-PAIRED | join both sides at reference, 40ct after ramp | **$0 measured on 3.900 despite 2.5h paired** (see R2 doc — market-specific, DIESELW positive controls DID accrue) | :3332-:3399 |
| HOLDING/EXIT-ONLY | `_reducing_quotes` ONLY | est-feed: no row (confounded by R2 wall — cannot attribute to unpairedness on this market) | **:3328-:3331** |
| NAKED >=15s | strand taker-cross attempt each cycle | $0, pays taker fee when it crosses | `_strand_cross` :7105 |
| PRECLOSE naked | taker flatten attempt | $0 | `_preclose_naked_flatten` :7002 |
| REENTRY-COOL | unwind only, no accumulating | $0 | :4818-:4833 |

### Key transitions, with measured latencies
- **Fill → exit resting**: same or next cycle (~66s cadence; journal 08-24T19:49 fill →
  exit resting; re-rested every cycle since, `created_time` on the resting orders
  refreshes each cycle, venue read 03:46:44Z).
- **HOLDING duration is queue-dependent, unbounded**: the 3.900 exit (buy YES 0.98)
  rests AT the touch level occupied by a ~1,020ct rival wall (D4 raw tape 08-24T17-19Z:
  yes-bid level 1,040-1,060ct incl. our 40) — it fills only if sellers cross ~1,000ct
  ahead of us or the wall pulls. Measured: fill 08-24T19:49:13Z → still holding at
  03:57:06Z read = **8.1h and counting**.
- **Exit-fill economics**: NO basis $0.01/ct + YES exit 0.98 → $1-pair redemption =
  +$0.01/ct if filled. AAAGASD variant: 0.99 exit → +$0.00/ct - fees ≈ breakeven.
- **HOLDING => EXIT-ONLY is UNCONDITIONAL** (:3328): operator directive 2026-07-27,
  made unconditional by Q1 decision 2026-07-28 (KEEP_BOTH/minjoin machinery removed;
  last live copy `git show 228bedd^:kalshi_live/maker_kalshi_quoter.py`). One
  tolerance-crossing fill (INV_TOLERANCE=1 ct, live.env) strips the accumulating side
  until flat. Same guard on the JOIN_ALWAYS drill path (:3258) and the void/activate
  path (:3269).
- **reentry_cool (3600s, live.env KALSHI_REENTRY_COOLDOWN_S=3600)** is stamped ONLY by
  taker-family exits: strand cross (:6199-:6203), preclose taker (:6175-:6179),
  settle/ladder taker (:5403-:5405). A pure maker unwind fill does NOT cool — re-entry
  next cycle, subject to gates. NOTE: memory's "15-min reentry_cool" note is stale; the
  observed 15:28→16:28Z re-entry on 08-24 matches 3600s exactly.
- **Re-entry blockers after flat**: entry gates incl. MIN_RUNWAY_H=49 — the pending
  operator ask (re-entry exemption when est-feed accrued > $0.50) lives on this edge.

### Why exit-only exists (the 07-27 rationale, quoted from code :3317-:3327)
KXNDQHUD 2026-07-27 live tape: while HOLDING an adverse mid-band position the quoter
re-posted the same losing side (19:06:44 sell 20 @ NO 0.40 → 19:07:16 sell 17 @ 0.34 →
19:07:31 sell 5 @ 0.30); 22 of the 42 contracts existed solely because of the re-post.
The rule kills loss-compounding on ADVERSE fills in directional moves. That regime is
mid-band, meaningful basis, price moving against us.

### What it costs in the CURRENT regime (extreme-shell, cheap-side fills)
A 1c-basis NO fill has max further downside = basis already paid (here $0.40 total).
The risk the 07-27 rule defends against is bounded at pennies — but the rule still
forfeits the two-sided-presence state for the whole holding period (8.1h and counting
on the flagship market). "Fills stop scoring" (R1-probe canon) is thereby implemented
as a one-way trapdoor, not a re-pairing.

## 2. Which states earn — honest statement of the confound
Per the R2 study (same date): during this window even QUOTING-PAIRED earned $0 on
3.900 (wall mechanism), while DIESELW-26AUG24 programs DID accrue for 5ct pairs
(est-feed rows 26→1,270 and 89→820 centicents). So:
- The claim "exit-only forfeited ~8h of accrual on 3.900" is **NOT established** — on
  THIS market, paired presence also earned 0. The 8h forfeit is real only in markets
  where paired presence actually earns (e.g., the DIESELW shape).
- R1's dollar impact is therefore **contingent on R2's answer**. This is a report of a
  dependency, not a demotion (Rule Nine): the state-machine map and the design below
  stand as commissioned.

## 3. RE-PAIR-AFTER-CHEAP-FILL — design for operator signoff (NO code yet)

**Intent**: restore the earning state (two-sided) after a benign cheap-side fill,
without re-opening the 07-27 loss-compounding hole.

- **Trigger**: in the HOLDING branch (:3328), when ALL of:
  1. avg cost of held inventory ≤ `KALSHI_REPAIR_BASIS_MAX_D` (proposed default $0.02/ct
     — covers 1c-2c cheap-side fills; the 07-27 incident basis was $0.30-0.40/ct, far
     above the line);
  2. the market would pass the FLAT entry gates this cycle (band, runway, mid-band-out,
     one-sided/wide, storm when armed — no gate bypass);
  3. re-pair size respects caps: `count = min(join_after_ramp, INV_HARD_CT - |inv|)`,
     per-market capital cap, F15 total cap, halt intact;
  4. one price per ticker+side (venue constraint already enforced).
- **Action**: emit the CONSUMED side again (accumulating, at reference) alongside the
  unchanged `_reducing_quotes` exit. The exit keeps absolute priority on the write
  budget (existing reducing-first ordering preserved).
- **Off switch**: `KALSHI_REPAIR_CHEAP_FILL` (default **0** = byte-identical behavior).
- **Risk bound**: each additional fill adds ≤ basis×count ≤ $0.02×40 = **$0.80** max
  further downside per re-pair round; INV_HARD_CT bounds total inventory; the existing
  breaker/governors are untouched and still strip accumulating quotes when tripped.
- **Interaction with 07-27 rule**: preserved verbatim for basis > threshold (mid-band
  adverse fills stay exit-only). The rule's own incident tape (NDQHUD $0.30-0.40 basis)
  would still be exit-only under this design.
- **EV lens (honest)**: expected benefit = (accrual/h of paired presence on this
  market) × (holding hours avoided). Current evidence says that accrual is $0 on
  wall-dominated books and ~$0.002/h on the DIESELW shape (sub-$1 → paid $0 at window
  end). **Do not ship before R2 resolves what paired presence earns and where** —
  otherwise we'd add an accumulating-side risk (bounded but nonzero) for provably $0
  reward on exactly the books we currently hold.

## 4. Per-section adversarial review (incl. EV lens)
- *Is the state map complete?* Governor/breaker overlays (_exit_only_mkts, mkt_out,
  two-strikes, exit_only_all) compress into "HOLDING/EXIT-ONLY behavior applied to
  non-held tickers"; they're listed in §1 table but not drawn as separate nodes —
  acceptable for R1's purpose (earning states), noted here so it isn't silently lost.
- *Could re-pair churn?* Fill → re-pair → fill → re-pair loops at 1c: each round costs
  ≤ basis and books +1c-if-paired exits; the loop is capped by INV_HARD_CT. But
  repeated cheap fills on one side signal one-way flow — storm detector (armed later)
  and INV_SOFT/HARD throttles are the existing guards. Design deliberately does NOT
  add a new cooldown; reviewer flags this as the weakest point — an optional
  `KALSHI_REPAIR_MAX_ROUNDS_PER_DAY` knob is listed as an operator option, not a
  recommendation (Rule Nine: additive option only).
- *EV*: §3 EV-lens paragraph is the review conclusion: implementation order should be
  R2-then-R1-code. Stated, not silently reordered — R1 design work (this doc) is done
  first exactly as the operator ranked it.
- *Numbers check*: 8.1h (19:49:13Z→03:57:06Z reads), 1,020ct wall (D4 raw 08-24T17-19Z),
  3600s (live.env read 03:47Z), $0.80 bound (arithmetic from proposed knobs), NDQHUD
  tape (code comment :3321-:3325, primary tape cited there). All sourced.
