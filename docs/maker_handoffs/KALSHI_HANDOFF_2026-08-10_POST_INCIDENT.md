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
