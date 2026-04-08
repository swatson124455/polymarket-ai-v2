"""
EsportsBot — Pre-game + live in-play esports trading bot.

All 8 game titles: LoL, CS2, Dota 2, Valorant, CoD, R6, StarCraft II, Rocket League.
Pre-game: prediction engine + model-vs-market edge validation.
Live: win probability model updates on game events via PandaScore.

Requires PANDASCORE_API_KEY — fails fast if missing.
Scan interval: 120s default, 10s during live matches.
"""
from __future__ import annotations

import asyncio
import collections
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from structlog import get_logger

from bots.base_bot import BaseBot
from base_engine.data.daily_counter import increment_counter as _inc_daily, restore_counters as _restore_daily
from base_engine.features.aggregation import extremized_geometric_mean
from base_engine.monitoring.alerting import AlertSeverity
from config.settings import settings

logger = get_logger()


# ---------------------------------------------------------------------------
# S100: Beta calibration (Kull et al., AISTATS 2017) with Bayesian identity
# priors.  Replaces the old 3-stage sequential pipeline (bias_decomp →
# focal_temp → horizon_bias) which compounded estimation variance and was
# trained on stale pre-S97 data.
#
# Calibration function:
#     calibrated = sigmoid(a·ln(p) − b·ln(1−p) + c)
#
# Identity at a=1, b=1, c=0.  Regularized toward identity via:
#     loss = NLL(a, b, c) + λ(a−1)² + λ(b−1)² + λc²
#
# With high λ and few samples the calibrator stays at identity — raw model
# probs pass through untouched.  As resolved predictions accumulate the
# corrections grow gradually under Bayesian shrinkage.
# ---------------------------------------------------------------------------
class BetaCalibrator:
    """Per-game beta calibrator with Bayesian identity priors."""

    __slots__ = ("a", "b", "c", "lambda_reg", "min_samples", "_fitted",
                 "_n_samples")

    def __init__(self, lambda_reg: float = 10.0, min_samples: int = 15):
        self.a: float = 1.0
        self.b: float = 1.0
        self.c: float = 0.0
        self.lambda_reg = lambda_reg
        self.min_samples = min_samples
        self._fitted: bool = False
        self._n_samples: int = 0

    # -- inference ----------------------------------------------------------

    def calibrate(self, p: float) -> float:
        """Apply beta calibration.  Returns *p* unchanged when not fitted."""
        if not self._fitted:
            return p
        p = max(1e-6, min(1.0 - 1e-6, p))
        logit_cal = self.a * math.log(p) - self.b * math.log(1.0 - p) + self.c
        # numerically-stable sigmoid
        if logit_cal >= 0:
            result = 1.0 / (1.0 + math.exp(-logit_cal))
        else:
            ez = math.exp(logit_cal)
            result = ez / (1.0 + ez)
        return max(0.01, min(0.99, result))

    # -- fitting ------------------------------------------------------------

    async def fit_from_db(self, db, game: str, days: int = 90) -> bool:
        """Fit from ``esports_prediction_log`` resolved predictions.

        Returns ``True`` when fitting succeeds, ``False`` when there is
        insufficient data (calibrator stays at / reverts to identity).
        """
        if db is None:
            return False

        try:
            from sqlalchemy import text as _text
            async with db.get_session(timeout=15) as session:
                result = await session.execute(
                    _text(
                        "SELECT COALESCE(raw_model_prob, predicted_prob), actual_outcome, "
                        "EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400.0 AS age_days "
                        "FROM esports_prediction_log "
                        "WHERE actual_outcome IS NOT NULL "
                        "AND game = :game "
                        "AND created_at > NOW() - :days_int * INTERVAL '1 day' "
                        "ORDER BY created_at DESC LIMIT 5000"
                    ),
                    {"game": game, "days_int": int(days)},
                )
                rows = result.fetchall()
        except Exception as exc:
            logger.debug("beta_cal_query_failed", game=game, error=str(exc))
            return False

        if len(rows) < self.min_samples:
            self._fitted = False
            self._n_samples = len(rows)
            return False

        import numpy as np
        from scipy.optimize import minimize as _minimize

        preds = np.clip(
            np.array([float(r[0]) for r in rows]), 1e-6, 1.0 - 1e-6
        )
        outcomes = np.array([float(r[1]) for r in rows])
        # S151: Temporal decay — half-life 7 days. Recent post-fix data
        # dominates; old death-spiral-era predictions fade within 2-3 weeks.
        ages = np.array([max(0.0, float(r[2])) for r in rows])
        _half_life = 7.0
        weights = np.exp(-np.log(2.0) * ages / _half_life)
        weights = weights / weights.sum() * len(weights)  # normalize, preserve loss scale
        ln_p = np.log(preds)
        ln_1mp = np.log(1.0 - preds)
        # S151: Lowered from 200/n (floor 2.0) to 50/n (floor 0.5).
        # At LoL n=38, old lambda=5.26 pinned calibrator to identity.
        # New lambda=1.32 lets it actually learn. Bounds still prevent wild params.
        lam = max(0.5, 50.0 / max(len(rows), 1))

        def _loss(params):
            a, b, c_ = params
            logits = a * ln_p - b * ln_1mp + c_
            # S151: Weighted NLL — recent samples count more
            per_sample = (
                outcomes * np.logaddexp(0.0, -logits)
                + (1.0 - outcomes) * np.logaddexp(0.0, logits)
            )
            nll = np.sum(weights * per_sample) / len(weights)
            reg = lam * ((a - 1.0) ** 2 + (b - 1.0) ** 2 + c_ ** 2)
            return nll + reg

        res = _minimize(
            _loss,
            x0=[1.0, 1.0, 0.0],
            method="L-BFGS-B",
            bounds=[(0.1, 5.0), (0.1, 5.0), (-2.0, 2.0)],
        )
        self.a, self.b, self.c = float(res.x[0]), float(res.x[1]), float(res.x[2])
        self._fitted = True
        self._n_samples = len(rows)
        return True

    @property
    def is_fitted(self) -> bool:
        return self._fitted


class OnlinePlattCalibrator:
    """Streaming Platt scaling — updates calibration on every resolved prediction.

    Uses River LogisticRegression for online logistic calibration.
    Identity at <30 samples. Supplements batch BetaCalibrator with
    streaming adaptation between refits.
    """
    __slots__ = ("_model", "_n", "_min_samples", "_available")

    def __init__(self, lr: float = 0.01, min_samples: int = 50):
        self._n: int = 0
        self._min_samples = min_samples
        self._available = False
        try:
            from river.linear_model import LogisticRegression
            from river.optim import SGD
            self._model = LogisticRegression(optimizer=SGD(lr))
            self._available = True
        except ImportError:
            self._model = None

    def update(self, predicted: float, actual: int) -> None:
        """Feed one resolved prediction into the streaming model."""
        if not self._available:
            return
        predicted = max(1e-6, min(1.0 - 1e-6, predicted))
        logit = math.log(predicted / (1.0 - predicted))
        self._model.learn_one({"logit": logit}, actual)
        self._n += 1

    def calibrate(self, p: float) -> float:
        """Apply online Platt scaling. Returns p unchanged if <min_samples."""
        if not self._available or self._n < self._min_samples:
            return p
        p = max(1e-6, min(1.0 - 1e-6, p))
        logit = math.log(p / (1.0 - p))
        return self._model.predict_proba_one({"logit": logit}).get(1, p)

    @property
    def is_fitted(self) -> bool:
        return self._available and self._n >= self._min_samples


class EsportsBot(BaseBot):
    """
    Pre-game + live in-play esports trading bot.

    Covers match_winner, map_winner, tournament_winner, total_maps
    across LoL, CS2, Dota 2, Valorant, CoD, R6, StarCraft II, and Rocket League on Polymarket.
    """

    def __init__(self, base_engine):
        super().__init__("EsportsBot", base_engine)

        # Fail fast if PandaScore API key not configured
        api_key = getattr(settings, "PANDASCORE_API_KEY", None)
        if not api_key:
            raise ValueError(
                "EsportsBot requires PANDASCORE_API_KEY — set it in .env"
            )

        self._api_key = api_key
        self._pandascore = None       # Lazy init in start()
        self._patch_drift = None      # PatchDriftDetector instance
        self._market_scanner = None   # EsportsMarketScanner instance
        self._market_service = None   # EsportsMarketService instance (Commit 4/5)
        self._lol_model = None       # LoLWinModel
        self._cs2_model = None       # CS2EconomyModel
        self._dota2_model = None     # Dota2Model
        self._valorant_model = None  # ValorantModel
        # S136 Phase 11A-11D: New game models
        self._cod_model = None       # CoDModel
        self._rl_model = None        # RocketLeagueModel
        self._sc2_model = None       # SC2Model
        self._r6_model = None        # R6Model
        self._trainer = None         # EsportsModelTrainer
        self._opendota = None        # OpenDotaClient (Dota2 enrichment)
        self._aligulac = None        # AligulacClient (SC2 ratings blend)
        self._ballchasing = None     # BallchasingClient (RL replay stats)
        self._cross_game_model = None  # XGBClassifier (cross-game meta model)
        self._bg_train_tasks: Dict[str, asyncio.Task] = {}  # game → background train task
        # S156: Limit concurrent training tasks to prevent CPU/memory spikes on 16GB VPS
        self._train_semaphore = asyncio.Semaphore(3)

        # Per-game/tournament/team exposure tracking (USD deployed)
        self._game_exposure: Dict[str, float] = {}        # game → USD
        self._tournament_exposure: Dict[str, float] = {}  # tournament_id → USD

        # Live match tracking
        self._live_matches: Dict[str, Dict] = {}  # match_id → PandaScore match data
        self._last_live_refresh = 0.0
        self._live_refresh_failures: int = 0       # consecutive PandaScore refresh failures

        # Prediction cache for WS reactive path
        self._prediction_cache: Dict[str, Dict] = {}  # market_id → {prob, ts, game}

        # Token map: market_id → {"yes": yes_token_id, "no": no_token_id}
        # Populated during scan, used by WS path to identify YES vs NO token prices.
        self._market_token_map: Dict[str, Dict[str, str]] = {}

        # Market→game mapping: populated on trade execution, used for game
        # exposure decrement on exit.  Survives prediction_cache expiry (1h TTL).
        self._market_game: Dict[str, str] = {}

        # Race-condition guard: prevents concurrent WS trades on the same market.
        # Lifecycle: added at the top of _handle_ws_price_update(), removed in
        # the `finally` block — always, even on exception.
        # This set is empty between trades; it never accumulates entries.
        self._ws_pending_trades: set = set()

        # S132 EB-3: Opposing-side guard — {(market_id, side)} persists across restarts
        self._entered_market_sides: set = set()
        self._entered_sides_restored: bool = False

        # Track data collection attempts per game: game → attempt count (max 3 retries)
        self._collection_attempted: Dict[str, int] = {}

        # Latency tracker: WS price move time vs last PandaScore refresh (bounded)
        self._latency_samples: collections.deque = collections.deque(maxlen=100)

        # Prediction log dedup: market_id → (logged_prob, logged_ts)
        # Skip re-logging if prediction unchanged for same market within 10 min
        self._prediction_log_cache: Dict[str, tuple] = {}

        # S157: Reason-specific execution failure cooldown.
        # Replaces S135 blanket 300s cooldown. Dict stores (fail_code, expiry_monotonic).
        self._exec_fail_cooldown: Dict[str, tuple] = {}  # market_id → (fail_code, expiry)

        # E4: Monitoring thresholds — per-game Brier alerts
        self._monitoring_halted_games: set = set()  # games halted by monitoring
        self._monitoring_last_check: float = 0.0
        self._monitoring_check_interval: float = 600.0  # 10 minutes

        # Glicko-2 trackers for "easy mode" pre-game predictions
        self._glicko2_trackers: Dict[str, Any] = {}  # game → Glicko2Tracker
        self._team_name_to_id: Dict[str, str] = {}    # lowercased team name → team key
        self._team_name_to_ps_id: Dict[str, int] = {}   # S137 10C: lowercased name → PandaScore numeric ID
        self._backfill_attempted: set = set()            # "game:name" keys already queried this session
        self._backfill_calls_this_scan: int = 0          # reset each scan; capped
        self._max_backfills_per_scan: int = int(
            getattr(settings, "ESPORTS_MAX_BACKFILLS_PER_SCAN", 10)
        )
        self._team_fail_logged: set = set()              # rate-limit team_match_fail logs (per session)
        self._last_glicko2_miss_reason: str = ""         # S100: diagnostic reason for last glicko2 miss
        self._calibration_ece: Dict[str, float] = {}     # game → latest ECE (updated every 10 min)
        self._edge_decay_data: Dict[str, Dict] = {}      # game → latest edge decay analysis
        self._game_kelly_mult: Dict[str, float] = {}     # game → Kelly multiplier (Brier-based)
        self._game_brier_cache: Dict[str, float] = {}     # game → latest rolling Brier (for signal quality)
        # S136 Phase 3A: Entry edge cache for edge-based exits
        self._entry_edge_cache: Dict[str, Dict] = {}  # market_id → {model_prob, edge, market_type}
        self._edge_peaks: Dict[str, float] = {}        # market_id → peak remaining edge
        self._edge_cache_restored: bool = False  # S142: one-time restore from DB
        # S109: Post-exit cooldown — prevents stop-loss churn (RC1)
        self._recently_exited: Dict[str, float] = {}     # market_id → monotonic time of exit
        # S138: Track exit reason for extended edge_gone cooldown
        self._exit_reasons: Dict[str, str] = {}          # market_id → exit reason
        # S109: Per-market rolling entry cap — hard backstop against churn (RC3)
        self._market_entry_times: Dict[str, list] = {}    # market_id → [monotonic timestamps]
        # S157: Edge hysteresis — separate entry threshold from hold threshold.
        # Replaces S155 escalating cooldown + S156 adaptive min_edge.
        self._min_edge_entry = float(getattr(settings, "ESPORTS_MIN_EDGE_ENTRY", 0.08))
        self._min_edge_hold = float(getattr(settings, "ESPORTS_MIN_EDGE_HOLD", 0.03))
        # S157 review: Counter for markets in hysteresis band (hold < edge < entry)
        self._hysteresis_hold_count = 0

        # WS price tracking and cooldown dicts (moved from hasattr lazy-init)
        self._ws_prev_prices: Dict[str, float] = {}
        self._ws_cooldowns: Dict[str, float] = {}
        self._series_ws_prev_prices: Dict[str, float] = {}
        self._series_ws_cooldowns: Dict[str, float] = {}
        self._last_cleanup_date: Optional[str] = None

        # S100: Single-stage beta calibrators per game
        # S154: Lowered min_samples from 50 to 15 so thin games (Dota2 n=34,
        # Valorant n=16) can fit per-game BetaCal. Safe because:
        #   - Regularization is strong at low n: lam = max(0.5, 50/n) = 3.3 at n=15
        #   - Hierarchical pooling dominates: w_global = 25/(n+25) = 61% at n=16
        #   - Worst case: near-identity calibration (harmless, equivalent to global)
        self._beta_calibrators: Dict[str, BetaCalibrator] = {
            g: BetaCalibrator(lambda_reg=10.0, min_samples=15)
            for g in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")
        }
        self._onnx_cross_game_session: Any = None  # ONNX InferenceSession for cross-game XGB
        # Per-game ONNX sessions for faster inference (Session 83)
        self._onnx_lol_session: Any = None
        self._onnx_cs2_session: Any = None
        self._onnx_dota2_session: Any = None
        self._onnx_valorant_session: Any = None
        # Session 83: Per-game EGM d, edge decay multiplier, conformal intervals
        # S142: Initialize all games to d=1.0 (conservative, no extremization).
        # _update_per_game_egm_d() elevates well-calibrated games to d=1.5+ within 10min.
        # Prevents over-extremized predictions during the startup window before monitoring runs.
        self._game_egm_d: Dict[str, float] = {
            g: 1.0 for g in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")
        }
        self._edge_decay_mult: Dict[str, float] = {}  # game → sizing multiplier from edge decay
        self._tabpfn_predictor: Any = None  # TabPFN ensemble for sparse games
        self._cot_validator: Any = None  # CoT LLM validator for high-edge trades
        # S100b: Per-game conformal predictors for Kelly sizing (Phase 3)
        self._conformal_per_game: Dict[str, Any] = {}
        # S100b: Per-game ADWIN drift detectors (Phase 4)
        self._adwin_per_game: Dict[str, Any] = {}
        # S136 Phase 9C: Per-game divergence accuracy tracking
        self._divergence_accuracy: Dict[str, Dict[str, list]] = {}
        # S136 Phase 9D: ADWIN drift retrain flags
        self._adwin_drift_detected: Dict[str, bool] = {}
        # S100b: Per-game Online Platt calibrators (Phase 2)
        # S154: Lowered min_samples from 50 to 15 (matching BetaCal).
        # OnlinePlatt has only 2 params (slope+intercept), lr=0.01 is conservative.
        self._online_platt_per_game: Dict[str, Any] = {
            g: OnlinePlattCalibrator(min_samples=15) for g in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")
        }
        # S136 Phase 4A: Per-game Venn-ABERS calibrators
        self._venn_abers_per_game: Dict[str, Any] = {}
        # S136 Phase 4B: Global pooled BetaCalibrator (hierarchical shrinkage)
        self._global_beta_calibrator = BetaCalibrator(lambda_reg=10.0, min_samples=50)

        # Parallel analysis (Item 6)
        _concurrency = int(getattr(settings, "ESPORTS_ANALYSIS_CONCURRENCY", 10))
        self._analysis_semaphore = asyncio.Semaphore(_concurrency)
        self._trade_lock = asyncio.Lock()  # serializes exposure-mutating trade execution

        # Series trading state (always on — merged from EsportsSeriesBot)
        self._series_min_edge = float(getattr(settings, "ESPORTS_SERIES_MIN_EDGE", 0.10))
        self._series_reverse_sweep_floor = float(
            getattr(settings, "ESPORTS_SERIES_REVERSE_SWEEP_FLOOR", 0.05)
        )
        self._series_hedge_enabled = bool(
            getattr(settings, "ESPORTS_SERIES_HEDGE_ENABLED", True)
        )
        self._series_refresh_interval = int(
            getattr(settings, "ESPORTS_SERIES_REFRESH_INTERVAL", 30)
        )
        self._active_series: Dict[str, Dict] = {}
        self._series_prediction_cache: Dict[str, Dict] = {}
        self._series_last_refresh: float = 0.0
        self._series_glicko2_cache: Dict[str, float] = {}
        self._hltv = None

        # Recent form cache: (team_id, game) → (win_rate, mono_timestamp)
        self._team_form_cache: Dict[tuple, tuple] = {}
        self._team_form_ttl: float = 5400.0  # 90min TTL (form changes once per match, ~2-4h)

        # Roster change detection: team_id → (roster_hash, change_timestamp)
        self._roster_cache: Dict[str, tuple] = {}
        self._roster_change_cache: Dict[str, float] = {}  # team_id → mono_time of change

        # S94: Background form prefetch task
        self._form_prefetch_task: Optional[asyncio.Task] = None

        # S94: WS-primary trading mode (scan becomes cache-warmer)
        self._ws_trading_active: bool = True
        self._last_ws_price_ts: float = 0.0

        # CatBoost draft models (Session C): game → CatBoostDraftModel
        self._catboost_models: Dict[str, Any] = {}
        self._draft_feature_builder: Any = None  # DraftFeatureBuilder instance
        self._catboost_last_train: Dict[str, float] = {}  # game → monotonic timestamp

        # CLV-gated position scaling (WS2)
        self._clv_scaling_tier: str = "conservative"

        # Settings
        # "Easy mode": relaxed thresholds until models graduate, then tighten.
        # Graduation = accuracy >= 55% + brier <= 0.24 on holdout.
        self._min_edge = float(getattr(settings, "ESPORTS_MIN_EDGE", 0.05))  # 5% easy mode
        self._min_confidence = float(getattr(settings, "ESPORTS_MIN_CONFIDENCE", 0.50))  # easy mode
        # S112: edge cap removed — all edges trade. High edges logged for monitoring.
        # S157: _churn_edge_penalty removed — replaced by edge hysteresis (min_edge_entry/hold)
        self._egm_d = float(getattr(settings, "ESPORTS_EGM_D", 1.5))  # EGM extremization factor
        self._maker_timeout = float(
            getattr(settings, "ESPORTS_MAKER_FALLBACK_TIMEOUT_S", 3.0)
        )
        self._scan_count: int = 0  # P1.2: periodic outcome backfill counter
        self._exposure_restored: bool = False  # P0: seed exposure dicts from DB on first scan
        self._market_game_restored: bool = False  # S125: seed _market_game from trade_events

        # S94: Rolling accuracy cache (batch all 8 games, 5-min TTL)
        # Keyed by last_n so we can cache both last_n=50 and last_n=20
        self._rolling_accuracy_cache: Dict[int, Dict[str, Dict]] = {}
        self._rolling_accuracy_cache_ts: Dict[int, float] = {}

        # S94: Time-based guards (replace scan-count modulo guards for zero-sleep scanning)
        self._last_cache_cleanup: float = 0.0       # was: _scan_count % 100 (~3.3h)
        self._last_pnl_refresh: float = 0.0         # was: _scan_count % 10 (~20min)
        self._last_reevaluate: float = 0.0           # was: _scan_count % 5 (~10min)
        self._last_kelly_check: float = 0.0          # was: _scan_count % 10 (~20min)
        self._last_outcome_backfill: float = 0.0     # was: _scan_count % 10 (~20min)
        self._last_clv_backfill: float = 0.0         # was: _clv_backfill_counter >= 10
        self._last_ems_prune: float = 0.0            # S159: prune _entered_market_sides every 30min

        # A1+A8: Daily loss limit + drawdown halt
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: Optional[str] = None
        self._daily_loss_limit = float(getattr(settings, "ESPORTS_DAILY_LOSS_LIMIT", 500.0))
        self._drawdown_halted: bool = False

        # A3: Dynamic Kelly graduation
        self._kelly_graduated: bool = False  # True when 50+ resolved + Brier<0.24

    def _get_scan_interval_seconds(self) -> float:
        """A4: Tournament-aware scan intervals.

        0s during live matches (rescan immediately), 60s with open positions,
        120s otherwise. Safe because all periodic operations use time-based
        guards (monotonic clock) instead of scan-count modulo.
        """
        if self._live_matches:
            return float(getattr(settings, "SCAN_INTERVAL_ESPORTS_LIVE", 0))
        # A4: Tighter scan when we have open positions (for stop-loss monitoring)
        try:
            og = getattr(self.base_engine, "order_gateway", None)
            if og is not None:
                bot_positions = getattr(og, "_open_position_markets", {})
                if isinstance(bot_positions, dict) and bot_positions.get(self.bot_name):
                    return 60.0
        except Exception as e:
            logger.debug("scan_interval_position_check_failed: %s", e)
        return float(getattr(settings, "SCAN_INTERVAL_ESPORTS", 120))

    async def start(self) -> None:
        """Initialize data clients and models, then start scan loop."""
        from esports.data.pandascore_client import PandaScoreClient
        from esports.models.patch_drift import PatchDriftDetector
        from esports.markets.esports_market_scanner import EsportsMarketScanner

        self._pandascore = PandaScoreClient(api_key=self._api_key)
        await self._pandascore.init()

        riot_key = getattr(settings, "RIOT_API_KEY", None)
        riot_client = None
        if riot_key:
            from esports.data.riot_api_client import RiotApiClient
            riot_client = RiotApiClient(api_key=riot_key)
            await riot_client.init()

        _obs_hours = int(getattr(settings, "ESPORTS_OBSERVATION_HOURS", 48))
        self._patch_drift = PatchDriftDetector(
            riot_client=riot_client, observation_hours=_obs_hours,
        )

        # OpenDota client — free Dota2 hero + team form data (no auth needed)
        try:
            from esports.data.opendota_client import OpenDotaClient
            self._opendota = OpenDotaClient()
            logger.info("EsportsBot: OpenDota client initialized")
        except Exception as exc:
            self._opendota = None
            logger.warning("EsportsBot: OpenDota client not available", error=str(exc))

        # Aligulac client — SC2 Elo ratings + match predictions (free key)
        aligulac_key = getattr(settings, "ALIGULAC_API_KEY", None)
        if aligulac_key:
            try:
                from esports.data.aligulac_client import AligulacClient
                self._aligulac = AligulacClient(api_key=aligulac_key)
                logger.info("EsportsBot: Aligulac client initialized")
            except Exception as exc:
                self._aligulac = None
                logger.warning("EsportsBot: Aligulac client not available", error=str(exc))
        else:
            self._aligulac = None

        # Ballchasing client — RL replay stats (free key)
        ballchasing_key = getattr(settings, "BALLCHASING_API_KEY", None)
        if ballchasing_key:
            try:
                from esports.data.ballchasing_client import BallchasingClient
                self._ballchasing = BallchasingClient(api_key=ballchasing_key)
                logger.info("EsportsBot: Ballchasing client initialized")
            except Exception as exc:
                self._ballchasing = None
                logger.warning("EsportsBot: Ballchasing client not available", error=str(exc))
        else:
            self._ballchasing = None

        # HLTV scraper — CS2 per-team map win rates for series analysis
        try:
            from esports.data.hltv_scraper import HLTVScraper
            self._hltv = HLTVScraper()
            logger.info("EsportsBot: HLTV scraper initialized")
        except Exception:
            logger.debug("EsportsBot: HLTV scraper not available")

        db = getattr(self.base_engine, "db", None)
        # market_service passed below after initialization (if successful)
        self._market_scanner = EsportsMarketScanner(db=db)

        # Load models — try saved first, then train if data available
        try:
            from esports.models.lol_win_model import LoLWinModel
            self._lol_model = LoLWinModel()
            if not self._lol_model.load():
                logger.info("EsportsBot: no saved LoL model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: LoL model not loaded (no saved model yet)")

        try:
            from esports.models.cs2_economy_model import CS2EconomyModel
            self._cs2_model = CS2EconomyModel()
            if not self._cs2_model.load():
                logger.info("EsportsBot: no saved CS2 model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: CS2 model not loaded")

        try:
            from esports.models.dota2_model import Dota2Model
            self._dota2_model = Dota2Model()
            if not self._dota2_model.load():
                logger.info("EsportsBot: no saved Dota2 model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: Dota2 model not loaded")

        try:
            from esports.models.valorant_model import ValorantModel
            self._valorant_model = ValorantModel()
            if not self._valorant_model.load():
                logger.info("EsportsBot: no saved Valorant model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: Valorant model not loaded")

        # S136 Phase 11A-11D: Load new game models
        try:
            from esports.models.cod_model import CoDModel
            self._cod_model = CoDModel()
            if not self._cod_model.load():
                logger.info("EsportsBot: no saved CoD model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: CoD model not loaded")

        try:
            from esports.models.rl_model import RocketLeagueModel
            self._rl_model = RocketLeagueModel()
            if not self._rl_model.load():
                logger.info("EsportsBot: no saved RL model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: RL model not loaded")

        try:
            from esports.models.sc2_model import SC2Model
            self._sc2_model = SC2Model()
            if not self._sc2_model.load():
                logger.info("EsportsBot: no saved SC2 model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: SC2 model not loaded")

        try:
            from esports.models.r6_model import R6Model
            self._r6_model = R6Model()
            if not self._r6_model.load():
                logger.info("EsportsBot: no saved R6 model — will train on first scan")
        except Exception:
            logger.debug("EsportsBot: R6 model not loaded")

        # Initialize trainer for periodic retraining
        try:
            from esports.models.esports_trainer import EsportsModelTrainer
            self._trainer = EsportsModelTrainer(pandascore_client=self._pandascore)
        except Exception:
            logger.debug("EsportsBot: trainer not available")

        # Load cross-game XGBoost meta model (if previously trained)
        self._load_cross_game_model()

        # S100: Old 3-stage calibration pipeline removed. BetaCalibrators
        # initialized in __init__ (per-game, identity priors).  The old classes
        # (FocalTemperatureCalibrator, EsportsBiasDecomposition, HorizonBias-
        # Calibrator) still exist in their modules but are no longer used by
        # EsportsBot.
        logger.info("EsportsBot: BetaCalibrator initialized for %d games",
                     len(self._beta_calibrators))

        # Session 83: Load per-game ONNX sessions
        self._load_per_game_onnx_sessions()

        # Session 83: Initialize TabPFN for sparse games
        try:
            from esports.models.tabpfn_ensemble import TabPFNEnsemble
            self._tabpfn_predictor = TabPFNEnsemble()
            logger.info("EsportsBot: TabPFN ensemble initialized")
        except Exception as exc:
            logger.debug("EsportsBot: TabPFN not available", error=str(exc))

        # Session 83: Initialize CoT validator for high-edge trades
        try:
            from esports.models.cot_validator import CoTValidator
            self._cot_validator = CoTValidator()
        except Exception as exc:
            logger.debug("EsportsBot: CoT validator not available", error=str(exc))

        lol_trained = self._lol_model is not None and self._lol_model.is_trained
        cs2_trained = self._cs2_model is not None and self._cs2_model.is_trained

        # Build Glicko-2 trackers and team name mapping for "easy mode" pre-game predictions.
        # These provide real signal (63% accuracy per EsportsBench) while ML models train.
        await self._init_glicko2_trackers(db)

        # S94: Warm form cache to avoid cold-start API burst on first scan
        await self._warm_form_cache()
        # S94: Background form prefetch — keeps form data cached between scans
        self._form_prefetch_task = asyncio.create_task(self._background_form_prefetch())
        self._form_prefetch_task.add_done_callback(self._task_error_handler)  # S156: log if task dies

        # Initialize dedicated esports market service — bypasses broken Gamma API.
        # Queries DB directly for esports markets + background CLOB price refresh.
        try:
            from esports.markets.esports_market_service import EsportsMarketService
            poly_client = getattr(self.base_engine, "client", None)
            self._market_service = EsportsMarketService(db=db, polymarket_client=poly_client)
            self._market_service.start_background_refresh()
            # Wire to scanner so it also benefits from DB-backed market discovery
            if self._market_scanner:
                self._market_scanner._market_service = self._market_service
        except Exception as exc:
            logger.warning("EsportsBot: market service init failed", error=str(exc))

        # P2.2: Wire trainer into LearningScheduler for unified retrain scheduling
        _sched = getattr(self.base_engine, "scheduler", None)
        if _sched is not None and self._trainer is not None:
            _sched.esports_trainer = self._trainer

        # S109: Restore exit cooldowns from Redis (survives restarts)
        await self._restore_exit_cooldowns_from_redis()
        # S156: Restore exec failure cooldowns from Redis
        await self._restore_exec_fail_from_redis()
        # S143: Restore per-market entry counts from Redis (prevents cap bypass on restart)
        await self._restore_entry_counts_from_redis()

        # S131: Seed Brier cache from DB on startup — eliminates 10-min cold start
        # where _game_brier_cache is empty and sq_brier defaults to 0.0.
        try:
            _acc_all = await self._get_cached_rolling_accuracy(db)
            for _g, _acc in _acc_all.items():
                if _acc and _acc.get("total", 0) >= 10:
                    self._game_brier_cache[_g] = _acc["brier_score"]
            if self._game_brier_cache:
                logger.info("esportsbot_brier_cache_seeded",
                            games=list(self._game_brier_cache.keys()),
                            values={g: round(v, 3) for g, v in self._game_brier_cache.items()})
        except Exception as exc:
            logger.warning("esportsbot_brier_seed_failed", error=str(exc))

        logger.info(
            "EsportsBot: initialized",
            pandascore=True,
            riot_api=bool(riot_key),
            lol_model_trained=lol_trained,
            cs2_model_trained=cs2_trained,
            glicko2_teams=len(self._team_name_to_id),
            market_service=self._market_service is not None,
        )

        await super().start()

    async def stop(self) -> None:
        """Clean up HTTP clients, market service, and prefetch task."""
        if self._form_prefetch_task and not self._form_prefetch_task.done():
            self._form_prefetch_task.cancel()
            try:
                await self._form_prefetch_task
            except asyncio.CancelledError:
                pass
        if self._market_service:
            await self._market_service.close()
        if self._pandascore:
            await self._pandascore.close()
        await super().stop()

    def _passes_ws_entry_gates(
        self, market_id: str, side: str, game: str, trade_price: float,
    ) -> bool:
        """Shared WS entry gates for both match_winner and series paths.

        Returns True if trade should proceed, False if any gate blocks it.
        Gates match the scan path enforcement to prevent WS/scan divergence.
        """
        # Daily loss limit
        if self._check_daily_loss_limit():
            return False
        # Hard game disable
        _disabled = getattr(settings, "ESPORTS_DISABLED_GAMES", "")
        if _disabled and game in {g.strip() for g in _disabled.split(",") if g.strip()}:
            return False
        if game in self._monitoring_halted_games:
            return False
        if self._patch_drift and self._patch_drift.is_observation_mode(game):
            return False
        if self._patch_drift and self._patch_drift.is_halted(game):
            return False
        # Exposure cap per game
        _caps = self._get_exposure_caps()
        if self._game_exposure.get(game, 0.0) >= _caps["per_game"]:
            return False
        # Penny/extreme price guard
        _min_price = float(getattr(settings, "ESPORTS_MIN_ENTRY_PRICE", 0.05))
        _max_price = float(getattr(settings, "ESPORTS_MAX_ENTRY_PRICE", 0.95))
        if trade_price < _min_price or trade_price > _max_price:
            return False
        # S157: Flat post-exit cooldown (escalating removed — hysteresis handles churn)
        _exit_ts = self._recently_exited.get(market_id)
        if _exit_ts is not None:
            _exit_reason = self._exit_reasons.get(market_id, "")
            _cooldown = (float(getattr(settings, "ESPORTS_EDGE_GONE_COOLDOWN_SECONDS", 1800.0))
                         if _exit_reason in ("edge_gone", "trailing_edge")
                         else float(getattr(settings, "ESPORTS_EXIT_COOLDOWN_SECONDS", 300.0)))
            if time.monotonic() - _exit_ts < _cooldown:
                return False
        # Per-market rolling entry cap
        _max_entries = int(getattr(settings, "ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW", 2))
        _window_s = float(getattr(settings, "ESPORTS_ENTRY_WINDOW_HOURS", 12.0)) * 3600
        _now_mono = time.monotonic()
        _recent = [t for t in self._market_entry_times.get(market_id, []) if _now_mono - t < _window_s]
        if len(_recent) >= _max_entries:
            return False
        # Pending trade guard (race condition)
        if market_id in self._ws_pending_trades:
            return False
        # Position check
        og = getattr(self.base_engine, "order_gateway", None)
        if og is not None and og.has_open_position(self.bot_name, str(market_id)):
            return False
        # Opposing-side historical guard
        _opposite = "NO" if side.upper() == "YES" else "YES"
        if (market_id, _opposite) in self._entered_market_sides:
            return False
        return True

    async def on_price_update(self, event: dict) -> None:
        """
        React to WS price updates — cross-reference cached prediction vs new price.

        LATENCY FIX: Check prediction cache BEFORE calling super().on_price_update().
        The base_bot's on_price_update() does latency logging, price caching, and
        metrics for EVERY WS event (~26K markets). By skipping super() for non-esports
        markets, we reduce processing from ~100 events/sec to ~1/sec for this bot.

        TOKEN FIX: WS events carry a token_id (YES or NO token). Previous code keyed
        _ws_prev_prices by market_id only, so YES (0.84) and NO (0.06) prices
        overwrote each other → false 0.84→0.06 "oscillation" and fake 44-63% edges.
        Now uses _market_token_map to identify which token the price belongs to and
        converts to YES-equivalent before computing edge.
        """
        if not self.running:
            return

        market_id = event.get("market_id", "")
        if not market_id:
            return

        # Early exit: only process events for markets we've already analyzed.
        # This MUST come before super() to avoid processing all 26K markets.
        cached = self._prediction_cache.get(market_id)
        if not cached:
            # S100: Update WS liveness even without prediction cache — fixes
            # bootstrap where _last_ws_price_ts never updates because cache
            # is empty on startup (populated after first scan completes).
            if market_id in self._market_token_map:
                self._last_ws_price_ts = time.monotonic()
            # Fallback: check series prediction cache for BO3/BO5 markets
            if self._series_prediction_cache.get(market_id):
                await self._series_on_price_update(event)
            return  # Skip super() for non-esports markets — avoids latency overhead

        # Only call super() for esports markets we care about
        await super().on_price_update(event)
        self._last_ws_price_ts = time.monotonic()

        token_id = event.get("token_id", "")
        new_price = float(event.get("price", 0))
        if new_price <= 0 or not token_id:
            return

        # Identify which token this price update is for (YES or NO).
        # _market_token_map is populated during scan_and_trade → analyze_opportunity.
        token_map = self._market_token_map.get(market_id)
        if not token_map:
            return  # Haven't scanned this market yet — skip

        yes_token_id = token_map.get("yes", "")
        no_token_id = token_map.get("no", "")

        if token_id == yes_token_id:
            yes_price = new_price
        elif token_id == no_token_id:
            yes_price = 1.0 - new_price  # Convert NO price to YES-equivalent
        else:
            return  # Unknown token for this market — skip

        # Track latency: time since last PandaScore refresh
        if self._last_live_refresh > 0:
            latency = time.monotonic() - self._last_live_refresh
            self._latency_samples.append(latency)

        # Significance threshold — keyed by token_id to avoid YES/NO cross-contamination
        threshold = float(getattr(settings, "ESPORTS_WS_PRICE_CHANGE_PCT", 0.01))
        old_yes_price = self._ws_prev_prices.get(token_id)
        self._ws_prev_prices[token_id] = yes_price
        if old_yes_price is None or abs(yes_price - old_yes_price) / max(old_yes_price, 0.01) < threshold:
            return

        # Cooldown
        now = time.monotonic()
        cooldown = int(getattr(settings, "ESPORTS_WS_COOLDOWN_SECONDS", 10))
        if now - self._ws_cooldowns.get(market_id, 0) < cooldown:
            return
        self._ws_cooldowns[market_id] = now

        # Recalculate edge with correctly-identified YES price
        model_prob = cached["prob"]  # Always P(YES)
        game = cached.get("game", "")

        # S159: LoL YES dampener — mirror scan path correction in WS path
        _LOL_YES_SHRINK_WS = float(getattr(settings, "ESPORTS_LOL_YES_SHRINK", 0.40))
        if game == "lol" and model_prob > yes_price and _LOL_YES_SHRINK_WS > 0:
            model_prob = yes_price + (model_prob - yes_price) * (1.0 - _LOL_YES_SHRINK_WS)

        edge = model_prob - yes_price

        # S139: Divergence cap — WS path must enforce the same cap as scan path.
        _div_ws = abs(model_prob - yes_price)
        _eff_div_cap_ws = min(
            self._get_adaptive_div_cap(game),
            float(getattr(settings, "ESPORTS_MAX_MODEL_DIVERGENCE", 0.25)),
        )
        if _div_ws > _eff_div_cap_ws:
            logger.info(
                "esportsbot_divergence_capped_ws", market_id=market_id, game=game,
                model_prob=round(model_prob, 4), market_price=round(yes_price, 4),
                divergence=round(_div_ws, 4), cap=_eff_div_cap_ws,
            )
            return

        # S157: Edge hysteresis — use higher entry threshold for new positions
        if abs(edge) < self._min_edge_entry:
            if abs(edge) >= self._min_edge_hold:
                self._hysteresis_hold_count += 1
                logger.debug("esportsbot_hysteresis_hold", market_id=market_id,
                             edge=round(edge, 4), entry_thresh=self._min_edge_entry)
            return

        if abs(edge) > 0.40:
            logger.info("esportsbot_ws_high_edge", market_id=market_id, edge=round(edge, 4))

        side = "YES" if edge > 0 else "NO"
        trade_token_id = yes_token_id if side == "YES" else no_token_id
        trade_price = yes_price if side == "YES" else (1.0 - yes_price)

        # Shared entry gates (daily loss, game disable, exposure, price, cooldown, etc.)
        if not self._passes_ws_entry_gates(market_id, side, game, trade_price):
            return

        self._ws_pending_trades.add(market_id)
        try:
            side_prob = model_prob if side == "YES" else (1.0 - model_prob)
            _sq, _ = self._compute_signal_quality(game, market_id)
            # S131: SQ is sizing multiplier, confidence = raw side_prob
            confidence = side_prob
            # S142: Pull BM sigma inputs from prediction cache event_data
            _cached_ed = cached.get("event_data") or {}
            opp = {
                "type": "esports_ws_reactive",
                "market_id": market_id,
                "token_id": trade_token_id,
                "side": side,
                "price": trade_price,
                "confidence": confidence,
                "prediction": model_prob,
                "edge": round(abs(edge), 4),
                "game": game,
                "market_type": "match_winner",
                "_signal_quality": _sq,
                "_phi_a": float(_cached_ed.get("_phi_a", 200.0)),
                "_phi_b": float(_cached_ed.get("_phi_b", 200.0)),
                "_conformal_width": float(_cached_ed.get("_conformal_width", 0.15)),
                "_agreement_stdev": float(_cached_ed.get("_agreement_stdev", 0.10)),
            }
            logger.info(
                "EsportsBot WS reactive trade",
                market_id=market_id,
                side=side,
                yes_price=round(yes_price, 4),
                edge=round(abs(edge), 4),
            )
            async with self._trade_lock:
                _ws_success = await self._execute_esports_trade(opp)
            if _ws_success:
                self._market_entry_times.setdefault(market_id, []).append(time.monotonic())
                await self._save_entry_count_to_redis(market_id)
            # After successful execution, extend cooldown to one full scan cycle
            # to prevent re-triggering on the same market before next scan
            self._ws_cooldowns[market_id] = time.monotonic() + 110  # +110 so total ~120s
        except Exception as exc:
            logger.warning("esportsbot_ws_reactive_failed", market_id=market_id, error=str(exc))
        finally:
            self._ws_pending_trades.discard(market_id)

    def _cleanup_caches(self) -> None:
        """Evict stale entries from in-memory caches to prevent unbounded growth."""
        now = time.monotonic()
        # _prediction_cache: entries have 'ts' field — evict > 1 hour old
        stale = [k for k, v in self._prediction_cache.items() if now - v.get("ts", 0) > 3600]
        for k in stale:
            del self._prediction_cache[k]
        # _prediction_log_cache: entries are (confidence, ts) tuples — evict > 1 hour
        stale_log = [k for k, v in self._prediction_log_cache.items()
                     if isinstance(v, tuple) and len(v) >= 2 and now - v[1] > 3600]
        for k in stale_log:
            del self._prediction_log_cache[k]
        # _market_token_map: cap at 1000 entries — evict oldest half (no blackout)
        if len(self._market_token_map) > 1000:
            keys = list(self._market_token_map.keys())
            for k in keys[:len(keys) // 2]:
                del self._market_token_map[k]
        # _series_prediction_cache: evict > 30 min old
        stale_series = [k for k, v in self._series_prediction_cache.items()
                        if now - v.get("ts", 0) > 1800]
        for k in stale_series:
            del self._series_prediction_cache[k]
        # S109: Evict expired exit cooldowns (S155: use actual cooldown per market)
        _cd_default = float(getattr(settings, "ESPORTS_EXIT_COOLDOWN_SECONDS", 300.0))
        _cd_edge = float(getattr(settings, "ESPORTS_EDGE_GONE_COOLDOWN_SECONDS", 1800.0))
        # S157: Flat cooldown lookup (escalating removed — hysteresis handles churn)
        stale_exits = []
        for k, v in self._recently_exited.items():
            _r = self._exit_reasons.get(k, "")
            _eff_cd = _cd_edge if _r in ("edge_gone", "trailing_edge") else _cd_default
            if now - v >= _eff_cd:
                stale_exits.append(k)
        for k in stale_exits:
            del self._recently_exited[k]
        # S109: Evict expired entry timestamps from rolling window
        _window_s = float(getattr(settings, "ESPORTS_ENTRY_WINDOW_HOURS", 12.0)) * 3600
        _now_mono = time.monotonic()
        _stale_markets = []
        for _mk, _times in self._market_entry_times.items():
            self._market_entry_times[_mk] = [t for t in _times if _now_mono - t < _window_s]
            if not self._market_entry_times[_mk]:
                _stale_markets.append(_mk)
        for _mk in _stale_markets:
            del self._market_entry_times[_mk]
        # _series_glicko2_cache: cap at 500 entries — bulk clear when oversized
        if len(self._series_glicko2_cache) > 500:
            self._series_glicko2_cache.clear()
        # Prune transient WS tracking dicts — remove markets no longer in prediction caches
        _active_markets = set(self._prediction_cache.keys()) | set(self._series_prediction_cache.keys())
        for _ws_dict in (self._ws_cooldowns, self._series_ws_prev_prices,
                         self._series_ws_cooldowns):
            _stale_ws = [k for k in _ws_dict if k not in _active_markets]
            for k in _stale_ws:
                del _ws_dict[k]
        # Prune long-lived dicts with 7-day TTL (markets gone from scan for >7d)
        _7d_s = 7 * 86400
        # S157: exec_fail_cooldown stores (fail_code, expiry_monotonic) tuples
        _stale_exec = [k for k, v in self._exec_fail_cooldown.items()
                       if (v[1] if isinstance(v, tuple) else v) < now - _7d_s]
        for k in _stale_exec:
            del self._exec_fail_cooldown[k]
        _stale_reasons = [k for k in self._exit_reasons if k not in self._recently_exited]
        for k in _stale_reasons:
            del self._exit_reasons[k]
        if stale or stale_log or stale_series or stale_exits:
            logger.debug("esports_cache_cleanup", prediction_evicted=len(stale),
                         log_evicted=len(stale_log), series_evicted=len(stale_series),
                         exit_cooldown_evicted=len(stale_exits),
                         token_map_size=len(self._market_token_map),
                         glicko2_cache_size=len(self._series_glicko2_cache))

    async def scan_and_trade(self) -> None:
        """
        Main scan loop body.

        1. Check patch observation mode
        2. Refresh live match data
        3. Get esports markets from Polymarket
        4. Analyze each market for edge
        """
        self._scan_count += 1
        self._backfill_calls_this_scan = 0
        if self._cot_validator is not None:
            self._cot_validator.reset_scan_counter()
        _now = time.monotonic()
        _t0 = _now  # S110: scan timing instrumentation
        self._scan_start_mono = _now  # S115: for shadow fill latency tracking
        # S94: Time-based guards (safe with zero-sleep scanning)
        if _now - self._last_cache_cleanup >= 3600:  # 1 hour
            self._last_cache_cleanup = _now
            self._cleanup_caches()
        db = getattr(self.base_engine, "db", None)

        # P0: Restore exposure counters from today's paper_trades on first scan
        if not self._exposure_restored:
            await self._restore_exposure_from_db(db)

        # S125: Restore game tags for open positions (EXIT events need game after restart)
        if not self._market_game_restored:
            await self._restore_market_game_from_db(db)

        # S132 EB-3: Restore entered market sides for opposing-side guard
        if not self._entered_sides_restored:
            self._entered_sides_restored = True
            try:
                from sqlalchemy import text as _ems_text
                async with db.get_session(timeout=15) as _ems_sess:
                    _ems_rows = await _ems_sess.execute(
                        _ems_text(
                            "SELECT DISTINCT market_id, side FROM trade_events "
                            "WHERE bot_name IN ('EsportsBot', 'EsportsLiveBot', 'EsportsSeriesBot') "
                            "AND event_type = 'ENTRY' AND side IN ('YES', 'NO') "
                            "AND event_time >= NOW() - INTERVAL '30 days'"
                        )
                    )
                    for _mr in _ems_rows.fetchall():
                        self._entered_market_sides.add((_mr[0], _mr[1]))
                    logger.info("esports_entered_sides_restored", n=len(self._entered_market_sides))
            except Exception as _ems_exc:
                logger.warning("esports_entered_sides_restore_failed", error=str(_ems_exc))

        # S142: Restore entry_edge_cache from trade_events for open positions.
        # Without this, edge_gone and trailing_edge exits can't fire after restarts.
        if not self._edge_cache_restored and db:
            self._edge_cache_restored = True
            try:
                from sqlalchemy import text as _eec_text
                async with db.get_session(timeout=15) as _eec_sess:
                    _eec_rows = await _eec_sess.execute(
                        _eec_text(
                            "SELECT e.market_id, e.event_data "
                            "FROM trade_events e "
                            "JOIN positions p ON e.market_id = p.market_id "
                            "  AND p.source_bot = 'EsportsBot' AND p.status = 'open' "
                            "WHERE e.bot_name = 'EsportsBot' "
                            "  AND e.event_type = 'ENTRY' "
                            "  AND e.event_data IS NOT NULL "
                            "  AND e.event_data->>'entry_model_prob' IS NOT NULL "
                            "ORDER BY e.event_time DESC"
                        )
                    )
                    _restored = 0
                    for _er in _eec_rows.fetchall():
                        _mid = _er[0]
                        if _mid in self._entry_edge_cache:
                            continue  # already populated this scan
                        _ed = _er[1] if isinstance(_er[1], dict) else {}
                        _mp = float(_ed.get("entry_model_prob", 0.5))
                        _eg = float(_ed.get("entry_edge", 0.0))
                        _mt = str(_ed.get("market_type", "match_winner"))
                        self._entry_edge_cache[_mid] = {
                            "model_prob": _mp, "edge": _eg, "market_type": _mt,
                        }
                        _restored += 1
                    if _restored > 0:
                        logger.info("esports_edge_cache_restored", n=_restored)
                    # S150: Seed _edge_peaks from entry_edge_cache so trailing_edge
                    # exits don't fire immediately on first scan after restart.
                    # Without this, _edge_peaks is {} after restart, peak defaults
                    # to entry_edge, and negative remaining_edge triggers instant exit.
                    _peaks_seeded = 0
                    for _pk_mid, _pk_cache in self._entry_edge_cache.items():
                        _pk_key = f"_peak_edge_{_pk_mid}"
                        if _pk_key not in self._edge_peaks:
                            _pk_edge = float(_pk_cache.get("edge", 0.0))
                            if _pk_edge > 0:
                                self._edge_peaks[_pk_key] = _pk_edge
                                _peaks_seeded += 1
                    if _peaks_seeded > 0:
                        logger.info("esports_edge_peaks_seeded", n=_peaks_seeded)
            except Exception as _eec_exc:
                logger.warning("esports_edge_cache_restore_failed", error=str(_eec_exc))

        # A1: Restore daily P&L + reset at UTC midnight
        # Refresh every 10 min to capture mid-day resolutions
        if _now - self._last_pnl_refresh >= 600:  # 10 min
            self._last_pnl_refresh = _now
            self._daily_pnl_date = None
        await self._restore_daily_pnl_from_db(db)

        # Fetch positions once for stop-loss + re-evaluation (avoids duplicate DB query)
        # S94: Run in parallel with monitoring thresholds (independent tables)
        _positions = None
        _pos_and_monitor = []
        try:
            if db:
                async def _fetch_positions():
                    return await db.get_open_positions_for_bot(self.bot_name)
                _pos_and_monitor = await asyncio.gather(
                    _fetch_positions(),
                    self._check_monitoring_thresholds(db),
                    return_exceptions=True,
                )
                _positions = _pos_and_monitor[0] if not isinstance(_pos_and_monitor[0], Exception) else None
                if len(_pos_and_monitor) > 1 and isinstance(_pos_and_monitor[1], Exception):
                    logger.warning("esportsbot_monitoring_threshold_error", error=str(_pos_and_monitor[1]))
        except Exception:
            _positions = None

        # A1+A8: Block trading if daily loss limit or drawdown halt active
        if self._check_daily_loss_limit():
            logger.info("esportsbot_trading_blocked",
                        daily_pnl=round(self._daily_pnl, 2),
                        limit=-self._daily_loss_limit)
            # B1: Still check stop-loss exits even when new entries blocked
            await self._check_and_execute_exits(db, positions=_positions)
            return

        # B1: Check stop-loss and max hold time exits
        await self._check_and_execute_exits(db, positions=_positions)

        # A2: Re-evaluate open positions (every 10 min)
        if _now - self._last_reevaluate >= 600:  # 10 min
            self._last_reevaluate = _now
            await self._reevaluate_open_positions(db, positions=_positions)

        # A3: Dynamic Kelly graduation check (every 20 min)
        if _now - self._last_kelly_check >= 1200:  # 20 min
            self._last_kelly_check = _now
            await self._check_kelly_graduation(db)

        _t1 = time.monotonic()  # S110: after Phase A (pre-scan housekeeping)
        # S110: Retrain/accuracy checks wrapped in async function to run
        # concurrently with PandaScore + market fetch (OPT-4).  Internal
        # sequencing preserved — only the await points yield to other branches.
        async def _step_retrain_and_accuracy():
            # Step 0: Auto-retrain models in background (non-blocking)
            # Clean up completed background training tasks — retrieve exceptions to
            # prevent "Task exception was never retrieved" warnings.
            for game_key in list(self._bg_train_tasks):
                task = self._bg_train_tasks[game_key]
                if task.done():
                    if not task.cancelled():
                        exc = task.exception()
                        if exc:
                            logger.error("bg_train_task_failed", game=game_key,
                                         error=str(exc), task_name=task.get_name())
                    del self._bg_train_tasks[game_key]

            # Gather smart retrain trigger data (lightweight, degrades gracefully)
            _smart_brier: Dict[str, float] = {}
            _smart_row_count: Dict[str, int] = {}
            try:
                _acc_cache = await self._get_cached_rolling_accuracy(db)
                for _rg in ("lol", "cs2"):
                    _acc = _acc_cache.get(_rg)
                    if _acc and _acc["total"] >= 10:
                        _smart_brier[_rg] = _acc["brier_score"]
            except Exception as _e:
                logger.debug("esportsbot_smart_retrain_brier_failed", error=str(_e))
            try:
                from sqlalchemy import text as _sa_text
                async with db.get_session(timeout=15) as _sess:
                    for _rg in ("lol", "cs2"):
                        _cnt = await _sess.execute(
                            _sa_text("SELECT COUNT(*) FROM esports_training_data WHERE game = :game"),
                            {"game": _rg},
                        )
                        _smart_row_count[_rg] = int(_cnt.scalar() or 0)
            except Exception as _e:
                logger.debug("esportsbot_training_data_count_failed", error=str(_e))
            _lol_patch = ""
            if self._patch_drift:
                _lol_patch = self._patch_drift._known_patches.get("lol", "")

            for _retrain_game in ("lol", "cs2"):
                if (self._trainer
                        and self._trainer.needs_retrain(
                            _retrain_game,
                            current_brier=_smart_brier.get(_retrain_game),
                            current_row_count=_smart_row_count.get(_retrain_game),
                            current_patch=_lol_patch if _retrain_game == "lol" else None,
                            adwin_drift_detected=self._adwin_drift_detected,
                        )
                        and _retrain_game not in self._bg_train_tasks):
                    # Rebuild Glicko-2 trackers after training if this game
                    # has no tracker yet (e.g. LoL on first data collection)
                    _needs_glicko = self._glicko2_trackers.get(_retrain_game) is None
                    self._bg_train_tasks[_retrain_game] = asyncio.create_task(
                        self._train_in_background(_retrain_game, db, init_glicko=_needs_glicko),
                        name=f"retrain_{_retrain_game}",
                    )

            # E7: Cross-game XGBoost retrain (pools all 8 games)
            if (self._trainer
                    and self._trainer.needs_retrain(
                        "cross_game",
                        adwin_drift_detected=self._adwin_drift_detected,
                    )
                    and "cross_game" not in self._bg_train_tasks):
                _cg_task = asyncio.create_task(
                    self._train_in_background("cross_game", db),
                    name="retrain_cross_game",
                )

                def _on_retrain_done(t: asyncio.Task) -> None:
                    if t.cancelled():
                        logger.warning("esports_cross_game_retrain_cancelled")
                    elif t.exception():
                        logger.error("esports_cross_game_retrain_failed", error=str(t.exception()))

                _cg_task.add_done_callback(_on_retrain_done)
                self._bg_train_tasks["cross_game"] = _cg_task

            # Step 0a: Collect historical data for games missing Glicko-2 trackers (one-shot)
            for _game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                if (_game not in self._glicko2_trackers
                        and self._collection_attempted.get(_game, 0) < 3
                        and self._trainer
                        and _game not in self._bg_train_tasks):
                    self._collection_attempted[_game] = self._collection_attempted.get(_game, 0) + 1
                    self._bg_train_tasks[_game] = asyncio.create_task(
                        self._train_in_background(_game, db, init_glicko=True),
                        name=f"collect_{_game}",
                    )

            # Step 0b: Check rolling accuracy — auto-disable if below threshold
            # Guard: skip retrain trigger for halted games and games where
            # training data hasn't changed since last retrain (prevents thrash
            # loop where 7-day rolling accuracy stays low but retraining produces
            # identical model every 10s).
            min_acc = float(getattr(settings, "ESPORTS_MIN_ACCURACY_TO_TRADE", 0.52))
            try:
                _acc_all = await self._get_cached_rolling_accuracy(db)
                for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                    acc_data = _acc_all.get(game)
                    if acc_data and acc_data["total"] >= 30 and acc_data["accuracy"] < min_acc:
                        # Skip retrain for halted games — no point retraining
                        # a game we won't trade
                        if game in self._monitoring_halted_games:
                            continue
                        # Skip retrain if trainer already ran recently — the 2h
                        # cooldown in needs_retrain() is the right gate.  Popping
                        # _last_train_time bypasses it and causes a thrash loop
                        # where the same model is rebuilt every 10s.
                        if (self._trainer
                                and game in self._trainer._last_train_time
                                and (time.monotonic() - self._trainer._last_train_time[game]) < 1800):
                            continue
                        logger.warning(
                            "EsportsBot: accuracy below threshold — triggering retrain",
                            game=game,
                            accuracy=round(acc_data["accuracy"], 3),
                            threshold=min_acc,
                            brier=round(acc_data["brier_score"], 4),
                        )
            except Exception as exc:
                logger.warning("esportsbot_accuracy_check_failed", error=str(exc))

        # Steps 0-3 run in parallel — retrain/accuracy DB queries overlap
        # with PandaScore + market fetch (S110 OPT-4)
        async def _step_patch_drift():
            if self._patch_drift:
                try:
                    drift_status = await asyncio.wait_for(
                        self._patch_drift.check_all_games(), timeout=10.0
                    )
                    for game, status in drift_status.items():
                        if status.get("halted"):
                            logger.warning(
                                "EsportsBot: game halted (calibration failure)",
                                game=game,
                            )
                        # S154: Phi inflation on new patch detection.
                        # observation_mode=True + should_retrain=True = fresh patch.
                        # Only inflate once per patch (check _patch_phi_inflated).
                        if (status.get("observation_mode") and status.get("should_retrain")):
                            _inflated_key = f"{game}:{self._patch_drift._known_patches.get(game, '')}"
                            if _inflated_key not in getattr(self, "_patch_phi_inflated", set()):
                                if not hasattr(self, "_patch_phi_inflated"):
                                    self._patch_phi_inflated: set = set()
                                severity = self._patch_drift.get_patch_severity(game)
                                _phi_factors = {"hotfix": 1.0, "minor": 1.15, "major": 1.30}
                                _phi_factor = _phi_factors.get(severity, 1.0)
                                if _phi_factor > 1.0:
                                    _tracker = self._glicko2_trackers.get(game)
                                    if _tracker:
                                        _n = _tracker.inflate_phi_all_teams(factor=_phi_factor)
                                        self._patch_phi_inflated.add(_inflated_key)
                                        logger.warning(
                                            "esportsbot_patch_phi_inflated",
                                            game=game, severity=severity,
                                            factor=_phi_factor, teams_affected=_n,
                                        )
                except (asyncio.TimeoutError, Exception) as exc:
                    logger.debug("EsportsBot: patch drift check failed", error=str(exc))

        async def _step_get_markets():
            if self._market_service:
                return await self._market_service.get_tradeable_esports_markets()
            m = await self.base_engine.get_markets(active=True, limit=200)
            return self.base_engine.filter_markets_for_trading(m, categories=["esports"])

        _, _, _, esports_markets = await asyncio.gather(
            _step_retrain_and_accuracy(),
            _step_patch_drift(),
            self._refresh_live_matches(),
            _step_get_markets(),
        )
        _t2 = time.monotonic()  # S110: after Phase B (parallel gather)
        self._last_scan_markets = len(esports_markets) if esports_markets else 0
        if not esports_markets:
            return

        # Step 4: Analyze each market
        _opps = 0
        _trades = 0
        _skipped_position = 0
        _by_game: dict = {}  # markets per game for diagnostic
        # B4: Waterfall diagnostic counters (cross-pollinated from MirrorBot S48)
        self._wf = {"no_game": 0, "no_price": 0, "no_token": 0, "halted": 0,
                     "exposure_cap": 0, "observation": 0, "no_prediction": 0,
                     "low_edge": 0, "low_confidence": 0,
                     "passed": 0, "reentry_rejected": 0,
                     "exit_cooldown": 0, "max_entries": 0}
        self._hysteresis_hold_count = 0
        self._exposure_cap_logged: set = set()  # per-scan: games already logged for cap hit
        og = getattr(self.base_engine, "order_gateway", None)

        # Pre-compute per-game counts (sync, before parallel analysis)
        for market in esports_markets:
            _q = str(market.get("question", "")).lower()
            _g = self._detect_game(_q) or "other"
            _by_game[_g] = _by_game.get(_g, 0) + 1

        # S94: WS health check — fall back to scan trading if WS is stale
        _ws_threshold = float(getattr(settings, "ESPORTS_WS_STALE_THRESHOLD_S", 15.0))
        _ws_healthy = (self._last_ws_price_ts > 0
                       and (time.monotonic() - self._last_ws_price_ts) < _ws_threshold)
        if _ws_healthy != self._ws_trading_active:
            self._ws_trading_active = _ws_healthy
            if _ws_healthy:
                logger.info("esportsbot_ws_trading_resumed")
            else:
                logger.warning("esportsbot_ws_trading_fallback",
                               last_ws_age_s=round(time.monotonic() - self._last_ws_price_ts, 1))
        # S97: One-time WS diagnostic — log prediction_cache size so we can
        # tell if WS events are being filtered by the empty-cache early exit
        if self._scan_count == 1 and not _ws_healthy:
            _cache_sz = len(self._prediction_cache)
            logger.info("esportsbot_ws_diag",
                        prediction_cache_size=_cache_sz,
                        last_ws_ts=round(self._last_ws_price_ts, 1),
                        esports_markets=len(esports_markets))

        # Position re-entry config
        _reentry_min_edge = float(getattr(settings, "ESPORTS_REENTRY_MIN_EDGE", 0.12))
        _per_market_cap = float(getattr(settings, "ESPORTS_PER_MARKET_CAP", 600))

        # Parallel market analysis with bounded concurrency
        async def _analyze_one(m: Dict) -> tuple:
            """Analyze one market; returns (opps, trades, skips)."""
            async with self._analysis_semaphore:
                mid = str(m.get("id", ""))
                # S157: Flat post-exit cooldown (hysteresis handles churn, escalating removed)
                _exit_ts = self._recently_exited.get(mid)
                # S159: Post-exit elevated edge window — after cooldown expires,
                # require _reentry_min_edge for edge_gone/trailing exits within 2h.
                _post_exit_elevated = False
                if _exit_ts is not None:
                    _exit_reason = self._exit_reasons.get(mid, "")
                    _cooldown = (float(getattr(settings, "ESPORTS_EDGE_GONE_COOLDOWN_SECONDS", 1800.0))
                                 if _exit_reason in ("edge_gone", "trailing_edge")
                                 else float(getattr(settings, "ESPORTS_EXIT_COOLDOWN_SECONDS", 300.0)))
                    if time.monotonic() - _exit_ts < _cooldown:
                        self._wf["exit_cooldown"] += 1
                        return (0, 0, 1)
                    # Cooldown passed — check if still in post-exit elevated edge window
                    _post_exit_window = float(getattr(settings, "ESPORTS_POST_EXIT_EDGE_WINDOW_S", 7200.0))
                    if (_exit_reason in ("edge_gone", "trailing_edge")
                            and (time.monotonic() - _exit_ts) < _post_exit_window):
                        _post_exit_elevated = True
                # S157: Reason-specific execution failure cooldown
                _fail_entry = self._exec_fail_cooldown.get(mid)
                if _fail_entry is not None:
                    _fail_code, _fail_expiry = _fail_entry
                    if time.monotonic() < _fail_expiry:
                        self._wf["exec_fail_cooldown"] = self._wf.get("exec_fail_cooldown", 0) + 1
                        return (0, 0, 1)
                    else:
                        del self._exec_fail_cooldown[mid]
                # S109: Per-market rolling entry cap — hard backstop against churn
                _max_entries = int(getattr(settings, "ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW", 2))
                _window_s = float(getattr(settings, "ESPORTS_ENTRY_WINDOW_HOURS", 12.0)) * 3600
                _now_mono = time.monotonic()
                _recent = [t for t in self._market_entry_times.get(mid, []) if _now_mono - t < _window_s]
                if len(_recent) >= _max_entries:
                    self._wf["max_entries"] += 1
                    return (0, 0, 1)
                if og and mid and og.has_open_position(self.bot_name, mid):
                    # Position re-entry: allow if same direction + room under cap
                    _pos_key = f"{self.bot_name}:{mid}"
                    _pos = getattr(og, "_position_details", {}).get(_pos_key, {})
                    _existing_size = float(_pos.get("size", 0))
                    if _existing_size >= _per_market_cap:
                        self._wf["reentry_rejected"] += 1
                        return (0, 0, 1)  # Already at cap
                    # Let analyze_opportunity run; we'll validate direction + edge after
                    opp = await self.analyze_opportunity(m)
                    if opp:
                        _opp_side = opp.get("side", "")
                        _pos_side = _pos.get("side", "")
                        # Direction must match (no hedging against ourselves)
                        if _opp_side != _pos_side:
                            self._wf["reentry_rejected"] += 1
                            return (0, 0, 1)
                        # Edge must meet higher re-entry bar
                        if opp.get("edge", 0) < _reentry_min_edge:
                            self._wf["reentry_rejected"] += 1
                            return (0, 0, 1)
                        # Cap size at remaining room
                        _remaining = _per_market_cap - _existing_size
                        if _remaining <= 0:
                            self._wf["reentry_rejected"] += 1
                            return (0, 0, 1)
                        opp["max_size_override"] = _remaining
                        opp["is_reentry"] = True
                        logger.info(
                            "esportsbot_position_reentry", market_id=mid,
                            game=opp.get("game", ""), side=_opp_side,
                            existing_size=round(_existing_size, 2),
                            remaining_cap=round(_remaining, 2),
                            edge=round(opp.get("edge", 0), 4),
                        )
                        if not self._ws_trading_active:
                            async with self._trade_lock:
                                success = await self._execute_esports_trade(opp)
                            if success:
                                self._market_entry_times.setdefault(mid, []).append(time.monotonic())
                                await self._save_entry_count_to_redis(mid)
                            else:
                                self._set_exec_fail_cooldown(mid, getattr(self, "_last_fail_code", "unknown"))
                                await self._save_exec_fail_to_redis(mid)
                            return (1, 1 if success else 0, 0)
                        return (1, 0, 0)
                    return (0, 0, 1)
                opp = await self.analyze_opportunity(m)
                # S159: Post-exit elevated edge gate — reject if edge below reentry bar
                if opp and _post_exit_elevated:
                    _opp_edge = opp.get("edge", 0)
                    if _opp_edge < _reentry_min_edge:
                        self._wf["post_exit_edge"] = self._wf.get("post_exit_edge", 0) + 1
                        logger.info("esportsbot_post_exit_edge_gate",
                                    market_id=mid, edge=round(_opp_edge, 4),
                                    min_edge=_reentry_min_edge)
                        return (0, 0, 1)
                if opp and not self._ws_trading_active:
                    # Fallback: scan trades when WS is stale
                    async with self._trade_lock:
                        success = await self._execute_esports_trade(opp)
                    if success:
                        self._market_entry_times.setdefault(mid, []).append(time.monotonic())
                        await self._save_entry_count_to_redis(mid)
                    else:
                        # S157: Reason-specific cooldown after execution failure
                        self._set_exec_fail_cooldown(mid, getattr(self, "_last_fail_code", "unknown"))
                        await self._save_exec_fail_to_redis(mid)
                    logger.info(
                        "esportsbot_trade_attempt",
                        market_id=mid, game=opp.get("game", ""),
                        side=opp.get("side", ""), edge=opp.get("edge"),
                        confidence=round(opp.get("confidence", 0), 4),
                        success=success,
                    )
                    return (1, 1 if success else 0, 0)
                elif opp:
                    return (1, 0, 0)  # Opportunity found; WS will trade it
                return (0, 0, 0)

        results = await asyncio.gather(
            *[_analyze_one(m) for m in esports_markets],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.warning("esportsbot_scan_error", error=str(r), error_type=type(r).__name__)
            else:
                _opps += r[0]
                _trades += r[1]
                _skipped_position += r[2]
        self._last_scan_opportunities = _opps
        self._last_scan_trades = _trades
        _t3 = time.monotonic()  # S110: after Phase C (market analysis)

        # S109: Subscribe esports tokens to WS for real-time price updates.
        # _market_token_map is populated during analyze_opportunity; subscribe
        # any tokens not yet in the WS subscription set.
        _ws_mgr = getattr(self.base_engine, "websocket_manager", None)
        if _ws_mgr and self._market_token_map:
            _new_tokens = []
            for _mid, _tmap in self._market_token_map.items():
                for _role in ("yes", "no"):
                    _tid = _tmap.get(_role, "")
                    if _tid and f"price:{_tid}" not in _ws_mgr.subscriptions:
                        _new_tokens.append(_tid)
            if _new_tokens:
                try:
                    await _ws_mgr.subscribe_price_stream(_new_tokens)
                    logger.info("esportsbot_ws_subscribed",
                                new_tokens=len(_new_tokens),
                                total_esports_tokens=sum(
                                    1 for _tm in self._market_token_map.values()
                                    for _t in _tm.values() if _t))
                except Exception as _ws_exc:
                    logger.warning("esportsbot_ws_subscribe_failed", error=str(_ws_exc))

        # Diagnostic log every scan — helps diagnose low trade rate
        # B4: Include waterfall funnel for pipeline visibility
        _wf_nonzero = {k: v for k, v in self._wf.items() if v > 0}
        logger.info(
            "esportsbot_scan_summary",
            markets=len(esports_markets),
            markets_by_game=_by_game,
            skipped_has_position=_skipped_position,
            opportunities=_opps,
            trades=_trades,
            live_matches=len(self._live_matches),
            halted_games=list(self._monitoring_halted_games) or None,
            min_confidence=self._min_confidence,
            min_edge=self._min_edge,
            hysteresis_hold=self._hysteresis_hold_count,
            waterfall=_wf_nonzero or None,
            backfills_this_scan=self._backfill_calls_this_scan,
            ws_trading=self._ws_trading_active,
            timing_ms={
                "phase_a": round((_t1 - _t0) * 1000),
                "phase_b": round((_t2 - _t1) * 1000),
                "phase_c": round((_t3 - _t2) * 1000),
                "total": round((time.monotonic() - _t0) * 1000),
            },
        )

        # Series analysis — analyze live BO3/BO5 matches for conditional probability edge
        try:
            series_opps, series_trades = await self._series_scan()
            if series_opps or series_trades:
                logger.info(
                    "esports_series_scan",
                    active_series=len(self._active_series),
                    opportunities=series_opps,
                    trades=series_trades,
                )
        except Exception as exc:
            logger.debug("esports_series_scan_failed", error=str(exc))

        # P1.2: Backfill actual_outcome for settled esports paper_trades (every 20 min)
        # Non-financial bookkeeping — safe for fire-and-forget (updates prediction_log metadata)
        _bf_elapsed = _now - self._last_outcome_backfill
        _bf_ready = _bf_elapsed >= 1200
        _bf_db_ok = db is not None
        if _bf_ready and _bf_db_ok:
            self._last_outcome_backfill = _now
            logger.info("esportsbot_outcome_backfill_triggered")
            await self._safe_backfill_outcomes(db)

        # S159: Prune resolved markets from _entered_market_sides (every 30 min)
        if db and (_now - self._last_ems_prune) >= 1800:
            self._last_ems_prune = _now
            await self._prune_entered_market_sides(db)

    async def _prune_entered_market_sides(self, db) -> None:
        """Remove (market_id, side) pairs for resolved markets.

        Resolved markets can't be traded again, so the opposing-side guard
        is useless for them.  Active markets with closed positions KEEP
        their guard — prevents re-entering the opposite side within a
        market's lifetime.
        """
        if not self._entered_market_sides:
            return
        try:
            from sqlalchemy import text as _p_text
            _market_ids = list({mid for mid, _ in self._entered_market_sides})
            async with db.get_session(timeout=15) as _p_sess:
                _rows = await _p_sess.execute(
                    _p_text(
                        "SELECT DISTINCT market_id FROM traded_markets "
                        "WHERE market_id = ANY(:ids) AND resolved_at IS NOT NULL"
                    ),
                    {"ids": _market_ids},
                )
                _resolved = {r[0] for r in _rows.fetchall()}
            if _resolved:
                _before = len(self._entered_market_sides)
                self._entered_market_sides = {
                    (mid, side) for mid, side in self._entered_market_sides
                    if mid not in _resolved
                }
                _pruned = _before - len(self._entered_market_sides)
                if _pruned:
                    logger.info("esports_ems_pruned",
                                pruned=_pruned, remaining=len(self._entered_market_sides))
        except Exception as _exc:
            logger.debug("esports_ems_prune_failed", error=str(_exc))

    async def _safe_backfill_outcomes(self, db) -> None:
        """Background outcome backfill — non-blocking."""
        try:
            await self._backfill_esports_outcomes(db)
        except Exception as _exc:
            logger.warning("esportsbot_outcome_backfill_failed", error=str(_exc))

    async def _restore_exposure_from_db(self, db) -> None:
        """Seed _game_exposure from daily_counters on startup.

        Reads the write-through daily_counters table (migration 036) — the authoritative
        source for per-game exposure since every _execute_esports_trade() call writes
        through immediately. counter_date = CURRENT_DATE means this auto-resets at UTC midnight.

        Note: _tournament_exposure is NOT restored — tournament caps span multiple UTC days
        and daily_counters would reset them at midnight. Accept tournament exposure resets.
        """
        if self._exposure_restored:
            return
        self._exposure_restored = True
        if db is None or not getattr(db, "session_factory", None):
            return
        try:
            counters = await _restore_daily(db, "EsportsBot")
            for name, value in counters.items():
                if name.startswith("game_"):
                    game_key = name[5:]  # "game_cs2" → "cs2"
                    self._game_exposure[game_key] = value
            logger.info(
                "esports_exposure_restored",
                games=dict(self._game_exposure),
            )
        except Exception as exc:
            logger.warning("esports_restore_exposure_failed", error=str(exc))

    async def _restore_market_game_from_db(self, db) -> None:
        """S125: Restore _market_game from ENTRY trade_events for open positions.

        Ensures EXIT events can tag game even after restart (outlives
        _prediction_cache 1h TTL and in-memory _market_game dict).
        """
        if self._market_game_restored:
            return
        self._market_game_restored = True
        if db is None or not getattr(db, "session_factory", None):
            return
        try:
            from sqlalchemy import text as _sa_text
            async with db.get_session(timeout=15) as session:
                result = await session.execute(
                    _sa_text(
                        "SELECT DISTINCT ON (te.market_id) te.market_id, "
                        "te.event_data->>'game' AS game "
                        "FROM trade_events te "
                        "JOIN positions p ON te.market_id = p.market_id "
                        "WHERE te.bot_name IN ('EsportsBot','EsportsLiveBot','EsportsSeriesBot') "
                        "AND te.event_type = 'ENTRY' "
                        "AND p.status = 'open' "
                        "AND (p.bot_id IN ('EsportsBot','EsportsLiveBot','EsportsSeriesBot') "
                        "     OR p.source_bot IN ('EsportsBot','EsportsLiveBot','EsportsSeriesBot')) "
                        "AND (te.event_data->>'game') IS NOT NULL "
                        "AND (te.event_data->>'game') != '' "
                        "ORDER BY te.market_id, te.event_time DESC"
                    ),
                )
                rows = result.fetchall()
                restored = 0
                for row in rows:
                    mid, game = row[0], row[1]
                    if mid and game:
                        self._market_game[mid] = game
                        restored += 1
                if restored:
                    logger.info("esports_market_game_restored", count=restored)
        except Exception as exc:
            logger.warning("esports_market_game_restore_failed", error=str(exc))

    async def _restore_daily_pnl_from_db(self, db) -> None:
        """A1: Restore today's realized P&L from trade_events on startup."""
        if db is None:
            return
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._daily_pnl_date == today_str:
                return  # Already restored for today
            self._daily_pnl = 0.0
            self._daily_pnl_date = today_str
            self._drawdown_halted = False
            today_start = datetime.strptime(today_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            async with db.get_session(timeout=15) as session:
                from sqlalchemy import text
                result = await session.execute(text("""
                    SELECT COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0.0)
                    FROM trade_events
                    WHERE bot_name IN ('EsportsBot', 'EsportsLiveBot', 'EsportsSeriesBot')
                      AND event_type IN ('EXIT', 'RESOLUTION')
                      AND realized_pnl IS NOT NULL
                      AND event_time >= :today_start
                """), {"today_start": today_start})
                row = result.fetchone()
                if row and row[0] is not None:
                    self._daily_pnl = float(row[0])
            if self._daily_pnl != 0.0:
                logger.info("esports_daily_pnl_restored", pnl=round(self._daily_pnl, 2))
        except Exception as exc:
            logger.debug("esports_daily_pnl_restore_failed", error=str(exc))

    async def _save_exit_cooldown_to_redis(self, market_id: str, reason: str = "") -> None:
        """S109: Persist exit cooldown to Redis so it survives restarts.
        S157: Simplified — flat cooldown per reason (escalating removed, hysteresis handles churn)."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            ttl = (int(float(getattr(settings, "ESPORTS_EDGE_GONE_COOLDOWN_SECONDS", 1800.0)))
                   if reason in ("edge_gone", "trailing_edge")
                   else int(float(getattr(settings, "ESPORTS_EXIT_COOLDOWN_SECONDS", 300.0))))
            expire_at = time.time() + ttl
            await cache.set(f"esportsbot:exit:{market_id}", expire_at, ttl=ttl)
            if reason:
                await cache.set(f"esportsbot:exit_reason:{market_id}", reason, ttl=ttl)
        except Exception as exc:
            logger.debug("esportsbot_redis_exit_save_failed", error=str(exc))

    async def _save_entry_count_to_redis(self, market_id: str) -> None:
        """S143: Persist per-market entry count to Redis so it survives restarts.
        Prevents the 2-per-12h cap from resetting on every bot restart."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            _window_h = float(getattr(settings, "ESPORTS_ENTRY_WINDOW_HOURS", 12.0))
            _ttl = int(_window_h * 3600)
            _key = f"esportsbot:entry_count:{market_id}"
            raw = await cache.get(_key)
            _count = int(raw) + 1 if raw is not None else 1
            await cache.set(_key, _count, ttl=_ttl)
        except Exception as exc:
            logger.debug("esportsbot_redis_entry_count_save_failed", error=str(exc))

    async def _restore_entry_counts_from_redis(self) -> None:
        """S150: Reload per-market entry counts from trade_events DB (ground truth).

        S143 used Redis as sole source, but SIGKILL restarts lose Redis writes.
        Now queries actual ENTRY events in the rolling window.  Each entry's
        real event_time is converted to a synthetic monotonic offset so the
        in-memory window ages them out correctly.

        Falls back to Redis if the DB query fails (preserves S143 behaviour).
        """
        _window_h = float(getattr(settings, "ESPORTS_ENTRY_WINDOW_HOURS", 12.0))
        _window_s = _window_h * 3600
        _now_mono = time.monotonic()
        _now_utc = datetime.now(timezone.utc)

        # Primary: DB ground truth
        try:
            db = getattr(self.base_engine, "db", None)
            if db and db.session_factory:
                from sqlalchemy import text as _ec_text
                async with db.get_session(timeout=15) as _ec_sess:
                    _ec_rows = await _ec_sess.execute(
                        _ec_text(
                            "SELECT market_id, event_time "
                            "FROM trade_events "
                            "WHERE bot_name = 'EsportsBot' "
                            "  AND event_type = 'ENTRY' "
                            "  AND event_time >= NOW() - INTERVAL '1 hour' * :hours "
                            "ORDER BY event_time"
                        ),
                        {"hours": _window_h},
                    )
                    count = 0
                    for _row in _ec_rows.fetchall():
                        _mid = _row[0]
                        _et = _row[1]
                        # Convert real event_time to monotonic offset
                        if _et is not None:
                            if isinstance(_et, str):
                                _et = datetime.fromisoformat(_et)
                            if _et.tzinfo is None:
                                _et = _et.replace(tzinfo=timezone.utc)
                            _age_s = (_now_utc - _et).total_seconds()
                            _mono_ts = _now_mono - _age_s
                        else:
                            _mono_ts = _now_mono
                        self._market_entry_times.setdefault(_mid, []).append(_mono_ts)
                        count += 1
                    if count:
                        logger.info("esportsbot_entry_counts_restored",
                                    source="trade_events", total_entries=count,
                                    markets=len(self._market_entry_times))
                    return  # Success — skip Redis fallback
        except Exception as _db_exc:
            logger.warning("esportsbot_entry_counts_db_failed", error=str(_db_exc))

        # Fallback: Redis (S143 original path)
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            keys = await cache.redis.keys("esportsbot:entry_count:*")
            count = 0
            for key in keys:
                raw = await cache.get(key)
                if raw is None:
                    continue
                _n = int(raw)
                mid = key.split("esportsbot:entry_count:", 1)[-1]
                # Place synthetic timestamps at 'now' — they'll age out over the window
                self._market_entry_times[mid] = [_now_mono] * _n
                count += _n
            if count:
                logger.info("esportsbot_entry_counts_restored",
                            source="redis_fallback", total_entries=count, markets=len(keys))
        except Exception as exc:
            logger.warning("esportsbot_restore_entry_counts_failed", error=str(exc))

    async def _restore_exit_cooldowns_from_redis(self) -> None:
        """S109: Reload exit cooldowns from Redis on startup.
        S138: Also restore exit reasons for extended edge_gone cooldowns.
        S155: Also restore consecutive edge exit counters."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            keys = await cache.redis.keys("esportsbot:exit:*")
            now_wall = time.time()
            now_mono = time.monotonic()
            count = 0
            for key in keys:
                # S138: Skip exit_reason keys — they're metadata, not cooldowns
                if ":exit_reason:" in key or ":consec_edge:" in key:
                    continue
                raw = await cache.get(key)
                if raw is None:
                    continue
                expire_at = float(raw)
                if expire_at <= now_wall:
                    continue  # cooldown already expired
                mid = key.split("esportsbot:exit:", 1)[-1]
                # S138: Restore exit reason to determine correct cooldown duration
                # S143: trailing_edge gets same extended cooldown as edge_gone
                _reason_raw = await cache.get(f"esportsbot:exit_reason:{mid}")
                _reason = str(_reason_raw) if _reason_raw else ""
                # S157: Flat cooldown per reason (escalating removed — hysteresis handles churn)
                cooldown = (float(getattr(settings, "ESPORTS_EDGE_GONE_COOLDOWN_SECONDS", 1800.0))
                            if _reason in ("edge_gone", "trailing_edge")
                            else float(getattr(settings, "ESPORTS_EXIT_COOLDOWN_SECONDS", 300.0)))
                elapsed = cooldown - (expire_at - now_wall)
                self._recently_exited[mid] = now_mono - elapsed
                self._exit_reasons[mid] = _reason
                count += 1
            if count:
                logger.info("esportsbot_exit_cooldowns_restored", count=count)
            # S157: Clean up stale consec_edge Redis keys from removed escalating cooldown
            try:
                _stale_keys = await cache.redis.keys("esportsbot:consec_edge:*")
                if _stale_keys:
                    await cache.redis.delete(*_stale_keys)
                    logger.info("esportsbot_stale_consec_edge_cleaned", count=len(_stale_keys))
            except Exception:
                pass  # Non-critical cleanup
        except Exception as exc:
            logger.warning("esportsbot_restore_exits_failed", error=str(exc))

    async def _save_exec_fail_to_redis(self, market_id: str) -> None:
        """S156: Persist exec failure cooldown to Redis so it survives restarts.
        S158: Use reason-specific TTL from in-memory cooldown instead of generic 300s."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            _entry = self._exec_fail_cooldown.get(market_id)
            _cd = int(_entry[1] - time.monotonic()) if _entry and isinstance(_entry, tuple) else 120
            _cd = max(1, _cd)  # ensure positive TTL
            await cache.set(f"esportsbot:exec_fail:{market_id}", "1", ttl=_cd)
        except Exception as exc:
            logger.debug("esportsbot_exec_fail_redis_save_failed", error=str(exc))

    async def _restore_exec_fail_from_redis(self) -> None:
        """S156: Reload exec failure cooldowns from Redis on startup."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            keys = await cache.redis.keys("esportsbot:exec_fail:*")
            count = 0
            for key in (keys or []):
                key_str = key.decode() if isinstance(key, bytes) else str(key)
                mid = key_str.split("esportsbot:exec_fail:")[-1]
                ttl = await cache.redis.ttl(key_str)
                if ttl and ttl > 0:
                    # S157: Restore with unknown fail_code (Redis doesn't store it)
                    self._exec_fail_cooldown[mid] = ("unknown", time.monotonic() + ttl)
                    count += 1
            if count:
                logger.info("esportsbot_exec_fail_restored", count=count)
        except Exception as exc:
            logger.debug("esportsbot_exec_fail_redis_restore_failed", error=str(exc))

    def _check_daily_loss_limit(self) -> bool:
        """A1+A8: Return True if trading should be blocked (loss limit or drawdown halt)."""
        if self._daily_pnl <= -self._daily_loss_limit:
            return True
        capital = float(getattr(settings, "ESPORTS_TOTAL_CAPITAL", 5000.0))
        if capital > 0 and self._daily_pnl < 0:
            drawdown_pct = abs(self._daily_pnl) / capital
            halt_pct = float(getattr(settings, "ESPORTS_DRAWDOWN_HALT_PCT", 0.20))
            if drawdown_pct >= halt_pct:
                if not self._drawdown_halted:
                    self._drawdown_halted = True
                    logger.warning("esportsbot_drawdown_halt",
                                   daily_pnl=round(self._daily_pnl, 2),
                                   drawdown_pct=round(drawdown_pct, 4))
                return True
        return False

    def _get_drawdown_kelly_factor(self) -> float:
        """S136 Phase 8C: 5-step drawdown tiers with hysteresis.

        Replaces linear interpolation with discrete step tiers for clearer
        risk boundaries.  Hysteresis prevents oscillation at tier edges:
        once a lower tier is entered, the drawdown must recover 50%
        before restoring the prior (higher) tier.
        """
        capital = float(getattr(settings, "ESPORTS_TOTAL_CAPITAL", 20000.0))
        if capital <= 0:
            return 1.0
        dd_pct = abs(self._daily_pnl) / capital if self._daily_pnl < 0 else 0.0

        # Step tiers
        if dd_pct > 0.25:
            factor = 0.0  # halt
        elif dd_pct > 0.20:
            factor = 0.10
        elif dd_pct > 0.15:
            factor = 0.25
        elif dd_pct > 0.10:
            factor = 0.50
        elif dd_pct > 0.05:
            factor = 0.75
        else:
            factor = 1.0

        # Hysteresis: require 50% recovery before restoring prior tier
        # S158: _dd_tier_trigger_pct records the dd_pct that caused the LAST tier downgrade.
        # Only set on downgrade (factor < _prev_factor), not every call, so the recovery
        # comparison stays stable until the tier actually changes.
        _prev_factor = getattr(self, '_last_dd_factor', 1.0)
        _trigger_dd = getattr(self, '_dd_tier_trigger_pct', 0.0)
        if factor > _prev_factor:
            # Recovering — only restore higher tier if drawdown recovered 50% from trigger level
            if _trigger_dd > 0 and dd_pct > _trigger_dd * 0.5:
                factor = _prev_factor  # stay at current tier until sufficient recovery
        if factor < _prev_factor:
            # Tier degraded — record the drawdown level that caused this downgrade
            self._dd_tier_trigger_pct = dd_pct
        self._last_dd_factor = factor

        return round(factor, 3)

    # S157: _get_adaptive_min_edge removed — replaced by edge hysteresis (min_edge_entry/hold)

    # S157: Cooldown duration by paper_trading fail_code
    _FAIL_COOLDOWN_S = {
        "book_depleted": 60,       # Market may refill quickly
        "partial_fill": 60,        # Same as book_depleted
        "slippage": 300,           # Wide spread needs time to tighten
        "insufficient_cash": 86400,  # No retry until daily reset
        "insufficient_position": 86400,  # Position state won't change mid-scan
        "duplicate": 0,            # Idempotency working, not a failure
    }
    _FAIL_COOLDOWN_DEFAULT = 120   # Unknown fail_code fallback

    def _set_exec_fail_cooldown(self, market_id: str, fail_code: str) -> None:
        """S157: Set reason-specific execution failure cooldown."""
        cd = self._FAIL_COOLDOWN_S.get(fail_code, self._FAIL_COOLDOWN_DEFAULT)
        if cd <= 0:
            return  # duplicate = no cooldown
        if fail_code not in self._FAIL_COOLDOWN_S:
            logger.warning("esportsbot_exec_fail_unknown_code", market_id=market_id, fail_code=fail_code)
        self._exec_fail_cooldown[market_id] = (fail_code, time.monotonic() + cd)

    def _get_exposure_caps(self) -> Dict[str, float]:
        """S136 Phase 8A: Compute exposure caps -- percentage-based when enabled.

        Returns dict with keys: per_trade, per_market, per_team, per_game,
        per_tournament, total_portfolio.
        """
        if getattr(settings, "ESPORTS_PCT_CAPS_ENABLED", False):
            _cap_capital = float(getattr(settings, "ESPORTS_TOTAL_CAPITAL", 20000.0))
            return {
                "per_trade": _cap_capital * float(getattr(settings, "ESPORTS_PCT_PER_TRADE", 0.015)),
                "per_market": _cap_capital * float(getattr(settings, "ESPORTS_PCT_PER_MARKET", 0.03)),
                "per_team": _cap_capital * float(getattr(settings, "ESPORTS_PCT_PER_TEAM", 0.03)),
                "per_game": _cap_capital * float(getattr(settings, "ESPORTS_PCT_PER_GAME", 0.04)),
                "per_tournament": _cap_capital * float(getattr(settings, "ESPORTS_PCT_PER_TOURNAMENT", 0.12)),
                "total_portfolio": _cap_capital * float(getattr(settings, "ESPORTS_PCT_TOTAL_PORTFOLIO", 0.60)),
            }
        # Fallback: absolute caps from env
        return {
            "per_trade": float(getattr(settings, "ESPORTS_MAX_BET_USD", 300.0)),
            "per_market": float(getattr(settings, "ESPORTS_PER_MARKET_CAP", 600.0)),
            "per_team": float(getattr(settings, "ESPORTS_MAX_TEAM_EXPOSURE", 2000.0)),
            "per_game": float(getattr(settings, "ESPORTS_MAX_GAME_EXPOSURE", 5000.0)),
            "per_tournament": float(getattr(settings, "ESPORTS_MAX_TOURNAMENT_EXPOSURE", 8000.0)),
            "total_portfolio": float(getattr(settings, "ESPORTS_MAX_TOTAL_EXPOSURE_USD", 15000.0)),
        }

    async def _check_and_execute_exits(self, db, positions=None) -> None:
        """B1: Stop-loss + max hold time exits for open EsportsBot positions.

        S162: EsportsBot is in PM_EXCLUDE_BOTS, so position_manager does NOT
        update current_price in the positions table. This method fetches fresh
        prices directly from market_prices_latest (single bulk query) instead
        of relying on the stale current_price column. Without this, current_price
        stays at entry_price forever and no stop-loss/trailing-edge exits fire.
        """
        if db is None:
            return
        if positions is None:
            try:
                positions = await db.get_open_positions_for_bot(self.bot_name)
            except Exception as exc:
                logger.debug("esportsbot_exit_check_failed", error=str(exc))
                return
        if not positions:
            return

        # S162: Fetch fresh prices from market_prices_latest for all open positions.
        # One bulk query — no per-position overhead, no position_manager dependency.
        _token_ids = [str(p.get("token_id", "")) for p in positions if p.get("token_id")]
        _fresh_prices: dict = {}
        if _token_ids:
            try:
                from sqlalchemy import text as _sa_text
                async with db.get_session() as _ps:
                    _pr = await _ps.execute(
                        _sa_text("SELECT token_id, price FROM market_prices_latest WHERE token_id = ANY(:tids)"),
                        {"tids": _token_ids},
                    )
                    for _row in _pr.fetchall():
                        if _row[1] is not None and float(_row[1]) > 0:
                            _fresh_prices[str(_row[0])] = float(_row[1])
            except Exception as _pe:
                logger.warning("esportsbot_exit_price_fetch_failed", error=str(_pe))
                # Fall through — use stale current_price from positions table as fallback

        stop_pct = float(getattr(settings, "ESPORTS_STOP_LOSS_PCT", 0.20))
        max_hold_h = float(getattr(settings, "ESPORTS_MAX_HOLD_HOURS", 72))
        now_utc = datetime.now(timezone.utc)
        positions_to_close: list = []

        for pos in positions:
            mid = pos.get("market_id", "")
            entry = float(pos.get("entry_price", 0.5) or 0.5)
            # S162: Use fresh price from market_prices_latest, fall back to DB current_price
            _tid = str(pos.get("token_id", ""))
            current = _fresh_prices.get(_tid, float(pos.get("current_price", entry) or entry))
            side = (pos.get("side") or "YES").upper()
            size = float(pos.get("size", 0) or 0)
            token_id = pos.get("token_id", "")

            if size <= 0 or not token_id:
                continue

            # S134 Fix A: Widen dead-market guard — skip exit if book is likely empty.
            # Esports markets are binary (match winner). A price of <0.10 on an entry
            # of 0.20+ means the orderbook is empty, not a real price move.
            # Let resolution handle it (will pay 0.0 or 1.0).
            if current < 0.10 and entry >= 0.20:
                logger.debug("esportsbot_exit_skip_dead_market", market_id=mid,
                             entry=round(entry, 4), current=round(current, 4))
                continue

            # Prices are token-specific — (current - entry) is correct for BOTH YES and NO
            pnl_pct = (current - entry) / max(entry, 1e-6)

            # S136 Phase 3A: Edge-based exit evaluation
            _entry_model_prob = None
            _entry_edge = None
            _remaining_edge = None
            _market_type = "match_winner"
            _edge_cache = self._entry_edge_cache.get(mid, {})
            if _edge_cache:
                _entry_model_prob = _edge_cache.get("model_prob")
                _entry_edge = _edge_cache.get("edge")
                _market_type = _edge_cache.get("market_type", "match_winner")

            if _entry_model_prob is not None:
                # S136 fix: entry_model_prob is P(YES wins). For YES positions,
                # remaining edge = model_prob - current_price. For NO positions,
                # remaining edge = current_price - model_prob (NO profits when
                # YES price falls below model's prediction).
                if side == "NO":
                    _remaining_edge = (1.0 - _entry_model_prob) - current - 0.0075
                else:
                    _remaining_edge = _entry_model_prob - current - 0.0075

                # Track peak edge for trailing stop
                _peak_key = f"_peak_edge_{mid}"
                _current_edge_val = max(0.0, _remaining_edge)
                _prev_peak = self._edge_peaks.get(_peak_key, _entry_edge or 0.0)
                self._edge_peaks[_peak_key] = max(_prev_peak, _current_edge_val)
                _peak = self._edge_peaks[_peak_key]

                # S150: Minimum hold time before edge-based exits can fire.
                # opened_at is always datetime (tz-naive UTC) from SQLAlchemy
                # NaiveUTCDateTime, or None.  No string parsing needed.
                _min_hold_s = float(getattr(settings, "ESPORTS_MIN_HOLD_MINUTES", 10.0)) * 60
                _opened_at = pos.get("opened_at")
                if _opened_at is None:
                    _hold_ok = False
                else:
                    _hold_ok = (now_utc - _opened_at.replace(tzinfo=timezone.utc)).total_seconds() >= _min_hold_s

                # S157: Full exit when edge below hold threshold (hysteresis lower band)
                if _remaining_edge <= self._min_edge_hold:
                    if not _hold_ok:
                        logger.info("esportsbot_edge_exit_hold_gate",
                                     market_id=mid, opened_at=str(_opened_at),
                                     remaining_edge=round(_remaining_edge, 4))
                        continue  # S158: don't fall through to trailing edge — preserves stop-loss path
                    else:
                        logger.info("esportsbot_edge_exit_full", market_id=mid,
                                    remaining_edge=round(_remaining_edge, 4),
                                    entry_model_prob=round(_entry_model_prob, 4),
                                    current_price=round(current, 4),
                                    hold_threshold=self._min_edge_hold)
                        positions_to_close.append((pos, "edge_gone"))
                        continue

                # Trailing edge stop: edge dropped 50% from peak (peak was meaningful)
                if _peak > self._min_edge_hold and _remaining_edge < _peak * 0.5:
                    if not _hold_ok:
                        logger.info("esportsbot_trailing_edge_hold_gate",
                                     market_id=mid, opened_at=str(_opened_at),
                                     remaining_edge=round(_remaining_edge, 4))
                    else:
                        logger.info("esportsbot_trailing_edge_exit", market_id=mid,
                                    remaining_edge=round(_remaining_edge, 4),
                                    peak_edge=round(_peak, 4))
                        positions_to_close.append((pos, "trailing_edge"))
                    continue

            # S134 Fix B + S136 Phase 3A: Stop-loss with edge override
            # Floor price guard: sub-$0.10 = dead book, let resolution handle it.
            # S136: Tightened to -20% AND require remaining_edge < 0.03
            if pnl_pct <= -stop_pct:
                if current < 0.10:
                    logger.info("esportsbot_stop_loss_floor_skip", market_id=mid,
                                pnl_pct=f"{pnl_pct:.2%}", side=side,
                                entry=round(entry, 4), current=round(current, 4))
                    continue
                # S136: Only trigger stop-loss if edge is also gone (or unknown)
                if _remaining_edge is not None and _remaining_edge >= 0.03:
                    logger.debug("esportsbot_stop_loss_edge_override", market_id=mid,
                                 pnl_pct=f"{pnl_pct:.2%}",
                                 remaining_edge=round(_remaining_edge, 4))
                    continue
                logger.info("esportsbot_stop_loss", market_id=mid,
                            pnl_pct=f"{pnl_pct:.2%}", side=side,
                            entry=round(entry, 4), current=round(current, 4),
                            remaining_edge=round(_remaining_edge, 4) if _remaining_edge is not None else None)
                positions_to_close.append((pos, "stop_loss"))
                continue

            # Max hold time check using DB opened_at
            # S136 Phase 3A: Market-type-specific max hold hours
            # S154: Market-type-specific max hold hours.
            # match_winner: 12h, map_winner: 8h (resolves in 1-2h typically),
            # tournament_winner: 96h, default: ESPORTS_MAX_HOLD_HOURS.
            # LoL map_winner was 55% of entries and using 24h default —
            # tightened to force more profitable exits vs losing resolutions.
            if _market_type == "match_winner":
                _effective_max_hold = 12.0
            elif _market_type == "map_winner":
                _effective_max_hold = 8.0
            elif _market_type == "tournament_winner":
                _effective_max_hold = 96.0
            else:
                _effective_max_hold = max_hold_h
            opened_at = pos.get("opened_at")
            if opened_at is not None:
                try:
                    if isinstance(opened_at, str):
                        opened_at = datetime.fromisoformat(opened_at)
                    if opened_at.tzinfo is None:
                        opened_at = opened_at.replace(tzinfo=timezone.utc)
                    hold_h = (now_utc - opened_at).total_seconds() / 3600
                    if hold_h >= _effective_max_hold:
                        logger.info("esportsbot_max_hold_exit", market_id=mid,
                                    hold_h=f"{hold_h:.1f}h",
                                    market_type=_market_type,
                                    max_hold_h=_effective_max_hold)
                        positions_to_close.append((pos, "max_hold"))
                except Exception as exc:
                    logger.warning("esportsbot_max_hold_parse_error", market_id=mid, error=str(exc))

        # Execute exits via SELL-side order using the SAME token_id
        # (selling back the token we hold, not buying the opposite side)
        for pos, reason in positions_to_close:
            mid = pos["market_id"]
            try:
                side = (pos.get("side") or "YES").upper()
                size = float(pos.get("size", 0) or 0)
                token_id = pos.get("token_id", "")
                current = float(pos.get("current_price", 0.5) or 0.5)
                entry = float(pos.get("entry_price", 0.5) or 0.5)
                if size <= 0 or not token_id:
                    continue
                # Exit by SELL order — bypasses risk_manager confidence check (line 448 order_gateway.py)
                # S121: Carry game tag into EXIT event_data for per-game P&L tracking
                _exit_game = self._market_game.get(mid, "") or self._prediction_cache.get(mid, {}).get("game", "")
                _exit_result = await self.place_order(
                    market_id=mid, token_id=token_id, side="SELL",
                    size=size, price=current, confidence=0.0,
                    event_data={"game": _exit_game, "exit_reason": reason},
                )
                # If SELL failed (e.g. paper_trading_engine lost position on restart),
                # close the orphaned DB position directly to stop infinite stop-loss loop.
                if not _exit_result.get("success"):
                    logger.warning("esportsbot_exit_sell_failed", market_id=mid,
                                   error=_exit_result.get("error", "unknown"),
                                   reason=reason, size=round(size, 2))
                    _db = getattr(self.base_engine, "db", None)
                    if _db is not None:
                        try:
                            from sqlalchemy import text as _sa_text
                            async with _db.get_session(timeout=15) as _sess:
                                await _sess.execute(
                                    _sa_text("""
                                        UPDATE positions SET status = 'closed'
                                        WHERE market_id = :mid
                                          AND (bot_id = :bot_name OR source_bot = :bot_name)
                                          AND status = 'open'
                                    """),
                                    {"mid": mid, "bot_name": self.bot_name},
                                )
                                await _sess.commit()
                            # Also clean in-memory exposure tracker so position doesn't count
                            _og = getattr(self.base_engine, "order_gateway", None)
                            if _og is not None:
                                _og._track_position_close(self.bot_name, mid)
                            logger.info("esportsbot_orphan_position_closed", market_id=mid)
                        except Exception as _close_err:
                            logger.warning("esportsbot_orphan_close_failed", market_id=mid,
                                           error=str(_close_err))
                    self._market_game.pop(mid, None)
                    # S136: Clean up edge tracking for exited position
                    self._entry_edge_cache.pop(mid, None)
                    self._edge_peaks.pop(f"_peak_edge_{mid}", None)
                    # S110: Set cooldown even on failed exit — prevents churn re-entry
                    self._recently_exited[mid] = time.monotonic()
                    self._exit_reasons[mid] = reason  # S138: track for extended cooldown
                    self._prediction_cache.pop(mid, None)
                    await self._save_exit_cooldown_to_redis(mid, reason=reason)
                    continue  # Skip exposure decrement — position was orphaned
                # B3: Decrement game exposure (USD) on exit
                # Primary: _market_game (populated on entry, survives cache expiry)
                # Fallback: prediction_cache (1h TTL, may be stale)
                game = self._market_game.get(mid, "")
                if not game:
                    game = self._prediction_cache.get(mid, {}).get("game", "")
                if game and game in self._game_exposure:
                    # S103: Use entry_price * size (USD) to match entry-time increment
                    _exit_cost = entry * size
                    # S156: Decrement under _trade_lock to prevent race with concurrent entry
                    async with self._trade_lock:
                        self._game_exposure[game] = max(0.0, self._game_exposure.get(game, 0.0) - _exit_cost)
                    # Write-through decrement so daily_counters stays accurate across restarts
                    _db = getattr(self.base_engine, "db", None)
                    if _db is not None:
                        try:
                            await _inc_daily(_db, "EsportsBot", f"game_{game}", -_exit_cost)
                        except Exception as _exp_exc:
                            logger.warning("esportsbot_exit_exposure_write_failed", error=str(_exp_exc), game=game)
                # Clean up market→game mapping for exited position
                self._market_game.pop(mid, None)
                # S136: Clean up edge tracking for exited position
                self._entry_edge_cache.pop(mid, None)
                self._edge_peaks.pop(f"_peak_edge_{mid}", None)
                logger.info("esportsbot_exit_executed", market_id=mid, reason=reason,
                            exit_side="SELL", size=round(size, 2), game=game)
                # S109: Set cooldown + invalidate prediction cache to prevent churn
                self._recently_exited[mid] = time.monotonic()
                self._exit_reasons[mid] = reason  # S138: track for extended cooldown
                self._prediction_cache.pop(mid, None)
                await self._save_exit_cooldown_to_redis(mid, reason=reason)
            except Exception as exc:
                logger.warning("esportsbot_exit_failed", market_id=mid, error=str(exc))
                # S110: Set cooldown even on unexpected exit failure — prevents churn
                self._recently_exited[mid] = time.monotonic()
                self._exit_reasons[mid] = reason  # S138: track for extended cooldown
                self._prediction_cache.pop(mid, None)
                try:
                    await self._save_exit_cooldown_to_redis(mid, reason=reason)
                except Exception:
                    pass  # Best-effort; in-memory cooldown is primary

    async def _reevaluate_open_positions(self, db, positions=None) -> None:
        """A2: Re-evaluate open positions with fresh Glicko-2 predictions.

        Queries DB for current_price (updated every 10s by position_manager),
        compares against cached model prediction to detect edge collapse.
        Informational logging only — exits handled by stop-loss (B1).
        """
        if db is None:
            return
        if positions is None:
            try:
                positions = await db.get_open_positions_for_bot(self.bot_name)
            except Exception:
                return
        if not positions:
            return

        for pos in positions:
            mid = pos.get("market_id", "")
            cached = self._prediction_cache.get(mid)
            if not cached:
                continue

            side = (pos.get("side") or "YES").upper()
            current_price = float(pos.get("current_price", 0.5) or 0.5)
            model_prob = cached.get("prob", 0.5)

            # Check if edge has collapsed or flipped
            if side == "YES":
                current_edge = model_prob - current_price
            else:
                # current_price is NO token price (from position token_id lookup).
                # P(NO) - NO_price = remaining edge for NO position.
                current_edge = (1.0 - model_prob) - current_price

            if current_edge <= 0:
                logger.debug("esportsbot_edge_collapsed", market_id=mid,
                             side=side, current_edge=round(current_edge, 4),
                             model_prob=round(model_prob, 4),
                             current_price=round(current_price, 4))

    async def _backfill_esports_outcomes(self, db) -> None:
        """Backfill actual_outcome in esports_prediction_log from trade_events RESOLUTION.

        Runs every 10 scans. Idempotent — resolve_predictions() only updates
        rows where actual_outcome IS NULL. YES win → outcome=1, NO win → outcome=0.

        S132: Removed _resolve_esports_from_clob() — S104 workaround that caused
        SE-1 (paper_trades P&L NULL), EB-2 (per-row emission), EB-4 (triple path race).
        S125 fixed shared queue starvation with expired-first ordering.
        Esports resolutions now flow through shared resolution_backfill.py.
        """

        from sqlalchemy import text as _sa_text
        from esports.data.esports_db import resolve_predictions as _resolve
        # S149: Match backfill window to calibration window so all predictions
        # within _cal_days can get actual_outcome populated.  Anchored to
        # Glicko-2 fix date (2026-03-16) — no stale pre-fix data.
        _GLICKO2_FIX = datetime(2026, 3, 16, tzinfo=timezone.utc)
        _backfill_days = min(max(1, (datetime.now(timezone.utc) - _GLICKO2_FIX).days), 90)
        async with db.get_session(timeout=15) as _sess:
            result = await _sess.execute(
                _sa_text("""
                    SELECT DISTINCT te.market_id, te.side,
                        CASE WHEN te.realized_pnl > 0 THEN 1 ELSE 0 END AS won
                    FROM trade_events te
                    WHERE te.bot_name IN ('EsportsBot', 'EsportsLiveBot', 'EsportsSeriesBot')
                      AND te.event_type = 'RESOLUTION'
                      AND te.realized_pnl IS NOT NULL
                      AND te.side IN ('YES', 'NO')
                      AND te.event_time > NOW() - INTERVAL '1 day' * :backfill_days
                """),
                {"backfill_days": _backfill_days},
            )
            resolved = result.fetchall()
        for r in resolved:
            outcome = int(r.won) if r.side == "YES" else (1 - int(r.won))
            await _resolve(db, r.market_id, outcome)

        # S125: Fallback — resolve from markets table for predictions that have no
        # RESOLUTION trade_event (bot predicted but didn't trade, or event >7d old).
        try:
            async with db.get_session(timeout=15) as _sess_mkt:
                _mkt_result = await _sess_mkt.execute(
                    _sa_text("""
                        SELECT p.market_id, m.resolution
                        FROM esports_prediction_log p
                        JOIN markets m ON p.market_id = m.condition_id
                        WHERE p.actual_outcome IS NULL
                          AND m.resolution IN ('YES', 'NO')
                        LIMIT 200
                    """)
                )
                _mkt_rows = _mkt_result.fetchall()
            _mkt_count = 0
            for _mr in _mkt_rows:
                _mkt_outcome = 1 if _mr[1] == "YES" else 0
                _mkt_count += await _resolve(db, _mr[0], _mkt_outcome)
            if _mkt_count > 0:
                logger.info("esportsbot_markets_table_backfill", resolved=_mkt_count)
        except Exception as _mkt_err:
            logger.debug("esportsbot_markets_table_backfill_failed", error=str(_mkt_err))

        # S100b: Feed newly resolved predictions into streaming calibrators (ADWIN + OnlinePlatt)
        try:
            from sqlalchemy import text as _sa_text2
            async with db.get_session(timeout=15) as _sess2:
                _newly = await _sess2.execute(
                    _sa_text2(
                        "SELECT game, predicted_prob, actual_outcome "
                        "FROM esports_prediction_log "
                        "WHERE actual_outcome IS NOT NULL "
                        "AND updated_at > NOW() - INTERVAL '15 minutes' "
                        "LIMIT 500"
                    )
                )
                for _nr in _newly.fetchall():
                    _g = _nr.game if hasattr(_nr, 'game') else _nr[0]
                    _pp = float(_nr.predicted_prob if hasattr(_nr, 'predicted_prob') else _nr[1])
                    _ao = float(_nr.actual_outcome if hasattr(_nr, 'actual_outcome') else _nr[2])
                    if _g:
                        self._update_streaming_on_resolution(_g, _pp, _ao)
        except Exception as exc:
            logger.debug("esportsbot_streaming_feed_failed", error=str(exc))

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """
        Analyze a single esports market for trading opportunity.

        1. Classify market type + detect game
        2. Get model prediction (game-specific or prediction engine)
        3. Validate edge: model_prob - poly_price > ESPORTS_MIN_EDGE
        4. Build trade opportunity if edge exists
        """
        # B4: waterfall counter helper (safe if _wf not initialized)
        _wf = getattr(self, "_wf", None)

        market_id = str(market_data.get("id", ""))
        if not market_id:
            return None

        tokens = market_data.get("tokens", [])
        if not tokens:
            if _wf: _wf["no_token"] += 1
            return None

        # S152: Identify YES/NO tokens by outcome field instead of assuming tokens[0]=YES.
        # LoL regional leagues showed inverted token ordering → systematic YES-side losses.
        _yes_tok = None
        _no_tok = None
        for _t in tokens:
            _outcome = (_t.get("outcome") or _t.get("side") or "").upper()
            if _outcome in ("YES", "1", "TRUE"):
                _yes_tok = _t
            elif _outcome in ("NO", "0", "FALSE"):
                _no_tok = _t
        # Fallback: if no outcome field, use positional order (legacy behavior)
        if _yes_tok is None:
            _yes_tok = tokens[0]
        if _no_tok is None and len(tokens) > 1:
            _no_tok = tokens[1] if tokens[1] is not _yes_tok else tokens[0]

        token = _yes_tok
        price_raw = token.get("outcomePrice") or token.get("price")
        price = self.validate_price(price_raw, market_id)
        if price is None:
            if _wf: _wf["no_price"] += 1
            return None

        token_id = token.get("tokenId") or token.get("token_id")
        if not token_id:
            if _wf: _wf["no_token"] += 1
            return None

        question = (market_data.get("question") or "").lower()

        # Detect game title
        game = self._detect_game(question)
        if not game:
            if _wf: _wf["no_game"] += 1
            return None

        # S134: Hard game disable (env var, no data needed)
        _disabled = getattr(settings, "ESPORTS_DISABLED_GAMES", "")
        if _disabled and game in {g.strip() for g in _disabled.split(",") if g.strip()}:
            if _wf: _wf["halted"] += 1
            return None

        # E4: Check monitoring-halted games
        if game in self._monitoring_halted_games:
            if _wf: _wf["halted"] += 1
            return None

        # Exposure concentration check (per-game cap)
        # S136 8A: Use percentage-based caps when enabled
        _caps = self._get_exposure_caps()
        max_game = _caps["per_game"]
        if self._game_exposure.get(game, 0.0) >= max_game:
            if _wf: _wf["exposure_cap"] += 1
            _ecl = getattr(self, "_exposure_cap_logged", None)
            if _ecl is not None and game not in _ecl:
                _ecl.add(game)
                logger.info("esportsbot_exposure_cap_hit",
                            game=game,
                            exposure=round(self._game_exposure.get(game, 0.0), 2),
                            cap=max_game)
            return None

        # Check observation mode for this game
        if self._patch_drift and self._patch_drift.is_observation_mode(game):
            logger.debug(
                "EsportsBot: observation mode active (paper only)",
                game=game,
                market_id=market_id,
            )
            if _wf: _wf["observation"] += 1
            return None

        # Check if halted
        if self._patch_drift and self._patch_drift.is_halted(game):
            if _wf: _wf["halted"] += 1
            return None

        market_type = self._classify_market_type(question)

        # Skip market types that can't produce Glicko-2 predictions (no team matchup)
        if market_type in ("props", "first_blood", "tournament_winner"):
            logger.info("esportsbot_skip_market_type", game=game, market_type=market_type,
                        question=str(question)[:80])
            if _wf: _wf["no_prediction"] += 1
            return None

        # Get model prediction
        model_prob = await self._get_model_prediction(
            game, market_type, market_id, token_id, price, market_data
        )
        if model_prob is None:
            logger.info("esportsbot_no_prediction", game=game,
                        market_type=market_type, market_id=market_id,
                        question=str(question)[:120])
            if _wf: _wf["no_prediction"] += 1
            return None

        # S136 Phase 4C: Calibrator ensemble (replaces S100/S100b sequential override)
        # S154: Dropped VennABERS — interval_width=1.0 at current n is pure noise.
        # New weights (BetaCal + OnlinePlatt):
        #   Per-game fitted (n>=50):
        #     n 15-29: BC=0.75, OP=0.25
        #     n 30-49: BC=0.55, OP=0.45
        #     n>=50:   BC=0.50, OP=0.50
        #   Per-game NOT fitted (n<50): fall back to global BetaCal pool (n=244)
        #     Global pool provides cross-game miscalibration correction.
        _raw_prob = model_prob
        _beta_cal = self._beta_calibrators.get(game)
        _online_platt = self._online_platt_per_game.get(game)

        _cal_probs = []
        _cal_weights = []
        _n_resolved = _beta_cal
        _n_cal = _n_resolved._n_samples if (_n_resolved and _n_resolved._fitted) else 0

        # BetaCal (with hierarchical pooling)
        if _beta_cal and _beta_cal.is_fitted:
            _bc_prob = _beta_cal.calibrate(_raw_prob)
            # Hierarchical pooling: shrink per-game toward global
            _global_cal = self._global_beta_calibrator
            if _global_cal and _global_cal.is_fitted and _n_cal > 0:
                _lambda_pool = 25
                _w_game = _n_cal / (_n_cal + _lambda_pool)
                _global_prob = _global_cal.calibrate(_raw_prob)
                _bc_prob = _w_game * _bc_prob + (1 - _w_game) * _global_prob
            if _n_cal < 15:
                pass  # n<15: per-game BC skipped (handled by global fallback below)
            elif _n_cal < 30:
                _cal_probs.append(_bc_prob); _cal_weights.append(0.75)
            elif _n_cal < 50:
                _cal_probs.append(_bc_prob); _cal_weights.append(0.55)
            else:
                _cal_probs.append(_bc_prob); _cal_weights.append(0.50)

        # OnlinePlatt
        if _online_platt and _online_platt.is_fitted:
            _op_prob = _online_platt.calibrate(_raw_prob)
            if _n_cal < 15:
                pass  # n<15: per-game OP skipped
            elif _n_cal < 30:
                _cal_probs.append(_op_prob); _cal_weights.append(0.25)
            elif _n_cal < 50:
                _cal_probs.append(_op_prob); _cal_weights.append(0.45)
            else:
                _cal_probs.append(_op_prob); _cal_weights.append(0.50)

        # S154: Global BetaCal fallback for thin games.
        # When no per-game calibrator is fitted (Dota2, Valorant, CoD, R6, SC2, RL),
        # use the global pool (n=244) for universal miscalibration correction.
        if not _cal_probs:
            _global_cal = self._global_beta_calibrator
            if _global_cal and _global_cal.is_fitted:
                _global_prob = _global_cal.calibrate(_raw_prob)
                _cal_probs.append(_global_prob)
                _cal_weights.append(1.0)

        if _cal_probs:
            _w_total = sum(_cal_weights)
            if _w_total > 0:
                model_prob = sum(p * w for p, w in zip(_cal_probs, _cal_weights)) / _w_total
            # Log ensemble components
            logger.debug("esportsbot_calibrator_ensemble", game=game,
                         n_calibrators=len(_cal_probs), n_resolved=_n_cal,
                         raw_prob=round(_raw_prob, 4), ensemble_prob=round(model_prob, 4))

        # RFLB correction [T1-B]: favorites systematically overbetted.
        # Nudge model_prob toward 0.50 when market prices a heavy favorite
        # and model agrees with the favorite. Pre-game only.
        _rflb_strength = float(getattr(settings, "ESPORTS_RFLB_STRENGTH", 0.03))
        if _rflb_strength > 0 and price > 0.70 and model_prob > 0.60:
            _rflb_adj = _rflb_strength * (price - 0.50)
            model_prob = max(0.05, model_prob - _rflb_adj)
            # A/B logging: compute hypothetical adjustments at 0.03/0.05/0.08
            _price_diff = price - 0.50
            _rflb_ab = {
                "adj_003": round(0.03 * _price_diff, 6),
                "adj_005": round(0.05 * _price_diff, 6),
                "adj_008": round(0.08 * _price_diff, 6),
            }
            logger.info(
                "esportsbot_rflb_adjustment", market_id=market_id, game=game,
                raw_prob=round(_raw_prob, 4), adjusted_prob=round(model_prob, 4),
                rflb_adj=round(_rflb_adj, 4), market_price=round(price, 4),
                rflb_ab=_rflb_ab,
            )

        # S159: Per-game YES overestimation dampener.
        # LoL model_prob systematically overestimates P(YES) for underdogs — model says
        # 0.60, actual resolution win rate ~0.25.  When model produces a YES edge
        # (model_prob > price), shrink model_prob toward market price by _LOL_YES_SHRINK.
        # This reduces YES edge without affecting NO predictions (which are profitable).
        _LOL_YES_SHRINK = float(getattr(settings, "ESPORTS_LOL_YES_SHRINK", 0.40))
        if game == "lol" and model_prob > price and _LOL_YES_SHRINK > 0:
            _pre_shrink = model_prob
            model_prob = price + (model_prob - price) * (1.0 - _LOL_YES_SHRINK)
            logger.info("esportsbot_lol_yes_dampener", market_id=market_id,
                        pre_shrink=round(_pre_shrink, 4), post_shrink=round(model_prob, 4),
                        market_price=round(price, 4), shrink=_LOL_YES_SHRINK)

        # S133: Early prediction logging — log ALL model predictions for calibrator learning,
        # even if downstream edge/confidence gates reject the trade. The existing dedup
        # (ON CONFLICT UPDATE) prevents duplicates if the trade also logs later.
        _tournament_phase = self._detect_tournament_phase(market_data)
        _early_log_cache = self._prediction_log_cache.get(market_id)
        _should_early_log = True
        if _early_log_cache:
            _prev_prob, _prev_ts = _early_log_cache
            if abs(_prev_prob - model_prob) < 0.01 and (time.monotonic() - _prev_ts) < 600:
                _should_early_log = False
        if _should_early_log:
            try:
                _db_early = getattr(self.base_engine, "db", None)
                if _db_early is not None:
                    from esports.data.esports_db import log_prediction as _early_log_pred
                    _early_side = "YES" if model_prob >= price else "NO"
                    _early_edge = abs(model_prob - price)
                    await _early_log_pred(
                        db=_db_early,
                        match_id=market_id,
                        game=game,
                        market_id=market_id,
                        bot_name="EsportsBot",
                        predicted_prob=model_prob,
                        market_price=price,
                        side=_early_side,
                        edge=round(_early_edge, 4),
                        tournament_phase=_tournament_phase,
                        raw_model_prob=_raw_prob,
                    )
                    self._prediction_log_cache[market_id] = (model_prob, time.monotonic())
            except Exception as e:
                logger.debug("prediction_log_failed market=%s: %s", market_id, e)

        # S135+S136: Divergence cap — when model disagrees massively with market,
        # market has live info model doesn't. S136 Phase 9C: per-game adaptive cap.
        _div = abs(model_prob - price)
        _game_div_cap = self._get_adaptive_div_cap(game)
        _effective_div_cap = min(
            _game_div_cap,
            float(getattr(settings, "ESPORTS_MAX_MODEL_DIVERGENCE", 0.25)),
        )
        if _div > _effective_div_cap:
            logger.info(
                "esportsbot_divergence_capped", market_id=market_id, game=game,
                model_prob=round(model_prob, 4), market_price=round(price, 4),
                divergence=round(_div, 4), cap=_effective_div_cap,
                adaptive_cap=round(_game_div_cap, 4),
            )
            if _wf:
                _wf["divergence_cap"] = _wf.get("divergence_cap", 0) + 1
            return None

        # Validate edge
        # YES side: model thinks YES is more likely than market price
        # NO side: model thinks YES is less likely than market price
        no_token = _no_tok if _no_tok else {}
        no_token_id = no_token.get("tokenId") or no_token.get("token_id")

        # Populate token map for WS reactive path (Fix 1: YES/NO identification)
        self._market_token_map[market_id] = {
            "yes": str(token_id),
            "no": str(no_token_id) if no_token_id else "",
        }

        edge_yes = model_prob - price

        # Market-price fallback trades require higher edge threshold
        _cached = self._prediction_cache.get(market_id, {})
        _effective_min_edge = (
            float(getattr(settings, "ESPORTS_MARKET_FALLBACK_MIN_EDGE", 0.15))
            if _cached.get("fallback") else self._min_edge
        )
        # S157: Edge hysteresis — use higher entry threshold for new positions
        _effective_min_edge = max(_effective_min_edge, self._min_edge_entry)

        if edge_yes >= _effective_min_edge:
            side = "YES"
            trade_token_id = token_id
            trade_price = price
            edge = edge_yes
            side_prob = model_prob
        elif -edge_yes >= _effective_min_edge and no_token_id:
            side = "NO"
            trade_token_id = no_token_id
            trade_price = 1.0 - price
            edge = -edge_yes
            side_prob = 1.0 - model_prob
        else:
            if _wf: _wf["low_edge"] += 1
            return None

        # S131: SQ is a SIZING multiplier, not a confidence multiplier.
        # confidence = raw side_prob (what we believe). SQ scales bet SIZE in
        # _execute_esports_trade, not the probability itself.
        confidence = side_prob
        try:
            _sq, _sq_components = self._compute_signal_quality(game, market_id)
            logger.info("esportsbot_signal_quality", market_id=market_id, game=game,
                        side_prob=round(side_prob, 4), signal_quality=round(_sq, 4),
                        confidence=round(confidence, 4),
                        sq_agreement=_sq_components.get("agreement"),
                        sq_calibration=_sq_components.get("calibration"),
                        sq_uncertainty=_sq_components.get("uncertainty"),
                        sq_enrichment=_sq_components.get("enrichment"),
                        sq_brier=_sq_components.get("brier"))
        except Exception as _sq_exc:
            _sq = 0.5  # Fallback: conservative half sizing on error
            _sq_components = {}
            logger.warning("esportsbot_signal_quality_failed", error=str(_sq_exc),
                          market_id=market_id, game=game)

        # S112: Edge cap REMOVED — all edges trade through.
        # Log high-edge entries for handoff monitoring (negative trend at edge>0.40).
        if edge > 0.40:
            logger.info(
                "esportsbot_high_edge", market_id=market_id, game=game,
                edge=round(edge, 4), side=side,
                model_prob=round(model_prob, 4), price=round(price, 4),
            )

        # High-uncertainty filter: unrated/weakly-rated teams + thin edge + BO1 → skip.
        # matchup_uncertainty = (phi_a + phi_b) / 700; >= 0.70 means avg phi >= 245.
        _cached_pred = self._prediction_cache.get(market_id, {})
        _ed = _cached_pred.get("event_data", {})
        # S127: Store signal quality in event_data for ENTRY logging
        _ed["signal_quality"] = round(_sq, 4)
        _ed["sq_components"] = {k: round(v, 4) for k, v in _sq_components.items()}
        _mu = _ed.get("matchup_uncertainty", 0.0)
        _bo_cached = _ed.get("best_of", 1)
        if _mu >= 0.70 and _bo_cached == 1 and edge < 0.10:
            logger.info(
                "esportsbot_high_uncertainty_skip", market_id=market_id,
                game=game, edge=round(edge, 4),
                matchup_uncertainty=round(_mu, 3), best_of=_bo_cached,
            )
            if _wf: _wf["high_uncertainty"] = _wf.get("high_uncertainty", 0) + 1
            return None

        # Tournament phase detection and confidence boost
        # S100b: Suspend phase penalty while BetaCalibrator unfitted — raw Glicko2
        # probs already encode uncertainty via phi-based Bayesian blending.
        db = getattr(self.base_engine, "db", None)
        _beta_cal = self._beta_calibrators.get(game)
        if _beta_cal and not _beta_cal._fitted:
            _phase_mult = 1.0
        else:
            _phase_mult = await self._get_tournament_phase_mult(
                market_data, game, _tournament_phase, db
            )
        confidence *= _phase_mult

        # S151: Confidence gate uses env-configurable MIN_CONFIDENCE (default 0.20).
        # Previously hardcoded floor of 0.52 blocked 22 markets with real edge
        # where model predicted near 50/50 (e.g. model=0.47, market=0.25 → 22% edge).
        # BM sizing (S151) controls risk via sigma instead of hard blocking.
        _min_side_prob = self._min_confidence
        if confidence < _min_side_prob:
            if _wf: _wf["low_confidence"] += 1
            logger.info("esportsbot_low_confidence", game=game, market_id=market_id,
                        confidence=round(confidence, 4), model_prob=round(model_prob, 4),
                        edge=round(edge, 4), side=side, price=round(price, 4))
            return None

        # Log prediction for accuracy tracking (dedup: skip if unchanged within 10 min)
        _log_cache = self._prediction_log_cache.get(market_id)
        _should_log = True
        if _log_cache:
            _prev_prob, _prev_ts = _log_cache
            if abs(_prev_prob - model_prob) < 0.01 and (time.monotonic() - _prev_ts) < 600:
                _should_log = False
        if _should_log and db is not None:
            try:
                from esports.data.esports_db import log_prediction
                await log_prediction(
                    db=db,
                    match_id=market_id,
                    game=game,
                    market_id=market_id,
                    bot_name="EsportsBot",
                    predicted_prob=model_prob,
                    market_price=price,
                    side=side,
                    edge=round(edge, 4),
                    tournament_phase=_tournament_phase,
                    raw_model_prob=_raw_prob,
                )
                self._prediction_log_cache[market_id] = (model_prob, time.monotonic())
            except Exception as exc:
                logger.warning("EsportsBot: prediction logging failed", error=str(exc))

        # Session 83: CoT validation for high-edge trades (>=20%)
        if (self._cot_validator is not None
                and self._cot_validator.is_available
                and edge >= 0.20):
            try:
                question = str(market_data.get("question", ""))
                cot_result = await self._cot_validator.validate_trade(
                    question=question, game=game,
                    model_prob=model_prob, market_price=price,
                    edge=edge, side=side,
                )
                if not cot_result.get("approved", True):
                    logger.info("esportsbot_cot_rejected", market_id=market_id,
                                game=game, edge=round(edge, 4),
                                reason=cot_result.get("reason", "")[:100])
                    if _wf: _wf["cot_rejected"] = _wf.get("cot_rejected", 0) + 1
                    return None
            except Exception as exc:
                logger.debug("esportsbot_cot_error", error=str(exc))

        if _wf: _wf["passed"] += 1

        # CLOB volume passthrough for fill probability model (GAP-1, mirrors WeatherBot)
        _clob_volume = 0.0
        gw = getattr(self.base_engine, "order_gateway", None)
        if gw:
            try:
                _midx = getattr(gw, "_market_index", None)
                _midx_cid = getattr(gw, "_market_index_by_cid", None)
                _mdata = None
                if _midx and isinstance(_midx, dict):
                    _mdata = _midx.get(str(market_id))
                if not _mdata and _midx_cid and isinstance(_midx_cid, dict):
                    _mdata = _midx_cid.get(str(market_id))
                if _mdata and isinstance(_mdata, dict):
                    _clob_volume = float(_mdata.get("volume") or _mdata.get("volume24hr") or 0)
            except (TypeError, ValueError, AttributeError) as _vol_exc:
                logger.debug("esportsbot_volume_parse_failed", market_id=market_id, error=str(_vol_exc))

        # S141: Baker-McHale conformal width (real value from fitted predictor)
        _conformal_width = 0.15
        _cp = self._conformal_per_game.get(game)
        if _cp and getattr(_cp, "is_fitted", False):
            try:
                import numpy as _np_cw
                _cw_low, _, _cw_high = _cp.predict_interval(
                    _np_cw.array([[model_prob]])
                )
                _conformal_width = float(_cw_high[0] - _cw_low[0])
            except Exception as _cw_exc:
                logger.debug("esportsbot_conformal_width_failed", market_id=market_id, error=str(_cw_exc))

        # S141: Baker-McHale agreement stdev (from signal quality)
        _agreement_stdev = _sq_components.get("agreement_stdev", 0.10)

        return {
            "type": "esports_pregame" if not self._is_live(market_id) else "esports_live",
            "market_id": market_id,
            "token_id": str(trade_token_id),
            "side": side,
            "price": trade_price,
            "confidence": confidence,
            "prediction": model_prob,
            "edge": round(edge, 4),
            "game": game,
            "market_type": market_type,
            "end_date_iso": market_data.get("end_date_iso"),
            "_clob_volume": _clob_volume,
            "_signal_quality": _sq,
            # S141: Baker-McHale sigma inputs (real values from Glicko-2 + calibration)
            "_phi_a": float(_ed.get("_phi_a", 200.0)),
            "_phi_b": float(_ed.get("_phi_b", 200.0)),
            "_conformal_width": _conformal_width,
            "_agreement_stdev": _agreement_stdev,
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _enrich_prediction(
        self, prob: float, game: str, market_id: str,
        market_data: Dict, live_data,
    ) -> tuple:
        """Apply post-ML enrichment: form adj, cross-game XGB, CatBoost draft,
        LAN adj, blue side bonus, BO adjustment.

        Returns (enriched_prob, enrich_meta) where enrich_meta captures
        intermediate signal values for signal quality computation.

        Centralizes enrichment that was previously only in the Glicko-2 fallback.
        Now called by ALL prediction paths (ML + fallback) for consistency.
        """
        _enrich_meta: Dict[str, Any] = {
            "xgb_raw": None, "cb_prob": None,
            "form_applied": False, "tabpfn_applied": False,
            "lan_applied": False, "bo_applied": False,
        }

        # 1. Form adjustment (game-specific APIs)
        _pre_form = prob
        if game == "dota2":
            prob = await self._opendota_form_adjustment(market_data, prob)
        if game != "dota2":
            prob = await self._pandascore_form_adjustment(market_data, prob, game)
        if game == "sc2":
            prob = await self._aligulac_sc2_blend(market_data, prob)
        if game == "rl":
            prob = await self._ballchasing_rl_adjustment(market_data, prob)
        if abs(prob - _pre_form) > 0.001:
            _enrich_meta["form_applied"] = True

        # 2. TabPFN blend for sparse games (SC2, RL, CoD, R6)
        if (self._tabpfn_predictor is not None
                and self._tabpfn_predictor.is_available
                and game in ("sc2", "rl", "cod", "r6")):
            game_state_tabpfn = self._build_glicko2_game_state(market_data, game)
            if game_state_tabpfn and self._tabpfn_predictor.is_fitted(game):
                tabpfn_prob = self._tabpfn_predictor.predict(game, game_state_tabpfn)
                if tabpfn_prob is not None:
                    w = self._tabpfn_predictor.get_blend_weight()
                    prob = w * tabpfn_prob + (1 - w) * prob
                    prob = max(0.05, min(0.95, prob))
                    _enrich_meta["tabpfn_applied"] = True

        # 3. Cross-game XGB blend: augment with meta-patterns
        xgb_raw = None
        if self._cross_game_model is not None and game in self._CROSS_GAME_IDS:
            game_state = self._build_glicko2_game_state(market_data, game)
            if game_state:
                import numpy as _np
                feats = [
                    game_state["team_strength_diff"],
                    game_state["matchup_uncertainty"],
                    game_state["rd_asymmetry"],
                    game_state["team_a_volatility"],
                    game_state["team_b_volatility"],
                    game_state.get("team_a_recent_form", 0.5),
                    game_state.get("team_b_recent_form", 0.5),
                    float(self._CROSS_GAME_IDS[game]),
                    game_state.get("best_of", 1.0),
                ]
                _feat_arr = _np.array([feats], dtype=_np.float32)
                if self._onnx_cross_game_session is not None:
                    try:
                        from esports.models.onnx_compiler import OnnxCompiler
                        _onnx_probs = OnnxCompiler().predict_proba(
                            self._onnx_cross_game_session, _feat_arr
                        )
                        xgb_prob = float(_onnx_probs[0][1])
                    except Exception:
                        xgb_prob = float(
                            self._cross_game_model.predict_proba(_feat_arr)[0][1]
                        )
                else:
                    xgb_prob = float(
                        self._cross_game_model.predict_proba(_feat_arr)[0][1]
                    )
                xgb_raw = xgb_prob
                _enrich_meta["xgb_raw"] = xgb_raw
                # S141: simple weighted blend — EGM already applied in
                # per-game ML+Glicko-2 blend; re-applying here was
                # recursive extremization inflating all 8 games' probs.
                # S142: Reduce XGB weight to 0.1 if model is stale (>14 days)
                _xgb_w = 0.1 if getattr(self, "_xgb_stale", False) else 0.4
                prob = (1.0 - _xgb_w) * prob + _xgb_w * xgb_prob
                prob = max(0.05, min(0.95, prob))

        # 4. CatBoost draft model blend
        if (getattr(settings, "ESPORTS_CATBOOST_ENABLED", False)
                and game in self._catboost_models
                and self._catboost_models[game].is_fitted
                and self._draft_feature_builder is not None):
            _draft_data = None
            _live = self._live_matches.get(market_id, {})
            if isinstance(_live, dict):
                _draft_data = _live.get("draft")
            if not _draft_data and isinstance(market_data, dict):
                _draft_data = market_data.get("draft")
            if _draft_data and isinstance(_draft_data, dict):
                try:
                    _draft_feats = self._draft_feature_builder.build_features(
                        _draft_data, game,
                    )
                    _gs = self._build_glicko2_game_state(market_data, game)
                    if _gs:
                        for _fk in ("team_strength_diff", "matchup_uncertainty",
                                     "rd_asymmetry", "team_a_volatility",
                                     "team_b_volatility", "best_of"):
                            _draft_feats[_fk] = float(_gs.get(_fk, 0.0))
                    _cb_prob = self._catboost_models[game].predict_proba(_draft_feats)
                    _enrich_meta["cb_prob"] = _cb_prob
                    if 0.05 < _cb_prob < 0.95:
                        _cb_weight = float(getattr(
                            settings, "ESPORTS_CATBOOST_BLEND_WEIGHT", 0.4,
                        ))
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean(
                            [prob, _cb_prob],
                            weights=[1.0 - _cb_weight, _cb_weight], d=_d,
                        )
                        prob = max(0.05, min(0.95, prob))
                        logger.debug(
                            "esportsbot_catboost_blend", game=game,
                            market_id=market_id,
                            catboost_prob=round(_cb_prob, 4),
                            blended_prob=round(prob, 4),
                        )
                except Exception as _cb_exc:
                    logger.debug("esportsbot_catboost_blend_failed",
                                 game=game, error=str(_cb_exc))

        # 5. LAN adjustment (all games with LAN events — S137 parity fix)
        if getattr(settings, "ESPORTS_LAN_ADJUSTMENT_ENABLED", True):
            _is_lan = self._is_lan_event(market_data)
            if _is_lan:
                if prob > 0.55:
                    prob = max(0.05, prob - 0.02)
                elif prob < 0.45:
                    prob = min(0.95, prob + 0.01)
                _enrich_meta["lan_applied"] = True
                logger.debug(
                    "esportsbot_lan_adjustment", game=game,
                    market_id=market_id, prob=round(prob, 4),
                )

        # 6. LoL blue side bonus — DISABLED S149: no blue/red detection exists,
        # so this applied +1.9% in a random direction on every LoL market.
        # Re-enable only when actual blue-side detection is implemented.
        # if game == "lol":
        #     _blue_bonus = float(getattr(
        #         settings, "ESPORTS_LOL_BLUE_SIDE_BONUS", 0.019,
        #     ))
        #     if _blue_bonus > 0:
        #         prob = max(0.05, min(0.95, prob + _blue_bonus))
        #         logger.debug(
        #             "esportsbot_blue_side_applied", game=game,
        #             market_id=market_id, bonus=_blue_bonus,
        #             prob=round(prob, 4),
        #         )

        # 7. BO format adjustment
        _bo = 1
        if live_data is not None:
            try:
                _bo = int(getattr(live_data, "best_of", 1) if not isinstance(live_data, dict) else live_data.get("best_of", 1))
            except (ValueError, TypeError):
                _bo = 1
        if _bo > 1:
            _pre_bo = prob
            from esports.models.series_model import bo3_match_prob, bo5_match_prob
            if _bo == 3:
                prob = bo3_match_prob(prob, 0, 0)
            elif _bo >= 5:
                prob = bo5_match_prob(prob, 0, 0)
            _enrich_meta["bo_applied"] = True
            logger.info(
                "esportsbot_bo_adjustment", market_id=market_id, game=game,
                best_of=_bo, raw_prob=round(_pre_bo, 4),
                adjusted_prob=round(prob, 4),
            )
        elif _bo == 1:
            _pre_bo = prob
            from esports.models.series_model import bo1_underdog_adjustment
            prob = bo1_underdog_adjustment(prob)
            _enrich_meta["bo_applied"] = True
            if abs(prob - _pre_bo) > 0.001:
                logger.info(
                    "esportsbot_bo_adjustment", market_id=market_id, game=game,
                    best_of=1, raw_prob=round(_pre_bo, 4),
                    adjusted_prob=round(prob, 4),
                )

        return max(0.05, min(0.95, prob)), _enrich_meta

    async def _get_model_prediction(
        self,
        game: str,
        market_type: str,
        market_id: str,
        token_id: str,
        price: float,
        market_data: Dict,
    ) -> Optional[float]:
        """
        Get win probability from game-specific model or prediction engine.

        Returns model's estimated probability for YES outcome, or None.
        """
        # Try game-specific model for live matches (only if graduated)
        live_data = self._live_matches.get(market_id)

        if game == "lol" and self._lol_model and self._lol_model.is_trained and live_data:
            try:
                game_state = live_data.get("game_state", {})
                if game_state:
                    # Inject Glicko-2 metadata features for live inference
                    self._inject_glicko2_metadata(game_state, game, live_data)
                    # Blend ML + Glicko-2: trained model only learned from
                    # team_strength_diff (in-game features were neutralized in
                    # training), so raw predict() is ~51% on live data.
                    # predict_with_glicko2() anchors on the Glicko-2 baseline
                    # and lets the ML model adjust, dampening noise.
                    tsd = float(game_state.get("team_strength_diff", 0.0))
                    glicko2_est = max(0.05, min(0.95, 0.5 + tsd))
                    prob = await asyncio.to_thread(
                        self._lol_model.predict_with_glicko2,
                        game_state, glicko2_est,
                    )
                    if 0.0 < prob < 1.0:
                        prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                        _ed_lol = {"game": game, "model_prob": round(prob, 4),
                                   "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                   "_enrich_meta": _em}
                        _gs_lol = self._build_glicko2_game_state(market_data, game)
                        if _gs_lol:
                            for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                        "team_a_volatility", "team_b_volatility",
                                        "_phi_a", "_phi_b"):
                                _ed_lol[_fk] = round(float(_gs_lol.get(_fk, 0.0)), 6)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._lol_model.predict(game_state),
                            "glicko2_est": glicko2_est,
                            "event_data": _ed_lol,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_lol_model_predict_failed", game="lol", error=str(_e))

        # LoL pre-game: use ML model with neutral live features + real Glicko-2
        if game == "lol" and self._lol_model and self._lol_model.is_trained and not live_data:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game, price)
                if glicko2_prob is not None:
                    _gs_lol = self._build_glicko2_game_state(market_data, game)
                    if _gs_lol:
                        # Build LoL feature dict: neutral live features + real Glicko-2
                        game_state = {
                            "game_time_minutes": 30.0,
                            "gold_pct_blue": 0.5,
                            "tower_kills_diff": 0.0,
                            "dragon_kills_diff": 0.0,
                            "matchup_uncertainty": _gs_lol["matchup_uncertainty"],
                            "rd_asymmetry": _gs_lol["rd_asymmetry"],
                            "team_a_volatility": _gs_lol["team_a_volatility"],
                            "team_b_volatility": _gs_lol["team_b_volatility"],
                            "team_strength_diff": _gs_lol["team_strength_diff"],
                        }
                        tsd = float(_gs_lol.get("team_strength_diff", 0.0))
                        glicko2_est = max(0.05, min(0.95, 0.5 + tsd))
                        prob = await asyncio.to_thread(
                            self._lol_model.predict_with_glicko2,
                            game_state, glicko2_est,
                        )
                        if 0.0 < prob < 1.0:
                            prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                            _ed_lol_pre = {"game": game, "model_prob": round(prob, 4),
                                           "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                           "_enrich_meta": _em}
                            for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                        "team_a_volatility", "team_b_volatility",
                                        "_phi_a", "_phi_b"):
                                _ed_lol_pre[_fk] = round(float(_gs_lol.get(_fk, 0.0)), 6)
                            self._prediction_cache[market_id] = {
                                "prob": prob, "ts": time.monotonic(), "game": game,
                                "ml_raw": self._lol_model.predict(game_state),
                                "glicko2_est": glicko2_est,
                                "event_data": _ed_lol_pre,
                            }
                            return prob
            except Exception as _e:
                logger.debug("esportsbot_lol_pregame_predict_failed", game="lol", error=str(_e))

        if game == "cs2" and self._cs2_model and self._cs2_model.is_trained and live_data:
            try:
                game_state = live_data.get("game_state", {})
                if game_state:
                    prob = self._cs2_model.predict_match(
                        maps_won_a=live_data.get("score_maps_a", 0),
                        maps_won_b=live_data.get("score_maps_b", 0),
                        best_of=live_data.get("best_of", 1),
                        map_probs=game_state.get("map_probs"),
                    )
                    if 0.0 < prob < 1.0:
                        prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                        tsd = float(game_state.get("team_strength_diff", 0.0))
                        # S162: removed /2 — copy-paste artifact from 1982626fe, matches L3170/L3216
                        glicko2_est = max(0.05, min(0.95, 0.5 + tsd))
                        _ed_cs2 = {"game": game, "model_prob": round(prob, 4),
                                   "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                   "_enrich_meta": _em}
                        if game_state:
                            for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                        "team_a_volatility", "team_b_volatility",
                                        "_phi_a", "_phi_b"):
                                _ed_cs2[_fk] = round(float(game_state.get(_fk, 0.0)), 6)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": prob, "glicko2_est": glicko2_est,
                            "event_data": _ed_cs2,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_cs2_model_predict_failed", game="cs2", error=str(_e))

        # CS2 pre-game: use pregame model with Glicko-2 features (mirrors Dota2/Valorant pattern)
        if game == "cs2" and self._cs2_model and self._cs2_model.is_trained and not live_data:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game, price)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        # S154: HLTV injection DISABLED — train/serve skew bug.
                        # CS2 model was trained WITHOUT HLTV features (they default to 0.0
                        # in training data), but this block injected real non-zero values
                        # at inference. The model can't meaningfully interpret features it
                        # never saw during training. hltv_ranking_diff, recent_form_3m, and
                        # lan_flag now stay at 0.0 (matching training conditions).
                        # Re-enable ONLY after backfilling HLTV features into training data
                        # and retraining the CS2 model.
                        # --- Original S137 Phase 5A wiring (disabled) ---
                        # if getattr(self, "_hltv", None):
                        #     try:
                        #         _q = str(market_data.get("question", "")).lower()
                        #         _ta, _tb, _, _ = self._extract_team_ids_from_question(_q)
                        #         if _ta and _tb:
                        #             _ra = await self._hltv.get_team_rating(_ta, game="cs2")
                        #             _rb = await self._hltv.get_team_rating(_tb, game="cs2")
                        #             if _ra is not None and _rb is not None:
                        #                 game_state["hltv_ranking_diff"] = _ra - _rb
                        #             _results_a = await self._hltv.get_recent_results(_ta, game="cs2", n=20)
                        #             if _results_a:
                        #                 _wins = sum(1 for _r in _results_a if _r.get("won"))
                        #                 game_state["recent_form_3m"] = _wins / len(_results_a) - 0.5
                        #     except Exception:
                        #         pass
                        prob = self._cs2_model.predict_pregame(game_state)
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean([prob, glicko2_prob], d=_d)
                        prob = max(0.05, min(0.95, prob))
                        prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                        _ed_cs2_pre = {"game": game, "model_prob": round(prob, 4),
                                       "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                       "_enrich_meta": _em}
                        for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                    "team_a_volatility", "team_b_volatility",
                                    "_phi_a", "_phi_b"):
                            _ed_cs2_pre[_fk] = round(float(game_state.get(_fk, 0.0)), 6)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._cs2_model.predict_pregame(game_state),
                            "glicko2_est": glicko2_prob,
                            "event_data": _ed_cs2_pre,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_cs2_pregame_predict_failed", game="cs2", error=str(_e))

        # Dota2/Valorant: use ML model with Glicko-2 features (pre-match only)
        if game == "dota2" and self._dota2_model and self._dota2_model.is_trained:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game, price)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._onnx_predict_game(
                            self._onnx_dota2_session, game_state,
                            self._dota2_model, "dota2",
                        )
                        # Blend ML with Glicko-2: extremized geometric mean of odds
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean([prob, glicko2_prob], d=_d)
                        prob = max(0.05, min(0.95, prob))
                        prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                        _ed_d2 = {"game": game, "model_prob": round(prob, 4),
                                  "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                  "_enrich_meta": _em}
                        if game_state:
                            for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                        "team_a_volatility", "team_b_volatility",
                                        "_phi_a", "_phi_b"):
                                _ed_d2[_fk] = round(float(game_state.get(_fk, 0.0)), 6)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._dota2_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                            "event_data": _ed_d2,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_dota2_model_predict_failed", game="dota2", error=str(_e))

        if game == "valorant" and self._valorant_model and self._valorant_model.is_trained:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game, price)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._onnx_predict_game(
                            self._onnx_valorant_session, game_state,
                            self._valorant_model, "valorant",
                        )
                        # Blend ML with Glicko-2: extremized geometric mean of odds
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean([prob, glicko2_prob], d=_d)
                        prob = max(0.05, min(0.95, prob))
                        prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                        _ed_val = {"game": game, "model_prob": round(prob, 4),
                                   "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                   "_enrich_meta": _em}
                        if game_state:
                            for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                        "team_a_volatility", "team_b_volatility",
                                        "_phi_a", "_phi_b"):
                                _ed_val[_fk] = round(float(game_state.get(_fk, 0.0)), 6)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._valorant_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                            "event_data": _ed_val,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_valorant_model_predict_failed", game="valorant", error=str(_e))

        # S136 Phase 11A-11D: CoD, RL, SC2, R6 — pre-game only (same pattern as Dota2/Valorant)
        if game == "cod" and self._cod_model and self._cod_model.is_trained:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game, price)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._cod_model.predict(game_state)
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean([prob, glicko2_prob], d=_d)
                        prob = max(0.05, min(0.95, prob))
                        prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                        _ed_cod = {"game": game, "model_prob": round(prob, 4),
                                   "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                   "_enrich_meta": _em}
                        for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                    "team_a_volatility", "team_b_volatility",
                                    "_phi_a", "_phi_b"):
                            _ed_cod[_fk] = round(float(game_state.get(_fk, 0.0)), 6)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._cod_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                            "event_data": _ed_cod,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_cod_model_predict_failed", game="cod", error=str(_e))

        if game == "rl" and self._rl_model and self._rl_model.is_trained:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game, price)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._rl_model.predict(game_state)
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean([prob, glicko2_prob], d=_d)
                        prob = max(0.05, min(0.95, prob))
                        prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                        _ed_rl = {"game": game, "model_prob": round(prob, 4),
                                  "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                  "_enrich_meta": _em}
                        for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                    "team_a_volatility", "team_b_volatility",
                                    "_phi_a", "_phi_b"):
                            _ed_rl[_fk] = round(float(game_state.get(_fk, 0.0)), 6)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._rl_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                            "event_data": _ed_rl,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_rl_model_predict_failed", game="rl", error=str(_e))

        if game == "sc2" and self._sc2_model and self._sc2_model.is_trained:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game, price)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._sc2_model.predict(game_state)
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean([prob, glicko2_prob], d=_d)
                        prob = max(0.05, min(0.95, prob))
                        prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                        _ed_sc2 = {"game": game, "model_prob": round(prob, 4),
                                   "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                   "_enrich_meta": _em}
                        for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                    "team_a_volatility", "team_b_volatility",
                                    "_phi_a", "_phi_b"):
                            _ed_sc2[_fk] = round(float(game_state.get(_fk, 0.0)), 6)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._sc2_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                            "event_data": _ed_sc2,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_sc2_model_predict_failed", game="sc2", error=str(_e))

        if game == "r6" and self._r6_model and self._r6_model.is_trained:
            try:
                glicko2_prob = await self._get_glicko2_prediction(market_data, game, price)
                if glicko2_prob is not None:
                    game_state = self._build_glicko2_game_state(market_data, game)
                    if game_state:
                        prob = self._r6_model.predict(game_state)
                        _d = self._game_egm_d.get(game, self._egm_d)
                        prob = extremized_geometric_mean([prob, glicko2_prob], d=_d)
                        prob = max(0.05, min(0.95, prob))
                        prob, _em = await self._enrich_prediction(prob, game, market_id, market_data, live_data)
                        _ed_r6 = {"game": game, "model_prob": round(prob, 4),
                                  "scan_start_mono": getattr(self, "_scan_start_mono", None),
                                  "_enrich_meta": _em}
                        for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                    "team_a_volatility", "team_b_volatility",
                                    "_phi_a", "_phi_b"):
                            _ed_r6[_fk] = round(float(game_state.get(_fk, 0.0)), 6)
                        self._prediction_cache[market_id] = {
                            "prob": prob, "ts": time.monotonic(), "game": game,
                            "ml_raw": self._r6_model.predict(game_state),
                            "glicko2_est": glicko2_prob,
                            "event_data": _ed_r6,
                        }
                        return prob
            except Exception as _e:
                logger.debug("esportsbot_r6_model_predict_failed", game="r6", error=str(_e))

        # "Easy mode" fallback: Glicko-2 expected score from team strength ratings.
        # Replaces base prediction engine (politics/crypto model) which produced
        # random predictions for esports markets — cross-contamination.
        # Graduation: once ML models pass accuracy >= 55% + brier <= 0.24,
        # they take over and Glicko-2 becomes just one blend component.
        try:
            self._last_glicko2_miss_reason = ""
            glicko2_prob = await self._get_glicko2_prediction(market_data, game, price)
            if glicko2_prob is None:
                # Rate-limit: only log once per market per session (same pattern as team_match_fail)
                if market_id not in self._team_fail_logged:
                    self._team_fail_logged.add(market_id)
                    logger.info("esportsbot_glicko2_miss", game=game,
                                market_id=market_id,
                                reason=self._last_glicko2_miss_reason or "unknown",
                                question=str(market_data.get("question",""))[:80])
                # S94-P3: DISABLED fallback 0.50 — was creating huge fake edges
                # (0.50 vs market price 0.85-0.92 = 35-42% "edge") that flooded
                # the waterfall with edge_cap rejections.  A coin-flip guess has
                # zero informational value; returning None lets the waterfall
                # count it correctly as no_prediction instead.
                return None
            if glicko2_prob is not None:
                # Apply all enrichment steps (form adj, XGB blend, LAN, blue side, BO)
                glicko2_prob, _em = await self._enrich_prediction(
                    glicko2_prob, game, market_id, market_data, live_data,
                )

                # Build event_data for ENTRY trade_event (E1/E7 training data)
                _event_data = {"game": game, "model_prob": round(glicko2_prob, 4),
                              "scan_start_mono": getattr(self, "_scan_start_mono", None),
                              "_enrich_meta": _em}
                _gs = self._build_glicko2_game_state(market_data, game) if game else None
                if _gs:
                    for _fk in ("team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                                "team_a_volatility", "team_b_volatility", "best_of",
                                "_phi_a", "_phi_b"):
                        _event_data[_fk] = round(float(_gs.get(_fk, 0.0)), 6)
                if live_data is not None:
                    try:
                        _event_data["best_of"] = int(getattr(live_data, "best_of", 1) if not isinstance(live_data, dict) else live_data.get("best_of", 1))
                    except (ValueError, TypeError):
                        pass

                self._prediction_cache[market_id] = {
                    "prob": glicko2_prob, "ts": time.monotonic(), "game": game,
                    "ml_raw": None, "glicko2_est": glicko2_prob,
                    "event_data": _event_data,
                }
                return glicko2_prob
        except Exception as exc:
            logger.warning("esportsbot_glicko2_fallback_failed", game=game, error=str(exc))

        return None

    async def _refresh_live_matches(self) -> None:
        """Fetch live matches from PandaScore (rate-limited, configurable interval)."""
        now = time.monotonic()
        refresh_interval = int(getattr(settings, "ESPORTS_PANDASCORE_REFRESH_INTERVAL", 15))
        if now - self._last_live_refresh < refresh_interval:
            return
        self._last_live_refresh = now

        if not self._pandascore:
            return

        _ps_timeout = float(getattr(settings, "ESPORTS_PANDASCORE_TIMEOUT", 5.0))
        try:
            live = await asyncio.wait_for(
                self._pandascore.get_live_matches(), timeout=_ps_timeout
            )
            new_live = {}
            for match in (live or []):
                mid = str(match.match_id)
                if mid:
                    new_live[mid] = match
            self._live_matches = new_live
            self._live_refresh_failures = 0
        except asyncio.TimeoutError:
            self._live_refresh_failures += 1
            if self._live_refresh_failures >= 3:
                logger.warning("esportsbot_live_refresh_stale",
                               consecutive_failures=self._live_refresh_failures,
                               timeout_s=_ps_timeout)
            else:
                logger.debug("EsportsBot: live match refresh timed out (%.0fs)", _ps_timeout)
        except Exception as exc:
            self._live_refresh_failures += 1
            logger.warning("EsportsBot: live match refresh failed", error=str(exc))

    def _is_live(self, market_id: str) -> bool:
        """Check if a market has an associated live match."""
        return market_id in self._live_matches

    def _inject_glicko2_metadata(
        self, game_state: Dict, game: str, live_data: Dict
    ) -> None:
        """Inject Glicko-2 metadata features into game_state for model inference.

        The Glicko-2 tracker provides per-team mu, phi, sigma. We derive
        matchup_uncertainty, rd_asymmetry, and volatility features that the
        LoL model can use for predictions. Modifies game_state in place.
        """
        tracker = self._glicko2_trackers.get(game)
        if tracker is None:
            return
        # Try to find team names from live_data opponents
        # S97 FIX: Use team NAME (lowercased) not numeric ID — glicko2_ratings
        # keys are lowercased team names (e.g. "bilibili gaming"), not PandaScore
        # numeric IDs (e.g. "128947"). Every lookup by ID returned default 1500/350.
        opponents = live_data.get("opponents", [])
        team_a_key = team_b_key = None
        if len(opponents) >= 2:
            if isinstance(opponents[0], dict):
                team_a_key = str(opponents[0].get("opponent", {}).get("name", "")).lower().strip()
            if isinstance(opponents[1], dict):
                team_b_key = str(opponents[1].get("opponent", {}).get("name", "")).lower().strip()
        if not team_a_key or not team_b_key:
            return
        rating_a = tracker.get_rating(team_a_key)
        rating_b = tracker.get_rating(team_b_key)
        # S97-P2: Guard against both teams being unrated defaults (phi=350).
        # Injecting garbage metadata (TSD=0, uncertainty=1.0) is worse than
        # no metadata — the model anchors on noise.
        if rating_a.phi >= 349.0 and rating_b.phi >= 349.0:
            _dflt_key = f"{game}:{team_a_key}:{team_b_key}"
            if _dflt_key not in self._team_fail_logged:
                self._team_fail_logged.add(_dflt_key)
                logger.info("esportsbot_glicko2_default_ratings",
                            game=game, team_a=team_a_key, team_b=team_b_key)
            return
        # S97: Also compute team_strength_diff — without this, LoL ML model
        # anchors on glicko2_est=0.50 because game_state has no TSD signal.
        expected = tracker.expected_score(team_a_key, team_b_key)
        game_state["team_strength_diff"] = expected - 0.5
        game_state["matchup_uncertainty"] = (rating_a.phi + rating_b.phi) / 700.0
        game_state["rd_asymmetry"] = (rating_a.phi - rating_b.phi) / 350.0
        game_state["team_a_volatility"] = rating_a.sigma / 0.06
        game_state["team_b_volatility"] = rating_b.sigma / 0.06

    def get_latency_stats(self) -> Dict[str, float]:
        """Return PandaScore data latency statistics (seconds since last refresh at WS event time)."""
        if not self._latency_samples:
            return {"min": 0.0, "max": 0.0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "samples": 0}
        sorted_s = sorted(self._latency_samples)
        n = len(sorted_s)
        return {
            "min": round(sorted_s[0], 2),
            "max": round(sorted_s[-1], 2),
            "avg": round(sum(sorted_s) / n, 2),
            "p50": round(sorted_s[n // 2], 2),
            "p95": round(sorted_s[int(n * 0.95)], 2),
            "samples": n,
        }

    @staticmethod
    def _detect_tournament_phase(market_data: Dict) -> str:
        """Detect tournament phase from market data. Returns phase string."""
        serie_type = str(market_data.get("serie_type", "")).lower()
        question = str(market_data.get("question", "")).lower()

        # Detect from PandaScore serie_type (primary)
        if serie_type in ("group", "group_stage", "round_robin"):
            return "group"
        if serie_type in ("grand_final", "grand_finals", "final", "finals"):
            return "finals"
        if serie_type in ("bracket", "playoff", "quarterfinal", "semifinal"):
            return "bracket"

        # Fallback: detect from market question text
        if any(kw in question for kw in ("group stage", "group play", "round robin")):
            return "group"
        if any(kw in question for kw in ("grand final", "championship", "finals")):
            return "finals"

        return "unknown"

    _PHASE_STATIC_MULTS = {"group": 0.90, "bracket": 1.0, "finals": 1.15, "unknown": 1.0}

    async def _get_tournament_phase_mult(
        self, market_data: Dict, game: str = "", phase: str = "", db=None,
    ) -> float:
        """Tournament phase confidence multiplier with auto-calibration.

        Static multipliers: group=0.90, bracket=1.0, finals=1.15.
        When ≥ESPORTS_TOURNAMENT_PHASE_MIN_SAMPLES resolved trades exist for a
        phase, blends 70% static + 30% calibrated (from observed Brier score).
        Falls back to static multipliers when data is insufficient.
        """
        if not phase:
            phase = self._detect_tournament_phase(market_data)
        static_mult = self._PHASE_STATIC_MULTS.get(phase, 1.0)

        # Auto-tune if enough resolved trades exist
        if db and game and phase != "unknown":
            try:
                from esports.data.esports_db import get_phase_accuracy
                min_samples = int(getattr(
                    settings, "ESPORTS_TOURNAMENT_PHASE_MIN_SAMPLES", 20
                ))
                acc = await get_phase_accuracy(db, game, phase)
                if acc and acc["total"] >= min_samples:
                    # Brier 0.25 (no-skill) → calibrated=1.0; lower Brier → higher mult
                    calibrated_mult = 1.0 + (0.25 - acc["brier_score"]) * 2.0
                    calibrated_mult = max(0.70, min(1.30, calibrated_mult))
                    blended = 0.7 * static_mult + 0.3 * calibrated_mult
                    logger.debug(
                        "EsportsBot: phase mult auto-tuned",
                        game=game,
                        phase=phase,
                        static=round(static_mult, 3),
                        calibrated=round(calibrated_mult, 3),
                        blended=round(blended, 3),
                        samples=acc["total"],
                    )
                    return blended
            except Exception:
                pass

        return static_mult

    async def _get_recent_form(
        self, team_id: str, game: str,
    ) -> Optional[float]:
        """Get multi-window recent form (win rate) for a team via PandaScore.

        Returns weighted win rate (5/10/20 match windows, weights 0.5/0.3/0.2).
        Cached for 30 minutes per (team_id, game). Returns None if unavailable.
        """
        cache_key = (team_id, game)
        cached = self._team_form_cache.get(cache_key)
        if cached and (time.monotonic() - cached[1]) < self._team_form_ttl:
            return cached[0]

        if not self._pandascore:
            return None

        try:
            ps_id = int(team_id)
            matches = await asyncio.wait_for(
                self._pandascore.get_team_matches(ps_id, game, per_page=20),
                timeout=5.0,
            )
            if not matches or len(matches) < 5:
                return None

            wins = []
            for m in matches:
                if m.team_a_id == ps_id:
                    wins.append(1 if m.score_a > m.score_b else 0)
                elif m.team_b_id == ps_id:
                    wins.append(1 if m.score_b > m.score_a else 0)

            if len(wins) < 5:
                return None

            w5 = sum(wins[:5]) / 5.0
            w10 = sum(wins[:min(10, len(wins))]) / min(10, len(wins))
            w20 = sum(wins) / len(wins)
            form = 0.5 * w5 + 0.3 * w10 + 0.2 * w20

            self._team_form_cache[cache_key] = (form, time.monotonic())
            return form
        except (ValueError, asyncio.TimeoutError):
            return None
        except Exception:
            return None

    async def _pandascore_form_adjustment(
        self, market_data: Dict, base_prob: float, game: str,
    ) -> float:
        """Adjust probability using PandaScore multi-window recent form [T1-Q].

        Applies ±3% adjustment based on form differential between teams.
        Skips dota2 (uses OpenDota instead). Returns base_prob if unavailable.
        """
        if game == "dota2":
            return base_prob  # dota2 uses OpenDota form

        question = str(market_data.get("question", "")).lower()
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if not vs_match:
            return base_prob

        name_a = vs_match.group(1).strip().rstrip(":")
        name_b = vs_match.group(2).strip().rstrip("?").strip()
        for prefix in ("will ", "can ", "does "):
            if name_a.startswith(prefix):
                name_a = name_a[len(prefix):]
        name_a, name_b = self._clean_team_names(name_a, name_b)

        team_a_id = self._match_team_name(name_a)
        team_b_id = self._match_team_name(name_b)
        if not team_a_id or not team_b_id:
            return base_prob

        try:
            form_a, form_b = await asyncio.gather(
                self._get_recent_form(team_a_id, game),
                self._get_recent_form(team_b_id, game),
                return_exceptions=True,
            )
            if isinstance(form_a, Exception) or isinstance(form_b, Exception):
                return base_prob
            if form_a is None or form_b is None:
                return base_prob

            form_diff = form_a - form_b
            form_adj = max(-0.03, min(0.03, form_diff * 0.05))
            adjusted = max(0.05, min(0.95, base_prob + form_adj))

            if abs(form_adj) >= 0.005:
                logger.debug(
                    "esportsbot_form_adjustment", game=game,
                    team_a=name_a, team_b=name_b,
                    form_a=round(form_a, 3), form_b=round(form_b, 3),
                    adj=round(form_adj, 4),
                )
            return adjusted
        except Exception:
            return base_prob

    async def _opendota_form_adjustment(
        self, market_data: Dict, base_prob: float,
    ) -> float:
        """Adjust Dota2 probability using OpenDota recent form data.

        Fetches team form (win rate over last 10 matches) for both teams
        and applies a small ±3% adjustment to the base probability.
        Returns adjusted prob, or base_prob unchanged if data unavailable.

        All API calls are cached (30 min TTL) so first call per team is slow
        (~1.5s) but subsequent calls within the same scan are instant.
        """
        if not self._opendota:
            return base_prob

        question = str(market_data.get("question", "")).lower()
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if not vs_match:
            return base_prob

        name_a = vs_match.group(1).strip().rstrip(":")
        name_b = vs_match.group(2).strip().rstrip("?").strip()
        for prefix in ("will ", "can ", "does "):
            if name_a.startswith(prefix):
                name_a = name_a[len(prefix):]
        name_a, name_b = self._clean_team_names(name_a, name_b)

        try:
            enrich_a, enrich_b = await asyncio.gather(
                asyncio.wait_for(
                    self._opendota.get_team_enrichment(name_a), timeout=5.0,
                ),
                asyncio.wait_for(
                    self._opendota.get_team_enrichment(name_b), timeout=5.0,
                ),
                return_exceptions=True,
            )
            if isinstance(enrich_a, Exception) or isinstance(enrich_b, Exception):
                return base_prob
            if not enrich_a or not enrich_b:
                return base_prob

            form_diff = enrich_a["form_wr"] - enrich_b["form_wr"]
            # Small adjustment: ±3% max, scaled by form difference
            form_adj = max(-0.03, min(0.03, form_diff * 0.05))
            adjusted = max(0.05, min(0.95, base_prob + form_adj))

            if abs(form_adj) >= 0.005:
                logger.debug(
                    "opendota_form_adjustment",
                    team_a=name_a, team_b=name_b,
                    form_a=enrich_a["form_wr"], form_b=enrich_b["form_wr"],
                    adj=round(form_adj, 4),
                )
            return adjusted
        except Exception:
            return base_prob

    async def _aligulac_sc2_blend(
        self, market_data: Dict, glicko2_prob: float,
    ) -> float:
        """Blend Glicko-2 prediction with Aligulac's SC2 rating prediction.

        50% Aligulac + 50% Glicko-2 when Aligulac data available.
        Returns glicko2_prob unchanged if Aligulac unavailable.
        """
        if not self._aligulac:
            return glicko2_prob

        question = str(market_data.get("question", "")).lower()
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if not vs_match:
            return glicko2_prob

        name_a = vs_match.group(1).strip().rstrip(":")
        name_b = vs_match.group(2).strip().rstrip("?").strip()
        for prefix in ("will ", "can ", "does "):
            if name_a.startswith(prefix):
                name_a = name_a[len(prefix):]
        name_a, name_b = self._clean_team_names(name_a, name_b)

        try:
            enrichment = await asyncio.wait_for(
                self._aligulac.get_player_enrichment(name_a, name_b, best_of=3),
                timeout=5.0,
            )
            if enrichment is None:
                return glicko2_prob

            aligulac_prob = enrichment["aligulac_prob_a"]
            # 50/50 blend of Aligulac and our Glicko-2
            blended = 0.5 * aligulac_prob + 0.5 * glicko2_prob
            blended = max(0.05, min(0.95, blended))

            logger.debug(
                "aligulac_sc2_blend",
                player_a=name_a, player_b=name_b,
                aligulac_prob=round(aligulac_prob, 4),
                glicko2_prob=round(glicko2_prob, 4),
                blended=round(blended, 4),
            )
            return blended
        except Exception as exc:
            logger.debug("esportsbot_aligulac_sc2_blend_failed", error=str(exc))
            return glicko2_prob

    async def _ballchasing_rl_adjustment(
        self, market_data: Dict, base_prob: float,
    ) -> float:
        """Adjust RL probability using Ballchasing aggregate team stats.

        Compares goals_per_game and shooting_pct between teams.
        Returns adjusted prob, or base_prob unchanged if data unavailable.
        """
        if not self._ballchasing:
            return base_prob

        question = str(market_data.get("question", "")).lower()
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if not vs_match:
            return base_prob

        name_a = vs_match.group(1).strip().rstrip(":")
        name_b = vs_match.group(2).strip().rstrip("?").strip()
        for prefix in ("will ", "can ", "does "):
            if name_a.startswith(prefix):
                name_a = name_a[len(prefix):]
        name_a, name_b = self._clean_team_names(name_a, name_b)

        try:
            stats_a, stats_b = await asyncio.gather(
                asyncio.wait_for(
                    self._ballchasing.get_team_aggregate_stats(name_a, days_back=30),
                    timeout=10.0,
                ),
                asyncio.wait_for(
                    self._ballchasing.get_team_aggregate_stats(name_b, days_back=30),
                    timeout=10.0,
                ),
                return_exceptions=True,
            )
            if isinstance(stats_a, Exception) or isinstance(stats_b, Exception):
                return base_prob
            if not stats_a or not stats_b:
                return base_prob

            # Compare goals_per_game and shooting_pct
            gpg_diff = stats_a["goals_per_game"] - stats_b["goals_per_game"]
            shoot_diff = stats_a["shooting_pct"] - stats_b["shooting_pct"]

            # Small adjustment: ±3% max, weighted by goals and shooting diff
            adj = max(-0.03, min(0.03, gpg_diff * 0.01 + shoot_diff * 0.001))
            adjusted = max(0.05, min(0.95, base_prob + adj))

            if abs(adj) >= 0.005:
                logger.debug(
                    "ballchasing_rl_adjustment",
                    team_a=name_a, team_b=name_b,
                    gpg_a=stats_a["goals_per_game"], gpg_b=stats_b["goals_per_game"],
                    adj=round(adj, 4),
                )
            return adjusted
        except Exception as exc:
            logger.debug("esportsbot_ballchasing_rl_adjustment_failed", error=str(exc))
            return base_prob

    # Pre-compiled word-boundary patterns for short esports acronyms.
    # Prevents false positives: "lec" in "election", "lcs" in "councils", etc.
    _WB_LOL = tuple(re.compile(p) for p in (r"\blck\b", r"\blec\b", r"\blpl\b", r"\blcs\b", r"\bmsi\b"))
    _WB_CS2 = tuple(re.compile(p) for p in (r"\besl\b", r"\bpgl\b", r"\biem\b"))
    _WB_DOTA2 = (re.compile(r"\bdpc\b"), re.compile(r"\bthe international\s+\d"), re.compile(r"\bti\b"))
    _WB_COD = (re.compile(r"\bcdl\b"),)
    _WB_SC2 = tuple(re.compile(p) for p in (r"\bgsl\b", r"\basl\b"))

    @staticmethod
    def _detect_game(question: str) -> Optional[str]:
        """Detect game title from market question text.

        Uses word-boundary regex for short acronyms (lck, lec, lpl, lcs, msi,
        esl, pgl, iem, dpc, cdl, gsl, asl) to prevent false positives inside
        common words like "election", "stablecoins", "councils", etc.
        """
        q = question.lower()
        # LoL
        if any(kw in q for kw in ("league of legends", "lol:", "lol ", " lol ")):
            return "lol"
        if any(p.search(q) for p in EsportsBot._WB_LOL):
            return "lol"
        # CS2
        if any(kw in q for kw in ("counter-strike", "cs2", "csgo", "blast premier")):
            return "cs2"
        if any(p.search(q) for p in EsportsBot._WB_CS2):
            return "cs2"
        # Dota2
        if any(kw in q for kw in ("dota 2", "dota2", "dota:")):
            return "dota2"
        if any(p.search(q) for p in EsportsBot._WB_DOTA2):
            return "dota2"
        # Valorant
        if any(kw in q for kw in ("valorant", "vct ", "champions tour")):
            return "valorant"
        # CoD
        if any(kw in q for kw in ("call of duty", "cod ")):
            return "cod"
        if any(p.search(q) for p in EsportsBot._WB_COD):
            return "cod"
        # R6
        if any(kw in q for kw in ("rainbow six", "r6 ", "six invitational")):
            return "r6"
        # SC2
        if any(kw in q for kw in ("starcraft", "sc2 ", "sc2:", "brood war")):
            return "sc2"
        if any(p.search(q) for p in EsportsBot._WB_SC2):
            return "sc2"
        # RL
        if any(kw in q for kw in ("rocket league", "rlcs")):
            return "rl"
        return None

    @staticmethod
    def _classify_market_type(question: str) -> str:
        """Classify market type from question text."""
        q = question.lower()
        if any(kw in q for kw in ("map ", "game 1", "game 2", "game 3", "game 4", "game 5")):
            return "map_winner"
        # S151: Check for H2H pattern BEFORE tournament keywords.
        # Questions like "team a vs team b (bo1) - circuito desafiante regular season"
        # were misclassified as tournament_winner because "season" matched.
        # If "vs" or "versus" is present, it's a head-to-head match.
        _has_h2h = " vs " in q or " versus " in q
        if not _has_h2h and any(kw in q for kw in (
            "tournament", "championship", "champion", "split winner", "season",
            "win msi", "win worlds", "msi 20", "worlds 20",
            "league winner", "win lpl", "win lck", "win lec", "win lcs", "win vct",
            "qualify", "advance to", "make it to",
        )):
            return "tournament_winner"
        if any(kw in q for kw in ("total maps", "over", "under", "maps played")):
            return "total_maps"
        if any(kw in q for kw in ("first blood", "first kill")):
            return "first_blood"
        if any(kw in q for kw in ("mvp", "kills", "assists", "be said", "signs for")):
            return "props"
        return "match_winner"

    async def _execute_esports_trade(self, opp: Dict) -> bool:
        """Execute trade with maker-first, taker-fallback strategy.

        Returns True if a trade was successfully placed, False otherwise.

        Includes A10 (pre-update exposure), A6 (uncertainty-scaled sizing),
        A5 (near-expiry boost), A8 (drawdown Kelly reduction),
        Session 83: conformal conservative sizing.
        Series: S-T size override for correlated series bets.
        """
        # S143: Hard-coded safety floors — cannot be overridden by env vars.
        # Backstop against misconfiguration that allowed 10K-share / penny-price entries.
        _entry_price = float(opp.get("price", 0))
        _HARD_MIN_PRICE = 0.03
        _HARD_MAX_PRICE = 0.97
        if _entry_price < _HARD_MIN_PRICE or _entry_price > _HARD_MAX_PRICE:
            logger.warning(
                "esportsbot_hard_price_floor",
                market_id=opp.get("market_id", "")[:16],
                price=round(_entry_price, 4),
            )
            return False

        # S133: Penny/extreme price guard — configurable layer on top of hard floor
        _esports_min_price = float(getattr(settings, "ESPORTS_MIN_ENTRY_PRICE", 0.05))
        _esports_max_price = float(getattr(settings, "ESPORTS_MAX_ENTRY_PRICE", 0.95))
        if _entry_price < _esports_min_price or _entry_price > _esports_max_price:
            logger.info(
                "esports_extreme_price_rejected",
                market_id=opp.get("market_id", "")[:16],
                price=round(_entry_price, 4),
                min_price=_esports_min_price,
                max_price=_esports_max_price,
            )
            return False

        # S132 EB-3: Opposing-side guard — block entry if we hold or ever entered opposite side
        _market_id = opp.get("market_id", "")
        _side_upper = str(opp.get("side", "")).upper()
        _opposite = "NO" if _side_upper == "YES" else "YES"
        og = getattr(self.base_engine, "order_gateway", None)
        if og is not None and og.has_open_position(self.bot_name, str(_market_id)):
            _pos_key = f"{self.bot_name}:{str(_market_id)}"
            _existing = getattr(og, "_position_details", {}).get(_pos_key, {})
            if _existing and str(_existing.get("side", "")).upper() == _opposite:
                logger.info("esports_opposing_side_blocked", market_id=_market_id[:16], side=opp["side"], existing=_opposite)
                return False
        if (_market_id, _opposite) in self._entered_market_sides:
            logger.info("esports_opposing_side_blocked_historical", market_id=_market_id[:16], side=opp["side"], prior=_opposite)
            return False

        # Retrieve event_data from prediction cache for ENTRY persistence
        _cached_pred = self._prediction_cache.get(opp.get("market_id", ""), {})
        _event_data = _cached_pred.get("event_data") or {}
        # GAP-1: Pass CLOB volume for fill probability model
        _event_data["volume_24h"] = opp.get("_clob_volume", 0.0)

        # Series S-T allocation override — size already computed by
        # _series_smoczynski_tomkins_allocate(), skip Kelly sizing
        st_override = opp.pop("_st_size_override", None)
        if st_override is not None and st_override >= 1.0:
            game = opp.get("game", "")
            if not game:
                logger.warning("esportsbot_st_empty_game", market_id=opp.get("market_id", ""))
                return False
            # S103: Track USD cost, not shares
            _st_cost = opp["price"] * st_override
            # S156: Re-verify game cap under _trade_lock (gate at L748 is pre-lock)
            _st_caps = self._get_exposure_caps()
            if game and self._game_exposure.get(game, 0.0) + _st_cost > _st_caps["per_game"]:
                logger.info("esportsbot_st_exposure_lock_reject", game=game,
                            exposure=round(self._game_exposure.get(game, 0.0), 2),
                            st_cost=round(_st_cost, 2),
                            cap=round(_st_caps["per_game"], 2))
                return False
            self._game_exposure[game] = self._game_exposure.get(game, 0.0) + _st_cost
            if game:
                self._market_game[opp["market_id"]] = game
            # S132 EB-5: Persist confidence + signal_quality for S-T path
            _event_data["confidence"] = round(opp.get("confidence", 0.0), 4)
            _event_data["signal_quality"] = round(float(opp.get("_signal_quality", 1.0)), 4)
            # S136 Phase 3A: Persist entry model_prob and edge for edge-based exits
            _st_model_prob = opp.get("prediction", opp.get("confidence", 0.5))
            _st_edge_val = opp.get("edge", 0.0)
            _event_data["entry_model_prob"] = round(float(_st_model_prob), 6)
            _event_data["entry_edge"] = round(float(_st_edge_val), 6)
            _event_data["market_type"] = opp.get("market_type", "match_winner")
            # S145: Populate signal meta for auto-store in place_order()
            self._pending_signal_meta[str(opp["market_id"])] = {
                "signal_direction": opp["side"],
                "signal_confidence": round(opp.get("confidence", 0.0), 4),
                "signal_source": f"esports_{opp.get('game', 'unknown')}",
                "signal_multiplier": round(float(opp.get("_signal_quality", 1.0)), 4),
                "order_flow_direction": None,
                "order_flow_multiplier": round(float(_st_edge_val), 4) if _st_edge_val else None,
                "trends_signal": opp.get("game"),
                "trends_multiplier": None,
            }
            order = await self.place_order(
                market_id=opp["market_id"],
                token_id=opp["token_id"],
                side=opp["side"],
                size=st_override,
                price=opp["price"],
                confidence=opp["confidence"],
                event_data=_event_data,
            )
            if order and order.get("success"):
                # S132 EB-3: Track entered side for opposing-side guard
                self._entered_market_sides.add((opp["market_id"], str(opp["side"]).upper()))
                # S136 Phase 3A: Cache entry edge data for exit evaluation
                self._entry_edge_cache[opp["market_id"]] = {
                    "model_prob": float(_st_model_prob),
                    "edge": float(_st_edge_val),
                    "market_type": opp.get("market_type", "match_winner"),
                }
                if game:
                    _db = getattr(self.base_engine, "db", None)
                    if _db is not None:
                        try:
                            await _inc_daily(_db, "EsportsBot", f"game_{game}", _st_cost)
                        except Exception as exc:
                            logger.warning("esports_series_counter_write_failed", error=str(exc))
                logger.info(
                    "esports_series_trade_executed",
                    type=opp.get("type"),
                    game=game,
                    market_id=opp["market_id"],
                    side=opp["side"],
                    size=round(st_override, 2),
                    edge=opp.get("edge"),
                    series_score=opp.get("series_score"),
                )
                return True
            else:
                self._game_exposure[game] = max(
                    0.0, self._game_exposure.get(game, 0.0) - _st_cost
                )
                return False

        confidence = opp["confidence"]

        # S94: Conformal sizing handled by BotBankrollManager width-based dampening (S91).
        # The old conservative_prob() approach here pushed confidence from ~0.52 to ~0.02,
        # killing ALL trades. Removed — bankroll_manager handles conformal correctly.

        # A5: Near-expiry confidence boost
        confidence = self._apply_expiry_boost(confidence, opp)

        # A6: Uncertainty-scaled sizing — dampen when Glicko-2 phi is high
        # S100b (Phase 3): Use conformal conservative bounds when available
        cp = self._conformal_per_game.get(opp.get("game", ""))
        if cp and cp.is_fitted:
            import numpy as np
            _prob_arr = np.array([[opp["prediction"]]])
            _conservative = float(cp.conservative_prob(_prob_arr)[0])
            _conservative_edge = abs(_conservative - opp["price"])
            phi_factor = min(1.0, _conservative_edge / max(opp["edge"], 0.01))
        else:
            phi_factor = self._get_phi_sizing_factor(opp)

        # A8: Drawdown Kelly reduction
        dd_factor = self._get_drawdown_kelly_factor()

        # CLV-gated sizing override [WS2]: scale max_bet based on CLV tier
        _clv_max_override = None
        if getattr(settings, "ESPORTS_CLV_SCALING_ENABLED", False):
            _tier = self._clv_scaling_tier
            if _tier == "aggressive":
                _clv_max_override = float(getattr(settings, "ESPORTS_SCALE_AGGRESSIVE_MAX_BET", 300.0))
            elif _tier == "moderate":
                _clv_max_override = float(getattr(settings, "ESPORTS_SCALE_MODERATE_MAX_BET", 200.0))
            else:
                _clv_max_override = float(getattr(settings, "ESPORTS_SCALE_CONSERVATIVE_MAX_BET", 100.0))

        size = await self.calculate_bot_position_size(
            confidence, opp["price"], category="esports"
        )
        if size <= 0:
            logger.warning(
                "esportsbot_sizing_killed_at_bankroll",
                market_id=opp.get("market_id"),
                game=opp.get("game", ""),
                confidence=round(confidence, 4),
                price=round(opp["price"], 4),
                edge=round(confidence - opp["price"], 4),
                phi_factor=round(phi_factor, 4),
                dd_factor=round(dd_factor, 4),
                signal_quality=round(float(opp.get("_signal_quality", 1.0)), 4),
            )
            return False

        # Apply CLV max override after base sizing
        if _clv_max_override is not None and size > _clv_max_override:
            size = _clv_max_override

        # Apply A6 + A8 + per-game Kelly multiplier + edge decay + S131 signal quality sizing
        _game_mult = self._game_kelly_mult.get(opp.get("game", ""), 1.0)
        _decay_mult = self._get_edge_decay_sizing_mult(opp.get("game", ""))
        # S131: Signal quality scales SIZE, not probability. Low-trust → smaller bet.
        _sq_sizing = float(opp.get("_signal_quality", 1.0))
        size = size * phi_factor * dd_factor * _game_mult * _decay_mult * _sq_sizing

        # Upset risk scaling [T1-D]: reduce sizing for volatile favorites
        if getattr(settings, "ESPORTS_UPSET_RISK_ENABLED", True):
            _cache = self._prediction_cache.get(opp.get("market_id", ""), {})
            _ed = _cache.get("event_data", {})
            if confidence > 0.60:
                # Favored team — check its volatility
                _vol = _ed.get("team_a_volatility", 1.0) if opp["side"] == "YES" else _ed.get("team_b_volatility", 1.0)
                if _vol > 1.5:
                    _upset_factor = max(0.5, 1.0 - (_vol - 1.0) * 0.25)
                    size *= _upset_factor
                    logger.debug(
                        "esportsbot_upset_risk_scaling", market_id=opp.get("market_id"),
                        volatility=round(_vol, 3), factor=round(_upset_factor, 3),
                        side=opp["side"],
                    )
            elif confidence < 0.55:
                # Underdog — boost stable underdogs
                _vol = _ed.get("team_a_volatility", 1.0) if opp["side"] == "YES" else _ed.get("team_b_volatility", 1.0)
                if _vol < 0.8:
                    size *= 1.10

        # S136 Phase 2A: Baker-McHale shadow mode — compute new sizing alongside old
        # Will replace old cascade after 48h validation
        _bm_price = opp["price"]
        _opp_edge = opp.get("edge", 0.0)
        _opp_conf = opp.get("confidence", 0.5)

        # sigma_model from Glicko-2 phi + conformal width + model agreement
        _phi_a = opp.get("_phi_a", 200.0)
        _phi_b = opp.get("_phi_b", 200.0)
        _phi_norm = ((_phi_a + _phi_b) / 2.0) / 350.0  # normalized [0, 1]
        _conf_width = opp.get("_conformal_width", 0.15)
        _agreement_std = opp.get("_agreement_stdev", 0.10)
        _sigma_model = max(0.08, _phi_norm * 0.15 + _conf_width * 0.5 + _agreement_std * 0.5)

        # Adjust sigma by signal quality (SQ >= 0.30 gate already passed)
        _sq_val = opp.get("_signal_quality", 0.7)
        if _sq_val > 0:
            _sigma_model = _sigma_model / _sq_val

        # Baker-McHale shrinkage
        _k_bm = _opp_edge ** 2 / (_opp_edge ** 2 + _sigma_model ** 2) if (_opp_edge ** 2 + _sigma_model ** 2) > 0 else 0.0
        _base_kelly = float(getattr(settings, "ESPORTS_KELLY_DEFAULT_FRACTION", 0.25))
        # S142: Floor lowered from 0.15 to 0.005 — old floor dominated every sample
        # (k_bm*0.25 ranges 0.005-0.084, all below 0.15). Every trade got $300 flat.
        # New floor: min size = 0.005 * $20K / $0.50 = $50 (above $10 min trade).
        _eff_kelly = max(0.005, _k_bm * _base_kelly)

        # Drawdown modifies bankroll, not size
        _dd_bm = dd_factor  # reuse the already-computed daily drawdown factor
        _capital_bm = float(getattr(settings, "ESPORTS_TOTAL_CAPITAL", 20000.0))
        _eff_bankroll = _capital_bm * _dd_bm
        _bm_size = _eff_kelly * _eff_bankroll / max(_bm_price, 0.01)

        # Hard constraints (Layer 2)
        _max_bet_bm = float(getattr(settings, "ESPORTS_MAX_BET_USD", 300.0))
        _bm_size = min(_bm_size, _max_bet_bm / max(_bm_price, 0.01))

        # Log shadow comparison
        _old_size = size  # current cascade result
        logger.info(
            "esportsbot_sizing_shadow",
            market_id=opp.get("market_id", ""),
            game=opp.get("game", "?"),
            old_size=round(float(_old_size) * _bm_price, 2),  # USD
            new_size_bm=round(float(_bm_size) * _bm_price, 2),  # USD
            ratio=round(float(_bm_size) / max(float(_old_size), 0.01), 2) if _old_size > 0 else 0.0,
            k_bm=round(_k_bm, 4),
            sigma_model=round(_sigma_model, 4),
            eff_kelly=round(_eff_kelly, 4),
            edge=round(_opp_edge, 4),
        )
        # S141: env-flag cutover — ESPORTS_BM_ACTIVE=true activates BM sizing
        if getattr(settings, "ESPORTS_BM_ACTIVE", False):
            size = _bm_size
        else:
            size = min(size, _bm_size)  # shadow: never exceed cascade

        # Position re-entry: cap size at remaining room under per-market cap
        _max_size_override = opp.get("max_size_override")
        if _max_size_override is not None and size > _max_size_override:
            size = _max_size_override

        # P6: Enforce ESPORTS_MAX_BET_USD cap (cost = price * size in shares)
        price = opp["price"]
        _max_bet = float(getattr(settings, "ESPORTS_MAX_BET_USD", 300.0))
        _cost = price * size
        if _cost > _max_bet:
            size = _max_bet / max(price, 0.01)
            _cost = price * size  # S133: Recalculate after max-bet cap

        # S143: Hard-coded max shares and max cost — non-overridable backstop
        _HARD_MAX_SHARES = 3000.0
        _HARD_MAX_COST = 500.0
        if size > _HARD_MAX_SHARES:
            logger.warning("esportsbot_hard_max_shares",
                           market_id=opp.get("market_id", "")[:16],
                           size=round(size, 1), cap=_HARD_MAX_SHARES)
            size = _HARD_MAX_SHARES
            _cost = price * size
        if _cost > _HARD_MAX_COST:
            logger.warning("esportsbot_hard_max_cost",
                           market_id=opp.get("market_id", "")[:16],
                           cost=round(_cost, 2), cap=_HARD_MAX_COST)
            size = _HARD_MAX_COST / max(price, 0.01)
            _cost = price * size

        # GAP-4: Min trade floor — reject dust positions
        _min_trade = float(getattr(settings, "ESPORTS_MIN_TRADE_USD", 10.0))
        if _cost < _min_trade:
            logger.info(
                "esportsbot_below_min_trade",
                market_id=opp.get("market_id"),
                game=opp.get("game", ""),
                cost_usd=round(_cost, 2),
                min_trade_usd=_min_trade,
            )
            return False

        if size < 0.10:
            logger.info(
                "esportsbot_size_crushed",
                market_id=opp.get("market_id"),
                game=opp.get("game", ""),
                base_size_usd=round(confidence * 200, 2),
                final_size_shares=round(size, 4),
                phi_factor=round(phi_factor, 4),
                dd_factor=round(dd_factor, 4),
                game_kelly_mult=round(_game_mult, 4),
                edge_decay_mult=round(_decay_mult, 4),
            )
            return False

        # A10: Pre-update exposure BEFORE placing order (race condition fix)
        # S103: Track USD cost (price * size), not shares — units must match ESPORTS_MAX_GAME_EXPOSURE (USD)
        game = opp.get("game", "")
        if not game:
            logger.warning("esportsbot_empty_game_reject", market_id=opp.get("market_id", ""))
            return False
        _entry_cost = price * size
        # S156: Re-verify game cap under _trade_lock (gate at L748 is pre-lock)
        _caps_lock = self._get_exposure_caps()
        if self._game_exposure.get(game, 0.0) + _entry_cost > _caps_lock["per_game"]:
            logger.info("esportsbot_exposure_lock_reject", game=game,
                        exposure=round(self._game_exposure.get(game, 0.0), 2),
                        entry_cost=round(_entry_cost, 2),
                        cap=round(_caps_lock["per_game"], 2))
            return False
        self._game_exposure[game] = self._game_exposure.get(game, 0.0) + _entry_cost
        # Persist market→game for reliable exit decrement (outlives prediction_cache 1h TTL)
        if game:
            self._market_game[opp["market_id"]] = game

        # S136 Phase 8B: Correlation-aware tournament cap
        _tournament_name = opp.get("tournament", "")
        if _tournament_name:
            _caps_exec = self._get_exposure_caps()
            _game_cap = _caps_exec["per_game"]
            _tournament_cap = _caps_exec["per_tournament"]
            _n_games_in_tournament = len(set(
                g for g, exp in self._game_exposure.items() if exp > 0
            ))
            _rho = 0.25  # estimated correlation between games in same tournament
            # S162: (1-rho) inside sqrt — portfolio variance scaling, not flat multiplier
            _corr_cap = _game_cap * math.sqrt(max(1, _n_games_in_tournament) * (1.0 - _rho))
            _tournament_cap = min(_tournament_cap, _corr_cap)
            _current_tourn_exp = self._tournament_exposure.get(_tournament_name, 0.0)
            if _current_tourn_exp + _entry_cost > _tournament_cap:
                # Undo game exposure pre-update
                self._game_exposure[game] = max(
                    0.0, self._game_exposure.get(game, 0.0) - _entry_cost
                )
                logger.info("esportsbot_tournament_cap_hit",
                            tournament=_tournament_name,
                            exposure=round(_current_tourn_exp, 2),
                            entry_cost=round(_entry_cost, 2),
                            cap=round(_tournament_cap, 2),
                            corr_cap=round(_corr_cap, 2))
                return False

        # S132 EB-5: Persist confidence + signal_quality for bucketed WR analysis
        _event_data["confidence"] = round(confidence, 4)
        _event_data["signal_quality"] = round(_sq_sizing, 4)
        # S136 Phase 3A: Persist entry model_prob and edge for edge-based exits
        _entry_model_prob = opp.get("prediction", opp.get("confidence", 0.5))
        _entry_edge_val = opp.get("edge", 0.0)
        _event_data["entry_model_prob"] = round(float(_entry_model_prob), 6)
        _event_data["entry_edge"] = round(float(_entry_edge_val), 6)
        _event_data["market_type"] = opp.get("market_type", "match_winner")

        # S145: Populate signal meta for auto-store in place_order()
        self._pending_signal_meta[str(opp["market_id"])] = {
            "signal_direction": opp["side"],
            "signal_confidence": round(confidence, 4),
            "signal_source": f"esports_{opp.get('game', 'unknown')}",
            "signal_multiplier": round(float(opp.get("_signal_quality", 1.0)), 4),
            "order_flow_direction": None,
            "order_flow_multiplier": round(float(_entry_edge_val), 4) if _entry_edge_val else None,
            "trends_signal": opp.get("game"),
            "trends_multiplier": None,
        }

        order = await self.place_order(
            market_id=opp["market_id"],
            token_id=opp["token_id"],
            side=opp["side"],
            size=size,
            price=opp["price"],
            confidence=confidence,
            event_data=_event_data,
        )

        if order and order.get("success"):
            # S132 EB-3: Track entered side for opposing-side guard
            self._entered_market_sides.add((opp["market_id"], str(opp["side"]).upper()))
            # S136 Phase 3A: Cache entry edge data for exit evaluation
            self._entry_edge_cache[opp["market_id"]] = {
                "model_prob": float(_entry_model_prob),
                "edge": float(_entry_edge_val),
                "market_type": opp.get("market_type", "match_winner"),
            }
            # Update tournament exposure
            tournament = opp.get("tournament", "")
            if tournament:
                self._tournament_exposure[tournament] = (
                    self._tournament_exposure.get(tournament, 0.0) + _entry_cost
                )
            # Write-through: persist game exposure (USD) to daily_counters for restart recovery
            # S133: Retry once on failure — without DB write, restart loses the increment
            if game:
                _db = getattr(self.base_engine, "db", None)
                if _db is not None:
                    for _ct_attempt in range(2):
                        try:
                            await _inc_daily(_db, "EsportsBot", f"game_{game}", _entry_cost)
                            break
                        except Exception as _exc:
                            logger.debug("esports_game_counter_write_retry", attempt=_ct_attempt, error=str(_exc))
                            if _ct_attempt == 1:
                                logger.warning("esports_game_counter_write_failed", error=str(_exc))

            logger.info(
                "EsportsBot trade executed",
                type=opp.get("type"),
                game=game,
                market_type=opp.get("market_type"),
                market_id=opp["market_id"],
                side=opp["side"],
                price=opp["price"],
                confidence=round(confidence, 3),
                edge=opp.get("edge"),
                size=round(size, 2),
                game_exposure=round(self._game_exposure.get(game, 0.0), 2),
                phi_factor=phi_factor,
                dd_factor=dd_factor,
                game_kelly_mult=_game_mult,
                edge_decay_mult=_decay_mult,
                signal_quality=round(_sq_sizing, 4),
            )
            return True
        else:
            # A10: Rollback exposure (USD) if order failed
            self._game_exposure[game] = max(
                0.0, self._game_exposure.get(game, 0.0) - _entry_cost
            )
            # S157: Return fail_code for reason-specific cooldown
            _fail_code = order.get("fail_code", "unknown") if order else "unknown"
            logger.info("esportsbot_exec_fail", market_id=opp.get("market_id", ""),
                        fail_code=_fail_code, error=str(order.get("error", ""))[:80] if order else "no_order")
            self._last_fail_code = _fail_code
            return False

    # ── Sizing + confidence helpers ─────────────────────────────────────

    @staticmethod
    def _apply_expiry_boost(confidence: float, opp: Dict) -> float:
        """A5: Boost confidence for markets close to expiry.

        <6h to expiry: 1.5x confidence boost (capped at 0.95)
        <24h to expiry: 1.2x boost
        Otherwise: no change.

        Reads end_date_iso from opp dict (set by analyze_opportunity from market data).
        """
        end_date = opp.get("end_date_iso")
        if not end_date:
            return confidence
        try:
            if isinstance(end_date, str):
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            else:
                end_dt = end_date
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            hours_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_left <= 0:
                return confidence
            if hours_left < 6:
                return min(0.95, confidence * 1.5)
            if hours_left < 24:
                return min(0.95, confidence * 1.2)
        except Exception as exc:
            logger.debug("esportsbot_expiry_boost_failed", error=str(exc))
        return confidence

    def _get_phi_sizing_factor(self, opp: Dict) -> float:
        """A6: Scale position size by Glicko-2 rating certainty (phi).

        Uses max phi of both teams from the prediction cache.
        Low phi (< 100) = high certainty → full size (1.0)
        Medium phi (100-200) → 0.8x
        High phi (200-350) → 0.5x
        Very high phi (>= 350) → 0.3x (near-default rating)
        """
        game = opp.get("game", "")
        market_id = opp.get("market_id", "")
        tracker = self._glicko2_trackers.get(game)
        if tracker is None:
            return 0.7  # No tracker → conservative default

        # Extract team names from the prediction cache or re-extract from question
        # We store team IDs during _get_glicko2_prediction, but not in opp dict.
        # Use the prediction cache which has the game state info.
        cached = self._prediction_cache.get(market_id, {})
        if not cached:
            return 0.7

        # Try to get phi by looking up the teams in the tracker.
        # The prediction involved two teams — find their max phi.
        # Since we don't store team IDs in the cache, scan tracker ratings
        # for teams that contributed to this prediction.
        # Approximation: use the confidence/edge spread as proxy for phi.
        # High edge + high confidence = low phi (certain). Low edge = high phi.
        edge = opp.get("edge", 0.0)
        confidence = opp.get("confidence", 0.5)

        # S100b: While BetaCalibrator unfitted, use generous floor (0.8) — raw
        # Glicko2 probs produce legitimate high-phi edges; don't penalize sizing
        # based on stale accuracy data.
        _cal_phi = self._beta_calibrators.get(game)
        _phi_floor = 0.8 if (_cal_phi and not _cal_phi._fitted) else 0.5

        # Map edge magnitude to phi proxy: >0.15 edge → phi<100, <0.06 → phi>300
        if edge >= 0.15 and confidence >= 0.65:
            return 1.0   # High certainty
        if edge >= 0.10 and confidence >= 0.58:
            return 0.8   # Medium-high certainty
        if edge >= 0.06:
            return max(0.7, _phi_floor)   # Medium certainty
        return _phi_floor  # Low certainty (barely above min_edge)

    def _compute_signal_quality(self, game: str, market_id: str) -> tuple:
        """S127/S131: Compute signal quality score [0.30, 1.0] from existing model signals.

        Measures how much we TRUST the prediction, not what the prediction IS.
        S131: Used as a SIZING multiplier, not confidence multiplier.
        Components: model agreement, calibration status, Glicko-2 uncertainty,
        enrichment depth, rolling Brier score.

        Returns (signal_quality, components_dict) for logging.
        """
        import statistics as _stats

        cached = self._prediction_cache.get(market_id, {})
        _ed = cached.get("event_data", {})
        _em = _ed.get("_enrich_meta", {})

        # 1. Model agreement (weight 0.30): how well do independently-computed models agree?
        _probs = []
        _final_prob = _ed.get("model_prob")
        if _final_prob is not None:
            _probs.append(float(_final_prob))
        _glicko2_est = cached.get("glicko2_est")
        if _glicko2_est is not None:
            _probs.append(float(_glicko2_est))
        if _em.get("xgb_raw") is not None:
            _probs.append(float(_em["xgb_raw"]))
        if _em.get("cb_prob") is not None:
            _probs.append(float(_em["cb_prob"]))
        if len(_probs) >= 2:
            _stdev_raw = _stats.stdev(_probs)
            _agreement = max(0.0, min(1.0, 1.0 - _stdev_raw / 0.20))
        else:
            # S131: Single model is not "uncertain" — it's just one source.
            # 0.70 = "no contradicting signal" (was 0.50 = coin-flip penalty).
            _stdev_raw = 0.10
            _agreement = 0.70

        # 2. Calibration score (weight 0.25): is this game calibrated?
        _beta = self._beta_calibrators.get(game)
        _platt = self._online_platt_per_game.get(game)
        _beta_fit = _beta is not None and _beta._fitted
        _platt_fit = _platt is not None and getattr(_platt, "is_fitted", False)
        if _beta_fit and _platt_fit:
            _calibration = 1.0
        elif _beta_fit or _platt_fit:
            _calibration = 0.7
        else:
            # S138: Unfitted = low confidence in calibration, not neutral (was 0.50).
            _calibration = 0.25

        # 3. Uncertainty (weight 0.20): Glicko-2 matchup uncertainty
        # matchup_uncertainty = (phi_a + phi_b) / 700; 0 = certain, 1 = unknown
        _mu = float(_ed.get("matchup_uncertainty", 0.5))
        _uncertainty = max(0.0, min(1.0, 1.0 - _mu))

        # 4. Enrichment depth (weight 0.15): how many enrichment layers fired?
        _enrich_count = sum(1 for k in ("form_applied", "tabpfn_applied", "lan_applied", "bo_applied")
                           if _em.get(k))
        if _em.get("xgb_raw") is not None:
            _enrich_count += 1
        if _em.get("cb_prob") is not None:
            _enrich_count += 1
        _enrichment = min(1.0, _enrich_count / 3.0)

        # 5. Brier component (weight 0.10): rolling game-level Brier
        # S131: Default 0.15 (was 0.25). "No data" ≠ "worst possible".
        # 0.15 → score 0.40 (moderate trust), 0.25 → score 0.0 (zero trust).
        _brier = self._game_brier_cache.get(game, 0.15)
        _brier_score = max(0.0, min(1.0, 1.0 - _brier / 0.25))

        # S136: Reweighted — calibration most important for Kelly sizing,
        # Brier most holistic metric, model agreement overlaps uncertainty.
        _sq = (
            0.20 * _agreement
            + 0.30 * _calibration
            + 0.20 * _uncertainty
            + 0.10 * _enrichment
            + 0.20 * _brier_score
        )
        _sq = max(0.30, min(1.0, _sq))

        _components = {
            "agreement": round(_agreement, 4),
            "calibration": round(_calibration, 4),
            "uncertainty": round(_uncertainty, 4),
            "enrichment": round(_enrichment, 4),
            "brier": round(_brier_score, 4),
            "agreement_stdev": round(_stdev_raw, 4),  # S141: raw stdev for BM sigma
        }
        return _sq, _components

    def _update_streaming_on_resolution(
        self, game: str, predicted: float, actual: float,
    ) -> None:
        """S100b: Feed resolved prediction into streaming calibrators (ADWIN + OnlinePlatt).

        Called from resolution/accuracy tracking. Updates:
        1. Per-game ADWIN drift detector with Brier contribution
        2. Per-game Online Platt calibrator with (predicted, outcome) pair
        """
        # ADWIN drift detection (Phase 4)
        try:
            from river.drift import ADWIN
            if game not in self._adwin_per_game:
                self._adwin_per_game[game] = ADWIN(delta=0.002)
            brier_contribution = (predicted - actual) ** 2
            self._adwin_per_game[game].update(brier_contribution)
            if self._adwin_per_game[game].drift_detected:
                logger.warning("esportsbot_adwin_drift", game=game,
                               estimation=round(self._adwin_per_game[game].estimation, 4),
                               width=self._adwin_per_game[game].width)
                # S136 Phase 9D: Wire ADWIN drift to retrain flag
                self._adwin_drift_detected[game] = True
                logger.warning("esportsbot_adwin_drift_flagged", game=game)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("esportsbot_adwin_update_failed", game=game, error=str(exc))

        # S136 Phase 9C: Track divergence accuracy for adaptive caps
        try:
            if game not in self._divergence_accuracy:
                self._divergence_accuracy[game] = {}
            # Use |predicted - 0.5| as proxy for divergence magnitude
            _proxy_div = abs(predicted - 0.5)
            _correct = int((predicted >= 0.5) == (actual >= 0.5))
            # Bin to nearest 0.05
            _bin_key = f"{round(_proxy_div / 0.05) * 0.05:.2f}"
            if _bin_key not in self._divergence_accuracy[game]:
                self._divergence_accuracy[game][_bin_key] = []
            _bin_list = self._divergence_accuracy[game][_bin_key]
            _bin_list.append(_correct)
            # Keep last 200 samples per bin
            if len(_bin_list) > 200:
                self._divergence_accuracy[game][_bin_key] = _bin_list[-200:]
        except Exception:
            pass  # Non-critical tracking

        # Online Platt update (Phase 2)
        _platt = self._online_platt_per_game.get(game)
        if _platt:
            _platt.update(predicted, int(actual))

    def _get_adaptive_div_cap(self, game: str) -> float:
        """S136 Phase 9C: Per-game divergence cap from accuracy tracking."""
        bins = self._divergence_accuracy.get(game, {})
        if not bins or sum(len(v) for v in bins.values()) < 30:
            # S151: Widen cap for games with <50 calibration samples.
            # These games need trades to build calibration data. BM sizing
            # controls risk via high sigma → tiny bets. As n grows past 50,
            # the adaptive logic below takes over and tightens automatically.
            _beta = self._beta_calibrators.get(game)
            _n_cal = _beta._n_samples if _beta else 0
            if _n_cal < 50:
                return float(getattr(settings, "ESPORTS_LOW_SAMPLE_DIV_CAP", 0.35))
            return 0.25  # no accuracy data but enough calibration samples
        # Find highest threshold where accuracy drops below 55%
        for threshold in [0.25, 0.20, 0.15, 0.10, 0.05]:
            bin_key = f"{threshold:.2f}"
            samples = bins.get(bin_key, [])
            if len(samples) >= 5:
                acc = sum(samples) / len(samples)
                if acc < 0.55:
                    # This bin is unprofitable — cap just below it
                    return max(0.05, threshold - 0.05)
        return 0.25  # all bins profitable

    async def _check_kelly_graduation(self, db) -> None:
        """A3: Continuous Kelly scaling with de-graduation.

        Runs every 10 scans. Replaces the old one-shot 0.25→0.30 threshold.
        Scale = clamp(2.0 - avg_brier/0.25, 0.80, 1.30) → effective Kelly [0.20, 0.325].
        De-graduation: if recent 20-trade Brier > degrade_threshold → cap at 0.20.
        """
        if db is None:
            return
        try:
            _acc_50 = await self._get_cached_rolling_accuracy(db, last_n=50)
            total_resolved = 0
            weighted_brier = 0.0

            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                acc = _acc_50.get(game)
                if acc and acc["total"] > 0:
                    total_resolved += acc["total"]
                    weighted_brier += acc["brier_score"] * acc["total"]

            if total_resolved < 50:
                return  # Not enough data for reliable scaling

            avg_brier = weighted_brier / total_resolved

            # Continuous scaling: lower Brier → higher multiplier
            # scale = clamp(2.0 - avg_brier/0.25, 0.80, 1.30)
            scale = max(0.80, min(1.30, 2.0 - avg_brier / 0.25))

            base_kelly = float(getattr(settings, "ESPORTS_KELLY_DEFAULT_FRACTION", 0.25))
            new_kelly = base_kelly * scale

            # De-graduation: recent 20-trade Brier too high → floor
            _degrade_brier = float(getattr(settings, "ESPORTS_KELLY_DEGRADE_BRIER", 0.28))
            # Use per-game data to compute recent aggregate (most recent 20 across all games)
            _acc_20 = await self._get_cached_rolling_accuracy(db, last_n=20)
            recent_total = 0
            recent_brier_sum = 0.0
            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                acc = _acc_20.get(game)
                if acc and acc["total"] > 0:
                    recent_total += acc["total"]
                    recent_brier_sum += acc["brier_score"] * acc["total"]
            if recent_total >= 20:
                recent_brier = recent_brier_sum / recent_total
                # S100b: Suspend kelly degradation while any BetaCalibrator is
                # unfitted — aggregate Brier is polluted by stale pre-greenfield data.
                _any_unfitted = any(
                    cal and not cal._fitted
                    for cal in self._beta_calibrators.values()
                )
                if recent_brier > _degrade_brier and not _any_unfitted:
                    new_kelly = min(new_kelly, 0.20)
                    logger.warning("esportsbot_kelly_degraded",
                                   recent_brier=round(recent_brier, 4),
                                   new_kelly=round(new_kelly, 4))

            # Absolute cap
            max_kelly = float(getattr(settings, "ESPORTS_KELLY_MAX_FRACTION", 0.35))
            new_kelly = min(new_kelly, max_kelly)

            # Apply if changed significantly
            if self.bankroll is not None:
                old_kelly = self.bankroll.kelly_fraction
                if abs(new_kelly - old_kelly) > 0.005:
                    self.bankroll.kelly_fraction = round(new_kelly, 4)
                    self._kelly_graduated = new_kelly > base_kelly
                    logger.info("esportsbot_kelly_updated",
                                old=round(old_kelly, 4),
                                new=round(new_kelly, 4),
                                avg_brier=round(avg_brier, 4),
                                total_resolved=total_resolved,
                                scale=round(scale, 3))
        except Exception as exc:
            logger.warning("esportsbot_kelly_graduation_failed", error=str(exc))

    # ── Glicko-2 "easy mode" helpers ──────────────────────────────────────

    async def _init_glicko2_trackers(self, db) -> None:
        """Build Glicko-2 trackers from persisted DB ratings or historical data.

        Called once during start(). Tries fast path first (load persisted
        ratings from glicko2_ratings table). Falls back to full rebuild from
        esports_training_data if no persisted ratings exist, then saves them.
        """
        if db is None or not getattr(db, "session_factory", None):
            return
        try:
            from sqlalchemy import text
            from esports.models.glicko2 import Glicko2Tracker, Glicko2Rating

            # 1. Build team name → ID mapping from esports_teams
            async with db.get_session(timeout=30) as session:
                rows = await session.execute(text(
                    "SELECT external_id, name FROM esports_teams WHERE name IS NOT NULL"
                ))
                for row in rows.fetchall():
                    tid, name = str(row[0]).strip(), str(row[1]).strip()
                    if tid and name:
                        self._team_name_to_id[name.lower()] = name.lower()
                        # S137 10C: Build PandaScore ID mapping for roster lookups
                        try:
                            self._team_name_to_ps_id[name.lower()] = int(tid)
                        except (ValueError, TypeError):
                            pass

            # 2. Fast path: load persisted Glicko-2 ratings from DB
            all_games = ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl")
            games_loaded = set()
            for game in all_games:
                try:
                    async with db.get_session(timeout=15) as session:
                        rows = await session.execute(text("""
                            SELECT team_key, mu, phi, sigma, match_count
                            FROM glicko2_ratings
                            WHERE game = :game
                        """), {"game": game})
                        ratings_rows = rows.fetchall()
                except Exception:
                    # Table doesn't exist yet (migration not run) — fall back
                    ratings_rows = []

                if ratings_rows:
                    # S136: Per-game tau
                    _tau_key = f"ESPORTS_GLICKO2_TAU_{game.upper()}"
                    _tau_val = float(getattr(settings, _tau_key, getattr(settings, "ESPORTS_GLICKO2_TAU_DEFAULT", 0.5)))
                    tracker = Glicko2Tracker(tau=_tau_val)
                    total_matches = 0
                    for row in ratings_rows:
                        team_key = str(row[0])
                        rating = Glicko2Rating(
                            mu=float(row[1]),
                            phi=float(row[2]),
                            sigma=float(row[3]),
                        )
                        tracker.set_rating(team_key, rating)
                        self._team_name_to_id[team_key] = team_key
                        total_matches = max(total_matches, int(row[4] or 0))
                    tracker.set_match_count(total_matches)
                    self._glicko2_trackers[game] = tracker
                    games_loaded.add(game)
                    # S137 10C: Load player ratings from DB
                    _player_count = 0
                    try:
                        async with db.get_session(timeout=15) as session:
                            _pr_rows = await session.execute(text("""
                                SELECT player_id, mu, phi, sigma
                                FROM glicko2_player_ratings
                                WHERE game = :game
                            """), {"game": game})
                            for _pr in _pr_rows.fetchall():
                                _p_id = str(_pr[0])
                                _p_rating = Glicko2Rating(
                                    mu=float(_pr[1]),
                                    phi=float(_pr[2]),
                                    sigma=float(_pr[3]),
                                )
                                tracker.set_player_rating(_p_id, _p_rating)
                                _player_count += 1
                    except Exception:
                        pass  # Table may not exist yet (migration 060 not run)

                    logger.info(
                        "EsportsBot: Glicko-2 loaded from DB",
                        game=game,
                        teams_rated=len(ratings_rows),
                        match_count=total_matches,
                        players_loaded=_player_count,
                    )

            # 3. Slow path: rebuild games missing from DB (first run or new game added)
            games_to_rebuild = [g for g in all_games if g not in games_loaded]
            if not games_to_rebuild:
                return

            rebuilt_any = False
            for game in games_to_rebuild:
                async with db.get_session(timeout=30) as session:
                    rows = await session.execute(text("""
                        SELECT team_a, team_b, outcome
                        FROM esports_training_data
                        WHERE game = :game AND outcome IS NOT NULL
                        ORDER BY COALESCE(scheduled_at, created_at) ASC
                    """), {"game": game})
                    matches = rows.fetchall()

                if not matches:
                    continue

                # S136: Per-game tau
                _tau_key2 = f"ESPORTS_GLICKO2_TAU_{game.upper()}"
                _tau_val2 = float(getattr(settings, _tau_key2, getattr(settings, "ESPORTS_GLICKO2_TAU_DEFAULT", 0.5)))
                tracker = Glicko2Tracker(tau=_tau_val2)
                for row in matches:
                    team_a_name = str(row[0] or "").strip()
                    team_b_name = str(row[1] or "").strip()
                    outcome = int(row[2]) if row[2] is not None else None
                    if not team_a_name or not team_b_name or outcome is None:
                        continue
                    a_id = team_a_name.lower()
                    b_id = team_b_name.lower()
                    if outcome == 1:
                        w = "a"
                    elif outcome == 0:
                        w = "b"
                    else:
                        w = "draw"
                    tracker.process_match(a_id, b_id, winner=w)
                    self._team_name_to_id[a_id] = a_id
                    self._team_name_to_id[b_id] = b_id

                self._glicko2_trackers[game] = tracker
                rebuilt_any = True
                logger.info(
                    "EsportsBot: Glicko-2 rebuilt from training data",
                    game=game,
                    matches_processed=tracker.match_count,
                    teams_rated=len(tracker._ratings),
                )

            # 4. Persist rebuilt ratings so next restart is fast
            if rebuilt_any:
                await self._save_glicko2_ratings(db)

        except Exception as exc:
            logger.warning("EsportsBot: Glicko-2 init failed (non-fatal)", error=str(exc))

    async def _save_glicko2_ratings(self, db) -> None:
        """Persist all Glicko-2 ratings to glicko2_ratings table.

        Uses upsert (ON CONFLICT UPDATE) so it's safe to call repeatedly.
        """
        if db is None:
            return
        try:
            from sqlalchemy import text

            for game, tracker in self._glicko2_trackers.items():
                all_ratings = tracker.get_all_ratings()
                if not all_ratings:
                    continue
                match_count = tracker.match_count
                async with db.get_session(timeout=15) as session:
                    for team_key, rating in all_ratings.items():
                        await session.execute(text("""
                            INSERT INTO glicko2_ratings
                                (game, team_key, mu, phi, sigma, match_count, updated_at)
                            VALUES
                                (:game, :team_key, :mu, :phi, :sigma, :mc, NOW())
                            ON CONFLICT (game, team_key) DO UPDATE SET
                                mu = :mu, phi = :phi, sigma = :sigma,
                                match_count = :mc, updated_at = NOW()
                        """), {
                            "game": game,
                            "team_key": team_key,
                            "mu": rating.mu,
                            "phi": rating.phi,
                            "sigma": rating.sigma,
                            "mc": match_count,
                        })
                    await session.commit()

                # S137 10C: Save player ratings
                try:
                    player_ratings = tracker.get_all_player_ratings()
                    if player_ratings:
                        async with db.get_session(timeout=15) as session:
                            for p_id, (p_mu, p_phi, p_sigma) in player_ratings.items():
                                await session.execute(text("""
                                    INSERT INTO glicko2_player_ratings
                                        (game, player_id, mu, phi, sigma, updated_at)
                                    VALUES
                                        (:game, :player_id, :mu, :phi, :sigma, NOW())
                                    ON CONFLICT (game, player_id) DO UPDATE SET
                                        mu = :mu, phi = :phi, sigma = :sigma,
                                        updated_at = NOW()
                                """), {
                                    "game": game,
                                    "player_id": p_id,
                                    "mu": p_mu,
                                    "phi": p_phi,
                                    "sigma": p_sigma,
                                })
                            await session.commit()
                except Exception:
                    pass  # Table may not exist yet (migration 060 not run)

            logger.info("EsportsBot: Glicko-2 ratings saved to DB",
                        games=len(self._glicko2_trackers))
        except Exception as exc:
            logger.debug("EsportsBot: Glicko-2 save failed (non-fatal)", error=str(exc))

    # ── S94: Form prefetch (startup warmup + background refresh) ────────

    async def _warm_form_cache(self) -> None:
        """Pre-fetch form data for rated teams on startup.

        Eliminates cold-start API burst on the first scan cycle.
        Rate-limited: 0.5s between calls, stops if budget < 700.
        Capped at 50 teams (~25s startup cost).
        """
        if not self._pandascore:
            return
        from esports.data.pandascore_client import PandaScoreClient

        pairs: list = []
        seen: set = set()
        for game, tracker in self._glicko2_trackers.items():
            if game == "dota2":
                continue
            for team_key in tracker.get_all_ratings():
                pair = (team_key, game)
                if pair not in seen:
                    seen.add(pair)
                    pairs.append(pair)

        pairs = pairs[:50]
        warmed = 0
        for team_id, game in pairs:
            if PandaScoreClient.get_remaining_budget() < 700:
                logger.info("esportsbot_form_warmup_budget_stop",
                            warmed=warmed, budget_remaining=PandaScoreClient.get_remaining_budget())
                break
            try:
                await self._get_recent_form(team_id, game)
                warmed += 1
            except Exception as exc:
                logger.debug("esportsbot_form_warmup_failed", team_id=team_id, game=game, error=str(exc))
            await asyncio.sleep(0.5)

        if warmed > 0:
            logger.info("esportsbot_form_cache_warmed",
                        teams_warmed=warmed, total_candidates=len(pairs))

    async def _background_form_prefetch(self) -> None:
        """Continuously refresh form data for known teams in the background.

        Cycles through all (team_id, game) pairs from Glicko2 trackers.
        Priority: live match teams first, then all rated teams.
        Skips entries still 80%+ fresh (< 80% of TTL elapsed).
        Rate: ~1 req per 18s = ~200 req/hr. Pauses if budget < 500.
        Target cycle: 20 minutes.
        """
        from esports.data.pandascore_client import PandaScoreClient

        logger.info("esportsbot_form_prefetch_started")

        while self.running:
            try:
                priority_pairs: list = []
                normal_pairs: list = []
                seen: set = set()

                # Priority: teams from live matches
                for _mid, match_data in list(self._live_matches.items()):
                    game = getattr(match_data, "game", "") if not isinstance(match_data, dict) else match_data.get("game", "")
                    if game == "dota2":
                        continue
                    for tkey in ("team_a", "team_b"):
                        if isinstance(match_data, dict):
                            tid = str(match_data.get(f"{tkey}_id", match_data.get(tkey, "")))
                        else:
                            tid = str(getattr(match_data, f"{tkey}_id", 0) or getattr(match_data, tkey, ""))
                        if tid and (tid, game) not in seen:
                            seen.add((tid, game))
                            priority_pairs.append((tid, game))

                # Normal: all rated teams
                for game, tracker in self._glicko2_trackers.items():
                    if game == "dota2":
                        continue
                    for team_key in tracker.get_all_ratings():
                        pair = (team_key, game)
                        if pair not in seen:
                            seen.add(pair)
                            normal_pairs.append(pair)

                all_pairs = priority_pairs + normal_pairs
                if not all_pairs:
                    await asyncio.sleep(60)
                    continue

                cycle_start = time.monotonic()
                refreshed = 0

                for team_id, game in all_pairs:
                    if not self.running:
                        break

                    budget = PandaScoreClient.get_remaining_budget()
                    if budget < 500:
                        logger.debug("esportsbot_prefetch_budget_pause",
                                     budget=budget, refreshed=refreshed)
                        await asyncio.sleep(120)
                        continue

                    # Skip if still 80%+ fresh
                    cached = self._team_form_cache.get((team_id, game))
                    if cached and (time.monotonic() - cached[1]) < self._team_form_ttl * 0.8:
                        continue

                    try:
                        await self._get_recent_form(team_id, game)
                        refreshed += 1
                    except Exception:
                        pass

                    await asyncio.sleep(18.0)

                cycle_time = time.monotonic() - cycle_start
                logger.info("esportsbot_prefetch_cycle_complete",
                            refreshed=refreshed, total_teams=len(all_pairs),
                            cycle_seconds=round(cycle_time, 0))

                # Sleep remainder of 20-min target cycle
                remaining = 1200.0 - cycle_time
                if remaining > 0:
                    await asyncio.sleep(remaining)

            except asyncio.CancelledError:
                logger.info("esportsbot_form_prefetch_stopped")
                return
            except Exception as exc:
                logger.warning("esportsbot_prefetch_error", error=str(exc))
                await asyncio.sleep(60)

    async def _train_in_background(
        self, game: str, db, init_glicko: bool = False
    ) -> None:
        """Run model training as background task — does not block scan loop."""
        # S156: Acquire semaphore to limit concurrent training (max 3)
        async with self._train_semaphore:
            try:
                if game == "cross_game":
                    await asyncio.wait_for(
                        self._trainer.train_cross_game(db=db), timeout=300.0,
                    )
                    self._load_cross_game_model()
                else:
                    result = await asyncio.wait_for(
                        self._trainer.train_game(game, db=db), timeout=300.0,
                    )
                    # Reload updated models
                    if game == "lol" and self._lol_model:
                        self._lol_model.load()
                    elif game == "cs2" and self._cs2_model:
                        self._cs2_model.load()
                    elif game == "dota2" and self._dota2_model:
                        self._dota2_model.load()
                    elif game == "valorant" and self._valorant_model:
                        self._valorant_model.load()
                    # S136 Phase 11A-11D: Reload new game models after retrain
                    elif game == "cod" and self._cod_model:
                        self._cod_model.load()
                    elif game == "rl" and self._rl_model:
                        self._rl_model.load()
                    elif game == "sc2" and self._sc2_model:
                        self._sc2_model.load()
                    elif game == "r6" and self._r6_model:
                        self._r6_model.load()
                    # Rebuild Glicko-2 trackers if new game data collected
                    if init_glicko and result.get("samples", 0) > 0:
                        await self._init_glicko2_trackers(db)
                logger.info("EsportsBot: bg retrain complete", game=game)
            except asyncio.CancelledError:
                logger.info("EsportsBot: bg retrain cancelled", game=game)
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning("EsportsBot: bg retrain failed", game=game, error=str(exc))

    async def _get_cached_rolling_accuracy(self, db, last_n: int = 50) -> Dict[str, Dict]:
        """S94: Batch rolling accuracy with 5-min cache.

        Returns {game: {total, correct, accuracy, brier_score}}.
        One SQL query for all 8 games instead of 8 sequential queries.
        Cached per last_n value (e.g. 50 for monitoring, 20 for de-graduation).
        """
        now = time.monotonic()
        cached_ts = self._rolling_accuracy_cache_ts.get(last_n, 0.0)
        if now - cached_ts < 300.0:  # 5 min TTL
            return self._rolling_accuracy_cache.get(last_n, {})
        try:
            from esports.data.esports_db import get_rolling_accuracy_batch
            _all_games = ["lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"]
            result = await get_rolling_accuracy_batch(db, _all_games, bot_name="EsportsBot", last_n=last_n)
            self._rolling_accuracy_cache[last_n] = result
            self._rolling_accuracy_cache_ts[last_n] = now
            return result
        except Exception as exc:
            logger.debug("esportsbot_rolling_accuracy_cache_failed", error=str(exc), last_n=last_n)
            return self._rolling_accuracy_cache.get(last_n, {})

    async def _check_monitoring_thresholds(self, db) -> None:
        """E4: Check per-game Brier scores and emit structured alerts.

        Thresholds:
          - Brier > 0.25 (7d) → WARNING, retrain model
          - Brier > 0.30 (7d) → CRITICAL, halt trading for that game

        Checks run every 10 minutes (self._monitoring_check_interval).
        """
        now = time.monotonic()
        if now - self._monitoring_last_check < self._monitoring_check_interval:
            return
        self._monitoring_last_check = now

        if not db:
            return

        alerting = getattr(self.base_engine, "alerting_system", None)

        try:
            _acc_all = await self._get_cached_rolling_accuracy(db)
            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                acc_data = _acc_all.get(game)
                if not acc_data or acc_data["total"] < 30:
                    # S100: Clear halt when data is insufficient to justify it.
                    # Prevents catch-22 where halted games never accumulate data.
                    if game in self._monitoring_halted_games:
                        logger.info("esportsbot_monitoring_halt_cleared_insufficient_data",
                                    game=game,
                                    total=acc_data["total"] if acc_data else 0)
                        self._monitoring_halted_games.discard(game)
                    continue

                brier = acc_data["brier_score"]
                accuracy = acc_data["accuracy"]
                self._game_brier_cache[game] = brier

                # S136: Statistical Brier halt — halt only when lower bound of
                # 90% CI exceeds threshold (statistically confident worse than random).
                # Requires n>=50. Replaces fixed threshold which false-halted on noise.
                # S142: Threshold lowered from 0.25 to 0.22 via ESPORTS_BRIER_HALT_LOWER_BOUND
                # so CS2 (Brier 0.339, lb=0.249) actually gets caught.
                import math as _math
                _n_total = acc_data["total"]
                _halt_lb = float(getattr(settings, "ESPORTS_BRIER_HALT_LOWER_BOUND", 0.22))
                _should_halt = False
                if _n_total >= 50:
                    _se = _math.sqrt(brier * (1.0 - brier) / _n_total) if brier < 1.0 else 0.0
                    _lower_bound = brier - 1.645 * _se  # 90% one-sided
                    _should_halt = _lower_bound > _halt_lb
                if _should_halt:
                    # S100: Don't halt when BetaCalibrator has no clean data yet.
                    _cal = self._beta_calibrators.get(game)
                    if _cal and not _cal._fitted:
                        if game in self._monitoring_halted_games:
                            logger.info("esportsbot_monitoring_halt_suspended_no_cal",
                                        game=game, brier=round(brier, 4))
                            self._monitoring_halted_games.discard(game)
                        continue
                    self._monitoring_halted_games.add(game)
                    if alerting:
                        await alerting.send_alert(
                            title=f"EsportsBot {game} Brier CRITICAL",
                            message=f"{game} 7d Brier={brier:.4f} (>{_halt_lb}), accuracy={accuracy:.1%} — "
                                    f"trading halted for {game}.",
                            severity=AlertSeverity.CRITICAL,
                            source="EsportsBot",
                            metadata={"game": game, "brier": brier, "accuracy": accuracy,
                                      "total": acc_data["total"]},
                        )
                    logger.critical(
                        "esportsbot_monitoring_halt",
                        game=game, brier=round(brier, 4), accuracy=round(accuracy, 3),
                    )
                elif brier > 0.25:
                    # WARNING but don't halt — trigger retrain
                    self._monitoring_halted_games.discard(game)
                    if alerting:
                        await alerting.send_alert(
                            title=f"EsportsBot {game} Brier WARNING",
                            message=f"{game} 7d Brier={brier:.4f} (>0.25), accuracy={accuracy:.1%} — "
                                    f"consider retraining.",
                            severity=AlertSeverity.WARNING,
                            source="EsportsBot",
                            metadata={"game": game, "brier": brier, "accuracy": accuracy,
                                      "total": acc_data["total"]},
                        )
                    logger.warning(
                        "esportsbot_monitoring_warning",
                        game=game, brier=round(brier, 4), accuracy=round(accuracy, 3),
                    )
                else:
                    # Below thresholds — clear halt
                    if game in self._monitoring_halted_games:
                        logger.info("esportsbot_monitoring_halt_cleared",
                                    game=game, brier=round(brier, 4))
                    self._monitoring_halted_games.discard(game)

                # Per-game Kelly multiplier based on Brier score
                # S100b: Suspend penalties while BetaCalibrator unfitted — stale
                # accuracy data from pre-greenfield pipeline is not actionable.
                _cal_game = self._beta_calibrators.get(game)
                if _cal_game and not _cal_game._fitted:
                    self._game_kelly_mult[game] = 1.0
                else:
                    _brier_penalty = float(getattr(settings, "ESPORTS_KELLY_BRIER_PENALTY", 0.25))
                    _brier_boost = float(getattr(settings, "ESPORTS_KELLY_BRIER_BOOST", 0.20))
                    if brier > _brier_penalty:
                        self._game_kelly_mult[game] = 0.5
                    elif brier < _brier_boost:
                        self._game_kelly_mult[game] = 1.2
                    else:
                        self._game_kelly_mult[game] = 1.0
        except Exception as exc:
            logger.debug("esportsbot_monitoring_check_failed", error=str(exc))

        # Log per-game Kelly multipliers if any changed
        if self._game_kelly_mult:
            _nondefault = {g: m for g, m in self._game_kelly_mult.items() if m != 1.0}
            if _nondefault:
                logger.info("esportsbot_game_kelly_mult", multipliers=_nondefault)

        # Calibration curve + ECE tracking per game
        try:
            from esports.data.esports_db import compute_calibration_curve
            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                cal = await compute_calibration_curve(db, game=game, days=90)
                if cal and cal["total"] >= 30:
                    self._calibration_ece[game] = cal["ece"]
                    logger.info("esportsbot_calibration_report",
                                game=game, ece=cal["ece"],
                                bins=cal["bins"], total=cal["total"])
        except Exception as exc:
            logger.debug("esportsbot_calibration_failed", error=str(exc))

        # S100: Fit BetaCalibrators per game.  Training window starts from the
        # S97 Glicko2 fix date (2026-03-16) so stale pre-fix data never enters.
        _GLICKO2_FIX_DATE = datetime(2026, 3, 16, tzinfo=timezone.utc)
        _days_since_fix = max(1, (datetime.now(timezone.utc) - _GLICKO2_FIX_DATE).days)
        _cal_days = min(_days_since_fix, 90)
        for _cal_game, _cal in self._beta_calibrators.items():
            try:
                _cal_fitted = await _cal.fit_from_db(db, _cal_game, days=_cal_days)
                if _cal_fitted:
                    logger.info("esportsbot_beta_cal_fitted", game=_cal_game,
                                a=round(_cal.a, 4), b=round(_cal.b, 4),
                                c=round(_cal.c, 4), n=_cal._n_samples,
                                window_days=_cal_days)
                else:
                    logger.info("esportsbot_beta_cal_insufficient_data",
                                game=_cal_game, n_samples=_cal._n_samples,
                                min_required=_cal.min_samples)
            except Exception as exc:
                logger.debug("esportsbot_beta_cal_fit_failed",
                             game=_cal_game, error=str(exc))

        # S136 Phase 4B: Hierarchical pooled BetaCal — pool all games for global calibrator
        try:
            from sqlalchemy import text as _text
            import numpy as np
            _all_preds = []
            _all_outcomes = []
            _all_ages = []
            _game_counts = {}
            async with db.get_session(timeout=30) as session:
                for _pool_game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                    _pool_result = await session.execute(
                        _text(
                            "SELECT COALESCE(raw_model_prob, predicted_prob), actual_outcome, "
                            "EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400.0 AS age_days "
                            "FROM esports_prediction_log "
                            "WHERE actual_outcome IS NOT NULL "
                            "AND game = :game "
                            "AND created_at > NOW() - :days_int * INTERVAL '1 day' "
                            "ORDER BY created_at DESC LIMIT 5000"
                        ),
                        {"game": _pool_game, "days_int": int(_cal_days)},
                    )
                    _pool_rows = _pool_result.fetchall()
                    _game_counts[_pool_game] = len(_pool_rows)
                    for _pr in _pool_rows:
                        _all_preds.append(float(_pr[0]))
                        _all_outcomes.append(float(_pr[1]))
                        _all_ages.append(max(0.0, float(_pr[2])))
            if len(_all_preds) >= self._global_beta_calibrator.min_samples:
                _all_preds_arr = np.clip(
                    np.array(_all_preds), 1e-6, 1.0 - 1e-6
                )
                _all_outcomes_arr = np.array(_all_outcomes)
                # S151: Temporal decay for global pooled calibrator (half-life 7 days)
                _all_ages_arr = np.array(_all_ages)
                _gw = np.exp(-np.log(2.0) * _all_ages_arr / 7.0)
                _gw = _gw / _gw.sum() * len(_gw)
                # Fit global BetaCal using in-memory data (same algo as per-game)
                from scipy.optimize import minimize as _minimize
                ln_p = np.log(_all_preds_arr)
                ln_1mp = np.log(1.0 - _all_preds_arr)
                # S151: Matched per-game lambda reduction (50/n, floor 0.5)
                _glam = max(0.5, 50.0 / max(len(_all_preds), 1))

                def _global_loss(params):
                    a, b, c_ = params
                    logits = a * ln_p - b * ln_1mp + c_
                    # S151: Weighted NLL with temporal decay
                    _per_sample = (
                        _all_outcomes_arr * np.logaddexp(0.0, -logits)
                        + (1.0 - _all_outcomes_arr) * np.logaddexp(0.0, logits)
                    )
                    nll = np.sum(_gw * _per_sample) / len(_gw)
                    reg = _glam * ((a - 1.0) ** 2 + (b - 1.0) ** 2 + c_ ** 2)
                    return nll + reg

                _gres = _minimize(
                    _global_loss,
                    x0=[1.0, 1.0, 0.0],
                    method="L-BFGS-B",
                    bounds=[(0.1, 5.0), (0.1, 5.0), (-2.0, 2.0)],
                )
                self._global_beta_calibrator.a = float(_gres.x[0])
                self._global_beta_calibrator.b = float(_gres.x[1])
                self._global_beta_calibrator.c = float(_gres.x[2])
                self._global_beta_calibrator._fitted = True
                self._global_beta_calibrator._n_samples = len(_all_preds)
                logger.info("esportsbot_global_beta_cal_fitted",
                            a=round(self._global_beta_calibrator.a, 4),
                            b=round(self._global_beta_calibrator.b, 4),
                            c=round(self._global_beta_calibrator.c, 4),
                            n=len(_all_preds), game_counts=_game_counts)
        except Exception as exc:
            logger.debug("esportsbot_global_beta_cal_failed", error=str(exc))

        # S136 Phase 4A: Fit per-game Venn-ABERS calibrators
        try:
            from esports.models.venn_abers_calibrator import VennAbersCalibrator
            import numpy as np
            async with db.get_session(timeout=30) as session:
                for _va_game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                    _va_result = await session.execute(
                        _text(
                            "SELECT COALESCE(raw_model_prob, predicted_prob), actual_outcome "
                            "FROM esports_prediction_log "
                            "WHERE actual_outcome IS NOT NULL "
                            "AND game = :game "
                            "AND created_at > NOW() - :days_int * INTERVAL '1 day' "
                            "ORDER BY created_at DESC LIMIT 5000"
                        ),
                        {"game": _va_game, "days_int": int(_cal_days)},
                    )
                    _va_rows = _va_result.fetchall()
                    if len(_va_rows) >= 50:
                        _va_preds = np.array([float(r[0]) for r in _va_rows])
                        _va_outcomes = np.array([float(r[1]) for r in _va_rows])
                        va = self._venn_abers_per_game.get(_va_game)
                        if va is None:
                            va = VennAbersCalibrator(min_samples=50)
                            self._venn_abers_per_game[_va_game] = va
                        _va_ok = va.fit(_va_preds, _va_outcomes)
                        if _va_ok:
                            logger.info("esportsbot_venn_abers_fitted",
                                        game=_va_game, n=len(_va_rows),
                                        interval_width=round(va.interval_width, 4))
        except Exception as exc:
            logger.debug("esportsbot_venn_abers_fit_failed", error=str(exc))

        # S100b: Fit per-game conformal predictors (Phase 3) from same data window.
        # Reuses BetaCalibrator's query pattern — (predicted_prob, actual_outcome).
        try:
            from esports.models.conformal_wrapper import ConformalPredictor
            from sqlalchemy import text as _text
            import numpy as np
            async with db.get_session(timeout=30) as session:
                for _cf_game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                    _cf_result = await session.execute(
                        _text(
                            "SELECT predicted_prob, actual_outcome "
                            "FROM esports_prediction_log "
                            "WHERE actual_outcome IS NOT NULL "
                            "AND game = :game "
                            "AND created_at > NOW() - :days_int * INTERVAL '1 day' "
                            "ORDER BY created_at DESC LIMIT 5000"
                        ),
                        {"game": _cf_game, "days_int": int(_cal_days)},
                    )
                    _cf_rows = _cf_result.fetchall()
                    if len(_cf_rows) >= 30:
                        _cf_preds = np.array([float(r[0]) for r in _cf_rows])
                        _cf_outcomes = np.array([float(r[1]) for r in _cf_rows])
                        cp = self._conformal_per_game.get(_cf_game)
                        if cp is None:
                            cp = ConformalPredictor(alpha=0.10)
                            self._conformal_per_game[_cf_game] = cp
                        _cf_ok = cp.fit_from_predictions(_cf_preds, _cf_outcomes)
                        if _cf_ok:
                            logger.info("esportsbot_conformal_fitted",
                                        game=_cf_game, n=len(_cf_rows))
        except Exception as exc:
            logger.debug("esportsbot_conformal_fit_failed", error=str(exc))

        # Session 83: Dynamic per-game EGM d tuning from Brier scores
        self._update_per_game_egm_d()

        # Edge decay analysis per game
        try:
            from esports.data.esports_db import analyze_edge_decay
            for game in ("lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"):
                decay = await analyze_edge_decay(db, game=game, days=30, n_bins=5)
                if decay and decay.get("total_predictions", 0) >= 20:
                    self._edge_decay_data[game] = decay
                    bins = decay.get("bins", [])
                    logger.info("esportsbot_edge_decay",
                                game=game,
                                total=decay["total_predictions"],
                                bins=bins)
                    # Flag fast decay: top time-bin has negative CLV
                    if bins and bins[0].get("avg_clv", 0) < 0:
                        logger.warning("esportsbot_edge_decay_negative_clv",
                                       game=game,
                                       top_bin_clv=bins[0].get("avg_clv"),
                                       top_bin_profit=bins[0].get("avg_profit"))
        except Exception as exc:
            logger.debug("esportsbot_edge_decay_failed", error=str(exc))

        # P&L summary logging
        try:
            from esports.data.esports_db import compute_pnl_summary
            pnl = await compute_pnl_summary(db)
            if pnl and pnl["total_trades"] > 0:
                logger.info(
                    "esportsbot_pnl_summary",
                    total_pnl=pnl["total_pnl"],
                    total_trades=pnl["total_trades"],
                    win_rate=round(pnl["win_rate"], 4),
                    per_game=pnl["per_game"],
                )
        except Exception as exc:
            logger.debug("esportsbot_pnl_summary_failed", error=str(exc))

        # Pinnacle CLV backfill (every 20 min)
        if now - self._last_clv_backfill >= 1200:
            self._last_clv_backfill = now
            oddspapi_key = getattr(settings, "ODDSPAPI_API_KEY", "")
            if oddspapi_key:
                try:
                    from esports.data.oddspapi_client import OddsPapiClient
                    from esports.data.esports_db import backfill_pinnacle_closing_lines
                    oddspapi = OddsPapiClient(api_key=oddspapi_key)
                    updated = await backfill_pinnacle_closing_lines(db, oddspapi)
                    if updated > 0:
                        logger.info("pinnacle_clv_backfill_done", rows_updated=updated)
                except Exception as exc:
                    logger.debug("pinnacle_clv_backfill_failed", error=str(exc))

        # Fit TabPFN for sparse games (SC2, RL, CoD, R6)
        await self._fit_tabpfn_models(db)


        # Fit CatBoost draft models (per game, if enough data) [Session C]
        await self._fit_catboost_draft_models(db)

        # Update draft feature stats [Session B]
        await self._update_draft_feature_stats(db)

        # CLV scaling tier [WS2]
        self._clv_scaling_tier = await self._compute_clv_scaling_tier(db)

        # Retention cleanup — once daily
        await self._cleanup_old_esports_data(db)

    async def _cleanup_old_esports_data(self, db) -> None:
        """Delete old esports data beyond retention window. Called once daily."""
        import datetime as _dt_mod
        today = _dt_mod.date.today()
        if self._last_cleanup_date == today:
            return
        self._last_cleanup_date = today
        try:
            from sqlalchemy import text as _sa_text
            train_days = int(getattr(settings, "ESPORTS_TRAINING_RETENTION_DAYS", 365))
            pred_days = int(getattr(settings, "ESPORTS_PREDICTION_RETENTION_DAYS", 180))
            async with db.get_session(timeout=15) as session:
                r1 = await session.execute(
                    _sa_text("DELETE FROM esports_training_data WHERE created_at < NOW() - INTERVAL '1 day' * :days"),
                    {"days": train_days},
                )
                r2 = await session.execute(
                    _sa_text("DELETE FROM esports_prediction_log WHERE created_at < NOW() - INTERVAL '1 day' * :days"),
                    {"days": pred_days},
                )
                await session.commit()
            logger.info("esports_data_cleanup",
                        training_deleted=r1.rowcount, prediction_deleted=r2.rowcount)
        except Exception as exc:
            logger.warning("esports_data_cleanup_failed", error=str(exc))

    async def _fit_catboost_draft_models(self, db) -> None:
        """Train/reload CatBoost draft models for games with sufficient data.

        Gated behind ESPORTS_CATBOOST_ENABLED. Per-game models for LoL, Dota2,
        Valorant, R6. Respects retrain interval (default 24h).
        """
        if not getattr(settings, "ESPORTS_CATBOOST_ENABLED", False):
            return
        if not db:
            return

        _retrain_hours = int(getattr(settings, "ESPORTS_CATBOOST_RETRAIN_HOURS", 24))
        _retrain_interval = _retrain_hours * 3600.0

        for game in ("lol", "dota2", "valorant", "r6"):
            last_train = self._catboost_last_train.get(game, 0.0)
            if (time.monotonic() - last_train) < _retrain_interval:
                continue

            try:
                # Try to load existing model first (on first check)
                if game not in self._catboost_models:
                    import os as _os
                    from esports.models.catboost_draft_model import CatBoostDraftModel
                    _model_path = _os.path.join(
                        _os.path.dirname(__file__), "..", "saved_models",
                        f"catboost_{game}.cbm",
                    )
                    _model_path = _os.path.abspath(_model_path)
                    _model = CatBoostDraftModel(game)
                    if _model.load(_model_path):
                        self._catboost_models[game] = _model
                        self._catboost_last_train[game] = time.monotonic()
                        logger.info("esportsbot_catboost_loaded", game=game)
                        continue

                # Train new model
                if hasattr(self, "_trainer") and self._trainer is not None:
                    metrics = await self._trainer.train_catboost_draft(game, db)
                    if metrics and metrics.get("graduated", False):
                        # Reload the saved model
                        import os as _os
                        from esports.models.catboost_draft_model import CatBoostDraftModel
                        _model_path = _os.path.join(
                            _os.path.dirname(__file__), "..", "saved_models",
                            f"catboost_{game}.cbm",
                        )
                        _model_path = _os.path.abspath(_model_path)
                        _model = CatBoostDraftModel(game)
                        if _model.load(_model_path):
                            self._catboost_models[game] = _model
                    self._catboost_last_train[game] = time.monotonic()
            except Exception as exc:
                logger.debug("esportsbot_catboost_fit_failed", game=game, error=str(exc))

    async def _update_draft_feature_stats(self, db) -> None:
        """Refresh DraftFeatureBuilder stats from training data.

        Initializes builder on first call, then refreshes hourly per game.
        """
        if not getattr(settings, "ESPORTS_DRAFT_FEATURES_ENABLED", True):
            return
        if not db:
            return

        try:
            if self._draft_feature_builder is None:
                from esports.models.draft_features import DraftFeatureBuilder
                self._draft_feature_builder = DraftFeatureBuilder()

            for game in ("lol", "dota2", "valorant", "r6"):
                if self._draft_feature_builder.needs_refit(game, interval_seconds=3600.0):
                    await self._draft_feature_builder.fit_stats(db, game)
        except Exception as exc:
            logger.debug("esportsbot_draft_features_update_failed", error=str(exc))

    async def _compute_clv_scaling_tier(self, db) -> str:
        """Compute CLV-based scaling tier for position limits.

        Uses existing compute_clv_stats() from esports_db.py.
        Returns: "conservative" / "moderate" / "aggressive"
        """
        if not getattr(settings, "ESPORTS_CLV_SCALING_ENABLED", False):
            return "conservative"
        if not db:
            return "conservative"

        try:
            from esports.data.esports_db import compute_clv_stats

            # Aggregate CLV across all games
            total_samples = 0
            total_clv_sum = 0.0
            total_clv_positive = 0

            for game in ("lol", "cs2", "dota2", "valorant"):
                clv = await compute_clv_stats(db, game, days=30)
                if clv and clv["total"] > 0:
                    total_samples += clv["total"]
                    total_clv_sum += clv["avg_clv"] * clv["total"]
                    total_clv_positive += clv["clv_positive_count"]

            if total_samples < 30:
                tier = "conservative"
            else:
                avg_clv = total_clv_sum / total_samples
                hit_rate = total_clv_positive / total_samples

                if hit_rate >= 0.55 and avg_clv > 0.02 and total_samples >= 100:
                    tier = "aggressive"
                elif hit_rate >= 0.52 and avg_clv > 0.01 and total_samples >= 50:
                    tier = "moderate"
                else:
                    tier = "conservative"

            logger.info(
                "esportsbot_clv_scaling_tier",
                tier=tier,
                total_samples=total_samples,
                avg_clv=round(total_clv_sum / total_samples, 4) if total_samples > 0 else 0.0,
                hit_rate=round(total_clv_positive / total_samples, 4) if total_samples > 0 else 0.0,
            )
            return tier
        except Exception as exc:
            logger.debug("esportsbot_clv_scaling_failed", error=str(exc))
            return "conservative"

    async def _fit_tabpfn_models(self, db) -> None:
        """Fit TabPFN classifiers for sparse games using resolved trade data.

        Queries ENTRY event_data (Glicko-2 features) joined with RESOLUTION
        outcomes. Gracefully degrades when data < 20 or tabpfn not installed.
        """
        if self._tabpfn_predictor is None or not self._tabpfn_predictor.is_available:
            return
        if not db:
            return

        try:
            from sqlalchemy import text as _sa_text
            import numpy as _np

            _FEATURES = [
                "team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                "team_a_volatility", "team_b_volatility", "best_of",
            ]

            for game in ("sc2", "rl", "cod", "r6"):
                async with db.get_session(timeout=30) as session:
                    rows = await session.execute(_sa_text(
                        "SELECT e.event_data, r.realized_pnl "
                        "FROM trade_events r "
                        "JOIN trade_events e "
                        "  ON e.market_id = r.market_id "
                        "  AND e.bot_name = r.bot_name "
                        "  AND e.event_type = 'ENTRY' "
                        "WHERE r.bot_name = 'EsportsBot' "
                        "  AND r.event_type = 'RESOLUTION' "
                        "  AND r.realized_pnl IS NOT NULL "
                        "  AND e.event_data IS NOT NULL "
                        "  AND e.event_data->>'game' = :game "
                        "ORDER BY r.event_time DESC LIMIT 500"
                    ), {"game": game})
                    data = rows.fetchall()

                if len(data) < 20:
                    continue

                X_list, y_list = [], []
                for row in data:
                    ed = row[0] if isinstance(row[0], dict) else {}
                    pnl = float(row[1])
                    feats = [float(ed.get(f, 0.0)) for f in _FEATURES]
                    if all(v == 0.0 for v in feats):
                        continue
                    X_list.append(feats)
                    y_list.append(1 if pnl > 0 else 0)

                if len(X_list) < 20:
                    continue

                X = _np.array(X_list, dtype=_np.float32)
                y = _np.array(y_list, dtype=_np.int32)
                fitted = self._tabpfn_predictor.fit_game(game, X, y)
                if fitted:
                    logger.info("esportsbot_tabpfn_fitted", game=game, n_samples=len(X))
        except Exception as exc:
            logger.debug("esportsbot_tabpfn_fit_failed", error=str(exc))

    async def _backfill_unknown_team(self, name: str, game: str) -> Optional[str]:
        """On-demand PandaScore lookup for a team missing from Glicko-2 DB.

        Searches PandaScore by name, fetches recent matches, processes through
        Glicko-2, persists ratings, and adds to _team_name_to_id.

        Returns team_key if successful, None if not found.
        Costs 2 API requests. Guarded by _backfill_attempted to avoid
        re-querying the same missing team every scan cycle.
        """
        cache_key = f"{game}:{name.lower()}"
        if cache_key in self._backfill_attempted:
            return self._team_name_to_id.get(name.lower())
        # Rate budget: cap backfill API calls per scan to avoid blowing PandaScore quota
        if self._backfill_calls_this_scan >= self._max_backfills_per_scan:
            logger.debug("esportsbot_backfill_budget_exhausted", name=name, game=game)
            return None
        self._backfill_calls_this_scan += 1
        self._backfill_attempted.add(cache_key)

        if not self._pandascore:
            return None

        try:
            # 1. Search PandaScore for team
            team_data = await self._pandascore.search_team_by_name(name)
            if not team_data or not team_data.get("id"):
                logger.info("esportsbot_team_backfill_not_found",
                            name=name, game=game)
                return None

            team_id = int(team_data["id"])
            team_name = str(team_data.get("name", name)).lower()
            # S137 10C: Populate PandaScore ID mapping for roster lookups
            self._team_name_to_ps_id[team_name] = team_id

            # 2. Fetch recent finished matches
            matches = await self._pandascore.get_team_matches(team_id, game, per_page=20)
            if not matches:
                logger.info("esportsbot_team_backfill_no_matches",
                            name=name, game=game, pandascore_id=team_id)
                return None

            # 3. Process through Glicko-2
            tracker = self._glicko2_trackers.get(game)
            if tracker is None:
                return None

            processed = 0
            for match in matches:
                a_name = match.team_a.lower().strip()
                b_name = match.team_b.lower().strip()
                if not a_name or not b_name:
                    continue
                # Determine winner from score
                if match.status != "finished":
                    continue
                if match.score_a > match.score_b:
                    winner = "a"
                elif match.score_b > match.score_a:
                    winner = "b"
                else:
                    winner = "draw"
                tracker.process_match(a_name, b_name, winner=winner)
                # Add ALL encountered teams to lookup
                self._team_name_to_id[a_name] = a_name
                self._team_name_to_id[b_name] = b_name
                processed += 1

            if processed == 0:
                return None

            # 4. Persist to DB
            db = getattr(self.base_engine, "db", None)
            if db:
                await self._save_glicko2_ratings(db)

            logger.info("esportsbot_team_backfilled",
                        name=team_name, game=game,
                        pandascore_id=team_id,
                        matches_processed=processed)

            # S136 6C: Auto-persist the query name → team_name mapping for session
            # so subsequent scans don't re-backfill the same team
            clean_name = name.lower().strip()
            resolved_tid = self._team_name_to_id.get(clean_name)
            if not resolved_tid and team_name:
                # Map original query name to the PandaScore canonical name
                self._team_name_to_id[clean_name] = team_name
                logger.info("esportsbot_team_auto_added", name=clean_name, team_id=team_name)
                resolved_tid = team_name

            return resolved_tid

        except Exception as exc:
            logger.debug("esportsbot_team_backfill_failed",
                         name=name, game=game, error=str(exc))
            return None

    async def _get_glicko2_prediction(
        self, market_data: Dict, game: str, market_price: float = 0.50
    ) -> Optional[float]:
        """Extract team names from market question and return Glicko-2 expected score.

        Returns P(team_a wins) based on Glicko-2 ratings, or None if we can't
        identify both teams or don't have ratings for them.

        S94-P3: market_price used as Bayesian prior instead of 0.50 — anchors
        uncertain predictions on market consensus rather than coin-flip.
        """
        tracker = self._glicko2_trackers.get(game)
        if tracker is None or tracker.match_count < 10:
            self._last_glicko2_miss_reason = "tracker_missing"
            return None

        question = str(market_data.get("question", "")).lower()
        if not question:
            self._last_glicko2_miss_reason = "empty_question"
            return None

        # S97-P2: Use shared 6-pattern extraction (was inline, now shared with
        # _build_glicko2_game_state to fix single-pattern gap)
        team_a_id, team_b_id, _clean_a, _clean_b = self._extract_team_ids_from_question(question)

        if not team_a_id or not team_b_id:
            # On-demand backfill: try PandaScore lookup for missing team(s)
            if not team_a_id and _clean_a:
                team_a_id = await self._backfill_unknown_team(_clean_a, game)
            if not team_b_id and _clean_b:
                team_b_id = await self._backfill_unknown_team(_clean_b, game)
            if not team_a_id or not team_b_id:
                # S100: Distinguish extraction vs match failure
                if not _clean_a or not _clean_b:
                    self._last_glicko2_miss_reason = f"extraction_failed"
                else:
                    _failed = _clean_a if not team_a_id else _clean_b
                    self._last_glicko2_miss_reason = f"match_failed:{_failed}"
                _fail_key = f"{game}:{_clean_a}:{_clean_b}"
                if _fail_key not in self._team_fail_logged:
                    self._team_fail_logged.add(_fail_key)
                    logger.info("esportsbot_team_match_fail", game=game,
                                question=question[:80],
                                name_a=_clean_a or "?",
                                name_b=_clean_b or "?",
                                team_a_id=team_a_id, team_b_id=team_b_id,
                                reason=self._last_glicko2_miss_reason)
                return None

        # Get Glicko-2 expected score with Bayesian prior blending (E5).
        # When teams have few matches (high phi), blend toward 0.50 (base rate)
        # instead of returning None. Blend weight based on min(phi_a, phi_b):
        #   phi >= 350 (unrated):    80% prior (0.50), 20% Glicko-2
        #   phi 200-350 (sparse):    50% prior, 50% Glicko-2
        #   phi < 200 (established): 20% prior, 80% Glicko-2
        #   phi < 100 (mature):       0% prior, 100% Glicko-2
        try:
            rating_a = tracker.get_rating(team_a_id)
            rating_b = tracker.get_rating(team_b_id)
            # S97-P2: Log when both teams are unrated defaults
            if rating_a.phi >= 349.0 and rating_b.phi >= 349.0:
                logger.debug("esportsbot_glicko2_high_phi",
                             game=game, team_a=team_a_id, team_b=team_b_id,
                             phi_a=round(rating_a.phi, 1), phi_b=round(rating_b.phi, 1))
            prob = tracker.expected_score(team_a_id, team_b_id)
            if not (0.01 < prob < 0.99):
                return None

            # E5: Bayesian prior blend — dampen predictions for uncertain teams
            # S94-P3: Use market_price as prior instead of 0.50.  When model is
            # uncertain (high phi), predictions stay close to market → small
            # realistic edges.  When confident (low phi), Glicko-2 dominates.
            # S136: Smooth sigmoid prior replaces discrete 4-tier brackets.
            # Eliminates artificial cliff at phi=350. Sigmoid yields similar
            # values at bracket boundaries but transitions smoothly.
            import math as _math_prior
            max_phi = max(rating_a.phi, rating_b.phi)
            prior_weight = 0.85 / (1.0 + _math_prior.exp(-(max_phi - 200.0) / 50.0))

            # S136 Phase 7C: Scale prior weight by market liquidity
            # Thin markets should get less market weight even for uncertain teams
            _volume_24h = float(market_data.get("volume_24h", market_data.get("volume", 0.0)))
            _baseline_volume = 3000.0  # Median esports market 24h volume
            _liquidity_factor = min(1.0, _volume_24h / _baseline_volume) if _baseline_volume > 0 else 0.5
            prior_weight = prior_weight * max(0.10, _liquidity_factor)  # Floor at 10% of phi-weight

            _prior = max(0.05, min(0.95, market_price))
            prob = prior_weight * _prior + (1.0 - prior_weight) * prob

            # S151: Roster stability only for moderate predictions.
            # Previously, prob in [0.95, 0.99) silently fell through to return None.
            # Now we return prob for all valid predictions in (0.01, 0.99).
            if 0.05 < prob < 0.95:
                # Roster stability [T2-D]: penalize teams with recent roster changes
                try:
                    _roster_a = await self._check_roster_stability(team_a_id, game=game)
                    _roster_b = await self._check_roster_stability(team_b_id, game=game)
                    if _roster_a < 1.0 or _roster_b < 1.0:
                        # Nudge prob toward market_price proportional to penalty
                        _roster_factor = min(_roster_a, _roster_b)
                        _roster_prior = max(0.05, min(0.95, market_price))
                        prob = _roster_factor * prob + (1.0 - _roster_factor) * _roster_prior
                        prob = max(0.05, min(0.95, prob))
                except Exception:
                    pass
            return prob
        except Exception as exc:
            logger.debug("esportsbot_glicko2_rating_error", error=str(exc), game=game)
        return None

    # ── Session 83: New helper methods ──────────────────────────────────

    async def _check_roster_stability(self, team_id: str, game: str = "") -> float:
        """Return confidence multiplier based on roster stability [T2-D].

        Returns 1.0 if no change detected, or (1 - penalty * decay) if recent change.
        Checks PandaScore team roster hash, caches with 24h TTL.

        S137 10C: Fixed to accept team names (not just PandaScore numeric IDs).
        Uses _team_name_to_ps_id mapping built during _init_glicko2_trackers().
        When roster change detected, also calls tracker.update_roster() for
        composite player rating adjustment.
        """
        if not self._pandascore:
            return 1.0

        try:
            # S137 10C: Map team name → PandaScore ID via lookup table.
            # Previously did str(int(team_id)) which silently failed for all
            # non-numeric team names (i.e., all Glicko-2 team keys).
            ps_id = self._team_name_to_ps_id.get(team_id)
            if ps_id is None:
                # Fallback: try numeric conversion for backward compat
                try:
                    ps_id = int(team_id)
                except (ValueError, TypeError):
                    return 1.0
            team_id = str(ps_id)
            now = time.monotonic()

            # 1K: Cooldown after API failure — skip refetch for 1h
            _fail_time = getattr(self, "_roster_fail_cache", {}).get(team_id, 0.0)
            if now - _fail_time < 3600.0:
                return 1.0

            # Check if roster was already fetched
            cached = self._roster_cache.get(team_id)
            if cached:
                old_hash, fetch_time = cached
                # Refresh every 24h
                if now - fetch_time < 86400.0:
                    # Check for known change
                    change_time = self._roster_change_cache.get(team_id)
                    if change_time:
                        decay_days = float(getattr(
                            settings, "ESPORTS_ROSTER_CHANGE_DECAY_DAYS", 7,
                        ))
                        elapsed_days = (now - change_time) / 86400.0
                        if elapsed_days >= decay_days:
                            self._roster_change_cache.pop(team_id, None)
                            return 1.0
                        penalty = float(getattr(
                            settings, "ESPORTS_ROSTER_CHANGE_PENALTY", 0.15,
                        ))
                        decay = 1.0 - (elapsed_days / decay_days)
                        return max(0.5, 1.0 - penalty * decay)
                    return 1.0

            # Fetch roster
            roster = await asyncio.wait_for(
                self._pandascore.get_team_roster(ps_id), timeout=5.0,
            )
            if not roster:
                return 1.0

            import hashlib
            roster_hash = hashlib.md5(
                "|".join(roster).encode()
            ).hexdigest()[:8]

            if cached:
                old_hash, _ = cached
                if old_hash != roster_hash:
                    # S137 10C: Call update_roster() for composite player rating
                    _change_ratio = 0.0
                    if game and roster:
                        _tracker = self._glicko2_trackers.get(game)
                        if _tracker:
                            # team_id here is str(ps_id); need original team name for tracker
                            # Reverse lookup: find the team name that mapped to this ps_id
                            _tracker_key = None
                            for _tn, _pid in self._team_name_to_ps_id.items():
                                if _pid == ps_id:
                                    _tracker_key = _tn
                                    break
                            if _tracker_key:
                                _change_ratio = _tracker.update_roster(_tracker_key, roster)
                    logger.info(
                        "esportsbot_roster_change", team_id=team_id,
                        old_hash=old_hash, new_hash=roster_hash,
                        change_ratio=round(_change_ratio, 3),
                    )
                    self._roster_change_cache[team_id] = now
                    self._roster_cache[team_id] = (roster_hash, now)
                    penalty = float(getattr(
                        settings, "ESPORTS_ROSTER_CHANGE_PENALTY", 0.15,
                    ))
                    return max(0.5, 1.0 - penalty)
                else:
                    self._roster_cache[team_id] = (roster_hash, now)
            else:
                self._roster_cache[team_id] = (roster_hash, now)

            # Check for pending change
            change_time = self._roster_change_cache.get(team_id)
            if change_time:
                decay_days = float(getattr(
                    settings, "ESPORTS_ROSTER_CHANGE_DECAY_DAYS", 7,
                ))
                elapsed_days = (now - change_time) / 86400.0
                if elapsed_days >= decay_days:
                    self._roster_change_cache.pop(team_id, None)
                    return 1.0
                penalty = float(getattr(
                    settings, "ESPORTS_ROSTER_CHANGE_PENALTY", 0.15,
                ))
                decay = 1.0 - (elapsed_days / decay_days)
                return max(0.5, 1.0 - penalty * decay)

            return 1.0
        except (ValueError, asyncio.TimeoutError):
            # 1K: Cooldown after API failure — avoid hammering PandaScore
            if not hasattr(self, "_roster_fail_cache"):
                self._roster_fail_cache = {}
            self._roster_fail_cache[team_id] = time.monotonic()
            return 1.0
        except Exception:
            return 1.0

    @staticmethod
    def _is_lan_event(market_data: Dict) -> bool:
        """Detect LAN events from market question keywords [T1-C].

        S137 parity: Expanded from CS2/Val-only to cover all 8 games.
        """
        q = str(market_data.get("question", "")).lower()
        _lan_keywords = (
            # Generic LAN indicators
            " lan ", "lan final", "major ", "finals ", "playoff",
            "world championship", "grand final",
            # CS2
            "blast premier", "iem ", "esl pro league",
            "pgl major", "copenhagen", "shanghai", "rio ",
            # Valorant
            "champions tour", "masters ", "vct ",
            # Dota2
            "the international", " ti1", " ti2", "dpc ",
            "esl one", "dreamhack ",
            # CoD
            "cdl ", "call of duty league", "cod major",
            # R6
            "six invitational", "six major", "r6 major",
            # SC2
            "gsl ", "katowice", "global starcraft",
            # RL
            "rlcs ", "rocket league championship",
        )
        return any(kw in q for kw in _lan_keywords)

    @staticmethod
    def _compute_ttr_days(market_data: Dict) -> Optional[float]:
        """Compute time-to-resolution in days from market end_date_iso."""
        end_date = market_data.get("end_date_iso")
        if not end_date:
            return None
        try:
            if isinstance(end_date, str):
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            else:
                end_dt = end_date
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (end_dt - now).total_seconds() / 86400.0)
        except Exception:
            return None

    def _update_per_game_egm_d(self) -> None:
        """Dynamic d tuning: adjust EGM extremization per game based on Brier score.

        Games with low Brier (well-calibrated) get higher d (more extreme aggregation).
        Games with high Brier (poor calibration) get lower d (more conservative).
        Range: d ∈ [1.0, 2.5] (IARPA ACE range).
        """
        for game, mult in self._game_kelly_mult.items():
            # Kelly mult encodes Brier quality: 1.2 = good (Brier < 0.20), 0.5 = bad (> 0.25)
            if mult >= 1.2:
                self._game_egm_d[game] = min(2.5, self._egm_d + 0.5)  # More extreme
            elif mult <= 0.5:
                self._game_egm_d[game] = 1.0  # S138: No extremization for poorly calibrated games
            else:
                self._game_egm_d[game] = self._egm_d  # Default

    def _get_edge_decay_sizing_mult(self, game: str) -> float:
        """Return sizing multiplier based on edge decay analysis.

        If a game shows fast edge decay (top time-bin CLV < 0), reduce sizing.
        If edge holds well (all bins CLV > 0), keep at 1.0.
        """
        decay = self._edge_decay_data.get(game)
        if not decay:
            return 1.0
        bins = decay.get("bins", [])
        if not bins:
            return 1.0
        top_bin_clv = bins[0].get("avg_clv", 0)
        if top_bin_clv < -0.05:
            return 0.6  # Significant negative CLV: heavy reduction
        elif top_bin_clv < 0:
            return 0.8  # Mild negative CLV: moderate reduction
        return 1.0

    def _onnx_predict_game(
        self, onnx_session: Any, game_state: Dict, native_model: Any, game: str,
    ) -> float:
        """Predict via ONNX if session available, fallback to native model.predict()."""
        if onnx_session is not None:
            try:
                import numpy as _np
                from esports.models.onnx_compiler import OnnxCompiler
                # Build feature array from game_state in model's FEATURE_NAMES order
                feature_names = getattr(native_model, "FEATURE_NAMES", None)
                if feature_names:
                    feats = [float(game_state.get(f, 0.0)) for f in feature_names]
                    _arr = _np.array([feats], dtype=_np.float32)
                    probs = OnnxCompiler().predict_proba(onnx_session, _arr)
                    return float(probs[0][1])
            except Exception as exc:
                logger.debug("esportsbot_onnx_inference_failed", error=str(exc))
        return native_model.predict(game_state)

    def _load_per_game_onnx_sessions(self) -> None:
        """Load ONNX sessions for per-game models (LoL, CS2, Dota2, Valorant)."""
        import os
        try:
            from esports.models.onnx_compiler import OnnxCompiler
            compiler = OnnxCompiler()
        except ImportError:
            return

        models_dir = os.path.join(os.path.dirname(__file__), "..", "saved_models")
        models_dir = os.path.abspath(models_dir)
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        data_dir = os.path.abspath(data_dir)

        # Map: (attribute, onnx_filename, search_dirs)
        _onnx_map = [
            ("_onnx_lol_session", "lol_win_model.onnx", [models_dir, data_dir]),
            ("_onnx_cs2_session", "cs2_economy_model.onnx", [models_dir, data_dir]),
            ("_onnx_dota2_session", "dota2_xgb.onnx", [models_dir]),
            ("_onnx_valorant_session", "valorant_xgb.onnx", [models_dir]),
        ]

        for attr, filename, dirs in _onnx_map:
            for d in dirs:
                path = os.path.join(d, filename)
                if os.path.exists(path):
                    try:
                        session = compiler.load_session(path)
                        if session is not None:
                            setattr(self, attr, session)
                            logger.info(f"EsportsBot: {filename} ONNX loaded", path=path)
                    except Exception:
                        pass
                    break

    # Cross-game model: game → integer ID (must match esports_trainer.py _GAME_IDS)
    _CROSS_GAME_IDS = {"lol": 0, "cs2": 1, "dota2": 2, "valorant": 3,
                       "cod": 4, "r6": 5, "sc2": 6, "rl": 7}

    def _load_cross_game_model(self) -> None:
        """Load cross-game XGBoost model from saved_models/ (if exists)."""
        import os
        model_path = os.path.join(
            os.path.dirname(__file__), "..", "saved_models", "cross_game_xgb.json",
        )
        model_path = os.path.abspath(model_path)
        if not os.path.exists(model_path):
            return
        try:
            from xgboost import XGBClassifier
            model = XGBClassifier()
            model.load_model(model_path)
            self._cross_game_model = model
            # S142: Check model freshness — stale XGB means 40% of every prediction is garbage
            _age_days = (time.time() - os.path.getmtime(model_path)) / 86400.0
            self._xgb_model_age_days = _age_days
            if _age_days > 14:
                logger.warning("esportsbot_xgb_stale_warning",
                               age_days=round(_age_days, 1), path=model_path)
                self._xgb_stale = True
            else:
                self._xgb_stale = False
            logger.info("EsportsBot: cross_game_xgb loaded",
                        path=model_path, age_days=round(_age_days, 1))

            # Session 82: Try loading ONNX compiled version for faster inference
            try:
                from esports.models.onnx_compiler import OnnxCompiler
                onnx_path = model_path.replace(".json", ".onnx")
                compiler = OnnxCompiler()
                session = compiler.load_session(onnx_path)
                if session is not None:
                    self._onnx_cross_game_session = session
                    logger.info("EsportsBot: cross_game ONNX session loaded", path=onnx_path)
            except Exception:
                pass  # ONNX is optional enhancement
        except Exception as exc:
            logger.warning("EsportsBot: cross_game_xgb load failed", error=str(exc))

    def _extract_team_ids_from_question(
        self, question: str,
    ) -> tuple:
        """Extract team IDs from a market question using 6 regex patterns.

        Returns (team_a_id, team_b_id, clean_name_a, clean_name_b).
        Any of the returned values may be None/empty if extraction fails.
        Shared by _get_glicko2_prediction() and _build_glicko2_game_state().
        """

        # S100b: Strip game prefixes + format suffixes BEFORE extraction.
        # Prevents "lol: teamA vs teamB - game 1 winner" regex confusion
        # where Pattern 6 (" - " dash separator) matches the suffix instead
        # of Pattern 1 ("vs") matching the team separator.
        for _gp in (
            "counter-strike 2: ", "counter-strike: ", "cs2: ", "csgo: ",
            "league of legends: ", "lol: ", "dota 2: ", "dota: ",
            "valorant: ", "call of duty: ", "cod: ",
            "rainbow six siege: ", "rainbow six: ", "r6: ",
            "starcraft ii: ", "starcraft 2: ", "starcraft: ",
            "rocket league: ", "overwatch 2: ", "overwatch: ",
        ):
            if question.startswith(_gp):
                question = question[len(_gp):]
                break
        question = re.sub(r"\s*-\s+(?:game|map|match)\s+\d+\s+winner$", "", question).strip()
        question = re.sub(r"\s*\(bo\d+\).*$", "", question).strip()

        team_a_id = team_b_id = None
        _clean_a = _clean_b = ""

        # Pattern 1: "Team A vs Team B" or "Team A versus Team B"
        vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|v)\s+(.+?)(?:\?|$)", question)
        if vs_match:
            name_a = vs_match.group(1).strip().rstrip(":")
            name_b = vs_match.group(2).strip().rstrip("?").strip()
            for prefix in ("will ", "can ", "does "):
                if name_a.startswith(prefix):
                    name_a = name_a[len(prefix):]
            name_a, name_b = self._clean_team_names(name_a, name_b)
            _clean_a, _clean_b = name_a, name_b
            team_a_id = self._match_team_name(name_a)
            team_b_id = self._match_team_name(name_b)

        # Pattern 2: "Will [Team] beat/defeat [Team]?"
        if not team_a_id or not team_b_id:
            beat_match = re.search(
                r"(?:will\s+)?(.+?)\s+(?:beat|defeat|win against|win over)\s+(.+?)(?:\?|$)",
                question,
            )
            if beat_match:
                ba = beat_match.group(1).strip()
                bb = beat_match.group(2).strip()
                ba, bb = self._clean_team_names(ba, bb)
                _clean_a, _clean_b = ba, bb
                team_a_id = self._match_team_name(ba)
                team_b_id = self._match_team_name(bb)

        # Pattern 3: "Will [Team] win against/over/vs [Team]?"
        if not team_a_id or not team_b_id:
            win_match = re.search(
                r"(?:will\s+)?(.+?)\s+win\s+(?:against|over|vs\.?)\s+(.+?)(?:\?|$)",
                question,
            )
            if win_match:
                wa = win_match.group(1).strip()
                wb = win_match.group(2).strip()
                wa, wb = self._clean_team_names(wa, wb)
                _clean_a, _clean_b = wa, wb
                team_a_id = self._match_team_name(wa)
                team_b_id = self._match_team_name(wb)

        # Pattern 4: "[Team] to win against/over/vs [Team]"
        if not team_a_id or not team_b_id:
            to_win_match = re.search(
                r"(.+?)\s+to\s+win\s+(?:against|over|vs\.?)\s+(.+?)(?:\?|$)",
                question,
            )
            if to_win_match:
                ta = to_win_match.group(1).strip()
                tb = to_win_match.group(2).strip()
                ta, tb = self._clean_team_names(ta, tb)
                _clean_a, _clean_b = ta, tb
                team_a_id = self._match_team_name(ta)
                team_b_id = self._match_team_name(tb)

        # Pattern 5: "[Team] or [Team] — who will win?"
        if not team_a_id or not team_b_id:
            or_match = re.search(
                r"(?:who\s+(?:will\s+)?win[s]?[:\s]+)?(.+?)\s+or\s+(.+?)(?:\?|$)",
                question,
            )
            if or_match:
                oa = or_match.group(1).strip()
                ob = or_match.group(2).strip()
                oa, ob = self._clean_team_names(oa, ob)
                _clean_a, _clean_b = oa, ob
                team_a_id = self._match_team_name(oa)
                team_b_id = self._match_team_name(ob)

        # Pattern 6: "[Team] - [Team]" (dash-separated)
        if not team_a_id or not team_b_id:
            dash_match = re.search(
                r"(.+?)\s+-\s+(.+?)(?:\?|$)", question,
            )
            if dash_match:
                da = dash_match.group(1).strip()
                db = dash_match.group(2).strip()
                for prefix in ("will ", "can ", "does "):
                    if da.startswith(prefix):
                        da = da[len(prefix):]
                da, db = self._clean_team_names(da, db)
                _clean_a, _clean_b = da, db
                team_a_id = self._match_team_name(da)
                team_b_id = self._match_team_name(db)

        return team_a_id, team_b_id, _clean_a, _clean_b

    def _build_glicko2_game_state(
        self, market_data: Dict, game: str
    ) -> Optional[Dict[str, float]]:
        """Build a feature dict from Glicko-2 ratings for dota2/valorant ML models.

        Extracts team names from market question, looks up Glicko-2 ratings,
        and returns the 6-feature dict expected by Dota2Model/ValorantModel.
        """
        tracker = self._glicko2_trackers.get(game)
        if tracker is None:
            return None

        question = str(market_data.get("question", "")).lower()
        if not question:
            return None

        team_a_id, team_b_id, _, _ = self._extract_team_ids_from_question(question)

        if not team_a_id or not team_b_id:
            return None

        try:
            rating_a = tracker.get_rating(team_a_id)
            rating_b = tracker.get_rating(team_b_id)
            expected = tracker.expected_score(team_a_id, team_b_id)

            # P6.5 parity: compute inference-time proxy for rolling win rate.
            # expected_score(team, neutral_ref) maps Glicko-2 rating to [0,1],
            # correlated with recent winning — same range as training-time form.
            from esports.models.glicko2 import Glicko2Rating as _G2R, expected_score as _g2es
            _ref = _G2R()  # neutral: mu=1500, phi=350, sigma=0.06
            return {
                "team_strength_diff": expected - 0.5,
                "matchup_uncertainty": (rating_a.phi + rating_b.phi) / 700.0,
                "rd_asymmetry": (rating_a.phi - rating_b.phi) / 350.0,
                "team_a_volatility": rating_a.sigma / 0.06,
                "team_b_volatility": rating_b.sigma / 0.06,
                "best_of": 1.0,  # Default; overridden if series data available
                "team_a_recent_form": float(_g2es(rating_a, _ref)),
                "team_b_recent_form": float(_g2es(rating_b, _ref)),
                # S141: expose raw phi for Baker-McHale sigma_model
                "_phi_a": rating_a.phi,
                "_phi_b": rating_b.phi,
            }
        except Exception:
            return None

    @staticmethod
    def _clean_team_names(name_a: str, name_b: str) -> tuple:
        """Strip game title prefixes, tournament/format suffixes, and normalize team names.

        Regex captures game titles (e.g. "counter-strike: themongolz") and
        tournament suffixes (e.g. "pain (bo3) - esl pro league stage 2").
        Also normalizes common abbreviations and formatting artifacts.
        """
        _game_prefixes = (
            "counter-strike 2: ", "counter-strike: ", "cs2: ", "csgo: ",
            "league of legends: ", "lol: ",
            "dota 2: ", "dota: ",
            "valorant: ",
            "call of duty: ", "cod: ",
            "rainbow six siege: ", "rainbow six: ", "r6: ",
            "starcraft ii: ", "starcraft 2: ", "starcraft: ",
            "rocket league: ",
            "overwatch 2: ", "overwatch: ",
        )
        for gp in _game_prefixes:
            if name_a.startswith(gp):
                name_a = name_a[len(gp):]
            if name_b.startswith(gp):
                name_b = name_b[len(gp):]
        # Strip tournament/format suffixes: "(bo3)", "- esl pro league ...", etc.
        _suffix_re = r"\s*\(bo\d+\).*$"
        _tourney_re = (
            r"\s*-\s+(?:esl |blast |pgl |iem |dreamhack|faceit|weplay|rievent"
            r"|major|champions|masters|challengers|lck|lpl|lec|lcs|vct|worlds"
            r"|msi |msc |lcl|cblol|ljl|pcs|spring|summer|winter|fall|split"
            r"|playoffs|qualifier|group|stage|round|final"
            r"|aorus |emea |nacl |lta |game changers|winner"
            r"|cct |gamers8|betboom|perfect\s*world|thunderpick|elisa |skyesports"
            r"|yalla|esea |open\s+qual|closed\s+qual|regular\s+season"
            r"|upper\s+bracket|lower\s+bracket|grand\s+final|elimination"
            r"|decider|promotion|relegation|showmatch|invitational"
            r"|lan\s+final|rmr |asia\s+league|americas|pacific|emea\s+league).*$"
        )
        # " - game N winner" / " - map N winner" suffix (must run before _tourney_re)
        _game_winner_re = r"\s*-\s+(?:game|map|match)\s+\d+\s+winner$"
        for name_ref in ("name_a", "name_b"):
            n = name_a if name_ref == "name_a" else name_b
            n = re.sub(_suffix_re, "", n).strip()
            n = re.sub(_game_winner_re, "", n, flags=re.IGNORECASE).strip()
            n = re.sub(_tourney_re, "", n, flags=re.IGNORECASE).strip()
            # Strip trailing " map N", " game N", " game N winner", or bare "game N winner"
            n = re.sub(r"(?:^|\s+)(?:map|game)\s+\d+(?:\s+winner)?$", "", n, flags=re.IGNORECASE).strip()
            # Strip region tags in parens: "(KR)", "(CN)", "(EU)"
            n = re.sub(r"\s*\([A-Z]{2,4}\)\s*$", "", n).strip()
            if name_ref == "name_a":
                name_a = n
            else:
                name_b = n
        return name_a.strip(), name_b.strip()

    # Common team name aliases: market_name → pandascore_name
    _TEAM_ALIASES: Dict[str, str] = {
        # China (LPL)
        "jdg": "jd gaming", "edg": "edward gaming", "rng": "royal never give up",
        "fpx": "funplus phoenix", "blg": "bilibili gaming", "lng": "lng esports",
        "tes": "top esports", "we": "team we", "wbg": "weibo gaming",
        "ig": "invictus gaming", "ra": "rare atom", "omg": "oh my god",
        # Korea (LCK)
        "gen": "gen.g", "geng": "gen.g", "dk": "dplus kia", "dplus": "dplus kia",
        "drx": "drx", "kt": "kt rolster", "hle": "hanwha life esports",
        "bro": "fredit brion", "ns": "nongshim redforce", "lsb": "liiv sandbox",
        "fox": "foxx esports",
        # EU/West (LEC/LCS)
        "g2": "g2 esports", "fnc": "fnatic", "mad": "mad lions",
        "sk": "sk gaming", "xls": "excel esports", "msf": "misfits gaming",
        "vit": "team vitality", "bds": "team bds", "koi": "koi",
        "tl": "team liquid", "c9": "cloud9", "100t": "100 thieves",
        "eg": "evil geniuses", "fly": "flyquest", "dig": "dignitas",
        "tsm": "tsm", "clg": "counter logic gaming", "gg": "golden guardians",
        "nrg": "nrg esports", "sr": "shopify rebellion",
        # CS2
        "navi": "natus vincere", "na'vi": "natus vincere",
        "faze": "faze clan", "col": "complexity gaming",
        "mouz": "mouz", "mousesports": "mouz", "nip": "ninjas in pyjamas",
        "heroic": "heroic", "ence": "ence", "ef": "eternal fire",
        "saw": "saw", "gl": "gamerlegion", "gamerlegion": "gamerlegion",
        "big": "big", "apeks": "apeks", "aurora": "aurora gaming",
        "3dmax": "3dmax", "imp": "imperial esports", "imperial": "imperial esports",
        "pain": "pain gaming", "mibr": "mibr", "furia": "furia",
        "9z": "9z team", "wildcard": "wildcard gaming",
        "grayhound": "grayhound gaming", "tyloo": "tyloo",
        "lynn vision": "lynn vision gaming", "mongols": "the mongolz",
        "the mongolz": "the mongolz",
        # Dota 2
        "og": "og", "nigma": "team nigma", "bb": "betboom team",
        "spirit": "team spirit", "vp": "virtus.pro",
        "tundra": "tundra esports", "gaimin": "gaimin gladiators",
        "xtreme": "xtreme gaming", "nouns": "nouns esports",
        # Valorant
        "prx": "paper rex", "zeta": "zeta division",
        "loud": "loud", "lev": "leviatán", "sen": "sentinels",
        "rrq": "rex regum qeon", "th": "team heretics",
        "kcorp": "karmine corp", "fut": "fut esports",
        "bleed": "bleed esports", "dfm": "detonation focusme",
        "geng": "gen.g",  # Valorant also uses geng
        # SC2
        "serral": "serral", "clem": "clem", "maru": "maru", "hero": "hero",
        "reynor": "reynor", "maxpax": "maxpax", "oliveira": "oliveira",
        "dark": "dark", "byun": "byun", "trap": "trap",
        # S136 Phase 6A: Expanded aliases from common market question patterns
        "ktr": "kt rolster", "rolster": "kt rolster",
        "dwg": "dplus kia",
        "ast": "astralis",
        "100": "100 thieves",
        "mous": "mousesports",
        "kru": "kru esports",
        "betboom": "betboom team",
        "entity": "entity gaming",
        # Multi-game
        "weibo": "weibo gaming",
        "t1": "t1", "skt": "t1", "skt1": "t1", "sk telecom": "t1",
        "drx": "drx", "kwangdong freecs": "kwangdong freecs", "kdf": "kwangdong freecs",
        "al": "anyone's legend", "anyone's legend": "anyone's legend",
        "tt": "thunder talk gaming", "ttg": "thunder talk gaming",
        "lgd": "lgd gaming", "psg": "lgd gaming",
        "nip": "ninjas in pyjamas",
    }

    def _match_team_name(self, name: str) -> Optional[str]:
        """Fuzzy match a team name to a PandaScore team ID.

        Tries: exact match → alias lookup → longest-substring-first match →
        reverse substring (name in known_name, for long market names) →
        word-boundary match for short names (2-3 chars) →
        difflib fuzzy match (0.78 threshold, last resort for typos).
        """
        name = name.lower().strip()
        if not name:
            return None

        # Exact match
        tid = self._team_name_to_id.get(name)
        if tid:
            return tid

        # Alias lookup: e.g. "jdg" → "jd gaming" → team_id
        alias = self._TEAM_ALIASES.get(name)
        if alias:
            tid = self._team_name_to_id.get(alias)
            if tid:
                return tid

        # Substring match: longest known name first to prevent short-name collision.
        for known_name, tid in sorted(
            self._team_name_to_id.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            # Skip very short known names for substring (handled by word-boundary below)
            if len(known_name) <= 3:
                continue
            if known_name in name:
                return tid

        # Reverse substring: market name may contain the full team name
        # e.g. name="hanwha life esports academy" contains known "hanwha life esports"
        for known_name, tid in sorted(
            self._team_name_to_id.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if len(known_name) <= 3:
                continue
            if name in known_name:
                return tid

        # Word-boundary match for short names (2-3 chars): "t1", "g2", "og"
        # Must match as whole word to avoid false positives
        for known_name, tid in self._team_name_to_id.items():
            if len(known_name) <= 3:
                if re.search(r'\b' + re.escape(known_name) + r'\b', name):
                    return tid

        # Tier 6: fuzzy match via difflib (stdlib) — last resort for typos/transliterations
        # S136 6B: Lower threshold for long names (5+ chars) to catch more matches
        from difflib import SequenceMatcher as _SM
        best_ratio, best_tid, best_match = 0.0, None, None
        for known_name, tid in self._team_name_to_id.items():
            if len(known_name) <= 2:
                continue
            ratio = _SM(None, name, known_name).ratio()
            if ratio > best_ratio:
                best_ratio, best_tid, best_match = ratio, tid, known_name
        # Short names (<5 chars) need higher threshold to avoid false positives
        threshold = 0.73 if len(name) >= 5 else 0.78
        if best_ratio >= threshold and best_tid is not None:
            logger.info("esportsbot_fuzzy_match", query=name, matched=best_match, ratio=round(best_ratio, 3))
            return best_tid

        return None

    # ── Series Analysis (merged from EsportsSeriesBot) ────────────────────

    async def _series_scan(self) -> tuple:
        """Orchestrate series analysis: refresh, analyze, allocate, execute.

        Returns (opportunities_count, trades_count).
        """
        db = getattr(self.base_engine, "db", None)

        # Prune stale series prediction cache (>30 min)
        now = time.monotonic()
        stale = [k for k, v in self._series_prediction_cache.items()
                 if now - v.get("ts", 0) > 1800]
        for k in stale:
            del self._series_prediction_cache[k]

        self._series_refresh()

        if not self._active_series:
            return (0, 0)

        all_opps: List[Dict] = []
        for match_id, series_data in list(self._active_series.items()):
            try:
                opps = await self._series_analyze(match_id, series_data, db)
                all_opps.extend(opps)
            except Exception as exc:
                logger.debug(
                    "esports_series_analysis_error",
                    match_id=match_id,
                    error=str(exc),
                )

        _trades = 0

        # S110: Apply the same anti-churn gates as _analyze_one and WS path.
        # Without these, _series_scan was an unguarded backdoor that bypassed
        # exit cooldown and per-market entry caps — root cause of Mar 19 churn.
        # S143: Use reason-aware cooldowns matching WS/scan paths.
        _cooldown_default = float(getattr(settings, "ESPORTS_EXIT_COOLDOWN_SECONDS", 300.0))
        _cooldown_edge = float(getattr(settings, "ESPORTS_EDGE_GONE_COOLDOWN_SECONDS", 1800.0))
        _max_entries = int(getattr(settings, "ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW", 2))
        _window_s = float(getattr(settings, "ESPORTS_ENTRY_WINDOW_HOURS", 12.0)) * 3600
        _now_mono = time.monotonic()

        def _churn_blocked(mid: str) -> bool:
            """Return True if market is blocked by anti-churn gates."""
            # S157: Flat post-exit cooldown (hysteresis handles churn, escalating removed)
            _exit_ts = self._recently_exited.get(mid)
            if _exit_ts is not None:
                _reason = self._exit_reasons.get(mid, "")
                _cd = _cooldown_edge if _reason in ("edge_gone", "trailing_edge") else _cooldown_default
                if _now_mono - _exit_ts < _cd:
                    return True
            # Gate 2: Per-market rolling entry cap
            _recent = [t for t in self._market_entry_times.get(mid, []) if _now_mono - t < _window_s]
            if len(_recent) >= _max_entries:
                return True
            return False

        if len(all_opps) >= 2:
            max_daily = float(getattr(settings, "ESPORTS_MAX_DAILY_USD", 500.0))
            daily_spent = 0.0
            gw = getattr(self.base_engine, "order_gateway", None)
            if gw:
                daily_exposure = getattr(gw, "_daily_exposure_usd", {})
                daily_spent = float(daily_exposure.get(self.bot_name, 0.0))
            group_budget = max(0.0, max_daily * 0.5 - daily_spent)

            st_sizes = self._series_smoczynski_tomkins_allocate(all_opps, group_budget)
            if st_sizes:
                logger.info(
                    "esports_series_st_allocation",
                    n_opps=len(st_sizes),
                    total_usd=round(sum(st_sizes.values()), 2),
                    budget=round(group_budget, 2),
                )
            for opp in all_opps:
                mid = opp.get("market_id", "")
                if _churn_blocked(mid):
                    continue
                st_size = st_sizes.get(mid)
                if st_size and st_size >= 1.0:
                    opp["_st_size_override"] = st_size
                    async with self._trade_lock:
                        _ok = await self._execute_esports_trade(opp)
                    if _ok:
                        self._market_entry_times.setdefault(mid, []).append(time.monotonic())
                        await self._save_entry_count_to_redis(mid)
                        _trades += 1
        else:
            for opp in all_opps:
                mid = opp.get("market_id", "")
                if _churn_blocked(mid):
                    continue
                async with self._trade_lock:
                    _ok = await self._execute_esports_trade(opp)
                if _ok:
                    self._market_entry_times.setdefault(mid, []).append(time.monotonic())
                    await self._save_entry_count_to_redis(mid)
                    _trades += 1

        return (len(all_opps), _trades)

    def _series_refresh(self) -> None:
        """Filter _live_matches for BO3+ series. No API call — reuses existing data."""
        now = time.monotonic()
        if now - self._series_last_refresh < self._series_refresh_interval:
            return
        self._series_last_refresh = now

        new_series: Dict[str, Dict] = {}
        for mid, match in self._live_matches.items():
            best_of = 1
            if isinstance(match, dict):
                best_of = int(match.get("best_of", 1))
            else:
                best_of = int(getattr(match, "best_of", 1))
            if best_of < 3:
                continue

            if isinstance(match, dict):
                team_a = match.get("team_a", "")
                team_b = match.get("team_b", "")
                score_a = match.get("score_a", 0)
                score_b = match.get("score_b", 0)
                game = match.get("game", "")
            else:
                team_a = getattr(match, "team_a", "")
                team_b = getattr(match, "team_b", "")
                score_a = getattr(match, "score_a", 0)
                score_b = getattr(match, "score_b", 0)
                game = getattr(match, "game", "")

            new_series[mid] = {
                "match_id": mid,
                "game": game,
                "team_a": team_a,
                "team_b": team_b,
                "score_maps_a": int(score_a),
                "score_maps_b": int(score_b),
                "best_of": best_of,
            }

        stale = set(self._active_series) - set(new_series)
        if stale:
            logger.debug(
                "esports_series_pruned",
                pruned=len(stale),
                match_ids=list(stale)[:5],
            )

        self._active_series = new_series

    async def _series_analyze(
        self, match_id: str, series_data: Dict, db=None
    ) -> List[Dict]:
        """Compute conditional match probability and compare to market."""
        from esports.models.series_model import (
            bo3_match_prob,
            bo5_match_prob,
            series_prob_with_map_veto,
        )

        best_of = int(series_data.get("best_of", 1))
        if best_of < 3:
            return []

        maps_a = int(series_data.get("score_maps_a", 0))
        maps_b = int(series_data.get("score_maps_b", 0))
        game = series_data.get("game", "")
        team_a = series_data.get("team_a", "")
        team_b = series_data.get("team_b", "")

        needed = (best_of + 1) // 2
        if maps_a >= needed or maps_b >= needed:
            return []

        # Get per-map win rates: DB first, HLTV fallback (CS2 only)
        map_rates_a: Dict[str, float] = {}
        map_rates_b: Dict[str, float] = {}
        if game == "cs2":
            if db:
                try:
                    from esports.data.esports_db import get_team_map_rates
                    map_rates_a = await get_team_map_rates(db, team_a, game="cs2")
                    map_rates_b = await get_team_map_rates(db, team_b, game="cs2")
                except Exception:
                    pass
            if not map_rates_a and self._hltv:
                try:
                    map_rates_a = await self._hltv.get_map_win_rates(team_a)
                except Exception:
                    pass
            if not map_rates_b and self._hltv:
                try:
                    map_rates_b = await self._hltv.get_map_win_rates(team_b)
                except Exception:
                    pass

        # Compute conditional probability
        if map_rates_a and map_rates_b:
            veto_order = self._series_derive_veto_order(
                map_rates_a, map_rates_b, best_of
            )
            if veto_order:
                try:
                    model_prob = series_prob_with_map_veto(
                        team_a_map_rates=map_rates_a,
                        team_b_map_rates=map_rates_b,
                        veto_order=veto_order,
                        maps_won_a=maps_a,
                        maps_won_b=maps_b,
                    )
                except Exception:
                    model_prob = self._series_simple_prob(
                        maps_a, maps_b, best_of
                    )
            else:
                model_prob = self._series_simple_prob(maps_a, maps_b, best_of)
        else:
            glicko_prob = await self._series_get_glicko2_expected_score(
                game, team_a, team_b, db
            )
            model_prob = self._series_simple_prob(
                maps_a, maps_b, best_of, per_map_prob=glicko_prob
            )

        # Series draft adjustment [Session D]: blend CatBoost draft prob into series model
        if (getattr(settings, "ESPORTS_SERIES_DRAFT_ADJUST_ENABLED", False)
                and model_prob is not None
                and game in self._catboost_models
                and self._catboost_models[game].is_fitted
                and self._draft_feature_builder is not None):
            _series_draft = self._get_series_latest_draft(match_id)
            if _series_draft:
                try:
                    _draft_feats = self._draft_feature_builder.build_features(
                        _series_draft, game, team_a, team_b,
                    )
                    # Add Glicko-2 features
                    _g_cache = self._series_glicko2_cache.get(f"{game}:{team_a}:{team_b}")
                    if _g_cache:
                        _draft_feats["team_strength_diff"] = _g_cache - 0.5
                    _cb_prob = self._catboost_models[game].predict_proba(_draft_feats)
                    if 0.05 < _cb_prob < 0.95:
                        _blend_w = float(getattr(
                            settings, "ESPORTS_SERIES_DRAFT_BLEND_WEIGHT", 0.3,
                        ))
                        model_prob = (1.0 - _blend_w) * model_prob + _blend_w * _cb_prob
                        logger.debug(
                            "esportsbot_series_draft_adjust",
                            match_id=match_id, game=game,
                            catboost_prob=round(_cb_prob, 4),
                            blended_prob=round(model_prob, 4),
                        )
                except Exception as _sd_exc:
                    logger.debug("esportsbot_series_draft_failed", error=str(_sd_exc))

        if model_prob is None or not (0.01 < model_prob < 0.99):
            return []

        market_info = await self._series_find_market(
            match_id, game, team_a, team_b, db
        )
        if not market_info:
            return []

        market_price = market_info.get("price", 0.5)
        market_id = market_info.get("market_id")
        token_id = market_info.get("token_id")

        if not market_id or not token_id:
            return []

        edge_yes = model_prob - market_price
        edge_no = market_price - model_prob

        # S139: Divergence cap — series scan path was the only path with no cap.
        _div_sa = abs(model_prob - market_price)
        _eff_div_cap_sa = min(
            self._get_adaptive_div_cap(game),
            float(getattr(settings, "ESPORTS_MAX_MODEL_DIVERGENCE", 0.25)),
        )
        if _div_sa > _eff_div_cap_sa:
            logger.info(
                "esportsbot_divergence_capped_series", market_id=market_id, game=game,
                model_prob=round(model_prob, 4), market_price=round(market_price, 4),
                divergence=round(_div_sa, 4), cap=_eff_div_cap_sa,
            )
            return []

        side = None
        trade_token_id = token_id
        trade_price = market_price
        edge = 0.0

        if edge_yes >= self._series_min_edge:
            side = "YES"
            edge = edge_yes
        elif edge_no >= self._series_min_edge:
            side = "NO"
            trade_price = 1.0 - market_price
            edge = edge_no
            no_token_id = market_info.get("no_token_id")
            if no_token_id:
                trade_token_id = no_token_id

        if not side:
            return []

        # Don't trade if market already prices in reverse sweep
        if (maps_a > maps_b and side == "NO") or (maps_b > maps_a and side == "YES"):
            trailing_price = market_price if side == "NO" else (1.0 - market_price)
            if trailing_price > self._series_reverse_sweep_floor:
                logger.debug(
                    "esports_series_reverse_sweep_priced_in",
                    match_id=match_id,
                    trailing_price=round(trailing_price, 3),
                    floor=self._series_reverse_sweep_floor,
                )
                return []

        side_prob = model_prob if side == "YES" else (1.0 - model_prob)
        _sq, _ = self._compute_signal_quality(game, market_id)
        # S131: SQ is sizing multiplier, confidence = raw side_prob
        confidence = side_prob

        _token_map = self._market_token_map.get(market_id, {})

        # Log prediction
        try:
            from esports.data.esports_db import log_prediction
            await log_prediction(
                db=db,
                match_id=match_id,
                game=game,
                market_id=market_id,
                bot_name="EsportsBot",
                predicted_prob=model_prob,
                market_price=market_price,
                side=side,
                edge=round(edge, 4),
                raw_model_prob=model_prob,  # WS path has no calibration, raw=calibrated
            )
        except Exception as exc:
            logger.warning("esports_series_prediction_log_failed", error=str(exc))

        # S142: Pull BM sigma inputs from Glicko-2 tracker for series trades
        _phi_a_s, _phi_b_s = 200.0, 200.0
        _tracker_s = self._glicko2_trackers.get(game)
        if _tracker_s:
            _ra_s = _tracker_s.get_rating(team_a.lower()) if team_a else None
            _rb_s = _tracker_s.get_rating(team_b.lower()) if team_b else None
            if _ra_s:
                _phi_a_s = _ra_s.phi
            if _rb_s:
                _phi_b_s = _rb_s.phi

        # Cache prediction for WS reactive path (include token info + phi for BM sigma)
        self._series_prediction_cache[market_id] = {
            "prob": model_prob, "ts": time.monotonic(), "game": game,
            "yes_token_id": _token_map.get("yes", str(token_id)),
            "no_token_id": _token_map.get("no", ""),
            "event_data": {"_phi_a": _phi_a_s, "_phi_b": _phi_b_s},
        }

        match_opp = {
            "type": "esports_series",
            "market_id": market_id,
            "token_id": str(trade_token_id),
            "side": side,
            "price": trade_price,
            "confidence": confidence,
            "prediction": model_prob,
            "edge": round(edge, 4),
            "game": game,
            "market_type": "match_winner",
            "series_score": f"{maps_a}-{maps_b}",
            "best_of": best_of,
            "_signal_quality": _sq,
            "_phi_a": _phi_a_s,
            "_phi_b": _phi_b_s,
        }
        result = [match_opp]

        # Hedge: current map market
        # S160: Use per-map probability (Glicko-2 expected_score), NOT series probability
        if self._series_hedge_enabled:
            current_map = maps_a + maps_b + 1
            map_market = await self._series_find_map_market(
                match_id, game, team_a, team_b, current_map, db
            )
            if map_market:
                per_map_prob = await self._series_get_glicko2_expected_score(game, team_a, team_b, db)
                if per_map_prob is None:
                    per_map_prob = 0.50
                map_price = float(map_market.get("price") or 0.5)
                map_edge_yes = per_map_prob - map_price
                map_edge_no = map_price - per_map_prob
                map_side = None
                map_trade_price = map_price
                map_edge_val = 0.0
                if side == "YES" and map_edge_yes >= self._series_min_edge:
                    map_side = "YES"
                    map_edge_val = map_edge_yes
                elif side == "NO" and map_edge_no >= self._series_min_edge:
                    map_side = "NO"
                    map_trade_price = 1.0 - map_price
                    map_edge_val = map_edge_no
                if map_side:
                    map_conf = per_map_prob if map_side == "YES" else (1.0 - per_map_prob)
                    result.append({
                        "type": "esports_series_hedge",
                        "market_id": map_market["market_id"],
                        "token_id": str(map_market["token_id"]),
                        "side": map_side,
                        "price": map_trade_price,
                        "confidence": map_conf,
                        "prediction": model_prob,
                        "edge": round(map_edge_val, 4),
                        "game": game,
                        "market_type": "map_winner",
                        "series_score": f"{maps_a}-{maps_b}",
                        "map_number": current_map,
                    })

        return result

    def _get_series_latest_draft(self, match_id: str) -> Optional[Dict]:
        """Extract draft data from the most recent game in an active series.

        Returns draft dict if available, None otherwise.
        """
        series_data = self._active_series.get(match_id)
        if not series_data or not isinstance(series_data, dict):
            return None

        # Check live match data for this match_id
        live_match = self._live_matches.get(match_id)
        if isinstance(live_match, dict):
            draft = live_match.get("draft")
            if isinstance(draft, dict) and (
                draft.get("team_a_picks") or draft.get("team_b_picks")
            ):
                return draft

        # Check game state from the series prediction cache
        cache = self._series_prediction_cache.get(match_id, {})
        game_state = cache.get("game_state", {})
        if isinstance(game_state, dict):
            draft = game_state.get("draft")
            if isinstance(draft, dict) and (
                draft.get("team_a_picks") or draft.get("team_b_picks")
            ):
                return draft

        return None

    async def _series_get_glicko2_expected_score(
        self, game: str, team_a: str, team_b: str, db,
    ) -> Optional[float]:
        """Glicko-2 expected score for series per-map probability fallback."""
        cache_key = f"{game}:{team_a.lower()}:{team_b.lower()}"
        if cache_key in self._series_glicko2_cache:
            return self._series_glicko2_cache[cache_key]

        if not db:
            return None

        try:
            from sqlalchemy import text as _sg_text
            async with db.get_session(timeout=10) as _sg_session:
                _sg_result = await _sg_session.execute(
                    _sg_text(
                        "SELECT team_key, mu, phi, sigma, match_count "
                        "FROM glicko2_ratings WHERE game = :game "
                        "AND team_key = ANY(:teams)"
                    ),
                    {"game": game, "teams": [team_a.lower().strip(), team_b.lower().strip()]},
                )
                rows = [dict(r._mapping) for r in _sg_result.fetchall()]
            if len(rows) < 2:
                return None

            from esports.models.glicko2 import Glicko2Rating, expected_score
            ratings = {}
            for r in rows:
                if int(r.get("match_count", 0) or 0) < 10:
                    return None
                ratings[r["team_key"]] = Glicko2Rating(
                    mu=float(r["mu"]), phi=float(r["phi"]),
                    sigma=float(r.get("sigma", 0.06) or 0.06),
                )

            ra = ratings.get(team_a.lower().strip())
            rb = ratings.get(team_b.lower().strip())
            if ra is None or rb is None:
                return None

            prob = expected_score(ra, rb)
            if prob < 0.05 or prob > 0.95:
                return None

            self._series_glicko2_cache[cache_key] = prob
            return prob
        except Exception:
            return None

    def _series_simple_prob(
        self, maps_a: int, maps_b: int, best_of: int,
        per_map_prob: Optional[float] = None,
    ) -> Optional[float]:
        """Fallback: use Glicko-2 expected score (or 0.50) for series probability."""
        from esports.models.series_model import bo3_match_prob, bo5_match_prob

        p = per_map_prob if per_map_prob is not None else 0.50
        if best_of == 3:
            return bo3_match_prob(p, maps_a, maps_b)
        elif best_of == 5:
            return bo5_match_prob(p, maps_a, maps_b)
        return None

    @staticmethod
    def _series_smoczynski_tomkins_allocate(
        opps: List[Dict], group_budget: float, kelly_mult: float = 0.25,
    ) -> Dict[str, float]:
        """Optimal Kelly allocation for correlated series bets."""
        if not opps or group_budget <= 0:
            return {}

        edges: Dict[str, float] = {}
        for opp in opps:
            p = opp.get("confidence", 0.5)
            price = opp.get("price", 0.5)
            if price <= 0.02 or price >= 0.98 or p <= price:
                continue
            b = (1.0 - price) / price
            if b <= 0:
                continue
            q = 1.0 - p
            f_i = (p * b - q) / b
            if f_i > 0:
                edges[opp["market_id"]] = f_i

        if not edges:
            return {}

        total_edge = sum(edges.values())
        if total_edge <= 0:
            return {}

        allocations = {}
        for mid, f_i in edges.items():
            share = (f_i / total_edge) * kelly_mult * group_budget
            allocations[mid] = round(max(1.0, share), 2)

        total = sum(allocations.values())
        if total > group_budget:
            scale = group_budget / total
            allocations = {mid: round(v * scale, 2) for mid, v in allocations.items()}

        return allocations

    @staticmethod
    def _series_derive_veto_order(
        rates_a: Dict[str, float],
        rates_b: Dict[str, float],
        best_of: int,
    ) -> List[str]:
        """Derive plausible map veto order from team map preferences."""
        pool = set(rates_a.keys()) | set(rates_b.keys())
        if len(pool) < best_of:
            return []

        picks_per_team = 1 if best_of == 3 else 2
        veto_order: List[str] = []
        remaining = set(pool)

        a_sorted = sorted(remaining, key=lambda m: rates_a.get(m, 0.5), reverse=True)
        for m in a_sorted[:picks_per_team]:
            veto_order.append(m)
            remaining.discard(m)

        b_sorted = sorted(remaining, key=lambda m: rates_b.get(m, 0.5), reverse=True)
        for m in b_sorted[:picks_per_team]:
            veto_order.append(m)
            remaining.discard(m)

        if remaining:
            decider = max(
                remaining,
                key=lambda m: rates_a.get(m, 0.5) + rates_b.get(m, 0.5),
            )
            veto_order.append(decider)

        return veto_order[:best_of]

    async def _series_find_market(
        self, match_id: str, game: str, team_a: str, team_b: str, db=None,
    ) -> Optional[Dict]:
        """Find matching Polymarket match-winner market for this series."""
        if not self._market_scanner:
            return None
        try:
            team_names = [n for n in (team_a, team_b) if n]
            markets = await asyncio.wait_for(
                self._market_scanner.find_markets_for_match(
                    match_id, game, db=db, team_names=team_names or None,
                ),
                timeout=5.0,
            )
            if markets:
                return markets[0]
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    async def _series_find_map_market(
        self, match_id: str, game: str, team_a: str, team_b: str,
        current_map: int, db=None,
    ) -> Optional[Dict]:
        """Find Polymarket map-winner market for the current map being played."""
        if not self._market_scanner:
            return None
        try:
            team_names = [n for n in (team_a, team_b) if n]
            markets = await asyncio.wait_for(
                self._market_scanner.find_markets_for_match(
                    match_id, game, db=db, team_names=team_names or None,
                ),
                timeout=5.0,
            )
            map_patterns = [f"map {current_map}", f"game {current_map}"]
            for m in (markets or []):
                if m.get("market_type") != "map_winner":
                    continue
                q = str(m.get("question", "")).lower()
                if any(p in q for p in map_patterns):
                    return m
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    async def _series_on_price_update(self, event: dict) -> None:
        """WS reactive path for series predictions.

        Uses _passes_ws_entry_gates() shared with on_price_update to prevent
        gate drift (the original cause of missing 12 safety gates — S155 audit).
        """
        market_id = event.get("market_id", "")
        token_id = event.get("token_id", "")
        new_price = float(event.get("price", 0))
        if not market_id or new_price <= 0:
            return

        cached = self._series_prediction_cache.get(market_id)
        if not cached:
            return

        # NO price conversion: identify token and convert to YES-equivalent
        _yes_tid = cached.get("yes_token_id", "")
        _no_tid = cached.get("no_token_id", "")
        if _yes_tid and _no_tid:
            if token_id == _yes_tid:
                yes_price = new_price
            elif token_id == _no_tid:
                yes_price = 1.0 - new_price
            else:
                return  # Unknown token — can't safely compute edge
        elif _yes_tid and token_id == _yes_tid:
            yes_price = new_price
        else:
            # No token map — cannot distinguish YES/NO, skip
            return

        # Significance threshold — keyed by token_id (not market_id) to avoid
        # YES/NO price cross-contamination. Stores YES-normalized price.
        # _series_ws_cooldowns stays market_id-keyed (trade cooldown, not price tracking).
        threshold = float(getattr(settings, "ESPORTS_SERIES_WS_PRICE_CHANGE_PCT", 0.01))
        old_yes_price = self._series_ws_prev_prices.get(token_id)
        self._series_ws_prev_prices[token_id] = yes_price
        if old_yes_price is None or abs(yes_price - old_yes_price) / max(old_yes_price, 0.01) < threshold:
            return

        # Cooldown
        now = time.monotonic()
        cooldown = int(getattr(settings, "ESPORTS_SERIES_WS_COOLDOWN_SECONDS", 10))
        if now - self._series_ws_cooldowns.get(market_id, 0) < cooldown:
            return
        self._series_ws_cooldowns[market_id] = now

        model_prob = cached["prob"]
        _series_ws_game = cached.get("game", "")
        edge = model_prob - yes_price

        # Divergence cap
        _div_sws = abs(edge)
        _eff_div_cap_sws = min(
            self._get_adaptive_div_cap(_series_ws_game),
            float(getattr(settings, "ESPORTS_MAX_MODEL_DIVERGENCE", 0.25)),
        )
        if _div_sws > _eff_div_cap_sws:
            logger.info(
                "esportsbot_divergence_capped_series_ws", market_id=market_id,
                game=_series_ws_game,
                model_prob=round(model_prob, 4), market_price=round(yes_price, 4),
                divergence=round(_div_sws, 4), cap=_eff_div_cap_sws,
            )
            return

        # S157: Edge hysteresis — use higher entry threshold for new series positions
        _sws_min_edge = max(self._series_min_edge, self._min_edge_entry)
        if abs(edge) < _sws_min_edge:
            return

        side = "YES" if edge > 0 else "NO"
        trade_price = yes_price if side == "YES" else (1.0 - yes_price)

        # Shared entry gates (daily loss, game disable, exposure, price, cooldown, etc.)
        if not self._passes_ws_entry_gates(market_id, side, _series_ws_game, trade_price):
            return

        self._ws_pending_trades.add(market_id)
        try:
            side_prob = model_prob if side == "YES" else (1.0 - model_prob)
            _sq, _ = self._compute_signal_quality(_series_ws_game, market_id)
            confidence = side_prob
            _cached_ed_sws = cached.get("event_data") or {}
            # S158: Use correct token_id for the chosen side (was using raw WS token regardless)
            _sws_token_id = (cached.get("no_token_id") or token_id) if side == "NO" else (cached.get("yes_token_id") or token_id)
            opp = {
                "type": "esports_series_ws",
                "market_id": market_id,
                "token_id": _sws_token_id,
                "side": side,
                "price": trade_price,
                "confidence": confidence,
                "prediction": model_prob,
                "edge": round(abs(edge), 4),
                "game": _series_ws_game,
                "market_type": "match_winner",
                "_signal_quality": _sq,
                "_phi_a": float(_cached_ed_sws.get("_phi_a", 200.0)),
                "_phi_b": float(_cached_ed_sws.get("_phi_b", 200.0)),
                "_conformal_width": float(_cached_ed_sws.get("_conformal_width", 0.15)),
                "_agreement_stdev": float(_cached_ed_sws.get("_agreement_stdev", 0.10)),
            }
            logger.info(
                "esports_series_ws_reactive",
                market_id=market_id,
                price_move=f"{old_yes_price:.4f}->{new_price:.4f}" if old_yes_price else f"?->{new_price:.4f}",
                edge=round(abs(edge), 4),
            )
            async with self._trade_lock:
                _ws_success = await self._execute_esports_trade(opp)
            if _ws_success:
                self._market_entry_times.setdefault(market_id, []).append(time.monotonic())
                await self._save_entry_count_to_redis(market_id)
        except Exception as exc:
            logger.debug("esports_series_ws_failed", error=str(exc))
        finally:
            self._ws_pending_trades.discard(market_id)
