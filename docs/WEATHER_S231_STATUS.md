# WEATHER S231 SESSION HANDOFF (2026-07-15 → 07-16, local Windows session, wb-whiteboard worktree)

> Session type: WB-scoped, the DEEP-BACKTEST session (operator-approved program from
> S230). **ZERO bot-code changes, ZERO deploys** — live release is still
> `20260714_003205` (rollback `20260713_160143`), exactly as S229 left it. Everything
> below is research scripts, ONE new VPS research cron (pws_mesh), and docs.
> Verification of THIS handoff: `bash scripts/wb_resume_check.sh` (manifest updated
> this session) + §7 below.

## 0. QUICK FACTS (cross-check these when verifying the handoff)

| Fact | Value | Source |
|---|---|---|
| **Peak-model GATE (task 1)** | **PASS** — TEST n=135 meanEV **+0.091 (SE ~0.039)**; +0.091−2SE > 0 AND ≥ +0.05 (pre-registered bar); family-clustered: 100 family-days, day-mean +0.107, cSE ~0.040 (~2.7σ); TRAIN +0.084 (n=219); rejected-by-rule −0.007 (n=718) | `~/wb_research/nowcast_peak_133d.out` |
| Coverage (task 1) | 719 family-days 03-01..07-12 (question-date keyed; was 406), 1,072 priced entries (796 DB-forecast / 276 archive-fill / 390 March) | same |
| Robustness cuts (task 1) | DB-only TEST +0.083 (n=58); **ARCH-only TEST +0.059 (n=148, ~1.6σ — does NOT clear 2σ alone)**; DB−arch forecast-max offset +0.64F mean; 90d h≥14 flip REVERSED (+0.054 n=175 — was noise) | same |
| Archived-forecast source | previous-runs API `temperature_2m_previous_day1` (issued D−1 → no lookahead); historical-forecast mosaic REJECTED (shortest-lead = lookahead); historical-ensemble API only reaches 2026-04-13 | probes 07-15, spec |
| Maker fills (task 2) | 304 reveal windows 03→07 (months 3/4/5/6/7 = 93/95/9/74/33); median p0 0.68; median repricing +8¢; any-fill 97/95/93/86% at p0−0/1/2/5¢; **POST-reveal-only 74/71/65/54%**; control (−3.5h) 80-87% — ALL UPPER BOUNDS | `~/wb_research/maker_fill_133d.out` |
| **9-12h cell (task 3)** | **DEAD bot-independently**: +0.002 (cSE 0.019, n=692 bets / 368 family-days); all hour buckets ≈ 0; thresholds 0.05/0.10/0.15 flat. S230's +0.118 (n=66 bot rows) = bot-conditional or under-clustered noise | `~/wb_research/dayof_cell_133d.out` |
| Gamma probe (task 4) | CLOSED — temp dailies began **2025-12-28** (19 events / 133 markets pre-2026, Dec 28-31 only); no 2025-summer out-of-regime set | Gamma events tag_slug=daily-temperature ascending |
| May DB hole | 82 resolved YES buckets in May vs 439-632 adjacent months (all cities) — ingestion gap, thins early TEST halves | SQL on markets by question month |
| **Phase 1 (operator: "build it")** | **BUILT + LIVE**: `pws_mesh.py` cron `2-57/5 * * * *`, active-market US cities in local 09-21, ≤4 WU PWS each, first ticks wrote 233 obs | VPS crontab + `pws_mesh_20260716.jsonl` |
| EMOS correction path | VERIFIED (code read): per-station shift reaches bucket TAILS in BOTH routes — parametric loc shift (probability_engine.py:151 → `_integrate_bucket`) and empirical member shift (:305); `_global_emos_by_station` consulted at :553 | code read |
| AsosOneMinClient | `ASOS_1MIN_ENABLED` unset everywhere → NEVER instantiated live; recommend leave dormant, spend nothing | env grep + weather_bot.py:785-788 |
| executable_replay (info) | 25 resolved / 24 family-days: leader-at-ask LOSES h10-16, +0.061/n=2 h17 at ask≥0.94 — maker-not-taker read holds; needs weeks | run 07-15 |

## 1. WHAT CHANGED (all commits on `claude/new-whiteboard-session-9b23tq`, all pushed)

- `dd428d0` **nowcast_peak_model.py S231** — archived previous-runs forecasts (no-lookahead),
  question-date family keying, DB-only/ARCH-only cuts, clustered-SE info. Rule FROZEN.
- `559988e` **maker_fill_study.py** — historical maker-fill UPPER bounds (task 2).
- `dda3c66` **dayof_cell_scale.py** — bot-independent 9-12h re-cut, clustered SEs (task 3).
- `2968532` research README — S230 late harnesses + S231 harnesses documented.
- `332d3f2` **pws_mesh.py** — Phase-1 PWS-mesh collector (operator-approved build).
- (this commit) docs: spec S231 results block + supersessions, WEATHER_STATUS header/OD-2/4b/
  changelog, this handoff, S232 kickoff, manifest → S231.

## 2. VPS STATE (changes this session — all research-layer, reversible)

- **New cron:** `2-57/5 * * * * ~/wb_research/pws_mesh.sh` (offset from shadow_book :00
  and trade_prints :05). Rollback = remove the line. Logs `pws_mesh_YYYYMMDD.jsonl`
  (+`pws_mesh_err.log`, state `.pws_mesh_state.json`).
- **New files in `~/wb_research/`:** nowcast_peak_model_s231.py, maker_fill_study.py,
  dayof_cell_scale.py, pws_mesh.{py,sh}; outputs nowcast_peak_133d.out,
  maker_fill_133d.out, dayof_cell_133d.out.
- **Service untouched.** Health at session open: EMOS loads 4/24h, reload/fit failures 0,
  avg_clim_mean 0, leak SQL 0. Release `20260714_003205` unchanged.
- **Local operator machine:** stale `wb-s222-gate-check` scheduled task DELETED
  (was set to re-fire 07-16; verification already ran twice; SKILL.md left on disk).

## 3. THE VERDICT CHAIN (why each conclusion is what it is)

1. **The gate PASSED because power arrived, not because the rule changed.** Same frozen
   rule, same split convention; archived previous-runs forecasts filled the holes that
   cost the 90d run half its entries and added March. Estimate went +0.074 → +0.091 with
   SE 0.060 → 0.039. The one 90d anomaly (h≥14 negative) reversed — it was noise.
2. **Lookahead hygiene is the load-bearing detail:** only the previous-runs variant
   (`..._previous_day1`, issued D−1) is a genuine pre-crossing forecast. The
   historical-forecast API is a shortest-lead mosaic ≈ analysis — using it would have
   manufactured a fake PASS. ARCH-only being WEAKER (+0.059) than PRIMARY is the
   expected signature of honest staler forecasts, not a red flag.
3. **Capture-side (task 2) says maker fills exist post-reveal** (54-74% windows, upper
   bounds) — so a proven edge is plausibly harvestable maker-side at small size. The
   high CONTROL fill rate (80-87%) is the warning: bids fill when you're wrong too;
   adverse selection is priced by the (passed) peak model, not by fill mechanics.
4. **The 9-12h cell died under a bot-free signal at scale** — the market prices raw
   public info efficiently. This kills the "generic disagreement" route and leaves the
   crossing-finality selection as the ONLY validated edge. Consistent picture: peak-model
   rejects ≈ 0 EV; day-ahead duels lost (S229/S230); 9-12h ≈ 0 at scale.
5. **No pre-2026 history exists** — out-of-regime validation must come from forward
   accrual, which is exactly what the loggers + pws_mesh now collect.

## 4. CORRECTIONS TO PRIOR-SESSION CLAIMS (do not re-cite the stale versions)

- S230 "9-12h cell SURVIVED accrual (+0.118, 2.1σ)": **SUPERSEDED — DEAD bot-independently**
  (task 3). Keep only the passive clean-window watch.
- S230 "GATE NOT MET / NO Phase-1 infra spend": **SUPERSEDED — gate PASSED at S231 power;
  Phase 1 built on operator approval.**
- Spec's "bot already has WU integration (S224 WS-2)": the bot's WU path is a
  **history-page scrape**, not an API — no WU API key exists anywhere. pws_mesh uses the
  public web key (unofficial; recorded dependency caveat).

## 5. NEXT SESSION (S232) — priorities

> **S231-LATE DELTA #2 (operator: "fold in 4-6 also — go — always global, not
> negotiable"):** GLOBAL MANDATE codified (memory `feedback_wb_always_global.md`
> + spec §GLOBAL MANDATE) — pws_mesh went GLOBAL 07-17 00:42Z (MMMX confirmed;
> forward-only data was the urgent piece); mesh_validation ICAO-global; EGLC
> probe: half-hourly METARs (2× reveal cadence non-US). FIVE pre-registered
> studies ran frozen-rules-first (commits `c005592`/`6d496fc`): **A DEAD**
> (market does NOT carry the print-world bias), **B DEAD + discovery** (lock
> rules false-fire ~9.3-9.5% — settlement-source/boundary risk ≈9%,
> cross-validates rep_bias 81%; feeds Phase-2 sizing), **C signal-positive at
> n=10 = NO verdict** (market kills the dead lane pre-print; rare survivor
> pays ~+0.34/sh; accrue, don't build), **D DEAD** (no revision lag),
> **GLOBAL peak robustness: US cut REPRODUCES the gate** (TEST +0.084 n=183
> on the shifted window), **non-US INCONCLUSIVE** (90 entries, mixed signs —
> global mesh accrues the sample). Ops lesson stamped: 3× pkill/pgrep
> self-match incidents — ALWAYS use [b]racket patterns in kill one-liners.
> Anomaly noted: the 14:40Z A/B runs died and re-ran ~00:35Z (same frozen
> scripts; cause unidentified — likely VPS-side; outputs consistent).

> **S231-LATE DELTA (operator "do next session items now"):** item 2's harness
> is BUILT (`mesh_validation.py`, staged on VPS) and its --bias half RAN on the
> first evening of mesh data (raw mesh−METAR +0.72F mean, per-city −2.5..+3.4F,
> sd 1.7F, n=19 → per-PWS debiasing mandatory); item 3's DESIGN is WRITTEN
> (spec §"PHASE-2 DESIGN (S231)"). S232's real remaining work = --lead runs
> from ~07-18 + acceptance-gate verdict; Phase-2 IMPLEMENTATION still needs an
> explicit operator go. Kickoff prompt updated accordingly.

1. §0 mechanical verification (resume check + VPS spot-checks + quick-facts re-derive).
2. **Mesh validation:** once IEM 1-min catches up (~42h), reconstruct running-max curves
   from `pws_mesh_*.jsonl` and compare vs IEM 1-min + print times — does the mesh
   reproduce the 58-min median lead (nowcast_skill.py numbers)? Also check PWS-vs-METAR
   bias/scatter per city (mesh must predict the PRINT world).
3. **Phase-2 DESIGN (operator-scoped, no code without go):** paper strategy spec —
   `WEATHER_NOWCAST_ENTRY_ENABLED` flag-OFF, maker-first (task 2), separate model_name,
   all existing risk plumbing unchanged; sizing honesty ~$100/window.
4. Executable capture keeps accruing (executable_replay ≥50/cell bar; shadow books +
   trade prints + mesh all running).
5. Standing queue unchanged: S222 re-cut at n≥200-ish; calibrator hands-off ~08-07;
   bootstrap landmine one-commit fix post-verdict.

## 6. OPERATOR STANDING ITEMS (echo until confirmed done)

1. **ROTATE trading wallet `0xd6a5…627F`** (unchanged; detail 2e row 8).
2. **VPS release pruning** — unblocked; ~42G in 11 fat legacy releases; disk ~62%.
3. ~~DELETE local `wb-s222-gate-check` task~~ **DONE this session.**
4. **NWWS-OI application** (free) — now feeds a PASSED program's react leg; apply early.
5. **NEW: WU key or Synoptic token** — replace pws_mesh's public-web-key dependency
   (swap via `WU_WEBKEY` env in `pws_mesh.sh`); also answers the WU rate-limit question.

## 7. HOW TO VERIFY THIS HANDOFF

1. `bash scripts/wb_resume_check.sh` — manifest pins S231 commits/docs/scripts. Expected:
   ALL PASS except the known agent-WORKTREE location FAIL (when run from the worktree)
   and the deploy-parity WARN (research/docs commits ahead of `20260714_003205`).
2. VPS spot-checks: `crontab -l | grep -c wb_research` → **4** (nightly, shadow_book,
   trade_prints, pws_mesh); `ls ~/wb_research/pws_mesh_$(date -u +%Y%m%d).jsonl` growing
   during US daytime; `grep -c "GATE: PASS" ~/wb_research/nowcast_peak_133d.out` → 1.
3. Re-derive any 3 rows of §0 from their named sources (outputs are stored files; SQL
   read-only). Disagreement beyond rounding → STOP and report before new work.
