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
