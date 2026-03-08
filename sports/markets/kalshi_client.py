"""
Kalshi Sports Market Client — RSA-PSS authentication (Kalshi v2 API).

Kalshi's v2 API uses RSA-PSS / SHA-256 key-based authentication instead of
the legacy email+password flow in base_engine/data/kalshi_client.py.

Auth header format:
  KALSHI-ACCESS-KEY: <your_api_key_id>
  KALSHI-ACCESS-TIMESTAMP: <unix_ms>
  KALSHI-ACCESS-SIGNATURE: <base64(RSA-PSS-SHA256(message))>

Message to sign:  "{timestamp}{METHOD}{path}" (no nonce, no query params)

The private key is a PEM-encoded RSA key stored at KALSHI_RSA_PRIVATE_KEY_PATH.
The key ID (api key identifier) is KALSHI_API_KEY.

Wraps / extends base_engine/data/kalshi_client.py for sports-specific filtering.
cryptography library is already installed (requirements.txt).
"""
from __future__ import annotations

import base64
import time
from typing import Any, Dict, List, Optional
from structlog import get_logger

logger = get_logger()

_KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Sports event type categories used to filter Kalshi markets
_SPORTS_EVENT_TYPES = {
    "nba", "nfl", "mlb", "nhl", "soccer", "tennis",
    "football", "basketball", "baseball", "hockey",
}

# NFL offseason keywords for free-agency / draft / combine markets
_NFL_OFFSEASON_KEYWORDS = {
    "free agency", "draft", "combine", "nfl draft", "free agent",
    "signs with", "agrees to terms", "traded to",
}


# ---------------------------------------------------------------------------
# Dataclass for a resolved sports market candidate
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field

@dataclass
class SportsMarketCandidate:
    """A prediction market (Polymarket or Kalshi) matched to a sport/game."""
    platform: str                   # "polymarket" | "kalshi"
    market_id: str                  # Polymarket condition_id or Kalshi ticker
    market_type: Optional[str]      # e.g. "moneyline", "draft_pick", "injury_prop"
    sport: str
    yes_token_id: Optional[str]     # Polymarket YES token (None for Kalshi)
    no_token_id: Optional[str]      # Polymarket NO token  (None for Kalshi)
    current_price: Optional[float]  # Last mid-price (0–1)
    title: str = ""
    # I39: Timestamp when current_price was fetched — arb calc rejects prices >60s old
    price_fetched_at: Optional[float] = field(default=None)  # time.monotonic() at fetch


# ---------------------------------------------------------------------------
# RSA-PSS signing helper
# ---------------------------------------------------------------------------

def _load_private_key(pem_path: str):
    """Load RSA private key from PEM file."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    with open(pem_path, "rb") as fh:
        return load_pem_private_key(fh.read(), password=None)


def _sign_request(
    private_key,
    method: str,
    path: str,
    timestamp_ms: int,
) -> str:
    """
    Sign a Kalshi API request using RSA-PSS / SHA-256.

    Message: "{timestamp_ms}{METHOD}{path}" — no nonce, no query params.
    Returns: base64-encoded signature string.
    Source: https://docs.kalshi.com/getting_started/api_keys
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    message = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")
    signature_bytes = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature_bytes).decode("utf-8")


# ---------------------------------------------------------------------------
# KalshiSportsClient
# ---------------------------------------------------------------------------

class KalshiSportsClient:
    """
    Kalshi REST client with RSA-PSS authentication for sports markets.

    Falls back to stub mode (returns empty lists) when credentials are missing.

    Usage:
        client = KalshiSportsClient()
        await client.init()
        markets = await client.get_sports_markets(sport="nba")
    """

    def __init__(self, private_key_path: Optional[str] = None, api_key_id: Optional[str] = None) -> None:
        from config.settings import settings
        self._api_key: Optional[str] = api_key_id or getattr(settings, "KALSHI_API_KEY", None) or None
        self._rsa_key_path: Optional[str] = private_key_path or getattr(settings, "KALSHI_RSA_PRIVATE_KEY_PATH", None) or None
        self._private_key = None
        self._initialized: bool = False
        self._base_url: str = _KALSHI_BASE_URL
        # I62: Track key load time for rotation support
        self._key_loaded_at: float = 0.0
        self._key_rotation_interval: int = int(
            getattr(settings, "KALSHI_RSA_KEY_ROTATION_INTERVAL", 86400)
        )

    @property
    def is_available(self) -> bool:
        """True when RSA credentials are configured."""
        return bool(self._api_key and self._rsa_key_path)

    async def init(self) -> None:
        """Load the RSA private key. No network call required for RSA auth."""
        if not self.is_available:
            logger.info(
                "KalshiSportsClient: credentials not configured — running in stub mode. "
                "Set KALSHI_API_KEY and KALSHI_RSA_PRIVATE_KEY_PATH to enable."
            )
            return
        try:
            self._private_key = _load_private_key(self._rsa_key_path)  # type: ignore[arg-type]
            self._initialized = True
            self._key_loaded_at = time.time()  # I62: record when key was loaded
            logger.info("KalshiSportsClient: RSA key loaded", key_path=self._rsa_key_path)
        except Exception as e:
            logger.warning(
                "KalshiSportsClient: failed to load RSA private key",
                path=self._rsa_key_path,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Auth header builder
    # ------------------------------------------------------------------

    def _maybe_rotate_key(self) -> None:
        """
        I62: Reload RSA private key if rotation interval has elapsed.

        Key rotation prevents stale key errors after VPS reboots or key changes.
        Default interval: KALSHI_RSA_KEY_ROTATION_INTERVAL=86400 (24h).
        """
        if not self._rsa_key_path:
            return
        if time.time() - self._key_loaded_at < self._key_rotation_interval:
            return
        try:
            self._private_key = _load_private_key(self._rsa_key_path)
            self._key_loaded_at = time.time()
            logger.info("KalshiSportsClient: RSA key rotated", key_path=self._rsa_key_path)
        except Exception as e:
            logger.warning("KalshiSportsClient: RSA key rotation failed", error=str(e))

    def _auth_headers(self, method: str, path: str) -> Dict[str, str]:
        """Build RSA-PSS auth headers for a Kalshi v2 API request.

        Source: https://docs.kalshi.com/getting_started/api_keys
        Headers: KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP, KALSHI-ACCESS-SIGNATURE
        """
        if not self._initialized or self._private_key is None:
            return {}
        self._maybe_rotate_key()  # I62: rotate if interval elapsed
        timestamp_ms = int(time.time() * 1000)
        signature = _sign_request(self._private_key, method, path, timestamp_ms)
        return {
            "KALSHI-ACCESS-KEY": self._api_key or "",
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Sports market fetching
    # ------------------------------------------------------------------

    async def get_sports_markets(
        self,
        sport: Optional[str] = None,
        limit: int = 200,
    ) -> List[SportsMarketCandidate]:
        """
        Fetch open Kalshi markets and filter to sports categories.

        Args:
            sport: if given, only return markets matching this sport keyword.
            limit: max markets to fetch per request.

        Returns:
            List of SportsMarketCandidate objects.
        """
        if not self._initialized:
            # I41: Warn so operator knows Kalshi is not contributing to arb scanner
            logger.warning(
                "KalshiSportsClient: not initialized — returning empty market list. "
                "Set KALSHI_API_KEY and KALSHI_RSA_PRIVATE_KEY_PATH to enable.",
            )
            return []

        path = f"/trade-api/v2/markets"
        try:
            import httpx
            params: Dict[str, Any] = {"limit": limit, "status": "open"}
            if sport:
                params["event_ticker"] = sport.upper()

            headers = self._auth_headers("GET", path)
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    f"{self._base_url}/markets",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                raw_markets: List[Dict] = r.json().get("markets", [])

            candidates: List[SportsMarketCandidate] = []
            for m in raw_markets:
                ticker = m.get("ticker", "")
                title = m.get("title", "")
                category = m.get("category", "").lower()

                # Filter to sports markets
                if not self._is_sports_market(ticker, title, category):
                    continue
                detected_sport = self._detect_sport(ticker, title, category)
                if sport and detected_sport != sport.lower():
                    continue

                candidates.append(SportsMarketCandidate(
                    platform="kalshi",
                    market_id=ticker,
                    market_type=self._detect_market_type(ticker, title),
                    sport=detected_sport or "unknown",
                    yes_token_id=None,   # Kalshi uses ticker-based pricing
                    no_token_id=None,
                    current_price=self._extract_price(m),
                    title=title,
                    price_fetched_at=time.monotonic(),  # I39: track when price was fetched
                ))

            logger.debug(
                "KalshiSportsClient.get_sports_markets",
                raw=len(raw_markets),
                filtered=len(candidates),
                sport=sport,
            )
            return candidates

        except Exception as e:
            logger.warning("KalshiSportsClient.get_sports_markets failed", error=str(e))
            return []

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_sports_market(ticker: str, title: str, category: str) -> bool:
        ticker_lower = ticker.lower()
        title_lower = title.lower()
        if category in _SPORTS_EVENT_TYPES:
            return True
        for kw in _SPORTS_EVENT_TYPES:
            if kw in ticker_lower or kw in title_lower:
                return True
        # NFL offseason keywords
        for kw in _NFL_OFFSEASON_KEYWORDS:
            if kw in title_lower:
                return True
        return False

    @staticmethod
    def _detect_sport(ticker: str, title: str, category: str) -> str:
        combined = f"{ticker.lower()} {title.lower()} {category.lower()}"
        sport_keywords = [
            ("nba", "nba basketball"),
            ("nfl", "nfl football"),
            ("mlb", "mlb baseball"),
            ("nhl", "nhl hockey"),
            ("tennis", "tennis atp wta wimbledon"),
            ("soccer", "soccer mls epl premier league laliga bundesliga champions"),
        ]
        for sport, keywords in sport_keywords:
            if any(k in combined for k in keywords.split()):
                return sport
        return "unknown"

    @staticmethod
    def _detect_market_type(ticker: str, title: str) -> str:
        t = title.lower()
        if any(k in t for k in ("will win", "champion", "championship")):
            return "futures"
        if any(k in t for k in ("draft", "pick", "selected")):
            return "draft_prop"
        if any(k in t for k in ("signs with", "agree", "free agent")):
            return "free_agent_move"
        if any(k in t for k in ("injury", "will play", "active")):
            return "injury_prop"
        return "moneyline"

    @staticmethod
    def _extract_price(market: Dict) -> Optional[float]:
        """Extract best available price from Kalshi market dict (0–1 scale)."""
        try:
            yes_bid = market.get("yes_bid")
            yes_ask = market.get("yes_ask")
            if yes_bid is not None and yes_ask is not None:
                mid = (float(yes_bid) + float(yes_ask)) / 2.0
                return round(mid / 100.0, 4)  # Kalshi prices in cents
            last = market.get("last_price")
            if last is not None:
                return round(float(last) / 100.0, 4)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Order placement (S-6)
    # ------------------------------------------------------------------

    async def place_order(
        self,
        market_id: str,
        side: str,
        size: float,
        price: float,
    ) -> Dict[str, Any]:
        """
        Place an order on Kalshi.

        Args:
            market_id: Kalshi market ticker.
            side:      "YES" or "NO".
            price:     Price in 0–1 scale (converted to cents for Kalshi API).
            size:      Number of contracts.

        Returns:
            {"success": True/False, "order_id": str, "error": str}
        """
        if not self._initialized or self._private_key is None:
            logger.warning("KalshiSportsClient.place_order: not initialized")
            return {"success": False, "error": "not_initialized"}

        path = "/trade-api/v2/portfolio/orders"
        try:
            import httpx

            # Kalshi API expects: side = "yes" or "no", price in cents (1-99)
            kalshi_side = side.lower() if side else "yes"
            price_cents = max(1, min(99, int(round(price * 100))))

            payload = {
                "ticker": market_id,
                "action": "buy",
                "side": kalshi_side,
                "count": max(1, int(size)),
                "type": "limit",
                "yes_price": price_cents if kalshi_side == "yes" else None,
                "no_price": price_cents if kalshi_side == "no" else None,
            }
            # Remove None values
            payload = {k: v for k, v in payload.items() if v is not None}

            headers = self._auth_headers("POST", path)
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    f"{self._base_url}/portfolio/orders",
                    headers=headers,
                    json=payload,
                )
                if r.status_code in (200, 201):
                    data = r.json()
                    order_id = data.get("order", {}).get("order_id", "")
                    logger.info(
                        "KalshiSportsClient.place_order: success",
                        market_id=market_id,
                        side=kalshi_side,
                        size=int(size),
                        price_cents=price_cents,
                        order_id=order_id,
                    )
                    return {"success": True, "order_id": order_id}
                else:
                    error_msg = r.text[:200]
                    logger.warning(
                        "KalshiSportsClient.place_order: failed",
                        status=r.status_code,
                        error=error_msg,
                    )
                    return {"success": False, "error": f"HTTP {r.status_code}: {error_msg}"}
        except Exception as exc:
            logger.warning("KalshiSportsClient.place_order: exception", error=str(exc))
            return {"success": False, "error": str(exc)}
