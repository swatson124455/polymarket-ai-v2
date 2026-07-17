#!/usr/bin/env python3
"""Paper Maker sim V6 — NEGRISK LAB arm. PAPER ONLY. 100% separate.

Measures the negRisk multi-outcome netted-quoting thesis (build queue item,
operator-approved 2026-07-17): quoting EVERY outcome of a multi-outcome
event (election / tournament winner / exact-score family) earns N reward
pools while the netted inventory is structurally self-hedged — exactly one
outcome resolves YES, so worst-case event P&L is bounded by
min_w(pos_w) - total_cost, not the sum of standalone exposures.

Cloned from the V5 gate-lab core (2cd88b6 state: V3 WS books incl. 0c9708f
batched price_change fix, shared-tape pairing, review-hardened accounting).
Deltas: negRisk-event discovery/grouping, a TWO-policy paired matrix, and
per-event netted rollups in hb/--report.

Policies (paired on identical events/books/prints; V3 base gates for both):
  N0_all      quote EVERY covered outcome of each event (netted thesis)
  N1_single   quote ONLY the event's largest-pool outcome — the
              single-market baseline the thesis is measured against

Universe: gamma negRisk=true markets grouped by negRiskMarketID; events with
>=3 rewarded outcomes; top MAX_EVENTS by summed pool; <=MAX_PER_EVENT
outcomes each (worst-case accounting treats uncovered outcomes as a
winner-pays-0 branch, so partial coverage stays conservative).

Guard rails identical to V3/V5: no keys, no DATABASE_URL, HTTP GET + WS
subscribe only (cannot trade); STOP sentinel; disk cap + gzip rotation;
HTTP budget/hour (tape fetched ONCE per market, shared by both policies);
bounded book cache; systemd strict sandbox. Own dir/venv/state.

Usage:  maker_paper_sim_v6.py --run [--base /opt/pa2-maker-sim-v6]
        maker_paper_sim_v6.py --report [--base ...]
"""
import argparse
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime

UA = {"User-Agent": "pa2-maker-paper-sim-v6/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
TAPE = "https://data-api.polymarket.com/trades?market={cid}&limit=200"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

MAX_EVENTS = 20
MAX_PER_EVENT = 15
MAX_MARKETS_TOTAL = 150
DISCOVERY_EVERY_S = 1800
DISCOVERY_RETRY_S = 60        # empty-universe retry backoff (review: the 1s
                              # retry loop could burn the whole HTTP budget)
INV_CAP_MULT = 3
LAST_HOURS_GATE_UTC = 19
REQUOTE_TICKS = 0.002
MAX_DISK_MB = 500
HTTP_BUDGET_PER_HOUR = 36000  # v6 worst case: 150 markets x 3 tape pages
                              # x 60 min = 27K/hr + discovery (2x(21 sweep
                              # + <=20 /events)) — ~25% headroom. Exhaustion
                              # is SILENT (get() -> None) so hb reports usage.
WS_CHUNK = 90
WS_IDLE_RECONNECT_S = 40

# ── the policy matrix (V3 base gates both; ONLY the coverage differs) ────────
POLICIES = {
    "N0_all":    {"gated": True, "vol_pts": 0.020, "vol_s": 600, "ramp_h": None, "tapevel": False, "single": False},
    "N1_single": {"gated": True, "vol_pts": 0.020, "vol_s": 600, "ramp_h": None, "tapevel": False, "single": True},
}
TAPEVEL_PRINTS_5M = 8         # prints in 5 min that mark a market "hot"
TAPEVEL_MOVE_5M = 0.03        # or a 3c mid move over 5 min
TAPEVEL_OFF_S = 600

BOOKS = {}
BOOKS_LOCK = threading.Lock()
GEN = {"n": 0}
_http_window = deque()


def http_ok():
    now = time.time()
    while _http_window and now - _http_window[0] > 3600:
        _http_window.popleft()
    return len(_http_window) < HTTP_BUDGET_PER_HOUR


def get(url, timeout=10):
    if not http_ok():
        return None
    _http_window.append(time.time())
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def ts_of(tr):
    """Print timestamp as float, 0.0 on any malformed shape (review: an
    unguarded float() here crashed the whole daemon on one bad print)."""
    try:
        return float(tr.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_tape(cid, since_ts):
    """All tape prints since since_ts, OLDEST-FIRST (v3 96df6d2 semantics)."""
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
        t = str(s).strip().replace("Z", "+00:00")
        if re.search(r"[+-]\d{2}$", t):
            t += ":00"
        return datetime.fromisoformat(t).timestamp()
    except Exception:
        return None


def S(v, s, size):
    return ((v - s) / v) ** 2 * size if v > 0 and 0 <= s < v else 0.0


def event_worst(poss, cost, covered, nout):
    """Guaranteed one-winner floor for an event position set: min over
    possible winners w of (pos_w - total_cost). Whenever coverage is
    partial (covered < nout) an uncovered outcome can win and every covered
    position pays 0 — the 0-branch keeps the floor conservative."""
    minw = min(poss) if poss else 0.0
    if covered < nout:
        minw = min(minw, 0.0)
    return minw - cost


def netted_rollup(state, uinfo):
    """Per-(event, policy) netted metrics from a state dict + live-universe
    info ({market_id: row}). DEPARTED markets with inventory stay in the
    floor via their state-persisted ev — silently dropping a departed short
    overstated safety on the exact metric the arm exists to produce (review
    finding 1; extra min-branches and real cost are always conservative).
    Returns [{ev, pol, cov, nout, dep, acc, net, worst, cap}]."""
    ev_rows = defaultdict(lambda: defaultdict(list))
    for k, st in state.items():
        if "|" not in k or k.endswith("|SH") or not isinstance(st, dict):
            continue
        mkt, pol = k.rsplit("|", 1)
        if pol not in POLICIES:
            continue
        mi = uinfo.get(mkt)
        ev = (mi.get("ev") if mi else None) or st.get("ev")
        if not ev:
            continue
        ev_rows[ev][pol].append((mkt, st, mi))
    out = []
    for ev, pols in ev_rows.items():
        for pol, entries in pols.items():
            live = [(mk, st) for mk, st, mi in entries if mi is not None]
            dep = [(mk, st) for mk, st, mi in entries if mi is None
                   and (st.get("pos") or st.get("cost"))]
            poss = [st.get("pos", 0.0) for _, st in live + dep]
            cost = sum(st.get("cost", 0.0) for _, st in live + dep)
            acc = sum(st.get("acc", 0.0) for _, st in live + dep)
            nout = max([mi["nout_total"] for _, _, mi in entries
                        if mi is not None and "nout_total" in mi] or
                       [st.get("nout") or len(live) for _, st, _ in entries])
            worst = event_worst(poss, cost, len(live), nout)
            net = 0.0
            for mk, st in live + dep:
                shm = state.get(mk + "|SH") or {}
                lm = shm.get("last_mid")
                net += st.get("real", 0.0) + \
                    ((st.get("pos", 0.0) * lm - st.get("cost", 0.0))
                     if lm is not None else 0.0)
            # cov = outcomes actually quoted or held (a state entry exists
            # for EVERY market x policy — counting those printed N1's
            # coverage as if it were N0's) (review finding 5)
            cov = sum(1 for _, st in live
                      if st.get("bid") is not None or st.get("pos"))
            out.append({"ev": ev, "pol": pol, "cov": cov, "nout": nout,
                        "dep": len(dep), "acc": acc, "net": net,
                        "worst": worst, "cap": max(0.0, -worst)})
    return out


def _row_of(m):
    """Quotable universe row from a gamma market object (sweep or /events
    embedded shape — same schema). None if unrewarded/unquotable."""
    if not m.get("negRisk"):
        return None
    ev = str(m.get("negRiskMarketID") or "").lower()
    if not ev:
        return None
    pool = 0.0
    for r in (m.get("clobRewards") or []):
        try:
            pool += float(r.get("rewardsDailyRate") or 0)
        except Exception:
            pass
    if pool <= 0:
        return None
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
        v = float(m.get("rewardsMaxSpread")) / 100.0
        msz = float(m.get("rewardsMinSize"))
    except Exception:
        return None
    if len(toks) < 2 or v <= 0 or msz <= 0:
        return None
    return {"id": m.get("id"), "cid": m.get("conditionId"),
            "q": (m.get("question") or "")[:70], "sector": sector_of(m),
            "ev": ev,
            "eid": str((m.get("events") or [{}])[0].get("id") or ""),
            "yes": str(toks[0]), "no": str(toks[1]), "v": v, "msz": msz,
            "pool": pool, "end": (m.get("endDate") or "")[:10],
            "end_ts": parse_iso(m.get("endDate")),
            "game_start": parse_iso(m.get("gameStartTime"))}


def discover(base):
    """negRisk markets grouped into multi-outcome events by negRiskMarketID.
    Event selection needs >=3 rewarded outcomes visible in the volume sweep;
    each selected event then gets one /events fetch for (a) the TRUE outcome
    count and (b) rewarded siblings past the pagination wall."""
    rows, seen = [], set()
    nout_total = defaultdict(int)
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
            if m.get("negRisk") and m.get("negRiskMarketID"):
                nout_total[str(m.get("negRiskMarketID")).lower()] += 1
            r = _row_of(m)
            if r:
                rows.append(r)
        if new == 0 or len(data) < 100:
            break
    by_ev = defaultdict(list)
    for r in rows:
        by_ev[r["ev"]].append(r)
    events = []
    for ev, ms in by_ev.items():
        if len(ms) < 3:            # multi-outcome means >=3 rewarded outcomes
            continue
        ms.sort(key=lambda x: -x["pool"])
        events.append((sum(x["pool"] for x in ms), ev, ms))
    events.sort(key=lambda e: -e[0])
    # /events per selected event: the vol-ordered sweep misses siblings past
    # the ~2,100-offset pagination wall. Two uses: (1) TRUE nout_total — an
    # undercount would falsely claim full coverage and OVERSTATE the netted
    # floor (verified live: WC-winner event = 60 negRisk outcomes, sweep saw
    # a handful); (2) harvest rewarded siblings the sweep never saw, so
    # coverage is as complete as the rewards program allows. On fetch
    # failure the sweep view stands (floors keep the undercount caveat).
    for _, ev, ms in events[:MAX_EVENTS]:
        eid = ms[0].get("eid")
        if not eid:
            continue
        data = get("https://gamma-api.polymarket.com/events?" +
                   urllib.parse.urlencode({"id": eid}), timeout=15)
        try:
            # ONE predicate for count AND harvest: same family (ev guard),
            # still winnable (not closed, active). Counting closed siblings
            # (eliminated teams, dropped candidates) or a bundled second
            # family would inflate nout, pin the 0-branch on permanently,
            # and structurally hide the N0 self-hedge the arm exists to
            # measure (review finding 2).
            emkts = [x for x in (data[0].get("markets") or [])
                     if x.get("negRisk")
                     and str(x.get("negRiskMarketID") or "").lower() == ev
                     and not x.get("closed") and x.get("active") is not False]
        except Exception:
            continue
        if len(emkts) > nout_total[ev]:
            nout_total[ev] = len(emkts)
        have = set(r["id"] for r in ms)
        for x in emkts:
            if x.get("id") in have:
                continue
            r = _row_of(x)
            if r and r["ev"] == ev:
                ms.append(r)
                have.add(r["id"])              # dup ids inside one response
        ms.sort(key=lambda x: -x["pool"])
    # re-rank by POST-harvest pool (the wall hid pool mass from the first
    # ranking), then fill the market budget without a hard break
    events = [(sum(x["pool"] for x in ms), ev, ms)
              for _, ev, ms in events[:MAX_EVENTS]]
    events.sort(key=lambda e: -e[0])
    picked, picked_ids = [], set()
    for _, ev, ms in events:
        ms = [r for r in ms[:MAX_PER_EVENT] if r["id"] not in picked_ids]
        if not ms or len(picked) + len(ms) > MAX_MARKETS_TOTAL:
            continue
        for i, r in enumerate(ms):
            r["flag"] = (i == 0)               # largest pool = N1's baseline
            r["nout_total"] = nout_total[ev]   # winnable negRisk siblings
            picked_ids.add(r["id"])
        picked.extend(ms)
    with open(os.path.join(base, "universe.json"), "w") as f:
        json.dump({"t": time.time(), "markets": picked}, f)
    return picked


# ── WebSocket book maintenance (v3 verbatim, incl. 0c9708f batched fix) ─────
def _apply_book_snapshot(asset, msg):
    bids, asks = {}, {}
    for key_b, key_a in (("bids", "asks"), ("buys", "sells")):
        for lv in msg.get(key_b) or []:
            try:
                p, s = float(lv["price"]), float(lv["size"])
                if 0 < p < 1 and s > 0:
                    bids[p] = s
            except Exception:
                continue
        for lv in msg.get(key_a) or []:
            try:
                p, s = float(lv["price"]), float(lv["size"])
                if 0 < p < 1 and s > 0:
                    asks[p] = s
            except Exception:
                continue
    if bids or asks:
        with BOOKS_LOCK:
            BOOKS[asset] = {"bids": bids, "asks": asks, "ts": time.time()}


def _apply_price_change(asset, msg):
    with BOOKS_LOCK:
        book = BOOKS.get(asset)
        if not book:
            return
        for ch in msg.get("changes") or []:
            try:
                p, s = float(ch["price"]), float(ch["size"])
                side = str(ch.get("side", "")).upper()
            except Exception:
                continue
            levels = book["bids"] if side == "BUY" else book["asks"]
            if s <= 0:
                levels.pop(p, None)
            elif 0 < p < 1:
                levels[p] = s
        book["ts"] = time.time()


def _apply_price_change_batched(msg):
    with BOOKS_LOCK:
        for ch in msg.get("price_changes") or []:
            if not isinstance(ch, dict):
                continue
            book = BOOKS.get(str(ch.get("asset_id") or ""))
            if not book:
                continue
            try:
                p, s = float(ch["price"]), float(ch["size"])
                side = str(ch.get("side", "")).upper()
            except Exception:
                continue
            levels = book["bids"] if side == "BUY" else book["asks"]
            if s <= 0:
                levels.pop(p, None)
            elif 0 < p < 1:
                levels[p] = s
            book["ts"] = time.time()


def ws_worker(assets, gen):
    from websockets.sync.client import connect
    while GEN["n"] == gen:
        try:
            with connect(WS_URL, open_timeout=15, close_timeout=5) as ws:
                ws.send(json.dumps({"assets_ids": assets, "type": "market"}))
                while GEN["n"] == gen:
                    try:
                        raw = ws.recv(timeout=WS_IDLE_RECONNECT_S)
                    except TimeoutError:
                        break
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue
                    for msg in data if isinstance(data, list) else [data]:
                        if not isinstance(msg, dict):
                            continue
                        et = msg.get("event_type") or msg.get("type")
                        asset = str(msg.get("asset_id") or "")
                        if et == "book":
                            if asset:
                                _apply_book_snapshot(asset, msg)
                        elif et == "price_change":
                            if asset:
                                _apply_price_change(asset, msg)
                            else:
                                _apply_price_change_batched(msg)
        except Exception:
            time.sleep(3)


def cached_touch(asset):
    with BOOKS_LOCK:
        book = BOOKS.get(asset)
        if not book:
            return None, None, None
        bb = max(book["bids"]) if book["bids"] else None
        ba = min(book["asks"]) if book["asks"] else None
        return bb, ba, book["ts"]


def cached_scores(m, mid):
    v = m["v"]
    with BOOKS_LOCK:
        y = BOOKS.get(m["yes"], {"bids": {}, "asks": {}})
        n = BOOKS.get(m["no"], {"bids": {}, "asks": {}})
        def sc(levels, center):
            return sum(S(v, abs(p - center), s) for p, s in levels.items())
        q1 = sc(y["bids"], mid) + sc(n["asks"], 1 - mid)
        q2 = sc(y["asks"], mid) + sc(n["bids"], 1 - mid)
    return q1, q2


def gate(m, sh, st, pol, now, mid):
    """Policy-aware gate. sh = shared market state, st = this policy's state."""
    if not pol["gated"]:
        return None
    if m["sector"] in ("esports", "sports") and m.get("game_start") and now >= m["game_start"]:
        return "in_play"
    if m["sector"] == "weather" and mid is not None and not (0.10 <= mid <= 0.90):
        return "extreme_wx"
    if pol["ramp_h"] is not None:
        if m.get("end_ts") and 0 <= m["end_ts"] - now <= pol["ramp_h"] * 3600:
            return "winddown"
    else:
        if m.get("end") == time.strftime("%Y-%m-%d", time.gmtime(now)) \
                and time.gmtime(now).tm_hour >= LAST_HOURS_GATE_UTC:
            return "last_hours"
    if pol["tapevel"] and now < sh.get("hot_until", 0):
        return "tapevel"
    if now < st.get("pull_until", 0):
        return "vol_pull"
    return None


def match_window(prints, qh, st, msz, yes_asset, prev_ts):
    """Match a window of tape prints against this policy's quote history.
    Mutates st (pos/cost/real/cap_msz), returns (fills, max_ts_seen).
    Extracted from v3's inline loop so it is unit-testable."""
    pos, cost, real = st.get("pos", 0.0), st.get("cost", 0.0), st.get("real", 0.0)
    cap_msz = st.get("cap_msz")
    if not cap_msz or pos == 0:
        cap_msz = st["cap_msz"] = msz
    cap = INV_CAP_MULT * cap_msz
    fills = 0
    max_ts = prev_ts
    for tr in prints if isinstance(prints, list) else []:
        try:
            ts = float(tr.get("timestamp"))
            p = float(tr.get("price"))
            asset = str(tr.get("asset") or "")
        except Exception:
            continue
        if ts <= prev_ts or asset != yes_asset:
            continue
        max_ts = max(max_ts, ts)
        qbid = qask = None
        for qt, qb, qa in reversed(qh):
            if qt <= ts:
                qbid, qask = qb, qa
                break
        if qbid is None:
            continue
        if p < qbid and pos + msz <= cap + 1e-9:
            pos += msz
            cost += msz * qbid
            fills += 1
        elif qask is not None and p > qask and pos - msz >= -cap - 1e-9:
            if pos > 0:
                avg = cost / pos if pos else 0.0
                real += msz * (qask - avg)
                cost -= msz * avg
            else:
                cost -= msz * qask
            pos -= msz
            fills += 1
    st.update({"pos": round(pos, 2), "cost": round(cost, 4), "real": round(real, 4)})
    return fills, max_ts


def run(base):
    universe = []
    state_path = os.path.join(base, "state.json")
    state = {}
    if os.path.exists(state_path):
        try:
            state = json.load(open(state_path))
        except Exception as e:
            print(f"STATE LOAD FAILED ({type(e).__name__}) — trying .bak", flush=True)
            try:
                state = json.load(open(state_path + ".bak"))
                print("recovered state from .bak", flush=True)
            except Exception:
                print("STATE .bak ALSO FAILED — starting with EMPTY ledgers "
                      "(all acc/pos/real reset; era note this restart!)", flush=True)
    else:
        print("no state.json — first boot", flush=True)
    # restart normalization: never credit accrual across a downtime gap
    # (pull_until is wall-clock and simply expires on its own)
    _t0 = time.time()
    for _v in state.values():
        if isinstance(_v, dict) and "last_acc_t" in _v:
            _v["last_acc_t"] = _t0
    last_discovery = 0.0
    last_minute = 0.0
    last_disk = 0.0
    minute_n = 0
    lat_ms = deque(maxlen=2000)
    hb = time.time()

    def sh_of(mid_key):
        return state.setdefault(mid_key + "|SH", {})

    def st_of(mid_key, pol):
        return state.setdefault(mid_key + "|" + pol, {})

    while True:
        now = time.time()
        if os.path.exists(os.path.join(base, "STOP")):
            print("STOP sentinel — exiting cleanly", flush=True)
            return 0
        if now - last_disk > 3600:
            last_disk = now
            # gzip-rotate previous days' samples
            today = time.strftime("%Y%m%d", time.gmtime(now))
            for old in [p for p in os.listdir(base)
                        if p.startswith("samples-") and p.endswith(".jsonl")
                        and not p.endswith(f"samples-{today}.jsonl")]:
                op = os.path.join(base, old)
                try:
                    with open(op, "rb") as i, gzip.open(op + ".gz", "wb") as o:
                        o.write(i.read())
                    os.remove(op)
                except Exception:
                    pass
            size_mb = sum(os.path.getsize(os.path.join(r, f))
                          for r, _, fs in os.walk(base) if "venv" not in r
                          for f in fs) / 1e6
            if size_mb > MAX_DISK_MB:
                # exit 0: Restart=on-failure must NOT flap us back up — each
                # flap would corrupt accrual/fills (review finding)
                print(f"disk cap exceeded ({size_mb:.0f}MB) — CLEAN STOP", flush=True)
                return 0

        # ── universe (re)discovery + WS lifecycle ───────────────────────────
        if now - last_discovery > (DISCOVERY_EVERY_S if universe else DISCOVERY_RETRY_S):
            u = discover(base)
            # reject a PARTIAL discovery (mid-pagination failure) — replacing
            # 140 markets with a fragment would silently drop live experiments
            # floor 9 = 3 events x 3 outcomes (v5's 40 assumed a 140-market
            # universe; v6's event universe is legitimately smaller)
            if u and universe and len(u) < max(9, len(universe) // 2):
                print(f"discovery PARTIAL ({len(u)} vs {len(universe)}) — keeping "
                      f"current universe", flush=True)
                u = None
            if u:
                universe = u
                GEN["n"] += 1
                gen = GEN["n"]
                assets = []
                for m in universe:
                    assets.extend((m["yes"], m["no"]))
                keep = set(assets)
                with BOOKS_LOCK:
                    for dead in [a for a in BOOKS if a not in keep]:
                        BOOKS.pop(dead, None)
                threads = []
                for i in range(0, len(assets), WS_CHUNK):
                    t = threading.Thread(target=ws_worker,
                                         args=(assets[i:i + WS_CHUNK], gen), daemon=True)
                    t.start()
                    threads.append(t)
                print(f"universe: {len(universe)} markets, {len(assets)} assets, "
                      f"{len(threads)} ws conns (gen {gen})", flush=True)
            last_discovery = now

        # ── fast loop: shared signals + per-policy gates/quotes ─────────────
        for m in universe:
            key = str(m["id"])
            sh = sh_of(key)
            bb, ba, bts = cached_touch(m["yes"])
            if bb is None or ba is None or not (0 < bb < ba <= 1):
                continue
            mid = (bb + ba) / 2
            mh = sh.setdefault("mid_hist", [])
            mh.append([now, mid])
            while mh and now - mh[0][0] > 330:
                mh.pop(0)
            sh["last_mid"] = mid
            sh["mid_t"] = now       # freshness stamp: accrual requires a mid
                                    # recently refreshed from LIVE books
            # shared tape-velocity signal (price leg): 3c move over <=5.5min
            if abs(mid - mh[0][1]) >= TAPEVEL_MOVE_5M and now - mh[0][0] >= 60:
                sh["hot_until"] = now + TAPEVEL_OFF_S
            # per-policy vol triggers use the ~120s window point (v3 semantics)
            vol_ref = None
            for t0, m0 in mh:
                if now - t0 <= 150:
                    vol_ref = (t0, m0)
                    break
            try:
                arm = "wide" if int(key[-1]) % 2 else "touch"
            except Exception:
                arm = "touch"
            s_touch = (ba - bb) / 2
            for pol_name, pol in POLICIES.items():
                st = st_of(key, pol_name)
                # vol trigger BEFORE the single-skip: N1's pull state must
                # stay warm on every market it might inherit as flagship at
                # rotation, or a freshly promoted flagship would quote
                # through volatility N0 is pulled from — a pro-thesis
                # pairing leak (review finding 3)
                if pol["vol_pts"] is not None and vol_ref is not None:
                    t0, m0 = vol_ref
                    if now - t0 >= 60 and abs(mid - m0) > pol["vol_pts"]:
                        st["pull_until"] = now + pol["vol_s"]
                if pol.get("single") and not m.get("flag"):
                    # N1 baseline covers only the event's flagship outcome; a
                    # standing quote on a DEMOTED flagship must be pulled or
                    # it would keep filling forever (flagship can rotate at
                    # rediscovery)
                    if st.get("bid") is not None:
                        st["bid"] = st["ask"] = None
                        st.setdefault("qh", []).append([now, None, None])
                    continue
                g = gate(m, sh, st, pol, now, mid)
                if g is not None:
                    if st.get("bid") is not None:
                        st["bid"] = st["ask"] = None
                        st.setdefault("qh", []).append([now, None, None])
                    st["gates"] = {**st.get("gates", {}),
                                   g: st.get("gates", {}).get(g, 0) + 1}
                    continue
                s_mine = max(s_touch, m["v"] / 2) if arm == "wide" else s_touch
                if S(m["v"], s_mine, m["msz"]) <= 0:
                    continue
                want_bid, want_ask = mid - s_mine, mid + s_mine
                cur = st.get("bid")
                if cur is None or abs(want_bid - cur) >= REQUOTE_TICKS or not st.get("qh"):
                    st["bid"], st["ask"], st["arm"] = want_bid, want_ask, arm
                    qh = st.setdefault("qh", [])
                    qh.append([now, want_bid, want_ask])
                    if len(qh) > 400:
                        del qh[:len(qh) - 400]
                    if bts and pol_name == "N0_all":
                        lat_ms.append(max(0.0, (now - bts) * 1000))

        # ── minute loop: shared tape, per-policy accrual + fills, samples ───
        if now - last_minute >= 60:
            last_minute = now
            minute_n += 1
            from concurrent.futures import ThreadPoolExecutor
            tape_cache = {}
            need = [m for m in universe if m.get("cid")]
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(fetch_tape, m["cid"],
                                  (state.get(str(m["id"]) + "|SH") or {}).get(
                                      "last_trade_ts", now - 60)): str(m["id"])
                        for m in need}
                for fu in futs:
                    try:
                        tape_cache[futs[fu]] = fu.result(timeout=45) or []
                    except Exception:
                        tape_cache[futs[fu]] = []
            rows = []
            for m in universe:
                key = str(m["id"])
                sh = sh_of(key)
                mid = sh.get("last_mid")
                if mid is None:
                    continue
                tape = tape_cache.get(key) or []
                prev_ts = sh.get("last_trade_ts", now - 60)
                # shared tape-velocity signal (print-count leg)
                ph = sh.setdefault("prints_hist", [])
                n_new = sum(1 for tr in tape
                            if isinstance(tr, dict) and ts_of(tr) > prev_ts)
                ph.append([now, n_new])
                while ph and now - ph[0][0] > 300:
                    ph.pop(0)
                if sum(c for _, c in ph) >= TAPEVEL_PRINTS_5M:
                    sh["hot_until"] = now + TAPEVEL_OFF_S
                max_ts_all = prev_ts
                # competition scores hoisted OUT of the policy loop: reading
                # live BOOKS per policy let a WS update land between policies
                # and break the identical-inputs pairing guarantee (review)
                q1, q2 = cached_scores(m, mid)
                q_comp = max(min(q1, q2), max(q1, q2) / 3.0) \
                    if 0.10 <= mid <= 0.90 else min(q1, q2)
                # freshness guard, NOT score-emptiness: q1+q2==0 with a live
                # book is the LEGIT sole-in-band-quoter (farm) case earning
                # real 100% share (masking check 07-17: the score-based guard
                # wrongly zeroed ~3% of quoting rows, mostly politics). Only
                # a STALE mid (restart before books repopulate) may not accrue.
                books_live = (now - sh.get("mid_t", 0)) < 180
                for pol_name, pol in POLICIES.items():
                    st = st_of(key, pol_name)
                    # dt/last_acc_t advance EVERY minute regardless of gating:
                    # advancing only-while-quoting credited up to 120s of
                    # accrual at gate-exit — a bias correlated with gating,
                    # the exact variable under test (review)
                    dt = min(now - st.get("last_acc_t", now - 60), 120)
                    st["last_acc_t"] = now
                    if st.get("bid") is not None:
                        q_mine = S(m["v"], abs(mid - st["bid"]), m["msz"])
                        share = q_mine / (q_mine + q_comp) \
                            if (q_mine > 0 and books_live) else 0.0
                        if share > 0:
                            st["acc"] = round(st.get("acc", 0.0)
                                              + share * m["pool"] * dt / 86400.0, 6)
                    else:
                        share = 0.0
                    fills = 0
                    if st.get("qh"):
                        fills, mts = match_window(tape, st["qh"], st, m["msz"],
                                                  m["yes"], prev_ts)
                        max_ts_all = max(max_ts_all, mts)
                    st.update({"sector": m["sector"], "q": m["q"],
                               "pool": m["pool"], "msz": m["msz"],
                               "ev": m["ev"], "nout": m.get("nout_total")})
                    # per-policy sample every 5 min, plus immediately on fills
                    if fills or minute_n % 5 == 0:
                        rows.append({"t": round(now), "id": m["id"], "pol": pol_name,
                                     "sec": m["sector"], "mid": round(mid, 4),
                                     "shr": round(share, 4),
                                     "pos": st.get("pos", 0.0), "fills": fills,
                                     "real": st.get("real", 0.0),
                                     "unreal": round(st.get("pos", 0.0) * mid
                                                     - st.get("cost", 0.0), 4),
                                     "quoting": st.get("bid") is not None})
                # advance the SHARED watermark once, after every policy saw
                # the same window (per-policy watermarks would skew the A/B)
                for tr in tape if isinstance(tape, list) else []:
                    try:
                        max_ts_all = max(max_ts_all, float(tr.get("timestamp")))
                    except Exception:
                        continue
                sh["last_trade_ts"] = max_ts_all
            day = time.strftime("%Y%m%d", time.gmtime(now))
            with open(os.path.join(base, f"samples-{day}.jsonl"), "a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            tmp = state_path + ".tmp"
            slim = {k: {kk: vv for kk, vv in v.items()
                        if kk not in ("qh", "mid_hist", "prints_hist")}
                    for k, v in state.items()}
            with open(tmp, "w") as f:
                json.dump(slim, f)
            # keep a one-minute-old good copy: a torn state.json (power loss,
            # SIGKILL mid-replace) silently reset ALL ledgers before (review)
            if os.path.exists(state_path):
                try:
                    os.replace(state_path, state_path + ".bak")
                except OSError:
                    pass
            os.replace(tmp, state_path)
            if now - hb > 300:
                hb = now
                lm = sorted(lat_ms)
                med = lm[len(lm) // 2] if lm else -1
                with BOOKS_LOCK:
                    stale = sum(1 for b in BOOKS.values() if now - b["ts"] > 300)
                parts = []
                for pol_name in POLICIES:
                    acc = net = fl = 0.0
                    quoting = 0
                    for k, st in state.items():
                        if not k.endswith("|" + pol_name):
                            continue
                        acc += st.get("acc", 0.0)
                        shm = state.get(k.rsplit("|", 1)[0] + "|SH") or {}
                        lmid = shm.get("last_mid")
                        if lmid is not None:
                            net += st.get("real", 0.0) + st.get("pos", 0.0) * lmid \
                                   - st.get("cost", 0.0)
                        else:
                            net += st.get("real", 0.0)
                        quoting += 1 if st.get("bid") is not None else 0
                    parts.append("%s[q=%d acc=$%.2f tot=$%.2f]"
                                 % (pol_name.split("_")[0], quoting, acc, acc + net))
                # nobook: universe assets with NO book at all — a dead WS
                # chunk was invisible to stale_books (which only counts
                # books that once arrived) (review)
                with BOOKS_LOCK:
                    nobook = sum(1 for m2 in universe
                                 for a in (m2["yes"], m2["no"]) if a not in BOOKS)
                n_ev = len(set(m2.get("ev") for m2 in universe))
                print("hb: " + " ".join(parts)
                      + f" | events={n_ev} requote_lat_med={med:.0f}ms books={len(BOOKS)}"
                      + f" stale_books={stale} nobook={nobook}"
                      + f" http_hr={len(_http_window)}/{HTTP_BUDGET_PER_HOUR}",
                      flush=True)

        time.sleep(1)


def report(base):
    try:
        state = json.load(open(os.path.join(base, "state.json")))
    except OSError:
        print("no state.json yet — nothing to report")
        return
    # departed markets keep frozen marks forever — split them out so the
    # headline compares policies on LIVE experiments only (review)
    try:
        current = set(str(m["id"]) for m in
                      json.load(open(os.path.join(base, "universe.json")))["markets"])
    except Exception:
        current = None
    dep_net = defaultdict(float)
    dep_n = set()
    by = defaultdict(lambda: [0, 0.0, 0.0, 0.0])   # (pol) -> n, acc, real, unreal
    bysec = defaultdict(lambda: [0.0, 0.0])        # (pol, sec) -> acc, pnl
    for k, st in state.items():
        if "|" not in k or k.endswith("|SH"):
            continue
        mkt, pol = k.rsplit("|", 1)
        if current is not None and mkt not in current:
            sh = state.get(mkt + "|SH") or {}
            lm = sh.get("last_mid")
            dep_net[pol] += st.get("acc", 0.0) + st.get("real", 0.0) + \
                ((st.get("pos", 0.0) * lm - st.get("cost", 0.0)) if lm is not None else 0.0)
            dep_n.add(mkt)
            continue
        sh = state.get(mkt + "|SH") or {}
        lmid = sh.get("last_mid")
        unreal = (st.get("pos", 0.0) * lmid - st.get("cost", 0.0)) \
            if lmid is not None else 0.0
        by[pol][0] += 1
        by[pol][1] += st.get("acc", 0.0)
        by[pol][2] += st.get("real", 0.0)
        by[pol][3] += unreal
        sec = st.get("sector") or "?"
        bysec[(pol, sec)][0] += st.get("acc", 0.0)
        bysec[(pol, sec)][1] += st.get("real", 0.0) + unreal
    print("%-12s %5s %10s %10s %10s %10s" % ("policy", "n", "rewards$",
                                             "real$", "unreal$", "NET$"))
    for pol in POLICIES:
        n, a, r, u = by.get(pol, [0, 0, 0, 0])
        print("%-12s %5d %10.2f %10.2f %10.2f %10.2f" % (pol, n, a, r, u, a + r + u))
    if dep_n:
        print("departed markets (frozen marks, EXCLUDED above): n=%d, NET by policy: %s"
              % (len(dep_n), {p: round(v, 2) for p, v in dep_net.items()}))
    print("\nper policy x sector NET$:")
    secs = sorted(set(s for _, s in bysec))
    print("%-12s" % "policy" + "".join("%12s" % s[:11] for s in secs))
    for pol in POLICIES:
        print("%-12s" % pol + "".join(
            "%12.2f" % (bysec.get((pol, s), [0, 0])[0] + bysec.get((pol, s), [0, 0])[1])
            for s in secs))
    # ── per-event NETTED rollup — the v6 thesis metric ──────────────────────
    # worst$ = guaranteed one-winner floor (see event_worst / netted_rollup);
    # cap$ = max(0, -worst$) = worst-case capital consumed. The thesis is
    # N0 rew$ per cap$ >> N1 rew$ per cap$. dep = departed markets whose
    # inventory is still counted in the floor.
    try:
        umkts = json.load(open(os.path.join(base, "universe.json")))["markets"]
    except Exception:
        umkts = []
    uinfo = {str(m["id"]): m for m in umkts}
    roll = netted_rollup(state, uinfo)
    order = defaultdict(float)
    for r in roll:
        order[r["ev"]] += r["acc"]
    print("\nper-event netted rollup (incl. departed inventory):")
    print("%-14s %-10s %8s %4s %9s %9s %9s %9s" % (
        "event", "policy", "cov/tot", "dep", "rew$", "net$", "worst$", "cap$"))
    for r in sorted(roll, key=lambda r: (-order[r["ev"]], r["ev"], r["pol"])):
        print("%-14s %-10s %4d/%-3d %4d %9.2f %9.2f %9.2f %9.2f" % (
            r["ev"][:14], r["pol"], r["cov"], r["nout"], r["dep"],
            r["acc"], r["net"], r["worst"], r["cap"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--base", default="/opt/pa2-maker-sim-v6")
    a = ap.parse_args()
    if a.report:
        report(a.base)
    elif a.run:
        sys.exit(run(a.base))
    else:
        print("need --run or --report")
