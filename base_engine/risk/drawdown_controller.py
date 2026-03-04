"""
Drawdown Controller
==================
Automatically reduces risk during losing streaks.
Prevents catastrophic losses.

QUICK WIN: ~50 lines of code, prevents major losses.
"""
from typing import Dict, Optional
from datetime import datetime, timedelta, timezone
from structlog import get_logger

logger = get_logger()


class DrawdownController:
    """
    Automatically reduces risk during drawdowns.
    Prevents catastrophic losses during losing streaks.
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize drawdown controller.
        
        Args:
            config: Optional config dict with:
                - max_daily_loss: Max daily loss percentage (default: 0.05 = 5%)
                - max_weekly_loss: Max weekly loss percentage (default: 0.15 = 15%)
                - cooldown_hours: Hours to wait after hitting limit (default: 24)
                - reduction_steps: List of position multipliers [0.5, 0.25, 0] (default: [0.5, 0.25, 0])
        """
        if config is None:
            config = {}
        
        self.max_daily_loss = config.get("max_daily_loss", 0.05)  # 5%
        self.max_weekly_loss = config.get("max_weekly_loss", 0.15)  # 15%
        self.cooldown_hours = config.get("cooldown_hours", 24)
        self.reduction_steps = config.get("reduction_steps", [0.5, 0.25, 0])
        
        self.starting_capital = config.get("starting_capital", 10000.0)
        self.last_reset = datetime.now(timezone.utc)
    
    async def check_drawdown_status(
        self,
        portfolio: Dict,
        current_pnl: Optional[float] = None
    ) -> Dict:
        """
        Check current drawdown and return trading restrictions.

        Args:
            portfolio: Portfolio dict with:
                - 'starting_capital' or 'total_value': Base capital
                - 'realized_pnl_today': Today's realized P&L (negative = losses)
                - 'realized_pnl_week': Rolling 7-day realized P&L (optional, falls back to daily)
            current_pnl: Optional override for daily P&L

        Returns:
            Dict with:
                - status: 'normal' | 'caution' | 'restricted' | 'halted'
                - position_multiplier: 0.0 to 1.0
                - drawdown: Current drawdown percentage
                - reason: Why restriction is active
                - action: Recommended action
                - resume_at: When trading can resume (if halted)
        """
        # Get starting capital
        starting_capital = portfolio.get("starting_capital") or portfolio.get("total_value") or self.starting_capital

        # Calculate P&L if not provided
        if current_pnl is None:
            current_pnl = portfolio.get("realized_pnl_today", 0.0)

        # M3 fix: Use separate weekly P&L if available, otherwise fall back to daily
        weekly_pnl = portfolio.get("realized_pnl_week", current_pnl)

        # Calculate drawdowns (positive = losing money)
        daily_drawdown = -current_pnl / starting_capital if starting_capital > 0 else 0.0
        weekly_drawdown = -weekly_pnl / starting_capital if starting_capital > 0 else 0.0
        
        # Determine restriction level
        if daily_drawdown > self.max_daily_loss:
            return {
                "status": "halted",
                "reason": "daily_loss_limit",
                "drawdown": daily_drawdown,
                "position_multiplier": 0.0,
                "resume_at": datetime.now(timezone.utc) + timedelta(hours=self.cooldown_hours),
                "action": "close_all_positions",
                "message": f"Daily loss limit exceeded: {daily_drawdown:.1%} > {self.max_daily_loss:.1%}"
            }
        
        elif weekly_drawdown > self.max_weekly_loss:
            return {
                "status": "restricted",
                "reason": "weekly_loss_limit",
                "drawdown": weekly_drawdown,
                "position_multiplier": 0.25,
                "action": "reduce_exposure",
                "message": f"Weekly loss limit exceeded: {weekly_drawdown:.1%} > {self.max_weekly_loss:.1%}"
            }
        
        elif daily_drawdown > self.max_daily_loss * 0.5:
            return {
                "status": "caution",
                "reason": "approaching_daily_limit",
                "drawdown": daily_drawdown,
                "position_multiplier": 0.5,
                "action": "reduce_new_positions",
                "message": f"Approaching daily limit: {daily_drawdown:.1%} > {self.max_daily_loss * 0.5:.1%}"
            }
        
        return {
            "status": "normal",
            "drawdown": max(daily_drawdown, weekly_drawdown),
            "position_multiplier": 1.0,
            "action": "none",
            "message": "No restrictions"
        }
    
    async def get_position_multiplier(self, portfolio: Dict) -> float:
        """
        Get current position size multiplier based on drawdown status.

        Returns:
            Multiplier (0.0 to 1.0)
        """
        status = await self.check_drawdown_status(portfolio)
        return status.get("position_multiplier", 1.0)
