# D1+D6 CAPITAL-COUPLING STUDY — 2026-08-02 (READ-ONLY measurement)

Scope: the BLOCKING unknown from the 2026-08-01 selection review §5.3 — does `cap_desired`'s
total-capital cut, once armed by the D1/D6 fixes pushing the footprint from ~12 toward 40,
produce continuous order churn (queue-position destruction against WRITE_BUDGET=60)?

Method: code read at the worktree checkout (`kalshi_live/maker_kalshi_quoter.py`,
branch `claude/maker-kalshi-live`; checkout HEAD at read time `f55b4c5` — the study brief
said `751f0cd`; the branch advanced by Sel-D9 between briefing and reading; every function
cited below was read at `f55b4c5`) + offline analysis of VPS logs
`/opt/pa2-maker-kalshi-live/{plans,quotes,caprank}-2026072{9}..20260801.jsonl`.
No repo file (other than this new doc), no VPS state touched. Bot was STOPPED (STOP file,
2026-08-01T23:13Z) throughout.

Data windows (denominators used throughout):
- plan rows: 07-29 n=2,285 · 07-30 n=1,898 · 07-31 n=1,157 · 08-01 n=1,740 (08-01 is
  partial: 00:00–23:13Z, STOP at 23:13).
- quotes rows (per-market-per-cycle): 104,230 rows / 7,079 cycles / 55,586 nonzero
  (actually-quoted) market-cycles, 07-29..08-01.
- caprank rows (env variant, top-40 components): 11,458 cycles, 07-29T17:49Z..08-01T23:13Z.
- live.env read 2026-08-02 (mtime 00:05Z): FOOTPRINT_TOP=40, MAX_MARKET_CAPITAL=45,
  MAX_TOTAL_CAPITAL=350, WRITE_BUDGET=60, INV_HARD_CT=50, PER_SERIES_CAP=100,
  SERIES_MAX_USD=100, SERIES_PCT unset→0.25 (default), DROP_GRACE=3, SCORE_RANK=1,
  INCUMBENCY_BONUS=0.25, **KALSHI_ALLOC_KEY=1 staged**, ALLOC_INCUMBENT_FIRST unset→0 (dark),
  PIVOT_SELECT unset→0 (dark). All logged cycles ran with ALLOC_KEY **absent (=0)** —
  confirmed by `env_absent` in every 08-01 plan row.

---

## 1. What the code does when the total-capital cut fires (ESTABLISHED, code read)

`cap_desired(desired, usd_day, incumbents=None, fam_held=None)` — quoter :2597:

1. Every market with any `reason=="unwind"` quote is kept UNCONDITIONALLY (and its dollars
   count toward the total and its family).
2. Remaining markets are ordered by `(0 if t in incumbents else 1, -priority[t])`,
   Python-stable sort, so ties keep `desired` insertion order (= footprint/quote-loop order).
   - `incumbents` is passed ONLY when `ALLOC_INCUMBENT_FIRST=1` — **dark in live.env**, so
     today `inc=∅` and ordering is pure priority.
   - Priority = `alloc_prio` from `_alloc_priority(footprint, now, usd_day)` (:2531, called
     :2962). ALLOC_KEY=0 → the pool dict `usd_day` verbatim. ALLOC_KEY=1 (staged) →
     `kalshi_capital_rank.shadow_rank(...)` cap_score:
     `(base·calib − λ·fill_cost) / max(est_commit, $1)` with base = measured capture
     (decayed) > fresh sweeper pcap > pool prior; fail-OPEN back to `usd_day`
     (counts `alloc_key_fail`). NOTE: `_alloc_priority` scores the already-selected
     footprint rows only; a drop-grace-retained ticker absent from the footprint gets
     `priority=0` → sorts LAST → is cut first.
3. Walk in that order: family cap (`_series_cap() = min(SERIES_MAX_USD=100, SERIES_PCT·equity)`)
   SKIPS a sibling (`continue`, dollars flow to next family, counted `series_cap_dropped`);
   the TOTAL cap (`_total_cap() = min(MAX_TOTAL_CAPITAL=350, equity)`) is a **tail-cut
   `break`**: the first accumulating market that would breach it AND EVERY market after it
   in priority order is dropped, even if a smaller later market would still fit.
   `capped_markets` = that tail count.
4. A dropped market is simply absent from `desired`. Downstream, `diff_orders` (:2380)
   survives an order only on exact (side, price, count) match → every standing order of a
   cut market becomes a CANCEL that same cycle. **`apply_drop_grace` (:2300) explicitly does
   NOT protect it**: grace covers only tickers absent from the footprint ("we didn't look"),
   never "we said no" — a cap-cut ticker is in the footprint, so its book is torn down with
   zero hysteresis. If it re-enters the kept set next cycle, its quotes are re-created at the
   BACK of the queue (creates are new orders; `split_amends` only covers same-price size
   decreases).
5. `bound_creates` (:2650): cancels are NEVER budget-bounded; creates are kept in whole-ticker
   groups, unwind groups first, then by the same `alloc_prio`, within
   `WRITE_BUDGET (60) − len(cancels)`.

So the feared mechanism is real in code: cut → same-cycle cancels → (on rank wobble)
next-cycle re-creates at queue-back. Whether it *oscillates* is an empirical question about
the ordering key's stability — measured below.

---

## 2. The cut is NOT virgin — it fired for three straight days (ESTABLISHED, plan rows)

The review's "capped_markets=0 in 1,125/1,125 cycles" holds only for its (08-01) window.
Over the full period:

| day | cycles | cycles with capped_markets>0 | capped p50/p90 (when >0 incl. 0s: p50) | est_capital_usd p50 | equity p50 |
|---|---|---|---|---|---|
| 07-29 | 2,285 | 1,365 (60%) | 5 / 8 | $218.30 | $274.82 |
| 07-30 | 1,898 | 1,890 (99.6%) | 5 / 8 | $323.31 | $318.39 |
| 07-31 | 1,157 | 690 (60%) | 4 / 6 | $281.93 | $287.74 |
| 08-01 | 1,740 | 0 | 0 / 0 | $127.24 | $278.29 |

On 07-30 the desired book saturated the portfolio-tracking cap (est_cap p50 $323 ≈
min(350, equity 318)) and the cut fired in 1,890/1,898 cycles — i.e. **we already have three
days of live evidence of the armed regime**. 08-01's quiet is the anomaly (loss-governor
exit-onlys + thin quoting: `loss_exitonly=8, mkt_out=7` in the closing plan row), not the norm.

**Real order ops while the cut was firing (ESTABLISHED, plan rows):**
- 07-30 (cut firing 99.6% of cycles): 979 cancels + 2,025 creates across 1,898 cycles;
  cancels/cycle p90 = 2.
- 08-01 (cut never fired): 599 cancels + 702 creates across 1,740 cycles; cancels p90 = 2.
- 07-29 (noisiest day, cut firing 60%): 4,798 cancels + 5,176 creates / 2,285 cycles;
  cancels p90 = 6. Order_ops p50 that day = 2, p90 = 12 — still ≤ WRITE_BUDGET 60.

A continuously-armed cut did NOT produce a cancel storm under the live (ALLOC_KEY=0) key.

---

## 3. Per-market capital and the arming threshold

Per-market capital, three measurements:
- **Pre-cut demand** (quotes rows, written BEFORE `cap_desired` — verified in code, the
  telemetry block states books are evaluated "BEFORE the capital cap gates the create"):
  nonzero market-cycle notional p10/p25/p50/p75/p90 = $19.00/$33.60/**$44.00**/$51.20/$66.80,
  max $74.30 (n=55,586 market-cycles). ESTABLISHED.
- **Post-cut funded book** (plans, est_capital_usd/quoted_markets, 07-30..08-01 n=4,792
  cycles): p10/p50/p90 = $15.88/**$38.10**/$56.67. ESTABLISHED.
- **Model commitment** (caprank env `commit_usd`, mirrors `_capped_join` sizing at
  MAX_MARKET_CAPITAL=45/INV_HARD_CT=50): p10/p50/p90 = $20.30/**$31.00**/$46.00
  (n=458,320 market-cycles). ESTABLISHED as a model output; it under-reads real demand by
  ~30% (skew/quantization).

**Arming threshold (INFERRED from the above):** the cut arms at
`N_arm ≈ C / per-market-$`:

| per-mkt model | N_arm at C=$252 (equity floor, 07-30..08-01 tce min $252.08) | N_arm at C=$350 (env cap) |
|---|---|---|
| $44 (demand p50) | **5–6 markets** | **7–8 markets** |
| $31 (commit model p50) | 8 | 11 |

Cross-check on real ranked lists: walking the caprank env top-40 in cap_score order and
cutting at C, the funded count is p10/p50/p90 = 4/**6**/10 at C=$252 and 6/**9**/15 at
C=$350 (n=11,458 cycles). ESTABLISHED (given the commit model).

Demand at hypothetical footprints (INFERRED, per-mkt $44 p50 / $31 model):
- N=20 → $880 / $620 → 2.5–3.5× / 1.8–2.5× oversubscribed vs C=$350–$252
- N=30 → $1,320 / $930 → 3.8–5.2× / 2.7–3.7×
- N=40 → $1,760 / $1,240 → 5.0–7.0× / 3.5–4.9×
  (caprank top-40 total commit p50 = $1,279, p90 = $1,708 — same answer from real lists.)

**Structural consequence:** at current sizing, capital funds only ~6–11 markets no matter
what D1/D6 do to the footprint. The "all 40 slots survive" counterfactual survives
*selection* but not *funding* — the D1/D6 payoff at unchanged sizing is a better-CHOSEN
~6–11 funded markets plus a long always-cut tail, not 40 quoted markets. Funding 40 markets
inside C=$252–350 needs per-market ≈ $6–9, i.e. MAX_MARKET_CAPITAL ≈ $12–17 (vs 45) or an
equivalent count cap. HYPOTHESIS on the right sizing number; the arithmetic is ESTABLISHED.

---

## 4. Churn verdict: STABLE, boundary-local — not a storm

**Replay of the live (ALLOC_KEY=0) cut on real pre-cut demand** (quotes rows, exact
`cap_desired` semantics: stable sort by static `usd_day`, tail-cut `break` at C=daily
equity p50):
- 07-30 (C=$318, 1,895 consecutive-cycle pairs): kept-status flips per pair mean **0.065**,
  p50=0, p90=0; only 116/1,895 pairs (6%) had ≥1 flip — while cutting p50 6 markets/cycle.
- Baseline selection churn (pre-cut quoted-set membership change, no cap): mean 0.63/pair
  (07-30), 0.69/pair (08-01). **The cut added ~10× LESS churn than footprint rotation
  itself.** ESTABLISHED (replay on logged data).
- Why: the ALLOC_KEY=0 key is static — 0 of 231 tickers changed `usd_day` within the window
  (ESTABLISHED); within-cycle tie mass is high (p50 44% of quoted markets share a usd_day
  with another) but stable sort + sticky insertion order (SCORE_RANK with
  INCUMBENCY_BONUS=0.25) keeps tie order fixed.

**Simulation of the STAGED (ALLOC_KEY=1) key** (caprank env variant, 11,454
consecutive-cycle pairs, cycle gap p50 11s / p90 60s):
- kept-status flips per pair: mean **0.27** (C=$252) / **0.30** (C=$350), p50=0, p90=1;
  16–17% of pairs have ≥1 flip. Total 3,104 flips / 11,457 pairs at C=$252.
- Rank displacement of a ticker between consecutive cycles: p50 = 0.0 positions,
  p90 = 0.92 — the cap_score ordering is nearly frozen cycle-to-cycle.
- Flips are **boundary-local**: flip rank p10/p50/p90 = 3/5/9, i.e. exactly at the arming
  index (funded p50 6) — the deep tail never flips. And concentrated: the top-8 flippiest
  tickers carry 31% (965/3,104) of all flips.
- Cost bound (INFERRED): at ~7,000 cycles/day, mean 0.27 flips/pair ≈ ~1,900 flips/day;
  a flip costs ≤2 cancels + ≤2 creates → ≤~7,600 ops/day worst case ≈ the 07-29 real load
  (9,974 ops/day) which the system absorbed; per-cycle it is ≪ WRITE_BUDGET=60. The real
  cost is queue-position loss on the ~5–8 boundary markets, a few times a day each — not
  continuous churn.

**Caveats on the ALLOC_KEY=1 sim (INFERRED, not ESTABLISHED):** the caprank env variant
scores the FULL candidate list with the offline prospective file, while live
`_alloc_priority` scores only the selected footprint and merges the live sweeper's fresher
pcap (age-cutoff 6h); calib=1.0 in both. Direction of error unknown but bounded by the same
score inputs; the rank-stability conclusion is robust to it. Also `commit_usd` is the model,
not realized notional (~30% low).

**Answer to the blocking unknown:** the total-capital cut arming at larger footprints is
not, on this evidence, a churn engine. It fired for 3 days at p50 4–6 markets cut per cycle
with cancels p90 = 2/cycle. Under the staged cap_score key it oscillates slightly more
(0.27 vs 0.065 flips/pair) but stays boundary-local and budget-trivial. The known
structural gaps remain: (a) no incumbency protection at the cut (ALLOC_INCUMBENT_FIRST
built+dark), (b) no hysteresis (drop-grace explicitly excludes cap-cut tickers), (c)
tail-cut `break` denies small markets that would fit after one large breach.

---

## 5. D6 sizing: what multiplicity would absorb, and whether the family cap bounds it

From the caprank env top-40 (what the rank WOULD pick with no one-per-series rule),
n=11,458 cycles:
- Distinct families in the top-40: p10/p50/p90 = 7/**9**/12 — the rank concentrates hard.
- Top families by desired sibling count and dollars (sib/cycle p50, family $ p50 / p90):
  - KXJOINCLUB: 19 siblings, $408 / $606
  - KXEURUSDAW: 9, $338 / $560
  - KXAAAGASM: 8, $284 / $697
  - KXCHINAAI: 5, $172 / $336 · KXUSGDPSHARE: 5, $185 / $223 · KXAPRPOTUS: 6, $193 / $231
  (KXRAIN did not reach the env top-40 in this window; the operator's $4,000/day 40-program
  family example is of this same shape — one $100/day slot today under round-robin.)
- **The family cap already bounds all of it.** `_series_cap() = min(SERIES_MAX_USD=100,
  SERIES_PCT 0.25 × equity)` = **$63.02 at equity $252.10** (ESTABLISHED, formula + logged
  equity). Every top family's desired multiplicity ($172–408) is cut to ~$63 — which at
  current per-sibling capital ($31–44) is **~1–2 siblings per family**. So D6 multiplicity
  under unchanged sizing buys roughly ONE extra sibling per top family; the $63 family
  budget, not the round-robin, becomes the binding constraint the moment multiplicity is
  allowed. The family cap's skip semantics (`continue`, dollars flow on) are already correct
  for this. Reaching e.g. 4–8 funded siblings in a $400/day family requires smaller
  per-sibling size (§3) and/or a larger SERIES_PCT — a sizing decision, not a selection one.

---

## 6. Design options (unordered — operator decides)

Orthogonal to all options, two dark switches already built for exactly the frictions
measured here: `ALLOC_INCUMBENT_FIRST=1` (incumbents outrank entrants inside `cap_desired`
→ suppresses the residual boundary flips at the cost of slower rotation into better
markets) and the tail-cut→best-fit question (change `break` to `continue` — small markets
fill the residual budget; behavior change, needs its own review).

### Option A — D1 complete pre-filter + keep selection and cut as-is
Fix only the unchecked-tail append (the `_kept.extend(rows[_ri:])` at :1406 that re-admits
~3,300 unpriced rows): price the close_time of everything that can reach a slot (bigger
budget, persistent cache, or filter-at-fetch).
- Pros: smallest change; footprint fills with 40 *quotable* markets; the funded ~6–11 are
  then chosen from a 40-deep checked pool instead of ~12 (pure quality win); churn regime
  already measured safe (§4); D6 untouched (round-robin still 1/series).
- Cons: 29–34 of 40 selected markets are permanently cut every cycle — selection work and
  book reads spent on markets that can never fund; the cut becomes the de-facto allocator
  (with its no-hysteresis, no-incumbency, tail-cut semantics); D6 families still get 1 slot.

### Option B — D1 + PIVOT_SELECT (built, dark) for D6 multiplicity, family cap as the bound
Enable the existing density branch (coverage floor per series, remainder by
priority/near-money; PER_SERIES_CAP still binds) — with ALLOC_KEY=1 both branches already
rotate on the unified cap_score key.
- Pros: D6 fixed with code that already exists and has tests (test_pivot_select.py);
  multiplicity is automatically dollar-bounded at ~$63/family by SERIES_PCT=0.25 (§5) —
  the "sibling takeover" failure is pre-capped; over-selection (pool > FOOTPRINT_TOP) feeds
  the quote loop past gate-outs.
- Cons: concentration rises by design (rank top-40 is 9 families p50) — reward-source
  diversity drops; at unchanged sizing multiplicity ≈ +1 sibling/family only, so the win is
  small until sizing shrinks; the cut still arbitrates the boundary (same residual churn);
  PIVOT parameters (POOL_MULT, COVERAGE) are untuned live.

### Option C — D1 + capital-aware selection (select-to-budget), cut demoted to backstop
Move the capital constraint INTO selection: walk the (cap_score-ordered) candidate list
accumulating `est_commit_usd` (already computed per row by `kalshi_capital_rank`) and stop
selecting near `_total_cap()` (plus a small overshoot margin for gate-outs); optionally with
per-family budget = `_series_cap()` for D6. `cap_desired` stays as an unchanged safety net
that should then almost never fire.
- Pros: footprint = fundable set by construction — no permanent 30-market cut tail, book
  reads spent only on markets that can hold dollars; boundary decisions inherit selection's
  existing stickiness (INCUMBENCY_BONUS, explore quota, drop grace) instead of the
  hysteresis-free cut; makes footprint size self-adjusting to equity.
- Cons: largest change, in the most safety-reviewed function of the file; commit model
  under-reads real demand ~30% (§3) so the margin needs calibration; interacts with the
  explore quota (probe-sized, cheap — needs an explicit carve-out) and with drop-grace
  retained books (not in footprint rows); needs its own D10-style observability to prove
  it didn't silently shrink the universe.

Sizing sub-decision common to all three (named, not chosen): keep MAX_MARKET_CAPITAL=45 →
~6–11 funded markets; shrink toward $12–17 → 20–40 funded but each seat earns a smaller
reward share and pays proportionally more churn per dollar. The reward-share curve
(share of qualifying depth → credit) is the calibration receipts should price before this
knob moves.

---

*Study artifacts (local scratchpad only, not committed): extract/replay/analyze scripts and
the plans/quotes/caprank extracts under the session scratchpad `d1d6/`.*
