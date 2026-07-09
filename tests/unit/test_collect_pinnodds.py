"""Unit tests for the PinnOdds forward-collector (record building + append)."""
import json

from esports_v2.scripts.collect_pinnodds import build_snapshot_records, append_jsonl
from esports_v2.data.pinnodds_loader import PinnOddsLoader
from esports_v2.data.pm_market_index import PMMarketRef


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


def test_build_snapshot_records_no_pm_index_gives_null_pm_fields():
    r = build_snapshot_records(_ROWS, "t")[0]
    # GAP B fields are present but None when no PM market matched.
    for f in ("condition_id", "yes_token_id", "yes_outcome", "market_price"):
        assert f in r and r[f] is None


def test_build_snapshot_records_attaches_matched_pm_ref():
    pm = PMMarketRef(condition_id="0xabc", yes_token_id="tok0", yes_outcome="BIG",
                     market_price=0.57, question="BIG vs PVISION", game_start="2026-07-10")
    recs = build_snapshot_records(_ROWS, "t", {"big||pvision||2026-07-10": pm})
    r = recs[0]
    assert r["condition_id"] == "0xabc"
    assert r["yes_token_id"] == "tok0"
    assert r["yes_outcome"] == "BIG"
    assert r["market_price"] == 0.57


def test_build_snapshot_records_unmatched_row_stays_null():
    pm = PMMarketRef(condition_id="0xabc", yes_token_id="tok0", yes_outcome="X",
                     market_price=0.5, question="X vs Y", game_start="2026-07-10")
    # index keyed for a DIFFERENT match -> our row must not pick it up.
    recs = build_snapshot_records(_ROWS, "t", {"other||match||2026-07-10": pm})
    assert recs[0]["condition_id"] is None and recs[0]["market_price"] is None


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
