# KALSHI — LIP SCORING + DCM RULEBOOK, read against our model (2026-07-25)

Sources, both read this session:
- Kalshi help, **Liquidity Incentive Program** (`help.kalshi.com/en/articles/13823851`), fetched
  2026-07-25. Screenshot-corroborated by the operator for the "Key Parameters (Set Per Time
  Period)" block.
- **Kalshi DCM Rulebook v.1.28** (`kalshi-public-docs.s3.amazonaws.com/.../Kalshi DCM Rulebook
  v.1.28 clean.docx.pdf`), extracted with `pdftotext -layout`, 3,540 lines.

---

## §1 — IS VOLUME IN THE SCORE? NO.

Two independent verbatim-only passes over the LIP article returned **NO-VOLUME-SENTENCES**: the
page contains no sentence mentioning volume, trade, traded, fill, filled, execute, executed,
transaction, or turnover. **Scoring is resting quoted depth only.** ESTABLISHED.

The scoring rules, verbatim:
> "Kalshi takes random snapshots every second during trading hours"
> "Only orders that help reach the Target Size qualify for points"
> "Your score for each snapshot = Order Size × Distance Multiplier"
> "Your final score = Sum of all your snapshot scores during the time period"
> reward = (Your Score ÷ Total All Participants' Scores) × Reward Pool

## §2 — WHAT WE *ARE* MISSING: TIME-IN-BOOK

**"Your final score = Sum of all your snapshot scores during the time period"**, over snapshots
taken **every second**. Score is an INTEGRAL over the window, not an instantaneous quantity.

Our capture model — both `_prospective_capture` (maker_kalshi_quoter.py:680) and `venue_scan.py` —
computes an **instantaneous** share and multiplies by the full daily pool. That implicitly assumes
we rest at that size and price for **100% of the window's snapshots**. Time-in-book is absent from
the model entirely.

This is a plausible mechanical driver of the documented "M7 over-predicts 2–6x" haircut
(HYPOTHESIS — the formula quote is ESTABLISHED, the causal attribution is not tested), and it
retro-explains the two weak signals in the closed-window study: paid events entered EARLIER in the
window (0.16 vs 0.36) and had a LONGER activity span (0.39 vs 0.19). Both are duration proxies —
exactly what a sum-over-snapshots score predicts, and neither is a behavioural mystery.

**Consequence for temp specifically:** temp windows are ~58 minutes. A 2-minute quote cycle is
**3.4%** of an hourly window vs **0.14%** of a daily one, so discovery latency and any dropped
cycle cost proportionally ~24x more in temp than in a daily market. Uptime is a first-class
variable in hourly programs and we have never measured it.

**Fix owed:** the per-market telemetry (`e01e188`) records our resting size per market per cycle —
integrating those rows over a window gives time-in-book directly. The model should multiply by
(our resting seconds / window seconds), and the venue scan's numbers should be read as an UPPER
BOUND assuming perfect uptime.

## §3 — THE $1.00 FLOOR IS DOCUMENTED, NOT AN ARTIFACT

> "Minimum payout: $1.00 (rounded down to nearest cent)"

The prior session RETRACTED "the $1.00 per-period floor is confirmed" as *not established* (zero
items below $1.00 across n=31, p≈0.14–0.37). That retraction was correct **as an inference from our
own sample**, but the floor is **stated by Kalshi as a program parameter**. Un-retract it on the
documentation, not on our 31 rows.

It is a **hard threshold, not a small number**: below $1.00 you are paid **$0** while carrying full
fill risk. Applied to the corrected venue scan, **only 16 of 30 scanned series (53%) clear $1.00**
on median modelled capture — and that is the optimistic, perfect-uptime number from §2. Some of the
12 zero-paying closed windows may be sub-$1.00 truncations rather than failures to qualify; those
are different diagnoses with different fixes, and we cannot currently tell them apart.

## §4 — PARAMETERS, MEASURED VENUE-WIDE (not assumed)

Over all **2,271 active liquidity programs** (single pull, 2026-07-25):
- `discount_factor_bps` = **5000 on 100.0%** → DF = 0.50 everywhere. Our `CAPTURE_DF_DEFAULT=0.5` is
  correct venue-wide. (The article's "Discount Factor: Up to 1.00" is the parameter RANGE, and the
  block is headed "Set Per Time Period" — so this must be re-checked, not assumed permanent.)
- `target_size_fp` = **1000 on 97.7%**, **300 on 2.3%** (52 programs). Article range is
  100–20,000.
- ⚠ **`target_size` (no `_fp`) is `None` on 100% of rows** — the documented `*_fp` footgun. My first
  `venue_scan.py` read the wrong key and silently defaulted every market to Target 1000. Fixed.
  Re-running changed several capture figures. **The live quoter is NOT affected** — it reads
  `target_size_fp` correctly and skips programs missing it (maker_kalshi_quoter.py:472, :498).
  (`KXEOWEEK`, the Target-300 niche, still scores $0.00 after the fix — R3 fails there regardless.)

## §5 — DCM RULEBOOK: CHAPTER 4 IS NOT US, AND THAT IS GOOD

**Chapter 4 "Market Maker" is a DESIGNATED STATUS, not a description of what we do.**
- Rule 4.1(d): *"A Member must complete and file a market maker agreement with Kalshi to be
  considered for Market Maker status."* 4.1(b): Kalshi has *"sole discretion"*.
- Rule 4.4(b) obligations include *"maintaining two-sided markets within a defined spread and with a
  minimum depth during trading."*

**We must NOT seek this designation:** the LIP article's ineligible list includes *market makers
with agreements*. Designated-MM status and LIP reward income are **mutually exclusive**. So Rule 4.4
quoting obligations do **not** bind us — we may withdraw quotes at any time, which is exactly the
freedom the wind-down relies on.

The flip side, Rule 4.5(b)/5.19(a): designated MMs are exempt from Position Limits. **We are not.**

**Rule 5.19(a)** — operationally the sharpest line in the chapter:
> *"any Participant entering bids or offers, if accepted, which would cause that Participant to
> exceed the applicable Position Limit shall be in violation of this rule."*

**RESTING ORDERS count toward the limit test, not just filled positions.** For a two-sided quoter
across many strikes the binding quantity is *resting notional + held position*, and "Position Limit"
is defined loss-denominated (*"the maximum loss that can be incurred"*), not contract-denominated.
Our caps are position-based (`INV_HARD_CT`, `HELD_MAX_USD`) plus a resting-book cap
(`MAX_TOTAL_CAPITAL`); per-contract limits live in each contract's Terms & Conditions and we have
**not** audited our allowlist against them. UNVERIFIED — worth a pass before any un-park.

Prohibited practices that bind an automated quoter (Chapter 5):
- **(bb)** no *"money pass"*, *"wash trade"* or *"front-running"*.
- **(aa)** fraudulent/abusive trading incl. *"violating bids or offers, demonstrating intentional or
  reckless disregard for the orderly execution of transactions during the closing period, or
  spoofing."* — directly relevant to any aggressive pre-close flatten. `TAKER_FLATTEN=0` today.
- **(b)** no non-competitive or prearranged trades.
- Messaging: participants must *"prevent excessive messaging or other activity that may be deemed
  detrimental or disruptive"* — our cancel/create churn is bounded by `KALSHI_WRITE_BUDGET=60`.

Because we quote both sides of the same market, wash/self-match is the live exposure. Existing
guards: the quoter refuses a book where `best_y + best_n >= 1.0` (maker_kalshi_quoter.py:745), all
creates are `post_only=True`, and the client sets `self_trade_prevention_type`. **Not audited
end-to-end against Rule (bb)** — UNVERIFIED.
