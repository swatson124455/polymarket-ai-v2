# KALSHI MAKER — HANDOFF 2026-08-05 (LOGIC-AUDIT MANDATE). BOT LIVE ON THE PILOT.

Supersedes `KALSHI_HANDOFF_2026-08-05_EOD.md` for current state. Canon unchanged:
`KALSHI_SCALE_PLAN_2026-08-04.md` · `KALSHI_MASTER_PLAN_2026-08-02.md` ·
`KALSHI_W10_ZERO_PAYER_STUDY_2026-08-04.md`. All 13 hook-injected rules bind. Memory step
zero: `project_kalshi_halt_0805.md` (contains the full 08-05 timeline).

**OPERATOR MANDATE FOR THE NEXT SESSION (verbatim intent):** "We have multiple
algos/logic/parameters running into each other. Find them and present the resolutions,
recs and why." Full logic audit of the selection→quote pipeline. Section 3 is the seed
inventory; section 4 is the method; section 5 is the copy-paste prompt.

## 1. LIVE STATE (verify fresh at step zero — every figure stale by definition)
- **LIVE on the closed-world pilot** since 2026-08-05T14:13:28Z; wedge-fixed and QUOTING
  as of 19:18:00Z plans row: footprint 19, quoted 4, fam_top KXAAAGASD $9.65,
  d3_ramp_tracked 4, committed $20.32. Cash $300.32 (15:15Z read; re-read).
- Pilot config (live.env, all operator-ruled 08-05): SERIES_ALLOW = 23 receipt-proven
  series · DAILY_LOSS_HALT 18.25 TODAY ONLY (absorbs the $8.25 tainted-window carry per
  D4; a session cron reverts to 10 at 00:00Z — **if that cron died with the session, the
  revert is the FIRST action**: sed live.env to 10; the knob live-applies) ·
  ALLOW_PROBE_EXCEPTION=1, PROBE_MAX_SLOTS=5, EXPLORE_PROBE_CT=5 · D3_RAMP=1,
  D2_FEEDBACK=1 · NETEV_GATE=1 margin −7.0 · PIVOT_SELECT=1 (armed today) ·
  MAX_VOL24H_CT=1000 with ALLOWLIST EXEMPTION (option A, `db9fafa`) · W12_PRICE_SHAPE=0
  (gated on B8 haircut re-fit).
- **Verdict (pre-committed CANON):** 5 clean days from 14:13:28Z → 2026-08-10T14:13Z;
  PASS = window credits (credit_history) > window trading drag (position-aware recorder);
  PASS → widen; FAIL → halt + autopsy. The 08-04T23:31→08-05T01:27 window is tainted and
  excluded; day 1 (08-05) is annotated wedge-shortened (~5h dead 14:13→19:15).
- Deployed quoter `19f43336` = HEAD `db9fafa`. Suite 1169 passed / 2 xfailed, exit 0.
- Watch counters each read: `daily_dd` vs halt · `d3_ramp_capped` · `d3_feedback_empty`
  · `probe_slots_dropped` · `drop_high_activity` · `netev_skipped_markets` · journal
  "systematic failure" (its absence = quoting healthy).

## 2. TODAY'S THREE LIVE COLLISIONS (all found by measurement, two fixed — the mandate's
proof that interactions, not single features, are the failure mode now)
1. **Activity gate × payer allowlist (FIXED, option A):** MAX_VOL24H_CT=1000 dropped
   24/40 allowlist rows incl 17/17 gas strikes (vol 1,001–21,636ct) and selected FOR
   decided low-vol strikes that the 4c–96c/two-sided gates then refused → quoted=0 for
   ~3h. Fix: allowlist exempt; gate live for unknowns.
2. **Probe slot cap × pre-filter ordering (FIXED):** cap ran before the clock/activity
   filter → all 5 slots burned deterministically on doomed rows → discovery dead. Fix:
   cap moved after the pre-filter.
3. **SCORE_EXPLORE quota × allowlist sizing (OPEN — audit item A1):** with a small
   footprint, `kalshi_market_scores.rank()` explore-tags up to SCORE_EXPLORE=10 rows
   INCLUDING allowlist payers → the explore probe clamp sizes them 5ct. Diagnostic
   measured 9/9 survivors tagged. Consequence: proven payers may quote probe-size instead
   of full size — directly suppresses the pilot's earning. REC: exempt SERIES_ALLOW (or
   receipt-proven) series from explore tagging — explore exists to measure UNKNOWNS. Why:
   the quota's purpose is data acquisition; taxing proven earners' size buys nothing.

## 3. AUDIT SEED — the full pipeline and every known/suspect interaction
**Pipeline order (selection → orders), each stage with its knobs:**
program harvest (status=active, ~4.1k) → allowlist/probe-exception (SERIES_ALLOW,
ALLOW_PROBE_EXCEPTION) → SERIES_DENY prefixes → date-parse/late-life (LATE_LIFE_FRAC,
MAX_ENTRY_CUTOFF_MIN) → far-close vs PROGRAM end (MAX_DAYS_TO_CLOSE) → market-clock
pre-filter: close cache + vol gate (MAX_VOL24H_CT + allowlist exemption; budget-bounded
lazy reads) → PROBE_MAX_SLOTS cap → SCORE_RANK (scores cache: decay, swing penalty,
unknown bonus, INCUMBENCY_BONUS, SCORE_EXPLORE tagging) → PIVOT picker (POOL_MULT,
COVERAGE floor, near-money proxy, density fill, PER_SERIES_CAP) → SELECT_BUDGET walk
(margin −0.3; held/incumbent/exit-only exemptions at zero cost) → footprint → quote loop
per market (read budget; gates in order: flattened-skip, band 4c–96c, wide/one-sided
(MAX_SPREAD_TICKS, MIN_DEPTH_SYM), presence floor gate (`_expected_credit_usd` =
prospective capture × payout period × presence factor; MIN_CREDIT_USD 1.20), NETEV gate
(receipt table ± margin | model fallback pc/HAIRCUT−fingerprint), standdown, JOIN sizing
`_capped_join` → explore probe clamp → D3 ramp + W7 receipt clamp → loss-governor strips
→ incumbent-only strip) → cap_desired (capital + family caps) → bound_creates → write
budget (60).
**Known/suspect interactions to audit (beyond §2):**
- A2 **D2 paid-bonus × stale score cache** (p50 ~88h at last read): bonus×pool-prior
  ranks DECIDED strikes of paid series top (TOPMODEL/TRUMPEND wedge contributors). Pivot
  near-money ordering mitigates within series; cross-series ranking still favors dead
  books. REC direction: rank input should discount rows whose ref is outside the
  quotable band BEFORE allocation, or D2 bonus should require the market (not just the
  series) to be quotable.
- A3 **SELECT_BUDGET walk semantics**: measured used=$5.00 vs limit=$211.99 with ZERO
  drop counters while the wedge ran — the walk's held/incumbent zero-cost exemptions and
  est_commit inputs (refs from prior cycle books) need a correctness pass. Suspect it
  admits little when refs are missing, without counting drops.
- A4 **Sweeper freshness**: score_age_p50 rose exactly with wall-clock for 66 min
  (zero refreshes landing) despite SWEEP_ENABLED=1 — sweeper thread health post-restart
  unverified. W6 verify script exists (`w6_sweep_verify.py`); it has never PASSED.
- A5 **NETEV_MODEL_HAIRCUT(3.0) × W12 shape**: double-discount if W12 arms without
  re-fit (documented in-code; B8).
- A6 **SERIES_DENY prefixes × allowlist**: KXDXY/KXNDQ/KXINX deny-drop allowlisted
  KXDXYDUD/KXNDQHUD/KXINXHUD if their programs return (deny runs after allowlist).
  REC: exact-ticker/series deny match or allowlist-wins precedence. Operator aware.
- A7 **daily_dd UTC-day meter × P2 window scoping**: the governor's day includes
  pre-restart tainted fills (today's 18.25 workaround). REC: a state-recorded day
  baseline at operator-named restarts, so governors and verdict windows agree.
- A8 **Two capture implementations** (`_prospective_capture` vs
  `_market_telemetry_row`): share `_w12_shape` now but remain parallel math — drift risk
  documented by the W12 review (finding A). REC: single implementation.
- A9 **Explore-probe clamp × D3 ramp × STANDDOWN × MIN_QUOTE_CT**: four independent
  size-shrinkers compose by min(); verify no path shrinks UNWIND sizes (unwind exemption
  is pinned per-feature but not for compositions).
- A10 **Presence-floor estimator** over-prediction (W10: model 6.33× median) vs the
  $1.20 floor — W12 is the dark fix; until armed the gate under-blocks sub-$1 presence.
**Audit assets available:** W11 replay harness (`w11_replay.py`) + caprank telemetry
(selection variants per cycle) · plans jsonl (per-cycle funnel counters, all drops
ALWAYS-EMIT) · FP_DROPS/FP_SHAPE · the offline funnel-replica method (diagnostic agent
proved select_footprint replicates bit-for-bit offline with live env; reuse that
pattern under /tmp/diag) · frozen artifacts in `kalshi_live/w10_results/`.

## 3b. OPERATOR RULINGS ON AUDIT EXECUTION (2026-08-05, binding)
1. **FINDINGS-FIRST (1b):** the audit completes its confirmations, presents ONE decision
   batch, the operator rules, then fixes land together. EXCEPTION per ruling 2.
2. **ACTIVE-HARM EXCEPTION (2a):** a CONFIRMED collision that is actively suppressing
   pilot earnings or causing bleed (e.g. A1 if verified) is fixed IMMEDIATELY under the
   full NORM protocol, with the verdict window annotated — do not let a known defect
   under-measure the window. Everything else waits for the batch.
3. **SIMPLIFICATION ALLOWED, FULL AUTHORIZATION REQUIRED (3-yes, operator emphasis):**
   the audit MAY recommend removing/merging features (the 4 overlapping entry brains,
   the 2 capital governors), but EVERY removal/merge needs an explicit, per-item operator
   authorization before any code changes — no batch blanket, no inferred consent.
   RULE NINE applies at full strength.
4. **BREADTH (4a):** deep pass on the live-armed pilot path first; dark/off features get
   a conflicts-at-arming check second.
5. **ACCEPTANCE (5a, DEFAULT — operator's letter pending, amendable):** done = every
   confirmed collision documented with evidence + resolution options, operator rulings
   recorded, and the ruled fixes deployed. (Upgrade to 5b architecture doc / 5c
   interaction-test pins if the operator says so.)

## 4. AUDIT METHOD (what "find them" means operationally)
1. Re-verify live state + revert-halt-to-10 check first.
2. Build the interaction MAP mechanically: for every knob/gate, list what it reads and
   writes (row fields, caches, state keys, counters); collisions = two features writing
   the same decision input under different assumptions. The pipeline in §3 is the spine.
3. For each candidate collision: REPRODUCE offline (funnel replica) or from telemetry
   (plans counters over a window) before proposing anything — measure, then rec
   (RULE THIRTEEN). Each finding: mechanism, live evidence, resolution options, REC+why.
4. Present ALL findings as a decision list to the operator (RULE NINE: nothing gets
   disabled/demoted without their word). Fixes follow the NORM (failing-before test,
   scratch mutation, blind review, md5 deploy).
5. Do not disturb the running pilot except operator-ruled fixes; the 5-day verdict
   window is accumulating.

## 5. NEXT-SESSION PROMPT (copy-paste)
---
KALSHI MAKER LANE — new session. Kalshi venue ONLY. Real money. BOT IS LIVE on the
closed-world pilot (top-23 payer allowlist, $10/day halt — VERIFY the 18.25→10 midnight
revert happened, else do it first; sed live.env, knob live-applies). Branch
`claude/maker-kalshi-live`; find the worktree via `git worktree list` (kalshi-wt under a
Temp scratchpad path); verify `git branch --show-current` before any repo write. Never
touch the main checkout or master. STOP file = halt; only the operator lifts holds.
STEP ZERO — read in order: (1) memory `project_kalshi_halt_0805.md` (full 08-05
timeline), (2) `docs/maker_handoffs/KALSHI_HANDOFF_2026-08-05_LOGIC_AUDIT.md` — THE
MANDATE IS ITS §§2-4 UNDER THE §3b EXECUTION RULINGS: full logic audit of interacting algos/parameters; find every
collision, reproduce it from telemetry or the offline funnel replica, and present
resolutions with recs and why as an operator decision list. Seed items A1-A10 are in §3;
A1 (SCORE_EXPLORE tags allowlist payers probe-size, suppressing pilot earnings) is the
first to verify live. (3) `KALSHI_SCALE_PLAN_2026-08-04.md` for the forward plan.
THEN verify live state fresh (plans/cash/journal reads; the handoff's figures are stale
by definition): quoting healthy = no "systematic failure" journal lines; watch daily_dd
vs halt, d3_ramp_capped, drop_high_activity, probe_slots_dropped. Daily P2 reads:
credits (credit_history) vs drag (position-aware recorder) scoped from
2026-08-05T14:13:28Z; verdict due 2026-08-10T14:13Z, rule = credits > drag (CANON,
pre-committed). W14: when reward chips are visible on a market page the operator does
F12→Network→save HAR — analyze it for the per-user earning signal. B8 at window end:
rebuild netev table on clean data, re-rule margin −7.0, re-fit NETEV_MODEL_HAIRCUT
jointly with W12 before arming it. All 13 hook-injected rules bind; the NORM binds
(verify-first, failing-before tests, scratch-copy mutation, blind review, md5 deploys
from git show, backups *.bak-TAG). Name work items yourself; bring the operator only
genuine decisions with options and a default (their standing process ruling).
---
