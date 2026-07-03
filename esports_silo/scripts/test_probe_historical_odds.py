#!/usr/bin/env python3
"""Tests for the historical-odds parser (pure) — stdlib asserts only.

Run:  python -m esports_silo.scripts.test_probe_historical_odds   (exit 0 = all pass)
Shape is WEB-SOURCED; these lock the parser's behavior against that documented shape so a real
payload only needs field-name tweaks, not a rewrite.
"""
from __future__ import annotations

try:
    from . import probe_historical_odds as h
except ImportError:
    import probe_historical_odds as h  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def approx(a, b, t=1e-9):
    return a is not None and b is not None and abs(a - b) <= t


PAYLOAD = {
    "fixtureId": "id123",
    "bookmakers": {
        "pinnacle": {"markets": [{"key": "moneyline", "outcomes": [
            {"name": "NAVI", "odds": [
                {"price": 1.90, "createdAt": "2026-07-10T08:00:00Z"},
                {"price": 1.85, "createdAt": "2026-07-10T13:30:00Z"},   # closing (before 14:00)
                {"price": 1.40, "createdAt": "2026-07-10T14:30:00Z"}]},  # AFTER start — must be excluded
            {"name": "FaZe", "odds": [
                {"price": 2.00, "createdAt": "2026-07-10T08:00:00Z"},
                {"price": 2.10, "createdAt": "2026-07-10T13:30:00Z"}]}]}]},
        "singbet": {"markets": [{"key": "match_winner", "outcomes": [
            {"name": "NAVI", "odds": [{"price": 1.88, "createdAt": "2026-07-10T13:45:00Z"}]},
            {"name": "FaZe", "odds": [{"price": 2.05, "createdAt": "2026-07-10T13:45:00Z"}]}]}]},
    },
}
START = "2026-07-10T14:00:00Z"


def test():
    rows = h.parse_historical_odds(PAYLOAD, "NAVI", "FaZe", START)
    by = {r["book"]: r for r in rows}
    check("both books parsed", set(by) == {"pinnacle", "singbet"})
    # pinnacle closing = last price BEFORE 14:00 start (1.85 / 2.10), NOT the 14:30 post-start move
    check("closing = last pre-start price (no look-ahead)",
          approx(by["pinnacle"]["team_a_odds"], 1.85) and approx(by["pinnacle"]["team_b_odds"], 2.10))
    check("post-start move excluded", not approx(by["pinnacle"]["team_a_odds"], 1.40))
    check("is_closing flagged", by["pinnacle"]["is_closing"] is True)
    check("n_moves counted", by["pinnacle"]["n_moves"] == 3)
    check("line_time is the closing timestamp", by["pinnacle"]["line_time"].startswith("2026-07-10T13:30"))
    check("singbet parsed", approx(by["singbet"]["team_a_odds"], 1.88))

    # team alignment is by NAME, not order: swap outcome order, still correct
    swapped = {"bookmakers": {"pinnacle": {"markets": [{"key": "moneyline", "outcomes": [
        {"name": "FaZe", "odds": [{"price": 2.10, "createdAt": "2026-07-10T13:30:00Z"}]},
        {"name": "NAVI", "odds": [{"price": 1.85, "createdAt": "2026-07-10T13:30:00Z"}]}]}]}}}
    r = h.parse_historical_odds(swapped, "NAVI", "FaZe", START)[0]
    check("name alignment (not positional)", approx(r["team_a_odds"], 1.85) and approx(r["team_b_odds"], 2.10))

    # no start_time -> take the latest available (still excludes nothing, just 'last')
    r2 = {r["book"]: r for r in h.parse_historical_odds(PAYLOAD, "NAVI", "FaZe", None)}
    check("no start_time -> latest overall", approx(r2["pinnacle"]["team_a_odds"], 1.40))

    # bookmakers as a LIST shape (alt) also handled
    listshape = {"bookmakers": [{"slug": "pinnacle", "markets": [{"key": "ml", "outcomes": [
        {"name": "NAVI", "odds": [{"price": 1.7, "createdAt": "2026-07-10T13:00:00Z"}]},
        {"name": "FaZe", "odds": [{"price": 2.3, "createdAt": "2026-07-10T13:00:00Z"}]}]}]}]}
    check("list-shaped bookmakers handled",
          approx(h.parse_historical_odds(listshape, "NAVI", "FaZe", START)[0]["team_a_odds"], 1.7))

    # missing market -> error flag, not a guess
    bad = {"bookmakers": {"pinnacle": {"markets": []}}}
    check("no market -> error flag (never guessed)",
          h.parse_historical_odds(bad, "NAVI", "FaZe", START)[0].get("error") is not None)

    # flat price on the outcome (no series) is tolerated
    flat = {"bookmakers": {"x": {"markets": [{"key": "ml", "outcomes": [
        {"name": "NAVI", "price": 1.5}, {"name": "FaZe", "price": 2.6}]}]}}}
    check("flat outcome price tolerated", approx(
        h.parse_historical_odds(flat, "NAVI", "FaZe", START)[0]["team_a_odds"], 1.5))




def test_real_schema():
    """REAL schema tests — built from the actual /v4/odds payload (live, 2026-07-03)."""
    # paf's market 171 from the real payload: 171->1.32 (Mibr Academy), 172->2.95 (Vexa)
    real = {"fixtureId": "id1705104272533136", "bookmakerOdds": {
        "paf": {"suspended": False, "markets": {
            "171": {"marketActive": True, "outcomes": {
                "171": {"players": {"0": {"price": 1.32, "changedAt": "2026-07-03T15:25:23.623Z"}}},
                "172": {"players": {"0": {"price": 2.95, "changedAt": "2026-07-03T15:25:23.623Z"}}}}},
            "1747": {"marketActive": True, "outcomes": {  # map-1 market must be IGNORED
                "1747": {"players": {"0": {"price": 1.49, "changedAt": "2026-07-03T15:25:23.623Z"}}},
                "1748": {"players": {"0": {"price": 2.55, "changedAt": "2026-07-03T15:25:23.623Z"}}}}}}},
        "polymarket": {"suspended": False, "markets": {
            "171": {"marketActive": True, "outcomes": {
                "171": {"players": {"0": {"price": 1.515, "changedAt": "2026-07-03T23:31:09.826Z",
                                          "exchangeMeta": {"back": [], "lay": []}}}},
                "172": {"players": {"0": {"price": 2.857, "changedAt": "2026-07-03T23:31:09.826Z"}}}}}}},
        "deadbook": {"suspended": True, "markets": {"171": {"outcomes": {}}}},
        "nomarket": {"suspended": False, "markets": {"999": {"outcomes": {
            "998": {"players": {"0": {"price": 1.5}}}}}}},
    }}
    rows = h.parse_historical_odds(real, "Mibr Academy", "Vexa", "2026-07-04T16:00:00Z")
    by = {r["book"]: r for r in rows}
    check("REAL: paf 171/172 -> team_a/team_b by ID", approx(by["paf"]["team_a_odds"], 1.32)
          and approx(by["paf"]["team_b_odds"], 2.95))
    check("REAL: map market (1747) ignored, match market (171) used",
          not approx(by["paf"]["team_a_odds"], 1.49))
    check("REAL: polymarket (exchange) parsed like a book", approx(by["polymarket"]["team_a_odds"], 1.515))
    check("REAL: suspended book flagged", by["deadbook"].get("error") == "suspended")
    check("REAL: book without 171 market flagged", "171" in str(by["nomarket"].get("error")))
    check("REAL: line_time from changedAt", by["paf"]["line_time"].startswith("2026-07-03T15:25"))

    # expected HISTORICAL shape: a series per player -> closing = last pre-start point
    hist = {"bookmakerOdds": {"pinnacle": {"suspended": False, "markets": {
        "171": {"outcomes": {
            "171": {"players": {"0": {"odds": [
                {"price": 1.90, "createdAt": "2026-07-04T08:00:00Z"},
                {"price": 1.85, "createdAt": "2026-07-04T15:30:00Z"},
                {"price": 1.40, "createdAt": "2026-07-04T16:30:00Z"}]}}},   # post-start: excluded
            "172": {"players": {"0": {"odds": [
                {"price": 2.10, "createdAt": "2026-07-04T15:30:00Z"}]}}}}}}}}}
    r = h.parse_historical_odds(hist, "A", "B", "2026-07-04T16:00:00Z")[0]
    check("REAL-hist: series closing = last pre-start", approx(r["team_a_odds"], 1.85))
    check("REAL-hist: post-start move excluded", not approx(r["team_a_odds"], 1.40))
    check("REAL-hist: n_moves counted from series", r["n_moves"] == 3)


if __name__ == "__main__":
    import sys
    test()
    test_real_schema()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
else:
    test()
    test_real_schema()
