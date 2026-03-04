"""
Mempool Monitor for Polymarket

Monitors the Polygon mempool for pending Polymarket transactions.
Provides real-time alerts for:
- Pending trades
- Large orders
- Elite trader activity
- Market movements before they're confirmed
"""

import asyncio
from typing import Dict, List, Optional, Any, Callable, Set, Awaitable
from datetime import datetime, timezone
from collections import defaultdict
from structlog import get_logger
from base_engine.data.blockchain_client import BlockchainClient, EXCHANGE_CONTRACT, CONDITIONAL_TOKENS_CONTRACT
from config.settings import settings

logger = get_logger()

# Polymarket contract addresses to monitor
POLYMARKET_CONTRACTS = {
    "exchange": EXCHANGE_CONTRACT.lower(),
    "conditional_tokens": CONDITIONAL_TOKENS_CONTRACT.lower(),
}


class MempoolMonitor:
    """
    Monitor Polygon mempool for pending Polymarket transactions.
    
    Features:
    - Real-time pending transaction detection
    - Filter by contract addresses
    - Filter by trader addresses
    - Filter by market/token IDs
    - Large order detection
    - Transaction analysis
    """
    
    def __init__(self, blockchain_client: Optional[BlockchainClient] = None):
        self.blockchain_client = blockchain_client or BlockchainClient()
        self.running = False
        self.monitor_task: Optional[asyncio.Task] = None
        
        # Monitored addresses (traders, contracts)
        self.monitored_addresses: Set[str] = set()
        
        # Monitored contracts
        self.monitored_contracts: Set[str] = set(POLYMARKET_CONTRACTS.values())
        
        # Monitored markets/tokens
        self.monitored_markets: Set[str] = set()
        self.monitored_tokens: Set[str] = set()
        
        # Callbacks
        self.transaction_callbacks: List[Callable[[Dict], Awaitable[None]]] = []
        self.trade_callbacks: List[Callable[[Dict], Awaitable[None]]] = []
        self.large_order_callbacks: List[Callable[[Dict], Awaitable[None]]] = []
        
        # Transaction cache (to avoid duplicates)
        self.seen_transactions: Set[str] = set()
        self.max_cache_size = 10000
        
        # Configuration
        self.poll_interval = 2.0  # Poll mempool every 2 seconds
        self.min_value_usd = 100.0  # Minimum transaction value to alert (in USD)
        self.large_order_threshold_usd = 1000.0  # Threshold for "large order" alerts
    
    async def start(self):
        """Start mempool monitoring."""
        if self.running:
            logger.warning("Mempool monitor already running")
            return
        
        self.running = True
        
        # Ensure blockchain client is initialized
        await self.blockchain_client.ensure_client()
        
        # Start monitoring task
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Mempool monitor started", contracts=len(self.monitored_contracts))
    
    async def stop(self):
        """Stop mempool monitoring."""
        self.running = False
        
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Mempool monitor stopped")
    
    def add_monitored_address(self, address: str):
        """Add an address to monitor (trader or contract)."""
        address_lower = address.lower()
        self.monitored_addresses.add(address_lower)
        logger.debug(f"Added monitored address: {address_lower}")
    
    def remove_monitored_address(self, address: str):
        """Remove an address from monitoring."""
        address_lower = address.lower()
        self.monitored_addresses.discard(address_lower)
        logger.debug(f"Removed monitored address: {address_lower}")
    
    def add_monitored_market(self, market_id: str):
        """Add a market to monitor."""
        self.monitored_markets.add(market_id)
        logger.debug(f"Added monitored market: {market_id}")
    
    def add_monitored_token(self, token_id: str):
        """Add a token to monitor."""
        self.monitored_tokens.add(token_id)
        logger.debug(f"Added monitored token: {token_id}")
    
    def add_transaction_callback(self, callback: Callable[[Dict], Awaitable[None]]):
        """Add callback for all transactions."""
        self.transaction_callbacks.append(callback)
    
    def add_trade_callback(self, callback: Callable[[Dict], Awaitable[None]]):
        """Add callback for trade transactions."""
        self.trade_callbacks.append(callback)
    
    def add_large_order_callback(self, callback: Callable[[Dict], Awaitable[None]]):
        """Add callback for large orders."""
        self.large_order_callbacks.append(callback)
    
    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self.running:
            try:
                await self._check_pending_transactions()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in mempool monitor loop: {str(e)}", exc_info=True)
                await asyncio.sleep(self.poll_interval)
    
    async def _check_pending_transactions(self):
        """Check for pending transactions in mempool."""
        if not self.blockchain_client.w3:
            return
        
        try:
            # Get pending transactions from mempool
            # Note: Polygon RPC may not support pending transactions endpoint
            # We'll use a combination of methods:
            # 1. Subscribe to new blocks and check transactions
            # 2. Poll latest block for new transactions
            # 3. Use WebSocket if available for real-time updates
            
            # Get latest block
            latest_block = await self.blockchain_client.w3.eth.get_block('latest', full_transactions=True)
            
            if not latest_block or 'transactions' not in latest_block:
                return
            
            # Check transactions in latest block
            for tx in latest_block['transactions']:
                if isinstance(tx, dict):
                    await self._process_transaction(tx, pending=False)
            
            # Also check pending transactions if RPC supports it
            try:
                pending_txs = await self.blockchain_client.w3.eth.get_block('pending', full_transactions=True)
                if pending_txs and 'transactions' in pending_txs:
                    for tx in pending_txs['transactions']:
                        if isinstance(tx, dict):
                            await self._process_transaction(tx, pending=True)
            except Exception as e:
                # Pending block may not be supported, that's okay
                logger.debug(f"Pending block not available: {str(e)}")
        
        except Exception as e:
            logger.error(f"Error checking pending transactions: {str(e)}", exc_info=True)
    
    async def _process_transaction(self, tx: Dict[str, Any], pending: bool = False):
        """Process a transaction and trigger callbacks if relevant."""
        tx_hash = tx.get('hash')
        if not tx_hash:
            return
        
        # Convert hash to hex string if needed
        if hasattr(tx_hash, 'hex'):
            tx_hash_hex = tx_hash.hex()
        else:
            tx_hash_hex = str(tx_hash)
        
        # Skip if already seen
        if tx_hash_hex in self.seen_transactions:
            return
        
        # Add to seen cache
        self.seen_transactions.add(tx_hash_hex)
        if len(self.seen_transactions) > self.max_cache_size:
            # Remove oldest (simple FIFO - remove first item)
            oldest = next(iter(self.seen_transactions))
            self.seen_transactions.remove(oldest)
        
        # Check if transaction is relevant
        to_address = tx.get('to')
        from_address = tx.get('from')
        
        if not to_address and not from_address:
            return
        
        to_address_lower = to_address.lower() if to_address else None
        from_address_lower = from_address.lower() if from_address else None
        
        # Check if transaction involves monitored contracts
        is_polymarket_tx = False
        if to_address_lower in self.monitored_contracts:
            is_polymarket_tx = True
        elif from_address_lower in self.monitored_addresses:
            is_polymarket_tx = True
        elif to_address_lower in self.monitored_addresses:
            is_polymarket_tx = True
        
        if not is_polymarket_tx:
            return
        
        # Analyze transaction
        analysis = await self._analyze_transaction(tx, pending)
        
        if not analysis.get('is_polymarket'):
            return
        
        # Create transaction event
        tx_event = {
            "hash": tx_hash_hex,
            "from": from_address,
            "to": to_address,
            "value": tx.get('value', 0),
            "pending": pending,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "analysis": analysis
        }
        
        # Trigger callbacks
        for callback in self.transaction_callbacks:
            try:
                result = callback(tx_event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Transaction callback error: {str(e)}", exc_info=True)
        
        # Check if it's a trade
        if analysis.get('is_trade'):
            for callback in self.trade_callbacks:
                try:
                    result = callback(tx_event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Trade callback error: {str(e)}", exc_info=True)
        
        # Check if it's a large order
        if analysis.get('is_large_order'):
            for callback in self.large_order_callbacks:
                try:
                    result = callback(tx_event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Large order callback error: {str(e)}", exc_info=True)
    
    async def _analyze_transaction(self, tx: Dict[str, Any], pending: bool) -> Dict[str, Any]:
        """
        Analyze a transaction to determine if it's a Polymarket trade.
        
        Returns:
            Dictionary with analysis results
        """
        analysis = {
            "is_polymarket": False,
            "is_trade": False,
            "is_large_order": False,
            "contract_type": None,
            "estimated_value_usd": 0.0,
            "market_id": None,
            "token_id": None,
            "trader": None
        }
        
        to_address = tx.get('to')
        if not to_address:
            return analysis
        
        to_address_lower = to_address.lower()
        
        # Check if it's a Polymarket contract
        if to_address_lower == POLYMARKET_CONTRACTS["exchange"]:
            analysis["is_polymarket"] = True
            analysis["contract_type"] = "exchange"
            analysis["is_trade"] = True
        elif to_address_lower == POLYMARKET_CONTRACTS["conditional_tokens"]:
            analysis["is_polymarket"] = True
            analysis["contract_type"] = "conditional_tokens"
            # Could be trade, redemption, or other operation
            analysis["is_trade"] = True  # Assume trade for now
        
        # Extract trader address
        from_address = tx.get('from')
        if from_address:
            analysis["trader"] = from_address.lower()
        
        # Estimate transaction value
        value = tx.get('value', 0)
        if isinstance(value, (int, str)):
            try:
                value_wei = int(value)
                # Convert wei to USD (rough estimate: 1 ETH ≈ $2000, 1 MATIC ≈ $0.5)
                # For Polygon, value is in MATIC
                value_matic = value_wei / 1e18
                value_usd = value_matic * 0.5  # Rough estimate
                analysis["estimated_value_usd"] = value_usd
            except (ValueError, TypeError):
                pass
        
        # Check if it's a large order
        if analysis["estimated_value_usd"] >= self.large_order_threshold_usd:
            analysis["is_large_order"] = True
        
        # Try to decode transaction input to extract market/token info
        # This would require contract ABIs and more complex decoding
        # For now, we'll mark it as a potential trade if it's to a Polymarket contract
        
        return analysis
    
    async def get_pending_transactions(
        self,
        address: Optional[str] = None,
        contract: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get pending transactions for a specific address or contract.
        
        Args:
            address: Address to filter by (trader or contract)
            contract: Contract address to filter by
            
        Returns:
            List of pending transaction dictionaries
        """
        if not self.blockchain_client.w3:
            return []
        
        try:
            # Get pending block
            pending_block = await self.blockchain_client.w3.eth.get_block('pending', full_transactions=True)
            
            if not pending_block or 'transactions' not in pending_block:
                return []
            
            results = []
            address_lower = address.lower() if address else None
            contract_lower = contract.lower() if contract else None
            
            for tx in pending_block['transactions']:
                if isinstance(tx, dict):
                    tx_to = tx.get('to', '').lower() if tx.get('to') else None
                    tx_from = tx.get('from', '').lower() if tx.get('from') else None
                    
                    # Filter by address
                    if address_lower:
                        if tx_from != address_lower and tx_to != address_lower:
                            continue
                    
                    # Filter by contract
                    if contract_lower:
                        if tx_to != contract_lower:
                            continue
                    
                    results.append({
                        "hash": tx.get('hash'),
                        "from": tx.get('from'),
                        "to": tx.get('to'),
                        "value": tx.get('value', 0),
                        "pending": True,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
            
            return results
        
        except Exception as e:
            logger.error(f"Error getting pending transactions: {str(e)}", exc_info=True)
            return []
    
    def get_monitored_addresses(self) -> List[str]:
        """Get list of monitored addresses."""
        return list(self.monitored_addresses)
    
    def get_monitored_contracts(self) -> List[str]:
        """Get list of monitored contracts."""
        return list(self.monitored_contracts)
    
    def get_monitored_markets(self) -> List[str]:
        """Get list of monitored markets."""
        return list(self.monitored_markets)
