# Session 96 — EsportsBot: Trade Sizing Root Cause + Glicko Model Crisis
# COMPLETE AGENT HANDOFF — Carbon Copy for Seamless Continuation

**Date**: 2026-03-16
**Bot scope**: EsportsBot ONLY — no other bot modifications
**Operator**: Sam Watson
**Deploy on VPS**: `20260316_114035` (latest)
**Git HEAD**: `8a36e98` — `fix(esports): S96 kill 0.50 fallback + conformal sizing fix + market-price-as-prior`
**Uncommitted esports changes**: NO — all S96 esports changes committed in `8a36e98`
**Prior sessions**: S94 (latency), S89 (features + audit), S88 (observation mode), S87 (resolution dedup), S85 (data overhaul)

---

## SESSION NARRATIVE (what happened, in order)

### Phase 1: Picked up S94 Part 3 handoff
- Read `AGENT_HANDOFF_ESPORTS_SESSION94_2026_03_15.md` and continued WS-primary trading implementation
- Completed Part 3 code: WS guards, `_trade_lock` coverage, `_ws_trading_active`, scan-as-cache-warmer

### Phase 2: Correctness audit of all latency changes
- User requested thorough review ensuring speed optimizations don't cause wrong trades
- **5 findings implemented**:
  1. `_trade_lock` missing on 3 callers (series scan ×2, series WS ×1) — FIXED
  2. Trade counter lying (`_analyze_one` reported trades=1 even when sizing failed) — FIXED: `_execute_esports_trade()` now returns `bool`
  3. `ws_trading` field added to scan summary log
  4. All 5 WS guards verified matching scan path
  5. WS health check verified (30s stale threshold)

### Phase 3: Deployed + discovered ZERO TRADES executing
- Deploy `35789a5` (`20260315_235531`) — committed as `feat(esports): S94 WS-primary trading + trade counter fix + _trade_lock coverage`
- Live VPS showed: `trades=0` every single scan despite `opportunities=1` per scan
- The trade counter fix REVEALED the truth: trades had been silently failing for weeks, masked by the old counter that always said `trades=1`

### Phase 4: ROOT CAUSE FOUND — Conformal confidence killer
- **Root cause**: `_execute_esports_trade()` lines 2433-2454 called `self._conformal_predictor.conservative_prob()` which crushed confidence from ~0.52 to **0.0225** (pushed toward 0.50, then used as confidence)
- This made edge = 0.0225 - 0.385 = **-36%** → bankroll manager returned size=0 → trade silently killed
- **Fix**: Removed the conformal confidence override block entirely. Bankroll manager already handles conformal via width-based dampening (S91 fix). This was double-dipping.
- **Also fixed**: `size < 1.0` threshold → `size < 0.10` (post-multiplier sizes were getting killed)
- **Also added**: Diagnostic log `esportsbot_sizing_killed_at_bankroll` at `size <= 0` return point
- Deploy `20260316_001430` — FIRST REAL TRADE IN WEEKS: `confidence=0.524, price=0.385, size=$100, trades=1`

### Phase 5: Overnight monitoring
- 8h overnight: 2,757 scans, 2 trades executed, 3 opportunities found
- System stable: ~10.4s cycles, zero crashes
- **Found and fixed**: `_background_form_prefetch()` crash — `_live_matches` values were LiveMatch objects not dicts. Added `isinstance()` checks. Deploy `20260316_094236`

### Phase 6: CRITICAL DISCOVERY — Glicko model outputting ~0.50 for everything
- User flagged 3 opportunities in 8 hours as "incredibly low"
- **Waterfall analysis** (per scan of ~21 markets):
  - `halted=2` (Valorant, 0% accuracy)
  - `no_prediction=5` (can't match team names — mostly SC2/minor teams)
  - `edge_cap=7` → later 3 (model disagrees with market >25%)
  - `low_confidence=6` → later 8 (model_prob too close to 0.50)
  - `low_edge=2` (edge < 5%, actually good filtering)
  - **opportunities=0**

- **Root cause**: The Glicko-2 model outputs ~0.4961-0.5039 for nearly everything because:
  1. Many team pairs have **default ratings** (phi > 200) — Glicko-2 expected score ≈ 0.50
  2. LoL markets (10/21, largest segment) go through the LoL ML model path which does NOT use `_get_glicko2_prediction()` — they go through `predict_with_glicko2()` which builds `glicko2_est = 0.5 + team_strength_diff` and TSD ≈ 0 for similar teams
  3. The Bayesian prior blend (up to 80% weight for uncertain teams) was blending toward 0.50 (neutral prior) — even aggressive blending can't help when the raw output is already ~0.50

### Phase 7: Attempted fixes (partially deployed, partially broken)

#### Fix A: Killed 0.50 fallback for unknown teams (DEPLOYED)
- `_get_glicko2_prediction()` used to return `0.50` as fallback when teams couldn't be matched — creating huge fake edges (0.50 vs market price 0.85 = 35% "edge")
- **Fix**: Return `None` instead → waterfall counts as `no_prediction` (honest)
- This shifted edge_cap rejections to no_prediction (correct behavior)

#### Fix B: Market-price-as-prior Bayesian blend (DEPLOYED, PARTIALLY EFFECTIVE)
- Changed `_get_glicko2_prediction()` signature to accept `market_price` parameter
- Bayesian prior blend now uses `market_price` instead of `0.50`:
  ```python
  _prior = max(0.05, min(0.95, market_price))
  prob = prior_weight * _prior + (1.0 - prior_weight) * prob
  ```
- Updated all 3 callers (dota2, valorant, fallback path) to pass `price`
- Also updated roster stability nudge to use `market_price` instead of 0.50
- **Problem**: This only affects markets going through `_get_glicko2_prediction()` (dota2, valorant, non-live fallback). The LoL ML model path (10/21 markets) is UNAFFECTED.

#### Fix C: Config threshold changes (DEPLOYED to .env, NOT TAKING EFFECT)
- Changed VPS `.env`:
  - `ESPORTS_MIN_CONFIDENCE=0.52` → `0.50`
  - `ESPORTS_MAX_EDGE=0.25` → `0.40`
- **MAX_EDGE took effect** — scan summary shows `max_edge=0.4`, edge_cap dropped from 7→3
- **MIN_CONFIDENCE DID NOT TAKE EFFECT** — scan summary still shows `min_confidence=0.52`
- Possible cause: The deploy creates a symlink to a release directory. The `.env` at `/opt/polymarket-ai-v2/.env` has the updated value (`0.50`), but the service may be reading a different `.env` or the settings module has a caching issue. NEEDS INVESTIGATION.

### Phase 8: Esports changes committed
- Committed `8a36e98` — `fix(esports): S96 kill 0.50 fallback + conformal sizing fix + market-price-as-prior`
- Only `bots/esports_bot.py` committed. All other uncommitted files left untouched.

### Phase 9: REAL ROOT CAUSE FOUND — `_inject_glicko2_metadata()` key mismatch
- User asked "why are we putting .5 for most teams thats the root issue?"
- Investigated Glicko2 ratings DB directly via asyncpg:
  - **Ratings ARE differentiated**: LoL has 248 teams (mu 1499-1705, phi 65-260), CS2 has 395 teams (mu 1480-1714, phi 63-290)
  - **Training data is substantial**: CS2=4145 matches, Dota2=2454, LoL=1953, Valorant=182
  - Glicko2 math works: mu=1700 vs mu=1300 with phi=150 gives `expected_score=0.889` (not 0.50!)

- **THE BUG**: `_inject_glicko2_metadata()` (line 1848-1876) looks up team ratings by **PandaScore numeric opponent ID** (e.g., "128947"):
  ```python
  team_a_id = str(opponents[0].get("opponent", {}).get("id", ""))  # → "128947"
  rating_a = tracker.get_rating(team_a_id)  # looks up "128947" → NOT FOUND → DEFAULT (1500/350)
  ```
  But `glicko2_ratings` table keys are **lowercased team names** (e.g., "bilibili gaming"):
  ```
  Sample keys: "3bl esports", "bilibili gaming", "anyone's legend", "jd gaming"
  ```
  **Every single lookup returns default rating (mu=1500, phi=350)** because numeric IDs never match name keys.

- **Result**: `game_state["team_strength_diff"]` is always ~0 → `glicko2_est = 0.5 + 0 = 0.5` → LoL ML model anchors on 0.50 → `model_prob ≈ 0.50` for ALL LoL markets.

- **This affects ALL LoL live markets** (10/21 = 48% of tradeable markets). The LoL ML model path uses `_inject_glicko2_metadata()` → `predict_with_glicko2()`. Since the metadata always has default ratings, the model has zero team-strength signal.

- **The fix is surgical**: In `_inject_glicko2_metadata()`, use `opponents[i].get("opponent", {}).get("name", "").lower()` instead of `.get("id", "")` to look up ratings. The PandaScore opponent data contains both `id` and `name` — we just need to use `name`.

- **Alternatively**: Use `self._team_name_to_id` reverse lookup, or add a `_pandascore_id_to_name` mapping populated during `_init_glicko2_trackers()`.

### Phase 10: User requested final handoff document for new agent

---

## ACTIVE BUGS TO FIX (Priority order)

### P0: ESPORTS_MIN_CONFIDENCE not loading from .env
**Symptom**: VPS `.env` says `ESPORTS_MIN_CONFIDENCE=0.50` but scan summary logs `min_confidence=0.52`
**Impact**: Markets with confidence 0.50-0.52 (several per scan) are rejected unnecessarily
**Likely cause**: Deploy symlink issue or settings module caching
**Investigation needed**:
```bash
# Check if settings module actually sees the env var:
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
sudo -u polymarket python3 -c "
import sys; sys.path.insert(0, '/opt/polymarket-ai-v2')
from config import settings
print(f'MIN_CONFIDENCE={settings.ESPORTS_MIN_CONFIDENCE}')
print(f'MAX_EDGE={settings.ESPORTS_MAX_EDGE}')
"
# Also check if .env is loaded by dotenv or systemd:
cat /etc/systemd/system/polymarket-ai.service | grep -i env
```
**Quick fix if env not loading**: Change the hardcoded default in `esports_bot.py` line 187:
```python
# FROM:
self._min_confidence = float(getattr(settings, "ESPORTS_MIN_CONFIDENCE", 0.52))
# TO:
self._min_confidence = float(getattr(settings, "ESPORTS_MIN_CONFIDENCE", 0.50))
```
Then deploy. This is a Tier 1 config change.

### P0: `_inject_glicko2_metadata()` key mismatch — THE REAL ROOT CAUSE OF ~0.50 OUTPUT
**Symptom**: Model probability for most LoL markets is 0.4806-0.5347 (barely different from coin flip). 10/21 markets (48%) affected.
**Impact**: Creates either huge fake edges (→ edge_cap) or near-zero confidence (→ low_confidence). Result: 0-3 opportunities per 8 hours.

**Root cause**: `_inject_glicko2_metadata()` (line 1848-1876) looks up Glicko2 ratings using PandaScore **numeric opponent IDs** (e.g., `"128947"`), but `glicko2_ratings` table keys are **lowercased team names** (e.g., `"bilibili gaming"`). Every lookup misses → returns default rating (mu=1500, phi=350) → `team_strength_diff=0` → `glicko2_est=0.50` → model anchors on coin flip.

**Proof the ratings ARE good** (verified via direct DB query):
```
LoL: 248 teams, mu range 1499-1705, phi range 65-260, 1875 matches
CS2: 395 teams, mu range 1480-1714, phi range 63-290, 3754 matches
Math works: mu=1700 vs mu=1300 with phi=150 → expected_score=0.889
```

**The fix** (surgical, ~5 lines):
In `_inject_glicko2_metadata()`, change team ID extraction from:
```python
team_a_id = str(opponents[0].get("opponent", {}).get("id", ""))    # numeric ID → MISS
```
To:
```python
team_a_id = str(opponents[0].get("opponent", {}).get("name", "")).lower()  # team name → HIT
```
Same for `team_b_id`. The PandaScore `opponents` payload contains both `id` and `name`.

**Verification after fix**: Check that `game_state["team_strength_diff"]` is non-zero in logs. For a match like "Bilibili Gaming vs Team WE" (mu=1705 vs mu=1499), TSD should be ~0.206, giving `glicko2_est ≈ 0.706` instead of 0.50. The model should then output differentiated probabilities.

**Secondary issues** (lower priority, fix AFTER the key mismatch):
- **Option A**: Increase Bayesian blend weights for uncertain teams in `_get_glicko2_prediction()` (phi 100-200 range gets 20% blend → try 40%)
- **Option B**: Lower `min_confidence` to 0.48 temporarily to get more paper trades flowing
- **Option C**: The non-LoL fallback path (`_get_glicko2_prediction()`) may also have team name matching gaps — verify after LoL is fixed

### P1: `no_prediction=5` per scan — team name matching failures
**Symptom**: 5 markets per scan can't match team names to Glicko data
**Impact**: 24% of markets (5/21) completely untradeable
**Root cause**: SC2, minor LoL academy teams, team name aliases not in PandaScore
**Fix**: Improve team name fuzzy matching or maintain an alias dictionary

### P2: Valorant permanently halted (0% accuracy)
**Symptom**: `esportsbot_monitoring_halt accuracy=0.0 brier=0.4727 game=valorant`
**Impact**: 2 markets per scan permanently blocked
**Root cause**: Too few resolved Valorant trades (5 trades, 1 win) with tiny sample creating extreme Brier score
**Fix**: Either reset Valorant monitoring counters or increase sample size requirement before halting

### P3: WS 13+ days stale — WS-primary mode cannot engage
**Symptom**: `ws_trading=False` in every scan. `_last_ws_price_ts` never updates because no WS events arrive.
**Impact**: All the WS-primary latency work (S94) is useless — scan loop trades everything
**Root cause**: Unknown. Either WS subscription not including esports markets, or the base `on_price_update()` filters them out before reaching EsportsBot.
**Investigation**: Check if esports market token IDs are in the WS subscription list. Check `_market_token_map` for populated entries.

---

## ALL S96 CODE CHANGES IN `bots/esports_bot.py` (COMMITTED as `8a36e98`)

Summary of all changes made this session (92 lines changed, now committed):

### 1. Edge cap / low_confidence logs promoted to INFO (was debug)
- Lines 1394, 1411: `logger.debug` → `logger.info` for `esportsbot_edge_cap` and `esportsbot_low_confidence`
- **Why**: Need to see these in production logs for waterfall diagnosis

### 2. Glicko2 callers pass `market_price` (3 locations)
- Lines 1573, 1598, 1625: `_get_glicko2_prediction(market_data, game)` → `_get_glicko2_prediction(market_data, game, price)`
- **Why**: Market-price-as-prior Bayesian blend (Fix B above)

### 3. Killed 0.50 fallback for unknown teams
- Lines 1633-1643: Removed `ESPORTS_MARKET_FALLBACK_ENABLED` block that returned `0.50` as fallback probability
- Replaced with `return None` and comment explaining why
- **Why**: 0.50 fallback created huge fake edges (35-42%) flooding waterfall with edge_cap rejections

### 4. Removed conformal confidence override from `_execute_esports_trade()`
- Lines 2430-2454: Deleted entire `if self._conformal_predictor...` block (20 lines)
- Replaced with comment explaining S91 bankroll_manager handles conformal correctly
- **Why**: ROOT CAUSE of all trades dying. `conservative_prob()` crushed confidence 0.52→0.02

### 5. Added diagnostic logging at `size <= 0` return point
- After line 2452: New `logger.warning("esportsbot_sizing_killed_at_bankroll", ...)` with confidence, price, edge, phi_factor, dd_factor
- **Why**: Observability — if sizing fails again, we'll see exactly why

### 6. Minimum bet threshold lowered
- Line 2494: `if size < 1.0:` → `if size < 0.10:`
- **Why**: Post-multiplier sizes (Kelly 0.20 × game_mult 0.5 × phi × dd × edge_decay) were landing between $0.10-$1.00 and getting killed

### 7. Prefetch bug fix — `_live_matches` value type handling
- Lines 2914-2920: Added `isinstance(match_data, dict)` checks with fallback to `getattr()` for LiveMatch objects
- **Why**: `_live_matches` values can be either dicts or LiveMatch objects depending on source. Prefetch crashed with `dict.get()` on LiveMatch objects.

### 8. `_get_glicko2_prediction()` signature + market-price-as-prior
- Line 3602: Added `market_price: float = 0.50` parameter
- Lines 3747-3761: Bayesian prior blend uses `_prior = max(0.05, min(0.95, market_price))` instead of `0.50`
- Lines 3770-3771: Roster stability nudge uses `market_price` instead of `0.50`
- **Why**: Anchors uncertain predictions on market consensus rather than coin-flip

---

## KEY ARCHITECTURE FACTS (for continuation)

### File: `bots/esports_bot.py` (~4900 lines)
This is the ONLY file modified in this session. It contains:
- `EsportsBot(BaseBot)` — the main class
- `__init__()` — instance vars, model refs, config loading
- `scan_and_trade()` — main scan loop (every 10s, or 0s with live matches)
- `on_price_update()` — WebSocket reactive trading path
- `analyze_opportunity()` — prediction pipeline (Glicko-2 / ML model → calibration → edge computation)
- `_execute_esports_trade()` — sizing + order placement (returns `bool`)
- `_get_model_prediction()` — routes to game-specific model (LoL ML, Dota2 Glicko, etc.)
- `_get_glicko2_prediction()` — Glicko-2 rating lookup + Bayesian blend
- `_background_form_prefetch()` — continuous task refreshing PandaScore team data

### Prediction Pipeline (CRITICAL to understand)
```
Market → detect_game() → _get_model_prediction() → one of:
  ├─ LoL live: _lol_model.predict_with_glicko2(game_state)  ← NOT affected by market-price-as-prior
  ├─ Dota2: _get_glicko2_prediction() → _dota2_model.predict_with_glicko2()  ← IS affected
  ├─ Valorant: _get_glicko2_prediction() → _valorant_model.predict_with_glicko2()  ← IS affected
  └─ Fallback: _get_glicko2_prediction() → raw Glicko-2 expected score  ← IS affected
         └─ (was: return 0.50 if no teams found. NOW: return None)
```

### Sizing Pipeline (where trades previously died)
```
confidence (from analyze_opportunity)
  → [REMOVED: conformal conservative_prob override — was the killer]
  → _apply_expiry_boost()
  → bankroll_manager.calculate_bet_size(confidence, price, category="esports")
      → if confidence <= price: return 0.0  ← this was triggered when conformal crushed confidence
      → kelly * (confidence - price) / (1 - price) * bankroll
      → capped at max_bet_usd ($100)
  → apply multipliers: phi_factor, dd_factor, volatility, edge_decay_mult
  → if size < 0.10: return False  ← lowered from $1.00
  → place_order(side="YES"/"NO", size=size)
```

### Waterfall (analyze_opportunity rejection flow)
```
Market enters analyze_opportunity():
  1. detect_game() → if unknown: no_game++, return None
  2. game in halted_games → halted++, return None
  3. observation_mode(game) → observation++, return None
  4. patch_halt(game) → halted++, return None
  5. game_exposure >= max → exposure_cap++, return None
  6. _get_model_prediction() → if None: no_prediction++, return None
  7. compute edge = |confidence - price|
  8. edge < min_edge → low_edge++, return None
  9. edge > max_edge → edge_cap++, return None
  10. confidence < min_confidence → low_confidence++, return None
  11. ✅ Return opportunity dict
```

### Live VPS Config (as of this session)
```
ESPORTS_MIN_EDGE=0.05          (5% minimum edge to trade)
ESPORTS_MIN_CONFIDENCE=0.50    (in .env, but NOT taking effect — shows 0.52 in logs!)
ESPORTS_MAX_EDGE=0.40          (raised from 0.25, IS taking effect)
ESPORTS_MAX_GAME_EXPOSURE=300  (per-game cap)
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0
KELLY_FRACTION=0.25            (auto-degraded to 0.20 due to Brier 0.37)
Game Kelly multipliers: cs2=0.5, valorant=0.5
```

### Model Health (as of session end)
```
CS2:      accuracy=24%, brier=0.2883, kelly_mult=0.5   WARNING
Valorant: accuracy=0%,  brier=0.4727, kelly_mult=0.5   HALTED
Dota2:    trades=7, 6W/1L, pnl=+$217.81                HEALTHY
LoL:      trades=1, 0W/1L, pnl=-$4.15                  INSUFFICIENT DATA
```

### Total P&L
```
Total: -$174.00 across 71 trades (49.3% win rate)
CS2: +$97.53 (47 trades, 53.2% WR)
Dota2: +$217.81 (7 trades, 85.7% WR)
LoL: -$4.15 (1 trade, 0% WR)
Unknown: -$276.39 (11 trades, 27.3% WR)
Valorant: -$208.79 (5 trades, 20% WR)
```

---

## DEPLOY PROCESS

```bash
# From local Windows machine:
cd C:\lockes-picks\polymarket-ai-v2
git add bots/esports_bot.py
git commit -m "feat(esports): S96 ..."
bash deploy/deploy.sh   # deploys to VPS via rsync + atomic symlink

# OR if deploy.sh fails (no git remote):
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
cd /opt/polymarket-ai-v2 && sudo bash deploy/deploy.sh

# Verify:
journalctl -u polymarket-ai --since "2 min ago" -o cat --no-pager 2>/dev/null | sed "s/\x1b\[[0-9;]*m//g" | grep "esportsbot_scan_summary" | tail -3

# Restart service (for .env changes):
sudo systemctl restart polymarket-ai
```

### VPS Connection
```
SSH: ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
Code: /opt/polymarket-ai-v2/ (symlink to release dir)
Logs: journalctl -u polymarket-ai --since "5 min ago" -o cat --no-pager 2>/dev/null | sed "s/\x1b\[[0-9;]*m//g"
```

---

## UNCOMMITTED NON-ESPORTS CHANGES IN WORKING TREE (DO NOT COMMIT WITH ESPORTS)

| File | Source | Description |
|------|--------|-------------|
| `AGENT_HANDOFF_MIRRORBOT_SESSION91_2026_03_14.md` | S91 | Handoff doc |
| `base_engine/data/database.py` | S93 | uPnL formula fix |
| `base_engine/data/ingestion_error_capture.txt` | Unknown | Error log |
| `run_ui.py` (DELETED) | UI overhaul | Old UI |
| `ui/async_worker.py` (DELETED) | UI overhaul | Old async worker |
| `ui/dashboard.py` (DELETED) | UI overhaul | Old dashboard |
| `tests/unit/test_mirror_bot_logic.py` | S93 | Test updates |

**Esports changes already committed** as `8a36e98`. These non-esports files remain uncommitted from other sessions.

---

## PHASE 3: Smart Prediction Invalidation (NOT STARTED — from S94 handoff)

This was in the S94 plan but was never implemented. Lower priority than the model quality issues above.

### 3A. New instance var:
```python
self._prediction_refresh_needed: Set[str] = set()
```

### 3B. New method `_should_repredict()`:
```python
def _should_repredict(self, market_id: str) -> bool:
    if market_id in self._prediction_refresh_needed:
        self._prediction_refresh_needed.discard(market_id)
        return True
    cached = self._prediction_cache.get(market_id)
    if not cached:
        return True
    age = time.monotonic() - cached.get("ts", 0)
    if age > 300.0:
        return True
    if market_id in self._live_matches:
        return True
    return False
```

### 3C. Wire invalidation triggers
### 3D. Modify `_analyze_one()` to skip unchanged markets

---

## CRITICAL TRAPS (from CLAUDE.md + session learnings)

- **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
- **Paper trading IS production** — no shortcuts
- **NEVER invert P&L formulas for NO positions** — `cost = entry * size`, `uPnL = (current - entry) * size` for ALL sides
- **`_execute_esports_trade()` returns `bool`** — True = trade placed, False = sizing/order failed
- **`_trade_lock`** — ALL 5 callers of `_execute_esports_trade()` must use `async with self._trade_lock:`
- **Conformal sizing handled by bankroll_manager** — do NOT add conformal override in `_execute_esports_trade()` again
- **`_get_glicko2_prediction()` now takes `market_price` parameter** — all callers must pass it
- **LoL ML model path does NOT go through `_get_glicko2_prediction()`** — market-price-as-prior doesn't affect LoL
- **LoL ML model path code**: Lines 1525-1548 — `_get_model_prediction()` → `_inject_glicko2_metadata()` (1848) → `predict_with_glicko2()` (1538-1540)
- **The broken lookup**: Lines 1864-1867 — `opponents[i].get("opponent", {}).get("id", "")` should be `.get("name", "").lower()`
- **CS2 model path**: Lines 1552-1569 — uses `predict_match()` with `team_strength_diff` from game_state (ALSO affected by same bug if CS2 uses `_inject_glicko2_metadata`)
- **Glicko2 tracker init**: Lines 2704-2803 — `_init_glicko2_trackers()` builds ratings from DB with name keys
- **`_match_team_name()`**: Used by `_get_glicko2_prediction()` fallback path — may already work correctly for non-live markets
- **`_live_matches` values can be dict or LiveMatch objects** — use `isinstance()` checks
- **PatchDriftDetector**: `_patch_timestamps` only set on genuine patch changes (`old is not None`)
- **`asyncpg JSONB`**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function
- **BOT_REGISTRY=14 bots** — shared module changes require all 14 verified
- **ESPORTS_MAX_EDGE took effect but ESPORTS_MIN_CONFIDENCE did NOT** — investigate .env loading
- **`_inject_glicko2_metadata()` uses WRONG KEY** — looks up by PandaScore numeric ID, but glicko2_ratings keys are lowercased team names. MUST fix to `.get("name", "").lower()` not `.get("id", "")`
- **Glicko2 ratings ARE differentiated** — DB has 248 LoL teams (mu 1499-1705), 395 CS2 teams. The data is good, the lookup is broken.

---

## OPERATOR PREFERENCES

- **Scope lock**: NEVER add unsolicited features. Fix only what's requested.
- **Bot-scoped sessions**: Each session modifies ONE bot. No bleed.
- **Show plans in conversation**: User wants plans displayed in chat, not hidden in files.
- **"Find the root and resolve it"**: User expects you to trace to actual root cause, not band-aid.
- **Paper trading IS production**: No shortcuts because "we're only paper trading."
- **User gets frustrated with zero activity**: Getting trades executing (even bad ones) matters more than perfect filtering in paper mode.

---

## WHAT THE NEXT SESSION SHOULD DO (priority order)

1. **Fix P0: `_inject_glicko2_metadata()` key mismatch** — THE #1 PRIORITY. Change lines 1864-1867 in `bots/esports_bot.py` to use `opponents[i].get("opponent", {}).get("name", "").lower()` instead of `.get("id", "")`. This single fix should make LoL model_prob go from ~0.50 to differentiated values (0.30-0.70+). Deploy, wait one scan, verify `team_strength_diff != 0` in logs and that opportunities increase dramatically.

2. **Fix P0: MIN_CONFIDENCE not loading** — VPS `.env` says `0.50` but bot shows `0.52`. Either fix .env loading or change the hardcoded default in esports_bot.py line 187 to 0.50. Deploy and verify `min_confidence=0.50` appears in scan summary.

3. **Verify CS2/Dota2/Valorant paths** — After fixing LoL, check if non-LoL games also have team name matching issues in `_get_glicko2_prediction()`. The Glicko2 fallback path uses `_match_team_name()` which might already use names correctly — verify.

4. **Investigate WS staleness** — Why has WS been stale for 13+ days? Is WS subscribed to esports markets?

5. **Phase 3: Smart Prediction Invalidation** — From S94 plan, not yet implemented. Lower priority than model quality.

---

## CHANGE LOG

```
## CHANGE: 2026-03-16 (Session 96, continuing S94)
**Issue:** EsportsBot trades silently failing at sizing pipeline — zero trades executing despite opportunities found
**Root cause:** conformal conservative_prob() in _execute_esports_trade() crushing confidence 0.52→0.02
**Files modified:** bots/esports_bot.py (uncommitted)
**Lines changed:** +52/-40 (net +12)
**Blast radius:** EsportsBot only — no shared modules touched
**Verification:**
  - First real trade executed within 30s of deploy (confidence=0.524, size=$100)
  - 2 trades in 8h overnight — confirmed working
  - Zero sizing kills in diagnostic log
  - Prefetch crash found and fixed
  - Waterfall now shows honest rejection reasons (not masked by fallback 0.50)
**Remaining:**
  - MIN_CONFIDENCE .env not loading (shows 0.52, should be 0.50)
  - Glicko model outputs ~0.50 for most markets — fundamental model quality issue
  - LoL ML model path not affected by market-price-as-prior fix
**Rollback:** git revert 8a36e98
```

## GLICKO2 DATABASE EVIDENCE (from direct asyncpg queries on VPS)

```
=== GLICKO2 RATINGS TABLE ===
  cod: 12 teams, avg_phi=66, min_phi=64, max_phi=70, avg_matches=367
  cs2: 395 teams, avg_phi=128, min_phi=63, max_phi=290, avg_matches=3754
  dota2: 154 teams, avg_phi=138, min_phi=63, max_phi=290, avg_matches=1758
  lol: 248 teams, avg_phi=142, min_phi=65, max_phi=260, avg_matches=1875
  r6: 24 teams, avg_phi=251, min_phi=228, max_phi=260, avg_matches=28
  rl: 53 teams, avg_phi=106, min_phi=63, max_phi=173, avg_matches=722
  sc2: 42 teams, avg_phi=113, min_phi=64, max_phi=217, avg_matches=550
  valorant: 87 teams, avg_phi=199, min_phi=108, max_phi=304, avg_matches=249

=== LOL — lowest phi (most confident ratings) ===
  invictus gaming          mu=1567 phi=65 matches=1875
  jd gaming                mu=1642 phi=65 matches=1875
  weibo gaming             mu=1643 phi=66 matches=1875
  top esports              mu=1602 phi=67 matches=1875
  bilibili gaming          mu=1705 phi=67 matches=1875
  anyone's legend          mu=1665 phi=67 matches=1875
  oh my god                mu=1597 phi=69 matches=1875
  dplus kia                mu=1692 phi=69 matches=1875
  team we                  mu=1499 phi=69 matches=1875

=== CS2 — lowest phi ===
  los kogutos              mu=1566 phi=63 matches=3754
  vp.prodigy               mu=1585 phi=64 matches=3754
  ww team                  mu=1645 phi=64 matches=3754
  omega                    mu=1577 phi=65 matches=3754
  whitebird                mu=1714 phi=65 matches=3754
  mouz nxt                 mu=1672 phi=66 matches=3754

=== TRAINING DATA (resolved matches) ===
  cs2: 4145 matches
  dota2: 2454 matches
  lol: 1953 matches
  rl: 722 matches
  r6: 676 matches
  sc2: 550 matches
  cod: 367 matches
  valorant: 182 matches

=== KEY LOOKUPS ===
  glicko2_ratings.team_key samples: "3bl esports", "bilibili gaming", "jd gaming"
  PandaScore opponent.id samples: "128947", "52341" (numeric)
  ⚠️ MISMATCH: tracker.get_rating("128947") → DEFAULT (1500/350) every time
```

## EDGE CAP PROBLEM EXPLAINED (for operator context)

The model outputs ~0.50 for most teams. When market prices are extreme, the gap is huge:

| Market Price | Model Prob | Edge | Result |
|---|---|---|---|
| 0.115 | 0.6545 | **54%** | edge_cap (>40%) |
| 0.845 | 0.4425 | **40.2%** | edge_cap (barely over) |
| 0.055 | 0.5814 | **52.6%** | edge_cap |
| 0.605 | 0.5039 | **10.1%** | low_confidence (0.4961 < 0.52) |
| 0.770 | 0.5039 | **26.6%** | low_confidence |
| 0.630 | 0.4806 | **14.9%** | low_confidence (0.5194 < 0.52) |

After fixing `_inject_glicko2_metadata()`, model_prob should be differentiated (e.g., 0.70 for strong favorites). Edge becomes realistic (10-20%) and confidence passes threshold.

Config options considered:
| Option | Current | Considered | Status |
|---|---|---|---|
| ESPORTS_MIN_CONFIDENCE | 0.52 (in code, .env=0.50 broken) | 0.48-0.50 | .env not loading |
| ESPORTS_MAX_EDGE | 0.40 (was 0.25) | 0.40 | Working |
| ESPORTS_MIN_EDGE | 0.05 | 0.05 | Working |
