"""Unit tests for the PinnOdds forward-collector (record building + append)."""
import json

from esports_v2.scripts.collect_pinnodds import build_snapshot_records, append_jsonl
from esports_v2.data.pinnodds_loader import PinnOddsLoader


_ROWS = [
    {"match_key": "big||pvision||2026-07-10", "home": "BIG", "away": "PVISION",
     "starts": "2026-07-10T08:00:00Z", "league_name": "CS2 - XSE Pro League",
     "odds_a": 2.3, "odds_b": 1.632, "event_type": "prematch"},
]


def test_build_snapshot_records_stamps_time():
    recs = build_snapshot_records(_ROWS, "2026-07-09T00:00:00+00:00")
    assert len(recs) == 1
    r = recs[0]
    assert r["captured_at"] == "2026-07-09T00:00:00+00:00"
    assert r["match_key"] == "big||pvision||2026-07-10"
    assert (r["odds_a"], r["odds_b"]) == (2.3, 1.632)
    assert r["home"] == "BIG" and r["away"] == "PVISION"


def test_append_jsonl_is_append_only(tmp_path):
    p = tmp_path / "snap.jsonl"
    append_jsonl(build_snapshot_records(_ROWS, "t1"), p)
    append_jsonl(build_snapshot_records(_ROWS, "t2"), p)  # second run
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2                       # history preserved, not overwritten
    assert json.loads(lines[0])["captured_at"] == "t1"
    assert json.loads(lines[1])["captured_at"] == "t2"


def test_fetch_rows_shape(monkeypatch):
    loader = PinnOddsLoader(api_key="dummy")
    ev = {"home": "BIG", "away": "PVISION", "starts": "2026-07-10T08:00:00Z",
          "league_name": "CS2 - XSE Pro League",
          "periods": {"num_0": {"money_line": {"home": 2.3, "away": 1.632, "draw": None}}}}
    monkeypatch.setattr(loader, "_get", lambda path, params=None: {"events": [ev]})
    monkeypatch.setattr("esports_v2.data.pinnodds_loader.time.sleep", lambda *_: None)
    rows = loader.fetch_rows(event_types=("prematch",))
    assert len(rows) == 1
    assert rows[0]["match_key"] == "big||pvision||2026-07-10"
    assert rows[0]["odds_a"] == 2.3 and rows[0]["league_name"] == "CS2 - XSE Pro League"
