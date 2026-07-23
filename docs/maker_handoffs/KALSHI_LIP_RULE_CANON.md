# KALSHI LIP — RULE CANON (primary sources, quoted verbatim)

**Created 2026-07-23 after the operator asked "how do we not know pool rules?"**

Honest answer to that question: every prior use of `period_reward` was a **ratio** (arm A vs
arm B on the same market), and a ratio cancels the period length. Nobody had to know the unit,
so nobody pinned it. It only became load-bearing the moment a **dollar** figure was quoted.
That is the failure mode — a quantity can sit unverified for weeks because the way it is used
happens to hide the gap.

**This file is now canon. Pull rules from here, not from memory, not from a handoff paraphrase.**

## §T — TERMS (operator-agreed 2026-07-23). USE THESE EXACTLY.

Counts verified live from `/incentive_programs?status=active`, 2026-07-23 ~03:20Z.

| term | example | count | what ATTACHES at this level |
|---|---|---|---|
| **sector** | weather · gas · politics · sports | ~a dozen | **OURS, not Kalshi's** — thematic grouping for EV analysis (running tab §C). No API field. |
| **series** | `KXAAAGASD` | **192** | maker-fee regime · toxicity · ladder-vs-categorical structure. **The level you switch ON or OFF** (`KALSHI_SERIES_ALLOW`). |
| **event** | `KXAAAGASD-26JUL23` | **344** | **ONE CORRELATED RISK.** All strikes inside move together. Grouped by `"-".join(ticker.split("-")[:2])` (`maker_kalshi_quoter.py:1591`). |
| **market** (= **contract**) | `KXAAAGASD-26JUL23-4.100` | **2,547** | the order book · YES/NO sides · **reward pool · $1 floor · Target Size · Discount Factor · two-sided test**. The smallest unit. |
| **program** | the LIP schedule on that contract | **2,547** | one Time Period, one payout. Currently 1:1 with market — but the rulebook allows "a sequence of one or more Time Periods" and "Time Periods may overlap", so **do not hard-code 1:1**. |

### The two traps this glossary exists to prevent

1. **"market" is the SMALLEST unit, not a marketplace.** Everyday English says "the weather
   market" meaning a whole space; Kalshi's API and every line of our code mean **one binary
   contract**. `MAX_MARKET_CAPITAL=$15` read as "sector" is $15 across all of gas; read as the
   code means it, it is $15 on **each of 24 strikes** — up to $360. **24× apart on the same
   words.** Same trap for "$1 minimum per market" and "one bet per market".
   **Mitigation: say "contract" whenever precision matters.**
2. **"event" is ONE RISK but MANY trades.** A gas event holds 24 tradeable contracts. Our 8 live
   positions sit inside just **2** events — so as risk we hold *two* bets, not eight.

### Where reward and risk diverge — the structural tension

**Reward accrues per CONTRACT** (24 strikes in a gas event = 24 separate pots, each with its own
$1 floor) → this rewards spreading WIDE.
**Risk accrues per EVENT** (those 24 contracts are one directional bet on gas) → spreading across
them diversifies **NOTHING**.

- A **threshold ladder** ("above X") is therefore unusually good: many pots, one risk, and
  adjacent strikes partly offset — which is exactly what the ladder self-hedge exploits.
- A **categorical event** (named/mutually-exclusive outcomes) breaks it: strikes are
  ANTI-correlated, so the event-aggregate throttle would sum them as additive and **mis-fire**.

⚠ **This limits §M1.** The concentration study asked "how many markets to spread $85 across" and
treated contracts as independent. They are not. "Optimum K≈6–7" is a **reward-side** answer only.
The real question is two-dimensional: how many **events** (risk units) and how many **contracts
within each event** (reward pots). §M1 measured the second and silently assumed the first.

### Naming defects corrected under this glossary
- `kalshi_sector_scan.py` → **`kalshi_series_scan.py`** (+ `sector_scan.json` →
  `series_scan.json`). It scans series, never sectors. Shipped mislabelled in `200222f`.
- §M5's heading "SECTORS WE ARE NOT IN" → **SERIES we are not in**.

## SOURCES (ranked by authority)

| # | source | what it settles |
|---|---|---|
| S1 | **CFTC filing, KalshiEX LLC — Amendment to August 2025 Liquidity Incentive Program, filed 2026-02-11, effective 2026-02-28** — `https://www.cftc.gov/sites/default/files/filings/orgrules/26/02/rules02112639183.pdf` | **THE rulebook.** Full Appendix A program terms, extracted from the PDF text layer. Everything below is quoted from it. |
| S2 | Kalshi Help Center — Liquidity Incentive Program — `https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program` | Consistent summary; **does NOT mention the two-sided rule at all**, so it is NOT sufficient on its own. |
| S3 | Live API `GET /trade-api/v2/incentive_programs?status=active` | Per-program `start_date`/`end_date`/`period_reward`/`target_size_fp`/`discount_factor_bps`. Empirically confirms S1. |
| — | CFTC Aug-2025 original filing `…/25/09/rules09082530054.pdf` | **Scanned image, no text layer** (5 pages, 4 extractable chars). Cannot be quoted. Superseded by S1 anyway. |

## R1 — `period_reward` is the TOTAL FOR THE WINDOW, not a rate  ✅ RESOLVED

> "'Time Period Reward' shall be no less than $10 and no greater than $1,000 **per calendar day
> encompassed in the Time Period**." — S1

> "'Time Period' shall be no greater than 31 days" — S1

> "a liquidity incentive schedule … comprised of a sequence of one or more time periods
> ('Time Period'), each with a corresponding target size ('Target Size'), discount factor
> ('Discount Factor'), and reward ('Time Period Reward')." — S1

**Payout happens ONCE per Time Period**, not hourly. The API's `start_date`→`end_date` IS the
Time Period. Measured on our own allowlist (S3, 2026-07-23T02:35Z) and **re-verified against the frozen
sample file itself**, not just a fresh API call — every stored row agrees exactly, single-valued
per series (GASD: pools {100.0}, windows {13.15h}; GASW: pools {100.0}, windows {156.08h}):

| series | n active | Time Period Reward | window | implied $/day |
|---|---|---|---|---|
| `KXAAAGASD` (gas daily) | 24 | $100.00 | **13.15 h** | **$182.51/day** |
| `KXAAAGASW` (gas weekly) | 15 | $100.00 | **156.08 h** | **$15.38/day** |
| `KXTEMP*` (all 5 cities) | **0** | — | — | — (no active programs at that moment) |

⚠ **THE TRAP THIS CREATES:** two programs can both show `period_reward = $100` and be worth
**12× different amounts per day**. Any code that sums or ranks raw `pool` across markets with
different windows is silently wrong. **Normalise to $/day before comparing anything.**
Window-length histogram across all 2,547 active programs is wide (227h is the modal bucket;
values from ~13h to ~698h), so this is not an edge case — it is the common case.

## R2 — the $1.00 minimum is a THRESHOLD, and it applies PER TIME PERIOD  ✅ RESOLVED

> "Each Time Period Liquidity Provider Score is multiplied by the Time Period Reward, and **if
> the result is greater than or equal to $1.00, the result is paid out** to the corresponding
> user, **rounded down to the nearest cent**." — S1

The ambiguity flagged earlier ("threshold vs rounding") is **settled: it is both, in sequence.**
Below $1.00 → **pays zero**. At or above → pays, rounded down to the cent.

**Crucially the test is against the WHOLE Time Period payout, not an hourly slice.** For a 13-hour
gas program with a $100 pool, clearing $1.00 needs only a **1% share sustained over 13 hours**.
This is why the "spreading thin rounds everything to $0" fear did not survive measurement (§M1).
It would bite much harder on a ~1-hour temp program — untested, see the gap in §M1.

## R3 — the two-sided rule is MARKET-level, NOT participant-level  ⚠ CORRECTS A LIVE ASSUMPTION

> "**Snapshots will be excluded if there is not two-sided liquidity** (i.e., resting orders
> sufficient to meet the Target Size **on each side of the market**) at the time of the Snapshot."
> — S1, Appendix A

And the stated purpose of the Feb-2026 amendment:

> "(i) exclude Snapshots where there are no qualifying 'Yes' bids or no qualifying 'No' bids.
> **This serves to limit liquidity rewards to two-sided markets.**" — S1, cover letter

**Read it precisely: the test is on THE MARKET'S BOOK, not on our own orders.** The snapshot is
excluded when *the market* lacks Target-Size resting liquidity on a side — regardless of who
supplied it. Quoting one-sided ourselves does **not** zero us in a market that is two-sided
because of other participants; it costs us only the normalised score on the side we dropped.

This matters because the reduce-only two-sided plug-in currently under live A/B is justified in
handoff §2 as: *"we went one-sided → earned **$0** while the guard was engaged."* Under R3 that
is **only** true where our own order is what carries a side over Target Size. Measured — see §M2
— that never happened once in the sample.

## R4 — the scoring formula (confirms our implementation)

> "each Qualifying Yes Bid is assigned a score equal to the **Discount Factor taken to the Nth
> power multiplied by its size**, where N is the number of ticks between the Reference Yes Price
> and the price of the Qualifying Yes Bid. The score divided by the sum of the scores creates a
> normalized score" — S1

> "The sum of all Normalized Qualifying Yes Scores and Normalized Qualifying No Scores
> corresponding to bids submitted by a single user is that user's **Snapshot Liquidity Provider
> Score**." — S1

> "the Snapshot Liquidity Provider Scores are totaled for each user and **divided by the sum of
> all Snapshot Liquidity Provider Scores**" — S1

Across all users the normalised Yes scores sum to 1.0 and the No scores to 1.0, so the
all-user total per snapshot is 2.0. Our payout fraction is therefore the mean over snapshots of
`(our_yes_share + our_no_share) / 2` — **exactly what `score_market()` computes.** The payout
model in `kalshi_concentration_study.py` is rulebook-correct; only its *units* were wrong.

Qualifying walk (also matches `maker_kalshi_recorder.qualifying_walk`): start at the reference
(highest bid below the max price), accumulate size down the book until Target Size is met; if the
book runs out first, **Qualifying Bids are cleared** — the side does not qualify at all.

> "Target Size will be greater than 100 contracts and less than 20,000 contracts" — S1
> "Discount Factor will be no greater than 1.00" — S1

Our allowlist runs `target_size_fp = 1000`, `discount_factor_bps = 5000` (DF 0.50) — so credit
**halves every tick** away from reference. That is a brutal decay and is why quoting a tick inside
costs so much reward (the throttle A/B's 1.59× finding).

## MEASUREMENTS TAKEN AGAINST THESE RULES

### THE DATASET — frozen, so every number below is reproducible

```
kalshi_live/concentration_samples.jsonl   md5 e920bf99850279099897a79e8ad78dec
27 paired snapshots · 353 market-snapshots · 02:25:02–02:48:36Z 2026-07-23
series mix: KXAAAGASD 189 · KXAAAGASW 164 · KXTEMP* 0
```

⚠ **CORRECTION (self-caught on the operator's "verify it matches the data" pass).** The first
version of this file quoted §M1 at *197* market-snapshots and §M2 at *184* — two different sizes
in one document, because each measurement was run at a different moment against a file the
sampler was still appending to. Neither was the final dataset. **Both sections below are now
re-run against the single frozen file above.** Every conclusion survived; the dollar figures moved
4–6%. Superseded figures are shown struck through rather than deleted, per the running-tab rule.

### §M1 — concentration: the premise did NOT survive
`kalshi_live/kalshi_concentration_study.py` on the frozen dataset. Same $85 spread across top-K
markets, scored on identical books.

| ranking | best K | $/day at best | $/day at K=14 | $/day at K=1 |
|---|---|---|---|---|
| oracle (hindsight upper bound) | **6** | **99.43** ~~103.17~~ | **70.16** ~~75.37~~ | **8.30** ~~9.36~~ |
| as-is (venue order, no skill) | **7** | **84.83** ~~89.23~~ | **70.16** ~~75.37~~ | **0.07** ~~0.00~~ |

- **The optimum is in the MIDDLE (K≈6–7), not at either extreme.** Heavy concentration is much
  worse; maximum spread is ~25–30% off the peak. Both rankings agree, which is the point of
  running the control — the oracle brackets the top, the as-is brackets the bottom.
- **Cost of the $1 threshold at max spread: ~1.3%** (raw $104.13 → floored $102.75 at K=14).
  The "spreading $85 thin rounds to $0 in most markets" hypothesis is **NOT SUPPORTED** — because
  R2's threshold applies to a 13-hour period total, not to an hourly slice.
- The deployed footprint (~10) sits near the plateau, so the tuning upside is ~10–15%, not a step
  change. `MAX_MARKET_CAPITAL=$15` binds at K≤5, so hard concentration isn't even reachable
  without raising it — and the data says don't bother.

**KNOWN DOWNWARD BIAS (found during verification).** Per R3, a snapshot lacking two-sided
qualifying liquidity is excluded from **both** the numerator and the denominator, so the period
fraction is the mean over *included* snapshots only. The study instead averages over **all**
sampled snapshots, scoring an excluded one at its reduced one-sided share. Since that value is
lower than the included-only mean, the study **understates** payout — conservative, in the safe
direction, and bounded by the 13.9% one-sided rate measured in §M2. Not corrected, because a
conservative reward estimate is the right error to have when the risk side is unmeasured.

**NOT COVERED:** reward side only — fill rate / adverse selection are not simulatable without
queue position, and concentration is strictly worse on exactly that axis. **Gas-only sample:
`KXTEMP*` had zero active programs in the window, and temp is both the #1 EV sector and the
~1-hour-window case where the $1 threshold WOULD bite hardest. The temp case is untested.**

### §M2 — are we ever the marginal maker? NO, in 304/304 — now tested on BOTH sides
For each two-sided market-snapshot, remove our 20 contracts from a side and re-run the qualifying
walk. The first version tested only the No side; this tests both.

```
market-snapshots            353     (Target Size = 1000 contracts, our size 20 ct)
  BOTH sides qualify        304     86.1%     (was 84.8% on the partial sample)
  yes only                   22      6.2%
  no only                    27      7.6%
  NEITHER                     0      0.0%
  our 20ct is marginal to two-sidedness:   NO side 0/304   ·   YES side 0/304
```

**Our 20 contracts never once decided whether a market qualified as two-sided, on either side** —
unsurprising against a 1000-contract Target Size. Combined with R3, the plug-in's premise
("one-sided ⇒ $0") does not hold in this sample. Dropping a side costs us **that side's score**
(up to half our snapshot score), which is a real but ~2× cost, not a total wipeout — and the
plug-in buys it back by resting an extra fillable order, i.e. with **risk**.

⚠ Same gas-only caveat. Thin temp books are exactly where 20 contracts could matter more.
**This does not by itself justify changing anything — it justifies re-deriving the plug-in's
expected value before the A/B result is acted on.** (task #7)

### §M4 — WHERE WE ARE vs WHERE THE DATA SAYS WE SHOULD BE (2026-07-23 ~02:55Z)

**Allocation within the allowlist is already near-optimal. The allowlist itself is the problem.**

Per-market expected `$/day` at the deployed shape (JOIN 20 ct/side, $15/market), scored on the
frozen dataset; live holdings from `kalshi_status_readonly.py`:

| rank | market | $/period | **$/day** | ours |
|---|---|---|---|---|
| 1 | KXAAAGASD-26JUL23-4.100 | 22.10 | **40.34** | ✅ |
| 2 | KXAAAGASD-26JUL23-4.095 | 16.81 | **30.68** | ✅ |
| 3 | KXAAAGASD-26JUL23-4.105 | 9.43 | **17.22** | ✅ |
| 4 | KXAAAGASW-26JUL27-4.160 | 35.02 | 5.38 | ✅ |
| 5 | KXAAAGASW-26JUL27-4.140 | 32.13 | 4.94 | ✅ |
| 6 | KXAAAGASD-26JUL23-4.090 | 2.54 | 4.64 | ✅ |
| 7 | KXAAAGASW-26JUL27-4.200 | 23.88 | 3.67 | ❌ |
| 8 | KXAAAGASD-26JUL23-4.085 | 0.72 | 1.31 | ❌ |
| 12 | KXAAAGASW-26JUL27-4.120 | 1.40 | 0.22 | ✅ dud |

- **We hold 7 markets worth $103.41/day of the $110.18/day available in-allowlist — 94% capture**,
  and 6 of the top 8. Actual K=7 vs the §M1 theoretical optimum of K≈6–7. **Bang on. There is
  nothing meaningful to gain by reallocating inside the allowlist.**
- ⚠ **The `$/period` column is exactly the trap R1 warns about.** Ranked by `$/period`,
  `GASW-4.160` ($35.02) looks like the single best market on the board. By `$/day` it is **7×
  worse** than `GASD-4.100`. A naive pool-ranking inverts the portfolio.
- Two duds held: `GASW-4.120` at $0.22/day, and `GASW-26JUL27-4.080` (a live position) has **no
  active program at all** — zero rewards, pure inventory risk.

**The real constraint — venue-wide census, all 2,547 active programs / 192 series:**

```
venue total pool      $49,411/day
our allowlist          $4,611/day  =  9.33% of the venue
  KXAAAGASD            $4,380/day  venue rank 3    <- 95% of our allowlist's value
  KXAAAGASW              $231/day  venue rank 43   <- dead weight
  KXTEMP* (all 5)            $0/day  NO ACTIVE PROGRAMS
```

- **`KXAAAGASD` is a genuinely excellent pick — rank 3 of 192 series venue-wide.** Not luck.
- **`KXAAAGASW` is dead weight**: rank 43, yet it holds 4 of our 8 positions and half our resting
  orders. Capital and inventory risk are sitting where the reward is not.
- **Temp is dark.** All five city series have zero active programs right now, so today the
  "#1 EV sector, ~30×" (running tab §C) contributes **nothing**. Any plan assuming temp carries
  us is wrong as of this timestamp.

#### ⚠ §M4a — this CORRECTS the parked widening recommendation in running tab §H

Running tab §H recommends: *"GAS candidate: `KXAAAGASM` (monthly, 54 mkts, **$5,400 pool = 5.4×
daily gas**). This is the recommended slight widening available now."*

**Measured: `KXAAAGASM` = $255/day, venue rank 39. `KXAAAGASD` = $4,380/day, rank 3.**
GASM is **~17× WORSE per day**, not 5.4× better. The $5,400 headline is a **monthly** pool; the
comparison silently divided a month by a day. **This is the R1 unit error, already sitting in a
standing recommendation, one session away from being acted on.** §H's widening advice is
**withdrawn** pending a $/day re-rank.

**CAVEATS on the venue census — do not act on the ranking alone:**
- `pool $/day` is the **size of the prize, not our capture**. Capture depends on competition,
  which needs books; only our own allowlist was book-scored here.
- **Toxicity is a separate axis and cuts hard against the top of the list.** Venue ranks 1–2 are
  `KXWNBAMENTION` / `KXMLBMENTION` ($4,469/day each) — the *mention* family the running tab flags
  as a settlement trap (`FIGHTMENTION` +745 in-window / −1338 settled). **High pool ≠ good.**
  Unevaluated large pools: `KXFUNDRAISING` ($2,501/day, rank 4), `KXLIUKELIMINATION` ($1,567/day),
  `KXRT` ($1,263/day).
- Single point in time; programs churn hourly.

### §M5 — SERIES WE ARE NOT IN: where could we eat, or get scraps? (2026-07-23 ~03:10Z)
*(terminology corrected per §T — this scans SERIES, not sectors)*

`kalshi_live/kalshi_series_scan.py` — top 40 series by pool $/day, **4 markets sampled each**,
scored at the deployed shape (join 20 ct/side, $15/market) with all four rulebook rules applied.
Output `kalshi_live/series_scan.json`.

⚠ **SCANNER BUG FOUND AND FIXED MID-RUN — the first output was inverted.** The initial version
scored share without applying R3, and put `KXWNBAMENTION` at the **top** with $604/day capture
while **0%** of its sampled books were two-sided. Under R3 an excluded snapshot pays **nobody**,
and per §M2 our 20 ct cannot rescue a book that misses a 1000-contract Target Size. With R3
applied that series scores **$0**. Every number below is post-fix.

#### The single most useful finding: the biggest pools are UNEARNABLE

| series | pool $/day | programs | two-sided |
|---|---|---|---|
| KXWNBAMENTION | 4,469 | 18 | **0%** |
| KXMLBMENTION | 4,469 | 18 | **0%** |
| KXLIUKELIMINATION | 1,567 | 13 | **0%** |
| KXHURCAT | 433 | 10 | **0%** |
| KXLUV | 433 | 15 | **0%** |
| KXCHINAAI | 380 | 18 | **0%** |
| KXGOOG | 362 | 9 | **0%** |

**$12,112/day of headline pool — 24.5% of the venue's $49,411/day — sat at 0% two-sided.**
Those snapshots are excluded and pay nobody, ours or anyone's. **Pool-size ranking points
straight at them.** This is why R3 has to be applied before any opportunity ranking.

#### Candidates that ARE two-sided (reference: `KXAAAGASD` = **$6.00/market/day**)

| series | $/mkt/day | 2-sided | programs | structure | verdict |
|---|---|---|---|---|---|
| KXCLAUDE | **12.21** | 100% | 4 | date-nested (monotone) | best per-market on the board, but n=4 and tiny |
| KXEARNINGSMENTIONLMT | 7.42 | 100% | 13 | — | ⚠ **mention family = settlement trap** |
| KXEARNINGSMENTIONAXP | 4.50 | 100% | 14 | — | ⚠ same |
| KXEARNINGSMENTIONAAL | 4.04 | 100% | 15 | — | ⚠ same |
| KXEARNINGSMENTIONINTC | 2.78 | 100% | 15 | — | ⚠ same |
| **KXINTC** | 1.98 | 100% | 9 | **numeric threshold ladder** | ✅ structurally safe |
| KXROLEINPRODUCTIONDOOMSDAY | 1.50 | 100% | 20 | **named/mutually exclusive** | ❌ event-aggregate would mis-fire |
| **KXPM** | 1.46 | 100% | 11 | **numeric threshold ladder** | ✅ structurally safe |
| **KXRT** | 0.69 | 100% | **70** | **numeric threshold ladder** | ✅ scraps-at-scale |
| **KXFUNDRAISING** | 0.59 | 100% | **86** | A-prefixed numeric ladder | ✅ scraps-at-scale |

- **"Eat in": nothing clearly beats what we hold.** `KXCLAUDE` is the only thing scoring above
  GASD per market, and it is 4 programs sampled 4 times — far too thin to act on.
- **"Scraps": `KXRT` (70 programs) and `KXFUNDRAISING` (86 programs)** are the real shape — low
  per-market, high program COUNT, 100% two-sided, threshold-laddered. Extrapolated $48/day and
  $51/day respectively vs GASD's $144/day.
- The `EARNINGSMENTION*` cluster scores well and is 100% two-sided, but it is the **mention
  family** the tab flags as a settlement trap (`FIGHTMENTION` +745 in-window / −1338 settled).
  **Score is a reason to investigate, never to trade.**

#### BLOCKERS before any of this is actionable
1. **MAKER FEES UNVERIFIED — hard blocker.** Only `KXTEMP*`, `KXAAAGASD`, `KXAAAGASW` are
   fee-verified $0 by prod read-back. A maker fee on a new series can swallow the entire reward.
2. **TOXICITY UNMEASURED** for every candidate. The reward side says nothing about whether the
   flow filling you is informed. This is what the mention trap is.
3. **STRUCTURE per series.** The event-aggregate throttle assumes additive "above X" ladders;
   categorical/mutually-exclusive series would sum anti-correlated strikes and mis-fire.
   ⚠ The classifier used here is a crude numeric-suffix heuristic and **mis-flags prefixed
   numerics** — `KXFUNDRAISING-…-A145000000` is really a threshold ladder. Verify per series
   by hand, do not trust the heuristic.
4. **n = 4 markets per series, one instant.** Extrapolating that to 70–86 programs is a big leap
   and is shown only as `extrap`. Treat per-market figures as the measurement and the series
   totals as indicative.

### §M6 — OVERNIGHT LIQUIDITY DROUGHT + a SELECTION BIAS in §M1/§M2 (2026-07-23 03:14Z)

Operator asked how two-sided quoting is doing and — correctly — flagged that volume is near zero
at this hour, so the ratio should be checked rather than the counts. That check found two things.

#### (a) The books, not the bot. ~79% of the allowlist is UNEARNABLE right now.

All 39 allowlist contracts with active programs, market-level test (does the BOOK reach Target
Size on each side, per R3 — nothing to do with our orders):

```
OK two-sided                 8 / 39   = 20.5%
a side is completely EMPTY  28 / 39   = 71.8%   <- the whole story
depth < Target(1000)         3 / 39

by event:  KXAAAGASD-26JUL23   2/24 =  8.3%
           KXAAAGASW-26JUL27   6/15 = 40.0%
```

Under R3 a contract whose book is one-sided is **excluded and pays nobody**, and per §M2 our 20 ct
cannot rescue a 1000-contract Target Size. So this is **environmental, not behavioural** — we
cannot earn two-sided credit in a contract where nobody is on the other side.

Our own quoting at the same moment: **1 two-sided / 2 one-sided of 3 quoted = 33%**, with
`breaker_reduce_only = 0` — **the plug-in is not even engaged**, so the guard is NOT the cause of
our one-sidedness tonight.

The A/B ON arm tracks this decay exactly, which is the point:

| time | ON-arm TWO-SIDED % | reduce-only % of cycles |
|---|---|---|
| 02:05Z (13 cycles) | 79.0% | 76.9% |
| 03:13Z (47 cycles) | **46.4%** | 27.7% |

⚠ **IMPLICATION FOR THE A/B (task #1).** The ON arm ran 01:38–04:38Z and the OFF arm starts after
04:38Z — both overnight, but across a **moving liquidity gradient**. Two-sided coverage is falling
for reasons that have nothing to do with the plug-in. Comparing the arms without normalising for
market-level two-sidedness repeats the confound that got the 07-22 live throttle A/B disowned.
**Normalise: measure our two-sided rate as a fraction of the contracts whose BOOKS were two-sided,
not as a fraction of contracts quoted.**

#### (b) ⚠ SELECTION BIAS in §M1/§M2 — self-caught, my defect

`kalshi_concentration_study.py:133` **skips** a contract when either side's book is empty
(`if not yl or not nl: continue`). Those contracts never entered the frozen dataset. So:

- **§M2's "86.1% two-sided" is conditional on both sides being non-empty** — a pre-filtered
  denominator. The unconditional rate is materially lower (20.5% at 03:14Z).
- **§M1's capture figures are computed only over non-empty books**, so they **overstate** what
  the same capital would earn across the real contract population.
- **§M2's core claim survives unchanged**: our 20 ct was marginal in 0/304. Excluded contracts
  are ones where we are even *less* able to be the marginal maker, so adding them can only
  strengthen that finding.
- **§M5 is NOT affected**: `kalshi_series_scan.py:116` returns `(0, 0, False)` for an empty side
  rather than dropping it, so those contracts are correctly counted as failures. That is exactly
  why §M5 could report the $12,112/day unearnable pool.

**Do not quote §M1/§M2 rates as unconditional.** Either re-sample without the filter, or state the
condition every time. Not silently re-run here — the frozen dataset is what the committed numbers
refer to, and replacing it would break reproducibility.

### §M7 — RECEIPT-GRADE REWARD DATA (operator screenshots, 2026-07-23). THE MODEL OVER-PREDICTS.

The operator supplied Kalshi web-UI transaction rows: **"Liquidity Incentive For Event <ticker>"**
with dated dollar amounts. This is **RECEIPT** evidence — actual credited money — and it is the
ground truth every reward figure in this lane has been approximating. It supersedes modelled
capture wherever the two disagree.

#### (a) Payouts are labelled PER EVENT but land PER CONTRACT
Multiple rows appear for the same event (`KXAAAGASD-26JUL23` × 4; `KXTEMPCHIH-26JUL2211` × 2).
That matches R4/R2: each contract's program pays separately; the UI just labels by event ticker.

#### (b) R2 CONFIRMED BY RECEIPT — the $1.00 threshold is real and binding
Smallest observed credits: **$1.01, $1.14, $1.33, $1.37, $1.42**. **Nothing below $1.00 appears.**
Contracts landing just above the threshold *do* pay. This is the first non-modelled confirmation.

#### (c) ⚠ TEMP IS THE EARNER — and my "temp is dark" framing was a SNAPSHOT ARTEFACT
Largest observed credits are all `KXTEMP*`: **NYCH $12.94**, AUSH $7.39, DCH/CHIH/NYCH in the
$1.4–3.2 band. Gas-daily contracts pay **$1.42–$3.75**.
§M4 recorded "all five KXTEMP* series have ZERO active programs — temp contributes nothing
today." That was TRUE AT 02:35–03:14Z and MISLEADING as a characterisation: temp programs are
**~1-hour and hourly-cycling**, so an instantaneous read between windows shows zero. **Do not
read a point-in-time program census as a statement about a series' earning power.**

#### (d) ⚠⚠ THE MODEL OVER-PREDICTS BY ROUGHLY 2–6× — treat §M1/§M4/§M5 as UPPER BOUNDS
For `KXAAAGASD-26JUL23`, §M4 predicted per-Time-Period payouts of **$22.10 / $16.81 / $9.43 /
$2.54** on its top four contracts. The visible receipts for that event are **$3.75 / $1.75 /
$2.57 / $2.02** (sum $10.09 vs $50.88 predicted, ~5×).

Why the model runs hot — all of these are real and none are in it:
- it assumes we rest at reference on BOTH sides for the WHOLE Time Period; live we are throttled,
  frequently one-sided, and periodically reduce-only;
- it scores an instantaneous book, while payout integrates over every snapshot in the period,
  including the overnight drought (§M6) when we earn nothing;
- competitors requote and dilute our share continuously.

**Consequence: every `$/day` figure in §M1, §M4 and §M5 is an UPPER BOUND, not a forecast.**
Relative ranking between series is far more trustworthy than the absolute level. Re-state them
that way; do not quote them as expected earnings.
⚠ Caveat on this comparison: the screenshots are a partial scroll, so more contracts may have
paid off-screen. The direction (model high) is solid; the exact multiple is indicative.

#### (e) NO INCENTIVE ENDPOINT EXISTS — probed and recorded, so nobody re-probes
READ-ONLY authenticated probe of ~25 candidate paths. **All 404 except:**
`/portfolio/settlements` · `/portfolio/fills` · `/portfolio/orders` · `/portfolio/positions`
(returns `event_positions` incl. `realized_pnl_dollars`) · `/portfolio/balance`.
404s included: `/portfolio/transfers|ledger|incentives|rewards|account_history|balance_history|
transactions|earnings|payouts|credits|activity|statements|incentive_earnings|
liquidity_incentives|rewards_history|incentive_history`, `/incentives`, `/incentive_payouts`,
`/incentive_programs/payouts`.
**The web UI is the ONLY source of receipt-grade reward data.** Operator screenshots are
therefore a first-class instrument, not an anecdote.

#### (f) `portfolio_value` FOUND — reconciles EXACTLY to the UI
`/portfolio/balance` → `{"balance": 7685, "balance_dollars": "76.8589", "portfolio_value": 2080}`.
**Both integers are CENTS.** cash $76.8589 + positions $20.80 = **$97.6589** = the UI's
"Portfolio $97.65". Exact.
⚠ UNIT TRAP, same family as R1: `balance`/`portfolio_value`/settlement `revenue`/`value` are
integer CENTS, while `*_dollars` fields are dollar strings. (Checked: `settlement_revenue()`
at `kalshi_attribution_ledger.py:137` already does `/100.0` correctly — the cents bug is NOT
the cause of the bad residual. Ruled out, not found.)
NOTE the quoter's loss meter uses `balance + held COST BASIS`
(`maker_kalshi_quoter.py:844-850`), deliberately, so settled positions don't read as losses.
That is a different quantity from the venue's market-value `portfolio_value`. Both defensible —
but do not treat them as the same number.

### §M8 — GROUND TRUTH FROM THE FULL TRANSACTION EXPORT. **THE ANSWER IS NARROW, NOT WIDEN.**

Operator supplied the complete Kalshi transaction export (`Kalshi-Transactions-2026.csv`, copied
to `kalshi_live/kalshi_transactions_2026-07-23.csv`). 254 rows: 244 `trade`, 10 `credit`.
This is **RECEIPT-GRADE and complete for its window** — it supersedes every modelled figure and
the entire `rewards_residual` apparatus.

#### WHOLE FILE (trades 07-20..22, credits 07-21..22)
```
trading P&L before fees   -77.4108
fees paid                  -2.5823
trading P&L after fees    -79.9931
LIP credits               +25.2100
------------------------------------
NET                       -54.7831      credits cover 0.32x of the bleed
```

#### ⚠ "THE BOT IS KILLING IT" — **NOT SUPPORTED. The bot is net negative.**
Per day (`close_timestamp`): 07-20 **−$44.13** (0 credits) · 07-21 **+$6.98** (the one positive
day) · 07-22 **−$17.64**. 07-20 predates the delta-neutral fixes and contains the documented
$21 go-live error, so it is not representative — but even excluding it the window is negative.

#### THE DECOMPOSITION THAT MATTERS — like-for-like 07-21..22 (both trades AND credits present)
Credits carry an **EMPTY** `market_ticker` in the CSV, so family attribution comes from the
operator's UI screenshots. **Cross-check: screenshot-attributed total $25.21 == CSV credit total
$25.21, exact.** That validates the attribution.

| family | trades | trading P&L | credits | **NET** | notional | **net % of notional** |
|---|---|---|---|---|---|---|
| **GAS** (`KXAAAGAS*`) | 99 | **+0.25** | +2.15 | **+2.40** | 214.85 | **+1.1%** ✅ |
| **TEMP** (`KXTEMP*`) | 60 | **−36.12** | +23.06 | **−13.06** | 142.67 | **−9.2%** ❌ |
| TOTAL | 159 | −35.86 | +25.21 | −10.65 | | rewards cover 0.70x |

**GAS IS PROFITABLE. TEMP IS NOT, AND TEMP IS THE ENTIRE LOSS.**
Temp earns the *biggest* credits ($23.06 of $25.21 = 91% of all reward income) and still loses
money, because its trading bleed is 2.6× its credit income.

Bleed efficiency over the whole file: GAS 130 trades, −$5.28 on $268.28 notional = **−1.97%**.
TEMP 114 trades, −$74.71 on $233.83 notional = **−31.95%**. **Temp bleeds ~16× worse per dollar
of notional.**

#### WHY — the worst trades are the adverse-selection signature, and they are all temp
```
-7.23  KXTEMPCHIH-26JUL2212-T69.99  yes  11.85 ct  0.61 -> 0.00
-7.20  KXTEMPLAXH-26JUL2212-T71.99  yes  15.00 ct  0.48 -> 0.00
-5.61  KXTEMPDCH-26JUL2123-T73.99   yes  11.00 ct  0.66 -> 0.15
-5.16  KXTEMPDCH-26JUL2021-T79.99   no   19.00 ct  0.29 -> 0.02
-4.70  KXTEMPCHIH-26JUL2123-T70.99  yes  10.00 ct  0.70 -> 0.23
```
Positions carried into resolution and expiring worthless. Hourly temp markets resolve fast against
one-way informed flow — **this is the FIGHTMENTION settlement-trap shape occurring inside our own
allowlist**, in the series the running tab §C ranks #1.

#### ⚠⚠ THIS INVERTS THE SECTOR HIERARCHY (running tab §C)
§C ranks `weather_temp` **#1 at 14.65 NET/cap/day, ~30× the next sector** — a MODEL estimate.
Receipts say temp is the only thing losing money and gas is the only thing making it.
**Where model and receipts disagree, receipts win.** §C's temp ranking is not safe to act on.

#### CONSEQUENCE FOR THE WIDENING QUESTION
The operator asked how to open scope. **The measured answer is the opposite: the first move is to
NARROW** — the profitable slice is gas, and temp is subtracting from it. Widening into more
series while a known-losing series stays enabled just adds variance on top of a negative base.
Sequence: fix/□drop temp → confirm gas-only is net positive over a real window → then widen.

**BLOCKED BY THE FREEZE — this is a PROPOSAL, not a change.** Dropping `KXTEMP*` from
`KALSHI_SERIES_ALLOW` is a Tier-2 trade-universe change and needs operator authorisation.

#### CAVEATS (do not drop these when quoting the above)
- **The export ENDS 07-22.** 07-23 is absent, yet the screenshots show 07-23 was a large credit
  day (~$42 visible, incl. a single $12.94). The trend may be materially better than this window.
  **Re-export after 07-23 closes before treating the verdict as final.**
- n = 159 trades over 2 days. Small, and it spans config changes (deposit, cap 65→85, naked-risk
  fix). Not a clean experiment.
- Credit attribution to family relies on screenshots; only the TOTAL is CSV-verified (exactly).
- Temp was quoted during the hours it was *available*; gas runs longer windows. Some of the
  difference is exposure profile, not pure edge.
- `-9.2%` and `+1.1%` are net-of-notional over one window, not annualised anything.

### §M9 — FEES CONFIRMED AGAINST OUR OWN RECEIPTS + the INCENTIVE-PROGRAM LANDSCAPE

#### Fee formula — VERIFIED, 67/67 fee-bearing rows match
Published formula (secondary sources; the official `kalshi.com/docs/kalshi-fee-schedule.pdf` is
**bot-blocked** — curl returns a JS challenge, not a PDF, so it is NOT quotable):
```
taker = ceil(0.07 * P * (1-P) * qty * 100) / 100      maker = 25% of taker      cap $0.035/contract
```
**Tested against `kalshi_transactions_2026-07-23.csv`: every one of the 67 fee-bearing rows
matches the taker-or-maker prediction within $0.02. Zero unmatched.** That is receipt-grade
confirmation of the formula, independent of the blocked PDF.

Fee reality for us: **175 of 244 trades (71.7%) paid ZERO fees**; total fees **$2.58**
(GAS $0.92 / TEMP $1.66) against a **$79.99** trading loss.
⚠ **Fees are NOT the problem — they are 3% of the bleed.** Adverse selection is (§M8).
⚠ But maker fees are **NOT universally zero** on Kalshi. Our series are exempt (verified by
read-back + these receipts). **Any widening candidate must have its fee status established
before admission** — a non-exempt series charges ~25% of taker on every maker fill.

#### The three OTHER incentive programs — one is a trap, one may be free money
| program | pays for | how to get it | verdict |
|---|---|---|---|
| **Liquidity Incentive (LIP)** | **resting quotes** | automatic, all members | what we run today |
| **Combo Incentive** | **maker VOLUME** of eligible trades, pro-rata | **must OPT IN by email** | ⭐ pays for FILLS — the thing we currently treat as pure cost. Periods ≥1 week. **We are presumably not opted in.** |
| **Liquidity Provider (DLP)** | designated MM in incentivised series | requires an executed **Market Maker Agreement** | ⚠ **TRAP — see below** |
| **Volume Incentive** | volume | not yet read | unassessed |

⚠⚠ **THE MARKET-MAKER-AGREEMENT TRAP.** The LIP rulebook (S1) excludes from Eligible
Participants: *"members who have executed a Market Maker Agreement with Kalshi"*. The DLP
program **requires** exactly that agreement. **They are therefore MUTUALLY EXCLUSIVE — signing an
MM agreement would DISQUALIFY us from the LIP income we currently earn.** Do not pursue DLP or
"become a market maker" status without modelling the LIP income being given up.
(The MM route also carries a **98% availability per hour** quoting obligation — far beyond our
current operating profile.)

**ACTION CANDIDATE (operator decision, free to ask):** opt into the **Combo Incentive Program**.
It rewards maker volume, which we generate anyway and currently book as pure cost. It is an
email opt-in, not a contract, so it does not appear to carry the MM-agreement disqualification —
**but confirm that with Kalshi before opting in**, since eligibility wording is what creates the
DLP trap above.

### §M10 — OFFICIAL FEE SCHEDULE OBTAINED. **MAKER FEES ARE ZERO BY DEFAULT.** ⚠ CORRECTS §M9

Source: `kalshi.com/docs/kalshi-fee-schedule.pdf`, **"Last updated and effective: July 7, 2026"**,
12 pages. Retrieved through the operator's authenticated browser session (the URL returns HTTP 429
to this environment's egress — it is NOT bot-blocked, just rate-limited by IP). Archived in-repo at
`docs/maker_handoffs/kalshi-fee-schedule-2026-07-07.pdf` so it never has to be re-fetched.

#### The formulas, verbatim
```
Trading (taker) fees = round up(M x 0.07   x C x P x (1-P))    M = multiplier, DEFAULT 1
Maker fees          = round up(M x 0.0175 x C x P x (1-P))    M = multiplier, DEFAULT 0
```
> "M = the multiplier for each contract (default is 1 unless otherwise indicated)" — taker
> "M = the multiplier for each contract (**default is 0** unless otherwise indicated)" — maker

**A default multiplier of ZERO means maker fees are NOT CHARGED exchange-wide unless a series is
explicitly listed.** Also verbatim: **"There is no settlement fee."** and **"There is no
membership fee."**

#### ⚠ THIS CORRECTS §M9. I had it backwards.
§M9 said *"maker fees are NOT universally zero on Kalshi. Our series are exempt."* That framing
is **wrong**. The truth is the inverse: **maker fees are zero by default and only 86 explicitly
listed series charge them.** Our series are not special — they are the norm. The ratio I quoted
(maker = 25% of taker) is arithmetically right (0.0175/0.07 = 0.25) but describes a rate that is
multiplied by zero for most of the exchange.

#### The "Non-Standard Fees" table — 86 series carry explicit multipliers
Checked programmatically against the extracted text:

| series | in table? | maker fee |
|---|---|---|
| `KXAAAGASD` `KXAAAGASW` `KXTEMPDCH/AUSH/LAXH/NYCH/CHIH` (**all 7 of ours**) | **no** | **ZERO** ✅ |
| `KXINTC` `KXPM` `KXRT` `KXFUNDRAISING` `KXCLAUDE` (**all §M5 candidates**) | **no** | **ZERO** ✅ |
| `KXEARNINGSMENTION*` `KXWNBAMENTION` `KXMLBMENTION` `KXLIUKELIMINATION` | no | ZERO |
| **`KXAAAGASM`** (monthly gas) | **YES** | **maker mult = 1 — CHARGES** ❌ |

Most listed series are **sports** (NFL/NBA/MLB/NHL/NCAA/PGA/UEFA all at maker 1) plus macro
(`KXCPI`, `KXFED`, `KXGDP`, `KXPAYROLLS`, `KXU3`) and awards (Emmys). A few carry 0/0.
**Sports is expensive for a maker — factor that into any future widening.**

#### CONSEQUENCES
1. **The "fees unverified" HARD BLOCKER on widening is DISSOLVED.** Every §M5 candidate is
   maker-fee-free. Fee status no longer gates admission — it is now a lookup against this table.
2. **`KXAAAGASM` earns a SECOND, INDEPENDENT rejection.** Running tab §H recommended it; §M4a
   withdrew it on the $/day unit error. It is *also* one of the ~86 series that charges maker
   fees. Two unrelated reasons to leave it out.
3. **Support-email question 2 is ANSWERED** — drop it, keep only the narrow ask about whether the
   exemption is permanent (the table is dated and versioned, so even that is low value).
4. Confirms §M9's receipt-side finding from the other direction: 71.7% of our trades paid zero
   fees because maker fills on unlisted series are free; the $2.58 we did pay was taker activity.

**Method note worth keeping:** the file was unreachable from this environment (HTTP 429 by IP) but
trivially available in a signed-in browser. When a primary source 429s, that is a *rate limit*, not
a wall — route through the operator's session rather than concluding the document is inaccessible.

### §M11 — KALSHI SUPPORT REPLY (AI-generated, 2026-07-23). 1 of 5 ANSWERED. ⚠ SEP-1 SUNSET CONFIRMED

The operator sent the §KALSHI_SUPPORT_EMAIL_DRAFT questions. The reply was **AI-generated** and is
**LOW AUTHORITY** — where it conflicts with the CFTC filing (S1) or the fee schedule PDF, the
primary sources win. Scored against what was actually asked:

| # | asked | reply | verdict |
|---|---|---|---|
| 1 | Does Combo opt-in affect **LIP eligibility**? Confirm in writing. | "The program rules **do not state** that opting in is treated as a Market Maker Agreement, and they **do not link it** to LIP eligibility." | ⚠ **NOT ANSWERED.** That is *absence of evidence*, not confirmation. It is mildly reassuring — silence is consistent with Combo ≠ MM Agreement — but it is not the written assurance requested. **Do not opt in on this basis alone.** |
| 2 | Which series are maker-fee exempt? | Pointed at the fee-schedule PDF. | **MOOT — we answered it ourselves** (§M10). Their reply adds one useful operational fact: fees are calculated **at execution** and surface on the **order object** as `maker_fees_dollars`. |
| 3 | Is there an API endpoint for incentive **payouts**? | "Incentive programs are visible through the `incentive_programs` endpoint. The API Technical FAQ does not list a separate endpoint for incentive or liquidity reward payouts." | ✅ **ANSWERED — no endpoint exists.** Corroborates our 112-path probe (§M7e). ⚠ But they **conflate two different things**: `incentive_programs` lists the *programs* (pools, target size, DF — we already consume it); it does **not** report what we were *paid*. |
| 3c | **Put the event ticker on CSV `credit` rows** | *(ignored entirely)* | ❌ **UNANSWERED.** This was the highest-value ask — without it, per-series reward attribution requires manual screenshot cross-referencing (which is what §M8 had to do). |
| 4 | Volume Incentive: eligible? opt-in? stacks with LIP? | Gave programme dates and "rewards trading volume in eligible markets". | ❌ **NONE of the three sub-questions answered.** |
| 5 | Confirm an MM Agreement makes an account **ineligible** for LIP | *(not addressed at all)* | ❌ **UNANSWERED.** The §M9 trap remains unconfirmed by the venue — though S1's own exclusion clause already states it. |

#### ⚠⚠ THE MOST IMPORTANT THING IN THE REPLY — a business-planning fact, not a support answer
> "This program runs from **September 15, 2025 through September 1, 2026**" — Volume Incentive

The LIP rulebook (S1) independently says the Program continues "until the earlier of
**September 1, 2026**, or the date that Kalshi amends or terminates the Program."

**BOTH incentive programmes carry the SAME September 1, 2026 expiry.** The entire reward basis —
which is the *only* reason this strategy is viable at all, since §M8 shows the trading side is
net negative — has a known common expiry date. This corroborates the "Sep-1 LIP sunset tripwire"
already parked in running tab §E and handoff §5 item 4, and **upgrades it from a single-programme
risk to a whole-revenue-basis risk**.

Operator ruling on record is "assume renewal; census = tripwire." That remains a *ruling*, not
evidence. Any plan whose payback period extends past 2026-09-01 must state this dependency.

### §M12 — A/B COMPLETE (2026-07-23 13:53Z). PLUG-IN CONFIRMED ON ITS OWN METRIC.

Timer fired on schedule; marker now `{"arm_on_start":"...01:38:21Z","arm_off_start":"...04:38:22Z"}`.

| arm | cycles | reduce-only % | **two-sided DURING reduce-only** | mean naked $ | mean committed $ | ladder viol. |
|---|---|---|---|---|---|---|
| **ON** | 89 | 14.6% | **66.1%** (56 mkt-cycles) | 13.13 | 57.20 | 0 |
| **OFF** | 247 | 40.5% | **0.0%** (127 mkt-cycles) | 15.97 | 39.23 | 0 |

**66.1% vs 0.0% during reduce-only.** Without the plug-in, reduce-only mode goes **completely
one-sided, every time**. With it, two-thirds of held markets keep both sides resting. The
mechanism is deterministic (the accumulating side is floored at `MIN_QUOTE_CT` rather than
pulled), so the gap is not a sampling artefact. **CONFIRMED on the coverage metric.**
Risk did not worsen: ON carried *lower* mean naked ($13.13 vs $15.97) on *more* committed capital.

⚠ **Overall two-sided (ON 40.8% vs OFF 43.8%) is NOT the comparison** — it mixes regimes, and the
arms spent very different fractions of time in reduce-only (14.6% vs 40.5%). Only the
during-reduce-only column is controlled.

#### ⚠ WHAT THIS DOES **NOT** ESTABLISH — and it is the whole economic question
Per **R3**, snapshot exclusion is **MARKET-level**. Our own one-sidedness does **not** zero us in
a market whose book is two-sided from other participants — and per **§M2** our 20 ct was never the
marginal maker (0/304, both sides). So the plug-in's real benefit is **recovering our own side's
normalised score** (up to half our snapshot score), **not** avoiding exclusion — which is a ~2×
effect, not a wipeout-vs-not effect, and it is bought with an extra fillable resting order.
**The A/B measures coverage. It cannot measure rewards per arm.** Task #7 (re-derive the EV)
remains the open question; a coverage win is not automatically a reward win.

⚠ **Time confound stands (§M6):** ON ran 01:38–04:38Z (deep overnight drought), OFF ran
04:38–13:53Z (into US morning). Different liquidity regimes, and OFF got 2.8× the cycles.

#### FREEZE INTEGRITY AT 13:53Z — HELD
- quoter md5 `727ca7c59840a42b51c19e24c65a0982` — **unchanged**, still == branch HEAD blob.
- `live.env` sha256 changed `8ebc0b76…` → `f192f033…`. **Diffed line-by-line: exactly ONE key
  moved, `KALSHI_REDUCE_ONLY_KEEP_BOTH` 1 → 0** — the pre-scheduled timer flip documented in the
  freeze file as the deliberate non-exception. Every other value identical. No STOP sentinel.
  **The freeze held; the single change was predicted and recorded in advance.**

### §M13 — ⚠⚠ CORRECTION TO §M8. MY FRAMING WAS WRONG ON TWO COUNTS (operator challenge, 2026-07-23)

Operator: *"most of the loss was you being a dumbass... when you stopped losing money on bugs and
fuck ups we killed it."* **Tested, and substantially CORRECT.** §M8's headline
(**"NET −$54.78, the bot is net negative"**) is withdrawn as a characterisation. The arithmetic was
right; the framing around it was not.

#### ERROR 1 — I pooled a one-time emergency unwind into a steady-state verdict
On our series maker fills are fee-free (§M10), so **any fee-bearing row is a TAKER trade** — a
forced exit, not the strategy. That gives a clean, objective split:

| | n | P&L |
|---|---|---|
| **TAKER-touched** | 69 | **−39.11** ← 49% of the entire loss |
| pure MAKER | 175 | −40.88 |
| of which: **07-20 20:3x mass IOC flatten** (operator-directed emergency unwind) | 45 | **−33.09** |

| date | n | ALL | maker-only | **taker-touched** |
|---|---|---|---|---|
| 2026-07-20 | 85 | −44.13 | −5.02 | **−39.11** |
| 2026-07-21 | 13 | −11.65 | −11.65 | **0.00** |
| 2026-07-22 | 146 | −24.22 | −24.22 | **0.00** |

**Every single taker trade in the entire export is on 07-20, and −$33.09 of it is one 4-minute
operator-directed IOC flatten.** Since `TAKER_FLATTEN=0` there have been **zero** taker trades.
07-20 also predates the delta-neutral rebuild and contains the documented $21 go-live error.
Carrying it into a headline "the bot is net negative" is exactly the kind of pooled-sample framing
the concentration rule (Protocol 14) exists to stop. **The operator's objection is correct.**

#### ERROR 2 — I matched trading days against credit days. CREDITS LAG BY A TIME PERIOD.
R1 says payout happens **once per Time Period**, at its end. The posting timestamps prove it:

```
credits posted 07-21T01:36/02:50  -> pay for KXAAAGASD-26JUL21 and KXTEMPAUSH-26JUL2021
credits posted 07-22T02:29/02:58  -> pay for KXTEMPDCH/CHIH-26JUL2123
```
Credits post **after the period closes**, not on the day the quoting happened. So §M8 compared
**3 days of trading against 2 days of credits** — and the operator's screenshots show a further
**~$42 posting on 07-23** that the export does not contain at all. That is a real accounting
error on my part, and it biases the result **pessimistic**.

⚠ **It also biases the GAS/TEMP split — against gas.** Period lengths differ enormously:
temp ≈1h (credits post within hours, fully captured), gas-daily 13.15h (captured), **gas-weekly
156.08h, ending 2026-07-27T03:59Z — NOT YET CLOSED, so every dollar of GASW quoting is
UNCREDITED in this export.** GASW shows 7 trades / −$0.36 with $0.00 of credit income purely
because its period has not ended.

#### WHAT STILL STANDS, AND WHAT DOES NOT
- ❌ **WITHDRAWN:** "the bot is net negative / not killing it" as a characterisation of current
  operation. It described a window dominated by a one-off emergency unwind and an incomplete
  credit ledger.
- ✅ **STANDS, and is actually STRENGTHENED:** temp bleeds far worse than gas. The lag bias runs
  *against* gas (gas-weekly credits missing entirely, temp credits fully posted), so the true
  gas-vs-temp gap is **wider** than §M8's +1.1% / −9.2%, not narrower.
- ✅ **STANDS:** the adverse-selection signature in temp (positions carried into resolution
  expiring worthless) — that is within-window and unaffected by credit timing.
- ⚠ **UNRESOLVED:** the post-fix steady state. Maker-only P&L by day is −5.02 / −11.65 / −24.22,
  but trade counts are 85 / 13 / 146 and the offsetting credits for 07-22 land on 07-23+. **The
  honest position is that the post-07-20 window cannot be scored until a CSV export containing
  07-23 (and ideally 07-27, when GASW's period closes) exists.**

#### THE MEASUREMENT THAT SETTLES IT
**Re-export the transaction CSV after 2026-07-27T04:00Z.** That is the first moment at which every
Time Period covering our 07-21→07-23 quoting has closed and been credited — gas-weekly included.
Until then, any net figure is a partial ledger, and I should not have presented one as a verdict.

### §M3 — code-vs-rulebook conformance check

| rule clause (S1) | implementation | verdict |
|---|---|---|
| ref = highest bid, must exist and be `< 1.00` | `qualifying_walk` `if not lv or lv[0][0] >= 1.0: return None` | ✅ |
| walk down, add **all** size at each price, stop at `>= Target Size` | `for price,size in lv: q.append(...); tot+=size; if tot>=target: return` | ✅ |
| if bids run out first, **clear** the qualifying set | `return None, [], 0.0` | ✅ |
| score = `DF^N × size`, N = ticks from reference | `w = df ** n_ticks; total += w*size` in `side_share` | ✅ |
| normalise by the sum of scores on that side | `our_score / total_score` | ✅ |
| user snapshot score = normalised yes + normalised no | `(ys + ns)`, payout `pool × (ys+ns)/2` | ✅ — across all users normalised-yes sums to 1.0 and normalised-no to 1.0, so the all-user snapshot total is 2.0 and our fraction is `(ys+ns)/2` |

The payout model was rulebook-correct before this session; only its **units** (R1) and the
**size model** were wrong.
