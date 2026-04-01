# MirrorBot Agent Handoff — Session 150 Master Document
**Date:** 2026-04-01 | **Scope:** MirrorBot only — no cross-bot bleed unless explicitly requested
**Commits:** `ad07cb7` (BM n>=3, adaptive bet-size, edge decay, HTTP 425)
**Deploys:** `20260401_132559`
**Prior session:** S149 Master (`AGENT_HANDOFF_MIRRORBOT_SESSION149_MASTER.md`)

---

## 1. WHAT THIS BOT IS

MirrorBot is a real-time copy-trading bot on Polymarket. It:
1. Subscribes to RTDS WebSocket (`wss://ws-live-data.polymarket.com`) — receives every trade on the platform in real-time
2. Filters for trades from its watchlist of top 300 profitable traders (monthly leaderboard)
3. Evaluates each trade through a 28-gate chain in `_execute_mirror_trade()`
4. Sizes per-trader by copy-P&L tier (Tier 1 full / Tier 2 50% / Tier 3 25%)
5. Sizes with BotBankrollManager (Kelly criterion) + Baker-McHale shrinkage + reliability multiplier
6. Places paper orders (execution flag only difference from live)
7. Monitors positions for exits (4-tier graduated stop-loss with edge decay, force-exit, take-profit, max-hold)

**Capital:** $20K configured. **Max bet:** $300/trade. **Max daily:** $5K (further constrained by adaptive safety).
**P&L truth:** `trade_events` table, NOT `paper_trades`. Use `python scripts/bot_pnl.py MirrorBot <hours>` for canonical number.

---

## 2. WHAT S150 DID

### 2A — Phase 1 Diagnostics (VPS SSH)

**1A — Signal coverage: 94.7%** (195/206 post-S146 entries have matching trade_signals).
Action: Added `SIGNAL_REQUIRED_BOTS=MirrorBot` to VPS `.env`.

**1B — Canonical P&L (72h):** 59 open positions. All-time realized: -$107,847.
- Exit: +$415, Resolution: -$108,262
- 1018 data integrity warnings (SELL entries with no ENTRY — legacy)

**1C — Post-S146 tier performance:**
- T1: 86 entries, T2: 104, T3: 5 — ALL have ZERO exits/resolutions yet
- 225 exits+resolutions are from pre-S146 unscored positions (tier "?"), realized -$7,139
- **Cannot evaluate tier performance yet — too early. 2A blacklist and 3D shrinkage DEFERRED.**

**1D — Exit reasons (48h):**
| Reason | Count | Avg Hours | Avg PnL% | Total PnL |
|--------|-------|-----------|----------|-----------|
| stop_loss | 45 | 15.6h | -67.5% | -$6,059 |
| take_profit | 29 | — | +95.4% | +$4,209 |
| (null) | 17 | — | — | +$234 |
| force_exit | 8 | 66.5h | +2.8% | +$144 |

Stop-loss is the primary loss driver. Edge decay (2C below) addresses this.

### 2B — Baker-McHale n>=5 to n>=3 (commit `ad07cb7`)
**File:** `bots/mirror_bot.py` ~line 1976
**Change:** `if _eq_n >= 5:` → `if _eq_n >= 3:`
**Why:** Unscored/thin-data traders were +$3,485 which could be luck. BM shrinkage at n>=3 applies earlier skepticism. If the edge is real, BM passes it through; if noise, BM damps sizing.
**Blast radius:** Sizing pipeline only. No signature change.

### 2C — Adaptive Bet-Size Multiplier (commit `ad07cb7`)
**File 1:** `bots/mirror_adaptive_safety.py` — new `get_adjusted_bet_size_mult()`
**File 2:** `bots/mirror_bot.py` ~line 2050 — wired before per-market cap

Formula: `exp(-4.0 * drawdown_pct)`, floor 0.20, cap 1.0 (never boosts).
- 0% dd → 1.00x, 5% → 0.82x, 10% → 0.67x, 20% → 0.45x

**Current state:** 5.4% drawdown → 0.81x per-trade sizing. Confirmed live via `mirror_adaptive_safety_refresh drawdown_pct=0.054`.

**Why:** Previously adaptive safety only shrunk position COUNT and daily CAP during drawdowns, but each individual trade stayed full-sized. This adds per-trade defense-in-depth.

Log signature:
```
mirror_adaptive_bet_size mult=0.807 market=0xabc123def456
```

### 2D — Edge Decay on Held Positions (commit `ad07cb7`)
Three touch points:

1. **Entry:** `entry_confidence` stored in position dict at trade execution (~line 2162)
2. **Startup:** `entry_confidence` restored from trade_events ENTRY via DISTINCT ON query (~line 288). Fallback 0.55 if not found.
3. **Exit eval:** After computing graduated stop-loss threshold (~line 971):
   ```python
   decayed_conf = entry_confidence - 0.02 * days_held
   if decayed_conf < 0.50:
       effective_stop *= 0.50  # halve stop → tighter exit
   ```

**Effect:** Position with entry_conf=0.55 held 3 days → decayed to 0.49 → stop halved.
- At 0-24h tier: stop goes from -6% to -3%
- At 72h+ tier: stop goes from -12% to -6%

**Confirmed live:** `mirror_entry_confidence_restored enriched=59 total=59` — all positions got confidence.

Log signature (debug level):
```
mirror_edge_decay_tighten market=0xabc entry_conf=0.550 decayed_conf=0.490 days_held=3.0 tightened_stop=0.0300
```

### 2E — HTTP 425 Retryable (commit `ad07cb7`)
**File 1:** `base_engine/execution/async_clob_client.py` ~line 123
- HTTP status codes 425/429/502/503 now return `{"retryable": True}` in error dict

**File 2:** `base_engine/execution/execution_engine.py` ~line 317
- After receiving order_result, checks for `retryable=True` before breaking retry loop
- Retries with exponential backoff instead of accepting failure

**File 3:** `tests/unit/test_pass3_fixes.py` — un-xfailed Fix 3 test

**Note:** Paper trading bypasses clob_adapter entirely. This is a pre-live safety net.

### 2F — Tests
- 10 new tests added to `tests/unit/test_mirror_bot_logic.py`:
  - `TestBakerMcHaleThreshold` (2 tests): BM activates at n=3, k increases with n
  - `TestAdaptiveBetSizeMult` (5 tests): no-dd returns 1, 10% dd, floor 0.20, unfitted, no boost
  - `TestEdgeDecay` (4 tests): decay formula, stop halved, no tightening when fresh, default conf
- Fix 3 test un-xfailed (was xfail, now passes)
- **Suite: 1740 passed, 2 skipped, 6 xfailed** (was 7 xfailed)

---

## 3. CURRENT PLAN STATE (updated from S149)

### COMPLETED
- Phase 0: Fix the Source (S146) ✅
- Phase 1: Fix the Filters (S146-S147) ✅
- Phase 2: Fix Infrastructure (S147) — MOSTLY COMPLETE
  - 2A: DB pool — resolved ✅
  - 2B: S145 signal auto-save ✅
  - 2C: SIGNAL_REQUIRED_BOTS=MirrorBot — **✅ DEPLOYED S150** (94.7% coverage)
  - 2D: Stale prices — mostly resolved by 48h force-exit ✅
- S149: Shadow confidence band 0.50-0.55 ✅
- S149: CLAUDE.md forbidden patterns #7-#9 ✅
- **S150: Baker-McHale n>=3 ✅ DEPLOYED**
- **S150: Adaptive bet-size multiplier ✅ DEPLOYED**
- **S150: Edge decay on exits ✅ DEPLOYED**
- **S150: HTTP 425 retryable ✅ DEPLOYED**

### REMAINING (priority order)
1. **P2 — Monitor S146-S150 impact** (SSH) → wait for post-S146 resolutions to evaluate tier performance
2. **4B — Trader blacklist 35%→45%** (config) → DEFERRED until T1 has resolution data
3. **4C — Confidence shrinkage rework** (code) → DEFERRED until 1000+ post-S146 resolved trades
4. **4D — Baker-McHale n≥3** → ✅ DONE (was priority, now deployed)
5. **P3 — Remaining 6 xfailed Pass 3 tests** → Fix 1 (CLOB signature) = pre-live. Fix 2 (WS cap) = defer unless sub count approaches 400.

### REMOVED (with reasons)
- ~~1D: Confidence 0.55→0.58~~ — no data support (S148)
- ~~3A: WR-adaptive cooldown~~ — re-entries net +$1,502, only 4% of markets (S148)
- ~~3C: Dynamic category blocklist~~ — user decision, static list sufficient (S148)
- ~~4A: Refresh forensics~~ — user decision (S148)
- ~~3D: Edge decay~~ → ✅ DONE (S150)
- ~~3B: Adaptive bet-size multiplier~~ → ✅ DONE (S150)

---

## 4. CURRENT CONFIG STATE

### Code Defaults (`config/settings.py`)
```python
MIRROR_MAX_SPREAD          = 0.08
MIRROR_MIN_CONFIDENCE      = 0.55
MIRROR_MAX_SLIPPAGE_PCT    = 0.05
TOP_TRADER_COUNT           = 300
WATCHLIST_SIZE             = 300
MIRROR_NO_SIDE_DAMPENER    = 0.3
MIRROR_NO_BLOCK_FLOOR      = 0.20
MIRROR_NO_MIN_EDGE         = 0.05
MIRROR_STOP_LOSS_PCT       = 0.15
MIRROR_STOP_LOSS_TIGHTEN_24H = -0.06
MIRROR_STOP_LOSS_TIGHTEN_48H = -0.12
MIRROR_STOP_LOSS_TIGHTEN_72H = -0.15
MIRROR_STOP_LOSS_NEAR_RES_PCT = -0.05
MIRROR_MAX_HOLD_FRACTION   = 0.80
MIRROR_FORCE_EXIT_HOURS    = 96
MIRROR_COPY_TIER2_MULT     = 0.50
MIRROR_COPY_TIER3_MULT     = 0.25
MIRROR_COPY_MIN_TRADES_FOR_TIER = 20
RTDS_RECV_TIMEOUT          = 25
MIRROR_TRADER_MIN_WIN_RATE = 0.35  # blacklist threshold
```

### VPS `.env` Overrides (`/opt/pa2-shared/.env`)
```bash
MIRROR_ADAPTIVE_SAFETY=true
MIRROR_CATEGORY_BLOCKLIST=crypto,15-minute,speed,finance
MIRROR_MAX_CONCURRENT_POSITIONS=1000
MIRROR_MIN_CONFIDENCE=0.55
MIRROR_NO_SIDE_DAMPENER=0.3
MIRROR_SKIP_LIQUIDITY_RTDS=true
MIRROR_USE_CALIBRATION=false
MIRROR_USE_CONFORMAL=true
WATCHLIST_ENABLED=true
MIRROR_FORCE_EXIT_HOURS=48
MIRROR_MAX_HOLD_FRACTION=0.60
MIRROR_STOP_LOSS_PCT=0.12
MIRROR_STOP_LOSS_TIGHTEN_48H=-0.08
MIRROR_STOP_LOSS_TIGHTEN_72H=-0.10
MIRROR_STOP_LOSS_NEAR_RES_PCT=-0.03
SIGNAL_REQUIRED_BOTS=MirrorBot  # S150: 94.7% coverage
```

---

## 5. SIZING FORMULA — FULL PIPELINE (updated S150)

```
1. Kelly:     f* = (p*b - q)/b  where b=(1-price)/price, p=confidence, q=1-p
              → capped at max_bet_usd=$300, kelly_fraction=0.25

2. Reliability mult:
              lr = Beta(6,10) prior + win/loss likelihood
              reliability_mult = min(lr, 1.0) × min(1.0, n/50)

3. Copy-P&L tier mult (S146):
              Tier 1 (copy-profitable, n≥20): 1.0x
              Tier 2 (thin data):             0.50x
              Tier 3 (copy-unprofitable, n≥20): 0.25x

4. Baker-McHale (S142, S150):
              k = edge² / (edge² + p*(1-p)/n)
              size *= k  (S150: now applied when n ≥ 3, was n ≥ 5)

5. NO dampener (S142/S146):
              Edge gate: confidence - price >= 0.05
              price < 0.20 → BLOCK
              price < 0.25 → 0.15×
              price < 0.40 → 0.30×
              price < 0.60 → 0.50×
              else         → 0.75×

6. S150: Adaptive bet-size multiplier:
              mult = exp(-4.0 × drawdown_pct)
              size *= max(0.20, min(1.0, mult))
              (0%→1.0x, 5%→0.82x, 10%→0.67x, 20%→0.45x)

7. Per-market cap: min(capital × 5%, $400) → cap USD
8. Daily remaining cap: min(size, (max_daily - _daily_exposure) / price)
   → daily cap also adjusted by adaptive safety: max_daily *= exp(-8.0 × dd)
9. Dust gate: size × price >= $50 (else reject)
10. VWAP trim (S147): if book_walk VWAP > sizing_price, trim shares to original USD
```

---

## 6. EXIT LOGIC — 4-TIER STOP-LOSS + EDGE DECAY (updated S150)

```
Priority order (first match wins):
1. Take-profit: PnL >= +25%  → exit_reason="take_profit"
2. Force-exit: held >= 48h   → exit_reason="force_exit"
3. Max-hold fraction: held > 60% of total market duration → exit_reason="max_hold_fraction"
4. Edge decay (S150):
   decayed_conf = entry_confidence - 0.02 × days_held
   if decayed_conf < 0.50 → halve the stop-loss threshold
5. Graduated stop-loss (with S150 decay applied):
   - Near-res (<24h TTR): -3% (or -1.5% if decayed)
   - 0-24h held:   -6%  (or -3% if decayed)
   - 24-48h held:  -8%  (or -4% if decayed)
   - 48-72h held:  -10% (or -5% if decayed)
   - 72h+ held:    -12% (or -6% if decayed)
6. Circuit breaker: total unrealized <= -20% of capital → pause entries 15min
```

---

## 7. CRITICAL TRAPS — DO NOT BREAK

1. **`_market_meta_cache` is a 3-tuple `(cat, ttr, expiry_monotonic)`** — never expand
2. **`side="YES"/"NO"` only** — never BUY/SELL in `place_order()`
3. **`trade_events` is P&L authority** — never read `paper_trades` for P&L
4. **`_eq_n` must be initialized to 0 before reliability tracker try-block** — BM references it
5. **`MIRROR_NO_SIDE_DAMPENER < 1.0` required** to activate dynamic NO dampener
6. **VPS `.env` overrides code defaults** — always check `/opt/pa2-shared/.env`
7. **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — both must pass
8. **`asyncpg JSONB`:** use `CAST(:x AS jsonb)` not `:x::jsonb`
9. **`PSEUDO_LABEL_ENABLED=false`** — DO NOT enable
10. **Deploy heredoc escaping** — `<<REMOTE` is unquoted, `$` expands locally, use `\$` for VPS
11. **event_data trader field** — S146: stores full address. Historical data has [:10] truncated.
12. **Price floor 3c-97c** — S146 defense-in-depth at T0 gate.
13. **`positions_to_close` is `List[tuple]`** — S147: `(pos_key, exit_event_data)`, NOT `List[str]`
14. **Paper VWAP trim** — S147: trims shares when VWAP > sizing price
15. **DB connection** — PgBouncer on port 6432, password `polymarket_s46`. NOT direct postgres on 5432.
16. **P&L reporting** — ALWAYS run `scripts/bot_pnl.py` FIRST. See CLAUDE.md Forbidden Patterns #7-#9.
17. **S150: `entry_confidence` in position dict** — stored at entry, restored from trade_events on startup. Default 0.55 if not found. Used by edge decay.
18. **S150: Baker-McHale threshold is n>=3** — was n>=5 before S150.
19. **S150: Adaptive bet-size mult uses -4.0 decay** — position limits use -8.0. Different constants.

---

## 8. VPS OPERATIONS

```bash
# SSH
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# Deploy
cd C:/lockes-picks/polymarket-ai-v2
bash deploy/deploy.sh

# Monitor MirrorBot
sudo journalctl -u polymarket-mirror -f | grep -E 'mirror_|MirrorBot|RTDS|rtds'

# S150 features
sudo journalctl -u polymarket-mirror --since '5 minutes ago' --no-pager | \
  grep -E 'mirror_adaptive_bet_size|mirror_edge_decay_tighten|mirror_bm_shrinkage|mirror_entry_confidence_restored|adaptive_safety_refresh'

# S147-S149 features
sudo journalctl -u polymarket-mirror --since '5 minutes ago' --no-pager | \
  grep -E 'exit_reason|paper_vwap_size_trim|copy_tier|mirror_no_edge|force_exit|autonomous stop-loss|price_floor|mirror_shadow_conf_band'

# Check .env
cat /opt/pa2-shared/.env | grep -E 'MIRROR_|WATCHLIST|SIGNAL_REQUIRED'

# CANONICAL P&L (always run this first)
cd /opt/pa2-releases/$(readlink /opt/polymarket-ai-v2 | xargs basename) && \
  PYTHONPATH=. venv/bin/python scripts/bot_pnl.py MirrorBot 72

# DB queries (PgBouncer)
PGPASSWORD=polymarket_s46 psql -h 127.0.0.1 -p 6432 -U polymarket -d polymarket

# Clean restart
sudo systemctl stop polymarket-mirror && sleep 3 && sudo systemctl start polymarket-mirror

# Rollback
sudo ln -sfn /opt/pa2-releases/20260401_111415 /opt/polymarket-ai-v2
sudo systemctl restart polymarket-mirror
```

---

## 9. DATA INSIGHTS (updated S150)

### Post-S146 Tier Status (as of S150 deploy)
- 195 entries with matching signals (94.7% coverage)
- T1: 86 entries, T2: 104, T3: 5 — **ZERO resolutions yet**
- All exits/resolutions in 72h window are pre-S146 unscored positions
- **Cannot evaluate tier performance until post-S146 entries resolve**

### Exit Reason Analysis (48h, S150)
- Stop-loss: 45 exits, avg 15.6h held, avg -67.5% PnL, -$6,059 total
- Take-profit: 29 exits, avg +95.4%, +$4,209
- Force-exit: 8 exits, avg 66.5h, +$144 (near breakeven — working as designed)
- Edge decay will tighten stops on aging losers → should reduce stop-loss depth

### Adaptive Safety State at Deploy
- Drawdown: 5.4% → bet-size mult = 0.81x
- Win rate: 40% (last 50 trades)
- Consecutive losses: 0
- Net effect: each trade is 19% smaller than at 0% drawdown

---

## 10. WHAT TO DO NEXT SESSION

**Priority order:**
1. **P1 — Confidence gate review (0.55→0.52?)** — S150 data: 2,188 shadow band rejects (0.50-0.55) vs 1,700 low-confidence rejects (<0.50) in 24h. 56% of all confidence rejects fall in the shadow band. Query post-S146 resolution outcomes for trades that WOULD have passed at 0.52 to estimate EV. If positive → lower gate. If negative → keep 0.55.
2. **P2 — Monitor S150 impact** — edge decay tightening frequency, adaptive bet-size in logs, BM shrinkage at n=3-4, stop-loss depth trend
3. **Wait for post-S146 resolutions** — need T1/T2/T3 resolution data before touching blacklist or shrinkage
4. **4B — Trader blacklist 35%→45%** — BLOCKED on tier resolution data
5. **4C — Confidence shrinkage rework** — BLOCKED on tier resolution data
6. **P3 — Remaining 6 xfailed Pass 3 tests** — Fix 1 (CLOB signature) = pre-live. Fix 2 (WS cap 450) = defer.

**Context for next session:**
- S150 deployed 2026-04-01 17:28 UTC
- S146 deployed 2026-03-30 12:43 UTC (~2.2 days ago)
- 59 open positions, RTDS connected, 300 elites, 6ms scans
- Drawdown 5.4%, WR 40%, 0 consecutive losses
- Entry rate ~32/day (down from 189/day pre-S146)
- All-time realized: -$107,847

---

## 11. SESSION HISTORY (S146-S150)

| Session | Key Changes | Commits | Deploy |
|---------|------------|---------|--------|
| S146 | Copy-P&L tiered watchlist, NO edge gate, 4-tier stop-loss, force-exit 48h, price floor 3c-97c | `a5474e3`, `786118b` | `20260330_124353` |
| S147 | EXIT event_data exit_reason, VWAP book walk USD cap trim | `577233c`, `769fa10` | `20260330_133741`, `20260330_192126` |
| S148 | Plan refinement, shadow conf band design, re-entry analysis | uncommitted | — |
| S149 | Shadow watch 0.50-0.55, CLAUDE.md forbidden patterns #7-#9, 72h P&L analysis | `d1e3ec0`, `3550292`, `b649711` | `20260401_111415`, `20260401_112750` |
| S150 | BM n>=3, adaptive bet-size mult, edge decay, HTTP 425, SIGNAL_REQUIRED | `ad07cb7` | `20260401_132559` |

---

## 12. FEEDBACK RULES (MANDATORY)

### "Fix Not Remove" (NON-NEGOTIABLE, ALL BOTS)
Removing an item is NEVER the answer. Fixing the root so it functions IS.

### Scope Lock
NEVER add unsolicited features. Only fix what the handoff or user explicitly requests.

### Canonical Script First (S149)
ALWAYS run `bot_pnl.py` before ANY ad-hoc SQL for P&L. See CLAUDE.md Forbidden Patterns #7-#9.

### P&L Math
- NEVER invert formulas for NO positions — prices are token-specific
- `cost = entry_price * size` (ALL sides), `uPnL = (current - entry) * size`
- Canonical script: `python scripts/bot_pnl.py BotName hours`

### Bot Sessions
This is a MirrorBot-only session. No cross-bot bleed unless explicitly requested.
