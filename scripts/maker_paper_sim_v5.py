#!/usr/bin/env python3
"""Paper Maker sim V5 — GATE LAB arm. PAPER ONLY. 100% separate.

Runs SIX gate policies simultaneously on the same markets, same cached books,
same tape prints — perfectly paired A/B of every gate idea and combination
(operator 2026-07-17: "measure them all in a silo, mixing and matching").
Cloned from V3's proven core (55b089c + 0c9708f batched price_change fix);
the ONLY delta is the policy matrix: per-market shared inputs, per-policy
virtual quotes/fills/rewards ledgers.

Policies (fitted params from mm_gate_fits 2026-07-17, recorded-data fits):
  P0_base     V3 gates verbatim: in_play/extreme_wx/last_hours(19Z)/vol(2c,10m)
  P1_volfit   vol gate refit: pull at >1.5c per 2min, off 15min (fit 1:
              continuation is tail-driven; p75 +4c@10m after a 2c move)
  P2_ramp     last_hours replaced by wind-down: pull when <=9h to market end
              (fit 2: adverse>2pt peaks 20-31% in the final 1-9h vs ~4% at 1-3d)
  P3_tapevel  base + tape-velocity detector: market "hot" (pull 10min) when
              >=8 prints/5min OR |mid move| >=3c/5min — catches live games and
              news without needing a start time
  P4_all      P1+P2+P3 combined
  P5_ungated  no gates at all — paired control on the SAME universe
  P6_tilted   (added 2026-07-17 ~22Z, own era) P0 gates verbatim + quote
              center tilted toward WB's forecast prob on markets covered by
              /opt/pa2-maker-feeds/wb_forecasts.jsonl (read-only, append-only
              contract; WB S231 accepted). No forecast -> shift 0.0 -> quotes
              BIT-IDENTICAL to P0 (pairing invariant). WB semantics encoded:
              prob is P(YES) never inverted; Hong Kong weight 0 (mis-
              stationed); 7 cold-start cities x0.5 until 2026-08-01;
              non-temperature models x0.5; cheap-NO guard = no NO-ward tilt
              when wb_prob < 0.20 (WB: low buckets under-price YES). A
              TILTED quote's rewards score the two-sided MIN (real-program
              semantics; the family bid-side proxy would inflate a YES-ward
              tilt — adversarial review 07-17). Known accepted limits: trust
              weight frozen at feed-load time (one-day smear at the 08-01
              cold-start boundary); +1/6 sample volume vs the 6-policy era.

Guard rails identical to V3: no keys, no DATABASE_URL, HTTP GET + WS
subscribe only (cannot trade); STOP sentinel; disk cap + gzip rotation;
HTTP budget/hour (tape fetched ONCE per market, shared by all policies);
bounded book cache; systemd strict sandbox. Own dir/venv/state.

Usage:  maker_paper_sim_v5.py --run [--base /opt/pa2-maker-sim-v5]
        maker_paper_sim_v5.py --report [--base ...]
"""
import argparse
import calendar
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

UA = {"User-Agent": "pa2-maker-paper-sim-v5/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
TAPE = "https://data-api.polymarket.com/trades?market={cid}&limit=200"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

MAX_MARKETS_PER_SECTOR = 25
MAX_MARKETS_TOTAL = 140
DISCOVERY_EVERY_S = 1800
DISCOVERY_RETRY_S = 60        # empty-universe retry backoff (review: the 1s
                              # retry loop could burn the whole HTTP budget)
INV_CAP_MULT = 3
LAST_HOURS_GATE_UTC = 19
REQUOTE_TICKS = 0.002
MAX_DISK_MB = 500
HTTP_BUDGET_PER_HOUR = 36000  # v5 fetches tape for ALL 140 markets each minute
                              # (the prints-velocity signal needs it): worst
                              # case 140x3 pages + discovery ~= 27K/hr (review
                              # finding: 24K did NOT cover it). Exhaustion is
                              # SILENT (get() -> None) so hb reports usage.
WS_CHUNK = 90
WS_IDLE_RECONNECT_S = 40

# ── the policy matrix ────────────────────────────────────────────────────────
POLICIES = {
    "P0_base":    {"gated": True,  "vol_pts": 0.020, "vol_s": 600, "ramp_h": None, "tapevel": False},
    "P1_volfit":  {"gated": True,  "vol_pts": 0.015, "vol_s": 900, "ramp_h": None, "tapevel": False},
    "P2_ramp":    {"gated": True,  "vol_pts": 0.020, "vol_s": 600, "ramp_h": 9.0,  "tapevel": False},
    "P3_tapevel": {"gated": True,  "vol_pts": 0.020, "vol_s": 600, "ramp_h": None, "tapevel": True},
    "P4_all":     {"gated": True,  "vol_pts": 0.015, "vol_s": 900, "ramp_h": 9.0,  "tapevel": True},
    "P5_ungated": {"gated": False, "vol_pts": None,  "vol_s": 0,   "ramp_h": None, "tapevel": False},
    "P6_tilted":  {"gated": True,  "vol_pts": 0.020, "vol_s": 600, "ramp_h": None, "tapevel": False, "tilt": True},
}
TAPEVEL_PRINTS_5M = 8         # prints in 5 min that mark a market "hot"
TAPEVEL_MOVE_5M = 0.03        # or a 3c mid move over 5 min
TAPEVEL_OFF_S = 600

# ── P6_tilted: WB forecast feed (contract: README_WB_FORECASTS_CONTRACT.md) ──
WB_FEED = "/opt/pa2-maker-feeds/wb_forecasts.jsonl"
WB_STALE_S = 24 * 3600        # cid-matching scopes the market day; this is a
                              # safety net (WB dedups: silence = unchanged)
WB_READ_CAP = 8 * 1024 * 1024  # max bytes per load — a runaway writer must
                               # never turn the minute loop into a giant alloc
WB_LOW_PROB = 0.20            # WB caveat: [0,0.2) buckets resolve YES ~0.27
                              # vs ~0.04 predicted -> never tilt toward NO here
TILT_K = 0.5                  # shift half the WB-vs-market disagreement...
TILT_MAX = 0.010              # ...capped at 1 cent ("slightly", per proposal)
WB_EXCLUDE_CITIES = {"Hong Kong"}   # mis-stationed until WB S233 HKO work
WB_COLDSTART_CITIES = {"Dallas", "Denver", "Houston", "Seoul", "Taipei",
                       "Milan", "Istanbul"}   # re-stationed 07-17, EMOS reset
WB_COLDSTART_UNTIL = calendar.timegm((2026, 8, 1, 0, 0, 0))
WB_TILT = {}                  # cid -> {"prob","t","w"}; read-only elsewhere
_wb_feed = {"off": 0}


def wb_weight(city, model, now):
    """Trust-tier weight per WB's S231 semantics answer. Pseudo-station
    cities are not enumerable from our side ("etc.") — they ride at 1.0 and
    the readout segments by city from the sample rows instead."""
    if city in WB_EXCLUDE_CITIES:
        return 0.0
    w = 1.0
    if city in WB_COLDSTART_CITIES and now < WB_COLDSTART_UNTIL:
        w *= 0.5
    if model != "weather_temperature":
        w *= 0.5
    return w


def wb_shift(wb, mid, v, s_mine):
    """Bounded quote-center shift toward WB's P(YES). 0.0 when no forecast,
    zero weight, cheap-NO guard, or no band room — 0.0 keeps P6's quote
    arithmetic bit-identical to P0 (mid + 0.0 - s == mid - s)."""
    if not wb or wb["w"] <= 0:
        return 0.0
    raw = TILT_K * (wb["prob"] - mid) * wb["w"]
    if wb["prob"] < WB_LOW_PROB and raw < 0:
        return 0.0
    cap = min(TILT_MAX, max(0.0, v - s_mine - 0.002))
    return max(-cap, min(cap, raw))


def q_mine_of(pol, v, mid, bid, ask, msz, tilt_q):
    """Reward score for a standing quote. P0-P5 keep the historical bid-side
    proxy (bid==min side for symmetric quotes; changing it mid-experiment
    would break the A/B). A TILTED quote must score the two-sided MIN like
    the real program does — the bid-side proxy would credit a YES-ward tilt
    with MORE rewards when reality pays LESS (adversarial review 07-17,
    finding 1: sign-inverted bias on the exact number under test)."""
    q = S(v, abs(mid - bid), msz)
    if pol.get("tilt") and tilt_q and ask is not None:
        q = min(q, S(v, abs(ask - mid), msz))
    return q


def load_wb_feed(now, path=WB_FEED):
    """Incremental read of WB's append-only feed into WB_TILT. Latest line
    per market_id wins; a partial trailing line (WB appending concurrently)
    is left for the next read; file shrink (rotation) resets; entries older
    than WB_STALE_S pruned. Any failure leaves the previous map untouched
    (WB contract: silence = no data, handled gracefully)."""
    try:
        sz = os.path.getsize(path)
    except OSError:
        return
    if sz < _wb_feed["off"]:
        _wb_feed["off"] = 0
        WB_TILT.clear()
    chunk = b""
    try:
        with open(path, "rb") as f:
            # head-prefix guard: an append-only file's first bytes never
            # change. A same-or-longer REPLACEMENT (copytruncate race,
            # history re-emit) passes the size check but would be read from
            # a misaligned offset — silently skipping whole lines (review
            # finding 2). Prefix mismatch -> full re-read.
            head = f.read(64)
            if _wb_feed["off"] and not head.startswith(_wb_feed.get("head", b"")):
                _wb_feed["off"] = 0
                WB_TILT.clear()
            _wb_feed["head"] = head
            if sz > _wb_feed["off"]:
                f.seek(_wb_feed["off"])
                chunk = f.read(min(sz - _wb_feed["off"], WB_READ_CAP))
    except OSError:
        return
    body, nl, _rest = chunk.rpartition(b"\n")
    if nl:
        _wb_feed["off"] += len(body) + 1
        for line in body.split(b"\n"):
            try:
                d = json.loads(line)
                cid = str(d.get("market_id") or "").strip().lower()
                t = float(d.get("t") or 0)
                prob = float(d["prob"])
            except Exception:
                continue
            # t plausibility: NaN/Infinity/ms-epoch would make an entry
            # immortal vs the staleness prune (review finding 6)
            if not cid or not (0.0 <= prob <= 1.0) \
                    or not (0.0 <= t <= now + 86400.0):
                continue
            prev = WB_TILT.get(cid)
            if prev and prev["t"] > t:
                continue
            WB_TILT[cid] = {"prob": prob, "t": t,
                            "w": wb_weight(str(d.get("city") or ""),
                                           str(d.get("model") or ""), now)}
    for cid in [c for c, d in WB_TILT.items() if now - d["t"] > WB_STALE_S]:
        WB_TILT.pop(cid, None)

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
                         "end_ts": parse_iso(m.get("endDate")),
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
    # restart normalization: never credit accrual across a downtime gap, and
    # never let a pre-restart pull_until linger against a fresh clock
    _t0 = time.time()
    for _v in state.values():
        if isinstance(_v, dict) and "last_acc_t" in _v:
            _v["last_acc_t"] = _t0
    load_wb_feed(_t0)
    print(f"wb tilt feed: {len(WB_TILT)} forecasts loaded "
          f"(P6_tilted active)", flush=True)
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
            # gzip-rotate previous days' samples (6 policies write ~3x v3 volume)
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
            if u and universe and len(u) < max(40, len(universe) // 2):
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
            wb = WB_TILT.get(str(m.get("cid") or "").lower())
            for pol_name, pol in POLICIES.items():
                st = st_of(key, pol_name)
                if pol["vol_pts"] is not None and vol_ref is not None:
                    t0, m0 = vol_ref
                    if now - t0 >= 60 and abs(mid - m0) > pol["vol_pts"]:
                        st["pull_until"] = now + pol["vol_s"]
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
                shift = 0.0
                if pol.get("tilt"):
                    shift = wb_shift(wb, mid, m["v"], s_mine)
                    if wb:
                        st["wbp"], st["wbw"] = wb["prob"], wb["w"]
                    else:
                        for kk in ("tilt", "wbp", "wbw"):
                            st.pop(kk, None)
                want_bid, want_ask = mid + shift - s_mine, mid + shift + s_mine
                if shift:
                    # post-only: a tilted quote must never cross the live touch
                    want_bid = min(want_bid, ba - 0.001)
                    want_ask = max(want_ask, bb + 0.001)
                    if want_bid >= want_ask:
                        # locked/crossed on a <=2-tick book: no room to tilt —
                        # fall back to the P0 quote (review finding 9)
                        shift = 0.0
                        want_bid, want_ask = mid - s_mine, mid + s_mine
                # a tilt CHANGE forces a requote even under the 2-tick
                # hysteresis: otherwise a pruned/guarded forecast leaves a
                # sub-tick zombie tilt standing and fills get misattributed
                # (review findings 4+5). Always False for P0-P5.
                tilt_requote = pol.get("tilt") and (
                    abs(shift - st.get("tilt_q", 0.0)) >= 0.001
                    or (shift == 0.0 and st.get("tilt_q", 0.0) != 0.0))
                cur = st.get("bid")
                if cur is None or abs(want_bid - cur) >= REQUOTE_TICKS \
                        or tilt_requote or not st.get("qh"):
                    st["bid"], st["ask"], st["arm"] = want_bid, want_ask, arm
                    if pol.get("tilt"):
                        st["tilt_q"] = round(shift, 4)   # the STANDING shift
                    qh = st.setdefault("qh", [])
                    qh.append([now, want_bid, want_ask])
                    if len(qh) > 400:
                        del qh[:len(qh) - 400]
                    if bts and pol_name == "P0_base":
                        lat_ms.append(max(0.0, (now - bts) * 1000))

        # ── minute loop: shared tape, per-policy accrual + fills, samples ───
        if now - last_minute >= 60:
            last_minute = now
            minute_n += 1
            load_wb_feed(now)
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
                        q_mine = q_mine_of(pol, m["v"], mid, st["bid"],
                                           st.get("ask"), m["msz"],
                                           st.get("tilt_q", 0.0))
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
                               "pool": m["pool"], "msz": m["msz"]})
                    # per-policy sample every 5 min, plus immediately on fills
                    if fills or minute_n % 5 == 0:
                        r = {"t": round(now), "id": m["id"], "pol": pol_name,
                             "sec": m["sector"], "mid": round(mid, 4),
                             "shr": round(share, 4),
                             "pos": st.get("pos", 0.0), "fills": fills,
                             "real": st.get("real", 0.0),
                             "unreal": round(st.get("pos", 0.0) * mid
                                             - st.get("cost", 0.0), 4),
                             "quoting": st.get("bid") is not None}
                        if pol.get("tilt") and "wbp" in st:
                            # trust-tier segmentation at read time (WB ask);
                            # tilt = the STANDING quote's shift, not the
                            # latest computed one (review finding 4)
                            r["tilt"] = st.get("tilt_q", 0.0)
                            r["wbp"] = st.get("wbp")
                            r["wbw"] = st.get("wbw")
                        rows.append(r)
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
                # tilted: universe markets currently matched to a WB forecast
                # — a structural zero (cid mismatch, dead feed) must be
                # visible here, not days later (review finding 3)
                tilted = sum(1 for m2 in universe
                             if str(m2.get("cid") or "").lower() in WB_TILT)
                print("hb: " + " ".join(parts)
                      + f" | requote_lat_med={med:.0f}ms books={len(BOOKS)} stale_books={stale}"
                      + f" nobook={nobook} tilted={tilted}"
                      + f" http_hr={len(_http_window)}/{HTTP_BUDGET_PER_HOUR}",
                      flush=True)

        time.sleep(1)


def report(base):
    state = json.load(open(os.path.join(base, "state.json")))
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--base", default="/opt/pa2-maker-sim-v5")
    a = ap.parse_args()
    if a.report:
        report(a.base)
    elif a.run:
        sys.exit(run(a.base))
    else:
        print("need --run or --report")
