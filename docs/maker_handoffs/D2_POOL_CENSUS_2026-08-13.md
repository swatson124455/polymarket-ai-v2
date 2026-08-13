# D2 POOL CENSUS — venue-wide reward-pool map (READ-ONLY)

**Reads:** programs 2026-08-13T01:01:30Z (single page, limit=10000, next_cursor exhausted); orderbooks (top-100 by pool) 2026-08-13T01:01:31Z → 01:02:56Z, 0.6s spacing via `kalshi_attribution_ledger.get`.
**Canon applied:** daily pool $ = `period_reward/10000` — NO division by window days (R1 canon). All programs `incentive_type=liquidity`.
**Companion JSON:** `D2_POOL_CENSUS_2026-08-13.json` (all 3,591 programs raw + top-100 book metrics + read timestamps). md5 = `4c4a6561613d5adce5fa1d1e4b791fb6`.

## Headline

- **3,591 active programs** = 3,591 distinct markets (1 program per market, no doubles).
- **Total venue daily pool: $250,003.33/day.**
- Per-market pool: median **$50/d**, mean $69.62/d, min $15/d, max $1,000/d.
- **Concentration is FLAT** — the money lives in the tail:
  - top 1% of markets (36): **4.96%** of pool
  - top 10 markets: 2.07% · top 30: 4.39% · top 100: **11.92%**
  - top 10% of markets (359): **30.38%**
  - i.e. ~3x uniform at the top decile, nothing like a power law. Breadth (many $50–$285/d markets) beats chasing the few $1k markets.
- Program `target_size_fp` is almost universally **1000 ct** (EOWEEK 300 ct), `discount_factor_bps=5000` (DF 0.5). W10 canon: a book that never reaches target pays NOBODY — an empty-side book is an unclaimed pool for whoever posts qualifying depth.

## Top 20 series by summed daily pool (2026-08-13T01:01:30Z)

| series | $/day | markets |
|---|---:|---:|
| KXSTATEBALLOTMEASURE | 23,540 | 110 |
| KXVOTEPRIMARY | 8,500 | 71 |
| KXYTVIEWSW | 7,600 | 76 |
| KXROLEATEVENTCOACHELLA | 5,700 | 114 |
| KXFEDFUNDSYEAR | 5,250 | 210 |
| KXYTVIEWSHIGH | 5,100 | 51 |
| KXRAIN | 4,000 | 40 |
| KXLEAGUESCUP | 3,600 | 36 |
| KXNOMGDPGROWTH | 3,575 | 143 |
| KXTRUMPMENTION | 3,500 | 35 |
| KXUSCPIYEAR | 3,250 | 130 |
| KXTRUMPSAY | 3,100 | 31 |
| KXTRUMPSAYMONTH | 2,900 | 29 |
| KXEURUSDAW | 2,400 | 20 |
| KXEOWEEK | 2,333 | 7 |
| KXJOINCLUB | 2,300 | 23 |
| KXH200MS | 2,145 | 143 |
| KXHOODA | 2,100 | 21 |
| KXYTDAILYTOPVIDEOG | 2,100 | 30 |
| KXYTTOPVIDEO2D | 2,100 | 30 |

(⚠ KXTRUMPMENTION / KXTRUMPSAY* = the mention-sweep toxicity class that produced the KXTRUMPTIME drag — pool is real but adverse-selection history is bad; excluded from shortlist.)

## Top-30 markets by daily pool

Full table in JSON (`top30_markets`). Shape: KXMAMDANIEO-26AUG15-T0 and KXGENERICBALLOTVOTEHUB-26AUG14-T6.7 at $1,000/d; 3× KXDROPOUTPRIMARY at $500/d; 7× KXEOWEEK at $333.33/d; then a wall of KXSTATEBALLOTMEASURE strikes at $285/d (all ending 2026-08-16T03:59Z).

## Book quality among top-100 pool markets (read 01:01:31Z–01:02:56Z)

- 94/100 two-sided books, 6 EMPTY-SIDE (5× KXEOWEEK + KXMAMDANIOUT-27JAN01).
- Median book by series (top-100 sample): KXSTATEBALLOTMEASURE 1c spread / ~4,700 ct within 2c of touch (deep+tight = competitive); KXEOWEEK 1.5c / ~980 ct; app-download series (KXGROKAPP/KXFANDUELAPP/KXNFLXAPP) 1c / 4–7k ct; KXDROPOUTPRIMARY 1c / 103k ct (hyper-competitive); KXMAMDANIEO 1c / 75k ct (hyper-competitive despite the $1k pool).
- Easiest-capture flags (BIG pool + WIDE spread + THIN depth): KXGOOGSHARE-GOOG-25 ($240/d, 27c spread, 0 ct within 2c), the empty-side KXEOWEEK strikes ($333/d each), KXSTATEBALLOTMEASURE-AL-A4 ($250/d, 4c, 2 ct).

## 15-candidate shortlist ($1k–3k capital; pool $ / spread / depth-within-2c, all read 2026-08-13T01:01–01:03Z)

Caveats first: (a) EMPTY-SIDE books mean the pool is currently paying nobody IF total depth can't reach target — first qualifying quoter takes ~100% share, but verify the market is actually open/quotable (KXEOWEEK-26AUG01-1/-2 showed 0 levels BOTH sides — possibly closed, check before quoting); (b) most 08-16-ending programs leave only ~3 days of accrual; (c) wide spreads near price boundaries usually mean near-resolved — inventory risk, not free money. All book numbers are one snapshot, UNVERIFIED as stable.

| # | market/series | pool $/d | spread | depth 2c | notes |
|---|---|---:|---:|---:|---|
| 1 | KXEOWEEK-26AUG15-0 | 333.33 | n/a | 0 (NO side empty, 14 yes lvls) | tgt only 300 ct; program to 08-29 |
| 2 | KXEOWEEK-26AUG08-1 | 333.33 | n/a | 0 (NO side empty) | to 08-22 |
| 3 | KXEOWEEK-26AUG08-2 | 333.33 | n/a | 0 (NO side empty) | to 08-22 |
| 4 | KXEOWEEK-26AUG15-1 | 333.33 | 1c | 604 | thinnest two-sided EOWEEK, mid 0.505 |
| 5 | KXEOWEEK-26AUG15-2 | 333.33 | 2c | 1,359 | mid 0.15 |
| 6 | KXGOOGSHARE-GOOG-25 | 240.00 | 27c | 0 | mid 0.435 — nobody near touch; tgt 1000 ct |
| 7 | KXGOOGSHARE-GOOG-26 | 240.00 | 1c | 1,066 | sibling strike, thin |
| 8 | KXGOOGSHARE-GOOG-27 | 240.00 | 1c | 1,164 | sibling strike, thin |
| 9 | KXMAMDANIOUT-26AUG-27JAN01 | 250.00 | n/a | 0 (empty side) | far-dated tail strike |
| 10 | KXSTATEBALLOTMEASURE-AL-A4 | 250.00 | 4c | 2 | near-zero depth at touch; ends 08-16 |
| 11 | KXSTATEBALLOTMEASURE-MD-Q1 | 285.00 | 2c | 478 | thinnest of the $285 ballot wall; ends 08-16 |
| 12 | KXSTATEBALLOTMEASURE-KY-A1 | 285.00 | 2c | 1,166 | ends 08-16 |
| 13 | KXNFLXAPP-26SEP07-T70 | 250.00 | 2c | 1,387 | app-DL series, mid 0.17, longer runway |
| 14 | KXGROKAPP-26SEP07-T60 | 250.00 | 1c | 1,414 | thinnest GROK strike, mid 0.195 |
| 15 | KXGENERICBALLOTVOTEHUB-26AUG14-T6.7 | 1,000.00 | 1c | 1,603 | biggest single pool; tgt 300 ct; competitive but pool/depth ratio still best-in-class; ends 08-14 |

Anti-candidates (big pool, hyper-competitive): KXMAMDANIEO-26AUG15-T0 ($1,000/d, 75k ct at touch), KXDROPOUTPRIMARY strikes ($500/d, 103k ct), KXFANDUELAPP T120 (14.6k ct).

Capital fit: at $1k–3k, rows 1–12 each need ≤ target×price ≈ $150–$500/side to fully qualify; a 5–8 market basket from this list covers ~$2.5–4.5k/day of pool exposure with realistic 20–100% share on the empty/thin books.

*Generated by D2 study session 2026-08-13. Not committed to git.*
