#!/usr/bin/env python3
"""Maker sensor-grid feed — informed-flow tripwire PUBLISHER. PAPER/READ-ONLY.

Build-queue item (operator-approved 2026-07-17, started same day): the Maker
lane's half of the fleet sensor grid. Watches the union of the Maker arms'
universes via PUBLIC tape only and appends rate-limited informed-flow events
to the Maker-owned feed drop:

    /opt/pa2-maker-feeds/informed_flow.jsonl      (append-only, one JSON/line)
    {"t": <epoch s>, "market_id": "<gamma id>", "cid": "0x...",
     "trigger": "bite|stampede|run", "direction": -1|0|1,
     "intensity": <float >=1.0 rounded 2dp>, "px": <last YES trade price>,
     "sector": "<label>", "q": "<question prefix>"}

Triggers (v1, thresholds era-stamped in code — change = new era):
  bite      >= $300 dominant-direction (>=3x the other side) taker notional
            within 60s AND >=1c move vs the pre-60s reference price (or the
            prior poll's last price when the window has no earlier print —
            the single-whale-print case MUST fire; review finding 2)
  stampede  >= 8 prints / 5 min (v5 tapevel lineage); direction = net flow
  run       |px move| >= 3c over 5 min
Direction: +1 = toward YES (taker buying YES), -1 = toward NO, 0 = unclear.
EMISSION IS ONSET-ONLY (False->True edge per (market, trigger); review
finding 1 — re-emitting a persisting condition every cooldown expiry stamps
post-move events as fresh and makes the precede-the-move study
uninterpretable), with the 600s cooldown as a secondary guard. Events carry
print-time anchors (ts_px_last) because event t is detection-time, up to a
poll late. Direction frame: YES-asset prints only (data-api tape is
taker-only; NO-taker prints under-represented — documented family
limitation, the validation readout will price it).

VALIDATION-FIRST: no bot consumes this feed until >=1 week of events is
scored for hit rate (do feed events PRECEDE adverse moves?). Consumption by
other bots = propose-only handoffs after that readout.

Guard rails: stdlib only, HTTP GET only, no keys, no DATABASE_URL; the code
opens ONLY its own dir + the feed file (kernel backstop: the unit grants
ReadWritePaths on the informed_flow.jsonl FILE, not the shared feeds dir —
wb_forecasts.jsonl stays kernel-read-only to this daemon); STOP sentinel;
disk/feed caps HALT EMISSION but keep the daemon alive and loudly warning
(a silent clean-exit under Restart=on-failure would never come back);
HTTP budget/hour + truncation counters visible in hb.

Usage:  maker_sensor_feed.py --run [--base /opt/pa2-maker-sensor]
        maker_sensor_feed.py --report [--base ...]
"""
import argparse
import glob
import json
import os
import sys
import threading
import time
from collections import defaultdict, deque

UA = {"User-Agent": "pa2-maker-sensor/1.0"}
TAPE = "https://data-api.polymarket.com/trades?market={cid}&limit=200"
FEED_PATH = "/opt/pa2-maker-feeds/informed_flow.jsonl"
UNIVERSE_GLOB = "/opt/pa2-maker-sim*/universe.json"   # read-only, own-lane files

MAX_MARKETS = 250
UNIVERSE_REFRESH_S = 1800
COOLDOWN_S = 600
MAX_DISK_MB = 200
FEED_MAX_MB = 100             # hard stop on feed size — a runaway emitter
                              # must not fill the shared drop dir
HTTP_BUDGET_PER_HOUR = 24000  # 250 mkts x ~1.2 pages x 60 min ~= 18K worst

BITE_NOTIONAL = 300.0         # $ one-direction taker notional in 60s
BITE_MOVE = 0.01
STAMPEDE_PRINTS_5M = 8        # v5 tapevel lineage
RUN_MOVE_5M = 0.03

_http_window = deque()
_http_lock = threading.Lock()   # get() runs from 8 worker threads


def http_used():
    now = time.time()
    with _http_lock:
        while _http_window and now - _http_window[0] > 3600:
            _http_window.popleft()
        return len(_http_window)


def get(url, timeout=10):
    with _http_lock:
        now = time.time()
        while _http_window and now - _http_window[0] > 3600:
            _http_window.popleft()
        if len(_http_window) >= HTTP_BUDGET_PER_HOUR:
            return None
        _http_window.append(now)
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def load_universe(pattern=UNIVERSE_GLOB):
    """Union of the Maker arms' universes (id, cid, q, sector, pool), capped
    MAX_MARKETS by pool. Any unreadable/foreign-schema file is skipped —
    the sensor must keep running on whatever subset exists."""
    by_cid = {}
    for path in sorted(glob.glob(pattern)):
        try:
            for m in json.load(open(path)).get("markets") or []:
                cid = str(m.get("cid") or "")
                if not cid.startswith("0x"):
                    continue
                pool = float(m.get("pool") or 0)
                prev = by_cid.get(cid)
                if prev is None or pool > prev["pool"]:
                    by_cid[cid] = {"id": str(m.get("id") or ""), "cid": cid,
                                   "q": (m.get("q") or "")[:60],
                                   "sector": m.get("sector") or "?",
                                   "yes": str(m.get("yes") or ""),
                                   "pool": pool}
        except Exception:
            continue
    dropped = sum(1 for r in by_cid.values() if not r["yes"])
    if dropped:
        # a market without its YES token id can never match a print — it
        # would burn HTTP forever while silently emitting nothing (review 4)
        print(f"universe: dropped {dropped} rows lacking a yes-token id",
              flush=True)
    rows = [r for r in by_cid.values() if r["yes"]]
    rows.sort(key=lambda r: -r["pool"])
    return rows[:MAX_MARKETS]


def fetch_tape(cid, since_ts, max_pages=2):
    """(prints, truncated) since since_ts, oldest-first (96df6d2 lineage:
    newest-first endpoint + offset pagination + dedup). truncated=True when
    the fetch ended (page cap / failed page) BEFORE reaching since_ts —
    prints in the gap are lost when the watermark advances; the caller
    counts truncations so the loss is visible, not silent (review 3/6)."""
    seen, out = set(), []
    reached = False
    for page in range(max_pages):
        t = get(TAPE.format(cid=cid) + "&offset=%d" % (200 * page))
        if not isinstance(t, list) or not t:
            # [] = genuinely no more prints (reached); None = failed page
            # (NOT reached — there may be a gap)
            reached = reached or isinstance(t, list)
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
            reached = True
            break

    def _ts(tr):
        try:
            return float(tr.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0.0
    out.sort(key=_ts)
    return out, (not reached and bool(out))


def window_stats(prints, yes_asset, now):
    """Summarize the YES-asset print window for the detectors. Pure.
    px_pre60 = latest print OLDER than 60s (the bite move reference — using
    the oldest print INSIDE 60s made a single whale print invisible;
    review finding 2). ts anchors ride along for the validator."""
    ws = {"n_5m": 0, "buy_60": 0.0, "sell_60": 0.0,
          "buy_5m": 0.0, "sell_5m": 0.0,
          "px_last": None, "ts_last": None,
          "px_5m": None, "ts_5m": None, "px_pre60": None}
    for tr in prints:
        if not isinstance(tr, dict):
            continue
        try:
            ts = float(tr.get("timestamp"))
            px = float(tr.get("price"))
            sz = float(tr.get("size"))
            side = str(tr.get("side") or "").upper()
            asset = str(tr.get("asset") or "")
        except (TypeError, ValueError):
            continue
        if not yes_asset or asset != yes_asset \
                or not (0 < px < 1) or sz <= 0 or ts > now:
            continue
        if now - ts > 300:
            continue
        ws["n_5m"] += 1
        if ws["px_5m"] is None:
            ws["px_5m"], ws["ts_5m"] = px, ts
        if now - ts > 60:
            ws["px_pre60"] = px
        notional = px * sz
        if side == "BUY":
            ws["buy_5m"] += notional
            if now - ts <= 60:
                ws["buy_60"] += notional
        elif side == "SELL":
            ws["sell_5m"] += notional
            if now - ts <= 60:
                ws["sell_60"] += notional
        ws["px_last"], ws["ts_last"] = px, ts
    return ws


def _sign(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def evaluate(ws, prev_px=None):
    """Detector matrix over a window summary. Pure; returns
    [(trigger, direction, intensity)] with intensity >= 1.0.
    prev_px = last price seen on a PRIOR poll — the bite move reference of
    last resort so a lone whale print on a quiet market still fires."""
    out = []
    if ws["px_last"] is None:
        return out
    # bite: dominant-direction 60s notional (>=3x the other side — two-sided
    # churn is not a bite; review finding 9) + immediate impact vs the
    # pre-60s reference (or prior-poll price; single print still fires)
    big = max(ws["buy_60"], ws["sell_60"])
    small = min(ws["buy_60"], ws["sell_60"])
    ref = ws["px_pre60"] if ws["px_pre60"] is not None else prev_px
    if big >= BITE_NOTIONAL and big >= 3 * small \
            and (ref is None or abs(ws["px_last"] - ref) >= BITE_MOVE):
        out.append(("bite", _sign(ws["buy_60"] - ws["sell_60"]),
                    round(big / BITE_NOTIONAL, 2)))
    # stampede: print-rate spike; direction = net taker flow
    if ws["n_5m"] >= STAMPEDE_PRINTS_5M:
        out.append(("stampede", _sign(ws["buy_5m"] - ws["sell_5m"]),
                    round(ws["n_5m"] / STAMPEDE_PRINTS_5M, 2)))
    # run: sustained one-direction move
    if ws["px_5m"] is not None:
        move = ws["px_last"] - ws["px_5m"]
        if abs(move) >= RUN_MOVE_5M:
            out.append(("run", _sign(move), round(abs(move) / RUN_MOVE_5M, 2)))
    return out


def edge_and_cooldown(st, trig, active, now):
    """ONSET-ONLY emission decision (review finding 1): returns True only on
    a False->True condition edge that is also past the cooldown. Mutates st
    (on_<trig> latch + cd_<trig> stamp). Pure w.r.t. everything else."""
    was_on = bool(st.get("on_" + trig))
    st["on_" + trig] = active
    if not active or was_on:
        return False
    cd = st.get("cd_" + trig)
    if cd is not None and now - float(cd) < COOLDOWN_S:
        return False
    st["cd_" + trig] = now
    return True


def run(base):
    # the feed dir must exist and be appendable or the daemon is pointless —
    # die loudly at startup (exit 1 -> systemd on-failure restarts, visible)
    try:
        with open(FEED_PATH, "a"):
            pass
    except OSError as e:
        print(f"FATAL: cannot append {FEED_PATH}: {e}", flush=True)
        return 1
    state_path = os.path.join(base, "state.json")
    state = {}
    if os.path.exists(state_path):
        try:
            state = json.load(open(state_path))
        except Exception:
            try:
                state = json.load(open(state_path + ".bak"))
                print("recovered state from .bak", flush=True)
            except Exception:
                print("STATE unreadable — fresh watermarks (era note!)", flush=True)
    universe = []
    last_universe = 0.0
    last_disk = 0.0
    hb = time.time()
    emitted_total = int(state.get("_emitted", 0))
    emitted_hr = deque()
    trunc_hr = deque()
    capped = None                 # emission-halt reason; daemon stays alive
    print("sensor feed starting (era: v1 thresholds bite %s/%.2fc dom3x, "
          "stampede %d/5m, run %.2fc/5m, onset-only)" %
          (BITE_NOTIONAL, BITE_MOVE * 100, STAMPEDE_PRINTS_5M,
           RUN_MOVE_5M * 100), flush=True)
    while True:
        now = time.time()
        if os.path.exists(os.path.join(base, "STOP")):
            print("STOP sentinel — exiting cleanly", flush=True)
            return 0
        if now - last_disk > 3600:
            last_disk = now
            try:
                size_mb = sum(os.path.getsize(os.path.join(r, f))
                              for r, _, fs in os.walk(base) if "venv" not in r
                              for f in fs) / 1e6
            except OSError:
                size_mb = 0.0
            feed_mb = 0.0
            try:
                feed_mb = os.path.getsize(FEED_PATH) / 1e6
            except OSError:
                pass
            # caps HALT EMISSION, never silently kill the daemon (a clean
            # exit under Restart=on-failure would never come back; review 5)
            if size_mb > MAX_DISK_MB or feed_mb > FEED_MAX_MB:
                capped = f"disk={size_mb:.0f}MB feed={feed_mb:.0f}MB"
            else:
                capped = None
        if now - last_universe > UNIVERSE_REFRESH_S or not universe:
            u = load_universe()
            if u:
                universe = u
                live = set(m["cid"] for m in universe)
                # prune state for markets long gone from the union (review 11)
                for cid in [c for c, v in state.items()
                            if c.startswith("0x") and c not in live
                            and isinstance(v, dict)
                            and now - max([float(v.get("wm", 0))] +
                                          [float(v.get(k, 0)) for k in v
                                           if k.startswith("cd_")]) > 86400]:
                    state.pop(cid, None)
                print(f"universe: {len(universe)} markets "
                      f"(union of arm universes)", flush=True)
            last_universe = now

        from concurrent.futures import ThreadPoolExecutor, as_completed
        events = []
        n_trunc = n_timeout = 0
        # budget shed: near exhaustion, single-page fetches only (review 6)
        pages = 1 if http_used() > 0.8 * HTTP_BUDGET_PER_HOUR else 2
        ex = ThreadPoolExecutor(max_workers=8)
        futs = {ex.submit(fetch_tape, m["cid"],
                          float(state.get(m["cid"], {}).get("wm", now - 300)),
                          pages): m
                for m in universe}
        try:
            for fu in as_completed(futs, timeout=50):
                m = futs[fu]
                try:
                    tape, trunc = fu.result()
                except Exception:
                    continue
                n_trunc += 1 if trunc else 0
                st = state.setdefault(m["cid"], {})
                ws = window_stats(tape, m["yes"], now)
                prev_px = st.get("last_px")
                evs = {t: (d, i) for t, d, i in evaluate(ws, prev_px)}
                if ws["px_last"] is not None:
                    st["last_px"] = ws["px_last"]
                for trig in ("bite", "stampede", "run"):
                    if edge_and_cooldown(st, trig, trig in evs, now):
                        d, inten = evs[trig]
                        events.append({"t": round(now), "market_id": m["id"],
                                       "cid": m["cid"], "trigger": trig,
                                       "direction": d, "intensity": inten,
                                       "px": ws["px_last"],
                                       "ts_px_last": ws["ts_last"],
                                       "sector": m["sector"], "q": m["q"]})
                for tr in tape:
                    try:
                        st["wm"] = max(float(st.get("wm", 0)),
                                       float(tr.get("timestamp")))
                    except (TypeError, ValueError):
                        continue
        except TimeoutError:
            n_timeout = sum(1 for fu in futs if not fu.done())
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        if events and not capped:
            try:
                with open(FEED_PATH, "a") as f:
                    for e in events:
                        f.write(json.dumps(e) + "\n")
                emitted_total += len(events)
                emitted_hr.append((now, len(events)))
            except OSError as e:
                print(f"feed write FAILED: {e}", flush=True)
        while emitted_hr and now - emitted_hr[0][0] > 3600:
            emitted_hr.popleft()
        trunc_hr.append((now, n_trunc))
        while trunc_hr and now - trunc_hr[0][0] > 3600:
            trunc_hr.popleft()
        state["_emitted"] = emitted_total
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        if os.path.exists(state_path):
            try:
                os.replace(state_path, state_path + ".bak")
            except OSError:
                pass
        os.replace(tmp, state_path)
        poll_s = time.time() - now
        if capped:
            print(f"WARN: EMISSION HALTED ({capped}) — daemon alive, "
                  f"rotate/clear to resume", flush=True)
        if time.time() - hb > 300:
            hb = time.time()
            print(f"hb: markets={len(universe)} emitted_hr="
                  f"{sum(n for _, n in emitted_hr)} emitted_total={emitted_total}"
                  f" trunc_hr={sum(n for _, n in trunc_hr)}"
                  f" timeouts={n_timeout} poll_s={poll_s:.0f}"
                  f" http_hr={http_used()}/{HTTP_BUDGET_PER_HOUR}",
                  flush=True)
        time.sleep(max(1.0, 60 - poll_s))


def report(base=None):
    try:
        lines = open(FEED_PATH).read().splitlines()
    except OSError:
        print("no feed file yet")
        return
    by = defaultdict(int)
    by_sec = defaultdict(int)
    for ln in lines:
        try:
            e = json.loads(ln)
        except Exception:
            continue
        by[e.get("trigger")] += 1
        by_sec[(e.get("sector"), e.get("trigger"))] += 1
    print(f"feed events: {len(lines)} total; by trigger: {dict(by)}")
    print("by sector x trigger:")
    for k in sorted(by_sec, key=lambda k: -by_sec[k]):
        print("  %-14s %-9s %d" % (k[0], k[1], by_sec[k]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--base", default="/opt/pa2-maker-sensor")
    a = ap.parse_args()
    if a.report:
        report(a.base)
    elif a.run:
        sys.exit(run(a.base))
    else:
        print("need --run or --report")
