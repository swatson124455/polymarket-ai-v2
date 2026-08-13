# D3 — Adverse-Selection Measurement (Kalshi maker lane)

Generated 2026-08-13T01:03:16Z (venue reads 00:56–01:10Z). READ-ONLY study — file reads + GET only.
Companion data: `D3_ADVERSE_SELECTION_2026-08-13.json` (md5 `4b56d880840b1c1f1fffb7d360507b10`).
Box state at read time: STOP present (operator wind-down 2026-08-12T19:50:13Z); tapes end there.

**BASIS (label on every number below):**
- **SETTLEMENT basis**: per fill, `edge = (terminal value of the ACQUIRED outcome − fill price of that outcome) × contracts`. Terminal = $1 if `market_result` == acquired side else $0 (from `/portfolio/settlements`, 203 rows). This marks every filled contract to terminal even if later offset — maker rows and taker rows (our exits) therefore sum coherently per series. LIP credits are EXCLUDED — this is trading-flow toxicity only. Fees reported separately (maker fills carry $0.00 fee on all 908 rows; canon §M10).
- **MARK basis (+5m/+30m)**: yes-basis mark from the two inventoried tapes (samples mid, quotes y_ref), same edge formula against the mark instead of terminal. Partial coverage — denominators stated.
- Fill direction/price/count via the receipt-verified `kalshi_attribution_ledger` helpers (book_side bid→yes, ask→no; `*_dollars`/`*_fp` fields).

**Denominators:** 1,630 lifetime fills; 1,454 on settled markets (176 on still-open markets skipped); of the settled: 908 maker fills / 11,356.95 contracts, 546 taker fills / 5,844.18 contracts. Market meta (open/close) resolved 227/227 tickers.

---

## (a) Data inventory — what price-over-time actually exists on the box

| Source | Content | Price field | Cadence | Coverage | Verdict for D3/D4 |
|---|---|---|---|---|---|
| `/opt/pa2-maker-kalshi-live/quotes-YYYYMMDD.jsonl(.gz)` | Per-cycle per-SELECTED-market quoter rows: ts, ticker, y_ref/n_ref (yes/no reference price from qualifying-walk book read), y_lowq, book depth $, our px/ct/score/share, gates | `y_ref` (yes basis) | ~41 s cycles, only the active selection (~34 markets/cycle; 265 tickers, 53,842 rows on 08-12) | 07-29→08-02 (gz), 08-04→08-09, 08-12. **Gaps: 08-03, 08-10, 08-11 and every halt window** (tape stops with the quoter; ends 08-12T19:50Z) | Right universe, wrong availability |
| `/opt/pa2-maker-kalshi/samples-YYYYMMDD.jsonl.gz` (recorder arm) | ts, ticker, **yb/ya yes touch**, tick, vol24, LIP fields | mid=(yb+ya)/2 | ~5 min per tracked market; footprint = top-100 LIP-pool markets + 20 random (~120/day; 34,560 rows on 08-12) | **07-30→08-13 GAPLESS** (independent of quoter); KEEP_DAYS=14 prune; older only in `/opt/pa2-maker-backups/maker-data-*.tar.gz` (census back to 07-16) | Gapless, wrong universe (pool-ranked, not our-fill markets) |
| `caprank-*.jsonl` | Selection/ranking snapshots (selected list + variants) | none | per cycle | same as quotes | NOT a price tape |
| `plans-*.jsonl` | Per-cycle counters (conditional keys 106–183) | none | per cycle | same as quotes | No per-market prices |
| `ws_daemon_log.jsonl` | Ops events (`cold_cycle`, book_ws counts) | none | — | 07-27→08-12 | **Books live in memory only — never persisted** |
| `cash-202608.jsonl` / `ledger-202608.jsonl` | Account snapshots (15 min / hourly); `resting_raw` = OUR order prices only | own orders only | 15m/1h | Aug | Not market prices |
| `estimates-202608.jsonl` | Per-program reward_centicents | none | hourly | 08-06→08-13 | Reward calibration, not prices |
| Venue API | `/portfolio/fills` (1,630), `/portfolio/settlements` (203 → terminal price), `/markets?tickers=` (open/close) | terminal | on demand | always | Settlement basis always computable |

**Mark coverage of the 908 settled maker fills** (nearest-row tolerance ±150 s for +5m, ±600 s for +30m, ±300 s at-fill): at-fill 381 (42.0%), +5m 308 (33.9%), +30m 311 (34.3%). Two-thirds of our maker fills have NO intermediate mark — this is the D4 gap.

## (b) Settlement-basis adverse selection (per contract, fees separate)

| Role | n fills | contracts | total edge | edge/contract | fees |
|---|---|---|---|---|---|
| **Maker** | 908 | 11,356.95 | **−$543.47** | **−$0.0478** | $0.00 |
| Taker (mostly our exits) | 546 | 5,844.18 | +$61.26 | +$0.0105 | $60.08 |

Maker fills lose ~4.8 c/contract by settlement DESPITE the half-spread already embedded in the fill price. Taker flow is ~breakeven gross and ~flat net of fees (+$1.18 total). Sum −$542.29 ≈ lifetime trading loss on settled markets (LIP credits excluded) — consistent in sign/magnitude with the account picture (INFERRED cross-check only, not a bot_pnl claim).

**Mark basis (covered subset only):** +5m −$0.0276/ct (308 fills, 4,504.69 ct); +30m −$0.0246/ct (311 fills, 4,829.89 ct). ~55–60% of the eventual settlement edge is already marked against us within 5 minutes → the flow that hits our quotes is INFORMED at fill time, not random drift that later resolves against us.

**Worst maker series by total settlement edge** (n≥10 fills unless noted): KXTRUMPENDORSEMENTS −$151.50 (−28.9 c/ct, 523.65 ct), KXAAAGASD −$125.85 (−2.9 c/ct, 4,277.6 ct — small toxicity × huge volume), KXAPRPOTUS −$62.59 (−27.7 c/ct), KXTRUMPTIME −$46.57 (−10.0 c/ct), KXDXYDUD −$42.59 (−9.3 c/ct), KXEURUSDAW −$38.53 (−13.1 c/ct), KXTEMPCHIH −$30.58 (−10.5 c/ct), KXRAIN −$26.88 (−11.1 c/ct).
**Maker-positive outliers are illusory once exits are added** (combined maker+taker, net of fees): KXMUSKNW +$117.47 maker but −$143.19 taker → **−$33.92 combined**; KXACTBLUETOP +$53.21 / −$56.96 → −$5.14. Full combined table in the JSON (`by_series_combined_settlement`).

**By time-to-close at fill (maker):** <1h −7.6 c/ct (129 fills, 1,448 ct); 1–6h −3.3 c (138/1,435); 6–24h −3.6 c (372/4,005); 1–3d −8.7 c (141/2,446); >3d −1.5 c (128/2,022). Toxicity is bimodal: the last hour AND the 1–3 d window (where TRUMPENDORSEMENTS/APRPOTUS-style event risk lives).
**By UTC hour (maker; small buckets — INFERRED, not load-bearing):** worst 17Z −24.3 c (36 fills), 23Z −20.1 c, 08Z −20.4 c (7 fills), 03Z −15.4 c, 14–15Z −10.8/−13.0 c; positive 22Z +25.1 c, 00Z +5.1 c, 16Z +7.0 c. US-afternoon news hours (14–17Z) are consistently negative on decent samples.

## (c) Survivable vs toxic for a mid-quoter (settlement basis, maker fills)

Survivability test: a mid-quoter's only trading income is ~half-spread (1 tick ≈ 1 c on most of these books) plus LIP credits; per-contract settlement edge already NETS the half-spread, so **any series worse than about −1 c/ct is trading-negative and must be carried by rewards; worse than ~−5 c/ct is unrecoverable at current reward density** (lifetime LIP $189.06 vs lifetime maker adverse −$543.47: rewards cover ~35% of the adverse drag — ESTABLISHED from credit_history canon + this study).

- **Survivable (|edge| ≤ ~3 c/ct, volume-bearing):** KXAAAGASD −2.9 c (4,278 ct), KXAAAGASW −3.5 c, KXTOPMODEL −2.1 c (732 ct), KXCLAYTONDNI −0.3 c, KXDIESELW −0.7 c, KXUSDJPY +0.3 c, KXCLARITYVOTE −1.0 c. Slow-moving mechanical/administrative series. Note even "survivable" AAAGASD is the single largest combined dollar loser (−$91.60 net incl. taker exits) purely on volume — size discipline, not exclusion, is the lever (drips-are-fine rule).
- **Toxic (≤ −9 c/ct — informed/event flow):** KXTRUMPENDORSEMENTS −28.9 c, KXAPRPOTUS −27.7 c, KXADJOURNRECESS −38.9 c (4 fills), KXINXHUD −43.8 c (3 fills), KXCHINAAI −15.1 c, KXEURUSDAW −13.1 c, KXBLANCHEWITHDRAW −13.2 c, KXMLABELSHARE −11.3 c, KXGENERICBALLOTVOTEHUB −11.8 c, KXRAIN −11.1 c, KXTEMPCHIH/LAX −10.5 c, KXTRUMPTIME −10.0 c, KXDXYDUD −9.3 c. Pattern: political-announcement and threshold-index (FX/index ladder near the strike) series, plus weather at resolution proximity.
- **Timing:** avoid resting through 14–17Z news hours in event series; the <1h-to-close window is toxic in dailies (−7.6 c/ct) — the existing preclose logic under-protects.

## (d) What D4's recorder must add (missing today)

1. **A quoter-independent per-QUOTED-market touch tape**: yes bid/ask (+ our own top-of-book flag) for every market we quote or hold, cadence ≤60 s, running through halts. Today the right-universe tape (quotes-*) dies with the quoter and covers only in-selection cycles → 66% of maker fills have no +5m mark. The recorder-arm samples tape proves the pattern (unauthenticated public GETs, gapless) — D4 = same arm, footprint driven by our quote/position set instead of pool rank.
2. **Book depth at touch ±3 ticks** at the same cadence (ws daemon already holds books in memory — persist a 1-line snapshot per market per cycle instead of discarding).
3. **Public trades tape** (`/markets/trades`) for quoted tickers — needed to classify aggressor size/burstiness of the flow that fills us (this study cannot distinguish one 100-lot sweep from 20 drips).
4. **Market meta at record time** (open/close/strike distance) so age-at-fill and strike-proximity cuts don't need after-the-fact API joins.
5. **Retention**: KEEP_DAYS=14 prunes the samples tape; D4 tape must gzip-rotate into `/opt/pa2-maker-backups` like census does, or the next D3 rerun loses its marks again.

*(Not committed to git per task instructions. All numbers above are from the 2026-08-13T01:03Z venue pull + on-box tapes; bases as labeled.)*
