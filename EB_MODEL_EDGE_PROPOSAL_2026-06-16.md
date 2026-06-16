# EsportsBot Model-Edge Investigation + Proposal — 2026-06-16

**Session:** EB splinter (eb/main), read-only investigation. **Mode:** paper. No code changed, nothing deployed.
**Predecessor:** `AGENT_HANDOFF_EB_2026-06-14.md` (§3 model-edge plan). Infra churn chain is CLOSED+verified — this is pure model work.

## TL;DR
EsportsBot V1 is unprofitable (**−$4,463.78 all-time clean, WR 37.5%, raw edge −6.12%**; `edge_verification.py EsportsBot --clean`, n=536). The root cause is **not** miscalibration, an inverted signal, a loose threshold, or sizing — it is that **the model is a strictly worse forecaster than the Polymarket CLOB price**, and the bot's trade rule (enter when `model_prob − price ≥ min_edge`) **selects for the model's largest errors**. This is adverse selection against a sharper line. No threshold/sizing/recalibration tweak fixes a model that loses to the market everywhere; the fix must make the bot defer to the price far more, or earn the right to deviate by proving out-of-sample skill vs the market.

## Phase 0 — infra durably held (verified, not assumed)
- 4 `scan_stall_self_restart`/24h (vs ~22 baseline); scan loop completing every ~60s (`esportsbot_scan_summary`); grace + scan-deadline mechanisms firing as designed. The 6-fix churn stack is durable. (journalctl, 2026-06-16 00:00 UTC.)

## Evidence (all read-only; calibration from `esports_prediction_log`, P&L from `edge_verification.py`/`bot_pnl.py`)

### 1. The market beats the model — everywhere (M1/M2, n=603 resolved)
| metric | model (predicted_prob) | market (market_price) |
|---|---|---|
| Brier (lower=better) | 0.247 | **0.181** |
| corr with outcome | +0.19 | **+0.53** |

Per game, market Brier < model Brier in **every** game: cs2 0.174 vs 0.268 · lol 0.185 vs 0.224 · dota2 0.182 vs 0.238 · valorant 0.203 vs 0.241.

### 2. The model has *weak positive* skill — NOT inverted
corr(pred,outcome)=+0.19; Brier-as-is 0.247 < Brier-flipped 0.312; reliability deciles trend upward. (An earlier read of the per-side table looked like inversion; the decile + Brier-flip check refuted it — the model is real-but-weak, just sub-market.)

### 3. The bot's "edge" is anti-predictive (M3 — the smoking gun)
Win-rate on the bet side vs the model's edge over the market price:
| bet edge (model−price, bet side) | n | side win-rate |
|---|---|---|
| 0.00–0.10 | 165 | 51.5% |
| 0.10–0.20 | 165 | 46.1% |
| 0.20–0.30 | 129 | 34.1% |
| 0.30–0.40 | 55 | 12.7% |
| > 0.40 | 63 | **7.9%** |

The `min_edge` gate (effective ~0.08) admits exactly the bets in the losing half of this table. **Bigger claimed edge → bigger loss.**

### 4. Why valorant looks "profitable" (the unifying mechanism)
valorant has the **thinnest** Glicko-2 ratings (avg phi 214, zero mature teams) → the `_get_glicko2_prediction` phi-prior blend pulls valorant predictions toward the market price → smallest model−price deviations → least adverse selection → ~breakeven/+ (n=53, partly variance). cs2/lol/dota2 have **mature** ratings → the model diverges confidently from the market → and gets punished because the market is sharper. **The closer the bot stays to the price, the better it does.** valorant is not a model success; it is the model getting out of its own way.

### 5. Secondary amplifiers (real, but downstream of #1)
- **Confidence inflation:** stored/sized "confidence" is the model side-prob × phase-mult (0.90 group) × expiry-boost (1.2–1.5), so `event_data.confidence` runs ~0.5 model_prob → 0.7–0.95 stored. The gate (`ESPORTS_MIN_CONFIDENCE=0.20` in `.env.esports`, code default 0.50) and the Kelly sizer both key off this inflated number. (`bots/esports_bot.py` ~3109/3164/4908.)
- **Sub-0.50 entries:** 150 trades entered below 0.50 stored confidence (−$1,587), several at model_prob≈0.20 (betting a side the model rates ~20% because the price made it "cheap").
- **Sizing keys off inflated confidence; the calibration dampener is bypassed.** EsportsBot V1's `calculate_bot_position_size` call omits `calibration_quality`, so `BotBankrollManager`'s Brier-based fraction reduction (`bankroll_manager.py:396-401`) never fires. Sizing is broadly near the $300 cap across all confidence bins; the 15 worst single trades = 119% of net loss.
- **Team↔token alignment gap (latent, unconfirmed magnitude):** model computes P(team_a)=P(opponents[0]) and equates it to P(YES) with no check that the YES token pays out on team_a; all 603 markets are "TeamA vs TeamB" phrasing where YES assignment is arbitrary. This adds noise but is NOT the dominant driver — the dominant driver is that even the calibrated model is sub-market (#1).

## Proposal — fix options (ranked root-first; NO code here)
Discipline guardrails respected: no side disabled, no game halted, no feature removed. All sizing/threshold items are PROPOSALS with rollback.

### A. ROOT — make the bot defer to the market price (highest leverage)
The model loses to the price, so the bot should treat the price as a strong prior and only act on a *small residual*. The phi-prior blend already exists (`prob = w*market_price + (1−w)*prob`) but only weights toward market when phi is high. **Proposal:** raise the market-prior weight globally / make it the default anchor, so model−price edges shrink toward zero and the adverse-selection tail (M3 buckets ≥0.20) largely disappears. Expected impact: far fewer large-edge bets (the 7.9%/12.7% buckets), entry rate down, net bleed sharply reduced. This is the structural analogue of valorant's accidental success, applied on purpose. (Tier-3 code, EsportsBot-scoped.)

### B. ROOT — invert the edge→size/gate relationship
Today bigger edge → bigger Kelly bet; the data says bigger edge → more likely wrong (M3). **Proposal:** tighten `divergence_cap` hard, and stop sizing *up* on large edges — treat a large model−price gap as "model probably wrong," not "big opportunity." Pair with passing `calibration_quality` into the sizer so poor-calibration games shrink size (closes the Thread-D bypass). (Tier-3 EsportsBot-scoped + Tier-1 cap.)

### C. ROOT — earn the right to trade: gate on proven out-of-sample skill vs market
`edge_verification.py` already returns P(edge>0)=0.14. **Proposal:** require a model variant to beat the **market Brier** out-of-sample (rolling window) before it sizes real bets; until then, shadow-only. This prevents re-bleeding while a better model is built. (Process + a gate.)

### D. Model rebuild — make the model market-aware
The model ignores the single most informative feature: the price. **Proposal (larger effort):** add market_price as a feature / train the model to output a *deviation from the line* only when it has incremental info (e.g. live in-game state the market hasn't priced — LoL already has live gold/tower features; generic games don't). This is the only path to a model that legitimately beats the market.

### E. Interim damage-control (Tier-1, fast, partial — NOT a fix)
Raise `ESPORTS_MIN_CONFIDENCE` off 0.20; cut the expiry/phase confidence inflation so the gate/sizer see the real model prob; lower `ESPORTS_MAX_BET_USD` to shrink the tail. Each reduces bleed but leaves the model<market core intact. Rollbacks: `export KEY=old && sudo systemctl restart polymarket-esports`.

## Recommendation
**A + B together** are the root fix and are EsportsBot-scoped (splinter-safe under RULE FOUR): anchor to the market price and stop rewarding large divergences. **C** as the standing guard. **E** is acceptable as a same-day stopgap *only if* labeled interim. **D** is the real long-term answer but is a model-training project, not a surgical fix. Recommend NOT continuing to size real (paper) bets on the current edge rule — every large-edge entry is −EV by construction (M3).

## What I could NOT verify / caveats
- Magnitude attribution of the team↔token alignment gap (#5) vs pure model<market (#1) — both point to "defer to market," so it doesn't change the recommendation, but a clean fix to alignment is worth a separate look.
- valorant's +$1,690 is n=53 — partly the defer-to-market mechanism, partly variance; do not over-weight it.
- All figures: P&L from `edge_verification.py`/`bot_pnl.py` (canonical, reconciled); calibration from `esports_prediction_log` (n=603 resolved, 2026-03-07→06-16). No code changed; no live state touched.
