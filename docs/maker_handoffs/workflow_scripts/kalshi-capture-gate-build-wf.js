export const meta = {
  name: 'kalshi-capture-gate-build',
  description: 'Automatic market-quality gate: compute our PROSPECTIVE reward capture (R4 share x pool) per market in-cycle, and auto-skip (flat) / reduce-only (holding) any market where we cannot actually earn — no manual pulls. Behind an off-by-default flag, tested + adversarially verified',
  phases: [
    { title: 'Design', detail: 'prospective-capture formula (our size at reference vs book qualifying score), the floor, skip-vs-reduce, composition with existing gates + stand-down' },
    { title: 'Implement', detail: 'code behind KALSHI_CAPTURE_GATE (default 0 = no-op) + pin tests + pytest, commit' },
    { title: 'Verify', detail: 'adversarial: flag-off no-op, never blocks exits, does not starve gas, actually auto-skips the dead markets (Trump A15 / saturated pools)' },
    { title: 'Deliver', detail: 'ship checklist, md5, deploy+rollback, calibration plan' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = [
  'KALSHI MAKER LANE. Worktree: ' + WT + ' (branch claude/maker-kalshi-live). cd ' + WT + '; bash cwd drifts, use absolute paths.',
  '',
  '=== THE DIRECTIVE (operator, verbatim intent) ===',
  '"Stop asking to pull specific markets. The calibration and the reviews of the liquidity we can attack should AUTOMATICALLY stop us if the market is poor." So: wire the market-quality assessment INTO the bot as an automatic gate. The bot must, per market, judge whether WE can actually capture reward, and auto-skip (when flat) / go reduce-only (when holding) any market that is POOR for us -- no human pulling individual tickers.',
  '',
  '=== WHY THE EXISTING GATES ARE NOT ENOUGH (measured) ===',
  'maker_kalshi_quoter.py already has an unqualifiable gate (:504) that skips a market whose BOOK cannot reach two-sided Target Size ("can anyone earn here"). It does NOT check whether WE earn. Live scorecard (kalshi_live/kalshi_market_scorecard.py) proved the gap: e.g. KXTRUMPENDORSEMENTS-26JUL25-A15 holds ~$16.72 capital with a two-sided book but our R4 qualifying SHARE is 0 (our order is off-reference / below the qualifying cutoff) -> $0 reward FOR US, pure risk. And the god-mode dive found saturated big pools (KXFUNDRAISING $2,501/day) where our 20ct share ~= 0 (rivals fill the 1000-ct target). The existing gate passes both; a CAPTURE gate would skip both.',
  '',
  '=== THE R4 CAPTURE MODEL (already implemented in the scorecard -- reuse it) ===',
  'kalshi_market_scorecard.qualifying_share(bids, our_price, our_size, target, df): reference = highest bid (<1.0); walk down accumulating size to Target; score = DF^N * size (N = ticks from ref); our normalized share = our_score / total_score; returns (share, side_qualifies). Our snapshot score = (yes_share + no_share)/2 when BOTH sides qualify (R3). Expected reward $/day for us on a market = pool_usd_day * our_snapshot_score (R1 pool, R3-gated). This is a MODEL (M7: over-predicts 2-6x) -- use it as a RELATIVE gate signal, and CALIBRATE the floor against actual period-close credits, do not treat as a ledgered EV.',
  '',
  '=== CRITICAL DESIGN SUBTLETY -- PROSPECTIVE not CURRENT share ===',
  'Our CURRENT share in a market we have not quoted yet is 0 (no order in the book). A naive "skip if current share == 0" would skip everything and never open. The gate MUST compute our PROSPECTIVE share: if we rested our intended size at the qualifying reference, what share would we score = our_size scored at reference / (book qualifying score + our contribution). Compute it from the book the bot ALREADY fetches in-cycle (no new API read) + our intended quote size. The gate then compares prospective expected-reward-$/day to a FLOOR.',
  '',
  '=== THE GATE (design target) ===',
  'New flag KALSHI_CAPTURE_GATE (_envi, DEFAULT 0 = OFF = provable no-op). When ON, per market, before OPENING (accumulating), compute prospective_capture_usd_day = prospective_our_snapshot_share * pool_usd_day. If prospective_capture_usd_day < KALSHI_CAPTURE_MIN_USD_DAY (floor, a config, starting value to be justified by the design -- likely a few $/day, calibrated later), the market is POOR FOR US: do NOT open there (skip when FLAT); if we HOLD inventory, go reduce-only (exit, never add). Requirements: (1) EXITS/reducing/unwind NEVER blocked or downsized -- de-risk always proceeds. (2) Must NOT starve GAS on a normal day -- gas near-money strikes have real prospective capture; calibrate the floor so gas passes. (3) Uses ONLY in-cycle data (the book already fetched + our intended size) -- NO extra API reads. (4) Observable: emit plan telemetry (capture_gate, capture_skipped_markets, per-market prospective_capture where useful). (5) Composes with the existing unqualifiable/void/selection gates, funding gate (KALSHI_FUNDING_GATE=1 live), pivot-select (KALSHI_PIVOT_SELECT=1 live). NOTE: a pool-only stand-down (KALSHI_STANDDOWN, built, NOT deployed) uses pool DENSITY only; THIS capture gate uses our actual SHARE x pool and is the more complete signal -- the design should state whether it SUPERSEDES the stand-down or composes with it (prefer: this is the primary market-quality gate; stand-down can stay off).',
  '',
  '=== FLAG + NO-OP ===',
  'KALSHI_CAPTURE_GATE default 0 = today exact behavior byte-for-byte; deploys with zero live change until flipped. Telemetry only when on.',
  '',
  '=== HARD CONSTRAINTS ===',
  '1. Edit ONLY maker_kalshi_quoter.py + a new test file. No deploy, no live.env, no ssh-write, no systemctl. Worktree edit does not affect the running VPS bot.',
  '2. Preserve every signature; EXITS/reducing + all existing guards + funding gate + pivot-select + loss meter unchanged. One behavioral change behind one flag.',
  '3. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.',
  '4. COMMIT when green: git -C ' + WT + ' add kalshi_live/maker_kalshi_quoter.py kalshi_live/test_capture_gate.py && git -C ' + WT + ' commit -m "feat(kalshi): capture gate behind KALSHI_CAPTURE_GATE (default off) — auto-skip/reduce markets where our prospective R4 capture is below floor [NOT DEPLOYED]". Return sha + full diff + pytest output.',
  '5. Kalshi authed reads work locally for measurement (cd ' + WT + '/kalshi_live && python3, module L). Orderbook: L.get(L.P+"/markets/TICKER/orderbook")["orderbook_fp"] keys yes_dollars/no_dollars = [[price_str,size_str]]. Reuse kalshi_market_scorecard.qualifying_share.',
  '',
  '=== TESTS (kalshi_live/test_capture_gate.py) ===',
  '1. FLAG-OFF NO-OP: KALSHI_CAPTURE_GATE unset/0 -> identical quotes/plan to legacy on a fixture with a poor market. Assert against the legacy path.',
  '2. THE FIX PIN: a fixture like Trump A15 -- book two-sided but our prospective share ~0 (our size negligible vs a deep rival book OR reference far from our price). Flag OFF: bot opens/keeps quoting it. Flag ON: bot SKIPS it (flat) / reduce-only (holding). FAILS on legacy, PASSES on fix.',
  '3. NEVER-BLOCKS-EXITS: holding inventory in a poor market, flag ON must STILL rest the reducing/unwind quote at full size. Assert the reducing quote present.',
  '4. DOES-NOT-STARVE-GAS: a normal near-money gas fixture with real prospective capture (share x pool >> floor). Flag ON KEEPS quoting it full-size. Assert quotes remain.',
  'Run the FULL suite: cd ' + WT + '/kalshi_live && python -m pytest test_*.py -q. ALL must pass.',
].join('\n')

phase('Design')

const design = await agent(RULES + '\n\nTASK — DESIGN THE CAPTURE GATE. Read maker_kalshi_quoter.py:460-560 (quote-gen + gates, where desired_quotes decides sides/sizes), the qualifying-walk logic the bot uses (maker_kalshi_recorder.qualifying_walk if present, else the scorecard), and how the book + our intended size are available in-cycle at the point of the gate. Derive the EXACT prospective-capture formula (our intended size scored at the reference vs the book qualifying score; handle both sides; the (yes+no)/2 snapshot; R3 two-sided requirement). Specify the floor config + a justified starting value, the skip-vs-reduce-only branch (compose with the existing inventory-unwind early return so exits are never touched), the flag-off no-op proof, and the 4 pin-test fixtures concretely. State whether it supersedes KALSHI_STANDDOWN. Prefer the SIMPLEST design that provably auto-skips the dead markets without starving gas. RETURN the design.', { label: 'design', phase: 'Design', effort: 'high' })

phase('Implement')

const impl = await agent(RULES + '\n\nDESIGN TO IMPLEMENT:\n' + String(design).slice(0, 12000) + '\n\nTASK — IMPLEMENT the capture gate exactly per the design + 4 pin tests. Flag KALSHI_CAPTURE_GATE default 0 = provable no-op. Never block/downsize EXITS. Use only in-cycle data (no extra API reads). Do NOT touch funding gate, pivot-select, loss meter, velocity breaker, or existing gates except to compose. Run the FULL pytest suite; fix until green (or flag a pre-existing unrelated failure with evidence). Commit. RETURN commit sha, the COMPLETE unified diff of maker_kalshi_quoter.py + the test file, the exact pytest summary, and a 4-line plain description + why flag-off is a no-op.', { label: 'implement', phase: 'Implement', effort: 'high' })

phase('Verify')

const verds = await parallel([
  'flag-off-is-a-noop', 'never-blocks-exits', 'does-not-starve-gas', 'actually-skips-the-dead-markets',
].map(lens => () => agent(RULES + '\n\nTASK — ADVERSARIALLY VERIFY the capture gate. Lens: ' + lens + '. DEFAULT TO REFUTED IF UNCERTAIN. Live-money change. Read the committed diff READ-ONLY: git -C ' + WT + ' show HEAD and git -C ' + WT + ' diff HEAD~1 HEAD -- kalshi_live/maker_kalshi_quoter.py. Do NOT edit.\n- flag-off-is-a-noop: prove KALSHI_CAPTURE_GATE unset/0 gives byte-identical quotes/plan to legacy. Any divergence off = REFUTED.\n- never-blocks-exits: find ANY path where flag-ON blocks/downsizes a reducing/unwind/exit quote. De-risk must ALWAYS proceed. If inventory can be trapped, REFUTE (deadliest).\n- does-not-starve-gas: prove flag-ON still quotes near-money gas at full size (real prospective capture >> floor). A false skip of our only edge is a REFUTE. Check the prospective-share math does not understate gas.\n- actually-skips-the-dead-markets: prove flag-ON WOULD skip/reduce the genuinely poor markets (prospective capture ~0: Trump A15-style off-reference, saturated deep-rival pools). If it does not actually catch them, REFUTE -- it must earn its complexity. Also check the PROSPECTIVE (not current) share is used, or it would skip everything unquoted.\nRETURN refuted true/false, severity, the specific state, the check that settles it.', { label: 'verify:' + lens, phase: 'Verify', schema: { type: 'object', required: ['lens', 'refuted', 'severity', 'finding'], properties: { lens: { type: 'string' }, refuted: { type: 'boolean' }, severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NONE'] }, finding: { type: 'string' }, failing_state: { type: 'string' }, settling_check: { type: 'string' } } } })))

phase('Deliver')

const final = await agent(RULES + '\n\nIMPLEMENTATION:\n' + String(impl).slice(0, 8000) + '\n\nVERIFIER VERDICTS:\n' + JSON.stringify(verds.filter(Boolean), null, 1) + '\n\nTASK — write docs/maker_handoffs/KALSHI_CAPTURE_GATE_BUILD_2026-07-24.md and return it: (1) what changed plain English + flag-off no-op proof; (2) every verifier verdict, lead with any CRITICAL/HIGH (esp. never-blocks-exits or starve-gas); (3) if green: pytest counts, new-file md5 (git -C ' + WT + ' show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum), per-file md5-gated deploy step, rollback (flag unset = no-op / git revert); (4) expected LIVE effect when flipped on (dead markets like Trump A15 auto-skip/reduce, saturated pools skipped, gas kept, exits proceed); (5) THE CALIBRATION PLAN — the floor is a starting value; run flag-on in shadow, compare per-market prospective capture telemetry to the next period-close actual credits, tune KALSHI_CAPTURE_MIN_USD_DAY before trusting it; (6) Tier-3 -- operator sign-off + md5-gated deploy before live. Return the doc.', { label: 'deliver', phase: 'Deliver', effort: 'high' })

return { final, verds: verds.filter(Boolean), implPreview: String(impl).slice(0, 1200) }
