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
