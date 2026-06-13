# MirrorBot Zero-Trade Filter Audit (S244, 2026-06-13)

**Purpose:** root-cause why MirrorBot placed **0 live trades in 24h+** despite a live whale feed, and document every filter that contributes, with code evidence and live-log evidence, for third-party review.
**Scope:** diagnostic only. **No code was changed for this audit.** (Two unrelated bugs *were* fixed earlier this session and are noted in §0.)
**Method:** code trace of `bots/mirror_bot.py` + `bots/elite_watchlist.py` + `config/settings.py`, cross-checked against 8.5h of live `journalctl` from release `20260613_083400` (window `2026-06-13 12:39:56`→`21:30` UTC). All counts below are `journalctl` grep tallies (infra-state, not trading P&L).

---

## §0 — What was already fixed this session (context, not part of the open findings)
- **Bug A — kill-switch crash loop** (`bfe4040`): pausing via the kill switch starved the watchdog heartbeat → 18h restart loop. Fixed: heartbeat during pause.
- **Bug B — paper history blocked live trades** (`c5818d0`): the opposing-side guard `_entered_market_sides` was restored from ALL trade_events ENTRY incl. paper (284/286 paper, 0/286 backing an open position) → 888 false `opposing_side_blocked_historical`/hr. Fixed: mode-filtered restore. **Verified post-fix: 357→2 entries, 888/hr→0.** This removed the single largest blocker but did NOT make the bot trade — the findings below are why.

---

## §1 — Executive summary

After Bug B, the bot still trades zero. The cause is **not one bug** — it is a stack of entry filters that **collectively reject ~100% of the live feed**, and at the center is a **mathematically mis-calibrated scoring gate** that rejects ~99% of signals *by construction*.

Live rejection funnel (8.5h, ranked — note a signal can hit several):

| Rank | Filter (log key) | Blocks | Severity |
|---|---|---|---|
| 1 | `mirror_whale_too_small` | 518 | structural (feed) |
| 2 | `mirror_gate_blocked` | 508 | **CRITICAL — mis-calibrated** |
| 3 | `mirror_market_maker_blocked` | 469 | **HIGH — false positives** |
| 4 | `mirror_no_edge_rejected` | 426 | HIGH (NO-side only) |
| 5 | `mirror_sell_balance_guard_reject` | 353 | (exits, not entries — ignore) |
| 6 | `mirror_no_dynamic_dampened` | 294 | LOW (size haircut, not a block) |
| 7 | `mirror_category_blocked` | 269 | MEDIUM-HIGH (259 = crypto) |
| 8 | `mirror_no_fav_hard_block` | 184 | MEDIUM (NO-side) |
| 9 | `mirror_trader_wr_hard_block` | 117 | MEDIUM |
| 10 | `mirror_no_dynamic_blocked` | 63 | LOW |

Whale-size distribution of the feed (8.5h): **<$5 = 517**, $5–25 = 236, $25–100 = 143, $100–1k = 108, >$1k = 27. So ~half the feed is sub-$5 (filtered at #1); the *other half clears the size gate and is then killed by #2–#9.*

**Highest `gate_score` reached in 8.5h = 0.761** (a few signals DO clear the gate), but those then die at market-maker / no-edge / category — hence zero orders.

---

## §2 — FINDING 1 (CRITICAL): the scoring gate is mis-calibrated and rejects ~99% by construction

**Where:** `mirror_bot.py` split-scoring block, gate check at [mirror_bot.py:3757](bots/mirror_bot.py:3757).
**Config (live = code defaults; none overridden in `/opt/pa2-shared/.env`):** `MIRROR_GATE_THRESHOLD=0.52` (YES), `MIRROR_GATE_THRESHOLD_NO=0.50`, `MIRROR_GATE_FACTOR_WEIGHT=0.30`. (Live logs show thresholds 0.52 ×234 and 0.48 ×274 — the 0.48 is the dynamically-adjusted NO/category variant.)

### The formula
```
gate_score = gate_base × (0.70 + 0.30 × geo_mean)          # factor_w = 0.30  (line 3584)
gate_base  = decay_w·eff_prior + (1−decay_w)·_base          # ~0.50–0.55 (line 3531)
gate_base ×= reliability_mult           (warm: eq_n≥5)      # THE KILLER   (line 3555)
gate_base  = clamp(gate_base, 0.20, 0.85)                   # floor 0.20   (line 3556)
reliability_mult = min(likelihood_ratio, 1.0) × min(1.0, eq_n/50)   # capped @1.0, sample-ramped
geo_mean   = geometric mean of the 9 wf_* factors that are < 0.99    # 0 if ANY factor = 0
```
`eq_n` = the tracked trader's count of *resolved* trades.

### Two independent failure modes, both proven in live logs

**Mode A — `gate_base` pinned to the 0.20 floor (172 of 514 scored signals = 33%).**
`reliability_mult` is hard-capped at 1.0 (never amplifies, S132) and multiplied by `sample_ramp = eq_n/50`. For any trader with **< 50 resolved trades** — the overwhelming majority of mirrored whales — `gate_base` is driven below 0.20 and clamped up to 0.20. There is **no floor for `5 ≤ eq_n < 50`** (the `MIRROR_COLD_START_SIZE_FLOOR=0.35` at line 3313-3315 applies only to *sizing* when `eq_n<5`). Live `gate_base` histogram: **0.20 = 191** (largest bucket), 0.25=11, 0.30=31, 0.35=25, 0.40=33, 0.45=51, 0.50=40, 0.55=77, …, 0.85=3.

**Mode B — `geo_mean` collapses to 0.0.** The geometric mean of the wf_* factors is **zero whenever any single factor is 0**. Live samples (different traders) repeatedly show `geo_mean=0.0 → gate_score=0.14`:
```
gate_base=0.2 gate_score=0.14 geo_mean=0.0 kelly_prob=0.06  trader=0x0346afAe
gate_base=0.2 gate_score=0.14 geo_mean=0.0 kelly_prob=0.074 trader=0xf49cE459
```
A *single* weak factor (e.g. `wf_volume=0.0` on a thin market, `wf_slippage=0` on a wide book) zeroes the whole geo-mean and pins `gate_score` to `gate_base × 0.70`.

**Combined effect:** a healthy signal with mostly-1.0 factors and geo_mean 0.561 still scored `gate_base=0.2 → gate_score=0.174` (live sample, trader 0xfE787d2D). Realistic `gate_score` range is **0.14–0.53**; the threshold **0.52** sits at the ~99th percentile.

### Root cause = scale mismatch + a sizing penalty leaking into the gate
- The threshold 0.52 is **inherited from an old `confidence` (probability) scale** — see the comment block at [mirror_bot.py:3745-3748](bots/mirror_bot.py:3745) ("50%+ = profitable", "<40% = 9% WR"). That comment describes a *probability* centered at 0.50. But `gate_score` is **not a probability** — it is a clamped product whose practical max is ~0.53. The two are not on the same scale.
- The dominant collapse (Mode A) is **`reliability_mult` — a sample-size/track-record penalty — being multiplied into the gate** (line 3555). Sample-size shrinkage is a legitimate *sizing* concern (bet less on unproven whales); applying it to the *yes/no gate* means an unproven-but-good whale can never clear the bar regardless of signal quality.

### Severity: CRITICAL. It is the central reason almost nothing passes, and it rejects good signals (geo_mean 0.561, factors ~1.0) on calibration grounds alone.

### Fix options (not implemented — for reviewer)
1. **Recalibrate the threshold to the gate_score scale** (e.g. set `MIRROR_GATE_THRESHOLD` to the empirical ~70-80th percentile of observed gate_scores, ≈0.30–0.35). Lowest-risk, fastest, reversible via env. Does not fix the underlying scale confusion but unblocks immediately.
2. **Remove `reliability_mult` from the gate** (line 3555) and keep it only in sizing. Bigger change; makes the gate reflect signal quality, with sample-size handled by bet size. Preferred structurally; needs care + backtest.
3. **Replace `geo_mean` (multiplicative, zero-collapsing) with a softer aggregator** (e.g. weighted arithmetic mean, or geo-mean with a per-factor floor) so one weak factor can't zero the score. Addresses Mode B.
> A reviewer should weigh 1 (tactical) vs 2+3 (structural). All three are config/logic changes to a live money path → backtest + staged.

---

## §3 — FINDING 2 (HIGH): `market_maker_blocked` false-positives on directional whales (469 blocks; 34 traders, 134 markets)

**Where:** runtime gate at [mirror_bot.py:2874-2904](bots/mirror_bot.py:2874); rejection at line 2882-2895.
**Logic:** on every BUY, build the *opposite-side* key `"{trader}:{market}:{opposite_side}"`; if that trader took the opposite side of the **same market within 24h** (hardcoded `_mm_window = 86400.0`, line 2876) → reject as market-maker. The current side is then recorded **unconditionally** (line 2898).
**No config knob** (window, repeat-count, size are all hardcoded). It is a **cruder duplicate** of the proper DB-level `users.is_likely_market_maker` flag (`elite_detector.py:135-156`, threshold `ELITE_MARKET_MAKER_BOTH_SIDES_RATIO=0.6` over the trader's *full history*), which already gates upstream SQL.

**Why it over-fires:**
- It cannot distinguish a market-maker from a **directional whale who changed his view** (bought YES, then bought NO 6h later on news). One opposite-side touch in 24h is enough — no size, simultaneity, or repeat-count test.
- **Neg-risk markets** (elections/tournaments) — explicitly in-scope per CLAUDE.md RULE TWO — naturally involve opposing YES/NO exposure and trip this by design.
- Unconditional self-recording (line 2898) widens the match surface every signal.

**Live evidence:** 469 blocks across **34 distinct traders / 134 distinct markets** — i.e. 34 tracked whales are being excluded wholesale. If most are directional (likely, since the DB MM flag already removes true MMs upstream), this is a large false-positive loss.

**Severity: HIGH.** 469/8.5h, and it removes whole traders from the eligible set.

**Fix options (not implemented):** require a *repeat* opposite-side pattern (e.g. ≥3 flips) and/or roughly-balanced sizing before flagging; shorten the 24h window toward true LP timescales; or rely solely on the DB `is_likely_market_maker` flag and retire the runtime heuristic. Must be reasoned jointly with the §2-Bug-B opposing-side guard so the two don't re-block what the other allows.

---

## §4 — FINDING 3 (HIGH): NO-side suppression stack starves NO bets

NO-side bets pass through a dedicated suppression block (master gate `MIRROR_NO_SIDE_DAMPENER=0.3`, <1.0 ⇒ active). It contains four mechanisms; **YES bets touch none of them.** Rationale was real (S146 note: NO = −$139K / 87% of historical losses) — the question is whether it is now **over-corrected**.

| Sub-filter | Live blocks | Where | Mechanism | Assessment |
|---|---|---|---|---|
| `mirror_no_edge_rejected` | 426 | [3922-3941](bots/mirror_bot.py:3922) | reject if `confidence − price < MIRROR_NO_MIN_EDGE(0.05)`. But NO `kelly_prob` is clamped to `[price+0.005, price+MIRROR_NO_MAX_KELLY_EDGE(0.10)]` (3599-3618) → achievable edge ∈ [0.005, 0.10]. **Demands 0.05 against a 0.10 ceiling = razor pass band.** | **Too strict for its own formula** |
| `mirror_no_fav_hard_block` | 184 | [3493](bots/mirror_bot.py:3493) | hard block if NO price ≥ 0.90 | defensible (favorite) |
| `mirror_no_dynamic_blocked` | 63 | [3906-3921](bots/mirror_bot.py:3906) | hard block if NO price < `MIRROR_NO_BLOCK_FLOOR(0.20)` | narrow, defensible |
| `mirror_no_dynamic_dampened` | 294 | [3942-3952](bots/mirror_bot.py:3942) | **NOT a block** — a size haircut (0.15×/0.30×/0.50×/0.75× by price tier) | mislabeled; but 0.15× can push `size×price` below the CLOB $1 min → silent downstream no-fill (check) |

**Severity: HIGH** for `no_edge` (426 real blocks). The dampener (294) is a size reduction, not a block — it conflates "dampened" with "blocked" in the funnel.

**Fix options (not implemented):** widen `MIRROR_NO_MIN_EDGE` vs `MIRROR_NO_MAX_KELLY_EDGE` so the pass band isn't ~half the ceiling; verify dampened-then-sub-$1 NO orders aren't silently dropped; re-test whether the historical NO-loss rationale still holds with current calibration. **Caution:** NO-side was a documented loss source — loosen with a backtest, not blind.

---

## §5 — FINDING 4 (MEDIUM-HIGH): category blocklist + hidden market-quality gate

**Category blocklist** ([mirror_bot.py:3001](bots/mirror_bot.py:3001)): `MIRROR_CATEGORY_BLOCKLIST=crypto,15-minute,speed,finance` (live). Substring match (`_bl in _cat_lower`), and on a hit it **caches the market into `_market_blocklist`** (self-amplifying — every future signal on that market dies faster at the Tier-0 blocklist).
- **Live evidence: 269 category blocks = 259 `crypto` + 10 `finance`.** The tracked whales trade heavily in crypto; the bot refuses all of it. This is a strategy choice, but at 259/8.5h it's a major aperture cut — and worth confirming it's intentional vs inherited.

**Hidden market-quality gate** ([mirror_bot.py:3255](bots/mirror_bot.py:3255), `mirror_market_quality_blocked`, threshold `MIRROR_MARKET_QUALITY_THRESHOLD=0.30`): blocks if the **geometric mean** of (volume, spread, time-to-resolution, category-information-edge) < 0.30. Geo-mean again ⇒ any weak input tanks it; an *unknown* category contributes only 0.50, low volume (<$5k→<0.5) drags hard. This silently kills the thin, fast markets whales actually trade and does not appear under the named counters.

**Severity: MEDIUM-HIGH.** Category alone is 259/8.5h; quality is a silent compounding gate.

**Fix options:** confirm the blocklist is intentional (esp. `crypto`); make category match exact not substring; reconsider the geo-mean quality aggregator (same zero-collapse issue as §2 Mode B); raise/lower the 0.30 threshold against the observed quality-score distribution.

---

## §6 — FINDING 5 (MEDIUM): the whale feed is mostly sub-$5; watchlist selection

`mirror_whale_too_small` (518) fires on `0 < size×price < MIRROR_MIN_WHALE_TRADE_USD($5 live)`. **517 of ~1031 signals are <$5** — half the feed. The min is already low ($5), so this is **feed composition**, not a tuning error: the watchlist is surfacing many tiny trades. The other half (≥$5, ~514 signals) is real and proceeds into the §2–§5 gauntlet.

**Severity: MEDIUM** (structural, not a bug). **Question for review:** is the elite-watchlist selecting the right traders if half their flow is sub-$5 and much of the rest is crypto (blocked)? This is a *signal-source* question, separate from the filter calibration.

---

## §7 — The meta-pattern

Every gate is individually defensible; **together they select against the exact shape of the live feed.** To place a trade, a signal must be: large-notional **and** non-crypto/finance **and** in a liquid long-dated market **and** from a 50+-resolved-trade whale **and** on a side not already held **and** clear a gate whose math tops out near the threshold. The live feed is small, fast, crypto-heavy, thin, from short-track-record whales. The intersection is ≈ empty. The **highest-leverage single fix is the gate calibration (§2)** — it alone rejects ~99% and does so on healthy signals.

---

## §8 — Appendix: full ordered entry pipeline (the gauntlet)

Handler `_execute_mirror_trade()` ([mirror_bot.py:2739](bots/mirror_bot.py:2739)); upstream feeder `EliteWatchlist.on_trade_event()` ([elite_watchlist.py:720](bots/elite_watchlist.py:720)). A signal must survive both. Order:

**Stage A — EliteWatchlist pre-filter (silent, no mirror_* log):** state-restored (740) · on-watchlist (753) · tx-dedup (761) · price 0.01–0.99 (780) · size>0 (782) · wash-trader ≥3 round-trips/1h→48h skip (831) · `_can_open_position` (844).
**Stage B — Tier 0 in-memory:** `bug12_mode_flip` (2760) · `mirror_price_floor_blocked` 0.03/0.97 (2778) · `mirror_trader_blacklisted` (2794) · **`mirror_whale_too_small`** `MIRROR_MIN_WHALE_TRADE_USD=$5` (2814) · `mirror_market_blocklist` (2839) · `mirror_market_cooldown` `MIRROR_MARKET_COOLDOWN_SECONDS=86400` (2857).
**Stage C — dedup/hedge:** **`mirror_market_maker_blocked`** 24h opposite-side (2881) · `mirror_opposing_side_blocked` open pos (2918) · `mirror_opposing_side_blocked_historical` `_entered_market_sides` (2934, **§0 Bug B path, now mode-filtered**) · `mirror_same_side_blocked` (2968).
**Stage D — category:** **`mirror_category_blocked`** `MIRROR_CATEGORY_BLOCKLIST` (3001) · `mirror_can_open_position_false` pos/exposure cap (3024).
**Stage E — market data/quality:** `mirror_market_data_retry_fail` (3201) · `mirror_market_inactive` (3211) · **`mirror_market_quality_blocked`** geo-mean<`MIRROR_MARKET_QUALITY_THRESHOLD=0.30` (3255).
**Stage F — scoring gate:** `mirror_trader_wr_hard_block` WR≤25%@≥20 resolved (3437) · `mirror_spread_hard_block` ≥0.25 (3471) · `mirror_no_fav_hard_block` NO≥0.90 (3493) · **`mirror_gate_blocked`** `gate_score<MIRROR_GATE_THRESHOLD(0.52/0.50)` split path (3757) [or `mirror_low_confidence` `<MIRROR_MIN_CONFIDENCE(0.55)` legacy path].
**Stage G — NO-side + sizing:** `mirror_no_dynamic_blocked` (3908) · `mirror_no_edge_rejected` (3925) · `mirror_no_dynamic_dampened` size haircut (3942) · `mirror_size_zero_after_limits` (4000) · dust-floor clamp-up (4045) · `place_order`.

**Live config snapshot (`/opt/pa2-shared/.env`, 2026-06-13):** `MIRROR_MIN_WHALE_TRADE_USD=5` · `MIRROR_CATEGORY_BLOCKLIST=crypto,15-minute,speed,finance` · `MIRROR_NO_SIDE_DAMPENER=0.3` · `MIRROR_MIN_CONFIDENCE=0.55` · `MIRROR_MAX_CONCURRENT_POSITIONS=1000` · split scoring **ON** (live logs `split_live=True`) · gate/no-edge/quality/min-resolved knobs **unset → code defaults** (`MIRROR_GATE_THRESHOLD=0.52`, `MIRROR_GATE_THRESHOLD_NO=0.50`, `MIRROR_NO_MIN_EDGE=0.05`, `MIRROR_NO_MAX_KELLY_EDGE=0.10`, `MIRROR_MARKET_QUALITY_THRESHOLD=0.30`, `MIRROR_TRADER_MIN_RESOLVED=20`).

---

## §9 — Prioritized recommendation (nothing implemented)

1. **Gate calibration (§2) — CRITICAL, do first.** Either recalibrate the threshold to the gate_score scale (tactical, env-only, reversible) or pull `reliability_mult` out of the gate + soften the geo-mean (structural). This single change unblocks ~99% of the current rejects.
2. **market_maker_blocked (§3) — HIGH.** Stop flagging directional flips / neg-risk; require a real repeated two-sided pattern, or defer to the DB MM flag.
3. **NO-side `no_edge` (§4) — HIGH but cautious.** Fix the min-edge-vs-ceiling band; verify no silent sub-$1 drops; re-validate the NO-loss rationale before loosening.
4. **Category/quality (§5) — MEDIUM-HIGH.** Confirm `crypto` block is intentional; reconsider substring match + the geo-mean quality gate.
5. **Whale/watchlist (§6) — MEDIUM.** Separate signal-source review: are we tracking the right whales?

Every item touches a live money path. Recommend: backtest each change, stage behind config where possible, and change **one lever at a time** with observation between, per the project's surgical-change protocol.

---

# ADDENDUM (v2) — Rejection-CORRECTNESS analysis (answers the reviewer's #1 gap)

A third-party review correctly flagged that §1–§9 above measured **how much each filter blocks, not whether what it blocks would have won.** That is the gap between "trade more" and "trade profitably." This addendum closes it using `mirror_rejected_signals` (which carries `side`, `price`, and a backfilled `resolution`/`resolved_at`): **11.88M rejected signals since 2026-04-22, 2.75M with a YES/NO resolution.**

**Metric:** counterfactual EV per $1 if each rejected signal had been entered at its **signal price** and held to resolution: `win → (1−price)`, `lose → (−price)`. *Caveats: directional + entry-price only; EXCLUDES the ~2% round-trip cost (`TAKER_FEE_BPS=150`+`FIXED_SLIPPAGE_BPS=50`), slippage beyond that, and the bot's early stop-loss exits (it does not hold to resolution). This is a research counterfactual, NOT realized P&L and NOT `bot_pnl.py`.* (An initial pass produced impossible EVs — averaging `−price` over *unresolved* rows — and was discarded per the project's "don't present impossible numbers" rule; the corrected query restricts EV to resolved rows and is internally consistent with the win%/price marginals.)

**Decision rule:** feed baseline EV = **+0.0086/$1** (resolved rejects). Round-trip cost ≈ **0.02/$1**. So a filter whose rejected signals average **EV < +0.02 is correctly rejecting after-fee losers**; only **EV > +0.02 on a large sample** is destroying real edge.

| rejection_reason | resolved | avg price | EV/$1 | dir. win% | verdict (after ~2% fees) |
|---|---|---|---|---|---|
| `mirror_trader_blacklisted` | 2,799 | 0.683 | **+0.305** | 98.8% | +EV **but suspicious** — favorites/near-certain; needs trader-level review |
| `mirror_no_dynamic_blocked` | 335 | 0.154 | +0.294 | 44.8% | +EV but tiny sample |
| **`mirror_opposing_side_blocked_historical`** | **50,509** | 0.606 | **+0.163** | 76.9% | **DESTROYS EDGE — large sample, clearly +EV. The strongest real finding.** |
| `mirror_trader_wr_hard_block` | 370 | 0.514 | +0.088 | 60.3% | +EV but small sample |
| **`mirror_opposing_side_blocked`** | **32,796** | 0.526 | **+0.044** | 56.9% | **modestly destroys edge — large sample, after-fee positive** |
| `mirror_buy_capital_guard_reject` | 4,010 | 0.576 | +0.020 | 59.6% | borderline; it's the "bot was broke" guard, not strategy |
| `mirror_whale_too_small` | 1,413,825 | 0.405 | +0.013 | 41.8% | **~neutral → -EV after fees. KEEP. (Reviewer's hypothesis confirmed.)** |
| `mirror_gate_blocked` | 149,456 | 0.511 | **+0.010** | 52.1% | **~neutral. The gate has NO edge-discriminating power — it rejects ~breakeven signals indiscriminately.** |
| `mirror_market_cooldown` | 31,635 | 0.527 | +0.005 | 53.2% | neutral → -EV after fees. Keep. |
| `mirror_market_maker_blocked` | 101,376 | 0.534 | +0.005 | 53.9% | **neutral → -EV after fees. KEEP — my §3 "loosen it" was WRONG.** |
| `mirror_category_blocked` | 12,183 | 0.542 | +0.001 | 54.3% | neutral → -EV after fees. Keep. |
| `mirror_market_blocklist` | 899,166 | 0.584 | −0.004 | 58.1% | -EV. Protects. Keep. |
| `mirror_same_side_blocked` | 35,209 | 0.529 | −0.042 | 48.7% | protects (re-piling is -EV). Keep. |
| `mirror_no_edge_rejected` | 8,131 | 0.747 | **−0.093** | 65.4% | **PROTECTS. My §4 "too strict" was WRONG — it correctly rejects -EV favorite bets.** |
| `mirror_no_fav_hard_block` | 2,378 | 0.939 | **−0.254** | 68.5% | **strongly PROTECTS (NO on 94¢ favorites). My flag was WRONG — keep it.** |
| `mirror_exposure_lock_reject` | 2,487 | 0.496 | −0.235 | 26.1% | protects. Keep. |

## What this reverses in §1–§9
- **The gate (§2) is NOT rejecting winners.** It rejects ~breakeven signals (EV +0.010, ≈ baseline) with **no discriminating power** — it's a high, indiscriminate wall, not an edge filter. **Lowering its threshold to "trade more" would let through ~breakeven signals that lose after fees.** The §2 calibration bugs (geo-mean zero-collapse, scale mismatch) are still real *correctness* bugs, but fixing them to increase trade volume would NOT add profit on the current feed.
- **`no_edge_rejected` (§4) and `no_fav_hard_block` are CORRECT** — they protect against -EV favorite bets (-0.093 and -0.254 EV). My recommendation to loosen them was exactly the trap the reviewer warned about: loosening a filter that correctly rejects -EV signals makes the bot trade garbage.
- **`market_maker_blocked` (§3) is ~neutral (-EV after fees)** — loosening it gains nothing. (Its directional-flip false-positive logic is still ugly, but it isn't costing money.)
- **`whale_too_small` (§6) is defensible** — ~neutral before fees, -EV after. Keep the size floor.

## What is actually true
1. **The only robust, large-sample edge being destroyed is the opposing-side / one-bet-per-market family** — `opposing_side_blocked_historical` (**+0.163/$1 on 50k**) and `opposing_side_blocked` (**+0.044 on 32k**). These reject the *opposite* side of markets the bot is/was in, and that opposite side is genuinely +EV. This is a real, money-losing constraint — but it is **RULE TWO ("one bet per market", marked sacred)**, and acting on it requires position-flip cost analysis + operator sign-off. *Caveat: the +EV assumes hold-to-resolution; the bot exits early, so realizable edge is lower.*
2. **`trader_blacklisted` (+0.305) needs a trader-level look** — likely favorites/near-certain markets (98.8% win @ 0.68 price), possibly the blacklist flagging sharp or wash traders; do not act before seeing who they are.
3. **The feed baseline is +0.0086/$1 before fees ≈ −0.01 after fees.** **The average mirror signal is unprofitable after costs.** This is the deepest finding: **the bot trading zero may be approximately correct given its current signal source has no edge after fees.** The fix is then **upstream (the watchlist / signal source — the reviewer's "strategy fork"), not downstream (loosening filters).**

## Revised recommendation (supersedes §9)
1. **Resolve the strategy fork FIRST (was deferred to §6).** The feed is ~-EV after fees → the highest-leverage question is the **watchlist**: are we tracking whales with demonstrable post-fee edge? If the feed has no edge, no filter change makes the bot profitable. (Investigation B.)
2. **Fix the §2 gate *correctness* bugs** (geo-mean missing-data-as-zero; scale mismatch) — but as **correctness**, not as a volume lever. They crush even good signals; fixing them is right, but expect ~0 profit impact on the current feed until the feed has edge.
3. **Opposing-side family** (`historical` +0.163, `open` +0.044) is the only filter family worth loosening on EV grounds — but it's RULE TWO; requires flip-cost analysis + sign-off, and the early-exit caveat caps the realizable gain.
4. **Do NOT loosen** `no_edge`, `no_fav`, `whale_too_small`, `market_maker`, `category`, `market_blocklist` — all reject -EV-after-fee signals. (Reverses §3/§4/§9.)

**Bottom line for the reviewer:** the original audit correctly found *where* signals die and proved the gate's calibration bugs, but its fix direction (loosen filters to trade) was largely backwards. The correctness analysis shows most filters are correctly rejecting a feed that is ~-EV after fees; the real levers are (a) the signal source/watchlist and (b) the opposing-side one-bet-per-market constraint — not the gate threshold or the NO-side filters.
