#!/usr/bin/env python3
"""Paper Maker sim V4 — LIVE-SPORTS LANE arm. PAPER ONLY. 100% separate.

Tests the two Bucket-1 hypotheses from the 2026-07-15 3rd-party review
(docs/MAKER_V4_LANE_TEST_PLAN.md is the binding spec):

  H1  live-sports lane: quote SPORTS game markets ONLY while in play, with
      hard +/-1x inventory caps and ~1s WS re-centering (v2/v3 own pre-game;
      zero overlap => clean attribution).
  H2  split-inventory (ask-ask) quoting vs classic bid/ask-around-mid,
      A/B by market-id parity on the same universe.

Deltas vs V3 (everything else inherited verbatim from 55b089c):
  D1 universe = rewarded sports markets WITH gameStartTime, top-70 by pool,
     discovery every 15 min
  D2 INVERTED gate: pre_game gated, in-play quoted; NO last_hours gate;
     vol_pull kept
  D3 INV_CAP_MULT = 1 (frozen msz)
  D4 A/B: even id -> classic (v3-touch), odd id -> split (pre-split pairs,
     ask both tokens; fills REDUCE inventory; re-split <=3/day)
  D5 complement-side fills counted: NO-token prints mapped to YES space
     (p_yes = 1 - p_no) and matched against the same quotes; cross-view
     dedup on (txhash, ts, size)
  D6 one-sided score haircut for the split arm (x1/3 in mid-band, 0 outside)
  D7 "settled" pull gate: mid >= 0.92 or <= 0.08 -> quotes pulled (finished-
     but-unresolved games are the v1-esports bleed channel)

Guard rails identical to V3: no keys, no DATABASE_URL, HTTP GET + WS
subscribe only (cannot trade); STOP sentinel; disk cap; HTTP budget/hour;
bounded book cache; systemd strict sandbox + Restart=on-failure. Own
dir/venv/state — shares NOTHING with v1/v2/v3.

Usage:  maker_paper_sim_v4.py --run [--base /opt/pa2-maker-sim-v4]
        maker_paper_sim_v4.py --report [--base ...]
"""
import argparse
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

UA = {"User-Agent": "pa2-maker-paper-sim-v4/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
TAPE = "https://data-api.polymarket.com/trades?market={cid}&limit=200"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

MAX_MARKETS_TOTAL = 70        # sports-only universe (140 assets = 2 WS chunks)
DISCOVERY_EVERY_S = 900       # game markets churn daily; 15 min (v3: 30)
INV_CAP_MULT = 1              # D3 hard caps (v1-v3: 3)
RESPLIT_MAX_PER_DAY = 3       # D4 split-arm capital bound
SETTLED_HI, SETTLED_LO = 0.92, 0.08   # D7
VOL_PULL_PTS = 0.02
VOL_PULL_S = 600
REQUOTE_TICKS = 0.002
MAX_DISK_MB = 500
HTTP_BUDGET_PER_HOUR = 12000
WS_CHUNK = 90
WS_IDLE_RECONNECT_S = 40

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


SPORTS_KW = re.compile(
    r"nba|nfl|mlb|nhl|ncaa|premier|epl|serie-a|la-liga|bundesliga|ligue|ufc|"
    r"atp|wta|pga|f1-|grand-prix|world-cup|fifa|uefa|copa|boxing|tennis|-vs-|"
    r"derby|open-")
ESPORTS_KW = re.compile(
    r"lol-|league-of-legends|cs2|csgo|counter-strike|dota|valorant|esports|"
    r"lck|lpl|lec|ewc")


def is_sports(m):
    """Sports (NOT esports) — esports is explicitly out of this lane."""
    c = (m.get("category") or "").strip().lower()
    text = ((m.get("slug") or "") + " " + (m.get("question") or "")).lower()
    if ESPORTS_KW.search(text) or c == "esports":
        return False
    return c == "sports" or bool(SPORTS_KW.search(text))


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def S(v, s, size):
    return ((v - s) / v) ** 2 * size if v > 0 and 0 <= s < v else 0.0


def arm_of(mid_str):
    """D4: even trailing digit -> classic, odd -> split (v2/v3 parity pattern)."""
    try:
        return "split" if int(str(mid_str)[-1]) % 2 else "classic"
    except Exception:
        return "classic"


def gate(m, st, now, mid):
    """D2/D7: pre_game gated (inverse of v3), settled pulled, vol_pull kept.
    Deliberately NO last_hours gate (night games ARE last hours)."""
    if m.get("game_start") and now < m["game_start"]:
        return "pre_game"
    if mid is not None and (mid >= SETTLED_HI or mid <= SETTLED_LO):
        return "settled"
    if now < st.get("pull_until", 0):
        return "vol_pull"
    return None


def _quote_changed(cur, want):
    if (cur is None) != (want is None):
        return True
    if cur is None:
        return False
    return abs(want - cur) >= REQUOTE_TICKS


def my_qmine(st, mid, v, msz):
    """Own reward score with the D6 one-sided haircut.
    Returns (q_mine, n_sides)."""
    n_sides = (st.get("bid") is not None) + (st.get("ask") is not None)
    if not n_sides:
        return 0.0, 0
    dist = abs(mid - st["bid"]) if st.get("bid") is not None else abs(st["ask"] - mid)
    q = S(v, dist, msz)
    if n_sides == 1:
        q = q / 3.0 if 0.10 <= mid <= 0.90 else 0.0
    return q, n_sides


def split_init(st, msz, now):
    """D4: one-time initial split — msz pairs at $1 each, fee-free."""
    if "capital" in st:
        return
    st["capital"] = msz * 1.0
    st["cash"] = 0.0
    st["yes_inv"] = msz
    st["no_inv"] = msz
    st["resplits"] = 0
    st["rs_day"] = time.strftime("%Y%m%d", time.gmtime(now))


def maybe_resplit(st, msz, now):
    """D4: when BOTH sides are exhausted, split again — bounded per UTC day."""
    if "capital" not in st:
        return False
    day = time.strftime("%Y%m%d", time.gmtime(now))
    if st.get("rs_day") != day:
        st["rs_day"], st["resplits"] = day, 0
    if (st.get("yes_inv", 0) < msz - 1e-9 and st.get("no_inv", 0) < msz - 1e-9
            and st.get("resplits", 0) < RESPLIT_MAX_PER_DAY):
        st["capital"] += msz * 1.0
        st["yes_inv"] = st.get("yes_inv", 0) + msz
        st["no_inv"] = st.get("no_inv", 0) + msz
        st["resplits"] = st.get("resplits", 0) + 1
        return True
    return False


def match_prints(st, arm, yes_id, no_id, msz, tape, default_since):
    """D5 fill engine, both arms. Prints from BOTH tokens are mapped to YES
    space (p_yes = 1 - p_no); the same trade surfacing via both token views
    is deduped on (txhash, ts, size). Quotes are time-matched from st['qh']
    ([ts, bid, ask]; either side may be None). Mutates st in place
    (pos/cost/real | yes_inv/no_inv/cash, and last_trade_ts). Returns fills."""
    qh = st.get("qh") or []
    last_ts = st.get("last_trade_ts", default_since)
    if not qh:
        return 0
    fills = 0
    cap = INV_CAP_MULT * msz
    pos, cost, real = st.get("pos", 0.0), st.get("cost", 0.0), st.get("real", 0.0)
    fseen = set()
    for tr in tape if isinstance(tape, list) else []:
        try:
            ts = float(tr.get("timestamp"))
            p = float(tr.get("price"))
            sz = float(tr.get("size") or 0)
            asset = str(tr.get("asset") or "")
        except Exception:
            continue
        if ts <= last_ts:
            continue
        if asset == yes_id:
            py = p
        elif asset == no_id:
            py = 1.0 - p                          # D5 complement mapping
        else:
            continue
        fk = (tr.get("transactionHash"), ts, round(sz, 4))
        if fk in fseen:                           # same trade via both tokens
            last_ts = max(last_ts, ts)
            continue
        fseen.add(fk)
        qbid = qask = None
        found = False
        for qt, qb, qa in reversed(qh):
            if qt <= ts:
                qbid, qask, found = qb, qa, True
                break
        if not found or (qbid is None and qask is None):
            last_ts = max(last_ts, ts)
            continue
        if arm == "split":
            if qask is not None and py > qask \
                    and st.get("yes_inv", 0) >= msz - 1e-9:
                st["yes_inv"] = st.get("yes_inv", 0) - msz
                st["cash"] = st.get("cash", 0.0) + msz * qask
                fills += 1
            elif qbid is not None and py < qbid \
                    and st.get("no_inv", 0) >= msz - 1e-9:
                # our YES-space bid IS an ask on the NO token at (1 - qbid)
                st["no_inv"] = st.get("no_inv", 0) - msz
                st["cash"] = st.get("cash", 0.0) + msz * (1 - qbid)
                fills += 1
        else:
            if qbid is not None and py < qbid and pos + msz <= cap + 1e-9:
                pos += msz
                cost += msz * qbid
                fills += 1
            elif qask is not None and py > qask and pos - msz >= -cap - 1e-9:
                if pos > 0:
                    avg = cost / pos if pos else 0.0
                    real += msz * (qask - avg)
                    cost -= msz * avg
                else:
                    cost -= msz * qask
                pos -= msz
                fills += 1
        last_ts = max(last_ts, ts)
    st.update({"pos": round(pos, 2), "cost": round(cost, 4),
               "real": round(real, 4), "last_trade_ts": last_ts})
    return fills


def net_of(st, arm, mid):
    """Mark-to-mid total P&L, comparable across arms."""
    if arm == "split":
        if "capital" not in st:
            return 0.0
        return (st.get("cash", 0.0) + st.get("yes_inv", 0.0) * mid
                + st.get("no_inv", 0.0) * (1 - mid) - st["capital"])
    return st.get("real", 0.0) + (st.get("pos", 0.0) * mid - st.get("cost", 0.0))


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
            if not is_sports(m):
                continue
            gs = parse_iso(m.get("gameStartTime"))
            if gs is None:                        # D1: game markets only
                continue
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
                         "q": (m.get("question") or "")[:70], "sector": "sports",
                         "yes": str(toks[0]), "no": str(toks[1]), "v": v, "msz": msz,
                         "pool": pool, "end": (m.get("endDate") or "")[:10],
                         "game_start": gs})
        if new == 0 or len(data) < 100:
            break
    rows.sort(key=lambda x: -x["pool"])
    picked = rows[:MAX_MARKETS_TOTAL]
    with open(os.path.join(base, "universe.json"), "w") as f:
        json.dump({"t": time.time(), "markets": picked}, f)
    return picked


# ── WebSocket book maintenance (verbatim v3) ─────────────────────────────────
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
                        if not asset:
                            continue
                        if et == "book":
                            _apply_book_snapshot(asset, msg)
                        elif et == "price_change":
                            _apply_price_change(asset, msg)
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


def run(base):
    universe = []
    state_path = os.path.join(base, "state.json")
    try:
        state = json.load(open(state_path))
    except Exception:
        state = {}
    last_discovery = 0.0
    last_minute = 0.0
    last_persist = 0.0
    lat_ms = deque(maxlen=2000)
    hb = time.time()

    while True:
        now = time.time()
        if os.path.exists(os.path.join(base, "STOP")):
            print("STOP sentinel — exiting cleanly", flush=True)
            return 0
        if now - last_persist > 3600:
            size_mb = sum(os.path.getsize(os.path.join(r, f))
                          for r, _, fs in os.walk(base) for f in fs) / 1e6
            if size_mb > MAX_DISK_MB:
                print(f"disk cap exceeded ({size_mb:.0f}MB) — exiting", flush=True)
                return 1

        # ── universe (re)discovery + WS thread lifecycle (verbatim v3) ─────
        if now - last_discovery > DISCOVERY_EVERY_S or not universe:
            u = discover(base)
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
                n_threads = 0
                for i in range(0, len(assets), WS_CHUNK):
                    threading.Thread(target=ws_worker,
                                     args=(assets[i:i + WS_CHUNK], gen),
                                     daemon=True).start()
                    n_threads += 1
                n_live = sum(1 for m in universe
                             if m.get("game_start") and m["game_start"] <= now)
                print(f"universe: {len(universe)} sports game markets "
                      f"({n_live} in play), {len(assets)} assets, "
                      f"{n_threads} ws conns (gen {gen})", flush=True)
            last_discovery = now

        # ── fast loop: gates + requotes off cached books (~1s cadence) ─────
        for m in universe:
            st = state.setdefault(str(m["id"]), {})
            bb, ba, bts = cached_touch(m["yes"])
            if bb is None or ba is None or not (0 < bb < ba <= 1):
                continue
            mid = (bb + ba) / 2
            mh = st.setdefault("mid_hist", [])
            mh.append([now, mid])
            while mh and now - mh[0][0] > 150:
                mh.pop(0)
            if now - mh[0][0] >= 60 and abs(mid - mh[0][1]) > VOL_PULL_PTS:
                st["pull_until"] = now + VOL_PULL_S
            st["last_mid"] = mid
            g = gate(m, st, now, mid)
            if g is not None:
                if st.get("bid") is not None or st.get("ask") is not None:
                    st["bid"] = st["ask"] = None
                    st.setdefault("qh", []).append([now, None, None])
                st["gates"] = {**st.get("gates", {}), g: st.get("gates", {}).get(g, 0) + 1}
                continue
            arm = arm_of(m["id"])
            st["arm"] = arm
            s_mine = (ba - bb) / 2
            if S(m["v"], s_mine, m["msz"]) <= 0:
                continue                          # touch outside incentive band
            if "cap_msz" not in st:
                st["cap_msz"] = m["msz"]          # freeze once (gamma drifts it)
            msz = st["cap_msz"]
            if arm == "split":
                split_init(st, msz, now)
                want_ask = mid + s_mine if st.get("yes_inv", 0) >= msz - 1e-9 else None
                want_bid = mid - s_mine if st.get("no_inv", 0) >= msz - 1e-9 else None
            else:
                want_bid, want_ask = mid - s_mine, mid + s_mine
            if (_quote_changed(st.get("bid"), want_bid)
                    or _quote_changed(st.get("ask"), want_ask)
                    or not st.get("qh")):
                st["bid"], st["ask"] = want_bid, want_ask
                qh = st.setdefault("qh", [])
                qh.append([now, want_bid, want_ask])
                if len(qh) > 400:
                    del qh[:len(qh) - 400]
                if bts:
                    lat_ms.append(max(0.0, (now - bts) * 1000))

        # ── minute loop: accrual, fills (both tapes, D5), samples ───────────
        if now - last_minute >= 60:
            last_minute = now
            from concurrent.futures import ThreadPoolExecutor
            tape_cache = {}
            need = [m for m in universe
                    if m.get("cid") and (state.get(str(m["id"])) or {}).get("qh")]
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(fetch_tape, m["cid"],
                                  (state.get(str(m["id"])) or {}).get("last_trade_ts", now - 60)):
                        str(m["id"]) for m in need}
                for fu in futs:
                    try:
                        tape_cache[futs[fu]] = fu.result(timeout=45) or []
                    except Exception:
                        tape_cache[futs[fu]] = []
            rows = []
            for m in universe:
                st = state.get(str(m["id"])) or {}
                mid = st.get("last_mid")
                if mid is None:
                    continue
                arm = st.get("arm") or arm_of(m["id"])
                msz = st.get("cap_msz") or m["msz"]
                # reward accrual with D6 one-sided haircut
                q_mine, n_sides = my_qmine(st, mid, m["v"], msz)
                share = 0.0
                if q_mine > 0:
                    q1, q2 = cached_scores(m, mid)
                    q_comp = max(min(q1, q2), max(q1, q2) / 3.0) \
                        if 0.10 <= mid <= 0.90 else min(q1, q2)
                    share = q_mine / (q_mine + q_comp)
                    dt = min(now - st.get("last_acc_t", now - 60), 120)
                    st["acc"] = round(st.get("acc", 0.0)
                                      + share * m["pool"] * dt / 86400.0, 6)
                    st["last_acc_t"] = now
                # fills (D5 both-token engine) + split lifecycle (D4)
                fills = match_prints(st, arm, m["yes"], m["no"], msz,
                                     tape_cache.get(str(m["id"])), now - 60)
                maybe_resplit(st, msz, now)
                st["net"] = round(net_of(st, arm, mid), 4)
                st.update({"sector": "sports", "q": m["q"], "pool": m["pool"],
                           "msz": msz})
                rows.append({"t": round(now), "id": m["id"], "arm": arm,
                             "mid": round(mid, 4), "shr": round(share, 4),
                             "fills": fills, "net": st["net"],
                             "in_play": bool(m.get("game_start")
                                             and m["game_start"] <= now),
                             "quoting": n_sides > 0, "sides": n_sides})
            day = time.strftime("%Y%m%d", time.gmtime(now))
            with open(os.path.join(base, f"samples-{day}.jsonl"), "a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            tmp = state_path + ".tmp"
            slim = {k: {kk: vv for kk, vv in v.items() if kk not in ("qh", "mid_hist")}
                    for k, v in state.items()}
            with open(tmp, "w") as f:
                json.dump(slim, f)
            os.replace(tmp, state_path)
            last_persist = now
            if now - hb > 300:
                hb = now
                lm = sorted(lat_ms)
                med = lm[len(lm) // 2] if lm else -1
                by_arm = defaultdict(lambda: [0, 0.0, 0.0])
                for st in state.values():
                    a = st.get("arm")
                    if not a:
                        continue
                    by_arm[a][0] += 1
                    by_arm[a][1] += st.get("acc", 0.0)
                    by_arm[a][2] += st.get("net", 0.0)
                with BOOKS_LOCK:
                    stale = sum(1 for b in BOOKS.values() if now - b["ts"] > 300)
                arm_str = " ".join(
                    f"{a}[n={c[0]} acc=${c[1]:.2f} net=${c[2]:.2f}]"
                    for a, c in sorted(by_arm.items()))
                print(f"hb: {sum(1 for r in rows if r['quoting'])}/{len(rows)} quoting "
                      f"({sum(1 for r in rows if r['in_play'])} in-play), {arm_str}, "
                      f"fills_min={sum(r['fills'] for r in rows)}, "
                      f"requote_lat_med={med:.0f}ms, books={len(BOOKS)}, "
                      f"stale_books={stale}", flush=True)

        time.sleep(1)


def report(base):
    state = json.load(open(os.path.join(base, "state.json")))
    by = defaultdict(lambda: [0, 0.0, 0.0])
    for st in state.values():
        a = st.get("arm")
        if not a:
            continue
        by[a][0] += 1
        by[a][1] += st.get("acc", 0.0)
        by[a][2] += st.get("net", 0.0)
    print("%-10s %4s %10s %10s" % ("arm", "n", "rewards$", "netPnL$"))
    for a, b in sorted(by.items()):
        print("%-10s %4d %10.2f %10.2f" % (a, b[0], b[1], b[2]))
    gates = defaultdict(int)
    for st in state.values():
        for g, c in (st.get("gates") or {}).items():
            gates[g] += c
    if gates:
        print("gate counts:", dict(sorted(gates.items())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/opt/pa2-maker-sim-v4")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.base, exist_ok=True)
    if a.report:
        report(a.base)
    elif a.run:
        sys.exit(run(a.base))
    else:
        ap.error("pass --run (daemon) or --report")
