export const meta = {
  name: 'kalshi-reward-density-allowlist',
  description: 'Measure reward density per allowlisted series and quantify the round-robin dilution across the widened 14-series allowlist; propose a reversible allowlist trim (operator-gated)',
  phases: [
    { title: 'Ground', detail: 'per-series reward-density census + live footprint slot distribution vs pure-density counterfactual' },
    { title: 'Analyze', detail: 'dilution cost, dead-weight/toxic series, weighed against C18 starvation + R3 thinness + M8 P&L' },
    { title: 'Refute', detail: 'adversarial: is the dilution illusory / does trimming re-break C18 / is the ranking just model noise' },
    { title: 'Deliver', detail: 'reversible allowlist/per-series proposal, operator-gated, exact lever + rollback' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE. Worktree: ${WT} (branch claude/maker-kalshi-live). cd ${WT}/kalshi_live; bash cwd drifts, use absolute paths.

=== HARD CONSTRAINTS ===
1. READ-ONLY. No deploys, no live.env writes, no orders, no systemctl, no config writes. GETs only. This is a PROPOSAL, not a change.
   Kalshi authed reads work locally: cd ${WT}/kalshi_live && python3, module 'L' = kalshi_attribution_ledger. L.get(path), L.get_paginated(L.P+path,key), L.P='/trade-api/v2'. 0.6s spacing.
   Public API (no keys) also fine for /incentive_programs and /markets: >=0.3s spacing, paginate limit up to 1000.
2. CREATE ONLY NEW FILES. Do NOT edit any module. Do NOT import maker_kalshi_quoter.py (it may have import-time side effects) -- REPLICATE its selection logic (documented below) instead.
3. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.

=== THE DEPLOYED SELECTION LOGIC (verified from maker_kalshi_quoter.py select_footprint, md5 727ca7c5..., DO NOT re-read/edit; replicate) ===
For each active incentive program p (from GET /incentive_programs?status=active):
  - keep only incentive_type == 'liquidity'; require target_size_fp and discount_factor_bps present.
  - t = p['market_ticker']; series = t.split('-')[0]; keep only if series in SERIES_ALLOW.
  - parse start_date/end_date. LATE-LIFE GATE: cutoff_min = min(120, max(45, 0.6*life_min)); drop if end < now + cutoff_min.
  - days = max((end-start)/86400, 1/24).
  - usd_day = (period_reward/10000)/days   <-- THIS IS REWARD $/DAY PER CONTRACT, R1-normalized. NOT volume. (period_reward is fixed-point x10000.)
  - rows.sort by (-usd_day, ticker).
  - ROUND-ROBIN across series: series_order = series sorted by their best-usd_day desc; then repeatedly take 1 market per series per round (in series_order), skipping a series once it hits PER_SERIES_CAP or runs out, until FOOTPRINT_TOP picked.
LIVE CONFIG (verified in live.env this session): FOOTPRINT_TOP=40, PER_SERIES_CAP=100 (effectively NON-BINDING), SERIES_ALLOW = 14 series:
  KXTEMPDCH,KXTEMPAUSH,KXTEMPLAXH,KXTEMPNYCH,KXTEMPCHIH (5 temp), KXAAAGASD,KXAAAGASW (2 gas),
  KXB200MON,KXAMSAVO,KXH100MON,KXMUSKNW,KXCHIPBURRITO,KXTRUMPENDORSEMENTS,KXGENERICBALLOTVOTEHUB (7 added today).
CONSEQUENCE (the thing to measure): with round-robin + non-binding per-series cap, 40 slots spread ~evenly (~1 per active series per round) across ALL active-program series, regardless of each series' density. So a low-density/toxic series' best contracts are picked before a high-density series' 2nd/3rd/4th contracts.

=== THE PREMISE CORRECTION (already verified this session -- do NOT re-litigate) ===
The handoff said "the bot ranks by usd_day (VOLUME); it should rank by reward density." THAT IS FALSE: usd_day IS reward $/day. The bot already ranks by density. The REAL lever is the ROUND-ROBIN spreading the widened allowlist's slots into low-density series (the C18 anti-starvation fix, now over-widened). Frame everything around that, not a volume-vs-density ranking bug.

=== CANON RULES THAT BIND THIS ANALYSIS ===
- R1 (units): ALWAYS $/day = period_reward/10000 / window_days. NEVER rank raw period_reward (the $5,400 monthly-pool trap that made KXAAAGASM look 5.4x better when it is 17x WORSE/day). Window lengths vary 13h..698h across the venue -- normalize before comparing anything.
- R3 (two-sided is MARKET-level): a snapshot with a book NOT reaching Target Size on BOTH sides is EXCLUDED and pays NOBODY. So a series' EARNABLE density = $/day ONLY over contracts whose BOOKS are two-sided (fetch books, test both sides vs target_size_fp). A high pool at 0% two-sided (KXWNBAMENTION etc.) = $0 earnable. Apply R3 BEFORE ranking (the §M5 scanner shipped INVERTED by skipping this).
- Receipts > model (§M7): the $/day capture model OVER-PREDICTS 2-6x (rests-both-sides-all-period assumption; live is throttled/one-sided/drought). Treat $/day as an UPPER BOUND and a RELATIVE rank, never expected earnings. NEVER quote rewards_residual.
- Fees (§M10): maker fees ZERO by default; only ~86 listed series charge (mult=1). All 5 temp + both gas + the §M5 candidates are fee-free; KXAAAGASM charges. VERIFY each of the 7 new series against docs/maker_handoffs/kalshi-fee-schedule-2026-07-07.pdf text (or the archived extraction) -- a maker fee swallows the reward.
- Toxicity is a SEPARATE axis and cuts against the top of any pool ranking. KXTRUMPENDORSEMENTS = Truth-Social endorsement counts = discrete news-driven informed flow (highest-toxicity on the allowlist) AND has A-prefixed strikes that break the deployed _strike_of (float('A3') throws -> ladder pairing silently disabled -> counted fully naked). 'mention' families are settlement traps. High pool != good.
- Structure: threshold ladders ('above X') are good (many reward pots, one event risk, adjacent strikes self-hedge). Categorical / mutually-exclusive series break the event-aggregate throttle (anti-correlated strikes summed additively -> mis-fire). Classify each of the 7 new series.
- §M8 P&L receipts: GAS is the profitable family (+1.1% net of notional); TEMP is the entire measured loss (-9.2%, adverse selection, positions carried to resolution expiring worthless). So trimming toward gas aligns density AND P&L. BUT: temp cut is HARD-GATED to 2026-07-27 (credits lag a Time Period; the -$13.06 temp figure was a withdrawn partial ledger) -- do NOT propose cutting KXTEMP* now. Focus trim candidates on the 7 unproven/toxic NEW series, not temp.

=== METHOD ===
* MEASURE, do not assume. If a number looks impossible it IS wrong (this lane self-caught 6 measurement bugs + the inverted scanner last session). Sample size + window + what-is-NOT-covered on every number. Flag GUESSes.
* The C18 counter-argument is real: pure density concentration once starved the fee-free gas lane behind temp's high-pot strikes. Any trim/concentration proposal MUST show it does not re-create that (and note temp is currently DARK -- hourly programs between windows -- so an instantaneous census understates temp; §M7c).
`

phase('Ground')

const ground = await parallel([
  () => agent(`${RULES}

TASK -- PER-SERIES REWARD-DENSITY CENSUS (R1 + R3 + fees + structure), for the 14 allowlisted series.

DO:
1. GET /incentive_programs?status=active (paginate fully). For each of the 14 SERIES_ALLOW series, collect its active liquidity programs.
2. Per series compute, applying the LATE-LIFE gate: number of active programs; per-contract usd_day = period_reward/10000/window_days (R1); the series' total $/day and its best-contract $/day and median.
3. Apply R3: for a representative sample of each series' contracts, fetch the order book and test whether BOTH sides reach target_size_fp. Report each series' two-sided FRACTION and its EARNABLE $/day (density over two-sided contracts only). A series at ~0% two-sided is $0 earnable regardless of pool.
4. Fee status per series (§M10): check each against the archived fee schedule -- flag any that charge maker fees.
5. Structure per series: threshold-ladder vs categorical/mutually-exclusive vs mention-family. Flag toxicity (esp. KXTRUMPENDORSEMENTS A-prefix + news-driven).
6. Rank the 14 series by EARNABLE $/day (two-sided-adjusted), and separately note pool-only rank so the R3 gap is visible.

RETURN: a 14-row table [series | active_progs | best $/day | series $/day | two-sided % | earnable $/day | fee | structure | toxicity flag], the ranking, and every caveat (temp dark right now, sample sizes, model=upper-bound).`,
    { label: 'ground:density-census', phase: 'Ground' }),

  () => agent(`${RULES}

TASK -- LIVE FOOTPRINT SLOT DISTRIBUTION vs PURE-DENSITY COUNTERFACTUAL. Quantify the round-robin dilution.

DO:
1. Replicate select_footprint (logic above) on the live active programs with the live config (FOOTPRINT_TOP=40, PER_SERIES_CAP=100, the 14-series allowlist, the late-life gate). Produce the 40 picked contracts and their per-series distribution -- this is what the bot quotes NOW. Cross-check against the actual live resting orders (GET /portfolio/orders; the bot is quoting gas + KXAMSAVO + KXCHIPBURRITO + KXTRUMPENDORSEMENTS right now).
2. Compute the PICKED footprint's total usd_day (sum of the 40 picked contracts' usd_day).
3. COUNTERFACTUAL: take the same eligible rows but pick the top-40 by pure usd_day (NO round-robin, PER_SERIES_CAP still 100). Compute its total usd_day and per-series distribution.
4. DILUTION = counterfactual_total_usd_day - picked_total_usd_day. Break down which low-density series' slots (in the round-robin pick) displaced which high-density gas strikes (in the pure pick). Express as $/day and as % of the achievable.
5. IMPORTANT R3/thinness check: a high-density gas strike only helps if its BOOK is two-sided (else $0). Re-rank the counterfactual with R3 applied -- does concentrating in gas actually capture the extra $/day, or does gas run out of two-sided strikes (making the dilution illusory)? This is the crux: if gas has only ~6-8 two-sided strikes, spreading beyond them is forced, not wasteful.

RETURN: picked vs counterfactual per-series slot tables with totals, the dilution in $/day (raw and R3-adjusted), and an explicit verdict on whether the dilution is REAL (gas has more two-sided strikes we're not taking) or ILLUSORY (gas is exhausted, spread is forced). Note temp is likely dark now.`,
    { label: 'ground:dilution', phase: 'Ground' }),
])

log(`ground: ${ground.filter(Boolean).length}/2`)

phase('Analyze')

const analysis = await agent(`${RULES}

TASK -- SYNTHESIZE: is there a reward-density trim worth making, and what exactly?

DENSITY CENSUS: ${String(ground[0]).slice(0, 9000)}
DILUTION: ${String(ground[1]).slice(0, 9000)}

Decide, with the evidence:
1. Is the round-robin dilution REAL and material (gas has un-taken two-sided strikes worth more than the slots given to low-density series) or ILLUSORY (gas exhausted, spread forced by R3)? Lead with this -- it determines whether there is anything to do at all.
2. Which of the 7 NEW series are dead weight (low earnable $/day) and/or toxic (KXTRUMPENDORSEMENTS) consuming slots + inventory risk? Rank trim candidates.
3. What is the right LEVER: (a) trim SERIES_ALLOW (remove specific dead-weight/toxic series), (b) set PER_SERIES_CAP to a binding value that concentrates in the top series while still guaranteeing gas its slots, (c) a per-series MIN earnable-$/day gate, or (d) nothing? Weigh each against the C18 gas-starvation failure mode and R3 thinness.
4. Cross-check with §M8: trimming toward gas/away from toxic aligns with the P&L receipts. But temp is 07-27-gated and currently dark -- keep temp OUT of the trim. State this explicitly.
5. Quantify the expected upside HONESTLY as a RELATIVE, upper-bound number (§M7: model over-predicts 2-6x). If the upside is small (§M4 found in-allowlist reallocation is ~10-15% at most, near a plateau), SAY SO -- do not oversell.

RETURN: the real-vs-illusory verdict, the ranked trim candidates, the recommended lever with its exact config change, and an honest upside estimate with its uncertainty.`,
  { label: 'analyze', phase: 'Analyze' })

phase('Refute')

const refutations = await parallel([
  'dilution-illusory-r3', 'trim-rebreaks-c18-gas-starvation', 'ranking-is-model-noise', 'measurement-or-premise-error',
].map(lens => () => agent(`${RULES}

TASK -- ADVERSARIALLY REFUTE the trim proposal. Lens: **${lens}**. DEFAULT TO REFUTED IF UNCERTAIN. A trade-universe change on a live bot with real money -- find why it is wrong or unnecessary.

PROPOSAL: ${String(analysis).slice(0, 11000)}

Through your lens:
- dilution-illusory-r3: prove the dilution is illusory -- that gas (and other high-density series) have NO more two-sided-reachable strikes than they already get, so concentrating captures nothing and just adds event-risk concentration. Fetch the actual two-sided gas strike count. If gas is exhausted at ~6-8 strikes, the whole premise collapses.
- trim-rebreaks-c18-gas-starvation: show that trimming/concentrating re-creates the C18 failure (high-pot temp strikes, when temp un-darkens hourly, starving fee-free gas) -- or some new starvation. Remember temp is DARK now; the census understates it. Does the proposal survive temp coming back?
- ranking-is-model-noise: per §M7 the capture model over-predicts 2-6x and §M4 found the footprint already near a plateau (94% capture, ~10-15% max upside). Is the claimed upside within the noise? Is this churn for a rounding error?
- measurement-or-premise-error: re-verify the premise correction (usd_day really is reward not volume -- re-read the arithmetic) and every unit (period_reward /10000, /days; cents vs dollars; target_size). Did R3 get applied consistently? Did settled/dark programs leak in? Is the counterfactual computed correctly?

RETURN: refuted true/false, severity, the specific failing state, and the measurement that settles it.`,
    { label: `refute:${lens}`, phase: 'Refute', schema: {
      type: 'object',
      required: ['lens','refuted','severity','defect','settling_measurement'],
      properties: {
        lens: { type: 'string' },
        refuted: { type: 'boolean' },
        severity: { type: 'string', enum: ['CRITICAL','HIGH','MEDIUM','LOW','NONE'] },
        defect: { type: 'string' },
        failing_state: { type: 'string' },
        settling_measurement: { type: 'string' },
      },
    } })))

phase('Deliver')

const final = await agent(`${RULES}

TASK -- WRITE THE PROPOSAL. First-tasks priority #2: rank the footprint by reward density. The verified finding is that the bot ALREADY ranks by density and the real lever is the round-robin dilution across the widened 14-series allowlist.

DENSITY CENSUS: ${String(ground[0]).slice(0, 5000)}
DILUTION: ${String(ground[1]).slice(0, 5000)}
ANALYSIS: ${String(analysis).slice(0, 8000)}
REFUTATIONS: ${JSON.stringify(refutations.filter(Boolean), null, 1)}

Write \`docs/maker_handoffs/KALSHI_REWARD_DENSITY_2026-07-23.md\` and return it. Structure:
1. PREMISE CORRECTION up top: usd_day is reward $/day, not volume; the bot already ranks by density; the real lever is the round-robin over the widened allowlist. One paragraph, with the code cite.
2. THE PER-SERIES EARNABLE-DENSITY TABLE (R1 + R3 + fees + toxicity + structure), ranked.
3. IS THE DILUTION REAL OR ILLUSORY -- the decisive verdict from the refuters (lead with any CRITICAL that says illusory / gas-exhausted / re-breaks C18). If illusory, the honest recommendation is DO NOTHING and say why.
4. IF REAL: the ranked trim candidates (the 7 new series; NOT temp -- temp is 07-27-gated + currently dark, state this), and the recommended reversible lever with EXACT config (e.g. KALSHI_SERIES_ALLOW=<trimmed list> or KALSHI_PER_SERIES_CAP=<binding value>), the rollback line, and which risk breaker/invariant it touches. Tier-2 trade-universe change -> OPERATOR-GATED, propose-only.
5. HONEST UPSIDE: relative, upper-bound (§M7), with the §M4 plateau context. If it is ~10-15% of a small base, say the expected dollar impact is small.
6. THE OVERLAP WITH P&L (§M8): trimming toward gas/away from toxic also aligns with the receipts, but do NOT conflate -- this doc ranks by REWARD; the P&L case is separate and its temp verdict waits for 07-27.
7. UNCERTAINTY + read-only commands for the operator to verify.

Report every refuter verdict even where it kills the proposal. Flag GUESSes. No deploy, no config write -- proposal only.`,
  { label: 'deliver', phase: 'Deliver' })

return { final, refutations: refutations.filter(Boolean) }
