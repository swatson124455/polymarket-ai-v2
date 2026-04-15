"""Tests for esports_v2/shadow/metrics.py"""
from __future__ import annotations

import pytest

from esports_v2.shadow.metrics import compute_shadow_gate, format_gate_report


class TestComputeShadowGate:
    def test_all_pass(self):
        stats = {
            "n_total": 200,
            "n_singletons": 80,
            "n_resolved": 100,
            "accuracy_singletons": 0.68,  # within 5% of backtest 0.712
            "brier": 0.22,
            "clv_polymarket_mean": 0.035,
        }
        passed, metrics, failures = compute_shadow_gate(stats, backtest_accuracy=0.712)
        assert passed is True
        assert failures == []

    def test_too_few_resolved(self):
        stats = {"n_total": 30, "n_resolved": 20}
        passed, metrics, failures = compute_shadow_gate(stats)
        assert passed is False
        assert any("n_resolved=20 < 50" in f for f in failures)

    def test_accuracy_fails(self):
        stats = {
            "n_total": 200, "n_singletons": 80, "n_resolved": 100,
            "accuracy_singletons": 0.50,
            "brier": 0.22, "clv_polymarket_mean": 0.03,
        }
        passed, _, failures = compute_shadow_gate(stats)
        assert passed is False
        assert any("accuracy_singletons" in f for f in failures)

    def test_brier_fails(self):
        stats = {
            "n_total": 200, "n_singletons": 80, "n_resolved": 100,
            "accuracy_singletons": 0.60,
            "brier": 0.28, "clv_polymarket_mean": 0.03,
        }
        passed, _, failures = compute_shadow_gate(stats)
        assert passed is False
        assert any("brier" in f for f in failures)

    def test_clv_fails(self):
        stats = {
            "n_total": 200, "n_singletons": 80, "n_resolved": 100,
            "accuracy_singletons": 0.60,
            "brier": 0.22, "clv_polymarket_mean": 0.01,
        }
        passed, _, failures = compute_shadow_gate(stats)
        assert passed is False
        assert any("clv_polymarket_mean" in f for f in failures)

    def test_accuracy_drop_fails(self):
        stats = {
            "n_total": 200, "n_singletons": 80, "n_resolved": 100,
            "accuracy_singletons": 0.60,  # backtest was 0.712 → drop 0.112
            "brier": 0.22, "clv_polymarket_mean": 0.03,
        }
        passed, _, failures = compute_shadow_gate(stats, backtest_accuracy=0.712)
        assert passed is False
        assert any("accuracy_drop" in f for f in failures)

    def test_no_clv_data(self):
        stats = {
            "n_total": 200, "n_singletons": 80, "n_resolved": 100,
            "accuracy_singletons": 0.70,
            "brier": 0.20, "clv_polymarket_mean": None,
        }
        passed, _, failures = compute_shadow_gate(stats)
        assert passed is False
        assert any("clv_polymarket_mean=N/A" in f for f in failures)


class TestFormatGateReport:
    def test_pass_report(self):
        report = format_gate_report(True, {"n_total": 100, "n_singletons": 40, "n_resolved": 60}, [])
        assert "PASS" in report
        assert "FAIL" not in report.split("PASS")[0]

    def test_fail_report(self):
        report = format_gate_report(False, {"n_total": 100, "n_singletons": 40, "n_resolved": 60}, ["brier too high"])
        assert "FAIL" in report
        assert "brier too high" in report
