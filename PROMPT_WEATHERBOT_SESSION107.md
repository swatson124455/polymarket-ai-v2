# WeatherBot Session 107 — Scope-Locked Task Prompt
# Copy-paste this into a fresh session. DO NOT bleed into MirrorBot or EsportsBot.

---

## SCOPE LOCK
You are working on **WeatherBot ONLY**. Do not touch MirrorBot, EsportsBot, or any other bot's files. If a shared module needs changes, justify it explicitly and verify all 14 bots.

---

## READ FIRST (in this order)
1. `CLAUDE.md` — development rules (surgical fixes, zero collateral damage)
2. `AGENT_HANDOFF_WEATHERBOT_SESSION106_2026_03_18.md` — latest handoff (S106: 7 items completed)
3. `AGENT_HANDOFF_WEATHERBOT_SESSION104_2026_03_18.md` — prior session (S104 + S104b)
4. `bots/weather_bot.py` — the bot (~4,000 lines)
5. `base_engine/weather/probability_engine.py` — EMOS + skew-normal
6. `base_engine/execution/paper_trading.py` — fill model (shared, read-only unless bug found)
7. `config/settings.py` — all config
8. `scripts/fill_quality.py` — fill quality analytics (new in S106)
9. `tests/` — relevant weather tests

---

## CURRENT STATE (as of S106, 2026-03-18)
- **P&L**: +$2,888 realized (2889 entries, 267 exits, 677 resolutions)
- **Open positions**: ~96
- **Key config**: kelly=0.25, capital=$20K, max_bet=$300 (BotBankrollManager), max_daily=$10K
- **RISK_MAX=$1,000** (NOT $100 — prior prompts were wrong. BotBankrollManager max_bet=$300 is the real per-trade cap)
- **S106 changes**: Taker-side flat factor (0.55), probability_engine fallback fix, stale positions table fix, negative counter clamp, CLAUDE.md cap correction, fill_quality.py script
- **VPS DEPLOY STATUS**: S106 changes are UNCOMMITTED. VPS still running S104b code. WEATHER_DAILY_LOSS_LIMIT still $2,000 on VPS (should be $10,000).

---

## IMMEDIATE TASKS (before any new work)

### Step 0: Commit + Deploy S106 Changes
All S106 code changes are verified (1622 tests pass) but NOT committed. Before doing anything else:
1. `git status` — verify the diff matches S106 handoff Section 4
2. Commit with message format per CLAUDE.md (one fix per commit ideally, but these are small enough for one bundled commit)
3. Deploy to VPS via `deploy.sh`
4. Verify post-deploy: `journalctl -u polymarket-ai -f | grep weatherbot_scan_done`

---

## TASK LIST — IN PRIORITY ORDER

### P1: Post-Deploy Monitoring (immediate after deploy)
After deploying S106, verify:
1. Taker-side factor is firing: `journalctl --since "30 min ago" | grep paper_taker_side` (or check event_data for fill_prob reduction)
2. Negative counters clamped: `SELECT * FROM daily_counters WHERE bot_id='WeatherBot' AND counter_value < 0;` (should be 0 rows)
3. Stale close working: `grep weatherbot_stale_closed` in logs
4. Scan health: `grep weatherbot_scan_done` — should show entries/opportunities/trades

### P2: Dallas City P&L Re-evaluation (2 weeks post-S104)
S104 added city tags to event_data. After ~2 weeks of data:
```sql
SELECT event_data->>'city' as city,
       sum((event_data->>'realized_pnl')::numeric) as pnl,
       count(*) as resolutions
FROM trade_events
WHERE bot_name='WeatherBot' AND event_type='RESOLUTION'
  AND event_data ? 'city'
GROUP BY city ORDER BY pnl ASC;
```
If Dallas is worst by >$200, raise min_edge from 0.08 to 0.12 for Dallas specifically.

### P3: Cross-Bot Feature Assessment
Read-only analysis, no code changes:
1. **Price bucket dampeners** — MirrorBot dampens 30-50c (0.50x) and >=70c (0.40x). Does WeatherBot show similar bucket P&L patterns?
2. **Per-market entry cap** — MirrorBot caps 2 entries/market. WeatherBot can stack. Is stacking profitable?
3. **ADWIN drift detection** — EsportsBot uses ADWIN. WeatherBot uses DDM/EDDM. Is ADWIN better?

### P4: Fill Quality Monitoring
Run `scripts/fill_quality.py 168` (1 week) after deploy. Compare:
- Pre-taker-factor avg_fill_prob (~0.42) vs post (~0.23 expected)
- If rejection rate >85%, consider raising PAPER_TAKER_SIDE_FACTOR from 0.55 to 0.70
- Track slippage by city — flag any city consistently >15bps

### P5: Unit Test for probability_engine Fallback
The P7 fix (degenerate distribution returns `{}`) has no dedicated test. Add:
```python
def test_bucket_probabilities_fallback_degenerate_returns_empty(self):
    """Degenerate distribution (all probs ~0) returns empty dict, not uniform."""
    # Force degenerate: loc=100, scale=0.001, buckets at 50-60F
    buckets = self._make_buckets()  # centered far from loc
    probs = self.engine._bucket_probabilities_fallback(100.0, 0.001, buckets)
    assert probs == {}
```

### P6: Cap Simplification Decision
The full cap hierarchy is documented in S106 handoff Section 2 (P2). The sizing pipeline has 6 layers but BotBankrollManager max_bet=$300 is the real cap.
- **Option A**: Raise max_bet to let pipeline breathe (more variance)
- **Option B**: Lower max_bet to $200 (more conservative)
- **Option C**: Keep as-is ($300)
- **Do NOT change without explicit user approval**

---

## CRITICAL TRAPS (DO NOT BREAK)
- `trade_events` is P&L authority — never paper_trades
- `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL"
- Alpha decay is BUY-only (S104b). DO NOT remove `side == "BUY"` gate
- `_market_group_cache` 3-tuple: NEVER expand
- `_restore_exposure_from_db()` uses daily_counter, NOT paper_trades
- `_close_stale_positions()` direct DB UPDATE — no EXIT event. By design
- Exposure reserved BEFORE place_order() under lock, reverted on failure
- `event_data` dict mutated in-place — DO NOT copy before passing
- `WEATHER_SKIP_COORDINATOR_BUY=True` — confirm_position() direct INSERT
- `scan_start_mono` in event_data required for alpha decay
- `asyncpg JSONB`: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- Baker-McHale `1/(1+sigma^2)` is INTENTIONAL
- RISK_MAX=$1,000 (not $100). Real cap is BotBankrollManager max_bet=$300
- Paper engine positions key: `(bot_name, market_id)` tuple
- Python 3.13: `from X import Y` inside function -> local for ENTIRE function
- `PAPER_TAKER_SIDE_FILTER=true` must be on for flat factor to apply
- probability_engine fallback returns `{}` for degenerate (S106)
- VPS WEATHER_DAILY_LOSS_LIMIT=$2,000 until deployed (should be $10,000)

---

## VERIFICATION AFTER ANY CHANGES
1. `pytest` — all 1622+ pass
2. List every file modified
3. One fix per commit
4. Write change log per CLAUDE.md format
5. Verify on VPS after deploy:
   - `journalctl -u polymarket-ai -f | grep weatherbot_scan_done`
   - `journalctl -u polymarket-ai -f | grep weatherbot_exposure_decremented`
