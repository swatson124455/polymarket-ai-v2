"""
Check 5E: fill_analysis data quality.

Checks:
1. adverse_move_30s math consistency: should be (price_30s - fill_price) * size
   Tolerance: $0.001. Skipped if price_30s IS NULL (incomplete record).
2. fill_price outside [0, 1] → CRITICAL (binary prediction market, price must be in [0,1])
3. price_300s IS NULL for rows older than 6h → WARNING (price pipeline gap)
4. price_30s outside [0, 1] → CRITICAL
"""
import time
from typing import List

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck


class FillAnalysisCheck(BaseCheck):
    name = "fill_analysis_inconsistency"
    tables_queried = ["fill_analysis", "market_prices"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # adverse_move_30s math check
        # fill_analysis has no size column; adverse_move_30s = price_30s - fill_price
        math_rows = await session.execute(text("""
            SELECT fa.source_bot, fa.market_id, fa.fill_side,
                   CAST(fa.fill_price     AS DOUBLE PRECISION) AS fill_px,
                   CAST(fa.price_30s      AS DOUBLE PRECISION) AS px_30s,
                   CAST(fa.adverse_move_30s AS DOUBLE PRECISION) AS adv_move,
                   fa.id AS fill_id
            FROM fill_analysis fa
            WHERE fa.price_30s IS NOT NULL
              AND fa.adverse_move_30s IS NOT NULL
              AND fa.fill_price IS NOT NULL
              AND ABS(
                  CAST(fa.adverse_move_30s AS DOUBLE PRECISION)
                  - (CAST(fa.price_30s AS DOUBLE PRECISION) - CAST(fa.fill_price AS DOUBLE PRECISION))
              ) > 0.001
            LIMIT 100
        """))
        for row in math_rows.fetchall():
            bot_name, market_id, side, fill_px, px_30s, adv_move, fill_id = row
            expected = (px_30s - fill_px) if px_30s is not None else None
            violations.append(AuditViolation(
                recon_type="FILL_ANALYSIS_INCONSISTENCY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "adverse_move_30s_math_error",
                    "side": side,
                    "fill_id": str(fill_id) if fill_id else None,
                    "fill_price": round(float(fill_px), 6),
                    "price_30s": round(float(px_30s), 6),
                    "reported_adverse_move": round(float(adv_move), 6),
                    "expected_adverse_move": round(float(expected), 6) if expected is not None else None,
                },
            ))

        # fill_price or price_30s outside [0, 1] — CRITICAL
        oob_rows = await session.execute(text("""
            SELECT fa.source_bot, fa.market_id, fa.fill_side,
                   CAST(fa.fill_price AS DOUBLE PRECISION) AS fill_px,
                   CAST(fa.price_30s  AS DOUBLE PRECISION) AS px_30s,
                   fa.id AS fill_id
            FROM fill_analysis fa
            WHERE fa.fill_price IS NOT NULL
              AND (
                  CAST(fa.fill_price AS DOUBLE PRECISION) < 0
                  OR CAST(fa.fill_price AS DOUBLE PRECISION) > 1
                  OR (fa.price_30s IS NOT NULL AND (
                      CAST(fa.price_30s AS DOUBLE PRECISION) < 0
                      OR CAST(fa.price_30s AS DOUBLE PRECISION) > 1
                  ))
              )
            LIMIT 100
        """))
        for row in oob_rows.fetchall():
            bot_name, market_id, side, fill_px, px_30s, fill_id = row
            violations.append(AuditViolation(
                recon_type="FILL_ANALYSIS_INCONSISTENCY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "price_out_of_range_0_1",
                    "side": side,
                    "fill_id": str(fill_id) if fill_id else None,
                    "fill_price": round(float(fill_px), 6) if fill_px is not None else None,
                    "price_30s": round(float(px_30s), 6) if px_30s is not None else None,
                },
            ))

        # price_300s NULL for rows older than 6h — WARNING (pipeline gap)
        stale_rows = await session.execute(text("""
            SELECT fa.source_bot, fa.market_id, COUNT(*) AS gap_count
            FROM fill_analysis fa
            WHERE fa.price_300s IS NULL
              AND fa.fill_time IS NOT NULL
              AND fa.fill_time < NOW() - INTERVAL '6 hours'
            GROUP BY fa.source_bot, fa.market_id
            HAVING COUNT(*) > 0
            ORDER BY gap_count DESC
            LIMIT 100
        """))
        for row in stale_rows.fetchall():
            bot_name, market_id, count = row
            violations.append(AuditViolation(
                recon_type="FILL_ANALYSIS_INCONSISTENCY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "price_300s_null_older_than_6h",
                    "gap_count": int(count),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} fill_analysis inconsistency(s)",
        )
