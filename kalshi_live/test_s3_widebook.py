"""Pins for S3 WIDE-BOOK MODE (operator-approved 2026-08-30, strategy review).

A WIDE mid-band book holding Target depth both sides pays DF^k share to depth parked
inside the spread (R3 official-rules canon); the mid-band exclusion was an at-touch
toxicity measurement. This mode admits ONLY flat + wide + both-sides-qualifying +
discounted-credit-clearing books, rests WIDEBOOK_TICKS_INSIDE behind each touch, and
leaves every other doctrine untouched (holding => reduce-only, caps, exits).
Census motivation: the two highest qualifying-uptime markets (90%, 08-26 census) were
exactly this shape and were refused by the old gate. Default 0 = byte-identical.
"""
from test_live_hardening import q


# wide qualifying book: walk needs BOTH levels -> lowest_q 2-3 ticks down, so a
# 3-tick-inside rest is inside the qualifying set with a real DF-weighted share.
_YL_WIDE = [[0.12, 600.0], [0.09, 600.0]]
_NL_WIDE = [[0.13, 600.0], [0.10, 600.0]]
_YL_TOUCHONLY = [[0.12, 1500.0]]          # single-level: a 3-tick-back rest is OUTSIDE
_NL_TOUCHONLY = [[0.13, 1500.0]]          # the walk -> discounted share 0 -> must skip
_YL_NARROW = [[0.49, 1500.0]]
_NL_NARROW = [[0.48, 1500.0]]


def _mkt(usd_day=120.0, target=1000):
    return {"ticker": "KXWB-EV-T1", "target": target,
            # end NOW-RELATIVE (+7d; this file was the date-rot incident of 2026-09-06)
            "end": (q.utcnow() + __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"), "usd_day": usd_day, "df": 0.5}


def _cfg(monkeypatch, mode=1):
    monkeypatch.setattr(q, "WIDEBOOK_MODE", mode)
    monkeypatch.setattr(q, "WIDEBOOK_MIN_SPREAD_TICKS", 20)
    monkeypatch.setattr(q, "WIDEBOOK_TICKS_INSIDE", 2)
    monkeypatch.setattr(q, "WIDEBOOK_MAX_CT", 40)
    monkeypatch.setattr(q, "MID_BAND_OUT", (0.10, 0.90))
    monkeypatch.setattr(q, "CAPTURE_GATE", 1)
    monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", 1.00)
    monkeypatch.setattr(q, "QUALIFIABLE_GATE", True)
    monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "STANDDOWN", 0)
    monkeypatch.setattr(q, "PRESENCE_GATE", 0)
    monkeypatch.setattr(q, "MIN_RUNWAY_H", 0.0)
    monkeypatch.setattr(q, "MIN_PRICE_DOLLARS", 0.003)
    monkeypatch.setattr(q, "MAX_PRICE_DOLLARS", 0.995)
    monkeypatch.setattr(q, "MIN_DEPTH_SYM", 0.0)
    monkeypatch.setattr(q, "MAX_SPREAD_TICKS", 8)
    monkeypatch.setattr(q, "JOIN_SIZE", 40)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "INV_SOFT_CT", 15.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 50.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 60.0)
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 60.0)
    monkeypatch.setattr(q, "REPAIR_CHEAP_FILL", 0)
    monkeypatch.setattr(q, "EVENT_DELTA_DOLLARS", 1)
    monkeypatch.setattr(q, "EVENT_SOFT_USD", 5.25)
    monkeypatch.setattr(q, "EVENT_HARD_USD", 17.50)


def test_w1_mode_off_is_byte_identical_refusal(monkeypatch):
    _cfg(monkeypatch, mode=0)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WIDE, _NL_WIDE, q.utcnow(), inv=0.0, stats=stats)
    assert qs == [] and stats.get("gate_mid_band") == 1


def test_w2_wide_qualifying_book_rests_inside(monkeypatch):
    _cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WIDE, _NL_WIDE, q.utcnow(), inv=0.0, stats=stats)
    assert stats.get("widebook_admitted") == 1
    sides = {x["side"]: x for x in qs}
    assert sides["yes"]["price_dollars"] == 0.10      # 0.12 - 2 ticks
    assert sides["no"]["price_dollars"] == 0.11       # 0.13 - 2 ticks
    assert sides["yes"]["count"] <= 40 and sides["no"]["count"] <= 40
    assert all(x["reason"] == "join" for x in qs)


def test_w3_narrow_mid_band_still_refused(monkeypatch):
    _cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_NARROW, _NL_NARROW, q.utcnow(), inv=0.0, stats=stats)
    assert qs == [] and stats.get("gate_mid_band") == 1
    assert stats.get("widebook_admitted") is None


def test_w4_subtarget_side_refused_before_resting(monkeypatch):
    _cfg(monkeypatch)
    stats = {}
    thin_n = [[0.13, 40.0]]                            # NO side can never reach Target
    qs = q.desired_quotes(_mkt(), _YL_WIDE, thin_n, q.utcnow(), inv=0.0, stats=stats)
    assert qs == []
    assert stats.get("unqualifiable") == 1             # armed D1 gate fires first


def test_w5_discounted_credit_check_bites(monkeypatch):
    # single-level books: at-reference capture is rich, but our 2-tick-back rest falls
    # OUTSIDE the qualifying walk -> discounted share 0 -> the mode must rest NOTHING.
    _cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_TOUCHONLY, _NL_TOUCHONLY, q.utcnow(), inv=0.0,
                          stats=stats)
    assert qs == []
    assert stats.get("widebook_credit_skip") == 1


def test_w6_holding_in_wide_book_stays_reduce_only(monkeypatch):
    _cfg(monkeypatch)
    qs = q.desired_quotes(_mkt(), _YL_WIDE, _NL_WIDE, q.utcnow(), inv=-40.0, cost=0.10)
    assert qs and all(x.get("reason") == "unwind" for x in qs)
