"""
build_audit_orchestrator() — registers all 21 checks in order.

SIGNAL_REQUIRED_BOTS: opt-in list for CRITICAL rogue-trade detection.
Starts empty — populate once you've verified that each bot reliably
writes trade_signals rows for every ENTRY.

TODO (2026-04-30): populate SIGNAL_REQUIRED_BOTS after confirming signal write
coverage per bot. Verify with:
  SELECT DISTINCT bot_name FROM trade_signals;
  vs
  SELECT DISTINCT bot_name FROM trade_events WHERE event_type='ENTRY';
"""
import os
from typing import List, TYPE_CHECKING

from structlog import get_logger

from base_engine.audit.orchestrator import AuditOrchestrator
from base_engine.audit.checks.size_invariant_check import SizeInvariantCheck
from base_engine.audit.checks.orphan_check import OrphanCheck
from base_engine.audit.checks.temporal_order_check import TemporalOrderCheck
from base_engine.audit.checks.duplicate_entry_check import DuplicateEntryCheck
from base_engine.audit.checks.pnl_math_check import PnlMathCheck
from base_engine.audit.checks.fee_check import FeeCheck
from base_engine.audit.checks.fk_integrity_check import FkIntegrityCheck
from base_engine.audit.checks.traded_markets_check import TradedMarketsCheck
from base_engine.audit.checks.resolution_consistency_check import ResolutionConsistencyCheck
from base_engine.audit.checks.position_trade_events_check import PositionTradeEventsCheck
from base_engine.audit.checks.paper_trade_check import PaperTradeCheck
from base_engine.audit.checks.stale_position_check import StalePositionCheck
from base_engine.audit.checks.shadow_fill_check import ShadowFillCheck
from base_engine.audit.checks.fill_analysis_check import FillAnalysisCheck
from base_engine.audit.checks.signal_execution_check import SignalExecutionCheck
from base_engine.audit.checks.prediction_accuracy_check import PredictionAccuracyCheck
from base_engine.audit.checks.dlq_check import DlqCheck
from base_engine.audit.checks.equity_snapshot_check import EquitySnapshotCheck
from base_engine.audit.checks.schema_drift_check import SchemaDriftCheck
from base_engine.audit.checks.price_integrity_check import PriceIntegrityCheck
from base_engine.audit.checks.bot_health_state_check import BotHealthStateCheck

if TYPE_CHECKING:
    from base_engine.data.database import Database

logger = get_logger(__name__)

# TODO: populate by 2026-04-30 after confirming signal write coverage per bot.
# Verify with: SELECT DISTINCT bot_name FROM trade_signals;
# vs: SELECT DISTINCT bot_name FROM trade_events WHERE event_type='ENTRY';
# Leave empty until coverage is confirmed — see plan section 0F.
SIGNAL_REQUIRED_BOTS: List[str] = []


def build_audit_orchestrator(db: "Database", alerting=None) -> AuditOrchestrator:
    """
    Build and return an AuditOrchestrator with all 21 checks registered.

    Reads SIGNAL_REQUIRED_BOTS from environment variable if set:
        SIGNAL_REQUIRED_BOTS=MirrorBot,WeatherBot,EsportsBot

    Falls back to the module-level SIGNAL_REQUIRED_BOTS list (default: empty).
    """
    env_bots_raw = os.getenv("SIGNAL_REQUIRED_BOTS", "")
    signal_required_bots: List[str] = (
        [b.strip() for b in env_bots_raw.split(",") if b.strip()]
        if env_bots_raw.strip()
        else SIGNAL_REQUIRED_BOTS
    )

    orchestrator = AuditOrchestrator(db=db, alerting=alerting)

    # Phase 2: Trade event integrity
    orchestrator.register_check(SizeInvariantCheck())
    orchestrator.register_check(OrphanCheck())
    orchestrator.register_check(TemporalOrderCheck())
    orchestrator.register_check(DuplicateEntryCheck())

    # Phase 3: P&L mathematical verification
    orchestrator.register_check(PnlMathCheck())
    orchestrator.register_check(FeeCheck())

    # Phase 4: Cross-table referential integrity
    orchestrator.register_check(FkIntegrityCheck())
    orchestrator.register_check(TradedMarketsCheck())
    orchestrator.register_check(ResolutionConsistencyCheck())

    # Phase 5: Position-level reconciliation + data pathway checks
    orchestrator.register_check(PositionTradeEventsCheck())
    orchestrator.register_check(PaperTradeCheck())
    orchestrator.register_check(StalePositionCheck())
    orchestrator.register_check(ShadowFillCheck())
    orchestrator.register_check(FillAnalysisCheck())
    orchestrator.register_check(SignalExecutionCheck(signal_required_bots=signal_required_bots))
    orchestrator.register_check(PredictionAccuracyCheck())

    # Phase 6: System health integrity
    orchestrator.register_check(DlqCheck())
    orchestrator.register_check(EquitySnapshotCheck())
    orchestrator.register_check(SchemaDriftCheck())
    orchestrator.register_check(PriceIntegrityCheck())
    orchestrator.register_check(BotHealthStateCheck())

    logger.info(
        "audit_orchestrator_built",
        checks=len(orchestrator._checks),
        signal_required_bots=signal_required_bots,
    )
    return orchestrator
