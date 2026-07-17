# KALSHI WEATHER OVERLAP — PROPOSE-ONLY MAP (Maker splinter → Maker main lane + WB)

2026-07-18 (splinter session, Kalshi lane). **PROPOSAL ONLY.** Nothing here touches WB
code, WB data, or any runtime. WB has veto on anything involving their models (standing
rule). Consume alongside the Kalshi feasibility handoff
(`AGENT_HANDOFF_2026-07-17_KALSHI_FEASIBILITY.md`, repo root, gitignored) and
`scripts/maker_research/kalshi_canon.py`.

## What Kalshi has (measured 2026-07-17, public API)

**289 Climate-and-Weather series** (`GET /series?category=Climate and Weather`). The
maker-relevant classes:

| Class | Series (examples) | Cadence |
|---|---|---|
| **Hourly directional temp** | KXTEMPNYCH / KXTEMPCHIH / KXTEMPDCH / KXTEMPLAXH / KXTEMPAUSH (+KXHIGHNYD) | hourly cycles, ~10-20 strikes each |
| **Daily max temp** | HIGHNY, KXHIGHCHI, KXHIGHTDAL, KXHIGHTNOLA, KXHIGHTDC, KXHIGHTLV, KXDVHIGH (Death Valley) | daily |
| **Daily min temp** | KXLOWNY/KXLOWNYC, KXLOWTAUS, KXLOWTMIA, KXLOWTDEN/KXLOWDEN, KXLOWTDC | daily |
| Rain / snow | KXRAINDALM, KXRAINLAXM, KXBOSSNOWM, KXNYCSNOWXMAS | monthly/custom |
| Hurricanes / AQI / climate | KXHURRICANE family, KXFIRSTHURRICANE, KXAQICITY, KXGTEMP … | custom |

City coverage for temp markets: **NYC, Chicago, DC, LA, Austin (hourly + daily), Dallas,
New Orleans, Las Vegas, Miami, Denver, Boston(snow), Death Valley** — US-only so far
(WB's global directive doesn't conflict; this is an additional venue, not a city filter).

## The subsidy shape (point-in-time + first recorder ticks; recorder census is the living record)

- At the 21:35Z active-programs pull: weather = **$4,500 of $273,535** standing scheduled
  rewards (1.6%) across 10 series — SMALL as a standing pool. BUT the temp programs are
  **short hourly-cycle windows** ($20-$500 each) that CHURN — the standing sum understates
  daily throughput exactly like Polymarket's treadmill class (27% of pools there). The
  recorder's hourly census will integrate true daily weather throughput within ~2 days.
- First recorder ticks (22:10-23:47Z, n=1-30 snapshots — NOT quotable capture): the five
  hourly-temp series ran **void rates 50-78%** (no two-sided Target-Size liquidity =
  nobody home), activate-capital ~$77-324/market, and where two-sided books existed our
  hypothetical 100ct JOIN took 60-88% of the qualifying score. Weather books on Kalshi
  are, at first look, nearly uncontested farm territory — the exact opposite of their
  WC/MLB books.

## Why WB matters here (the proposal)

Neutral quoting earns the subsidy but eats adverse selection on temp resolution moves.
WB has calibrated peak-temp models + a PWS mesh (Phase 1 live). The identical shape to
the existing Maker→WB forecast-tilt proposal (repo root,
`AGENT_HANDOFF_2026-07-17_MAKER_WB_FORECAST_TILT_PROPOSAL.md`), second venue:

1. **Tilted quoting**: center Kalshi temp quotes on WB's forecast distribution instead of
   the (often absent) book mid; skew size toward the model-favored side.
2. **Wind-down gating**: WB's nowcast vs strike distance = a principled pull-quotes
   signal near settlement (Kalshi hourly temps settle FAST — hourly cycles).
3. **Strike-ladder coverage**: models give a full temperature CDF → quote every strike
   with consistent relative pricing (Kalshi lists 10-20 strikes/city/cycle).

**Asks (WB decides, no action taken):**
- Does the existing shard-drop contract (`/opt/pa2-maker-feeds/wb_forecasts.jsonl`,
  being built for the Polymarket tilt test) cover NYC/CHI/DC/LA/AUS/DAL/NOLA/LV/MIA/DEN
  hourly + daily-peak horizons? If yes, the SAME feed serves both venues — zero extra WB
  work beyond confirming city/horizon coverage.
- Are Kalshi's settlement sources (NWS station obs per market rules — verify per-series
  `rules_primary` before any build) compatible with what WB's mesh predicts? (WB judges.)

## What the Maker lane should do with this (sequenced, all propose-only)

1. Let the Kalshi recorder run — its census integrates true weather-pool daily
   throughput and its samples measure competition arrival (~Jul 20-21 readout).
2. If capture + throughput hold up AND the operator opens the account lane: a Kalshi
   weather farm tier is the natural first pilot slice (small capital: activating all 5
   hourly-temp cities looked like ~$0.5-1.5K at first-tick prices — single-snapshot
   figures, re-measure at readout).
3. Tilt integration only AFTER the Polymarket tilt A/B reads out (don't double-build).

## Guard rails respected
Read-only public API (this doc consumed ~5 requests: 1 series catalog + reuse of the
21:35Z programs pull); no WB reads beyond the two Maker-lane handoff docs already
addressed to Maker; no VPS changes; numbers are point-in-time or first-tick teasers,
labeled, with the recorder as the source of quotable figures.
