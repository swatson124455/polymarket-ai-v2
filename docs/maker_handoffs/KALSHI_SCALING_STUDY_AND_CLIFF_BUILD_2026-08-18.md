# SIZE-vs-ACCRUAL SCALING STUDY + CONCENTRATED-CLIFF BUILD PLAN
# (2026-08-18; v2-RECALIBRATED + F9 RECOUNT FOLDED 2026-08-19)

## ⛔ RETRACTION (2026-08-18 review; folded into this doc 2026-08-19)
The original v1 text of this doc claimed **$1.56/day/market at 30-59ct, ~linear
scaling, and a $12-16/day portfolio projection. ALL THREE ARE RETRACTED** (v1 had
three inflation biases caught by the per-section adversarial review; v1 script
`size_scaling_study.py` is superseded by `size_scaling_v2.py`, hardened per review
F1-F8). The v1 table is preserved below, struck, for the record only.

## v2 GOVERNING NUMBERS (hardened; from the 2026-08-18 handoff, ESTABLISHED unless labeled)
- Survivable classes (D3 measured allowlist): 1-9ct **$0.24/d**; 10-29ct **$1.10/d**
  (13 ticker-days); 30-59ct thin (n=3 ticker-days).
- **Honest per-market-day: $0.50-0.63 on the BEST real days** (optimistic tail, not
  a median — typical days run lower and are unquantified).
- **Size scaling is SUB-LINEAR**: within-ticker contrast ≈ 1.8x accrual for ~4x size.
- Existence proof: KXTOPMODEL-26AUG31 pair cleared the per-program $1 cliff at
  10-50ct over ~3d windows and PAID in full ($1.05 + $1.42, per-program credit rows).
- **Portfolio ceiling at current universe: ~$4-6.5/day gross → $2-5/day net**
  (INFERRED from measured rates; fill-cost side budgeted in F14 below).
- 1,673 ticker→pid map collisions flagged (dailies re-key programs; v2 sums sibling
  pids per ticker — double-count class check open).

### v1 table (RETRACTED — record only)
~~1-9ct $0.20-0.29 | 10-29ct $0.17-0.58 | 30-59ct $1.56 full presence / best hours
$4-8/day pace; "scaling ~linear"; "8-10 markets = $12-16/day gross"~~

## SCALING-REVIEW FINDINGS FOLDED (required before any relight ask)
- **F9 — universe recount under the exact plan filters: DONE 2026-08-19T02:20:42Z**
  (script `workflow_scripts/f9_universe_recount.py`, frozen JSON
  `F9_RECOUNT_2026-08-19.json` md5 e5a1c4c9). Results in the next section.
  (Distinct from cliff-review F9 = sub-$1 credit re-read ~08-21+, still queued.)
- **F14 — per-market fill-cost budget: designed below** (§F14).
- **F15 — per-market $ caps + halt semantics at 40-60ct: designed below** (§F15).
- **F12 — TEXT UNRECOVERED.** The finding's content exists only in the closed
  session's transcript (not in any committed doc, box file, or searchable local
  transcript; searched 2026-08-19). NOT dropped — flagged per Rule Nine. If the
  operator can supply it, fold it here; otherwise the committed dispositions
  (retraction, sub-linear label, best-days label, F9/F14/F15) are the governing set.

## F9 RECOUNT RESULTS (read 2026-08-19T02:20:42Z; ESTABLISHED counts, INFERRED projections)
Funnel: 3,542 active liquidity programs → 81 survivable-class (KXUSDJPY 15,
KXAAAGASD 17, KXTOPMODEL 5, KXAAAGASW 23, KXDIESELW 21) → 49 after close≤8d +
runway≥49h (32 dropped, all runway<49h — USDJPY short-window + AAAGASD dailies)
→ **25 after the near-strike toxicity overlay** (24 removed with mid in
(0.10,0.90) — measured-toxic ladder_near_strike mechanism; the overlay was MISSING
from the first recount pass and self-caught in review).

**The 25 investable candidates** (all runway 4.07-5.07d, all in the preferred 3-7d
window, all extreme-price mid ≤0.095 or ≥0.91 — the TOPMODEL-26AUG31 existence-proof
shape): 18× KXAAAGASW-26AUG24 tail strikes (pool $100/d each), 4× KXDIESELW-26AUG24
tails (pool $120/d), 3× KXTOPMODEL-26AUG24 longshots (pool $200/d). Full list in the
frozen JSON.

Cliff projection: runway × $0.50-0.63/d → $2.03-3.19/program per candidate, all
clear the $1.50 gate — **but the gate discriminates nothing at these runways; the
PASS verdicts inherit the best-days rate assumption (INFERRED).** The daily pacing
gate (§build delta 4) is the protection: sub-cliff pacing → cancel + reallocate.

**Review notes on this section (adversarial, incl. EV lens):**
1. Mids were read once (02:20Z meta quotes) — near-strike status DRIFTS. The
   near-strike gate must be LIVE at entry AND a daily eviction check, not a
   one-shot list (folded into delta 3 below).
2. Concentration: 18/25 candidates are ONE event (AAAGASW-26AUG24). A gas-price
   move relocates the whole ladder: simultaneous near-strike evictions +
   correlated adverse fills across every held tail. Per-EVENT cap mandatory (§F15).
3. **Capital cap is binding**: two-sided pair collateral at extreme prices ≈
   ct × ~$1.00/market (rich side ~0.99 + cheap side ~0.005). At balance $246.8126
   (read 02:13:03Z): 8 mkts × 50ct ≈ $400 DOES NOT FIT. Fits: 5×50ct ≈ $250 (no
   buffer), 6×40ct ≈ $240 (no buffer), 5×40ct = $200 (+$46 buffer). → operator
   decision at relight ask: smaller footprint vs top-up (~$150-200 restores
   8×50ct with buffer). Scale-plan canon: top-up is contingent on rung proof.
4. EV: expected gross carries downside vs the projection (best-days rate); the
   pacing gate bounds dead-weight (sub-cliff accrual) but not fill cost — that is
   §F14's job. Books were NOT read in the recount; near-touch-paired feasibility
   at extreme prices is a shadow-phase question.

## §F14 — PER-MARKET FILL-COST BUDGET (design; scaling-review F14)
- Unit cost basis: D3 survivable band **−2..−3.5c/ct** (measured class average;
  never the whole-probe 0.11 revenue/cost blunt ratio — cliff-canon review F15).
- Worst-case per market = full rich-side collateral (tail event: 0.99 → 0
  ≈ ct × $1). This is bounded by §F15 per-market caps, not by the unit band.
- **Budget rule: a market's projected program revenue (cliff-gated, so ≥$1.50)
  must exceed its EXPECTED fill cost at the D3 band: expected_fills_ct ×
  3.5c/ct < 0.5 × projected accrual.** Operationally: at 50ct resting, one full
  turnover of the book costs ≈ 50 × 3.5c = $1.75 — i.e., ONE adverse turnover
  wipes a $1.50-2.5 program. Therefore re-quote-on-fill discipline + the $10/day
  halt are load-bearing, and the shadow phase must measure fill frequency in
  THESE books before relight (pre-registered: if shadow-observed touch-trade
  frequency implies >1 expected turnover/program at our depth, the market fails
  the budget and is not entered).
- Extreme-price asymmetry: the RICH side (0.99) loses big/rarely, the cheap side
  (0.005) loses small/often. Both sides count in the budget at the band rate;
  the rich-side tail is capped by §F15, accepted as the strategy's structural
  risk (reward-positive, defect-negative framing does not apply forward — this
  is priced strategy cost).

## §F15 — PER-MARKET $ CAPS + HALT SEMANTICS AT 40-60ct (design; scaling-review F15)
- **Per-market collateral cap: ct × $1.00 (≈ $40-60 at plan size). Hard cap $60.**
- **Per-EVENT cap: ≤3 markets of any one event ladder** (kills the 18-sibling
  AAAGASW concentration; also bounds correlated tail loss to ≤3 × per-market cap).
- **Portfolio cap: total resting collateral ≤ operator-named deploy number**
  (decision at relight ask; options in F9 review note 3).
- **Halt: $10/day window-equity basis UNCHANGED** (position-aware, the R1 probe
  gauge semantics, DD_CARRY discipline stands: post-halt restart carries the
  drawdown; relight after halt = operator-named governor reset, §11 precedent).
- At 40-60ct the halt trips at ~3 adverse rich-side fills (3 × 50ct × ~7c avg
  move-through ≈ ... measured live, not modeled — the point: halt-to-cap ratio
  10/60 means a single market CANNOT eat more than ~1/6 of its collateral in one
  day without halting the day). Cancel physics: ≤12 markets bound holds (80
  cancels × 0.6s); at 8 markets two-sided = 16 orders, well inside.
- Eviction semantics (cliff pacing + near-strike drift): daily check → cancel +
  reallocate to the next candidate from the frozen-then-refreshed F9 list;
  est-feed 3-state gate governs (stale → FREEZE-AND-HOLD, never fire-sale).

## BUILD: CONCENTRATED-CLIFF MODE (on the existing quoter chassis, per T7 no-rewrite)
Config/code deltas (each reviewed + tested before relight; relight itself =
operator-named GO after shadow):
1. FOOTPRINT: cap ~8 markets (≤12 cancel-physics bound holds) — ACTUAL number =
   operator capital decision (F9 review note 3).
2. SIZE: per-market two-sided resting 40-60ct (within §F15 caps).
3. SELECTION: series allowlist = D3-survivable slow-mechanical classes ONLY
   (KXAAAGASD/KXAAAGASW/KXTOPMODEL/KXCLAYTONDNI/KXDIESELW/KXUSDJPY/KXCLARITYVOTE);
   measured-toxic mechanisms hard-excluded WITH a live near-strike gate
   (entry + daily re-check, mid in (0.10,0.90) = out); close≤8d LOCKED;
   runway = min(close, program_end) − now ≥ 49h; prefer 3-7d program windows.
4. CLIFF GATE: enter/hold only while projected accrual (measured curve × remaining
   window, replaced by live est-feed pace once accruing) ≥ **$1.50/program**
   (bracket ($0.9719,$1.0034] + feed staleness margin — cliff canon F1). Sub-cliff
   pacing at daily check → cancel + reallocate (est-feed 3-state gate: stale →
   FREEZE-AND-HOLD, never fire-sale).
5. reward_pnl LEAKAGE FIX: **DONE + DEPLOYED 2026-08-19 02:04Z** (md5 1ecd8060;
   accrued-at-conclusion from tape history + SUBCLIFF status).
6. SCOREBOARD RE-REGISTRATION for the new window (it still scores the dead 08-12
   window until edited) + new-window pre-registration (T0, PASS = counted credits >
   |position-aware drag|, per-EVENT same-set scoring, verdict at end+72h).
Unchanged: $10/day halt, post-only, re-quote-on-fill, STOP discipline, all kill
switches, KALSHI_LIVE_ARMED three-lock, deploy md5-vs-blob, suite exit codes.

Calendar (operator picked (a) 2026-08-19 ~02:1xZ): build+review → shadow →
relight ask (operator GO) → run to ~08-25 → payment reads 08-26/27 → mandate
decision 2026-08-27.
