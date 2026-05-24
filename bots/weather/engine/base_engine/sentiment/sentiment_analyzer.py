"""
Market Sentiment Analysis

Provides sentiment signals from multiple sources:
- Social media sentiment (Twitter, Reddit)
- News sentiment analysis
- Market maker activity patterns
- Volume/price divergence signals
"""

import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from enum import Enum
from structlog import get_logger
from bots.weather.engine.config.settings import settings

logger = get_logger()


class SentimentSignal(Enum):
    """Sentiment signal types."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    STRONG_BULLISH = "strong_bullish"
    STRONG_BEARISH = "strong_bearish"


class SentimentAnalyzer:
    """
    Market sentiment analyzer from multiple sources.
    
    Sources:
    - Social media (Twitter, Reddit) - future implementation
    - News sentiment - future implementation
    - Market maker activity - current implementation
    - Volume/price divergence - current implementation
    """
    
    def __init__(self):
        self.sentiment_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl = 300  # 5 minutes
    
    async def analyze_market_sentiment(
        self,
        market_id: str,
        price_data: Dict[str, Any],
        volume_data: Dict[str, Any],
        orderbook_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Analyze sentiment for a market.
        
        Args:
            market_id: Market identifier
            price_data: Price history and current price
            volume_data: Volume history and current volume
            orderbook_data: Optional orderbook data
            
        Returns:
            Dictionary with sentiment analysis
        """
        cache_key = f"sentiment:{market_id}"
        if cache_key in self.sentiment_cache:
            cached = self.sentiment_cache[cache_key]
            age = (datetime.now(timezone.utc) - cached.get("timestamp", datetime.min.replace(tzinfo=timezone.utc))).total_seconds()
            if age < self.cache_ttl:
                return cached.get("data")
        
        # Analyze different sentiment signals
        volume_sentiment = self._analyze_volume_sentiment(price_data, volume_data)
        orderbook_sentiment = self._analyze_orderbook_sentiment(orderbook_data) if orderbook_data else None
        divergence_sentiment = self._analyze_price_volume_divergence(price_data, volume_data)
        
        # Combine signals
        combined_sentiment = self._combine_sentiment_signals([
            volume_sentiment,
            orderbook_sentiment,
            divergence_sentiment
        ])
        
        result = {
            "market_id": market_id,
            "overall_sentiment": combined_sentiment["signal"].value,
            "confidence": combined_sentiment["confidence"],
            "signals": {
                "volume": volume_sentiment,
                "orderbook": orderbook_sentiment,
                "divergence": divergence_sentiment
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Cache result
        self.sentiment_cache[cache_key] = {
            "data": result,
            "timestamp": datetime.now(timezone.utc)
        }
        
        return result
    
    def _analyze_volume_sentiment(
        self,
        price_data: Dict[str, Any],
        volume_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze sentiment based on volume patterns."""
        current_price = price_data.get("current_price", 0)
        price_change = price_data.get("price_change_24h", 0)
        current_volume = volume_data.get("current_volume", 0)
        avg_volume = volume_data.get("avg_volume_24h", 0)
        
        if avg_volume == 0:
            return {"signal": SentimentSignal.NEUTRAL, "confidence": 0.0, "reason": "insufficient_data"}
        
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        # High volume + price increase = bullish
        if volume_ratio > 1.5 and price_change > 0.05:
            return {"signal": SentimentSignal.STRONG_BULLISH, "confidence": 0.8, "reason": "high_volume_price_increase"}
        elif volume_ratio > 1.2 and price_change > 0.02:
            return {"signal": SentimentSignal.BULLISH, "confidence": 0.6, "reason": "increased_volume_price_up"}
        
        # High volume + price decrease = bearish
        if volume_ratio > 1.5 and price_change < -0.05:
            return {"signal": SentimentSignal.STRONG_BEARISH, "confidence": 0.8, "reason": "high_volume_price_decrease"}
        elif volume_ratio > 1.2 and price_change < -0.02:
            return {"signal": SentimentSignal.BEARISH, "confidence": 0.6, "reason": "increased_volume_price_down"}
        
        return {"signal": SentimentSignal.NEUTRAL, "confidence": 0.4, "reason": "normal_volume"}
    
    def _analyze_orderbook_sentiment(self, orderbook_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Analyze sentiment based on orderbook imbalance."""
        if not orderbook_data:
            return None
        
        bids = orderbook_data.get("bids", [])
        asks = orderbook_data.get("asks", [])
        
        if not bids or not asks:
            return None
        
        total_bid_volume = sum(b.get("size", 0) for b in bids)
        total_ask_volume = sum(a.get("size", 0) for a in asks)
        
        if total_bid_volume == 0 and total_ask_volume == 0:
            return None
        
        imbalance = (total_bid_volume - total_ask_volume) / (total_bid_volume + total_ask_volume)
        
        if imbalance > 0.3:
            return {"signal": SentimentSignal.BULLISH, "confidence": 0.7, "reason": "strong_bid_imbalance", "imbalance": imbalance}
        elif imbalance < -0.3:
            return {"signal": SentimentSignal.BEARISH, "confidence": 0.7, "reason": "strong_ask_imbalance", "imbalance": imbalance}
        else:
            return {"signal": SentimentSignal.NEUTRAL, "confidence": 0.5, "reason": "balanced_orderbook", "imbalance": imbalance}
    
    def _analyze_price_volume_divergence(
        self,
        price_data: Dict[str, Any],
        volume_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze price/volume divergence."""
        price_trend = price_data.get("price_trend", 0)  # -1 to 1
        volume_trend = volume_data.get("volume_trend", 0)  # -1 to 1
        
        # Bullish divergence: price down but volume up
        if price_trend < -0.1 and volume_trend > 0.2:
            return {"signal": SentimentSignal.BULLISH, "confidence": 0.6, "reason": "bullish_divergence"}
        
        # Bearish divergence: price up but volume down
        if price_trend > 0.1 and volume_trend < -0.2:
            return {"signal": SentimentSignal.BEARISH, "confidence": 0.6, "reason": "bearish_divergence"}
        
        return {"signal": SentimentSignal.NEUTRAL, "confidence": 0.4, "reason": "no_divergence"}
    
    def _vader_score(self, text: str) -> Optional[Dict[str, Any]]:
        """VADER rule-based sentiment (fast, no model download)."""
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            if not hasattr(self, "_vader_analyzer"):
                self._vader_analyzer = SentimentIntensityAnalyzer()
            scores = self._vader_analyzer.polarity_scores(text)
            return {"compound": scores["compound"], "scores": scores, "source": "vader"}
        except ImportError:
            return None
        except Exception:
            return None

    def _transformer_score(self, text: str) -> Optional[Dict[str, Any]]:
        """HuggingFace distilbert sentiment (general purpose)."""
        try:
            from transformers import pipeline
            if not hasattr(self, "_hf_pipeline"):
                self._hf_pipeline = pipeline(
                    "sentiment-analysis",
                    model="distilbert-base-uncased-finetuned-sst-2-english",
                    device=-1,  # CPU
                    truncation=True,
                    max_length=512,
                )
            result = self._hf_pipeline(text[:512])[0]
            label = result["label"]  # POSITIVE or NEGATIVE
            score = result["score"]  # 0-1 confidence
            compound = score if label == "POSITIVE" else -score
            return {"compound": compound, "hf_label": label, "hf_score": score, "source": "transformer"}
        except ImportError:
            return None
        except Exception:
            return None

    def _finbert_score(self, text: str) -> Optional[Dict[str, Any]]:
        """FinBERT sentiment — domain-specific for financial/news text."""
        try:
            from transformers import pipeline
            if not hasattr(self, "_finbert_pipeline"):
                self._finbert_pipeline = pipeline(
                    "sentiment-analysis",
                    model="ProsusAI/finbert",
                    device=-1,
                    truncation=True,
                    max_length=512,
                )
            result = self._finbert_pipeline(text[:512])[0]
            label = result["label"].lower()  # positive, negative, neutral
            score = result["score"]
            if label == "positive":
                compound = score
            elif label == "negative":
                compound = -score
            else:
                compound = 0.0
            return {"compound": compound, "label": label, "score": score, "source": "finbert"}
        except ImportError:
            logger.debug("transformers not installed — FinBERT unavailable")
            return None
        except Exception as e:
            logger.debug("FinBERT scoring failed: %s", e)
            return None

    def _cardiffnlp_score(self, text: str) -> Optional[Dict[str, Any]]:
        """CardiffNLP RoBERTa sentiment — domain-specific for social media text."""
        try:
            from transformers import pipeline
            if not hasattr(self, "_cardiffnlp_pipeline"):
                self._cardiffnlp_pipeline = pipeline(
                    "sentiment-analysis",
                    model="cardiffnlp/twitter-roberta-base-sentiment-latest",
                    device=-1,
                    truncation=True,
                    max_length=512,
                )
            result = self._cardiffnlp_pipeline(text[:512])[0]
            label = result["label"].lower()  # positive, negative, neutral
            score = result["score"]
            if label == "positive":
                compound = score
            elif label == "negative":
                compound = -score
            else:
                compound = 0.0
            return {"compound": compound, "label": label, "score": score, "source": "cardiffnlp"}
        except ImportError:
            logger.debug("transformers not installed — CardiffNLP unavailable")
            return None
        except Exception as e:
            logger.debug("CardiffNLP scoring failed: %s", e)
            return None

    def analyze_text_sentiment(self, text: str, text_type: str = "news") -> Dict[str, Any]:
        """
        Cascade sentiment: VADER fast-path → domain model (FinBERT or CardiffNLP).

        VADER runs first (~0.001s). If |compound| > threshold, return immediately.
        Otherwise cascade to domain-specific model:
          - text_type="news" → FinBERT (ProsusAI/finbert)
          - text_type="social" → CardiffNLP (twitter-roberta-base-sentiment-latest)
        Ensemble weight: 0.3 * VADER + 0.7 * domain_model.
        Falls back gracefully if domain model unavailable.
        """
        vader = self._vader_score(text)
        vader_threshold = getattr(settings, "SENTIMENT_VADER_THRESHOLD", 0.6)
        use_finbert = getattr(settings, "SENTIMENT_USE_FINBERT", True)

        # VADER fast-path: high-confidence VADER → skip expensive model
        if vader and abs(vader["compound"]) > vader_threshold:
            compound = vader["compound"]
            return self._build_sentiment_result(compound, "vader_fastpath", vader=vader)

        # Domain model cascade
        domain_result = None
        if use_finbert:
            if text_type == "social":
                domain_result = self._cardiffnlp_score(text)
            else:
                domain_result = self._finbert_score(text)
            # Fallback to generic transformer if domain model fails
            if domain_result is None:
                domain_result = self._transformer_score(text)

        # Ensemble
        if vader and domain_result:
            compound = 0.3 * vader["compound"] + 0.7 * domain_result["compound"]
            source = f"vader+{domain_result['source']}"
        elif vader:
            compound = vader["compound"]
            source = "vader"
        elif domain_result:
            compound = domain_result["compound"]
            source = domain_result["source"]
        else:
            return {"signal": SentimentSignal.NEUTRAL, "confidence": 0.0, "reason": "no_sentiment_engine"}

        return self._build_sentiment_result(compound, source, vader=vader, domain=domain_result)

    def _build_sentiment_result(
        self, compound: float, source: str,
        vader: Optional[Dict] = None, domain: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Build standardized sentiment result dict."""
        if compound >= 0.05:
            signal = SentimentSignal.BULLISH if compound < 0.3 else SentimentSignal.STRONG_BULLISH
        elif compound <= -0.05:
            signal = SentimentSignal.BEARISH if compound > -0.3 else SentimentSignal.STRONG_BEARISH
        else:
            signal = SentimentSignal.NEUTRAL

        return {
            "signal": signal,
            "confidence": min(abs(compound), 1.0),
            "reason": f"{source}_text_sentiment",
            "compound": round(compound, 4),
            "vader_scores": vader.get("scores") if vader else None,
            "domain_model": domain.get("source") if domain else None,
            "domain_label": domain.get("label") if domain else None,
            "domain_score": domain.get("score") if domain else None,
        }

    def _combine_sentiment_signals(self, signals: List[Optional[Dict[str, Any]]]) -> Dict[str, Any]:
        """Combine multiple sentiment signals into overall sentiment."""
        valid_signals = [s for s in signals if s is not None]
        
        if not valid_signals:
            return {"signal": SentimentSignal.NEUTRAL, "confidence": 0.0}
        
        # Count signals
        bullish_count = sum(1 for s in valid_signals if s["signal"] in [SentimentSignal.BULLISH, SentimentSignal.STRONG_BULLISH])
        bearish_count = sum(1 for s in valid_signals if s["signal"] in [SentimentSignal.BEARISH, SentimentSignal.STRONG_BEARISH])
        
        # Calculate average confidence
        avg_confidence = sum(s.get("confidence", 0) for s in valid_signals) / len(valid_signals)
        
        # Determine overall signal
        if bullish_count > bearish_count:
            if bullish_count >= 2:
                signal = SentimentSignal.STRONG_BULLISH
            else:
                signal = SentimentSignal.BULLISH
        elif bearish_count > bullish_count:
            if bearish_count >= 2:
                signal = SentimentSignal.STRONG_BEARISH
            else:
                signal = SentimentSignal.BEARISH
        else:
            signal = SentimentSignal.NEUTRAL
        
        return {"signal": signal, "confidence": avg_confidence}
