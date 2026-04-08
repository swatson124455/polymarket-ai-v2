"""
Check 6D: Price integrity — binary market prices must sum near 1.0 and be in [0,1].

Severity tiers (thin market correction per plan):
- liquidity >= 100: WARNING at sum outside [0.90, 1.10]; CRITICAL at [0.80, 1.20]
- liquidity < 100:  INFO skipped (thin market violations stored at WARNING severity still)
  Actually per plan: INFO at sum outside [0.85, 1.15]; WARNING at [0.70, 1.30]
  Prices outside [0, 1]: CRITICAL at any liquidity level.

Prices are summed per (market_id) across YES and NO sides from market_prices table.
Only markets with exactly 2 price rows (YES + NO) are checked — multi-outcome markets
have different constraints and are excluded.
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class PriceIntegrityCheck(BaseCheck):
    name = "price_integrity"
    tables_queried = ["markets", "market_prices"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Prices outside [0, 1] — CRITICAL regardless of liquidity
        # S164: Use market_prices_latest (tiny table) instead of market_prices
        # (millions of rows) to avoid 120s timeout.
        oob_rows = await session.execute(text("""
            SELECT mpl.token_id, 'latest' AS side,
                   CAST(mpl.price AS DOUBLE PRECISION) AS px,
                   m.liquidity
            FROM market_prices_latest mpl
            LEFT JOIN markets m ON m.yes_token_id = mpl.token_id
                                OR m.no_token_id = mpl.token_id
            WHERE mpl.price IS NOT NULL
              AND (
                  CAST(mpl.price AS DOUBLE PRECISION) < 0
                  OR CAST(mpl.price AS DOUBLE PRECISION) > 1
              )
            LIMIT 100
        """))
        for row in oob_rows.fetchall():
            market_id, side, px, liquidity = row
            violations.append(AuditViolation(
                recon_type="PRICE_SUM_ANOMALY",
                bot_name="",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "price_out_of_range_0_1",
                    "side": side,
                    "price": round(float(px), 6),
                    "liquidity": float(liquidity) if liquidity else None,
                },
            ))

        # Price sum anomalies — binary markets (YES + NO token prices)
        # S164: Use markets.yes_price/no_price instead of scanning market_prices.
        # These are refreshed by ingestion every few minutes.
        sum_rows = await session.execute(text("""
            SELECT m.id AS market_id,
                   CAST(m.yes_price AS DOUBLE PRECISION) + CAST(m.no_price AS DOUBLE PRECISION) AS price_sum,
                   CAST(m.liquidity AS DOUBLE PRECISION) AS liquidity
            FROM markets m
            WHERE m.yes_price IS NOT NULL
              AND m.no_price IS NOT NULL
              AND m.active = TRUE
              AND m.resolved = FALSE
              AND (
                  -- Liquid markets: warn at [0.90, 1.10], critical at [0.80, 1.20]
                  (CAST(m.liquidity AS DOUBLE PRECISION) >= 100
                   AND (CAST(m.yes_price AS DOUBLE PRECISION) + CAST(m.no_price AS DOUBLE PRECISION) < 0.90
                        OR CAST(m.yes_price AS DOUBLE PRECISION) + CAST(m.no_price AS DOUBLE PRECISION) > 1.10))
                  OR
                  -- Thin markets: warn at [0.70, 1.30]
                  (CAST(m.liquidity AS DOUBLE PRECISION) < 100
                   AND (CAST(m.yes_price AS DOUBLE PRECISION) + CAST(m.no_price AS DOUBLE PRECISION) < 0.70
                        OR CAST(m.yes_price AS DOUBLE PRECISION) + CAST(m.no_price AS DOUBLE PRECISION) > 1.30))
              )
            LIMIT 200
        """))
        for row in sum_rows.fetchall():
            market_id, price_sum, liquidity = row
            liq = float(liquidity) if liquidity is not None else 0
            ps  = float(price_sum) if price_sum is not None else 0

            if liq >= 100:
                severity = "CRITICAL" if (ps < 0.80 or ps > 1.20) else "WARNING"
            else:
                severity = "WARNING"  # thin market: INFO tier → stored as WARNING

            violations.append(AuditViolation(
                recon_type="PRICE_SUM_ANOMALY",
                bot_name="",
                market_id=str(market_id) if market_id else None,
                severity=severity,
                details={
                    "reason": "binary_price_sum_anomaly",
                    "price_sum": round(ps, 4),
                    "liquidity": round(liq, 2),
                    "thin_market": liq < 100,
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} price integrity issue(s)",
        )
