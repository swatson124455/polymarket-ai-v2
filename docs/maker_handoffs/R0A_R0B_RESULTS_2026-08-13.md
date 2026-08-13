# R0a + R0b RESULTS — money ladder rungs 0a/0b (2026-08-13, READ-ONLY studies)

Both rungs of the ratified ladder (roadmap §3) ran 2026-08-13 ~14:5x–15:0xZ. GET-only +
on-box tape reads; nothing placed, cancelled, or modified. Scripts archived in session
scratchpad; frozen outputs on box `/tmp/R0A_PAPER_BACKTEST.json` (md5 `2d3bcdbe`) and
`/tmp/R0B_VENUE_CENSUS.json` (md5 `6b0d96b8`) — copy to a durable path before box reboot.

## R0a — PAPER BACKTEST (filters applied retroactively to our own tape)

**Reads (UTC 2026-08-13 ~14:56Z):** 1,630 fills (full tape, cursor-exhausted), 204
settlements, 63 credit rows; market meta for all 227 tickers (0 missing). Method =
D1 canon (position-aware `replay_fills`, event = `rsplit('-',1)[0]`, credits event-level,
$15 referral excluded).

**Sanity anchor (ESTABLISHED):** full-tape lifetime net **−$390.73** = D1's −$391.67
(read 01:01Z) + the +$0.94 gas/rain settlement landed 13:05:44Z. Exact reconciliation.

**Filters as implemented** (mechanism proxies, stated limits):
- `close8d`: event's first fill ≤ 8d before market close.
- `announcement`: title contains a discrete-actor announcement verb (keyword proxy).
- `ladder_near_strike`: market has a strike AND contract-weighted mean fill price in
  (0.10, 0.90).
- `resolution_proximity`: >50% of contracts filled <1h to close.
- Eviction (2×-with-paid-ratio): simulable ONLY where the estimates tape exists
  (2026-08-06+); min/target SIZING is NOT retroactively simulable — stated, not faked.

**Result (ESTABLISHED, full account history 07-15→08-13):**

| Subset | Events | Net | Paid | Realized | Fills |
|---|---:|---:|---:|---:|---:|
| Full tape | 115 | −390.73 | +189.06 | −579.79 | 1,630 |
| **KEPT (passes all filters)** | **19** | **−79.59** | **+29.58** | **−109.17** | **231** |
| Dropped | 96 | −311.14 | +159.48 | −470.62 | 1,399 |

Dropped-by (overlapping buckets): ladder_near_strike 86 events / −$329.14;
resolution_proximity 35 / −$24.63 (note: paid +$81.16 — the weather/daily winners live
here too, incl. KXTEMPNYCH); announcement 7 / −$29.74; close8d_fail 3 / −$1.66.

**KEPT subset by ERA (first-fill date; ESTABLISHED):**

| Era | Events | Net | Paid | Realized |
|---|---:|---:|---:|---:|
| 07-25→07-31 (scaling/defect era) | 6 | −51.23 | 18.41 | −69.64 |
| 08-01→08-09 (governors era) | 7 | −34.21 | 3.66 | −37.87 |
| **08-10+ (fixed build)** | **1** | **−1.66** | 0.00 | −1.66 (still-open TOPMODEL-26AUG17, incl. open-inventory basis) |
| zero-fill credit events | 5 | **+7.51** | +7.51 | 0.00 |

**Classifier-fidelity caveat (IMPORTANT):** the keyword announcement proxy MISSED
KXTRUMPTIME (−$39.91 across 2 kept events; title "Will Trump post on Truth Social…" has
no announce-verb) and KXCHINAAI (−$6.11) — both D3-measured toxic (≤ −10 c/ct). With
D3's MEASURED toxicity classes applied instead of keywords, kept-set net ≈ −$33.6.
Either way the direction is unchanged.

**R0a verdict (per-rung question: "is the filtered subset clearly credit-negative?"):**
The filtered subset was historically credit-negative ($29.58 paid vs −$109.17 realized)
— BUT the losses concentrate in the defect eras (Rule 7 attribution applies: the same
eras carry the known agent-defect classes), and the fixed-build sample is 1 filled
event + 5 zero-fill credits (UNPOWERED). The tape contains ZERO examples of the R2
shape (full-target presence, ~0 fills) other than the 5 zero-fill credits — which were
pure-positive. **The tape does NOT answer the mandate; it does not refute the presence
thesis; it re-confirms fills-lose/presence-pays. R1/R2 remain the live test.**
Eviction sim (INFERRED, denominator = 22 tape-covered events): 2 evictions triggered,
~$5.97 net avoided — small because coverage starts 08-06.

## R0b — FILTERED VENUE CENSUS (all active liquidity programs)

**Reads (UTC 2026-08-13T14:59:54Z→~15:07Z):** 3,722 active liquidity programs (single
page limit=10000, cursor exhausted, no alarm); meta for all tickers (batched, 0
missing); orderbooks for all 232 survivors (0 errors; 0.6s spacing). Venue total pool
**$266,543.33/day** (was $250,003.33 at D2's 01:01Z read — the universe moves daily).

**Funnel:** 3,722 → not-open −20 → close>8d −3,020 → toxic (announcement 43,
ladder-near-strike 222, resolution-proximity-24h 243) → **232 survivors**.

**Share model:** replica of the quoter's `_qualifying_score` R4 walk (ref = best bid;
score = DF^N·size; book that can't reach Target pays NOBODY; share at ref =
size/(rival_q + size); R3 both sides must qualify; $/day = pool·(s_yes+s_no)/2). Bot
was DOWN with 0 resting (verified 14:49:59Z) so fetched books are rivals-only.

**Headline (ESTABLISHED as a MODEL output — M7 canon: the capture model over-predicts
2–6× as an absolute, treat as RELATIVE ranking + upper bound):**
- Addressable at TARGET size, all 232: **$10,825/day** (upper bound, see M7).
- Addressable at TARGET size, **top-12 (concurrency cap): $1,415/day**.
- Addressable at MIN size (10 ct), all 232: $306.52/day — and that number assumes the
  book already qualifies on rival depth; **sub-target books model $0 (W10 floor — R1's
  question)**. 195 of 232 survivors have at least one sub-target side.
- **vs the ~$500/day kill threshold: PASSES at target size even under a 3× M7 haircut
  on the top-12 ($1,415/3 ≈ $472 ≈ threshold; the full-232 bound gives wide margin —
  but capital, not concurrency, then binds).** The arithmetic-kill branch does NOT fire.

**Candidate quality flags (carry into R1/R2 selection):**
- KXTRUMPTIME strikes rank high in the census but are D3-MEASURED toxic (−10.0 c/ct,
  announcement mechanism) — the census keyword proxy missed "post on Truth Social".
  Selection must overlay D3 measured classes on top of census keywords.
- KXTOPMODEL-26AUG17-CLAUM is a RESIDUAL wind-down position (EXIT-ONLY pinned) — not
  a new-entry candidate; sibling strikes share its event.
- Best floor-probe shapes (sub-target quiet books, no measured-toxic history, ≤4d to
  close, $145–200/day pools): KXOPENSHARE / KXTOPUSAGEAI / KXDEEPSHARE weekly
  share-trackers — NOTE mechanism kinship with KXMLABELSHARE (D3 toxic −11.3 c/ct,
  data-release class): min-size probe fills there are themselves informative tape.
  Final selection happens at `plan` time on fresh books.

## What gates R1
R0a: no kill. R0b: no kill; addressable clears threshold at target size. R1 (floor
probe, ~$5–20, 48h) is GO-gated: standalone script built + reviewed this session
(`kalshi_live/r1_floor_probe.py`); FIRST live order is relight-class — operator
one-word GO required in-session before `place`.

# ============ EV ADVERSARIAL REVIEW CORRECTIONS (2026-08-13, per operator directive) ============
Operator directive: after each completed section, an adversarial review covering EV /
money-making soundness as well as logic/bugs. Three independent reviewers ran against
R0a, R0b, and the R1 probe design. Every decisive number below was RE-VERIFIED by this
session directly from the frozen JSONs before being written here. The original text
above is left intact for the record; where this section contradicts it, THIS section
governs.

## R0a corrections (all ESTABLISHED from the frozen R0A JSON unless labeled)

1. **Provenance fix:** the reads line above says "204 settlements, 63 credit rows";
   the frozen JSON says **208 settlements, 62 credit rows**. The nets reconcile; the
   frozen JSON governs.
2. **The filters kept the WORSE subset on unit economics.** KEPT realized/fill
   -$109.17/231 = -$0.47/fill vs DROPPED -$470.62/1,399 = -$0.34/fill; credit-coverage
   (paid/|realized|) KEPT 0.27 vs DROPPED 0.34. Mechanically: the resolution-proximity
   drop removes the credit-richest slice on the tape (paid +$81.16). The retro-applied
   filters show NEGATIVE measured value-add on this tape.
3. **Look-ahead bias in close8d:** the filter used TODAY'S close_time; early-determined
   markets (KXCLAYTONDNI-27JAN01, kept, -$14.83; also BLANCHEWITHDRAW/SENATEADJOURN
   signatures) pass retroactively BECAUSE the announcement fired -- the live policy
   could never have entered them. The kept -$79.59 contains events the policy could
   not hold.
4. **The +$7.51 zero-fill credit events bypass every filter by construction** (no
   meta fetched for zero-fill tickers -> no filter evaluable -> kept-by-default) and
   are a survivor-only sample: presence with zero fills AND zero credits is invisible
   to this tape. They are NOT evidence of presence yield.
5. **The ladder_near_strike bucket (86 events / -$329.14) is outcome-conditioned**
   (fires on OUR realized fill prices, not a prospective market-state rule); the
   KEPT/DROPPED partition is an ex-post construct, not a backtest of the deployable
   v2.0 policy.
6. **Eviction sim retracted as a quotable number:** the paid-ratio is structurally 0
   for any not-yet-paid series (canon: credits pay at conclusion) -> hair-trigger
   (KXRAIN-26AUG07 evicted on ratio 0.0); paid_forgone was initialized 0 and never
   computed; the $5.97 "avoided" is gross cash flow on a still-open market. Do not
   cite it.
7. **VERDICT RESTATED:** the pre-registered rung question -- "is the filtered subset
   clearly credit-negative?" -- measured **YES** ($29.58 paid vs -$109.17 realized;
   negative in every era slice with fills, including the governors era -$34.21/7
   events). The original "no kill" was an OVERRIDE of that measurement on two grounds:
   (a) defect-era attribution (Rule 7 applies to the eras, but is partially circular
   here), (b) the tape contains zero R2-shaped observations. The honest statement:
   **the tape is uninformative for the R2 concept and mildly adverse for the v2.0
   filter set as retro-proxied; the override is the operator's call, not a pass.**
   The fixed-build slice (-$1.66) is 1 open event -- no evidentiary weight.

## R0b corrections (decisive numbers re-verified from the frozen R0B JSON this session)

1. **Capital-conditioned headline (the decisive correction).** Top-12 at target needs
   **$10,600 collateral**; all-232 needs **$215,900**. Deployable = $252.53 on venue +
   $3,000 contingent = **$3,252.53**. Greedy-filling the census ranking under that
   budget buys **3 markets / $3,000 / $385.65 per day modeled -- BEFORE the M7
   haircut**; after M7 (2-6x, and see caveats below on whether it even transfers):
   ~**$64-$193/day vs the ~$500/day threshold**. Under $252.53 alone: zero markets fit.
2. **"PASSES at 3x" retracted:** $1,415.04/3 = **$471.68 < $500**. That sentence
   rounded a fail into a pass. The M7 range puts the top-12 at $236-$708/day -- a
   straddle whose central estimate is below threshold.
3. **Empty-side model divergence:** the census gives a side with an EMPTY book share
   1.0 at target; the quoter's _qualifying_score (the model M7 was calibrated on)
   scores an empty side as DISQUALIFYING the market ($0). 127/232 candidates are
   anchor_needed; their $/day sum is $5,615.59, of which ~$4,240.00 is the empty-side
   half-pool credit. The census is therefore a strictly-more-optimistic model than the
   one the M7 haircut was measured on.
4. **Qualification at target size is vacuously true** ((book+our_size) >= target with
   our_size = target) -- the census EMBEDS the optimistic answer to R1's untested
   question (does our resting depth count toward Target / does a sub-target book pay).
   If R1 confirms the floor, the target-size headline does not survive either.
5. **W10 4a extreme-price signature unpriced:** 190/232 candidates ($8,062.41/day of
   the $10,825 headline) sit at min(mid, 1-mid) < $0.05 -- the band W10 measured being
   credited $0.00 (two events, INFERRED mechanism). No W12 shape discount was applied.
6. **Top-12 contamination:** KXTRUMPTIME-H2/H5 ($222.29 -- D3-measured toxic, keyword
   miss), KXTOPMODEL-CLAUM ($115.76 -- residual EXIT-ONLY), KXTOPUSAGEAI-ANTH ($105.76
   -- empty-side artifact) were still in the headline sum. Re-ranked after exclusions
   the sum barely moves ($1,342.89) because near-identical siblings slot in -- and the
   corrected top-12 is ~10/12 weekly AI-share trackers, one data-release mechanism
   family (kin to KXMLABELSHARE, D3 -11.3 c/ct), with ZERO fill-cost debit modeled.
7. **Endogeneity + lifetime:** shares of 60-97% assume rivals do not respond to a
   1,000-lot at reference; the universe moved 3,591->3,722 programs / $250,003->
   $266,543 in ~14h; top-5 close in 3.54d -- "$/day" requires continuously re-winning
   a refreshing universe. Not haircut-able by M7 (measured at ~100ct footprints).
8. **Funnel presentation fix:** drop counters tally once per REASON; 450 markets were
   dropped for 508 reason-tallies. (Also: capital_target_usd == target size in
   dollars; the mid terms cancel.)
9. **VERDICT RESTATED:** the original "the arithmetic-kill branch does NOT fire" is
   RETRACTED. Honest statement: **the model UPPER BOUND does not exclude viability;
   the model's central estimate at deployable capital is below the $500/day threshold;
   R1/R2 are the discriminating evidence.** Whether the kill branch fires now, or
   R1/R2 proceed as the deciders, is an OPERATOR DECISION (options in the session
   report).

## R1 probe design -- EV review disposition (code fixes shipped separately)

- Loss framing: $15.52 is COLLATERAL; the true one-sided adverse tail on the staged
  plan is **~$8.64** (max(y,n) x 8ct summed); both-sides-fill is the good outcome
  (+$0.08/+$0.40 guaranteed per pair). Expected fill cost over 48h: INFERRED
  ~$1.5-3.6 (D3 -8.7 to -11.3 c/ct classes x partial fill assumption) -- cheap for
  the information.
- Instrument checks (venue reads 2026-08-13T23:05:38Z): the estimates tape DOES emit
  zero-value rows (10,836 of 91,991 rows across 2,234 snapshots) -> "program present
  with delta 0" is observable and distinguishable from absence. DEEP/TENC map to two
  DISTINCT per-market programs (ids 6c533124..., 47dab17d...), each period_reward
  1,450,000 = $145/day. No minimum-qualifying-size field exists in the program
  payload (target_size_fp 1000 only).
- **NEW FACT: program end_date 2026-08-16T03:59:59Z != market close 08-17T03:59Z.**
  The probe window must sit inside the PROGRAM window. Code now enforces
  min(close, program_end) >= 49h at plan time -> the staged candidates remain
  placeable only until ~2026-08-14T03:00Z; after that, re-plan (new candidates).
- PRE-REGISTERED VERDICT RULES (binding for the R1 read):
  * CONFIRMED (floor holds): both programs PRESENT in the feed with accrual delta ~0
    at +24h AND +48h, accrual_basis DELTA_OK throughout.
  * VOID: programs absent from snapshots (PARTIAL/VOID basis), feed stale, or
    plumbing alarm -- a zero here is NOT a floor confirmation.
  * REFUTED (provisional): delta trajectory >= ~$1/event by program end -- final
    verdict ONLY after the credit_history read post-conclusion (payment envelope
    ~38-48h after 08-16T04:00Z -> read ~08-18). A marginal delta ($0.10-0.90 total)
    is an ANOMALY: payment check required, no verdict from the feed alone.
  * Expected magnitude if the floor is FALSE (census min-size model, INFERRED):
    ~$10-20/day on DEEP's yes-side share -- far above feed precision; tiny positive
    deltas are suspicious, not confirmatory.
- Code fixes shipped from the review: window-basis equity halt gauge (day-blind
  mark-loss hole closed); resting-order refusal in plan (spec'd but unimplemented);
  program-end-aware entry window. Replication caveat stays: n=2 markets, one event,
  one data release -- this is n=1 experiment and will be labeled so in the verdict.
