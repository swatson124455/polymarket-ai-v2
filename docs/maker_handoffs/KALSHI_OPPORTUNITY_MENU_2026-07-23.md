# KALSHI OPPORTUNITY MENU — 2026-07-23

**Purpose:** the full flush-list from the "creative god-mode" opportunity dive. Everything is here; nothing was discarded. This is a THINKING deliverable — no trades, no deploys, no config edits were made. Every item below is a candidate for the operator to accept, reject, or send back for measurement.

**Read this first — the four things that reorder everything:**

1. **Ranking by pool $/day is the wrong axis.** The binding metric is **`cap/mkt`** — OUR modelled DF-weighted capture per market. Big pools (KXFUNDRAISING $2,501/d, KXLIUKELIMINATION $2,450/d) are already saturated by rivals who provide the 1000-ct depth, so our 20-ct share is a rounding error (their `cap/mkt` ≈ 0.00–0.06, un-snipeable).
2. **Our own flagship KXAAAGASD has `cap/mkt` = 24.6 — 3-4× higher than ANY non-allowlisted candidate.** The census did NOT find a series that beats gas. Gas-daily is the best market on the venue and we already own it.
3. **So the real question this menu answers is: where does idle capital go once gas two-sided-exhausts (~10 strikes)?** Diversification targets, not a gas replacement.
4. **We can never be the marginal maker (E2).** Measured 0/304 at 20-ct vs a 1000-ct target. We can only RIDE a book someone else keeps two-sided; we cannot CREATE eligibility. Every "be first to an empty book" idea dies on this.

**Number conventions (bind every $ figure below):**
- **§M7 upper bound** — all `cap/mkt` / capSeries figures are R1-normalized ($/day = (period_reward/10000) ÷ window_days), R3-adjusted (× two-sided%) where measured, and STILL over-predict receipts 2-6×. **Divide by ~3 for a realistic figure.** Never bank the headline.
- **Receipt reality check:** LIP credits were **$25.21 over ~2 days** (§M8, CSV-verified). The trading/settlement side is **net-negative** (Ground Task 3: −$87.59 over 51 contracts). LIP credits are the only thing keeping the lane above water.
- **Sunset 2026-09-01** hangs over everything: both LIP and Volume incentive programs expire. ~5.5 weeks runway. Any payback past that date is discounted to near-zero.

---

## 1. TOP SHORTLIST — highest-conviction, ranked

Leading with the best **snipe** and the best **farm**, then the rest by conviction.

### ⚡ BEST SNIPE — Gas-daily relist-instant timing (score 4, PURSUE)
KXAAAGASD rungs open on a **scheduled 12:00Z daily boundary** (`open_time` in the record) — being first at the fresh reference is a *scheduled* event we can poll for at 0.6s, not an HFT race. Compounds our best market at zero new capital, fee-free, protective ladder shape.
- **Grounded#:** gas `cap/mkt` 24.6/day §M7 upper bound; realistic ~8/day, and only the DELTA from better-timed entry. Live data corrected the thesis: ONE ~16h window/day (12:00Z→03:59Z), NOT 2×/day — so it fires ~1×/day.
- **Snipe.** **Next step:** poll `open_time`; rest at-reference two-sided in the first minutes of the 12:00Z window; A/B snapshots captured vs mid-window arrival. **Risk:** a rival camping the same relist minute re-saturates the 1000-ct target and our edge evaporates to a ~2% share.

### 🌾 BEST FARM — Go DEEP not WIDE: concentrate freed capital into gas (score 5, PURSUE)
Gas-daily `cap/mkt` 24.6 is 3-4× any non-allowlisted candidate AND the only shape that converts for us (fingerprint −$0.0106/ct, 5% taker). Live-verified 6/8 gas-daily strikes two-sided at $1.5k-28k depth with our own quotes resting → at 20ct we're sub-marginal, so reward is ~linear in our size (E2-safe: others keep it eligible, fee-free quadratic/mult-1 confirmed). **Doubling size on the single best density beats spreading thin across sub-1.0 `cap/mkt` series by ~an order of magnitude.**
- **Grounded#:** `cap/mkt` 24.6 §M7 upper bound (~8/day after ÷3); live 6/8 strikes two-sided $1.5k-28k depth.
- **Farm (capital-scale).** **Next step:** measure per-strike competitor depth on the ~6-7 two-sided gas strikes to find the size where our score share stops being negligible; scale to THAT, not past it. **Risk:** linearity breaks if we become a material score fraction (competitor depth unmeasured).

### The rest of the shortlist (ranked)

| Rank | Idea | Verdict/Score | Grounded# (§M7 UB) | S/F | The ONE next step |
|---|---|---|---|---|---|
| 3 | **GPU-street-price MAX ladder family** (H200/A100/RTX5090/H100/B200) | PURSUE 5 | ~$25-30/d capSeries agg; ~$8-10/d after ÷3. `cap/mkt` 0.6-1.0/series | Farm | Allowlist the two live-verified 100%-two-sided series (**KXH200MAX 6/6, KXA100MAX 9/9**) FIRST; verify MAX settlement semantics before adding the rest |
| 4 | **KXHOODA** — highest `cap/mkt` clean ladder after gas (Robinhood funded-accounts) | PURSUE 5 | capSeries/d 105.6 §M7 UB; ~35/d after ÷3 IF depth holds | Farm | Monitor eligibility persistence over 48h — census saw it churn 1/6 vs live 17/21; confirm depth holds off-peak before committing |
| 5 | **Combo Incentive opt-in** — reprice the fills we book as pure cost | INVESTIGATE 4 | basis ~1,275 maker ct/day (~$2,201 notional/3d, MEASURED); payout **UNQUANTIFIED** | Farm | Send drafted support email: *"If I opt into Combo, does my account keep earning LIP — yes or no?"* + ask pool/rate. **Do NOT opt in on silence** (DLP-trap logic) |
| 6 | **KXWTIWHEN** — top clean non-gas farm (WTI $100 date ladder) | PURSUE/INV 4 | `cap/mkt` 7.55 (highest clean non-allowlist); capSeries ~45/d UB, ~15/d ÷3 | Farm | Reconfirm two-sided depth on the -100-* strikes with a working orderbook read during US session, then propose allowlist add (operator-gated) |
| 7 | **Load gas-weekly KXAAAGASW first** — proven-shape, already allowlisted capital sink | PURSUE/INV 4 | unmeasured (0 settled); live 2/8 strikes fully two-sided, near-money $1.2k-30k | Farm | Deploy small, then run `kalshi_settlement_pnl.py` on the first KXAAAGASW resolutions BEFORE scaling — confirm it behaves like gas-daily, not like temp |
| 8 | **Time-box presence to early/mid window** — be flat before settlement | INVESTIGATE 4 | targets the MEASURED −$34.21 settle-leg (24 lost / 8 won on 32 carried); halving ≈ +$17/period vs $25/2d LIP | Farm | Backtest per-series settle-leg P&L vs time-remaining on the Task-3 snapshot; apply first to gas with a **rest-only flatten** rule so early-exit doesn't just move loss to the realized leg |
| 9 | **KXNETFLIXTOPVIEWSMOVIE** — clean entertainment view-count ladder | PURSUE 4 | `cap/mkt` 2.54 (top clean ladder); capSeries 28/d UB, ~9/d ÷3; live 7-8/11 two-sided | Farm | Allowlist with a **catalyst blackout**: suppress quotes ~2h either side of the weekly Tuesday view-count print; measure receipts vs model over one week |
| 10 | **KXUSGASCPI** — monetize the gas underlying we already model | PURSUE 4 | `cap/mkt` 0.61 × 13 prog ~$7.9/d UB, ~$2.6/d ÷3; maker-fee-FREE confirmed | Farm | Add to allowlist, quote 1-2 near-money rungs with a **model skew** off our gas nowcast; measure realized $/ct vs the gas-daily −$0.011 benchmark; hard-stop quoting in the 24-48h pre-print late-life window |
| 11 | **Velocity-matched selection** — quote only slow-reference underlyings | INVESTIGATE 4 | unmeasured off gas; target = reproduce gas −0.0106/ct, 5%-taker on GPU/econ ladders | Farm (quality) | Poll orderbook mids per candidate ladder over a day, compute tick-changes-per-0.6s, keep only series whose reference is quiescent within a snapshot interval |
| 12 | **Window-length arbitrage** — always prefer the shortest-window variant of a family | PURSUE 4 | structural rule; gas-daily `cap/mkt` 24.6 = 3-4× any weekly sibling (~20× density gap vs a 228h weekly) | Farm | Encode "prefer shortest-window variant within a family" as a hard tiebreaker in series-selection ranking, gated behind the structure filter |

**Honorable mentions just below the line:** Minimum-viable-size probe (4, resolves the linearity assumption under 5/6/7); New-series land-grab weekly `/series` diff (4, the process that turns one-off finds into a pipeline); KXRT Rotten Tomatoes ladder (4, broadest clean farm — 167 markets, 80% two-sided); Self-hedged LIP bracket on gas (4, extends the only converting shape); Front-load capital by early August (3, free sunset arithmetic).

---

## 2. THE FULL MENU — by edge_type (every idea, nothing dropped)

DEAD ideas are kept with their one-line kill reason so we don't re-propose them. Duplicated ideas from parallel ideation runs are consolidated into one row (independent re-discovery = higher confidence, noted).

### THEME A — Capital allocation & sizing

| Title | Verdict | Score | Grounded# (§M7 UB) | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| Go DEEP not WIDE — concentrate freed cash into gas | PURSUE | 5 | `cap/mkt` 24.6, ~8/d ÷3; live 6/8 two-sided | LOW | Yes, already here | Measure per-strike competitor depth; scale to the non-marginal knee |
| Load gas-weekly KXAAAGASW first | PURSUE | 4 | 0 settled; live 2/8 fully two-sided | MED | Yes, allowlisted | Settle-leg fingerprint before scaling |
| Front-load ALL capital by early August (sunset clock) | PURSUE | 3 | avoids stranding capital <2wk of earning life | LOW | Yes, funding decision | Fund to the 2-3x absorption knee by early Aug, then stop |
| Compound LIP credits into gas depth on a schedule | PURSUE | 3 | recovers idle-cash drag; bounded by ~10 gas strikes + 5.5wk | LOW | Yes, infra we run | Confirm swept credits route to redeployable free cash, let run |
| Window-length arbitrage (daily > weekly variant) | PURSUE | 4 | gas-daily 24.6 = 3-4× weekly sibling | LOW | Yes, ranking rule | Encode shortest-window tiebreaker behind structure filter |
| 2x/5x capital scenario map (attention+strikes bind, not cash) | INVESTIGATE | 3 | knee ~2-3x is a **GUESS** pending per-strike depth | LOW | Yes, read-only analysis | Pull per-strike orderbook depth to pin the non-marginal x |
| Barbell 70% energy / 30% clean-ladder tail | INVESTIGATE | 3 | slightly below pure-gas; tail buys unquantified insurance | LOW | Yes in principle | Reconfirm tail-name live two-sidedness before fixing any split |
| Minimum-viable-size probe (smallest scoring clip) | INVESTIGATE | 4 | unmeasured — resolves deep-vs-wide linearity | LOW | Yes, existing gas deploy | Spatial A/B: 10/20/40ct on 3 equidistant gas rungs, read per-rung credit/ct over 4-5d |
| Sub-account multiplies LIP (pro-rata) | **DEAD** | 1 | zero — formula-neutral | UNK | No | **KILL:** pro-rata sums exactly; multi-accounting violates KYC-per-person ToS |
| Second account runs Combo, primary runs LIP | **DEAD** | 1 | UNQUANTIFIED | HIGH | No | **KILL:** two-account structure = ToS violation; single-account Combo opt-in is the real move |

### THEME B — Series selection / new farm targets

| Title | Verdict | Score | Grounded# (§M7 UB) | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| GPU-street-price MAX family (H200/A100/RTX5090/H100/B200) | PURSUE | 5 | agg ~$25-30/d UB, ~$8-10/d ÷3; `cap/mkt` 0.6-1.0 | LOW | Yes, shape already run | Allowlist KXH200MAX + KXA100MAX first (live 6/6, 9/9); verify MAX settlement per series |
| KXHOODA — best clean ladder after gas | PURSUE | 5 | capSeries 105.6 UB, ~35/d ÷3 | LOW | Yes | 48h eligibility-persistence check (census churned 1/6) |
| KXWTIWHEN — top clean non-gas density | PURSUE/INV | 4 | `cap/mkt` 7.55; capSeries ~45/d UB, ~15/d ÷3 | LOW-MED | Yes IF rivals keep it two-sided | Reconfirm -100-* two-sided depth in US session; operator allowlist add |
| KXNETFLIXTOPVIEWSMOVIE — clean entertainment ladder | PURSUE | 4 | `cap/mkt` 2.54; capSeries 28/d UB, ~9/d ÷3 | MED | Yes between prints | Allowlist w/ ~2h catalyst blackout around weekly print |
| KXRT — Rotten Tomatoes score ladder (broadest clean farm) | PURSUE | 4 | `cap/mkt` 0.43; capSeries ~30/d UB, ~10/d ÷3 | LOW | Yes, far-from-release rungs | Allowlist deepest rungs on far-dated/unreleased films; skip opening-weekend windows |
| KXUSGASCPI — gas underlying we model | PURSUE | 4 | `cap/mkt` 0.61 ×13 ~$7.9/d UB, ~$2.6/d ÷3; fee-FREE | LOW-HIGH* | Yes | Allowlist, model-skew 1-2 near-money rungs, avoid print late-life window (*tox HIGH only near monthly print) |
| Single-stock ladders (KXMETA, KXTLN) | INVESTIGATE | 3 | KXMETA 10.1 + KXTLN 5.4 UB, ~5/d ÷3; fee-FREE confirmed | MED | Yes on fees/data; E9-bundle-limited | Confirm no earnings date inside reward window; trial KXMETA as single overflow |
| GPU-adjacent econ ladders (KXNHSALES) | INVESTIGATE | 3 | capSeries 4.9/d UB, ~1.6/d ÷3; fee-FREE | MED | Yes structurally | Pull new-home-sales release calendar; quote between prints only |
| Politics THRESHOLD ladders only (KXVOTEPRIMARY), skip mutex | INVESTIGATE | 3 | `cap/mkt` 0.77; capSeries 13/d UB, ~4/d ÷3; fee-FREE | HIGH | Marginal — news-gappy, no feed | Adopt mutex=False as permanent gate; trial KXVOTEPRIMARY only >30d from resolution |
| GPU-price ladder BASKET as correlated allocation | INVESTIGATE | 2-3 | agg ~$20-25/d UB, ~$7/d ÷3; variance-reduction | LOW-MED | Partial — E9 attention cap | Add ONE non-listed GPU ladder beside B200/H100, measure if attention amortizes before widening |
| KXWTIWHEN cumulative-date monotonicity arb (as farm) | INVESTIGATE | 3 | drop the arb; farm value `cap/mkt` 7.55 | MED | Farm yes, arb no (HFT race) | Propose KXWTIWHEN for farm allowlist; ignore the arb angle |
| KXCLAUDE / AI-model-release short-window density | INVESTIGATE/**DEAD** | 1-2 | `cap/mkt` 4.69, capSeries 18.8/d UB — but realized EV negative | MED-HIGH | No | **KILL/park:** discrete-date, no self-hedge, release-news informed flow, E8 excludes; books verified 0/0 & one_off |
| Ultra-long-dated meme farm (GTA6/Word-of-Year) | **DEAD** | 1 | `cap/mkt` 0.06-0.15 (trivial) | MED | No | **KILL:** 2030 close = 4.5yr capital lock (E1); mutex; sunset-orphaned |
| MrBeast-subscriber threshold snipe | **DEAD** | 1 | negligible; 2 markets, 0-ct books | MED | No | **KILL:** empty books, no LIP earn, naked rest trips breaker |

### THEME C — Incentive-program stacking

| Title | Verdict | Score | Grounded# | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| **Combo Incentive opt-in** (repriced from cost to revenue) | INVESTIGATE | 4 | basis ~1,275 maker ct/day (MEASURED); payout **UNQUANTIFIED** — pool/rate not API-discoverable | LOW | Yes to pursue (email); payout unknown | Get written yes/no on LIP-stacking + pool/rate; **do NOT opt in on silence** |
| Joint objective: at-reference maximizes LIP AND Combo fills | INVESTIGATE | 3 | no standalone $; de-risks Combo placement | LOW | Yes, no retune | Confirm Combo credits MAKER fills (not taker) in same email |
| Invert fill-avoidance ONLY for benign ladder churn (under Combo) | INVESTIGATE | 3 | break-even = per-ct bleed; gas −$0.0106/ct flips + at tiny Combo R | LOW | Clean ladders safe; E9 binds | After stacking confirmed, court churn only where credit > series bleed |
| GASW Combo expansion (stands on LIP alone) | INVESTIGATE | 4 | 48 fills/3d unmonetized; LIP gas −$0.0106/ct proven | LOW | Best fit — allowlisted | Pull GASW settled P&L before deepening depth |
| Recompute per-series break-even WITH Combo credit | INVESTIGATE | 2 | unmeasured; endogenous pool-share fixed-point | LOW | Analytical only | Defer until opt-in-and-observe yields effective R; reprices realized-leg only, not settle adverse-selection |
| Volume Incentive as subsidy on forced flatten-crosses | INVESTIGATE | 2 | caps at rebating a fraction of −$50.87/3d; rate UNQUANTIFIED | MED | Question free; action gated | Add one line to support email; do NOT let it drive more crossing |
| Post-sunset Combo survivorship (be opted in before 09-01) | PARK | 2 | pure option value; E2 undercuts payoff | UNK | Zero-cost rider on Combo opt-in | No standalone action |
| Pro-rata timing: concentrate volume in thin-competition windows | PARK | 2 | undiscoverable pre-opt-in | MED | No | Shelve until Combo credits observable |
| Measure DLP crossover capital (confirm the trap) | PARK | 2 | forfeit floor = all LIP ($25.21/2d) + Combo; DLP gain UNKNOWABLE | LOW | No — 98%/hr quoting infeasible | One-paragraph memo bounding the forfeit floor; do NOT sign an MM Agreement |

### THEME D — Timing / snapshot mechanics

| Title | Verdict | Score | Grounded# | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| Gas-daily relist-instant snipe (12:00Z boundary) | PURSUE | 4 | gas 24.6 UB; realistic ~8/d, delta only | LOW | Yes, scheduled event | Poll `open_time`, rest at-ref in first minutes, A/B vs mid-window |
| Gas-weekly Monday-relist expansion | INVESTIGATE | 4 | ~gas-daily/7 = order 1-3/d UB | MED | Yes, allowlisted | One full weekly cycle + settle-leg check |
| Measure snapshot/period cadence (ENABLER) | PURSUE | 3 | no direct $; unlocks/kills the timing family | LOW | Yes, read-only | Mine LIP credit timestamps, classify per-min/hourly/period-end |
| Reward-density-by-hour (competitor-drought shoulder hours) | INVESTIGATE | 3 | ceiling = closing part of the 2-6× model gap; low single-$/d | LOW | Yes, scheduling only | Hourly-poll gas books 1wk; find hours ≥target with fewer qualifiers |
| R3-flicker rotation queue (turn E9 into the edge) | INVESTIGATE | 3 | efficiency multiplier; rotation set caps ~9.3 vs gas 24.6 | LOW | Feasible on 0.6s loop; rotate flat-only | Measure % of cycles 2nd slot sits in an ineligible book; build only if material |
| Period-boundary re-arm (never miss opening snapshots) | INVESTIGATE | 2 | few opening-snapshot credits/period, material only on daily gas | LOW | Yes if it matters | Gate on cadence enabler + verify quoter drops quotes across rollover |
| Snapshot-instant tightening within 2¢ cap | PARK | 2 | fraction of −$50.87 realized-leg; addressable cheaper via velocity-selection | LOW | Marginal, order-churn risk | Park behind cadence + velocity-selection |
| Snapshot-cadence rotation (time-slice many books) | INVESTIGATE | 2 | 2-4× coverage IF cadence coarse, ZERO if continuous | LOW | Only if cadence proves coarse | Measure if 30s-rest vs full-period changes credit before believing |
| Anti-split: pile 100% size on reference tick | **DEAD** | 2 | $0 capturable — already rests at reference | LOW | N/A | **KILL:** CDF_OFFSET already OFF; only off-ref is a deliberate risk brake |

### THEME E — Arbitrage / locked-box (taker, held to resolution)

| Title | Verdict | Score | Grounded# | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| Ladder monotonicity dutch-book (P(≥X) ≥ P(≥X+step)) | INVESTIGATE | 3 | handful of sub-$2 net-of-2-leg-fee locks/wk at best; 0 in snapshot | LOW | Yes, read-only scanner | Build monotonicity scanner over ladder books we already poll; log violation freq + net-of-taker-fee edge 2wk before crossing |
| Mutex field dutch-book (buy field when YES asks <100c) | **DEAD** | 2 | MEASURED: KXINDIANPM field $0.96 (4c gross) but taker fees 11c → net −7c | LOW | No net-positive path | **KILL:** taker fees exceed the underround; screen only >~10c on all-extreme-priced fields |
| Mutex overround harvest (rest NO across field + LIP) | **DEAD** | 1 | MEASURED: NO-field ask $8.17 vs $8.00 payout (overround AGAINST us) | HIGH | No — partial fill = naked field, breaker veto | **KILL:** can't assemble wide fields at 20ct/$125 |
| Categorical sum-to-one overround snipe | PARK | 2 | rare, low-$ but genuinely locked when it appears | LOW | Marginal — empty/long-dated books | Fold into the monotonicity scanner, deprioritize |
| YES+NO < 100c crossed-book buy-both lock | PARK | 2 | 1-3c/lock when it fires; 0 in snapshot; latency-race entry | LOW | Marginal — we lose the sub-second race | Add `yes_ask+no_ask<100` flag inside the monotonicity scanner |

### THEME F — Exit / adverse-selection reduction (the loss half nobody owned)

| Title | Verdict | Score | Grounded# | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| Time-box presence to early/mid window (be flat before settlement) | INVESTIGATE | 4 | targets MEASURED −$34.21 settle-leg; halving ≈ +$17/period | LOW | Yes, late-life gate is the lever | Backtest settle-leg vs time-remaining; apply to gas first w/ rest-only flatten |
| Passive-reduce laddered exit (stop paying spread to flatten) — **GAP 1** | INVESTIGATE | new | attacks the −$50.87 realized leg (58% of total loss) | LOW | Yes, mechanics only | Rest reducing orders at reference on mutex=False ladders; pair with toxicity calendar so you exit while there's still time to rest out |
| External free fair-value skew within 2¢ cap — **GAP 3** | PARK/INV | 2 | de-tox the settle-leg; overlap set unenumerated | LOW | No as full feed; manual skew conceivable | Enumerate the actual Kalshi×external clean-overlap set first; drop if <3 tight pairs |
| Self-hedged LIP bracket on gas-daily ladder | INVESTIGATE | 4 | gas 24.6 UB basis; marginal value = bleed reduction; only ~2-3 rungs currently two-sided | LOW | Yes, we already rest GASD rungs | Count rungs simultaneously clearing 1000ct BOTH sides over a gas day; A/B bracket vs single-rung |

### THEME G — Discovery / process (repeatable pipelines)

| Title | Verdict | Score | Grounded# | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| New-series land-grab: weekly `/series` diff for day-one incentivized tickers | INVESTIGATE | 4 | discovery pipeline; hit-rate unproven | LOW | Yes, weekly REST diff (not low-latency) | Build read-only filter (`/series` diff × active-programs × fee-free ladder × target≤300); dry-run 1wk, count clean hits |
| Program-reissue first-mover (churn sniping) | INVESTIGATE | 3 | 118,731 lifetime ids vs 2,329 active = heavy churn; actionable non-toxic rate UNMEASURED | MED | Yes envelope-wise (cron-scale rivals, not HFT) | Multi-hour `/incentive_programs` diff: new-id → book-thinness → fee-free-ladder |
| Period-reward spike detector (snipe pool boosts) | INVESTIGATE | 3 | event-driven; only pays where boosted series has few qualifiers | UNK | Yes to run monitor | Log `period_reward` per id daily 1-2wk; alert only on boost × <3 two-sided competitors |
| Fresh-program first-mover (rest before saturation) | PARK | 2 | fresh book verified 0/0; R3 pays $0 until 3rd party brings depth | UNK | Marginal — E2 blocks earning | Only alert on new programs landing on ALREADY-liquid markets |
| New-listing first-mover monitor (<1h old) | INVESTIGATE | 2 | earn thesis E2-dead; salvage = discovery tool | MED | As discovery: yes; as earn: no | Nightly diff of `/incentive_programs`, flag fee-free non-mention mutex=False ladders for review |
| Overlapping-period double-dip monitor | PARK | 2 | 0/2,329 programs share a market_ticker (dormant) | LOW | Yes to arm (counter) | Add ticker-collision counter to existing pull; nothing to trade until it fires |

### THEME H — Guards, filters & reusable selection rules

| Title | Verdict | Score | Grounded# | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| Velocity-matched selection (only slow-reference underlyings) | INVESTIGATE | 4 | unmeasured; target = reproduce gas −0.0106/ct profile | LOW | Yes, read-only measurement | Poll mids/0.6s per candidate ladder 1d, rank by quiescence |
| Formula-param trap guard (DF≠5000 OR target∉{300,1000}) | PURSUE | 3 | loss-avoidance; exactly 2 programs trip it now (KXGOVWINS DF2500+target10000) | LOW | Yes, trivial two-field filter | Add hard pre-quote gate rejecting off-spec DF/target |
| Two-field formula pre-screen (target_size_fp + discount_factor_bps) | PURSUE | 3 | rejects 2 traps; surfaces the 46-program 300-target tier | LOW | Yes, read-only | Add DF=5000/target≤1000 filter; pull the 46 for inspection |
| 300-ct target-size tier (nearer-marginal at 6.7% vs 2%) | INVESTIGATE | 2-3 | 46/2,347 at target 300 (VERIFIED); clean subset thin | MED | Yes for clean subset | Adopt target≤300 as standing pre-filter; measure GENERICBALLOT/EOWEEK two-sided% by hour |
| Anti-adverse-selection signature as reusable series screen | INVESTIGATE | 3 | no direct $; validates vs −$87.59/51-ct fingerprint | LOW | Yes, read-only | Codify (continuous ladder / mutex=False / no leak channel / >30d settle); backtest ranking vs 51 settled contracts |
| Longest-window ladders as attention-efficiency axis | INVESTIGATE | 3 | selection heuristic; KXEOWEEK instance thin (2 markets) | MED | Yes as a rule; EOWEEK a poor pick | Sort active programs by (window_days × cap/mkt), filter fee-free non-mutex ladders, pick top non-EOWEEK |
| Sunset-cliff endgame: concentrate as rivals exit | PARK | 2 | contingent on pools staying funded through late Aug | LOW | Monitoring posture only | Weekly-track pool $/day on core ladders through Aug; lean in only if pools hold AND competitors thin |
| Sunset front-load + renewal first-mover | PARK | 2 | runway-bounded; front-load amplifies §M8 net-neg trading leg | LOW | 'Hurry' framing free, not an edge | Keep E6 as payback filter; watch feed for a renewal id after 09-01 |
| DF=0.75 gentle-decay program hunting | PARK | 2 | 2/2,329 at DF=0.75, both KXGOVWINS 10k-target/expiring | LOW | Yes to arm a flag | Act only if a fee-free ≤1000-target DF=0.75 instance ever appears |
| Sub-300 target-size watch (path to being marginal) | **DEAD** | 1 | live floor is exactly 300; depth (20k-90k/side) sets marginality, not target | UNK | No | **KILL:** premise refuted — no sub-300 exists, and lower target wouldn't rescue a book we can't reach |
| Stale-mid N=0 snipe on wide-spread rungs | **DEAD** | 2 | structurally $0 — wide spread = thin = R3-fail | MED | No | **KILL:** cheap-to-be-reference state and earnable state are mutually exclusive |
| Be the reference-setter (seed a fresh 300-ct program) | **DEAD** | 1 | negative EV — one-sided seed fails R3 + adverse fill | HIGH | No | **KILL:** trips E2, E5 (naked breaker), E8 |
| Mutex full-event basket (reframe throttle mis-fire as spread) | **DEAD** | 1 | net-negative after adverse fills on illiquid legs | HIGH | No | **KILL:** E2 blocks reward-capture; needs a per-event guard that doesn't exist |

### THEME I — Directional / cross-venue snipes (mostly infra-blocked)

| Title | Verdict | Score | Grounded# | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| Gasoline-CPI print snipe (informational edge on KXUSGASCPI) | INVESTIGATE | 4 | sub-$5/cycle, 1-2 cycles before sunset; fee-free confirmed | MED | Yes — we ingest the input, no low-latency | Build MTD gas-CPI nowcast off AAA/EIA, backtest vs last 2-3 prints, re-check book depth pre-print |
| Netflix Top-10 scheduled-publish snipe (Tudum clock) | INVESTIGATE | 3 | few $/weekly cycle IF post-publish book lag exists; ~5 cycles runway | MED | Yes if lag exists | Watch orderbooks across ONE Tudum event; confirm resolution source + measure minutes-of-lag |
| Gas-daily far-rung fade (sell tails ~0 fair value) | INVESTIGATE | 3 | ~$1.20-4.80/d gross if 2-4 fades/d; sub-$1/d after 1c fee floor | LOW | Yes — our flagship book | Log far-rung bids 1wk, count ≥3c net-of-fee fadeable tails, gate on energy-headline days |
| Election/primary first-mover (KXVOTEPRIMARY) | PARK | 2 | capSeries 13/d UB, ~$4/d; near-zero while books empty | MED | Marginal — E2 + E9 | Measure if books ever independently two-sided at ~1000ct near a vote date |
| Temp settlement snipe (informed taker on our maker-loser) | PARK | 2 | unmeasured, scope-fenced | LOW | No — late-life gate + temp gate 07-27 + WB scope wall | Log as proof toxic-maker = informed-taker; revisit only if temp extended + carve-out authorized |
| Crypto price-at-expiry snipe (BTC/ETH spot vs book) | PARK | 2 | not reward-dependent; late-life-gate blocked | MED | No — needs guard carve-out + a real constantly-expiring series | Find/confirm an eligible expiring BTC series, measure gap frequency first |
| GPU-ladder settlement-lag (public listing vs stale book) | PARK | 2 | contingent on resolution-source clarity (unread) | MED | Marginal — empty long-dated books | Read GPU-MAX resolution rules to find the settling source before any claim |
| Polymarket book as free fair-value feed to de-tox Kalshi | PARK | 2 | overlap set unenumerated; taker edge eaten by fees | LOW | No — needs feed infra + contract-mapping we lack | Enumerate clean overlap pairs; drop if <3; scope-caution (Kalshi-only session) |
| WTI-futures leader snipe (CME move not yet in book) | PARK | 1 | infra-blocked; edge likely negative after 10-15min data lag + fee | MED | No — no live WTI feed, we lose the race | Shelve unless a near-real-time WTI feed is ever wired |

### THEME J — Micro-tactics (low ceiling)

| Title | Verdict | Score | Grounded# | Tox | Feasible? | Next step / kill |
|---|---|---|---|---|---|---|
| Adjacent-rung two-sided self-provision at thinnest clearing rung | INVESTIGATE | 2 | ~nil credit-share gain given 0/304 marginality | MED | Partial | Measure if any rung is BOTH clearing-target AND thin enough for 20ct to be pivotal (expect none) |
| Penny-wall / round-number spread capture | INVESTIGATE | 2 | ~1-3c/fill × low fill-rate; conflicts w/ at-reference LIP | LOW | Marginal | Measure fill-rate one tick inside a wall vs LIP credit lost; drop unless net-positive |
| Favorite-longshot-bias harvest (naked NO tails) | PARK | 2 | 1-3c premium × basket; naked = breaker veto | LOW | No as stated (directional) | Only as a self-hedged ladder within one mutex=False event |
| Cheap deep-tail rung farming (flat size-score, low tox) | INVESTIGATE | 2 | lower LOSS not more reward; tails fail R3 (14/14 empty live) | LOW | Rarely | Per-rung scan for tails clearing 1000ct both sides (likely tiny set) |
| WILD: nudge the reference on a thin 300-target book | **DEAD** | 1 | ~zero-to-negative; ToS/CFTC manipulation exposure | HIGH | No | **KILL:** our references are EXTERNAL (AAA index); book-derived case is illegal regardless |
| Short-window KXLIUKELIMINATION-class 12.7h programs | **DEAD** | 2 | ~$188/d UB but $0 earnable — mutex/discrete-event toxic | HIGH | No | **KILL:** E8; keep only the shape note for a future threshold-ladder instance |

---

## 3. QUICK WINS vs BIG BETS vs MOONSHOTS

**QUICK WINS** — cheap, reversible, high-EV, mostly today:
- **Go DEEP into gas** (score 5) — pure size increment, no new config, best density on the venue.
- **Load gas-weekly KXAAAGASW** (4) — already allowlisted, same shape, just deploy small + measure.
- **Window-length arbitrage rule** (4) + **formula-param trap guard** (3) — free ranking/filter rules, always-correct, zero risk.
- **Front-load capital by early August** (3) — a funding-timing decision, not a trade.
- **Send the Combo/Volume support email** (4) — one email, unlocks the highest-value untapped channel.
- **Snapshot-cadence enabler measurement** (3) — read-only mining of data we hold; gates the whole timing family.

**BIG BETS** — real upside, need an allowlist add + a week of measurement:
- **GPU-MAX ladder family** (5) — start with KXH200MAX + KXA100MAX.
- **KXHOODA** (5) — pending the 48h persistence check.
- **KXWTIWHEN** (4) — pending a working orderbook re-read.
- **KXNETFLIXTOPVIEWSMOVIE** (4) + **KXRT** (4) — orthogonal-sector diversification with catalyst blackouts.
- **KXUSGASCPI** (4) — the one we can skew with a model we already run.
- **Time-box / passive-reduce exit** (4 + GAP 1) — attacks the loss side, which no selection idea touches.

**MOONSHOTS** — speculative, gated on unknowns or infra we don't have:
- **Combo aggressive "lean into more fills"** — probably −EV until the pool rate is observed.
- **Gasoline-CPI informational snipe** — needs a nowcast model built + a liftable book near the print.
- **Netflix Tudum publish-lag snipe** — lives or dies on whether Kalshi settles instantly on the same feed.
- **Program-reissue churn sniping** — needs a multi-hour diff study to prove a non-toxic reissue rate exists.
- **Ladder monotonicity dutch-book** — a cheap always-on scanner, but violations are rare/small on tight pro books.

---

## 4. COMPLETENESS-CRITIC GAPS (folded in as fresh candidates)

The 10 ideation lenses were almost all about **which series to ENTER**. Three blind spots nobody owned:

- **GAP 1 — the EXIT half of the loss got ZERO ideas.** The realized leg (−$50.87, 58% of total −$87.59) is entirely "crossing the spread to flatten" (temp at 38-52% taker). → **Passive-reduce laddered exit** (Theme F): rest reducing orders at reference, let the ladder's own self-hedge drain inventory. Kill condition: a hard settlement deadline forces a cross anyway — so it only works paired with an early-exit toxicity calendar (GAP 8 / Time-box).
- **GAP 2 — the binding constraint (E9: only 2 books live) is an AUTOMATION gap, not a strategy gap.** We're live in 2 series despite a ~13-series allowlist. If the quoter loop can rest in N books, EVERY selection idea unblocks at once and the "which 3rd series" debate is moot — **fixing the loop is worth more than winning the selection argument.** ⚠ **Must verify WHY we're at 2 before assuming it's free:** if the 2-book cap is a risk/monitoring guard (naked-risk breaker can't supervise N books) rather than a loop limit, it's load-bearing and stays. **This is the single highest-leverage investigation on the list — it's a multiplier on the whole portfolio.**
- **GAP 3 — external free fair-value as an adverse-selection REDUCER (skew), not a directional snipe.** Use a free external mid (spot/CME/Polymarket) only to decide which side of the 2¢-capped pair to lean heavier/thinner. Never directional, never crosses, keeps 100% LIP eligibility. Kill: only worth it where the external feed genuinely LEADS the Kalshi book.
- **GAP 4 — snapshot-synchronized flicker.** LIP score is a point-in-time snapshot; rest around the snapshot, cancel between, to earn the same credit with fewer fill-hours. Attacks BOTH loss legs. Enabler: needs the cadence measurement first. Note: at 20ct vs 1000ct target we have no queue priority to lose (E2), which actually makes flicker CHEAPER for us than for a real maker.
- **GAP 5 — crypto threshold-ladder LIP farm.** KXBTCD / KXETHD price-above/below ladders are the structural TWIN of gas (deep continuous underlying, mutex=False), confirmed `fee_type=quadratic` = maker-free. Named elsewhere only as a directional snipe; as a fee-free gas-shape FARM it belongs on the candidate list.

---

## 5. CROSS-CUTTING PLAYS (2+ ideas that combine)

1. **Combo-stack × gas-daily concentration** — 60% of our ~1,275 maker ct/day is already in gas-daily, our least-toxic ladder. If Combo stacks, "Go DEEP into gas" (5) simultaneously maximizes LIP snapshot AND Combo fills with no retune (the at-reference config already does both). The single highest-value combination on the board — *if the email comes back yes.*
2. **Fix the 2-book loop (GAP 2) × the entire selection menu** — GPU family, HOODA, WTIWHEN, gas-weekly, Netflix, RT all unblock at once the moment the quoter can sustain N books. Selection ideas are worth far more AFTER this than before.
3. **Cadence enabler × flicker × time-box** — measure snapshot cadence once (Theme D enabler), and it simultaneously decides whether snapshot-flicker (GAP 4), rotation-queue, period-boundary re-arm AND the timing snipes are real or collapse to "just be present all period." One cheap measurement gates six ideas.
4. **Velocity-matched selection × every farm target** — the velocity screen (H) is the mechanism behind why gas converts; run it as the gate on GPU/econ/equity/entertainment ladders before allowlisting any of them, so we only add slow-reference books that reproduce the gas profile.
5. **Time-box exit (F) × passive-reduce (GAP 1) × toxicity calendar** — the exit trilogy: leave early (before informed flow picks the strike), leave passively (rest, don't cross), and know WHEN to start leaving (per-series catalyst calendar). Together they attack the −$34 settle-leg AND the −$50 realized-leg — the whole loss.
6. **Monotonicity scanner × sum-to-one × crossed-book** (Theme E) — one read-only ladder-book poller with three flags (`P(≥X) violations`, `field YES-sum<100`, `yes_ask+no_ask<100`) captures all three locked-box arbs at near-zero marginal cost.
7. **New-series land-grab (G) × velocity screen (H) × formula-param guard (H)** — the discovery pipeline: weekly `/series` diff → filter fee-free non-mutex ladders with target≤300 and DF=5000 → velocity-check → surface the next KXHOODA-class ladder before it saturates.

---

## 6. HONEST CAVEATS (read before acting on any number)

- **§M7 — every $ figure is an UPPER BOUND.** `cap/mkt` and capSeries are R1-normalized, R3-adjusted where measured, and STILL over-predict receipts 2-6×. Divide by ~3 for realism. Receipt truth: LIP was $25.21/2d; the trading side is net-NEGATIVE. Do not present any headline figure as expected earnings.
- **R3 two-sidedness is a hard gate.** A snapshot whose book fails ~1000-ct Target on EITHER side pays NOBODY. Several "live 6/6 two-sided" census claims could NOT be reconfirmed this session — the orderbook endpoint returned 0/0 even on our own actively-quoted gas flagship, so those reads are invalid (broken/off-hours), NOT proof of dead books. **Every allowlist candidate needs a two-sided depth reconfirm during US session before it counts.**
- **E2 — we are never the marginal maker.** 0/304 at 20ct vs 1000-ct target. We ride others' depth; we cannot create it. Every "be first / seed the book / rescue a thin rung" idea is structurally $0-or-worse.
- **Sep-1 sunset over everything.** Both LIP and Volume expire 2026-09-01, ~5.5 weeks out. Anything with payback past that = near-zero. Front-loading is right BUT amplifies the net-negative trading leg — don't manufacture volume to chase it.
- **Infra/size envelope:** ~$125 deployable cash (~$198 total), 20-40ct quotes, 0.6s polled REST (no streaming, no news feed, no sub-second reaction), 2 books sustained live, risk guards live (naked-risk breaker, $40 daily halt, 2¢ pair cap, late-life gate).
- **Snipes that need infra we DON'T have yet:** WTI-futures leader (no WTI feed), crypto price-at-expiry (needs a late-life carve-out + a real expiring series), Polymarket fair-value feed (no wired feed + contract-mapping), any sub-second race (E4 — we lose every one). These are PARK/DEAD for good reason.
- **Combo is invisible to our pipeline.** The API exposes only `incentive_type='liquidity'` across all 2,347 programs and every status. Combo/Volume/DLP exist only in the fee PDF + Help Center + two support replies. We can size the volume BASIS (~1,275 ct/day, measured) but NOT the payout — genuine info gap, not a modelling choice. **Never opt into Combo on support's silence** (the DLP mutual-exclusion trap proves eligibility wording is exactly where the danger hides — signing an MM Agreement forfeits ALL LIP income).
- **Toxicity is orthogonal to reward size.** A fat pool on a mention/discrete-event/mutex structure is still excluded (E8). Prefer threshold ladders (`greater_or_equal`, mutex=False, adjacent rungs self-hedge).
- **GUESS flags:** the "2-3x capital absorption knee" is a GUESS pending per-strike depth. The exact taker-fee coefficient (`ceil(0.07·C·p·(1−p))`) is a GUESS per series — verify before subtracting from any arb edge.

---

## 7. RECOMMENDED FIRST 3 MOVES

All reversible, operator-gated, cheapest-highest-EV. In order:

1. **Send the Combo/Volume support email (zero capital, zero risk, highest untapped upside).** Ask verbatim: *"If I opt into Combo, does my account keep earning LIP — yes or no?"* plus *"What is the Combo pool size and per-contract rate?"* plus one line: *"Does Volume Incentive stack with LIP, and does it credit maker or taker volume?"* This unlocks the ~1,275 ct/day basis we currently book as pure cost. **Do NOT opt in until BOTH the stacking yes/no AND the rate come back in writing.**

2. **Go DEEP into gas + load gas-weekly — measure the non-marginal knee (uses capital we already have).** Increase gas-daily size on the ~6-7 live two-sided strikes and deploy small into the already-allowlisted KXAAAGASW. In parallel, pull per-strike competitor depth to find the size where our score share stops being negligible, and run `kalshi_settlement_pnl.py` on the first GASW resolutions to confirm it behaves like gas-daily, not temp. This is the highest-density, lowest-friction deploy on the venue.

3. **Investigate WHY we're at 2 books (GAP 2) + run the two free measurements that gate everything else.** Determine whether the 2-book ceiling is a quoter-loop limit (fixable, unblocks the whole selection menu) or a load-bearing risk guard (stays). Simultaneously run the two read-only enablers: (a) mine the LIP credit timeline to classify snapshot cadence (gates the entire timing family), and (b) reconfirm two-sided depth in US session on the top allowlist candidates (KXH200MAX, KXA100MAX, KXHOODA, KXWTIWHEN) before spending an allowlist slot. Add the formula-param trap guard (DF≠5000 or target∉{300,1000}) and the window-length tiebreaker as free standing rules while you're in there.

---

*Scope note: read-only brainstorm, Kalshi venue only. No trades, deploys, config or module edits were made. Every idea above is executable on Kalshi and competes for the same ~$125 deployable cash. All $ figures §M7 upper bounds unless labelled MEASURED. Branch `claude/maker-kalshi-live`.*
