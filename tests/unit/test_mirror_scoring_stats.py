"""Tests for the v3 scoring statistical core — each test pins a failure
mode the audit showed loses money."""

import numpy as np
import pytest

from bots.mirror_scoring import stats as S


class TestClusterStats:
    def test_matches_hand_computed_small_case(self):
        e = np.array([0.1, 0.3, -0.2, 0.0])
        cl = np.array(["a", "a", "b", "b"])
        mean, se, C = S.cluster_stats(e, cl)
        assert mean == pytest.approx(0.05)
        # cluster sums of residuals: a: (0.05+0.25)=0.30, b: (-0.25-0.05)=-0.30
        expected = np.sqrt((2 / 1) * (0.30**2 + 0.30**2)) / 4
        assert se == pytest.approx(expected)
        assert C == 2

    def test_correlated_clusters_widen_se_vs_iid(self):
        rng = np.random.default_rng(1)
        shock = np.repeat(rng.normal(0, 0.3, 10), 20)
        e = shock + rng.normal(0, 0.1, 200)
        cl = np.repeat(np.arange(10), 20)
        _, se_cr, _ = S.cluster_stats(e, cl)
        se_iid = e.std(ddof=1) / np.sqrt(e.size)
        assert se_cr > 1.5 * se_iid  # naive SE badly overconfident

    def test_weighted_se_uses_weighted_residuals(self):
        e = np.array([0.5, -0.5, 0.5, -0.5])
        w = np.array([1.0, 0.0, 1.0, 0.0])  # only positives deployed
        cl = np.array(["a", "a", "b", "b"])
        mu, se, _ = S.weighted_cluster_stats(e, w, cl)
        assert mu == pytest.approx(0.5)   # per deployed dollar
        assert se == pytest.approx(0.0)   # zero weighted residuals


class TestDegenerateRecords:
    """The tail-risk-seller hole: spotless favorite records must not pass."""

    def test_spotless_record_t_bound_is_neg_inf(self):
        e = np.full(20, 0.05)  # bought at 0.95, won 20/20 => zero variance
        cl = np.arange(20)
        mean, se, C = S.cluster_stats(e, cl)
        assert se == pytest.approx(0.0)
        assert S.t_lower_bound(mean, se, C) == float("-inf")

    def test_spotless_favorite_fails_jeffreys(self):
        # 20/20 event wins at avg price 0.95: exact binomial says the true
        # win rate could easily be < 0.95 => edge LB must be negative.
        assert S.jeffreys_edge_lb(20, 20, 0.95) < 0

    def test_long_spotless_favorite_eventually_passes_jeffreys(self):
        # 400/400 wins at 0.95 IS evidence (LB ~ 0.9905 > 0.95).
        assert S.jeffreys_edge_lb(400, 400, 0.95) > 0

    def test_genuine_edge_passes_jeffreys(self):
        # 45/60 event wins buying at avg 0.55 => real edge.
        assert S.jeffreys_edge_lb(45, 60, 0.55) > 0

    def test_adverse_event_count(self):
        assert S.adverse_event_count(np.array([1, 0, 1, 0, 1])) == 2


class TestBootstrapAndBH:
    def test_bootstrap_p_high_under_null(self):
        rng = np.random.default_rng(7)
        e = rng.normal(0, 0.4, 100)
        cl = np.repeat(np.arange(20), 5)
        p = S.wild_cluster_bootstrap_p(e, cl, n_boot=199, seed=3)
        assert p > 0.05

    def test_bootstrap_p_low_with_real_edge(self):
        rng = np.random.default_rng(8)
        e = 0.15 + rng.normal(0, 0.3, 200)
        cl = np.repeat(np.arange(40), 5)
        p = S.wild_cluster_bootstrap_p(e, cl, n_boot=199, seed=3)
        assert p < 0.05

    def test_bootstrap_degenerate_returns_one(self):
        e = np.full(10, 0.05)
        cl = np.arange(10)
        assert S.wild_cluster_bootstrap_p(e, cl, n_boot=99, seed=1) == 1.0

    def test_bh_known_example(self):
        p = np.array([0.001, 0.008, 0.039, 0.041, 0.60, 0.9])
        admitted = S.bh_fdr(p, q=0.10)
        # thresholds: 0.0167, 0.0333, 0.05, 0.0667...: p3=0.039<=0.05 passes
        assert admitted.tolist() == [True, True, True, True, False, False]

    def test_bh_none_admitted(self):
        assert not S.bh_fdr(np.array([0.5, 0.7, 0.9]), q=0.10).any()


class TestShrinkageAndKelly:
    def test_shrinkage_pulls_toward_grand_mean(self):
        edges = np.array([0.20, 0.02, 0.05, 0.03])
        ses = np.array([0.10, 0.01, 0.02, 0.01])
        shrunk = S.empirical_bayes_shrink(edges, ses)
        # the noisy outlier moves toward the mean more than precise ones
        assert shrunk[0] < edges[0]
        assert abs(shrunk[1] - edges[1]) < abs(shrunk[0] - edges[0])

    def test_kelly_zero_without_adverse_events(self):
        ev = np.array([0.05, 0.04, 0.06, 0.05])
        assert S.kelly_event_weight(0.05, ev, 0.95, n_adverse=0) == 0.0
        assert S.kelly_event_weight(0.05, ev, 0.95, n_adverse=1) == 0.0

    def test_kelly_variance_floor_blocks_cap_ride(self):
        # near-zero event variance would send edge/var to infinity;
        # the pbar(1-pbar) floor must keep the weight modest.
        ev = np.full(10, 0.05)
        w = S.kelly_event_weight(0.05, ev, 0.50, n_adverse=2,
                                 lam=0.25, cap=0.02)
        assert 0 < w <= 0.02
        assert w == pytest.approx(min(0.25 * 0.05 / 0.25, 0.02))

    def test_kelly_cap_binds(self):
        ev = np.array([0.5, -0.4, 0.6, -0.3, 0.5])
        w = S.kelly_event_weight(0.5, ev, 0.5, n_adverse=2, cap=0.02)
        assert w == 0.02

    def test_kelly_zero_on_nonpositive_edge(self):
        ev = np.array([0.1, -0.1, 0.2])
        assert S.kelly_event_weight(0.0, ev, 0.5, n_adverse=2) == 0.0
        assert S.kelly_event_weight(-0.1, ev, 0.5, n_adverse=2) == 0.0

    def test_event_returns_aggregates_per_cluster(self):
        e = np.array([0.2, 0.4, -0.1])
        cl = np.array(["a", "a", "b"])
        ev = S.event_returns(e, None, cl)
        assert sorted(ev.tolist()) == pytest.approx([-0.1, 0.3])
