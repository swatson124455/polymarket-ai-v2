"""
RLVR ensemble inference — run the trained forecasting model with guard-rails.

Key features:
  - Ensemble inference: median of N runs (default 7)
  - Guard-rails: token-length limits, gibberish filtering, early-stop
  - Batch inference across thousands of markets
"""
from __future__ import annotations
import re
import statistics
from typing import List, Optional
from structlog import get_logger

from bots.weather.engine.base_engine.ml.rlvr.training_config import RLVRTrainingConfig

logger = get_logger()


def _extract_probability(text: str) -> Optional[float]:
    """
    Extract probability from model output text.

    Looks for patterns like "0.75", "75%", "probability: 0.75", etc.
    """
    if not text:
        return None

    # Try explicit probability patterns
    patterns = [
        r"(?:probability|prob|confidence|estimate)[:\s]*([01]?\.\d+)",
        r"(\d{1,3})%",
        r"\b([01]\.\d+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            try:
                val = float(match.group(1))
                if val > 1.0:
                    val = val / 100.0
                if 0.0 <= val <= 1.0:
                    return val
            except (ValueError, TypeError):
                continue
    return None


def _is_gibberish(text: str, threshold: float = 0.3) -> bool:
    """Check if output is mostly non-alphanumeric (gibberish)."""
    if not text:
        return True
    alnum = sum(1 for c in text if c.isalnum() or c.isspace())
    return (alnum / len(text)) < (1.0 - threshold)


class RLVRInference:
    """
    Runs ensemble inference with the RLVR-trained model.

    If the model is not loaded (no GPU, model not downloaded), returns None
    for all predictions — callers should fall back to API-based LLMs.
    """

    def __init__(self, config: Optional[RLVRTrainingConfig] = None, model=None, tokenizer=None):
        self._config = config or RLVRTrainingConfig()
        self._model = model
        self._tokenizer = tokenizer
        self._loaded = model is not None and tokenizer is not None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load_model(self, model_path: str) -> bool:
        """
        Load the quantized RLVR model from disk.

        Returns True if successful, False otherwise.
        """
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            self._tokenizer = AutoTokenizer.from_pretrained(model_path)
            self._model = AutoModelForCausalLM.from_pretrained(
                model_path,
                device_map="auto",
                torch_dtype="auto",
            )
            self._loaded = True
            logger.info("RLVR model loaded from %s", model_path)
            return True
        except ImportError:
            logger.info("transformers not installed — RLVR inference disabled")
            return False
        except Exception as e:
            logger.warning("RLVR model load failed: %s", e)
            return False

    async def predict(self, question: str, context: str = "") -> Optional[float]:
        """
        Get probability estimate for a prediction question.

        Runs ensemble inference (N runs, take median) with guard-rails.
        Returns probability [0, 1] or None if inference fails.
        """
        if not self._loaded:
            return None

        prompt = self._build_prompt(question, context)
        probabilities: List[float] = []

        for run in range(self._config.ensemble_runs):
            try:
                output = self._generate(prompt)
                if _is_gibberish(output, self._config.gibberish_threshold):
                    continue
                prob = _extract_probability(output)
                if prob is not None:
                    probabilities.append(prob)
                # Early stop if we have enough good runs
                if len(probabilities) >= max(3, self._config.ensemble_runs // 2):
                    if run >= self._config.early_stop_patience:
                        break
            except Exception as e:
                logger.debug("RLVR inference run %d failed: %s", run, e)

        if not probabilities:
            return None

        # Aggregate
        if self._config.aggregation == "median":
            return statistics.median(probabilities)
        return statistics.mean(probabilities)

    async def predict_batch(self, questions: List[str]) -> List[Optional[float]]:
        """Run predictions for multiple questions."""
        results = []
        for q in questions:
            prob = await self.predict(q)
            results.append(prob)
        return results

    def _build_prompt(self, question: str, context: str = "") -> str:
        """Build the forecasting prompt for the model."""
        parts = [
            "You are a superforecaster. Estimate the probability that the following will resolve YES.",
            "Think step by step: consider base rates, reference classes, and recent evidence.",
            "Output your final probability as a decimal between 0.0 and 1.0.",
        ]
        if context:
            parts.append(f"\nContext: {context}")
        parts.append(f"\nQuestion: {question}")
        parts.append("\nProbability:")
        return "\n".join(parts)

    def _generate(self, prompt: str) -> str:
        """Generate text from the model (synchronous for GPU inference)."""
        if not self._model or not self._tokenizer:
            return ""
        try:
            inputs = self._tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=self._config.max_seq_length,
            ).to(self._model.device)

            outputs = self._model.generate(
                **inputs,
                max_new_tokens=self._config.max_output_tokens,
                do_sample=True,
                temperature=0.7,
                top_p=0.9,
            )
            text = self._tokenizer.decode(outputs[0], skip_special_tokens=True)
            # Return only the generated part (after prompt)
            if prompt in text:
                text = text[len(prompt):]
            return text.strip()
        except Exception as e:
            logger.debug("RLVR generate failed: %s", e)
            return ""
