# MAKER MASTER PLAN
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

## 2. The thesis (what the evidence says so far)

1. **Trading edge alone ≈ breakeven at best.** Month-long replay over 1,590
   resolved markets: only gated+fast quoting was positive (+$1,657/30d floor);
   naive+stale lost $198K. Live recorder arms confirm the same shape daily.
2. **The subsidy layer is the actual profit.** Real makers verifiably
   collected ~$513K/30d (payment records, chain-verified). Our 66-wallet
   cohort's steady-state (Apr+May, pre-World-Cup) ≈ **$160–185K/month** —
   **PROVISIONAL** per operator ruling until post-cup re-measurement.
3. **Mandatory disciplines**, each priced from our own control-arm losses:
   gates (in-play/settled/vol), sub-second refresh, breadth over size
   (~1% pool share at min-size), auto-redeploy (half the pool base churns
   daily).

## 3. Moving pieces (what is running right now)

| Piece | What | Where |
|---|---|---|
| v1 recorder | naive control — quotes everything, prices every bad habit in $ | `/opt/pa2-maker-sim`, 5-min timer |
| v2 recorder | gated + touch/wide width A/B | `/opt/pa2-maker-sim-v2`, 2-min timer |
| v3 recorder | gated + sub-second WS refresh | `/opt/pa2-maker-sim-v3`, daemon |
| v4 recorder | in-play game lane + classic/split A/B + per-fill rebate meter | `/opt/pa2-maker-sim-v4`, daemon |
| v5 recorder | **GATE LAB** — 6 gate policies paired on identical inputs (P0 baseline / P1 fitted-vol / P2 wind-down ramp / P3 tape-velocity / P4 all / P5 ungated control); era start 07-17 01:09:09Z, commit `27ba2d7` | `/opt/pa2-maker-sim-v5`, daemon |
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
`docs/MAKER_V4_LANE_TEST_PLAN.md` §5. Capital ask, expected floor (backtest)
and income basis (real cohort per-$ rates, provisional baseline) get final
numbers from the readout + post-cup re-measures.

### Gate elevation (07-17): fitted parameters + the v5 lab
Fit 1 (vol gate, 21K events): continuation after a 2-min move is TAIL-driven —
median further move ≈ 0 but p75 = +4c at 10min / +7c at 30min after a 2c move;
grows with move size. Fit 2 (wind-down, 1,450 fills w/ end dates): adverse>2pt
peaks 20–31% in the final 1–9h of a market vs ~4% at 1–3 days. Both encoded as
v5 policies; the lab measures each gate idea and combination against an
ungated paired control on identical markets/prints. First read: ≥3 days of
lab data (~07-20). Tier-3 candidates still parked: book-collapse early
warning (needs depth logging), catalyst calendar.

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
2. **WB forecast → single-sided weather quoting** — highest-value nuance;
   needs WB handoff (their forecast table schema) + operator sign-off.
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
- **Scheduled tasks** (operator machine): `maker-sim-readout` (Fri),
  `maker-arms-midwindow-health` (Thu), `maker-data-backup-pull` (daily).
- **Raw data**: VPS `/opt/pa2-maker-*`; nightly tarballs in
  `/opt/pa2-maker-backups` + local `~/.claude/projects/C--lockes-picks-polymarket-ai-v2/maker-backups/`.
