#!/usr/bin/env python3
"""Paper Maker sim V2 — EXPERIMENT ARM. 100% separate from the v1 control.

Same measurement core and guard rails as maker_paper_sim.py (v1, the untouched
naive control), plus the data-derived policy rules from the 2026-07-15 deep
dive. Runs in ITS OWN directory/service/timer/state — shares nothing with v1.

Policy deltas vs the naive control (each one tagged in output so the arms are
comparable rule-by-rule):
  GATE in-play   : esports/sports markets are not quoted after gameStartTime
                   (in-play inventory was the entire v1 loss).
  GATE extreme-wx: weather markets with mid outside [0.10, 0.90] are not quoted
                   (v1: -$4.13/market there vs +$1.19 in the middle band).
  GATE last-hours: same-day-ending markets are not quoted after 19:00 UTC
                   (index dailies flip near close; v1 finance-middle -$5.83/mkt).
  VOL-PULL       : quotes pulled for 10 min after a >2pt tick-to-tick mid move
                   (v1's entire net loss sat in the high-vol third).
  2-MIN ticks    : re-prices every 2 min vs v1's 5 (stale-quote exposure ↓60%).
  WIDTH A/B      : markets split by id parity — even: quote AT touch (v1-like);
                   odd: quote HALFWAY into the incentive band (~25-50% of the
                   quadratic score, far fewer toxic fills). Arm recorded per row.
Sizing unchanged (min size, per operator: "don't tinker yet, keep tabs").

Guard rails identical to v1: stdlib-only, GET-only, no keys, no DATABASE_URL,
STOP sentinel, disk cap, deadline + HTTP budget, inventory caps, systemd
ProtectSystem=strict sandbox. This arm cannot trade and cannot touch v1.

Usage:  python3 maker_paper_sim_v2.py --once [--base /opt/pa2-maker-sim-v2]
        python3 maker_paper_sim_v2.py --report [--base ...]
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
from datetime import datetime, timezone

UA = {"User-Agent": "pa2-maker-paper-sim-v2/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
BOOK = "https://clob.polymarket.com/book?token_id="
TAPE = "https://data-api.polymarket.com/trades?market={cid}&limit=200"

TICK_SECONDS = 120
DEADLINE_S = 100
HTTP_BUDGET = 520
MAX_DISK_MB = 500
MAX_MARKETS_PER_SECTOR = 25
MAX_MARKETS_TOTAL = 140
DISCOVERY_EVERY_S = 1800
INV_CAP_MULT = 3
WORKERS = 8
VOL_PULL_PTS = 0.02
VOL_PULL_S = 600
LAST_HOURS_GATE_UTC = 19

_t0 = time.time()
_http_calls = 0


def deadline_ok():
    return time.time() - _t0 < DEADLINE_S


def get(url, timeout=10):
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


def fetch_tape(cid, since_ts):
    """All tape prints since since_ts, OLDEST-FIRST (see v1 docstring: fixes
    the 200-print truncation AND the newest-first iteration skip that limited
    fill detection to <=1 print per market per tick — verified 2026-07-15)."""
    seen, out = set(), []
    for page in range(3):
        t = get(TAPE.format(cid=cid) + "&offset=%d" % (200 * page)) or []
        if not isinstance(t, list) or not t:
            break
        oldest = None
        for tr in t:
            if not isinstance(tr, dict):
                continue
            k = (tr.get("transactionHash"), tr.get("timestamp"),
                 tr.get("price"), tr.get("size"), tr.get("asset"))
            if k in seen:
                continue
            seen.add(k)
            out.append(tr)
            try:
                ts = float(tr.get("timestamp"))
                oldest = ts if oldest is None else min(oldest, ts)
            except (TypeError, ValueError):
                continue
        if len(t) < 200 or (oldest is not None and oldest <= since_ts):
            break
    def _ts(tr):
        try:
            return float(tr.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0.0
    out.sort(key=_ts)
    return out


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


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def S(v, s, size):
    return ((v - s) / v) ** 2 * size if v > 0 and 0 <= s < v else 0.0


def best_levels(book):
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
                         "pool": pool, "end": (m.get("endDate") or "")[:10],
                         "game_start": parse_iso(m.get("gameStartTime"))})
        if new == 0 or len(data) < 100:
            break
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


def gate(m, st, now, mid):
    """Return a gate reason string, or None if the market is quotable."""
    if m["sector"] in ("esports", "sports") and m.get("game_start") and now >= m["game_start"]:
        return "in_play"
    if m["sector"] == "weather" and mid is not None and not (0.10 <= mid <= 0.90):
        return "extreme_wx"
    if m.get("end") == time.strftime("%Y-%m-%d", time.gmtime(now)) \
            and time.gmtime(now).tm_hour >= LAST_HOURS_GATE_UTC:
        return "last_hours"
    if now < st.get("pull_until", 0):
        return "vol_pull"
    return None


def sample_market(m, st, now):
    b_yes = get(BOOK + m["yes"]) or {}
    b_no = get(BOOK + m["no"]) or {}
    bb, ba = best_levels(b_yes)
    if bb is None or ba is None or not (0 < bb < ba <= 1):
        return None
    mid, v, msz = (bb + ba) / 2, m["v"], m["msz"]

    # vol trigger BEFORE gating decision (arming the pull applies from next check)
    last_mid = st.get("last_mid")
    if last_mid is not None and abs(mid - last_mid) > VOL_PULL_PTS:
        st["pull_until"] = now + VOL_PULL_S
    st["last_mid"] = mid

    g = gate(m, st, now, mid)
    if g is not None:
        # record the gate; also settle fill detection window (no standing quotes)
        st.update({"bid": None, "ask": None, "last_sample": now,
                   "sector": m["sector"], "q": m["q"], "pool": m["pool"], "msz": msz,
                   "gates": {**st.get("gates", {}), g: st.get("gates", {}).get(g, 0) + 1}})
        return {"t": round(now), "id": m["id"], "sec": m["sector"], "mid": round(mid, 4),
                "gate": g}

    # WIDTH A/B by market-id parity: even -> at touch; odd -> halfway into band
    try:
        arm = "wide" if int(str(m["id"])[-1]) % 2 else "touch"
    except Exception:
        arm = "touch"
    s_touch = (ba - bb) / 2
    s_mine = max(s_touch, v / 2) if arm == "wide" else s_touch
    q_mine = S(v, s_mine, msz)
    if q_mine <= 0:
        return None
    my_bid, my_ask = mid - s_mine, mid + s_mine

    q1 = side_score(b_yes, "bids", mid, v) + side_score(b_no, "asks", 1 - mid, v)
    q2 = side_score(b_yes, "asks", mid, v) + side_score(b_no, "bids", 1 - mid, v)
    q_comp = max(min(q1, q2), max(q1, q2) / 3.0) if 0.10 <= mid <= 0.90 else min(q1, q2)
    share = q_mine / (q_mine + q_comp)
    dt = min(max(now - st.get("last_sample", now - TICK_SECONDS), 0), 2 * TICK_SECONDS)
    accrued = share * m["pool"] * dt / 86400.0

    fills = 0
    pos, cost, real = st.get("pos", 0.0), st.get("cost", 0.0), st.get("real", 0.0)
    prev_bid, prev_ask = st.get("bid"), st.get("ask")
    last_ts = st.get("last_trade_ts", now - TICK_SECONDS)
    if prev_bid is not None and prev_ask is not None and m.get("cid"):
        tape = fetch_tape(m["cid"], last_ts)
        # Frozen-msz cap + post-fill guard (see v1 note: gamma adjusts
        # rewardsMinSize intraday; current-msz caps drifted to 5x observed).
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
            if p < prev_bid and pos + msz <= cap + 1e-9:
                pos += msz
                cost += msz * prev_bid
                fills += 1
            elif p > prev_ask and pos - msz >= -cap - 1e-9:
                if pos > 0:
                    avg = cost / pos if pos else 0.0
                    real += msz * (prev_ask - avg)
                    cost -= msz * avg
                else:
                    cost -= msz * prev_ask
                pos -= msz
                fills += 1
            last_ts = max(last_ts, ts)
    unreal = pos * mid - cost
    st.update({"pos": round(pos, 2), "cost": round(cost, 4), "real": round(real, 4),
               "bid": my_bid, "ask": my_ask, "last_trade_ts": last_ts, "last_sample": now,
               "acc": round(st.get("acc", 0.0) + accrued, 6), "sector": m["sector"],
               "q": m["q"], "pool": m["pool"], "msz": msz, "arm": arm})
    return {"t": round(now), "id": m["id"], "sec": m["sector"], "arm": arm,
            "mid": round(mid, 4), "spr": round(ba - bb, 4), "shr": round(share, 4),
            "acc": round(accrued, 5), "pos": st["pos"], "fills": fills,
            "real": st["real"], "unreal": round(unreal, 4)}


def tick(base):
    if os.path.exists(os.path.join(base, "STOP")):
        print("STOP sentinel present — exiting without action")
        return 0
    size_mb = sum(os.path.getsize(os.path.join(r, f))
                  for r, _, fs in os.walk(base) for f in fs) / 1e6
    if size_mb > MAX_DISK_MB:
        print(f"disk cap exceeded ({size_mb:.0f}MB) — exiting")
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
    quoted = [r for r in results if "gate" not in r]
    gated = defaultdict(int)
    for r in results:
        if "gate" in r:
            gated[r["gate"]] += 1
    print(f"tick ok: {len(quoted)} quoted / {sum(gated.values())} gated "
          f"({dict(gated)}) of {len(universe)}, "
          f"+${sum(r['acc'] for r in quoted):.3f} rewards, "
          f"{sum(r['fills'] for r in quoted)} fills, {_http_calls} http, "
          f"{time.time()-_t0:.0f}s")
    return 0


def report(base):
    state = json.load(open(os.path.join(base, "state.json")))
    by = defaultdict(lambda: [0, 0.0, 0.0])
    arms = defaultdict(lambda: [0, 0.0, 0.0])
    gates = defaultdict(int)
    for st in state.values():
        if not st.get("sector"):
            continue
        by[st["sector"]][0] += 1
        by[st["sector"]][1] += st.get("acc", 0.0)
        by[st["sector"]][2] += st.get("real", 0.0)
        a = st.get("arm")
        if a:
            arms[a][0] += 1
            arms[a][1] += st.get("acc", 0.0)
            arms[a][2] += st.get("real", 0.0)
        for g, n in st.get("gates", {}).items():
            gates[g] += n
    print("%-14s %4s %10s %10s" % ("sector", "n", "rewards$", "realPnL$"))
    for sec, b in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print("%-14s %4d %10.2f %10.2f" % (sec, b[0], b[1], b[2]))
    print("\nwidth arms:")
    for a, b in arms.items():
        print("  %-6s n=%-4d rewards=%.2f realPnL=%.2f" % (a, b[0], b[1], b[2]))
    print("\ngate events:", dict(gates))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/opt/pa2-maker-sim-v2")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.base, exist_ok=True)
    if a.report:
        report(a.base)
    elif a.once:
        sys.exit(tick(a.base))
    else:
        ap.error("pass --once or --report")
