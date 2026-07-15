# WB research harnesses (S229, 2026-07-13) — READ-ONLY market-structure studies

Reusable probes behind the S229 EV scoreboard (see `docs/WEATHER_STATUS.md` OPEN
DECISION 2c). All read-only; primary sources only (CLOB price-history/books, IEM
METAR archive, live Gamma, CLOB-verified resolutions); matched information time.
Terminology: **"ensemble" = the bot's raw INPUT** (GFS/ECMWF member forecasts via
Open-Meteo), NOT the bot. Chain: ensemble → correction layer (EMOS etc.) → bot.

## brier_duel.py — "can signal X beat the market at lead T?"
Runs ON the VPS (imports the release station registry; needs the weather venv).
stdin = `{"markets":[{question,token,resolution,end_date}...],
"forecasts":[{sid,td,ft,members}...]}` (SQL shape in the S229 transcript /
WEATHER_STATUS.md 2c). Picks the stored forecast nearest the target lead
(edit the `timedelta(hours=24)` line for other leads), computes raw-member
bucket probability, fetches the CLOB price at the SAME timestamp, Briers both
against CLOB-verified outcomes.
S229 results: market 0.195 vs raw ensemble 0.243 (n=186, 24h lead);
market 0.191 vs 0.250 (n=230, 8h lead). Re-run with the bot's OWN probabilities
(prediction_log) instead of raw members once the clean window has ≥50 resolved
markets (~2026-07-16/17) — that is the option-5 station-wedge verdict.

## race_study.py — "how much money sits on the table intraday?"
Runs anywhere (pure public APIs). argv[1] = input file: line 1
`{"citymap":{city:{sid,tz,unit}}}`, line 2 = markets JSON array (same SQL as
above). For every resolved US family: IEM METAR running max by local hour vs
CLOB price paths for all buckets. Backtests "buy the current-leader bucket at
local hour H, hold to resolution" INCLUDING losers (no survivorship), plus the
winner reaction curve at first bucket entry.
S229 results (46 families, one summer week): +EV every afternoon hour,
monotone: H=15 +0.037 (68% win, n=31) → H=17 +0.085 (89% win, n=18) per $1 at
mid, pre-costs; winners 0.536 → 0.715 within 15 min → drift to ~1.0 over hours.
NOTE (S230): the nightly accrual updates these — read the latest
`~/wb_research/nightly_*.log` on the VPS, not this snapshot (H=17 had already
thinned to +0.035/n=22 by 07-14).
Caveats: one week, one season, mid prices, thin best-ask depth (3–8 shares on
tail legs) — needs the shadow-book logger before anything trades on it.

## executable_replay.py — "same strategy, but at prices you can actually get"
Runs on the VPS (psql for resolutions; `--res file.json` for offline testing).
Replays buy-the-leader against the shadow-book logger's captured books
(`~/wb_research/shadow_books_*.jsonl`): one simulated entry per
(family-day, local hour) at the earliest tick of that hour, filled at the
LOGGED BEST ASK, graded vs `markets.resolution`. Same EV units as race_study
(profit per share = outcome − price) so mid-vs-executable capture is a direct
subtraction. Also counts 'unbuyable' (leader had no ask) and 'ask>=0.98'
(priced-in) entries — the h17+ mid-edge largely lives there (S230 first pass,
WEATHER_STATUS 2c). Sample accrues at ~11 US family-days/day; hold verdicts to
the same ≥50-per-cell bar as everything else.

## nowcast_skill.py — Phase-0a: how much lead does the 1-min curve give? (S230)
Runs on VPS (needs registry import). IEM 1-min archive vs public instantaneous
prints (METAR+SPECI), per station-day: every rounded-°F increment of the
running max is an event; lead = time until a print reveals it (+6min pub-delay
assumption). S230 RESULT (21d × 12 US stations, 230 station-days, 1,966
events): median lead **58 min**, 85% of events ≥30 min lead; **14% of events
never print intraday**; **78% of days the true daily max never appears in any
intraday print** (⚠ CORRECTED by rep_bias_test.py: resolution tracks the
PRINT world 81%-vs-35%, so hidden peaks are NOT tradeable; the lead matters
only as "know the next print early"). NOTE: IEM 1-min lags ~42h — backtest-only; the live
substitute is a PWS mesh (see docs/WB_NOWCAST_CAPTURE_SPEC.md).

## nowcast_price_path.py — Phase-0b': does the market front-run the print? (S230)
Runs on VPS. For resolved WINNER buckets: t_cross (1-min running max enters
bucket) vs t_reveal (first public print + delay); CLOB minute-price path
averaged aligned on both. S230 RESULT (33 events, winners-only, gap≥8min):
cross-aligned path FLAT (0.46→0.47→0.47 through +15m) — nobody trades the
real-time crossing; reveal-aligned path jumps 0.47→0.68 AT the print and
drifts to 0.85 by +90m. The ~21¢ repricing is fully concentrated at
publication → the hole is open. CAVEAT: winners-only conditioning — strategy
EV needs the loser legs (overshoot) too; that replay is the next step.

## nowcast_entry_ev.py — loser-leg replay: is buy-every-crossing +EV? (S230)
Runs on VPS. Every 1-min crossing entry (winners AND losers) at the CLOB price
at t_cross vs at t_reveal. S230 RESULT (58 family-days, 121 entries): naive
crossing entry is EV-ZERO (+0.008 ± 0.041 at cross; −0.043 at reveal) — only
~33% of crossings are final; the 58-min lead is worth ~+5¢ vs reacting but the
base strategy has no edge → a peak-proximity model is REQUIRED (next harness).

## nowcast_peak_model.py — Phase-0a-ii: THE gate. "is this crossing final?" (S230/S231)
Runs on VPS (venv). Pre-registered FROZEN rule: enter iff E_rem <= 1.0F AND
hour >= 12, date-split, verdict on the TEST half (bar: meanEV >= +0.05 with
2SE excluding 0). S230: 12d/28d/90d runs — picks positive every cut
(+0.074..+0.105 TEST) but 1.2-1.4σ, GATE NOT MET; offline DB history exhausted.
S231: archived Open-Meteo forecasts wired in via the PREVIOUS-RUNS API
(`temperature_2m_previous_day1` = issued day D-1, no lookahead; the
historical-forecast mosaic is shortest-lead = lookahead, NOT used;
historical-ensemble API only reaches 2026-04-13). PRIMARY = DB forecast when
present, archived fills holes; DB-only/ARCH-only cuts + offset diagnostics;
family window keyed on the QUESTION date (NULL end_date_iso no longer drops
families) → 719 family-days 03-01..07-12. Results: `nowcast_peak_133d.out`.

## rep_bias_test.py — which world settles the market? (S230, ROOT CAUSE)
Runs on VPS. Three-way layer diff over 18d × 12 US stations: continuous 1-min
max (C) vs hourly-print max (H) vs WU ground truth vs ensemble median (Fm).
S230 RESULT: resolution lives in the PRINT world (winner bucket contains H 81%
vs C 35%, n=48); WU−H = −0.18 (n=72); C−H = +0.95; forecast layer +0.86F HOT
vs print world → the cheap-NO-tail root cause; EMOS pairs already carry −0.62.

## maker_fill_study.py — historical maker fills at reveal windows (S231, task 2)
Runs on VPS (venv). For each resolved US-F WINNER bucket 03→07: t_reveal =
first hourly METAR print entering the bucket (+6min); full data-api print
history (paginated, deduped, YES-frame: taker SELL-Yes or BUY-No both hit a
resting YES bid via merged-book minting); fills = taker-sell prints <= bid
level in [-30m,+45m]; same-day control window 3.5h earlier. S231 RESULT
(304 reveal windows; months 3/4/5/6/7 = 93/95/9/74/33 — May thin in DB;
median p0 0.68, median repricing +8¢): any-fill 97/95/93/86% at
p0−0/1/2/5¢ (med ~150-200 sh) BUT control 80-87% — books churn two-sided all
day; the reveal-specific signal is POST-reveal-only fills 74/71/65/54%.
ALL UPPER BOUNDS (queue position unknowable; winner-conditioned; wash flow
included). Read: capture is not the blocker — adverse selection when wrong is,
i.e. the peak-model gate decides. Capacity confirmed small (~$100/window UB).

## dayof_cell_scale.py — the 9-12h cell, bot-independent, at scale (S231, task 3)
Runs on VPS (venv). Re-cuts the one surviving S230 cell over ALL resolved
03→07 US-F families with a signal that never touches the bot: P(bucket) =
raw DB ensemble members (latest <=24h before T, no lookahead) FLOORED at the
hourly-METAR running max at T (print world), vs CLOB minute price at
T = local-midnight-EOD − h (4.5/7.5/10.5/18h). Bet-the-disagreement
(|P−price| >= 0.10; 0.05/0.15 sensitivity on the 9-12h cell);
FAMILY-DAY-CLUSTERED SEs. Results: `dayof_cell_133d.out`.

## trade_prints.py — "do resting orders actually get filled?" (maker leg)
Runs on the VPS via cron (`trade_prints.sh`, every 10 min at :05 offset,
alongside shadow_book.sh at :00). For each active US highest-temp family
(station local time >= 10:00): fetches public prints from
`data-api.polymarket.com/trades?market=<condition_id>` for every bucket and
appends NEW ones (per-market timestamp cursor in `.trade_prints_state.json`)
to `~/wb_research/trade_prints_YYYYMMDD.jsonl`. `side` is the TAKER side, so
a print with side=SELL means a resting BID got hit — joined with the shadow
books this prices maker fill-probability at each level. First tick backfills
up to 200 prints/market (dedup-safe). Analysis should still dedup on
(transactionHash, asset, timestamp, price, size).
