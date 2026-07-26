#!/usr/bin/env python3
"""KALSHI DRIFT DETECTION — build every computable detector and test it on our real tape.

NEW FILE. Edits nothing. Pure offline analysis over kalshi_drift_cache.json (public API,
unauthenticated) + kalshi_transactions_2026-07-23.csv (our receipts).

=============================================================================
READ THIS BEFORE READING ANY NUMBER BELOW
=============================================================================

CONFOUND (measured, fatal to the briefed test as briefed):
  19 of the 20 canonical 07-22 worthless lots are KXTEMP*.  Every "profitable gas"
  negative is KXAAAGAS*.  A classifier that reads the FIRST SIX CHARACTERS OF THE
  TICKER and uses no market data at all scores TPR 0.95 / FPR 0.00 on the briefed
  split.  Therefore any pooled positives-vs-gas result is a family detector wearing
  a drift detector's clothes, and is reported ONLY as a null to beat.

  The load-bearing test is WITHIN TEMP: 22 run-over TEMP positions vs 27 TEMP
  positions that were not run over.  A detector earns its keep only there.

STRATIFICATION (measured, and it splits the loss in half):
  The positives are two different failure modes, not one.
    TAIL   entry VWAP <= 0.20 : we were sold a cheap out-of-the-money lottery ticket
           that simply never came in.  There is often NO adverse price move to detect
           -- the contract sat at 0.05 and expired at 0.00.
    RUNOVER entry VWAP >  0.20 : real adverse drift through our resting quote.
  A drift detector is only *physically capable* of catching RUNOVER. Reporting a
  pooled hit rate over both would understate a good detector and overstate a bad one.

DATA LIMITS, stated up front:
  * candles carry NO BOOK SIZE -> no retroactive depth / queue imbalance / OFI.
    Every depth-based method in the literature is UNTESTABLE here. Said, not faked.
  * public tape is NOT de-selfed: our own fills are in it. Our measured share of tape
    volume is 0.38%-2.28% (prior phase), so this is second-order but non-zero.
    De-selfing needs authed fill trade_ids, which this read-only study does not use.
  * 1-minute candles = 2x our 2-minute cycle. Nothing sub-minute is testable.
=============================================================================
"""
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

from kalshi_drift_labels import load_positions

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "kalshi_drift_cache.json")

CYCLE_S = 120           # our real decision cadence
TAIL_CUT = 0.20         # entry VWAP separating TAIL from RUNOVER
DEAD_EXITABLE = 0.01    # exitable price at/below which we cannot get out at all


# ---------------------------------------------------------------- timeline ---

def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def build_timeline(entry, side):
    """Per-minute series for one contract, expressed in OUR side's terms.

    Returns list of dicts sorted by ts:
      ts, yes_bid, yes_ask, yes_mid, value (our side mid), exitable (price we could
      hit to get out), spread, volume, oi
    """
    out = []
    for c in entry.get("candles") or []:
        ts = c.get("end_period_ts")
        yb = f((c.get("yes_bid") or {}).get("close_dollars"))
        ya = f((c.get("yes_ask") or {}).get("close_dollars"))
        px = f((c.get("price") or {}).get("close_dollars"))
        if ts is None:
            continue
        # A minute with no book quotes still has a last price; fall back so the grid
        # stays dense, but flag it.
        quoted = yb is not None and ya is not None
        if not quoted:
            if px is None:
                continue
            yb = ya = px
        mid = (yb + ya) / 2.0
        if side == "yes":
            value, exitable = mid, yb
        else:
            value, exitable = 1.0 - mid, 1.0 - ya
        out.append({
            "ts": ts, "yes_bid": yb, "yes_ask": ya, "yes_mid": mid,
            "value": value, "exitable": exitable, "spread": ya - yb,
            "volume": f(c.get("volume_fp"), 0.0) or 0.0,
            "oi": f(c.get("open_interest_fp"), 0.0) or 0.0,
            "quoted": quoted,
        })
    return sorted(out, key=lambda r: r["ts"])


def build_tape_minutes(entry, side):
    """Per-minute tape aggregates in OUR side's terms.

    adverse = taker aggressed the side OPPOSITE ours (pushes our value down).
      we hold YES -> taker_side 'no'  is adverse
      we hold NO  -> taker_side 'yes' is adverse
    """
    agg = defaultdict(lambda: {"adv": 0.0, "fav": 0.0, "n": 0, "nadv": 0})
    for t in entry.get("trades") or []:
        try:
            tt = datetime.fromisoformat(
                t["created_time"].replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001
            continue
        minute = int(tt // 60) * 60 + 60      # align to candle end_period_ts
        sz = f(t.get("count_fp"), 0.0) or 0.0
        a = agg[minute]
        a["n"] += 1
        if t.get("taker_side") != side:
            a["adv"] += sz
            a["nadv"] += 1
        else:
            a["fav"] += sz
    return agg


def find_dead_ts(tl, start_ts):
    """First minute at/after start_ts where our exitable price is <= DEAD_EXITABLE
    and NEVER recovers above it.  This is the true point of no return: after it,
    no exit at a non-zero price was available at any later minute."""
    idx = [i for i, r in enumerate(tl) if r["ts"] >= start_ts]
    if not idx:
        return None
    dead = None
    for i in reversed(idx):
        if tl[i]["exitable"] > DEAD_EXITABLE:
            break
        dead = tl[i]["ts"]
    return dead


# ---------------------------------------------------------------- detectors ---
# Every detector: (state, i) -> bool, evaluated at minute index i of the timeline.
# state carries tl (timeline), tape (per-minute agg), fills (our entry lot times),
# side, and market metadata.  All strictly TRAILING -- no future data.

def _val_at(tl, i, back_min):
    j = i - back_min
    return tl[j]["value"] if j >= 0 else None


def det_adverse_mid_drift(st, i, W=6, theta=0.08):
    """D1  ADVERSE MID DRIFT. Our side's mid fell >= theta over trailing W minutes."""
    v0 = _val_at(st["tl"], i, W)
    if v0 is None:
        return False
    return (st["tl"][i]["value"] - v0) <= -theta


def det_adverse_exitable_drift(st, i, W=6, theta=0.08):
    """D2  ADVERSE EXITABLE DRIFT. The price we could actually exit at fell >= theta
    over trailing W minutes. Harsher and more honest than mid."""
    j = i - W
    if j < 0:
        return False
    return (st["tl"][i]["exitable"] - st["tl"][j]["exitable"]) <= -theta


def det_tape_adverse_share(st, i, W=6, theta=0.75, minvol=20.0):
    """D3  ONE-SIDED PUBLIC TAPE FLOW (share). Of contracts traded in the trailing W
    minutes, the fraction aggressed AGAINST our side >= theta, on >= minvol volume."""
    adv = fav = 0.0
    for k in range(max(0, i - W + 1), i + 1):
        a = st["tape"].get(st["tl"][k]["ts"])
        if a:
            adv += a["adv"]
            fav += a["fav"]
    tot = adv + fav
    if tot < minvol:
        return False
    return (adv / tot) >= theta


def det_tape_adverse_net(st, i, W=6, theta=40.0):
    """D4  ONE-SIDED PUBLIC TAPE FLOW (net size). Net contracts aggressed against us
    in the trailing W minutes >= theta. Scale-aware version of D3 (our clip is 20)."""
    net = 0.0
    for k in range(max(0, i - W + 1), i + 1):
        a = st["tape"].get(st["tl"][k]["ts"])
        if a:
            net += a["adv"] - a["fav"]
    return net >= theta


def det_own_fill_onesided(st, i, W=8, k=2):
    """D5  ONE-SIDED OWN-FILL CLUSTERING  *** THE MANDATED DETECTOR ***
    >= k of OUR OWN entry lots on THIS (ticker, side) landed in the trailing W
    minutes, with zero entry lots on the opposite side of the same contract in the
    same window.  Zero latency, zero cost, uses only data we already own.

    LIMIT: built from CSV entry lots, so EXIT fills are not in it. That is the
    correct direction anyway (we care about inventory BUILD), but it means this is
    a build-clustering detector, not a full two-sided fill-imbalance detector."""
    t1 = st["tl"][i]["ts"]
    t0 = t1 - W * 60
    same = sum(1 for x in st["fills_same"] if t0 < x <= t1)
    opp = sum(1 for x in st["fills_opp"] if t0 < x <= t1)
    return same >= k and opp == 0


def det_own_fill_any(st, i, W=8, k=2):
    """D5c CONTROL for D5: same clustering test but ignoring one-sidedness. If D5c
    scores like D5, the 'one-sided' part carries no information."""
    t1 = st["tl"][i]["ts"]
    t0 = t1 - W * 60
    return sum(1 for x in st["fills_same"] if t0 < x <= t1) >= k


def det_run_persistence(st, i, m=6, k=5, minmove=0.02):
    """D6  TREND PERSISTENCE. Our value fell in >= k of the last m minutes AND the
    cumulative fall over those m minutes is >= minmove.

    The minmove floor is NOT cosmetic: without it this detector FIRED ON THE FLAT
    NEGATIVE CONTROL (kalshi_drift_selftest.py), because pure noise produces 5
    down-ticks in 6 minutes routinely. A count-only persistence rule is a noise
    detector. Floor added after the control failure, before any result was read."""
    if i < m:
        return False
    down = sum(1 for j in range(i - m + 1, i + 1)
               if st["tl"][j]["value"] < st["tl"][j - 1]["value"])
    moved = st["tl"][i - m]["value"] - st["tl"][i]["value"]
    return down >= k and moved >= minmove


def det_value_floor(st, i, theta=0.15):
    """D7  VALUE FLOOR (structural, not a drift detector). Our side is now worth
    <= theta. Included because it is the cheapest possible rule and any real
    detector must beat it."""
    return st["tl"][i]["value"] <= theta


def det_spread_widen(st, i, W=10, theta=0.05):
    """D8  SPREAD WIDENING / LIQUIDITY WITHDRAWAL. Current spread exceeds the
    trailing-W median by theta. Book-shape proxy -- NOTE candles have no size, so
    this sees width only, never depth."""
    if i < W:
        return False
    base = statistics.median(st["tl"][j]["spread"] for j in range(i - W, i))
    return (st["tl"][i]["spread"] - base) >= theta


def det_vol_surge_adverse(st, i, W=10, z=2.0, drift=0.03):
    """D9  ADVERSE VOLUME SURGE. Volume spike (>= z sigma over trailing W) that
    coincides with our value falling >= drift over the same window."""
    if i < W:
        return False
    hist = [st["tl"][j]["volume"] for j in range(i - W, i)]
    mu = statistics.mean(hist)
    sd = statistics.pstdev(hist)
    if sd <= 0:
        return False
    if (st["tl"][i]["volume"] - mu) / sd < z:
        return False
    v0 = _val_at(st["tl"], i, W)
    return v0 is not None and (st["tl"][i]["value"] - v0) <= -drift


def det_always(st, i):
    """N0  NULL: fires on everything. TPR 1.0 / FPR 1.0 by construction."""
    return True


DETECTORS = {
    "D1 adverse_mid_drift":     (det_adverse_mid_drift,  {"W": 6, "theta": 0.08}),
    "D2 adverse_exit_drift":    (det_adverse_exitable_drift, {"W": 6, "theta": 0.08}),
    "D3 tape_adverse_share":    (det_tape_adverse_share, {"W": 6, "theta": 0.75, "minvol": 20.0}),
    "D4 tape_adverse_net":      (det_tape_adverse_net,   {"W": 6, "theta": 40.0}),
    "D5 own_fill_onesided":     (det_own_fill_onesided,  {"W": 8, "k": 2}),
    "D5c own_fill_any(ctrl)":   (det_own_fill_any,       {"W": 8, "k": 2}),
    "D6 run_persistence":       (det_run_persistence,    {"m": 6, "k": 5, "minmove": 0.02}),
    "D7 value_floor":           (det_value_floor,        {"theta": 0.15}),
    "D8 spread_widen":          (det_spread_widen,       {"W": 10, "theta": 0.05}),
    "D9 vol_surge_adverse":     (det_vol_surge_adverse,  {"W": 10, "z": 2.0, "drift": 0.03}),
    "N0 always(null)":          (det_always,             {}),
}


# ------------------------------------------------------------------ harness ---

def make_state(p, cache, all_positions):
    """Assemble everything a detector may look at for one position."""
    e = cache.get(p["ticker"])
    if not e or not e.get("market"):
        return None
    tl = build_timeline(e, p["side"])
    if len(tl) < 8:
        return None
    mkt = e["market"]
    try:
        mkt_close = datetime.fromisoformat(
            mkt["close_time"].replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        mkt_close = p["close_ts"]

    start = p["first_entry_ts"]
    dead = find_dead_ts(tl, start)
    # window end: the last moment a decision could still have helped
    if p["label"] == "positive":
        end = min(x for x in (dead or mkt_close, mkt_close) if x)
    else:
        end = min(p["close_ts"], mkt_close)

    opp = "no" if p["side"] == "yes" else "yes"
    fills_same = [l["open_ts"] for l in p["lots"]]
    fills_opp = []
    for q in all_positions:
        if q["ticker"] == p["ticker"] and q["side"] == opp:
            fills_opp += [l["open_ts"] for l in q["lots"]]

    return {
        "p": p, "tl": tl, "tape": build_tape_minutes(e, p["side"]),
        "fills_same": sorted(fills_same), "fills_opp": sorted(fills_opp),
        "side": p["side"], "mkt": mkt, "dead_ts": dead,
        "win_start": start, "win_end": end, "mkt_close": mkt_close,
    }


def decision_points(st):
    """Indices into the timeline at which we could actually have acted: every
    CYCLE_S seconds from first entry to window end. Our cycle is 2 minutes, so
    evaluating every minute would credit detectors with reactions we cannot make."""
    idx = []
    nxt = st["win_start"]
    for i, r in enumerate(st["tl"]):
        if r["ts"] < st["win_start"] or r["ts"] > st["win_end"]:
            continue
        if r["ts"] >= nxt:
            idx.append(i)
            nxt = r["ts"] + CYCLE_S
    return idx


def evaluate(st, fn, kw):
    """Run one detector over one position. Returns (fired, first_fire_ts,
    n_points, n_fired_points)."""
    pts = decision_points(st)
    first, nf = None, 0
    for i in pts:
        try:
            hit = fn(st, i, **kw)
        except Exception:  # noqa: BLE001
            hit = False
        if hit:
            nf += 1
            if first is None:
                first = st["tl"][i]["ts"]
    return (first is not None), first, len(pts), nf


def lead_time_s(st, first_ts):
    """Seconds between the first fire and the point of no return. For a positive,
    the deadline is dead_ts (last chance to exit above zero) or market close."""
    if first_ts is None:
        return None
    deadline = st["dead_ts"] or st["mkt_close"]
    return deadline - first_ts


def pct(a, b):
    return (a / b) if b else float("nan")


def fmt_lead(vals):
    if not vals:
        return "     --        "
    v = sorted(vals)
    med = statistics.median(v)
    return f"{med/60:5.0f}m [{v[0]/60:4.0f},{v[-1]/60:5.0f}]"


# --------------------------------------------------------------------- main ---

def run_group(states, name, fn, kw):
    fired, leads, pts, fpts = 0, [], 0, 0
    for st in states:
        hit, first, n, k = evaluate(st, fn, kw)
        pts += n
        fpts += k
        if hit:
            fired += 1
            lt = lead_time_s(st, first)
            if lt is not None:
                leads.append(lt)
    return {"n": len(states), "fired": fired, "leads": leads,
            "points": pts, "fired_points": fpts}


def main():
    cache = json.load(open(CACHE))
    positions = load_positions()

    states = {}
    for p in positions:
        st = make_state(p, cache, positions)
        if st:
            states[(p["ticker"], p["side"])] = st

    print(f"positions with usable timeline: {len(states)} / {len(positions)}")

    def group(pred):
        return [st for st in states.values() if pred(st["p"])]

    POS = group(lambda p: p["label"] == "positive")
    POS_T = [st for st in POS if st["p"]["vwap_entry"] > TAIL_CUT]
    POS_L = [st for st in POS if st["p"]["vwap_entry"] <= TAIL_CUT]
    NEG_S = group(lambda p: p["label"] == "neg_strict")
    NEG_B = group(lambda p: p["label"] in ("neg_strict", "neg_broad"))
    TCTL = group(lambda p: p["label"] == "temp_control")

    print(f"\nGROUPS")
    print(f"  POSITIVES (run over)            n={len(POS):>3}  "
          f"pnl {sum(s['p']['pnl'] for s in POS):>8.2f}")
    print(f"    of which RUNOVER (entry>{TAIL_CUT})  n={len(POS_T):>3}  "
          f"pnl {sum(s['p']['pnl'] for s in POS_T):>8.2f}")
    print(f"    of which TAIL     (entry<={TAIL_CUT}) n={len(POS_L):>3}  "
          f"pnl {sum(s['p']['pnl'] for s in POS_L):>8.2f}")
    print(f"  NEG strict  (gas, profitable)   n={len(NEG_S):>3}  "
          f"pnl {sum(s['p']['pnl'] for s in NEG_S):>8.2f}")
    print(f"  NEG broad   (all gas, survived) n={len(NEG_B):>3}  "
          f"pnl {sum(s['p']['pnl'] for s in NEG_B):>8.2f}")
    print(f"  TEMP control(temp, survived)    n={len(TCTL):>3}  "
          f"pnl {sum(s['p']['pnl'] for s in TCTL):>8.2f}")

    # window lengths -- exposure asymmetry must be visible, not hidden
    print("\nWINDOW LENGTHS (min) median [min,max] and decision points/position")
    for nm, g in (("POS", POS), ("NEG_strict", NEG_S), ("NEG_broad", NEG_B),
                  ("TEMP_ctrl", TCTL)):
        if not g:
            continue
        w = sorted((st["win_end"] - st["win_start"]) / 60 for st in g)
        dp = sorted(len(decision_points(st)) for st in g)
        print(f"  {nm:<11} {statistics.median(w):5.0f} [{w[0]:4.0f},{w[-1]:5.0f}]"
              f"   points {statistics.median(dp):4.0f}")

    groups = [("POS_all", POS), ("POS_RUNOVER", POS_T), ("POS_TAIL", POS_L),
              ("NEG_strict", NEG_S), ("NEG_broad", NEG_B), ("TEMP_ctrl", TCTL)]

    results = {}
    print("\n" + "=" * 118)
    print("DETECTOR RESULTS  (fire = detector ON at >=1 two-minute decision point "
          "inside the position's window)")
    print("=" * 118)
    hdr = (f"{'detector':<26}{'POSall':>8}{'RUNOVR':>8}{'TAIL':>8}"
           f"{'NEGstr':>8}{'NEGbrd':>8}{'TEMPctl':>9}"
           f"{'lead(RUNOVR) med[min,max]':>28}{'dutycyc':>9}")
    print(hdr)
    print("-" * 118)
    for label, (fn, kw) in DETECTORS.items():
        r = {g: run_group(gs, g, fn, kw) for g, gs in groups}
        results[label] = r
        duty = pct(sum(x["fired_points"] for x in r.values()),
                   sum(x["points"] for x in r.values()))
        print(f"{label:<26}"
              f"{pct(r['POS_all']['fired'], r['POS_all']['n']):>8.2f}"
              f"{pct(r['POS_RUNOVER']['fired'], r['POS_RUNOVER']['n']):>8.2f}"
              f"{pct(r['POS_TAIL']['fired'], r['POS_TAIL']['n']):>8.2f}"
              f"{pct(r['NEG_strict']['fired'], r['NEG_strict']['n']):>8.2f}"
              f"{pct(r['NEG_broad']['fired'], r['NEG_broad']['n']):>8.2f}"
              f"{pct(r['TEMP_ctrl']['fired'], r['TEMP_ctrl']['n']):>9.2f}"
              f"{fmt_lead(r['POS_RUNOVER']['leads']):>28}"
              f"{duty:>9.2f}")

    json.dump(
        {k: {g: {kk: vv for kk, vv in v.items() if kk != "leads"}
             | {"lead_med_s": (statistics.median(v["leads"]) if v["leads"] else None),
                "leads_s": v["leads"]}
             for g, v in r.items()} for k, r in results.items()},
        open(os.path.join(HERE, "kalshi_drift_detect.json"), "w"), indent=1)
    print(f"\nwrote {os.path.join(HERE, 'kalshi_drift_detect.json')}")
    return states, groups, results


if __name__ == "__main__":
    main()
