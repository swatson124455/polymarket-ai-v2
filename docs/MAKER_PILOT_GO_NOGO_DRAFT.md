# MAKER REAL-CAPITAL PILOT — GO/NO-GO PACKAGE (DRAFT)

**Status: DRAFT — propose-only. The operator decides capital, mix, and kill
numbers. Nothing trades real money until the operator signs this package and
the funded preflight passes.**

Drafted 2026-07-18 (engine-build session). Numbers below are from the
2026-07-18 fresh run of `scripts/maker_research/mm_roi_canon.py` on the VPS
(1.96-day NET window, model-accrual rewards, PRE promo-cliff) — re-run the
script before deciding; do not reuse these figures after Jul 19.

## 1. What is being proposed

A small real-capital maker pilot on Polymarket running the live engine
(`scripts/maker_live_engine.py`) in `MAKER_SUBMIT_MODE=live` on the VPS,
post-only GTC, breadth-at-min-size, full guard stack. Purpose: convert the
model-estimated reward capture into RECEIPTS to our own wallet — the only
number that verifies income.

## 2. Capital ask (operator chooses a tier)

Per the canon blind-tier table (2026-07-18 run; model accrual, short
pre-cliff window — LOWER-leaning per canon caveat 4):

| budget | markets | EV/day (model) | of which rewards | ROI/day |
|-------:|--------:|---------------:|-----------------:|--------:|
| $1,000 | 39 | $64 | $63 | 6.5% |
| $2,500 | 41 | $298 | $63 | 12.0% |

Recommendation held from the handoff: **$1–2.5K**. Do NOT scale past this
before (a) the post-cliff pool re-measure (Jul 20+) and (b) two weeks of
receipt-verified capture.

## 3. Mix (from measured pain, adverse-magnitude pass 07-18)

- **Led by**: weather + politics dailies (farm tier; strongest reviewed niche).
- **Excluded**: esports (worst adverse by 10×), finance (dropped per handoff).
- **Sports**: allowed with in-play gate (engine default).
- **Geopolitical**: demoted per operator ruling — engine may quote only if it
  survives sector caps; operator may zero it via `MAKER_SECTOR_CAPS_USD`.
- Subjective-settlement markets: size-cap rule (playbook review) — encode via
  per-market gross cap.

## 4. Pre-registered kill numbers (PROPOSED — operator edits then they bind)

| Trigger | Threshold (proposed) | Action (automatic) |
|---|---|---|
| Portfolio day loss | −$75 (MAKER_DAY_LOSS_FLOOR_USD) | cancel-ALL then HALT (engine) |
| Settlement revert (#338 class) | first occurrence | operator page + halt (manual day-1, engine metric) |
| Receipt vs model divergence | >50% and >$5 for 2 consecutive days | halt + share-model investigation |
| Book freshness | >180s stale | auto-unquote (engine interlock) |
| Pool vanished | census/discovery drop | auto-unquote (engine) |
| Cumulative pilot loss | −$250 | operator kill (manual, pre-registered) |
| Share decay (anti-landmark tell) | our share halves at stable footprint | rotate/retreat, operator review |

## 5. Preconditions (in order)

1. **Operator**: deposit-flow-provisioned wallet (pUSD via Collateral Onramp
   `wrap()`, POL for gas, V2 exchange approvals), key into
   `/opt/pa2-maker-live/env` per `deploy/maker-live-env.example`.
   Eligibility/KYC = operator compliance question (geo: VPS eu-west-1 passes;
   residential IP is 403).
2. **Engine**: paper-mode burn-in on VPS ≥48h clean (no guard misfires, hb
   healthy, accrual model plausible vs the recorder arms).
3. **Gate-lab read (~07-20)**: pick the winning gate policy via
   `MAKER_GATE_POLICY` (no code change). 2026-07-18 canon snapshot: P3_tapevel
   EV/day $1,373 vs P0_base $1,041 on 1.96d — short-window, band-caveated;
   the lab's paired read decides, not this snapshot.
4. **Funded preflight** (`scripts/maker_preflight.py`): sanity → scoring
   (is_order_scoring TRUE on a resting min order) → fill (tiny FAK, tx hash
   recorded, no revert) → first-midnight receipts vs model.
5. **Post-cliff re-measure** (Jul 20+): if the operator wants to wait for the
   post-promo pool baseline before funding, the engine loses nothing by
   running paper — the decision date is the operator's.

## 6. What the pilot measures (success criteria)

- **Receipts/day to our wallet** vs model accrual (the paper-twin alarm) —
  the pilot's primary output.
- Fill toxicity on OUR fills (adverse>1pt/2pt rates vs the 34%/27% recorder
  baseline).
- Share-at-size self-calibration (predicted vs paid, per market).
- Settlement success rate (#338 metric, expect ~0%).
- 2 weeks receipt-verified → scale decision package (separate, new canon run).

## 7. Rollback

`sudo touch /opt/pa2-maker-live/STOP` (cancels all, exits clean) or
`sudo systemctl disable --now polymarket-maker-live`. Wallet drain-back =
operator (redeem/withdraw via the deposit flow). Engine HALT file semantics
in `deploy/polymarket-maker-live.service` header.
