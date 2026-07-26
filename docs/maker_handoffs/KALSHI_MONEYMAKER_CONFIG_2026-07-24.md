# KALSHI MONEYMAKER CONFIG — Path to Positive (2026-07-24)

**Bot state:** WOUND DOWN / reduce-only. This doc is a counterfactual on the historical tape plus a forward config. Nothing here was deployed; author is READ-ONLY.

---

## 1. RESOLUTION (one line)

**DEPLOY-AFTER-ONE-MEASUREMENT.** Honest expected net **≈ $0.0/day** (band **−$2 … +$1/day**, downside-skewed). The clean-compute basket is *plausibly* positive but its entire positive sign rests on ONE unmeasured number — **clean-family balanced in-window fill cost per contract**. Measure that one number first; deploy only if it clears the pass threshold in §6. Gas does NOT flip positive and is excluded.

Why not DEPLOY-THIS-CONFIG: after refuters the surviving net is −$0.10/day worst-case, +$0.05…+$0.21 central — statistically indistinguishable from zero, and the one load-bearing input (clean fill cost) has zero receipts. You cannot receipt-prove a positive today.

Why not NARROW / stop: the market has already been narrowed to the correct single lever (the self-averaging NVIDIA compute-monthly family is the menu's #1 risk-adjusted pick). What's missing is not a better lever — it's the measurement that confirms this one. That is precisely the DEPLOY-AFTER-ONE-MEASUREMENT signature.

---

## 2. Fill-leg decomposition — RESOLVES the review's key assumption

The prior "retire" verdict rested on one unproven claim: that the gas **−$23.14 fill leg** is IN-window intraday adverse selection no gate can remove. Receipt-grade tape replay (305 KXAAAGAS fills, snapshot 2026-07-23T23:15Z, validated by `kalshi_settlement_pnl.py` 51/51 revenue reconcile, 47/47 CSV cross-val) settles it:

| Bucket | Gas fill-leg (snapshot, 51 ct) | Removable by | Verdict |
|---|---|---|---|
| **Out-of-window** | **$0.00** (305/305 fills IN their market's TRUE LIP reward window) | `KALSHI_CAPTURE_GATE` | Gate removes **$0** here |
| **In-window, NAKED** (forced-taker exit) | **−$3.92** | `KALSHI_PRECLOSE_FLATTEN` | Fixable — but small |
| **In-window, BALANCED** (live-paired maker, trend-delta) | **−$14.19** | *nothing named* — structural to trending days | **NOT fixable** |
| **Total (snapshot)** | **−$18.10** | | |

Reconciliation notes (blunt):
- Internal recon is EXACT: 0.00 + (−18.10) = −18.10; (−3.92) + (−14.19) = −18.10.
- Snapshot measures **−$18.10** = ~78% of the review's **−$23.14**. The −$5.04 gap is later 26JUL24 + full 26JUL27 fills not yet placed at snapshot time; those markets have **zero taker fills** → same two-sided paired character → folding them in deepens BALANCED to ~**−$19.2**, leaves NAKED ~**−$3.9**. Direction and verdict unchanged. The exact −23.14 cannot be reproduced without the 58-contract export (not local).
- The **−$14.19 is one trending session** (26JUL24 daily, YES fell 0.87→0.23 monotonically while the bot quoted two-sided). The other three daily events + weekly are wash-to-positive on the maker leg (26JUL23 +0.74, 26JUL27 +1.25, 26JUL22 −0.14). "Structural" = structural to **trending days**, on a tiny sample (~1 trend day of 5 events).

**Consequence for the review's assumption: it HOLDS for gas.** Capture-gate removes ~$0; pre-close-flatten removes at most ~$3.9 of ~$18–19. The residual is in-window, two-sided, trend-delta adverse selection — not gate-addressable. **Gas does not flip positive.** The path to positive is therefore NOT rehabilitating gas; it is the clean-compute family, which has the opposite (self-averaging) underlying.

Settle-leg (separate, receipt-anchored, gas): measured settled-gas settle-leg **−$8.01** (GASD-23 dominates at −$8.20), `naked_fraction = 1.0` (unfloored anti-straddle: long-NO-low + long-YES-high, the shape `ladder_pairing` never floors). Pre-close-flatten central recovery ~27% (residual −$6.08), and can go **underwater** if it fires late (`preclose_taker_failed`, thin book). The family-wide −$23.86 settle claim is NOT reproducible locally (aged out of both CSV and snapshot); method to reconcile in §6 note. This reinforces: gas out.

---

## 3. The config (flags / markets / size)

Measurement basket — the clean compute-monthly lane, minimum footprint:

```
MARKETS  = KXH200MS, KXB200MS, KXB200MON        # NVIDIA H200/B200 monthly-AVERAGE compute price
SIZE     = 20 ct/side, near-money strikes only  # ~3-4 strikes/market at ~$230 capital
QUOTING  = two-sided balanced, join-at-BBO
```

Flags (all in `live.env`):
```
KALSHI_NETEV_GATE=1        # per-family net-positive only -> CORRECTLY excludes gas AND temp
KALSHI_CAPTURE_GATE=1      # in-window quoting; ~$0 benefit on this family, cheap insurance
KALSHI_PRECLOSE_FLATTEN=1  # kills naked settle residual (weak guard; see refuter 3)
KALSHI_FUNDING_GATE=1
# EXCLUDE (do not quote): KXAAAGASD, KXAAAGASW  (receipt-proven structural loss, -$47.86 lifetime)
# EXCLUDE: temp KXTEMP* (netev net_per_day -$6.53), KXPRIMARYMOV (mutex categorical),
#          earnings/movie/election single-event ladders (KXRT/KXDPZ/KXCOINBASE/KXWING(A)/KXVOTEPRIMARY/KXDWTSCAST)
```

Why these three: menu ranks the compute-monthly family #1 on risk-adjusted maker math — monthly-average underlying is **self-averaging → near-zero intraday adverse selection** (the exact opposite of gas's fill problem), fee-free, 155–179h horizon (clears the ~Sep-1 sunset), clean `greater` threshold ladders, mutex=False. KXB200MON/KXH100MON also carry the highest per-contract reward share (21.6% / 19.2% @100ct); KXB200MON is already in the allowlist.

Per-market modeled net at 20ct (optimizer, fill_cost assumed 0): KXH200MS +$0.19/day, KXB200MON +$0.21, KXB200MS +$0.12 → **gross reward ≈ $0.52/day**. **That $0.52 is the entire upside**, and it assumes the unmeasured fill_cost = 0.

---

## 4. Deploy path + go/no-go gate

Deploy is GATED on §6 measurement passing. When/if it passes:

```bash
# On VPS, deploy branch HEAD (d9dfbee has the _preclose_naked_flatten logic) to the kalshi live slot.
# Kalshi is a SEPARATE session/service; per RULE FIVE, STOP and get operator sign-off before
# touching anything shared. This slot (/opt/pa2-maker-kalshi-live) is Maker-owned.

TARGET=/opt/pa2-maker-kalshi-live
cp $TARGET/live.env $TARGET/live.env.bak.$(date +%Y%m%dT%H%M%SZ)   # .bak the prior env
# set the §3 flags + MARKETS in $TARGET/live.env
md5sum $TARGET/live.env                                            # md5-gate: record, compare post-write
# rsync/checkout branch HEAD into a fresh release dir, md5-verify the quoter binary/module,
# then atomic symlink swap (mirror deploy.sh pattern), 90s health check.
# rollback: restore live.env.bak.* + symlink to prior release.
```

**Go/No-Go gate — first 3 settlement events (NOT cycles; clean markets settle every 155–179h, so this is ~2 weeks):**
1. **Fill leg:** run `kalshi_settlement_pnl.py` + `kalshi_netev_calibrate.py` on the clean-family fills. Balanced in-window fill P&L per ct must be **≥ −0.001 $/ct**. If it drifts toward gas's −0.00523, ABORT — the self-averaging thesis is false.
2. **Settle leg:** any carried inventory must be the FLOORED shape (long-YES-low + long-NO-high), `naked_fraction` low. If the bot builds the unfloored anti-straddle (as it did on gas GASD-23), the pre-close-flatten guard is known-weak — halt and add a ladder-shape gate before continuing.
3. **Reward realization:** credits actually posted vs modeled. The menu reward model is receipt-shown to overstate gas credits **10–50×** ($186/day modeled vs $1.20/day real). If clean credits track that miss, the $0.52 gross is fiction and the basket is negative on any fill cost — ABORT.

If any of the three fails, the config does not make money; do not scale, revert to wound-down.

---

## 5. What each refuter found + surviving net

| Lens | Attack | Net after ($/day) | Holds? |
|---|---|---|---|
| **clean-cost-wishful** | fill_cost=0 has zero receipts; both measured maker families (gas −0.00523/ct, temp −$6.53/day) lose on trend; clean's 117–141 fine near-money strikes have *higher* per-strike gamma than gas's 17 coarse ones → per-ct cost could equal/exceed gas. Porting gas cost → basket collapses to break-even/negative. | **−0.10** | config fails |
| **share-fragility** | $0.52 reward scored vs a static book; share = Q/(comp+Q) decays as competitors thicken. Live-book 2×/3× stress → reward keeps 57%/40% → $0.30/$0.21. Thin monthly strikes keep only 16–37% under one +100ct competitor. | **0.21** | config fails |
| **flatten-and-tail** | settle_cost=0 assumes flatten catches every naked residual; flatten is receipt-proven ~27% effective and can go underwater; clean family is the SAME ladder that built the unfloored anti-straddle; monthly cadence → only ~4–6 settlements to sunset → one fully-missed tail (~−$3.3) = −$0.085/day. | **0.05** | config fails |
| **materiality-and-sunset** | $0.3/day × 39 days = **$11.70 total** on $230 capital, atop a −$2/day (=−$78, −34% of capital) stated low. One gas-style trend day (−$14.19) > entire 39-day expected upside. Reward model overstates 10–50×. A single monitoring session costs more operator time than $11.70. | **0.00** | config fails |

**Worst-case surviving net: −$0.10/day. No fatal refuter.** Every refuter is "material," none is "fatal" — meaning the config is not receipt-provably a money-loser either, it is receipt-provably *marginal*. The honest read: expected net is within noise of zero with a fat negative tail, and every lens converges on the same root cause — **clean-family fill cost is unmeasured.** That is why the resolution is measure-first, not deploy and not stop.

---

## 6. The open measurement (the single gate)

**Measure: clean-family balanced in-window fill P&L per contract**, on KXH200MS / KXB200MS / KXB200MON.

**How to run:** quote the §3 basket at **minimum size (10–20 ct/side, near-money only)**, two-sided, join-at-BBO, for **≥ 2 settlement events per series** (~2 weeks — note this burns ~½ the 39-day LIP runway; see materiality caveat). Then:
```bash
cd .../kalshi_live
python kalshi_settlement_pnl.py        # fresh signed GET /portfolio/settlements + /portfolio/fills;
                                       # surfaces clean-family carried positions + settle legs
python kalshi_netev_calibrate.py       # per-family net = credits - fill P&L; read balanced in-window per-ct
```
Split the resulting fills NAKED vs BALANCED exactly as the gas decomp did (taker-flag / live-pairing), and compute **balanced in-window $/ct**.

**Pass threshold:** balanced in-window fill cost **≥ −0.001 $/ct** (materially better than gas's −0.00523). Rationale: gross reward ~$0.52/day at the basket's implied ~90–260 ct/day turnover puts break-even fill cost around −0.002…−0.006 $/ct; requiring ≥ −0.001 leaves margin for the reward-model haircut and share decay. Also require realized credits within ~3× of model (not the 10–50× gas miss) and carried inventory in the floored shape.

- **Pass** → the basket is net-positive; keep deployed through sunset, monitor the §4 gate.
- **Fail** → fill cost eats the reward; do NOT deploy for money. The clean lane is then non-positive and the honest next step is a ladder-shape/self-hedge gate experiment (hold only the floored pair) OR leave wound-down.

**Materiality caveat, stated plainly:** even a passing measurement caps forward upside at ~$11.70 over the 39-day sunset on $230 capital, and knowledge of clean-family fill cost has near-zero forward value once LIP ends ~2026-09-01. The measurement is the honest gate to a *positive sign*; it is not a claim that the dollars are large.

---

## Change Log
```
## CONFIG SYNTHESIS: 2026-07-24
Deliverable: KALSHI_MONEYMAKER_CONFIG_2026-07-24.md
Resolution: DEPLOY-AFTER-ONE-MEASUREMENT
Honest net: ~$0.0/day (band -$2..+$1), worst-case after refuters -$0.10/day, no fatal refuter
Key assumption resolved: gas fill-leg is IN-window two-sided BALANCED structural (-$14.19 of -$18.10),
  NOT out-of-window and NOT primarily naked -> gas does NOT flip positive, stays excluded.
Path to positive: clean compute-monthly family (KXH200MS/KXB200MS/KXB200MON), gated on one
  unmeasured number (clean balanced in-window fill $/ct, pass >= -0.001).
Deploy: gated; branch HEAD/d9dfbee to /opt/pa2-maker-kalshi-live, .bak env, md5-gate, operator sign-off (RULE FIVE).
Source: receipt-validated (kalshi_settlement_pnl.py 51/51 + 47/47 CSV; 58/58 settlement reconcile anchor).
No trades run, no config changed, no deploy performed (READ-ONLY, bot wound-down).
```

---

## TREND STAND-DOWN BACKTEST + RECONCILE (2026-07-24, recs #3/#5)

### (1) Reconciled complete lifetime (aged-out folded in)

Live signed GET `/portfolio/settlements` (58 settled ct) + `/portfolio/fills` (531 fills), pulled 2026-07-24 ~17:30Z via `kalshi_attribution_ledger.get_paginated` (prod_key.pem), replayed through `kalshi_settlement_pnl.py`. EXACT, method-independent.

| Leg | USD |
|---|---|
| **Lifetime realized** | **−122.5715** (58 ct) |
| Fill leg (total) | −72.5124 |
| — fill leg, BALANCED (maker two-sided / ladder-delta) | −32.6975 (−0.02328/ct on 1404 reduce-ct) |
| — fill leg, NAKED (taker-reduce minus all fees) | −39.8149 |
| — fill leg, in-window / out-of-window | −72.5124 / **0.00** (531/531 fills in TRUE reward window) |
| Settle leg (total) | −50.0591 |
| **Aged-out folded in** (7 ct, KXAAAGASD-26JUL24 strikes 4.100–4.130, settled 2026-07-24T12:55:26Z after snap_task3's 07-23 cutoff) | **−34.9769** (matches receipt −34.98) |

Reconciliation: fill leg (−72.5124) + settle leg (−50.0591) = −122.5715. Model-A settlement-row replay == Model-B tape replay 58/58 to the cent; revenue reconciles 58/58; CSV cross-validation 47/47 on contracts settled at/before the export cutoff. Prior snap_task3 measured only 51 ct / −87.59; the 7 aged-out ct (−34.98) close the gap to lifetime.

### (2) Trend-day baseline and best-rule net improvement (KXAAAGASD-26JUL24, gas daily, 146 maker fills, 7 strikes)

- **Named-target BALANCED maker fill-leg baseline:** **−14.19** (snap_task3 gas subset) / **−19.126** (full live tape, 7 aged-out strikes folded in). YES value fell 0.87→0.23 monotonically while quoting two-sided; underlying move 1.03× median (normal vol, not a spike — recurs).
- **Best rule by raw net:** MOM X=15c/Y=20m (per-strike, adverse-side, latched). **best_net_improvement = +10.22** (fill-leg loss_avoided +1.637; residual_inventory_pnl +8.758; reward_foregone −0.18). Trend-day fill after best rule = −8.91; does NOT turn to wash (≥ −1.00).
- **Refuter-corrected worst case:** **+0.31** net/event (materiality-and-recurrence lens; three other lenses land at +1.46 robust fill-leg, +8.9 two-sided-reward-corrected). No fatal refuter. ~86% of the +10.22 headline (+8.758) is a directional net-short residual on strikes 4.105/4.110 that settled NO only because gas fell monotonically — non-repeatable, priced to ~0. Robust maker-quality benefit = fill-leg +1.637 only.
- **Already-realized-before-trigger (unavoidable):** of the −19.126 baseline, only **+1.637** is avoidable by a stop-adding rule; **−17.489** is already realized/unwound on reducing fills the rule permits (LOCKED pre-trigger +1.985; ~90% of the 4.110 strike loss locks post-trigger: running_realized −4.13 at 18:57Z trigger → −9.71 final). The ladder-delta loss is structurally not gate-addressable by stand-down.
- Aggressive variants relocate rather than remove: INVCAP Z=20 fill-leg +5.643 but residual −3.217 (net +1.46); INVCAP Z=30 net −3.77; RUNS N=4 fill +4.751 residual −7.171 (net −2.86).

### (3) Reward-foregone and false-trigger cost

- **Reward foregone (best rule):** +0.18 linear (prevented_add 18 ct × R_ct $0.01; at R_ct $0.02 → net moves +10.22 → +10.04). Two-sided-reward-structure correction (R3 market cliff / R4 half-share) raises worst-case foregone to ~$1.5, which erodes the robust +1.637 fill-leg benefit to ~+0.1 on maker-quality grounds.
- **False-trigger cost (26JUL23 gas control, gradual drift, +0.74 maker leg):** **$0.00** — best rule prevented 0 ct; fire/no-fire band is 13c (no-fire) to 16c (fire). Tightening to 10c/15m false-triggers ~2 ct (~$0.02–0.04) and makes the trend day worse (−0.52); 25c/20m never fires. Usable band ~15–20c, a 2–5c margin.

### (4) Confidence + N=1 caveat

**Confidence: medium.** Single-session evidence: N=1 fast-monotonic gas trend day (KXAAAGASD-26JUL24, 146 fills); 26JUL24 is the ONLY fast-monotonic gas session, so the 15–20c threshold cannot be selected without peeking at it (in-sample by construction, no OOS gas trend day exists). Trend-loss failure mode recurs ~1/5 events (snap_task3) / ~1/11 on the full account (only KXAAAGASD-26JUL24 is a pure-balanced ladder-delta loss; KXTEMPAUSH-26JUL2021 −15.36 and KXTEMPDCH-26JUL2021 −14.79 are naked-dominated → different lever; KXAAAGASD-26JUL23 +0.74 and KXAAAGASW-26JUL27 +1.25 open are trended-profitable controls). The tape's −19.126 reconciles EXACTLY to the live balanced_fill for this ticker.

---

## OPERATOR-APPROVED CONSTRAINTS (2026-07-24)

- **#1 — Retain taker=0 + balanced + flatten (addresses 77%).** Keep TAKER_FLATTEN=0, two-sided balanced quoting, and pre-close-flatten; this stack addresses ~77% of the loss surface. Do not re-enable taker flattening as a blanket lever.
- **#2 — Capture-gate = $0 on gas; never credit gas savings to it.** Every gas reducing fill fell in-window (out-of-window fill-leg = $0.00), so KALSHI_CAPTURE_GATE removes $0 on gas. Do not attribute any gas loss reduction to the capture gate.
- **#6 — Model flatten recovery at 20–40%, not 100%.** Pre-close-flatten recovers only ~20–40% of a naked settle residual (gas central ~27%) and can go underwater if it fires late into a thin book. Never model it as fully recovering naked carry.
- **#7 — Select on ABSOLUTE volatility, not gross reward pool.** Rank series by absolute underlying vol: GASW / GASD / TEMPNYCH lowest (favored); TEMPLAXH / DCH / AUSH highest (avoid). Do not select markets by gross reward pool size.
- **#8 — Never build the anti-straddle shape (long-NO-low + long-YES-high).** This shape has NO settlement floor: both legs can lose when settlement lands between (GASD-23 long-NO-4.090 −21.89 + long-YES-4.105 +28.0 both lost). Only ever carry the floored pair (long-YES-low + long-NO-high). `ladder_pairing` must not build the unfloored shape.
