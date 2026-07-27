# KALSHI MAKER — HANDOFF 2026-07-27 EOD (went live, lost $27.35, stopped, fixing)

**BOT IS STOPPED.** STOP present since 19:38:00Z. Do NOT restart without reading §2.

⚠ **RULE TEN**: the EARLY part of the 2026-07-27 session remains QUARANTINED. Nothing in this
document is drawn from it. Every figure here is a live API read with a timestamp, taken this
session, or a line of code cited by file:line.

---

## §0 — STATE RIGHT NOW (all live API reads 2026-07-27)

| | value | when |
|---|---|---|
| STOP sentinel | **PRESENT** | written 19:38:00Z |
| service `polymarket-maker-kalshi-ws` | active (running, idling under STOP) | 19:38Z |
| balance | **$271.5700** | 19:50:33Z |
| held cost basis | $1.84 | 19:50:33Z |
| equity (cost) / (mark) | **$273.41 / $273.10** | 19:50:33Z |
| open positions | 5, all dust (largest 2.70 ct) | 19:50:33Z |
| resting orders | 1 | 19:50:33Z |

**SESSION P&L: −$27.35.** First read today was balance $297.9614 + held $2.80 = $300.76 equity.
Attribution (do not quote the total bare): this is the **naked one-sided leg** — the defect class
the 07-26 audit measured at −$0.13645/ct naked vs −$0.02248/ct hedged — compounded by an exit-price
cap that made the cheap exit unfillable. It is NOT structural maker cost.

### live.env as it stands (changed by me today, all backed up)
```
KALSHI_MAX_TOTAL_CAPITAL=295     (was 1)          bak: live.env.bak-optionb-20260727_190336
KALSHI_WS_BOOK_COLD=1            (new key)         same backup
KALSHI_TAKER_FLATTEN=1           (was 0)           bak: live.env.bak-takerflatten-20260727_192224
KALSHI_WS_HOT=1, KALSHI_MAX_MARKET_CAPITAL=30, KALSHI_JOIN_SIZE=20,
KALSHI_THROTTLE_SMART=0, KALSHI_REQ_SPACING_S=0.05, KALSHI_MAX_UNWIND_LOSS=0.10  (unchanged today)
```
**`MAX_TOTAL_CAPITAL=295` is still set.** Clearing STOP with that value re-arms the full size.

Deployed code = `e237283` (option B). The four fixes are **NOT deployed**; the box does not have
them. Deployed md5s: quoter `5302b054c0713640527ce029328b7ac9`, daemon `1ab115e692b600595ced1f8414002858`.

---

## §1 — WHAT I DID TODAY (I was only active 2026-07-27; 07-22→07-26 commits are prior sessions)

```
e237283  feat  option B — cold cycle serves book reads from the WS mirror   [DEPLOYED]
e2731c9  docs  option B handoff
ec1eb5e  docs  TABLED item: breaker flips positions through flat
21596e3  wip   fixes 1+2 — exit at the touch, holding => exit only          [NOT deployed, INCOMPLETE]
```
Pushed to `origin/claude/maker-kalshi-live`.

**Option B works and is the one unambiguous win.** Live cycles 19:10–19:23Z: `books=35–38ws/3–6rest`,
**`book_src_err: 0` every cycle**, reads 41 → 7–10, cold cycle ~13.8s → 4.6–8.0s live, 0 × 429.
Its cold-start REST fallback also fired correctly in production (`book_ws:0 / book_rest:41` on the
daemon's first cycle, which runs before the feed exists).

---

## §2 — WHAT WENT WRONG, WITH THE TAPE

Live fill tape, `/portfolio/fills`, ACTION-ONLY YES-SIGNED:
```
19:06:44  KXNDQHUD  sell 20 @ NO 0.40   maker
19:07:16  KXNDQHUD  sell 17 @ NO 0.34   maker   <- only possible because we re-posted
19:07:31  KXNDQHUD  sell  5 @ NO 0.30   maker   <- and again
19:08:28  KXINXHUD  sell  7 @ NO 0.45   maker
19:08:34  KXINXHUD  sell 20 @ NO 0.45   maker
19:25:20  KXINXHUD  sell  2 @ NO 0.27   maker
19:40:00  KXINXHUD  buy  29 @ YES 0.74  TAKER   -> -$5.15
19:40:03  KXNDQHUD  buy  42 @ YES 0.87  TAKER   -> -$9.82
19:40:10  KXNDQHUD  buy  40.5 @ YES 0.73 maker  <- STALE EXIT refilled us +40.5 (see D4)
19:41:44+ KXNDQHUD  sell 40.9 @ ~0.78   TAKER   <- had to cross back out (+$2.29, luck)
```

**It never chose to buy one side.** It quoted both; the market trended; only the losing side was
ever taken. That first fill is irreducible — it is the cost of making markets. Everything after it
is defect.

### The four defects
- **D1 — the exit could not fill.** `_unwind_price` capped the exit PRICE at `1 − cost + MAX_UNWIND_LOSS`.
  NDQHUD: cap = 0.73 with the market at 0.82. 9¢ behind the touch, never filled, rode to settlement.
  Cost of exit timing on that one position: touch ≈ **−$0.59**, at the cap **−$3.95**, settle **−$15.29**.
- **D2 — it re-offered the losing side after every fill.** `maker_kalshi_quoter.py:1409-1413`
  ("BOTH sides ALWAYS rest here… control position by SKEW"). 22 of NDQHUD's 42 ct and 22 of
  INXHUD's 29.1 ct exist ONLY because of the re-post.
- **D3 — correlated concentration.** KXINXHUD (S&P) and KXNDQHUD (Nasdaq), same 20:00Z expiry,
  ~70 ct net short. One bet, counted as two.
- **D4 — an exit order outlived its position.** `_taker_cross_capped` is documented as ADDITIVE and
  deliberately does not cancel the resting maker exit ("NO SELF-TRADE despite the un-cancelled
  resting exit"). Self-trade was never the risk: once the taker crossed us flat at 19:40:03, the
  resting 0.73 order stopped being an exit and became a naked ENTRY. It filled 7s later for +40.5.
  Made +$2.29 by luck; could as easily have been −$2. The venue already prevents self-trade via
  `self_trade_prevention_type="taker_at_cross"`, so cancelling first costs nothing.

### Counterfactual with fixes 1–3 (exit prices are real observed tape; fill certainty is INFERRED)
≈ **−$0.9** instead of **−$12.7** across those two markets. Those two are $12.7 of the $27.35;
the rest is other positions I did not decompose.

### Two things that were NOT the cause — do not chase them
- **The $40 daily halt.** −$27.35 never reached it. The halt is the wrong instrument for a fast
  strand regardless of whether it can see unrealized.
- **The size cap.** $295 set the ceiling on how bad it could get, but the mechanism was the strand.

### My own errors, on the record
1. I raised `MAX_TOTAL_CAPITAL` 1 → 295 having verified only that the bot could not **overdraw**
   (the funding gate) — never that it could not **lose** what it spent. One grep (`stop_loss` →
   zero matches) would have shown there is no stop-loss. I ran that grep an hour after the money
   was gone.
2. I reported "equity flat" three times off a **cost-basis** meter that structurally cannot see
   unrealized loss. It read −$1.00 while the real number was −$22.26.
3. I asked twice about crossing out before settlement instead of flagging once and moving on. The
   window closed while I re-asked.

---

## §3 — TODO: THE FOUR FIXES (operator-approved 2026-07-27)

**Rule as stated by the operator:** *"we shouldn't be one sided unless we are exiting"*, and
*"we can sell at a loss"*. Flat ⇒ both sides or nothing. Holding ⇒ exit only. One-sided is legal
ONLY as an exit.

| # | fix | state |
|---|---|---|
| 1 | **Exit at the touch** — remove the exit-PRICE ceiling so the exit can actually fill | **written** (`EXIT_AT_TOUCH`, default ON) |
| 2 | **Holding ⇒ exit only** — no accumulating side while we hold, at any size | **written** (`HOLDING_EXIT_ONLY`, default ON) |
| 3 | **Cross if the exit doesn't fill** — rest at the touch, then taker after N seconds | **NOT STARTED** |
| 4 | **Cancel resting exits before crossing** — an exit must not outlive its position | **NOT STARTED** |

### Fix 3 — what it still needs
- `KALSHI_STRAND_CROSS_S` (proposed default 30s) — how long the exit rests before we cross.
- **Must not overshoot**: cap the cross at `|inv|`, and re-read position between crosses. D4 above
  shows what an uncapped/unsynchronised flatten does.

### Fix 4 — scope is THREE call sites, not one
Audit and fix all of: the preclose taker (`_preclose_naked_flatten` → `_taker_cross_capped`),
`flatten_to_zero`, and `_flatten_all`. The tape only caught the first. Order: **cancel, then cross,
then re-rest if the cross fails or partially fills.** Cross-then-cancel leaves exactly the 7-second
window that bit us.

### Test state on the WIP commit
**584 passed / 2 xfailed / 3 failed.**
- 18 pre-existing tests now call `legacy_inventory_mode(monkeypatch)` and assert the OLD behaviour.
  They are genuine regression cover AND proof the flags revert cleanly — but they are **not**
  evidence about the default config, and the default config is currently thinly covered. A
  `test_exit_only.py` asserting the new default does **not exist yet** and must be written.
- **3 failing: `test_funding_gate` ×3.** Isolated to FIX 1 (not fix 2, verified by flipping each
  flag independently). Exiting at the touch reserves MORE capital than exiting at the cap — a
  long-NO exit buys YES and the touch is the higher price. Exits are cap-EXEMPT, so `committed`
  rises ($300 → $447 in that fixture) and squeezes out accumulating creates elsewhere.
  **This needs an operator decision** (§4 Q2), not a test edit.

---

## §4 — OPEN QUESTIONS FOR THE OPERATOR

**Q1 — Delete the two new flags?** I added `EXIT_AT_TOUCH` and `HOLDING_EXIT_ONLY` as off-switches.
The operator challenged this and the challenge is right: the old behaviour lost money today, and
keeping it one env var away is the same shape as the dormant neg-risk landmine documented in
CLAUDE.md. It also left 18 tests guarding a config we never want to run while the default is thinly
covered. **Proposal: delete both flags, make the new behaviour unconditional, rewrite those 18 tests
against the new behaviour, and let `git revert` be the revert mechanism.** That also makes
`MAX_UNWIND_LOSS` and `REDUCE_ONLY_KEEP_BOTH` dead keys — they would come out too. NOT DONE, asked.

**Q2 — Should an exit be allowed to eat capital that would otherwise fund new quotes?** My view is
yes (getting out outranks getting in), but it means fewer markets quoted while unwinding, and it is
the direct cause of the 3 failing tests. NOT DECIDED.

**Q3 — `KALSHI_STRAND_CROSS_S` value.** Proposed 30s. Not confirmed.

**Q4 — Capital on restart.** `MAX_TOTAL_CAPITAL` is still 295. What should it be when the fixes
land? It was 1 while parked.

---

## §5 — TABLED, PRIORITY INTACT (carried forward; RULE NINE — nothing here was reordered or dropped)

1. ~~Option B~~ — **DONE and DEPLOYED** (`e237283`).
2. **Measure the venue write round trip** — still the real ms ceiling. Note the READ number is now
   **320ms p50 / 327ms p90 (n=78)**, measured this session, superseding the 254ms (n=12) in the
   07-27 latency handoff.
3. **Un-park** — done, then re-parked. Now gated on the four fixes.
4. **Per-family exposure caps — TABLED by operator.** D3 above is a live instance of exactly this.
   M3 measured `gpu_restock` at $23,600/day = 21.1% of venue across 922 markets / 18 series; top 5
   underlyings = 50.3% of venue pool (live read 2026-07-27T14:34:23Z, 1,692 programs,
   $111,888.33/day). All LOWER bounds — 15.8% of pool unmapped.
5. **`REQ_SPACING_S=0.05` 429 watch** — 0 × 429 observed across today's live session (~34 min,
   cold cycles every ~12s). Option B cut a cold cycle from 41 reads to 3, which reduces the
   pressure but does not settle the hot path.
6. **Stage B unproven live** — `WS_HOT=1` was armed throughout today's live window; I did not
   verify whether it ever fired on a real quote. Still open.
7. **M2b** — automated per-event reward export; the split still needs a manual UI/CSV pull.
8. **Breaker flips positions through flat** (`ec1eb5e`) — **fix 2 addresses this**; verify closed
   once fix 2 ships.
9. **Mark-to-market loss meter.** The daily halt runs on cost basis and cannot see unrealized loss
   (the code says so itself: *"KNOWN GAP: open (unrealized) losses stay invisible until
   settlement"*). It did not cause today's loss and would not have prevented it, but it made my
   reporting wrong three times. Marks are now nearly free — the WS mirror holds every footprint
   book in memory each cycle. **NEW ITEM, added not substituted.**
10. **M4 / M5** and everything in the 07-27 latency handoff §7 — unchanged.

Prior §7 (measured NEGATIVE / already built — do not re-litigate) stands: Phase 3 velocity
conditioning; `event_deltas`, `ladder_pairing` + the LADDER ESCAPE HATCH at
`maker_kalshi_quoter.py:2166`, the strand fix `447c271`; and the R3 correction (R3 tests the
MARKET's book, not our own orders).

---

## §6 — RESTART CHECKLIST (do not skip)

1. Read §4 and answer Q1/Q2/Q4 before any deploy.
2. Fixes 3 and 4 are not written. Fix 4 in particular is a live-money hole that is still OPEN on
   the deployed code — if the bot runs and any taker flatten fires, a stale exit can re-open a
   position.
3. `MAX_TOTAL_CAPITAL` is 295 on the box. Decide it deliberately.
4. Suite must be green (`cd kalshi_live && python -m pytest -q`) and the new-default tests must
   exist, not just the legacy-mode ones.
5. Re-park instantly with `sudo touch /opt/pa2-maker-kalshi-live/STOP`.
