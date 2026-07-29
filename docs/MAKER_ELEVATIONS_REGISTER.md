# MAKER ELEVATIONS REGISTER

Every candidate improvement to the Polymarket Maker bot, each notated with **how
it elevates** and **how it hinders** (cost / risk / downside). Numbers discipline
per `MAKER_NUMBERS_LEDGER.md`: rewards basis only, MODEL = unverified, PAPER
marks = NOISE, state facts = MEASURED. No EV/net headline until a receipt.

Created session 7 (2026-07-24) on operator directive: "add in all elevations and
notate each ... how they elevate or hinder."

## Session-7 decision status (operator, 07-24)
1. **Wallet** — operator provisioning (the sole live blocker). ✅ acknowledged.
2. **Halted arm** — gather all info (done, §A below); resume = operator call.
3. **Redeploy engine to HEAD** — BUNDLE with the live cutover (not standalone). ✅
4. **WS event hot path** — BUILD it if feasible (it is): flagged, default-OFF,
   reviewed, shipped inside the bundle. ✅

---

## §A — HALTED-ARM SNAPSHOT (MEASURED 2026-07-25 ~01:59Z, state.json + settlements)

- Held now: **3 one-sided fossils, $100.84 spent** (2967837 NO $84.15; 678414 NO
  $13.84; 2975225 YES $2.85) — all naked, no resting orders (halted).
- `day_pnl=-$0.70`, `acc_day=$0.00`, `halted=True` (file-latched since 07-23 19:09Z).
- The −$75 floor trip was **realized fossil loss**, not mark drift: fossil 2967324
  resolved $0 payout / **realized −$75.38** (PAPER). Siblings −$61/−$60/−$19/−$14.
- These are pre-fix deadlock fossils; the merge-fix + one-sided de-risk close the
  mechanism forward. **While halted, the 3 survivors are naked** — de-risk can't
  hedge them. Resume lets it cap them before settlement.

---

## §B — ELEVATIONS REGISTER

Ranked by leverage. Tier: state facts MEASURED, reward figures MODEL/unverified.

### 1. Funded receipt — first live 00:00Z window  ★ the promotion gate
- **Elevates:** the ONLY thing that converts every model number to real. Anchors
  reward SHARE vs on-chain payment; unblocks all downstream tuning; tests the
  load-bearing "trading ≈ breakeven" assumption directly.
- **Hinders:** requires the dedicated wallet + real USDC at risk; one bad window
  (informed flow, like Kalshi's go-live) can print a real loss before edge is proven.
- **Status:** wallet-gated. Path: sanity → scoring → tiny live → receipts.

### 2. Softness multi-sampling (`mm_softness_probe` averaged)
- **Elevates:** picks the 2–3 genuinely uncrowded $20-tier markets → higher reward
  SHARE (share-rank ≠ pool-rank; whales camp fat pools at ~0% share). Directly
  raises modelled rew/day. Wallet-INDEPENDENT — can run now.
- **Hinders:** snapshot-noisy (share moved 15.5%→12.5% run-to-run); a single sample
  misleads; competitors' reaction to our resting size is unpriced (not EV).
- **Status:** tool built; needs repeated sampling to de-noise before it drives selection.

### 3. Cap sizing redesign (GAP-4) — msz-based, not flat $150
- **Elevates:** flat $150 gross leaves 30/140 markets (measured: 65/140 on the live
  arm) structurally unquotable; sizing off msz unlocks the full universe → more
  reward-eligible footprint at scale.
- **Hinders:** needs an operator capital decision; MOOT for the sub-$150 pilot (a
  smaller wallet can never bind a $150 cap). Wrong sizing re-introduces
  concentration/adverse-selection risk. Must NOT be conflated with the (fixed)
  merge-blindness bug.
- **Status:** parked, propose-only, post-pilot.

### 4. WS event-driven hot path (~200ms reprice) — Kalshi `614eb5a` port  [decision 4]
- **Elevates:** reprice latency ~1s → ~200ms on book ticks → tighter quotes, less
  stale-quote adverse selection, better queue position. Cold path = existing
  guarded cycle verbatim (all guards intact).
- **Hinders:** structural change to the LIVE-CAPITAL loop (extract `run_once()`,
  event-drive it) — CLAUDE.md Rule 7 territory; large blast radius; even Kalshi
  keeps Stage B default-OFF/unreviewed. Poly is already ws-driven at 1Hz, so the
  gain is marginal until receipts prove the strategy earns.
- **Status:** BUILD flagged (`MAKER_WS_HOT`, default OFF) + tests + independent
  adversarial review + paper smoke; ship in the cutover bundle, not ahead of it.

### 5. Classifier fixes (bug class #1 `heat-`, #2 `\biran`/`\bnato`/`\bpremier\b`/`\bepl\b`)
- **Elevates:** correct sector labels → the allowlist gates the RIGHT markets; stops
  mislabelled markets dodging/walking the pilot allowlist (a BTS album read as
  geopolitical; an NBA slug as weather).
- **Hinders:** none functionally; on the halted allowlist-OFF paper arm it is
  measurement-attribution only. Cost = the deployed arm is 1 commit behind (472 B)
  until redeploy.
- **Status:** fixed on HEAD (`d9dfd87`); redeploy REQUIRED before live — bundled (decision 3).

### 6. One-sided de-risk (`MAKER_ONESIDED_DERISK`, ON)
- **Elevates:** hedges an accumulating one-sided leg (buy complement → $1 pair →
  merge) so unwanted directional inventory can't ride naked into settlement — the
  exact fossil loss seen in §A (−$75 realized on 2967324).
- **Hinders:** the lone hedge scores ZERO rewards (two-sided min) — risk reduction
  only, never restores income; if price already drifted, hedging LOCKS the loss
  vs. gambling on resolution. Frequent firing (`derisk1` climbing) means the real
  problem is cap sizing (GAP-4), not hedging.
- **Status:** ON (operator 07-23); revisit on first receipts / scaling / frequent derisk1.

### 7. Sector allowlist (pilot pinning)
- **Elevates:** pins sectors BEFORE ranking so a shrunk max-markets can't grab wrong
  markets; fail-closes the "unknown" gate hole out of the pilot.
- **Hinders:** excludes the 4 cheapest $20 "unknown" markets (deliberate); a
  mislabel (see #5) still mis-gates until the classifier fix ships.
- **Status:** built + staged (`maker-pilot-env.staged`), wallet-gated.

### 8. "unknown"-sector in-play gate hole fix (full-universe)
- **Elevates:** the in-play settlement gate keys on sector ∈ (sports,esports);
  ~45% of "unknown" markets carry a sports/event signature and quote straight
  through settlement. Fixing it removes a fail-open source of trading drag.
- **Hinders:** de-fanged for the pilot already (allowlist excludes unknown); a
  full-universe fix touches classification + gate → needs care + review.
- **Status:** parked; de-fanged for pilot, unfixed full-universe.

### 9. Pre-settlement flatten of survivors (Poly-native, = ensure de-risk fires pre-close)
- **Elevates:** directly targets the §A loss — a naked fossil resolving to $0. On
  Poly the no-taker-safe equivalent of Kalshi's pre-close flatten is a de-risk
  BUY of the complement (merges to $1), capping the stake before settlement.
- **Hinders:** locks the loss if already adverse; forgoes upside if the held side
  would have won; only helps markets the sweep hasn't already departed.
- **Status:** partially covered by #6; gap = making de-risk reliably fire on
  near-close survivors. Propose-only.

### 10. Reconciler → engine wiring
- **Elevates:** on-chain reconciliation feeds the engine's own state → catches
  drift between believed and actual inventory before it costs money live.
- **Hinders:** build + review cost; only meaningful once live (paper has no chain).
- **Status:** parked, post-wallet.

### 11. `qh` partial-row consumption fix (G2)
- **Elevates:** stops paper over-crediting a partly-filled one-sided row → cleaner
  measurement of the de-risk success path (the mode meant to measure it).
- **Hinders:** measurement-only; no live-money effect. Small.
- **Status:** parked.

### 12. Sub-msz scoring empirical test
- **Elevates:** settles whether a $3 order scores at all (place $3 + $20, call
  `is_order_scoring` on both) → confirms the $20-tier floor is the right pilot size,
  not wasted capital.
- **Hinders:** needs the funded wallet to place real orders; can't be reasoned, only measured.
- **Status:** wallet-gated (in the staged-env open items).

### 13. Future Kalshi ports — net-EV gate / capture gate / stand-down / pivot-select
- **Elevates:** receipt-calibrated gates that open less where reward doesn't justify
  fill loss, skip markets below a capture floor, and backfill footprint with earning
  markets — all raise realized rew/cost once calibrated.
- **Hinders:** ALL are receipt-calibrated — meaningless until we have receipts (they'd
  be tuned on model numbers = the banned circularity). Kalshi keeps them default-OFF.
  Their taker/flatten machinery is N/A by platform (both our legs are BUYs).
- **Status:** future; gated behind the first receipt (per doctrine port).

---

## §C — SEQUENCING (what elevates without hindering the pilot)

- **NOW, wallet-independent, no live risk:** #2 softness sampling; build #4 WS hot
  path (branch only, flagged OFF, review) — both improve the cutover without
  touching live state.
- **AT CUTOVER (bundled, decision 3):** redeploy HEAD (#5) + apply staged env (#7)
  + WS hot path shipped default-OFF (#4).
- **FIRST RECEIPT unlocks:** #1 promotion, then #13 gates, #6 revisit, #3 cap sizing.
- **DO NOT do ahead of receipts:** anything receipt-calibrated (#13) or any
  structural change that rides the live cutover unproven.

### 7–12. Session-8 Kalshi-audit ports (E-A…E-F, built 07-29, on branch, default-off)
Audit: Poly engine vs Kalshi maker (their 07-29 LIVE_SCALED handoff + post-07-22
commits, read-only design reference; no blind copy; taker/exit machinery = N/A by
platform). Gate chain: 181 tests, 9 mutants killed, independent adversarial review
SHIP-WITH-FIXES (all fixes applied), isolated 3-run VPS paper smoke PASS.

- **E-A two-arm halt** (`MAKER_DAY_REALIZED_FLOOR_USD`, 0=off). Elevates: the
  single mark-fed floor took the paper arm dark twice on NOISE-tier mark swings
  (07-25: settle_realized −$0.57 of the −$76.20 trip); the REALIZED arm fires on
  settlement-realized loss only and its kill message self-diagnoses. Hinders:
  same-day resume re-fires by design (documented in the message); one more knob.
- **E-B config fail-loud**: `off` disables MAKER_ONESIDED_DERISK (twin of the
  Stage-A ws_hot footgun); banner echoes caps/derisk/rfloor/clock. Hinders: none
  found (print + parse-set only).
- **E-C market-clock veto** (`MAKER_MIN_HOURS_TO_END`/`MAKER_MAX_DAYS_TO_END`,
  0=off): automates the 07-25 expiring-cluster exclusion at DISCOVERY; fail-closed
  on unparseable end when enabled. Hinders: staged 24h veto cut 38 of 64
  allowlisted survivors at review time (MEASURED, reviewer's live-gamma read) —
  intended, but shrinks the pickable set.
- **E-D funnel audit** (`mm_funnel_audit.py`, read-only): per-market first-gate
  waterfall; GAP-4-class diagnosis becomes one command. Hinders: needs engine env.
- **E-E capital-aware ranking** (softness probe extension): share-model rew/day ÷
  measured committed capital (both BUY legs ≈ msz·(1−v)); the Kalshi task-#1 shape.
  Hinders: MODEL until receipts; fill-cost term deferred to post-receipt tape.
- **E-F signal-only monitor** (`mm_live_monitor.py`, read-only stdin filter):
  KILL/halt transitions/pnl jumps/derisk1/zombie signals only; validated 7/7
  against the real 07-25 kill tape. Hinders: none (consumes text).

### GAP-4 CAP-SIZING DESIGN PROPOSAL (S8, operator-directed follow-up to the
### fired derisk1 trigger; PROPOSE-ONLY — no code until receipts + an operator
### capital number)
Trigger record (MEASURED, 07-25 journal): derisk1 63->650 in ~10 quoting hours;
deny market_gross_cap=5901 cumulative; 65/140 markets denied on the live arm
(07-23 hb) vs 30/140 in the original census — the flat $150 gross cap is the
binding constraint on the FULL universe (it cannot bind the sub-$150 pilot).

Design (Kalshi capital-aware-ranking shape, re-derived for OUR gate):
  market_gross_cap_i = clamp(K_msz * msz_i * (1 - v_i), CAP_MIN, CAP_MAX)
- msz_i*(1-v_i) = measured cost of ONE two-sided min quote (both legs BUY);
  K_msz = how many min-quote units of inventory a market may accumulate
  (the current flat $150 at msz=$100 weather ~= K 1.5; at msz=$20 ~= K 7.5 —
  the flat cap is secretly a WILDLY uneven K, which is the whole defect).
- CAP_MIN/CAP_MAX + a portfolio budget check (sum of caps vs wallet) are the
  operator capital decision; sector caps stay as-is on top.
- Ship telemetry-FIRST (the Kalshi task-1 pattern): log would-be msz-cap
  denies alongside actual flat-cap denies for >=3 days on the paper arm,
  THEN flip behind a default-off env knob with the full gate chain.
Blockers by design: (1) first receipts (income model calibration), (2) the
operator wallet/budget number, (3) RULE NINE sign-off to activate.
