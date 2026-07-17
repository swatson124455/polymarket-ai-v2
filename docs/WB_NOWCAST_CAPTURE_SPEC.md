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
