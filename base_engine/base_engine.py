import asyncio
import time
from typing import Optional, Dict, Any, List
from structlog import get_logger
from base_engine.data.polymarket_client import PolymarketClient
from base_engine.data.database import Database
from base_engine.data.redis_cache import RedisCache
from base_engine.data.data_ingestion import DataIngestionService
from base_engine.data.ingestion_scheduler import IngestionScheduler
from base_engine.data.unified_market_service import UnifiedMarketService
from base_engine.backtesting.backtest_engine import BacktestEngine
from base_engine.learning.learning_engine import LearningEngine
from base_engine.learning.simulation_engine import SimulationEngine
from base_engine.prediction.prediction_engine import PredictionEngine
from base_engine.risk.risk_manager import RiskManager
from base_engine.execution.execution_engine import ExecutionEngine
from config.settings import settings

# New monitoring systems
from base_engine.monitoring.health_monitor import HealthMonitor
from base_engine.monitoring.alerting import AlertingSystem
from base_engine.monitoring.metrics_dashboard import MetricsDashboard
from base_engine.monitoring.data_quality import DataQualityMonitor
from base_engine.monitoring.recovery import RecoveryProcedure
from base_engine.monitoring.backup_recovery import BackupManager

# New analysis systems
from base_engine.analysis.market_regime import MarketRegimeDetector
from base_engine.analysis.multi_timeframe import MultiTimeframeAnalyzer
from base_engine.analysis.correlation_strategies import CorrelationStrategy
from base_engine.analysis.order_flow import OrderFlowAnalyzer

# New portfolio systems
from base_engine.portfolio.portfolio_rebalancer import PortfolioRebalancer

# New risk systems
from base_engine.risk.dynamic_position_sizing import DynamicPositionSizing

# New execution systems
from base_engine.execution.advanced_orders import AdvancedOrderManager
from base_engine.execution.position_manager import AutomatedPositionManager

# New signal and data systems
from base_engine.signals.signal_ingestion import SignalIngestionService
from base_engine.signals.whale_tracker import WhaleTracker
from base_engine.learning.performance_tracker import PerformanceTracker
from base_engine.data.websocket_manager import WebSocketManager

# New risk and analysis systems
from base_engine.risk.drawdown_controller import DrawdownController
from base_engine.data.orderbook_tracker import OrderBookTracker
from base_engine.data.trade_flow_analyzer import TradeFlowAnalyzer

# New roadmap implementations
from base_engine.analysis.resolution_risk import ResolutionRiskAnalyzer
from base_engine.execution.smart_order_router import SmartOrderRouter
from base_engine.risk.liquidity_guardian import LiquidityGuardian
from base_engine.execution.order_management_system import OrderManagementSystem
from base_engine.signals.copy_trading_engine import CopyTradingEngine
from base_engine.learning.wallet_clustering import WalletClustering
from base_engine.analysis.performance_attribution import PerformanceAttribution
from base_engine.analysis.trade_journal import TradeJournal
from base_engine.monitoring.report_generator import ReportGenerator
from base_engine.data.market_metadata_enricher import MarketMetadataEnricher
from base_engine.signals.google_trends import GoogleTrendsClient
from base_engine.signals.signal_effectiveness import SignalEffectivenessTracker

# Lower priority implementations
from base_engine.data.historical_data_warehouse import HistoricalDataWarehouse
from base_engine.execution.paper_trading import PaperTradingEngine
from base_engine.learning.ab_testing import ABTestingFramework
from base_engine.data.mempool_monitor import MempoolMonitor

# Coordination and lifecycle (mandatory for multi-bot)
from base_engine.coordination import KillSwitch, TradeCoordinator
from base_engine.learning.scheduler import LearningScheduler
from base_engine.learning.elite_detector import EliteUserDetector
from base_engine.learning.calibration_tracker import CalibrationTracker
from base_engine.learning.incremental_learning import IncrementalLearner
from base_engine.learning.meta_learning import MetaLearner
from base_engine.learning.causal_inference import CausalInferenceEngine
from base_engine.learning.feature_store import FeatureStore
from base_engine.monitoring.error_tracking import ErrorTracker, init_error_tracking
from base_engine.learning.model_versioning import ModelVersionManager
from base_engine.sentiment.sentiment_analyzer import SentimentAnalyzer
from base_engine.monitoring.metrics_collector import metrics_collector as _metrics_singleton
from base_engine.monitoring.distributed_tracing import DistributedTracer, get_tracer
from base_engine.monitoring.quality_metrics import QualityMetrics
from base_engine.monitoring.snapshot import SnapshotManager
from base_engine.monitoring.pipeline_gate import PipelineGate
from base_engine.lifecycle import LifecycleManager
from base_engine.execution.order_gateway import OrderGateway
from base_engine.execution.rl_trade_timing import RLTradeTimingAgent

# Elevation Plan additions
from base_engine.coordination.multi_kill_switch import MultiLayerKillSwitch
from base_engine.risk.correlation_risk import CorrelationRiskManager
from base_engine.config.tunable_config import TunableConfigStore
from base_engine.learning.config_tuner import ConfigTuner
from base_engine.features.market_impact import MarketImpactEstimator

# Elite Model Deep Dive — new signal sources + analysis
from base_engine.signals.legislative_tracker import LegislativeTracker
from base_engine.signals.polling_client import PollingClient
from base_engine.signals.court_monitor import CourtMonitor
from base_engine.signals.intl_elections import InternationalElectionsClient
from base_engine.analysis.logical_arbitrage import LogicalArbitrageDetector
from base_engine.analysis.bayesian_model import BayesianPollingModel
from base_engine.features.spread_decomposition import SpreadDecomposer
from base_engine.features.counterparty_classifier import CounterpartyClassifier
from base_engine.portfolio.reconciliation import PositionReconciler
from base_engine.data.tax_logger import TaxLogger
from base_engine.data.oracle_monitor import OracleMonitor
from base_engine.data.kalshi_client import KalshiClient
from base_engine.config.secret_manager import SecretManager

# Self-healing architecture (2026-02-23)
from base_engine.monitoring.bot_state_machine import BotStateMachine
from base_engine.monitoring.streaming_anomaly import StreamingAnomalyDetector
from base_engine.monitoring.log_miner import LogTemplateMiner
from base_engine.monitoring.portfolio_drawdown import PortfolioDrawdownBreaker
from base_engine.monitoring.degradation_manager import DegradationManager
from base_engine.monitoring.health_scheduler import HealthScheduler

# 2026 Alpha Roadmap additions
from base_engine.exchanges.arb_scanner import ArbScanner
from base_engine.exchanges.capital_tracker import CapitalTracker
from base_engine.features.calibration import DomainCalibrator
from base_engine.features.agentic_rag import AgenticRAG
from base_engine.data.sports_client import SportsClient
from base_engine.data.contract_change_monitor import ContractChangeMonitor
from base_engine.monitoring.airdrop_tracker import AirdropTracker
from base_engine.monitoring.regulatory_monitor import RegulatoryMonitor
from base_engine.analysis.wash_trading_detector import WashTradingDetector

logger = get_logger()


class BaseEngine:
    def __init__(self):
        self.client: Optional[PolymarketClient] = None
        self.db: Optional[Database] = None
        self.cache: Optional[RedisCache] = None
        self.data_ingestion: Optional[DataIngestionService] = None
        self.unified_market_service: Optional[UnifiedMarketService] = None
        self.backtest_engine: Optional[BacktestEngine] = None
        self.learning_engine: Optional[LearningEngine] = None
        self.simulation_engine: Optional[SimulationEngine] = None
        self.prediction_engine: Optional[PredictionEngine] = None
        self.risk_manager: Optional[RiskManager] = None
        self.execution_engine: Optional[ExecutionEngine] = None
        self.kill_switch: Optional[KillSwitch] = None
        self.trade_coordinator: Optional[TradeCoordinator] = None
        self.order_gateway: Optional[OrderGateway] = None
        self.elite_detector: Optional[EliteUserDetector] = None
        self.ingestion_scheduler: Optional[IngestionScheduler] = None
        self.scheduler: Optional[LearningScheduler] = None
        self.incremental_learner: Optional[IncrementalLearner] = None
        self.lifecycle_manager: Optional[LifecycleManager] = None
        self.calibration_tracker: Optional[CalibrationTracker] = None

        # New monitoring systems
        self.health_monitor: Optional[HealthMonitor] = None
        self.alerting_system: Optional[AlertingSystem] = None
        self.metrics_dashboard: Optional[MetricsDashboard] = None
        self.data_quality_monitor: Optional[DataQualityMonitor] = None
        self.recovery_procedure: Optional[RecoveryProcedure] = None
        self.backup_manager: Optional[BackupManager] = None
        
        # New analysis systems
        self.market_regime_detector: Optional[MarketRegimeDetector] = None
        self.multi_timeframe_analyzer: Optional[MultiTimeframeAnalyzer] = None
        self.correlation_strategy: Optional[CorrelationStrategy] = None
        self.order_flow_analyzer: Optional[OrderFlowAnalyzer] = None
        
        # New portfolio systems
        self.portfolio_rebalancer: Optional[PortfolioRebalancer] = None
        
        # New risk systems
        self.dynamic_position_sizing: Optional[DynamicPositionSizing] = None
        
        # New execution systems
        self.advanced_order_manager: Optional[AdvancedOrderManager] = None
        self.position_manager: Optional[AutomatedPositionManager] = None
        
        # New signal and data systems
        self.signal_ingestion: Optional[SignalIngestionService] = None
        self.signal_effectiveness: Optional[SignalEffectivenessTracker] = None
        self.whale_tracker: Optional[WhaleTracker] = None
        self.performance_tracker: Optional[PerformanceTracker] = None
        self.websocket_manager: Optional[WebSocketManager] = None
        
        # New risk and analysis systems
        self.drawdown_controller: Optional[DrawdownController] = None
        self.orderbook_tracker: Optional[OrderBookTracker] = None
        self.trade_flow_analyzer: Optional[TradeFlowAnalyzer] = None
        
        # New roadmap implementations
        self.resolution_risk_analyzer: Optional[ResolutionRiskAnalyzer] = None
        self.smart_order_router: Optional[SmartOrderRouter] = None
        self.liquidity_guardian: Optional[LiquidityGuardian] = None
        self.order_management_system: Optional[OrderManagementSystem] = None
        self.copy_trading_engine: Optional[CopyTradingEngine] = None
        self.wallet_clustering: Optional[WalletClustering] = None
        self.performance_attribution: Optional[PerformanceAttribution] = None
        self.trade_journal: Optional[TradeJournal] = None
        self.report_generator: Optional[ReportGenerator] = None
        self.market_metadata_enricher: Optional[MarketMetadataEnricher] = None
        self.google_trends: Optional[GoogleTrendsClient] = None
        
        # Lower priority implementations
        self.historical_data_warehouse: Optional[HistoricalDataWarehouse] = None
        self.paper_trading: Optional[PaperTradingEngine] = None
        self.ab_testing: Optional[ABTestingFramework] = None
        self.mempool_monitor: Optional[MempoolMonitor] = None
        self.streaming_persister: Optional[Any] = None
        # Game-changer additions (#24, #25, #29, #33, #34, #35, #38, #39, #42, #44, #46, #47, #48)
        self.webhook_dispatcher: Optional[Any] = None
        self.arbitrage_detector: Optional[Any] = None
        self.resolution_listener: Optional[Any] = None
        self.anomaly_detector: Optional[Any] = None
        self.event_bus: Optional[Any] = None
        self.lineage_tracker: Optional[Any] = None
        self.portfolio_optimizer: Optional[Any] = None
        self.risk_analytics: Optional[Any] = None
        self.compliance_reporter: Optional[Any] = None
        self.data_quality_sla: Optional[Any] = None
        self.market_clustering: Optional[Any] = None
        self.replay_engine: Optional[Any] = None

        # Dead-module wiring: newly activated subsystems
        self.feature_store: Optional[FeatureStore] = None
        self.error_tracker: Optional[ErrorTracker] = None
        self.model_version_manager: Optional[ModelVersionManager] = None
        self.sentiment_analyzer: Optional[SentimentAnalyzer] = None
        self.metrics_collector: Optional[Any] = None
        self.distributed_tracer: Optional[Any] = None
        self.quality_metrics: Optional[Any] = None
        self.snapshot_manager: Optional[Any] = None
        self.pipeline_gate: Optional[Any] = None

        # Elevation Plan components
        self.multi_kill_switch: Optional[MultiLayerKillSwitch] = None
        self.correlation_risk: Optional[CorrelationRiskManager] = None
        self.tunable_config: Optional[TunableConfigStore] = None
        self.config_tuner: Optional[ConfigTuner] = None
        self.market_impact: Optional[MarketImpactEstimator] = None

        # Elite Model Deep Dive — new signal sources + analysis
        self.legislative_tracker: Optional[LegislativeTracker] = None
        self.polling_client: Optional[PollingClient] = None
        self.court_monitor: Optional[CourtMonitor] = None
        self.intl_elections: Optional[InternationalElectionsClient] = None
        self.logical_arbitrage: Optional[LogicalArbitrageDetector] = None
        self.bayesian_model: Optional[BayesianPollingModel] = None
        self.spread_decomposer: Optional[SpreadDecomposer] = None
        self.counterparty_classifier: Optional[CounterpartyClassifier] = None
        self.position_reconciler: Optional[PositionReconciler] = None
        self.tax_logger: Optional[TaxLogger] = None
        self.oracle_monitor: Optional[OracleMonitor] = None
        self.kalshi_client: Optional[KalshiClient] = None
        self.secret_manager: Optional[SecretManager] = None

        # 2026 Alpha Roadmap: infrastructure modules + exchange adapters
        self._exchange_adapters: List[Any] = []
        self._arb_scanner: Optional[ArbScanner] = None
        # I52: removed 7 dead adapter fields (_capital_tracker, _contract_change_monitor,
        # _airdrop_tracker, _regulatory_monitor, _wash_trading_detector, _domain_calibrator,
        # _agentic_rag) — set in init() but never read by any external caller.
        self._sports_client: Optional[SportsClient] = None

        # RL Trade Timing Agent (learns WHEN to trade from paper trade outcomes)
        self.rl_agent: Optional[RLTradeTimingAgent] = None

        # Self-healing architecture (2026-02-23)
        self.streaming_anomaly_detector: Optional[StreamingAnomalyDetector] = None
        self.log_template_miner: Optional[LogTemplateMiner] = None
        self.portfolio_drawdown_breaker: Optional[PortfolioDrawdownBreaker] = None
        self.degradation_manager: Optional[DegradationManager] = None
        self.health_scheduler: Optional[HealthScheduler] = None

        # In-memory market index for millisecond-latency reactive trading
        # Populated by scan loops, consumed by on_price_update() handlers
        self._market_index: Dict[str, Dict[str, Any]] = {}
        self._market_index_by_cid: Dict[str, Dict[str, Any]] = {}  # keyed by condition_id (0x hash from WS)
        self._market_index_populated: bool = False

        # B5: Cross-bot feature performance sharing — all bots write {feature: lift} here.
        # EnsembleBot reads this to boost features other bots found informative.
        # dict[feature_name] = list of (timestamp_float, lift_float) tuples (rolling 2h).
        self._shared_feature_stats: Dict[str, Any] = {}

        self.running = False

    # ── In-memory market index for ms-latency reactive trading ────────

    def update_market_index(self, markets: List[Dict[str, Any]]) -> None:
        """Called by scan loops after fetching markets to keep the index fresh."""
        for m in markets:
            mid = m.get("id")
            cid = m.get("condition_id") or ""
            if mid is not None:
                self._market_index[str(mid)] = m
            if cid:
                self._market_index_by_cid[cid] = m  # index by condition_id for WS lookups
        if markets:
            self._market_index_populated = True

    def get_market_from_index(self, market_id: str) -> Optional[Dict[str, Any]]:
        """O(1) dict lookup — used by reactive on_price_update() instead of REST calls.
        Checks numeric id first, then condition_id (0x hash sent by Polymarket WebSocket).
        """
        return (
            self._market_index.get(str(market_id))
            or self._market_index_by_cid.get(str(market_id))
        )

    async def init(
        self, 
        wallet_private_key: Optional[str] = None, 
        wallet_address: Optional[str] = None
    ) -> None:
        """
        Initialize BaseEngine with all components.
        
        Initialization Order (CRITICAL - dependencies must be initialized in this order):
        
        Level 1 - Core Infrastructure (no dependencies):
        1. PolymarketClient - API client
        2. Database - Data persistence
        3. RedisCache - Caching layer
        
        Level 2 - Data Services (depend on Level 1):
        4. DataIngestionService - Requires client, db
        5. UnifiedMarketService - Requires client, cache, db, optional thegraph
        
        Level 3 - Core Engines (depend on Level 1-2):
        6. BacktestEngine - Requires db
        7. LearningEngine - Requires db
        8. SimulationEngine - Requires db, learning_engine
        9. PredictionEngine - Requires db, learning_engine
        10. RiskManager - Requires db
        11. ExecutionEngine - Requires client, risk_manager, db
        
        Level 4 - Monitoring Systems (depend on Level 1-3):
        12. HealthMonitor - Requires db, cache, client
        13. AlertingSystem - Requires health_monitor
        14. MetricsDashboard - Requires health_monitor
        15. DataQualityMonitor - Requires db
        16. RecoveryProcedure - Requires health_monitor, db, cache
        17. BackupManager - Requires db
        
        Level 5 - Analysis Systems (depend on Level 1-3):
        18. MarketRegimeDetector - Requires db
        19. MultiTimeframeAnalyzer - Requires db
        20. CorrelationStrategy - Requires db
        21. OrderFlowAnalyzer - Requires db, client
        
        Level 6 - Portfolio & Risk Systems (depend on Level 3):
        22. PortfolioRebalancer - Requires db, risk_manager
        23. DynamicPositionSizing - No dependencies
        
        Level 7 - Execution Systems (depend on Level 3):
        24. AdvancedOrderManager - Requires execution_engine, client
        25. AutomatedPositionManager - Requires execution_engine, advanced_order_manager, db
        
        Level 8 - Signal & Data Systems (depend on Level 1-2):
        26. SignalIngestionService - Requires db, cache, client
        27. WhaleTracker - Requires client, db, cache
        28. PerformanceTracker - Requires db, cache
        29. WebSocketManager - Requires cache
        
        Level 9 - Risk & Analysis Systems (depend on Level 1-2):
        30. DrawdownController - No dependencies (config only)
        31. OrderBookTracker - Requires client, cache
        32. TradeFlowAnalyzer - Requires db, client
        
        Level 10 - Roadmap Implementations (depend on Level 1-9):
        33. ResolutionRiskAnalyzer - Requires db
        34. SmartOrderRouter - Requires client, execution_engine, orderbook_tracker
        35. LiquidityGuardian - Requires client, orderbook_tracker
        36. OrderManagementSystem - Requires execution_engine, client
        37. CopyTradingEngine - Requires execution_engine, whale_tracker
        38. WalletClustering - Requires db
        39. PerformanceAttribution - Requires db
        40. TradeJournal - Requires db
        41. ReportGenerator - Requires db
        42. MarketMetadataEnricher - Requires db
        43. GoogleTrendsClient - Optional API key
        
        Level 11 - Lower Priority (depend on Level 1):
        44. HistoricalDataWarehouse - Requires db
        45. PaperTradingEngine - No dependencies
        46. ABTestingFramework - Requires db
        47. MempoolMonitor - Optional (requires blockchain_client)
        """
        try:
            logger.info("Initializing base engine")
        except Exception:
            print("Warning: Logger not configured, continuing without logging")
        
        pk = (wallet_private_key or "").strip() or None
        addr = (wallet_address or "").strip() or None
        
        # Level 1: Core Infrastructure
        try:
            self.client = PolymarketClient(private_key=pk, wallet_address=addr)
        except Exception as e:
            logger.error("Failed to create PolymarketClient: %s", ascii(str(e)))
            raise RuntimeError(f"Cannot initialize without PolymarketClient: {str(e)}") from e
        
        self.db = Database()
        self.cache = RedisCache()
        
        try:
            await self.db.init()
        except Exception as e:
            logger.warning("Database initialization failed (non-fatal): %s", ascii(str(e)))
        
        try:
            await self.cache.init()
        except Exception as e:
            logger.warning("Redis initialization failed (non-fatal): %s", ascii(str(e)))
        
        # Level 2: Data Services
        try:
            from base_engine.data.smart_fetcher import SmartDataFetcher
            self.smart_fetcher = SmartDataFetcher(self.db) if self.db and self.db.session_factory else None
            self.data_ingestion = DataIngestionService(
                self.client,
                self.db,
                smart_fetcher=self.smart_fetcher,
            )
            
            # Initialize unified market service (aggregates API/blockchain/cache)
            from base_engine.data.thegraph_client import TheGraphClient
            thegraph_client = TheGraphClient() if getattr(settings, 'USE_THEGRAPH_QUERIES', False) else None
            self.unified_market_service = UnifiedMarketService(
                client=self.client,
                thegraph_client=thegraph_client,
                cache=self.cache,
                db=self.db
            )
            
            # Level 3: Core Engines
            self.backtest_engine = BacktestEngine(self.db)
            self.learning_engine = LearningEngine(self.db)
            self.simulation_engine = SimulationEngine(self.db, self.learning_engine)
            self.prediction_engine = PredictionEngine(self.db, self.learning_engine)
            # B4: Pass Redis cache to prediction engine for shared prediction caching
            if self.cache:
                self.prediction_engine._redis_cache = self.cache
            self.risk_manager = RiskManager(self.db)
            self.calibration_tracker = CalibrationTracker(db=self.db)
            # FeatureStore: pre-compute ML features for backtesting/training
            if getattr(settings, "USE_FEATURE_STORE", True) and self.db:
                try:
                    self.feature_store = FeatureStore(db=self.db)
                except Exception as e:
                    logger.debug("FeatureStore init failed (non-critical): %s", e)
            self.execution_engine = ExecutionEngine(self.client, self.risk_manager, self.db, private_key=pk)
            
            # Level 4: Monitoring Systems
            self.health_monitor = HealthMonitor(db=self.db, cache=self.cache, client=self.client)
            self.alerting_system = AlertingSystem(health_monitor=self.health_monitor)
            self.risk_manager.alerting = self.alerting_system
            self.metrics_dashboard = MetricsDashboard(health_monitor=self.health_monitor)
            self.data_quality_monitor = DataQualityMonitor(db=self.db)
            self.recovery_procedure = RecoveryProcedure(health_monitor=self.health_monitor, db=self.db, cache=self.cache)
            self.backup_manager = BackupManager(db=self.db)
            from base_engine.monitoring.gap_detector import GapDetector
            from base_engine.monitoring.auto_healer import AutoHealer
            self.gap_detector = GapDetector(self.db) if self.db else None
            self.auto_healer = (
                AutoHealer(self.db, self.gap_detector, self.data_ingestion)
                if self.db and self.gap_detector and self.data_ingestion
                else None
            )
            # ErrorTracker: structured error logging + optional Sentry
            if getattr(settings, "USE_ERROR_TRACKER", True):
                try:
                    sentry_dsn = getattr(settings, "SENTRY_DSN", None)
                    self.error_tracker = init_error_tracking(sentry_dsn=sentry_dsn)
                except Exception as e:
                    logger.debug("ErrorTracker init failed (non-critical): %s", e)
            # MetricsCollector (Prometheus): trade/prediction/cache metrics
            if getattr(settings, "USE_METRICS_COLLECTOR", True):
                try:
                    self.metrics_collector = _metrics_singleton
                except Exception as e:
                    logger.debug("MetricsCollector init failed (non-critical): %s", e)
            # DistributedTracer: request tracing across services
            if getattr(settings, "USE_DISTRIBUTED_TRACING", True):
                try:
                    self.distributed_tracer = get_tracer()
                except Exception as e:
                    logger.debug("DistributedTracer init failed (non-critical): %s", e)
            # QualityMetrics: market data quality scoring (A-F grades)
            if getattr(settings, "USE_QUALITY_METRICS", True) and self.db:
                try:
                    self.quality_metrics = QualityMetrics(db=self.db)
                except Exception as e:
                    logger.debug("QualityMetrics init failed (non-critical): %s", e)
            # SnapshotManager: pre/post operation verification
            if getattr(settings, "USE_SNAPSHOT_MANAGER", True) and self.db:
                try:
                    self.snapshot_manager = SnapshotManager(db=self.db)
                except Exception as e:
                    logger.debug("SnapshotManager init failed (non-critical): %s", e)
            # PipelineGate: post-condition checker between pipeline stages
            if getattr(settings, "USE_PIPELINE_GATE", True) and self.db:
                try:
                    self.pipeline_gate = PipelineGate(
                        db=self.db,
                        alerting=self.alerting_system,
                    )
                except Exception as e:
                    logger.debug("PipelineGate init failed (non-critical): %s", e)

            # Level 5: Analysis Systems
            self.market_regime_detector = MarketRegimeDetector(db=self.db)
            self.multi_timeframe_analyzer = MultiTimeframeAnalyzer(db=self.db)
            self.correlation_strategy = CorrelationStrategy(db=self.db)
            self.order_flow_analyzer = OrderFlowAnalyzer(db=self.db, client=self.client)
            
            # Level 6: Portfolio & Risk Systems
            self.portfolio_rebalancer = PortfolioRebalancer(
                db=self.db,
                risk_manager=self.risk_manager
            )
            self.dynamic_position_sizing = DynamicPositionSizing()
            
            # Level 7: Execution Systems
            self.advanced_order_manager = AdvancedOrderManager(
                execution_engine=self.execution_engine,
                client=self.client
            )
            self.position_manager = AutomatedPositionManager(
                execution_engine=self.execution_engine,
                order_manager=self.advanced_order_manager,
                db=self.db,
                prediction_engine=getattr(self, "prediction_engine", None),
            )
            
            # Level 8: Signal & Data Systems
            self.signal_ingestion = SignalIngestionService(
                db=self.db,
                cache=self.cache,
                client=self.client
            )

            # R3: Signal effectiveness tracker — dynamic multipliers from outcome_correct
            self.signal_effectiveness = SignalEffectivenessTracker(db=self.db)
            
            self.whale_tracker = WhaleTracker(
                client=self.client,
                db=self.db,
                cache=self.cache
            )
            
            self.performance_tracker = PerformanceTracker(
                db=self.db,
                cache=self.cache
            )
            # SentimentAnalyzer: centralized volume/orderbook/divergence sentiment
            if getattr(settings, "USE_SENTIMENT_ANALYZER", True):
                try:
                    self.sentiment_analyzer = SentimentAnalyzer()
                except Exception as e:
                    logger.debug("SentimentAnalyzer init failed (non-critical): %s", e)

            # EventBus: create early so WebSocketManager and other components can use it
            # Use EventSourcingBus when DB is available for append-only audit trail
            if self.event_bus is None:
                try:
                    from base_engine.coordination.event_bus import EventBus, EventSourcingBus
                    if self.db and self.db.session_factory:
                        self.event_bus = EventSourcingBus(db=self.db)
                        logger.info("EventSourcingBus initialized (persistent decision log)")
                    else:
                        self.event_bus = EventBus()
                except Exception as e:
                    logger.debug("EventBus early init failed (non-critical): %s", e)

            # Use WebSocket URL from client (which gets it from settings)
            ws_url = getattr(self.client, 'ws_url', 'wss://ws-subscriptions-clob.polymarket.com/ws/market')
            self.websocket_manager = WebSocketManager(
                cache=self.cache,
                ws_url=ws_url,
                event_bus=self.event_bus,
                # I49: pass market index resolver so WS condition_id → numeric id is resolved
                # at the source before price_update events are emitted to bots
                market_index_resolver=self.get_market_from_index,
            )
            # Real-time streaming to DB (trade/price events from WebSocket)
            if self.db and self.db.session_factory:
                try:
                    from base_engine.data.streaming_persister import StreamingPersister
                    self.streaming_persister = StreamingPersister(self.db)
                    self.streaming_persister.register_with_websocket(self.websocket_manager)
                    # Phase 5: Wire whale fast-path hook (StreamingPersister → WhaleTracker <1ms)
                    if hasattr(self, "whale_tracker") and self.streaming_persister is not None:
                        self.streaming_persister._whale_callback = self.whale_tracker.handle_streaming_trade
                        self.streaming_persister._whale_threshold_usd = self.whale_tracker.min_whale_size_usd
                        logger.info("Whale fast-path wired: streaming_persister → whale_tracker")
                except Exception as e:
                    logger.warning("StreamingPersister init failed (non-critical): %s", e)
                    self.streaming_persister = None

            # Phase 7: User/order WebSocket (order_filled, order_update -> EventBus)
            self.user_order_websocket: Optional[Any] = None
            if getattr(settings, "USER_ORDER_WS_ENABLED", False) and self.event_bus and getattr(settings, "CLOB_API_KEY", None) and getattr(settings, "CLOB_SECRET", None):
                try:
                    from base_engine.data.user_order_websocket import UserOrderWebSocket
                    ws_base = ws_url.replace("/ws/market", "").rstrip("/") or "wss://ws-subscriptions-clob.polymarket.com"
                    auth = {
                        "apiKey": (getattr(settings, "CLOB_API_KEY") or "").strip(),
                        "secret": (getattr(settings, "CLOB_SECRET") or "").strip(),
                        "passphrase": (getattr(settings, "CLOB_PASSPHRASE") or "").strip(),
                    }
                    self.user_order_websocket = UserOrderWebSocket(
                        ws_url_base=ws_base,
                        event_bus=self.event_bus,
                        auth=auth,
                    )
                except Exception as e:
                    logger.debug("UserOrderWebSocket init failed (non-critical): %s", e)
                    self.user_order_websocket = None

            # Level 9: Risk & Analysis Systems
            self.drawdown_controller = DrawdownController(
                config={
                    "max_daily_loss": 0.05,  # 5%
                    "max_weekly_loss": 0.15,  # 15%
                    "cooldown_hours": 24,
                    "starting_capital": 10000.0  # Default, should be set from portfolio
                }
            )
            
            self.orderbook_tracker = OrderBookTracker(
                client=self.client,
                cache=self.cache
            )
            
            self.trade_flow_analyzer = TradeFlowAnalyzer(
                db=self.db,
                client=self.client
            )
            
            # Level 10: Roadmap Implementations
            self.resolution_risk_analyzer = ResolutionRiskAnalyzer(db=self.db)
            # Tier 2 #16: Wire rra into prediction_engine so _extract_features() can read cached scores
            if hasattr(self, "prediction_engine"):
                self.prediction_engine._resolution_risk_analyzer = self.resolution_risk_analyzer
            
            self.smart_order_router = SmartOrderRouter(
                client=self.client,
                execution_engine=self.execution_engine,
                orderbook_tracker=self.orderbook_tracker  # Pass OrderBookTracker for order book access
            )
            
            self.liquidity_guardian = LiquidityGuardian(
                client=self.client,
                orderbook_tracker=self.orderbook_tracker
            )
            
            self.order_management_system = OrderManagementSystem(
                execution_engine=self.execution_engine,
                client=self.client
            )
            
            self.copy_trading_engine = CopyTradingEngine(
                execution_engine=self.execution_engine,
                whale_tracker=self.whale_tracker
            )
            
            self.wallet_clustering = WalletClustering(db=self.db)
            
            self.performance_attribution = PerformanceAttribution(db=self.db)
            
            self.trade_journal = TradeJournal(db=self.db)
            
            self.report_generator = ReportGenerator(
                db=self.db,
                config={
                    "email_enabled": getattr(settings, 'EMAIL_REPORTS_ENABLED', False),
                    "slack_enabled": getattr(settings, 'SLACK_REPORTS_ENABLED', False),
                    "email_recipients": getattr(settings, 'EMAIL_RECIPIENTS', []),
                    "slack_webhook": getattr(settings, 'SLACK_WEBHOOK', None)
                }
            )
            
            self.market_metadata_enricher = MarketMetadataEnricher(db=self.db)
            
            self.google_trends = GoogleTrendsClient(
                api_key=getattr(settings, 'GOOGLE_TRENDS_API_KEY', None)
            )
            
            # Level 11: Lower Priority Implementations
            self.historical_data_warehouse = HistoricalDataWarehouse(db=self.db)
            
            self.paper_trading = PaperTradingEngine(
                initial_capital=getattr(settings, 'PAPER_TRADING_CAPITAL', 10000.0),
                db=self.db,
            )
            # K7 FIX: Wire PerformanceTracker into PaperTradingEngine for outcome tracking
            if hasattr(self, 'performance_tracker') and self.performance_tracker:
                self.paper_trading._performance_tracker = self.performance_tracker
            
            self.ab_testing = ABTestingFramework(db=self.db)
            # ModelVersionManager: in-memory model versioning and A/B testing
            if getattr(settings, "USE_MODEL_VERSIONING", True):
                try:
                    self.model_version_manager = ModelVersionManager()
                except Exception as e:
                    logger.debug("ModelVersionManager init failed (non-critical): %s", e)

            # Optional: Mempool Monitor (if blockchain available)
            try:
                from base_engine.data.blockchain_client import BlockchainClient
                blockchain_client = BlockchainClient()
                self.mempool_monitor = MempoolMonitor(blockchain_client=blockchain_client)
            except Exception as e:
                logger.debug(f"Mempool monitor not available (blockchain client not initialized): {str(e)}")
                self.mempool_monitor = None

            # Webhooks (#39), Arbitrage (#24), Resolution listener (#33), Anomaly (#35)
            try:
                from base_engine.data.webhook_dispatcher import WebhookDispatcher
                from base_engine.integrations.arbitrage_detector import ArbitrageDetector
                from base_engine.data.resolution_listener import ResolutionListener
                from base_engine.monitoring.anomaly_detector import AnomalyDetector
                self.webhook_dispatcher = WebhookDispatcher(db=self.db)
                self.arbitrage_detector = ArbitrageDetector(db=self.db) if self.db else None
                self.resolution_listener = ResolutionListener(db=self.db) if self.db else None
                self.anomaly_detector = AnomalyDetector(db=self.db) if self.db else None
            except Exception as e:
                logger.debug("Webhook/arbitrage/resolution/anomaly init failed (non-critical): %s", e)
                self.webhook_dispatcher = WebhookDispatcher(db=None)
                self.arbitrage_detector = None
                self.resolution_listener = None
                self.anomaly_detector = None

            # Lineage (#38), Portfolio optimizer (#42), Risk analytics (#46),
            # Compliance (#47), Data quality SLA (#48), Market clustering (#34), Replay (#44)
            try:
                from base_engine.data.lineage_tracker import LineageTracker
                from base_engine.portfolio.portfolio_optimizer import PortfolioOptimizer
                from base_engine.risk.risk_analytics import RiskAnalytics
                from base_engine.monitoring.compliance_reporter import ComplianceReporter
                from base_engine.monitoring.data_quality_sla import DataQualitySLA
                from base_engine.analysis.market_clustering import MarketClustering
                from base_engine.backtesting.replay_engine import ReplayEngine
                self.lineage_tracker = LineageTracker(db=self.db) if self.db else None
                self.portfolio_optimizer = PortfolioOptimizer()
                self.risk_analytics = RiskAnalytics()
                self.compliance_reporter = ComplianceReporter(db=self.db) if self.db else None
                self.data_quality_sla = DataQualitySLA(db=self.db) if self.db else None
                self.market_clustering = MarketClustering(db=self.db) if self.db else None
                self.replay_engine = ReplayEngine(db=self.db) if self.db else None
            except Exception as e:
                logger.debug("Lineage/optimizer/risk/compliance/SLA/clustering/replay init failed (non-critical): %s", e)
                self.lineage_tracker = None
                self.portfolio_optimizer = PortfolioOptimizer()
                self.risk_analytics = RiskAnalytics()
                self.compliance_reporter = None
                self.data_quality_sla = None
                self.market_clustering = None
                self.replay_engine = None

            # ---- Elevation Plan: Initialize all new components (individually wrapped) ----
            _elevation_ok = 0
            for _name, _init_fn in [
                ("CorrelationRisk", lambda: CorrelationRiskManager(db=self.db)),
                ("MarketImpact", lambda: MarketImpactEstimator(db=self.db)),
                ("SpreadDecomposer", lambda: SpreadDecomposer(db=self.db)),
                ("CounterpartyClassifier", lambda: CounterpartyClassifier(db=self.db)),
                ("PositionReconciler", lambda: PositionReconciler(db=self.db)),
                ("TaxLogger", lambda: TaxLogger(db=self.db)),
                ("OracleMonitor", lambda: OracleMonitor(db=self.db)),
                ("KalshiClient", lambda: KalshiClient()),
                ("SecretManager", lambda: SecretManager()),
                ("TunableConfigStore", lambda: TunableConfigStore(db=self.db)),
            ]:
                try:
                    obj = _init_fn()
                    attr = _name[0].lower() + _name[1:]
                    # Map class names to attribute names
                    _attr_map = {
                        "correlationRisk": "correlation_risk",
                        "marketImpact": "market_impact",
                        "spreadDecomposer": "spread_decomposer",
                        "counterpartyClassifier": "counterparty_classifier",
                        "positionReconciler": "position_reconciler",
                        "taxLogger": "tax_logger",
                        "oracleMonitor": "oracle_monitor",
                        "kalshiClient": "kalshi_client",
                        "secretManager": "secret_manager",
                        "tunableConfigStore": "tunable_config",
                    }
                    setattr(self, _attr_map.get(attr, attr), obj)
                    _elevation_ok += 1
                except Exception as e:
                    logger.debug("Elevation component %s init failed (non-critical): %s", _name, e)
            # Config tuner depends on tunable_config
            try:
                self.config_tuner = ConfigTuner(db=self.db, config_store=self.tunable_config)
                _elevation_ok += 1
            except Exception as e:
                logger.debug("ConfigTuner init failed (non-critical): %s", e)
            logger.info("Elevation Plan: %d/11 components initialized", _elevation_ok)

            # ---- Elite Model Deep Dive: New signal sources + analysis ----
            _elite_ok = 0
            for _name, _init_fn in [
                ("LegislativeTracker", lambda: LegislativeTracker()),
                ("PollingClient", lambda: PollingClient()),
                ("CourtMonitor", lambda: CourtMonitor()),
                ("InternationalElectionsClient", lambda: InternationalElectionsClient()),
                ("LogicalArbitrageDetector", lambda: LogicalArbitrageDetector(db=self.db)),
                ("BayesianPollingModel", lambda: BayesianPollingModel()),
            ]:
                try:
                    obj = _init_fn()
                    _elite_attr_map = {
                        "LegislativeTracker": "legislative_tracker",
                        "PollingClient": "polling_client",
                        "CourtMonitor": "court_monitor",
                        "InternationalElectionsClient": "intl_elections",
                        "LogicalArbitrageDetector": "logical_arbitrage",
                        "BayesianPollingModel": "bayesian_model",
                    }
                    setattr(self, _elite_attr_map[_name], obj)
                    _elite_ok += 1
                except Exception as e:
                    logger.debug("Elite component %s init failed (non-critical): %s", _name, e)

            # Configure Bayesian fundamentals from settings
            if self.bayesian_model is not None:
                try:
                    self.bayesian_model.set_fundamentals(
                        gdp_growth_q2=float(getattr(settings, "BAYESIAN_FUNDAMENTALS_GDP_Q2", 2.0)),
                        incumbent_approval=float(getattr(settings, "BAYESIAN_FUNDAMENTALS_APPROVAL", 45.0)),
                        is_first_term=bool(getattr(settings, "BAYESIAN_FUNDAMENTALS_FIRST_TERM", True)),
                    )
                except Exception as e:
                    logger.debug("Bayesian fundamentals setup failed: %s", e)

            # Wire new signal sources into SignalIngestionService
            if self.signal_ingestion is not None:
                if self.legislative_tracker is not None:
                    self.signal_ingestion.legislative_tracker = self.legislative_tracker
                if self.polling_client is not None:
                    self.signal_ingestion.polling_client = self.polling_client
                if self.court_monitor is not None:
                    self.signal_ingestion.court_monitor = self.court_monitor
                if self.intl_elections is not None:
                    self.signal_ingestion.intl_elections = self.intl_elections

            logger.info("Elite Model components: %d/6 initialized", _elite_ok)

            # DEAD-1 fix: Wire CorrelationRiskManager into RiskManager for CVaR tail-risk checks
            if self.correlation_risk is not None and self.risk_manager is not None:
                self.risk_manager.set_correlation_risk(self.correlation_risk)
                logger.debug("CorrelationRiskManager wired into RiskManager for CVaR checks")

            # ---- 2026 Alpha Roadmap: Wire 9 infrastructure modules + exchange adapters ----
            _alpha_ok = 0

            # Exchange adapters (Polymarket is primary; Kalshi, Coinbase, ForecastEx are optional)
            try:
                from base_engine.exchanges.polymarket_adapter import PolymarketAdapter
                from base_engine.exchanges.kalshi_adapter import KalshiAdapter
                from base_engine.exchanges.coinbase_adapter import CoinbaseAdapter
                from base_engine.exchanges.forecastex_adapter import ForecastExAdapter

                _poly_adapter = PolymarketAdapter(polymarket_client=self.client, db=self.db)
                self._exchange_adapters = [_poly_adapter]

                # Kalshi (uses existing kalshi_client)
                try:
                    _kalshi = KalshiAdapter(kalshi_client=self.kalshi_client)
                    if _kalshi.is_enabled():
                        self._exchange_adapters.append(_kalshi)
                except Exception as e:
                    logger.debug("KalshiAdapter init failed (non-critical): %s", e)

                # Coinbase prediction markets
                try:
                    _cb_key = getattr(settings, "COINBASE_PRED_API_KEY", None)
                    _cb_secret = getattr(settings, "COINBASE_PRED_API_SECRET", None)
                    _coinbase = CoinbaseAdapter(api_key=_cb_key, api_secret=_cb_secret)
                    if _coinbase.is_enabled():
                        self._exchange_adapters.append(_coinbase)
                except Exception as e:
                    logger.debug("CoinbaseAdapter init failed (non-critical): %s", e)

                # ForecastEx (IB Gateway)
                try:
                    _fx_host = getattr(settings, "FORECASTEX_IB_HOST", "127.0.0.1")
                    _fx_port = int(getattr(settings, "FORECASTEX_IB_PORT", 7497))
                    _forecastex = ForecastExAdapter(host=_fx_host, port=_fx_port)
                    if _forecastex.is_enabled():
                        self._exchange_adapters.append(_forecastex)
                except Exception as e:
                    logger.debug("ForecastExAdapter init failed (non-critical): %s", e)

                _alpha_ok += 1
            except Exception as e:
                logger.debug("Exchange adapter init failed (non-critical): %s", e)

            # ArbScanner (needs exchange adapters)
            try:
                self._arb_scanner = ArbScanner(adapters=self._exchange_adapters)
                _alpha_ok += 1
            except Exception as e:
                logger.debug("ArbScanner init failed (non-critical): %s", e)

            # SportsClient (API-Football key optional)
            try:
                _sports_key = getattr(settings, "API_FOOTBALL_KEY", None)
                self._sports_client = SportsClient(api_football_key=_sports_key)
                _alpha_ok += 1
            except Exception as e:
                logger.debug("SportsClient init failed (non-critical): %s", e)

            # I52: removed ContractChangeMonitor, AirdropTracker, RegulatoryMonitor,
            # WashTradingDetector, DomainCalibrator, AgenticRAG init blocks — dead writes.
            logger.info("2026 Alpha: %d/3 modules initialized, %d exchange adapters", _alpha_ok, len(self._exchange_adapters))

            # Wire event bus -> webhooks (#25 + #39): resolutions and anomalies push to webhooks
            if self.event_bus and self.webhook_dispatcher:
                async def _dispatch_market_resolved(payload):
                    await self.webhook_dispatcher.dispatch("market_resolved", payload)
                async def _dispatch_anomaly_detected(payload):
                    await self.webhook_dispatcher.dispatch("anomaly_detected", payload)
                self.event_bus.on("market_resolved", _dispatch_market_resolved)
                self.event_bus.on("anomaly_detected", _dispatch_anomaly_detected)
            
        except Exception as e:
            if self.error_tracker:
                self.error_tracker.capture_exception(e, context={"phase": "engine_init"})
            logger.error("Failed to initialize engine components: %s", ascii(str(e)))
            raise RuntimeError(f"Failed to initialize engine components: {str(e)}") from e
        
        try:
            if self.learning_engine is not None:
                await self.learning_engine.init()
        except Exception as e:
            logger.warning("Learning engine init failed: %s", str(e))
        try:
            # Timeout training to prevent indefinite hang
            training_timeout = getattr(settings, "TRAINING_INIT_TIMEOUT", 120)
            await asyncio.wait_for(self.prediction_engine.init(), timeout=training_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Prediction engine training timed out after %ds. "
                "Bots will start without trained models and retrain on next cycle.",
                getattr(settings, "TRAINING_INIT_TIMEOUT", 120),
            )
        except Exception as e:
            logger.warning("Prediction engine init failed (DB and ingested data required): %s", str(e))

        # Coordination and lifecycle (mandatory for multi-bot)
        bot_id = getattr(settings, "BOT_ID", "default")
        if self.db is not None:
            self.kill_switch = KillSwitch(self.db, telegram_bot=None)
            if self.risk_manager is not None:
                self.risk_manager.set_kill_switch(self.kill_switch)
            if self.execution_engine is not None:
                self.execution_engine.set_kill_switch(self.kill_switch)
            self.trade_coordinator = TradeCoordinator(self.db, bot_id)
            adverse_tracker = None
            if self.db is not None:
                try:
                    from base_engine.analysis.game_theory import AdverseSelectionTracker
                    adverse_tracker = AdverseSelectionTracker(self.db)
                except Exception as e:
                    logger.debug("AdverseSelectionTracker not available: %s", e)
            # Wire OrderBookAnalyzer + SmartOrderPlacer for limit-order price improvement
            from base_engine.analysis.game_theory import OrderBookAnalyzer, SmartOrderPlacer, CascadeDetector
            _ob_analyzer = OrderBookAnalyzer()
            _smart_placer = SmartOrderPlacer()
            _cascade_detector = CascadeDetector(
                self.db, threshold=getattr(settings, "CASCADE_SCORE_THRESHOLD", 0.6)
            ) if self.db else None

            # RL Trade Timing Agent: learns WHEN to trade from paper trade outcomes
            # Disabled by default (RL_TRADE_TIMING_ENABLED=false); enable in .env to activate
            if getattr(settings, "RL_TRADE_TIMING_ENABLED", False):
                try:
                    from pathlib import Path
                    self.rl_agent = RLTradeTimingAgent(
                        learning_rate=getattr(settings, "RL_LEARNING_RATE", 0.1),
                        discount_factor=getattr(settings, "RL_DISCOUNT_FACTOR", 0.95),
                        epsilon_start=getattr(settings, "RL_EPSILON_START", 0.3),
                        epsilon_min=getattr(settings, "RL_EPSILON_MIN", 0.05),
                        epsilon_decay_trades=getattr(settings, "RL_EPSILON_DECAY_TRADES", 500),
                        replay_buffer_size=getattr(settings, "RL_REPLAY_BUFFER_SIZE", 2000),
                        replay_batch_size=getattr(settings, "RL_REPLAY_BATCH_SIZE", 32),
                    )
                    _rl_state_path = Path("data/rl_qtable.pkl")
                    if _rl_state_path.exists():
                        self.rl_agent.load(_rl_state_path)
                        logger.info("RL Trade Timing agent loaded from %s", _rl_state_path)
                    else:
                        logger.info("RL Trade Timing agent initialized (no prior state)")
                except Exception as e:
                    logger.warning("RL Trade Timing agent init failed (non-critical): %s", e)
                    self.rl_agent = None

            self.order_gateway = OrderGateway(
                kill_switch=self.kill_switch,
                risk_manager=self.risk_manager,
                trade_coordinator=self.trade_coordinator,
                execution_engine=self.execution_engine,
                liquidity_guardian=self.liquidity_guardian,
                adverse_selection_tracker=adverse_tracker,
                orderbook_analyzer=_ob_analyzer,
                smart_order_placer=_smart_placer,
                paper_trading_engine=self.paper_trading,
                cascade_detector=_cascade_detector,
                dynamic_position_sizing=self.dynamic_position_sizing,
                drawdown_controller=self.drawdown_controller,
                multi_kill_switch=self.multi_kill_switch,
                rl_agent=self.rl_agent,
            )
            # Share market index with order gateway for condition_id lookups (order book API)
            self.order_gateway._market_index = self._market_index
            if self.advanced_order_manager and hasattr(self.advanced_order_manager, "set_order_gateway"):
                self.advanced_order_manager.set_order_gateway(self.order_gateway)
            if self.smart_order_router and hasattr(self.smart_order_router, "set_order_gateway"):
                self.smart_order_router.set_order_gateway(self.order_gateway)
            if self.order_management_system and hasattr(self.order_management_system, "set_order_gateway"):
                self.order_management_system.set_order_gateway(self.order_gateway)
            if self.copy_trading_engine and hasattr(self.copy_trading_engine, "set_order_gateway"):
                self.copy_trading_engine.set_order_gateway(self.order_gateway)
            if self.position_manager and hasattr(self.position_manager, "set_order_gateway"):
                self.position_manager.set_order_gateway(self.order_gateway)
            if self.position_manager and self.risk_manager and hasattr(self.position_manager, "set_risk_manager"):
                self.position_manager.set_risk_manager(self.risk_manager)
            # RL Trade Timing: wire paper trading outcome callback so RL learns from sells
            if self.rl_agent and self.paper_trading:
                _rl_ref = self.rl_agent
                def _rl_outcome_cb(market_id, realized_pnl, exit_price, avg_entry_price):
                    """Sync callback → sync RL record_outcome_from_trade (Q-table update)."""
                    try:
                        _rl_ref.record_outcome_from_trade(market_id, realized_pnl)
                    except Exception as _e:
                        logger.debug("RL outcome recording failed: %s", _e)
                self.paper_trading.set_rl_outcome_callback(_rl_outcome_cb)
                logger.info("RL Trade Timing callback wired to PaperTradingEngine")
            # P4-03: Multi-layered kill switch wrapping existing KillSwitch
            self.multi_kill_switch = MultiLayerKillSwitch(
                base_kill_switch=self.kill_switch,
                db=self.db,
                alerting=self.alerting_system,
            )
            # BUG-1 fix: OrderGateway was created before multi_kill_switch — backfill the reference
            self.order_gateway.multi_kill_switch = self.multi_kill_switch
            # Wire OrderGateway into RiskManager for in-memory position/exposure lookups (avoids 3+ DB queries)
            if self.risk_manager is not None:
                self.risk_manager.set_order_gateway(self.order_gateway)
            # Seed in-memory position tracker from DB for ms-latency reactive path
            if self.db is not None:
                try:
                    await self.order_gateway.seed_positions_from_db(self.db)
                except Exception as e:
                    logger.debug("Position seed from DB failed (non-critical): %s", e)
            self.elite_detector = EliteUserDetector(self.db) if self.db is not None else None
            if self.learning_engine is not None and self.prediction_engine is not None and self.db is not None:
                self.incremental_learner = IncrementalLearner(
                    self.learning_engine,
                    self.prediction_engine,
                    self.db,
                )
                interval_hours = getattr(settings, "RETRAIN_INTERVAL_HOURS", 6)
                initial_delay = getattr(settings, "LEARNING_SCHEDULER_INITIAL_DELAY_SECONDS", 60)
                _meta_learner = MetaLearner()
                # Feature importance components
                _causal_engine = None
                if getattr(settings, "USE_CAUSAL_IMPORTANCE", True):
                    try:
                        _causal_engine = CausalInferenceEngine()
                    except Exception as e:
                        logger.debug("CausalInferenceEngine init failed: %s", e)
                _feature_engineer = getattr(self.prediction_engine, "_feature_engineer", None)
                self.scheduler = LearningScheduler(
                    self.db,
                    self.learning_engine,
                    self.prediction_engine,
                    interval_hours=interval_hours,
                    elite_detector=self.elite_detector,
                    calibration_tracker=self.calibration_tracker,
                    initial_delay_seconds=initial_delay,
                    alerting=self.alerting_system,
                    incremental_learner=self.incremental_learner,
                    meta_learner=_meta_learner,
                    causal_engine=_causal_engine,
                    feature_engineer=_feature_engineer,
                    model_version_manager=self.model_version_manager,
                )
            if self.data_ingestion is not None:
                interval_min = getattr(settings, "INGESTION_SCHEDULER_INTERVAL_MINUTES", 5)
                top_markets = getattr(settings, "INGESTION_TOP_MARKETS_COUNT", 500)
                ingest_initial_delay = getattr(settings, "INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS", 30)
                daily_full = getattr(settings, "DAILY_FULL_INGESTION_ENABLED", True)
                # Fallbacks aligned with config/settings.py (365/1000/1000)
                daily_days = getattr(settings, "DAILY_INGESTION_DAYS_BACK", 365)
                daily_markets = getattr(settings, "DAILY_INGESTION_MARKETS_COUNT", 1000)
                daily_prices = getattr(settings, "DAILY_INGESTION_PRICES_MARKETS", 1000)
                self.ingestion_scheduler = IngestionScheduler(
                    self.data_ingestion,
                    elite_detector=self.elite_detector,
                    interval_minutes=interval_min,
                    top_markets_count=top_markets,
                    initial_delay_seconds=ingest_initial_delay,
                    daily_full_ingestion_enabled=daily_full,
                    daily_days_back=daily_days,
                    daily_markets_count=daily_markets,
                    daily_prices_markets=daily_prices,
                    alerting=self.alerting_system,
                    auto_healer=self.auto_healer,
                    performance_tracker=self.performance_tracker,
                )
            components = {
                "scheduler": self.scheduler,
                "learning_engine": self.learning_engine,
                "prediction_engine": self.prediction_engine,
                "trade_coordinator": self.trade_coordinator,
                "database": self.db,
            }
            self.lifecycle_manager = LifecycleManager(components)

        try:
            logger.info("Base engine initialized")
        except Exception:
            print("Base engine initialized (logger not available)")

    def register_bot_for_price_events(self, bot: Any) -> None:
        """Phase 4: Register a bot so it receives real-time price_update events from WebSocket."""
        if self.event_bus is not None and hasattr(bot, "on_price_update"):
            self.event_bus.on("price_update", bot.on_price_update)

    def _on_bg_task_done(self, task: asyncio.Task, name: str = "unknown") -> None:
        """Callback for background task completion. Logs crash."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.critical("Background task '%s' crashed: %s", name, exc, exc_info=exc)

    async def start(self):
        self.running = True
        logger.info("Starting base engine services")

        # Enable paper trading when SIMULATION_MODE=true (orders go to PaperTradingEngine, not real API)
        if getattr(settings, "SIMULATION_MODE", False) and self.paper_trading:
            self.paper_trading.enable()
            # Seed paper positions from DB so SELL exits work for positions opened in prior sessions
            await self.paper_trading.seed_positions_from_db()

        # Load tunable config from DB (P5-01)
        if self.tunable_config:
            try:
                await self.tunable_config.load()
            except Exception as e:
                logger.debug("Tunable config load failed (non-critical): %s", e)

        # Phase 6: Restore adverse selection fill history from DB (survives restarts)
        if self.order_gateway and getattr(self.order_gateway, "adverse_selection_tracker", None):
            try:
                n = await self.order_gateway.adverse_selection_tracker.restore_fills_from_db()
                if n:
                    logger.info("Restored %d fills from fill_analysis table", n)
            except Exception as e:
                logger.debug("Fill history restore failed (non-critical): %s", e)

        # Phase 1: Pre-approve USDCe (MAX_UINT256) at startup to populate ApprovalCache and skip per-order on-chain checks
        if getattr(settings, "PREAPPROVE_ON_STARTUP", True) and self.execution_engine and getattr(self.execution_engine, "contract_manager", None):
            try:
                await self.execution_engine.contract_manager.ensure_usdce_approved(amount_usd=None)
            except Exception as e:
                logger.debug("Pre-approval at startup failed (non-critical): %s", e)

        # Initialize oracle monitor (P5-06)
        if self.oracle_monitor:
            try:
                await self.oracle_monitor.init()
            except Exception as e:
                logger.debug("Oracle monitor init failed (non-critical): %s", e)

        # Initialize position reconciler (P2B-06)
        if self.position_reconciler:
            try:
                await self.position_reconciler.init()
            except Exception as e:
                logger.debug("Position reconciler init failed (non-critical): %s", e)

        await self.data_ingestion.start()

        # Start signal ingestion
        if self.signal_ingestion:
            await self.signal_ingestion.start()

        # Start whale tracking
        if self.whale_tracker:
            await self.whale_tracker.start_monitoring()

        # Start automated position monitoring (stop-loss / take-profit)
        if self.position_manager:
            await self.position_manager.start_monitoring()

        # I58: Pre-populate market index from DB so bots have full metadata from their first scan.
        # Without this, WS messages arriving in the first 30s have no market metadata.
        try:
            _initial_markets = await self._fetch_tradeable_markets()
            if _initial_markets:
                self.update_market_index(_initial_markets)
                logger.info("I58: Market index pre-populated at startup", count=len(_initial_markets))
        except Exception as _prepop_err:
            logger.debug("I58: Market index pre-populate failed (non-critical): %s", _prepop_err)

        # --- Startup stagger: let initial DB connections settle before opening more ---
        await asyncio.sleep(2)

        # Connect WebSocket (optional, may fail if not available)
        if self.websocket_manager:
            try:
                await self.websocket_manager.connect()
            except Exception as e:
                logger.warning("WebSocket connection failed (non-critical): %s", e)
            if self.user_order_websocket:
                try:
                    await self.user_order_websocket.connect()
                except Exception as e:
                    logger.debug("UserOrderWebSocket connect failed (non-critical): %s", e)
            if self.streaming_persister:
                try:
                    await self.streaming_persister.start()
                except Exception as e:
                    logger.warning("StreamingPersister start failed (non-critical): %s", e)

            # Subscribe to active markets for real-time price streaming
            self._subscribe_task = asyncio.create_task(self._subscribe_active_markets())
            self._subscribe_task.add_done_callback(lambda t: self._on_bg_task_done(t, "subscribe_active_markets"))

        # UMA ProposePrice monitor: poll OOv3 for proposed_outcome events (optional)
        if getattr(settings, "UMA_PROPOSAL_MONITOR_ENABLED", False) and self.event_bus and getattr(settings, "UMA_OO_V3_POLYGON", None):
            try:
                from base_engine.data.blockchain_client import BlockchainClient
                from base_engine.data.uma_proposal_monitor import run_uma_proposal_monitor
                _uma_bc = BlockchainClient()
                self._uma_monitor_task = asyncio.create_task(
                    run_uma_proposal_monitor(
                        self.event_bus,
                        _uma_bc,
                        contract_address=getattr(settings, "UMA_OO_V3_POLYGON", None),
                        poll_interval_seconds=120.0,
                    )
                )
                self._uma_monitor_task.add_done_callback(lambda t: self._on_bg_task_done(t, "uma_monitor"))
                logger.info("UMA proposal monitor started")
            except Exception as e:
                logger.debug("UMA proposal monitor start failed (non-critical): %s", e)

        # Start mempool monitoring (if available)
        if self.mempool_monitor:
            try:
                await self.mempool_monitor.start()
                logger.info("Mempool monitoring started")
            except Exception as e:
                # Encode to ASCII with replacement to avoid charmap codec errors on Windows
                # when the RPC error response contains non-ASCII/emoji characters.
                _err_safe = str(e).encode("ascii", errors="replace").decode("ascii")
                logger.warning("Mempool monitoring failed to start (non-critical): %s", _err_safe)

        # --- Startup stagger: space out scheduler starts to avoid thundering herd on DB ---
        await asyncio.sleep(2)

        # Start learning scheduler (periodic get_trades_since → learn_from_trades → retrain)
        if self.scheduler is not None:
            try:
                await self.scheduler.start()
                logger.info("Learning scheduler started")
            except Exception as e:
                logger.warning(f"Learning scheduler failed to start (non-critical): {str(e)}")

        # Start ingestion scheduler (periodic markets; daily full ingestion when DAILY_FULL_INGESTION_ENABLED)
        if self.ingestion_scheduler is not None:
            try:
                await self.ingestion_scheduler.start()
                logger.info("Ingestion scheduler started")
            except Exception as e:
                logger.warning("Ingestion scheduler failed to start (non-critical)", error=str(e))

        # Start stale reservation reaper (cleans ghost reservations from crashed processes)
        if self.trade_coordinator is not None:
            try:
                await self.trade_coordinator.start_reaper()
            except Exception as e:
                logger.warning("Stale reservation reaper failed to start (non-critical)", error=str(e))

        # --- Startup stagger: let schedulers initialize before heavy feature precompute ---
        await asyncio.sleep(3)

        # Start feature pre-computation background loop.
        # Every 60s, pre-computes feature vectors for all active markets so
        # EnsembleBot scan_and_trade() predict() calls hit the fast path (zero DB queries).
        if self.prediction_engine and self.prediction_engine.initialized:
            self._feature_precompute_task = asyncio.create_task(self._feature_precompute_loop())
            self._feature_precompute_task.add_done_callback(lambda t: self._on_bg_task_done(t, "feature_precompute"))
            logger.info("Feature pre-compute background loop started")
            # B12: KS test regime detection — background check every 30min
            self._ks_regime_task = asyncio.create_task(self._ks_regime_detection_loop())
            self._ks_regime_task.add_done_callback(lambda t: self._on_bg_task_done(t, "ks_regime"))
            logger.info("B12 KS regime detection loop started")

        # Observability SLI loop — always start (lightweight, <1ms per check)
        self._sli_task = asyncio.create_task(self._observability_sli_loop())
        self._sli_task.add_done_callback(lambda t: self._on_bg_task_done(t, "observability_sli"))
        logger.info("Observability SLI monitoring loop started")

        # ── Self-healing architecture startup ────────────────────────────────
        # Initialize streaming anomaly detector, log miner, portfolio drawdown,
        # degradation manager, and APScheduler health scheduler.
        # All are non-critical: failures here are logged and swallowed.
        try:
            # 1. Streaming ADWIN anomaly detector
            self.streaming_anomaly_detector = StreamingAnomalyDetector()

            # 2. Log template miner (Drain3 tails paper_trading.log)
            self.log_template_miner = LogTemplateMiner()

            # 3. Portfolio drawdown circuit breaker — daily limit from DAILY_LOSS_LIMIT_PCT setting
            self.portfolio_drawdown_breaker = PortfolioDrawdownBreaker(
                daily_loss_limit_pct=getattr(settings, "DAILY_LOSS_LIMIT_PCT", 0.05),
            )

            # 4. Degradation manager with fleet-level tier control
            self.degradation_manager = DegradationManager(
                total_bots=8,
                order_gateway=self.order_gateway,
            )

            # 5. APScheduler health scheduler — wires all components together
            self.health_scheduler = HealthScheduler(
                health_monitor=self.health_monitor,
                streaming_anomaly=self.streaming_anomaly_detector,
                log_miner=self.log_template_miner,
                degradation_manager=self.degradation_manager,
                drawdown_breaker=self.portfolio_drawdown_breaker,
                base_engine=self,
                sports_db=self.db,  # I02: wire sports_db so calibration job can persist to DB
            )
            self.health_scheduler.start()
            logger.info("Self-healing architecture started (state_machine, ADWIN, drain3, drawdown, scheduler)")
        except Exception as _sh_err:
            logger.warning("Self-healing architecture startup failed (non-critical): %s", _sh_err)

        # Start resolution listener: resolutions -> event_bus -> webhooks
        if self.resolution_listener and self.event_bus:
            try:
                async def _on_resolution(payload):
                    await self.event_bus.emit("market_resolved", payload)
                    # Immediately backfill prediction_log and paper_trades for this resolution
                    try:
                        n1 = await self.db.backfill_prediction_log_resolution()
                        n2 = await self.db.backfill_paper_trades_resolution()
                        if n1 or n2:
                            logger.info("Resolution event: backfilled %d prediction_log + %d paper_trades", n1, n2)
                    except Exception as _e:
                        logger.debug("Resolution event backfill failed (non-fatal): %s", _e)

                    # Feed resolved outcomes back into drift tracker (B1 + ADWIN accuracy + calibration).
                    # Queries the prediction_log rows that were just resolved and calls record_outcome()
                    # so high-surprise deque, _recent_outcomes, and calibration drift stay current.
                    try:
                        pe = self.prediction_engine
                        if pe and pe.initialized and pe._drift_tracker and self.db.session_factory:
                            from sqlalchemy import text as _sa_text
                            async with self.db.get_session() as _sess:
                                _rows = await _sess.execute(_sa_text("""
                                    SELECT market_id, predicted_prob, resolution
                                    FROM prediction_log
                                    WHERE resolution IN ('YES', 'NO')
                                      AND was_correct IS NOT NULL
                                      AND resolved_at >= NOW() - INTERVAL '5 minutes'
                                    ORDER BY resolved_at DESC
                                    LIMIT 100
                                """))
                                resolved_rows = _rows.fetchall()
                            for _row in resolved_rows:
                                try:
                                    _mid = str(_row[0]) if _row[0] else None
                                    _pred = float(_row[1]) if _row[1] is not None else 0.5
                                    _actual = 1 if str(_row[2]).upper() == "YES" else 0
                                    pe._drift_tracker.record_outcome(_pred, _actual, market_id=_mid)
                                except Exception:
                                    pass
                            if resolved_rows:
                                logger.debug(
                                    "Drift tracker fed %d resolved outcomes (B1 + ADWIN accuracy)",
                                    len(resolved_rows),
                                )
                    except Exception as _fe:
                        logger.debug("Drift tracker outcome feed failed (non-fatal): %s", _fe)
                self.resolution_listener.start(_on_resolution)
                logger.info("Resolution listener started (events -> event_bus -> webhooks + feedback backfill)")
            except Exception as e:
                logger.warning("Resolution listener start failed (non-critical): %s", e)
    
    async def stop(self):
        self.running = False
        logger.info("Stopping base engine services")
        db_closed_by_lifecycle = False
        if self.lifecycle_manager is not None:
            try:
                await self.lifecycle_manager.shutdown()
                db_closed_by_lifecycle = True
            except Exception as e:
                logger.warning("Lifecycle shutdown failed: %s", e)

        stop_errors = []

        # Stop self-healing health scheduler
        try:
            if self.health_scheduler:
                self.health_scheduler.stop()
        except Exception as e:
            stop_errors.append(f"health_scheduler: {str(e)}")

        # Stop position manager monitoring
        try:
            if self.position_manager:
                await self.position_manager.stop_monitoring()
        except Exception as e:
            stop_errors.append(f"position_manager: {str(e)}")
            logger.warning(f"Error stopping position manager: {str(e)}")
        
        # Stop signal ingestion
        try:
            if self.signal_ingestion:
                await self.signal_ingestion.stop()
        except Exception as e:
            stop_errors.append(f"signal_ingestion: {str(e)}")
            logger.warning(f"Error stopping signal ingestion: {str(e)}")
        
        # Stop whale tracking
        try:
            if self.whale_tracker:
                await self.whale_tracker.stop_monitoring()
        except Exception as e:
            stop_errors.append(f"whale_tracker: {str(e)}")
            logger.warning(f"Error stopping whale tracker: {str(e)}")
        
        # Release all reserving positions for bots that placed orders in this process (multi-bot shutdown)
        try:
            if self.trade_coordinator and self.order_gateway:
                used = getattr(self.order_gateway, "_bot_names_used", None)
                if used:
                    n = await self.trade_coordinator.release_all_reservations(bot_ids=list(used))
                    if n:
                        logger.info("Released reservations for bots on shutdown", count=n, bot_ids=list(used))
                else:
                    await self.trade_coordinator.release_all_reservations()
            elif self.trade_coordinator:
                await self.trade_coordinator.release_all_reservations()
        except Exception as e:
            stop_errors.append("trade_coordinator_release: " + str(e))
            logger.warning("Error releasing reservations on shutdown: %s", e)
        # Stop stale reservation reaper
        try:
            if self.trade_coordinator:
                await self.trade_coordinator.stop_reaper()
        except Exception as e:
            stop_errors.append("trade_coordinator_reaper: " + str(e))
            logger.warning("Error stopping reservation reaper", error=str(e))

        # Stop streaming persister (flush remaining to DB)
        try:
            if self.streaming_persister:
                await self.streaming_persister.stop()
        except Exception as e:
            stop_errors.append("streaming_persister: " + str(e))
            logger.warning("Error stopping StreamingPersister: %s", e)
        # Save RL Trade Timing Q-table on shutdown
        try:
            if self.rl_agent:
                from pathlib import Path
                self.rl_agent.save(Path("data/rl_qtable.pkl"))
                logger.info("RL Trade Timing Q-table saved to data/rl_qtable.pkl")
        except Exception as e:
            stop_errors.append("rl_agent_save: " + str(e))
            logger.warning("RL Q-table save failed (non-critical): %s", e)
        # Stop resolution listener
        try:
            if self.resolution_listener:
                self.resolution_listener.stop()
        except Exception as e:
            stop_errors.append("resolution_listener: " + str(e))
            logger.warning("Error stopping resolution listener: %s", e)
        # Cancel UMA proposal monitor task (if running)
        try:
            _uma_task = getattr(self, "_uma_monitor_task", None)
            if _uma_task and not _uma_task.done():
                _uma_task.cancel()
                try:
                    await _uma_task
                except asyncio.CancelledError:
                    pass
        except Exception as e:
            stop_errors.append(f"uma_monitor_task: {str(e)}")

        # Close exchange adapter connections
        for adapter in getattr(self, "_exchange_adapters", []):
            try:
                if hasattr(adapter, "close"):
                    await adapter.close()
            except Exception as e:
                _name = getattr(adapter, "platform_name", type(adapter).__name__)
                stop_errors.append(f"adapter_{_name}: {str(e)}")

        # Disconnect WebSockets (user channel first, then market)
        try:
            if getattr(self, "user_order_websocket", None):
                await self.user_order_websocket.disconnect()
        except Exception as e:
            stop_errors.append(f"user_order_websocket: {str(e)}")
        try:
            if self.websocket_manager:
                await self.websocket_manager.disconnect()
        except Exception as e:
            stop_errors.append(f"websocket_manager: {str(e)}")
            logger.warning("Error disconnecting WebSocket: %s", e)

        # Stop mempool monitoring
        try:
            if self.mempool_monitor:
                await self.mempool_monitor.stop()
        except Exception as e:
            stop_errors.append(f"mempool_monitor: {str(e)}")
            logger.warning(f"Error stopping mempool monitor: {str(e)}")
        
        try:
            if self.ingestion_scheduler:
                await self.ingestion_scheduler.stop()
        except Exception as e:
            stop_errors.append(f"ingestion_scheduler: {str(e)}")
            logger.warning(f"Error stopping ingestion scheduler: {str(e)}")
        try:
            if self.data_ingestion:
                await self.data_ingestion.stop()
        except Exception as e:
            stop_errors.append(f"data_ingestion: {str(e)}")
            logger.warning(f"Error stopping data ingestion: {str(e)}")
        
        try:
            if self.cache:
                await self.cache.close()
        except Exception as e:
            stop_errors.append(f"cache: {str(e)}")
            logger.warning(f"Error closing cache: {str(e)}")
        
        try:
            if self.db and not db_closed_by_lifecycle:
                await self.db.close()
            elif self.db and db_closed_by_lifecycle:
                self.db = None
        except Exception as e:
            stop_errors.append(f"database: {str(e)}")
            logger.warning(f"Error closing database: {str(e)}")
        
        try:
            # BUG FIX: Properly check and close httpx client
            # Root cause: Code checked hasattr but PolymarketClient.client is the httpx.AsyncClient
            # Impact: Client connections not properly closed, causing resource leaks
            # Fix: Check if client exists and has a client attribute that is an httpx.AsyncClient
            if self.client is not None:
                # PolymarketClient wraps httpx.AsyncClient in self.client attribute
                if hasattr(self.client, 'client') and self.client.client is not None:
                    try:
                        await self.client.client.aclose()
                        self.client.client = None
                    except Exception as close_error:
                        stop_errors.append(f"client_close: {str(close_error)}")
                        logger.warning(f"Error closing httpx client: {str(close_error)}")
        except Exception as e:
            stop_errors.append(f"client: {str(e)}")
            logger.warning(f"Error closing client: {str(e)}")
        
        if stop_errors:
            logger.warning(f"Some services had errors during shutdown: {', '.join(stop_errors)}")
        else:
            logger.info("All services stopped successfully")

    async def _feature_precompute_loop(self) -> None:
        """Background loop: pre-compute feature vectors for all active markets every 60s.

        Populates PredictionEngine._feature_vector_cache so scan_and_trade() predict()
        calls hit the fast path (in-memory feature lookup → model inference, zero DB queries).
        """
        # Wait 150s for bots to complete first scan before feature precompute starts.
        # EnsembleBot cold scan takes up to 120s from t≈15s → completes at t≈135s.
        # 150s ensures no overlap with EnsembleBot cold scan. Reduces pool contention.
        await asyncio.sleep(150)
        pe = self.prediction_engine
        while self.running:
            try:
                # Model-ready guard: skip pre-compute until models are fully trained and initialized
                if not pe.models or not getattr(pe, "initialized", False):
                    logger.debug("Feature pre-compute: waiting for prediction engine to be ready...")
                    await asyncio.sleep(10)
                    continue
                markets = await self.get_all_tradeable_markets()
                if markets:
                    ids = [str(m["id"]) for m in markets if m.get("id")]
                    n = await pe.batch_precompute_all_features(ids)
                    logger.info("Feature pre-compute: %d/%d markets cached", n, len(ids))
                    # Mark cache as warmed after first successful run so scan loop knows to proceed
                    if n > 0 and not pe._feature_cache_warmed:
                        pe._feature_cache_warmed = True
                        logger.info("Feature vector cache warmed: %d markets pre-computed", n)
            except Exception as e:
                logger.warning("Feature pre-compute loop failed (non-fatal): %s", e)
            await asyncio.sleep(60)

    def get_observability_slis(self) -> Dict[str, Any]:
        """
        Observability SLIs (Section 6 of elite_polymarket_v2.docx).
        Returns a dict of current service health indicators:
          - data_freshness_seconds: age of newest ingested price record (alert >60s)
          - signal_staleness: oldest signal source age per source
          - ingestion_error_rate: fraction of failed ingestion cycles (alert >1%)
          - queue_utilization: B9 per-source queue depth (alert >80%)
          - scan_performance: last scan duration per bot in ms
        """
        import time as _time
        _now = _time.time()
        slis: Dict[str, Any] = {}

        # Data freshness: streaming persister last flush timestamp
        sp = getattr(self, "streaming_persister", None)
        if sp:
            _last_flush = getattr(sp, "_last_flush_ts", None)
            if _last_flush:
                slis["data_freshness_seconds"] = round(_now - _last_flush, 1)
                slis["data_freshness_ok"] = slis["data_freshness_seconds"] < 60.0

        # Signal ingestion queue utilization (B9)
        si = getattr(self, "signal_ingestion", None)
        if si and hasattr(si, "get_queue_stats"):
            q_stats = si.get_queue_stats()
            slis["queue_stats"] = q_stats
            _overloaded = [s for s, v in q_stats.items() if v.get("utilization", 0) > 0.8]
            if _overloaded:
                slis["queue_overloaded_sources"] = _overloaded

        # DB pool semaphore availability
        db = getattr(self, "db", None)
        if db:
            _sem = getattr(db, "_semaphore", None)
            if _sem:
                _free = getattr(_sem, "_value", None)
                if _free is not None:
                    slis["db_semaphore_free"] = _free
                    slis["db_semaphore_ok"] = _free > 2

        return slis

    async def _observability_sli_loop(self) -> None:
        """Periodic SLI reporting — logs alerts for any metric outside safe bounds."""
        await asyncio.sleep(120)  # 2 min initial delay
        while self.running:
            try:
                slis = self.get_observability_slis()
                # Data freshness alert
                freshness = slis.get("data_freshness_seconds")
                if freshness is not None and freshness > 60:
                    logger.warning("SLI ALERT: data_freshness=%.0fs (threshold=60s)", freshness)
                # Queue overload alert
                overloaded = slis.get("queue_overloaded_sources")
                if overloaded:
                    logger.warning("SLI ALERT: signal queues >80%% full: %s", overloaded)
                # DB semaphore depletion alert
                sem_free = slis.get("db_semaphore_free")
                if sem_free is not None and sem_free <= 2:
                    logger.warning("SLI ALERT: DB semaphore near-exhaustion: free=%d", sem_free)
                logger.debug("SLI snapshot: %s", slis)
            except Exception as _sli_err:
                logger.debug("SLI loop error (non-fatal): %s", _sli_err)
            await asyncio.sleep(60)  # Check every 60s

    def publish_feature_lift(self, bot_name: str, feature_stats: Dict[str, float]) -> None:
        """
        B5: Any bot calls this after scan to share which features had positive lift.
        Stores rolling 2h window of (timestamp, lift) tuples per feature.
        """
        import time as _time
        _now = _time.time()
        _cutoff = _now - 7200.0  # 2h rolling window
        for _feat, _lift in feature_stats.items():
            if _feat not in self._shared_feature_stats:
                self._shared_feature_stats[_feat] = []
            _entries = self._shared_feature_stats[_feat]
            _entries.append((_now, float(_lift), bot_name))
            # Prune stale entries
            self._shared_feature_stats[_feat] = [e for e in _entries if e[0] > _cutoff]

    def get_cross_bot_feature_boost(self, feature_name: str) -> float:
        """
        B5: EnsembleBot calls this to get average lift for a feature from other bots.
        Returns multiplier (1.0 = neutral, >1.0 = other bots found this feature informative).
        """
        import time as _time
        _now = _time.time()
        _cutoff = _now - 7200.0
        entries = [e for e in self._shared_feature_stats.get(feature_name, []) if e[0] > _cutoff]
        if not entries:
            return 1.0
        avg_lift = sum(e[1] for e in entries) / len(entries)
        # Map lift to boost: lift > 0.05 = up to 1.05× boost, capped at 1.10×
        return min(1.10, 1.0 + max(0.0, avg_lift) * 0.5)

    async def _ks_regime_detection_loop(self) -> None:
        """
        B12: KS-test based regime detection (scipy — already installed as sklearn transitive dep).
        Every 30 min: compare feature distributions from last 2h vs prior 2h.
        KS statistic > 0.3 = regime shift candidate → log warning and set flag.
        Also starts B12 KS regime loop task via create_task in start().
        """
        await asyncio.sleep(300)  # 5 min initial delay
        while self.running:
            try:
                pe = self.prediction_engine
                if pe and pe.initialized and pe._drift_tracker:
                    dt = pe._drift_tracker
                    preds = list(dt._recent_predictions)
                    if len(preds) >= 40:
                        from scipy import stats as _scipy_stats
                        half = len(preds) // 2
                        _ks_stat, _ks_pval = _scipy_stats.ks_2samp(preds[:half], preds[half:])
                        if _ks_stat > 0.3:
                            logger.warning(
                                "B12 KS regime shift detected: ks_stat=%.3f p=%.4f — "
                                "feature distribution changed significantly in last hour",
                                _ks_stat, _ks_pval,
                            )
                        else:
                            logger.debug("B12 KS regime check: ks_stat=%.3f (stable)", _ks_stat)
            except Exception as _b12_err:
                logger.debug("B12 KS regime loop error (non-fatal): %s", _b12_err)
            await asyncio.sleep(1800)  # 30 min

    async def _subscribe_active_markets(self) -> None:
        """Subscribe WebSocket to active markets for real-time price streaming."""
        if not self.websocket_manager:
            return
        try:
            markets = await self.get_markets(active=True, limit=500)
            token_ids: List[str] = []
            for m in markets:
                yes_tid = (m.get("yes_token_id") or m.get("yesTokenId") or "").strip()
                no_tid = (m.get("no_token_id") or m.get("noTokenId") or "").strip()
                if yes_tid:
                    token_ids.append(yes_tid)
                if no_tid:
                    token_ids.append(no_tid)
                # Also check tokens list (API format)
                for tok in (m.get("tokens") or []):
                    tid = (tok.get("token_id") or "").strip()
                    if tid and tid not in token_ids:
                        token_ids.append(tid)
            if token_ids:
                await self.websocket_manager.subscribe_price_stream(token_ids)
                logger.info("WebSocket subscribed to %d token price streams", len(token_ids))
            else:
                logger.warning("No token IDs found for WebSocket subscription")
        except Exception as e:
            logger.warning("WebSocket market subscription failed (non-critical): %s", e)

    async def get_markets(
        self, 
        active: bool = True, 
        limit: int = 100, 
        use_unified_service: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get markets using unified market service (recommended) or fallback to legacy method.
        
        Args:
            active: If True, fetch active markets
            limit: Maximum number of markets to return
            use_unified_service: If True, use UnifiedMarketService (recommended)
        """
        # Use unified market service if available (recommended)
        if use_unified_service and self.unified_market_service:
            return await self.unified_market_service.get_markets(
                active=active,
                limit=limit,
                use_cache=True
            )
        
        # Fallback to legacy method for backward compatibility
        if (self.data_ingestion is not None and 
            hasattr(self.data_ingestion, 'cached_markets') and 
            self.data_ingestion.cached_markets):
            cached = self.data_ingestion.get_cached_markets(limit=limit, active=active)
            if cached:
                logger.debug(f"Returning {len(cached)} markets from ingestion cache")
                return cached
        
        if self.cache and self.cache.redis:
            try:
                cache_key = f"markets:all:{active}"
                cached = await self.cache.get(cache_key)
                if cached and isinstance(cached, list) and len(cached) > 0:
                    filtered = [m for m in cached if m.get("active") == active] if active is not None else cached
                    if filtered:
                        result = filtered[:limit] if limit else filtered
                        logger.debug(f"Returning {len(result)} markets from Redis cache")
                        return result
            except Exception as e:
                logger.debug(f"Redis cache check failed: {str(e)}")
        
        result = await self.client.get_markets(active=active, limit=limit)
        
        if result and self.cache and self.cache.redis:
            try:
                cache_key = f"markets:all:{active}"
                await self.cache.set(cache_key, result, ttl=300)
            except Exception:
                pass
        
        return result

    async def get_markets_with_price_history(
        self,
        active: bool = True,
        limit: int = 500,
        price_limit_per_market: int = 50,
        use_db_scan: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Bot input: markets + price history. Clean, functional data for ML/strategy.
        Returns [{"market": {...}, "price_history": [{price, timestamp, ...}]}].
        Token IDs from yes_token_id/no_token_id (DB) or tokens[].tokenId (API).
        """
        if use_db_scan:
            markets = await self.get_all_tradeable_markets()
            # Apply limit BEFORE bulk price fetch. get_all_tradeable_markets() returns ALL
            # ~481 markets from cache; without this slice, get_recent_prices_bulk fetches
            # 481×50=24k price rows and holds a semaphore slot for several seconds.
            # Callers should pass limit=SCAN_MARKET_LIMIT to match their scan window.
            if limit and 0 < limit < len(markets):
                markets = markets[:limit]
        else:
            markets = await self.get_markets(active=active, limit=limit)

        # B17 FIX: Bulk price fetch (1 query) instead of N+1 per-market queries
        out: List[Dict[str, Any]] = []
        if self.db and hasattr(self.db, "get_recent_prices_bulk"):
            market_keys = []
            for m in markets:
                mid = m.get("id")
                if mid is None:
                    continue
                cond_id = m.get("condition_id") or m.get("conditionId") or ""
                market_keys.append((str(mid), str(cond_id) if cond_id else None))
            prices_bulk = await self.db.get_recent_prices_bulk(
                market_keys, limit_per_market=price_limit_per_market
            )
            for m in markets:
                mid = m.get("id")
                if mid is None:
                    continue
                out.append({
                    "market": m,
                    "price_history": prices_bulk.get(str(mid), [])
                })
        else:
            # Fallback to per-market queries
            for m in markets:
                mid = m.get("id")
                if mid is None:
                    continue
                market_id = str(mid)
                token_ids = self._extract_token_ids_from_market(m)
                prices = []
                if self.db and hasattr(self.db, "get_recent_prices_for_market"):
                    cond_id = m.get("condition_id") or m.get("conditionId") or ""
                    prices = await self.db.get_recent_prices_for_market(
                        market_id, token_ids=token_ids, limit=price_limit_per_market,
                        condition_id=str(cond_id) if cond_id else None,
                    )
                out.append({"market": m, "price_history": prices})
        return out

    def _extract_token_ids_from_market(self, m: Dict[str, Any]) -> Optional[List[str]]:
        """Extract token IDs for price lookup. DB format: yes_token_id/no_token_id. API format: tokens[].tokenId."""
        ids = []
        if m.get("yes_token_id"):
            ids.append(str(m["yes_token_id"]))
        if m.get("no_token_id"):
            ids.append(str(m["no_token_id"]))
        if ids:
            return ids
        tokens = m.get("tokens") or []
        if isinstance(tokens, list):
            for t in tokens:
                if isinstance(t, dict):
                    tid = t.get("tokenId") or t.get("token_id")
                    if tid:
                        ids.append(str(tid))
        return ids if ids else None

    async def get_all_tradeable_markets(
        self,
        min_liquidity: Optional[float] = None,
        categories: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get ALL active markets from DB that have price data and token IDs.
        This is the primary scan source — returns hundreds of markets, not API-limited batches.
        Falls back to get_markets() if DB unavailable.
        """
        if not self.db or not self.db.session_factory:
            return await self.get_markets(active=True, limit=500)

        # In-memory cache (60s TTL) + lock so only 1 caller does the DB query.
        # Without this, 4 bots call simultaneously → 8 concurrent DB sessions.
        _now = time.monotonic()
        _cache = getattr(self, "_tradeable_markets_cache", None)
        if _cache and _now - _cache[0] < 60 and not categories:
            return list(_cache[1])  # Return copy

        # Serialize DB access: only one caller queries, others wait + get cached result
        _lock = getattr(self, "_tradeable_markets_lock", None)
        if _lock is None:
            self._tradeable_markets_lock = asyncio.Lock()
            _lock = self._tradeable_markets_lock
        return await self._get_all_tradeable_markets_locked(
            _lock, min_liquidity, categories, _now
        )

    async def _get_all_tradeable_markets_locked(
        self, _lock, min_liquidity, categories, _now
    ):
        """Inner method that serializes DB access via lock."""
        async with _lock:
            # Re-check cache after acquiring lock
            _cache = getattr(self, "_tradeable_markets_cache", None)
            if _cache and _now - _cache[0] < 60 and not categories:
                return list(_cache[1])
            return await self._fetch_tradeable_markets(min_liquidity, categories, _now)

    async def _fetch_tradeable_markets(self, min_liquidity, categories, _now):
        min_liq = min_liquidity if min_liquidity is not None else getattr(settings, "MIN_MARKET_LIQUIDITY", 100)
        try:
            from sqlalchemy import select, text as sa_text
            from base_engine.data.database import Market

            # B1+B5: Remove INNER JOIN on market_prices (was excluding 80% of markets).
            # Feature extraction handles missing prices gracefully (fills zeros).
            # Order by liquidity DESC + LIMIT to prioritize high-value markets per scan cycle.
            scan_limit = getattr(settings, "SCAN_MARKET_LIMIT", 300)

            # B4: Check Redis cache first (TTL 300s) to avoid DB query every cycle
            _cache_key = f"tradeable_market_ids:{min_liq}:{scan_limit}"
            if self.cache:
                try:
                    cached = await self.cache.get(_cache_key)
                    if cached and isinstance(cached, list):
                        market_ids = cached
                        logger.debug("Tradeable market IDs from Redis cache", count=len(market_ids))
                    else:
                        cached = None
                except Exception:
                    cached = None
            else:
                cached = None

            if cached is None:
                async with self.db.get_session() as session:
                    result = await session.execute(sa_text(
                        "SELECT m.id FROM markets m "
                        "WHERE m.active = true "
                        "AND m.resolved = FALSE "
                        "AND ((m.yes_token_id IS NOT NULL AND m.yes_token_id != '') "
                        "  OR (m.no_token_id IS NOT NULL AND m.no_token_id != '')) "
                        "AND COALESCE(m.liquidity, 0) >= :min_liq "
                        "AND COALESCE(m.yes_price, 0.5) BETWEEN 0.01 AND 0.99 "  # I19: wider range
                        "ORDER BY COALESCE(m.liquidity, 0) DESC "
                        "LIMIT :scan_limit"
                    ), {"min_liq": min_liq, "scan_limit": scan_limit})
                    market_ids = [str(row[0]) for row in result.fetchall()]

                # Cache in Redis for 60s (I20: was 300s — resolved/delisted markets scanned for ≤60s now)
                if self.cache and market_ids:
                    try:
                        await self.cache.set(_cache_key, market_ids, ttl=60)
                    except Exception:
                        pass

            if not market_ids:
                logger.debug("No tradeable markets in DB, falling back to API")
                return await self.get_markets(active=True, limit=500)

            # Now fetch full market objects from L3 cache
            markets = []
            if self.unified_market_service and hasattr(self.unified_market_service, "_get_from_l3_cache"):
                # Batch fetch from DB
                async with self.db.get_session() as session:
                    from base_engine.data.database import Market as MarketModel
                    result = await session.execute(
                        select(MarketModel).where(
                            MarketModel.active == True,
                            MarketModel.id.in_(market_ids),
                        )
                    )
                    db_markets = result.scalars().all()
                    markets = [self.unified_market_service._market_to_dict(m) for m in db_markets]
            else:
                # Fallback: get each market individually
                for mid in market_ids:
                    m = await self.get_market(mid)
                    if m:
                        markets.append(m)

            # Apply category filter if specified
            if categories:
                markets = [
                    m for m in markets
                    if (m.get("category") or "").lower() in [c.lower() for c in categories]
                ]

            logger.info("Tradeable markets from DB", total=len(markets), min_liquidity=min_liq)
            # Store in-memory cache (60s TTL)
            if not categories:
                self._tradeable_markets_cache = (_now, markets)
            return markets

        except Exception as e:
            logger.warning("get_all_tradeable_markets DB query failed, falling back to API: %s", e)
            return await self.get_markets(active=True, limit=500)

    def filter_markets_for_trading(
        self,
        markets: List[Dict[str, Any]],
        min_liquidity: Optional[float] = None,
        categories: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filter markets for trading by liquidity and category.
        Uses MIN_MARKET_LIQUIDITY from settings if min_liquidity not provided.
        """
        if not markets:
            return []
        min_liq = min_liquidity if min_liquidity is not None else getattr(settings, "MIN_MARKET_LIQUIDITY", 1000)
        filtered = []
        for m in markets:
            liq = m.get("liquidity") or m.get("volume") or 0
            try:
                liq = float(liq)
            except (ValueError, TypeError):
                liq = 0
            if liq < min_liq:
                continue
            if categories:
                cat = (m.get("category") or "").lower()
                if cat not in [c.lower() for c in categories]:
                    continue
            filtered.append(m)
        return filtered
    
    async def get_market(self, market_id: str):
        return await self.client.get_market(market_id)
    
    async def get_predictions(
        self,
        market_id: str,
        token_id: str,
        price: float,
        user_address: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = await self.prediction_engine.predict(market_id, token_id, price, user_address, correlation_id=correlation_id)
        if self.calibration_tracker and result:
            pred = result.get("prediction") or result.get("confidence")
            if pred is not None:
                self.calibration_tracker.record_prediction(market_id, float(pred))
        return result
    
    def get_feature_importance(self) -> Dict[str, float]:
        """Return current feature importance scores from the prediction engine.
        Bots can use this to understand which features matter most."""
        if self.prediction_engine and hasattr(self.prediction_engine, '_feature_importance_scores'):
            return dict(self.prediction_engine._feature_importance_scores)
        return {}

    async def place_order(
        self,
        bot_name: str,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
        confidence: float,
        prediction: Optional[float] = None,
        order_type: str = "market",
        correlation_id: Optional[str] = None,
    ):
        # Single path: OrderGateway (kill switch, risk, liquidity, coordinator).
        # When SIMULATION_MODE=true it uses paper_trading; else execution_engine.
        if self.order_gateway is not None:
            return await self.order_gateway.place_order(
                bot_name, market_id, token_id, side, size, price, confidence,
                prediction=prediction, order_type=order_type, correlation_id=correlation_id,
            )
        raise RuntimeError("OrderGateway not initialized — cannot place orders without gateway")
    
    async def run_anomaly_detection_and_emit(self) -> List[Dict[str, Any]]:
        """Run anomaly detection and emit each anomaly to event_bus (-> webhooks). Returns list of anomalies."""
        if not self.anomaly_detector or not self.event_bus:
            return []
        async def _on_anomaly(payload):
            await self.event_bus.emit("anomaly_detected", payload)
        return await self.anomaly_detector.run_detection(on_anomaly=_on_anomaly)

    async def run_backtest(
        self,
        strategy_func,
        start_date,
        end_date,
        initial_capital: float = 10000.0,
        market_ids=None,
        data_source: str = "auto",
    ):
        return await self.backtest_engine.run_backtest(
            strategy_func, start_date, end_date, initial_capital,
            market_ids=market_ids,
            data_source=data_source,
        )
    
    async def run_simulation(self, market_id: str, token_id: str, price: float, iterations: int = None):
        return await self.simulation_engine.run_monte_carlo_simulation(
            market_id, token_id, price, iterations
        )
    
    async def simulate_portfolio(self, strategy_config: Dict, time_horizon_days: int = 30, iterations: int = None):
        return await self.simulation_engine.simulate_portfolio_strategy(
            strategy_config, time_horizon_days, iterations
        )
