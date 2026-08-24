"""Pins for the storm detector's pure state machine (operator ruling #2, 2026-08-24)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalshi_storm_detector as sd                  # noqa: E402

NOW = datetime.datetime(2026, 8, 24, 18, 0, tzinfo=datetime.timezone.utc)


def _m(minutes_ago, mid):
    return (NOW - datetime.timedelta(minutes=minutes_ago), mid)


def test_calm_book_stays_calm():
    e = sd.classify(NOW, [_m(25, 0.98), _m(10, 0.985), _m(1, 0.98)], [], None)
    assert e["state"] == "CALM" and e["dmid30"] < sd.MOVE_TRIG


def test_big_mid_move_trips_storm():
    e = sd.classify(NOW, [_m(20, 0.02), _m(2, 0.13)], [], None)
    assert e["state"] == "STORMY" and e["dmid30"] >= sd.MOVE_TRIG


def test_volume_burst_trips_storm():
    e = sd.classify(NOW, [_m(5, 0.98)], [(NOW - datetime.timedelta(minutes=3), 606.0)], None)
    assert e["state"] == "STORMY" and e["vol30"] >= sd.VOL_TRIG


def test_storm_holds_through_quiet_period_then_clears():
    prev = {"state": "STORMY", "since": "2026-08-24T15:00:00+00:00",
            "last_trigger": "2026-08-24T15:30:00+00:00"}
    # 150 min since last trigger > QUIET_MIN 90 -> clears
    e = sd.classify(NOW, [_m(5, 0.98)], [], prev)
    assert e["state"] == "CALM"
    # only 40 min since last trigger -> still STORMY
    prev2 = dict(prev, last_trigger="2026-08-24T17:20:00+00:00")
    e2 = sd.classify(NOW, [_m(5, 0.98)], [], prev2)
    assert e2["state"] == "STORMY"


def test_old_moves_outside_window_do_not_trip():
    e = sd.classify(NOW, [_m(120, 0.02), _m(1, 0.98)], [], None)
    assert e["state"] == "CALM", "the move happened 2h ago, outside the 30-min window"


def test_no_data_ticker_carries_prior_state():
    prev = {"state": "STORMY", "since": "x", "last_trigger": NOW.isoformat()}
    e = sd.classify(NOW, [], [], prev)
    assert e["state"] == "STORMY", "a dark ticker must not silently reset to CALM"
