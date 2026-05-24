"""
Compliance Reporting (#47) - generate audit reports for trades.

Export trade history for tax/compliance; track trades with timestamps and PnL.
Extended (2026 roadmap): dual tax classification (Section 1256 + gambling + 90% deduction cap).
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from pathlib import Path
from structlog import get_logger

logger = get_logger()


class ComplianceReporter:
    """
    Generate compliance-ready trade reports (CSV, summary).
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db

    async def get_trades_for_period(
        self,
        bot_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 50000,
    ) -> List[Dict[str, Any]]:
        """Fetch trades in period for reporting."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return []
        from sqlalchemy import select
        from bots.weather.engine.base_engine.data.database import Trade
        async with self.db.get_session() as session:
            q = select(Trade).order_by(Trade.timestamp.asc()).limit(limit)
            if bot_id:
                q = q.where(Trade.bot_id == bot_id)
            if since:
                q = q.where(Trade.timestamp >= since)
            if until:
                q = q.where(Trade.timestamp <= until)
            result = await session.execute(q)
            rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "market_id": r.market_id,
                "user_address": r.user_address,
                "bot_id": r.bot_id,
                "side": r.side,
                "size": float(r.size) if r.size is not None else 0,
                "price": float(r.price) if r.price is not None else 0,
                "pnl": float(r.pnl) if r.pnl is not None else None,
                "entry_time": r.entry_time.isoformat() if r.entry_time else None,
                "exit_time": r.exit_time.isoformat() if r.exit_time else None,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]

    async def generate_audit_report(
        self,
        output_path: str = "compliance_audit.csv",
        bot_id: Optional[str] = None,
        since_days: int = 365,
        limit: int = 50000,
    ) -> Dict[str, Any]:
        """
        Export trade audit to CSV and return summary.
        """
        until = datetime.now(timezone.utc)
        since = until - timedelta(days=since_days)
        trades = await self.get_trades_for_period(bot_id=bot_id, since=since, until=until, limit=limit)
        if not trades:
            logger.warning("No trades for compliance report")
            return {"trades_count": 0, "output_path": output_path}
        import csv
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(trades)
        total_pnl = sum(t["pnl"] for t in trades if t.get("pnl") is not None)
        logger.info("Compliance report written: %s (%s trades)", output_path, len(trades))
        return {
            "trades_count": len(trades),
            "total_pnl": total_pnl,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "output_path": output_path,
        }

    # ── Dual Tax Classification (2026 roadmap) ──────────────────────────

    def compute_tax_classification(
        self,
        trades: List[Dict[str, Any]],
        tax_method: str = "section_1256",
    ) -> Dict[str, Any]:
        """
        Compute tax liability under dual classification.

        Section 1256:
          - 60% long-term capital gains (LTCG) + 40% short-term (STCG)
          - Applies to CFTC-regulated event contracts (Kalshi, ForecastEx)
          - Mark-to-market at year-end

        Gambling income:
          - Treated as ordinary income
          - Deductions capped at winnings (90% gambling loss deduction cap)
          - Applies to unregulated platforms (Polymarket)

        Args:
            trades: List of trade dicts with 'pnl', 'platform' fields.
            tax_method: "section_1256", "gambling", or "dual" (compute both).

        Returns:
            Tax computation summary.
        """
        # Separate by platform type
        regulated_trades = [t for t in trades if self._is_regulated_platform(t)]
        unregulated_trades = [t for t in trades if not self._is_regulated_platform(t)]

        result = {
            "method": tax_method,
            "total_trades": len(trades),
            "regulated_trades": len(regulated_trades),
            "unregulated_trades": len(unregulated_trades),
        }

        if tax_method in ("section_1256", "dual"):
            reg_pnl = sum(t.get("pnl", 0) or 0 for t in regulated_trades)
            ltcg = reg_pnl * 0.60 if reg_pnl > 0 else 0
            stcg = reg_pnl * 0.40 if reg_pnl > 0 else 0
            result["section_1256"] = {
                "total_pnl": reg_pnl,
                "long_term_gains_60pct": ltcg,
                "short_term_gains_40pct": stcg,
                "estimated_tax_ltcg": ltcg * 0.15,  # 15% LTCG rate
                "estimated_tax_stcg": stcg * 0.37,  # 37% top marginal
            }

        if tax_method in ("gambling", "dual"):
            wins = sum(t.get("pnl", 0) for t in unregulated_trades if (t.get("pnl") or 0) > 0)
            losses = abs(sum(t.get("pnl", 0) for t in unregulated_trades if (t.get("pnl") or 0) < 0))
            # Gambling losses deductible only up to winnings (90% cap)
            deductible_losses = min(losses, wins * 0.90)
            net_gambling_income = max(0, wins - deductible_losses)
            result["gambling"] = {
                "total_wins": wins,
                "total_losses": losses,
                "deductible_losses": deductible_losses,
                "net_gambling_income": net_gambling_income,
                "estimated_tax": net_gambling_income * 0.37,  # Ordinary income rate
            }

        return result

    def _is_regulated_platform(self, trade: Dict) -> bool:
        """Check if a trade was on a CFTC-regulated platform."""
        platform = (trade.get("platform") or trade.get("source_bot") or "").lower()
        return platform in ("kalshi", "forecastex", "coinbase")
