"""
UMA Oracle Resolution Monitor (P5-06).

Monitors UMA Optimistic Oracle ProposePrice / PriceProposed events on Polygon.
When proposal submitted: ~2-hour liveness window where outcome is known
but not fully priced in. 98.5% resolve without dispute.

Requires: web3 package, UMA_ORACLE_CONTRACT address.
Hardwired dependency — returns empty when contract address not configured.
"""
import os
import time
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()

# UMA Optimistic Oracle V2/V3 on Polygon
DEFAULT_ORACLE_CONTRACT = os.getenv("UMA_ORACLE_CONTRACT", "")
POLYGON_RPC = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")

# Default liveness period for UMA Optimistic Oracle (2 hours = 7200 seconds)
DEFAULT_LIVENESS_SECONDS = 7200

# Dispute rate from historical data (~1.5% of proposals get disputed)
HISTORICAL_DISPUTE_RATE = 0.015

# UMA Optimistic Oracle V2 ProposePrice event ABI
# This is the standard event emitted when a proposer submits a price
PROPOSE_PRICE_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "requester", "type": "address"},
        {"indexed": True, "name": "identifier", "type": "bytes32"},
        {"indexed": True, "name": "timestamp", "type": "uint256"},
        {"indexed": False, "name": "ancillaryData", "type": "bytes"},
        {"indexed": False, "name": "proposedPrice", "type": "int256"},
        {"indexed": False, "name": "expirationTimestamp", "type": "uint256"},
        {"indexed": False, "name": "currency", "type": "address"},
    ],
    "name": "ProposePrice",
    "type": "event",
}

# UMA OOv3 PriceProposed / AssertionMade event (newer oracle version)
ASSERTION_MADE_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "assertionId", "type": "bytes32"},
        {"indexed": False, "name": "domainId", "type": "bytes32"},
        {"indexed": False, "name": "claim", "type": "bytes"},
        {"indexed": True, "name": "asserter", "type": "address"},
        {"indexed": False, "name": "callbackRecipient", "type": "address"},
        {"indexed": False, "name": "escalationManager", "type": "address"},
        {"indexed": False, "name": "caller", "type": "address"},
        {"indexed": False, "name": "expirationTime", "type": "uint64"},
        {"indexed": False, "name": "currency", "type": "address"},
        {"indexed": False, "name": "bond", "type": "uint256"},
        {"indexed": True, "name": "identifier", "type": "bytes32"},
    ],
    "name": "AssertionMade",
    "type": "event",
}

# DisputePrice event — if we see this, the proposal is contested
DISPUTE_PRICE_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "requester", "type": "address"},
        {"indexed": True, "name": "identifier", "type": "bytes32"},
        {"indexed": True, "name": "timestamp", "type": "uint256"},
        {"indexed": False, "name": "ancillaryData", "type": "bytes"},
        {"indexed": False, "name": "proposedPrice", "type": "int256"},
    ],
    "name": "DisputePrice",
    "type": "event",
}

# Full contract ABI (just the events we need)
ORACLE_EVENTS_ABI = [PROPOSE_PRICE_ABI, ASSERTION_MADE_ABI, DISPUTE_PRICE_ABI]


def _decode_ancillary_data(raw_bytes: bytes) -> str:
    """Attempt to decode ancillary data bytes to a human-readable string.

    Polymarket encodes the market question/identifier as UTF-8 in ancillary data.
    Falls back to hex if decoding fails.
    """
    if not raw_bytes:
        return ""
    try:
        # Strip null bytes and decode
        decoded = raw_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")
        return decoded
    except Exception:
        return raw_bytes.hex() if isinstance(raw_bytes, bytes) else str(raw_bytes)


def _extract_market_id_from_ancillary(ancillary_text: str) -> Optional[str]:
    """Try to extract a Polymarket condition_id or market identifier from ancillary data.

    Polymarket ancillary data typically contains the question text and sometimes
    a reference to the condition_id. Format varies by market type.
    """
    if not ancillary_text:
        return None
    # Look for condition_id pattern (0x-prefixed hex, 64 chars)
    import re
    hex_match = re.search(r"(0x[a-fA-F0-9]{64})", ancillary_text)
    if hex_match:
        return hex_match.group(1)
    # Some ancillary data contains "q: title:..." format
    # Return the full text for fuzzy matching downstream
    return None


class OracleMonitor:
    """Monitor UMA ProposePrice events for early resolution signals.

    Tracks:
    - ProposePrice events (OOv2) and AssertionMade events (OOv3)
    - DisputePrice events to invalidate active proposals
    - Liveness countdown per proposal
    - Market-to-proposal mapping via ancillary data decoding
    """

    # Polygon block time ~2 seconds
    POLYGON_BLOCK_TIME_SECONDS = 2.0

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._contract_address = DEFAULT_ORACLE_CONTRACT
        self._web3 = None
        self._contract = None
        self._initialized = False
        self._recent_proposals: List[Dict] = []
        self._disputed_proposals: set = set()  # Set of disputed proposal keys
        self._last_checked_block: Optional[int] = None

    @property
    def is_available(self) -> bool:
        return bool(self._contract_address)

    async def init(self) -> None:
        """Initialize web3 connection and oracle contract. No-op if not configured."""
        if not self.is_available:
            logger.info("Oracle monitor: no UMA_ORACLE_CONTRACT set, running in stub mode")
            return
        try:
            from web3 import Web3
            self._web3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
            if self._web3.is_connected():
                checksum = Web3.to_checksum_address(self._contract_address)
                self._contract = self._web3.eth.contract(
                    address=checksum,
                    abi=ORACLE_EVENTS_ABI,
                )
                self._initialized = True
                logger.info(
                    "Oracle monitor connected to Polygon RPC, contract=%s",
                    self._contract_address[:10] + "...",
                )
            else:
                logger.warning("Oracle monitor: could not connect to Polygon RPC")
        except ImportError:
            logger.debug("web3 package not installed for oracle monitoring")
        except Exception as e:
            logger.debug("Oracle monitor init failed: %s", e)

    async def check_proposals(self) -> List[Dict[str, Any]]:
        """
        Check for recent ProposePrice / AssertionMade events.

        Returns list of proposals with:
        - proposal_id: unique identifier (tx hash or assertion ID)
        - proposed_price: the proposed outcome value (1e18 = YES, 0 = NO)
        - proposed_outcome: 1 (YES) or 0 (NO)
        - ancillary_data: decoded question/market reference
        - market_id: extracted condition_id if found in ancillary data
        - expiration_timestamp: when the liveness window ends
        - liveness_remaining_seconds: seconds until proposal can be settled
        - block_number: block where proposal was made
        - proposer: address that submitted the proposal
        - disputed: whether a DisputePrice event was seen for this proposal
        """
        if not self._initialized or not self._web3 or not self._contract:
            return []

        try:
            current_block = self._web3.eth.block_number
            # Look back ~2 hours (~3600 blocks at 2s/block) or from last checked
            if self._last_checked_block is not None:
                from_block = self._last_checked_block + 1
            else:
                lookback_blocks = int(DEFAULT_LIVENESS_SECONDS / self.POLYGON_BLOCK_TIME_SECONDS)
                from_block = max(0, current_block - lookback_blocks)

            if from_block > current_block:
                return self._recent_proposals

            logger.debug(
                "Oracle monitor checking blocks %d-%d (%d blocks)",
                from_block, current_block, current_block - from_block,
            )

            proposals = []
            now_ts = int(time.time())

            # ── Fetch ProposePrice events (OOv2) ──
            try:
                propose_events = self._contract.events.ProposePrice.get_logs(
                    fromBlock=from_block, toBlock=current_block,
                )
                for evt in propose_events:
                    proposal = self._parse_propose_price_event(evt, now_ts)
                    if proposal:
                        proposals.append(proposal)
            except Exception as e:
                logger.debug("ProposePrice get_logs failed (may be OOv3): %s", e)

            # ── Fetch AssertionMade events (OOv3) ──
            try:
                assertion_events = self._contract.events.AssertionMade.get_logs(
                    fromBlock=from_block, toBlock=current_block,
                )
                for evt in assertion_events:
                    proposal = self._parse_assertion_made_event(evt, now_ts)
                    if proposal:
                        proposals.append(proposal)
            except Exception as e:
                logger.debug("AssertionMade get_logs failed (may be OOv2): %s", e)

            # ── Fetch DisputePrice events to mark disputed proposals ──
            try:
                dispute_events = self._contract.events.DisputePrice.get_logs(
                    fromBlock=from_block, toBlock=current_block,
                )
                for evt in dispute_events:
                    dispute_key = self._get_dispute_key(evt)
                    if dispute_key:
                        self._disputed_proposals.add(dispute_key)
                        logger.info("Oracle monitor: dispute detected — %s", dispute_key[:20])
            except Exception as e:
                logger.debug("DisputePrice get_logs failed: %s", e)

            # Mark disputed proposals
            for p in proposals:
                p_key = p.get("proposal_id", "")
                p["disputed"] = p_key in self._disputed_proposals

            # Merge with existing, deduplicate by proposal_id
            existing_ids = {p["proposal_id"] for p in self._recent_proposals}
            for p in proposals:
                if p["proposal_id"] not in existing_ids:
                    self._recent_proposals.append(p)

            # Prune expired proposals (liveness window passed + 1 hour buffer)
            self._recent_proposals = [
                p for p in self._recent_proposals
                if p.get("expiration_timestamp", 0) > now_ts - 3600
            ]

            # Recalculate liveness remaining for all
            for p in self._recent_proposals:
                p["liveness_remaining_seconds"] = max(
                    0, p.get("expiration_timestamp", 0) - now_ts
                )

            self._last_checked_block = current_block

            if proposals:
                logger.info(
                    "Oracle monitor found %d new proposals (%d total active)",
                    len(proposals), len(self._recent_proposals),
                )

            return self._recent_proposals

        except Exception as e:
            logger.debug("Oracle proposal check failed: %s", e)
            return self._recent_proposals

    def _parse_propose_price_event(self, evt: Any, now_ts: int) -> Optional[Dict[str, Any]]:
        """Parse a ProposePrice event (OOv2) into a proposal dict."""
        try:
            args = evt.get("args") or {}
            proposed_price = int(args.get("proposedPrice", 0))
            expiration_ts = int(args.get("expirationTimestamp", 0))
            ancillary_raw = args.get("ancillaryData", b"")
            tx_hash = evt.get("transactionHash")
            block_number = evt.get("blockNumber", 0)

            # Decode ancillary data to find market reference
            ancillary_text = _decode_ancillary_data(
                ancillary_raw if isinstance(ancillary_raw, bytes) else b""
            )
            market_id = _extract_market_id_from_ancillary(ancillary_text)

            # UMA prices: 1e18 = YES (1.0), 0 = NO
            proposed_outcome = 1 if proposed_price > 0 else 0

            # Get proposer from transaction
            proposer = ""
            if tx_hash and self._web3:
                try:
                    tx = self._web3.eth.get_transaction(tx_hash)
                    proposer = str(tx.get("from", ""))
                except Exception:
                    pass

            proposal_id = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash or block_number)

            return {
                "proposal_id": proposal_id,
                "source": "ProposePrice_v2",
                "proposed_price": proposed_price,
                "proposed_outcome": proposed_outcome,
                "proposed_side": "YES" if proposed_outcome == 1 else "NO",
                "ancillary_data": ancillary_text,
                "market_id": market_id,
                "expiration_timestamp": expiration_ts,
                "liveness_remaining_seconds": max(0, expiration_ts - now_ts),
                "block_number": block_number,
                "proposer": proposer,
                "disputed": False,
                "timestamp": datetime.fromtimestamp(
                    now_ts - int((evt.get("blockNumber", 0) - block_number) * self.POLYGON_BLOCK_TIME_SECONDS),
                    tz=timezone.utc,
                ).isoformat(),
            }
        except Exception as e:
            logger.debug("Failed to parse ProposePrice event: %s", e)
            return None

    def _parse_assertion_made_event(self, evt: Any, now_ts: int) -> Optional[Dict[str, Any]]:
        """Parse an AssertionMade event (OOv3) into a proposal dict."""
        try:
            args = evt.get("args") or {}
            assertion_id = args.get("assertionId")
            claim_raw = args.get("claim", b"")
            expiration_time = int(args.get("expirationTime", 0))
            asserter = str(args.get("asserter", ""))
            bond = int(args.get("bond", 0))
            block_number = evt.get("blockNumber", 0)

            # Decode claim bytes
            claim_text = _decode_ancillary_data(
                claim_raw if isinstance(claim_raw, bytes) else b""
            )
            market_id = _extract_market_id_from_ancillary(claim_text)

            # OOv3 assertions: the claim itself encodes the proposed outcome
            # For Polymarket, assertion = "YES" outcome proposed
            # (non-zero assertionId with bond means someone is asserting truth)
            proposed_outcome = 1  # OOv3 assertions are affirmative by default

            proposal_id = assertion_id.hex() if hasattr(assertion_id, "hex") else str(assertion_id or block_number)

            return {
                "proposal_id": proposal_id,
                "source": "AssertionMade_v3",
                "proposed_price": 1,  # OOv3 asserts truth
                "proposed_outcome": proposed_outcome,
                "proposed_side": "YES",
                "ancillary_data": claim_text,
                "market_id": market_id,
                "expiration_timestamp": expiration_time,
                "liveness_remaining_seconds": max(0, expiration_time - now_ts),
                "block_number": block_number,
                "proposer": asserter,
                "bond_wei": bond,
                "disputed": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.debug("Failed to parse AssertionMade event: %s", e)
            return None

    def _get_dispute_key(self, evt: Any) -> Optional[str]:
        """Extract a key to match disputes against proposals."""
        try:
            tx_hash = evt.get("transactionHash")
            args = evt.get("args") or {}
            # For OOv2, disputes reference the same requester/identifier/timestamp
            identifier = args.get("identifier")
            if identifier:
                return identifier.hex() if hasattr(identifier, "hex") else str(identifier)
            if tx_hash:
                return tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
            return None
        except Exception:
            return None

    async def assess_proposal(self, proposal: Dict) -> Dict[str, Any]:
        """
        Assess if a proposal is actionable and generate a trading recommendation.

        Scoring factors:
        - Liveness remaining: more time = lower urgency but safer
        - Dispute status: disputed proposals are unreliable
        - Bond size: higher bond = more confident proposer
        - Historical dispute rate: ~1.5% of proposals get disputed
        - Market price vs proposed outcome: bigger gap = bigger opportunity

        Returns:
            Dict with confidence, recommendation, and reasoning.
        """
        proposal_id = proposal.get("proposal_id", "unknown")
        market_id = proposal.get("market_id")
        proposed_outcome = proposal.get("proposed_outcome", 0)
        proposed_side = proposal.get("proposed_side", "YES" if proposed_outcome == 1 else "NO")
        liveness_remaining = proposal.get("liveness_remaining_seconds", 0)
        disputed = proposal.get("disputed", False)
        expiration_ts = proposal.get("expiration_timestamp", 0)
        bond_wei = proposal.get("bond_wei", 0)

        # ── Base confidence: start from historical non-dispute rate ──
        base_confidence = 1.0 - HISTORICAL_DISPUTE_RATE  # ~0.985

        # ── Factor 1: Dispute status ──
        if disputed:
            return {
                "proposal_id": proposal_id,
                "market_id": market_id,
                "proposed_outcome": proposed_outcome,
                "proposed_side": proposed_side,
                "confidence": 0.0,
                "recommendation": "avoid",
                "liveness_remaining_seconds": liveness_remaining,
                "reason": "Proposal has been disputed — outcome uncertain",
            }

        # ── Factor 2: Liveness window timing ──
        # Early in liveness: slightly lower confidence (dispute still possible)
        # Late in liveness: higher confidence (dispute window closing)
        if liveness_remaining <= 0:
            # Already expired — should be settling/settled
            time_factor = 1.0
        elif liveness_remaining < 300:
            # < 5 minutes left — very high confidence
            time_factor = 0.99
        elif liveness_remaining < 1800:
            # < 30 minutes — high confidence
            time_factor = 0.97
        elif liveness_remaining < 3600:
            # < 1 hour — moderate confidence
            time_factor = 0.95
        else:
            # > 1 hour — full liveness period, still early
            time_factor = 0.92

        # ── Factor 3: Bond strength (OOv3) ──
        # Higher bond = proposer has more skin in the game
        bond_factor = 1.0
        if bond_wei > 0:
            bond_eth = bond_wei / 1e18
            if bond_eth >= 10.0:
                bond_factor = 1.02  # Strong bond
            elif bond_eth >= 1.0:
                bond_factor = 1.0
            else:
                bond_factor = 0.98  # Weak bond

        # ── Composite confidence ──
        confidence = min(0.99, base_confidence * time_factor * bond_factor)

        # ── Factor 4: Check current market price if we have DB access ──
        market_price = await self._get_market_price(market_id) if market_id else None
        price_gap = None
        if market_price is not None:
            # Expected value: proposed_outcome (0 or 1)
            expected_value = float(proposed_outcome)
            price_gap = abs(expected_value - market_price)

        # ── Generate recommendation ──
        recommendation, reason = self._generate_recommendation(
            confidence=confidence,
            liveness_remaining=liveness_remaining,
            proposed_side=proposed_side,
            market_price=market_price,
            price_gap=price_gap,
            disputed=disputed,
        )

        result = {
            "proposal_id": proposal_id,
            "market_id": market_id,
            "proposed_outcome": proposed_outcome,
            "proposed_side": proposed_side,
            "confidence": round(confidence, 4),
            "recommendation": recommendation,
            "liveness_remaining_seconds": liveness_remaining,
            "reason": reason,
        }
        if market_price is not None:
            result["market_price"] = round(market_price, 4)
        if price_gap is not None:
            result["price_gap"] = round(price_gap, 4)

        return result

    def _generate_recommendation(
        self,
        confidence: float,
        liveness_remaining: int,
        proposed_side: str,
        market_price: Optional[float],
        price_gap: Optional[float],
        disputed: bool,
    ) -> Tuple[str, str]:
        """Generate a recommendation and reason string.

        Returns:
            Tuple of (recommendation, reason)
            recommendation: "trade" | "alert" | "wait" | "avoid"
        """
        if disputed:
            return "avoid", "Proposal disputed — outcome uncertain"

        if confidence < 0.90:
            return "wait", f"Low confidence ({confidence:.1%}) — waiting for more data"

        if liveness_remaining <= 0:
            return "alert", "Liveness expired — market should be settling imminently"

        # If we know market price, check if there's a tradeable gap
        if market_price is not None and price_gap is not None:
            if proposed_side == "YES":
                # Proposed YES → market price should be near 1.0
                if market_price < 0.97:
                    edge = 1.0 - market_price - 0.015  # Subtract taker fee
                    if edge > 0.01:
                        return "trade", (
                            f"BUY YES @ {market_price:.3f} — proposal says YES, "
                            f"edge ~{edge:.1%} after fees, "
                            f"{liveness_remaining}s until settlement"
                        )
            else:
                # Proposed NO → market price should be near 0.0
                if market_price > 0.03:
                    edge = market_price - 0.015  # Can buy NO at (1 - market_price)
                    if edge > 0.01:
                        return "trade", (
                            f"BUY NO @ {1.0 - market_price:.3f} — proposal says NO, "
                            f"edge ~{edge:.1%} after fees, "
                            f"{liveness_remaining}s until settlement"
                        )

            # Price already reflects proposal
            return "wait", (
                f"Market price {market_price:.3f} already near proposed "
                f"outcome ({proposed_side}) — no edge"
            )

        # No market price data — recommend alert for manual review
        if liveness_remaining < 1800:
            return "trade", (
                f"Proposal {proposed_side} with {confidence:.1%} confidence, "
                f"{liveness_remaining}s remaining — likely settles soon"
            )

        return "alert", (
            f"Proposal {proposed_side} with {confidence:.1%} confidence, "
            f"{liveness_remaining}s liveness remaining"
        )

    async def _get_market_price(self, market_id: str) -> Optional[float]:
        """Fetch current market price from DB if available."""
        if not self.db or not market_id:
            return None
        try:
            if hasattr(self.db, "get_session"):
                from sqlalchemy import text
                async with self.db.get_session() as session:
                    result = await session.execute(
                        text("""
                            SELECT yes_price FROM markets
                            WHERE id = :mid OR condition_id = :mid
                            LIMIT 1
                        """),
                        {"mid": market_id},
                    )
                    row = result.fetchone()
                    if row and row[0] is not None:
                        return float(row[0])
        except Exception as e:
            logger.debug("Oracle monitor: market price lookup failed: %s", e)
        return None

    async def get_active_proposals(self) -> List[Dict[str, Any]]:
        """Return only proposals with liveness remaining and not disputed."""
        all_proposals = await self.check_proposals()
        return [
            p for p in all_proposals
            if p.get("liveness_remaining_seconds", 0) > 0
            and not p.get("disputed", False)
        ]

    async def match_proposals_to_markets(
        self,
        markets: Optional[List[Dict]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Match active proposals to known markets via ancillary data.

        If DB is available, tries to match condition_id from ancillary data
        to markets table. Otherwise returns proposals with whatever market_id
        was extracted from ancillary data.
        """
        proposals = await self.get_active_proposals()
        if not proposals:
            return []

        matched = []
        for p in proposals:
            mid = p.get("market_id")
            ancillary = p.get("ancillary_data", "")

            # Try to match via DB if no market_id extracted from ancillary data
            if not mid and self.db and ancillary and hasattr(self.db, "get_session"):
                try:
                    from sqlalchemy import text
                    async with self.db.get_session() as session:
                        # Fuzzy match: search for markets whose question
                        # contains keywords from the ancillary data
                        # (ancillary data often includes the market question)
                        search_term = ancillary[:100].replace("'", "''")
                        result = await session.execute(
                            text("""
                                SELECT id, condition_id, question FROM markets
                                WHERE question ILIKE :pattern
                                AND active = true
                                LIMIT 3
                            """),
                            {"pattern": f"%{search_term[:50]}%"},
                        )
                        rows = result.fetchall()
                        if rows and len(rows) == 1:
                            # Unique match
                            p["market_id"] = str(rows[0][0])
                            p["condition_id"] = rows[0][1]
                            p["matched_question"] = rows[0][2]
                except Exception as e:
                    logger.debug("Oracle market matching failed: %s", e)

            # Also match against provided markets list
            if not p.get("market_id") and markets and ancillary:
                for m in markets:
                    q = (m.get("question") or "").lower()
                    if q and ancillary.lower()[:50] in q:
                        p["market_id"] = str(m.get("id", ""))
                        p["matched_question"] = m.get("question", "")
                        break

            matched.append(p)

        return matched
