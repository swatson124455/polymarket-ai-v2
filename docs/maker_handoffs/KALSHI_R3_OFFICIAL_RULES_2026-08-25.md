# R3 COMPLETE — THE OFFICIAL LIP RULES, VERBATIM (2026-08-25, web research)

Operator: "research proper answers — is there no place on the internet with the proper
rewards rules?" **There is. Three public places.** The repo had partial quotes since
07-22/07-30; the FULL primary text was never brought into the repo until now.

## 1. The public sources
1. **CFTC filing (THE primary document, full algorithm in Appendix A)**:
   https://www.cftc.gov/sites/default/files/filings/orgrules/26/02/rules02112639183.pdf
   — "KalshiEX LLC – Amendment to August 2025 Liquidity Incentive Program",
   filed 2026-02-11, effective 2026-02-28. Read IN FULL this session (PDF fetched
   ~14:2xZ; local copy in session tool-results).
2. **Kalshi help center**: article 13823851 "Liquidity Incentive Program" (plain-
   English version; one discrepancy vs the filing, §4 below), plus 16076644
   "where to find them" and 15410219 (the SEPARATE Market-Maker "Liquidity
   Provider Program" — MMA members are EXCLUDED from our LIP; its terms are
   sealed/irrelevant to us).
3. **kalshi.com/incentives** + per-market pages (the live schedule: Target/DF/pool
   per Time Period) — which is what `/trade-api/v2/incentive_programs` serves.

## 2. The algorithm, verbatim (filing Appendix A, effective 2026-02-28)
- **Snapshots**: once per second, random instant, times nonpublic. "Snapshots will
  be excluded if there is not two-sided liquidity (i.e., resting orders sufficient
  to meet the Target Size on each side of the market) at the time of the Snapshot."
- **Qualifying set per side**: Reference Price = the HIGHEST bid ("If the highest
  yes bid price exists and is less than the highest possible price, it is assigned
  to the Reference Yes Price"). Walk down accumulating ALL size per level until
  cum ≥ Target; stop. "If no more bids exist, Kalshi will clear the Qualifying Yes
  Bids, as there were not enough bids to reach the Target Size."
- **⚠ MAX-PRICE RULE**: "if the highest yes bid price does not exist **or is not
  less than the highest possible price**, there are also no Qualifying Yes Bids."
  → a side whose best bid sits at $0.99 (max on the 1c grid) has NO qualifying
  bids → snapshot excluded. 0.99-touch books pay $0 to EVERYONE.
- **Score**: `DF^(ticks below ref) × size`, **normalized pro-rata over all
  qualifying bids on the side** — NO time priority within a level; a co-priced
  wall dilutes, never zeroes. (A yes ask counts as a no bid.)
- **Period**: user's period score = Σ own snapshot scores ÷ Σ all users' snapshot
  scores; payout = period score × Time Period Reward, **≥ $1.00 or nothing**,
  rounded down to the cent.
- **Parameter ranges**: Time Period ≤ 31d; Target ∈ (100, 20,000); DF ≤ 1.00;
  reward $10–$1,000 per calendar day.

## 3. What this settles (against our measurements — all now rule-grounded)
- **Gas zero**: NO side of 3.900 cum ≈ 49ct < Target 1000 across ALL levels (own
  telemetry walk) → every snapshot excluded → $0 for everyone. CONFIRMED.
- **Wall/time-priority hypothesis (R2 doc)**: REFUTED by the filing — pro-rata.
  On a qualifying book our 40ct behind a 1,020ct wall earns ~3.8% of that side.
- **R1-probe "conflict" DISSOLVED in principle**: qualification is CUMULATIVE
  ACROSS ALL LEVELS, so "thin at touch" books can still reach Target deep. The
  probe-era "far below Target both sides" characterization needs re-measuring as
  full-depth cum (archived log check remains as confirmation, no longer blocking).
- **0.99-touch canon**: rule-grounded now (max-price rule) — those books are
  excluded for everyone, independent of the anchor-grid argument.
- **Capture-gate arming (13:53Z)**: direction validated — `_prospective_capture`'s
  both-sides-qualify zero IS the venue rule. The void-bypass gap stands (§4b of
  the capture-arm doc); note the filing also grounds the "activate fully to
  Target" design: supplying enough depth to reach Target cumulatively makes the
  snapshot qualify with us holding most of the qualifying score.

## 4. Discrepancies + R4 items (reported, NO action taken)
1. **Replica max-price defect**: `_qualifying_score` excludes only `bids[0] >= 1.0`
   (:2667) — the venue excludes at best-bid = highest possible PRICE (0.99). The
   quoter models credit on 0.99-touch sides the venue scores $0. Affects capture/
   MIN_CREDIT admission on 0.99-touch books.
2. **Help article vs filing on Reference Price**: article says ref = walk to
   Target/5; filing says ref = best bid. Different program iterations or a
   simplification — verify empirically (est-feed join) before touching the replica.
3. **Excluded-snapshot payout scaling**: article says reward is scaled by the
   non-excluded share; filing formula normalizes over users with no such scaling.
   Same empirical test decides.
4. **Program continuity past the filed 2026-09-01 sunset**: venue feed (15:24:23Z
   paged read, first 1000 active rows — the endpoint truncates at limit) shows
   **211 programs ending after 09-01** (through 09-15+). The LIP operationally
   continues; the renewal/extension filing should be located at
   kalshi.com/regulatory/notices when convenient. (Context: Volume Incentive
   Program refiled 08-04→Sept 2027; CFTC DMO asked all DCMs to amend incentive
   filings by 09-14 — commentary source, secondary.)

## 5. Sources
- CFTC filing PDF (primary, read in full): rules02112639183.pdf (link §1)
- https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program
- https://help.kalshi.com/en/articles/15410219-liquidity-provider-program
- https://kalshi.com/incentives · kalshi.com/regulatory/notices
- Venue reads: incentive_programs 13:48:09Z + 15:24:23Z; own quotes-tape telemetry.
