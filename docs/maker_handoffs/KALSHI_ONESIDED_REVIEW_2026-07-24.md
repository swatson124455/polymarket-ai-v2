# KALSHI ONE-SIDED QUOTING — RIGOROUS REVIEW (read-only)

**Date:** 2026-07-24 · **Lane:** Kalshi Maker (live pilot) · **Scope:** READ-ONLY, Kalshi venue only. No trades, deploys, config, or edits.
**Deployed quoter:** `maker_kalshi_quoter.py` (live md5 `1c68e130`; funding-gate + pivot-select build).
**Live config (verified — `ssh … sudo grep /opt/pa2-maker-kalshi-live/live.env`):** `FUNDING_GATE=1`, `PIVOT_SELECT=1`, `REDUCE_ONLY_KEEP_BOTH=1`, `INV_SOFT_CT=15`, `INV_HARD_CT=60`, `INV_TOLERANCE=1`, `MAX_MARKET_CAPITAL=15`, `MAX_ACTIVATE_CAPITAL=15`, `MAX_TOTAL_CAPITAL=250`, `JOIN_SIZE=20`, `WIND_DOWN_MIN=20`, `MIN_PRICE_DOLLARS=0.04`, `MAX_PRICE_DOLLARS=0.96`, `WRITE_BUDGET=60`, `MIN_DEPTH_SYM=0.25`, `MAX_SPREAD_TICKS=8`, `HELD_MAX_USD=100`, `DAILY_LOSS_HALT_USD=40`, `TAKER_FLATTEN=0`.

**Provenance correction up front:** the GROUND code-map cited MODULE DEFAULTS (`WIND_DOWN 45`, `INV_HARD 80`, `MAX_MARKET_CAPITAL 250`, `INV_TOLERANCE 3`). The RUNNING bot uses the live.env values above (`WIND 20`, `HARD 60`, `CAP 15`, `TOL 1`). Every hard/throttle boundary in this doc uses the **live.env** numbers. Anyone reading GROUND's thresholds will mis-place the hard/throttle line that drives attribution.

**Evidence base:** resting-orders + positions + fills + orderbook + incentive_programs reads via `kalshi_attribution_ledger` across ~13:25–14:16Z (multiple snapshots, n=5–6 one-sided markets per instant, ~9 persistence samples); bot plan/journal telemetry 13:10–14:16Z; code re-read and re-verified against the live worktree file. Instant/sample size stated per claim.

**No CRITICAL or HIGH refuter finding exists.** All four adversarial lenses returned MEDIUM. Two central claims of the prior ANALYSIS were REFUTED (the dollar magnitude of the reward cost, and the A15/GASW "correct HARD de-risk" carve-outs). Those refutations are folded into the verdict below rather than quarantined.

---

## (1) WHY WE ARE ONE-SIDED — attributed causes, per-order

### 1.0 LEAD: the one UNEXPLAINED / anomalous order (candidate bug)

**KXAAAGASW-26JUL25-4.120 — held +9 (long YES), resting the ACCUMULATING side ALONE.**
At 13:40Z this market was flat, rested a lone `yes`×9 @0.77, which then FILLED @13:40:59 → naked **+9 long**, no hedge leg resting (fills read). At a later sample it held +9 and rested a stale `yes`@0.68 (created 13:57:50) with the reducing `no` MISSING — the **opposite direction** from the dominant funding-gate mechanism, which skips the *accumulating* side and keeps the *reducing* side (which is capital-exempt, `:1636`, `:1647`). `desired_quotes` replay for this state wants TWO sides `[yes join, no unwind]`; the exempt reducing `no` is the one absent.

- **This is not explained by any de-risk rule.** It is a reconciliation / **WRITE_BUDGET=60** create-cycle artifact: the reducing create did not get placed in that cycle, leaving the risk-INCREASING accumulating leg resting alone on a held long.
- **Severity, honest:** it did NOT recur across ~9 persistence samples — a one-off transient, not systematic. Dollar-immaterial. But it is the only order whose one-sidedness makes the book *more* naked, so it is flagged as the lead item and a candidate write-cycle bug, not de-risk.

### 1.1 The DOMINANT live cause is capital starvation, NOT a de-risk decision

When `desired_quotes()` is replayed against the live books at these instants, **zero markets have one-sided INTENT** — every held market returns a two-sided desired book (throttled/join accumulating + unwind reducing). Yet 5–6 markets rest one-sided. The gap is created downstream in `run_once`'s create-loop by the **funding gate**:

- Creates are sorted so reducing/unwind land FIRST and are EXEMPT from the cap (`:1636`, `:1647` — "risk-reducing order can never over-commit; Kalshi frees covered collateral on fill").
- Accumulating (earning `join`) creates are then gated: skipped when `funding_committed + cost > min(free_cash, MAX_TOTAL_CAPITAL=250)` (`:1653-1656`).
- **Live telemetry (journal @14:13:58Z):** `committed=$352.29/250 … held=$144.98 … skipped=10`. Surviving-standing gross ≈ `352.29 − 144.98 ≈ $207` vs free cash ≈ **$184** (balance reads $184–186, 14:04–14:15Z). Over-committed → 10 accumulating creates skipped → **every held market rests reducing-side-only.**
- **Correlation is clean** (plan telemetry 13:12–13:34Z): `create_skipped` = 14–20 when `free_cash` is tight ($144–147), 0–3 when it loosens ($210–225). `committed_usd` oscillates $236–360, frequently above the $250 cap.

**Code rule:** `hard = mag >= INV_HARD_CT(60) or held_usd >= MAX_MARKET_CAPITAL($15)` (`:912`). BELOW hard, `desired_quotes` ALWAYS intends two sides. So a held market resting one side is one-sided by the create-loop, not by design — UNLESS it is genuinely at/above HARD (see 1.3).

### 1.2 Per-order tally (representative instant, n=5–6; state churns ~2–3/cycle)

| bucket (operator's categories) | count | markets (examples) | code rule |
|---|---|---|---|
| **funding-skip** (capital starvation — accumulating create dropped, reducing exempt rests) | **dominant, ~3–4** | KXAAAGASD-4.110/4.115/4.125 (long YES, NO-only); KXCHIPBURRITO-T9.80 | `:1653-1656` gate; `:1636`/`:1647` reducing-exempt |
| **price-bound** (throttled accumulating side stepped below the `MIN_PRICE=0.04` floor → dropped) | 1 | **KXTRUMPENDORSEMENTS-A15** (NO-only, ~13 h) | throttle `:591`/`:912`; per-side floor drop `MIN_PRICE < 0.04` fails |
| **book-one-sided / selection gate** (FLAT + asymmetric book → `desired=[]`, stale side cancel-pending) | 1 | KXAAAGASW-4.160 (`sym=0.18 < MIN_DEPTH_SYM 0.25`) | `:620-624` selection gate |
| **filled** (one leg filled, other still resting — transient) | occasional | GASD-4.120 (flat→both sides within 1 cycle) | n/a — create-cycle timing |
| **reduce-only BY DESIGN** (genuine HARD / wind-down / void / strand — see 1.3) | **contested; ≤1** | GASW-4.140 (raw +60) — DISPUTED | `:912` hard; `:570-586` wind-down; `:631-647` void; `:1254-1276` strand |
| **UNEXPLAINED / anomaly** (accumulating leg rests alone — 1.0) | 1 (transient) | GASW-4.120 | none — WRITE_BUDGET/reconciliation artifact |

### 1.3 The "correct HARD de-risk, leave alone" carve-outs are mostly MISCLASSIFIED (refuted)

The prior GROUND/ANALYSIS labeled **A15** and **GASW-4.140** as genuine HARD reduce-only ("$0 cost, correct"). Two refuters overturned this; I verified the A15 flip in code:

- **A15 is NOT hard.** `KXTRUMPENDORSEMENTS-…-A15` is CATEGORICAL: `_strike_of("A15")` → `float("A15")` raises → `None` (`:2050-2058`). `_is_ladder_event` returns `False` when any strike is `None` (`:2121-2122`), so `event_deltas` keys the position **per-ticker**, never as an aggregate (`:2149-2151`; `event_delta_for` takes the ticker key first, `:2160`). GROUND's "+71 event delta → HARD" is a number the bot **does not compute** for this categorical series — the live journal even logs `strike parse FAILED on 5 held ticker(s)`. A15's true directional delta is its per-ticker **+20 < HARD 60** → throttle, two sides INTENDED. It rests NO-only because the throttled accumulating YES steps to `0.04` and is dropped by the strict `MIN_PRICE=0.04` floor — a **price-bound edge case at a 5¢ strike**, not a risk envelope. So its ~13 h one-sidedness is a suppression bug, not de-risk.
- **GASW-4.140 is DISPUTED between refuters** and I did not fully resolve it read-only: one lens read raw held **+60 = HARD** (`:912`, genuine reduce-only, $0 cost); another applied `ladder_pairing` (`:2061`) which nets naked to **+35 < 60 → throttle → funding-skip**. The difference is whether pairing has floored part of the position. **Flag: the genuine-HARD residue may be ≤1 market or zero at these instants** — the "leave alone because it's correct de-risk" set is much smaller than GROUND claimed. GUESS-free resolution needs a live `ladder_pairing` replay on 4.140's exact held book; not performed here.

**Net for §1:** at the sampled instants essentially ALL held one-sided markets are funding-skip or price-bound artifacts, NOT deliberate de-risk. The genuinely-correct one-sided residue (wind-down within 20 min of expiry, void-book, strand-out-of-footprint, and any true HARD) exists in the code (`:570-586`, `:631-647`, `:1254-1276`, `:912`) and DOES fire at other times, but was near-absent in this window.

---

## (2) WHAT IT COSTS — reward forgone

**Mechanism (real):** the scorecard's per-market snapshot score `our_snap = (ys + ns)/2 if book_2s else 0` (`kalshi_market_scorecard.py:101`). Resting one side zeroes one term. On the R4 qualifying share, our lone-side share is typically near-zero because the side that rests is the DEEP reducing quote (e.g. NO@0.92), far from the reference tick where DF-weighted reward concentrates. Measured lone-side shares: GASD-4.110 NO **0.1%**, 4.160 NO **1.6%**, T9.80 YES **3.5%**, A15 **0% both**.

**Dollar magnitude — REFUTED as material; it is NOISE (two independent lenses, §M7 applies and then some):**

- The R4 model's own docstring warns it over-predicts **2–6×** (§M7). Here the gap is WORSE. Single-instant qualifying-share → $/day conversions are physically impossible and unstable:
  - Naive `qualifying_share` (our size absent from denominator) gave a missing-side share of **701%** on 4.110 and "$527/day forgone" from a **$150/day pool** — impossible on its face (codebase impossible-number rule → the query is wrong, do not report it).
  - LIP-corrected `our/(total+our)`: GASD-4.115 missing-side share read **39.7%** at one instant (→ modeled $29.8/day, alone 62% of a $48/day instant total) and **5.5%** minutes later — a **7× swing** driven purely by transient competing-maker depth AT the reference tick (§M2: "we are never the marginal maker"). A per-instant share→$ estimate is unrealizable.
- **Receipts ceiling (decisive):** forgone reward cannot exceed reward earned. Total account reward credits are **a few $/day** (ledger); total realized fill P&L is **+$1.25 over 3.84 days**. A single missing side cannot forgo $15–48/day. The prior ANALYSIS's hedged "**$1–3/day real cost**" is itself above what the receipts ceiling allows for the time-averaged one-sided set.
- **Time-averaged truth:** the one-sided set churns every cycle (measured 7→4→2→5 within ~4 min; membership rotates); most one-sided markets are HELD where the missing side is the already-resting reducer, HARD-zeroed by design, or a THROTTLE quote the code intends at `MIN_QUOTE_CT=2ct` not the 20ct counterfactual (so the 20ct estimate overstates ~10×). **Durable, receipt-consistent forgone reward is well under $1/day — indistinguishable from zero** (§M7-inflated model figures are NOT reportable as dollars).

**FLAT one-sided specifically (operator asked to isolate this):** pools are tiny — KXCHIPBURRITO T9.76/T9.80 pool ≈ $6.6/day → share-delta ≈ **$0.005–0.02/day** (§M7-model, upper bound). Immaterial. Note the FLAT set is NOT as transient as ANALYSIS claimed — CHIPBURRITO was flat one-sided 6/6 consecutive samples (~5 cycles) — but the dollar weight is noise regardless.

**Reduce-only (correct de-risk) cost = $0** by construction: the accumulating side is zeroed on purpose (`:912`→count 0), so there is no forgone earning side to price. Confirmed.

---

## (3) WHAT IT PROTECTS — avoided adverse fill (honest, partly illusory)

**Measured avoided cost is tiny, and on FLAT markets one-sidedness can INCREASE risk:**

- Missing-side maker fill rate (500-fill window, 3.84 d) ≈ **2–11 ct/side/day** on the near-dated strikes we quote. At the gas fingerprint −$0.011/ct, avoided adverse ≈ **$0.05–0.11/day per market, ~$0.2–0.5/day aggregate**.
- **Maker fills are NOT adverse in aggregate:** 4,424 maker contracts at **$0 fee**, total realized **+$1.25** over 3.84 d; per-series gas realized KXAAAGASD −$0.19, KXAAAGASW +$1.25 — no bleed. The −$0.011/ct is a microstructure estimate that has NOT shown up as realized loss, so the "protection" is largely theoretical.
- **On FLAT markets, removing the offsetting leg INCREASES risk** — traced GASW-4.120 (§1.0): a lone flat leg that fills creates naked inventory with no hedge resting. So the FLAT-market one-sidedness protects nothing and can hurt.
- **On HELD gas markets the "protection" is directional and real but bounded:** the skipped side is the ACCUMULATING side of an existing long, so skipping it caps directional growth of a +28/+32-ct near-expiry ladder. But this is bounded tiny by `MAX_MARKET_CAPITAL=$15/market` and the `DAILY_LOSS_HALT=$40`, and realized P&L shows no gas bleed. **Refuter tension worth stating:** because the skip is directionally ALIGNED with de-risk on held longs, "relieve capital so the earning side places" would RE-ADD directional inventory to chase reward that is (a) a thin-book artifact and (b) only earnable by increasing risk. So the protection is real in direction but the reward it forgoes is near-zero — both sides of the trade-off are immaterial.

---

## (4) NET VERDICT

**No CRITICAL/HIGH refuter finding. All MEDIUM. Verdict: our current one-sidedness is a low-dollar BUG of capital over-commitment, NOT a de-risk feature — but the bug is dollar-immaterial, and the naive "fix" (quote both sides at full size everywhere) is unfundable and would fight the risk control.**

Regime by regime:

- **Genuine reduce-only de-risk** (wind-down `:570-586`, void `:631-647`, strand `:1254-1276`, true HARD `:912`): **RIGHT, $0 reward cost.** But near-absent at the sampled instants (§1.3); the two carve-outs GROUND labeled "correct" (A15, GASW-4.140) are misclassified/disputed.
- **FLAT one-sided** (funding-skip / selection-churn `:620-624`, `:1653-1656`): **net-NEGATIVE but immaterial.** Forgoes ~$0.005–0.02/day (§M7-model), saves ~$0 risk, and can create naked-fill exposure. A bug, dollar ≈ noise.
- **HELD-throttle one-sided** (funding-skip of the accumulating leg on a sub-HARD long): **net roughly NEUTRAL and MISLABELED.** It is capital starvation ($352 committed vs $250 cap vs ~$184 free cash), not a de-risk choice. Its reward cost is sub-$1/day (receipts ceiling), its risk saving is ~$0.1/day and directionally aligned with de-risk. Immaterial in both directions.
- **The UNEXPLAINED anomaly** (§1.0, GASW-4.120 accumulating-leg-alone): a genuine defect direction (grows an unhedged long), but transient/non-recurring across 9 samples and dollar-immaterial.

**Bottom line:** the operator's premise "one-sided halves our reward" is TRUE at the mechanism level (`kalshi_market_scorecard.py:101`) but FALSE at the dollar level — the receipts ceiling caps the true time-averaged forgone reward well under $1/day. There is no material money being left on the table and no capital at risk. The one thing that is genuinely wrong (funding-gate one-sidedness rooted in $352 > $250 over-commitment) is a **capital-sizing** issue, not a discrete quoting defect.

---

## (5) RECOMMENDATION (no code deploy from this review)

1. **Do NOT "fix" one-sidedness by quoting both sides at full size.** It is unfundable ($352 already committed > $250 cap) and on held longs it would fight the throttle by re-growing directional inventory. This is the trap the prior ANALYSIS's "relieve the capital constraint" framing walks into — reject it.

2. **Root issue is over-commitment, not gating.** The funding gate (`:1653-1656`) is arguably working as designed: it correctly keeps reducing creates and drops accumulating creates when the account can't fund them. The lever, if any, is **how much capital each market consumes**, so both legs fit the budget — i.e. size, not sidedness.

   - **Tier-2, operator-gated config candidate:** **`KALSHI_JOIN_SIZE`** (live `=20`). Lowering it (e.g. to 10) halves per-accumulating-create cost, letting more second-legs clear `min(free_cash, 250)` and reducing funding-skip one-sidedness — at the cost of thinner quotes. Rollback: `export KALSHI_JOIN_SIZE=20 && sudo systemctl restart polymarket-maker-kalshi-live`. **Expected impact:** fewer one-sided held markets; per-market qualifying share on each side lower; net reward effect ≈ noise (receipts ceiling). Recommend **MEASURE-FIRST, do not ship blind** — the dollar upside is sub-$1/day.
   - **Do NOT change** `MAX_TOTAL_CAPITAL` upward to "fund both sides" — that raises absolute exposure on a lane whose entire realized edge is +$1.25/3.84d; the constraint is doing its job.

3. **Investigate the A15 price-floor suppression (Tier-3, separate task).** A categorical 5¢-strike series (`KXTRUMPENDORSEMENTS`) where the throttled accumulating side deterministically steps to `0.04` and is dropped by the `MIN_PRICE=0.04` floor will sit one-sided indefinitely (observed ~13 h). Not urgent (dollar-immaterial, and categorical series are admitted cautiously), but it is a real edge case, not de-risk. No fix proposed here.

4. **Note the GASW-4.120 write-cycle anomaly (§1.0) for the next session.** If accumulating-leg-alone-on-a-held-long recurs and persists (it did not here), it is a `WRITE_BUDGET=60` / reconciliation ordering bug worth tracing — it is the only one-sidedness that increases nakedness.

5. **Correct the record:** future handoffs must NOT carry "A15 = correct HARD de-risk (+71 event delta)" or "GASW-4.140 confirmed HARD" as settled — the first is code-refuted (categorical, per-ticker +20), the second is unresolved read-only.

**GUESS flags:** GASW-4.140 hard-vs-throttle classification is UNRESOLVED (needs live `ladder_pairing` replay). The `KALSHI_JOIN_SIZE` impact estimate is directional, not measured. All $ figures are §M7-model upper bounds unless anchored to receipts (+$1.25/3.84d realized; a few $/day credits) — treat model dollars as inflated, receipts as canon.

---
*SCOPE: read-only, Kalshi venue only. No trades, deploys, config, or module edits. Temp analysis scripts (`_onesided_final.py`, `_onesided_rich.py`, walk/persist probes) are untracked; no tracked files modified.*
