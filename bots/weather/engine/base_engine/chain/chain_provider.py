"""
Chain provider implementations for Polygon and PolyL2.

PolygonProvider: delegates on-chain transaction submission to an injected
blockchain_client.  Raises NotImplementedError when no client is provided.

PolyL2Provider: placeholder for future Layer-2 Polygon support; raises
NotImplementedError until the feature is implemented.
"""
from __future__ import annotations

from typing import Any, Optional


class PolygonProvider:
    """Sends transactions on the Polygon PoS chain via an injected client."""

    def __init__(self, blockchain_client: Optional[Any] = None) -> None:
        self._client = blockchain_client

    async def send_transaction(
        self,
        to: str,
        data: bytes,
        value: int,
    ) -> str:
        """Submit a transaction and return the tx hash.

        Parameters
        ----------
        to:    recipient address (hex string)
        data:  encoded call data
        value: native token value in wei

        Raises
        ------
        NotImplementedError
            When no blockchain_client was supplied at construction time.
        """
        if self._client is None:
            raise NotImplementedError(
                "PolygonProvider requires a blockchain_client — "
                "pass one at construction time."
            )
        return await self._client.send_transaction(to, data, value)


class PolyL2Provider:
    """Placeholder for future Polygon Layer-2 transaction support."""

    async def send_transaction(
        self,
        to: str,
        data: bytes,
        value: int,
    ) -> str:
        """Not yet implemented.

        Raises
        ------
        NotImplementedError
            Always — Layer-2 support is not yet available.
        """
        raise NotImplementedError(
            "PolyL2Provider: Layer-2 transaction support is not yet available."
        )
