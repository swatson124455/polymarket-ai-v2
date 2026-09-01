# POLYMARKET LIQUIDITY REWARDS — OFFICIAL RULES CANON (fetched 2026-09-01)

Item D1 of the Kalshi-failure port (operator-ordered 2026-09-01): the Kalshi
lane's most expensive failure was weeks of capital aimed at markets paying $0
by PUBLIC rule while running on a reverse-engineered guess. This doc pins
Polymarket's OFFICIAL rules, each element sourced, and diffs them against the
engine's model. Re-verify at every scale-up; rules drift.

Sources (both fetched 2026-09-01, live reads):
- S1: https://docs.polymarket.com/market-makers/liquidity-rewards (developer spec)
- S2: https://help.polymarket.com/en/articles/13364466-liquidity-rewards (user rules;
  docs.polymarket.com/polymarket-learn/trading/liquidity-rewards 307-redirects here)

## THE RULES (each sourced)

| # | Rule | Source |
|---|------|--------|
| R1 | Per-order score `S(v,s) = ((v−s)/v)² · b` — v = market max spread, s = spread from ADJUSTED midpoint, b = "in-game multiplier", quadratic | S1 §1 |
| R2 | `Q_one` = Σ S weighted by BidSize on m + AskSize on complement m′; `Q_two` = mirror | S1 §2 |
| R3 | **Midpoint ∈ [0.10, 0.90]: `Q_min = max(min(Q1,Q2), max(Q1,Q2)/c)` with c = 3.0 ("currently 3.0 on all markets") — SINGLE-SIDED LIQUIDITY SCORES at 1/3 rate** | S1 §3 |
| R4 | Midpoint ∈ [0,0.10) ∪ (0.90,1.0]: strictly two-sided `Q_min = min(Q1,Q2)` | S1 §3-4 |
| R5 | Sampling: scores computed **every minute via random sampling**; "10,080 samples per epoch" (= 7d × 1,440/min) per S1's wording — see OPEN-C1 on epoch semantics | S1 §5 |
| R6 | Allocation: `Q_final = Q_epoch / Σ Q_epoch` × market reward pool; paid to maker addresses **daily at ~00:00 UTC** for the previous day | S1 §6, §9; S2 |
| R7 | Per-market `min size` (reward-qualifying threshold) + `max spread` + reward allocation configured per market | S1 §7; matches gamma `rewardsMinSize`/`rewardsMaxSpread` |
| R8 | **MINIMUM PAYOUT FLOOR: $1** — "a day only pays out if your earnings for that day reach $1; below $1 not paid, and they do NOT roll over" | S2; S1 §8 |

## DIFFS vs THE ENGINE MODEL (corrections to prior canon — reported, not silently edited)

1. **"One-sided scores ZERO" is REFUTED for mid-range markets.** Doctrine port
   §3c, the elevations register, and the `MAKER_ONESIDED_DERISK` env comment
   all state a lone leg earns nothing (two-sided MIN). Official R3: for
   midpoint ∈ [0.10,0.90] a lone side scores at **1/3 rate**. Consequences:
   (a) the one-sided de-risk leg DOES earn (at ÷3) in mid-range — better than
   documented; (b) engine/model accrual using strict MIN **understates** our
   one-sided credit; (c) the softness probe's competitor score counts only
   what our model counts — single-sided campers in mid-range earn too, so our
   modeled SHARE may **overstate** (uncounted competitors). Strict-MIN remains
   CORRECT for midpoints outside [0.10,0.90] (R4).
2. **$1/day payout floor exists (R8)** — the Kalshi-cliff analog, previously
   unmodeled. Reads as per-address-per-day (S2 wording "your earnings for that
   day"); scope per-market vs per-user not fully disambiguated (OPEN-C2). For
   the ~$60 pilot: total modeled/day must clear $1 or the day pays $0.
3. **`b` "in-game multiplier" (R1) is unmodeled** — value/source unknown
   (OPEN-C3); if b varies by market/promo, per-market model error follows.
4. **Sampling epoch wording (R5)** — "10,080 samples per epoch" implies a
   7-DAY share window while payment is daily (R6); if share is computed over
   a 7d epoch, day-level accrual models mis-time competition effects
   (OPEN-C1).

## OPEN CONFIRMATIONS (settle before/at the first receipts read)
- OPEN-C1: epoch = 1 day (1,440 samples) or 7 days (10,080)? Decide from the
  first receipt's arithmetic vs the calibration table.
- OPEN-C2: $1 floor per ADDRESS-day (S2 reading) or per market-day? First
  receipt on a multi-market day disambiguates.
- OPEN-C3: `b` multiplier default + variability.
- OPEN-C4 (pre-existing, unchanged): does a sub-msz order score at all —
  empirical `is_order_scoring` probe at `--stage scoring` ($3 vs $20).

## WHAT THIS DOES NOT CHANGE
No engine behavior change is made on this doc alone (RULE NINE: the strict-MIN
accrual model and every doc claiming "one-sided = zero" are corrected via this
canon + tab row, pending operator direction on whether to update the model).
Positions/quoting are unaffected; this is measurement/model canon.
