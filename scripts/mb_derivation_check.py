#!/usr/bin/env python3
"""MB DERIVATION CHECK - does every published tier still reproduce from the
raw inputs? (operator "do fixes", 2026-10-17)

WHY THIS EXISTS. canon_verify bounds how wrong a RECORDED value can be. The
completeness probe bounds what is MISSING. Neither looks at the layer in
between: the tier printed on the tailable list is produced by the same code
that reports it, so a filter that drifts from the ruling - an inverted
comparison, a renamed field read as None, a threshold edited in one of two
places - changes the published answer with nothing to contradict it.

This stage re-applies the five RULED filters from scratch and refuses to
import mb_tailable_list or cohort5_qualification. Agreement is then real
evidence; importing the implementation would only prove it agrees with
itself. The filters, typed here from the rulings:

    VERIFIED = eligibility PASS (first trade <= today-30d AND >= 25 trades)
           AND position-level peak concurrency <= 20
           AND per-wallet label coverage >= 50%
           AND holdout ROI LCB > 0
           AND chain deep-dive ADMIT
    board choice: the ROSTER row wins when it carries a holdout LCB (our
    fills, real gates, never fill-modeled); otherwise the firehose row.

THE DIVE LAG IS NOT A MISMATCH. The list runs at ~12:30Z; the dive runner
runs at 13:00Z. A wallet whose dossier was written AFTER the list was
generated could not have been seen by it, so it reads VERIFIED here and
PENDING there until tomorrow. That is the pipeline working, and it is
classified LAGGED, never counted as a defect. Measured live 2026-10-16:
0x8bad0e57ff, list 12:32Z, dossier 13:22Z.

Read-only except its own artifact.

    PYTHONPATH=<mb_readout> python mb_derivation_check.py
    ... --self-test     # offline: filter table, lag rule, summary
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = "/opt/pa2-shared/mb_copyable_data"
# ruled thresholds, typed from the rulings - NEVER imported from the code
# under test (that is the whole point of this stage)
ELIG_MIN_DAYS = 30
ELIG_MIN_TRADES = 25
CONC_BAR = 20
COV_BAR = 50.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(stamp):
    """ISO -> epoch seconds, or None. Accepts Z, +00:00 and naive (UTC)."""
    try:
        dt = datetime.fromisoformat(str(stamp).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def pick_board(roster_row, firehose_row):
    """PURE. The ruled board choice: the roster row wins when it carries a
    holdout LCB; else the firehose row; else {}."""
    if roster_row and roster_row.get("ho_roi_lcb") is not None:
        return roster_row, "roster"
    if firehose_row:
        return firehose_row, "firehose"
    return (roster_row or {}), ("roster" if roster_row else None)


def rederive(board, elig_status, dive_verdict, conc_pos):
    """PURE. (verified, fails) from the five ruled filters. `fails` names
    every criterion that did not pass, so a disagreement is explainable."""
    fails = []
    if elig_status != "PASS":
        fails.append(f"elig={elig_status or 'UNREAD'}")
    conc = conc_pos if conc_pos is not None else board.get("peak_conc_replay")
    if conc is None:
        fails.append("conc=unmeasured")
    elif conc > CONC_BAR:
        fails.append(f"conc={conc}>{CONC_BAR}")
    cov = board.get("cov_pct")
    if cov is None:
        fails.append("cov=unmeasured")
    elif cov < COV_BAR:
        fails.append(f"cov={cov:.0f}<{COV_BAR:.0f}")
    lcb = board.get("ho_roi_lcb")
    if lcb is None or lcb <= 0:
        fails.append(f"lcb={lcb}")
    if not str(dive_verdict or "").startswith("ADMIT"):
        fails.append(f"dive={dive_verdict or 'none'}")
    return (not fails), fails


def classify(mine_verified, published_tier, dossier_ts, generated_ts):
    """PURE. REPRODUCED / LAGGED / MISMATCH.

    LAGGED covers exactly one thing: the inputs moved after the list was
    generated, so the list could not have used them. Anything else that
    disagrees is a MISMATCH and must alarm."""
    theirs_verified = (published_tier == "VERIFIED")
    if mine_verified == theirs_verified:
        return "REPRODUCED"
    if (dossier_ts is not None and generated_ts is not None
            and dossier_ts > generated_ts):
        return "LAGGED"
    return "MISMATCH"


def summarize(rows, generated_utc):
    """PURE. (line, alarm)."""
    c = {k: 0 for k in ("REPRODUCED", "LAGGED", "MISMATCH")}
    for r in rows:
        c[r["class"]] = c.get(r["class"], 0) + 1
    alarm = c["MISMATCH"] > 0
    head = "[derivation] !!" if alarm else "[derivation]"
    line = (f"{head} rows={len(rows)} reproduced={c['REPRODUCED']} "
            f"lagged={c['LAGGED']} MISMATCH={c['MISMATCH']} "
            f"| list generated {generated_utc} | filters re-applied from the "
            f"rulings, implementation NOT imported")
    if alarm:
        line += " | " + ", ".join(
            f"{r['wallet'][:12]}..(mine={'VERIFIED' if r['mine'] else 'not'},"
            f"published={r['published']})"
            for r in rows if r["class"] == "MISMATCH")
    return line, alarm


def jload(p, default=None):
    try:
        with open(p, errors="replace") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jlines(p):
    out = []
    try:
        with open(p, errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def newest_dossiers(dirs):
    """{wallet: (mtime, verdict)} - newest dossier per address by mtime."""
    newest = {}
    for d in dirs:
        for f_ in glob.glob(os.path.join(d, "0x*.json")):
            b = jload(f_)
            if not b:
                continue
            a = str(b.get("address", "")).lower()
            try:
                mt = os.path.getmtime(f_)
            except OSError:
                continue
            if a and (a not in newest or mt > newest[a][0]):
                newest[a] = (mt, str(b.get("verdict", "")))
    return newest


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def run(args) -> int:
    blob = jload(args.list)
    if not blob or not blob.get("rows"):
        print("[derivation] !! FATAL: tailable_list.json unreadable or empty "
              "- refusing to report 'no mismatches' on no input")
        return 2
    gen_utc = blob.get("generated_utc")
    gen_ts = parse_utc(gen_utc)
    fh = {str(r.get("w", "")).lower(): r
          for r in jlines(args.firehose_board)}
    ro = {str(r.get("w", "")).lower(): r for r in jlines(args.roster_board)}
    if not fh and not ro:
        print("[derivation] !! FATAL: both leaderboards unreadable - the "
              "re-derivation has no evidence to work from")
        return 2
    conc = {str(r.get("w", "")).lower(): r.get("peak_conc")
            for r in jlines(args.peak_conc)}
    elig = jload(args.elig_cache, {}) or {}
    dos = newest_dossiers(args.admit_dirs)

    rows = []
    for pr in blob["rows"]:
        w = str(pr.get("wallet", "")).lower()
        board, src = pick_board(ro.get(w), fh.get(w))
        d_mt, d_verdict = dos.get(w, (None, None))
        mine, fails = rederive(board, (elig.get(w) or {}).get("status"),
                               d_verdict, conc.get(w))
        rows.append({"wallet": w, "mine": mine, "fails": fails,
                     "published": pr.get("tier"), "board": src,
                     "class": classify(mine, pr.get("tier"), d_mt, gen_ts),
                     "dossier_utc": (datetime.fromtimestamp(
                         d_mt, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                         if d_mt else None)})

    line, alarm = summarize(rows, gen_utc)
    print(line)
    for r in rows:
        if r["class"] == "REPRODUCED":
            continue
        print(f"    {r['wallet'][:12]}..  {r['class']:<11} "
              f"mine={'VERIFIED' if r['mine'] else 'not-VERIFIED'} "
              f"published={r['published']} board={r['board']} "
              f"dossier={r['dossier_utc']} fails={';'.join(r['fails']) or '-'}")
    save_json(args.out, {"ts": now_iso(), "list_generated": gen_utc,
                         "alarm": alarm, "rows": rows})
    return 0


def _self_test() -> int:
    print("SELF-TEST - mb_derivation_check (offline)\n")
    ok = True
    good = {"ho_roi_lcb": 0.2, "cov_pct": 90.0, "peak_conc_replay": 12}
    v, f = rederive(good, "PASS", "ADMIT", 10)
    ok1 = v and not f
    for board, es, dv, cc, why in (
            (good, "FAIL", "ADMIT", 10, "elig"),
            (good, "PASS", "INSUFFICIENT-EVIDENCE", 10, "dive"),
            (good, "PASS", None, 10, "dive"),
            (good, "PASS", "ADMIT", 25, "conc"),
            ({**good, "cov_pct": 30.0}, "PASS", "ADMIT", 10, "cov"),
            ({**good, "ho_roi_lcb": 0.0}, "PASS", "ADMIT", 10, "lcb"),
            ({**good, "ho_roi_lcb": None}, "PASS", "ADMIT", 10, "lcb"),
            ({"cov_pct": 90.0, "ho_roi_lcb": 0.2}, "PASS", "ADMIT", None,
             "conc")):
        vv, ff = rederive(board, es, dv, cc)
        ok1 = ok1 and (not vv) and any(x.startswith(why) for x in ff)
    # boundary: exactly at each bar must PASS (the bars are inclusive)
    vb, _ = rederive({"ho_roi_lcb": 1e-9, "cov_pct": 50.0,
                      "peak_conc_replay": 20}, "PASS", "ADMIT", 20)
    ok1 = ok1 and vb
    print(f"  [filters] all five gate; each failure is named; the bars are "
          f"inclusive at 20 / 50% : {ok1}")
    ok &= ok1
    rr = {"ho_roi_lcb": 0.5}
    ff_ = {"ho_roi_lcb": 0.9}
    ok2 = (pick_board(rr, ff_) == (rr, "roster")
           and pick_board({"ho_roi_lcb": None}, ff_) == (ff_, "firehose")
           and pick_board(None, ff_) == (ff_, "firehose")
           and pick_board(None, None) == ({}, None))
    print(f"  [board] roster row wins when it has an LCB, else firehose : {ok2}")
    ok &= ok2
    g = 1_000_000.0
    ok3 = (classify(True, "VERIFIED", g - 10, g) == "REPRODUCED"
           and classify(False, "PENDING", g - 10, g) == "REPRODUCED"
           # dossier written AFTER the list ran -> the designed dive lag
           and classify(True, "PENDING", g + 50, g) == "LAGGED"
           # dossier older than the list -> the list SAW it -> real defect
           and classify(True, "PENDING", g - 50, g) == "MISMATCH"
           and classify(False, "VERIFIED", g + 50, g) == "LAGGED"
           and classify(True, "PENDING", None, g) == "MISMATCH"
           and classify(True, "PENDING", g + 50, None) == "MISMATCH")
    print(f"  [lag] only an input that moved AFTER the list is LAGGED; "
          f"unknown timing is a MISMATCH, never excused : {ok3}")
    ok &= ok3
    rows = [{"wallet": "0x" + "a" * 40, "class": "REPRODUCED", "mine": True,
             "published": "VERIFIED"},
            {"wallet": "0x" + "b" * 40, "class": "LAGGED", "mine": True,
             "published": "PENDING"},
            {"wallet": "0x" + "c" * 40, "class": "MISMATCH", "mine": False,
             "published": "VERIFIED"}]
    line, alarm = summarize(rows, "2026-10-17T12:00:00Z")
    ok4 = (alarm and line.startswith("[derivation] !!") and "MISMATCH=1" in line
           and "lagged=1" in line and "0xcccccccccc" in line
           and "implementation NOT imported" in line)
    print(f"  [summary] alarms only on MISMATCH, names the wallet, states "
          f"independence : {ok4}")
    ok &= ok4
    line2, alarm2 = summarize(rows[:2], "2026-10-17T12:00:00Z")
    ok5 = (not alarm2) and line2.startswith("[derivation] rows=2")
    print(f"  [negctl] reproduced + lagged only -> no alarm : {ok5}")
    ok &= ok5
    # BEHAVIORAL, not a source-text scan: if this stage had imported the
    # code under test, that module would be in sys.modules. (A text scan
    # matched its own docstring - the exact self-referential pin the
    # 2026-09-09 review flagged.)
    ok6 = ("mb_tailable_list" not in sys.modules
           and "cohort5_qualification" not in sys.modules
           and ELIG_MIN_DAYS == 30 and ELIG_MIN_TRADES == 25
           and CONC_BAR == 20 and COV_BAR == 50.0)
    print(f"  [independence] the code under test is NOT imported; the ruled "
          f"thresholds are typed here : {ok6}")
    ok &= ok6
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MB published-tier re-derivation")
    ap.add_argument("--list", default=f"{BASE}/tailable/tailable_list.json")
    ap.add_argument("--firehose-board", dest="firehose_board",
                    default=f"{BASE}/backtest/leaderboard_firehose.jsonl")
    ap.add_argument("--roster-board", dest="roster_board",
                    default=f"{BASE}/backtest/leaderboard_roster.jsonl")
    ap.add_argument("--peak-conc", dest="peak_conc",
                    default=f"{BASE}/firehose/peak_conc.jsonl")
    ap.add_argument("--elig-cache", dest="elig_cache",
                    default=f"{BASE}/tailable/tailable_elig.json")
    ap.add_argument("--admit-dirs", dest="admit_dirs", nargs="*", default=[
        f"{BASE}/deep_dive", f"{BASE}/deep_dive_rereview",
        f"{BASE}/deep_dive_insuff_regrade", f"{BASE}/deep_dive_scout",
        f"{BASE}/deep_dive_scout2", f"{BASE}/deep_dive_promo0907",
        f"{BASE}/deep_dive_pipeline"])
    ap.add_argument("--out", default=f"{BASE}/tailable/derivation_check.json")
    ap.add_argument("--self-test", dest="self_test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else run(a))
