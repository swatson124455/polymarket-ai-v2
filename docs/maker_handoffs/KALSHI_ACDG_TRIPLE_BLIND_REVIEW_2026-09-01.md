# ACDG SELECTION DESIGN — TRIPLE-BLIND ADVERSARIAL REVIEW + VERIFICATION (2026-09-01 ~17:0xZ)

Operator order: "full adversarial triple blind review of new algo selection build find any
weakness bugs or areas to elevate then report only." Scope = the ACDG design (allocator C +
supply D + census G + go-live A) as specced in `KALSHI_SELECTION_REVIEW_AND_ALLOCATOR_
2026-09-01.md` §4 + the chat-ratified ACDG combo. REPORT ONLY — nothing fixed, nothing
built, bot OFF throughout.

**Method.** Three independent reviewers (economics/EV, venue-rules, systems/ops), identical
spec + facts pack, mutually blind, code access read-only. 46 raw findings (12+17+17).
Every load-bearing claim then verified by the consolidating session against live.env (box
reads ~15:5x–16:4xZ), the quoter blob (= deployed md5 `a039f749`), and fresh measurements.
Verdicts: VERIFIED-MEASURED (new measurement run), VERIFIED-CODE (line/env read),
CONFIRMED-LOGIC (spec contradiction, no code needed), PLAUSIBLE (unverified, test named),
CORRECTED (claim right, number wrong), KILLED (refuted by live config/data).
Raw reviewer outputs are session-local (task transcripts); this doc is the consolidated
record.

---

## A. NEW MEASUREMENTS PRODUCED BY THIS REVIEW (stand on their own)

- **A1. est-feed "accrued" is NOT banked dollars.** Scan of the full estimates tape
  (08-06→09-01: 7,617 feed rows, 143 programs): **25 per-program DECREASES**, incl.
  2424→2127cc (−12%) and 1923→1767cc (−8%) within 5 minutes. Confirms the filing's
  ratio semantics: payout = own Σ ÷ ALL users' Σ at period end — future rival snapshots
  dilute already-shown "accrued" retroactively. [R2-F1 → VERIFIED-MEASURED]
- **A2. Family cap is live and binding: ~$78.64.** live.env `KALSHI_SERIES_MAX_USD=100`;
  `SERIES_PCT` unset → default 0.25 (quoter:4052) → `_series_cap()` = min(100, 0.25×equity)
  ≈ $78.64 at $314.57 (:4069-4079); `cap_desired` skips any sibling pushing the family past
  it (:4209-4213). **Binds the ALREADY-RULED footprints**: money-plan Opt-1a (CLAU5+T5.60+
  T5.58, diesels $93+$98=$191/family) and Opt-1b (3 diesels ≈$273/family) are un-fundable
  as projected — roughly TWO diesels at ~40ct or ONE at ~100ct fit under $78.64. Note the
  08-31→09-01 night is consistent: T5.60/T5.58 both-resting at 33-40ct ≈ $73 family ≤ cap,
  and T5.62's 0.164 presence (vs 0.40/0.40) may be partly family-cap crowding, not only the
  widebook mechanics the money plan named — undecomposed. [R3-F5 → VERIFIED-CODE+ENV; NEW]
- **A3. Rich-side placement is 56ct, not 100ct.** `_capped_join` per-side $ cap =
  MAX_MARKET_CAPITAL/2 = $50 (:2595-2598) → at 0.89 touch: min(JOIN_SIZE=100, int(50/0.89))
  = **56ct**; cheap side 100ct. Actual committed ≈ $59/market vs the money plan §2's
  "100ct/side ≈ $93-98". Every 100ct-linear projection (incl. the ~$13 presence-fixed 09-06
  scenario) carries ~20-25%+ built-in optimism on the rich side, and the allocator's capital
  model would over-reserve ~$35-39/market. [R1-F5, R3-F6, R2-F17 → VERIFIED-CODE]

**A2+A3 apply to Option A (GO with applied config) as well — independent of ACDG.**
Reported, not acted on; the ruled values stand until you say otherwise (Rule Nine).

---

## B. CONFIRMED CRITICAL FINDINGS (design must answer these before any build sheet)

- **B1. Cliff projection premise is wrong: "accrued" dilutes.** `projected = accrued +
  rate×time-left` treats a ratio-estimate as a floor (A1). A market can clear $1.50
  projected and finish sub-$1.00 → $0, purely from rival depth arriving later — which
  SUPPLY-mode's visible qualification-flip actively invites. The $1.50 margin was sized
  against model noise, not against unbounded denominator growth. [R2-F1; R1-F2 partially]
- **B2. SUPPLY mode as specced cannot place one order — five independent verified vetoes.**
  (i) QUALIFIABLE_GATE addable math divides MAX_ACTIVATE_CAPITAL ($60 env) by the RICH
  price: `_addable = 60/max(best_y,best_n) ≈ 67ct` (:3286-3289) — a ~950ct cheap gap can
  never qualify; raising INV_HARD_CT (the §8 fix) touches the non-binding term.
  (ii) The activate branch floors BOTH sides at JOIN_SIZE=100 (:3509-3510) → cap ≈ $136-180
  > MAX_ACTIVATE=$60 → `gate_activate_cost` refusal (:3511-3515).
  (iii) It rests at the CURRENT touch (:3521-3524) — the allocator's chosen 2-9c park price
  is inexpressible.
  (iv) D3 ramp clamps every reason except unwind/macro_probe (:700) → even a passed
  supply order rests 5→…→100ct max, never Target → excluded snapshots, pure fill risk
  (the measured S4/anchor class re-created).
  (v) Counts clamp to INV_HARD_CT (:3519).
  All three reviewers found this independently. "Clamps ride existing size paths" is
  false: clamps only shrink; SUPPLY needs a NEW quote-construction path + scoped-knob
  design — live-order-path code, where the defect history lives. [R1-F3ii, R2-F5, R3-F1/F2/F3
  → VERIFIED-CODE]
- **B3. The evidence rule dead-ends SUPPLY and locks the daily class at probe tier
  forever.** "Only measurement earns size": a sub-Target book measures $0/day for everyone
  by the filing rule; linear-in-size from 0 is 0; a probe cannot bridge a ~950ct gap → no
  licensed path ever authorizes the first Target-size supply order. Dailies: program_ids
  are born nightly, die in 16h, get ~1-2 lagged est-feed rows, and concluded programs
  VANISH from the live feed — per-program measured rates cannot exist at allocation time,
  so the $11,900/day daily class (119×$100 of the $17,520 family total) is structurally
  unmeasurable under the spec's identity rule; series-level rate inheritance is required
  and is an extrapolation the spec never licenses. [R1-F3i/F6, R2-F2, R3-F8 →
  CONFIRMED-LOGIC]
- **B4. Standing canon conflict is load-bearing and unresolved: does sub-Target depth
  accrue?** R1-probe (08-13..16) measured 5/7 programs accruing NONZERO on books far below
  Target both sides (recorded at :3290-3296); the filing says those snapshots pay $0.
  If the filing binds → B3 stands and SUPPLY's measurement problem is fatal as specced.
  If R1's observation binds → SUPPLY's entire rationale (buy the qualification bit) is
  built on a rule that doesn't operate as read. The 08-25 canon already ordered this
  reconciled "before ANY void-path code"; two reviewers independently rediscovered it;
  the ACDG spec ignores it. [R2-F3 → CONFIRMED as standing conflict]
- **B5. Allocation objective is gross of costs.** Fill cost is input (e) but appears in no
  eligibility or ranking formula; F14 is post-hoc. Break-even fill hazards from measured
  anchors: T5.60 ~14.5%/day of resting contracts, T5.62 ~5.5%, CLAU5 ~0.65% (per-ct rate ÷
  $0.03064/ct all-in settlement cost) — the allocator would output the identical footprint
  if fill costs were 10x. [R1-F1 → CONFIRMED-LOGIC; arithmetic checked]
- **B6. Accrual-stopping machinery is unmodeled in rate×time-left, and the floor makes any
  stop total.** Verified live values sharpen it: INV_TOLERANCE=1 (env) — a single 1ct fill
  flips the ticker HOLDING⇒EXIT-ONLY; halt = mark-basis day-peak drawdown $10, N-of-5
  confirm, then STOP + `_flatten_all()` + process exit until operator clears (:5397-5453) —
  one adverse mark event zeroes accrual on EVERY market; HELD_MAX_USD=$40 (env) flips the
  whole book reduce-only on naked held cost (:1875-1881) — reachable by ~450ct of filled
  9c supply; rival-depth withdrawal de-qualifies snapshots with our capital parked.
  Mitigation exists but is partial: REPAIR_CHEAP_FILL=1 is armed (re-pairs basis ≤$0.02,
  gates-first). None of these hazards enter the projection. [R1-F2, R3-F4/F10 →
  VERIFIED-CODE with corrections]
- **B7. Under capital stress the scrapped ranking comes back.** `cap_desired` orders
  non-incumbents by usd_day POOL (:4202-4203) and the create loop spends budget in that
  order — the footprint file carries no priority, so at the margin (and the plan runs at
  ~$290 = the cap) CLAU5's $200/day pool outranks a diesel measured 22x better per day.
  The central defect the program exists to kill, resurrected exactly at binding.
  [R3-F12 → VERIFIED-CODE]
- **B8. The backtest cannot license anything beyond cliff arithmetic.** Labels: credits
  exist only pre-08-16 (16 days of $0.00 since — credit_history 13:18:13Z); daily-gas tape
  presence 0%; >40ct never run; supplied-side never existed; and the whole tape was
  generated by the OLD env (three gate mechanics since changed). Passing it green-lights
  nothing that puts money at risk; the forward week is the FIRST real test. [R1-F11,
  R3-F16 → CONFIRMED on label facts]

## C. CONFIRMED MAJOR FINDINGS

- **C1. Rate estimator unspecified on a ~3-updates/day, 1-2h-lagged, dilution-capable
  gauge** — no window, staleness discount, presence-conditioning, or change-point rule;
  regime change (old-env anchors → new env applied 13:45:28Z) uncovered by the linear
  license; +$0.0001-at-0-resting anomaly un-quarantined. [R1-F4, R3-F8 tail]
- **C2. Eviction-mid-accrual strands banked share** — greedy has no term for value
  destroyed by leaving before the cliff (T5.58 $0.1134 forfeits on any single-night
  eviction); DROP_GRACE=3 (env) softens cancel timing, not the economics; quoter's own
  INCUMBENCY_BONUS=0.25 exists for this class and the spec re-omits it. [R3-F9 CORRECTED]
- **C3. Allocator intent vs quoter reality never reconciled** — refused-every-cycle
  tickers stay "EARNING/PROBING" in the coverage report; quoter suppression state
  (reentry_cool, mkt_out permanent set, create-fail ratchet) is not an allocator input;
  detection defaults to the 7×$0 weekly rule which cannot attribute cause. [R3-F14, R1-F12]
- **C4. PROBE tier vs armed gates** — near-money dailies (mid 0.30-0.70, spread <5 ticks)
  are mid-band-refused; widebook-admitted ones face the UNCONDITIONAL widebook credit
  re-check (runs regardless of CAPTURE_GATE): probe-size share models under the floor →
  refused → tape records $0 → allocator concludes "measured dead" — a closed loop that
  poisons its own discovery. [R3-F13, R1-F6 → PLAUSIBLE-to-CONFIRMED; replay named]
- **C5. Daily-class cadence mismatches** — 06:45Z run vs 04:00Z settles/unknown period
  opens: yesterday's daily tickers are close-past at run time; next period's programs may
  not be listed/walkable yet; no re-run trigger; book states walked at the deadest hour.
  Period boundaries UNVERIFIED — one 06:45Z + one 12:30Z programs read decides. [R3-F7,
  R1-F10]
- **C6. est-feed per-ticker SUM bug class** — the existing cache sums centicents across
  ALL non-ended programs of a ticker (:823-825); any allocator logic keyed on ticker
  (not program_id+period) double-counts at boundaries and carries prior-period accrual
  into new-period projections. [R2-F13 → VERIFIED-CODE]
- **C7. Pipeline hardening absent from spec** — no atomic write (codebase standard exists:
  :3982-3986), no staleness bound (EST_FEED_MAX_AGE_S precedent), no missing/corrupt-file
  semantics (fail-open = proxy selection resumes silently at full size over the 119-daily
  universe; fail-closed = dark + stranded accruals), no writer lock, file JSON order
  becomes de-facto priority, FOOTPRINT_TOP=5 + PIVOT quote-loop stop can silently truncate
  the probe tier with flag-dependent behavior. [R3-F11/F17, R1-F12]
- **C8. 09-06 correlated readout + stop-clock interaction** — day-1 output at anchor rates
  = 2-3 same-family weeklies paying only at 09-06/07T04:00Z → the daily credited-$ report
  reads $0 on 09-02..05 by design, consuming 4-5 of the 7 allowed $0 days before the first
  real datum, which is one correlated draw (same commodity/family/period/halt), not three.
  Clock semantics need an operator ruling. [R1-F7 → calendar arithmetic]
- **C9. Pro-rata equilibrium / dilution dynamics unpriced** — all ranking inputs public,
  rival depth dilutes by rule (A1 measured it), nightly cadence + ramp resets + cooldowns
  guarantee late arrival; no term in the design creates an uncopyable edge; the visible
  supply order advertises the flip it pays for. [R1-F8; R2-F7 denominator-composition
  sub-question PLAUSIBLE with test named]
- **C10. Census G capacity + lock-in** — d4 recorder hard-capped at 40 tickers vs 166
  family programs (≤~24% of family pool dollars measurable per sweep); watchlist-follows-
  footprint = uptime evidence only for incumbents; full-book rows multiply write volume
  (rotation DOES exist — d4_rotate.sh + timer verified on box, disk claim CORRECTED);
  re-armed UPTIME_RANK thresholds from old-census scale break silently. [R3-F15 CORRECTED]

## D. CONFIRMED MINOR / ELEVATE

- D1. Supply price generator constraints: crossing bound (price ≤ 1 − rich_touch − 0.01;
  anchor GRID-FIT precedent) — the "1-9c" band is not uniformly legal on high touches.
  [R2-F10 partial]
- D2. Ladder-vs-wall shape is a real scored trade-off (robustness to over-bid rivals vs
  exclusion insurance after partial fills); macro-probe precedent required ≥3 levels —
  needs an explicit ruling. [R2-F11]
- D3. Coverage-report EARNING must bucket by projection-vs-cliff, not accrual>0 — else the
  largest silent-$0 class (sub-cliff accruers like T5.62 $0.068/d → $0.85 → $0) reads
  green. [R2-F12]
- D4. Schema tripwires: `_qualifying_breakdown` still books 0.99-touch as qualifying
  (`bids[0][0] >= 1.0`, :2992 — VERIFIED; the fixed walk got the rule, the breakdown
  didn't) — census G must pin to the FIXED walk; DF coercion `or 0.5` on falsy df (:2314)
  must not be replicated; filing parameter ranges = free units-misparse detectors;
  programs read must cursor-page (quoter pages 5×10k). [R2-F15 → VERIFIED-CODE]
- D5. ~12x score-per-committed-$ asymmetry between sides at extreme prices — symmetric
  contracts spend ~85% of per-market capital on the expensive half; compute the
  cheap-weighted alternative before fixing mode shapes. [R1-F9]
- D6. Rich-side "join only if it doesn't qualify" must be the activate branch's LIVE
  predicate (per-cycle `ext_rich < Target`), not a nightly boolean. [R2-F16]
- D7. Consolidator addition: live entry band tops at 0.995 (env) — the quoter MAY open a
  bid at 0.99, where the venue pays $0 by the max-price rule; the only guard is the armed
  CAPTURE_GATE via the FIXED walk. Keep that dependency explicit in any gate re-shuffle.
- D8. Target/`target_size_fp` units and per-contract T&C position limits remain unread —
  both are one-read checks that bound SUPPLY's arithmetic. [R2-F3c; gaps #1/#8]

## E. KILLED / CORRECTED CLAIMS (verification ledger — what blind review got wrong)

- KILLED: "entry band stops at 0.97 while venue pays to 0.99" and "forfeits 0.98 books"
  (R1-F2b, R2-F14) — live.env `MAX_PRICE_DOLLARS=0.995`; reviewers read the code default.
- KILLED: "1c supply/anchor price banned by exclusive MIN_PRICE=0.01" (parts of R1-F3ii,
  R2-F5d/F10) — live.env `MIN_PRICE_DOLLARS=0.003`; 0.01 passes.
- CORRECTED: INV_TOLERANCE 3.0→**1** (env; finding stronger) · MAX_ACTIVATE_CAPITAL
  $150→**$60** (stronger) · HELD_MAX_USD $20→**$40** (weaker, still reachable) ·
  REPAIR_CHEAP_FILL "absent"→**armed =1** (partial mitigation exists) · DROP_GRACE 0→**3**
  · d4 "no rotation"→rotation timer exists.
- Method note: both KILLED claims were shared by two blind reviewers — same-source
  (code-default) correlated error. Blindness caught nothing there; the env verification
  layer did. Any future review round must include live.env in the reviewer facts pack.

## F. WHAT SURVIVES UNTOUCHED

The core re-pointing survives every lens: rank/allocate on venue-paid ground truth with
the $1 cliff explicit, coverage-ledger every pool dollar, keep the quoter's safety stack.
No reviewer found the PRINCIPLE wrong; all 46 findings attack the spec's mechanics,
instruments, and interactions. The three deepest structural answers now owed by any v1
spec: (1) a dilution/hazard term in the cliff projection (B1/B6/C9); (2) a real SUPPLY
path + the B4 reconciliation before it; (3) a measurement identity that can license
dailies (B3/C5/C6).

Report only — no design edits made, no build started, bot OFF (last verified inactive+
disabled 15:53:43Z). Raw reviewer outputs in session task transcripts.
