"""Unit tests for the PinnOdds snapshot -> closing-line reducer.

Covers the no-look-ahead closing rule, correct-or-absent drops, line-movement
capture, and the JSONL/lookup helpers. Pure — no network, no DB.
"""
from datetime import datetime, timezone

from esports_v2.model.closing_line import (
    ClosingLine,
    closing_lines_to_odds_lookup,
    iter_snapshots_jsonl,
    parse_ts,
    reduce_to_closing_lines,
)


def _snap(match_key, captured_at, odds_a, odds_b, starts="2026-07-10T18:00:00Z",
          home="Team A", away="Team B", league_name="Test League"):
    return {
        "captured_at": captured_at,
        "match_key": match_key,
        "home": home,
        "away": away,
        "starts": starts,
        "league_name": league_name,
        "odds_a": odds_a,
        "odds_b": odds_b,
        "event_type": "prematch",
    }


# ── parse_ts ────────────────────────────────────────────────────────────────


def test_parse_ts_iso_with_z():
    dt = parse_ts("2026-07-10T18:00:00Z")
    assert dt == datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc)


def test_parse_ts_space_separator_treated_utc():
    dt = parse_ts("2026-07-10 18:00:00")
    assert dt == datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc)


def test_parse_ts_numeric_offset_normalized_to_utc():
    dt = parse_ts("2026-07-10T20:00:00+02:00")
    assert dt == datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc)


def test_parse_ts_date_only():
    dt = parse_ts("2026-07-10")
    assert dt == datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_ts_junk_returns_none():
    assert parse_ts("not-a-date") is None
    assert parse_ts("") is None
    assert parse_ts(None) is None


# ── reduce_to_closing_lines: the closing rule ───────────────────────────────


def test_closing_is_last_snapshot_before_start():
    k = "team a||team b||2026-07-10"
    snaps = [
        _snap(k, "2026-07-09T18:00:00Z", 2.0, 1.9),   # open
        _snap(k, "2026-07-10T12:00:00Z", 2.1, 1.8),   # mid
        _snap(k, "2026-07-10T17:45:00Z", 2.3, 1.63),  # closing (last <= starts)
    ]
    out = reduce_to_closing_lines(snaps)
    assert set(out) == {k}
    cl = out[k]
    assert cl.odds_a == 2.3 and cl.odds_b == 1.63
    assert cl.open_odds_a == 2.0 and cl.open_odds_b == 1.9
    assert cl.n_snapshots == 3 and cl.n_before_start == 3


def test_post_start_snapshots_are_ignored():
    k = "team a||team b||2026-07-10"
    snaps = [
        _snap(k, "2026-07-10T17:00:00Z", 2.2, 1.7),   # last before start
        _snap(k, "2026-07-10T18:30:00Z", 5.0, 1.1),   # AFTER start — look-ahead
        _snap(k, "2026-07-10T19:00:00Z", 9.0, 1.05),  # AFTER start — look-ahead
    ]
    out = reduce_to_closing_lines(snaps)
    cl = out[k]
    assert cl.odds_a == 2.2 and cl.odds_b == 1.7  # closing = last pre-start
    assert cl.n_snapshots == 3          # all counted as valid observations
    assert cl.n_before_start == 1       # only one is pre-start


def test_exactly_at_start_counts_as_pre_start():
    k = "team a||team b||2026-07-10"
    snaps = [_snap(k, "2026-07-10T18:00:00Z", 2.5, 1.6)]  # captured_at == starts
    out = reduce_to_closing_lines(snaps)
    assert k in out and out[k].odds_a == 2.5


def test_only_post_start_snapshots_dropped():
    k = "team a||team b||2026-07-10"
    snaps = [_snap(k, "2026-07-10T19:00:00Z", 2.5, 1.6)]  # after start only
    assert reduce_to_closing_lines(snaps) == {}


def test_unparseable_starts_drops_match():
    k = "team a||team b||2026-07-10"
    snaps = [_snap(k, "2026-07-10T12:00:00Z", 2.0, 1.9, starts="TBD")]
    assert reduce_to_closing_lines(snaps) == {}


def test_degenerate_odds_snapshots_skipped():
    k = "team a||team b||2026-07-10"
    snaps = [
        _snap(k, "2026-07-09T18:00:00Z", 1.0, 1.9),    # odds_a <= 1.0 -> skip
        _snap(k, "2026-07-10T10:00:00Z", 2.0, None),   # missing odds -> skip
        _snap(k, "2026-07-10T17:00:00Z", 2.2, 1.7),    # valid closing
    ]
    out = reduce_to_closing_lines(snaps)
    assert out[k].odds_a == 2.2
    assert out[k].n_snapshots == 1  # only the valid one counted


def test_missing_captured_at_skipped():
    k = "team a||team b||2026-07-10"
    good = _snap(k, "2026-07-10T17:00:00Z", 2.2, 1.7)
    bad = _snap(k, None, 9.9, 9.9)
    assert reduce_to_closing_lines([bad, good])[k].odds_a == 2.2


def test_multiple_matches_independent():
    k1 = "a||b||2026-07-10"
    k2 = "c||d||2026-07-11"
    snaps = [
        _snap(k1, "2026-07-10T17:00:00Z", 2.0, 1.9, starts="2026-07-10T18:00:00Z"),
        _snap(k2, "2026-07-11T17:00:00Z", 1.5, 2.6, starts="2026-07-11T18:00:00Z"),
    ]
    out = reduce_to_closing_lines(snaps)
    assert set(out) == {k1, k2}
    assert out[k1].odds_a == 2.0 and out[k2].odds_a == 1.5


def test_snapshots_out_of_order_still_pick_latest():
    k = "a||b||2026-07-10"
    snaps = [
        _snap(k, "2026-07-10T17:45:00Z", 2.3, 1.63),  # closing, given first
        _snap(k, "2026-07-09T18:00:00Z", 2.0, 1.9),   # open, given second
    ]
    cl = reduce_to_closing_lines(snaps)[k]
    assert cl.odds_a == 2.3          # closing = latest pre-start regardless of order
    assert cl.open_odds_a == 2.0     # open = earliest pre-start


def test_non_dict_and_keyless_snapshots_ignored():
    k = "a||b||2026-07-10"
    good = _snap(k, "2026-07-10T17:00:00Z", 2.2, 1.7)
    out = reduce_to_closing_lines([None, "junk", {}, {"match_key": ""}, good])
    assert set(out) == {k}


# ── helpers ─────────────────────────────────────────────────────────────────


def test_closing_lines_to_odds_lookup_projection():
    cl = ClosingLine(
        match_key="a||b||2026-07-10", home="A", away="B", starts="x",
        league_name="L", odds_a=2.3, odds_b=1.63, captured_at="t",
        n_snapshots=3, n_before_start=3, open_odds_a=2.0, open_odds_b=1.9,
    )
    lookup = closing_lines_to_odds_lookup({"a||b||2026-07-10": cl})
    assert lookup == {"a||b||2026-07-10": (2.3, 1.63)}


def test_iter_snapshots_jsonl_skips_malformed(tmp_path):
    p = tmp_path / "snaps.jsonl"
    p.write_text(
        '{"match_key": "a||b||2026-07-10", "odds_a": 2.0}\n'
        "\n"                       # blank
        "{not json}\n"             # malformed
        "[1,2,3]\n"                # not a dict
        '{"match_key": "c||d||2026-07-11", "odds_a": 1.5}\n',
        encoding="utf-8",
    )
    rows = list(iter_snapshots_jsonl(p))
    assert len(rows) == 2
    assert rows[0]["match_key"] == "a||b||2026-07-10"


def test_iter_snapshots_jsonl_missing_file(tmp_path):
    assert list(iter_snapshots_jsonl(tmp_path / "nope.jsonl")) == []


def test_closing_line_carries_pm_fields_from_closing_snapshot():
    from esports_v2.model.closing_line import reduce_to_closing_lines
    snaps = [
        {"match_key": "a||b||2026-07-10", "home": "A", "away": "B",
         "starts": "2026-07-10T18:00:00Z", "captured_at": "2026-07-09T00:00:00Z",
         "odds_a": 2.0, "odds_b": 1.8, "market_price": None},  # opening: no PM
        {"match_key": "a||b||2026-07-10", "home": "A", "away": "B",
         "starts": "2026-07-10T18:00:00Z", "captured_at": "2026-07-10T17:00:00Z",
         "odds_a": 2.1, "odds_b": 1.75, "condition_id": "0xabc",
         "yes_token_id": "tok0", "yes_outcome": "A", "market_price": 0.55},  # closing
    ]
    out = reduce_to_closing_lines(snaps)
    cl = out["a||b||2026-07-10"]
    assert cl.condition_id == "0xabc"
    assert cl.yes_token_id == "tok0"
    assert cl.market_price == 0.55       # bet-time price from the CLOSING snapshot


def test_closing_line_degenerate_pm_price_becomes_none():
    from esports_v2.model.closing_line import reduce_to_closing_lines
    snaps = [{"match_key": "a||b||2026-07-10", "home": "A", "away": "B",
              "starts": "2026-07-10T18:00:00Z", "captured_at": "2026-07-10T17:00:00Z",
              "odds_a": 2.0, "odds_b": 1.8, "condition_id": "0xabc",
              "yes_token_id": "tok0", "market_price": 1.0}]  # resolved/degenerate
    cl = reduce_to_closing_lines(snaps)["a||b||2026-07-10"]
    assert cl.condition_id == "0xabc" and cl.market_price is None


# ── schedule drift (2026-07-12 live finding: starts moves when matches delay) ─


def _drift_snap(cap, starts, oa, ob):
    return {"captured_at": cap, "match_key": "a||b||2026-08-01", "home": "A",
            "away": "B", "starts": starts, "league_name": "L",
            "odds_a": oa, "odds_b": ob, "event_type": "prematch"}


def test_delayed_match_uses_latest_starts_and_keeps_fresh_snapshot():
    # match delayed 08:00 -> 08:15; the 08:10 capture IS pre-start and must win
    snaps = [_drift_snap("2026-08-01T07:00:00Z", "2026-08-01T08:00:00Z", 2.0, 1.8),
             _drift_snap("2026-08-01T08:10:00Z", "2026-08-01T08:15:00Z", 1.5, 2.6)]
    out = reduce_to_closing_lines(snaps)
    cl = out["a||b||2026-08-01"]
    assert cl.odds_a == 1.5                       # fresh line, not the stale one
    assert cl.starts == "2026-08-01T08:15:00Z"    # latest schedule knowledge


def test_match_moved_earlier_excludes_lookahead():
    # match moved 08:15 -> 08:00; the 08:10 capture is POST-start -> excluded
    snaps = [_drift_snap("2026-08-01T07:00:00Z", "2026-08-01T08:15:00Z", 2.0, 1.8),
             _drift_snap("2026-08-01T08:10:00Z", "2026-08-01T08:00:00Z", 9.0, 1.01)]
    out = reduce_to_closing_lines(snaps)
    cl = out["a||b||2026-08-01"]
    assert cl.odds_a == 2.0                       # look-ahead odds rejected
