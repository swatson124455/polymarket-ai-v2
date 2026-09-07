"""Pins for the W2 EFFECTIVE-SIZE CAPTURE GATE (KALSHI_CAPTURE_EFFECTIVE_SIZE; operator
'build the effective size gate ... now' 2026-09-07). The live gate compared the FULL-join
model $/day against CAPTURE_MIN_USD_DAY while ramp/floor/clamps rest 5-25ct — admitted
markets a clamped session cannot clear (measured live 09-06/07). Flag scales the gate's
pc by (effective ct / join ct), refuse-only. Default 0 = byte-identical legacy gate.
"""
from test_live_hardening import q
from test_s3_widebook import _YL_WIDE, _NL_WIDE, _cfg as _wb_cfg


def _mkt():
    import datetime
    e = (q.utcnow() + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"ticker": "KXCES-EV-T1", "target": 1000, "end": e, "usd_day": 600.0,
            "df": 0.5, "life_min": 7 * 1440.0}


def _setup(monkeypatch, tmp_path, eff_flag, model_usd_day=2.0, ramp_floor=True):
    _wb_cfg(monkeypatch)                                  # armed CAPTURE_GATE=1, floor 1.00
    monkeypatch.setattr(q, "CAPTURE_EFFECTIVE_SIZE", eff_flag)
    monkeypatch.setattr(q, "D3_RAMP", 1)
    monkeypatch.setattr(q, "JOIN_SIZE", 100)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 200.0)   # per-side $100 -> join well >5ct
    monkeypatch.setattr(q, "NEARMONEY_DAILY_MAX_CT", 0)
    monkeypatch.setattr(q, "_FOOTPRINT_CAPS", {})
    monkeypatch.setattr(q, "_prospective_capture",
                        lambda *a, **k: float(model_usd_day))
    f = tmp_path / "RAMP_FLOOR_SESSION"
    if ramp_floor:
        f.write_text("armed")
    monkeypatch.setattr(q, "RAMP_FLOOR_FILE", str(f))
    monkeypatch.setattr(q, "_RAMP_FLOOR_CACHE", {"ts": 0.0, "active": False})
    monkeypatch.setattr(q, "OBS_HOLD", 0)
    monkeypatch.setattr(q, "D3_NEWSERIES_MAX_RUNG", -1)
    monkeypatch.setattr(q, "_D3_FIRST_SEEN", {"KXCES-EV-T1": 1.0})


def test_ce1_flag_off_full_size_model_admits(monkeypatch, tmp_path):
    """Legacy gate: model $2/day >= $1 floor -> admitted even though the floor rests 5ct."""
    _setup(monkeypatch, tmp_path, eff_flag=0)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WIDE, _NL_WIDE, q.utcnow(), inv=0.0, stats=stats)
    assert stats.get("capture_skipped") is None
    assert qs                                             # quotes rest


def test_ce2_flag_on_floored_session_refused(monkeypatch, tmp_path):
    """W2: same book, same model — at the 5ct floor the gate sees $2 x 5/100 = $0.10
    < $1.00 and REFUSES flat entry (the 09-06/07 sub-cliff shape can't recur)."""
    _setup(monkeypatch, tmp_path, eff_flag=1)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WIDE, _NL_WIDE, q.utcnow(), inv=0.0, stats=stats)
    assert stats.get("capture_skipped") == 1
    assert qs == []


def test_ce3_flag_on_full_ramp_still_admits(monkeypatch, tmp_path):
    """No floor, aged ticker at top rung ~= join size -> scaling ~1 -> admitted. The gate
    only refuses when a clamp actually binds (refuse-only, never over-refuses)."""
    _setup(monkeypatch, tmp_path, eff_flag=1, ramp_floor=False)
    monkeypatch.setattr(q, "WIDEBOOK_MAX_CT", 1000)       # widebook cap not binding
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WIDE, _NL_WIDE, q.utcnow(), inv=0.0, stats=stats)
    assert stats.get("capture_skipped") is None
    assert qs


def test_ce4_holding_reduce_only_unchanged(monkeypatch, tmp_path):
    """De-risk is never gated: holding + refused gate -> reducing quotes, not []."""
    _setup(monkeypatch, tmp_path, eff_flag=1)
    qs = q.desired_quotes(_mkt(), _YL_WIDE, _NL_WIDE, q.utcnow(), inv=-40.0)
    assert qs and all(x.get("reason") == "unwind" for x in qs)
