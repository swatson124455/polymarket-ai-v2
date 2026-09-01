# KALSHI RELIGHT SHEET — approved values, NOT applied (2026-09-01 ~13:4xZ)

**Operator rulings (2026-09-01, verbatim ordering of the 5-decision list):**
1. Turn on: **"not now" — bot stays OFF. Nothing below touches the box until an explicit GO.**
2. Picker fix bot-wide: **YES all three** (UPTIME_RANK off, runway rule killed, state gas series allowlisted).
3. Stay-resting posture + values: **YES** (mid-band 0.30,0.70; widebook min spread 5).
4. Capital: $314.57 only — no new money (operator).
5. Nightly credited-dollar reporting, 7×$0 → stop-or-change sheet: **YES.**

## The exact live.env diff to apply at GO (and nothing else)
```
KALSHI_UPTIME_RANK=1                  -> KALSHI_UPTIME_RANK=0
KALSHI_MIN_RUNWAY_H=49                -> KALSHI_MIN_RUNWAY_H=0
KALSHI_MID_BAND_OUT=0.10,0.90        -> KALSHI_MID_BAND_OUT=0.30,0.70
(unset; code default 20)              -> KALSHI_WIDEBOOK_MIN_SPREAD_TICKS=5
KALSHI_SERIES_ALLOW=KXAAAGASD,KXAAAGASW,KXTOPMODEL,KXCLAYTONDNI,KXDIESELW,KXCLARITYVOTE
  -> KALSHI_SERIES_ALLOW=KXAAAGASD,KXAAAGASDCA,KXAAAGASDFL,KXAAAGASDIL,KXAAAGASDNJ,KXAAAGASDNY,KXAAAGASDTX,KXAAAGASW,KXTOPMODEL,KXCLAYTONDNI,KXDIESELW,KXCLARITYVOTE
```
- State series enumerated from the live feed 13:40:18Z (5,822 active programs): exactly CA/FL/IL/NJ/NY/TX exist, 17 programs × $100/d each. CLAYTONDNI/CLARITYVOTE have 0 active programs today; left in the list (no removals).
- Runway kill is safe against late entries: the separate late-life gate (MAX_ENTRY_CUTOFF_MIN=120, quoter:1690/:2280) still blocks entries in a market's final 2h.
- UNCHANGED and restated (already in live.env, operator-seen 2026-09-01): MAX_TOTAL_CAPITAL=290 · MAX_MARKET_CAPITAL=100 · JOIN_SIZE=100 · WIDEBOOK_MAX_CT=100 · INV_SOFT/HARD=30/100 · D3_RUNGS=5,10,25,40,100 · DAILY_LOSS_HALT_USD=10 · REENTRY_COOLDOWN_S=3600 · PER_SERIES_CAP=3 · MAX_DAYS_TO_CLOSE=8 · CAPTURE_GATE=1 @ MIN_USD_DAY=1.00.

## What this config actually does on day 1 (from 13:02–13:10Z book reads)
- Universe: allowlist family = 166 active programs, $17,520/day of pools (119 gas dailies ×$100, 21 gas wkly ×$100, 21 diesel ×$120, 5 topmodel ×$200; feed read 13:40:18Z). 80 of the 104 books pulled today held Target both sides.
- Ranking with UPTIME_RANK=0 = pool dollars: TOPMODEL ($200) first, then DIESELW ($120), then the $100 series; PER_SERIES_CAP=3 and MAX_TOTAL_CAPITAL=290 stop it at ~3–6 markets (~$50–100 committed each).
- Band behavior at the approved values: skewed strikes (refs ≥0.70 / ≤0.30 — most of each daily ladder, T5.58/T5.60, CLAU5 at mid 0.295) quote at touch via the normal path; mid books with spread ≥5c (T5.62 at 15c) rest 2 ticks inside via widebook; **tight near-money dailies (1–4c spread, mid 0.3–0.7, e.g. 4.1100 at 1c) remain excluded** — that fill-protection trade is kept.
- Expected (INFERRED, from the 08-31→09-01 measured anchors scaled linearly): single-digit $ credited per week at this capital; dailies settle nightly at 04:00Z so the first credited-or-not reading is within ~24–48h of GO.

## D5 wiring at GO
Daily 07:30Z: credit_history read (canonical) + est-feed delta; report ONE number — credited dollars, absolute. Seven consecutive days of $0 credited while resting per plan → stop-or-change sheet to operator.

## GO procedure (single reviewed step, when the operator says so)
1. Apply the diff above to /opt/pa2-maker-kalshi-live/live.env (backup .bak-RELIGHT-<ts>).
2. `systemctl enable --now polymarket-maker-kalshi-ws`; verify first cycle: fails=0, quoted list, committed $ ≤ 290.
3. Report the first cycle's footprint + committed dollars to the operator same-day.
