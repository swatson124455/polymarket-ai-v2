# MirrorBot Agent Handoff — Session 143 Master Document
**Date:** 2026-03-29 | **Scope:** MirrorBot only — no cross-bot bleed unless explicitly requested
**Deploy:** `20260329_173227` (commit `a538cf4`) — LIVE on VPS

---

## 1. WHAT THIS BOT IS

MirrorBot is a real-time copy-trading bot on Polymarket. It:
1. Subscribes to RTDS WebSocket (`wss://ws-live-data.polymarket.com`) — receives every trade on the platform in real-time
2. Filters for trades from its watchlist of top 300 profitable traders
3. Evaluates each trade through a 28-gate chain in `_execute_mirror_trade()`
4. Sizes with BotBankrollManager (Kelly criterion) + Baker-McHale shrinkage + reliability multiplier
5. Places paper orders (execution flag only difference from live)
6. Monitors positions for exits (stop-loss, time-based, consensus SELL)

**Capital:** $20K configured. **Max bet:** $300/trade. **Max daily:** $5K.
**P&L truth:** `trade_events` table, NOT `paper_trades`. Use `python scripts/bot_pnl.py MirrorBot 168` for the canonical number.
**Actual realized P&L to date:** +$15,051 (post-dedup-fix, as of S86).

---

## 2. SYSTEM ARCHITECTURE — MIRRORBOT SPECIFIC

### Key Files
| File | Purpose |
|------|---------|
| `bots/mirror_bot.py` | Main bot — all entry/exit/sizing logic |
| `bots/mirror_adaptive_safety.py` | Drawdown circuit breaker (`exp(-8*drawdown_pct)`) |
| `bots/mirror_calibration.py` | FTS+Le2026 calibration stack (currently disabled via env) |
| `bots/mirror_ml_selector.py` | XGBoost+Q-learning shadow selector (shadow only, not live) |
| `bots/elite_watchlist.py` | RTDS watchlist management, trader scoring |
| `base_engine/risk/bankroll_manager.py` | Kelly sizing: `f* = (p*b - q)/b` |
| `base_engine/learning/elite_reliability.py` | Beta(6,10) prior, likelihood_ratio, sample ramp |
| `config/settings.py` lines 325–416 | All MIRROR_ config values |

### Data Flow
```
RTDS WebSocket → _on_rtds_trade() → watchlist filter → _execute_mirror_trade()
                                                              │
                                    ┌─────────────────────────┤
                                    │  28-gate chain (in order):
                                    │  T0: SELL bypass (has position → exit)
                                    │  T0: category blocklist (crypto/finance/speed)
                                    │  T0: same-side dedup (already holding YES → skip)
                                    │  T0: market cooldown (1800s between attempts)
                                    │  T0: market maker detection
                                    │  T1: blacklist (WR <35% after 20+ trades)
                                    │  T1: min whale trade USD ($50)
                                    │  T1: max concurrent positions (1000)
                                    │  T1: daily exposure cap
                                    │  T2: market data (spread, volume, price correction)
                                    │  T2: spread gate (max 8c)
                                    │  T2: volume gate (>$5K 24h)
                                    │  T2: slippage gate (max 5%)
                                    │  T2: price direction (not already moved >5%)
                                    │  T2: TTR gate (>4h to resolution)
                                    │  T2: sports NO block
                                    │  T3: elite reliability (LR gate, sample ramp)
                                    │  T3: multi-factor confidence (4-factor formula)
                                    │  T3: calibration (disabled)
                                    │  T3: min_confidence gate (0.55)
                                    │  T4: Kelly sizing (BotBankrollManager)
                                    │  T4: reliability_mult (min(LR,1.0) × sample_ramp)
                                    │  T4: Baker-McHale shrinkage k=edge²/(edge²+var)
                                    │  T4: NaN guard
                                    │  T4: dynamic NO dampener (price-tiered)
                                    │  T4: per-market cap ($150 = 3000×5%)
                                    │  T4: daily remaining cap
                                    │  T4: dust gate ($50 min)
                                    └─────────→ place_order()
```

### In-Memory State (critical — survives restart via DB restore)
| Variable | Type | Persisted how |
|----------|------|---------------|
| `_open_positions` | dict `mkt:tok → {side,size,entry,traders,ts}` | `positions` table via `_restore_state_on_startup()` |
| `_daily_exposure` | float | `paper_trades` SUM on startup |
| `_recently_entered_sides` | dict `mkt:tok:side → ts` | DB query on startup (`mirror_entered_sides_restored`) |
| `_dedup_cache` | set | DB query on startup (`mirror_dedup_restored`) |
| `_market_cooldowns` | dict | NOT persisted (TTL-only, restart resets) |
| `_token_side_cache` | dict | NOT persisted (re-queried from DB on miss) |

**P1 KNOWN BUG:** `current_price` in `_open_positions` is NOT restored from DB on startup. Positions restored on startup have `current_price = entry_price` until first fill event. Stop-loss uses stale prices for ~10-30 min after restart. This needs fixing (next session priority).

---

## 3. CURRENT CONFIG STATE

### Code Defaults (`config/settings.py`)
```python
MIRROR_MAX_SPREAD          = 0.08   # S142: was 0.20
MIRROR_MIN_CONFIDENCE      = 0.55   # S142: was 0.50
MIRROR_MAX_SLIPPAGE_PCT    = 0.05   # S142: was 0.08
TOP_TRADER_COUNT           = 300    # S142: was 500
WATCHLIST_SIZE             = 300    # S142: was 500
MIRROR_NO_SIDE_DAMPENER    = 0.3    # default (can override in .env)
MIRROR_STOP_LOSS_PCT       = 0.15
MIRROR_STOP_LOSS_TIGHTEN_48H = -0.12
MIRROR_STOP_LOSS_TIGHTEN_72H = -0.15
MIRROR_STOP_LOSS_NEAR_RES_PCT = -0.05  # <24h to res
MIRROR_MAX_HOLD_FRACTION   = 0.80   # exit after 80% of market duration
MIRROR_MIN_WHALE_TRADE_USD = 50.0
MIRROR_MIN_TRADE_USD       = 50.0
RTDS_RECV_TIMEOUT          = 25     # seconds
```

### VPS `.env` Overrides (`/opt/pa2-shared/.env`)
```bash
MIRROR_ADAPTIVE_SAFETY=false          # ← PHASE 4 INCOMPLETE — should be true
MIRROR_CATEGORY_BLOCKLIST=crypto,15-minute,speed,finance
MIRROR_MAX_CONCURRENT_POSITIONS=1000
MIRROR_MIN_CONFIDENCE=0.55            # S142 updated
MIRROR_ML_MODEL_PATH=/opt/pa2-shared/saved_models/mirror_ml_selector.pkl
MIRROR_ML_QTABLE_PATH=/opt/pa2-shared/saved_models/mirror_ml_qtable.pkl
MIRROR_NO_SIDE_DAMPENER=0.3           # S142 updated (was 1.0)
MIRROR_SKIP_LIQUIDITY_RTDS=true
MIRROR_USE_CALIBRATION=false
MIRROR_USE_CONFORMAL=true
WATCHLIST_ENABLED=true
```

---

## 4. S142 CHANGES (THIS SESSION) — DEPLOYED AS `a538cf4`

### Phase 1 — Config (settings.py)
- `MIRROR_MAX_SPREAD`: 0.20→**0.08** — entering with 20c spread = starting 20% underwater
- `MIRROR_MIN_CONFIDENCE`: 0.50→**0.55** — aligns with profitable confidence bucket
- `MIRROR_MAX_SLIPPAGE_PCT`: 0.08→**0.05** — tighter execution quality floor
- `TOP_TRADER_COUNT`/`WATCHLIST_SIZE`: 500→**300** — higher-conviction watchlist

### Phase 2 — Dynamic NO-side Dampener (mirror_bot.py ~L1894)
Replaced flat `MIRROR_NO_SIDE_DAMPENER × size` with price-tiered:
```python
if price < 0.10:  → BLOCK (return False)
elif price < 0.25: → 0.15×
elif price < 0.40: → 0.30×
elif price < 0.60: → 0.50×
else:              → 0.75×
```
Gate: only fires when `MIRROR_NO_SIDE_DAMPENER < 1.0` (currently 0.3 in .env ✅).

### Phase 3 — Baker-McHale Edge-Uncertainty Shrinkage (mirror_bot.py ~L1871)
```python
# After Kelly sizing + reliability_mult, before NaN guard:
if _eq_n >= 5:
    k = edge² / (edge² + p*(1-p)/n)
    size *= k
    # log if k < 0.90
```
Where `edge = max(0, confidence - price)`, `n = _eq_n` (trader's total trade count).

### Phase 4 — Adaptive Safety Re-enable
**NOT DONE.** `.env` still has `MIRROR_ADAPTIVE_SAFETY=false`.
To complete: `sudo sed -i 's/^MIRROR_ADAPTIVE_SAFETY=false$/MIRROR_ADAPTIVE_SAFETY=true/' /opt/pa2-shared/.env && sudo systemctl restart polymarket-ai`
Then verify: `journalctl -u polymarket-ai -f | grep -E 'adaptive_safety|safety_mult'`

### Bugfix Created During S142 (not in original plan)
`_eq_n = 0` initialized at line ~1652 (before the reliability tracker try-block) to prevent `UnboundLocalError` if the tracker skips/throws before assigning `_eq_n` at line 1669.

### Test Coverage Gap Created During S142
`test_spread_gate_allows_tight_spread` was updated with WR=0.85 to survive BM shrinkage. Original scenario (WR=0.60, tight spread → correctly rejected by BM) has no test. Not broken, but coverage gap exists.

---

## 5. SIZING FORMULA — FULL PIPELINE

```
1. Kelly:     f* = (p*b - q)/b  where b = (1-price)/price, p=confidence, q=1-p
              → capped at max_bet_usd=$300, kelly_fraction=0.25
              → returns shares (USD/price)

2. Reliability mult:
              lr = Beta(6,10) prior + win/loss likelihood
              reliability_mult = min(lr, 1.0) × min(1.0, n/50)
              → penalize unreliable traders; never amplify above 1.0

3. Baker-McHale (S142):
              k = edge² / (edge² + p*(1-p)/n)
              size *= k  (only when n ≥ 5)
              → shrinks toward 0 when edge estimate is uncertain

4. NO dampener (S142):
              price-tiered multiplier (0.15–0.75×) when side=NO and MIRROR_NO_SIDE_DAMPENER < 1.0

5. Per-market cap:
              min(capital × 5%, $400)  → cap USD, convert to shares

6. Daily remaining cap:
              min(size, (max_daily - _daily_exposure) / price)

7. Dust gate:
              size × price >= $50  (else return False)
```

---

## 6. CONFIDENCE FORMULA (multi-factor, S110)

```python
# Factor 1: Category Bayesian base
if category:
    shrinkage = cat_n / (cat_n + 20)
    _base = 0.50 + shrinkage × (cat_wr - 0.50)
    if cat_n < 10: _base = min(_base, 0.52)
else:
    shrinkage = min(1.0, total_n / 50)
    _base = 0.50 + shrinkage × (overall_wr - 0.50)

# Factor 2: Price deviation (ZEROED — anti-signal per S132 data)
_price_adj = 0.0

# Factor 3: Whale conviction (large vs avg trade)
_conv_adj = +0.04 (trade > 2× avg) or -0.03 (trade < 0.3× avg) or 0

# Factor 4: TTR adjustment
_ttr_adj = -0.05 (<12h), +0.02 (12-48h), 0 (48-168h), -0.02 (>168h)

confidence = max(0.35, min(0.75, _base + _price_adj + _conv_adj + _ttr_adj))
```

---

## 7. OPEN ITEMS (PRIORITY ORDER)

### P1 — `current_price` Coverage Gap (BLOCKING for accurate stop-loss)
**Root cause:** `_restore_state_on_startup()` restores `_open_positions` from DB but does NOT set `current_price` from the positions table. All restored positions start with `current_price = entry_price`. Stop-loss is comparing against stale price for up to 30 min after every restart.
**Fix needed:** In `_restore_state_on_startup()`, after loading positions from DB, query `positions.current_price` and populate `_open_positions[key]['current_price']`.
**Risk if not fixed:** Stop-loss fires too late or not at all during the restart window. Not P0 because restarts are infrequent, but this is a real trading risk.

### P2 — Phase 4: Re-enable MIRROR_ADAPTIVE_SAFETY
**Action:** Single `.env` line change + restart (see Section 4).
**Risk:** On restart, `_fitted=False` until enough trade history loads (~first scan cycle). During that window, adaptive safety is bypassed. Acceptable risk for a single restart window.

### P3 — PgBouncer Pool Exhaustion (Infrastructure)
**Problem:** PgBouncer `default_pool_size=25` is too small for 14 bots sharing the pool during restart/migrations. Deploy script migration step consistently fails because pool is exhausted by running bots. Worked around in S142 by running migration directly via postgres unix socket.
**Proper fix:** Increase `default_pool_size` to 40-50 in `/etc/pgbouncer/pgbouncer.ini`, or add a pre-migration step that lowers bot connection pressure temporarily.
**File:** `/etc/pgbouncer/pgbouncer.ini` — requires sudo.

### P4 — Test Coverage Gap (test_spread_gate_allows_tight_spread)
Add a test for: WR=0.60 trader, tight spread, price=0.55 → `result is False` because BM correctly kills the low-edge trade. Documents the interaction between BM and spread gate.

### P5 — Deploy Script Migration Timeout
`deploy.sh` runs `run_migrations.py` which uses PgBouncer. When pool is exhausted, migration fails and entire deploy rolls back. Should bypass PgBouncer (use unix socket) or check-and-skip if all migrations already applied.

---

## 8. CRITICAL TRAPS — DO NOT BREAK

1. **`_market_meta_cache` is a 3-tuple `(cat, ttr, expiry_monotonic)`** — never expand to 4-tuple without reading every caller
2. **`side="YES"/"NO"` only** — never BUY/SELL in `place_order()`
3. **`trade_events` is P&L authority** — never read `paper_trades` for P&L
4. **`_eq_n` must be initialized to 0 before reliability tracker try-block** — Baker-McHale references it unconditionally
5. **MIRROR_NO_SIDE_DAMPENER < 1.0 required** to activate dynamic NO dampener — if set to 1.0, entire dampener block is bypassed
6. **VPS `.env` overrides code defaults** — always check `/opt/pa2-shared/.env` before assuming a config value
7. **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — both must pass; `risk_manager.calculate_position_size()` is DEPRECATED
8. **`asyncpg JSONB`:** use `CAST(:x AS jsonb)` not `:x::jsonb`
9. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
10. **CLOB volume=0 always** — never use volume gates for MirrorBot (CLOB data is unreliable)

---

## 9. VPS OPERATIONS

```bash
# SSH
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# Deploy (normal)
cd C:/lockes-picks/polymarket-ai-v2
bash deploy/deploy.sh

# Deploy when PgBouncer exhausted (workaround)
# 1. Build + upload tar manually
# 2. On VPS: DATABASE_URL=postgresql://polymarket:polymarket_s46@/polymarket?host=/var/run/postgresql \
#    sudo -u polymarket /opt/pa2-shared/venv/bin/python scripts/run_migrations.py
# 3. Symlink swap + restart

# Monitor MirrorBot
sudo journalctl -u polymarket-ai -f | grep -E 'mirror_|MirrorBot|RTDS|rtds'

# Check .env overrides
cat /opt/pa2-shared/.env | grep -E 'MIRROR_|WATCHLIST|TOP_TRADER'

# Rollback
sudo ln -sfn /opt/pa2-releases/<previous_timestamp> /opt/polymarket-ai-v2
sudo systemctl restart polymarket-ai

# P&L
python scripts/bot_pnl.py MirrorBot 168
```

---

## 10. WHAT S142 LOGS LOOK LIKE (verify these are firing)

```
mirror_low_confidence     confidence=0.507 min_required=0.55   ← Phase 1 gate ✅
watchlist_refresh         total_fetched=300 watchlist_size=300  ← Phase 1 watchlist ✅
mirror_bm_shrinkage       n=50 edge=0.057 k=0.42               ← Phase 3 BM active ✅
mirror_no_dynamic_dampened no_price=0.35 dampener=0.30         ← Phase 2 NO dampener ✅
mirror_no_dynamic_blocked  no_price=0.08 reason=sub-10c        ← Phase 2 block ✅
mirror_multifactor        base=0.5xx final=0.5xx               ← Confidence formula ✅
```

Currently seeing `mirror_low_confidence` and `mirror_small_whale_skip` — bot is scanning and filtering correctly. No `mirror_entry` seen yet (high-confidence qualifying trades haven't appeared since restart).

---

## 11. FULL P&L HISTORY (context for decisions)

| Session | Event | P&L |
|---------|-------|-----|
| S77 | First honest audit | +$230 (7W/7L) |
| S81 | RTDS live | +$230 base |
| S86 | Dedup fix (+3238 fake RESOLUTION events removed) | **+$15,051** corrected |
| S87 | EsportsBot dedup fix | separate |
| S116 | Bot dead (294 junk positions, calibration broken) | N/A |
| S117 | Bot revived | N/A |
| S120 | "+$26,986" P&L claimed | **FALSE** (pre-dedup data) |
| S132 | Corrected: real P&L -$159K | **ALSO FALSE** (different pre-dedup artifact) |
| S132 | After kills: contrarian boost, $50 whale gate, rel_mult cap, NO dampener | Ongoing |
| S142 | Current | **+$15,051 baseline** + trades since S86 |

The $15,051 is the last verified ground-truth. All claims before S86 were contaminated by duplicate RESOLUTION events.

---

## 12. BANKROLL CONFIG

```python
# BotBankrollManager for MirrorBot
capital       = 20000.0   # USD
kelly_fraction = 0.25     # quarter-Kelly
max_bet_usd   = 300.0     # hard cap per trade
max_daily_usd = 5000.0    # daily cap
```

Kelly formula: `f* = max(0, (p*b - q) / b)` where `b = (1-price)/price`.
When `confidence <= price` → returns 0.0 (no edge → no trade).
Calibration quality scaling: `kelly × min(1.0, calibration_quality / 0.6)` — currently no calibration running so quality=1.0.

---

## 13. TRADE GATE DETAIL — 28 GATES IN ORDER

See `bots/mirror_bot.py::_execute_mirror_trade()` starting ~line 1380.

Key gates and their current values:
| Gate | Config | Current Value |
|------|--------|---------------|
| Crypto blocklist | MIRROR_CATEGORY_BLOCKLIST | crypto,15-minute,speed,finance |
| Market cooldown | MIRROR_MARKET_COOLDOWN_SECONDS | 1800s |
| Min whale trade | MIRROR_MIN_WHALE_TRADE_USD | $50 |
| Spread gate | MIRROR_MAX_SPREAD | **0.08** (S142) |
| Volume gate | MIRROR_MIN_MARKET_VOLUME_24H | $5,000 24h |
| Slippage | MIRROR_MAX_SLIPPAGE_PCT | **0.05** (S142) |
| TTR gate | hardcoded | >4h |
| Sports NO block | hardcoded | side=NO + sports category |
| LR gate | elite_reliability | LR < 1.0 → reject |
| Min confidence | MIRROR_MIN_CONFIDENCE | **0.55** (S142) |
| Dust gate | MIRROR_MIN_TRADE_USD | $50 |

---

## 14. ADAPTIVE SAFETY DETAIL (Phase 4 incomplete)

File: `bots/mirror_adaptive_safety.py`

```python
mult = exp(-8 * drawdown_pct)
# drawdown_pct = max(0, (peak_pnl - current_pnl) / capital)
# mult=1.0 when flat, mult≈0.45 at 10% drawdown, mult≈0.20 at 20% drawdown
```

Uses:
1. `get_adjusted_daily_cap_mult()` → scales `_max_daily_usd` in sizing loop
2. `get_adjusted_size_mult()` → additional size multiplier in exit logic

Currently disabled via `MIRROR_ADAPTIVE_SAFETY=false` in `.env`. Code is correct (BUG-14 fixed in S137). Safe to re-enable.

---

## 15. WHAT TO DO NEXT SESSION

**Priority order:**
1. **Fix P1 — `current_price` on startup restore** (financial risk, stop-loss accuracy)
2. **Complete Phase 4 — re-enable MIRROR_ADAPTIVE_SAFETY** (one-line .env change)
3. **Monitor S142 impact** — wait 24-48h, check `mirror_bm_shrinkage` frequency, NO dampener hit rate, spread rejections. Compare trade count vs S141 baseline.
4. **Fix deploy script** — migration step should use unix socket or pre-check applied migrations before attempting PgBouncer connection
5. **Add missing test** for BM-rejection-despite-tight-spread scenario

**Context for P1 fix:**
In `mirror_bot.py::_restore_state_on_startup()`, after the loop that populates `_open_positions`, add:
```python
# Restore current_price from positions table
async with self.engine.db.get_session() as session:
    result = await session.execute(
        text("SELECT market_id, token_id, current_price FROM positions WHERE status='open' AND bot_name='MirrorBot'")
    )
    for row in result:
        key = f"{row.market_id}:{row.token_id}"
        if key in self._open_positions and row.current_price:
            self._open_positions[key]['current_price'] = float(row.current_price)
```

---

## 16. SESSION RULES (inherit from CLAUDE.md)

- **Mirror only session** — do not touch WeatherBot/EsportsBot unless explicitly requested
- **One fix per commit** — never bundle unrelated changes
- **Read file before editing** — always
- **Grep dependents before changing any shared function signature**
- **Git snapshot before any edit** — `git stash` or pre-fix commit
- **No silent behavior changes** — if a function behavior changes, state: "this changes from X to Y, callers affected: [list]"
- **Paper trading = production** — never skip a feature "because we're paper trading"
- **Test mandate:** `pytest tests/` must pass (currently 1723 passed, 0 failed) before any commit

---

## 17. AUDIT SELF-VALIDATION RULE

All audit findings must be self-validated before reporting:
1. Re-read the code path claimed to have the bug
2. Trace execution with realistic inputs
3. Check if existing tests already cover it
4. Rate confidence (HIGH/MEDIUM/LOW)
5. Remove false positives before presenting to user

See `memory/feedback_audit_self_validation.md`.

---

## 18. SCOPE LOCK RULE

Never add unsolicited features. Only fix what the handoff or user explicitly requests. "I noticed while I was in here..." is forbidden. File an observation in the handoff doc and move on.

See `memory/feedback_scope_lock.md`.

---

## 19. UNIFIED AUDIT SYSTEM (deployed 2026-03-29, commits fd1afd5 + ba8e5b3)

A system-wide data integrity audit covering all 36+ DB tables is now live. Every MirrorBot session must be aware of it.

### What changed in shared infrastructure
| File | Change | Impact on MirrorBot |
|------|--------|---------------------|
| `base_engine/data/database.py` | Added `AuditRun` ORM class; added `audit_run_id` + `violation_hash` columns to `ReconciliationBreak` | None — additive only, no existing columns removed |
| `base_engine/monitoring/health_scheduler.py` | Added `daily_audit` job at 86400s | Runs once/day; uses READ COMMITTED + 30s timeout per check so never blocks MirrorBot trades |
| `base_engine/data/trade_event_audit.py` | After existing checks, runs 3-check mini-audit (SizeInvariant, Orphan, TemporalOrder) and persists violations to `reconciliation_breaks` | Fires every 30min resolution backfill. Non-fatal (wrapped in try/except). Adds ~3 SQL queries only when violations exist. |
| `schema/migrations/062_audit_runs.sql` | New `audit_runs` table; `violation_hash` + `audit_run_id` on `reconciliation_breaks` | No schema change to tables MirrorBot writes to directly |

### CLI commands for every MirrorBot session
```bash
# Run at session start to get current violation baseline
python scripts/run_audit.py --bot MirrorBot --json

# Check open violations from previous runs
python scripts/run_audit.py --list-open

# Acknowledge a resolved violation
python scripts/run_audit.py --ack <break_id> --reason "resolved in S143"
```

### What audit checks are most relevant to MirrorBot
- `size_invariant` — catches EXIT+RESOLUTION > ENTRY (P&L integrity)
- `temporal_order` — EXIT before ENTRY (ordering bugs)
- `orphan_resolution` — RESOLUTION with no ENTRY (ghost P&L)
- `position_size_mismatch` — positions table vs trade_events net (phantom positions)
- `stale_open_position` — positions open on resolved markets (resolution backfill gap)
- `shadow_fill_mismatch` — trade_executed=TRUE but no ENTRY event (CRITICAL money leak)
- `paper_trade_mismatch` — paper_trades P&L vs trade_events P&L discrepancy > $0.10

### First automated run
First daily_audit fires ~24h post-deploy (~2026-03-30 21:35 UTC). Results persist to `reconciliation_breaks` and alert via AlertingSystem if CRITICAL violations found.

### Full audit system docs
`AGENT_HANDOFF_AUDIT_SYSTEM_2026_03_29.md` — complete architecture, all 21 checks, known issues.
