"""Unit tests for scripts/mb_bayes.py — empirical-Bayes head start
(build 3, 2026-09-06 mandate; built to the adversarial-review gate).
Properties: canon-consumption, exact moment math, honest tau2 clipping,
shrinkage limits, degenerate-variance flagging.
Run: python3 -m pytest tests/unit/test_mb_bayes.py --override-ini "addopts=" """
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "scripts"))
import mb_bayes as mb  # noqa: E402


def _rec(tok, fill=0.4):
    return {"trader": "w", "token_id": tok, "detect_ts": 1.0,
            "first_buy": True, "verdict": "OK", "shadow_fill": fill}


def test_moments_canon_and_small_n():
    m = mb.wallet_moments([_rec("a"), _rec("b")], {"a": 1, "b": 0}, {}, {})
    assert m["n"] == 2
    assert abs(m["mean"] - 0.092) < 1e-12   # flat-2% fee edges .592/-.408
    assert abs(m["var"] - 0.5) < 1e-9
    assert mb.wallet_moments([_rec("a")], {"a": 1}, {}, {}) is None


def test_fit_prior_moments_exact():
    moms = [{"n": 100, "mean": 0.10, "var": 1.0},
            {"n": 100, "mean": -0.10, "var": 1.0},
            {"n": 100, "mean": 0.10, "var": 1.0},
            {"n": 100, "mean": -0.10, "var": 1.0}]
    pr = mb.fit_prior(moms, 10)
    between = sum((x - 0.0) ** 2 for x in (0.1, -0.1, 0.1, -0.1)) / 3
    assert abs(pr["mu"]) < 1e-12
    assert abs(pr["tau2"] - (between - 0.01)) < 1e-12
    assert pr["tau2_clipped"] is False


def test_fit_prior_clips_and_guards():
    pr = mb.fit_prior([{"n": 2, "mean": 0.01, "var": 1.0},
                       {"n": 2, "mean": -0.01, "var": 1.0}], 2)
    assert pr["tau2"] == 0.0 and pr["tau2_clipped"] is True
    with pytest.raises(ValueError):
        mb.fit_prior([{"n": 100, "mean": 0.1, "var": 1.0}], 10)
    # min_n excludes small-n wallets from the FIT
    pr2 = mb.fit_prior([{"n": 100, "mean": 0.1, "var": 1.0},
                        {"n": 100, "mean": -0.1, "var": 1.0},
                        {"n": 2, "mean": 9.9, "var": 1.0}], 50)
    assert pr2["n_wallets"] == 2 and abs(pr2["mu"]) < 1e-12


def test_posterior_shrinkage_limits():
    prior = {"mu": 0.0, "tau2": 0.01}
    p = mb.posterior(prior, {"n": 4, "mean": 0.2, "var": 0.04})
    assert 0.0 < p["post_mean"] < 0.2
    assert 0.0 < p["shrink_w"] < 1.0
    p_hi = mb.posterior(prior, {"n": 10000, "mean": 0.2, "var": 0.04})
    assert abs(p_hi["post_mean"] - 0.2) < 1e-3
    p_lo = mb.posterior({"mu": 0.0, "tau2": 0.0},
                        {"n": 4, "mean": 0.2, "var": 0.04})
    assert p_lo["post_mean"] == 0.0 and p_lo["shrink_w"] == 0.0


def test_posterior_degenerate_flagged():
    p = mb.posterior({"mu": 0.0, "tau2": 0.01},
                     {"n": 5, "mean": 0.3, "var": 0.0})
    assert p.get("degenerate_zero_var") is True


def test_no_reimplementation():
    src = inspect.getsource(mb)
    for name in ("per_market_edges", "e_value", "lcb_edge", "canon_fee",
                 "synth_records"):
        assert f"def {name}(" not in src
