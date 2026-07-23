export const meta = {
  name: 'kalshi-coverage-expansion',
  description: 'Find safe expansion: series whose reward window matches their market life, live during our dark hours',
  phases: [
    { title: 'Map', detail: 'quantify the coverage gap and census the venue by horizon ratio' },
    { title: 'Qualify', detail: 'per-candidate diligence on the survivors' },
    { title: 'Verify', detail: 'adversarial refutation' },
    { title: 'Propose', detail: 'ranked slate with admission criteria' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE. Worktree: ${WT} (branch claude/maker-kalshi-live).
ALWAYS cd "${WT}" first — bash cwd drifts to another checkout.

=== HARD CONSTRAINTS ===
1. READ-ONLY on the live system. NO deploys, NO live.env edits, NO orders, NO systemctl.
   SSH only to READ. Public API reads are fine (no keys needed), space >= 0.3s, paginate with
   limit=10000 (NOT 1000 — 1000 silently truncates and is a known defect).
2. CREATE ONLY NEW FILES. Another workflow is concurrently editing
   kalshi_attribution_ledger.py, kalshi_concentration_study.py, kalshi_series_scan.py,
   maker_kalshi_quoter.py, test_live_hardening.py. DO NOT TOUCH THOSE. Put new work in new files.
3. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.

=== CANON — read first ===
docs/maker_handoffs/KALSHI_LIP_RULE_CANON.md
  §T sector > series > event(=ONE RISK) > market/contract(=one binary, one book, one reward pot).
  R1 period_reward is the TOTAL for a Time Period, NOT a rate. ALWAYS normalise to $/day.
  R2 $1.00 is a THRESHOLD on the WHOLE-period payout.
  R3 two-sided exclusion is MARKET-level — if the BOOK misses Target Size on either side the
     snapshot pays NOBODY. Apply R3 BEFORE ranking anything, or you rank unearnable pools top.
  R4 payout fraction = mean over snapshots of (yes_share + no_share)/2.
  §M10 maker fees: default multiplier 0 => FREE. Only 130 of 12,151 series charge.
       kalshi_live/series_fee_types.json has the map. Of series with active programs, only
       KXAAAGASM charges.
  §M13 credits LAG — they post once per Time Period, after it closes.

=== THE TWO FINDINGS THAT FRAME THIS JOB (both measured today) ===
**A. WE ARE DARK MOST OF THE DAY.** At 15:05Z, 6 of our 7 allowlist series had ZERO active
programs (all 5 KXTEMP*, plus KXAAAGASD in its recurring ~11h daily blackout — measured duty cycle
~48.8%, windows all end 03:59Z and restart ~14:44-15:31Z). Only KXAAAGASW was live. Consequence:
over 16h the bot quoted a mean of **2.14 markets** against a footprint of 11.38 and committed
**$42.62 of $85**. The under-deployment is NOT a gating bug — THERE IS NOTHING TO QUOTE.

**B. THE ADMISSION DISCRIMINATOR IS THE HORIZON RATIO.** Measured today:
    ratio = (market close_time - now) / (program end_date - now)
    KXAAAGASD 1.0 · KXAAAGASW 1.0   <- our two working series: reward spans the whole hold
    KXRT 15.4 · KXH200MS 29.5 · KXFUNDRAISING 33.6 · KXDPZ 79.3 · KXVOTEPRIMARY 151.9
A high ratio means the reward stops days-to-months before the market closes, leaving inventory
carried uncompensated. Worse, there is NO EXIT for it: when a program expires the ticker leaves
the footprint (select_footprint keys lifecycle on program end_date, maker_kalshi_quoter.py:294-312),
and STRAND UNWIND (:1022-1053) iterates naked positions ONLY — so a MATCHED PAIR has no exit path
at all. **Ratio ~1 is why gas works. This is the first-class filter.**

=== METHOD ===
* MEASURE BEFORE CLAIMING. If a number looks impossible it IS wrong — today produced a
  $156/period figure on an $85 account, a -$442 settlement, and a $604/day capture on a book that
  was 0% two-sided. Find the bug; do not explain it away.
* State SAMPLE SIZE and WHAT IS NOT COVERED on every number.
* Reward-side modelling cannot see fill rate or adverse selection (no queue position). Say so.
* Beware degenerate sampling: a series sharing ONE pool across contracts makes "top N by pool"
  arbitrary API order — that defect produced a false "100% two-sided / $7.42" on an n=4 sample.
`

phase('Map')

const mapping = await parallel([
  () => agent(`${RULES}

TASK — QUANTIFY THE COVERAGE GAP PRECISELY. How many hours a day can we actually earn?

DO:
1. For each of our 7 allowlist series, reconstruct the PROGRAM CALENDAR over the last 7 days from
   /incentive_programs (use status filters; include non-active to see historical windows if the
   endpoint allows, else reconstruct from start_date/end_date of what is visible plus the archived
   samples in kalshi_live/*.jsonl). Produce, per series: window start/end times, duration, gaps,
   and DUTY CYCLE (fraction of the day with >=1 active program).
2. Build the UNION calendar: for each UTC hour 0-23, how many of our series have a live program?
   Report the hours where the union is ZERO — those are the hours we structurally cannot earn.
3. Cross-check against reality: read /opt/pa2-maker-kalshi-live/plans-2026072*.jsonl (READ-ONLY via
   ssh sudo cat) and compute quoted_markets and committed_usd by UTC hour. The dark hours in (2)
   should coincide with the low-quoting hours. If they DON'T, that means something ELSE is
   throttling us and that is a much bigger finding — chase it.
4. Quantify the prize: if we had ratio~1 coverage in the dark hours at our current earn rate,
   what is the upside? Express as extra $/day of pool ACCESSIBLE (not captured — be explicit).

RETURN: per-series duty cycles, the 24-hour union coverage table, the dark-hour list, the
plans-file cross-check, and the accessible-pool upside with its caveats.`,
    { label: 'map:coverage-gap', phase: 'Map' }),

  () => agent(`${RULES}

TASK — VENUE-WIDE CENSUS RANKED BY HORIZON RATIO. Find every series that looks like GAS.

The ratio filter (finding B) is the first-class test. Apply it to the WHOLE venue.

DO:
1. Pull ALL active incentive programs (limit=10000, paginate). For each series compute:
   - pool $/day (R1-normalised — a monthly pool is NOT a daily pool)
   - median HORIZON RATIO = (market close_time - now) / (program end_date - now), sampling
     several contracts per series. Fetch close_time from /markets/{ticker}.
   - fee status from kalshi_live/series_fee_types.json (or /series/{ticker}.fee_type;
     "quadratic" = maker-FREE, "quadratic_with_maker_fees" = CHARGES)
   - structure: numeric threshold ladder vs categorical. Be careful — a crude numeric-suffix
     heuristic MIS-CLASSIFIES prefixed numerics (e.g. KXFUNDRAISING-...-A145000000 IS a ladder).
     Check strike_type / mutually_exclusive from /markets/{ticker} rather than the ticker string.
   - R3 two-sided rate on a real sample of books (apply R3 BEFORE ranking).
2. RANK by: ratio <= 1.5 FIRST, then maker-fee FREE, then two-sided rate, then $/day.
   **Report how many of the ~162 series pass the ratio filter at all.** If the answer is "only
   gas", that is a first-class finding and the expansion answer may be "there is nothing to add" —
   say so plainly rather than manufacturing a slate.
3. For any survivor, ALSO report its program CALENDAR (does it run during our dark hours?) —
   a ratio~1 series that runs only when gas is already live adds nothing.

RETURN: the full ranked table, the count passing the ratio filter, and the survivors annotated
with whether they cover our dark hours.`,
    { label: 'map:horizon-census', phase: 'Map' }),

  () => agent(`${RULES}

TASK — CAN WE EARN MORE FROM WHAT WE ALREADY HAVE? (the cheapest expansion is depth, not breadth)

We commit a mean of $42.62 of $85 and quote 2.14 markets. Some of that is darkness (finding A),
but establish how much is darkness versus self-imposed limits.

DO:
1. From plans-2026072*.jsonl (READ-ONLY ssh sudo cat), analyse ONLY the cycles where our series
   HAD live programs (i.e. footprint > 0). In those cycles: what is quoted_markets, committed_usd,
   gated_out, capped_markets, budget_dropped_markets, create_fail? Which gate is actually binding?
2. Examine the config ceilings against observed behaviour:
   MAX_TOTAL_CAPITAL=85, MAX_MARKET_CAPITAL=15, JOIN_SIZE=20, PER_SERIES_CAP=10, FOOTPRINT_TOP=40,
   MAX_SPREAD_TICKS=8, MIN_DEPTH_SYM=0.25, INV_SOFT_CT=15, INV_HARD_CT=60, HELD_MAX_USD=20.
   Which of these is the binding constraint when programs ARE live? Quantify with counts.
3. The selection gate (MIN_DEPTH_SYM / MAX_SPREAD_TICKS, maker_kalshi_quoter.py ~:236-237 applied
   ~:497-499) rejects wide/one-sided books. How many in-allowlist contracts does it reject per
   cycle, and would relaxing it admit contracts that are genuinely earnable under R3?
4. §M1 measured the reward-side optimum at K~6-7 contracts. We quote 2.14. When programs are live,
   HOW MANY could we quote within existing caps? If the answer is >=6, the expansion is free —
   no new series needed.
5. ⚠ Note KALSHI_REDUCE_ONLY_KEEP_BOTH was turned back ON and KALSHI_TAKER_FLATTEN turned ON at
   ~15:11Z / ~15:19Z today. Cycles after those timestamps are a DIFFERENT CONFIG — segment your
   analysis and do not pool across the change.

RETURN: the binding constraint with counts, how many contracts we could quote when live, whether
depth-expansion beats breadth-expansion, and the specific knob you would change (propose only —
the operator decides, nothing is deployed).`,
    { label: 'map:depth-not-breadth', phase: 'Map' }),
])

log(`map phase: ${mapping.filter(Boolean).length}/3 lanes`)

phase('Qualify')

const qualified = await agent(`${RULES}

TASK — QUALIFY THE SURVIVORS. Take the horizon-census output and do real diligence on anything
that passed the ratio filter AND covers dark hours.

COVERAGE GAP:
${String(mapping[0]).slice(0, 5000)}

HORIZON CENSUS:
${String(mapping[1]).slice(0, 9000)}

DEPTH ANALYSIS:
${String(mapping[2]).slice(0, 5000)}

For each survivor (if there are none, say so and stop — a null result is a real result):
1. Confirm structure PROPERLY (strike_type / mutually_exclusive from the API, not the ticker
   string). Categorical series are unsafe: event_deltas buckets by ticker.split('-')[:2] and would
   net INDEPENDENT risks as if they offset, reading FLAT while carrying live naked exposure.
2. Confirm maker-fee FREE.
3. Measure the R3 two-sided rate over a real sample, and the achievable $/day at the deployed
   shape (join 20 ct/side, $15/contract).
4. Assess toxicity in the FIGHTMENTION shape — fine in-window, gutted at settlement. Note that
   eight candidate toxicity metrics were built and refuted by their own authors in a prior run;
   if you cannot measure it, say UNMEASURED rather than inventing a proxy.
5. State the ONE thing that would disqualify it.

RETURN a per-survivor verdict: series, ratio, structure+evidence, fee, two-sided %, $/day,
dark-hour coverage, toxicity (or UNMEASURED), sample size, ADMIT/REJECT/NEEDS-PROBE, and the
single biggest reason.`,
  { label: 'qualify', phase: 'Qualify' })

phase('Verify')

const refutations = await parallel(['horizon-and-exit', 'structure-and-risk', 'reward-reality'].map(lens => () =>
  agent(`${RULES}

TASK — ADVERSARIALLY REFUTE the expansion proposal. Lens: **${lens}**.
DEFAULT TO REFUTED IF UNCERTAIN. Your job is to find why this is wrong.

THE PROPOSAL:
${String(qualified).slice(0, 12000)}

Through your lens:
- horizon-and-exit: is the ratio computed correctly (program end vs market close, not vs settle)?
  Does a ratio~1 series ACTUALLY release capital at program end, or does inventory persist? Trace
  the exit path in maker_kalshi_quoter.py for BOTH a naked position and a MATCHED PAIR after the
  program expires. Remember TAKER_FLATTEN is now ON (changed today ~15:19Z) — does that change the
  exit story, and does it introduce a new cost?
- structure-and-risk: would event_deltas mis-fire on any proposed series? Is ladder_pairing
  operative (does _strike_of parse its strikes — float() on the strike suffix)? Would admitting
  this series create correlated exposure the guards cannot see? Check the +low/-high vs -low/+high
  asymmetry — today a -low/+high gas pair lost BOTH legs at settlement.
- reward-reality: is the $/day real or model-inflated? Canon §M7(d) says the model over-predicts
  RECEIPTS by 2-6x. Is the two-sided rate from a big enough sample, or an n=4 artefact? Does the
  claimed pool survive R1 normalisation and R2's $1 threshold?

RETURN: refuted true/false, severity, the specific defect, and what measurement would settle it.`,
    { label: `refute:${lens}`, phase: 'Verify', schema: {
      type: 'object',
      required: ['lens','refuted','severity','defect','settling_measurement'],
      properties: {
        lens: { type: 'string' },
        refuted: { type: 'boolean' },
        severity: { type: 'string', enum: ['BLOCKER','HIGH','MEDIUM','LOW','NONE'] },
        defect: { type: 'string' },
        settling_measurement: { type: 'string' },
      },
    } })))

phase('Propose')

const proposal = await agent(`${RULES}

TASK — WRITE THE EXPANSION PROPOSAL. Operator directive: "we need to expand, cautiously but
expand." Nothing deploys without their say-so; this is a decision document.

COVERAGE GAP:
${String(mapping[0]).slice(0, 6000)}
HORIZON CENSUS:
${String(mapping[1]).slice(0, 8000)}
DEPTH vs BREADTH:
${String(mapping[2]).slice(0, 6000)}
QUALIFIED SURVIVORS:
${String(qualified).slice(0, 8000)}
ADVERSARIAL REFUTATIONS:
${JSON.stringify(refutations.filter(Boolean), null, 1)}

WRITE docs/maker_handoffs/KALSHI_EXPANSION_PROPOSAL_2026-07-23.md and return it, structured:

1. **THE COVERAGE PROBLEM IN ONE NUMBER** — how many hours a day can we currently earn at all?
2. **DEPTH FIRST** — when programs ARE live, are we leaving capacity unused? If yes, that is the
   cheapest expansion and needs NO new series. Name the binding knob and the proposed value.
3. **BREADTH** — the ranked slate that survived the horizon filter AND refutation. If NOTHING
   survives, lead with that; do not manufacture a slate. State how many of ~162 series passed.
4. **THE ADMISSION CRITERIA** distilled into a checklist a future session can apply mechanically
   (horizon ratio, fee type, structure, R3 rate, dark-hour coverage, toxicity status).
5. **WHAT WOULD HAVE TO CHANGE IN THE CODE** for high-ratio series (KXRT, KXFUNDRAISING etc.) to
   become admissible — specifically the missing exit path for matched pairs on program-expired
   tickers. Is that a small fix or a redesign? Be concrete with file:line.
6. **SEQUENCED PLAN**, cheapest first, marking which items are free/sandbox and which need a live
   config change.
7. **WHAT I WOULD NOT DO AND WHY.**

Rules: normalise to $/day; attach sample size and "what this does not cover" to every number;
report the refuters' verdicts even where they contradict the qualifier; flag any lane that
returned nothing.`,
  { label: 'proposal', phase: 'Propose' })

return { proposal, refutations: refutations.filter(Boolean) }
