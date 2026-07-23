export const meta = {
  name: 'kalshi-root-fix-instruments',
  description: 'Root-fix every reporting/measurement defect and live-code flaw found today; commit but do NOT deploy (freeze in force)',
  phases: [
    { title: 'Fix', detail: 'one agent per file — ledger, studies, netting, quoter' },
    { title: 'Verify', detail: 'adversarial review of each fix: does it fix, does it break' },
    { title: 'Integrate', detail: 'full suite, smoke, synthesis' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE. Worktree: ${WT}  (branch claude/maker-kalshi-live)
Bash cwd drifts to another checkout — ALWAYS cd "${WT}" first, or use git -C "${WT}".

=== HARD CONSTRAINTS ===
1. A STATE FREEZE is in force (docs/maker_handoffs/KALSHI_FREEZE_2026-07-23T0219Z.md).
   **COMMIT YOUR FIX TO THE BRANCH. DO NOT DEPLOY IT.** No scp/rsync of code to the VPS, no
   systemctl, no live.env edits, no order placement. SSH is READ-ONLY (sudo cat/tail/md5sum) and
   only if you actually need it. The deployed artifact must remain md5 727ca7c59840a42b51c19e24c65a0982.
2. Kalshi venue only. Never touch MB/WB/EB/SB code or shared modules.
3. FILE OWNERSHIP IS EXCLUSIVE — edit ONLY the files assigned to you. Other agents are editing
   other files concurrently. Touching a file you do not own will cause a lost update.
4. Do not modify committed sandbox datasets (concentration_samples.jsonl md5
   e920bf99850279099897a79e8ad78dec; kalshi_transactions_2026-07-23.csv). They are evidence.

=== METHOD — THIS IS THE PART THAT MATTERS ===
* **PIN EVERY FIX.** Write the test FIRST, run it against the PRE-fix code, and CONFIRM IT FAILS.
  State the measured pin count ("N new tests, M of them fail on pre-fix"). A test that passes both
  before and after your fix is not a pin — say so rather than claiming coverage.
* **MEASURE BEFORE CLAIMING.** If a number looks impossible, IT IS WRONG — find the bug, do not
  explain it away. Today alone produced: a $156/period figure on an $85 account, a -$442
  settlement on an $85 account, a $604/day capture on a book that was 0% two-sided.
* Never break the existing suite. Baseline: \`cd kalshi_live && python -m pytest test_live_hardening.py -q\`
  is **83 passed**. It must still be >= 83 passed when you finish.
* Preserve function signatures and external interfaces unless the signature IS the bug.
* Return DATA: exact file:line, what changed, pin counts, and what you did NOT fix and why.

=== CANON (read before editing) ===
docs/maker_handoffs/KALSHI_LIP_RULE_CANON.md — §T terms, R1-R4 rules, §M1-M13 measurements.
  R1 period_reward is the TOTAL for a Time Period, not a rate. Normalise to $/day.
  R2 $1.00 is a THRESHOLD on the whole-period payout, then round down.
  R3 two-sided exclusion is MARKET-level (the BOOK must reach Target Size each side).
  R4 payout fraction = mean over snapshots of (yes_share + no_share)/2.
  §M10 maker fees: multiplier default 0 => FREE unless the series is in the Non-Standard table.
  §M13 credits LAG — they post once per Time Period, AFTER it closes. Never match credits to
       trading by calendar day.
`

phase('Fix')

const fixes = await parallel([
  () => agent(`${RULES}

YOU OWN: kalshi_live/kalshi_attribution_ledger.py  and a NEW kalshi_live/test_attribution.py

**THIS IS THE HIGHEST-VALUE FIX OF THE SET.** The ledger's \`rewards_residual\` has been garbage for
days (printed +$57.82/day, then +$75.17/day, on a ~$65 account). Three prior attempts to fix it
were tested and REFUTED — read the \`settlement_revenue\` docstring before starting so you do not
repeat them.

**THE ROOT CAUSE IS NOW KNOWN** (found today by an independent lane, needs your verification):
\`kalshi_attribution_ledger.py\` around **:108-109** books a fill with \`action='sell', side='no'\`
as a cash **INFLOW**, when it is the bot's **NO bid filling** — i.e. a cash **OUTFLOW**.
That is **148 of 300 fills**. Reported evidence: position reconstruction from fills is
**6/6 MISMATCH** against \`/portfolio/positions\` under the ledger's current reading, and
**6/6 EXACT** under the corrected reading.

DO:
1. **Verify the root cause yourself** before fixing. Reconstruct net position per ticker from
   \`/portfolio/fills\` under both sign conventions and compare against \`/portfolio/positions\`
   (\`position_fp\`, string, signed — see §M canon and the quoter's \`_held_cost\`). Report the
   match count both ways. If the reported 6/6 does not reproduce, SAY SO and do not fix blind.
   (Read-only VPS run is allowed: sudo -u polymarket env $(sudo cat /opt/pa2-maker-kalshi-live/live.env | grep -E "^KALSHI_(TRADING_MODE|LIVE_ARMED|API_KEY_ID|RSA_PRIVATE_KEY_PATH)=" | xargs) /opt/pa2-maker-kalshi-live/venv/bin/python <script>)
2. **Fix the sign convention at the root** — a single correct cash-flow function used everywhere,
   not a patch at each call site. Kalshi semantics: buying YES and buying NO are BOTH cash out;
   "sell yes" IS "buy no". Make the mapping explicit and documented.
3. **Fix \`fee_cost\` too** — canon §M9 notes the ledger never reads it, so fees leak into the
   residual. Fills expose \`fee_cost\`.
4. **VALIDATE the corrected residual against RECEIPTS.** We have ground truth now:
   \`kalshi_live/kalshi_transactions_2026-07-23.csv\` has 10 \`credit\` rows totalling **$25.21**
   (07-21: $1.14 $1.01 $1.73 $1.33 $7.39 $2.29 $3.74 = $18.63; 07-22: $1.88 $2.47 $2.23 = $6.58).
   The operator's web UI independently confirms ~$18.60 to inception. A corrected residual MUST
   reproduce those. Report the comparison honestly — if it does not match, the fix is incomplete.
5. Tests in **test_attribution.py** (your own file — do NOT touch test_live_hardening.py):
   pin the sign bug with a synthetic fill set where the old code is wrong and the new code right.

RETURN: verified-or-refuted root cause with match counts both ways, file:line changed, the
receipt validation ($25.21 / $18.63 / $6.58), pin count, and what remains unexplained.`,
    { label: 'fix:attribution-ledger', phase: 'Fix' }),

  () => agent(`${RULES}

YOU OWN: kalshi_live/kalshi_concentration_study.py, kalshi_live/kalshi_series_scan.py,
and a NEW kalshi_live/test_studies.py

Three measurement defects, all of which produced WRONG PUBLISHED NUMBERS today:

**D1 — selection bias (canon §M6b).** \`kalshi_concentration_study.py:133\` does
\`if not yl or not nl: continue\`, silently dropping contracts whose book is empty on one side.
That made §M2's "86.1% two-sided" **conditional on both sides being non-empty** and made §M1's
capture figures cover only non-empty books. FIX: record them and score them as R3 failures
(payout 0) instead of dropping. **Keep the frozen dataset untouched** — add the capability, and
make the report state clearly whether the loaded data was sampled with or without the filter.

**D2 — truncating page size.** \`kalshi_series_scan.py:146\` uses \`limit=1000\` on
\`/incentive_programs\`, which TRUNCATES and made the scan report KXRT=0 programs. The live quoter
uses **10000** and is correct. Fix, and verify the series count changes.

**D3 — degenerate ranking.** \`kalshi_series_scan.py:167-169\` sorts markets within a series by
per-program reward, but when a series shares ONE pool across all contracts the key is constant, so
"top N" is **arbitrary API order**. This is what produced §M5's false "100% two-sided / $7.42" on
KXEARNINGSMENTIONLMT and KXPM from an n=4 sample. FIX: break ties deterministically and, more
importantly, make the sampler take a RANDOM or FULL-CENSUS sample rather than a head-of-list slice,
and report which it used. Also surface the effective sample size honestly (§M5 counted 4 contracts
at one instant as if it were evidence).

ALSO: wire \`kalshi_live/series_fee_types.json\` (if present; else regenerate from
\`GET /series/{ticker}\` \`.fee_type\`, a two-valued enum \`quadratic\` = maker-free vs
\`quadratic_with_maker_fees\`) into the scan so every candidate is annotated maker-fee FREE/CHARGES.
Canon §M10: of series with active LIP programs, exactly one charges — KXAAAGASM.

Tests in **test_studies.py** (your own file). Pin each of D1/D2/D3 with a test that FAILS pre-fix.

RETURN: file:line for each fix, pin counts, and the before/after effect of D2 on series count and
of D1 on the two-sided rate.`,
    { label: 'fix:studies', phase: 'Fix' }),

  () => agent(`${RULES}

YOU OWN: a NEW file kalshi_live/kalshi_settlement_pnl.py (plus its tests inside that file or a
new test_settlement_pnl.py). Do not edit any existing module.

**THE PROBLEM.** Settlement P&L cannot currently be computed, and naive readings produce
IMPOSSIBLE numbers. Today a straightforward reading of \`/portfolio/settlements\` gave
**-$442 on an $85 account**. Root cause: \`yes_count_fp\` / \`no_count_fp\` /
\`yes_total_cost_dollars\` / \`no_total_cost_dollars\` are **CUMULATIVE LIFETIME** figures, not net
position. Real example (KXAAAGASD-26JUL23-4.100): yes_count 233.52, no_count 233.75,
yes_cost $83.4621, no_cost $151.2455, \`revenue\` **23** (integer CENTS = $0.23), market_result "no".
The near-equal counts are a FLATTENED book, not a $234 loss. Note \`revenue\` and \`value\` are
integer CENTS while \`*_dollars\` are dollar strings (same unit family as R1 — see §M7f).

BUILD a correct, tested settlement P&L attributor:
1. Derive NET position per contract and show that \`revenue\` reconciles to
   \`net_position_on_winning_side x $1\`. Verify on all available settlement rows and report the
   match rate. (On the row above: net = 233.75 - 233.52 = 0.23 no, result "no", revenue $0.23.)
2. Compute realised P&L per settled contract, and per EVENT (§T: event = ticker.split('-')[:2]).
3. **Cross-validate against the CSV**, which is receipt-grade:
   \`kalshi_live/kalshi_transactions_2026-07-23.csv\` — its \`trade\` rows carry
   \`realized_pnl_with_fees_dollars\` per closed lot. Your model must agree with the CSV on
   overlapping markets. Report the agreement rate; if it disagrees, YOUR MODEL IS WRONG.
4. An independent lane scored the KXAAAGASD-26JUL23 settlement at **-$8.20** (4 contracts,
   revenue $0.29 against $8.4933 of net cost basis). Reproduce or refute that number.
5. Make it runnable read-only and print a per-event table.

Read \`kalshi_attribution_ledger.py\` for existing helpers/conventions but DO NOT EDIT IT (another
agent owns it).

RETURN: the revenue-reconciliation match rate, the CSV agreement rate, the KXAAAGASD-26JUL23
figure, pin count, and any settlement row your model cannot explain.`,
    { label: 'fix:settlement-pnl', phase: 'Fix' }),

  () => agent(`${RULES}

YOU OWN: kalshi_live/maker_kalshi_quoter.py and kalshi_live/test_live_hardening.py
(you are the ONLY agent touching either — other agents own other files).

⚠ **THIS IS LIVE TRADING CODE AND THE DEPLOYED BUILD MUST NOT CHANGE.** Commit to the branch;
do NOT deploy. The VPS artifact stays md5 727ca7c59840a42b51c19e24c65a0982. Say so in your report.
The baseline suite is **83 passed** and must not regress.

Fix these THREE clear-cut defects. Each is a real live flaw found today.

**Q1 — \`event_deltas\` mis-fires on CATEGORICAL series (~:1579-1587).**
It buckets by \`"-".join(t.split("-")[:2])\`. On a categorical series (independent word-binaries,
\`mutually_exclusive: false\`, \`strike_type: custom\`) that collapses N INDEPENDENT risks into one
key: long 20 of one + short 20 of another reads **FLAT** while carrying two live naked exposures.
\`ladder_pairing\` already abstains safely on non-numeric strikes (\`_strike_of\` returns None,
~:1531) — **\`event_deltas\` has no equivalent guard.** Add one: the event aggregate must only be
applied where strikes are a genuine additive threshold ladder. Fail SAFE (treat as independent,
i.e. do not net) when it cannot prove additivity. Per canon §T this is why categorical series are
unsafe to admit at all — the guard makes that structural rather than a matter of allowlist hygiene.

**Q2 — \`_strike_of\` cannot parse non-numeric strikes (~:1526-1531).**
\`float("26JUL24")\` raises -> returns None -> \`ladder_pairing\` leaves **100% of that inventory
naked** with no error. Latent for gas too: legacy 4-part tickers like
\`KXAAAGASM-25MAR31-US-4.00\` return None, so if Kalshi restores a \`-US\` suffix, ladder pairing
goes DARK SILENTLY. Fix the parsing to handle the real ticker shapes, and — more importantly —
make the failure LOUD: a telemetry counter (e.g. \`strike_parse_failed\`) so silent darkness is
visible in the plan rows. Do NOT force a numeric interpretation on genuinely categorical strikes;
those must stay unpaired (canon: the ladder's deliberate asymmetry must not be "cleaned up").

**Q3 — the daily-loss meter can be inflated (~:844-874).**
It computes \`equity = balance + held COST BASIS\` against a FROZEN \`equity_day_start\`, with
\`KALSHI_DAILY_LOSS_HALT_USD=40\`. Measured today: equity 99.76 vs day_start 63.34 => the halt only
trips at 23.34, i.e. **$76.42 of effective room, 1.91x the nominal $40 quota — 76% of the account
can evaporate first.** Three compounding defects: (a) reward credits inflate the numerator while
day-start stays frozen, so room grows monotonically all day; (b) cost basis rather than mark hides
open losses; (c) a mid-day deposit adds room 1:1.
FIX the root: the quota must measure a DRAWDOWN THAT CANNOT BE INFLATED BY INCOME OR DEPOSITS —
e.g. track a high-water mark within the day and halt on drawdown from it, and/or exclude
non-trading cash movements. **State the behaviour change explicitly and list what could now halt
that would not have before** (Rule 4: no silent behaviour changes). Keep the existing env var name.
Note \`/portfolio/balance\` exposes \`portfolio_value\` (integer CENTS) = venue mark, if you want it —
but the current cost-basis choice was deliberate ("settled positions don't look like losses"), so
justify any switch.

DO NOT attempt in this task (report them as findings instead): the paired-inventory downside
invisible to \`naked_held_cost\`, and the missing exit path for a matched pair on a program-expired
ticker. Those are design changes needing their own review — write FAILING/xfail tests documenting
them if you can, but do not redesign the guards here.

PIN EVERYTHING: new tests must FAIL on pre-fix code. Report the measured pin count per defect.

RETURN: file:line per fix, pin counts, the full suite result, the deployed-md5-unchanged
confirmation, the Q3 behaviour-change statement, and the two findings you did not fix.`,
    { label: 'fix:quoter', phase: 'Fix' }),
])

log(`fix phase: ${fixes.filter(Boolean).length}/4 lanes returned`)

phase('Verify')

const LANES = [
  { k: 'attribution-ledger', r: fixes[0] },
  { k: 'studies', r: fixes[1] },
  { k: 'settlement-pnl', r: fixes[2] },
  { k: 'quoter', r: fixes[3] },
]

const verdicts = await parallel(LANES.filter(l => l.r).map(l => () => agent(`${RULES}

TASK — ADVERSARIALLY VERIFY a fix. You are READ-ONLY: do NOT edit any file. Run tests, read code,
run read-only probes. **DEFAULT TO "NOT FIXED" IF UNCERTAIN.**

Lane: **${l.k}**
What the fixing agent reported:
${String(l.r).slice(0, 6000)}

Check, in this order:
1. **Does the claimed root cause actually hold?** Re-derive it independently. Today three
   "confident" numbers died on verification and six of my own bugs were self-caught — assume the
   report is optimistic until you reproduce it.
2. **Is the pin real?** Check out / reconstruct the PRE-fix version of the changed file
   (\`git show HEAD~1:<path>\` or the diff), run the NEW tests against it, and confirm they FAIL.
   A test that passes pre-fix is not a pin, however good the fix sounds. Report the number you
   measured, not the number claimed.
3. **Did it break anything?** Run the full suite: \`cd "${WT}/kalshi_live" && python -m pytest -q\`.
   Baseline was 83 passed for test_live_hardening.py. Also \`python dryrun_smoke.py\` if the quoter
   was touched.
4. **Is it a ROOT fix or a patch?** A sign flipped at one call site instead of one correct
   cash-flow function is a patch. A guard added to one branch instead of the shared helper is a
   patch. Say which it is.
5. **Silent behaviour change?** For the quoter lane especially — did anything change that a caller
   or a live cycle depends on, without being stated?
6. **Freeze integrity:** confirm nothing was deployed — VPS artifact must still be md5
   727ca7c59840a42b51c19e24c65a0982 (read-only ssh check), and no live.env write occurred.

RETURN: verified true/false per claim, MEASURED pin count, suite result, root-vs-patch verdict,
any regression, and the single biggest remaining risk in this lane.`,
  { label: `verify:${l.k}`, phase: 'Verify', schema: {
    type: 'object',
    required: ['lane','root_cause_holds','measured_pin_count','suite_result','root_or_patch','regressions','biggest_remaining_risk'],
    properties: {
      lane: { type: 'string' },
      root_cause_holds: { type: 'boolean' },
      root_cause_note: { type: 'string' },
      measured_pin_count: { type: 'string' },
      claimed_vs_measured_pin_mismatch: { type: 'boolean' },
      suite_result: { type: 'string' },
      root_or_patch: { type: 'string', enum: ['ROOT','PATCH','MIXED'] },
      regressions: { type: 'string' },
      silent_behaviour_change: { type: 'string' },
      freeze_intact: { type: 'boolean' },
      biggest_remaining_risk: { type: 'string' },
    },
  } })))

phase('Integrate')

const synthesis = await agent(`${RULES}

TASK — INTEGRATE AND REPORT. Four fix lanes ran, each adversarially verified.

FIX REPORTS:
${LANES.map(l => `### ${l.k}\n${String(l.r).slice(0, 4000)}`).join("\n\n")}

VERIFICATION VERDICTS:
${JSON.stringify(verdicts.filter(Boolean), null, 1)}

DO:
1. Run the full suite yourself: \`cd "${WT}/kalshi_live" && python -m pytest -q\` and
   \`python dryrun_smoke.py\`. Report the actual numbers.
2. Confirm the freeze: deployed md5 must still be 727ca7c59840a42b51c19e24c65a0982 (read-only ssh),
   \`git -C "${WT}" status --porcelain\` should show no unintended stray files, and the frozen
   datasets must be unmodified (concentration_samples.jsonl md5 e920bf99850279099897a79e8ad78dec).
3. Make sure every lane's work is COMMITTED on claude/maker-kalshi-live with a descriptive message.
   If a lane left uncommitted work, commit it (one commit per lane, do not squash lanes together).
4. Write \`docs/maker_handoffs/KALSHI_INSTRUMENT_FIXES_2026-07-23.md\` recording, per defect:
   symptom, root cause with file:line, the fix, the MEASURED pin count (verifier's number, not the
   fixer's claim), and what is still unfixed. Include a section "WRONG NUMBERS THIS RETIRES" listing
   the specific published figures that were wrong because of each defect.
5. State plainly which defects are FIXED, which are PARTIALLY fixed, and which remain OPEN —
   especially the two the quoter lane was told not to attempt (paired-inventory downside invisible
   to the breakers; no exit path for a matched pair on a program-expired ticker).
6. **Deployment readiness:** the freeze means nothing shipped. State exactly what a deploy would
   now change on the live bot, so the operator can make that call with the blast radius in front
   of them. Be specific about the quoter changes — they alter live trading behaviour.

Be honest about disagreements between a fixer and its verifier — report the verifier's number.
If a lane failed or returned nothing, say so explicitly rather than papering over it.`,
  { label: 'integrate', phase: 'Integrate' })

return { synthesis, verdicts: verdicts.filter(Boolean) }
