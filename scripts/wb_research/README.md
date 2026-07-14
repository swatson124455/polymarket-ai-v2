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
Caveats: one week, one season, mid prices, thin best-ask depth (3–8 shares on
tail legs) — needs the shadow-book logger before anything trades on it.
