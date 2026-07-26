# KALSHI MAKER — WHY WE ARE NOT OPERATING THE WAY WE INTEND

**2026-07-26, live API reads timestamped inline. Bot PARKED. Companion to
`KALSHI_REWARD_VS_FILL_MEASUREMENT_2026-07-26.md`.**

That document answered *where to rest*. This one answers *why the thing behaves nothing like
the design*. The two findings connect: the measurement said naked contracts cost **6.1×** hedged
ones ($0.13645/ct vs $0.02248/ct). This explains why we keep ending up naked.

---

## §1 — THE HEADLINE: THERE IS NO WORKING EXIT PATH. ALL FOUR ARE CLOSED.

A position can be closed four ways. Every one is currently disabled or skipped:

| # | exit path | state | evidence |
|---|---|---|---|
| 1 | **Footprint unwind** (reducing quote on a market in this cycle's footprint) | never runs while parked | plan row 14:24:05Z: `footprint: 40`, `gated_out: 40`, `quoted_markets: 0` |
| 2 | **Strand unwind** (reducing quote on a held market NOT in the footprint) | **skips 6 of 6 positions** | predicate eval 14:27:46Z, below |
| 3 | **Pre-close flatten** (purpose-built: cross the naked residual before close) | **OFF** — `KALSHI_PRECLOSE_FLATTEN` absent from `live.env` → default `0` | `maker_kalshi_quoter.py:634` |
| 4 | **Taker flatten** (last-resort backstop) | **OFF** — `KALSHI_TAKER_FLATTEN=0` | 32 log lines: `flatten: 8 residual position(s) but TAKER_FLATTEN=0 — left resting, check manually` |

**Observable consequence, API 14:26:13Z: 6 open positions, −$42.66 unrealized, and
`N_RESTING_ORDERS = 0`.** Not one exit order anywhere. Every position rides to settlement by
construction.

### The deferral points at a disabled component

`maker_kalshi_quoter.py:1999-2001`, the strand path:

```python
if sby is None or sbn is None or sby + sbn >= 1.0:
    continue          # unpriceable/crossed — taker handles it
```

The taker does not handle it. `TAKER_FLATTEN=0`. Evaluated against live positions
(14:27:46Z) — **the predicate skips 6 of 6**:

```
KXAAAGASW-26JUL27-4.080   pos  -20.0  sby=0.99  sbn=None  -> SKIP: NO book EMPTY
KXAAAGASW-26JUL27-4.120   pos    9.0  sby=None  sbn=0.99  -> SKIP: YES book EMPTY
KXAAAGASW-26JUL27-4.140   pos   62.0  sby=None  sbn=0.99  -> SKIP: YES book EMPTY
KXAAAGASW-26JUL27-4.160   pos  -34.0  sby=None  sbn=0.99  -> SKIP: YES book EMPTY
KXTRUMPENDORSEMENTS-26JUL25-A20  20.0 sby=None  sbn=None  -> SKIP: both EMPTY
KXTRUMPENDORSEMENTS-26JUL25-A3  -17.0 sby=None  sbn=None  -> SKIP: both EMPTY
```

Each component is individually defensible. The **composition** has a hole, and nothing logs it
as an error — the cycle prints `cycle ok`.

### The ratchet, stated precisely

The two failures are sequenced, and that sequencing is the whole problem:

1. **While an exit IS possible** (book two-sided, market unresolved), `MAX_UNWIND_LOSS=0.02`
   prices the reducing quote *below* the market — verified at 04:52:38Z, **7 of 8 positions had
   an unfillable exit**, because the cap is measured against COST BASIS, so the further
   underwater, the further the exit sits from the market.
2. **Once the market resolves directionally**, the book goes one-sided (`sby`/`sbn` empty), the
   strand path skips, and there is nothing to sell into at any price.

So the bot **declines to exit while it can, and cannot exit once it wants to.**

**Confirmed by what actually happened over the following 9.5h.** Of the 8 positions at 04:52Z,
exactly the 2 on live, unresolved markets closed by fills (`KXMUSKNW`, `KXCLARITYVOTE`; cash
$234.1257 → $248.1757, 0 new settlements). The 6 that remain are all effectively resolved. The
cap lets us exit the positions that recover and strands the ones that do not — **selection
against ourselves.**

---

## §2 — PARKED IS NOT "SAFE IDLE". IT IS ALL RISK, NO INCOME.

Reward under R4 is scored on **qualifying resting BIDS**. With `N_RESTING_ORDERS = 0` we earn
**exactly $0** — while holding 6 positions at −$42.66 unrealized that cannot be exited.

Parking removed the income and left the risk. That is worth stating plainly because "parked"
reads as safe, and this state is not safe — it is simply un-hedged and un-paid.

---

## §3 — CONFIG DRIFT: 37 OF 67 KNOBS (55%) SILENTLY TAKE CODE DEFAULTS

`THROTTLE_SMART` being OFF was not an isolated miss. The quoter reads **67** `KALSHI_*` knobs;
`live.env` sets **34**. Any key that is absent silently takes the code default and **nothing logs
it**. Full enumeration: `kalshi_live/study/env_audit.py`.

The ones that matter, all absent → default:

| knob | default | consequence |
|---|---|---|
| `KALSHI_PRECLOSE_FLATTEN` | `0` OFF | **the purpose-built defence against riding naked into settlement never runs** — built 2026-07-24, `test_preclose_flatten.py`, its own build doc |
| `KALSHI_CAPTURE_GATE` | `0` OFF | the market-quality gate (R4 walk + R3, the "is this market worth quoting" brain) never runs |
| `KALSHI_STANDDOWN` | `0` OFF | thin-reward stand-down never runs |
| `KALSHI_NETEV_GATE` | `0` OFF | net-EV gate never runs |
| `KALSHI_PIVOT_SELECT` | `0` OFF | density/near-money selection off; legacy egalitarian round-robin is what actually runs |
| `KALSHI_MIN_QUOTE_CT` | `2` | floor on quoted size |
| `KALSHI_READ_BUDGET` | `200` | caps coverage at ~200 of 1,774 programs seen per cycle |

`PRECLOSE_FLATTEN` is the sharpest one. Its own docstring diagnoses §1 exactly:

> *"This is the missing ACTIVE flatten — WIND_DOWN only STOPS quoting and the late-life gate only
> blocks ENTRY; **nothing today crosses the naked residual before close, it just rides.**"*

It was built, tested, documented — and left at default 0. That is the same failure mode as
`THROTTLE_SMART`: **the fix exists and was never switched on.**

---

## §4 — COVERAGE: WE SEE 1,774 PROGRAMS AND QUOTE 40

Plan row 14:24:05Z: `programs_seen: 1774`, `scored_markets: 600`, `footprint: 40`,
`drop_far_close: 28`, `presence_skipped_markets: 4`, `empty_books: 2`, `two_sided_markets: 14`.

`READ_BUDGET` (200, defaulted) and `FOOTPRINT_TOP` (40) mean we evaluate a small slice of the
venue each cycle. Whether 40 is the right width is **NOT established** — see the measurement
doc §4; the two half-splits disagree and I refused to recommend a change on that evidence.

Also live: `strike_parse_failed: 2` — letter-coded strikes (`A3`/`A20`) cannot be ladder-paired
and are counted fully naked. **That is correct and deliberate** (guessing an ordinal would
fabricate a hedge and understate real exposure). Leave it.

---

## §5 — THE FIX ORDER (my recommendation; operator decides)

Nothing below should be applied while parked except where noted — several realize losses.

1. **`KALSHI_PRECLOSE_FLATTEN=1`** — the single highest-value change. It is the built defence
   against the dominant measured loss (naked $0.13645/ct = 31.9% of settled loss from 7.2% of
   contracts). It is naked-only, near-close-only, capped at `|naked|`, maker-first with a grace
   period, and additive (cancels nothing). Note it taker-crosses, so it needs `TAKER_FLATTEN`
   semantics available — verify that interaction before enabling.
2. **`KALSHI_MAX_UNWIND_LOSS` 0.02 → 0.10** — removes the sunk-cost ratchet in §1. Two
   independent measurements converge on ~0.10 (break-even $0.0999/ct; realized naked cost
   $0.13645/ct); 0.10 is also the code's own default. **Apply at un-park, not before.**
3. **Close the strand-path hole** — `continue`-on-empty-book defers to a component that is off.
   Either enable a backstop or log it loudly. Today it is silent: the cycle prints `cycle ok`
   while 6 positions have no exit. **Code change; needs its own commit and blast-radius pass.**
4. **Make silent defaults visible** — log every `KALSHI_*` knob absent from `live.env` at
   startup. This class of defect has now bitten twice (`THROTTLE_SMART`, `PRECLOSE_FLATTEN`)
   and is invisible by construction.

---

## §5b — WHAT WAS ACTUALLY DONE (2026-07-26, all verified)

**Code — deployed to `/opt/pa2-maker-kalshi-live/maker_kalshi_quoter.py`, md5
`ac5b86d145c0010d64891f6e56ba77ff`, backups `*.bak-strandfix-20260726_*`:**

| commit | fix |
|---|---|
| `447c271` | strand unwind no longer requires BOTH book sides; 3 plan counters so a position with no exit is counted, not silent |
| `7ca30e3` | every `KALSHI_*` knob registers itself inside its accessor; absent set → plan row, unset protection knobs NAMED in the log |
| `a903d30` | the daily halt names WHICH measure breached (it printed `cumulative-down $8.98 > $10`, which is not even true — the trigger was drawdown $13.74 > $10) |

Suite 509 → **532 passed + 2 xfailed**. Every new test was verified adversarially against
the old implementation (`git stash`) and fails there.

**Config — `live.env`, backups `live.env.bak-throttlesmart-*` / `live.env.bak-allfixes-*`:**

| knob | was | now |
|---|---|---|
| `KALSHI_THROTTLE_SMART` | absent (OFF) | `1` |
| `KALSHI_PRECLOSE_FLATTEN` | absent (OFF) | `1` |
| `KALSHI_MAX_UNWIND_LOSS` | `0.02` | `0.10` |
| `KALSHI_CAPTURE_GATE` / `STANDDOWN` / `NETEV_GATE` | absent | `0` explicitly — unchanged in VALUE, written so the audit records a CHOICE |

Unset protection knobs: **10 → 0**. Absent knobs overall: 37 → 33.
`MAX_TOTAL_CAPITAL=1` and `TAKER_FLATTEN=0` **unchanged — still parked.**

`PRECLOSE_FLATTEN` is safe to enable independently: `_preclose_naked_flatten` calls
`_taker_cross_capped` directly and does **not** read `TAKER_FLATTEN`, so it does not
re-open the blanket launch-day crossing. It is naked-only, near-close-only, hard-capped at
`|naked|` and decremented by confirmed fills.

**VERIFICATION LIMIT, stated plainly:** a STOP sentinel has been present since
2026-07-26T14:54:08Z, and `run_once` returns before reaching the strand block and the env
audit. So the strand fix and the config-visibility log are **deployed and unit-tested but
not yet exercised live.** They will run on the first cycle after the STOP clears.

## §5c — A FIFTH CLOSED EXIT PATH, FOUND WHILE FIXING THE OTHERS  ⚠ NOT FIXED

`MAX_PRICE_DOLLARS=0.96` / `MIN_PRICE_DOLLARS=0.04` is an **entry** band, and it is applied
to **reducing** orders too. Live 15:31:46Z, with `MAX_UNWIND_LOSS` already raised to 0.10:

```
flatten: KXAAAGASW-26JUL27-4.080 pos=-20.00 — reducing side unpriceable, will re-check at escalation
```

The exit for that long-NO position is a YES bid at the 0.99 touch. `_unwind_price` returns
0.99 (the cap, 1.05, does not bind), then `MIN_PRICE_DOLLARS < 0.99 <= MAX_PRICE_DOLLARS`
is **False** — so the exit is refused as "unpriceable".

This is systematic, not incidental: **a position that has moved deep against us necessarily
has an exit price near 1.00**, so the entry band blocks precisely the exits that matter
most. It is the same family as the standing rule that the YES/NO mandate governs ENTRIES
only — a guard written for entering being applied to leaving.

**Why it is not fixed here:** ~15 call sites gate a reducing order on this band
(`maker_kalshi_quoter.py` lines 1108, 1113, 1188, 1194, 1227, 1232, 1257, 1262, 1289, 1295,
2081, 2084, 2120, 2628, 2630). Rewriting all of them in one pass at the end of a long
session, in a money path, is the shotgun fix this repo forbids — and getting it wrong in the
`desired_quotes` sites, where entry and unwind branches sit side by side, would let ENTRIES
rest at 0.99. That is a far worse failure than the one being fixed.

**Design for the next session:** give reducing orders their own bounds — the VENUE limits
(0.01–0.99) rather than the strategy band — leaving `MAX_UNWIND_LOSS` as the sole economic
governor of whether an exit is worth taking. Do the `_flatten_all` and strand sites first
(unambiguously exit-only), then `desired_quotes` with a test that pins entries still
respecting 0.04/0.96.

**Current cost of leaving it:** exactly one position, `4.080`, 20 ct — exiting realizes
−$0.80 versus −$1.00 at settlement. $0.20. It is a structural defect with a trivial
immediate price tag, which is why it can wait for a careful fix.

## §5d — THE BOT IS HALTED, AND THE LIMIT MAY BE TOO TIGHT

STOP written 2026-07-26T14:54:08Z: `drop=$13.74 (equity $316.02 vs day-peak $329.76)`.
Legitimate — true drawdown $13.74 against `DAILY_LOSS_HALT_USD=10`. **Not** the ratcheting
counter, and **not** caused by this session's deploy (which came 26 minutes later).

Two observations for the operator, both decisions rather than defects:

1. **$10 on a ~$316 book is 3.2%.** Equity here is cash + held COST basis, so it moves when
   positions settle or fill — meaning the limit trips on ordinary de-risking. It halted
   twice today.
2. **Clearing the STOP would not resume quoting.** With `MAX_TOTAL_CAPITAL=1` the footprint
   gates out entirely (`gated_out: 40`, `quoted_markets: 0`). Clearing it would only let the
   *exit* paths run — which is the direction we want. **I did not clear it: that is an
   un-park-adjacent action on real money and it is the operator's call.**

## §6 — WHAT IS ALREADY CORRECT (do not "fix" these)

- **Resting at the touch** — measured optimal, 6 of 6 configurations. See the measurement doc.
- **`THROTTLE_SMART=1`** — enabled this session 04:58:35Z; 51.0% of qualifying YES snapshots
  would otherwise score zero on the throttle step.
- **Letter-coded strikes counted naked** — failing safe, correct.
- **`SCORE_RANK` left ON** — not shown harmful; benefit unproven at the live N=40.
- **The capture model does not prefer empty books** — `_qualifying_score` applies the R4
  Target-Size clearing, so an empty book scores zero and sorts last.
