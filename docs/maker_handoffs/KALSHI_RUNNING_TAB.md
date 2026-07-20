# KALSHI MAKER — RUNNING TAB (living ledger; append, never overwrite history)

Operator directive 2026-07-20: "cross reference all sectors and rewards and ev to
formulate a proper hierarchy. keep a running tab as well." This file is that tab.
Every number carries its source + method + date. **All capture/EV figures are MODEL
ESTIMATES on real inputs (real books, real public tape, real settlements) unless
marked RECEIPT (real payment). Nothing has traded real money.**

Rules for future sessions:
- APPEND dated entries; never silently revise an old number — post a correction row.
- Pull venue facts from `scripts/maker_research/kalshi_canon.py`, never memory.
- Concentration-check every pooled number before quoting it (Protocol 14).

---

## A. LEDGER (chronological)

| date | event | $ / verdict | source |
|---|---|---|---|
| 07-17 | Recorder arm live (VPS, 5-min oneshot, 120-mkt footprint) | — | handoff §2 |
| 07-18 | Quoter dry-run arm live (10-min oneshot) | — | handoff §2 |
| 07-19 | Demo order plumbing verified (V2 pinned; legacy 410-dead) | 6 PASS / 0 FAIL | verify_kalshi_demo.py run |
| 07-19 | FIRST READOUT (50h, 71,280 samples, 2,729 mkts) | data clean; WC cliff fired; ex-WC base HELD $71.0K→$76.7K/day floor | READOUT_2026-07-19.txt §A/§B |
| 07-19 | GO/NO-GO delivered → **operator GO** (small weather slice) | CONDITIONAL GO | GO_NO_GO_2026-07-19.md |
| 07-19 | post_only cross-block probe built; run INCONCLUSIVE (demo exchange closed, all writes 503) | residual OPEN | branch `1521a15` |
| 07-20 | Trading-P&L leg added (temp series, 1,650 mkts, full sample) | NET **+$19.4K–20.5K** cons (rewards +22.5K, trading −2.0K inwin / −3.1K settle) | kalshi_net_pnl.py full run |
| 07-20 | Temp-market structure decoded: ~$120 pool / ~1h program, hourly churn; $132K program money churned through 1,550 temp mkts in 2d | — | rw/usd_day cross-check |
| 07-20 | Hierarchy model v1 adversarially reviewed → **UNSOUND** (silent tape loss; censoring; unfloored ranking; settle conflation); v1 full run KILLED mid-flight, output discarded | review earned its cost | reviewer agent, 3 criticals + 1 |
| 07-20 | v2 rewritten (all criticals + moderates) → fix-verification: **SOUND-WITH-CAVEATS**; C1/C2/C3 CLOSED, C4/M10 partial (disclosure-grade) | reading rules: trust NETin/cap/d + fill/h; NETset indicative unless set%≈100; excluded>0 in a decision series ⇒ rerun | fix-verifier pass |
| 07-20 | ALL-SECTOR hierarchy v2 full run (2,729 mkts, 0 fetch fails / 0 exclusions) | weather_temp #1 at 14.65 NET/cap/d, ~30x next sector; mentions NETset trap exposed | §C + SECTOR_HIERARCHY_2026-07-20.txt |
| 07-20 | **LIVE PILOT DEPLOYED + RUNNING on the VPS** (weather/temp slice, $40 cap, 10-min timer). Two adversarial review rounds (23+10 agents) gated it: found NO_GO x4 blockers + 1 fail-open regression, ALL fixed; 73 Kalshi tests. First hand-run live cycle placed 6 real orders ($35.45/$40) — but on WRONG markets (temp was between hourly windows so rate-sort grabbed KXDXYDUD/KXLIUKELIMINATION); flattened via kill-switch (validated end-to-end), added a series allowlist (KXTEMP* only), redeployed. Timer live `polymarket-maker-kalshi-live.timer`; currently idle (footprint=0, no active temp programs this window); account FLAT $100. | pilot LIVE, awaiting first temp-window orders | branch HEAD `9dd4ce7`; /opt/pa2-maker-kalshi-live |
| 07-20 | **FIRST LIVE ORDER on Kalshi PROD, from the VPS (operator-authorized: real money + Ireland compliance accepted)** — 1ct non-marketable post_only bid @ $0.05 on KXSILVERH; HTTP 201 accepted, cancelled HTTP 200, `status=canceled`; independent US read-only check: **account FLAT $100.00 / 0 positions / 0 resting** | **WRITE PATH WORKS FROM IRELAND** (Kalshi does not geo-block order placement from the eu-west-1 IP) | vps_trade_test.py run + flat-check |
| 07-20 | Geo-block test (bogus-auth from VPS) + valid-auth balance read from VPS | endpoints auth-gated not geo-gated; authed read HTTP 200 | ssh probes |
| 07-20 | **post_only cross-block probe PASS** (demo reopened): control rested, crossing order REJECTED at HTTP 400 `post only cross` vs EXTERNAL liquidity; post-run verify 0 resting / balance flat $100.0000 | residual **CLOSED** (demo) | verify_kalshi_postonly.py run |

## B. CANONICAL NUMBERS (latest-good; supersede by appending, with date)

| quantity | value | status | method / source (date) |
|---|---|---|---|
| Ex-WC standing pool floor | $71–77K/day | MEASURED | census §B across Jul-19 WC cliff (07-19) |
| Temp farm: rewards capture (120-mkt JOIN footprint, 2d window) | +$22,518 | MODEL | kalshi_net_pnl full 1,650-mkt run (07-20); cross-checked to 0.08% by independent local recompute |
| Temp farm: trading P&L, in-window | −$2,000 cons / −$2,822 opt | MODEL | same run; queue bracketed |
| Temp farm: trading P&L, settle-marked | −$3,135 cons / −$4,624 opt | MODEL | same run; incl. frozen-position artifact −$1,135 |
| Temp farm: NET | +$17.9K to +$20.5K (all 4 assumption combos positive) | MODEL | same run |
| Adverse-selection bite (temp) | 9–21% of reward capture | MODEL | trading÷rewards across the 4 combos |
| Concentration (temp rewards) | top-5 mkts = 1.8%; 923/1,650 mkts earn | MEASURED | local recompute (07-20) — clean, broad-based |
| Temp program structure | ~$120 pool, ~1h window, hourly churn | MEASURED | rw + usd_day fields (07-20) |
| Void rate (temp) | 52.4% of snapshots | MEASURED | samples (07-20) |
| Competition | 83% of first-void mkts never contested; median 5min to flip when contested | MEASURED | READOUT §D (07-19) |
| Maker fee | $0.000000 on sampled temp/WNBA mkts | MEASURED (demo receipt) | demo read-back (07-19) |
| Live pilot config | FOOTPRINT_TOP=40 series=KXTEMP* JOIN_SIZE=20 MAX_MARKET=$15 MAX_TOTAL=$40 WIND_DOWN=20 | DEPLOYED | /opt/pa2-maker-kalshi-live/live.env (07-20) |
| Live pilot safeguards (all prod-verified) | $40 committed-cap binds; series allowlist; STOP-flatten; post_only enforced; Kalshi rejects over-balance; fail-closed on read errors | VERIFIED | 2 review rounds + prod tests (07-20) |
| Kill switch | `flatten_kalshi.py` (openssl-signed) — cancels all resting; validated on real box (6 orders) | VERIFIED | 07-20 |
| VPS (Ireland) live write path | WORKS — order placed+cancelled from eu-west-1, account flat | MEASURED (live prod, real money) | vps_trade_test.py 07-20; **compliance (trade-from-Ireland) = operator-accepted risk, NOT a legal ruling** |
| Prod account | funded $100.00; key id 89314df3-… (demo key was cc784540-…) | MEASURED | prod balance read 07-20 |
| Sector hierarchy | weather_temp #1 (14.65 NET/cap/d, ~30x next); mentions settlement-trap; rest ≤0.30 | MODEL | §C, v2 run (07-20) |

## C. SECTOR HIERARCHY (2026-07-20 v2 run — 2,729 mkts / 162 series / 50h window;
## model SOUND-WITH-CAVEATS per fix-verifier; telemetry: 0 fetch failures, 0 exclusions;
## concentration top-5 = 9.6% in-window / 10.5% settle — clean)

Ranked by in-window NET per $ resting collateral per day (cons queue). Floor:
cap$d≥20 & obs_h≥5 (86/151 series eligible; 65 below-floor series total rew$ ~26 — noise).

| rank | sector | mkts | rew$ | trade$ | NETin$ | NETset$ (set%) | cap$d | NET/cap/d | fill/h |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **weather_temp** | 1,034 | 22,518 | −3,374 | **19,145** | 19,383 (100%) | 1,307 | **14.65** | 111 |
| 2 | mentions | 74 | 2,493 | +302 | 2,795 | **678 (70%)** ⚠ | 5,700 | 0.49 | 22 |
| 3 | climate_env (AQI) | 12 | 395 | −60 | 335 | 391 (100%) | 1,123 | 0.30 | 26 |
| 4 | politics | 18 | 366 | −88 | 279 | n/a (6%) | 1,779 | 0.16 | 3 |
| 5 | econ_prices | 211 | 814 | −108 | 706 | n/a (8%) | 5,113 | 0.14 | 48 |
| 6 | wc_promo (ENDED Jul 19) | 150 | 2,802 | −971 | 1,831 | 2,335 (89%) | 14,181 | 0.13 | 85 |
| 7–9 | entertainment / sports_other / other | 227 | 292 | −53 | 240 | n/a | 4,627 | ≤0.06 | — |

Series-level standouts (full table in SECTOR_HIERARCHY_2026-07-20.txt):
- **Top 5 ranks = the 5 temp cities** (DC 17.7, AUS 16.6, NYC 14.7, CHI 12.3, LAX 12.0
  NET/cap/d; 100% settled; fill/h 74–184 → their loss leg IS sampled, not censored).
- **KXFIGHTMENTION = the trap signature**: NETin +745 but NETset **−1,338** (100% settled)
  — looks profitable while quoting, settlement reveals the adverse selection. Generalize:
  mention-market inventory is toxic AT RESOLUTION; the sector's NETset (678) is a quarter
  of its NETin (2,795).
- KXLATENIGHTMENTION the exception: +1,356 NETset, trading leg POSITIVE — but 12 mkts/2d,
  thin evidence.
- KXWCVIEWERSHIP: churn machine (fill/h 287) and net-negative — avoid-shape.

READING RULES (from the adversarial verifier — binding on any quote from this table):
1. Temp's NET/cap/d ≈ 14.7 is real but NOT a scalable ROI — void-market resting collateral
   is structurally tiny (2¢ bid / 98¢ ask → ~$4 per 100ct pair); the binding constraint is
   POOL SIZE (~$132K churned/2d; we model ~17% capture), not capital. Adding capital does
   not add capture.
2. NETset trustworthy only at set%≈100 (mentions' 70% blends marks — the 678 is indicative).
3. Any future rerun with excluded>0 in a decision-relevant series = incomplete, rerun.
4. All numbers MODEL ESTIMATES (snapshot replay; competitors' response to us unpriced;
   queue = cons shown, opt bracket in sector_hierarchy_20260720_v2.json).

**HIERARCHY VERDICT: the operator's GO slice (weather/temp) is confirmed the #1 corner by
net EV per dollar — by ~30× over the next sector — and it is the ONLY sector where the
loss leg is both well-sampled AND fully settlement-verified. Mentions look tempting
in-window but settlement guts them (FIGHTMENTION −1,338). Nothing else is close to
pilot-worthy at current pool sizes.**

## D. STANDING CAVEATS (apply to every MODEL row above)

1. Snapshot replay: assumes our order present at every 1-Hz LIP snapshot; competitors'
   RE-ACTION to us is not priced (their existing depth IS — join share merges our order
   into the real book, exact CFTC formula).
2. Queue position unknown → always quote the cons/opt bracket, never one side.
3. Observation windows only (~25min per temp mkt; ~2d for long-lived series) — not full
   market life; frozen-position artifact separated out but not eliminated.
4. Figures are for the FULL recorder footprint at 100ct/side (measured concurrent
   collateral: quoter est_capital $1.8–2.9K for a 60-mkt join footprint, journal 07-19),
   NOT the $300 pilot slice. Do not scale linearly — capture is pool-capped, not
   capital-capped (§C reading rule 1).
5. The only numbers that end these caveats are the pilot's own RECEIPTS.

## E. OPEN ITEMS

- ~~post_only cross-block probe~~ **CLOSED 07-20**: PASS on demo vs EXTERNAL liquidity
  (HTTP 400 `post only cross`). Day-1 live: sanity-confirm once on the prod book at min size.
- Operator-only live wall: account + KYC + funding + prod keys + KALSHI_LIVE_ARMED.
- Sep-1 LIP sunset: operator ruling = assume renewal; census = tripwire.
- Maker-fee exception list: enumerate per-series at pilot build.
