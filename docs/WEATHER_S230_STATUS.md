# WEATHER S230 SESSION HANDOFF (2026-07-14 → 07-15, local Windows session, wb-whiteboard worktree)

> Session type: WB-scoped, research-heavy. **ZERO bot-code changes, ZERO deploys** —
> live release is still `20260714_003205` (restart 2026-07-14 00:32:41Z), rollback
> `20260713_160143`, exactly as S229 left it. Everything below is research scripts,
> VPS research crons, and docs. Nothing to roll back.
> Verification of THIS handoff: `bash scripts/wb_resume_check.sh` (manifest updated
> this session — S230 commits/docs/scripts are checked mechanically) + §7 below.

## 0. QUICK FACTS (cross-check these when verifying the handoff)

| Fact | Value | Source |
|---|---|---|
| S222 clean-window count at session end | 133 resolved / 356 predicted (grew 19→50→133 during session) | gate SQL, cutoff `2026-07-13 16:02:29` |
| S222 verdict @ n=133 | **A1/A3 FAIL** (PIT KS 0.1924 p=0.0001), dampeners FAIL/insufficient, caps INCONCLUSIVE, C0 FAIL — **retire NOTHING** | calibration_check --dedup-markets |
| Cheap-NO tail | [0.0-0.1) predicted 0.04 → actual **0.269 (n=67)**; [0.1-0.2) 0.14 → 0.359 (n=39) | same |
| Duel @ n=133 | market Brier **0.1765** vs bot **0.2542**; head-to-head 68% bot-closer (magnitude loses) | one-off pl-duel (bot Brier cross-validates calibration_check exactly) |
| Root cause (CONFIRMED) | resolution = HOURLY-PRINT world (winner bucket contains H **81%** vs continuous C **35%**, n=48); forecast layer **+0.86°F hot** vs print world (Fm−H, n=47); WU truth = print world (WU−H −0.18, n=72); C−H = +0.95 (n=190) | `rep_bias_test.py` 18d × 12 US stations |
| EMOS self-heal | post-07-01 US pairs mean bias **−0.62** (n=146) — training data carries the corrective shift | SQL on weather_calibration |
| Surviving edge cell | day-of 9-12h-to-resolution bet-the-disagreement **+0.118 (SE 0.055, 2.1σ), WR 79%, n=66** mid prices (was +0.142/n=27 — survived 2.4× accrual) | day-of-hour EV re-cut |
| Nowcast Phase 0 | **CLOSED, GATE NOT MET** — 90d peak-model TEST n=57 **+0.074 (1.2σ)** vs rejects −0.002; offline history exhausted; **NO Phase-1 infra spend** | `nowcast_peak_model.py` 12d/28d/90d, pre-registered rule |
| Info-lead (real, insufficient alone) | 1-min curve leads public print by **median 58 min** (1,966 events, 230 station-days); naive crossing-entry EV-zero (+0.008±0.041); lead worth ~+5¢ vs reacting | `nowcast_skill.py`, `nowcast_entry_ev.py` |
| Market blindness (real) | winner price FLAT at real-time crossing (0.46→0.47), jumps 0.47→**0.68** at the print, 0.85 by +90min (33 winners) | `nowcast_price_path.py` |
| Latency audit | AsosOneMinClient has 3 request bugs (date-sep, `what=dl`, 4-char ICAO) AND IEM 1-min lags ~42h → hourly METAR is the true cadence for EVERYONE; chain quantified in WEATHER_STATUS 3a-pre | live probes 07-15 |
| Deep-backtest feasibility | CLOB minute-candles retained indefinitely (Feb-2025 market: 1,440 candles); Open-Meteo historical-forecast API works (bot's vendor, ~2022+); DB dense from 2026-03 | probes 07-15, §5 |

## 1. WHAT CHANGED (all commits on `claude/new-whiteboard-session-9b23tq`, all pushed)

Chronological; every commit is docs or `scripts/wb_research/` — no bot code:
- `56e6369` shadow-book first pass note (taker h15 ~+2¢, h17+ dead) — later refined
- `b480c5d` **executable_replay.py** (leader at LOGGED ASK vs resolution)
- `475d342` **trade_prints.py** + VPS cron 5-55/10 (maker-fill evidence; data-api prints, ts-cursor)
- `49d64b8` docs: tooling + race-study staleness (H17 +0.085→+0.035 as n grew)
- `bee2325` S222 verdict @ n=50 (PARTIAL — later superseded at n=133)
- `6b7bce8` lead/EV sweep (no stored forecasts >60h → 3-5d untestable; 24/48h EV = noise)
- `b524805` latency audit (3a-pre block in WEATHER_STATUS)
- `a4517af` **WB_NOWCAST_CAPTURE_SPEC.md** created (phased, gated)
- `b3363d6` **nowcast_skill.py + nowcast_price_path.py** + Phase-0a/0b' results
- `82f2c3b` **nowcast_entry_ev.py** (loser-leg: naive EV-zero; peak-model = required)
- `2151995` **nowcast_peak_model.py** (E_rem × hour, pre-registered rule, date-split)
- `f794268` S222 re-run @ n=133 (A1/A3 REVERSED to FAIL; 9-12h cell survived)
- `9c5bbfb` **rep_bias_test.py** + ROOT CAUSE CONFIRMED (sign inverted from first guess)
- `51b3e3d` stale continuous-max claims corrected in spec + README
- `1878a5c` Phase-0 CLOSED (90d gate not met; no infra spend)
- `217b897` deep-backtest program queued as next session priority 1 (operator-approved)

## 2. VPS STATE (changes this session — all research-layer, reversible)

- **New crons (ubuntu crontab):** `5-55/10 * * * * ~/wb_research/trade_prints.sh`
  (added S230) alongside the S229b `*/10 shadow_book.sh` + nightly 09:17. Rollback =
  remove the crontab line. Both verified firing unattended.
- **New files in `~/wb_research/`:** trade_prints.{py,sh}, trade_prints_YYYYMMDD.jsonl
  (~6MB/day), `.trade_prints_state.json`, executable_replay.py, nowcast_skill.py,
  nowcast_price_path.py, nowcast_entry_ev.py, nowcast_peak_model.py, rep_bias_test.py,
  outputs: nowcast_skill_21d.out, nowcast_peak_28d.out, nowcast_peak_90d.out.
- **Service untouched.** Health at last check: per-station EMOS loads 4/24h,
  reload/fit failures 0, avg_clim_mean 0, leak rows 0, CancelledError 0.
- Shadow books now have FULL local-hour coverage 10-19 (the 07-14 "h16-17 gap" was
  clock timing, verified filled).

## 3. THE VERDICT CHAIN (why each conclusion is what it is)

1. **n=50 PIT "PASS" was a power illusion** — same pipeline at n=133 rejects at
   p=0.0001. Lesson recorded: don't grade PIT below n≈100.
2. **Cheap-NO tail is THE defect** and `rep_bias_test.py` explains it end-to-end:
   the ensemble tracks the continuous max; settlement + WU truth live in the
   hourly-print world ~0.9°F lower; buckets below forecast therefore win more than
   modeled. The EMOS layer's post-cutoff pairs already average −0.62 → partially
   self-healing. **Remaining code task: verify the per-station correction actually
   reaches the bucket-tail computation** (if it only shifts the mean fit but the
   tail probabilities are computed pre-correction, the heal never lands).
3. **Hidden peaks are NOT tradeable** (they don't settle markets) — several S230
   docs initially claimed otherwise; corrected in `51b3e3d`. The 1-min lead matters
   ONLY as "know the next print early".
4. **Nowcast program: real-but-small.** Every window cut has picks positive
   (+0.074..+0.105 test) and rejects ~0, but significance stalls ~1.2-1.4σ and the
   estimate shrank with window growth. Pre-registered gate not met → parked on
   zero-cost accrual; the deep-backtest (§5) is the one move that can settle it now.
5. **9-12h cell** is the only candidate that got STRONGER under accrual. Caveats:
   family correlation (effective n < 66), mid prices, and it plausibly IS the same
   phenomenon as the crossing/peak window seen from the bot's side.

## 4. CORRECTIONS TO PRIOR-SESSION CLAIMS (do not re-cite the stale versions)

- Race-study H17 mid-edge: +0.085 (n=18) → **+0.035 (n=22)** — always read the
  latest `~/wb_research/nightly_*.log`, never the S229 snapshot.
- "resolution uses the continuous max" (early S230 docs): **WRONG** — print world.
- S222 @ n=50 "A1/A3 PASS-leaning": **SUPERSEDED** — FAIL at n=133.
- "AsosOneMinClient gives 59-min faster detection": **it has never worked live**
  (3 request bugs + 42h upstream lag). Decision open: fix-for-research or remove.

## 5. NEXT SESSION = DEEP-BACKTEST PROGRAM (priority 1)

Full plan in `docs/WB_NOWCAST_CAPTURE_SPEC.md` §"NEXT SESSION PLAN" (operator-approved
07-15): (1) archived Open-Meteo forecasts → peak-model at ~2× n_test (decisive);
(2) historical trade prints → maker-fill study over 03→07 NOW; (3) 9-12h cell at
scale, bot-independent, family-clustered SEs; (4) Gamma probe for 2025 listings;
(5) fold verdicts back into spec + WEATHER_STATUS → Phase-1 build/kill.
Hard limit: order-book depth (asks) is NOT backfillable — forward-only via loggers.

After that, the standing queue (WEATHER_STATUS 2e) continues: EMOS-correction-path
verification (§3.2 above), bootstrap landmine one-commit fix (post-verdict),
calibrator hands-off until ~08-07, S222 next re-cut at n≥200-ish for bin power.

## 6. OPERATOR STANDING ITEMS (echo until confirmed done)

1. **ROTATE trading wallet `0xd6a5…627F`** (unchanged from S229; detail 2e row 8).
2. **VPS release pruning** — now unblocked by the S222 verdict; plan from S230
   chat: delete the 11 fat legacy releases (~42G), keep live+rollback+small ones;
   disk NOT under pressure (62%), so timing is convenience.
3. **DELETE the local `wb-s222-gate-check` daily task** — verification ran (twice).
4. **NWWS-OI application** (free NWS push feed) — worth doing regardless of the
   parked nowcast program (helps the existing bot's react leg).
5. WU API rate-limit question — ONLY IF the deep-backtest revives Phase 1.

## 7. HOW TO VERIFY THIS HANDOFF (for the next session; also in the S231 kickoff)

1. `bash scripts/wb_resume_check.sh` — the manifest now pins all S230 commits,
   docs, and research scripts. Expected: ALL PASS except (a) the agent-WORKTREE
   location FAIL if run from `.claude/worktrees/wb-whiteboard` while the main tree
   is EB-held (known artifact — the substantive checks still run), and (b) a
   deploy-parity WARN (docs/research commits ahead of `20260714_003205` — correct,
   nothing deployable changed).
2. VPS spot-checks (3 lines):
   `crontab -l | grep -c wb_research` → 3 (nightly, shadow_book, trade_prints);
   `ls ~/wb_research/trade_prints_$(date -u +%Y%m%d).jsonl` → exists and growing;
   `grep GATE ~/wb_research/nowcast_peak_90d.out` → "FAIL/INSUFFICIENT" line present.
3. Cross-check any 3 rows of §0 QUICK FACTS against the named source (rerun the
   SQL / script — all read-only). If ANY number disagrees beyond rounding → STOP,
   report the discrepancy to the operator before doing new work.
