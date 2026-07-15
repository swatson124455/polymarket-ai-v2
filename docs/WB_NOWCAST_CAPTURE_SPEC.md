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
any intraday instantaneous print** (resolution uses the continuous max —
hourly-print watchers are structurally blind). Detection of a crossing is
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

**Remaining before Phase 1 go:** loser-leg EV replay; capture size at the
logged ask (0b); maker-fill evidence (0c). The live data source must be a
PWS mesh (IEM 1-min lags ~42h — backtest-only).

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
