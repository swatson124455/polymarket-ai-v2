#!/usr/bin/env python3
"""MB COMPLETENESS PROBE - does our capture hold every fill the venue says
the trader made? (operator "do fixes", 2026-10-17)

WHY THIS EXISTS. canon_verify.py samples EXISTING rows and re-derives them
against chain/venue ground truth, so it bounds how wrong a recorded value
can be. By construction it can never see a row that SHOULD exist and does
not: a sample of what is there says nothing about what is missing. A missed
BUY is exactly what corrupts a ladder-aware position ROI - the estimand the
whole lane is built on - so completeness needs its own standing check.

WHAT IT MEASURES. For every tailable-list wallet that is on the watcher
roster (the watcher records only those), inside a trailing window:
    venue set = {(transactionHash, asset)}  BUY fills, data-api
    ours  set = {(tx, token_id)}            BUY rows, mirror3_shadow.jsonl
    missing = venue - ours        capture = |venue & ours| / |venue|
Identity, not counts: a count match can hide one missed fill offset by one
duplicate. Window edges are padded so a fill detected a few seconds after
the cut is not scored as missing.

WHAT IT DOES NOT COVER (stated, never implied away):
  * NON-roster wallets - most of the list universe. The firehose recorder
    captures the whole venue and is not checked here; that needs its own
    probe over candidate_rows and is NOT part of this stage.
  * SELL-side completeness (the exits sink), unmeasured here.

Read-only except its own artifact. Network failures print UNAVAILABLE and
never crash the stage - an API outage must not read as "capture is broken",
and must not read as "all clean" either.

    PYTHONPATH=<mb_readout> python mb_completeness_probe.py
    ... --self-test     # offline: verdict table, window padding, set math
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = "/opt/pa2-shared/mb_copyable_data"
DAY_S = 86400.0
WINDOW_D = 7.0            # trailing window scored
EDGE_PAD_S = 900.0        # 15 min: detection lands after the venue timestamp
CAPTURE_BAR = 0.98        # below this (with enough fills) = alarm
MIN_VENUE_FILLS = 5       # fewer than this is THIN, never an alarm
MAX_PAGES = 20            # hard cap on data-api paging per wallet
DATA_API = "https://data-api.polymarket.com/activity"
UA = {"User-Agent": "PolymarketAI/1.0 (https://github.com; data)",
      "Accept": "application/json"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def capture_verdict(n_venue: int, n_hit: int, bar: float = CAPTURE_BAR,
                    min_venue: int = MIN_VENUE_FILLS) -> tuple:
    """PURE. (verdict, ratio) for one wallet.
      THIN  - too few venue fills to judge (never an alarm, never an OK)
      OK    - capture ratio >= bar
      UNDER - capture ratio < bar  (THE alarm)
    ratio is None when there is nothing to divide by."""
    if n_venue <= 0:
        return "THIN", None
    ratio = n_hit / float(n_venue)
    if n_venue < min_venue:
        return "THIN", ratio
    return ("OK" if ratio >= bar else "UNDER"), ratio


def window_bounds(now_ts: float, window_d: float = WINDOW_D,
                  pad_s: float = EDGE_PAD_S) -> tuple:
    """PURE. (venue_lo, venue_hi, ours_lo, ours_hi). The venue side is
    scored strictly INSIDE the window; our side may hold a detection that
    landed up to pad_s after the venue stamp, so its window is wider.
    Without this a fill at the very edge reads as a missed capture."""
    lo, hi = now_ts - window_d * DAY_S, now_ts
    return lo + pad_s, hi - pad_s, lo, hi + pad_s


def summarize(rows: list) -> tuple:
    """PURE. (line, alarm) for the stage's one-line verdict."""
    n = len(rows)
    under = [r for r in rows if r["verdict"] == "UNDER"]
    thin = [r for r in rows if r["verdict"] == "THIN"]
    una = [r for r in rows if r["verdict"] == "UNAVAILABLE"]
    ok = [r for r in rows if r["verdict"] == "OK"]
    miss = sum(r.get("n_missing") or 0 for r in rows)
    head = "[completeness] !!" if (under or una) else "[completeness]"
    line = (f"{head} wallets={n} OK={len(ok)} UNDER={len(under)} "
            f"THIN={len(thin)} UNAVAILABLE={len(una)} | missing fills={miss} "
            f"| window={WINDOW_D:.0f}d bar={CAPTURE_BAR:.2f} "
            f"(roster wallets only; non-roster capture NOT covered)")
    if under:
        line += " | UNDER: " + ", ".join(
            f"{r['wallet'][:12]}..={r['ratio']:.3f}" for r in under)
    if una:
        line += " | UNAVAILABLE: " + ", ".join(
            f"{r['wallet'][:12]}..({r.get('error', '?')[:28]})" for r in una)
    return line, bool(under or una)


def jload(p, default=None):
    try:
        with open(p, errors="replace") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def venue_buys(addr: str, lo: float, hi: float, timeout_s: float = 25.0):
    """{(tx, asset)} BUY fills in [lo, hi]. (set, None) or (None, error)."""
    out, offset = set(), 0
    while offset < MAX_PAGES * 100:
        url = DATA_API + "?" + urllib.parse.urlencode(
            {"user": addr, "type": "TRADE", "limit": 100, "offset": offset,
             "sortBy": "TIMESTAMP", "sortDirection": "DESC"})
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA),
                    timeout=timeout_s) as r:
                page = json.load(r)
        except Exception as e:
            return None, repr(e)[:70]
        if not isinstance(page, list) or not page:
            break
        oldest = None
        for t in page:
            try:
                ts = float(t["timestamp"])
            except (KeyError, TypeError, ValueError):
                continue
            oldest = ts if oldest is None else min(oldest, ts)
            if lo <= ts <= hi and str(t.get("side", "")).upper() == "BUY":
                out.add((str(t.get("transactionHash") or ""),
                         str(t.get("asset") or "")))
        if oldest is not None and oldest < lo:
            break
        offset += 100
        time.sleep(0.25)
    return out, None


def ours_buys(sink_path: str, wallets: set, lo: float, hi: float) -> dict:
    """{wallet: {(tx, token_id)}} BUY rows in [lo, hi]. One pass over the
    sink - it is ~1 GB, so never one pass per wallet."""
    got = {w: set() for w in wallets}
    try:
        with open(sink_path, errors="replace") as f:
            for ln in f:
                if not ln.strip():
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                w = str(r.get("trader", "")).lower()
                if w not in got:
                    continue
                if r.get("side") not in (None, "BUY"):
                    continue
                try:
                    ts = float(r.get("detect_ts") or 0)
                except (TypeError, ValueError):
                    continue
                if lo <= ts <= hi and r.get("tx"):
                    got[w].add((str(r["tx"]), str(r.get("token_id") or "")))
    except OSError:
        return {}
    return got


def save_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def run(args) -> int:
    blob = jload(args.list)
    if not blob or not blob.get("rows"):
        print("[completeness] !! FATAL: tailable_list.json unreadable or "
              "empty - refusing to report capture health on no input")
        return 2
    audit = jload(args.chain_audit)
    if not audit or not audit.get("clean"):
        print("[completeness] !! FATAL: chain_audit roster unreadable/empty")
        return 2
    clean = {str(a).lower() for a in audit["clean"]}
    listed = [str(r.get("wallet", "")).lower() for r in blob["rows"]]
    targets = sorted(w for w in listed if w in clean)
    n_off = len(listed) - len(targets)
    if not targets:
        print(f"[completeness] no list wallet is on the watcher roster "
              f"({len(listed)} listed, none capturable) - nothing to probe")
        return 0

    now_ts = float(args.now_ts) if args.now_ts else time.time()
    v_lo, v_hi, o_lo, o_hi = window_bounds(now_ts)
    ours = ours_buys(args.sink, set(targets), o_lo, o_hi)

    rows = []
    for w in targets:
        vs, err = venue_buys(w, v_lo, v_hi)
        if vs is None:
            rows.append({"wallet": w, "verdict": "UNAVAILABLE", "error": err,
                         "ratio": None, "n_venue": None, "n_ours": None,
                         "n_missing": None})
            continue
        mine = ours.get(w, set())
        hit = len(vs & mine)
        verdict, ratio = capture_verdict(len(vs), hit)
        missing = sorted(vs - mine)
        rows.append({"wallet": w, "verdict": verdict, "ratio": ratio,
                     "n_venue": len(vs), "n_ours": len(mine), "n_hit": hit,
                     "n_missing": len(missing),
                     "missing_sample": [list(x) for x in missing[:5]]})

    line, alarm = summarize(rows)
    print(line)
    for r in sorted(rows, key=lambda x: (x["verdict"] != "UNDER",
                                         x["wallet"])):
        rr = "   -  " if r["ratio"] is None else f"{r['ratio']:.3f}"
        print(f"    {r['wallet'][:12]}..  {r['verdict']:<11} capture={rr} "
              f"venue={r['n_venue']} ours={r['n_ours']} "
              f"missing={r['n_missing']}"
              + (f" err={r['error'][:40]}" if r.get("error") else ""))
    print(f"    [scope] {len(targets)} of {len(listed)} list wallets are "
          f"roster-capturable; {n_off} are NOT and their capture is "
          f"UNMEASURED by this stage")
    save_json(args.out, {
        "ts": now_iso(), "window_days": WINDOW_D, "bar": CAPTURE_BAR,
        "alarm": alarm, "n_listed": len(listed), "n_probed": len(targets),
        "n_not_capturable": n_off, "rows": rows})
    return 0


def _self_test() -> int:
    print("SELF-TEST - mb_completeness_probe (offline)\n")
    ok = True
    v = capture_verdict
    ok1 = (v(100, 100)[0] == "OK"
           and v(100, 98)[0] == "OK"               # exactly at the bar
           and v(100, 97)[0] == "UNDER"            # below the bar
           and v(4, 0)[0] == "THIN"                # too few to judge
           and v(0, 0) == ("THIN", None)           # nothing to divide by
           and abs(v(50, 49)[1] - 0.98) < 1e-12)
    print(f"  [verdict] OK at/above bar, UNDER below, THIN under {MIN_VENUE_FILLS}"
          f" venue fills, no divide-by-zero : {ok1}")
    ok &= ok1
    now = 1_800_000_000.0
    vlo, vhi, olo, ohi = window_bounds(now)
    ok2 = (vlo == now - WINDOW_D * DAY_S + EDGE_PAD_S
           and vhi == now - EDGE_PAD_S
           and olo == now - WINDOW_D * DAY_S
           and ohi == now + EDGE_PAD_S
           and olo < vlo and vhi < ohi)
    print(f"  [window] venue scored strictly inside; our window is wider on "
          f"both edges so a late detection is not 'missing' : {ok2}")
    ok &= ok2
    rows = [{"wallet": "0x" + "a" * 40, "verdict": "OK", "ratio": 1.0,
             "n_missing": 0},
            {"wallet": "0x" + "b" * 40, "verdict": "UNDER", "ratio": 0.5,
             "n_missing": 3},
            {"wallet": "0x" + "c" * 40, "verdict": "THIN", "ratio": None,
             "n_missing": 0},
            {"wallet": "0x" + "d" * 40, "verdict": "UNAVAILABLE",
             "ratio": None, "n_missing": None, "error": "api down"}]
    line, alarm = summarize(rows)
    ok3 = (alarm and line.startswith("[completeness] !!")
           and "UNDER=1" in line and "THIN=1" in line and "UNAVAILABLE=1" in line
           and "missing fills=3" in line and "0xbbbbbbbbbb" in line
           and "non-roster capture NOT covered" in line)
    print(f"  [summary] alarms on UNDER or UNAVAILABLE; names them; states "
          f"the uncovered scope : {ok3}")
    ok &= ok3
    line2, alarm2 = summarize([rows[0], rows[2]])
    ok4 = (not alarm2) and line2.startswith("[completeness] wallets=2")
    print(f"  [negctl] all OK/THIN -> no alarm, no '!!' : {ok4}")
    ok &= ok4
    # THIN must never be an alarm even at a terrible ratio - a 1-fill wallet
    # cannot distinguish a broken watcher from a quiet trader
    ok5 = v(1, 0)[0] == "THIN" and not summarize(
        [{"wallet": "0x" + "e" * 40, "verdict": "THIN", "ratio": 0.0,
          "n_missing": 1}])[1]
    print(f"  [thin] a tiny sample never raises the alarm : {ok5}")
    ok &= ok5
    import inspect
    src = inspect.getsource(ours_buys)
    ok6 = ("for ln in f" in src and src.count("open(") == 1)
    print(f"  [perf] the ~1GB sink is read in ONE pass for all wallets : {ok6}")
    ok &= ok6
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MB capture-completeness probe")
    ap.add_argument("--list", default=f"{BASE}/tailable/tailable_list.json")
    ap.add_argument("--chain-audit", dest="chain_audit",
                    default=f"{BASE}/chain_audit.json")
    ap.add_argument("--sink", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    ap.add_argument("--out", default=f"{BASE}/tailable/completeness.json")
    ap.add_argument("--now-ts", dest="now_ts", type=float, default=None)
    ap.add_argument("--self-test", dest="self_test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else run(a))
