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
| 07-20 | **OPTIMALITY AUDIT (9 axes, 19 agents, adversarially self-corrected) → verdict NEEDS_REDESIGN.** THE finding: the bot was STRUCTURALLY NET-NEGATIVE as a naive accumulator — measured −$8/hr adverse-selection+taker-unwind EXCEEDED gross reward (~$5.58/hr competed). Root cause = 1 line discarding the signed position (`held_cost,_ = _held_cost`) → blind to its own delta. Capital is NOT the constraint (~$20 reserved of $100). Verify phase caught its own overstatements: cap/skew PER-TICKER not event-summed (ladder offset = capital, not delta); keep the gross capital brakes; DEMO-validate the net sign-flip before live. **BUILT P0 core (7a27ce9): inventory loop closed + delta-neutral quote shaping (throttle accumulating side, keep reducing side at ref = $0 passive unwind, pull past hard cap, activate=pull-market/void-safe); 17 tests.** | biggest lever DONE; account FLAT + bot STOPPED | audit wf_4e6c203a; branch 7a27ce9 |
| 07-20 | **DELTA-NEUTRAL BOT BACK LIVE (small, $40 cap; branch b880fb1).** Adversarial review = GO_WITH_FIXES; fixed 2 blockers (flatten_to_zero OVERSHOOT on lagging position read → now caps cumulative crossing at |pos0| via confirmed fill_count; selection gate flatness threshold SOFT→TOLERANCE) + 2 cleanups; 23 tests. Deployed md5-verified; first hand-run live cycle = correct two-sided maker quotes on 3 fee-free gas mkts, both sides at ref (flat), committed $34/$40. Timer enabled; monitor watching WARNING/taker-flatten/held>$30/cap-breach. **NOW MEASURING net over a real window = the actual proof of net-positive.** balance $91.93. | back live as a proper maker; scale to $100 after net proven | commits b880fb1; monitor bdoqst4au |
| 07-20 | **DELTA-NEUTRAL REBUILD COMPLETE (branch 1e3aca3): P0 backstop + P1 gate.** Maker-first unwind (grow reducing-side bid at ref = $0 passive flatten; throttle/pull accumulating side). `flatten_to_zero` = SOLE taker path (sign-mapped, self-trade-safe, fail-closed, bounded) fired ONLY by the de-risk pass (settlement<30min OR |pos|>hard-cap, over ALL held decoupled from footprint) or STOP/_flatten_all (now really flattens inventory, was cancel-only). Selection gate skips wide/one-sided books (only when flat). 22 tests, dry-run clean. **Adversarial review RUNNING** (6 areas incl. the taker sign) → then demo mechanics check → live SMALL+measured. Bot STOPPED, account FLAT. | doctrine built: flatten as a maker | wf_266722cf; commits 7a27ce9+1e3aca3 |
| 07-20 | PENDING before any live restart (in order): (a) real flatten on STOP + settlement-flat pass + bounded last-resort taker backstop [P0]; (b) selection toxicity gate + expected-net ranking + get_balance ceiling [P1]; (c) DEMO validation of net sign-flip; (d) adversarial review; then live. | roadmap | audit spec §redesign |
| 07-20 | **OPERATOR DOCTRINE: flat market risk + bag rewards. FLATTENED + timer STOPPED.** Confirmed sell-side mapping with a 1-ct empirical test (ask sells yes, position 8->7) BEFORE flattening at size. Closed all 7 positions (4 gas daily + 1 gas weekly + 2 temp) via IOC taker crosses. **HONEST RESULT: account flat (0 pos / 0 resting), balance $91.93 = NET −$8.07** (realized −$6.85 + taker fees −$0.94), **$0 rewards credited yet.** KEY FINDING: the naive bot ACCUMULATES inventory (adversely selected as takers dumped YES into our bids on the one-sided gas drift), and flattening as taker costs spread+fee. It is NOT a delta-neutral maker. Timer disabled; account safe/idle. **LAUNCHED full 9-axis optimality audit** (delta-neutrality, DF-pricing, sizing, passive-unwind, flow-balanced selection, ladder-margin capital eff, void-presence, wind-down-to-flat, cadence) → redesign spec pending. | −$8/hr as naive accumulator = the anti-pattern; redesign to true delta-neutral | flatten via IOC; audit wf_4e6c203a |
| 07-20 | **CORRECTION x2 (append-only per tab rules):** (1) The "MARGIN DISCOVERY / ladder netting ~$20.17 reserved" entry below was a MISREAD — the balance drop was the CASH COST OF FILLS already executing (fill costs at that instant = $20.17 exactly), not netted resting margin. Resting orders do NOT visibly deduct from balance_dollars. (2) My "0 fills" reports were WRONG: prod positions field is `position_fp` (string) not `position` — the deployed _held_cost + my verify scripts read the wrong key and were blind to real inventory. FIXED same hour: quoter+flatten patched (position_fp w/ fallback), prod-shape test added (12/12), redeployed md5-verified; quoter now reports held=$ correctly and went conservative (committed>cap -> no new creates). Maker fee $0 now RECEIPT-GRADE (fees_paid_dollars=0.000000 on real fills). | first fills: 5 maker fills 17:45-17:54Z; inventory = gas ladder straddle | fills/positions API |
| 07-20 | **FIRST REAL-SHARE MEASUREMENT + CAP RAISED TO $100 (operator: "max capital is 100")**. Measured our LIVE share with the verified CFTC formula on books containing our REAL orders: at $40 cap, 3 gas mkts, share 4-9%, earning **$33.83/day-equiv on $31.89** committed. Raised MAX_TOTAL_CAPITAL to 100 (= balance; exchange backstop coincides) → **9 gas mkts (7 daily + 2 weekly), 17 orders, $86.95/100 committed (our gross measure), real earning rate $133.74/day-equiv**; all in-allowlist, 0 fills. **MARGIN DISCOVERY: Kalshi nets ladder-event margin — balance shows only ~$20.17 reserved for $86.95 gross resting** (event-level cross-strike netting; our committed measure is conservative by ~4×, safe direction). 1 create_fail (benign; likely post_only cross on a book move; count-only telemetry). Instant readings — share drifts as competitors requote. | earning rate 4×'d; receipt test = balance credit after daily-gas program end 03:59Z (Kalshi payout timing UNVERIFIED — 00:00Z was the Polymarket schedule, do not assume) | env change only; monitor3 watches thru ~04:40Z |
| 07-20 | **PILOT WIDENED to fee-free non-toxic sectors + FIRST REAL INTENDED ORDERS** (operator: "does it have to be weather?" → add fee-free sectors). Verified KXAAAGASD (daily gas) + KXAAAGASW (weekly gas): settlement-confirmed non-toxic (NETset +392/+537 @100% settled) AND maker_fees_dollars=0.000000 (fee-free) via prod order read-back. Added to allowlist (temp5 + gas2). Held KXAQICITY (non-toxic but not active/unverifiable now); skipped mentions (FIGHTMENTION trap). Pilot placed 6 real maker quotes on 3 KXAAAGASD markets, committed $31.89/$40, all in-allowlist, account balance intact. | pilot ACTIVELY TRADING fee-free gas (+temp when its windows reopen) | env allowlist update, no code change |
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
| Fee-free verified series | KXTEMP* (weather), KXAAAGASD/KXAAAGASW (gas) — maker_fees=0 via read-back; non-toxic (settlement-verified). AVOID: mentions (FIGHTMENTION +745in/-1338set trap). HOLD: KXAQICITY (non-toxic, fee-unverified/inactive) | VERIFIED | 07-20 |
| Live pilot config (UPDATED 07-20) | MAX_TOTAL_CAPITAL=**100**; FOOTPRINT_TOP=40 series=KXTEMP*+gas JOIN_SIZE=20 MAX_MARKET=$15 MAX_TOTAL=$40 WIND_DOWN=20 | DEPLOYED | /opt/pa2-maker-kalshi-live/live.env (07-20) |
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

## F. DELTA-NEUTRALITY REBUILD — 2026-07-20 (post-"multiple stances" + triple-blind)

**Trigger:** operator saw the bot holding multiple correlated stances in ONE gas event
(`KXAAAGASD-26JUL21`: −20 on the 4.010 strike + dust on 4.020/4.025/4.030, aggregate ~−20
short). Root cause (triple-blind, unanimous): the capital gates + inventory shaping were
POLARITY-BLIND and PER-TICKER-ONLY. Each strike sat under the SOFT throttle individually,
so the aggregate directional short accumulated unguarded; and the committed-capital cap
blocked ALL creates (incl. the only de-risking order) once held inventory > cap → the bot
got STUCK holding inventory it could not unwind.

**8 fixes implemented in `probe/maker_kalshi_quoter.py` (main loop) + tests:**

| # | Fix | What changed |
|---|-----|--------------|
| A | Delta-aware capital gate | Reducing ('unwind'-tagged) creates processed FIRST and NEVER blocked by `MAX_TOTAL_CAPITAL`, `cap_desired`, `bound_creates`, or a failed-cancel defer. A risk-reducing order cannot over-commit. |
| B | Per-EVENT aggregate delta | `event_deltas(held_by)` sums signed net across a nested-threshold event (`ticker.split('-')[:2]`). It LOWERS the throttle trigger (`max(|inv|,|event|)`) so correlated strikes each under SOFT still throttle. Direction stays per-ticker (drives each ticker to flat); event supplies direction only when flat on the ticker. |
| C | Real held value | `_held_cost` now reads `market_exposure_dollars` (actual reserved cost) instead of `|pos|×$1` (was ~28× over-conservative → tripped the cap at a fraction of real capital and deadlocked the unwind). **Verified live: displayed $50 → real $1.81.** |
| D | Void-branch reducing side | An ACTIVATE (thin) market where we carry inventory now rests ONLY the reducing side (passive maker unwind) instead of blanket-returning [] (which froze the position + made the taker unreachable). |
| E | Stranded-inventory unwind | Inventory on a ticker dropped from the footprint (program near-end / usd_day fell off) now gets its own passive maker unwind quote, not left unmanaged until the taker backstop. |
| F | Wind-down keeps reducing side | The wind_down gate no longer abandons an open position into resolution — it keeps resting the reducing side until the settlement taker backstop takes over. |
| G | Taker IOC status guard | `flatten_to_zero` cancels any order the venue returns still-open after an IOC (defends against a venue not honoring IOC → naked non-post_only taker order lingering). |
| H | `JOIN_SIZE ≤ INV_HARD_CT` | Clamp resting join size to the hard cap so a single fill can't overshoot the shapeable [SOFT,HARD] band before the next cycle reacts. |

**Verification:** 29/29 tests pass (`test_live_hardening.py`), incl. new cases for the
event-aggregate throttle, delta-aware cap exemption, strand unwind, and polarity-aware
cap_desired/bound_creates. Adversarial dry-run smoke clean (places cycle-1, quiesces
cycle-2 → new `reason` tags cause no diff churn; zero forbidden calls). Self-reviewed (no
sub-agent review — monthly spend limit). Corrected a design flaw found during self-review:
throttle DIRECTION must be per-ticker (drives each ticker flat), event only lowers the
trigger + supplies direction when flat — an event-driven direction conflicted with a
ticker's own position.

**Account state at close (read-only, cover bids untouched):** balance $90.03; 4 nonzero
positions all in `KXAAAGASD-26JUL21` (aggregate −20 short, real exposure $1.81); 1 resting
maker cover bid (yes @ 4.010) clearing the −20 to flat. Bot STOPPED (timer disabled).

**REMAINING:** operator-gated live restart (three-lock live wall unchanged). Bot stays
STOPPED until the operator arms the restart.

## G. LIVE GO — 2026-07-21 00:15 UTC (delta-neutral rebuild shipped)

**Sequence (operator authorized "gtg to trade", ceiling 90):**
1. Targeted adversarial review (plan B) run inline on fixes A + B. **Caught 1 must-fix:**
   **A1 overshoot** — `_unwind_size` floored the reducing-side count at a full capped-join,
   which can exceed |inv|; on a full fill that crosses THROUGH flat and opens the opposite
   position. FIXED: cap at |inv| (`max(1, min(round(|inv|), room))`), regression test added.
   30/30 tests pass. Full multi-agent adversarial pass TABLED for handoff (spend limit).
   Lower-sev notes (documented, not fixed): A3 reducing-bid can be balance-rejected when
   collateral near-exhausted (fails safe); B2 event-aggregate additive-correlation holds ONLY
   for threshold ("above X") ladders = TRIPWIRE on widening; B6 taker HARD trigger is
   per-ticker (event aggregate relies on per-ticker settlement taker + maker throttle).
2. Deployed quoter to VPS md5-verified (`fb9cac8c…`), backup `…bak-20260721_001143`, ceiling
   `MAX_TOTAL_CAPITAL` 40→90, venv import OK.
3. One manual live cycle → held **$1.81** (was fake $50.74), **skipped 0** (was 6 = unstuck),
   quoted 8, committed $75.64/90. Compared vs pre-deploy cycles = fixes confirmed on real money.
4. Timer enabled `--now`, 10-min cadence confirmed (NEXT 00:25:50). Auto cycle #2 clean.
5. **Live delta check:** per-event net = KXTEMPAUSH **−2 ct** (~flat, gross $27), KXAAAGASD
   **−20 ct** (unwinding, <SOFT). Both under SOFT → throttle armed, not needed. Delta-neutral
   confirmed on live money. Balance $62.65 free (rest = Kalshi ~4.3× margin-netted collateral).

**LIVE STATE:** trading, ceiling $90, 7-series temp+gas slice, delta-neutral, 10-min timer.
Kill: `sudo systemctl disable --now polymarket-maker-kalshi-live.timer`; flatten: STOP sentinel
or `flatten_kalshi.py`.

## H. WIDENING SPEC (item 4 — for when you want to grow the slice)

Census of ACTIVE LIP 2026-07-21 (2001 programs, 151 series):
- **TEMP: nothing to add today** — only 5 cities have active programs (AUS/CHI/DC/LAX/NYC),
  all already in the allowlist. Temp is the #1 EV corner (~30×); re-census and add new cities
  the moment they get programs — always first choice.
- **GAS candidate: `KXAAAGASM`** (monthly, 54 mkts, $5,400 pool = 5.4× daily gas). Same
  threshold-ladder structure as GASD/GASW → event-aggregate SAFE. This is the recommended
  slight widening available now. Verify it's "above X" laddered (it is by series family) then
  append to `KALSHI_SERIES_ALLOW`.
- **Sizing knob:** `MAX_MARKET_CAPITAL` $15→$20-25 rests deeper per market, but capture is
  POOL-capped not capital-capped (diminishing). Ceiling $90 ≈ the $100 funded max — raise
  ceiling only with more funding.
- **DO NOT widen to non-threshold series** (mutually-exclusive / range markets, the other 146
  series) without extending the correlation model first — the event-aggregate throttle assumes
  additive "above X" correlation (review finding B2). A candidate/range series would sum
  anti-correlated strikes as if additive → mis-fire. Per-series correlation check required.
