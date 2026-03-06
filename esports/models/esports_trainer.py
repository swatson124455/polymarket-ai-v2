"""
Esports Model Trainer — orchestrates data collection, training, validation, and saving.

Wires EsportsDataCollector → LoLWinModel.train() / CS2EconomyModel.train().
Graduation gate: dual Brier + accuracy — model must achieve >55% accuracy AND
Brier score < 0.24 (no-skill baseline = 0.25) on holdout set before use.
Auto-retrain: runs periodically via LearningScheduler or manual trigger.

Usage::
    trainer = EsportsModelTrainer(pandascore_client=client)
    result = await trainer.train_game("lol", db=db)
    # result = {"game": "lol", "accuracy": 0.62, "brier": 0.21, "graduated": True, ...}
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import numpy as np
from structlog import get_logger

from config.settings import settings

logger = get_logger()

# Settings
_MIN_ACCURACY = float(getattr(settings, "ESPORTS_MODEL_MIN_ACCURACY", 0.55))
_MAX_BRIER = float(getattr(settings, "ESPORTS_MODEL_MAX_BRIER", 0.24))  # no-skill = 0.25
_RETRAIN_INTERVAL = float(getattr(settings, "ESPORTS_RETRAIN_INTERVAL_HOURS", 24)) * 3600
_VALIDATION_SPLIT = 0.2  # 20% holdout for validation
_MIN_LOL_SAMPLES = 50
_MIN_CS2_SAMPLES = 100
_MIN_CS2_UNIQUE_MATCHES = 15  # At least 15 unique matches (not just 100 rounds)
_EARLY_STOPPING_ROUNDS = 20
_ECE_BINS = 10  # Number of bins for Expected Calibration Error


class EsportsModelTrainer:
    """
    Orchestrates training for LoL and CS2 esports models.

    Flow:
      1. Collect historical data from PandaScore (if not already in DB)
      2. Load training data from esports_training_data table
      3. Split into train/validation
      4. Train model
      5. Evaluate on holdout → graduation gate
      6. Save if graduated
    """

    def __init__(self, pandascore_client=None) -> None:
        self._ps = pandascore_client
        self._last_train_time: Dict[str, float] = {}  # game → monotonic timestamp
        self._train_results: Dict[str, Dict] = {}     # game → last training result

    @property
    def train_results(self) -> Dict[str, Dict]:
        """Last training results per game."""
        return dict(self._train_results)

    def needs_retrain(self, game: str) -> bool:
        """Check if a game model needs retraining based on interval."""
        last = self._last_train_time.get(game, 0.0)
        return (time.monotonic() - last) > _RETRAIN_INTERVAL

    async def train_game(
        self,
        game: str,
        db=None,
        collect_if_empty: bool = True,
        days_back: int = 90,
    ) -> Dict[str, Any]:
        """
        Train model for a specific game.

        Args:
            game: 'lol' or 'cs2'.
            db: AsyncSession for DB access.
            collect_if_empty: If True, run data collection when DB has insufficient data.
            days_back: Days of history to collect.

        Returns:
            Dict with: game, accuracy, graduated, samples, error (if any).
        """
        result = {
            "game": game,
            "accuracy": 0.0,
            "brier_score": 1.0,
            "ece": 1.0,
            "graduated": False,
            "samples": 0,
            "error": None,
        }

        try:
            # Step 1: Load existing training data
            from esports.data.esports_data_collector import EsportsDataCollector
            collector = EsportsDataCollector(pandascore_client=self._ps)

            training_data = []
            if db is not None:
                training_data = await collector.get_training_data(db, game)

            min_samples = _MIN_LOL_SAMPLES if game == "lol" else _MIN_CS2_SAMPLES

            # Step 2: Collect if insufficient data
            if len(training_data) < min_samples and collect_if_empty and self._ps:
                logger.info(
                    "EsportsModelTrainer: collecting historical data",
                    game=game,
                    current_rows=len(training_data),
                    min_needed=min_samples,
                )
                stats = await collector.collect_historical(
                    game=game, days_back=days_back, db=db,
                )
                # Reload after collection
                if db is not None:
                    training_data = await collector.get_training_data(db, game)

            result["samples"] = len(training_data)

            if len(training_data) < min_samples:
                result["error"] = f"insufficient data: {len(training_data)} < {min_samples}"
                logger.warning(
                    "EsportsModelTrainer: insufficient data for training",
                    game=game,
                    samples=len(training_data),
                    min_needed=min_samples,
                )
                return result

            # CS2: check unique match count (rounds from same match are NOT independent)
            if game == "cs2":
                unique_matches = len({
                    str(r.get("match_id", "")).split("_g")[0]
                    for r in training_data if r.get("match_id")
                })
                if unique_matches < _MIN_CS2_UNIQUE_MATCHES:
                    result["error"] = (
                        f"insufficient unique CS2 matches: {unique_matches} < {_MIN_CS2_UNIQUE_MATCHES}"
                    )
                    logger.warning(
                        "EsportsModelTrainer: insufficient unique CS2 matches",
                        unique_matches=unique_matches,
                        min_needed=_MIN_CS2_UNIQUE_MATCHES,
                    )
                    return result

            # Step 3: Reverse to oldest-first (DB returns newest-first via ORDER BY created_at DESC)
            # This ensures temporal split trains on OLDER data and validates on NEWER data
            training_data = list(reversed(training_data))
            split_idx = int(len(training_data) * (1 - _VALIDATION_SPLIT))
            train_set = training_data[:split_idx]       # older data = training
            val_set = training_data[split_idx:]          # newer data = validation

            # Step 4: Train
            if game == "lol":
                metrics = await self._train_lol(train_set, val_set)
            elif game == "cs2":
                metrics = await self._train_cs2(train_set, val_set)
            else:
                result["error"] = f"unsupported game: {game}"
                return result

            accuracy = metrics["accuracy"]
            brier = metrics["brier_score"]
            ece = metrics["ece"]

            result["accuracy"] = round(accuracy, 4)
            result["brier_score"] = round(brier, 4)
            result["ece"] = round(ece, 4)
            # Dual gate: accuracy AND Brier score must both pass
            result["graduated"] = accuracy >= _MIN_ACCURACY and brier <= _MAX_BRIER

            self._last_train_time[game] = time.monotonic()
            self._train_results[game] = result

            logger.info(
                "EsportsModelTrainer: training complete",
                game=game,
                accuracy=round(accuracy, 4),
                brier_score=round(brier, 4),
                ece=round(ece, 4),
                graduated=result["graduated"],
                samples=len(training_data),
                train_size=len(train_set),
                val_size=len(val_set),
            )

            # Log CLV stats if available (informational, not part of graduation)
            if db is not None:
                try:
                    from esports.data.esports_db import compute_clv_stats
                    clv = await compute_clv_stats(db, game, days=30)
                    if clv and clv["total"] > 0:
                        result["clv_avg"] = round(clv["avg_clv"], 4)
                        result["clv_hit_rate"] = round(clv["clv_hit_rate"], 4)
                        logger.info(
                            "EsportsModelTrainer: CLV stats",
                            game=game,
                            avg_clv=round(clv["avg_clv"], 4),
                            clv_hit_rate=round(clv["clv_hit_rate"], 4),
                            clv_total=clv["total"],
                        )
                except Exception:
                    pass  # CLV is informational

        except Exception as exc:
            result["error"] = str(exc)
            logger.error("EsportsModelTrainer: training failed", game=game, error=str(exc))

        return result

    async def train_all(self, db=None, days_back: int = 90) -> Dict[str, Dict]:
        """Train all supported games. Returns {game: result}."""
        results = {}
        for game in ("lol", "cs2"):
            results[game] = await self.train_game(game, db=db, days_back=days_back)
        return results

    def is_graduated(self, game: str) -> bool:
        """Check if a game's model passed the graduation gate."""
        r = self._train_results.get(game)
        if r is None:
            return False
        return bool(r.get("graduated", False))

    # ── Game-specific training ──────────────────────────────────────────

    async def _train_lol(
        self, train_set: List[Dict], val_set: List[Dict]
    ) -> Dict[str, float]:
        """Train LoL model and return validation metrics dict."""
        from esports.models.lol_win_model import LoLWinModel

        zero_metrics = {"accuracy": 0.0, "brier_score": 1.0, "ece": 1.0}

        model = LoLWinModel()

        # Determine current patch from most recent data (train_set is oldest-first, look backwards)
        current_patch = ""
        for row in reversed(train_set):
            p = row.get("patch", "")
            if p:
                current_patch = p
                break

        success = await model.train(train_set, current_patch=current_patch)
        if not success:
            return zero_metrics

        # Split val_set into calibration + evaluation (prevent double-dip)
        cal_split = len(val_set) // 2
        cal_set = val_set[:cal_split]
        eval_set = val_set[cal_split:]

        # Calibrate on first half of validation data
        if len(cal_set) >= 20:
            model.calibrate(cal_set)

        # Evaluate on second half (never seen by calibrator)
        metrics = self._evaluate_full(model, eval_set, label_key="blue_win")

        graduated = metrics["accuracy"] >= _MIN_ACCURACY and metrics["brier_score"] <= _MAX_BRIER
        if graduated:
            model.save()
            logger.info(
                "LoLWinModel: saved (graduated)",
                accuracy=round(metrics["accuracy"], 4),
                brier=round(metrics["brier_score"], 4),
                ece=round(metrics["ece"], 4),
            )
        else:
            logger.warning(
                "LoLWinModel: below graduation threshold — NOT saved",
                accuracy=round(metrics["accuracy"], 4),
                brier=round(metrics["brier_score"], 4),
                threshold_acc=_MIN_ACCURACY,
                threshold_brier=_MAX_BRIER,
            )

        return metrics

    async def _train_cs2(
        self, train_set: List[Dict], val_set: List[Dict]
    ) -> Dict[str, float]:
        """Train CS2 model and return validation metrics dict."""
        from esports.models.cs2_economy_model import CS2EconomyModel

        zero_metrics = {"accuracy": 0.0, "brier_score": 1.0, "ece": 1.0}

        model = CS2EconomyModel()
        success = await model.train(train_set, val_data=val_set)
        if not success:
            return zero_metrics

        # Split val_set into calibration + evaluation (prevent double-dip)
        cal_split = len(val_set) // 2
        cal_set = val_set[:cal_split]
        eval_set = val_set[cal_split:]

        # Calibrate on first half of validation data
        if len(cal_set) >= 20:
            model.calibrate(cal_set)

        # Evaluate on second half (never seen by calibrator)
        metrics = self._evaluate_full_cs2(model, eval_set)

        graduated = metrics["accuracy"] >= _MIN_ACCURACY and metrics["brier_score"] <= _MAX_BRIER
        if graduated:
            model.save()
            logger.info(
                "CS2EconomyModel: saved (graduated)",
                accuracy=round(metrics["accuracy"], 4),
                brier=round(metrics["brier_score"], 4),
                ece=round(metrics["ece"], 4),
            )
        else:
            logger.warning(
                "CS2EconomyModel: below graduation threshold — NOT saved",
                accuracy=round(metrics["accuracy"], 4),
                brier=round(metrics["brier_score"], 4),
                threshold_acc=_MIN_ACCURACY,
                threshold_brier=_MAX_BRIER,
            )

        return metrics

    # ── Evaluation ──────────────────────────────────────────────────────

    @staticmethod
    def _evaluate_binary(model, val_set: List[Dict], label_key: str = "blue_win") -> float:
        """Compute accuracy on validation set for LoL model."""
        if not val_set:
            return 0.0

        correct = 0
        total = 0
        for row in val_set:
            label = int(row.get(label_key, 0))
            prob = model.predict(row)
            predicted = 1 if prob > 0.5 else 0
            if predicted == label:
                correct += 1
            total += 1

        return correct / total if total > 0 else 0.0

    @staticmethod
    def _evaluate_binary_cs2(model, val_set: List[Dict]) -> float:
        """Compute accuracy on validation set for CS2 round model."""
        if not val_set:
            return 0.0

        correct = 0
        total = 0
        for row in val_set:
            label = int(row.get("team_a_won_round", 0))
            prob = model.predict_round(row)
            predicted = 1 if prob > 0.5 else 0
            if predicted == label:
                correct += 1
            total += 1

        return correct / total if total > 0 else 0.0

    @staticmethod
    def _evaluate_full(
        model, val_set: List[Dict], label_key: str = "blue_win"
    ) -> Dict[str, float]:
        """Compute accuracy, Brier score, and ECE for LoL model."""
        if not val_set:
            return {"accuracy": 0.0, "brier_score": 1.0, "ece": 1.0}

        probs = []
        labels = []
        correct = 0
        for row in val_set:
            label = int(row.get(label_key, 0))
            prob = model.predict(row)
            probs.append(prob)
            labels.append(label)
            if (1 if prob > 0.5 else 0) == label:
                correct += 1

        n = len(labels)
        accuracy = correct / n
        brier = sum((p - a) ** 2 for p, a in zip(probs, labels)) / n
        ece = EsportsModelTrainer._compute_ece(probs, labels)
        return {"accuracy": accuracy, "brier_score": brier, "ece": ece}

    @staticmethod
    def _evaluate_full_cs2(model, val_set: List[Dict]) -> Dict[str, float]:
        """Compute accuracy, Brier score, and ECE for CS2 round model."""
        if not val_set:
            return {"accuracy": 0.0, "brier_score": 1.0, "ece": 1.0}

        probs = []
        labels = []
        correct = 0
        for row in val_set:
            label = int(row.get("team_a_won_round", 0))
            prob = model.predict_round(row)
            probs.append(prob)
            labels.append(label)
            if (1 if prob > 0.5 else 0) == label:
                correct += 1

        n = len(labels)
        accuracy = correct / n
        brier = sum((p - a) ** 2 for p, a in zip(probs, labels)) / n
        ece = EsportsModelTrainer._compute_ece(probs, labels)
        return {"accuracy": accuracy, "brier_score": brier, "ece": ece}

    @staticmethod
    def _compute_ece(probs: List[float], labels: List[int]) -> float:
        """
        Expected Calibration Error — weighted average gap across probability bins.

        Bins predictions into _ECE_BINS buckets, computes |avg_predicted - avg_actual|
        per bin, weighted by bin size. Perfect calibration = 0.0.
        """
        if not probs:
            return 1.0

        n = len(probs)
        bin_sums = [0.0] * _ECE_BINS
        bin_true = [0.0] * _ECE_BINS
        bin_counts = [0] * _ECE_BINS

        for p, a in zip(probs, labels):
            idx = min(int(p * _ECE_BINS), _ECE_BINS - 1)
            bin_sums[idx] += p
            bin_true[idx] += a
            bin_counts[idx] += 1

        ece = 0.0
        for i in range(_ECE_BINS):
            if bin_counts[i] > 0:
                avg_pred = bin_sums[i] / bin_counts[i]
                avg_actual = bin_true[i] / bin_counts[i]
                ece += (bin_counts[i] / n) * abs(avg_pred - avg_actual)

        return ece
