#!/usr/bin/env python3
"""Paper Maker sim V4 — LIVE-SPORTS LANE arm. PAPER ONLY. 100% separate.

Tests the two Bucket-1 hypotheses from the 2026-07-15 3rd-party review
(docs/MAKER_V4_LANE_TEST_PLAN.md is the binding spec):

  H1  live-game lane: quote EVERY rewarded market with in-play semantics
      (= has gameStartTime; the only mechanical scope filter) ONLY while in
      play, with hard +/-1x inventory caps and ~1s WS re-centering (v2/v3
      own pre-game; zero overlap => clean attribution). Operator 2026-07-16:
      cover all items it can — sector (sports/esports/other/category label)
      is ATTRIBUTION ONLY, never exclusion; each answers separately. v1's
      esports in-play bleed was measured UNPROTECTED — v4 tests whether the
      armor fixes it.
  H2  split-inventory (ask-ask) quoting vs classic bid/ask-around-mid,
      A/B by market-id parity on the same universe.

Deltas vs V3 (everything else inherited verbatim from 55b089c):
  D1 universe = ALL rewarded markets WITH gameStartTime, <=40/sector-label,
     top-100 by pool, discovery every 15 min
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

MAX_MARKETS_TOTAL = 100       # game-market universe (200 assets = 3 WS chunks)
MAX_PER_SECTOR = 40           # no sector may crowd the others out
DISCOVERY_EVERY_S = 900       # game markets churn daily; 15 min (v3: 30)
DISCOVERY_RETRY_S = 60        # empty-universe retry (NOT every second — scan #3)
FINALIZE_PER_CYCLE = 20       # resolution-backfill gamma GETs per discovery
INV_CAP_MULT = 1              # D3 hard caps (v1-v3: 3)
RESPLIT_MAX_PER_DAY = 3       # D4 split-arm capital bound
SETTLED_HI, SETTLED_LO = 0.92, 0.08   # D7
VOL_PULL_PTS = 0.02
VOL_PULL_S = 600
REQUOTE_TICKS = 0.002
MAX_DISK_MB = 500
HTTP_BUDGET_PER_HOUR = 24000  # 100 mkts x 3 tape pages x 60/min = 18K worst case
                              # (scan #8: starving fills on hot nights is the
                              # asymmetric failure — rewards accrue via WS, fills don't)
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
    r"lck|lpl|lec|ewc|overwatch|rocket-league|call-of-duty|cod-|rainbow-six|"
    r"r6-|pubg|fortnite|starcraft|mlbb|mobile-legends|apex-legends|tekken|"
    r"street-fighter|halo-|smash-")


def game_sector(m):
    """Attribution LABEL for a game market — NEVER excludes. v4 covers every
    rewarded market with in-play semantics, i.e. a gameStartTime (operator
    2026-07-16: cover all items it can); the only mechanical constraint is
    that without a start time there is no in-play window to test. Sector
    labels exist purely so each answers separately in the sector x arm
    readout. Category is authoritative when present (scan: keyword fallback
    misclassified); keywords else; 'other' fallback."""
    c = (m.get("category") or "").strip().lower()
    if c:
        return c
    text = ((m.get("slug") or "") + " " + (m.get("question") or "")).lower()
    if ESPORTS_KW.search(text):
        return "esports"
    if SPORTS_KW.search(text):
        return "sports"
    return "other"


def parse_iso(s):
    if not s:
        return None
    try:
        t = str(s).strip().replace("Z", "+00:00")
        # gamma emits short offsets ("2026-07-16 00:00:00+00") which
        # datetime.fromisoformat only accepts from Python 3.11 — on 3.10 the
        # whole universe silently emptied (scan #3, live-verified format)
        if re.search(r"[+-]\d{2}$", t):
            t += ":00"
        return datetime.fromisoformat(t).timestamp()
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
        st["resplits_total"] = st.get("resplits_total", 0) + 1   # cumulative,
        return True                    # survives day rollover (scan: unlogged)
    return False


def _final_mid_from_gamma(market_id):
    """Resolution price for a CLOSED market via gamma outcomePrices (YES)."""
    d = get(f"{GAMMA}/{market_id}", timeout=10)
    if not isinstance(d, dict) or not d.get("closed"):
        return None
    try:
        op = d.get("outcomePrices")
        op = json.loads(op) if isinstance(op, str) else op
        fm = float(op[0])
        return fm if 0.0 <= fm <= 1.0 else None
    except Exception:
        return None


def finalize_dropped(state, universe_ids):
    """Scan finding #1 (CRITICAL): residual inventory used to freeze at the
    last observed mid — overstating NET for BOTH arms by ~residual x
    (1 - frozen_mid) on essentially every game, and biasing H2 toward split
    (which always carries residual). When a market leaves the universe:
    pull its quotes + drop stale qh (so re-entry can't fill at 15-min-stale
    prices — scan #5), then mark any residual to its RESOLUTION price once
    gamma reports the market closed. Bounded gamma GETs per cycle; markets
    not yet closed are retried next discovery."""
    fetches = 0
    for k, st in state.items():
        if k in universe_ids or st.get("final") or not st.get("arm"):
            continue
        if st.get("bid") is not None or st.get("ask") is not None:
            st["bid"] = st["ask"] = None
        st["qh"] = []
        residual = abs(st.get("pos", 0.0)) + st.get("yes_inv", 0.0) \
            + st.get("no_inv", 0.0)
        if residual < 1e-9 and "capital" not in st:
            st["final"] = 1                       # nothing to mark
            continue
        if fetches >= FINALIZE_PER_CYCLE:
            continue
        fetches += 1
        fm = _final_mid_from_gamma(k)
        if fm is None:
            continue                              # not closed yet — retry later
        st["final"] = 1
        st["final_mid"] = fm
        st["residual"] = round(residual, 2)
        st["net"] = round(net_of(st, st["arm"], fm), 4)


def match_prints(st, arm, yes_id, no_id, msz, tape, default_since):
    """D5 fill engine, both arms. Prints from BOTH tokens are mapped to YES
    space (p_yes = 1 - p_no). Quotes are time-matched from st['qh']
    ([ts, bid, ask]; either side may be None). Mutates st in place
    (pos/cost/real | yes_inv/no_inv/cash, last_trade_ts, seen_edge).
    Returns fills.

    Watermarking (scan #2 rewrite): data-api timestamps are INTEGER SECONDS
    and ~24% of live prints share a second — a per-print `ts <= last_ts`
    watermark silently dropped every same-second sibling. Instead: process
    everything with ts >= last_ts, and make the edge second exactly-once via
    a persisted identity set (fetch_tape's own 5-tuple) for prints AT the
    watermark, which are re-fetched next tick by design.

    The default data-api view is TAKER-ONLY (live-verified 2026-07-15:
    each trade appears once, on the taker's token; 0/1,100 cross-token
    duplicates). Do NOT switch fetch_tape to takerOnly=false without adding
    cross-leg dedup — in that view every trade is reported twice."""
    qh = st.get("qh") or []
    last_ts = st.get("last_trade_ts", default_since)
    edge = set(map(tuple, st.get("seen_edge") or []))
    if not qh:
        return 0
    fills = 0
    cap = INV_CAP_MULT * msz
    pos, cost, real = st.get("pos", 0.0), st.get("cost", 0.0), st.get("real", 0.0)
    max_ts = last_ts
    cur_edge = set(edge)
    for tr in tape if isinstance(tape, list) else []:
        try:
            ts = float(tr.get("timestamp"))
            p = float(tr.get("price"))
            sz = float(tr.get("size") or 0)
            asset = str(tr.get("asset") or "")
        except Exception:
            continue
        if ts < last_ts:
            continue
        if asset == yes_id:
            py = p
        elif asset == no_id:
            py = 1.0 - p                          # D5 complement mapping
        else:
            continue
        key = (tr.get("transactionHash"), ts, p, sz, asset)
        if ts == last_ts and key in edge:
            continue                              # edge-second re-fetch
        if ts > max_ts:                           # tape is sorted ascending
            max_ts = ts
            cur_edge = set()
        cur_edge.add(key)
        qbid = qask = None
        found = False
        for qt, qb, qa in reversed(qh):
            if qt <= ts:
                qbid, qask, found = qb, qa, True
                break
        if not found or (qbid is None and qask is None):
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
    st.update({"pos": round(pos, 2), "cost": round(cost, 4),
               "real": round(real, 4), "last_trade_ts": max_ts,
               "seen_edge": [list(k) for k in list(cur_edge)[:300]]})
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
            gs = parse_iso(m.get("gameStartTime"))
            if gs is None:                        # D1: the ONLY scope filter —
                continue                          # no start time = no in-play window
            sec = game_sector(m)
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
            # isinstance first: a scalar clobTokenIds made len() raise OUTSIDE
            # the try -> discover -> run crash loop (scan #6); a JSON string
            # would yield 1-char garbage token ids
            if not isinstance(toks, list) or len(toks) < 2 or v <= 0 or msz <= 0:
                continue
            rows.append({"id": m.get("id"), "cid": m.get("conditionId"),
                         "q": (m.get("question") or "")[:70], "sector": sec,
                         "yes": str(toks[0]), "no": str(toks[1]), "v": v, "msz": msz,
                         "pool": pool, "end": (m.get("endDate") or "")[:10],
                         "game_start": gs})
        if new == 0 or len(data) < 100:
            break
    by_sec = defaultdict(list)
    for r in rows:
        by_sec[r["sector"]].append(r)
    picked = []
    for sec, ms in by_sec.items():
        ms.sort(key=lambda x: -x["pool"])
        picked.extend(ms[:MAX_PER_SECTOR])
    picked.sort(key=lambda x: -x["pool"])
    picked = picked[:MAX_MARKETS_TOTAL]
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
                                _apply_price_change(asset, msg)   # legacy single-asset shape
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


def run(base):
    universe = []
    state_path = os.path.join(base, "state.json")
    try:
        state = json.load(open(state_path))
    except Exception:
        state = {}
    last_discovery = 0.0
    last_minute = 0.0
    last_disk = 0.0
    lat_ms = deque(maxlen=2000)
    hb = time.time()

    while True:
        now = time.time()
        if os.path.exists(os.path.join(base, "STOP")):
            print("STOP sentinel — exiting cleanly", flush=True)
            return 0
        # dedicated hourly clock (scan #7: gating on last_persist made this
        # check dead in steady state — persistence refreshes it every minute)
        if now - last_disk > 3600:
            last_disk = now
            size_mb = sum(os.path.getsize(os.path.join(r, f))
                          for r, _, fs in os.walk(base) for f in fs) / 1e6
            if size_mb > MAX_DISK_MB:
                print(f"disk cap exceeded ({size_mb:.0f}MB) — exiting", flush=True)
                return 1

        # ── universe (re)discovery + WS thread lifecycle (verbatim v3) ─────
        # empty-universe retry is BACKED OFF (scan #3: `or not universe` alone
        # re-ran 21-page discovery every second, burning the HTTP budget)
        if (now - last_discovery > DISCOVERY_EVERY_S
                or (not universe and now - last_discovery > DISCOVERY_RETRY_S)):
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
                sec_n = defaultdict(int)
                for m in universe:
                    sec_n[m["sector"]] += 1
                print(f"universe: {len(universe)} game markets "
                      f"({dict(sorted(sec_n.items()))}, {n_live} in play), "
                      f"{len(assets)} assets, {n_threads} ws conns (gen {gen})",
                      flush=True)
                finalize_dropped(state, {str(m["id"]) for m in universe})
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
                # touch outside incentive band: PULL quotes (scan #9 — a bare
                # continue left stale quotes standing, fillable at stale prices)
                if st.get("bid") is not None or st.get("ask") is not None:
                    st["bid"] = st["ask"] = None
                    st.setdefault("qh", []).append([now, None, None])
                st["gates"] = {**st.get("gates", {}),
                               "band": st.get("gates", {}).get("band", 0) + 1}
                continue
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
                # freshness guard (scan #4): with no live book, cached_scores
                # returns (0,0) -> q_comp 0 -> phantom share=1.0 accrual
                if q_mine > 0:
                    _, _, bts_m = cached_touch(m["yes"])
                    if bts_m is None or now - bts_m > 300:
                        q_mine = 0.0
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
                st.update({"sector": m["sector"], "q": m["q"], "pool": m["pool"],
                           "msz": msz})
                rows.append({"t": round(now), "id": m["id"], "arm": arm,
                             "sec": m["sector"],
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
            # qh/mid_hist are ephemeral; bid/ask/last_mid/last_acc_t are
            # DELIBERATELY dropped too (scan #4): persisting them let the first
            # post-restart minute-loop accrue at share=1.0 against empty book
            # caches. seen_edge MUST persist (exactly-once fills across
            # restarts) and does.
            _EPHEMERAL = ("qh", "mid_hist", "bid", "ask", "last_mid", "last_acc_t")
            slim = {k: {kk: vv for kk, vv in v.items() if kk not in _EPHEMERAL}
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
                fills_arm = defaultdict(int)
                for r in rows:
                    fills_arm[r["arm"]] += r["fills"]
                arm_str = " ".join(
                    f"{a}[n={c[0]} acc=${c[1]:.2f} net=${c[2]:.2f} "
                    f"fills={fills_arm.get(a, 0)}]"
                    for a, c in sorted(by_arm.items()))
                resplits = sum(st.get("resplits_total", 0) for st in state.values())
                print(f"hb: {sum(1 for r in rows if r['quoting'])}/{len(rows)} quoting "
                      f"({sum(1 for r in rows if r['in_play'])} in-play), {arm_str}, "
                      f"fills_min={sum(r['fills'] for r in rows)}, "
                      f"resplits={resplits}, requote_lat_med={med:.0f}ms, "
                      f"books={len(BOOKS)}, stale_books={stale}", flush=True)

        time.sleep(1)


def report(base):
    state = json.load(open(os.path.join(base, "state.json")))
    by = defaultdict(lambda: [0, 0.0, 0.0])
    for st in state.values():
        a = st.get("arm")
        if not a:
            continue
        key = (st.get("sector") or "?", a)
        by[key][0] += 1
        by[key][1] += st.get("acc", 0.0)
        by[key][2] += st.get("net", 0.0)
    print("%-10s %-10s %4s %10s %10s" % ("sector", "arm", "n", "rewards$", "netPnL$"))
    for (sec, a), b in sorted(by.items()):
        print("%-10s %-10s %4d %10.2f %10.2f" % (sec, a, b[0], b[1], b[2]))
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
