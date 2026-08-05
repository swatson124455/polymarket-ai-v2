# KALSHI LOGIC AUDIT — FINDINGS & OPERATOR DECISION BATCH (2026-08-05, session 2)

Mandate: KALSHI_HANDOFF_2026-08-05_LOGIC_AUDIT.md §§2-4 under §3b rulings.
Method: every finding below is REPRODUCED from live telemetry (quotes/plans/caprank
jsonl, VPS state) or pinned to file:line code evidence. Labels per RULE SIX.
Suite at draft: 1174 passed / 2 xfailed, exit 0 (commit d1a9db9).

## 0. ALREADY EXECUTED (active-harm exception, §3b ruling 2)

**A1 (rank-explore path) — CONFIRMED and FIXED, deployed 2026-08-05T19:59:43Z.**
- Mechanism: `kalshi_market_scores.rank()` explore quota (SCORE_EXPLORE=10) tagged
  stale/unknown rows with NO allowlist exemption; the EXPLORE_PROBE_CT=5 clamp
  (quoter:4680) then probe-sized proven payers.
- Live evidence (ESTABLISHED, quotes-20260805.jsonl 14:13→19:41Z): KXTOPMODEL-26AUG10-
  CLAUM explore_probe_capped 28× (5ct both sides, ramp_capped 0); KXAAAGASD strikes 2×
  early. Score cache staleness (score_age_p50 92.6h) made every payer row "stale" and
  explore-eligible.
- Fix `d1a9db9`: rank() gains `explore_exempt` (default None = byte-identical);
  quoter passes frozenset(SERIES_ALLOW). 5 pins (test_a1_explore_exempt.py P1-P5).
  Adversarial blind review: nothing blocking; P2 pin repaired per review (it was
  non-discriminating); deploy md5-verified vs HEAD blobs, backups .bak-A1-20260805_195931.
- Post-deploy verification (first new-code cycle 19:59:43): no rank-path caps on any
  clean payer series; probe containment intact (KXADJOURNRECESS still capped, correct).
- **P2 window annotation: day 1 was rank-path size-suppressed 14:13→20:00Z on top of
  the wedge (14:13→19:15 quoted=0). TOPMODEL/TRUMPEND additionally remain L3-suppressed
  (A1b below) until ruled.**

## 1. DECISION ITEMS (RULE NINE — nothing below changes without your word)

### A1b — L3 series-probe permanently probe-sizes 6/23 payer series off SETTLED convictions
- Mechanism (quoter:3520-3533): any footprint row whose SERIES has a `mkt_out` member
  (loss-governor $5-rung permanent ban) is explore-tagged → 5ct, incumbents included
  (the 2026-07-31 MLABELSHARE reversal: −$10.88/45min churn at full size).
- Live state (ESTABLISHED, quoter_state.json read 19:52Z): mkt_out n=10, of which 6 are
  in allowlist series: KXCLAYTONDNI-27JAN01-0807, KXMLABELSHARE-W3026JUL30-SME,
  KXTEMPAUSH-26AUG0203-T81.99, KXTOPMODEL-26AUG03-CLAU5, KXTRUMPENDORSEMENTS-26AUG01-A5,
  KXTRUMPTIME-26AUG01-H2. 9/10 convictions are on settled/expired markets. mkt_out is a
  bare ticker list — no conviction date, no evidence pointer, no expiry.
- Live cost today (ESTABLISHED): series_probe=12 rows/cycle; the only currently
  quotable one is KXTOPMODEL-26AUG10-CLAUM — 5ct on a $200/day pool, capture
  $1.12/day at probe size (model basis). INFERRED: at D3-ramp full size (TOPMODEL is
  receipt-proven so W7 lets it climb 5→10→25→50ct) capture would be roughly
  5-10× that on the same model — the model over-predicts (W10 6.33× median), so treat
  as direction, not dollars.
- Tension: L3 is a RISK feature installed on live bleed evidence; the pilot's earning
  goal wants payers at full size. Two features writing the same decision input
  (row["explore"]) under different assumptions — the mandate's exact collision shape.
- OPTIONS:
  a) Allowlist exemption from L3 (mirror of the A1 rank fix; receipts are the screen).
     Removes the bound on payer series that HAVE bled before (TOPMODEL's own strike
     hit a $5 rung on 08-03 era flow).
  b) Conviction EXPIRY: L3 taint clears when the convicted ticker's market settles
     (a settled strike can't bleed again; the fresh-sibling first-burn bound stays for
     series whose convicted strike is still open). Keeps L3 for NETFLIX (open ticker).
  c) Rung-count threshold: series-probe only when ≥2 members convicted (single-strike
     convictions are usually the strike ladder's edge, not a series property).
  d) Leave as-is (payers with any conviction stay probe-sized; earning stays capped).
- REC: (b), optionally (b)+(c). Why: it fixes the actual defect — a PERMANENT taint
  from evidence that can no longer recur — without weakening L3 where its evidence is
  live. (a) is simplest but discards a bound the operator installed on measured bleed.
  DEFAULT if unruled: as-is (d) — nothing changes without your word.

### A2 — ALLOC/D2 ranking has no quotability discount; decided strikes of paid series absorb footprint+budget
- Mechanism: ALLOC_KEY=1 + D2_FEEDBACK=1 (both LIVE): cap_score = base × d2(1.5 paid)
  × calib − λ·cost, over commit (kalshi_capital_rank.py:181-195). No term asks whether
  the market's ref is inside the 4c-96c band or two-sided — a DECIDED strike of a paid
  series ranks on pool × bonus.
- Live evidence (ESTABLISHED, cycle 19:59:43Z): footprint 17 = 14 rows refused by quote
  gates (5 TOPMODEL decided/one-sided incl 4 gate_one_sided, 7 TRUMPEND one-sided,
  2 gas wide/asym) + 3 quoted. select_budget_used $115-158 vs committed $16.89 —
  the budget walk charges est_commit for rows the quote gates then refuse.
- Live harm in the PILOT: bounded — universe (34 rows) < FOOTPRINT_TOP (40), so nothing
  quotable is displaced today. Harm activates at W6 widening (universe ~1,905).
- OPTIONS:
  a) Rank-input discount: rows whose cached/known ref is outside the entry band get
    base×0 (or ×0.1) BEFORE allocation (handoff REC direction).
  b) D2 bonus requires the MARKET (not just series) quotable at last observation.
  c) Defer to W6 arming checklist (pilot harm bounded; fix before widening).
- REC: (c) now + (a) designed and tested as part of the W6 gate. Why: no measurable
  pilot bleed today, and W6 is where it bites; changing live ranking mid-verdict-window
  adds noise to P2. DEFAULT: (c).

### A4 — RESOLVED as measurement-tool defect: w6_sweep_verify can never PASS; sweeper itself healthy
- Sweeper HEALTHY (ESTABLISHED, plans sweep stats 19:35Z): reads 1243 = stored 1243,
  err_429 0, passes 24; pcap_age_p50 68.8-69.8m ≈ the ~72min full-pass period.
- w6_sweep_verify run (REPRODUCED, 20:07Z): "NOT YET — need scored>=1000 and
  p50<=180m"; scored=8192 ✓ but it gates on score_age_p50 (5580m). That gauge is ts =
  ACTUAL-quoting measurements, which the 2026-07-31 gauge split deliberately excluded
  from sweeper writes — with quoted=3 of 8192 rows, ts-p50 mathematically cannot reach
  180m in the pilot. The handoff's "zero refreshes for 66min" (A4 seed) was the same
  mechanism during the wedge (quoted=0 → no D4 folds).
- DECISION: repoint the verify criterion at pcap_age_p50 (the gauge the sweeper DOES
  write) — needs your word since it changes a verification standard (RULE NINE).
  DEFAULT: leave; then W6 stays formally unverified though the sweeper is measured healthy.

### A6 — SERIES_DENY prefixes shadow 3 allowlisted payer series (armed-to-misfire, not binding today)
- SERIES_DENY=KXDXY,KXNDQ,KXINX,KXDJI (prefix startswith, quoter:1726, runs AFTER
  allowlist) vs SERIES_ALLOW containing KXDXYDUD, KXNDQHUD, KXINXHUD.
- ESTABLISHED: drop_series_deny=0 all day (those programs inactive today) — misfires
  only when their programs return. Operator was already flagged 08-05 evening.
- OPTIONS: a) exact-series deny match; b) allowlist-wins precedence; c) rename deny
  entries to exact tickers. REC: (b) — one-line precedence rule, preserves the deny
  list's intent (fast index books) while receipts keep their standing. DEFAULT: as-is.

### A7 — daily_dd is UTC-day-scoped; operator-named restarts inherit tainted carry
- Code (quoter:4281-4301): equity_day_peak resets only on UTC day change; a mid-day
  named restart keeps the pre-restart peak → today needed the 18.25 absorption hack.
- DECISION: record a day-baseline in quoter state at operator-named restarts (STOP
  archive event), so the governor's day and the P2 verdict window agree. Purely
  additive state key + one reset line; the 18.25-style manual absorption disappears.
  REC: yes, build dark, arm at next named restart. DEFAULT: as-is (manual absorption
  each time).

### A5 / A8 / A10 — documented, gated, no action until B8/W12 (conflicts-at-arming class)
- A5 double-discount (haircut 3.0 × W12 shape): pinned in-code (quoter:2280-2283);
  W12_PRICE_SHAPE=0; the B8 joint re-fit gate stands. NO ACTION.
- A8 two capture implementations: single `_w12_shape` is shared (quoter:513) but
  `_prospective_capture` (:2254) and `_market_telemetry_row` (:2405) remain parallel
  math. Merge = removal-class change → needs explicit per-item authorization; nothing
  drifts TODAY (W12 review already aligned the bases). REC: defer merge to a quiet
  window post-P2. DEFAULT: as-is.
- A10 presence floor under-blocks (model 6.33× median over-prediction vs $1.20 floor):
  W12 is the dark fix; gate = B8 re-fit. presence_skipped=0 today (pilot books rich).
  NO ACTION until B8.

### A9 — size-shrinker composition: core invariant HOLDS; 3 latent unwind-loss paths found
Full 16-stage shrinker map + 9 findings in the audit agent report (this section = the
decision-relevant subset). The invariant "no counted shrinker reduces an unwind's count"
is REPRODUCED-IN-CODE across explore clamp (:4685), D3/W7 (:563), loss strips (:4718),
incumbent strip (:4728), breaker (:4951), cap_desired (:3181), create gates (:82,:5172).

- **A9-F4 (LIVE-ARMED — KALSHI_DROP_GRACE=3 in live.env): grace retention strips the
  unwind reason tag.** apply_drop_grace copies retained orders as {side, price, count}
  only (:2886) and runs BEFORE cap_desired (:5049). Composition: held ticker rotates
  out of footprint + strand-unwind pass fails (read-budget break :4824, transient fetch
  :4826, no-exit-side :4841, crossed :4844, unpriceable :4855) → grace retains the
  resting EXIT untagged → cap_desired treats it as accumulating → tail-cut/family drop
  → diff_orders CANCELS a live resting exit on a held position. The in-loop fetch-fail
  path explicitly re-tags unwind to prevent exactly this (:4619-4632); grace lacks it.
  Not observed live (no occurrence counter exists); conditional, not active harm.
  REC: surgical fix — carry the reason tag through grace retention (mirror :4619-4632).
  Failing-before test + NORM. DECISION: authorize the fix (it removes nothing).
- **A9-F2 (latent, currently unreachable): ladder escape hatch halves an unwind**
  (:4932) compensated by an accumulating "ladder" leg that 5 downstream paths can drop
  (breaker/cap_desired/bound_creates/capital gate/ratchet) — leaves the unwind at half
  size uncompensated. Trigger unsatisfiable under post-Q1 pricing (in-code comment
  :4924). REC: note-and-pin (a test asserting the trigger stays unreachable), no code
  change. DECISION: pin only.
- **A9-F3: write budget charges cancels first** (:3220 budget = MAX − len(cancels)) —
  a mass-strip cycle (≥ budget cancels) drops ALL creates incl. unwind-first groups for
  one cycle. Bounded: already-resting exits untouched. Live exposure low at footprint
  ~20-40; rises at W6 widening. REC: fold into the W6 arming checklist (cap cancels or
  reserve create headroom for unwind). DECISION: defer to W6 gate.
- A9-F6 (SUSPECTED, same class): READ budget exhaustion before the strand pass can
  end a cycle with held tickers absent from desired → resting exits cancelled
  (DROP_GRACE=3 mitigates by retaining… but see F4 — retention is exactly the
  tag-stripped path). No occurrence counter. REC: add a counter (additive telemetry)
  now; assess at W6. Reads 109-167 vs budget 200 today — not binding.

### A3 — SELECT_BUDGET walk: no silent-vanish defect; the wedge numbers explained; 3 real interaction findings
Direct answers (REPRODUCED-IN-CODE, agent audit of :4408-4485):
- No row can vanish without a counter inside the walk (4 exits: held-kept, exempt-kept,
  drop_family_budget, drop_budget_full; exceptions fail-open + _SILENT). The wedge
  signature (used=$5.00/zero drops) was NOT a walk defect: pre-A1 every allowlist
  survivor was explore-tagged → est clamped to $5, and the activity gate had already
  eaten the footprint upstream. Mechanism closed.
- Missing ref → row admitted at MAXIMUM est (p=0.5), never dropped, no counter. Refs
  come ONLY from prior own-quoting folds (SCORES.ref, 6h cutoff) — the sweeper's pref
  is never consulted by the walk, so every fresh entrant charges max.
- Limit = 0.7 × mark equity (observed 211.67 = 0.7 × 302.39) — EQUITY, not
  MAX_TOTAL_CAPITAL=350, is the binding term; family cap floats at 0.25 × equity ≈ $75.6.

Decision-relevant findings:
- **A3-F5+F7 — family eviction is ALPHABETICAL among same-pool siblings, and evicted
  rows lose drop-grace (standing book torn down same cycle).** Same-series rows share
  one pool value → sorted(-prio, ticker) admits the lexicographically-EARLIEST strikes,
  not the near-money ones the pivot picked for quality; INFERRED with live knobs
  (MAX_MARKET_CAPITAL=45, INV_HARD_CT=50, famcap ≈$75.6): ≈1 non-held full-est sibling
  admits per family → the observed drop_family_budget=14/cycle is the gas-ladder tail.
  Held/incumbent rows are exempt (quoted strikes safe), but a NEW near-money strike can
  be evicted in favor of a decided lexicographically-earlier sibling, and a standing
  book family-evicted loses its queue asset immediately.
  REC: (i) per-ticker family-drop telemetry (additive) now; (ii) near-money-aware
  tie-break (distance-to-ref instead of ticker) as a ruled fix. DECISION: (i) needs no
  ruling (additive telemetry, will build+deploy with the batch fixes if authorized);
  (ii) needs your word.
- **A3-F6b — est ignores the D3 ramp/W7 clamp**: a ramp-capped payer (young ticker,
  5-10ct actual) is budget-charged at full est ($45-50) → evicts siblings over demand
  that cannot materialize this cycle; over-read has no alarm (the backstop only alarms
  under-read). Post-A1 this worsened (payers now full-est). Also allowlist families
  are now a MIX of $5-est (L3-tagged, A1b) and full-est siblings — order-sensitive.
  REC: charge est at min(full, current ramp rung × price) — needs your word (sizing-
  adjacent logic). DEFAULT: as-is + watch drop_family_budget.
- **A3-F3b — held free-ride**: a held ticker's NEW accumulating quotes charge $0
  against the walk limit (only cap_desired backstops) → select_budget_used understates
  true demand. Note for reading the gauge; no change proposed.

## 2. WATCH LIST (armed, post-A1 deploy)
- drop_budget_full / drop_family_budget (A1 fix makes payer est full-size → budget
  binds sooner; reviewer concern, expected+intended, verify eviction lands on tail).
- explore_probe_capped on any NON-mkt_out allowlist row = regression of the A1 fix.
- d3_ramp_capped rising on gas strikes = ramp working (expected).
- Halt-revert timer kalshi-halt-revert-0806 fires 2026-08-06T00:00Z (transient — does
  not survive VPS reboot; re-verify after midnight: live.env =10 + journal tag
  kalshi-halt-revert).

## 3. P2 LEDGER (day 1)
- Window credits since 14:13:28Z: $0.00 (ESTABLISHED, credit_history read 19:38:42Z,
  n=0 of 58 lifetime / $198.95 — payouts lump at close+1, none expected yet).
- Window drag: −$0.26 d_cash (ESTABLISHED, recorder 14:15:44→19:36:00Z), $1.92 held
  cost marked $2.39 (unreal +$0.42, plans 19:35Z).
- Day-1 annotations: wedge-dead 14:13→19:15; A1 rank-suppression →20:00; TOPMODEL/
  TRUMPEND L3-suppressed all day (pending A1b).
