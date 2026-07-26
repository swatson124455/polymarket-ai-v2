export const meta = {
  name: 'kalshi-profitability-crossmatrix',
  description: 'Deep full bot review: cross-matrix the base bot + all 6 behavioral additions (with/without Combo) against the receipt-calibrated economics to answer ONE question — does ANY combination make money, or is the maker fundamentally unprofitable at 20ct size?',
  phases: [
    { title: 'Ground', detail: 'receipt-calibrated per-family net economics; each addition mechanism + its reward/cost sign; base-bot economics; constraints (size/sunset/Combo)' },
    { title: 'Matrix', detail: 'evaluate the meaningful functional stacks (combinations) — expected net EV per stack, with and without Combo' },
    { title: 'Refute', detail: 'adversarial: is any positive stack real or model-optimism; is the base economics unfixable at our size' },
    { title: 'Deliver', detail: 'the verdict — which combination makes money (if any) or "none without X"; full honest bot review' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = [
  'KALSHI MAKER LANE — FULL PROFITABILITY REVIEW. Worktree: ' + WT + ' (branch claude/maker-kalshi-live). cd ' + WT + '/kalshi_live for reads; bash cwd drifts, use absolute paths.',
  '',
  '=== THE QUESTION (operator) ===',
  '"Cross-matrix all items and additions to see if any mixing — or none — will lead to making money. Deep dive, full review of the bot." Answer ONE thing rigorously: is there ANY configuration (base bot + any subset of the 6 behavioral additions, with or without Combo) that is NET-POSITIVE, or is this maker fundamentally unprofitable at our size? No optimism — receipts over model. The bot was WOUND DOWN today after a ~-$47 day; this review decides whether to rebuild, reconfigure, or retire it.',
  '',
  '=== THE MEASURED ECONOMICS (receipts, not model — this is the foundation) ===',
  'The strategy earns LIP REWARD (paid for resting two-sided quotes at snapshots) and pays ADVERSE-SELECTION COST (fills) + settlement + fees. Receipt-calibrated per-family net (kalshi_live/kalshi_netev_calibrate.py on the 07-23 CSV, reproduces §M8):',
  '  GAS  net = +1.1% of notional (+$1.20/day): trading +$0.25 + credits +$2.15 on $214.85 notional. Confidence: receipt. Razor thin.',
  '  TEMP net = -9.2% of notional (-$6.53/day): trading -$36.12 + credits +$23.06 on $142.67 notional. ⚠ OPERATOR FLAGGED THIS DATA AS CONTAMINATED by past bugs/go-live errors/config churn (07-21..22 predates the delta-neutral fixes; §M8 itself said "not a clean experiment", §M13 withdrew "net negative"). Treat temp net as QUARANTINED/UNKNOWN, not a clean -9.2%. Do NOT conclude "temp loses" from contaminated data — it is UNPROVEN.',
  'Our reward SHARE is tiny: 20ct vs a 1000-ct Target Size means we are NEVER the marginal maker (measured 0/304); live scorecard shows 1-7% share per market. Reward accrual measured ~$1/hour (lagged, MODEL, apply §M7 3x haircut). Today: -$34.98 realized on a gas settlement (naked ladder inventory carried into resolution) + ~$12 paper.',
  'Reward $ are §M7 UPPER BOUNDS (model over-predicts 2-6x). Credits LAG a Time Period. Fills on our fee-free series are $0 fee (taker rows are forced exits). Sep-1 SUNSET: both LIP + Volume incentive programs expire 2026-09-01 (~5.5 weeks) — any profitable config has a hard ~6-week horizon.',
  '',
  '=== THE 6 BEHAVIORAL ADDITIONS (all built this session, all reverted off the live bot, on branch behind default-off flags) — mechanism + reward/cost sign ===',
  '1. FUNDING-GATE (de224fc, KALSHI_FUNDING_GATE): gate new buys on FREE CASH not the gross-inflated cap -> frees idle capital to deploy. EFFECT: more deployment -> MORE reward AND MORE fills(cost). Sign: AMBIGUOUS (activity amplifier).',
  '2. PIVOT-SELECT (4c731f6, KALSHI_PIVOT_SELECT): backfill the footprint with EARNING near-money strikes instead of quoting fewer -> more markets quoted. EFFECT: more reward AND more churn (MEASURED to increase the visible bleed today). Sign: AMBIGUOUS/activity amplifier.',
  '3. STAND-DOWN (62f39e9, KALSHI_STANDDOWN): size-down / reduce-only when the POOL reward density is below a floor. EFFECT: less activity on thin days -> less cost, less reward. Sign: COST-REDUCING (protective). Superseded by net-EV.',
  '4. CAPTURE-GATE (eac0443, KALSHI_CAPTURE_GATE): skip markets where OUR prospective R4 capture (share x pool) is below a floor. EFFECT: avoid deploying where we cannot earn. Sign: COST-REDUCING. Superseded by net-EV.',
  '5. NET-EV GATE (f17c0c2, KALSHI_NETEV_GATE): skip families whose receipt-calibrated NET (credits - fill P&L) is negative; keep net-positive (gas). EFFECT: trade only the WINNERS. Sign: COST-REDUCING / the core selector. (But depends on clean calibration — temp is quarantined/unknown.)',
  '6. PRE-CLOSE FLATTEN (d9dfbee, KALSHI_PRECLOSE_FLATTEN): exit the NAKED (unpaired) residual before market close so it never rides into settlement. EFFECT: cuts the SETTLEMENT loss (today -$35, the single biggest leak). Sign: LOSS-CUTTING (the biggest one). Taker cost ~$1/40ct residual.',
  'PLUS the external lever — COMBO INCENTIVE (not built; email drafted not sent): pays makers for FILL VOLUME, confirmed stacks with LIP, rate UNKNOWN. EFFECT: pays for the fills that are currently pure COST -> directly flips the cost side. Sign: the potential game-changer, magnitude unknown.',
  'NOTE supersession: net-EV subsumes stand-down + capture-gate (all three are "don\'t trade where we lose"). Funding-gate + pivot are the two ACTIVITY amplifiers. Pre-close is orthogonal (loss-cut). So the real matrix axes are: {activity amplifiers on/off} x {selective cost-control on/off} x {settlement loss-cut on/off} x {Combo on/off}.',
  '',
  '=== DATA (READ-ONLY) ===',
  'Kalshi authed reads local: cd ' + WT + '/kalshi_live && python3, module L. kalshi_netev_calibrate.calibrate (per-family net), kalshi_market_scorecard (per-market R4 share + fill P&L), kalshi_settlement_pnl (realized). Build docs: docs/maker_handoffs/KALSHI_*_BUILD_2026-07-24.md. The 07-23 CSV: kalshi_live/kalshi_transactions_2026-07-23.csv.',
  '',
  '=== METHOD ===',
  'RECEIPTS over model. If a stack "makes money" only on the §M7-inflated model, it does NOT make money — say so. State the sign AND rough magnitude of every reward/cost delta per stack, anchored to the receipt economics (gas +1.1% thin, our share tiny, ~$1/hr reward, settlement tails). A stack cannot conjure reward that the receipts do not support. Sunset over everything. Flag every GUESS. This lane self-caught 6 measurement bugs + several wrong calls — verify, do not assume. READ-ONLY: no trades/deploys/config/edits.',
].join('\n')

phase('Ground')

const ground = await parallel([
  () => agent(RULES + '\n\nGROUND 1 — THE RECEIPT ECONOMICS, PINNED. Re-run kalshi_netev_calibrate on the 07-23 CSV and confirm the per-family net (gas +1.1%, temp -9.2%-but-QUARANTINED). Then decompose the UNIT economics: per-contract reward we actually capture (our R4 share x pool, receipt-anchored via §M7 receipts $10.09/gas-event) vs per-contract adverse-selection cost (fill fingerprint) vs settlement risk per event vs fees. What is the reward-per-fill vs cost-per-fill, and the reward-per-day at our current footprint, in RECEIPT terms (not model)? What would it take (share, size, or cost) to make one gas market net-positive after ALL costs? RETURN the unit economics table + the break-even conditions.', { label: 'ground:economics', phase: 'Ground', effort: 'high' }),

  () => agent(RULES + '\n\nGROUND 2 — EACH ADDITION QUANTIFIED. For each of the 6 additions (+ Combo), from its build doc + the measured behavior, quantify its effect on (a) reward $/day and (b) cost $/day, in receipt terms with sign and rough magnitude. Which are net-POSITIVE standalone, net-NEGATIVE, or sign-ambiguous? Specifically: does pivot/funding (activity) add more reward than churn cost at our tiny share? does net-EV/capture actually cut losers (and can it, given temp is quarantined)? does pre-close flatten\'s settlement saving (~$35 events) exceed its taker cost (~$1)? what is the plausible Combo rate range and does it flip the per-fill sign? RETURN a per-addition reward/cost/net table with magnitudes + confidence.', { label: 'ground:additions', phase: 'Ground', effort: 'high' }),

  () => agent(RULES + '\n\nGROUND 3 — THE HARD CONSTRAINTS + THE BASE-BOT NET. What is the BASE bot (727ca7c5, all additions off) net EV in receipt terms — is it positive, break-even, or negative, and driven by what (gas thin-positive, temp quarantined, settlement tails)? Then the binding constraints any profitable config must clear: (a) SIZE — at 20ct/1000-Target our share is ~2%, capped by §M2; would 2x/5x/10x size or capital change the share/net materially or hit R3/competition ceilings? (b) SUNSET — Sep-1, ~5.5 weeks; does any config pay back inside that? (c) the reward is LAGGED + tiny + model-inflated. RETURN the base-bot net, the size/scale sensitivity, and the sunset payback envelope.', { label: 'ground:constraints', phase: 'Ground', effort: 'high' }),
]).then(r => r.filter(Boolean))

const gd = ground.map((g, i) => '--- GROUND ' + ['ECONOMICS', 'ADDITIONS', 'CONSTRAINTS'][i] + ' ---\n' + String(g).slice(0, 5000)).join('\n\n')
log('ground: ' + ground.length + '/3')

phase('Matrix')

const STACKS = [
  { key: 'base-only', desc: 'Base bot, all additions off (the wound-down 727ca7c5 baseline).' },
  { key: 'activity-only', desc: 'Funding-gate + pivot-select ON, no cost control (max activity — this is roughly what bled today).' },
  { key: 'selective-only', desc: 'Net-EV + capture-gate ON (trade only net-positive/high-capture markets), no activity amplifiers.' },
  { key: 'lossscut-only', desc: 'Pre-close flatten ON only (cut settlement loss), everything else base.' },
  { key: 'full-defensive', desc: 'Net-EV + capture-gate + pre-close flatten (winners only, no settlement loss), activity amplifiers OFF.' },
  { key: 'full-offensive', desc: 'Everything ON: funding + pivot + net-EV + capture + pre-close (max activity WITH all controls).' },
  { key: 'defensive-plus-combo', desc: 'Full-defensive + COMBO opted in (winners only, no settlement loss, PAID for fills).' },
  { key: 'offensive-plus-combo', desc: 'Full-offensive + COMBO (max activity, all controls, paid for fills).' },
  { key: 'combo-only', desc: 'Base bot + COMBO only (paid for fills, no other changes).' },
]

const matrix = await parallel(STACKS.map(st => () => agent(RULES + '\n\nGROUND:\n' + gd + '\n\nMATRIX CELL — evaluate this configuration for NET PROFITABILITY (receipt terms, §M7-haircut applied, honest sign + magnitude): **' + st.key + '** = ' + st.desc + '\nGiven the unit economics (reward-per-fill vs cost-per-fill, gas +1.1% thin, our ~2% share, settlement tails, ~$1/hr reward, Sep-1 sunset): estimate this config\'s net $/day (a range is fine), state WHY (which deltas dominate), and classify it: PROFITABLE / BREAK-EVEN / NET-NEGATIVE / UNKNOWN-PENDING-DATA. Be explicit whether the sign depends on the unknown Combo rate or the quarantined temp data. Do NOT let §M7 model optimism turn a receipt-negative config positive. RETURN {config, net_per_day_range, classification, dominant_driver, depends_on}.', { label: 'matrix:' + st.key, phase: 'Matrix', effort: 'high', schema: { type: 'object', required: ['config', 'net_per_day_range', 'classification', 'dominant_driver'], properties: { config: { type: 'string' }, net_per_day_range: { type: 'string' }, classification: { type: 'string', enum: ['PROFITABLE', 'BREAK-EVEN', 'NET-NEGATIVE', 'UNKNOWN-PENDING-DATA'] }, dominant_driver: { type: 'string' }, depends_on: { type: 'string' } } } })))

const cells = matrix.filter(Boolean)
log('matrix: ' + cells.length + '/' + STACKS.length + ' cells')

phase('Refute')

const refs = await parallel([
  'best-positive-stack-is-model-optimism', 'base-economics-unfixable-at-our-size', 'combo-wont-save-it', 'a-profitable-config-was-missed',
].map(lens => () => agent(RULES + '\n\nGROUND:\n' + gd + '\n\nMATRIX CELLS:\n' + JSON.stringify(cells, null, 1) + '\n\nADVERSARIALLY REFUTE the matrix. Lens: ' + lens + '. DEFAULT TO REFUTED IF UNCERTAIN.\n- best-positive-stack-is-model-optimism: take whichever stack the matrix rated best/PROFITABLE and prove its positivity rests on §M7-inflated reward or the unknown Combo rate, not receipts. What is its net using ONLY receipt-grade numbers?\n- base-economics-unfixable-at-our-size: argue the additions only REDUCE losses (cost-control, loss-cut) and cannot CREATE reward beyond our tiny fixed ~2% share — so the ceiling is ~gas +1.1% on a small book, i.e. a few $/day at best, dwarfed by variance (a single -$35 settlement tail = weeks of that). Is the whole thing sub-scale?\n- combo-wont-save-it: even paid for fills, is the Combo pool so diluted (pro-rata across the venue) that our tiny volume earns trivial Combo, insufficient to flip the sign? What Combo rate would be REQUIRED, and is it plausible?\n- a-profitable-config-was-missed: is there a config or a lever NOT in the 9 cells (e.g. a different size, a single-market concentration, a non-LIP edge) that WOULD be profitable? If the honest answer is no config works, say so; if one was missed, name it.\nRETURN refuted true/false, severity, the specific finding, the settling number.', { label: 'refute:' + lens, phase: 'Refute', schema: { type: 'object', required: ['lens', 'refuted', 'severity', 'finding'], properties: { lens: { type: 'string' }, refuted: { type: 'boolean' }, severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NONE'] }, finding: { type: 'string' }, settling_number: { type: 'string' } } } })))

phase('Deliver')

const final = await agent(RULES + '\n\nGROUND:\n' + gd + '\n\nMATRIX:\n' + JSON.stringify(cells, null, 1) + '\n\nREFUTATIONS:\n' + JSON.stringify(refs.filter(Boolean), null, 1) + '\n\nWrite docs/maker_handoffs/KALSHI_PROFITABILITY_REVIEW_2026-07-24.md and return it. Structure: (1) THE VERDICT IN ONE LINE — does ANY configuration make money, and if so which, if not why. (2) THE CROSS-MATRIX — a table of all 9 configs: net $/day (receipt terms), classification, dominant driver, what it depends on. Lead with the best cell and the honest caveat. (3) THE UNIT-ECONOMICS TRUTH — reward-per-fill vs cost-per-fill, our ~2% share ceiling, the gas +1.1% thin base, why the additions REDUCE loss but cannot CREATE reward beyond our fixed share. (4) THE THREE THINGS THAT COULD CHANGE THE ANSWER, ranked: Combo rate (needs the email + observation), size/scale (does bigger help or hit the R3/competition ceiling), clean temp recalibration (07-27 CSV). (5) THE REFUTERS\' VERDICTS — lead with any that says "unfixable at our size" or "Combo won\'t save it". (6) THE RECOMMENDATION — one of: REBUILD (a specific profitable config exists — name it + the deploy order), RECONFIGURE (a defensive config break-evens, worth running only for the option value till sunset), or RETIRE (no config makes money at our size; stop). Be brutally honest — the operator wound the bot down and needs to know if it is worth restarting AT ALL. Every $ receipt-anchored or flagged §M7/GUESS. Return the doc.', { label: 'deliver', phase: 'Deliver', effort: 'high' })

return { final, cells, refs: refs.filter(Boolean) }
