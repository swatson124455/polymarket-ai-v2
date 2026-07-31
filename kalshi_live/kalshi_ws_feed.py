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
    """[[price, size], ...] in either dialect -> {price_dollars: size_ct}.
    Row hygiene (review 07-25): drop non-positive prices/sizes and off-range
    prices — a garbage row must never become a phantom level (best()=0.0)."""
    out = {}
    for row in raw or []:
        try:
            pr, sz = row[0], row[1]
        except (TypeError, IndexError):
            continue
        p, s = round(_norm_price(pr), 4), _f(sz)
        if 0 < p < 1 and s > 0:
            out[p] = s
    return out


def _side_rows(msg, side):
    """Extract one side's rows from a snapshot msg across ALL observed dialects.
    LIVE-VERIFIED 2026-07-25: prod WS sends 'yes_dollars_fp'/'no_dollars_fp'
    (dollar-string rows); older dialects were 'yes_dollars' and cents-int 'yes'."""
    for key in (f"{side}_dollars_fp", f"{side}_dollars", side):
        if key in msg:
            return msg.get(key)
    return None


# Audit probe 2026-07-30: count of WS messages the mirror could NOT parse (unknown side key,
# unpriceable price/delta after both dialect fallbacks). Each one fails SAFE (book marked dirty,
# re-snapshot) but a venue dialect migration would tick this on every delta — the silent-degrade
# gauge the 07-29 audit asked for. Surfaced in ws_daemon_log cold_cycle rows.
PARSE_FAILS = [0]


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
        yr, nr = _side_rows(msg, "yes"), _side_rows(msg, "no")
        if yr is None and nr is None:
            # NO recognized side key at all (next dialect migration): this is a
            # book we cannot read, not an empty book — refuse to claim clean.
            # (One-sided books legitimately send only one key — live-verified.)
            PARSE_FAILS[0] += 1     # audit probe 2026-07-30: dialect drift must be countable
            self.dirty = True
            return
        self.yes = _norm_rows(yr)
        self.no = _norm_rows(nr)
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
            # Fails SAFE (dirty -> re-snapshot) but used to fail SILENT: a venue key rename
            # (price_dollars/delta_fp -> next dialect) would cold every book with no gauge
            # (audit probe 2026-07-30). Counted module-wide; surfaced in ws_daemon_log.
            PARSE_FAILS[0] += 1
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


async def _recv_or_stop(ws, stop_event):
    """recv raced against stop_event so shutdown is ~instant even on an idle
    book (review 07-25: a bare wait_for(recv, 300) made stop wait up to 300s).
    Returns ("msg", raw) | ("stopped", None); raises TimeoutError on true idle."""
    recv_f = asyncio.ensure_future(ws.recv())
    if stop_event is None:
        try:
            return "msg", await asyncio.wait_for(recv_f, timeout=RECV_TIMEOUT_S)
        except asyncio.TimeoutError:
            recv_f.cancel()
            raise
    stop_f = asyncio.ensure_future(stop_event.wait())
    done, _ = await asyncio.wait({recv_f, stop_f}, timeout=RECV_TIMEOUT_S,
                                 return_when=asyncio.FIRST_COMPLETED)
    if stop_f in done and recv_f not in done:
        recv_f.cancel()
        return "stopped", None
    stop_f.cancel()
    if recv_f in done:
        return "msg", recv_f.result()
    recv_f.cancel()
    raise asyncio.TimeoutError()


class Feed:
    """Owns the WS connection + mirrors. on_book(ticker, mirror) and
    on_fill(msg) are sync callbacks (must be fast; daemon does the work)."""

    def __init__(self, tickers, on_book=None, on_fill=None, want_fills=False,
                 initial_fails=0):
        # audit batch 3 (J7, operator-approved 2026-07-29): the fail counter lives on the
        # instance so a rebuild (footprint resubscribe / watchdog) can CARRY it into the new
        # Feed — a fresh object during a venue outage used to reset the backoff to 2s and
        # turn footprint churn into a reconnect storm.
        self.fails = int(initial_fails)
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
        self.confirmed_channels = set()               # "subscribed" acks seen this connection
        self.error_frames = 0

    @property
    def fills_confirmed(self):
        """True only when the venue ACKed the fill channel THIS connection.
        Review 07-25 BLOCKER: the fill channel is the hot path's only staleness
        invalidation — an unacked subscription must gate hot writes OFF."""
        return "fill" in self.confirmed_channels

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
        if not isinstance(d, dict):
            return True                               # valid-JSON non-dict frame: ignore, never die
        self.msg_count += 1
        self.last_msg_mono = time.monotonic()
        typ = d.get("type") or ""
        if typ == "subscribed":
            ch = (d.get("msg") or {}).get("channel")
            if ch:
                self.confirmed_channels.add(ch)
            return True
        if typ == "error":
            self.error_frames += 1
            print(f"WS error frame: {str(d)[:200]}")
            return False                              # reconnect; repeated errors back off in run()
        msg = d.get("msg")
        if not isinstance(msg, dict):
            msg = {}
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
        # J7: self.fails (consecutive failures -> escalating backoff) lives on the instance so
        # a rebuilt Feed inherits the prior backoff state instead of resetting to 2s.
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            if deadline and time.monotonic() >= deadline:
                return
            try:
                ws = await self._connect()
                self.confirmed_channels = set()       # acks are per-connection
                try:
                    await self._subscribe(ws)
                    while True:
                        if stop_event is not None and stop_event.is_set():
                            return
                        if deadline and time.monotonic() >= deadline:
                            return
                        kind, raw = await _recv_or_stop(ws, stop_event)
                        if kind == "stopped":
                            return
                        ok = self._dispatch(raw)
                        # fails resets only AFTER a frame dispatches cleanly (re-review:
                        # resetting before dispatch pinned backoff at 2s forever when a
                        # CALLBACK raised on every frame).
                        if not ok:
                            # BACKOFF ON THIS PATH TOO (re-review: the break path had no
                            # sleep -> a persistent error frame produced ~19k connects/s).
                            self.fails += 1
                            backoff = min(2.0 * (2 ** min(self.fails - 1, 5)), 60.0)
                            print(f"WS seq gap/error — reconnect (fail #{self.fails}, "
                                  f"backoff {backoff:.0f}s)")
                            await asyncio.sleep(backoff)
                            break                     # reconnect loop re-subscribes
                        self.fails = 0                # clean dispatch = healthy connection
                finally:
                    self._sub_seq = None
                    self.confirmed_channels = set()
                    await ws.close()
            # BROAD except is deliberate (review 07-25 HIGH): a dispatch bug on one
            # malformed frame must cost ONE reconnect, never silently kill the feed
            # task (the daemon would degrade to a dumb timer with no signal).
            except Exception as e:
                self.fails += 1
                for m in self.mirrors.values():
                    m.dirty = True                   # stale on disconnect — never trust across gaps
                backoff = min(2.0 * (2 ** min(self.fails - 1, 5)), 60.0)
                print(f"WS reconnect after {e!r} (fail #{self.fails}, backoff {backoff:.0f}s)")
                await asyncio.sleep(backoff)


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
