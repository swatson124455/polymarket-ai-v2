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
