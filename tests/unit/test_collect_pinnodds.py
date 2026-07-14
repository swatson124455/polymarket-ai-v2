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
                     market_price=0.57, question="BIG vs PVISION", game_start="2026-07-10",
                     team_a="BIG", team_b="PVISION", day="2026-07-10")
    recs = build_snapshot_records(_ROWS, "t", {"big||pvision||2026-07-10": pm})
    r = recs[0]
    assert r["condition_id"] == "0xabc"
    assert r["yes_token_id"] == "tok0"
    assert r["yes_outcome"] == "BIG"
    assert r["market_price"] == 0.57


def test_build_snapshot_records_unmatched_row_stays_null():
    pm = PMMarketRef(condition_id="0xabc", yes_token_id="tok0", yes_outcome="X",
                     market_price=0.5, question="X vs Y", game_start="2026-07-10",
                     team_a="X", team_b="Y", day="2026-07-10")
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


def test_build_snapshot_records_quote_fields_null_without_touch_quotes():
    r = build_snapshot_records(_ROWS, "t")[0]
    for f in ("best_bid", "best_ask", "bid_size", "ask_size"):
        assert f in r and r[f] is None


def test_build_snapshot_records_attaches_touch_quote_by_yes_token():
    from esports_v2.data.pm_market_index import TouchQuote
    pm = PMMarketRef(condition_id="0xabc", yes_token_id="tok0", yes_outcome="BIG",
                     market_price=0.57, question="BIG vs PVISION", game_start="2026-07-10",
                     team_a="BIG", team_b="PVISION", day="2026-07-10")
    quotes = {"tok0": TouchQuote(best_bid=0.56, best_ask=0.58,
                                 bid_size=120.0, ask_size=40.0)}
    r = build_snapshot_records(_ROWS, "t", {"big||pvision||2026-07-10": pm}, quotes)[0]
    assert (r["best_bid"], r["best_ask"]) == (0.56, 0.58)
    assert (r["bid_size"], r["ask_size"]) == (120.0, 40.0)
    # matched but book missing -> nulls, row intact
    r2 = build_snapshot_records(_ROWS, "t", {"big||pvision||2026-07-10": pm}, {})[0]
    assert r2["condition_id"] == "0xabc" and r2["best_bid"] is None


def test_fetch_rows_dedups_live_and_prematch_prefer_prematch(monkeypatch):
    # Same match in BOTH feeds within a tick, different odds -> ONE row, prematch.
    loader = PinnOddsLoader(api_key="dummy")
    def ev(oa, ob):
        return {"home": "BIG", "away": "PVISION", "starts": "2026-07-10T08:00:00Z",
                "league_name": "L",
                "periods": {"num_0": {"money_line": {"home": oa, "away": ob}}}}
    def fake_get(path, params=None):
        return {"events": [ev(2.5, 1.5)]} if params["event_type"] == "live" \
            else {"events": [ev(2.3, 1.632)]}
    monkeypatch.setattr(loader, "_get", fake_get)
    monkeypatch.setattr("esports_v2.data.pinnodds_loader.time.sleep", lambda *_: None)
    rows = loader.fetch_rows(event_types=("live", "prematch"))
    assert len(rows) == 1
    assert rows[0]["event_type"] == "prematch"
    assert rows[0]["odds_a"] == 2.3      # prematch odds won, not the live 2.5


def test_fetch_rows_keeps_live_when_no_prematch(monkeypatch):
    loader = PinnOddsLoader(api_key="dummy")
    ev = {"home": "BIG", "away": "PVISION", "starts": "2026-07-10T08:00:00Z",
          "league_name": "L",
          "periods": {"num_0": {"money_line": {"home": 2.5, "away": 1.5}}}}
    monkeypatch.setattr(loader, "_get",
                        lambda path, params=None: {"events": [ev]} if params["event_type"] == "live" else {"events": []})
    monkeypatch.setattr("esports_v2.data.pinnodds_loader.time.sleep", lambda *_: None)
    rows = loader.fetch_rows(event_types=("live", "prematch"))
    assert len(rows) == 1 and rows[0]["event_type"] == "live"


def test_fetch_rows_distinct_matches_both_kept(monkeypatch):
    loader = PinnOddsLoader(api_key="dummy")
    def fake_get(path, params=None):
        e = {"home": "A", "away": "B", "starts": "2026-07-10T08:00:00Z", "league_name": "L",
             "periods": {"num_0": {"money_line": {"home": 2.0, "away": 1.9}}}}
        e2 = {"home": "C", "away": "D", "starts": "2026-07-10T09:00:00Z", "league_name": "L",
              "periods": {"num_0": {"money_line": {"home": 1.7, "away": 2.2}}}}
        return {"events": [e, e2]}
    monkeypatch.setattr(loader, "_get", fake_get)
    monkeypatch.setattr("esports_v2.data.pinnodds_loader.time.sleep", lambda *_: None)
    rows = loader.fetch_rows(event_types=("prematch",))
    assert len(rows) == 2
