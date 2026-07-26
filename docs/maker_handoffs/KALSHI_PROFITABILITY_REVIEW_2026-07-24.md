# KALSHI MAKER — FULL PROFITABILITY REVIEW (2026-07-24)

**Question (operator):** Cross-matrix the base bot + all 6 behavioral additions + Combo. Is there ANY
configuration that is NET-POSITIVE on receipts, or is this maker fundamentally unprofitable at our size?
The bot was wound down today after a ~-$47 day. This review decides: rebuild, reconfigure, or retire.

**Method:** RECEIPTS over model. Every $ is receipt-anchored or flagged GUESS/§M7-MODEL. Two live receipt
sources were re-run and reproduced this session (not quoted from memory):
- `kalshi_settlement_pnl.py` — realized settled P&L, **58/58 revenue-reconciled, 47/47 CSV-cross-checked** (authoritative).
- `kalshi_netev_calibrate.py kalshi_transactions_2026-07-23.csv` — per-family maker-loop net over the 07-21..22 credit window.

---

## 1. THE VERDICT IN ONE LINE

**NO configuration is receipt-provably net-positive. Retire (or, at most, run pre-close-flatten ALONE as a
bounded de-risking experiment with no profit expectation). The authoritative settled receipts show
-$122.57 realized lifetime across 58 contracts, with EVERY ONE of the 4 gas dailies negative
(gas -$47.86 total) — and even a hypothetical perfect, free pre-close-flatten leaves gas at -$20.99,
because flatten removes only the -$23.86 settle-leg and cannot touch the -$23.14 intraday adverse-selection
fill-leg, which is >10x the +$2.15 gas credits.** The "+$1.20/day gas" that makes the matrix's defensive
cells look profitable is a 2-day, in-window, delta-neutral maker-loop slice that **excludes settlement
entirely and excludes 31 out-of-window gas trades** — it is a real measurement of a subset, not the P&L of
any runnable configuration.

---

## 2. THE CROSS-MATRIX (all 9 configs)

**Read the best cell first, then the caveat that guts it.** The matrix below is scored two ways: the
maker-loop window figure (what the build session reported) and the **settled-receipt reconciliation** (what
the authoritative tape actually realized). Where they disagree, the settled receipt wins.

| Config | Maker-loop window net (subset) | **Settled-receipt reality** | Classification (receipt) | Dominant driver | Depends on |
|---|---|---|---|---|---|
| **full-defensive** (net-EV gas-only + capture-gate + pre-close-flatten) | +$1.20/day | Even with a **perfect free flatten**, gas = fill-leg **-$23.14** + credits **+$2.15** = **-$20.99** over the gas span (~-$5/day). | **NEGATIVE (break-even only if the fill-leg was contamination — UNPROVEN)** | The intraday adverse-selection fill-leg, which flatten does NOT remove. Flatten removes the -$23.86 settle-leg; the -$23.14 fill-leg (dominated by JUL24 -$19.13) survives. | Whether the -$19.13 JUL24 ATM fill-leg is structural or one-off contamination — the same unproven "clean vs quarantined" question as temp. |
| **defensive-plus-combo** (full-defensive + Combo) | +$1.2 to +$9/day (GUESS ceiling) | Combo reprices the **fill-leg ONLY**, cannot touch the -$23.86 settle-leg, and our pro-rata share is sub-marginal. Still gas -$20.99 core before any Combo rebate. | **UNKNOWN → lean NEGATIVE** | Same uncovered fill-leg + settle-leg. Combo is a capped sweetener on the near-zero side. | Unmeasured Combo rate (GUESS); cannot flip a tail/fill-driven loss. |
| **base-only** (727ca7c5, all OFF — the wound-down baseline) | steady carry +$1-2/day | **gas -$47.86 realized, 4/4 events negative; whole-bot -$122.57 + $25.21 credits = -$97.36 (~-$24/day)** | **NET-NEGATIVE** | Naked settlement tail (-$34.98 JUL24) on top of a fill-leg that does not wash out-of-window. | Robust to temp-quarantine and Combo; the gas receipt alone is negative. |
| **activity-only** (funding-gate + pivot-select ON, no controls — ~today's live behavior) | tail-free +$1-3/day | The config that produced **today's -$47**. Pivot concentrates in the toxic ATM band; funding-gate deploys more naked residual. | **NET-NEGATIVE** | Same tail + fill-leg, AMPLIFIED. Both amplifiers scale cost/tail linearly; reward scales sublinearly (~2% share). | Robust; does not need temp or Combo to be negative. |
| **selective-only** (net-EV + capture-gate, no flatten) | -$5 to +$1.20/day | Isolates gas but leaves the settle-leg + fill-leg intact → gas -$45.71 (realized -$47.86 + $2.15 credits). | **NET-NEGATIVE** | Uncovered settlement tail — selectors thin the book, they do not fix the leak. | Settlement-tail frequency; temp-quarantine (forgone side only). |
| **lossscut-only** (pre-close-flatten ON, quotes all families) | -$5 to +$2/day | Removes settle-legs system-wide but keeps temp AND the gas fill-leg → still negative on receipts. | **NEGATIVE (matrix called BREAK-EVEN; receipts say worse)** | Residual fill-leg adverse selection + unscored temp family. | Quarantined temp sign; fill-leg structural-or-not. |
| **full-offensive** (both amplifiers + all controls, no Combo) | +$1 to +$5/day | Amplifiers add no proven reward (sub-marginal share); flatten + net-EV cap downside to the same -$20.99 gas fill-leg core. | **NEGATIVE / UNPROVEN** | Amplifiers are variance-adders at our share; the fill-leg core is unfixed. | Fill-leg structural-or-not; amplifier reward is a GUESS (0/304 marginal). |
| **offensive-plus-combo** (everything + Combo) | -$3 to +$6/day | Entirely Combo-rate-dependent; core still the -$20.99 gas fill-leg + settle-leg Combo can't touch. | **UNKNOWN-PENDING-DATA → lean NEGATIVE** | Unmeasured Combo rate; pivot-amplified residual if a flatten misses. | Combo rate (GUESS) + temp-quarantine + flatten reliability. |
| **combo-only** (base + Combo, no controls) | tail-free +$1.7 to +$3.2/day | Combo sweetens fills that already wash; leaves the -$34.98 tail and the fill-leg fully intact. | **NET-NEGATIVE (UNKNOWN only b/c Combo unmeasured)** | The untouched settlement tail — no Combo rate fixes it without flatten. | Combo rate; but no plausible rate flips a tail-driven loss. |

**Bottom line of the matrix:** the maker-loop window column makes several cells look positive; the
settled-receipt column shows **zero configurations that produced a single realized net-positive day
anywhere on the tape.** The "+$1.20/day" is genuine but it measures the in-window delta-neutral ladder in
isolation — strip the settlement and the out-of-window trades back in and the same gas family is -$45.71.

---

## 3. THE UNIT-ECONOMICS TRUTH (why the additions reduce loss but cannot create reward)

**Reward per fill vs cost per fill (gas, the only clean family):**
- **Reward is NOT earned per fill.** It accrues to *resting* size at LIP snapshots (R4: normalized LP score
  × pool, score = discount^N × our_size). Our fills earn nothing directly.
- **Cost per fill in-window ≈ 0** (+0.051 ct/ct on the delta-neutral ladder). So the fill business, at its
  cleanest, is a wash by construction.
- **But out-of-window and at the ATM band it is NOT a wash:** the realized fill-leg across all settled gas
  events is **-$23.14** (JUL24 alone -$19.13). The delta-neutral wash held only inside the 07-21..22 window;
  the full tape did not.

**Our reward SHARE is the hard ceiling nothing can lift:**
- R3 Target Size = 1000 ct; we quote ~20 ct ≈ 2% of Target. **Measured marginal-maker snapshots: 0 of 304.**
- Live scorecard share: **0.0-2.9%** per market. We are never the marginal maker.
- Reward therefore scales **sublinearly** with our size (own size dilutes its own denominator: ×2 size →
  ×1.90 reward, ×10 → ×6.90), while adverse-selection fill cost and settlement-tail risk scale **linearly**.
  Net margin per notional DEGRADES as you scale (index 1.00 → 0.83 at ×5 → 0.69 at ×10).

**The gas +1.1% base is thin AND partial:** +1.1% of notional = +$1.20/day is the *in-window* figure. It
omits the settle-leg (-$23.86 gas) and 31 out-of-window gas trades. It is a floor on a subset, not a net.

**Why all 6 additions are loss-reducers, never reward-creators:** every addition is a cost-gate, a
loss-cut, or an activity throttle. **None of them makes us the marginal maker or lifts our LIP share.**
Reward is fixed by our 0-3% share against a 1000-ct Target; the additions can only stop us deploying where
we lose (net-EV, capture-gate, stand-down), cut the settle-leg (flatten), or move capital around
(funding-gate, pivot). The best any stack can do is **isolate the gas subset and remove the settle-leg** —
which still leaves the -$23.14 fill-leg uncovered and >10x the +$2.15 credits.

---

## 4. THE THREE THINGS THAT COULD CHANGE THE ANSWER (ranked)

1. **Is the -$19.13 JUL24 / -$23.14 aggregate fill-leg STRUCTURAL or one-off contamination?** — *highest
   value, resolvable from data we hold.* The in-window fill-leg washed (+$0.25); the full-tape fill-leg did
   not (-$23.14). If a clean gas-only delta-neutral book (net-EV on, pivot OFF, controls on) genuinely
   washes the fill-leg the way the window did, then full-defensive-with-flatten could reach break-even. If
   the -$19.13 is structural adverse selection on near-money gas, **no config is positive.** This is the
   same unproven "clean vs quarantined" question as temp, and it is load-bearing. **Do not assume the clean
   side.** The 07-27 settled re-export + a controls-on paper replay would resolve it.

2. **Combo rate** — *external lever, UNMEASURED (email drafted, not sent).* Combo pays for fill VOLUME and
   **stacks with LIP**, so it is pure upside on the fill side. But: (a) it **reprices the fill-leg only —
   mechanically cannot touch the -$23.86/-$15.85 settle-leg**, which alone exceeds the gas credits; (b) our
   pro-rata share of any shared pool is a rounding error at 2% book share; (c) to merely double the gas
   carry it must net ≥0.16-0.49 ct/ct AFTER dilution on ~247-765 gas ct/day — structurally implausible at
   our volume and unquantifiable before opt-in (never opt in on silence). **Cannot rescue a
   settlement/fill-driven loss.** GUESS ceiling ~$2-2.5/day even generously.

3. **Size / scale** — *does not help; hits the ceiling.* No size multiple turns this positive. 1×→~2× is
   capital-feasible (gas-only capital ~$48-71/day vs ~$100 balance) but margin-neutral-to-worse and
   **doubles the naked residual** (the -$35 tail becomes -$70). ≥5× is both capital-infeasible (needs a
   deposit, which corrupts the equity loss-meter) and margin-erosive. Scaling multiplies the sign you
   already have, and the clean-subset sign is +1.1% ≈ noise, while the realized sign is negative.

**Over everything — SUNSET:** LIP + Volume incentives expire **2026-09-01 = 39 days.** Best-case
sustained-clean gas at +$1.20/day × 39 = **+$47 total — less than today's single -$47 tail.** Post-Sep-1 the
maker loop is pure adverse-selection cost with zero reward → **structurally negative, no ongoing business.**

---

## 5. THE REFUTERS' VERDICTS (lead with "unfixable at our size" / "Combo won't save it")

- **base-economics-unfixable-at-our-size (CRITICAL, REFUTED = confirmed unfixable):** "Gas = **-$47.86
  realized on LIVE settlement receipts** (58 contracts, 58/58 reconciled), **4/4 gas events negative**. The
  '+$1.20/day gas' is a 2-day window that EXCLUDES settlement and every expensive gas day (31 out-of-window
  trades). Even a hypothetical **PERFECT, FREE** pre-close-flatten leaves gas at **-$20.99** (fill-leg
  -$23.14 + credits +$2.15, >10x gap). Whole-bot receipt net = **-$97.36 over ~4 days (~-$24/day)**. No
  configuration is net-positive on receipt-grade numbers."

- **combo-wont-save-it (HIGH):** "Combo reprices the realized-leg ONLY, not the settle adverse-selection;
  it caps at rebating a FRACTION of the fill-leg and **mechanically cannot touch the -$15.85 settle-leg**,
  which alone exceeds the gas carry. No Combo rate flips a tail-dominated config. A technically net-positive
  config *appears* to exist (full-defensive) but nets ~$1/day, lifetime ≤~$47 over 39 days < one -$47 tail,
  has no scaling path, no non-LIP directional edge (drift AUC 0.6-0.76, wide CI), and goes structurally
  negative post-sunset. **Lean RETIRE.**"

- **best-positive-stack-is-model-optimism (HIGH):** "The best 'PROFITABLE' cell's positive SIGN rests on
  two non-receipts: a **screenshot-attributed** credit split (every credit CSV row has an EMPTY
  market_ticker; only the $25.21 TOTAL is receipt-exact) and an **unrun flatten** (reverted, default-off;
  the -$34.98 tail fired with flatten OFF). Whole-export realized NET = **-$54.78 over 3 days + -$47 on
  07-24. ZERO net-positive days exist anywhere on the tape.** No non-LIP edge exists (drift/toxicity signals
  all AUC-CI-span-0.5). **RETIRE, or at most run flatten-only as a de-risking experiment.**"

- **a-profitable-config-was-missed (HIGH, REFUTED = none missed):** "Receipt lifetime = **-$122.57 across
  all 58 settled contracts** (GAS -$47.86, every daily negative; TEMP -$74.70). Even with perfect never-run
  flatten, clean post-fix gas = **-$7.18/day** because the intraday adverse-fill leg on 26JUL24 alone
  (-$19.13) = 15.9 days of the claimed profit. Whole-account realized maker P&L = **+$0.33/day ≈ 0** only if
  you cherry-pick the window. No receipt-grade net-positive config exists."

All four refuters converge: **the maker is fundamentally sub-scale, variance-dominated, and
sunset-terminal. Combo — the one external re-scaling lever — cannot touch the settle-leg and cannot be
quantified before opt-in. No profitable config was missed.**

---

## 6. THE RECOMMENDATION

### RETIRE.

The receipts do not support a business. The authoritative settled tape (`kalshi_settlement_pnl.py`, 58/58
reconciled) is **-$122.57 realized lifetime, gas -$47.86 with all 4 dailies negative, whole-bot ~-$24/day.**
The "+$1.20/day gas" that animates the matrix's defensive cells is a real but partial measurement — the
in-window delta-neutral ladder in isolation — and it evaporates the moment settlement legs and out-of-window
trades are counted, which is to say, the moment you run an actual configuration.

**Why not RECONFIGURE (defensive-only, for option value):** The single most defensible action is
**pre-close-flatten alone**, because it is the one addition that provably attacks the biggest receipt leak
(the -$23.86 settle-leg → ~-$1 taker). But flatten removes ONLY the settle-leg; it leaves the -$23.14
fill-leg, which is >10x the +$2.15 credits. For flatten-plus-gas-isolation to reach even break-even, the
fill-leg must turn out to have been contamination from running without controls — an assumption identical in
kind to the quarantined-temp question and **not proven by anything on the tape.** Even if it were true, the
ceiling is +$1.20/day, the lifetime is ≤$47 before the Sep-1 sunset (< one tail), and there is zero business
after. That is not option value worth capital and attention.

**Why not REBUILD:** No specific profitable config exists. Every "PROFITABLE" classification in the matrix
was scored on the maker-loop window; none survives the settled-receipt reconciliation. There is no non-LIP
directional edge to fall back on (drift/toxicity AUC 0.6-0.76, CI spans 0.5 — useless). Scaling erodes
margin and multiplies the tail. Combo cannot touch the settle-leg. The reward source is a fixed 0-3% LIP
share that no addition can lift, decaying to zero in 39 days.

**If the operator wants ONE bounded experiment before full retirement** (not a rebuild): run
**pre-close-flatten ONLY, gas-only via net-EV, pivot OFF, funding-gate OFF, minimal footprint**, purely to
MEASURE whether the fill-leg washes clean under controls — with a hard -$20 stop, no profit expectation, and
a decision to retire the moment the fill-leg fails to wash or the first flatten misses. Deploy order if so:
`KALSHI_NETEV_GATE=1` (gas-only) → `KALSHI_PRECLOSE_FLATTEN=1` → amplifiers stay OFF. But frame it as a
data-collection run against the §4-#1 open question, **not a return to trading for profit.** Given the
39-day sunset caps the entire upside at ≤$47, even a clean result does not justify a rebuild.

---

### Receipt provenance (every load-bearing number)

| Number | Value | Source (re-run this session) |
|---|---|---|
| Whole-bot realized lifetime | **-$122.57** / 58 contracts | `kalshi_settlement_pnl.py` (58/58 reconciled, 47/47 CSV-checked) — RECEIPT |
| Gas realized lifetime | **-$47.86** (JUL24 -34.98, JUL23 -7.47, JUL21 -5.27, JUL22 -0.15) | same — RECEIPT, 4/4 negative |
| Gas settle-leg / fill-leg split | **-$23.86 / -$23.14** | same per-event table — RECEIPT |
| Perfect-free-flatten gas floor | **-$20.99** (fill-leg -$23.14 + credits +$2.15) | derived from RECEIPT; flatten removes settle-leg only |
| Gas maker-loop window net | **+$1.20/day** (+1.12%; trading +$0.25 + credits +$2.15; **31 out-of-window trades excluded, settlement excluded**) | `kalshi_netev_calibrate.py` — RECEIPT (subset), credit_lag=true |
| Temp maker-loop window net | -$6.53/day | same — RECEIPT but **QUARANTINED** (07-21..22 pre-delta-neutral-fix contamination); do NOT score as a loss |
| CSV credit total (exact) | **$25.21** | CSV — RECEIPT; gas/temp $2.15/$23.06 split is screenshot/UI-attributed, empty market_ticker |
| Our marginal-maker snapshots | **0 / 304** | measured — RECEIPT |
| Our LIP book share | 0.0-2.9% per market | live scorecard — RECEIPT |
| Combo rate | UNKNOWN | email drafted, not sent — **GUESS**, unquantifiable pre-opt-in |
| M7d settled gas reward (n=1) | $10.09/period vs $50.88 model = 5.04x haircut | `KALSHI_*` build doc — **§M7 MODEL, n=1**, unresolved pending 07-27 export |
| Sunset | **2026-09-01 = 39 days** | LIP + Volume program terms |

*Numbers labeled RECEIPT are from re-run live/CSV sources. The gas/temp credit split is screenshot-attributed
(empty market_ticker on every credit row); only the $25.21 total is receipt-exact. M7d reward is n=1 MODEL.
Combo rate is a GUESS. The temp maker-loop net is receipt-derived but QUARANTINED and must not be scored.*
