"""
LLM Signal Extractor - Use LLM to extract trading signals from text.

Maps news/social posts to specific Polymarket markets and extracts:
- Affected market IDs
- Direction (YES/NO)
- Confidence
- Reasoning
"""
from typing import Dict, List, Optional, Any
from structlog import get_logger

logger = get_logger()


class LLMSignalExtractor:
    """
    Extract trading signals from text using LLM.
    
    Note: This is a framework. In production, would integrate with:
    - OpenAI GPT-4
    - Anthropic Claude
    - Local LLM (Llama, Mistral)
    """
    
    def __init__(self, llm_provider: str = "openai", api_key: Optional[str] = None):
        self.llm_provider = llm_provider
        self.api_key = api_key
        self.llm_client = None
    
    async def extract_signal(
        self,
        text: str,
        source: str,
        markets: List[Dict[str, Any]],
        timestamp: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Extract trading signal from text.
        
        Args:
            text: News/article/social post text
            source: Source name
            markets: List of active markets for matching
            timestamp: Optional timestamp
        
        Returns:
            Signal dictionary or None
        """
        if not markets:
            return None
        
        # Build market context for LLM
        market_context = self._build_market_context(markets[:50])  # Limit to 50 markets
        
        # Build prompt
        prompt = self._build_extraction_prompt(text, source, market_context)
        
        # Call LLM (placeholder - would use actual LLM API)
        try:
            response = await self._call_llm(prompt)
            signal = self._parse_llm_response(response, text, source)
            return signal
        except Exception as e:
            logger.error(f"LLM extraction error: {str(e)}", exc_info=True)
            return None
    
    def _build_market_context(self, markets: List[Dict[str, Any]]) -> str:
        """Build market context string for LLM."""
        context_lines = []
        for market in markets:
            question = market.get("question", "")
            market_id = market.get("id", "")
            category = market.get("category", "unknown")
            context_lines.append(f"- {market_id}: {question} (Category: {category})")
        
        return "\n".join(context_lines)
    
    def _build_extraction_prompt(
        self,
        text: str,
        source: str,
        market_context: str
    ) -> str:
        """Build prompt for LLM."""
        return f"""
Analyze this news/social post and determine if it affects any prediction markets.

TEXT: {text}
SOURCE: {source}

ACTIVE MARKETS:
{market_context}

Return JSON:
{{
    "affected_markets": [
        {{
            "market_id": "...",
            "direction": "YES" | "NO",
            "confidence": 0.0-1.0,
            "reasoning": "..."
        }}
    ],
    "time_sensitivity": "immediate" | "hours" | "days",
    "is_breaking": true | false
}}

Only include markets where the text clearly relates to the market question.
Confidence should reflect how directly the text relates to the market outcome.
"""
    
    async def _call_llm(self, prompt: str) -> str:
        """
        Call LLM API using configured provider (Anthropic, OpenAI, or Google Gemini).

        Falls back to empty response when no API key is configured.
        Uses env vars: ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY.
        """
        import os

        # ── Anthropic (Claude) ───────────────────────────────────────────
        if self.llm_provider == "anthropic" or (not self.api_key and os.getenv("ANTHROPIC_API_KEY")):
            api_key = self.api_key or os.getenv("ANTHROPIC_API_KEY", "")
            if api_key:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        r = await client.post(
                            "https://api.anthropic.com/v1/messages",
                            headers={
                                "x-api-key": api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json",
                            },
                            json={
                                "model": "claude-sonnet-4-20250514",
                                "max_tokens": 1024,
                                "messages": [{"role": "user", "content": prompt}],
                            },
                        )
                        if r.status_code == 200:
                            data = r.json()
                            return data.get("content", [{}])[0].get("text", "")
                except Exception as e:
                    logger.debug("Anthropic LLM call failed: %s", e)

        # ── OpenAI (GPT) ────────────────────────────────────────────────
        if self.llm_provider == "openai" or (not self.api_key and os.getenv("OPENAI_API_KEY")):
            api_key = self.api_key or os.getenv("OPENAI_API_KEY", "")
            if api_key:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        r = await client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": "gpt-4o-mini",
                                "messages": [{"role": "user", "content": prompt}],
                                "max_tokens": 1024,
                                "temperature": 0.2,
                            },
                        )
                        if r.status_code == 200:
                            data = r.json()
                            return data["choices"][0]["message"]["content"]
                except Exception as e:
                    logger.debug("OpenAI LLM call failed: %s", e)

        # ── Google Gemini ────────────────────────────────────────────────
        google_key = os.getenv("GOOGLE_API_KEY", "")
        if google_key:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    r = await client.post(
                        f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={google_key}",
                        json={"contents": [{"parts": [{"text": prompt}]}]},
                    )
                    if r.status_code == 200:
                        data = r.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            except Exception as e:
                logger.debug("Gemini LLM call failed: %s", e)

        # ── No API key configured — return empty response ────────────────
        logger.debug("LLM signal extraction skipped (no API key configured for %s)", self.llm_provider)
        return '{"affected_markets": [], "time_sensitivity": "days", "is_breaking": false}'
    
    def _parse_llm_response(
        self,
        response: str,
        original_text: str,
        source: str
    ) -> Optional[Dict[str, Any]]:
        """Parse LLM response into signal format."""
        try:
            import json
            data = json.loads(response)
            
            affected_markets = data.get("affected_markets", [])
            if not affected_markets:
                return None
            
            # Return highest confidence signal
            best_signal = max(affected_markets, key=lambda x: x.get("confidence", 0.0))
            
            return {
                "market_id": best_signal.get("market_id"),
                "source_type": "news" if "news" in source.lower() else "social",
                "source_name": source,
                "direction": best_signal.get("direction", "YES"),
                "confidence": best_signal.get("confidence", 0.5),
                "raw_text": original_text,
                "time_sensitivity": data.get("time_sensitivity", "hours"),
                "is_breaking": data.get("is_breaking", False),
                "reasoning": best_signal.get("reasoning", "")
            }
        except Exception as e:
            logger.error(f"Failed to parse LLM response: {str(e)}", exc_info=True)
            return None
    
    async def batch_extract(
        self,
        texts: List[str],
        source: str,
        markets: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Extract signals from multiple texts (batch processing).
        
        Args:
            texts: List of texts
            source: Source name
            markets: List of active markets
        
        Returns:
            List of extracted signals
        """
        signals = []
        
        for text in texts:
            signal = await self.extract_signal(text, source, markets)
            if signal:
                signals.append(signal)
        
        return signals
