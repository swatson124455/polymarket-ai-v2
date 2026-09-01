#!/usr/bin/env python3
"""Unified daily MB scoreboard - the four live forward instruments in one
glance, appended to the 11:40Z cron block (docs/MB_STATE.md 2026-08-20 plan:
"ONE build allowed").

Read-only. Every section reads its OWN authoritative artifact rather than
re-deriving, and every section fails LOUD: a missing/empty source prints an
explicit UNAVAILABLE line. It never silently omits a section - a scoreboard
that quietly drops an instrument reads exactly like an instrument with
nothing to report (this lane's documented empty-set false-pass failure).

Usage: mb_scoreboard.py [--log PATH] [--base DIR] [--bidsim PATH]
"""
import argparse
import collections
import glob
import json
import os
import sys
from datetime import datetime, timezone

DEF_BASE = "/opt/pa2-shared/mb_copyable_data"
DEF_LOG = DEF_BASE + "/deep_dive/label_fee_refresh.log"
DEF_BIDSIM = "/opt/pa2-shared/mirror3_bidsim.jsonl"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def band_line(log_path):
    """Last [band] line written to the cron log (band_tracker is the source of
    truth; this run wrote its line moments ago)."""
    if not os.path.exists(log_path):
        return None, "log not found: %s" % log_path
    last = None
    with open(log_path, errors="replace") as f:
        for ln in f:
            if ln.startswith("[band]"):
                last = ln.strip()
    if not last:
        return None, "no [band] line in log (band tracker never ran?)"
    # staleness guard (2026-08-25, hygiene review): the band cron stage can
    # die invisibly; serving yesterday's line as current is the exact
    # dead-instrument-impersonating-live failure this board exists to stop.
    import re as _re
    m = _re.search(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})Z", last)
    if m:
        from datetime import datetime as _dt, timezone as _tz
        try:
            t = _dt.strptime(m.group(0), "%Y-%m-%dT%H:%MZ").replace(
                tzinfo=_tz.utc)
            age_h = (_dt.now(_tz.utc) - t).total_seconds() / 3600.0
            if age_h > 26:
                return None, (f"STALE - last [band] line is {age_h:.0f}h old "
                              f"({m.group(0)}); the band stage has NOT run "
                              f"today - investigate the cron")
        except ValueError:
            pass
    return last, None


# same 26h bar as the [band] staleness guard above (2026-08-25 hygiene
# review, batch B e335d9b) — one day of cron plus slack, no new constant
GRADER_STALE_H = 26.0


def grader_line(hb, now_utc):
    """[grader] health line from the cohort5 heartbeat (2026-09-01 alarm,
    operator 'build it'; born from the frm NameError that killed the grader
    on 7/7 daily runs 08-26..09-01 with nothing on this board). Pure:
    hb = parsed heartbeat dict or None. Returns (line, alarm).
    Every unreadable/absent/old state ALARMS — fail-toward-alarm."""
    if not isinstance(hb, dict) or not hb.get("ts"):
        return ("  [grader] !! NO HEARTBEAT - the qualification grader has "
                "never completed cleanly since this alarm deployed - read "
                "the cohort5 section of the cron log", True)
    try:
        t = datetime.strptime(str(hb["ts"]), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return ("  [grader] !! HEARTBEAT UNREADABLE (ts=%r) - treat the "
                "grader as DOWN" % (hb.get("ts"),), True)
    age_h = (now_utc - t).total_seconds() / 3600.0
    if age_h > GRADER_STALE_H:
        return ("  [grader] !! STALE - last clean grader run %s (%.0fh ago, "
                "bar %.0fh) - the grader is crashing or absent; read the "
                "cohort5 section of the cron log" %
                (hb["ts"], age_h, GRADER_STALE_H), True)
    return ("  [grader] OK %s groups=%s locks_written=%s" %
            (hb["ts"], hb.get("groups_graded", "?"),
             hb.get("locks_written", "?")), False)


def cohort5(base):
    """Locks file is authoritative for consumed single looks."""
    p = os.path.join(base, "deep_dive", "cohort5_qual_locks.json")
    if not os.path.exists(p):
        return None, "locks file not found: %s" % p
    try:
        d = json.load(open(p))
    except ValueError as e:
        return None, "locks file unreadable: %r" % (e,)
    if not isinstance(d, dict):
        return None, "locks file wrong shape"
    return d, None


def bidsim(path):
    if not os.path.exists(path):
        return None, "sink not found: %s" % path
    posts, term, bad = {}, {}, 0
    with open(path, errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                bad += 1
                continue
            k = (r.get("trader"), r.get("token_id"))
            if r.get("type") == "post":
                posts[k] = r
            elif r.get("type") in ("fill", "expire"):
                term[k] = r
    return {"posts": posts, "term": term, "parse_fail": bad}, None


def scout(base):
    files = sorted(glob.glob(os.path.join(base, "deep_dive_scout", "0x*.json")))
    if not files:
        return None, "no scout verdict files"
    c = collections.Counter()
    for fp in files:
        try:
            c[json.load(open(fp)).get("verdict")] += 1
        except ValueError:
            c["UNREADABLE"] += 1
    return c, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=DEF_LOG)
    ap.add_argument("--base", default=DEF_BASE)
    ap.add_argument("--bidsim", default=DEF_BIDSIM)
    ap.add_argument("--heartbeat",
                    default=DEF_BASE + "/deep_dive/"
                                       "cohort5_grader_heartbeat.json")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()

    out = ["===== %s MB SCOREBOARD (4 forward instruments) =====" % _now()]
    missing = 0

    b, err = band_line(a.log)
    out.append("  [1/4 band ] " + (b if b else "UNAVAILABLE - " + err))
    missing += 0 if b else 1

    d, err = bidsim(a.bidsim)
    if d is None:
        out.append("  [2/4 bidsim] UNAVAILABLE - " + err)
        missing += 1
    else:
        posts, term = d["posts"], d["term"]
        tc = collections.Counter(v.get("type") for v in term.values())
        nf, ne = tc.get("fill", 0), tc.get("expire", 0)
        res = nf + ne
        rate = ("%.3f" % (nf / res)) if res else "n/a"
        # self-fill guard: post-AMENDMENT 1 this must stay 0 (docs/BIDSIM_DESIGN)
        self_fills = sum(
            1 for v in term.values()
            if v.get("type") == "fill" and v.get("fill_tx")
            and v.get("fill_tx") == v.get("trigger_tx"))
        flag = "" if self_fills == 0 else "  <== SELF-FILL REGRESSION!"
        out.append("  [2/4 bidsim] posts=%d resolved=%d (fill=%d expire=%d) "
                   "fill_rate=%s open=%d parse_fail=%d self_fill=%d%s"
                   % (len(posts), res, nf, ne, rate, len(posts) - len(term),
                      d["parse_fail"], self_fills, flag))
        if res:
            waits = sorted(v.get("wait_s") or 0 for v in term.values()
                           if v.get("type") == "fill")
            if waits:
                out.append("              wait_s med=%.1f  <=5s=%d/%d  >60s=%d/%d"
                           % (waits[len(waits) // 2],
                              sum(1 for w in waits if w <= 5), len(waits),
                              sum(1 for w in waits if w > 60), len(waits)))
        out.append("              tripwire: chase-vs-post proposal at ~100 "
                   "resolved (now %d)" % res)

    d, err = cohort5(a.base)
    if d is None:
        out.append("  [3/4 cohort5] UNAVAILABLE - " + err)
        missing += 1
    else:
        nq = sum(1 for v in d.values()
                 if str(v.get("verdict", "")).upper().startswith("DOES NOT"))
        out.append("  [3/4 cohort5] locks consumed=%d (DOES-NOT-QUALIFY=%d) "
                   "of 20 eligible chain-ADMITs" % (len(d), nq))

    c, err = scout(a.base)
    if c is None:
        out.append("  [4/4 scout ] UNAVAILABLE - " + err)
        missing += 1
    else:
        tot = sum(c.values())
        parts = " ".join("%s=%d" % (k, v) for k, v in sorted(c.items()))
        out.append("  [4/4 scout ] %d verdicts: %s | ADMITs=%d"
                   % (tot, parts, c.get("ADMIT", 0)))

    hb = None
    try:
        hb = json.load(open(a.heartbeat))
    except (ValueError, OSError):
        pass  # grader_line alarms on None/malformed — fail-toward-alarm
    ln, _alarm = grader_line(hb, datetime.now(timezone.utc))
    out.append(ln)

    if missing:
        out.append("  !! %d of 4 instruments UNAVAILABLE - investigate before "
                   "reading this scoreboard as 'nothing happening'" % missing)
    print("\n".join(out))
    return 0


def _self_test() -> int:
    print("SELF-TEST — mb_scoreboard grader alarm (offline)\n")
    from datetime import timedelta
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    fresh = {"ts": (now - timedelta(hours=1)).strftime(fmt),
             "groups_graded": 5, "locks_written": 0}
    old = {"ts": (now - timedelta(hours=27)).strftime(fmt),
           "groups_graded": 5, "locks_written": 0}
    ln1, a1 = grader_line(fresh, now)
    ok1 = (not a1) and ln1.startswith("  [grader] OK") and "groups=5" in ln1
    print(f"  [ok] fresh heartbeat -> OK line, no alarm : {ok1}")
    ln2, a2 = grader_line(old, now)
    ok2 = a2 and "STALE" in ln2 and "27h ago" in ln2
    print(f"  [stale] 27h-old heartbeat -> STALE alarm (bar 26h) : {ok2}")
    ln3, a3 = grader_line(None, now)
    ok3 = a3 and "NO HEARTBEAT" in ln3
    print(f"  [never] missing heartbeat -> alarm, never OK : {ok3}")
    ln4, a4 = grader_line({"ts": "garbage"}, now)
    ok4 = a4 and "UNREADABLE" in ln4
    print(f"  [garbage] unparseable ts -> alarm, never OK : {ok4}")
    # negative control: a just-inside-the-bar heartbeat must NOT alarm —
    # an alarm that always fires is as blind as one that never does
    ln5, a5 = grader_line({"ts": (now - timedelta(hours=25)).strftime(fmt)},
                          now)
    ok5 = not a5 and ln5.startswith("  [grader] OK")
    print(f"  [negctl] 25h-old (inside bar) -> OK, alarm stays quiet : {ok5}")
    ok6 = GRADER_STALE_H == 26.0
    print(f"  [const] bar equals the ratified [band] 26h guard : {ok6}")
    ok = ok1 and ok2 and ok3 and ok4 and ok5 and ok6
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
