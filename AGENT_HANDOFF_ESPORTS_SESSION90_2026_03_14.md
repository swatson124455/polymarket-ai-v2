# AGENT HANDOFF — EsportsBot Session 90 (2026-03-14)
## Resolve All Outstanding Items: Team Names, LiveBot, SeriesBot, Test Fix, Deferred Docs

**Predecessor**: Session 89 (E2-E5 scaling + 9 audit fixes), Session 88 (observation mode fix)
**Scope**: Esports subsystem only (EsportsBot, EsportsLiveBot, EsportsSeriesBot)
**Status**: All changes uncommitted, tests passing (144/144 esports + paper_is_production)

---

## WHAT WAS DONE (6 code changes + 1 doc)

### Change 1: Fix test_paper_is_production failure (P5)
**File**: `tests/unit/test_paper_is_production.py`
- Added `ESPORTS_MAX_TOTAL_EXPOSURE_USD=100000.0` to `_RISK_SETTINGS_DEFAULTS`
- Root cause: MagicMock returned MagicMock instead of float default for `getattr(settings, "ESPORTS_MAX_TOTAL_EXPOSURE_USD", max_total)` at `risk_manager.py:405`
- All 11 EsportsBot-parameterized tests now pass

### Change 2: Expanded tournament suffix regex (P2, part 1/3)
**File**: `bots/esports_bot.py` — `_clean_team_names()`
- Added 20+ tournament patterns: aorus, cct, gamers8, betboom, perfect world, thunderpick, elisa, skyesports, yalla, esea, open/closed qual, regular season, upper/lower bracket, grand final, elimination, decider, promotion, relegation, showmatch, invitational, lan final, rmr, asia league, americas, pacific, emea league
- Added `"overwatch 2: "`, `"overwatch: "` to game prefixes
- Added `"match"` to `_game_winner_re` pattern (was only `game|map`)

### Change 3: Expanded _TEAM_ALIASES (P2, part 2/3)
**File**: `bots/esports_bot.py` — `_TEAM_ALIASES`
- Grew from 57 to ~85 entries, organized by region with comments
- New CS2: heroic, ence, eternal fire (ef), saw, gamerlegion (gl), big, apeks, aurora, 3dmax, imperial (imp), pain, mibr, furia, 9z, wildcard, grayhound, tyloo, lynn vision, the mongolz, mousesports
- New Dota 2: tundra, gaimin gladiators, xtreme gaming, nouns
- New Valorant: sentinels (sen), rrq, team heretics (th), karmine corp (kcorp), fut, bleed, detonation focusme (dfm)
- New LoL: dplus, foxx (fox), weibo

### Change 4: Added fuzzy match Tier 6 (P2, part 3/3)
**File**: `bots/esports_bot.py` — `_match_team_name()`
- New Tier 6 after word-boundary: `difflib.SequenceMatcher` with 0.85 threshold
- Skips known names <= 2 chars to avoid false positives
- stdlib only — no new dependency (difflib already used in 3 other project files)
- Catches typos, transliterations, minor spelling variations

### Change 5: Added retry with backoff to EsportsGameMonitor poll (P3 — LiveBot)
**File**: `esports/live/esports_game_monitor.py` — `_poll_live_matches()`
- Per-game poll now retries once on TimeoutError with 2x timeout
- First attempt: `ESPORTS_LIVE_POLL_TIMEOUT` (default 10s)
- Retry: 2x timeout (20s)
- Max additional latency: 20s per game per cycle
- Logs: `"poll timeout, retrying"` and `"poll timeout after retry"`

### Change 6: EsportsSeriesBot Glicko-2 fallback (P3 — SeriesBot)
**File**: `bots/esports_series_bot.py`
- New `_get_glicko2_expected_score()` — queries `glicko2_ratings` table for both teams, computes `expected_score(a, b)` from Glicko-2 ratings
- Cached per session (`_glicko2_cache` dict)
- Rejects extreme probabilities (<0.05 or >0.95)
- Requires match_count >= 10 for both teams
- `_simple_series_prob()` now accepts `per_map_prob=None` kwarg
- Falls back to 0.50 if Glicko-2 unavailable (no regression)

---

## DEFERRED ITEMS — STATUS & OPERATOR ACTIONS

### E1: TabPFN Ensemble
- **Code**: Complete in `esports/models/tabpfn_ensemble.py`. Wired into `_get_model_prediction()` at 30/70 blend for sparse games (SC2, RL, CoD, R6).
- **Status**: Gracefully degrades when `tabpfn` not installed (returns None).
- **Operator action**: `pip install tabpfn` on VPS (requires torch ~2GB). Check disk space first:
  ```bash
  ssh -i "$KEY" ubuntu@34.251.224.21 "df -h /opt"
  # If >3GB free:
  ssh -i "$KEY" ubuntu@34.251.224.21 "source /opt/pa2-shared/venv/bin/activate && pip install tabpfn"
  ```
- **Risk**: Low — graceful fallback means failed install is harmless.

### E6: Map-Veto Model
- **Code**: Not implemented. Requires reliable HLTV scraper + training pipeline.
- **Status**: Blocked on `esports/data/hltv_scraper.py` reliability (CS2 only, scrapes hltv.org).
- **Next step**: Dedicated session to build HLTV scraper with retries + anti-bot handling.

### E7: Conformal Prediction Intervals
- **Code**: Complete in `esports/models/conformal_wrapper.py`. `mapie>=0.9.0` in requirements. Wired into `_execute_esports_trade()` for conservative Kelly sizing.
- **Status**: Identity (unfitted) — needs 30+ calibration samples per game.
- **Check if ready**:
  ```sql
  SELECT game, COUNT(*) as n
  FROM prediction_log
  WHERE bot_name = 'EsportsBot' AND trade_executed = true
  GROUP BY game;
  ```
  If any game has 30+, conformal will auto-activate on next 10-min calibration cycle.

---

## FILES MODIFIED THIS SESSION

| File | Changes |
|------|---------|
| `tests/unit/test_paper_is_production.py` | +1 line: `ESPORTS_MAX_TOTAL_EXPOSURE_USD` |
| `bots/esports_bot.py` | Tournament regex (+20 patterns), aliases (57→85), fuzzy Tier 6, overwatch prefix, match winner re |
| `esports/live/esports_game_monitor.py` | Retry loop: 1 retry with 2x timeout on poll TimeoutError |
| `bots/esports_series_bot.py` | `_get_glicko2_expected_score()` method, `_simple_series_prob()` per_map_prob kwarg, `_glicko2_cache` |

---

## BLAST RADIUS

| Scope | Affected |
|-------|----------|
| Test-only | Change 1 (test_paper_is_production) |
| EsportsBot only | Changes 2-4 (team name matching) |
| EsportsLiveBot only | Change 5 (poll retry) |
| EsportsSeriesBot only | Change 6 (Glicko-2 fallback) |
| Cross-bot | None |
| New config keys | None |
| Schema | None |

---

## VERIFICATION

```bash
# Tests (all should pass)
pytest tests/unit/test_paper_is_production.py tests/unit/test_esports_bot.py -x -q

# Post-deploy on VPS:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"

# Team name matching: target no_prediction < 5 (was 12)
ssh -i "$KEY" ubuntu@34.251.224.21 "journalctl -u polymarket-ai --no-pager -n 200" | grep esportsbot_scan_summary

# LiveBot retry logs
ssh -i "$KEY" ubuntu@34.251.224.21 "journalctl -u polymarket-ai -f" | grep "EsportsGameMonitor"

# SeriesBot Glicko-2 usage
ssh -i "$KEY" ubuntu@34.251.224.21 "journalctl -u polymarket-ai -f" | grep "EsportsSeriesBot"

# Verify LiveBot/SeriesBot enabled
ssh -i "$KEY" ubuntu@34.251.224.21 "grep -E 'ESPORTS_(LIVE|SERIES)' /opt/polymarket-ai-v2/.env"
```

---

## OUTSTANDING ITEMS (EsportsBot)

| Priority | Item | Status |
|----------|------|--------|
| P2 | Team name matching (12→<5 expected) | Fixed this session |
| P3 | PandaScore timeout (LiveBot) | Retry added this session |
| P3 | EsportsSeriesBot 0 trades | Glicko-2 fallback added this session |
| P3 | 604 unresolved markets | Naturally resolving via backfill |
| P5 | Test failure | Fixed this session |
| P5 | Remaining `no_prediction` (~3-5/scan) | Diminishing returns — mostly games with <10 Glicko matches |
| Deferred | E1 TabPFN | Operator: `pip install tabpfn` |
| Deferred | E6 Map-veto | Needs HLTV scraper session |
| Deferred | E7 Conformal | Auto-activates at 30 samples/game |
