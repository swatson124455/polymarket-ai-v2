"""
LLMForecasterBot — Batch RLVR probability estimation across all active markets.

Distinct from EnsembleBot: runs a local RLVR-trained model (or falls back to
LLM API) in batch mode every 30-60 minutes across ALL active markets. Outputs
probabilities to prediction_log for other bots to consume as features.

The RLVR model (DeepSeek-R1-Distill-Qwen-14B) matches o1 forecasting at
1/30th the cost, enabling batch inference that's cost-prohibitive with API calls.
"""
import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from structlog import get_logger
from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()


class LLMForecasterBot(BaseBot):
    """
    Batch RLVR probability estimator — runs every 30-60 minutes.

    Writes results to prediction_log with model_name='rlvr_ensemble' (or
    'llm_batch_api' for API fallback) for other bots to consume.
    """

    def __init__(self, base_engine):
        super().__init__("LLMForecasterBot", base_engine)
        self._batch_interval = int(getattr(settings, "RLVR_BATCH_INTERVAL_MINUTES", 30)) * 60
        self._last_batch_time = 0.0
        self._rlvr_estimator = None

    def _get_scan_interval_seconds(self) -> float:
        """Check every 60s whether it's time for a new batch."""
        return 60.0

    async def scan_and_trade(self):
        """Run batch estimation if enough time has passed since last batch."""
        now = time.monotonic()
        if now - self._last_batch_time < self._batch_interval:
            return  # Not time for a new batch yet
        self._last_batch_time = now

        logger.info("LLMForecasterBot: starting batch probability estimation")
        await self._run_batch()

    async def _run_batch(self):
        """Fetch all active markets and estimate probabilities in batch."""
        db = getattr(self.base_engine, "db", None)
        if not db or not getattr(db, "session_factory", None):
            return

        try:
            markets = await self.base_engine.get_markets(active=True, limit=500)
            markets = self.base_engine.filter_markets_for_trading(markets)
        except Exception as e:
            logger.warning("LLMForecasterBot: failed to fetch markets: %s", e)
            return

        if not markets:
            return

        # Try RLVR model first, fall back to LLM API
        estimator = self._get_estimator()
        batch_results = []
        batch_size = 10  # Process in batches to avoid overwhelming

        for i in range(0, len(markets), batch_size):
            batch = markets[i:i + batch_size]
            tasks = [self._estimate_one(m, estimator) for m in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for market, result in zip(batch, results):
                if isinstance(result, Exception):
                    continue
                if result is not None:
                    batch_results.append(result)

        # Persist all results to prediction_log (batched to avoid N+1 DB round-trips)
        persisted = 0
        if batch_results and db and hasattr(db, "get_session"):
            try:
                from sqlalchemy import text
                async with db.get_session() as session:
                    for result in batch_results:
                        try:
                            await session.execute(text("""
                                INSERT INTO prediction_log (market_id, predicted_prob, market_price, model_name, edge, prediction_time)
                                VALUES (:mid, :prob, :mp, :model, :edge, :pred_time)
                            """), {
                                "mid": result["market_id"],
                                "prob": result["probability"],
                                "mp": result.get("market_price", 0),
                                "model": result.get("model_name", "rlvr_ensemble"),
                                "edge": result["probability"] - result.get("market_price", 0),
                                "pred_time": datetime.now(timezone.utc).replace(tzinfo=None),
                            })
                            persisted += 1
                        except Exception as e:
                            logger.debug("LLMForecasterBot: persist row failed for %s: %s", result.get("market_id"), e)
                    await session.commit()
            except Exception as e:
                logger.debug("LLMForecasterBot: batch persist failed: %s", e)

        logger.info(
            "LLMForecasterBot: batch complete — %d/%d markets estimated, %d persisted",
            len(batch_results), len(markets), persisted,
        )

    async def _estimate_one(self, market: Dict, estimator) -> Optional[Dict]:
        """Estimate probability for a single market."""
        market_id = str(market.get("id", ""))
        question = market.get("question", "")
        if not market_id or not question:
            return None

        tokens = market.get("tokens", [])
        token_id = ""
        price = 0.5
        if tokens and isinstance(tokens[0], dict):
            token_id = str(tokens[0].get("tokenId", ""))
            try:
                price = float(tokens[0].get("outcomePrice", 0.5))
            except (ValueError, TypeError):
                price = 0.5

        try:
            if estimator:
                prob = await estimator.estimate_probability(
                    market_id=market_id, question=question,
                    current_price=price, category=market.get("category", ""),
                )
                if prob is not None and 0 <= prob <= 1:
                    return {
                        "market_id": market_id, "token_id": token_id,
                        "probability": prob, "confidence": 0.7,
                        "model_name": "rlvr_ensemble",
                    }
        except Exception as e:
            logger.debug("RLVR estimation failed for %s: %s", market_id, e)

        # Fallback: Polyseer structured pro/con prompting via Anthropic
        try:
            prob = await self._polyseer_estimate(question, price, market.get("category", ""))
            if prob is not None and 0 <= prob <= 1:
                return {
                    "market_id": market_id, "token_id": token_id,
                    "probability": prob, "confidence": 0.65,
                    "model_name": "polyseer",
                    "market_price": price,
                }
        except Exception as e:
            logger.debug("Polyseer estimation failed: %s", e)

        # Final fallback: use existing LLM probability estimator
        try:
            llm = getattr(self.base_engine, "_llm_estimator", None)
            if not llm:
                pe = getattr(self.base_engine, "prediction_engine", None)
                llm = getattr(pe, "_llm_estimator", None) if pe else None
            if llm:
                prob = await llm.estimate_probability(
                    market_id=market_id, question=question,
                    current_price=price,
                )
                if prob is not None and 0 <= prob <= 1:
                    return {
                        "market_id": market_id, "token_id": token_id,
                        "probability": prob, "confidence": 0.6,
                        "model_name": "llm_batch_api",
                    }
        except Exception as e:
            logger.debug("LLM API fallback estimation failed: %s", e)

        return None

    async def _polyseer_estimate(
        self, question: str, current_price: float, category: str
    ) -> Optional[float]:
        """
        Polyseer: structured pro/con prompting with Bayesian aggregation.
        Dispatches PRO and CON researcher prompts, then a critic fills gaps.
        """
        import os
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        try:
            import anthropic
        except ImportError:
            return None

        client = anthropic.AsyncAnthropic(api_key=api_key)
        model = "claude-haiku-4-5-20251001"

        # Step 1: PRO researcher
        pro_prompt = (
            f"You are a researcher arguing YES for this prediction market.\n"
            f"Question: {question[:500]}\nCategory: {category}\n"
            f"Current price: {current_price}\n"
            f"List your 3 strongest arguments for YES. Be specific and factual.\n"
            f"End with: PROBABILITY: X.XX"
        )
        # Step 2: CON researcher
        con_prompt = (
            f"You are a researcher arguing NO for this prediction market.\n"
            f"Question: {question[:500]}\nCategory: {category}\n"
            f"Current price: {current_price}\n"
            f"List your 3 strongest arguments for NO. Be specific and factual.\n"
            f"End with: PROBABILITY: X.XX"
        )

        pro_resp, con_resp = await asyncio.gather(
            client.messages.create(model=model, max_tokens=300, messages=[{"role": "user", "content": pro_prompt}]),
            client.messages.create(model=model, max_tokens=300, messages=[{"role": "user", "content": con_prompt}]),
        )

        pro_text = pro_resp.content[0].text
        con_text = con_resp.content[0].text

        # Extract probabilities
        import re
        pro_prob = self._extract_prob(pro_text)
        con_prob = self._extract_prob(con_text)

        if pro_prob is None and con_prob is None:
            return None

        # Step 3: Critic aggregation
        critic_prompt = (
            f"Two researchers analyzed this prediction market.\n"
            f"Question: {question[:300]}\n\n"
            f"PRO argument (YES probability: {pro_prob or 'unknown'}):\n{pro_text[:300]}\n\n"
            f"CON argument (NO probability: {con_prob or 'unknown'}):\n{con_text[:300]}\n\n"
            f"As a neutral critic, what gaps exist? Give your final probability.\n"
            f"Reply with ONLY: PROBABILITY: X.XX"
        )
        critic_resp = await client.messages.create(
            model=model, max_tokens=50, messages=[{"role": "user", "content": critic_prompt}]
        )
        critic_prob = self._extract_prob(critic_resp.content[0].text)

        # Bayesian aggregation: weight critic 40%, pro 30%, con 30%
        probs = []
        weights = []
        if pro_prob is not None:
            probs.append(pro_prob)
            weights.append(0.3)
        if con_prob is not None:
            probs.append(1.0 - con_prob)  # Convert NO prob to YES prob
            weights.append(0.3)
        if critic_prob is not None:
            probs.append(critic_prob)
            weights.append(0.4)

        if not probs:
            return None

        total_w = sum(weights)
        final = sum(p * w for p, w in zip(probs, weights)) / total_w
        return max(0.01, min(0.99, round(final, 3)))

    @staticmethod
    def _extract_prob(text: str) -> Optional[float]:
        """Extract probability from text like 'PROBABILITY: 0.75'."""
        import re
        match = re.search(r'PROBABILITY:\s*(0?\.\d+|1\.0|0|1)', text)
        if match:
            return float(match.group(1))
        return None

    def _get_estimator(self):
        """Load RLVR estimator if available."""
        if self._rlvr_estimator is not None:
            return self._rlvr_estimator
        try:
            from base_engine.features.rlvr_probability import RLVRProbabilityEstimator
            model_path = getattr(settings, "RLVR_MODEL_PATH", "")
            if model_path:
                self._rlvr_estimator = RLVRProbabilityEstimator(model_path=model_path)
                logger.info("LLMForecasterBot: RLVR model loaded from %s", model_path)
                return self._rlvr_estimator
        except ImportError:
            logger.debug("LLMForecasterBot: RLVR module not available, using API fallback")
        except Exception as e:
            logger.debug("LLMForecasterBot: RLVR model load failed: %s", e)
        return None

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        return None  # Batch-only bot, no per-market analysis
