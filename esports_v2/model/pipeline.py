"""
Full prediction pipeline: XGBoost + Venn-ABERS + MAPIE conformal.

Implements the PredictionPipeline protocol expected by the walk-forward
backtester. Wires B2 (meta-model) + B3 (calibration) + B4 (conformal filter)
into a single fit/predict interface.

Sizing: Quarter-Kelly with $100 cap, consistent with Phase 5v2 risk controls.
"""
from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path
from typing import Dict, List, Optional

import joblib
import numpy as np

from esports_v2.model.meta_model import XGBoostMetaModel
from esports_v2.model.calibrator import VennAbersCalibrator
from esports_v2.model.conformal import ConformalFilter

# S195 Day 2: skops trusted-type whitelist for safe-load.
# skops.io.load rejects any type not in this list to defend against pickle
# code-execution surfaces. Adding a type here is an explicit security review.
# Probe with: skops.io.get_untrusted_types(file=<path>) when the model adds
# new sub-components.
_SKOPS_TRUSTED_TYPES: List[str] = [
    "esports_v2.model.calibrator.VennAbersCalibrator",
    "esports_v2.model.conformal.ConformalFilter",
    "esports_v2.model.meta_model.XGBoostMetaModel",
    "venn_abers.venn_abers.VennAbers",
    "xgboost.core.Booster",
    "xgboost.sklearn.XGBClassifier",
]

logger = logging.getLogger(__name__)

# Sizing constants (Phase 5v2 risk controls)
KELLY_FRACTION = 0.25   # Quarter-Kelly
MAX_BET_USD = 100.0
MAX_BANKROLL_PCT = 0.05
MIN_EDGE = 0.05          # 5% minimum edge to bet
BANKROLL = 20_000.0      # Paper bankroll for sizing


class EsportsPipeline:
    """
    Full prediction pipeline for EsportsBot v2.

    fit() -> train XGBoost, fit Venn-ABERS calibrator, fit conformal filter.
    predict() -> raw prob -> calibrated prob -> conformal set -> Kelly sizing.
    """

    def __init__(
        self,
        xgb_params: Optional[Dict] = None,
        alpha: float = 0.10,
        cal_fraction: float = 0.2,
    ) -> None:
        """
        Args:
            xgb_params: Override XGBoost hyperparameters.
            alpha: Conformal significance level.
            cal_fraction: Fraction of training data held out for calibration.
        """
        self._xgb = XGBoostMetaModel(**(xgb_params or {}))
        self._calibrator = VennAbersCalibrator()
        self._conformal = ConformalFilter(alpha=alpha)
        self._cal_fraction = cal_fraction

    def fit(self, records: List[dict]) -> None:
        """
        Train the full pipeline on historical records.

        Splits records into train (for XGBoost) and calibration (for Venn-ABERS
        + conformal). The split is temporal — last cal_fraction of records used
        for calibration.
        """
        n = len(records)
        if n < 50:
            logger.warning(f"Only {n} records, pipeline may underfit")

        # Temporal split: last cal_fraction for calibration
        cal_size = max(20, int(n * self._cal_fraction))
        train_records = records[:-cal_size]
        cal_records = records[-cal_size:]

        if len(train_records) < 30:
            # Not enough for split — use all for training, calibrate on train
            train_records = records
            cal_records = records

        # B2: Train XGBoost
        self._xgb.fit(train_records)

        # Get raw probabilities on calibration set
        cal_probs = self._xgb.predict_proba_batch(cal_records)
        cal_labels = np.array([r["actual"] for r in cal_records], dtype=np.float32)

        # B3: Fit Venn-ABERS per game
        for game in set(r.get("game", "unknown") for r in cal_records):
            mask = np.array([r.get("game") == game for r in cal_records])
            if mask.sum() < 10:
                continue
            self._calibrator.fit(cal_probs[mask], cal_labels[mask], game)

        # B4: Fit conformal filter on calibrated probabilities
        cal_calibrated = np.array([
            self._calibrator.predict(float(p), r.get("game", "unknown"))[0]
            for p, r in zip(cal_probs, cal_records)
        ])
        self._conformal.fit(cal_calibrated, cal_labels)

        logger.info(
            f"Pipeline fit: {len(train_records)} train, {len(cal_records)} cal"
        )

    def predict(self, record: dict) -> dict:
        """
        Full prediction for a single record.

        Returns dict with:
          p_raw: XGBoost raw probability
          p_model: Venn-ABERS calibrated probability
          p_lower, p_upper: Venn-ABERS interval
          conformal_set: list of class labels
          is_singleton: bool
          kelly_fraction: suggested fraction (0 if not singleton or no edge)
          stake: dollar amount to bet
          edge: model prob - market price (or 0.5 if no market)
        """
        game = record.get("game", "unknown")

        # Raw XGBoost
        p_raw = self._xgb.predict_proba(record)

        # Venn-ABERS calibration
        p_model, p_lower, p_upper = self._calibrator.predict(p_raw, game)

        # Conformal filter
        conf = self._conformal.predict(p_model)

        # Sizing — uses record's market_price (default 0.5 stub if caller
        # doesn't have it yet). Callers that look up the real market_price
        # AFTER predict() must call compute_sizing() to refresh edge/kelly/
        # stake — overriding only `market_price` and `edge` in the result
        # dict produces stale Kelly/stake (S213 root-cause fix in
        # bots/esports_bot_v2.py:_predict_upcoming_matches).
        market_price = record.get("market_price", 0.5)
        sizing = self.compute_sizing(p_model, conf["is_singleton"], market_price)

        return {
            "p_raw": p_raw,
            "p_model": p_model,
            "p_lower": p_lower,
            "p_upper": p_upper,
            "conformal_set": conf["conformal_set"],
            "is_singleton": conf["is_singleton"],
            "kelly_fraction": sizing["kelly_fraction"],
            "stake": sizing["stake"],
            "edge": sizing["edge"],
            "market_price": market_price,
        }

    @staticmethod
    def compute_sizing(
        p_model: float,
        is_singleton: bool,
        market_price: float,
    ) -> Dict[str, float]:
        """Compute Kelly sizing for a given calibrated probability and market
        price. Returns {"edge", "kelly_fraction", "stake"}.

        Extracted so callers can recompute sizing AFTER overriding
        market_price (e.g. when predict() is invoked with a default-stub
        market_price=0.5 at feature-record build time, then the actual
        Polymarket price is found later during the same scan iteration).
        Recomputing only the override fields ("market_price", "edge") and
        leaving "stake" stale was the S213 root-cause for v2's queued-but-
        never-traded predictions: stake was computed against the stub price
        and would coincidentally cap at MAX_BET_USD for high-prob predictions
        regardless of the real market — but the real market price could put
        the trade on the wrong side of the edge gate at place_order time
        downstream.

        Same Kelly criterion as the original inline path: f = (bp - q) / b
        where b = (1/market_price - 1), p = model prob of winning the
        chosen side, q = 1 - p. Quarter-Kelly applied. Stake clamped by
        per-bet and per-bankroll-pct caps.
        """
        edge = abs(p_model - market_price)
        kelly = 0.0
        stake = 0.0

        if is_singleton and edge >= MIN_EDGE:
            if p_model > 0.5:
                b = (1.0 / market_price) - 1.0 if market_price > 0 else 1.0
                p = p_model
            else:
                b = (1.0 / (1 - market_price)) - 1.0 if market_price < 1 else 1.0
                p = 1 - p_model

            q = 1 - p
            if b > 0:
                kelly = max(0.0, (b * p - q) / b)
                kelly *= KELLY_FRACTION  # Quarter-Kelly
                stake = min(kelly * BANKROLL, MAX_BET_USD, BANKROLL * MAX_BANKROLL_PCT)

        return {
            "edge": edge,
            "kelly_fraction": kelly,
            "stake": stake,
        }

    # ── Serialization (S177) ─────────────────────────────────────────────

    STALENESS_SECONDS = 24 * 3600  # 24 hours

    @property
    def is_fitted(self) -> bool:
        """True if XGBoost model has been trained."""
        return self._xgb.is_fitted if hasattr(self._xgb, "is_fitted") else self._xgb._model is not None

    def save(self, path: Path) -> None:
        """Serialize fitted pipeline to disk in skops format.

        Always writes skops regardless of the caller's extension. skops
        provides safe-load semantics (rejects unknown types at load time),
        replacing the pickle-backed joblib that was previously used. Old
        pipeline.joblib files on disk remain loadable via the legacy fallback
        in load() until they age past STALENESS_SECONDS and get refit.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "xgb": self._xgb,
            "calibrator": self._calibrator,
            "conformal": self._conformal,
            "saved_at": time.time(),
        }
        # Defer the import so the rest of the package keeps working when
        # skops is absent (graceful degradation matches venn_abers / mapie).
        import skops.io as sio  # noqa: PLC0415
        sio.dump(state, str(path))
        logger.info("pipeline_saved", path=str(path), format="skops")

    def load(self, path: Path) -> bool:
        """
        Load pipeline from disk. Returns True on success.

        Tries the requested path as skops first. If the path is missing,
        falls back to the legacy joblib snapshot at the same stem with
        suffix .joblib (S195 transition: pre-Day-2 deploys wrote joblib).
        Both paths share the staleness check.

        Catches deserialization errors (XGBoost/sklearn version mismatch,
        skops trusted-type rejection, etc.) and reports as refit-needed.
        """
        path = Path(path)
        skops_path = path if path.suffix == ".skops" else path.with_suffix(".skops")
        legacy_path = path.with_suffix(".joblib")

        chosen = skops_path if skops_path.exists() else (
            legacy_path if legacy_path.exists() else None
        )
        if chosen is None:
            return False

        try:
            age = time.time() - chosen.stat().st_mtime
            if age > self.STALENESS_SECONDS:
                logger.info("pipeline_snapshot_stale", age_hours=age / 3600,
                            path=str(chosen))
                return False

            if chosen.suffix == ".skops":
                import skops.io as sio  # noqa: PLC0415
                state = sio.load(str(chosen), trusted=_SKOPS_TRUSTED_TYPES)
                fmt = "skops"
            else:
                # Legacy joblib path. The next save() call rewrites in skops
                # format, so this fallback drains over one retrain cycle.
                state = joblib.load(chosen)
                fmt = "joblib_legacy"

            self._xgb = state["xgb"]
            self._calibrator = state["calibrator"]
            self._conformal = state["conformal"]
            logger.info("pipeline_loaded", path=str(chosen),
                        age_hours=age / 3600, format=fmt)
            return True
        except (
            ModuleNotFoundError,   # library removed/renamed
            ImportError,           # library not installed
            AttributeError,        # class API changed
            TypeError,             # constructor signature changed
            ValueError,            # numpy dtype mismatch / skops type-check
            EOFError,              # truncated file
            KeyError,              # missing state key
            pickle.UnpicklingError,  # corrupt/incompatible pickle (joblib path)
            OSError,               # file I/O error
        ) as e:
            logger.warning(
                "pipeline_snapshot_incompatible_refitting",
                error=str(e), error_type=type(e).__name__,
                path=str(chosen),
            )
            return False
