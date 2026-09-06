"""Pins for the N4 RAIL — near-money daily clamp (operator "proceed with recs" 2026-09-06).

Shape rail from the blind review: the 2026-09-01 GO-window halt leg was a 100ct fill on a
widebook-admitted near-money DAILY; the D3 new-series clamp exempts credited series
(KXAAAGASD "paid" in the 08-06 feedback table), so the clamp keys on program-window
LENGTH (life_min), never series identity and never time-left. Default 0 = provable no-op.
"""
from test_live_hardening import q
from test_s3_widebook import _YL_WIDE, _NL_WIDE, _cfg

DAILY_LIFE_MIN = 16 * 60.0        # AAAGASD-class 16h window
WEEKLY_LIFE_MIN = 7 * 1440.0      # DIESELW-class 7d window


def _mkt(life_min, usd_day=600.0):
    m = {"ticker": "KXNMD-EV-T1", "target": 1000, "end": "2026-09-07T03:59:00Z",
         "usd_day": usd_day, "df": 0.5}
    if life_min is not None:
        m["life_min"] = life_min
    return m


def _sizes(monkeypatch, life_min, clamp_ct, usd_day=600.0):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "NEARMONEY_DAILY_MAX_CT", clamp_ct)
    monkeypatch.setattr(q, "NEARMONEY_DAILY_LIFE_H", 24.0)
    stats = {}
    qs = q.desired_quotes(_mkt(life_min, usd_day), _YL_WIDE, _NL_WIDE, q.utcnow(),
                          inv=0.0, stats=stats)
    assert stats.get("widebook_admitted") == 1, stats
    return {x["side"]: x["count"] for x in qs}, stats


def test_nm1_off_is_noop(monkeypatch):
    """Flag 0: a 16h daily sizes exactly as WIDEBOOK_MAX_CT allows (byte-identical)."""
    off, s_off = _sizes(monkeypatch, DAILY_LIFE_MIN, 0)
    assert s_off.get("nearmoney_daily_clamped") is None
    assert off and all(c <= 40 for c in off.values())     # WIDEBOOK_MAX_CT=40 fixture


def test_nm2_daily_clamped(monkeypatch):
    """Armed 25: a 16h-window widebook admission caps both sides at 25ct."""
    sides, stats = _sizes(monkeypatch, DAILY_LIFE_MIN, 25)
    assert stats.get("nearmoney_daily_clamped") == 1
    assert sides and all(c <= 25 for c in sides.values())


def test_nm2b_clamped_size_under_credit_floor_refuses(monkeypatch):
    """Armed 25 on a SMALL pool: 25ct models under CAPTURE_MIN_USD_DAY at the widebook
    discounted re-check -> quote NOTHING (cliff law: sub-$1 pays $0). This is the E4
    catch-22 interplay on record (money review 2026-09-01) — pinned as intended."""
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "NEARMONEY_DAILY_MAX_CT", 25)
    monkeypatch.setattr(q, "NEARMONEY_DAILY_LIFE_H", 24.0)
    stats = {}
    qs = q.desired_quotes(_mkt(DAILY_LIFE_MIN, usd_day=100.0), _YL_WIDE, _NL_WIDE,
                          q.utcnow(), inv=0.0, stats=stats)
    assert stats.get("nearmoney_daily_clamped") == 1
    assert qs == [] and stats.get("widebook_credit_skip") == 1, stats


def test_nm3_weekly_untouched(monkeypatch):
    """Armed 25: a 7d-window program is NOT clamped by this rail (window LENGTH test —
    a weekly on its final cliff-clearing day still sizes fully)."""
    sides, stats = _sizes(monkeypatch, WEEKLY_LIFE_MIN, 25)
    assert stats.get("nearmoney_daily_clamped") is None
    off, _ = _sizes(monkeypatch, WEEKLY_LIFE_MIN, 0)
    assert sides == off


def test_nm4_missing_life_min_untouched(monkeypatch):
    """Armed 25: a row with no life_min stays unclamped (fail-open toward legacy for
    the unknown-window case; documented in the knob block)."""
    sides, stats = _sizes(monkeypatch, None, 25)
    assert stats.get("nearmoney_daily_clamped") is None
    off, _ = _sizes(monkeypatch, None, 0)
    assert sides == off


def test_nm5_non_widebook_paths_untouched(monkeypatch):
    """Armed 25: an extreme (out-of-band) book never hits this clamp — the rail lives
    only in the widebook JOIN branch."""
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "NEARMONEY_DAILY_MAX_CT", 25)
    monkeypatch.setattr(q, "NEARMONEY_DAILY_LIFE_H", 24.0)
    yl = [[0.02, 1500.0], [0.01, 1500.0]]     # deep-extreme book, mid outside (0.10,0.90)
    nl = [[0.97, 1500.0], [0.96, 1500.0]]
    stats = {}
    q.desired_quotes(_mkt(DAILY_LIFE_MIN), yl, nl, q.utcnow(), inv=0.0, stats=stats)
    assert stats.get("nearmoney_daily_clamped") is None
