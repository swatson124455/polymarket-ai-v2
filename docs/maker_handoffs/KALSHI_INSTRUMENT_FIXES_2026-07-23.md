# KALSHI MAKER — INSTRUMENT FIXES 2026-07-23

**Branch:** `claude/maker-kalshi-live` · **Nothing deployed.** The state freeze
(`KALSHI_FREEZE_2026-07-23T0219Z.md`) held for **code**; see §7 for the config breach.

Four fix lanes ran, each was adversarially verified by a separate agent, and this document
records the **verifier's** measured numbers, not the fixer's claims. Where the two disagree,
the disagreement is stated in the open.

---

## 0. INTEGRATION EVIDENCE (measured by the integrator, this session)

| check | result |
|---|---|
| `cd kalshi_live && python -m pytest -q` | **159 passed, 2 xfailed** |
| `python -m pytest test_live_hardening.py -q` (the ≥83 baseline gate) | **93 passed, 2 xfailed** |
| `python dryrun_smoke.py` | `PROBLEMS: NONE — dry_run intact`; tripwires `_flatten_all/_held_cost/get_positions/get_balance` all **0**; cycle 2 quiesces at 0 churn |
| deployed `maker_kalshi_quoter.py` md5 | **`727ca7c59840a42b51c19e24c65a0982`** — exact, unchanged |
| deployed `maker_kalshi_client.py` / `kalshi_attribution_ledger.py` / `kalshi_ab_plugin_report.py` / `flatten_kalshi.py` | `3599d513…` / `67363bdd…` / `f5de2b82…` / `b422eda5…` — all exact vs freeze baseline |
| STOP sentinel | absent |
| `concentration_samples.jsonl` md5 | **`e920bf99850279099897a79e8ad78dec`** — unmodified |
| `kalshi_transactions_2026-07-23.csv` md5 | `7d6cd448ce89fd53ee6263c7f41a9ca9` — unmodified |
| `git status --porcelain` (tracked) | **clean** — no uncommitted lane work |

The 2 xfails are `strict=True` and are **deliberate encoded findings**, not flake — see §5.

### Commits (one per lane, not squashed)

| sha | lane | files |
|---|---|---|
| `80315af` | studies | `kalshi_concentration_study.py`, `kalshi_series_scan.py`, `test_studies.py` |
| `5cb3fd9` | quoter | `maker_kalshi_quoter.py`, `test_live_hardening.py` |
| `72c01f3` | attribution-ledger | `kalshi_attribution_ledger.py`, `test_attribution.py` |
| `49f60bc` | settlement-pnl | `kalshi_settlement_pnl.py` (new), `test_settlement_pnl.py` (new) |

**All four lanes returned work and all four committed it. No lane failed or returned nothing.**

### Untracked files — deliberately NOT committed

17 untracked files exist in `kalshi_live/` (`gasm_*`, `kalshi_twosided_*`,
`kalshi_unbiased_sample.py`, `unbiased_samples.jsonl`, `series_fee_types.json`). All have
mtimes of **08:42–09:12 local**, i.e. they predate every fix commit (15:03–15:10Z) — they are a
**prior session's** study artifacts, not stray output from these lanes. Left untracked.

One of them matters and is tracked as open task #8: **`series_fee_types.json` is untracked but
`kalshi_series_scan.py:83` reads it.** Two fee pins call `_need_fee_table()` and
`pytest.skip()` when it is absent — measured on a reconstructed fresh clone: **19 passed, 2
SKIPPED**. The skipped pair contains the only assertion that `KXAAAGASM` **CHARGES** maker fees
— by live measurement the *single* series out of 162 carrying active LIP programs that does, and
it sits in the same `KXAAAGAS*` family as our allowlisted `KXAAAGASD`/`KXAAAGASW`. With the file
missing, `fee_status()` falls back to `GET /series/{ticker}` whose except-branch returns
**UNKNOWN** — a silent fail-open, with the test that would catch it already self-skipped. I did
not commit it because there is **no committed generator** for it, so it cannot be regenerated or
audited; committing 1.28 MB of unverifiable venue metadata as canon is the wrong trade. Fix
properly: commit a generator, regenerate, commit the output.

---

## 1. DEFECT — rewards attribution: fill cash flow sign-inverted AND position-blind

**Lane:** attribution-ledger · **Commit:** `72c01f3` · **Verdict: ROOT**

**Symptom.** `rewards_residual` — the number the whole rewards-basis thesis is measured in —
was contaminated by a mis-signed trading cash flow. Every ledger snapshot's residual was wrong
by the size of that interval's `ask` fills.

**Root cause.** `kalshi_attribution_ledger.py` (pre-fix `:109` region, blob md5
`67363bdd4b02a6edd99fe168923dab30` — *this is still what is deployed*): `fill_cashflow` keyed on
the legacy `action`/`side` pair. Every live fill carries exactly one of two shapes (317/317 on
the pull, re-confirmed 318/318): `bid`/`yes` → legacy `buy`/`yes` (161), and `ask`/`no` → legacy
**`sell`/`no`** (156). An `ask` is **our NO bid** (`maker_kalshi_client.py:151-158`
`create_order_v2`, the only order-creation path, takes `book_side ∈ {bid, ask}`), so "sell no" is
us **buying NO** = cash **OUT**. The ledger booked it as cash **IN** — 156 of 317 fills wrong-signed.

**The reported fix was only half right, and the verifier confirmed it.** A plain sign flip is
*worse* than the bug. Kalshi has no naked short and **nets per market**: an `ask` fill against a
long-YES position closes pairs, and each released pair pays $1, so the offsetting quantity is a
genuine **inflow of (1 − price)**. Measured over the live 07-22→23 window (27 ledger intervals,
168 fills):

| model | fills_cash | vs balance-implied |
|---|---|---|
| old (shipped, still deployed) | −$86.54 | **$25.63 wrong** |
| naive sign flip | −$741.15 | **$680 wrong** |
| **position-aware (the fix)** | **−$60.91** | **exact, all 27 intervals** |

**Fix.** `kalshi_attribution_ledger.py` — `:140 fill_outcome` reads `book_side` (`outcome_side`
fallback) and **raises** on any unverified shape; `:155 fill_count`; `:159 fill_price`;
`:176 fill_fee`; `:187 fill_position_delta`; **`:193 fill_cashflow(f, position_before)` — the
signature WAS the bug**, `position_before` is deliberately required so a stale 1-arg call raises
rather than silently reproducing the −$741 model; `:215 replay_fills(fills)` chronological
replay of the full tape; `:373 position_recon_mismatch` two-way check vs `position_fp`.

**Independent receipt (verifier, not the fixer's word).** The committed
`kalshi_transactions_2026-07-23.csv` shows the venue itself booking fill `d4` as **CLOSING**
11.00+6.00 = 17 YES at `exit_price_dollars` 0.1500 → proceeds **+$2.55**. That kills the shipped
model (+$14.45) *and* the naive flip (−$14.45) from a venue receipt. The verifier also
recomputed the CSV independently: `DCH realized_pnl_with_fees` = **+8.290** exactly; open fees
$0.0056 + close fees $2.5767 = **$2.5823**, corroborating "`fee_cost` is DOLLARS".

**Residual validation.** Corrected residual is **exactly $0.0000 in 24 of 27 intervals**. The
three non-zero: `07-22T06:43Z` +$4.3500 = credits $1.88 + $2.47 to the cent; `07-22T07:43Z`
+$2.2300 = credit $2.23 to the cent; `07-22T19:34Z` +$19.6000 = the operator's **+$20 deposit**,
not rewards.

**MEASURED PIN COUNT (verifier): 19 of 20 fail on pre-fix — but all 19 fail on API surface**
(13 `AttributeError`, 6 `TypeError`), zero on a value assertion against the old signature. The
fixer disclosed this. The verifier therefore ran **mutation testing** — 6 wrong implementations
built against the *same new API*, all killed: naive-sign-flip-ignores-position **7 killed**;
legacy-convention-in-new-API **9**; offset-sign-inverted **7**; offset-pays-price-not-pair-release
**7**; fee-not-subtracted **2**; all-or-nothing-offset **1**. So the pins **are** behavioural.
The verifier discounts one further non-pin the fixer did not:
`test_attribution.py:285 test_residual_equals_the_credit_end_to_end` is algebraically
tautological. **Honest pin count on the cash model: 18 of 20.**

**STILL UNFIXED (5 items, verifier-found):**
1. **MIXED-MODEL LEDGER FILE — the highest-severity open item in this document.** The row dict
   (`:363-378`) gained `fills_fees` and `position_recon_mismatch` but **no model/version field**.
   Confirmed by the integrator by direct read. After a deploy, `ledger-202607.jsonl` accumulates
   rows written under two incompatible cash models with nothing to tell them apart, and
   `report()` (`:394-418`) sums `fills_cash` and `rewards_residual` across every row in the
   window. The deployed `/opt/pa2-maker-kalshi-live/ledger-202607.jsonl` is **16,008 bytes of
   old-model rows** (verified on box). The first `--report` after deploy emits a blended rewards
   figure that looks authoritative and is wrong — *the exact failure class this fix was written
   to kill, resurrected at the reporting layer.*
2. **`position_recon_mismatch` gates nothing.** `collect()` prints a loud WARNING (`:328-331`)
   and stores the field; `report()` sums `rewards_residual` **unconditionally** (integrator
   confirmed by read). A row the code itself declares untrustworthy still lands in the reported
   total.
3. **Fail-closed severity understated.** `fill_outcome` raises and `replay_fills` runs the FULL
   historical tape every run, so **one** unrecognised historical fill bricks the hourly ledger
   timer on every subsequent run, permanently. Pre-fix degraded to a wrong number; post-fix
   produces no row at all.
4. `fill_fee` dropped the old cents-scale guard (`if fee > 1000: fee/100`). Receipt-backed as
   dollars, but there is now **no unit guard at all**.
5. Minor, **proven immaterial**: `replay_fills` sorts `created_time` as a **string** and the live
   tape carries variable-length fractional seconds, so sub-second order can invert (verifier
   reproduced it). Cannot change the total — `cash = −Σ(price·count) + Σ(offset)` and `Σ(offset)`
   is order-invariant — it only shifts cash between reporting intervals.

---

## 2. DEFECT — settlement P&L: the −$442 reading

**Lane:** settlement-pnl · **Commit:** `49f60bc` (2 NEW files, 0 deletions, no existing file
touched) · **Verdict: ROOT**

**Symptom.** A **−$442 settlement** was published against an **$85 account** — impossible on its
face, and one of the three impossible numbers this whole session was convened over.

**Root cause.** Not a code line — a **misread of the venue schema**. The settlement row's
`yes_count_fp` / `no_count_fp` / `*_total_cost_dollars` are **cumulative LIFETIME GROSS**, not
net. Subtracting cumulative cost without crediting the matched pairs double-counts every pair
that YES/NO auto-netting already released. Correct model:
`P&L = revenue/100 + min(Y,N) − Ycost − Ncost − fee`, where `min(Y,N)` is provably the lifetime
pair count because YES and NO auto-net. `revenue` is **cents**, not dollars.

**Fix.** New standalone module `kalshi_settlement_pnl.py` (471 lines): `:141
expected_revenue_dollars`, `:203 fill_signed_qty`, `:352 crossvalidate_csv`, and — critically —
`settlement_leg_pnl` and `settlement_row_pnl` as **separate functions so the two can never be
conflated again**.

**Verifier re-derived all of it from first principles in an independent script (no import of the
module):**

| result | measured |
|---|---|
| revenue reconciliation | **51/51 exact**, zero unexplained rows |
| CSV agreement | **47/47 exact** on the comparable set |
| total | **−$87.5946** |
| `KXAAAGASD-26JUL23` event lifetime | **−$7.4656** (vs naive **−$442.1356**) |
| carried basis / settlement leg / realised earlier | 8.4932 / **−8.2032** / +0.7376 |

The CSV cutoff is **not cherry-picked**: verifier computed `max(close_timestamp)` over the CSV
trade rows = `2026-07-23T03:38:05Z`, exactly the hardcoded `CSV_EXPORT_CUTOFF_UTC`. The 4
excluded rows are the export being incomplete (the GASD event settled at 12:25:36Z), excluded
**by timestamp before any P&L is compared** — a naive all-51 comparison would read 47/51 = 92.2%.

**The −$8.20 is REPRODUCED, not refuted — it is a different quantity.** Settlement-leg P&L
(revenue − carried basis) = −$8.2032; event *lifetime* P&L = −$7.4656. Both are right.

**MEASURED PIN COUNT (verifier): 20 of 24**, by rebuilding 4 source-mutated pre-fix copies and
running the verbatim test file against each — naive-cumulative-cost **12/24**, sell-adds-YES
**9/24**, revenue-as-dollars **13/24**, counts-already-net **8/24**. Identical to the claim
(12/9/13/8), union exactly 20. **Caveat the fixer did not make:** 3 of the 20 are meta-tests that
open with `assert _count_failures(...) == 0` and therefore fail under *any* mutation —
tautological amplifiers. **Strict substantive pin count: 17 of the 21 non-meta tests.**

**Disagreements with the fixer, reported as the verifier found them:**
- The fixer's **"Suite: 117 passed"** was a two-file subset, **not** a full-suite result. It was
  stated as if full. (Harmless — the full suite passes; the claim was overstated.)
- The fixer's **flake claim is NOT reproduced.** `test_ladder_invariants_flagged_live` was run
  6 consecutive times: 7 passed every time, zero TypeErrors. Treat "that test is flaky" as
  **unverified** — do not carry it forward as fact.
- The fixer's `51/51 two-model agreement`, `6/6 venue realized_pnl` and
  `fee_cost == SUM(fill.fee_cost) 51/51` headlines are **live-run assertions with no committed
  evidence**. The fixture carries a fill tape for only **4 of 51** settled contracts, only **3**
  (not 6) `VENUE_POSITIONS` rows, and every fixture fill has `fee=0.000000` — **the fee identity
  is unpinned entirely.**

**STILL UNFIXED:**
1. **The lane's own "NOT FIXED" hand-off paragraph is STALE AND WRONG, and it is dangerous.** It
   states that `kalshi_attribution_ledger.py:109 fill_cashflow` still books a sell as receiving
   `no_price*count`, a −$431.34 mis-booking. That is the **pre-`72c01f3`** state —
   and `72c01f3` is this very commit's **direct parent**. An agent acting on it would reverse a
   measured root fix on the live money-handling module, doing exactly what that file's own
   comment at `:118-125` forbids in terms. **That paragraph is killed here and must not
   propagate.**
2. `fill_signed_qty` (`:203-207`) discriminates on legacy `action == "buy"` with a **silent
   else-branch**, and `fill_yes_price` (`:193-200`) falls back to **0.0** — whereas the sibling
   ledger deliberately RAISES on an unverified shape. A genuine `action='sell', side='yes'` fill
   would be silently booked as acquiring NO at price 0.0.
3. `expected_revenue_dollars` returns 0.0 for any `market_result` not in {yes,no} and
   `settlement_row_pnl` has no **void** branch — a voided market surfaces as a false
   "UNEXPLAINED" row plus a wrong P&L rather than an error. No void rows exist in the 51.
4. 47 of 51 contracts are pinned only against a Model-A-vs-CSV comparison. If Model A is ever
   wrong in a way the CSV is also wrong about, nothing committed would catch it.

---

## 3. DEFECT — concentration/scan studies: D1 selection bias, D2 census ceiling, D3 degenerate ranking

**Lane:** studies · **Commit:** `80315af` · **Verdict: MIXED** (D1 root, D2 latent-ceiling,
D3 root; R3 left defined two ways — see below)

### D1 — selection bias (VERIFIED at source)

**Symptom.** The two-sided rate published off `concentration_samples.jsonl` was **conditional on
a filter nobody declared**, and read as if unconditional.

**Root cause.** `kalshi_concentration_study.py` pre-fix `:132-133`
`if not yl or not nl: continue` — the sampler **discarded one-sided books before writing**, so
the dataset can never contain the failure mode the rate is supposed to measure.

**Fix.** `:81` `KEEP_ONESIDED` (env `CONC_KEEP_ONESIDED`, default **ON**); `:148` the defect site
becomes `if (not yl or not nl) and not KEEP_ONESIDED`; `score_market` (`:171`, empty-side return
~`:194`) returns `0.0,0.0,0.0,0.0,"r3_empty"` instead of raising `ValueError` on `max()` of an
empty book; `:213 two_sided_stats()` applies **R3 as a Target Size test**, splitting failures into
`empty` vs `thin`; `:239 dataset_provenance()`; `:340` sampler stamps `keep_onesided` on every
snapshot.

**Verifier reconstructed pre-fix modules into a shadow tree and drove `sample_once()` with a fake
venue: pre-fix returns 1 of 2 rows (silently dropping the one-sided contract); post-fix returns
2.** The `max()` crash also confirmed (`ValueError: max() iterable argument is empty`, `:175`).

**Measured effect — and it is honest about being zero on the frozen data.** The frozen dataset
is byte-unchanged and now self-reports `304/353 = 86.1%` two-sided, **`one side EMPTY 0, below
Target 49`**. Zero empty-side rows across 353 snapshots **is the fingerprint of the pre-filter**.
The rate on this dataset **cannot** change — the dropped rows were never written. The fix changes
what *future* samples contain. Canon §M6b's unconditional comparator (**20.5%** at 03:14Z) is the
honest contrast. Additionally **49 of 353 are non-empty but below Target Size** — R3 failures the
`not yl or not nl` framing never counted at all.

### D2 — page ceiling (VERIFIED, **including its negative result**)

**Root cause.** `kalshi_series_scan.py` hardcoded `limit=1000` with no truncation warning.
**Fix:** `:93 PAGE_LIMIT` (env `SCAN_PAGE_LIMIT`, default 10000); `:187 fetch_programs()` warns
when the cursor is still open at the page cap instead of returning a short census as complete.

**D2 IS A LATENT-CEILING FIX AND IS *NOT* THE CAUSE OF ANY "KXRT = 0 PROGRAMS" READING.**
Verifier's own live read-only census: `limit=1000` → 3 pages, **2,316 programs / 161 series**,
KXRT=70, cursor closed; `limit=10000` → 1 page, **2,316 / 161**, KXRT=70. **Identical.** (The
fixer measured 2,298/160 — drift is program churn between reads; the load-bearing identity holds.)
The fixer stated this and **declined the credit** — correct, and recorded here so nobody
re-attributes a symptom to it later.

### D3 — degenerate ranking (VERIFIED, **stronger than claimed**)

**Root cause.** Selection took the head of the API response, so "top N" was a property of the
response order, not the data.
**Fix:** `:208 rank_programs()` tie-breaks on ticker; `:219 select_markets()` supports
`random` (seeded) / `census` / `head`; `:96` default `random`; `:274 main(top_n, per_series,
sample_mode=None, seed=None)` (positional signature preserved); `:162 fee_status()` with
`series_fee_types.json` + `GET /series/{ticker}` fallback, **unknown reported UNKNOWN, never
assumed free**.

Verifier live: **9 of the top 12** series by pool $/day have exactly **1 distinct ranking key**
(fixer said 8/12 — **under**-claimed). All four cited series reproduce to the decimal:
`KXEARNINGSMENTIONLMT` 13 programs / 1 key / 30.8% head-4 coverage; `KXFEDMENTION` 43 / 1 / 9.3%;
`KXTRUMPMENTION` 34 / 1 / 11.8%; `KXRT` 70 / 21 / 5.7%.

**Fee wiring verified exactly:** 162 series carrying active LIP programs → **161 FREE / 1 CHARGES
/ 0 UNKNOWN**, the one charging being **KXAAAGASM**. Reproduces canon §M10 independently of canon.

**MEASURED PIN COUNT (verifier): 22 new tests, 22/22 fail on pre-fix** — headline verified
exactly. Grading by failure mode, **measured**: **5 behavioural, 17 API-existence** (fixer
claimed 4/18 — the miss is in the honest direction). The 5 behavioural:
`test_d1_score_market_treats_empty_side_as_r3_failure` (ValueError),
`test_d1_score_snapshot_survives_one_sided_rows` (ValueError),
`test_d2_program_fetch_requests_10000_per_page` (AssertionError),
`test_d2_census_is_not_truncated` (**strongest pin — KXRT vanishes entirely pre-fix**),
`test_d3_sample_is_independent_of_api_order` (two disjoint "top 4" sets from one venue).
**On a clean checkout the pin count is 20, not 22** — 2 skip on the missing fee table (§0).

**Regression check — strongest evidence in the whole session:** verifier diffed the full
`--report` output on the frozen dataset pre-fix vs post-fix. The diff is **purely additive** —
exactly 3 lines added (provenance banner + R3 census line), **zero committed numbers changed**.
§M1/§M2 figures byte-preserved.

**STILL UNFIXED:**
1. **R3 is now defined TWO WAYS in one report** — this is why the lane grades MIXED.
   `two_sided_stats()` (`:213`) applies R3 as a **Target Size** test; `score_snapshot()`
   (`:288-294`) **re-inlines** that predicate instead of calling it; `score_market()` still uses a
   **non-empty** test. So the header prints "below Target 49" while the capture tables underneath
   pay out on those same 49. **The verifier quantified it and its own adversarial hypothesis was
   WRONG:** the 49 below-Target rows are 13.9% of rows but only **$3.07 of $4,097.09 = 0.1%** of
   scored payout, and **zero** clear the $1 MIN_PAYOUT threshold — floored capture and
   paying-market counts are **entirely unaffected**. **§M1 is NOT materially inflated.**
   Downgraded from "inflates §M1" to **"real mechanism, immaterial on this dataset"** — a
   synthetic book at depth 990 of Target 1000 is R3-unpayable yet scores $1.51, so it can bite
   elsewhere.
2. **Wording overclaim, NOT disclosed by the fixer:** `dataset_provenance()` prints
   *"rates below are UNCONDITIONAL"* for a post-fix sample. That is **overstated** —
   `sample_once()` still filters to a 7-series ALLOW allowlist (`:60`, applied `:119`), so even a
   fully unfiltered post-fix dataset is conditional on our own series and is not a venue-wide
   rate. *The function built to stop a conditional rate reading as unconditional itself prints
   the word UNCONDITIONAL.*
3. `series_fee_types.json` untracked — see §0. **Open task #8.**
4. Frozen-dataset md5 guard is **CRLF-dependent** and fails on any LF checkout. Undisclosed
   portability artifact, not a data problem. **Open task #9.**
5. **Disclosed behaviour change:** default sampling flips from implicit head-of-list to
   `SAMPLE_MODE="random"` (`:96`). **Re-running the scan will NOT reproduce committed §M5 rows
   unless `--head` is passed.** Study script, zero importers, off the live path.

---

## 4. DEFECT — quoter: Q1 categorical netting, Q2 strike parse, Q3 daily-loss meter

**Lane:** quoter · **Commit:** `5cb3fd9` · **Verdict: `root_cause_holds = FALSE` — 2 of 3 hold as
stated; Q2 does not, and one "fails SAFE" claim is REFUTED by measurement.** This is the one lane
where the verifier materially disagrees with the fixer, and it is the lane that touches live
trading.

### Q1 — categorical event netting: **HOLDS (root cause real), but PARTIAL ROOT**

**Symptom / root cause.** `event_deltas` keyed every held ticker by event, so a categorical
(non-additive) event netted to flat while carrying live naked exposure. Verifier reproduced:
pre-fix `event_deltas({"KXWORDLE-26JUL23-HELLO": +20, "KXWORDLE-26JUL23-WORLD": −20})` returns
`{"KXWORDLE-26JUL23": 0.0}` — **reads FLAT while carrying two live naked exposures.**

**Fix.** `maker_kalshi_quoter.py:1675 _is_ladder_event(tickers)` (additivity proof: every strike
parses numeric **and** strikes distinct); `:1697 event_deltas` groups first, keys by **event**
only for a proven ladder and by **ticker** otherwise (return type and signature unchanged);
`:1725 event_delta_for(ev_delta, ticker)` ticker-key-first (collision-proof); `:1071` call site
switched; `:1015-1024 plan["nonladder_events"]` telemetry.

**REFUTED CLAIM — "Fails SAFE (unprovable ⇒ not a ladder)" is FALSE in one direction.**
`event_delta` **LOWERS** the throttle trigger (`mag = max(|inv|,|ev|)`, `:564`) and supplies
direction when flat (`:560-561`). Degrading a **genuine** ladder to per-ticker therefore fails
**OPEN** on the accumulation guard. Measured on a real ticker shape present in the repo's own
captures (86 `KXFUNDRAISING` tickers, strikes `"A23000000"` — an **"A"** prefix, and `_strike_of`
only lstrips `"T"`):

```
held = {…-A14000000:+12, …-A20000000:+18, …-A23000000:+20}
PRE : ev = 50.0 for every leg; flat sibling also 50.0
POST: ev = 12.0 / 18.0 / 20.0; flat sibling 0.0
```

With `INV_SOFT_CT` (live **15**) the +12 leg goes **from throttled to NOT throttled**, and the
flat-sibling direction signal is destroyed. Not reachable under the frozen allowlist (all 5
allowlisted events measure as provable ladders), but the report asserted conservatism without
qualification.

### Q2 — `_strike_of` parsing: **DOES NOT HOLD AS A LIVE DEFECT, and introduces a NEW hazard**

**Fix as written.** `:1587 _strike_of(ticker, stats=None)` drops leading purely-alphabetic
qualifier fields — never the last field, never a bare `T`; `:1614` every failure bumps
`stats["strike_parse_failed"]`; `:1631 ladder_pairing(held_by, stats=None)` threads it;
`:947-953` `run_once` prints a WARNING when non-zero.

**Verifier extracted every ticker in the repo's committed/local captures (456 unique, incl. 154
real `KXAAAGASM` market tickers) and diffed PRE vs POST `_strike_of` on all of them: 0
disagreements.** Every real market ticker is 3-field; the 4-field `KXAAAGASM-25MAR31-US-4.00`
shape appears **nowhere** in the evidence (only the 3-field EVENT ticker does), and `KXAAAGASM`
is not in `KALSHI_SERIES_ALLOW`. The commit headline calls this one of "3 live defects" and the
new docstring lists the 4-part form under "REAL TICKER SHAPES" — **it is historical/latent**. The
report body concedes this; the headline and docstring do not.

**NEW HAZARD introduced (measured).** Stripping leading alpha fields makes previously-unparseable
tickers **parseable**, while `ladder_pairing` groups by `_event_key` = first **two** fields only
(the qualifier is discarded) and **`ladder_pairing` is NOT gated by `_is_ladder_event`**:

```
held = {"KXFOO-26JUL23-US-4.00": +10, "KXFOO-26JUL23-EU-6.00": -10}
PRE : strikes None/None -> pairing leaves naked +10/-10
POST: strikes 4.0/6.0   -> _is_ladder_event True -> pairing returns 0.0/0.0, ev_delta 0.0
```

Two different sub-entities are now marked a **FLOORED pair, naked=0 on both legs**, which blinds
velocity, HELD_MAX, the capital cap, the de-risk pass, the strand-unwind **and** the settle-taker.
This is exactly what `_strike_of`'s own docstring warns against, and it **re-creates the Q1 defect
through the parser Q2 added.** Latent today (same 4-field precondition), **bounded by no code.**

### Q3 — daily-loss meter: **HOLDS, ROOT, and the dominance proof survives**

**Symptom.** Measuring against a **frozen** day-start let **income inflate the quota**. Live
07-23: equity $99.76 vs day_start $63.34 → **$76.42** of effective room against a nominal **$40**
quota (**1.91×**) — 76% of an $85 account could evaporate first.

**Fix (`:875-916`), behaviour change stated explicitly.** Two meters, halt on the **worse**:
`dd` = drawdown from the intraday high-water mark (`equity_day_peak`, ratchets); `down` =
cumulative sum of per-cycle equity **decreases** (`equity_day_down`, up-moves ignored). Env var
name `KALSHI_DAILY_LOSS_HALT_USD` unchanged. New plan keys `daily_dd`, `daily_down`; new state
keys `equity_day_peak`, `equity_day_down`, `equity_prev`. Peak seeds from `equity_day_start`
(`:899`) so a pre-fix state file migrates with old behaviour intact **as a floor**.

**Verifier: "new meter dominates old pointwise" VERIFIED by property test over 20,000 random
equity paths — `min(new − old) = 0` exactly, never negative.** The $76.42 / 1.91× arithmetic is
internally consistent; the verifier could **not** verify the $99.76 input (freeze doc records
equity $64.98 / day_start $63.341146 at 02:19Z).

**MEASURED PIN COUNT (verifier): 12 new tests, 9 fail on pre-fix** — exactly the claim. Method:
reconstructed `5cb3fd9~1` (md5 **`727ca7c5…`**, i.e. the deployed artifact) and ran the post-fix
test file against it. Per lane: Q1 4/4, Q2 3/3, Q3 3/2.
**Nuance the fixer did not state: only 6 of the 9 fail on pre-fix BEHAVIOUR.** Three fail purely
because a symbol/signature is absent (`event_delta_for`, `_is_ladder_event`, `ladder_pairing`'s
`stats` kwarg). **Q1's honest behavioural pin count is 2, not 4.** The 3 declared non-pins are
correctly self-declared. The verifier independently reproduced the **83 passed** pre-fix baseline.

**STILL UNFIXED / OPEN in the quoter:**
1. **R1 — throttle loosening on any genuine ladder whose strikes do not parse** (non-`T` prefix,
   e.g. the "A"-prefixed `KXFUNDRAISING` shape in the repo's own captures). Fail-**OPEN**, no test.
2. **R2 — cross-qualifier false pairing** (Q2 × Q1 interaction, above). `ladder_pairing` still has
   **no `_is_ladder_event` gate**. No test.
3. **Q1 = PARTIAL ROOT.** The additivity precondition went into a shared helper and the aggregate
   path was fixed — but `ladder_pairing`, the **other** consumer of the identical precondition,
   was left ungated. Classic "guard added to one branch instead of the shared helper."
4. **Q2 = PATCH, not root.** A heuristic string-munge with no venue-derived structure. **The root
   fix is reading `strike_type` / `floor_strike` / `cap_strike` off the market object the client
   already fetches — which would close R1 and R2 at once.** This is the single highest-value
   follow-up in the quoter.
5. **Three UNDISCLOSED behaviour changes** (verifier-found):
   - **Upward transients now permanently consume the quota.** The report says a deposit buys zero
     room (true) but not the converse: the high-water mark ratchets on **any** transient equity
     spike, and the next normal cycle reads the normalization as a drawdown. Pre-fix, up-moves were
     ignored entirely and the meter was immune.
   - **Un-held tickers in a non-ladder event now get `event_delta` 0.0** instead of the (wrong)
     sibling net — measured 50.0 → 0.0. The documented "when we're flat on this ticker, supplies
     the throttle direction" feature (`:432-437`) is silently removed for every non-provable event.
   - **`ladder_pairing`'s signature changed** (`held_by` → `held_by, stats=None`) and it now
     **mutates the caller's `plan` dict in place** at `:947`. Additive for positional callers
     (`_flatten_all` at `:1468` still calls with one arg) and the one in-repo monkeypatch stub was
     updated, but any out-of-repo wrapper breaks with TypeError.
6. **(b) cost basis vs mark — deliberately NOT switched**, justified in-code at `:852`:
   `/portfolio/balance`'s `portfolio_value` is offered as a venue mark but whether it *includes
   cash* is unverified; read wrong it double-counts the balance and halves or doubles the meter.

---

## 5. THE TWO DEFECTS THE QUOTER LANE WAS TOLD NOT TO ATTEMPT — **BOTH REMAIN OPEN**

Both are encoded as `@pytest.mark.xfail(strict=True)` in `test_live_hardening.py`, i.e. they are
**executable proofs of a live defect** that will go RED the moment someone fixes them. Integrator
re-ran with `--runxfail` and confirmed both fail for their stated reasons:

**FINDING 1 — paired-inventory downside is invisible to the breakers.**
`test_live_hardening.py:1521 test_finding_paired_downside_invisible_to_naked_held_cost`
```
assert 0.0 >= (10 * 2.0)
  where 0.0 = naked_held_cost({'KXAAAGASD-26JUL23-4.00': 10.0, 'KXAAAGASD-26JUL23-6.00': -10.0},
                              {'…-4.00': 0.6, '…-6.00': 0.55})
```
A floored ladder pair's **real** downside is the **strike gap**, and `naked_held_cost` scores it
**0.0**. Every breaker that gates on naked cost — velocity, HELD_MAX, the de-risk pass — is blind
to it. **OPEN.** This matters more now than at freeze time, because live `paired_ct` went
**0 → 2.0 → 12.0** (freeze doc §4): the pairing path is now firmly exercised in production.

**FINDING 2 — no exit path for a matched pair on a program-expired ticker.**
`test_live_hardening.py:1530 test_finding_no_exit_path_for_matched_pair_off_footprint`
```
AssertionError: no exit order was emitted for the stranded matched pair
assert [] == MockClient.created
```
A fully matched pair on a ticker whose incentive program has ended falls off the footprint, and
**nothing emits an exit** — the capital is stranded until settlement. **OPEN.**

Neither was in scope for any lane this session. Both are now pinned, which is strictly better
than the pre-session state where they were prose.

---

## 6. WRONG NUMBERS THIS RETIRES

Every figure below was **published** and is **wrong because of a specific defect above**. Do not
re-quote any of them. Where a corrected value exists it is given; where the honest answer is
"unknown", that is said.

| retired figure | why it was wrong | replace with |
|---|---|---|
| **−$442 settlement on an $85 account** (`KXAAAGASD-26JUL23`) | §2 — settlement counts read as net when they are **cumulative lifetime gross**; matched pairs never credited | **−$7.4656** event lifetime P&L; **−$8.2032** settlement-leg P&L (two different quantities, never conflate) |
| **$156/period** | §1 — `period_reward` and residual computed off a fill-cash model that mis-signed 156/317 fills; on an $85 account this was impossible on its face | no replacement — **do not restate a per-period figure until the fixed ledger has run**; and canon R1: `period_reward` is a TOTAL, normalise to $/day |
| **$604/day capture on a book that was 0% two-sided** | §3 D1 + canon R3 — capture scored on markets the **book** never made two-sided at Target Size, from a dataset that had already discarded every one-sided row | recompute under `CONC_KEEP_ONESIDED=1` with R3 as a **Target Size** test; the honest unconditional comparator today is canon **§M6b 20.5%** |
| **any `rewards_residual` / rewards-per-day figure from `ledger-202607.jsonl`** | §1 — 16,008 bytes of old-model rows on the box, computed with the inverted sign; wrong by that interval's `ask` fills | **nothing yet.** The corrected model measures −$60.91 fill cash vs a shipped −$86.54 over 07-22→23, and **$0.0000 residual in 24/27 intervals**. Any pre-cutover row must be recomputed or excluded (§1 unfixed #1) |
| **"the two-sided rate is 86.1%"** stated unconditionally | §3 D1 — `not yl or not nl: continue` filtered the failure mode out **before writing**; zero empty-side rows in 353 is the filter's fingerprint | 86.1% is **CONDITIONAL** on the pre-filter *and* on our 7-series allowlist. Additionally **49 of 353** are non-empty-but-below-Target R3 failures the old framing never counted |
| **"KXRT reads 0 programs because the census truncated at 1000"** | §3 D2 — **the symptom does not reproduce.** Both page sizes return an identical census; KXRT = **70** under both | KXRT = **70 programs**. D2 is a latent-ceiling fix only. The 0-reading has some **other**, still-unidentified cause (point-in-time program state is the leading candidate) |
| **any "top N series" list produced by `kalshi_series_scan.py` before `80315af`** | §3 D3 — selection was head-of-API-response; **9 of the top 12** series have exactly 1 distinct ranking key, so "top N" was arbitrary | re-run with an explicit `--sample-mode` and seed. Note the default is now `random`, so committed §M5 rows need `--head` to reproduce |
| **"the $40 daily-loss halt caps the day's loss at $40"** | §4 Q3 — measured against a frozen day-start, income inflated the quota: **$76.42** of real room against a $40 nominal (**1.91×**) | post-fix the meter is `max(dd_from_peak, cumulative_down)` and is provably ≥ the old meter on every input (20,000-path property test, `min(new−old)=0`) |
| **"a sign flip fixes the ledger"** (a live proposal, never shipped) | §1 — measured at **−$741.15** fill cash against a balance-implied **−$60.91**, i.e. **$680 wrong** — *worse than the bug* | the model must be **POSITION-AWARE**. This is written into `kalshi_attribution_ledger.py:118-125`; do not repeat it |
| **"`kalshi_attribution_ledger.py:109` still books sells with the wrong sign, worth −$431.34"** (in the settlement lane's hand-off) | §2 unfixed #1 — the author read a pre-`72c01f3` tree; `72c01f3` is that commit's own **parent** and already fixed it | **the statement is void.** Acting on it would reverse a measured root fix on the live money-handling module |
| **"maker fees are free"** stated for an unknown series | §3 D3 — `fee_status()` previously assumed free; canon §M10's default-0 multiplier is only safe **against the Non-Standard table** | 162 series with active LIP programs: **161 FREE / 1 CHARGES / 0 UNKNOWN**. The one that charges is **KXAAAGASM** — same `KXAAAGAS*` family as our allowlisted GASD/GASW |
| **"`test_ladder_invariants_flagged_live` is flaky"** | §2 — the fixer's claim; verifier ran it **6 consecutive times, 7 passed every time, zero TypeErrors** | **unverified.** Do not carry it forward as a known flake |
| **"117 passed" as a suite result** | §2 — a two-file subset presented as a full run | the full suite is **159 passed, 2 xfailed** |

---

## 7. FREEZE STATUS — CODE INTACT, **CONFIG BREACHED**

**Code: INTACT.** All five deployed `.py` md5s match the freeze baseline **exactly** (§0),
including `maker_kalshi_quoter.py` = `727ca7c59840a42b51c19e24c65a0982`. No STOP sentinel.
Nothing from any of the four commits reached the box. All SSH this session was read-only
(`md5sum` / `sha256sum` / `stat` / `ls` / `grep`).

**Config: NOT intact, and the freeze doc's own detector is blind to it.**

```
/opt/pa2-maker-kalshi-live/live.env
  sha256 now    70ea49ff2569df4c39713f2d7ee476d02ce6247c53fb9f6c3283440470628fd5
  sha256 frozen 8ebc0b76be7697abd7718e46fcd5c0591b2aebcc5684e4dd154d8463e6186179
  mtime         2026-07-23 15:19:18Z
  KALSHI_TAKER_FLATTEN=1        (freeze doc records 0, and lists the flip as deferred task #2)
  KALSHI_REDUCE_ONLY_KEEP_BOTH=1
```

Three writes happened inside the freeze window:
- **04:38:22Z** — `REDUCE_ONLY_KEEP_BOTH 1→0`. **Documented**: the armed `kalshi-plugin-off.timer`,
  freeze doc §3, deliberately left running.
- **15:11:44Z** — `REDUCE_ONLY_KEEP_BOTH 0→1` (backup `live.env.bak-20260723_151144`).
  **Not documented anywhere.**
- **15:19:18Z** — `TAKER_FLATTEN 0→1`. Recorded after the fact as **completed task #2**
  ("DONE 15:19Z"), i.e. operator-directed, but it directly contradicts freeze doc §2 which pins
  that flip as `[FROZEN]`.

**THE DETECTOR IS BROKEN — stop using it.** Because the 15:11:44Z write **restored** the value the
04:38 timer had changed, `sha256(live.env)` was **back at the frozen baseline** for the window
between 15:11 and 15:19. Two of the three verifiers ran their check inside that window and both
reported *"live.env sha256 matches the freeze baseline — held"* **while the file had already been
mutated twice.** The freeze doc's §6 rule ("any live.env sha256 change = the freeze was broken")
is **round-trip blind**. Replace it with an append-only write log or per-key diffing against a
recorded key/value table. Tracked as open task **#10**.

Attribution: **not attributable to any of the four lanes.** All four commits touch only `.py`
files under `kalshi_live/`, none has a VPS or deploy path, no committed code writes `live.env`,
and the `.bak-<ts>` convention is ad-hoc shell (an interactive session). All sessions SSH as
`ubuntu` from the same source IP, so the audit trail cannot separate them.

---

## 8. STATUS ROLL-UP — FIXED / PARTIAL / OPEN

### FIXED (root, verifier-confirmed)
| defect | lane | note |
|---|---|---|
| Fill cash flow sign-inverted on 156/317 fills, and position-blind | attribution-ledger | ROOT. Residual exactly $0.0000 in 24/27 intervals; the other 3 reconcile to credits and a deposit to the cent |
| Settlement counts read as net when they are cumulative lifetime gross (the −$442) | settlement-pnl | ROOT. 51/51 revenue reconciliation, 47/47 CSV agreement, both independently re-derived |
| Concentration sampler discarded one-sided books before writing (D1) | studies | ROOT for future samples. Cannot retroactively repair the frozen dataset — the rows were never written |
| Degenerate head-of-response ranking (D3) | studies | ROOT. Verifier found it **worse** than reported: 9/12, not 8/12 |
| Daily-loss meter measured against a frozen day-start (Q3) | quoter | ROOT. Dominance over the old meter proven on 20,000 random equity paths |

### PARTIALLY FIXED
| defect | what's fixed | what is NOT |
|---|---|---|
| **Categorical event netting (Q1)** | the **aggregate** path (`event_deltas` / `event_delta_for`) is gated on a proved-additive precondition | **`ladder_pairing` — the other consumer of the identical precondition — is still ungated.** Same defect class live in the pairing path, and Q2 widened it |
| **Strike parsing (Q2)** | failures are now counted and surfaced instead of silent | the parse itself is a **heuristic string-munge (PATCH)**. Root fix = read `strike_type`/`floor_strike`/`cap_strike` off the market object the client already fetches |
| **Census page ceiling (D2)** | the ceiling is raised and truncation now warns | it fixed **no observed symptom**; the "KXRT = 0 programs" reading has some other, unidentified cause |
| **R3 two-sided exclusion (studies)** | `two_sided_stats()` applies it correctly as a Target Size test, and the report prints the census | `score_snapshot()` **re-inlines** the predicate and `score_market()` still uses a non-empty test — **R3 is defined two ways in one report.** Measured impact on this dataset: **0.1% of payout, zero effect on floored capture** |

### OPEN — nothing shipped, no fix attempted
| defect | evidence |
|---|---|
| **Paired-inventory downside invisible to the breakers** | `test_live_hardening.py:1521`, strict xfail, reproduced. `naked_held_cost` scores a floored pair **0.0** when its real downside is the strike gap. Live `paired_ct` is now **12.0** |
| **No exit path for a matched pair on a program-expired ticker** | `test_live_hardening.py:1530`, strict xfail, reproduced. Capital stranded until settlement |
| **R1 — throttle fails OPEN on a genuine ladder with non-`T`-prefixed strikes** | measured: event aggregate 50.0 → per-ticker 12/18/20; the +12 leg crosses from throttled to not-throttled at live `INV_SOFT_CT=15`. No test |
| **R2 — cross-qualifier false pairing** | measured: two different sub-entities now pair to `naked=0` on both legs, blinding every breaker. No test |
| **Mixed-model ledger file** | no version field in the row; `report()` sums old- and new-model rows together. 16,008 bytes of old-model rows on the box |
| **`position_recon_mismatch` gates nothing** | `report()` sums `rewards_residual` unconditionally, including rows the code declares untrustworthy |
| **Ledger fails closed permanently on one unknown fill shape** | `replay_fills` runs the full historical tape every run; one raise bricks the hourly timer forever |
| **`series_fee_types.json` untracked** | 2 fee pins skip silently on a clean checkout; `fee_status()` fails **open** to UNKNOWN. Task #8 |
| **`dataset_provenance()` prints "UNCONDITIONAL"** for a still-allowlist-conditional sample | the anti-overclaim function overclaims |
| **Frozen-dataset md5 guard is CRLF-dependent** | fails on any LF checkout. Task #9 |
| **live.env freeze detector is round-trip blind** | two verifiers reported "held" through two real writes. Task #10 |

---

## 9. DEPLOYMENT READINESS — what a deploy would change on the live bot

**Nothing has shipped. `deploy` is entirely the operator's call.** Blast radius, by artifact:

### A. `maker_kalshi_quoter.py` — **THIS ONE CHANGES LIVE TRADING BEHAVIOUR. Read before deciding.**

**What changes for real money:**

1. **The daily-loss halt gets strictly tighter, immediately.** The meter becomes
   `max(drawdown-from-intraday-peak, cumulative-sum-of-per-cycle-decreases)` against the same
   `KALSHI_DAILY_LOSS_HALT_USD=40`. It is **provably ≥ the old meter on every input** (never
   weaker), so **nothing that used to halt stops halting**. Newly halting:
   - equity rises to a new peak then falls back >$40, **even while still above the day's open**;
   - a slow bleed of separate small down-moves whose **sum** exceeds $40, papered over by income
     between them;
   - a mid-day deposit no longer adds room 1:1 and never forgives drawdown already taken.
   On the measured live case (equity 99.76 vs day-start 63.34) the old meter had **$76.42** of
   room; the new one gives exactly **$40** from the peak.

2. **⚠ THE SINGLE BIGGEST DEPLOY RISK — the new meter can turn an upward equity *glitch* into a
   real-money taker fire-sale, and `TAKER_FLATTEN` is now `1` on the box.**
   `equity_day_peak` ratchets on **any** up-move (`:894`, `:899`). `_held_cost` at `:1580-1581`
   is `total += me if me else abs(n)` — when the venue omits `market_exposure_dollars` (a branch
   the code's own docstring at `:1566` explicitly anticipates), held cost reads **~$1.00/contract
   instead of the real ~$0.15**. At the frozen position set (8 positions, held $24.13) that is a
   **$100+ upward spike** → the peak ratchets → the very **next clean cycle** measures
   `dd ≫ $40` → `STOP` written → `_flatten_all(client)` → which, after `STOP_ESCALATE_S=90`,
   escalates from passive maker offsets to a **bounded taker cross** (`:1519-1531`).
   `equity_day_down` also records the move and is **never repaid**, so the day stays poisoned
   after the data recovers. **The pre-fix meter ignored up-moves entirely and was structurally
   immune to this.** This lane's recorded losses are **~−$45 from exactly two taker fire-sales**,
   and `KALSHI_TAKER_FLATTEN=0` was the frozen value — it is now **1** (§7), so a false halt is
   strictly more expensive than when the fix was written and tested. **There is no test, no
   clamp, and no sanity bound on a single-cycle equity jump anywhere in the new meter.**
   **Recommendation: do not deploy the quoter until a single-cycle equity-jump clamp exists, or
   deploy it with `KALSHI_TAKER_FLATTEN=0` restored.**

3. **Throttle behaviour changes on non-provable events.** Under the current allowlist
   (`KXTEMPDCH,KXTEMPAUSH,KXTEMPLAXH,KXTEMPNYCH,KXTEMPCHIH,KXAAAGASD,KXAAAGASW`) **all 5
   allowlisted events measure as provable ladders**, so Q1 is a **no-op on today's universe** —
   it only bites if the allowlist is widened. When it does bite: un-held siblings in a
   non-ladder event get `event_delta 0.0` instead of the sibling net, removing the "supplies the
   throttle direction when flat" behaviour; and a genuine ladder whose strikes do not parse
   loses its event-level aggregate (**fails OPEN**, R1).

4. **New telemetry only, no consumer breaks:** plan keys `daily_dd`, `daily_down`, and
   conditionally `nonladder_events`, `strike_parse_failed`, appended to `plans-*.jsonl`. New
   state keys `equity_day_peak`, `equity_day_down`, `equity_prev`; `save_state` is in a `finally`
   (`:1339-1341`) so every early return persists them. **A pre-fix state file migrates cleanly** —
   `equity_day_peak` seeds from `equity_day_start`, reproducing old behaviour as a floor.

5. **No change to order placement, sizing, spread, gating, or the naked-risk breaker.**
   `dryrun_smoke.py` is clean and cycle 2 quiesces at 0 churn.

### B. `kalshi_attribution_ledger.py` — **do NOT deploy as-is.**
The cash model is correct going forward, but there is **no version field on the ledger row** and
`report()` sums old- and new-model rows together over a 16,008-byte file of old-model rows on the
box. The **first `--report` after deploy emits a blended, authoritative-looking, wrong rewards
figure.** Preconditions for deploying this file:
1. stamp a `cash_model` version into the row;
2. make `report()` either exclude pre-cutover rows or recompute them;
3. make `report()` drop or flag any row with a non-empty `position_recon_mismatch`;
4. decide the fail-closed policy — today one unrecognised historical fill bricks the hourly timer
   permanently, because `replay_fills` re-runs the whole tape every run.
Note this file is on an **hourly timer**, so a brick is silent until someone reads a report.

### C. `kalshi_settlement_pnl.py` — **safe to deploy, and also pointless to.**
A new standalone module with **zero importers** (`grep -rn kalshi_settlement_pnl` → nothing
outside its own test), no network at import, and one opt-in `--save DIR` write path. It cannot
change live behaviour. Deploy it only if you want to run it on the box; running it locally
against a fresh export is equivalent.

### D. `kalshi_concentration_study.py`, `kalshi_series_scan.py` — **off-box, no deploy needed.**
Neither file exists on the VPS. `kalshi_series_scan.py` has zero importers. The only operational
note is that **`--head` is now required to reproduce committed §M5 rows** (default flipped to
`random`).

### Recommended sequence if the operator lifts the freeze
1. Restore `KALSHI_TAKER_FLATTEN=0` **or** add a single-cycle equity-jump clamp to the new meter.
   (This is the cheap half of the risk in §9.A.2 and takes one env line.)
2. Deploy the **quoter alone**, md5-gate it from the **git blob**, and watch `daily_dd` /
   `daily_down` / `strike_parse_failed` / `nonladder_events` in `plans-*.jsonl` for a full day.
3. Only then land the ledger, and only after its 4 preconditions in §9.B are met.
4. Separately: redeploy `flatten_kalshi.py` from the git blob and md5-gate it (freeze finding,
   open task **#6** — the **kill switch** is currently the one artifact installed from an
   unverified Windows working file).
