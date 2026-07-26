# KALSHI MAKER — THE REWARD-vs-FILL RATIO, MEASURED

**Session 2026-07-26. Branch `claude/maker-kalshi-live`. Bot PARKED throughout; not un-parked.**

The standing question was: *reward collected per hour of resting vs (expected loss per fill ×
fill rate)* — and specifically whether the bot should sit at the touch or one tick back. It had
never been measured. It is measured now.

---

## §0 — THE ANSWER

**Sit at the touch. `k=0` maximises net in 6 of 6 configurations** (3 markout horizons ×
2 queue models). The bot's current behaviour is correct — but it was correct by accident,
because the reward formula peaks there. Now it is correct on evidence.

| | k=0 | k=1 | k=2 | k=3 |
|---|---|---|---|---|
| net $, 5min markout, swept | **53.82** | 34.42 | 22.25 | 14.01 |
| net $, 15min markout, swept | **48.21** | 31.55 | 19.97 | 13.72 |
| net $, 30min markout, swept | **45.18** | 29.33 | 17.72 | 11.54 |
| net $, 30min markout, touched | **40.67** | 29.82 | 20.64 | 12.04 |

ESTABLISHED, frozen dataset (below). Net dollars over the matched window, resting 20ct on both
sides of all 408 quoted markets.

**The operator's mechanism is real but is outweighed.** Adverse selection per contract *does*
rise toward the touch — $0.0173/ct at k=0 vs $0.0064/ct at k=3 (30min markout, swept). But
stepping back one tick surrenders $20.55 of reward to avoid $4.70 of adverse selection.

**The decisive number — the break-even.** k=1 beats k=0 only if every filled contract loses an
**additional $0.0999 beyond its 30-minute markout**. Measured against that:

| quantity | value | denominator | source |
|---|---|---|---|
| break-even extra cost needed to justify k=1 | **$0.0999/ct** | frozen study | `robust.py` |
| ACTUAL all-in realized settlement cost | **$0.03064/ct** | 70 settlements, 4,522.1 ct | API 2026-07-26T04:48:23Z |
| — of which PAIRED (hedged) | **$0.02248/ct** | 4,198.1 ct (92.8%) | same |
| — of which NAKED (one side) | **$0.13645/ct** | 324.0 ct (7.2%) | same |

Hedged contracts sit **4.4× inside** the break-even. Stepping back a tick is not close to
justified for them.

---

## §1 — THE REAL LEVER IS PAIREDNESS, NOT TICK OFFSET

Naked contracts cost **6.1× what hedged contracts cost** ($0.13645 vs $0.02248 per contract) and
are the *only* category that breaches the break-even (1.37× over it). They are **7.2% of settled
contracts but 31.9% of settled loss**.

Robustness: 13 of 14 naked settlements lost money; leave-one-out (dropping the worst row,
`KXAAAGASD-26JUL24-4.100`, −$10.99) still gives **−$0.12396/ct over 268.0 ct**. Not one blow-up.

So the tick-offset knob is the wrong thing to tune. The question is not *where to rest* but
*what happens after a fill*.

---

## §2 — LIVE DEFECT FOUND: THE UNWIND CAP IS A SUNK-COST RATCHET  ⚠ NOT YET FIXED

`_unwind_price` (`maker_kalshi_quoter.py:875`) caps the reducing quote at
`cap = floor((1 − cost + MAX_UNWIND_LOSS) × 100)/100` and rests at `min(best, cap)`.
The cap is measured **against our cost basis**, so *the more underwater a position is, the
further its exit moves from the market*. We can close winners and never losers.

With live `KALSHI_MAX_UNWIND_LOSS=0.02` (unchanged across all 27 `live.env.bak-*` files; the
code's own default is 0.10), measured live at 2026-07-26T04:52:38Z:

**7 of 8 open positions have an exit priced BELOW the market — it cannot fill. $83.67 of cost
basis stranded, riding to settlement. Total unrealized −$42.83.**

Two independent measurements agree the cap should be ~0.10: the frozen study's break-even
($0.0999/ct) and the realized cost of *not* exiting ($0.13645/ct on naked contracts).
`KALSHI_MAX_UNWIND_LOSS=0.02` is roughly 5× too tight.

**DELIBERATELY NOT APPLIED THIS SESSION, and this is a judgement call the operator should
overrule if they disagree.** Changing it now would reprice exits on 8 legacy positions that are
mostly already economically resolved (marks at 0.00/0.99) and settle within ~1 day — exiting
would pay the spread *on top of* a loss that is already locked. Holding those to settlement is
strictly better. **This change belongs with un-parking, not before it.**

---

## §3 — LIVE DEFECT FIXED: `THROTTLE_SMART` WAS OFF IN PRODUCTION  ✅ APPLIED

`THROTTLE_SMART` reads `os.environ.get("KALSHI_THROTTLE_SMART") == "1"`, and that key was
**absent from `live.env`** — so a built, tested fix has never run.

When throttling, the quote steps back `THROTTLE_STEP_TICKS=1`. Under the R4 qualifying walk a
bid one tick back earns credit only if it is still inside the qualifying set. **In 1,817 of
3,560 qualifying YES snapshots (51.0%) the depth at the reference alone already meets Target
Size**, so the walk terminates at the reference and a quote one tick back scores **exactly
zero** — the throttle pays full reward for its risk reduction. `THROTTLE_SMART` detects that
case, holds the quote at reference, and takes the risk reduction from size instead.

The prior sandbox A/B measured this at **12% of n=612** snapshots on the weather/temp allowlist.
**51.0% of n=3,560** is the same statistic on the current allowlist-open universe. Different
denominators, both stated; identical mechanism.

Applied to `live.env` 2026-07-26T04:58:35Z, backup `live.env.bak-throttlesmart-20260726_045835`.
Inert while parked (the throttle branch needs an accumulating quote plus inventory over
`INV_SOFT_CT`). First post-change cycle 04:59:46Z: `cycle ok ... fails=0c/0cr/0q`.
Reverse with: remove the line, or set `KALSHI_THROTTLE_SMART=0`.

---

## §4 — WHAT I COULD **NOT** ESTABLISH (do not let the next session re-assert these)

- **A fill-risk term for selection.** Cannot be validated on this data: only **5 markets** had
  fills in the fitting half. Feature correlations (spread, depth, book_df vs realized
  loss/contract) were computed on n=5 and are meaningless. Ranking on `capture − measured loss`
  did **not** beat `capture` alone out-of-sample at N=20 or N=40. **I refused to ship an
  unvalidated model** — that is the same error as `SCORE_RANK`.
- **Footprint width.** The two half-splits disagree: fitting on the 1st half shows net peaking
  at N=20 and falling to N=100; fitting on the 2nd half shows net rising monotonically to N=408.
  The second split has a near-zero cost side, so it cannot discriminate. **`FOOTPRINT_TOP=40`
  should not be changed on this evidence.**
- **Whether `SCORE_RANK` earns its keep.** Capture-ranking beats pool-ranking decisively at
  N=10–20 in *both* splits (18.90 vs 1.50; 21.36 vs 7.55). At the live N=40 the splits disagree
  and margins are small (17.78 vs 15.21 one way; 24.79 vs 25.34 the other). Not shown harmful,
  benefit unproven at the width we actually run. **Left ON, unchanged.**

The binding constraint on all three is fill sparsity — 3.58 hours yields 5–21 markets with
fills. These are answerable with a few days of `quotes-*.jsonl`, which the bot writes anyway.

---

## §5 — CORRECTION TO A STANDING DESIGN CRITICISM

The criticism on record: *"Capture = our_size/(book+our_size) × pool is maximised when the book
is nearly EMPTY."* That is **too strong as stated.** The live ranker feeds on `capture_usd_day`
from `_market_telemetry_row`, which applies the full R4 walk **including the Target-Size
clearing** (`_qualifying_score` returns `0.0, False` when `cum < target`) and the R3 two-sided
test. A genuinely empty book therefore scores **zero and sorts last**, not infinity.

The real defect is narrower and still real: among markets that *do* qualify, the model prefers
the **thinnest qualifying book**, and nothing in the rank key sees fill risk. §4 explains why I
could not yet correct it responsibly.

Note also: `kalshi_market_scores.py`'s docstring cites pools spanning "$1,750 → $10,470/day".
Live telemetry over 4,159 rows shows `usd_day` min 100 / median 200 / **max 1,000** — consistent
with Kalshi's documented $10–$1,000 per-day-per-market band. The docstring's figures are stale
and should not be quoted; the code itself reads live `usd_day` and is unaffected.

---

## §6 — VERIFIED / REFUTED FROM THE PRIOR HANDOFF

| claim | verdict |
|---|---|
| 509 tests + 2 xfailed green | **VERIFIED** — `509 passed, 2 xfailed in 15.34s` |
| quoter md5 `31f9e19e…` | **VERIFIED** — worktree matches deployed byte-for-byte |
| cash $234.13 | **VERIFIED** — `balance_dollars 234.1257` @ 04:30:59Z |
| 8 open positions | **VERIFIED** (field is `position_fp`, not `position`) |
| equity $281.98 | **CLOSE** — $283.58 @ 04:30:59Z (cash $234.13 + portfolio $49.45); marks move |
| DF = 0.5 on all active programs | **VERIFIED** on this universe — 4,159/4,159 rows |
| `TAKER_FLATTEN=0`, `MAX_TOTAL_CAPITAL=1` (parked) | **VERIFIED** |
| letter-coded strikes counted naked | **VERIFIED, correct** — live warns `strike parse FAILED on 3 held ticker(s)`; failing safe. Left alone. |
| `KXTRUMPENDORSEMENTS-26JUL25-*` looked stale | **NOT STUCK** — `status=active`, closes 2026-07-26T14:00Z. `26JUL25` is event naming. |

---

## §7 — THE FROZEN DATASET (so every number above is reproducible)

| file | contents |
|---|---|
| `kalshi_live/study/quotes_frozen.jsonl` | md5 `7d7023857c07cdb1b14bd1aab3cc73c5` — 4,159 per-market-per-cycle telemetry rows, 104 cycles, 408 markets, 77 series, 2026-07-26T00:59:58Z..04:35:01Z. Our own orders resting in only 39/4,159 rows (0.94%), so the observed books are essentially uncontaminated by us. |
| `kalshi_live/study/tape_frozen.jsonl` | 4,763 public trades, same window, 0 outside the filter, 0 duplicate `trade_id`. 187 of 408 quoted markets traded at all; **221 (54.2%) had zero trades in 3.58h**. |

**Direction convention, ground-truth validated** against the two known maker fills of 2026-07-26
(sub-millisecond timestamp alignment; the `KXMUSKNW` case matches on price *and* an exact
`ct=10.00` print):

- `taker_side='no'` hits **YES** bids → our YES bid at q fills when `yes_price <= q`
- `taker_side='yes'` hits **NO** bids → our NO bid at p fills when `yes_price >= 1-p`

This was validated, not assumed — deriving it the other way would have inverted roughly half the
tape and silently reversed every adverse-selection number.

**Bounds and biases, stated:**
- Queue position inside a level is unobservable, so fill rate is reported as **SWEPT** (lower
  bound; a joiner sits at the back of the queue) and **TOUCHED** (upper bound). k=0 wins under both.
- Fills are capped at 20ct per (market, side, cycle) — we hold 20ct per side and are flat until
  the next 120s re-quote. An earlier pass that charged every touching trade as a fresh 20ct fill
  overstated the cost side ~6× and inverted the sign of the result. That error is corrected here.
- Reward share uses `rival_book_df` from the logged walk. Adding our own size can terminate the
  walk one level earlier, so the estimate is mildly **conservative** (understates our share).
- **Markout ≤30min cannot see settlement.** The §0 net figures are an **upper** bound on true
  net. §1 is the settlement-inclusive counterweight and is why the break-even comparison, not
  the raw net, carries the argument.

Reproduce: `study3.py` (§0), `robust.py` (break-even, concentration, leave-one-out),
`settle_pnl.py` (§1, live API), `unwind_check2.py` (§2, live API), `selection.py` + `footprint.py`
(§4), `reward_curve.py` + `anom.py` (§3's 51.0%).

---

## §8 — OPEN, IN PRIORITY ORDER

1. **`KXAAAGASW-26JUL27` credits ~07-28** — still the first multi-day reward receipt ever, and
   still the thing worth waiting for. We hold 4 strikes in it. Settles whether the $1 minimum is
   per-day or per-period.
2. **Apply `KALSHI_MAX_UNWIND_LOSS=0.10` when un-parking** (§2). Highest-value change identified
   this session; deliberately deferred, with reasons.
3. **Accumulate `quotes-*.jsonl` across several days**, then re-run §4. Fill sparsity is the only
   thing blocking the selection questions, and it dissolves with time, not cleverness.
4. Reward concentration is extreme — top 1 market 19.5%, top 5 markets 60.8% of $54.13 capture
   over the window. Any live result on a short window is a statement about a handful of markets;
   always disclose it.
