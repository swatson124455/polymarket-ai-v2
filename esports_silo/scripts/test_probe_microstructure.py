#!/usr/bin/env python3
"""Self-contained tests for the microstructure probe's pure math — stdlib asserts only.

Run:  python -m esports_silo.scripts.test_probe_microstructure   (exit 0 = all pass)
"""
from __future__ import annotations

try:
    from . import probe_market_microstructure as p
except ImportError:
    import probe_market_microstructure as p  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def approx(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


# --- book_stats: hand-computed --------------------------------------------------------
bids = [{"price": "0.55", "size": "100"}, {"price": "0.52", "size": "200"}, {"price": "0.40", "size": "999"}]
asks = [{"price": "0.60", "size": "50"}, {"price": "0.64", "size": "80"}, {"price": "0.90", "size": "999"}]
s = p.book_stats(bids, asks)
check("best bid 0.55 / ask 0.60", s["best_bid"] == 0.55 and s["best_ask"] == 0.60)
check("spread 0.05", approx(s["spread"], 0.05))
check("mid 0.575", approx(s["mid"], 0.575))
check("two_sided", s["two_sided"] is True)
# bid depth within 5c of 0.55: 0.55x100 + 0.52x200 = 55+104 = 159 (0.40 excluded)
check("bid depth 5c = 159.0", approx(s["bid_depth_usd_5c"], 159.0))
# ask depth within 5c of 0.60: 0.60x50 + 0.64x80 = 30+51.2 = 81.2 (0.90 excluded)
check("ask depth 5c = 81.2", approx(s["ask_depth_usd_5c"], 81.2))

# unsorted input is handled (sorts internally)
s2 = p.book_stats(list(reversed(bids)), list(reversed(asks)))
check("unsorted levels -> same touch", s2["best_bid"] == 0.55 and s2["best_ask"] == 0.60)

# one-sided book -> spread/mid None, not fabricated
s3 = p.book_stats(bids, [])
check("one-sided: no spread/mid", s3["spread"] is None and s3["mid"] is None
      and s3["two_sided"] is False)
check("one-sided keeps the real side", s3["best_bid"] == 0.55 and s3["best_ask"] is None)

# the doc-sourced claim's shape: an 86c-spread book
wide = p.book_stats([{"price": 0.07, "size": 10}], [{"price": 0.93, "size": 10}])
check("wide book measures 0.86 spread", approx(wide["spread"], 0.86))

# garbage levels dropped, never crash
s4 = p.book_stats([{"price": "x", "size": 1}, None, {"price": 0.5}], [{"price": 0.6, "size": "5"}])
check("garbage levels dropped", s4["best_bid"] is None and s4["best_ask"] == 0.6)

# --- summarize ------------------------------------------------------------------------
rows = [
    {"game": "cs2", "two_sided": True, "spread": 0.02, "bid_depth_usd_5c": 100, "ask_depth_usd_5c": 90},
    {"game": "cs2", "two_sided": True, "spread": 0.10, "bid_depth_usd_5c": 10, "ask_depth_usd_5c": 20},
    {"game": "cs2", "two_sided": False, "spread": None, "bid_depth_usd_5c": None, "ask_depth_usd_5c": None},
    {"game": "lol", "two_sided": True, "spread": 0.04, "bid_depth_usd_5c": 50, "ask_depth_usd_5c": 60},
]
sm = p.summarize(rows)
check("cs2 sampled=3, two-sided share 0.667", sm["cs2"]["markets_sampled"] == 3
      and approx(sm["cs2"]["two_sided_share"], 0.667, 1e-3))
check("cs2 median spread 0.06 (of two-sided only)", approx(sm["cs2"]["median_spread"], 0.06))
check("lol median spread 0.04", approx(sm["lol"]["median_spread"], 0.04))
check("empty summarize -> {}", p.summarize([]) == {})

if __name__ == "__main__":
    import sys
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
