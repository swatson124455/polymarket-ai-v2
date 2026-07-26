export const meta = {
  name: 'kalshi-onesided-review',
  description: 'No-assumptions deep review of one-sided quoting: map EVERY code path that produces a single-side quote, attribute each live one-sided order to its actual cause, quantify the reward cost + risk benefit, adversarially verify',
  phases: [
    { title: 'Ground', detail: 'code-map every one-sided path + live per-order cause attribution (no assumptions)' },
    { title: 'Analyze', detail: 'reward cost (R4 half-share) vs risk benefit (avoided fill exposure) vs net' },
    { title: 'Refute', detail: 'adversarial: is the cost overstated / benefit illusory / a bug not a feature / miscalibrated' },
    { title: 'Deliver', detail: 'verdict: why we are one-sided (attributed), what it costs/protects, change or keep' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = [
  'KALSHI MAKER LANE. Worktree: ' + WT + ' (branch claude/maker-kalshi-live). cd ' + WT + '; bash cwd drifts, use absolute paths.',
  '',
  '=== THE TASK (operator: "review one sided in depth, NO ASSUMPTIONS") ===',
  'The live market scorecard (kalshi_live/kalshi_market_scorecard.py) shows several markets where we rest only ONE side (rest column -Y or Y-), and some fully two-sided (YY). One-sided quoting roughly HALVES our R4 reward share on that market (we only score the side we rest). We need a rigorous, assumption-free review: WHY are we one-sided, per order; what it COSTS in reward; what it PROTECTS in risk; and whether it is the right call.',
  '',
  '=== CRITICAL: DO NOT ASSUME THE CAUSE ===',
  'The prior session ASSUMED the cause was "MAKER_ONESIDED_DERISK" — but that flag belongs to the POLYMARKET maker (claude/maker-bot), NOT this Kalshi bot. Do NOT carry that assumption. Find the ACTUAL Kalshi mechanism(s) in maker_kalshi_quoter.py by reading the code. Candidate causes to CHECK (confirm or rule out each with code + live evidence, assume none):',
  '  - REDUCE-ONLY / unwind: when we HOLD inventory in a market, do we quote only the reducing side? (the strand/unwind path).',
  '  - KALSHI_REDUCE_ONLY_KEEP_BOTH plugin (live.env has it =1): what does it actually do to one/two-sidedness? (the §M12 A/B two-sided plug-in).',
  '  - FILLS: one side filled and the other still rests -> transient one-sidedness (check fills vs resting).',
  '  - PRICE-BOUND / crossed gate: one side has no valid in-bounds price (best bid < 0.04 or > 0.96) so only the other side is quotable.',
  '  - FUNDING GATE (KALSHI_FUNDING_GATE=1, live): does it ever admit one side and skip the other?',
  '  - PIVOT-SELECT (KALSHI_PIVOT_SELECT=1, live, just deployed): does the backfill/qualification loop ever quote one side only?',
  '  - VELOCITY / HELD_MAX breaker reduce-only mode: whole-book reduce-only.',
  '  - Book one-sidedness: the market book itself is one-sided (nobody on a side) so our two-sided quote cannot rest.',
  'EACH live one-sided order MUST be attributed to a specific mechanism with evidence (do we hold inventory there? did a create fail on the missing side? is that side price-bound? is the book missing that side?). No hand-waving.',
  '',
  '=== TOOLS + DATA (READ-ONLY) ===',
  'Kalshi authed reads work locally: cd ' + WT + '/kalshi_live && python3, module L = kalshi_attribution_ledger. L.get(path), L.P="/trade-api/v2".',
  '  /portfolio/orders?status=resting -> our orders: fields ticker, outcome_side(yes/no), yes_price_dollars/no_price_dollars (DOLLARS), remaining_count_fp, fill_count_fp, initial_count_fp.',
  '  /portfolio/positions -> market_positions: position_fp (signed, in contracts), realized_pnl_dollars, market_exposure_dollars.',
  '  /portfolio/fills -> our fills (which side filled, when).',
  '  /markets/TICKER/orderbook -> orderbook_fp keys yes_dollars/no_dollars = [[price_str,size_str]] (best bid = max price).',
  '  /incentive_programs?status=active&limit=10000 -> pool/target/DF per market.',
  'The scorecard tool kalshi_market_scorecard.py already computes the R4 qualifying SHARE per side (reuse its qualifying_walk / qualifying_share logic to quantify the reward cost of one- vs two-sidedness). The deployed quoter is md5 1c68e130 (funding gate + pivot-select). Live config: KALSHI_FUNDING_GATE=1, KALSHI_PIVOT_SELECT=1, KALSHI_REDUCE_ONLY_KEEP_BOTH=1, MIN_DEPTH_SYM=0.25, MAX_SPREAD_TICKS=8. Read live.env for the full set: ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@18.201.216.0 "sudo grep -vE \'KEY|PRIVATE|SECRET\' /opt/pa2-maker-kalshi-live/live.env".',
  '',
  '=== METHOD ===',
  'MEASURE, do not assume. Cite maker_kalshi_quoter.py:line for every code claim; cite the live read for every data claim. If a number looks impossible it IS wrong. State sample size / instant. This lane has repeatedly shipped wrong conclusions from assumed causes (this session alone: an eligibility parse bug, a scorecard parse bug, the MAKER_ONESIDED_DERISK mis-attribution) — verify everything.',
  '=== SCOPE: READ-ONLY. No trades, deploys, config, or module edits. Analysis only. Kalshi venue only.',
].join('\n')

phase('Ground')

const ground = await parallel([
  () => agent(RULES + '\n\nGROUND TASK 1 — CODE MAP: read maker_kalshi_quoter.py and enumerate EVERY code path where the bot produces a ONE-SIDED (single outcome_side) quote for a market, or pulls one side. For each path give the exact line refs, the trigger condition, and classify it as ONE-SIDED-BY-DESIGN (a deliberate risk cut / unwind) vs ONE-SIDED-BY-SIDE-EFFECT (price-bound gate, fill, funding/pivot interaction, book one-sidedness). Specifically resolve: (a) the unwind/strand path (holding inventory -> reducing side only); (b) what KALSHI_REDUCE_ONLY_KEEP_BOTH actually does (does it FORCE both sides in reduce-only, or allow one-sided?); (c) whether the JOIN branch can ever return one side; (d) whether the price-bound gate (:482) drops a market entirely or can leave one quotable side; (e) whether funding-gate/pivot-select can admit one side and skip the other. RETURN the enumerated path list with line refs + design-vs-sideeffect classification + the plain-English rule for when we go one-sided.', { label: 'ground:code-map', phase: 'Ground', effort: 'high' }),

  () => agent(RULES + '\n\nGROUND TASK 2 — LIVE PER-ORDER ATTRIBUTION: pull our resting orders + positions + fills + books RIGHT NOW. For EVERY market we rest in, record which side(s) we rest (yes/no) and classify: TWO-SIDED, or ONE-SIDED. For each ONE-SIDED market, ATTRIBUTE the cause with evidence — check in order: do we hold nonzero inventory there (position_fp != 0 -> reduce-only/unwind, expected one-sided)? did the missing side recently fill (fills show a fill on the missing side -> transient)? is the missing side price-bound (its best bid < 0.04 or > 0.96 / book missing that side)? else UNEXPLAINED (flag for the code-map to explain). Tally: how many one-sided markets, and the cause breakdown (reduce-only / filled / price-bound / book-one-sided / unexplained). RETURN the per-market table + the cause tally + any UNEXPLAINED one-sided orders (these are the important ones).', { label: 'ground:live-attribution', phase: 'Ground', effort: 'high' }),
]).then(r => r.filter(Boolean))

const gd = ground.map((g, i) => '--- GROUND ' + ['CODE-MAP', 'LIVE-ATTRIBUTION'][i] + ' ---\n' + String(g).slice(0, 6000)).join('\n\n')
log('ground: ' + ground.length + '/2')

phase('Analyze')

const analysis = await agent(RULES + '\n\nGROUND:\n' + gd + '\n\nANALYZE TASK — the COST/BENEFIT of our current one-sidedness, quantified. Reuse the scorecard R4 machinery (kalshi_market_scorecard.qualifying_share). (1) REWARD COST: for each one-sided market that is NOT reduce-only (i.e. we are flat and CHOSE one side), compute our current R4 share vs the share we WOULD have if we rested the missing side too (two-sided). Sum the reward $/day left on the table (R1 pool x share delta; §M7 upper bound, divide ~3). Separate the reduce-only ones (one-sidedness there is CORRECT de-risk, not a cost). (2) RISK BENEFIT: what fill exposure does the pulled side avoid on those flat one-sided markets — i.e. what would we be risking if we rested both sides (the adverse-selection cost per our fingerprint ~ -$0.011/ct gas, worse elsewhere)? (3) NET: for the FLAT one-sided markets, is being one-sided net-positive (avoided fill loss > forgone reward) or net-negative (forgone reward > avoided loss)? Be explicit which markets are reduce-only (correct) vs flat-and-one-sided (a real cost/benefit tradeoff). RETURN the quantified cost, benefit, and per-regime net.', { label: 'analyze', phase: 'Analyze', effort: 'high' })

phase('Refute')

const refs = await parallel([
  'reward-cost-overstated', 'risk-benefit-illusory', 'its-a-bug-not-a-feature', 'attribution-wrong',
].map(lens => () => agent(RULES + '\n\nGROUND:\n' + gd + '\n\nANALYSIS:\n' + String(analysis).slice(0, 8000) + '\n\nADVERSARIALLY REFUTE the one-sided review. Lens: ' + lens + '. DEFAULT TO REFUTED IF UNCERTAIN.\n- reward-cost-overstated: is the "half share / $X/day forgone" real? Recall §M2 (our 20ct is never the marginal maker, share is small either way) and §M7 (model over-predicts 2-6x). Is the two-sided-vs-one-sided share delta actually material after the haircut, or noise?\n- risk-benefit-illusory: is the avoided fill exposure real? On a two-sided book our resting bid on the pulled side — would it actually get adversely filled, or just sit? Is one-sidedness protecting against a risk that would not materialize?\n- its-a-bug-not-a-feature: are ANY of the one-sided orders one-sided for a WRONG reason (a price-parse / gate / funding / pivot side-effect) rather than a deliberate de-risk? Re-attribute the UNEXPLAINED ones. A market where we SHOULD be two-sided but are not by accident is a reward bug.\n- attribution-wrong: re-check the live attribution independently — pull orders/positions/fills yourself. Are the reduce-only vs flat classifications correct? Is any "reduce-only" actually flat (or vice versa)? Does holding inventory really force one-sided in the code, or should we still quote both?\nRETURN refuted true/false, severity, the specific finding, and the check that settles it.', { label: 'refute:' + lens, phase: 'Refute', schema: { type: 'object', required: ['lens', 'refuted', 'severity', 'finding'], properties: { lens: { type: 'string' }, refuted: { type: 'boolean' }, severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NONE'] }, finding: { type: 'string' }, settling_check: { type: 'string' } } } })))

phase('Deliver')

const final = await agent(RULES + '\n\nGROUND:\n' + gd + '\n\nANALYSIS:\n' + String(analysis).slice(0, 6000) + '\n\nREFUTATIONS:\n' + JSON.stringify(refs.filter(Boolean), null, 1) + '\n\nWrite docs/maker_handoffs/KALSHI_ONESIDED_REVIEW_2026-07-24.md and return it. Structure: (1) WHY WE ARE ONE-SIDED — the attributed causes, per-order tally (reduce-only vs price-bound vs filled vs book-one-sided vs UNEXPLAINED), with the code rule for each; lead with anything UNEXPLAINED (that is a bug). (2) WHAT IT COSTS — the quantified reward forgone on FLAT one-sided markets (R4 share delta, §M7-haircut), separated from reduce-only (which is correct de-risk, $0 cost). (3) WHAT IT PROTECTS — the avoided fill/adverse exposure, honestly (refuters may say it is illusory). (4) NET VERDICT — is our current one-sidedness right, a bug, or a miscalibrated tradeoff? Lead with any refuter CRITICAL/HIGH. (5) RECOMMENDATION — keep / fix a bug / change a config (name the exact knob, Tier-2 operator-gated) — no code deploy from this review. Every $ §M7-labeled, every claim code:line or live-read cited, flag GUESSes. Return the doc.', { label: 'deliver', phase: 'Deliver', effort: 'high' })

return { final, refs: refs.filter(Boolean) }
