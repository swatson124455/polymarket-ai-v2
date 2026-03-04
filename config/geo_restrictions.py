"""
Geographic restrictions — per-platform, per-state availability matrix.

Different prediction market platforms have different geographic restrictions:
  - Polymarket: US-restricted (VPS in allowed jurisdiction handles access)
  - Kalshi: US-only, CFTC-regulated, state-specific restrictions
  - ForecastEx: US-only, CFTC-regulated
  - Coinbase: State-dependent (similar to Kalshi)

This module provides a pre-execution check for OrderGateway.
"""
from __future__ import annotations
from typing import Dict, Optional, Set
from structlog import get_logger

logger = get_logger()

# Platform availability by US state (simplified matrix)
# True = available, False = restricted/banned
# This is a best-effort snapshot; check platform docs for current status
PLATFORM_STATE_MATRIX: Dict[str, Dict[str, bool]] = {
    "polymarket": {
        "_default": True,  # Non-US default
        "_us_default": False,  # US default (restricted)
    },
    "kalshi": {
        "_default": False,  # Non-US default (US-only)
        "_us_default": True,  # US default
        "NY": False,  # NY restricted
    },
    "forecastex": {
        "_default": False,  # Non-US default (US-only)
        "_us_default": True,  # US default
    },
    "coinbase": {
        "_default": False,
        "_us_default": True,
        "NY": True,  # Coinbase has NY BitLicense
        "HI": False,  # Hawaii restricted
    },
}


class GeoRestrictionChecker:
    """
    Check geographic restrictions before placing orders.

    Integrates with OrderGateway as a pre-execution guard.
    """

    def __init__(self, user_state: str = "", user_country: str = "US", enabled: bool = True):
        self._state = user_state.upper().strip()
        self._country = user_country.upper().strip()
        self._enabled = enabled

    def is_platform_allowed(self, platform: str) -> bool:
        """Check if a platform is available for the configured user location."""
        if not self._enabled:
            return True

        platform = platform.lower().strip()
        matrix = PLATFORM_STATE_MATRIX.get(platform)
        if not matrix:
            return True  # Unknown platform: allow (fail at API level)

        if self._country == "US":
            # Check state-specific override first
            if self._state and self._state in matrix:
                return matrix[self._state]
            return matrix.get("_us_default", True)
        else:
            return matrix.get("_default", True)

    def get_allowed_platforms(self) -> Set[str]:
        """Get set of platform names that are available for the user."""
        return {p for p in PLATFORM_STATE_MATRIX if self.is_platform_allowed(p)}

    def check_and_log(self, platform: str) -> bool:
        """Check restriction and log if blocked."""
        allowed = self.is_platform_allowed(platform)
        if not allowed:
            logger.warning(
                "GeoRestriction: %s blocked for %s/%s",
                platform, self._country, self._state or "unknown",
            )
        return allowed
