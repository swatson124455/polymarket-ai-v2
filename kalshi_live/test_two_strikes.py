"""Strike ladder (operator-named 2026-07-31, tightened same day: "one strike your out for
anything costing over 5 dollars until 8-3 rereview").

>= STRIKES_OUT strikes -> OUT: no expiry, prune-exempt. Live setting STRIKES_OUT=1: one trip
of the $/day governor is a permanent ban (until an operator clears the mkt_strike_hist entry).
The knob exists for the operator's 2026-08-03 policy re-review. Pure helper _two_strikes
tested directly.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


def _dt(s):
    return datetime.datetime.fromisoformat(s + "+00:00")


class TestOneStrikeOut:
    def test_default_is_one_strike_out(self):
        assert q.STRIKES_OUT == 1, "operator-named 2026-07-31: one strike = OUT"

    def test_first_strike_is_out_immediately(self):
        hist, banned = q._two_strikes({}, {"T-1"}, "2026-07-30", _dt("2026-07-30T12:00:00"))
        assert hist == {"T-1": ["2026-07-30"]}
        assert banned == {"T-1"}

    def test_ban_has_no_expiry_and_survives_the_prune_horizon(self):
        hist = {"T-1": ["2026-07-30"]}
        # far beyond TWO_STRIKES_MEMORY_D (14d): a struck market must NOT be pruned back in
        hist, banned = q._two_strikes(hist, set(), "2026-09-15", _dt("2026-09-15T12:00:00"))
        assert banned == {"T-1"}
        assert hist["T-1"] == ["2026-07-30"], "OUT entries are prune-exempt"

    def test_same_day_retrip_records_one_strike(self):
        hist, _ = q._two_strikes({}, {"T-1"}, "2026-07-30", _dt("2026-07-30T10:00:00"))
        hist, banned = q._two_strikes(hist, {"T-1"}, "2026-07-30", _dt("2026-07-30T14:00:00"))
        assert hist["T-1"] == ["2026-07-30"] and banned == {"T-1"}

    def test_flat_market_ban_persists(self):
        # tripped days ago, position long flat, not tripped today -> still OUT
        hist = {"T-1": ["2026-07-29"]}
        _, banned = q._two_strikes(dict(hist), set(), "2026-07-31", _dt("2026-07-31T12:00:00"))
        assert banned == {"T-1"}

    def test_legacy_multi_strike_history_is_out(self):
        # entries seeded under the older 2/3-rung ladder are >= 1 strike -> OUT under the
        # tightened rule (operator-named consequence: all 5 seeded markets ban immediately)
        hist = {"T-1": ["2026-07-30", "2026-07-31"]}
        _, banned = q._two_strikes(dict(hist), set(), "2026-07-31", _dt("2026-07-31T13:00:00"))
        assert banned == {"T-1"}


class TestStrikesOutKnob:
    """Pins the KALSHI_STRIKES_OUT knob semantics for the 2026-08-03 re-review: below the
    threshold there is no ladder ban (day-latch still applies via the caller) and memory
    pruning works; at the threshold it is OUT with no expiry."""

    def test_below_threshold_no_ban_and_prunes(self, monkeypatch):
        monkeypatch.setattr(q, "STRIKES_OUT", 3)
        hist, banned = q._two_strikes({}, {"T-1"}, "2026-07-30", _dt("2026-07-30T12:00:00"))
        assert banned == set()
        # ancient single strike prunes out at the memory horizon
        hist = {"T-2": ["2026-07-01"]}
        hist, banned = q._two_strikes(hist, set(), "2026-07-31", _dt("2026-07-31T02:00:00"))
        assert "T-2" not in hist and banned == set()

    def test_threshold_reached_is_out_forever(self, monkeypatch):
        monkeypatch.setattr(q, "STRIKES_OUT", 2)
        hist = {"T-1": ["2026-07-29"]}
        hist, banned = q._two_strikes(hist, {"T-1"}, "2026-07-30", _dt("2026-07-30T12:00:00"))
        assert banned == {"T-1"}
        _, banned = q._two_strikes(hist, set(), "2026-09-15", _dt("2026-09-15T12:00:00"))
        assert banned == {"T-1"}, "no expiry, no prune once the threshold is reached"
