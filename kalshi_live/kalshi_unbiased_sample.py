#!/usr/bin/env python3
"""UNBIASED SAMPLE — sandbox, READ-ONLY, NO KEYS, NO MONEY, NEVER TRADES.

WHY THIS EXISTS (canon KALSHI_LIP_RULE_CANON.md §M6b — a self-caught defect):
`kalshi_concentration_study.py:133` does `if not yl or not nl: continue`, so any
contract with an EMPTY book side never entered `concentration_samples.jsonl`. Two
committed numbers are therefore CONDITIONAL on both sides being non-empty:
  * §M2's "86.1% two-sided"  — a pre-filtered denominator
  * §M1's capture figures    — scored only over books that had two sides

This script re-samples the SAME allowlist with the filter REMOVED. Every contract with
an active incentive program is recorded, empty side or not.

THE FROZEN FILE IS NOT TOUCHED. `concentration_samples.jsonl`
(md5 e920bf99850279099897a79e8ad78dec) is what the committed numbers refer to and must
stay reproducible. This writes a NEW file, `unbiased_samples.jsonl`.

=== THE CONFOUND, AND THE DESIGN THAT REMOVES IT ===
The frozen dataset was taken 02:25-02:48Z. This one is taken later. Canon §M6a measured
two-sided coverage COLLAPSING over exactly those hours (79.0% -> 46.4% on the A/B ON arm
between 02:05Z and 03:13Z). So a naive "new number vs committed number" comparison
conflates TWO effects:
    (1) removing the selection bias   <- what we want to measure
    (2) overnight liquidity drift     <- confound, nothing to do with the bias

So the report scores THREE arms on data captured at the SAME instants:
    A. NEW / FILTERED    — this dataset with the frozen study's `not yl or not nl` filter
                           re-applied. This is what the OLD METHOD would say NOW.
    B. NEW / UNFILTERED  — this dataset, all contracts. The unbiased answer.
    C. committed figures — quoted from canon §M1 for reference only.
  A vs B  = THE SELECTION BIAS, time held constant.   <- the clean measurement
  C vs A  = time-of-day drift.                        <- the confound, isolated

=== R3 IS ALSO REPORTED BOTH WAYS ===
The frozen study never applied R3 (two-sided exclusion). Its capture figures score a
one-sided snapshot at its reduced one-sided share instead of at ZERO. Canon §M1 flags
this as a KNOWN DOWNWARD BIAS. That was defensible when the dataset had no empty sides;
once empty-side contracts are admitted it is the dominant modelling choice, because
under R3 those snapshots pay NOBODY. Both are printed:
    noR3 = payout pool*(ys+ns)/2, exactly the frozen study's method (apples-to-apples)
    R3   = market-level two-sided test on the BOOK ALONE; excluded snapshot pays $0
Per canon §M2 our 20 ct was marginal to two-sidedness in 0/304, so testing the book with
or without our orders is empirically the same test here.

=== WHAT THIS DOES NOT COVER ===
  * REWARD SIDE ONLY. Fill rate and adverse selection are NOT simulatable without queue
    position. Concentration is strictly worse on exactly that unmeasured axis.
  * Instantaneous snapshots; competitors requote; programs churn hourly.
  * Allowlist only (7 series). Says nothing about the other 185 series.
  * One time-of-day. Canon §M6a proves this population is strongly time-varying, so
    these rates are a POINT ESTIMATE at the sampling window, not a steady state.

Run:     python kalshi_unbiased_sample.py [minutes]
Report:  python kalshi_unbiased_sample.py --report
"""
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "unbiased_samples.jsonl")
PUB = "https://api.elections.kalshi.com/trade-api/v2"
ALLOW = ("KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH",
         "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH")

# identical to the frozen study's constants so the K-sweep is comparable
TOTAL_CAPITAL = float(os.environ.get("CONC_TOTAL_CAPITAL", 85))
MAX_MARKET = float(os.environ.get("CONC_MAX_MARKET", 15))
JOIN_SIZE = float(os.environ.get("CONC_JOIN_SIZE", 20))
MIN_PAYOUT = float(os.environ.get("CONC_MIN_PAYOUT", 1.00))
TICK = 0.01
SPACING_S = 0.35          # >= 0.3s public-API spacing, per lane constraint
SLEEP_S = float(os.environ.get("UNB_SLEEP", 20))
_last = [0.0]

# canon §M1, frozen dataset — quoted for the drift comparison, NOT recomputed here
COMMITTED = {"oracle": (6, 99.43, 70.16, 8.30), "asis": (7, 84.83, 70.16, 0.07)}


def _load_scoring():
    """Import the recorder's PURE scoring functions (no side effects, no auth)."""
    import importlib.util
    for cand in (os.path.join(HERE, "..", "scripts", "maker_kalshi_recorder.py"),
                 os.path.join(HERE, "maker_kalshi_recorder.py")):
        if os.path.exists(cand):
            spec = importlib.util.spec_from_file_location("_rec", cand)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise SystemExit("maker_kalshi_recorder.py not found (need its CFTC scoring core)")


REC = _load_scoring()


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-unbiased/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def levels(raw):
    out = []
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out.append((p, s))
    return out


def event_of(ticker):
    """ONE CORRELATED RISK — canon §T, same grouping as maker_kalshi_quoter.py:1591."""
    return "-".join(ticker.split("-")[:2])


def sample_once():
    """One paired snapshot of EVERY in-allowlist contract that has an active program.

    THE ONE LINE THAT MATTERS: unlike kalshi_concentration_study.py:133 there is no
    `if not yl or not nl: continue`. A contract with an empty side is RECORDED with an
    empty list, because its absence is precisely the bias being corrected."""
    progs, cur = [], ""
    for _ in range(8):
        d = get("/incentive_programs?status=active&limit=1000" + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    ours = [p for p in progs if (p.get("market_ticker") or "").split("-")[0] in ALLOW]
    rows, skipped = [], 0
    for p in ours:
        t = p.get("market_ticker")
        target = float(p.get("target_size_fp") or 0)
        df = float(p.get("discount_factor_bps") or 0) / 10000.0
        pool = float(p.get("period_reward") or 0) / 10000.0
        if target <= 0 or df <= 0 or pool <= 0:
            skipped += 1
            continue
        try:
            ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
        except Exception:
            skipped += 1            # a FETCH failure is not an empty book; drop it
            continue
        yl, nl = levels(ob.get("yes_dollars")), levels(ob.get("no_dollars"))
        rows.append({"t": t, "target": target, "df": df, "pool": pool,
                     "start": p.get("start_date"), "end": p.get("end_date"),
                     "yl": yl, "nl": nl})
    return rows, len(ours), skipped


def window_days(row):
    """Time Period length in days. R1: period_reward is the TOTAL for the window, not a
    rate; windows in this allowlist run 13.15h vs 156.08h on identical $100 pools."""
    try:
        a = datetime.fromisoformat(row["start"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(row["end"].replace("Z", "+00:00"))
        d = (b - a).total_seconds() / 86400.0
        return d if d > 0 else None
    except Exception:
        return None


def classify(row):
    """Market-level book state. R3 tests THE BOOK, not our orders.
    Returns 'ok' | 'empty' | 'depth' — the same partition canon §M6a reports."""
    yl, nl = row["yl"], row["nl"]
    if not yl or not nl:
        return "empty"
    y_ok = REC.qualifying_walk(yl, row["target"])[0] is not None
    n_ok = REC.qualifying_walk(nl, row["target"])[0] is not None
    return "ok" if (y_ok and n_ok) else "depth"


def score_market(row, dollars, apply_r3):
    """Payout for ONE contract at `dollars` of capital, quoted at reference both sides.

    SIZE MODEL (unchanged from the frozen study, and it is the part that is easy to get
    catastrophically wrong): size_ct = min(JOIN_SIZE, half_dollars/price). Capital does
    NOT convert to size at cheap strikes — JOIN_SIZE binds first.

    EMPTY-SIDE HANDLING — the new case. A side with no book has no reference price, so
    we cannot rest at reference and that side's share is 0. Under noR3 the contract still
    earns pool*(ys+0)/2 from the live side; under R3 the snapshot is EXCLUDED and pays
    zero to everyone, us included."""
    yl, nl = row["yl"], row["nl"]
    if apply_r3 and classify(row) != "ok":
        return 0.0, 0.0, 0.0, 0.0
    ys = ns = 0.0
    ct = 0.0
    half = dollars / 2.0
    if yl:
        by = max(p for p, _ in yl)
        if by > 0:
            cy = min(JOIN_SIZE, half / by)
            ys = REC.side_share(yl, [(by, cy)], row["target"], row["df"], TICK)[0]
            ct += cy
    if nl:
        bn = max(p for p, _ in nl)
        if bn > 0:
            cn = min(JOIN_SIZE, half / bn)
            ns = REC.side_share(nl, [(bn, cn)], row["target"], row["df"], TICK)[0]
            ct += cn
    return row["pool"] * (ys + ns) / 2.0, ct, ys, ns


def deployed_usd(row, dollars):
    """Capital ACTUALLY used, not allotted — JOIN_SIZE usually binds first."""
    used, half = 0.0, dollars / 2.0
    for lv in (row["yl"], row["nl"]):
        if lv:
            b = max(p for p, _ in lv)
            if b > 0:
                used += min(JOIN_SIZE, half / b) * b
    return used


def score_snapshot(rows, k, mode, apply_r3):
    """Allocate TOTAL_CAPITAL across top-K contracts. Mirrors the frozen study exactly."""
    per = min(TOTAL_CAPITAL / k, MAX_MARKET)
    if mode == "oracle":
        # ORACLE = same-snapshot hindsight, an UPPER BOUND on concentration. Ranking by
        # raw pool is degenerate here (every pool is $100) and would measure insertion
        # order, which is the 'asis' control's job.
        ranked = sorted(rows, key=lambda r: -score_market(r, per, apply_r3)[0])[:k]
    else:
        ranked = list(rows)[:k]     # control: venue order, zero selection skill
    if not ranked:
        return None
    raw = floored = perday = used = 0.0
    paying = nowin = 0
    shares = []
    for r in ranked:
        pay, ct, ys, ns = score_market(r, per, apply_r3)
        raw += pay
        wd = window_days(r)
        if wd:
            perday += (pay if pay >= MIN_PAYOUT else 0.0) / wd
        else:
            nowin += 1
        shares += [ys, ns]
        used += deployed_usd(r, per)
        if pay >= MIN_PAYOUT:
            floored += pay
            paying += 1
    return {"k": k, "per_market_usd": per, "deployed_usd": used, "raw": raw,
            "floored": floored, "paying": paying, "perday": perday, "nowin": nowin,
            "mean_share": sum(shares) / len(shares) if shares else 0.0,
            "capped": per >= MAX_MARKET - 1e-9}


def load():
    snaps = []
    try:
        for line in open(OUT):
            try:
                d = json.loads(line)
                if d.get("rows"):
                    snaps.append(d)
            except json.JSONDecodeError:
                pass
    except FileNotFoundError:
        return []
    return snaps


def main(minutes):
    end = time.time() + minutes * 60
    n = 0
    while time.time() < end:
        try:
            rows, n_prog, skipped = sample_once()
        except Exception as e:
            print(f"sample error: {e!r}")
            time.sleep(10)
            continue
        if rows:
            ts = datetime.now(timezone.utc).isoformat()
            with open(OUT, "a") as fh:
                fh.write(json.dumps({"ts": ts, "rows": rows}, separators=(",", ":")) + "\n")
            n += 1
            cls = [classify(r) for r in rows]
            print(f"{ts[11:19]} snap {n}: {len(rows)} contracts recorded "
                  f"({n_prog} in-allowlist programs, {skipped} unusable) "
                  f"| ok {cls.count('ok')} empty {cls.count('empty')} depth {cls.count('depth')}")
        if time.time() < end:
            time.sleep(SLEEP_S)
    return 0


def rates(snaps):
    """UNCONDITIONAL book-state rates. Denominator = every contract-snapshot recorded,
    including the ones the frozen study's filter dropped."""
    tot = defaultdict(int)
    by_event = defaultdict(lambda: defaultdict(int))
    by_series = defaultdict(lambda: defaultdict(int))
    side_empty = {"yes": 0, "no": 0, "both": 0}
    for s in snaps:
        for r in s["rows"]:
            c = classify(r)
            tot[c] += 1
            tot["all"] += 1
            by_event[event_of(r["t"])][c] += 1
            by_event[event_of(r["t"])]["all"] += 1
            by_series[r["t"].split("-")[0]][c] += 1
            by_series[r["t"].split("-")[0]]["all"] += 1
            if not r["yl"] and not r["nl"]:
                side_empty["both"] += 1
            elif not r["yl"]:
                side_empty["yes"] += 1
            elif not r["nl"]:
                side_empty["no"] += 1
    return tot, by_event, by_series, side_empty


def report():
    snaps = load()
    if not snaps:
        print("no samples yet — run the sampler first")
        return 0
    counts = [len(s["rows"]) for s in snaps]
    tot, by_event, by_series, side_empty = rates(snaps)
    N = tot["all"]

    print("=" * 78)
    print("UNBIASED SAMPLE — no empty-side filter (corrects canon §M6b)")
    print(f"  {len(snaps)} paired snapshots · {N} contract-snapshots · "
          f"{min(counts)}-{max(counts)} contracts/snapshot")
    print(f"  window {snaps[0]['ts'][11:19]}..{snaps[-1]['ts'][11:19]}Z  "
          f"({snaps[0]['ts'][:10]})")
    print(f"  capital ${TOTAL_CAPITAL:.0f} · per-contract cap ${MAX_MARKET:.0f} · "
          f"join {JOIN_SIZE:.0f} ct/side · min payout ${MIN_PAYOUT:.2f}")
    print("=" * 78)

    print("\n### UNCONDITIONAL BOOK-STATE RATES  (R3 market-level test, book alone)")
    print(f"  {'state':<28} {'n':>7} {'% of all':>10}")
    print(f"  {'-'*28} {'-'*7} {'-'*10}")
    for key, lab in (("ok", "OK two-sided (qualifies)"),
                     ("depth", "depth < Target (both sides live)"),
                     ("empty", "a side completely EMPTY")):
        print(f"  {lab:<28} {tot[key]:>7} {100.0*tot[key]/N:>9.1f}%")
    print(f"  {'-'*28} {'-'*7} {'-'*10}")
    print(f"  {'TOTAL':<28} {N:>7} {100.0:>9.1f}%")
    print(f"\n  empty-side breakdown:  yes-side empty {side_empty['yes']}  ·  "
          f"no-side empty {side_empty['no']}  ·  BOTH empty {side_empty['both']}")
    print(f"  UNCONDITIONAL two-sided rate = {100.0*tot['ok']/N:.1f}%   "
          f"(canon §M2 conditional figure: 86.1%)")
    cond_den = tot["ok"] + tot["depth"]
    if cond_den:
        print(f"  same data, frozen study's filter re-applied (non-empty both sides only):"
              f" {100.0*tot['ok']/cond_den:.1f}%  on n={cond_den}")

    print("\n### PER EVENT  (event = ONE CORRELATED RISK, canon §T)")
    print(f"  {'event':<24} {'n':>6} {'two-sided':>11} {'empty':>9} {'depth':>9}")
    print(f"  {'-'*24} {'-'*6} {'-'*11} {'-'*9} {'-'*9}")
    for ev in sorted(by_event, key=lambda e: -by_event[e]["all"]):
        d = by_event[ev]
        a = d["all"]
        print(f"  {ev:<24} {a:>6} {100.0*d['ok']/a:>10.1f}% "
              f"{100.0*d['empty']/a:>8.1f}% {100.0*d['depth']/a:>8.1f}%")
    print("\n### PER SERIES")
    print(f"  {'series':<24} {'n':>6} {'two-sided':>11} {'empty':>9} {'depth':>9}")
    print(f"  {'-'*24} {'-'*6} {'-'*11} {'-'*9} {'-'*9}")
    for se in sorted(by_series, key=lambda e: -by_series[e]["all"]):
        d = by_series[se]
        a = d["all"]
        print(f"  {se:<24} {a:>6} {100.0*d['ok']/a:>10.1f}% "
              f"{100.0*d['empty']/a:>8.1f}% {100.0*d['depth']/a:>8.1f}%")

    # ---------- K-SWEEP, three arms ----------
    for apply_r3 in (False, True):
        tag = "R3 APPLIED (excluded snapshot pays $0)" if apply_r3 else \
              "noR3 — frozen study's method verbatim (apples-to-apples)"
        print(f"\n{'='*78}\n### K-SWEEP  ·  {tag}\n{'='*78}")
        for mode in ("oracle", "asis"):
            lab = "ORACLE (upper bound, same-snapshot hindsight)" if mode == "oracle" \
                  else "AS-IS (control: venue order, no selection skill)"
            print(f"\n  {lab}")
            print(f"  {'K':>3} | {'FILTERED $/day':>14} {'paying':>7} | "
                  f"{'UNFILTERED $/day':>16} {'paying':>7} | {'delta':>9}")
            print(f"  {'-'*3} | {'-'*14} {'-'*7} | {'-'*16} {'-'*7} | {'-'*9}")
            best = {}
            kmax = max(len(s["rows"]) for s in snaps)
            for k in range(1, kmax + 1):
                out = {}
                for arm in ("filtered", "unfiltered"):
                    got = []
                    for s in snaps:
                        rows = s["rows"]
                        if arm == "filtered":
                            # EXACTLY kalshi_concentration_study.py:133
                            rows = [r for r in rows if r["yl"] and r["nl"]]
                        g = score_snapshot(rows, k, mode, apply_r3)
                        if g:
                            got.append(g)
                    if got:
                        out[arm] = (sum(g["perday"] for g in got) / len(got),
                                    sum(g["paying"] for g in got) / len(got))
                if not out:
                    continue
                f = out.get("filtered")
                u = out.get("unfiltered")
                for arm, v in out.items():
                    if v[0] > best.get(arm, (0, 0))[0]:
                        best[arm] = (v[0], k)
                fs = f"{f[0]:>14.2f} {f[1]:>7.2f}" if f else f"{'—':>14} {'—':>7}"
                us = f"{u[0]:>16.2f} {u[1]:>7.2f}" if u else f"{'—':>16} {'—':>7}"
                dl = f"{u[0]-f[0]:>+9.2f}" if (f and u) else f"{'—':>9}"
                print(f"  {k:>3} | {fs} | {us} | {dl}")
            cm = COMMITTED[mode]
            print(f"\n    best K  FILTERED   = {best.get('filtered',(0,0))[1]}  "
                  f"(${best.get('filtered',(0,0))[0]:.2f}/day)")
            print(f"    best K  UNFILTERED = {best.get('unfiltered',(0,0))[1]}  "
                  f"(${best.get('unfiltered',(0,0))[0]:.2f}/day)")
            print(f"    canon §M1 committed (frozen 02:25-02:48Z dataset): "
                  f"K={cm[0]} (${cm[1]:.2f}/day)")

    print("\n" + "=" * 78)
    print("WHAT THIS DOES NOT COVER: reward side ONLY — fill rate and adverse selection")
    print("are not simulatable without queue position, and concentration is strictly")
    print("worse on exactly that axis. Allowlist-only (7 of 192 series). One time-of-day;")
    print("canon §M6a shows this population is strongly time-varying, so these are point")
    print("estimates at the sampling window, not a steady state.")
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        sys.exit(report())
    mins = next((float(x) for x in sys.argv[1:] if x.replace(".", "").isdigit()), 8.0)
    sys.exit(main(mins))
