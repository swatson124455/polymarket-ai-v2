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


class TestTwoSampleGroupPermutation:
    """Review 2026-07-08: the trader-block permutation test that replaced the
    anti-conservative percentile bootstrap (which fired ~30% on null data)."""

    @staticmethod
    def _blocks(n_traders, k_admitted, bump_admitted, seed):
        r = np.random.default_rng(seed)
        vals, tids, grp = [], [], []
        adm = set(r.choice(n_traders, size=k_admitted, replace=False).tolist())
        for t in range(n_traders):
            m = int(r.integers(5, 40))
            b = bump_admitted if t in adm else 0.0
            vals.append(r.normal(b, 0.25, size=m))
            tids.append(np.full(m, t))
            grp.append(np.full(m, t in adm))
        return (np.concatenate(vals), np.concatenate(tids),
                np.concatenate(grp).astype(bool))

    def test_null_calibrated_not_anticonservative(self):
        """The bug this fixes: on NULL data (no group difference) the FP rate
        must be ~ALPHA, not the ~30% the old bootstrap produced."""
        v, t, g = None, None, None
        rejects = 0
        for i in range(60):
            v, t, g = self._blocks(120, 12, 0.0, seed=200 + i)
            p, _ = S.two_sample_group_permutation_p(v, t, g, n_perm=199, seed=7)
            if p < 0.05:
                rejects += 1
        assert rejects <= 9   # ~5% of 60 = 3; generous ceiling, old test hit ~18

    def test_real_edge_has_power(self):
        v, t, g = self._blocks(120, 12, 0.20, seed=42)
        p, spread = S.two_sample_group_permutation_p(v, t, g, n_perm=299, seed=7)
        assert spread > 0
        assert p < 0.05

    def test_one_whale_cannot_carry_it(self):
        """Trader-equal-weight: a single high-volume lucky trader among noise
        peers must NOT pass (volume weighting failed this ~66% of the time)."""
        r = np.random.default_rng(11)
        vals, tids, grp = [], [], []
        adm = set(r.choice(120, size=12, replace=False).tolist())
        whale = min(adm)
        for tt in range(120):
            if tt == whale:
                vals.append(r.normal(0.40, 0.25, size=1500))  # lucky + huge
            else:
                vals.append(r.normal(0.0, 0.25, size=int(r.integers(5, 40))))
            n = vals[-1].size
            tids.append(np.full(n, tt))
            grp.append(np.full(n, tt in adm))
        v = np.concatenate(vals); t = np.concatenate(tids)
        g = np.concatenate(grp).astype(bool)
        p, _ = S.two_sample_group_permutation_p(v, t, g, n_perm=299, seed=7)
        assert p > 0.05   # the 11 noise admitted peers sink it

    def test_degenerate_single_group_is_one(self):
        v = np.array([0.1, 0.2, 0.3]); t = np.array([1, 2, 3])
        assert S.two_sample_group_permutation_p(v, t, np.array([True] * 3))[0] == 1.0
        assert S.two_sample_group_permutation_p(v, t, np.array([False] * 3))[0] == 1.0
        assert S.two_sample_group_permutation_p(
            np.array([]), np.array([]), np.array([], dtype=bool))[0] == 1.0

    def test_deterministic_under_seed(self):
        v, t, g = self._blocks(40, 6, 0.1, seed=5)
        p1, s1 = S.two_sample_group_permutation_p(v, t, g, n_perm=299, seed=7)
        p2, s2 = S.two_sample_group_permutation_p(v, t, g, n_perm=299, seed=7)
        assert p1 == p2


class TestShrinkageAndKelly:
    def test_shrinkage_pulls_toward_grand_mean(self):
        edges = np.array([0.20, 0.02, 0.05, 0.03])
        ses = np.array([0.10, 0.01, 0.02, 0.01])
        shrunk = S.empirical_bayes_shrink(edges, ses)
        # the noisy outlier moves toward the mean more than precise ones
        assert shrunk[0] < edges[0]
        assert abs(shrunk[1] - edges[1]) < abs(shrunk[0] - edges[0])

    def test_f5_pool_shrink_targets_full_pool_grand_not_winners_mean(self):
        """Shrinking selected winners toward the winners' own mean is
        under-correction (the target itself is selection-inflated). With the
        full pool supplied, shrunk winner edges land closer to the POOL mean."""
        winners = np.array([0.10, 0.12])
        winner_ses = np.array([0.05, 0.05])
        pool_edges = np.array([0.10, 0.12, -0.02, 0.00, -0.05, 0.01])
        pool_ses = np.array([0.05, 0.05, 0.05, 0.05, 0.05, 0.05])
        self_shrunk = S.empirical_bayes_shrink(winners, winner_ses)
        pool_shrunk = S.empirical_bayes_shrink(
            winners, winner_ses, pool_edges=pool_edges, pool_ses=pool_ses
        )
        pool_grand = pool_edges.mean()
        # pool-based shrinkage pulls harder toward the (lower) pool grand mean
        assert pool_shrunk[0] < self_shrunk[0]
        assert pool_shrunk[1] < self_shrunk[1]
        assert all(pool_shrunk >= pool_grand - 1e-12)  # between grand and raw

    def test_f5_omitting_pool_preserves_legacy_behavior(self):
        edges = np.array([0.20, 0.02, 0.05, 0.03])
        ses = np.array([0.10, 0.01, 0.02, 0.01])
        legacy = S.empirical_bayes_shrink(edges, ses)
        explicit_self = S.empirical_bayes_shrink(
            edges, ses, pool_edges=edges, pool_ses=ses
        )
        assert np.allclose(legacy, explicit_self)

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
