export const meta = {
  name: 'kalshi-standdown-build',
  description: 'Stop bleeding on dead days: an EV-aware stand-down that opens LESS (size-down / reduce-only) when expected reward does not justify expected adverse-selection cost — behind an off-by-default flag, tested + adversarially verified',
  phases: [
    { title: 'Design', detail: 'pick the mechanism (per-market EV gate vs regime size-scale vs fast micro-breaker), grounded in our fingerprint; must not forfeit gas edge; composes with pivot-select' },
    { title: 'Implement', detail: 'code it behind KALSHI_STANDDOWN (default 0 = no-op) + pin tests + pytest, commit' },
    { title: 'Verify', detail: 'adversarial: flag-off no-op, never blocks EXITS, does not forfeit +EV gas, actually cuts the dead-day bleed' },
    { title: 'Deliver', detail: 'ship checklist, md5, deploy+rollback, expected live effect' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = [
  'KALSHI MAKER LANE. Worktree: ' + WT + ' (branch claude/maker-kalshi-live). cd ' + WT + '; bash cwd drifts, use absolute paths.',
  '',
  '=== THE PROBLEM (operator, verbatim intent) ===',
  'The bot has NO brain for "should I even be playing right now?". It farms LIP mechanically. On a day when temp (our main reward, ~91% of reward income) is DARK, it is left churning thin gas — and every gas fill is a small adverse-selection loss. With no reward to cover those losses and no logic to stand down, it plays a losing hand and bleeds (measured: ~-$9 on 2026-07-23, mostly one ATM gas strike 4.110 getting run over by one-way flow). Nothing tells it to fold.',
  'Operator: "if the rewards arent there we pivot to another trade / dont force a losing trade" and "the bot loses money if its not on weather, that is a fucking issue". BUILD the missing stand-down: when expected reward does not justify expected fill loss, OPEN LESS (size-down or reduce-only) so a dead day costs ~$0 instead of bleeding.',
  '',
  '=== GROUND TRUTH (measured this lane) ===',
  '- Our realized trading edge BEFORE rewards is NEGATIVE (adverse selection): fingerprint ~ -$0.011/contract on GAS (our least-toxic ladder), far worse on temp. LIP rewards are the ONLY thing that makes the strategy +EV.',
  '- GAS net (incl. gas rewards) was ~ +1.1% of notional in the M8 window — razor thin, break-even-ish. So gas is SLIGHTLY +EV on a normal day; a stand-down must NOT blanket-shut gas or it forfeits the only edge.',
  '- Rewards are LAGGED (post at Time-Period close), so real-time realized reward is not observable intra-period. Reward AVAILABILITY (the LIP pool $/day on the markets we quote, R1-normalized, R3-adjusted for two-sidedness) IS observable live.',
  '- The bleed mechanism is per-strike adverse selection (an ATM strike getting lifted repeatedly by one-way flow), not fast inventory growth — so the existing VELOCITY breaker (held-$ growth) and the $40 DAILY-LOSS halt do NOT catch a slow adverse grind while rewards are absent. This is the gap.',
  '- Existing guards to compose with, NOT duplicate: unqualifiable gate (maker_kalshi_quoter.py:504, skips $0-reward books), void/selection gates (:508-547), velocity breaker + HELD_MAX level breaker (:214-227), DAILY_LOSS_HALT (:228-238, trips at $40 = way too loose for a $9 grind). Pivot-select build (KALSHI_PIVOT_SELECT, separate, may or may not be merged) fills the footprint with EARNING markets — the stand-down must COMPOSE: pivot fills with earners, stand-down caps/sizes the total when even the earners are too thin to justify the fill risk.',
  '',
  '=== THE DESIGN TARGET ===',
  'A stand-down guard behind a NEW flag KALSHI_STANDDOWN (_envi, DEFAULT 0 = OFF = provable no-op). When ON, before OPENING (accumulating) inventory in a market, judge whether expected reward justifies expected adverse cost, and if not, do NOT open there (go reduce-only for that market / size it to MIN) — bleeding ~$0. Candidate mechanisms (the Design phase MUST pick and justify one, or a minimal combination):',
  '  (A) PER-MARKET EV GATE: estimate reward-per-contract for this market (its LIP pool $/day, R3-two-sided, divided by a conservative our-share proxy — remember we are NEVER the marginal maker, share is small) vs our measured adverse-cost-per-contract (fingerprint). If reward/ct does not beat cost/ct by a margin, go REDUCE-ONLY in that market. Principled, but needs a defensible share proxy.',
  '  (B) REGIME SIZE-SCALE: compute total earnable reward density available right now across the footprint (sum of R1xR3 $/day). If it is below a floor (e.g. temp dark, only thin gas left), scale quote SIZE toward MIN_QUOTE_CT globally — smaller fills = proportionally smaller adverse loss — until the reward regime recovers. Simple, robust, does not fully exit gas.',
  '  (C) FAST ADVERSE MICRO-BREAKER: a tighter, faster cousin of the daily halt — if cumulative realized adverse loss over a short rolling window exceeds a small threshold (e.g. $5-8) WHILE reward availability is thin, go reduce-only until the window clears. Catches the slow grind the $40 halt misses.',
  'REQUIREMENTS on whatever is chosen: (1) EXITS / reducing / unwind creates are NEVER blocked or down-sized — de-risking must always proceed. (2) Must NOT shut off gas on a NORMAL (reward-present) day — calibrate the threshold against the fingerprint so gas keeps quoting when its reward is live; only bite when reward is genuinely thin/absent relative to adverse cost. (3) Must be OBSERVABLE (emit plan telemetry: standdown state, the reward-vs-cost numbers driving it). (4) Bounded, no unbounded loops, no extra per-cycle API reads beyond what the quoter already fetches (reuse the book/reward data already in-cycle).',
  '',
  '=== FLAG + NO-OP ===',
  'KALSHI_STANDDOWN default 0 = today exact behavior, byte-for-byte. The code must deploy with zero live change until the flag is flipped. Telemetry emitted only when the flag is on.',
  '',
  '=== HARD CONSTRAINTS ===',
  '1. Edit ONLY maker_kalshi_quoter.py + a new test file. No deploy, no live.env, no ssh-write, no systemctl. Worktree edit does not affect the running VPS bot.',
  '2. Preserve every signature; EXITS/reducing + all existing guards + the funding gate + loss meter unchanged. One behavioral change behind one flag.',
  '3. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.',
  '4. COMMIT when green: git -C ' + WT + ' add kalshi_live/maker_kalshi_quoter.py kalshi_live/test_standdown.py && git -C ' + WT + ' commit -m "feat(kalshi): stand-down guard behind KALSHI_STANDDOWN (default off) — open less when reward does not justify fill loss [NOT DEPLOYED]". Return sha + full diff + pytest output.',
  '5. Kalshi authed reads work locally for measurement (cd ' + WT + '/kalshi_live && python3, module L). Orderbook: L.get(L.P+"/markets/TICKER/orderbook")["orderbook_fp"] keys yes_dollars/no_dollars = [[price_str,size_str]].',
  '',
  '=== TESTS (kalshi_live/test_standdown.py) ===',
  '1. FLAG-OFF NO-OP: KALSHI_STANDDOWN unset/0 -> identical quotes/plan to legacy on a fixture where the regime is thin. Assert against the legacy path.',
  '2. THE FIX PIN: a thin-reward regime fixture (temp dark, only a thin/adverse gas market). Flag OFF: bot opens full-size and would take the adverse fill. Flag ON: bot opens MIN size / reduce-only in that market -> materially less fill exposure. FAILS on legacy, PASSES on fix.',
  '3. NEVER-BLOCKS-EXITS: with inventory to unwind, flag ON must STILL rest the reducing/unwind quote (de-risk always proceeds). Assert the reducing quote is present under stand-down.',
  '4. DOES-NOT-FORFEIT-GAS: a normal reward-present gas fixture (reward clearly beats adverse cost). Flag ON must KEEP quoting gas at normal size (no false stand-down). Assert full-size quotes remain.',
  'Run the FULL suite: cd ' + WT + '/kalshi_live && python -m pytest test_*.py -q. ALL must pass.',
].join('\n')

phase('Design')

const design = await agent(RULES + '\n\nTASK — DESIGN THE STAND-DOWN. Read maker_kalshi_quoter.py:460-560 (quote-gen + gates), :790-940 (equity/breakers/loss-meter), :1245-1380 (quote loop + plan dict), and how quote SIZE is set (JOIN_SIZE / _capped_join / MIN_QUOTE_CT). Pick ONE mechanism (A per-market EV gate / B regime size-scale / C fast micro-breaker) or a minimal combination, and JUSTIFY it against: the requirements (never block exits, never forfeit +EV gas, observable, bounded), the fingerprint numbers, and composition with the funding gate + pivot-select. Give the exact flag-gated pseudo-code, insertion points (line refs), the flag-off no-op proof, and the 4 pin-test fixtures concretely. Prefer the SIMPLEST design that provably cuts the dead-day bleed without shutting gas on a normal day. RETURN the design.', { label: 'design', phase: 'Design', effort: 'high' })

phase('Implement')

const impl = await agent(RULES + '\n\nDESIGN TO IMPLEMENT:\n' + String(design).slice(0, 12000) + '\n\nTASK — IMPLEMENT the stand-down exactly per the design + the 4 pin tests. Flag KALSHI_STANDDOWN default 0 = provable no-op. Never block/downsize EXITS. Do NOT touch the funding gate, loss meter, velocity breaker, or existing gates except to compose. Run the FULL pytest suite; fix until green (or flag a pre-existing unrelated failure with evidence). Commit. RETURN commit sha, the COMPLETE unified diff of maker_kalshi_quoter.py + the test file, the exact pytest summary, and a 4-line plain description of the change + why flag-off is a no-op.', { label: 'implement', phase: 'Implement', effort: 'high' })

phase('Verify')

const verds = await parallel([
  'flag-off-is-a-noop', 'never-blocks-exits', 'does-not-forfeit-plus-ev-gas', 'actually-cuts-the-dead-day-bleed',
].map(lens => () => agent(RULES + '\n\nTASK — ADVERSARIALLY VERIFY the stand-down change. Lens: ' + lens + '. DEFAULT TO REFUTED IF UNCERTAIN. Live-money change. Read the committed diff READ-ONLY: git -C ' + WT + ' show HEAD and git -C ' + WT + ' diff HEAD~1 HEAD -- kalshi_live/maker_kalshi_quoter.py. Do NOT edit.\n- flag-off-is-a-noop: prove KALSHI_STANDDOWN unset/0 gives byte-identical quotes/plan to legacy. Any divergence when off = REFUTED.\n- never-blocks-exits: find ANY path where flag-ON blocks or down-sizes a reducing/unwind/exit quote. De-risk must ALWAYS proceed. If it can trap inventory, REFUTE (this is the deadliest failure).\n- does-not-forfeit-plus-ev-gas: prove flag-ON still quotes gas at normal size when gas reward is live (a false stand-down that shuts our only edge is a REFUTE).\n- actually-cuts-the-dead-day-bleed: prove flag-ON WOULD materially reduce opening/fill exposure in the thin-reward regime (temp dark + adverse gas). If it does not actually cut the bleed, REFUTE — it must earn its complexity.\nRETURN refuted true/false, severity, the specific state, and the check that settles it.', { label: 'verify:' + lens, phase: 'Verify', schema: { type: 'object', required: ['lens','refuted','severity','finding'], properties: { lens: { type: 'string' }, refuted: { type: 'boolean' }, severity: { type: 'string', enum: ['CRITICAL','HIGH','MEDIUM','LOW','NONE'] }, finding: { type: 'string' }, failing_state: { type: 'string' }, settling_check: { type: 'string' } } } })))

phase('Deliver')

const final = await agent(RULES + '\n\nIMPLEMENTATION:\n' + String(impl).slice(0, 8000) + '\n\nVERIFIER VERDICTS:\n' + JSON.stringify(verds.filter(Boolean), null, 1) + '\n\nTASK — write docs/maker_handoffs/KALSHI_STANDDOWN_BUILD_2026-07-24.md and return it: (1) what changed in plain English + flag-off no-op proof; (2) every verifier verdict, lead with any CRITICAL/HIGH (especially any never-blocks-exits failure); (3) if green: pytest counts, the new-file md5 (git -C ' + WT + ' show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum), the per-file md5-gated deploy step, rollback (flag unset = no-op, or git revert); (4) the expected LIVE effect when flipped on (a temp-dark/adverse day opens MIN/reduce-only instead of full-size, dead-day bleed ~$0, gas still quoted on a normal day); (5) Tier-3 change — operator sign-off + md5-gated deploy before live. Return the doc.', { label: 'deliver', phase: 'Deliver', effort: 'high' })

return { final, verds: verds.filter(Boolean), implPreview: String(impl).slice(0, 1200) }
