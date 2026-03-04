"""
LLM Probability Estimation (P5-03).

Uses Claude (Anthropic) or GPT-4 (OpenAI) to estimate probability for a
prediction market question. Result is cached with configurable TTL and used
as an additional feature in the prediction ensemble.

Dependencies: anthropic (optional — graceful fallback when not installed).
"""
import os
import json
from typing import Optional, Any, Dict
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()

DEFAULT_CACHE_TTL = 3600  # 1 hour


class LLMProbabilityEstimator:
    """Estimate market probability using LLM API calls."""

    def __init__(self, db: Optional[Any] = None, cache: Optional[Any] = None):
        self.db = db
        self.cache = cache
        self._api_key = os.getenv("ANTHROPIC_API_KEY") or ""
        self._openai_key = os.getenv("OPENAI_API_KEY") or ""
        self._enabled = bool(self._api_key or self._openai_key)
        self._cache_ttl = int(os.getenv("LLM_PROBABILITY_CACHE_TTL", str(DEFAULT_CACHE_TTL)))
        self._superforecaster_prompt = os.getenv("LLM_SUPERFORECASTER_PROMPT", "false").lower() in ("true", "1", "yes")
        self._local_cache: Dict[str, Dict] = {}

    @property
    def is_available(self) -> bool:
        return self._enabled

    async def estimate_probability(
        self,
        market_question: str,
        current_price: float,
        category: str = "",
        time_to_resolution: str = "",
        prompt_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Ask LLM to estimate probability for a market question.

        prompt_type: "standard" | "superforecaster" | None. If None, uses instance default (LLM_SUPERFORECASTER_PROMPT).
        Used for A/B testing: call twice with prompt_type="standard" and "superforecaster", log both to prediction_log.

        Returns:
            probability: float 0.0-1.0 (in "probability" key)
            reasoning: str summary
            model: str which LLM was used
        """
        if not self._enabled:
            return None
        use_super = self._superforecaster_prompt
        if prompt_type == "standard":
            use_super = False
        elif prompt_type == "superforecaster":
            use_super = True

        # Check cache (key includes prompt variant so A/B results are cached separately)
        cache_suffix = "sf" if use_super else "std"
        cache_key = f"llm_prob:{hash(market_question)}:{cache_suffix}"
        cached = self._local_cache.get(cache_key)
        if cached and (datetime.now(timezone.utc) - cached["timestamp"]).total_seconds() < self._cache_ttl:
            return cached["result"]

        # Also check Redis cache
        if self.cache:
            try:
                redis_cached = await self.cache.get(cache_key)
                if redis_cached:
                    result = json.loads(redis_cached) if isinstance(redis_cached, str) else redis_cached
                    self._local_cache[cache_key] = {"result": result, "timestamp": datetime.now(timezone.utc)}
                    return result
            except Exception:
                pass

        # Build prompt (use_super overrides instance default for this call)
        if use_super:
            prompt = self._build_superforecaster_prompt(market_question, current_price, category, time_to_resolution)
        else:
            prompt = self._build_standard_prompt(market_question, current_price, category, time_to_resolution)

        result = None
        if self._api_key:
            result = await self._call_anthropic(prompt)
        elif self._openai_key:
            result = await self._call_openai(prompt)

        if result:
            self._local_cache[cache_key] = {"result": result, "timestamp": datetime.now(timezone.utc)}
            if self.cache:
                try:
                    await self.cache.set(cache_key, json.dumps(result), ttl=self._cache_ttl)
                except Exception:
                    pass

        return result

    def _build_prompt(self, question: str, price: float, category: str, time_to_res: str) -> str:
        if self._superforecaster_prompt:
            return self._build_superforecaster_prompt(question, price, category, time_to_res)
        return self._build_standard_prompt(question, price, category, time_to_res)

    def _build_standard_prompt(self, question: str, price: float, category: str, time_to_res: str) -> str:
        return f"""You are a prediction market probability estimator. Given a market question, estimate the probability of YES resolution.

Market question: {question}
Current market price: {price:.2f} (this is what traders currently think)
Category: {category or 'unknown'}
Time to resolution: {time_to_res or 'unknown'}

Respond with ONLY a JSON object:
{{"probability": <float 0.0 to 1.0>, "reasoning": "<one sentence>"}}"""

    def _build_superforecaster_prompt(self, question: str, price: float, category: str, time_to_res: str) -> str:
        """Tetlock-style superforecasting: reference class, base rates, belief decomposition (improves calibration)."""
        return f"""You are a superforecaster-style probability estimator for prediction markets. Use these principles:
1) Reference class: Think of similar past cases and their base-rate outcomes.
2) Base rate: For category "{category or 'general'}", what is the typical resolution rate for similar questions?
3) Belief decomposition: Break your estimate into factors (e.g. evidence for YES vs NO) and combine.
4) Avoid overconfidence: Only shift strongly from 0.5 when evidence is clear; otherwise stay moderate.
5) Update from market: Current price {price:.2f} reflects others' beliefs—use it as a prior and adjust for information you have.

Market question: {question}
Current market price: {price:.2f}
Category: {category or 'unknown'}
Time to resolution: {time_to_res or 'unknown'}

Respond with ONLY a JSON object:
{{"probability": <float 0.0 to 1.0>, "reasoning": "<one sentence referencing base rate or reference class>"}}"""

    async def _call_anthropic(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Call Claude API for probability estimate with prompt caching."""
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self._api_key)

            # System prompt cached across calls (ephemeral = 5min TTL)
            _sys = "You are a prediction market probability estimator. Respond with ONLY a JSON object: {\"probability\": <float 0.0 to 1.0>, \"reasoning\": \"<one sentence>\"}."
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                system=[{
                    "type": "text",
                    "text": _sys,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            parsed = json.loads(text)
            prob = float(parsed.get("probability", 0.5))
            prob = max(0.01, min(0.99, prob))
            return {
                "probability": prob,
                "reasoning": parsed.get("reasoning", ""),
                "model": "claude-sonnet",
            }
        except ImportError:
            logger.debug("anthropic package not installed")
            return None
        except Exception as e:
            logger.debug("Anthropic API call failed: %s", e)
            return None

    async def _call_openai(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Call OpenAI API for probability estimate."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self._openai_key}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200,
                    },
                    timeout=30,
                )
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"].strip()
                parsed = json.loads(text)
                prob = float(parsed.get("probability", 0.5))
                prob = max(0.01, min(0.99, prob))
                return {
                    "probability": prob,
                    "reasoning": parsed.get("reasoning", ""),
                    "model": "gpt-4o-mini",
                }
        except Exception as e:
            logger.debug("OpenAI API call failed: %s", e)
            return None

    async def _call_gemini(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Call Google Gemini API for probability estimate."""
        gemini_key = os.getenv("GOOGLE_GEMINI_API_KEY") or ""
        if not gemini_key:
            return None
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-2.0-flash")
            response = await model.generate_content_async(prompt)
            text = response.text.strip()
            parsed = json.loads(text)
            prob = float(parsed.get("probability", 0.5))
            prob = max(0.01, min(0.99, prob))
            return {
                "probability": prob,
                "reasoning": parsed.get("reasoning", ""),
                "model": "gemini-2.0-flash",
            }
        except ImportError:
            logger.debug("google-generativeai not installed")
            return None
        except Exception as e:
            logger.debug("Gemini API call failed: %s", e)
            return None

    # ── Multi-provider ensemble ─────────────────────────────────────────

    async def estimate_ensemble(
        self,
        market_question: str,
        current_price: float,
        category: str = "",
        time_to_resolution: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Run LLM providers according to consensus mode.

        Modes (LLM_CONSENSUS_MODE env var):
        - "fallback": Sequential — try Claude, then GPT, then Gemini (default, cheapest)
        - "parallel_vote": All providers in parallel, majority vote
        - "median": All providers in parallel, take median probability

        Tracks disagreement metric: max spread across providers.
        high_disagreement flag when spread > 0.15.
        """
        import asyncio
        mode = os.getenv("LLM_CONSENSUS_MODE", "fallback").lower()
        prompt = self._build_prompt(market_question, current_price, category, time_to_resolution)

        if mode == "fallback":
            return await self._ensemble_fallback(prompt)
        else:
            return await self._ensemble_parallel(prompt, mode)

    async def _ensemble_fallback(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Sequential fallback: try providers in order until one succeeds."""
        import asyncio
        providers = []
        if self._api_key:
            providers.append(("claude", self._call_anthropic))
        if self._openai_key:
            providers.append(("gpt", self._call_openai))
        if os.getenv("GOOGLE_GEMINI_API_KEY"):
            providers.append(("gemini", self._call_gemini))

        if not providers:
            return None

        for name, call_fn in providers:
            try:
                result = await asyncio.wait_for(call_fn(prompt), timeout=15.0)
                if result and result.get("probability") is not None:
                    result["consensus_mode"] = "fallback"
                    result["provider_results"] = {name: result["probability"]}
                    result["disagreement"] = 0.0
                    result["high_disagreement"] = False
                    return result
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug("LLM fallback %s failed: %s", name, e)
                continue

        return None

    async def _ensemble_parallel(self, prompt: str, mode: str) -> Optional[Dict[str, Any]]:
        """Parallel consensus: run all providers, aggregate by mode."""
        import asyncio
        tasks = []
        provider_names = []

        if self._api_key:
            tasks.append(asyncio.wait_for(self._call_anthropic(prompt), timeout=15.0))
            provider_names.append("claude")
        if self._openai_key:
            tasks.append(asyncio.wait_for(self._call_openai(prompt), timeout=15.0))
            provider_names.append("gpt")
        if os.getenv("GOOGLE_GEMINI_API_KEY"):
            tasks.append(asyncio.wait_for(self._call_gemini(prompt), timeout=15.0))
            provider_names.append("gemini")

        if not tasks:
            return None

        results = await asyncio.gather(*tasks, return_exceptions=True)

        probabilities = []
        provider_results = {}
        for name, result in zip(provider_names, results):
            if isinstance(result, Exception) or result is None:
                logger.debug("LLM ensemble provider %s failed: %s", name, result)
                continue
            prob = result.get("probability")
            if prob is not None:
                probabilities.append(prob)
                provider_results[name] = prob

        if not probabilities:
            return None

        # Disagreement metric: max spread across providers
        disagreement = max(probabilities) - min(probabilities) if len(probabilities) > 1 else 0.0
        high_disagreement = disagreement > 0.15

        if high_disagreement:
            logger.warning(
                "LLM high disagreement",
                providers=provider_results,
                spread=round(disagreement, 4),
            )

        # Aggregate based on mode
        if mode == "parallel_vote":
            # Majority vote: each provider votes YES (>0.5) or NO (<=0.5)
            yes_votes = sum(1 for p in probabilities if p > 0.5)
            no_votes = len(probabilities) - yes_votes
            if yes_votes > no_votes:
                final_prob = sum(p for p in probabilities if p > 0.5) / yes_votes
            elif no_votes > yes_votes:
                final_prob = sum(p for p in probabilities if p <= 0.5) / no_votes
            else:
                # Tie — use median
                probabilities.sort()
                final_prob = probabilities[len(probabilities) // 2]
        else:
            # Median mode (default for parallel)
            probabilities.sort()
            final_prob = probabilities[len(probabilities) // 2]

        return {
            "probability": final_prob,
            "reasoning": f"Ensemble of {len(probabilities)} LLMs ({mode}, spread={disagreement:.3f})",
            "model": "llm_ensemble",
            "consensus_mode": mode,
            "provider_results": provider_results,
            "disagreement": round(disagreement, 4),
            "high_disagreement": high_disagreement,
        }
