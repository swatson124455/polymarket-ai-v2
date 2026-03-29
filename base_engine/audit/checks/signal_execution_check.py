"""
Check 5F: Signal-to-execution alignment.

Two violation types with different severities:
1. trade_signals row fired but no ENTRY in trade_events within 5-minute window
   → WARNING (some bots legitimately filter signals; not all bots write trade_signals)
2. ENTRY in trade_events with no trade_signals row within 5-minute window
   → CRITICAL per bot if that bot is in SIGNAL_REQUIRED_BOTS; WARNING otherwise

SIGNAL_REQUIRED_BOTS is injected at registration time via factory.py.
Default: empty list — rogue-trade CRITICAL detection is disabled until a bot's
signal write coverage is verified. Every audit run logs a WARNING if the list
is empty so the disabled check stays visible.

Visibility contract (per plan 0F):
- If SIGNAL_REQUIRED_BOTS is empty, log WARNING "rogue trade detection disabled"
  once per run. This surfaces in structlog and health scheduler output.
"""
import time
from typing import List

from sqlalchemy import text
from structlog import get_logger

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck

logger = get_logger(__name__)


class SignalExecutionCheck(BaseCheck):
    name = "signal_trade_mismatch"
    tables_queried = ["trade_signals", "trade_events"]

    def __init__(self, signal_required_bots: List[str] = None):
        self._signal_required_bots: List[str] = signal_required_bots or []

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        if not self._signal_required_bots:
            logger.warning(
                "signal_required_bots_empty",
                msg=(
                    "SIGNAL_REQUIRED_BOTS is empty — rogue trade detection (CRITICAL) is "
                    "disabled for all bots. Set SIGNAL_REQUIRED_BOTS env var to enable. "
                    "See factory.py for bot coverage notes."
                ),
            )

        # Signal fired but no ENTRY within 5 minutes — WARNING
        signal_no_entry = await session.execute(text("""
            SELECT ts.bot_name, ts.market_id, ts.side,
                   ts.signal_time, ts.signal_type,
                   COUNT(*) AS unmatched_count
            FROM trade_signals ts
            WHERE NOT EXISTS (
                SELECT 1 FROM trade_events te
                WHERE te.bot_name  = ts.bot_name
                  AND te.market_id = ts.market_id
                  AND te.event_type = 'ENTRY'
                  AND te.event_time BETWEEN ts.signal_time AND ts.signal_time + INTERVAL '5 minutes'
            )
            GROUP BY ts.bot_name, ts.market_id, ts.side, ts.signal_time, ts.signal_type
            ORDER BY ts.signal_time DESC
            LIMIT 100
        """))
        for row in signal_no_entry.fetchall():
            bot_name, market_id, side, signal_time, signal_type, count = row
            violations.append(AuditViolation(
                recon_type="SIGNAL_TRADE_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "signal_fired_no_entry",
                    "side": side,
                    "signal_time": str(signal_time) if signal_time else None,
                    "signal_type": signal_type,
                    "unmatched_count": int(count),
                },
            ))

        # ENTRY with no signal within 5-minute window
        # Severity depends on whether bot is in SIGNAL_REQUIRED_BOTS
        entry_no_signal = await session.execute(text("""
            SELECT te.bot_name, te.market_id, te.side,
                   te.event_time, te.sequence_num
            FROM trade_events te
            WHERE te.event_type = 'ENTRY'
              AND NOT EXISTS (
                SELECT 1 FROM trade_signals ts
                WHERE ts.bot_name  = te.bot_name
                  AND ts.market_id = te.market_id
                  AND ts.signal_time BETWEEN te.event_time - INTERVAL '5 minutes' AND te.event_time
              )
            ORDER BY te.event_time DESC
            LIMIT 100
        """))
        for row in entry_no_signal.fetchall():
            bot_name, market_id, side, event_time, seq = row
            is_required = bot_name in self._signal_required_bots if bot_name else False
            severity = "CRITICAL" if is_required else "WARNING"
            violations.append(AuditViolation(
                recon_type="SIGNAL_TRADE_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity=severity,
                details={
                    "reason": "entry_event_no_prior_signal",
                    "side": side,
                    "event_time": str(event_time) if event_time else None,
                    "sequence_num": seq,
                    "signal_required": is_required,
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} signal-execution mismatch(es)",
        )
