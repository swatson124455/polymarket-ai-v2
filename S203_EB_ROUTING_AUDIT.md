# S203 — EsportsBotV2 vs EsportsBot Routing Audit

**Status:** Routing decision document for the EB v2 → live trade transition (the `ESPORTS_V2_DRY_RUN=false` flip).
**Date:** 2026-04-29
**Author:** S203 (closes §S202 hygiene item 8 — prerequisite to Phase 5v2-D gate evaluation)
**Predecessor:** S202 close documented the routing-mismatch concern; this audit fills it in with verified per-consumer data.

---

## 1. Purpose

EB v2 is currently in shadow mode (`ESPORTS_V2_DRY_RUN=true`). When the flip to live happens, v2-tagged rows will land in operational tables. Every downstream consumer of bot-name-keyed data must be verified to handle both `'EsportsBot'` (v1 legacy) and `'EsportsBotV2'` (v2 live) cleanly — otherwise we silently mis-account v2 trades.

This is the same router-mismatch class that bit S195 (rapidfuzz) and S192 (TMD CSV). A third instance is one bug away.

The audit shapes the operational decision: **what must be fixed before the flag flip, and what can wait.**

## 2. Phase 0 verification snapshot (data state at 2026-04-29 19:40 UTC)

| Table | `EsportsBot` rows | `EsportsBotV2` rows | Notes |
|---|---|---|---|
| `prediction_log` | 0 (always silent for v1) | 110 (since 2026-04-19) | v2 explicitly writes via `db.insert_prediction_log(bot_name="EsportsBotV2")` at [bots/esports_bot_v2.py:549-554](bots/esports_bot_v2.py:549) |
| `prediction_log.resolved` | 0 | 3 of 110 | Only 2.7% resolved — early shadow-window |
| `esports_predictions` | n/a (no bot_name col) | 625 v2-trinity / 35 v2-trinity-contaminated | Discriminator is `mode='shadow'` + `model_version='v2-trinity'` (see Phase 0.3) |
| `trade_events` | 1417 (774 ENTRY + 287 EXIT + 356 RESOLUTION) | 0 | v2 hasn't traded; DRY_RUN gates `_execute_trades()` at [bots/esports_bot_v2.py:348-349](bots/esports_bot_v2.py:348) |
| `paper_trades` | 538 | 0 | Same DRY_RUN gate |
| `positions` (bot_id, source_bot) | 840 / 840 | 0 / 0 | Same |
| Env `SIGNAL_REQUIRED_BOTS` (VPS) | listed (`EsportsBot`) | NOT listed | Found in `/opt/pa2-releases/20260429_134741/.env` |

**Key asymmetry:** v2 writes ONLY to `prediction_log` and `esports_predictions` today. The trade-table tables (`trade_events`, `paper_trades`, `positions`) will start receiving v2-tagged rows the moment DRY_RUN flips.

## 3. Per-consumer routing table

Six consumers were enumerated in §S202 hygiene item 8. One was de-scoped on inspection (item 3.5 — `mirror_rejected_signals`); see §3.5. The remainder are scored below.

### 3.1 — `scripts/bot_pnl.py`

| Field | Value |
|---|---|
| **(a) Current bot_name handling** | Positional CLI arg, default `"WeatherBot"` ([scripts/bot_pnl.py:497](scripts/bot_pnl.py:497)). All SQL queries filter exact-match: `WHERE bot_name = :bot` ([:70](scripts/bot_pnl.py:70), [:152](scripts/bot_pnl.py:152), [:188](scripts/bot_pnl.py:188), [:224](scripts/bot_pnl.py:224), [:238](scripts/bot_pnl.py:238), [:250](scripts/bot_pnl.py:250)). Positions query also filters `bot_id = :bot OR source_bot = :bot` ([:116](scripts/bot_pnl.py:116)). |
| **(b) When arg = "EsportsBot"** | Returns ONLY v1-tagged rows. Misses all v2 rows (post-flip). |
| **(c) When arg = "EsportsBotV2"** | Returns ONLY v2-tagged rows. Misses all v1 rows. The `--clean` contamination CTE (block 3b at [:220-244](scripts/bot_pnl.py:220)) computes contamination per-bot — a market that v1 contaminated and v2 later traded would appear "clean" for v2. |
| **(d) Failure mode** | **SILENT** — no warning, no error. Operator sees a partial cohort and trusts it as canonical. **High blast radius:** `bot_pnl.py` is the canonical source for Protocol 6/11 P&L claims; cohort-split silent failure could propagate into handoffs and Phase 7 verdicts. The block 3b CLEAN total marked "canonical for downstream analysis" inherits the same defect. |
| **(e) Recommended action** | **BLOCKING before flag flip.** Two options: (i) Expand the SQL filter to `WHERE bot_name IN ('EsportsBot', 'EsportsBotV2')` whenever arg matches `'EsportsBot'` or `'EsportsBotV2'`, treating them as one logical bot family. (ii) Add a `--bot-strict` flag for exact-match and make the family-union default. **Recommend (i)** — operationally simpler, matches the actual analysis question ("how is the EB family doing"), and the v1 stops trading at flip-time so v1+v2 eventually converges to v2-only naturally. The contamination CTE must use the same family-union logic. |

### 3.2 — `scripts/edge_verification.py`

| Field | Value |
|---|---|
| **(a) Current bot_name handling** | Default 3-bot list `["WeatherBot", "MirrorBot", "EsportsBot"]` ([scripts/edge_verification.py:77](scripts/edge_verification.py:77)). Same exact-match `WHERE bot_name = :bot` SQL pattern. The `--clean` CTE at [:84-97](scripts/edge_verification.py:84) is identical to bot_pnl.py's. |
| **(b) When arg = "EsportsBot"** | Returns ONLY v1 closed trades for v7 gate evaluation. Phase 7 verdict would compute on v1 cohort only — but post-flip v1 has stopped trading, so the entire Phase 5v2-D evaluation would be on a frozen v1 sample. |
| **(c) When arg = "EsportsBotV2"** | Returns ONLY v2 closed trades. This is the desired behavior for Phase 5v2-D evaluation — but it requires the operator to KNOW to pass `"EsportsBotV2"` explicitly. Default invocation `python scripts/edge_verification.py` (no arg) iterates the legacy 3-bot list and **silently skips v2**. |
| **(d) Failure mode** | **SILENT** — same shape as 3.1. Phase 7 elevation gate could be evaluated on the wrong cohort with no warning. The hardcoded default 3-bot list is the highest-risk surface: every default-invocation skips v2. |
| **(e) Recommended action** | **BLOCKING before flag flip.** Two parts: (i) Update default 3-bot list at [:77](scripts/edge_verification.py:77) to `["WeatherBot", "MirrorBot", "EsportsBot", "EsportsBotV2"]` — explicit enumeration, no implicit family expansion (Phase 5v2-D wants v2 evaluated as its own cohort). (ii) **Decision needed:** does the v7 gate apply to v2 the same way as v1? Phase 5v2-D criteria require P(edge>0)≥0.70 (different from v7's 0.30 PROCEED threshold). The `v7_verdict` function at [:50](scripts/edge_verification.py:50) currently applies one ladder universally. Either add a `--phase-5v2-d` flag with the v2 thresholds OR document that the v7 ladder is operator-aware and v2 must be evaluated separately. |

### 3.3 — `base_engine/audit/factory.py` (audit framework)

| Field | Value |
|---|---|
| **(a) Current bot_name handling** | `SIGNAL_REQUIRED_BOTS` is the only bot-name-aware config — env-var override at [base_engine/audit/factory.py:67-72](base_engine/audit/factory.py:67), default empty list at [:55](base_engine/audit/factory.py:55). Read once at orchestrator-build time, passed to `SignalExecutionCheck`. The other 23 audit checks read `bot_name` from data tables (no filter). VPS env is `SIGNAL_REQUIRED_BOTS=EsportsBot` (only v1). |
| **(b) When v2 trades land** | `SignalExecutionCheck` enforces signal-write coverage ONLY for bots in the list. Today v2 is NOT in the list — even post-flip, v2's trades would not be checked for signal coverage. |
| **(c) v2 explicitly named** | If env is updated to `SIGNAL_REQUIRED_BOTS=EsportsBot,EsportsBotV2`, both are enforced independently. The check is per-bot, no cross-bot logic. |
| **(d) Failure mode** | **NOISY (positive direction)** for the data-driven 23 checks — they group by whatever `bot_name` they find and emit one violation row per bot. v2 rows will be visible. **SILENT (negative direction)** for `SignalExecutionCheck` — v2 not in the env list means v2 signal coverage is unenforced. |
| **(e) Recommended action** | **BLOCKING but minimal.** Update VPS `.env` to `SIGNAL_REQUIRED_BOTS=EsportsBot,EsportsBotV2` BEFORE the flag flip. Same change in deploy template. The TODO at [base_engine/audit/factory.py:8](base_engine/audit/factory.py:8) ("populate SIGNAL_REQUIRED_BOTS by 2026-04-30") is now **TOMORROW** — coincidence with the flip prep, but worth flagging that the deadline is here. |

### 3.4 — `base_engine/audit/checks/prediction_accuracy_check.py`

| Field | Value |
|---|---|
| **(a) Current bot_name handling** | Data-driven — all 4 sub-queries [`SELECT bot_name FROM prediction_log GROUP BY bot_name`](base_engine/audit/checks/prediction_accuracy_check.py:43). No bot-list filter, no enumeration. Cold-start guard at n<30 at [:88](base_engine/audit/checks/prediction_accuracy_check.py:88). |
| **(b) v2 in prediction_log today** | 110 rows / 3 resolved / 3 scored / 0 null_prob. With n_total=3 resolved, the dynamic threshold path is in cold-start (skipped). Absolute floor only (Brier > 0.35 CRITICAL). |
| **(c) v2-only filter behavior** | The check has no filter mechanism — it processes whatever `bot_name` values exist in `prediction_log`. Symmetric for v1+v2. |
| **(d) Failure mode** | **SAFE** — the check is data-driven and produces per-bot violations. No silent omission. |
| **(e) Recommended action** | **NONE before flag flip.** Continue monitoring — once v2 has ≥30 resolved predictions, the dynamic threshold activates. At current rate (~110 predictions / 14 days = ~8/day, ~3 resolved over 14 days = ~0.2 resolved/day), v2 cold-start will persist for months. **Note:** the Phase 5v2-D gate's "≥100 resolved predictions" prerequisite is sourced from `esports_predictions` (276 resolved per Phase 0.3), NOT `prediction_log` (3 resolved). Different tables, different counts — operator must use the right one. |

### 3.5 — `mirror_rejected_signals` consumers (DE-SCOPED)

| Field | Value |
|---|---|
| **(a) Current bot_name handling** | Table has NO `bot_name` column ([schema/migrations/073_mirror_rejected_signals.sql:17-32](schema/migrations/073_mirror_rejected_signals.sql:17)). Schema is MirrorBot-specific by design — it captures whale signals at MirrorBot rejection sites only. EliteWatchlist RTDS-ingress dedup is deliberately excluded per S187 §2.1. |
| **(b)–(e)** | **N/A** — not a bot-name-keyed routing surface. The §S202 hygiene item 8 enumeration of this consumer was a Protocol 7 framings-vs-hypotheses moment: the framing inherited `mirror_rejected_signals consumers` from prior session prose without verifying the structural claim. On inspection, this surface is structurally not bot-name-aware. **De-scoped.** |

### 3.6 — `BOT_REGISTRY` ([main.py:79-99](main.py:79))

| Field | Value |
|---|---|
| **(a) Current bot_name handling** | `BOT_REGISTRY` dict registers both `"EsportsBot": (EsportsBot, "BOT_ENABLED_ESPORTS")` and `"EsportsBotV2": (EsportsBotV2, "BOT_ENABLED_ESPORTS_V2")` ([main.py:92-93](main.py:92)). Each has its own enable flag. Bot startup loop at [main.py:501](main.py:501) iterates the registry. |
| **(b)–(c)** | Both bots are independently registered and toggleable. The startup path is correct. |
| **(d) Failure mode** | **SAFE for startup**, but two adjacent surfaces have gaps: (i) Heartbeat dict at [main.py:311-317](main.py:311) lists only `"EsportsBot"`, `"EsportsLiveBot"`, and the legacy-suppressed `"EsportsSeriesBot"` — **EsportsBotV2 is missing.** When v2 emits heartbeats, this dict won't recognize them. (ii) `ui/dashboard.py` has a hardcoded count `f"BOT_REGISTRY: 16"` at [:584](ui/dashboard.py:584) — currently correct (16 entries), but the dashboard's bot-iteration list at [:365](ui/dashboard.py:365) hardcodes `[("EsportsBot", EsportsBot), ("EsportsLiveBot", EsportsLiveBot)]` and excludes v2. The metric tile at [:3395](ui/dashboard.py:3395) shows only v1's BOT_ENABLED_ESPORTS state. |
| **(e) Recommended action** | **NON-BLOCKING but should-fix.** Heartbeat dict ([main.py:311-317](main.py:311)) needs `"EsportsBotV2": "BOT_ENABLED_ESPORTS_V2"` added — small change, high observability value. Dashboard list at [ui/dashboard.py:365](ui/dashboard.py:365) and metric tile at [ui/dashboard.py:3395](ui/dashboard.py:3395) need v2 added — affects operator visibility, not trade correctness. The hardcoded "BOT_REGISTRY: 16" string at [ui/dashboard.py:584](ui/dashboard.py:584) is fragile but currently correct. |

## 4. Failure-mode summary

| Consumer | Severity if v2 trades land unaddressed | Failure type | Blocks flag flip? |
|---|---|---|---|
| `scripts/bot_pnl.py` (3.1) | HIGH — canonical P&L source mis-reports | SILENT | YES |
| `scripts/edge_verification.py` (3.2) | HIGH — Phase 7 verdict computed on wrong cohort | SILENT | YES |
| `SIGNAL_REQUIRED_BOTS` env (3.3) | MEDIUM — v2 signal coverage unenforced | SILENT (one-direction) | YES (env update) |
| `prediction_accuracy_check` (3.4) | NONE — data-driven, safe by design | SAFE | NO |
| `mirror_rejected_signals` (3.5) | N/A | N/A | NO (de-scoped) |
| `BOT_REGISTRY` (3.6) — startup path | NONE — both registered correctly | SAFE | NO |
| `BOT_REGISTRY` adjacents — heartbeat dict (main.py:311-317) | LOW — observability only | SILENT | NO |
| `BOT_REGISTRY` adjacents — dashboard (ui/dashboard.py:365, :3395) | LOW — operator visibility only | SILENT | NO |

## 5. Recommended actions ranked by blocking severity

### Must-fix before `ESPORTS_V2_DRY_RUN=false` flip

1. **`scripts/bot_pnl.py` family-union for EB family** — change exact-match SQL to `WHERE bot_name IN ('EsportsBot', 'EsportsBotV2')` whenever arg ∈ {EsportsBot, EsportsBotV2}. Apply uniformly to blocks 1, 2, 3, 3b (CLEAN), 4a, 4b, and 5 (WB-specific blocks unaffected). Test must include a fixture with mixed v1/v2 rows confirming a single cohort total.
2. **`scripts/edge_verification.py` default list extension** — add `"EsportsBotV2"` to the default 3-bot list. Keep v1 and v2 as DISJOINT (each evaluated independently — Phase 5v2-D wants v2 alone). **Decision needed in same commit:** add `--phase-5v2-d` flag with thresholds `P(edge>0)≥0.70`, OR document that v7 ladder is universal and v2 evaluation uses operator judgment.
3. **VPS `.env` update** — `SIGNAL_REQUIRED_BOTS=EsportsBot,EsportsBotV2` (or add a deploy-time hook that injects v2 when `BOT_ENABLED_ESPORTS_V2=true`). Closes the `factory.py:8` TODO simultaneously.

### Should-fix at next deploy (post-flip is fine)

4. **`main.py:311-317` heartbeat dict** — add `"EsportsBotV2": "BOT_ENABLED_ESPORTS_V2"`. Two-line change. Affects observability of stale-heartbeat suppression logic.
5. **`ui/dashboard.py:365` esports bot list** — add `("EsportsBotV2", EsportsBotV2)`. Requires `from bots.esports_bot_v2 import EsportsBotV2` at the imports.
6. **`ui/dashboard.py:3395` metric tile** — add a second tile for `BOT_ENABLED_ESPORTS_V2`.

### Nice-to-have (future hardening)

7. **`ui/dashboard.py:584` hardcoded count** — replace `f"BOT_REGISTRY: 16"` with `f"BOT_REGISTRY: {len(BOT_REGISTRY)}"` (importing from main).
8. **Routing guard** — add a CI check that any new `bots/*.py` class registered in `BOT_REGISTRY` triggers a grep for the bot-name string in known consumer files (this audit's scope) and warns if missing. Prevents the next router-mismatch instance.

## 6. Open questions for follow-up sessions

- **Q1 — Family-union vs disjoint policy:** §3.1 recommends family-union for `bot_pnl.py` but §3.2 recommends disjoint for `edge_verification.py`. The semantic difference: P&L is operator-reporting (operator wants to know "how is EB family doing"), but Phase 7 elevation is gate-evaluation (gate wants to know "does THIS bot meet promotion criteria"). The split is principled — but does the family-union vs disjoint distinction need a wider review across all bot-name-keyed consumers (e.g. heartbeat dict, audit checks)?
- **Q2 — Phase 5v2-D thresholds:** Confirm in [S172_CONSOLIDATED_PLAN.md](S172_CONSOLIDATED_PLAN.md) that the Phase 5v2-D ladder is `P(edge>0)≥0.70` (per memory) — different from v7's 0.30. If yes, edge_verification.py needs a phase-5v2-d mode. If no, document that v7's ladder is universal and the Phase 5v2-D phrase in handoffs is a gate-prerequisite count, not a separate ladder.
- **Q3 — `esports_predictions` vs `prediction_log` reconciliation:** The Phase 5v2-D "≥100 resolved" prerequisite has 276 resolved in `esports_predictions` but only 3 resolved in `prediction_log` for v2. These are different writes (one is the v2-internal singletons table, the other is the cross-bot observability log per `esports_bot_v2.py:541` comment). Which table the gate uses must be explicit when the gate fires.
- **Q4 — `EsportsLiveBot` parallel routing:** EsportsLiveBot is in `BOT_REGISTRY` and the heartbeat dict but was NOT enumerated as part of this audit. If a v2 of EsportsLiveBot is in the roadmap, the same audit pattern applies. Track for future.

## 7. Audit closure

This audit closes §S202 hygiene item 8. The blocking items in §5 are the prerequisite to Phase 5v2-D evaluation; once those land, the gate becomes operationally evaluable on the criteria already met (n=276 resolved exceeds the ≥100 threshold per Phase 0.3).

**Next action (S203 Track 1b):** ship items 1–3 from §5 as code/config changes. Items 4–8 are filed as `§S203 Hygiene Backlog` for follow-up sessions, ordered by blocking severity.
