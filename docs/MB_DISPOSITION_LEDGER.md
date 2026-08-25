# MB DISPOSITION LEDGER — DRAFT for operator sign-off
**Date:** 2026-08-25 · **Source tree (read-only):** `C:/lockes-picks/mb-steward` (branch `claude/repo-setup-docs-fq9bhn`) · **Inputs:** `docs/mb_overhaul_review_findings.json` (87 confirmed findings; areas legacy/data/charters read in full), `MB_REBUILD_PLAN.md`, `docs/MB_STATE.md`, `docs/MB_COPYTRADER_CONTEXT.md`, fresh greps run this session (cited per item).

## Binding frame (nothing in this ledger executes anything)
1. **This ledger deletes, stops, and demotes NOTHING.** Every RETIRE requires explicit operator sign-off naming the item (sign-off sheet at the end — one word per line).
2. **House removal sequence for every RETIRE:** backup → quarantine-mark → observe one full cycle → remove, with a per-item rollback line.
   - *Repo files:* pre-fix git snapshot commit → `git mv` into an attic/archive path (quarantine-mark) → one full cycle green (full `pytest` + one deploy + one 11:40Z/12:30Z cron day where relevant) → delete in a separate later commit. Rollback: `git revert <sha>`.
   - *VPS files:* `cp -a X /opt/pa2-backups/quarantine/X.$(date +%Y%m%d)` → `mv X X.QUARANTINED-$(date +%Y%m%d)` → observe one full daily cron cycle → `rm`. Rollback: `mv X.QUARANTINED-… X`.
   - *DB rows / services:* never deleted or stopped by this process at all — separate operator-executed runbook steps only.
3. **Evidence discipline:** every "no consumer" claim below is a grep I ran today against this worktree, with the command result summarized. Anything about *live VPS runtime state* (which units are enabled, whether timers fire, current row counts) is labeled **UNVERIFIED-from-repo** — the repo cannot prove runtime. The "794 trades/7d" figure for legacy MB is operator-context supplied to this review, not re-measured here (canonical check: `scripts/bot_pnl.py MirrorBot 168`).
4. Scope: MB lane only. Root-level `diag_conf*.py`, `conf_history*.py`, `wb_48h_raw.csv`, and all WEATHER/ESPORTS handoffs were looked at and are **out of MB scope** — not dispositioned here (grep showed no MirrorBot linkage for the diag/conf files).

---

## Summary table

| # | Item | Disposition | One-line reason |
|---|------|-------------|-----------------|
| 1 | Legacy MirrorBot lane (`polymarket-mirror.service`, `bots/mirror_bot.py`, `bots/elite_watchlist.py`) + its `mirror_rejected_signals` label stream | **KEEP (today); RETIRE path defined, gated on one operator ruling** | Label stream's only remaining justification is the chartered-but-idle §2 acceptance gate; FORWARD DATA ONLY has zeroed the corpus's decision weight |
| 2 | Inert code inside `mirror_bot.py` (`_slippage_fail_count`/`_slippage_backoff`, `mirrored_trades`, `_whale_consensus`) | **QUARANTINE** (already comment-marked) | Piecemeal deletion FORBIDDEN by MB_REBUILD_PLAN §1; retires only with item 1 |
| 3 | `bots/mirror_scoring/validation.py` (false-PASS machine) | **QUARANTINE** (add in-file banner) | Documented circular in MB_STATE §7 but the file itself carries no warning |
| 4 | `bots/mirror_scoring/` package + `scripts/mirror_scoring_run.py` | **QUARANTINE** | Superseded by the copy-trader pivot; 5 green test files pin it; sole runner consumer |
| 5 | `deploy/mb_vps_oneshot.sh` | **RETIRE** | Clones a dead branch (`claude/mb-formula-review-vdxmtr`) to run the quarantined scoring stage |
| 6 | `scripts/backtest_tail_leaderboard.py` (+ its test) | **RETIRE** | Marked "superseded" in the tooling map; zero non-doc consumers |
| 7 | `scripts/check_trader_persistence.py` (+ its test) | **RETIRE** | Marked "superseded"; zero non-doc consumers |
| 8 | `scripts/crypto_kill_test.py` (+ its test) | **RETIRE** | 07-12 kill-test era; zero non-doc consumers; verdict already recorded in docs |
| 9 | `scripts/counterfactual_pnl.py` | **RETIRE** | 05-06 era, zero consumers, reads in-sample `shadow_fills` only |
| 10 | `scripts/shadow_analysis.py` | **RETIRE** | Broken (`db.initialize()`), named do-not-carry in MB_REBUILD_PLAN §4, zero consumers |
| 11 | `scripts/mirror_whale_analysis.py` | **RETIRE** | Broken (queries nonexistent `elite_traders`), named in §4, zero consumers |
| 12 | `scripts/slippage_check.py` | **RETIRE** | Broken (str→TIMESTAMP bind), named in §4, zero consumers |
| 13 | `scripts/verify_salvage_data.py` | **RETIRE** | M2 verifier; M2 CLEARED 2026-07-02 — purpose spent |
| 14 | `scripts/backtest_copyable_fills.py` (+ its test) | **QUARANTINE** | Still named in the MB_COPYTRADER_CONTEXT §6 decision tree; superseded in practice by forward shadow fill measurement |
| 15 | Old-MB diagnostics batch: `audit_mirror_pnl.py`, `mirror_48h_pnl_tiers.py`, `mirror_48h_verify.py`, `mirror_cal_diag.py`, `mirror_conf_charts.py`, `mirror_conf_history.py`, `mirror_factor_eval.py`, `mirror_realistic_pnl.py`, `retroactive_confidence.py` | **RETIRE (batch)** | All 0 external refs (2 comment-only mentions); dead-strategy-era diagnostics |
| 16 | ML-selector remnants: `scripts/train_mirror_ml_selector.py`, `ml_selector_shadow_analysis.py`, `ml_shadow_analysis.py`, `ml_shadow_p2.py`, `ml_vs_regular.py` | **RETIRE (batch)** | Feature removed at S168 (`mirror_bot.py:4292`); zero external refs |
| 17 | Root-level empty files `ml_score_combo`, `ml_score_ql`, `ml_score_xgb` | **RETIRE** | 0-byte accidental artifacts (2026-03-26); the grep hits elsewhere are JSONB keys, not these paths |
| 18 | 29× `AGENT_HANDOFF_MIRRORBOT_*.md` + `PROMPT_MIRRORBOT_SESSION{104,106,127}.md` + `AUDIT_MIRRORBOT_S127.md` + `MIRRORBOT_PRICE_FRESHNESS_PLAN_S244.md` | **ARCHIVE-MOVE** (git mv, not deletion) | HANDOFF_INDEX.md references them 0 times; cross-refs come only from other stale handoffs |
| 19 | `MB_REBUILD_PLAN.md`, `MB_SALVAGE_MANIFEST.md`, `SALVAGE_PACKAGE.json`, `MIRRORBOT_FILTER_AUDIT_S244.md`, `docs/MB_DEEP_DIVE_NEXT_PROMPT.md`-pointer stack, `scripts/edge_verification.py` | **KEEP** | Chartered inputs / only record of live env values / STEP-ZERO pointer / gate disposer |
| 20 | `docs/mirror_scoring_v3.md` | **QUARANTINE** (banner) | Design doc of the circular engine; unmarked it invites reuse |
| 21 | `shadow_fills` DB table (12,892 MB rows @ M0, incl. 12,713 L2 ladders) | **QUARANTINE** label + **urgent hazard flag** | In-sample under FORWARD DATA ONLY; `prune_old_data.py` deletes it at 90-day retention with NO guard — precise-model ladders may already be eroding |
| 22 | Stale `copyable_cache` per-trader histories (2026-07-10-era, 500-row/`status='hft'` truncations) | **QUARANTINE (stale entries only)** | The cache DIR is load-bearing (watcher median seeding, gamma/fee maps live inside) — never retire the dir |
| 23 | `mirror3_shadow_rtds.jsonl` (RTDS A/B sink) | **KEEP** | Designated successor-source evidence; A/B coverage measurements still pending |
| 24 | 07-30 scout capture `rtds_scout/scout_20260730.jsonl` | **KEEP + BACKUP — never retire** | Single-copy, producer never committed (`/tmp/rtds_scout.py`, wiped 08-01), still feeding new decisions (08-24 filter dry-run) |
| 25 | Forward sinks + parked epochs + lock files under `/opt/pa2-shared` | **KEEP + BACKUP** | The entire admissible evidence base; currently zero offsite copies |
| 26 | VPS `/tmp`-era leftovers; repo copies in `scripts/vps_jobs/` | **KEEP repo copies; flag `/tmp` input dependency** | `scout_queue2.sh:10` still reads `/tmp/scout_dive_roster.txt` post-08-01 lesson |
| 27 | Legacy paper rows under `bot_name='MirrorBot'` (trade_events/positions/paper_trades) | **QUARANTINE label — no deletion** | Zero decision weight (FORWARD DATA ONLY); identity-scoped queries already separate them |
| 28 | Duplicated gate/repair constants (0.02/0.05/0.98 ×4 sites; flat-fee 0.02 hardcode) | **KEEP + consolidation ticket** | Live and load-bearing; duplication is a drift risk, not an unused item |

---

## Per-item detail

### 1. Legacy MirrorBot lane + `mirror_rejected_signals` label stream — KEEP (today), RETIRE path defined
**(a) What it is.** The full v2 copy-trader still running in paper: `deploy/polymarket-mirror.service` (ExecStart `main.py`, EnvironmentFile `/opt/pa2-shared/.env` + `.env.mirror`), `bots/mirror_bot.py` (last touched 2026-06-22), `bots/elite_watchlist.py`, registered via `main.py:81` (`BOT_ENABLED_MIRROR`). It opens paper positions (`_execute_mirror_trade` → `place_order`, mirror_bot.py:2898, :4396-4404), runs exits (`:1171`), and writes every rejection to `mirror_rejected_signals` (`_log_rejection`, :2763). Kept alive **deliberately** by MB_REBUILD_PLAN §0 Decision 5: flip-to-paper instead of stop, "a full stop would starve Path-B/validation data." Operator context: still producing ~794 paper trades/7d (UNVERIFIED-from-repo; check with `scripts/bot_pnl.py MirrorBot 168`).
**(b) Consumer evidence (grep, this session).** `mirror_rejected_signals` touchpoints split three ways:
- *Writer:* only `mirror_bot.py` (the stream stops the moment the service stops).
- *Label maintenance (independent of the mirror service):* `base_engine/data/database.py:3950` `backfill_mirror_rejected_signals_resolution()` called from `base_engine.py:1796`, `ingestion_scheduler.py:391`, `resolution_backfill.py:552` — these run in the shared/ingestion processes and only UPDATE resolution on *existing* rows; they keep working after a stop.
- *Decision consumers:* every one is salvage-era: `bots/mirror_backtest/data_access.py:23` (the chartered §2 acceptance-gate signal source), `bots/mirror_scoring/validation.py:41` (quarantined false-PASS machine, item 3), `scripts/backtest_tail_leaderboard.py` and `scripts/check_trader_persistence.py` (both marked superseded, items 6-7), `scripts/crypto_kill_test.py` (item 8), `scripts/mirror_scoring_run.py` (item 4). **The live forward lane (`mirror_v3/` watcher, band/cohort5/bidsim instruments) never touches this table** — its evidence is the JSONL sinks.
**(c) Disposition.** **KEEP today.** The Decision-5 rationale ("starves Path-B/validation data") now protects consumers that are themselves superseded or quarantined; under FORWARD DATA ONLY (08-20) the rejected-signals corpus carries zero decision weight. The one *chartered* consumer left is the MB_REBUILD_PLAN §2 fill-replay acceptance gate (`bots/mirror_backtest/`), which is idle. The retire decision is therefore really one operator ruling: **"Is the §2 rejected-signals acceptance gate superseded by the forward shadow instruments?"** If YES → the label stream has no consumer and the lane can retire. If NO → KEEP until the gate has been run or replaced. Note MB_REBUILD_PLAN §5 "Leaving the old behind" step 3 requires v3 to gain "its own rejection-signal logging" before step 4 "old MB then fully stopped" — the v3 shadow sink's per-signal verdict field (OK/SPREAD_TOO_WIDE/PRICE_RAN_AWAY/NO_BOOK/PRICE_NO_UPSIDE) is arguably that log; the operator should ratify that reading explicitly rather than have it assumed.
**(d) Safe-removal sequence (only after the ruling above).**
1. Backup: confirm last pg_dump (daily 04:00 UTC, `deploy/crontabs/postgres.crontab:6`) succeeded; additionally `pg_dump -t mirror_rejected_signals` to a named file in `/opt/pa2-backups/quarantine/`.
2. Quarantine-mark: `sudo systemctl stop polymarket-mirror && sudo systemctl disable polymarket-mirror` (unit file left in place). Do NOT touch `polymarket-redeem.service` — it sweeps resolved capital independently and MUST keep running.
3. Observe one full cycle (≥7 days recommended, one label-backfill cycle minimum): confirm ingestion Phase A3 backfill still logs `mirror_rejected_signals` updates; confirm no other bot errors referencing MirrorBot; confirm the 123h force-exit-loop symptom (MB_STATE:832-834) is gone from journals.
4. Remove (much later, separate sign-off): MB_SALVAGE_MANIFEST scrap checklist steps 1–3 (deregister from BOT_REGISTRY, delete `mirror_bot.py`/`elite_watchlist.py`, prune shared-module MirrorBot branches) — per MB_REBUILD_PLAN §5 step 5 this is applicable only at final wind-down, and it is a shared-module change requiring the full cross-bot protocol (18 mirror-named test files under `tests/unit/` will need matching removal; `pytest` 1090+ must stay green).
**Rollback:** `sudo systemctl enable --now polymarket-mirror` (code untouched); for step 4, `git revert <sha>`.
**(e) What could break / how we'd notice.** (i) Label stream stops growing → any future session that tries to run the §2 gate on fresh data finds the corpus frozen at stop date — visible in `MAX(event_time)`. (ii) Legacy open paper positions stop being exit-managed → they ride to resolution; `positions` rows under MirrorBot stay OPEN longer — visible in `scripts/bot_pnl.py MirrorBot`. (iii) The v2 partial-restore latent bug (`_state_restored=True` at :496 before the `_entered_market_sides` rebuild at :562-592) becomes moot — it is a reason TO retire, not a risk of retiring. (iv) Anything on the VPS not visible in the repo that greps journals for "MirrorBot" heartbeats could false-alarm — operator should check dashboards once after stop.

### 2. Inert legacy code inside `mirror_bot.py` — QUARANTINE
**(a)** Three verified-inert structures: `_slippage_fail_count`/`_slippage_backoff` (init comment at mirror_bot.py:162-167: "Population logic … from S158 plan was never committed — these dicts are inert"); `mirrored_trades` (write-only tx-hash bookkeeping, init :80 — CLAUDE.md confirms zero membership checks); `_whale_consensus` (":186-187 'legacy — kept for backward compat reads'").
**(b)** Findings JSON (legacy area, verified True) + CLAUDE.md neg-risk section. No guard or gate consults them.
**(c) QUARANTINE.** MB_REBUILD_PLAN §1 scrap-checklist row makes deleting these files FORBIDDEN; piecemeal edits to a running bot violate the surgical-fix directive for zero benefit. They are already comment-marked in place — that IS the quarantine. They retire automatically with item 1 step 4.
**(d)** No removal sequence — bound to item 1. **(e)** Nothing can break: they are provably unconsulted; the only risk is a future session "cleaning them up" — this ledger entry is the do-not-touch marker.

### 3. `bots/mirror_scoring/validation.py` — QUARANTINE (the named precedent)
**(a)** The counterfactual validation harness. Confirmed CIRCULAR by the 61-agent adversarial review 2026-07-09: `_UNIVERSE_SQL` has no time bound, BH admission keys on the post-cutoff test half, "out-of-sample" set shares the same post-cutoff randomness — a false-PASS machine (MB_STATE:1780 landmine; MB_COPYTRADER_CONTEXT §7.6 "PASSes mean nothing").
**(b) Consumers (grep):** exactly one import in the whole tree — `scripts/mirror_scoring_run.py:41` (`from bots.mirror_scoring.validation import validate_ranking`). **No test file imports it** (grep over `tests/unit/test_mirror_scoring_*.py`: zero hits for `validate_ranking`). Notably, **the file itself carries no warning banner** — its docstring still reads as a working kill-criterion harness; the quarantine documentation lives only in MB_STATE §7 and MB_COPYTRADER_CONTEXT.
**(c) QUARANTINE, not retire:** MB_STATE:1780 notes FAILs from it are directionally credible (the bias runs toward PASS), so the file has residual forensic value, and it is the operator's cited precedent for keep-but-mark.
**(d) Quarantine-mark action (needs a write session + sign-off):** add a top-of-file banner: `# ⛔ QUARANTINED 2026-07-09 (61-agent review): CIRCULAR — a PASS from this file must never clear anything. FAILs directionally credible. See MB_STATE §7.` and make `mirror_scoring_run.py --stage validate` print the same banner. Rollback: `git revert <sha>`.
**(e)** Break risk ≈ nil (banner-only change; the one importer imports a function, not the docstring). What we'd notice if quarantine is violated: a future session citing a validation PASS — the banner makes that impossible to do innocently.

### 4. `bots/mirror_scoring/` package + `scripts/mirror_scoring_run.py` — QUARANTINE
**(a)** The v3 whale trader-ranking engine (q_score, tailability, estimand, exit_replay, stats, config, price_lookup) and its runner. Stage-1 FAIL is hearsay (commit `172d72a` unrecoverable), the validation harness is circular (item 3), and the whole ranking approach was superseded by the 07-10 operator pivot to full-history copy-trader search ("Why the f— are we running on leftovers?").
**(b) Consumers (grep):** runner `scripts/mirror_scoring_run.py` (imports q_score + validation); `deploy/mb_vps_oneshot.sh:35-39` (item 5); five green test files (`test_mirror_scoring_{estimand,exit_replay,pipeline,runner,stats}.py`) pin the package. No live instrument or service imports it.
**(c) QUARANTINE:** keep the package and its tests green (cheap; deleting breaks the suite and burns review bandwidth for zero risk reduction), mark the package `__init__` docstring with the same banner as item 3. Retire later only as part of a deliberate cleanup commit that removes the five test files in the same change.
**(d)** Banner commit as item 3; rollback `git revert`. **(e)** Nothing consumes it at runtime; the only hazard is silent reuse, which the banner addresses.

### 5. `deploy/mb_vps_oneshot.sh` — RETIRE
**(a)** Read-only VPS diagnostic (last commit 2026-07-05) that reports mirror mode, sports-share of labeled signals, and then **git-clones the stale branch `claude/mb-formula-review-vdxmtr` into /tmp to run `mirror_scoring_run.py --stage validate`** — i.e. its "algo brain go/no-go" stage runs the quarantined scoring stack from a frozen branch.
**(b) Consumers (grep):** nothing references `mb_vps_oneshot` anywhere (no service, timer, cron, doc pointer besides itself). Superseded by the v3 lane's own tooling (`mb_scoreboard.py`, readout cron).
**(c) RETIRE.** **(d)** Sequence: git snapshot → `git mv deploy/mb_vps_oneshot.sh docs/archive/mb_attic/` (quarantine-mark) → one deploy cycle green (deploy.sh does not reference it — verified by grep) → optional delete. Rollback: `git revert <sha>`. Also confirm no copy lingers on the VPS: `ls /opt/polymarket-ai-v2/deploy/mb_vps_oneshot.sh` post-deploy.
**(e)** Break risk: an operator muscle-memory invocation fails with file-not-found — the attic copy plus this ledger line is the remedy. Nothing automated calls it.

### 6–8. Superseded kill-test-era instruments — RETIRE (each individually signed)
**(a)** `scripts/backtest_tail_leaderboard.py` (2026-07-09; tail backtest at ~10s lag on the rejected corpus), `scripts/check_trader_persistence.py` (2026-07-09; cross-period autocorrelation), `scripts/crypto_kill_test.py` (2026-07-12; category kill test). All three ran, delivered verdicts recorded in MB_STATE, and were then superseded — the tooling map (MB_COPYTRADER_CONTEXT §8) literally marks the first two "(superseded)".
**(b) Consumers (grep):** zero imports/invocations outside their own paired tests (`test_tail_backtest_script.py`, `test_persistence_check_script.py`, `test_crypto_kill_test.py`) and doc mentions. `backtest_tail_leaderboard` imports `bots/mirror_backtest/gate.py` (not vice versa). MB_STATE §5 lists the "tail backtest" among the 07-14 fossil TO-DO's superseded instruments.
**(c) RETIRE** (results live on in docs; the scripts themselves are the unused item).
**(d)** Sequence per script: git snapshot → `git mv` script + its paired test into `docs/archive/mb_attic/` in ONE commit (moving the script alone reds the suite) → full `pytest` green → one cron day (none of the 11:40Z/12:30Z jobs reference them — verified: cron scripts reference only shadow_readout/band_tracker/cohort5/label_and_fee_refresh/mb_scoreboard) → delete later if desired. Rollback: `git revert <sha>`.
**(e)** Break: only the test suite, which the same-commit rule prevents. If a future session wants to re-run a kill test, the attic copy is intact; results are already in MB_STATE.

### 9–13. Salvage-era one-shot scripts — RETIRE (batch, individually listed)
**(a/b)** All verified zero-consumer by grep this session (only comment/doc mentions):
- `scripts/counterfactual_pnl.py` (2026-05-06) — reads in-sample `shadow_fills`; the only "counterfactual_pnl" hits elsewhere are an unrelated DB column comment.
- `scripts/shadow_analysis.py` (2026-03-26) — **broken** (`db.initialize()` → crashed the 2026-07-02 VPS run; named "do not carry as-is" in MB_REBUILD_PLAN §4).
- `scripts/mirror_whale_analysis.py` (2026-03-26) — **broken** (queries nonexistent `elite_traders`; §4).
- `scripts/slippage_check.py` — **broken** (str→TIMESTAMP bind; §4).
- `scripts/verify_salvage_data.py` (2026-07-03) — the M0/M2 data verifier; M2 is ✅ CLEARED 2026-07-02 (`docs/m0_db_results_2026-07-02.md`), purpose spent. Referenced only in a `mirror_backtest/data_access.py` docstring.
**(c) RETIRE all five.** The three broken ones are the strongest candidates in the whole ledger — they cannot run.
**(d)** Same attic sequence as item 6; none has a paired test file (verified: no `test_shadow_analysis/test_slippage_check/test_verify_salvage/test_counterfactual` under tests/unit). Rollback: `git revert <sha>`.
**(e)** Break: nothing runnable breaks. Loss of the salvage-audit trail is prevented by attic-move-not-delete.

### 14. `scripts/backtest_copyable_fills.py` — QUARANTINE
**(a)** The post-PASS fill-quality gate for a named roster (pessimistic coarse model at the real ask; SURVIVES/FILL-KILLED/NO-BOOK per trader), 2026-07-11.
**(b) Consumers (grep):** imports `find_copyable_traders` as a library; paired test `test_copyable_fills_script.py`; **still named as a mandatory step in the MB_COPYTRADER_CONTEXT §6 decision tree** (chain audit → fill gate → operator decision). In practice the forward shadow watcher now measures fill quality directly and continuously — the retrospective gate is superseded-in-fact but not superseded-on-paper.
**(c) QUARANTINE** (keep + mark "superseded by forward shadow fill measurement — use only if the §6 retrospective path is deliberately revived"). Retire only when the operator formally closes the walkforward/§6 decision tree.
**(d)** Banner commit; rollback `git revert`. **(e)** Nothing breaks; hazard is a future session running a retrospective fill gate when forward data exists — the banner prevents that.

### 15. Old-MB diagnostics batch — RETIRE (one signature covers the batch)
**(a)** Nine dead-strategy-era diagnostic/chart scripts: `audit_mirror_pnl.py` (03-29), `mirror_48h_pnl_tiers.py`, `mirror_48h_verify.py`, `mirror_cal_diag.py`, `mirror_conf_charts.py`, `mirror_conf_history.py`, `mirror_factor_eval.py`, `mirror_realistic_pnl.py`, `retroactive_confidence.py` (S130-era).
**(b) Consumers (grep, per-file counts run this session):** all zero external refs except two *comment-only* mentions (`bot_pnl.py:722` cites `mirror_conf_charts.py` in a comment; a test docstring mentions `mirror_realistic_pnl.py`'s f-string pattern). No service/timer/cron/doc-procedure invokes any of them. They analyze the dead whale-copy strategy's confidence/P&L — in-sample by definition under FORWARD DATA ONLY.
**(c) RETIRE (batch).** **(d)** One attic commit for all nine (none has a paired test); update the two comments only if desired (they are inert). `pytest` green → one cycle → delete later. Rollback: `git revert <sha>`.
**(e)** Break: none mechanical. Risk is losing a chart script someone liked — attic copy covers it.

### 16–17. ML-selector remnants — RETIRE
**(a)** The S-era ML selector was **removed from the bot at S168** (`mirror_bot.py:4292`: "ML selector removed (was shadow-only, MIRROR_USE_ML_SELECTOR=false)"). Leftovers: trainer `scripts/train_mirror_ml_selector.py` (05-14), analyzers `ml_selector_shadow_analysis.py`, `ml_shadow_analysis.py`, `ml_shadow_p2.py`, `ml_vs_regular.py`, and — separately signed — three **0-byte files at repo root**: `ml_score_combo`, `ml_score_ql`, `ml_score_xgb` (committed 2026-03-26; `file` confirms empty; almost certainly shell-redirection accidents).
**(b) Consumers (grep):** `MIRROR_USE_ML_SELECTOR` appears only in the S168 removal comment. No script/service imports the five .py files. The strings `ml_score_xgb` etc. elsewhere are **JSONB keys inside `trade_events.event_data`** read by the analyzers themselves — not references to the root files.
**(c) RETIRE** both sub-items. The empty root files are the single safest removal in this ledger.
**(d)** Attic commit (root empties can go straight to deletion after the observation cycle — there is nothing to back up; `git rm ml_score_combo ml_score_ql ml_score_xgb`). Rollback: `git revert <sha>`.
**(e)** Break: none. Historical `event_data` keys in the DB are untouched and remain queryable.

### 18. MB handoff/prompt docs — ARCHIVE-MOVE (never delete)
**(a)** 29 files `AGENT_HANDOFF_MIRRORBOT_SESSION85…150*.md` at repo root (March-era sessions), plus `PROMPT_MIRRORBOT_SESSION104/106/127.md`, `AUDIT_MIRRORBOT_S127.md`, and `MIRRORBOT_PRICE_FRESHNESS_PLAN_S244.md` (plan executed — `test_mirror_price_freshness.py` pins the shipped behavior). One stray copy also sits in `memory/AGENT_HANDOFF_MIRRORBOT_SESSION96_FULL_2026_03_16.md`.
**(b) Consumers (grep):** `HANDOFF_INDEX.md` contains **zero** occurrences of "MIRRORBOT" (verified — the index never covered them). Cross-references exist only FROM other stale handoffs/prompts (7 hits, all themselves archive-class). No script or doc-procedure reads them. Current MB truth lives in `docs/MB_STATE.md` + `docs/MB_DEEP_DIVE_NEXT_PROMPT.md` (rewritten 08-20), which explicitly supersede session handoffs.
**(c) ARCHIVE-MOVE:** `git mv` the set into `docs/archive/mb_handoffs/` (new dir — no `docs/archive/` exists yet; this creates the house archive precedent). NOT deletion — they are the only narrative record of sessions 85–150.
**(d)** One commit; add a one-line `docs/archive/mb_handoffs/README.md` saying what moved and why; leave `HANDOFF_INDEX.md` untouched (it never pointed at them). Observe one cycle (nothing can consume them; the cycle is a formality). Rollback: `git revert <sha>`.
**(e)** Break: stale intra-doc links from other archived handoffs — cosmetic. Keep `MIRRORBOT_FILTER_AUDIT_S244.md` OUT of this move (item 19: it is the only record of live env values).

### 19. Chartered/provenance docs and tools — KEEP (listed so nobody "cleans" them)
`MB_REBUILD_PLAN.md` (binding operator decisions incl. Decision 5 and the §5 wind-down sequence), `MB_SALVAGE_MANIFEST.md` + `SALVAGE_PACKAGE.json` (the scrap checklist that item 1 step 4 will finally execute), `MIRRORBOT_FILTER_AUDIT_S244.md` (**sole record of legacy live env values** — e.g. whale gate $5 live vs $100 code default `config/settings.py:461`; the findings JSON itself had to be corrected against it), `docs/MB_STATE.md`, `docs/MB_DEEP_DIVE_NEXT_PROMPT.md`, `docs/MB_COPYTRADER_CONTEXT.md`, `docs/{BAND,COHORT5}_PREREGISTRATION.md`, `docs/BIDSIM_DESIGN.md`, `scripts/edge_verification.py` (Decision-1 gate disposer). **KEEP all.** No action, no sign-off needed — listed defensively.

### 20. `docs/mirror_scoring_v3.md` — QUARANTINE (banner)
Design doc of the circular scoring engine (items 3–4). Grep: referenced only from mirror_scoring code comments. Unmarked, it reads as a live design. Action: same banner as item 3, one line at the top. Rollback: `git revert`.

### 21. `shadow_fills` DB table — QUARANTINE label + urgent hazard flag
**(a)** Old-MB shadow-fill table: 12,892 MB rows at M0 (5,208 executed; **12,713 with stored L2 ladders** — the "precise fill model" substrate of the §2 gate; `docs/m0_db_results_2026-07-02.md:22-40`).
**(b) Consumers (grep):** only retrospective tools — `verify_salvage_data`, `shadow_analysis`, `prune_old_data`, `counterfactual_pnl`, `backtest_tail_leaderboard`, `backtest_copyable_fills` — none of the four live forward instruments touch it. It sits inside the pg_dump perimeter (ironically better-backed-up than the irreplaceable forward sinks).
**(c) QUARANTINE label** (in-sample; zero decision weight) — do NOT delete rows.
**⚠ HAZARD (new, found this session):** `scripts/prune_old_data.py` retention for `shadow_fills` is **90 days with an UNGUARDED DELETE** (`prune_old_data.py:42,76-83` — plain `created_at < NOW()-90d`, no open-position guard like trade_events has), and `deploy/polymarket-prune-data.service` + `.timer` run it with `--table all --execute`. If that timer is enabled on the VPS (UNVERIFIED-from-repo), ladder rows older than ~2026-05-27 are already being deleted on schedule — the precise-model evidence erodes monthly. **If the operator wants the §2 precise model to remain possible, snapshot now:** `pg_dump -t shadow_fills polymarket | gzip > /opt/pa2-backups/shadow_fills_full_$(date +%Y%m%d).sql.gz` and copy it off-box. If instead the §2 gate is ruled superseded (item 1 ruling), this hazard becomes moot and the retention is fine.
**(d/e)** No removal action. Notice-of-erosion: compare `SELECT COUNT(*) FROM shadow_fills` against the M0 figure 12,892.

### 22. Stale `copyable_cache` per-trader histories — QUARANTINE stale entries only
**(a)** Per-address API history JSONs under `/opt/pa2-shared/copyable_cache/`, built by `find_copyable_traders.py`. A 2026-07-10-era subset is truncated at 500 rows with `status='hft'` and **silently fed scout dives** via `chain_deep_dive.py`'s cache-hit short-circuit (:1283-1291) — the documented 08-01 incident produced verdicts with `incomplete_cache_sweep=TRUE`.
**(b) Consumers (grep, 17 files):** the cache DIR is **load-bearing for the live watcher** — conviction-median seeding at boot (`copy_watcher.py:518-526`) — and it also contains `gamma_resolutions.json`, `fee_map.json`, `fee_rate_map.json`, and `chain_fills/`, consumed by the entire daily verdict stack (shadow_readout, band_tracker, cohort5, build_fee_map, shadow_label_supplement). **The directory must never be retired.**
**(c) QUARANTINE (stale per-address entries only).** Two acceptable mechanisms, operator picks: (i) *passive* — rely on `chain_deep_dive --refresh` for all future dives and record that rule in MB_STATE (no file touched); (ii) *active* — on the VPS, move only the identified stale per-address files: `mkdir -p /opt/pa2-shared/copyable_cache/QUARANTINE_20260710_era && mv <stale files> …/QUARANTINE_20260710_era/` after `cp -a` backup. **Coupling warning for (ii):** if any quarantined address is on the current clean roster, the watcher's median seeding for that trader changes at next restart (cold-start 1.0x until 20 obs) — check the roster (`chain_audit.json` "clean") against the stale list first, and prefer (i) if any overlap exists.
**(d)** Backup → mv → observe one full daily cron cycle (readout/band/cohort5 logs clean, watcher boot log shows expected "medians seeded for N/M") → nothing further (quarantine is the end state). Rollback: `mv` back.
**(e)** Break: a dive on a quarantined address re-fetches (slower, correct); watcher seeding coverage drops if the coupling warning is ignored — visible in the boot log line `conviction medians seeded for N/M`.

### 23. `mirror3_shadow_rtds.jsonl` — KEEP
Sole scripted consumer is `build_fee_map.py:275` (grep-verified), but this file is the **A/B leg for the pending chain→RTDS source-swap decision** — the findings' own review questions (maker-leg coverage, side-label agreement, proxyWallet equivalence) are unmeasured and require exactly this sink. **KEEP; no action.** (Its unbounded growth is a rotation ticket for the rebuild, not a disposition.)

### 24. 07-30 scout capture — KEEP + BACKUP, never retire
`/opt/pa2-shared/rtds_scout/scout_20260730.jsonl`: 6h full-firehose capture (983,755 trades / 26,123 wallets). **Single copy; producer was `/tmp/rtds_scout.py`, never committed, wiped in the 08-01 reboot — a fresh capture requires re-writing the tool.** Still feeding NEW decisions: the 08-24 scout-filter dry-run (82 human-scale candidates) and the bidsim 400k-row BUY/SELL feed-asymmetry read both ran on it. Repo consumer: `scripts/vps_jobs/scout_roster_build2.py:4`.
**Action (operator one-liner):** `cp -a /opt/pa2-shared/rtds_scout/scout_20260730.jsonl /opt/pa2-backups/ && gzip -k /opt/pa2-backups/scout_20260730.jsonl` — and ideally scp one copy off-box. **This item is KEEP no matter what; sign-off requested only for the backup step.**

### 25. Forward sinks, parked epochs, lock files — KEEP + BACKUP (defensive listing)
`mirror3_shadow.jsonl`, `mirror3_bidsim.jsonl` (+ parked `*.pre-amend1-20260821` / `*.pre-amend1b-20260821`), `chain_audit.json` (+ its `.bak` chain), `deep_dive*/` (verdict_locks.json, band_lock.json, cohort5_qual_locks.json, shadow_readout_log.txt durable log, label_fee_refresh.log). The findings establish: the only scheduled backup on the box is the pg_dump — **none of these flat files is backed up anywhere**, and MB_STATE rules "where they differ the durable log is the record." **KEEP + one rsync/scp job proposal** (a build item for the overhaul, listed here so no cleanup session mistakes `.bak`/parked files for clutter — the parked bidsim epochs are frozen evidence, not stale copies).

### 26. `/tmp`-era scripts — KEEP repo copies; flag the remaining `/tmp` dependency
Repo copies `scripts/vps_jobs/scout_queue2.sh` + `scout_roster_build2.py` are the canon (created after the 08-01 "/tmp is not a home" lesson) — **KEEP**. Residual hazards, flag-only (read-only session): (i) `scout_queue2.sh:10` still reads its roster from `/tmp/scout_dive_roster.txt` — regenerated by `scout_roster_build2.py:32`, so reproducible, but a reboot mid-campaign silently empties the queue input; (ii) any `/tmp` copies on the VPS predating 08-01 are already gone — nothing to retire. Proposed one-line fix for a future write session (NOT this ledger): point the roster path into `/opt/pa2-shared/rtds_scout/`.

### 27. Legacy `bot_name='MirrorBot'` DB rows — QUARANTINE label, no deletion
trade_events/positions/paper_trades rows from the dead strategy keep accreting while item 1 runs. Identity-scoping already separates them from MirrorBotV3 reads (`mirror_v3/run.py:62-75`); `trade_events` carries an immutability trigger (migration 043); `bot_pnl.py` is the only sanctioned reader. **QUARANTINE = a one-line MB_STATE note "MirrorBot-identity rows are in-sample; zero decision weight" — no data action ever.** (Retention already handles paper_trades/trade_events aging via the prune service.)

### 28. Duplicated gate/repair constants — KEEP + consolidation ticket
The gate values 0.02 chase / 0.05 spread / 0.98 max-fill live independently in: `mirror_v3/copy_watcher.py` (env-configurable defaults :337-339, the LIVE gates), `scripts/analyze_shadow.py` repair defaults (:77, :355-356), `scripts/shadow_readout.py` inline namespaces (:788, :893) + CLI defaults (:1109-1110), `scripts/cohort5_qualification.py:129` inline NS; plus the flat-fee 0.02 hardcoded inline at `band_tracker.py:74` instead of config, and the fee-precedence chain re-implemented three times (established by findings; fee divergence already consumed at least one immutable DQ lock). **KEEP (all live/load-bearing — not unused)**, but this is the ledger's one "duplication debt" entry: the overhaul should centralize one gates+fees module all verdict scripts import. If the watcher's env knobs ever change, the readout-side copies do NOT follow — that drift would silently change every verdict. No sign-off needed; recorded as a named rebuild ticket.

---

## Execution notes (apply to every YES)
- Each RETIRE/ARCHIVE/QUARANTINE-banner is **its own commit** (one fix per commit), with the CLAUDE.md change-log block, on an MB-lane branch; full `pytest` before and after; script+paired-test moves in the same commit.
- Order of operations if multiple YES: items 17 → 15 → 16 → 9–13 → 6–8 → 5 (pure-repo, safest first), then 18 (archive move), then 3/4/20/14 (banners), then VPS items 22/24/25 (operator one-liners), with item 1 LAST and only after its named ruling.
- One full observation cycle = full pytest + one deploy + one complete 11:40Z/12:30Z cron day with clean readout/band/cohort5/scoreboard output.

## One-word sign-off sheet
Answer per line, e.g. "1 defer, 5 yes, 17 yes, 21 snapshot …". "Yes" authorizes exactly the disposition named — nothing more.

1. Legacy MirrorBot lane: rule the §2 rejected-signals acceptance gate superseded and start the retire sequence? (yes = retire path begins / no = keep alive / defer)
2. Inert mirror_bot.py structures: ratify QUARANTINE-in-place (retire only with item 1)?
3. validation.py: add the in-file QUARANTINE banner?
4. mirror_scoring package + runner: QUARANTINE banner (keep tests green)?
5. deploy/mb_vps_oneshot.sh: RETIRE (attic)?
6. backtest_tail_leaderboard.py (+test): RETIRE (attic)?
7. check_trader_persistence.py (+test): RETIRE (attic)?
8. crypto_kill_test.py (+test): RETIRE (attic)?
9. counterfactual_pnl.py: RETIRE (attic)?
10. shadow_analysis.py (broken): RETIRE (attic)?
11. mirror_whale_analysis.py (broken): RETIRE (attic)?
12. slippage_check.py (broken): RETIRE (attic)?
13. verify_salvage_data.py (M2 spent): RETIRE (attic)?
14. backtest_copyable_fills.py: QUARANTINE banner (supersede-in-fact note)?
15. Nine old-MB diagnostics (batch): RETIRE (attic)?
16. Five ML-selector scripts (batch): RETIRE (attic)?
17. Root empty files ml_score_{combo,ql,xgb}: RETIRE (delete after cycle)?
18. MB handoff/prompt docs (34 files): ARCHIVE-MOVE to docs/archive/mb_handoffs/?
19. Chartered docs list: acknowledge KEEP (no action)?
20. docs/mirror_scoring_v3.md: QUARANTINE banner?
21. shadow_fills table: (a) ratify QUARANTINE label AND (b) snapshot now before further 90-day pruning? (answer: "21 snapshot" / "21 label-only" / "21 no")
22. Stale copyable_cache entries: passive (--refresh rule) or active (VPS quarantine dir)? (answer: "22 passive" / "22 active" / "22 no")
23. mirror3_shadow_rtds.jsonl: acknowledge KEEP?
24. 07-30 scout capture: authorize the backup one-liner? (KEEP is not in question)
25. Forward sinks/locks/parked epochs: authorize adding an off-box backup job to the build queue?
26. scripts/vps_jobs repo copies: acknowledge KEEP + queue the /tmp-roster-path fix?
27. Legacy MirrorBot DB rows: ratify the one-line zero-decision-weight note in MB_STATE?
28. Gate/fee constant consolidation: queue as a named rebuild ticket?


---
## POST-DRAFT VERIFICATION + EMERGENCY ACTION (2026-08-25 ~13:58Z, VPS reads)
Item 21's hazard is CONFIRMED ACTIVE, not hypothetical:
- polymarket-prune-data.timer ACTIVE, last ran 2026-08-25T03:32:51Z, next
  2026-08-26T03:30:58Z.
- shadow_fills = 9,514 rows spanning 2026-05-27..2026-08-25 (psql count,
  13:57Z) vs the 12,713 documented - ~3,199 rows already pruned; the oldest
  surviving row sits exactly at the 90-day retention edge, i.e. EROSION IS
  ONGOING DAILY.
- PROTECTIVE COPY TAKEN (additive, no disposition executed): pg_dump of
  shadow_fills to /opt/pa2-backups/mb_evidence/
  shadow_fills_snapshot_20260825.dump (2.9MB, pg_restore-verified TOC).
  Tonight's evidence bundle + the Windows pull carry it off-box.
- The ~3,199 already-pruned rows are likely unrecoverable (DB dumps retain
  7 days on the same box). Stated plainly.
- STILL OPERATOR-GATED: excluding shadow_fills from prune_old_data (a
  shared-module change) - sign-off sheet item 21.
