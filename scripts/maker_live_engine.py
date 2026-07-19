#!/usr/bin/env python3
"""MAKER LIVE ENGINE — the deployable maker trader. PAPER-FIRST.

Paper trading IS production (CLAUDE.md): the ENTIRE pipeline — discovery,
WS books, gates, quoting, the guard stack, ledgers, reward accrual — runs
IDENTICALLY in both modes; the only difference is the final submit step:

  MAKER_SUBMIT_MODE=paper   (default) approved orders are logged to the
                            order ledger and fills are simulated from the
                            public tape (family match semantics, y/n model)
  MAKER_SUBMIT_MODE=live    approved orders post to the CLOB via
                            py-clob-client-v2 (post-only GTC); fills come
                            from the authenticated trades feed

LIVE-MODE INTERLOCKS (ALL required, else the engine REFUSES TO START —
no silent fallback to paper; a mode mismatch must be loud):
  MAKER_SUBMIT_MODE=live
  MAKER_PK=<hex key of the operator-provisioned deposit-flow wallet>
  MAKER_LIVE_ACK=I-UNDERSTAND-REAL-MONEY
Trading must run from the VPS (eu-west-1 passes the geo gate, verified
2026-07-18; residential IPs are 403 for order submission — reads are fine).

SDK LANDMINE (verified 2026-07-18): the VPS exec venv contains BOTH
`py_clob_client` (0.34.6 — ARCHIVED, rejected by the CLOB since the Apr-28
V2 migration) and `py_clob_client_v2` (1.1.0 — verified working). Import
ONLY `py_clob_client_v2`. An `import py_clob_client` here is always a bug.

QUOTE STRUCTURE: two-sided liquidity = BUY YES at (center - s) plus BUY NO
at 1 - (center + s). Both legs are BUYs funded from pUSD (you cannot post a
YES ask without holding YES). Inventory is tracked per market as y/n token
quantities; min(y, n) pairs are worth exactly $1 (merge mechanics) and are
netted out of spend at match time. Net exposure = y - n on the YES scale.

PROVISIONAL DEFAULTS (config, NOT hardcoded policy):
  - gate policy default P0_base — the v5 gate-lab first read (~2026-07-20)
    picks the winner; switch via MAKER_GATE_POLICY, no code change.
  - quote style default "wide" (wide wins NET so far; touch earns more
    rewards — readout decides). Switch via MAKER_QUOTE_STYLE.
  - informed-flow sensor feed consumption is OFF by default (validation-
    first until ≥07-25); hook via MAKER_SENSOR_FEED=/path/informed_flow.jsonl.

GUARD STACK (single gateway choke-point, checked in this order):
  kill-switch → freshness → inventory caps (market / event / sector) →
  portfolio day-loss floor → liquidity (post-only never-cross) → execute →
  confirm.  Kill sequence is ALWAYS cancel-ALL-first-THEN-halt (latency =
  loss rate).  HALT persists across restarts (HALT file + state flag);
  the operator resumes by removing <base>/HALT.

Usage:  maker_live_engine.py --run    [--base /opt/pa2-maker-live]
        maker_live_engine.py --report [--base ...]
"""
import argparse
import atexit
import gzip
import json
import os
import random
import re
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "pa2-maker-live-engine/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
TAPE = "https://data-api.polymarket.com/trades?market={cid}&limit=200"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

DISCOVERY_EVERY_S = 1800
DISCOVERY_RETRY_S = 60
REQUOTE_TICKS = 0.002
MAX_DISK_MB = 500
HTTP_BUDGET_PER_HOUR = 36000
WS_CHUNK = 90
WS_IDLE_RECONNECT_S = 40
BATCH_MAX = 15                # official docs canon: batch-15 max per post
SENSOR_PULL_S = 600           # informed-flow event -> pull quotes this long
SENSOR_READ_CAP = 8 * 1024 * 1024

# gate-policy matrix — v5 gate-lab parameter sets (P5 control and P6 tilt are
# lab-only and deliberately absent here). Default P0_base is PROVISIONAL
# until the lab's first read (~2026-07-20) picks the winner.
POLICIES = {
    "P0_base":    {"vol_pts": 0.020, "vol_s": 600, "ramp_h": None, "tapevel": False},
    "P1_volfit":  {"vol_pts": 0.015, "vol_s": 900, "ramp_h": None, "tapevel": False},
    "P2_ramp":    {"vol_pts": 0.020, "vol_s": 600, "ramp_h": 9.0,  "tapevel": False},
    "P3_tapevel": {"vol_pts": 0.020, "vol_s": 600, "ramp_h": None, "tapevel": True},
    "P4_all":     {"vol_pts": 0.015, "vol_s": 900, "ramp_h": 9.0,  "tapevel": True},
}
LAST_HOURS_GATE_UTC = 19
TAPEVEL_PRINTS_5M = 8
TAPEVEL_MOVE_5M = 0.03
TAPEVEL_OFF_S = 600

LIVE_ACK_PHRASE = "I-UNDERSTAND-REAL-MONEY"


def load_config(env=None):
    """All knobs from env with paper-safe defaults. Raises ValueError on a
    bad live-mode interlock — the engine must never silently downgrade or
    upgrade its submit mode."""
    env = os.environ if env is None else env

    def f(key, dflt):
        try:
            return float(env.get(key, "") or dflt)
        except ValueError:
            raise ValueError(f"bad float for {key}: {env.get(key)!r}")

    mode = (env.get("MAKER_SUBMIT_MODE") or "paper").strip().lower()
    if mode not in ("paper", "live"):
        raise ValueError(f"MAKER_SUBMIT_MODE must be paper|live, got {mode!r}")
    if mode == "live":
        if not env.get("MAKER_PK"):
            raise ValueError("live mode requires MAKER_PK")
        if env.get("MAKER_LIVE_ACK") != LIVE_ACK_PHRASE:
            raise ValueError("live mode requires MAKER_LIVE_ACK=%s" % LIVE_ACK_PHRASE)
    policy = (env.get("MAKER_GATE_POLICY") or "P0_base").strip()
    if policy not in POLICIES:
        raise ValueError(f"MAKER_GATE_POLICY must be one of {sorted(POLICIES)}")
    style = (env.get("MAKER_QUOTE_STYLE") or "wide").strip().lower()
    if style not in ("wide", "touch"):
        raise ValueError("MAKER_QUOTE_STYLE must be wide|touch")
    excluded = set(s.strip().lower() for s in
                   (env.get("MAKER_EXCLUDED_SECTORS") or "esports,finance").split(",")
                   if s.strip())
    sector_caps = {}
    raw = env.get("MAKER_SECTOR_CAPS_USD")
    if raw:
        try:
            sector_caps = {str(k).lower(): float(v)
                           for k, v in json.loads(raw).items()}
        except Exception:
            raise ValueError(f"MAKER_SECTOR_CAPS_USD must be a JSON object: {raw!r}")
    out = {
        "mode": mode,
        "pk": env.get("MAKER_PK") or None,
        "funder": env.get("MAKER_FUNDER") or None,
        "sig_type": int(env["MAKER_SIG_TYPE"]) if env.get("MAKER_SIG_TYPE") else None,
        "policy": policy,
        "style": style,
        "max_markets": int(f("MAKER_MAX_MARKETS", 140)),
        "max_per_sector": int(f("MAKER_MAX_PER_SECTOR", 25)),
        "excluded_sectors": excluded,
        "inv_cap_mult": f("MAKER_INV_CAP_MULT", 3.0),
        "market_gross_cap": f("MAKER_MARKET_GROSS_CAP_USD", 150.0),
        "event_cap": f("MAKER_EVENT_CAP_USD", 200.0),
        "sector_gross_cap": f("MAKER_SECTOR_GROSS_CAP_USD", 600.0),
        "sector_caps": sector_caps,          # per-sector override of gross cap
        "day_loss_floor": f("MAKER_DAY_LOSS_FLOOR_USD", 75.0),
        "freshness_s": f("MAKER_FRESHNESS_S", 180.0),
        "size_jitter": f("MAKER_SIZE_JITTER", 0.20),
        "px_jitter_ticks": int(f("MAKER_PX_JITTER_TICKS", 1)),
        "rotation_frac": f("MAKER_ROTATION_FRAC", 0.0),
        "sensor_feed": env.get("MAKER_SENSOR_FEED") or None,
    }
    # bounds validation — a nonsense knob must fail LOUD at start, not
    # corrupt the money path at runtime (review finding 15: negative
    # size_jitter silently produced sub-min-size quotes that fill but
    # score ZERO rewards; negative px_jitter crashes the fast loop)
    if out["size_jitter"] < 0:
        raise ValueError("MAKER_SIZE_JITTER must be >= 0")
    if out["px_jitter_ticks"] < 0:
        raise ValueError("MAKER_PX_JITTER_TICKS must be >= 0")
    if out["freshness_s"] <= 0:
        raise ValueError("MAKER_FRESHNESS_S must be > 0")
    if not (0.0 <= out["rotation_frac"] < 1.0):
        raise ValueError("MAKER_ROTATION_FRAC must be in [0, 1)")
    for cap_key in ("inv_cap_mult", "market_gross_cap", "event_cap",
                    "sector_gross_cap", "day_loss_floor"):
        if out[cap_key] <= 0:
            raise ValueError(f"{cap_key} must be > 0")
    if any(v <= 0 for v in out["sector_caps"].values()):
        raise ValueError("MAKER_SECTOR_CAPS_USD values must be > 0 "
                         "(exclude a sector via MAKER_EXCLUDED_SECTORS)")
    if out["max_markets"] <= 0 or out["max_per_sector"] <= 0:
        raise ValueError("market count knobs must be > 0")
    return out


# ── shared quoting math (family semantics) ──────────────────────────────────
def S(v, s, size):
    return ((v - s) / v) ** 2 * size if v > 0 and 0 <= s < v else 0.0


def event_worst(poss, cost, covered, nout):
    """v6-verbatim guaranteed one-winner floor: min over possible winners w
    of (pos_w - total_cost); partial coverage keeps the conservative
    0-branch (an uncovered outcome can win and every covered YES pays 0)."""
    minw = min(poss) if poss else 0.0
    if covered < nout:
        minw = min(minw, 0.0)
    return minw - cost


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


# ── HTTP budget + fetchers (v5 family verbatim) ─────────────────────────────
_http_window = deque()


def http_ok():
    now = time.time()
    try:
        while _http_window and now - _http_window[0] > 3600:
            _http_window.popleft()
    except IndexError:
        pass    # tape threads race the deque; losing one prune is harmless
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
    try:
        return float(tr.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_tape(cid, since_ts):
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
    out.sort(key=ts_of)
    return out


def discover(base, cfg):
    """Rewarded-universe discovery (v5 semantics) + engine extras: excluded
    sectors filtered OUT (they are policy-excluded, don't spend WS/tape on
    them), event id for the per-event floor, tick size for price rounding."""
    rows, seen = [], set()
    dropped_excl = 0
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
            sec = sector_of(m)
            if sec in cfg["excluded_sectors"]:
                dropped_excl += 1
                continue
            try:
                tick = float(m.get("orderPriceMinTickSize") or 0) or 0.001
            except (TypeError, ValueError):
                tick = 0.001
            rows.append({"id": m.get("id"), "cid": m.get("conditionId"),
                         "q": (m.get("question") or "")[:70], "sector": sec,
                         "yes": str(toks[0]), "no": str(toks[1]), "v": v, "msz": msz,
                         "pool": pool, "tick": tick,
                         "neg_risk": bool(m.get("negRisk")),
                         "ev": str(m.get("negRiskMarketID") or "") or ("mkt:" + str(m.get("id"))),
                         "end": (m.get("endDate") or "")[:10],
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
        picked.extend(ms[:cfg["max_per_sector"]])
    picked.sort(key=lambda x: -x["pool"])
    picked = picked[:cfg["max_markets"]]
    if picked:
        with open(os.path.join(base, "universe.json"), "w") as f:
            json.dump({"t": time.time(), "markets": picked,
                       "dropped_excluded": dropped_excl}, f)
    return picked


# ── WebSocket book maintenance (v5/v3 verbatim, incl. batched fix) ──────────
BOOKS = {}
BOOKS_LOCK = threading.Lock()
GEN = {"n": 0}


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


# ── informed-flow sensor feed (optional; validation-first, default OFF) ─────
SENSOR_HOT = {}
_sensor = {"off": 0, "head": b""}


def load_sensor_feed(now, path):
    """Incremental read of informed_flow.jsonl -> SENSOR_HOT[cid] = until_ts.
    Same head-prefix / partial-line / shrink discipline as the WB reader."""
    try:
        sz = os.path.getsize(path)
    except OSError:
        return
    if sz < _sensor["off"]:
        _sensor["off"] = 0
    chunk = b""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
            if _sensor["off"] and not head.startswith(_sensor["head"]):
                _sensor["off"] = 0
            _sensor["head"] = head
            if sz > _sensor["off"]:
                fh.seek(_sensor["off"])
                chunk = fh.read(min(sz - _sensor["off"], SENSOR_READ_CAP))
    except OSError:
        return
    body, nl, _ = chunk.rpartition(b"\n")
    if nl:
        _sensor["off"] += len(body) + 1
        for line in body.split(b"\n"):
            try:
                d = json.loads(line)
                cid = str(d.get("market") or d.get("cid") or "").lower()
                t = float(d.get("t") or 0)
            except Exception:
                continue
            if cid and 0 < t <= now + 3600:
                SENSOR_HOT[cid] = max(SENSOR_HOT.get(cid, 0), t + SENSOR_PULL_S)
    for cid in [c for c, u in SENSOR_HOT.items() if u < now]:
        SENSOR_HOT.pop(cid, None)


# ── gates (v5 semantics, single policy) ─────────────────────────────────────
def gate(m, sh, st, pol, cfg, now, mid):
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
    if cfg.get("sensor_feed") and now < SENSOR_HOT.get(str(m.get("cid") or "").lower(), 0):
        return "sensor_hot"
    if now < st.get("pull_until", 0):
        return "vol_pull"
    return None


# ── guard stack ─────────────────────────────────────────────────────────────
class Guards:
    """Single choke-point checks. Pure state-in/verdict-out — unit-testable.
    Denials are counted by reason; the heartbeat surfaces them."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.denials = defaultdict(int)

    def deny(self, reason):
        self.denials[reason] += 1
        return False, reason

    def check_place(self, intent, m, st, state, uni_by_ev, now, sibling=None):
        """intent: {"leg": "yes"|"no", "px": float, "sz": float}. All our
        orders are BUYs (YES-bid leg or NO-bid leg). Checked in the
        handoff order: kill -> freshness -> caps -> liquidity.
        sibling: the already-approved other leg of THIS pair, so the
        second leg's caps see the first leg's cost (review finding 11)."""
        cfg = self.cfg
        meta = state.get("meta") or {}
        if meta.get("halted"):
            return self.deny("halted")
        # portfolio day floor: the kill fires on the minute loop; this deny
        # stops NEW exposure inside the minute (review finding 12)
        if meta.get("day_pnl", 0.0) < -cfg["day_loss_floor"]:
            return self.deny("day_floor")
        # freshness interlock, BOTH legs' books: the NO-leg order rests on
        # the NO book (review finding 16)
        _, _, yts = cached_touch(m["yes"])
        _, _, n_ts = cached_touch(m["no"])
        if yts is None or now - yts > cfg["freshness_s"] \
                or n_ts is None or now - n_ts > cfg["freshness_s"]:
            return self.deny("stale_book")
        cost = intent["px"] * intent["sz"]
        sib_cost = (sibling["px"] * sibling["sz"]) if sibling else 0.0
        y, n = st.get("y", 0.0), st.get("n", 0.0)
        pend_y = st.get("ob", {}).get("sz", 0.0) if st.get("ob") else 0.0
        pend_n = st.get("oa", {}).get("sz", 0.0) if st.get("oa") else 0.0
        if sibling:
            if sibling["leg"] == "yes":
                pend_y += sibling["sz"]
            else:
                pend_n += sibling["sz"]
        if intent["leg"] == "yes":
            pend_y += intent["sz"]
        else:
            pend_n += intent["sz"]
        net_after = (y + pend_y) - (n + pend_n)
        cap = cfg["inv_cap_mult"] * m["msz"]
        if abs(net_after) > cap + 1e-9 and abs(net_after) > abs(y - n):
            return self.deny("market_net_cap")
        pend_cost = (st.get("ob", {}) or {}).get("cost", 0.0) + \
                    (st.get("oa", {}) or {}).get("cost", 0.0)
        if st.get("spent", 0.0) + pend_cost + sib_cost + cost \
                > cfg["market_gross_cap"]:
            return self.deny("market_gross_cap")
        # sector gross over LIVE markets (departed markets are quarantined
        # out of the sum and reported separately — otherwise dead spend
        # strangles the sector forever, review finding 10)
        sec = m["sector"]
        sec_cap = cfg["sector_caps"].get(sec, cfg["sector_gross_cap"])
        sec_spent = sum(s2.get("spent", 0.0) for k2, s2 in state.items()
                        if isinstance(s2, dict) and s2.get("sector") == sec
                        and not s2.get("departed")
                        and not k2.startswith("meta"))
        if sec_spent + sib_cost + cost > sec_cap:
            return self.deny("sector_gross_cap")
        # per-event netted one-winner floor (v6 semantics on the y/n model)
        ok_ev, ev_reason = self._event_cap_ok(intent, m, state, uni_by_ev,
                                              sibling)
        if not ok_ev:
            return self.deny(ev_reason)
        # liquidity: post-only never-cross, belt (the post_only flag at the
        # submit step is the suspenders)
        if intent["leg"] == "yes":
            _, ya, _ = cached_touch(m["yes"])
            if ya is not None and intent["px"] >= ya:
                return self.deny("would_cross")
        else:
            _, na, _ = cached_touch(m["no"])
            if na is not None and intent["px"] >= na:
                return self.deny("would_cross")
        return True, None

    def _event_cap_ok(self, intent, m, state, uni_by_ev, sibling=None):
        cfg = self.cfg
        sibs = uni_by_ev.get(m["ev"], [m])
        poss, sum_n, cost = [], 0.0, 0.0
        covered = 0
        for sm in sibs:
            st2 = state.get(str(sm["id"])) or {}
            y2, n2 = st2.get("y", 0.0), st2.get("n", 0.0)
            if sm["id"] == m["id"]:
                if intent["leg"] == "yes":
                    y2 += intent["sz"]
                else:
                    n2 += intent["sz"]
                cost += intent["px"] * intent["sz"]
                if sibling:
                    if sibling["leg"] == "yes":
                        y2 += sibling["sz"]
                    else:
                        n2 += sibling["sz"]
                    cost += sibling["px"] * sibling["sz"]
            if y2 or n2 or st2.get("spent"):
                covered += 1
            poss.append(y2 - n2)
            sum_n += n2
            cost += st2.get("spent", 0.0)
        # departed same-event siblings still carry REAL one-winner exposure
        # (2nd-pass NEW-4: enumerating only the live universe let the floor
        # forget rotated-out inventory and re-load the surviving siblings)
        sib_ids = {str(sm["id"]) for sm in sibs}
        for k2, st2 in state.items():
            if k2 == "meta" or not isinstance(st2, dict) or k2 in sib_ids:
                continue
            if st2.get("ev") != m["ev"]:
                continue
            y2, n2 = st2.get("y", 0.0), st2.get("n", 0.0)
            if not (y2 or n2 or st2.get("spent")):
                continue
            poss.append(y2 - n2)
            sum_n += n2
            cost += st2.get("spent", 0.0)
            covered += 1
        # nout: winnable outcomes. Binary standalone market = 2 (YES/NO);
        # negRisk event = at least one sibling beyond what we see (universe
        # under-counts siblings) -> covered+1 forces the conservative
        # 0-branch. event_worst poss/cost mapping per the y/n derivation:
        # payout(w) = (y_w - n_w) + sum_n ; uncovered-winner payout = sum_n.
        nout = 2 if m["ev"].startswith("mkt:") else max(len(sibs), covered + 1)
        worst = event_worst(poss, cost - sum_n, covered, nout)
        if max(0.0, -worst) > cfg["event_cap"]:
            return False, "event_cap"
        return True, None

    def day_floor_breached(self, state, day_pnl):
        return day_pnl < -self.cfg["day_loss_floor"]


def parse_post_orders_resp(resp, n):
    """STRICT post_orders response contract: a list, 1:1 with what we
    posted, each item a dict carrying an order id (or a positive
    rejection). ANY other shape -> every leg marked ambiguous — the caller
    must assume the orders are LIVE (cancel-all + backoff). Never fabricate
    per-leg results by replication (review CRIT 4)."""
    if not isinstance(resp, list) or len(resp) != n:
        return [{"ok": False, "oid": None,
                 "err": "unexpected-response-shape: %s" % str(resp)[:150],
                 "ambiguous": True} for _ in range(n)]
    out = []
    for item in resp:
        oid = None
        ok = False
        if isinstance(item, dict):
            oid = item.get("orderID") or item.get("orderId") or item.get("id")
            ok = bool(item.get("success", oid is not None))
        if ok and oid:
            out.append({"ok": True, "oid": oid, "err": None})
        elif isinstance(item, dict) and not item.get("success", True):
            # positively rejected — safe, not live
            out.append({"ok": False, "oid": oid,
                        "err": str(item.get("errorMsg") or item)[:200]})
        else:
            out.append({"ok": False, "oid": oid,
                        "err": "unparseable item: %s" % str(item)[:150],
                        "ambiguous": True})
    return out


# ── execution core ──────────────────────────────────────────────────────────
class ExecCore:
    """The ONLY component that touches order submission. Paper and live take
    the identical call path up to _submit_batch/_cancel; paper logs, live
    posts via py_clob_client_v2 (post-only GTC).

    LIVE restart discipline: cancel_all() FIRST (standing orders from a
    previous run are unknown/stale), then trade-feed reconciliation."""

    def __init__(self, cfg, base):
        self.cfg = cfg
        self.base = base
        self.live = cfg["mode"] == "live"
        self.paper_seq = 0
        self.client = None
        self.address = None
        self._tickcache = {}
        if self.live:
            self._init_live()

    def _init_live(self):
        # V2 ONLY — py_clob_client (no _v2) is the archived pre-migration
        # client and is REJECTED by the CLOB. See module docstring.
        import httpx
        import py_clob_client_v2.http_helpers.helpers as _h
        from py_clob_client_v2.client import ClobClient
        # deliberate explicit timeout (httpx default is 5s; we pin 10s so a
        # hung POST can never wedge the quote loop indefinitely)
        _h._http_client = httpx.Client(http2=True, timeout=10.0)
        self.client = ClobClient(
            CLOB_HOST, CHAIN_ID, key=self.cfg["pk"],
            signature_type=self.cfg["sig_type"], funder=self.cfg["funder"])
        self.client.set_api_creds(self.client.create_or_derive_api_key())
        self.address = self.client.get_address()

    def market_meta(self, m, ttl_s=6 * 3600):
        """Authoritative tick/neg_risk for LIVE order building (probe-
        verified endpoints); paper trusts gamma's values. TTL'd cache —
        a permanently stale tick means order rejections + retry churn
        (review finding 13); prewarm() moves the bulk off the fast loop."""
        if not self.live:
            return str(m["tick"]), m["neg_risk"]
        got = self._tickcache.get(m["yes"])
        if got and time.time() - got[2] < ttl_s:
            return got[0], got[1]
        tick = str(self.client.get_tick_size(m["yes"]))
        neg = bool(self.client.get_neg_risk(m["yes"]))
        self._tickcache[m["yes"]] = (tick, neg, time.time())
        return tick, neg

    def prewarm_meta(self, universe):
        """Fetch tick/neg_risk for the whole universe at discovery time so
        the 1s quote loop never pays a cold-cache HTTP burst."""
        if not self.live:
            return
        for m in universe:
            try:
                self.market_meta(m)
            except Exception:
                continue

    def place_batch(self, placements):
        """placements: [{mkt, leg, tok, px, sz, tick, neg_risk}] pre-approved
        by the guard stack. Returns [{ok, oid, err}] aligned to input.
        Batches of <= BATCH_MAX (docs canon)."""
        out = []
        for i in range(0, len(placements), BATCH_MAX):
            chunk = placements[i:i + BATCH_MAX]
            if self.live:
                out.extend(self._submit_live(chunk))
            else:
                out.extend(self._submit_paper(chunk))
        return out

    def _submit_paper(self, chunk):
        res = []
        for p in chunk:
            self.paper_seq += 1
            res.append({"ok": True, "oid": "p%08d" % self.paper_seq, "err": None})
        return res

    def _submit_live(self, chunk):
        from py_clob_client_v2.clob_types import (OrderArgs, OrderType,
                                                  PostOrdersV2Args,
                                                  PartialCreateOrderOptions)
        from py_clob_client_v2.order_builder.constants import BUY
        args = []
        res = [{"ok": False, "oid": None, "err": "not-submitted"} for _ in chunk]
        built = []
        for j, p in enumerate(chunk):
            try:
                signed = self.client.create_order(
                    OrderArgs(token_id=p["tok"], price=p["px"],
                              size=p["sz"], side=BUY),
                    PartialCreateOrderOptions(tick_size=p["tick"],
                                              neg_risk=p["neg_risk"]))
                args.append(PostOrdersV2Args(order=signed,
                                             orderType=OrderType.GTC))
                built.append(j)
            except Exception as e:
                res[j] = {"ok": False, "oid": None, "err": repr(e)[:200]}
        if not args:
            return res
        try:
            resp = self.client.post_orders(args, post_only=True)
        except Exception as e:
            # a raised POST is AMBIGUOUS, not absent: the server may have
            # accepted the orders and the error hit on the response path
            # (review CRIT 4 — unknown must be treated as possibly-live)
            err = repr(e)[:200]
            for j in built:
                res[j] = {"ok": False, "oid": None, "err": err,
                          "ambiguous": True}
            return res
        parsed = parse_post_orders_resp(resp, len(built))
        for j, r in zip(built, parsed):
            res[j] = r
        return res

    def cancel(self, oids):
        """Returns True only on a clean cancel. Callers must NOT forget a
        failed cancel's oids — a live order the engine thinks is gone is a
        ghost resting at a stale price (review finding 5): track zombies."""
        oids = [o for o in oids if o]
        if not oids:
            return True
        if not self.live:
            return True
        try:
            self.client.cancel_orders(oids)
            return True
        except Exception:
            return False

    def cancel_all(self, attempts=3):
        """The kill primitive. Cancel-ALL comes FIRST in every halt path;
        retries fast because latency here is loss rate."""
        if not self.live:
            return True
        for i in range(attempts):
            try:
                self.client.cancel_all()
                return True
            except Exception:
                time.sleep(0.5 * (i + 1))
        return False

    def fetch_my_trades(self):
        """Live fill feed (authenticated). Returns None on FAILURE — the
        caller must distinguish a dead feed from an empty one: a silently
        broken feed blinds inventory, caps, and the loss floor while
        quoting continues (2nd-pass M1, the worst masking hole found)."""
        if not self.live:
            return []
        try:
            from py_clob_client_v2.clob_types import TradeParams
            return self.client.get_trades(
                TradeParams(maker_address=self.address)) or []
        except Exception:
            return None

    def earnings_for_day(self, date_iso):
        if not self.live:
            return None
        try:
            return self.client.get_earnings_for_user_for_day(date_iso)
        except Exception:
            return None


# ── quoting + reconciliation ────────────────────────────────────────────────
def desired_quote(m, mid, bb, ba, cfg, rng=random):
    """Two-sided quote on the YES scale. Returns (raw_bid, raw_ask,
    jit_bid, jit_ask, sz_b, sz_a) or None when no reward-scoring quote
    exists. Jitter (anti-landmark): size up to +size_jitter, price up to
    px_jitter_ticks toward mid (never out of band, never crossing —
    the guard stack + post_only re-verify)."""
    s_touch = (ba - bb) / 2
    s_mine = max(s_touch, m["v"] / 2) if cfg["style"] == "wide" else s_touch
    if S(m["v"], s_mine, m["msz"]) <= 0:
        return None
    raw_bid, raw_ask = mid - s_mine, mid + s_mine
    tick = m.get("tick") or 0.001
    jb = rng.randint(0, cfg["px_jitter_ticks"]) * tick if cfg["px_jitter_ticks"] else 0.0
    ja = rng.randint(0, cfg["px_jitter_ticks"]) * tick if cfg["px_jitter_ticks"] else 0.0
    jit_bid = round_tick(raw_bid + jb, tick, up=False)
    jit_ask = round_tick(raw_ask - ja, tick, up=True)
    if jit_bid >= jit_ask:            # <=2-tick book: no jitter room
        jit_bid = round_tick(raw_bid, tick, up=False)
        jit_ask = round_tick(raw_ask, tick, up=True)
        if jit_bid >= jit_ask:
            return None
    sz_b = round(m["msz"] * (1.0 + rng.uniform(0.0, cfg["size_jitter"])), 2)
    sz_a = round(m["msz"] * (1.0 + rng.uniform(0.0, cfg["size_jitter"])), 2)
    return raw_bid, raw_ask, jit_bid, jit_ask, sz_b, sz_a


def round_tick(px, tick, up):
    n = px / tick
    n = -(-n // 1) if up else n // 1
    out = round(n * tick, 6)
    return min(max(out, tick), 1.0 - tick)


def merge_pairs(st):
    """min(y, n) YES/NO pairs are worth exactly $1 each (merge mechanics);
    net them out of inventory and spend. Live-mode note: the on-chain merge
    is a pilot mechanic — until executed the pairs sit as locked collateral;
    the ledger nets them anyway (value identity) and tracks the pending
    total for the operator."""
    pairs = min(st.get("y", 0.0), st.get("n", 0.0))
    if pairs > 1e-9:
        st["y"] = round(st.get("y", 0.0) - pairs, 4)
        st["n"] = round(st.get("n", 0.0) - pairs, 4)
        st["spent"] = round(st.get("spent", 0.0) - pairs * 1.0, 4)
        st["merged"] = round(st.get("merged", 0.0) + pairs, 4)


def match_fills_paper(prints, qh, st, yes_asset, prev_ts):
    """Family fill model on the y/n structure: a YES-asset print at/below
    our standing YES bid buys us YES; at/above our standing ask leg buys us
    NO at (1 - ask). qh rows: [t, bid, ask, sz_b, sz_a] (None = pulled).
    Mutates st, returns (fills, max_ts)."""
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
        row = None
        for r in reversed(qh):
            if r[0] <= ts:
                row = r
                break
        if row is None or row[1] is None:
            continue
        _, qbid, qask, sz_b, sz_a = row
        if p < qbid:
            st["y"] = round(st.get("y", 0.0) + sz_b, 4)
            st["spent"] = round(st.get("spent", 0.0) + sz_b * qbid, 4)
            fills += 1
        elif qask is not None and p > qask:
            st["n"] = round(st.get("n", 0.0) + sz_a, 4)
            st["spent"] = round(st.get("spent", 0.0) + sz_a * (1.0 - qask), 4)
            fills += 1
        if fills:
            merge_pairs(st)
    return fills, max_ts


def write_halt(base, reason):
    """Persist the HALT file. Returns False on failure (e.g. full disk) —
    the caller must treat an unpersisted halt as fatal, because the
    auto-resume check is file-backed (review CRIT 1: a halt with no file
    would be silently 'resumed' one second later)."""
    try:
        with open(os.path.join(base, "HALT"), "w") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {reason}\n")
        return True
    except OSError:
        return False


def _backoff(st, now):
    """Exponential per-market retry backoff, 5s..300s (review finding 8:
    a persistent failure at the 1 Hz loop = API hammering / deny-ledger
    disk exhaustion). Reset to 0 on a successful two-sided placement."""
    st["backoff"] = min(300.0, max(5.0, st.get("backoff", 2.5) * 2))
    st["retry_after"] = now + st["backoff"]


def kill_sequence(execc, state, base, reason):
    """cancel-ALL first, THEN halt. ALWAYS this order — cancel latency is
    loss rate. Halt persists via HALT file + state flag; operator resumes
    by removing <base>/HALT. meta flags for the run loop:
      halt_cancel_ok=False -> loop retries cancel_all until it lands
      halt_unpersisted=True -> loop does a clean STOP (cannot guarantee
                               the halt survives, so don't keep running)"""
    ok = execc.cancel_all()
    if ok:
        _clear_zombies(state)
    for k in list(state):
        if k != "meta" and isinstance(state.get(k), dict):
            state[k]["ob"] = state[k]["oa"] = None
            qh = state[k].get("qh")
            if qh is not None:
                qh.append([time.time(), None, None, 0, 0])
    meta = state.setdefault("meta", {})
    meta["halted"] = True
    meta["halt_reason"] = reason
    meta["halt_cancel_ok"] = ok
    meta["halt_unpersisted"] = not write_halt(base, reason)
    print(f"KILL: cancel_all={'OK' if ok else 'FAILED'} then HALT — {reason}"
          + (" [HALT FILE WRITE FAILED — engine will clean-stop]"
             if meta["halt_unpersisted"] else ""),
          flush=True)
    ledger_write(base, "orders", {"t": round(time.time()), "act": "kill",
                                  "why": reason, "cancel_ok": ok})
    return ok


RESOLUTION_SWEEP_S = 3600           # departed-market outcome sweep cadence
RESOLUTION_LOOKUPS_PER_SWEEP = 30   # gamma lookups per sweep (budgeted)


def try_settle(st, prices, uma_resolved=False):
    """Settle a departed market at RESOLVED outcome prices (closes 2nd-pass
    M3: frozen marks were invisible to the day-loss floor forever).
    prices = [p_yes, p_no] floats. Acceptance (settlement-review finding
    2): a decisive 0/1 outcome (ε=1e-3) always settles; NON-decisive
    payout vectors (50/50 'no answer' splits etc.) settle ONLY when the
    row is UMA-final (uma_resolved) — the money math y·p_yes + n·p_no is
    exact for any payout vector, but without UMA finality a non-0/1
    vector could be a pre-resolution price, so we wait. On settle:
    inventory zeroed, spend netted so the market's portfolio contribution
    freezes at realized value — the mark→outcome jump hits day_pnl (and
    the floor) the day it settles. Returns the realized payout value."""
    try:
        py, pn = float(prices[0]), float(prices[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (0.0 <= py <= 1.0 and 0.0 <= pn <= 1.0
            and abs(py + pn - 1.0) < 1e-3):
        return None
    decisive = abs(py - round(py)) < 1e-3 and abs(pn - round(pn)) < 1e-3
    if not decisive and not uma_resolved:
        return None
    if decisive:
        py, pn = float(round(py)), float(round(pn))
    y, n = st.get("y", 0.0), st.get("n", 0.0)
    val = y * py + n * pn
    st["spent"] = round(st.get("spent", 0.0) - val, 4)
    st["y"] = st["n"] = 0.0
    st["last_mid"] = py
    st["settled"] = True
    return val


def resolution_sweep(state, base, now):
    """Hourly: look up departed-with-inventory markets on gamma and settle
    the resolved ones. Round-robin cursor so a long tail all gets visited;
    lookups go through get() and count against the HTTP budget."""
    meta = state.setdefault("meta", {})
    if now - meta.get("res_sweep_t", 0) < RESOLUTION_SWEEP_S:
        return 0
    meta["res_sweep_t"] = now
    cands = sorted(k for k, st in state.items()
                   if k != "meta" and isinstance(st, dict)
                   and st.get("departed") and not st.get("settled")
                   and (st.get("y") or st.get("n") or st.get("spent")))
    meta["res_pending"] = len(cands)   # heartbeat-visible: a stuck tail of
                                       # unresolvable departures must not
                                       # be silent (review finding 2)
    if not cands:
        return 0
    cur = meta.get("res_sweep_cursor", 0) % len(cands)
    batch = (cands[cur:] + cands[:cur])[:RESOLUTION_LOOKUPS_PER_SWEEP]
    meta["res_sweep_cursor"] = (cur + len(batch)) % len(cands)
    settled = 0
    for k in batch:
        st = state[k]
        # PATH form, not ?id= — the id query param EXCLUDES closed markets
        # by default, i.e. exactly the sweep's targets returned empty rows
        # forever (caught live 2026-07-19: respend stuck at 15 while gamma
        # had the resolutions). The path form returns a dict.
        rows = get(f"{GAMMA}/{urllib.parse.quote(str(k))}")
        m = rows if isinstance(rows, dict) else \
            (rows[0] if isinstance(rows, list) and rows
             and isinstance(rows[0], dict) else None)
        # the row must be THIS market — settling k at an unrelated row's
        # outcome would be a permanent wrong realize (review finding 4)
        if not m or str(m.get("id")) != str(k) or not m.get("closed"):
            continue
        uma = str(m.get("umaResolutionStatus") or "").lower()
        if uma and uma != "resolved":
            continue                   # closed but UMA not final: wait
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
        except Exception:
            continue
        pre_spent = st.get("spent", 0.0)
        val = try_settle(st, prices, uma_resolved=(uma == "resolved"))
        if val is None:
            continue
        settled += 1
        meta["settle_realized_day"] = round(
            meta.get("settle_realized_day", 0.0) + (val - pre_spent), 4)
        ledger_write(base, "settlements",
                     {"t": round(now), "mkt": k, "payout": round(val, 4),
                      "realized": round(val - pre_spent, 4),
                      "uma": uma[:20]})
    meta["res_pending"] = len(cands) - settled
    if settled:
        print(f"resolution sweep: settled {settled}/{len(batch)} looked-up "
              f"({len(cands) - settled} departed pending)", flush=True)
    return settled


def portfolio_net(state):
    """Mark-to-mid portfolio value minus spend, over ALL markets (departed
    keep their last mid — frozen marks are reported, not hidden)."""
    net = 0.0
    for k, st in state.items():
        if k == "meta" or not isinstance(st, dict):
            continue
        lm = st.get("last_mid")
        y, n = st.get("y", 0.0), st.get("n", 0.0)
        if lm is None:
            val = 0.0 if (y == 0 and n == 0) else y * 0.5 + n * 0.5
        else:
            val = y * lm + n * (1.0 - lm)
        net += val - st.get("spent", 0.0)
    return net


# ── engine ──────────────────────────────────────────────────────────────────
_SIG = {"stop": False}


def _sigterm(_sig, _frm):
    _SIG["stop"] = True


_LEDGER_MISS = {"n": 0}


def ledger_write(base, name, row):
    """Best-effort: a full disk must NEVER crash the engine mid-kill (2nd-
    pass NEW-2: an ENOSPC raise out of kill_sequence resurrected the
    unpersisted-halt hole). Misses are counted and heartbeat-surfaced."""
    try:
        day = time.strftime("%Y%m%d", time.gmtime())
        with open(os.path.join(base, f"{name}-{day}.jsonl"), "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        _LEDGER_MISS["n"] += 1


def _clear_zombies(state):
    """After ANY successful cancel_all no order survives server-side —
    every zombie list is moot. Leaving them set kept markets barred and
    let the minute retry escalate into a collateral universe-wide
    cancel_all (2nd-pass NEW-3)."""
    for k, s2 in state.items():
        if k != "meta" and isinstance(s2, dict) and s2.get("zombies"):
            s2["zombies"] = []
    m = state.get("meta")
    if isinstance(m, dict):
        m["zombie_fail_n"] = 0


def run(base, cfg):
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)   # operator Ctrl-C must also
                                             # cancel quotes before exit
    pol = POLICIES[cfg["policy"]]
    guards = Guards(cfg)
    execc = ExecCore(cfg, base)

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
                print("STATE .bak ALSO FAILED — EMPTY ledgers (era note!)", flush=True)
    meta = state.setdefault("meta", {})
    now0 = time.time()
    meta["era"] = now0
    # crash-path cleanup (2nd-pass NEW-2): on ANY unhandled exception the
    # process must still cancel live orders and persist state (incl. a
    # halted flag set moments earlier) before exiting. atexit runs on
    # unhandled-exception shutdown; SIGTERM/SIGINT have their own path.
    def _final_cleanup():
        try:
            execc.cancel_all(attempts=1)
        except Exception:
            pass
        try:
            _save_state(base, os.path.join(base, "state.json"), state)
        except Exception:
            pass
    atexit.register(_final_cleanup)
    # ── startup normalization (v5 discipline, review findings 9 + 14) ──
    # Quotes do NOT survive the process: standing paper quotes would become
    # zombies that accrue rewards but can never fill (qh is not persisted);
    # standing live quotes are cancel_all'd below anyway. And never credit
    # accrual across a downtime gap.
    for _k, _v in state.items():
        if _k != "meta" and isinstance(_v, dict):
            _v["ob"] = _v["oa"] = None
            _v["want_raw"] = None
            if "last_acc_t" in _v:
                _v["last_acc_t"] = now0
    if os.path.exists(os.path.join(base, "HALT")):
        meta["halted"] = True
    elif meta.get("halted"):
        # state says halted but the file is gone (crash before write, manual
        # state surgery): RE-ARM the file — a halt may only ever be lifted
        # by the operator removing the file while the engine is running
        if not write_halt(base, meta.get("halt_reason") or "re-armed at boot"):
            print("HALT re-arm write FAILED — clean stop", flush=True)
            return 0
    meta["halt_unpersisted"] = False
    print(f"ENGINE START mode={cfg['mode'].upper()} policy={cfg['policy']} "
          f"style={cfg['style']} max_mkts={cfg['max_markets']} "
          f"excluded={sorted(cfg['excluded_sectors'])} "
          f"floor=${cfg['day_loss_floor']:.0f} halted={meta.get('halted', False)}",
          flush=True)
    if execc.live:
        # restart discipline: unknown standing orders are cancelled before
        # the first quote goes out
        ok = execc.cancel_all()
        if ok:
            _clear_zombies(state)   # nothing survives a cancel_all
        print(f"live start: cancel_all {'OK' if ok else 'FAILED (halting)'}",
              flush=True)
        if not ok:
            # a real halt, file-backed (review CRIT 1: the flag alone would
            # be auto-'resumed' by the file-absence check one second later)
            meta["halted"] = True
            meta["halt_reason"] = "startup cancel_all failed"
            meta["halt_cancel_ok"] = False
            if not write_halt(base, meta["halt_reason"]):
                print("HALT write failed too — clean stop", flush=True)
                return 0
        if "trade_wm" not in meta:
            # first live boot: TIMESTAMP watermark, not an id set (review
            # CRIT 3: an unordered id set with eviction re-ingests old
            # trades as new fills once history exceeds the cap). Only
            # trades matched after this instant are this engine's fills.
            pre = execc.fetch_my_trades()
            wm = now0
            for t in pre if isinstance(pre, list) else []:
                if isinstance(t, dict):
                    wm = max(wm, _trade_ts(t))
            meta["trade_wm"] = wm
            # pre-seed the recent-id map with everything inside the grace
            # lap, so historical trades near the watermark can't re-ingest
            meta["trade_recent"] = {
                str(t.get("id") or t.get("trade_id") or ""): _trade_ts(t)
                for t in (pre if isinstance(pre, list) else [])
                if isinstance(t, dict) and _trade_ts(t) >= wm - TRADE_LAP_S
                and (t.get("id") or t.get("trade_id"))}
            print(f"live start: trade watermark {wm:.0f} "
                  f"({len(pre) if isinstance(pre, list) else 0} historical, "
                  f"{len(meta['trade_recent'])} in-lap seeded)", flush=True)

    # anchors + timers
    day_key = time.strftime("%Y%m%d", time.gmtime(now0))
    if meta.get("day") != day_key:
        yday_boot = meta.get("day")
        prev_acc_boot = meta.get("acc_day", 0.0)
        meta["day"] = day_key
        meta["day_anchor"] = portfolio_net(state)
        meta["acc_day"] = 0.0
        meta["day_pnl"] = 0.0    # stale yesterday's pnl would wrongly deny
                                 # placement for the first minute (NEW-6)
        meta["settle_realized_day"] = 0.0
        if execc.live and yday_boot:
            # cross-midnight restart: don't lose yesterday's model-vs-
            # receipt comparison (review finding 22)
            d_iso = f"{yday_boot[:4]}-{yday_boot[4:6]}-{yday_boot[6:]}"
            rec = execc.earnings_for_day(d_iso)
            ledger_write(base, "receipts",
                         {"t": round(now0), "day": d_iso, "model": prev_acc_boot,
                          "receipt": rec, "note": "fetched at boot rollover"})
    universe = []
    uni_by_ev = {}
    last_discovery = 0.0
    last_minute = 0.0
    last_disk = 0.0
    hb = time.time()
    minute_n = 0

    def st_of(mid_key):
        return state.setdefault(str(mid_key), {})

    def cancel_market_quotes(m_or_id, reason, now_ts=None):
        """Cancel a market's standing pair. A FAILED live cancel keeps the
        oids as zombies (review finding 5): the market is barred from new
        quotes until the zombie cancel lands (minute-loop retry)."""
        t = now_ts if now_ts is not None else time.time()
        st = st_of(m_or_id if isinstance(m_or_id, str) else str(m_or_id["id"]))
        oids = [(st.get(x) or {}).get("oid") for x in ("ob", "oa")]
        oids = [o for o in oids if o]
        if oids:
            ok = execc.cancel(oids)
            if not ok:
                st["zombies"] = (st.get("zombies") or []) + oids
                guards.denials["cancel_failed"] += 1
            ledger_write(base, "orders", {"t": round(t), "act": "cancel",
                                          "mkt": st.get("gid"), "oids": oids,
                                          "ok": ok, "why": reason})
        st["ob"] = st["oa"] = None
        st["want_raw"] = None
        qh = st.get("qh")
        if qh is not None:
            qh.append([t, None, None, 0, 0])

    while True:
        now = time.time()
        if _SIG["stop"] or os.path.exists(os.path.join(base, "STOP")):
            print("STOP — cancelling quotes, saving, exiting cleanly", flush=True)
            execc.cancel_all()
            _save_state(base, state_path, state)
            return 0
        if meta.get("halt_unpersisted"):
            # halt could not be made durable (full disk?) — do not keep
            # running on an in-memory-only halt (review CRIT 1)
            print("halt unpersisted — clean stop after final cancel_all", flush=True)
            execc.cancel_all()
            _save_state(base, state_path, state)
            return 0
        if meta.get("halted") and meta.get("halt_cancel_ok") is False \
                and now - meta.get("halt_cancel_last", 0) > 5:
            # a halt with live orders still standing is the state the kill
            # exists to prevent — keep retrying until the cancel LANDS
            # (review finding 6)
            meta["halt_cancel_last"] = now
            if execc.cancel_all(attempts=1):
                meta["halt_cancel_ok"] = True
                _clear_zombies(state)
                print("halted: retried cancel_all — LANDED", flush=True)
        # operator resume: HALT file removed while halted flag set. Only a
        # file-backed halt is resumable (the file existing at some point is
        # guaranteed by kill_sequence/boot re-arm; unpersisted halts exited
        # above)
        if meta.get("halted") and not os.path.exists(os.path.join(base, "HALT")):
            if meta.get("halt_cancel_ok") is False:
                print("HALT file removed but cancel_all never landed — "
                      "NOT resuming; re-arming HALT", flush=True)
                write_halt(base, "un-cancelled orders outstanding")
            else:
                meta["halted"] = False
                meta["halt_reason"] = None
                print("HALT file removed — RESUMING (era note)", flush=True)

        if now - last_disk > 3600:
            last_disk = now
            today = time.strftime("%Y%m%d", time.gmtime(now))
            for old in [p for p in os.listdir(base)
                        if p.split("-")[0] in ("orders", "fills", "samples")
                        and p.endswith(".jsonl") and today not in p]:
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
                print(f"disk cap exceeded ({size_mb:.0f}MB) — CLEAN STOP", flush=True)
                execc.cancel_all()
                _save_state(base, state_path, state)
                return 0

        # ── discovery + WS lifecycle + pool-vanished auto-unquote ───────────
        if now - last_discovery > (DISCOVERY_EVERY_S if universe else DISCOVERY_RETRY_S):
            u = discover(base, cfg)
            if u and universe and len(u) < max(40, len(universe) // 2):
                print(f"discovery PARTIAL ({len(u)} vs {len(universe)}) — keeping",
                      flush=True)
                u = None
            if u:
                old_ids = set(str(m["id"]) for m in universe)
                new_ids = set(str(m["id"]) for m in u)
                for gone in old_ids - new_ids:
                    st = state.get(gone) or {}
                    if st.get("ob") or st.get("oa"):
                        cancel_market_quotes(gone, "pool_vanished", now)
                        guards.denials["pool_vanished"] += 1
                    if isinstance(st, dict) and st:
                        # quarantine: departed spend must not strangle the
                        # sector cap forever (review finding 10); frozen
                        # marks are reported in --report's departed bucket
                        st["departed"] = True
                universe = u
                # departure marking from STATE, not the in-memory old
                # universe: at boot the old universe is [] and markets that
                # departed while the engine was DOWN would otherwise never
                # be marked — never swept/settled, dead spend strangling
                # the sector cap (settlement-review finding 1, the restart
                # regression of both M3 and finding 10)
                for k2, s2 in state.items():
                    if k2 == "meta" or not isinstance(s2, dict) \
                            or k2 in new_ids or s2.get("departed"):
                        continue
                    if s2.get("y") or s2.get("n") or s2.get("spent") \
                            or s2.get("tok_y"):
                        s2["departed"] = True
                uni_by_ev = defaultdict(list)
                for m in universe:
                    uni_by_ev[m["ev"]].append(m)
                GEN["n"] += 1
                gen = GEN["n"]
                assets = []
                for m in universe:
                    assets.extend((m["yes"], m["no"]))
                keep = set(assets)
                with BOOKS_LOCK:
                    for dead in [a for a in BOOKS if a not in keep]:
                        BOOKS.pop(dead, None)
                nthreads = 0
                for i in range(0, len(assets), WS_CHUNK):
                    threading.Thread(target=ws_worker,
                                     args=(assets[i:i + WS_CHUNK], gen),
                                     daemon=True).start()
                    nthreads += 1
                print(f"universe: {len(universe)} markets, {len(assets)} assets, "
                      f"{nthreads} ws conns (gen {gen})", flush=True)
                # prewarm in a BACKGROUND thread: serial on the engine
                # thread stalled the fast loop for minutes at TTL rollover
                # while live quotes rested unattended (2nd-pass NEW-5);
                # _tickcache writes are GIL-atomic dict assignments
                threading.Thread(target=execc.prewarm_meta,
                                 args=(list(universe),), daemon=True).start()
                # hourly rotation set (anti-landmark; default frac 0 = off)
                meta["rot_until"] = 0
            last_discovery = now

        if cfg["rotation_frac"] > 0 and now > meta.get("rot_until", 0) and universe:
            k = int(len(universe) * cfg["rotation_frac"])
            meta["rot_skip"] = random.sample([str(m["id"]) for m in universe], k) if k else []
            meta["rot_until"] = now + 3600
        rot_skip = set(meta.get("rot_skip") or [])

        # ── fast loop: gates -> desired quotes -> gateway -> submit ─────────
        placements = []
        pl_meta = []
        for m in universe:
            key = str(m["id"])
            st = st_of(key)
            st["gid"] = key
            st["sector"] = m["sector"]
            st["msz"] = m["msz"]
            st["tok_y"], st["tok_n"] = m["yes"], m["no"]   # persistent map
            st["ev"] = m["ev"]        # persisted: event floor must see
                                      # departed siblings (2nd-pass NEW-4)
            st["departed"] = False
            bb, ba, bts = cached_touch(m["yes"])
            _, _, nts = cached_touch(m["no"])
            fresh = (bb is not None and ba is not None and 0 < bb < ba <= 1
                     and bts is not None and now - bts <= cfg["freshness_s"]
                     and nts is not None and now - nts <= cfg["freshness_s"])
            if not fresh:
                # standing quotes must NOT rest on a book the engine can no
                # longer see — the hysteresis path previously bypassed the
                # freshness interlock entirely (review finding 7; NO-book
                # staleness is finding 16)
                if st.get("ob") or st.get("oa"):
                    cancel_market_quotes(key, "stale_book", now)
                continue
            mid = (bb + ba) / 2
            mh = st.setdefault("mid_hist", [])
            mh.append([now, mid])
            while mh and now - mh[0][0] > 330:
                mh.pop(0)
            st["last_mid"] = mid
            st["mid_t"] = now
            if abs(mid - mh[0][1]) >= TAPEVEL_MOVE_5M and now - mh[0][0] >= 60:
                st["hot_until"] = now + TAPEVEL_OFF_S
            vol_ref = None
            for t0, m0 in mh:
                if now - t0 <= 150:
                    vol_ref = (t0, m0)
                    break
            if pol["vol_pts"] is not None and vol_ref is not None:
                t0, m0 = vol_ref
                if now - t0 >= 60 and abs(mid - m0) > pol["vol_pts"]:
                    st["pull_until"] = now + pol["vol_s"]

            g = gate(m, st, st, pol, cfg, now, mid)
            if g is None and meta.get("halted"):
                g = "halted"
            if g is None and key in rot_skip:
                g = "rotation"
            if g is not None:
                if st.get("ob") or st.get("oa"):
                    cancel_market_quotes(key, g, now)
                st["gates"] = {**st.get("gates", {}),
                               g: st.get("gates", {}).get(g, 0) + 1}
                continue
            if st.get("zombies"):
                # un-cancelled live orders outstanding: no new quotes here
                # until the zombie cancel lands (review finding 5)
                guards.denials["zombie_pending"] += 1
                continue
            if now < st.get("retry_after", 0.0):
                continue          # failure backoff (review finding 8)

            want = desired_quote(m, mid, bb, ba, cfg)
            if want is None:
                if st.get("ob") or st.get("oa"):
                    cancel_market_quotes(key, "no_scoring_quote", now)
                continue
            raw_bid, raw_ask, jit_bid, jit_ask, sz_b, sz_a = want
            cur = st.get("want_raw")
            if cur is not None and st.get("ob") and st.get("oa") \
                    and abs(raw_bid - cur[0]) < REQUOTE_TICKS \
                    and abs(raw_ask - cur[1]) < REQUOTE_TICKS:
                continue                     # hysteresis: standing quote holds
            # requote = cancel standing, place fresh (no native replace in V2)
            if st.get("ob") or st.get("oa"):
                cancel_market_quotes(key, "requote", now)
                if st.get("zombies"):
                    continue              # requote-cancel failed: bar market
            tick_s, neg = (str(m["tick"]), m["neg_risk"])
            if execc.live:
                try:
                    tick_s, neg = execc.market_meta(m)
                except Exception:
                    guards.denials["meta_fetch"] += 1
                    _backoff(st, now)
                    continue
            approved = []
            deny_why = None
            for leg, px, sz, tok in (
                    ("yes", jit_bid, sz_b, m["yes"]),
                    ("no", round(1.0 - jit_ask, 6), sz_a, m["no"])):
                # the second leg's caps must see the first leg's cost —
                # per-leg-only checks let the PAIR overshoot gross/event
                # caps by a whole leg (review finding 11)
                sib = approved[0] if approved else None
                ok, why = guards.check_place({"leg": leg, "px": px, "sz": sz},
                                             m, st, state, uni_by_ev, now,
                                             sibling=sib)
                if ok:
                    approved.append({"mkt": key, "leg": leg, "tok": tok,
                                     "px": px, "sz": sz, "tick": tick_s,
                                     "neg_risk": neg})
                else:
                    deny_why = why
            # two-sided or nothing: a single-legged quote scores zero
            # (two-sided MIN) and doubles adverse exposure
            if len(approved) == 2:
                placements.extend(approved)
                pl_meta.append((key, raw_bid, raw_ask, jit_bid, jit_ask, sz_b, sz_a))
            else:
                st["want_raw"] = None
                _backoff(st, now)         # denial churn control (finding 8)
                if now - st.get("last_deny_log", 0) > 60:
                    st["last_deny_log"] = now
                    ledger_write(base, "orders",
                                 {"t": round(now), "act": "deny", "mkt": key,
                                  "why": deny_why,
                                  "backoff_s": round(st.get("backoff", 0), 1)})

        if placements:
            results = execc.place_batch(placements)
            # ANY ambiguous result means orders may be live with ids we do
            # not hold: the only safe recovery is cancel-ALL (which also
            # voids every good standing quote — null them so they requote)
            # + backoff (review CRIT 4)
            if any(r.get("ambiguous") for r in results):
                guards.denials["ambiguous_response"] += 1
                ok = execc.cancel_all()
                if ok:
                    _clear_zombies(state)
                for k2 in list(state):
                    if k2 != "meta" and isinstance(state.get(k2), dict):
                        st2 = state[k2]
                        if st2.get("ob") or st2.get("oa"):
                            st2["ob"] = st2["oa"] = None
                            st2["want_raw"] = None
                            qh2 = st2.get("qh")
                            if qh2 is not None:
                                qh2.append([now, None, None, 0, 0])
                for key, *_rest in pl_meta:
                    _backoff(st_of(key), now)
                ledger_write(base, "orders",
                             {"t": round(now), "act": "ambiguous_recovery",
                              "cancel_all_ok": ok,
                              "errs": [r["err"] for r in results
                                       if r.get("ambiguous")][:3]})
                if not ok:
                    kill_sequence(execc, state, base,
                                  "ambiguous placement + cancel_all failed")
                results = None
            if results is not None:
                by_mkt = defaultdict(list)
                for p, r in zip(placements, results):
                    by_mkt[p["mkt"]].append((p, r))
                for key, raw_bid, raw_ask, jit_bid, jit_ask, sz_b, sz_a in pl_meta:
                    st = st_of(key)
                    pair = by_mkt.get(key, [])
                    if len(pair) == 2 and all(r["ok"] for _, r in pair):
                        legs = {p["leg"]: (p, r) for p, r in pair}
                        yb, nb = legs["yes"], legs["no"]
                        st["ob"] = {"oid": yb[1]["oid"], "px": yb[0]["px"],
                                    "sz": yb[0]["sz"],
                                    "cost": yb[0]["px"] * yb[0]["sz"]}
                        st["oa"] = {"oid": nb[1]["oid"], "px": nb[0]["px"],
                                    "sz": nb[0]["sz"],
                                    "cost": nb[0]["px"] * nb[0]["sz"]}
                        st["want_raw"] = [raw_bid, raw_ask]
                        st["backoff"] = 0.0
                        qh = st.setdefault("qh", [])
                        qh.append([now, jit_bid, jit_ask, sz_b, sz_a])
                        if len(qh) > 400:
                            del qh[:len(qh) - 400]
                        for p, r in pair:
                            ledger_write(base, "orders",
                                         {"t": round(now), "act": "place",
                                          "mkt": key, "leg": p["leg"],
                                          "px": p["px"], "sz": p["sz"],
                                          "oid": r["oid"]})
                    else:
                        # partial acceptance = one-sided exposure: cancel the
                        # survivor; a FAILED survivor-cancel leaves a zombie
                        oids = [r["oid"] for _, r in pair if r["ok"] and r["oid"]]
                        if oids and not execc.cancel(oids):
                            st["zombies"] = (st.get("zombies") or []) + oids
                            guards.denials["cancel_failed"] += 1
                        st["ob"] = st["oa"] = None
                        st["want_raw"] = None
                        guards.denials["partial_place"] += 1
                        _backoff(st, now)
                        ledger_write(base, "orders",
                                     {"t": round(now), "act": "place_failed",
                                      "mkt": key,
                                      "errs": [r["err"] for _, r in pair
                                               if not r["ok"]]})

        # ── minute loop: fills, accrual, floors, persistence, heartbeat ─────
        if now - last_minute >= 60:
            last_minute = now
            minute_n += 1
            if cfg["sensor_feed"]:
                load_sensor_feed(now, cfg["sensor_feed"])

            # fills
            if execc.live:
                _apply_live_trades(execc, state, universe, base, meta)
                # fill-feed integrity kills (2nd-pass M1/M2): a blind or
                # drifted feed means caps and the loss floor are fiction
                if not meta.get("halted"):
                    if meta.get("feed_fail_n", 0) >= 10:
                        kill_sequence(execc, state, base,
                                      "trade feed dead %d consecutive minutes"
                                      % meta["feed_fail_n"])
                    elif meta.get("unmatched_fills_n", 0) >= 5:
                        kill_sequence(execc, state, base,
                                      "%d unmatched own-address fills — "
                                      "feed schema drift?"
                                      % meta["unmatched_fills_n"])
            else:
                from concurrent.futures import ThreadPoolExecutor
                tape_cache = {}
                need = [m for m in universe if m.get("cid")]
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futs = {ex.submit(fetch_tape, m["cid"],
                                      (state.get(str(m["id"])) or {}).get(
                                          "last_trade_ts", now - 60)): str(m["id"])
                            for m in need}
                    for fu in futs:
                        try:
                            tape_cache[futs[fu]] = fu.result(timeout=45) or []
                        except Exception:
                            tape_cache[futs[fu]] = []
                for m in universe:
                    key = str(m["id"])
                    st = st_of(key)
                    tape = tape_cache.get(key) or []
                    prev_ts = st.get("last_trade_ts", now - 60)
                    ph = st.setdefault("prints_hist", [])
                    n_new = sum(1 for tr in tape
                                if isinstance(tr, dict) and ts_of(tr) > prev_ts)
                    ph.append([now, n_new])
                    while ph and now - ph[0][0] > 300:
                        ph.pop(0)
                    if sum(c for _, c in ph) >= TAPEVEL_PRINTS_5M:
                        st["hot_until"] = now + TAPEVEL_OFF_S
                    max_ts = prev_ts
                    if st.get("qh"):
                        fills, max_ts = match_fills_paper(
                            tape, st["qh"], st, m["yes"], prev_ts)
                        if fills:
                            ledger_write(base, "fills",
                                         {"t": round(now), "mkt": key, "n": fills,
                                          "y": st.get("y", 0.0), "nn": st.get("n", 0.0),
                                          "spent": st.get("spent", 0.0)})
                    for tr in tape if isinstance(tape, list) else []:
                        max_ts = max(max_ts, ts_of(tr))
                    st["last_trade_ts"] = max_ts

            # reward accrual model (two-sided MIN — real-program semantics)
            for m in universe:
                st = st_of(str(m["id"]))
                mid = st.get("last_mid")
                dt = min(now - st.get("last_acc_t", now - 60), 120)
                st["last_acc_t"] = now
                if mid is None:
                    continue
                books_live = (now - st.get("mid_t", 0)) < cfg["freshness_s"]
                ob, oa = st.get("ob"), st.get("oa")
                if ob and oa and books_live:
                    q_mine = min(S(m["v"], max(0.0, mid - ob["px"]), ob["sz"]),
                                 S(m["v"], max(0.0, (1.0 - oa["px"]) - mid), oa["sz"]))
                    if q_mine > 0:
                        q1, q2 = cached_scores(m, mid)
                        if execc.live:
                            # in live mode our own resting orders are IN the
                            # WS book — remove our contribution from the
                            # competition sums or share is systematically
                            # underestimated vs paper (review finding 21):
                            # YES bid sits in q1's yes-bids leg; NO bid sits
                            # in q2's no-bids leg (center 1-mid)
                            q1 = max(0.0, q1 - S(m["v"], abs(mid - ob["px"]),
                                                 ob["sz"]))
                            q2 = max(0.0, q2 - S(m["v"],
                                                 abs(oa["px"] - (1.0 - mid)),
                                                 oa["sz"]))
                        q_comp = max(min(q1, q2), max(q1, q2) / 3.0) \
                            if 0.10 <= mid <= 0.90 else min(q1, q2)
                        share = q_mine / (q_mine + q_comp) if q_comp >= 0 else 0.0
                        inc = share * m["pool"] * dt / 86400.0
                        st["acc"] = round(st.get("acc", 0.0) + inc, 6)
                        meta["acc_day"] = round(meta.get("acc_day", 0.0) + inc, 6)

            # day rollover + receipts (live)
            day_now = time.strftime("%Y%m%d", time.gmtime(now))
            if day_now != meta.get("day"):
                yday = meta.get("day")
                meta["day"] = day_now
                prev_acc = meta.get("acc_day", 0.0)
                meta["day_anchor"] = portfolio_net(state)
                meta["acc_day"] = 0.0
                meta["settle_realized_day"] = 0.0
                if execc.live and yday:
                    d_iso = f"{yday[:4]}-{yday[4:6]}-{yday[6:]}"
                    rec = execc.earnings_for_day(d_iso)
                    ledger_write(base, "receipts",
                                 {"t": round(now), "day": d_iso, "model": prev_acc,
                                  "receipt": rec})
                    print(f"receipts {d_iso}: model=${prev_acc:.2f} raw={str(rec)[:200]}",
                          flush=True)

            # departed-market resolution backfill (2nd-pass M3 closure);
            # save immediately on any settle — a crash before the regular
            # minute-end save would duplicate settlement ledger rows
            # (review finding 7)
            if resolution_sweep(state, base, now):
                _save_state(base, state_path, state)

            # zombie cancel retries (failed cancels from the fast loop);
            # escalate to cancel_all, then kill, if they keep failing
            zmkts = [k2 for k2, s2 in state.items()
                     if k2 != "meta" and isinstance(s2, dict)
                     and s2.get("zombies")]
            if zmkts:
                allz = [o for k2 in zmkts for o in state[k2]["zombies"]]
                if execc.cancel(allz):
                    for k2 in zmkts:
                        state[k2]["zombies"] = []
                    meta["zombie_fail_n"] = 0
                    print(f"zombie cancel landed ({len(allz)} oids)", flush=True)
                else:
                    meta["zombie_fail_n"] = meta.get("zombie_fail_n", 0) + 1
                    if meta["zombie_fail_n"] >= 5:
                        if execc.cancel_all():
                            _clear_zombies(state)
                            for k2, s2 in state.items():
                                if k2 != "meta" and isinstance(s2, dict):
                                    s2["ob"] = s2["oa"] = None
                                    s2["want_raw"] = None
                            print("zombie escalation: cancel_all landed "
                                  "(all standing quotes voided)", flush=True)
                        else:
                            kill_sequence(execc, state, base,
                                          "zombie cancels + cancel_all failing")

            # portfolio day-loss floor -> kill sequence
            day_pnl = portfolio_net(state) - meta.get("day_anchor", 0.0)
            meta["day_pnl"] = day_pnl        # read by the in-minute guard deny
            if not meta.get("halted") and guards.day_floor_breached(state, day_pnl):
                kill_sequence(execc, state, base,
                              f"day loss floor: pnl={day_pnl:.2f} "
                              f"floor=-{cfg['day_loss_floor']:.2f} "
                              f"(of which settle_realized="
                              f"{meta.get('settle_realized_day', 0.0):.2f} — "
                              f"a stale-loss settlement kill is not live "
                              f"bleed; check settlements ledger before "
                              f"resuming)")

            _save_state(base, state_path, state)

            if now - hb > 300:
                hb = now
                quoting = sum(1 for k, s2 in state.items()
                              if k != "meta" and isinstance(s2, dict) and s2.get("ob"))
                gross = sum(s2.get("spent", 0.0) for k, s2 in state.items()
                            if k != "meta" and isinstance(s2, dict))
                nzomb = sum(len(s2.get("zombies") or []) for k, s2 in state.items()
                            if k != "meta" and isinstance(s2, dict))
                with BOOKS_LOCK:
                    stale = sum(1 for b in BOOKS.values() if now - b["ts"] > 300)
                    nobook = sum(1 for m2 in universe
                                 for a in (m2["yes"], m2["no"]) if a not in BOOKS)
                dtop = sorted(guards.denials.items(), key=lambda x: -x[1])[:6]
                print(f"hb[{cfg['mode']}]: q={quoting}/{len(universe)} "
                      f"gross=${gross:.0f} dayPnL=${day_pnl:.2f} "
                      f"floor=-${cfg['day_loss_floor']:.0f} "
                      f"accday=${meta.get('acc_day', 0.0):.2f} "
                      f"halted={meta.get('halted', False)} zombies={nzomb} "
                      f"lmiss={_LEDGER_MISS['n']} "
                      f"feedfail={meta.get('feed_fail_n', 0)} "
                      f"anom={meta.get('feed_anomaly_n', 0)}"
                      f"/{meta.get('unmatched_fills_n', 0)} "
                      f"respend={meta.get('res_pending', 0)} "
                      f"deny={dict(dtop)} books={len(BOOKS)} stale={stale} "
                      f"nobook={nobook} http_hr={len(_http_window)}/{HTTP_BUDGET_PER_HOUR}",
                      flush=True)

        time.sleep(1)


def _trade_ts(tr):
    """Best-effort trade timestamp in seconds. Tries numeric epoch fields
    (ms-epoch normalized) then ISO strings; 0.0 when unparseable."""
    for k in ("match_time", "matched_at", "timestamp", "created_at",
              "last_update"):
        v = tr.get(k)
        if v is None:
            continue
        try:
            fv = float(v)
            if fv > 1e12:
                fv /= 1000.0
            if fv > 0:
                return fv
        except (TypeError, ValueError):
            ts = parse_iso(str(v))
            if ts:
                return ts
    return 0.0


TRADE_LAP_S = 3600.0    # watermark grace window for out-of-order records


def _apply_live_trades(execc, state, universe, base, meta):
    """Reconcile the authenticated trade feed into y/n ledgers.

    Feed semantics (review CRIT 2): the TOP-LEVEL record is taker-oriented —
    its side/size describe the taker across ALL makers in the match. A
    post-only maker's fills live in maker_orders[]; we apply ONLY entries
    whose maker address is ours, at the per-entry matched amount. A trade
    where WE are the taker (preflight FAK only; the engine never takes) is
    applied from the top-level fields. Anything else is ledgered LOUDLY and
    never applied. The funded preflight's tiny-fill stage verifies the real
    record shape against this parser before any capital scales.

    Watermark (review CRIT 3): timestamp + a recent-id map bounded by a
    time lap — never an unordered id set (eviction there re-ingested old
    trades as fresh fills). Token->market mapping is persistent via each
    st's tok_y/tok_n (review finding 17) so fills on ghost orders or
    rotated-out markets still reach the ledgers and caps."""
    addr = str(getattr(execc, "address", "") or "").lower()
    trades = execc.fetch_my_trades()
    if trades is None:
        # dead feed: count consecutively; blind quoting is the exact state
        # the kill exists to prevent (2nd-pass M1)
        meta["feed_fail_n"] = meta.get("feed_fail_n", 0) + 1
        return
    meta["feed_fail_n"] = 0
    wm = float(meta.get("trade_wm") or 0.0)
    recent = dict(meta.get("trade_recent") or {})
    tok2mkt = {}
    for k, st2 in state.items():
        if k != "meta" and isinstance(st2, dict):
            if st2.get("tok_y"):
                tok2mkt[st2["tok_y"]] = (k, "yes")
            if st2.get("tok_n"):
                tok2mkt[st2["tok_n"]] = (k, "no")
    for m in universe:
        tok2mkt[m["yes"]] = (str(m["id"]), "yes")
        tok2mkt[m["no"]] = (str(m["id"]), "no")
    max_ts = wm
    for tr in trades if isinstance(trades, list) else []:
        if not isinstance(tr, dict):
            continue
        tid = str(tr.get("id") or tr.get("trade_id") or "")
        ts = _trade_ts(tr)
        if not tid:
            # id-less record: loud, never applied (2nd-pass NEW-7 — the
            # silent drop contradicted the everything-loud contract)
            meta["feed_anomaly_n"] = meta.get("feed_anomaly_n", 0) + 1
            ledger_write(base, "fills", {"t": round(time.time()),
                                         "no_id": str(tr)[:250]})
            continue
        if tid in recent:
            continue
        if ts <= 0.0:
            # ts-unparseable: QUARANTINE, never apply. Applying with a
            # wall-clock recent-stamp let the id time-evict and the same
            # historical record re-apply every LAP forever (2nd-pass
            # NEW-1). Pin the id far-future so it never evicts.
            recent[tid] = 4e12
            meta["feed_anomaly_n"] = meta.get("feed_anomaly_n", 0) + 1
            ledger_write(base, "fills", {"t": round(time.time()), "tid": tid,
                                         "no_timestamp": str(tr)[:250]})
            continue
        if ts < wm - TRADE_LAP_S:
            continue                        # decisively pre-watermark
        recent[tid] = ts
        max_ts = max(max_ts, ts)
        legs = []                           # (asset, side, px, sz, role)
        for mo in tr.get("maker_orders") or []:
            if not isinstance(mo, dict):
                continue
            mo_addr = str(mo.get("maker_address") or mo.get("owner") or "").lower()
            if mo_addr != addr:
                continue
            legs.append((str(mo.get("asset_id") or mo.get("asset") or ""),
                         str(mo.get("side") or "").upper(), mo.get("price"),
                         mo.get("matched_amount") or mo.get("size"), "maker"))
        if not legs:
            tk_addr = str(tr.get("taker_address") or tr.get("owner") or "").lower()
            if tk_addr == addr:
                legs.append((str(tr.get("asset_id") or tr.get("asset") or ""),
                             str(tr.get("side") or "").upper(), tr.get("price"),
                             tr.get("size"), "taker"))
        if not legs:
            meta["feed_anomaly_n"] = meta.get("feed_anomaly_n", 0) + 1
            ledger_write(base, "fills", {"t": round(time.time()), "tid": tid,
                                         "no_our_leg": str(tr)[:250]})
            continue
        for asset, side, px_raw, sz_raw, role in legs:
            try:
                px, sz = float(px_raw), float(sz_raw)
            except (TypeError, ValueError):
                # OUR-address leg we cannot apply = possible schema drift:
                # inventory silently stops tracking real money (2nd-pass
                # M2) — counted, heartbeat-surfaced, kill-thresholded
                meta["unmatched_fills_n"] = meta.get("unmatched_fills_n", 0) + 1
                ledger_write(base, "fills", {"t": round(time.time()),
                                             "tid": tid, "unparsed":
                                             str((px_raw, sz_raw))[:120]})
                continue
            hit = tok2mkt.get(asset)
            if hit is None or side != "BUY" or not (0 < px < 1) or sz <= 0:
                # the engine only ever BUYs — anything else is loud
                meta["unmatched_fills_n"] = meta.get("unmatched_fills_n", 0) + 1
                ledger_write(base, "fills",
                             {"t": round(time.time()), "tid": tid, "unmatched":
                              str((asset[:20], side, px_raw, sz_raw, role))[:200]})
                continue
            key, leg = hit
            st = state.setdefault(key, {})
            if st.get("settled"):
                # late-arriving fill after settlement: the new tokens are
                # unrealized again — clear the flag so the next sweep
                # re-settles idempotently (settlement-review finding 3)
                st["settled"] = False
            if leg == "yes":
                st["y"] = round(st.get("y", 0.0) + sz, 4)
            else:
                st["n"] = round(st.get("n", 0.0) + sz, 4)
            st["spent"] = round(st.get("spent", 0.0) + px * sz, 4)
            merge_pairs(st)
            ledger_write(base, "fills", {"t": round(time.time()), "tid": tid,
                                         "mkt": key, "leg": leg, "px": px,
                                         "sz": sz, "role": role})
    meta["trade_wm"] = max_ts
    meta["trade_recent"] = {i: t for i, t in recent.items()
                            if t >= max_ts - TRADE_LAP_S}


def _save_state(base, state_path, state):
    tmp = state_path + ".tmp"
    slim = {}
    for k, v in state.items():
        if isinstance(v, dict):
            slim[k] = {kk: vv for kk, vv in v.items()
                       if kk not in ("qh", "mid_hist", "prints_hist")}
        else:
            slim[k] = v
    with open(tmp, "w") as f:
        json.dump(slim, f)
    if os.path.exists(state_path):
        try:
            os.replace(state_path, state_path + ".bak")
        except OSError:
            pass
    os.replace(tmp, state_path)


def report(base):
    state = json.load(open(os.path.join(base, "state.json")))
    meta = state.get("meta") or {}
    bysec = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])  # n, spent, val, acc, merged
    dep = [0, 0.0, 0.0]                                   # n, spent, val (frozen)
    sett = [0, 0.0]                                       # n, realized$
    for k, st in state.items():
        if k == "meta" or not isinstance(st, dict):
            continue
        lm = st.get("last_mid")
        y, n = st.get("y", 0.0), st.get("n", 0.0)
        val = (y * lm + n * (1.0 - lm)) if lm is not None else (y + n) * 0.5
        if st.get("departed"):
            if st.get("settled"):
                # resolution-backfilled: contribution is REALIZED
                sett[0] += 1
                sett[1] += -st.get("spent", 0.0)
            elif y or n or st.get("spent"):
                # frozen marks, sector-cap-quarantined — reported, not hidden
                dep[0] += 1
                dep[1] += st.get("spent", 0.0)
                dep[2] += val
            continue
        row = bysec[st.get("sector") or "?"]
        row[0] += 1
        row[1] += st.get("spent", 0.0)
        row[2] += val
        row[3] += st.get("acc", 0.0)
        row[4] += st.get("merged", 0.0)
    print(f"mode-agnostic ledger report  (halted={meta.get('halted', False)} "
          f"day={meta.get('day')} acc_day=${meta.get('acc_day', 0.0):.2f})")
    print("%-14s %5s %10s %10s %10s %10s %10s" %
          ("sector", "n", "spent$", "value$", "pnl$", "accrual$", "merged"))
    tot = [0, 0.0, 0.0, 0.0, 0.0]
    for sec in sorted(bysec):
        n_, sp, va, ac, mg = bysec[sec]
        print("%-14s %5d %10.2f %10.2f %10.2f %10.2f %10.2f" %
              (sec, n_, sp, va, va - sp, ac, mg))
        for i, x in enumerate((n_, sp, va, ac, mg)):
            tot[i] += x
    print("%-14s %5d %10.2f %10.2f %10.2f %10.2f %10.2f" %
          ("TOTAL", tot[0], tot[1], tot[2], tot[2] - tot[1], tot[3], tot[4]))
    if dep[0]:
        print("departed UNSETTLED (frozen marks, awaiting resolution sweep): "
              "n=%d spent$%.2f value$%.2f pnl$%.2f"
              % (dep[0], dep[1], dep[2], dep[2] - dep[1]))
    if sett[0]:
        print("departed SETTLED (resolution-backfilled, realized): "
              "n=%d realized$%.2f" % (sett[0], sett[1]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--base", default="/opt/pa2-maker-live")
    a = ap.parse_args()
    if a.report:
        report(a.base)
    elif a.run:
        cfg = load_config()
        sys.exit(run(a.base, cfg))
    else:
        print("need --run or --report")
