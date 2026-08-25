# D3 DESIGN — RE-PAIR AFTER CHEAP FILL + DOLLAR-WEIGHTED EVENT RISK (2026-08-25)

Operator ruling 2026-08-25: design direction approved ("yes to d3"); this is the combined
design for ONE implementation signoff. No code exists yet. Implementation sequenced after
(i) the F2 feed-lag discriminator closes (T5.82 frozen-meter check, passive) and (ii) a
few days of armed-gate observation (D1/D4 deployed 16:34Z).

## The measured problem (all from 2026-08-25 live tape)
One 40ct NO fill at $0.02 on T5.42 (15:17:07Z, max further loss $0.80) produced BOTH:
1. T5.42 itself went exit-only (HOLDING => EXIT ONLY, :3335-area) — loses its
   accumulating-side share in a qualifying book for the whole holding period; and
2. the DIESELW event delta (−40 CONTRACTS) crossed INV_SOFT_CT=15 and throttled the
   SIBLING earners (T5.82 explicitly `gate_event_directional` in the 16:1x tape) —
   an $0.80-bounded position muting markets accruing real cents/hour.
Under the official rules (R3 doc): a user's score sums their qualifying bids on BOTH
sides, so exit-only still earns the exit side's share in a qualifying book; the missing
accumulating side is roughly HALF the per-market accrual, and sibling muting is pure loss.

## Part A — RE-PAIR AFTER CHEAP FILL
- **Trigger** (all must hold, evaluated per cycle in the HOLDING branch):
  1. avg basis of held inventory ≤ `KALSHI_REPAIR_BASIS_MAX_D` (default **0.02**/ct);
  2. the market passes EVERY entry gate that cycle (armed qualifiable, capture ≥ $2 floor,
     band, runway-or-exemption, mid-band, storm when armed — no bypasses);
  3. size = `min(join_after_ramp, INV_HARD_CT − |inv|)` ≥ 1; caps/halt/write-budget
     unchanged; one-price-per-ticker+side respected (top-up rule as in pair-unwind).
- **Action**: emit the CONSUMED side (accumulating, at reference) alongside the untouched
  `_reducing_quotes` exit; exit keeps write-budget priority.
- **Bound**: each re-fill adds ≤ basis×count ≤ $0.02×40 = **$0.80**/round; INV_HARD_CT is
  the absolute envelope; governors/breaker still strip accumulating quotes when tripped.
- **07-27 rule preserved verbatim** for basis > threshold (the NDQHUD 0.30-0.40 class
  stays exit-only). Knob `KALSHI_REPAIR_CHEAP_FILL` default **0** (byte-identical off).
- **EV**: adds the accumulating side's share in qualifying books only (gates refuse the
  rest). At today's measured diesel rates (F1/F8) that is roughly a doubling of the
  per-market accrual while holding — INFERRED, receipt-checked after 08-30.

## Part B — DOLLAR-WEIGHTED EVENT DELTA (R6 risk unit, first cut)
- **Today**: `event_delta` = signed CONTRACTS across the event; thresholds INV_SOFT_CT=15 /
  INV_HARD_CT=50 make 40ct@$0.02 indistinguishable from 40ct@$0.35.
- **Design**: compute `event_delta_usd` = Σ over event tickers of signed inv_ct × basis/ct
  (the bounded additional loss of the held side), and drive the EVENT throttle from dollar
  thresholds `KALSHI_EVENT_SOFT_USD` / `KALSHI_EVENT_HARD_USD`. Proposed defaults **$5.25 /
  $17.50** (= the current contract thresholds × the 07-27 incident's ~$0.35 basis, so the
  mid-band class that motivated the throttle maps onto today's protection exactly, while
  $0.80 of 2c inventory no longer mutes siblings).
- **Scope**: ONLY the event-aggregate throttle changes unit. The per-ticker HOLDING =>
  EXIT-ONLY rule and the per-ticker INV_HARD_CT envelope are untouched.
- Knob `KALSHI_EVENT_DELTA_DOLLARS` default **0** (byte-identical off).

## Test plan (pins shipped with the code)
A: trigger-on/off at the basis boundary; every entry gate individually refuses re-pair;
size clamp at INV_HARD−|inv|; governor strip still wins; knob-off byte-identical.
B: $0.80 event → siblings unthrottled; $14 event → throttled (parity with today at the
0.35 calibration); HARD_USD zeroes the accumulating side; knob-off byte-identical.
Plus chaos-cycle runs with both armed.

## Signoff asks (decisions only)
1. Approve Part A as specced (default basis 0.02, knob off until armed)?
2. Approve Part B as specced (defaults $5.25/$17.50, knob off until armed)?
3. Arm-together or A-first? (Recommend A+B together: A without B re-creates the sibling
   muting the moment a re-paired side fills again; B without A leaves the missing-side
   accrual on the table.)
