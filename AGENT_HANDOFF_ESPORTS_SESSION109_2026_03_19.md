# AGENT HANDOFF — EsportsBot Session 109 (2026-03-19)

## Session Type: EsportsBot-scoped (anti-churn fix + WS activation)

## What Was Done

### 1. Anti-Churn Fix — DEPLOYED (Commit `f4cf596`)

**Problem**: Market 0x284a (Valorant) lost ~$606 in 28 minutes through 13 rapid stop-loss/re-entry cycles. Market 0x2ef6 (Dota2) lost ~$319 through 5 similar cycles. Total: **-$925 in 28 minutes**.

**Root causes found and fixed:**

| RC | Issue | Fix |
|----|-------|-----|
| RC1 | No post-exit reentry cooldown (WeatherBot has 900s, EsportsBot had 0) | Added `_recently_exited` dict + 900s cooldown + Redis persistence |
| RC2 | Prediction cache not cleared on stop-loss exit (same stale edge=0.13/conf=0.75 reused 13 times) | Clear `_prediction_cache[mid]` on stop-loss exit |
| RC3 | No per-market entry cap (confluence gate alone can't block — edge weight 65% exceeds 55% threshold) | Rolling 12h window, max 2 entries per market (1 original + 1 reentry) |
| RC4 | Entry price inflation (positions table stores requested price not fill price) | **OUT OF SCOPE** — touches shared position_manager |

**Config added:**
- `ESPORTS_EXIT_COOLDOWN_SECONDS=900` (15 minutes)
- `ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=2`
- `ESPORTS_ENTRY_WINDOW_HOURS=12.0`

**New waterfall counters**: `exit_cooldown`, `max_entries` — appear in scan summary when triggered.

**Files modified**: `bots/esports_bot.py`, `config/settings.py`, `tests/unit/test_esports_bot.py`

---

### 2. WS Reactive Path Activated — DEPLOYED (Commit `9f9ac4c`)

**Problem**: EsportsBot's WS reactive trading path has **NEVER worked** since it was built (S94). `ws_trading=False` for 16+ days.

**Root cause**: WS subscribed to 1000 tokens from `get_markets(active=True, limit=500)` — general Polymarket markets (politics, crypto, sports). EsportsBot gets its markets from a completely separate pipeline (`get_tradeable_esports_markets()`). Zero token overlap → `_last_ws_price_ts` never updated → WS health check always failed.

**Fix**: After each scan's parallel analysis populates `_market_token_map`, subscribe any new esports token IDs to `websocket_manager.subscribe_price_stream()`. Reconnect handler already re-subscribes all tokens in `self.subscriptions` automatically.

**Verified on VPS:**
```
esportsbot_ws_subscribed       new_tokens=10 total_esports_tokens=10
esportsbot_ws_trading_resumed
esportsbot_scan_summary        ws_trading=True
```

WS flipped to True within 6 seconds of first token subscription. All subsequent scans confirm `ws_trading=True`.

**Impact**: EsportsBot now reacts to price moves in ~1-2s instead of ~10s. For live esports matches where odds swing on every round/teamfight, this is real edge.

**Files modified**: `bots/esports_bot.py` only (no shared module changes)

---

### 3. P&L — Post-Churn Assessment

| Entry Bucket | Markets | P&L |
|---|---|---|
| 1 entry | 119 | **+$762.66** |
| 2 entries | 10 | **+$137.70** |
| 3 entries | 1 | **+$3.83** |
| **4+ entries** | **2** | **-$967.82** |

The 2/12h rolling cap loses zero profitable trades. All churn damage was in the 4+ bucket.

---

### 4. BetaCalibrator Status — 4/8 FITTED (up from 3)

| Game | N | Status |
|------|---|--------|
| Valorant | 1,927 | **FITTED** |
| LoL | 365-367 | **FITTED** |
| CS2 | 229 | **FITTED** (NEW — was 22/30 in S108) |
| SC2 | 52 | **FITTED** |
| Dota2 | ~40 | Not logging (time window issue, self-healing) |
| CoD/R6/RL | 0 | No data |

CS2 jumped from 22 to 229 resolved predictions — likely a backfill caught up.

---

## Files Modified

| File | Lines | Change |
|------|-------|--------|
| `bots/esports_bot.py` | +277 | Anti-churn (cooldown, cache clear, entry cap, Redis), WS subscription |
| `config/settings.py` | +3 | 3 new ESPORTS_ settings |
| `tests/unit/test_esports_bot.py` | +80 | 7 new tests (cooldown, cache clear, entry cap, WS guard) |

---

## Outstanding Items (EsportsBot-scoped)

| Priority | Item | Status | Action |
|----------|------|--------|--------|
| **P2** | **Scan loop speed optimization** — scan takes ~7-10s per cycle. Profile and reduce without losing functionality (market fetch, analysis, exits, monitoring). WS reactive path now handles time-critical trading, so scan can potentially run less frequently or be parallelized further. | **TODO** | Profile bottlenecks (PandaScore API, DB queries, Glicko-2 compute), consider caching/batching |
| P2 | RC4: Entry price inflation — positions table stores requested price not actual fill price | Deferred | Separate session — touches shared position_manager |
| P2 | Kelly degradation suspended (CS2 now fitted, but needs ALL 8 games) | Blocked on Dota2/CoD/R6/RL | None — wait |
| P3 | LoL Brier=0.2842 (near 0.30 halt) | Stable, monitoring | Check next session |
| P3 | EsportsSeriesBot silent | No series markets on Polymarket | Expected |
| P3 | WS reconnect stability — drops every ~40s-5min | Working (auto-reconnects + re-subscribes) | Monitor |
| P4 | Dota2 Brier=0.3002 (over threshold, 77.5% WR) | Suspension active | Self-governs when fitted |
| P5 | taker_side dead code / PAPER_BOOK_WALK_ENABLED | No data source | Deferred |

### Items RESOLVED This Session

| Item | Resolution |
|------|-----------|
| Live-match churn (-$925 in 28min) | 3-layer fix: 900s cooldown + cache clear + 2/12h entry cap |
| WS reactive path never worked | Esports tokens now subscribed to WS; `ws_trading=True` confirmed |
| Per-market entry cap assessment (S108 said "not needed") | Reversed — 24h data revealed 13 entries on one market. Cap deployed. |

---

## VPS Config (updated)

```
# Existing
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000}}
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_STOP_LOSS_PCT=0.15

# NEW (S109)
ESPORTS_EXIT_COOLDOWN_SECONDS=900
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=2
ESPORTS_ENTRY_WINDOW_HOURS=12.0
```

---

## Rollback

```bash
# Anti-churn rollback
sudo cp /opt/polymarket-ai-v2/bots/esports_bot.py.bak /opt/polymarket-ai-v2/bots/esports_bot.py
sudo cp /opt/polymarket-ai-v2/config/settings.py.bak /opt/polymarket-ai-v2/config/settings.py
sudo systemctl restart polymarket-ai
```

---

## Verification

```bash
# Anti-churn waterfall counters (appear after stop-loss fires)
journalctl -u polymarket-ai -f | grep "exit_cooldown\|max_entries\|esportsbot_stop_loss"

# WS subscription
journalctl -u polymarket-ai --since "5 min ago" | grep "esportsbot_ws_subscribed\|ws_trading"

# Scan summary
journalctl -u polymarket-ai --since "5 min ago" | grep esportsbot_scan_summary | tail -3

# P&L
sudo -u polymarket psql -d polymarket -c "SELECT event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' GROUP BY event_type;"
```
