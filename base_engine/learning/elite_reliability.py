"""
Bayesian Elite Reliability (Element 4).

Per-user Beta(alpha, beta) from resolved trades. All counts from data; no invented numbers.
Prior: Beta(1, 1) = "I know nothing" for users with no history.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from structlog import get_logger

logger = get_logger()


class EliteReliabilityTracker:
    """
    Tracks per-user reliability as Beta(correct+1, incorrect+1) from historical
    resolved trades. likelihood_ratio(user, side) returns odds for that user/side.
    """

    def __init__(self, db: Any, lookback_days: int = 365):
        self.db = db
        self.lookback_days = lookback_days
        self._cache: Dict[str, Dict[str, float]] = {}  # address -> { alpha_yes, beta_yes, alpha_no, beta_no }

    async def refresh(self) -> None:
        """Load per-user resolution counts from DB and compute Beta params."""
        if not self.db or not getattr(self.db, "get_user_resolution_counts", None):
            self._cache = {}
            return
        rows = await self.db.get_user_resolution_counts(lookback_days=self.lookback_days)
        self._cache = {}
        for r in rows:
            addr = (r.get("user_address") or "").strip()
            if not addr:
                continue
            # Beta(alpha, beta): alpha = correct+1, beta = incorrect+1 (prior Beta(1,1))
            yes_correct = int(r.get("yes_correct") or 0)
            yes_total = int(r.get("yes_total") or 0)
            no_correct = int(r.get("no_correct") or 0)
            no_total = int(r.get("no_total") or 0)
            yes_incorrect = yes_total - yes_correct
            no_incorrect = no_total - no_correct
            self._cache[addr.lower()] = {
                "alpha_yes": yes_correct + 1,
                "beta_yes": yes_incorrect + 1,
                "alpha_no": no_correct + 1,
                "beta_no": no_incorrect + 1,
                "yes_total": yes_total,
                "no_total": no_total,
            }
        logger.debug("Elite reliability refreshed", n_users=len(self._cache))

    def _get_beta(self, address: str, side: str) -> Tuple[float, float]:
        """Return (alpha, beta) for this user and side. Side is YES or NO."""
        key = (address or "").strip().lower()
        rec = self._cache.get(key)
        if not rec:
            return (1.0, 1.0)  # Beta(1,1) = know nothing
        side_upper = (side or "YES").upper()
        if side_upper in ("YES", "BUY"):
            return (rec["alpha_yes"], rec["beta_yes"])
        return (rec["alpha_no"], rec["beta_no"])

    def mean(self, address: str, side: str) -> float:
        """Posterior mean accuracy for this user/side (alpha/(alpha+beta))."""
        a, b = self._get_beta(address, side)
        if a + b <= 0:
            return 0.5
        return a / (a + b)

    def likelihood_ratio(self, address: str, side: str) -> float:
        """
        Odds of outcome given this user traded this side: mean / (1 - mean).
        When they trade YES, how much more likely is YES? Returns odds (e.g. 1.67).
        For unknown user or no data, returns 1.0 (no update).
        """
        a, b = self._get_beta(address, side)
        if a + b <= 2:
            return 1.0  # Prior only (1,1) -> mean 0.5 -> odds 1.0
        mean = a / (a + b)
        if mean <= 0 or mean >= 1:
            return 1.0
        return mean / (1 - mean)

    def log_likelihood_ratio(self, address: str, side: str) -> float:
        """Log-odds for Bayesian belief update: log(likelihood_ratio)."""
        lr = self.likelihood_ratio(address, side)
        if lr <= 0:
            return 0.0
        return math.log(lr)

    def equivalent_samples(self, address: str, side: str) -> float:
        """Alpha + beta - 2 = effective sample count (for width / confidence)."""
        a, b = self._get_beta(address, side)
        return max(0, a + b - 2)


def beta_mean(alpha: float, beta: float) -> float:
    """Posterior mean of Beta(alpha, beta)."""
    if alpha + beta <= 0:
        return 0.5
    return alpha / (alpha + beta)
