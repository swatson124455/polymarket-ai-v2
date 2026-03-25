"""
LearningScheduler - Runs periodic retraining (learn_from_trades, retrain).
Uses RETRAIN_INTERVAL_HOURS and datetime.now(timezone.utc).
Uses advisory locks to prevent concurrent elite_update/model_training from multiple processes.
Autonomous learning: performance-based retrain trigger (A) and feeding resolved predictions to IncrementalLearner (C).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from structlog import get_logger
from base_engine.data.database import Database
from base_engine.data.database_lock import acquire_lock, LockAcquisitionError
from base_engine.learning.learning_engine import LearningEngine
from base_engine.prediction.prediction_engine import PredictionEngine
from config.settings import settings

logger = get_logger()


class LearningScheduler:
    """Schedules periodic retraining of models and patterns."""

    def __init__(
        self,
        db: Database,
        learning_engine: LearningEngine,
        prediction_engine: PredictionEngine,
        interval_hours: Optional[int] = None,
        elite_detector=None,
        initial_delay_seconds: int = 60,
        calibration_tracker=None,
        alerting=None,
        incremental_learner: Optional[Any] = None,
        meta_learner: Optional[Any] = None,
        causal_engine: Optional[Any] = None,
        feature_engineer: Optional[Any] = None,
        model_version_manager: Optional[Any] = None,
    ):
        self.db = db
        self.learning_engine = learning_engine
        self.prediction_engine = prediction_engine
        self.calibration_tracker = calibration_tracker
        self.interval_hours = interval_hours or getattr(settings, "RETRAIN_INTERVAL_HOURS", 6)
        self.elite_detector = elite_detector
        self.initial_delay_seconds = max(0, int(initial_delay_seconds))
        self.alerting = alerting
        self.incremental_learner = incremental_learner
        self.meta_learner = meta_learner
        self.causal_engine = causal_engine
        self.feature_engineer = feature_engineer
        self.model_version_manager = model_version_manager
        self.ensemble_bot = None  # L6: archived — EnsembleBot removed from registry
        self.esports_trainer = None  # P2.2: set by EsportsBot.start() after init
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._cycles_without_retrain = 0
        self._last_degradation_retrain_at: Optional[datetime] = None
        self._last_fed_resolved_at: Optional[datetime] = None
        self._meta_learner_cycle_count = 0
        # M5 FIX: Track consecutive retrain failures to alert after persistent breakage
        self._consecutive_retrain_failures = 0
        # M7 FIX: Guard against concurrent retrain (degradation check + scheduled can overlap)
        self._retrain_in_progress: bool = False
        # S100: Restore canary stage from DB on first transition check
        self._canary_restored: bool = False

    async def start(self) -> None:
        """Start the scheduled retraining loop."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        self._task.add_done_callback(self._on_loop_done)
        logger.info("LearningScheduler started", interval_hours=self.interval_hours)

    async def stop(self) -> None:
        """Stop the scheduler."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("LearningScheduler stopped")

    def _on_loop_done(self, task: asyncio.Task) -> None:
        """Callback for loop task completion — auto-restart on crash."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.critical("LearningScheduler._loop() crashed: %s", exc, exc_info=exc)
            if self.running:
                logger.warning("LearningScheduler: auto-restarting loop after crash")
                self._task = asyncio.create_task(self._loop())
                self._task.add_done_callback(self._on_loop_done)

    async def _loop(self) -> None:
        if self.initial_delay_seconds > 0:
            logger.info("LearningScheduler: waiting %s seconds before first cycle", self.initial_delay_seconds)
            await asyncio.sleep(self.initial_delay_seconds)
        while self.running:
            try:
                await self._retrain_cycle()
                self._consecutive_retrain_failures = 0  # M5 FIX: reset on success
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._consecutive_retrain_failures += 1  # M5 FIX: count failures
                try:
                    from base_engine.monitoring.error_tracking import get_error_tracker
                    get_error_tracker().capture_exception(e, context={"phase": "retrain_cycle"})
                except Exception:
                    pass
                logger.error("Scheduler cycle failed: %s", e, exc_info=True)
                # M5 FIX: Alert and reduce interval after 3 consecutive failures
                if self._consecutive_retrain_failures >= 3 and self.alerting:
                    try:
                        from base_engine.monitoring.alerting import AlertSeverity
                        await self.alerting.send_alert(
                            title="LearningScheduler: persistent retrain failures",
                            message=f"{self._consecutive_retrain_failures} consecutive retrain failures. Last: {str(e)[:300]}",
                            severity=AlertSeverity.WARNING,
                            source="learning_scheduler",
                            metadata={"failures": self._consecutive_retrain_failures},
                        )
                    except Exception:
                        pass
                # M5 FIX: Use short retry interval on repeated failure (30 min) vs. normal (6h)
                _retry_secs = 1800 if self._consecutive_retrain_failures >= 3 else self.interval_hours * 3600
                await asyncio.sleep(_retry_secs)
                continue
            await asyncio.sleep(self.interval_hours * 3600)

    def _degradation_retrain_cooldown_elapsed(self) -> bool:
        """True if we are past cooldown since last degradation-triggered retrain."""
        if self._last_degradation_retrain_at is None:
            return True
        cooldown_hours = getattr(settings, "AUTO_RETRAIN_COOLDOWN_HOURS", 1.0)
        return (datetime.now(timezone.utc) - self._last_degradation_retrain_at).total_seconds() >= cooldown_hours * 3600

    async def _check_degradation_and_force_retrain(self) -> bool:
        """If AUTO_RETRAIN_ON_DEGRADATION and recent Brier/accuracy degraded, return True to force retrain."""
        if not getattr(settings, "AUTO_RETRAIN_ON_DEGRADATION", False):
            return False
        if not self._degradation_retrain_cooldown_elapsed():
            return False
        n = getattr(settings, "AUTO_RETRAIN_RECENT_N", 50)
        min_samples = getattr(settings, "AUTO_RETRAIN_MIN_SAMPLES", 20)
        try:
            perf = await self.db.get_recent_brier_from_prediction_log(n)
            if not perf or perf.get("count", 0) < min_samples:
                return False
            brier_max = getattr(settings, "AUTO_RETRAIN_BRIER_MAX", 0.30)
            acc_min = getattr(settings, "AUTO_RETRAIN_ACC_MIN", 0.45)
            brier = perf.get("brier", 0.25)
            acc = perf.get("accuracy", 0.0)
            # L1 FIX: Also check DriftTracker for concept drift
            drift_detected = False
            if self.prediction_engine and hasattr(self.prediction_engine, '_drift_tracker'):
                try:
                    # I05 bugfix: was check_model_drift() which doesn't exist → always silently failed
                    drift_result = self.prediction_engine._drift_tracker.check_drift()
                    drift_detected = drift_result.get("drifted", False)
                    if drift_detected:
                        logger.warning("DriftTracker detected concept drift: %s", drift_result.get("checks", {}))
                except Exception:
                    pass

            # L1 / I05: Also check CalibrationTracker's DDM/EDDM drift detectors.
            # These detect abrupt (DDM) and gradual (EDDM) accuracy degradation
            # from resolved prediction outcomes — complementary to ADWIN in DriftTracker.
            # I05 FIX: removed "if not drift_detected and" guard so CalibrationTracker is
            # ALWAYS evaluated independently. The old guard short-circuited CalibrationTracker
            # whenever DriftTracker already fired, meaning a DriftTracker false-negative would
            # suppress CalibrationTracker even when DDM/EDDM had detected gradual decay.
            if self.calibration_tracker:
                try:
                    ct_drift = self.calibration_tracker.get_drift_status()
                    if ct_drift.get("ddm_drift") or ct_drift.get("eddm_drift"):
                        drift_detected = True
                        logger.warning(
                            "CalibrationTracker DDM/EDDM drift detected — forcing retrain",
                            ddm_drift=ct_drift.get("ddm_drift"),
                            eddm_drift=ct_drift.get("eddm_drift"),
                            error_rate=round(ct_drift.get("error_rate", 0), 4),
                            n_observations=ct_drift.get("n_observations", 0),
                        )
                except Exception:
                    pass

            if brier > brier_max or acc < acc_min or drift_detected:
                logger.warning(
                    "Autonomous retrain: degradation detected (brier=%.3f > %.3f or acc=%.2f < %.2f, drift=%s), forcing retrain",
                    brier, brier_max, acc, acc_min, drift_detected,
                )
                if self.alerting:
                    try:
                        from base_engine.monitoring.alerting import AlertSeverity
                        await self.alerting.send_alert(
                            title="Model degradation: auto-retrain triggered",
                            message=f"Brier={brier:.3f} acc={acc:.2f} (n={perf.get('count')})",
                            severity=AlertSeverity.WARNING,
                            source="learning_scheduler",
                            metadata=perf,
                        )
                    except Exception:
                        pass
                self._last_degradation_retrain_at = datetime.now(timezone.utc)
                return True
        except Exception as e:
            logger.debug("Degradation check failed: %s", e)
        return False

    async def _run_meta_learner_tuning(self) -> None:
        """Run MetaLearner tuning every Nth cycle (e.g. every 4th retrain cycle).
        Uses recent prediction performance to optimize hyperparameters and ensemble weights."""
        if not self.meta_learner:
            return
        meta_interval = getattr(settings, "META_LEARNER_CYCLE_INTERVAL", 4)
        self._meta_learner_cycle_count += 1
        if self._meta_learner_cycle_count % meta_interval != 0:
            return
        try:
            # Gather model performance from prediction engine
            models = getattr(self.prediction_engine, "models", {})
            if not models:
                return
            # Build performance history from prediction log
            perf = await self.db.get_recent_brier_from_prediction_log(100)
            if not perf or perf.get("count", 0) < 20:
                return
            # L2 FIX: Use per-model validation accuracy instead of aggregate.
            # Old code gave every model the same accuracy → MetaLearner produced equal weights.
            per_model_acc = getattr(self.prediction_engine, "_per_model_val_accuracy", {})
            model_performances = {}
            for name in models:
                model_performances[name] = {
                    "accuracy": per_model_acc.get(name, perf.get("accuracy", 0.5)),
                    "sharpe_ratio": 0.0,  # placeholder; could derive from backtest
                }
            if getattr(settings, "SELF_TUNE_MODEL_WEIGHTS", True):
                result = await self.meta_learner.learn_optimal_ensemble(model_performances)
                new_weights = result.get("ensemble_weights", {})
                if new_weights:
                    logger.info("MetaLearner: new ensemble weights: %s", new_weights)
                    if hasattr(self.prediction_engine, "model_weights"):
                        self.prediction_engine.model_weights = new_weights
            if getattr(settings, "SELF_TUNE_ENSEMBLE_BLEND", True):
                current_blend = getattr(self.prediction_engine, "ensemble_blend", 0.6)
                learned_blend = await self.meta_learner.learn_ensemble_blend(self.db, current_blend, n=50)
                if hasattr(self.prediction_engine, "ensemble_blend"):
                    self.prediction_engine.ensemble_blend = learned_blend
                    logger.info("MetaLearner: ensemble_blend set to %.2f", learned_blend)
            min_resolved_for_features = getattr(settings, "MIN_RESOLVED_FOR_FEATURE_SELECTION", 50)
            if getattr(settings, "SELF_TUNE_FEATURES", True) and perf.get("count", 0) >= min_resolved_for_features:
                try:
                    # 1. Get MDI importance from tree models
                    feature_scores = getattr(self.prediction_engine, "get_feature_scores", lambda: {})()

                    # 2. Blend with causal importance if available
                    if feature_scores and self.causal_engine and getattr(settings, "USE_CAUSAL_IMPORTANCE", True):
                        try:
                            causal_scores = await self._compute_causal_importance(feature_scores)
                            if causal_scores:
                                causal_w = getattr(settings, "CAUSAL_IMPORTANCE_WEIGHT", 0.3)
                                mdi_w = 1.0 - causal_w
                                blended = {}
                                all_feats = set(feature_scores.keys()) | set(causal_scores.keys())
                                for f in all_feats:
                                    blended[f] = mdi_w * feature_scores.get(f, 0.0) + causal_w * causal_scores.get(f, 0.0)
                                feature_scores = blended
                                logger.info("Blended MDI+causal importance for %d features", len(blended))
                        except Exception as ce:
                            logger.debug("Causal importance blending failed (using MDI only): %s", ce)

                    # 3. Update FeatureEngineer with learned importance
                    if feature_scores and self.feature_engineer:
                        try:
                            self.feature_engineer.update_feature_importance(feature_scores)
                        except Exception:
                            pass

                    # 4. Feed to MetaLearner for best feature selection
                    if feature_scores:
                        best = await self.meta_learner.learn_best_features(feature_scores)
                        if best and hasattr(self.prediction_engine, "best_feature_names"):
                            self.prediction_engine.best_feature_names = best
                            logger.info("MetaLearner: best_feature_names set (%d features)", len(best))

                    # 5. Store blended importance for bot access
                    if feature_scores and hasattr(self.prediction_engine, "_feature_importance_scores"):
                        self.prediction_engine._feature_importance_scores = feature_scores
                except Exception as fe_err:
                    logger.debug("Feature selection failed: %s", fe_err)
            try:
                await self.prediction_engine.save_models_to_db()
            except Exception as save_err:
                logger.debug("Save learned params after MetaLearner failed: %s", save_err)
            # Hyperparameter tuning (record current config as performance history entry)
            for name in models:
                hp = {}
                model_obj = models[name]
                if hasattr(model_obj, "get_params"):
                    hp = model_obj.get_params()
                await self.meta_learner.learn_optimal_hyperparameters(
                    model_type=name,
                    performance_history=[{
                        "hyperparameters": hp,
                        "performance_score": perf.get("accuracy", 0.5),
                    }],
                )
        except Exception as e:
            logger.debug("MetaLearner tuning failed: %s", e)

    async def _compute_causal_importance(self, mdi_scores: dict) -> dict:
        """Run CausalInferenceEngine to compute causal feature importance.
        Uses heuristic graph (correlation-based) as a complement to MDI."""
        if not self.causal_engine:
            return {}
        feature_names = list(mdi_scores.keys())
        if len(feature_names) < 3:
            return {}
        try:
            # Learn a global causal graph with outcome as target
            graph = await self.causal_engine.learn_causal_graph(
                market_id="__global__",
                features=feature_names,
                outcomes=["outcome"],
                data=None,  # Uses heuristic fallback (correlation-based)
            )
            # Get causal importance relative to outcome
            importance = self.causal_engine.get_causal_importance(
                market_id="__global__",
                outcome="outcome",
            )
            return importance
        except Exception as e:
            logger.debug("Causal importance computation failed: %s", e)
            return {}

    async def _feed_resolved_predictions_to_incremental_learner(self, since: datetime) -> None:
        """Feed prediction_log rows resolved after last_fed into IncrementalLearner (C). Only new rows."""
        if not self.incremental_learner or not self.db.session_factory:
            return
        try:
            feed_since = self._last_fed_resolved_at if self._last_fed_resolved_at is not None else since
            rows = await self.db.get_recent_resolved_predictions(feed_since)
            for r in rows:
                await self.incremental_learner.process_resolved_prediction(
                    market_id=r["market_id"],
                    predicted_prob=r["predicted_prob"],
                    resolution=r["resolution"],
                    resolved_at=r.get("resolved_at"),
                )
            if rows:
                max_resolved = max((r.get("resolved_at") for r in rows if r.get("resolved_at") is not None), default=None)
                if max_resolved is not None:
                    self._last_fed_resolved_at = max_resolved
            if rows:
                logger.info("Fed %d resolved predictions to IncrementalLearner", len(rows))
        except Exception as e:
            logger.warning("Feed resolved predictions to IncrementalLearner failed: %s", e)

    async def _retrain_cycle(self) -> None:
        """Run one retraining cycle. Skips learn when no new trades; still runs retrain every Nth cycle."""
        # M7 FIX: Guard against concurrent retrains. The degradation check can trigger a retrain
        # while the scheduled cycle is still running, causing two concurrent _train_models() calls
        # that modify self.models/self.scaler simultaneously — a race condition on model state.
        if self._retrain_in_progress:
            logger.debug("LearningScheduler: retrain already in progress, skipping this cycle")
            return
        # Also guard against the startup _background_train() still running.
        _pe_task = getattr(self.prediction_engine, "_training_task", None)
        if _pe_task is not None and not _pe_task.done():
            logger.debug("LearningScheduler: startup training in progress, deferring retrain cycle")
            return
        self._retrain_in_progress = True
        try:
            await self._retrain_cycle_inner()
        finally:
            self._retrain_in_progress = False

    async def _retrain_cycle_inner(self) -> None:
        """Inner retrain logic — called only when _retrain_in_progress guard is held."""
        # PipelineGate: replace staleness check with full gate (freshness + training-sample sufficiency)
        try:
            from base_engine.monitoring.pipeline_gate import PipelineGate
            from base_engine.monitoring.alerting import AlertSeverity

            gate = PipelineGate(self.db, alerting=self.alerting)
            gate_result = await gate.check_before_training()
            if not gate_result.passed:
                logger.warning(
                    "Training gate failed: %s",
                    gate_result.summary,
                    failures=gate_result.failures,
                )
                if self.alerting and gate_result.stale:
                    await self.alerting.send_alert(
                        title="Data stale, training deferred",
                        message=gate_result.summary,
                        severity=AlertSeverity.WARNING,
                        source="pipeline_gate",
                        metadata={"failures": gate_result.failures},
                    )
                return
        except Exception as e:
            logger.error("PipelineGate check FAILED -- training SKIPPED: %s", e)
            return

        force_retrain = await self._check_degradation_and_force_retrain()
        since = datetime.now(timezone.utc) - timedelta(hours=self.interval_hours)
        await self._feed_resolved_predictions_to_incremental_learner(since)
        if self.incremental_learner:
            try:
                await self.incremental_learner.force_update()
            except Exception as e:
                logger.debug("IncrementalLearner force_update failed: %s", e)

        if self.elite_detector is not None:
            try:
                async with acquire_lock(self.db, "elite_update", timeout_seconds=30):
                    await self.elite_detector.update_elite_status()
            except LockAcquisitionError:
                logger.debug("Elite update lock busy, skipping")
            except Exception as e:
                logger.warning("Elite status update failed (non-fatal): %s", e)
        logger.info("Starting scheduled retraining...")
        since = datetime.now(timezone.utc) - timedelta(hours=self.interval_hours)
        recent_trades: list = []
        feedback_count = 0
        try:
            recent_trades = await self.db.get_trades_since(since)
            # Check for new feedback from paper trades and prediction log
            if not recent_trades and getattr(settings, "RETRAIN_ON_NEW_FEEDBACK", True):
                try:
                    from sqlalchemy import text as _text
                    async with self.db.get_session() as _sess:
                        _r = await _sess.execute(_text(
                            "SELECT COUNT(*) FROM paper_trades "
                            "WHERE resolution IS NOT NULL AND resolved_at > :since"
                        ), {"since": since})
                        paper_count = _r.scalar() or 0
                        _r = await _sess.execute(_text(
                            "SELECT COUNT(*) FROM prediction_log "
                            "WHERE was_correct IS NOT NULL AND resolved_at > :since"
                        ), {"since": since})
                        pred_count = _r.scalar() or 0
                        feedback_count = paper_count + pred_count
                        if feedback_count > 0:
                            logger.info("New feedback: %d paper trades + %d predictions resolved since last cycle",
                                        paper_count, pred_count)
                except Exception as _e:
                    logger.debug("Feedback count check failed: %s", _e)

            if not recent_trades and feedback_count == 0 and not force_retrain:
                if self._cycles_without_retrain < 3:
                    self._cycles_without_retrain += 1
                    # L6 FIX: Only INFO on first quiet cycle; subsequent cycles at DEBUG to reduce spam.
                    _quiet_log = logger.info if self._cycles_without_retrain == 1 else logger.debug
                    _quiet_log("No new trades or feedback, skipping learn and retrain (cycle %s/3)", self._cycles_without_retrain)
                    return
                # Every 4th cycle (after 3 skips), run retrain only if enough resolved predictions.
                self._cycles_without_retrain = 0
                logger.info("No new trades; running retrain only (periodic)")
                try:
                    _resolved_perf = await self.db.get_recent_brier_from_prediction_log(500)
                    _resolved_count = (_resolved_perf or {}).get("count", 0)
                    _min_resolved = getattr(settings, "MIN_RESOLVED_FOR_RETRAIN", 20)
                    if _resolved_count < _min_resolved:
                        logger.info(
                            "Periodic retrain skipped: %d resolved predictions available (need %d)",
                            _resolved_count, _min_resolved,
                        )
                        return
                except Exception as _rg_err:
                    logger.debug("Resolved count gate failed (proceeding with retrain): %s", _rg_err)
            elif feedback_count > 0 and not recent_trades:
                self._cycles_without_retrain = 0
                logger.info("Triggering retrain on %d new feedback rows (paper trades + predictions)", feedback_count)
            else:
                self._cycles_without_retrain = 0
                try:
                    await self.learning_engine.learn_from_trades(recent_trades)
                except Exception as e:
                    logger.warning("learn_from_trades failed: %s", e)
        except Exception as e:
            logger.warning("get_trades_since failed: %s", e)
        # L9 FIX: Price-history learning treats price increases as "wins" (noise, not outcomes).
        # Only use as last resort when trades are truly sparse. Raised threshold from 10 to 100.
        min_trades = getattr(settings, "LEARN_FROM_PRICES_MIN_TRADES", 100)
        if getattr(settings, "LEARN_FROM_PRICES_WHEN_TRADES_SPARSE", False) and len(recent_trades or []) < min_trades:
            try:
                await self.learning_engine.learn_from_price_history(since, limit=10000)
            except Exception as e:
                logger.warning("learn_from_price_history failed: %s", e)
        try:
            async with acquire_lock(self.db, "model_training", timeout_seconds=60):
                await self.prediction_engine.retrain()
                # Clear prediction caches after retraining to avoid stale predictions
                for cache_attr in ("_prediction_cache", "_elite_cache", "_regime_cache", "_path_cache", "_fe_cache", "_signal_cache"):
                    cache = getattr(self.prediction_engine, cache_attr, None)
                    if cache and hasattr(cache, "clear"):
                        cache.clear()
                # P1-2: Also clear feature vector + L8 caches — these use old scaler/feature names.
                # After retrain, keeping them would mix new-model weights with old feature values.
                for cache_attr in ("_feature_vector_cache", "_market_cache", "_l8_cache"):
                    cache = getattr(self.prediction_engine, cache_attr, None)
                    if cache and hasattr(cache, "clear"):
                        cache.clear()
                logger.info("Prediction caches cleared after retrain")
                # L1: Reset CalibrationTracker's DDM/EDDM drift detector after successful retrain.
                # The new model should have adapted to the distribution shift; continuing with
                # stale drift state would immediately re-trigger retrain on the next cycle.
                if self.calibration_tracker and hasattr(self.calibration_tracker, 'drift_detector'):
                    try:
                        _should_reset = True
                        if hasattr(self.calibration_tracker, 'get_recent_accuracy'):
                            _recent_acc = self.calibration_tracker.get_recent_accuracy()
                            if _recent_acc is not None and _recent_acc < 0.45:
                                logger.warning(
                                    "Post-retrain accuracy %.2f < 0.45 -- keeping drift detector active",
                                    _recent_acc,
                                )
                                _should_reset = False
                        if _should_reset:
                            self.calibration_tracker.drift_detector.reset()
                            logger.info("CalibrationTracker DDM/EDDM drift detector reset after retrain")
                    except Exception as _dr_err:
                        logger.debug("DDM/EDDM drift detector reset failed (non-fatal): %s", _dr_err)
        except LockAcquisitionError:
            logger.debug("Model training lock busy, skipping retrain")
        except Exception as e:
            logger.warning("retrain failed (skipping cycle): %s", e)
        # Log model version after retrain (observational only)
        if self.model_version_manager:
            try:
                version = self.model_version_manager.create_version("ensemble")
                self.model_version_manager.set_active_version(version.version_id)
                logger.info("Model version registered: %s", version.version_id)
            except Exception as e:
                logger.debug("ModelVersionManager post-retrain failed: %s", e)
        if getattr(settings, "CALIBRATION_TRACKING_ENABLED", True):
            try:
                tracker = self.calibration_tracker
                if tracker is None and hasattr(self, "db"):
                    from base_engine.learning.calibration_tracker import CalibrationTracker
                    tracker = CalibrationTracker(db=self.db)
                if tracker:
                    n = await tracker.process_resolved_from_db()
                    if n > 0:
                        logger.info("Calibration: processed %d resolutions", n)
            except Exception as e:
                logger.debug("Calibration process_resolved failed: %s", e)

        # L6: Auto-invoke EnsembleBot.optimize_weights() after retrain.
        # Uses per-model validation accuracy from the retrain cycle to compute
        # Sharpe-ratio-optimized weights (different from MetaLearner's approach).
        if self.ensemble_bot is not None:
            try:
                per_model_acc = getattr(self.prediction_engine, "_per_model_val_accuracy", {})
                if per_model_acc and len(per_model_acc) >= 3:
                    backtest_results = [
                        {"model_name": name, "accuracy": acc}
                        for name, acc in per_model_acc.items()
                    ]
                    await self.ensemble_bot.optimize_weights(backtest_results)
                    logger.info("L6: optimize_weights() called with %d model accuracies", len(per_model_acc))
            except Exception as e:
                logger.debug("L6: optimize_weights post-retrain failed (non-fatal): %s", e)

        # MetaLearner tuning: optimizes hyperparameters and ensemble weights periodically
        await self._run_meta_learner_tuning()

        # Canary auto-transition (Item 22): advance/reset stage based on recent metrics
        if getattr(settings, "CANARY_AUTO_ADVANCE", True):
            try:
                await self._canary_auto_transition()
            except Exception as e:
                logger.debug("Canary auto-transition failed: %s", e)

        logger.info("Scheduled retraining complete")

        # Daily PnL summary alert — fires once per UTC calendar day
        if self.alerting and self.db:
            try:
                from datetime import datetime as _dt, timezone as _tz
                _today = _dt.now(_tz.utc).date()
                if _today != getattr(self, "_last_daily_pnl_date", None):
                    await self.alerting.check_daily_pnl_summary(self.db)
                    self._last_daily_pnl_date = _today
            except Exception as _pnl_exc:
                logger.debug("daily_pnl_alert_failed", error=str(_pnl_exc))

        # P2.2: Trigger esports cross-game retrain if trainer available + due
        if self.esports_trainer is not None:
            try:
                if self.esports_trainer.needs_retrain("cross_game"):
                    _cg_task = asyncio.create_task(
                        self.esports_trainer.train_cross_game(db=self.db),
                        name="scheduler_retrain_cross_game",
                    )

                    def _on_cross_game_retrain_done(t: asyncio.Task) -> None:
                        if not t.cancelled() and t.exception() is not None:
                            logger.error(
                                "LearningScheduler: cross_game retrain task failed",
                                error=str(t.exception()),
                            )

                    _cg_task.add_done_callback(_on_cross_game_retrain_done)
                    logger.info("LearningScheduler: triggered esports cross-game retrain")
            except Exception as _exc:
                logger.warning("LearningScheduler: esports retrain hook failed", error=str(_exc))

    async def _canary_auto_transition(self) -> None:
        """Auto-advance or reset canary stage based on Brier + accuracy metrics.
        get_recent_brier_from_prediction_log returns {count, brier, accuracy}.
        Accuracy is used as directional quality signal (sharpe not available from prediction_log)."""
        # S100: Restore persisted canary stage from DB on first call
        if not self._canary_restored:
            self._canary_restored = True
            try:
                from sqlalchemy import text as _txt
                async with self.db.get_session() as _sess:
                    _row = (await _sess.execute(
                        _txt("SELECT value FROM system_kv WHERE key = 'canary_stage'")
                    )).scalar_one_or_none()
                    if _row is not None:
                        settings.CANARY_STAGE = int(_row)
                        logger.info("canary_stage_restored_from_db", stage=int(_row))
            except Exception:
                pass  # Fall back to env var
        current_stage = getattr(settings, "CANARY_STAGE", 0)
        if current_stage >= 4:
            return  # Already at full capital
        max_brier = getattr(settings, "CANARY_MAX_BRIER", 0.25)
        min_trades = getattr(settings, "CANARY_MIN_TRADES_PER_STAGE", 50)
        try:
            perf = await self.db.get_recent_brier_from_prediction_log(
                getattr(settings, "AUTO_RETRAIN_RECENT_N", 50)
            )
        except Exception:
            return
        if not perf or perf.get("count", 0) < min_trades:
            return  # Not enough data to make a decision
        brier = perf.get("brier", 1.0)          # DB returns "brier" not "brier_score"
        accuracy = perf.get("accuracy", 0.0)    # DB returns "accuracy" (sharpe not available)
        old_stage = current_stage
        # Advance: Brier below threshold AND accuracy above random (>55%)
        if brier <= max_brier and accuracy >= 0.55:
            new_stage = min(current_stage + 1, 4)
        # Regress: Brier terrible (>1.5x limit) OR accuracy worse than random (<45%)
        elif brier > max_brier * 1.5 or accuracy < 0.45:
            new_stage = max(current_stage - 1, 0)
        else:
            return  # Acceptable range — no change
        if new_stage != old_stage:
            settings.CANARY_STAGE = new_stage
            _pcts = {0: "off (paper)", 1: "5%", 2: "25%", 3: "50%", 4: "100%"}
            logger.info(
                "Canary stage transition",
                old=old_stage, new=new_stage,
                capital_pct=_pcts.get(new_stage, "unknown"),
                brier=round(brier, 4), accuracy=round(accuracy, 4),
            )
            # S100: Persist canary stage to DB
            try:
                from sqlalchemy import text as _txt2
                async with self.db.get_session() as _sess2:
                    await _sess2.execute(_txt2(
                        "INSERT INTO system_kv (key, value, updated_at) "
                        "VALUES ('canary_stage', :val, NOW()) "
                        "ON CONFLICT (key) DO UPDATE SET value = :val, updated_at = NOW()"
                    ), {"val": str(new_stage)})
                    await _sess2.commit()
            except Exception as _pe:
                logger.debug("canary_stage_persist_failed", error=str(_pe))
            if self.alerting:
                try:
                    from base_engine.monitoring.alerting import AlertSeverity
                    await self.alerting.send_alert(
                        title=f"CANARY_STAGE: {old_stage} → {new_stage} ({_pcts.get(new_stage, '?')} capital)",
                        message=(
                            f"Capital deployment stage changed.\n"
                            f"Brier={brier:.4f}, accuracy={accuracy:.2%}.\n"
                            f"To rollback: export CANARY_STAGE={old_stage} && sudo systemctl restart polymarket-ai"
                        ),
                        severity=AlertSeverity.WARNING,
                        source="canary_controller",
                    )
                except Exception as _ae:
                    logger.debug("canary_alert_failed", error=str(_ae))
