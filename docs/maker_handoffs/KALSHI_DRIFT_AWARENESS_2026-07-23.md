# KALSHI DRIFT AWARENESS — VERDICT (2026-07-23)

**Lane:** Kalshi maker (rewards-farming). **Branch:** `claude/maker-kalshi-live`.
**Question posed:** can the bot detect being *run over* in advance and respond, or is the weather bleed a quality ceiling we cannot see coming?
**All analysis in NEW files** (`kalshi_drift_*.py/.json/.txt`, `kalshi_cadence_refute.py`, `kalshi_field_data_probe.py`, `kalshi_book_shape_probe.py`, `kalshi_tape_density_probe.py`, `kalshi_ws_public_probe.py`). **Zero existing modules edited** (`git status`: all `??`).
**Window covered:** 07-20 → 07-22, two families (TEMP, GAS). **Binding sample:** n=6 run-over positives. Everything below inherits that limit.

---

## 1. CAN WE EVEN SEE IT COMING?

**No. At our 2-minute cycle, with no queue position and no book sizes, being run over is not detectable in advance. The killer move arrives inside a single decision cycle with no measurable warning in the cycle before it.**

This is the load-bearing finding and it is the most robust thing in the study, because it is the one claim an adversarial refuter re-tested at our real cadence and could **not** break (cadence-and-realism lens: `refuted: false`).

- Resampling the six run-overs to the real **120-second** grid (`kalshi_cadence_refute.py`, imports the study's own timeline builder so only cadence differs): median `jump_share_2min = 1.04`; **5/6** run-overs have a single 2-min cycle carrying ≥80% of the entire loss.
- **Zero lead time.** The cycle immediately *before* the killer cycle had a median adverse move of **+0.000** (max +0.020); **0/5** exceeded even a 0.08 warning threshold. The exitable price collapses inside one cycle: 0.48→0.01, 0.42→0.00, 0.30→0.00.
- A 2-min rate trigger is therefore **coincident with the damage, not predictive of it**. It fires *during* the kill, by which point we cannot re-quote until the next cycle and the exitable price is already ~$0.01. It salvages nothing.

Mechanism, from the minute-level decomposition (`kalshi_drift_jump.py`): **TEMP markets do not trend through us — they gap.** The scheduled temperature observation lands near close and re-rates the book in one print. The largest single loss, `KXTEMPLAXH-26JUL2212-T71.99`, sat flat at 0.485 for six minutes, then went **0.465 → 0.085 in one minute**. Nothing sampling slower than that print can have lead time on it.

> Honest limit, stated because it is the door left open: this is a verdict about **tape + best-bid/ask candles**, which is all we can reconstruct retroactively. **Book *sizes* are not retrievable historically** (no book history exists — `plans-*.jsonl` are cycle-level aggregate counters, verified read-only on the VPS). So depth imbalance, queue position, and true order-flow-imbalance (OFI) are **untested, not refuted.** Testing them needs a live `orderbook_delta` WebSocket recorder (auth-gated at handshake, our RSA key works) standing up and then *waiting* — it cannot be validated against a single historical loss. See §6.

---

## 2. THE DETECTOR — none survived

**No detector separated the losers from the survivors once exposure was matched.** Every flow/drift score collapsed to a coin flip; several inverted (fire *more* on the winners).

### Method controls first (`kalshi_drift_selftest.py`)
All 10 detectors PASS a synthetic run-over (0.60→0.02 over 30 min, one-sided tape + fills) and PASS flat balanced noise. Two failed initially and were fixed *before* results were read (D6 was a noise detector → magnitude floor added; D9's control had zero-variance volume → fixed). A coin flip on real tape is therefore a property of the tape, not a coding bug.

### The confound that makes this hard (and disqualifies the obvious answer)
19 of the 20 canonical worthless lots are `KXTEMP*`; every profitable-gas negative is `KXAAAGAS*`. **A classifier reading the first six characters of the ticker scores TPR 0.91 / FPR 0.00 using zero market data** (null N2, `kalshi_drift_eval.txt`). Any pooled "positives vs gas" number is a family detector in disguise. **The only unconfounded test is within TEMP**, and that is every number below.

### Confusion matrix — WITHIN TEMP (point-level hazard, exposure-normalised)
20 positives vs 27 controls; 315 positive decision-points / 193 negative. `lift < 1` = fires MORE on survivors than losers.

| detector | TPR | FPR | lift | verdict |
|---|---|---|---|---|
| D1 adverse_mid_drift | 0.17 | 0.19 | **0.92** | coin flip |
| D2 adverse_exit_drift | 0.18 | 0.21 | **0.87** | coin flip |
| D3 tape_adverse_share | 0.29 | 0.45 | **0.63** | **inverted** |
| D4 tape_adverse_net | 0.30 | 0.42 | **0.70** | **inverted** |
| **D5 own_fill_onesided** (the mandated one) | 0.12 | 0.26 | **0.45** | **inverted** |
| D5c own_fill_any (control) | 0.14 | 0.28 | 0.49 | same as D5 → one-sidedness carries nothing |
| D6 run_persistence | 0.01 | 0.01 | 1.23 | fires ~never |
| D7 value_floor | 0.63 | 0.35 | 1.79 | not a drift detector — "our side is cheap" |
| D8 spread_widen | 0.11 | 0.09 | 1.23 | negligible signal |
| D9 vol_surge_adverse | 0.05 | 0.04 | 1.30 | fires ~never |

### Threshold-free (AUC, within TEMP, 2000-sample bootstrap) — the exposure trap
The first pass showed **AUC 0.759 [0.61, 0.89]** for mid-drift — apparent real separation. It is an **artifact**: positives have ~3× longer windows (36 vs 12 min median), so "worst-over-window" gives them more draws. Matched to equal window length it vanishes:

| score | AUC unmatched | AUC matched (K=5) | verdict |
|---|---|---|---|
| S1 mid_drift | 0.759 | **0.550 [0.37, 0.74]** | coin flip |
| S2 exit_drift | 0.676 | **0.492 [0.30, 0.69]** | coin flip |
| S3 tape_adv_share | 0.631 | **0.528 [0.35, 0.70]** | coin flip |
| S4 tape_adv_net | 0.600 | **0.294 [0.12, 0.47]** | **INVERTED** |
| S5 own_fill_onesided | 0.434 | **0.463 [0.28, 0.65]** | coin flip, point est. <0.5 |
| S7 value_floor | 0.922 | **0.638 [0.45, 0.81]** | coin flip |
| S8 spread | 0.750 | **0.562 [0.37, 0.74]** | coin flip |

**Every matched CI spans 0.5.** Most generous possible reading (threshold picked *after* seeing labels, same positions — a deliberate overfit ceiling): best Youden's J = 0.34, and that is S7 value_floor ("our side is cheap"), not a drift detector. Every genuine flow/drift score: **J ≤ 0.21, firing on >half the survivors.**

### The mandated detector — one-sided fill clustering: **REFUTED**
Refuted independently on different data (public candles + position level, vs the prior phase's authed fills): point-level lift 0.45; matched AUC 0.463 / 0.400 with point estimate **below** 0.5; its own control D5c (one-sidedness removed) scores the **same or better**; fired on only **2 of 6** run-overs. Do not build the ninth refuted toxicity metric on our fill stream.

### Lead time (of the detectors that fire at all)
D1/D2 fired 6/6 at 22–42 min lead — **but at FPR 0.53–0.70 inside TEMP** (matched K=5, `kalshi_drift_eval.txt`). A detector that warns 29 min early on the losers *and on two-thirds of the winners* is a stop-trading switch, not a drift detector. At our real 2-min cadence (§1) even that evaporates: the prior-cycle warning drops to **0/5**.

**False-positive rate against profitable gas** (the number the brief demanded): the strongest scores fire on **40–83%** of gas decision-points (D3/D4 matched, gasAll FPR 0.11–0.94). D1's *only* clean read is FPR 0.00 on profitable-gas at K=5 — but at TPR 0.63/FPR 0.53 *within temp* and 0-lead at 2-min cadence, that clean-gas number is an artifact of gas simply not gapping, not of the detector working.

**Section verdict: no detector survived. There is no live drift trigger to attach a response to.**

---

## 3. THE RESPONSE — priced in LIP terms

Because no live signal beats a coin flip, **there is no trigger to fire a live response on.** For completeness, the brief asked the three live mechanisms ranked by reward-cost per unit of protection at the real **DF = 0.50** (`discount_factor_bps=5000`; canon R4/§M-line 156: credit **halves every tick** away from reference).

| mechanism | reward cost | protection | reward-cost per unit protection |
|---|---|---|---|
| **Shrink the side** | LINEAR (R4: score ∝ size) — half size = half that side's score | half the fill risk on that side | **1:1, no cliff — best** |
| **Pull one side** | forfeits that side's score = up to 50% of the market's snapshot score (two sides score additively, R4) | 100% of one side's fill risk | 50% cost for one-side protection |
| **Step back a tick** | DF 0.50 → **−50%** on that side, PLUS risk of dropping below the qualifying-walk cutoff → **zero** on that side | still resting, still fillable one tick worse → minimal fill-risk reduction | **strictly dominated** |

Ranking best→worst: **shrink > pull-one-side > step-back**. Step-back is dominated (pays the tick decay *and* the qualifying cliff and barely reduces fill risk).

**Break-even false-positive rate.** For a live trigger to be non-negative it must save more expected fill-loss than the reward it forfeits by firing on a survivor. On a signal measured at **lift ≈ 1** (TPR ≈ FPR within temp), every true-positive is matched by a false-positive that forfeits reward for nothing → **the break-even FPR is unachievable by any of D1–D9.** Firing any live mechanism on these signals is negative-EV by construction. This is exactly the "ninth refuted metric" the brief warns against, and I will not propose it.

> ⚠ **GUESS flagged:** the design draft attached a specific "**12% chance of zeroing** by falling out of the qualifying set" to the step-back cliff. That precise figure is **not in canon.** The nearest measured quantity is §M2's **13.9% one-sided rate**. The step-back qualifying-cliff is a real mechanism (R4 qualifying walk clears a side that runs out of size before Target Size), but its magnitude is not measured. Treat "~12%" as illustrative, not sourced.

---

## 4. QUANTIFIED BENEFIT against the real −$40.62

The canonical loss reconciles exactly: **07-22 = 20 worthless-expiry lots, exit 0.00, −$40.62** (18 distinct contracts; whole file 24 lots / −$40.92; the 18 `(ticker,side)` positions total −$46.21 = −$40.62 worthless + −$5.60 partial-exit on the same contracts). I will not round any of it.

**Benefit of DETECTION against −$40.62: ~$0.** No detector fires with lead time (§1–2); the money leaves inside one 2-min cycle. There is nothing for a live drift response to save.

**Benefit of SELECTION (de-admit temp) against −$40.62 — GROSS vs NET, do not conflate them:**

- **Gross:** 19 of the 20 worthless lots are temp, so not being in temp on 07-22 would have avoided **essentially all** of the −$40.62 of *gross* worthless-expiry loss. That number is real but it is **half a ledger** — it ignores the reward temp also earned.
- **Net — and this is where three HIGH-severity refuters land:** the designer's claimed steady-state benefit was **temp net −$13.06/window** (§M8: temp trading −$36.12 + credits +$23.06). **That −$13.06 is a partial ledger that §M13 explicitly withdrew** ("any net figure is a partial ledger, and I should not have presented one as a verdict"). Two accounting facts make the true temp net **unknown and biased pessimistic**:
  1. **Credits lag by a Time Period.** The export ends 07-22; the 07-22 temp quoting day's credits post on **07-23** and are **absent** from the export. The operator's screenshots show a ~$42 07-23 credit batch, including a single **$12.94 (NYCH, a temp credit)**. **$12.94 alone closes 99% of the −$13.06 gap.** Temp is the biggest-credit family (91% of all reward), so the missing batch is disproportionately temp.
  2. **§M13 booked only the gas-weekly side of the lag** ("runs against gas, GASW uncredited") and wrongly called temp "fully captured." The symmetric lag against temp (07-23 excluded) was dropped by the design and is restored here.

**So the honest quantified benefit of de-admitting temp is: gross ≈ the temp share of −$40.62 on 07-22, but NET is unscoreable and could be near-zero or positive.** Presenting "−$13.06/window saved" as the benefit would be resurrecting a number its own source retracted. **Do not.**

---

## 5. WHAT THIS DOES NOT FIX — and whether a simpler lever dominates

**Detection fixes none of the −$40.62.** Even selection leaves three distinct failure modes, only one of which is temp-gapping:

1. **TAIL (16 positions, −$22.25 across the window):** cheap OTM tickets bought at 0.03–0.20 that simply never came in. **6/16 never moved more than 0.05** — *there is no adverse move in existence to detect.* This is a **pricing** problem (we bought a 7¢ ticket worth <7¢), not a detection or drift problem, and not fully fixed by de-admitting temp either (it is fixed by not buying tickets priced above their worth).
2. **RUNOVER (6 positions, −$27.64):** real adverse moves — but **gaps, not drifts** (§1). Selection can avoid the population; nothing live can catch the individual event.
3. **The 07-23 gas both-wings pair:** short 4.090 + long 4.105, settled **4.091 between them**, both wings worthless. Pure **strike-pairing geometry** — the underlying went nowhere, no directional detector can fire. Fixed by strike selection / not holding both wings into settlement. (These lots post-date the CSV, so they are in **no** confusion matrix here.)

**Does a simpler lever dominate detection? Yes — selection / sizing / entry-timing dominate, unambiguously.** The strongest thing in the entire study uses **no market data at all**: "entry VWAP ≤ 0.20" (null N3: TPR 0.73 / FPR 0.30, lift 2.45) and "is it TEMP" (TPR 0.91 / FPR 0.00). Both beat every drift detector built. Late-life entry gating and strike selection are worth more than any live detector.

**Weather is both sides of the ledger — state both, because the design under-weighted the reward side:**
- Weather (temp) is **~all the loss** — the entire −$40.62 worthless-expiry event, and the −31.95%-of-notional bleed (16× worse per dollar than gas's −1.97%).
- Weather is **91% of reward income** — $23.06 of the $25.21 total LIP credits (§M8, screenshot-cross-checked exact).

Cutting 91% of the reward engine to remove an *unproven* per-window loss risks reducing the operation to a ~$2/window gas-only rump (§M11: LIP is "the only reason this strategy is viable at all"). **This is why the refuters converge on a reversible lever, not an irreversible cut** — see §6.

---

## 6. THE SANDBOX EXPERIMENT — pre-registered, criteria fixed *before* data

Two experiments. The first settles the *benefit sign*; the second is the *only* path to test the book-based detectors we could not test retroactively. **Both success criteria are registered here and must not be re-chosen after seeing results.**

### Experiment A — settle whether temp is actually net-negative (the load-bearing unknown)
- **What to log:** re-export the full Kalshi transaction CSV **after 2026-07-27T04:00Z** — the first moment every Time Period covering 07-21→07-23 quoting has closed and credited, gas-weekly included (§M13's own named test). Attach the event ticker to credit rows if the §M8 ask 3c has been answered; otherwise cross-reference the operator UI screenshots as §M8 did.
- **How long:** one clean export at/after 07-27T04:00Z; ideally a second covering a config-stable stretch with `TAKER_FLATTEN` fixed (zero taker trades since it was set — §M13).
- **Pre-registered success criterion:** recompute per-family **net = in-window trading P&L + ALL credits attributable to that family, each matched to its own quoting period** (including the excluded 07-23 temp credits, the $12.94 NYCH among them). **De-admit temp only if temp net-of-full-rewards remains materially negative over the complete window.** If temp net is ≥ −$2/window or positive, **hold temp and use a reversible size reduction instead.** No permanent Tier-2 cut before this number exists.
- **Why pre-register:** the −$13.06 was a withdrawn partial ledger; the whole point is not to let a post-hoc reading re-justify the same irreversible cut.

### Experiment B — the one detector class we could not falsify: book depth / OFI
- **What to log:** stand up an `orderbook_delta` WebSocket recorder (full snapshot + incremental deltas with `ts_ms`; auth-gated at handshake, our RSA key works — verified `kalshi_ws_public_probe.py`) on the temp series, persisting **per-market L2 depth over time** — the exact thing no historical file contains.
- **How long:** ≥ 10 fresh settled temp markets (M ≥ 10), so a within-family AUC has non-degenerate n on both arms.
- **Pre-registered success criterion:** depth-imbalance / true-OFI earns a live response **only if** its **within-family** (temp-only, family label held constant) matched AUC ≥ **0.65 with a 95% CI whose lower bound clears 0.55**, AND its FPR on profitable-gas cycles ≤ 0.10. Anything with a CI spanning 0.5 — the fate of every retroactively-testable detector — is refuted and shelved. Positive and negative controls (synthetic gap vs flat noise) must pass first, exactly as `kalshi_drift_selftest.py` demanded of the tape detectors.
- **Honest caveat, registered:** this cannot be validated against any historical loss; it requires waiting. If the operation is size-constrained ($85–100 account, 20 contracts/side, ~2% of Target Size), a depth signal that needs size or speed to act on is useless here even if it separates — that disqualifier must be checked before any deploy.

### ⚠ Do NOT reuse the `jump_share` gate to admit new series
The design proposed a per-series `jump_share < 0.70` admission gate. **Refuted (HIGH).** It is the ticker family in disguise: null "startswith TEMP" already scores TPR 0.91/FPR 0.00 with zero market data, and `jump_share` (temp 0.95 vs gas 0.54) adds no separation beyond that free prefix. **The 0.70 threshold is a GUESS** — the midpoint of a two-point gap, fit on **n=2 families**; it cannot be validated as a gate until ≥5 series with settled tape exist to regress per-series maker P&L on it. Worse, it is **potentially inverted for the real forward risk**: a slow persistently-trending series — the canonical symmetric-maker killer — scores *low* `jump_share` (looks like "safe gas") and would be **greenlit**. Any expansion candidate (`KXINTC`, `KXPM`, `KXRT`, `KXFUNDRAISING`, `KXCLAUDE`) must have its own settled-tape toxicity measured before admission — that is the §M5 "toxicity unmeasured" blocker, and `jump_share` does not discharge it.

---

## 7. INTERACTION WITH THE CONFIG AS IT STANDS

Config read as given: `SERIES_ALLOW` = 9 series (5 temp, 2 gas, `KXB200MON`, `KXAMSAVO`); `MAX_TOTAL_CAPITAL=100`, `HELD_MAX_USD=100` (both **above account value ≈ $85–100 → effectively inert**); `PER_SERIES_CAP=30`; `FOOTPRINT_TOP=40`; `JOIN_SIZE=20`; `MAX_MARKET_CAPITAL=15`; `MAX_UNWIND_LOSS=0.02`; `TAKER_FLATTEN=1`; `REDUCE_ONLY_KEEP_BOTH=1`; `DAILY_LOSS_HALT_USD=40`.

- **No live drift response is being added**, so no new live knob. The only actionable lever this study supports is a **Tier-2 `KALSHI_SERIES_ALLOW` change** (remove/size-down temp) — and per §4/§6 that is **HELD pending Experiment A**, in reversible form (shrink) not an irreversible cut.
- **The naked-risk brake is unreachable** (disabled at 100, above account value). The brief correctly flags that this leaves **`DAILY_LOSS_HALT_USD=40` as the only backstop** between an adverse run and a halt. **The study says that backstop is load-bearing and cannot be replaced by drift detection** — because no drift detector works. Note the coincidence with eyes open: the halt is **$40** and the realized worst-day worthless-expiry loss was **−$40.62** — i.e. the halt tripped *at* the damage, after the fact, exactly once. It is an after-the-event circuit breaker, not prevention. Prevention lives in **selection and sizing** (§5), which act before the halt is ever in question.
- **`TAKER_FLATTEN=1`:** since it was set there have been **zero** taker trades (§M13); the −$45 of prior taker fire-sales are not recurring. Selection reduces the naked risk `TAKER_FLATTEN` would otherwise have to flatten, so the two are complementary, not in tension.
- **`INV_SOFT_CT`/`INV_HARD_CT` (level throttle):** trigger on **level, not rate** — by the time a level trips, the one-cycle gap has already happened (§1). The prior phase found the deployed level throttle is *anti-correlated* in temp. De-admitting/sizing-down temp removes the population where it misbehaves; gas (where it works) is untouched. No temp-specific throttle fix is needed if temp exposure is cut.
- **Caps are inert:** with `MAX_TOTAL_CAPITAL` and `HELD_MAX_USD` above account value, the binding constraints are `PER_SERIES_CAP=30`, `MAX_MARKET_CAPITAL=15`, and `JOIN_SIZE=20`. A **reversible temp size reduction** (the §6 lever) is naturally expressed by lowering `PER_SERIES_CAP` / `MAX_MARKET_CAPITAL` for temp — not by dropping it from `SERIES_ALLOW`. That keeps the 91%-of-reward engine on while Experiment A settles the sign.

---

## REFUTERS' VERDICTS — reported in full, including where they contradict the designer

Four adversarial lenses ran. **Three refuted the design's central move (permanent de-admit temp) at HIGH severity; one tested the core "can't see it coming" claim and did NOT refute it.**

| lens | refuted design? | severity | what it establishes |
|---|---|---|---|
| **cadence-and-realism** | **NO** — design survives | none | Re-tested at the real 2-min grid: killer is a single-cycle event, **zero lead time**, gas structurally never gaps per-cycle (0/1,851 gas cycles ≥0.15 single-cycle move). **Strengthens §1.** The "selection not detection" conclusion is cadence-robust. |
| **detector-is-noise** | **YES** | HIGH | The retained `jump_share` gate is the ticker family in disguise (null prefix already TPR 0.91/FPR 0.00), contradicts its own control (benign temp at 0.67 sits *below* the 0.70 admit line), and is **inverted for slow-bleed series**. Not a blocker only because de-admit is independently ledger-justified — but that ledger is itself withdrawn (§M13). **→ §6 warning.** |
| **response-costs-more-than-it-saves** | **YES** | HIGH | The −$13.06 benefit pairs a full-window loss side against a **~1-day-short** credit side; the missing 07-22 temp credits post 07-23; the response may forfeit a break-even/positive series supplying 91% of reward income. **→ §4, §6-Experiment A.** |
| **wrong-lever** | **YES** | HIGH | Permanent de-admit is irreversible and gates re-entry on the loss *mechanism* (`jump_share`), not net-of-reward EV. $12.94 of one excluded temp credit closes 99% of the gap. Reversible size-down / hold strictly dominates until the 07-27 re-export. **→ §4, §6, §7.** |

**Synthesis:** the design's **diagnosis is correct and refuter-confirmed** — we cannot see the run-over coming, no live detector survives, and the lever is selection/sizing, not live drift response. The design's **prescription is over-committed** — it converts a correct diagnosis into an irreversible cut of 91% of reward income, priced on a ledger its own source (§M13) withdrew. **The correct action is the reversible one:** size temp down via `PER_SERIES_CAP`/`MAX_MARKET_CAPITAL`, hold the series enabled, and settle the sign with the pre-registered 07-27 re-export before any permanent de-admission.

---

## GUESSES / UNVERIFIED, flagged explicitly
- **"~12% chance of zeroing" (step-back cliff, §3):** not in canon. Nearest measured figure is §M2's 13.9% one-sided rate. Mechanism real, magnitude unmeasured.
- **`jump_share < 0.70` admission threshold (§6):** a GUESS — midpoint of a 2-point gap, fit on n=2 families; not validatable until ≥5 series with settled tape exist.
- **"temp net −$13.06/window" (§4):** a partial ledger **withdrawn by its own source (§M13)**. Do not quote as a verdict. True temp net is unknown pending the 07-27T04:00Z re-export.
- **Tape is not de-selfed** (needs authed fill `trade_id`s to remove our own prints); our measured share of tape volume is 0.38–2.28%.

## SAMPLE SIZES (binding)
- **n=6** run-over positives — the binding constraint on all lead-time / jump-share / cadence conclusions.
- **20** canonical 07-22 worthless lots (18 distinct contracts), **−$40.62**.
- Within-TEMP confusion matrix: **20 positives vs 27 controls** (315 pos / 193 neg decision-points); matched K=5 keeps 19/20 pos, 17/27 ctrl; K=10 keeps 10/27 ctrl (CIs widen).
- Gas false-positive control (cadence lens): **18 positions, 1,851 cycles** — well-sampled; gas is the only large-n arm.
- Like-for-like family ledger (§M8): **159 trades, 07-21..22, 2 days** — small, spans config changes.
- **Coverage: 07-20 → 07-22 only, two families (TEMP, GAS).** No book sizes retroactively → depth/queue/OFI untested. The 07-23 gas both-wings pair is strike geometry, out of scope for drift and in no confusion matrix.

## LANES THAT RETURNED NOTHING
- **Book-history lane returned nothing usable:** `plans-2026072*.jsonl` are cycle-level aggregate counters with zero per-market book rows; `concentration_samples.jsonl` is a handful of frozen gas-only snapshots, not a time series. There is no retroactive depth data at all — hence Experiment B must build it forward.
- **Public WebSocket "no-auth" channels returned nothing:** `asyncapi.yaml` lists `trade`/`ticker` as auth-not-required, but unauthenticated connect returns **HTTP 401**. Our RSA key is required; the channels exist but only authed.
