"""
Tax-Compliant Transaction Logger (P6-03).

Structured logging of every transaction for tax reporting.
Exports to CSV compatible with CoinTracker / Koinly format.

No specific IRS guidance on prediction market taxation exists.
Three competing frameworks: capital gains, gambling income, or ordinary income.
This logger captures all data needed for any framework.
"""
import csv
import io
from typing import Optional, Any, List, Dict
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()


class TaxLogger:
    """Log and export transactions for tax compliance."""

    def __init__(self, db: Optional[Any] = None):
        self.db = db

    async def log_transaction(
        self,
        market_id: str,
        side: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        gas_cost: float = 0.0,
        market_question: str = "",
    ) -> None:
        """Log a trade for tax purposes."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return

        cost_basis = quantity * price + fee + gas_cost
        net_proceeds = quantity * price - fee - gas_cost if side.upper() in ("SELL", "NO") else 0.0

        try:
            from sqlalchemy import text
            now = datetime.now(timezone.utc)
            async with self.db.get_session() as session:
                await session.execute(text("""
                    INSERT INTO tax_transactions
                    (tx_time, market_id, market_question, side, quantity, price, fee, gas_cost,
                     net_proceeds, cost_basis, created_at)
                    VALUES (:t, :mid, :q, :side, :qty, :price, :fee, :gas, :net, :cost, :t)
                """), {
                    "t": now, "mid": market_id, "q": market_question,
                    "side": side, "qty": quantity, "price": price,
                    "fee": fee, "gas": gas_cost, "net": net_proceeds, "cost": cost_basis,
                })
                await session.commit()
        except Exception as e:
            logger.debug("Tax log insert failed (table may not exist): %s", e)

    async def export_csv(self, year: Optional[int] = None) -> str:
        """
        Export transactions as CSV string (CoinTracker/Koinly compatible).
        If year specified, filter to that tax year.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return ""

        try:
            from sqlalchemy import text
            where = ""
            params: Dict[str, Any] = {}
            if year:
                where = "WHERE EXTRACT(YEAR FROM tx_time) = :yr"
                params["yr"] = year

            async with self.db.get_session() as session:
                r = await session.execute(text(f"""
                    SELECT tx_time, market_id, market_question, side, quantity, price,
                           fee, gas_cost, net_proceeds, cost_basis, realized_pnl
                    FROM tax_transactions
                    {where}
                    ORDER BY tx_time
                """), params)
                rows = r.fetchall()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "Date", "Market ID", "Description", "Side", "Quantity", "Price",
                "Fee", "Gas Cost", "Net Proceeds", "Cost Basis", "Realized P&L",
            ])
            for row in rows:
                writer.writerow([
                    row[0].isoformat() if row[0] else "",
                    row[1], row[2], row[3],
                    round(row[4], 6) if row[4] else "",
                    round(row[5], 6) if row[5] else "",
                    round(row[6], 4) if row[6] else "0",
                    round(row[7], 4) if row[7] else "0",
                    round(row[8], 2) if row[8] else "",
                    round(row[9], 2) if row[9] else "",
                    round(row[10], 2) if row[10] else "",
                ])

            return output.getvalue()
        except Exception as e:
            logger.debug("Tax CSV export failed: %s", e)
            return ""
