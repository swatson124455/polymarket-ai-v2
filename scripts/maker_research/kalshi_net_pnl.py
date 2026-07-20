#!/usr/bin/env python3
"""Kalshi weather-farm NET readout: rewards MINUS trading P&L.

The first readout measured reward capture only. This adds the missing leg: what
our resting quotes would have been FILLED on, and what those fills settle to.
Rewards and fills are accrued over the SAME observed windows, so the net is
apples-to-apples.

Method (stated so it can be attacked):
  - Our quote = the recorder's JOIN policy: 100 contracts at each side's
    reference, i.e. a yes bid @yb and a yes ask @ya (= no bid @1-ya).
  - A standing quote is live from snapshot i until snapshot i+1. Fills are
    matched from the PUBLIC trade tape inside that window.
  - Fill rule (taker semantics verified on live rows): taker_side='yes' means
    the taker BOUGHT yes, filling a resting yes ASK -> hits our ask. taker_side
    ='no' means the taker bought no (= sold yes), filling a resting yes BID ->
    hits our bid.
  - QUEUE is unknown from our data (we log shares, not per-level depth), so we
    bracket it:
      CONSERVATIVE: only prints STRICTLY THROUGH our price fill us.
      OPTIMISTIC:   prints AT our price fill us too (we're front of queue).
    Truth is between. Report both; never quote one alone.
  - Fills are capped at our 100ct per side per window (we re-quote each tick).
  - Settlement: real market result -> long yes pays $1, short yes pays -$1.
    Unsettled/unknown markets are marked at the last observed mid and flagged.
  - Rewards accrue ONLY on non-void snapshots (void pays nothing) but fills
    accrue on ALL snapshots -- being exposed while earning nothing is a real
    cost the rewards-only view hides.

Numbers are MODEL ESTIMATES on real inputs (real books, real tape, real
settlements). Not payment records.
"""
import collections
import glob
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

KDIR = r"C:/Users/samwa/.claude/projects/C--lockes-picks-polymarket-ai-v2/maker-backups/kalshi"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "kalshi-net-pnl/1.0"}
OUR_SIZE = 100.0          # JOIN policy: contracts per side
HTTP_TIMEOUT = 20
MAX_TICKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 200


def iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def get(url, tries=3):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and a < tries - 1:
                time.sleep(1.5 * (a + 1))
                continue
            return None
        except Exception:
            if a < tries - 1:
                time.sleep(0.5)
                continue
            return None
    return None


def load_samples():
    by = collections.defaultdict(list)
    for f in sorted(glob.glob(KDIR + "/samples-*.jsonl*")):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("t", "").startswith("KXTEMP"):
                    by[r["t"]].append(r)
    for t in by:
        by[t].sort(key=lambda r: r["ts"])
    return by


def fetch_tape(tkr):
    """Full public tape for a ticker (paginated)."""
    out, cursor = [], None
    for _ in range(10):
        url = f"{BASE}/markets/trades?ticker={tkr}&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        d = get(url)
        if not d:
            break
        out.extend(d.get("trades") or [])
        cursor = d.get("cursor") or d.get("next_cursor")
        if not cursor:
            break
    for t in out:
        try:
            t["_ts"] = iso(t["created_time"])
            t["_yes"] = float(t["yes_price_dollars"])
            t["_sz"] = float(t["count_fp"])
        except Exception:
            t["_ts"] = None
    return [t for t in out if t.get("_ts")]


def settle(tkr):
    """Real settlement: 1.0 if YES won, 0.0 if NO, None if not settled."""
    d = get(f"{BASE}/markets/{tkr}")
    m = (d or {}).get("market") or {}
    res = (m.get("result") or "").lower()
    if res == "yes":
        return 1.0
    if res == "no":
        return 0.0
    return None


def run():
    samples = load_samples()
    tickers = sorted(samples, key=lambda t: -len(samples[t]))[:MAX_TICKERS]
    print(f"temp tickers available={len(samples)} analysed={len(tickers)} "
          f"(sample, ranked by snapshot count)\n")

    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    n_settled = n_unsettled = 0
    contrib = []
    fills_c = fills_o = 0

    for i, tkr in enumerate(tickers, 1):
        rows = samples[tkr]
        if len(rows) < 2:
            continue
        tape = fetch_tape(tkr)
        payoff = settle(tkr)
        series = tkr.split("-")[0]
        if payoff is None:
            n_unsettled += 1
        else:
            n_settled += 1

        # cash/position under both queue assumptions
        st = {"cons": {"cash": 0.0, "pos": 0.0}, "opt": {"cash": 0.0, "pos": 0.0}}
        rew = 0.0
        last_mid = None

        for a, b in zip(rows, rows[1:]):
            try:
                yb, ya = float(a["yb"]), float(a["ya"])
            except (TypeError, ValueError):
                continue
            if not (0 < yb < 1 and 0 < ya < 1 and yb < ya):
                continue
            t0, t1 = iso(a["ts"]), iso(b["ts"])
            dt = (t1 - t0).total_seconds()
            if dt <= 0 or dt > 3600:      # skip rotation gaps
                continue
            last_mid = (yb + ya) / 2.0

            # --- rewards: only non-void snapshots pay ---
            if not a.get("void"):
                rew += float(a.get("join") or 0.0) * float(a.get("usd_day") or 0.0) * (dt / 86400.0)

            # --- fills from the tape inside this window ---
            rem = {"cons": {"bid": OUR_SIZE, "ask": OUR_SIZE},
                   "opt": {"bid": OUR_SIZE, "ask": OUR_SIZE}}
            for tr in tape:
                if not (t0 < tr["_ts"] <= t1):
                    continue
                px, sz = tr["_yes"], tr["_sz"]
                taker_yes = (tr.get("taker_side") or "").lower() == "yes"
                for mode, strict in (("cons", True), ("opt", False)):
                    if taker_yes:
                        # taker bought yes -> fills a resting yes ASK -> our ask
                        hit = (px > ya) if strict else (px >= ya)
                        if hit and rem[mode]["ask"] > 0:
                            f = min(sz, rem[mode]["ask"])
                            st[mode]["cash"] += ya * f      # we sold yes @ya
                            st[mode]["pos"] -= f
                            rem[mode]["ask"] -= f
                    else:
                        # taker bought no (= sold yes) -> fills a resting yes BID
                        hit = (px < yb) if strict else (px <= yb)
                        if hit and rem[mode]["bid"] > 0:
                            f = min(sz, rem[mode]["bid"])
                            st[mode]["cash"] -= yb * f      # we bought yes @yb
                            st[mode]["pos"] += f
                            rem[mode]["bid"] -= f

        # --- settle / mark ---
        # TWO marks, to separate the two very different risks:
        #   mid  = mark at the last mid we OBSERVED -> in-window adverse selection
        #          (what we actually lost while quoting)
        #   sett = mark at real settlement -> ALSO includes the artifact that we
        #          stop quoting after ~25min but hold the inventory to resolution
        #          with no chance to re-quote or flatten. A live maker keeps
        #          quoting; the gap between these columns is mostly artifact.
        for mode in ("cons", "opt"):
            mid_mark = last_mid if last_mid is not None else 0.5
            sett_mark = payoff if payoff is not None else mid_mark
            agg[series][f"mid_{mode}"] += st[mode]["cash"] + st[mode]["pos"] * mid_mark
            agg[series][f"trade_{mode}"] += st[mode]["cash"] + st[mode]["pos"] * sett_mark
            agg[series][f"absfill_{mode}"] += abs(st[mode]["pos"])
        fills_c += 1 if st["cons"]["pos"] else 0
        fills_o += 1 if st["opt"]["pos"] else 0
        agg[series]["rew"] += rew
        agg[series]["n"] += 1
        # per-ticker contribution, for the concentration check before presenting
        mid_mark = last_mid if last_mid is not None else 0.5
        contrib.append((tkr, rew + st["cons"]["cash"] + st["cons"]["pos"] * mid_mark))

        if i % 25 == 0:
            print(f"  ...{i}/{len(tickers)}")
        time.sleep(0.12)

    # ---------------- report ----------------
    print(f"\nsettled={n_settled} unsettled(marked at last mid)={n_unsettled}")
    print(f"tickers with any fill: conservative={fills_c} optimistic={fills_o}\n")
    hdr = (f"{'series':<14}{'mkts':>5}{'rewards$':>10}"
           f"{'inwin cons':>12}{'NET inwin':>11}"
           f"{'settle cons':>13}{'NET settle':>12}")
    print(hdr)
    print("-" * len(hdr))
    tot = collections.defaultdict(float)
    for s in sorted(agg):
        a = agg[s]
        print(f"{s:<14}{int(a['n']):>5}{a['rew']:>10.2f}"
              f"{a['mid_cons']:>12.2f}{a['rew']+a['mid_cons']:>11.2f}"
              f"{a['trade_cons']:>13.2f}{a['rew']+a['trade_cons']:>12.2f}")
        for k in ("rew", "mid_cons", "trade_cons", "mid_opt", "trade_opt", "n"):
            tot[k] += a[k]
    print("-" * len(hdr))
    print(f"{'TOTAL':<14}{int(tot['n']):>5}{tot['rew']:>10.2f}"
          f"{tot['mid_cons']:>12.2f}{tot['rew']+tot['mid_cons']:>11.2f}"
          f"{tot['trade_cons']:>13.2f}{tot['rew']+tot['trade_cons']:>12.2f}")
    print(f"\n(optimistic-queue variant: in-window {tot['mid_opt']:+.2f} -> NET "
          f"{tot['rew']+tot['mid_opt']:+.2f} | settle {tot['trade_opt']:+.2f} -> NET "
          f"{tot['rew']+tot['trade_opt']:+.2f})")
    print(f"FROZEN-POSITION ARTIFACT (settle minus in-window) = "
          f"{tot['trade_cons']-tot['mid_cons']:+.2f} conservative — this is the part "
          f"caused by us\n  holding inventory to resolution after we stopped quoting; "
          f"a live maker keeps quoting.")
    print("\nMODEL ESTIMATES on real inputs (real books, real tape, real settlements).")
    print("Rewards accrue on non-void snapshots only; fills accrue on all snapshots.")
    print("Truth sits BETWEEN the conservative and optimistic queue columns.")
    print("Covers only the ~25min/market we actually observed — NOT full market life.")


if __name__ == "__main__":
    run()
