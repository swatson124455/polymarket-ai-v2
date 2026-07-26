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

## §6 — WHAT IS ALREADY CORRECT (do not "fix" these)

- **Resting at the touch** — measured optimal, 6 of 6 configurations. See the measurement doc.
- **`THROTTLE_SMART=1`** — enabled this session 04:58:35Z; 51.0% of qualifying YES snapshots
  would otherwise score zero on the throttle step.
- **Letter-coded strikes counted naked** — failing safe, correct.
- **`SCORE_RANK` left ON** — not shown harmful; benefit unproven at the live N=40.
- **The capture model does not prefer empty books** — `_qualifying_score` applies the R4
  Target-Size clearing, so an empty book scores zero and sorts last.
