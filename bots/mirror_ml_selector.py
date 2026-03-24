"""
MirrorBot ML Trade Selector — Three-way shadow race (S124).

Scores every MirrorBot trade with three independent strategies:
  A) XGBoost binary classifier: P(trade wins)
  B) Tabular Q-learning: Q(trade) vs Q(skip)
  C) Combo: both A and B must agree to accept

All three log scores to event_data in shadow mode (default). When
MIRROR_USE_ML_SELECTOR=true, the strategy chosen by MIRROR_ML_STRATEGY
becomes a live gate that rejects low-score trades.

Patterns follow:
  - bots/mirror_calibration.py (shadow/live dual-mode gating)
  - base_engine/execution/rl_trade_timing.py (Q-learning, PER, save/load)
"""
import math
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
from structlog import get_logger

from config.settings import settings

logger = get_logger()

# ── Q-learning constants ──────────────────────────────────────────────────
# State: conf_bin(3) x price_bin(3) x side(2) x rel_bin(3) x hour_bucket(4) = 216
_QL_DIM_SIZES = (3, 3, 2, 3, 4)
_QL_N_STATES = math.prod(_QL_DIM_SIZES)  # 216
_QL_N_ACTIONS = 2  # 0=TRADE, 1=SKIP
ACTION_TRADE = 0
ACTION_SKIP = 1


class MirrorMLSelector:
    """ML-based trade quality selector for MirrorBot.

    Loads pre-trained XGBoost model + Q-table from disk.
    Scores every trade with three strategies.
    Gated by MIRROR_USE_ML_SELECTOR (default false).
    """

    def __init__(self):
        # XGBoost (Option A)
        self._xgb_model = None
        self._xgb_calibrator = None
        self._feature_names: list = []
        self._category_encoding: Dict[str, float] = {}  # target encoding map
        self._xgb_loaded = False

        # Q-learning (Option B)
        self._q_table = np.zeros((_QL_N_STATES, _QL_N_ACTIONS), dtype=np.float64)
        self._q_loaded = False

        # Config
        self._min_score_xgb = float(getattr(settings, "MIRROR_ML_MIN_SCORE", 0.45))
        self._strategy = getattr(settings, "MIRROR_ML_STRATEGY", "xgb")
        self._max_age_days = int(getattr(settings, "MIRROR_ML_MAX_AGE_DAYS", 14))
        self._model_date: Optional[str] = None

        # Cold-start guard
        self._min_training_samples = 300

    @property
    def loaded(self) -> bool:
        """True if at least one model is loaded."""
        return self._xgb_loaded or self._q_loaded

    # ── Load / Save ───────────────────────────────────────────────────────

    def load_xgb(self, path: Optional[Path] = None) -> bool:
        """Load XGBoost model + isotonic calibrator from pickle."""
        if path is None:
            path = Path(getattr(settings, "MIRROR_ML_MODEL_PATH",
                                "models/mirror_ml_selector.pkl"))
        try:
            if not path.exists():
                logger.debug("ml_selector_xgb: no model at %s", path)
                return False
            with open(path, "rb") as f:
                payload = pickle.load(f)

            # Stale model guard
            saved_at = payload.get("saved_at", "")
            if saved_at:
                from datetime import datetime as _dt
                try:
                    save_dt = _dt.fromisoformat(saved_at)
                    age_days = (datetime.now(timezone.utc) - save_dt).days
                    if age_days > self._max_age_days:
                        logger.warning("ml_selector_xgb: model too old (%d days > %d max)",
                                       age_days, self._max_age_days)
                        return False
                except Exception:
                    pass

            # Cold-start guard
            n_samples = payload.get("n_samples", 0)
            if n_samples < self._min_training_samples:
                logger.warning("ml_selector_xgb: insufficient samples (%d < %d)",
                               n_samples, self._min_training_samples)
                return False

            self._xgb_model = payload["model"]
            self._xgb_calibrator = payload.get("calibrator")
            self._feature_names = payload.get("feature_names", [])
            self._category_encoding = payload.get("category_encoding", {})
            self._model_date = saved_at
            self._xgb_loaded = True

            logger.info("ml_selector_xgb loaded: %d samples, AUC=%.3f",
                        n_samples, payload.get("cv_auc", 0.0))
            return True
        except Exception as e:
            logger.debug("ml_selector_xgb load failed: %s", e)
            return False

    def load_qtable(self, path: Optional[Path] = None) -> bool:
        """Load Q-table from pickle."""
        if path is None:
            path = Path("models/mirror_ml_qtable.pkl")
        try:
            if not path.exists():
                logger.debug("ml_selector_ql: no Q-table at %s", path)
                return False
            with open(path, "rb") as f:
                payload = pickle.load(f)

            self._q_table = payload.get("q_table", self._q_table)
            self._q_loaded = True

            n_trades = payload.get("total_trades", 0)
            logger.info("ml_selector_ql loaded: %d trades", n_trades)
            return True
        except Exception as e:
            logger.debug("ml_selector_ql load failed: %s", e)
            return False

    def load_all(self) -> Dict[str, bool]:
        """Attempt to load both models. Returns status dict."""
        return {
            "xgb": self.load_xgb(),
            "ql": self.load_qtable(),
        }

    # ── Prediction ────────────────────────────────────────────────────────

    def _predict_xgb(self, features: Dict[str, float]) -> float:
        """XGBoost P(win). Returns 0.50 if not loaded."""
        if not self._xgb_loaded or self._xgb_model is None:
            return 0.50

        try:
            # Build feature vector in training order
            x = np.array([[features.get(f, 0.0) for f in self._feature_names]])
            raw_prob = self._xgb_model.predict_proba(x)[0, 1]

            # Apply isotonic calibration if available
            if self._xgb_calibrator is not None:
                cal_prob = float(self._xgb_calibrator.predict(
                    np.array([raw_prob])
                )[0])
                return float(np.clip(cal_prob, 0.01, 0.99))

            return float(np.clip(raw_prob, 0.01, 0.99))
        except Exception as e:
            logger.debug("ml_selector_xgb predict error: %s", e)
            return 0.50

    def _predict_ql(self, features: Dict[str, float]) -> Tuple[int, float, float]:
        """Q-learning decision. Returns (action, q_trade, q_skip)."""
        if not self._q_loaded:
            return ACTION_TRADE, 0.0, 0.0

        try:
            state_idx = self._discretize_ql_state(features)
            q_trade = float(self._q_table[state_idx, ACTION_TRADE])
            q_skip = float(self._q_table[state_idx, ACTION_SKIP])
            action = ACTION_TRADE if q_trade >= q_skip else ACTION_SKIP
            return action, q_trade, q_skip
        except Exception as e:
            logger.debug("ml_selector_ql predict error: %s", e)
            return ACTION_TRADE, 0.0, 0.0

    def _discretize_ql_state(self, features: Dict[str, float]) -> int:
        """Convert features to Q-table state index (0 to 215)."""
        # conf_composite: low(0) / medium(1) / high(2)
        conf = features.get("conf_composite", 0.50)
        if conf < 0.52:
            d0 = 0
        elif conf < 0.58:
            d0 = 1
        else:
            d0 = 2

        # price: low(0) / mid(1) / high(2)
        price = features.get("price", 0.50)
        if price < 0.30:
            d1 = 0
        elif price < 0.70:
            d1 = 1
        else:
            d1 = 2

        # side: YES(0) / NO(1)
        d2 = 1 if features.get("side_is_no", 0) > 0.5 else 0

        # rel_mult: low(0) / medium(1) / high(2)
        rel = features.get("rel_mult", 1.0)
        if rel < 0.8:
            d3 = 0
        elif rel < 1.3:
            d3 = 1
        else:
            d3 = 2

        # hour bucket: asia(0) / europe(1) / us_morning(2) / us_afternoon(3)
        hour = int(features.get("hour_utc", 12))
        if hour < 8:
            d4 = 0
        elif hour < 14:
            d4 = 1
        elif hour < 19:
            d4 = 2
        else:
            d4 = 3

        idx = ((d0 * _QL_DIM_SIZES[1] + d1) * _QL_DIM_SIZES[2] + d2) * _QL_DIM_SIZES[3] + d3
        idx = idx * _QL_DIM_SIZES[4] + d4
        return max(0, min(idx, _QL_N_STATES - 1))

    # ── Public API ────────────────────────────────────────────────────────

    def encode_category(self, category: str) -> float:
        """Target-encode a category string. Returns 0.50 (prior) for unknown."""
        if not category:
            return 0.50
        return self._category_encoding.get(category.lower().strip(), 0.50)

    def score_trade(self, features: Dict[str, float]) -> Dict[str, Any]:
        """Score a trade with all three strategies. Returns dict with scores and decisions.

        This is the main entry point called from _execute_mirror_trade().
        Always returns scores for shadow logging. Never raises.
        """
        result: Dict[str, Any] = {
            "ml_score_xgb": None,
            "ml_decision_xgb": True,
            "ml_score_ql": None,
            "ml_q_trade": None,
            "ml_q_skip": None,
            "ml_decision_ql": True,
            "ml_score_combo": None,
            "ml_decision_combo": True,
        }

        # Strategy A: XGBoost
        if self._xgb_loaded:
            xgb_score = self._predict_xgb(features)
            result["ml_score_xgb"] = round(xgb_score, 4)
            result["ml_decision_xgb"] = xgb_score >= self._min_score_xgb

        # Strategy B: Q-learning
        if self._q_loaded:
            ql_action, q_trade, q_skip = self._predict_ql(features)
            result["ml_score_ql"] = round(q_trade - q_skip, 4)  # Q-advantage
            result["ml_q_trade"] = round(q_trade, 4)
            result["ml_q_skip"] = round(q_skip, 4)
            result["ml_decision_ql"] = ql_action == ACTION_TRADE

        # Strategy C: Combo (both must agree)
        result["ml_decision_combo"] = result["ml_decision_xgb"] and result["ml_decision_ql"]

        # Combo score: average of normalized scores
        scores = []
        if result["ml_score_xgb"] is not None:
            scores.append(result["ml_score_xgb"])
        if result["ml_score_ql"] is not None:
            # Normalize Q-advantage to [0, 1] range via sigmoid
            q_adv = result["ml_score_ql"]
            scores.append(1.0 / (1.0 + math.exp(-q_adv)))
        if scores:
            result["ml_score_combo"] = round(sum(scores) / len(scores), 4)

        return result

    def should_block(self, scores: Dict[str, Any]) -> bool:
        """Returns True if the active strategy says to reject this trade.

        Only called when MIRROR_USE_ML_SELECTOR=true (live mode).
        In shadow mode this is never called — all trades pass through.
        """
        strategy = self._strategy

        if strategy == "xgb":
            return not scores.get("ml_decision_xgb", True)
        elif strategy == "ql":
            return not scores.get("ml_decision_ql", True)
        elif strategy == "combo":
            return not scores.get("ml_decision_combo", True)
        else:
            logger.warning("ml_selector: unknown strategy '%s', passing through", strategy)
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Return status for monitoring/dashboard."""
        return {
            "xgb_loaded": self._xgb_loaded,
            "ql_loaded": self._q_loaded,
            "strategy": self._strategy,
            "min_score_xgb": self._min_score_xgb,
            "model_date": self._model_date,
            "live_gate": getattr(settings, "MIRROR_USE_ML_SELECTOR", False),
        }
