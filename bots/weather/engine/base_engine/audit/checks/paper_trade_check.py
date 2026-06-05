"""
Check 5B: paper_trades vs trade_events alignment.

paper_trades is legacy compatibility storage. trade_events is P&L authority.
Each BUY paper_trade should correspond to an ENTRY trade_event (same market, bot, side).
Each SELL paper_trade should correspond to an EXIT trade_event.

Checks:
1. BUY paper_trade with no matching ENTRY in trade_events → WARNING
2. SELL paper_trade with no matching EXIT in trade_events → WARNING
3. paper_trade P&L (realized_pnl column) materially differs from trade_events realized_pnl
   for the same position closure → WARNING (> $0.10 difference)

Note: paper_trades.status='open' may have no SELL/EXIT — that's expected.
Only 'closed' or outcome-set rows are checked for exit alignment.
"""
import time
from typing import List

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck


class PaperTradeCheck(BaseCheck):
    name = "paper_trade_mismatch"
    tables_queried = ["paper_trades", "trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # BUY paper_trade with no ENTRY in trade_events
        buy_orphan = await session.execute(text("""
            SELECT pt.bot_name, pt.market_id, pt.side,
                   CAST(pt.size AS DOUBLE PRECISION) AS size,
                   pt.created_at
            FROM paper_trades pt
            WHERE LOWER(pt.side) = 'buy'
              AND NOT EXISTS (
                  SELECT 1 FROM trade_events te
                  WHERE te.bot_name  = pt.bot_name
                    AND te.market_id = pt.market_id
                    AND te.event_type = 'ENTRY'
              )
            LIMIT 100
        """))
        for row in buy_orphan.fetchall():
            bot_name, market_id, side, size, created_at = row
            violations.append(AuditViolation(
                recon_type="PAPER_TRADE_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "buy_paper_trade_no_entry_event",
                    "side": side,
                    "size": round(float(size), 6) if size else 0,
                    "created_at": str(created_at) if created_at else None,
                },
            ))

        # SELL paper_trade with no EXIT in trade_events
        sell_orphan = await session.execute(text("""
            SELECT pt.bot_name, pt.market_id, pt.side,
                   CAST(pt.size AS DOUBLE PRECISION) AS size,
                   CAST(pt.realized_pnl AS DOUBLE PRECISION) AS pnl,
                   pt.created_at
            FROM paper_trades pt
            WHERE LOWER(pt.side) = 'sell'
              AND NOT EXISTS (
                  SELECT 1 FROM trade_events te
                  WHERE te.bot_name  = pt.bot_name
                    AND te.market_id = pt.market_id
                    AND te.event_type = 'EXIT'
              )
            LIMIT 100
        """))
        for row in sell_orphan.fetchall():
            bot_name, market_id, side, size, pnl, created_at = row
            violations.append(AuditViolation(
                recon_type="PAPER_TRADE_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "sell_paper_trade_no_exit_event",
                    "side": side,
                    "size": round(float(size), 6) if size else 0,
                    "realized_pnl": round(float(pnl), 4) if pnl else 0,
                    "created_at": str(created_at) if created_at else None,
                },
            ))

        # P&L discrepancy > $0.10 between paper_trades and trade_events for closed positions
        pnl_diff = await session.execute(text("""
            WITH pt_pnl AS (
                SELECT bot_name, market_id,
                       SUM(CAST(realized_pnl AS DOUBLE PRECISION)) AS pt_total_pnl
                FROM paper_trades
                WHERE realized_pnl IS NOT NULL
                  AND LOWER(side) != 'buy'
                GROUP BY bot_name, market_id
            ),
            te_pnl AS (
                SELECT bot_name, market_id,
                       SUM(CAST(realized_pnl AS DOUBLE PRECISION)) AS te_total_pnl
                FROM trade_events
                WHERE event_type IN ('EXIT', 'RESOLUTION')
                  AND realized_pnl IS NOT NULL
                GROUP BY bot_name, market_id
            )
            SELECT p.bot_name, p.market_id,
                   p.pt_total_pnl, t.te_total_pnl,
                   ABS(p.pt_total_pnl - t.te_total_pnl) AS abs_diff
            FROM pt_pnl p
            JOIN te_pnl t ON t.bot_name = p.bot_name AND t.market_id = p.market_id
            WHERE ABS(p.pt_total_pnl - t.te_total_pnl) > 0.10
            ORDER BY abs_diff DESC
            LIMIT 100
        """))
        for row in pnl_diff.fetchall():
            bot_name, market_id, pt_pnl, te_pnl, diff = row
            violations.append(AuditViolation(
                recon_type="PAPER_TRADE_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "pnl_discrepancy_between_paper_trades_and_trade_events",
                    "paper_trades_pnl": round(float(pt_pnl), 4),
                    "trade_events_pnl": round(float(te_pnl), 4),
                    "abs_diff": round(float(diff), 4),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} paper trade mismatch(es)",
        )
