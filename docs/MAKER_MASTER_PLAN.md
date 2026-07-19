# MAKER MASTER PLAN

## §0 — NUMBERS RULE (read every time; hardcoded 2026-07-17 after repeated self-contradiction)

**NEVER quote a Maker number from memory or a prior message.** Every quantity
has ONE canonical definition + source in `scripts/maker_research/maker_canon.py`.
To state a number you MUST either (a) run `python scripts/maker_research/maker_canon.py`
(or an in-session live pull), or (b) cite a specific in-session measurement
WITH its method tag. Snapshot share/payout are ESTIMATES (~5x above
time-averaged) — never state them as realized. If a value contradicts an
earlier one, FLAG IT AS A CORRECTION, never slip it in. This rule exists
because min-bet got quoted 4 different ways in one session (median-off-subset,
xprice, xpair) — all wrong except `MIN_BET = rewardsMinSize dollars`.

---

*The single vision/plan anchor for the Maker (market-making) initiative.
Updated 2026-07-17. Every Maker session reads this first; update it when the
picture changes. Naming: "Maker", never "MB" (= MirrorBot), never "MM";
the background processes are RECORDER ARMS, never "sim". Everything is
paper until the operator approves a real-capital pilot.*

## 1. Mission

Decide, with measured evidence, whether and how to run a real-capital
market-making bot on Polymarket. The deliverable is an operator decision
package: config, capital ask, expected return, kill criteria. The operator
decides; nothing trades real money before that.

## 2. The thesis (current-method numbers ONLY — operator directive 2026-07-17:
nothing computed with superseded methods may be quoted from this document)

1. **Trading edge alone ≈ breakeven at best.** Month-long replay, 1,503
   full-lifecycle resolved markets, current methods (fee-filter verified
   1,502/1,503 enabled): only gated+fast was positive (+$1,657/30d floor);
   naive+stale lost $198K.
2. **The subsidy pool is real** — that is ALL the cohort receipts are used
   for: existence proof that Polymarket pays makers at scale (chain-verified
   payments; post-cup re-measure Jul 20+ sizes the post-promo pool). Cohort
   receipts are NEVER an income forecast — those wallets run their own
   algorithms, not ours.
3. **Our expected income comes from OUR OWN measured capture**: three
   independent gated implementations converge at **$1.0–1.1K/day
   model-estimated reward capture** at the 140-market min-size footprint
   (v2 $1,119/day over 1.8d; v3 $1,055/day post-fix; v5-P0 $978/day) —
   share-model approximations documented in §7c; verified only by pilot
   receipts to our own wallet.
4. **Mandatory disciplines**, each priced from our own control-arm losses:
   gates (adverse>2pt = 36–44% of fills in a market's final 16h vs 10–12%
   beyond 48h; 34.1%>1pt overall), sub-second refresh, breadth over size
   (~1% pool share at min-size), auto-redeploy (half the pool base churns
   daily).

## 3. Moving pieces (what is running right now)

| Piece | What | Where |
|---|---|---|
| v1 recorder | naive control — quotes everything, prices every bad habit in $ | `/opt/pa2-maker-sim`, 5-min timer |
| v2 recorder | gated + touch/wide width A/B | `/opt/pa2-maker-sim-v2`, 2-min timer |
| v3 recorder | gated + sub-second WS refresh | `/opt/pa2-maker-sim-v3`, daemon |
| v4 recorder | in-play game lane + classic/split A/B + per-fill rebate meter | `/opt/pa2-maker-sim-v4`, daemon |
| v5 recorder | **GATE LAB** — 7 gate policies paired on identical inputs (P0 baseline / P1 fitted-vol / P2 wind-down ramp / P3 tape-velocity / P4 all / P5 ungated control / **P6 WB-forecast tilt**, added `ca6c1c5`); eras: 01:09:09Z launch `27ba2d7` → 02:36:41Z clean (P0-P5) → **21:55:09Z 07-17 P6 era start** (P0-P5 ledgers carried, verified) | `/opt/pa2-maker-sim-v5`, daemon |
| v6 recorder | **NEGRISK LAB** — netted multi-outcome quoting, paired N0_all (every covered outcome) vs N1_single (flagship baseline); one-winner floor accounting; era start **07-17 22:20:27Z** `1652ae0` | `/opt/pa2-maker-sim-v6`, daemon |
| Sensor feed | **informed-flow tripwire publisher** — bite/stampede/run events (onset-only, anchored) to `/opt/pa2-maker-feeds/informed_flow.jsonl`; 250-mkt arm-union universe; era start **07-18 00:11:02Z** `e712b67`+`1caf921`; VALIDATION-FIRST ≥1wk before any fleet-consumption proposal | `/opt/pa2-maker-sensor`, daemon |
| **LIVE ENGINE (paper)** | the deployable trader — full guard stack (kill/freshness/caps/event-floor/day-floor), py-clob-client-v2 exec core behind triple live-mode interlocks, resolution backfill; gate policy via env (default P0_base until the gate-lab lock); 3× adversarially reviewed, burn-in since 07-19 00:30Z (day-floor kill + HALT persistence validated live 07-19 07:22Z) | `/opt/pa2-maker-live`, daemon `polymarket-maker-live` |
| Pool census | hourly count of every reward pool | `/opt/pa2-maker-census`, hourly timer |
| Backups | nightly 00:20Z tarball + 09:30 local pull (keeps 7) | `/opt/pa2-maker-backups` + operator machine |

Scheduled: **health check Thu 07-17 10:00** (`maker-arms-midwindow-health`),
**READOUT Fri 07-18 10:00** (`maker-sim-readout` — the big one), backup pull
daily 09:30. All on the operator machine's task list.

Kill switches: `sudo touch <dir>/STOP` per arm; `systemctl disable --now <unit>`.

**Data-era discipline:** every code/scope change stamps an era; the readout
reconstructs boundaries from `journalctl … | grep universe:` + the memory
blocks. Do not trust pre-era data without the era's caveat. Major eras:
v3 parser fix 07-16 01:22Z (pre-fix v3 = trade-driven refresh only);
v4 universe steps 01:51Z → 02:03Z → 14:22Z → uncapped ~16:45Z 07-16.

## 4. Decision timeline

| Date | Event | Output |
|---|---|---|
| Jul 17 | automated health check | all-clear or era-stamped anomaly |
| **Jul 18** | **automated READOUT** (4 arms + census + real-cohort refresh) | draft pilot decision package (baseline marked provisional) |
| Jul 19 | World Cup final; promo ends | census records the subsidy cliff |
| Jul 20–31 | post-cup re-measures: census cliff read, `mm_income_weekly.py` with post-promo weeks | cup-vs-meta settled (operator ruled TBD until then) |
| early Aug | `mm_income_monthly.py` re-run for one clean month | final baseline → pilot go/no-go + scale (operator) |

## 5. Pilot shape (current best guess — the readout updates this)

Gated + WS-fast + sports-led + min-size, **plus a farm tier** (breadth
quoting of weather/politics/finance dailies — the strongest reviewed niche)
and auto-redeploy (treadmill). Kill criteria pre-registered in
`docs/MAKER_V4_LANE_TEST_PLAN.md` §5. **Income basis = OUR measured capture
($1.0–1.1K/day at the current footprint, §2.3), scaled by footprint, floored
by the backtest trading floor, and VERIFIED by pilot receipts to our own
wallet within the kill-criteria window. Cohort receipts are a pool-existence
anchor only — never the forecast.** Final numbers from the readout +
post-cup pool re-measure.

### Gate elevation — REFIT numbers (07-17, `acd40ca`; the only quotable set)
Vol gate (clean-era, de-overlapped, 2,141 events): continuation is tail-only —
median ≈ 0, p75 = +2c at 10min / +4c at 30min after a 2c move. Wind-down
(full 613-market mapping, actual resolved_at, side-inferred, censoring
reported): adverse>2pt = 36–44% ANYWHERE in the final 16h vs 10–12% beyond
48h (hot-zone censoring makes these floors) — v5's P2 tests a 9h ramp; a 16h
variant is queued for the next lab round. The v5 lab (6 paired policies,
identical inputs) is the arbiter of each gate's $ value; first read ~07-20.
Tier-3 parked: book-collapse early warning (needs depth logging), catalyst
calendar (GTD orders make it near-free to implement).


## 5b. Algo hardening — assumption -> mechanism map (operator ask 07-17)

| Assumption in the numbers | Algo defense | Calibratable |
|---|---|---|
| Through-fills only (fill model) | **Chain-fills study**: real OrderFilled events -> at-price fill-probability curve | NOW — feasibility CONFIRMED (see below) |
| Share = formula math | Pilot self-calibration loop: predicted share vs next-morning payment, per market, auto-correcting multiplier; + strict per-market qualification (tick/min-size/max-spread fetched live) | harness now, data = pilot day 1 |
| Our presence changes nothing | Anti-landmark: randomized in-band placement, size jitter, market rotation; share-decay telemetry with AUTO-RETREAT per market | design now, calibrate in pilot |
| Marks = profit | Scoreboard counts realized+resolution only; unrealized displayed as risk; kill criteria bind to realized | free, accounting discipline |
| Subsidy persists | Census-driven config: pool gone -> unquote (already); portfolio daily income floor -> auto-halt | wiring now |
| Regime stability | Monthly self-refit of gate params from own fills (v5 lab process productized) | after lab verdict |
| Instruments correct | **Paper twin**: identical-logic recorder beside the live bot; model-vs-receipt divergence alarm | nearly free (arms exist) |

**Chain-fills study — FEASIBLE, decode recipe (probe 07-17, scripts/maker_research/mm_chain_probe.py):**
OrderFilled on both exchanges (0xE111... / 0xe2222...), topic0 = 0xd543adfd945773f1...,
topics[2]=maker, topics[3]=taker, data words = [orderHash-ish, assetId, makerAmt, takerAmt,
fee, ...] /1e6; price = makerAmt/takerAmt (verified vs live trade 0.40 exact). Public RPC
(polygon-bor-rpc.publicnode.com) works from the VPS (403s from residential IP). Study v1:
getLogs over sampled block ranges -> at-price fill volumes at our hypothetical quote levels
+ real-maker-outcome study (who stood where we would stand, what happened to them).
Queued as the next research block (post-readout).

## 6. Niche ledger (reviewed 07-17, data in memory + `scripts/maker_research/`)

| Niche | Status | Next step |
|---|---|---|
| Pure farm (weather/politics dailies) | ✅ real: rewards $3.75/mkt/day floor (weather wide, 60 mkts) | include as pilot farm tier day one |
| Daily treadmill | ✅ real: 27% of pools ($24.6K/day) reset daily; $45–48K/day churns | auto-redeploy = pilot infra (arms already do it) |
| Complement-side quoting | ✅ structural: 58% of books lopsided (median 1.5×) | readout adds per-side competition cut of v4 split data |
| Quiet-hours uptime | ❌ negative: share-by-hour flat (1.5–2×, not 10×) | demoted to sizing detail |
| New-listing latency | ⚠ unanswered: census first-seen is contaminated (discovery wobble) | post-readout: join census vs gamma createdAt |
| Ghost-read other bots | propose-only design (see §7) | MB-alarm backtest v0 runs from OUR data (below) |
| Geopolitical making | demoted (operator) — only toxic sector + fee-free | none |
| negRisk arb | dead (fee-free era over) | none |
| negRisk netted quoting | **v6 NEGRISK LAB live 07-17 22:20:27Z** (`1652ae0`): N0 all-outcome vs N1 flagship, paired; worst$ = one-winner floor (winnable-sibling counts from /events; departed inventory kept in floor) | first read ≥3-5d (~07-21+): N0 rew$/cap$ vs N1 |
| Subjective-settlement markets | size-cap rule adopted from playbook review | encode in pilot config |

## 7. Ghost-reading other bots (propose-only, operator-acknowledged 07-17)

Mechanism: read-only SELECTs on shared DB tables — never their code, runtime,
env, or Redis. Candidates, best first:
1. **Sharp-flow toxicity alarm** — pull quotes when a sharp wallet trades our
   market. IMPORTANT: `users.is_elite` is degraded (force-flagged, 4,430 rows)
   — do NOT use it. **Backtest v0 RAN 07-17 (mm_sharp_alarm_backtest.py):
   INCONCLUSIVE** — top-P&L wallets rarely trade the small rewarded markets
   the arms quote (n=7 overlapping fills at top-100/15min; widening to
   "top-500" polluted the set — only 452 wallets have ≥50 trades and the tail
   is big losers). KEEPER from the run: baseline fill toxicity measured on
   3,334 recorder fills = mean +0.6pt at 30min but **34% suffer >1pt adverse
   (27% >2pt)** — the pick-off tail on our own fills. v1 spec (post-readout,
   ≥5 days of fills): sharps = wallets with pnl ≥ +$50K AND ≥50 trades
   (threshold not top-N), per-sector sets, ±60min window, era-split.
2. **WB forecast → tilted weather quoting — LIVE IN THE LAB (07-17 21:55:09Z,
   `ca6c1c5`)**: WB ACCEPTED (S231; feed flowing at
   `/opt/pa2-maker-feeds/wb_forecasts.jsonl`, their writer deployed
   release 20260717_145326 with kill switch WEATHER_MAKER_FEED_ENABLED).
   v5 P6_tilted = P0 gates + bounded quote tilt (0.5×disagreement, cap 1c,
   post-only). WB semantics encoded: prob=P(YES) never inverted; Hong Kong
   excluded (w=0); cold-start-7 ×0.5 until ~08-01; non-temp models ×0.5;
   cheap-NO guard (no NO-ward tilt on prob<0.20 — WB's biggest-risk caveat).
   P6 rewards score two-sided MIN (adversarial-review fix — the family
   bid-side proxy would have inflated a YES-ward tilt). Paired read ≥3-5d
   (~07-21+), segmented by trust tier from sample-row tilt/wbp/wbw; results
   go back to WB in full per the proposal.
3. **EB match data → sharper in-play gates** — EB lane owns; v4 already uses
   the public gameStartTime field.

## 7b. Blind-review register (2026-07-17, 3 independent reviewers, 58 findings)

**Fixed same-night (`3f4de7d`, v5 redeployed 02:14Z):** daemon crash on
malformed tape timestamp; per-policy competition-score reads (pairing leak);
accrual credit at gate-exit (bias correlated with gating) + restart accrual
normalization + empty-books share=1.0 poison; 1s empty-universe discovery
churn + partial-discovery universe replacement; silent state.json corruption
reset (now logged + .bak chain); disk-cap crash-flap (clean stop, venv
excluded); dead-WS-chunk blindness (hb `nobook=`); hb label; budget 36K;
backup missing v5 + tar live-writer race + per-dir counts.

**Accepted family-wide behaviors (v1–v5 share them; changing v5 alone would
break cross-arm comparability — document, don't diverge):** realized/unreal
split misstated when a fill crosses position through zero (NET is correct —
readouts must use NET, never the real/unreal columns); vol-pull re-arms while
the move stays in the 150s window (effective 600–750s); restart loses the
downtime fill window (correct semantics — no quotes existed); same-second
boundary prints skipped (v4 has the edge-set fix; symmetric across policies);
3-page tape cap undercounts fills in the hottest windows (conservative:
understates ungated bleed, so it biases AGAINST gates); old-gen WS workers
resurrect pruned books ≤40s; no monotonic clock; departed markets keep
frozen marks (report now splits them out).

**Methodology caveats — every fitted number is v0 until refit at readout on
clean-era data:** fit-1 (vol) pooled PRE-fix v3 samples (stale-jump
artifacts inflate move frequency) and uses overlapping windows
(pseudo-replication); fit-2's 257/613 end-date mapping selects the benign
survivor subset AND gamma endDate = scheduled not actual resolution (both
flatten the near-end toxicity ramp → the 9h ramp is if anything TOO LOOSE);
the 34%>1pt fill-toxicity baseline signs adversity by post-fill net position
(attenuates toward zero → true rate likely HIGHER); sharp-alarm v1 spec must
add an out-of-sample split (sharp set selected on a window containing the
eval window = circular), client-side type checks, dedup by txhash, and a
join-rate diagnostic. Income scripts: add txhash dedup + API-failure flags
before any rerun (current numbers safe: every wallet fit in one page, but
the claim is unverifiable from request counts alone).

## 7c. Official-docs fine-print audit (2026-07-17, all 5 market-maker doc pages)

**Exact matches (our canon == docs):** quadratic scoring, complement-book
Q1/Q2 cross-terms, one-sided divisor c=3.0, [0.10,0.90] band rule, daily
00:00 UTC payouts, $1 floor, maker-never-pays, geo fee-free, split/merge/
redeem mechanics, every contract address.

**Fine print we had missed:**
1. Scoring formula contains `b` = in-game multiplier (value unpublished) —
   recorders don't model it → in-play rewards (v4 especially) are FLOORS.
   Census measures the real pool behavior; keep that as the empirical anchor.
2. Scoring midpoint is "size-cutoff-adjusted" (dust at the touch ignored) —
   our raw-touch-mid share estimates are approximations.
3. Rebates only on `feesEnabled: true` markets (fee activation is
   deploy-date-gated) — v4's rebate meter lacks the filter → overstates on
   legacy markets (sibling-session arm; flagged, propose-only). Backtest
   floors unaffected (pools excluded, conservative rates).
4. Normalization epoch = 10,080 samples (7 days of minutes) vs daily pools/
   payouts — our per-day accrual is an approximation that converges for
   continuous presence; open question for intermittent quoting. Real payment
   records unaffected.

**Pilot-relevant mechanics:** tick sizes vary per market (0.0025 on WC
ML/spread/totals; 0.0001 exists; ALWAYS fetch dynamically); GTD orders =
native catalyst-expiry (simplifies the catalyst gate); batch-15 +
cancelAll() kill switch + WS (already adopted). **Combos** = separate RFQ
product (400ms quote window, Exchange V3 `0xe3333700…`, PositionManager,
AutoRedeemer, Last-Look at ~$2.5K notional) — out of scope for pilot v1,
future niche; addresses recorded so "Exchange V3" surprises no one.


**Rerun with new rules (07-17 13:00Z):** feesEnabled fetched for all 208
v4 fee-accruing markets (162 enabled / 46 legacy) → v4 rebate estimate
$141 → **$86 corrected** (naive meter overstated 64%). v4 rewards $823 =
measured floor; ×2.5 in-game sensitivity = $2,058 (b unpublished — always
label). Cascade: the 30d backtest's rebate leg lacked the same filter → the
+$1,657 gated+fast floor may shrink on refit (readout item). Real-payment
cohort numbers need no correction.

## 8. TBD register (open questions, owner, when)

- **Cup vs meta** — operator ruled TBD until cup over → census cliff + post-promo
  re-measures (Jul 20+). Until then: baseline quoted as provisional everywhere.
- **v3 near-zero fills** — genuine freshness signature vs artifact → readout
  (v2-same-market cross-check, era-split at 07-16 01:22Z).
- **Touch vs wide width** — touch earns ~2.5× rewards, wide wins NET so far → readout.
- **Classic vs split inventory** — v4 A/B → readout.
- **New-listing latency** — needs createdAt join → post-readout session.
- **v4 non-game "in-play" semantics** — dailies carry gameStartTime, so v4's
  gate there means "measurement-day"; sector×arm attribution keeps it honest.
- **w-1 REWARD dip** (Jun 4–10 ≈ $1.5K) — unexplained transition week; do not
  anchor anything on it.
- ~~Sept-1 "rewards expire" claim~~ — **DROPPED per operator 2026-07-17** ("pretend
  it doesn't exist"). It never had a primary source across an exhaustive sweep.
  The real, separate risk — the subsidy is discretionary — stays fully tracked
  (§9 risk 1: hourly census + changelog watch); no date attached to it.

## 9. Risks

1. **Subsidy is discretionary** (the business risk). Mitigations: $0-capex
   posture, hourly census, changelog watch, post-cup baseline before scale.
2. **Competition thickness** — ~1% share at min size; breadth is the answer,
   and share-at-size curves need the pilot to measure.
3. **VPS is a single point** — mitigated 07-17: nightly tarball + off-server pull.
4. **Session sprawl** — mitigated: ONE branch (`claude/maker-bot`), this doc
   as anchor, memory pointer, era discipline.
5. **Measurement bugs** — the standing defense: every number needs an
   independent cross-check (the v3 parser bug and the +$6.8K marks error were
   both caught this way; assume more exist).

## 10. Document map (where everything lives)

- **This plan**: `docs/MAKER_MASTER_PLAN.md` on `claude/maker-bot` (the ONLY branch).
- **Evidence log** (full history, all numbers + caveats): memory
  `project_mm_feasibility_study.md`; index line in memory `MEMORY.md` ("Maker ← next").
- **Binding v4 spec + kill criteria**: `docs/MAKER_V4_LANE_TEST_PLAN.md`.
- **Handoffs** (versioned 07-17): `docs/maker_handoffs/`.
- **Research scripts + measured outputs**: `scripts/maker_research/` (README inside).
- **Recorder/census/backup code + units**: `scripts/maker_paper_sim*.py`,
  `scripts/pool_census.py`, `deploy/maker-backup.*`, `deploy/polymarket-*.{service,timer}`.
- **Live engine**: `scripts/maker_live_engine.py` + `tests/test_maker_live_engine.py`
  + `deploy/polymarket-maker-live.service` + `deploy/maker-live-env.example`;
  funded preflight `scripts/maker_preflight.py`; decision anchor = repo-root
  `AGENT_HANDOFF_2026-07-18_MAKER_PILOT_DECISION_PACKAGE_DRAFT.md`, engine
  annex `docs/MAKER_PILOT_GO_NOGO_DRAFT.md`.
- **Scheduled tasks** (operator machine): `maker-sim-readout` (Fri),
  `maker-arms-midwindow-health` (Thu), `maker-data-backup-pull` (daily).
- **Raw data**: VPS `/opt/pa2-maker-*`; nightly tarballs in
  `/opt/pa2-maker-backups` + local `~/.claude/projects/C--lockes-picks-polymarket-ai-v2/maker-backups/`.
