#!/usr/bin/env python3
"""Self-contained tests for polymarket_collector's pure parsing — stdlib asserts only.

Run:  python -m esports_silo.collectors.test_polymarket_collector   (exit 0 = all pass)
The network fetch + DB write are a marked SEAM (need the box); the parsing logic below is
what determines what gets stored, so it is what we test.
"""
from __future__ import annotations

try:
    from . import polymarket_collector as c
except ImportError:
    import polymarket_collector as c  # type: ignore

FAILS = []
TS = "2026-07-02T00:00:00+00:00"


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


# --- classify_game: content-based, never the category tag (Cmd 5) --------------------
check("cs2 by name", c.classify_game("Will NAVI beat FaZe in the CS2 Major?") == "cs2")
check("counter-strike phrase", c.classify_game("Counter-Strike Major winner") == "cs2")
check("valorant", c.classify_game("VCT Champions: Sentinels vs Fnatic") == "valorant")
check("dota", c.classify_game("The International 2026: Team Spirit vs Falcons") == "dota2")
check("lol by league", c.classify_game("LCK Spring: T1 vs Gen.G") == "lol")
# politics / non-esports must NOT classify (the pollution the old category tag let through)
check("politics -> None", c.classify_game("Will the incumbent win the 2026 election?") is None)
check("empty -> None", c.classify_game("") is None)
# short-token guard: 'lol' inside 'lollapalooza' must not match as League
check("short token 'lol' not matched inside a word", c.classify_game("lollapalooza lineup") is None)
check("bare 'ti' not matched inside a word", c.classify_game("titans vs giants basketball") is None)

# --- outcomes/prices parsing: JSON-string AND real-list shapes -----------------------
mkt_str = {"id": "0xabc", "question": "NAVI vs FaZe — CS2",
           "outcomes": '["NAVI","FaZe"]', "outcomePrices": '["0.62","0.38"]',
           "volume24hr": "12345.6"}
ta, tb, yp = c.parse_outcomes_prices(mkt_str)
check("outcomes from JSON string", ta == "NAVI" and tb == "FaZe")
check("yes_price = first outcome price", abs(yp - 0.62) < 1e-9)
mkt_list = {"id": "1", "outcomes": ["T1", "Gen.G"], "outcomePrices": [0.55, 0.45]}
ta2, tb2, yp2 = c.parse_outcomes_prices(mkt_list)
check("outcomes from real list", ta2 == "T1" and tb2 == "Gen.G" and abs(yp2 - 0.55) < 1e-9)
check("malformed prices -> None yes_price",
      c.parse_outcomes_prices({"outcomes": ["a", "b"], "outcomePrices": "not json"})[2] is None)

# --- parse_market: full record, deterministic snapshot_time --------------------------
rec = c.parse_market(mkt_str, TS)
check("parse_market builds record", rec is not None and rec["market_id"] == "0xabc")
check("parse_market game classified", rec["game"] == "cs2")
check("parse_market yes_price", abs(rec["yes_price"] - 0.62) < 1e-9)
check("parse_market volume from live field", abs(rec["volume_24h"] - 12345.6) < 1e-9)
check("parse_market uses passed snapshot_time (pure)", rec["snapshot_time"] == TS)

# non-esports market -> skipped (None), not guessed
politics = {"id": "9", "question": "2026 election winner?", "outcomes": ["Dem", "Rep"],
            "outcomePrices": ["0.5", "0.5"]}
check("non-esports market skipped", c.parse_market(politics, TS) is None)
# missing id -> None
check("missing id -> None", c.parse_market({"question": "NAVI vs FaZe CS2"}, TS) is None)
# conditionId fallback for id
rec2 = c.parse_market({"conditionId": "0xff", "question": "T1 vs Gen.G LCK",
                       "outcomes": ["T1", "Gen.G"], "outcomePrices": ["0.5", "0.5"]}, TS)
check("conditionId used as market_id", rec2 is not None and rec2["market_id"] == "0xff")

# --- volume field fallbacks ----------------------------------------------------------
check("volume_24h fallback key", c.parse_market(
    {"id": "v", "question": "CS2: NAVI vs FaZe", "outcomes": ["NAVI", "FaZe"],
     "outcomePrices": ["0.5", "0.5"], "volume_24h": 999.0}, TS)["volume_24h"] == 999.0)
check("missing volume -> None (not 0 — $0 stored volume is banned)", c.parse_market(
    {"id": "v2", "question": "CS2: NAVI vs FaZe", "outcomes": ["NAVI", "FaZe"],
     "outcomePrices": ["0.5", "0.5"]}, TS)["volume_24h"] is None)

if __name__ == "__main__":
    import sys
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
