"""
Check 5C: Stale open positions — positions that should have been closed.

A position is stale if:
1. positions.size > 0 AND markets.resolved = TRUE (market resolved, position open)
   → CRITICAL: position should have been resolved/exited
2. positions.size > 0 AND markets.end_date_iso < NOW() - INTERVAL '24 hours'
   → WARNING: market expired more than 24h ago, position still open
3. positions.size > 0 AND markets.active = FALSE AND markets.resolved = FALSE
   → WARNING: market deactivated but not resolved, position open

These indicate the resolution backfill or exit logic missed a position.
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class StalePositionCheck(BaseCheck):
    name = "stale_open_position"
    tables_queried = ["positions", "markets"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Resolved market but position still open — CRITICAL
        resolved_stale = await session.execute(text("""
            SELECT p.bot_name, p.market_id, p.side,
                   CAST(p.size AS DOUBLE PRECISION) AS pos_size,
                   CAST(p.entry_price AS DOUBLE PRECISION) AS entry_px,
                   m.resolved_at
            FROM positions p
            JOIN markets m ON m.id = p.market_id
            WHERE CAST(p.size AS DOUBLE PRECISION) > 0
              AND m.resolved = TRUE
            LIMIT 200
        """))
        for row in resolved_stale.fetchall():
            bot_name, market_id, side, pos_size, entry_px, resolved_at = row
            violations.append(AuditViolation(
                recon_type="STALE_OPEN_POSITION",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "position_open_on_resolved_market",
                    "side": side,
                    "size": round(float(pos_size), 6),
                    "entry_price": round(float(entry_px), 6) if entry_px else None,
                    "market_resolved_at": str(resolved_at) if resolved_at else None,
                },
            ))

        # Market end_date passed >24h ago but position open — WARNING
        expired_stale = await session.execute(text("""
            SELECT p.bot_name, p.market_id, p.side,
                   CAST(p.size AS DOUBLE PRECISION) AS pos_size,
                   m.end_date_iso
            FROM positions p
            JOIN markets m ON m.id = p.market_id
            WHERE CAST(p.size AS DOUBLE PRECISION) > 0
              AND m.resolved = FALSE
              AND m.end_date_iso IS NOT NULL
              AND m.end_date_iso < NOW() - INTERVAL '24 hours'
            LIMIT 100
        """))
        for row in expired_stale.fetchall():
            bot_name, market_id, side, pos_size, end_date = row
            violations.append(AuditViolation(
                recon_type="STALE_OPEN_POSITION",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "position_open_on_expired_market",
                    "side": side,
                    "size": round(float(pos_size), 6),
                    "market_end_date": str(end_date) if end_date else None,
                },
            ))

        # Market inactive + unresolved but position open — WARNING
        inactive_stale = await session.execute(text("""
            SELECT p.bot_name, p.market_id, p.side,
                   CAST(p.size AS DOUBLE PRECISION) AS pos_size
            FROM positions p
            JOIN markets m ON m.id = p.market_id
            WHERE CAST(p.size AS DOUBLE PRECISION) > 0
              AND m.resolved = FALSE
              AND (m.active = FALSE OR m.accepting_orders = FALSE)
            LIMIT 100
        """))
        for row in inactive_stale.fetchall():
            bot_name, market_id, side, pos_size = row
            violations.append(AuditViolation(
                recon_type="STALE_OPEN_POSITION",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "position_open_on_inactive_market",
                    "side": side,
                    "size": round(float(pos_size), 6),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} stale open position(s)",
        )
