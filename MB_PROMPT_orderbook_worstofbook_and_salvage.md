# MB SESSION PROMPT — orderbook worst-of-book fix + salvage-data integrity + landmine

**Origin:** EB-session cross-bot data audit, 2026-07-14 (operator-authorized). Two
worst-of-book bugs found, fixed, and adversarially verified on branch
`claude/mb-orderbook-collector-fix` (2 commits off master `13ce512`:
`61541c5` collector, `d2f5c2f` ingestion). This prompt hands the MB-owned parts to you.

**Why this is yours:** `orderbook_snapshots` is consumed by `bots/mirror_backtest/`
(replay, fill_models, data_access) and `bots/mirror_scoring/tailability.py`, and the
in-flight MB v3 rebuild is actively salvaging this exact table (commits `421c5f1`
M0-DB shape assertion on orderbook_snapshots, `78847f0` salvage verification). An EB
session must not deploy into your migration or truncate a table your rebuild is using.

---

## 1. CRITICAL — the salvage data is 100% worst-of-book garbage

`scripts/orderbook_collector.py` took `bids[0]/asks[0]` from the raw CLOB `/book`
response as best bid/ask. The raw feed sorts **bids ASCENDING, asks DESCENDING** — `[0]`
is the WORST level. Verified live 2026-07-14: gamma `bestBid/bestAsk` == `bids[-1]/asks[-1]`.

**Consequence:** every row of `orderbook_snapshots` (43.8M, 2026-04-13 → fix deploy) has:
- `spread` ≈ 0.997 (avg, EVERY month; 0 rows with spread ≤ 0.15 across all 43.8M)
- `best_bid` ≈ 0.001, `best_ask` ≈ 0.999, `mid_price` ≈ 0.5
- `bid/ask_depth_1pct/5pct` computed within 1%/5% of that phantom 0.5 mid → garbage
- `imbalance` summed the WORST 5 levels per side → garbage

**Action for the M0-DB / acceptance-gate work:** any shape assertion, salvage backtest,
tailability score, or acceptance gate built on `orderbook_snapshots` touch/spread/depth
is invalid. Re-derive after clean data exists. The fix is verified (10 collector tests +
5 ingestion tests; live parse matches gamma exactly; 4-agent adversarial review — see §5).

## 2. Deploy decision (fixes are MB-owned; ship through YOUR stream)

Both fixed files are MB-territory (`orderbook_collector.py`, base_engine
`data_ingestion.py`). DO NOT expect the EB session to deploy — `deploy.sh` ships the
whole tree + restarts all 4 services, and master is 20 commits ahead of the live release
(`20260622_225148`) including your unfinished v3 rebuild. Options:
- **(a) Fold into v3 deploy (recommended):** cherry-pick `61541c5`+`d2f5c2f` onto your v3
  branch; they ship when v3 ships. The collector fix only needs the file updated (the
  `polymarket-orderbook.timer` re-execs `orderbook_collector.py --once` each tick — no bot
  restart). The ingestion fix needs `polymarket-ingestion` restarted (data-plumbing, no
  capital, low-risk).
- **(b) Surgical hotfix now:** push just those 2 files to the live release tree + restart
  ONLY `polymarket-ingestion`. Ships both fixes with zero MB/WB/EB restart. Breaks the
  immutable-release model (files diverge from the release tarball) — acceptable as a
  documented hotfix, your call.
- **Preflight is a HARD BLOCKER right now (empirically confirmed):** `deploy.sh` runs
  `pytest tests/unit/` and aborts on any failure. Running that exact command 2026-07-14
  gives `4 failed, 3744 passed` — 4 PRE-EXISTING order-dependent failures in
  `tests/unit/test_weather_bot.py` (`TestWeatherConfidenceCalibrator::test_no_db_returns_false`,
  `::test_brier_guard_rejects_worse_calibration`, `TestZeroKellyGuard::test_zero_kelly_returns_false`,
  `::test_zero_kelly_logs_shadow_entry`). Identical 4 fail on CLEAN master with these fixes
  reverted; they PASS in file-isolation (243/243) → test-ordering pollution, not a real
  regression. **Consequence: `deploy.sh` currently ABORTS at preflight for ANY deploy,
  including your v3 deploy, until this pollution is fixed or the preflight is scoped.**
  This is a standalone MB/infra blocker worth fixing regardless of the orderbook work.

## 3. Historical rows — truncate + flag (operator wants both; held for YOU)

Operator directive is truncate AND flag, but the table is under your active salvage work,
so the EB session did NOT execute it. Recommended once the collector fix is live:
```sql
-- after the fixed collector is deployed and writing good rows:
TRUNCATE TABLE orderbook_snapshots;   -- 100% of existing rows are worst-of-book
COMMENT ON TABLE orderbook_snapshots IS
  'Rows before <fix-deploy ts> were WORST-of-book (collector bug, fixed 61541c5,
   2026-07-14); table truncated at reset. All rows after are best-of-book.';
```
If your salvage work still needs the old rows for any structural (non-price) purpose,
DELETE WHERE snapshot_time < '<deploy ts>' instead, keeping post-fix good rows. Your call
— you own the table and know what M0-DB still reads from it.

## 4. Dormant landmine — position_manager CLOB mark fallback

`base_engine/execution/position_manager.py:746-784` (Session-51 CLOB price fallback for
positions missing from `market_prices`, e.g. MirrorBot) reads a RAW book and takes
`bids[0]/asks[0]`. It is currently a **guarded no-op**: the `spread < 0.5` sanity check at
:766 rejects the 0.998 garbage every time, so it has NEVER updated a mark. If anyone
"fixes" the book parsing there, it ACTIVATES live MB mark-to-market for those positions —
a real behavior change (uPnL jumps, possible exit triggers). Decision for you: (a) leave
as documented no-op, (b) fix parsing + accept the activated live behavior (own the exit
implications), or (c) remove the dead fallback. Do NOT let it get "repaired" by accident.

## 5. Verification already done (don't redo)

- Collector fix (`61541c5`): 10 unit tests (`tests/unit/test_orderbook_collector_parsing.py`);
  `parse_book_metrics` run on the LIVE Argentina-WC book matches gamma bestBid/bestAsk.
- Ingestion fix (`d2f5c2f`): 5 unit tests (`tests/unit/test_data_ingestion_best_bid_ask.py`).
- 4-agent adversarial review (high effort): both fixes CORRECT; all "safe/dormant"
  classifications independently confirmed (OrderBookTracker `_aggregate_levels` re-sorts
  unconditionally → order_gateway shadow-walk, mirror/weather exit revalidation all read
  SORTED snapshots, VWAP helpers re-sort internally too → `shadow_fills` data is CLEAN).
  Residual on the collector: 2 LOW-sev PRE-EXISTING non-regressions left untouched
  (negative spread on an unreachable crossed book; one-sided book records depth 0.0).
- Full suite: 3945 passed / 4 failed (the 4 pre-existing weather pollution above).

Detail + the full site-classification table: memory `project_mm_feasibility_study.md`.
