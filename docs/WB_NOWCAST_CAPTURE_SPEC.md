# WB Nowcast-and-Capture Spec (S230, 2026-07-15) — compete where the market is SLOW

**Thesis (evidence-backed, not yet proven):** the market beats us at forecasting
(day-ahead duel: market Brier 0.194 vs ensemble 0.223; clean-window duel: market
0.143 vs bot 0.234) but is SLOW at the end: winners jump **0.54 → 0.72 in the
~15 min after the deciding METAR ob publishes** (race study, n=49 reaction
curves). If the crowd were anticipating the print, there would be no jump — that
18¢ step is the measured size of information nobody front-runs. The official
print is a once-per-hour snapshot (~:51-:55 ob, API-visible minutes after :00)
of a smooth physical curve. Real-time neighbor stations (WU PWS, ~2.5-min
cadence, dozens near most resolution airports) can read that curve 30–50 min
before the snapshot publishes. Predicting 40 min of temperature drift with
dense local data is a far easier problem than the day-ahead one we lost.

**Supporting context (S230 latency audit, WEATHER_STATUS 3a-pre):** there is NO
public real-time sub-hourly feed for the official station (IEM 1-min lags ~42h;
the bot's AsosOneMinClient is broken anyway — 3 request bugs, silent). Everyone
reacts to the same hourly METAR. Reacting faster is worth ~2¢; predicting the
print is worth (up to) the 18¢ step.

**Capacity honesty:** books hold tens of dollars at the touch. Even fully
working, this is a small-dollar strategy; it must stay cheap to run.

---

## PHASE 0 RESULTS (RAN 2026-07-15, S230 — same night as the spec)

**0a: PASS, decisively (no model even needed for detection).**
`nowcast_skill.py`, 21d × 12 US stations, 230 station-days, 1,966
bucket-boundary events: the station's own 1-min curve leads the public print
by **median 58 min** (85% of events ≥30 min). **14% of events never print
intraday at all**, and on **78% of days the true daily max never appears in
any intraday instantaneous print** (⚠ CORRECTED by rep_bias_test, addendum
below: resolution tracks the PRINT world, so hidden peaks are NOT a tradeable
advantage — the lead matters only as "know the next print early"). Detection
of a crossing is
pure observation; the MODEL part (will it keep rising / abstain on
non-smooth regimes) is only needed for the overshoot leg.

**0b': PASS — the hole is open and quantified.**
`nowcast_price_path.py`, 33 winner events: market price is FLAT when the
1-min curve enters the winning bucket (0.46→0.47→0.47 through +15 min) and
jumps **0.47→0.68 exactly at the print**, drifting to 0.85 by +90 min.
Nobody currently trades the real-time crossing. Caveat: winners-only
alignment — full strategy EV must include loser legs (bucket entered, later
overshot); that replay + capture-at-ask (0b) + maker fills (0c) are the
remaining Phase-0 items, pending shadow-book/trade-print accrual.

**Loser-leg replay: RAN same night (`nowcast_entry_ev.py`, 58 family-days,
121 crossing entries incl. losers) — NAIVE STRATEGY IS EV-ZERO; a peak-model
is REQUIRED.** Buy-every-crossing at t_cross: meanEV **+0.008 ± 0.041** (zero);
same entries at t_reveal: −0.043 ± 0.047. So the 58-min observation lead is
worth ~**+5¢/share vs reacting to the print**, but the base strategy carries
no edge: only 33% of crossings are final (median entry 0.32 — the market's
forecast-based pricing already anticipates crossings without watching
real-time obs; the 21¢ reveal jump is 'will it STOP here' uncertainty
resolving, winners-only conditioning made it look free). Hour pattern
confirms: midday crossings (h12, mostly overshot later) −0.089; h13-14
crossings +0.024..+0.043. CONCLUSION: the tradeable version = observation
lead + a PEAK-PROXIMITY MODEL ("is this crossing final?") — time-of-day +
forecast max + trajectory. The race study's fixed-hour entries were a crude
version of exactly this.

**Remaining before Phase 1 go (revised):** (1) **0a-ii peak-model backtest —
now THE critical gate** (offline, same IEM data: predict P(crossing is final);
bar: model-filtered crossing entries meanEV ≥ +0.05 at mid with CI excluding
0); (2) capture size at the logged ask (0b); (3) maker-fill evidence (0c).
The live data source must be a PWS mesh (IEM 1-min lags ~42h — backtest-only).

**0a-ii RESULTS SO FAR (`nowcast_peak_model.py`, rule: E_rem<=1.0F AND h>=12,
date-split):** 12d run — test n=31 meanEV +0.074 (SE 0.081), rejects −0.029;
28d run — TRAIN n=12 +0.141, **TEST n=35 +0.105 (SE 0.076, 1.4σ)**, rejects
n=112 −0.031/28% win. Direction ROBUST across windows (picks positive both
halves both runs; ~13.6¢ pick-vs-reject separation; sharpest a-priori cell =
E_rem≤0.5 × h12-13: +0.163 n=21) but the 2σ gate is NOT yet met — verdict
INSUFFICIENT, not fail. A 90-day run (forecasts exist to 2026-03-08) was
launched 07-15 ~19:1xZ → `~/wb_research/nowcast_peak_90d.out`; expect
~3x entries → decisive either way. COLLECTED SAME SESSION — see below.

**90d FINAL (⚠ SUPERSEDED by the S231 deep-backtest below — GATE now PASSED
at n_test=135 with archived forecasts; do not re-cite "no Phase-1 infra
spend"): GATE NOT MET (as of S230).**
406 family-days, 277 priced entries. TEST half (05-28..07-12): n=57 meanEV
**+0.074 (SE 0.060, 1.2σ)**, rejects n=193 −0.002. Picks positive in every
window cut (+0.074..+0.105 test) but significance stalls ~1.2-1.4σ and the
effect shrinks as the window grows; cell structure shifted (E_rem≤0.5 h<12
+0.117 n=20, h12-13 +0.121 n=38, but h≥14 flipped to −0.021 n=34 — the
"peak hours" half of the prior weakened; do NOT re-tune post-hoc).
**DECISION per the pre-registered framework: no Phase 1 infra spend.**
The edge is plausibly real but small (+7¢ mid ≈ marginal after costs) and
unprovable with existing history. PATH FORWARD (zero-cost): loggers keep
accruing; entries accrue ~3/day → re-evaluate at n_test ≥ 150 (~4-6 weeks);
meanwhile 0b/0c (capture-at-ask, maker fills) get answered from live shadow
data and decide whether even a proven +7¢ is capturable. NWWS-OI remains
worth applying for regardless (free, benefits the existing bot's react leg).
NOTE: the EMOS training pairs already carry the rep-bias shift (post-cutoff
US pairs mean bias −0.62, n=146) — the calibration root-fix is partially
self-healing; verify the correction reaches the tail computation (code work).

**S230 late addendum — rep_bias_test.py RESULT bears directly on this program:**
resolution tracks the HOURLY-PRINT world (81% vs 35% continuous, n=48) — the
hidden-peak advantage is VOID for trading; the 1-min lead is valid only as
"know the next print early", and never-printing crossings (14%) are a risk
factor for crossing entries. See WEATHER_STATUS OD-2 re-run block for the
full layer-diff table (forecast +0.86F hot vs settlement world = the bot's
cheap-NO-tail root cause).

## S231 DEEP-BACKTEST RESULTS (RAN 2026-07-15/16, S231) — **GATE PASSED**

**Task 1 — peak-model at full power: GATE PASS (first time).**
`nowcast_peak_model.py` extended with archived Open-Meteo forecasts via the
PREVIOUS-RUNS API (`temperature_2m_previous_day1` = issued day D−1, structurally
before any day-D crossing — no lookahead; the historical-forecast API is a
shortest-lead mosaic = lookahead and was NOT used; the historical-ensemble API
only reaches 2026-04-13). Family window keyed on the question date (NULL
`end_date_iso` no longer drops families) → **719 family-days 03-01..07-12**
(was 406), 1,072 priced entries (796 DB-forecast, 276 archive-fill, 390 March).
Rule FROZEN (E_rem≤1.0F AND h≥12), split 05-06. Output: `nowcast_peak_133d.out`.
- **PRIMARY TEST: n=135 meanEV +0.091 (SE ~0.039) → +0.091−2SE = +0.013 > 0
  AND ≥ +0.05 → PASS** per the pre-registered bar. Family-clustered
  cross-check: 100 family-days, day-mean +0.107, clustered SE ~0.040 (~2.7σ).
- TRAIN +0.084 (n=219) — no train/test divergence. Rejected-by-rule −0.007
  (n=718) — ~10¢ pick-vs-reject separation. Strongest a-priori cell
  E_rem≤0.5 × h12-13: +0.125 (n=142). The 90d h≥14 flip REVERSED (+0.054
  n=175 — it was noise, as suspected; rule stays frozen).
- Robustness: DB-only TEST +0.083 (n=58 — consistent, underpowered alone);
  **ARCH-only TEST +0.059 (n=148, ~1.6σ)** — positive but does NOT clear 2σ
  by itself (day-1-lead forecast is staler; expected). DB-vs-arch forecast-max
  offset +0.64F mean (DB hotter — the known hot forecast layer).
- CAVEATS: mid prices (not executable — see task 2); May is a real DB coverage
  hole (82 resolved YES buckets vs 439-632 adjacent months) thinning early
  TEST; family correlation handled by the clustered SE (still >2σ).

**Task 2 — historical maker-fill study (0c from history): capture is NOT the
blocker.** `maker_fill_study.py`, 304 winner-bucket reveal windows 03→07
(months 3/4/5/6/7 = 93/95/9/74/33), median pre-reveal price 0.68, median
repricing +8¢. Resting-bid fills (ALL UPPER BOUNDS — queue position unknowable,
winner-conditioned, wash flow included): any-fill 97/95/93/86% at bid =
p0−0/1/2/5¢ (median ~150-200 shares when filled); **POST-reveal-only fills
74/71/65/54%** — sellers still hit stale levels after the print lands. BUT the
same-length control window 3.5h earlier shows 80-87% any-fill: these books
churn two-sided all day, so bids also fill when you're WRONG — adverse
selection is the real cost, and that is exactly what the (now-passed)
peak-model gate prices. Capacity confirmed small (~$100/window upper bound).

**Task 4 — Gamma probe: CLOSED, no pre-2026 history.** Temp dailies began
**2025-12-28** (19 events / 133 markets, Dec 28-31 2025 only; matches the DB's
~13-market shadow). No 2025-summer out-of-regime validation set exists.

**Task 3 — 9-12h cell at scale: DOES NOT HOLD bot-independently.**
`dayof_cell_scale.py`, 433 family-days with bets (03→07), raw ensemble
members floored at hourly-METAR runmax vs CLOB minute prices,
FAMILY-CLUSTERED SEs: 9-12h bet-the-disagreement **+0.002 (cSE 0.019, ~0σ,
n=692 bets / 368 family-days)**; every hour bucket ≈ 0 (6-9h −0.023; 3-6h
+0.079 on n=21 noise); threshold sensitivity 0.05/0.10/0.15 all flat ≈ 0.
The S230 +0.118 (n=66, bot prediction_log rows) does NOT replicate with a
public signal → it was bot-conditional information or under-clustered noise;
do NOT build on the 9-12h cell. CONSISTENCY READ: the market prices raw
public signals efficiently (this zero + the S229 day-ahead duel), while the
peak-model's crossing-finality selection (task 1 PASS) draws on real-time
obs composition at specific moments — its reject-set EV ≈ 0 matches.

**PHASE-1 DECISION (operator, 2026-07-15): BUILD — executed same session.**
Recommendation rationale: gate PASSED on pre-registered terms + capture-side
plausible (task 2) + costs stay research-tier. Built: `pws_mesh.py` collector
(read-only VPS cron `2-57/5`, alongside shadow_book/trade_prints): per
active-market US city in local 09-21, polls ≤4 nearby WU PWS, per-PWS epoch
cursors, daily roster re-resolution, dead-station rotation, logs
`pws_mesh_YYYYMMDD.jsonl`. DEPENDENCY CAVEAT: uses the public web API key the
wunderground.com site embeds (no operator WU key exists — the bot's "WU
integration" is a history-page scrape, not an API); durable path = operator
WU key or Synoptic token (swap via `WU_WEBKEY` env). NWWS-OI application
remains an operator action. Phase 2 (bot integration, flag-gated OFF,
maker-first per task 2) = design next session; capacity honesty stands
(~$100/window upper bound). Next validation: mesh-vs-IEM-1min lead
reproduction once IEM catches up (~42h).

## NEXT SESSION PLAN — DEEP-BACKTEST PROGRAM (queued 2026-07-15, operator-approved)

Converts three "wait for accrual" verdicts into answers from data that already
exists. Feasibility PROBED 07-15 (all verified live from the VPS):
CLOB minute-candles retained indefinitely (Feb-2025 market returned all 1,440
final-day candles); Open-Meteo historical-forecast API serves archived runs
back to ~2022 in °F (the bot's exact vendor; previous-runs variant needs its
`..._previous_dayN` param shape, not `forecast_days`); IEM archives years-deep;
data-api trade prints paginate historically. Our DB is dense from 2026-03
(~11k resolved highest-temp buckets), near-empty before.

Session tasks, in order:
1. **Peak-model at full power:** wire archived Open-Meteo forecasts (historical
   + previous-runs APIs; also the historical ENSEMBLE api for members) into
   `nowcast_peak_model.py` to (a) fill the missing-forecast holes that cost the
   90d run ~half its entries, (b) add March. Expect n_test ~120+ → the +0.074
   estimate lands ~1.8-2σ → gate clears or dies honestly. Keep the
   pre-registered rule FROZEN (E_rem<=1.0 AND h>=12); report cell table for
   information only — no post-hoc re-tuning.
2. **Historical maker-fill study (0c now, not in weeks):** paginate data-api
   prints for resolved families (03→07); for each reveal window, measure
   fill-probability of hypothetical resting bids at L1/L2/L3 of the pre-reveal
   book proxy (prints ARE fills; side=TAKER side). Answers whether even a
   proven +7¢ is capturable maker-side. Caveat to state in results: queue
   position unknowable; treat computed fill rates as UPPER bounds.
3. **9-12h cell at scale:** re-cut the day-of hours-to-resolution EV table over
   ALL 03→07 resolved families using CLOB minute prices at matched timestamps
   (not just clean-window prediction_log rows) — bot-independent version of the
   one surviving cell; family-clustered SEs.
4. **Gamma probe for pre-2026 listings** (one curl session): if 2025-summer
   temp dailies exist, backfill markets+prices → out-of-regime validation set
   (the jackpot; likely absent — DB shows ~13 markets Oct-Dec 2025).
5. Fold verdicts back into THIS spec + WEATHER_STATUS OD-2; the Phase-1
   build/kill decision follows directly.
HARD LIMIT (unchanged): order-book depth cannot be backfilled — executable-ask
questions stay forward-only via the shadow logger (keep both crons running).

## Phase 0 — validate BOTH halves offline (no new infra, no bot changes)

Gate everything on two backtests, both feasible with existing data:

**0a. Nowcast skill:** can neighbor stations + last METAR + solar curve predict
the NEXT hourly print? Backtest on history: IEM 1-min/5-min archives (lagged is
fine for backtest) or WU PWS history vs official METAR prints. Target metric:
precision of "next print crosses bucket boundary X" claims. Bar: ≥90% precision
when it fires, ≥30 min average lead, across ≥100 station-days incl. at least
one frontal-passage / non-smooth regime. (Smooth diurnal days are easy; the
model must KNOW when it doesn't know — abstain on convection/frontal noise.)

**0b. Capture size:** given a correct 30-min-early call, what was actually
buyable? Replay shadow_books_*.jsonl (accruing since 07-14): book state at
T-30min before each deciding ob vs after. `executable_replay.py` pattern; the
race-study reaction curve (0.54→0.72) bounds the theoretical max. Bar: net
capture after half-spread ≥ +5¢/share taker, or maker-fill evidence (0c).

**0c. Maker fills:** from trade_prints_*.jsonl (accruing since 07-14): do
resting bids at pre-print levels actually get lifted in the repricing window?
side=TAKER side, so SELL prints at our hypothetical bid levels = fills we'd
have gotten. Bar: fill probability high enough that maker EV ≥ taker EV.

If 0a fails → the whole thesis dies; write it down and stop. If 0a passes but
0b+0c fail → information exists but isn't capturable; stop. No phase 1 until
0a AND (0b or 0c) pass.

## Phase 1 — data path (read-only research crons, same pattern as shadow_book)

- **PWS-mesh collector:** per US resolution city, poll WU PWS (bot already has
  WU integration — S224 WS-2 ground-truth path) or Synoptic/IEM mesonet APIs
  every 2–5 min during the local resolution window; log
  `pws_mesh_YYYYMMDD.jsonl` next to the shadow books. OPERATOR CHECK FIRST: WU
  API tier/rate limits for ~11 cities × 2.5-min cadence.
- **NWWS-OI application (operator action, free):** NWS push feed delivers the
  METAR in seconds vs minutes — cheap insurance for the react leg and tightens
  every backtest timestamp. Apply early; approval takes time.
- Keep the existing 10-min shadow-book + trade-print crons running — they are
  the capture-side ground truth.

## PHASE-2 DESIGN (S231, written on operator "do next session items now") —
## DESIGN ONLY; implementation requires a separate operator go

**Signal (all pieces already validated or logging):**
1. Mesh consensus curve: median of ≥2 qc==1 PWS obs per 5-min bin, each PWS
   DEBIASED by its trailing-N-day offset vs the airport METAR PRINT series
   (mesh_validation.py --bias machinery; raw offsets run −2.5..+3.4F per city,
   first pass 07-16 — debiasing is mandatory, not optional).
2. Crossing: debiased consensus running-max enters bucket B (±0.5F convention).
3. Filter: the FROZEN peak rule — E_rem ≤ 1.0F AND local hour ≥ 12, with
   E_rem from the bot's own latest ensemble forecast (day-of DB forecasts are
   the sharper feature: DB-only +0.083 vs ARCH-only +0.059, task 1).
4. Enter only while price < nowcast-fair minus costs (existing edge math).

**Execution (maker-first, per task 2):** rest a bid on B at (last print price
− 1..2¢) — post-reveal fill probability 65-71% UPPER bound at those levels;
cancel-on-invalidate when the mesh runmax exceeds B's hi bound (overshoot) or
the local day ends. NO taker chasing at h≥17 (dead per executable_replay).

**Bot integration surface (small by design):**
- `WEATHER_NOWCAST_ENTRY_ENABLED` (default **false**) — Tier-2 flag.
- Separate `model_name='weather_nowcast_peak'` in prediction_log → graders,
  calibration_check, and the confidence calibrator treat it independently.
- ALL existing risk plumbing UNCHANGED (BotBankrollManager sizing,
  risk_manager limits, caps, dampeners, group/city exposure, existing
  one-bet-per-market guards). This is a new signal, not a new risk model.
- Sizing honesty: `WEATHER_NOWCAST_MAX_PER_WINDOW_USD` (default 50) — task-2
  capacity is ~$100/window UPPER bound; never exceed it.
- React leg: S228 latency package env flips (OD-3a) activate WITH the flag —
  Tier-2 operator decision at the same moment.

**Acceptance gates BEFORE the flag ever goes true (paper):**
- mesh_validation --lead (runnable ~07-18): mesh leads the print on ≥50% of
  gradeable events with median lead ≥15 min AND false-crossing rate <20%.
- ≥7 days pws_mesh uptime with per-PWS debias table populated per city.
- Operator WU key / Synoptic token in place (web-key dependency retired).
**Judgment (Phase 3 bars unchanged):** ≥50-100 independent market-days per
cell; calibration + hit-rate only (#11); kill stays on the table.

**Explicit non-goals (S231 evidence):** no 9-12h disagreement betting (dead
bot-independently, task 3); no hidden-peak trading (print world settles); no
taker capture at the high-EV end (no ask / 0.999).

**Second flag-gated signal (S231-late addition, same infra): PEAK-PASSED.**
The mirror of the crossing trade: when the DEBIASED mesh consensus has fallen
for ≥K consecutive bins (K default 6 = ~30 min) across ≥2 stations after
local 14:00, the running-max leader and every surviving at_or_below leg
become drift candidates (the 0.68→0.85→1.00 leg — the makeable part). Same
flag, same model_name family (`weather_nowcast_peakpass`), same caps; it
also converts the 14%-never-print days from a risk into a signal. Gated on
the same acceptance gates + the Study-B (post-lock) evidence below.

## S231-LATE PRE-REGISTERED STUDIES (written BEFORE running; read-only)

**STUDY A — does the MARKET carry the print-world (cheap-NO) bias?**
Motivation: rep_bias_test proved settlement = hourly-print world, ~0.9F below
the continuous-max world the public forecasts describe; our bot's cheap-NO
tail came from exactly this. If the crowd anchors on the same forecasts, low
buckets should be underpriced MARKET-WIDE.
Rule (frozen before run): resolved US-F families 03-01..07-12
(question-date keyed). fc_max = archived previous-runs day-1 forecast max
(uniform, no lookahead). Select buckets with hi_bound ≤ fc_max − 1.0F
(range and at_or_below). Sample CLOB minute price at T = local-midnight-EOD
minus {24h, 14h}; require 0.01 < p < 0.60. EV/$1 = outcome − price
(buy-the-bucket). Family-day-clustered SEs. Report by lead and by
distance-below-forecast bins (1-3F, 3-5F, >5F) — bins are informational,
the GATE is pooled per lead.
GATE: meanEV ≥ +0.05 with 2×clustered-SE excluding 0 at either lead →
trade-candidate; ≥2σ but < +0.05 → BIAS-CONFIRMED-NOT-TRADEABLE; else DEAD.
**RESULT (ran 07-17, `study_a_bias.out`): DEAD.** 24h lead n=696 meanEV
−0.007 (cSE 0.013); 14h n=552 −0.013 (cSE 0.015); 420 family-days; no
distance bin shows bias (>5F +0.020 = noise). The market does NOT carry the
bot's print-world defect — its low-bucket pricing is already correct.
Consistent with the market winning every day-ahead duel. The peak-model
selection remains the only validated edge.
**GLOBAL RE-RUN (ran 07-17, `study_a_global.out`, per the pre-registered
robustness block): DEAD pooled at both leads** (24h n=795 +0.000 cSE 0.012;
14h n=634 −0.004) — the global verdict matches the US one. NOTED FOR
ACCRUAL, NOT A VERDICT: the **non-US cut is mildly positive at ~1.2-1.4σ**
(24h +0.062 cSE 0.043 n=70; 14h +0.052 n=59) — consistent with the
thinner-attention-overseas thesis but far below the bar; also the >5F-below
bin is +0.03-0.04 (~1.3σ) both leads. No trading, no re-tuning; re-cut as
non-US family-days accrue.

**STUDY B — post-lock drift capture (execution-only edge).**
Motivation: winners drift 0.85→1.00 for hours after the outcome is
physically locked; task 2 excluded 151 already-decided windows — the
unexamined leg. If sellers still print below fair AFTER lock, that is
latency/attention money with zero forecast risk (only settlement/void risk).
Rule (frozen before run): resolved US-F families 03-01..07-12, hourly-METAR
print series. Two prospective lock rules evaluated on EVERY bucket (not just
winners — false-lock rate is part of the verdict):
  L1: after the day's LAST print (local ≥23:30) with runmax inside bucket.
  L2 (earlier): local hour ≥ 21 AND runmax inside bucket AND latest print
      ≤ runmax − 2.0F.
Measure per fired lock: YES-frame taker-SELL prints (data-api full history,
maker_fill_study conventions) at price ≤ {0.97, 0.95, 0.90} in
[t_lock, t_lock+12h]; shares and EV/share = 1 − price. UPPER BOUNDS caveat
carries (queue/wash unknowable).
GATE: false-lock rate (fired-but-resolved-NO / fired) < 1% for the rule to
be usable; then viability = ≥50% of locked family-days show ≥20 shares
buyable at ≤0.97. Report both rules; do NOT tune thresholds post-hoc.

## GLOBAL MANDATE (operator hard directive 2026-07-16 — NOT NEGOTIABLE)

Every WB collector, study, and backtest covers ALL listed cities (any country,
any temp unit) from now on. US-only filters are forbidden. Unit hygiene:
bucket bounds match in NATIVE units (±0.5 native); IEM serves tmpf (°F) for
all ICAOs — convert before matching; frozen F-thresholds apply as native
equivalents (E_rem ≤ 1.0F ≡ 0.556C). Prior US-only verdicts (the S231
peak-model gate) are NOT re-gated: global runs are separately-reported
ROBUSTNESS cuts. pws_mesh went global 2026-07-17 ~00:42Z (Mexico City obs
confirmed first tick). Probe fact: EGLC METARs are HALF-HOURLY (2x reveal
cadence) — the non-US microstructure differs, which is part of the point.
Memory: feedback_wb_always_global.md.

**STUDY B RESULT (ran under the frozen rules above): DEAD — and the
false-lock finding is the discovery.** L1 fired 550, false-locks 9.27%;
L2 fired 546, 9.52% — BOTH fail the <1% gate; print analysis correctly
skipped. Even the day's FINAL print-max bucket loses ~9% of the time:
independent cross-validation of rep_bias_test's 81%-print-world number —
settlement (WU history) diverges from METAR prints at bucket boundaries.
CONSEQUENCES: (a) late 0.95-0.97 sellers are rationally pricing
settlement-source risk, not being lazy — no free money at the close;
(b) ~9% boundary risk is a REAL haircut on any "certain" late position and
must appear in Phase-2 sizing; (c) NEW CANDIDATE (not run, needs its own
pre-registration): lock only when runmax ≥1.0F INSIDE the bucket bounds —
expected to collapse the false rate; quantify before any use.

## S231-LATE PRE-REGISTERED STUDIES C & D (GLOBAL; frozen before running)

**STUDY C — sibling-bucket repricing latency ("sell the dead lane").**
Rule: GLOBAL resolved families 03→07 (native-unit bucket matching; IEM
station id = ICAO minus leading K for US, full ICAO otherwise). At the
winner's reveal (first hourly print entering the winner bucket, +6 min), the
DEAD SIBLING = the bucket the running max occupied immediately before the
crossing (resolved-anything; graded vs resolution — Study B says adjacent
buckets carry ~9% boundary risk, so the win-rate haircut is part of the EV,
not a filter). q0 = last YES-frame print of the sibling before t_reveal−2min;
require q0 ≥ 0.10. Hypothetical resting ASK at q ∈ {q0, q0−0.01, q0−0.02};
fills = YES-frame taker-BUY prints ≥ q in [t_reveal, +45min] (UPPER bounds);
control window at t_reveal−3.5h. EV/share at fill = q − y (y=1 if the
sibling somehow won).
GATE: ≥30% of reveal windows show ≥20 shares filled at q0−0.02 post-reveal
AND meanEV ≥ +0.05 with 2×family-clustered SE excluding 0 → exploitable;
fills without EV → BIAS-CONFIRMED-NOT-TRADEABLE; else DEAD.
**RESULT (ran 07-17, `study_c_sibling.out`): SIGNAL-POSITIVE BUT n=10 —
BELOW DECISION BAR; keep accruing, do NOT build.** Of 1,624 family-days only
10 windows qualified (345 dead siblings had ALREADY collapsed below 0.10
pre-reveal; 969 no usable winner-crossing) — the market normally kills the
old lane BEFORE the print; the exploitable event is rare (~3-4 windows with
≥20sh fills in 4.5 months). In those 10: premium ~+0.34/share, 0/10
siblings won, gate line technically "EXPLOITABLE" — but 10 samples is far
under the program's own n≥50-100 bar. Treat the 4.9σ as UNRELIABLE at this
n. Forward trade_prints accrual grades it passively.

**STUDY D — forecast-REVISION momentum (the duel tested levels, not deltas).**
Rule: GLOBAL families 03→07. rev = archived previous_day1 fmax −
previous_day2 fmax (same target date, both no-lookahead, °F). Target bucket =
the bucket containing the FRESH forecast (previous_day1 max, converted to
native units). Sample CLOB price at T = local-midnight-EOD − 24h; require
0.03 < p < 0.90. CASES: |rev| ≥ 1.5F. CONTROL: |rev| < 0.5F. EV/$1 = y − p;
family-day-clustered SEs.
GATE: case meanEV ≥ +0.05 with 2×cSE excluding 0 AND case meanEV > control
meanEV → the market lags revisions (trade-candidate); significant-but-small
→ BIAS-CONFIRMED-NOT-TRADEABLE; else DEAD.
**RESULT (ran 07-17, `study_d_revision.out`): DEAD.** Case (|rev|≥1.5F)
n=270 meanEV +0.002 (cSE 0.026) vs control n=117 +0.009 — the market does
not lag day-scale forecast revisions. Non-US case +0.023 (n=31) = noise.

**GLOBAL ROBUSTNESS RUNS (not re-gates; separately reported):** peak-model
(frozen rule, native 0.556C threshold for C stations) and Study A re-run
over ALL cities; report global-pooled and non-US-only cuts side by side
with the US numbers. Regional cuts (Europe/Asia/other) informational.
**RESULT (ran 07-17, `peak_global.out`, window shifted to 02-28..07-14):**
US cut REPRODUCES the gate — TEST n=183 meanEV +0.084 (cSE 0.032), rejected
−0.013 (n=818); global pooled TEST +0.081 (n=201). **NON-US (print-time
detector): INCONCLUSIVE** — TRAIN −0.082 (n=33) / TEST +0.048 (n=18), mixed
signs at tiny n (only 90 priced non-US entries — thin CLOB minute history +
sparser DB forecasts there). No verdict; the now-global mesh + loggers
accrue the non-US sample forward.

**Candidate queue (listed, NOT pre-registered):** deep-inside lock rule
(Study-B follow-up, above); PSW radar-lead (precip begins = real-time
observable, prints hourly).

## S231 ADVERSARIAL REVIEW (8-angle blind sweep, 2026-07-17) — REQUIRED FIXES
## BEFORE ANY HARNESS RE-RUN (do not re-run the studies until these land)

Fixed same-night (commits e7a5e70..a73d585): Study A/B scripts committed
(were untracked while verdicts recorded); manifest repaired (+7 commits,
+5 scripts); mesh_validation reads date AND date+1 files + local-day filter
(western-evening obs were missing from the Phase-1 grade — bias sample went
19→181 prints on refetch); pws_mesh atomic state write + malformed-prune
guard + DB-failure sentinel + wu_fails counter + WU_WEBKEY wrapper wiring.

**RESOLVED 07-17 ~12:xxZ on operator instruction (commits 126fae8..e5af74b):
items 1-5 below are DONE** — year-guard SQL in all 7 harnesses; px_at
unified to ±300s; peak-model GATE now requires the family-clustered 2σ
(+ printworld se>0 guard); get_prints polarity unified to yes_sell;
wu_fails excludes 204s (live-tested). Item 4's GLOBAL Study A re-run
LAUNCHED (native units, US/non-US cuts) — result recorded below when it
lands. Item 6 stays as disclosed caveats. STILL OPEN from the review:
national-feed wiring (S232, needs unhurried per-station verification) and
the non-US --lead arbiter gap (national feeds are the fix).

**Original deferred list (all resolved except as noted):**
1. **Year guard**: families key on (city, mon, day) with year hardcoded 2026
   and no end_date filter. DB-verified: three 2020 'March 1' markets entered
   the S231 window as phantom families (price-gating dropped them from EV
   studies; ≤3 of Study B's 551 L1 locks affected — its DEAD verdict stands
   at 9%≫1%). Fix pattern: `AND (end_date_iso IS NULL OR end_date_iso >=
   '2026-01-01')` in each markets SQL + try/except around
   `datetime(2026,...)` (Feb-29 ValueError kills a whole run). Fix ALL 7
   sites in one pre-registered sweep commit.
2. **Gate SE convention**: the gate decides on the unclustered 0.45/√n SE;
   the family-clustered SE is print-only. The RECORDED S231 PASS is robust
   (clustered ~2.7σ also clears), but future pre-registrations must gate on
   the CLUSTERED SE explicitly.
3. **px_at drift**: gate uses ±300s match tolerance; all S231-late scripts
   use ±600s — the 'US cut REPRODUCES the gate' comparison is directionally
   valid but not like-for-like on price matching. Unify to ±300s in the
   sweep commit; note nearest-match can sample post-crossing prices (both
   directions; state it in any consumer).
4. **Global Study A re-run**: promised in the GLOBAL ROBUSTNESS block,
   NEVER ran (recorded Study A result is the US run). Either run it in S232
   or strike the promise.
5. **get_prints polarity**: sibling's copy returns yes_BUY where maker/
   postlock return yes_SELL in the same tuple slot — rename the field in
   the sweep commit before any snippet gets pasted across scripts.
6. Known-and-accepted skips (disclosed, verdicts robust): maker_fill drops
   single-step overshoot reveals (31 counted); postlock L1 skips days whose
   last print lands before 23:30 local — both are non-random-skip caveats
   to carry, not silent errors.

**SECOND-ORDER VERIFICATION (07-17 ~03:0xZ — were the fixes masking more?):**
- Study B's ~9% independently re-derived (9.89%, 36/364 fresh pass): ZERO
  double-fires, ZERO phantom contamination — the discovery is real; correct
  mechanism statement = settlement-source divergence ≥1F (WU vs METAR;
  integer prints saturate the edge-distance lens, so 'boundary' is shorthand).
- Fixed bias table (n=181) unmasked per-city heterogeneity: KSFO mesh
  +3.3F hot (marine microclimate), KIAH +2.0; LGA/LAX/SEA tight (±1F,
  sd ~0.8); ATL/AUS/MIA sd 2.2-2.7 — those cities may stay marginal for 1F
  buckets even after per-PWS debiasing. Phase-2 input: debias PER PWS and
  drop cities whose post-debias residual sd stays >~1.5F.
- **DAY-2 FULL GLOBAL BIAS READ (07-17 complete day, 653 prints / 37 cities,
  run 23:5xZ):** (a) **First correct-station reads** (post-deploy rows):
  KDAL −0.77 sd 0.94, KBKF −0.62 sd 1.25 (GOOD — and Denver has no 1-min
  product, so mesh matters most there), KHOU +2.04 sd 0.86 (stable warm,
  debias-able), LIMC +1.55, LTFM −1.19 but 1 PWS/bin (thin roster — needs
  candidates). RKSI/RCSS absent — Seoul/Taipei local windows ended before the
  14:54Z deploy; first reads next local day. Old-id rows (KDFW/KDEN/KIAH/
  RKSS/RCTP/LIML/LTBA) are pre-deploy morning obs and vanish from tomorrow's
  table. (b) **Day-over-day stability**: REPRODUCIBLE offsets at EGLC
  (+3.5→+3.0), EHAM, LEMD, WSSS, WMKK, RJTT, LTBA → scalar debias works
  there. DIURNAL structure confirmed at KSFO (+0.1 morning vs +2.8 full-day —
  sea-breeze differential) and suspected at CYYZ (−0.9 evening → +2.6
  full-day) → those need hour-of-day debias terms. MMMX drifted (+1.4→+3.2,
  sd 2.5) — watch. ZGSZ still garbage (−22, sd 26) — drop confirmed.
- The 00:35Z A/B re-run mystery: RESOLVED BENIGN — auth.log shows the
  operator machine's own restarted session re-executed interrupted
  launchers (six logins 00:20-00:31, same IP); frozen scripts, consistent
  outputs, no third party.
- Cron post-fix: 0 duplicate (pws,epoch) in 2,479 obs; wu_fails baseline
  ~5/tick (~2.5% transient). S232 refinement: split 204-dead-station counts
  from network-error counts so the alarm channel stays clean. (DONE 07-17
  12:xxZ — wu_fails now excludes 204s; ~5/tick = genuine transients.)

**FIRST GLOBAL BIAS READ (07-17 ~13:40Z, `mesh_validation --bias 20260717`,
371 prints / 32 cities — the day-2 mesh incl. every non-US city):**
- LEAD verdict still data-gated: IEM 1-min lag ~30h → 0 gradeable events for
  07-16 (verified by live run); full-day grading possible ~Sat 09:30Z — the
  scheduled task (Sat 10:00 ET) lands right; partial (US-morning) possible
  late Fri.
- Non-US mesh quality TIERS (raw, pre-debias): GOOD (workable with per-PWS
  debias): CYYZ −0.9, LFPB −0.2, LEMD −1.0, EDDM +0.9, RJTT −1.4, LLBG +0.5,
  VHHH −1.3. LARGE-BUT-STABLE offsets (debias-able): EGLC +3.5, EFHK +3.3,
  EHAM +3.0, RKSS +2.3, LTBA −3.1, NZWN +1.4, WSSS +1.7, MMMX +1.4.
  SUSPECT: EPWA +5.2 (2 PWS/bin — thin roster), WMKK −4.0 sd 4.0,
  RCTP −9.4 (tight sd — airport 40km from city PWS cluster?).
  **BROKEN: ZGSZ −23.6F sd 26 — Shenzhen roster is garbage (wrong-location
  or junk stations passing qc); drop-rule candidate #1.**
- NEW HYPOTHESIS from day-over-day: KSFO read +3.3F (afternoon-dominated
  07-16 sample) vs +0.1F (morning-only 07-17 sample) — per-PWS offsets may
  be DIURNAL (sea-breeze/heating cycles). Phase-2 debias likely needs an
  hour-of-day term, not a scalar. Verify as bias samples accrue.

## ⚠⚠ INPUT AUDIT (operator-ordered re-review, 07-17 ~14:3xZ) — THE BIG ONE:
## RESOLUTION-STATION MISMATCHES + RETRACTION OF THE ~9% "DISCOVERY"

Gamma market DESCRIPTIONS name the exact resolution station (standing memory
rule finally executed). Verified against the registry — **8 MISMATCHES**:

| City | Polymarket resolves at | Registry uses (WRONG) |
|---|---|---|
| Dallas | Love Field (KDAL) | KDFW |
| Denver | Buckley SFB (KBKF) | KDEN |
| Houston | Hobby (KHOU) | KIAH |
| Seoul | Incheon (RKSI) | RKSS Gimpo |
| Taipei | Songshan city (RCSS) | RCTP Taoyuan |
| Hong Kong | HK OBSERVATORY (urban; not an airport) | VHHH |
| Milan | Malpensa (LIMC) | LIML Linate |
| Istanbul | Istanbul Airport (LTFM) | LTBA Atatürk |

Plus ~10 active market cities ABSENT from the registry entirely (Busan/Gimhae,
Cape Town, Guangzhou, Jeddah, Jinan, Karachi/Masroor, Manila, Panama
City/Albrook — NOT Tocumen, Qingdao/Jiaodong, Zhengzhou) → mesh never collects
them; bot never models them.

**RETRACTION — Study B's "~9% settlement-source risk" was 100% miswiring
artifact.** Re-derived excluding Dallas/Denver/Houston: false-locks
**0/268 = 0.00%** (was 35/369; every false lock came from the 3 miswired US
cities — we watched DFW/DEN/IAH while settlement read DAL/BKF/HOU).
CONSEQUENCES: (a) settlement DOES follow the print-max at the CORRECT
station (rep_bias's 81% number is also contaminated by the same 3 cities —
recompute); (b) the Phase-2 "~9% boundary-risk haircut" is WRONG — replace
with the remapped number; (c) Study B's free-money-at-the-close question is
RE-OPENED — its gate now passes, remapped re-run launched
(`postlock_remap.out`, Dallas=KDAL Denver=KBKF Houston=KHOU); (d) peak-model
remapped robustness re-run launched (`peak_remap.out`; KDAL/KHOU have 1-min,
KBKF does not → Denver families self-exclude); prior US numbers carried
wrong-station noise in 3/11 cities — the gate PASSED DESPITE it (noise, not
lookahead), expect the corrected run to hold or improve, but VERIFY.
Mesh-bias reinterpretation: Taipei's −9.4F "suspect" mesh is likely the mesh
being RIGHT near Songshan while we compared vs Taoyuan; HK's debias target
must be HKO (whose open-data feed we already verified); Shenzhen's roster is
Hong Kong stations 26-31km cross-border (mainland has no PWS) — Chinese
cities: mesh unusable, METAR-only.

**REGISTRY FIX = LIVE BOT CODE (station_registry.py) — Tier-3, S232, full
protocol + operator sign-off; do NOT hotfix.** The bot has been
forecasting/grounding 8 cities against wrong stations (a real live defect —
likely a material chunk of "bot loses to market" in those cities). Fix plan:
correct 8 station rows + add ~10 missing cities + defect tests + release cut.
Studies interim: use the remap variants. HISTORY ASSUMPTION to verify in
S232: descriptions have named these same stations all season (spot-check a
March market's description).

## CORRECTED-STATION PEAK-MODEL RESULT (`peak_remap.out`, ran 07-17 ~17-18Z)

**GATE: PASS — 4th independent confirmation, under the HARDENED gate.**
Stations corrected (Dallas=KDAL, Houston=KHOU via 1-min; DENVER EXCLUDED —
Buckley has no 1-min product; disclosed): PRIMARY TEST n=190 meanEV **+0.070**
(SE ~0.033; 2SE cleared) AND family-clustered day-mean **+0.087, cSE ~0.035,
clustered-2σ OK** (139 family-days); TRAIN +0.084; rejected −0.008 (n=823).
Cuts: DB-only TEST +0.091 (n=58); ARCH-only +0.035 (n=191 — staler day-1
forecasts dilute, consistent pattern). The pass chain is now: original gate
(+0.091) → shifted-window global US cut (+0.084) → corrected stations +
hardened gate (+0.070/clustered +0.087). Caveats stand: mid prices (not
executable), Denver absent, ARCH-only weak alone.

## DOUBLE-BLIND VERIFICATION OF THE FIX WAVE (operator-ordered, 07-17 ~16-17Z)

Four independent blind agents (diff review, registry blast-radius, deployed-
artifact bit-for-bit, live measurement with no expected values). Results:
- **Deploy VERIFIED**: release 20260717_105239 content-identical to git HEAD
  (CRLF-only divergence from the Windows tarball — normalize CR before any
  future raw-hash check); runtime import prints exactly the 7 corrected
  stations; crons drift-free; rollback intact. Independent measurements
  matched every claimed number; wu_fails now reads a true 0.
- **CORRECTED STUDY B (right stations)**: L1 false-locks 0/579 (0.00%),
  L2 1/579 (0.17%) — both PASS the <1% gate (the old ~9% stays retracted);
  fills at <=0.97 post-lock exist on only ~1% of locked days → VIABILITY
  FAIL. Final verdict: **no free money at the close — supply vanishes after
  certainty** (not settlement risk). The 5 filled windows paid +0.08..0.44/sh
  on real size — Study-C-class rare events, accrual-watch only.
- **Blast-radius consequences (registry swap), assessed**: EMOS/calibration/
  reliability history under old ids is orphaned → the 7 cities run cold-start.
  That is CORRECT (the orphaned history measured the wrong airports — incl.
  Milan's "EMOS-ready" status). WATCH ITEM: sizing reliability factors reset
  to 1.0 baseline for those cities ~14 days (old haircuts lived under old
  ids); caps/dampeners still bound it. Cleared: no autodiscovery duplicates
  (static alias map wins), empty ghcnd inert, no stale old-id literals in
  runtime code. S232 cleanups: has_asos_1min flag is consumer-less;
  data/city_icao_mapping.yaml stale (not runtime-loaded).
- **Blind-review finds, fixed same hour**: gate docstrings now state the
  hardened bar (2af2c78); Jacksonville carried Houston Hobby's GHCND id —
  corrected (8ace004, inert field, next cut carries it). DISCLOSED: the
  corrected peak re-run covers Dallas+Houston via 1-min; **Denver is
  excluded** (Buckley has no 1-min product). Launcher lesson (4th instance):
  compound ssh `&` backgrounds the whole chain — the corrected peak run
  initially never started; relaunched with absolute paths.
- **NEW discovery by the blind measurer**: weather_forecasts contains rows
  keyed by lowercase CITY NAMES (busan/guangzhou/jeddah/karachi/manila/
  qingdao) — city_autodiscovery has been auto-covering unregistered cities
  with geocoded pseudo-stations of UNAUDITED quality. S232 additions task
  upgraded: replace dynamic auto-entries with verified ICAO stations.

## REAL-TIME SOURCE LEDGER (probed live from the VPS 2026-07-17 ~02:20Z)

Legit alternatives/additions to the WU PWS mesh, by resolution city.
"Latency" = probe-measured freshness vs UTC clock at probe time.

**VERIFIED WORKING — keyless, official, wire-ready:**
| Feed | Cities | Cadence / latency at probe |
|---|---|---|
| DWD Open Data (10-min TU "now" files) | Berlin EDDB, Munich EDDM | 10-min; last-modified == probe minute (~0 lag) — EXCELLENT |
| JMA amedas (`bosai/amedas/data/latest_time.txt` + point JSON) | Tokyo RJTT | 10-min; latest 02:10 @ 02:24 (~10-15 min) |
| data.gov.sg air-temperature | Singapore WSSS | ~1-5 min fresh (02:15 @ 02:20); station count needs a daytime check |
| HKO rhrread JSON | Hong Kong VHHH | worked, 26 stations; temp recordTime 02:00 @ 02:20 — cadence (10-min vs hourly) needs a 1h watch |
| BOM fwo JSON | Sydney YSSY, Melbourne YMML | official; ob 02:00 @ 02:20 (~10-30 min product) |
| SMN Argentina map_items | Buenos Aires SAEZ | works, 218 stations; ~hourly product |

**FIXABLE — endpoint alive, follow-up needed:** Geosphere Austria (Vienna —
right station id via metadata); EC MSC Datamart SWOB (Toronto/Vancouver/
Montreal — listings returned empty from the VPS at probe; retry with proper
UA/path, and the real prize is the AMQP push).

**FAILED at probe:** INMET (São Paulo) — empty responses.

**NEEDS OPERATOR SIGNUP (free tiers):** KMA data.go.kr (Seoul — MINUTELY
official AWS, best single upgrade); Météo-France (Paris, ~6-min);
Met Office DataHub (London); MET Norway Frost (Oslo); DMI (Copenhagen);
KNMI (Amsterdam); CWA (Taipei); plus the standing four: WU key, Synoptic,
NWWS-OI, MADIS (5-min US ASOS question).

**NO LEGIT REAL-TIME SOURCE (stay on METAR/half-hourly cadence + WU PWS):**
China ×6, India ×3, Russia, Mexico, UAE, Turkey, Egypt, Kenya, South
Africa, Israel, and remaining cities not listed above.

WIRING PLAN: national feeds are debias ANCHORS + redundancy for the PWS
mesh, not replacements; wire the wire-ready six into a `nat_mesh` collector
next session (or fold into pws_mesh) — prioritized by which cities actually
carry active markets, after Friday's mesh-lead verdict says how much the
PWS mesh alone delivers.

## Phase 2 — paper strategy (bot integration, gated on Phase 0 PASS)

- New resolution-day entry mode: when nowcast says P(running max crosses into
  bucket B by end-of-day) ≥ X and the book prices B below nowcast-fair minus
  costs → enter. **Maker-first:** rest bids at predicted-reprice levels; cross
  only if 0b showed taker-viability. Flag-gated OFF by default
  (`WEATHER_NOWCAST_ENTRY_ENABLED`), separate model_name in prediction_log so
  calibration machinery grades it independently from day-ahead entries.
- ALL existing risk plumbing unchanged (caps, dampeners, exposure,
  one-bet-per-market). This is a new signal, not a new risk model.
- S228 latency package (priority wake + faster polls) activates WITH this —
  it's the react leg of the same trade.

## Phase 3 — judgment (same bars as everything else)

- n ≥ 50–100 independent market-days per cell before any verdict; EV must
  clear costs with the error bar (±2 SE), not just the point estimate.
- Communicate via calibration + hit-rate, never P&L (#11).
- If it fails: this bot has no edge anywhere — say so and recommend
  decommission/maker-rewards-only. That outcome must stay on the table.

## Sequencing vs the existing queue (WEATHER_STATUS 2e)

Does NOT jump the queue: S222 re-run at n≥100-150 (~07-17/18), gate-retirement
decision, and the bootstrap-landmine fix proceed as planned. Phase 0 is
read-only research that runs alongside; Phase 2 is post-verdict, operator-scoped.
The broken AsosOneMinClient decision (fix-for-research vs remove) folds into
Phase 1.

## Session-1 execution list (next WB session, ~half day)

1. Phase 0a backtest harness: `scripts/wb_research/nowcast_skill.py` (IEM
   archive 1-min/5-min + METAR prints, per-station precision/lead table).
2. Phase 0b replay: extend executable_replay with T-minus-30 entries.
3. Phase 0c: first trade-print analysis (needs ~3+ days of prints — check
   accrual first).
4. Operator asks: WU rate-limit answer, NWWS-OI application submitted?

## S232 MESH-LEAD VERDICT (2026-07-18 00:2xZ) — PHASE-2 LEAD GATE: **PASS** (day 1)

IEM 1-min backfilled through 07-16 (the 40+h stall ended). First live `--lead`
run crashed `KeyError: KDEN` — mesh files written before the registry-fix
deploy (≤07-17 ~14:52Z) carry OLD station ids while `SID` is built from the
current registry. S232 fix (`8778f7d`): `LEGACY_SID` alias map (7 pairs
verified against commit `5fb0b56`), skip-don't-crash for unknown sids, pooled
gate median in the TOTAL line. The Sat 10:00 ET scheduled task would have
crashed identically.

**RESULT `--lead 20260716`** (run 07-18 00:1xZ; 8 gradeable cities of the 50
US cities in the file — the rest skipped on <300 1-min obs [partial IEM
backfill / no 1-min product] or no prints):

```
  KATL 13 ev / 5 led / 21.0 min / 0 false      KMIA 12 / 7 / 156.0 / 1
  KDEN 18 / 15 / 52.4 / 0   (legacy-id day)    KORD 11 / 9 /  70.0 / 2
  KIAH 12 /  9 / 92.1 / 2   (legacy-id day)    KSEA  9 / 1 /   4.2 / 0
  KLGA 10 /  7 / 93.6 / 1                      KSFO  8 / 3 / 179.9 / 4
  TOTAL 93 events | mesh-led 56 (60%) | false-crossings 10
        | pooled median lead 74.8 min (n=56 led events)
```

**Gate grades (spec bar → measured):** led ≥50% → **60% PASS**; pooled median
≥15 min → **74.8 min PASS**; false-crossing rate <20% → **10.8%** (10/93 truth
events; 15.2% under the most conservative denominator led+false) **PASS**.

**Caveats (disclosed):** ONE day of data; KDEN/KIAH are legacy-id days
(arbiter = the OLD station — Denver's correct station KBKF has no 1-min
product, so Denver will NEVER be 1-min gradeable going forward); KLGA's 1-min
reappeared (was absent at S231 close); no city >19% of events (concentration
OK); per-city weak spots consistent with the diurnal story — KSEA 1/9 led,
KSFO 3/8 with 4/10 of all false-crossings. The OTHER two acceptance gates are
NOT yet met: ≥7 days mesh uptime + per-PWS debias table (~07-23 earliest) and
the operator WU key/Synoptic token. Lead gate passing ≠ flag goes true.

**Day-over-day bias:** 00:0xZ re-run of `--bias 20260717` (651 prints / 37
cities) reproduces the 23:5xZ S231 day-2 read within print-matching noise
(KDAL −0.75 vs −0.77, KBKF −0.50 vs −0.62, KHOU +2.25 vs +2.04 medians) —
table stable, no new classification changes. RKSI/RCSS first correct-station
reads land with the 07-18 Asia local day (new ids only began logging ~15Z
07-17 ≈ local midnight). RCTP old-station read (−9.65F median, tight sd)
retroactively confirms how badly Taipei was miswired.

## S232 REGISTRY-ADDITIONS EVIDENCE (queue item 1 — evidence only; the code
## change stays Tier-3 + operator sign-off + defect tests + release cut)

Primary source: live Gamma market DESCRIPTIONS (pulled 07-18 00:3xZ; every
description embeds the resolution WU URL whose last segment is the station
code). AWC METAR probe = live `api/data/metar` same hour.

| City (dyn key) | Description names | ICAO | METAR live? | Action |
|---|---|---|---|---|
| busan | Gimhae Intl | RKPK | YES | ADD |
| cape_town | Cape Town Intl | FACT | YES | ADD |
| guangzhou | Baiyun Intl | ZGGG | YES | ADD |
| jeddah | King Abdulaziz Intl | OEJN | YES | ADD |
| manila | Ninoy Aquino Intl | RPLL | YES | ADD |
| panama_city | Marcos A. Gelabert / Albrook | MPMG | YES | ADD (NOT Tocumen MPTO) |
| qingdao | Jiaodong Intl | ZSQD | YES | ADD |
| karachi | **Masroor Airbase** | OPMR | **NO — zero METARs** (AWC 72h + IEM 4d both empty) | **SPECIAL** — see below |
| lagos | (no active market found) | — | — | defer; dyn row exists but roster inactive |

- **Karachi trap:** the description PROSE says "Masroor Airbase Station" while
  its WU URL says `/pk/karachi/OPKC` (Jinnah Intl). The WU page at that URL
  RENDERS Masroor/OPMR as its station — WU's Karachi city page defaults to
  OPMR regardless of the URL slug. Resolution follows the WU page ⇒ correct
  station = OPMR, which publishes NO METARs. Karachi therefore joins Hong
  Kong (HKO) in the "resolution source is not a METAR station" class —
  grounding needs WU-page/synop data, NOT an OPKC proxy. Do NOT add OPKC:
  that would recreate exactly the S231 wrong-station defect.
- Istanbul: NOAA `weather.gov/wrh/timeseries?site=LTFM` confirmed in the live
  description — S231's LTFM fix matches the current resolution source.
- Jinan / Zhengzhou: not in dynamic_stations and no active events — markets
  gone; add only if they return (re-pull descriptions then).
- Remaining before the Tier-3 change: coords/elevation/GHCND per ICAO, WU
  page spot-check per city (Karachi-style URL-vs-page traps), defect tests,
  release cut — all under operator sign-off per the S231 fix pattern.

## S232 REP_BIAS RECOMPUTE AT CORRECTED STATIONS (queue item 4) — S230 READ REVERSED

`rep_bias_test.py 18` re-run 07-18 00:4xZ on the deployed (corrected) registry
— stations incl. KDAL/KBKF/KHOU; output `~/wb_research/rep_bias_s232_corrected.out`.
Window 06-27..07-15, 180 station-days, n=61 resolved winner buckets:

- winner bucket contains HOURLY-PRINT max H: **61/61 (100%)**
- winner bucket contains CONTINUOUS 1-min max C: 22/61 (36%)
- C−H mean +0.84F (SE 0.16) — the continuous max still runs high, but
  **resolution does NOT track it**
- WU−H +0.11 (≈0), Fm−H +0.45 vs Fm−C −0.08/WU−C −0.59

VERDICT: the old "81% tracks C ⇒ resolution is the continuous max" number was
wrong-station noise. At correct stations **resolution = the print-world max**
(consistent with the S231 audit note "settlement DOES follow the print-max"
and Study-B 0/268 false locks). Consequences: (a) the S230 representativeness
-bias root-cause candidate for the cheap-NO tail is DEAD — the bot's stored
ground truth (WU−H≈0) already lives in the same world resolution grades;
(b) the mesh/nowcast framing is unchanged and slightly strengthened: the
thing worth leading is the PRINT, and 1-min-only crossings that never print
are pure false positives — exactly what the false-crossing gate measures;
(c) any future "resolution is continuous" claim must cite THIS recompute, not
S230's contaminated table.

## S232 PHASE-2 BUILD (operator GO 07-18 ~00:3xZ; "go and review to flip on day 2")

**BUILT, flag OFF.** Commits `1bd54f4` (data plane) + `1db55e6` (drop-rule
residual fix) + `9bfd27a` (signal). Flag = `WEATHER_NOWCAST_ENTRY_ENABLED`
(declared, default false); flip = Tier-2 operator decision at the day-2 review.

**Data plane (research layer, ubuntu crons; bot only READS):**
- `/opt/pa2-weather-feeds/` (0755, ubuntu-owned; service user reads under
  ProtectSystem=strict — /home/ubuntu is 750 and unreadable, hence the mirror).
- pws_mesh dual-writes each tick's rows there (best-effort; research file
  canonical; mirror verified live 00:27Z).
- `mesh_debias.py` cron (15 9 * * * UTC): trailing-5d per-PWS offsets vs the
  METAR PRINT series — scalar + LOCAL hour-block (morning/afternoon/evening,
  block published at n>=8) so diurnal handling is EMERGENT, never a hardcoded
  city list; per-city post-debias residual sd; drop rule sd>1.5F. First runs:
  30 cities tabled; drop list 20 (thin day-1 blocks — expect shrink as block
  samples accrue; KSFO/ZGSZ stay legitimately dropped). Residual defect
  caught+fixed on first output: scalar-only residuals over-fired the drop
  rule at diurnal cities (25→20 after computing residuals with the same
  offset the consumer applies).
- Atomic table writes (tmp+rename); feed-dir day files pruned >7d.

**Signal (weather_bot.py, all inside try/except isolation, S231 doctrine):**
- `_scan_nowcast_entries` (scan_and_trade Phase-4 tail): debiased consensus
  running-max (median of >=2 known-PWS debiased obs per 5-min bin; unknown
  PWS excluded — no offset means raw bias) crossing into range/at_or_higher
  buckets, FROZEN peak rule (E_rem <= 1.0F-equivalent native, local hour >=
  12), staleness gates (mesh <=900s, table <=48h), dropped-city exclusion,
  price <= model_prob − min_edge with model_prob 0.44 = the backtest's
  measured win rate (nowcast_peak_133d.out; NOT calibrated — the paper phase
  calibrates it under its own model_name `weather_nowcast_peak`).
- Maker-first approximation (no resting-order machinery exists): enter ONLY
  when price already sits at/below the would-be bid — strictly conservative
  vs task-2's 65-71% post-reveal fill upper bounds.
- Sizing honesty: `WEATHER_NOWCAST_MAX_PER_WINDOW_USD` 50 per
  (station,date), Redis-tracked (`weatherbot:nowcast_spent:*`, 48h TTL);
  threaded as `_st_size_override` + `_nowcast_window_remaining` hard clamp;
  flat-mode bypass (deployed WEATHER_FLAT_SIZE_USD=100 would otherwise
  consume-and-ignore the override). Window counter increments by the
  RESERVED amount (>= executed after dampeners) — conservative side.
- ALL existing risk plumbing unchanged and applies: same-side dedup, exit
  cooldowns, spread + executable-edge gates (fail-closed), YES price floor,
  dampeners, group/city caps + lock-guarded reservation, daily loss limit.
- Cancel-on-invalidate: `_evaluate_nowcast_invalidations` — held RANGE
  bucket whose debiased runmax ROUNDS above high_bound → SELL held token via
  the canonical 4-step chain (order first; cooldown→Redis→exposure only on
  confirmed fill; reason NOWCAST_OVERSHOOT). Tracking in Redis
  `weatherbot:nowcast_pos:*` (48h TTL, restart-safe); positions without a
  fresh scan price are HELD and retried. Runs before entries each scan.
- prediction_log: `model_override` → model_name `weather_nowcast_peak`
  (graders/calibration_check/calibrator treat it independently).

**Tests:** 15 defect tests (tests/unit/test_nowcast_mesh.py) — debias
block-vs-scalar, table staleness, local-day/qc/sid filtering, min-PWS +
unknown-PWS exclusion, crossing/overshoot rounding conventions, flag-off
no-ops, admission blocks (repriced/dropped/E_rem/not-crossed), window cap
SURVIVING flat mode through the real _execute_weather_trade path, overshoot
SELL chain, hold-without-fresh-price. Weather suites 389 passed. A broken
intermediate edit (flat-branch `if` dropped) was CAUGHT BY the cap test
before commit — the defect-test-first requirement earned its keep.

**NOT flipped:** the spec's other acceptance gates stand — >=7d mesh uptime
with populated debias tables (~07-23) + operator WU key/Synoptic token. A
day-2 flip overrides those two gates: operator's call, to be presented
explicitly at the Sat review. React leg (S228 env flips) activates WITH the
flag, same moment, per the design. PEAK-PASSED second signal NOT built this
session (same infra, separate build after the crossing leg proves out).

## S232 DAY-2 LEAD VERDICT + FLIP (2026-07-19 ~13:4xZ) — **FLAG IS ON (paper)**

**Day-2 (`--lead 20260717`, graded after IEM backfill ~Sun 13:2xZ): PASS —
gates hold twice.** 9 gradeable cities, 89 events, **62% mesh-led** (≥50% ✓),
**pooled median lead 61.0 min** (≥15 ✓), false-crossings 10 = **11.2%** of
truth events (15.4% worst-case denominator) (<20% ✓). Day-over-day: 60%/74.8/
10.8% → 62%/61.0/11.2% — consistent. First correct-station lead reads: KDAL
7/13 led (19.0 min), KHOU 5/7 (82.2 min). KSFO again the false-crossing
hotspot (5 of 10). Max city share 15% (concentration OK). 07-18 lead grade
still backfill-gated (~07-20).

**Sat-review data-plane findings (fixed `448c05c`):** (a) `iem_prints` day2
bound is EXCLUSIVE — the current day was silently dropped from every debias
window and RCSS could never enter the table; (b) pws_mesh null-temp obs never
counted as misses — Seoul/RKSI logged 600+ temp_f=None rows without rotating.
**RCSS first correct-station read: −8.13F median (sd 1.53) — REFUTES the
audit's "mesh right near Songshan" reinterpretation; the Taipei roster itself
is broken and the drop rule correctly excludes it.** RKSI rotates when its
local window reopens; KMA signup remains the best single upgrade.

**FLIP executed 2026-07-19 ~13:3xZ on the operator's standing order ("go and
review to flip on day 2") after the day-2 PASS:** `WEATHER_NOWCAST_ENTRY_ENABLED=true`
+ `WEATHER_PRIORITY_WAKE_ENABLED=true` (react leg, per design) appended to
WB-owned `/opt/pa2-shared/.env.weather` (backup `.env.weather.bak_20260719_preflip`);
service restarted 13:34:40Z; both flags verified in the process env; scans
normal; nowcast hooks armed (silent until a crossing — correct). PAPER mode;
self-limiting to the non-dropped debias-table cities (13 at flip time);
$50/(station,date) caps. **Overridden gates, disclosed at decision time:**
≥7d debias depth (table was ~3 days) and the WU key/Synoptic token (still
pending). Kill: both flags false + restart. First calibration data accrues
under model_name `weather_nowcast_peak`.

## S232 RETRO SHADOW REPLAY (operator order 07-19 "get what you missed back")

`nowcast_retro_shadow.py` replays the EXACT deployed nowcast rule over the
mesh days that predate live shadow logging (07-17/18/19-capped-at-13:50Z) and
grades each would-be prediction vs the actual market resolution. No-lookahead:
mesh obs, forecasts (forecast_time-stamped), and resolutions were all recorded
contemporaneously. RESEARCH LEDGER ONLY (`~/wb_research/nowcast_retro_shadow.out`)
— NOT inserted into prediction_log (retro rows would defeat the temporal-order
guard). det-high proxied by the stored ensemble MEDIAN (closest recorded
equivalent of the live deterministic_high); no price gate (grades the SIGNAL,
same semantics as the live shadow set).

**RESULT (CORRECTED, re-review c6): n=2, 1 win = 50% (vs model 0.44), Brier 0.254.**
- 07-17: 1 pred (KLGA), 0 wins — as-of table was 07-16 US-only, 2 clean cities
- 07-18: 1 pred (KDAL), 1 win
- 07-19: 0 (capped at the 13:50Z shadow deploy; US barely into eligible hours)

⚠ **The FIRST run reported n=6 (3 wins) — that was WRONG.** It proxied the live
rule's `forecast.deterministic_high` with the ensemble MEDIAN; the stored
deterministic_high (NBM, higher for US 'F' stations) is what the deployed rule
uses. Using it (c6 fix, `SELECT deterministic_high`) raises E_rem above the 1.0F
frozen-peak bar for 4 of the 5 07-17 candidates → they correctly drop out.
Corrected recovered count = **2**, not 6.

**Honest note on the recovery estimate:** I told the operator ~20-35 recoverable;
actual is **2** (my n=6 was itself inflated by the median-proxy bug). The estimate
anchored on the signal's raw fire rate (~8/day) and ignored the clean-city ∩
resolved-market intersection. 2 points is a footnote, not a scorecard. The live
shadow set (deployed `20260719_095037`, further fixed by re-review c2 so nowcast
rows near 0.44 are no longer deduped away) is the real accumulation path.

## S222 GATE-RETIREMENT RE-CUT (2026-07-19, N=627) — RETIRE NOTHING

Re-cut at the now-met sample gate (S230 ran n=133; clean window since the
2026-07-13 16:02:29Z EMOS-fix restart is now N=627 distinct resolved markets).
Canonical measurements only (calibration_check --since 20260713_160229 --clean
--dedup-markets; weather_brier 5; weather_brier_by_side 142; bot_pnl 142 for
conf-bins). Preconditions all PASS (release ≥ floor; S222 A1/A3 + S227≥4
fingerprints; VIF default 1.4; calibration reloaded 6/24h, 0 fails; leak=0;
PSW-frame-null=0; **nowcast rows 0 resolved in window → no S232 contamination**).
Verdict cross-checked by a 4-gate independent grade + adversarial-verify +
synthesis workflow (all four verdicts HELD under adversarial attack).

| Gate | Verdict | Decisive number | Action |
|---|---|---|---|
| A1/A3 raw pipeline (VIF 1.4) | **FAIL** | PIT KS p=0.0000 (baseline p<1e-4 — did NOT rise); PIT mean 0.563→0.588 (more overconfident); traded high-conf gaps −0.12/−0.34/−0.30 → −0.14/−0.36/−0.38 (widened) | keep + tune VIF |
| YES/NO price dampeners | **FAIL** | no positive BSS anywhere (overall −0.1205); traded 0.70+ bins miss stated by −14/−36/−38pp (n=24/33/27) | KEEP |
| YES/NO max-entry-price caps | **INSUFFICIENT** (→ keep) | NO 80-100¢ n=0 — the cap blocks its own test data; skew unmeasurable while cap is live | KEEP |
| Flat-size → Kelly (C0) | **FAIL** | [0.9,1.0) realized WR 55.6% (n=27) vs required ≥0.85; PIT KS still rejects | KEEP flat |

**Delta vs S230 n=133: nothing moved — the 4.7× larger sample flipped no
verdict.** It converted "failed but thin" into "fails with authority": KS is now
decisively powered (crit ≈0.054, observed 0.1532 ≈2.8× over) so A1/A3 and
KELLY's calibration leg are no longer arguably under-powered; the traded
high-conf bins now carry real mass (n=17/24/33/27) so the dampener overconfidence
miss is measured, not inferred — and WIDENED vs baseline. CAPS stayed
structurally unmeasurable: more data can never populate the 80-100¢ cell while
the cap blocks those entries.

**Highest-value next action (operator-scoped, Tier-1):** raise
`WEATHER_VARIANCE_INFLATION_FACTOR` above 1.4, restart the 4 shared-env services
(sequence behind MB per standing priority), re-measure PIT KS + traded 0.70+
reliability on a fresh clean window BEFORE reconsidering any control. VIF is the
direct lever on the systemic overconfidence (PIT mean 0.588, top bin 2.34×) that
underlies A1/A3's FAIL and gates KELLY; it touches neither the calibrator nor any
gate under test. Do NOT lift the cap to get 80-100¢ data (removes a live control
to measure it) — populate CAPS off-book via counterfactual scoring of 80-100¢
predicted_prob rows vs realized outcomes until NO 80-100¢ and 0-20¢ each reach
n≥20.

**Disclosures:** (1) book ~98% NO — 617/627 markets carry a NO-side row, YES
traded n=6; every FAIL survives dropping YES entirely. (2) 494/627 (79%) of mass
sits in the two LOWEST bins, which are UNDER-confident (0.041→0.232) — the
whole-distribution KS is partly tail-driven, which muddies reading CAPS
specifically. (3) Miswired-city (Dallas/Denver/Houston pre-07-17-remap)
contamination UNQUANTIFIED — the prediction_log→markets join returned an
impossible 0 (Gamma-id vs condition_id key-mismatch trap), so no magnitude
asserted; bounded well under the ~65% KS reduction needed to un-reject, cannot
flip p=0.0000, but is a real unmeasured contaminant. (4) Calibrator mid-relearn
since 07-11 (53 leak-era rows until ~08-07); NOT judged/gated/touched;
baseline-vs-now comparisons cross a fitted→near-identity boundary → directional,
not clean deltas. Re-cut retirement only after that window clears AND a fresh
clean window has ≥30 traded resolutions per 0.70+ bin.

## S222 FOLLOW-THROUGH — VIF TUNE (2026-07-19, operator-authorized "do it")

Acting on the re-cut's highest-value next action. Tier-1 threshold tune:
`WEATHER_VARIANCE_INFLATION_FACTOR` **1.4 → 1.8**.

- **What/where:** appended to WB-owned `/opt/pa2-shared/.env.weather` (backup
  `.env.weather.bak_20260719_vif`); polymarket-weather ONLY restarted
  (ActiveEnter 2026-07-19 17:10:10Z). NOT the shared `/opt/pa2-shared/.env` —
  no 4-service restart, no MB-priority collision.
- **Mechanism (code-read, probability_engine.py:135 & :156):** VIF multiplies
  the ensemble std ONLY on the NON-EMOS path (`std * _vif`); EMOS-calibrated
  stations use `emos_sigma` and are UNAFFECTED. So this widens dispersion for
  uncalibrated stations/leads only — currently the 7 cold-start corrected
  stations + most international cities. It touches neither the calibrator nor
  the nowcast signal (fixed model_prob 0.44 bypasses this engine) nor any S222
  gate under test.
- **Why 1.8:** within the cited empirical underdispersion range (1.3-2.0x,
  Gneiting 2005/MeteoSwiss); upper-half because overconfidence is MEASURED
  (PIT mean 0.588, top PIT bin 2.34x), not maxed so a second step (→2.0) stays
  available. Judgment call, reversible in one line.
- **Expected impact:** non-EMOS predictions less extreme → PIT mean toward
  0.5, top-bin overload down, the cheap-NO tail's 0.041→0.232 gap narrows on
  the non-EMOS subset. Does NOT fix EMOS-station overconfidence (that is the
  calibrator's job, hands-off until ~08-07). So expect a PARTIAL PIT
  improvement, not full uniformity.
- **Verified live:** all 3 flags in process env (VIF=1.8, nowcast + priority-
  wake still true); scan completed (47 cities/100 groups/309 weather markets);
  only error is the pre-existing `_publish_signal` startup transient.
- **Rollback:** remove the VIF line from `.env.weather` + restart
  polymarket-weather → reverts to code default 1.4.
- **RE-MEASURE (scheduled ~07-24):** re-run calibration_check
  `--since 20260719_171010 --clean --dedup-markets` once N≥50 distinct resolved
  accrue under VIF=1.8; compare PIT KS/mean + non-EMOS reliability vs the
  pre-tune 0.588. If PIT improves but still rejects, consider VIF→2.0 (last
  step) BEFORE any control retires. No control retires on this window
  regardless (calibrator still mid-relearn until ~08-07).

## S232 SECOND TRIPLE-BLIND — FIX VALIDATION + UNMASKING SWEEP (2026-07-19)

Operator asked: are the 4 re-review fixes (c1/c5/c2/c6) real & correct, did any
break another item, and did the original bugs HIDE anything? Ran a 6-blind-finder
→ triage → adversarial-verify → synthesis workflow.

**Fixes validated:** all 4 CONFIRMED real bugs, correctly fixed, NO live-path
regression (independently: c2 does NOT reach the live confidence calibrator — it
fits from trade_events with a lead>=48 filter — nor EMOS/SAMOS which never read
prediction_log; tuple-key eviction is safe; c6 is research-ledger-only). Full
suite 4007 passed corroborates.

**UNMASKED — 2 pre-existing WB bugs the fixes revealed (both FIXED now):**
- **c9 (MED, `weather_bot.py`):** `_backfill_weather_outcomes` fed every resolved
  WeatherBot row — incl. constant-0.44 nowcast rows sharing the crossing market's
  market_id — into `_update_city_brier` (keyed by CITY, not model_name). That
  deque drives `_get_city_brier_mult` → a <1.0 dampener on the MAIN model's
  `combined_boost` in that city. Fix: exclude `model_name LIKE '%nowcast%'`.
- **c11 (MED, `calibration_check.py`):** the read-side twin of c2 —
  `_dedup_latest_per_market` keyed by market_id alone, so the later-logged
  nowcast 0.44 row won the `--dedup-markets` collapse and REPLACED the main
  model's prediction in the WeatherBot reliability/PIT/Brier (the exact mode
  the S222 re-cut uses). Fix: dedup by (market_id, model_name) + exclude
  `%nowcast%` from all three cuts (count/main/side×lead). **The 07-19 S222 re-cut
  itself was CLEAN — 0 nowcast rows had resolved into that window (verified at
  run time); the 07-24 VIF re-measure would NOT have been, so this had to land
  before then.**

**Cross-bot flags (NOT fixed from WB — RULE ONE-A / shared-module):**
- **c13 (MED, Maker):** pre-c5, executed nowcast entries appended constant-0.44
  lines to `/opt/pa2-maker-feeds/wb_forecasts.jsonl` mislabeled `weather_temperature`
  (WEATHER_MAKER_FEED_ENABLED defaults TRUE). c5 stops future pollution but does
  not purge written lines; the pending Maker forecast-tilt readout could ingest
  them. Bounded ≤1 line/(station,date). **Maker/operator action:** audit 07-19
  `wb_forecasts.jsonl` lines with prob≈0.44 & model==weather_temperature and
  purge before the readout.
- **c12 (LOW, MB/shared):** `base_engine/features/calibration.py`
  Focal/FavoriteLongshot `fit_from_prediction_log` + `database.py`
  `get_recent_brier_from_prediction_log` have no bot_name/model_name filter, so
  MB's calibrator + the learning-scheduler brier meta-weighting now ingest the
  weather_nowcast_peak 0.44 rows. INERT for the WB live path. **Flag for the
  MB/owning session** (a bot_name/model_name filter is feasible).

**Intended-but-now-visible (NO code change):**
- **c7 (INFO):** the window cap charges the RESERVED amount (spent+remaining =
  full $50 on the first entry) regardless of the smaller executed size → exactly
  ONE nowcast entry per (station,date) window, ~45% nominal utilization
  (fail-safe under-deploy). Documented as intended in the method docstring.
  Operator may intent-confirm `WEATHER_NOWCAST_MAX_PER_WINDOW_USD=$50` as a
  one-dampened-shot ceiling.
- **c8 (LOW):** residual write-blip double-spend — if the raw READ succeeds but
  the charge WRITE blips transiently and recovers before the next scan, a second
  crossed bucket can re-enter uncharged. NOT a regression (the old `except: pass`
  had the identical gap, silently); c1 made it LOUD (`weatherbot_nowcast_cap_charge_failed`)
  but added no guard. Bounded (~1 extra $50 window, paper, flag-gated); sustained
  outages fail closed at the read. Accept-as-is is defensible; optional
  pre-charge/refund mirroring the exposure reserve/revert.

Two REFUTED first-pass candidates (c3 conservative-direction group gate; c4
producer-guarded row extraction) stayed refuted.

## S232 THIRD PASS — ROOT-CAUSE vs BAND-AID AUDIT (2026-07-19)

Operator: "verify all fixes are root-cause, not band-aids." Ran a 5-blind-classifier
→ adversarial-adjudicate → synthesis workflow over all 14 session code commits.

**Verdict: 0 genuine band-aids.** Every fix removes a real defect/contaminating data
at its call site; none masks a symptom or swallows an error. 7 unanimous root-cause;
the rest partial-justified (scoped by Rule 4 / MB-priority). BUT the audit caught one
thing the two prior review passes missed:

**BLOCKING REGRESSION (fixed): c11 shipped a live `ValueError`.** The c11 fix added
`pl.model_name` (7th SELECT column) but the consuming loop at calibration_check.py:199
unpacked 6 → `ValueError: too many values to unpack` on EVERY non-empty run — it
crashed the S222 / VIF-re-measure gate readout (offline tool; bot/capital never at
risk). My c11 tests only hit the helper functions, not the integrated
`calibration_check()` path — the "prove it after" step was skipped for the tool.
Before/after PROVEN on the VPS: deployed release raised the ValueError; the fix runs
clean (main-model N=683, verdict unchanged A1/A3 FAIL). Fixed + end-to-end integration
test added (drives calibration_check() with mocked 7-col rows). Deployed `20260719_185505`.

**Other audit reworks (all WB-local, done):**
- **F5b** (weather_bot.py): the nowcast overshoot-exit skip when a later NO entry
  overwrites the one-slot-per-market `_position_details` (order_gateway.py:69) is now
  VISIBLE (`weatherbot_nowcast_overshoot_skipped_slot_overwritten`) instead of silent.
  Fail-SAFE (YES rides to a bounded ≤$50 resolution; never an erroneous sell). The
  fully-correct fix = per-(market,side) tracking in order_gateway = a shared structural
  change, FLAGGED not jammed into a bugfix.
- **F7** (mesh_debias.py): now fails LOUD (`sys.exit(1)`) on a broken feed dir after
  writing the dated research copy first, instead of a swallowed error + exit-0 that
  left the bot on a silently stale table.

**Kept deliberately (not band-aids):** the `LIKE '%nowcast%'` filters (c9/c11) — LIKE,
not `=`, is intentional: it catches the planned `weather_nowcast_peakpass` family and
is a no-op for non-WeatherBot rows. **Flagged as system-wide latent debt (NOT WB-fixable):**
`RedisCache.get()` swallows every exception → phantom cache-miss for any correctness-
critical caller (c1's deeper cause; left under Rule 4). Proposal for the shared/MB owner:
a variant that distinguishes error from miss. Full suite 4010 green.

## S232 SHARED REDIS ROOT FIX (2026-07-19, operator-ordered, CROSS-BOT)

Operator: "fix shared redis now for all deep root fix, notify all other bots."
The deeper root cause behind c1 (flagged as system-wide debt) — `RedisCache.get()`
/`set()` swallow EVERY exception → None/no-op, so a Redis ERROR is indistinguishable
from a real MISS and correctness-critical callers fail OPEN on an outage.

**Fix (both copies on the WB branch):** added opt-in `raise_on_error: bool = False`
to `get()` and `set()` in `base_engine/data/redis_cache.py` (top-level/shared) AND
`bots/weather/engine/base_engine/data/redis_cache.py` (WB vendored). Default False =
BYTE-IDENTICAL legacy behavior — all ~225 existing `cache.*` callers across all 14
bots unaffected. Correctness-critical callers pass `raise_on_error=True` → error
RAISES (fail closed) while a real miss still returns None. New capability test
(`tests/test_base_components.py::test_redis_cache_raise_on_error`). Full suite green.

**Deploy split:** WB deploys its vendored copy (inert for WB today — c1 already
fixed at the call site via the raw handle; the param is available for future/other
adopters). The top-level copy reaches MB/main services only via a master merge +
deploy.sh = **MB/operator action (PROPOSED, not executed from WB — RULE ONE)**.

**Cross-bot notification:** shared memory `project_shared_redis_get_root_fix.md` +
MEMORY.md coordination pointer (every session reads memory at start). Per-bot
adoption actions (MB cherry-pick to master; EB/Maker/SB apply the 3-line change to
their branch copy + adopt the flag in correctness-critical reads) are listed there.
Related untouched debt noted: `RedisCache.delete()` same pattern; c12 shared
prediction_log calibrators unfiltered (MB).

## S233 DAY-3 LEAD VERDICT (2026-07-20 ~00:3xZ) — **PASS, gates hold a THIRD time**

Day-3 (`--lead 20260718`) was IEM-1-min-backfill-gated at S232 close. Backfill
confirmed landed this session by a per-station coverage probe against each
station's OWN 07-16 baseline (cross-station row counts are NOT comparable —
1-min reporting cadence varies per station):

```
sid   0718 rows   0716 baseline      read
LGA        720          721          covered (LGA reports ~2-min)
LAX        335          356          covered (LAX is natively sparse)
ORD       1440         1196          covered (exceeds baseline)
ATL       1138         1197          covered
SEA       1440         1196          covered (exceeds)
DAL       1440         1440          covered
MIA       1440         1197          covered (exceeds)
BKF          0            0          STRUCTURAL — no 1-min ASOS at Buckley
HOU       1440         1440          covered
SFO        886         1347          THIN (66% of baseline)
AUS       1325          781          covered (exceeds)
```

**Grade: 8 gradeable stations, 87 events, 63 mesh-led = 72% (≥50% ✓), pooled
median lead 49.0 min (≥15 ✓), false-crossings 5 = 5.7% of truth events
(<20% ✓).** Per-station: KATL 10/16 (18.6 min), KAUS 12/12 (93.4), KDAL 7/15
(19.0), KHOU 6/9 (87.7), KLGA 0/2 (nan), KMIA 8/9 (76.0), KORD 7/8 (55.2),
KSEA 13/16 (54.0). Concentration OK — max city share 16/87 = 18% of events,
13/63 = 21% of led events (Protocol 14 checked BEFORE presenting).

**Three-day trend: 60%/74.8min/10.8% → 62%/61.0/11.2% → 72%/49.0/5.7%.**

**Two caveats recorded so a later session does not over-read the improvement:**
1. **Median lead is monotonically DECLINING** (74.8 → 61.0 → 49.0 min). Still
   3.3x the 15-min gate, but three points cannot separate meteorological regime
   from systematic drift. WATCH — if day-4/day-5 continue down, investigate
   before treating the lead as a stable ~50-min property.
2. **The false-crossing improvement is partly COMPOSITION, not quality.** KSFO
   was the day-2 false-crossing hotspot (5 of 10) and is ABSENT from the day-3
   grade (thin 1-min coverage, 66% of baseline). KLAX likewise absent; KBKF
   structurally ungradeable. So 5.7% is measured over a station set that
   excludes the known worst offender — weight it cautiously, do NOT quote it as
   a clean halving vs day-2.

No bot-code change; grading is read-only research. Flag state unchanged
(ON, paper) — day-3 is confirmatory, the flip already happened on day-2.

## ⚠ RULE ONE RESCINDED 2026-07-20 (operator) — MB IS A PEER, NOT TOP DOG

Recorded at S233 close, AFTER the S232 blocks above were written. **All bots are
PEERS on shared resources** — deploys, master merges, shared modules,
`/opt/pa2-shared/.env`, shared RPC, VPS capacity, operator bandwidth.
**Coordinate on contention; there is NO default winner.**

**Stale framing in THIS file and in commit `e37d666`'s body** — specifically the
"MB/operator action (PROPOSED, not executed from WB — RULE ONE)" language at
~:1160 and the "MB cherry-pick to master" phrasing at ~:1164 — reflects the
pre-rescission rule. Those lines are left as written (historical record; do not
rewrite committed history) but **must NOT be used to derive current priority.**
The shared RedisCache fix still needs to reach master + deploy; that is now a
peer-coordinated action rather than MB's prerogative, and the deploy matters
because deploy.sh restarts mirror/esports/ingestion (several bots' runtimes).

**What did NOT change:** Layer-1 scope (a bot-scoped session works ONLY on its
own bot's code) and **RULE ONE-A** (WB/EB sessions never touch MB — no reads of
MB code/env/handoffs/telemetry). Those are separate rules and still bind. The
rescission removes MB's *right of way*, not WB's *scope boundary* — do not read
"MB is a peer" as "WB may now edit MB."

## S233 REGISTRY ADDITIONS — BUILT (commit e49aa01, awaiting deploy sign-off)

Queue item 1 executed. 7 static WeatherStation rows added
(busan/cape_town/guangzhou/jeddah/manila/panama_city/qingdao), replacing the
lowercase dynamic auto-discovered pseudo-stations that had been resolving these
cities at city CENTROIDS (empty ICAO, source=open-meteo-geocoding, created
2026-05-31). Measured centroid-vs-airport offset (live dynamic_stations DB row
vs AWC airport metadata):

```
city          ICAO   offset_km
qingdao       ZSQD      42.2
guangzhou     ZGGG      31.2
jeddah        OEJN      21.8
cape_town     FACT      17.1
busan         RKPK      12.0
manila        RPLL      11.1
panama_city   MPMG       4.9      (median 17.1, max 42.2)
```

This is an ACTIVE wrong-station defect of the S231 class — not an enhancement.
lookup_station checks static before dynamic, so the new rows shadow the centroid
rows and move the forecast query point onto the resolution airport.

**Every field verified independently (S233, 2026-07-20):** station_id + AIRPORT
coords from live Polymarket market descriptions ("...recorded at the <Airport>
Station...") AND live AWC METAR, cross-checked; temp_unit="C" verbatim in each
market text; timezone from the live dynamic rows (jeddah=Asia/Riyadh UTC+3, NOT
Dubai UTC+4); ghcnd_id="" (seoul/taipei/istanbul/milan precedent);
panama_city=MPMG (Gelabert) explicitly NOT Tocumen MPTO. Karachi NOT added
(OPMR, no METARs; OPKC = S231 trap) — locked by a defect test.

**Adversarial review:** 33-agent fan-out (7 per-station fact + 5 integration
lenses + per-finding refutation). 21 HIGH/MED raised; the verify pass corrected
most "HIGH" to MED/LOW and NONE impugned a shipped value — they drove the test
set (exact-coord/hemisphere guard, explicit temp_unit=='C', ICAO-shape+unique,
static-shadows-dynamic, karachi-exclusion). Full suite 4017 passed. Both
byte-identical registry copies edited in sync.

**KNOWN, DISCLOSED, non-blocking:**
1. **Calibration cold-start (expected, corrective):** each city's bias/EMOS
   history was accumulated under the old lowercase station_id against centroid
   coords; the ICAO key starts with zero pairs and falls back to the pooled
   global path until fresh (forecast,actual) pairs accrue. The discarded history
   was miscalibrated anyway (wrong coords) — this is the S231 renamed-city
   cold-start pattern, not a loss. WATCH the 7 cities re-learn EMOS post-deploy.
2. **busan local_model:** operator opted IN (S233) — busan now carries
   local_model="jma_seamless" (commit after e49aa01), matching the seoul Korea
   analog; JMA's domain covers Busan's coords. Locked by
   test_s233_busan_uses_jma_local_model. (This was flagged as a
   model-behavior change requiring explicit consent; consent given.)
3. **panama_city alias "panama city":** follows convention (mexico_city etc.).
   No US "Panama City, FL" market exists today; if Polymarket ever lists one it
   would word-boundary-match MPMG (Panama, Celsius) — a prospective ambiguity of
   the same class every shared city name carries. Documented, not guarded.

**DEPLOYED 2026-07-20 (operator-authorized "you do it"): release
`20260720_113011`.** Sequence: git archive HEAD (71ba226) → scp → wb-release-cut.sh
→ record parity (5dcdb26). Restart 15:31:08Z. POST-DEPLOY VERIFIED in the running
release venv: registry 114, all 7 resolve to their ICAOs via lookup_station,
busan local_model=jma_seamless, 3 nowcast flags survived (.env.weather not in the
tarball), scan healthy (`weatherbot_scan_done active_cities=49 weather_markets=341
groups=109`), 0 station/import errors post-restart. Rollback = symlink back to
`20260719_195417` + restart. WATCH: the 7 cities cold-start EMOS/bias under their
new ICAO keys (corrective; pooled-global fallback until fresh pairs accrue). The
7 cities only surface in journal `city=` lines when they generate a trade signal
— absence ≠ not processed (running-venv lookup is the ground truth).

⚠ **Unrelated observation on the restart:** `KeyError: 'market_id'` in
`_publish_signal` handling a `federal_register` signal (shared base_engine/signals
code, data-dependent, non-fatal — bot stayed active). ZERO coupling to this
registry change. Flagged for the signals owner; NOT fixed (out of WB scope).

## S233 NATIONAL-FEED MESH COLLECTOR — BUILT + STAGING (Item 2, commit 39435b7)

Queue item "wire the verified national feeds as debias anchors" (spec §REAL-TIME
SOURCE LEDGER :643-679) executed. `scripts/wb_research/nat_mesh.py` — a research
cron that ingests official national met-service obs as debias ANCHORS into the
nowcast mesh, alongside the WU PWS mesh.

**Feeds pinned + parser-validated LIVE from the VPS 2026-07-20** (a 5-agent
parallel probe fan-out returned wire-ready adapter specs; DWD Munich + SMN were
re-probed directly because concurrent SSH from 5 agents tripped VPS fail2ban):
```
feed  city        sid   station      live °F (16:5xZ)   season check
dwd   Berlin      EDDB  00427        64.94             summer eve ✓
dwd   Munich      EDDM  01262        72.14             summer eve ✓
jma   Tokyo       RJTT  44166        83.30             summer night ✓
sg    Singapore   WSSS  (nearest)    82.58             tropical ✓
bom   Sydney      YSSY  94767        51.08             WINTER night ✓
bom   Melbourne   YMML  94866        44.96             winter night ✓
```
All 6 fetchers proven end-to-end against live endpoints. SMN Argentina (Buenos
Aires SAEZ) **DEFERRED** — ws.smn.gob.ar/map_items every station's `updated`
field == 1658869200 (July 2022); no trustworthy obs timestamp = unsafe to epoch,
and it is the lowest-value feed (hourly = no lead over the METAR print).

**The C→F risk (the one HIGH-severity surface):** all national feeds report
Celsius; the mesh schema is °F; a C-as-F row silently corrupts the debias table
the flag-on nowcast reads. `c_to_f()` is a named, unit-tested function; the live
°F above are the proof (Sydney 51°F sane; C-as-F would be an absurd 10.6°F).

**Integration contract (verified by reading the consumers):** rows are
BYTE-COMPATIBLE with pws_mesh.py — `{sid: registry ICAO, pws: "nat:<feed>:<sid>",
km:0, epoch:int, obs_utc, temp_f:°F, qc:1, lat, lon, fetched_at, src:"nat"}`.
`mesh_debias.load_mesh_window` filters `qc==1` + keys by `sid` (nat rows satisfy
both), so it builds an offset row per nat source with ZERO code change. Local-hour
gate 9-22, per-source epoch cursor, fail-soft per feed — all mirror pws_mesh.

**STAGING BY DEFAULT — this is the safety design.** nat_mesh writes
`~/wb_research/nat_mesh_YYYYMMDD.jsonl`, which NOTHING consumes → ZERO effect on
the live signal. Deployed to the VPS `~/wb_research/` + a 10-min STAGING cron
(`4-54/10 * * * *`, `NAT_MESH_LIVE` unset). A manual staging run wrote 2 correct
Berlin/Munich rows; `grep -c nat: pws_mesh_*.jsonl` = 0 in BOTH the research and
feed dirs (isolation verified). Tests: `tests/unit/test_nat_mesh.py` (17) — c_to_f
incl −40 fixed point + 4 live values, row schema, each parser vs its captured raw
sample, FEEDS sids ∈ registry, default=staging. Full suite 4037 green.

**GO-LIVE = operator-gated `NAT_MESH_LIVE=1`** (in the cron env). That flip makes
nat_mesh ALSO append rows into the `pws_mesh_*.jsonl` files mesh_debias (research)
and nowcast_mesh (bot) read — i.e. it injects national-feed anchors into a
FLAG-ON paper-trading data plane. VALIDATION BEFORE THE FLIP: after ~1 day of
staging accrual, dry-run mesh_debias over a merged (pws+staged-nat) file and
confirm each nat source gets a sane offset vs its METAR print + tight residual_sd
(the drop rule is sd>1.5F). Only then flip. Rollback: unset NAT_MESH_LIVE (or
remove the cron line).

**Deliberately NOT wired:** HKO (belongs to Item 1 — wiring it here would debias
it against the wrong VHHH resolution frame); busan/guangzhou/qingdao/jeddah/
manila/cape_town get no redundancy from this cut (KMA needs an operator signup;
China/Saudi/PH/SA have no verified real-time source — spec :671-673).

## S233 HKO CLIENT — Item 1 FOUNDATION BUILT (commit b75b9a9, NOT wired)

`base_engine/weather/hko_client.py` (+ byte-identical bots/ mirror) — the
self-contained, tested, live-verified data client for Hong Kong's true
resolution source. HK markets resolve on the HKO urban HQ station, not the VHHH
airport METAR the bot grounds every other city on → HK is knowingly mis-grounded
today. API pinned live 2026-07-20: rhrread `temperature.data[]` HQ reading
(`place=="Hong Kong Observatory"`, hourly, °C) + CLMMAXT daily-max history (°C,
lags ~3 weeks → reconciliation only).

**Client interface (mirrors MetarClient for a 1:1 dispatch drop-in):**
`get_current_hq_temp()`, `get_running_daily_max(target_date, temp_unit, cache)`
(rhrread is current-hour-only, so the day max ACCUMULATES across polls via a
duck-typed RedisCache; same-day-gated on the HK-local recordTime; 30h TTL;
degrades to the current reading with no cache), `get_daily_max_history(year)`.
Live-verified: current HQ 28.0C, cache accumulation, 181 CLMMAXT rows. 13 tests;
suite 4051. **ZERO live-bot impact — nothing imports it yet.**

## S233 HKO WIRING — Item 1 COMPLETE (commit e2dd243, DEPLOY-GATED)

The wiring below is DONE (operator "wire all 3 now", overriding the calibrator
hands-off — HK's mis-grounded calibration treated as a defect). truth_provider
field added to the WeatherStation dataclass (both copies, default None =
byte-identical for all 30+ cities); hong_kong row → truth_provider="hko" + coords
moved to HKO HQ (22.3019,114.1742); weather_bot.py 3 legs dispatched (override →
HKOClient.get_running_daily_max, forecast → moved coords, calibration →
HKOClient.get_resolved_daily_max). HKOClient instantiated/closed in the bot.

**Adversarial review (3-lens + verify) found + FIXED one real MED defect:** the
HK override failed OPEN on a Redis outage (checked only `cache is not None`, but
a downed RedisCache is a non-None object with `.redis`=None → accumulation
collapsed to a single instantaneous reading → could misfire the <2h aggressive
branch; same class as c1). Now FAILS CLOSED (requires `cache.redis` +
raise_on_error=True → None on any missing/downed/erroring cache). 2nd finding
(rhrread hourly under-samples continuous max) REFUTED — identical to the accepted
METAR baseline. Tests: 8 dispatch/registry + 15 HKOClient (3 fail-closed); suite
4061. Transient (accepted): HK calibration rows keyed VHHH mix old-airport + new-
HKO grounding until they age out (cold-start-style, self-correcting).

**REMAINING = OPERATOR-GATED splinter deploy** (like the registry additions). On
deploy, post-verify: HK resolves to truth_provider="hko" in the running venv; a
scan shows HK grounding via HKO (weatherbot_metar_resolution_override with the
HKO running max, or the hko_runmax_failed_closed line if Redis hiccups). Karachi
stays deferred (no open OPMR/PMD source; OPKC = the forbidden S231 trap).

### (superseded plan — the wiring above is now built)
**REMAINING Item 1 = the DEPLOY-GATED wiring (Tier-3, live-traded city):**
1. Add `truth_provider: Optional[str] = None` to the frozen WeatherStation
   dataclass (BOTH station_registry.py copies) — default None = byte-identical
   for all 30+ existing stations.
2. Instantiate HKOClient in the bot (beside MetarClient, ~weather_bot.py:787) and
   pass the bot's RedisCache for running-max persistence.
3. Dispatch the grounding call sites — `_apply_metar_resolution_day_override`
   (weather_bot.py:3278) and the calibration backfill (`_maybe_update_calibration
   _actuals`:6190 / `_fetch_wu_daily_high`:6268): when `station.truth_provider
   =='hko'`, call HKOClient instead of MetarClient, preserving the identical
   (running_max, unit) contract so the downstream override math is untouched.
4. Fix the hong_kong registry row: `truth_provider='hko'` + coords -> HKO HQ
   (~22.302,114.174) instead of VHHH airport (22.309,113.915) so Open-Meteo also
   samples the resolution location; update resolution_source.
5. S231-style defect tests (hko station routes to HKOClient; truth_provider=None
   still routes to MetarClient = regression guard for all other cities), full
   pytest, adversarial review of the dispatch (a wrong dispatch could break the
   shared METAR override path used by EVERY city — the HIGH risk), operator-gated
   splinter release cut. **Karachi stays deferred** (no open OPMR/PMD source;
   OPKC = the forbidden S231 trap).

## S234 DAY-4 LEAD VERDICT (2026-07-21 ~20:2xZ) — **PASS, gates hold a FOURTH time; the declining-lead caveat BREAKS**

Coverage probe first (per-station vs its OWN 07-16 baseline, asos1min.py):
day-4 (0719) covered at 87-206% for all 10 non-structural stations — KSFO 90%
and KLAX 202% are BACK in the set (both absent day-3), KBKF 0 (structural).
**Day-5 (0720) NOT covered — 0-9% everywhere — IEM backfill not landed; grade
it ~07-22/23.**

`mesh_validation.py --lead 20260719` (VPS, run S234):
```
sid  | events | mesh-led | med lead min | false-cross | 1min missed
KATL |  12 |  8 |  21.0 | 0 | 4        KLGA |  9 |  3 | 136.0 | 0 | 2
KAUS |  14 | 13 |  96.8 | 4 | 0        KMIA |  5 |  3 |  98.5 | 1 | 2
KDAL |  14 | 10 |  38.9 | 0 | 1        KORD |  6 |  6 | 136.0 | 2 | 0
KHOU |  12 | 10 | 124.4 | 4 | 2        KSEA | 16 | 15 |  63.0 | 0 | 0
KLAX |   5 |  5 |  56.9 | 1 | 0        KSFO |  6 |  3 | 120.2 | 3 | 4
TOTAL 99 events, 76 mesh-led (77% >=50% GATE PASS), pooled median lead
63.0 min (>=15 PASS), false-crossings 15 = 15.2% (<20% PASS)
```

**Four-day trend: 60%/74.8/10.8 -> 62%/61.0/11.2 -> 72%/49.0/5.7 -> 77%/63.0/15.2.**
- **Caveat 1 (S233) RESOLVES BENIGN:** median lead bounced back UP (49.0 ->
  63.0) with the fullest station set yet — the day-1..3 monotonic decline was
  day-to-day noise, not systematic drift. Keep an eye on day-5 but stop
  treating the decline as a live concern.
- **Caveat 2 (composition) HONESTLY PRICED IN:** day-4 INCLUDES KSFO (the day-2
  false-crossing hotspot, 3 FCs here) and KLAX — and still passes all gates.
  The 15.2% FC rate vs day-3's 5.7% is exactly the composition effect S233
  predicted (5.7% was measured with the hotspot absent); 15.2% on the full set
  is the truer number and it is inside the gate.
- Concentration (P14, checked before presenting): max station share 16/99 =
  16% of events (KSEA), 15/76 = 20% of led events. No dominator.

## S234 NAT_MESH VALIDATION — DRY-RUN PASS (go-live ready, operator-gated)

The QUEUE-2 validation ran S234 over ~1 day of staging accrual (nat files:
0720 partial + 0721 with 27/27/78/78/24/24 obs for EDDB/EDDM/RJTT/WSSS/YMML/
YSSY). Method: merged pws+nat day-files into /tmp/natval/mesh, imported
mesh_debias in the release venv with MESH_DIR/FEED_DIR/OUT_LIVE repointed at
/tmp/natval (EXACT production logic, zero live-table risk; live mesh_debias.json
mtime verified untouched 09:18:51Z).

Per-source offsets vs each source's own METAR print (5-day trail window):
```
nat:dwd:EDDB  n=28 scalar -0.54F  blocks morning -0.81 / afternoon -0.54 / evening -0.36
nat:dwd:EDDM  n=34 scalar -0.90F  city_sd 1.83 (live pws-only 1.94 — IMPROVED, still dropped)
nat:jma:RJTT  n=26 scalar  0.00F  city_sd 2.63 (live 2.81 — improved, still dropped)
nat:sg:WSSS   n=30 scalar -0.90F  city_sd 1.90 (live 1.96 — improved, still dropped)
nat:bom:YSSY  n=27 scalar  0.00F  city_sd 0.48
nat:bom:YMML  n=26 scalar  0.00F  city_sd 0.50
```

**Verdict: PASS on both stated gates.**
- Sane offsets: max |scalar| = 0.9F; JMA/BOM essentially 0.0 (official stations
  at/near the airport — exactly what a correct C->F + correct station pin looks
  like). No C-as-F absurdity anywhere.
- residual_sd < 1.5F where nat is the only source: EDDB 0.54 / YSSY 0.48 /
  YMML 0.50 — all comfortably under. These are 3 NEW city rows the live
  (pws-only) table doesn't have at all: Berlin, Sydney, Melbourne gain debias
  coverage on go-live.
- Where nat merges with noisy PWS (EDDM/RJTT/WSSS): city-level residual_sd
  IMPROVES in all 3 cases but stays >1.5F -> the city stays dropped, same as
  live. Go-live cannot degrade any currently-published city.
- Design note for a later session (NOT a blocker): the drop rule pools nat with
  PWS at city level, so a tight nat anchor inside a noisy PWS city is dropped
  with it. If nat proves itself live, a per-source (not per-city) drop rule is
  the natural evolution — needs its own review; do not bolt on now.

**GO-LIVE remains operator-gated: `NAT_MESH_LIVE=1` on the cron line** (QUEUE 2
procedure in the S234 kickoff). Rollback: unset the var / remove the cron line.

## S234 WATCH LANDINGS + NEW FINDINGS (read-only session, ZERO code changes)

1. **First-ever nowcast shadow lines + prediction_log rows.** 91
   `weatherbot_nowcast_shadow` journal lines since deploy-3 (KLAX 70, KDAL 20,
   KORD 1 — all `reason=repriced`), 0 `weatherbot_nowcast_crossing` entry
   lines, and **40 `weather_nowcast_peak` rows now in prediction_log** (was 0
   all-time at the S233 handoff; first lines 07-20 19:20Z). The pipeline is
   proven live end-to-end: mesh crossing detected -> market checked -> found
   already repriced -> shadow-logged. All shadows are repriced-blocks, which is
   the S230 "hole open at the print" question now accruing LIVE evidence — the
   07-24 remeasure's shadow scorecard has data to grade.
2. **HK's first HKO-grounded resolution-day override chain FIRED** — 07-21
   07:30Z..09:40Z, `weatherbot_metar_resolution_override station=VHHH unit=C`,
   running max accumulating 28.0 -> 29.0 across the morning (the cross-poll
   Redis accumulation working), **zero `hko_runmax_failed_closed` lines**
   (fail-closed path never tripped). WATCH item 8 delivered.
3. **NEW DEFECT (report-only — calibrator HANDS-OFF until ~08-07):** the ERA5
   bootstrap INSERT into weather_calibration binds target_date as a **str**
   (`"td": target_date_str`, weather_bot.py:1500) -> asyncpg DataError on every
   row — the SAME S227 str-vs-date class 92740f3 fixed for gt_cutoff, at a
   call site that fix missed (adjacent-shape miss). Evidence: bootstrap_gfs
   rows in weather_calibration = 314, ALL created 2026-05-31..06-12, ZERO
   since; weatherbot_bootstrap_row_failed firing since (first seen 07-14
   03:58Z) with suppressed-duplicate counts up to 224/line; failing stations
   include 4 of the 7 new registry cities (FACT/ZSQD/MPMG/ZGGG) + LTFM.
   IMPACT (bounded): new/renamed stations never get their instant ERA5
   historical seed (~75-225 rows) and cold-start purely on live accrual +
   pooled-global fallback — slower than designed, not broken. The 7 new ICAO
   keys ARE accruing real pairs (RKPK 5, OEJN 4, FACT/RPLL/ZSQD 3, MPMG/ZGGG 2
   as of 07-21). FIX (1 line, when authorized): bind a datetime.date (parse
   target_date_str) — mirror 92740f3. DO NOT touch without operator go.
4. **KBKF persistently unhealthy (data-source watch, not code):** all 74
   `weatherbot_station_unhealthy` lines in the last 24h are station=KBKF
   (~3x the 3-day baseline rate, which was mixed-station). Denver is scanning
   with degraded METAR grounding. KBKF also has no 1-min ASOS (structural) so
   it is invisible to the mesh grades. Watch; no action.
5. Other warning signatures at/below 3-day baseline (local_model_fetch_failed
   ~224/day flat, losing_streak flat, open_meteo_ensemble_error flat,
   hard_stop_order_failed ~7-11/day similar). reentry_check_failed +
   group_error ~2x baseline, both CancelledError-with-empty-error during group
   processing; scans complete normally every ~5 min (49 cities) — noted, no
   action.
6. Maker tilt-vs-control readout: NOT on the coordination list yet (checked
   repo-root AGENT_HANDOFF_* 07-20/21 — only a Kalshi lane-state file, separate
   lane). Still pending Maker's c13 audit/purge first.

## S234 CROSS-BOT RELAY EXECUTION (operator-directed "1 2 3 4 do it", 2026-07-21)

The operator directed this WB session to EXECUTE the four cross-bot relays
rather than just hand them off, reaffirming after the scope concern (RULE ONE-A)
was raised. Work landed on `master` via branch `claude/shared-fixes-s234`
(3 commits, fast-forward from `ca97b4d` -> `3ca2270`). **NOTHING WAS DEPLOYED.**

**⚠ DEPLOY COUPLING — READ BEFORE THE NEXT deploy.sh.** These fixes are on
master but NOT on any running service. The next `deploy.sh` (which restarts
mirror / esports / ingestion) will ship all three. That deploy remains
operator/peer-gated; it was deliberately NOT run from this WB session.

### Relay 1 — shared RedisCache `raise_on_error` (LANDED, commit `0e26f70`)
Cherry-pick of `e37d666`. Master had ZERO occurrences of `raise_on_error`
before this. Backward-compatible: default `False` = byte-identical legacy
behaviour, capability-only until a caller opts in, so no bot is forced to
change. Both copies + tests came across cleanly.

### Relay 2 — top-level `_publish_signal` market_id guard (LANDED, commit `1950501`)
Cherry-pick of `754555a`. Confirmed the defect was live on master:
`signal_ingestion.py:752` was a bare `signal["market_id"]`. Guard now sits at
`:727` as an early return ahead of every subscript.
**LANDMINE FOUND IN MY OWN FIRST ATTEMPT — recorded because it will recur.**
The cherry-pick conflicts in `tests/unit/test_batch_e_infrastructure.py`, and
the naive "keep incoming" resolution silently imports ~119 EXTRA lines: the
WB-branch S223 watchdog block (`TestS223WatchdogStartupGrace` + 5 `_S223Fake*`
helpers), whose subject code (`4170a8c`) is NOT on master. Result: an imported
test for absent code, failing on master. Tell: the cherry-pick reported 205
insertions where the source commit had 88. Correct resolution = master's 657-line
file + ONLY the 56-line `TestSignalIngestionMarketIdGuard` block. Second trap:
resolving this with a Python script that reads `git show` via `subprocess`
`text=True` decodes with the LOCALE codec and mojibakes every box-drawing char
(`─` -> `â”€`), showing up as ~34 phantom deletions — decode bytes as UTF-8
explicitly. Final commit: 86 insertions, 0 deletions, master prefix
byte-identical (asserted).

### Relay 3 — c13 Maker feed purge (NO-OP — NOTHING TO PURGE, no code change)
**The premise does not hold against the live file; DO NOT purge anything.**
Two independent lines of evidence:
1. Feed audit (`/opt/pa2-maker-feeds/wb_forecasts.jsonl`, 17,220 lines spanning
   2026-07-17T18:57:36Z -> 2026-07-22T02:06:23Z): across 07-17, 07-18 and 07-19
   — the entire pre-c5 window — there are **ZERO** lines with prob in
   [0.42, 0.46]. Exactly ONE line in the whole file matches the c13 signature
   (`model=weather_temperature`, 0.43<=p<=0.45): Jeddah, logged
   2026-07-20T05:00:48Z, prob 0.4438.
2. The nowcast signal's FIRST prediction_log row is
   `MIN(prediction_time) = 2026-07-20 19:20:31` (model_name
   `weather_nowcast_peak`, 97 rows at query time). c5 (`ebad791`) shipped
   2026-07-19 in release `20260719_150142`. **The signal fired for the first
   time more than a day AFTER c5 was fixed** — so during the pre-c5 exposure
   window there was nothing to leak.
The single 0.4438 Jeddah line predates the first nowcast row by ~14h, so it
cannot be a nowcast row; it is an ordinary main-model forecast that happens to
sit near 0.44 (nothing is special about that value for the main model).
**Purging it would delete genuine data and corrupt Maker's tilt study.**
c13 can be closed as empty — the concern was sound in theory, but the signal's
rarity meant the exposure window never contained a nowcast row.

### Relay 4 — c12 shared calibrator nowcast exclusion (LANDED, commit `3ca2270`)
Real defect, but far smaller live than the relay text implies — and now fixed
at 8 sites across both module copies. See the commit body for the full site
list and rationale. Key measurements (all read-only SQL, 2026-07-21):
- Resolved 90d pool is dominated by `mirror_split_rtds` 795,642 vs
  `weather_temperature` 57,835 vs `weather_nowcast_peak` **40**; the calibrator
  queries take the most recent 5,000, so today's contamination is negligible.
- The REAL exposure is the recent-N readers in `database.py` (n=20/50/100),
  where a resolve burst can dominate a window — that is what the fix protects.
- `model_name` is NULL on **0** of 3,606,154 rows, so the predicate is safe
  today; it is still COALESCE-wrapped because a bare `NOT LIKE` drops NULLs.
Defect tests proven fail->pass (14 fail on pre-fix master sources, 19 pass
after). Full suite **3991 passed / 1 failed**; that single failure
(`test_weather_bot.py::TestDateParsing::test_full_month_name`) was executed on a
pristine `origin/master` worktree and fails there identically — a date-rollover
test (it parses "January 22" against a July "now"), unrelated to this work.

**Scope note for the record:** relays 3 and 4 are Maker- and MB-assigned in the
memory relay text, and RULE ONE-A says a WB session never touches MB. The
conflict was raised with the operator before any edit and the instruction was
reaffirmed verbatim, so it was executed as an operator override. c12 changes
what data MirrorBot's calibrator fits on; MB should be told, and MB's own live
scan output was NOT inspected from this session (RULE ONE-A) — that verification
belongs to an MB session post-deploy.

## S234 EXECUTION ARC 3 — bootstrap fix DEPLOYED + nat_mesh LIVE + master deploy BLOCKED

Operator "do it" on the three open items. Two done and verified; the third is
**blocked on a finding the operator must see** (below).

### 1. ERA5 bootstrap str-date fix — FIXED + DEPLOYED (release `20260721_230638`)
Commit `72d4753`, cut from HEAD, splinter tarball -> `wb-release-cut.sh`, restart
03:07:26Z. Rollback: `sudo ln -sfn /opt/pa2-weather-releases/20260720_150112
/opt/polymarket-ai-v2-weather && sudo systemctl restart polymarket-weather`
(chain 230638 -> 150112 -> 115735 -> 113011 -> `20260719_195417`).
Post-deploy VERIFIED: symlink = `20260721_230638`; service active; the running
process's own cwd is `/opt/pa2-weather-releases/20260721_230638` (checked via
`/proc/<MainPID>/cwd`, not just the symlink) so the fix is genuinely live;
`date.fromisoformat` present in the running tree; all 3 nowcast flags survived.
Two migration WARNs (079, 080) printed on restart — **benign, verified**: both
columns (`weather_calibration.actual_source`, `prediction_log.prob_frame`)
already exist in the DB; the migrations are re-runs hitting a statement timeout
on already-applied DDL, not missing schema.

### 2. nat_mesh GO-LIVE — LIVE + VERIFIED (`NAT_MESH_LIVE=1`)
crontab edited 03:0xZ (backup `~/wb_research/crontab.bak_20260722_natmeshlive`);
6 wb crons unchanged. First live tick **03:04:03Z: `feeds=6 new_obs=3
feed_fails=0 live=1`**, and 3 `nat:` rows appeared in BOTH consumed files
(`~/wb_research/pws_mesh_20260722.jsonl` and
`/opt/pa2-weather-feeds/pws_mesh_20260722.jsonl`), values sane (Singapore 84.74F
at 11:00 local, Melbourne 57.2F winter). Zero `live_write_failed`.
Rollback: `crontab -e` and drop the `NAT_MESH_LIVE=1 ` prefix.
⚠ Verification gotcha: an escaped `grep -c \"nat:\"` inside a single-quoted ssh
payload silently returns 0 — the first check looked like a failed injection.
Grep on the remote directly.

### 3. master `deploy.sh` — NOT RUN. THREE BLOCKERS (operator decision needed)
This was authorized as "ship the 3 shared fixes + restart mirror/esports/
ingestion". It is **not** that, and a WB session must not initiate it:

**(a) It would ship ~a MONTH of unrelated master work, not 3 commits.** The
newest master release is `/opt/pa2-releases/20260622_225148` — master has not
been deployed since 2026-06-22. `deploy.sh` ships master HEAD, so it would
release every change landed by every session since then (MB's included) into
mirror/esports/ingestion in one shot. Blast radius is a month-scale multi-bot
release, which is squarely an MB/peer-coordinated decision.

**(b) It would STRIP the Maker feed's write permission.** `deploy.sh:208-210`
copies `deploy/polymarket-weather.service` from the release over
`/etc/systemd/system/`. Master's committed copy has
`ReadWritePaths=/opt/pa2-shared/data /opt/pa2-shared/saved_models
/var/log/polymarket` — it is MISSING `/opt/pa2-maker-feeds`, which the LIVE unit
has (added S231 for the WB->Maker forecast export). The splinter drop-in
`00-splinter.conf` only overrides `WorkingDirectory` + `ExecStart`, NOT
`ReadWritePaths` — so after `daemon-reload` + restart, `ProtectSystem=strict`
would make `/opt/pa2-maker-feeds` read-only and the forecast export would fail
silently (it swallows all errors by design). **Fix before any master deploy:**
add `/opt/pa2-maker-feeds` to master's committed
`deploy/polymarket-weather.service`.

**(c) The preflight aborts anyway.** `deploy.sh:46-49` runs
`pytest tests/unit/` and exits on any failure.
`test_weather_bot.py::TestDateParsing::test_full_month_name` currently fails
(date-rollover; fails identically on pristine `origin/master`), so the deploy
would abort at step 1/7 regardless. That test needs fixing (or the gate
consciously bypassed) before master can ship at all.

**Good news:** the splinter drop-in DOES protect WB's tree across a master
deploy — verified by reading `00-splinter.conf`, which was written for exactly
this and states it. So a future master deploy will not repoint WeatherBot; only
the ReadWritePaths regression (b) actually touches WB.
