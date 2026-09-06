"""Pins for the A3b RAMP-FLOOR SESSION (operator "proceed with remaining open items"
2026-09-06): the "first session after a config change runs at ramp floor" duty,
previously text-only (blind-review hole A3), is code — while the marker file exists,
_d3_ramp_ct and its budget mirror _d3_est_ct return D3_RUNGS[0] for every ticker.
Marker lifecycle lives in kalshi_safe_start.sh (touch on acked change, rm on clean
start). Missing marker = byte-identical ramp.
"""
from test_live_hardening import q

T = "KXRF-EV-T1"
OLD = 1_000_000.0          # first-seen far in the past -> top rung when unfloored
NOW = OLD + 10 * 86400.0


def _setup(monkeypatch, tmp_path, marker):
    f = tmp_path / "RAMP_FLOOR_SESSION"
    if marker:
        f.write_text("armed")
    monkeypatch.setattr(q, "RAMP_FLOOR_FILE", str(f))
    monkeypatch.setattr(q, "_RAMP_FLOOR_CACHE", {"ts": 0.0, "active": False})
    monkeypatch.setattr(q, "OBS_HOLD", 0)
    monkeypatch.setattr(q, "D3_NEWSERIES_MAX_RUNG", -1)   # isolate: no other clamps
    monkeypatch.setattr(q, "_D3_FIRST_SEEN", {T: OLD})


def test_rf1_marker_floors_ramp_to_rung0(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, marker=True)
    stats = {}
    assert q._d3_ramp_ct(T, NOW, {T: OLD}, {}, qstats=stats) == q.D3_RUNGS[0]
    assert stats.get("ramp_floor_session") == 1


def test_rf2_no_marker_full_ramp(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, marker=False)
    stats = {}
    assert q._d3_ramp_ct(T, NOW, {T: OLD}, {}, qstats=stats) == q.D3_RUNGS[-1]
    assert stats.get("ramp_floor_session") is None


def test_rf3_budget_mirror_floors_too(monkeypatch, tmp_path):
    """Budget-estimation parity: the select-budget walk must charge floored size, or
    it re-creates the D1/W6 over-read class the mirror exists to prevent."""
    _setup(monkeypatch, tmp_path, marker=True)
    assert q._d3_est_ct(T, NOW) == q.D3_RUNGS[0]


def test_rf4_budget_mirror_unfloored_without_marker(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, marker=False)
    assert q._d3_est_ct(T, NOW) == q.D3_RUNGS[-1]


def test_rf5_floor_lifts_when_marker_removed(monkeypatch, tmp_path):
    """The ramp clock keeps running under the floor: removing the marker restores the
    ticker's TRUE age rung immediately (after the 60s existence-cache expires — the
    pin resets the cache to model that)."""
    _setup(monkeypatch, tmp_path, marker=True)
    assert q._d3_ramp_ct(T, NOW, {T: OLD}, {}) == q.D3_RUNGS[0]
    import os
    os.remove(q.RAMP_FLOOR_FILE)
    monkeypatch.setattr(q, "_RAMP_FLOOR_CACHE", {"ts": 0.0, "active": False})
    assert q._d3_ramp_ct(T, NOW, {T: OLD}, {}) == q.D3_RUNGS[-1]
