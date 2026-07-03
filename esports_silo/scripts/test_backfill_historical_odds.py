#!/usr/bin/env python3
"""Tests for the backfill's PURE parts (windows / linking / row shaping) — stdlib asserts.

Run:  python -m esports_silo.scripts.test_backfill_historical_odds   (exit 0 = all pass)
The I/O loop is operator-box-only; everything decision-bearing is tested here, including
the two silent-corruption risks: orientation swap and the honest is_closing rule.
"""
from __future__ import annotations

try:
    from . import backfill_historical_odds as bf
except ImportError:
    import backfill_historical_odds as bf  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def approx(a, b, t=1e-9):
    return a is not None and b is not None and abs(a - b) <= t


def test_windows():
    w = bf.windows("2026-01-01", "2026-01-20")
    check("windows cover the whole range", w[0][0] == "2026-01-01" and w[-1][1] == "2026-01-20")
    check("every window ≤ 9 days apart (API hard limit)", all(
        (bf.date.fromisoformat(t) - bf.date.fromisoformat(f)).days <= 9 for f, t in w))
    check("windows are contiguous (boundary shared, deduped by fixtureId)",
          all(w[i][1] == w[i + 1][0] for i in range(len(w) - 1)))
    check("single-day range -> one window", bf.windows("2026-01-05", "2026-01-05") ==
          [("2026-01-05", "2026-01-05")])
    check("exact 9-day range -> one window", bf.windows("2026-01-01", "2026-01-10") ==
          [("2026-01-01", "2026-01-10")])
    try:
        bf.windows("2026-01-10", "2026-01-01")
        check("reversed range raises", False)
    except ValueError:
        check("reversed range raises", True)


FIX = {"fixtureId": "id1703162167643464", "participant1Name": "Fnatic",
       "participant2Name": "Astralis", "startTime": "2026-01-13T16:00:00.000Z"}
ALIASES = {"fnatic": "fnatic", "fnc": "fnatic", "astralis": "astralis", "ast": "astralis"}


def test_linking():
    # exact names, direct orientation
    cands = [{"match_id": "ps_1", "team_a": "Fnatic", "team_b": "Astralis",
              "start_time": "2026-01-13T16:00:00+00:00"}]
    link = bf.link_fixture(FIX, cands, ALIASES)
    check("exact direct link", link and link["match_id"] == "ps_1" and link["swapped"] is False)

    # swapped orientation MUST be detected (else every row is inverted)
    cands = [{"match_id": "ps_2", "team_a": "Astralis", "team_b": "Fnatic",
              "start_time": "2026-01-13T16:00:00+00:00"}]
    link = bf.link_fixture(FIX, cands, ALIASES)
    check("swapped orientation detected", link and link["swapped"] is True)

    # alias resolution: silo stores canonical, fixture uses an alias
    fx = dict(FIX, participant1Name="FNC", participant2Name="AST")
    cands = [{"match_id": "ps_3", "team_a": "Fnatic", "team_b": "Astralis",
              "start_time": "2026-01-13T16:00:00+00:00"}]
    link = bf.link_fixture(fx, cands, ALIASES)
    check("alias-canonicalized link", link and link["match_id"] == "ps_3"
          and approx(link["score"], 100.0))

    # unrelated teams do NOT link
    cands = [{"match_id": "ps_4", "team_a": "Natus Vincere", "team_b": "Team Vitality",
              "start_time": "2026-01-13T16:00:00+00:00"}]
    check("unrelated teams rejected", bf.link_fixture(FIX, cands, ALIASES) is None)

    # ONE team matching is not enough (two-team gate)
    cands = [{"match_id": "ps_5", "team_a": "Fnatic", "team_b": "Team Vitality",
              "start_time": "2026-01-13T16:00:00+00:00"}]
    check("single-team match rejected", bf.link_fixture(FIX, cands, ALIASES) is None)

    # rematch disambiguation: same teams twice, closer start wins
    cands = [{"match_id": "ps_far", "team_a": "Fnatic", "team_b": "Astralis",
              "start_time": "2026-01-13T04:00:00+00:00"},
             {"match_id": "ps_near", "team_a": "Fnatic", "team_b": "Astralis",
              "start_time": "2026-01-13T15:30:00+00:00"}]
    link = bf.link_fixture(FIX, cands, ALIASES)
    check("rematch: closer start_time wins", link and link["match_id"] == "ps_near")


def test_shape_rows():
    parsed = [
        {"book": "pinnacle", "team_a_odds": 2.32, "team_b_odds": 1.617,
         "line_time": "2026-01-13T12:34:38+00:00", "is_closing": True, "n_moves": 300},
        {"book": "sbobet", "error": "no match-winner (171) market"},
        {"book": "singbet", "team_a_odds": 2.30, "team_b_odds": 1.62,
         "line_time": "2026-01-13T15:45:00+00:00", "is_closing": True, "n_moves": 5},
        {"book": "unrequested", "team_a_odds": 2.0, "team_b_odds": 2.0,
         "line_time": "2026-01-13T15:45:00+00:00", "is_closing": True, "n_moves": 1},
        {"book": "22bet", "team_a_odds": 2.1, "team_b_odds": 1.7,
         "line_time": None, "is_closing": True, "n_moves": 1},
    ]
    books = ["pinnacle", "singbet", "sbobet", "22bet"]
    ok, bad = bf.shape_rows(parsed, books, swapped=False, start_time="2026-01-13T16:00:00Z")
    by = {r["book"]: r for r in ok}
    check("error row diverted, not written", all(r["book"] != "sbobet" for r in ok)
          and any(b.get("book") == "sbobet" for b in bad))
    check("unrequested book dropped", "unrequested" not in by)
    check("null line_time diverted (NOT NULL column)", "22bet" not in by
          and any(b.get("error", "").startswith("unparseable") for b in bad))
    # HONEST is_closing: T-3h26m tick is NOT closing; T-15m tick is (30-min rule,
    # same as the forward collector). The parser's own is_closing=True is overridden.
    check("stale tick (T-3h26m) NOT flagged closing", by["pinnacle"]["is_closing"] is False)
    check("T-15m tick flagged closing", by["singbet"]["is_closing"] is True)
    check("staleness recorded (minutes)", approx(by["pinnacle"]["stale_min"], 205.366666, 1e-3))
    check("odds carried unswapped", approx(by["pinnacle"]["team_a_odds"], 2.32))

    # swapped orientation: odds flip so team_a_odds refers to the SILO match's team_a
    ok2, _ = bf.shape_rows(parsed[:1], books, swapped=True, start_time="2026-01-13T16:00:00Z")
    check("swap applied to odds", approx(ok2[0]["team_a_odds"], 1.617)
          and approx(ok2[0]["team_b_odds"], 2.32))


def test_team_score():
    check("canonical equality -> 100", approx(bf.team_score("FNC", "Fnatic", ALIASES), 100.0))
    check("case/space tolerant", approx(bf.team_score(" fnatic ", "FNATIC", {}), 100.0))
    check("empty name -> 0", bf.team_score("", "Fnatic", {}) == 0.0)
    check("different teams score low", bf.team_score("Fnatic", "Astralis", {}) < 80.0)


if __name__ == "__main__":
    import sys
    test_windows()
    test_linking()
    test_shape_rows()
    test_team_score()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
else:
    test_windows()
    test_linking()
    test_shape_rows()
    test_team_score()
