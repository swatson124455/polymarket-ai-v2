#!/usr/bin/env python3
"""KALSHI WEBSOCKET MARKET-DATA FEED — read-only book mirror for the WS daemon.

Provides:
  - authenticated WS connect (same RSA signing as REST; sign "GET /trade-api/ws/v2")
  - orderbook_snapshot / orderbook_delta subscription for a ticker set
  - BookMirror: local orderbook replica, tolerant to BOTH payload dialects
    (legacy cents ints under 'yes'/'no' AND the _fp dollars-strings under
    'yes_dollars'/'no_dollars' — the REST API migrated to _fp keys 2026-07;
    the WS dialect is empirically resolved by --smoke, so parse both)
  - strict seq accounting: any gap marks the ticker DIRTY (consumers must
    refetch the REST book instead of trusting the mirror — never quote off
    a book we know we misassembled)
  - --smoke N: connect for N seconds, subscribe, print msg rates + latency
    stats, place NO orders, then exit. Read-only by construction (this
    module never imports the order client).

This module is FEED ONLY. It has no order surface at all.
"""
import asyncio
import json
import os
import sys
import time

import websockets
import websockets.exceptions  # explicit: v15 lazy-loads submodules (CLAUDE.md)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_client import KalshiAuth, PROD_BASE  # noqa: E402

WS_PATH = "/trade-api/ws/v2"
# candidate hosts, tried in order: same host as REST first, then the public one.
WS_URL_CANDIDATES = [
    os.environ.get("KALSHI_WS_URL") or "",
    "wss://" + PROD_BASE.split("//", 1)[-1] + WS_PATH,
    "wss://api.elections.kalshi.com" + WS_PATH,
]
CONNECT_TIMEOUT_S = float(os.environ.get("KALSHI_WS_CONNECT_TIMEOUT_S", "30"))
# idle recv timeout is LONG on purpose: sleepy ladder books can be silent for
# minutes and the ping/pong keepalive (below) already detects dead sockets —
# a short recv timeout just churns reconnects + dirties every mirror (measured
# in the 07-25 smoke: reconnect after 30s idle on an alive socket).
RECV_TIMEOUT_S = float(os.environ.get("KALSHI_WS_RECV_TIMEOUT_S", "300"))
PING_INTERVAL_S = 10.0
PING_TIMEOUT_S = 20.0


def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _norm_price(p):
    """Normalize a price to DOLLARS float. Ints >= 1 are cents; strings/floats
    are dollars ('0.3600'). An int 0 is 0 either way."""
    if isinstance(p, int) and p >= 1:
        return p / 100.0
    return _f(p)


def _norm_rows(raw):
    """[[price, size], ...] in either dialect -> {price_dollars: size_ct}."""
    out = {}
    for row in raw or []:
        try:
            pr, sz = row[0], row[1]
        except (TypeError, IndexError):
            continue
        out[round(_norm_price(pr), 4)] = _f(sz)
    return out


def _side_rows(msg, side):
    """Extract one side's rows from a snapshot msg across ALL observed dialects.
    LIVE-VERIFIED 2026-07-25: prod WS sends 'yes_dollars_fp'/'no_dollars_fp'
    (dollar-string rows); older dialects were 'yes_dollars' and cents-int 'yes'."""
    for key in (f"{side}_dollars_fp", f"{side}_dollars", side):
        if key in msg:
            return msg.get(key)
    return None


class BookMirror:
    """Local orderbook replica for one ticker. yes/no maps: price->size.
    dirty=True means the mirror CANNOT be trusted (seq gap / never seeded):
    consumers must fall back to a REST book fetch."""

    __slots__ = ("ticker", "yes", "no", "seq", "dirty", "last_update_mono")

    def __init__(self, ticker):
        self.ticker = ticker
        self.yes = {}
        self.no = {}
        self.seq = None
        self.dirty = True
        self.last_update_mono = 0.0

    def apply_snapshot(self, msg, seq=None):
        self.yes = _norm_rows(_side_rows(msg, "yes"))
        self.no = _norm_rows(_side_rows(msg, "no"))
        self.seq = seq
        self.dirty = False
        self.last_update_mono = time.monotonic()

    def apply_delta(self, msg, seq=None):
        """NOTE: seq is GLOBAL per subscription (live-verified), so cross-message
        gap detection lives in Feed, not here — this only applies a parsed delta."""
        if self.dirty:
            return                                   # ignore deltas until a snapshot seeds us
        side = (msg.get("side") or "").lower()
        price = round(_norm_price(msg.get("price_dollars")
                                  if "price_dollars" in msg else msg.get("price")), 4)
        delta = _f(msg.get("delta_fp") if "delta_fp" in msg else msg.get("delta"))
        book = self.yes if side == "yes" else self.no if side == "no" else None
        if book is None or price <= 0:
            self.dirty = True                        # unparseable delta -> refuse to guess
            return
        new = book.get(price, 0.0) + delta
        if new <= 0:
            book.pop(price, None)
        else:
            book[price] = new
        self.last_update_mono = time.monotonic()

    def best(self):
        """(best_yes_bid, best_no_bid) in dollars, None where side empty."""
        by = max(self.yes, default=None)
        bn = max(self.no, default=None)
        return by, bn

    def rows(self):
        """REST-shaped [[price_str, size_str]...] pair (yes, no) for reuse by
        maker_kalshi_quoter._levels — ascending price like the API returns."""
        ys = [[f"{p:.4f}", f"{s:.2f}"] for p, s in sorted(self.yes.items())]
        ns = [[f"{p:.4f}", f"{s:.2f}"] for p, s in sorted(self.no.items())]
        return ys, ns


def _auth_headers():
    key_id = os.environ.get("KALSHI_API_KEY_ID")
    pem = os.environ.get("KALSHI_RSA_PRIVATE_KEY_PATH")
    if not (key_id and pem):
        return {}
    return KalshiAuth(key_id, pem).headers("GET", WS_PATH)


async def _recv(ws):
    """recv with a hard timeout on every await (ship discipline)."""
    return await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT_S)


class Feed:
    """Owns the WS connection + mirrors. on_book(ticker, mirror) and
    on_fill(msg) are sync callbacks (must be fast; daemon does the work)."""

    def __init__(self, tickers, on_book=None, on_fill=None, want_fills=False):
        self.tickers = list(tickers)
        self.mirrors = {t: BookMirror(t) for t in self.tickers}
        self.on_book = on_book
        self.on_fill = on_fill
        self.want_fills = want_fills
        self.connected_url = None
        self.msg_count = 0
        self.gap_count = 0
        self.last_msg_mono = 0.0
        self._sub_seq = None                          # GLOBAL per-subscription seq (live-verified)
        self.feed_lat_ms = []                         # recv-time - exchange ts_ms (feed latency)

    async def _connect(self):
        last_err = None
        for url in [u for u in WS_URL_CANDIDATES if u]:
            try:
                ws = await asyncio.wait_for(
                    websockets.connect(url, additional_headers=_auth_headers(),
                                       ping_interval=PING_INTERVAL_S,
                                       ping_timeout=PING_TIMEOUT_S),
                    timeout=CONNECT_TIMEOUT_S)
                self.connected_url = url
                return ws
            except Exception as e:                   # try next candidate
                last_err = e
        raise ConnectionError(f"all WS candidates failed; last={last_err!r}")

    async def _subscribe(self, ws):
        chans = ["orderbook_delta"]
        if self.want_fills:
            chans.append("fill")
        await ws.send(json.dumps({
            "id": 1, "cmd": "subscribe",
            "params": {"channels": chans, "market_tickers": self.tickers}}))

    def _dispatch(self, raw):
        """Returns True normally, False on a GLOBAL seq gap (caller must
        reconnect: a missed message belongs to an UNKNOWN ticker, so every
        mirror is suspect and only fresh snapshots restore trust)."""
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            return True
        self.msg_count += 1
        self.last_msg_mono = time.monotonic()
        typ = d.get("type") or ""
        msg = d.get("msg") or {}
        seq = d.get("seq")
        if typ in ("orderbook_snapshot", "orderbook_delta") and seq is not None:
            if self._sub_seq is not None and seq != self._sub_seq + 1:
                self.gap_count += 1
                self._sub_seq = None
                for m in self.mirrors.values():
                    m.dirty = True
                return False                          # force reconnect for fresh snapshots
            self._sub_seq = seq
        ts_ms = msg.get("ts_ms")
        if ts_ms:
            self.feed_lat_ms.append(time.time() * 1000 - float(ts_ms))
        t = msg.get("market_ticker") or msg.get("ticker") or ""
        if typ == "orderbook_snapshot" and t in self.mirrors:
            self.mirrors[t].apply_snapshot(msg, seq)
            if self.on_book:
                self.on_book(t, self.mirrors[t])
        elif typ == "orderbook_delta" and t in self.mirrors:
            m = self.mirrors[t]
            m.apply_delta(msg, seq)
            if self.on_book:
                self.on_book(t, m)
        elif typ == "fill" and self.on_fill:
            self.on_fill(msg)
        return True

    async def run(self, stop_event=None, max_seconds=None):
        """Connect/subscribe/dispatch until stop_event set or max_seconds up.
        Reconnects (fresh snapshots re-seed mirrors) on any socket error."""
        deadline = time.monotonic() + max_seconds if max_seconds else None
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            if deadline and time.monotonic() >= deadline:
                return
            try:
                ws = await self._connect()
                try:
                    await self._subscribe(ws)
                    while True:
                        if stop_event is not None and stop_event.is_set():
                            return
                        if deadline and time.monotonic() >= deadline:
                            return
                        if not self._dispatch(await _recv(ws)):
                            print("WS seq gap — reconnecting for fresh snapshots")
                            break                     # reconnect loop re-subscribes
                finally:
                    self._sub_seq = None
                    await ws.close()
            except (websockets.exceptions.WebSocketException,
                    ConnectionError, OSError, asyncio.TimeoutError) as e:
                for m in self.mirrors.values():
                    m.dirty = True                   # stale on disconnect — never trust across gaps
                print(f"WS reconnect after {e!r}")
                await asyncio.sleep(2.0)


def smoke(seconds, tickers):
    """Read-only connectivity + latency probe. NO ORDERS (no order client import)."""
    lat = []

    def on_book(t, m):
        lat.append(time.monotonic())

    feed = Feed(tickers, on_book=on_book)
    t0 = time.monotonic()
    asyncio.run(feed.run(max_seconds=seconds))
    dt = time.monotonic() - t0
    gaps_ms = [round((b - a) * 1000) for a, b in zip(lat, lat[1:])]
    print(f"url={feed.connected_url}")
    print(f"secs={dt:.1f} msgs={feed.msg_count} book_events={len(lat)} "
          f"seq_gaps={feed.gap_count} rate={feed.msg_count / max(dt, 1e-9):.2f}/s")
    if gaps_ms:
        gaps_ms.sort()
        print(f"inter-event ms: p50={gaps_ms[len(gaps_ms)//2]} "
              f"p90={gaps_ms[int(len(gaps_ms)*0.9)]} max={gaps_ms[-1]}")
    if feed.feed_lat_ms:
        fl = sorted(feed.feed_lat_ms)
        print(f"FEED LATENCY (recv - exchange ts_ms): p50={fl[len(fl)//2]:.0f}ms "
              f"p90={fl[int(len(fl)*0.9)]:.0f}ms n={len(fl)}")
    for t, m in feed.mirrors.items():
        by, bn = m.best()
        print(f"  {t:<28} dirty={m.dirty} levels y/n={len(m.yes)}/{len(m.no)} "
              f"best_yes={by} best_no={bn}")


if __name__ == "__main__":
    secs = 30
    tix = []
    args = sys.argv[1:]
    if args and args[0] == "--smoke":
        secs = int(args[1]) if len(args) > 1 else 30
        tix = args[2:]
    if not tix:
        tix = ["KXB200MON-26JUL31-6.960", "KXB200MON-26JUL31-7.360"]
    smoke(secs, tix)
