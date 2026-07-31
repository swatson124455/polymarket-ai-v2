"""Strike-history bookkeeping under the TIERED LOSS LADDER (operator-named 2026-07-31
"do 3$ then 5$ then out", superseding one-strike-out): count-based bans are OFF by default
(STRIKES_OUT=0) — permanent bans now come from the $5 rung (quoter_state['mkt_out'], pinned
in test_loss_ladder_v2.py). The knob remains for the operator's 2026-08-03 re-review."""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


def _dt(s):
    return datetime.datetime.fromisoformat(s + "+00:00")


class TestDefaultCountBansOff:
    def test_default_is_off(self):
        assert q.STRIKES_OUT == 0, "E ladder supersedes count bans (operator 2026-07-31)"

    def test_strikes_recorded_but_never_ban_at_zero(self, monkeypatch):
        monkeypatch.setattr(q, "STRIKES_OUT", 0)
        hist, banned = q._two_strikes({}, {"T-1"}, "2026-07-30", _dt("2026-07-30T12:00:00"))
        assert hist == {"T-1": ["2026-07-30"]} and banned == set()
        hist, banned = q._two_strikes(hist, {"T-1"}, "2026-07-31", _dt("2026-07-31T12:00:00"))
        assert hist["T-1"] == ["2026-07-30", "2026-07-31"] and banned == set()

    def test_memory_prunes_at_zero(self, monkeypatch):
        monkeypatch.setattr(q, "STRIKES_OUT", 0)
        hist = {"T-2": ["2026-07-01"]}
        hist, banned = q._two_strikes(hist, set(), "2026-07-31", _dt("2026-07-31T02:00:00"))
        assert "T-2" not in hist and banned == set()

    def test_same_day_retrip_records_one_strike(self, monkeypatch):
        monkeypatch.setattr(q, "STRIKES_OUT", 0)
        hist, _ = q._two_strikes({}, {"T-1"}, "2026-07-30", _dt("2026-07-30T10:00:00"))
        hist, _ = q._two_strikes(hist, {"T-1"}, "2026-07-30", _dt("2026-07-30T14:00:00"))
        assert hist["T-1"] == ["2026-07-30"]


class TestStrikesOutKnob:
    """The knob's >0 semantics survive for the 2026-08-03 re-review: at the threshold it is
    OUT with no expiry and prune-exempt."""

    def test_below_threshold_no_ban_and_prunes(self, monkeypatch):
        monkeypatch.setattr(q, "STRIKES_OUT", 3)
        hist, banned = q._two_strikes({}, {"T-1"}, "2026-07-30", _dt("2026-07-30T12:00:00"))
        assert banned == set()

    def test_threshold_reached_is_out_forever(self, monkeypatch):
        monkeypatch.setattr(q, "STRIKES_OUT", 2)
        hist = {"T-1": ["2026-07-29"]}
        hist, banned = q._two_strikes(hist, {"T-1"}, "2026-07-30", _dt("2026-07-30T12:00:00"))
        assert banned == {"T-1"}
        hist2, banned = q._two_strikes(hist, set(), "2026-09-15", _dt("2026-09-15T12:00:00"))
        assert banned == {"T-1"}, "no expiry, no prune once the threshold is reached"
        assert hist2["T-1"] == ["2026-07-29", "2026-07-30"], "OUT entries are prune-exempt"

    def test_one_strike_out_semantics_restorable(self, monkeypatch):
        monkeypatch.setattr(q, "STRIKES_OUT", 1)
        _, banned = q._two_strikes({}, {"T-1"}, "2026-07-30", _dt("2026-07-30T12:00:00"))
        assert banned == {"T-1"}
