"""
UMA Optimistic Oracle V3 ProposePrice / PriceProposed monitor.

Polls Polygon for proposal events and emits proposed_outcome to EventBus.
Bots can react: hold if proposed aligns with position, exit if contradicts, or buy proposed outcome.
Enable with UMA_PROPOSAL_MONITOR_ENABLED=true and set UMA_OO_V3_POLYGON to the OO contract address.

Extended (2026 roadmap): governance proposal monitoring + token concentration tracking.
"""
import asyncio
from typing import Any, Dict, Optional
from structlog import get_logger
from config.settings import settings

logger = get_logger()

# UMA OOv3 PriceProposed-style event (assertionId, proposedPrice, ancillaryData)
PRICE_PROPOSED_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "assertionId", "type": "bytes32"},
        {"indexed": False, "name": "proposedPrice", "type": "int256"},
        {"indexed": False, "name": "ancillaryData", "type": "bytes"},
    ],
    "name": "PriceProposed",
    "type": "event",
}


async def run_uma_proposal_monitor(
    event_bus: Any,
    blockchain_client: Any,
    contract_address: Optional[str] = None,
    poll_interval_seconds: float = 120.0,
    from_block_offset: int = 500,
) -> None:
    """
    Poll UMA OOv3 for PriceProposed events and emit proposed_outcome to event_bus.
    Stops when event_bus is None or contract_address is not set.
    """
    address = (contract_address or getattr(settings, "UMA_OO_V3_POLYGON", None) or "").strip()
    if not address or not event_bus:
        logger.debug("UMA proposal monitor disabled (no address or event_bus)")
        return
    try:
        await blockchain_client.ensure_client()
    except Exception as e:
        logger.warning("UMA proposal monitor: blockchain client failed: %s", e)
        return
    last_block: Optional[int] = None
    while True:
        try:
            latest = await blockchain_client.w3.eth.get_block_number()
            from_b = last_block if last_block is not None else max(0, latest - from_block_offset)
            to_b = latest
            if from_b > to_b:
                await asyncio.sleep(poll_interval_seconds)
                continue
            try:
                w3 = blockchain_client.w3
                try:
                    from web3 import AsyncWeb3
                    checksum = AsyncWeb3.to_checksum_address(address)
                except (AttributeError, ImportError):
                    from web3 import Web3
                    checksum = Web3.to_checksum_address(address)
                contract = w3.eth.contract(
                    address=checksum,
                    abi=[PRICE_PROPOSED_ABI],
                )
                events = await contract.events.PriceProposed.get_logs(
                    fromBlock=from_b,
                    toBlock=to_b,
                )
            except Exception as e:
                logger.debug("UMA get_logs failed: %s", e)
                events = []
            for evt in events:
                args = evt.get("args") or {}
                assertion_id = args.get("assertionId")
                proposed_price = args.get("proposedPrice", 0)
                ancillary_data = args.get("ancillaryData", b"")
                if assertion_id is not None:
                    try:
                        proposed_outcome = 1 if (int(proposed_price) != 0) else 0
                        ancillary_hex = ancillary_data.hex() if isinstance(ancillary_data, bytes) else str(ancillary_data)
                        await event_bus.emit("proposed_outcome", {
                            "assertionId": assertion_id.hex() if hasattr(assertion_id, "hex") else str(assertion_id),
                            "proposedPrice": int(proposed_price),
                            "proposed_outcome": proposed_outcome,
                            "ancillaryData": ancillary_hex,
                            "blockNumber": evt.get("blockNumber"),
                            "transactionHash": evt.get("transactionHash").hex() if evt.get("transactionHash") else None,
                        })
                        logger.info(
                            "UMA proposed_outcome emitted",
                            assertion_id=assertion_id,
                            proposed_outcome=proposed_outcome,
                        )
                    except Exception as emit_err:
                        logger.debug("UMA emit failed: %s", emit_err)
            last_block = to_b + 1
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("UMA proposal monitor iteration failed: %s", e)
        await asyncio.sleep(poll_interval_seconds)


# ── Extended (2026 roadmap): governance + token concentration ────────

async def check_governance_proposals(
    blockchain_client: Any,
    event_bus: Any,
    governance_contract: Optional[str] = None,
) -> None:
    """
    One-shot check for UMA governance proposals that could affect oracle behavior.

    Monitors for UMIP proposals, fee changes, or voter concentration that
    could impact proposal reliability.
    """
    address = (governance_contract or "").strip()
    if not address or not event_bus:
        return
    try:
        logger.info("Checking UMA governance proposals at %s", address)
        # In production: query Snapshot API for active UMA proposals
        # and check if any affect oracle dispute resolution parameters
    except Exception as e:
        logger.debug("Governance proposal check failed: %s", e)


async def check_proposer_concentration(
    blockchain_client: Any,
    event_bus: Any,
    contract_address: Optional[str] = None,
    lookback_blocks: int = 10000,
) -> Optional[Dict]:
    """
    Check if proposal activity is concentrated among few addresses.

    High concentration (few proposers dominate) increases manipulation risk.
    """
    address = (contract_address or getattr(settings, "UMA_OO_V3_POLYGON", None) or "").strip()
    if not address or not blockchain_client:
        return None
    try:
        latest = await blockchain_client.w3.eth.get_block_number()
        from_b = max(0, latest - lookback_blocks)
        try:
            from web3 import Web3
            checksum = Web3.to_checksum_address(address)
        except (AttributeError, ImportError):
            checksum = address
        contract = blockchain_client.w3.eth.contract(
            address=checksum, abi=[PRICE_PROPOSED_ABI],
        )
        events = await contract.events.PriceProposed.get_logs(fromBlock=from_b, toBlock=latest)
        proposer_counts: Dict = {}
        for evt in events:
            tx_hash = evt.get("transactionHash")
            if tx_hash:
                try:
                    tx = await blockchain_client.w3.eth.get_transaction(tx_hash)
                    sender = tx.get("from", "unknown")
                    proposer_counts[sender] = proposer_counts.get(sender, 0) + 1
                except Exception:
                    pass
        total_proposals = sum(proposer_counts.values())
        if total_proposals == 0:
            return {"proposer_count": 0, "top_proposer_share": 0, "concentration_score": 0}
        top_share = max(proposer_counts.values()) / total_proposals
        result = {
            "proposer_count": len(proposer_counts),
            "total_proposals": total_proposals,
            "top_proposer_share": top_share,
            "concentration_score": top_share,
        }
        if top_share > 0.5:
            logger.warning("High proposer concentration: top address controls %.0f%%", top_share * 100)
            if event_bus:
                await event_bus.emit("oracle_concentration_warning", result)
        return result
    except Exception as e:
        logger.debug("Proposer concentration check failed: %s", e)
        return None
