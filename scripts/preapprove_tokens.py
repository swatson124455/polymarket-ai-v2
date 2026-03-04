"""
Pre-approve USDCe (and optionally outcome tokens) for the Polymarket CTF Exchange.
Run once at startup to set MAX_UINT256 allowance so per-order approval checks can be skipped (Phase 1).
Requires: PRIVATE_KEY, POLYGON_RPC (or QUICKNODE_HTTP), and wallet with MATIC for gas.
"""
import asyncio
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from structlog import get_logger
from config.settings import settings
from base_engine.execution.contract_manager import ContractManager

logger = get_logger()


async def main() -> bool:
    """Run USDCe pre-approval (amount=None => MAX_UINT256). Returns True if already approved or success."""
    key = (getattr(settings, "PRIVATE_KEY", None) or os.getenv("PRIVATE_KEY") or "").strip()
    if not key:
        logger.warning("No PRIVATE_KEY set; skipping pre-approval")
        return False
    try:
        cm = ContractManager()
        result = await cm.ensure_usdce_approved(amount_usd=None)
        if result.get("success"):
            logger.info("USDCe pre-approval done (or already approved)", already_approved=result.get("already_approved"))
            return True
        logger.warning("USDCe pre-approval failed: %s", result.get("error"))
        return False
    except Exception as e:
        logger.error("Pre-approval error: %s", e, exc_info=True)
        return False


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
