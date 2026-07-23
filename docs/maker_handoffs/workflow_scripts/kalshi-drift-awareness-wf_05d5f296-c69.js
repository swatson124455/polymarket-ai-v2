export const meta = {
  name: 'kalshi-drift-awareness',
  description: 'Detect when the market is running us over, and respond — the quality ceiling behind the -31.95% weather bleed',
  phases: [
    { title: 'Signals', detail: 'what detection methods exist, and which are computable with our data' },
    { title: 'Backtest', detail: 'do they fire on our real losers, and not on our winners' },
    { title: 'Design', detail: 'concrete detect-and-respond rule' },
    { title: 'Refute', detail: 'adversarial review' },
    { title: 'Deliver', detail: 'spec + sandbox experiment' },
  ],
}

const WT = 'C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/02f270fe-27ab-42e6-8906-2ebc25f6df3b/scratchpad/kalshi-wt'

const RULES = `
KALSHI MAKER LANE. Worktree: ${WT} (branch claude/maker-kalshi-live). cd there first — cwd drifts.

=== HARD CONSTRAINTS ===
1. READ-ONLY on the live system. No deploys, no live.env writes, no orders, no systemctl.
   Public API reads fine (no keys, >=0.3s spacing, paginate limit=10000 NOT 1000).
   Authed READ-ONLY on the VPS is allowed via:
     sudo -u polymarket env $(sudo cat /opt/pa2-maker-kalshi-live/live.env | grep -E "^KALSHI_(TRADING_MODE|LIVE_ARMED|API_KEY_ID|RSA_PRIVATE_KEY_PATH)=" | xargs) /opt/pa2-maker-kalshi-live/venv/bin/python <script>
2. CREATE ONLY NEW FILES. Other workflows are editing kalshi_live/*.py — do not edit existing
   modules. New analysis in new files.
3. Kalshi venue only. Never touch MB/WB/EB/SB or shared modules.

=== THE PROBLEM, STATED PRECISELY ===
We are a rewards-farming maker. LIP pays for RESTING QUOTES; fills are a pure COST. Read
docs/maker_handoffs/KALSHI_LIP_RULE_CANON.md (§T terms, R1-R4 rules, §M8/§M13 measurements).

**The bot quotes both sides symmetrically and has NO VIEW ON DIRECTION.** In a market whose price
is trending, that means standing in front of it repeatedly and being run over. Measured, receipts:

    GAS   -1.97% of notional   (13h markets, slow-moving)
    TEMP  -31.95% of notional  (~1h markets, resolve toward a temperature that becomes known)

Same bot, same rules, same size. **The difference is the market, not our exit logic.** On 07-22,
20 positions expired worthless (-$40.62) while all other trading made +$16.40; entries 0.05-0.76,
exits 0.00, held 117-147 min. They were RUN OVER, not badly exited.

Today the same shape appeared in gas: we held short 4.090 and long 4.105, gas settled BETWEEN
them, and BOTH legs expired worthless.

**Current inventory controls trigger on LEVEL, not RATE:** INV_SOFT_CT=15 throttles the
accumulating side one tick inside; INV_HARD_CT=60 pulls it. By the time a level trips, we have
already been run over. There is no signal for "the market is moving through me RIGHT NOW".

=== WHAT WE CAN ACTUALLY OBSERVE (establish this before designing anything) ===
- Book snapshots at a **2-minute** cadence (both sides, all levels) — that is our cycle time.
- Our own fills, with side/price/time/is_taker, via /portfolio/fills (authed).
- Public market data: check what exists — /markets/{t}/orderbook, and whether a public TRADES
  endpoint exists (e.g. /markets/trades) with tape we could use. VERIFY, do not assume.
- Market metadata: close_time, strike, rules.
- Historical: /opt/pa2-maker-kalshi-live/plans-2026072*.jsonl (per-cycle telemetry, READ-ONLY ssh),
  kalshi_live/kalshi_transactions_2026-07-23.csv (244 receipt-grade trades 07-20..22 with entry/exit
  prices and per-lot realised P&L), kalshi_live/concentration_samples.jsonl (FROZEN md5
  e920bf99850279099897a79e8ad78dec, raw books).

**We do NOT have: queue position, order-by-order feed, sub-second data, or size.** $85-100 account,
20 contracts/side, ~2% of a 1000-contract Target Size. Any method needing speed or size we lack is
USELESS HERE — say so rather than recommending it.

=== METHOD ===
* MEASURE BEFORE CLAIMING. If a number looks impossible it IS wrong — this lane has produced a
  $156/period figure on an $85 account and a -$442 settlement on the same account. Find the bug.
* Every detector MUST be tested against a POSITIVE CONTROL and a NEGATIVE CONTROL. Canon records
  eight toxicity metrics built and refuted by their own authors — do not add a ninth.
* State sample size and what is NOT covered on every number.
* A detector that fires on everything is worthless. Report FALSE POSITIVE rate on the profitable
  gas trades, not just hit rate on the losers.
`

phase('Signals')

const signals = await parallel([
  () => agent(`${RULES}

TASK — WHAT DOES THE FIELD ACTUALLY USE TO DETECT BEING RUN OVER? Research, with real definitions.

Get the actual formulations, not vibes:
1. **Order Flow Imbalance (OFI)** and **queue/depth imbalance** — exact definitions, what data they
   need, what they predict, over what horizon.
2. **Microprice / weighted mid** (Stoikov). The claim is that size-weighted mid predicts the next
   move better than the mid. **Crucially: is it computable from a plain L2 book snapshot?** If yes
   this is directly available to us every cycle.
3. **VPIN** (Easley/Lopez de Prado/O'Hara) — volume-synchronised probability of informed trading.
   What does it need (trade tape? bulk classification?) and does a 2-minute cadence destroy it?
4. **Markout / post-fill drift** as the standard adverse-selection MEASUREMENT (not a live signal):
   the price N seconds/minutes after our fill vs the fill price. What horizons are standard?
5. **Trade-through / sweep detection** — spotting a taker eating multiple levels, which is the
   signature of informed flow.
6. **Glosten-Milgrom**: when flow is informed, is WIDENING the correct response, or must you stop
   quoting that side entirely? This is the core design question for us.

For EACH: state the data requirement, the horizon it works over, and a blunt verdict on whether it
survives a **2-minute snapshot cadence with no queue position and no tape** (verify whether Kalshi
exposes a public trades endpoint — that changes the answer for several of these).

RETURN: definitions with sources, data requirements, and a computable/NOT-computable verdict for
each under our constraints.`,
    { label: 'signals:methods', phase: 'Signals' }),

  () => agent(`${RULES}

TASK — INVENTORY THE SIGNALS WE ACTUALLY HAVE. Be exhaustive and concrete; this bounds everything.

DO:
1. **Probe the public API surface** for anything trade- or history-shaped: a public trades/tape
   endpoint, candlesticks/OHLC, market stats (volume, open interest), any history endpoint.
   Try plausible paths under /trade-api/v2 and REPORT STATUS CODES. This is decisive — a public
   tape unlocks OFI/VPIN-style methods; without it we are limited to book snapshots and our fills.
2. **Characterise our own fills as a signal.** From /portfolio/fills (authed READ-ONLY) plus the
   CSV: when we get run over, do fills arrive CLUSTERED on ONE SIDE in quick succession? Measure
   the actual distribution: time between consecutive same-side fills, run lengths, and how they
   differ between the profitable gas trades and the -$40.62 weather losers.
   **This is the zero-cost signal we already have and currently ignore** — the bot throttles on
   inventory LEVEL, never on fill RATE or fill ONE-SIDEDNESS.
3. **Book-derived signals at 2-minute resolution.** From plans files and any stored books: how much
   does the best bid/ask move between consecutive cycles, per series? Is 2 minutes fast enough to
   see a trend before it takes us, or is the move complete within one cycle? **Quantify per series
   — this determines whether ANY book-based drift detection can work for weather.**
4. **External/underlying signals.** Weather markets resolve to a measured temperature; gas to a
   published price. Is there a cheap public source that would tell us the outcome is becoming
   determined? Note we have a whole sibling weather project — but it is OUT OF SCOPE and must not
   be touched or referenced; just note whether an independent public source exists.

RETURN: the probe results with status codes, the fill-clustering statistics with sample sizes, the
per-series inter-cycle price movement, and a ranked list of AVAILABLE signals by (cost, latency,
discriminating power).`,
    { label: 'signals:available', phase: 'Signals' }),
])

log(`signals: ${signals.filter(Boolean).length}/2 lanes`)

phase('Backtest')

const backtest = await agent(`${RULES}

TASK — BUILD THE DETECTORS AND TEST THEM ON OUR REAL TAPE. This is the phase that decides whether
any of this is real.

METHODS AVAILABLE:
${String(signals[0]).slice(0, 8000)}

SIGNALS WE HAVE:
${String(signals[1]).slice(0, 8000)}

THE TEST SET is our own history — no simulation:
- **POSITIVES (must fire):** the 20 positions that expired worthless on 07-22, -$40.62. In
  kalshi_live/kalshi_transactions_2026-07-23.csv, rows with exit_price_dollars == 0.0 on
  2026-07-22. Plus today's GASD-26JUL23 pair where both legs died (held +28.00 on -4.105 which
  resolved NO, and -21.89 on -4.090 which resolved YES).
- **NEGATIVES (must NOT fire):** the profitable gas trades. Same CSV, KXAAAGAS* rows, which
  measured +0.25 trading P&L over 07-21..22 and -1.97% of notional lifetime. Any detector that
  also fires on these is useless — it would just stop us trading the thing that works.

DO:
1. Implement each computable detector from the signals phase in a NEW file
   \`kalshi_live/kalshi_drift_detect.py\`.
2. For each: report **hit rate on positives** AND **false-positive rate on negatives**, with a
   confusion matrix and sample sizes. **A detector without a false-positive number is not reported.**
3. **Lead time is the whole point.** For each true positive, how far in advance of the loss does it
   fire? A detector that fires as the position dies is worthless. Report the lead-time distribution.
   Note our cycle is 2 minutes — a signal with <2 min lead is unusable.
4. Test the ONE-SIDED FILL CLUSTERING detector explicitly, even if the literature does not name it
   — it uses data we already have, at zero latency, and it is the most likely thing to work here.
5. **Be adversarial with yourself.** State the base rate. If 40% of all positions lose money, a
   detector firing on 40% at random looks like a 40% hit rate. Compare against that null.
6. If NOTHING separates positives from negatives, SAY SO. That is a real and valuable result — it
   would mean the losses are not predictable from our data and the answer is market selection or
   position sizing, not detection.

RETURN: per-detector confusion matrix, lead-time distribution, comparison against the base-rate
null, sample sizes, and an honest verdict on whether any detector is usable.`,
  { label: 'backtest', phase: 'Backtest' })

phase('Design')

const design = await agent(`${RULES}

TASK — DESIGN THE DRIFT RESPONSE. Only for detectors that survived the backtest.

BACKTEST RESULTS:
${String(backtest).slice(0, 12000)}

If nothing survived, say so plainly and design the fallback instead (market selection / sizing /
not entering) — do not invent a detector the data does not support.

Otherwise design, with concrete numbers:
1. **The detector**: exact computation, inputs, threshold, and how the threshold was chosen
   (derived or measured — not picked).
2. **The response.** This is the important half, and it interacts with how we earn:
   - Pull the side being run over entirely? (forfeits that side's LIP score — R4: the two sides
     score independently and additively, so this costs HALF our score in that market, not all)
   - Shrink that side? (score is LINEAR in size, so half the size = half that side's score)
   - Step it back a tick? (**exponentially** expensive: DF=0.50, so one tick = -50%, and canon
     records the 1-tick step ZEROING credit in 12% of snapshots by falling out of the qualifying
     set — a cliff, not a slope)
   Rank these by reward-cost per unit of protection, using the real DF, and pick.
3. **Re-entry**: when do we resume quoting the pulled side? A rule that never re-enters is just a
   slower way of not trading.
4. **Per-market or per-event scope?** (§T: an event is ONE correlated risk; a market/contract is
   one book.)
5. **Interaction with what already exists**: INV_SOFT_CT / INV_HARD_CT throttling, the naked-risk
   breaker (now effectively disabled at 100), REDUCE_ONLY_KEEP_BOTH, TAKER_FLATTEN (now on),
   the late-life entry gate. Does drift response duplicate, conflict with, or subsume any of them?
6. **Expected benefit, quantified against the real losers** — how much of the -$40.62 does this
   actually recover? If the honest answer is "a third", say a third.

Every parameter needs a derivation or a measurement. Label any GUESS as a guess and give the
experiment that settles it.

RETURN: detector spec, response rule, re-entry rule, scope, interaction table, and quantified
expected benefit.`,
  { label: 'design', phase: 'Design' })

phase('Refute')

const refutations = await parallel([
  'detector-is-noise', 'response-costs-more-than-it-saves', 'cadence-and-realism', 'wrong-lever',
].map(lens => () => agent(`${RULES}

TASK — ADVERSARIALLY REFUTE the drift proposal. Lens: **${lens}**.
DEFAULT TO REFUTED IF UNCERTAIN.

THE PROPOSAL:
${String(design).slice(0, 12000)}
BACKTEST IT RESTS ON:
${String(backtest).slice(0, 8000)}

Through your lens:
- detector-is-noise: is the separation real or overfitted? n=20 positives is small. Check the
  base rate. Would the detector fire on a random subset just as often? Is it using information
  that would NOT have been available at decision time (lookahead)? **Lookahead is the most likely
  fatal flaw — check it specifically.**
- response-costs-more-than-it-saves: price the response in LIP terms with the real DF=0.50 and the
  qualifying-set cliff. Pulling a side costs half that market's score EVERY cycle it stays pulled.
  If the detector has false positives, we pay that cost on markets that were fine. Compute the
  break-even false-positive rate — above it, the cure is worse.
- cadence-and-realism: 2-minute cycle, no queue position, no tape (unless the probe found one),
  ~2% of Target Size. Does the signal survive 2-minute sampling? By the time we act, is the move
  already over? Check the measured inter-cycle price movement for weather specifically.
- wrong-lever: even if it works, is drift detection the right answer? The same data says weather
  loses 16x more than gas REGARDLESS of timing. Would simply not quoting weather, or quoting it
  smaller, dominate this whole design? Weather is also 91% of our reward income — so quantify
  BOTH sides of that trade rather than asserting either.

RETURN: refuted true/false, severity, the specific defect, and the measurement that settles it.`,
    { label: `refute:${lens}`, phase: 'Refute', schema: {
      type: 'object',
      required: ['lens','refuted','severity','defect','settling_measurement'],
      properties: {
        lens: { type: 'string' },
        refuted: { type: 'boolean' },
        severity: { type: 'string', enum: ['BLOCKER','HIGH','MEDIUM','LOW','NONE'] },
        defect: { type: 'string' },
        settling_measurement: { type: 'string' },
      },
    } })))

phase('Deliver')

const final = await agent(`${RULES}

TASK — WRITE THE VERDICT. Operator asked for a deep dive on drift awareness because it is the
suspected quality ceiling behind the weather bleed.

SIGNALS/METHODS: ${String(signals[0]).slice(0, 4000)}
SIGNALS AVAILABLE: ${String(signals[1]).slice(0, 5000)}
BACKTEST: ${String(backtest).slice(0, 8000)}
DESIGN: ${String(design).slice(0, 8000)}
REFUTATIONS: ${JSON.stringify(refutations.filter(Boolean), null, 1)}

Write \`docs/maker_handoffs/KALSHI_DRIFT_AWARENESS_2026-07-23.md\` and return it:

1. **CAN WE EVEN SEE IT COMING?** Lead with the honest answer. With 2-minute snapshots, no queue
   position, and whatever tape the probe found — is being run over detectable in advance at all?
   If no, say no in the first line. That verdict is worth more than a clever design.
2. **THE DETECTOR** that survived, with its confusion matrix, lead time, and false-positive rate
   against the profitable gas trades. If none survived, this section says so.
3. **THE RESPONSE**, priced in LIP terms (DF=0.50, the qualifying-set cliff), including the
   break-even false-positive rate.
4. **QUANTIFIED BENEFIT** against the real -$40.62. Be specific and do not round up.
5. **WHAT THIS DOES NOT FIX**, and whether a simpler lever (market selection, size, entry timing)
   dominates. Weather is 91% of reward income AND ~all the losses — state both sides.
6. **THE SANDBOX EXPERIMENT** that would validate before any deploy: what to log, for how long,
   and the pre-registered success criterion. Pre-register it — do not leave it to be chosen after
   seeing results.
7. **INTERACTION** with the config as it now stands: SERIES_ALLOW has 9 series (5 temp, 2 gas,
   KXB200MON, KXAMSAVO), MAX_TOTAL_CAPITAL=100, HELD_MAX_USD=100 (both above account value, so
   both effectively inert), PER_SERIES_CAP=30, FOOTPRINT_TOP=40, JOIN_SIZE=20, MAX_MARKET_CAPITAL=15,
   MAX_UNWIND_LOSS=0.02, TAKER_FLATTEN=1, REDUCE_ONLY_KEEP_BOTH=1, DAILY_LOSS_HALT_USD=40.
   Note that the naked-risk brake is currently unreachable, so drift response may be the only
   thing standing between an adverse run and the daily-loss halt.

Report refuters' verdicts even where they contradict the designer. Flag every GUESS. Attach sample
sizes. If a lane returned nothing, say so.`,
  { label: 'deliver', phase: 'Deliver' })

return { final, refutations: refutations.filter(Boolean) }
