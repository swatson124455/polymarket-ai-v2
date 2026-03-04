"""
Dynamic Position Sizing - Kelly Criterion, volatility-adjusted, confidence-based sizing.

Provides:
- Kelly Criterion (full and fractional)
- Volatility-adjusted sizing
- Confidence-adjusted sizing
- Portfolio heat limits
- Market condition adjustments
"""
import math
from typing import Dict, Optional, Any
from structlog import get_logger
from base_engine.utils.validation import validate_price, validate_confidence, validate_numeric
from config.settings import settings

logger = get_logger()


class DynamicPositionSizing:
    """
    Advanced position sizing using multiple factors.
    
    Factors:
    - Confidence level (higher confidence = larger position)
    - Market volatility (lower volatility = larger position)
    - Portfolio exposure (diversification limits)
    - Kelly Criterion (optimal sizing)
    - Market conditions (regime adjustments)
    """
    
    def __init__(
        self,
        kelly_fraction: float = 0.25,  # Fractional Kelly (25% of full Kelly)
        max_position_pct: float = 0.10,  # Max 10% of capital per position
        min_position_pct: float = 0.01,  # Min 1% of capital
        volatility_adjustment: bool = True,
        confidence_adjustment: bool = True
    ):
        self.kelly_fraction = kelly_fraction
        self.max_position_pct = max_position_pct
        self.min_position_pct = min_position_pct
        self.volatility_adjustment = volatility_adjustment
        self.confidence_adjustment = confidence_adjustment
    
    def calculate_kelly_size(
        self,
        win_probability: float,
        odds: float,  # Price (0-1)
        bankroll: float,
        kelly_fraction: Optional[float] = None
    ) -> float:
        """
        Calculate position size using Kelly Criterion.
        
        Args:
            win_probability: Probability of winning (0-1)
            odds: Odds/price (0-1)
            bankroll: Total capital available
            kelly_fraction: Fraction of full Kelly to use (default: self.kelly_fraction)
        
        Returns:
            Optimal position size in dollars
        """
        win_probability = validate_confidence(win_probability, "win_probability")
        odds = validate_price(odds, "odds")
        bankroll = validate_numeric(bankroll, "bankroll", min_val=0.0)
        
        if kelly_fraction is None:
            kelly_fraction = self.kelly_fraction
        
        # Kelly formula: f = (p * b - q) / b
        # where:
        #   p = win probability
        #   q = loss probability (1 - p)
        #   b = net odds (if price is 0.6, you win 0.4/0.6 = 0.667 if you win, lose 1 if you lose)
        
        if odds <= 0 or odds >= 1:
            logger.warning(f"Invalid odds {odds}, using default size")
            return bankroll * self.min_position_pct
        
        # Net odds: if you bet $1 at price p, you get (1-p)/p back if you win
        net_odds = (1 - odds) / odds if odds > 0 else 0
        
        if net_odds <= 0:
            return bankroll * self.min_position_pct
        
        # Kelly fraction
        q = 1 - win_probability
        kelly_f = (win_probability * net_odds - q) / net_odds
        
        # Apply fractional Kelly
        fractional_kelly = kelly_f * kelly_fraction

        # A5: Meister boundary scale (arXiv:2412.14144) — prevents oversizing near extremes
        # Full Kelly at p=0.5 (odds=0.5), ~36% scale at p=0.1 or p=0.9.
        # Formula: scale = min(1.0, 4 * odds * (1 - odds))
        # This is the parabola 4p(1-p): equals 1 at p=0.5, equals 0 at p=0 and p=1.
        _boundary_scale = min(1.0, 4.0 * odds * (1.0 - odds))
        fractional_kelly = fractional_kelly * _boundary_scale

        # Ensure positive
        fractional_kelly = max(0.0, fractional_kelly)
        
        # Calculate position size
        position_size = bankroll * fractional_kelly
        
        # Apply limits
        position_size = max(bankroll * self.min_position_pct, position_size)
        position_size = min(bankroll * self.max_position_pct, position_size)
        
        return position_size
    
    def adjust_for_volatility(
        self,
        base_size: float,
        volatility: float,
        base_volatility: float = 0.1
    ) -> float:
        """
        Adjust position size based on volatility.
        
        Args:
            base_size: Base position size
            volatility: Current market volatility (0-1)
            base_volatility: Baseline volatility for comparison
        
        Returns:
            Adjusted position size
        """
        base_size = validate_numeric(base_size, "base_size", min_val=0.0)
        volatility = validate_numeric(volatility, "volatility", min_val=0.0, max_val=1.0)
        base_volatility = validate_numeric(base_volatility, "base_volatility", min_val=0.0)
        
        if base_volatility <= 0:
            return base_size
        
        # Higher volatility = smaller position
        # Lower volatility = larger position
        volatility_ratio = base_volatility / max(volatility, 0.01)  # Avoid division by zero
        
        # Cap adjustment between 0.5x and 1.5x
        adjustment = max(0.5, min(1.5, volatility_ratio))
        
        return base_size * adjustment
    
    def adjust_for_confidence(
        self,
        base_size: float,
        confidence: float,
        min_confidence: float = 0.5
    ) -> float:
        """
        Adjust position size based on confidence.
        
        Args:
            base_size: Base position size
            confidence: Confidence level (0-1)
            min_confidence: Minimum confidence threshold
        
        Returns:
            Adjusted position size
        """
        base_size = validate_numeric(base_size, "base_size", min_val=0.0)
        confidence = validate_confidence(confidence, "confidence")
        min_confidence = validate_confidence(min_confidence, "min_confidence")
        
        if confidence < min_confidence:
            # Reduce size significantly for low confidence
            multiplier = confidence / min_confidence * 0.5
        else:
            # Scale up for higher confidence
            multiplier = 0.5 + (confidence - min_confidence) / (1.0 - min_confidence) * 0.5
        
        return base_size * multiplier
    
    def calculate_optimal_size(
        self,
        win_probability: float,
        price: float,
        bankroll: float,
        volatility: Optional[float] = None,
        confidence: Optional[float] = None,
        portfolio_exposure: float = 0.0,
        max_portfolio_exposure: float = 0.5,
        prediction_interval_width: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Calculate optimal position size using all factors.

        Args:
            win_probability: Probability of winning
            price: Current price
            bankroll: Total capital
            volatility: Market volatility (optional)
            confidence: Confidence level (optional)
            portfolio_exposure: Current portfolio exposure (0-1)
            max_portfolio_exposure: Maximum portfolio exposure (0-1)
            prediction_interval_width: Prediction interval width (0-1, optional).
                When wide (>threshold), reduce position size.

        Returns:
            Dictionary with size and calculation details
        """
        # Start with Kelly Criterion
        kelly_size = self.calculate_kelly_size(
            win_probability=win_probability,
            odds=price,
            bankroll=bankroll
        )

        adjusted_size = kelly_size

        # Adjust for volatility
        if self.volatility_adjustment and volatility is not None:
            adjusted_size = self.adjust_for_volatility(adjusted_size, volatility)

        # Adjust for confidence
        if self.confidence_adjustment and confidence is not None:
            adjusted_size = self.adjust_for_confidence(adjusted_size, confidence)

        # Conformal prediction interval adjustment: wide intervals → reduce size
        conformal_multiplier = 1.0
        if prediction_interval_width is not None:
            try:
                from config.settings import settings as _s
                wide_threshold = getattr(_s, "CONFORMAL_WIDE_INTERVAL_THRESHOLD", 0.30)
                narrow_threshold = getattr(_s, "CONFORMAL_NARROW_INTERVAL_THRESHOLD", 0.10)
                wide_multiplier = getattr(_s, "CONFORMAL_WIDE_SIZE_MULTIPLIER", 0.50)
                if prediction_interval_width >= wide_threshold:
                    conformal_multiplier = wide_multiplier
                elif prediction_interval_width > narrow_threshold:
                    # Linear interpolation between narrow (1.0x) and wide (0.5x)
                    t = (prediction_interval_width - narrow_threshold) / max(wide_threshold - narrow_threshold, 0.01)
                    conformal_multiplier = 1.0 - t * (1.0 - wide_multiplier)
                # else: narrow interval, keep 1.0x
            except Exception:
                conformal_multiplier = 1.0
            adjusted_size *= conformal_multiplier

        # Check portfolio exposure limits
        available_capital = bankroll * (1 - portfolio_exposure)
        if portfolio_exposure >= max_portfolio_exposure:
            adjusted_size = 0.0
            logger.warning(f"Portfolio exposure {portfolio_exposure:.1%} at limit {max_portfolio_exposure:.1%}")
        else:
            # Ensure we don't exceed available capital
            adjusted_size = min(adjusted_size, available_capital)

        # Final limits
        adjusted_size = max(bankroll * self.min_position_pct, adjusted_size)
        adjusted_size = min(bankroll * self.max_position_pct, adjusted_size)

        return {
            "optimal_size": adjusted_size,
            "kelly_size": kelly_size,
            "size_as_pct_of_capital": adjusted_size / bankroll if bankroll > 0 else 0.0,
            "shares": adjusted_size / price if price > 0 else 0.0,
            "conformal_multiplier": conformal_multiplier,
            "calculation": {
                "win_probability": win_probability,
                "price": price,
                "volatility": volatility,
                "confidence": confidence,
                "portfolio_exposure": portfolio_exposure,
                "kelly_fraction": self.kelly_fraction,
                "prediction_interval_width": prediction_interval_width,
            }
        }
    
    def check_portfolio_heat(
        self,
        current_positions_value: float,
        new_position_value: float,
        bankroll: float,
        max_heat_pct: float = 0.10
    ) -> Dict[str, Any]:
        """
        Check portfolio heat (total risk exposure).
        
        Args:
            current_positions_value: Total value of current positions
            new_position_value: Value of new position
            bankroll: Total capital
            max_heat_pct: Maximum portfolio heat (0-1)
        
        Returns:
            Dictionary with heat check result
        """
        total_exposure = current_positions_value + new_position_value
        heat_pct = total_exposure / bankroll if bankroll > 0 else 0.0
        
        allowed = heat_pct <= max_heat_pct
        
        return {
            "allowed": allowed,
            "current_heat": current_positions_value / bankroll if bankroll > 0 else 0.0,
            "new_heat": heat_pct,
            "max_heat": max_heat_pct,
            "message": f"Portfolio heat {heat_pct:.1%} {'exceeds' if not allowed else 'within'} limit {max_heat_pct:.1%}"
        }
