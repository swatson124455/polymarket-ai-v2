#!/usr/bin/env python3
"""D4 books-over-time recorder — GET-only observation infra (spec: D3(d), 2026-08-13).

This script can NEVER trade: it issues only unauthenticated public GETs
(orderbook / trades / market meta), the same proven access pattern as the
recorder-arm samples tape (gapless 07-30 -> 08-13, D3 §a). It is fully
independent of the quoter, the ws daemon, and the STOP file.

Every 60 s sweep, for each ticker in d4_tickers.json (flat JSON list, capped
at 40):
  - GET /markets/{t}/orderbook -> one row per ticker per sweep in
    d4_books-YYYYMMDD.jsonl:
      {ts, ticker, yes_bid, yes_ask, spread_c, mid,
       bid_depth: [[price,qty]..] (yes bids within 3 ticks of touch),
       ask_depth: [[price,qty]..] (yes-basis asks = 1-no_bid, within 3 ticks)}
Every 5th sweep additionally:
  - GET /markets/trades?ticker=... -> {ts, ticker, trades:[...]} rows in
    d4_trades-YYYYMMDD.jsonl, watermarked per ticker (last_ts + trade_ids at
    the boundary second) so trades are never duplicated across sweeps/restarts.
Once per ticker per UTC day:
  - GET /markets/{t} -> {ts, ticker, close_time, status, ...} in
    d4_meta-YYYYMMDD.jsonl. A meta sweep defers the trades pass to the next
    sweep so a combined sweep stays under the 60 s budget at 40 tickers
    (40 + 40 GETs x 0.6 s = 48 s < 60 s).

Crash-safe: any per-ticker or per-sweep exception is logged and skipped; the
loop exits only on SIGTERM/SIGINT. State (watermarks, meta dates) persists in
d4_state.json via tmp+rename.
"""
import json
import os
import signal
import sys
import time
import urllib.request
from datetime import datetime, timezone

BASE = "https://external-api.kalshi.com"
P = "/trade-api/v2"
SPACING_S = 0.6                      # global read spacing (public rate-limit hygiene)
SWEEP_S = 60
TRADES_EVERY = 5                     # every 5th sweep
MAX_TICKERS = 40
DEPTH_TICKS = 3
TICK = 0.01
_EPS = 1e-9

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
TICKERS_FILE = os.path.join(DATA_DIR, "d4_tickers.json")
STATE_FILE = os.path.join(DATA_DIR, "d4_state.json")

_last = [0.0]
_running = [True]


def log(msg):
    print(f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}", flush=True)


def _stop(signum, frame):
    log(f"signal {signum} -> shutting down after current sweep")
    _running[0] = False


def get(path):
    """Spaced unauthenticated public GET (orderbook/trades/markets are public)."""
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        BASE + path, headers={"User-Agent": "kalshi-d4-recorder/1.0"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    finally:
        _last[0] = time.time()


# ---------------------------------------------------------------------------
# Pure functions (unit-tested in test_d4_book_recorder.py)
# ---------------------------------------------------------------------------

def parse_levels(raw):
    """[[price_str,size_str]..] -> [(price,size)] floats, size>0 only.
    Same semantics as the quoter's proven _levels parse (malformed rows skipped)."""
    out = []
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out.append((p, s))
    return out


def book_row(ts, ticker, ob):
    """orderbook_fp payload -> one d4_books row (yes basis).
    yes_ask = 1 - best_no_bid (quoter _touch convention)."""
    yl = parse_levels((ob or {}).get("yes_dollars"))
    nl = parse_levels((ob or {}).get("no_dollars"))
    yes_bid = max((p for p, _ in yl), default=None)
    no_bid = max((p for p, _ in nl), default=None)
    yes_ask = round(1.0 - no_bid, 4) if no_bid is not None else None
    spread_c = mid = None
    if yes_bid is not None and yes_ask is not None:
        spread_c = round((yes_ask - yes_bid) * 100.0, 2)
        mid = round((yes_bid + yes_ask) / 2.0, 4)
    lim = DEPTH_TICKS * TICK + _EPS
    bid_depth = []
    if yes_bid is not None:
        bid_depth = [[p, q] for p, q in sorted(yl, key=lambda x: -x[0])
                     if yes_bid - p <= lim]
    ask_depth = []
    if yes_ask is not None:
        asks = [(round(1.0 - p, 4), q) for p, q in nl]
        ask_depth = [[p, q] for p, q in sorted(asks, key=lambda x: x[0])
                     if p - yes_ask <= lim]
    return {"ts": ts, "ticker": ticker, "yes_bid": yes_bid, "yes_ask": yes_ask,
            "spread_c": spread_c, "mid": mid,
            "bid_depth": bid_depth, "ask_depth": ask_depth}


def trade_ts(t):
    """created_time ISO string -> int epoch seconds (0 if unparseable)."""
    try:
        return int(datetime.fromisoformat(
            str(t.get("created_time", "")).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def dedupe_trades(trades, wm):
    """Watermark dedup. wm = {"last_ts": int, "ids": [trade_id..]} (ids = trades
    AT last_ts). Returns (new_trades sorted ascending, new_wm). A trade is new
    iff ts > last_ts, or ts == last_ts and trade_id unseen. Restart-safe: the
    watermark persists in d4_state.json."""
    last_ts = int((wm or {}).get("last_ts") or 0)
    ids = set((wm or {}).get("ids") or [])
    new, seen = [], set()
    for t in sorted(trades or [], key=trade_ts):
        ts = trade_ts(t)
        tid = t.get("trade_id")
        if tid in seen:
            continue
        if ts > last_ts or (ts == last_ts and tid not in ids):
            new.append(t)
            seen.add(tid)
    if new:
        max_ts = trade_ts(new[-1])
        boundary = {t.get("trade_id") for t in new if trade_ts(t) == max_ts}
        if max_ts == last_ts:
            boundary |= ids
        wm = {"last_ts": max_ts, "ids": sorted(x for x in boundary if x)}
    else:
        wm = {"last_ts": last_ts, "ids": sorted(ids)}
    return new, wm


def cap_tickers(tickers, cap=MAX_TICKERS):
    """(kept, n_dropped) — flat-list cap, first `cap` kept."""
    tickers = [t for t in (tickers or []) if isinstance(t, str) and t]
    return tickers[:cap], max(0, len(tickers) - cap)


# ---------------------------------------------------------------------------
# I/O plumbing
# ---------------------------------------------------------------------------

def utc_day():
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_row(prefix, row):
    path = os.path.join(DATA_DIR, f"{prefix}-{utc_day()}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def load_tickers(warned):
    try:
        with open(TICKERS_FILE) as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        log(f"ERROR reading {TICKERS_FILE}: {e} -> empty sweep")
        return []
    kept, dropped = cap_tickers(raw)
    if dropped and not warned[0]:
        log(f"WARNING: {len(raw)} tickers configured, cap is {MAX_TICKERS} — "
            f"recording the first {MAX_TICKERS}, dropping {dropped}")
        warned[0] = True
    return kept


def fetch_trades_page(ticker, min_ts):
    """Up to 3 cursor pages of the public tape (newest-first), bounded by min_ts."""
    out, cursor = [], ""
    for _ in range(3):
        qs = f"ticker={ticker}&limit=1000" + (f"&min_ts={min_ts}" if min_ts else "")
        if cursor:
            qs += f"&cursor={cursor}"
        d = get(f"{P}/markets/trades?{qs}")
        batch = d.get("trades") or []
        out.extend(batch)
        cursor = d.get("cursor") or ""
        if not cursor or not batch:
            break
    return out


# ---------------------------------------------------------------------------
# Sweep loop
# ---------------------------------------------------------------------------

def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log(f"kalshi_d4_book_recorder start (pid {os.getpid()}, dir {DATA_DIR})")
    state = load_state()
    state.setdefault("watermarks", {})
    state.setdefault("meta_dates", {})
    warned = [False]
    sweep_n = 0
    trades_pending = False

    while _running[0]:
        t0 = time.time()
        sweep_n += 1
        tickers = load_tickers(warned)
        day = utc_day()

        # --- meta: once per ticker per UTC day -----------------------------
        need_meta = [t for t in tickers if state["meta_dates"].get(t) != day]
        did_meta = False
        for t in need_meta:
            if not _running[0]:
                break
            try:
                mk = get(f"{P}/markets/{t}").get("market") or {}
                append_row("d4_meta", {
                    "ts": now_iso(), "ticker": t,
                    "close_time": mk.get("close_time"),
                    "status": mk.get("status"),
                    "open_time": mk.get("open_time"),
                    "strike_type": mk.get("strike_type"),
                    "result": mk.get("result")})
                state["meta_dates"][t] = day
                did_meta = True
            except Exception as e:
                log(f"meta {t}: {type(e).__name__}: {e}")

        # --- books: every sweep --------------------------------------------
        ok = 0
        for t in tickers:
            if not _running[0]:
                break
            try:
                ob = get(f"{P}/markets/{t}/orderbook").get("orderbook_fp") or {}
                append_row("d4_books", book_row(now_iso(), t, ob))
                ok += 1
            except Exception as e:
                log(f"book {t}: {type(e).__name__}: {e}")

        # --- trades: every 5th sweep (deferred off meta sweeps for budget) --
        run_trades = (sweep_n % TRADES_EVERY == 0) or trades_pending
        if run_trades and did_meta:
            trades_pending = True           # keep this sweep < 60 s
        elif run_trades:
            trades_pending = False
            n_new = 0
            for t in tickers:
                if not _running[0]:
                    break
                try:
                    wm = state["watermarks"].get(t)
                    min_ts = int((wm or {}).get("last_ts") or 0)
                    raw = fetch_trades_page(t, min_ts)
                    new, wm2 = dedupe_trades(raw, wm)
                    state["watermarks"][t] = wm2
                    if new:
                        append_row("d4_trades",
                                   {"ts": now_iso(), "ticker": t, "trades": new})
                        n_new += len(new)
                except Exception as e:
                    log(f"trades {t}: {type(e).__name__}: {e}")
            if n_new:
                log(f"sweep {sweep_n}: {n_new} new trades")

        try:
            save_state(state)
        except Exception as e:
            log(f"state save: {type(e).__name__}: {e}")

        el = time.time() - t0
        log(f"sweep {sweep_n}: {ok}/{len(tickers)} books in {el:.1f}s"
            + (" (meta refreshed)" if did_meta else "")
            + (" (trades deferred)" if trades_pending else ""))
        # sleep the remainder in 1 s slices so SIGTERM lands promptly
        remaining = SWEEP_S - (time.time() - t0)
        while remaining > 0 and _running[0]:
            time.sleep(min(1.0, remaining))
            remaining = SWEEP_S - (time.time() - t0)

    log("kalshi_d4_book_recorder clean exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
