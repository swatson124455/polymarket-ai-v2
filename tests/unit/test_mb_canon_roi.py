"""Pins for the 2026-09-06 mb_canon ROI amendment (operator hardcode:
ROI + net winnings, ladder-aware). The mixture generalization exists ONLY
because band_tracker.e_value hard-asserts its band-specific support bound
(-1.02) and ROI atoms with venue fees reach -1.07 — these pins hold the
two implementations identical wherever both are defined, so the grids can
never drift (MEASUREMENT_CANON no-drift discipline).
Run: python3 -m pytest tests/unit/test_mb_canon_roi.py --override-ini "addopts=" """
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "scripts"))
import band_tracker as bt  # noqa: E402
import mb_canon as mc  # noqa: E402


def test_lambda_grid_pinned_to_band_tracker():
    assert mc.LAMBDAS == bt.LAMBDAS


def test_mixture_matches_band_e_value_on_shared_support():
    """On atoms both accept, the two e-values must be bit-identical."""
    for ys in ([], [0.0] * 50, [0.5] * 30, [-0.5, 0.2, 0.9, -1.0],
               [-1.02, 0.3]):
        assert mc.mixture_e_value(ys, y_min=bt.Y_MIN) == bt.e_value(ys)


def test_roi_support_wider_than_band():
    """A venue-fee ROI atom (-1.07) is inside ROI support but would
    assert in the band tracker; the mixture accepts down to the wealth-
    positivity bound (-1.25) so LCB shifts are never artificially cut."""
    assert mc.mixture_e_value([-1.07]) > 0.0
    assert mc.mixture_e_value([-1.24]) > 0.0     # shifted atom, valid
    with pytest.raises(AssertionError):
        bt.e_value([-1.07])
    with pytest.raises(AssertionError):
        mc.mixture_e_value([-1.26])              # would zero a wealth factor


def test_wager_rois_raises_on_corrupt_atom():
    """Raw-atom sanity moved to the data layer: a physically impossible
    ROI (below -1.10) is corruption and must raise loudly."""
    bad = [{"detect_ts": 1.0, "verdict": "OK", "shadow_fill": 0.5,
            "token_id": "x"}]
    with pytest.raises(ValueError):
        mc.wager_rois(bad, {"x": -0.2}, {}, {})   # impossible outcome


def test_lcb_not_truncated_at_one():
    """Truncation A (2026-09-06 first ROI run): never-lost wallets showed
    roi_lcb = exactly +1.000 — the edge-era 1.0 domain cap. With
    m_cap=None the LCB must exceed 1 on overwhelming cheap-share wins."""
    import mb_sizer as msz
    rois = [4.0] * 60
    lcb = msz.lcb_edge(rois, mc.mixture_e_value, mc.MIX_POSITIVITY_FLOOR,
                       m_cap=None)
    assert lcb is not None and lcb > 1.0
    # edge callers keep the historical cap (backward compatibility)
    capped = msz.lcb_edge(rois, mc.mixture_e_value,
                          mc.MIX_POSITIVITY_FLOOR)
    assert capped == pytest.approx(1.0)


def test_lcb_not_truncated_at_support_offset():
    """Truncation B: one full flat-fee loss (ROI exactly -1.02) capped
    every LCB at -1.02+1.10 = +0.08 when the raw-atom floor was misused
    as the search bound. With the positivity floor the search reaches
    -1.02+1.25, so strong evidence must clear +0.08."""
    import mb_sizer as msz
    rois = [-1.02] + [2.0] * 80
    lcb = msz.lcb_edge(rois, mc.mixture_e_value, mc.MIX_POSITIVITY_FLOOR,
                       m_cap=None)
    assert lcb is not None and lcb > 0.08 + 1e-6


def test_wager_rois_ladder_and_formula():
    """Every OK buy is a wager (first_buy NOT required — the ladder
    ruling); roi = (outcome - fill - fee)/fill exactly; unresolved and
    non-OK skipped; ordered by time."""
    recs = [{"detect_ts": 2.0, "first_buy": False, "verdict": "OK",
             "shadow_fill": 0.25, "token_id": "L"},     # ladder add
            {"detect_ts": 1.0, "first_buy": True, "verdict": "OK",
             "shadow_fill": 0.5, "token_id": "L"},
            {"detect_ts": 3.0, "first_buy": True, "verdict": "NO_BOOK",
             "shadow_fill": 0.5, "token_id": "L"},      # not OK
            {"detect_ts": 4.0, "first_buy": True, "verdict": "OK",
             "shadow_fill": 0.5, "token_id": "U"}]      # unresolved
    seq = mc.wager_rois(recs, {"L": 1}, {}, {})
    assert [t for t, _, _ in seq] == [1.0, 2.0]
    assert abs(seq[0][2] - (1 - 0.5 - 0.01) / 0.5) < 1e-12
    assert abs(seq[1][2] - (1 - 0.25 - 0.005) / 0.25) < 1e-12


def test_roi_vs_edge_scale():
    """The reason for the hardcode: a cheap winning share's per-dollar
    return dwarfs its per-share edge. 8c share paying $1 at flat 2% fee:
    edge +0.9184/share but ROI +11.48x/dollar."""
    seq = mc.wager_rois([{"detect_ts": 1.0, "verdict": "OK",
                          "shadow_fill": 0.08, "token_id": "c"}],
                        {"c": 1}, {}, {})
    roi = seq[0][2]
    edge = 1 - 0.08 - 0.02 * 0.08
    assert abs(roi - edge / 0.08) < 1e-12
    assert roi > 11.0
