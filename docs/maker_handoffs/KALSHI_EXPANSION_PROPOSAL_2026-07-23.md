# KALSHI EXPANSION PROPOSAL — 2026-07-23

**Status: DECISION DOCUMENT. Nothing deploys without operator say-so. Zero orders were placed, zero
config was changed, zero services were restarted in producing it.** All measurement was read-only:
public Kalshi API (no keys), offline replay of frozen datasets, and reads of
`kalshi_live/maker_kalshi_quoter.py`. Every new script is a NEW file; the five files under
concurrent edit by another workflow (`kalshi_attribution_ledger.py`, `kalshi_concentration_study.py`,
`kalshi_series_scan.py`, `maker_kalshi_quoter.py`, `test_live_hardening.py`) were not touched.

**Operator directive being answered:** *"we need to expand, cautiously but expand."*

**The answer this document reaches:** expand **DEPTH**, on the series we already have, by one
env value — and only *after* two live-risk items that were surfaced during the study are closed.
Expand **BREADTH: not yet, and not from the slate that was produced today.** The breadth slate did
not survive its own refutation. That is reported as a finding, not papered over.

---

## 0. HEADLINE — WHAT CHANGED IN THE PREMISES

Three of the framing premises this study started from were **measured false**. They are stated up
front because every downstream decision inherits them.

| Premise going in | Verdict | Where |
|---|---|---|
| "We are dark most of the day — there is nothing to quote" | **FALSE.** Union duty cycle 99.2% live / 90.1% quotable. Zero dark hours on either definition. | §1 |
| "Horizon ratio ≈ 1 is the first-class admission filter" | **DEMOTED.** Right direction, wrong unit and wrong object: use *tail hours* (binary, tail>0 breaks the exit ladder), and the ratio's denominator is a **period** boundary, not a **reward** boundary. | §4, §5 |
| "Capital binds at K≈7, so any new series must displace gas" | **UNRESOLVED — two measurements disagree by 2.2×.** Must be settled offline before any admission. | §2.3 |

And one item that is **not** an expansion question at all but was found while doing the work:

> ⚠ **`KALSHI_TAKER_FLATTEN=1` (flipped live ~15:19Z today) has two measured failure modes on the
> CURRENT book, independent of any expansion.** See §6 item 0. I would close those before touching
> anything else.

---

## 1. THE COVERAGE PROBLEM IN ONE NUMBER

### 1.1 Hours per day we can earn at all: **23.8 h live / 21.6 h quotable. There are no dark hours.**

Union across the 7 allowlisted series (`KXTEMPDCH/AUSH/LAXH/NYCH/CHIH`, `KXAAAGASD`, `KXAAAGASW`),
7-day window 2026-07-16T15:00Z → 07-23T15:00Z:

- **union duty cycle (≥1 live program): 99.2%** → 23.8 h/day
- **union duty cycle passing the footprint gates: 90.1%** → 21.6 h/day
- **UTC hours in which the union is zero: NONE — not one hour, on either definition.**

**Sample:** n = 4,910 programs across the 7 series, drawn from a fully paginated 118,731-row
program universe (`limit=10000` × 12 pages to exhaustion).
**What this does not cover:** program *presence*, not earnability — a live program on a one-sided
book pays nobody under canon R3. It says nothing about fill rate, queue position, or adverse
selection. It is a 7-day window; program schedules can change.

### 1.2 Two method defects found first — both affect existing scripts

- **D1 — `status=scheduled` is silently ignored by the API.** `status=zzzgarbage`, `status=''`,
  `status=ACTIVE`, `status=settled` and *no status param* all return byte-identical pages; full
  pagination of `scheduled` vs the garbage control yields **118,731 programs each, overlap
  118,731/118,731**. Only `active` (2,329) and `closed` (1,392) are honoured. `scheduled` is the
  *unfiltered universe*, not a historical superset.
- **D2 — truncation worse than the known 1000-vs-10000 defect.**
  `kalshi_twosided_profile.py:324` paginates at `limit=1000` inside `for _ in range(30)` → a hard
  ceiling of **30,000 of 118,731 rows (25.3%)**. Its published KXAAAGASD duty-cycle row happens to
  be right, but it was computed on a quarter of the data and does not generalise.

**Method validation (not asserted without a cross-check):** the interval math reproduces
`status=active` exactly at 15:27Z — `{GASD: 7, GASW: 15, all five KXTEMP*: 0}` from both paths.

### 1.3 Where the coverage story actually goes wrong

- **The five KXTEMP\* series are ONE calendar, not five.** Identical program counts (960 each),
  identical merged windows (96 each), identical duty (55.2% live / 12.3% quotable), identical max
  gap (27.0 h), to the minute. As *coverage* diversification they are worth exactly **1 series**.
  As *risk* they remain 5 uncorrelated cities — that part is real.
- **The late-life gate destroys 78% of the temp calendar.** Temp programs run ~58 min; the entry
  cutoff is `min(MAX_ENTRY_CUTOFF_MIN, max(WIND_DOWN_MIN, LATE_LIFE_FRAC × life))`
  (`maker_kalshi_quoter.py:309`) = 45 min at shipped defaults → a 58-min program is admissible for
  ~13 min. Measured 12.3/55.2 = 22.3%, matching the 45-min arithmetic exactly, which also confirms
  **the shipped default was in force across the sampled week**.
- **Coverage varies by CALENDAR DAY, not by hour of day.** Per-day union was 100% on every sampled
  day; the temp calendar swung 0% → 92% across days. The hour-of-day lens is what produced the
  false "dark most of the day" reading.
- The KXAAAGASD 04:00–15:00Z blackout **is real and reproduces** (windows all end 03:59Z, restart
  14:44–15:31Z, duty 46.1% live / 32.3% quotable) — but **KXAAAGASW covers every one of those
  hours at 86–100%.**

### 1.4 The number that actually matters: **we deploy ~21% of the book**

Coverage is not the constraint. Deployment is. From 431 plan records, 07-23 00:01→15:23Z:

| segment | window | n | footprint | quoted | committed | of which HELD inventory | **RESTING (earning)** |
|---|---|---|---|---|---|---|---|
| A | 00:01–04:38 (KB=1) | 139 | 14.32 | 3.50 | $58.66 | $23.42 | **$34.95** |
| B | 04:38–15:11 (KB=0) | 285 | 10.00 | 1.60 | $37.96 | $28.14 | **$10.30** |
| C | 15:11–15:19 (KB=1) | 4 | 13.50 | 1.00 | $42.27 | $38.24 | **$4.03** |
| D | 15:19– (KB=1, TF=1) | 3 | 17.00 | 1.00 | $42.27 | $38.24 | **$4.03** |

- **Footprint was > 0 in 431/431 cycles.** Darkness reduces the footprint; it never zeroes it.
- The "$42.62 of $85" headline **overstates deployment**: most of "committed" is held inventory,
  which earns nothing. In segment B only **$10.30 of $85 (12%)** was actually resting in a book.
- Cycle-weighted mean resting capital across all 431 cycles: **$18.15 of $85 = 21.4%**, i.e. **3.3
  full-book-equivalent hours in the 15.4 h sampled.** (Naïvely extrapolated flat to 24 h that is
  ~5.1 h/day — **flagged as an extrapolation, not a measurement**; the un-sampled 15:23–24:00Z
  block is not covered.)
- Nothing downstream is dropping anything, today: `capped_markets` 0 in **431/431** ·
  `budget_dropped_markets` 0 in 431/431 · `unqualifiable` 0 · `empty_books` 0 · `quote_fail` 0 ·
  `fetch_failed` 0 · `create_fail` mean 0.072 (31 cycles) · `cancel_fail` 9 cycles.
- ⚠ **Not always true:** on 07-22 (footprint mean 21.04, `MAX_TOTAL_CAPITAL=90`) `capped_markets`
  was nonzero in **271/595 cycles (46%)**, mean 2.05. The total-capital cap binds on
  broad-footprint days — it just did not bind today.

**What §1.4 does not cover:** one day, one account, one config trajectory (two flips mid-day);
"resting" is order value at plan time, not filled or earned money.

---

## 2. DEPTH FIRST — YES, WE LEAVE CAPACITY UNUSED. THE KNOB IS `KALSHI_MIN_DEPTH_SYM`.

### 2.1 Gate attribution — where the footprint goes

Live replay, 9 snapshots 15:26–15:37Z, footprint 17 every time (GASD 7 + GASW 10):

| first gate that drops it | per cycle | R3-earnable by anyone? |
|---|---|---|
| `one_side_no_bids` (yes 0.98–0.99, no side empty) | 7.50 | **0/45 — unearnable by anyone** |
| `price_bounds` (0.97 / 0.02) | 1.50 | 9/9 earnable |
| **`sel_sym` (MIN_DEPTH_SYM)** | **4.83** | **29/29 earnable** |
| `pass_join` | 3.17 | 19/19 earnable |

Frozen dataset (`concentration_samples.jsonl`, 27 snapshots 02:25–02:48Z, 353 market-snapshots):
`price_bounds` 179 (73% R3-earnable) · **`sel_sym` 117 (33% of all, 117/117 R3-earnable)** ·
`sel_spread` **2** · `pass_join` 55.

> **`MAX_SPREAD_TICKS=8` is not binding — 2/353 frozen, 0/71 live. Leave it alone.**
> All the recoverable loss is `MIN_DEPTH_SYM`.

**Method validation:** an independent re-implementation of the quoter's flat-book path
(`maker_kalshi_quoter.py:445–530`) run over 22 allowlist contracts returns
`one_side_no_bids 10 / price_bounds 2 / sel_sym 6 / pass_join 4`; the live replay scaled to 22
contracts gives `9.7 / 1.9 / 6.2 / 4.1`. Near-exact agreement from a different code path.

### 2.2 What relaxing it buys — MODELLED, upper bound

Same cached books, gate relaxed, then the real `cap_desired` at $85 (live, 15:36–15:37Z):

| MIN_DEPTH_SYM | admitted | capital | after $85 cap |
|---|---|---|---|
| **0.25 (live)** | **3** | **$31.9** | 3 |
| 0.20 | 6 | $69.7 | 6 |
| 0.15 | 6 | $69.6 | 6 |
| 0.10 | 6–7 | $69.6–77.3 | 6–7 |
| off | 7–8 | $77–91 | 7, 1 capped out |

Frozen books, admitted/cycle: 0.25 → 2.04 · 0.15 → 3.74 · 0.10 → 4.89 · off → 6.37.
Modelled reward, frozen set, R3-correct denominator: **$68.91/day (0.25) → $75.13 (0.15) → $86.95
(0.10) → $93.80 (off)**, i.e. **+9% to +36%**.

⚠ **These $/day are MODELLED reward-side upper bounds.** Canon §M7d measured the model
over-predicting **2–6×** against receipts (`KXAAAGASD-26JUL23`: $50.88 predicted vs $10.09
received, ~5×) and names competitive dilution as the cause. Treat as **ratios only**. The model
cannot see fill rate, queue position, or adverse selection — it has no queue-position input at all.

### 2.3 ⚠ THE CAPITAL HEADROOM IS UNRESOLVED — TWO MEASUREMENTS DISAGREE BY 2.2×

| measurement | gas-only committed | headroom vs $85 | basis |
|---|---|---|---|
| depth study, live replay, ONE instant, gate at 0.25 | $31.9 (3 admits) | ~$53 | instantaneous `cap_desired` |
| survivor basket, greedy, **admission-weighted** | $47.96 (K=7) | ~$37 | `capital × adm_pct/100` |
| refuter re-solve, greedy, **RAW** capital, union over 7 instants | **$70.74** | **$14.26** | worst-case simultaneous |

The refuter's accounting critique is **correct and I accept it**: weighting *reward* by admission
rate is defensible, weighting *capital* is not. The quoter's cap is instantaneous and unweighted —
`if not reducing and committed + cost > MAX_TOTAL_CAPITAL` (`maker_kalshi_quoter.py:1274`), where
`committed` is surviving standing orders **plus gross held inventory**
(`committed += held_cost`, `:1259`). Note the asymmetry that makes it worse: `naked_held_cost()`
strips paired inventory from the **breaker** (`:927`), but the gross figure feeds the **capital
gate** — a ladder pair consumes the $85 in full while being invisible to `HELD_MAX_USD=20`.
Admissions are also not independent: the binding gate is book depth, which is common-mode across a
series and across the venue's quiet hours, so the expectation-smoothing the weighting assumes is
exactly what does not happen.

But the RAW figure is a **union over 7 instants** — an upper bound on simultaneous admission, not a
measurement of it. **True headroom is bounded [$14.26, $53] and is not yet a number.** Settling it
costs nothing (§6 item 1) and it gates every breadth decision, so it is item 1 of the plan.

### 2.4 The breaker is the second constraint, and it LATCHES

`naked_held_usd ≥ 20` in exactly **148/431 cycles (34.3%)** — identical to the 148 breaker cycles,
so it is the **level** trip on `HELD_MAX_USD=20` (`:936`), never velocity. Clean same-footprint
comparison (segment B, footprint == 10 in all 285 cycles):

- breaker = 0 (n=182): quoted 1.80, resting **$13.99**, two_sided 1.27
- breaker = 1 (n=103): quoted 1.26, resting **$3.77 (−73%)**, two_sided **0.00**

**One run of 100 consecutive cycles, 04:42:24 → 09:01:37 (~3 h 20 m, 23% of the sampled day)** with
`naked_held_usd` frozen at exactly **$23.36** — zero fills, so nothing could clear it.
`TAKER_FLATTEN=0` for the whole day until 15:19Z is why the reduce-only quote never got help.
**This is a latch with no time-based escape.** The 15:19Z `TAKER_FLATTEN=1` flip may be the release
— **unverified, n=3 cycles post-flip** — and §6 item 0 argues it may make things worse, not better.

**This ordering matters:** relaxing `MIN_DEPTH_SYM` produces more fills → more naked inventory →
more level trips. Verify the latch releases *before* relaxing the gate, not after.

### 2.5 Depth vs breadth — the recommendation

**DEPTH.** Not because breadth is bad in principle, but because:

1. K ≥ 6 is reachable **from the existing allowlist by moving one number**, with no new series, no
   new toxicity surface, and no new structural risk;
2. at K=7 `MAX_TOTAL_CAPITAL=85` plausibly becomes the next binding constraint (§2.3 unresolved),
   in which case new series cannot deploy dollars the cap forbids;
3. `PER_SERIES_CAP=10` is technically binding (KXAAAGASW has 15 active programs; footprint was
   exactly 10.00 for 285 consecutive cycles) — but **every KXAAAGASW program carries an identical
   $100 pot / $15.376 per day**, so the 5 excluded strikes are chosen by ticker-alphabetical
   tie-break, not by value, and they are the far-OTM ones already failing `one_side_no_bids`.
   **Raising `PER_SERIES_CAP` buys nothing.**

**PROPOSED KNOB — Tier 2 (trade-universe gating):**

```
KALSHI_MIN_DEPTH_SYM   0.25  ->  0.20      (stage 1)
```

- **Rollback:** set `KALSHI_MIN_DEPTH_SYM=0.25` in live.env; next cycle picks it up; **no deploy,
  no restart, no code change.**
- **What is now admitted:** books whose thin side is 20–25% of the deep side. Live replay: 3 → 6
  markets admitted. Frozen replay at 0.20 is **NOT MEASURED** (0.25 → 2.04, 0.15 → 3.74/cycle) —
  flagged.
- **Why 0.20 and not the depth lane's 0.15:** 0.20 captures the *entire* measured live gain (both
  0.20 and 0.15 admit 6) at the smallest move into unmeasured book regime. The depth lane's own
  counter-argument is strong and I am not discounting it: the sym distribution is **bimodal** —
  frozen n=174 gate-relevant snapshots, 68% of mass in 0.06–0.24, a second mode at 0.30–0.82, and
  **0.25 sits in the valley between them.** This is not a marginal setting; it moves the bot into a
  structurally different book regime (thin side / 4–14× deeper other side) — the regime the gate's
  own comment says adverse-selects us and then will not let the passive exit fill. Canon §M8/§M13
  attribute the realised losses to exactly that adverse selection.
- **I am recording the deviation:** the depth lane proposed **0.15**. I propose staging through
  **0.20** because the breaker interaction (§2.4) is unmeasured and 0.20 costs nothing in measured
  admits. If the operator prefers the lane's number, 0.15 is defensible on the same data.

**Gate the change on:** §6 item 0 closed, and ≥50 observed cycles spanning the 04:00–15:00Z trough
confirming the naked-held latch releases under `TAKER_FLATTEN=1` (P17 window rule).

---

## 3. BREADTH — **NOTHING SURVIVED. THERE IS NO SLATE.**

**I am not manufacturing one.** Stated plainly:

> **Of ~162 series with active liquidity programs: 23 (14.2%) passed the horizon ratio filter at
> ≤1.5, 29 at ≤2.0. Of the 29 taken to qualification: 0 ADMIT, 15 REJECT, 14 NEEDS-PROBE. Of the
> 14 NEEDS-PROBE, ZERO survive the three adversarial refutations. The admissible slate today is
> EMPTY.**

### 3.1 The universe

Venue at 15:25Z: **2,329 active liquidity programs / 162 series / $35,613/day headline pool**
(modelled, single page at `limit=10000`, no cursor). Programs are **1:1 with markets** (2,329
programs, 2,329 distinct `market_ticker`) — verified, so "the program" for a contract is
unambiguous.

| ratio ≤ | series |
|---|---|
| 1.05 | 18 |
| **1.50** | **23** |
| 2.00 | 29 |
| 5.00 | 45 |
| 10.0 | 54 |
| 100 | 121 |

161/162 had a measurable median ratio (KXBA: all 5 sampled contracts already closed while its
programs stay active — **flagged, unexplained**). The filter is genuinely discriminating and is not
an artefact — ratio = 1.00 means `program end_date == market close_time` **exactly**, hand-verified.
Everything above ~5 shares one shape: **a program terminating 2026-07-26T03:59:59Z on a market that
closes months later.** That is one venue-wide scheduling convention, and it is why ~85% of the board
is structurally wrong for us as currently coded.

### 3.2 Why each of the 14 NEEDS-PROBE candidates falls

Sample behind the qualification: 7 instants, 7 min apart, 16:22–17:04Z, 297 contracts, **2,079
book-snapshots**, full contract census (no top-N-by-pool sampling — every series here shares ONE pot
value across contracts, so "top N by pool" degenerates to API order; that defect previously produced
a false "100% two-sided / $7.42" on an n=4 sample).

| candidate | tail (h) | killed by | verdict |
|---|---|---|---|
| KXNETFLIXTOPVIEWSMOVIE | **48** | tail>0 breaks the exit ladder AND pairing is operative → **no exit path at all**; raw capital $53.01 vs headroom as low as $14.26; modelled $20.12/d cal → **$3.92/d** at receipt scale | **OUT** |
| KXNETFLIXTOPVIEWSTV | **48** | same, at 1/6 the value | **OUT** |
| KXMUSKNW | 19 | tail>0 | **OUT** |
| KXACTBLUETOP · KXNHSALES · KXFEDMENTION | 10 | tail>0; FEDMENTION → **$0.00/d** after R2 re-thresholding | **OUT** |
| KXAMSAVO | 2 | tail>0 (2 h is 4× `SETTLE_UNWIND_MIN=30`; the gap opens at *any* tail>0) | **OUT** |
| KXTRUTHSOCIAL | 0 | **BLOCKER** — `ladder_pairing` returns FULLY PAIRED on live event `KXTRUTHSOCIAL-26JUL25` across a `less`/`greater` pair; settlement `<80 → $2.00 · 80–240 → $1.00 · >240 → $0.00, both legs worthless`. The proposal's "SAFE-ABSTAIN" label read the wrong code path. | **HARD REJECT** |
| KXTRUMPENDORSEMENTS | 0 | pairing **DARK** (7/7 `_strike_of` parse-fail on the `A` prefix) → 100% of inventory counts naked against `HELD_MAX_USD=20`; and its $6.63/d sits on a *qualification knife-edge* (fragility >100%: added competitor depth makes a previously-failing side qualify and un-clears our bids) | **OUT** |
| KXB200MON | 0 | **$0.00/d** after receipt calibration + differential fragility — its value is diffusely spread across 2–20 ct reference levels. It was the proposal's "healthy low-concentration" pick; low concentration here is **diffuse fragility**, a rank inversion against the proposal's own materiality test. | **OUT** |
| KXEOWEEK | 0 | n=6 sampled contracts is really **n=2** after the 4 finalized markets; modelled $5.60/d → below the $1/period threshold at receipt scale | **OUT** |
| KXBIGBROTHERELIMINATION | 0 | modelled $1.23/d cal → ~$0.25/d real; 0% coverage of the 04:00–15:00Z trough | **OUT** |
| KXRTX5090MON | 0 | modelled $1.69/d cal → below floor | **OUT** |

*(The source verdict table is truncated at 13 named rows; the 14th is in `survivor_verdict.json`.
It is not carried forward, because the ranking it belongs to is itself refuted — see §3.4.)*

### 3.3 The four hard structural REJECTs (pre-admission, and the reason matters)

`event_deltas` was patched today to only net a provable ladder, but `_is_ladder_event`
(`maker_kalshi_quoter.py:1675`) is a **pure ticker-string test** — every ticker parses to a distinct
float. It never reads `strike_type` or `mutually_exclusive`. Four events pass it that must not:

| series | strike_types | mut_ex | code nets? | the $0 region |
|---|---|---|---|---|
| KXAPRPOTUS | between, greater, less | **True** | **YES** | long YES `<40.2` + long NO `>41.9` → **outcome >41.9 pays $0, both legs worthless** |
| KXTRUMPPHOTO | between (buckets 4/5/6/7) | **True** | **YES** | long YES `4` + long NO `7` → **outcome 7 pays $0** |
| KXNBATEAMANNOUNCE | custom (dates) | False | **YES** | **inverted polarity** — the LOW numeric strike is the TIGHTER condition; **Jul 24–27 pays $0**, i.e. the hole sits on the most likely dates |
| KXDXYDUD | greater | **True** | YES (n=1, no-op today) | — |

The `ladder_pairing` docstring promises "settlement returns ≥ $1 per matched pair". That floor is
real **only when the low strike is the LOOSER condition** (`greater`/`above` polarity). Because
paired quantity is excluded from unwind targeting, throttle direction, the settle-taker, the STOP
offsets **and** `naked_held_cost`, a mis-classification removes every de-risking path at once, on a
position that can settle to zero on both legs — with `strike_parse_failed = 0`, so the loud WARNING
never fires, and all three live ladder invariant checks in `run_once` PASS.

**Scope, stated precisely: this is a PRE-ADMISSION blocker, not a live-risk finding.** Every series
in `KALSHI_SERIES_ALLOW` — all five `KXTEMP*` plus GASD and GASW — was verified
`strike_type=greater`, `mutually_exclusive=False`, correct polarity. **The live book is safe today.**
This is direct evidence for open task **#3** ("find real polarity coverage") and names a victim for
open task **#12** ("read strikes off the market object, not the ticker string").

**And the guard is coincidence, not design.** `KXTRUMPENDORSEMENTS` is a genuine additive
`greater_or_equal` ladder, structurally identical to gas — pairing is dark on it purely because of
the `A` prefix. The same accident that correctly saves us on KXTRUTHSOCIAL's *event_deltas* path
cripples the one series we would actually want to pair.

### 3.4 The refuters' verdicts, reported even where they contradict the qualifier

| lens | verdict | severity | what it overturned |
|---|---|---|---|
| horizon-and-exit | **REFUTED** | HIGH | §2(a) "additive, not displacing" is a capital-accounting artifact (§2.3); tail>0 is **binary**, magnitude irrelevant; the ratio's denominator is a *period* boundary — programs are demonstrably re-issued mid-life on 9/15 series (median +2.0 h to +842.7 h), so every "tail" is an **upper bound on darkness**, not a measurement; the ratio is **non-stationary** (→∞ as now → period end; two survivors were ~30 min from crossing RATIO_CUT=2.0 and falling out of the shortlist that produced them) |
| structure-and-risk | **REFUTED** | **BLOCKER** | `ladder_pairing` (`:1631`) never calls `_is_ladder_event` (which guards `event_deltas` only, `:1697`); the proposal's `structure_verdict` measured the wrong path. KXTRUTHSOCIAL pairs with a $0 region on a LIVE event. Also: verdicts are **heterogeneous across events** on 4 series (27 of 29 rows were scored on `events: 1`, while admission is at SERIES level and unbounded over future events — `:296`) |
| reward-reality | **REFUTED** | HIGH | the rulebook plumbing is sound (R1/R2/R3/R4 correctly applied), but "treat as ratios only" hides that the over-prediction factor is **not common-mode**: Spearman ρ = **−0.784, n=38**, between modelled capture and share retained if one competitor posts 100 ct. The ranking IS a ranking of how empty the reference level is. Value-weighted retention: **GASD 65.7% vs non-gas 46.5%**; 77% of non-gas modelled value ($50.42 of $65.28/day) sits on contracts with a <25 ct reference on at least one side. Re-applying **R2 as a threshold** (not a scaling) after the receipt-calibrated 5.04× haircut: **4 series → exactly $0.00/day**, 9 of 14 below $1.00/day, non-gas subtotal $65.28/d → **$10.05/d**. |

**Where the refuters contradict the qualifier, the refuters have the measurement and I side with
them** — except on one point, recorded as an open disagreement:

- The horizon lens calls §2(a) *"a measured factual error"*. It is a **methodological** error for
  certain (admission-weighted capital vs an unweighted instantaneous cap) but its RAW counter-figure
  is a 7-instant union, itself an upper bound. **Neither number is the headroom.** §6 item 1 settles
  it offline.

**What none of this covers, on every number above:** fill rate, queue position, adverse selection,
settlement toxicity. Structure probes are one instant (16:2x–17:5xZ). The dark-book exit census is
one instant at 17:15Z, n=30 (n=18 survivors-only), and **does not span the 04:00–15:00Z trough**.
The 5.04× receipt anchor inherits canon §M7d's n=4 receipt sample and its partial-scroll caveat.
The +100 ct competitor is a **sensitivity, not a forecast** — competitor arrival rates were not
measured.

### 3.5 ⚠ LANE THAT RETURNED NOTHING

**TOXICITY: `UNMEASURED` on 29 of 29 candidates.** Not "low", not "acceptable" — *not measured at
all*. There is no fill-rate, queue-position, or realised-adverse-selection number for **any**
non-gas series in this study. Canon §M8/§M13 identify adverse selection as the source of the entire
realised loss to date. **No series may be admitted on a reward-side model alone.** This is the
single largest hole in the breadth case, and it is why §3's answer is "nothing", not "the least-bad
three".

---

## 4. ADMISSION CRITERIA — MECHANICAL CHECKLIST

Apply in order. **Any FAIL is terminal — do not weigh it against a high $/day.** Every item is
cheap and offline except C7 and C8.

**C1 — TAIL HOURS, not ratio. `tail = market close_time − program end_date`. REQUIRE `tail == 0`.**
Ratio is scale-free, hides carry, and is non-stationary (→∞ as now → period end). Tail>0 is a
**binary** disqualifier under the current code (§5), not a magnitude to trade off: 2 h fails for the
same reason 48 h fails. *Waived only if §5's redesign ships.*
⚠ Because programs are re-issued mid-life (9/15 series measured), `tail` is an **upper bound on
darkness**. Re-issue must be measured over ≥3 Time Periods before any waiver.

**C2 — MAKER FEE FREE.** `/series/{ticker}.fee_type` → default multiplier 0. Canon §M10: only 130 of
12,151 series charge; of series with active programs, only **KXAAAGASM**. Verified FREE on all 29
candidates. Effectively never binds — keep it as a cheap assertion, not a discriminator.

**C3 — PAIRING FLOOR PROOF (replaces "structure").** For **every open event** in the series, run the
live `ladder_pairing` over the real ticker set and, for **every pair it declares**, assert payout
**≥ $1.00 in all outcome regions** using each leg's actual `(strike_type, yes_sub_title)` read
**off the market object**, not the ticker string. Any $0 region → **HARD REJECT**.
*Do NOT use `events_code_abstains` / `_is_ladder_event` as the test — `ladder_pairing` never calls
it (`:1631` vs `:1697`), and that substitution is exactly what let KXTRUTHSOCIAL through.*

**C4 — ALL EVENTS, NOT ONE.** Admission is at SERIES level (`:296`) and unbounded over future
events. C3 must pass on every open event (cursor-paginated), and the series must be re-checked when
new events open. Heterogeneous-across-events was measured on 4 of 20 series.

**C5 — R3 TWO-SIDEDNESS, TIME-INTEGRATED.** Not a snapshot. Sample ≥50 observations at ≤15-min
spacing **spanning the 04:00–15:00Z trough** (P17). Report the mean payout fraction over INCLUDED
(two-sided) snapshots only, per canon R4. Reject if the reference level fills in or the book goes
one-sided overnight. *Today's 7-instant/42-minute sample pinned 81 of 98 admitted contracts at
exactly 0% or 100% — the time axis carried almost no information.*

**C6 — RAW CAPITAL FITS THE HEADROOM.** Sum the **unweighted, instantaneous** capital of the
contracts the series would admit, add **gross** held inventory (`:1259`), and require it to fit
under `MAX_TOTAL_CAPITAL` **with the incumbent basket intact**. Never admission-weight capital.
If it does not fit, the series is a **displacement**, and must then beat gas on **$/day per $
committed**, not on materiality.

**C7 — DARK-HOUR COVERAGE + DARK-BOOK EXITABILITY.** Two separate things:
 (a) *coverage* — which UTC hours of the 04:00–15:00Z trough the series' programs actually cover.
 Currently worth **little**: union coverage is already 99.2%/90.1% (§1), so a new series buys
 diversification of *earning*, not of *presence*.
 (b) *exitability* — for markets open with **no** active program, measure both the strand-unwind
 precondition (both best bids present **and** `sby + sbn < 1.0`, `:1101`) and direction-correct
 `flatten_to_zero` feasibility (long YES needs a YES bid; long NO needs a YES ask = 1 − best NO bid;
 `price is None or not (0.01 ≤ price ≤ 0.99)` is a **silent no-flatten**, `:1417-1418`).
 **Measured today (n=30 dark books, ONE instant 17:15Z, 16 series, 274 open markets enumerated;
 does NOT span the trough):** strand-unwind precondition holds **16.7%** (survivors only: 11.1%) ·
 long-YES flattenable 40.0% · long-NO flattenable 76.7% · **both directions exitable 16.7%**
 (survivors only 11.1%). **Dark books are not thin — depth is 5,000–40,000 ct — they are ONE-SIDED.
 Availability is the problem, not slippage.**

**C8 — TOXICITY MEASURED, NOT ASSUMED.** A reward-side model has **no** fill-rate, queue-position,
or adverse-selection input. `UNMEASURED` is a **FAIL**, not a neutral. Minimum bar: a sandbox or
minimum-size paired study over ≥1 full Time Period, reported with its "what this does not cover"
caveat, per the standing Kalshi handoff rule.

**C9 — PROBE ORDER (if a probe is ever authorised).** Probe the **LEAST FRAGILE** survivor first,
not the highest-scoring one. Ordering by share-retention rather than modelled $/day inverts the
list: KXCHIPBURRITO (91.6%), KXTRUMPUAP (94.7%), KXNBATEAMANNOUNCE (72.4%),
KXBIGBROTHERELIMINATION (71.5%) are the only books where the model operates inside its calibrated
regime, and the only ones where a real fill tells you about *reward capture* rather than about being
the sole maker in the room. **A top-down probe off the modelled table would spend capital and
unmeasured adverse-selection exposure on the emptiest books in the sample** — precisely canon §M8's
sole-counterparty-to-informed-flow setup.

---

## 5. WHAT WOULD HAVE TO CHANGE IN THE CODE FOR HIGH-RATIO SERIES

Target: KXRT (9.9), KXFUNDRAISING (35.6), KXDPZ (79.5), KXVOTEPRIMARY (152.1) — and more usefully
the *modest*-tail candidates (KXAMSAVO 2 h, KXNHSALES/ACTBLUETOP/FEDMENTION 10 h, KXMUSKNW 19 h,
KXNETFLIXTOPVIEWS* 48 h), which are the ones actually worth wanting.

### 5.1 The mechanism, exactly

**Every de-risk constant is keyed to the PROGRAM end_date. Exactly one is keyed to the MARKET close
time.** With `tail == 0` these coincide and the ladder is contiguous — **that, not "the reward spans
the hold", is the mechanical reason gas works.** With `tail = T > 0` the ladder splits into two
halves separated by T hours of no coverage.

| surface | file:line | clock |
|---|---|---|
| footprint membership + late-life cutoff | `maker_kalshi_quoter.py:296`, `:309`, `:314` (`"end": end.isoformat()` ← **program** `end_date`) | PROGRAM |
| wind-down / quote pull | `:445` (`end = parse_iso(m["end"])`), `:450` | PROGRAM |
| ramp scaling | `:319` (`ramp_min` from program days), `:555` | PROGRAM |
| settle-taker arming (`near_settle`) | `:981-982` (`market.close_time`, `SETTLE_UNWIND_MIN=30`, clamped ≤ `WIND_DOWN_MIN` at `:169-172`) | **MARKET** |

During the T-hour gap:
1. the ticker is **not in `progs`**, so it cannot be in the footprint at all (`select_footprint`
   iterates the program list, `:282-320`);
2. `near_settle` is False for T − 0.5 h, so the taker is unarmed (`:982`);
3. the **only** surviving mechanism is the STRAND UNWIND (`:1083-1110`) — and it is gated on
   `if sby is None or sbn is None or sby + sbn >= 1.0: continue` (`:1101`), a two-sided uncrossed
   book, which **16.7% of dark books satisfy** (n=30, one instant, does not span the trough).

### 5.2 And a MATCHED PAIR has **no** exit path at all

All three remaining position-exit loops iterate `naked_by`:

- settle-taker trigger — `for t, pos in list(naked_by.items())` (`:976`), skip at `:977`
- strand unwind — `for t, pos in list(naked_by.items())` (`:1088`), skip at `:1089`
- ladder escape hatch — `for t, qn in list(naked_by.items())` (`:1124`), skip at `:1125`

A fully paired ticker has `naked = 0` → `abs(pos) < INV_TOLERANCE` → **skipped in all three.**

**This inverts the proposal's own safety label.** It awarded SAFE-LADDER ("pairing operative, nets
1/1") to KXNETFLIXTOPVIEWSMOVIE, its #1 survivor, with a 48 h tail. **Pairing-operative + tail>0 is
not a safety property — it is the attribute that removes the exit.** Meanwhile pairing-dark
KXTRUMPENDORSEMENTS was flagged as a defect when, at tail = 0, it is the harmless case.

Capital consequence: the pair keeps consuming `MAX_TOTAL_CAPITAL` in **gross** terms
(`committed += held_cost` `:1259`, gate `:1274`) for the entire tail, and **capital is released only
at settlement — there is no code path to release it earlier.** Modelled capital-lock haircut:
NETFLIX 68.0% compensated ($18.83/program-day → $12.81/lock-day), MUSKNW 95.8%, ACTBLUETOP 84.7%,
NHSALES/FEDMENTION 96–97%, gas 100%. **The haircut is modest and is NOT the argument** — the
argument is the absent release path.

### 5.3 Small fix or redesign? **REDESIGN. Four coupled surfaces, no one-liner.**

A one-line "also iterate `held_by`" in the strand unwind is **not** sufficient and would be actively
dangerous: resting a reducing quote on one leg of a floored pair converts a ~riskless pair into a
naked directional position at the moment of fill. What is actually required:

1. **A second clock.** `select_footprint` must emit both `program_end` and `market_close`
   (`:314`), and every consumer at `:445/:450/:555` must choose the right one: *entry* gating on
   program end, *risk* gating on market close.
2. **A risk-only footprint tier.** Program-expired tickers with inventory must remain visible to the
   position-control path — today they exit the footprint entirely and only the strand path sees
   them, which is why the exits are one-sided and conditional.
3. **A PAIR-UNWIND primitive.** A first-class "unwind BOTH legs of a floored pair together" that
   the strand path can target, with the combined realised loss bounded like `MAX_UNWIND_LOSS`
   (`:200`). This is genuinely new logic, not a loop-condition change.
4. **`flatten_to_zero` must be capped at the NAKED remainder,** not `pos0` = full signed venue
   position (`:1400`). The code comment at `:989-991` calls this "bounded pennies, rare" — measured
   on real gas tickers it is a **20-lot de-hedge**: held `{4.050:+25, 4.130:−20}` → naked `{+5, 0}`;
   the +5 trips the naked-only trigger; the flatten crosses all 25; the surviving −20 flips from
   floored pair to fully naked directional. **Every ladder series admitted multiplies this path.**

**And the code fix would still not be sufficient.** 83% of dark books are one-sided (n=30, one
instant). Building the exit path does not create a counterparty. **C7(b) must pass on the venue
before the redesign is worth building.**

**Estimate:** 4 coupled surfaces + a new primitive + invariant tests + an adversarial review pass.
This is a project, not a session task. **Recommendation: do not start it until (a) C7(b) is measured
across ≥50 observations spanning the trough, and (b) the depth expansion has run long enough to say
whether K≈6–7 on gas alone is capital-saturating.** If gas alone saturates $85, the entire
high-ratio universe is moot at current bankroll.

---

## 6. SEQUENCED PLAN — CHEAPEST FIRST

**FREE** = offline / read-only, no config, no capital, no orders.
**LIVE CONFIG** = a `live.env` value change (no deploy, no code).

| # | item | cost | blocks | detail |
|---|---|---|---|---|
| **0** | ⚠ **`TAKER_FLATTEN=1` safety re-check — DO THIS FIRST, it is not an expansion item** | FREE (offline replay + read-only positions snapshot) | everything | Two measured failure modes on the CURRENT book: **(a)** `flatten_to_zero` cancels the ticker's resting orders first (`:1394-1398`) and `run_once` pops `standing` unconditionally (`:1000`) whether or not the flatten succeeded — on a one-sided book the IOC loop breaks at `:1417-1418`, `taker_failed` increments, and the documented fallback (the strand unwind) fails on the *same* one-sided book. **Net effect: our resting exit quote is cancelled and nothing is put back.** This failure mode did not exist before 15:19Z today. **(b)** the trigger is naked-only but the flatten crosses the full venue position → de-hedges live gas pairs (§5.3 item 4). **Verify by replaying today's positions snapshot offline:** for every ticker where `\|naked\| ≥ INV_TOLERANCE=3` while `\|held\| > \|naked\|`, compute the orphaned quantity the present code would leave on the sibling strike. **If that is non-zero on today's book, `TAKER_FLATTEN=1` is de-hedging live pairs right now and the whole expansion question waits behind it.** |
| **1** | RAW-CAPITAL BASKET RE-SOLVE | FREE, ~10 lines, new file | §2.3, C6 | Re-run the greedy with **raw** capital instead of `capital × adm_pct/100`, add gross held inventory to `used`, and use a **single instant** (not a 7-instant union). Prototyped in `kalshi_live/kalshi_basket_capital_recheck.py`. Output: the actual headroom number, currently only bounded [$14.26, $53]. **This is the cheapest disqualifying check in the whole document.** |
| **2** | PAIRED-POSITION EXIT ASSERTION (code test) | FREE | §5 | New test file (**NOT** `test_live_hardening.py` — concurrently owned). Construct `held_by = {low:+20, high:−20}` on a proven ladder event, run `ladder_pairing`, assert the ticker is absent from the strand-unwind loop (`:1088`), the taker trigger (`:976`) **and** the escape hatch (`:1124`); then quantify capital-days locked as gross `held_cost × tail_hours` against `MAX_TOTAL_CAPITAL=85`. It will pass. That is the point: it converts §5.2 into a standing regression guard and discharges part of open task **#4**. |
| **3** | PAIRING-FLOOR PROVER (C3/C4) | FREE | all breadth | Generalise `kalshi_pairing_floor_refute.py`: every candidate series × every open event → assert ≥$1.00 in all outcome regions off the market object. It already returns `FLOOR_CLAIM_FALSE=True` on the live `KXTRUTHSOCIAL-26JUL25`. **Until this replaces `events_code_abstains`, no series may be admitted on the current `structure_verdict`.** Feeds open tasks **#3** and **#12**. |
| **4** | LONGITUDINAL RE-ISSUE CENSUS | FREE, ≥72 h wall-clock | C1 | Snapshot `/incentive_programs?status=active` every 15 min, keyed by `market_ticker`, alongside `/markets` status+close_time. For every (market, program) whose `end_date` passes while the market is OPEN, record whether a new program appears and the gap in hours. Per series: re-issue rate, median dark-gap hours, fraction of market life uncovered. **Refuse to rank any series with n < 3 periods.** Converts "tail" from an upper bound into measured darkness — and either validates or kills the whole ratio-based shortlist. |
| **5** | DARK-BOOK EXIT CENSUS (C7b, P17) | FREE, ≥1 full trough | §5, C7 | ≥50 observations at ≤10-min spacing **spanning 04:00–15:00Z**, on candidate series' open-with-no-program markets: strand-unwind precondition (`:1101`), direction-correct flatten feasibility per side (`:1417-1418`), depth at touch. Split by which side we would actually be long. **Report concentration — which series and which single event dominate the pooled sample — BEFORE quoting any pooled percentage.** Today's one-instant figure is 16.7% (11.1% survivors-only). |
| **6** | TIME-INTEGRATED SHARE-PERSISTENCE PANEL (C5) | FREE, ≥24 h | C5, C9 | ~20 top-value candidate contracts + the GASD control, 15-min spacing, ≥1 full 24 h cycle spanning the trough. Per snapshot: reference-level size excluding ours, modelled payout fraction, R3 two-sidedness. Report the mean over INCLUDED snapshots only; re-apply **R2 as a threshold, per Time Period**. **Decision rule:** if Netflix-25 / B200MON-6.660 / EOWEEK-1 hold within 2× of their instantaneous 0.33–0.45, the thin-reference edge is real; if they decay toward the incumbent's 0.045–0.077, the ranking was an unoccupied book at one instant and the probe order must be rebuilt on time-integrated share. Settles the 42-minute-sample problem for free. |
| **7** | BREAKER-LATCH OBSERVATION under `TAKER_FLATTEN=1` | FREE, ≥50 cycles spanning the trough | item 8 | Does `naked_held_usd` still latch (100 consecutive cycles at $23.36 was measured under `TAKER_FLATTEN=0`)? Currently n=3 post-flip. **Gate on item 0 being clean first** — if 0(a) holds, `TAKER_FLATTEN=1` makes the latch *worse*, not better. |
| **8** | ✅ **THE EXPANSION: `KALSHI_MIN_DEPTH_SYM 0.25 → 0.20`** | **LIVE CONFIG (Tier 2)** | — | Rollback: `KALSHI_MIN_DEPTH_SYM=0.25`, next cycle picks it up, no deploy. Expected: 3 → 6 markets admitted (live replay, one instant); modelled reward +9–36% (frozen set, **upper bound, model over-predicts 2–6×**). **Prerequisites: items 0 and 7 clean.** Hold ≥50 cycles spanning a full trough before considering 0.15, and watch `naked_held_usd` level-trip rate (baseline 34.3% of cycles) as the primary abort signal. |
| **9** | RECEIPT CALIBRATION POINT #2 | FREE (operator screenshot) | C8, all modelling | Once `KXAAAGASD-26JUL24`'s Time Period closes, compare receipts against this run's modelled $75.10/day (per-window-day) / $36.18/day (calendar). A second anchor in the **robust** regime, measured **prospectively** rather than retrofitted. If the ratio again lands near 5×, 5.04× is confirmed as the robust-regime anchor against which challenger fragility is measured. ⚠ Canon §M13: **credits LAG** — they post once per Time Period, after it closes. |
| **10** | `flatten_to_zero` naked-cap fix + `ladder_pairing` strike-type fix | CODE (out of scope for this doc) | high-ratio breadth | §5.3 items 3–4; open tasks **#4** and **#12**. Requires full ship discipline: self-test + pytest + adversarial review. **Note the fix un-darkens `KXTRUMPENDORSEMENTS`' pairing — re-measure its naked exposure vs `HELD_MAX_USD=20` in the same pass.** |
| **11** | THE TWO-CLOCK REDESIGN | PROJECT | tail>0 admission | §5.3 items 1–3. **Do not start until item 5 shows dark books are exitable at a rate the operator will name.** Today: 16.7% both-directions, one instant. |

**If exactly one thing gets done: item 0.** It is a live-risk check on the current config, it is
free, and it is the only item where doing nothing has a cost today.
**If exactly one expansion thing gets done: item 1** — it is offline, deterministic, and it inverts
or confirms the central capital premise for ~10 lines of code.

---

## 7. WHAT I WOULD NOT DO, AND WHY

1. **I would not admit ANY new series today.** Zero of 29 candidates pass §4. The gap is not
   "insufficient analysis" — it is that **toxicity is `UNMEASURED` on 29/29** (§3.5) and canon
   §M8/§M13 attribute the entire realised loss to adverse selection. Admitting on a reward-side
   model alone repeats the failure that produced the loss.

2. **I would not admit any `tail > 0` series at any tail length** until §5's paired-exit path
   exists. Not 2 h, not 10 h. The predicate is **binary** — the exit ladder loses contiguity at any
   tail>0, and a matched pair has *no* exit path in *any* of the three loops. `KXAMSAVO`'s 2 h is
   already 4× `SETTLE_UNWIND_MIN`.

3. **I would not carry the NEEDS-PROBE ranking forward.** Its #1 entry is simultaneously
   unfittable (raw $53.01 vs headroom as low as $14.26) and un-exitable (48 h tail with pairing
   operative), and its "healthy" pick (KXB200MON) goes to **$0.00/day** at receipt scale. A future
   session probing top-down off that table would spend real capital on the emptiest books in the
   sample. **The ranking must be rebuilt on time-integrated share and raw capital, or not used.**

4. **I would not raise `MAX_TOTAL_CAPITAL` to make room for breadth.** The $85 cap is the only thing
   currently bounding gross exposure, `HELD_MAX_USD=20` is deliberately sized to about one day's
   measured rewards, and the equity loss-meter is already known to be corruptible by deposits.
   Raising the cap to fit a slate that failed admission is solving the wrong problem.

5. **I would not touch `MAX_SPREAD_TICKS=8`.** 2/353 frozen, 0/71 live. It is not binding. Changing
   a non-binding gate adds regime risk for zero measured gain.

6. **I would not raise `PER_SERIES_CAP=10`.** It binds nominally on KXAAAGASW (15 active programs)
   but every program carries an identical $100 pot / $15.376 per day, so the 5 excluded strikes are
   selected by ticker-alphabetical tie-break — and they are the far-OTM ones already failing
   `one_side_no_bids`. Zero expected gain.

7. **I would not "fix" `_strike_of` to parse `A`/`B` prefixes as a standalone change.** It looks
   like a bug (it darkens pairing on a genuine `KXTRUMPENDORSEMENTS` ladder) but fixing it in
   isolation **activates `ladder_pairing` on series whose ≥$1 floor is unproven** — including
   `KXTRUTHSOCIAL`, where the floor is measurably FALSE. **Order matters: the floor prover (item 3)
   and the strike-type fix must land in the same change, or the parse fix ships a silent $0-floor
   pairing.**

8. **I would not use the horizon ratio as the shortlist key going forward.** It is non-stationary
   (→∞ as now → period end; two survivors were ~30 min from falling out of the shortlist that
   generated them), and its denominator is a **period** boundary, not a **reward** boundary —
   programs were measured being issued 2 h to 843 h into a market's life on 9 of 15 series.
   Replace it with C1 (tail hours) + item 4 (measured re-issue).

9. **I would not add series to increase duty cycle.** There are no dark hours (§1.1). Any breadth
   case must be argued on *earning* diversification, never on *presence*.

10. **I would not run a live probe before a sandbox paired study.** Standing Kalshi lane rule: the
    no-money paired studies answer the strategy questions a live A/B cannot (confounded, and costs
    real money). The two realised lessons on this lane — ~−$45 from two taker fire-sales and −$21
    from go-live timing into informed flow — were both timing/execution, not model error.

11. **I would not deploy anything.** This document changes nothing. Item 8 is the only live change
    proposed, it is a single env value with a one-line rollback, and it is gated on items 0 and 7.

---

## APPENDIX — ARTEFACTS

All new files, all under
`…/scratchpad/kalshi-wt/kalshi_live/` unless noted. Nothing existing was edited.

**Coverage:** `kalshi_coverage_gap.py` → `coverage_gap.json`
**Horizon:** `kalshi_horizon_census.py` → `horizon_census.json`, `horizon_census.log` ·
`kalshi_horizon_deepdive.py` → `horizon_deepdive.json`, `horizon_deepdive.log`
**Depth/capacity:** `kalshi_depth_capacity_study.py` · `kalshi_selection_gate_study.py`
**Qualification:** `kalshi_survivor_qualify.py` · `kalshi_ladder_safety_probe.py` ·
`kalshi_survivor_basket.py` · `kalshi_survivor_verdict.py` → `survivor_qualify.json`,
`ladder_safety_probe.json`, `survivor_basket.json`, `survivor_verdict.json`, `survivor_duty.json`
**Refutations:** `kalshi_tail_exit_probe.py` · `kalshi_tail_capital_lock.py` ·
`kalshi_basket_capital_recheck.py` → `basket_capital_recheck.json` · `kalshi_program_reissue_probe.py` ·
`kalshi_multievent_structure_refute.py` → `multievent_structure_refute.json` ·
`kalshi_pairing_floor_refute.py` → `pairing_floor_refute.json` ·
`kalshi_reward_reality_refute.py` → `reward_reality_refute.json` ·
`kalshi_share_fragility_probe.py` → `share_fragility.json`
**This document:** `docs/maker_handoffs/KALSHI_EXPANSION_PROPOSAL_2026-07-23.md`

**Open tasks this document feeds:** #3 (polarity coverage — §3.3 provides it) · #4 (ladder
self-hedge review debt — §5.2 + plan item 2) · #12 (read strikes off the market object — §3.3
names the victim and plan item 7 names the ordering constraint).

**Canon dependencies:** §T (sector > series > event > market) · R1 (period_reward is a TOTAL,
normalise to $/day) · R2 ($1.00 is a THRESHOLD on the whole-period payout — **re-apply it after any
haircut, do not scale through it**) · R3 (two-sided exclusion is MARKET-level, apply BEFORE ranking)
· R4 (payout fraction = mean over snapshots) · §M7d (model over-predicts 2–6×) · §M8/§M13 (adverse
selection is the loss; credits LAG) · §M10 (maker fees free by default).
