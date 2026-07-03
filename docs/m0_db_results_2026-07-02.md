# M0-DB Results — MirrorBot salvage data verification (2026-07-02)

**Source:** `scripts/verify_salvage_data.py` run on the VPS against the live `polymarket` DB
(gentle default, then `--timeout 60`; no `--exact`). Cascaded rows re-run on fresh
connections with the script's exact SQL. **These figures are the canonical source for the
data-tier facts and supersede the salvage manifest's estimates** (Forbidden Patterns 7/8/10).

This clears the **M0-DB gate** (`MB_REBUILD_PLAN.md` M2): data-dependent rebuild work may
proceed, with the two flagged caveats below.

## Verified — assets that hold

| Asset | Measured | Manifest claim | Verdict |
|---|---|---|---|
| `orderbook_snapshots` rows | **37,724,068** | ~37.7M | ✅ PASS |
| `orderbook_snapshots` window | 2026-04-13 → 2026-07-02 | covers signal window | ✅ |
| `orderbook_snapshots` **shape** | **aggregated buckets, no per-level ladder** | (correction) | ✅ **confirms the 2026-07-02 L2 correction on the live DB** |
| `mirror_rejected_signals` rows | **17,589,336** (reltuples) | ~16.4M / id-seq ~17.5M | ✅ PASS (refutes the "~12M" figure — off by ~5.5M) |
| `mirror_rejected_signals` window | 2026-04-22 → 2026-07-02 | 04-22→07-01 | ✅ |
| `whale_movements` rows | **9,332** | ~9,121 | ✅ PASS |
| `whale_movements` smart_money_rank | **100% populated** | all cols populated | ✅ |
| `shadow_fills(MB)` total | **12,892** | 13,855 | ✅ PASS (in range) |
| `shadow_fills(MB)` executed | **5,208** | 5,823 | ✅ PASS (in range) |
| `shadow_fills(MB)` shadow_pnl NULL | **100%** | 100% → not P&L-usable | ✅ PASS |
| `positions(MB)` side='SELL' live | **55** | ~55 | ✅ PASS |

## The two numbers that were the whole point

- **Gate-stage labeled intersection = 286,293** (`rejection_stage='gate' AND resolution IS NOT NULL`).
  This is the figure the original manifest listed as **never verified**. Total gate rows = 2,099,395.
  Per-day (recent): 07-02=23, 06-30=1, 06-29=2, 06-28=26, 06-27=83, 06-26=123, 06-25=353 —
  **"low hundreds/day" confirmed, and highly variable (some days near zero).**
- **Precise-model sample size = 12,713** `shadow_fills(MB)` rows carry a stored L2 ladder
  (`book_snapshot IS NOT NULL`). This is the ceiling on the acceptance harness's PRECISE-model
  coverage — the intersection of these with labeled signals sets how many markets a rule can
  actually be gated on.

## ⚠ Flags — do not paper over

1. **`positions(MB)` side='SELL' paper = 191 — FAILS the manifest's ~1,421 claim** (range 800–3,000).
   Live shows 191, which matches the *original* prior figure the 2026-07-01 audit claimed to
   "correct" upward to ~1,421. Either the audit's upward correction was wrong or the table was
   pruned since. **The ~1,421 figure is not reproducible; live is 191.** Manifest corrected.
2. **Gate-deduped (tx-window) still unmeasured** — `DISTINCT (trader, market, side, hour)` over
   286,293 rows times out even at 60s. The true trainable sample after tx-window dedup is
   **the Path-B (whale-label training) viability number** and remains open. Rough estimate:
   labeled ÷ manifest's ~27–42× pseudo-replication ≈ **~7k–10k unique labeled gate signals**
   — order-of-magnitude only, NOT a verified count. A materialized/offline dedup is needed to
   settle it.
3. **Script cascade bug (fixed this commit):** the run showed all rows after the first heavy
   timeout cascading to TIMEOUT/ERR — a caught error left the transaction aborted. Fixed by
   rolling back on error (`_scalar`/`_reset`); a re-run will resolve the small-table rows on the
   same session instead of needing fresh connections.

## Runtime env (M-ENV inputs; secrets reported presence-only)

| Key | Runtime value | Note |
|---|---|---|
| `SIMULATION_MODE` | **false (LIVE)** | the production MB is live |
| `CANARY_STAGE` | **4 (=100%)** | full live exposure |
| `CANARY_AUTO_ADVANCE` | **unset in env → code default `true`** (`settings.py:1213`) | ⚠ **effectively TRUE — footgun armed** (moot at stage 4, but arms the rebuild if it launches at a lower stage) |
| `TRADING_PHASE` | unset → code default `"paper"` (`settings.py:1077`) | divergence vs `SIMULATION_MODE=false` |
| `BOT_BANKROLL_CONFIG[MirrorBot]` | capital 20000, kelly 0.25, **max_bet_usd 1**, max_daily_usd 20 | the $1 cap is the ONLY throttle on live exposure |
| `POLYGON_RPC` / `RELAYER_API_KEY` / `PRIVATE_KEY` / `DATABASE_URL` | all **set** (presence only) | |

**Effective production state: live at 100% canary, auto-advance on by default, real money,
throttled only by max_bet_usd=$1** — on the strategy core that has no measured edge. This is
the concrete confirmation that M-ENV must HARD-SET `SIMULATION_MODE=true` +
`CANARY_AUTO_ADVANCE=false` + `CANARY_STAGE=0` before the rebuilt bot runs, or "launch in
paper" is silently violated by inherited env.

MIRROR_* runtime knobs (verbatim) captured in the M-ENV appendix of `MB_REBUILD_PLAN.md`.

## Algo-lane note (TASK 4)

`mb-formula-review` `scripts/mirror_scoring_run.py --stage validate` **crashed on startup**:
`AttributeError: 'Database' object has no attribute 'initialize'` — it calls `db.initialize()`;
the class exposes `db.init()`. **Identical to the `shadow_analysis.py` bug** catalogued in the
salvage package. One-char fix in the algo lane; the kill-criterion never ran, so
`mirror_scoring` validation is still **pending**.
