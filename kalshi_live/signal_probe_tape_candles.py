#!/usr/bin/env python3
"""SIGNAL INVENTORY probe #2 — characterise the PUBLIC TAPE and CANDLESTICKS.

READ-ONLY, unauthenticated, >=0.35s spacing. New file; edits nothing.

Probe #1 established that these two return 200:
    GET /trade-api/v2/markets/trades
    GET /trade-api/v2/series/{series}/markets/{ticker}/candlesticks
    GET /trade-api/v2/markets/candlesticks   (batch; needs market_tickers)

This probe answers the questions that decide whether they are USABLE:
  * exact field set of a trade (does it carry aggressor side and size?)
  * filter params (ticker / min_ts / max_ts) and pagination depth
  * how far back the tape goes
  * candlestick field set + granularity: does it carry BOOK (bid/ask) or only trades?
  * do they work on OUR series (KXAAAGASD, KXTEMP*) which are thin?
"""
import json
import sys
import time
import urllib.error
import urllib.request

HOST = "https://api.elections.kalshi.com"
ROOT = "/trade-api/v2"
SPACING = 0.35
TIMEOUT = 25
_last = [0.0]


def get(path):
    dt = time.time() - _last[0]
    if dt < SPACING:
        time.sleep(SPACING - dt)
    _last[0] = time.time()
    req = urllib.request.Request(HOST + path,
                                 headers={"User-Agent": "kalshi-signal-probe/1.0",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400]
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode()[:400]


def j(path):
    st, b = get(path)
    if st != 200:
        return st, None, b.decode("utf-8", "replace")[:250]
    try:
        return st, json.loads(b), None
    except Exception as e:  # noqa: BLE001
        return st, None, f"parse fail {e}"


def find_live(prefix, limit=1000):
    """Return open market tickers for a series prefix."""
    st, d, err = j(f"{ROOT}/markets?limit=1000&status=open&series_ticker={prefix}")
    if st != 200:
        return [], f"{st} {err}"
    return [m["ticker"] for m in d.get("markets", [])], None


def main():
    print("=" * 78)
    print("A. FIND LIVE TICKERS IN OUR SERIES")
    print("=" * 78)
    live = {}
    for s in ["KXAAAGASD", "KXAAAGASW", "KXTEMPNYCH", "KXTEMPCHIH", "KXTEMPDCH"]:
        ts, err = find_live(s)
        live[s] = ts
        print(f"  {s:<12} open markets: {len(ts)}  err={err}  sample={ts[:2]}")

    gas = live.get("KXAAAGASD") or []
    temp = (live.get("KXTEMPNYCH") or []) + (live.get("KXTEMPCHIH") or []) \
        + (live.get("KXTEMPDCH") or [])
    if not gas and not temp:
        print("!! no live tickers in our series; aborting")
        return 1

    print()
    print("=" * 78)
    print("B. TRADE TAPE — /markets/trades  (field set, filters, depth, history)")
    print("=" * 78)

    # B1 unfiltered tape: full field set
    st, d, err = j(f"{ROOT}/markets/trades?limit=10")
    print(f"B1 unfiltered  -> {st}")
    if d and d.get("trades"):
        t0 = d["trades"][0]
        print("   TRADE OBJECT FIELDS: " + json.dumps(t0, indent=None, sort_keys=True))
        print("   keys: " + ", ".join(sorted(t0.keys())))
        print(f"   cursor present: {bool(d.get('cursor'))}")

    # B2 does ?ticker= actually filter?
    for tk in ([gas[0]] if gas else []) + ([temp[0]] if temp else []):
        st, d, err = j(f"{ROOT}/markets/trades?ticker={tk}&limit=1000")
        n = len(d.get("trades", [])) if d else 0
        mism = 0
        if d:
            mism = sum(1 for x in d["trades"] if x.get("ticker") != tk)
        print(f"B2 ticker={tk} -> {st}  n={n}  wrong-ticker rows={mism}  err={err}")
        if d and d.get("trades"):
            times = [x["created_time"] for x in d["trades"]]
            print(f"   newest={max(times)}  oldest={min(times)}")
            sizes = sorted(float(x.get("count_fp", x.get("count", 0))) for x in d["trades"])
            print(f"   size min/median/max = {sizes[0]} / {sizes[len(sizes)//2]} / {sizes[-1]}")
            sides = {}
            for x in d["trades"]:
                k = (x.get("taker_side"), x.get("taker_book_side"))
                sides[k] = sides.get(k, 0) + 1
            print(f"   (taker_side, taker_book_side) counts: {sides}")

    # B3 limit ceiling
    for lim in [1000, 5000, 10000]:
        st, d, err = j(f"{ROOT}/markets/trades?limit={lim}")
        print(f"B3 limit={lim:<6} -> {st}  n={len(d.get('trades', [])) if d else 0}  {err or ''}")

    # B4 time filters
    now = int(time.time())
    for q in [f"min_ts={now-3600}", f"max_ts={now}", f"min_ts={now-86400}&max_ts={now}"]:
        st, d, err = j(f"{ROOT}/markets/trades?limit=5&{q}")
        n = len(d.get("trades", [])) if d else 0
        tt = [x["created_time"] for x in d["trades"]] if d and d.get("trades") else []
        print(f"B4 {q:<32} -> {st} n={n} newest={max(tt) if tt else '-'} "
              f"oldest={min(tt) if tt else '-'} {err or ''}")

    # B5 how far back does history go for one gas ticker (paginate)
    if gas:
        tk = gas[0]
        cursor, pages, total, oldest = "", 0, 0, None
        while pages < 6:
            p = f"{ROOT}/markets/trades?ticker={tk}&limit=1000"
            if cursor:
                p += f"&cursor={cursor}"
            st, d, err = j(p)
            if st != 200 or not d:
                print(f"B5 page {pages} -> {st} {err}")
                break
            rows = d.get("trades", [])
            total += len(rows)
            if rows:
                oldest = min([x["created_time"] for x in rows] + ([oldest] if oldest else []))
            cursor = d.get("cursor") or ""
            pages += 1
            if not rows or not cursor:
                break
        print(f"B5 {tk}: paginated {pages} pages, {total} trades, oldest={oldest}")

    # B6 tape volume in OUR series over the last hour vs venue-wide
    print("\nB6 tape density on our series (1 page of 1000, per ticker):")
    for tk in (gas[:4] + temp[:4]):
        st, d, err = j(f"{ROOT}/markets/trades?ticker={tk}&limit=1000")
        rows = d.get("trades", []) if d else []
        if rows:
            times = sorted(x["created_time"] for x in rows)
            print(f"   {tk:<34} n={len(rows):<5} {times[0]} .. {times[-1]}")
        else:
            print(f"   {tk:<34} n=0")

    print()
    print("=" * 78)
    print("C. CANDLESTICKS — field set, granularity, book-vs-trade content")
    print("=" * 78)
    now = int(time.time())
    for tk in ([gas[0]] if gas else []) + ([temp[0]] if temp else []):
        s = tk.split("-")[0]
        for interval in [1, 60, 1440]:
            st, d, err = j(f"{ROOT}/series/{s}/markets/{tk}/candlesticks"
                           f"?start_ts={now-86400}&end_ts={now}&period_interval={interval}")
            cs = d.get("candlesticks", []) if d else []
            print(f"C {tk} interval={interval:<5} -> {st} n={len(cs)} {err or ''}")
            if cs:
                print("   FIRST CANDLE: " + json.dumps(cs[0], sort_keys=True))
                print("   LAST  CANDLE: " + json.dumps(cs[-1], sort_keys=True))
                break

    # C2 batch endpoint
    if gas:
        mt = ",".join(gas[:3])
        st, d, err = j(f"{ROOT}/markets/candlesticks?market_tickers={mt}"
                       f"&start_ts={now-7200}&end_ts={now}&period_interval=1")
        print(f"C2 batch /markets/candlesticks (3 tickers) -> {st} {err or ''}")
        if d:
            print("   top-level keys: " + ", ".join(sorted(d.keys())))
            print("   " + json.dumps(d)[:600])

    # C3 invalid interval -> discover allowed set
    if gas:
        s = gas[0].split("-")[0]
        for interval in [2, 5, 15, 30, 720]:
            st, d, err = j(f"{ROOT}/series/{s}/markets/{gas[0]}/candlesticks"
                           f"?start_ts={now-7200}&end_ts={now}&period_interval={interval}")
            print(f"C3 interval={interval:<5} -> {st} {(err or '')[:120]}")

    print()
    print("=" * 78)
    print("D. MARKET OBJECT — what metadata is on a live contract?")
    print("=" * 78)
    if gas:
        st, d, err = j(f"{ROOT}/markets/{gas[0]}")
        if d:
            m = d["market"]
            print("   keys: " + ", ".join(sorted(m.keys())))
            keep = {k: v for k, v in m.items()
                    if k in ("ticker", "open_time", "close_time", "expiration_time",
                             "volume", "volume_24h", "open_interest", "liquidity",
                             "liquidity_dollars", "last_price_dollars", "yes_bid_dollars",
                             "yes_ask_dollars", "previous_price_dollars", "status",
                             "cap_strike", "floor_strike", "strike_type")}
            print("   " + json.dumps(keep, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
