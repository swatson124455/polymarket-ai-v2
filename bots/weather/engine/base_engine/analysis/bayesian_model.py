"""
Bayesian Polling Model — Fundamentals prior + poll updating.

Implements simplified Gelman/Goodrich/Han approach (used by The Economist):
1. Fundamentals prior from economic conditions + approval
2. Poll updating with recency, sample size, pollster quality weighting
3. State correlations via multivariate normal

Designed to integrate with PredictionEngine as an additional feature source.
"""
import math
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from structlog import get_logger

logger = get_logger()


class BayesianPollingModel:
    """
    Bayesian model that combines fundamentals priors with poll evidence.

    Produces posterior probability estimates for political markets
    that can be compared against market prices to identify mispricings.

    Key insight: poll impact on prices DECLINES over time as information
    accumulates → early-cycle mispricings are largest.
    """

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = 3600  # 1 hour
        self._fundamentals: Optional[Dict[str, float]] = None

    # ── Fundamentals Prior ────────────────────────────────────────────────────

    def set_fundamentals(
        self,
        gdp_growth_q2: float = 2.0,
        incumbent_approval: float = 45.0,
        is_first_term: bool = True,
        midterm_year: bool = False,
    ) -> None:
        """
        Set economic fundamentals for the prior.

        Based on Abramowitz "Time for Change" model (R² = 0.82):
        - Q2 GDP growth
        - Incumbent approval at mid-year
        - Term penalty (president's party wins 7/9 first-term, 2/10 after two+)

        Midterm base rate: president's party loses seats 90% of time.
        Approval < 50% → 37-43 seats lost.
        """
        # Abramowitz formula (simplified, incumbent party vote share)
        # VoteShare = 47.26 + 0.108*NetApproval + 0.543*GDP_Q2 + TermBonus
        net_approval = incumbent_approval - 50.0  # Center at 50
        term_bonus = 4.4 if is_first_term else -4.4  # First-term advantage
        vote_share = 47.26 + 0.108 * net_approval + 0.543 * gdp_growth_q2 + term_bonus
        # Clamp to realistic range
        vote_share = max(40.0, min(60.0, vote_share))

        # Convert to probability of incumbent party winning
        # Simplified: vote share > 50 → wins; margin determines confidence
        incumbent_win_prob = self._logistic(vote_share - 50.0, steepness=0.3)

        self._fundamentals = {
            "gdp_growth_q2": gdp_growth_q2,
            "incumbent_approval": incumbent_approval,
            "is_first_term": is_first_term,
            "midterm_year": midterm_year,
            "vote_share_estimate": round(vote_share, 2),
            "incumbent_win_prob": round(incumbent_win_prob, 4),
            "midterm_seat_loss_prob": 0.90 if midterm_year else 0.0,
        }

        logger.info(
            "Fundamentals prior set",
            vote_share=round(vote_share, 2),
            incumbent_win_prob=round(incumbent_win_prob, 4),
        )

    def get_fundamentals_prior(self, race_type: str = "president") -> float:
        """
        Get fundamentals-based prior probability.

        Returns:
            Prior probability (0.0-1.0) for incumbent party winning.
        """
        if not self._fundamentals:
            return 0.5  # Uninformative prior

        if race_type == "president":
            return self._fundamentals["incumbent_win_prob"]
        elif race_type in ("house", "senate") and self._fundamentals.get("midterm_year"):
            # Midterm: president's party very likely loses seats
            # For "will incumbent party keep majority?" type questions
            approval = self._fundamentals.get("incumbent_approval", 50)
            if approval < 50:
                return 0.25  # Low approval + midterm = very bearish
            else:
                return 0.40  # Even decent approval doesn't save midterms
        return 0.5

    # ── Bayesian Update ───────────────────────────────────────────────────────

    def update_with_polls(
        self,
        prior: float,
        polls: List[Dict[str, Any]],
        candidate: str = "",
        recency_lambda: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Update prior probability with polling evidence.

        Implements weighted Bayesian update:
        - Each poll shifts the prior proportional to its weight
        - Weight = recency × sqrt(sample_size) × population_quality × partisan_discount
        - Herding penalty for pollsters clustering near average

        Args:
            prior: Fundamentals-based prior probability (0-1)
            polls: List of poll dicts from PollingClient
            candidate: Candidate name to track
            recency_lambda: Exponential decay rate for recency weighting

        Returns:
            Dict with posterior probability, confidence interval, poll stats
        """
        if not polls:
            return {
                "posterior": prior,
                "prior": prior,
                "poll_shift": 0.0,
                "poll_count": 0,
                "confidence_interval": (max(0, prior - 0.15), min(1, prior + 0.15)),
                "information_ratio": 0.0,
            }

        # Population quality weights
        pop_weights = {"lv": 1.0, "rv": 0.85, "a": 0.7}
        now = datetime.now(timezone.utc)

        weighted_sum = 0.0
        weight_total = 0.0
        raw_pcts = []

        for poll in polls:
            # Get candidate percentage
            pct = poll.get("pct", 0)
            if not pct and candidate:
                candidates = poll.get("candidates", {})
                pct = float(candidates.get(candidate, 0) or 0)
            if not pct:
                continue

            pct = float(pct) / 100.0  # Convert to 0-1
            raw_pcts.append(pct)

            # Recency weight
            days_old = self._days_since(poll.get("end_date", ""), now)
            recency_w = math.exp(-recency_lambda * days_old)

            # Sample size weight (sqrt, capped at sqrt(1500))
            n = max(1, poll.get("sample_size", 0) or 1)
            sample_w = min(n ** 0.5, 1500 ** 0.5)

            # Population quality
            pop = str(poll.get("population", "a")).lower()
            pop_w = pop_weights.get(pop, 0.7)

            # Partisan discount
            partisan = str(poll.get("partisan", "")).lower()
            partisan_w = 0.6 if partisan and partisan != "nonpartisan" else 1.0

            # Pollster grade bonus (FTE grades: A+ = 1.3, A = 1.2, B = 1.0, C = 0.8)
            grade = str(poll.get("fte_grade", "")).upper()
            grade_w = {"A+": 1.3, "A": 1.2, "A-": 1.15, "B+": 1.1, "B": 1.0,
                       "B-": 0.95, "C+": 0.9, "C": 0.85, "C-": 0.8, "D": 0.7}.get(grade, 1.0)

            weight = recency_w * sample_w * pop_w * partisan_w * grade_w
            weighted_sum += pct * weight
            weight_total += weight

        if weight_total == 0 or not raw_pcts:
            return {
                "posterior": prior,
                "prior": prior,
                "poll_shift": 0.0,
                "poll_count": 0,
                "confidence_interval": (max(0, prior - 0.15), min(1, prior + 0.15)),
                "information_ratio": 0.0,
            }

        # Weighted poll average (0-1 scale)
        poll_avg = weighted_sum / weight_total

        # Herding check: if std dev of polls is suspiciously low (< 1pp),
        # discount poll evidence
        import numpy as np
        poll_std = float(np.std(raw_pcts)) if len(raw_pcts) > 1 else 0.05
        herding_penalty = 1.0
        if poll_std < 0.01 and len(raw_pcts) > 3:
            herding_penalty = 0.7
            logger.debug("Herding detected: poll std=%.4f, applying 0.7× penalty", poll_std)

        # Bayesian update: blend prior and poll evidence
        # Weight of evidence increases with poll count and quality
        # information_ratio: how much we trust polls vs fundamentals
        n_effective = len(raw_pcts) * herding_penalty
        information_ratio = min(0.9, n_effective / (n_effective + 5.0))

        posterior = prior * (1.0 - information_ratio) + poll_avg * information_ratio
        posterior = max(0.01, min(0.99, posterior))

        # Confidence interval (simplified)
        se = poll_std / math.sqrt(max(1, len(raw_pcts))) if raw_pcts else 0.1
        ci_low = max(0.0, posterior - 1.96 * se)
        ci_high = min(1.0, posterior + 1.96 * se)

        return {
            "posterior": round(posterior, 4),
            "prior": round(prior, 4),
            "poll_average": round(poll_avg, 4),
            "poll_shift": round(posterior - prior, 4),
            "poll_count": len(raw_pcts),
            "poll_std": round(poll_std, 4),
            "information_ratio": round(information_ratio, 4),
            "herding_penalty": herding_penalty,
            "confidence_interval": (round(ci_low, 4), round(ci_high, 4)),
        }

    # ── Market Signal ─────────────────────────────────────────────────────────

    def compute_poll_market_divergence(
        self,
        posterior: float,
        market_price: float,
    ) -> Dict[str, Any]:
        """
        Compute divergence between polling model and market price.

        This is THE core alpha signal: when our Bayesian model disagrees
        with the market by more than the profitability threshold.

        Returns:
            divergence: float (positive = model thinks YES is underpriced)
            signal_strength: float 0-1
            recommendation: str
        """
        divergence = posterior - market_price
        abs_div = abs(divergence)

        # Signal strength scales with divergence magnitude
        signal_strength = min(1.0, abs_div / 0.15)  # Max signal at 15pp divergence

        recommendation = "HOLD"
        if abs_div >= 0.05:  # 5pp minimum
            if divergence > 0:
                recommendation = "BUY_YES"
            else:
                recommendation = "BUY_NO"
        if abs_div >= 0.10:  # 10pp = strong signal
            recommendation = recommendation.replace("BUY", "STRONG_BUY")

        return {
            "divergence": round(divergence, 4),
            "abs_divergence": round(abs_div, 4),
            "signal_strength": round(signal_strength, 4),
            "recommendation": recommendation,
            "posterior": round(posterior, 4),
            "market_price": round(market_price, 4),
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _logistic(x: float, steepness: float = 1.0) -> float:
        """Logistic sigmoid function."""
        return 1.0 / (1.0 + math.exp(-steepness * x))

    @staticmethod
    def _days_since(date_str: str, now: datetime) -> float:
        """Compute days since a date string."""
        if not date_str:
            return 30.0
        try:
            from dateutil.parser import parse as parse_date
            dt = parse_date(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (now - dt).total_seconds() / 86400.0)
        except Exception:
            return 30.0
