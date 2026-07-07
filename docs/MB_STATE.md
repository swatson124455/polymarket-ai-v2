# MirrorBot Rebuild — Living State / Handoff (docs/MB_STATE.md)

**Last updated:** 2026-07-06 · **Branch:** `claude/session-handoff-6uuafb` (= `master` `b5a0c89` + handoff docs + v3 signal collector; not yet merged)
**Read first:** `CLAUDE.md` (binding directives), `MB_REBUILD_PLAN.md` (the plan + operator decisions), then this file.
**Protocol for updating this file:** `docs/MB_HANDOFF_PROTOCOL.md`.

---

## 1. One-paragraph state

MirrorBot's old whale-copy strategy is confirmed dead (no measured edge). The old bot is **paused to paper** (real money off, 2026-07-05) but still collecting signal data. A **clean-silo rebuild** (`mirror_v3/`) is scaffolded, tested, and ready to deploy — safety spine only, strategy slot deliberately empty behind an acceptance gate. The strategy direction is a **sharp-line reference** (compare whale entries to an efficient outside price); its vendor-independent core is built and tested, waiting on an OddsPapi paid tier for sports data. Everything is on GitHub; nothing is deployed except the pause.

## 2. Current system state (verified)

- **Old MB:** paper mode. `SIMULATION_MODE=true, CANARY_STAGE=0, CANARY_AUTO_ADVANCE=false` appended to `/opt/pa2-shared/.env.mirror` and service restarted. Still writes `mirror_rejected_signals` (the rebuild's data).
- **VPS is LIVE-config by default otherwise** — before the pause it was `SIMULATION_MODE=false, CANARY_STAGE=4`, auto-advance armed by code default. The env-drift-to-live risk is real; `mirror_v3` env_guard exists specifically to end it.
- **Data tier (measured 2026-07-05, `docs/m0_db_results_2026-07-02.md` + this session):** ~5.06M labeled whale signals — **crypto 73%, sports 17%, esports 5%**, rest <5%. orderbook_snapshots 37.7M (aggregated buckets, NOT full L2). mirror_rejected_signals 17.5M. gate-labeled 286k. precise-fill ladder rows (shadow_fills) 12,713.
- **Tests:** 423+ green on the merged tree; each new module ships its own suite.

## 3. Key decisions (all in MB_REBUILD_PLAN.md, do not re-litigate)

1. **Acceptance gate:** no strategy ships without passing the fill-replay backtest (precise model) + edge check. "Algo proposes, backtest disposes."
2. **Clean silo** (`mirror_v3/`), new identity `MirrorBotV3`, own systemd unit, allowlist env, same VPS.
3. **Paper-first, real sizing** (code defaults, not the old flat-$1).
4. **Strategy = sharp-line reference, sports-first.** Crypto (73% of volume) is a **latency** edge → un-tailable on our 60s delay → expected to FAIL the gate (use the harness's latency model as the kill test). Sports/esports are **knowledge** edges → tailable. Pinnacle for sports, OddsPapi for esports.
5. **clob_adapter fill-price fix** landed (S250, `d3d2369`) — defensive, paper-unchanged.

## 4. What's built (all pushed)

| Area | Location | State |
|---|---|---|
| Clean silo | `mirror_v3/{env_guard,guards,state_restore,run}.py` | scaffold + 22 tests; boots, restores fail-closed, strategy idle |
| Silo deploy | `deploy/polymarket-mirror3.service`, `deploy/env.mirror3.example` | ready; needs real `DATABASE_URL` on first install |
| Acceptance gate | `bots/mirror_backtest/{fill_models,replay,gate,data_access}.py` | dual-model harness + 19 tests; DB-execution gated on M0-DB |
| Sharp-line core | `bots/mirror_backtest/sharp_reference.py` | no-vig, point-in-time, gate rule + 19 tests; OddsPapi seam env-key-only |
| v3 signal collector | `mirror_v3/signal_collector.py` | RTDS→`mirror_rejected_signals` raw watched-whale stream + 27 tests; reuses existing `insert_mirror_rejected_signal` (no schema change); NO whale floor (gate decides); wired into run.py |
| v3 watchlist source | `mirror_v3/watchlist_source.py` | monthly-leaderboard `is_watched` predicate + 7 tests; re-implements ONLY the Data API fetch (no dead scoring stack); 6h refresh TTL, fail-safe (keeps prior set) |
| v3 run wiring | `mirror_v3/run.py` | after restore: refresh watchlist → build collector → `build_rtds_feed().connect()` → heartbeat refreshes watchlist + logs collector stats → disconnect on exit; compiles, silo tests green |
| Scoring engine | `bots/mirror_scoring/` (from `mb-formula-review`) | 45 tests; runner unblocked (`8ea683d`); validate run pending |
| M0-DB verify | `scripts/verify_salvage_data.py` | read-only; cascade bug fixed |
| Operator runbooks | `docs/VPS_RUNBOOK_2026-07-02.md`, `deploy/mb_vps_oneshot.sh` | one-paste checks; mktemp-safe |

## 5. Open threads / what's next

- **[operator] Algo validate run — UNBLOCKED (fixes landed 2026-07-06).** All 6 review findings (`docs/ALGO_REVIEW_FINDINGS_2026-07-06.md`) FIXED on operator "fix now": F1 overlap exclusion + `n_excluded_overlap` (`8750627`), F4 case-insensitive joins (`9a9a71d`), F3 `--stream legacy|v3|all` filter (`4ef5910`), F2 v3 heartbeat resolution backfill + runbook pre-stop check (`188ce45`), F5 full-pool EB shrinkage (`a0a435c`), F6 two-sample cluster bootstrap (`5fb8d89`). Full scoring+v3 sweep 136 tests green. Statistical-lane note: F1/F5/F6 touch the `mb-formula-review` lane's modules — applied under explicit operator authorization; hand the commits across for lane review. **FIRST VALIDATE RUN COMPLETED (2026-07-06 23:48 UTC, VPS, ~27 min):** `VALIDATION: FAIL [stream=legacy, overlap_excluded=0] — need both admitted and non-admitted traders to compare; scored=3026 admitted=0`. The engine admitted ZERO of 3,026 scored traders, so the kill-criterion comparison never ran. **Do NOT conclude no-skill yet** — three distinguishable causes, and the report JSON (`scoring_reports/mirror_scoring_validate_20260706T234840Z.json` on the VPS) decides: (1) p-floor×BH discreteness (bootstrap p floored at 1/(1+N_BOOT)≈0.001; BH over pool m needs ≥⌈m·0.001/0.10⌉ traders tied at the floor — test-resolution artifact), (2) holdout starvation around the 2026-05-25 cutoff (p=1 fail-closed), (3) genuine null (skeptical prior wins, pivot to sharp-line). **DIAGNOSED + FIXED (2026-07-06, `eea96d7`).** `diagnose_scoring_report.py` on the real report: BH pool 2,993, of which **2,512 (84%) were p=1.0 placeholders** — untested traders (empty test half / failed train screen) that were nonetheless counted in the multiple-testing denominator, raising the BH bar ~6x for the 481 genuinely-tested traders. Ten strong candidates sat at the bootstrap floor (edges +0.06…+0.22, events 13–1,538) and still couldn't clear. This is a **statistical bug, not evidence of no-skill** — the top-10 by p show real out-of-sample edge (e.g. `0xe738…` 125 events, +0.12 edge, test +0.30). **Fix:** BH family now restricted to `holdout_tested` traders (genuine train-screened + test-data hypotheses); untested placeholders keep p=1 but leave the denominator. **[operator] RERUN validate** (same command) — with m≈481 the ~10 floor traders now clear BH → the kill criterion actually runs → real PASS/FAIL. Then add `--placebo 20`. N_BOOT=999 (floor 0.001) is still ASSUMED — if the rerun admits a thin set, bump N_BOOT to 9999 for finer resolution (slower). **Audit actions also landed (2026-07-06):** A1 control-sample collection (`a20ee4c` — v3 logs 1-in-50 non-watchlist trades as `metadata.stream="control"`; validation `--stream` gains `control`; `v3` stream excludes control rows), A6 placebo calibration (`714b168` — `--placebo N` shuffles admitted labels through the exact verdict machinery; shuffled rankings must FAIL ~95%; CALIBRATION SUSPECT flag otherwise), A3 dual exit-policy tailability (`7653b3b` — `l_net_hold` alongside the ladder's `l_net`; Stage-2 gating policy is an open decision). Full sweep 147 tests green. Recommended verdict protocol: real run + `--placebo 20` + repeat at 2 extra cutoffs (2026-04-25, 2026-06-05); trust PASS only if placebo calibrates and cutoffs agree.
- **[operator] OddsPapi paid tier** — confirm sports coverage + that `ODDSPAPI_API_KEY` is set in the VPS env (presence only). Then the sharp-line engine wires to live data.
- **[build, blocked on above] Sports sharp-line pipeline:** live OddsPapi fetch, sports team-name → Polymarket condition_id matcher (esports matcher exists in EB, sports is net-new), offline backfill of `sharp_prob` onto signals, then run through the gate.
- **[build, unblocked] Crypto kill-test:** run crypto signals through the harness at realistic latency to confirm the latency-trap hypothesis and formally drop crypto.
- **[build, in progress] v3 rejection logging + RTDS plumbing.** Collector built (`mirror_v3/signal_collector.py`, 26 tests): consumes the RTDS global feed, applies old-MB's exact ingress filters (watched-wallet, tx-dedup, 0.01<p<0.99, size>0, whale ≥ `MIRROR_MIN_WHALE_TRADE_USD` $100, SELL=exit skipped, Yes/Up→YES No/Down→NO), and writes the **raw watched-wallet entry-signal stream** to `mirror_rejected_signals` tagged `rejection_reason="mirror_v3_strategy_gated"` + `metadata.source="mirror_v3"`. **Semantic note (validate at gate time):** this is a DIFFERENT population from old MB's gate-specific rejections — it's the unbiased raw signal universe, not the copied-vs-rejected split (v3 has no strategy to produce that split). No `bot_name` column exists, so the reason+metadata marker is the only separator; do not blend the two populations without accounting for it. **Whale floor DROPPED (2026-07-06, operator decision):** the gate (`bots/mirror_backtest/data_access.py:fetch_signals`) applies NO whale-size floor and NO stage filter — it takes every resolved, price-bounded, deduped row and is meant to pick the size threshold itself. The old bot's `$100` floor was a strategy artifact; keeping it upstream would irrecoverably destroy sub-$100 signal once v3 is the sole writer. Chosen on the reversibility asymmetry (drop is reversible — gate can filter later; keep is not — dropped rows are gone), NOT on a run of the verifier. Collector default is now `min_whale_usd=0` (log everything); `whale_trade_usd` is still recorded so the gate can bucket on it. `scripts/verify_signal_population.py` remains available to revisit whether a floor should ever be re-enabled (`--dedup` for gate-shape). **Seam CLOSED (2026-07-06):** wallet source built (`mirror_v3/watchlist_source.py`, monthly-leaderboard `is_watched`, no dead-stack import) and `mirror_v3/run.py` now connects the RTDS feed after restore, refreshes the watchlist on its 6h TTL, and logs collector stats. The end-to-end path (RTDS → watched-wallet filter → `mirror_rejected_signals`) is code-complete and unit-tested. **[operator] deploy runbook ready:** `docs/VPS_DEPLOY_v3_collector.md` — one SSH session (clone branch → auto-fill DATABASE_URL → install unit → start → verify rows tagged `metadata.source="mirror_v3"` → stop old MB). Deploys from branch `claude/session-handoff-6uuafb`; paper-only; coexists with old MB until confirmed. Not yet verified end-to-end against live RTDS/DB (no socket or DB in the sandbox) — verify at deploy.
- **[decision] Merge/PR hygiene:** master is current; direct master pushes are operator-gated by the sandbox.

## 6. Cross-session coordination

- **EB (esports)** owns the OddsPapi vendor integration (esports). Odds-capability report is in this session's history; EB has a team-alias matcher MB can reuse. Registry publish (`EB_ODDS_CAPABILITY.json`) offered, not yet committed.
- **mb-formula-review** branch owns the *statistical* scoring lane; MB owns execution/guards/gate. Three statistical findings (condition-vs-event clustering, validation statistic, EB-shrink pool) handed over as recommendations, not applied.
- **MB has priority** on shared resources (CLAUDE.md). Old poisoned project lives at parent `C:/lockes-picks/` — OUT OF SCOPE.

## 7. Landmines (do not trip)

- **order_gateway neg-risk block** no-ops for MB by accident (CLAUDE.md "DORMANT LANDMINE"). "Repairing" the index re-creates Bug 14 (election blackout). Leave it.
- **Do NOT add a `neg_risk=True` filter** anywhere (CLAUDE.md).
- **`mirrored_trades` is bookkeeping, not a guard** — the real same-side dedup is the `_open_positions` scan.
- **CANARY_AUTO_ADVANCE unset → true** by code default. Any live-capable path must set it false explicitly.
- **orderbook_snapshots is aggregated buckets, not L2** — precise replay needs `shadow_fills.book_snapshot`.
