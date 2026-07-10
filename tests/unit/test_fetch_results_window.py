"""Unit tests for the GAP-A results-window fetcher (PandaScore -> bulk JSONL)."""
import json

from esports_v2.model.results_join import load_bulk_results
from esports_v2.scripts.fetch_results_window import (
    fetch_window,
    parse_match_row,
    write_jsonl,
)


def _ps_match(**over):
    """Real PandaScore past-match shape (fields PandaScoreLoader._parse_match reads)."""
    m = {
        "id": 812345,
        "status": "finished",
        "opponents": [
            {"opponent": {"id": 1, "name": "JD Gaming"}},
            {"opponent": {"id": 2, "name": "TYLOO"}},
        ],
        "winner": {"id": 2, "name": "TYLOO"},
        "results": [{"team_id": 1, "score": 1}, {"team_id": 2, "score": 2}],
        "begin_at": "2026-07-10T11:00:00Z",
        "scheduled_at": "2026-07-10T11:00:00Z",
        "end_at": "2026-07-10T13:40:00Z",
        "league": {"name": "VCT China"},
    }
    m.update(over)
    return m


def test_parse_finished_match():
    row = parse_match_row(_ps_match(), "valorant")
    assert row == {
        "match_id": "psw_valorant_812345",
        "game": "valorant",
        "team_a": "JD Gaming",
        "team_b": "TYLOO",
        "winner": "TYLOO",
        "match_date": "2026-07-10T11:00:00Z",   # begin_at preferred (match-start day)
        "source": "pandascore_window",
    }


def test_reject_not_finished():
    assert parse_match_row(_ps_match(status="running"), "valorant") is None


def test_reject_missing_team_name():
    m = _ps_match(opponents=[{"opponent": {"name": ""}}, {"opponent": {"name": "TYLOO"}}])
    assert parse_match_row(m, "valorant") is None


def test_winner_falls_back_to_scores():
    row = parse_match_row(_ps_match(winner=None), "valorant")
    assert row is not None and row["winner"] == "TYLOO"   # 2 > 1


def test_no_winner_no_scores_skipped():
    m = _ps_match(winner=None, results=[])
    assert parse_match_row(m, "valorant") is None


def test_tied_scores_no_winner_skipped():
    m = _ps_match(winner=None,
                  results=[{"score": 1}, {"score": 1}])
    assert parse_match_row(m, "valorant") is None


def test_fetch_window_pages_and_filters(tmp_path):
    pages = {1: [_ps_match(), _ps_match(id=9, status="running")], 2: []}
    calls = []
    def fake_get(url):
        calls.append(url)
        page = int(url.split("&page=")[1].split("&")[0])
        return pages.get(page, [])
    rows = fetch_window(["valorant"], "2026-07-06T00:00:00Z", "2026-07-10T23:59:59Z",
                        http_get=fake_get)
    assert len(rows) == 1
    assert "valorant/matches/past" in calls[0]
    assert "filter%5Bstatus%5D=finished" in calls[0]


def test_unknown_game_skipped():
    rows = fetch_window(["chess"], "a", "b", http_get=lambda u: [])
    assert rows == []


def test_output_loads_via_results_join(tmp_path):
    """The written JSONL must round-trip through load_bulk_results unchanged."""
    out = tmp_path / "win.jsonl"
    write_jsonl([parse_match_row(_ps_match(), "valorant")], out)
    recs = load_bulk_results(out)
    assert len(recs) == 1
    r = recs[0]
    assert (r.team_a, r.team_b, r.winner) == ("JD Gaming", "TYLOO", "TYLOO")
    assert r.day == "2026-07-10"
    assert r.source == "pandascore_window"
