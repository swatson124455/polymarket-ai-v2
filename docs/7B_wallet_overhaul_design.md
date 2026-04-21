# Phase 7B — Wallet Selection Overhaul: Design Doc

**Bot**: MirrorBot
**Status**: DESIGN ONLY — no code, no deploys
**Author**: 2026-04-19
**Supersedes**: Nothing; elevation of existing `bots/elite_watchlist.py` S155 structure.
**Parent plan**: S172 Consolidated Plan (copy-trading elevation track)
**Scope boundary**: This doc is SCOPED-IN-HALF by a data-availability blocker; see §2.

---

## 1. Current State Summary

MirrorBot selects whale wallets to copy from Polymarket's per-category leaderboards via `bots/elite_watchlist.py` (1,045 lines). Pipeline has 4 stages plus a 5th post-copy scoring pass:

| Stage | File:Line | Action |
|-------|-----------|--------|
| 1. Fetch | `elite_watchlist.py:254-283` | Per-category×period leaderboard pulls (SPORTS/POLITICS/FINANCE/ECONOMICS/CULTURE/WEATHER/TECH × MONTH/ALL), filter by `WATCHLIST_MIN_ROI=0.03` (`:259`) and per-category min volume (`:45`, `_CATEGORY_MIN_VOLUME`). |
| 2. Dedup | `:285-311` | First-match-wins across categories in priority order: SPORTS > POLITICS > FINANCE_ECON > CULTURE > WEATHER > TECH. |
| 3. Enrich | `:313-367` | For top `quota × 1.5` per category, pull closed positions, compute `_trade_count` and `_profit_factor`. Filter by per-category `_CATEGORY_MIN_TRADES` (`:44`) and `WATCHLIST_MIN_PROFIT_FACTOR=1.2` (`:319`, ALL-period only). |
| 4. Rank & select | `:369-417` | Sort by ROI desc within each category, take top N per quota. Build `_watchlist_addresses` set for O(1) RTDS match. |
| 5. Copy-tier scoring | `:554-655` | After watchlist is set, query `trade_events` to compute realized copy-PnL per trader. Assign tier 1 (profit), 2 (thin <20 trades), 3 (loss). Used downstream in `mirror_bot.py` for size multiplier only — does NOT remove from watchlist. |

**Governing thresholds** (all in `elite_watchlist.py:38-45`):

```python
_DEFAULT_QUOTAS       = {"SPORTS": 40, "POLITICS": 22, "FINANCE_ECON": 15, "CULTURE": 10, "WEATHER": 8, "TECH": 5}
_CATEGORY_MIN_TRADES  = {"SPORTS": 30, "POLITICS": 20, "FINANCE_ECON": 15, "CULTURE": 15, "WEATHER": 10, "TECH": 10}
_CATEGORY_MIN_VOLUME  = {"SPORTS": 25_000, "POLITICS": 25_000, "FINANCE_ECON": 25_000, "CULTURE": 25_000, "WEATHER": 15_000, "TECH": 15_000}
```

Total watchlist size: sum of quotas = **100 traders**. `WATCHLIST_MIN_ROI=0.03`, `WATCHLIST_MIN_PROFIT_FACTOR=1.2`, `WATCHLIST_SUNSET_DAYS=7` (`:440`), refresh every 6 h (`:691`).

A downstream hard gate filters whale trades below `MIRROR_MIN_WHALE_TRADE_USD=100.0` (`bots/mirror_bot.py:2004-2010`) regardless of trader tier. This filter is applied AFTER the watchlist match but BEFORE the copy — so a qualified whale's small trades are rejected without producing a `paper_trades` row.

---

## 2. Data Feasibility Check — Counterfactual PnL

**Verdict: BLOCKED ON INSTRUMENTATION.**

**Evidence (verified on VPS 2026-04-19):**

1. `prediction_log` schema (migration `007_prediction_log.sql`, augmented through `048_trade_model_linkage.sql`) has NO `trader_address` / `proxyWallet` / `whale_address` column. Full column list confirmed against production schema: 26 columns, none identify the whale that triggered the signal.
2. `mirror_bot.py:2810` inserts prediction_log BEFORE the gate check (S177 fix), which means every signal that reaches the gate is recorded — but the `insert_prediction_log()` call passes no trader-identifying field:
   ```python
   await _db.insert_prediction_log(
       market_id=market_id, predicted_prob=kelly_prob, market_price=price,
       model_name=f"mirror_split_{source}", bot_name="MirrorBot",
       confidence=gate_score,
   )
   ```
   No `feature_snapshot={"trader": addr}`, no `correlation_id="...trader..."`.
3. VPS query on `prediction_log WHERE bot_name='MirrorBot'`: **138,543 rows total, 0 non-null `feature_snapshot`, 0 non-null `correlation_id`, 25 with resolution set.** Trader identity is unrecoverable for any of the 138,543 rows.
4. Rejections that happen BEFORE `insert_prediction_log` (notably `mirror_whale_too_small` at line 2006, and many other pre-prediction early-returns in `elite_watchlist.on_trade_event`) produce neither a `prediction_log` row nor a `paper_trades` row. They appear only in `logger.info(...)` output — stringified, not queryable by trader address.
5. `trade_events` DOES carry `event_data->>'trader'` for ENTRY events (`elite_watchlist.py:582, 589`, and mirror_bot.py:3051 stores the full address). But trade_events only exist for trades MB actually took → it's the copy-PnL path, not the counterfactual path.

**Net:** with current instrumentation, we can compute copy-PnL for wallets MB copied, but we cannot recover the rejected-wallet set or attribute rejected trades to a trader. Ranking only by copy-PnL would be circular — optimizing the subset MB already liked.

---

## 3. Proposed Measures

### Measure 1 — Copy-PnL (available today)

**Formula:** For each wallet `w` on the watchlist in the scoring window `[regime_start, NOW]`:

```
copy_pnl(w)       = SUM(te.realized_pnl) over trade_events te WHERE entry_trader = w AND event_type IN ('EXIT','RESOLUTION')
copy_trades(w)    = COUNT(DISTINCT market_id) with same filter
copy_wr(w)        = wins / copy_trades  (unreliable below n=20)
copy_edge(w)      = copy_pnl(w) / total_notional_copied(w)
```

**Required data:** `trade_events` (populated today), `paper_trades` (populated today). Already computed in `elite_watchlist.refresh_watchlist()` at lines 566-655.

**Use:** rank wallets whose signals MB has acted on at least 20 times. Tier 1 (profit) = full size, Tier 3 (loss) = probation 25% size, Tier 2 (thin) = 50% size. Current production already does this — the 7B change is to feed copy-PnL back into **watchlist inclusion**, not only sizing.

### Measure 2 — Counterfactual PnL (blocked on instrumentation)

**Formula:** For each signal `s = (trader, market_id, token_id, side, price, timestamp)` that arrived via RTDS but was rejected:

```
counterfactual_pnl(s) = notional(s) × (resolution_payout(market_id, token_id) - price) − fees
counterfactual_pnl(w) = SUM(counterfactual_pnl(s)) over rejected signals for wallet w
```

`resolution_payout` comes from `markets.resolution` once the market resolves ($1.00 for winning side, $0 for losing side; floor/ceiling for scalar markets).

**Required data (not yet present):** A per-signal log containing at minimum `(trader_address, market_id, token_id, side, price, size, timestamp, rejection_reason)`. Neither `prediction_log` nor `trade_events` captures this for rejected signals.

**Blocker:** design must ship instrumentation before Measure 2 is usable.

---

## 4. Proposed Retune Values

### 4.1 Category quotas (`_DEFAULT_QUOTAS`)

**Keep current for all six.** Changing the per-category structure itself is out of scope (v2 architecture). Re-weighting across categories requires comparing copy-PnL AND counterfactual-PnL by category; with only copy-PnL data available, the sample is censored by whatever filters rejected the rest. Recommend revisiting under Phase B (after instrumentation) when both measures are queryable.

### 4.2 Per-category `_CATEGORY_MIN_TRADES`

**Keep current.** `copy_pnl(w)` is already computed and used for sizing tiers, but the min-trades threshold governs watchlist INCLUSION (Stage 3 enrichment filter), and tightening it removes wallets that might still be viable in a non-copied market regime. No data-grounded case for change from the available 138,543 rows (none attributed to wallet), and copy-PnL alone can't tell us whether SPORTS=30 is correctly filtering — the rejected wallets leave no evidence trail. Requires Measure 2.

### 4.3 Per-category `_CATEGORY_MIN_VOLUME`

**Keep current.** Same blocker as 4.2: volume threshold governs stage-1 inclusion before any copy-PnL data would exist.

### 4.4 `MIRROR_MIN_WHALE_TRADE_USD` (adjacent to 7B)

Currently `100.0` (S173 Day 2, `mirror_bot.py:2004`). This is the hard gate that rejects whale trades under $100 regardless of wallet tier. It's the single highest-volume rejection path and the easiest instrumentation win: each rejection already logs `whale_usd`, `min_usd`, `market` — add `trader_address` (full, not `[:10]`) and switch `logger.info` to a structured `rejected_whale_trade` emitter with a DB backing table or a dedicated JSONB column in prediction_log. This populates Measure 2's input stream for the single most common rejection reason without touching the rest of the gate pipeline.

**Recommendation:** requires further data. Do not retune the dollar threshold until Measure 2 is queryable.

### 4.5 `WATCHLIST_MIN_ROI` / `WATCHLIST_MIN_PROFIT_FACTOR`

**Keep current** (`0.03` and `1.2`). These are Stage 1/3 filters — wallets that fail them never enter the watchlist and therefore never produce copy-PnL. Same blocker.

**Net 4.x:** no threshold changes proposed in this document, because the 136,895-row dataset does not contain the trader identifier needed to ground any per-wallet retune. 7B becomes a TWO-PHASE project (see §5).

---

## 5. Rollout Plan — Two-Phase

Per S172 shadow-first protocol.

### Phase A — Instrumentation (prereq, ~1 week)

Single commit, merged to master, deployed via normal release pipeline.

**A1.** Add a `mirror_rejected_signals` table (migration N+1):
```sql
CREATE TABLE mirror_rejected_signals (
  id BIGSERIAL PRIMARY KEY,
  event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  trader_address TEXT NOT NULL,
  market_id TEXT NOT NULL,
  token_id TEXT,
  side TEXT,
  price DOUBLE PRECISION,
  whale_trade_usd DOUBLE PRECISION,
  rejection_reason TEXT NOT NULL,  -- 'whale_too_small','gate_blocked','dust','no_edge', etc.
  rejection_stage TEXT NOT NULL,   -- 'pre_watchlist','watchlist','pre_gate','gate','post_gate'
  metadata JSONB
);
CREATE INDEX ON mirror_rejected_signals (trader_address, event_time DESC);
CREATE INDEX ON mirror_rejected_signals (market_id, event_time DESC);
```
A2. Wire a single `_log_rejection(trader, market_id, token_id, side, price, whale_usd, reason, stage)` helper in `mirror_bot.py`; call it at every rejection site.

**Phase A enumeration (2026-04-20, S185 verification — corrects prior 10-12 estimate):** 38 total early-exit points across the RTDS → execute pipeline. Per-handoff directive (don't trust the design doc's estimate), the exhaustive list below was produced by reading `elite_watchlist.on_trade_event` / `on_rtds_trade` and `mirror_bot._execute_mirror_trade` end-to-end. Line numbers as of master `d60ae17`.

*Legend: `log_status` = emits a logger call today (`L`) vs. silent return (`S`). `instrument` = whether this site warrants a row in `mirror_rejected_signals` (`Y`) or not (`N` — malformed events / infra guards have no analytical value).*

**Group 1 — `elite_watchlist.on_trade_event` (WS Market Channel handler)**

| # | File:Line | Emitter / condition | Stage | log_status | instrument |
|---|---|---|---|---|---|
| 1 | elite_watchlist.py:740 | `_state_restored` False | infra guard | S | N |
| 2 | elite_watchlist.py:745 | `user` not dict | malformed | S | N |
| 3 | elite_watchlist.py:748 | `addr` missing | malformed | S | N |
| 4 | elite_watchlist.py:753 | addr not on watchlist | pre_watchlist | S | N (highest volume, but no signal — fires on every non-whale trade) |
| 5 | elite_watchlist.py:761 | `tx_hash` in `_seen_tx` | dedup | S | N |
| 6 | elite_watchlist.py:771 | market_id / token_id missing | malformed | S | N |
| 7 | elite_watchlist.py:777 | price/size parse fail | malformed | S | N |
| 8 | elite_watchlist.py:780 | price ≤0.01 or ≥0.99 | watchlist | S | Y — signal-bearing whale trade with unplayable price |
| 9 | elite_watchlist.py:782 | size ≤0 | malformed | S | N |
| 10 | elite_watchlist.py:840 | wash_trader_flagged_skip (trader in `_wash_flagged`, <48h) | watchlist | S (log fired earlier at flag-time) | Y — trader-class reject, central to counterfactual analysis |
| 11 | elite_watchlist.py:844 | `_can_open_position(price)` False (position/daily cap) | watchlist | S | Y — capacity reject |

**Group 2 — `elite_watchlist.on_rtds_trade` (RTDS global handler)**

| # | File:Line | Emitter / condition | Stage | log_status | instrument |
|---|---|---|---|---|---|
| 12 | elite_watchlist.py:963 | `_state_restored` False | infra guard | S | N |
| 13 | elite_watchlist.py:978 | addr missing or not on watchlist | pre_watchlist | S | N (same as #4) |
| 14 | elite_watchlist.py:984 | `_dedup_key` in `_seen_tx` | dedup | S | N |

**Group 3 — `mirror_bot._execute_mirror_trade`, pre-gate tier (Tier 0+1+2)**

| # | File:Line | Emitter / condition | Stage | log_status | instrument |
|---|---|---|---|---|---|
| 15 | mirror_bot.py:1985 | `mirror_price_floor_blocked` (price <0.03 or >0.97) | pre_gate | L | Y |
| 16 | mirror_bot.py:1995 | `mirror_trader_blacklisted` | pre_gate | L | Y |
| 17 | mirror_bot.py:2005 | `mirror_whale_too_small` (<`MIRROR_MIN_WHALE_TRADE_USD`=$100) | pre_gate | L | Y — single highest-volume rejection (§4.4) |
| 18 | mirror_bot.py:2022 | `_market_blocklist` membership | pre_gate | S | Y |
| 19 | mirror_bot.py:2031 | `_market_cooldown` active (24h default per S172 D8) | pre_gate | S | Y |
| 20 | mirror_bot.py:2046 | `mirror_market_maker_blocked` (same trader both sides within 24h) | pre_gate | L | Y |
| 21 | mirror_bot.py:2071 | `mirror_opposing_side_blocked` (in-mem open opposite) | pre_gate | L | Y |
| 22 | mirror_bot.py:2078 | `mirror_opposing_side_blocked_historical` (`_entered_market_sides`) | pre_gate | L | Y |
| 23 | mirror_bot.py:2092 | `mirror_same_side_blocked` (dup signal same side) | pre_gate | L (debug) | Y — flag as de-dup, not true rejection |
| 24 | mirror_bot.py:2129 | `mirror_category_blocked` (`MIRROR_CATEGORY_BLOCKLIST` match) | pre_gate | L | Y |
| 25 | mirror_bot.py:2143 | `_can_open_position(price, category)` False | pre_gate | S | Y — capacity reject (duplicates #11 on pre-RTDS path) |
| 26 | mirror_bot.py:2155 | SELL path — no existing open position to close | pre_gate | L (debug) | N — SELL housekeeping, not a whale signal reject |
| 27 | mirror_bot.py:2164 | SELL path — existing position size=0 | pre_gate | L | N — same as #26 |

**Group 4 — `mirror_bot._execute_mirror_trade`, gate tier (split scoring)**

| # | File:Line | Emitter / condition | Stage | log_status | instrument |
|---|---|---|---|---|---|
| 28 | mirror_bot.py:2538 | `mirror_trader_wr_hard_block` (WR ≤25% with n≥20 resolved) | gate | L | Y |
| 29 | mirror_bot.py:2563 | `mirror_spread_hard_block` (spread ≥0.25) | gate | L | Y |
| 30 | mirror_bot.py:2577 | `mirror_no_fav_hard_block` (NO price ≥0.90) | gate | L | Y |
| 31 | mirror_bot.py:2833 | `mirror_gate_blocked` (gate_score < threshold, split scoring live) | gate | L | Y |
| 32 | mirror_bot.py:2842 | `mirror_low_confidence` / `mirror_shadow_conf_band` (legacy path, confidence < min) | gate | L | Y |

**Group 5 — `mirror_bot._execute_mirror_trade`, post-gate tier (sizing + exposure)**

| # | File:Line | Emitter / condition | Stage | log_status | instrument |
|---|---|---|---|---|---|
| 33 | mirror_bot.py:2947 | `mirror_no_dynamic_blocked` (NO price < `MIRROR_NO_BLOCK_FLOOR`=0.20) | post_gate | L | Y |
| 34 | mirror_bot.py:2955 | `mirror_no_edge_rejected` (NO edge <5%) | post_gate | L | Y |
| 35 | mirror_bot.py:3019 | "size zero after limits" (per-market + daily shares cap collapses size) | post_gate | L | Y |
| 36 | mirror_bot.py:3027 | `mirror_dust_skipped` (trade_usd < `MIRROR_MIN_TRADE_USD`=$25) | post_gate | L | Y |
| 37 | mirror_bot.py:3094 | `mirror_exposure_lock_reject` (daily cap exceeded under lock) | post_gate | L | Y |
| 38 | mirror_bot.py:3103 | `mirror_category_cap_reject` (per-category ≥$40K) | post_gate | L | Y |

**Counts:** 38 total early-exit sites; 25 true signal rejections worth instrumenting (instrument=Y). Remaining 13 are malformed events, infra guards, SELL housekeeping, or the always-false non-whale filter (#4/#13) whose volume would swamp the table. (Prior draft said 26/12 — off-by-one in summary paragraph, table rows are the authoritative count. Corrected S186 spot-check 2026-04-20.)

**Stage buckets (for the `rejection_stage` column):**
- `pre_watchlist`: #4, #13 (non-whale fast-reject — deliberately OUT of instrumentation)
- `watchlist`: #8, #10, #11 (whale trade present but rejected at watchlist layer)
- `pre_gate`: #15–#25 (11 sites — most in-memory hard blocks)
- `gate`: #28–#32 (5 sites — scoring path)
- `post_gate`: #33–#38 (6 sites — sizing / exposure)

**Consequence for §9.3 open question** (grouping stages): the 5-stage scheme holds; the 10-12 estimate in the prior draft was ~1/3 of the real count. `_log_rejection` helper signature unchanged; call-site count triples.

**Not in instrumentation (explicit):**
- Any malformed-event early return (user dict missing, price unparseable, size ≤0) — these are not signal rejections, they're data hygiene on the WS/RTDS feed.
- Watchlist fast-reject (#4/#13) — fires on ~99% of RTDS events and carries no trader signal.
- SELL housekeeping paths (#26/#27) — SELL paths close existing positions; "no position to close" is a no-op, not a rejected copy signal.

A3. Backfill resolution. Existing `backfill_prediction_log_resolution` (`database.py:3227`) pattern applies — add equivalent `backfill_mirror_rejected_signals_resolution` that joins to `markets` on `market_id` once resolved.

A4. Add 3-4 unit tests covering the helper with fake rejection at each stage.

**Rollout:** A is a pure additive instrumentation commit — zero behavior change, no gates, flags, or thresholds touched. Deploy via normal pipeline.

**Accumulation window:** wait **2 weeks minimum** for the table to fill. At MB's current RTDS throughput (~10K signals/day observed in watchlist fanout), 2 weeks yields ~140K rejected-signal rows — enough to rank the 100 watchlist wallets and bracket confidence intervals on `counterfactual_pnl(w)`.

### Phase B — Retune thresholds (1 week, after 2-week soak)

Runs under a shadow flag.

**B1.** Offline script `scripts/7b_wallet_retune.py` reads `mirror_rejected_signals` + `trade_events`, computes per-wallet `copy_pnl(w)` and `counterfactual_pnl(w)`, ranks all 100 watchlist + ~200 rejected-but-adjacent wallets, produces a proposed tier table.

**B2.** Add env flag `MIRROR_WALLET_RETUNE_SHADOW=true` (default). Under the flag:
   - Compute the 7B tier decision per trader at refresh time.
   - Log the delta vs current tier assignment as `mirror_wallet_retune_delta` (trader, old_tier, new_tier, delta_copy_pnl, delta_counter_pnl).
   - Do NOT change actual watchlist contents or sizing — shadow only.

**B3.** After 2 weeks of shadow data, review deltas. If deltas are directional (shadow ranks correlate with realized copy-PnL drift), flip default to `MIRROR_WALLET_RETUNE_SHADOW=false` (live).

**B4.** After 2 weeks stable under live (no drawdown spike, no trade count collapse, no category quota starvation), remove the flag entirely.

**Rollback:** `export MIRROR_WALLET_RETUNE_SHADOW=true && sudo systemctl restart polymarket-ai` reverts Phase B to shadow.

**Gate criteria (Phase B → live):**
- Shadow proposes tier changes for ≥15/100 wallets (non-trivial overhaul).
- Proposed changes move wallets TOWARD the profitable half of copy-PnL distribution AND away from the loss half of counterfactual-PnL.
- No single category loses >30% of its quota under the new tiers.
- Require `scripts/bot_pnl.py MirrorBot 336` (14 days) before AND after — result must not regress beyond noise.

---

## 6. Scope — Explicit IN / OUT

### IN-SCOPE for 7B

- Phase A instrumentation commit (rejected_signals table + logging helper).
- Phase B shadow mode + retune script.
- Threshold retune PROPOSAL (not applied) after Phase B soak, grounded in both measures.
- Wiring copy-PnL into watchlist INCLUSION (currently only affects sizing).

### OUT-OF-SCOPE for 7B

- WebSocket architecture (`RTDS` / Market channel connection lifecycle). That is **Phase 7A**.
- Changing the per-category structure (SPORTS/POLITICS/... set itself). v2 architecture question.
- Changing hard-coded category priority order in Stage 2 dedup. Structural.
- Non-watchlist-side filters (`MIRROR_NO_MIN_EDGE`, `MIRROR_GATE_THRESHOLD`, Kelly / BM / adaptive safety). These modify a trade after the wallet is already selected.
- Polymarket leaderboard API alternatives (switching from Data API to Goldsky subgraph, etc.). Data-source question.
- Wash detection tuning (`:831` threshold of 3 round-trips in 24h). Separate audit item.

---

## 7. Deliverable Checklist

- [x] §1 Current state summary with file:line citations.
- [x] §2 Feasibility check: counterfactual PnL is BLOCKED ON INSTRUMENTATION with VPS-verified evidence (138,543 rows, 0 non-null feature_snapshot).
- [x] §3 Two proposed measures with formulas and required data.
- [x] §4 Retune values — every threshold marked "keep current" because retune is blocked on Phase A. No invented numbers.
- [x] §5 Two-phase rollout: Phase A instrumentation → 2-week soak → Phase B shadow → 2-week shadow → live → 2-week stable → remove flag.
- [x] §6 Scope IN/OUT list.

## 8. Canonical P&L Source (Rule Zero)

Any P&L/win-rate/trade-count claim evaluating this design MUST be sourced from `scripts/bot_pnl.py`. On VPS:
```
PYTHONPATH=/opt/polymarket-ai-v2 python3 /opt/polymarket-ai-v2/scripts/bot_pnl.py MirrorBot 336
```
This doc deliberately does NOT embed copy-PnL or win-rate numbers by category, because:
- Ungrounded category-level copy-PnL would violate Rule Zero (numbers not from bot_pnl.py).
- Category-level splits from `bot_pnl.py` output are available to the human operator on demand.
- Any threshold change in §4 that depends on per-category performance must be re-derived from bot_pnl.py output at the time of proposed change — not snapshotted here.

## 9. Open Questions for Operator

1. Do we have the budget (disk / write throughput) for ~140K rows/2wk of rejected-signal logs? At ~300 bytes/row, 2.1 MB/day, 42 MB/month. Trivial.
2. Should Phase A's `mirror_rejected_signals.metadata` JSONB column carry the full `event_data` from RTDS, or only rejection-specific fields? Recommend rejection-specific only (trader, whale_usd, gate_score) to keep row size manageable and GIN indexing cheap.
3. Which of the 10-12 rejection sites warrants a separate `rejection_stage`? Recommend grouping into the 5 listed in A1 — fewer stages = simpler retune script.
