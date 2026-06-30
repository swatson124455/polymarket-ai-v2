# EB Rebuild — DATA ONLY (no interpretation)

**Every line below is a raw fact with the exact command that reproduces it, captured 2026-06-23 against the live system. No verdicts, no conclusions, no recommendations, no "keep/dead/reusable" — those are interpretation and are listed in the STRIPPED section at the bottom so they are NOT carried into the rebuild as fact.** If a line here is wrong, the command will show it.

---

## 1. Database tables — row counts (reproduce: `ssh … psql "$DBURL" -c "SELECT count(*) FROM <table>"`)

Each count was run twice in the same query (c1, c2) and matched; corroborating column in parentheses.

| Table | count | corroborating fact |
|---|---|---|
| esports_matches | 32,369 | winner IS NOT NULL: 30,882 · match_date 2024-01-01 → 2026-06-21 |
| esports_training_data | 17,729 | outcome IS NOT NULL: 17,729 |
| esports_predictions | 1,438 | actual_winner IS NOT NULL: 950; model_version all 'v2-trinity' (35 contaminated rows DELETED 2026-06-23, backup /tmp/eb_contaminated_backup_20260623.csv) |
| esports_prediction_log | 1,234 | actual_outcome IS NOT NULL: 633 · created_at 2026-03-07 → 2026-06-23 |
| esports_unmatched_predictions | 1,453 | — |
| esports_team_aliases | 1,777 | distinct game: 2 |
| esports_calibration | 4 | — |
| esports_odds | 0 | (empty) |
| glicko2_ratings | 1,205 | — |
| positions WHERE source_bot='EsportsBot' | 984 | — |

## 2. Trade ledger — canonical (reproduce: `cd /opt/polymarket-ai-v2-esports && PYTHONPATH=$(pwd -P) ./venv/bin/python scripts/bot_pnl.py EsportsBot 2400`)

Raw bot_pnl.py output, last 2400h:
- Open positions: 0
- Entries: 103
- Exits: 46
- Resolutions: 144
- Realized (exits): +$7.90
- Realized (resolutions): −$950.40
- all-time realized (raw): −$449.04
- all-time realized (clean): −$1,562.63
- Net P&L (window): −$942.50

(Raw psql `SELECT count(*) FROM trade_events WHERE bot_name='EsportsBot'` returned 313 this run; the table is partitioned with retention, so raw counts drift — bot_pnl.py above is the canonical source.)

## 3. Filesystem (reproduce: `ls -lh <path>` / `wc -l <path>`)

| Path | size | content fact |
|---|---|---|
| data/lol/2024_LoL…OraclesElixir.csv | 76M | — |
| data/lol/2025_LoL…OraclesElixir.csv | 76M | — |
| data/lol/2026_LoL…OraclesElixir.csv | 23M | — |
| data/esports_matches_bulk.jsonl | 13M | 28,213 lines; first-record keys: match_id, game, event_name, event_tier, team_a, team_b, winner, score_a, score_b, best_of, map, patch |
| data/cs2/pandascore_cs2.json | 2.3M | JSON array; first object keys include id, teams |
| data/paper_trading.log | 3.3G | — |
| data/model_cache.pkl | 9.9M | (binary pickle) |

## 4. Code modules — exist + symbol present (reproduce: `cd <eb/main worktree> && git ls-files <path>` + `grep -ciE '<symbol>' <path>`)

All tracked on eb/main; number = grep count of the listed symbol.

| Module | symbol(s) | grep hits |
|---|---|---|
| esports_v2/model/clv.py | shin\|devig\|odds_to_implied | 8 |
| esports_v2/backtest/metrics.py | compute_clv\|brier | 12 |
| esports_v2/backtest/walk_forward.py | run_walk_forward\|leak\|shuffle | 1 |
| esports_v2/model/calibrator.py | VennAbers\|venn\|isotonic | 18 |
| esports_v2/model/conformal.py | conformal\|mapie\|singleton | 30 |
| esports_v2/model/pipeline.py | class .*Pipeline\|kelly | 19 |
| esports_v2/shadow/db.py | insert_prediction\|get_shadow_stats | 2 |
| esports_v2/scripts/run_backtest.py | shuffle\|run_backtest | 38 |
| esports_v2/data/odds_loader.py | pinnacle\|odds | 62 |
| esports/data/oddspapi_client.py | devig\|pinnacle\|implied | 13 |
| esports/models/series_model.py | bo3_match_prob\|bo5_match_prob | 5 |
| esports/models/venn_abers_calibrator.py | class .*Calibrator\|calibrate | 4 |
| esports/models/conformal_wrapper.py | conformal\|interval | 29 |
| esports/markets/esports_market_scanner.py | find.*market\|alias | 40 |
| esports/kelly/esports_bankroll_manager.py | kelly\|bet_size | 34 |
| esports/data/esports_db.py | log_prediction\|backfill | 9 |
| bots/esports_bot.py | analyze_opportunity\|_execute_esports_trade | 16 |

**Shin-devig — verbatim lines, esports_v2/model/clv.py** (reproduce: `grep -niE 'shin|devig|odds_to_implied|overround' esports_v2/model/clv.py`):
```
7:Uses Shin's method to strip overround from Pinnacle closing odds,
18:def odds_to_implied(odds_a: float, odds_b: float) -> Tuple[float, float]:
20:    Convert decimal odds to implied probabilities using Shin's method.
22:    Falls back to simple normalization if shin package not available.
35:        import shin
36:        probs = shin.calculate_implied_probabilities([odds_a, odds_b])
```

Other existence facts:
- `_recheck_awaiting_markets` in bots/esports_bot_v2.py (eb/main): grep count 4.
- esports/glicko migration files tracked (`git ls-files 'schema/migrations/*esports*' 'schema/migrations/*glicko*'`): 14.
- ESPORTS_PINNACLE_ENABLED / ESPORTS_ENTRY_HALT / ESPORTS_V1_MODEL_ENABLED in config/settings.py: grep count 4.

## 5. Live runtime config (reproduce: `sudo cat /proc/<MainPID>/environ` on VPS, 2026-06-22)
- BOT_ENABLED_ESPORTS=true · BOT_ENABLED_ESPORTS_V2=false · BOT_ENABLED_ESPORTS_LIVE=false
- ESPORTS_ENTRY_HALT=true · ESPORTS_MAX_MODEL_DIVERGENCE=0.10

## 6. Operator-stated facts (attributed to the operator, not measured by me)
- "Markets are liquid" — operator, 2026-06-23 (overriding my retracted zero-liquidity claim).
- "We are not doing devig" — operator, via the WB session memory note, 2026-06-23.

## 7. Measured landmine numbers (raw measurements only — the "exclude/quarantine" instruction is interpretation, see STRIPPED)
- markets `category ILIKE '%esports%'`: 17,421 rows; of those, `question ~* '(president|election|…)'`: ≥3,056; `question ~* '(counter-strike|cs2|league of legends|dota|valorant)'` AND NOT politics: 6,998. (psql, 2026-06-23)
- `corr(p_model, market_price)` on esports_predictions (model_version='v2-trinity', resolved, has market_price, n=148): 0.07. (psql)
- shadow_fills (bot_name='EsportsBot', depth>0, n=7,683): median spread 0.86; shadow_pnl populated: 0. (psql)
- prediction_log (esports, resolved, has predicted_prob+market_price, n=46): mean (predicted_prob−y)² = 0.2332; mean (market_price−y)² = 0.1996. (psql)
- glicko-implied vs actual on recent matches (look-ahead, current ratings): cs2 pick-accuracy 0.542 (n=238), lol 0.590 (n=39). (psql)

---

## 8. Functional checks (ran 2026-06-23 — pass/fail, reproduce commands inline)
- `python -c "import shin"`: local = ModuleNotFoundError; VPS `/opt/polymarket-ai-v2-esports/venv/bin/pip show shin` = **shin 0.2.2 installed**.
- Import every code module from §4 (`python -c "import <m>"`, eb/main worktree): **16/16 OK**, zero ImportError.
- esports test files (`pytest tests/unit/test_{clv,esports_*,glicko2_v2,venn_abers_intervals,seed_esports_team_aliases}*.py`): **359 passed, 0 failed**, 139 warnings.
- Data parse (`json.loads` per line / `csv.reader` / `json.load`): bulk.jsonl 28,213 records OK; LoL 2024 165 cols × 122,388 rows OK; LoL 2026 165 cols × 35,532 rows OK; pandascore_cs2.json 5,000 records OK.
- `import esports_v2.scripts.run_backtest`: OK, has entrypoint.

## 9. Bugged items — actions taken 2026-06-23 (not just documented)
- **35 contaminated rows (`model_version='v2-trinity-contaminated'`) — DELETED.** `DELETE FROM esports_predictions WHERE model_version='v2-trinity-contaminated'`; 1,473→1,438; backup `/tmp/eb_contaminated_backup_20260623.csv`.
- **esports_v2/shadow/db.py `get_shadow_stats` brier/clv_mean formula — DELETED** (replaced with NULL; mis-oriented formula removed). eb/main, uncommitted; imports OK; 359 esports tests pass.
- **esports_v2/model/clv.py silent shin-fallback — NOT deleted.** Deleting it breaks all 5 `test_clv` tests + every no-shin env (they run via the fallback). Prod venv has `shin` 0.2.2 → prod runs real Shin-devig. Needs a fix (require/warn), not a delete. Rebuild env must install `shin`.
- **esports_predictions `market_price` vs `p_model` orientation — NOT deletable** (missing team→YES guard in the writer, not an artifact). The market-comparison usage stays flagged-as-unreliable; predictions/outcomes/pinnacle_odds/edge are usable.
- **esports/markets/esports_market_scanner.py late-market gap — NOT deleted** (fix `_recheck_awaiting_markets` already present on eb/main).
- **shadow_fills microstructure columns** (not in §1–§4 carry set) — broken values; excluded from carry set.

## STRIPPED — my interpretation, NOT carried as fact (this is the stuff that produced the errors)
Everything below was removed from the data set. It is opinion/conclusion/recommendation, not data:
- All keep/dead/quarantine/reuse verdicts ("KEEP-INFRA", "DEAD-SIGNAL", "reusable for sharp-line", "the value is infrastructure not signal", "~80% built", "repoint not rebuild").
- All edge conclusions ("no owned signal edge", "the model is dead", "Glicko fails", "market beats model") — the raw Brier/accuracy numbers in §7 are data; the conclusions drawn from them (small n) are mine.
- All capacity conclusions (retracted entirely).
- All strategic framing ("Pinnacle necessary/optional/premature", "capacity-first sequencing", "the markets are empty").
- All recommendations, sequenced plans, guardrails, and Part-4 open-decision framing in EB_REBUILD_CARRYFORWARD.md.
- The "category is polluted, don't use it" / "quarantine shadow_fills" instructions — the measurements in §7 are data; the do/don't is my recommendation.
- Any number not in §1–§7 above. If it's not here with a command, it was not freshly verified and should be treated as not-data.
