# AGENT HANDOFF — MirrorBot Session 131 (Carbon Copy)
## SCOPE: MirrorBot ONLY — no bleed to Weather/Esports unless explicit demand
## SESSION TYPE: Continuation of S130 — ML shadow race evaluation, P&L diagnosis, confidence tuning

---

## HOW TO USE THIS FILE

You are a new agent continuing MirrorBot development. This is a CARBON COPY of everything the prior agent knew. Read this file completely before doing anything.

**Read order:**
1. THIS FILE (you're reading it) — full context, vision, plans, learnings
2. `CLAUDE.md` — Prime directive, rules of engagement, critical traps (MANDATORY)
3. `memory/MEMORY.md` — Cross-session memory index

**Do NOT read/modify other bot files** (weather_bot.py, esports_bot.py) unless explicitly asked.

---

## SYSTEM OVERVIEW

This is a **15-bot Polymarket automated trading system**. Real capital is NOT at risk (paper trading, `SIMULATION_MODE=true`) but paper trading IS treated as production per CLAUDE.md. The ONLY difference between paper and live is whether the final order submission sends to the CLOB or logs to the paper trade table.

- **VPS**: Ubuntu at `34.251.224.21`, SSH key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **DB**: PostgreSQL, localhost, user=polymarket, db=polymarket
- **Python**: 3.13 (CRITICAL — scoping traps, see traps section)
- **Service**: `sudo systemctl restart polymarket-ai` (PID 3356641 as of S130)
- **Deploy**: `cd /c/lockes-picks/polymarket-ai-v2 && KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh`
- **Rollback**: Same with `rollback.sh`
- **VPS paths**: `/opt/polymarket-ai-v2` → symlink to latest in `/opt/pa2-releases/`. Shared: `/opt/pa2-shared/{data,saved_models,venv}`
- **Running scripts on VPS**: SCP to `/tmp`, then `ssh ... "cd /opt/polymarket-ai-v2 && source venv/bin/activate && PYTHONPATH=/opt/polymarket-ai-v2 python /tmp/script.py"`. The deploy dir does NOT have a `scripts/` folder.

### Active Bots
| Bot | Status | Notes |
|-----|--------|-------|
| MirrorBot | Active (RTDS live) | Copies elite traders via real-time WebSocket feed |
| WeatherBot | Active | Weather prediction markets, 35 cities |
| EsportsBot | Active | CS2/LoL/Valorant match betting via PandaScore |
| EsportsLiveBot | Active | Live in-play esports |
| EsportsSeriesBot | Active | Series-level esports |
| 9 others | Disabled | BOT_ENABLED_* flags |

---

## WHAT MIRRORBOT IS

MirrorBot copies elite traders in real-time via RTDS (Real-Time Data Stream) WebSocket feed. It:
1. Maintains a 500-trader watchlist of elite Polymarket traders
2. Receives real-time trade events via WebSocket
3. Evaluates each trade with a multi-factor confidence formula
4. Scores each trade with ML models (XGBoost + Q-Learning + Combo) in SHADOW mode
5. Executes paper trades with size from BotBankrollManager (Kelly criterion)
6. Tracks positions, P&L, and resolution via shared infrastructure

### Key Files
| File | Purpose |
|------|---------|
| `bots/mirror_bot.py` | MirrorBot main logic — confidence formula, ML scoring, trade execution |
| `base_engine/data/resolution_backfill.py` | Resolution engine — Phase 4b and 4b-alt resolve closed positions |
| `base_engine/data/database.py` | DB layer — insert_trade_event, position management |
| `base_engine/base_engine.py` | Base class for all bots — market index, order routing |
| `base_engine/order_gateway.py` | Order execution — paper/live routing |
| `config/settings.py` | All config — env vars, defaults |
| `scripts/bot_pnl.py` | Canonical P&L script |
| `scripts/mirror_conf_charts.py` | S130 confidence spread + P&L charts (matplotlib) |
| `scripts/ml_shadow_p2.py` | ML shadow race analysis (category, confidence tier, side, price) |

### Confidence Formula (S130 — CURRENT)
Located in `bots/mirror_bot.py`, around the `_execute_mirror_trade()` method:

```
_base = upstream_efficiency  (0.55-0.70 range, from RTDS trader stats)
_price_adj = price-based adjustment (0.01-0.04)
_conv_adj = conviction adjustment (-0.03 to +0.04, based on whale trade size ratio)
_cat_adj = category bonus (0.001-0.01, from category win rate history)

confidence = max(0.35, min(0.75, _base + _price_adj + _conv_adj))
```

S110 broke this with a Bayesian replacement that crushed all scores to >=0.80. S130 restored the upstream efficiency floor. Post-S130 entries show spread 0.550-0.734, median 0.600.

### ML Shadow Race (S124+)
Three ML models score every entry in SHADOW mode (event_data keys):
- `ml_score_xgb` — XGBoost classifier score (0-1)
- `ml_score_ql` — Q-Learning score (negative = bad)
- `ml_score_combo` — Combined score (0-1)

Currently SHADOW ONLY — `MIRROR_USE_ML_SELECTOR=false`. ML scores are logged in event_data but do NOT gate trades. The plan is to evaluate ML scores against resolved P&L once we have enough resolution data, then enable ML gating if it shows predictive value.

**Gating env var**: `MIRROR_USE_ML_SELECTOR=true` + `MIRROR_ML_STRATEGY=xgb|ql|combo`

### Event Data Structure (trade_events.event_data JSONB)
```json
{
  "category": "crypto",
  "source": "rtds",
  "whale_trade_usd": 1500.0,
  "conf_base": 0.553,
  "conf_price_adj": 0.037,
  "conf_conv_adj": 0.0,
  "conf_upstream": 0.553,
  "conf_cal_shadow": null,
  "rel_mult": 0.85,
  "trader": "0x1234abcd",
  "consensus": 3,
  "scan_start_mono": 12345.678,
  "ml_score_xgb": 0.3582,
  "ml_score_ql": -2.0354,
  "ml_score_combo": 0.2369
}
```

**IMPORTANT**: The `confidence` value is stored as a COLUMN in `trade_events`, NOT in `event_data` JSON. To query confidence, use `trade_events.confidence` column, NOT `event_data->>'confidence'`. The event_data has `conf_base`, `conf_price_adj`, `conf_conv_adj`, `conf_upstream` as components.

---

## WHAT WAS DONE IN S130

### FIX 1: Confidence Formula Restored
- **Bug**: S110 Bayesian formula crushed all confidences to >=0.80
- **Fix**: Restored upstream efficiency as floor with additive adjustments
- **Result**: 440 post-S130 entries, spread 0.550-0.734, median 0.600
- **Status**: DEPLOYED, working

### FIX 2: Phase 4b-alt Bad Import (P0)
- **Bug**: `resolution_backfill.py:532` had `__import__("base_engine.config.settings")` → `ModuleNotFoundError` every 30min since S126
- **Fix**: Changed to `__import__("config.settings", fromlist=["settings"])`
- **Impact**: Unblocks resolution of ~1,150+ ML-scored entries (resolution rate was 14%, expected 40-50% post-fix)
- **Status**: DEPLOYED, service restarted (PID 3356641). NEEDS VERIFICATION — check `journalctl -u polymarket-ai --since '30 min ago' | grep '4b-alt'` for success instead of ModuleNotFoundError

### FIX 3: Phase 4b Exit P&L Subtraction (linter)
- Phase 4b now subtracts prior EXIT P&L from RESOLUTION P&L to avoid double-counting
- **Status**: DEPLOYED

### Charts Generated
Three matplotlib charts saved to `/tmp/mirror_*.png` on VPS (also pulled to local Windows temp):
1. `mirror_conf_scatter.png` — Confidence scatter post-S130 (440 entries, 0.55-0.73 spread)
2. `mirror_conf_buckets.png` — Confidence bucket vs WR + P&L (7-day, all buckets P&L-negative)
3. `mirror_cat_pnl.png` — Category P&L (crypto = -$79K, esports = +$7K)

---

## CURRENT P&L DATA (as of S130, 7-day lookback)

### Overall
| Metric | Value |
|--------|-------|
| Total resolved (7d) | 1,777 |
| Overall WR | 39.7% |
| Total P&L (7d) | **-$92,811** |
| Avg P&L/trade | -$53.16 |

### By Category (worst first)
| Category | n | WR% | P&L | AvgPnL |
|----------|---|-----|-----|--------|
| Crypto | 681 | 38.6% | -$78,292 | -$114.97 |
| Sports | 506 | 40.9% | -$14,448 | -$28.61 |
| Finance | 12 | 41.7% | -$1,469 | -$122.42 |
| Politics | 10 | 50.0% | -$1,465 | -$146.47 |
| Geopolitical | 1 | 0.0% | -$443 | -$442.55 |
| Unknown | 395 | 41.0% | +$64 | +$0.16 |
| Esports | 172 | 37.2% | +$3,241 | +$22.51 |

**CRYPTO IS 88% OF ALL LOSSES.**

### By Side
| Side | n | WR% | P&L |
|------|---|-----|-----|
| NO | 1,016 | 39.1% | -$81,739 |
| YES | 761 | 40.6% | -$11,072 |

**NO side bleeds 7.4x more than YES.**

### By Confidence Bucket (7-day, ALL pre-S130 broken formula)
ALL 1,777 entries were in the >=0.80 bucket because the Bayesian formula crushed everything. This data is NOT useful for evaluating the new S130 formula — need to wait for post-S130 entries to resolve.

### Post-S130 Confidence Scatter (440 entries, NOT YET RESOLVED)
- Min: 0.550, Max: 0.734, Median: 0.600
- Clean distribution, no more bimodal collapse
- These entries need to RESOLVE before we can evaluate WR by confidence bucket

---

## ML SHADOW RACE STATUS

- **1,335 entries** scored with all 3 ML models
- **182 resolved** (14%) — was blocked by 4b-alt import bug since S126
- Expected resolution rate post-fix: **40-50% within 24h** as 4b-alt catches up
- **CANNOT evaluate ML effectiveness until resolution rate climbs** — need 500+ resolved with ML scores

### What to Do When Data Arrives
Once resolution rate hits 40%+, run this analysis:
1. **XGB score vs realized P&L** — does higher ml_score_xgb correlate with positive P&L?
2. **QL score vs realized P&L** — does less-negative ml_score_ql correlate with wins?
3. **Combo threshold** — what ml_score_combo cutoff maximizes P&L?
4. **Category-stratified** — does ML work differently for crypto vs sports vs esports?

If ML shows predictive value → enable gating: `MIRROR_USE_ML_SELECTOR=true`

---

## VERIFIED BUG AUDIT (from S128-S129 comprehensive audit)

### S130 Fixed
- **MB-1 (P0)**: Phase 4b-alt import path → FIXED

### Deferred to Other Bot Sessions
- **SE-1**: Signal ingestion dead method name → shared infra
- **SE-2**: Signal type enum mismatch → shared infra
- **SE-3**: Signal handler kwarg name → shared infra
- **EB-1**: EsportsBot config overlap → EsportsBot session
- **EB-2**: Learning engine structural key eviction → shared infra
- **EB-3**: Kill switch fail-safe default → shared infra
- **EB-4**: Health scheduler metadata query → shared infra
- **EB-5**: Alerting UTC date → shared infra

### FALSE POSITIVE
- **SE-4**: Phase 4b partial exit double-counting → FALSE POSITIVE. Phase 4b WHERE clause `pt.side IN ('YES', 'NO')` already excludes SELL trades.

---

## NEXT STEPS (S131 PRIORITIES)

### P0: Verify 4b-alt Success
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo journalctl -u polymarket-ai --since '30 min ago' | grep '4b-alt'"
```
Should show success message, not ModuleNotFoundError. If still failing, the service may need another restart or the deploy didn't propagate correctly.

### P1: Monitor Resolution Rate
Track resolved count climbing from 182. Target: 600+ within 24h.
```sql
SELECT COUNT(*) FROM trade_events
WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION';
```

### P2: Evaluate ML Shadow Race (BLOCKED on resolution data)
Once 500+ ML-scored entries resolve, analyze:
- XGB/QL/Combo scores vs realized P&L
- Category-stratified effectiveness
- Optimal gating thresholds

### P3: Crypto Category Diagnosis
88% of losses. Options:
1. Category-specific confidence floor (e.g., crypto requires 0.65+)
2. Reduced position sizing for crypto
3. Category exclusion (nuclear option)
4. ML gating (if ML shows predictive value for crypto specifically)

### P4: NO Side Asymmetry
NO side loses -$81,739 vs YES -$11,072 (7.4x worse). Investigate:
- Are NO tokens systematically overpriced?
- Is the fee structure asymmetric for NO?
- Should we apply a NO-side penalty to confidence?

### P5: S130 Confidence Calibration
Monitor post-S130 entries as they resolve. Key question: does the new 0.55-0.73 spread produce WR that correlates with confidence? If 0.70+ entries have higher WR than 0.55 entries, the formula is working.

---

## KEY OPERATIONAL KNOWLEDGE

### SSH/SCP to VPS
```bash
# SSH
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o StrictHostKeyChecking=no -o ConnectTimeout=10 ubuntu@34.251.224.21 "COMMAND"

# SCP file TO VPS
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o StrictHostKeyChecking=no LOCAL_FILE ubuntu@34.251.224.21:/tmp/REMOTE_FILE

# SCP file FROM VPS
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o StrictHostKeyChecking=no ubuntu@34.251.224.21:/tmp/REMOTE_FILE LOCAL_PATH

# Run Python script on VPS
ssh ... "cd /opt/polymarket-ai-v2 && source venv/bin/activate && PYTHONPATH=/opt/polymarket-ai-v2 python /tmp/script.py 2>&1"
```

**GOTCHA**: Multi-line Python in SSH commands breaks due to quoting. Always SCP a .py file to `/tmp/` then run it.

### Running Analysis Scripts
Scripts live locally in `scripts/`. They use `from base_engine.data.database import Database` and need PYTHONPATH set. Process:
1. Write/edit script locally
2. SCP to VPS `/tmp/`
3. Run with PYTHONPATH from `/opt/polymarket-ai-v2`
4. SCP results (PNGs, CSVs) back

### Logs
```bash
# Live tail
ssh ... "sudo journalctl -u polymarket-ai -f"

# Last 30 min, filter for MirrorBot
ssh ... "sudo journalctl -u polymarket-ai --since '30 min ago' | grep -i mirror | tail -50"

# Check specific component
ssh ... "sudo journalctl -u polymarket-ai --since '30 min ago' | grep '4b-alt'"
```

### Service Management
```bash
# Restart
ssh ... "sudo systemctl restart polymarket-ai"

# Status
ssh ... "systemctl show polymarket-ai --property=MainPID,ActiveState"

# Check PID
ssh ... "ps aux | grep main.py | grep -v grep"
```

---

## CRITICAL TRAPS (WILL BITE YOU)

1. **trade_events is P&L AUTHORITY** — NEVER read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL"
3. **confidence is a COLUMN**, not in event_data JSON. Use `trade_events.confidence`, NOT `event_data->>'confidence'`
4. **Python 3.13 scoping**: `from X import Y` inside a function shadows Y for the ENTIRE function. NEVER use local imports that shadow top-level names
5. **VPS deploy path**: `/opt/polymarket-ai-v2` is a symlink. Scripts dir doesn't exist in deploy. SCP to `/tmp/`
6. **asyncpg JSONB**: Use `CAST(:x AS jsonb)` NOT `:x::jsonb`
7. **positions table**: NO `bot_name` column (use `source_bot`), NO `closed_at`, NO `updated_at`
8. **paper_trades**: NO `metadata` JSONB column, NO `resolved_pnl` column (it's `resolved_at`)
9. **trade_events immutability trigger**: Must `DISABLE TRIGGER` then re-enable for data cleanup
10. **RESOLUTION dedup**: ON CONFLICT broken on partitioned tables. Uses INSERT...WHERE NOT EXISTS
11. **MirrorBot entry price**: Uses CURRENT market price, NOT trader's historical fill price
12. **_market_meta_cache**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand
13. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
14. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time
15. **PatchDriftDetector**: Only set `_patch_timestamps` when `old is not None` (genuine patch change)

---

## USER PREFERENCES & FEEDBACK

1. **Scope lock**: NEVER add unsolicited features. Only fix what the handoff or user explicitly requests.
2. **Show data, not code**: User wants to see analysis results (tables, charts), not code snippets, unless asked
3. **Bot sessions are isolated**: MirrorBot session = MirrorBot only. No touching Weather/Esports unless explicitly demanded
4. **Be direct**: User is technical, direct, sometimes blunt. Don't sugarcoat, don't hedge, don't pad. If data shows bad results, say so plainly.
5. **Don't make up root causes**: If you don't know why something is broken, say so. Don't guess and present it as fact. User has explicitly called this out.
6. **Deploy means deploy**: When user says "deploy", run deploy.sh. Don't ask for confirmation or explain what deploy does.
7. **Short responses**: User prefers concise output. Tables over paragraphs. Don't summarize what you just did at the end.

---

## P&L MATH (MANDATORY)

- **NEVER invert formulas for NO positions** — prices are token-specific
- `cost = entry_price * size` (ALL sides)
- `uPnL = (current - entry) * size` (ALL sides)
- Canonical script: `python scripts/bot_pnl.py BotName hours`
- Data sources: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)

---

## KEY CONFIG (live VPS values)
```
ALL BOTS:    capital=$20000, max_bet=$300, max_daily=$10000
MirrorBot:   kelly=0.25
MIRROR_MIN_CONFIDENCE=0.45
MIRROR_MIN_RELIABILITY=0.52
MIRROR_USE_CALIBRATION=true (should be false — calibration was disabled but env var not updated)
MIRROR_MAX_POSITIONS=200
MIRROR_MAX_PER_MARKET=400
MIRROR_USE_ML_SELECTOR=false (shadow mode only)
WATCHLIST_ENABLED=true, WATCHLIST_SIZE=1000
SIMULATION_MODE=true (paper trading)
TAKER_FEE_BPS=150
```

---

## STATE PERSISTENCE (all gaps closed)
| State | Mechanism |
|-------|-----------|
| `_daily_exposure_usd` | `daily_counters` 60s flush + SIGTERM + startup restore |
| `_daily_exposure` (MirrorBot) | `_restore_state_on_startup()` paper_trades SUM |
| `_open_positions` (MirrorBot) | `positions` table, clears on restart, re-enters by EOD UTC |
| Open positions (all bots) | `order_gateway.seed_positions_from_db()` |
| Canary stage | `system_kv` table key='canary_stage' |

---

## RESOLUTION BACKFILL ARCHITECTURE

Resolution backfill runs every ~30 min (mini cycle) + daily full cycle. Key phases:

- **Phase 2a**: Query Polymarket API for market resolution status
- **Phase 4b**: Join `paper_trades` → `trade_events` for resolved markets, emit RESOLUTION events. Subtracts EXIT P&L to avoid double-counting. Excludes SELL trades.
- **Phase 4b-alt**: Position-based resolution for positions where `paper_trades.market_id` differs from `positions.market_id` (condition_id vs Gamma ID mismatch). Queries positions table directly. **FIXED in S130** — was broken since S126 due to bad import path.

---

## CHART SCRIPTS (for future analysis)

### mirror_conf_charts.py
Generates 3 matplotlib charts:
1. Confidence scatter (post-S130 entries)
2. Confidence bucket vs WR + P&L (7-day resolved)
3. Category P&L (7-day resolved)

**Key SQL patterns** (uses COALESCE to compute confidence from components when column is NULL):
```sql
COALESCE(confidence,
  (event_data->>'conf_base')::float
  + COALESCE((event_data->>'conf_cat_adj')::float, 0)
  + COALESCE((event_data->>'conf_price_adj')::float, 0)
  + COALESCE((event_data->>'conf_conv_adj')::float, 0)
) as conf
```

### ml_shadow_p2.py
Analysis script for ML shadow race: category P&L, confidence tier, price bucket, side split, current state. Note: positions table uses `source_bot` not `bot_name`.

---

## FULL SESSION HISTORY (MirrorBot)

| Session | Date | Key Changes |
|---------|------|-------------|
| S77 | Mar 11 | Phantom dedup, stale entry pricing fix, SELL overwrite fix |
| S79 | Mar 12 | Selectivity tightening: MIN_CONFIDENCE 0.10→0.55, ELITE_MIN_TRADES 5→100 |
| S81 | Mar 12 | RTDS live, 1000 watchlist, DB persistence fix |
| S85 | Mar 14 | Resolution backfill fix (3 root causes), P&L data overhaul, trade_events authority |
| S86 | Mar 14 | Ingestion sync fix, RESOLUTION dedup (3238 deleted) |
| S87 | Mar 14 | Atomic INSERT...SELECT for RESOLUTION dedup on partitioned tables |
| S93 | Mar 15 | Conformal dampening fix, Kelly 0.0625→0.25, P&L +$4.5k |
| S94 | Mar 16 | Latency 2967ms→11.9ms, lock-free DB, RTDS fast-path |
| S100 | Mar 17 | L2 book walk, whale trade log, category cap $40K |
| S109 | Mar 19 | 5 root-cause P&L fixes, cap 200→400 |
| S110-111 | Mar 20 | Multi-factor confidence formula (S110 Bayesian — BROKEN) |
| S116-117 | Mar 22 | Bot dead/revived: flood purge, calibration disabled |
| S119 | Mar 23 | 5 bug fixes, dead code purge, P&L corrected to +$26,986 |
| S120 | Mar 23 | Production readiness: fee 150bps, fill confirmation, canary |
| S124 | Mar 23 | ML selector introduced (shadow mode) |
| S126 | Mar 24 | Phase 4b-alt introduced (BAD IMPORT — broken until S130) |
| S129 | Mar 25 | 21 shared infra audit fixes, P&L corrections |
| **S130** | **Mar 25** | **Confidence formula fixed, 4b-alt import fixed, charts generated** |

---

## VISION / STRATEGIC DIRECTION

The MirrorBot roadmap (in priority order):

1. **Get resolution data flowing** (S130 fix unblocks this) → 4b-alt now resolves condition_id mismatches
2. **Evaluate ML shadow race** → Do XGB/QL/Combo scores predict realized P&L? Need 500+ resolved entries
3. **Enable ML gating if effective** → `MIRROR_USE_ML_SELECTOR=true` with optimal strategy and threshold
4. **Category-level controls** → Crypto is 88% of losses. May need per-category confidence floors or exclusion
5. **Side asymmetry fix** → NO side loses 7.4x more than YES. Investigate systematic overpricing
6. **Confidence calibration** → Monitor S130 formula vs resolved outcomes. Does higher confidence = higher WR?
7. **Go live** → Once paper P&L is consistently positive, flip `SIMULATION_MODE=false`

The fundamental thesis: MirrorBot copies elite traders, but not all copies are equal. The confidence formula + ML scoring should filter out bad copies (wrong category, wrong price range, wrong conviction level) while keeping good ones.

---

## FILES MODIFIED IN S130 (staged, not committed)

```
M base_engine/data/resolution_backfill.py   # Line 532: config.settings import fix
                                              # Lines 455-460, 484-489: exit_pnl subtraction
```

These changes are on VPS (deployed) but may not be committed to git. Check with `git diff base_engine/data/resolution_backfill.py`.

### Untracked Files Created in S130
```
scripts/mirror_conf_charts.py   # Confidence + P&L charts (matplotlib)
scripts/ml_shadow_p2.py         # ML shadow race analysis
AGENT_HANDOFF_MIRRORBOT_SESSION130_2026_03_25.md
```
