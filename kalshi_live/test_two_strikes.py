"""Two-strikes rule (operator-named 2026-07-31: '2 strikes and you are out for 2x current').

Strike 1 = the existing day-latch (unchanged). Strike 2 within memory = exit-only through the
END of the day after the second trip. Pure helper _two_strikes tested directly.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


def _dt(s):
    return datetime.datetime.fromisoformat(s + "+00:00")


class TestTwoStrikes:
    def test_first_strike_records_but_no_ban(self):
        hist, banned = q._two_strikes({}, {"T-1"}, "2026-07-30", _dt("2026-07-30T12:00:00"))
        assert hist == {"T-1": ["2026-07-30"]} and banned == set()

    def test_second_strike_bans_through_next_day(self):
        hist = {"T-1": ["2026-07-30"]}
        hist, banned = q._two_strikes(hist, {"T-1"}, "2026-07-31",
                                      _dt("2026-07-31T02:00:00"))
        assert banned == {"T-1"}                                    # trip day...
        _, banned = q._two_strikes(hist, set(), "2026-08-01",
                                   _dt("2026-08-01T23:59:00"))
        assert banned == {"T-1"}                                    # ...and the ENTIRE next day
        _, banned = q._two_strikes(hist, set(), "2026-08-02",
                                   _dt("2026-08-02T00:01:00"))
        assert banned == set()                                      # 2x served -> re-admitted

    def test_same_day_retrip_is_one_strike(self):
        hist, _ = q._two_strikes({}, {"T-1"}, "2026-07-30", _dt("2026-07-30T10:00:00"))
        hist, banned = q._two_strikes(hist, {"T-1"}, "2026-07-30",
                                      _dt("2026-07-30T14:00:00"))
        assert hist["T-1"] == ["2026-07-30"] and banned == set()    # latched, not double-struck

    def test_memory_prunes_ancient_strikes(self):
        old = "2026-07-01"
        hist = {"T-1": [old]}
        hist, banned = q._two_strikes(hist, {"T-1"}, "2026-07-31",
                                      _dt("2026-07-31T02:00:00"))
        assert hist["T-1"] == ["2026-07-31"] and banned == set()    # old strike expired

    def test_flat_market_history_persists_until_pruned(self):
        hist = {"T-1": ["2026-07-29", "2026-07-30"]}
        hist2, banned = q._two_strikes(dict(hist), set(), "2026-07-31",
                                       _dt("2026-07-31T12:00:00"))
        assert banned == {"T-1"}                                    # ban outlives the trip day

    def test_third_strike_is_out_forever(self):
        hist = {"T-1": ["2026-07-28", "2026-07-29"]}
        hist, banned = q._two_strikes(hist, {"T-1"}, "2026-08-01",
                                      _dt("2026-08-01T12:00:00"))
        assert banned == {"T-1"}                                    # strike 3: OUT
        # far beyond any doubled window AND beyond the memory prune horizon:
        _, banned = q._two_strikes(hist, set(), "2026-09-15", _dt("2026-09-15T12:00:00"))
        assert banned == {"T-1"}                                    # no expiry, no prune
        assert hist["T-1"] == ["2026-07-28", "2026-07-29", "2026-08-01"]
