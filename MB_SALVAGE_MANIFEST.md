# MirrorBot Salvage Manifest — what survives if MB is scrapped

> **Machine-readable index for other silos:** [`SALVAGE_PACKAGE.json`](SALVAGE_PACKAGE.json) (package id `mirrorbot-salvage`) — a foreign bot/agent parses that file to discover + consume these assets (paths, imports, CLI, SQL, deps, caveats). This `.md` is the human narrative.

**Date:** 2026-07-01 · **Deployed release:** `20260622_225148` (= commit `a6efa68`, MB code) · **master HEAD:** `b60b485` (esports-data commits on top; MB code unchanged) · **Method:** two read-only multi-agent stress-test workflows (`wf_aef66e8b-75e`, `wf_3c453b00-442`) + serial canonical-tool runs, all triangulated (≥2 independent sources per claim) and adversarially verified. DB was storm-crippled during the run, so all heavy DB access was funnelled through a single serial agent to protect the live bots.

**Provenance rules:** trading-state $/win-rate omitted unless `bot_pnl.py`-sourced; LIVE outcomes from `reconcile_live_onchain.py` (canonical §8); PG-table metadata / journalctl / config file:line cited inline. This is a code/data/tools inventory — no dollar P&L is asserted here.

---

## 0. Corrections to prior context (numbers that changed under measurement)

| Prior claim | Verified truth | Source |
|---|---|---|
| `mirror_rejected_signals` 17.5M rows | **~16.4M live rows** (reltuples); 17.5M is the id sequence | PG `pg_class.reltuples` + `MAX(id)` |
| `trade_events(MB)` "87% paper" | **row-composition inventory:** dataset is predominantly paper-mode; the `execution_mode='live'` partition is a small minority | PG `execution_mode` cardinality |
| positions `side` "191 paper SELL" | **column-integrity inventory:** ~1,421 paper + 55 live rows carry a corrupt `side='SELL'` string (recoverable via `token_id`) | PG `positions.side` cardinality |
| `whale_trades` "~30d rolling" | **DEAD snapshot, frozen 2026-03-18→19** (3.5mo stale) | PG MIN/MAX event_time |
| calibration files have an EB consumer | **FALSE** — only a docstring comment in `conformal_wrapper.py:82`, no import | import-graph grep |
| "3 tools work, 2 crash" | **5 sound, 3 broken** (shadow_analysis, slippage_check, mirror_whale_analysis) | live runs + code |
| clob_adapter "cost basis = signal price" | **True only for LIVE GTC**; paper mode records the real VWAP fill → paper P&L is accurate; the defect is **latent** | code + paper_trading.py:817-864 |

---

## Tier 1 — Shared infra (`base_engine/`): survives the scrap, a successor inherits it free

209/209 targeted unit tests passed (mock-based, no live DB). Verdicts:

| Module | Verdict | Note |
|---|---|---|
| `execution_engine.py` (CircuitBreaker) | **USABLE-AS-IS** | Best-hardened; CB-neutral-on-bad-order set (S245) prevents the kill-switch-storm class |
| `risk/bankroll_manager.py` | **USABLE-AS-IS** | Kelly math textbook-correct; used by other bots; MB's flat-$1 is a *caller/config* state, not a module defect |
| `data/rtds_websocket.py` | **USABLE-AS-IS** | 5s ping, 25s recv-timeout, dual reconnect — proven live (see Tier 5) |
| `data/database.py` Phase-4b backfill | **USABLE-AS-IS** | Side-agnostic dedup fix present + git-confirmed (`08c0b06`) |
| `main.py` watchdog + `coordination/kill_switch.py` | **USABLE-AS-IS** | S249 watchdog **validated firing in production** (caught the Jul-1 13:10 freeze); semaphore-bypass on every safety SELECT. Cosmetic: `kill_switch.py:13` TTL is 30s but docstrings say "5s" |
| `coordination/trade_coordinator.py` | **USABLE-WITH-FIX** | Delete dead `can_take_position` (0 hot-path callers — the real contention guard is `reserve_position` ON CONFLICT) |
| `execution/clob_adapter.py` | **USABLE-WITH-FIX** | **The one load-bearing debt:** live GTC returns the *submitted* price, not the fill ([clob_adapter.py:146](base_engine/execution/clob_adapter.py:146)) → cost-basis blind. LATENT (paper is accurate); activates on flip to live GTC. Fix = capture matched fill price into cost basis before `confirm_position` |

**The solid foundation:** CircuitBreaker/kill-switch/watchdog safety spine + Kelly bankroll math + RTDS feed + DB-atomic reservation. **Inherited debt = exactly two items** (clob_adapter fill-capture; dead `can_take_position`).

---

## Tier 2 — Data assets (persist in Postgres regardless)

| Asset | Verdict | Facts (PG metadata) |
|---|---|---|
| `mirror_rejected_signals` | **USABLE-WITH-HEAVY-CLEANING** | ~16.4M rows, 2026-04-22→07-01. **Labels are temporally-guarded** (backfill enforces `m.resolved_at >= mrs.event_time` — no look-ahead; 2 passing tests). Pseudo-replication ~27–42×. **Feature vector (`gate_score`/`kelly_prob`) lives only on `rejection_stage='gate'` rows, which are ~0.7% labeled** → the trainable-AND-feature-rich intersection is **low hundreds/day**, not millions |
| `orderbook_snapshots` | **USABLE-AS-IS (best asset)** | ~37.7M rows, full L2, live, **covers the entire signal window** → point-in-time fill-replay backtest feasible |
| `shadow_fills(MB)` | **USABLE for execution-mechanics; NOT for P&L** | 13,855 rows (5,823 executed), Apr 2→Jun 26. signal_price/vwap/slippage/fill_fraction ~100% populated; intended-vs-actual only 16%. **`shadow_pnl` is 100% NULL for MB** |
| `whale_movements` | **USABLE but THIN** | ~9,121 rows, Feb 17→Jul 1, all cols populated incl. `smart_money_rank`; only ~68/day → forward-collect for depth |
| `whale_trades` | **DO-NOT-REUSE** | Dead snapshot, frozen Mar 18→19; no `smart_money_rank` |
| `trade_events`/`positions(MB)` | **USABLE as live-outcome record** | live partition is small (mostly paper); `side='SELL'` column-integrity corruption (data-quality inventory: ~1,421 paper / 55 live rows) is **recoverable via `token_id`** — corruption is in the `side` string only, not position identity |

**Correct cleaning recipe for `mirror_rejected_signals`** (verifier-corrected): (1) dedup to **(trader, market, side) + timestamp/tx window** — NOT (trader,market), which collapses sides and legit re-entries; (2) filter `rejection_stage='gate'` for feature rows; (3) filter `resolution IS NOT NULL`; (4) apply the stale-price window **only to `gate`-stage rows** (pre_gate prices are the whale fill, unaffected by the `b3c26f7` bug — do NOT blanket-drop pre-06-13).

---

## Tier 3 — Tools/scripts

| Tool | Verdict |
|---|---|
| `bot_pnl.py` (MB path) | **RELIABLE** — canonical; the `:651` f-string bug is WB-gated, MB never reaches it |
| `reconcile_live_onchain.py` | **RELIABLE** — canonical for LIVE |
| `edge_verification.py` | **RELIABLE** — bootstrap edge (its contamination filter == `bot_pnl.py:140`) |
| `counterfactual_pnl.py` | **RELIABLE-WITH-CAVEAT** — carries its own bias warning (don't use for cap decisions) |
| `redeem_and_retrade.py` | **RELIABLE-WITH-CAVEAT** — standalone; neg-risk NegRiskAdapter path works; `--execute` moves money (operator-gated) |
| `shadow_analysis.py` | **BROKEN** — `db.initialize()` should be `db.init()` (1-word fix); also its P&L blocks are dead (shadow_pnl NULL) |
| `slippage_check.py` | **BROKEN** — binds a `str` cutoff to a TIMESTAMP column (asyncpg won't coerce); needs datetime parse |
| `mirror_whale_analysis.py` | **BROKEN** — queries non-existent `elite_traders` table; also a per-trader join over-attributes market P&L to every trader in the market |

---

## Tier 4 — MB-local logic worth extracting (files die; patterns portable)

**Caveat (verifier):** all these are **methods on `MirrorBot`**, not free functions — even the "self-contained" ones are copy-and-repoint-`self.base_engine`, not clean imports.

| Component | Verdict | Portability note |
|---|---|---|
| Leaderboard fetch/rank/select/refresh (`elite_watchlist.py:120-687`) | **EXTRACT-CLEAN** | aiohttp + 4 injected db/client methods; 23 tests run with `db=None`. Bugs: `profit_factor=inf` on zero-loss traders ([:347](bots/elite_watchlist.py:347)); ROI is OVERALL not per-category |
| `_fresh_side_price` ([:2823](bots/mirror_bot.py:2823)) | **EXTRACT-CLEAN** | Only deps `client.get_token_midpoint` + markets read; test confirms zero MB-state. Returns midpoint, not tradeable ask |
| `_confirm_zero_ctf_balance` + `_live_sell_balance_guard` ([:2126-2212](bots/mirror_bot.py:2126)) | **EXTRACT-CLEAN** | Single self-contained RPC helper (`check_ctf_balance`); **preserve the asymmetric fail-open (guard) / fail-closed (phantom-confirm) design** |
| Dedup guard *mechanisms* (capped-OrderedDict, monotonic-TTL cooldown, 2-layer guard, MM-detector) | **EXTRACT-CLEAN** | Extract the mechanisms; **discard the "one-bet-per-market" policy** (MB-specific product rule) |
| Copy hot-path handlers (`elite_watchlist.py:720-1005`) | **EXTRACT-WITH-REWORK** | Secretly calls 6 MB internals. Clean sub-patterns inside: side-resolution, wash/round-trip detector (3-bug-fixed, sound), RTDS price cache |
| `_close_position_terminal` ([:1869](bots/mirror_bot.py:1869)) | **EXTRACT-WITH-REWORK** | Pattern only; the gem = `known_zero_balance → realized_pnl=NULL` (0 tokens on-chain ⇒ any payout P&L is fictional) |
| `_restore_state_on_startup` ([:311](bots/mirror_bot.py:311)) | **EXTRACT-WITH-REWORK** | Reference impl of CLAUDE.md net-counter tier. **Carries a latent bug:** `_state_restored=True` set before the `_entered_market_sides` rebuild ([:496 vs :562-592](bots/mirror_bot.py:496)) → silent partial-restore; keep the `is_paper` mode-filter |
| `mirror_calibration.py` / `mirror_adaptive_safety.py` | **LEAVE (delete with scrap)** | Calibration is a thin shim (real logic in shared `base_engine/features/calibration.py`); adaptive-safety is gated OFF, never live-proven. **Both delete cleanly — no EB dependency** |

---

## Tier 5 — External avenues + signal-research "brains"

| Avenue / module | Verdict | Note |
|---|---|---|
| RTDS feed (`rtds_websocket.py`) | **ALIVE-RELIABLE** | Live now (~5,000 events/min; both reconnect paths exercised in-vivo). Config-driven endpoint. **Silent-break:** undocumented Polymarket socket schema (`payload`/`proxyWallet`) |
| Leaderboard `/v1/leaderboard` | **ALIVE-RELIABLE** | HTTP 200, shape-verified, running live (watchlist_size=300). `pnl`/`vol` are Polymarket-reported, not ground-truth edge |
| CLOB `/ok` + relayer + on-chain addrs | **ALIVE-RELIABLE** | Addresses multi-source-confirmed + S247 executed-tx-proven. **Silent-break:** `POLYGON_RPC` key has no public fallback → if it dies, redeem recovers **$0 silently** (same signature as a real "no winners" run) |
| `learning/wallet_clustering.py` | **USABLE-WITH-FIX** | Coordination/dedup graph reusable as-is (label-free); but `get_combined_rank` is a **size-only placeholder** — needs profit-factor ranking. Shared (used by `ensemble_bot`), survives scrap |
| `learning/venn_abers_intervals.py` | **USABLE-AS-IS (calibrator)** | Clean IVAP calibration lib; **not wired into any live bot**; calibrates a score, cannot create edge |
| `learning/prediction_drift.py` | **USABLE-AS-IS (monitor)** | ADWIN drift detector, live-wired to MB; safety asset, not a signal |
| `learning/scheduler.py` (LearningScheduler) | **USABLE-WITH-FIX** | Retrain orchestrator; 13-collaborator coupling. **⚠ FOOTGUN:** `_canary_auto_transition` auto-advances paper→live capital on a Brier window, `CANARY_AUTO_ADVANCE` default **True** — neutralize before any reuse |
| Config surface (`MIRROR_*` + `BOT_BANKROLL_CONFIG`) | **ALIVE** | ~55 env-overridable knobs. Code defaults (`max_bet_usd=300`, whale gate `$100`) diverge from runtime env (`1`, `$5`) — verify env before trusting either |

---

## What dies with the scrap (no measured value — do NOT carry forward)

- **The whale-copying strategy core** — `gate_score`/`kelly_prob` scoring ([mirror_bot.py:3602-3810](bots/mirror_bot.py:3602)), flat sizing, multi-factor confidence. Signal edge is not established (this audit: edge_verification indistinguishable from zero; no +EV wallet subset; confidence non-monotonic). Don't port the scoring.
- **RUBBLE** — dead slippage dicts, `can_take_position`, mode-flip guards, cache band-aid, dual ingestion path, `ensure_future` financial write-throughs.

---

## Silent-break dependency table (for a successor)

| Dependency | Silent-break mode | Detectability |
|---|---|---|
| `POLYGON_RPC` key (`redeem_and_retrade.py:110`, `clob_adapter.py:326+`) | key dies → `eth_call` fails → redeem recovers $0 | **POOR** (looks like a real no-winners run) |
| RTDS envelope schema (`rtds_websocket.py:219-230`) | Polymarket renames topic/field → connected-but-empty | Partial (watchdog catches total stall, not partial rename) |
| `CANARY_AUTO_ADVANCE=true` (`scheduler.py:579`) | auto-flips paper→live on a Brier window | **CAPITAL RISK** — verify flag |
| `/v1/leaderboard` path/fields | path/field change → watchlist→0 | Good (logged) |
| `prediction_log` cols (drift/venn-abers) | migration → silent unfitted/False | Poor (swallowed to debug) |

---

## If you actually delete the files — scrap cleanup checklist

1. Remove `MirrorBot` from `BOT_REGISTRY` ([main.py:81](main.py:81)).
2. Delete `bots/mirror_bot.py`, `bots/elite_watchlist.py`, `bots/mirror_calibration.py`, `bots/mirror_adaptive_safety.py` (calibration/adaptive confirmed EB-independent).
3. Prune name-based `"MirrorBot"` branches (dead, non-breaking): `database.py` resolution-backfill, `order_gateway.py`, `position_manager.py`, `resolution_backfill.py`, `multi_kill_switch.py`, `prometheus_exporter.py`, `learning/wallet_clustering.py`.
4. Keep `redeem_and_retrade.py` + timer (recovers any residual capital) until the wallet is drained.
5. Leave all `base_engine/` shared infra and all data tables intact.

---

## What could NOT be verified (constraint-bound — DB storm + read-only)

- Exact full-table labeled COUNT on `mirror_rejected_signals` (planner estimate ~80%, skewed old); resolution correctness vs an authoritative market source.
- Live `POLYGON_RPC` / `RELAYER_API_KEY` / `CANARY_STAGE` / `BOT_BANKROLL_CONFIG` env values.
- Whether drift/canary paths have ever fired live; Phase-4b convergence behavior (code+git only).
- Live V2 matched-order response shape (no live order placed).

All "usable" verdicts rest on code-read + ≥1 of {live log, live curl, importer-grep, schema-confirm, unit test}, with adversarial verification of the six load-bearing claims.
