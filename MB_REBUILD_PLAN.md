# MirrorBot Rebuild Plan

**Date:** 2026-07-02 · **Branch:** `claude/mirror-bot-salvage-rebuild-d08v6x` · **Base:** master `67b3039`
**Inputs:** `SALVAGE_PACKAGE.json` / `MB_SALVAGE_MANIFEST.md` (verified this session: 430/430 salvage-asset tests green, all 25 code paths present, 24/26 line refs exact), `CLAUDE.md` directives, operator decisions of 2026-07-02.
**Verification posture:** everything below is assumed wrong until verified. PG data-table claims remain **UNVERIFIED** until M0-DB runs on the VPS.

---

## 0. Operator decisions (2026-07-02, binding)

1. **Acceptance gate approved:** external algo work proposes signal rules; the fill-replay backtest + `edge_verification.py` bootstrap gate disposes. No algo becomes production code without passing both.
2. **Fill-price capture fix authorized now** (3-site, defensive; deploy timing operator-controlled).
3. **M0-DB verification:** session writes the script; operator runs it on the VPS.
4. **Relaunch in PAPER, treated as real** (paper-is-production). Sizing at **code defaults** (real Kelly sizing, not the old bot's de-risked flat-$1/$5 end-state). The $200 paper-phase cap binds below the $300 per-bot cap (`config/settings.py:1085-1088`, `bankroll_manager.py:426,429`). Operator note: *"sizing appears to mess us up"* — the paper phase explicitly validates BotBankrollManager sizing behavior as a first-class deliverable, not a side effect.

---

## 1. Orientation overrides — the salvage docs address a FOREIGN consumer; this rebuild inverts these instructions

The salvage package (`SALVAGE_PACKAGE.json` `_description`) is written for "a foreign bot/agent in another silo" after an MB scrap. This session rebuilds MirrorBot itself. The following salvage instructions are **overridden** for this rebuild (workflow-verified 2026-07-02, 26 confirmed / 0 refuted):

| Salvage instruction | Rebuild override |
|---|---|
| `pattern.dedup_mechanisms`: "DISCARD the one-bet-per-market policy" | **KEEP.** One-bet-per-market is a binding CLAUDE.md constraint AND the neg-risk-specific safety mechanism that replaced the reverted blanket `neg_risk` filter (`f66ed43`). Re-implement all four guard scopes **(guard list corrected 2026-07-02, verified by read-audit + CLAUDE.md `5251d30`)**: `_open_positions` scan (same-side dedup while open, `mirror_bot.py:3110-3140`), `mirror_opposing_side_blocked` (cross-scan), `mirror_opposing_side_blocked_historical` (cross-session), `_entered_market_sides` restored at startup. `mirrored_trades` is write-only tx-hash bookkeeping — keep it as bookkeeping, do NOT count it as a guard. |
| Manifest "scrap cleanup checklist" steps 1–3 (deregister from BOT_REGISTRY, delete `bots/mirror_bot.py` / `elite_watchlist.py` / calibration files, prune "MirrorBot" branches in shared modules) | **FORBIDDEN.** MirrorBot stays registered; files are rebuilt at their existing paths; shared-module "MirrorBot" branches (e.g. `order_gateway.py:751` SELL-exit liquidity skip, `:763-764` depth mult) are infrastructure the rebuilt bot depends on. |
| `pattern.state_restore_netcounter`: status `not_now`, "rewrite SQL for your schema" | **REQUIRED, same schema.** Retain `_restore_state_on_startup` (actual span `mirror_bot.py:311-700`) incl. the S228 `is_paper` filter and S244 `execution_mode` filter. **Fix the latent partial-restore bug:** `_state_restored=True` may only be set after BOTH the main restore AND the `_entered_market_sides` rebuild succeed (currently set at `:496`, before the rebuild at `:562-592`) — the current ordering silently corrupts guard #1. |
| `category=pattern` consume model: "copy into your bot and re-point `self.base_engine`" | **Retain in place** (or carry into the replacement file at the same path), fixing the enumerated defects on retention (see §4). |
| `infra.clob_adapter`: "latent, bites only on flip to live GTC" | **Fix now** (operator-authorized). Paper-is-production forbids deferral; MB already traded live with the defect active; FOK path carries up to `CLOB_MARKETABLE_CAP_PCT` (5%) systematic cost-basis error. |
| "Keep `redeem_and_retrade.py` + timer until the wallet is drained" | **Keep indefinitely** — the wallet keeps operating in a rebuild. |
| `strategy.gate_scoring`: DO NOT REUSE | **Upheld — strongest form.** The whale-copy scoring core (`mirror_bot.py:3602-3810`) is dead: audit found signal indistinguishable from zero, no +EV wallet subset, non-monotonic confidence. Nothing from it is ported. |

**Identity decisions:** keep bot name `MirrorBot` (DB history, restore queries, dashboards key on it), keep file paths, rebuild in place. Phase 1 does **not** instantiate the learning stack (see §3 CANARY rule).

---

## 2. Acceptance gate — "the algo proposes, the backtest disposes"

The old strategy died of unmeasured edge. The gate exists so that cannot recur.

- **Gate inputs:** a candidate rule as a pure function (signal-in → decision/score-out). Signal logic is the external algo workstream's lane; execution, guards, persistence, and this gate are this session's lane. Neither commits into the other's lane.
- **Gate mechanics (corrected 2026-07-02 — `orderbook_snapshots` is aggregated buckets, NOT full L2):** dual fill model. **Precise** — VWAP ladder walk over `shadow_fills.book_snapshot` (real per-level L2 at ~13.8k MB signal moments; high fidelity, narrow). **Coarse** — bucket interpolation from `orderbook_snapshots` (best bid/ask + 1%/5% depth; broad, approximate). Signals from cleaned `mirror_rejected_signals` (4-step recipe), no look-ahead; then bootstrap: **a rule passes only if P(edge>0) clears the threshold on the PRECISE model** (operator decision 2026-07-02); the coarse model is a supporting breadth check, never the sole basis for admission. Every reported result is tagged with fill model + coverage + sample size.
- **Hard rule:** no production strategy code (live scoring, sizing, entry/exit rules) is scheduled until a rule passes the gate AND the operator picks it. Research code (harness, cleaners, collectors) is pre-gate work and proceeds now.
- **Prior:** skeptical. The audit's central negative result was that no whale subset was measurably +EV. A plausible-sounding rule that fails the gate is the expected outcome, not a surprise.

---

## 3. Milestones

| # | Milestone | Gate/dependency |
|---|---|---|
| M1 | Fill-price capture fix (3-site, defensive) | Authorized. Tier-3 protocol; salvage-asset suite green; deploy timing = operator. Final field-shape confirmation needs ONE live order (operator-gated). |
| M2 | M0-DB data verification | ✅ **CLEARED 2026-07-02** (see `docs/m0_db_results_2026-07-02.md`). Ran on VPS: orderbook 37.7M + aggregated-shape confirmed, mirror_rejected 17.5M (refutes "12M"), gate-labeled = **286,293** (the never-verified number), precise-model ceiling = **12,713** ladder rows. **Two open caveats:** positions paper SELL = 191 (manifest's ~1,421 did NOT reproduce); gate tx-window dedup still times out (Path-B trainable size unsettled, est. ~7k–10k). Data-dependent work may proceed. |
| M3 | Fill-replay backtest harness v1 + bootstrap gate | Code now (mock-tested); execution against real data blocked on M2. |
| M4 | Rebuilt bot core: guards (§1), state restore (with `_state_restored` ordering fix), salvaged patterns (§4), NO strategy core | Blocked on nothing; strategy slot stays empty behind the §2 gate. |
| M5 | M-ENV reconciliation before first paper deploy | ⚠ **Verified prod env 2026-07-02 is LIVE, not paper:** `SIMULATION_MODE=false`, `CANARY_STAGE=4` (100%), `CANARY_AUTO_ADVANCE` unset→**default true**, `TRADING_PHASE` unset→default "paper", `max_bet_usd=1` is the only exposure throttle. **The rebuilt bot must HARD-SET `SIMULATION_MODE=true` + `CANARY_AUTO_ADVANCE=false` + `CANARY_STAGE=0` (and reset `max_bet_usd` to a real paper size per Decision 4) BEFORE it runs**, or inherited env silently makes "paper" live. Full MIRROR_* runtime knob list in `docs/m0_db_results_2026-07-02.md`. |
| M6 | Paper relaunch, treated as production | Sizing-behavior validation is a first-class deliverable. Live flip is a separate operator decision gated on measured paper edge. |

**CANARY rule (standing):** Phase 1 must not construct learning collaborators — `LearningScheduler` is auto-wired by `base_engine.py:1232` when learning_engine+prediction_engine+db are present, and `CANARY_AUTO_ADVANCE` defaults True twice (`settings.py:1213` env default + code default), auto-advancing paper→live on a Brier window. If a later milestone adopts the learning stack, a standalone neutralization commit (explicit `CANARY_AUTO_ADVANCE=false` in env + verified at startup) lands FIRST.

---

## 4. Salvage consumption map (rebuild-oriented)

**Import in place (shared `base_engine`, test-green this session):** CircuitBreaker/execution_engine, BotBankrollManager (Kelly), RTDS feed, TradeCoordinator, kill-switch + watchdog spine, Phase-4b backfill.

**Retain/carry in place, fixing named defects on retention (each fix its own commit):**
1. `wash_detector` (`elite_watchlist.py:799-841`) — sound, no changes.
2. `dedup_mechanisms` (`mirror_bot.py:3013-3105` +) — keep mechanisms AND policy (§1); the block embeds four `await self._log_rejection(...)` calls (`:3017/:3044/:3080/:3096`) that must stay pointed at the rejection logger.
3. `fresh_side_price` (`mirror_bot.py:2823-2894`) — returns midpoint, not tradeable ask: treat as entry/sizing caveat.
4. `phantom_detection` (`mirror_bot.py:2126-2212`) — PRESERVE asymmetry: sell-guard fails OPEN, phantom-confirm fails CLOSED.
5. `leaderboard_select` (`elite_watchlist.py:120-687`) — fix `profit_factor=inf` on zero-loss traders (`:347`); note ROI is overall, not per-category.

**Do not carry:** gate scoring core, dead `can_take_position`, `whale_trades` table (frozen snapshot), broken tools as-is (`shadow_analysis` `db.initialize()`→`db.init()`; `slippage_check` str→TIMESTAMP bind; `mirror_whale_analysis` nonexistent `elite_traders` — each a separate fix commit if/when needed, with Can't-Fully-Verify disclosures until DB-verified).

---

## 5. Evidence discipline (standing)

- No doc-quoted quantity (row counts, rows/day, label %) is carried into commit messages or operator reports as fact until re-measured (M2).
- Trading-state numbers only from `scripts/bot_pnl.py`; live truth from `scripts/reconcile_live_onchain.py`.
- Every fix commit carries the CLAUDE.md change-log block; shared-module changes carry full cross-bot verification.
