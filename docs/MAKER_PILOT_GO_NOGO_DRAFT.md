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
| Portfolio day loss | −$75 (MAKER_DAY_LOSS_FLOOR_USD) — **size to footprint**: burn-in 07-19 killed at −$166 marks on ~$1K gross (cup-final vol); $75 vs $1K = 7.5% daily-drawdown trigger, tight by design for paper | cancel-ALL then HALT (engine) |
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

## 6b. Departed-market resolution backfill — CLOSED (was M3)

Resolution backfill is BUILT (hourly `resolution_sweep`, gamma outcome
join, reviewed): departed markets settle at their 0/1 outcome (or UMA-final
splits), the mark→outcome jump hits `day_pnl` and CAN trip the floor, and
`--report` splits settled (realized) from unsettled (frozen). Residuals,
disclosed: (a) settlement lag up to the 1h sweep cadence + gamma resolution
timing; (b) a stale loss settling today can floor-kill today's healthy
quoting — the kill reason carries `settle_realized` so the operator can
distinguish stale-loss kills from live bleed before resuming; (c) settled
entries accumulate in state (pruning deferred — needs a realized-aggregate
fold to keep `portfolio_net` and the day anchor consistent).

## 6c. Independent verification (workflow, 4 agents, 2026-07-19) + residual watch items

Gate-lab cross-check: the v5 lab paired ledger (independent of the canon
script) AGREES with canon on every load-bearing rank — P3_tapevel/P4_all top
tier, P0_base beaten by both, P5_ungated worst. Policy switch stays HELD
(window 2.6d < 3.0, and Jul 19 is the cliff day — the 3-clean-day clock
should restart AFTER the cliff). ⚠ Presentation trap for whoever locks it:
the v5 report's headline "active markets" table ranks P5 (ungated) #1 because
P5's damage sits in departed/frozen-mark markets excluded from that table —
**rank only on total (active+departed) NET or canon EV/day, never the
active-only column.** P3-vs-P4 tie-break deferred to decision-time on capital
efficiency.

Cold-eyes code audit fixed this session (settlement path): kill-reason
`settle_realized` now reports the day-jump not lifetime realized (was masking
live bleed from the resume decision); settled siblings excluded from the event
floor; per-settle state persist (ledger-dup on mid-sweep crash); heartbeat
`respend` is now a live count (was a stale sweep-snapshot, off-by-one).

WATCH ITEMS (documented, not code-changed — changing risks re-introducing the
respend-stuck bug just fixed): decisive settlement (prices within 1e-3 of 0/1)
can proceed when gamma's `umaResolutionStatus` is absent — verified populated
on the path form for real resolutions, but a closed-pre-final row with
decisive-looking prices and an empty UMA field would settle irreversibly.
Rewards accrual is MODEL, never yet reconciled to an on-chain receipt — the
funded preflight's receipts stage is the first real check. Paper→live fill
fidelity (queue position, adverse selection at quoted levels) is unmeasured
until the pilot.

## 7. Rollback

`sudo touch /opt/pa2-maker-live/STOP` (cancels all, exits clean) or
`sudo systemctl disable --now polymarket-maker-live`. Wallet drain-back =
operator (redeem/withdraw via the deposit flow). Engine HALT file semantics
in `deploy/polymarket-maker-live.service` header.
