"""
Independent Chain-of-Thought (CoT) validation for high-edge esports trades.

When the model identifies a high-edge opportunity (>15%), use an LLM
to independently validate the prediction. This catches cases where:
- Market question was misinterpreted by regex
- Team roster changes invalidate Glicko-2 ratings
- Tournament format/rules differ from what model assumes

Only used for high-edge trades to control API costs.
Gracefully degrades: if no LLM API key, returns approved=True (no-op).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CoTValidator:
    """LLM-based independent validation for high-edge trades.

    Queries an LLM with the market question, team names, model probability,
    and market price. Returns a structured verdict: approve, reject, or caution.

    Cost-controlled: only called for edge > EDGE_THRESHOLD (default 15%).
    """

    EDGE_THRESHOLD = 0.15  # Only validate trades with edge > 15%

    def __init__(self):
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._available = bool(self._api_key)
        self._call_count = 0
        self._max_calls_per_scan = 3  # Rate limit per scan cycle

        if self._available:
            logger.info("CoTValidator: initialized (API key present)")
        else:
            logger.debug("CoTValidator: no ANTHROPIC_API_KEY, validation disabled")

    @property
    def is_available(self) -> bool:
        return self._available

    def reset_scan_counter(self) -> None:
        """Reset per-scan call counter. Called at start of each scan."""
        self._call_count = 0

    async def validate_trade(
        self,
        question: str,
        game: str,
        model_prob: float,
        market_price: float,
        edge: float,
        side: str,
        team_a: str = "",
        team_b: str = "",
    ) -> Dict[str, Any]:
        """Validate a high-edge trade using LLM reasoning.

        Args:
            question: Market question text.
            game: Game identifier.
            model_prob: Model's probability estimate.
            market_price: Current market price.
            edge: Computed edge (model_prob - market_price).
            side: Trade side (YES/NO).
            team_a: Team A name (if extracted).
            team_b: Team B name (if extracted).

        Returns:
            Dict with keys:
            - approved: bool
            - reason: str (short explanation)
            - confidence: float (LLM's confidence in its verdict)
        """
        # Default: approve (no-op when unavailable or under threshold)
        default_result = {"approved": True, "reason": "below_threshold", "confidence": 1.0}

        if not self._available:
            return default_result

        if edge < self.EDGE_THRESHOLD:
            return default_result

        if self._call_count >= self._max_calls_per_scan:
            return {"approved": True, "reason": "rate_limited", "confidence": 0.5}

        self._call_count += 1

        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=self._api_key)

            prompt = (
                f"You are validating an esports prediction market trade.\n\n"
                f"Market: {question}\n"
                f"Game: {game}\n"
                f"Teams: {team_a} vs {team_b}\n"
                f"Model probability (team A wins): {model_prob:.3f}\n"
                f"Market price: {market_price:.3f}\n"
                f"Edge: {edge:.3f} ({side} side)\n\n"
                f"Quick sanity check:\n"
                f"1. Does the market question match a {game} esports match?\n"
                f"2. Are the team names plausible {game} teams?\n"
                f"3. Is a {edge:.1%} edge reasonable, or does this suggest a parsing error?\n\n"
                f"Respond with exactly one word: APPROVE or REJECT\n"
                f"Then a one-sentence reason."
            )

            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip().upper()
            approved = text.startswith("APPROVE")
            reason = response.content[0].text.strip()

            logger.info(
                "cot_validation_result",
                game=game,
                edge=round(edge, 4),
                approved=approved,
                reason=reason[:100],
            )

            return {
                "approved": approved,
                "reason": reason[:200],
                "confidence": 0.8 if approved else 0.9,
            }

        except ImportError:
            logger.debug("CoTValidator: anthropic package not installed")
            self._available = False
            return default_result
        except Exception as e:
            logger.debug("CoTValidator: validation failed: %s", e)
            return default_result  # Fail open: approve on error
