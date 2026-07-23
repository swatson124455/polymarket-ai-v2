# KALSHI REWARD-DENSITY CENSUS + FOOTPRINT-DILUTION PROPOSAL — 2026-07-23

**Status:** PROPOSAL. READ-ONLY, GET-only. No orders, no config write, no deploy, no `_strike_of`/module edit.
**Census instant:** 2026-07-23T19:15Z · **venue:** 2,345 active incentive programs (single page, not truncated) · **method:** exact census, EVERY post-gate contract's book fetched (120/120, 0 errors), no sampling. Late-life gate replicated from the deployed `select_footprint`: `cutoff=min(120,max(45,0.6·life_min))` — dropped 0 contracts this instant.
**Backing files (all under `kalshi_live/`):** `allowlist_density_census.json`, `footprint_dilution.json`, `footprint_r3_symmetric.json`, `allowlist_footprint_replicate.py`, `refute_dilution_illusory.{py,json}`, `refute_live_resting_gas.py`, `refute_posttrim_footprint.py`.
**Every dollar below is R1-normalized `$/day` and an §M7 UPPER BOUND, never expected earnings.**

---

## 1. PREMISE CORRECTION (do not re-litigate)

**The handoff premise — "the bot ranks by `usd_day` (VOLUME); it should rank by reward density" — is FALSE.** In the deployed `select_footprint` (`maker_kalshi_quoter.py`, md5 `727ca7c5…`), `usd_day = (period_reward/10000)/window_days`. That is **reward dollars per day per contract**, R1-normalized — it is **not** volume. `period_reward` is fixed-point ×10000; dividing by the window length converts a variable-window pot (13h…698h across the venue) into a comparable daily rate. Verified live against `/incentive_programs`: GENERIC `10,000,000 → $1000 / 6.915d = $144.62/day`; gas-D `1,000,000 → $100 / 0.531d = $188.24/day`; TRUMP `1,428,571 → $142.86 / 6.915d = $20.66/day` — all reconcile with the census. **The bot already ranks by reward density.**

**The real lever is the ROUND-ROBIN, not the ranking.** `select_footprint` sorts rows by `(-usd_day, ticker)` and then spreads `FOOTPRINT_TOP=40` slots **round-robin across series** (1 pick per active series per round, `PER_SERIES_CAP=100` non-binding), so with temp dark the 40 slots land ~evenly across all 9 active non-temp series regardless of each series' density. The lever anyone can pull is the **`SERIES_ALLOW` width** (widened today from 7 → 14 series = the C18 anti-starvation fix, arguably over-widened), not a ranking change. **Frame everything around the round-robin over the widened allowlist, not a volume-vs-density ranking bug.**

---

## 2. PER-SERIES EARNABLE-DENSITY TABLE (R1 + R3 + fees + structure + toxicity)

`earnable $/day` = series pool `$/day` × (two-sided contracts ÷ eligible contracts) — R3 says a snapshot whose book fails Target Size on **either** side pays **nobody**, so only two-sided rungs earn. Ranked by earnable `$/day`. Temp shown separately (dark).

| # | series | active progs | best $/day (per-contract) | pool $/day | two-sided % | **earnable $/day** | fee | structure | toxicity |
|---|--------|:---:|:---:|:---:|:---:|:---:|:---:|---|---|
| — | KXTEMPDCH | **0 (DARK)** | 0 | 0 | — | **0** | FREE | ladder (temp) | §M8 loss family; HARD-GATE to 07-27 |
| — | KXTEMPAUSH | **0 (DARK)** | 0 | 0 | — | **0** | FREE | ladder (temp) | §M8 loss family; HARD-GATE to 07-27 |
| — | KXTEMPLAXH | **0 (DARK)** | 0 | 0 | — | **0** | FREE | ladder (temp) | §M8 loss family; HARD-GATE to 07-27 |
| — | KXTEMPNYCH | **0 (DARK)** | 0 | 0 | — | **0** | FREE | ladder (temp) | §M8 loss family; HARD-GATE to 07-27 |
| — | KXTEMPCHIH | **0 (DARK)** | 0 | 0 | — | **0** | FREE | ladder (temp) | §M8 loss family; HARD-GATE to 07-27 |
| 1 | KXAAAGASD | 7 | 188.2 | 1317.6 | 71% (5/7) | **941.2** | FREE | ladder (`greater`, mutex=F) | **LOW** — gas = §M8 profit family (+1.1%) |
| 2 | KXAMSAVO | 11 | 16.4 | 180.5 | 100% (11/11) | **180.5** | FREE | ladder (`greater`, mutex=F) | LOW — avocado wkly avg price, commodity |
| 3 | KXGENERICBALLOTVOTEHUB | 1 | 144.6 | 144.6 | 100% (1/1) | **144.6** | FREE | ladder (`greater`, mutex=F) | MODERATE — poll-release-driven; 1 contract |
| 4 | KXTRUMPENDORSEMENTS | 7 | 20.7 | 144.6 | 100% (7/7) | **144.6** | FREE | ladder (`≥`, mutex=F) **but pairing DARK** | **HIGHEST** — see §4 |
| 5 | KXMUSKNW | 13 | 11.1 | 144.5 | 77% (10/13) | **111.1** | FREE | ladder (`greater`, mutex=F) | MODERATE — net-worth, headline-sensitive |
| 6 | KXB200MON | 40 | 2.6 | 105.4 | 92% (37/40) | **97.5** | FREE | ladder (`greater`, mutex=F) | LOW-MOD — Nvidia B200 price; new/unproven, thinnest per-rung |
| 7 | KXAAAGASW | 15 | 15.4 | 230.6 | **27% (4/15)** | **61.5** | FREE | ladder (`greater`, mutex=F) | LOW — gas family; tails one-sided now |
| 8 | KXCHIPBURRITO | 6 | 6.6 | 39.7 | 100% (6/6) | **39.7** | FREE | ladder (`≥`, mutex=F) | LOW — Chipotle price; tiny pool, 30-day window |
| 9 | KXH100MON | 20 | 2.6 | 52.7 | 70% (14/20) | **36.9** | FREE | ladder (`greater`, mutex=F) | LOW-MOD — Nvidia H100 price; new/unproven, thin |

**Fees:** all 14 series are maker-fee **FREE** (`fee_type: quadratic` in `series_fee_types.json`; none are `quadratic_with_maker_fees`). Fees are **not** a differentiator here.
**Structure:** all 9 live series are `mutually_exclusive=False` threshold ladders (`greater`/`greater_or_equal`, "above X") — the GOOD structure (adjacent rungs self-hedge, one event risk). NONE are categorical/mutex; NONE are `mention` families. Confirmed from `/markets.strike_type` + `/events.mutually_exclusive`, not the ticker string. **Structure is not a differentiator either** — the differentiators are per-rung density and toxicity.

**Ranking — by earnable $/day (R3-adjusted, the number that matters):**
`GASD 941 ≫ AMSAVO 181 > GBVH 145 ≈ TRUMP 145 > MUSK 111 > B200 98 > GASW 62 > CHIP 40 > H100 37 | temp 0×5 (dark)`

**The R3 gap:** **KXAAAGASW collapses from rank 2 (pool $231) to rank 7 (earnable $62)** — its deep-OTM/deep-ITM ladder tails are one-sided; only ~4 near-the-money rungs are two-sided. GASD (71%), MUSK (77%), H100 (70%) show the same tail-thinning, milder; AMSAVO/GBVH/TRUMP/CHIP are 100% two-sided.

---

## 3. IS THE DILUTION REAL OR ILLUSORY? — DECISIVE VERDICT: **ILLUSORY as a reward story. DO NOTHING on reward grounds.**

The nominal round-robin dilution vs a pure top-40-by-density footprint is real arithmetic but collapses at every honest step:

| stage | dilution vs pure-density top-40 | source |
|---|---|---|
| Nominal `$/day` (R3-blind) | **$542/day (27.1%)** | `footprint_dilution.json`: 2002.68 − 1460.43 |
| R3-symmetric earnable (two-sided both sides) | **$307/day** | `footprint_r3_symmetric.json`: 1472.45 − 1165.75 |
| Live-faithful (bot rests gas-D on two-sided 4.125, not the tiebreak's one-sided 4.100) | **~$100–120/day** *(MODEL est.)* | refuter #1 live cross-check |
| After §M7 2–6× over-prediction haircut | **~$20–60/day** *(GUESS band)* | §M7 prior |

**Why the gas-reward story is illusory (CONFIRMED by two refuters that did NOT refute it):**
- **Gas is two-sided-EXHAUSTED at the depth already quoted.** There are only **9–10 two-sided gas strikes on the whole venue right now** (gas-D 5–6 of 7 eligible, gas-W 4 of 15) — fewer than the **10 gas slots the round-robin already allocates**. A pure-density footprint would stack 21 gas contracts, but 12 are one-sided → pay **nobody** (R3). Concentrating the allowlist toward gas recovers **≈ $0**.
- **Live authed cross-check (same instant, `/portfolio/orders`):** the bot already rests all 5 two-sided gas-D strikes (4.105/4.110/4.115/4.120/4.125 — including the $188/day 4.125 first flagged as "uncaptured"), skipping the one-sided 4.100/4.130. It leaves 4 of 5 gas-W slots and four whole series' slots **unused** and is bounded by a ~$149 resting-notional budget. So slot reallocation reshuffles a **non-binding** constraint and adds zero two-sided gas capture.
- **The C18 anti-starvation round-robin is validated in reverse.** The spread beyond gas is **forced by R3 thinness**, not wasteful. Concentrating back into gas re-creates the exact starvation C18 fixed and buys nothing.

**Honest reward recommendation: DO NOTHING to reclaim "gas dilution" — there is none.** The only genuinely uncaptured *gas* is ~3 thin two-sided gas-W strikes (~$46/day nominal → **~$8–23/day after the §M7 haircut — GUESS band**), and those are **capital/gate-limited, not allowlist-limited** (bot rests 1 of 4) — a series trim cannot capture them.

> ⚠ **The "reward upside of ANY trim ≈ $0" claim is TOO STRONG — see refuter #4 in §6.** A `SERIES_ALLOW` trim does NOT produce the pure-top-40 footprint measured above; it produces a round-robin over the *remaining* series, which in the same R3 model earns **+$193.8/day** (concentration into surviving two-sided AMSAVO/MUSK/gas-W, NOT gas). That model delta is unmeasured live, likely absorbed by the same ~$149 capital ceiling, and partly offset by deleting TRUMP's own $103–145/day earnable — so the live reward delta of a trim is an **UNMEASURED band, sign unknown (~ −$100 to +$194/day pre-haircut, MODEL)**. Do not sell a trim as reward, and do not claim it costs nothing either.

---

## 4. IF A TRIM IS PURSUED: RANKED CANDIDATES (the 7 NEW series only — NOT temp)

**Temp is explicitly OUT of any cut:** all 5 KXTEMP* series are (a) **DARK right now** (0 active programs — hourly windows between runs; an instantaneous census structurally *understates* temp, §M7c), and (b) **HARD-GATED to 2026-07-27** (credits lag a Time Period; the −$13.06 temp figure was a withdrawn partial ledger). **Do not cut temp now.**

Candidates ranked by structural/toxicity disqualification first, then dead-weight per-rung density:

| rank | series | earnable $/day | 2-sided | proposed verdict | why |
|---|---|---:|---:|---|---|
| **1** | **KXTRUMPENDORSEMENTS** | 144.6 | 100% | **FIX PARSER, don't delete (see below)** | Highest informed-flow toxicity on the board (discrete, news-driven Truth-Social endorsement *counts*). A-prefixed strikes (`A3/A5/A10/A15/A20/A25/A50`) make deployed `_strike_of` return `None` → excluded from `ladder_pairing` → any fill carried **naked**, `strike_parse_failed` bumped silently. |
| **2** | **KXB200MON** | 97.5 | 92% | **OPTIONAL / live no-op** | Thinnest per-rung density on the board — **$2.64/day per rung** × 40 rungs. New, unproven. Pool looks OK only via breadth. **Live: 0 resting orders, 0 inventory → cutting sheds ~0 live exposure (refuter #3).** |
| **3** | **KXH100MON** | 36.9 | 70% | **OPTIONAL / live no-op** | Same $2.64/day per rung, 20 thin rungs, 70% two-sided, lowest earnable of the live nine. **Live: 0 resting, 0 inventory (refuter #3).** |
| 4 | KXCHIPBURRITO | 39.7 | 100% | OPTIONAL | $6.6/rung, tiny $40 pool, 100% two-sided, LOW toxicity, self-hedges cleanly. Cut-or-keep is a wash. |
| 5 | KXMUSKNW | 111.1 | 77% | KEEP (watch) | MODERATE toxicity but parses/self-hedges fine, decent earnable. |
| 6 | KXGENERICBALLOTVOTEHUB | 144.6 | 100% | KEEP | $145 earnable, 100% two-sided, single contract, self-hedges. |
| 7 | KXAMSAVO | 180.5 | 100% | KEEP | Best of the 7 new — $181 earnable, 100% two-sided, LOW-toxicity commodity ladder. |

### The one genuine kernel: KXTRUMPENDORSEMENTS — FIX the parser, don't delete the series

Per **Fix-not-remove / never-dismiss-market** doctrine (MEMORY.md core feedback) and **refuter #3**, the root remedy for TRUMP's A-prefix hazard is a **one-line `_strike_of` A-prefix parse fix + toxicity sizing**, **NOT** a permanent deletion of a 100%-two-sided $145/day-earnable series (2nd-densest of the 7 new, tied with the KEPT GENERIC). That fix is a **CODE change — OUT OF SCOPE for this read-only proposal; flag to a Kalshi code session.** The hazard is currently **latent, not urgent:** live TRUMP inventory = **0** (2 resting YES on A3 only), the naked-risk breaker guards fills, and the programs auto-expire ~66h out (26JUL25).

### Reversible interim lever (only if the operator wants risk mitigation BEFORE the parser fix lands)

A `SERIES_ALLOW` trim of **TRUMP only** removes the toxic-naked series without touching gas or temp and without re-creating C18 (round-robin stays on; this reverts *today's over-widening*, not a concentration into temp). **This is a Tier-2 trade-universe change → OPERATOR-GATED, propose-only.**

```
# live.env — SERIES_ALLOW: 14 → 13 series (TRUMP-only interim risk trim)
# REMOVE: KXTRUMPENDORSEMENTS   (toxic + A-prefix naked ladder — interim, pending _strike_of fix)
# B200MON / H100MON cuts are LIVE NO-OPS (0 resting, 0 inventory) — SKIP unless minimizing slot count
SERIES_ALLOW=KXTEMPDCH,KXTEMPAUSH,KXTEMPLAXH,KXTEMPNYCH,KXTEMPCHIH,KXAAAGASD,KXAAAGASW,KXAMSAVO,KXGENERICBALLOTVOTEHUB,KXMUSKNW,KXCHIPBURRITO,KXB200MON,KXH100MON
```
**Rollback:** restore the prior 14-value string and redeploy. No code change, no `_strike_of` touch, no round-robin/`PER_SERIES_CAP` change.
**Risk breaker / invariant touched:** none directly — this narrows the quoting universe upstream of the naked-risk breaker and ladder self-hedge invariants; it removes the series whose A-prefix strikes those invariants cannot currently pair. The late-life gate, $40 daily auto-halt, 2c pair cap, and mirror-symmetry tests are unaffected.

**Levers REJECTED** (analysis in §6): binding `PER_SERIES_CAP` (TRUMP's defect is *presence*, not slot count; any cap that concentrates would starve gas); per-series MIN earnable-$/day gate (gates out temp every time temp is dark → violates the temp hard-gate + fights C18).

---

## 5. HONEST UPSIDE — small, relative, upper-bound

- **Reclaimable gas reward: ≈ $0/day.** Gas is two-sided-exhausted below the depth already quoted (§3). Consistent with the §M4 in-allowlist-reallocation plateau (~10–15% of a small base, near saturation) — and here even that is R3-illusory for gas.
- **Reward delta of an actual trim: UNMEASURED, sign unknown** (refuter #4: model +$193.8/day from concentrating into surviving two-sided series, but live capital-truncated to ~$149 resting notional and offset by deleting TRUMP's earnable). **Do NOT sell any trim as a yield play; also do not claim it is costless.**
- **The real value of acting is RISK reduction, not reward:** removing TRUMP's latent fully-naked A-prefix carry — a series whose only structural hedge (`ladder_pairing`) is silently disabled by the `_strike_of` parse failure. That value is **forward-looking** (0 current inventory) and is **better realized by the `_strike_of` fix than by deletion** (Fix-not-remove).
- **Expected dollar impact of the recommendation: small.** On reward, ~$0. On risk, it closes a latent naked-carry path that has not yet fired.

---

## 6. REFUTER VERDICTS — reported in full, including the two that qualify the proposal

Four adversarial lenses were run READ-ONLY against live authed data and no-network replicates. **Two did not refute the illusory-dilution finding; two (MEDIUM) refuted the proposal's framing of reward-upside and risk-quantities.**

| # | lens | refuted? | severity | what it settles |
|---|------|:---:|:---:|---|
| 1 | dilution-illusory-r3 | **NO** | LOW | **CONFIRMS illusory.** Fresh census 19:29Z + authed resting book: gas two-sided venue-wide = 10; bot already rests the two-sided gas-D strikes incl. the $188/day 4.125; leaves 4/5 gas-W + 4 series' slots unused; ~$149 resting budget. Constraint is downstream capital/quoter budget, NOT allowlist width → trimming captures ≈$0 two-sided gas. Only a framing nit remains. |
| 2 | trim-rebreaks-c18-gas-starvation | **NO** | NONE | **No C18 re-break.** Under fixed `FOOTPRINT_TOP=40` round-robin, removing NON-gas series can only redistribute slots toward survivors (gas weakly ↑). 60-cell temp-return grid: min gas under trim = 8 vs 6 current; ZERO regimes where trim gas < current. Temp's high pots cannot starve gas under round-robin regardless of width — the property C18 installed and the proposal preserves. (Orthogonal note: with temp dark the trim raises gas concentration 10→16 slots, ~7 one-sided/naked — an event-concentration concern in the *opposite* direction, not starvation.) |
| 3 | ranking-is-model-noise | **YES** | MEDIUM | **Risk quantities are selection-model artifacts, not live exposure.** Authed `/portfolio/positions`+`/orders`: inventory ONLY in gas (~$85) + trace AMSAVO; 25 one-sided resting orders across 5 series (gasD 8, AMSAVO 8, CHIP 6, TRUMP 2 on A3, gasW 1); B200/H100/MUSK/GENERIC = 0 resting, 0 inventory. So (a) cutting B200+H100 sheds **0** live exposure — "frees ~14 of 40 slots of adverse-selection exposure" is a round-robin-replicate artifact; (b) TRUMP "carried fully naked" overstates — 0 inventory, the hazard is forward-looking; (c) the genuine kernel is the A-prefix `_strike_of` fix + toxicity sizing (**Fix-not-remove**), not deletion of a $145/day two-sided series. |
| 4 | measurement-or-premise-error | **YES** | MEDIUM | **Counterfactual + premise errors** (units premise itself is CORRECT — `usd_day` = reward $/day, re-verified). (1) The "reward upside ≈ $0 / no trim to make" conclusion measured deployed-RR-9-series ($1165.8) vs PURE-top-40 ($1472.4). A `SERIES_ALLOW` trim produces NEITHER — it produces RR over the 6 remaining series = **$1359.6/day (+$193.8 vs current)** in the identical R3 model (`refute_posttrim_footprint.py`, no-network, replication-faithful — I re-ran it, confirmed). Honest reward delta = UNMEASURED band ~ −$100…+$194/day pre-haircut, sign unknown (cutting TRUMP deletes its $103–145/day two-sided earnable). (2) The "40 slots spread ~evenly into low-density series" premise is contradicted by live state: bot rests ~14 distinct contracts across 5 series, concentrated in the highest-density series, quotes B200/H100/MUSK/GENERIC ZERO times — capital-truncated far short of 40, never reaches the tail the trim targets. |

**Net synthesis of the four:** the illusory-dilution / gas-exhausted / no-C18-re-break findings **stand** (refuters 1 & 2). But refuters 3 & 4 correctly puncture two claims from the earlier DECISION draft: (a) "reward upside ≈ $0 of any trim" is too strong — the honest delta is unmeasured with unknown sign; (b) "B200/H100 cuts free adverse-selection exposure" is false — they are live no-ops; and (c) TRUMP should be **fixed at the parser, not deleted** (Fix-not-remove). This proposal adopts all three corrections: **reward → DO NOTHING; risk → fix `_strike_of` (code session) + toxicity sizing on TRUMP; SERIES_ALLOW trim of TRUMP only as an optional operator-gated interim stopgap; skip B200/H100.**

---

## 7. UNCERTAINTY + OPERATOR VERIFICATION COMMANDS (READ-ONLY)

**Known limits of every number here:**
- **One instant.** Two-sided % and the whole census are the 19:15Z books; a ladder's two-sided rungs shift with the underlying — treat fractions as a snapshot, not a duty cycle.
- **$/day is an §M7 UPPER BOUND** (model over-predicts 2–6×), a RELATIVE rank only — never expected earnings.
- **Temp is dark now** — an instantaneous census understates temp (§M7c). Temp is excluded from any cut regardless (07-27 hard-gate).
- **Full-book two-sided test is GENEROUS** (ignores the reward band → true two-sided gas capacity is ≤ the 9–10 counted, which only *strengthens* "illusory").
- **GUESS flags:** the "~$8–23/day" post-haircut gas-W figure and the "~$20–60/day" and "−$100…+$194/day" bands are MODEL estimates with the §M7 haircut applied, not measured receipts.
- **`rewards_residual` is never quoted** (demonstrably wrong).

**To reproduce the census / dilution (no keys needed, public API):**
```
cd .../kalshi-wt/kalshi_live
python3 allowlist_density_census.py      # → allowlist_density_census.json (14-row table)
python3 allowlist_footprint_replicate.py # → footprint_dilution.json (round-robin vs pure top-40)
python3 refute_posttrim_footprint.py     # no-network: post-trim RR earnable = $1359.6/day (+$193.8)
```
**To verify live resting/inventory state (authed reads, module `L`):**
```
cd .../kalshi-wt/kalshi_live && python3
>>> import kalshi_attribution_ledger as L
>>> L.get(L.P+'/portfolio/orders?status=resting')     # expect ~25 one-sided orders, 5 series
>>> L.get(L.P+'/portfolio/positions')                 # expect gas-only inventory, TRUMP/B200/H100 = 0
```
**To confirm the units premise on any series:** `L.get(L.P+'/incentive_programs?status=active')` → for a program, `usd_day = (period_reward/10000) / ((end-start)/86400)`.

**No orders, no config write, no deploy, no module edit were made. This document is a proposal only.**
