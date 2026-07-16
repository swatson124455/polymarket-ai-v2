#!/usr/bin/env python3
"""Paper Maker sim V3 — WebSocket-driven arm. PAPER ONLY. 100% separate.

Same policy gates as V2 (in_play / extreme_wx / last_hours / vol_pull, width
A/B by market-id parity, min size) — the ONLY delta vs V2 is reaction speed:
book updates arrive by WS push and hypothetical quotes re-center within ~1s
instead of a 2-minute poll. The V2↔V3 comparison therefore isolates the
dollar value of quote freshness (staleness cost), and V3 records its own
requote latency distribution so the speed claim is measured, not asserted.

Architecture (threads, no asyncio):
  * discovery loop: rewarded-market universe from gamma every 30 min (as V2)
  * WS threads: chunks of asset ids on wss://.../ws/market; maintain local
    books from 'book' snapshots + 'price_change' deltas; reconnect forever
  * quote loop (1s): gates -> re-center hypothetical quotes when mid moved
    >=2 ticks; log requote latency (book-event -> quote update)
  * minute loop: reward-share accrual from CACHED books (no HTTP), tape poll
    for fills matched against the quote standing AT each print's timestamp
    (per-market quote history), state/samples persistence

Guard rails: no keys, no DATABASE_URL, HTTP GET + WS subscribe only (cannot
trade); STOP sentinel; disk cap; HTTP budget/hour; bounded book cache; systemd
strict sandbox + Restart=on-failure. Own dir/venv/state — shares nothing with
V1 (naive control) or V2 (gated poller).

Usage:  maker_paper_sim_v3.py --run [--base /opt/pa2-maker-sim-v3]
        maker_paper_sim_v3.py --report [--base ...]
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

UA = {"User-Agent": "pa2-maker-paper-sim-v3/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
BOOK = "https://clob.polymarket.com/book?token_id="
TAPE = "https://data-api.polymarket.com/trades?market={cid}&limit=200"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

MAX_MARKETS_PER_SECTOR = 25
MAX_MARKETS_TOTAL = 140
DISCOVERY_EVERY_S = 1800
INV_CAP_MULT = 3
VOL_PULL_PTS = 0.02
VOL_PULL_S = 600
LAST_HOURS_GATE_UTC = 19
REQUOTE_TICKS = 0.002        # re-center when mid moved >= 2 ticks
MAX_DISK_MB = 500
HTTP_BUDGET_PER_HOUR = 12000  # tape+gamma+book-fallback; WS carries the books
WS_CHUNK = 90
WS_IDLE_RECONNECT_S = 40

BOOKS = {}                    # asset_id -> {"bids":{p:s}, "asks":{p:s}, "ts":float}
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
    """All tape prints since since_ts, OLDEST-FIRST (fixes the 200-print
    truncation AND the newest-first iteration skip — verified 2026-07-15)."""
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
                         "yes": str(toks[0]), "no": str(toks[1]), "v": v, "msz": msz,
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


# ── WebSocket book maintenance ───────────────────────────────────────────────
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
    # 2026 market-channel shape: no top-level asset_id; each entry in
    # price_changes[] carries its own asset_id (verified live 2026-07-16 —
    # under the legacy-only parser every price_change was silently dropped,
    # so books refreshed only on trade-driven book events / reconnects)
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
                        break                      # idle — reconnect
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
                                _apply_price_change(asset, msg)   # legacy single-asset shape
                            else:
                                _apply_price_change_batched(msg)
        except Exception:
            time.sleep(3)                          # backoff, then reconnect


def cached_touch(asset):
    with BOOKS_LOCK:
        book = BOOKS.get(asset)
        if not book:
            return None, None, None
        bb = max(book["bids"]) if book["bids"] else None
        ba = min(book["asks"]) if book["asks"] else None
        return bb, ba, book["ts"]


def cached_scores(m, mid):
    """Q1/Q2 competitor scores from cached books (no HTTP)."""
    v = m["v"]
    with BOOKS_LOCK:
        y = BOOKS.get(m["yes"], {"bids": {}, "asks": {}})
        n = BOOKS.get(m["no"], {"bids": {}, "asks": {}})
        def sc(levels, center):
            return sum(S(v, abs(p - center), s) for p, s in levels.items())
        q1 = sc(y["bids"], mid) + sc(n["asks"], 1 - mid)
        q2 = sc(y["asks"], mid) + sc(n["bids"], 1 - mid)
    return q1, q2


def gate(m, st, now, mid):
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
    threads = []
    hb = time.time()

    while True:
        now = time.time()
        if os.path.exists(os.path.join(base, "STOP")):
            print("STOP sentinel — exiting cleanly", flush=True)
            return 0
        if now - last_persist > 3600:              # hourly disk-cap check
            size_mb = sum(os.path.getsize(os.path.join(r, f))
                          for r, _, fs in os.walk(base) for f in fs) / 1e6
            if size_mb > MAX_DISK_MB:
                print(f"disk cap exceeded ({size_mb:.0f}MB) — exiting", flush=True)
                return 1

        # ── universe (re)discovery + WS thread lifecycle ────────────────────
        if now - last_discovery > DISCOVERY_EVERY_S or not universe:
            u = discover(base)
            if u:
                universe = u
                GEN["n"] += 1
                gen = GEN["n"]
                assets = []
                for m in universe:
                    assets.extend((m["yes"], m["no"]))
                # evict books for assets no longer subscribed — without this the
                # dict grows per refresh and rolled-out markets pollute the
                # stale_books metric (observed 498 books / 210 "stale" at 23:40Z
                # 2026-07-15, masking whether any LIVE subscription was stale)
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

        # ── fast loop: gates + requotes off cached books (~1s cadence) ─────
        for m in universe:
            st = state.setdefault(str(m["id"]), {})
            bb, ba, bts = cached_touch(m["yes"])
            if bb is None or ba is None or not (0 < bb < ba <= 1):
                continue
            mid = (bb + ba) / 2
            # vol trigger over a ~120s WINDOW to match V2's tick-to-tick
            # semantics (comparing 1s steps never accumulates 2pt — verified
            # divergence 2026-07-15; window keeps the arms rule-identical)
            mh = st.setdefault("mid_hist", [])
            mh.append([now, mid])
            while mh and now - mh[0][0] > 150:
                mh.pop(0)
            if now - mh[0][0] >= 60 and abs(mid - mh[0][1]) > VOL_PULL_PTS:
                st["pull_until"] = now + VOL_PULL_S
            st["last_mid"] = mid
            g = gate(m, st, now, mid)
            if g is not None:
                if st.get("bid") is not None:
                    st["bid"] = st["ask"] = None
                    st.setdefault("qh", []).append([now, None, None])
                st["gates"] = {**st.get("gates", {}), g: st.get("gates", {}).get(g, 0) + 1}
                continue
            try:
                arm = "wide" if int(str(m["id"])[-1]) % 2 else "touch"
            except Exception:
                arm = "touch"
            s_touch = (ba - bb) / 2
            s_mine = max(s_touch, m["v"] / 2) if arm == "wide" else s_touch
            if S(m["v"], s_mine, m["msz"]) <= 0:
                continue
            want_bid, want_ask = mid - s_mine, mid + s_mine
            cur = st.get("bid")
            # also (re)seed when quote history is empty — after a daemon
            # restart st carries bid/ask but qh is stripped from persistence,
            # and without this fill detection stays dead on quiet markets
            if cur is None or abs(want_bid - cur) >= REQUOTE_TICKS or not st.get("qh"):
                st["bid"], st["ask"], st["arm"] = want_bid, want_ask, arm
                qh = st.setdefault("qh", [])
                qh.append([now, want_bid, want_ask])
                if len(qh) > 400:
                    del qh[:len(qh) - 400]
                if bts:
                    lat_ms.append(max(0.0, (now - bts) * 1000))

        # ── minute loop: accrual, fills vs time-matched quotes, samples ────
        if now - last_minute >= 60:
            last_minute = now
            # prefetch tapes CONCURRENTLY — 140 serial GETs blocked this loop
            # (and therefore the fast requote loop) for up to ~1 min
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
                if st.get("bid") is not None:
                    q_mine = S(m["v"], abs(mid - st["bid"]), m["msz"])
                    q1, q2 = cached_scores(m, mid)
                    q_comp = max(min(q1, q2), max(q1, q2) / 3.0) if 0.10 <= mid <= 0.90 else min(q1, q2)
                    share = q_mine / (q_mine + q_comp) if q_mine > 0 else 0.0
                    dt = min(now - st.get("last_acc_t", now - 60), 120)
                    st["acc"] = round(st.get("acc", 0.0) + share * m["pool"] * dt / 86400.0, 6)
                    st["last_acc_t"] = now
                else:
                    share = 0.0
                # fills: prints matched to the quote standing AT print time
                fills = 0
                pos, cost, real = st.get("pos", 0.0), st.get("cost", 0.0), st.get("real", 0.0)
                qh = st.get("qh") or []
                last_ts = st.get("last_trade_ts", now - 60)
                if qh and m.get("cid"):
                    tape = tape_cache.get(str(m["id"])) or []
                    # frozen-msz cap, post-fill guard (gamma adjusts
                    # rewardsMinSize intraday; current-msz caps drift to 5x)
                    cap_msz = st.get("cap_msz")
                    if not cap_msz or pos == 0:
                        cap_msz = st["cap_msz"] = m["msz"]
                    cap = INV_CAP_MULT * cap_msz
                    for tr in tape if isinstance(tape, list) else []:
                        try:
                            ts = float(tr.get("timestamp"))
                            p = float(tr.get("price"))
                            asset = str(tr.get("asset") or "")
                        except Exception:
                            continue
                        if ts <= last_ts or asset != m["yes"]:
                            continue
                        qbid = qask = None
                        for qt, qb, qa in reversed(qh):
                            if qt <= ts:
                                qbid, qask = qb, qa
                                break
                        if qbid is None:
                            last_ts = max(last_ts, ts)
                            continue
                        msz = m["msz"]
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
                        last_ts = max(last_ts, ts)
                st.update({"pos": round(pos, 2), "cost": round(cost, 4),
                           "real": round(real, 4), "last_trade_ts": last_ts,
                           "sector": m["sector"], "q": m["q"], "pool": m["pool"],
                           "msz": m["msz"]})
                rows.append({"t": round(now), "id": m["id"], "sec": m["sector"],
                             "arm": st.get("arm"), "mid": round(mid, 4),
                             "shr": round(share, 4), "pos": st["pos"], "fills": fills,
                             "real": st["real"],
                             "unreal": round(pos * mid - cost, 4),
                             "quoting": st.get("bid") is not None})
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
                quoting = sum(1 for r in rows if r["quoting"])
                # stale-book counter: a silently-dead WS chunk thread would
                # otherwise leave ~90 books frozen with no visible symptom
                with BOOKS_LOCK:
                    stale = sum(1 for b in BOOKS.values() if now - b["ts"] > 300)
                print(f"hb: {quoting}/{len(rows)} quoting, "
                      f"acc_total=${sum(st.get('acc',0) for st in state.values()):.2f}, "
                      f"fills_min={sum(r['fills'] for r in rows)}, "
                      f"requote_lat_med={med:.0f}ms, books={len(BOOKS)}, "
                      f"stale_books={stale}", flush=True)

        time.sleep(1)


def report(base):
    state = json.load(open(os.path.join(base, "state.json")))
    by = defaultdict(lambda: [0, 0.0, 0.0])
    for st in state.values():
        if not st.get("sector"):
            continue
        by[st["sector"]][0] += 1
        by[st["sector"]][1] += st.get("acc", 0.0)
        by[st["sector"]][2] += st.get("real", 0.0)
    print("%-14s %4s %10s %10s" % ("sector", "n", "rewards$", "realPnL$"))
    for sec, b in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print("%-14s %4d %10.2f %10.2f" % (sec, b[0], b[1], b[2]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/opt/pa2-maker-sim-v3")
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
