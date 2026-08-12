# KALSHI MAKER — HANDOFF 2026-08-10. BOT HALTED. PRIOR SESSION'S CONCLUSIONS ARE SUSPECT.

## 0. READ THIS FIRST — TRUST POSTURE

The operator's words at the end of the 2026-08-09 session: **"i have 0 faith in any work you have
done."** That is warranted. The prior session made two claims to the operator that were **wrong in
sign or internally inconsistent**, and its own restart caused a live-money incident.

**DO NOT INHERIT THIS SESSION'S CONCLUSIONS. VERIFY OR DISCARD THEM.**

What is safe to rely on (mechanical, reproducible by you):
- md5 comparisons against git blobs
- test pass/fail (run them yourself, capture the EXIT CODE, never grep)
- venue API reads with a timestamp you took
- predictions registered BEFORE the fact and checked after

What to distrust:
- Any prose conclusion, severity rating, or narrative from the 08-06 → 08-09 sessions
- The 147-finding adversarial review (`KALSHI_ADVERSARIAL_REVIEW_2026-08-06.md`), the triple-blind
  pass, and `KALSHI_RESOLUTION_PLAN_2026-08-07.md`. These were produced by fan-out agents and
  are probably *directionally* useful, but the summarizing layer (me) demonstrably mis-framed
  things. Treat every item as UNVERIFIED until you re-derive it.
- Any number not accompanied by a command you can run.

## 1. LIVE STATE — verified 2026-08-10T00:11:25Z (re-verify; stale by definition)

- **BOT HALTED.** `STOP` present. Service `active` but idle behind the sentinel (RUNNING ≠ LIVE).
- Book **flat**: 3 dust positions, **0 resting**, `n_fills_todate` 1484 (recorder 00:10:13Z).
- Cash **$270.9891**. Venue accrued-unpaid estimates **$6.0353 across 57 rows** (00:10:02Z).
- **Every live-path file is byte-identical to the pre-session state**, verified md5-vs-git-blob at
  commit `233ee86`: quoter `5c7aed6f` · ws_daemon `4aebb58b` · client `ee6ba04c` · ws_feed
  `5cd28300` · market_scores `f4ee97f0` · capital_rank `9b9bfd1e` · credit_feedback.py `2d7cf41e`.
- `kalshi_credit_feedback.json` = `bdeccf08` (pre-session; the prior session's rebuild was reverted
  and preserved as `.rebuilt-20260809-REVERTED`).
- **The prior session never changed the trading code on the box.** Its quoter commits are in git
  only, NOT deployed.
- Still differing from pre-session on the VPS: 4 OFFLINE tools only (`w16_successor_finder.py`,
  `w17_coverage_ledger.py`, `kalshi_estimates_recorder.py`, `w0b_payout_timing.py`). No trading
  path; none imported by the quoter. w16/w17 were BROKEN before (PermissionError since 08-06), so
  reverting them restores a crash, not a good state.

VERIFY IT YOURSELF:
```
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@18.201.216.0 \
  "sudo -n bash -c 'cd /opt/pa2-maker-kalshi-live; ls STOP; md5sum maker_kalshi_quoter.py; tail -1 cash-202608.jsonl'"
```

## 2. THE INCIDENT (2026-08-08T23:52Z → 2026-08-09T00:13Z)

Sequence, all from plan rows (`equity_mark_usd` / `equity_day_peak` / `daily_dd` — note these key
names; the prior session guessed `equity`/`day_peak_equity`, got None, and built a false
"the halt meter is blind" narrative on it):

```
23:52:05Z eq 275.16 peak None   dd None   <- restart (day-change re-baseline)
23:54:33Z eq 275.16 peak None   dd None   <- day_baseline_reset marker consumed = 2nd re-baseline
23:56:26Z eq 273.96 peak 275.16 dd 1.20
23:58:18Z eq 272.16 peak 275.16 dd 3.00   <- correctly tracking toward the $10 halt
00:00:09Z eq 267.61 peak None   dd None   <- UTC roll: peak re-seeded at the BOTTOM, dd erased
00:11:44Z eq 268.54 peak 268.71 dd 0.17
```

**Agent root cause:** the restart was executed 8 minutes before the 00:00Z UTC boundary. The
governor re-baselines at the boundary and re-seeded `equity_day_peak` at current equity, erasing an
open drawdown. **Operating rule: never restart within ~60 min of 00:00Z.**

**CRITICAL CAVEAT — the loss was much smaller than the equity meter implies.** The quoter marks
longs at the best BID (liquidation), so acquiring inventory on 28–42-tick books instantly books the
whole spread as "loss". Same inventory, same minute (00:02:2xZ): quoter `mkt_unreal_usd` **−$7.81**
vs venue cost-vs-value **−$0.97**. Venue-basis unrealized never exceeded −$1.87. Real economic
damage ≈ the **$2.63** of printed taker-cross cost + fees. The "−$7.55" figure is ~87% a
mark-convention artifact. **Any fix built on the $7.55 as if it were destroyed value is mis-aimed.**

**D4a UPDATE (2026-08-11, operator-ruled) — the canonical CLOSED-EPISODE realized is −$5.5078, not
$2.63; both are correct at different scopes, keep both.** The KXTRUMPTIME-26AUG15 episode is now
FLAT and COMPLETE (net position 0.0 in all 5 strikes). Position-aware `replay_fills` over the full
tape (read 2026-08-10T03:00:30Z; 1,484-fill tape reconciles to the recorder's `n_fills_todate`
1484) gives the round trip: 5 maker YES buys −$12.10 (23:56:31→23:58:59Z), 6 taker sells +$4.5222
(00:05:14→00:09:01Z), then 2 maker sells on H1 at 2026-08-09T03:06:04/06Z **+$2.0700** (a resting
flatten offset that filled ~2h53m AFTER the halt). **Episode realized = −$5.5078, final, fill-side
only (not decomposed into defect vs structural).** The **$2.63** is the printed taker-cross
*component*; −$5.5078 is the full realized round trip and is the number a future session must size
against. No credit has been paid for KXTRUMPTIME-26AUG15 (credit_history 2026-08-11T14:22:21Z: 0
credits for that event; peak accrued $0.0355 across its 5 markets; programs run to
2026-08-15T14:00Z, so not yet due). Reproduce: `replay_fills` over `/portfolio/fills`, filter
ticker prefix `KXTRUMPTIME-26AUG15`.

## 3. THE ONE OPEN QUESTION — this is the actual work

**The bot entered a series it had not touched in five days, immediately after the restart, and that
series produced the entire loss.** KXTRUMPTIME quote rows/day (ESTABLISHED, from `quotes-*.jsonl`):

| 08-01 | 08-02 | 08-04 | 08-05 | 08-06 | 08-07 | 08-08 | 08-09 |
|---|---|---|---|---|---|---|---|
| 814 (255 sized) | 0 | 0 | 0 | 0 | 0 | 20 sized | 20 sized |

Zero across ~89,850 quote rows over five consecutive days, then it reappears the moment the bot
restarts. Every loss-generating strand-cross in the journal was KXTRUMPTIME H1–H5, spreads
10/11/28/29/42 ticks.

**WHY did selection admit KXTRUMPTIME after the restart when it had ranked it out for 5 days?**
UNTESTED HYPOTHESES — stale close-cache / market scores after the 31h halt · ramp first-seen state ·
reentry-cooldown expiry (`KALSHI_REENTRY_COOLDOWN_S=3600`) · fresh hourly strikes ranking high on
pool. **This is the next measurement.** It matters because the loss happened on UNCHANGED trading
code, i.e. it was latent before anyone touched anything, and it will recur on the next restart.

Reproduce the table:
```
cd /opt/pa2-maker-kalshi-live && python3 -c "
import json,glob
for f in sorted(glob.glob('quotes-2026080*.jsonl')):
    n=t=s=0
    for line in open(f):
        try: d=json.loads(line)
        except Exception: continue
        n+=1
        if (d.get('ticker') or '').split('-')[0]=='KXTRUMPTIME':
            t+=1
            if (d.get('y_ct') or 0) or (d.get('n_ct') or 0): s+=1
    print(f, n, 'TRUMPTIME', t, 'sized', s)"
```

**Related but NOT established:** pairing collapsed post-restart (`inv_paired_frac` 0.0000 on 08-08
and 08-09 vs 0.2235 / 0.1597 / 0.1298 on 08-05/06/07; `two_sided_markets` 0). ⚠ DENOMINATOR: 4 and
7 quoting cycles vs 1000+ on normal days — **too small to claim a regime change**. Gate mix was NOT
a collapse (`gate_one_sided_book` 43.6% of 257 rows on 08-09 vs 34.2% of 33,011 on 08-07). The
prior session called one-sidedness "the actual mechanism" — that claim is NOT supported by the
sample and should not be inherited. Real key names: `two_sided_markets`, `one_sided_markets`,
`inv_paired_frac`, `inv_pairedness_measured` (181 keys in a plan row — enumerate, don't guess).

## 4. COMMITTED, NOT DEPLOYED — decide whether to keep or discard

On branch `claude/maker-kalshi-live`:
- `1d7e5be` w16/w17 read config from `os.environ` (both daily reports had been dead on
  PermissionError since 08-06). **DEPLOYED** (offline tools).
- `c10a13d` estimates-recorder atomic map write + corrupt-preserve + shrink guard. **DEPLOYED**
  (offline). Verified live: `map_status=ok`, 5003 programs preserved.
- `45500ab` + `0263aa4` w0b per-close-basis classification + transient-retry. **DEPLOYED** (offline
  study tool). Re-run result: 57/57 close-readable, 9 BEFORE_CLOSE all watertight, 0 AMBIGUOUS.
- `ee12958` + `635fc1b` **daily-loss governor: carry an open drawdown across a day/marker
  re-baseline; marker consumed at its own re-baseline; repayment latched on the monotone peak.**
  **NOT DEPLOYED.** Ships `KALSHI_DD_CARRY` **ON** by default.
  ⚠ **OPEN DECISION:** given §2's mark-artifact finding, DD_CARRY would bank a bid-mark artifact as
  standing debt (~$7.55 of "debt" against ~$1–2 of real loss), leaving little envelope for a day.
  **Consider fixing the mark basis first, or shipping DD_CARRY OFF.** Operator has not ruled.
- Suite at handoff: **1255 passed / 2 xfailed, exit 0** (run it yourself).

**Untracked, unfinished:** `kalshi_live/w18_credit_watch.py` — a credit-history watcher, never
tested, never deployed, not running. Finish it or delete it; do not assume it works.

## 5. KNOWN-GOOD RESULT (independent of agent judgment)

The D-A estimates-feed checkpoint **passed on a pre-registered number**: KXAPRPOTUS-26AUG07 estimate
row was **$1.6319**; the credit landed **$1.63** at 2026-08-09T05:14:11Z (credit_history read
2026-08-09T21:41:44Z; lifetime 58/$198.95 → 59/$200.58). **The estimates feed predicts its own
credit to the cent.** Timing: program ended 08-07T15:00Z → credited ~38.2h later, i.e. the SECOND
daily batch, not the first. FIX-H (KXTOPMODEL-26AUG31, programs ended 08-09T03:59:59Z) had not
credited as of that read — pending, not failed. **Re-check it.**

## 6. DEFECTS THE PRIOR SESSION INTRODUCED OR MISSED (fix or verify)

1. w16/w17 standing detector is **WEAKER than what it replaced**: pre-fix an unreadable config
   exited 1 → systemd `failed` (machine-visible); post-fix exits 0 with a `#` log line nothing
   parses. Restore a non-zero exit.
2. `test_w16_w17_env_source.py` w16 half is **host-luck**: `hasattr(w16,'DATA')` is False, so it
   never controls the fallback; it passes on Windows only because `/opt/...` is absent and would
   **FAIL on the VPS as root**.
3. Three more identical silent-empty file reads left in the same two files (w16:127-137,
   w17:102-108). The w17 one silently shrinks `proven`, the ledger's own universe.
4. A partial `KALSHI_*` environ yields `allowlist=[]` with **no alarm**.
5. The credit-feedback rebuild bypassed the builder's own paid→convicted alarm (built to `/tmp`;
   the alarm reads its baseline from the `--out` path). Also 4 series vanished / 2 appeared and were
   not reported.
6. P2 verdict window is badly damaged (~30h dead from the 08-07 halt + this incident + a
   mid-window restart). Operator extended credit-observation to 2026-08-11T14:13Z but has not ruled
   on whether to void and re-run the window.

## 7. BINDING RULES (unchanged)

All 13 hook-injected operator rules bind. Plus: never restart within ~60 min of 00:00Z; the 2026-07-27
session stays quarantined; money-path changes need failing-before tests + md5-verified deploys with
`.bak` backups; nothing deploys to the trading path without explicit operator naming.

## 8. SESSION 2026-08-11 — M-1 CENSUS, MATCHED STUDY, FIX-H VERDICT (findings only, nothing deployed)

Step Zero re-verified this session: STOP present, quoter md5 `5c7aed6f…`, credit_feedback
`bdeccf08…`, suite **1255 passed / 2 xfailed exit 0** (captured, not grepped). Live-path files still
byte-identical to `233ee86`.

**THE OPEN QUESTION (§3) IS ANSWERED — the premise was wrong.** Selection never "ranked KXTRUMPTIME
out for 5 days." The five `KXTRUMPTIME-26AUG15-H1..H5` liquidity programs were CREATED at
`2026-08-08T16:01:52.885728Z` (venue read 2026-08-10T00:50:21Z), i.e. DURING the 31h halt; the
market opened `2026-08-08T14:00:00Z`. The first live cycle that could see them WAS the first cycle
after the restart. The 5-day gap was a DIFFERENT event (`KXTRUMPTIME-26AUG08`, closed
2026-08-08T13:03:52Z) that had NO active liquidity program in any record (program map 5,299 progs =
0 for 26AUG08; persistent close-cache = 0). Admission was NOT competitive: KXTRUMPTIME is in
`KALSHI_SERIES_ALLOW` (allowlist bypass), the per-series coverage floor is uncapped
(`PIVOT_COVERAGE=1`, quoter :2286-2289), and pool p50 (45) < capacity (FOOTPRINT_TOP 40). Caprank
cycle 1 shows it at `kind:"unknown", ref:null, cap_score 0.4` — it out-ranked nothing; it landed on
the coverage floor at index 8 of 11 distinct series. ALL FOUR handoff hypotheses REFUTED as cause
(stale close-cache / market-scores / ramp-first-seen / reentry-cooldown=0 on every incident cycle).
Recurrence is NOT restart-conditional: any new event in any of the 24 allowlist series is quoted on
the first cycle its programs appear.

**M-1 CENSUS (frozen; census 2026-08-10T02:52Z, canonical replay_fills read 02:56:07Z — 1,484
fills / 195 settlements matches recorder n_fills_todate):** all 24 allowlist series, every ticker
first-seen ≥2026-08-01. **394 new tickers admitted; 346 (87.8%) NEVER sized ($0, book gates
blocked them: gate_entry_band 140, gate_one_sided_book 126, gate_wide_or_asym 35, presence_skipped
26); 48 ever sized; 35 sized on their FIRST footprint row across 20 events.** KXTRUMPTIME was
mid-pack, not an outlier: cohort A (first-row-sized) realized −$37.8575 over 35 tickers; KXTRUMPTIME
5/35 = −$5.5078. Worst single first-entry ticker = `KXTEMPAUSH-26AUG0203-T81.99` −$9.4939 on 2 fills
in 108s (it sits in `mkt_out`). Target-conditioned first-row sizing: **target-300 6/13 (46%) vs
target-1000 29/381 (7.6%)** — but 7 KXTRUMPENDORSEMENTS-26AUG07 target-300 markets were NEVER sized
over 1,465–2,341 rows each, so Target is not sufficient. Reward leg (credit_history
2026-08-11T14:22:21Z, 62 credits/$204.06): of 42 new events, **3 have paid credits** (KXTEMPAUSH-
26AUG0202 $3.68, KXTOPMODEL-26AUG31 $2.46, KXADJOURNRECESS-26AUG $1.02) — the estimates feed
predicted each to the cent. Reward is a LAGGING receipt feed; not an earnings statement.

**D1 (operator-ruled A1): TWO items, both live. Item-admission** = "the uncapped per-series
coverage floor seats every allowlisted series on sight" (unchanged). **NEW Item-sizing** = "a
brand-new allowlisted market is SIZED on its first footprint cycle at ramp size (5ct) with zero
score, zero history, and a book the model has never measured." The census shows sizing, not
admission, is where the cost lands (346 admissions = $0). Both stay; neither demoted.

**D2 (operator-ruled B3): matched A-vs-B timing study is NOT ESTIMABLE — and it is a
null-because-UNMEASURABLE, NOT a null-because-tested-and-absent. Do not read it as "timing has no
effect".** Frozen input `study_frozen.json` (md5 `ab239530…`), deterministic `matched_study.py`,
exact permutation (no RNG). Cohort B (sized-later) lives ENTIRELY in one series — KXAAAGASD (13/13);
9 of A's 10 series have no B counterpart, so the contrast is unidentified for every non-gas type.
Matched pool (settled + series-in-both) collapses to gas: A n=4 mean −$1.7932 vs B n=13 mean
−$1.1172; A−B = −$0.676 (A nominally worse). Exact permutation on the 17-ticker gas pool (2,380
assignments): **two-sided p = 0.2651** — indistinguishable from label noise, and n_A=4 from only 2
events / 3 entry timestamps is essentially powerless. The matching binds ONLY on series (target
1000 and two_sided_qual are constant in the pool); the pool is SHARPLY UNBALANCED on everything
else — n_book_df 760.99 vs 104.15 (**7.31×**), y_px 0.665 vs 0.435, and cohort is perfectly
confounded with the entry gate (A all `explore_probe_capped`, B all `d3_ramp_capped`/None) and with
day/event (A only AUG02/06, B mostly AUG07/08). So the −$0.68 "timing" gap is inseparable from a
sizing-pathway and a between-day-regime effect. ⚠ OUTCOME IS COST-ONLY: `realized` = fill_cash +
settle_revenue; the reward/rebate leg is NOT joined, so this does not say either cohort was
profitable — and the reward is DIFFERENTIALLY censored (all 4 matched-pool A entered 08-01/08-05,
before the estimates tape began 08-06T03:31:49Z → reward unobservable; only 3/13 B pre-tape), so a
reward join cannot rescue A's comparison either. ⚠ THE −$0.68/ticker "A worse" gap is a VOLUME
ARTIFACT, not execution quality: cost-per-fill is near-identical (A −$0.1435 over 50 fills vs B
−$0.1529 over 95 — B nominally WORSE per fill); A just traded more (12.5 vs 7.3 fills/ticker). ⚠
Cohort is PERFECTLY confounded with sizing pathway (A 100% `explore_probe_capped`, B 100%
`d3_ramp`/None) — so even within gas, "timing" and "explore-probe-vs-d3-ramp sizing" are the SAME
variable and no clean timing effect is identifiable. The raw "A≈B" pooled number (A −$1.08, B −$1.12
per sized ticker) was mixing gas with temp/topmodel/trumptime. **Verified by an 8-agent adversarial workflow (3 blind re-derivations +
3-lens refutation + completeness critic + synthesis): verdict B3 HOLDS, high confidence; every
anchor number independently reproduced; no refutation conclusion-changing.** (Workflow honesty note:
my first run had a schema typo that errored the 3 dedicated re-derivers; the synthesis agent
independently reproduced all 7 numbers, and a resume re-ran the 3 re-derivers clean.)

**D3 (operator-ruled C2): FIX-H VERDICT = PASS, window closed.** `KXTOPMODEL-26AUG31` credited
**$2.46** (2 credits, 2026-08-10T18:25:53Z), ~38.4h after programs ended 2026-08-09T03:59:59Z — the
SECOND daily batch, not September, matching the pre-registered prediction. Accrued-estimate row was
$2.4691 → credited $2.46 (CLAUM $1.4187→$1.41, CLAU5 $1.0504→$1.05): the estimates feed predicted
its own credit to the cent AGAIN (second D-A checkpoint pass on a pre-registered number, independent
of agent judgment). The 48h envelope (closed 2026-08-11T03:59:59Z) and operator observation deadline
(2026-08-11T14:13:00Z) are both PAST; this line is terminal.

**⚠ FLAGGED, NOT RULED (Rule Nine):** the census pointed the mechanism at first-cycle SIZING rather
than admission — reported as D1's new item, admission item untouched. The P2 verdict window
observation deadline (2026-08-11T14:13Z) has now passed; whether to void/re-run it (handoff §6.6)
is still unruled. FIX-H position capital stays locked to close 2026-08-31 even though its reward
timing passed.

## 9. NET-EV DATA FOR THE SIZING ITEM + PLAN (2026-08-11 cont., findings + plan only)

**NET-EV (leg 1, `net_ev_and_counterfactual.py` over frozen local files; reward read
2026-08-11T14:55:38Z; verified by 2 independent workflow agents, all anchors matched):** cohort A
(first-cycle sized) cost **−$37.8575/35 tickers**; reward is barely observable — accrued estimate
covers only **12/35** (29 entered before the estimates tape existed, 2026-08-06T03:31:49Z), summing
**$3.9784**; only **3 of 23** sized events earned any credit (**$7.16** total, event-level, NOT
divisible to a ticker: KXADJOURNRECESS-26AUG $1.02, KXTOPMODEL-26AUG31 $2.46, KXTEMPAUSH-26AUG0202
$3.68). ⚠ CORRECTION to a chat-only mis-scope: the **SIZED (August) gas events earned $0 credits**,
but the **KXAAAGASD series earned $33.04 in JULY** (26JUL21 $2.15, 23 $10.09, 24 $8.81, 25 $11.99;
last 2026-07-25) — gas was a credit-PROVEN series whose fresh August daily strikes we sized paid
$0, which STRENGTHENS the "proven-series, unobserved-fresh-ticker" class, not weakens it.
**COUNTERFACTUAL (leg 2, 346 never-sized):** 165 one-sided at first sight ($0-payable regardless,
correctly skipped), **181 two-sided qualifiable** ($24,733/day pool) skipped with
`gate_entry_band` present on **112/181** first rows (self-review F5: computed via a one-off Bash
cross-tab this session, NOT frozen in `netev_cf_frozen.json`, and gate buckets can co-occur per
ticker, so read it as "gate_entry_band present on 112" not a clean partition — price outside our band
= price discipline, NOT free money); total $46,510/day pool represented; the 181's reward
counterfactual is NOT readable from any frozen tape (0/181 in the participation-gated feed) — it needs
a dry shadow-quote log.
**⚠ SCOPE CLARIFICATION to §8's "cohort perfectly confounded with pathway":** that is exact only in
the n=4 MATCHED gas pool (gas-A there is 4/4 `explore_probe`). At FULL-cohort level **A is 26
`explore_probe` / 8 `d3_ramp` / 1 other**, B is 11 `d3_ramp` / 2 other — so a full-cohort stratified
de-confound IS feasible (plan step 3).

**PLAN (6-agent design panel: verify + 3 approaches + synthesis; confidence high). 10 OFFLINE steps
first, 3 LIVE-PATH proposals gated behind a pre-registered warrant — nothing armed without operator
naming.** OFFLINE spine: (1) calibrate accrued-estimate→paid-credit ratio + leakage list; (2)
tape-covered apples-to-apples net-EV (both cohorts on/after tape0, A:6/B:10) — the fair test; (3)
full-cohort de-confound first-cycle × pathway × series × two_sided, re-run gas permutation; (4)
cost decomp fill_cash vs settle (do not inherit the −$7.55 bid-mark pattern); (5) est-feed
warmup-latency vs market life (the hazard: warmup > short-daily life ⇒ observe-first becomes a
de-facto bench = drips violation); (6) sensor-validation accrued→paid; (7) two standing detectors
(blind-at-sizeup coverage bucket + fresh-sizing burst detector, KXTRUMPTIME cyc=1786233125 the
proof case); (8) F-sweep + concurrency cap-vs-cost curve; (9) delay-to-observability cost + dry
shadow-quote harness for the 181; (10) pre-register the warrant threshold. LIVE-PATH proposals
(operator-named only if step-10 threshold met, one fix per commit, flag defaults byte-identical,
failing-before tests): **A (primary)** — layer a per-TICKER observability HOLD on the D3 ramp that
clamps a fresh ticker to probe size until the estimate feed COVERS it AND accrued≥F, consuming
`KALSHI_EST_FEED` as a **size-lowering BLOCKING READ only** (NOT the expected-credit floor the HELD
gate uses) — ships WITHOUT the flat-activate/sub-Target hazard and WITHOUT M-9/M-6/M-10; **B
(secondary)** — per-event fresh-market concurrency cap that DEFERS (never bans) excess same-cycle
fresh sizings above K≈2, wrapping BOTH pathways; **C (fallback)** — Tier-1/2 tune of
EXPLORE_PROBE_CT / D3 cycle-1 size. Plan detail: session chat + `net_ev_and_counterfactual.py`,
`netev_cf_frozen.json` (md5 `aa9fd970…`).

**OFFLINE STEPS 1–11 RUN (operator "run"; `offline_steps.py`, `offline_results.json` md5
`deaeed07…`, est-timing `est_timing.json`; 4-agent verify: 2 blind re-derivers + skeptic + synth,
ALL anchors reproduced, high confidence). RESULT: NO live change on the first-cycle-sizing item —
but for a SHARPER reason than "no penalty".** Steps: (1) accrued→paid ratio **min 0.48 / median 0.99
/ max 1.00** over the 3 paid events (self-review F3: I earlier quoted only the median — **KXAPRPOTUS-
26AUG07 over-predicts 2×: accrued $3.3881 vs paid $1.63 = 0.481**; the binary filter misses partial
over-prediction, so accrued→paid fidelity is **UNVALIDATED for a floor gate**); 3 full-leakage events
(accrued>$0.5, paid $0: KXACTBLUETOP-26AUG07 $1.24, KXAAAGASD-26AUG07 $1.12, -26AUG08 $0.92). (2)
tape-covered net A −$9.5147 (n=6) vs B −$9.6083 (n=10). (4) cohort-A cost = fill_cash −$38.0675 +
settle +$0.21 = **~100% real cross cost, NOT a mark artifact.** (5) **0 of 16 post-tape sized tickers
ever reached the $1.20 accrued floor.** (7) **16/16 blind at size-up** (we always size before the feed
can see the market). (8) concurrency avoidable-cost is a RANGE, not a point (self-review F6 — my
earlier "−$4.91" was the cost-MINIMIZING ordering): K=1 **defer-mildest −$4.91 / random-arrival
≈−$10.79 / defer-worst −$16.62** of −$37.86; a real cap defers by arrival, so ≈−$10.79 is the honest
expectation — Proposal B is NOT as low-value as −$4.91 implied. (10) warrant NOT met (C1 net<−$0.50
true; **C2 p<0.05 FALSE at 0.4446**; C3 delay-cost DEFERRED/9a).
**⚠ THREE CORRECTIONS the verification forced (adopt these; my step-2 draft framing was wrong):**
(i) The no-change holds because the first-cycle effect is **UNIDENTIFIABLE + underpowered**, NOT
because "A≈B, no penalty" — within d3_ramp (the only pathway where cohort varies) cohort A is 100%
non-gas and B is 100% gas, zero series overlap, so p=0.4446 is a gas-vs-non-gas contrast mislabelled
as first-cycle. Absence of evidence, not evidence of absence. (ii) The "A≈B net parity" is an
**accrual artifact**: cohort B's entire +$2.041 covered reward is KXAAAGASD-26AUG06/07/08 accrued,
all events **paid $0**; on a PAID basis B reward=$0 and A is worse per-ticker (−$1.586 vs −$0.961).
(iii) **Proposal A is NOT contraindicated** — the 16 never-floor tickers are 100% net-negative with
$0-paid accrual, so a floor/accrual gate would likely SAVE ~$19–21, not bench payable markets;
**re-evaluate Proposal A on a PAID (credit_history) basis** before any verdict.
**⚠ STEP 11 — DISTINCT NEW FINDING (additive, report+ask, Rule Nine — the first-cycle item is NOT
demoted):** SERIES/MARKET SELECTION is the actual loss driver. By-series realized over the 48 sized:
KXAAAGASD **−$21.6965** (n=17), KXTEMPAUSH −$12.9029 (n=9), KXTRUMPTIME −$5.5078 (n=5),
KXGENERICBALLOTVOTEHUB −$4.17 (n=1), KXTEMPDCH −$3.0083 (n=3), KXTOPMODEL −$2.1823 (n=3),
KXADJOURNRECESS −$1.9377 (n=1), + small temp; **every sized series net-negative on realized; only
KXTOPMODEL turns net-positive (+$0.5151)** — via reward that is **PAID, not unpaid (self-review
correction F1): its $2.4691 accrual was CREDITED $2.46 on 2026-08-10 (= the D3 FIX-H credit).** ASK:
should selection/accrual-based series gating be measured as the PRIMARY lever, ahead of anything
first-cycle-specific?
**⚠ SERIES-SELECTION MEASURED WITH THE REWARD LEG (operator "proceed"; `series_netev.py`,
`series_netev.json`; credit_history read 2026-08-11T14:55:38Z; Table 1 sums to $204.06 = lifetime
total, exact) — the cost-only "series is the loss driver" is DIRECTIONALLY reframed (self-review
correction F2 downgraded the earlier "REFUTED ON A PAID BASIS" verb): these series HAVE paid us
historically, but on the MATCHED scope the fresh Aug strikes themselves paid $0 while costing ~$52
realized — so this is "the series pays historically", NOT "keep sizing the fresh strikes".** Every
series we sized fresh Aug strikes in has paid us lifetime credits, and **8 of 10 have lifetime paid >
|fresh-strike cost|**: KXAAAGASD fresh −$21.70 vs paid **$33.04** (all July), KXTEMPAUSH −$12.90 vs
**$24.80**, KXTEMPNYCH −$0.52 vs **$17.99**, KXTEMPCHIH −$0.31 vs $8.21, KXTEMPDCH −$3.01 vs $5.86,
KXTRUMPTIME −$5.51 vs $7.90, KXTOPMODEL −$2.18 vs $4.61, KXTEMPLAXH −$0.15 vs $1.85. Only TWO invert
(fresh cost > lifetime paid), both n=1: **KXGENERICBALLOTVOTEHUB −$4.17 vs $2.33** and
**KXADJOURNRECESS −$1.94 vs $1.02** — candidates for a look, not conclusions. ⚠ SCOPE (Rule Six):
lifetime-paid is EVENT-level over ALL our activity in the series and mostly JULY; fresh-cost is the
census-Aug strikes only — DIFFERENT tickers/months/cost-bases, so this is DIRECTIONAL ("does the
series ever pay?" = yes for 8/10), NOT a net-EV (July fill costs not loaded). So these are
REWARD-POSITIVE series with a fresh-strike reward-TIMING/observability cost, not "bad series". One
unparsed non-liquidity credit ($15.00, 2026-07-24) excluded from series attribution.
**PROPOSAL-A PAID RE-EVAL (`series_netev.json`) — SPLIT concluded vs pending (self-review F4; my flat
"$0 forgone, benches non-payers" conflated leakage with pending):** the 16 post-tape never-floor
tickers = **10 CONCLUDED (settled; cost −$11.6493; events truly paid $0 = genuine non-payers)** + **6
PENDING (unsettled; cost −$9.6778, part mark-based/not final; not due until Aug 14/15:
KXGENERICBALLOTVOTEHUB-26AUG14 + 5× KXTRUMPTIME-26AUG15) — their $0 is PENDING, NOT proven
non-payment.** So a floor gate benches proven non-payers for the 10 concluded (saves −$11.65, forgoes
$0); the 6 pending are unresolved. Proposal A is NOT contraindicated, but the "saves ~$21" figure is
an upper bound resting partly on not-yet-due markets. ⚠ THE GATE'S KEY UNVERIFIED RISK: whether the
$1.20 accrued floor actually SEPARATES payers from non-payers is UNVERIFIED — no pre-2026-08-06
estimates tape (July PAYING gas strikes' accrual unseen), only 3 paid events inside the tape, and the
accrued→paid sensor itself over-predicts 2× on KXAPRPOTUS (F3). Do not arm a floor gate until that
separation is validated on a paid basis.
**DEFERRED (not dropped):** step 9a (book-better-later, needs box quotes scan) + 9b (181-ticker dry
shadow-quote replay) — so warrant C3 stays unevaluable and the no-change is a conservative default,
not a closed proof.

**⚠ SELF-REVIEW (operator "review and report"; 5-agent adversarial pass over this session's scripts +
claims; extraction foundation independently re-counted from raw box tapes — 394/48/35/42 EXACT match;
live state re-verified 2026-08-12T00:03:28Z: STOP present, quoter `5c7aed6f`, cash $274.4691, 0
resting, n_pos 0). RAW NUMBERS ALL SOUND; 6 FRAMING DEFECTS FOUND + CORRECTED IN PLACE ABOVE:** F1
(KXTOPMODEL net-pos was via PAID not "unpaid" reward — contradicted my own D3), F2 (the "series is the
loss driver REFUTED on paid basis" verb overstated a directional, cross-month/cross-ticker comparison
— downgraded), F3 (I quoted accrued→paid median 0.99 but hid min 0.48 / KXAPRPOTUS 2× over-predict —
sensor now flagged UNVALIDATED), F4 (Proposal-A "$0 forgone, benches non-payers" conflated 6 pending
markets with 10 concluded non-payers — split), F5 (112/181 is a session Bash cross-tab, not frozen;
gates co-occur — relabelled), F6 (Proposal-B "−$4.91 low value" was the cost-MINIMIZING ordering;
honest range −$4.91/≈−$10.79/−$16.62). None changed a headline CONCLUSION (first-cycle no-change
holds; series pay historically; Proposal A needs paid-basis floor-separation validation), but four
were operator-decision-relevant framing errors. The load-bearing figures were reproduced to precision
by independent blind re-derivers earlier and by the review's own recompute.

## 10. OPERATOR RULINGS 2026-08-12 ("all proceed as planned") + PRE-REGISTRATIONS + RESTART PACKAGE

**Interpretation on the record (auditable):** the operator approved the 6-item action plan
(A–F, presented 2026-08-12 session) with "ok all proceed as planned". That adopts the plan's
recommended defaults for A and B, authorizes building D/E/F, and does NOT itself start the bot —
the standing directive ("halted until I explicitly name a restart") plus the plan's own wording
("name C") reserve the start command. **C therefore still requires one explicit operator word.**

**RULING A (adopted default): P2 VERDICT WINDOW = VOID.** The 2026-08-05T14:13:28Z →
2026-08-10T14:13Z window (credit-observation extended to 08-11T14:13Z) is VOID — ~30h dead from
halts + a mid-window restart; no verdict is scored from it. Its replacement is the pre-registered
window below, which starts at the next restart. The drag data collected in P2 remains on record,
unscored, never re-decomposed.

**RULING B (adopted default): DD_CARRY (`ee12958`+`635fc1b`) stays committed, NOT DEPLOYED.** No
deploy bundled with a restart. Consequence accepted and on record: the 00:00Z forgiveness hole
(quoter day-rollover re-seeds `equity_day_peak` unconditionally) REMAINS OPEN on the box;
mitigations = never restart within ~60min of 00:00Z + the daily $10 halt re-arming each day.
Fixing the mark basis first, then re-deciding DD_CARRY, stays an open item.

**PRE-REGISTRATION 1 — measurement window (replaces P2; registered BEFORE any result is visible):**
- T0 = the moment of the next operator-named restart. Window = T0 → T0+7 days (presence + drag).
  Credit observation to T0+7d+48h (the observed payment envelope; FIX-H paid at ~38.4h).
- CREDITS gauge = credit_history rows whose PROGRAM concluded inside [T0, T0+7d], observed to the
  deadline. Accrued-but-unconcluded at window end is REPORTED alongside, never counted.
- DRAG gauge = position-aware replay_fills delta over [T0, T0+7d] (fills + settlements), the
  recorder's basis; bid-mark artifacts excluded by construction.
- VERDICT RULE (same as P2's): PASS iff counted credits > |drag|. Halts inside the window do not
  extend it (uptime is part of what is being measured).
**PRE-REGISTRATION 2 — OBS_HOLD arming gate (E; the floor-separation validation):**
- Sample: fresh allowlist tickers first sized after T0 whose event's programs CONCLUDE before
  evaluation; minimum n=10 concluded fresh-ticker events AND >=2 events that actually PAID >=$1
  (else UNPOWERED — keep collecting, do not arm).
- FALSE-BENCH rate = fraction of eventually-PAID (>=$1) events in the sample where NO strike of
  the event reached accrued >=$1.20 (per recorder tape) while fresh. PASS iff false-bench <=10%.
- SENSOR-FIDELITY check: for every PAID event in the sample, paid >= 0.5 x accrued-at-conclusion
  (the KXAPRPOTUS 0.481 shape is the known floor). FAIL -> re-derive the floor constant before
  arming; do not arm on the default $1.20.
- Evaluation is run from `reward_pnl_report.py` output + the estimates tape; result goes to the
  operator with the arming decision — ARMING IS A SEPARATE NAMED DEPLOY in all cases.

**RESTART PACKAGE (C) — everything staged; needs ONE operator word + a time:**
1. Preconditions: >60min from 00:00Z; no `day_baseline_reset` marker; env UNCHANGED (OBS_HOLD
   stays 0/absent; EST_FEED stays 0; no new flags); **NO DEPLOY** — the box runs the verified
   pre-session code (quoter `5c7aed6f` = `233ee86` blob); DD_CARRY not deployed (Ruling B).
2. Sequence: archive STOP (operator-named clearing — the STOP is an auto-halt class) → start
   service → verify first cycle (plan row: footprint>0, `reentry_cooldown` restored, equity/peak/dd
   sane) → record T0 in the handoff.
3. Watch protocol, first 30 min: plan rows each cycle; any fresh-ticker entries expected at 5ct
   ramp scale (OBS_HOLD is OFF — the fresh-strike drip class is ACCEPTED for this window per the
   first-cycle no-change ruling); daily-loss halt armed at $10.
4. Daily during the window: run `reward_pnl_report.py` (manual root run or a timer — timer install
   is a deploy, name it); watch LEAKAGE + PAID_PARTIAL rows; weekly E-evaluation per
   Pre-registration 2.

**BUILT DARK THIS SESSION (commits `122dd44`, `42f06ed`; suite 1274 passed / 2 xfailed exit 0):**
OBS_HOLD (Proposal A) behind `KALSHI_OBS_HOLD=0`, 10 failing-before pins, blocking-read-only,
fresh-window-scoped fail-closed, `_d3_est_ct` budget parity, `obs_hold_bound` telemetry;
`reward_pnl_report.py` (item F) with 9 pins, PENDING-never-LEAKAGE classifier, PAID_PARTIAL
over-prediction flag. Neither is deployed; the box is byte-identical to before this session.
