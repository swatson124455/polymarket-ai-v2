export const meta = {
  name: 'kalshi-net-ev-flagging-build',
  description: 'Redo the flagging to calibrate WILL-IT-MAKE-US-MONEY: a per-series calibrated NET-EV engine (actual reward credits minus measured fill P&L, from receipts) + an auto gate that skips/reduces net-negative markets. Calibrated off receipts, not the model. Behind an off-by-default flag, tested + adversarially verified',
  phases: [
    { title: 'Design', detail: 'net-EV = calibrated reward income - measured fill cost, per series; calibration from receipts; gate on net<margin; honest about the credit-ticker gap' },
    { title: 'Implement', detail: 'calibration engine (per-series net $/day from CSV credits + fills/settlements) + gate behind KALSHI_NETEV_GATE (default 0) + pin tests + pytest, commit' },
    { title: 'Verify', detail: 'adversarial: flag-off no-op, never blocks exits, keeps net-POSITIVE gas, skips net-NEGATIVE (temp-style), calibration is receipt-based not model-guessed' },
    { title: 'Deliver', detail: 'ship checklist, md5, deploy+rollback, the calibration-refresh runbook' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = [
  'KALSHI MAKER LANE. Worktree: ' + WT + ' (branch claude/maker-kalshi-live). cd ' + WT + '; bash cwd drifts, use absolute paths.',
  '',
  '=== THE DIRECTIVE (operator, verbatim intent) ===',
  '"Redo the flagging system to calibrate IF IT WILL MAKE US MONEY." The prior gates flag "reward possible" / "can we capture a slice". That is NOT enough: a market can capture reward and STILL lose money net if the fill losses (adverse selection) exceed the reward. The flagging must gate on NET: does the market make US money = (reward income) minus (fill/adverse cost) minus fees. And it must be CALIBRATED against actual receipts, not the over-predicting model. Then it drives the bot automatically (auto-skip/reduce net-negative markets) -- no manual per-ticker pulls.',
  '',
  '=== THE CALIBRATION PRINCIPLE (this is the core -- calibrate off receipts, NOT the model) ===',
  'We ALREADY have the receipt-grade net per family from M8/M13: over the 07-21..22 window, GAS netted +1.1% of notional (credits +2.15 - trading +0.25... net +2.40 on 214.85 notional) and TEMP netted -9.2% (credits +23.06 but trading -36.12 = net -13.06). Those are ACTUAL credits minus ACTUAL fill P&L -- the ground truth "does this family make us money". The flagging must AUTOMATE that computation per series/family and REFRESH it every transaction-CSV export, and use the CALIBRATED net as the gate signal -- not the R4 model (which M7 shows over-predicts 2-6x). The model is a FALLBACK only for a NEW series with no receipt history yet (flagged UNPROVEN, sized conservatively).',
  '',
  '=== DATA + THE HONEST CONSTRAINT ===',
  'Kalshi authed reads local: cd ' + WT + '/kalshi_live && python3, module L = kalshi_attribution_ledger. /portfolio/fills (per-market fill P&L via tape replay: kalshi_settlement_pnl.replay / venue realized_pnl_dollars), /portfolio/settlements. The transaction CSV export (kalshi_live/kalshi_transactions_2026-07-23.csv is the last one; a fresh one due after 2026-07-27T04:00Z) has the reward CREDIT rows.',
  'CONSTRAINT (do not hand-wave): CREDIT rows in the CSV carry an EMPTY market_ticker (Kalshi is fixing this per M11 but has not yet), so per-SERIES credit attribution is only as good as the operator UI screenshots (M8 attributed the $25.21 credit total to GAS vs TEMP that way; the TOTAL is CSV-verified exact). So: FILL P&L is measurable per-series precisely (API); CREDITS are attributable at the FAMILY level (gas / temp / etc.) from CSV totals + screenshots. The calibration therefore produces per-FAMILY calibrated net (gas, temp, ...) reliably, and per-series only where credits are attributable. Design around this reality, do not assume per-series credits are available.',
  'The R4 model (prospective share x pool) is in kalshi_market_scorecard.qualifying_share -- use it ONLY as the UNPROVEN-series fallback, with the M7 3x haircut, clearly labelled model-not-receipt.',
  '',
  '=== THE TWO PIECES TO BUILD ===',
  'PIECE 1 -- CALIBRATION ENGINE (a NEW read-only script kalshi_live/kalshi_netev_calibrate.py). From the transaction CSV (credits) + fills/settlements (fill P&L) + fee rows, compute per-FAMILY (and per-series where possible) REALIZED NET $/day = credits - fill_P&L - fees over the window, with sample size + window + the M8/M13 caveats (credits lag a Time Period; gas-weekly period may be open; exclude one-off emergency-unwind taker rows). Emit a calibration table (JSON: family -> {net_per_day, net_pct_notional, n_trades, window, credits, fill_pnl, confidence}). This AUTOMATES M8. Re-runnable each export. Validate it reproduces the M8 numbers (gas +1.1%, temp -9.2%) on the 07-23 CSV.',
  'PIECE 2 -- THE GATE (in maker_kalshi_quoter.py, behind KALSHI_NETEV_GATE, default 0 = no-op). Load the calibration table at startup. Per market, look up its family net-EV: if the family is calibrated NET-NEGATIVE (or below a small positive margin), the market is POOR FOR US -> skip when FLAT / reduce-only when HOLDING. For an UNPROVEN family (no receipt history), use the conservative R4 model net (prospective reward /3 minus fingerprint fill cost) -> only open if that is positive, else treat as unproven-skip (or size to MIN). EXITS/reducing NEVER blocked. Compose with the existing gates + funding gate + pivot-select. Do NOT starve a calibrated NET-POSITIVE family (gas). Emit telemetry (netev_gate, per-family net used, skipped markets).',
  '',
  '=== REQUIREMENTS ===',
  '1. FLAG DEFAULT 0 = provable byte-for-byte no-op; deploys with zero live change until flipped.',
  '2. EXITS/reducing/unwind ALWAYS proceed, full size. 3. No extra per-cycle API reads (the calibration table is loaded from disk, refreshed offline). 4. Calibrate off RECEIPTS; model is the labelled fallback only. 5. Observable telemetry. 6. Composes with existing guards; supersedes the pool-only KALSHI_STANDDOWN and the reward-only capture-gate idea (those become redundant -- state this).',
  '',
  '=== HARD CONSTRAINTS ===',
  '1. Edit maker_kalshi_quoter.py + new kalshi_netev_calibrate.py + new test_netev_gate.py. No deploy, no live.env, no ssh-write, no systemctl. Worktree edit does not affect the running VPS bot.',
  '2. Preserve every signature; EXITS + all guards + funding gate + pivot-select + loss meter unchanged. Kalshi venue only.',
  '3. COMMIT when green: git -C ' + WT + ' add kalshi_live/maker_kalshi_quoter.py kalshi_live/kalshi_netev_calibrate.py kalshi_live/test_netev_gate.py && git -C ' + WT + ' commit -m "feat(kalshi): net-EV flagging — receipt-calibrated per-family net gate behind KALSHI_NETEV_GATE (default off) [NOT DEPLOYED]". Return sha + full diff + pytest output + the calibration JSON it produces on the 07-23 CSV.',
  '',
  '=== TESTS (kalshi_live/test_netev_gate.py) ===',
  '1. FLAG-OFF NO-OP: KALSHI_NETEV_GATE unset/0 -> identical to legacy on a fixture with a net-negative family. Assert legacy path.',
  '2. THE FIX PIN: calibration table says family TEMP net = -9.2% (negative). Flag OFF: bot quotes a temp market. Flag ON: bot SKIPS it (flat) / reduce-only (holding). FAILS legacy, PASSES fix.',
  '3. KEEPS-NET-POSITIVE-GAS: table says GAS net = +1.1% (positive). Flag ON KEEPS quoting gas full-size. Assert quotes remain.',
  '4. NEVER-BLOCKS-EXITS: holding inventory in a net-negative family, flag ON STILL rests the reducing quote full size. Assert present.',
  '5. CALIBRATION-ENGINE: kalshi_netev_calibrate on the 07-23 CSV reproduces gas-positive / temp-negative (the M8 signs), with the credit-lag caveat surfaced.',
  'Run the FULL suite: cd ' + WT + '/kalshi_live && python -m pytest test_*.py -q. ALL must pass.',
].join('\n')

phase('Design')

const design = await agent(RULES + '\n\nTASK — DESIGN THE NET-EV FLAGGING. (1) The calibration engine: exact per-family net-$/day formula from CSV credits + fills/settlements, the credit-lag + taker-unwind exclusions (M13), the credit-attribution-is-family-level constraint, the output JSON schema, and how it reproduces the M8 gas+1.1%/temp-9.2% numbers. (2) The gate: where in maker_kalshi_quoter.py to load the table + apply per-family net (compose with the inventory-unwind early return so exits are untouched), the net<margin skip-vs-reduce branch, the UNPROVEN-family fallback (R4 model /3 minus fill fingerprint), and the flag-off no-op proof. (3) The 5 pin-test fixtures concretely. State clearly that this SUPERSEDES the pool-only stand-down + the reward-only capture gate. RETURN the design.', { label: 'design', phase: 'Design', effort: 'high' })

phase('Implement')

const impl = await agent(RULES + '\n\nDESIGN TO IMPLEMENT:\n' + String(design).slice(0, 12000) + '\n\nTASK — IMPLEMENT both pieces + the 5 pin tests exactly per the design. Flag KALSHI_NETEV_GATE default 0 = provable no-op. Never block/downsize EXITS. Calibration engine is read-only, produces the JSON table, reproduces M8 on the 07-23 CSV. Gate loads the table (no per-cycle API reads). Do NOT touch funding gate, pivot-select, loss meter, existing gates except to compose. Run the FULL pytest suite; fix until green (or flag a pre-existing unrelated failure with evidence). Commit. RETURN commit sha, the COMPLETE unified diff of all three files, the exact pytest summary, and the calibration JSON produced on the 07-23 CSV.', { label: 'implement', phase: 'Implement', effort: 'high' })

phase('Verify')

const verds = await parallel([
  'flag-off-is-a-noop', 'never-blocks-exits', 'keeps-net-positive-gas', 'skips-net-negative-and-calibration-is-real',
].map(lens => () => agent(RULES + '\n\nTASK — ADVERSARIALLY VERIFY the net-EV flagging. Lens: ' + lens + '. DEFAULT TO REFUTED IF UNCERTAIN. Live-money change. Read the committed diff READ-ONLY: git -C ' + WT + ' show HEAD; git -C ' + WT + ' diff HEAD~1 HEAD. Do NOT edit.\n- flag-off-is-a-noop: prove KALSHI_NETEV_GATE unset/0 = byte-identical to legacy. Any divergence off = REFUTED.\n- never-blocks-exits: find ANY path where flag-ON blocks/downsizes a reducing/unwind/exit. De-risk must ALWAYS proceed. Inventory-trap = REFUTE (deadliest).\n- keeps-net-positive-gas: prove flag-ON keeps quoting gas full-size when the table says gas is net-positive. A false skip of our +EV family is a REFUTE.\n- skips-net-negative-and-calibration-is-real: prove (a) flag-ON skips/reduces a calibrated net-negative family (temp), AND (b) the calibration engine is RECEIPT-based not model-guessed -- run it on the 07-23 CSV and confirm it reproduces gas-positive/temp-negative with the credit-lag caveat, and that the credit attribution honestly handles the empty-ticker CSV constraint (family-level, not fabricated per-series). If the calibration is secretly the over-predicting model, or fabricates per-series credits, REFUTE.\nRETURN refuted true/false, severity, the specific state, the check that settles it.', { label: 'verify:' + lens, phase: 'Verify', schema: { type: 'object', required: ['lens', 'refuted', 'severity', 'finding'], properties: { lens: { type: 'string' }, refuted: { type: 'boolean' }, severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NONE'] }, finding: { type: 'string' }, failing_state: { type: 'string' }, settling_check: { type: 'string' } } } })))

phase('Deliver')

const final = await agent(RULES + '\n\nIMPLEMENTATION:\n' + String(impl).slice(0, 8000) + '\n\nVERIFIER VERDICTS:\n' + JSON.stringify(verds.filter(Boolean), null, 1) + '\n\nTASK — write docs/maker_handoffs/KALSHI_NETEV_FLAGGING_BUILD_2026-07-24.md and return it: (1) what changed plain English (the flagging now decides will-it-make-us-money = calibrated net, not reward-possible) + flag-off no-op proof; (2) the calibration engine + the JSON it produces on the 07-23 CSV (does it reproduce gas +1.1% / temp -9.2%?), with the credit-ticker-gap caveat; (3) every verifier verdict, lead with any CRITICAL/HIGH (esp. never-blocks-exits, false-skip-gas, or calibration-is-actually-model); (4) if green: pytest counts, new-file md5s, per-file md5-gated deploy step, rollback (flag unset = no-op / git revert); (5) expected LIVE effect (net-negative families auto-skip/reduce, net-positive gas kept, exits proceed); (6) THE CALIBRATION-REFRESH RUNBOOK -- re-run kalshi_netev_calibrate after the 2026-07-27T04:00Z export, update the table, so the gate stays receipt-calibrated; (7) Tier-3 -- operator sign-off + md5-gated deploy before live. Return the doc.', { label: 'deliver', phase: 'Deliver', effort: 'high' })

return { final, verds: verds.filter(Boolean), implPreview: String(impl).slice(0, 1200) }
