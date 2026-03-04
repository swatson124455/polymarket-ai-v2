"""
Blockchain client for querying Polymarket trade events directly from Polygon blockchain.

This is Option 1 from HISTORICAL_PRICE_OPTIONS.md - the most foolproof approach.
Blockchain data is immutable and permanent, so historical prices are always available.
"""
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
# Fix for web3.py v7+ - Use AsyncWeb3 instead of Web3 with async modules
try:
    from web3 import AsyncWeb3
    ASYNC_WEB3_AVAILABLE = True
except ImportError:
    # Fallback for older versions - try Web3 with AsyncEth
    from web3 import Web3
    from web3.eth import AsyncEth
    ASYNC_WEB3_AVAILABLE = False
    AsyncWeb3 = None
# POA middleware - web3.py v7 uses ExtraDataToPOAMiddleware instead of geth_poa_middleware
# Note: Polygon is PoS (not PoA), so this middleware may not be strictly needed
# But some RPC endpoints might return extraData, so we'll use it if available
try:
    from web3.middleware import ExtraDataToPOAMiddleware
    POA_MIDDLEWARE_AVAILABLE = True
except ImportError:
    # Middleware not available - Polygon may not need it anyway
    POA_MIDDLEWARE_AVAILABLE = False
    ExtraDataToPOAMiddleware = None
from structlog import get_logger
from config.settings import settings

logger = get_logger()

# Fallback when env not set
POLYGON_RPC_DEFAULT = "https://polygon-rpc.com"

# ConditionalTokens contract address (from Polymarket docs)
CONDITIONAL_TOKENS_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# CTF Exchange contract address
EXCHANGE_CONTRACT = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# NegRiskAdapter contract address (for multi-outcome markets)
NEGRISK_ADAPTER_CONTRACT = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
# NegRiskExchange for NegRisk markets
NEGRISK_EXCHANGE_CONTRACT = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# Standard FPMM Trade event ABI (from Gnosis ConditionalTokens)
FPMM_TRADE_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "maker", "type": "address"},
        {"indexed": True, "name": "taker", "type": "address"},
        {"indexed": False, "name": "makerAmountFilled", "type": "uint256"},
        {"indexed": False, "name": "takerAmountFilled", "type": "uint256"},
        {"indexed": False, "name": "fee", "type": "uint256"},
    ],
    "name": "Trade",
    "type": "event"
}

# Exchange OrderFilled event ABI (for CTF Exchange trades)
ORDER_FILLED_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "orderHash", "type": "bytes32"},
        {"indexed": True, "name": "maker", "type": "address"},
        {"indexed": True, "name": "taker", "type": "address"},
        {"indexed": False, "name": "makerAssetId", "type": "uint256"},
        {"indexed": False, "name": "takerAssetId", "type": "uint256"},
        {"indexed": False, "name": "makerAmountFilled", "type": "uint256"},
        {"indexed": False, "name": "takerAmountFilled", "type": "uint256"},
        {"indexed": False, "name": "fee", "type": "uint256"},
    ],
    "name": "OrderFilled",
    "type": "event"
}


class BlockchainClient:
    """
    Client for querying Polymarket trade events directly from Polygon blockchain.
    
    This approach is foolproof because:
    - Blockchain data is immutable and permanent
    - No API dependencies or rate limits
    - Works for all markets (active or archived)
    - Complete historical data available
    """
    
    def __init__(self, rpc_url: Optional[str] = None):
        from config.settings import settings
        self.rpc_url = (
            rpc_url
            or getattr(settings, "POLYGON_RPC", None)
            or getattr(settings, "QUICKNODE_HTTP", None)
            or POLYGON_RPC_DEFAULT
        )
        self._backup_urls = [
            u for u in [
                getattr(settings, "ALCHEMY_HTTP", None),
                getattr(settings, "BLASTAPI_HTTP", None),
            ] if u
        ]
        self.w3: Optional[Any] = None  # Can be AsyncWeb3 or Web3 depending on version
        
    async def ensure_client(self):
        """Initialize Web3 client if not already initialized."""
        if self.w3 is None:
            try:
                if ASYNC_WEB3_AVAILABLE and AsyncWeb3 is not None:
                    # Use AsyncWeb3 for web3.py v7+ (recommended approach)
                    self.w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self.rpc_url))
                else:
                    # Fallback for older web3.py versions (shouldn't happen with v7+)
                    # This is a compatibility fallback only
                    logger.warning("Using fallback Web3 initialization - AsyncWeb3 not available")
                    from web3 import Web3
                    from web3.eth import AsyncEth
                    # For older versions, try HTTPProvider (sync) - this won't work for async operations
                    # But we include it for completeness
                    try:
                        from web3.providers import HTTPProvider
                        self.w3 = Web3(HTTPProvider(self.rpc_url), modules={"eth": (AsyncEth,)})
                    except Exception as e:
                        raise ImportError(f"Unable to initialize Web3 client: {str(e)}. Please upgrade to web3.py v7+")
                # Inject POA middleware for Polygon compatibility (if available)
                # Note: Polygon is PoS (not PoA), but some RPC endpoints may return extraData
                # In web3.py v7, use ExtraDataToPOAMiddleware instead of geth_poa_middleware
                if POA_MIDDLEWARE_AVAILABLE and ExtraDataToPOAMiddleware is not None:
                    try:
                        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                        logger.debug("Injected ExtraDataToPOAMiddleware for Polygon compatibility")
                    except Exception as e:
                        logger.debug(f"Could not inject POA middleware (may not be needed for Polygon): {str(e)}")
                else:
                    logger.debug("POA middleware not available - Polygon (PoS) may not need it")
                
                # Verify connection - Web3.py v6+ compatibility
                # is_connected() was deprecated/removed in Web3.py v6+
                # Use get_block('latest') as connectivity test instead
                try:
                    await self.w3.eth.get_block('latest')
                    # Connection successful
                except Exception as conn_error:
                    # ASCII-encode error to avoid charmap codec crash on Windows stdout
                    _ce_safe = str(conn_error).encode("ascii", errors="replace").decode("ascii")
                    err_str = _ce_safe.lower()
                    if "429" in _ce_safe or "rate limit" in err_str or "rate_limit" in err_str:
                        logger.warning(
                            "RPC rate limit (QuickNode primary may be at capacity); "
                            "check QUICKNODE_HTTP, consider backup RPC or upgrade tier",
                            event="rpc_rate_limit",
                            rpc_url=self.rpc_url,
                        )
                    raise ConnectionError(
                        f"Failed to connect to Polygon RPC: {self.rpc_url}. "
                        f"Error: {_ce_safe}"
                    ) from conn_error

                logger.info("Blockchain client connected to Polygon", rpc_url=self.rpc_url)
            except Exception as e:
                # ASCII-encode to avoid charmap codec errors on Windows with non-ASCII RPC responses
                _e_safe = str(e).encode("ascii", errors="replace").decode("ascii")
                err_str = _e_safe.lower()
                if "429" in _e_safe or "rate limit" in err_str or "rate_limit" in err_str:
                    logger.warning(
                        "RPC rate limit - QuickNode primary may be at max; "
                        "set ALCHEMY_HTTP/BLASTAPI_HTTP as backup or wire Telegram alerting",
                        event="rpc_rate_limit",
                        rpc_url=self.rpc_url,
                    )
                logger.error("Failed to initialize blockchain client: %s", _e_safe)
                self.w3 = None
                raise
    
    async def get_block_number_from_timestamp(self, timestamp: int) -> int:
        """
        Get block number closest to a given timestamp.
        
        Args:
            timestamp: Unix timestamp
            
        Returns:
            Block number
        """
        await self.ensure_client()
        
        try:
            # Get latest block to estimate block time
            latest_block = await self.w3.eth.get_block('latest')
            latest_block_time = latest_block['timestamp']
            latest_block_number = latest_block['number']
            
            # Polygon block time is ~2 seconds
            block_time = 2
            
            # Estimate block number
            time_diff = timestamp - latest_block_time
            block_diff = int(time_diff / block_time)
            estimated_block = latest_block_number + block_diff
            
            # Clamp to reasonable range (Polygon started around block 0)
            estimated_block = max(0, estimated_block)
            
            logger.debug(
                "Estimated block number from timestamp",
                timestamp=timestamp,
                estimated_block=estimated_block,
                latest_block=latest_block_number
            )
            
            return estimated_block
        except Exception as e:
            logger.error(f"Failed to estimate block number: {str(e)}", exc_info=True)
            raise
    
    async def query_fpmm_trade_events(
        self,
        fpmm_contract_address: str,
        condition_id: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        from_timestamp: Optional[int] = None,
        to_timestamp: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Query Trade events from a FixedProductMarketMaker contract.
        
        Args:
            fpmm_contract_address: Address of the FPMM contract
            condition_id: Optional condition ID to filter by (if contract supports it)
            from_block: Starting block number
            to_block: Ending block number
            from_timestamp: Starting timestamp (will convert to block)
            to_timestamp: Ending timestamp (will convert to block)
            
        Returns:
            List of trade event dictionaries
        """
        await self.ensure_client()
        
        try:
            # Convert timestamps to block numbers if provided
            if from_timestamp and not from_block:
                from_block = await self.get_block_number_from_timestamp(from_timestamp)
            if to_timestamp and not to_block:
                to_block = await self.get_block_number_from_timestamp(to_timestamp)
            
            # Default to latest block if not specified
            if to_block is None:
                to_block = await self.w3.eth.get_block_number()
            if from_block is None:
                # Default to 30 days ago (roughly 1.3M blocks at 2s/block)
                from_block = max(0, to_block - 1_300_000)
            
            # Validate FPMM contract address format
            # Ethereum addresses are 40 hex chars (20 bytes), condition_ids are 64 hex chars (32 bytes)
            # Remove '0x' prefix for length check
            address_hex = fpmm_contract_address.replace('0x', '').replace('0X', '')
            
            if len(address_hex) != 40:
                raise ValueError(
                    f"Invalid FPMM contract address format: '{fpmm_contract_address}' "
                    f"(expected 40 hex chars, got {len(address_hex)}). "
                    f"This looks like a condition_id (64 hex chars) instead of an address. "
                    f"Use get_market_by_condition_id() to resolve condition_id to FPMM address first."
                )
            
            # Create contract instance with Trade event ABI
            # Use AsyncWeb3.to_checksum_address if available, otherwise Web3
            try:
                if ASYNC_WEB3_AVAILABLE and AsyncWeb3 is not None:
                    checksum_address = AsyncWeb3.to_checksum_address(fpmm_contract_address)
                else:
                    from web3 import Web3
                    checksum_address = Web3.to_checksum_address(fpmm_contract_address)
            except Exception as e:
                raise ValueError(
                    f"Invalid FPMM contract address: '{fpmm_contract_address}'. "
                    f"Error: {str(e)}. "
                    f"This may be a condition_id (64 hex chars) instead of an FPMM address (40 hex chars)."
                ) from e
            contract = self.w3.eth.contract(
                address=checksum_address,
                abi=[FPMM_TRADE_EVENT_ABI]
            )
            
            # Query Trade events
            logger.info(
                "Querying FPMM Trade events",
                contract=fpmm_contract_address,
                from_block=from_block,
                to_block=to_block
            )
            
            # Get events in batches to avoid timeout
            batch_size = 10000  # Process 10k blocks at a time
            all_events = []
            
            current_from = from_block
            while current_from <= to_block:
                current_to = min(current_from + batch_size, to_block)
                
                try:
                    events = await contract.events.Trade.get_logs(
                        fromBlock=current_from,
                        toBlock=current_to
                    )
                    
                    all_events.extend(events)
                    
                    logger.debug(
                        f"Fetched {len(events)} events from blocks {current_from}-{current_to}",
                        events_count=len(events),
                        total_events=len(all_events)
                    )
                except Exception as e:
                    logger.warning(
                        f"Error fetching events from blocks {current_from}-{current_to}: {str(e)}",
                        exc_info=True
                    )
                    # Continue with next batch
                
                current_from = current_to + 1
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.1)
            
            logger.info(
                f"Fetched {len(all_events)} total Trade events",
                contract=fpmm_contract_address,
                total_events=len(all_events)
            )
            
            return all_events
            
        except Exception as e:
            logger.error(
                f"Failed to query FPMM trade events: {str(e)}",
                contract=fpmm_contract_address,
                exc_info=True
            )
            raise
    
    async def query_exchange_order_filled_events(
        self,
        condition_id: Optional[str] = None,
        token_id: Optional[int] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        from_timestamp: Optional[int] = None,
        to_timestamp: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Query OrderFilled events from the CTF Exchange contract.
        
        This is useful for newer markets that use the Exchange contract.
        
        Args:
            condition_id: Optional condition ID (not directly filterable, but can filter results)
            token_id: Optional token ID to filter by (makerAssetId or takerAssetId)
            from_block: Starting block number
            to_block: Ending block number
            from_timestamp: Starting timestamp
            to_timestamp: Ending timestamp
            
        Returns:
            List of OrderFilled event dictionaries
        """
        await self.ensure_client()
        
        try:
            # Convert timestamps to block numbers if provided
            if from_timestamp and not from_block:
                from_block = await self.get_block_number_from_timestamp(from_timestamp)
            if to_timestamp and not to_block:
                to_block = await self.get_block_number_from_timestamp(to_timestamp)
            
            # Default to latest block if not specified
            if to_block is None:
                to_block = await self.w3.eth.get_block_number()
            if from_block is None:
                # Default to 30 days ago
                from_block = max(0, to_block - 1_300_000)
            
            # Create contract instance
            if ASYNC_WEB3_AVAILABLE and AsyncWeb3 is not None:
                checksum_address = AsyncWeb3.to_checksum_address(EXCHANGE_CONTRACT)
            else:
                from web3 import Web3
                checksum_address = Web3.to_checksum_address(EXCHANGE_CONTRACT)
            contract = self.w3.eth.contract(
                address=checksum_address,
                abi=[ORDER_FILLED_EVENT_ABI]
            )
            
            logger.info(
                "Querying Exchange OrderFilled events",
                from_block=from_block,
                to_block=to_block,
                token_id=token_id
            )
            
            # Query events in batches
            batch_size = 10000
            all_events = []
            
            current_from = from_block
            while current_from <= to_block:
                current_to = min(current_from + batch_size, to_block)
                
                try:
                    # Filter by token_id if provided (check both makerAssetId and takerAssetId)
                    argument_filters = {}
                    if token_id is not None:
                        # Note: Can't filter by both fields, so we'll filter in post-processing
                        pass
                    
                    events = await contract.events.OrderFilled.get_logs(
                        fromBlock=current_from,
                        toBlock=current_to,
                        argument_filters=argument_filters if argument_filters else None
                    )
                    
                    # Filter by token_id in post-processing if needed
                    if token_id is not None:
                        filtered_events = [
                            e for e in events
                            if e['args'].get('makerAssetId') == token_id or e['args'].get('takerAssetId') == token_id
                        ]
                        all_events.extend(filtered_events)
                    else:
                        all_events.extend(events)
                    
                    logger.debug(
                        f"Fetched {len(events)} events from blocks {current_from}-{current_to}",
                        events_count=len(events),
                        total_events=len(all_events)
                    )
                except Exception as e:
                    logger.warning(
                        f"Error fetching events from blocks {current_from}-{current_to}: {str(e)}",
                        exc_info=True
                    )
                
                current_from = current_to + 1
                await asyncio.sleep(0.1)
            
            logger.info(
                f"Fetched {len(all_events)} total OrderFilled events",
                total_events=len(all_events)
            )
            
            return all_events
            
        except Exception as e:
            logger.error(f"Failed to query Exchange OrderFilled events: {str(e)}", exc_info=True)
            raise
    
    def extract_price_from_trade_event(self, event: Dict[str, Any]) -> Optional[float]:
        """
        Extract price from a Trade event.
        
        Price = makerAmountFilled / takerAmountFilled
        
        Args:
            event: Trade event dictionary
            
        Returns:
            Price as float, or None if invalid
        """
        try:
            args = event.get('args', {})
            maker_amount = args.get('makerAmountFilled', 0)
            taker_amount = args.get('takerAmountFilled', 0)
            
            if taker_amount == 0:
                logger.warning("Trade event has zero takerAmountFilled", event=event)
                return None
            
            # Price is makerAmount / takerAmount
            # Both amounts are in wei (18 decimals for outcome tokens, 6 for USDC)
            # For simplicity, we'll use raw ratio (will need market-specific adjustment)
            price = float(maker_amount) / float(taker_amount)
            
            return price
            
        except Exception as e:
            logger.error(f"Failed to extract price from trade event: {str(e)}", exc_info=True)
            return None
    
    def extract_price_and_side_from_order_filled_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract price and order side from an OrderFilled event.
        
        This is MORE DETAILED than Trade events because:
        - Includes token IDs (makerAssetId, takerAssetId)
        - Can determine order side (BUY vs SELL)
        - More accurate price calculation
        
        Args:
            event: OrderFilled event dictionary
            
        Returns:
            Dict with 'price', 'side', 'makerAssetId', 'takerAssetId', or None if invalid
        """
        try:
            args = event.get('args', {})
            maker_amount = args.get('makerAmountFilled', 0)
            taker_amount = args.get('takerAmountFilled', 0)
            maker_asset_id = args.get('makerAssetId', 0)
            taker_asset_id = args.get('takerAssetId', 0)
            
            if taker_amount == 0 or maker_amount == 0:
                logger.warning("OrderFilled event has zero amounts", event=event)
                return None
            
            # USDC collateral token ID is 0
            # Outcome tokens have non-zero token IDs
            
            # Determine order side from asset IDs
            # In OrderFilled: maker provides makerAssetId, taker provides takerAssetId
            # If takerAssetId == 0: taker is paying USDC, receiving tokens = BUY
            # If makerAssetId == 0: maker is paying USDC, receiving tokens = BUY (from maker's perspective)
            # For price: we want price per outcome token in USDC terms
            
            if taker_asset_id == 0:
                # Taker is buying outcome tokens with USDC
                side = "BUY"
                # Price = USDC paid per token = takerAmountFilled (USDC) / makerAmountFilled (tokens)
                # Note: USDC has 6 decimals, tokens have 18 decimals
                # Raw ratio: taker_amount / maker_amount
                # Normalized: (taker_amount / 10^6) / (maker_amount / 10^18) = (taker_amount / maker_amount) * 10^12
                price = float(taker_amount) / float(maker_amount)
            elif maker_asset_id == 0:
                # Maker is buying outcome tokens with USDC (or taker is selling tokens)
                # From taker's perspective: selling tokens for USDC = SELL
                side = "SELL"
                # Price = USDC received per token = makerAmountFilled (USDC) / takerAmountFilled (tokens)
                price = float(maker_amount) / float(taker_amount)
            else:
                # Both are outcome tokens (token-to-token trade, rare)
                # Default to BUY and use ratio
                side = "BUY"
                price = float(taker_amount) / float(maker_amount)
            
            return {
                "price": price,
                "side": side,
                "makerAssetId": maker_asset_id,
                "takerAssetId": taker_asset_id,
                "makerAmount": maker_amount,
                "takerAmount": taker_amount
            }
            
        except Exception as e:
            logger.error(f"Failed to extract price from OrderFilled event: {str(e)}", exc_info=True)
            return None
    
    def extract_price_from_order_filled_event(self, event: Dict[str, Any]) -> Optional[float]:
        """
        Extract price from an OrderFilled event (legacy method for compatibility).
        
        Args:
            event: OrderFilled event dictionary
            
        Returns:
            Price as float, or None if invalid
        """
        result = self.extract_price_and_side_from_order_filled_event(event)
        return result.get("price") if result else None
    
    async def get_block_timestamp(self, block_number: int) -> int:
        """Get timestamp for a block number."""
        await self.ensure_client()
        
        try:
            block = await self.w3.eth.get_block(block_number)
            return block['timestamp']
        except Exception as e:
            logger.error(f"Failed to get block timestamp: {str(e)}", exc_info=True)
            raise
    
    async def check_market_resolution(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """
        Check if a market is resolved and get resolution outcome.
        
        Uses ConditionalTokens contract to check payoutNumerators.
        If any payoutNumerator is non-zero, market is resolved.
        
        Args:
            condition_id: Condition ID (bytes32) as hex string
            
        Returns:
            Dict with 'resolved' (bool), 'outcome' (str), 'payouts' (list), or None if error
        """
        await self.ensure_client()
        
        try:
            # ConditionalTokens contract ABI for payoutNumerators
            conditional_tokens_abi = [
                {
                    "constant": True,
                    "inputs": [
                        {"name": "conditionId", "type": "bytes32"},
                        {"name": "index", "type": "uint256"}
                    ],
                    "name": "payoutNumerators",
                    "outputs": [{"name": "", "type": "uint256"}],
                    "type": "function"
                },
                {
                    "constant": True,
                    "inputs": [{"name": "conditionId", "type": "bytes32"}],
                    "name": "payoutDenominator",
                    "outputs": [{"name": "", "type": "uint256"}],
                    "type": "function"
                }
            ]
            
            # ConditionalTokens contract ABI for payoutNumerators
            if ASYNC_WEB3_AVAILABLE and AsyncWeb3 is not None:
                checksum_address = AsyncWeb3.to_checksum_address(CONDITIONAL_TOKENS_CONTRACT)
            else:
                from web3 import Web3
                checksum_address = Web3.to_checksum_address(CONDITIONAL_TOKENS_CONTRACT)
            contract = self.w3.eth.contract(
                address=checksum_address,
                abi=conditional_tokens_abi
            )
            
            # Check payout denominator first
            payout_denominator = await contract.functions.payoutDenominator(
                bytes.fromhex(condition_id.replace('0x', ''))
            ).call()
            
            if payout_denominator == 0:
                # Market not resolved
                return {"resolved": False, "outcome": None, "payouts": []}
            
            # Check payout numerators for each outcome (typically 2 for binary markets)
            payouts = []
            for index in range(2):  # Check first 2 outcomes (YES/NO for binary markets)
                try:
                    numerator = await contract.functions.payoutNumerators(
                        bytes.fromhex(condition_id.replace('0x', '')),
                        index
                    ).call()
                    payouts.append(int(numerator))
                except Exception as e:
                    logger.debug(f"Error checking payout numerator {index}: {str(e)}")
                    break
            
            # Determine outcome
            if payouts and any(p > 0 for p in payouts):
                resolved = True
                # Find winning outcome (non-zero payout)
                winning_index = next((i for i, p in enumerate(payouts) if p > 0), None)
                if winning_index == 0:
                    outcome = "YES"
                elif winning_index == 1:
                    outcome = "NO"
                else:
                    outcome = f"OUTCOME_{winning_index}"
            else:
                resolved = False
                outcome = None
            
            return {
                "resolved": resolved,
                "outcome": outcome,
                "payouts": payouts,
                "payoutDenominator": int(payout_denominator)
            }
            
        except Exception as e:
            logger.warning(f"Failed to check market resolution: {str(e)}", exc_info=True)
            return None
