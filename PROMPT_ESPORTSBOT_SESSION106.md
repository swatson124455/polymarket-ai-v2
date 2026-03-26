# EsportsBot Session 106 — Scope-Locked Task Prompt
# Copy-paste this into a fresh session. DO NOT bleed into MirrorBot or WeatherBot.

---

## SCOPE LOCK
You are working on **EsportsBot ONLY** (includes EsportsLiveBot and EsportsSeriesBot which share code). Do not touch MirrorBot, WeatherBot, or any other bot's files. If a shared module needs changes, justify it explicitly and verify all 14 bots.

---

## READ FIRST (in this order)
1. `CLAUDE.md` — development rules (surgical fixes, zero collateral damage)
2. `AGENT_HANDOFF_ESPORTS_SESSION105_2026_03_18.md` — latest handoff (cross-bot position fix, bankroll alignment)
3. `AGENT_HANDOFF_ESPORTS_SESSION103_2026_03_18.md` — prior session (P4-P7 fixes, daily_counter commit)
4. `bots/esports_bot.py` — the bot (~5,400 lines)
5. `esports/models/glicko2.py` — Glicko-2 rating system
6. `esports/models/conformal_wrapper.py` — conformal predictor
7. `base_engine/data/daily_counter.py` — exposure persistence
8. `config/settings.py` — all config
9. `tests/unit/test_esports_bot.py` — unit tests

---

## CURRENT STATE (as of S105 deploy 2026-03-18)
- **P&L**: -$189.29 realized (74 trades, 48.6% WR). CS2 dominant (48 trades), Dota2 best (+$200, 75% WR), Valorant worst (-$209, 20% WR)
- **Open positions**: ~7
- **BetaCalibrator**: UNFITTED for all 8 games (need 30+ resolved predictions per game post-2026-03-16)
- **Learning phase**: All 6 suspensions active (monitoring halt, tournament phase, edge cap, kelly degradation, game kelly mult, phi sizing floor)
- **Key config (post-S105)**: kelly=0.25, capital=$20K, max_bet=$300, max_daily=$10K, MAX_GAME_EXPOSURE=$600
- **S105 changes**: Cross-bot position isolation (paper engine key now `(bot_name, market_id)`), bankroll aligned to $20K/$300/$10K, partial exit fee proration, per-bot realized_pnl_today
- **S103 changes**: P4 event_data populated, P6 max_bet cap enforced, P7 exposure units shares→USD, daily_counter commit fix

---

## TASK LIST — IN PRIORITY ORDER

### P1: Config Contradiction Audit + Fix
EsportsBot has the MOST config contradictions of all 3 bots. S105 aligned BotBankrollManager code defaults but the VPS .env is likely stale.

**Known contradictions (MUST verify on VPS):**
- `.env ESPORTS_TOTAL_CAPITAL=5000` vs code default now $20,000 (S105). **Which wins?** BotBankrollManager uses `BOT_BANKROLL_CONFIG` JSON env var, NOT `ESPORTS_TOTAL_CAPITAL`.
- `.env ESPORTS_MAX_BET_USD=100` vs BotBankrollManager code default now $300 (S105). The P6 cap in `_execute_esports_trade()` reads `getattr(settings, "ESPORTS_MAX_BET_USD", 300.0)`. If .env says 100, that's what fires. **Is $100 the intended cap?**
- `.env ESPORTS_MAX_DAILY_USD=500` vs BotBankrollManager code default now $10,000 (S105). `.env` wins if `ESPORTS_MAX_DAILY_USD` is read by the bot. **Verify which one the bot actually reads.**
- `.env ESPORTS_DAILY_LOSS_LIMIT` was $500 (S105 changed settings.py default to $10K). **Does .env override?**
- `.env ESPORTS_MAX_EDGE=0.35` but code raises to 0.45 while BetaCalibrator unfitted. This is intentional — document it.
- `ESPORTS_MAX_GAME_EXPOSURE=600` — is this the right value now that units are USD (S103 fix)? $600 per game means max 1-6 positions per game depending on price. Seems low.

**Deliverable**: Complete table of every EsportsBot cap/limit, where enforced, .env value vs code default vs CLAUDE.md target. Then propose ONE .env update to align everything. Do NOT change .env without explicit approval — just propose the changes.

### P2: BetaCalibrator Progress Check
BetaCalibrator needs 30+ resolved predictions per game (post-2026-03-16) to fit. Check progress:

```sql
SELECT game, COUNT(*) as total, COUNT(actual_outcome) as resolved
FROM esports_prediction_log WHERE created_at > '2026-03-16' GROUP BY game;
```

**If any game has 30+ resolved**: BetaCalibrator should have auto-fitted. Check logs for `esportsbot_beta_cal`. If fitted, verify the learning-phase suspensions are auto-deactivating.

**If all games < 30**: Report progress and estimate when fitting will occur based on resolution rate.

**Deliverable**: Data report. No code changes.

### P3: low_confidence Threshold Tuning
10/28 markets blocked by `low_confidence` every scan. Root cause: LoL closely-rated teams produce model_prob ≈ 0.5076 → confidence 0.4924 < 0.50 threshold.

**Options:**
- A) Lower `ESPORTS_MIN_CONFIDENCE` from 0.50 to 0.48 — trades more markets, accepts less certain predictions
- B) Wait for BetaCalibrator to fit (will shift probabilities)
- C) Accept as working-as-designed

**Task**: Query the prediction log for markets blocked by low_confidence. What's their resolution outcome? If they would have been profitable trades, option A is warranted.

```sql
SELECT game, COUNT(*) as total,
  COUNT(*) FILTER (WHERE actual_outcome IS NOT NULL) as resolved,
  AVG(predicted_prob) as avg_prob,
  AVG(CASE WHEN actual_outcome=1 THEN 1.0 ELSE 0.0 END) as avg_outcome
FROM esports_prediction_log
WHERE created_at > '2026-03-16'
  AND predicted_prob BETWEEN 0.48 AND 0.52
GROUP BY game;
```

**Deliverable**: Data analysis + recommendation. Do NOT change threshold without approval.

### P4: CS2 Brier Score Investigation
CS2 Brier = 0.2895 (warning threshold 0.30). Valorant Brier = 0.4727 (very poor, close to random).

**Task**:
1. Query CS2 prediction accuracy by team tier (well-known teams vs minor teams)
2. Query Valorant — why is it so bad? Is it team name matching failures or genuine model weakness?
3. Check if `no_prediction=3` are Valorant teams

**Deliverable**: Root cause analysis. If Valorant is genuinely unpredictable, consider raising min_edge for Valorant specifically.

### P5: EsportsSeriesBot Status Check
S103 handoff notes EsportsSeriesBot as "stale (72h+)". Check:
1. Is it scanning?
2. Is it finding opportunities?
3. Is the series path (`_execute_series_trade()`) executing?

```bash
journalctl -u polymarket-ai --since "1h ago" | grep -i "esportsseriesbot"
```

**Deliverable**: Status report. If dead, investigate why and propose fix.

### P6: Contaminated EXIT P&L Corrections
S105 identified 7 contaminated EXIT events from the cross-bot position key bug. These have incorrect realized_pnl because they used another bot's entry price.

**Task**: Execute the P&L correction SQL from the S105 plan file. Must:
1. Disable immutability trigger on trade_events
2. Update the 7 specific EXIT events with corrected realized_pnl
3. Re-enable immutability trigger
4. Verify totals match

**Deliverable**: SQL execution + verification. Get explicit approval before running UPDATE on trade_events.

### P7: Wire taker_side for Paper Fill Filter
S105 added `PAPER_TAKER_SIDE_FILTER` setting (disabled) and a code stub in paper_trading.py lines 708-718. But no bot currently populates `event_data["taker_side"]`.

**Task**: Evaluate what it would take to populate taker_side:
1. For EsportsBot — is taker_side available from any data source (PandaScore, CLOB)?
2. For WeatherBot — is it available from order book data?
3. If neither can populate it, this feature is dead code. Document and defer.

**Deliverable**: Assessment. No code changes unless trivially simple.

---

## CAP/LIMIT SIMPLIFICATION PROPOSAL

EsportsBot has the most complex cap stack because of the learning-phase suspensions:

| # | Cap/Limit | Current Value | Enforced In | Keep/Simplify |
|---|-----------|--------------|-------------|---------------|
| 1 | MIN_CONFIDENCE | 0.50 | esports_bot.py | KEEP |
| 2 | MIN_EDGE | 0.05 (.env) | esports_bot.py | KEEP |
| 3 | MAX_EDGE | 0.45 (learning) / 0.35 (normal) | esports_bot.py | KEEP — auto-resolves |
| 4 | CONFLUENCE_MIN | 0.60 | esports_bot.py | KEEP |
| 5 | MAX_GAME_EXPOSURE | $600 | esports_bot.py | REVIEW — may be too low |
| 6 | MAX_BET_USD | $100 (.env) / $300 (code) | esports_bot.py (P6 cap) | ALIGN — pick one |
| 7 | MAX_DAILY_USD | $500 (.env) / $10K (code) | settings.py / bankroll | ALIGN — 20x gap |
| 8 | TOTAL_CAPITAL | $5K (.env) / $20K (code) | settings.py / bankroll | ALIGN — 4x gap |
| 9 | BotBankrollManager max_bet | $300 | bankroll_manager.py | Overridden by #6 |
| 10 | BotBankrollManager max_daily | $10,000 | bankroll_manager.py | Conflicts with #7 |
| 11 | Phi sizing factor | 0.5-1.0 (learning: 0.8 floor) | esports_bot.py | KEEP — auto-resolves |
| 12 | Drawdown Kelly factor | varies | esports_bot.py | KEEP |
| 13 | Game kelly mult | 1.0 (suspended) | esports_bot.py | KEEP — auto-resolves |
| 14 | Edge decay mult | varies | esports_bot.py | KEEP |
| 15 | Upset risk scaling | varies | esports_bot.py | KEEP |
| 16 | PatchDriftDetector | 48h observation | esports_bot.py | KEEP |
| 17 | Monitoring halt | Brier > 0.30 (suspended) | esports_bot.py | KEEP — auto-resolves |

**The big problem**: Items #5-10 are a mess. The .env has conservative paper-trading values ($5K capital, $100 max bet, $500 daily) while S105 aligned code defaults to the CLAUDE.md standard ($20K/$300/$10K). Which should win?

**Proposal**: Update VPS .env to match CLAUDE.md:
```
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MAX_DAILY_USD=10000
ESPORTS_DAILY_LOSS_LIMIT=10000
```

This removes the contradiction. The P6 cap in `_execute_esports_trade()` still enforces whatever `ESPORTS_MAX_BET_USD` is set to. The $600 game exposure cap remains as the per-game limit.

**Do NOT update .env without explicit approval.**

---

## CROSS-BOT FEATURES TO CONSIDER (from other bots)

1. **Price bucket dampeners** (MirrorBot S102) — MirrorBot dampens 30-50c and ≥70c based on all-time P&L data. EsportsBot has no price-based dampeners. With only 74 trades, insufficient data to justify — but monitor.
2. **Per-market entry cap** (MirrorBot S101) — MirrorBot caps at 2 entries per market. EsportsBot has no per-market cap. Could stack entries. Evaluate from trade data.
3. **Fill quality logging** (WeatherBot S104) — WeatherBot logs slippage_bps, fill_prob, fill_frac in event_data. EsportsBot does not. Consider adding for fill model calibration.
4. **Alpha decay** (WeatherBot) — Signal decay based on latency. EsportsBot scans every 10s with live match data. Alpha decay may help penalize stale pre-match predictions but not live ones. Evaluate.
5. **Exposure decrement on exit** (WeatherBot S104) — WeatherBot had a bug where exposure only went UP. EsportsBot S103 fixed this (exit decrement in USD). Verify it's working.

**Deliverable**: Assessment only. No code changes.

---

## SELF-HEALING SYSTEM STATUS

The learning-phase system is designed to self-heal. Track this status:

```
BetaCalibrator (30 samples/game to fit):
  - LoL:      ?/30 resolved
  - CS2:      ?/30 resolved
  - Dota2:    ?/30 resolved
  - Valorant: ?/30 resolved
  - CoD:      ?/30 resolved
  - R6:       ?/30 resolved
  - SC2:      ?/30 resolved
  - RL:       ?/30 resolved

When fitted → these auto-deactivate:
  [x] Monitoring halt suspension
  [x] Tournament phase suspension
  [x] Edge cap 0.45 → 0.35
  [x] Kelly degradation suspension
  [x] Game kelly mult suspension
  [x] Phi sizing floor 0.8 → 0.5
```

Fill in the resolved counts from the query in P2.

---

## VERIFICATION AFTER ANY CHANGES
1. `pytest tests/unit/test_esports_bot.py` — all 93 tests pass
2. `pytest` — full suite, all 1623+ pass
3. List every file modified
4. One fix per commit
5. Write change log per CLAUDE.md format
6. Verify on VPS after deploy:
   - `journalctl -u polymarket-ai --since "5 min ago" | grep esportsbot_scan_summary | tail -3`
   - `journalctl -u polymarket-ai --since "10 min ago" | grep -iE "(error|warning|failed)" | grep -i esport | head -20`

---

## CRITICAL TRAPS (DO NOT BREAK)
- `trade_events` is P&L authority — never paper_trades
- `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
- BetaCalibrator training window starts 2026-03-16 (`_GLICKO2_FIX_DATE`). Stale pre-fix data excluded.
- All learning suspensions check `_beta_calibrators.get(game)._fitted` — they auto-deactivate. Don't remove manually.
- Kelly degradation checks ALL games — won't degrade until ALL games fitted.
- `_tournament_phase` must be defined BEFORE the if/else (Python 3.13 scoping).
- `_game_exposure` tracks USD not shares (S103 fix). `_entry_cost = price * size`.
- `daily_counter.py` now commits (S103 fix). Do NOT remove `await sess.commit()`.
- ESPORTS_MAX_BET_USD enforced in `_execute_esports_trade()` — separate from BotBankrollManager.
- `_inject_glicko2_metadata()` uses `.get("name", "").lower()` (S97 fix). Do NOT change to numeric ID.
- PatchDriftDetector: `_patch_timestamps` only set when `old is not None` (S88 fix).
- Conformal sizing handled by BotBankrollManager — do NOT add conformal override.
- Paper engine positions key: `(bot_name, market_id)` tuple (S105 fix).
- `realized_pnl_today` is now `Dict[str, float]` not `float` (S105 fix).
- Partial exit fee proration: `prorated_entry_fee = entry_fee * (exit_size / pos_size)` (S105 fix).
- CLOB API + httpx: Works with full 66-char condition_ids. Returns 404 for numeric market_ids.
- `asyncpg JSONB`: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
- Positions table: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`.
- `prediction_log`: NO `rejection_reason`. Use `trade_executed` (bool).
- Python 3.13: `from X import Y` inside function → local for ENTIRE function.
