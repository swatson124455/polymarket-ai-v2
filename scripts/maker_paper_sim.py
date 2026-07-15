#!/usr/bin/env python3
"""Paper Maker sim — measures NET maker yield per sector. PAPER ONLY, guard-railed.

Per tick (systemd timer, every 5 min): for each rewarded market across ALL
sectors, hypothetically quote rewardsMinSize two-sided AT the touch, then
  * accrue estimated liquidity-reward share per the OFFICIAL formula
        S(v, s) = ((v - s) / v)^2 * size          (docs: liquidity-rewards)
        Q_one = bids(primary) + asks(complement), Q_two = mirror,
        Q_min = max(min(Q1,Q2), max(Q1,Q2)/3) for mid in [0.10, 0.90],
                else min(Q1,Q2);  my_share = q_mine / (q_mine + Q_min_comp)
  * detect hypothetical fills from the PUBLIC trade tape (a print strictly
    THROUGH the quote = definite fill — conservative; at-touch prints ignored,
    complement-book prints ignored -> fills are undercounted, losses are not)
  * track inventory (capped +/-3x min size per market) and paper P&L
    (realized on round trips, unrealized marked to mid).

GUARD RAILS (by construction):
  1. Trading is impossible: stdlib-only, GET-only HTTP, no keys, no order
     client, no DATABASE_URL — this process cannot place orders or touch the DB.
  2. STOP sentinel: create <base>/STOP and the next tick exits immediately.
  3. Disk cap: stops writing (and exits) if the base dir exceeds MAX_DISK_MB.
  4. Per-tick deadline + HTTP budget: hard-exits cleanly before the next tick.
  5. Meant to run under a systemd unit with ProtectSystem=strict and
     ReadWritePaths=<base> — kernel-enforced write isolation.

Usage:  python3 maker_paper_sim.py --once [--base /opt/pa2-maker-sim]
        python3 maker_paper_sim.py --report [--base ...]   # aggregate results
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "pa2-maker-paper-sim/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
BOOK = "https://clob.polymarket.com/book?token_id="
TAPE = "https://data-api.polymarket.com/trades?market={cid}&limit=200"

TICK_SECONDS = 300          # timer cadence (for reward accrual dt bound)
DEADLINE_S = 240            # hard per-tick runtime cap
HTTP_BUDGET = 520           # max HTTP requests per tick
MAX_DISK_MB = 500
MAX_MARKETS_PER_SECTOR = 25
MAX_MARKETS_TOTAL = 140
DISCOVERY_EVERY_S = 1800    # refresh market universe every 30 min
INV_CAP_MULT = 3            # |position| cap = 3 x min size
WORKERS = 8

_t0 = time.time()
_http_calls = 0


def deadline_ok():
    return time.time() - _t0 < DEADLINE_S


def get(url, timeout=10):
    """GET-only, budgeted, deadline-aware. Returns parsed JSON or None."""
    global _http_calls
    if _http_calls >= HTTP_BUDGET or not deadline_ok():
        return None
    _http_calls += 1
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


KW = [
    (r"nba|nfl|mlb|nhl|ncaa|premier|epl|serie-a|la-liga|bundesliga|ligue|ufc|atp|wta|pga|f1-|grand-prix|world-cup|fifa|uefa|copa|boxing|tennis|-vs-|derby|open-", "sports"),
    (r"lol-|league-of-legends|cs2|csgo|counter-strike|dota|valorant|esports|lck|lpl|lec|ewc", "esports"),
    (r"bitcoin|btc|ethereum|eth-|solana|xrp|doge|crypto", "crypto"),
    (r"trump|election|president|senate|congress|mayor|governor|primary|nominee|supreme-court|minister|parliament", "politics"),
    (r"temperature|highest-temp|rainfall|hurricane|snow|heat-|weather", "weather"),
    (r"fed-|interest-rate|cpi|inflation|gdp|recession|s-p-500|spx|nasdaq|spy|wti|crude|tariff|treasury", "finance"),
    (r"israel|gaza|ukraine|russia|iran|nato|ceasefire|hormuz|houthi|war-", "geopolitical"),
    (r"oscar|grammy|emmy|box-office|album|movie|netflix|spotify", "entertainment"),
]


def sector_of(m):
    c = (m.get("category") or "").strip().lower()
    if c:
        return c
    text = ((m.get("slug") or "") + " " + (m.get("question") or "")).lower()
    for pat, lab in KW:
        if re.search(pat, text):
            return lab
    return "unknown"


def S(v, s, size):
    return ((v - s) / v) ** 2 * size if v > 0 and 0 <= s < v else 0.0


def best_levels(book):
    """(best_bid, best_ask) from a raw /book dict — sort-defensive."""
    def best(side, want_max):
        px = []
        for lv in (book.get(side) or []) if isinstance(book, dict) else []:
            try:
                p = float(lv.get("price"))
            except (AttributeError, TypeError, ValueError):
                continue
            if 0.0 < p < 1.0:
                px.append(p)
        return (max(px) if want_max else min(px)) if px else None
    return best("bids", True), best("asks", False)


def side_score(book, side, mid, v):
    tot = 0.0
    for lv in (book.get(side) or []) if isinstance(book, dict) else []:
        try:
            p, sz = float(lv["price"]), float(lv["size"])
        except Exception:
            continue
        if 0 < p < 1 and sz > 0:
            tot += S(v, abs(p - mid), sz)
    return tot


# ── universe discovery (cached to disk, refreshed every 30 min) ─────────────
def discover(base):
    rows, seen = [], set()
    for page in range(21):
        q = urllib.parse.urlencode({"active": "true", "closed": "false", "limit": 100,
                                    "offset": page * 100, "order": "volume24hr",
                                    "ascending": "false"})
        data = get(f"{GAMMA}?{q}", timeout=15)
        if not data:
            break
        new = 0
        for m in data:
            if m.get("id") in seen:
                continue
            seen.add(m.get("id"))
            new += 1
            pool = 0.0
            for r in (m.get("clobRewards") or []):
                try:
                    pool += float(r.get("rewardsDailyRate") or 0)
                except Exception:
                    pass
            if pool <= 0:
                continue
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
                v = float(m.get("rewardsMaxSpread")) / 100.0
                msz = float(m.get("rewardsMinSize"))
            except Exception:
                continue
            if len(toks) < 2 or v <= 0 or msz <= 0:
                continue
            rows.append({"id": m.get("id"), "cid": m.get("conditionId"),
                         "q": (m.get("question") or "")[:70], "sector": sector_of(m),
                         "yes": toks[0], "no": toks[1], "v": v, "msz": msz,
                         "pool": pool, "end": (m.get("endDate") or "")[:10]})
        if new == 0 or len(data) < 100:
            break
    # top N per sector by pool, then global cap
    by = defaultdict(list)
    for r in rows:
        by[r["sector"]].append(r)
    picked = []
    for sec, ms in by.items():
        ms.sort(key=lambda x: -x["pool"])
        picked.extend(ms[:MAX_MARKETS_PER_SECTOR])
    picked.sort(key=lambda x: -x["pool"])
    picked = picked[:MAX_MARKETS_TOTAL]
    with open(os.path.join(base, "universe.json"), "w") as f:
        json.dump({"t": time.time(), "markets": picked}, f)
    return picked


def load_universe(base):
    try:
        u = json.load(open(os.path.join(base, "universe.json")))
        if time.time() - u.get("t", 0) < DISCOVERY_EVERY_S:
            return u["markets"]
    except Exception:
        pass
    return None


# ── one market, one tick ─────────────────────────────────────────────────────
def sample_market(m, st, now):
    b_yes = get(BOOK + m["yes"]) or {}
    b_no = get(BOOK + m["no"]) or {}
    bb, ba = best_levels(b_yes)
    if bb is None or ba is None or not (0 < bb < ba <= 1):
        return None
    mid, v, msz = (bb + ba) / 2, m["v"], m["msz"]
    s_mine = (ba - bb) / 2
    q_mine = S(v, s_mine, msz)
    if q_mine <= 0:
        return None                       # touch outside incentive band
    q1 = side_score(b_yes, "bids", mid, v) + side_score(b_no, "asks", 1 - mid, v)
    q2 = side_score(b_yes, "asks", mid, v) + side_score(b_no, "bids", 1 - mid, v)
    q_comp = max(min(q1, q2), max(q1, q2) / 3.0) if 0.10 <= mid <= 0.90 else min(q1, q2)
    share = q_mine / (q_mine + q_comp)
    dt = min(max(now - st.get("last_sample", now - TICK_SECONDS), 0), 2 * TICK_SECONDS)
    accrued = share * m["pool"] * dt / 86400.0

    # fills: prints on the YES token strictly THROUGH last tick's standing quotes
    fills = 0
    pos, cost, real = st.get("pos", 0.0), st.get("cost", 0.0), st.get("real", 0.0)
    prev_bid, prev_ask = st.get("bid"), st.get("ask")
    last_ts = st.get("last_trade_ts", now - TICK_SECONDS)
    if prev_bid is not None and prev_ask is not None and m.get("cid"):
        tape = get(TAPE.format(cid=m["cid"])) or []
        # Cap uses a FROZEN min-size (refreshed only while flat): gamma adjusts
        # rewardsMinSize intraday, and a cap recomputed from the CURRENT msz
        # let positions built under a larger msz exceed 3x (observed 5.0x,
        # 2026-07-15 verification). Post-fill guard, not pre-fill.
        cap_msz = st.get("cap_msz")
        if not cap_msz or pos == 0:
            cap_msz = st["cap_msz"] = msz
        cap = INV_CAP_MULT * cap_msz
        for tr in tape if isinstance(tape, list) else []:
            try:
                ts = float(tr.get("timestamp"))
                p = float(tr.get("price"))
                asset = str(tr.get("asset") or "")
            except Exception:
                continue
            if ts <= last_ts or asset != str(m["yes"]):
                continue
            if p < prev_bid and pos + msz <= cap + 1e-9:   # sold through my bid -> I bought
                pos += msz
                cost += msz * prev_bid
                fills += 1
            elif p > prev_ask and pos - msz >= -cap - 1e-9:  # bought through my ask -> I sold
                if pos > 0:
                    avg = cost / pos if pos else 0.0
                    real += msz * (prev_ask - avg)
                    cost -= msz * avg
                else:
                    cost -= msz * prev_ask        # short: negative cost basis
                pos -= msz
                fills += 1
            last_ts = max(last_ts, ts)
    unreal = pos * mid - cost
    st.update({"pos": round(pos, 2), "cost": round(cost, 4), "real": round(real, 4),
               "bid": bb, "ask": ba, "last_trade_ts": last_ts, "last_sample": now,
               "acc": round(st.get("acc", 0.0) + accrued, 6), "sector": m["sector"],
               "q": m["q"], "pool": m["pool"], "msz": msz})
    return {"t": round(now), "id": m["id"], "sec": m["sector"], "mid": round(mid, 4),
            "spr": round(ba - bb, 4), "shr": round(share, 4), "acc": round(accrued, 5),
            "pos": st["pos"], "fills": fills, "real": st["real"], "unreal": round(unreal, 4)}


def tick(base):
    if os.path.exists(os.path.join(base, "STOP")):
        print("STOP sentinel present — exiting without action")
        return 0
    size_mb = sum(os.path.getsize(os.path.join(r, f))
                  for r, _, fs in os.walk(base) for f in fs) / 1e6
    if size_mb > MAX_DISK_MB:
        print(f"disk cap exceeded ({size_mb:.0f}MB > {MAX_DISK_MB}MB) — exiting")
        return 1
    universe = load_universe(base) or discover(base)
    state_path = os.path.join(base, "state.json")
    try:
        state = json.load(open(state_path))
    except Exception:
        state = {}
    now = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(sample_market, m, state.setdefault(str(m["id"]), {}), now)
                for m in universe]
        for f in futs:
            try:
                r = f.result(timeout=DEADLINE_S)
                if r:
                    results.append(r)
            except Exception:
                continue
    day = time.strftime("%Y%m%d", time.gmtime(now))
    with open(os.path.join(base, f"samples-{day}.jsonl"), "a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, state_path)
    acc = sum(r["acc"] for r in results)
    fills = sum(r["fills"] for r in results)
    print(f"tick ok: {len(results)}/{len(universe)} markets sampled, "
          f"+${acc:.3f} rewards accrued this tick, {fills} paper fills, "
          f"{_http_calls} http calls, {time.time()-_t0:.0f}s")
    return 0


def report(base):
    state = json.load(open(os.path.join(base, "state.json")))
    by = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])
    for st in state.values():
        if not st.get("sector"):
            continue
        b = by[st["sector"]]
        b[0] += 1
        b[1] += st.get("acc", 0.0)
        b[2] += st.get("real", 0.0)
        b[3] += st.get("pos", 0.0) * 0  # unreal needs live mid; omitted in report
        b[4] += st.get("msz", 0.0)
    print("%-14s %4s %10s %10s %10s" % ("sector", "n", "rewards$", "realPnL$", "capital$"))
    for sec, b in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print("%-14s %4d %10.2f %10.2f %10.0f" % (sec, b[0], b[1], b[2], b[4]))
    print("%-14s %4d %10.2f %10.2f" % ("TOTAL", sum(b[0] for b in by.values()),
          sum(b[1] for b in by.values()), sum(b[2] for b in by.values())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/opt/pa2-maker-sim")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.base, exist_ok=True)
    if a.report:
        report(a.base)
    elif a.once:
        sys.exit(tick(a.base))
    else:
        ap.error("pass --once (single tick) or --report")
