export const meta = {
  name: 'kalshi-scope-widening',
  description: 'Measure what it would take to widen the Kalshi maker scope safely, under a live state freeze (sandbox only)',
  phases: [
    { title: 'Instrument', detail: 'fix measurement bias, decompose the equity move, profile the drought' },
    { title: 'Diligence', detail: 'per-candidate-series: structure, fees, toxicity, two-sided persistence' },
    { title: 'Verify', detail: 'adversarial refutation of each candidate verdict' },
    { title: 'Synthesise', detail: 'widening proposal with numbers and open blockers' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
YOU ARE WORKING IN THE KALSHI MAKER LANE. Worktree: ${WT}
Use absolute paths. Bash cwd drifts to another checkout — always cd "${WT}" first or use git -C.

=== HARD CONSTRAINTS (violating any of these is a failure) ===
1. A STATE FREEZE is in force (docs/maker_handoffs/KALSHI_FREEZE_2026-07-23T0219Z.md).
   READ-ONLY ONLY. NO ssh writes to the VPS. NO config/live.env changes. NO deploys.
   NO order placement. NO systemctl. You may SSH only to READ (sudo cat / tail / md5sum).
   Public Kalshi API reads (api.elections.kalshi.com/trade-api/v2) are fine — no keys needed.
2. Kalshi venue ONLY. Do NOT touch MB/WB/EB/SB code, shared modules, or other bots.
3. Do not modify committed sandbox datasets (concentration_samples.jsonl is FROZEN at
   md5 e920bf99850279099897a79e8ad78dec and committed numbers refer to it). Write NEW files.
4. Rate-limit public API calls to >= 0.3s spacing. Paginate with next_cursor.

=== CANON — read this FIRST, it is quoted from the CFTC filing ===
docs/maker_handoffs/KALSHI_LIP_RULE_CANON.md
  §T  TERMS. sector(ours) > series(192) > event(344, ONE RISK) > market/contract(2547,
      the order book + reward pot + $1 floor) > program(reward plan). Say CONTRACT when
      precision matters. "market" = ONE binary contract, NOT a marketplace.
  §R1 period_reward is the TOTAL for the Time Period, NOT a rate. Windows range ~13h..698h.
      ALWAYS normalise to $/day. Two $100 pools can be 12x apart per day.
  §R2 $1.00 minimum is a THRESHOLD on the WHOLE Time Period payout, then rounded down.
  §R3 two-sided exclusion is MARKET-level (the BOOK must reach Target Size on EACH side),
      NOT participant-level. An excluded snapshot pays NOBODY.
  §R4 score = DF^N * size, normalised per side; user snapshot score = norm_yes + norm_no;
      payout fraction = mean over snapshots of (ys+ns)/2.
  §M5 series scan results. §M6 overnight drought + a selection bias.

=== MEASUREMENT DISCIPLINE (earned the hard way this lane) ===
- MEASURE BEFORE CLAIMING. If a number looks impossible, IT IS WRONG — say so, find the bug,
  do not explain it away. Three confident numbers already died this way, plus three of MY OWN
  bugs today (capital->size conversion, degenerate ranking, missing R3 in the series scan).
- NEVER quote the attribution ledger's rewards_residual. It is GARBAGE (printed +$57.82/day,
  then +$75.17/day — impossible on this account size). Three fixes were tested and refuted.
  The ONLY trustworthy rewards method is clean-interval (zero fills, zero settlements,
  book unchanged), currently +$6.58 over 2.0h = a FLOOR at ~6% window coverage.
- Every result states its SAMPLE SIZE and explicitly WHAT IT DOES NOT COVER.
- Reward-side modelling CANNOT see fill rate / adverse selection (no queue position). Say so.
- Watch for SELECTION BIAS: kalshi_concentration_study.py:133 SKIPS contracts with an empty
  book side, which made an "86.1% two-sided" figure conditional. Do not repeat that.
Return DATA, not prose. Be specific, cite file:line and exact figures.
`

phase('Instrument')

const instrument = await parallel([
  () => agent(`${RULES}

TASK — UNBIASED RE-MEASUREMENT. The committed concentration study has a selection bias
(canon §M6b): kalshi_live/kalshi_concentration_study.py:133 does "if not yl or not nl: continue",
so contracts with an empty book side never entered the dataset. That made §M2's "86.1% two-sided"
conditional, and §M1's capture figures overstate the real contract population.

DO:
1. Write a NEW script kalshi_live/kalshi_unbiased_sample.py (do not edit the frozen study) that
   samples ALL in-allowlist contracts with active programs INCLUDING ones with an empty side,
   recording raw books + start/end/pool/target/df. Sample ~8 minutes.
2. Report the UNCONDITIONAL rates: two-sided %, empty-side %, depth<target %, overall and per event.
3. Re-run the §M1 concentration K-sweep on the unbiased data and report how much the capture
   figures move vs the committed conditional ones (oracle best K=6 $99.43/day; as-is K=7 $84.83/day).
4. State plainly whether §M1's "optimum is mid-range K~6-7" conclusion SURVIVES unbiased sampling.

RETURN: the script path, unconditional rates, the K-sweep table, and a SURVIVES/CHANGES verdict
with the numbers that justify it.`,
    { label: 'unbiased-resample', phase: 'Instrument' }),

  () => agent(`${RULES}

TASK — DECOMPOSE A LIVE EQUITY MOVE, RIGOROUSLY. Between ~03:14Z and ~03:35Z on 2026-07-23 the
account went from balance $38.97 / held $25.94 (equity ~$64.91, 8 positions) to balance $84.36 /
held ~$1.00 (equity ~$85.36, 3 positions). The KXAAAGASD-26JUL23 gas-daily contracts disappeared.
equity_day_start = $63.341146.

The hypothesis is that the gas-daily EVENT settled and this is settlement inflow, NOT rewards.
It must be decomposed properly because the operator currently believes the bot is "killing it"
and may scale up on that read.

DO (READ-ONLY; the account keys live on the VPS, you may run READ-only scripts there via
sudo -u polymarket /opt/pa2-maker-kalshi-live/venv/bin/python <abs path> — do NOT write):
1. Enumerate settlements and fills in the window. Look for a settlements endpoint, /portfolio/
   settlements or market_result rows, and the fills API. kalshi_live/ has read-only helpers —
   read them first (kalshi_status_readonly.py, kalshi_delta_check.py, kalshi_attribution_ledger.py).
2. Attribute the equity change into: (a) settlement of resolved contracts, (b) realised trading
   P&L on those contracts, (c) rewards credited, (d) unexplained residual.
3. State how much of the move is CONFIRMED by receipts vs INFERRED.
4. Assess the LOSS-METER implication: the daily halt is equity-based off equity_day_start=$63.34
   with a $40 quota, so a settlement inflow raises equity and widens the effective loss room.
   Quantify the new effective room and say whether that is a hazard.

CONSTRAINT: do NOT quote rewards_residual as a rewards figure. If you use it at all, label it
as the known-garbage diagnostic it is.

RETURN: the attribution breakdown with confirmed-vs-inferred marked, the loss-meter room number,
and a one-line verdict on whether "the bot is killing it" is SUPPORTED, UNSUPPORTED, or UNKNOWN
on the evidence available.`,
    { label: 'equity-decompose', phase: 'Instrument' }),

  () => agent(`${RULES}

TASK — IS THE TWO-SIDED DROUGHT DIURNAL? Canon §M6 measured at 03:14Z that only 8/39 in-allowlist
contracts had books reaching Target Size on BOTH sides; 28/39 had one side COMPLETELY EMPTY.
At 02:25-02:48Z the (conditionally-sampled) rate looked far healthier. This decides whether a
scope widening pays at all: a series whose books are only two-sided during US waking hours is
worth a fraction of its headline pool, because R3 excludes every one-sided snapshot and it pays
NOBODY.

DO:
1. Build kalshi_live/kalshi_twosided_profile.py — for a set of series, sample the market-level
   two-sided rate (REC.qualifying_walk on each side vs target) repeatedly and bucket by UTC hour.
2. You cannot wait 24h. So instead ESTABLISH THE METHOD and take a real reading now across a
   broad series set (our allowlist + the §M5 candidates: KXINTC, KXPM, KXRT, KXFUNDRAISING,
   KXCLAUDE, KXEARNINGSMENTIONLMT, KXEARNINGSMENTIONAXP), and state clearly that a full diurnal
   profile needs a longer run — leave the collector runnable and documented.
3. KEY COMPARATIVE QUESTION: at THIS hour, which series still have two-sided books? A series that
   stays two-sided overnight is strictly more valuable to us than one that dies, because our bot
   runs 24/7 on a 2-minute cadence. Rank the candidates by CURRENT two-sided rate.
4. Cross-check against §M5's reported two_sided_pct for the same series and flag any disagreement.

RETURN: script path, the per-series two-sided table at this hour, the ranking, disagreements
with §M5, and an explicit statement of what a single reading cannot establish.`,
    { label: 'drought-profile', phase: 'Instrument' }),
])

log(`instrument phase done: ${instrument.filter(Boolean).length}/3 lanes returned`)

phase('Diligence')

const CANDIDATES = [
  { s: 'KXINTC', note: 'numeric threshold ladder, $1.98/contract/day, 100% two-sided in §M5' },
  { s: 'KXPM', note: 'numeric threshold ladder, $1.46/contract/day, 100% two-sided' },
  { s: 'KXRT', note: '70 programs, $0.69/contract/day, scraps-at-scale, threshold ladder' },
  { s: 'KXFUNDRAISING', note: '86 programs, $0.59/contract/day; §M5 heuristic MIS-FLAGGED it as categorical, the A-prefixed suffix (A145000000) is really a threshold' },
  { s: 'KXCLAUDE', note: 'best per-contract in §M5 at $12.21/day but only 4 programs, date-nested structure, n=4 — likely too thin' },
  { s: 'KXEARNINGSMENTIONLMT', note: '$7.42/contract/day, 100% two-sided, BUT mention family = known settlement trap' },
  { s: 'KXAAAGASM', note: 'monthly gas, $255/day pool (rank 39). The §H recommendation was WITHDRAWN as a unit error — re-check on $/day' },
]

const diligence = await pipeline(
  CANDIDATES,
  (c) => agent(`${RULES}

TASK — FULL DILIGENCE ON ONE SERIES: ${c.s}
Context from canon §M5: ${c.note}

A widening decision needs FOUR things established. Do all four and be explicit where you cannot.

1. STRUCTURE (decides whether our risk math is even valid).
   Enumerate the series' events and contracts from /markets?series_ticker= or the programs list.
   Classify: THRESHOLD LADDER (nested "above X", strikes ADDITIVE within an event) vs CATEGORICAL
   / MUTUALLY EXCLUSIVE (named outcomes, ANTI-correlated). The event-aggregate delta throttle
   (maker_kalshi_quoter.py, event_deltas / "-".join(ticker.split("-")[:2])) assumes ADDITIVE.
   A categorical series would make it MIS-FIRE. The §M5 classifier was a crude numeric-suffix
   heuristic that mis-flags prefixed numerics — do this PROPERLY by reading actual tickers and
   the market titles/rules from the API.

2. MAKER FEES (a HARD BLOCKER — a fee can swallow the entire reward).
   Only KXTEMP*, KXAAAGASD, KXAAAGASW are fee-verified $0 by prod read-back. Determine what can
   be established for ${c.s} WITHOUT trading: check /markets/<ticker> fields for fee hints
   (maker_fee, fee_waiver, etc.), Kalshi's published fee schedule, and any per-series exception
   list. If it CANNOT be settled without placing an order, say so plainly and state exactly what
   probe would settle it.

3. TOXICITY / ADVERSE SELECTION.
   The mention family is a known settlement trap (FIGHTMENTION +745 in-window / -1338 settled):
   markets that look fine while open and gut you at settlement. Assess ${c.s} for the same shape.
   Useful signals: does price gap hard at resolution; is there an information event (earnings,
   a release, a scheduled announcement) that arrives as one-way informed flow; spread/depth
   asymmetry. scripts/maker_kalshi_recorder.py has the settlement-analysis machinery — read it
   and reuse rather than reinventing.

4. TWO-SIDED PERSISTENCE + $/DAY.
   Re-measure this series' achievable capture at the deployed shape (join 20ct/side, $15/contract)
   with R1 ($/day normalisation), R2 ($1 threshold per Time Period) and R3 (two-sided exclusion
   FIRST) all applied. Sample more contracts than §M5's n=4 if the series has them.

RETURN a verdict object: series, structure classification + evidence, fee status
(VERIFIED_FREE / UNKNOWN / CHARGES / needs-probe-X), toxicity assessment + evidence,
$/day per contract and for the series, two-sided rate, sample size, and an overall
ADMIT / REJECT / NEEDS-PROBE recommendation with the single biggest reason.`,
    { label: `diligence:${c.s}`, phase: 'Diligence', schema: {
      type: 'object',
      required: ['series', 'structure', 'fee_status', 'toxicity', 'two_sided_pct', 'usd_per_day_per_contract', 'sample_size', 'recommendation', 'biggest_reason'],
      properties: {
        series: { type: 'string' },
        structure: { type: 'string', enum: ['THRESHOLD_LADDER', 'CATEGORICAL', 'MIXED', 'UNKNOWN'] },
        structure_evidence: { type: 'string' },
        fee_status: { type: 'string', enum: ['VERIFIED_FREE', 'UNKNOWN', 'CHARGES', 'NEEDS_PROBE'] },
        fee_evidence: { type: 'string' },
        toxicity: { type: 'string', enum: ['LOW', 'MEDIUM', 'HIGH', 'UNMEASURED'] },
        toxicity_evidence: { type: 'string' },
        two_sided_pct: { type: 'number' },
        usd_per_day_per_contract: { type: 'number' },
        usd_per_day_series: { type: 'number' },
        sample_size: { type: 'integer' },
        recommendation: { type: 'string', enum: ['ADMIT', 'REJECT', 'NEEDS_PROBE'] },
        biggest_reason: { type: 'string' },
        not_covered: { type: 'string' },
      },
    } }),

  (verdict, c) => verdict && verdict.recommendation !== 'REJECT'
    ? parallel(['structure-and-risk-math', 'fee-and-cost', 'toxicity-and-settlement'].map(lens => () =>
        agent(`${RULES}

TASK — ADVERSARIALLY REFUTE a widening verdict. Lens: ${lens}.
DEFAULT TO REFUTED IF UNCERTAIN. Your job is to find the reason this is WRONG, not to agree.

The claim: series ${c.s} should be ${verdict.recommendation}.
  structure: ${verdict.structure} — ${verdict.structure_evidence || 'n/a'}
  fees: ${verdict.fee_status} — ${verdict.fee_evidence || 'n/a'}
  toxicity: ${verdict.toxicity} — ${verdict.toxicity_evidence || 'n/a'}
  two-sided ${verdict.two_sided_pct}%, $${verdict.usd_per_day_per_contract}/contract/day, n=${verdict.sample_size}

Through your lens specifically, attack it:
- structure-and-risk-math: would the event-aggregate throttle mis-fire here? Are strikes really
  additive? Check the actual resolution rules, not the ticker shape. What happens to the ladder
  self-hedge (+low/-high pairing) on this structure? Could we accumulate correlated exposure the
  guards cannot see?
- fee-and-cost: is the fee claim actually established or assumed from silence? What is the
  worst-case cost side — adverse fills, the 1-2c/pair unwind bleed, taker exposure at settlement?
  Would the reward survive a realistic cost load?
- toxicity-and-settlement: is there an information event that makes flow one-way? Model the
  FIGHTMENTION shape (fine in-window, gutted at settlement). What is the evidence this series is
  NOT that? Absence of evidence is not evidence of absence — say so if that is all there is.

Also check: is the sample size big enough to support the claim at all? n<10 contracts at one
instant supports almost nothing.

RETURN: refuted true/false, severity, the specific defect, and what measurement would settle it.`,
          { label: `refute:${c.s}:${lens}`, phase: 'Verify', schema: {
            type: 'object',
            required: ['refuted', 'severity', 'defect', 'settling_measurement'],
            properties: {
              refuted: { type: 'boolean' },
              severity: { type: 'string', enum: ['BLOCKER', 'HIGH', 'MEDIUM', 'LOW', 'NONE'] },
              defect: { type: 'string' },
              settling_measurement: { type: 'string' },
            },
          } })))
        .then(votes => ({ verdict, votes: votes.filter(Boolean) }))
    : { verdict, votes: [] },
)

const scored = diligence.filter(Boolean).map(d => {
  const v = d.verdict
  const votes = d.votes || []
  const refuted = votes.filter(x => x.refuted).length
  const blockers = votes.filter(x => x.severity === 'BLOCKER' || x.severity === 'HIGH')
  return {
    series: v && v.series,
    original: v && v.recommendation,
    refuted_count: refuted,
    total_votes: votes.length,
    survives: v && v.recommendation === 'ADMIT' && refuted < 2 && blockers.length === 0,
    blockers: blockers.map(b => `${b.severity}: ${b.defect}`),
    settling: votes.map(x => x.settling_measurement).filter(Boolean),
    verdict: v,
  }
})

log(`diligence+verify done: ${scored.length} series, ${scored.filter(s => s.survives).length} survive refutation`)

phase('Synthesise')

const synthesis = await agent(`${RULES}

TASK — SYNTHESISE A SCOPE-WIDENING PROPOSAL. The operator asked: how do we leverage the new data
and slightly open the scope of what we trade? A STATE FREEZE is in force, so the deliverable is a
PROPOSAL WITH MEASURED NUMBERS AND OPEN BLOCKERS — nothing gets deployed.

The operator also believes the bot is "killing it". Check that against the equity-decomposition
lane below and, if it is not supported, say so plainly and early. Do not let a widening
recommendation rest on an unverified profitability read.

=== INSTRUMENT PHASE ===
UNBIASED RE-SAMPLE:
${JSON.stringify(instrument[0], null, 1)}

EQUITY DECOMPOSITION:
${JSON.stringify(instrument[1], null, 1)}

TWO-SIDED / DROUGHT PROFILE:
${JSON.stringify(instrument[2], null, 1)}

=== CANDIDATE DILIGENCE, AFTER ADVERSARIAL REFUTATION ===
${JSON.stringify(scored, null, 1)}

WRITE the proposal as markdown, structured:
1. DOES THE PREMISE HOLD? Is "killing it" supported? What IS verified about earning vs bleeding.
   Lead with this. Be honest and brief.
2. WHAT THE NEW DATA CHANGES. Did §M1's conclusion survive unbiased sampling? What did the
   drought profile establish, and what does it NOT establish?
3. WIDENING SLATE. Per surviving candidate: series, structure, $/day, two-sided rate, sample size,
   and the ONE thing that must be settled before admitting it. Order by confidence, not by size.
   If NOTHING survives, say that — a null result is a real result here.
4. BLOCKERS THAT APPLY TO EVERYTHING (fees unverified, toxicity unmeasured, reward measurement
   is a floor only, sample sizes small, single point in time).
5. THE SEQUENCED PLAN: what to measure next, in order, cheapest-first, with what each buys.
   Distinguish what is free (sandbox / public API) from what needs a live probe and therefore
   needs the freeze lifted.
6. WHAT I WOULD NOT DO, and why.

Rules: normalise everything to $/day (R1). Never quote rewards_residual. Attach sample size and
"what this does not cover" to every number. Flag disagreements between lanes rather than
averaging them away. If a lane returned nothing or failed, say so explicitly.`,
  { label: 'synthesis', phase: 'Synthesise' })

return { synthesis, scored, instrument_ok: instrument.filter(Boolean).length }
