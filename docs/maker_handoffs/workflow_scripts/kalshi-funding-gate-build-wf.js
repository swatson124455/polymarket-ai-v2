export const meta = {
  name: 'kalshi-funding-gate-build',
  description: 'Implement the committed-capital counting fix behind an off-by-default flag (stop counting already-spent held_cost + un-filled resting notional against the cap), add pin tests, run pytest, adversarially verify it can never overdraw and is a no-op when off',
  phases: [
    { title: 'Implement', detail: 'code the fix behind KALSHI_FUNDING_GATE (default off) + pin tests + run pytest, commit to branch' },
    { title: 'Verify', detail: 'adversarial: flag-off is a no-op, flag-on never overdraws, fails safe, tests are real' },
    { title: 'Deliver', detail: 'ship checklist, md5, deploy+rollback, the one live confirmation still needed before flag-on' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE. Worktree: ${WT} (branch claude/maker-kalshi-live). cd ${WT}; bash cwd drifts, use absolute paths.

=== WHAT WE ARE FIXING (operator directive: "fix the dumb way the bot counts money") ===
The committed-capital gate blocks new accumulating buys when committed > MAX_TOTAL_CAPITAL.
maker_kalshi_quoter.py:1251-1287 (verified this session):
  :1254-1256  committed = sum(o["price_dollars"]*o["count"] for surviving standing NOT-cancelled orders)   # GROSS resting notional
  :1259       committed += held_cost                                                                        # <-- THE DUMB PART
  :1274       if not reducing and committed + cost > MAX_TOTAL_CAPITAL: skip/continue                       # the gate (accumulating only; reducing exempt)
  :99         MAX_TOTAL_CAPITAL = _envf("KALSHI_MAX_TOTAL_CAPITAL", 10000.0)   # live value = 250
held_cost (from _held_cost(client), :1561) is the cost basis of inventory ALREADY BOUGHT — that cash is already OUT of balance_dollars. Adding it to a gate measured against MAX_TOTAL_CAPITAL double-counts money that is already spent, so the gate pins at the cap while real free cash sits idle. That is the treadmill the operator kept escaping by RAISING the cap (85->100->150->250 in one day). ALSO note cap_desired (:672-684) applies MAX_TOTAL_CAPITAL to the desired-markets list — evaluate whether it needs the same treatment or should be left (state your decision).

=== THE VENUE FACT THIS RESTS ON (do not silently assume; state the assumption in code + doc) ===
Best committed evidence (kalshi_attribution_ledger.py:436-444, KALSHI_RUNNING_TAB.md:63, 07-20): resting orders do NOT deduct from balance_dollars — Kalshi reserves cash at FILL, not at placement (GROSS). The prior design's own reconciliation "proof" was CIRCULAR (account_value is never read from the venue; /portfolio/balance exposes only {balance, portfolio_value}) — see docs/maker_handoffs/KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md §1/§6 and its 4 refuters. So: DO NOT rely on a reconciliation identity as validation. Build the fix so it is SAFE whether balance is GROSS or NET (see the hard ceiling below). The fill-time collateral-release behavior for offsetting legs is UNMEASURED (n=1) — do NOT build logic that depends on it.

=== THE FIX (safe under BOTH gross and net; this is the design constraint, keep it simple) ===
Behind a new flag KALSHI_FUNDING_GATE (_envi, DEFAULT 0):
  * FLAG 0 (default): EXACTLY the current behavior, byte-for-byte in effect. committed still includes held_cost, gate unchanged. This MUST be a provable no-op so the code can deploy with zero live change.
  * FLAG 1 (the fix): the accumulating gate stops counting already-spent held_cost, and instead ensures the bot never rests more BUY notional than free cash can fund:
      free_cash = balance_dollars (real free cash, read once; if the balance read failed this cycle, FAIL CLOSED = keep the old gross gate / do not loosen).
      funding_committed = sum(surviving standing BUY notional)   # resting book that would draw cash IF it filled; NO held_cost term
      gate a new accumulating create of cost c:  if funding_committed + c > min(free_cash, MAX_TOTAL_CAPITAL): skip; else funding_committed += c
    Rationale: held_cost is already paid (out of balance) -> excluding it ends the freeze. Gating the resting BUY book against free_cash is the HARD CEILING that makes it safe REGARDLESS of gross/net: you can never rest more buy notional than you could fund if it all filled, so no overdraw, and MAX_TOTAL_CAPITAL stays as a real backstop (whichever is smaller binds). If balance is actually NET, worst case is a re-freeze (annoying, revert the flag) — NEVER a blowup.
  * KEEP reducing/unwind creates EXEMPT exactly as today (:1274 'not reducing').
  * Do NOT introduce a naked-coverage netting term in this build (the prior design's naked_buy_draw had a coverage double-count bug). Gate on gross surviving BUY notional vs free_cash — simpler and strictly safe. If you believe a coverage term is needed, STOP and flag it instead of adding it.
  * Do NOT touch the loss meter / equity (:853-873) — that is a separate quantity (balance + held COST) and is correct as-is.
  * Do NOT touch neg-risk routing, exit/SELL logic, ladder_pairing, or the risk breakers (naked_held_cost <= HELD_MAX_USD). This fix is ONLY the accumulating capital gate.

=== TESTS (add to kalshi_live/test_live_hardening.py or a new kalshi_live/test_funding_gate.py) ===
1. FLAG-OFF NO-OP: with KALSHI_FUNDING_GATE unset/0, a scenario that the old gross gate blocks is STILL blocked identically (behavior unchanged).
2. THE FIX PIN: a scenario where held_cost inflates committed past MAX_TOTAL_CAPITAL while free_cash is ample -> old gross gate BLOCKS (skips the create), new funding gate ADMITS it. Test must FAIL on the current gross code and PASS on the fix (prove it pins the real behavior change, not the bug).
3. HARD CEILING: with free_cash small, the funding gate REFUSES creates that would exceed free_cash even if MAX_TOTAL_CAPITAL is huge -> never overdraws.
4. BALANCE-READ-FAIL FAIL-CLOSED: if the balance read failed this cycle, the fix does NOT loosen (keeps the old gross gate).
Run the FULL suite: cd ${WT}/kalshi_live && python3 -m pytest test_live_hardening.py test_funding_gate.py -q (and any other test_*.py). ALL must pass. Report the exact counts.

=== HARD CONSTRAINTS ===
1. Edit ONLY maker_kalshi_quoter.py (the gate) and the test file. Do NOT deploy, do NOT touch live.env, do NOT ssh-write, no systemctl. The worktree edit does NOT affect the running VPS bot (deploy is a separate md5-gated step nobody takes here).
2. Preserve every function signature and the flag-off path exactly. One behavioral change, behind one flag.
3. COMMIT the change to the branch when tests pass: git -C ${WT} add -A && git -C ${WT} commit -m "feat(kalshi): funding-gate capital counting behind KALSHI_FUNDING_GATE (default off) — stop counting spent held_cost + free-cash hard ceiling [NOT DEPLOYED]". Return the commit sha + the full diff + pytest output.
4. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.
`

phase('Implement')

const impl = await agent(`${RULES}

TASK — IMPLEMENT THE FIX + TESTS, RUN PYTEST, COMMIT.

Steps:
1. Read docs/maker_handoffs/KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md (the full design + why the prior draft was not ship-ready — you are building the SAFE simplified version specified in the RULES above, NOT the circular/naked-coverage version).
2. Read maker_kalshi_quoter.py around :99, :670-690, :790-875, :1245-1290, :1330-1370, :1540-1710 to understand held_cost, the gate, cap_desired, and the plan dict.
3. Read kalshi_live/test_live_hardening.py to match the existing test style/fixtures.
4. Implement the flag + the funding gate exactly per the RULES (flag default 0 = provable no-op). Add the 4 pin tests.
5. Run the full pytest suite. If anything fails, FIX and re-run until green (or if a pre-existing test is unrelated-broken, say so explicitly with evidence).
6. Commit to the branch.

RETURN: the commit sha, the COMPLETE unified diff of maker_kalshi_quoter.py and the test file, the exact pytest summary line (e.g. "N passed, M xfailed"), and a plain-English 4-line description of the one behavioral change and why flag-off is a no-op.`,
  { label: 'implement', phase: 'Implement', effort: 'high' })

log('implement done; verifying')

phase('Verify')

const verds = await parallel([
  'flag-off-is-a-noop', 'flag-on-never-overdraws', 'fails-safe-if-net', 'tests-are-real-not-asserting-the-bug',
].map(lens => () => agent(`${RULES}

TASK — ADVERSARIALLY VERIFY the committed fix. Lens: **${lens}**. DEFAULT TO REFUTED IF UNCERTAIN. Live-money capital gate.

Read the committed diff READ-ONLY: git -C ${WT} show HEAD  (and git -C ${WT} diff HEAD~1 HEAD -- kalshi_live/maker_kalshi_quoter.py). Read the surrounding code as needed. Do NOT edit anything.

Through your lens:
- flag-off-is-a-noop: prove that with KALSHI_FUNDING_GATE unset/0 the gate computes and behaves IDENTICALLY to before the diff. Any code path where flag-off changes a value, an order, or the plan dict = REFUTED. Check the default resolves to 0, and that the old committed (incl held_cost) still gates when off.
- flag-on-never-overdraws: construct the worst book you can and show whether flag-on can ever admit accumulating creates whose total BUY notional exceeds free_cash (an overdraw). Check the balance-read-fail path (must fail closed, not loosen). Check reducing-exemption is unchanged. Check MAX_TOTAL_CAPITAL still binds as a backstop.
- fails-safe-if-net: assume balance is actually NET-of-reserve (resting DOES lock cash). Does flag-on ever become DANGEROUS (over-commit / blowup), or only annoying (re-freeze)? It must be at worst a re-freeze. If you find a dangerous path under NET, REFUTE.
- tests-are-real-not-asserting-the-bug: run the pin tests against the OLD code (git stash the fix or checkout HEAD~1 for the quoter, keep the new test) to confirm test #2/#3 actually FAIL on the old gross gate and PASS on the fix. A test that passes on both is asserting nothing. Verify flag-off test truly exercises the old path. Report which tests discriminate and which don't.

RETURN: refuted true/false, severity, the specific failing state or the proof it holds, and the measurement/test that settles it.`,
    { label: `verify:${lens}`, phase: 'Verify', schema: {
      type: 'object',
      required: ['lens','refuted','severity','finding','settling_check'],
      properties: {
        lens: { type: 'string' },
        refuted: { type: 'boolean' },
        severity: { type: 'string', enum: ['CRITICAL','HIGH','MEDIUM','LOW','NONE'] },
        finding: { type: 'string' },
        failing_state: { type: 'string' },
        settling_check: { type: 'string' },
      },
    } })))

phase('Deliver')

const final = await agent(`${RULES}

TASK — SHIP READINESS + HANDOFF. This is a diagnosis of ship-readiness, NOT a deploy.

IMPLEMENTATION: ${String(impl).slice(0, 9000)}
VERIFIER VERDICTS: ${JSON.stringify(verds.filter(Boolean), null, 1)}

Write docs/maker_handoffs/KALSHI_FUNDING_GATE_BUILD_2026-07-23.md and return it:
1. WHAT CHANGED — the one behavioral change, in plain English, and the proof flag-off is a no-op.
2. VERIFIER RESULTS — every lens verdict. Lead with any CRITICAL/HIGH (esp. a real overdraw path or a flag-off that isn't a no-op). If any survive, the verdict is NOT-READY and say exactly what to fix.
3. SHIP CHECKLIST if green: pytest counts; the new-file md5 (git -C ${WT} show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum); the per-file md5-gated deploy step; the rollback (KALSHI_FUNDING_GATE unset = no-op even if deployed; or git revert the commit).
4. THE ONE THING STILL UNCONFIRMED — flipping KALSHI_FUNDING_GATE=1 assumes balance is GROSS. Best evidence says it is, and the free-cash hard ceiling makes flag-on SAFE regardless (worst case re-freeze). But the clean confirmation is a place->observe->cancel test order (a live write, operator-gated). State clearly: the code can DEPLOY safely (flag off, no-op); turning the flag ON is the step that wants that confirmation, and even then it fails safe.
5. Tier-3 live-money change: before ANY deploy, operator sign-off + adversarial re-review + the deploy is per-file md5-gated. Say so.

RETURN the doc.`,
  { label: 'deliver', phase: 'Deliver', effort: 'high' })

return { final, verds: verds.filter(Boolean), implPreview: String(impl).slice(0, 1500) }
