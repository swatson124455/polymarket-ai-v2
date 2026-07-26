export const meta = {
  name: 'kalshi-opportunity-godmode',
  description: 'Massive creative opportunity dive — ground the whole Kalshi venue, fan out diverse edge-hunting lenses (reward-farm, mispricing snipe, arb, incentive-stack, microstructure, timing, capital, anti-toxic, program-quirk, wildcard), adversarially score every idea against real numbers, completeness-critic the gaps, deliver the full ranked menu',
  phases: [
    { title: 'Ground', detail: 'pull the real substrate: full venue census, incentive-program landscape, our own fill/edge history, capital/constraints' },
    { title: 'Ideate', detail: '10 diverse creative lenses each hunt a different edge — god mode, bring everything' },
    { title: 'Score', detail: 'adversarially score + ground every idea against real data (earnable? toxic? feasible with our size/capital? snipe vs farm?)' },
    { title: 'Synthesize', detail: 'completeness critic for gaps + the full ranked menu, nothing discarded' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE — CREATIVE OPPORTUNITY DIVE. Worktree: ${WT}. cd ${WT}/kalshi_live for data pulls; bash cwd drifts, use absolute paths.

=== SCOPE + HARD CONSTRAINTS ===
1. READ-ONLY + BRAINSTORM. No deploys, no live.env writes, no orders, no systemctl, no config/module edits. This is a THINKING dive; the deliverable is IDEAS, not trades.
2. KALSHI VENUE ONLY. Never touch MB/WB/EB/SB or the Polymarket maker (claude/maker-bot) or shared modules. Every idea must be executable on Kalshi.
3. Data: Kalshi authed reads work locally: cd ${WT}/kalshi_live && python3, module 'L' = kalshi_attribution_ledger. L.get(path), L.P='/trade-api/v2'. 0.6s spacing.
   PUBLIC endpoints (no keys): /incentive_programs?status=active&limit=10000 (returns ALL ~2,300 in one page — do NOT use limit=200, do NOT double-up '?'; use &-params), /markets?series_ticker=X&status=open&limit=1000, /markets/{ticker}/orderbook, /events/{ticker}, /series/{ticker}.
   AUTHED reads: /portfolio/{balance,positions,fills,settlements,orders}.

=== WHAT WE DO NOW (the baseline to beat) ===
We are a passive Liquidity-Incentive-Program (LIP) MAKER: rest two-sided quotes on incentivized contracts, earn the LIP reward pool, treat fills as a cost. Live capital ~$230 (funding-gate fix just shipped so free cash deploys correctly now). Quote size ~20 ct/side. Currently allowlisted: 5 temp cities, gas-daily (KXAAAGASD, the flagship — venue rank ~3-4 by \$/day), gas-weekly, + a few new series (KXAMSAVO/B200/H100/MUSKNW/CHIPBURRITO/TRUMPENDORSEMENTS/GENERICBALLOTVOTEHUB).

=== THE CANON RULES (bind every reward claim; violating these is how this lane shipped wrong numbers) ===
- R1 (units): reward density = \$/day = (period_reward/10000) / window_days. NEVER rank raw period_reward (a $5,400 MONTHLY pool looked 5.4x better but was 17x WORSE/day). Windows span 13h..698h. Normalize before comparing anything.
- R3 (two-sided is MARKET-level): a snapshot whose BOOK fails Target Size (~1000 ct) on EITHER side is EXCLUDED and pays NOBODY. So a series' EARNABLE \$/day = pool \$/day x (two-sided contracts / eligible). A huge pool at 0% two-sided (e.g. KXHURCAT hurricanes = empty books; KXWNBAMENTION) = \$0 earnable. Apply R3 before ranking. Our 20 ct is NEVER the marginal maker vs a 1000 Target (measured 0/304) — so we cannot rescue a one-sided book ourselves.
- R4 (scoring): score = DF^N x size (N = ticks from reference), normalized. DF=0.50 on our series -> credit HALVES every tick off reference. Resting a tick inside costs ~half the reward.
- Fees (§M10): maker fees ZERO by default; only ~86 listed series charge (fee_type 'quadratic_with_maker_fees' or a maker multiplier). Sports (NFL/NBA/MLB/NHL/NCAA/PGA/UEFA) + macro (KXCPI/KXFED/KXGDP/KXPAYROLLS) + awards charge makers. A maker fee can swallow the whole reward — check fee_type per candidate.
- Receipts > model (§M7): the \$/day capture MODEL over-predicts 2-6x (assumes rest-both-sides-all-period; live is throttled/one-sided/drought). Treat \$/day as an UPPER BOUND + a RELATIVE rank, never expected earnings. NEVER quote the ledger's rewards_residual (garbage).
- Toxicity is a SEPARATE axis: 'mention' families (WNBAMENTION, TRUMPMENTION, EARNINGSMENTION) = settlement traps (informed news flow). Hurricane/discrete-event = toxic. Categorical / mutually_exclusive events break the event-aggregate throttle (anti-correlated strikes summed additively -> mis-fire). Threshold ladders ('greater_or_equal', mutex=False) are the GOOD structure (adjacent rungs self-hedge, one event risk).
- Sunset: BOTH LIP and Volume incentive programs expire 2026-09-01. Any idea whose payback runs past that must say so.
- Other incentive programs exist and are UNDER-EXPLORED: **Combo Incentive** (pays for maker VOLUME/fills — the thing we currently book as pure COST; email opt-in, ~$ we may be leaving on the table), **Volume Incentive**, **DLP/designated-MM** (requires a Market Maker Agreement which DISQUALIFIES from LIP — mutually exclusive, model the tradeoff). Stacking LIP+Combo may be free money.

=== KNOWN DEAD ENDS (do not re-propose as fresh) ===
KXHURCAT (empty books, \$0 earnable); mention families (toxic); temp (adverse-selection net loser -9.2%, gated to 07-27); KXAAAGASM (charges maker fees + monthly-pool unit trap); pure concentration into gas (two-sided-exhausted at ~10 strikes). Naive pool-size ranking (ignores R1+R3).

=== THE ASK (operator, verbatim intent) ===
"We keep concluding there's nothing to trade because we only look at the narrow slice we know. Find NEW optimal markets, series, sectors — ANYTHING we can SNIPE. Bring ALL ideas, we'll flush the good ones out. Creative GOD MODE. Thinking only, no execution."
So: MAXIMIZE breadth + creativity. 'Snipe' = not just passive reward-farming but ACTIVE edges (mispricing, arbitrage, timing, being early to a new program, exploiting the LIP formula). Generate broadly; do NOT self-censor a wild idea — flag it wild and keep it. The scoring phase filters; ideation does NOT.

=== METHOD ===
Every reward \$ figure: R1-normalized, R3-adjusted where possible, labeled §M7 upper bound. State sample size + what's-not-covered. Flag GUESSes. If a number looks impossible it IS wrong — find the bug. Prefer a grounded number over a vibe, but in IDEATION a well-reasoned unmeasured idea is welcome (mark it unmeasured).
`

phase('Ground')

const ground = await parallel([
  () => agent(`${RULES}

GROUND TASK 1 — THE FULL VENUE CENSUS (the master opportunity table).
Pull ALL active incentive programs (public, one page limit=10000). Aggregate by series and by CATEGORY (from /series/{ticker} 'category' field). For the TOP ~40 series by R1 \$/day that we are NOT already in, sample 3-6 orderbooks each and compute the R3 two-sided rate + earnable \$/day, the fee_type (maker-charging?), the structure (mutex? strike_type? mention-family?), and the category/sector. Also roll up by CATEGORY: which whole sectors carry the most EARNABLE (R3-adjusted, fee-free, non-mention) \$/day that we have zero presence in?
RETURN: (a) a ranked table of the top ~25 NON-allowlisted series by earnable \$/day with fee/structure/toxicity flags, (b) a by-category rollup of earnable \$/day, (c) the 5 biggest 'we're-not-here-and-it's-earnable' surprises. Note the timestamp; programs churn.`,
    { label: 'ground:census', phase: 'Ground', effort: 'high' }),

  () => agent(`${RULES}

GROUND TASK 2 — THE INCENTIVE-PROGRAM LANDSCAPE (untapped reward channels).
Map every Kalshi reward/incentive mechanism, not just LIP. Sources: /incentive_programs (incentive_type field — are there non-'liquidity' types active?), the archived CFTC rulebook + fee schedule in docs/maker_handoffs/, canon §M9/§M10/§M11, and any Combo/Volume program terms discoverable. For each: what it pays for (resting quotes? fills? volume?), eligibility + opt-in mechanics, whether it STACKS with LIP, the Sep-1 sunset status, and the DLP/Market-Maker-Agreement mutual-exclusion tradeoff (signing an MM agreement disqualifies from LIP — quantify what LIP income would be given up).
RETURN: a table [program | pays for | opt-in | stacks with LIP? | sunset | verdict], the single highest-value UNTAPPED channel (likely Combo = getting paid for fills we already generate), and exactly what we'd need to confirm/do to capture it.`,
    { label: 'ground:programs', phase: 'Ground', effort: 'high' }),

  () => agent(`${RULES}

GROUND TASK 3 — OUR OWN EDGE FINGERPRINT + MICROSTRUCTURE.
From /portfolio/fills + /portfolio/settlements + the settlement-pnl tool (kalshi_settlement_pnl.py, receipt-validated), characterize WHERE we win and lose: per-series realized \$/contract, adverse-selection signature (positions carried to resolution expiring worthless), fill clustering, and reward-density-by-hour / the overnight drought (canon §M6). Also: what does the LIP scoring formula (R4, DF=0.50) reward — is there queue/reference-price positioning edge we're leaving on the table? Is there a cadence sweet spot (we run 2-min; §drift dive said faster = worse for temp)?
RETURN: (a) our win/lose fingerprint by series-shape (what maker-friendly looks like for US at 20ct/\$230), (b) the microstructure levers we don't currently pull (queue position, reference-price band, size-vs-DF, cadence), (c) the time-of-day reward-density profile. This tells ideation what SHAPE of opportunity actually converts for us.`,
    { label: 'ground:edge-fingerprint', phase: 'Ground', effort: 'high' }),

  () => agent(`${RULES}

GROUND TASK 4 — CAPITAL, SIZE, AND WHAT 'SNIPE' WOULD REQUIRE.
Recap the hard constraints any idea must fit: current capital (/portfolio/balance), the ~20ct size vs 1000 Target (so we're a price-taker on qualification, never marginal), fee-free-only today, the funding-gate fix (free cash now deploys), the risk guards (naked breaker, daily loss halt, late-life gate). Then scope the MODES an idea could use: passive LIP maker (what we do), ACTIVE taker-snipe (cross the spread on a mispricing — costs the taker fee + spread, needs a real edge), arbitrage (lock a spread across related markets), event/settlement timing (known-data resolution). For each mode: what capital/latency/data/edge it needs, and whether it's even feasible for us (we have no low-latency infra, no external data feeds wired, small capital).
RETURN: the constraint envelope (what an idea MUST satisfy to be real for us), and a feasibility rubric per mode (LIP-farm / taker-snipe / arb / timing) that the scoring phase can apply.`,
    { label: 'ground:constraints', phase: 'Ground', effort: 'high' }),
])

const G = ground.filter(Boolean)
const groundDigest = G.map((g, i) => `--- GROUND ${['CENSUS','PROGRAMS','EDGE-FINGERPRINT','CONSTRAINTS'][i]} ---\n${String(g).slice(0, 4500)}`).join('\n\n')
log(`ground: ${G.length}/4 lanes complete`)

phase('Ideate')

const IDEA_SCHEMA = {
  type: 'object',
  required: ['lens', 'ideas'],
  properties: {
    lens: { type: 'string' },
    ideas: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'thesis', 'edge_type', 'where', 'why_it_works', 'what_kills_it', 'novelty'],
        properties: {
          title: { type: 'string' },
          thesis: { type: 'string' },
          edge_type: { type: 'string', enum: ['reward-farm', 'mispricing-snipe', 'arbitrage', 'incentive-stack', 'microstructure', 'timing', 'structural', 'wildcard'] },
          where: { type: 'string' },
          why_it_works: { type: 'string' },
          what_kills_it: { type: 'string' },
          rough_upside: { type: 'string' },
          effort: { type: 'string', enum: ['low', 'med', 'high'] },
          novelty: { type: 'string', enum: ['known-adjacent', 'new', 'wild'] },
        },
      },
    },
  },
}

const LENSES = [
  { key: 'untapped-series-sectors', brief: 'Untapped earnable LIP series & whole sectors we have ZERO presence in. Use the census. Which fee-free, non-mention, threshold-ladder series/sectors carry real earnable \$/day? Crypto? Econ (fee-check!)? Commodities beyond gas? Company metrics? Entertainment/awards? Politics sub-types? Name specific series and why they fit our shape.' },
  { key: 'stacked-incentives', brief: 'Stacking incentive programs. Combo pays for the FILLS we already treat as cost — can we opt in and double-dip on LIP series? Volume Incentive? Is there a portfolio that maximizes LIP + Combo jointly? The DLP/MM-agreement tradeoff — is there any series where becoming a designated MM beats LIP? Quantify what we leave on the table.' },
  { key: 'microstructure-queue', brief: 'Microstructure / queue / formula edges. R4: DF^N decay means reference-price-band positioning is everything. Are we resting optimally vs the reference? Queue-position tricks, order-splitting to maximize normalized score, size-vs-DF tuning, joining the instant a snapshot is taken. Sub-1000-Target series where our 20ct actually IS marginal. Cadence sweet spots.' },
  { key: 'timing-calendar', brief: 'Timing / calendar / be-early edges. New-program-launch sniping (rest first when a fresh series lists = own the reference before competition). Reward-density-by-hour (quote where/when density peaks, skip the drought). Event-driven reward spikes. Short-window high-\$/day programs (13h gas-daily = \$188/day; are there shorter, hotter ones?). Settlement-schedule farming.' },
  { key: 'mispricing-snipe', brief: 'ACTIVE mispricing snipes (taker edges). Where is a Kalshi book STALE or WRONG vs a computable fair value? Scheduled public-data markets (econ prints, crypto price at time T, sports finals) where the number is knowable/modelable. Cross-market inconsistency snipes. Where retail sets a dumb price we can take. Be concrete about the data source and the edge size. (Note our infra limits — flag what needs building.)' },
  { key: 'arbitrage-structural', brief: 'Arbitrage & self-hedged structures. Cross-strike/cross-event/cross-series dutch books (do related markets violate arbitrage bounds — e.g. a ladder whose rungs sum wrong, or YES+NO < 1?). Reward-farming structures that net-hedge our directional risk to ~0 while collecting LIP. Neg-risk-style baskets. Correlated-market pairs that offset.' },
  { key: 'capital-scale', brief: 'Capital scale & efficiency (funding gate just freed cash). Given ~$230 and now-correct accounting: concentration vs breadth for max reward capture. Sub-account strategies (does a second account multiply LIP eligibility?). Compounding rewards into more footprint. Optimal K across series. What would 2x/5x capital unlock, and where would it hit diminishing returns (R3 two-sided exhaustion)?' },
  { key: 'anti-toxic-dumb-flow', brief: 'Flip toxicity into edge: find the DUMB-money markets. Which earnable series have UNINFORMED retail flow (noise traders) where a maker reliably wins the spread — the opposite of the mention/hurricane traps? Meme/novelty/pop-culture/long-dated markets where nobody has private info. Identify the anti-adverse-selection signature and name series that fit it.' },
  { key: 'program-quirk-exploit', brief: 'Exploit LIP FORMULA quirks (rulebook-legal). The $1.00 threshold, Target Size band (>100 & <20,000), DF band, Time-Period-length normalization, "Time Periods may overlap / a sequence of periods". Are there series with tiny Target Size (our 20ct qualifies), unusually generous DF, or window-length arbitrage (same pool, shorter window = higher \$/day)? Threshold-clearing tricks. Where the formula pays disproportionately.' },
  { key: 'wildcard-bluesky', brief: 'WILD CARD / contrarian / blue-sky — GOD MODE. Anything unconventional on Kalshi: providing liquidity where literally nobody does (first-mover on dead books that are about to get a reward program), market types we have not considered, meta-strategies, being the reference-setter, adapting a known market-making trick from crypto/equities to Kalshi, exploiting program churn, anything nobody has tried. Do NOT self-censor. Flag wild, keep it.' },
]

const ideaSets = await parallel(LENSES.map(L2 => () => agent(`${RULES}

=== GROUND TRUTH (use it; do not hallucinate series that aren't real) ===
${groundDigest}

IDEATION LENS: **${L2.key}**
${L2.brief}

Generate 6-12 CONCRETE opportunity ideas through THIS lens ONLY (other lenses cover other angles — do not drift). Each idea: a specific, actionable thesis (name the series/sector/mechanism), the edge source, why it could work for US (20ct, ~$230, fee-free, no low-latency infra), what would kill it, rough upside, and novelty. Ground each in the census/program facts where you can; mark unmeasured ideas as such. Bring bold ideas — the scoring phase filters, you do NOT. Prefer specific over vague ("rest first on KXCPI's fresh monthly program before competition" beats "trade econ").`,
  { label: `ideate:${L2.key}`, phase: 'Ideate', effort: 'high', schema: IDEA_SCHEMA })))

const allIdeas = ideaSets.filter(Boolean).flatMap(s => (s.ideas || []).map(it => ({ ...it, lens: s.lens })))
log(`ideate: ${allIdeas.length} raw ideas across ${ideaSets.filter(Boolean).length} lenses`)

phase('Score')

// chunk ideas for adversarial scoring against real data
const CHUNK = 8
const chunks = []
for (let i = 0; i < allIdeas.length; i += CHUNK) chunks.push(allIdeas.slice(i, i + CHUNK))

const SCORE_SCHEMA = {
  type: 'object',
  required: ['scored'],
  properties: {
    scored: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'verdict', 'score', 'reason'],
        properties: {
          title: { type: 'string' },
          verdict: { type: 'string', enum: ['PURSUE', 'INVESTIGATE', 'PARK', 'DEAD'] },
          score: { type: 'integer', minimum: 1, maximum: 5 },
          grounded_number: { type: 'string' },
          toxicity_risk: { type: 'string', enum: ['LOW', 'MED', 'HIGH', 'UNKNOWN'] },
          feasible_for_us: { type: 'string' },
          snipe_or_farm: { type: 'string' },
          reason: { type: 'string' },
          next_step: { type: 'string' },
        },
      },
    },
  },
}

const scoredChunks = await parallel(chunks.map((ch, ci) => () => agent(`${RULES}

=== GROUND TRUTH ===
${groundDigest}

SCORE these ${ch.length} candidate opportunities ADVERSARIALLY and HONESTLY. For each: verify the premise against real data where possible (pull a book, check a fee_type, check R3), then assign:
- verdict: PURSUE (real, earnable/edge, feasible now) / INVESTIGATE (promising but needs a measurement) / PARK (real but blocked by capital/infra/sunset) / DEAD (premise false — say why).
- score 1-5 (5 = best risk-adjusted opportunity for US specifically).
- grounded_number: an R1+R3 earnable \$/day or edge estimate if you can compute one (§M7 upper bound), else 'unmeasured'.
- toxicity_risk, feasible_for_us (20ct/$230/no-low-latency), snipe_or_farm, one-line next_step.
Default skeptical: if an idea needs infra we don't have, or a series that's 0% two-sided, or a maker-fee series, or violates R1/R3 — score it low and say so. But do NOT kill a genuinely novel idea just because it needs one measurement — that's INVESTIGATE.

CANDIDATES:
${JSON.stringify(ch, null, 1)}`,
  { label: `score:chunk${ci + 1}`, phase: 'Score', effort: 'high', schema: SCORE_SCHEMA })))

const scored = scoredChunks.filter(Boolean).flatMap(s => s.scored || [])
log(`score: ${scored.length} ideas scored`)

phase('Synthesize')

// completeness critic + synthesis in parallel, then merge in the deliverable
const critic = await agent(`${RULES}

=== GROUND TRUTH ===
${groundDigest}

You are the COMPLETENESS CRITIC. Here are all ${scored.length} scored ideas (titles + verdicts):
${JSON.stringify(scored.map(s => ({ t: s.title, v: s.verdict, sc: s.score })), null, 1)}

And the 10 lenses run: ${LENSES.map(l => l.key).join(', ')}.

What opportunity CLASS did nobody explore, or explored shallowly? Think: an edge type missing, a sector nobody named, a mechanic (e.g. a specific incentive quirk, a data source, a market family) that got skipped, a cross-cutting combination of two ideas that's bigger than either. Name 5-10 concrete GAPS, each with a specific new idea to fill it. Be the person who says "you missed the obvious thing."
RETURN: the gaps as concrete new ideas (same fields as an idea: title, thesis, edge_type, why, what_kills_it).`,
  { label: 'completeness-critic', phase: 'Synthesize', effort: 'high' })

const final = await agent(`${RULES}

=== GROUND TRUTH ===
${groundDigest}

=== ALL SCORED IDEAS ===
${JSON.stringify(scored, null, 1)}

=== COMPLETENESS-CRITIC GAPS (fold these in) ===
${String(critic).slice(0, 6000)}

Write \`docs/maker_handoffs/KALSHI_OPPORTUNITY_MENU_2026-07-23.md\` and return it. This is the FULL MENU for the operator to flush good ones from — present EVERYTHING, discard nothing, but make it navigable:
1. TOP SHORTLIST — the ~8-12 highest-conviction PURSUE/INVESTIGATE ideas, ranked, each 2-3 lines: the edge, the grounded number (§M7 upper bound), snipe-or-farm, the ONE next step. Lead with the best 'snipe' and the best 'farm'.
2. THE FULL MENU by theme (the 8 edge_types) — every idea in a compact table [title | verdict | score | grounded# | toxicity | feasible? | next step], including DEAD ones (with the one-line kill reason, so we don't re-propose them).
3. QUICK WINS vs BIG BETS vs MOONSHOTS — bucket the shortlist by effort/upside.
4. THE COMPLETENESS-CRITIC GAPS — what we almost missed, folded in as fresh candidates.
5. CROSS-CUTTING PLAYS — any 2+ ideas that combine into something bigger (e.g. Combo-stack + a high-fill series; be-early-launch + a hot short-window program).
6. HONEST CAVEATS — the §M7 upper-bound caveat, R3 two-sidedness gate, the Sep-1 sunset over everything, our infra/size limits, and which 'snipes' need infra we don't have yet.
7. RECOMMENDED FIRST 3 MOVES — the cheapest, highest-EV things to actually test next (all reversible, operator-gated).

Rank ruthlessly but present completely. Every \$ figure R1+R3+§M7-labeled. Flag GUESSes. Return the full doc.`,
  { label: 'synthesize-menu', phase: 'Synthesize', effort: 'high' })

return {
  final,
  counts: { ground: G.length, raw_ideas: allIdeas.length, scored: scored.length, pursue: scored.filter(s => s.verdict === 'PURSUE').length, investigate: scored.filter(s => s.verdict === 'INVESTIGATE').length },
  top: scored.filter(s => s.score >= 4).sort((a, b) => b.score - a.score).slice(0, 15),
}
