#!/usr/bin/env python3
"""OFFLINE PROSPECTIVE-CAPTURE SWEEP — warm the shadow rank with MEASURED books today, instead
of waiting a day for the live loop's exploration quota to crawl the venue.

WHY (operator ask 2026-07-29): the capital-aware shadow rank starts blind — the live score
cache only holds books the trading loop actually read, so every other market scores on its
pool prior ("stale"/"unknown" kind) and the first shadow rows ranked pool-guesses above
receipts. This script reads candidate orderbooks OFFLINE and computes the capture we WOULD
get if we joined each book at the reference price at our standard size.

ZERO-DRIFT BY CONSTRUCTION: the capture math is not re-implemented — each book is fed through
the quoter's own `_market_telemetry_row` (the exact function the live loop uses to score its
own books), with a hypothetical join priced at reference and sized by the quoter's own
`_capped_join`. If the quoter's capture model changes, this inherits it automatically.

SEPARATE FILE, NOT THE LIVE CACHE: results go to kalshi_prospective_capture.json, which ONLY
the caprank telemetry reads. Writing into kalshi_market_scores.json would race the quoter
(it saves its in-memory cache over that file every cycle and never re-reads it after import),
so the live ranking is structurally untouchable from here.

READ-ONLY against the venue; paced (SPACING_S between book reads) and bounded (top N candidate
markets by pool $/day). Run ad hoc or on a timer:
  cd /opt/pa2-maker-kalshi-live && set -a && . ./live.env && set +a && \
  venv/bin/python kalshi_capture_sweep.py [top_n]
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.request

SCHEMA = 1
SPACING_S = 0.35            # pacing between public orderbook reads (venue courtesy, not auth)
DEFAULT_TOP_N = 150


def candidate_rows(progs, top_n):
    """Top-N candidate markets by pool $/day from the active liquidity programs — the same
    fields select_footprint builds its rows from (R1 canon: period_reward/10000 IS the daily
    per-market pool; never divide by window length)."""
    rows = []
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        t = p.get("market_ticker")
        if not t or p.get("target_size_fp") is None or p.get("discount_factor_bps") is None:
            continue
        rows.append({"ticker": t, "usd_day": (p.get("period_reward") or 0) / 10000,
                     "target": float(p["target_size_fp"]),
                     "df": (p["discount_factor_bps"] / 10000) or 0.5})
    rows.sort(key=lambda r: (-r["usd_day"], r["ticker"]))
    return rows[:top_n]


def prospective_capture(q, row, yes_raw, no_raw, now):
    """Capture we WOULD earn joining this book at reference, at our live sizing. Delegates
    everything to the quoter's own functions — see the zero-drift note in the module doc."""
    (yl, _), (nl, _) = q._levels(yes_raw), q._levels(no_raw)
    best_y = max((p for p, _ in yl), default=None)
    best_n = max((p for p, _ in nl), default=None)
    if best_y is None or best_n is None:
        return 0.0, best_y                     # one-sided/empty book: R3 pays $0 to everyone
    quotes = [{"side": "yes", "price_dollars": best_y,
               "count": q._capped_join(best_y, best_n)},
              {"side": "no", "price_dollars": best_n,
               "count": q._capped_join(best_n, best_y)}]
    m = {"ticker": row["ticker"], "usd_day": row["usd_day"],
         "target": row["target"], "df": row["df"]}
    trow = q._market_telemetry_row(0, now, m, yl, nl, quotes, None, 0.0, None)
    return float(trow.get("capture_usd_day") or 0.0), trow.get("y_ref")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import maker_kalshi_quoter as q
    from maker_kalshi_client import KalshiOrderClient
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TOP_N
    progs, cursor = [], ""
    while True:
        u = ("https://external-api.kalshi.com/trade-api/v2/incentive_programs"
             "?status=active&limit=10000" + (("&cursor=" + cursor) if cursor else ""))
        d = json.load(urllib.request.urlopen(u, timeout=30))
        progs.extend(d.get("incentive_programs", []))
        # next_cursor, NOT "cursor": the incentive_programs family paginates with next_cursor
        # (quoter + daemon both use it); "cursor" is the /portfolio family key. Latent today
        # (limit=10000 covers ~3.3k programs in one page) but self-audit A1-F2 flagged it as
        # the silent page-1-truncation class the moment the venue shrinks page sizes.
        cursor = d.get("next_cursor") or ""
        if not cursor:
            break
    rows = candidate_rows(progs, top_n)
    c = KalshiOrderClient()
    now = q.utcnow()
    out_markets, read_fail = {}, 0
    for r in rows:
        try:
            # orderbook_fp, NOT the legacy "orderbook" envelope — same key the quoter's own
            # REST path reads (maker_kalshi_quoter._get_book); plain-name fields are the
            # canonical Kalshi API-shape trap and parse as empty books.
            ob = c.get_orderbook(r["ticker"]).get("orderbook_fp") or {}
            cap, ref = prospective_capture(
                q, r, ob.get("yes_dollars") or [], ob.get("no_dollars") or [], now)
            out_markets[r["ticker"]] = {"capture": round(cap, 4), "ref": ref,
                                        "pool_usd_day": r["usd_day"],
                                        "ts": now.timestamp()}
        except Exception:
            read_fail += 1                     # a bad book is skipped, never fatal
        time.sleep(SPACING_S)
    out = os.path.join(here, "kalshi_prospective_capture.json")
    tmp = out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"schema": SCHEMA, "markets": out_markets}, fh, separators=(",", ":"))
    os.replace(tmp, out)
    ranked = sorted(out_markets.items(), key=lambda kv: -kv[1]["capture"])
    print(f"{now.isoformat()} wrote {out}: {len(out_markets)} markets swept "
          f"({read_fail} read failures) of {len(rows)} candidates")
    for t, v in ranked[:15]:
        print(f"  {t:44s} prospective ${v['capture']:8.2f}/day  "
              f"(pool ${v['pool_usd_day']:.0f}, ref {v['ref']})")


if __name__ == "__main__":
    main()
