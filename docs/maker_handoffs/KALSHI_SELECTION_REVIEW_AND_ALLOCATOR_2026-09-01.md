# KALSHI SELECTION REVIEW + NEW ALLOCATOR PROPOSAL — 2026-09-01 (~14:0xZ)

Operator ask (this date): review the selection process we have; review all facts on how we
make money net-profit-wise; review all options; propose a NEW selection-process tool and
scrap the old one; ask all questions needed.

**Nothing here is executed. Bot is OFF (inactive+disabled, verified 13:49:58Z). Every
change line below is an operator approval item.**

---

## §1 THE SELECTION PROCESS WE HAVE (from code @ HEAD `d94e35d`; repo blob md5 `a039f749`
= deployed quoter md5, verified this session)

Two layers pick what we quote:

**Layer 1 — `select_footprint` (maker_kalshi_quoter.py:2229): which markets get the 5 slots.**
Funnel, in order: liquidity-type + null-field drops → SERIES_ALLOW (12 series, incl. the 6
state gas dailies applied 13:45:28Z) → SERIES_DENY prefixes → date parse → late-life entry
cutoff → MAX_DAYS_TO_CLOSE=8 (paying-program exemption) → market-clock + close-past checks →
vol24h>1000ct activity gate (allowlist-exempt) → **rank** → **slots**.
- **Rank key** = `usd_day` pool, overridden by SCORE_RANK=1 (`kalshi_market_scores.rank`):
  "capture = share × pool" **computed from the book by the replica scoring model**. Its own
  docstring: the prospective model "over-predicts 2-6x" (quoter :169); measured on the class
  that actually pays us: **43x over** (T5.60: model $18.30/d vs measured $0.178/d, money plan
  §1). UPTIME_RANK=0 (census blind for the paying class — ruled).
- **Slots**: PIVOT_SELECT=1 density pool (10× over-select), PIVOT_COVERAGE=3 per-series floor,
  near-money-first in-series ordering, ALLOC_KEY rank-keyed series rotation, PER_SERIES_CAP=3,
  FOOTPRINT_TOP=5.

**Layer 2 — `desired_quotes` (:3064): per-cycle gates on each slotted market.**
Safety (keep-class): bad clock → wind-down (reduce-only) → crossed book → one-sided
(reduce-only; ANCHOR path S4-gated to Target — dead at Target-1000 since INV_HARD_CT=100) →
entry band → MIN_RUNWAY_H=0 (off, ruled) → MID_BAND_OUT 0.30,0.70 with WIDEBOOK admission
(spread ≥5 ticks, ruled) → QUALIFIABLE_GATE=1 (cum depth + our addable ≥ Target both sides;
addable clamped by INV_HARD_CT=100, MAX_ACTIVATE_CAPITAL=$60) → CAPTURE_GATE=1 (model
≥$1/day) → wide/asym (MAX_SPREAD 8 unless widebook; MIN_DEPTH_SYM 0.25).
Sizing: JOIN_SIZE=100, D3 time-ramp 5,10,25,40,100 (KEEP_S=21600, new-series clamp OFF),
INV 30/100, caps $100/mkt $290 total, halt $10/d, reentry-cool 3600s.

**THE CENTRAL FINDING (ESTABLISHED, from code read this session):** the live stack contains
**zero receipts/accrual feedback**. `D2_FEEDBACK` (receipts→rank multiplier, quoter :491)
ships OFF; the W7 receipt clamp is disabled (D3_NEWSERIES_MAX_RUNG=-1, Option A 08-21);
OBS_HOLD=0. The per-user est-feed — the venue telling us our own accrued dollars per program,
recorded every 5 min since 08-06 — is consumed by exactly one live path: the runway re-entry
exemption (:3227). **The picker ranks on pool size and a model proven 2-43x optimistic, and
never on what we were actually paid or actually accrued.** Selection quality was never the
design target of this chain — it accreted as incident patches (every gate above cites its
incident) while ranking stayed proxy-driven.

What the old stack does RIGHT (keep-class, not scrapped): reduce-only doctrine everywhere,
crossed/one-sided refusals, caps/halt/ramp, loud drop-reason telemetry (FP_DROPS), the
allowlist boundary, and the R3-correct qualification math in `_prospective_capture`.

---

## §2 THE MONEY FACTS (each labeled; sources inline)

**Revenue mechanics (ESTABLISHED — R3 rules canon = CFTC filing read in full 08-25):**
- R1. Reward = your score-share × pool. Score = DF^(ticks below best-bid ref) × size,
  pro-rata within level (no time priority). Snapshot counts ONLY if cum depth ≥ Target on
  BOTH sides. Best-bid at 0.99 → side unqualifying → snapshot excluded ($0 for everyone).
- R2. **$1.00/program-period floor, else $0** (cliff canon 08-18: 38/38 events backtested
  exact; sub-$1 pays $0 — 30+ obs; above-cliff pays accrued ~1.0). Design law: enter only
  what is sized to clear ≥$1.50/period.
- R3. Payment is score-share, NOT time-proportional (W10: $12.94 for 2.4 min of presence on
  an empty scoreable book). Linear in size (filing formula; the one licensed extrapolation).
- R4. Pools in our allowlist family right now: **166 programs, $17,520/day** (09-01 13:40:18Z
  enumeration: 119 gas dailies ×$100 incl. 6 state series, 21 gas-wkly ×$100, 21 diesel
  ×$120, 5 topmodel ×$200). 80/104 pulled books Target-qualified both sides; **71/90 daily-gas
  books qualify** (13:02-13:10Z walks). Dailies settle nightly 04:00Z → receipts in ~24-48h.

**Our measured earn rates (ESTABLISHED, est-feed timeline 08-31T13:20→09-01T13:11Z):**
- M1. At ≤40ct, presence 0.40/0.40/0.16: DIESELW T5.60 **$0.178/d**, T5.58 $0.114/d,
  T5.62 $0.068/d; CLAU5 $0.008/d (presence 0.03); AAAGASW-4.120 $0.020/d (0.04).
  Sum ≈ **$0.39/day at ≤40ct** vs $17,520/day family pools — capture ~0.002% of family pool.
- M2. The instant-book share model over-predicts this class **~43x** at matched size (money
  plan §1). Ceiling only; NEVER an expectation. Only measured-anchored linear-in-size
  projections are licensed.
- M3. Presence was broken by three measured gate mechanics (band-edge flap at 0.90, widebook
  20-tick demand vs real 2-15c spreads, MIN_RUNWAY 49h vs 16h daily life) — all three
  addressed by the applied env values (ruled 09-01, applied 13:45:28Z).

**Receipts truth (ESTABLISHED, credit_history 13:18:13Z):**
- C1. Lifetime credited **$205.06** (63 rows). **$0.00 credited in the 16 days since
  08-16T06:55Z.** Best month-window on record: Aug UI $57.33 = credit_history exact
  (08-21 audit §5).
- C2. Pending accruals pay 09-06/09-07T04:00Z ONLY if a program crosses $1.00; current rows
  T5.60 $0.1760 / T5.58 $0.1134 / T5.62 $0.0679 are frozen while the bot is off (feed
  verified frozen 13:31→13:46:52Z this session).

**Cost side (ESTABLISHED unless noted):**
- K1. All-in realized settlement cost **$0.03064/ct** (70 settlements, 4,522ct, 07-26 study);
  hedged $0.02248/ct vs naked $0.13645/ct — **6.1× — pairedness is THE cost lever; sit at
  the touch** (6/6 configs).
- K2. D3-era diesel fill cost −2..−3.5c/ct (F14 budget, pre-registered).
- K3. Current-shape empirical: **0 adverse fills** at 33-40ct at-touch extreme books over the
  08-31→09-01 night; balance moved only +$0.04 (settlement). 1-night sample.
- K4. Loss history per RULE SEVEN: on the −$122.57 basis, ~61-77% agent defects; structural
  maker cost −$28.68 vs $88.07 rewards then earned → **reward-positive, defect-negative**.
  Never build gates over defect-era receipts (netev canon 08-03).
- K5. Tail: committed dollars are the worst case (all deep fills settle $0). 3-mkt/100ct
  footprint ≈ −$230 (money plan §2). Realized churn is halt-bounded $10/d; settlement of held
  inventory is NOT halt-bounded.

**The net-profit equation these facts force:**
NET = Σ_programs (accrued ≥ $1.00 ? accrued : 0) − fill costs − naked-settlement tails.
Every term says CONCENTRATE: N programs at $0.50 pay $0 where N/2 at $1.00+ pay everything
(R2); pairedness controls cost (K1); share is linear in size (R3) so splitting capital thinner
than a cliff is pure waste. And the binding scarcity at $314.57 is CAPITAL RESERVATION
(~$95/market at 100ct both-sides), not pool supply (R4).

---

## §3 OPTIONS (all of them; pros/cons; no numbers beyond cited anchors)

**A. GO with the applied config as-is** (already ruled; awaiting GO).
Pro: zero further change; T5.60/T5.58 resume accruing toward the 09-06 cliff (at 100ct,
linear from M1 ≈ 2.5× the 40ct rates → money plan §2 projects ~$3.6-13 credited 09-06,
INFERRED); dailies now admitted → first nightly receipts within ~24-48h of GO. Con: the
picker choosing the 5 slots is the §1 proxy stack — with 119 dailies newly eligible, WHICH
5 it picks is model-driven and unmeasured; every day dark also shrinks the 09-06 receipt
(T5.60 needs ~4 more accruing days at 40ct pace to clear $1; fewer at 100ct).

**B. Hand-pinned footprint** (money plan Opt-1a/1b). Pro: deterministic receipt machine on
the measured accruers. Con: operator style ruling 09-01 explicitly disfavors hand-pinned
footprints (bot-wide fixes preferred); doesn't fix the picker for the next period.

**C. NEW SELECTION TOOL — "the allocator" (§4). THE PROPOSAL.** Pro: selection finally keyed
on measured accrual + receipts + the $1 cliff; class-complete coverage report (every family
pool dollar bucketed); scrap of the proxy rank per operator direction. Con: build+validate
time (offline backtest exists day-1 from the est-feed tape); needs ~8 decisions below.

**D. Cheap-side Target supply ("activate concentration") — STUDY ASK, separate approval.**
R3 math: a snapshot pays only if BOTH sides cum ≥ Target (1000ct on our programs). On the
paying extreme books the rich side already qualifies (cums 2-2.9k, money plan §1); the CHEAP
side (1-9c) is what fails. Supplying the missing cheap-side depth ourselves costs $10-90
reserved per 1000ct and makes us ~the whole cheap-side qualifying score (pro-rata, R1).
Pro: potentially the largest share lever available at our capital; risk bounded at basis
($10-90/market worst case); S4 anchor logic already models the qualification math. Con:
requires INV_HARD_CT ≥ Target for those books + MAX_ACTIVATE_CAPITAL raise = a NEW held-
inventory risk shape (R1-probe measured all-5-settlements-against-us at small size, K4-class
tail); expected $ UNMEASURED — needs a 1-2 market bounded probe before any number exists.
Rule Thirteen: measurement first; no projection offered.

**E. Daily-gas breadth carpet.** 71/90 dailies qualify now at $100/d each (R4); nightly
receipt cadence = 7× faster learning than weeklies. Pro: receipts-per-week maximized;
exactly the class the applied env just admitted. Con: OUR share on dailies is UNMEASURED
(presence was 0% — M3); at ~$95/market reserved, $314 covers ~3 markets at 100ct both-sides
(fewer than the 5 slots) unless D-shape reduces per-market cost. First nights are the
measurement.

**F. $3k scale path.** RULED NO for now (operator, 09-01). Listed, not proposed. Unlocks per
the pre-registered 09-06 gate (≥$3 credited → sizing is arithmetic).

**G. Census full-book fix** (open item, operator-gated, read-only recorder change). Restores
a true qualifying-uptime input for the allocator's presence math. Currently census reads 0.0%
on the venue-paying class (d4 keeps only 3 ticks off touch).

**H. Stay dark.** $0 risk, $0 revenue; the frozen sub-$1 accruals (C2) then pay $0 at period
end by rule. This is the default until GO — listed so the cost of each dark day is explicit.

---

## §4 THE PROPOSED TOOL — `kalshi_allocator.py` (scrap-and-replace for Layer-1 selection)

**Principle: allocate capital to maximize projected CREDITED dollars per period, computed
from MEASURED accrual (est-feed), with the $1 cliff explicit. Model picks probes; only
measurement earns size.**

Nightly run (and on demand), fully offline, no order authority:

1. **UNIVERSE**: active allowlist-family programs (`incentive_programs` read) + full-book
   walk per candidate → Target-qualification both sides (the 13:02Z method, not the blind
   census).
2. **MEASURE**: per-program earn-rate $/day from OUR est-feed tape (5-min rows since 08-06;
   program→ticker via the recorder's map) at the size/presence we ran. Receipts
   (credit_history) close the loop at period ends; fill costs per market from the cash/fills
   tape (F14 budgets).
3. **CLIFF MATH**: projected credited = accrued + measured-rate(size) × time-left, per
   program-period. **≥$1.50 projected → eligible for real size** (cliff canon design law);
   linear-in-size is the only extrapolation used (R3).
4. **ALLOCATE**: greedy under MAX_TOTAL_CAPITAL by projected-credited-$ per committed-$;
   concentration preferred (clear cliffs with margin before adding markets). UNMEASURED
   candidates (all dailies today) get probe-tier size only, from an operator-set nightly
   probe budget, ordered by the book model (its only job).
5. **OUTPUT**: (a) footprint file `{ticker: max_ct}` the quoter consumes; (b) coverage
   report bucketing EVERY family pool dollar: EARNING / PROBING / EXCLUDED(named reason) /
   UNKNOWN — the class-not-instance ledger, so an unearned pool dollar is always visible.
6. **QUOTER CHANGE (small, flag-gated)**: `KALSHI_FOOTPRINT_FILE` — when set,
   `select_footprint` returns the file's tickers (safety drops still apply: date-parse,
   close-past, late-life); per-ticker ct clamps ride the existing size path. Flag unset =
   byte-identical today. ALL Layer-2 safety gates unchanged.

**Validation before any live use**: backtest over the recorded est-feed tape (08-06→09-01,
includes the pivot window with real accruals): "which programs would this rule have sized,
and which cleared $1" — against the known credit_history answers. Then a pre-registered
forward week: footprint + committed + nightly credited-$, F14 budget-fail rule armed.

**What gets SCRAPPED (all flag-off, no code deleted; each line needs your YES — Rule Nine):**
- S1. SCORE_RANK=0 (capture-model ranking out of live selection).
- S2. PIVOT machinery bypassed whenever the footprint file drives (flag stays, code stays).
- S3. D2_FEEDBACK stays OFF (superseded by the allocator; it was never armed).
- S4. vol24h gate / probe-exception machinery: unchanged (already no-op for allowlist picks).
**KEPT as belts (recommended)**: QUALIFIABLE_GATE, CAPTURE_GATE, MID_BAND+WIDEBOOK (ruled
values), all reduce-only/caps/halt/ramp. They can only refuse, never add.

---

## §5 DECISION SHEET (answer any subset; numbers reference above)

1. **Build the allocator (Option C) as specced?** (yes/no/modify)
2. **GO-now question, independent of the build**: relight the applied config while the
   allocator is built (Option A), or stay dark until the tool drives? Every dark day shrinks
   the 09-06 receipt (C2/M1); the build is days, the receipt window closes 09-06T04:00Z.
3. **Quoter consumption shape**: footprint file + tiny flag-gated select_footprint bypass
   (recommended, surgical) — approve that one code change (full ship discipline: tests,
   pins, review)?
4. **Cliff margin**: confirm ≥$1.50 projected (cliff canon's number) as the real-size bar.
5. **Probe budget**: how many $ reserved per night for UNMEASURED candidates at probe size
   (they are the dailies well, E; probes are for-profit under the cliff math, not data
   trades)? Name a number ($20-40 fits inside MAX_TOTAL 290 alongside 2-3 sized markets).
6. **Scrap list S1-S4**: approve as listed (flag-off only)?
7. **Option D study** (cheap-side Target supply): approve a bounded 1-2 market probe design
   doc first (no live change; sizing/INV_HARD decisions would come back as their own sheet)?
8. **Census full-book fix (G)**: approve the read-only recorder change now (feeds allocator
   presence math), or defer?
9. **Cadence**: nightly allocator run ~06:45Z + 07:30Z credit read reporting (single absolute
   credited-$ number, per standing ruling) — with the first N days operator-confirmed before
   the footprint file auto-applies? Name N (0 = auto from day 1).

Open items carried unchanged (not demoted): TOPMODEL sibling books never pulled (CLAU7/F/M/T);
tight near-money dailies (1-4c spread, mid 0.3-0.7) excluded by design under approved values;
$3k path per its 09-06 gate; est-feed freeze watch; daily credited-$ reporting duty at GO.
