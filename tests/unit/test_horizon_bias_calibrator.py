"""
Tests for HorizonBiasCalibrator (base_engine/features/calibration.py).

Le (2026) domain x time-to-resolution bias correction.
Formula: recalibrated = 1 / (1 + ((1-p)/p)^(1/b))

Covers:
  - Unfitted returns identity
  - b=1.0 is identity, b>1 pushes to extremes, b<1 softens
  - Symmetry at p=0.5
  - Priority: domain-specific > cross-domain TTR > global
  - TTR bucket assignment
  - Output clipping [0.01, 0.99]
  - fit_from_trade_events with mocked DB
"""
import numpy as np
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from base_engine.features.calibration import HorizonBiasCalibrator


# =========================================================================
# Unfitted
# =========================================================================

class TestHorizonBiasUnfitted:
    def test_returns_identity(self):
        """Unfitted calibrator returns raw_prob unchanged."""
        cal = HorizonBiasCalibrator(db=None)
        assert not cal.is_fitted
        for p in [0.10, 0.30, 0.50, 0.70, 0.90]:
            assert cal.calibrate(p) == p

    def test_returns_identity_with_category_and_ttr(self):
        """Unfitted calibrator ignores category/ttr_days."""
        cal = HorizonBiasCalibrator(db=None)
        assert cal.calibrate(0.65, category="politics", ttr_days=15.0) == 0.65


# =========================================================================
# Calibration math
# =========================================================================

class TestHorizonBiasCalibrate:
    def test_b_1_is_identity(self):
        """b=1.0 returns input unchanged."""
        cal = HorizonBiasCalibrator(db=None)
        cal._global_b = 1.0
        cal._fitted = True
        for p in [0.10, 0.30, 0.50, 0.70, 0.90]:
            assert cal.calibrate(p) == p

    def test_b_near_1_returns_raw(self):
        """b within 0.01 of 1.0 returns raw_prob (optimization)."""
        cal = HorizonBiasCalibrator(db=None)
        cal._global_b = 1.005
        cal._fitted = True
        assert cal.calibrate(0.70) == 0.70

    def test_b_gt_1_softens(self):
        """b > 1: raises odds exponent < 1, pushes probabilities toward 0.5.
        Formula: recal = 1/(1 + ((1-p)/p)^(1/b)). With 1/b < 1, odds < 1
        become larger (for p>0.5), increasing denominator, reducing recal.
        """
        cal = HorizonBiasCalibrator(db=None)
        cal._global_b = 1.5
        cal._fitted = True
        # p > 0.5 moves toward 0.5 (decreases)
        assert cal.calibrate(0.70) < 0.70
        assert cal.calibrate(0.70) > 0.50
        # p < 0.5 moves toward 0.5 (increases)
        assert cal.calibrate(0.30) > 0.30
        assert cal.calibrate(0.30) < 0.50

    def test_b_lt_1_sharpens(self):
        """b < 1: raises odds exponent > 1, pushes probabilities toward extremes."""
        cal = HorizonBiasCalibrator(db=None)
        cal._global_b = 0.7
        cal._fitted = True
        # p > 0.5 should increase (away from 0.5)
        assert cal.calibrate(0.70) > 0.70
        # p < 0.5 should decrease (away from 0.5)
        assert cal.calibrate(0.30) < 0.30

    def test_symmetry_at_half(self):
        """p=0.5 stays at 0.5 for any b (odds = 1, any power of 1 = 1)."""
        cal = HorizonBiasCalibrator(db=None)
        cal._fitted = True
        for b in [0.5, 0.7, 1.0, 1.5, 2.5]:
            cal._global_b = b
            result = cal.calibrate(0.50)
            assert abs(result - 0.50) < 1e-6, f"b={b} result={result}"

    def test_output_clipped(self):
        """Output stays within [0.01, 0.99]."""
        cal = HorizonBiasCalibrator(db=None)
        cal._global_b = 2.5
        cal._fitted = True
        for p in [0.001, 0.01, 0.50, 0.99, 0.999]:
            result = cal.calibrate(p)
            assert 0.01 <= result <= 0.99, f"p={p} b=2.5 result={result}"


# =========================================================================
# Priority chain: domain-specific > cross-domain TTR > global
# =========================================================================

class TestHorizonBiasPriority:
    def test_domain_specific_takes_priority(self):
        """Domain-specific b_param used when available."""
        cal = HorizonBiasCalibrator(db=None)
        cal._b_params = {
            "politics_0_7d": 1.5,   # domain-specific (b>1 softens)
            "all_0_7d": 0.7,        # cross-domain (b<1 sharpens)
        }
        cal._global_b = 1.0         # global (identity)
        cal._fitted = True
        result = cal.calibrate(0.70, category="politics", ttr_days=3.0)
        # Should use 1.5 (domain-specific), not 0.7 or 1.0
        # b=1.5 softens: 0.70 moves toward 0.50
        assert result < 0.70
        # Verify it used domain-specific (1.5) not cross-domain (0.7)
        result_cross = cal.calibrate(0.70, category="unknown_cat", ttr_days=3.0)
        # unknown_cat falls to all_0_7d with b=0.7 (sharpens: moves away from 0.5)
        assert result_cross > 0.70
        # Domain-specific and cross-domain give different results
        assert abs(result - result_cross) > 0.05

    def test_cross_domain_ttr_fallback(self):
        """When domain-specific missing, falls back to cross-domain TTR."""
        cal = HorizonBiasCalibrator(db=None)
        cal._b_params = {
            "all_0_7d": 0.7,        # cross-domain only (b<1 sharpens)
        }
        cal._global_b = 1.0
        cal._fitted = True
        result = cal.calibrate(0.70, category="crypto", ttr_days=3.0)
        # No crypto_0_7d, so uses all_0_7d with b=0.7 (sharpens)
        assert result > 0.70

    def test_global_fallback_when_no_bucket(self):
        """When no matching bucket, uses global b."""
        cal = HorizonBiasCalibrator(db=None)
        cal._b_params = {
            "politics_7_30d": 1.5,  # wrong bucket for ttr_days=3
        }
        cal._global_b = 0.6         # global (b<1 sharpens)
        cal._fitted = True
        result = cal.calibrate(0.70, category="politics", ttr_days=3.0)
        # ttr_days=3 is 0_7d bucket, but only 7_30d exists for politics
        # No all_0_7d either, falls to global b=0.6 (sharpens)
        assert result > 0.70


# =========================================================================
# TTR bucket assignment
# =========================================================================

class TestHorizonBiasTTRBuckets:
    def _make_cal_with_distinct_buckets(self):
        """Create calibrator with each bucket having a distinct b value."""
        cal = HorizonBiasCalibrator(db=None)
        cal._b_params = {
            "all_0_7d": 0.5,
            "all_7_30d": 0.7,
            "all_30_90d": 1.3,
            "all_90d_plus": 1.8,
        }
        cal._global_b = 1.0
        cal._fitted = True
        return cal

    def test_ttr_0_7d(self):
        """ttr_days=3 → 0_7d bucket (b=0.5, sharpens: pushes away from 0.5)."""
        cal = self._make_cal_with_distinct_buckets()
        result = cal.calibrate(0.70, ttr_days=3.0)
        assert result > 0.70

    def test_ttr_7_30d(self):
        """ttr_days=15 → 7_30d bucket (b=0.7, sharpens)."""
        cal = self._make_cal_with_distinct_buckets()
        result = cal.calibrate(0.70, ttr_days=15.0)
        assert result > 0.70

    def test_ttr_30_90d(self):
        """ttr_days=60 → 30_90d bucket (b=1.3, softens: pushes toward 0.5)."""
        cal = self._make_cal_with_distinct_buckets()
        result = cal.calibrate(0.70, ttr_days=60.0)
        assert result < 0.70

    def test_ttr_90d_plus(self):
        """ttr_days=120 → 90d_plus bucket (b=1.8, strongly softens)."""
        cal = self._make_cal_with_distinct_buckets()
        result = cal.calibrate(0.70, ttr_days=120.0)
        assert result < 0.70
        # 90d+ softens more strongly than 30_90d (b=1.8 vs b=1.3)
        result_30_90 = cal.calibrate(0.70, ttr_days=60.0)
        assert result < result_30_90

    def test_ttr_none_uses_global(self):
        """ttr_days=None skips bucket lookup, uses global b."""
        cal = self._make_cal_with_distinct_buckets()
        result = cal.calibrate(0.70, ttr_days=None)
        # Global b=1.0 → identity
        assert result == 0.70


# =========================================================================
# fit_from_trade_events (mocked DB)
# =========================================================================

class TestHorizonBiasFit:
    @pytest.mark.asyncio
    async def test_insufficient_data_returns_false(self):
        """<15 rows → returns False, stays unfitted."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(None, "sports", 0.6, 0.6, 10.0, 5.0)] * 10

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        class _Ctx:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, *a):
                pass

        mock_db = MagicMock()
        mock_db.session_factory = True
        mock_db.get_session = MagicMock(return_value=_Ctx())

        cal = HorizonBiasCalibrator(db=mock_db)
        result = await cal.fit_from_trade_events(n_days=180)
        assert result is False
        assert not cal.is_fitted

    @pytest.mark.asyncio
    async def test_no_db_returns_false(self):
        """None db → returns False."""
        cal = HorizonBiasCalibrator(db=None)
        result = await cal.fit_from_trade_events(n_days=180)
        assert result is False

    @pytest.mark.asyncio
    async def test_fit_with_sufficient_data(self):
        """50+ rows with valid data → fits successfully."""
        np.random.seed(42)
        n = 100
        rows = []
        for _ in range(n):
            pred_prob = np.random.uniform(0.2, 0.8)
            outcome_pnl = 10.0 if np.random.random() < pred_prob else -5.0
            ttr = np.random.uniform(1, 60)
            rows.append(("TestBot", "sports", pred_prob, pred_prob, outcome_pnl, ttr))

        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        class _Ctx:
            async def __aenter__(self):
                return mock_session
            async def __aexit__(self, *a):
                pass

        mock_db = MagicMock()
        mock_db.session_factory = True
        mock_db.get_session = MagicMock(return_value=_Ctx())

        cal = HorizonBiasCalibrator(db=mock_db)
        result = await cal.fit_from_trade_events(n_days=180)
        assert result is True
        assert cal.is_fitted
        # Global b should be within bounds [0.3, 3.0]
        assert 0.3 <= cal._global_b <= 3.0
