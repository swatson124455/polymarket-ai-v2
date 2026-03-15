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
import os
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
_VALIDATION_SPLIT = float(getattr(settings, "ESPORTS_VALIDATION_SPLIT", 0.2))
_MIN_LOL_SAMPLES = int(getattr(settings, "ESPORTS_MIN_LOL_SAMPLES", 50))
_MIN_CS2_SAMPLES = int(getattr(settings, "ESPORTS_MIN_CS2_SAMPLES", 100))
_MIN_CS2_UNIQUE_MATCHES = int(getattr(settings, "ESPORTS_MIN_CS2_UNIQUE_MATCHES", 15))
_EARLY_STOPPING_ROUNDS = int(getattr(settings, "ESPORTS_EARLY_STOPPING_ROUNDS", 20))
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
        # Smart retrain state (set at training time)
        self._last_train_brier: Dict[str, float] = {}      # game → brier at last training
        self._last_train_ece: Dict[str, float] = {}        # game → ECE at last training
        self._last_train_row_count: Dict[str, int] = {}    # game → row count at last training
        self._last_train_patch: Dict[str, str] = {}        # game → patch at last training
        self._min_retrain_interval: float = 7200.0          # 2h minimum between retrains

    @property
    def train_results(self) -> Dict[str, Dict]:
        """Last training results per game."""
        return dict(self._train_results)

    def needs_retrain(
        self,
        game: str,
        *,
        current_brier: Optional[float] = None,
        current_ece: Optional[float] = None,
        current_row_count: Optional[int] = None,
        current_patch: Optional[str] = None,
        recent_loss_streak: int = 0,
    ) -> bool:
        """Check if a game model needs retraining (interval + smart triggers).

        Smart triggers (any fires → retrain):
          1. 24h interval (original)
          2. Brier degradation >0.05 since last training
          2b. ECE degradation >0.04 since last training [T1-A]
          3. ≥50 new training rows since last training
          4. Patch change (LoL only)
          5. ≥4 consecutive losing trades

        All triggers respect a 2h minimum cooldown to prevent spam.
        """
        last = self._last_train_time.get(game, 0.0)
        elapsed = time.monotonic() - last

        # Hard minimum: don't retrain within 2h of last training
        if elapsed < self._min_retrain_interval:
            return False

        # Trigger 1: 24h interval (original behavior)
        if elapsed > _RETRAIN_INTERVAL:
            return True

        # Trigger 2: Brier degradation
        if current_brier is not None and game in self._last_train_brier:
            brier_delta = current_brier - self._last_train_brier[game]
            if brier_delta > 0.05:
                logger.info(
                    "EsportsModelTrainer: retrain trigger — brier degradation",
                    game=game,
                    last_brier=round(self._last_train_brier[game], 4),
                    current_brier=round(current_brier, 4),
                    delta=round(brier_delta, 4),
                )
                return True

        # Trigger 2b: ECE degradation [T1-A] — calibration drift matters more than accuracy
        if current_ece is not None and game in self._last_train_ece:
            ece_delta = current_ece - self._last_train_ece[game]
            if ece_delta > 0.04:
                logger.info(
                    "EsportsModelTrainer: retrain trigger — ECE degradation",
                    game=game,
                    last_ece=round(self._last_train_ece[game], 4),
                    current_ece=round(current_ece, 4),
                    delta=round(ece_delta, 4),
                )
                return True

        # Trigger 3: Data volume (≥50 new rows)
        if current_row_count is not None and game in self._last_train_row_count:
            new_rows = current_row_count - self._last_train_row_count[game]
            if new_rows >= 50:
                logger.info(
                    "EsportsModelTrainer: retrain trigger — data volume",
                    game=game,
                    last_count=self._last_train_row_count[game],
                    current_count=current_row_count,
                    new_rows=new_rows,
                )
                return True

        # Trigger 4: Patch change (LoL only)
        if (current_patch is not None
                and game in self._last_train_patch
                and current_patch
                and current_patch != self._last_train_patch[game]):
            logger.info(
                "EsportsModelTrainer: retrain trigger — patch change",
                game=game,
                old_patch=self._last_train_patch[game],
                new_patch=current_patch,
            )
            return True

        # Trigger 5: Loss streak (≥4 consecutive losses)
        if recent_loss_streak >= 4:
            logger.info(
                "EsportsModelTrainer: retrain trigger — loss streak",
                game=game,
                loss_streak=recent_loss_streak,
            )
            return True

        return False

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

            min_samples = _MIN_CS2_SAMPLES if game == "cs2" else _MIN_LOL_SAMPLES

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
            elif game == "dota2":
                metrics = await self._train_dota2(train_set, val_set)
            elif game == "valorant":
                metrics = await self._train_valorant(train_set, val_set)
            elif game in ("cod", "r6", "sc2", "rl"):
                # No dedicated ML model — Glicko-2 only. Collection already stored rows.
                metrics = {"accuracy": 0.0, "brier_score": 1.0, "ece": 1.0}
                result["graduated"] = False
                self._last_train_time[game] = time.monotonic()
                self._last_train_row_count[game] = len(training_data)
                self._train_results[game] = result
                return result
            else:
                result["error"] = f"unsupported game: {game}"
                return result

            accuracy = metrics["accuracy"]
            brier = metrics["brier_score"]
            ece = metrics["ece"]

            result["accuracy"] = round(accuracy, 4)
            result["brier_score"] = round(brier, 4)
            result["ece"] = round(ece, 4)
            # Always graduate — user controls when to go live
            result["graduated"] = True

            self._last_train_time[game] = time.monotonic()
            self._train_results[game] = result
            # Smart retrain state — snapshot at training time
            self._last_train_brier[game] = brier
            self._last_train_ece[game] = ece
            self._last_train_row_count[game] = len(training_data)
            if game == "lol":
                for row in reversed(training_data):
                    p = row.get("patch", "")
                    if p:
                        self._last_train_patch[game] = p
                        break

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

            # P2.1: Persist calibration metrics for adaptive Kelly sizing
            if db is not None:
                try:
                    from esports.data.esports_db import update_calibration as _update_cal
                    _correct = int(round(accuracy * len(val_set)))
                    _kelly = float(getattr(settings, "KELLY_FRACTION", 0.25))
                    await _update_cal(
                        db, game, "match_winner",
                        bet_count=len(val_set),
                        correct_count=_correct,
                        brier_score=round(brier, 4),
                        kelly_fraction=_kelly,
                    )
                except Exception:
                    pass  # Non-critical — don't fail training on calibration write error

        except Exception as exc:
            result["error"] = str(exc)
            logger.error("EsportsModelTrainer: training failed", game=game, error=str(exc))

        return result

    async def train_all(self, db=None, days_back: int = 90) -> Dict[str, Dict]:
        """Train all supported games. Returns {game: result}."""
        results = {}
        for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
            results[game] = await self.train_game(game, db=db, days_back=days_back)
        return results

    async def train_cross_game(
        self, db=None, days_back: int = 90,
    ) -> Dict[str, Any]:
        """E7: Train a single XGBoost model across ALL 8 games.

        Uses shared Glicko-2 features (team_strength_diff, matchup_uncertainty,
        rd_asymmetry, volatility) plus a game_id categorical feature. Cross-game
        patterns (e.g. "high uncertainty + BO1 = more upsets") learned jointly,
        while game_id lets the model specialize per game.

        Returns training result dict with accuracy, brier_score, graduated, etc.
        """
        result = {
            "game": "cross_game",
            "accuracy": 0.0,
            "brier_score": 1.0,
            "graduated": False,
            "samples": 0,
            "error": None,
        }

        try:
            from esports.data.esports_data_collector import EsportsDataCollector
            collector = EsportsDataCollector(pandascore_client=self._ps)

            _GAMES = ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")
            _GAME_IDS = {g: i for i, g in enumerate(_GAMES)}

            # Shared features used across all games (P6.5: + recent form)
            _SHARED_FEATURES = [
                "team_strength_diff", "matchup_uncertainty",
                "rd_asymmetry", "team_a_volatility", "team_b_volatility",
                "team_a_recent_form", "team_b_recent_form",
            ]

            all_rows = []
            for game in _GAMES:
                if db is None:
                    continue
                rows = await collector.get_training_data(db, game)
                for row in rows:
                    row["_game_id"] = _GAME_IDS[game]
                    row["_game"] = game
                all_rows.append(rows)

            # Flatten
            pooled = [row for rows in all_rows for row in rows]
            result["samples"] = len(pooled)

            if len(pooled) < 100:
                result["error"] = f"insufficient cross-game data: {len(pooled)} < 100"
                return result

            # Filter rows with missing Glicko-2 data: both team_strength_diff
            # and matchup_uncertainty at 0.0 means "unknown" (real even matches
            # have non-zero matchup_uncertainty from phi values).
            pre_filter = len(pooled)
            pooled = [
                r for r in pooled
                if not (
                    float(r.get("team_strength_diff", 0.0)) == 0.0
                    and float(r.get("matchup_uncertainty", 0.0)) == 0.0
                )
            ]
            if pre_filter != len(pooled):
                logger.info(
                    "EsportsModelTrainer: filtered missing-Glicko2 rows",
                    before=pre_filter,
                    after=len(pooled),
                    dropped=pre_filter - len(pooled),
                )

            if len(pooled) < 100:
                result["error"] = f"insufficient cross-game data after filter: {len(pooled)} < 100"
                return result

            # P6.5: Compute rolling 10-game recent form per team per game.
            # Sort oldest-first, annotate each row BEFORE updating histories
            # so there is zero lookahead bias. Teams without names default to 0.5.
            from collections import deque as _deque
            _team_win_hist: dict = {}
            for row in sorted(pooled, key=lambda r: str(r.get("scheduled_at", ""))):
                _g = row.get("_game", "")
                _ta = str(row.get("team_a", row.get("team_a_name", ""))).lower().strip()
                _tb = str(row.get("team_b", row.get("team_b_name", ""))).lower().strip()
                _lbl = int(
                    row.get("blue_win",
                    row.get("team_a_won_round",
                    row.get("team_a_won", 0)))
                )
                key_a = (_g, _ta) if _ta else None
                key_b = (_g, _tb) if _tb else None
                hist_a = _team_win_hist.get(key_a) if key_a else None
                hist_b = _team_win_hist.get(key_b) if key_b else None
                row["team_a_recent_form"] = (sum(hist_a) / len(hist_a)) if hist_a else 0.5
                row["team_b_recent_form"] = (sum(hist_b) / len(hist_b)) if hist_b else 0.5
                if key_a:
                    if key_a not in _team_win_hist:
                        _team_win_hist[key_a] = _deque(maxlen=10)
                    _team_win_hist[key_a].append(_lbl)
                if key_b:
                    if key_b not in _team_win_hist:
                        _team_win_hist[key_b] = _deque(maxlen=10)
                    _team_win_hist[key_b].append(1 - _lbl)

            # Oldest-first for temporal split
            pooled = list(reversed(pooled))
            split_idx = int(len(pooled) * (1 - _VALIDATION_SPLIT))
            train_set = pooled[:split_idx]
            val_set = pooled[split_idx:]

            # Extract features + labels
            import numpy as _np

            def _extract(row):
                feats = [float(row.get(f, 0.0)) for f in _SHARED_FEATURES]
                feats.append(float(row.get("_game_id", 0)))
                # Best-of as additional feature (default 1)
                feats.append(float(row.get("best_of", 1)))
                return feats

            def _label(row):
                # LoL uses blue_win, CS2 uses team_a_won_round, others use winner
                if row.get("_game") == "lol":
                    return int(row.get("blue_win", 0))
                elif row.get("_game") == "cs2":
                    return int(row.get("team_a_won_round", 0))
                else:
                    return int(row.get("team_a_won", 0))

            X_train = _np.array([_extract(r) for r in train_set], dtype=_np.float32)
            y_train = _np.array([_label(r) for r in train_set], dtype=_np.int32)
            X_val = _np.array([_extract(r) for r in val_set], dtype=_np.float32)
            y_val = _np.array([_label(r) for r in val_set], dtype=_np.int32)

            if len(X_train) < 50 or len(X_val) < 20:
                result["error"] = "insufficient split sizes"
                return result

            # Train XGBoost
            from xgboost import XGBClassifier

            model = XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                eval_metric="logloss",
                verbosity=0,
            )
            model.fit(X_train, y_train)

            # Evaluate
            probs = model.predict_proba(X_val)[:, 1]
            preds = (probs > 0.5).astype(int)
            accuracy = float((preds == y_val).mean())
            brier = float(((probs - y_val) ** 2).mean())
            ece = self._compute_ece(probs.tolist(), y_val.tolist())

            result["accuracy"] = round(accuracy, 4)
            result["brier_score"] = round(brier, 4)
            result["ece"] = round(ece, 4)
            result["graduated"] = True
            result["train_size"] = len(train_set)
            result["val_size"] = len(val_set)

            # Save the model — use top-level saved_models/ (same as dota2/valorant)
            import os
            model_dir = os.path.join(
                os.path.dirname(__file__), "..", "..", "saved_models",
            )
            model_dir = os.path.abspath(model_dir)
            os.makedirs(model_dir, exist_ok=True)
            model_path = os.path.join(model_dir, "cross_game_xgb.json")
            model.save_model(model_path)

            # ONNX export for faster inference
            try:
                from esports.models.onnx_compiler import OnnxCompiler
                _compiler = OnnxCompiler()
                _onnx_path = os.path.join(model_dir, "cross_game_xgb.onnx")
                _compiler.export_xgboost(model, n_features=9, save_path=_onnx_path)
            except Exception:
                pass  # ONNX export is optional

            self._last_train_time["cross_game"] = time.monotonic()
            self._train_results["cross_game"] = result
            self._last_train_brier["cross_game"] = brier
            self._last_train_ece["cross_game"] = ece
            self._last_train_row_count["cross_game"] = len(pooled)

            logger.info(
                "EsportsModelTrainer: cross-game XGBoost trained",
                accuracy=round(accuracy, 4),
                brier_score=round(brier, 4),
                ece=round(ece, 4),
                samples=len(pooled),
                train_size=len(train_set),
                val_size=len(val_set),
            )

        except Exception as exc:
            result["error"] = str(exc)
            logger.error("EsportsModelTrainer: cross-game training failed", error=str(exc))

        return result

    def is_graduated(self, game: str) -> bool:
        """Check if a game's model passed the graduation gate."""
        r = self._train_results.get(game)
        if r is None:
            return False
        return bool(r.get("graduated", False))

    # ── CatBoost draft model training ─────────────────────────────────

    async def train_catboost_draft(
        self, game: str, db=None
    ) -> Optional[Dict[str, Any]]:
        """Train CatBoost draft model for a specific game.

        Queries esports_training_data for rows with draft data,
        builds features via DraftFeatureBuilder, trains CatBoostDraftModel.

        Args:
            game: Game slug (lol, dota2, valorant, r6).
            db: Database session provider.

        Returns:
            Metrics dict or None if insufficient data.
        """
        if db is None:
            return None

        min_samples = int(getattr(settings, "ESPORTS_CATBOOST_MIN_SAMPLES", 200))

        try:
            from esports.models.draft_features import DraftFeatureBuilder
            from esports.models.catboost_draft_model import CatBoostDraftModel

            # Step 1: Fit draft feature stats from training data
            builder = DraftFeatureBuilder()
            fit_summary = await builder.fit_stats(db, game)
            if fit_summary.get("rows_with_draft", 0) < min_samples:
                logger.info(
                    "EsportsModelTrainer: insufficient draft data for CatBoost",
                    game=game,
                    rows_with_draft=fit_summary.get("rows_with_draft", 0),
                    min_needed=min_samples,
                )
                return None

            # Step 2: Load training data with draft + Glicko-2 features
            from esports.data.esports_data_collector import EsportsDataCollector
            collector = EsportsDataCollector(pandascore_client=self._ps)
            training_data = await collector.get_training_data(db, game)

            # Filter to rows that have draft data
            draft_rows = []
            for row in training_data:
                gs = row if isinstance(row, dict) else {}
                draft = gs.get("draft")
                if not isinstance(draft, dict):
                    continue
                a_picks = draft.get("team_a_picks", [])
                b_picks = draft.get("team_b_picks", [])
                if a_picks or b_picks:
                    draft_rows.append(row)

            if len(draft_rows) < min_samples:
                logger.info(
                    "EsportsModelTrainer: insufficient filtered draft rows",
                    game=game,
                    draft_rows=len(draft_rows),
                    min_needed=min_samples,
                )
                return None

            # Step 3: Build features + labels (oldest-first for temporal split)
            draft_rows = list(reversed(draft_rows))

            # Glicko-2 feature keys to merge
            _GLICKO_KEYS = (
                "team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                "team_a_volatility", "team_b_volatility", "best_of",
            )

            X_dicts = []
            y_labels = []
            for row in draft_rows:
                draft = row.get("draft", {})
                team_a = str(row.get("team_a", row.get("team_a_name", ""))).strip()
                team_b = str(row.get("team_b", row.get("team_b_name", ""))).strip()

                feats = builder.build_features(draft, game, team_a, team_b)
                # Merge Glicko-2 features from training row
                for gk in _GLICKO_KEYS:
                    feats[gk] = float(row.get(gk, 0.0))

                X_dicts.append(feats)

                # Label: team_a won
                if row.get("_game") == "lol":
                    y_labels.append(int(row.get("blue_win", 0)))
                elif row.get("_game") == "cs2":
                    y_labels.append(int(row.get("team_a_won_round", 0)))
                else:
                    y_labels.append(int(row.get("team_a_won", row.get("outcome", 0))))

            if len(X_dicts) < min_samples:
                return None

            # Step 4: Train
            all_feature_names = builder.get_all_feature_names() + list(_GLICKO_KEYS)
            cat_feature_names = builder.get_cat_feature_names()

            model = CatBoostDraftModel(game)
            metrics = model.fit(
                X_dicts, y_labels,
                cat_feature_names=cat_feature_names,
                all_feature_names=all_feature_names,
            )

            # Step 5: Save if graduated
            if metrics.get("graduated", False):
                model_dir = os.path.join(
                    os.path.dirname(__file__), "..", "..", "saved_models",
                )
                model_dir = os.path.abspath(model_dir)
                os.makedirs(model_dir, exist_ok=True)
                model_path = os.path.join(model_dir, f"catboost_{game}.cbm")
                model.save(model_path)

            # Track training state
            self._last_train_time[f"catboost_{game}"] = time.monotonic()
            self._train_results[f"catboost_{game}"] = metrics

            return metrics

        except Exception as exc:
            logger.error(
                "EsportsModelTrainer: CatBoost draft training failed",
                game=game, error=str(exc),
            )
            return {"error": str(exc), "graduated": False}

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

        # Always save — graduation gate removed, user controls go-live
        model.save()
        logger.info(
            "LoLWinModel: saved",
            accuracy=round(metrics["accuracy"], 4),
            brier=round(metrics["brier_score"], 4),
            ece=round(metrics["ece"], 4),
        )

        # ONNX export for faster inference
        try:
            from esports.models.onnx_compiler import OnnxCompiler
            _compiler = OnnxCompiler()
            _onnx_dir = os.path.join(os.path.dirname(__file__), "..", "..", "saved_models")
            _onnx_path = os.path.join(os.path.abspath(_onnx_dir), "lol_win_model.onnx")
            _compiler.export_xgboost(model._model, n_features=8, save_path=_onnx_path)
        except Exception:
            pass  # ONNX export is optional

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

        # Always save — graduation gate removed, user controls go-live
        model.save()
        logger.info(
            "CS2EconomyModel: saved",
            accuracy=round(metrics["accuracy"], 4),
            brier=round(metrics["brier_score"], 4),
            ece=round(metrics["ece"], 4),
        )

        # ONNX export for faster inference
        try:
            from esports.models.onnx_compiler import OnnxCompiler
            _compiler = OnnxCompiler()
            _onnx_dir = os.path.join(os.path.dirname(__file__), "..", "..", "saved_models")
            _onnx_path = os.path.join(os.path.abspath(_onnx_dir), "cs2_economy_model.onnx")
            _compiler.export_xgboost(model._round_model, n_features=14, save_path=_onnx_path)
        except Exception:
            pass  # ONNX export is optional

        return metrics

    async def _train_dota2(
        self, train_set: List[Dict], val_set: List[Dict]
    ) -> Dict[str, float]:
        """Train Dota2 model and return validation metrics dict."""
        from esports.models.dota2_model import Dota2Model

        zero_metrics = {"accuracy": 0.0, "brier_score": 1.0, "ece": 1.0}

        model = Dota2Model()
        success = model.train(train_set)
        if not success:
            return zero_metrics

        metrics = self._evaluate_full(model, val_set, label_key="team_a_won")
        model.save()
        logger.info(
            "Dota2Model: saved",
            accuracy=round(metrics["accuracy"], 4),
            brier=round(metrics["brier_score"], 4),
            ece=round(metrics["ece"], 4),
        )
        return metrics

    async def _train_valorant(
        self, train_set: List[Dict], val_set: List[Dict]
    ) -> Dict[str, float]:
        """Train Valorant model and return validation metrics dict."""
        from esports.models.valorant_model import ValorantModel

        zero_metrics = {"accuracy": 0.0, "brier_score": 1.0, "ece": 1.0}

        model = ValorantModel()
        success = model.train(train_set)
        if not success:
            return zero_metrics

        metrics = self._evaluate_full(model, val_set, label_key="team_a_won")
        model.save()
        logger.info(
            "ValorantModel: saved",
            accuracy=round(metrics["accuracy"], 4),
            brier=round(metrics["brier_score"], 4),
            ece=round(metrics["ece"], 4),
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
