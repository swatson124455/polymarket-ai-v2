"""
CLOB Adapter - Wraps py-clob-client-v2 for order placement and orderbook.
ExecutionEngine uses this when CLOB credentials are configured; otherwise falls back to PolymarketClient (httpx).

Polymarket migrated CLOB to V2 on 2026-04-28. V1 SDK (py-clob-client) is incompatible
and returns `order_version_mismatch` on every order placement. V2 SDK targets the
new Exchange contract at 0xE111180000d2663C0091e4f400237545B87B996B with EIP-712
domain version="2".
"""
import asyncio
from typing import Any, Dict, Optional
from structlog import get_logger
from config.settings import settings
import httpx

logger = get_logger()

_CLOB_CLIENT = None


def _get_clob_client():
    """Build ClobClient once when creds and key are available (sync, used from executor)."""
    global _CLOB_CLIENT
    if _CLOB_CLIENT is not None:
        return _CLOB_CLIENT
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds
    except ImportError:
        logger.debug("py-clob-client-v2 not installed; CLOB adapter disabled")
        return None
    host = (getattr(settings, "POLYMARKET_CLOB_API", None) or "").rstrip("/")
    key = (getattr(settings, "PRIVATE_KEY", None) or "").strip()
    if not key or not host:
        return None
    api_key = (getattr(settings, "CLOB_API_KEY", None) or "").strip()
    api_secret = (getattr(settings, "CLOB_SECRET", None) or "").strip()
    api_passphrase = (getattr(settings, "CLOB_PASSPHRASE", None) or "").strip()
    chain_id = getattr(settings, "POLYGON_CHAIN_ID", 137)
    funder = (getattr(settings, "DEPOSIT_WALLET_ADDRESS", None) or "").strip()
    if not api_key or not api_secret or not api_passphrase:
        logger.debug("CLOB_API_KEY/SECRET/PASSPHRASE not set; CLOB adapter disabled")
        return None
    try:
        creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
        # V2 deposit-wallet flow: signature_type=3 (POLY_1271) + funder=deposit_wallet.
        # Polymarket V2 (post 2026-04-28) rejects EOA-as-maker; orders must be attributed
        # to the deposit wallet. EOA still signs (via private key + EIP-1271 lookup).
        if funder:
            _CLOB_CLIENT = ClobClient(
                host=host,
                chain_id=chain_id,
                key=key,
                creds=creds,
                signature_type=3,
                funder=funder,
            )
            logger.info(
                "CLOB adapter initialized with py-clob-client-v2 (POLY_1271 deposit wallet flow)",
                funder=funder,
            )
        else:
            _CLOB_CLIENT = ClobClient(host=host, chain_id=chain_id, key=key, creds=creds)
            logger.warning(
                "CLOB adapter initialized WITHOUT DEPOSIT_WALLET_ADDRESS — V2 orders will be "
                "rejected with 'maker address not allowed'. Set DEPOSIT_WALLET_ADDRESS in .env "
                "to the Polymarket-provisioned deposit wallet address."
            )
        return _CLOB_CLIENT
    except Exception as e:
        logger.warning("Failed to build ClobClient: %s", e)
        return None


def _place_order_sync(market_id: str, token_id: str, side: str, size: float, price: float) -> Dict[str, Any]:
    """Sync place order via py-clob-client (run in executor)."""
    client = _get_clob_client()
    if not client:
        return {"success": False, "error": "CLOB client not configured"}
    try:
        from py_clob_client_v2.clob_types import OrderArgs
        side_upper = (side or "").upper()
        if side_upper not in ("BUY", "SELL"):
            if side_upper in ("YES", "NO"):
                side_upper = "BUY" if side_upper == "YES" else "SELL"
            else:
                return {"success": False, "error": f"Invalid side: {side}"}
        order_args = OrderArgs(
            token_id=token_id,
            price=float(price),
            size=float(size),
            side=side_upper,
        )
        result = client.create_and_post_order(order_args)
        if result is None:
            return {"success": False, "error": "create_and_post_order returned None"}
        order_id = result.get("orderID") or result.get("id") or result.get("order_id")
        return {
            "success": True,
            "order_id": order_id,
            "market_id": market_id,
            "side": side_upper,
            "size": size,
            "price": price,
        }
    except Exception as e:
        logger.warning("py-clob-client place_order failed: %s", e)
        return {"success": False, "error": str(e)}


def _get_order_book_sync(token_id: str) -> Dict[str, Any]:
    """Sync get order book via py-clob-client (run in executor). Returns dict with bids/asks for compatibility."""
    client = _get_clob_client()
    if not client:
        return {}
    try:
        book = client.get_order_book(token_id)
        if book is None:
            return {}
        bids = getattr(book, "bids", None) or []
        asks = getattr(book, "asks", None) or []

        def _level(level) -> Dict[str, Any]:
            if hasattr(level, "price") and hasattr(level, "size"):
                return {"price": getattr(level, "price"), "size": getattr(level, "size")}
            if isinstance(level, dict):
                return level
            return {"price": str(level), "size": ""}

        return {
            "bids": [_level(b) for b in bids],
            "asks": [_level(a) for a in asks],
        }
    except Exception as e:
        logger.debug("py-clob-client get_order_book failed: %s", e)
        return {}


def _cancel_order_sync(order_id: str) -> bool:
    """Sync cancel order via py-clob-client-v2 (run in executor).

    V2 SDK note: client.cancel() expects a SignedOrder object (with .orderID attr),
    not a string. For cancel-by-ID we use cancel_orders([order_id]) which returns
    {"canceled": [...ids...], "not_canceled": {...id: reason...}}.
    """
    client = _get_clob_client()
    if not client:
        return False
    try:
        result = client.cancel_orders([order_id])
        canceled = result.get("canceled", []) if isinstance(result, dict) else []
        if order_id in canceled:
            return True
        not_canceled = result.get("not_canceled", {}) if isinstance(result, dict) else {}
        logger.warning(
            "py-clob-client-v2 cancel returned but order not in canceled list",
            order_id=order_id,
            canceled=canceled,
            not_canceled=not_canceled,
        )
        return False
    except Exception as e:
        logger.warning("py-clob-client-v2 cancel_orders failed: %s (order_id=%s)", e, order_id)
        return False


class ClobAdapter:
    """
    Async CLOB adapter: uses AsyncClobClient (httpx, direct) when available,
    else falls back to py-clob-client in run_in_executor.
    """

    def __init__(self):
        self._async_client: Optional[Any] = None

    def _get_async_client(self) -> Optional[Any]:
        # S228 Bug 9: AsyncClobClient imports py_clob_client (V1) at file top,
        # then isinstance-checks the V2 ClobClient instance against the V1 class
        # — always False under V2 → _build_post_order_request returns None →
        # AsyncClobClient.place_order returns {success:False, error:'CLOB
        # client or request build failed'}. Surfaced S228 live flip #3 as a
        # retry storm of fake "Order placed" events (Bug 10 misclassified the
        # failure as success). Force the sync V2 path (_place_order_sync via
        # run_in_executor) until async_clob_client.py is ported to V2.
        return None

    @property
    def available(self) -> bool:
        return _get_clob_client() is not None

    async def place_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        price: float,
    ) -> Dict[str, Any]:
        """Place order via AsyncClobClient (async HTTP) or sync client in thread."""
        ac = self._get_async_client()
        if ac is not None:
            return await ac.place_order(market_id=market_id, token_id=token_id, side=side, size=size, price=price)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: _place_order_sync(market_id, token_id, side, size, price),
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order on the CLOB. Returns True if cancelled successfully."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _cancel_order_sync(order_id))

    async def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """Get order book via AsyncClobClient or sync client in thread."""
        ac = self._get_async_client()
        if ac is not None:
            return await ac.get_order_book(token_id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _get_order_book_sync(token_id))


async def check_usdc_balance(
    wallet_address: Optional[str] = None,
    rpc_url: Optional[str] = None,
) -> Optional[float]:
    """S217: Query wallet USDC.e balance via Polygon JSON-RPC.

    USDC.e (the bridged variant used by Polymarket markets) at the canonical
    Polygon contract. Returns balance in USD (float), or None when RPC/wallet
    config is missing or fails. Read-only — no signing required.

    Called by BotBankrollManager at startup and every 10 min to derive
    bot capital from actual on-chain wallet capacity (S217 root fix —
    replaces the BOT_BANKROLL_CONFIG `capital` fiction).
    """
    wallet = (wallet_address or getattr(settings, "WALLET_ADDRESS", None) or "").strip()
    rpc = (
        rpc_url
        or getattr(settings, "POLYGON_RPC", None)
        or getattr(settings, "POLYGON_RPC_URL", None)
        or ""
    ).strip()
    if not wallet or not rpc:
        logger.debug("usdc_balance_check_skipped: WALLET_ADDRESS or POLYGON_RPC not configured")
        return None
    # USDC.e contract address on Polygon (bridged, used by Polymarket)
    USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    # balanceOf(address) selector = 0x70a08231, address right-padded to 32 bytes
    data = "0x70a08231" + wallet.lower().replace("0x", "").rjust(64, "0")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(rpc, json={
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": USDC_E, "data": data}, "latest"],
                "id": 1,
            })
            resp.raise_for_status()
            j = resp.json()
        if "error" in j:
            logger.warning("usdc_balance_rpc_error", error=j["error"])
            return None
        result = j.get("result", "0x0")
        if not result or result == "0x":
            return 0.0
        # USDC.e has 6 decimals
        balance_usd = int(result, 16) / 10 ** 6
        return balance_usd
    except Exception as _e:
        logger.warning("usdc_balance_check_failed: %s", _e)
        return None


async def check_pusd_balance(
    wallet_address: Optional[str] = None,
    rpc_url: Optional[str] = None,
) -> Optional[float]:
    """S226: Query pUSD balance via Polygon JSON-RPC.

    pUSD (Polymarket USD) is the V2 collateral token held at the per-user
    deposit wallet provisioned by Polymarket's relayer (not the EOA).
    Under CLOB V2, this is the canonical buying-power source — EOA USDC.e
    via check_usdc_balance() is no longer authoritative.

    Defaults to settings.DEPOSIT_WALLET_ADDRESS when wallet_address is None.
    Returns balance in USD (float), or None when RPC/wallet config is
    missing or fails. Read-only — no signing required.
    """
    wallet = (wallet_address or getattr(settings, "DEPOSIT_WALLET_ADDRESS", None) or "").strip()
    rpc = (
        rpc_url
        or getattr(settings, "POLYGON_RPC", None)
        or getattr(settings, "POLYGON_RPC_URL", None)
        or ""
    ).strip()
    if not wallet or not rpc:
        logger.debug("pusd_balance_check_skipped: DEPOSIT_WALLET_ADDRESS or POLYGON_RPC not configured")
        return None
    # pUSD contract address on Polygon (Polymarket V2 collateral, 6 decimals — verified on-chain 2026-05-20)
    PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
    data = "0x70a08231" + wallet.lower().replace("0x", "").rjust(64, "0")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(rpc, json={
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": PUSD, "data": data}, "latest"],
                "id": 1,
            })
            resp.raise_for_status()
            j = resp.json()
        if "error" in j:
            logger.warning("pusd_balance_rpc_error", error=j["error"])
            return None
        result = j.get("result", "0x0")
        if not result or result == "0x":
            return 0.0
        balance_usd = int(result, 16) / 10 ** 6
        return balance_usd
    except Exception as _e:
        logger.warning("pusd_balance_check_failed: %s", _e)
        return None


async def check_matic_balance(
    threshold_matic: float = 1.0,
    discord_webhook: Optional[str] = None,
) -> Optional[float]:
    """P0.17: Query wallet MATIC balance via Polygon JSON-RPC.

    Fires logger.critical + Discord alert if balance < threshold_matic.
    Returns balance in MATIC, or None when RPC/wallet config is missing or fails.
    Called at startup (preflight) and every 10min via base_engine monitor loop.
    Only meaningful in live mode (SIMULATION_MODE=false); callers should gate.
    """
    wallet = (getattr(settings, "WALLET_ADDRESS", None) or "").strip()
    rpc = (
        getattr(settings, "POLYGON_RPC", None)
        or getattr(settings, "POLYGON_RPC_URL", None)
        or ""
    ).strip()

    if not wallet or not rpc:
        logger.debug("matic_balance_check_skipped: WALLET_ADDRESS or POLYGON_RPC not configured")
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(rpc, json={
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": [wallet, "latest"],
                "id": 1,
            })
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            logger.warning("matic_balance_rpc_error", error=data["error"])
            return None

        balance_matic = int(data.get("result", "0x0"), 16) / 10 ** 18

        if balance_matic < threshold_matic:
            logger.critical(
                "matic_balance_low",
                balance_matic=round(balance_matic, 4),
                threshold_matic=threshold_matic,
                wallet=wallet[:8] + "...",
            )
            if discord_webhook:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as dc:
                        await dc.post(discord_webhook, json={
                            "content": (
                                f"MATIC LOW on {wallet[:8]}...: "
                                f"{balance_matic:.4f} MATIC "
                                f"(threshold {threshold_matic}). "
                                "Trades may fail due to gas underflow."
                            )
                        })
                except Exception as _dw_err:
                    logger.debug("matic_discord_alert_failed: %s", _dw_err)
        else:
            logger.info(
                "matic_balance_ok",
                balance_matic=round(balance_matic, 4),
                threshold_matic=threshold_matic,
            )

        return balance_matic

    except Exception as _e:
        logger.warning("matic_balance_check_failed: %s", _e)
        return None
