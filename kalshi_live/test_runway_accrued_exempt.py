"""Pins for the D2 accrued-re-entry exemption (operator-approved 2026-08-25).

MIN_RUNWAY_H blocks FRESH entries into programs with less runway than the $1 cliff needs.
Measured overcompensation: it also blocked RE-ENTRY into programs the est-feed showed
already accruing for us (~1.6d of DIESELW-26AUG24 accrual forfeited, 08-21). The exemption:
a flat market whose ticker shows accrued > KALSHI_RUNWAY_ACCRUED_EXEMPT_USD in the est-feed
table passes the runway gate (all later gates still apply). Fail-closed on any feed problem.
"""
from datetime import timedelta

from test_live_hardening import q


_YL = [[0.50, 600.0], [0.49, 500.0]]   # healthy near-money book: clears Target both sides,
_NL = [[0.49, 600.0], [0.48, 500.0]]   # our join is a real share (the T4 gas-lane shape)
_YL_DEEP = [[0.50, 100000.0]]          # deep-rival book: capture-poor
_NL_DEEP = [[0.49, 100000.0]]


def _mkt(now, hours_left=10.0):
    return {"ticker": "KXRWY-TEST", "target": 1000,
            "end": (now + timedelta(hours=hours_left)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "usd_day": 150.0, "df": 0.5}


def _cfg(monkeypatch, exempt_usd=0.50):
    monkeypatch.setattr(q, "MIN_RUNWAY_H", 49.0)         # live value
    monkeypatch.setattr(q, "RUNWAY_ACCRUED_EXEMPT_USD", exempt_usd)
    monkeypatch.setattr(q, "CAPTURE_GATE", 0)            # isolate the runway gate
    monkeypatch.setattr(q, "QUALIFIABLE_GATE", False)
    monkeypatch.setattr(q, "STANDDOWN", 0)
    monkeypatch.setattr(q, "PRESENCE_GATE", 0)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "MIN_DEPTH_SYM", 0.0)
    monkeypatch.setattr(q, "MAX_SPREAD_TICKS", 8)
    monkeypatch.setattr(q, "JOIN_SIZE", 40)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "INV_SOFT_CT", 15.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 50.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 60.0)
    monkeypatch.setattr(q, "MIN_PRICE_DOLLARS", 0.003)
    monkeypatch.setattr(q, "MAX_PRICE_DOLLARS", 0.995)


def test_accrued_above_floor_reenters(monkeypatch):
    _cfg(monkeypatch)
    now = q.utcnow()
    monkeypatch.setattr(q, "_est_feed_cached", lambda ts, **k: {"KXRWY-TEST": 0.60})
    stats = {}
    qs = q.desired_quotes(_mkt(now), _YL, _NL, now, inv=0.0, stats=stats)
    assert qs                                          # short runway, but accruing -> enters
    assert stats.get("runway_exempt_accrued") == 1
    assert stats.get("gate_min_runway") is None


def test_accrued_below_floor_still_blocked(monkeypatch):
    _cfg(monkeypatch)
    now = q.utcnow()
    monkeypatch.setattr(q, "_est_feed_cached", lambda ts, **k: {"KXRWY-TEST": 0.30})
    stats = {}
    qs = q.desired_quotes(_mkt(now), _YL, _NL, now, inv=0.0, stats=stats)
    assert qs == []
    assert stats.get("gate_min_runway") == 1
    assert stats.get("runway_exempt_accrued") is None


def test_feed_failure_fails_closed(monkeypatch):
    _cfg(monkeypatch)
    now = q.utcnow()
    def _boom(ts, **k):
        raise RuntimeError("feed torn")
    monkeypatch.setattr(q, "_est_feed_cached", _boom)
    stats = {}
    qs = q.desired_quotes(_mkt(now), _YL, _NL, now, inv=0.0, stats=stats)
    assert qs == [] and stats.get("gate_min_runway") == 1


def test_zero_knob_disables_exemption(monkeypatch):
    _cfg(monkeypatch, exempt_usd=0.0)
    now = q.utcnow()
    monkeypatch.setattr(q, "_est_feed_cached", lambda ts, **k: {"KXRWY-TEST": 5.00})
    stats = {}
    qs = q.desired_quotes(_mkt(now), _YL, _NL, now, inv=0.0, stats=stats)
    assert qs == [] and stats.get("gate_min_runway") == 1


def test_exemption_does_not_bypass_later_gates(monkeypatch):
    # An exempted market must still pass every downstream gate: same accrued ticker on a
    # deep-rival (capture-poor) book with CAPTURE_GATE armed at the D4 floor -> refused.
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "CAPTURE_GATE", 1)
    monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", 2.00)
    monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
    now = q.utcnow()
    monkeypatch.setattr(q, "_est_feed_cached", lambda ts, **k: {"KXRWY-TEST": 0.60})
    stats = {}
    qs = q.desired_quotes(_mkt(now), _YL_DEEP, _NL_DEEP, now, inv=0.0, stats=stats)
    assert qs == []
    assert stats.get("runway_exempt_accrued") == 1     # runway let it through...
    assert stats.get("capture_skipped") == 1           # ...capture still refused it


def test_long_runway_never_consults_feed(monkeypatch):
    # Markets with ample runway never touch the exemption path (no feed dependency).
    _cfg(monkeypatch)
    now = q.utcnow()
    def _boom(ts, **k):
        raise RuntimeError("must not be called")
    monkeypatch.setattr(q, "_est_feed_cached", _boom)
    qs = q.desired_quotes(_mkt(now, hours_left=200.0), _YL, _NL, now, inv=0.0)
    assert qs                                          # normal entry, feed untouched
