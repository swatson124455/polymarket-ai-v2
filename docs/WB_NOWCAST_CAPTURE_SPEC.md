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

**90d FINAL (all available history — offline route EXHAUSTED): GATE NOT MET.**
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
