# KALSHI REWARD GAME AUDIT — 2026-08-06 (operator-ordered, findings-first)

Operator scope rulings (2026-08-06): (1a) findings-first, ONE decision batch, no code
changes; (2c) both shark axes, framed as COPY THE MAKERS / AVOID THE PICKERS; (3c) staged
objective — credits-max with near-zero fill exposure until the 2026-08-10T14:13Z verdict,
net-max proposals allowed for after; (4) fresh surgical pull of the venue's reward rules;
(5) whale referent = POLYMARKET_PRO_TRADER_STUDY_2026-08-01.md, fresh shark census
authorized. Everything below is labeled ESTABLISHED / INFERRED / HYPOTHESIS per RULE SIX.

## 1. THE RULES — fresh pull (help.kalshi.com article 13823851, UPDATED 2026-08-05;
article 16076644 updated 2026-07-26; read 2026-08-06 ~15:20-15:35Z, logged-in session)

ALL ESTABLISHED (venue-published):
- **Snapshots once per second at a random moment within each second.** Scoring is
  share-of-snapshot-score integrated over the program period.
- **Reference Price per snapshot per side**: walking down from the best bid, the first
  level where CUMULATIVE resting size reaches Target/5. A small lone top-of-book order
  does not set it.
- **Qualifying**: only orders that help reach Target Size on their side. If a side never
  reaches Target Size, NO orders on that side qualify for that snapshot.
- **Two-sided exclusion**: a snapshot is EXCLUDED for everyone unless BOTH sides meet
  Target Size; the period payout scales by (non-excluded / total snapshots). The venue
  keeps the rest.
- **Distance penalty**: at/better than Reference = 1.0×; else DiscountFactor^ticks.
- **Score**: size × distance multiplier, NORMALIZED per side (share of qualifying
  liquidity). A snapshot is worth ≤ 2.0 across all participants (1.0/side).
- **Payout** = your share of total snapshot scores × period reward × (valid/total
  snapshots), rounded down to cents. **Final reward below $1 for an individual PROGRAM
  (= per market) is not paid.** Periods up to 31 days, may overlap. $1–$1,000/mkt/day.
- **Payment timing**: "Final scoring occurs after a program ends, and payment follows in
  a later processing run." (Confirms this session's 0b measurement: 9/57 credits landed
  BEFORE market close, 0 ever later than close+48h — trigger is program conclusion.)
- **The web estimate is a live PROJECTION** — "can move up or down as the order book,
  other participants, and the remaining program time change. Only a paid credit is
  final." (Settles 0a's open monotonicity question; matches the observed −207cc dip at
  ~06:31Z in the recorder.)
- Per-order blue/gray qualification dot with an "estimated efficiency %" exists in the
  web book UI. Kalshi monitors for "abusive behavior and market manipulation";
  governing terms live at kalshi.com/regulatory/notices.

Measured parameters (API read 2026-08-06T15:19-15:25Z, 3,802 active liquidity programs):
**target_size 1000.00 on 3,755 programs, 300.00 on 46 (incl. all 7 KXTRUMPENDORSEMENTS),
500 on 1; discount_factor_bps 5000 (=0.5/tick) on 3,799 of 3,802.**

Canon reconciliation: R1 pool formula CONFIRMED ($1–1,000/mkt/day documented). The $1
floor is documented **per-market-program** — this REFINES the per-EVENT floor framing
used earlier on 08-06 (credits merely ARRIVE aggregated per event, canon §M11). W10's
"$1 floor terminal mechanism" stands, at per-market granularity.

## 2. THE SHARK MAP — venue competition ratings + orderbook census

- **The venue publishes a per-event "Competition" rating (low/medium/high)** on
  kalshi.com/incentives (server-rendered; 380 event rows parsed 2026-08-06 ~15:0xZ; no
  public API endpoint found — 5 candidate paths probed with the bot key, all 404).
- **Every one of our 5 active allowlist series is rated HIGH competition**
  (KXAAAGASD, KXTOPMODEL, KXTRUMPENDORSEMENTS, KXCHIPBURRITO, KXADJOURNRECESS). ESTABLISHED.
- **52 series are rated LOW**, led by (page event-window totals, not daily):
  KXFEDFUNDSYEAR $5,250 · KXTRUMPMENTION $4,800 · KXUSCPIYEAR $3,250 ·
  KXTRUMPSAYCOMPANY $2,900 · KXDIESELW $2,520 · KXEURUSDAW $2,400 · KXPBR $2,300 ·
  KXRAIN $2,000 · KXFSLR $1,800 · KXEARNINGSMENTION{LYFT,DKNG,NBIS} $1,700-1,800 each ·
  KXWTIMAX/WTIMIN $1,520-1,600 · KXCHINAAI $1,215 · KXHOODA $1,100 · KXGOVTFULLFUND $1,015 …
  (full list in session transcript; API daily pools for the same series:
  RAIN $4,000/day·40mkts, TRUMPMENTION $4,800/day·48mkts, FEDFUNDSYEAR $5,250/day·210mkts,
  HOODA $3,200/day·32mkts, PBR $2,300/day·23mkts — read 15:25Z).
- **Orderbook census** (83 markets, w0c_shark_census.json, 15:18:41Z): our series have
  the DEEPEST books in the sample — KXTOPMODEL avg top-3 depth ~39,084 units (max block
  106,449), KXTRUMPENDORSEMENTS ~27,098 — while LOW-rated series sit near-empty:
  KXTRUMPMENTION avg 351 units / 3.7c spread, KXRAIN 2,115, KXEARNINGSMENTIONDKNG 2,378.
  (Units = orderbook_fp *_dollars level values; unit semantics not independently
  verified — rankings are unit-invariant.)
- **The label ≠ raw depth**: KXTRUMPSAYCOMPANY is rated LOW yet carries a 350,128-unit
  max block — INFERRED: "competition" counts distinct participants, and that book is one
  monopolist farmer. LOW+thin book = genuinely free pool; LOW+deep = monopolist;
  HIGH = our current tanks.
- **Copy-the-makers observation** (ESTABLISHED from raw books): the large farmers rest
  DEEP CHEAP LADDERS (e.g. TOPMODEL-26AUG10-CLAUM YES: 6,630@1c / 10,080@2c / 7,840@3c;
  PBR NO: 2,030@1c / 1,100@10c / 5,000@11c) — size-at-distance meeting Target Size with
  minimal capital and near-zero pickoff (a 1c fill costs 1c) — NOT at-touch queue
  presence. We do the exact opposite: 5–50ct at the touch = maximum pickoff surface,
  minimal share. The 08-01 pro-trader study's H2 (the biggest actor is a low-margin
  volume machine, not a directional genius) and H1 (public flow pictures hide makers)
  rhyme perfectly: the winning shape is structural, boring, and invisible to
  taker-centric analysis.

## 3. GAME THEORY — what the formula actually rewards

1. **Per-side normalization makes it a SHARE game, not a size game.** In a book with
   106k units qualifying, our 50ct is noise. In an empty book, 1,000ct of ours is 100%.
2. **The two-sided exclusion creates a cooperative/competitive split**: adding depth to
   a side that already meets Target takes share FROM incumbents (competitive); completing
   a side that does NOT meet Target unlocks payment for EVERYONE (cooperative — the
   incumbent has no incentive to punish). → **"Symbiote play"**: find books where exactly
   one side meets Target; complete the thin side; capture ~1.0 share of that side.
   Detectable from a single book read. HYPOTHESIS (formula-derived, untested).
3. **The Reference Price is depth-derived, not fair-value-derived.** In an empty book,
   whoever rests Target/5 of cumulative size SETS the reference — the formula as written
   pays a solo two-sided 1,000ct ladder at ANY price levels 100% of both sides.
   Capital cost at cheap levels is trivial (1,000ct at 1–3c ≈ $10–30/side reserved).
   ⚠ Abuse-monitoring clause applies; observed live books show this exact shape
   resting today (§2), so it is at minimum tolerated practice. OPERATOR JUDGMENT ITEM.
4. **Distance is the pickoff shield the objective wants**: DF=0.5 costs 50%/tick of
   credit, but in quiet books fill probability falls faster than that with distance
   (HYPOTHESIS — measurable from our own fills-vs-distance history). At-touch-only
   placement (what we do) maximizes the picker surface the operator ordered avoided.
5. **Probes are formula-invisible at 5ct**: target 1000/side means a 5ct probe in an
   empty book can NEVER make a side qualify → no accrual → no receipt → the
   receipts-first graduation rule can never admit exactly the empty LOW-competition
   books the thesis targets. **The pilot is structurally locked into HIGH-competition
   pools.** ESTABLISHED (formula) + measured (probe slots live on DIESELW/EURUSDAW/
   RAIN/TOPMOVIE/YTVIEWSW for days; window credits $0.00).

## 4. SELF-AUDIT OF THE FUNNEL (subagent, full read of quoter/rank/feedback; 20 findings)

Structural leaks (full detail + file:line in the agent record):
- **F5 BLOCKER — own-size double-count**: live book reads INCLUDE our resting orders;
  measured share computes score/(book+score) → an empty book we occupy alone reads ~0.5
  share instead of ~1.0. The rank systematically penalizes exactly the empty books the
  thesis targets, fights incumbency, and the same understated capture feeds the armed
  net-EV fallback — which can cancel our BEST books. ESTABLISHED.
- **F1/F3 MAJOR — floor gate at wrong unit and wrong size**: `_expected_credit_usd`
  tests $1 against min(1 day, remaining window) instead of the full program period
  (doc: periods up to 31 days, paid at period end), and models FULL join size while the
  D3 ramp then rests 5–10ct — admitting markets whose clamped size cannot clear the
  floor. (Fresh-pull correction to the agent's brief: per-MARKET granularity is RIGHT
  per the 08-05 doc; the per-DAY unit is the defect.)
- **F2 MAJOR — allocation maximizes market count under a concave floor**: one-per-series
  round-robin + greedy per-dollar walk spread capital until every program accrues below
  $1 and pays zero. Estimates feed 14:12Z: $6.66 accrued across 25 events, only
  APRPOTUS-39.9 ($1.63) clears per-market floors. Concentrate-then-extend is the optimal
  shape; the code does the opposite. ESTABLISHED (shape) / INFERRED (dollar impact).
- **F4 MAJOR — never-pays deadlock**: size-trust requires a credit receipt, but the
  10ct clamp is what prevents the credit. Self-fulfilling never_paid_due convictions.
- **F7/8/9 — one-sided presence leaks** (create-fail ratchet freezes half-books up to
  1h; event-delta throttle rests one-sided by design; band check drops one side).
- **F13 MAJOR — placement is binary at-touch**: nothing weighs DF-cost vs
  fill-probability; the maker shape we should copy (ladder at distance) has no code path.
- **F14 MAJOR — one-cycle absence resets the D3 ramp to 5ct** (sawtooth churn on payers).
- **F17 MAJOR (latent) — 45-min absolute wind-down forfeits ~78% of any sub-hour
  program window** — guts the hourly temp lane the day it returns.
- **F15 — "queue position is what rewards pay for" is wrong in comments/design**: LIP
  pays size×time at price; queue priority only raises FILL probability (the cost side).
- Minor: F6 estimates feed unconsumed; F10 exit-only earning contradiction; F11 unwind
  evicts earners from budget; F16 amend-decrease OFF; F18 truncation-alarm limit
  hardcode; F19 series-grain verdicts; F20 binary "paid" trust.
Right-and-keep list (do not regress): pool math canon-correct; target/df consumed in
gates; share-based dilution-aware rank; unqualifiable-book skip; presence-gate
entry/continuation split; de-risk never gated; churn-suppression stack; score-cache
hygiene; probe budget/rotation discipline; FIX-H receipts-first; feedback alarms.

## 5. WINDOW SCOREBOARD (context for urgency; all fresh reads this session)

Restart window 2026-08-05T14:13:28Z → 08-06T14:12Z: position-aware drag −$9.14
(recorder), credits $0.00 (credit_history 14:14:00Z), venue estimate accrued-unpaid
$7.0682 (14:14:00Z; only ~$1.63 of it above a per-market floor at that read).
daily_dd $7.18 vs $10 halt at 14:13:03Z. Today's drag NOT yet decomposed (RULE SEVEN:
no attribution until decomposed).

## 6. DECISION BATCH (findings-first; nothing built; RULE NINE: nothing demoted)

- **D-A. Estimates-feed closed loop** — consume /v1/incentives/users/{uid}/estimates in
  the floor/netev gates in place of the 2–6x-off model, and as the measurement channel
  for every experiment below (hourly venue truth). Build dark, full NORM. DEFAULT: yes.
- **D-B. Fix the BLOCKER + floor unit + clamped-size gate (F5, F1, F3, F14)** — four
  surgical, testable fixes. Build dark, full NORM, deploy on naming. DEFAULT: yes.
- **D-C. Formula-valid probe ("macro-probe")** — replace/augment the 5ct probe with a
  two-sided Target-meeting cheap ladder (~$20–60 reserved/market) in 1–3 LOW-competition
  thin-book markets; success = estimates feed row appears/rises within hours; receipts
  follow at program end. This is the ONLY probe shape that can ever earn a receipt in an
  empty book. Candidates (LOW + thin, program inside horizon): KXTRUMPMENTION,
  KXEARNINGSMENTIONDKNG, KXRAIN; (LOW + far-close FIX-H shape, program ends 08-09/11:
  KXPBR, KXFEDFUNDSYEAR, KXUSCPIYEAR — inventory locks to 2027 close if filled, though
  cheap-ladder fills are near-free lottery tickets). Needs INV/size-cap carve-out for
  far-from-touch resting + allowlist additions (ADDITIVE). DEFAULT: yes, 2 markets,
  near-horizon candidates first; operator picks whether FIX-H-shaped ones join.
- **D-D. Symbiote detector** — standing scan for one-side-meets-target books; complete
  the thin side. DEFAULT: design + measure first (count how many such books exist now).
- **D-E. Ladder-distance placement policy** — shift size behind the reference with
  DF-weighting (copy the makers; shrink the picker surface). Bigger design change.
  DEFAULT: study on our own fills-vs-distance history first, no live change this window.
- **D-F. Abuse-risk stance** — deep cheap ladders are formula-legal and observed
  practice, but the venue "monitors for abusive behavior". Operator sets the line.
  DEFAULT: mirror observed practice (ladders at prices with real if small fill value,
  never 1c-only walls), stay modest-sized.
- **D-G. Wind-down proportionality (F17)** — fix before any hourly-program lane returns.
  DEFAULT: build dark with D-B batch.

Artifacts: w0c_shark_census.json (VPS) · estimates recorder running 5-min ·
agent record in session transcript · this doc.
