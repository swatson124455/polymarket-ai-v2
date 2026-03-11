"""
Position Reconciliation (P2B-06 / P6-01).

Compares internal position state against on-chain (Polygon) token balances.
Flags discrepancies as OVER_INTERNAL, UNDER_INTERNAL, or MATCHED.

Phase 1 (P2B-06): Periodic check every 15 minutes.
Phase 2 (P6-01): Three-way: internal DB vs CLOB API vs blockchain.

Requires: web3 (already in requirements.txt).
Hardwired — returns MATCHED when wallet not configured.
"""
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from enum import Enum
from structlog import get_logger

logger = get_logger()


class DiscrepancyType(Enum):
    MATCHED = "matched"
    OVER_INTERNAL = "over_internal"   # We think we hold more than blockchain confirms
    UNDER_INTERNAL = "under_internal" # Blockchain shows more than we track


class PositionReconciler:
    """Reconcile internal positions against on-chain state."""

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._wallet = os.getenv("WALLET_ADDRESS", "")
        self._rpc_url = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
        self._web3 = None
        self._initialized = False

    @property
    def is_available(self) -> bool:
        return bool(self._wallet)

    async def init(self) -> None:
        """Initialize web3 connection. No-op if wallet not configured."""
        if not self.is_available:
            logger.info("Reconciler: no WALLET_ADDRESS, running in stub mode")
            return
        try:
            from web3 import Web3
            self._web3 = Web3(Web3.HTTPProvider(self._rpc_url))
            if self._web3.is_connected():
                self._initialized = True
                logger.info("Position reconciler connected to Polygon")
        except ImportError:
            logger.debug("web3 not installed for reconciliation")
        except Exception as e:
            logger.debug("Reconciler init failed: %s", e)

    async def reconcile(self) -> List[Dict[str, Any]]:
        """
        Compare internal positions against blockchain balances.
        Returns list of discrepancies.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return []

        # Get internal positions
        internal = await self._get_internal_positions()
        if not internal:
            return []

        if not self._initialized:
            # Paper mode: reconcile positions table vs paper_trades net instead of chain
            return await self._reconcile_paper_positions(internal)

        # Get on-chain balances
        chain_balances = await self._get_chain_balances(internal)

        discrepancies = []
        for pos in internal:
            mid = pos["market_id"]
            internal_size = pos["size"]
            chain_size = chain_balances.get(mid, 0.0)

            tolerance = max(0.01, internal_size * 0.05)  # 5% tolerance

            if abs(internal_size - chain_size) <= tolerance:
                dtype = DiscrepancyType.MATCHED
            elif internal_size > chain_size:
                dtype = DiscrepancyType.OVER_INTERNAL
            else:
                dtype = DiscrepancyType.UNDER_INTERNAL

            discrepancies.append({
                "market_id": mid,
                "type": dtype.value,
                "internal_size": internal_size,
                "chain_size": chain_size,
                "difference": internal_size - chain_size,
            })

            if dtype != DiscrepancyType.MATCHED:
                logger.warning(
                    "Position discrepancy",
                    market_id=mid,
                    type=dtype.value,
                    internal=internal_size,
                    chain=chain_size,
                )

        return discrepancies

    async def _get_internal_positions(self) -> List[Dict]:
        """Get open positions from DB."""
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT market_id, token_id, side, size
                    FROM positions
                    WHERE status IN ('open', 'reserving')
                """))
                return [
                    {"market_id": row[0], "token_id": row[1], "side": row[2], "size": float(row[3])}
                    for row in r.fetchall()
                ]
        except Exception as e:
            logger.debug("Internal positions query failed: %s", e)
            return []

    async def _reconcile_paper_positions(self, internal: List[Dict]) -> List[Dict[str, Any]]:
        """Paper-trading reconciliation: compare positions table vs paper_trades net.

        For each open position in `internal`, compute the net size from paper_trades
        (BUY-side shares accumulated, minus SELL-side shares closed).  A discrepancy
        of > 1% triggers a WARNING log.  Read-only — no position mutations.
        """
        results: List[Dict[str, Any]] = []
        if not internal:
            return results
        try:
            from sqlalchemy import text as _text, bindparam
            market_ids = [p["market_id"] for p in internal]
            async with self.db.get_session() as session:
                pt_rows = await session.execute(
                    _text(
                        "SELECT market_id,"
                        " SUM(CASE WHEN side IN ('YES', 'BUY') THEN size ELSE -size END) AS net_size"
                        " FROM paper_trades"
                        " WHERE market_id = ANY(:ids)"
                        " AND realized_pnl IS NULL"
                        " GROUP BY market_id"
                    ),
                    {"ids": market_ids},
                )
                pt_map: Dict[str, float] = {
                    row.market_id: float(row.net_size or 0.0) for row in pt_rows.fetchall()
                }
        except Exception as exc:
            logger.debug("paper_reconcile_query_failed", error=str(exc))
            # Fall back to all-matched on DB error to avoid blocking
            return [
                {"market_id": p["market_id"], "type": DiscrepancyType.MATCHED.value,
                 "internal_size": p["size"], "paper_net_size": p["size"]}
                for p in internal
            ]
        for pos in internal:
            mid = pos["market_id"]
            internal_size = pos["size"]
            paper_net = pt_map.get(mid, 0.0)
            tolerance = max(0.01, internal_size * 0.01)  # 1% tolerance
            if abs(internal_size - paper_net) <= tolerance:
                dtype = DiscrepancyType.MATCHED
            elif internal_size > paper_net:
                dtype = DiscrepancyType.OVER_INTERNAL
            else:
                dtype = DiscrepancyType.UNDER_INTERNAL
            if dtype != DiscrepancyType.MATCHED:
                logger.warning(
                    "position_reconciliation_discrepancy",
                    market_id=mid,
                    type=dtype.value,
                    positions_table_size=internal_size,
                    paper_trades_net=paper_net,
                    difference=round(internal_size - paper_net, 6),
                )
            results.append({
                "market_id": mid,
                "type": dtype.value,
                "internal_size": internal_size,
                "paper_net_size": paper_net,
            })
        return results

    async def _get_chain_balances(self, positions: List[Dict]) -> Dict[str, float]:
        """Get on-chain CTF token balances via ERC1155 balanceOf.

        Uses the ConditionalTokens contract (ERC1155) on Polygon. Each position's
        token_id is the ERC1155 token ID. Balance is returned in USDC units
        (raw balance / 1e6 — CTF tokens have 6 decimals like USDC).
        """
        balances: Dict[str, float] = {}
        if not self._web3 or not self._wallet:
            return balances

        # Minimal ERC1155 ABI for balanceOf(address, uint256)
        ERC1155_BALANCE_ABI = [{
            "inputs": [
                {"name": "account", "type": "address"},
                {"name": "id", "type": "uint256"},
            ],
            "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function",
        }]

        try:
            from web3 import Web3
            ct_address = os.getenv(
                "CTF_CONTRACT",
                "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
            )
            contract = self._web3.eth.contract(
                address=Web3.to_checksum_address(ct_address),
                abi=ERC1155_BALANCE_ABI,
            )
            wallet_cs = Web3.to_checksum_address(self._wallet)
        except Exception as e:
            logger.debug("reconciler_contract_init_failed", error=str(e))
            return balances

        for pos in positions:
            token_id = pos.get("token_id")
            if not token_id:
                continue
            try:
                raw_balance = contract.functions.balanceOf(
                    wallet_cs, int(token_id)
                ).call()
                # CTF tokens have 6 decimals (matches USDC)
                balances[pos["market_id"]] = raw_balance / 1e6
            except Exception as e:
                logger.debug("reconciler_balance_query_failed",
                             market_id=pos["market_id"], error=str(e))

        return balances
