#!/usr/bin/env python3
"""SIGNAL INVENTORY part 1b — does the PUBLIC TAPE's order-flow imbalance discriminate?

READ-ONLY, unauthenticated for the fetch. New file; edits nothing.

Probe #1 found an undocumented-to-us PUBLIC endpoint:
    GET /trade-api/v2/markets/trades?ticker=<t>&limit=1000[&max_ts=&cursor=]   -> 200
carrying `taker_side` (aggressor), `count_fp` (size) and microsecond `created_time`.
That is a signed, sized order-flow tape covering EVERY participant, not just us — the
input OFI/VPIN-style methods need. This tests whether it actually separates the fills
that ran us over from the fills that did not.

POSITIVE CONTROL : our fills followed by a >=5c adverse move within 10 min ("run over")
NEGATIVE CONTROL : our fills not followed by such a move
Both drawn from the SAME fill stream, so exposure profile is held constant.

Two-stage:  --fetch  writes the tape cache;  --report  scores it.
Usage:
  python kalshi_live/signal_tape_ofi.py --fetch  <fills.json> <tape_cache.json>
  python kalshi_live/signal_tape_ofi.py --report <fills.json> <tape_cache.json> <hist_cache.json>
"""
import json
import statistics as st
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime

HOST = "https://api.elections.kalshi.com"
ROOT = "/trade-api/v2"
SPACING = 0.35
_last = [0.0]


def get(path):
    dt = time.time() - _last[0]
    if dt < SPACING:
        time.sleep(SPACING - dt)
    _last[0] = time.time()
    req = urllib.request.Request(HOST + path,
                                 headers={"User-Agent": "kalshi-signal-probe/1.0",
                                          "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + 3 * attempt)
                continue
            return e.code, e.read()[:200].decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            return -1, str(e)[:200]
    return 429, "rate limited"


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def fetch(fills_path, out_path):
    fills = json.load(open(fills_path))
    tickers = sorted({f["ticker"] for f in fills})
    cache = {}
    for i, tk in enumerate(tickers):
        rows, cursor, pages = [], "", 0
        while pages < 6:
            p = f"{ROOT}/markets/trades?ticker={tk}&limit=1000"
            if cursor:
                p += f"&cursor={cursor}"
            stt, d = get(p)
            if stt != 200 or not isinstance(d, dict):
                print(f"  {tk} page{pages} -> {stt} {str(d)[:90]}")
                break
            r = d.get("trades", [])
            rows.extend(r)
            cursor = d.get("cursor") or ""
            pages += 1
            if not r or not cursor:
                break
        cache[tk] = [{"t": ts(x["created_time"]),
                      "s": 1 if x.get("taker_side") == "yes" else -1,
                      "q": float(x.get("count_fp", 0)),
                      "p": float(x.get("yes_price_dollars", 0)),
                      "blk": bool(x.get("is_block_trade"))}
                     for x in rows]
        print(f"  {i:>3} {tk:<32} trades={len(rows):<6} pages={pages}")
    json.dump(cache, open(out_path, "w"))
    print(f"wrote {out_path}: {sum(len(v) for v in cache.values())} trades, "
          f"{len(cache)} contracts")


def report(fills_path, tape_path, hist_path):
    fills = json.load(open(fills_path))
    tape = json.load(open(tape_path))
    hist = json.load(open(hist_path))

    print("=" * 100)
    print("A. TAPE COVERAGE for the contracts we traded")
    print("=" * 100)
    for f in ["GAS", "TEMP"]:
        sel = [tk for tk in tape if (tk.startswith("KXTEMP")) == (f == "TEMP")]
        n = [len(tape[tk]) for tk in sel]
        if not n:
            continue
        print(f"  {f}: contracts={len(sel)} trades={sum(n)} "
              f"min={min(n)} median={sorted(n)[len(n)//2]} max={max(n)} "
              f"zero_tape={sum(1 for x in n if x == 0)}")
        # what fraction of a contract's life is covered by the returned page(s)?
        spans = []
        for tk in sel:
            v = tape[tk]
            if len(v) > 1:
                spans.append((max(x["t"] for x in v) - min(x["t"] for x in v)) / 60)
        if spans:
            print(f"       tape span minutes: median={st.median(spans):.0f} "
                  f"max={max(spans):.0f}")

    # book mid, for labelling run-over
    def fair(c):
        try:
            b = float(c["yes_bid"]["close_dollars"])
            a = float(c["yes_ask"]["close_dollars"])
        except Exception:  # noqa: BLE001
            return None
        return (a + b) / 2.0 if (b > 0.0 and a < 1.0 and (a - b) <= 0.20) else None

    idx = {tk: {c["end_period_ts"]: fair(c) for c in v["candles"]}
           for tk, v in hist.items()}

    def f_at(tk, t):
        d = idx.get(tk) or {}
        t = int(t) // 60 * 60
        for k in range(0, 6):
            for cand in (t - k * 60, t + k * 60):
                if d.get(cand) is not None:
                    return d[cand]
        return None

    def ofi(tk, t0, window_s):
        """Signed taker volume in [t0-window, t0], and its normalised imbalance."""
        v = tape.get(tk) or []
        sel = [x for x in v if t0 - window_s <= x["t"] <= t0]
        if not sel:
            return None, None, 0
        net = sum(x["s"] * x["q"] for x in sel)
        gross = sum(x["q"] for x in sel)
        return net, (net / gross if gross else 0.0), len(sel)

    recs = []
    for fl in fills:
        tk = fl["ticker"]
        t = ts(fl["created_time"])
        sgn = 1 if fl["book_side"] == "bid" else -1     # +1 = we got long yes
        f0, fp10 = f_at(tk, t), f_at(tk, t + 600)
        if f0 is None or fp10 is None:
            continue
        fwd = sgn * (fp10 - f0)
        r = dict(tk=tk, fam="TEMP" if tk.startswith("KXTEMP") else "GAS",
                 sgn=sgn, fwd=fwd, runover=fwd <= -0.05)
        for w, lab in ((240, "4m"), (600, "10m")):
            net, imb, n = ofi(tk, t, w)
            # sign convention: ADVERSE flow is taker flow pushing AGAINST our new position
            r[f"adv_net_{lab}"] = (-sgn * net) if net is not None else None
            r[f"adv_imb_{lab}"] = (-sgn * imb) if imb is not None else None
            r[f"ntr_{lab}"] = n
        recs.append(r)

    print()
    print("=" * 100)
    print("B. ADVERSE TAKER FLOW BEFORE OUR FILL  (positive = tape was already pushing")
    print("   against the position we were about to take)")
    print("=" * 100)
    for f in ["GAS", "TEMP"]:
        s = [r for r in recs if r["fam"] == f]
        pos = [r for r in s if r["runover"]]
        neg = [r for r in s if not r["runover"]]
        print(f"\n  {f}: fills scored={len(s)}  run-over={len(pos)}  not={len(neg)}")
        for lab in ("4m", "10m"):
            for name, grp in (("RUN-OVER   ", pos), ("not-run-over", neg)):
                vals = [r[f"adv_imb_{lab}"] for r in grp
                        if r[f"adv_imb_{lab}"] is not None]
                nets = [r[f"adv_net_{lab}"] for r in grp
                        if r[f"adv_net_{lab}"] is not None]
                if not vals:
                    print(f"    {lab} {name} n=0")
                    continue
                print(f"    {lab} {name} n={len(vals):<4} "
                      f"mean adverse imbalance={st.mean(vals):+.3f} "
                      f"median={st.median(vals):+.3f}  "
                      f"mean adverse net ct={st.mean(nets):+7.1f}  "
                      f"median trades in window={st.median([r[f'ntr_{lab}'] for r in grp]):.0f}")
        # sweeps
        for lab in ("4m", "10m"):
            P = [r for r in pos if r[f"adv_imb_{lab}"] is not None]
            N = [r for r in neg if r[f"adv_imb_{lab}"] is not None]
            if not P or not N:
                continue
            print(f"    DETECTOR ({lab} adverse imbalance >= X): "
                  f"{'X':>6}{'TPR':>7}{'FPR':>7}{'TP':>5}{'FP':>5}")
            for X in [0.2, 0.4, 0.6, 0.8]:
                tp = sum(1 for r in P if r[f"adv_imb_{lab}"] >= X)
                fp = sum(1 for r in N if r[f"adv_imb_{lab}"] >= X)
                print(f"    {'':>29}{X:>6.1f}{tp/len(P):>7.2f}{fp/len(N):>7.2f}"
                      f"{tp:>5}{fp:>5}")
            print(f"    DETECTOR ({lab} adverse NET contracts >= X): "
                  f"{'X':>6}{'TPR':>7}{'FPR':>7}{'TP':>5}{'FP':>5}")
            for X in [25, 50, 100, 200]:
                tp = sum(1 for r in P if r[f"adv_net_{lab}"] >= X)
                fp = sum(1 for r in N if r[f"adv_net_{lab}"] >= X)
                print(f"    {'':>29}{X:>6.0f}{tp/len(P):>7.2f}{fp/len(N):>7.2f}"
                      f"{tp:>5}{fp:>5}")

    print()
    print("=" * 100)
    print("C. TAPE INTENSITY as a regime flag — trades/min in the 10 min before the fill")
    print("=" * 100)
    for f in ["GAS", "TEMP"]:
        for name, sel in (("RUN-OVER   ", [r for r in recs
                                           if r["fam"] == f and r["runover"]]),
                          ("not-run-over", [r for r in recs
                                            if r["fam"] == f and not r["runover"]])):
            v = [r["ntr_10m"] / 10.0 for r in sel]
            if v:
                print(f"  {f} {name} n={len(v):<4} median trades/min="
                      f"{st.median(v):.2f}  p90={sorted(v)[int(.9*len(v))]:.2f}")
    return 0


def main():
    mode = sys.argv[1]
    if mode == "--fetch":
        fetch(sys.argv[2], sys.argv[3])
    elif mode == "--report":
        report(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
