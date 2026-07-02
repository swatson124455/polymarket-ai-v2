#!/usr/bin/env python3
"""Self-contained tests for match_matcher — stdlib asserts only.

Run:  python -m esports_silo.markets.test_match_matcher   (exit 0 = all pass)
"""
from __future__ import annotations

try:
    from . import match_matcher as mm
except ImportError:
    import match_matcher as mm  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


# --- alias map build -----------------------------------------------------------------
rows = [
    {"alias": "NaVi", "canonical": "Natus Vincere", "game": "cs2"},
    {"alias": "navi", "canonical": "Natus Vincere", "game": "cs2"},   # dup after lc -> collapse
    {"alias": "faze clan", "canonical": "FaZe", "game": "cs2"},
    {"alias": "t1", "canonical": "T1", "game": "lol"},                # alias == canonical -> skip
    {"alias": "skt", "canonical": "T1", "game": "lol"},
]
am = mm.build_alias_map(rows, game="cs2")
check("alias map scopes by game (no lol keys)", "t1" not in am and "natus vincere" in am)
check("alias map dedups lowercased aliases", am["natus vincere"] == ["navi"])
check("alias map drops alias==canonical", "t1" not in mm.build_alias_map(rows, game="lol").get("t1", ["x"]))

# --- team_present: 3 stages ----------------------------------------------------------
am_lol = mm.build_alias_map(rows, game="lol")
check("substring hit -> 100", mm.team_present("T1", "t1 vs gen.g", {}) == (True, 100.0))
ok, sc = mm.team_present("T1", "skt vs gen.g", am_lol)   # alias 'skt' -> canonical T1
check("alias hit -> True/100", ok and sc == 100.0)
ok2, _ = mm.team_present("Natus Vincere", "gen.g vs t1", am_lol)
check("no hit -> False", ok2 is False)

# --- TWO-TEAM GATE: both required (the verified-correct behavior) ---------------------
q = "natus vincere vs faze cs2 major final"
both, sa, sb = mm.both_teams_present("Natus Vincere", "FaZe", q, mm.build_alias_map(rows, "cs2"))
check("both teams present -> True", both and sa == 100.0 and sb == 100.0)
# only ONE team in the question must NOT match (the whole point of the gate)
only_one, _, _ = mm.both_teams_present("Natus Vincere", "FaZe", "natus vincere season winner", mm.build_alias_map(rows, "cs2"))
check("one team only -> gate rejects", only_one is False)

# --- specificity ---------------------------------------------------------------------
check("'vs' bonus", mm.specificity_score("t1 vs gen.g") == 130.0)
check("season penalty", mm.specificity_score("lck season winner") == 50.0)
check("submarket penalty", mm.specificity_score("t1 vs geng - map 1 winner") == 110.0)  # +30 vs, -20 map

# --- link_market: picks match-specific over season, needs both teams -----------------
matches = [
    {"match_id": "m_series", "team_a": "T1", "team_b": "Gen.G"},
    {"match_id": "m_other", "team_a": "T1", "team_b": "KT Rolster"},
]
res = mm.link_market("T1 vs Gen.G — Match Winner", matches, {})
check("links to the right both-teams match", res.link is not None and res.link.match_id == "m_series")
# a market naming only one team -> no link, but a near-miss for triage
res2 = mm.link_market("T1 season MVP", matches, {})
check("no both-team match -> link None", res2.link is None)
check("near-miss recorded for triage", res2.near_miss is not None and res2.near_miss["score"] == 100.0)

# specificity ranking: a season market and a match market both name both teams -> match wins
both_pass = [
    {"match_id": "m_specific", "team_a": "T1", "team_b": "Gen.G"},
]
r_specific = mm.link_market("T1 vs Gen.G map 1", both_pass, {})
r_season = mm.link_market("T1 and Gen.G season head to head", both_pass, {})
check("specificity: match-question scores higher than season-question",
      r_specific.link.specificity > r_season.link.specificity)

# --- fuzzy fallback works without rapidfuzz (difflib) --------------------------------
# near-identical spelling should fuzzy-match above threshold
okf, scf = mm.team_present("Cloud9", "match: cloud 9 vs liquid", {}, threshold=70.0)
check("fuzzy fallback returns a score", scf > 0)
check("empty team -> False", mm.team_present("", "anything", {}) == (False, 0.0))

if __name__ == "__main__":
    import sys
    print(f"\nrapidfuzz={'yes' if mm._RAPIDFUZZ else 'no (difflib fallback)'}")
    print(f"{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
