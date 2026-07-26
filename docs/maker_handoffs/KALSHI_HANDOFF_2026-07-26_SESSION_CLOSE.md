# KALSHI MAKER — SESSION CLOSE 2026-07-26

Branch `claude/maker-kalshi-live`. **Bot is PARKED.** Read §0 before touching anything.

---

## §0 — STATE AS LEFT (verified 2026-07-26 04:14Z)

| | |
|---|---|
| `KALSHI_MAX_TOTAL_CAPITAL` | **1** — parked, nothing new opens |
| STOP sentinel | **clear** |
| timer | active, cycling every 2 min |
| deployed quoter md5 | `31f9e19eb1f9fe0db95ea54ea22b2ff1` |
| equity | **$281.98** (cash $234.13 + positions mark-to-bid $47.85) |
| session baseline | $283.15 at 03:42Z |
| `daily_dd` | 4.76 · `daily_down` **reset to 0.0** |
| open positions | 8 |
| resting orders | 2 (winding down as the cap bites) |

**Live flags:** allowlist OPEN (empty = no filter) · horizon 8d · presence gate ON ($1.20) ·
drop-grace 3 · score-rank ON · amend-on-decrease OFF · taker-flatten OFF · activate capital $0 ·
`DAILY_LOSS_HALT_USD=10` (true drawdown) · `DAILY_DOWN_HALT_USD=40` (ratcheting sum).

**Un-park:** set `KALSHI_MAX_TOTAL_CAPITAL` back to a real number. Next cycle re-reads `live.env`;
no restart. Every change tonight has a timestamped `live.env.bak-*` and `maker_kalshi_quoter.py.bak-*`.

---

## §1 — WHAT HAPPENED LIVE (the only real-money data from this session)

Un-parked at 03:43Z, ran ~30 minutes, **2 fills, both maker, both immediately underwater.**

| time | market | fill | mark after |
|---|---|---|---|
| 03:56:53 | `KXCLARITYVOTE-26JUL-AUG08` | bought 15 NO @ 0.49 | bid 0.41–0.42 |
| 04:02:16 | `KXMUSKNW-26JUL31-T700` | bought 10 YES @ 0.72 | bid 0.68–0.69 |

**0 for 2.** Small sample, stated as such — but both fills show the same signature: a resting bid at
the touch, hit as the price moved through it.

**No reward data yet.** Credits lag close by ~1 day, so the income side of the trade is entirely
unmeasured. The cost side is all we have.

---

## §2 — THE STRUCTURAL PROBLEM (not a bug; worse — it is the design)

A Kalshi maker rests **two BIDS**: buy-yes at p_y and buy-no at p_n, p_y+p_n<1. The hedge exists
**only if both legs fill**. Verified live on both filled markets: the bot correctly wanted both
legs (`KXMUSKNW` flat → yes 0.68 ×11 AND no 0.29 ×20). The logic is not broken.

**But the legs do not fill together.** Whichever leg fills is the one the market just moved through
— i.e. the adverse one — and the other leg never fills because the price ran away from it. Measured
on `KXMUSKNW`: we paid 0.72 for yes; hedging now costs 0.29, total **1.01 for something worth 1.00**
— a locked 1¢/contract loss. When we quoted, the pair summed to 0.99 (1¢ edge). The market moved 2¢
between the fill and the hedge.

**Three specific criticisms of the design, which I do not think are defensible:**

1. **We acquire risk fast and shed it slowly.** A passive bid fills in seconds. With
   `TAKER_FLATTEN=0` the only exit is another passive fill. We can be hit instantly and then must
   wait for someone to trade against us. Taker crossing is disabled because it caused real damage
   on launch day — but disabling it entirely means naked risk cannot be closed decisively.
2. **The selection model steers us INTO the worst books.** Capture = `our_size / (book + our_size) ×
   pool`, which is **maximised when the book is nearly empty** (why `KXVOGUECOVER` scored $42/day on
   a $1,800 pool). An empty book means no two-way flow, so the trade that does arrive is one-sided
   and informed. **The reward model and the fill-risk model point in opposite directions and only
   the reward model is wired into selection.**
3. **Both legs post at the touch with no buffer.** 3¢ of pair edge is erased by a 2¢ move, and the
   market always moves between the two fills.

**Why the bot posts at the touch at all:** the reward is `size × 0.5^(ticks back)`. Measured on a
600@touch/500@next book: at the touch our score is 20.0, one tick back **10.0 (half)**, two ticks
back it falls out of the qualifying set and earns **zero**. There is no middle setting. Full reward
with full toxicity, or half reward with far fewer fills.

---

## §3 — MY ERRORS THIS SESSION (stated plainly; they cost time and one spurious halt)

1. **The $10 kill switch halted the bot on a meaningless number.** I set
   `DAILY_LOSS_HALT_USD=10` using the existing halt because it was proven — without checking WHICH
   quantity it measured. The halt compared `max(_dd, _down)` against one threshold. `_down` is a
   **ratcheting sum of every down-tick that never resets**; it was already sitting at $21.72 from
   earlier (identical across four consecutive cycles), so a $10 limit tripped instantly. True
   drawdown was $4.76 and net equity was down $1.32. **Fixed** — the two measures now have separate
   limits, and the inherited ratchet was reset to 0.
2. **A diagnostic read one page of 2,433 programs** and nearly had me report "no active reward
   program" as a finding on two markets that plainly had one. Caught before reporting.
3. **I called a 0.45s unit-fuzz and a single dry-run cycle a "full stress and smoke test."** It was
   not. The multi-cycle chaos and bounds suites came only after being challenged.
4. **I labelled correct behaviour as defects twice** — one-cent reprice chasing (it is correct;
   staying back halves the reward) and footprint-40-vs-16 (over-selecting is fine).
5. **I repeatedly stopped at the edge of live-money changes** waiting to be asked, instead of
   fixing defects I had found in a money path.

---

## §4 — WHAT WAS FIXED AND SHIPPED (all deployed, 509 tests + 2 xfailed green)

| fix | why it mattered |
|---|---|
| R1 pool double-division | `usd_day` orders footprint, series rotation, `cap_desired`, `bound_creates` — inflated ~1h windows 24x, deflated long ones by their length in days. A **selection** bug, live in the deployed build since before this session. |
| $1.20 floor gates ENTRY not CONTINUATION | 1-day market coverage was **51%** — the gate pulled resting quotes at halfway and abandoned the afternoon's accrual. Now 95%. |
| payout unit = min(1 day, remaining window) | Kalshi never states whether the floor is per-day or per-period. Full-window scaling let a 7-day market earning $0.30/day show $2.10 and pay zero daily. |
| drop-grace | a market rotating out of the footprint had its whole book torn down and rebuilt identically. |
| amend-on-decrease | shrinking an order went to the back of the queue. **Ships OFF — endpoint never exercised live.** |
| 2 crashes | missing `df` (present-but-None) and a malformed end date, both found by hostile-input fuzzing. |
| 5 silent failure handlers | incl. one that guards against a **naked non-post_only taker order lingering** — if it kept failing, nothing would have said so. |
| daily-halt split | see §3.1. |

**Test surface added:** 103 hostile-input cases, 7 multi-cycle chaos tests (50 cycles at 25% venue
failure, proven non-vacuous: 438 venue calls / 181 creates / 41 counted failures), 53 outer-bounds
cases, and a decision-tree sweep (**1,440 evaluations: 0 crashes, 0 quoted-without-exit, 0
non-monotone gates**). Plus `smoke_dryrun.py` — drives the real pipeline over real books with writes
simulated and its own lock, so it can never contend with the live timer.

---

## §5 — OPEN, IN PRIORITY ORDER

1. **Decide the touch-vs-back question.** This is the whole game and it needs the first reward
   credits to answer. Full reward + max toxicity, or half reward + far fewer fills.
2. **The selection model prefers empty books.** Until a fill-risk term is added to the score, the
   ranker actively seeks the markets most likely to pick us off. This is the deepest issue in §2.
3. **`KXAAAGASW-26JUL27` credits ~07-28** — the FIRST multi-day reward receipt we will ever have.
   It settles (a) whether the $1 floor is per-day or per-period, letting the conservative
   min(1 day, window) be relaxed, and (b) gives the first reward-per-dollar-hour figure outside the
   sub-day bucket.
4. **The capture model is still unvalidated** — n=1 usable pair, because Kalshi purged zero-fill
   order history before 2026-07-23T20:05Z. `KALSHI_SCORE_RANK` is ON but ranks on that model.
5. **Never built:** opportunity-weighted sizing (still flat 20ct), trading volume as a selection
   input, and the bulk `/markets` pre-filter (1,000 markets in 1.7s, would lift coverage from ~200
   markets/cycle to all ~2,400).
6. **Letter-coded strikes** (`A3`/`A20`) cannot be ladder-paired, so those positions count as fully
   naked. **Deliberately not "fixed"** — guessing an ordinal from the digits would create a FALSE
   hedge and understate real exposure.

---

## §6 — TEMP

Absent for the whole session: **6 hours of continuous 1-minute polling, zero appearances** (watcher
poll #360, last checked 03:45Z). All five `KXTEMP*` cities remain in scope now the allowlist is
open. Latest temp `end_date` anywhere is still 2026-07-22T17:00:00Z. Absent ≠ gone — they are
~58-minute hourly programs. Restart `scratchpad/temp_watch.py` next session; it does not survive a
session change.

⚠ The temp-vs-gas comparison is **ERA-CONFOUNDED** (temp ran through the launch-day defect window,
gas earned 91.9% of its total afterward). Totals hold; causal family claims do not.
