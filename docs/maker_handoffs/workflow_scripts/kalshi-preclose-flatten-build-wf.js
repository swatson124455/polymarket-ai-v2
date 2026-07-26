export const meta = {
  name: 'kalshi-preclose-flatten-build',
  description: 'Stop the settlement bleed: a NAKED-ONLY pre-close flatten that exits the unpaired residual before market close (maker-first, taker-backstop the naked qty only) so a directional residual never rides into settlement — fixing all 3 reasons TAKER_FLATTEN was disabled. Behind an off-by-default flag, tested + adversarially verified',
  phases: [
    { title: 'Design', detail: 'naked-only residual from ladder_pairing, pre-close timing, maker-first + taker-naked-only, addressing the 3 disable-reasons; reuse STOP escalation machinery' },
    { title: 'Implement', detail: 'behind KALSHI_PRECLOSE_FLATTEN (default 0) + pin tests + pytest, commit' },
    { title: 'Verify', detail: 'adversarial: flag-off no-op, crosses NAKED-ONLY never de-hedges pairs, never strands the resting exit, fires only near close' },
    { title: 'Deliver', detail: 'ship checklist, md5, deploy+rollback, the pre-close window + taker-cost note' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = [
  'KALSHI MAKER LANE. Worktree: ' + WT + ' (branch claude/maker-kalshi-live). cd ' + WT + '; bash cwd drifts, use absolute paths.',
  '',
  '=== THE PROBLEM (measured today, grounded) ===',
  'Biggest single loss driver: we carry NAKED (unpaired, net-directional) ladder inventory into settlement and it resolves against us. Measured 2026-07-24: gas-daily 26JUL24 settled 12:55Z for REALIZED -$34.98 across 7 strikes -- we held NO on 4.100 (resolved YES, -$10.99), YES on 4.105/4.110 (resolved NO, -$11.69/-$9.51). That is a directional band-bet at the ATM, not a self-hedged ladder. HARD CONSTRAINT: the market CLOSES (trading ends) at 03:59Z but SETTLES at 12:55Z -- after close we CANNOT trade, so whatever naked inventory we hold at CLOSE rides to settlement. A properly PAIRED ladder (YES low / NO high, adjacent) self-hedges to ~$1/pair and is SAFE to carry; only the NAKED residual is the settlement gamble.',
  '',
  '=== WHY TAKER_FLATTEN IS CURRENTLY OFF (must fix ALL THREE -- these are why it was disabled) ===',
  'From KALSHI_HANDOFF_2026-07-23_EOD.md §2 (the flatten was reverted 18:09Z for these reasons):',
  '1. IT DE-HEDGED LIVE PAIRS: the trigger was naked-only but the flatten crossed the FULL position. Ex: GASW-4.140 naked +6 but held +40 -> it would cross 40, ORPHANING the 34 paired on the 4.160 leg. FIX REQUIRED: cross ONLY the naked (unpaired) quantity, NEVER the paired legs.',
  '2. THE EXIT IT PROTECTS IS WORTH ~8% (inventory-doctrine 9.69->8.90 c/ct) -- i.e. taker-flattening the WHOLE position costs ~8% in spread. FIX/SCOPE: only taker the NAKED residual, and only NEAR CLOSE where the settlement loss (-$35 today) dwarfs the ~few-cents-per-contract taker cost (Kalshi taker fee ~ceil(0.07*P*(1-P)*qty*100)/100, cap $0.035/ct; ~$1 on a 40ct residual vs a $35 settlement loss). Paying spread to dodge the settlement gamble is +EV ONLY in the final pre-close stretch on naked qty.',
  '3. ONE-SIDED-BOOK STRANDING: flatten_to_zero cancelled our resting exit FIRST, the IOC failed on a one-sided book, the fallback failed too -> exit cancelled, nothing replaced it. FIX REQUIRED: do NOT cancel the resting maker exit before the taker; if the taker cannot fill, the resting exit MUST remain. Maker-first, taker as an additive backstop, never a replace-then-fail.',
  '',
  '=== EXISTING MACHINERY TO REUSE (do not reinvent; cite line refs) ===',
  'ladder_pairing(held_by) / naked_held_cost(held_by, cost_by) already compute the FLOORED pairs vs the NAKED remainder per event -- this is the source of the naked quantity to flatten. The STOP escalation (maker_kalshi_quoter.py ~:1489+ _flatten... : maker-first, rest offsets, WAIT STOP_ESCALATE_S=90, then taker-cross whatever is STILL >= STOP_TAKER_MIN_CT=5) is the maker-first-then-bounded-taker pattern to reuse -- but scoped to the NAKED residual and triggered PRE-CLOSE, not only on the STOP sentinel. WIND_DOWN_MIN (live 20) already pulls quotes N min before end; the late-life gate blocks entry. The gap: nothing ACTIVELY flattens the naked residual before close -- it just stops quoting and lets the residual ride.',
  '',
  '=== THE FIX (design target) ===',
  'New flag KALSHI_PRECLOSE_FLATTEN (_envi, DEFAULT 0 = OFF = provable no-op). When ON: for each market/event within KALSHI_PRECLOSE_FLATTEN_MIN (a config, e.g. 15-20 min) of its MARKET CLOSE (trading end -- the market close_time, NOT the reward-period end if they differ), compute the NAKED (unpaired) residual via ladder_pairing/naked_held_cost. Flatten ONLY that naked residual: (a) MAKER-FIRST rest a reducing quote for the naked qty (existing unwind path); (b) if naked qty still >= a min (reuse STOP_TAKER_MIN_CT) within the final minutes, TAKER-cross ONLY the naked qty (never the paired legs, never more than |naked|); (c) NEVER cancel a resting maker exit before the taker -- taker is additive; if it fails, the resting exit remains (fix reason 3). The PAIRED inventory is left to self-hedge into settlement.',
  'REQUIREMENTS: (1) crosses AT MOST |naked| contracts, provably never a paired leg (fix reason 1). (2) fires ONLY within the pre-close window AND only when naked residual exists (not continuously -- that was the old always-on de-hedging). (3) maker-first; taker never replaces-then-strands the exit (fix reason 3). (4) composes with WIND_DOWN, late-life gate, STOP escalation, funding gate, pivot-select. (5) observable telemetry (preclose_flatten, naked qty flattened, taker qty). (6) does NOT touch the general TAKER_FLATTEN=0 (that stays off) -- this is a separate, scoped, pre-close-only mechanism.',
  '',
  '=== FLAG + NO-OP ===',
  'KALSHI_PRECLOSE_FLATTEN default 0 = today exact behavior byte-for-byte; deploys with zero live change until flipped. Telemetry only when on.',
  '',
  '=== HARD CONSTRAINTS ===',
  '1. Edit ONLY maker_kalshi_quoter.py + a new test file. No deploy, no live.env, no ssh-write, no systemctl. Worktree edit does not affect the running VPS bot.',
  '2. Preserve every signature; the general TAKER_FLATTEN path, funding gate, pivot-select, net-EV gate, loss meter, all guards unchanged except to compose. One behavioral change behind one flag.',
  '3. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.',
  '4. COMMIT when green: git -C ' + WT + ' add kalshi_live/maker_kalshi_quoter.py kalshi_live/test_preclose_flatten.py && git -C ' + WT + ' commit -m "feat(kalshi): naked-only pre-close settlement flatten behind KALSHI_PRECLOSE_FLATTEN (default off) — exit the unpaired residual before close so it never rides into settlement [NOT DEPLOYED]". Return sha + full diff + pytest output.',
  '5. Kalshi authed reads local (cd ' + WT + '/kalshi_live && python3, module L) if needed for measurement.',
  '',
  '=== TESTS (kalshi_live/test_preclose_flatten.py) — the deadliest pins first ===',
  '1. CROSSES-NAKED-ONLY (fixes reason 1): held +40 with 34 paired / 6 naked, within the pre-close window, flag ON -> the taker crosses AT MOST 6 (the naked), NEVER 40. Assert taker qty <= |naked| and the paired 34 untouched. This is the exact GASW-4.140 bug — it MUST pass.',
  '2. FLAG-OFF NO-OP: flag unset/0 -> identical quotes/plan to legacy on a near-close held-naked fixture. Assert legacy path.',
  '3. NEVER-STRANDS-EXIT (fixes reason 3): one-sided book, flag ON, taker cannot fill -> the resting maker reducing exit REMAINS (not cancelled-then-nothing). Assert the reducing quote present after a failed taker.',
  '4. FIRES-ONLY-NEAR-CLOSE: a market with lots of time left + naked inventory, flag ON -> NO pre-close taker fires (only maker unwind as today). Assert no taker until inside the window.',
  '5. MAKER-FIRST: within the window, the naked residual first gets a resting reducing quote; taker only after the maker grace fails on the still-naked qty.',
  'Run the FULL suite: cd ' + WT + '/kalshi_live && python -m pytest test_*.py -q. ALL must pass.',
].join('\n')

phase('Design')

const design = await agent(RULES + '\n\nTASK — DESIGN THE NAKED-ONLY PRE-CLOSE FLATTEN. Read maker_kalshi_quoter.py: ladder_pairing / naked_held_cost (the naked-residual source), the unwind/strand path (maker reducing quotes), the STOP escalation _flatten machinery (~:1489+, maker-first + bounded taker), WIND_DOWN/late-life/select_footprint (the close-time + timing), and how the market close_time is available in-cycle. Design: (a) the pre-close window trigger keyed on market close_time (distinguish from reward-period end if they differ); (b) the exact naked-residual computation (ladder_pairing) and the maker-first-then-taker-naked-only flatten, provably crossing at most |naked|; (c) the do-not-strand-the-exit ordering (taker additive, never cancel-then-fail); (d) the flag-off no-op proof; (e) the 5 pin fixtures. Show pseudo-code + insertion points + how it reuses the STOP-escalation machinery. RETURN the design.', { label: 'design', phase: 'Design', effort: 'high' })

phase('Implement')

const impl = await agent(RULES + '\n\nDESIGN TO IMPLEMENT:\n' + String(design).slice(0, 12000) + '\n\nTASK — IMPLEMENT the naked-only pre-close flatten exactly per the design + the 5 pin tests. Flag KALSHI_PRECLOSE_FLATTEN default 0 = provable no-op. Cross AT MOST |naked|, NEVER a paired leg. Never cancel-then-strand the resting exit. Fire only in the pre-close window. Do NOT touch the general TAKER_FLATTEN path, funding gate, pivot-select, net-EV gate, loss meter, or other guards except to compose. Run the FULL pytest suite; fix until green (or flag a pre-existing unrelated failure with evidence). Commit. RETURN commit sha, the COMPLETE unified diff of maker_kalshi_quoter.py + the test file, the exact pytest summary, and a 4-line plain description + why flag-off is a no-op.', { label: 'implement', phase: 'Implement', effort: 'high' })

phase('Verify')

const verds = await parallel([
  'crosses-naked-only-never-dehedges', 'never-strands-the-exit', 'flag-off-is-a-noop', 'fires-only-near-close',
].map(lens => () => agent(RULES + '\n\nTASK — ADVERSARIALLY VERIFY the pre-close flatten. Lens: ' + lens + '. DEFAULT TO REFUTED IF UNCERTAIN. Live-money change re-arming the TAKER path that LOST money before — max scrutiny. Read the committed diff READ-ONLY: git -C ' + WT + ' show HEAD; git -C ' + WT + ' diff HEAD~1 HEAD -- kalshi_live/maker_kalshi_quoter.py. Do NOT edit.\n- crosses-naked-only-never-dehedges (DEADLIEST — this is the exact old bug): construct held +40 / paired 34 / naked 6 and prove the taker crosses AT MOST 6 and CANNOT touch the 34 paired. Trace the qty passed to the taker cross. If ANY path crosses more than |naked| or a paired leg, REFUTE. Check the naked qty is from ladder_pairing, not |held|.\n- never-strands-the-exit (fixes reason 3): find ANY path where the resting maker exit is cancelled and then nothing replaces it (taker fails on a one-sided book). The taker MUST be additive. If the exit can be stranded, REFUTE.\n- flag-off-is-a-noop: prove KALSHI_PRECLOSE_FLATTEN unset/0 = byte-identical to legacy; the general TAKER_FLATTEN=0 path unchanged. Any divergence off = REFUTE.\n- fires-only-near-close: prove the taker fires ONLY inside the pre-close window AND only on naked residual — never continuously (the old always-on de-hedging), never with lots of time left. If it can fire early or on paired-only inventory, REFUTE.\nRETURN refuted true/false, severity, the specific state, the check that settles it.', { label: 'verify:' + lens, phase: 'Verify', schema: { type: 'object', required: ['lens', 'refuted', 'severity', 'finding'], properties: { lens: { type: 'string' }, refuted: { type: 'boolean' }, severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NONE'] }, finding: { type: 'string' }, failing_state: { type: 'string' }, settling_check: { type: 'string' } } } })))

phase('Deliver')

const final = await agent(RULES + '\n\nIMPLEMENTATION:\n' + String(impl).slice(0, 8000) + '\n\nVERIFIER VERDICTS:\n' + JSON.stringify(verds.filter(Boolean), null, 1) + '\n\nTASK — write docs/maker_handoffs/KALSHI_PRECLOSE_FLATTEN_BUILD_2026-07-24.md and return it: (1) what changed plain English (naked-only pre-close flatten, fixes the -$35 settlement bleed) + flag-off no-op proof; (2) HOW IT FIXES ALL 3 TAKER_FLATTEN DISABLE-REASONS (naked-only not full / scoped-to-preclose / never-strands-exit) with the verifier evidence for each; (3) every verifier verdict, LEAD with the crosses-naked-only lens (the deadliest — the exact old bug) and any CRITICAL/HIGH; (4) if green: pytest counts, new-file md5, per-file md5-gated deploy step, rollback (flag unset = no-op / git revert); (5) expected LIVE effect (near close, the naked residual is exited maker-first + taker-backstop; a directional residual no longer rides into settlement; a -$35 day becomes ~-$1 of taker cost); (6) the pre-close window + taker-cost note (fee ~$1 on a 40ct residual vs the settlement loss avoided); (7) Tier-3 — operator sign-off + md5-gated deploy before live. Return the doc.', { label: 'deliver', phase: 'Deliver', effort: 'high' })

return { final, verds: verds.filter(Boolean), implPreview: String(impl).slice(0, 1200) }
