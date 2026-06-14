# CROSS-BRANCH LANDMINES — wb/main fixes NOT on `main` (master)

**Created:** 2026-06-13 (S245 WB session, P0 semantic audit follow-up)
**Owner of remediation:** MB session (master/cross-bot is MB's lane per CLAUDE.md SESSION PRIORITY §2; this file is a PROPOSAL/inventory, not an executed merge).
**Why this file exists:** the WeatherBot splinter (`wb/main`) deploys from `.claude/worktrees/wb-main/`. Multiple correctness fixes live ONLY on wb/main and are absent from `main`. Any session that (a) deploys WeatherBot from master, or (b) rebases wb/main onto master, or (c) merges wb/main→master without preserving wb/main's versions, will **silently regress** these fixes. This is a future-Claude trap. One inventory beats rediscovering them one at a time.

## How verified
Content comparison (authoritative), not branch topology:
- `git show main:<file> | grep -c <marker>` vs the wb/main working copy.
- `main` `bots/weather_bot.py`: **0** `side="SELL"` exit sites; wb/main: **3**.
- `main` `base_engine/execution/paper_trading.py`: **0** `_shadow_best_ask` refs; wb/main: **4**.

## Inventory (each = a fix on wb/main missing from master)

| # | Fix | wb/main commit | File(s) | Marker absent on master | Severity if regressed |
|---|-----|----------------|---------|-------------------------|------------------------|
| L1 | **S237 BLOCKER — exits use `side="SELL"`** (not flipped token side) | `882f1fb` | `bots/weather_bot.py` (3 exit sites) | `side="SELL"` count = 0 on master | **CRITICAL** — exits BUY the opposite token → averages-up losers on hard-stop. Active capital bleed if WB ever runs from master. |
| L2 | **S231 — real top-of-book slippage anchor** (prefer `_shadow_best_ask/_bid` over synthetic) | `2686c71` (+ silo `c8f5f86`) | `_shadow_best_ask` count = 0 on master | **HIGH** — NO>0.55 entries false-reject at ~183% phantom slippage (the S245 bug). |
| L3 | **S232 — phantom size-0 level filter** for shadow best ask/bid | `e7885e2` (+ silo `b9b25ce`) | (rides with L2 region) | MED — anchor diverges from walker on phantom-top books. |
| L4 | **S237 HIGH-2 — VWAP fill price as entry_price** (+ null-price guard `95c3325`) | `28f3375`, `95c3325` | confirm `result.get("price") or effective_price` | MED — positions.entry_price diverges from trade_events VWAP; null-price replay crash. |
| L5 | **S237 HIGH-1 — liquidity_guardian anchors to first real-size level** | `2725db9` (+ silo `1ff2baa`) | confirm `next(... size>0 ...)` in `base_engine/risk/liquidity_guardian.py` | MED — phantom anchor mis-sizes / false shadow-rejects. |

**NOT landmines (master already has the original):** A2 `3c5aeac` and A1-GAP-3 `f1f6340` are PORTS of master commits `2fc51b1` / `e9c109b` — master has the originals, so no regression risk there.

**GATED-BY-CONFIG bug (S245 P0, fix pending on wb/main, also absent from master):** the Kelly/S-T `model_prob` denomination inconsistency (wind/precip/snow NO double-inversion) exists on BOTH branches — it is NOT yet fixed anywhere. When fixed on wb/main it becomes a new landmine entry. Tracked in memory `project_wb_slippage_anchor_deadtree_ports.md`.

## The deeper issue (for MB)
These all share a root: WeatherBot's fixes historically landed in WB-only trees (silo and/or wb/main) and were never cherry-picked to master. The same fixes also affect **MirrorBot/EsportsBot** on master where the synthetic-anchor / phantom-filter logic is identical (S231 commit explicitly left master untouched). MB should decide, per fix, whether to cherry-pick to master (affects all bots) — especially L2/L3/L5 (slippage anchor family) and L1 (if any future WB-from-master deploy is possible).

## Recommended MB actions
1. Triage L1 first (CRITICAL): confirm no scenario runs WeatherBot from master; if any exists, cherry-pick `882f1fb` immediately.
2. Decide cherry-pick vs leave-divergent for L2–L5 (cross-bot benefit on master for MB/EB).
3. Add a merge-guard: when reconciling wb/main↔master, preserve wb/main's versions of these files unless explicitly superseded.
