"""Tests for B6: Metrics suite."""
import math
import pytest

from esports_v2.backtest.metrics import (
    compute_accuracy,
    compute_brier,
    compute_clv,
    compute_ece,
    compute_log_loss,
    compute_metrics,
    compute_pnl,
    compute_z_score,
    MetricsReport,
)


def _make_pred(p_model, actual, **kwargs):
    return {"p_model": p_model, "actual": actual, **kwargs}


class TestAccuracy:
    def test_perfect(self):
        preds = [_make_pred(0.9, 1), _make_pred(0.1, 0)]
        assert compute_accuracy(preds) == 1.0

    def test_zero(self):
        preds = [_make_pred(0.9, 0), _make_pred(0.1, 1)]
        assert compute_accuracy(preds) == 0.0

    def test_half(self):
        preds = [_make_pred(0.9, 1), _make_pred(0.9, 0)]
        assert compute_accuracy(preds) == 0.5

    def test_empty(self):
        assert compute_accuracy([]) == 0.0


class TestBrier:
    def test_perfect(self):
        preds = [_make_pred(1.0, 1), _make_pred(0.0, 0)]
        assert compute_brier(preds) == 0.0

    def test_worst(self):
        preds = [_make_pred(0.0, 1), _make_pred(1.0, 0)]
        assert compute_brier(preds) == 1.0

    def test_midpoint(self):
        preds = [_make_pred(0.5, 1)]
        assert compute_brier(preds) == 0.25

    def test_empty(self):
        assert compute_brier([]) == 1.0


class TestLogLoss:
    def test_perfect_near(self):
        preds = [_make_pred(0.999, 1)]
        assert compute_log_loss(preds) < 0.01

    def test_worst_near(self):
        preds = [_make_pred(0.001, 1)]
        assert compute_log_loss(preds) > 5.0

    def test_empty(self):
        assert compute_log_loss([]) == float("inf")


class TestECE:
    def test_perfect_calibration(self):
        # If predicted probs match actual rates, ECE ~ 0
        preds = [_make_pred(0.9, 1)] * 9 + [_make_pred(0.9, 0)]
        ece, bins = compute_ece(preds)
        assert ece < 0.15  # Not perfectly 0 due to binning

    def test_returns_bins(self):
        preds = [_make_pred(0.3, 0), _make_pred(0.7, 1)]
        ece, bins = compute_ece(preds)
        assert len(bins) > 0
        assert all("avg_pred" in b for b in bins)


class TestCLV:
    def test_positive_clv(self):
        preds = [_make_pred(0.65, 1, pinnacle_prob=0.55)]
        mean, median = compute_clv(preds)
        assert abs(mean - 0.10) < 0.001

    def test_no_pinnacle(self):
        preds = [_make_pred(0.65, 1)]
        mean, median = compute_clv(preds)
        assert mean == 0.0


class TestZScore:
    def test_small_sample(self):
        preds = [_make_pred(0.7, 1)] * 5
        assert compute_z_score(preds) == 0.0

    def test_positive_signal(self):
        preds = [_make_pred(0.7, 1, market_price=0.5)] * 100
        z = compute_z_score(preds)
        assert z > 0  # Model outperforms market


class TestPnL:
    def test_all_correct(self):
        preds = [_make_pred(0.7, 1, market_price=0.6, stake=10.0)] * 10
        profit, staked, roi, dd = compute_pnl(preds)
        assert profit > 0
        assert staked == 100.0
        assert roi > 0

    def test_all_wrong(self):
        preds = [_make_pred(0.7, 0, market_price=0.6, stake=10.0)] * 10
        profit, staked, roi, dd = compute_pnl(preds)
        assert profit < 0


class TestMetricsReport:
    def test_gate_fails_low_accuracy(self):
        report = MetricsReport(accuracy_singletons=0.50, brier=0.20, clv_mean=0.02, singleton_rate=0.40)
        passed, failures = report.passes_gate()
        assert not passed
        assert any("accuracy" in f for f in failures)

    def test_gate_passes(self):
        report = MetricsReport(
            accuracy_singletons=0.62,
            brier=0.20,
            clv_mean=0.02,
            singleton_rate=0.40,
            n_predictions=100,
        )
        passed, failures = report.passes_gate()
        # May still fail on per-game profit
        assert "accuracy" not in str(failures)
        assert "brier" not in str(failures)

    def test_summary_string(self):
        report = MetricsReport(n_predictions=100, accuracy=0.6)
        s = report.summary()
        assert "Predictions: 100" in s


class TestComputeMetrics:
    def test_full_pipeline(self):
        preds = [
            _make_pred(0.8, 1, game="cs2", is_singleton=True, market_price=0.6, stake=10),
            _make_pred(0.3, 0, game="cs2", is_singleton=True, market_price=0.6, stake=10),
            _make_pred(0.6, 1, game="lol", is_singleton=True, market_price=0.5, stake=10),
            _make_pred(0.4, 0, game="lol", is_singleton=False, market_price=0.5, stake=0),
        ]
        report = compute_metrics(preds)
        assert report.n_predictions == 4
        assert report.n_singletons == 3
        assert "cs2" in report.per_game
        assert "lol" in report.per_game
