export const meta = {
  name: 'kalshi-capital-accounting-rootfix',
  description: 'Root-fix the committed-capital accounting so paired/binary exposure is counted at TRUE risk, not worst-case gross — end the cap-raising treadmill',
  phases: [
    { title: 'Ground', detail: 'measure the gross-vs-net gap on the live book; understand exactly what Kalshi reserves' },
    { title: 'Design', detail: 'the correct capital measure + guard, with derivation' },
    { title: 'Refute', detail: 'adversarial review — where does net accounting under-reserve and blow up' },
    { title: 'Deliver', detail: 'spec, tests, migration' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE. Worktree: ${WT} (branch claude/maker-kalshi-live). cd there first — cwd drifts.

=== HARD CONSTRAINTS ===
1. READ-ONLY on the live system. No deploys, no live.env writes, no orders, no systemctl.
   Public API reads fine (no keys, >=0.3s spacing, paginate limit=10000 NOT 1000).
   Authed READ-ONLY on the VPS via:
     sudo -u polymarket env $(sudo cat /opt/pa2-maker-kalshi-live/live.env | grep -E "^KALSHI_(TRADING_MODE|LIVE_ARMED|API_KEY_ID|RSA_PRIVATE_KEY_PATH)=" | xargs) /opt/pa2-maker-kalshi-live/venv/bin/python <script>
2. CREATE ONLY NEW FILES for analysis. Do NOT edit existing modules — a DESIGN spec is the
   deliverable, not a code change to maker_kalshi_quoter.py. If you write a reference
   implementation, put it in a NEW file. maker_kalshi_quoter.py stays md5 727ca7c59840a42b51c19e24c65a0982.
3. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.

=== THE DEFECT (measured, live, today) ===
The bot's committed-capital guard uses GROSS held cost. Live at 18:07Z: cash $156, held_cost
$91.54, committed $149.32 of a $150 cap — PINNED, refusing to deploy $156 of real cash. The
operator has now raised the cap four times today (85->100->150->250) and it re-pins within cycles
because held inventory grows into it. THIS IS A TREADMILL. Root-fix it.

THE CODE (maker_kalshi_quoter.py, verified this session — do NOT edit, this is the spec target):
- :1193-1198  committed = sum(resting order price*count for not-cancelled standing) + held_cost
- :1213       if not reducing and committed + cost > MAX_TOTAL_CAPITAL: skip   (accumulating only)
- :879 (comment) "the CAPITAL cap (committed vs MAX_TOTAL_CAPITAL) still uses GROSS held_cost"
- :1503 (comment) "|pos|*1 reserves the max. Real committed capital must include this..."
- held_cost comes from _held_cost(): reads market_exposure_dollars per position, falls back to
  ~$1.00/contract (abs(n)) when the venue omits it — the branch the doctrine lane flagged as a
  phantom-spike source.
- ladder_pairing(held_by) already computes the FLOORED pairs (+low/-high in one event settle to
  >=$1/pair, risk = strike gap ~pennies) vs the naked remainder. naked_held_cost() already exists
  and is what the RISK breaker uses. So the machinery to measure NET exposure EXISTS; the capital
  cap just doesn't use it.

=== WHAT KALSHI ACTUALLY RESERVES (this is the crux — VERIFY IT, do not assume) ===
Kalshi is fully collateralised. The canonical question: for a resting BUY order and for a held
position, how much cash does the venue ACTUALLY lock? Candidates:
  - a YES bid at price p reserves p*count (you can lose at most your stake)
  - a NO bid at price q reserves q*count
  - a two-sided pair (YES@p + NO@q, same market) where p+q<1 is nearly self-funding: if BOTH fill
    you pay p+q and settlement returns exactly $1, so net cash at risk ~= (p+q-1)*count which can
    be NEGATIVE. Does Kalshi net this? (canon §M4a/§M7f note Kalshi returns collateral on
    offsetting positions.)
  - a laddered event (+low strike / -high strike) settles to >=$1/pair — Kalshi's event-level
    margining. Canon retracted an earlier "4.3x margin netting" CLAIM as a MISREAD (running tab
    correction) — so DO NOT trust folklore; MEASURE what the venue reserves from the balance API.
VERIFY against /portfolio/balance: balance_dollars is free cash, portfolio_value is mark. The
DIFFERENCE between account value and free cash after known positions IS the venue's actual
reservation. Reconcile it. If our gross 'committed' hugely exceeds what the venue locked, that gap
IS the bug quantified.

=== METHOD ===
* MEASURE BEFORE CLAIMING. If a number looks impossible it IS wrong — this lane produced a
  $156/period figure on an $85 account and a -$442 settlement. Find the bug.
* Units: period_reward is fixed-point x10000; balance/portfolio_value/revenue are integer CENTS;
  *_dollars fields are dollar strings. (canon §M7f). Mishandling these has burned multiple lanes.
* A capital guard has TWO failure directions. Over-reserving (today's bug) leaves money idle and
  is what we're fixing. UNDER-reserving lets the bot commit more than it can fund -> Kalshi
  rejects orders, or worse, we hold unfundable risk. The fix must not trade the first bug for the
  second. Every proposal states its behaviour in BOTH directions.
* State sample size and what is NOT covered on every number.
`

phase('Ground')

const ground = await parallel([
  () => agent(`${RULES}

TASK — QUANTIFY THE GAP ON THE LIVE BOOK. What does the venue actually reserve vs what our guard counts?

DO (authed READ-ONLY on the VPS):
1. Pull /portfolio/balance (balance_dollars = free cash, portfolio_value = mark, both relevant),
   /portfolio/positions (position_fp signed, market_exposure_dollars), and resting orders.
2. Compute FOUR numbers on the current book and lay them side by side:
   a. our guard's 'committed' = sum(resting price*count) + gross held_cost  (the bug)
   b. GROSS held cost alone
   c. NET held cost after ladder_pairing() — import the live module READ-ONLY and call it
      (kalshi_live/maker_kalshi_quoter.py's ladder_pairing / naked_held_cost)
   d. the VENUE's actual reservation = (account value) - (free cash) ... reconcile what portion
      of the account Kalshi has actually locked. Show your arithmetic in cents.
3. THE KEY RATIO: how far does (a) exceed (d)? That gap, in dollars, is the idle-capital bug
   quantified. Break it down by what causes it: paired ladder legs counted gross, two-sided
   near-self-funding quotes counted gross, the $1/contract fallback in _held_cost, resting orders
   double-counted against held.
4. Sanity: does free cash + venue reservation reconcile to account value to the cent? If not, you
   have mismodelled the reservation — say so and fix before reporting.

RETURN: the four-number table with arithmetic, the (a)-minus-(d) gap in dollars, the causal
breakdown, and the reconciliation check.`,
    { label: 'ground:live-gap', phase: 'Ground' }),

  () => agent(`${RULES}

TASK — ESTABLISH WHAT KALSHI RESERVES, EMPIRICALLY. This is the foundation the whole fix rests on.

We must not GUESS the reservation model. Derive it from data we can read.

DO (authed READ-ONLY):
1. From the transaction receipts (kalshi_live/kalshi_transactions_2026-07-23.csv) and the balance
   API, reconstruct: when we placed a resting BUY (YES bid at p / NO bid at q), how much did free
   cash drop? When a fill happened? When a pair completed? Track balance_dollars deltas against
   the fills that caused them.
2. Specifically test the self-funding hypothesis: find (or identify from history) a case where we
   held YES@p and NO@q in the SAME market with p+q < 1. Did the venue reserve p*ct + q*ct (gross)
   or ~(p+q-1)*ct netted? The balance delta answers this.
3. Test event-level laddering: for a +low/-high pair in one event, what did the venue lock? Canon
   retracted a "4.3x netting" claim as a misread of fill cash cost — so verify from balance
   deltas, not from that claim. State clearly whether Kalshi nets at the MARKET level, the EVENT
   level, both, or neither.
4. Resting vs filled: does an unfilled resting BUY reserve cash immediately, or only on fill?
   (Canon §M7f / running-tab correction: "resting orders do NOT visibly deduct from
   balance_dollars" was one reading — verify it, because if TRUE it means our guard counting
   resting orders against the cap at all is itself part of the bug.)

RETURN: the empirically-derived reservation rule (market-level? event-level? resting reserves or
not?), each claim backed by a balance-delta observation with the numbers, and an explicit list of
anything you could NOT determine from available data.`,
    { label: 'ground:venue-reservation', phase: 'Ground' }),
])

log(`ground: ${ground.filter(Boolean).length}/2 lanes`)

phase('Design')

const design = await agent(`${RULES}

TASK — DESIGN THE ROOT FIX for the committed-capital guard.

LIVE GAP:
${String(ground[0]).slice(0, 9000)}

VENUE RESERVATION MODEL:
${String(ground[1]).slice(0, 9000)}

Design the correct capital guard, with derivation, targeting maker_kalshi_quoter.py:1193-1213:

1. **The correct 'committed' measure.** Replace gross held_cost with a measure that matches what
   Kalshi actually reserves (from the Ground phase). If the venue nets paired/laddered exposure,
   the guard must use ladder_pairing()/naked-style netting that ALREADY EXISTS in the module.
   If resting BUYs don't reserve until fill, decide whether they belong in the cap at all.
   State the exact formula.
2. **Preserve the safe direction.** The current guard already exempts REDUCING (unwind) creates
   (:1204-1213) — keep that. The new measure must NEVER let the bot commit more cash than it can
   fund: cross-check against free cash (balance_dollars) as a hard ceiling, so that even if the
   net model is wrong, an order that would overdraw is refused. Belt and suspenders.
3. **Kill the $1/contract fallback phantom.** _held_cost falls back to abs(n) (~$1/contract) when
   market_exposure_dollars is missing — the doctrine lane showed this can spike held by $100+ and,
   with the new loss meter, trip a false halt. Specify the correct fallback (use the position's
   actual avg cost from fills, or the mark, or fail-closed — derive which).
4. **What replaces MAX_TOTAL_CAPITAL semantically.** Right now it's a gross-notional cap that the
   operator keeps raising. Should the real limit be: net capital at risk? free-cash floor? a
   per-event exposure cap (which also addresses the concentration gap the operator raised
   earlier)? Propose the limit(s) that make the knob stop needing to be raised.
5. **Behaviour in BOTH directions**, explicitly: what does the fix now ALLOW that was blocked
   (the idle $156), and what does it now BLOCK that was allowed (any new over-commit risk)?
6. **Migration**: how MAX_TOTAL_CAPITAL maps to the new scheme, rollback, and how to verify the
   fix reconciles to the venue reservation on the live book before trusting it.

Every parameter needs a derivation or measurement. Label GUESSes. Provide pseudo-code for the
replacement guard and the specific tests that would pin it (a test that FAILS on the current gross
code and PASSES on the fix).

RETURN: the formula, the dual-direction behaviour statement, the migration table, pseudo-code,
and the pin tests.`,
  { label: 'design', phase: 'Design' })

phase('Refute')

const refutations = await parallel([
  'under-reservation-blowup', 'venue-model-wrong', 'fallback-and-edge-cases', 'does-it-end-the-treadmill',
].map(lens => () => agent(`${RULES}

TASK — ADVERSARIALLY REFUTE the capital-accounting fix. Lens: **${lens}**.
DEFAULT TO REFUTED IF UNCERTAIN. Over-reserving wastes money; UNDER-reserving can blow up the
account. Find the failure.

THE PROPOSAL:
${String(design).slice(0, 13000)}

Through your lens:
- under-reservation-blowup: construct a book where the NET measure says "safe to add" but real
  cash at risk exceeds free cash. Can paired legs come UN-paired (one leg fills, the other is
  cancelled/rejected) leaving us gross-exposed with the guard having admitted on a net basis?
  This is the deadliest failure — a hedge that only exists on paper. Trace what happens when
  ladder_pairing assumed a pair that then half-fills.
- venue-model-wrong: is the empirically-derived reservation rule actually right, or inferred from
  too few balance deltas? Canon ALREADY retracted one netting claim (the 4.3x) as a misread. What
  if Kalshi does NOT net the way the Ground phase concluded? Does the fix over-commit then?
  What's the sample size behind the reservation rule?
- fallback-and-edge-cases: the $1/contract fallback, a position with no fills history, a market
  mid-settlement, an event with an odd number of legs, a resting order that partially filled.
  Where does the new formula divide-by-zero, read a stale mark, or mis-net?
- does-it-end-the-treadmill: even if correct, does it actually stop the operator having to raise
  the cap? Or does net exposure ALSO grow into whatever the new limit is? Is the real fix a
  per-event cap rather than a global one? Would this fix have prevented today's four raises?

RETURN: refuted true/false, severity, the specific failing state, and the measurement that settles it.`,
    { label: `refute:${lens}`, phase: 'Refute', schema: {
      type: 'object',
      required: ['lens','refuted','severity','defect','settling_measurement'],
      properties: {
        lens: { type: 'string' },
        refuted: { type: 'boolean' },
        severity: { type: 'string', enum: ['BLOCKER','HIGH','MEDIUM','LOW','NONE'] },
        defect: { type: 'string' },
        failing_state: { type: 'string' },
        settling_measurement: { type: 'string' },
      },
    } })))

phase('Deliver')

const final = await agent(`${RULES}

TASK — WRITE THE ROOT-FIX SPEC. Operator: "deep dive how we root fix instead of this bullshit" —
"this bullshit" being raising MAX_TOTAL_CAPITAL over and over (85->100->150->250 today) while real
cash sits idle because the guard counts GROSS.

LIVE GAP: ${String(ground[0]).slice(0, 5000)}
VENUE RESERVATION: ${String(ground[1]).slice(0, 5000)}
DESIGN: ${String(design).slice(0, 11000)}
REFUTATIONS: ${JSON.stringify(refutations.filter(Boolean), null, 1)}

Write \`docs/maker_handoffs/KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md\` and return it:

1. **THE BUG IN ONE SENTENCE + THE GAP IN ONE NUMBER** — how many idle dollars the gross count
   creates on the live book right now.
2. **WHAT KALSHI ACTUALLY RESERVES** — the empirically-derived rule, with the balance-delta
   evidence and its sample size. If it could not be fully determined, say exactly what's unknown.
3. **THE FIX** — the corrected formula, the free-cash hard ceiling (belt-and-suspenders against a
   wrong net model), the $1/contract fallback replacement, and whether the real limit should be
   per-event rather than global. Pseudo-code + pin tests.
4. **DUAL-DIRECTION SAFETY** — what it unblocks (idle cash) and what it must still block
   (over-commit). Lead with the under-reservation risk the refuters found, and how the fix guards it.
5. **WOULD IT HAVE ENDED THE TREADMILL?** — apply it to today's four raises. If net exposure would
   ALSO have grown into the cap, say so and pivot to the per-event cap as the real answer.
6. **MIGRATION + ROLLBACK**, and the reconciliation check that must pass on the live book before
   the fix is trusted (net-committed must equal the venue's actual reservation to the cent).
7. **INTERIM** — until the code fix ships, what is the least-bad config? (Today's answer was
   "raise the cap"; state the risk that carries — MAX_TOTAL_CAPITAL=250 now sits above account
   value $\~247, and both the naked brake and this cap are effectively inert, leaving only
   DAILY_LOSS_HALT_USD=40.)

Report refuters' verdicts even where they contradict the designer — especially any under-reservation
BLOCKER. Flag every GUESS. Attach sample sizes. This is a CODE change requiring full ship discipline
(pytest + adversarial review) before deploy — say so.`,
  { label: 'deliver', phase: 'Deliver' })

return { final, refutations: refutations.filter(Boolean) }
