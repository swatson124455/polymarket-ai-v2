# Response to the Independent Theory & Logic Review — with site data

**All evidence below is live-probed from the production Kalshi API against the trading account,
or cited to file:line in the running build. Probe timestamp: 2026-07-22T18:05:59Z.**
The reviewer's own caveat — *"No source code was available"* — is the root of the disputed items:
every risk-side verdict was inferred from documentation, while the code and the account behave
differently. The reward-side findings, which rest on published rules, are correct and valuable.

---

## ACCEPTED — the reviewer is right, we were wrong

### A1. $1.00 minimum payout + tick-distance discount + target size (THE finding)
Verified verbatim from Kalshi's LIP rules:
> "Minimum payout: $1.00 (rounded down to nearest cent)"
> "Orders at the best bid/ask get full credit (1.0x multiplier)... Orders further away get reduced credit based on the Discount Factor"
> "Target Size: 100-20,000 contracts"

Live confirmation of the parameters on our own allowlist (probe, 18:05Z):

| series | active programs | pool | target_size_fp | discount_factor_bps |
|---|---|---|---|---|
| KXAAAGASD | 24 | $2,400 | 1000 | 5000 |
| KXAAAGASM | 54 | $5,400 | 1000 | 5000 |
| KXAAAGASW | 15 | $1,500 | 1000 | 5000 |

**Our shape is wrong for this formula, three ways:** we rest 8–20 contracts against a
**1000-contract target**; we spread across up to 40 markets (maximizing sub-$1 buckets that pay
**zero**); and our inventory throttle deliberately prices **1 tick inside** best, plus loss-capped
unwinds park **deep** — both directly taxed by the discount factor. Four prior audits missed this
because all four were pointed at correctness (does it do what it claims / can it lose money), never
at earning optimality. That framing failure is ours.

### A2. Margin-netting premise was fabricated
The "~4.3x ladder margin netting" in our running tab came from a prior session misreading a balance
drop that was actually the cash cost of executing fills. Retail Kalshi is fully collateralized.
The real mechanism is **collateral return on offsetting positions** — which strengthens, not
weakens, the ladder self-hedge work now on branch. Tab corrected.

### A3. Ledger ignores fees
Live fill payload keys include **`fee_cost`** (probe, 18:05Z); our attribution ledger's
`fill_cashflow()` never reads it, so fees silently land inside `rewards_residual`. Real defect in
our measurement instrument. Impact today is ~0 (all fills maker, fee-verified $0) but a taker fill
would corrupt the reward number. Fixing.

### A4. Also accepted, actionable
- **Scheduled-event blackouts** (EIA gas storage Thu 10:30 ET) — same class as our late-life gate; not implemented.
- **`reduce_only`** — we enforce no-overshoot in our own arithmetic; a venue-enforced flag is strictly better.
- **Rank by `incentive_programs` reward params, not `usd_day`** — agreed, and already the Stage-2 plan.
- **Small residuals should settle, not cross** — agreed in principle; note our INV_TOLERANCE=1 was set to make 1–2ct positions *visible*, which is a different (and still correct) goal than *crossing* them.

---

## DISPUTED — live data contradicts the verdict

### R1. "HIGH-1 pagination OVERSTATED — settled rows move to a separate historical endpoint, they do not clog page 1"
**Contradicted by the account.** Probe, 18:05:59Z, production, this account:

```
/portfolio/positions                                    -> 200  rows=6  nonzero=2
     ZERO-POSITION ROWS PRESENT: KXAAAGASD-26JUL23-4.105,
                                 KXAAAGASW-26JUL27-4.060, KXAAAGASW-26JUL27-4.100
/portfolio/positions?limit=1000                         -> 200  rows=6  nonzero=2
/portfolio/positions?limit=1000&settlement_status=unsettled -> 200  rows=6  nonzero=2   <-- still 4 zero rows
/portfolio/positions?limit=1000&count_filter=position   -> 200  rows=2  nonzero=2   <-- only this filters
```

**4 of 6 rows on page 1 are zero-position rows — 67% —** and passing `settlement_status=unsettled`
*explicitly* does not remove them. Only `count_filter=position` does. The stated mechanism
("settled rows don't clog page 1") is false on live data; the finding and the fix stand as written.

### R2. "HIGH-2 IOC OVERSTATED / mislocated — the danger is client-code, not the venue"
This agrees with the finding while scoring it wrong. The finding was **located in our client code**
and fixed there — `maker_kalshi_client.py:177-179`:
```python
ioc = time_in_force == "immediate_or_cancel"
fatal = set() if ioc else {"rejected", "canceled", "cancelled"}
if status in fatal:
```
It never claimed the venue errors. Related: **"the create response returns `order_id` (so 'id
discarded' is a fixable choice)"** — correct, and it was fixed before this review was written
(`maker_kalshi_quoter.py:1102`, `:1127`).

### R3. "Stage 0 BLOCKING: public sources show only daily temperature markets, not hourly — verify"
**Hourly markets exist and we trade them.** Public API, 18:05Z (`KXTEMPNYCH`, event code = DDHH):
```
KXTEMPNYCH-26JUL2215-T87.99 | open 2026-07-22T18:00:00Z | close 2026-07-22T19:00:00Z
KXTEMPNYCH-26JUL2215-T86.99 | open 2026-07-22T18:00:00Z | close 2026-07-22T19:00:00Z
```
One-hour open→close windows. Not a blocker.

### R4. "`post_only=true` ... not being used"
Used on **every** quote — `maker_kalshi_client.py:154,162,184,190`; call sites
`maker_kalshi_quoter.py:1095` (all cycle quotes) and `:1325` (STOP offsets). The single
non-post_only path is the deliberate last-resort IOC taker.

### R5. "Footprint ranking starves gas: CONDITIONAL — only matters if gas markets are actually reward-eligible"
**Gas is reward-eligible right now**: 93 active programs, **$9,300** of pools across
KXAAAGASD/W/M (probe above). Not conditional. (Note also: at this instant **zero** KXTEMP*
programs are active while gas has 93 — so a ranking that starves gas can idle the bot entirely,
which is exactly why the round-robin fix was made.)

### R6. "`client_order_id` is a server-side idempotency key (reuse on retry = safe) — conflicts with the prior 'never reuse coid' finding"
Not in conflict — different scenarios. Reuse on a **retry of the same intent** is safe (that is
idempotency working). Our finding was reuse across **distinct STOP invocations minutes apart**,
where idempotency is precisely the hazard: the second invocation's *new* offset gets deduped
against the *old, since-cancelled* order, so no fresh offset rests and the escalation path takes
over. The fix (a per-invocation nonce) preserves idempotency within an attempt.

---

## UNRESOLVED / OUR OWN OPEN ITEM
Our concurrent blind review of the (undeployed) ladder self-hedge found a defect neither review
caught: `_strike_of` took the last dash-field, so a **negative strike lost its sign** —
live-verified `KXCPI-26SEP-T-0.4` parsed as **+0.4**. Sign loss inverts strike ordering, which
would mark a genuinely unfloored combo as hedged and strip its guards. Live exposure is sub-zero-F
winter strikes on our Chicago series. Fixed (`5a43b0c`), pinned by test, not yet deployed.

---

## WHAT WE ARE DOING WITH THIS
The reward-side critique implies **concentrate** (fewer markets, larger size, quote at best,
sized to clear $1/market/period). That directly opposes risk guards built after a real
loss — spreading limits per-market exposure, and stepping off best is how accumulation is
throttled. It is a genuine trade, not a free win.

We built an attribution ledger (hourly, read-only) that decomposes every dollar into rewards vs
trading vs settlement, per series. Rather than reshape strategy on a document, we run the current
shape for a full day, then run a concentration experiment against it and let the receipts decide.
Receipts to date: **~$18.60 + $12.69 rewards credited** (operator-confirmed), which already proves
eligibility empirically.
