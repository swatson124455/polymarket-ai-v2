"""Unit tests for the closing-line -> free-results join (Step 2).

Focus on the correct-or-absent guarantees: bijective team matching, ambiguous
multi-winner drops, alias injection, and the coverage stats.
"""
from esports_v2.model.closing_line import ClosingLine
from esports_v2.model.results_join import (
    JoinStats,
    ResultRecord,
    _match_teams,
    _same_team,
    join_closing_lines_to_results,
    load_bulk_results,
    load_pandascore_cs2,
)


def _cl(match_key, home, away, starts="2026-07-10T18:00:00Z", odds_a=2.0, odds_b=1.9):
    return ClosingLine(
        match_key=match_key, home=home, away=away, starts=starts,
        league_name="L", odds_a=odds_a, odds_b=odds_b, captured_at="t",
        n_snapshots=1, n_before_start=1, open_odds_a=odds_a, open_odds_b=odds_b,
    )


def _res(match_id, a, b, winner, day="2026-07-10", game="cs2", source="pandascore"):
    return ResultRecord(match_id=match_id, game=game, team_a=a, team_b=b,
                        winner=winner, day=day, source=source)


# ── team-equality primitives ─────────────────────────────────────────────────


def test_same_team_exact_normalized():
    assert _same_team("G2 Esports", "g2 esports", None)
    assert _same_team("Team Spirit", "team  spirit", None)


def test_same_team_token_subset_shares_non_generic():
    assert _same_team("G2 Esports", "G2", None)         # G2 shared, non-generic
    assert _same_team("DRX Academy", "DRX", None)


def test_same_team_rejects_all_generic_overlap():
    # only "esports"/"club" shared -> generic -> not the same org
    assert not _same_team("R2 Esports Club", "Esports Club", None)


def test_same_team_rejects_unrelated():
    assert not _same_team("Natus Vincere", "FaZe Clan", None)


def test_same_team_alias_injection():
    aliases = {"NAVI": ["Natus Vincere"]}
    exp = lambda t: aliases.get(t, [])
    assert _same_team("NAVI", "Natus Vincere", exp)


def test_match_teams_bijection_orientations():
    assert _match_teams("A", "B", "A", "B", None) is True
    assert _match_teams("A", "B", "B", "A", None) is False


def test_match_teams_ambiguous_returns_none():
    # home matches both result teams -> no clean bijection
    assert _match_teams("Spirit", "X", "Team Spirit", "Spirit Academy", None) is None
    # neither team present
    assert _match_teams("A", "B", "C", "D", None) is None


# ── the join: happy paths ────────────────────────────────────────────────────


def test_join_home_won():
    closing = {"k": _cl("k", "Natus Vincere", "FaZe Clan")}
    results = [_res("m1", "Natus Vincere", "FaZe Clan", "Natus Vincere")]
    recs, stats = join_closing_lines_to_results(closing, results)
    assert len(recs) == 1
    r = recs[0]
    assert r.home_won is True and r.winner == "Natus Vincere"
    assert stats.joined == 1


def test_join_away_won_with_swapped_result_order():
    # result lists teams in the opposite order; winner is the line's away team
    closing = {"k": _cl("k", "Natus Vincere", "FaZe Clan")}
    results = [_res("m1", "FaZe Clan", "Natus Vincere", "FaZe Clan")]
    recs, _ = join_closing_lines_to_results(closing, results)
    assert recs[0].home_won is False and recs[0].winner == "FaZe Clan"


def test_join_uses_alias_map():
    closing = {"k": _cl("k", "NAVI", "FaZe")}
    results = [_res("m1", "Natus Vincere", "FaZe Clan", "Natus Vincere")]
    aliases = {"NAVI": ["Natus Vincere"], "FaZe": ["FaZe Clan"]}
    exp = lambda t: aliases.get(t, [])
    recs, _ = join_closing_lines_to_results(closing, results, alias_expand=exp)
    assert len(recs) == 1 and recs[0].home_won is True


# ── the join: correct-or-absent drops ────────────────────────────────────────


def test_no_result_match_counted():
    closing = {"k": _cl("k", "A", "B")}
    results = [_res("m1", "C", "D", "C")]
    recs, stats = join_closing_lines_to_results(closing, results)
    assert recs == [] and stats.no_result_match == 1


def test_ambiguous_multi_winner_dropped():
    # same day + teams, two games, DIFFERENT winners -> ambiguous for a match line
    closing = {"k": _cl("k", "A", "B")}
    results = [
        _res("g1", "A", "B", "A"),
        _res("g2", "A", "B", "B"),
    ]
    recs, stats = join_closing_lines_to_results(closing, results)
    assert recs == [] and stats.ambiguous_multi_winner == 1


def test_same_winner_multiple_games_still_joins():
    # two rows, SAME winner -> not ambiguous, joins once
    closing = {"k": _cl("k", "A", "B")}
    results = [
        _res("g1", "A", "B", "A"),
        _res("g2", "A", "B", "A"),
    ]
    recs, stats = join_closing_lines_to_results(closing, results)
    assert len(recs) == 1 and recs[0].home_won is True


def test_winner_none_dropped():
    closing = {"k": _cl("k", "A", "B")}
    results = [_res("m1", "A", "B", None)]
    recs, stats = join_closing_lines_to_results(closing, results)
    assert recs == [] and stats.winner_not_a_team == 1


def test_day_mismatch_no_join_by_default():
    closing = {"k": _cl("k", "A", "B", starts="2026-07-10T18:00:00Z")}
    results = [_res("m1", "A", "B", "A", day="2026-07-11")]
    recs, stats = join_closing_lines_to_results(closing, results)
    assert recs == [] and stats.no_result_match == 1


def test_day_window_allows_adjacent_day():
    closing = {"k": _cl("k", "A", "B", starts="2026-07-10T23:30:00Z")}
    results = [_res("m1", "A", "B", "A", day="2026-07-11")]
    recs, _ = join_closing_lines_to_results(closing, results, day_window=1)
    assert len(recs) == 1 and recs[0].home_won is True


def test_stats_n_lines():
    closing = {"k1": _cl("k1", "A", "B"), "k2": _cl("k2", "C", "D")}
    recs, stats = join_closing_lines_to_results(closing, [])
    assert stats.n_lines == 2 and stats.no_result_match == 2


# ── loaders ──────────────────────────────────────────────────────────────────


def test_load_bulk_results(tmp_path):
    p = tmp_path / "bulk.jsonl"
    p.write_text(
        '{"match_id":"m1","game":"lol","team_a":"LNG Esports","team_b":"Rare Atom",'
        '"winner":"Rare Atom","match_date":"2024-01-01 05:13:15","source":"oracle_elixir"}\n'
        "\n"
        '{"match_id":"bad","team_a":"","team_b":"X","match_date":"2024-01-01"}\n'
        '{"match_id":"m2","game":"cs2","team_a":"A","team_b":"B","winner":"A",'
        '"match_date":"badly-formed"}\n',
        encoding="utf-8",
    )
    rows = load_bulk_results(p)
    assert len(rows) == 1
    assert rows[0].team_a == "LNG Esports" and rows[0].day == "2024-01-01"
    assert rows[0].winner == "Rare Atom"


def test_load_pandascore_cs2(tmp_path):
    import json
    p = tmp_path / "cs2.json"
    p.write_text(json.dumps([
        {"id": "952676", "teams": [{"name": "Samurai"}, {"name": "Wanted Goons"}],
         "winner": "Samurai", "startedAt": "2024-04-15T02:00:00Z", "source": "pandascore"},
        {"id": "bad", "teams": [{"name": "OnlyOne"}], "winner": "OnlyOne",
         "startedAt": "2024-04-15T02:00:00Z"},
    ]), encoding="utf-8")
    rows = load_pandascore_cs2(p)
    assert len(rows) == 1
    assert rows[0].team_a == "Samurai" and rows[0].day == "2024-04-15"


def test_load_missing_files_return_empty(tmp_path):
    assert load_bulk_results(tmp_path / "nope.jsonl") == []
    assert load_pandascore_cs2(tmp_path / "nope.json") == []
