"""
Validation utilities for numeric values, inputs, and data integrity.
Includes optional pre-insert validation for ingestion (market, price, trade dicts).
"""
import math
from typing import Optional, List, Any, Tuple


def validate_numeric(
    value: float, 
    name: str, 
    min_val: Optional[float] = None, 
    max_val: Optional[float] = None,
    allow_zero: bool = True
) -> float:
    """
    Validate numeric value is finite, within range, and optionally non-zero.
    
    Args:
        value: Value to validate
        name: Name for error messages
        min_val: Minimum allowed value (inclusive)
        max_val: Maximum allowed value (inclusive)
        allow_zero: If False, value must be > 0
    
    Returns:
        Validated value
    
    Raises:
        ValueError: If validation fails
    """
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric, got {type(value).__name__}")
    
    if math.isnan(value):
        raise ValueError(f"{name} is NaN (Not a Number)")
    
    if math.isinf(value):
        raise ValueError(f"{name} is Infinity")
    
    if not allow_zero and value == 0:
        raise ValueError(f"{name} cannot be zero")
    
    if min_val is not None and value < min_val:
        raise ValueError(f"{name} {value} below minimum {min_val}")
    
    if max_val is not None and value > max_val:
        raise ValueError(f"{name} {value} above maximum {max_val}")
    
    return float(value)


def validate_price(price: float, name: str = "price") -> float:
    """Validate price is between 0 and 1."""
    return validate_numeric(price, name, min_val=0.0, max_val=1.0, allow_zero=True)


def validate_confidence(confidence: float, name: str = "confidence") -> float:
    """Validate confidence is between 0 and 1."""
    return validate_numeric(confidence, name, min_val=0.0, max_val=1.0, allow_zero=True)


def validate_size(size: float, name: str = "size") -> float:
    """Validate size is positive."""
    return validate_numeric(size, name, min_val=0.0, allow_zero=False)


def validate_market_id(market_id: str) -> str:
    """Validate market ID format."""
    if not isinstance(market_id, str):
        raise ValueError(f"market_id must be string, got {type(market_id).__name__}")
    if not market_id or not market_id.strip():
        raise ValueError("market_id cannot be empty")
    if len(market_id) > 200:
        raise ValueError(f"market_id too long: {len(market_id)} characters")
    return market_id.strip()


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safely divide two numbers, returning default if denominator is zero.
    
    Args:
        numerator: Numerator
        denominator: Denominator
        default: Value to return if denominator is zero
    
    Returns:
        Result of division or default
    """
    if denominator == 0:
        return default
    result = numerator / denominator
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def validate_market_ids(market_ids: Optional[List[str]]) -> Optional[List[str]]:
    """Validate list of market IDs."""
    if market_ids is None:
        return None
    if not isinstance(market_ids, list):
        raise ValueError(f"market_ids must be list, got {type(market_ids).__name__}")
    return [validate_market_id(mid) for mid in market_ids]


def validate_market_dict(d: Any) -> Tuple[bool, str]:
    """
    Pre-insert validation for a market row. Used to skip bad rows instead of failing the batch.
    Returns (True, "") if valid, (False, reason) if invalid.
    """
    if not isinstance(d, dict):
        return (False, "not a dict")
    mid = d.get("id")
    if mid is None or (isinstance(mid, str) and not mid.strip()):
        return (False, "missing or empty id")
    if isinstance(mid, str) and len(mid.strip()) > 200:
        return (False, "id too long")
    return (True, "")


def validate_price_row(d: Any) -> Tuple[bool, str]:
    """
    Pre-insert validation for a price row. Price must be 0-1; market_id and token_id required.
    Returns (True, "") if valid, (False, reason) if invalid.
    """
    if not isinstance(d, dict):
        return (False, "not a dict")
    mid = d.get("market_id")
    if mid is None or str(mid).strip() == "":
        return (False, "missing market_id")
    tid = d.get("token_id")
    if tid is None or str(tid).strip() == "":
        return (False, "missing token_id")
    price = d.get("price")
    if price is not None:
        try:
            p = float(price)
            if math.isnan(p) or p < 0 or p > 1:
                return (False, f"price out of range 0-1: {price}")
        except (TypeError, ValueError):
            return (False, f"invalid price: {price}")
    return (True, "")


def validate_trade_dict(d: Any) -> Tuple[bool, str]:
    """
    Pre-insert validation for a trade row. id and market_id required; price 0-1; size >= 0.
    Returns (True, "") if valid, (False, reason) if invalid.
    """
    if not isinstance(d, dict):
        return (False, "not a dict")
    tid = d.get("id")
    if tid is None or (isinstance(tid, str) and not tid.strip()):
        return (False, "missing or empty id")
    mid = d.get("market_id")
    if mid is None or str(mid).strip() == "":
        return (False, "missing market_id")
    price = d.get("price")
    if price is not None:
        try:
            p = float(price)
            if math.isnan(p) or p < 0 or p > 1:
                return (False, f"price out of range 0-1: {price}")
        except (TypeError, ValueError):
            return (False, f"invalid price: {price}")
    size = d.get("size")
    if size is not None:
        try:
            s = float(size)
            if math.isnan(s) or s < 0:
                return (False, f"size must be >= 0: {size}")
        except (TypeError, ValueError):
            return (False, f"invalid size: {size}")
    return (True, "")
