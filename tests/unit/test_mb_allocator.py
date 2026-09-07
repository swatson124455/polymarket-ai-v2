"""Unit tests for scripts/mb_allocator.py — the cross-trader envelope
layer (build 2, 2026-09-06 mandate). The properties that matter: the
layer can never raise a stake, never over-commit the bankroll, never
silently pass an unknown tier, and carries NO default tier numbers.
Run: python3 -m pytest tests/unit/test_mb_allocator.py --override-ini "addopts=" """
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "scripts"))
import mb_allocator as al  # noqa: E402
import mb_sizer as msz  # noqa: E402

COMMON = dict(kelly_mult=0.25, concurrency=6, book_depth_usd=1e12,
              min_viable=1.0)


def test_parse_strict():
    assert al.parse_tier_fracs("proven:0.6, confirming:0.1") == \
        {"proven": 0.6, "confirming": 0.1}
    for bad in ("", "   ", "proven", "p:0.6,p:0.1", ":0.5", "p:x"):
        with pytest.raises(ValueError):
            al.parse_tier_fracs(bad)


def test_equal_split_within_tier():
    env = al.allocate_envelopes(
        500.0,
        [{"key": "a", "tier": "proven"}, {"key": "b", "tier": "proven"},
         {"key": "c", "tier": "confirming"}],
        {"proven": 0.6, "confirming": 0.1})
    assert abs(env["a"]["envelope"] - 150.0) < 1e-9
    assert abs(env["b"]["envelope"] - 150.0) < 1e-9
    assert abs(env["c"]["envelope"] - 50.0) < 1e-9


def test_unknown_tier_zero_and_flagged():
    env = al.allocate_envelopes(500.0, [{"key": "x", "tier": "mystery"}],
                                {"proven": 0.5})
    assert env["x"]["envelope"] == 0.0
    assert env["x"]["flagged_unknown_tier"] is True


def test_never_overcommits():
    with pytest.raises(ValueError):
        al.allocate_envelopes(500.0, [{"key": "a", "tier": "p"}],
                              {"p": 0.7, "q": 0.4})
    with pytest.raises(ValueError):
        al.allocate_envelopes(500.0, [{"key": "a", "tier": "p"}],
                              {"p": -0.1})
    with pytest.raises(ValueError):
        al.allocate_envelopes(500.0, [{"key": "a", "tier": "p"},
                                      {"key": "a", "tier": "p"}],
                              {"p": 0.5})   # duplicate key
    env = al.allocate_envelopes(
        500.0, [{"key": k, "tier": t} for k, t in
                (("a", "p"), ("b", "p"), ("c", "q"), ("d", "r"))],
        {"p": 0.6, "q": 0.4})
    assert sum(e["envelope"] for e in env.values()) <= 500.0 + 1e-9


def test_down_only_property():
    """Enveloped stake <= full-bankroll stake, across LCB/fill fixtures
    including the min_viable-to-zero region. The whole layer's contract."""
    for lcb in (-0.1, 0.005, 0.02, 0.10, 0.40):
        for fill in (0.10, 0.50, 0.90):
            for envelope in (0.0, 3.0, 50.0, 500.0):
                full = msz.recommend_stake_from_lcb(
                    lcb, fill, 0.01, bankroll=500.0, **COMMON)
                part = al.stake_in_envelope(envelope, lcb, fill, 0.01,
                                            **COMMON)
                assert part["stake"] <= full["stake"] + 1e-12, \
                    (lcb, fill, envelope)


def test_zero_envelope_structural():
    z = al.stake_in_envelope(0.0, 0.10, 0.50, 0.01, **COMMON)
    assert z["stake"] == 0.0 and "zero_envelope" in z["caps_applied"]
    with pytest.raises(ValueError):
        al.stake_in_envelope(-1.0, 0.10, 0.50, 0.01, **COMMON)


def test_no_default_tier_numbers():
    """No numeric tier fraction may live in this module (operator
    numbers only); allocate_envelopes requires tier_fracs."""
    sig = inspect.signature(al.allocate_envelopes)
    assert sig.parameters["tier_fracs"].default is inspect.Parameter.empty
    with pytest.raises(ValueError):
        al.allocate_envelopes(500.0, [{"key": "a", "tier": "p"}], {})
