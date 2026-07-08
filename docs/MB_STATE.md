# MirrorBot Rebuild — Living State / Handoff (docs/MB_STATE.md)

**Last updated:** 2026-07-08 · **Branch:** `claude/mirrorbot-persistence-check-oc02tk`
**Read first:** `CLAUDE.md` (binding directives), `MB_REBUILD_PLAN.md` (the plan + operator decisions), then this file.
**Protocol for updating this file:** `docs/MB_HANDOFF_PROTOCOL.md`.

---

## 1. One-paragraph state

MirrorBot's old whale-copy strategy is confirmed dead (no measured edge). The old bot is **paused to paper** (real money off, 2026-07-05) but still collecting signal data. A **clean-silo rebuild** (`mirror_v3/`) is scaffolded, tested, and ready to deploy — safety spine only, strategy slot deliberately empty behind an acceptance gate. **The v3 whale trader-ranking engine (`bots/mirror_scoring/`) FAILED its Stage-1 acceptance gate** under a now-calibrated permutation test (prior-session report `172d72a`: 2 cutoffs FAIL, placebos 0/20 and 1/20 — a *trustworthy* negative; the earlier "PASS" was a miscalibrated test). **Before any algo rework, the one honest go/no-go is a trader-skill PERSISTENCE check** — `scripts/check_trader_persistence.py`, built this session, awaiting an operator VPS run. It strips all modeling and just measures raw cross-period edge autocorrelation (do traders +edge in period 1 stay +edge in period 2?), with a calibrated placebo and multi-cutoff agreement. ≈0 ⇒ no persistent signal exists, no rework helps, FAIL stands. Clearly >0 ⇒ signal real, method lost it (likely pooling un-tailable crypto whales with sports/esports) ⇒ per-category rework justified. The other strategy direction is a **sharp-line reference** (compare whale entries to an efficient outside price); its vendor-independent core is built and tested, waiting on an OddsPapi paid tier for sports data. Everything is on GitHub; nothing is deployed except the pause.

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
| Scoring engine | `bots/mirror_scoring/` (from `mb-formula-review`) | 45 tests; runner unblocked (`8ea683d`); validate run pending |
| M0-DB verify | `scripts/verify_salvage_data.py` | read-only; cascade bug fixed |
| Persistence go/no-go | `scripts/check_trader_persistence.py` | read-only; pure-stdlib; offline self-test PASS (placebo calibrated at α); **awaiting operator VPS run** |
| Operator runbooks | `docs/VPS_RUNBOOK_2026-07-02.md`, `deploy/mb_vps_oneshot.sh` | one-paste checks; mktemp-safe |

## 5. Open threads / what's next

- **[operator, GATING] Run the persistence check** — the one honest go/no-go before any ranking rework:
  ```
  cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/opt/polymarket-ai-v2 \
    venv/bin/python scripts/check_trader_persistence.py --by-category | tee /tmp/persistence.log
  # optional cross-check on the rejected-signal corpus:
  ... scripts/check_trader_persistence.py --source rejected --by-category
  ```
  Read the VERDICT block. ≈0 pooled AND no category slice persists ⇒ FAIL stands, stop.
  Clearly >0 (pooled or a category) ⇒ rework justified, per-category not pooled.
  **Hard rule:** do NOT rework-then-retest until this passes (p-hacking); a real
  verdict needs the built-in calibrated placebo + multi-cutoff agreement (both are).
- **[operator, PENDING] 3rd cutoff (05-10) validate result** — grab it from `/tmp/val_all.log`
  on the VPS and record it here (2 cutoffs already reported FAIL; this session had no VPS access).
- **[operator] Re-run algo validate** — `deploy/mb_vps_oneshot.sh` (fixed); paste output. It's the scoring engine's go/no-go — but it FAILED Stage-1 (see §1); the persistence check above is the prerequisite that decides whether re-running is even worth it.
- **[operator] OddsPapi paid tier** — confirm sports coverage + that `ODDSPAPI_API_KEY` is set in the VPS env (presence only). Then the sharp-line engine wires to live data.
- **[build, blocked on above] Sports sharp-line pipeline:** live OddsPapi fetch, sports team-name → Polymarket condition_id matcher (esports matcher exists in EB, sports is net-new), offline backfill of `sharp_prob` onto signals, then run through the gate.
- **[build, unblocked] Crypto kill-test:** run crypto signals through the harness at realistic latency to confirm the latency-trap hypothesis and formally drop crypto.
- **[build, unblocked] v3 rejection logging + RTDS plumbing** so the silo collects its own signal stream (then old MB can be fully stopped, not just paused).
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
