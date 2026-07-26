"""OUTER BOUNDS + BORDER CASES, multi-cycle, under fault.

The chaos suite runs the bot at NORMAL settings. This one runs it at the EDGE of every dial and
at the exact boundary of every threshold — the values where an off-by-one or a divide-by-zero
lives — and it deliberately forces the two paths the chaos run never reached (amend, grace).

Boundaries covered: capital cap 0 / 1 (the live park value) / 1e9; footprint 0 / 1 / 500;
join size 1 / huge; horizon disabled / 1 second / 1e9 days; presence floor 0 / enormous;
grace 0 / 1 / enormous; venue failure 0% / 100%; price band edges; inventory exactly at the
soft and hard caps; a program ending exactly NOW; and a score cache exactly at its stale edge.
"""
import json
import os
import random

import pytest

from test_chaos_multicycle import ChaosClient, BOOK, _cfg_chaos, _mk, _run_cycles
from test_live_hardening import q


# ---------------------------------------------------------------------------------------------
# every dial at both ends, driven for real cycles under fault
# ---------------------------------------------------------------------------------------------
BOUNDS = [
    ("capital cap 0",        dict(cap=0.0)),
    ("capital cap 1 (LIVE PARK VALUE)", dict(cap=1.0)),
    ("capital cap 1e9",      dict(cap=1e9)),
    ("grace 0",              dict(grace=0)),
    ("grace 1",              dict(grace=1)),
    ("grace 100000",         dict(grace=100000)),
    ("gate off",             dict(gate=0)),
    ("rank off",             dict(rank=0)),
    ("amend off",            dict(amend=0)),
    ("all flags off",        dict(gate=0, rank=0, amend=0, grace=0)),
    ("all flags on, cap 0",  dict(cap=0.0, gate=1, rank=1, amend=1, grace=5)),
]


@pytest.mark.parametrize("label,kw", BOUNDS, ids=[b[0] for b in BOUNDS])
@pytest.mark.parametrize("fail_rate", [0.0, 1.0], ids=["no-faults", "total-venue-failure"])
def test_dials_at_their_bounds_never_crash(label, kw, fail_rate, monkeypatch, tmp_path):
    d = str(tmp_path)
    fp = [_mk(f"B-{i}") for i in range(6)]
    _cfg_chaos(monkeypatch, d, footprint=fp, **kw)
    rows = _run_cycles(monkeypatch, ChaosClient(random.Random(42), fail_rate=fail_rate), d, 12)
    assert len(rows) == 12, f"{label} @ {fail_rate}: lost plan rows"
    cap = kw.get("cap", 250.0)
    for r in rows:
        assert (r.get("est_capital_usd") or 0) <= cap + 1e-6, f"{label}: capital cap leaked"


@pytest.mark.parametrize("size", [0, 1, 500], ids=["footprint-0", "footprint-1", "footprint-500"])
def test_footprint_size_extremes(size, monkeypatch, tmp_path):
    d = str(tmp_path)
    _cfg_chaos(monkeypatch, d, footprint=[_mk(f"N-{i}") for i in range(size)])
    rows = _run_cycles(monkeypatch, ChaosClient(random.Random(8), fail_rate=0.2), d, 8)
    assert len(rows) == 8
    assert all((r.get("footprint") or 0) <= size for r in rows)


@pytest.mark.parametrize("join", [1, 10 ** 6], ids=["join-1", "join-1e6"])
def test_join_size_extremes_respect_the_market_cap(join, monkeypatch, tmp_path):
    d = str(tmp_path)
    _cfg_chaos(monkeypatch, d, footprint=[_mk(f"J-{i}") for i in range(4)])
    monkeypatch.setattr(q, "JOIN_SIZE", join)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 15.0)
    c = ChaosClient(random.Random(6), fail_rate=0.0)
    _run_cycles(monkeypatch, c, d, 6)
    for o in c.created:
        assert o["price"] * o["count"] <= 15.0 / 2.0 + 0.51, \
            f"join={join} broke the per-market cap: {o}"


@pytest.mark.parametrize("days", [0.0, 1.0 / 86400.0, 1e9],
                         ids=["horizon-disabled", "horizon-1-second", "horizon-1e9-days"])
def test_horizon_extremes(days, monkeypatch, tmp_path):
    d = str(tmp_path)
    _cfg_chaos(monkeypatch, d, footprint=[_mk(f"H-{i}") for i in range(4)])
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", days)
    rows = _run_cycles(monkeypatch, ChaosClient(random.Random(4), fail_rate=0.1), d, 6)
    assert len(rows) == 6


@pytest.mark.parametrize("floor", [0.0, 1e9], ids=["floor-0", "floor-1e9"])
def test_presence_floor_extremes(floor, monkeypatch, tmp_path):
    d = str(tmp_path)
    _cfg_chaos(monkeypatch, d, footprint=[_mk(f"P-{i}") for i in range(4)], gate=1)
    monkeypatch.setattr(q, "MIN_CREDIT_USD", floor)
    c = ChaosClient(random.Random(13), fail_rate=0.0)
    rows = _run_cycles(monkeypatch, c, d, 6)
    if floor >= 1e9:
        assert not c.created, "an impossible floor must reject EVERY market"
    else:
        assert c.created, "a zero floor must reject nothing on a healthy book"
    assert len(rows) == 6


# ---------------------------------------------------------------------------------------------
# THE TWO PATHS THE CHAOS RUN NEVER REACHED — forced here
# ---------------------------------------------------------------------------------------------
def test_amend_path_actually_fires_when_desired_size_shrinks(monkeypatch, tmp_path):
    """The 50-cycle chaos run recorded ZERO amends: the book converged and never shrank. Force it
    — build a resting book, then cut the sizing dial so every market wants LESS at the same price.
    That is the one case Kalshi preserves queue position for, so it must route to amend and NOT to
    cancel+recreate."""
    d = str(tmp_path)
    fp = [_mk(f"A-{i}") for i in range(4)]
    _cfg_chaos(monkeypatch, d, footprint=fp, grace=0, rank=0, amend=1)
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    c = ChaosClient(random.Random(21), fail_rate=0.0)
    _run_cycles(monkeypatch, c, d, 3)
    assert c._resting, "fixture failed to build a resting book"
    before_created = len(c.created)

    monkeypatch.setattr(q, "JOIN_SIZE", 8)          # same price, SMALLER size -> amend territory
    rows = _run_cycles(monkeypatch, c, d, 3)
    amends = sum(r.get("amends", 0) or 0 for r in rows)
    assert amends > 0, "amend path still never fired — the test would be vacuous"
    assert c.calls.get("amend", 0) > 0, "no amend call reached the client"
    # and the amended orders kept their identity rather than being torn down and rebuilt
    assert len(c.created) - before_created < amends + 4, \
        "amend fired but the book was still largely recreated"


def test_grace_path_actually_fires_and_then_releases(monkeypatch, tmp_path):
    """The chaos run recorded ZERO grace retentions. Force it: build a book, then collapse the
    footprint so every ticker rotates out. Grace must engage, hold for exactly its budget, then
    release — a grace that never releases pins capital forever."""
    d = str(tmp_path)
    fp = [_mk(f"G-{i}") for i in range(4)]
    _cfg_chaos(monkeypatch, d, footprint=fp, grace=2, rank=0, amend=0)
    c = ChaosClient(random.Random(22), fail_rate=0.0)
    _run_cycles(monkeypatch, c, d, 3)
    assert c._resting, "fixture failed to build a resting book"

    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    rows = _run_cycles(monkeypatch, c, d, 6)
    tail = [r.get("grace_retained", 0) or 0 for r in rows[3:]]
    assert max(tail) > 0, f"grace never engaged: {tail}"
    assert tail[-1] == 0, f"grace never released: {tail}"


# ---------------------------------------------------------------------------------------------
# exact-threshold border cases
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("inv", [0.0, 2.9, 3.0, 3.1, 15.0, 59.0, 60.0, 61.0, -60.0, 1e6],
                         ids=lambda v: f"inv={v}")
def test_inventory_exactly_on_every_threshold(inv, monkeypatch, tmp_path):
    """INV_TOLERANCE, INV_SOFT_CT and INV_HARD_CT are compared with >= / <. Sitting exactly ON
    each boundary is where an off-by-one flips a de-risk into an accumulate."""
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    monkeypatch.setattr(q, "INV_SOFT_CT", 15.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 60.0)
    monkeypatch.setattr(q, "PRESENCE_GATE", 1)
    quotes = q.desired_quotes(_mk("EDGE"), BOOK["yes_dollars"], BOOK["no_dollars"],
                              q.utcnow(), inv=inv, stats={})
    assert isinstance(quotes, list)
    if abs(inv) >= 3.0:
        reducing = "no" if inv > 0 else "yes"
        acc = [x for x in quotes if x["side"] != reducing]
        red = [x for x in quotes if x["side"] == reducing]
        assert red or not acc, f"inv={inv}: quoted the accumulating side without an exit"


@pytest.mark.parametrize("px", [0.01, 0.04, 0.05, 0.95, 0.96, 0.99],
                         ids=lambda v: f"px={v}")
def test_price_band_edges(px, monkeypatch, tmp_path):
    """MIN/MAX_PRICE_DOLLARS are the band edges; a quote must never be emitted outside them."""
    monkeypatch.setattr(q, "PRESENCE_GATE", 1)
    yl = [[f"{px:.2f}", "5000"]]
    nl = [[f"{max(0.01, 0.98 - px):.2f}", "5000"]]
    quotes = q.desired_quotes(_mk("PX"), yl, nl, q.utcnow(), inv=0.0, stats={})
    for x in quotes:
        assert q.MIN_PRICE_DOLLARS < x["price_dollars"] <= q.MAX_PRICE_DOLLARS


def test_program_ending_exactly_now_and_one_second_out(monkeypatch, tmp_path):
    import datetime as dt
    now = q.utcnow()
    monkeypatch.setattr(q, "PRESENCE_GATE", 1)
    for delta in (dt.timedelta(0), dt.timedelta(seconds=1), dt.timedelta(seconds=-1)):
        m = dict(_mk("END"), end=(now + delta).isoformat(), life_min=1440.0)
        out = q.desired_quotes(m, BOOK["yes_dollars"], BOOK["no_dollars"], now, inv=0.0, stats={})
        assert isinstance(out, list)
        assert q._window_frac_left(m, now) >= 0.0


def test_score_cache_exactly_on_the_stale_edge():
    import kalshi_market_scores as ks
    m = {}
    ks.update(m, "EDGE", 5.0, 0.5, now=1000.0)
    for off, want in ((ks.STALE_S - 1, "scored"), (ks.STALE_S, "stale"), (ks.STALE_S + 1, "stale")):
        _, kind = ks.score(m, "EDGE", 10.0, now=1000.0 + off)
        assert kind == want, f"offset {off}: expected {want}, got {kind}"


def test_total_venue_failure_for_a_hundred_cycles(monkeypatch, tmp_path):
    """The worst realistic outage: nothing works, for hours. The bot must keep emitting plan rows,
    never crash, and never invent an order."""
    d = str(tmp_path)
    _cfg_chaos(monkeypatch, d, footprint=[_mk(f"X-{i}") for i in range(8)])
    c = ChaosClient(random.Random(99), fail_rate=1.0)
    rows = _run_cycles(monkeypatch, c, d, 100)
    assert len(rows) == 100
    assert not c.created, "created orders while every venue call was failing"
