# WITH-EXITS ESTIMAND — ACTIVATED (ANALYSIS LENS)

**Status: ACTIVATED 2026-09-07 (operator ruling, verbatim: "with-exits
D1 = b, D2 fresh epoch at activation, D3 analysis lens").**
- **D1 = (b):** exit priced at whale SELL price MINUS the measured
  BUY-side follow-cost median (re-measured from the shadow sink at each
  run and printed — a disclosed transfer; (c) real exit fills remains
  the named requirement before any GO weight, unchanged).
- **D2: EPOCH = 2026-09-07T03:30:00Z** (clock measured 03:10:39Z at
  activation, next clean boundary; IMMUTABLE — epochs never move). Only
  positions entered at/after the epoch are graded.
- **D3: ANALYSIS LENS** — reported beside the registered estimand,
  neither replaced; never a gate, never a stake input.

**Implementation (deployed with this activation):**
`mb_canon.market_position_rois_with_exits` (amendment block in-file:
canon fees on BOTH fills of an exited round trip, exit price clamped to
[0,1], pre-entry and post-resolution SELLs ignored, unexited positions
hold to resolution exactly as the registered basis, unresolved unexited
skipped). Surface: a WITH-EXITS table printed after the roster
leaderboard in `mb_backtest replay`/`daily-replay` (`--sells`, default =
the live sink; firehose own-SELL variant = named future work).
Self-test 17/17 PASS; 5/5 targeted mutants killed (entry-guard,
res-guard, clamp, exit-fee, first-sell ordering).

---

The original draft below is retained verbatim as the design record.

# WITH-EXITS ESTIMAND — PRE-REGISTRATION DRAFT (superseded by the
# activation block above)

Provenance: ZERO_BASED_SIFTER stage 3 names "with-exits once SELL
recording ships" as a parallel pre-registered estimand;
docs/MB_GO_CHECKLIST.md item 4 names it as the remaining half of the SELL
line. Copy lens ONLY (fade is dead — operator 2026-09-06). Basis = ROI +
net winnings, ladder-aware, ONE atom per MARKET position (operator
hardcode 2026-09-06; correlated-atom fix).

## Sink maturity (measured 2026-09-07T01:1xZ, counts only — NO outcomes
## were read; this draft peeked at nothing gradeable)

- roster shadow OK BUY records: 109,178 → 11,544 distinct
  (trader, token) entry positions (mirror3_shadow.jsonl)
- SELL sink records: 2,261 (mirror3_shadow_sells.jsonl, accruing;
  sink live since 2026-09-06 with the layout fix `8e9c989`)
- entry positions with a post-entry SELL by the same trader on the same
  token: **269**, across **36 traders**

VERDICT: NOT yet mature for per-trader grading (~7 joined positions per
covered trader on average, days of capture). Recommend revisiting when
the joined count supports per-trader n in the tens for the funnel's top
rows; re-measure with the same join (first post-entry SELL, same trader,
same token).

## Proposed estimand (for ruling, not active)

Per MARKET position (the ruled atom):
1. ENTRY: identical to the registered basis — all ladder BUY wagers,
   priced at our recorded shadow fill (roster) or whale VWAP + measured
   follow-cost (firehose).
2. EXIT SIGNAL: the tailed trader's FIRST post-entry SELL on that token
   before resolution ⇒ the copy exits its ENTIRE position (mirrors the
   one-shot copy policy; partial-exit tracking is named future work, and
   sizing the whale's partial exits against our different position size
   would invent a mapping). No SELL before resolution ⇒ the position
   holds to resolution (identical to the current estimand).
3. ROI per wager under an exit at price x: (x − fill − fee)/fill, fees
   per canon; position atom = equal-stake mean of its wager ROIs
   (mc.market_position_rois structure, exit outcome substituted for the
   resolution outcome on exited positions).
4. PASS bars unchanged (ruled): e ≥ 20 AND LCB net winnings ≥ $100/wk at
   $100/wager reference; futility 1 week; anytime-valid e-process over
   the same physical-floor lambda-subgrids. NOTE the exit-price support
   differs from the resolution support (x ∈ (0,1), not {0,1}); the
   physical ROI floor −1.10 still bounds it — no new grid derivation.

## OPEN PARAMETERS — operator decisions required at activation

**D1. Exit pricing (the sink records the WHALE's price; we have no shadow
SELL fills, so OUR exit price is not yet measurable).** Options:
  (a) whale SELL price, zero haircut — optimistic, simplest, biased FOR
      the trader being tested; every quote carries the optimism label.
  (b) whale SELL price minus the MEASURED BUY-side follow-cost median —
      symmetric-cost assumption, disclosed as a transfer (BUY latency
      cost applied to SELLs; not measured on sells).
  (c) defer activation until a pilot/shadow SELL executor records real
      exit fills (GO checklist item 3's slippage recorder would cover
      this) — the only fully measured option.
  RECOMMENDATION: (b) for the analysis lens now, (c) before any GO
  weight — (a) only if the operator explicitly accepts the optimism.
**D2. Epoch.** Fresh epoch at activation (registered trials' epochs are
  immutable; this is a NEW estimand, not a conversion).
**D3. Standing.** Analysis lens beside the registered estimand (both
  reported, neither replaced) — replacing or gating on it would need its
  own ruling.

## What was deliberately NOT built yet

The estimand function itself. Building it before D1 is ruled risks
building the wrong exit-pricing rule into canon right after the canon
review closed; the join/maturity measurement above is the full extent of
this session's look at the data.
