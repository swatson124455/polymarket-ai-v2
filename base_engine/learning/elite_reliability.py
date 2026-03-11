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
        self._cat_cache: Dict[Tuple[str, str], Dict[str, float]] = {}  # (address, category) -> same shape

    @staticmethod
    def _build_beta_rec(r: Dict[str, Any]) -> Dict[str, float]:
        """Convert a row of resolution counts into Beta params."""
        yes_correct = int(r.get("yes_correct") or 0)
        yes_total = int(r.get("yes_total") or 0)
        no_correct = int(r.get("no_correct") or 0)
        no_total = int(r.get("no_total") or 0)
        yes_incorrect = yes_total - yes_correct
        no_incorrect = no_total - no_correct
        return {
            "alpha_yes": yes_correct + 1,
            "beta_yes": yes_incorrect + 1,
            "alpha_no": no_correct + 1,
            "beta_no": no_incorrect + 1,
            "yes_total": yes_total,
            "no_total": no_total,
        }

    async def refresh(self) -> None:
        """Load per-user resolution counts from DB and compute Beta params."""
        if not self.db or not getattr(self.db, "get_user_resolution_counts", None):
            self._cache = {}
            self._cat_cache = {}
            return
        rows = await self.db.get_user_resolution_counts(lookback_days=self.lookback_days)
        self._cache = {}
        for r in rows:
            addr = (r.get("user_address") or "").strip()
            if not addr:
                continue
            self._cache[addr.lower()] = self._build_beta_rec(r)

        # Per-category cache
        self._cat_cache = {}
        if getattr(self.db, "get_user_resolution_counts_by_category", None):
            try:
                cat_rows = await self.db.get_user_resolution_counts_by_category(
                    lookback_days=self.lookback_days
                )
                for r in cat_rows:
                    addr = (r.get("user_address") or "").strip()
                    cat = (r.get("category") or "unknown").strip().lower()
                    if not addr:
                        continue
                    self._cat_cache[(addr.lower(), cat)] = self._build_beta_rec(r)
            except Exception as e:
                logger.debug("Category reliability load failed: %s", e)

        logger.debug("Elite reliability refreshed", n_users=len(self._cache),
                     n_category_entries=len(self._cat_cache))

    def _get_beta(self, address: str, side: str,
                  category: Optional[str] = None,
                  min_cat_samples: int = 5) -> Tuple[float, float]:
        """Return (alpha, beta) for this user and side. Side is YES or NO.

        If *category* is provided and the user has >= min_cat_samples resolved
        trades in that category, returns category-specific Beta params.
        Otherwise falls back to the user's overall stats.
        """
        key = (address or "").strip().lower()
        side_upper = (side or "YES").upper()
        is_yes = side_upper in ("YES", "BUY")

        # Try category-specific first
        if category and self._cat_cache:
            cat_key = (key, category.strip().lower())
            cat_rec = self._cat_cache.get(cat_key)
            if cat_rec:
                cat_samples = (cat_rec["yes_total"] + cat_rec["no_total"])
                if cat_samples >= min_cat_samples:
                    if is_yes:
                        return (cat_rec["alpha_yes"], cat_rec["beta_yes"])
                    return (cat_rec["alpha_no"], cat_rec["beta_no"])

        # Fallback: overall per-user stats
        rec = self._cache.get(key)
        if not rec:
            return (1.0, 1.0)  # Beta(1,1) = know nothing
        if is_yes:
            return (rec["alpha_yes"], rec["beta_yes"])
        return (rec["alpha_no"], rec["beta_no"])

    def mean(self, address: str, side: str, **kwargs: Any) -> float:
        """Posterior mean accuracy for this user/side (alpha/(alpha+beta))."""
        a, b = self._get_beta(address, side, **kwargs)
        if a + b <= 0:
            return 0.5
        return a / (a + b)

    def likelihood_ratio(self, address: str, side: str, **kwargs: Any) -> float:
        """
        Odds of outcome given this user traded this side: mean / (1 - mean).
        When they trade YES, how much more likely is YES? Returns odds (e.g. 1.67).
        For unknown user or no data, returns 1.0 (no update).

        Accepts optional *category* kwarg for per-category Beta lookup.
        """
        a, b = self._get_beta(address, side, **kwargs)
        if a + b <= 2:
            return 1.0  # Prior only (1,1) -> mean 0.5 -> odds 1.0
        mean = a / (a + b)
        if mean <= 0 or mean >= 1:
            return 1.0
        return mean / (1 - mean)

    def log_likelihood_ratio(self, address: str, side: str, **kwargs: Any) -> float:
        """Log-odds for Bayesian belief update: log(likelihood_ratio)."""
        lr = self.likelihood_ratio(address, side, **kwargs)
        if lr <= 0:
            return 0.0
        return math.log(lr)

    def equivalent_samples(self, address: str, side: str, **kwargs: Any) -> float:
        """Alpha + beta - 2 = effective sample count (for width / confidence)."""
        a, b = self._get_beta(address, side, **kwargs)
        return max(0, a + b - 2)


def beta_mean(alpha: float, beta: float) -> float:
    """Posterior mean of Beta(alpha, beta)."""
    if alpha + beta <= 0:
        return 0.5
    return alpha / (alpha + beta)
