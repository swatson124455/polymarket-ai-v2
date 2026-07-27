# KALSHI MAKER — HANDOFF 2026-07-27 (option B landed; everything else still TABLED)

**Bot still PARKED.** STOP present, `MAX_TOTAL_CAPITAL=1`, `TAKER_FLATTEN=0`.
Un-park needs explicit operator sign-off and has NOT been given.

**NOTHING WAS DEPLOYED THIS SESSION.** The VPS still runs the pre-change files —
verified by md5 at session end, identical to session start. `-ws.service` active,
STOP present, `live.env` unmodified.

⚠ **RULE TEN**: the earlier part of the 2026-07-27 session remains QUARANTINED. Nothing
below is drawn from it. Every number here is a fresh measurement taken this session with
its own method, or the two 07-26 audit docs cited by section.

---

## §0 — WHAT LANDED: STAGE C (option B), commit `e237283`, branch `claude/maker-kalshi-live`

The cold cycle's book reads are served from the WS mirror instead of ~40 serialized REST
round trips. **Committed, NOT pushed, NOT deployed, and ships DISARMED**
(`KALSHI_WS_BOOK_COLD` default 0).

### The mechanism
- `maker_kalshi_quoter.py` gains `BOOK_SOURCE` (default `None`) and `_get_book()`.
  `None` => every book read is the same REST fetch on the same path as before, so a timer
  run or a manual quoter run is byte-identical. Pinned by
  `test_p1_flag_off_never_installs_the_provider`.
- `maker_kalshi_ws_daemon.py` installs `Daemon.mirror_book` for the duration of its own
  cold cycle, and restores the previous value in a `finally` so a raising cycle cannot
  leave a provider installed.
- Two call sites changed, both inside `run_once`: the footprint quote loop and the strand
  unwind loop. **Deliberately unchanged: the flatten / taker-cross book reads**
  (`_flatten_all`, `flatten_to_zero`, `_taker_cross_capped`). Those price an order that
  CROSSES — a different risk class from a resting quote — and were never part of the cold
  cycle's read count.

### The staleness predicate — the entire safety of the change
`mirror_book` can only ever **DECLINE** (return `None` => REST). It can never make a book
be skipped, and never suppresses a fetch it did not answer. It declines on:
no feed; `orderbook_delta` not ACKed on this connection; ticker not watched; mirror dirty
(seq gap / disconnect / unparseable delta / never seeded); the mirror going dirty
mid-read; a concurrent-mutation `RuntimeError` after 3 retries.

**Deliberately NO per-ticker age bound.** A quiet ladder book is legitimately silent for
minutes (`RECV_TIMEOUT_S=300`); silence is evidence of a quiet market, not a stale mirror.
Liveness is proven by the ping keepalive (10s/20s), whose failure routes through the same
except arm that dirties every mirror. An age bound would force mass REST fallback on
healthy quiet books and buy nothing the ping does not already buy.

**One race that did not exist for Stage B and does for Stage C:** `run_once` runs on a
worker thread (`asyncio.to_thread`) while the event loop keeps applying deltas, so
`BookMirror.rows()` sorts a dict being mutated. Handled by retry-then-decline; pinned by
`test_p4_concurrent_mutation_never_raises_kills_broken_variant`.

---

## §1 — MEASURED THIS SESSION (VPS, dry_run, isolated DATA_DIR, 2026-07-27)

**Preconditions — all four measured before a line was written:**

| question | method | result |
|---|---|---|
| does the mirror equal the REST book? | sandwich shadow-compare (mirror -> REST -> mirror), full level maps both sides | **78/78 exact, 0 mismatches** over 40 footprint tickers x 2 rounds |
| do our OWN resting orders appear in the mirror? | live resting order, mirror vs REST at its price level | **identical — 9.0 ct @ $0.76 in both**, so `own` subtraction is unaffected |
| how fast do mirrors seed? | clean-count timeline | **39/40 in 1.7s**; the 1 holdout has a genuinely EMPTY book the venue sends no snapshot for -> dirty forever -> REST forever (correct) |
| footprint churn | two selections 150s apart, scores NOT updated | 40/40 overlap, 0 added, 0 dropped |

**Decision equivalence on live books** (the book is the only variable; same market dict,
same clock, same own/inv/event_delta/cost):
`desired_quotes(mirror)` vs `desired_quotes(REST)` — **38 identical, 0 different**,
2 declined to REST.

**Paired interleaved A/B, 3 pairs** (alternating so drift cannot favour either arm):

| arm | wall clock | reads | books from mirror |
|---|---|---|---|
| REST | 13.84s / 13.68s / 13.76s | 41 | 0/40 |
| MIRROR | 1.60s / 1.61s / 1.54s | 3 | **38/40 (95%)** |

**Mean 13.76s -> 1.58s: 88% faster, ~12.2s saved per cycle.**

**Two numbers re-derived, and one of them disagrees with the prior handoff:**
- REST orderbook round trip: **320ms p50 / 327ms p90 / min 300 / max 388 (n=78)**.
  The prior handoff recorded 254ms p50 (n=12). Use 320ms.
- WS feed latency: **47ms p50 / 76ms p90 (n=342)**, 0 seq gaps, 0 error frames.

---

## §2 — A TRAP WORTH KNOWING (cost me a wrong intermediate number)

An intermediate harness measured only **9/40** books served and I nearly reported ~20%.
That was the harness, not the bot: it ran two cycles without resubscribing in between.

The real behaviour: `SCORE_RANK=1` re-ranks on measured capture, so the footprint selected
**before** a cycle and **after** it differ by ~60-72% (measured: 11-16 of 40 overlap across
3 consecutive `run_once` calls — the bot walks the strike ladder, e.g. `KXBA-26JULDELIV-150
-> -155 -> -160`). But the daemon resubscribes **after** `run_once` and **before** the next
one, and both selections then see the same score state — so the watch set is aligned and
the realized hit is 95%. **Any future harness that drives `run_once` in a loop must
resubscribe between cycles or it will measure a hit rate that production does not have.**

---

## §3 — VERIFICATION

- Suite: **587 passed + 2 xfailed** (567 baseline + 20 new in `test_ws_book_cold.py`).
  Run from the probe dir: `cd kalshi_live && python -m pytest -q`.
- **Mutation-tested.** 8 deliberate defects, introduced one at a time, suite re-run each
  time: drop the dirty check; truthiness instead of `is not None`; drop the channel-ACK
  check; leak the provider past the cycle; let a provider exception escape; drop the
  mid-read recheck; swap yes/no in the served book; remove the race retry.
  **All 8 killed.** Two survived the first pass (the dirty check was masked by the
  post-read recheck; the truthiness mutant was masked by a fixture that returned a truthy
  dict) and the tests were sharpened until they died.

**NOT verified:** behaviour under a real seq gap or mid-cycle disconnect on live traffic —
0 gaps were observed in 342 feed messages, so those fallback arms are covered only by unit
tests. That is the residual risk and it is why the flag ships off.

---

## §4 — TO ARM IT (operator decision; nothing here has been done)

Deploy is a restart of a live-money service and was not authorized this session.

```bash
git -C <worktree> push origin claude/maker-kalshi-live
```
Then copy the two files to `/opt/pa2-maker-kalshi-live/`, add
`KALSHI_WS_BOOK_COLD=1` to `live.env` (back it up first, as the lane's convention),
and `sudo systemctl restart polymarket-maker-kalshi-ws.service`.

**Watch on the first cycles:** the `cold_cycle` log row now carries `book_ws`,
`book_rest`, `book_src_err`, and the plan row carries `book_mirror` / `book_rest` /
`book_src_err`. Expect `book_ws` ~38, `book_rest` ~2, **`book_src_err` 0**. Any non-zero
`book_src_err` means the provider is throwing and we are silently back on REST.
The cycle print line also shows `books=38ws/2rest`.

Flag off is the rollback; `git revert e237283` is the hard rollback.

---

## §5 — STILL TABLED, PRIORITY INTACT (carried forward verbatim; RULE NINE)

Nothing below was started, reordered, or descoped this session.

1. ~~Option B~~ — **DONE**, this handoff.
2. **Measure the venue write round trip** — a dry_run order write or an authenticated
   read; replaces the ~300ms inference with a number. Still the real ms ceiling. Note the
   READ number is now 320ms p50 (n=78), so the ~300ms inference for a write is, if
   anything, low.
3. **Un-park** — operator's go. Mechanically: clear STOP, raise `MAX_TOTAL_CAPITAL`.
4. **Per-family exposure caps — TABLED by operator.** M3 measured `gpu_restock` at
   $23,600/day = 21.1% of venue across 922 markets / 18 series, and the top 5 underlyings
   = 50.3% of venue pool (live read 2026-07-27T14:34:23Z, 1,692 programs, $111,888.33/day).
   Spreading across markets does NOT diversify. All figures are LOWER bounds (15.8% of pool
   unmapped, each counted as its own underlying).
5. **`REQ_SPACING_S=0.05` 429 watch** — thin evidence. Note: Stage C cuts a cold cycle from
   41 reads to 3, which materially *reduces* read pressure, but does not settle the
   question for the hot path.
6. **Stage B unproven live** — armed (`KALSHI_WS_HOT=1`), never fired on a real quote.
7. **M2b** — automated per-event reward export; today the split needs a manual UI/CSV pull.
8. **M4 / M5** and everything in the prior handoff's §7 — unchanged.

9. **NEW 2026-07-27, TABLED by operator: the bot flips positions THROUGH flat under the
   breaker.** OBSERVED live, not inferred: `KXDXYDUD-26JUL27-T101.4640` held **−20.0** at
   19:23:08Z with a resting `yes 0.55 x37`; at 19:27:08Z the position was **+17.0**
   (−20 + 37 = +17 exactly). Mechanism: `KALSHI_REDUCE_ONLY_KEEP_BOTH=1` keeps the ordinary
   two-sided maker quote live while the breaker is reduce-only, and that side is sized by the
   capital/join rule — NOT capped at `|inv|` the way `_unwind_size` is. So a full fill on the
   "reducing" side crosses through flat and opens the opposite position, which `_unwind_size`'s
   own docstring calls out as the thing it exists to prevent.
   Same setup was live simultaneously on `KXINXHUD-26JUL271600-T7410.20`: −29.05 with
   `yes 0.65 x41` resting.
   The breaker was tripped on EVERY cycle from 19:11Z onward, so this is the steady state, not
   an edge case.
   Candidate lever: `KALSHI_REDUCE_ONLY_KEEP_BOTH=0`. Cost is a PROPORTIONAL reward loss on the
   dropped side (R4 pays the mean of `(yes_share + no_share)/2`; prior handoff §7 citing
   `KALSHI_LIP_RULE_CANON.md` §R3/R4) — not a $0 cliff.
   **BOTH SIDES OF THIS TRADE ARE UNMEASURED:** what KEEP_BOTH=0 costs in actual reward, and
   what the flips cost in spread. Operator tabled it 2026-07-27 pending measurement.

Prior handoff's §7 (measured NEGATIVE, do not re-litigate) stands as written: Phase 3
velocity-conditional placement; the already-built things (`event_deltas`, `ladder_pairing`
+ the LADDER ESCAPE HATCH at `maker_kalshi_quoter.py:2166`, the strand fix `447c271`); and
the R3 two-sided correction (R3 tests the MARKET's book, not our own orders).

---

## §6 — COMMITS THIS SESSION

```
e237283  feat(kalshi): STAGE C (option B) — cold cycle serves book reads from the WS mirror
```
