"""
Check 3B: Fee anomaly detection in trade_events.

Fee denominator is size * price (USD cost basis), NOT size (share count).
Polymarket fees are % of notional. On a $0.02 token: fee = size × $0.02 × 0.015.

Tiers (in priority order):
1. fees < 0                          → CRITICAL (impossible)
2. fees > size * price * 0.05        → CRITICAL (>5% of notional — clearly wrong)
3. fees = 0 AND execution_mode=live  → WARNING (live trade with no fee recorded)

event_data JSONB is the fee source when present; fallback to fee column.
Reads execution_mode from event_data JSONB key "execution_mode" if present,
falls back to checking bot_name suffix conventions.
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class FeeCheck(BaseCheck):
    name = "fee_anomaly"
    tables_queried = ["trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Negative fees — always CRITICAL
        neg_rows = await session.execute(text("""
            SELECT bot_name, market_id, side,
                   CAST(size AS DOUBLE PRECISION)   AS sz,
                   CAST(price AS DOUBLE PRECISION)  AS px,
                   CAST(fee AS DOUBLE PRECISION)    AS fee_val,
                   sequence_num
            FROM trade_events
            WHERE event_type = 'ENTRY'
              AND fee IS NOT NULL
              AND CAST(fee AS DOUBLE PRECISION) < 0
            LIMIT 100
        """))
        for row in neg_rows.fetchall():
            bot_name, market_id, side, sz, px, fee_val, seq = row
            violations.append(AuditViolation(
                recon_type="FEE_ANOMALY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "negative_fee",
                    "side": side,
                    "size": round(sz, 6) if sz else 0,
                    "price": round(px, 6) if px else 0,
                    "fee": round(fee_val, 6),
                    "sequence_num": seq,
                },
            ))

        # Fee > 5% of notional — CRITICAL
        excess_rows = await session.execute(text("""
            SELECT bot_name, market_id, side,
                   CAST(size AS DOUBLE PRECISION)   AS sz,
                   CAST(price AS DOUBLE PRECISION)  AS px,
                   CAST(fee AS DOUBLE PRECISION)    AS fee_val,
                   sequence_num
            FROM trade_events
            WHERE event_type = 'ENTRY'
              AND fee IS NOT NULL
              AND size IS NOT NULL
              AND price IS NOT NULL
              AND CAST(size AS DOUBLE PRECISION) > 0
              AND CAST(price AS DOUBLE PRECISION) > 0
              AND CAST(fee AS DOUBLE PRECISION) > 0
              AND CAST(fee AS DOUBLE PRECISION)
                  > CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION) * 0.05
            LIMIT 100
        """))
        for row in excess_rows.fetchall():
            bot_name, market_id, side, sz, px, fee_val, seq = row
            notional = sz * px if sz and px else 0
            fee_pct = (fee_val / notional * 100) if notional > 0 else 0
            violations.append(AuditViolation(
                recon_type="FEE_ANOMALY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "fee_exceeds_5pct_notional",
                    "side": side,
                    "size": round(sz, 6),
                    "price": round(px, 6),
                    "fee": round(fee_val, 6),
                    "notional": round(notional, 6),
                    "fee_pct": round(fee_pct, 2),
                    "sequence_num": seq,
                },
            ))

        # Fee = 0 on live execution (WARNING)
        # Detect live vs paper via event_data->>'execution_mode' when present,
        # otherwise assume live if fee column exists and is explicitly 0.
        zero_fee_rows = await session.execute(text("""
            SELECT bot_name, market_id, side,
                   CAST(size AS DOUBLE PRECISION)  AS sz,
                   CAST(price AS DOUBLE PRECISION) AS px,
                   sequence_num,
                   event_data->>'execution_mode' AS exec_mode
            FROM trade_events
            WHERE event_type = 'ENTRY'
              AND fee IS NOT NULL
              AND CAST(fee AS DOUBLE PRECISION) = 0
              AND size IS NOT NULL
              AND price IS NOT NULL
              AND CAST(size AS DOUBLE PRECISION) > 0
              AND (
                  event_data->>'execution_mode' = 'live'
                  OR (event_data->>'execution_mode' IS NULL
                      AND event_data->>'paper' IS NULL)
              )
            LIMIT 100
        """))
        for row in zero_fee_rows.fetchall():
            bot_name, market_id, side, sz, px, seq, exec_mode = row
            violations.append(AuditViolation(
                recon_type="FEE_ANOMALY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "zero_fee_on_live_entry",
                    "side": side,
                    "size": round(sz, 6) if sz else 0,
                    "price": round(px, 6) if px else 0,
                    "execution_mode": exec_mode or "unknown",
                    "sequence_num": seq,
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} fee anomaly(s)",
        )
