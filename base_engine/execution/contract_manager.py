import asyncio
from typing import Optional, Dict
# Fix for web3.py v7+ - Use AsyncWeb3 instead of Web3 with async modules
try:
    from web3 import AsyncWeb3, Web3
    ASYNC_WEB3_AVAILABLE = True
except ImportError:
    # Fallback for older versions
    from web3 import Web3
    from web3.eth import AsyncEth
    ASYNC_WEB3_AVAILABLE = False
    AsyncWeb3 = None
from eth_account import Account
from structlog import get_logger
from config.settings import settings

logger = get_logger()

# Optional: cache approval status to skip on-chain check (Phase 1)
_approval_cache = None

def _get_approval_cache():
    global _approval_cache
    if _approval_cache is None:
        try:
            from base_engine.execution.approval_cache import ApprovalCache
            _approval_cache = ApprovalCache()
        except Exception as e:
            logger.debug("ApprovalCache not available: %s", e)
    return _approval_cache

POLYGON_RPC_DEFAULT = "https://polygon-rpc.com"
POLYMARKET_EXCHANGE_CONTRACT = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
USDCe_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDCe_DECIMALS = 6
OUTCOME_TOKEN_DECIMALS = 18
MAX_UINT256 = 2**256 - 1
MAX_RETRIES = 3
RETRY_DELAY = 2.0

def _is_valid_address(address: str) -> bool:
    if not address or not isinstance(address, str):
        return False
    try:
        return Web3.is_address(address)
    except Exception:
        return False

class ContractManager:
    def __init__(self, private_key: Optional[str] = None, rpc_url: Optional[str] = None):
        self.private_key = (private_key or "").strip() or settings.PRIVATE_KEY
        self.rpc_url = (
            rpc_url
            or getattr(settings, "POLYGON_RPC", None)
            or getattr(settings, "QUICKNODE_HTTP", None)
            or POLYGON_RPC_DEFAULT
        )
        self.w3 = None
        self.account = None
        
        if self.private_key:
            try:
                self.account = Account.from_key(self.private_key)
                logger.info("Contract manager initialized with wallet")
            except Exception as e:
                logger.error(f"Failed to initialize account from private key: {str(e)}")
                self.account = None
        else:
            logger.warning("No private key configured - contract manager in read-only mode")
    
    async def ensure_client(self):
        if self.w3 is None:
            try:
                if ASYNC_WEB3_AVAILABLE and AsyncWeb3 is not None:
                    # Use AsyncWeb3 for web3.py v7+ (recommended approach)
                    self.w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self.rpc_url))
                else:
                    # Fallback for older web3.py versions
                    from web3 import Web3
                    from web3.eth import AsyncEth
                    self.w3 = Web3(Web3.AsyncHTTPProvider(self.rpc_url), modules={"eth": (AsyncEth,)})
                # S216: Polygon is a PoA chain; extraData field exceeds 32 bytes.
                # Inject middleware so web3.py accepts the longer extraData and
                # bor-style RPC endpoints (e.g. polygon-bor-rpc.publicnode.com) work.
                try:
                    from web3.middleware import ExtraDataToPOAMiddleware
                    self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                except ImportError:
                    try:
                        # web3.py v6 fallback
                        from web3.middleware import geth_poa_middleware
                        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
                    except ImportError:
                        pass
                # Verify connection - Web3.py v6+ compatibility
                # is_connected() was deprecated/removed in Web3.py v6+
                # Use get_block('latest') as connectivity test instead
                try:
                    await self.w3.eth.get_block('latest')
                    # Connection successful
                except Exception as conn_error:
                    raise ConnectionError(
                        f"Failed to connect to Polygon RPC: {self.rpc_url}. "
                        f"Error: {str(conn_error)}"
                    ) from conn_error
            except Exception as e:
                logger.error(f"Failed to initialize Web3 client: {str(e)}")
                self.w3 = None
                raise
    
    def _to_wei(self, amount: float, decimals: int) -> int:
        if amount < 0:
            raise ValueError(f"Amount cannot be negative: {amount}")
        return int(amount * (10 ** decimals))
    
    def _from_wei(self, amount: int, decimals: int) -> float:
        return float(amount) / (10 ** decimals)
    
    async def check_allowance(
        self,
        token_address: str,
        owner_address: str,
        spender_address: str
    ) -> Dict:
        if not _is_valid_address(token_address):
            return {"success": False, "error": f"Invalid token address: {token_address}"}
        
        if not _is_valid_address(owner_address):
            return {"success": False, "error": f"Invalid owner address: {owner_address}"}
        
        if not _is_valid_address(spender_address):
            return {"success": False, "error": f"Invalid spender address: {spender_address}"}
        
        await self.ensure_client()
        
        for attempt in range(MAX_RETRIES):
            try:
                abi = [
                    {
                        "constant": True,
                        "inputs": [
                            {"name": "_owner", "type": "address"},
                            {"name": "_spender", "type": "address"}
                        ],
                        "name": "allowance",
                        "outputs": [{"name": "", "type": "uint256"}],
                        "type": "function"
                    }
                ]
                
                # Use AsyncWeb3 or Web3 depending on version
                if ASYNC_WEB3_AVAILABLE and AsyncWeb3 is not None:
                    checksum_token = AsyncWeb3.to_checksum_address(token_address)
                    checksum_owner = AsyncWeb3.to_checksum_address(owner_address)
                    checksum_spender = AsyncWeb3.to_checksum_address(spender_address)
                else:
                    from web3 import Web3
                    checksum_token = Web3.to_checksum_address(token_address)
                    checksum_owner = Web3.to_checksum_address(owner_address)
                    checksum_spender = Web3.to_checksum_address(spender_address)
                
                contract = self.w3.eth.contract(
                    address=checksum_token,
                    abi=abi
                )
                allowance = await contract.functions.allowance(
                    checksum_owner,
                    checksum_spender
                ).call()
                
                return {"success": True, "allowance": allowance}
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Allowance check failed (attempt {attempt + 1}/{MAX_RETRIES}): {str(e)}")
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                else:
                    logger.error(f"Failed to check allowance after {MAX_RETRIES} attempts: {str(e)}", exc_info=True)
                    return {"success": False, "error": str(e)}
        
        return {"success": False, "error": "Max retries exceeded"}
    
    async def approve_token(
        self,
        token_address: str,
        spender_address: str,
        amount: Optional[int] = None
    ) -> Dict:
        if not self.account:
            return {
                "success": False,
                "error": "No wallet configured"
            }
        
        if not _is_valid_address(token_address):
            return {"success": False, "error": f"Invalid token address: {token_address}"}
        
        if not _is_valid_address(spender_address):
            return {"success": False, "error": f"Invalid spender address: {spender_address}"}
        
        await self.ensure_client()
        
        try:
            if amount is None:
                amount = MAX_UINT256
            
            if amount < 0:
                return {"success": False, "error": "Amount cannot be negative"}
            
            abi = [
                {
                    "constant": False,
                    "inputs": [
                        {"name": "_spender", "type": "address"},
                        {"name": "_value", "type": "uint256"}
                    ],
                    "name": "approve",
                    "outputs": [{"name": "", "type": "bool"}],
                    "type": "function"
                }
            ]
            
            # Use AsyncWeb3 or Web3 depending on version
            if ASYNC_WEB3_AVAILABLE and AsyncWeb3 is not None:
                checksum_token = AsyncWeb3.to_checksum_address(token_address)
                checksum_spender = AsyncWeb3.to_checksum_address(spender_address)
            else:
                from web3 import Web3
                checksum_token = Web3.to_checksum_address(token_address)
                checksum_spender = Web3.to_checksum_address(spender_address)
            
            contract = self.w3.eth.contract(
                address=checksum_token,
                abi=abi
            )
            
            nonce = await self.w3.eth.get_transaction_count(self.account.address)
            
            try:
                gas_price = await self.w3.eth.gas_price
            except Exception as e:
                logger.warning(f"Failed to get gas price, using default: {str(e)}")
                gas_price = 30_000_000_000
            
            transaction = contract.functions.approve(
                checksum_spender,
                amount
            ).build_transaction({
                "chainId": 137,
                "gas": 100000,
                "gasPrice": gas_price,
                "nonce": nonce,
                "from": self.account.address
            })
            
            signed_txn = self.account.sign_transaction(transaction)
            tx_hash = await self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
            
            receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
            
            if receipt.status == 1:
                logger.info(
                    f"Token approval successful: {token_address} -> {spender_address}",
                    token=token_address,
                    spender=spender_address,
                    tx_hash=tx_hash.hex()
                )
                return {
                    "success": True,
                    "tx_hash": tx_hash.hex(),
                    "token": token_address,
                    "spender": spender_address,
                    "amount": amount
                }
            else:
                logger.error(f"Token approval transaction failed: {tx_hash.hex()}")
                return {
                    "success": False,
                    "error": "Transaction failed",
                    "tx_hash": tx_hash.hex()
                }
        except Exception as e:
            logger.error(f"Token approval error: {str(e)}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    async def ensure_usdce_approved(self, amount_usd: Optional[float] = None) -> Dict:
        if not self.account:
            return {"success": False, "error": "No wallet configured"}
        
        if amount_usd is not None and amount_usd < 0:
            return {"success": False, "error": "Amount cannot be negative"}
        
        owner = self.account.address
        spender = POLYMARKET_EXCHANGE_CONTRACT
        
        if not _is_valid_address(owner) or not _is_valid_address(spender):
            return {"success": False, "error": "Invalid addresses"}
        
        cache = _get_approval_cache()
        if cache is not None:
            cached = await cache.is_approved(USDCe_CONTRACT, spender)
            if cached is True:
                logger.debug("USDCe approval cache HIT, skipping on-chain check")
                return {"success": True, "already_approved": True, "allowance_usd": None, "allowance_wei": None}
        
        try:
            allowance_result = await self.check_allowance(USDCe_CONTRACT, owner, spender)
            
            if not allowance_result.get("success"):
                return {"success": False, "error": f"Failed to check allowance: {allowance_result.get('error')}"}
            
            current_allowance_wei = allowance_result.get("allowance", 0)
            current_allowance_usd = self._from_wei(current_allowance_wei, USDCe_DECIMALS)
            
            if amount_usd is None:
                amount_wei = MAX_UINT256
            else:
                amount_wei = self._to_wei(amount_usd, USDCe_DECIMALS)
            
            if current_allowance_wei >= amount_wei:
                logger.debug(f"USDCe already approved: {current_allowance_usd:.2f} >= {amount_usd or 'MAX'}")
                if cache is not None:
                    await cache.set_approved(USDCe_CONTRACT, spender, True)
                return {
                    "success": True,
                    "already_approved": True,
                    "allowance_usd": current_allowance_usd,
                    "allowance_wei": current_allowance_wei
                }
            
            logger.info(f"USDCe approval needed: {current_allowance_usd:.2f} < {amount_usd or 'MAX'}")
            result = await self.approve_token(USDCe_CONTRACT, spender, amount_wei)
            if result.get("success") and cache is not None:
                await cache.set_approved(USDCe_CONTRACT, spender, True)
            return result
        except Exception as e:
            logger.error(f"USDCe approval check failed: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    async def get_usdce_balance(self) -> Dict:
        """Query on-chain USDCe balance for the configured wallet.

        Returns:
            {"success": True, "balance_usd": float, "balance_wei": int}
            or {"success": False, "error": str}
        """
        if not self.account:
            return {"success": False, "error": "No wallet configured"}

        await self.ensure_client()

        for attempt in range(MAX_RETRIES):
            try:
                abi = [
                    {
                        "constant": True,
                        "inputs": [{"name": "_owner", "type": "address"}],
                        "name": "balanceOf",
                        "outputs": [{"name": "balance", "type": "uint256"}],
                        "type": "function"
                    }
                ]

                if ASYNC_WEB3_AVAILABLE and AsyncWeb3 is not None:
                    checksum_token = AsyncWeb3.to_checksum_address(USDCe_CONTRACT)
                    checksum_owner = AsyncWeb3.to_checksum_address(self.account.address)
                else:
                    checksum_token = Web3.to_checksum_address(USDCe_CONTRACT)
                    checksum_owner = Web3.to_checksum_address(self.account.address)

                contract = self.w3.eth.contract(address=checksum_token, abi=abi)
                balance_wei = await contract.functions.balanceOf(checksum_owner).call()

                return {
                    "success": True,
                    "balance_usd": self._from_wei(balance_wei, USDCe_DECIMALS),
                    "balance_wei": balance_wei,
                }
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning("Balance query failed (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                else:
                    logger.error("Failed to query balance after %d attempts: %s", MAX_RETRIES, e)
                    return {"success": False, "error": str(e)}

        return {"success": False, "error": "Max retries exceeded"}

    async def ensure_outcome_token_approved(
        self,
        token_address: str,
        amount_tokens: Optional[float] = None
    ) -> Dict:
        if not self.account:
            return {"success": False, "error": "No wallet configured"}
        
        if not _is_valid_address(token_address):
            return {"success": False, "error": f"Invalid token address: {token_address}"}
        
        if amount_tokens is not None and amount_tokens < 0:
            return {"success": False, "error": "Amount cannot be negative"}
        
        owner = self.account.address
        spender = POLYMARKET_EXCHANGE_CONTRACT
        
        if not _is_valid_address(owner) or not _is_valid_address(spender):
            return {"success": False, "error": "Invalid addresses"}
        
        cache = _get_approval_cache()
        if cache is not None:
            cached = await cache.is_approved(token_address, spender)
            if cached is True:
                logger.debug("Outcome token approval cache HIT, skipping on-chain check")
                return {"success": True, "already_approved": True, "allowance_tokens": None, "allowance_wei": None}
        
        try:
            allowance_result = await self.check_allowance(token_address, owner, spender)
            
            if not allowance_result.get("success"):
                return {"success": False, "error": f"Failed to check allowance: {allowance_result.get('error')}"}
            
            current_allowance_wei = allowance_result.get("allowance", 0)
            current_allowance_tokens = self._from_wei(current_allowance_wei, OUTCOME_TOKEN_DECIMALS)
            
            if amount_tokens is None:
                amount_wei = MAX_UINT256
            else:
                amount_wei = self._to_wei(amount_tokens, OUTCOME_TOKEN_DECIMALS)
            
            if current_allowance_wei >= amount_wei:
                logger.debug(f"Outcome token already approved: {current_allowance_tokens:.6f} >= {amount_tokens or 'MAX'}")
                if cache is not None:
                    await cache.set_approved(token_address, spender, True)
                return {
                    "success": True,
                    "already_approved": True,
                    "allowance_tokens": current_allowance_tokens,
                    "allowance_wei": current_allowance_wei
                }
            
            logger.info(f"Outcome token approval needed: {current_allowance_tokens:.6f} < {amount_tokens or 'MAX'}")
            result = await self.approve_token(token_address, spender, amount_wei)
            if result.get("success") and cache is not None:
                await cache.set_approved(token_address, spender, True)
            return result
        except Exception as e:
            logger.error(f"Outcome token approval check failed: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
