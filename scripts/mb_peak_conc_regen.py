#!/usr/bin/env python3
"""Regenerate the screen's peak-concurrency input from CURRENT capture.

WHY (screening gap audit, operator "3 verify all screening for gaps /
4 proceed as needed", 2026-09-07): the tailability screen consumes
firehose/peak_conc.jsonl, which was produced by an UNCOMMITTED one-off
pass and frozen 2026-09-02 15:12Z (file mtime). Measured drift on the
2026-09-07 boards: 0xddc38b8242 frozen conc 15 vs 567 measured by the
replay over full capture; 0x4ed3513040 18 vs 50. A frozen snapshot lets
wallets that scale up later keep passing the operator's conc<=20 bar.
This script is the committed, re-runnable generator.

LENS (identical to the leaderboard's measured column — reuse, not
re-implementation): per wallet, a position opens at its first BUY of a
token and closes at the EARLIEST of the wallet's own first post-entry
SELL, the market's resolution time (resolutions cache), else the capture
end — mb_backtest.peak_concurrency_replay + wallet_exits, imported.

SCOPE: conc is computed for wallets with >= --min-trades rows (the only
wallets the screen consults it for) — bounds memory on a 50M-row
capture; the cut is printed, never silent. An eligible wallet with zero
BUYs gets no row (screen counts it UNKNOWN — the alarm state, correct).

SAFETY: dry-run by default; --write does backup + atomic replace +
readback guard. Inputs read-only.

    python scripts/mb_peak_conc_regen.py --wallets <stage1.wallets.jsonl> \
        [--write]
    ... --self-test    # offline, no files
"""
from __future__ import annotations

import argparse
import glob as globmod
import gzip
import json
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mb_backtest as mbt  # noqa: E402  (peak_concurrency_replay, exits)

FIREHOSE_GLOB = ("/opt/pa2-shared/mb_copyable_data/firehose/"
                 "firehose_2026*.jsonl.gz")
WALLETS_DEFAULT = ("/opt/pa2-shared/mb_copyable_data/firehose/"
                   "population_study_stage1.txt.wallets.jsonl")
OUT_DEFAULT = "/opt/pa2-shared/mb_copyable_data/firehose/peak_conc.jsonl"
CACHE_DEFAULT = ("/opt/pa2-shared/mb_copyable_data/copyable_cache/"
                 "gamma_resolutions.json")


def eligible_set(wallets_path: str, min_trades: int) -> set[str]:
    out: set[str] = set()
    with open(wallets_path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if int(r.get("n") or 0) >= min_trades and r.get("w"):
                out.add(str(r["w"]).lower())
    assert out, f"0 eligible wallets (n >= {min_trades}) in {wallets_path}"
    return out


def scan_positions(paths: list[str], keep: set[str]
                   ) -> tuple[dict, int, float]:
    """state[(w, tok)] = [first_buy_ts, first_post_entry_sell_ts|None]
    over kept wallets; (state, rows_read, max_ts). Same first-BUY /
    first-post-entry-SELL semantics as mbt.wallet_exits."""
    state: dict[tuple, list] = {}
    n_in = 0
    t_max = 0.0
    for path in paths:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            for ln in f:
                n_in += 1
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                w = str(r.get("w") or "").lower()
                if w not in keep:
                    continue
                tok = str(r.get("tok") or "")
                try:
                    ts = float(r["t"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not tok:
                    continue
                t_max = max(t_max, ts)
                key = (w, tok)
                s = state.get(key)
                side = r.get("s")
                if side == "BUY":
                    if s is None:
                        state[key] = [ts, None]
                    elif ts < s[0]:
                        s[0] = ts
                elif side == "SELL" and s is not None and s[1] is None \
                        and ts >= s[0]:
                    s[1] = ts
    return state, n_in, t_max


def conc_per_wallet(state: dict, res_at: dict, end_ts: float) -> dict:
    """wallet -> peak concurrency, via the replay's own event sweep."""
    per_w: dict[str, tuple[list, dict]] = {}
    for (w, tok), (b_ts, s_ts) in state.items():
        recs, exits = per_w.setdefault(w, ([], {}))
        recs.append({"token_id": tok, "detect_ts": b_ts})
        if s_ts is not None:
            exits[tok] = s_ts
    return {w: mbt.peak_concurrency_replay(recs, exits, res_at, end_ts)
            for w, (recs, exits) in per_w.items()}


def run(args) -> int:
    keep = eligible_set(args.wallets, args.min_trades)
    paths = sorted(globmod.glob(args.files))
    assert paths, f"0 firehose files match {args.files}"
    print(f"[conc] eligible wallets (n >= {args.min_trades}): {len(keep)} "
          f"(conc computed ONLY for these — the screen consults no "
          f"others; disclosed cut) | {len(paths)} capture files")
    state, n_in, t_max = scan_positions(paths, keep)
    assert state, "0 positions scanned for eligible wallets — query-shape " \
                  "failure, not 'no news'"
    cache = json.load(open(args.resolutions))
    r_at = mbt.res_at_map(cache)
    conc = conc_per_wallet(state, r_at, t_max)
    n_unknown = len(keep) - len(conc)
    print(f"[conc] rows read {n_in} | positions {len(state)} | wallets "
          f"with conc {len(conc)} | eligible w/o any BUY -> UNKNOWN "
          f"{n_unknown} | res_at keys {len(r_at)}")
    dist = sorted(conc.values())
    if dist:
        def q(p):
            return dist[min(len(dist) - 1, int(p * len(dist)))]
        print(f"[conc] distribution: med {q(0.5)} p90 {q(0.9)} p99 "
              f"{q(0.99)} max {dist[-1]}")
    if not args.write:
        print("\nDRY RUN — nothing written. Re-run with --write to apply.")
        return 0
    if os.path.exists(args.out):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        bak = f"{args.out}.pre-regen-{stamp}"
        if not os.path.exists(bak):
            shutil.copy2(args.out, bak)
        print(f"[conc] backup {bak}")
    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        for w in sorted(conc):
            f.write(json.dumps({"w": w, "peak_conc": int(conc[w])}) + "\n")
    os.replace(tmp, args.out)
    n_back = sum(1 for _ in open(args.out))
    assert n_back == len(conc), "readback row count mismatch — restore " \
                                "from the backup"
    print(f"[conc] WROTE {args.out}: {n_back} rows")
    return 0


def _self_test() -> int:
    print("SELF-TEST — mb_peak_conc_regen (offline)\n")
    ok = True
    # [scan] first-BUY kept, first post-entry SELL kept, pre-entry SELL
    # ignored, non-kept wallets skipped
    import tempfile
    day = 1000.0
    rows = [{"w": "0xA", "tok": "t1", "s": "BUY", "t": day + 10},
            {"w": "0xA", "tok": "t1", "s": "BUY", "t": day + 5},   # earlier
            {"w": "0xA", "tok": "t1", "s": "SELL", "t": day + 50},
            {"w": "0xA", "tok": "t1", "s": "SELL", "t": day + 60},  # 2nd
            {"w": "0xA", "tok": "t2", "s": "SELL", "t": day + 1},  # pre-entry
            {"w": "0xA", "tok": "t2", "s": "BUY", "t": day + 20},
            {"w": "0xB", "tok": "t1", "s": "BUY", "t": day + 30}]  # not kept
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "fh.jsonl")
        with open(p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
            f.write("garbage\n")
        state, n_in, t_max = scan_positions([p], {"0xa"})
        ok1 = (n_in == 8 and t_max == day + 60
               and state[("0xa", "t1")] == [day + 5, day + 50]
               and state[("0xa", "t2")] == [day + 20, None]
               and ("0xb", "t1") not in state)
        print(f"  [scan] first-buy min, first post-entry sell, keep-set : "
              f"{ok1}")
        ok &= ok1
    # [conc] reuse of the replay sweep: overlap of t1 (exits day+50) and
    # t2 (resolves day+40) vs t2 open-to-end
    conc = conc_per_wallet(state, {"t2": day + 40.0}, day + 100.0)
    ok2 = conc == {"0xa": 2}   # t1 open 5..50, t2 open 20..40 -> overlap 2
    conc_b = conc_per_wallet(state, {}, day + 25.0)
    ok2 = ok2 and conc_b == {"0xa": 2}
    print(f"  [conc] replay-lens exits (sell/resolution/end) : {ok2}")
    ok &= ok2
    # [reuse] no local re-implementation of the sweep
    import inspect
    src = inspect.getsource(sys.modules[__name__])
    ok3 = all(("def " + name + "(") not in src
              for name in ("peak_concurrency_replay", "wallet_exits"))
    print(f"  [reuse] sweep imported from mb_backtest, not re-implemented "
          f": {ok3}")
    ok &= ok3
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="regenerate firehose/peak_conc.jsonl from current "
                    "capture (screen-gap fix 2026-09-07)")
    ap.add_argument("--wallets", default=WALLETS_DEFAULT)
    ap.add_argument("--files", default=FIREHOSE_GLOB)
    ap.add_argument("--resolutions", default=CACHE_DEFAULT)
    ap.add_argument("--min-trades", type=int,
                    default=mbt.ELIGIBILITY_MIN_TRADES)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    args = ap.parse_args()
    sys.exit(_self_test() if args.self_test else run(args))
