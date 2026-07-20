# MAKER NUMBERS LEDGER — exact definitions + quote rules

Written 2026-07-20 after the same data was presented on shifting bases across a
session and trust in the numbers collapsed. This is the definition of record for
every Maker quantity. It does NOT present current values — it defines what each
number *is*, where it comes from, how trustworthy it is, and whether it may be
quoted at all.

## THE RULE (operator directive 2026-07-20)

**No derived EV / return is presented until a real reward RECEIPT anchors it.**
A "receipt" = an on-chain reward payment to our own wallet, read back and
reconciled. None exists yet. Until one does, every return figure below is a
MODEL estimate, and the trading-inclusive ones are not to be quoted at all.

The failure this prevents: a trading term that swings ±thousands on a 3-day
window was folded into headline "EV" points and re-quoted on every reslice,
producing a different answer each time (−$698/day, +$2,214/day, +116%/day — all
the same data, all noise). That is banned now.

## VERIFICATION TIERS (what "trust" means for a number)

| tier | meaning | may I quote it? |
|---|---|---|
| **CONFIG** | a value set in code/env | yes, with `file:line` |
| **MEASURED** | computed from PUBLIC data (gamma pool/min-size, on-chain balances, categorization) | yes, with method tag + date |
| **MODEL** | our reward SHARE, computed from the official formula but never checked against a real payment | only as "model, unverified", never as profit |
| **NOISE** | trading marks — path-dependent, mean ~0-to-negative, swings ±thousands intraday | never as a point; band/range only |
| **BANNED** | any return that folds NOISE into a headline point | not until a receipt |

## THE LEDGER

All formulas cite `scripts/maker_research/mm_roi_canon.py` (the only sanctioned
compute). "canon" below = a fresh run of that script.

| quantity | EXACT definition | source | tier | quote rule |
|---|---|---|---|---|
| **MIN_BET / msz / capital-per-market** | `rewardsMinSize` dollars. A two-sided min quote costs this via a split pair. | gamma `rewardsMinSize` (canon `net_of` inputs, `:20-21`) | MEASURED | quotable |
| **capital$ / footprint** | Σ `msz` over quoting markets in the slice | canon `foot`, `:215` | MEASURED | quotable |
| **pool** | Σ `rewardsDailyRate` over a market's `clobRewards` = offered daily subsidy | gamma, canon `:137` | MEASURED | quotable |
| **poolPerCap (liquidity)** | median `pool / msz` in a sector | canon per-sector | MEASURED | quotable. NOTE: negatively correlated with reward yield here — high liquidity ≈ adverse-selection risk, NOT quality |
| **rew / rew/day** | model reward accrual `st["acc"]` (per policy Σ, `/days`) | canon `ours[p][0]`, `:100` | **MODEL** | "model, unverified" only. Moves with the ALLOCATION (which markets), not with noise — so hold the portfolio fixed when comparing |
| **rewROI/day** (the anchor) | `100 * (rew/day) / capital$` | canon `:220` | **MODEL** | the single least-unstable number, BUT still model + unverified + NOT profit. Per the RULE, not presented as a decision figure until a receipt |
| **rewTop1 (concentration)** | one market's share of a sector's rewards (Protocol-14) | canon per-sector | MEASURED | quotable; high = fragile |
| **trade / trade/day** | `net_of` = realized + (pos·last_mid − cost). Trading P&L incl. open marks. | canon `net_of`, `:60-64` | **NOISE** | band/range only, never a point |
| **24h band [lo/med/hi]** | distribution of 30-min trading-trajectory sums over last 24h | canon `traj`, `:203-204` | **NOISE** | it is the RISK range, not expected value. A negative low is risk, not a forecast |
| **tradeDrag** | `net − rew` for a slice = its trading component | canon | **NOISE** | shown as a labelled drag; never added into a headline |
| **net / net_d / EV/day / ROI/day (trading-incl.)** | `rew + trade`, i.e. model rewards + NOISE | (removed from canon headline) | **BANNED** | do NOT quote until a receipt. This is the class that whipsawed |
| **blind-tier EV, steered-tier EV, "+116%", "−$698 tier", "+$2,214"** | greedy fills reporting a trading-inclusive point; the steered one also ranked on the outcome it reported (circular) | (removed) | **BANNED** | mirages. Never re-quote |
| **rewards-targeted tier (rew/cap rank)** | fill highest rewards-DENSITY markets; report rewROI | canon | MODEL | rank key is forward-computable (not hindsight); still model+unverified, subject to the RULE |

## THE ONE ANCHOR, stated exactly

`rewROI/day = model reward accrual ÷ capital deployed.` It is the least-unstable
figure because rewards are deterministic given the market set. It is STILL a
model estimate of our pool share, STILL unverified against any payment, and is
NOT profit (it is gross of the trading drag). Under THE RULE it is defined here
but not presented as a return until a receipt exists.

## WHAT PROMOTES A NUMBER FROM MODEL → TRUSTED

Exactly one thing: a funded preflight places a real min-size quote, and the next
day's on-chain reward payment to the wallet is read and reconciled against the
model's predicted accrual for that market. That receipt is the first and only
verification of the reward SHARE. `scripts/maker_preflight.py --stage receipts`.
Nothing here is trustworthy as profit until that number exists.

## VERIFIED NON-EV FACTS (MEASURED, safe to rely on)

These are not returns, so they are not under the ban:
- The in-play settlement gate keys on `sector in ('sports','esports')`
  ([maker_live_engine.py:533]); mis-categorized sports props labelled "unknown"
  bypass it. Sample check 2026-07-20: **45% of 40 "unknown" markets carry a
  sports/event-settlement signature** (FIFA WC props, Valorant map handicaps) —
  a fail-open gate hole, i.e. a fixable cause of trading drag, not inherent cost.
- Caps (CONFIG, `maker_live_engine.py:151-156`): market net `3×msz`, market
  gross `$150`, event `$200`, sector `$600`, day-floor `−$75`. No global cap.
- 30/140 live-universe markets have `msz > $150`, so a min two-sided quote alone
  exceeds the market gross cap → structurally unquotable (measured, state.json).
