# MirrorBot Session 106 — Scope-Locked Task Prompt
# Copy-paste this into a fresh session. DO NOT bleed into WeatherBot or EsportsBot.

---

## SCOPE LOCK
You are working on **MirrorBot ONLY**. Do not touch WeatherBot, EsportsBot, or any other bot's files. If a shared module needs changes, justify it explicitly and verify all 14 bots.

---

## READ FIRST (in this order)
1. `CLAUDE.md` — development rules (surgical fixes, zero collateral damage)
2. `memory/AGENT_HANDOFF_MIRRORBOT_SESSION102_2026_03_18.md` — latest handoff
3. `AGENT_HANDOFF_MIRRORBOT_SESSION102_2026_03_18.md` — full carbon copy (root dir)
4. `bots/mirror_bot.py` — the bot (~1,400 lines)
5. `bots/elite_watchlist.py` — RTDS dispatch + whale logging (~604 lines)
6. `config/settings.py` — all config
7. `tests/unit/test_mirror_bot_logic.py` — unit tests
8. `tests/unit/test_book_walk.py` — book walk tests

---

## CURRENT STATE (as of S102 deploy 2026-03-17)
- **P&L**: +$20,312 realized (3323 entries, 683 exits, 889 resolutions). Paper, realistic fill model.
- **Open positions**: 79, unrealized +$134, exposure $4,945
- **Key config**: kelly=0.25, capital=$20K, max_bet=$300, max_daily=$10K, HARD_MIN_PRICE=0.10, MIN_TRADE_USD=$50
- **S102 changes**: Hard floor 5c→10c, 3 stacking dampeners→1, min trade $10→$50, 536 lines dead code deleted

---

## TASK LIST — IN PRIORITY ORDER

### P1: Config Contradiction Audit + Fix
MirrorBot has the most config contradictions of any bot. Audit and align these:

**Known contradictions:**
- `capital` in BotBankrollManager code default was $3,000 but CLAUDE.md says $20,000. S105 aligned code defaults to $20K — **verify the VPS .env is also aligned** and that MirrorBot actually sizes off $20K.
- `max_daily` in BotBankrollManager code = $10,000, but `MIRROR_MAX_DAILY_EXPOSURE_PCT=0.15` → 15% of $20K = $3,000 (daily exposure cap in mirror_bot.py). These are different mechanisms but both gate daily volume. Document which one actually fires first.
- `max_bet` code default $300 vs what's on VPS .env. Verify.
- `MIRROR_MAX_PER_MARKET=400` (USD per market) vs `MIRROR_MAX_ENTRIES_PER_MARKET=2` (count cap). Both active, both enforced in different places. Document their interaction clearly.
- `MIRROR_MAX_CONCURRENT_POSITIONS=500` vs `MIRROR_MAX_POSITIONS` (if it exists separately). Verify single source of truth.

**Deliverable**: A clear table of every MirrorBot cap/limit, where it's enforced (file:line), what value is in code default vs .env, and which one wins. Then fix any contradictions by aligning to the CLAUDE.md Key Config values ($20K capital, $300 max_bet, $10K max_daily). ONE commit.

### P2: Monitor S102 Impact (48h check)
S102 deployed 2026-03-17 evening. It's now 2026-03-18+. Run these diagnostic queries on VPS and report:

1. Did "size zero after limits" log spam decrease? (`grep "size_zero" | wc -l` in last 24h vs prior 24h)
2. Are sweet-spot entries (10-30c) increasing as a percentage of total entries?
3. Are all entries ≥$50 (min trade USD)?
4. Price bucket distribution of entries post-S102 vs pre-S102
5. Any new rejection reasons appearing in waterfall?

```sql
-- Post-S102 entry price distribution
SELECT CASE
  WHEN price < 0.10 THEN '<10c'
  WHEN price < 0.30 THEN '10-30c'
  WHEN price < 0.50 THEN '30-50c'
  WHEN price < 0.70 THEN '50-70c'
  ELSE '>70c'
END as bucket, COUNT(*) as entries
FROM trade_events WHERE bot_name='MirrorBot' AND event_type='ENTRY'
  AND event_time > '2026-03-17 22:00:00'
GROUP BY bucket ORDER BY bucket;
```

**Deliverable**: Data summary. No code changes unless data reveals a bug.

### P3: Clean mirror_calibration.py — Strip Dead Conformal Code
`bots/mirror_calibration.py` (~195 lines) contains:
- **FTS calibrator**: ACTIVE and useful. Logs `mirror_calibrated`. KEEP.
- **Conformal prediction methods**: DEAD since S93 (disabled, never re-enabled, S102 stripped all callers). DELETE.

**Rules**:
- Read the entire file first
- Grep for every import of this module across the codebase
- Keep FTS calibration intact — it's wired into the validation pipeline
- Delete only conformal-related methods/classes
- ONE commit

### P4: Hold-Time Analysis Deep Dive
S102 data shows <24h positions are net -$1,370 (100 trades). Investigate:

1. Query hold-time P&L by entry price bucket (are <24h losses concentrated in specific price ranges?)
2. Query hold-time by trader tier (are specific whales producing the short-hold losers?)
3. Assess whether a minimum hold time (e.g., 6h) would improve P&L or just delay losses
4. Assess whether entry-time-of-day filtering (e.g., no entries after 8pm UTC) would help

**Deliverable**: Data analysis + recommendation. If a minimum hold time is warranted, propose the implementation (which file, which check, where in the validation pipeline). Do NOT implement without explicit approval.

### P5: whale_trades Table Retention
`whale_trades` grows ~270K rows/day (33,648+ already). At this rate:
- 1 week = ~1.9M rows
- 1 month = ~8.1M rows

**Task**: Check current table size on VPS. If >1M rows, propose a retention policy:
- Option A: 30-day rolling purge via cron
- Option B: Partition by week with auto-drop
- Option C: Archive to CSV + truncate

**Deliverable**: Recommendation with SQL. Do NOT execute without approval.

### P6: NO vs YES Side Asymmetry
All-time data shows NO side at 72% WR vs YES at 39% WR. This is a significant signal.

**Task**: Investigate whether:
1. Whales trade NO more profitably than YES (or is it market structure?)
2. Should MirrorBot apply a YES-side dampener (e.g., 0.7x Kelly on YES entries)?
3. What's the YES-side P&L by price bucket? Is it uniformly bad or concentrated in specific ranges?

**Deliverable**: Data analysis. Propose config change if warranted. Do NOT implement without approval.

---

## CAP/LIMIT SIMPLIFICATION PROPOSAL

MirrorBot currently has **15+ independent caps/limits** that interact in non-obvious ways. Here is the full list — the goal is to simplify without losing protection:

| # | Cap/Limit | Current Value | Enforced In | Keep/Simplify |
|---|-----------|--------------|-------------|---------------|
| 1 | Hard price floor | 0.10 | mirror_bot.py | KEEP — data-driven (S102) |
| 2 | Hard price ceiling | 0.95 | mirror_bot.py | KEEP — obvious |
| 3 | Position cap | 500 | mirror_bot.py | KEEP but consider lowering to 200 |
| 4 | Per-market entry cap | 2 | mirror_bot.py | KEEP — prevents stacking |
| 5 | Per-market USD cap | $400 | mirror_bot.py | KEEP |
| 6 | Category cap | $40,000 | mirror_bot.py | QUESTIONABLE — never hit with 79 positions at $4.9K exposure |
| 7 | Daily exposure cap | $20,000 | mirror_bot.py | KEEP |
| 8 | Daily exposure % | 15% of capital | mirror_bot.py | REDUNDANT with #7 — pick one |
| 9 | Min trade USD | $50 | mirror_bot.py + settings.py | KEEP — S102 |
| 10 | Min confidence | 0.55 | mirror_bot.py | KEEP |
| 11 | Min reliability | 0.52 | mirror_bot.py | KEEP |
| 12 | Dead zone dampener | 0.50 (30-50c) | mirror_bot.py | KEEP — S102 |
| 13 | Favorites dampener | 0.40 (≥70c) | mirror_bot.py | KEEP — S102 |
| 14 | BotBankrollManager max_bet | $300 | bankroll_manager.py | KEEP |
| 15 | BotBankrollManager max_daily | $10,000 | bankroll_manager.py | REDUNDANT with #7 |
| 16 | BotBankrollManager capital | $20,000 | bankroll_manager.py | KEEP — Kelly base |
| 17 | Market cooldown | 1800s | mirror_bot.py | KEEP |
| 18 | Hot trade max seconds | 900 | mirror_bot.py | KEEP |
| 19 | Slippage check | 8% | mirror_bot.py | KEEP |
| 20 | Near-resolution filter | 4h | mirror_bot.py | KEEP |
| 21 | Circuit breaker | drawdown-based | mirror_bot.py | KEEP |

**Candidates for removal/merge:**
- #6 (category cap $40K) — never triggers at current exposure levels
- #8 (daily exposure %) — redundant with #7 (daily exposure USD)
- #15 (BotBankrollManager max_daily) — redundant with #7 (daily exposure cap in bot)

**Proposal**: Remove #8 (daily exposure %) since #7 (fixed USD) is clearer. Keep #6 as a safety net but raise awareness it's never triggered. Verify #15 vs #7 — if both enforce $10K/$20K, pick the one in the bot and remove the BotBankrollManager one (or vice versa).

**Do NOT implement any of this without explicit approval.** Just audit, document, and propose.

---

## CROSS-BOT FEATURES TO CONSIDER (from other bots)

These exist in WeatherBot or EsportsBot but not MirrorBot. Evaluate whether they'd help:

1. **Alpha decay** (WeatherBot) — Signal staleness penalty based on latency. MirrorBot copies in real-time via RTDS, so latency is <100ms. Likely not useful — but verify.
2. **Daily counter write-through** (EsportsBot/WeatherBot) — MirrorBot uses `paper_trades SUM` for `_daily_exposure` on restart. Other bots use `daily_counters` table (faster, <10ms vs ~200ms). Consider migrating.
3. **Exposure decrement on exit** (WeatherBot S104) — WeatherBot had a bug where exposure only went UP. Does MirrorBot properly decrement daily exposure on exits? Verify.
4. **Fill quality logging** (WeatherBot S104) — WeatherBot now logs slippage_bps, fill_prob, fill_frac in event_data. MirrorBot does not. Consider adding for fill model calibration.

**Deliverable**: Assessment of each. Propose implementation only if data supports it. Do NOT implement without approval.

---

## VERIFICATION AFTER ANY CHANGES
1. `pytest tests/unit/test_mirror_bot_logic.py tests/unit/test_book_walk.py` — all 81 tests pass
2. `pytest` — full suite, all 1623+ pass
3. List every file modified
4. One fix per commit
5. Write change log per CLAUDE.md format

---

## CRITICAL TRAPS (DO NOT BREAK)
- `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
- `_market_meta_cache` is 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- `trade_events` is P&L authority, NOT `paper_trades`.
- RTDS envelope: unwrap `data.get("payload", data)`.
- RTDS dedup: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
- `whale_trades` requires explicit `await session.commit()` (S101 fix).
- `asyncpg JSONB`: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
- Positions table: NO `closed_at`, NO `updated_at`, NO `bot_name` — use `source_bot`.
- MirrorBot entry price: Uses CURRENT market price, NOT trader's fill price.
- Paper engine positions key: `(bot_name, market_id)` tuple (S105 fix).
- `realized_pnl_today` is now `Dict[str, float]` not `float` (S105 fix).
- Python 3.13: `from X import Y` inside function → local for ENTIRE function.
