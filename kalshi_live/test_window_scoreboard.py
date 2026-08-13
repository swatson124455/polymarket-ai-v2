"""Tests for kalshi_window_scoreboard — the pure windowing/bucketing logic.

The live fetches (credit_history, fills, settlements) are not exercised here; the cash model
itself is pinned in test_attribution.py. These tests pin the WINDOW semantics: closed
[T0, T7] bounds, conclusion-date bucketing, referral/'?' never counted, no-timestamp rows
counted-not-silent."""
import datetime

from kalshi_window_scoreboard import (T0, T7, event_end_map, parse_iso, score_credits,
                                      window_drag)

T0_DT = parse_iso(T0)
T7_DT = parse_iso(T7)


def _fill_event(ts, cash):
    return {"fill": {"created_time": ts}, "cash": cash}


class TestEventEndMap:
    def test_latest_end_wins_and_event_derived_from_ticker(self):
        m = {"1": {"market_ticker": "KXFOO-26AUG15-H1", "end_date": "2026-08-14T00:00:00Z"},
             "2": {"market_ticker": "KXFOO-26AUG15-H2", "end_date": "2026-08-15T14:00:00Z"},
             "3": {"market_ticker": None, "end_date": "2026-08-15T14:00:00Z"},
             "4": {"market_ticker": "KXBAR-26AUG20-T5"}}
        out = event_end_map(m)
        assert out == {"KXFOO-26AUG15": "2026-08-15T14:00:00Z"}


class TestScoreCredits:
    def test_buckets_and_counted_total(self):
        by_ev = {"KXIN-26AUG15": {"paid": 5.0},     # concludes in window
                 "KXOLD-26AUG01": {"paid": 7.9},    # concluded pre-T0
                 "KXLATE-26SEP01": {"paid": 1.0},   # concludes post-T7
                 "KXNOMAP-26AUG14": {"paid": 2.0},  # no program-map entry
                 "?": {"paid": 15.0}}               # referral-shaped: no event in reason
        ev_end = {"KXIN-26AUG15": "2026-08-15T14:00:00Z",
                  "KXOLD-26AUG01": "2026-08-01T14:00:00Z",
                  "KXLATE-26SEP01": "2026-09-01T14:00:00Z"}
        counted, b = score_credits(by_ev, ev_end, T0_DT, T7_DT)
        assert counted == 5.0
        assert b["pre_t0"]["usd"] == 7.9
        assert b["post_t7"]["usd"] == 1.0
        assert b["unmapped"]["usd"] == 17.0          # 2.0 no-map + 15.0 referral
        assert b["unmapped"]["events"] == ["?", "KXNOMAP-26AUG14"]

    def test_window_bounds_are_closed(self):
        by_ev = {"KXA-1": {"paid": 1.0}, "KXB-1": {"paid": 2.0}}
        ev_end = {"KXA-1": T0, "KXB-1": T7}
        counted, _ = score_credits(by_ev, ev_end, T0_DT, T7_DT)
        assert counted == 3.0

    def test_unparseable_end_goes_unmapped_not_counted(self):
        counted, b = score_credits({"KXA-1": {"paid": 4.0}}, {"KXA-1": "not-a-date"},
                                   T0_DT, T7_DT)
        assert counted == 0.0
        assert b["unmapped"]["usd"] == 4.0


class TestWindowDrag:
    def test_fills_filtered_to_closed_window_and_no_ts_counted(self):
        events = [_fill_event("2026-08-11T23:00:00Z", -100.0),   # pre-T0: excluded
                  _fill_event(T0, -1.0),                          # boundary: included
                  _fill_event("2026-08-13T00:00:00Z", -2.5),
                  _fill_event(None, -50.0)]                       # no ts: counted, excluded
        settles = [{"settled_time": "2026-08-14T00:00:00Z", "revenue": 150},
                   {"settled_time": "2026-08-01T00:00:00Z", "revenue": 9900},
                   {"revenue": 9900}]                             # no ts
        hi = parse_iso("2026-08-15T00:00:00+00:00")
        fills_cash, settle_rev, n_fills, n_setts, no_ts = window_drag(
            events, settles, T0_DT, hi)
        assert fills_cash == -3.5
        assert settle_rev == 1.5
        assert (n_fills, n_setts, no_ts) == (2, 1, 2)

    def test_hi_bound_respected(self):
        events = [_fill_event("2026-08-13T00:00:00Z", -1.0),
                  _fill_event("2026-08-20T00:00:00Z", -9.0)]      # post-T7 style: excluded
        fills_cash, settle_rev, n_fills, n_setts, no_ts = window_drag(
            events, [], T0_DT, T7_DT)
        assert fills_cash == -1.0
        assert (n_fills, no_ts) == (1, 0)


class TestFilterCreditsAndWindowState:
    """Review F1/F8: per-observation credit filter + window lifecycle stamp."""

    def test_pre_t0_and_post_deadline_excluded(self):
        from kalshi_window_scoreboard import filter_credits, OBS_DEADLINE
        deadline = parse_iso(OBS_DEADLINE)
        credits = [
            {"created_at": "2026-07-24T00:00:00Z", "amount_cents": 1500},   # referral, pre-T0
            {"created_at": "2026-08-13T12:00:00Z", "amount_cents": 200},    # in observation
            {"created_at": "2026-08-25T00:00:00Z", "amount_cents": 300},    # past deadline
        ]
        rows, dropped = filter_credits(credits, T0_DT, deadline)
        assert [c["amount_cents"] for c in rows] == [200]
        assert dropped == 0

    def test_no_ts_and_unparseable_counted_not_silent(self):
        from kalshi_window_scoreboard import filter_credits
        credits = [{"amount_cents": 100}, {"created_at": "junk", "amount_cents": 100},
                   {"created_at": "2026-08-13T00:00:00Z", "amount_cents": 100}]
        rows, dropped = filter_credits(credits, T0_DT, None)
        assert len(rows) == 1 and dropped == 2

    def test_naive_created_at_does_not_crash(self):
        from kalshi_window_scoreboard import filter_credits
        rows, dropped = filter_credits([{"created_at": "2026-08-13T00:00:00",
                                         "amount_cents": 100}], T0_DT, None)
        assert len(rows) == 1 and dropped == 0

    def test_window_state_three_phases(self):
        from kalshi_window_scoreboard import window_state, OBS_DEADLINE
        deadline = parse_iso(OBS_DEADLINE)
        assert window_state(parse_iso("2026-08-15T00:00:00Z"), T7_DT, deadline) == "open"
        assert window_state(parse_iso("2026-08-20T00:00:00Z"), T7_DT, deadline) == "post_window"
        assert window_state(parse_iso("2026-08-22T00:00:00Z"), T7_DT, deadline) == "concluded"


class TestPassGauge:
    """Review F2: net form of the pre-registered rule; correct for both drag signs."""

    def test_loss_drag_matches_preregistered_rule(self):
        from kalshi_window_scoreboard import pass_gauge
        assert pass_gauge(10.0, -9.0) is True     # credits > |drag|
        assert pass_gauge(8.0, -9.0) is False

    def test_profitable_window_passes_with_any_credits(self):
        from kalshi_window_scoreboard import pass_gauge
        assert pass_gauge(3.0, 8.0) is True       # abs() bug reported False here
        assert pass_gauge(0.0, 8.0) is True

    def test_zero_zero_is_not_pass(self):
        from kalshi_window_scoreboard import pass_gauge
        assert pass_gauge(0.0, 0.0) is False


class TestReconMismatches:
    """Review F3: ledger self-check reused — settled tickers exempt, live ones must match."""

    def test_match_and_mismatch(self):
        from kalshi_window_scoreboard import recon_mismatches
        replay = {"KXA-1-T1": 3.0, "KXB-1-T1": -1.5, "KXSETTLED-1-T1": 2.0}
        venue = {"KXA-1-T1": 3.0, "KXB-1-T1": -1.0}
        out = recon_mismatches(replay, venue, {"KXSETTLED-1-T1"})
        assert out == {"KXB-1-T1": [-1.0, -1.5]}

    def test_venue_only_position_flagged(self):
        from kalshi_window_scoreboard import recon_mismatches
        out = recon_mismatches({}, {"KXC-1-T1": 2.0}, set())
        assert out == {"KXC-1-T1": [2.0, 0.0]}

    def test_all_flat_clean(self):
        from kalshi_window_scoreboard import recon_mismatches
        assert recon_mismatches({"KXA-1-T1": 0.001}, {}, set()) == {}


class TestLatestRecorderCashBasis:
    """Review F9: funded_cash preferred so resting reservations don't read as a gap."""

    def test_funded_cash_preferred(self, tmp_path, monkeypatch):
        import kalshi_window_scoreboard as ws
        monkeypatch.setattr(ws, "DATA", str(tmp_path))
        (tmp_path / "cash-202608.jsonl").write_text(
            '{"ts": "2026-08-13T00:00:00Z", "cash": 250.0, '
            '"resting_reservation": 5.0, "funded_cash": 255.0}\n')
        val, ts, basis = ws.latest_recorder_cash()
        assert (val, basis) == (255.0, "funded_cash")

    def test_fallback_sum_then_cash(self, tmp_path, monkeypatch):
        import kalshi_window_scoreboard as ws
        monkeypatch.setattr(ws, "DATA", str(tmp_path))
        (tmp_path / "cash-202608.jsonl").write_text(
            '{"ts": "2026-08-13T00:00:00Z", "cash": 250.0, "resting_reservation": 5.0}\n')
        val, ts, basis = ws.latest_recorder_cash()
        assert (val, basis) == (255.0, "cash+reservation")
        (tmp_path / "cash-202608.jsonl").write_text(
            '{"ts": "2026-08-13T00:00:00Z", "cash": 250.0}\n')
        assert ws.latest_recorder_cash()[2] == "cash"

    def test_missing_files_none_triple(self, tmp_path, monkeypatch):
        import kalshi_window_scoreboard as ws
        monkeypatch.setattr(ws, "DATA", str(tmp_path))
        assert ws.latest_recorder_cash() == (None, None, None)
