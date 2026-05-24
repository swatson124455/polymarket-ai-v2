"""
Market Parser V2 - Properly parse Polymarket V2 market data.

Handles:
- clobTokenIds extraction (YES and NO tokens)
- outcomePrices parsing
- All edge cases and field name variations
"""
import json
from typing import Dict, Optional, List, Any
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()


class MarketParserV2:
    """
    Parse raw Gamma API market data into clean format.
    Properly extracts CLOB token IDs for V2 architecture.
    """
    
    @staticmethod
    def parse_market(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse a single market from Gamma API response.
        
        Args:
            raw: Raw market data from Gamma API
        
        Returns:
            Parsed market dictionary with token IDs, or None if parsing fails
        """
        try:
            # Extract basic fields
            market_id = raw.get("id") or raw.get("market_id") or raw.get("marketId")
            if not market_id:
                logger.warning("Market missing ID field")
                return None
            
            condition_id = raw.get("conditionId") or raw.get("condition_id") or raw.get("conditionID")
            
            # CRITICAL: Extract CLOB token IDs
            yes_token_id, no_token_id = MarketParserV2._extract_token_ids(raw)
            
            # Extract prices
            yes_price, no_price = MarketParserV2._extract_prices(raw)
            
            # Build parsed market
            parsed = {
                "id": str(market_id),
                "condition_id": str(condition_id) if condition_id else None,
                "question": raw.get("question", "Unknown"),
                "description": raw.get("description"),
                "category": raw.get("category"),
                "slug": raw.get("slug"),
                
                # V2 CLOB Token IDs (CRITICAL)
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
                
                # Prices
                "yes_price": yes_price,
                "no_price": no_price,
                "outcome_prices": json.dumps([yes_price, no_price]) if yes_price is not None and no_price is not None else None,
                
                # Market metrics
                "liquidity": MarketParserV2._safe_float(raw.get("liquidity"), 0.0),
                "volume": MarketParserV2._safe_float(raw.get("volume"), 0.0),
                "volume_24h": MarketParserV2._safe_float(
                    raw.get("volume24hr") or raw.get("volume_24h") or raw.get("volume24h"),
                    0.0
                ),
                
                # Status
                "active": raw.get("active", True),
                "closed": raw.get("closed", False),
                "end_date_iso": MarketParserV2._parse_datetime(raw.get("endDate") or raw.get("end_date")),

                # NegRisk defense: markets with negRisk=true + multiple outcomes have unsellable tokens
                "neg_risk": bool(raw.get("negRisk") or raw.get("neg_risk") or raw.get("negRiskAugmented") or False),
                "outcome_count": len(raw.get("outcomes", [])) if raw.get("outcomes") else 2,

                # Keep raw for debugging
                "raw_data": raw
            }
            
            # Log if missing token IDs (critical for price history)
            if not yes_token_id:
                q = (parsed.get("question") or "")[:50]
                q_safe = q.encode("ascii", "replace").decode("ascii")  # Windows cp1252-safe
                logger.warning(
                    "Market %s missing YES token ID",
                    market_id,
                    question=q_safe
                )
            
            return parsed
            
        except Exception as e:
            logger.error(f"Failed to parse market: {str(e)}", exc_info=True)
            logger.debug(f"Raw market data: {raw}")
            return None
    
    @staticmethod
    def _extract_token_ids(raw: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """
        Extract YES and NO token IDs from market data.
        
        Tries multiple field names and structures:
        - clobTokenIds (array)
        - clob_token_ids (array)
        - tokens (array of objects)
        - tokenIds (object with yes/no keys)
        
        Returns:
            Tuple of (yes_token_id, no_token_id)
        """
        yes_token_id = None
        no_token_id = None
        
        # Strategy 1: clobTokenIds (most common in V2)
        # Gamma API returns clobTokenIds as JSON string "[\"id1\", \"id2\"]", not array
        clob_token_ids = raw.get("clobTokenIds") or raw.get("clob_token_ids")
        if isinstance(clob_token_ids, str) and clob_token_ids.strip():
            try:
                parsed = json.loads(clob_token_ids)
                if isinstance(parsed, list):
                    clob_token_ids = parsed
            except (json.JSONDecodeError, TypeError):
                clob_token_ids = None

        if isinstance(clob_token_ids, list):
            if len(clob_token_ids) >= 2:
                yes_token_id = str(clob_token_ids[0]) if clob_token_ids[0] else None
                no_token_id = str(clob_token_ids[1]) if clob_token_ids[1] else None
            elif len(clob_token_ids) == 1:
                yes_token_id = str(clob_token_ids[0]) if clob_token_ids[0] else None
        
        # Strategy 2: tokens array (alternative structure)
        if not yes_token_id:
            tokens = raw.get("tokens", [])
            if isinstance(tokens, list):
                for token in tokens:
                    if isinstance(token, dict):
                        outcome = (token.get("outcome") or token.get("side") or "").upper()
                        token_id = (
                            token.get("tokenId") or 
                            token.get("token_id") or 
                            token.get("id") or
                            token.get("tokenId")
                        )
                        
                        if outcome in ["YES", "1", "TRUE"] and token_id:
                            yes_token_id = str(token_id)
                        elif outcome in ["NO", "0", "FALSE"] and token_id:
                            no_token_id = str(token_id)
                        elif not yes_token_id and token_id:  # First token is usually YES
                            yes_token_id = str(token_id)
                        elif not no_token_id and token_id:  # Second token is usually NO
                            no_token_id = str(token_id)
        
        # Strategy 3: tokenIds object (alternative structure)
        if not yes_token_id:
            token_ids = raw.get("tokenIds") or raw.get("token_ids")
            if isinstance(token_ids, dict):
                yes_token_id = str(token_ids.get("yes") or token_ids.get("YES") or token_ids.get("0")) or None
                no_token_id = str(token_ids.get("no") or token_ids.get("NO") or token_ids.get("1")) or None
        
        # Strategy 4: Check for single tokenId at root (legacy)
        if not yes_token_id:
            root_token_id = raw.get("tokenId") or raw.get("token_id")
            if root_token_id:
                yes_token_id = str(root_token_id)
        
        return yes_token_id, no_token_id
    
    @staticmethod
    def _extract_prices(raw: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
        """
        Extract YES and NO prices from market data.
        
        Returns:
            Tuple of (yes_price, no_price)
        """
        yes_price = None
        no_price = None
        
        # Strategy 1: outcomePrices array
        outcome_prices = raw.get("outcomePrices") or raw.get("outcome_prices")
        
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                outcome_prices = None
        
        if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
            try:
                yes_price = float(outcome_prices[0])
                no_price = float(outcome_prices[1])
            except (ValueError, TypeError):
                pass
        
        # Strategy 2: Individual price fields
        if yes_price is None:
            yes_price = MarketParserV2._safe_float(
                raw.get("yesPrice") or raw.get("yes_price") or raw.get("bestAsk") or raw.get("best_ask")
            )
        
        if no_price is None:
            no_price = MarketParserV2._safe_float(
                raw.get("noPrice") or raw.get("no_price") or raw.get("bestBid") or raw.get("best_bid")
            )
        
        # Strategy 3: Calculate from each other (YES + NO = 1.0)
        if yes_price is not None and no_price is None:
            no_price = 1.0 - yes_price
        elif no_price is not None and yes_price is None:
            yes_price = 1.0 - no_price
        
        return yes_price, no_price
    
    @staticmethod
    def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
        """Safely convert value to float."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        """Parse datetime from various formats."""
        from datetime import datetime, timezone
        
        if value is None:
            return None
        
        if isinstance(value, datetime):
            return value
        
        if isinstance(value, str):
            try:
                from dateutil.parser import parse
                dt = parse(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                pass
        
        return None
    
    @staticmethod
    def parse_markets(raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Parse list of markets, filtering out failures.
        
        Args:
            raw_list: List of raw market data from Gamma API
        
        Returns:
            List of parsed markets
        """
        parsed_markets = []
        for raw in raw_list:
            parsed = MarketParserV2.parse_market(raw)
            if parsed:
                parsed_markets.append(parsed)
        
        # Log statistics
        markets_with_tokens = sum(1 for m in parsed_markets if m.get("yes_token_id"))
        logger.info(
            f"Parsed {len(parsed_markets)}/{len(raw_list)} markets",
            markets_with_token_ids=markets_with_tokens
        )
        
        return parsed_markets
