# KALSHI MAKER — SELECTION & SCORING TRIPLE-BLIND REVIEW (2026-08-01)

**Operator-ordered:** "massive deep triple blind dive to review all selection process scoring
process and anything in between. assume nothing, if anything assume we have bugs. do not make
things up. facts only. no work full report."

**Method.** 11 agents, READ-ONLY, no code/config/service changes:
- 2 recon (code map of the pipeline; live funnel measurement from logs)
- 5 independent blind reviewers — 3 covering the WHOLE pipeline with no sight of each other
  (true triple-blind: agreement across them is the signal), + 2 specialists (temperature
  absence; entertainment/KXYTVIEWS absence)
- 3 adversarial lenses instructed to REFUTE (re-read the code / demand runtime evidence /
  attack materiality and double-counting)
- 1 synthesis (first attempt died on an API error; re-run from cached agent results)

**Provenance.** All 8 code-reading agents independently md5-matched the deployed binary:
`maker_kalshi_quoter.py` = `420dc43feb1ed46353e902ecee58b5ae`, 5,064 lines, mtime
2026-07-31T19:54:30Z. Service `polymarket-maker-kalshi-ws` active,
`ExecMainStartTimestamp = 2026-08-01T10:00:01Z`, `NRestarts=0`, pid 87664. Env changed at
10:00:01Z (caps 60/60 → 45/40); plan rows before 10:00Z are the same code under the old caps.
Live branch confirmed by all agents: `PIVOT_SELECT=0`, `ALLOC_KEY=0`,
`ALLOC_INCUMBENT_FIRST=0` — the legacy round-robin at `:1372-1391` is the executing path.

Labels: **ESTABLISHED** / **INFERRED** / **HYPOTHESIS**.

---

## 1. BOTTOM LINE

The bot allocates **40 market slots per cycle and quotes ~12**, because the market-close
pre-filter checks only ~80 rows and then appends the remaining ~3,300 **unchecked**
(`:1316-1318`); the round-robin draws most of its 40 picks from that unvetted tail and a later
belt at `:2807` kills them. Of the ~12 that survive, **50.7% of quote rows emit no price and no
gate counter**, so **23 markets actually rested today out of 6,861 scored**.

---

## 2. CONFIRMED DEFECTS

| # | Defect | file:line | Independent finders | Verdict |
|---|---|---|---|---|
| D1 | Pre-filter unchecked tail → ~70% of slots die at the belt | `:1316-1318` / `:2807` | **4 of 5** | CONFIRMED by all 3 lenses |
| D2 | 50.7% of quote rows emit no price and no gate counter | `:1901`, `:1909-1910`, `:1915-1918` | 2 | CONFIRMED (observability) |
| D3 | Series ranked by pool of the *score*-best member (split key) | `:1346-1350`→`:1358-1360`→`:1376` | 1 | CONFIRMED |
| D4 | Gate-skip written to score cache as *measured* capture $0, fresh `ts` | `:3464-3468` ← `:1833-1854` | 3 | Mechanism CONFIRMED, impact narrowed |
| D5 | net-EV gate live; its calibration module is not deployed | `:808-818`, `:1986-2006` | 1 | CONFIRMED |
| D6 | `PER_SERIES_CAP` unreachable — one member per series, ever | `:1379-1389` | 1 | CONFIRMED (binds *after* D1) |
| D7 | Alphabetical series tie-break | `:1376` | 3 | Mechanism CONFIRMED, **primacy REFUTED** |
| D8 | Explore probes measure at 5 ct, store as full-size value | `:3433-3437`→`:3464-3468` | 1 | Mechanism CONFIRMED, magnitude PLAUSIBLE |
| D9 | `SCORE_EXPLORE` cannot reach the low-pool tail | `scores.py:205`, `:212` | 1 | CONFIRMED |
| D10 | Largest drop stage in the funnel has no counter | `:1379-1391` | 1 | CONFIRMED |

### D1 — Pre-filter keeps an unchecked tail (ROOT DEFECT)
```python
1316            if len(_kept) >= FOOTPRINT_TOP * 2 or _checked >= _budget:
1317                _kept.extend(rows[_ri:])            # tail unchecked -> run_once belt handles it
1318                break
```
`_checked` increments **only on a cache miss** (`:1322`), and positive `_CLOSE_TIME_CACHE`
entries never expire (`:1064-1073`). In steady state the read budget is inert and **80-kept is
the only stop condition** — 80 *rows*, while the selector's unit is *series*.

**ESTABLISHED (runtime):**
- `footprint + drop_far_market_close == 40` in **2,277/2,277 cycles**, 3 independent readers,
  zero exceptions (1,125 + 1,122 + 1,125 on 08-01; 1,155 on 07-31). `picked` always exactly 40.
- `caprank.actual` length = 40 in **2,267/2,267** cycles — independent confirmation, different log.
- `footprint` p50 = **12** (08-01, n=1,125), **17** (07-31, n=1,155); `drop_far_market_close`
  p50 = **28** → **70% of allocated slots killed after allocation**.
- **129 distinct tickers picked and survived ZERO times; 27 picked ≥1,000×.** No ticker ever
  partially survives — the belt outcome is deterministic per ticker, so they are re-picked and
  re-killed every cycle, forever, at zero read cost.
- `KXGDPYEAR-31-B0.3`, close_time **2032-02-29T13:29:00Z**, picked **2,229/2,267** cycles today.

**Counterfactual (deployed `rank()` + exact `:1376-1389` round-robin vs live data, 16:33Z;
Case A reproduced live `drop_far_market_close_sel` exactly, 440 vs 440):**

| | rows post-pre-filter | picked | belt kills | survive | survivor pool |
|---|---|---|---|---|---|
| A — live pre-filter | 3,421 (80 checked + 3,341 unchecked) | 40 | 24 | **16** | $3,526.19/day |
| B — complete pre-filter | 564 | 40 | **0** | **40** | $5,251.19/day |

### D2 — 50.7% of quote rows emit no price and no gate counter
Five independent measurements: **50.6–50.7%** (n = 13,800–14,519 rows; 07-31 = 45.8% of 19,666).
The strongest replication in the review.

**Named example, four agents:** `KXMAMDANIEO-26AUG01-T0` — **$1,000/day, the largest single
active pool on the venue** — occupied a footprint slot in **1,081/1,081, 1,085/1,085,
1,135/1,135, 1,139/1,139** quote cycles today. `y_qual`/`n_qual` true on every row;
`y_ref` ∈ {0.01, 0.02, 0.03}; **0 rows priced, 0 rested, `gates` empty on every row.**
`_ok_entry_price` (`:1513-1515`) is `MIN < p <= MAX` with `MIN_PRICE_DOLLARS=0.04`. Because
`best_y + best_n < 1`, a YES reference below 0.04 forces NO above 0.96 — **both sides fail
together**, excluding the entire longshot category venue-wide with zero diagnostic output.

**CORRECTION (caught by a lens; two reviewers quoted a paraphrase, not the file):**
`:1915-1918` are **not** bare `return []`. Both read
`return _reducing_quotes(best_y, best_n, inv, cost, improve=_improve) if abs(inv) >= INV_TOLERANCE else []`.
The genuinely bare `return []` sites are `:1901` (wind_down flat) and `:1909-1910` (crossed book).

Counters that DO fire (08-01): `netev_skipped` 1,531 · `explore_probe_capped` 1,064 ·
`holding_exit_only` 221 · `unqualifiable` 114 · `loss_exitonly_stripped` 114.

### D3 — Series key and member key computed from different quantities
`rank()` reorders by **score** (`:1346-1350`); `by_series` built in rank order (`:1358-1360`); then
`series_order = sorted(by_series, key=lambda s: (-by_series[s][0]["usd_day"], s))` (`:1376`).
Member chosen by score; series ranked by that member's **pool**. Two keys that disagree.

Replay of deployed `select_footprint` (16:07:42Z): 4,755 programs → 3,395 rows → 214 eligible
series → 40 picked. Series whose rank-best member has a lower pool than a sibling: **2 of 214**;
retained in live top-40: **0 of 2**. `KXPANAMAWEEKLY` rank-best $40.0/day vs true best $160.0/day;
`KXSUEZWEEKLY` same. Both lost their slot to a $135/day series.

Source comment `:1361-1366` states `KALSHI_ALLOC_KEY=1` exists specifically to fix
"Audit issue #3 (series rotation was pool-keyed)". **That flag is absent from `live.env`.**

### D4 — Gate-skip recorded as a measurement of zero capture
`_kms.update(...)` at `:3464-3468` sits under `if MKT_TELEMETRY:` at loop top level — **not**
under `if q:` at `:3447`. `q == []` → `our_px = None` → `score = 0.0` → `share = 0.0` →
`capture_usd_day = 0.0`, written with `ts = now`.

Four independent census reads of `kalshi_market_scores.json` (08-01 afternoon):
**873 of 6,862 rows (12.7%) carry any `ts`; 605 of those (69.3%) are capture-0.** Kind census:
unknown 6,303 / stale 785 / **scored 90**. `KXMAMDANIEO-26AUG01-T0` carries `n=6,646` —
6,646 recorded "observations" of a market never priced. `score_age_p50_m` = 6,490.

A third conflated path the reviewers missed: `:1852-1854` also sets `capture_usd_day = 0.0`
whenever `two_sided` is false. One zero now means three different things.

**Surviving harm (narrower than reviewers claimed):** `rank()` builds its explore quota from
`unknown` + `stale` only (`scores.py:212-215`), so a gated-out market is locked out of the
exploration quota for 30 minutes on a measurement that never happened.

### D5 — net-EV gate live, calibrator not deployed
`KALSHI_NETEV_GATE=1` in `live.env`. `import kalshi_netev_calibrate` inside the deployed venv →
**ModuleNotFoundError**; `kalshi_netev_table.json` absent. `NETEV_TABLE == {}` unconditionally.
Every family takes the unproven branch (`:1993-1996`), with defaults `NETEV_MODEL_HAIRCUT=3.0`
and `NETEV_FINGERPRINT_USD_DAY=5.0` (`:789-790`) ⇒ **a market must model prospective capture
> $15.00/day to open while flat.**

Runtime 08-01: `netev_skipped_markets` **1,491** (n=1,089 cycles); other agents 1,482 / 1,531.
By family: KXMCMORROWENDORSE 723 · KXACTBLUETOP 231 · KXSUEZWEEKLY 187 · KXPANAMAWEEKLY 147 ·
KXBABELMANDEBWEEKLY 137 · KXCLAYTONDNI 32 · **gas 15 (1.0%)** · KXTRUMPENDORSEMENTS 6 · KXAPRPOTUS 4.

The docstring at `:809` reads *"fail-OPEN to {} -> every family unproven, never blocks."*
**Measurably false.**

### D6 — `PER_SERIES_CAP` unreachable
Round 1 of the `while` at `:1379` always fills all 40 slots, so `per_series[s]` never exceeds 1
and the `i >= PER_SERIES_CAP` test at `:1383` is never reached. `KALSHI_PER_SERIES_CAP=100` is
dead. **Cycles where distinct-series == quote-rows: 1,158/1,158 (07-31), 1,100/1,100 (08-01).**
Zero cycles ever contained two markets from one series. `KXRAIN` (40 programs, $4,000/day) can
earn at most 1/40th of its family pool per cycle.

### D7 — Alphabetical tie-break: binding today, NOT an independent defect
`caprank actual[10:40]` strictly alphabetical in **1,789/2,268 (78.9%)**, **1,777/2,256 (78.8%)**,
**1,788/2,267 (78.9%)** across three lenses. At 16:39:55Z slots 1–8 descend $1,000→$110;
**slots 9–40 are all exactly $100.00/day and slots 11–40 strictly alphabetical**
(`KXAAL → KXEARNINGSMENTIONSPCX`) = **32 of 40 slots allocated by alphabet**. Minimum `usd_day`
ever entering the footprint across 14,442 rows = **$100.00**.

Three-way KXYTVIEWS split (one reviewer correct, another's merged treatment wrong):

| series | progs | pool | close | why absent |
|---|---|---|---|---|
| `KXYTVIEWS` | 7 | $230/day | 30.89 d | far-close |
| `KXYTVIEWSHIGH` | 57 | $80–160/day | 30.89 d | selected 410×, then belt-killed |
| `KXYTVIEWSW` | 56 | $100.00/day | **2.89 d** | passes every filter — **0 picks in 4,508 cycles** |

**PRIMACY REFUTED:** in Case B the eligible set collapses to **49 series** against 40 slots,
`KXYTVIEWSW-ARI26AUG02-13.0M` **is picked**, and the tie-break decides only among $15–25/day
series — residual ≈ **$20/day**. Two agents' counterfactuals agree independently.
**Severity LOW once D1 is addressed.** Two reviewers graded it CRITICAL; the evidence does not
support that.

### D8 / D9 / D10
- **D8** — explore probe clamps `count` to `EXPLORE_PROBE_CT=5` (`:3433-3437`), which flows into
  `our_ct` → `score` → `share` → `capture_usd_day` stored as the market's full-size value.
  n=5 sample ratios: 116× / 24× / 6.8× / 2.2× / **0.78× (inverts)**. Mechanism ESTABLISHED
  (arithmetic at `:1838-1839`), magnitude PLAUSIBLE only. ⚠ `explore_probe_capped` disagrees
  across agents — **2,059 / 1,062 / 1,064 from the same log day, unexplained. Do not quote it.**
- **D9** — `scores.py:212` takes `unknown` markets off a list sorted by `-score`, and for unknown
  markets `score = pool × unknown_bonus`, so **unseen explore slots go highest-pool-first**. One
  replay: all 10 explore slots went to $285.00/day markets. Docstring promises
  "least-recently-seen" — true only for the `stale` half. **873 of 6,862 rows (12.7%) have ever
  carried a measurement.**
- **D10** — `:1379-1391` exits and `return picked` discards the remainder with no `drops[...]`
  write. At 15:57Z: 3,484 rows / 216 series eligible, 40 slots issued, **~3,444 rows dropped with
  zero log lines, zero counters, zero reason codes.** `KXYTVIEWSW` is indistinguishable in
  telemetry from a market that was never discovered.

---

## 3. REFUTED / DOWNGRADED — DO NOT ACT ON THESE

**(a) "Alphabetical lockout is an independent CRITICAL defect costing $45,975/day across 488
programs." — REFUTED on both counts.** Primacy: consequence of D1, not independent. Denominator:
of 1,223 $100.00/day programs passing program gates, only **283 (23.1%)** close within 8 days;
of those 10 sort after the cut line = **232 programs = $23,200.00/day**, ~half the claim.
⚠ Passed on verbatim as a coupling fact: changing `:1376`'s tie-break *without* fixing D1 would
rotate 40 slots across ~198 series of which ~68% are belt-killed anyway — footprint stays ~12 but
the surviving 12 change every cycle, and `diff_orders` (`:2262-2264`) cancels every standing
order whose ticker leaves `desired` → continuous queue-position destruction against
`WRITE_BUDGET=60`, for the same earning surface.

**(b) "The partition is exactly the alphabet — 0/33 exceptions." — DOES NOT REPLICATE.**
Replication found 87 tied series, 51 selected / 36 never, and **3 exceptions**.

**(c) "Locked out forever / deterministically." — REFUTED.** Cut line drifts
(`KXMAR`/`KXMEDIACOVERSI` → `KXMETA` → `KXEARNINGSMENTIONSPCX`). Alphabetical-tail share was
**78.9% on 08-01 but only 3.5% (81/2,319) on 07-31**. Correct claim: *locked out in any given
cycle, with a boundary that drifts.*

**(d) "`KXVOGUECOVER` unreachable by name." — REFUTED.** Its market closes 2027-06-30 (332.94 d)
against `MAX_DAYS_TO_CLOSE=8`. Different subsystem.

**(e) "The venue's largest pool is demoted 7.5× and drops to rank 237 because we looked at it."
— REFUTED by all three lenses.** `:1376` keys on `usd_day`, not score. `KXMAMDANIEO` is sole
member of its series at $1,000/day → series #1 every cycle, and is picked in **2,267/2,267**
caprank cycles. The demotion is real in `rows`; the harm is not. Its score value oscillates
0→60 with age (0.3587 at 31 s; ≈0.6 steady state) — **no single rank number should be quoted.**

**(f) "`:1915-1918` are bare `return []`." — REFUTED.** See D2 correction.

**(g) "The bot is capital-bound; $280 ÷ $45 = 6 markets." — REFUTED.**
`est_capital_usd / total_cap_eff` p50 **0.384**, max **0.735**, zero cycles above 0.75.
`capped_markets = 0` in **1,125/1,125** and **1,363/1,363** cycles; `budget_dropped_markets = 0`;
`series_cap_dropped = 0`. `est_capital_usd` p50 **$107.40** vs `total_cap_eff` p50 **$289.05**.
**The `cap_desired` total-capital cut has never fired on this binary.** The 3,497
`capped_markets` sum on 07-31 traces to the **previous** binary under 60/60 caps.
INFERRED: would begin binding at roughly 15 concurrent markets.

**(h) "KXTEMP zero presence across six dates = our defect." — REFUTED for 5 of 6 dates.**
Complete pagination (**127 pages, cursor exhausted, 126,096–126,679 unique program ids**, three
agents independently): KXTEMP programs by `end_date` — 07-20: 1,050 · 07-21: 1,200 · 07-22: 800 ·
**07-23 through 07-31: 0, all nine days** · 08-01: 750. Exact gap: last pre-gap program ends
2026-07-22T17:00:00Z, next starts 2026-08-01T00:02:14.719401Z = **9 days 7:02:14**.
*Retention control* (absence ≠ API artifact): other 57–58-minute hourly programs ARE retained on
the gap days — `KXINXHUD`/`KXNDQHUD`, 12 progs on 07-27, 12 on 07-29, 8 on 07-30, 10 on 07-31.
*Bot-side corroboration:* score cache holds 750 KXTEMP rows with `pts` spanning
**2026-08-01T00:09:02 → 14:18:54Z only**, while retaining rows back to 07-30T16:11:10Z;
`ledger-202607.jsonl` contains 8 KXTEMP lines, **all on 2026-07-22**.
**The bot-side defect here is observability, not selection:** `programs_seen` *rose*
2,522 → 3,811 across the lapse; no counter or alert changed sign when a family supplying 36.1%
of lifetime reward vanished for 9.29 days.
On **08-01 only** KXTEMP exists: 750 programs = 15 hourly cohorts × 50, **all at $20.00/day**,
passing every row filter. Zero KXTEMP in the footprint is ESTABLISHED (footprint `usd_day` floor
= $100.00 across all 13,849 rows, including 26 live cycles inside a KXTEMP entry window).
Attribution to `:1376` specifically is **PLAUSIBLE, not established**.
*Noted from `:1353-1357`:* the round-robin was added because "a single high-pot series (50
concurrent hourly temp strikes ~$1,920/day each) would otherwise fill the whole FOOTPRINT_TOP".
The mechanism built to stop KXTEMP crowding everything out now excludes KXTEMP entirely, at $20/day.

**(i) "0.79% of venue pool" uses an unreachable denominator.** Of 3,904 eligible program-rows
carrying **$293,111.67/day**, only **564 rows / $45,391.67/day** have a market `close_time`
inside `MAX_DAYS_TO_CLOSE=8`. The other **$247,720.00/day is far-close and cannot be quoted**.
Corroborated: 77.3% far-close at 05:10Z over all eligible rows; 76.9% over the $100/day subset
at 16:30Z. **Against the reachable denominator, today's $2,345.71/day rested pool is ~5.2%
(INFERRED), not 0.79%.**

**(j) Pool is not capture, and modelled capture is not reward.** ESTABLISHED (n=1,141 cycles):
modelled `capture_usd_day` summed across the footprint p50 **$104.82/day** vs footprint pool p50
**$3,091.19/day** = **3.4% capture-of-pool**. INFERRED: realized rewards $167.35 ÷ 11.93 days ≈
**$14.03/day**, i.e. the model over-predicts realized reward by **~7.5×**.
**Every "$X/day of lost pool" figure must be divided by roughly 30 to reach modelled capture and
roughly 220 to reach realized reward. No reviewer did this.**

**(k) `cap_desired` pool-keying / incumbency-ignored — CONFIRMED as code, DORMANT in fact** (see
(g)). One lens marked the finding ⚠ DANGEROUS but its justification did not reach synthesis.
**Treat as unresolved.**

**(l) `INCUMBENCY_BONUS` inverted — NOT ASSESSABLE.** That reviewer's submission truncated
mid-sentence; no lens could grade it.

---

## 4. CHECKED AND CLEAN

- **Discovery is not the gap.** All 4,801 active program tickers present in
  `kalshi_market_scores.json`. The sweeper reaches the whole venue; the loss is entirely in
  selection and gating.
- **Branch identification.** `PIVOT_SELECT=0`, `ALLOC_KEY=0`, `ALLOC_INCUMBENT_FIRST=0` confirmed
  from `/proc/87664/environ` and plan `env_absent` by all agents. Every citation is against the
  executing branch.
- **`_kms.update` placement** verified directly at `:3440-3470`, exactly as described.
- **`live.env` values** confirmed by direct read: `FOOTPRINT_TOP=40`, `PER_SERIES_CAP=100`,
  `MAX_MARKET_CAPITAL=45`, `MAX_ACTIVATE_CAPITAL=40`, `MAX_TOTAL_CAPITAL=350`, `INV_HARD_CT=50`,
  `HELD_MAX_USD=40`, `SCORE_EXPLORE=10`, `SERIES_MAX_USD=100`, `FUNDING_GATE=1`, `NETEV_GATE=1`,
  `MKT_DAY_LOSS_EXITONLY_USD=3`, `REENTRY_COOLDOWN_S=3600`, `DAILY_LOSS_HALT_USD=40`,
  `DAILY_DOWN_HALT_USD=60`, `WIND_DOWN_MIN=20`, `MAX_DAYS_TO_CLOSE=8`, `MIN_PRICE_DOLLARS=0.04`,
  `MAX_PRICE_DOLLARS=0.96`, `SCORE_UNKNOWN_BONUS=0.06`, `INCUMBENCY_BONUS=0.25`,
  `EXPLORE_PROBE_CT=5`. `ALLOC_KEY`, `ALLOC_INCUMBENT_FIRST`, `PAIR_UNWIND`, `PIVOT_SELECT` absent.
- **Counters that fire correctly:** `netev_skipped` (`:2001`), `explore_probe_capped` (`:3436`),
  `loss_exitonly_stripped` (`:3444`), `holding_exit_only`, `unqualifiable`, `presence_skipped`,
  `quote_fail` (`:3428`), `drop_far_market_close_sel` (`:1335`).
- **Telemetry isolation.** `_market_telemetry_row` and the score-cache write are inside bare
  `try/except` (`:3453`, `:3469-3470`) and cannot alter `desired`. A telemetry fault cannot break
  a live cycle.
- **The far-close belt works.** `farclose_check_failed` sum = **0** across 1,089 cycles. The belt
  is doing its job on rows the pre-filter should have removed 40 slots earlier.
- **Fail-open on unreadable clocks** (`:1328`) behaves as documented.
- **Venue API internally consistent.** Rebuilding `status=active` from full history matched the
  live filtered fetch **exactly, 4,752/4,752 ids, zero disagreement.**
- **Cross-agent replication** (the numbers to trust): 50.7% silent-gate rate (5 measurements,
  range 50.6–50.7%) · score-cache census (4 reads, 69.2–69.3%) · `footprint + drop == 40`
  (4 readers, 2,277 cycles, zero exceptions).

---

## 5. WHAT REMAINS UNKNOWN

1. **Does footprint pool predict realized reward?** Nobody joined `credit_history` (n=46,
   complete) to per-market presence. Every impact figure here is in *pool* or *modelled capture*.
   **Settled by:** joining the 46 credit rows to `quotes-*.jsonl` presence-minutes per market.
2. **Whether a complete pre-filter actually yields 40 quoted markets.** Case B is a replay and
   does not model D2's 50.7% attrition or capital coupling. True post-fix footprint is between
   12 and 40, **UNVERIFIED**.
3. **Capital coupling at 40 markets.** INFERRED ~$18/market × 40 ≈ $720 vs `MAX_TOTAL_CAPITAL=350`.
   `cap_desired` is dormant only because footprint is 12; at 40 it arms — and it is the pool-keyed
   cut with no incumbency protection whose drops emit CANCELs via `diff_orders`. **Settled by:**
   reading `cap_desired` and `diff_orders` end-to-end plus a bounded shadow run.
4. **`INCUMBENCY_BONUS` inverted** — never assessed (truncation). Re-run that review.
5. **`explore_probe_capped`** = 1,062 / 1,064 / 2,059 across three agents, same log day. Unexplained.
6. **The $335.46 P&L residual.** Deposits $565.00 − withdrawals $0 + rewards $167.35 vs equity
   $288.23 ⇒ lifetime trading P&L −$444.12; fills (1,066) + settlements (101) = −$108.66.
   The "traced to the collateral leg of the YES-signed fills convention" claim was **not verified
   by any agent this session**. A $335.46 residual on a $565.00 account is 59% of deposits.
7. **Ban-before-close → $0 reward.** MUSKNW-T700 (modelled $48.15) and EURUSDAW ladder ($13.83)
   paid $0.00; MLABELSHARE banned later paid $16.15. **HYPOTHESIS, n=2 vs 1.** If true, a
   mid-event ban forfeits the entire accrued lump. **Settled by:** instrumenting the next event
   where a quoted strike is banned.
8. **Why the reward rate halved** (07-21..07-28 $16.36/day vs 07-29..08-01 $5.38/day). Confounded
   by `SCORE_UNKNOWN_BONUS` 1.0→0.06, the KXTEMP venue lapse, and the caps change.
   **No causal claim is supportable.**
9. **Submission truncation.** 8 agent submissions were cut mid-section; some defects are graded
   on a verdict table alone and are labelled as such.
10. **The venue denominator is not stable.** 4,752 programs / $297,586.67/day at 15:57Z →
    5,085 / $331,586.67/day at 16:30Z = **+7.0% count, +11.4% pool in 33 minutes.** Any
    "% of venue pool" figure carries that drift.

---

## 6. OPEN QUESTIONS FOR THE OPERATOR (no recommendations attached)

1. `MAX_DAYS_TO_CLOSE=8` excludes 76.9–84.5% of venue pool ($247,720.00/day at 16:40Z). Code
   default is 3.0; live is 8. Is the horizon itself the coverage decision?
2. Which coverage denominator — venue total ($298K/day, ±11%/half-hour), clock-reachable
   ($45,391.67/day), or clock-reachable-and-two-sided-payable?
3. `ALLOC_KEY`, `ALLOC_INCUMBENT_FIRST`, `PIVOT_SELECT` are deployed and dark. `:1361-1366` says
   `ALLOC_KEY=1` exists to fix D3. Were these meant to be live?
4. One market per series (D6) — intended egalitarian policy, or an accident of `FOOTPRINT_TOP=40`
   saturating in round 1?
5. `FOOTPRINT_TOP=40` vs `MAX_TOTAL_CAPITAL=350` — which should bind, and at what number?
6. `NETEV_GATE=1` with the calibrator absent produces an uncalibrated ">$15.00/day" filter that
   skipped 1,491 markets today while its docstring says it never blocks. Was it meant to be on
   before the calibrator shipped?
7. `MIN_PRICE_DOLLARS=0.04` / `MAX_PRICE_DOLLARS=0.96` excludes the whole longshot category
   including the venue's largest single pool ($1,000/day). Deliberate or inherited?
8. KXTEMP now prices at $20.00/day per program against an observed footprint floor of $100.00/day.
   Is a $20/day program worth one of 40 seats?
9. The KXTEMP lapse was invisible for 9.29 days while `programs_seen` rose. What class of
   venue-side change should the bot alarm on?
10. Should the $335.46 P&L residual be reconciled before or after the selection work?
