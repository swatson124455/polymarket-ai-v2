"""
B10: Kalshi read-only price signal.

Fetches Kalshi public market prices for equivalent markets (no auth required for prices).
If Kalshi price differs from Polymarket by >3pp in the same direction, use as a
cross-venue confirmation signal boost (+0.02 confidence).

Source: Ng, Peng, Tao & Zhou (SSRN 2026) — Polymarket leads Kalshi in price discovery
when liquidity is high; lag-based cross-platform arbitrage is real.

Design:
- In-memory cache with 60s TTL (Kalshi prices are slow-moving)
- 5s HTTP timeout — never blocks bot scan
- Graceful degradation: returns 0.0 on any failure
- No authentication required for Kalshi REST API price reads
"""
import asyncio
import time
from typing import Dict, Optional, Tuple
import aiohttp
from structlog import get_logger

logger = get_logger()

# Kalshi public REST base (no auth needed for market price reads)
KALSHI_API_BASE = "https://trading-api.kalshi.com/trade-api/v2"

# In-memory cache: ticker → (price_yes, timestamp)
_kalshi_price_cache: Dict[str, Tuple[float, float]] = {}
_CACHE_TTL = 60.0  # seconds
_HTTP_TIMEOUT = 5.0  # seconds — never block scan


async def get_kalshi_yes_price(ticker: str) -> Optional[float]:
    """
    Fetch Kalshi YES price for a given ticker symbol.
    Returns float price [0,1] or None on failure.
    Uses in-memory TTL cache to avoid hammering the API.
    """
    now = time.monotonic()
    cached = _kalshi_price_cache.get(ticker)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    try:
        url = f"{KALSHI_API_BASE}/markets/{ticker}"
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
        ) as session:
            async with session.get(url, headers={"Accept": "application/json"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    market = data.get("market", {})
                    # Kalshi prices are in cents (0-99); normalize to 0-1
                    yes_price_cents = market.get("yes_bid") or market.get("last_price")
                    if yes_price_cents is not None:
                        yes_price = float(yes_price_cents) / 100.0
                        _kalshi_price_cache[ticker] = (yes_price, now)
                        return yes_price
    except asyncio.TimeoutError:
        logger.debug("Kalshi price fetch timeout for ticker=%s", ticker)
    except Exception as e:
        logger.debug("Kalshi price fetch failed for ticker=%s: %s", ticker, e)
    return None


async def get_kalshi_signal(
    polymarket_price: float,
    kalshi_ticker: Optional[str],
    side: str,
) -> float:
    """
    B10: Cross-venue signal. Compare Polymarket price to Kalshi price.
    If they differ by >3pp and Kalshi is leading (closer to fair value),
    return +0.02 if aligned with our trade direction, else -0.02.

    Args:
        polymarket_price: Current Polymarket YES token price (0-1)
        kalshi_ticker: Kalshi market ticker (e.g. 'PRESWIN-24-DT') or None
        side: 'YES' or 'NO'

    Returns:
        Signal adjustment: +0.02 (confirm), -0.02 (oppose), or 0.0 (no signal)
    """
    if not kalshi_ticker:
        return 0.0

    kalshi_price = await get_kalshi_yes_price(kalshi_ticker)
    if kalshi_price is None:
        return 0.0

    diff = kalshi_price - polymarket_price  # positive = Kalshi higher = YES more likely
    if abs(diff) < 0.03:  # <3pp difference — no material signal
        return 0.0

    # Cross-venue signal: Kalshi says YES is more/less likely than Polymarket
    kalshi_says_yes_up = diff > 0  # Kalshi price > Polymarket = Kalshi more bullish on YES

    if side == "YES":
        if kalshi_says_yes_up:
            return 0.02  # Both venues agree YES is underpriced on Polymarket → confirm
        else:
            return -0.02  # Kalshi disagrees — caution
    else:  # side == "NO"
        if not kalshi_says_yes_up:
            return 0.02  # Kalshi also bearish on YES → NO side confirmed
        else:
            return -0.02  # Kalshi disagrees

    return 0.0
