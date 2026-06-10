# WB Trade-Funnel Deep-Dive — Bottlenecks + Over-Tight Constraints (S241, 2026-06-10)

**Read-only diagnostic. No code changed.** Sources: live journalctl (polymarket-weather, release `20260609_132203`),
read-only SQL against `markets` (operational table, 2026-06-10 ~14:32 UTC; script `scripts/wb_funnel_diag.sql`),
code on wb/main `602eefb` (file:line cited). All numbers are infra-state per Protocol 11.

---

## VERDICT
WB's low trade count is NOT caused by trading gates, sizing, confidence thresholds, or the staleness filter.
The funnel is killed by **three stacked upstream bottlenecks** — two structural, one rate-limit:

```
markets table:  12,416 active+unresolved "weather" rows
                ├─ 10,504 have NULL end_date_iso   (dead, never deactivated)
                ├─  1,817 end_date_iso in the past (ghosts, never deactivated)
                └─     95 genuinely fresh (end_date_iso >= NOW)
                          │
   [B1] engine query: ORDER BY liquidity DESC LIMIT 2500, NO end-date filter
        (base_engine/base_engine.py:2438-2448; SCAN_MARKET_LIMIT=2500, :2405)
                          │  → only 21 of 94 eligible fresh markets make the top-2500
                          │  → 73 CROWDED OUT by dead rows (incl. Tokyo, Paris, Madrid,
                          │    Milan, Amsterdam, Warsaw, Ankara, Karachi, Lucknow…)
                          ▼
   grouping + staleness (cutoff_days=1, weather_bot.py:1869-1881 — behaving CORRECTLY,
        drops ~600 genuinely-old groups)
                          │  → city universe collapses to n=1 (Manila) every scan
                          ▼
   [B2] forecast fetch for the 1 surviving group: Open-Meteo HTTP 429 rate-limited
        (journalctl: open_meteo_ensemble_error status=429 + 300s cooldowns, all day,
         35-44 suppressed dups each; forecast_client.api_calls_this_scan = 541 in ONE
         cycle, weather_bot.py:2106 — burned mostly in the 54s discovery phase)
                          │  → weatherbot_analyze_skip reason=no_forecast EVERY cycle
                          ▼
        groups_with_edge=0, best_edge=0.0 → trades=0
```

**Ingestion IS writing fresh markets** (max created_at / updated_at = minutes before the check) — the markets
exist in the DB; the bot just never sees them (B1), and the one it sees it can't forecast (B2).

## THE THREE BOTTLENECKS, RANKED BY LEVERAGE

### B1 — Query crowd-out (HIGHEST: unlocks ~73 markets / ~dozen cities immediately)
`_fetch_tradeable_markets` (top-level `base_engine/base_engine.py:2438-2448` — the LIVE engine for WB) selects
`active=true AND resolved=FALSE` with NO end-date awareness, ordered by stored `liquidity` DESC, LIMIT 2500.
12.3k dead weather rows out-rank fresh dailies (fresh markets start with modest liquidity). Fix directions
(operator to choose; all WB-lane on wb/main since only WB runs this process):
- (a) add `AND (m.end_date_iso >= NOW() - INTERVAL '2 days')` when a category filter is present (weather dailies
  always carry end_date_iso; the 10.5k NULL rows are dead) — minimal, but changes semantics for any category caller;
- (b) order weather-category queries by `end_date_iso DESC NULLS LAST` instead of liquidity;
- (c) WB-side: dedicated fresh-markets query in weather_bot.py (no shared-query touch at all).

### B2 — Open-Meteo 429 starvation (BINDING once B1 lands: more cities ⇒ more forecast calls)
541 forecast-client API calls in one ~5-min scan → free-tier 429 → 300s deterministic cooldown ≈ the whole scan
interval → permanent `no_forecast`. The calls concentrate in the discovery phase (ms_discovery=54s). Fix directions:
attribute + cap the discovery-phase fan-out, batch Open-Meteo requests (multi-model per call), lengthen forecast
cache TTL, and/or paid API key. NOTE: B1 alone without B2 = more cities, all skipping no_forecast.

### B3 — markets-table hygiene (structural; shared-table + ingestion = MB-coordination)
12,321 dead weather rows flagged tradeable. One-time deactivation (UPDATE active=false on past/NULL-end-date dead
rows) + ingestion-side continuous deactivation. Shared table + shared ingestion service → operator sign-off + MB
coordination doc; NOT WB-unilateral. (B1 makes WB immune to the dirt; B3 removes the dirt + shrinks every
category query for all bots.)

## CONSTRAINTS REVIEWED AND **CLEARED** (not over-zealous)
- Staleness `cutoff_days=1` (`WEATHER_STALE_GROUP_CUTOFF_DAYS`, weather_bot.py:1869) — correctly drops old groups.
- `min_liquidity` — WB already passes 0 (weather_bot.py:1786). The `min_liquidity=100` log line is a different caller.
- Price-band 0.01–0.99, eighth-Kelly/$50 max bet (graduated 0.35 by `weatherbot_kelly_graduation`), exposure caps,
  cooldowns, one-bet-per-market — none binding; funnel dies before any of them.
- Confidence calibrator `insufficient_data n=5 need=200` — quality concern, not a count gate; will self-heal with volume.
- `consecutive_no_edge` backoff (=6 observed) — symptom of the starvation, not a cause; clears once trades flow.

## SIDE OBSERVATIONS (not chased; separate threads)
- `polymarket-weather` was restarted ~2026-06-10 14:18 UTC via stop-sigterm timeout → SIGKILL (initiator unknown;
  not this session). The dying process had consumed 4h29m CPU / 1.5G peak.
- `city_parse_fail=320` in grouping + unmatched cities Cape Town / Panama City (no station mapping) — minor
  universe leakage, only matters after B1/B2.
- systemd warns `Unknown key name 'StartLimitIntervalSec' in section 'Service'` for polymarket-weather.service:19
  (wrong section; belongs in [Unit]) — cosmetic-but-real unit-file bug.

## End S241 deep-dive

---
## ★ OUTCOME UPDATE (same day, 2026-06-10): B1+B2 IMPLEMENTED + DEPLOYED + VERIFIED
Commits (wb/main): **B1 `8895cd1`** (freshness clause, both trees + `CATEGORY_SCAN_FRESHNESS_DAYS` default 2, 0=rollback) ·
**B2 `e193549`** (silo ModelRunMonitor: `WEATHER_MODEL_RUN_REFRESH_DAYS` default 3 + 429-abort) ·
**`c3d3696`** deploy.sh probe-transport-255 ≠ health-fail (no false rollback).
Tests: B1 6/6, B2 6/6; full suite 3398 pass / 7 pre-existing EB. Release **`20260610_123721`**, probe HEALTH_OK@60s.
**First scan on new release (journalctl 16:41 UTC):** `city_universe n=38` (was 1) · `weather_markets=465` (was 2500 cap) ·
`api_calls=44` (was 541) · `model_run_refresh_aborted_429 done_pairs=0 total_pairs=321` (guard fired during pre-existing
Redis-persisted ensemble cooldowns) · `groups=78, groups_with_edge=1, trades=1` — first entry on first scan.
**WATCH:** no_forecast skips should fade as the 1h ensemble cooldowns expire; api_calls stays double-digit; entry rate over 24-48h.
**B3 (markets-table hygiene + ingestion deactivation) remains open** — operator + MB-coordination item.
