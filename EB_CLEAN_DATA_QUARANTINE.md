# EB Clean-Data Quarantine — verified landmines for any esports backtest

**Author:** EB session (esports), 2026-06-23
**Purpose:** Every future esports analysis/backtest MUST apply these filters. Each landmine below was measured live this session (psql, citations inline). Building on raw tables without these filters produces wrong answers — and this session caught itself doing exactly that twice.

---

## 🚩 LANDMINE 1 — `category='esports'` is ~60% POLLUTED. Never filter by it.

The `markets.category` tag is unreliable. Measured 2026-06-23:
- 17,421 markets tagged `category ILIKE '%esports%'`
- ≥3,056 of those are overtly **politics / other-sports** (`question ~* 'president|election|senate|congress|nominee|super bowl|nba|nfl'`)
- Only **6,998** are true esports content.

The highest-liquidity "esports" markets are all *"Will [Person] win the 2028 US Presidential election"* — mislabeled politics with $2M+ liquidity. Any universe count or capacity number computed via `category='esports'` (including the data-sweep's "17,359 esports markets" and an earlier "1,045 liquid markets") is **inflated by mislabeled markets and must be discarded.**

**CANONICAL esports-market filter (content, not category):**
```sql
WHERE question ~* '(counter-strike|cs2|csgo|league of legends|\bdota|valorant|\besports\b)'
  AND question !~* '(president|election|senate|congress|nominee)'
-- true esports universe = 6,998 markets (2,495 resolved) as of 2026-06-23
```

---

## 🚩 LANDMINE 2 — `markets.liquidity`/`volume` and `orderbook_snapshots` are UNRELIABLE for esports capacity. Do NOT conclude illiquidity from them.

**Correction (2026-06-23):** an earlier version of this doc claimed esports match markets are "zero-liquidity" based on `markets.liquidity=$0` / `volume=$0` and empty `orderbook_snapshots`. **That conclusion was WRONG** — operator has verified repeatedly that esports markets ARE liquid/tradeable. The columns lie:
- `markets.liquidity` / `markets.volume` are populated by *our* ingest (top-N priority); they read **$0 on liquid markets** that ingest didn't prioritize. They are NOT a read of true CLOB liquidity.
- `orderbook_snapshots` has zero esports-match rows because **the bot is HALTED and not subscribing to those tokens** — an artifact of bot state, NOT of empty books.

**Rule:** never judge esports market capacity from `markets.liquidity`, `markets.volume`, or `orderbook_snapshots` while the bot is halted. Real liquidity must be read from the live Polymarket CLOB (gamma / book endpoint) directly. Treat esports match markets as tradeable (operator-confirmed).

---

## 🚩 LANDMINE 3 — `esports_predictions` market-comparison is ORIENTATION-BROKEN.

`p_model` = P(team_a); `market_price` = YES-token price, but **YES may be team_a OR team_b** — there is no team→YES orientation guard ([bots/esports_bot_v2.py](bots/esports_bot_v2.py) `_find_polymarket_for_match`). Measured (clean v2-trinity, has market_price, n=148):
- `corr(p_model, market_price)` = **0.07** (≈ zero — the tell)
- market Brier 0.28 (worse than random 0.25) — physically implausible for a market price → axis is scrambled.

**Do NOT compute model-vs-market on `esports_predictions`.** For a clean model-vs-market read use `prediction_log` (esports_*), where `predicted_prob` and `was_correct` share the YES axis (corr 0.53, orientation valid) — but n is tiny (231 rows, 46 resolved → directional only).

`p_model`'s OWN Brier vs the team_a label (reconstructed from `correct`) IS valid: `y_a = correct if p_model>0.5 else NOT correct`.

---

## 🚩 LANDMINE 4 — `model_version` contamination in `esports_predictions`.

Exclude pre-OpenSkill-guard rows:
```sql
WHERE model_version = 'v2-trinity'        -- clean: 1,438 rows
-- excludes model_version='v2-trinity-contaminated' (35 rows) as of 2026-06-23
```

---

## 🚩 LANDMINE 5 — `shadow_fills` microstructure columns are UNUSABLE.

EsportsBot has 7,890 `shadow_fills` rows, but the order-book columns are synthetic/broken: median `spread` **0.86** (86¢ on a $1 contract), median `book_walk_slippage` **0.41**, avg `edge_at_vwap` **−0.85** (executing a +0.30 signal yields −0.85 edge — impossible), `shadow_pnl` **0% populated**, no depth/spread monotonicity. **Do not use `shadow_fills` for slippage, capacity, or execution-cost analysis.** Use `orderbook_snapshots` (real) instead — but note Landmine 2 (no esports-match coverage there either).

---

## ✅ Landmines that are CLEAN for esports (checked, no filter needed)

- **Temporal-ordering:** `prediction_log` esports = 231 rows, **0** with `resolved_at < prediction_time`. The "385 rows" landmine is fleet-wide (all bots), NOT esports. Esports prediction_log is temporally clean.
- **Schema collision:** migration 072 (`match_id TEXT`) is the LIVE `esports_matches` schema (verified `\d`). The `esports_v2/` code targets this shape → runs correctly. Migration 024 (BIGSERIAL) is dead.

---

## One-paste clean substrate (copy into any esports backtest)

```sql
-- TRUE esports markets:
--   question ~* '(counter-strike|cs2|csgo|league of legends|\bdota|valorant|\besports\b)'
--   AND question !~* '(president|election|senate|congress|nominee)'
-- Clean predictions:    esports_predictions WHERE model_version='v2-trinity'
-- Clean model-vs-market: prediction_log WHERE model_name ILIKE 'esports%' (orientation valid; tiny n)
-- NEVER use: category='esports' filter; shadow_fills microstructure cols; esports_predictions market_price comparison
```
