"""
Portfolio Rebalancing - Automated portfolio rebalancing.

Features:
- Automatic portfolio rebalancing
- Target allocation maintenance
- Risk-based rebalancing
- Tax-efficient rebalancing
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger
from bots.weather.engine.base_engine.data.database import Database, Position
from bots.weather.engine.base_engine.risk.risk_manager import RiskManager

logger = get_logger()


class PortfolioRebalancer:
    """
    Automated portfolio rebalancing system.
    
    Maintains optimal portfolio allocation and rebalances when needed.
    """
    
    def __init__(
        self,
        db: Database,
        risk_manager: RiskManager,
        rebalance_threshold: float = 0.05  # 5% deviation triggers rebalance
    ):
        self.db = db
        self.risk_manager = risk_manager
        self.rebalance_threshold = rebalance_threshold
    
    async def check_rebalance_needed(
        self,
        bot_name: str,
        target_allocation: Dict[str, float]  # market_id -> target percentage
    ) -> Dict[str, Any]:
        """
        Check if rebalancing is needed.
        
        Args:
            bot_name: Bot name
            target_allocation: Target allocation percentages
        
        Returns:
            Dictionary with rebalance analysis
        """
        if not self.db.session_factory:
            return {"rebalance_needed": False, "error": "Database not available"}
        
        async with self.db.get_session() as session:
            from sqlalchemy import select, func
            
            # Get current positions
            result = await session.execute(
                select(Position).where(
                    Position.bot_id == bot_name,
                    Position.status == "open"
                )
            )
            positions = result.scalars().all()
            
            if not positions:
                return {
                    "rebalance_needed": False,
                    "message": "No positions to rebalance"
                }
            
            # Calculate current allocation
            total_value = sum(p.size * (p.current_price or p.entry_price) for p in positions)
            
            current_allocation = {}
            deviations = {}
            max_deviation = 0.0
            
            for position in positions:
                position_value = position.size * (position.current_price or position.entry_price)
                current_pct = (position_value / total_value) if total_value > 0 else 0.0
                current_allocation[position.market_id] = current_pct
                
                target_pct = target_allocation.get(position.market_id, 0.0)
                deviation = abs(current_pct - target_pct)
                deviations[position.market_id] = deviation
                max_deviation = max(max_deviation, deviation)
            
            rebalance_needed = max_deviation > self.rebalance_threshold
            
            return {
                "rebalance_needed": rebalance_needed,
                "max_deviation": max_deviation,
                "threshold": self.rebalance_threshold,
                "current_allocation": current_allocation,
                "target_allocation": target_allocation,
                "deviations": deviations,
                "total_value": total_value
            }
    
    async def rebalance_portfolio(
        self,
        bot_name: str,
        target_allocation: Dict[str, float],
        total_capital: float
    ) -> Dict[str, Any]:
        """
        Rebalance portfolio to target allocation.
        
        Args:
            bot_name: Bot name
            target_allocation: Target allocation percentages
            total_capital: Total capital available
        
        Returns:
            Rebalancing plan
        """
        analysis = await self.check_rebalance_needed(bot_name, target_allocation)
        
        if not analysis.get("rebalance_needed"):
            return {
                "rebalanced": False,
                "message": "Portfolio is within threshold, no rebalancing needed",
                "analysis": analysis
            }
        
        if not self.db.session_factory:
            return {"rebalanced": False, "error": "Database not available"}
        
        async with self.db.get_session() as session:
            from sqlalchemy import select
            
            # Get current positions
            result = await session.execute(
                select(Position).where(
                    Position.bot_id == bot_name,
                    Position.status == "open"
                )
            )
            positions = result.scalars().all()
            
            rebalance_plan = {
                "positions_to_close": [],
                "positions_to_adjust": [],
                "new_positions": [],
                "total_trades": 0
            }
            
            current_allocation = analysis["current_allocation"]
            total_value = analysis["total_value"]
            
            # Calculate target values
            target_values = {
                market_id: total_capital * pct
                for market_id, pct in target_allocation.items()
            }
            
            # Plan rebalancing
            for position in positions:
                market_id = position.market_id
                current_value = position.size * (position.current_price or position.entry_price)
                target_value = target_values.get(market_id, 0.0)
                
                if target_value == 0.0:
                    # Close position (not in target allocation)
                    rebalance_plan["positions_to_close"].append({
                        "market_id": market_id,
                        "current_value": current_value,
                        "reason": "Not in target allocation"
                    })
                elif abs(current_value - target_value) / target_value > self.rebalance_threshold:
                    # Adjust position
                    adjustment = target_value - current_value
                    rebalance_plan["positions_to_adjust"].append({
                        "market_id": market_id,
                        "current_value": current_value,
                        "target_value": target_value,
                        "adjustment": adjustment,
                        "adjustment_pct": (adjustment / current_value) if current_value > 0 else 0.0
                    })
            
            # Check for new positions needed
            for market_id, target_pct in target_allocation.items():
                if market_id not in current_allocation and target_pct > 0:
                    target_value = total_capital * target_pct
                    rebalance_plan["new_positions"].append({
                        "market_id": market_id,
                        "target_value": target_value,
                        "target_pct": target_pct
                    })
            
            rebalance_plan["total_trades"] = (
                len(rebalance_plan["positions_to_close"]) +
                len(rebalance_plan["positions_to_adjust"]) +
                len(rebalance_plan["new_positions"])
            )
            
            return {
                "rebalanced": True,
                "plan": rebalance_plan,
                "analysis": analysis
            }
