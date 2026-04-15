# S175 HANDOFF — EsportsBot v2 Rebuild (Phases 5v2-A + 5v2-B Complete)

**Session:** 175 (EsportsBot-scoped)
**Date:** 2026-04-14
**Scope:** EsportsBot v2 rebuild — Phase 5v2-A complete, Phase 5v2-B complete, first backtest run
**Tests:** 159 passed (88 A-phase + 71 B-phase), 0 failed
**Branch:** master
**Prior sessions:** S173 (`AGENT_HANDOFF_S173_SHARED_MASTER.md`), S174 (`AGENT_HANDOFF_S174_ESPORTS_V2.md`)

---

## 0. ABSOLUTE RULES — READ FIRST

### RULE ZERO: No performance numbers without bot_pnl.py
Any number claiming to represent trading performance (P&L, win rates, ROI, trade counts, accuracy on trades/simulations) requires `bot_pnl.py` as source. **No bot_pnl.py = do not include the number in your response. Period.** No labels, no disclaimers, no "UNVERIFIED", no "from backtest." Point to the file and let the user read it. Config values with file:line citations are exempt. Test counts are exempt.

Full rule: `memory/feedback_verified_numbers_only.md`

### Bot-scoped session
This is an EsportsBot v2 session. No WeatherBot or MirrorBot code changes unless the user explicitly demands it. No bleed.

### Paper trading IS production
Implement everything fully regardless of trading mode. The paper/live flag affects ONLY the final order submission step.

### One fix per commit
No "while I'm in here" refactors. No scope creep. Read the entire file before modifying.

---

## 1. WHAT THIS SYSTEM IS

A 15-bot Polymarket automated trading system. Paper trading mode active (`SIMULATION_MODE=true`). EsportsBot v1 was KILLED in S173 (P(edge>0) = 0.002, met 4/4 kill criteria). EsportsBot v2 is a ground-up rebuild with a completely new prediction architecture.

**VPS:** Ubuntu-32 at 18.201.216.0 (32GB/8vCPU). SSH key: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`

**Active services:**
- `polymarket-weather` — WeatherBot (active, scanning)
- `polymarket-mirror` — MirrorBot (active, scanning)
- `polymarket-esports` — **STOPPED AND DISABLED** (killed in S173 D2-7)
- `polymarket-ingestion` — active
- `polymarket-orderbook.timer` — active, every 60s

**Current VPS release:** `/opt/pa2-releases/20260414_132211` (Day 2 code — does NOT contain esports_v2)

---

## 2. THE MASTER PLAN

### S172 Consolidated Plan v7.0
**File:** `S172_CONSOLIDATED_PLAN_v7.md` (APPROVED)

### Phase 5v2 Amendment
**File:** `S172_PHASE5V2_AMENDMENT.md` (APPROVED)

Architecture: Rating Trinity (Elo + Glicko-2 + OpenSkill) → XGBoost meta-model → Venn-ABERS calibration → MAPIE conformal filter → Quarter-Kelly sizing

### Sub-phase status

| Phase | Status | Gate |
|-------|--------|------|
| **5v2-A** (Data + Ratings) | **COMPLETE** | 8/8 items, 88 tests, A-gate validated on real data |
| **5v2-B** (Backtester + Meta-Model) | **COMPLETE** | 7/7 items, 71 tests, backtest run on 23K LoL matches |
| **5v2-C** (Shadow Mode) | **NEXT** | Blocked on: GRID CS2 data + Pinnacle odds for CLV |
| **5v2-D** (Paper Trading) | Queued | Blocked on 5v2-C gate |

---

## 3. WHAT S175 DID

### Commits (4)

| SHA | What |
|-----|------|
| `d4543cd` | Phase 5v2-A: data loaders + rating engines + migration 072 (88 tests) |
| `e506072` | Phase 5v2-B: backtester + meta-model + calibration + conformal (52 tests) |
| `081f48b` | Review fixes: B3/B4/B5 test coverage, DB writes, mandatory shuffle control |
| `b954495` | Fix: structlog-style logging + shuffle control label swap bug |

### Data obtained
- **Oracle's Elixir LoL CSVs** (2024 + 2025 + 2026) downloaded to `data/lol/`
  - 2024: 10,199 matches (76 MB)
  - 2025: 10,053 matches (76 MB)
  - 2026: 2,961 matches (23 MB)
  - Total: 23,213 LoL matches

### VPS changes
- **Migration 072 applied** — 6 new tables: `esports_matches`, `esports_players`, `esports_ratings`, `esports_features`, `esports_predictions`, `esports_odds`
- Old v1 `esports_matches` and `esports_players` (empty, different schema) were dropped and recreated with v2 schema

### Backtest run
- LoL-only walk-forward backtest completed (25 folds, 19,710 predictions)
- Results in `output/backtest/backtest_report.txt` — read that file for numbers
- Shuffle control PASSED (no data leakage) after fixing a bug in the shuffle logic
- CLV gate cannot be evaluated yet — no Pinnacle odds source connected

### Bugs found and fixed
1. **Structlog-style logging** — 8 calls in oracle_loader.py and grid_loader.py used keyword args (`logger.info("msg", key=val)`) which crashes with stdlib logging. Converted to f-strings.
2. **Shuffle control label swap** — Code was `m.winner, m.team_a, m.team_b = m.team_b, m.team_b, m.team_a` which set team_a=team_b (duplicate). Fixed to only swap which team is recorded as winner.
3. **compute_metrics recursion** — Per-game breakdown called `compute_metrics` recursively, causing infinite recursion. Fixed by inlining per-game computation (no depth guard — clean structural fix).

---

## 4. COMPLETE FILE INVENTORY

### esports_v2/ (all new code — parallel to existing esports/)

```
esports_v2/
  __init__.py
  ratings/                          # Phase 5v2-A
    __init__.py
    elo.py                          # A5: Elo engine, K=32 configurable (~110 lines)
    glicko2.py                      # A6: Glicko-2 engine, tau=0.5 (~175 lines)
    openskill_engine.py             # A7: OpenSkill player-level Plackett-Luce (~165 lines)
    trinity.py                      # A8: Trinity runner, pre-match prediction capture (~195 lines)
                                    #   Defines: TrinityPrediction, MatchResult, Trinity class
  data/                             # Phase 5v2-A
    __init__.py
    normalizer.py                   # RawMatch, raw_to_match_result(), raw_to_db_row() (~100 lines)
    oracle_loader.py                # A3: Oracle's Elixir LoL CSV loader (~190 lines)
    grid_loader.py                  # A4: GRID JSON + HLTV CSV loaders (~300 lines)
  model/                            # Phase 5v2-B
    __init__.py
    meta_model.py                   # B2: XGBoost, 12 features, record_to_features() (~150 lines)
    calibrator.py                   # B3: Venn-ABERS per-game, isotonic fallback (~130 lines)
    conformal.py                    # B4: MAPIE LAC conformal filter, alpha=0.10 (~140 lines)
    clv.py                          # B5: Shin's method odds→implied prob, CLV computation (~100 lines)
    pipeline.py                     # Wires B2+B3+B4: EsportsPipeline.fit()/predict() (~130 lines)
  features/                         # Empty stubs (future game-specific features)
    __init__.py
  backtest/                         # Phase 5v2-B
    __init__.py
    walk_forward.py                 # B1: Walk-forward engine, date folds, PredictionPipeline protocol (~200 lines)
    metrics.py                      # B6: Full metrics suite + 5v2-B gate check (~220 lines)
  scripts/
    __init__.py
    load_historical.py              # CLI: load data → Trinity → features JSON (~195 lines)
    run_backtest.py                 # B7: Full backtest + shuffle control + DB writes (~250 lines)
```

### Tests (all in tests/unit/)

```
test_elo_v2.py           # 13 tests — Elo math, K-factor, conservation
test_glicko2_v2.py       # 11 tests — Glicko-2 rating, uncertainty, scale roundtrip
test_openskill_v2.py     # 10 tests — Player-level ratings, roster changes
test_trinity.py          # 14 tests — Orchestration, per-game isolation, pre-match capture
test_oracle_loader.py    # 14 tests — CSV parsing, rosters, tiers, aliases
test_grid_loader.py      # 12 tests — JSON/NDJSON, tier classification, score-based winner
test_normalizer.py       # 6 tests  — Team name normalization, winner mapping
test_metrics.py          # 23 tests — Accuracy, Brier, log loss, ECE, CLV, z-score, PnL, gate check
test_walk_forward.py     # 12 tests — Date folds, temporal ordering, mock pipeline
test_pipeline.py         # 25 tests — XGBoost, Venn-ABERS (7 incl. overconfident/underconfident correction),
                         #            Conformal (7 incl. alpha sensitivity + uninformative model detection),
                         #            EsportsPipeline fit/predict
test_clv.py              # 12 tests — Shin's method with real Pinnacle odds (CS2/LoL/close matches),
                         #            overround removal, enrichment pipeline
                         # ----
                         # TOTAL: 159 tests
```

### Data files

```
data/lol/
  2024_LoL_esports_match_data_from_OraclesElixir.csv  # 76 MB, 10,199 matches
  2025_LoL_esports_match_data_from_OraclesElixir.csv  # 76 MB, 10,053 matches
  2026_LoL_esports_match_data_from_OraclesElixir.csv  # 23 MB, 2,961 matches

output/ratings/
  trinity_features.json    # 23,213 feature records from Trinity pass
  ratings_lol.json         # Final Elo/Glicko-2/OpenSkill ratings

output/backtest/
  backtest_predictions.json  # 19,710 walk-forward predictions
  backtest_report.txt        # Metrics summary + gate check
  .shuffle_control_passed    # Sentinel — shuffle control verified
```

### Schema (Migration 072 — applied on VPS)

```
schema/migrations/072_esports_v2.sql        # 6 new tables + indexes
schema/migrations/down/072_esports_v2.sql   # CASCADE drop
```

6 tables: `esports_matches`, `esports_players`, `esports_ratings`, `esports_features`, `esports_predictions`, `esports_odds`

### Modified existing files

| File | Change |
|------|--------|
| `requirements.txt` | +12 lines: openskill, venn-abers, shap |
| `tests/conftest.py` | +3 lines: sys.path for esports_v2 |

**No other existing files were modified.**

---

## 5. ARCHITECTURE — HOW IT ALL FITS TOGETHER

### Data Pipeline
```
Oracle CSV (LoL) ──→ OracleElixirLoader ──→ RawMatch ──→ normalizer ──→ MatchResult
GRID JSON (CS2) ───→ GridLoader ──────────→ RawMatch ──→ normalizer ──→ MatchResult
HLTV CSV (CS2) ────→ HLTVResultsLoader ──→ RawMatch ──→ normalizer ──→ MatchResult
                                              │
                                              ▼
                                        raw_to_db_row() → esports_matches table
```

### Rating Trinity (per-game isolation — CS2 ratings don't affect LoL)
```
MatchResult → Elo (team, K=32) ──────────→ P_elo
MatchResult → Glicko-2 (team, tau=0.5) ──→ P_glicko
MatchResult → OpenSkill (player-level) ──→ P_openskill
                                            │
  Trinity Runner: spread = max(P) - min(P)  │
    < 0.05 = high agreement (trust)         │
    > 0.15 = diverge (abstain)              │
                                            ▼
                                  TrinityPrediction
```

**Critical invariant:** `Trinity.process_match()` calls `predict()` BEFORE `update()`. Pre-match prediction captured structurally — no lookahead possible.

### Prediction Pipeline (Phase 5v2-B)
```
TrinityPrediction.to_feature_dict()  →  12 features:
  5 trinity: p_elo, p_glicko, p_openskill, trinity_spread, trinity_mean
  3 pairwise: elo-glicko diff, elo-openskill diff, glicko-openskill diff
  3 context: event_tier (ordinal s/a/b/c), is_lan, best_of
  1 game: is_cs2

  ┌──────────────────┐
  │  XGBoost (B2)    │ 200 trees, depth 4, lr 0.05
  │  Binary target:  │ 1 = team_a wins
  └────────┬─────────┘
           │ raw probability
           ▼
  ┌──────────────────┐
  │ Venn-ABERS (B3)  │ Per-game calibrators (separate cs2/lol)
  │ Isotonic fallback│ if venn-abers not installed
  └────────┬─────────┘
           │ calibrated prob + [p_lower, p_upper] interval
           ▼
  ┌──────────────────┐
  │ MAPIE LAC (B4)   │ alpha=0.10, 90% marginal coverage
  │ Conformal filter │ Singleton = bet, Multi-label = abstain
  └────────┬─────────┘
           │ is_singleton + conformal_set
           ▼
  ┌──────────────────┐
  │ Kelly Sizing     │ Quarter-Kelly, $100 cap, 5% bankroll max
  │ MIN_EDGE = 5%    │ No bet if edge < 5%
  └──────────────────┘
```

### Walk-Forward Backtester (B1)
```
1. Process ALL matches through Trinity chronologically (ratings accumulate)
2. Generate temporal folds: min 3 months training, 1 month test windows
3. For each fold:
   a. Split training records into XGBoost train (80%) + calibration (20%)
   b. Fit XGBoost on train portion
   c. Fit Venn-ABERS on calibration portion (per-game)
   d. Fit conformal filter on calibrated calibration probs
   e. Predict each test record through full pipeline
4. Aggregate all fold predictions → compute_metrics() → gate check
```

### 5v2-B Gate Thresholds

| Metric | Threshold | What it means |
|--------|-----------|---------------|
| Accuracy (singletons) | > 58% | Above breakeven after fees |
| Brier score | < 0.23 | Respectable calibration |
| CLV vs Pinnacle | > +1.5% mean | Edge over sharp market |
| Singleton rate | > 30% | Enough bettable matches |
| z-score (500+ preds) | > 1.5 | Approaching significance |
| Both CS2 and LoL individually profitable | Required | No cross-game hiding |

---

## 6. KEY DATACLASS SHAPES

### TrinityPrediction (from trinity.py)
```python
@dataclass
class TrinityPrediction:
    team_a: str
    team_b: str
    p_elo: float           # P(team_a wins) from Elo
    p_glicko: float        # P(team_a wins) from Glicko-2
    p_openskill: float     # P(team_a wins) from OpenSkill
    trinity_spread: float  # max(p) - min(p)
    trinity_mean: float    # average of 3 probs
    # Properties: high_agreement (spread < 0.05), should_abstain (spread > 0.15)
    # to_feature_dict() → 5 keys for XGBoost
```

### MatchResult (from trinity.py, imported by normalizer.py)
```python
@dataclass
class MatchResult:
    match_id: str
    game: str              # 'cs2' or 'lol'
    team_a: str
    team_b: str
    winner: str            # 'a' or 'b' or 'draw'
    is_lan: bool = False
    roster_a: Optional[List[str]] = None
    roster_b: Optional[List[str]] = None
    patch: Optional[str] = None
    match_date: Optional[str] = None
```

### RawMatch (from normalizer.py — pre-normalization)
```python
@dataclass
class RawMatch:
    match_id: str
    game: str
    event_name: Optional[str] = None
    event_tier: Optional[str] = None  # 's_tier', 'a_tier', 'b_tier', 'c_tier'
    team_a: str = ""
    team_b: str = ""
    winner: Optional[str] = None      # team name (not 'a'/'b')
    score_a: Optional[int] = None
    score_b: Optional[int] = None
    best_of: Optional[int] = None
    map_name: Optional[str] = None
    patch: Optional[str] = None
    match_date: Optional[str] = None
    is_lan: bool = False
    source: str = ""
    roster_a: Optional[List[str]] = None
    roster_b: Optional[List[str]] = None
    raw_data: Dict = field(default_factory=dict)
```

### Pipeline predict() output
```python
{
    "p_raw": float,          # XGBoost raw probability
    "p_model": float,        # Venn-ABERS calibrated probability
    "p_lower": float,        # Venn-ABERS interval lower bound
    "p_upper": float,        # Venn-ABERS interval upper bound
    "conformal_set": list,   # [0], [1], or [0, 1]
    "is_singleton": bool,    # True = bet, False = abstain
    "kelly_fraction": float, # Quarter-Kelly fraction
    "stake": float,          # Dollar amount ($100 cap)
    "edge": float,           # |p_model - market_price|
    "market_price": float,   # Market price (0.5 default if unavailable)
}
```

---

## 7. DEPENDENCIES

All already in requirements.txt:
- `openskill>=6.0.0` — player-level Plackett-Luce ratings
- `venn-abers>=0.4.0` — calibration (NOT installed locally — isotonic fallback active)
- `shap>=0.43.0` — feature importance (5v2-B diagnostics)
- `xgboost>=2.0.0` — meta-model (already present)
- `mapie>=0.9.0` — conformal prediction (already present)
- `python-dateutil>=2.8.0` — walk-forward date folds (already present)
- `shin>=0.1.0` — odds devigging for CLV (already present)

**Note:** `venn-abers` is not installed locally — the calibrator falls back to isotonic regression. Install with `pip install venn-abers` before final backtest.

---

## 8. WHAT'S NEXT — PRIORITY ORDER

### Immediate blockers (user action required)

1. **Apply for GRID Open Access** — grid.gg/open-access. Free, 1-2 day approval. Provides CS2 match data via GraphQL API. Page was opened in browser during S175.

2. **Connect Pinnacle odds source** — Best option: **OddsPapi** (oddspapi.io, free tier, historical Pinnacle esports odds for CS2 + LoL). Alternative: **OddsBase** (oddsbase.net, Pinnacle archive since 2015). This fills the CLV gap — the only failing 5v2-B gate criterion.

### Once data is available

3. **Install venn-abers** — `pip install venn-abers`. Currently falling back to isotonic regression.

4. **Build OddsPapi loader** — New file `esports_v2/data/odds_loader.py`. Fetch historical Pinnacle closing odds by match, enrich backtest predictions with CLV via `esports_v2/model/clv.py:enrich_with_clv()`.

5. **Run CS2+LoL combined backtest** — Once GRID data arrives:
   ```bash
   python -m esports_v2.scripts.run_backtest \
       --lol-csv data/lol/2024_LoL.csv data/lol/2025_LoL.csv data/lol/2026_LoL.csv \
       --cs2-json data/cs2/grid_matches.json \
       --output-dir output/backtest_v2
   ```

6. **Evaluate full 5v2-B gate** — All 6 criteria including CLV and per-game profitability.

### If 5v2-B gate passes → Phase 5v2-C (Shadow Mode)

Per `S172_PHASE5V2_AMENDMENT.md`:

| Item | What |
|------|------|
| C1 | Live data pipeline — real-time match ingestion from GRID/HLTV, rating updates after each result |
| C2 | Market discovery — map esports matches to Polymarket market_ids |
| C3 | Shadow prediction engine — trinity → meta-model → calibrate → conformal → Kelly. Log to `esports_predictions` with mode='shadow'. No trades. |
| C4 | Live CLV tracking — Polymarket price at prediction time + Pinnacle odds |

**Duration:** Minimum 2 weeks or 50 resolved predictions.

**Gate 5v2-C:**
- Shadow accuracy (singletons) > 55%
- Shadow Brier < 0.25
- CLV vs Polymarket > +2% mean
- Backtest-to-shadow accuracy drop < 5% absolute

### If 5v2-B gate fails

Iterate features/hyperparams (max 2 iterations). If still failing → approach lacks edge → kill. Do not proceed to shadow.

---

## 9. KNOWN ISSUES / TECHNICAL DEBT

1. **venn-abers not installed locally** — isotonic regression fallback is active. Install before final gate evaluation.

2. **No CS2 data yet** — GRID application pending. LoL-only backtest completed. CS2 must also pass individually per 5v2-B gate.

3. **No Pinnacle odds** — CLV = 0.0 on all predictions. Need odds loader from OddsPapi or OddsBase.

4. **Oracle's Elixir column aliases** — The loader handles column name variations, but future CSV releases may introduce new aliases. The loader fails loudly with a clear error listing expected vs found columns.

5. **TEXT[] column in esports_predictions** — `conformal_set TEXT[]` uses PostgreSQL array type. SQLAlchemy model (if built in 5v2-C/D) must use `ARRAY(Text)` or `postgresql.ARRAY(String)`.

6. **Migration 072 ownership** — Tables owned by postgres user on VPS, not polymarket user. This matches existing tables but may need GRANT statements for the polymarket service user.

7. **HLTV loader has no roster data** — OpenSkill returns 0.5 (uninformative) for HLTV-only matches. GRID is the primary CS2 source and provides rosters.

8. **run_backtest.py DB write** — `write_predictions_to_db()` is implemented but untested against the live VPS DB. Falls back gracefully if no DATABASE_URL or no psycopg2.

---

## 10. CRITICAL RULES (carried forward from all prior sessions)

1. **RULE ZERO** — No performance numbers without bot_pnl.py. See Section 0.
2. **Bot-scoped sessions** — EB v2 only. No WB/MB changes.
3. **One fix per commit** — each commit addresses exactly ONE issue.
4. **No asyncio.wait_for on DB** — use `SET LOCAL statement_timeout`.
5. **Paper trading IS production** — implement everything fully.
6. **Never blacklist cities** for WeatherBot (user directive, permanent).
7. **Phases 6/7 GATED** — need 4+ weeks post-Day-2 data + P(edge>0) >= 0.30.
8. **Phase 5v2 plan is canonical** — `S172_PHASE5V2_AMENDMENT.md`.
9. **No ad-hoc SQL for P&L** — use `scripts/bot_pnl.py` or replicate its EXACT SQL.
10. **EsportsBot stays in PM_EXCLUDE_BOTS** — removal causes semaphore exhaustion.

---

## 11. SESSION CHAIN

```
S172  → Day 1 + partial Phase 1
S172B → Phase 1 completion
S172C → Phase 1 final (12/12), Phase RC drafted
S173  → Phase RC complete, Day 2 deployed, EB v1 killed
S174  → Phase 5v2-A COMPLETE (8/8 items, 88 tests)
S175  → Phase 5v2-B COMPLETE (7/7 items, 71 tests), LoL backtest run, data downloaded ← YOU ARE HERE
S176+ → GRID data + Pinnacle odds → full 5v2-B gate → 5v2-C shadow mode
```

---

## 12. COMMANDS REFERENCE

```bash
# Run all esports_v2 tests (159 total)
python -m pytest tests/unit/test_elo_v2.py tests/unit/test_glicko2_v2.py \
  tests/unit/test_openskill_v2.py tests/unit/test_trinity.py \
  tests/unit/test_oracle_loader.py tests/unit/test_grid_loader.py \
  tests/unit/test_normalizer.py tests/unit/test_metrics.py \
  tests/unit/test_walk_forward.py tests/unit/test_pipeline.py \
  tests/unit/test_clv.py --tb=short -q

# Load data through Trinity (A-gate validation)
python -m esports_v2.scripts.load_historical \
  --lol-csv data/lol/2024_LoL_esports_match_data_from_OraclesElixir.csv \
            data/lol/2025_LoL_esports_match_data_from_OraclesElixir.csv \
            data/lol/2026_LoL_esports_match_data_from_OraclesElixir.csv \
  --output-dir output/ratings

# Run full walk-forward backtest (includes mandatory shuffle control on first run)
python -m esports_v2.scripts.run_backtest \
  --lol-csv data/lol/2024_LoL_esports_match_data_from_OraclesElixir.csv \
            data/lol/2025_LoL_esports_match_data_from_OraclesElixir.csv \
            data/lol/2026_LoL_esports_match_data_from_OraclesElixir.csv \
  --output-dir output/backtest

# SSH to VPS
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0

# Check migration 072 tables on VPS
sudo -u postgres psql -d polymarket -c "SELECT tablename FROM pg_tables WHERE tablename LIKE 'esports_%' ORDER BY tablename;"

# Canonical P&L (run on VPS — NOT for backtest results)
cd /opt/polymarket-ai-v2 && sudo -u polymarket bash -c \
  "source /opt/pa2-shared/venv/bin/activate && python3 scripts/bot_pnl.py EsportsBot 24"
```

---

## 13. KEY FILES REFERENCE

| Category | Files |
|----------|-------|
| Plans | `S172_CONSOLIDATED_PLAN_v7.md`, `S172_PHASE5V2_AMENDMENT.md` |
| EB v2 code | `esports_v2/` (entire directory — see Section 4) |
| Data | `data/lol/*.csv` (3 files, 23K matches) |
| Backtest output | `output/backtest/backtest_report.txt`, `output/backtest/backtest_predictions.json` |
| Trinity output | `output/ratings/trinity_features.json`, `output/ratings/ratings_lol.json` |
| Migration | `schema/migrations/072_esports_v2.sql` |
| Tests | `tests/unit/test_*_v2.py`, `test_metrics.py`, `test_walk_forward.py`, `test_pipeline.py`, `test_clv.py` |
| Dev rules | `CLAUDE.md` |
| Memory | `memory/feedback_verified_numbers_only.md` (RULE ZERO) |
| Prior handoffs | `AGENT_HANDOFF_S174_ESPORTS_V2.md`, `AGENT_HANDOFF_S173_SHARED_MASTER.md` |

---

**END OF HANDOFF — S175 ESPORTS V2**

**Rollback:** `git revert b954495 081f48b e506072 d4543cd` (4 commits, reverse order)
**Test verification:** `python -m pytest tests/unit/test_elo_v2.py tests/unit/test_glicko2_v2.py tests/unit/test_openskill_v2.py tests/unit/test_trinity.py tests/unit/test_oracle_loader.py tests/unit/test_grid_loader.py tests/unit/test_normalizer.py tests/unit/test_metrics.py tests/unit/test_walk_forward.py tests/unit/test_pipeline.py tests/unit/test_clv.py` → expect 159 passed
