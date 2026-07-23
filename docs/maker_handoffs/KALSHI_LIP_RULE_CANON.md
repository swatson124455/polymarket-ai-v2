# KALSHI LIP — RULE CANON (primary sources, quoted verbatim)

**Created 2026-07-23 after the operator asked "how do we not know pool rules?"**

Honest answer to that question: every prior use of `period_reward` was a **ratio** (arm A vs
arm B on the same market), and a ratio cancels the period length. Nobody had to know the unit,
so nobody pinned it. It only became load-bearing the moment a **dollar** figure was quoted.
That is the failure mode — a quantity can sit unverified for weeks because the way it is used
happens to hide the gap.

**This file is now canon. Pull rules from here, not from memory, not from a handoff paraphrase.**

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

### §M5 — SECTORS WE ARE NOT IN: where could we eat, or get scraps? (2026-07-23 ~03:10Z)

`kalshi_live/kalshi_sector_scan.py` — top 40 series by pool $/day, **4 markets sampled each**,
scored at the deployed shape (join 20 ct/side, $15/market) with all four rulebook rules applied.
Output `kalshi_live/sector_scan.json`.

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
