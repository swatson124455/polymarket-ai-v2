"""
Check 5D: shadow_fills vs trade_events alignment.

correlation_id is shared between shadow_fills and trade_events — same parameter
passed from bot call site into both insert calls within _place_order_locked().
Idempotency guard reuses same ID on retries. 1:1 relationship is guaranteed.

Violation types (per plan):
1. shadow_fills.trade_executed=TRUE with no matching trade_events ENTRY on
   correlation_id → CRITICAL (money leak: trade appeared to execute, no event)
2. shadow_fills.trade_executed=FALSE but matching ENTRY exists → CRITICAL (rogue execution)
3. shadow_fills.resolved_at IS NOT NULL but shadow_pnl IS NULL → WARNING (P&L pipeline gap)
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class ShadowFillCheck(BaseCheck):
    name = "shadow_fill_mismatch"
    tables_queried = ["shadow_fills", "trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # trade_executed=TRUE but no matching ENTRY — CRITICAL
        executed_no_entry = await session.execute(text("""
            SELECT sf.bot_name, sf.market_id, sf.side,
                   sf.correlation_id,
                   CAST(sf.size AS DOUBLE PRECISION)  AS sz,
                   CAST(sf.price AS DOUBLE PRECISION) AS px,
                   sf.filled_at
            FROM shadow_fills sf
            WHERE sf.trade_executed = TRUE
              AND sf.correlation_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM trade_events te
                  WHERE te.correlation_id = sf.correlation_id
                    AND te.event_type = 'ENTRY'
              )
            LIMIT 200
        """))
        for row in executed_no_entry.fetchall():
            bot_name, market_id, side, corr_id, sz, px, filled_at = row
            violations.append(AuditViolation(
                recon_type="SHADOW_FILL_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "trade_executed_true_no_entry_event",
                    "side": side,
                    "correlation_id": str(corr_id),
                    "size": round(float(sz), 6) if sz else 0,
                    "price": round(float(px), 6) if px else 0,
                    "filled_at": str(filled_at) if filled_at else None,
                },
            ))

        # trade_executed=FALSE but ENTRY exists — CRITICAL (rogue execution)
        not_executed_has_entry = await session.execute(text("""
            SELECT sf.bot_name, sf.market_id, sf.side,
                   sf.correlation_id,
                   te.sequence_num
            FROM shadow_fills sf
            JOIN trade_events te
              ON te.correlation_id = sf.correlation_id
             AND te.event_type = 'ENTRY'
            WHERE sf.trade_executed = FALSE
              AND sf.correlation_id IS NOT NULL
            LIMIT 100
        """))
        for row in not_executed_has_entry.fetchall():
            bot_name, market_id, side, corr_id, seq = row
            violations.append(AuditViolation(
                recon_type="SHADOW_FILL_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "trade_executed_false_but_entry_event_exists",
                    "side": side,
                    "correlation_id": str(corr_id),
                    "entry_sequence_num": seq,
                },
            ))

        # resolved_at set but shadow_pnl NULL — WARNING (P&L pipeline broken)
        missing_pnl = await session.execute(text("""
            SELECT sf.bot_name, sf.market_id, sf.side,
                   sf.resolved_at, sf.correlation_id
            FROM shadow_fills sf
            WHERE sf.resolved_at IS NOT NULL
              AND sf.shadow_pnl IS NULL
            LIMIT 100
        """))
        for row in missing_pnl.fetchall():
            bot_name, market_id, side, resolved_at, corr_id = row
            violations.append(AuditViolation(
                recon_type="SHADOW_FILL_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "resolved_but_shadow_pnl_null",
                    "side": side,
                    "resolved_at": str(resolved_at) if resolved_at else None,
                    "correlation_id": str(corr_id) if corr_id else None,
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} shadow fill mismatch(es)",
        )
