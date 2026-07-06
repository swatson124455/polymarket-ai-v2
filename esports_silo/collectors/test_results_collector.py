#!/usr/bin/env python3
"""Self-contained tests for the results collector's pure parsing — stdlib asserts only.

Run:  python -m esports_silo.collectors.test_results_collector   (exit 0 = all pass)
"""
from __future__ import annotations

try:
    from . import results_collector as rc
except ImportError:
    import results_collector as rc  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


# a realistic finished-match payload (PandaScore-ish shape)
MATCH = {
    "id": 1015039,
    "status": "finished",
    "begin_at": "2024-08-01T14:00:00Z",
    "opponents": [
        {"opponent": {"id": 11, "name": "NAVI"}},
        {"opponent": {"id": 22, "name": "FaZe"}},
    ],
    "winner": {"id": 22, "name": "FaZe"},
    "results": [{"team_id": 11, "score": 1}, {"team_id": 22, "score": 2}],
}


def test():
    r = rc.parse_result(MATCH, "cs2")
    check("match_id namespaced ps_<id>", r["match_id"] == "ps_1015039")
    check("teams parsed", r["team_a"] == "NAVI" and r["team_b"] == "FaZe")
    check("winner mapped to team_b (FaZe)", r["winner"] == "team_b")
    check("scores mapped by team_id (a=1, b=2)", r["score_a"] == 1 and r["score_b"] == 2)
    check("start_time carried", r["start_time"] == "2024-08-01T14:00:00Z")
    check("status finished", r["status"] == "finished")

    # winner as team_a
    m2 = dict(MATCH, winner={"id": 11, "name": "NAVI"})
    check("winner team_a", rc.parse_result(m2, "cs2")["winner"] == "team_a")

    # winner is a bare string (not an object) — still maps
    m3 = dict(MATCH, winner="FaZe")
    check("winner as bare string maps", rc.parse_result(m3, "cs2")["winner"] == "team_b")

    # unmappable winner -> NULL, but raw kept for the 'unresolved' counter
    m4 = dict(MATCH, winner={"name": "Some Third Team"})
    r4 = rc.parse_result(m4, "cs2")
    check("unmappable winner -> None (never guessed)", r4["winner"] is None)
    check("raw winner name kept for triage", r4["winner_name_raw"] == "Some Third Team")

    # no winner yet (live/unsettled shape) -> winner None, no crash
    m5 = dict(MATCH); m5.pop("winner")
    check("missing winner -> None", rc.parse_result(m5, "cs2")["winner"] is None)

    # missing id -> None
    check("missing id -> None", rc.parse_result({"opponents": MATCH["opponents"]}, "cs2") is None)
    # only one team -> None (can't map)
    check("one team only -> None", rc.parse_result(
        {"id": 5, "opponents": [{"opponent": {"name": "Solo"}}]}, "cs2") is None)
    # garbage results rows dropped, no crash
    m6 = dict(MATCH, results=[None, {"team_id": 11, "score": 3}, "x"])
    check("garbage results dropped", rc.parse_result(m6, "cs2")["score_a"] == 3)
    # missing scores entirely -> None scores, still parses
    m7 = dict(MATCH); m7.pop("results")
    r7 = rc.parse_result(m7, "cs2")
    check("no results -> scores None but winner still set", r7["score_a"] is None
          and r7["winner"] == "team_b")

    # --- orient_winner: attach a PandaScore result to another source's match row ------
    am = {}
    # same orientation, exact names -> winner unchanged
    check("orient same orientation",
          rc.orient_winner("team_a", "FaZe", "NAVI", "FaZe", "NAVI", am) == "team_a")
    # row stores teams swapped -> winner flips
    check("orient swapped flips winner",
          rc.orient_winner("team_a", "FaZe", "NAVI", "NAVI", "FaZe", am) == "team_b")
    # name variants (substring) still correspond
    check("orient name variants",
          rc.orient_winner("team_b", "E WIE EINFACH", "Orange",
                           "E WIE EINFACH E-SPORTS", "Team Orange Gaming", am) == "team_b")
    # different fixture sharing ONE team -> None (never guess)
    check("orient different fixture -> None",
          rc.orient_winner("team_a", "Bilibili Gaming", "LYON",
                           "Lyon", "Secret Whales", am) is None)
    # alias map bridges a rebrand
    am2 = {"kiwoom drx": ["drx"]}
    check("orient via alias",
          rc.orient_winner("team_a", "Kiwoom DRX", "T1", "DRX", "T1", am2) == "team_a")
    # winner None / unmappable in -> None out
    check("orient None winner -> None",
          rc.orient_winner(None, "A", "B", "A", "B", am) is None)


if __name__ == "__main__":
    import sys
    test()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
else:
    test()
