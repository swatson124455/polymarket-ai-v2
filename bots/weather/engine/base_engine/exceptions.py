"""
Custom Exception Classes
========================
Provides specific exception types for better error handling and debugging.
All exceptions include context information for easier troubleshooting.
"""
from typing import Optional, Dict, Any


class PolymarketError(Exception):
    """Base exception for all Polymarket-related errors."""
    
    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}
    
    def __str__(self) -> str:
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} (Context: {context_str})"
        return self.message


class DataIngestionError(PolymarketError):
    """Base exception for data ingestion errors."""
    pass


class MarketFetchError(DataIngestionError):
    """Error fetching markets from API."""
    
    def __init__(
        self, 
        message: str, 
        market_id: Optional[str] = None,
        api_endpoint: Optional[str] = None,
        status_code: Optional[int] = None,
        **kwargs
    ):
        context = {
            "market_id": market_id,
            "api_endpoint": api_endpoint,
            "status_code": status_code,
            **kwargs
        }
        super().__init__(message, context)


class PriceFetchError(DataIngestionError):
    """Error fetching price data."""
    
    def __init__(
        self,
        message: str,
        token_id: Optional[str] = None,
        market_id: Optional[str] = None,
        **kwargs
    ):
        context = {
            "token_id": token_id,
            "market_id": market_id,
            **kwargs
        }
        super().__init__(message, context)


class DatabaseError(PolymarketError):
    """Error saving to or reading from database."""
    
    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        table: Optional[str] = None,
        **kwargs
    ):
        context = {
            "operation": operation,
            "table": table,
            **kwargs
        }
        super().__init__(message, context)


class ValidationError(PolymarketError):
    """Data validation error."""
    
    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Optional[Any] = None,
        **kwargs
    ):
        context = {
            "field": field,
            "value": str(value) if value is not None else None,
            **kwargs
        }
        super().__init__(message, context)


class ExecutionError(PolymarketError):
    """Base exception for order execution errors."""
    pass


class OrderPlacementError(ExecutionError):
    """Error placing an order."""
    
    def __init__(
        self,
        message: str,
        market_id: Optional[str] = None,
        token_id: Optional[str] = None,
        side: Optional[str] = None,
        **kwargs
    ):
        context = {
            "market_id": market_id,
            "token_id": token_id,
            "side": side,
            **kwargs
        }
        super().__init__(message, context)


class RiskCheckError(ExecutionError):
    """Error during risk checks."""
    
    def __init__(
        self,
        message: str,
        check_type: Optional[str] = None,
        **kwargs
    ):
        context = {
            "check_type": check_type,
            **kwargs
        }
        super().__init__(message, context)


class ConfigurationError(PolymarketError):
    """Configuration or settings error."""
    pass


class NetworkError(PolymarketError):
    """Network/connection error."""
    
    def __init__(
        self,
        message: str,
        url: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs
    ):
        context = {
            "url": url,
            "timeout": timeout,
            **kwargs
        }
        super().__init__(message, context)
