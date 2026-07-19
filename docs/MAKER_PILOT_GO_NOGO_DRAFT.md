# MAKER PILOT — ENGINE-SIDE OPERATIONAL ANNEX (DRAFT)

**Status: DRAFT — propose-only. The DECISION document is the repo-root
`AGENT_HANDOFF_2026-07-18_MAKER_PILOT_DECISION_PACKAGE_DRAFT.md` (sibling
session, 2026-07-18 ~22:15Z) — verdict, canon numbers, income anchor, and
the two data gates live THERE. This annex maps that package onto the live
engine's actual switches: config, kill wiring, preflight steps, rollback.
The operator decides capital, mix, and kill numbers; nothing trades real
money until the package is signed and the funded preflight passes.**

**CORRECTION (logged out loud, 2026-07-19):** an earlier revision of this
annex quoted per-tier EV/ROI figures ($1K/$2.5K blind tiers) from a fresh
canon run. The decision package rules those NOT QUOTABLE — the 2-day
trading band (±$5–9K) exceeds the signal and the tier sign-flips are mark
noise. The table is removed; small-capital ROI has no quotable value until
the post-cup re-measure + a longer window. Rewards-with-rules
($589–951/day full-footprint MODEL accrual, canon 22:12Z run) and
no-rules-loses (~$11K/day) are the only two statements that clear the
noise.

## 1. What is being proposed

A small real-capital maker pilot on Polymarket running the live engine
(`scripts/maker_live_engine.py`) in `MAKER_SUBMIT_MODE=live` on the VPS,
post-only GTC, breadth-at-min-size, full guard stack. Purpose: convert the
model-estimated reward capture into RECEIPTS to our own wallet — the only
number that verifies income.

## 2. Capital ask

**$1–2.5K pUSD** per the decision package (small enough that the worst
theoretical day — every cap maxed — is a bounded, pre-stated number the
operator signs). No tier-level ROI is quotable yet (see CORRECTION above).
Do NOT scale before (a) the post-cliff pool re-measure (Jul 20+) and
(b) two weeks of receipt-verified capture.

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
   `MAKER_GATE_POLICY` (no code change). Interim leader per the decision
   package: P3 (base + fast-trading alarm), 2-day band-caveated; the lab's
   ≥3-clean-day paired read decides, not the interim.
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

## 6b. Known accepted risk — NEEDS OPERATOR CONFIRMATION

**The automated day-loss floor is blind to departed markets' real losses**
(2nd-pass review M3): a market that rotates out of the rewarded universe
keeps a FROZEN mark; if it later resolves worthless, that loss never moves
`day_pnl` and cannot trip the floor. The `--report` output discloses the
departed bucket as UNSETTLED. The real fix is resolution backfill
(gamma outcome join) — queued as a pilot iteration. Until then the floor
protects only against live-market drawdown. Confirm this is acceptable for
a $1–2.5K pilot or prioritize the backfill before funding.

## 7. Rollback

`sudo touch /opt/pa2-maker-live/STOP` (cancels all, exits clean) or
`sudo systemctl disable --now polymarket-maker-live`. Wallet drain-back =
operator (redeem/withdraw via the deposit flow). Engine HALT file semantics
in `deploy/polymarket-maker-live.service` header.
