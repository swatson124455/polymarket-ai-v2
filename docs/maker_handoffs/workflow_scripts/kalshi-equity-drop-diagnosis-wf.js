export const meta = {
  name: 'kalshi-equity-drop-diagnosis',
  description: 'Diagnose to the cent why total portfolio value fell ~$245->~$233 today: realized vs unrealized vs settlement vs fees, mark-to-mid-vs-adverse-drift, ongoing-vs-stabilized',
  phases: [
    { title: 'Ground', detail: 'clean instantaneous snapshot; reconcile equity bridge to the cent; establish trajectory' },
    { title: 'Diagnose', detail: 'is the unrealized markdown benign maker mark-to-mid (reward-compensated, lagged) or genuine adverse drift?' },
    { title: 'Refute', detail: 'adversarial: is it actually bleeding / ongoing / mismeasured?' },
    { title: 'Deliver', detail: 'verified answer + is-it-ongoing + what to do' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE. Worktree: ${WT} (branch claude/maker-kalshi-live). cd ${WT}/kalshi_live first; bash cwd drifts, use absolute paths.

=== HARD CONSTRAINTS ===
1. READ-ONLY. No deploys, no live.env writes, no orders, no systemctl, no config writes. GETs only.
   Kalshi authed reads work LOCALLY already: cd ${WT}/kalshi_live && python3 uses kalshi_attribution_ledger (module 'L').
   L.get(path) does one signed GET (0.6s spacing). L.get_paginated(L.P+path, key) follows cursors. L.P = '/trade-api/v2'.
   VPS (read-only) if needed: ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@18.201.216.0 'sudo ...'
2. CREATE ONLY NEW FILES. Do NOT edit any existing module. maker_kalshi_quoter.py stays md5 727ca7c59840a42b51c19e24c65a0982.
3. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.

=== VALIDATED TOOLING (reuse, do not reinvent) ===
kalshi_settlement_pnl.py (module 'S') is receipt-validated (Model B == venue realized_pnl_dollars 6/6 exact):
  S.replay(fills_for_one_ticker) -> (pos, avg_yes_cost, realized_pnl_before_fees, fees). yes-signed WAC lot accounting.
  S.carried_cost_basis(pos, avg) -> cost basis in venue terms (NO holding basis = pos*(1-avg)).
  S.settlement_row_pnl(row) -> lifetime realized P&L of a SETTLED contract (Model A).
  S.event_of / S.series_of.
kalshi_attribution_ledger.py (module 'L'): L.get, L.get_paginated, L.P.

=== ENDPOINTS + UNITS (canon sec.M7f; unit traps have burned this lane repeatedly) ===
/portfolio/balance -> {balance: CENTS free cash, portfolio_value: CENTS positions MARK}. UI "Portfolio" = balance+portfolio_value.
  So TOTAL EQUITY (mark) = (balance + portfolio_value)/100.
/portfolio/positions -> {market_positions:[...], event_positions:[...]}. market_positions fields:
  ticker; position_fp (STRING, IN CONTRACTS -- NOT centi: verified GASW-4.140 position_fp=40 * avg 0.710 = $28.40 exposure);
  market_exposure_dollars; realized_pnl_dollars (venue's OWN realized, AUTHORITATIVE); fees_paid_dollars; total_traded_dollars.
/portfolio/settlements -> settled contracts (revenue CENTS; yes_count_fp/no_count_fp are CUMULATIVE LIFETIME GROSS not position).
/portfolio/fills -> fills: ticker/market_ticker, action(buy/sell), side(yes/no), count_fp, yes_price_dollars, no_price_dollars, fee_cost, is_taker, created_time.
  NOTE: fills carry NO realized-pnl field. Realized churn is ONLY visible via tape replay (S.replay) or venue realized_pnl_dollars.
CAUTION: tape replay over ALL fills leaves PHANTOM residual positions on SETTLED contracts (settlement is not a fill).
  A truly-open position is one the venue lists in market_positions with nonzero position_fp. Filter to those; treat settled-contract tape residuals as closed (their P&L belongs to settlement, already realized).

=== THE QUESTION (operator, live, watching) ===
"Portfolio was ~$245, now ~$230." Total account MARK equity fell ~$12-15 today. WHY, to the cent, and IS IT STILL FALLING.
Reference measurements already taken this session (VERIFY them, they were taken inline under a moving target and one had a scale bug that was caught):
  - equity read $232.63 (18:48Z), $233.47 (18:59Z), $233.47 (19:01Z) -> looks STABILIZED ~$233, not free-falling. CONFIRM.
  - loss-meter equity_day_start = 247.54 (re-baselined 17:12Z after a +$150 operator deposit; NO deposits since).
  - today's ONLY settlement = -$7.47 at 12:25:36Z (KXAAAGASD-26JUL23, 4 contracts). Nothing settled after that. CONFIRM.
  - zero taker trades (TAKER_FLATTEN=0). CONFIRM from fills is_taker.
  - venue realized_pnl on the CURRENT open book markets ~= -$2.86 (GASD-4.110 -4.13 offset by GASW-4.140 +1.60).
  - current open book tape cost basis ~= $102 vs venue mark portfolio_value ~= $92 -> unrealized ~= -$10.
  - the 4x footprint expansion (10->40 markets, cap 85->250) happened 16:55-18:11Z; bot deployed ~$80-90 into inventory then.
Hypothesis to test, NOT assume: the drop is ~-$10 unrealized inventory markdown + ~-$3 realized churn on the GAS book, and the
  unrealized is largely benign maker mark-to-mid (buy at bid, mark to mid) whose compensating LIP reward credits are LAGGED
  (gas-daily posts at period close; GASW-26JUL27 credits not until after 2026-07-27) -- i.e. cost side visible, reward side not yet posted.

=== METHOD ===
* MEASURE, do not assume. If a number looks impossible it IS wrong -- this lane self-caught 6 measurement bugs last session.
* Reconcile the equity BRIDGE to the cent. Total equity change = realized(window) + Δunrealized(window) - fees + deposits.
  On a fully-collateralized venue this is an identity; if it doesn't close to the cent you have mismodelled something -- say what.
* Distinguish CASH movement (deployment/reservation, not loss) from EQUITY movement (real). Only mark equity answers the question.
* Distinguish benign mark-to-mid (maker half-spread, reward-compensated) from genuine ADVERSE drift (gas price moved against inventory / informed flow). Look at per-position entry vs current mid vs the market's price move over the window.
* State sample size / window / what is NOT covered on every number. Flag every GUESS.
`

phase('Ground')

const ground = await parallel([
  () => agent(`${RULES}

TASK -- CLEAN SNAPSHOT + EQUITY BRIDGE TO THE CENT.

DO:
1. Take ONE instantaneous snapshot: /portfolio/balance, /portfolio/positions, /portfolio/settlements, /portfolio/fills. Record the wall-clock.
2. TOTAL EQUITY now = (balance + portfolio_value)/100. Report it.
3. Decompose the account into: free cash; per-open-position (market_positions, nonzero position_fp) mark and tape cost basis (S.replay -> S.carried_cost_basis) -> current UNREALIZED = venue mark - Σ tape cost basis. Reconcile Σ cost basis and Σ market_exposure_dollars and portfolio_value against each other; explain any gap.
4. REALIZED today: settlements settled today (S.settlement_row_pnl) + venue realized_pnl_dollars on open-book markets. Give the total and by family (GAS vs TEMP vs KXAMSAVO vs other). Confirm the -$7.47 @ 12:25Z is the only settlement and it PREDATES the 17:12Z baseline.
5. Fees today (sum fee_cost on today's fills; confirm ~0 and that is_taker is false on all today's fills).
6. THE BRIDGE from the 17:12Z baseline (247.54, no deposits): 247.54 -> equity_now. Attribute the delta to realized(post-baseline) + Δunrealized + fees. Post-baseline settlements are ZERO, so realized-post-baseline is churn only. Does it close? If the identity leaves a residual, quantify it and name the most likely cause (reservation accounting? a position that turned over? a mismark?).

RETURN: total equity now (with timestamp), the unrealized number, the realized-today number by family, the fee number, and the bridge with its residual. Every number with its arithmetic.`,
    { label: 'ground:bridge', phase: 'Ground' }),

  () => agent(`${RULES}

TASK -- TRAJECTORY: WHEN did equity fall, and IS IT STILL FALLING?

We have no stored mark-equity time series (held_hist in quoter_state.json is cost-basis, 5 samples). Reconstruct the shape as best the data allows and, most importantly, determine the CURRENT trend.

DO:
1. Sample /portfolio/balance total equity NOW, then again after ~60-90s, then once more. Is it flat, falling, or rising minute-to-minute? This is the single most decision-relevant output -- ongoing bleed vs a settled markdown are very different situations.
2. From today's plans log on the VPS (sudo cat /opt/pa2-maker-kalshi-live/plans-$(date -u +%Y%m%d).jsonl), pull the trajectory of held_cost_usd / committed_usd / est_capital_usd / naked_held_usd / two_sided_markets / footprint across the day. Identify the expansion window (footprint 10->40) and correlate it with when capital was deployed.
3. From today's fills, bucket count + signed notional by hour to show when the bot deployed the current gas inventory. Cross-check the deployment timing against the ~16:55-18:11Z expansion.
4. If possible, estimate the mark-equity at ~17:12Z (baseline) and at the expansion start to confirm the ~$12-15 drop is post-expansion.

RETURN: the minute-to-minute CURRENT trend (flat/falling/rising with the three equity samples), the intraday capital-deployment trajectory, and when the drop most plausibly occurred. Be explicit about what could NOT be reconstructed.`,
    { label: 'ground:trajectory', phase: 'Ground' }),
])

log(`ground: ${ground.filter(Boolean).length}/2`)

phase('Diagnose')

const diagnose = await agent(`${RULES}

TASK -- IS THE UNREALIZED MARKDOWN BENIGN MARK-TO-MID OR GENUINE ADVERSE DRIFT? This is the crux of whether to worry.

BRIDGE: ${String(ground[0]).slice(0, 8000)}
TRAJECTORY: ${String(ground[1]).slice(0, 6000)}

A maker buys at its resting BID and the position immediately marks to MID -> an instant paper loss equal to ~half the spread, which is NOT a real loss: it is the liquidity the bot is paid (in LIP rewards) to provide. GENUINE adverse drift is different: the market's fair value moved against the inventory (informed flow / a gas print), and that is a real economic loss the rewards may or may not cover.

DO, per open GAS position (the current book is gas: GASD-26JUL24 + GASW-26JUL27):
1. For each, get entry avg (tape avg_yes_cost) vs the CURRENT market mid (fetch the book: /markets/{ticker} or the orderbook endpoint the quoter uses; yes_bid/yes_ask -> mid). Is (mid - entry) explained by the half-spread at entry, or larger (adverse)?
2. Did the underlying gas reference move over 16:55Z->now? The markets are strike ladders on a gas price; a coherent shift of the whole ladder = a real fair-value move (adverse or favorable), whereas per-strike mark-to-mid noise = benign. Distinguish.
3. Quantify: of the ~-$10 unrealized, how much is benign mark-to-mid (bounded by half-spread * size) vs residual adverse drift?
4. Factor in the LAGGED reward offset: today's GAS quoting accrues LIP credit that posts at period close (gas-daily) / after 2026-07-27 (gas-weekly). It is NOT in portfolio_value now. Estimate its rough magnitude ONLY as an upper-bound context (mark it a GUESS, do not present as booked) so the operator sees the cost side is shown without the reward side. NEVER quote rewards_residual.

RETURN: the split of the -$10 into benign vs adverse (with the per-position evidence), whether gas made a coherent adverse move, and the lagged-reward context as an explicit upper-bound/GUESS. State sample size and what is not covered.`,
  { label: 'diagnose', phase: 'Diagnose' })

phase('Refute')

const refutations = await parallel([
  'still-bleeding', 'reward-lag-story-is-cope', 'measurement-or-unit-error', 'adverse-selection-not-benign',
].map(lens => () => agent(`${RULES}

TASK -- ADVERSARIALLY REFUTE the diagnosis. Lens: **${lens}**. DEFAULT TO REFUTED IF UNCERTAIN.
The reassuring story ("benign mark-to-mid, rewards are just lagged, it's stabilized") is the one to attack. Real money is live.

BRIDGE: ${String(ground[0]).slice(0, 5000)}
DIAGNOSIS: ${String(diagnose).slice(0, 9000)}

Through your lens:
- still-bleeding: is equity actually still falling? Take fresh equity samples yourself over ~2-3 min. If it is trending down, the "stabilized" claim is false and this is ongoing loss, not a settled markdown. Also: will the growing footprint into the $250 cap keep deploying into more markdown?
- reward-lag-story-is-cope: is the lagged-reward offset real and sufficient, or a rationalization? What is the DEFENSIBLE upper bound on today's gas LIP accrual (from R1 $/day pools, NOT rewards_residual)? If even the optimistic reward can't cover the inventory markdown + churn, "rewards will catch up" is false comfort. Recall canon §M8: gas net was only +1.1% of notional -- thin.
- measurement-or-unit-error: re-derive the equity bridge independently. Check every unit (cents vs dollars, position_fp scale, count_fp, yes vs no price space). Does the bridge actually close, or was a residual hand-waved? Did settled-contract tape phantoms leak into "open" cost basis? Is portfolio_value really the positions mark?
- adverse-selection-not-benign: construct the case that the -$10 is NOT mark-to-mid but real adverse drift -- gas moved and the bot is holding the wrong side (the §M8 signature: positions carried toward resolution expiring worthless, now appearing on GAS not just temp). Is any current gas position already deep underwater on a coherent fair-value move?

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

TASK -- WRITE THE VERIFIED ANSWER. Operator asked, live: "portfolio was ~245 now ~230, why did it uptick (the bleed)."

BRIDGE: ${String(ground[0]).slice(0, 5000)}
TRAJECTORY: ${String(ground[1]).slice(0, 4000)}
DIAGNOSIS: ${String(diagnose).slice(0, 7000)}
REFUTATIONS: ${JSON.stringify(refutations.filter(Boolean), null, 1)}

Write \`docs/maker_handoffs/KALSHI_EQUITY_DROP_DIAGNOSIS_2026-07-23.md\` and return it. Structure:
1. ONE-LINE ANSWER + the drop decomposed to the cent (realized / unrealized / settlement / fees), with the equity bridge that closes.
2. IS IT STILL FALLING? -- the minute-to-minute trend, stated plainly. This governs urgency.
3. BENIGN vs REAL: how much of the unrealized is maker mark-to-mid (reward-compensated, lagged) vs genuine adverse drift. Lead with whatever the refuters escalated.
4. THE LAGGED-REWARD CONTEXT -- as an explicit upper-bound/GUESS from R1 pools, never rewards_residual; show that mid-period portfolio value shows cost without the not-yet-posted reward side (§M13 pattern).
5. WHAT (IF ANYTHING) TO DO -- options with reversibility. Candidates: nothing (benign, let rewards post); reduce MAX_TOTAL_CAPITAL to stop deploying into more markdown (reversible config, Tier-2, operator-gated); watch a specific position. Do NOT recommend a code deploy. Do NOT recommend cutting temp (gated to 2026-07-27, and temp is not even in the current book).
6. HONEST UNCERTAINTY -- what could not be verified, and the exact read-only commands for the operator to check themselves.

Report every refuter verdict, especially any CRITICAL 'still-bleeding' or 'adverse-selection' finding, even if it contradicts the benign story. Flag GUESSes. This is a diagnosis, not a change -- no deploy.`,
  { label: 'deliver', phase: 'Deliver' })

return { final, refutations: refutations.filter(Boolean) }
