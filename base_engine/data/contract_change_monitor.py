"""
Contract change monitor — detect ConditionalTokens.sol proxy upgrades.

Polymarket uses upgradeable proxy contracts. If the implementation changes,
our ABI assumptions may break. This monitor polls the proxy's implementation
slot and alerts when a change is detected.
"""
from __future__ import annotations
import asyncio
from typing import Optional
from structlog import get_logger

logger = get_logger()

# EIP-1967 implementation slot: keccak256("eip1967.proxy.implementation") - 1
IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

# Known ConditionalTokens proxy on Polygon
DEFAULT_CT_PROXY = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"


class ContractChangeMonitor:
    """
    Monitors proxy contracts for implementation upgrades.

    Polls the EIP-1967 implementation storage slot periodically
    and emits an event if the implementation address changes.
    """

    def __init__(self, chain_provider=None, event_bus=None):
        self._chain = chain_provider
        self._event_bus = event_bus
        self._known_implementations: dict = {}  # proxy_address -> impl_address
        self._running = False

    async def start(self, proxy_addresses: Optional[list] = None, poll_interval: int = 3600):
        """Start monitoring. Default: check every hour."""
        self._running = True
        addresses = proxy_addresses or [DEFAULT_CT_PROXY]

        # Get initial implementations
        for addr in addresses:
            impl = await self._get_implementation(addr)
            if impl:
                self._known_implementations[addr] = impl
                logger.info("ContractChangeMonitor: tracking %s -> %s", addr, impl)

        while self._running:
            await asyncio.sleep(poll_interval)
            for addr in addresses:
                try:
                    current_impl = await self._get_implementation(addr)
                    if not current_impl:
                        continue
                    known = self._known_implementations.get(addr)
                    if known and current_impl != known:
                        logger.critical(
                            "CONTRACT UPGRADE DETECTED: %s changed from %s to %s",
                            addr, known, current_impl,
                        )
                        self._known_implementations[addr] = current_impl
                        if self._event_bus:
                            await self._event_bus.emit("contract_upgrade", {
                                "proxy": addr,
                                "old_impl": known,
                                "new_impl": current_impl,
                            })
                    elif not known:
                        self._known_implementations[addr] = current_impl
                except Exception as e:
                    logger.debug("Contract monitor poll failed for %s: %s", addr, e)

    def stop(self):
        self._running = False

    async def _get_implementation(self, proxy_address: str) -> Optional[str]:
        """Read the EIP-1967 implementation slot from the proxy contract."""
        if not self._chain:
            return None
        try:
            result = await self._chain.call_contract(
                proxy_address, "eth_getStorageAt", IMPLEMENTATION_SLOT, "latest"
            )
            if result and isinstance(result, str) and len(result) >= 42:
                return "0x" + result[-40:]
            return result
        except Exception as e:
            logger.debug("Failed to read implementation slot: %s", e)
            return None
