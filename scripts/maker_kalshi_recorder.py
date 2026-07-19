#!/usr/bin/env python3
"""Kalshi Maker RECORDER ARM — read-only, unauthenticated, MEASUREMENT ONLY.

Never trades. No keys, no account, no auth headers. Public GETs to
api.elections.kalshi.com only, throttled, daily request budget, STOP sentinel.

What it measures (per the CFTC-filed Liquidity Incentive Program formula,
KalshiEX filing 2026-02-11, effective 2026-02-28):
  1. Hourly pool census — every active liquidity incentive program (the
     subsidy ledger; catches the Jul-19 WC cliff and the Sep-1 sunset).
  2. Per-market hypothetical maker share: replays the exact qualifying-walk
     scoring (Score = DF^ticks x size, per side, bounded at Target Size,
     two-sided-void rule) with our hypothetical resting orders inserted:
       - JOIN policy: 100 contracts at the reference price, both sides.
       - ACTIVATE policy: on markets void for lack of Target Size, size our
         orders to complete the target (captures a DILUTED share — any pre-existing
         sub-target bids stay in the qualifying set; ~100% only when the side was
         empty; capital logged).
     Per valid snapshot the sum of all users' scores is exactly 2.0
     (yes-normalized 1.0 + no-normalized 1.0), so our period share is
     mean(our_snapshot_score / 2) over valid snapshots.

Stop:   sudo touch /opt/pa2-maker-kalshi/STOP
Kill:   sudo systemctl disable --now polymarket-maker-kalshi.timer
Report: python3 maker_kalshi_recorder.py --report
"""
import gzip
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
STOP_FILE = os.path.join(DATA_DIR, "STOP")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

REQ_SPACING_S = 0.55          # <=2 req/s hard
HTTP_TIMEOUT_S = 20
# 288 ticks/day x ~241 req = ~69.4K steady-state; 80K leaves headroom without
# a time-of-day truncation (40K exhausted at ~14:00Z daily = biased means)
DAY_BUDGET = 80_000           # requests/day (attempts, not successes), persisted
FOOTPRINT_TOP = 100           # top markets by pool $/day
FOOTPRINT_RANDOM = 20         # random tail markets (day-seeded)
PER_SERIES_CAP = 12
JOIN_SIZE = 100.0             # contracts, hypothetical join order
DISK_CAP_MB = 400
KEEP_DAYS = 14
CENSUS_INTERVAL_S = 3300      # ~hourly

_last_req = [0.0]


class Budget:
    def __init__(self, state):
        today = utcnow().strftime("%Y-%m-%d")
        b = state.get("budget") or {}
        if b.get("day") != today:
            b = {"day": today, "used": 0}
        self.b = b
        state["budget"] = b

    def spend(self):
        self.b["used"] += 1

    def exhausted(self):
        return self.b["used"] >= DAY_BUDGET


def utcnow():
    return datetime.now(timezone.utc)


def get(path, budget):
    if budget.exhausted():
        raise RuntimeError("daily request budget exhausted")
    wait = REQ_SPACING_S - (time.time() - _last_req[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "maker-kalshi-recorder/1.0 (read-only measurement)"})
    last_err = None
    for attempt in range(4):
        if budget.exhausted():
            raise RuntimeError("daily request budget exhausted")
        budget.spend()  # count ATTEMPTS (incl. 429s), not successes
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
                _last_req[0] = time.time()
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                time.sleep(4 * (attempt + 1))
                continue
            if e.code >= 500:
                time.sleep(2)
                continue
            raise
        except Exception as e:  # timeouts, connection resets
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"retries exhausted {path}: {last_err}")


# ---------------- scoring (pure functions; unit-tested offline) ----------------

def qualifying_walk(levels, target):
    """levels: [(price, size)] any order, one side, price in dollars (bids for
    that side; a yes ask is a no bid and is NOT in this list). Returns
    (ref_price, [(price, size) qualifying levels], total_qualifying_size).
    Per the filing: walk from best (highest) price down, adding ALL size at
    each price, stop once cumulative >= target; if never reached -> empty.
    Reference price must exist and be < 1.00 (max price) or -> empty."""
    if not levels or target <= 0:
        return None, [], 0.0
    lv = sorted(((float(p), float(s)) for p, s in levels if float(s) > 0),
                key=lambda x: -x[0])
    if not lv or lv[0][0] >= 1.0:
        return None, [], 0.0
    ref = lv[0][0]
    q, tot = [], 0.0
    for price, size in lv:
        q.append((price, size))
        tot += size
        if tot >= target:
            return ref, q, tot
    return None, [], 0.0  # not enough size anywhere -> no qualifying bids


def merge_orders(levels, ours):
    """Aggregate our hypothetical orders into a level list by price."""
    agg = defaultdict(float)
    for p, s in levels:
        agg[round(float(p), 4)] += float(s)
    for p, s in ours:
        agg[round(float(p), 4)] += float(s)
    return [(p, s) for p, s in agg.items()]


def side_share(levels, ours, target, df, tick):
    """Our normalized share of one side's qualifying score, with our orders
    included in the book. Returns (share, ref, qualifying_total, our_in_set)."""
    merged = merge_orders(levels, ours)
    ref, q, tot = qualifying_walk(merged, target)
    if ref is None:
        return 0.0, None, 0.0, False
    ours_by_price = defaultdict(float)
    for p, s in ours:
        ours_by_price[round(float(p), 4)] += float(s)
    total_score = 0.0
    our_score = 0.0
    our_in = False
    for price, size in q:
        n_ticks = max(0, int(round((ref - price) / tick)))
        w = (df ** n_ticks)
        total_score += w * size
        mine = ours_by_price.get(round(price, 4), 0.0)
        if mine > 0:
            # our orders at this price are inside the qualifying set
            our_score += w * min(mine, size)
            our_in = True
    if total_score <= 0:
        return 0.0, ref, tot, False
    return our_score / total_score, ref, tot, our_in


def snapshot_scores(yes_levels, no_levels, target, df, tick, tick_no=None):
    """Compute JOIN and ACTIVATE policy snapshot scores for one book snapshot.

    Returns dict with:
      base_void: True if the market (without us) fails two-sided Target Size
      join: {score, valid}  — 100 ct at each side's reference (needs both refs)
      act:  {score, valid, capital_usd, add_yes, add_no} — top-up to target
    Snapshot score = yes_share + no_share (max 2.0); only counts when the
    with-us book satisfies two-sided qualifying liquidity.
    tick/tick_no: per-side tick sizes (no-side ladder maps to yes 1-p)."""
    if tick_no is None:
        tick_no = tick
    ref_y0, _, tot_y0 = qualifying_walk(yes_levels, target)
    ref_n0, _, tot_n0 = qualifying_walk(no_levels, target)
    # best existing bid on each side (for placement), regardless of qualifying
    best_y = max((float(p) for p, s in yes_levels if float(s) > 0), default=None)
    best_n = max((float(p) for p, s in no_levels if float(s) > 0), default=None)
    base_void = ref_y0 is None or ref_n0 is None

    out = {"base_void": base_void,
           "join": {"score": 0.0, "valid": False},
           "act": {"score": 0.0, "valid": False, "capital_usd": None,
                   "add_yes": 0.0, "add_no": 0.0}}

    if best_y is None or best_n is None or best_y >= 1.0 or best_n >= 1.0:
        return out  # can't price a side; nothing to join or activate

    # JOIN: 100 ct at each side's best price
    sy, ry, _, _ = side_share(yes_levels, [(best_y, JOIN_SIZE)], target, df, tick)
    sn, rn, _, _ = side_share(no_levels, [(best_n, JOIN_SIZE)], target, df, tick_no)
    if ry is not None and rn is not None:
        out["join"] = {"score": sy + sn, "valid": True}

    # ACTIVATE: top each side up to target at its best price (covers the
    # base_void farm case; on non-void markets this reduces to a plain join).
    # NB: on a void side qualifying_walk reports tot=0, but every resting
    # contract still counts toward the target once the walk succeeds — size
    # the top-up against the side's TOTAL resting size, not the walk result.
    side_tot_y = sum(float(s) for _, s in yes_levels)
    side_tot_n = sum(float(s) for _, s in no_levels)
    add_y = max(0.0, target - side_tot_y) if ref_y0 is None else JOIN_SIZE
    add_n = max(0.0, target - side_tot_n) if ref_n0 is None else JOIN_SIZE
    ay, ray, _, _ = side_share(yes_levels, [(best_y, add_y)] if add_y > 0 else [], target, df, tick)
    an, ran, _, _ = side_share(no_levels, [(best_n, add_n)] if add_n > 0 else [], target, df, tick_no)
    if ray is not None and ran is not None:
        out["act"] = {"score": ay + an, "valid": True,
                      "capital_usd": round(best_y * add_y + best_n * add_n, 2),
                      "add_yes": round(add_y, 2), "add_no": round(add_n, 2)}
    return out


def tick_size_for(market, ref_price):
    """Tick from price_ranges step at the reference price; default $0.01."""
    try:
        for r in market.get("price_ranges") or []:
            if float(r["start"]) <= ref_price <= float(r["end"]):
                return float(r["step"])
        return 0.01
    except Exception:
        return 0.01


def tick_size_no_side(market, no_price):
    """price_ranges describe the YES price ladder; a no bid at p maps to the
    yes region 1-p. Approximation: uses the step at the mapped point (a walk
    crossing a band boundary blends steps; depth is a few ticks in practice)."""
    return tick_size_for(market, max(0.0, min(1.0, 1.0 - no_price)))


# ---------------- data plumbing ----------------

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def append_jsonl(name, row):
    path = os.path.join(DATA_DIR, f"{name}-{utcnow().strftime('%Y%m%d')}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def rotate_and_cap():
    now = utcnow()
    today = now.strftime("%Y%m%d")
    total = 0
    for fn in sorted(os.listdir(DATA_DIR)):
        p = os.path.join(DATA_DIR, fn)
        if not os.path.isfile(p):
            continue
        total += os.path.getsize(p)
        if fn.endswith(".jsonl") and today not in fn:
            with open(p, "rb") as src, gzip.open(p + ".gz", "wb") as dst:
                dst.write(src.read())
            os.remove(p)
        if fn.endswith(".jsonl.gz"):
            try:
                stamp = fn.split("-")[-1].split(".")[0]
                if (now - datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=timezone.utc)).days > KEEP_DAYS:
                    os.remove(p)
            except Exception:
                pass
    if total > DISK_CAP_MB * 1024 * 1024:
        raise RuntimeError(f"disk cap exceeded: {total/1e6:.0f}MB > {DISK_CAP_MB}MB")


def fetch_programs(budget):
    progs, cursor = [], ""
    for _ in range(20):
        d = get("/incentive_programs?status=active&limit=10000" + (f"&cursor={cursor}" if cursor else ""), budget)
        ps = d.get("incentive_programs", [])
        progs.extend(ps)
        cursor = d.get("next_cursor") or ""
        if not cursor or not ps:
            break
    return progs


def program_usd_per_day(p):
    try:
        s = datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        if e.tzinfo is None:
            e = e.replace(tzinfo=timezone.utc)
        days = max((e - s).total_seconds() / 86400.0, 1 / 24)
        return ((p.get("period_reward") or 0) / 10000.0) / days, e
    except Exception:
        return 0.0, None


def series_of(t):
    return t.split("-")[0] if t else "?"


def select_footprint(progs):
    now = utcnow()
    rows = []
    for p in progs:
        usd_day, end = program_usd_per_day(p)
        if end is None or end < now + timedelta(minutes=30):
            continue
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        # a program missing its scoring parameters cannot be scored honestly —
        # skip rather than fabricate a target/DF (DF=0 is legitimate, keep it)
        if p.get("target_size_fp") is None or p.get("discount_factor_bps") is None:
            continue
        if not p.get("market_ticker"):
            continue
        rows.append({
            "ticker": p["market_ticker"],
            "usd_day": usd_day,
            "reward_usd": (p.get("period_reward") or 0) / 10000.0,
            "target": float(p["target_size_fp"]),
            "df": p["discount_factor_bps"] / 10000.0,
            "end": end.isoformat(),
        })
    rows.sort(key=lambda r: (-r["usd_day"], r["ticker"]))  # ticker tiebreak: stable across ticks
    picked, per_series = [], defaultdict(int)
    for r in rows:
        s = series_of(r["ticker"])
        if per_series[s] >= PER_SERIES_CAP:
            continue
        picked.append(r)
        per_series[s] += 1
        if len(picked) >= FOOTPRINT_TOP:
            break
    picked_ids = {r["ticker"] for r in picked}
    rest = [r for r in rows if r["ticker"] not in picked_ids]
    rng = random.Random(utcnow().strftime("%Y%m%d"))
    picked += rng.sample(rest, min(FOOTPRINT_RANDOM, len(rest)))
    return picked


def census_row(progs):
    tot = sum((p.get("period_reward") or 0) for p in progs) / 10000.0
    by_series = defaultdict(float)
    for p in progs:
        by_series[series_of(p.get("market_ticker") or "?")] += (p.get("period_reward") or 0) / 10000.0
    top = sorted(by_series.items(), key=lambda kv: -kv[1])[:40]
    return {"ts": utcnow().isoformat(), "kind": "census",
            "n_programs": len(progs), "total_scheduled_usd": round(tot, 2),
            "top_series_usd": [[k, round(v, 2)] for k, v in top]}


def sample_market(m, budget, accum):
    """Fetch + score one footprint market. Returns True if sampled.
    Raises only RuntimeError('...budget...') to stop the tick cleanly."""
    t = m["ticker"]
    try:
        ob = get(f"/markets/{t}/orderbook", budget).get("orderbook_fp") or {}
        md = get(f"/markets/{t}", budget).get("market") or {}
        yes = ob.get("yes_dollars") or []
        no = ob.get("no_dollars") or []
        best_y = max((float(p) for p, s in yes), default=None)
        best_n = max((float(p) for p, s in no), default=None)
        tick = tick_size_for(md, best_y if best_y is not None else 0.5)
        tick_no = tick_size_no_side(md, best_n if best_n is not None else 0.5)
        res = snapshot_scores(yes, no, m["target"], m["df"], tick, tick_no)
    except RuntimeError as e:
        if "budget" in str(e):
            raise
        return False
    except Exception:
        return False  # one malformed market must not kill the tick

    a = accum.setdefault(t, {"series": series_of(t), "n": 0, "n_valid_join": 0,
                             "n_valid_act": 0, "n_base_void": 0,
                             "sum_join": 0.0, "sum_act": 0.0,
                             "usd_day": m["usd_day"], "cap_max": 0.0,
                             "cap_sum": 0.0})
    a["n"] += 1
    a["usd_day"] = m["usd_day"]
    a["last_ts"] = time.time()
    if res["base_void"]:
        a["n_base_void"] += 1
    if res["join"]["valid"]:
        a["n_valid_join"] += 1
        a["sum_join"] += res["join"]["score"]
    if res["act"]["valid"]:
        a["n_valid_act"] += 1
        a["sum_act"] += res["act"]["score"]
        cap = res["act"]["capital_usd"] or 0.0
        a["cap_max"] = max(a["cap_max"], cap)
        a["cap_sum"] = a.get("cap_sum", 0.0) + cap

    append_jsonl("samples", {
        "ts": utcnow().isoformat(), "t": t,
        "yb": md.get("yes_bid_dollars"), "ya": md.get("yes_ask_dollars"),
        "tick": tick, "tick_no": tick_no,
        "vol24": md.get("volume_24h_fp"),
        "void": res["base_void"],
        "join": round(res["join"]["score"], 5) if res["join"]["valid"] else None,
        "act": round(res["act"]["score"], 5) if res["act"]["valid"] else None,
        "act_cap": res["act"]["capital_usd"],
        "lv_y": len(yes), "lv_n": len(no),
        "usd_day": round(m["usd_day"], 2),
        # rw/pend since 2026-07-18 era: total program reward $ + program end —
        # usd_day is a RATE; short windows make rate-sums exceed scheduled money
        "rw": round(m["reward_usd"], 2), "pend": m["end"],
    })
    return True


def run_once():
    if os.path.exists(STOP_FILE):
        print("STOP sentinel present; exiting")
        return 0
    os.chdir(DATA_DIR)
    try:
        rotate_and_cap()
    except RuntimeError as e:
        print(f"halted: {e}")  # disk cap: loud, non-crashing, self-heals via age-out
        return 0
    state = load_state()
    budget = Budget(state)

    sampled = 0
    footprint = []
    progs = []
    try:
        progs = fetch_programs(budget)
        if time.time() - state.get("last_census_ts", 0) >= CENSUS_INTERVAL_S:
            append_jsonl("census", census_row(progs))
            state["last_census_ts"] = time.time()

        footprint = select_footprint(progs)
        if not footprint:
            print("WARNING: footprint empty — program feed shape changed?")
        accum = state.setdefault("accum", {})
        for m in footprint:
            try:
                if sample_market(m, budget, accum):
                    sampled += 1
            except RuntimeError as e:
                print(f"stopping tick early: {e}")
                break

        # prune accum entries not sampled for 7d (history stays in samples jsonl)
        cutoff = time.time() - 7 * 86400
        for t in [t for t, a in accum.items() if a.get("last_ts", 0) < cutoff]:
            del accum[t]
    except RuntimeError as e:
        print(f"halted: {e}")  # e.g. budget exhausted during census — clean exit
    finally:
        save_state(state)  # budget spend must survive any crash path

    # symmetric with the empty-footprint warning: a full footprint that sampled
    # nothing = total measurement outage (endpoint change, systematic fault), NOT ok
    status = "tick ok" if not (footprint and sampled == 0) else \
        "WARNING footprint>0 but sampled=0 (measurement outage?)"
    print(f"{status}: programs={len(progs)} footprint={len(footprint)} sampled={sampled} "
          f"budget_used={state['budget']['used']}/{DAY_BUDGET}")
    return 0


def report():
    state = load_state()
    accum = state.get("accum", {})
    if not accum:
        print("no data yet")
        return 0
    # only markets sampled in the last 30 min count toward the CURRENT total —
    # departed/rotated markets otherwise inflate the estimate forever
    live_cut = time.time() - 1800
    rows = []
    for t, a in accum.items():
        if a.get("last_ts", 0) < live_cut:
            continue
        share_join = (a["sum_join"] / a["n_valid_join"] / 2.0) if a["n_valid_join"] else 0.0
        share_act = (a["sum_act"] / a["n_valid_act"] / 2.0) if a["n_valid_act"] else 0.0
        cap_avg = (a.get("cap_sum", 0.0) / a["n_valid_act"]) if a["n_valid_act"] else 0.0
        rows.append((t, a["series"], a["usd_day"], share_join * a["usd_day"],
                     share_act * a["usd_day"], a["n_base_void"] / max(a["n"], 1),
                     cap_avg, a["n"]))
    if not rows:
        print("no live-footprint data in the last 30 min (arm stopped?)")
        return 0
    rows.sort(key=lambda r: -r[4])
    print(f"{'ticker':42} {'series':22} {'pool$/d':>8} {'join$/d':>8} {'act$/d':>8} {'void%':>6} {'capavg$':>8} {'n':>4}")
    tot_j = tot_a = 0.0
    by_series = defaultdict(lambda: [0.0, 0.0])
    for t, s, pd, jd, ad, vr, cap, n in rows[:40]:
        print(f"{t:42} {s:22} {pd:8.2f} {jd:8.2f} {ad:8.2f} {vr*100:6.1f} {cap:8.0f} {n:4d}")
    for t, s, pd, jd, ad, vr, cap, n in rows:
        tot_j += jd
        tot_a += ad
        by_series[s][0] += jd
        by_series[s][1] += ad
    print(f"\nTOTAL est $/day  join(100ct)={tot_j:.2f}  activate={tot_a:.2f}  (model estimates, footprint only)")
    print("\nby series (join$/d, act$/d):")
    for s, (j, a) in sorted(by_series.items(), key=lambda kv: -kv[1][1])[:20]:
        print(f"  {s:24} {j:8.2f} {a:8.2f}")
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        sys.exit(report())
    sys.exit(run_once())
