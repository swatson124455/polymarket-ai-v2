# POLYMARKET — "HOW THE PROS WORK" SHADOW STUDY (2026-08-01)

**Operator-ordered:** "we need factual ways the pros work review polymarket in shadowmode
surgically with no blastzone at all no changes etc" → then "review all data from all bots we
have as well as blockchain we can scrape."

**Method.** 13 agents, READ-ONLY: 5 recon (our DB · our selection code · Polymarket public API ·
on-chain feasibility · practitioner literature) → 4 extraction (gated on what recon proved
reachable) → 3 adversarial lenses (data-artifact · inference-error · applicability-to-us) →
1 synthesis. 1,268,713 subagent tokens, 382 tool calls, 0 agent errors.

**Blast radius: zero.** No writes outside scratchpad, no git mutation, no DB writes, no service
action, no transactions. Scope note: this is Polymarket/MirrorBot territory, not the Kalshi lane —
recorded here because the Kalshi session ran it under operator direction.

Labels: **ESTABLISHED** / **INFERRED** / **HYPOTHESIS**.

---

## 1. WHAT SURVIVED SCRUTINY

**H1 — Polymarket V2 is structurally pro-maker, and the public API hides makers by default.**
30-block decode (1,368 logs / 513 tx): takers paid a fee on **502 of 513** legs (Σ $236.46 on
$14,559 notional); makers on **7 of 855** (Σ $0.19 on $11,097). Separately, wallet
`0x00033f10…3b49` had 58 maker txs in that window; `data-api/trades` at its **default
`takerOnly=true` returned 0 of 58**. `takerOnly=false` returns 58/58 but carries **no maker/taker
field and no fee field**, and the set-difference workaround fails (the two modes span 168.2h vs
1.1h at equal pagination depth — non-nested). *ESTABLISHED; not independently re-verified.*
⇒ **Any picture of "the pros" built from the public trades API is a picture of aggressive flow only.**

**H2 — The largest actor in our own whale dataset is a low-margin volume machine, not a
directional whale.** `0x204f72f35326db932158CBA6AdfF0B9A1DA95e14` is simultaneously our top
contributor (1,280,795 lifetime rows, exact index count) and public rank-1 all-time PnL
"swisstony": **$1,752,941,836 volume / $22,604,336 PnL = 1.29% margin**, against an observed
median fill of $4.98. *INFERRED (arithmetic on two unverified figures).*
⇒ Scoring a market maker's entries against resolution is a category error — its per-fill
directional EV is designed to be ≈0.

**H3 — Apparent trader "skill" at the fill level is almost entirely an artifact of correlated
fills.** Correct unit is **(trader, market)**. Full population, no sampling: the top row flips
from row-level EV +0.2640 (**t = +87.2**, 28,649 fills) to clustered **−0.0068 (t = −0.09)**
across 42 markets. Per-trader t-stat dispersion collapses sd **3.247 → 1.253** (1.0 = pure noise).
*ESTABLISHED as a methodological result; all three lenses accept it; the applicability lens rates
it the strongest transferring claim in the corpus.*

**H4 — The genuine edge tail is tiny and asymmetric — but the metric is contaminated.**
Traders with ≥200 resolved markets (n=35): **5 with t>2, 0 with t<−2; 2 with t>3, 0 with t<−3**
(≈0.8/tail expected under null). *ESTABLISHED as measured.* **But** the underlying field is our
buy-and-hold copy counterfactual — `base_engine/data/database.py:3961-3963`:
`counterfactual_pnl(s) = notional × (resolution_payout − price)` — **not the trader's realized
P&L**, and the source table cannot observe exits. See D1.

**H5 — The public success ranking has three defects, one landing on our own selection input.**
`timePeriod=MONTH` returns the **DAY** dataset (50 wallets byte-identical `vol`; `pnl` differing
only at the 11th significant figure). *ESTABLISHED.* Our watchlist consumes exactly that
parameter: `bots/elite_watchlist.py:139` `"timePeriod": "MONTH"`, primary path; `:40`
`_LEADERBOARD_TIME_PERIODS = ["MONTH","ALL"]`; size 300 (`config/settings.py:406`).
⇒ **If the API defect is persistent, our elite watchlist is selected on one day of PnL,
refreshed 4×/day.** Sanity check: a month of swisstony's trading cannot be $396,881 when its
WEEK volume is $17,195,231.

---

## 2. DEMONSTRATED BEHAVIOURS

| # | Behaviour | Evidence | Grade |
|---|---|---|---|
| B1 | **Pros trade through smart-contract wallets, not EOAs.** Bytecode census of 501 sampled wallets: 45B EIP-1167 = 115, 124B Safe = 126, 125B proxy = 118, 146B Safe = 117, 23B = 11, **EOA = 14** | single 30-block window | ESTABLISHED for that sample; **not** generalised |
| B2 | **Makers pay ~nothing; takers pay materially.** Taker fee ≈ `fee/(size·min(p,1−p))` median **0.0455** (p10 0.0325, p90 0.0602, n=498) | dispersion is real | Asymmetry ESTABLISHED; rate INFERRED; "constant" REFUTED by its own author |
| B3 | **The durable thing is operating posture, not directional calls.** Top-50 overlap by PNL: DAY∩WEEK **13/50**, WEEK∩ALL **4/50**, DAY∩ALL **2/50**; by volume 21/50 and 14/50 | leaderboard reads | Overlaps ESTABLISHED; interpretation INFERRED |
| B4 | **Daily PnL is a resolution artifact.** 5 of the DAY/PNL top 10 show `vol = 0` alongside PnL of $25k–$98k | n=10 page | ESTABLISHED |
| B5 | **Volume rank is orthogonal to profit.** `suntori`: all-time **$666,403,202** volume, **−$7,285,017** PnL, rank 3,069,860 | — | ESTABLISHED |
| B6 | **Winner concentration is severe.** Among 1,050 enumerable winners (Σ $868,912,520): top 10 = 14.9%, top 100 = **48.2%**, top 500 = 82.6%; Gini **0.558 among winners only**. Ranks reach **3,069,860** ⇒ enumerable winners are the top **0.034%** | — | ESTABLISHED, winners-only denominator stated |
| B7 | **Incentive income exists and is invisible in the ranking.** All-time leaderboard PnL equals realized closed-position PnL to the dollar (Theo4 $22,053,934 = 22 positions; fishalive $9,063,378 = 2). fishalive separately received **$2,463 MAKER_REBATE + $3 REWARD**, excluded | n=2 wallets | Existence ESTABLISHED; **magnitude at scale UNVERIFIED** |
| B8 | **On-chain gives passive fills AND the counterparty, per fill.** Rule: `taker == 0xE111…996B` → taker leg, aggressor = the `maker` field; `taker == a wallet` → maker leg, that `taker` names who lifted them | 4 independent supports incl. `Trading.sol:131-143`/`:240-244`; 513/513 and 186/186 | ESTABLISHED, quadruply corroborated |
| B9 | **No entity caught operating two wallets** — method verified, result negative. 125B proxy owner = trailing 20 bytes of bytecode (verified exact). **244 of 501 resolved → 244 distinct EOAs → zero collisions** | 30-block scale | ESTABLISHED as a real negative at that scale |
| B10 | **In one active market ~half the participants are lifetime-profitable.** One Liga MX market, 10-min window, 66 wallets (65 resolved): **35/65 = 53.8%**; winners Σ $38,549,493, losers Σ −$13,056,329; median rank **334,740** (≈top 11%) | — | INFERRED / heavily biased — the source refuses to call it a population estimate |
| B11 | **A reward-earning maker's objective is inverted vs a classical maker.** Classical: fills are revenue (spread). Reward-earning: revenue is per qualifying contract-second of presence, so **every fill is a cost** (adverse selection + exit cost). Adverse-selection floor: Glosten & Milgrom (1985) *JFE* 14:71-100; Kyle (1985) | — | Logic ESTABLISHED; **all literature citations second-hand, byte-unverified** |

---

## 3. WHAT DIED IN VERIFICATION

- **D1 — Every level claim about a named trader's edge. REFUTED.** The metric is our
  buy-and-hold copy counterfactual (`database.py:3961-3963`), not the trader's P&L, and the
  source table is **BUY-only** — it structurally cannot observe exits. A trader who scalps out
  before an adverse resolution is scored as having held to zero. The **relative** finding (H3)
  survives; the **levels** do not.
- **D2 — "Half of observed whale activity is under $5." REFUTED twice.** (a) False on its own
  numbers: sub-$5 is ~49% of *trades* but ~$43k of $3,507,545 sampled notional = **1.2% of
  activity**. (b) Endogenous: the source table is *rejected-only* and the live $5 whale gate is
  what rejects sub-$5 trades — it measures the gate's output.
- **D3 — "Top 0.08% of trades carry 32.5% of notional" as ESTABLISHED. REFUTED.**
  `TABLESAMPLE SYSTEM` samples **pages, not rows**; one address holds ~6% of a time-ordered
  table, so effective independent n ≪ 41,258. The numerator is ~**33 sampled rows**. The single
  max, **$99,999.83**, is alone 2.9% of sampled notional and is almost certainly a $100k ceiling
  artifact nobody checked.
- **D4 — "The watchlist churns hard" (median tenure 4.7d, 36.9% <1 day). REFUTED as stated.**
  Tenure was measured as first→last *rejected signal*, not watchlist membership. Left/right
  censoring uncorrected. The 6.4%-in-24h figure is a stock/flow comparison (101-day cumulative
  denominator vs 1-day numerator).
- **D5 — The censoring table's specifics.** Substance survives (all three lenses call the scope
  caveat the most important sentence in that extract) but: **"26 call sites" is wrong** (docstring
  at `mirror_bot.py:2775` says 22; `grep -c` returns **23** — three numbers, none reconciled);
  the **BUY-only code cite does not support the mechanism** (`_is_sell` at `:2931` gates a
  price-floor block, not a global SELL exclusion; `:3195` is an explicit SELL branch); the
  **price band is understated** — `mirror_bot.py:2933-2948` rejects `price < 0.03 or price > 0.97`
  **and logs that rejection into the table**, contaminating every EV figure with near-deterministic
  resolutions; and a **fifth censoring layer is missing entirely** — `elite_watchlist.py:831-841`,
  ≥3 round-trips per (trader, market) → `return  # Skip wash traders entirely`, 48h expiry,
  *before* any rejection-logging site. An entire behavioural class is deleted upstream.
- **D6 — "On-chain analysis is structurally blocked." REFUTED.** The prior recon tested one
  endpoint with an archive wall plus a dead subgraph and generalised. `polygon.drpc.org` serves
  full V2 archive `eth_getLogs` free, 10,000-block range cap. Depth proof: `head−5,000,000` →
  n=469 logs; `head−10,000,000` → n=0 **with no error** (pre-deployment, not refusal).
- **D7 — The prior recon's maker/taker rule was INVERTED** (see B8). This is the one claim a
  maker study cannot afford to get wrong, and it was wrong in the source it was inherited from.
- **D8 — Three claims killed by their own authors** (the strongest quality signal in the corpus):
  (a) "51 of 60 makers are EOAs" — a batched-RPC null-coercion bug; true figure **58/60 contracts**.
  (b) "`0xa2dc0c7e…08040033` shared by 3 proxies" — that is Solidity CBOR metadata
  (`64736f6c6343` solc marker), not an address. (c) "`0xe51abdf8…` owns 14 proxies" — codesize
  24,170 bytes: a shared Safe *implementation*, not an owner.
  ⇒ **Any wallet-linkage claim not validated against codesize will manufacture false entities.**
- **D9 — `timePeriod=MONTH` as a usable window. DEAD.**
- **D10 — The `kluckkluck` anomaly is UNEXPLAINED.** Leaderboard DAY `vol = 0` while its own
  `/activity` shows 500 fills totalling $40,870 in the preceding 45 minutes. The obvious
  hypothesis (leaderboard `vol` = taker-only) is **falsified** by `ferrari`: 98.7% maker,
  DAY vol $883,280. No explanation offered.
- **D11 — Our own gate score does not discriminate.** `corr(mean gate_score, clustered EV)` =
  **+0.0363** across 48 traders (≥150 resolved, ≥50 gate rows); by bucket the relation is
  non-monotone and mildly *negative* at the top (0.45–0.50 → −0.0182; 0.50–0.52 → −0.0647).
  *ESTABLISHED as measured — but it inherits D1's metric problem, so read it as "the score does
  not predict the copy-to-resolution counterfactual," not "the score does not identify skill."*

---

## 4. WHAT WE CANNOT SEE

- **No lens independently re-ran a single query, API call, or RPC probe.** All three adversarial
  reviews were repo-read-only. Three of four extracts rest on scratchpad artifacts nobody opened.
  **Every DB, API and on-chain number in this document is unverified by a second party.** Only
  the code cites were independently checked.
- **Two of four evidence extracts arrived truncated.** One cuts off mid-section; the literature
  extract received **Part A only** — the Part B/C material on prediction-market-specific operators
  and reward/liquidity-program behaviour, i.e. the sections closest to our business, **never
  existed in this corpus**.
- **Where lifetime PnL crosses zero: UNREACHABLE.** `offset` caps at 1000 (1,050 wallets).
  Bracket only: rank 4,622 = +$36,883; rank 3,069,681 = −$833,051 — a 3.06-million-wide gap.
  **The profitable fraction of Polymarket traders is UNVERIFIED and no source would estimate it.**
- **Maker vs taker role is absent from the public API at any setting** — not hard, nonexistent.
  Keys were enumerated: nothing matching maker/taker/role/liquidity/fee.
- **23% of sampled wallets have unresolvable owners.** 115 of 501 are 45-byte EIP-1167 clones
  (impl `0x44e999d5…`), storage slots 0–4 all zero, no getter responds. Recoverable only from the
  factory's CREATE2 deploy logs — not attempted.
- **Funding-source linkage never tested.** Requires pUSD `Transfer` scans per wallet.
  Timing-correlation linkage not attempted (30 blocks is too short to mean anything).
- **NegRisk V2 (`0xe2222d27…`) was never sampled.** Its event shape is *assumed* identical to CTF
  Exchange V2 — unverified. Elections and tournaments route there: a real coverage hole.
- **All on-chain findings come from one 30-block window (~60 seconds), one time of day.**
- **All live public-API findings come from one 11-minute window, 03:00–03:11 UTC on a Friday** —
  in-play universe was Liga MX, MLB, ATP/WTA. Anything about *which markets* top traders trade is
  contaminated by hour-of-day. ~241 GETs, paced 0.3–0.4s, unauthenticated.
- **External traders' realized P&L is structurally unobservable in our own data.** BUY-only table,
  no exits; wash-flagged traders deleted before recording; and **36.31% of sampled whale trades
  sit in markets absent from our `markets` table entirely** — never decomposed.
- **The ~21.37M row count is `n_live_tup`**, a planner statistic that undercounts on an
  append-heavy table between autovacuums. True total is ≥ that.
- **Repo observation, no action taken or proposed:** `base_engine/data/blockchain_client.py` is
  V1-shaped — `:42 EXCHANGE_CONTRACT = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"` (V1) and
  `:70-71` decoding `makerAssetId`/`takerAssetId`, the two fields V2 replaced with `side` +
  `tokenId`; `:47` NegRisk is `0xC5d563A3…`. Against a V2 log this mis-parses. The execution path
  (`base_engine/execution/contract_manager.py:34`) is stated to carry V2 correctly, so the
  exposure is **analytics-only**. Reported, not recommended.

---

## 5. OPEN OPTIONS (no recommendation attached; none involves a write or a transaction)

| Option | Cost | What it settles |
|---|---|---|
| **O1 — Re-probe `timePeriod=MONTH` vs `DAY` at a different hour/day** | **2 unauthenticated GETs** | Whether the defect is persistent or transient — i.e. whether our watchlist window is 30 days or 1. Cheapest high-leverage check in the corpus. Settles H5 |
| O2 — Decontaminate and re-run per-trader clustered EV: exclude the 0.03–0.97 band; state whether wash-flagged traders were ever present | SELECT-only, index-bounded | Whether the 5-of-35 positive tail (H4) survives. Does **not** fix D1 |
| O3 — On-chain maker-leg census over a longer multi-hour window at `polygon.drpc.org` | Free; 10,000-block cap ≈ 5.5h chain per batch | Maker concentration; whether top public-PnL wallets are in fact passive; fee-rate distribution on a real sample; the counterparty graph (who lifts whom), which exists nowhere else |
| O4 — Sample-based bracketing of the profitable fraction | ~1 GET per wallet | A *biased* but bounded estimate. Bias direction known and must be stated. Does not settle the zero-crossing |
| O5 — Factory CREATE2 deploy-log scan for the 45B EIP-1167 cohort | one `eth_getLogs` sweep | The 23% owner-resolution gap; closes B9 from a 49%-resolved negative to near-complete |
| O6 — 30-block probe of NegRisk V2 log shape | one `eth_getLogs` | Whether V2 decode rules transfer to the contract our election/tournament flow routes through |
| O7 — Enumerate `/activity?type=MAKER_REBATE` for the top-50 all-time PnL wallets | ~50 GETs | Whether incentive income is material for the largest operators. Currently n=2 |
| O8 — Obtain Parts B/C of the literature extract; byte-verify primary citations | retrieval-dependent | Whether prediction-market- and reward-program-specific literature contradicts B11 |
| O9 — Count `wash_trader_flagged` log lines for `0x204f72…` | log grep | Resolves how one address accumulates 1,280,795 rows if the ≥3-round-trip filter is live. Likely answer: round-trip detection needs SELL events and the dataset has none — **HYPOTHESIS** |
