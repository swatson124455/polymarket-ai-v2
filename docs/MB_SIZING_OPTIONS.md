# MirrorBot v3 — Copy-Sizing Options (PRE-REGISTERED 2026-07-11)

**Status: DESIGN ONLY. No code active. Written BEFORE any shadow data
exists** — deliberately, so the sizing rules cannot be reverse-engineered
from outcomes (the discipline that voided `bots/mirror_scoring/validation.py`
applies to sizing too). The operator picks an option (now or at the shadow
readout); implementation happens only after a SURVIVES readout, calibrated
on shadow data using the formulas fixed here.

**Context:** walk-forward PASS (+0.0237 pooled, pre-cost) on the 16
chain-CLEAN traders; per-trader confidence is currently BINARY (hire/fire);
amounts are fleet-level only (BotBankrollManager quarter-ish Kelly, $300/bet
Mirror cap, risk_manager limits). No per-bet conviction model exists.

---

## Non-negotiable rails (apply under EVERY option below)

R1. Per-bet cap: existing BotBankrollManager max_bet_usd ($300 Mirror tier).
R2. One bet per market (condition_id) — existing guard, unchanged.
R3. **Per-EVENT budget (NEW — closes the documented correlation gap):** all
    neg-risk sibling markets of one event share a single budget equal to
    ONE bet's cap. YES on candidate A + YES on candidate B of the same
    election is one budget, not two. (Sizing-layer implementation, NOT a
    market gate — per CLAUDE.md, never block neg-risk markets.)
R4. Per-trader daily budget: ≤3 units/trader/day (a whale on a spree is
    correlated conviction, not 10 independent edges).
R5. Daily total exposure cap — existing counters, unchanged.
R6. Fired trader (walk-forward decay rule) → size 0 immediately.
R7. UNDERPOWERED estimate → smallest unit or skip. Never size up on thin data.
R8. Gates (NO_BOOK / SPREAD_TOO_WIDE / PRICE_RAN_AWAY) veto BEFORE sizing.

"Unit" below = the bankroll fraction BotBankrollManager's fractional Kelly
assigns at the roster's pooled post-cost edge, capped by R1.

---

## Option A — Flat-uniform (the null hypothesis of sizing)

Every OK-gated copy from every rostered trader gets exactly 1 unit.

+ Zero estimation → zero overfit risk; matches the certified evidence
  exactly (the walk-forward PASS was computed on UNWEIGHTED bets).
+ Trivially auditable; the shadow readout maps 1:1 onto live expectations.
− Knowingly leaves edge on the table (the +0.09 trader = the +0.015 trader).

Data needed: none. | Complexity: none. | Overfit risk: none.

## Option B — Tiered Kelly (RECOMMENDED)

Three tiers from each trader's SHADOW-MEASURED post-cost edge (not the
API-history edge — the shadow number already includes spread + our latency):

| Tier | Shadow edge (net, market-clustered) | Size |
|---|---|---|
| T1 | ≥ +0.04 on ≥20 resolved OK fills | 1.5 units |
| T2 | +0.02 to +0.04 on ≥20 resolved | 1.0 unit |
| T3 | > 0 but < +0.02, OR <20 resolved | 0.5 unit |
| T0 | ≤ 0 net on ≥20 resolved | 0 (bench; re-tier monthly) |

Re-tier monthly on the same locked grid as hire/fire reviews. Tier
boundaries are FIXED HERE, blind to data.

+ Captures most of the trader-quality spread with 4 auditable buckets.
+ Robust: boundaries, not point estimates — a noisy edge moves a trader
  one tier, not to a wild stake.
+ Runnable straight off the standard shadow readout.
− Coarse; ignores per-bet context beyond the gates.

Data needed: the 2-4-week readout. | Complexity: low. | Overfit risk: low.

## Option C — Continuous shrunk-Kelly

Per-trader posterior edge = empirical-Bayes shrinkage of shadow edge toward
the roster pooled mean (weight ∝ resolved-fill count); per-bet fraction =
quarter-Kelly at that edge and the actual fill price; optional per-category
edge splits where a trader has ≥30 resolved fills in the category.

+ Theoretically most efficient use of the edge.
− Highest overfit surface (continuous knobs, category cells, price terms);
  hardest to audit ("why $212?"); needs 2-3× more shadow data before the
  estimates stop thrashing.

Data needed: ~3 months paper. | Complexity: high. | Overfit risk: the trap
this project keeps escaping. Only as an UPGRADE from B after paper trading
itself accumulates enough resolved fills — never as the starting point.

## Option D — Whale-conviction add-on (testable hypothesis, default OFF)

Scale any option's stake by the whale's OWN conviction: bet size relative
to their trailing median (log-capped ×0.5–×1.5). The shadow already records
`whale_size_usd`, so this is TESTABLE from shadow data before ever going
live: does whale size predict per-bet edge? If the shadow analysis shows no
signal, D dies without costing a cent.

Pre-registered test (fixed now): on resolved OK fills, per-trader Spearman
corr(whale_size / trailing-median, edge); adopt D only if pooled ρ > 0 with
P ≥ 0.95 AND ≥100 resolved fills.

---

## Recommendation (fixed before data)

- **Start paper trading on B** (A as fallback if the readout is
  barely-SURVIVES — a thin pooled edge doesn't support tier spreads).
- **Evaluate D's pre-registered test** on the same shadow data; enable only
  on a pass.
- **Consider C after ≥3 months of paper fills**, never sooner.
- Rails R1-R8 in every case, from day one.

## Decision record

- [x] Option chosen: **A+D hybrid** (operator, 2026-07-11): flat 1-unit base
  wage per approved copy + conviction bonus on wagers above the trader's
  own trailing median. PRE-REGISTERED FORMULA (locked blind to data):
  - r = same-tx-aggregated wager / trailing median of trader's last 50
    entry wagers (median not mean; window seeded from the full cached
    history at cold start).
  - Multiplier (operator-set 2026-07-11): r<2 → 1.0x | 2≤r<4 → 1.25x |
    r≥4 → 1.5x (hard cap). No malus below median (base wage floor).
  - Cold start: multiplier locked to 1.0x until the trader's median has
    ≥20 observations (cache-seeded, so normally immediate).
  - SHIPPED to the shadow watcher (mirror_v3/sizing.py): every shadow
    record carries conviction_r + size_multiplier, so the readout
    evaluates THIS rule, not an approximation.
  - All rails R1-R8 apply AFTER the multiplier.
  - PAPER from day one; the bonus component touches LIVE sizing only
    after passing D's pre-registered test (§Option D: pooled Spearman
    ρ>0, P≥0.95, ≥100 resolved fills — evaluable from shadow
    whale_size_usd already being recorded). Test fails → bonus dies,
    flat base stands.
- [ ] Readout verdict it was conditioned on: ____ (fill at readout)
