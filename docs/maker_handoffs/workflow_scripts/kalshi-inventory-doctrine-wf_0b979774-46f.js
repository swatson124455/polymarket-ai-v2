export const meta = {
  name: 'kalshi-inventory-doctrine',
  description: 'Replace the crude exit-cap ladder with a real inventory-management doctrine, grounded in professional market-making practice and our own data',
  phases: [
    { title: 'Research', detail: 'academic models, practitioner behaviour, prediction-market specifics, toxicity' },
    { title: 'Ground', detail: 'what our own fills/positions say, and the reward-vs-skew exchange rate' },
    { title: 'Design', detail: 'concrete doctrine for OUR objective function' },
    { title: 'Refute', detail: 'adversarial review' },
    { title: 'Deliver', detail: 'spec + migration path' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE. Worktree: ${WT} (branch claude/maker-kalshi-live). cd there first — cwd drifts.

=== HARD CONSTRAINTS ===
1. READ-ONLY on the live system. No deploys, no live.env writes, no orders, no systemctl.
   Public Kalshi API reads fine (no keys, >=0.3s spacing, paginate limit=10000 NOT 1000).
2. CREATE ONLY NEW FILES. Another workflow may be editing kalshi_live/*.py — do not edit
   existing modules. New analysis goes in new files.
3. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.

=== WHAT WE ARE AND WHAT WE ARE OPTIMISING (this is NOT classic market making) ===
We are a rewards-farming maker on Kalshi's Liquidity Incentive Program. Read
docs/maker_handoffs/KALSHI_LIP_RULE_CANON.md FIRST — especially R1-R4 and §T.

**Our objective is NOT spread capture.** It is:
    maximise   LIP rewards earned   MINUS   adverse selection on the inventory we accumulate
Rewards are paid for RESTING QUOTES, not for fills. Fills are a COST, not revenue.

**THE CENTRAL TENSION, and it is specific to us:**
LIP scores an order as \`DF^N x size\` where N = ticks from the side's reference price and
**DF = 0.50** on our series. So **every tick away from reference HALVES that order's reward
credit.** A classic market maker manages inventory by SKEWING quotes away from mid — for us that
skew is directly, exponentially expensive in reward terms. One tick = -50%. Two ticks = -75%.
This exchange rate is exact and known, and it is the heart of the design problem.

Also binding:
- R2: a market's whole-Time-Period payout below $1.00 pays ZERO.
- R3: if the BOOK lacks Target Size (1000 contracts) on either side, the snapshot is excluded and
  pays NOBODY — so quoting one-sided forfeits that side's score but does not zero the market.
- R4: score = normalised_yes + normalised_no, summed. Both sides earn independently.

=== THE CURRENT (CRUDE) DESIGN WE WANT TO REPLACE ===
- \`MAX_UNWIND_LOSS = $0.02\`: a hard cap, measured against COST BASIS, on how much loss the
  reducing-side order may crystallise. Fixed, time-blind, market-life-blind.
- \`INV_SOFT_CT=15 / INV_HARD_CT=60\`: throttle the accumulating side one tick inside at SOFT,
  pull it at HARD.
- \`THROTTLE_STEP_TICKS=1\`: the step-inside distance.
- \`HELD_MAX_USD\` (naked-risk breaker, now 50): a GLOBAL hard threshold -> reduce-only for the
  ENTIRE bot.
- \`SETTLE_UNWIND_MIN=20\`: taker backstop arms 20 min before close (TAKER_FLATTEN now = 1).

=== THE OBSERVED FAILURES THIS MUST FIX (all measured today, all real) ===
F1. **DEADLOCK.** Naked risk $20.17 vs a $20 cap — over by 17 cents. Breaker -> global
    reduce-only -> only exit quotes rest -> the exit is parked OFF reference (at_ref_pct=0.0) and
    never fills -> naked never falls -> stuck. ~60 minutes idle, 17 markets in footprint, 1
    quoted, 16 gated out. A hard threshold with no release path.
F2. **DEATH AT SETTLEMENT.** 2026-07-22: 20 positions expired worthless, -$40.62, while all other
    trading that day made +$16.40. Entries 0.05-0.76, exits 0.00, held 117-147 min. The $0.02 cap
    made exit structurally impossible once price moved more than 2c against basis.
    Worst 6 minutes = 125% of the day's loss, all clustered at :30 past the hour (hourly expiries).
F3. **THE CAP IS MEASURED AGAINST SUNK COST.** Once a position is 30c underwater, no cap below
    30c can ever permit an exit — the rule guarantees paralysis exactly when exit matters most.
F4. **GLOBAL BRAKE ON A LOCAL PROBLEM.** All naked risk was in ONE event (weekly gas); it halted
    16 daily-gas markets with zero inventory and no relationship to it.
F5. **THE -low/+high SHAPE.** Today GASD settled BETWEEN our strikes: we held short 4.090 and long
    4.105; BOTH legs expired worthless. The ladder deliberately refuses to pair that shape (it is
    not a hedge, it is double exposure paying only at the extremes) — but nothing prevents
    ACQUIRING it.

=== METHOD ===
* MEASURE BEFORE CLAIMING. If a number looks impossible it IS wrong — today produced a
  $156/period figure on an $85 account and a -$442 settlement on the same account. Find the bug.
* State sample size and what is NOT covered on every number.
* Cite sources properly: for literature give author/year/title; for web give the URL. Do not
  paraphrase a model you have not actually read the form of.
* We are SMALL: $85 total capital, 20 contracts/side join size, 2-minute cycle, ~1000-contract
  Target Size (so we are ~2% of the qualifying set). Any doctrine requiring continuous requoting,
  sub-second reaction, or size we do not have is USELESS HERE — say so rather than recommending it.
`

phase('Research')

const research = await parallel([
  () => agent(`${RULES}

TASK — THE ACADEMIC CANON ON MARKET-MAKER INVENTORY MANAGEMENT. What is the actual state of the
art, and which parts survive contact with our constraints?

Research and report the real mathematical form (not vibes) of:
1. **Avellaneda-Stoikov (2008)**, "High-frequency trading in a limit order book". Get the actual
   reservation-price and optimal-spread expressions, including the inventory term and the
   time-to-terminal term. THE KEY QUESTION: A-S manages inventory by shifting the reservation
   price so quotes become asymmetric — the position mean-reverts through NORMAL QUOTING rather
   than through a separate exit order. Does that reframing dissolve our F1/F3 (a separate exit
   order with a sunk-cost cap that cannot fill)?
2. **Guéant, Lehalle, Fernandez-Tapia** — closed-form approximations, inventory bounds q_max,
   and behaviour at the inventory boundary. How do they avoid the hard-wall deadlock (F1)?
3. **Terminal inventory penalty / liquidation cost.** How is urgency near T derived rather than
   bolted on? This is exactly our F2.
4. **Glosten-Milgrom / Kyle** on adverse selection: when flow is informed, does WIDENING help, or
   must you stop quoting that side entirely? Our F2 losses look like informed flow (positions
   carried into resolution, dying at 0).
5. Anything on **asymmetric / one-sided quoting** as an inventory tool, and on **quote skew vs
   inventory limits** as alternative mechanisms.

THEN, critically: for EACH model, state what it assumes that WE VIOLATE. Candidates: continuous
requoting, negligible fees, no rebate structure, symmetric information, unbounded inventory,
mid-price diffusion. **We are a $85 account on a 2-minute cycle earning DF^N-weighted rebates for
resting.** A model that assumes P&L comes from spread capture is answering a different question.

RETURN: the actual functional forms, the inventory/time terms, and a blunt per-model verdict on
transferability to a 2-minute-cycle rebate farmer.`,
    { label: 'research:academic', phase: 'Research' }),

  () => agent(`${RULES}

TASK — WHAT DO REAL MARKET MAKERS AND LARGE PARTICIPANTS ACTUALLY DO? Practitioner behaviour, not
theory. Prediction markets specifically where you can find it, adjacent venues where you cannot.

Research and report:
1. **Prediction-market makers on Kalshi and Polymarket.** Designated market makers, LIP/rewards
   farmers, known large participants. What inventory discipline do they describe? Any public
   write-ups, forum/Discord/substack posts, or interviews on running a rewards-farming book.
   (Known relevant: kalshi.com/incentives, help.kalshi.com, the CFTC LIP filings, and
   substack/blog coverage of "garage-band market makers".)
2. **How professionals handle an inventory they did not want.** Specifically: do they place a
   dedicated exit order with a loss limit (what we do), or do they skew their two-sided quote and
   let the market bring them flat? What is the actual practice for "working out" inventory?
3. **Hard limits vs soft penalties.** Do desks run hard position caps that stop quoting, or
   continuous inventory penalties that make quoting progressively lopsided? What do risk systems
   do when a limit binds and the position cannot be reduced (our F1)?
4. **End-of-life / expiry discipline** in venues with hard settlement — options MMs pinning at
   expiry, futures roll, binary/event contract settlement. What is the doctrine for "flat into
   settlement" and how early does it start? Is it time-based or moneyness-based?
5. **Rebate/maker-incentive farming as a distinct strategy** — crypto exchange maker-rebate
   programmes, equity maker-taker rebates. How do rebate farmers handle inventory, given (like us)
   their revenue comes from resting rather than from spread?

Use WebSearch/WebFetch. Prefer primary sources; label anything anecdotal AS anecdotal. If a claim
is folklore rather than documented, say so — do not launder a forum post into a recommendation.

RETURN: documented practices with sources, explicitly separated into (a) well-documented,
(b) plausible/anecdotal, (c) not found. Flag anything that assumes size or speed we do not have.`,
    { label: 'research:practitioner', phase: 'Research' }),

  () => agent(`${RULES}

TASK — THE ONE THING THAT MAKES US DIFFERENT: quantify the REWARD COST OF SKEW, exactly.

Every classic inventory tool moves quotes away from the touch. For us that is exponentially
expensive: LIP credit is \`DF^N x size\` with **DF = 0.50**, N = ticks from that side's reference.
Before any doctrine can be designed, this exchange rate must be measured, not assumed.

DO:
1. **Derive and verify the reward cost of skew.** Using the recorder's real CFTC scoring core
   (\`scripts/maker_kalshi_recorder.py\`: \`qualifying_walk\`, \`side_share\` — READ, do not edit)
   and real books from our allowlist, compute our share and $/day at N = 0, 1, 2, 3, 4 ticks
   inside/away from reference. Confirm empirically whether it really halves per tick, or whether
   the qualifying-set boundary makes it worse (canon: the 1-tick step ZEROED credit in 12% of
   snapshots because the order fell OUT of the qualifying set entirely — a cliff, not a slope).
   **That cliff is the crux: skew may not be a smooth cost at all.**
2. **Price the trade-off in common units.** Adverse selection is measurable from our own receipts:
   \`kalshi_live/kalshi_transactions_2026-07-23.csv\` (244 trades, 07-20..22) gives realised P&L
   per closed lot; canon §M8 measured GAS at -1.97% of notional and TEMP at -31.95%. So: how many
   cents of adverse selection does one tick of skew have to prevent to pay for its reward loss?
   Express as a breakeven, per series, with sample sizes.
3. **Asymmetric alternative.** R4 says the two sides score independently and additively. So
   instead of skewing BOTH sides, we could keep the reducing side AT reference (full credit) and
   only pull/shrink the accumulating side. Quantify: what fraction of reward do we keep under
   (a) symmetric skew N ticks, (b) accumulating side pulled entirely, (c) accumulating side
   floored at MIN_QUOTE_CT (today's reduce-only plug-in)? Which is cheapest per unit of inventory
   control?
4. **Size vs price.** We control size as well as price, and size is LINEAR in the score while
   price is EXPONENTIAL (DF^N). Is shrinking size strictly cheaper than moving price for the same
   inventory effect? Quantify — this may be the single most important result of the whole job.

RETURN: the measured DF-cliff behaviour (not the theoretical curve), the breakeven table, the
ranking of inventory tools by reward-cost per unit of control, and sample sizes throughout.`,
    { label: 'research:skew-cost', phase: 'Research' }),

  () => agent(`${RULES}

TASK — GROUND EVERYTHING IN OUR OWN TAPE. What actually happened to our inventory, and would any
alternative doctrine have helped?

Data available (READ-ONLY):
- \`kalshi_live/kalshi_transactions_2026-07-23.csv\` — 244 trades + 10 credits, 07-20..22,
  receipt-grade, with per-lot realised P&L, entry/exit prices, timestamps.
- \`/opt/pa2-maker-kalshi-live/plans-2026072*.jsonl\` via READ-ONLY ssh (sudo cat) — per-cycle
  telemetry: naked_held_usd, paired_ct, breaker_reduce_only, at_ref_pct, quoted_markets, etc.
- \`/portfolio/fills\`, \`/portfolio/settlements\`, \`/portfolio/positions\` (authed, READ-ONLY;
  run on the VPS with the live.env creds pattern).
- \`kalshi_live/concentration_samples.jsonl\` (FROZEN md5 e920bf99850279099897a79e8ad78dec) — raw
  books with program params.

ANSWER:
1. **Anatomy of the losers.** For the 20 positions that expired worthless on 07-22 (-$40.62):
   reconstruct the price path from entry to death where possible. WHEN did each become
   unexitable under a $0.02 basis cap — how long did we hold an already-doomed position? Was
   there a window where exiting was cheap and we declined because of the cap?
2. **Adverse selection or noise?** Do positions move against us systematically after we fill
   (informed flow), or is it symmetric and we simply notice the losers? Measure a post-fill
   markout at several horizons on our real fills. **Apply a positive control** and state it.
   If the sample cannot support the claim, say UNMEASURED — canon records eight toxicity metrics
   built and refuted by their own authors.
3. **Would inventory-skew quoting have worked HERE?** Counterfactual on real books: had we skewed
   the reducing side to the touch (or 1 tick through) instead of parking it at a basis-capped
   price, would it have filled? Use the recorded books to check whether a fillable price existed.
   Be honest about queue position being unknowable.
4. **The F5 shape.** How often do we ACQUIRE the -low/+high combination (short a low strike, long a
   high strike, same event)? Today both legs died when gas settled between them. Is this frequent
   or a one-off? Could an acquisition-time rule have prevented it?
5. **Was the deadlock (F1) rare or chronic?** Across all plans files: how many cycles were spent
   in breaker_reduce_only, how many of those had naked risk FLAT (not falling) — i.e. genuinely
   stuck rather than working down? What fraction of total uptime was lost to it?

RETURN: numbers with sample sizes, the loser anatomy, the markout result (or UNMEASURED with the
reason), the counterfactual verdict, F5 frequency, and total uptime lost to deadlock.`,
    { label: 'research:our-tape', phase: 'Research' }),
])

log(`research: ${research.filter(Boolean).length}/4 lanes`)

phase('Design')

const design = await agent(`${RULES}

TASK — DESIGN THE INVENTORY DOCTRINE. Replace the crude ladder with something principled, sized
for OUR account and OUR objective.

ACADEMIC CANON:
${String(research[0]).slice(0, 9000)}

PRACTITIONER BEHAVIOUR:
${String(research[1]).slice(0, 9000)}

REWARD COST OF SKEW (the binding constraint):
${String(research[2]).slice(0, 9000)}

OUR OWN TAPE:
${String(research[3]).slice(0, 9000)}

DESIGN, with concrete numbers and formulas:
1. **The objective function**, written down explicitly: rewards minus adverse selection, with the
   DF^N reward cost and the measured adverse-selection rate in the same units.
2. **The inventory control law.** Prefer a CONTINUOUS function of (inventory, time-to-close,
   market life) over thresholds — but ONLY if the measured DF cliff permits it. If credit is a
   CLIFF rather than a slope (canon: 12% of snapshots ZEROED at 1 tick), then continuous price
   skew is the wrong instrument and SIZE is the right one — say so and design accordingly.
   State which lever does the work: price skew, size reduction, one-sidedness, or refusal to enter.
3. **How the F1 deadlock becomes structurally impossible** — not "less likely". If a hard
   threshold remains anywhere, show the release path.
4. **The settlement endgame.** Derive urgency from terminal inventory risk rather than picking
   thresholds. Show what it does on BOTH a ~1h temp market and a ~13h gas market — an earlier
   proposal broke because an absolute 30-minute window covered half an hourly market's life.
5. **Prevent F5 at acquisition** rather than lamenting it at settlement.
6. **Scope: per-event vs global.** Where does each control belong?
7. **Honest ceiling: state what this CANNOT fix.** Our tape suggests the money was lost on ENTRY
   timing, not exit — if exit doctrine is a second-order lever, say so plainly and name the
   first-order one.
8. **Migration path from today's config**, with each existing knob either mapped to a new control
   or explicitly retired: MAX_UNWIND_LOSS, INV_SOFT_CT, INV_HARD_CT, THROTTLE_STEP_TICKS,
   HELD_MAX_USD, SETTLE_UNWIND_MIN, TAKER_FLATTEN, REDUCE_ONLY_KEEP_BOTH.

Every parameter needs a DERIVATION or a MEASUREMENT, not a guess. Where you must guess, label it
GUESS and give the experiment that would settle it.

RETURN the doctrine as a spec: objective, control law, parameters with provenance, pseudo-code for
the quote decision, and the migration table.`,
  { label: 'design', phase: 'Design' })

phase('Refute')

const refutations = await parallel([
  'reward-economics', 'deadlock-and-edge-cases', 'small-account-realism', 'does-it-fix-the-actual-losses',
].map(lens => () => agent(`${RULES}

TASK — ADVERSARIALLY REFUTE the proposed doctrine. Lens: **${lens}**.
DEFAULT TO REFUTED IF UNCERTAIN. Find why this is wrong or unimplementable.

THE PROPOSAL:
${String(design).slice(0, 14000)}

Through your lens:
- reward-economics: does it actually pay? Recompute the reward cost with the real CFTC core on
  real books. If the doctrine skews price at all, is that paid for? Watch for the QUALIFYING-SET
  CLIFF — one tick can zero credit entirely, which breaks any smooth-cost reasoning.
- deadlock-and-edge-cases: construct an inventory/time/price state where the new law still jams,
  oscillates, or does nothing. Check the boundaries: zero inventory, inventory exactly at a limit,
  a market with no opposing bid at all, a program that expires while inventory is held, and the
  -low/+high shape.
- small-account-realism: $85 capital, 20 contracts/side, 2-minute cycle, ~2% of a 1000-contract
  Target Size, no queue-position data. Does the doctrine need continuous requoting, sub-second
  reaction, or size we do not have? Does it survive a 2-minute cadence, or does it assume
  reaction speed we cannot deliver?
- does-it-fix-the-actual-losses: replay it mentally against the REAL losers in
  kalshi_live/kalshi_transactions_2026-07-23.csv (20 positions, -$40.62, entries 0.05-0.76 dying
  at 0.00) and against today's 60-minute deadlock. Quantify what it would ACTUALLY have saved.
  If the honest answer is "little, because the loss was already baked in at entry", SAY SO — that
  is the most valuable possible finding and it redirects the whole effort.

RETURN: refuted true/false, severity, the specific defect or failing state, and the measurement
that would settle it.`,
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

TASK — WRITE THE FINAL DOCTRINE. Operator feedback on the previous attempt: **"this is still too
crude"** — a threshold ladder with hand-picked cent values, which also broke on short-lived
markets. They asked specifically to review what large/professional traders actually do. Do not
hand back another ladder unless the evidence genuinely says thresholds beat a continuous law.

RESEARCH:
academic: ${String(research[0]).slice(0, 5000)}
practitioner: ${String(research[1]).slice(0, 5000)}
skew cost: ${String(research[2]).slice(0, 6000)}
our tape: ${String(research[3]).slice(0, 6000)}

DESIGN:
${String(design).slice(0, 12000)}

REFUTATIONS:
${JSON.stringify(refutations.filter(Boolean), null, 1)}

Write \`docs/maker_handoffs/KALSHI_INVENTORY_DOCTRINE_2026-07-23.md\` and return it:

1. **WHAT PROFESSIONALS ACTUALLY DO** — the honest summary, with sources, and which parts transfer
   to a rebate farmer on a 2-minute cycle with $85. Be blunt about what does not transfer.
2. **THE ONE INSIGHT THAT REFRAMES OUR PROBLEM** — if there is one. (Candidate: professionals do
   not place a capped exit order at all; inventory is worked off through skewed two-sided quoting,
   which dissolves the whole notion of an "unwind order that cannot fill". State whether our DF
   cliff permits that, or forbids it.)
3. **THE DOCTRINE** — objective, control law, every parameter with derivation or measurement.
   Pseudo-code for the quote decision.
4. **WHAT IT FIXES**, mapped to F1-F5, with quantified expected benefit from the real tape.
5. **WHAT IT DOES NOT FIX** — lead with this if exit doctrine turns out to be second-order to
   entry discipline. An honest "this is the wrong lever" beats an elegant wrong answer.
6. **MIGRATION** — knob by knob from today's live config, with rollback.
7. **THE EXPERIMENT** that would validate it before any deploy, preferably in the free sandbox.

Report refuters' verdicts even where they contradict the designer. Flag every GUESS as a guess.
Attach sample sizes. If a lane returned nothing, say so.`,
  { label: 'deliver', phase: 'Deliver' })

return { final, refutations: refutations.filter(Boolean) }
