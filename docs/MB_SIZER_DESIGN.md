# MB SIZER — PRE-REGISTERED SIZING RULE (2026-08-30)

Operator: "build the sizer" → "adversarial review report then build check in
on each step" (2026-08-30). This doc is the pre-registration: the review
findings, the amended rule, and the verification record. Built BEFORE any
trader has passed, so a PASS converts to a sized pilot action instantly
instead of a design debate.

## 1. Adversarial review of the naive proposal (findings)

The naive design ("fractional Kelly shrunk by e-value") was reviewed
adversarially before build; 3 CONFIRMED defects, 2 exposures:

- **D1 CONFIRMED — invented shrinkage map.** An e-value is evidence against
  the null, not an effect-size estimate; any "e→fraction" map is an invented
  constant (zero-base violation). *Amendment:* invert the grader's own
  betting e-process (`band_tracker.e_value`) into an anytime-valid LOWER
  confidence bound (LCB) on per-market mean edge at the already-ruled bar
  1/α = 20 (= `cohort5_qualification.C1_E_REJECT`). No new constant exists.
- **D2 CONFIRMED — winner's curse.** Sizing off the observed edge of a
  best-of-52-trials selection over-bets systematically. The LCB (anytime-
  valid, immune to optional stopping) is the mitigation; residual
  multiplicity across parallel trials is disclosed, controlled by the e≥20
  bar, not solved.
- **D3 CONFIRMED — concurrent Kelly insolvency.** Kelly assumes sequential
  bets; ours resolve concurrently (the 14-day sim measured peak simultaneous
  open positions in the hundreds). *Amendment:* bankroll is pre-divided by a
  MEASURED concurrency budget (required input, no default; Sept-2 population
  study is the standing source).
- **E4 EXPOSURE — price-blindness.** Fills cluster <20¢; Kelly is price-
  dependent. *Amendment:* exact binary form. Edge atoms are net-of-fee, so
  for cost c = fill+fee paying 1: k* = (p−c)/(1−c) with p = c + LCB, i.e.
  **k_full = LCB / (1 − fill − fee)**, capped at 1. Derived, not chosen.
- **E5 EXPOSURE — physical fills.** A recorded fill existed where book depth
  ($230) < the sim's need ($278). *Amendment:* rails strictly downward:
  book-depth cap → $300 canon per-bet cap → below-minimum ⇒ **$0, never
  clamped up** (the legacy dust-clamp defect, hygiene-verified, codified in
  reverse as a pinned test).

## 2. The rule (scripts/mb_sizer.py — pure, no I/O)

```
LCB  = sup{ m : e_value([edge − m]) ≥ 20 }        (bisection; valid because
        each betting factor is positive and strictly decreasing in m;
        search domain capped at min(edges) − Y_MIN to respect the
        e-process support bound)
k    = LCB / (1 − fill − fee)                      (exact binary Kelly, ≤ 1)
raw  = kelly_mult × k × bankroll / concurrency
stake= min(raw, book_depth_usd, $300 canon cap);  stake < min_viable ⇒ $0
```

Unproven trader (e < 20 ⇒ LCB ≤ 0) ⇒ **stake = exactly $0** — structural,
not a threshold.

**Operator parameters — REQUIRED, no defaults in code:** `bankroll`,
`kelly_mult` (risk appetite; full Kelly is ruinous under estimation error),
`concurrency` (measured), `book_depth_usd` (trade-time quote),
`min_viable` (venue minimum). The only baked numbers are asserted equal to
the ruled e≥20 bar and the $300 canon cap by test.

Entry points: `recommend_stake(edges, …)` and
`recommend_stake_from_lcb(lcb, …)` — one implementation, delegate parity
verified to 1e-12.

## 3. Verification record (2026-08-30)

- Unit suite `tests/unit/test_mb_sizer.py`: 16 tests GREEN (LCB duality —
  reject just below the bound, no-reject just above; shift-equivariance;
  support-bound edge case; exact Kelly to 1e-12; concurrency scaling;
  caps; clamp-up impossibility; no-defaults signature check; ruled-constant
  equality with the grader).
- **Mutation pass: 10/10 mutants killed** (Kelly denominator sign, fee
  dropped, bisection flipped, clamp-up re-enabled, concurrency removed,
  zero-gate weakened, e-bar 20→5, both caps removed, LCB +0.05 shift).
  One initial blind spot — zero-gate weakening to −0.05 survived because no
  fixture had LCB ∈ (−0.05, 0] — closed with a borderline-LCB test; mutant
  re-run RED. Control green before, file byte-restored and green after.

## 4. Funnel wiring (read-only)

`trader_funnel.py` gains `lcb` and `$stake` columns. Stakes print ONLY when
all four env vars are set (`MB_SIZER_BANKROLL / MB_SIZER_KELLY_MULT /
MB_SIZER_CONCURRENCY / MB_SIZER_MIN_VIABLE`) — all-or-nothing, self-tested.
Display stake is evaluated at the trader's median recorded OK first-buy fill
with that record's canon fee; book depth is a trade-time input and is NOT
applied to the display (header says so). OBS rows never get a stake.

Deployed to `/opt/pa2-shared/mb_readout` 2026-08-30 (backup
`trader_funnel.py.pre-sizer-20260830`); live run 2026-08-30T21:30Z: roster
59, TRIAL 52, PASSED 0 ⇒ all stakes correctly dash/[$0-equivalent].

**Defect found by the live run:** the 16 sweep2 admits (epoch
2026-08-30T20:30Z) fell to OBS as "no per-trader test registered" — the
funnel's group map predated sweep2. Fixed (`sweep2-admit` rows now TRIAL,
52 = 36 + 16) + a negative-controlled group-completeness self-test: any
address-list group defined in the grader that the funnel does not consult
turns the self-test RED naming the group.

## 5. Open operator decisions

1. `kelly_mult` (e.g. 0.25 / 0.5 — risk appetite, yours).
2. `bankroll` basis (the ruled $500 pilot is the natural candidate).
3. `concurrency` source until Sept-2: the 14-day sim's measured peak, or a
   deliberately tighter budget.
4. `min_viable` (venue minimum order).
5. Whether the funnel cron should carry the env foursome once chosen.
