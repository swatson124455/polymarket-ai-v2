"""Pins for R6 per-underlying exposure (operator-approved 2026-08-25, METERS-FIRST).

Meters always run; the two caps ship DARK (0). Enforcement code is pinned here so arming
later is env-only. Design: KALSHI_R6_UNDERLYING_RISK_DESIGN_2026-08-25.md.
"""
import pytest

from test_live_hardening import q


def test_map_parses_and_merges_gas_family():
    assert q._underlying_of("KXAAAGASW-26AUG31-3.900") == "aaa_gas"
    assert q._underlying_of("KXAAAGASD-26AUG26-4.0550") == "aaa_gas"
    assert q._underlying_of("KXDIESELW-26AUG31-T5.44") == "diesel"     # separate from gas
    assert q._underlying_of("KXNEVERMAPPED-26AUG-X") == "KXNEVERMAPPED"  # own underlying


def test_malformed_map_refuses_loudly():
    with pytest.raises(ValueError):
        q._parse_underlying_map("KXFOO")               # no colon
    with pytest.raises(ValueError):
        q._parse_underlying_map("KXFOO:")              # empty label
    assert q._parse_underlying_map("") == {}


def test_meter_math_held_and_committed(monkeypatch):
    monkeypatch.setattr(q, "EVENT_FALLBACK_BASIS_D", 0.35)
    held_by = {"KXAAAGASW-26AUG31-3.900": -40.0,       # basis 0.01 -> $0.40
               "KXDIESELW-26AUG31-T5.42": -40.0,       # basis 0.02 -> $0.80
               "KXAAAGASD-26AUG26-4.0550": 10.0}       # no basis -> fallback -> $3.50
    cost_by = {"KXAAAGASW-26AUG31-3.900": 0.01, "KXDIESELW-26AUG31-T5.42": 0.02}
    desired = {"KXDIESELW-26AUG31-T5.44": [
        {"side": "yes", "price_dollars": 0.92, "count": 30, "reason": "join"},   # $27.60
        {"side": "no", "price_dollars": 0.01, "count": 40, "reason": "join"},    # $0.40
        {"side": "yes", "price_dollars": 0.99, "count": 40, "reason": "unwind"}]}  # ignored
    held, comm = q._underlying_exposure(held_by, cost_by, desired)
    assert abs(held["aaa_gas"] - (0.40 + 3.50)) < 1e-9
    assert abs(held["diesel"] - 0.80) < 1e-9
    assert abs(comm["diesel"] - (0.80 + 27.60 + 0.40)) < 1e-9
    assert abs(comm["aaa_gas"] - held["aaa_gas"]) < 1e-9   # no gas accumulating intent


def test_caps_dark_by_default():
    assert q.UNDERLYING_MAX_COMMITTED_USD == 0.0
    assert q.UNDERLYING_MAX_HELD_USD == 0.0


def test_committed_cap_strips_accumulating_keeps_unwind(monkeypatch):
    # simulate the cycle block's strip logic at the function level
    held_by = {"KXDIESELW-26AUG31-T5.42": -40.0}
    cost_by = {"KXDIESELW-26AUG31-T5.42": 0.02}
    desired = {
        "KXDIESELW-26AUG31-T5.44": [
            {"side": "yes", "price_dollars": 0.92, "count": 30, "reason": "join"},
            {"side": "yes", "price_dollars": 0.99, "count": 40, "reason": "unwind"}],
        "KXAAAGASW-26AUG31-3.900": [
            {"side": "yes", "price_dollars": 0.99, "count": 40, "reason": "unwind"}]}
    held, comm = q._underlying_exposure(held_by, cost_by, desired)
    assert comm["diesel"] > 20.0                       # would breach a $20 committed cap
    # the strip (mirrors the cycle block): diesel capped -> join dropped, unwinds kept
    strip = {u for u, c in comm.items() if c >= 20.0}
    for t in list(desired):
        if q._underlying_of(t) in strip:
            desired[t] = [x for x in desired[t] if x.get("reason") == "unwind"]
    assert desired["KXDIESELW-26AUG31-T5.44"] == [
        {"side": "yes", "price_dollars": 0.99, "count": 40, "reason": "unwind"}]
    assert len(desired["KXAAAGASW-26AUG31-3.900"]) == 1   # other underlying untouched
