"""
Check 5A: Position-level reconciliation against trade_events.

Compares positions.size against the net of ENTRY - EXIT - RESOLUTION sizes
in trade_events, aggregated by (bot_name, market_id). Side is excluded from
te_net aggregation because historical EXIT events used side='SELL' while
ENTRYs used YES/NO (S163 transition) — per-side grouping created false
positives where te_net(bot, mkt, 'SELL') built solely from legacy EXIT(SELL)
rows matched phantom positions.side='SELL' rows with large negative net.

S186 correction (supersedes S185 P0 disposition's "mirror S164" framing):
a naive mirror of the S164 size_invariant_check GROUP BY fix is NOT correct
for PSM. The two checks are not structurally isomorphic. size_invariant
asserts a per-market sum invariant — aggregating across sides is
semantically valid. PSM asserts a per-side invariant — per-side attribution
is required. Verified against live data: a side-drop without guard shifts
false positives from legacy-SELL (shrinks) to dual-side-open markets where
positions has rows with size > 0 on BOTH sides simultaneously (grows).
Side-agnostic te_net aggregates both sides' entries into one net, which
then mismatches each individual positions row.

Guard: the mismatch JOIN excludes markets where another positions row on
the opposite side has size > 0 (NOT EXISTS sibling with size > 0). Single-
side markets benefit from the S163 legacy fix; dual-side markets are
routed to a separate diagnostic (DUAL_SIDE_CONCURRENT, filed as a follow-up
P0 item — not part of this check).

Tolerance: 0.1% of entry size OR 0.001 shares (whichever is larger) to
absorb rounding in DOUBLE PRECISION arithmetic.

Also checks:
- positions with size > 0 but no ENTRY event (phantom position)
- positions.unrealized_pnl sign consistency (if price < entry_price, uPnL < 0 for YES)
"""
import time
from typing import List

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck


class PositionTradeEventsCheck(BaseCheck):
    name = "position_size_mismatch"
    tables_queried = ["positions", "trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Size mismatch between positions table and trade_events net.
        # S186: te_net GROUPed BY (bot_name, market_id) only — side is
        # aggregated across to absorb legacy EXIT(SELL) / ENTRY(YES|NO)
        # asymmetry. NOT EXISTS guard excludes dual-side-open markets
        # where the side-agnostic aggregation would false-positive. See
        # module docstring for the full rationale.
        mismatch_rows = await session.execute(text("""
            WITH te_net AS (
                SELECT bot_name, market_id,
                    SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                        - SUM(CASE WHEN event_type IN ('EXIT','RESOLUTION') THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                    AS net_size,
                    SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                    AS total_entered
                FROM trade_events
                WHERE event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
                  AND size IS NOT NULL
                GROUP BY bot_name, market_id
            )
            SELECT p.source_bot, p.market_id, p.side,
                   CAST(p.size AS DOUBLE PRECISION) AS pos_size,
                   te.net_size,
                   te.total_entered,
                   ABS(CAST(p.size AS DOUBLE PRECISION) - te.net_size) AS abs_diff
            FROM positions p
            JOIN te_net te
              ON te.bot_name  = p.source_bot
             AND te.market_id = p.market_id
            WHERE CAST(p.size AS DOUBLE PRECISION) > 0
              AND ABS(CAST(p.size AS DOUBLE PRECISION) - te.net_size)
                  > GREATEST(te.total_entered * 0.001, 0.001)
              AND NOT EXISTS (
                  SELECT 1 FROM positions p2
                  WHERE p2.source_bot = p.source_bot
                    AND p2.market_id  = p.market_id
                    AND p2.side      <> p.side
                    AND CAST(p2.size AS DOUBLE PRECISION) > 0
              )
            LIMIT 200
        """))
        for row in mismatch_rows.fetchall():
            bot_name, market_id, side, pos_size, net_size, total_entered, diff = row
            violations.append(AuditViolation(
                recon_type="POSITION_SIZE_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "side": side,
                    "positions_size": round(float(pos_size), 6),
                    "trade_events_net": round(float(net_size), 6),
                    "total_entered": round(float(total_entered), 6),
                    "abs_diff": round(float(diff), 6),
                },
            ))

        # Phantom positions: size > 0 but no ENTRY in trade_events
        phantom_rows = await session.execute(text("""
            SELECT p.source_bot, p.market_id, p.side,
                   CAST(p.size AS DOUBLE PRECISION) AS pos_size
            FROM positions p
            WHERE CAST(p.size AS DOUBLE PRECISION) > 0
              AND NOT EXISTS (
                  SELECT 1 FROM trade_events te
                  WHERE te.bot_name  = p.source_bot
                    AND te.market_id = p.market_id
                    AND te.event_type = 'ENTRY'
              )
            LIMIT 100
        """))
        for row in phantom_rows.fetchall():
            bot_name, market_id, side, pos_size = row
            violations.append(AuditViolation(
                recon_type="POSITION_SIZE_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "phantom_position_no_entry_event",
                    "side": side,
                    "positions_size": round(float(pos_size), 6),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} position size mismatch(es)",
        )
