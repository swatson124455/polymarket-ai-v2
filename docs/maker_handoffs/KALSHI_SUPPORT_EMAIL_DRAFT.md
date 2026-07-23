# DRAFT — email to Kalshi support (NOT SENT)

**Status: draft for operator review. Nothing has been sent.**
Fill the `[...]` placeholders before sending. Suggested recipient: `support@kalshi.com`
(the Combo Incentive Program help article names a dedicated opt-in address — use that one for
item 1 if it differs; the help centre is the authority on the current address).

Deliberately **not** included: our API private key, key ID, balances, positions, or strategy
detail. None of it is needed to answer these questions, and item 1 is an opt-in request, not a
disclosure.

---

**Subject:** Incentive program eligibility, fee exemptions, and API access — account [ACCOUNT EMAIL / ID]

Hello,

I run an automated liquidity-providing account on Kalshi and have five questions. Items 1 and 2
are the ones I most need answered; the rest are lower priority.

**1. Combo Incentive Program — opt-in request, and does it affect LIP eligibility?**

I would like to opt into the Combo Incentive Program for account [ACCOUNT EMAIL / ID].

Before opting in, could you confirm in writing: **does participating in the Combo Incentive
Program affect my eligibility for the Liquidity Incentive Program?** I ask because the LIP rules
exclude "members who have executed a Market Maker Agreement with Kalshi," and I want to be certain
that a Combo opt-in is not treated as the same category of arrangement. I currently earn LIP
rewards and do not want to jeopardise that.

**2. Maker fee exemptions — which series currently have zero maker fees?**

My fills on `KXTEMP*`, `KXAAAGASD` and `KXAAAGASW` come back with `maker_fees_dollars` of exactly
0.000000, so those series appear to be maker-fee-exempt. Could you confirm:

  a. Is there a published list of series or categories that are exempt from **maker** fees?
  b. Is that exemption permanent, or tied to a promotion with an end date?
  c. If I add a new series, is there a way to determine its maker-fee treatment **before**
     placing an order?

(Note: `https://kalshi.com/docs/kalshi-fee-schedule.pdf` is not retrievable programmatically —
it returns a JavaScript challenge rather than the document. A direct copy would be appreciated.)

**3. Is there an API endpoint for incentive/liquidity reward payouts?**

Liquidity Incentive credits are visible in the web UI and in the CSV transaction export, but I
cannot find them in the trading API. I have checked `/trade-api/v2/portfolio/settlements`,
`/fills`, `/orders`, `/positions` and `/balance` (all working), plus a wide range of candidate
paths for incentives, rewards, transfers, ledger and transaction history — all return 404.

  a. Is there an endpoint that returns incentive payouts programmatically?
  b. If not, is the CSV export the only machine-readable source?
  c. The CSV's `credit` rows have an empty `market_ticker`, while the web UI shows the event each
     credit belongs to. **Could the export include the event ticker on credit rows?** Without it,
     reward income cannot be attributed to a series without manual cross-referencing.

**4. Volume Incentive Program** — am I eligible, does it require a separate opt-in, and does it
stack with the Liquidity Incentive Program?

**5. Market Maker Agreement** — could you confirm my reading that executing one makes an account
**ineligible** for the Liquidity Incentive Program? I am trying to understand whether the
Designated Liquidity Provider route is additive to LIP or a replacement for it.

Thank you,
[NAME]
[ACCOUNT EMAIL / ID]

---

## Why each item is being asked (operator reference — do not send this section)

| # | why it matters | what it unblocks |
|---|---|---|
| 1 | Combo pays pro-rata on **maker volume** — the fills we currently book as pure cost. §M8 shows the trading side is where we leak, so a program paying for that volume is a genuinely different revenue axis. | New income on activity we already generate. The eligibility question is the guard against the DLP trap (§M9). |
| 2 | Fee status is the **hard blocker** on every widening candidate (§M5). A non-exempt series charges ~25% of taker on every maker fill. We verified the formula against our own receipts (67/67 rows), but not which series are exempt. | Lets a widening candidate be admitted or rejected without a live probe. |
| 3 | 112 API paths probed, all 404 (§M7e). Reward income is currently only observable via the UI or a manual CSV export, which is why the `rewards_residual` instrument was ever needed. **3(c) is the highest-value ask** — without the event ticker on credit rows, per-series reward attribution requires screenshots. | Automated, receipt-grade reward attribution — retires the broken residual method entirely. |
| 4 | Unassessed program; may be additive. | Completes the incentive-program picture. |
| 5 | Confirms the mutual-exclusivity reading in §M9 in writing, from the venue. | Prevents a future session pursuing "become a market maker" and silently forfeiting LIP income. |
