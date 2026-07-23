# DRAFT 2 — follow-up to Kalshi support (NOT SENT)

**Status: draft for operator review. Nothing has been sent.**
Context: the first reply (§M11) was AI-generated and answered **1 of 5** questions. Question 2 is
withdrawn — we resolved it ourselves from the fee schedule PDF (§M10). This follow-up re-asks the
four that were dodged or ignored, and adds the one that matters most commercially.

Tone note: firm but easy to answer. Every ask is phrased as a **yes/no or a date**, so an agent
cannot satisfy it by restating documentation. It also opens by asking for a human — the first
reply's failure mode was quoting the rules back rather than answering.

---

**Subject:** Re: Incentive program eligibility — follow-up, 4 questions still open

Hello,

Thank you for the reply. Four of my five questions are still open, and I think three of them need
a human rather than a documentation lookup — in each case I am asking you to **confirm a fact
about my account**, not to tell me what the published rules say. I have already read the rules;
what I need is confirmation.

Question 2 (maker fee exemptions) is resolved — I found it in the fee schedule. Thank you.

**1. Combo Incentive Program — I need a yes or no.**

Your reply said the program rules "do not state" that a Combo opt-in is treated as a Market Maker
Agreement, and "do not link it" to LIP eligibility. I understand the rules are silent. **My
question is whether the outcome is silent too.**

> **If I opt into the Combo Incentive Program, will my account continue to earn Liquidity
> Incentive Program rewards? Yes or no.**

I am not willing to opt in on the basis that the documentation omits the risk. If you can confirm
yes, please opt account [ACCOUNT EMAIL / ID] in at the same time.

**2. Market Maker Agreement and LIP — this was not addressed.**

> **Does executing a Market Maker Agreement with Kalshi make an account ineligible for the
> Liquidity Incentive Program? Yes or no.**

The LIP rules exclude "members who have executed a Market Maker Agreement with Kalshi," which
reads as yes — but the Designated Liquidity Provider program requires exactly that agreement, so I
want to be certain the two are alternatives rather than additive before considering either.

**3. Volume Incentive Program — three specific questions.**

You gave me the programme dates, but not the answers:

  a. **Is account [ACCOUNT EMAIL / ID] currently eligible? Yes or no.**
  b. **Does it require a separate opt-in? If yes, please treat this as the opt-in request.**
  c. **Does it pay in addition to the Liquidity Incentive Program, or instead of it?**

**4. Reward payouts are not attributable to a market — a small data request.**

You confirmed there is no API endpoint for incentive payouts, which is fine. The remaining problem
is in the CSV transaction export: **`credit` rows have an empty `market_ticker` field**, while the
web UI clearly knows which event each credit came from (it displays "Liquidity Incentive For Event
[TICKER]" on every row).

> **Could the CSV export populate `market_ticker` (or add an `event_ticker` column) on `credit`
> rows?**

Without it there is no way to tell which markets my reward income actually came from except by
reading them off the screen one at a time. The data plainly exists on your side — it is displayed
in the UI — it is just dropped from the export.

**5. September 1, 2026 — the question behind all the others.**

Your reply gives the Volume Incentive Program as running "through September 1, 2026." The
Liquidity Incentive Program rules give the same end date. So both incentive programmes appear to
expire on the same day, roughly six weeks from now.

  a. **Is September 1, 2026 the intended end of these programmes, or a renewal date?**
  b. **If they are renewed or replaced, how much advance notice will members receive?**
  c. **Is there any published successor programme?**

I ask because liquidity provision is only economically viable for me while these incentives exist,
and I am making capital-allocation decisions with a six-week horizon against an unknown.

If any of the above cannot be answered by support, I would appreciate being pointed to the right
team rather than to the published rules.

Thank you,
[NAME]
[ACCOUNT EMAIL / ID]

---

## Operator reference — do not send

| ask | why it is phrased this way | what it unblocks |
|---|---|---|
| 1 | The first reply answered "the rules don't say." That is absence of evidence. Forcing a yes/no makes silence a visible refusal rather than a passable answer. | Combo opt-in — new income on maker volume we already generate (§M9). |
| 2 | Ignored entirely first time. S1's exclusion clause already implies yes; venue confirmation stops a future session pursuing DLP and silently forfeiting LIP. | Closes the §M9 trap. |
| 3 | All three sub-questions were skipped in favour of programme dates. (b) is written so a "yes" doubles as the opt-in. | Completes the incentive-programme picture. |
| 4 | Ignored entirely. **Highest operational value.** §M8 needed a full CSV export *plus* manual screenshot cross-referencing to attribute rewards by series — because this one field is blank. | Automated per-series reward attribution; retires `rewards_residual` for good. |
| 5 | **New, and the most important.** Their own reply surfaced that Volume Incentive and LIP share a 2026-09-01 expiry. §M8 shows the trading side is net negative, so rewards are the entire economic basis. A common expiry ~6 weeks out is a whole-strategy risk, not a programme detail. | Whether there is a runway at all. Feeds the Sep-1 tripwire in running tab §E. |
