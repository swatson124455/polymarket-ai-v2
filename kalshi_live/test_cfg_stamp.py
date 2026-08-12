"""B2 footgun telemetry — pins.

The two silent footguns this closes (self-review + deploy review 2026-08-12):
  * a malformed KALSHI_D3_RUNGS env silently falls back to 5,10,25,50 with no telemetry —
    the EFFECTIVE ladder must be visible on every plan row, not inferred from env
  * KALSHI_OBS_HOLD=1 is inert unless KALSHI_D3_RAMP=1 — the coupling must be a named
    plan-row alarm, not tribal knowledge
"""
import pytest

import maker_kalshi_quoter as q


def test_stamp_carries_effective_ladder():
    s = q._cfg_stamp()
    assert s["cfg_d3_rungs"] == q.D3_RUNGS       # the EFFECTIVE list, not the env string


def test_obs_hold_inert_alarm(monkeypatch):
    monkeypatch.setattr(q, "OBS_HOLD", 1)
    monkeypatch.setattr(q, "D3_RAMP", 0)
    s = q._cfg_stamp()
    assert s["cfg_obs_hold"] == 1
    assert s["cfg_obs_hold_inert"] == 1          # armed but dead -> named alarm


def test_obs_hold_live_no_alarm(monkeypatch):
    monkeypatch.setattr(q, "OBS_HOLD", 1)
    monkeypatch.setattr(q, "D3_RAMP", 1)
    s = q._cfg_stamp()
    assert "cfg_obs_hold_inert" not in s


def test_off_stamp_still_carries_ladder(monkeypatch):
    monkeypatch.setattr(q, "OBS_HOLD", 0)
    s = q._cfg_stamp()
    assert s["cfg_obs_hold"] == 0
    assert "cfg_obs_hold_inert" not in s
    assert isinstance(s["cfg_d3_rungs"], list) and s["cfg_d3_rungs"]
