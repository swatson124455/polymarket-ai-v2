"""
Unit tests for bots/mirror_ml_selector.py — ML Trade Selector (S124).

Coverage:
  - MirrorMLSelector construction and default state
  - XGBoost prediction (loaded vs cold-start)
  - Q-learning prediction and state discretization
  - Three-way scoring (score_trade)
  - Shadow mode: should_block always False when models not loaded
  - Live gate: should_block respects strategy config
  - Category encoding
  - Cold-start guard (returns 0.50)
"""
import math
import pickle
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from bots.mirror_ml_selector import (
    ACTION_SKIP,
    ACTION_TRADE,
    MirrorMLSelector,
    _QL_N_ACTIONS,
    _QL_N_STATES,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_features(**overrides):
    """Return a default feature dict."""
    base = {
        "conf_base": 0.55,
        "conf_price_adj": 0.02,
        "conf_conv_adj": 0.01,
        "rel_mult": 1.2,
        "price": 0.45,
        "whale_trade_usd": 500.0,
        "category_encoded": 0.52,
        "consensus": 2,
        "hour_utc": 14.0,
        "side_is_no": 0.0,
        "price_extremity": 0.05,
        "conf_composite": 0.58,
    }
    base.update(overrides)
    return base


def _make_fake_xgb_model(feature_names, always_prob=0.60):
    """Create a mock XGBoost model that returns a fixed probability."""
    model = MagicMock()
    model.predict_proba = MagicMock(
        return_value=np.array([[1 - always_prob, always_prob]])
    )
    return model


def _inject_xgb(sel: MirrorMLSelector, feature_names, always_prob=0.60):
    """Directly inject a fake XGBoost model into the selector (avoids pickle)."""
    sel._xgb_model = _make_fake_xgb_model(feature_names, always_prob)
    sel._xgb_calibrator = None
    sel._feature_names = feature_names
    sel._category_encoding = {"crypto": 0.55, "politics": 0.48}
    sel._model_date = datetime.now(timezone.utc).isoformat()
    sel._xgb_loaded = True


def _save_xgb_pickle(path: Path, feature_names, n_samples=500, always_prob=0.60):
    """Save a fake XGBoost model pickle."""
    payload = {
        "model": _PicklableFakeModel(always_prob),
        "calibrator": None,
        "feature_names": feature_names,
        "category_encoding": {"crypto": 0.55, "politics": 0.48},
        "n_samples": n_samples,
        "cv_auc": 0.62,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "wb") as f:
        pickle.dump(payload, f)


def _save_qtable_pickle(path: Path):
    """Save a fake Q-table pickle with TRADE preferred everywhere."""
    from datetime import datetime, timezone
    q_table = np.zeros((_QL_N_STATES, _QL_N_ACTIONS), dtype=np.float64)
    q_table[:, ACTION_TRADE] = 0.5  # Prefer TRADE
    q_table[:, ACTION_SKIP] = -0.2
    payload = {
        "q_table": q_table,
        "visit_counts": np.ones((_QL_N_STATES, _QL_N_ACTIONS), dtype=np.int64),
        "total_trades": 1000,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "wb") as f:
        pickle.dump(payload, f)


from datetime import datetime, timezone

FEATURE_NAMES = [
    "conf_base", "conf_price_adj", "conf_conv_adj", "rel_mult",
    "price", "whale_trade_usd", "category_encoded", "consensus",
    "hour_utc", "side_is_no", "price_extremity", "conf_composite",
]


class _PicklableFakeModel:
    """Fake XGBoost model that can be pickled (must be module-level class)."""
    def __init__(self, prob):
        self._prob = prob
    def predict_proba(self, X):
        return np.array([[1 - self._prob, self._prob]] * len(X))


# ── Tests ────────────────────────────────────────────────────────────────────

class TestMirrorMLSelectorInit:
    def test_default_state(self):
        sel = MirrorMLSelector()
        assert not sel.loaded
        assert not sel._xgb_loaded
        assert not sel._q_loaded

    def test_score_trade_no_models(self):
        """With no models loaded, all decisions default to True (pass-through)."""
        sel = MirrorMLSelector()
        scores = sel.score_trade(_make_features())
        assert scores["ml_decision_xgb"] is True
        assert scores["ml_decision_ql"] is True
        assert scores["ml_decision_combo"] is True
        assert scores["ml_score_xgb"] is None
        assert scores["ml_score_ql"] is None

    def test_should_block_no_models(self):
        """With no models, should_block always returns False."""
        sel = MirrorMLSelector()
        scores = sel.score_trade(_make_features())
        assert sel.should_block(scores) is False


class TestXGBoostLoading:
    def test_load_missing_file(self):
        sel = MirrorMLSelector()
        assert sel.load_xgb(Path("/nonexistent/model.pkl")) is False
        assert not sel._xgb_loaded

    def test_load_valid_model(self, tmp_path):
        model_path = tmp_path / "model.pkl"
        _save_xgb_pickle(model_path, FEATURE_NAMES)
        sel = MirrorMLSelector()
        assert sel.load_xgb(model_path) is True
        assert sel._xgb_loaded
        assert sel.loaded

    def test_cold_start_guard(self, tmp_path):
        """Model with too few samples is rejected."""
        model_path = tmp_path / "model.pkl"
        _save_xgb_pickle(model_path, FEATURE_NAMES, n_samples=50)
        sel = MirrorMLSelector()
        assert sel.load_xgb(model_path) is False
        assert not sel._xgb_loaded

    def test_predict_xgb_loaded(self):
        sel = MirrorMLSelector()
        _inject_xgb(sel, FEATURE_NAMES, always_prob=0.65)
        prob = sel._predict_xgb(_make_features())
        assert 0.60 <= prob <= 0.70  # Should be close to 0.65

    def test_predict_xgb_not_loaded(self):
        sel = MirrorMLSelector()
        prob = sel._predict_xgb(_make_features())
        assert prob == 0.50  # Cold-start default


class TestQLearningLoading:
    def test_load_missing_file(self):
        sel = MirrorMLSelector()
        assert sel.load_qtable(Path("/nonexistent/qtable.pkl")) is False
        assert not sel._q_loaded

    def test_load_valid_qtable(self, tmp_path):
        ql_path = tmp_path / "qtable.pkl"
        _save_qtable_pickle(ql_path)
        sel = MirrorMLSelector()
        assert sel.load_qtable(ql_path) is True
        assert sel._q_loaded
        assert sel.loaded

    def test_predict_ql_trade_preferred(self, tmp_path):
        ql_path = tmp_path / "qtable.pkl"
        _save_qtable_pickle(ql_path)
        sel = MirrorMLSelector()
        sel.load_qtable(ql_path)
        action, q_trade, q_skip = sel._predict_ql(_make_features())
        assert action == ACTION_TRADE
        assert q_trade > q_skip


class TestStateDiscretization:
    def test_low_confidence_low_price(self):
        sel = MirrorMLSelector()
        idx = sel._discretize_ql_state({"conf_composite": 0.48, "price": 0.20,
                                         "side_is_no": 0, "rel_mult": 0.5, "hour_utc": 3})
        assert 0 <= idx < _QL_N_STATES

    def test_high_confidence_high_price(self):
        sel = MirrorMLSelector()
        idx = sel._discretize_ql_state({"conf_composite": 0.65, "price": 0.80,
                                         "side_is_no": 1, "rel_mult": 1.5, "hour_utc": 20})
        assert 0 <= idx < _QL_N_STATES

    def test_different_inputs_different_states(self):
        sel = MirrorMLSelector()
        idx1 = sel._discretize_ql_state({"conf_composite": 0.48, "price": 0.20,
                                          "side_is_no": 0, "rel_mult": 0.5, "hour_utc": 3})
        idx2 = sel._discretize_ql_state({"conf_composite": 0.65, "price": 0.80,
                                          "side_is_no": 1, "rel_mult": 1.5, "hour_utc": 20})
        assert idx1 != idx2


class TestScoreTrade:
    def test_both_models_loaded(self, tmp_path):
        ql_path = tmp_path / "qtable.pkl"
        _save_qtable_pickle(ql_path)

        sel = MirrorMLSelector()
        _inject_xgb(sel, FEATURE_NAMES, always_prob=0.60)
        sel.load_qtable(ql_path)

        scores = sel.score_trade(_make_features())

        # All three strategies scored
        assert scores["ml_score_xgb"] is not None
        assert scores["ml_score_ql"] is not None
        assert scores["ml_score_combo"] is not None

        # Both accept (XGBoost 0.60 > 0.45, Q-learning TRADE preferred)
        assert scores["ml_decision_xgb"] is True
        assert scores["ml_decision_ql"] is True
        assert scores["ml_decision_combo"] is True

    def test_xgb_rejects_low_score(self):
        sel = MirrorMLSelector()
        _inject_xgb(sel, FEATURE_NAMES, always_prob=0.30)

        scores = sel.score_trade(_make_features())
        assert scores["ml_decision_xgb"] is False  # 0.30 < 0.45 threshold
        assert scores["ml_decision_combo"] is False  # Combo also rejects


class TestShouldBlock:
    def test_xgb_strategy_blocks(self):
        sel = MirrorMLSelector()
        sel._strategy = "xgb"
        scores = {"ml_decision_xgb": False, "ml_decision_ql": True, "ml_decision_combo": False}
        assert sel.should_block(scores) is True

    def test_ql_strategy_passes(self):
        sel = MirrorMLSelector()
        sel._strategy = "ql"
        scores = {"ml_decision_xgb": False, "ml_decision_ql": True, "ml_decision_combo": False}
        assert sel.should_block(scores) is False

    def test_combo_strategy_blocks(self):
        sel = MirrorMLSelector()
        sel._strategy = "combo"
        scores = {"ml_decision_xgb": True, "ml_decision_ql": False, "ml_decision_combo": False}
        assert sel.should_block(scores) is True

    def test_unknown_strategy_passes(self):
        sel = MirrorMLSelector()
        sel._strategy = "unknown"
        scores = {"ml_decision_xgb": False, "ml_decision_ql": False, "ml_decision_combo": False}
        assert sel.should_block(scores) is False


class TestCategoryEncoding:
    def test_known_category(self):
        sel = MirrorMLSelector()
        _inject_xgb(sel, FEATURE_NAMES)
        assert sel.encode_category("crypto") == 0.55
        assert sel.encode_category("politics") == 0.48

    def test_unknown_category(self):
        sel = MirrorMLSelector()
        assert sel.encode_category("nonexistent") == 0.50

    def test_empty_category(self):
        sel = MirrorMLSelector()
        assert sel.encode_category("") == 0.50


class TestGetStats:
    def test_returns_dict(self):
        sel = MirrorMLSelector()
        stats = sel.get_stats()
        assert stats["xgb_loaded"] is False
        assert stats["ql_loaded"] is False
        assert stats["strategy"] == "xgb"
