"""
Probability Engine — converts ensemble temperature forecasts into bucket probabilities.

Core math:
  1. Fit a skew-normal distribution to ensemble members
  2. Integrate CDF across each temperature bucket's bounds
  3. Compare model probabilities against market-implied probabilities
  4. Compute edge and Kelly-criterion position sizing
"""

from __future__ import annotations

import math
import warnings
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Set

from structlog import get_logger

from config.settings import settings

logger = get_logger()

# Import scipy lazily — it's heavy but already installed
try:
    from scipy.stats import skewnorm, norm
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


class WeatherProbabilityEngine:
    """Convert ensemble forecasts into Polymarket bucket probabilities."""

    def __init__(self):
        # Historical bias calibration: station_id → {lead_time_bucket → offset}
        # Populated from weather_calibration table over time
        self._calibration: Dict[str, Dict[int, float]] = {}
        # EMOS parameters: station_id → {lead_time_bucket → (a, b, sigma)}
        # μ_emos = a + b·X̄  (EMOS mean correction; b≠1 corrects systematic slope)
        # σ_emos = sigma    (EMOS spread correction; None = use raw ensemble spread)
        # Identity fallback: (a=0, b=1, sigma=None) ≡ no correction.
        # Requires ≥20 resolved pairs per bucket before activating.
        self._emos: Dict[str, Dict[int, Tuple[float, float, Optional[float]]]] = {}
        # S114: Global EMOS baseline — pooled from all stations' data.
        # Used as fallback for cold stations with no local EMOS.
        # Bühlmann blending: params = w*local + (1-w)*global where w = n/(n+κ).
        self._global_emos: Optional[Tuple[float, float, float]] = None
        # S154: Variance inflation factor for non-EMOS paths.
        self._variance_inflation_factor = float(getattr(settings, "WEATHER_VARIANCE_INFLATION_FACTOR", 1.4))
        # Isotonic tail calibration: (bucket_type, lead_bucket) → List[(model_prob, actual_freq)]
        # Replaces fixed 15% tail discount with data-driven calibration.
        # Requires ≥50 resolved tail events per cell; falls back to 0.85 multiplier until then.
        self._tail_isotonic: Dict[Tuple[str, int], List[Tuple[float, float]]] = {}

    def fit_distribution(
        self,
        ensemble_members: List[float],
        lead_time_hours: float,
        station_id: str = "",
    ) -> Tuple[float, float, float]:
        """Fit a skew-normal distribution to ensemble spread.

        Returns (loc, scale, shape) parameters for scipy.stats.skewnorm.

        Strategy:
          - Compute ensemble mean and std from raw members
          - Apply EMOS calibration: μ = a + b·X̄, σ = sigma (when ≥20 pairs exist)
          - Fall back to simple bias offset (a=bias, b=1, sigma=None) if not yet
          - Use raw ensemble spread as scale when EMOS sigma unavailable
          - Attempt skew-normal MLE fit; fall back to normal if it fails
        """
        if not ensemble_members:
            raise ValueError("Need at least 2 ensemble members")

        # C1: Filter NaN/Inf values — Open-Meteo can return NaN for members
        # beyond model horizon; unfiltered NaN propagates through the entire
        # probability pipeline (mean→variance→CDF→edge→trade).
        clean = [m for m in ensemble_members if math.isfinite(m)]
        if len(clean) < 2:
            raise ValueError(
                f"Need at least 2 finite ensemble members "
                f"(got {len(clean)} after filtering {len(ensemble_members) - len(clean)} NaN/Inf)"
            )

        n = len(clean)
        mean = sum(clean) / n
        variance = sum((x - mean) ** 2 for x in clean) / (n - 1)
        std = max(variance ** 0.5, 0.5)  # Floor at 0.5° to avoid overconfidence

        # Apply EMOS calibration (or fall back to simple bias offset)
        # μ_emos = a + b·X̄  — corrects both mean bias and systematic slope
        # σ_emos = sigma     — corrects spread (underdispersion common in raw ensembles)
        emos_a, emos_b, emos_sigma = self._get_emos_params(station_id, lead_time_hours)
        corrected_mean = emos_a + emos_b * mean

        # Use EMOS sigma when available; otherwise use raw ensemble spread with
        # S154 variance inflation for known NWP underdispersion.
        # EMOS sigma already captures the spread-skill gap for calibrated stations.
        # TODO: Upgrade to heteroscedastic EMOS (σ = c + d·S) for locally-calibrated
        # stations where fixed sigma doesn't track day-to-day spread variation.
        if emos_sigma is not None:
            effective_std = max(emos_sigma, 0.5)
        else:
            # S154: Inflate raw ensemble spread for uncalibrated stations/leads.
            # NWP ensembles underdispersed by 1.3-2.0x (MeteoSwiss, Gneiting 2005).
            _vif = getattr(self, "_variance_inflation_factor", 1.4)
            effective_std = max(std * _vif, 0.5)

        # EMOS loc shift applied to skewnorm-fitted location
        loc_shift = corrected_mean - mean  # = emos_a + (emos_b - 1) * mean

        # Try skew-normal fit via MLE
        shape = 0.0  # Default: symmetric normal
        if SCIPY_AVAILABLE and n >= 30:
            try:
                with warnings.catch_warnings():
                    # Suppress scipy precision-loss warning for nearly-identical members;
                    # the fallback to normal distribution handles this case correctly.
                    warnings.simplefilter("ignore", RuntimeWarning)
                    a, loc, scale = skewnorm.fit(clean)
                # Apply EMOS corrections: shift loc, use EMOS sigma if available
                loc_emos = loc + loc_shift
                # S154: Inflate non-EMOS scale for underdispersion
                if emos_sigma is not None:
                    scale_emos = max(emos_sigma, 0.5)
                else:
                    scale_emos = max(scale * self._variance_inflation_factor, 0.5)
                # Sanity: reject absurd scale; clip extreme shape (preserves direction)
                if 0.1 < scale_emos < 30.0:
                    a_clipped = max(-4.0, min(4.0, a))
                    if abs(a) > 4.0:
                        logger.debug(
                            "weather_shape_clipped",
                            station=station_id,
                            alpha_raw=round(a, 3),
                            alpha_clipped=round(a_clipped, 3),
                        )
                    return (loc_emos, scale_emos, a_clipped)
            except Exception:
                pass  # Fall through to normal

        return (corrected_mean, effective_std, shape)

    def bucket_probabilities(
        self,
        loc: float,
        scale: float,
        shape: float,
        buckets: list,
        lead_time_hours: float = 48.0,
    ) -> Dict[str, float]:
        """Integrate distribution across each bucket's bounds.

        Uses 0.5-degree offsets on range bucket boundaries for proper coverage:
          - "between 48-49" → P(47.5 ≤ T < 49.5)
          - "42 or below" → P(T < 42.5)
          - "55 or higher" → P(T ≥ 54.5)
          - "exact 10" → P(9.5 ≤ T < 10.5)

        Returns {market_id: probability} dict.
        """
        if not SCIPY_AVAILABLE:
            return self._bucket_probabilities_fallback(loc, scale, buckets, lead_time_hours)

        if abs(shape) < 0.01:
            dist = norm(loc=loc, scale=scale)
        else:
            dist = skewnorm(shape, loc=loc, scale=scale)

        probs: Dict[str, float] = {}
        for b in buckets:
            p = self._integrate_bucket(dist, b)
            # S132: Tail discount REMOVED — YES side is net profitable (+$815).
            probs[b.market_id] = max(0.001, min(0.999, p))  # Clamp to avoid 0/1

        # Normalize so probabilities sum to 1.0
        total = sum(probs.values())
        if total > 0.01 and abs(total - 1.0) > 0.01:
            for mid in probs:
                probs[mid] /= total
        elif total <= 0.01 and probs:
            # M1 fix: Degenerate distribution — ensemble is too tight for
            # any bucket to get meaningful probability.  Return empty rather
            # than uniform — uniform creates fake 45%+ edges on tail markets
            # (the root cause of the 10× re-entry doom loop on 2¢ markets).
            logger.debug(
                "weatherbot_degenerate_distribution",
                total=round(total, 6),
                n_buckets=len(probs),
            )
            return {}

        return probs

    @staticmethod
    def _integrate_bucket(dist, bucket) -> float:
        """CDF integration for a single bucket."""
        btype = bucket.bucket_type

        if btype == "at_or_below":
            # P(T ≤ high_bound)
            return float(dist.cdf(bucket.high_bound + 0.5))

        elif btype == "at_or_higher":
            # P(T ≥ low_bound)
            return float(1.0 - dist.cdf(bucket.low_bound - 0.5))

        elif btype == "range":
            # P(low - 0.5 ≤ T < high + 0.5)
            upper = float(dist.cdf(bucket.high_bound + 0.5))
            lower = float(dist.cdf(bucket.low_bound - 0.5))
            return max(0.0, upper - lower)

        elif btype == "exact":
            # P(val - 0.5 ≤ T < val + 0.5)
            upper = float(dist.cdf(bucket.high_bound + 0.5))
            lower = float(dist.cdf(bucket.low_bound - 0.5))
            return max(0.0, upper - lower)

        return 0.0

    def _bucket_probabilities_fallback(
        self, loc: float, scale: float, buckets: list,
        lead_time_hours: float = 48.0,
    ) -> Dict[str, float]:
        """Fallback using manual normal CDF if scipy unavailable."""
        probs: Dict[str, float] = {}
        for b in buckets:
            p = self._normal_cdf_bucket(loc, scale, b)
            # S132: Tail discount REMOVED
            probs[b.market_id] = max(0.001, min(0.999, p))
        total = sum(probs.values())
        if total > 0.01 and abs(total - 1.0) > 0.01:
            for mid in probs:
                probs[mid] /= total
        elif total <= 0.01 and probs:
            # M1 parity: degenerate distribution — return empty rather than
            # uniform to avoid fake 45%+ edges on tail markets.
            return {}
        return probs

    @staticmethod
    def _normal_cdf_bucket(loc: float, scale: float, bucket) -> float:
        """Manual normal CDF integration using math.erf."""
        def _cdf(x: float) -> float:
            return 0.5 * (1.0 + math.erf((x - loc) / (scale * math.sqrt(2))))

        btype = bucket.bucket_type
        if btype == "at_or_below":
            return _cdf(bucket.high_bound + 0.5)
        elif btype == "at_or_higher":
            return 1.0 - _cdf(bucket.low_bound - 0.5)
        elif btype in ("range", "exact"):
            return max(0.0, _cdf(bucket.high_bound + 0.5) - _cdf(bucket.low_bound - 0.5))
        return 0.0

    def compute_edges(
        self,
        model_probs: Dict[str, float],
        market_prices: Dict[str, float],
    ) -> List[Dict]:
        """Compute edge = model_prob - market_price for each bucket.

        Returns list sorted by |edge| descending, with side determination:
          - edge > 0 → model says bucket is underpriced → BUY YES
          - edge < 0 → model says bucket is overpriced → BUY NO
        """
        edges = []
        for market_id, model_prob in model_probs.items():
            market_price = market_prices.get(market_id, 0.0)
            if market_price <= 0.0 or market_price >= 1.0:
                continue

            edge = model_prob - market_price
            side = "YES" if edge > 0 else "NO"

            edges.append({
                "market_id": market_id,
                "model_prob": round(model_prob, 4),
                "market_price": round(market_price, 4),
                "edge": round(edge, 4),
                "abs_edge": round(abs(edge), 4),
                "side": side,
            })

        edges.sort(key=lambda e: e["abs_edge"], reverse=True)
        return edges

    @staticmethod
    def kelly_fraction(
        edge: float,
        model_prob: float,
        market_price: float,
        kelly_mult: float = 0.25,
    ) -> float:
        """Fractional Kelly sizing for a weather bucket bet.

        For YES: f* = kelly_mult * (p*b - q) / b  where b = (1/price - 1), p = model_prob, q = 1-p
        For NO:  same formula with flipped probabilities

        Returns fraction of capital to bet (0.0 if negative edge).
        """
        abs_edge = abs(edge)
        if abs_edge < 0.001:
            return 0.0

        # Guard: extreme prices produce degenerate Kelly fractions
        if market_price < 0.02 or market_price > 0.98:
            return 0.0

        if edge > 0:
            # Buying YES at market_price
            p = model_prob
            b = (1.0 / market_price) - 1.0 if market_price > 0 else 0.0
        else:
            # Buying NO at (1 - market_price)
            p = 1.0 - model_prob
            no_price = 1.0 - market_price
            b = (1.0 / no_price) - 1.0 if no_price > 0 else 0.0

        if b <= 0:
            return 0.0

        q = 1.0 - p
        kelly = (p * b - q) / b
        if kelly <= 0:
            return 0.0

        return min(kelly * kelly_mult, kelly_mult)  # Cap at kelly_mult

    # S134: _get_tail_discount() removed (S132 dampener deletion)

    def load_tail_calibration(
        self,
        tail_data: Dict[Tuple[str, int], List[Tuple[float, float]]],
    ) -> None:
        """Load isotonic tail calibration data.

        Args:
            tail_data: {(bucket_type, lead_bucket) → [(model_prob, actual_freq), ...]}
        """
        self._tail_isotonic = tail_data
        logger.info(
            "weather_tail_calibration_loaded",
            cells=len(tail_data),
            total_points=sum(len(v) for v in tail_data.values()),
        )

    def _get_bias_offset(self, station_id: str, lead_time_hours: float) -> float:
        """Look up historical forecast bias for this station + lead time.

        Returns additive offset (actual - forecast average). Positive means
        forecasts tend to underestimate.
        """
        station_cal = self._calibration.get(station_id)
        if not station_cal:
            return 0.0
        # Bucket lead time into 6-hour bins
        bucket = int(lead_time_hours // 6) * 6
        return station_cal.get(bucket, 0.0)

    def load_calibration(self, calibration_data: Dict[str, Dict[int, float]]) -> None:
        """Load calibration offsets from external source (DB or backtest)."""
        self._calibration = calibration_data
        logger.info("weather_calibration_loaded", stations=len(calibration_data))

    def _get_emos_params(
        self, station_id: str, lead_time_hours: float
    ) -> Tuple[float, float, Optional[float]]:
        """Return EMOS (a, b, sigma) for station + lead time.

        Returns:
            a     — intercept (additive mean correction)
            b     — slope (multiplicative mean correction; b≠1 corrects slope bias)
            sigma — spread correction (°F/°C); None = use raw ensemble spread

        Fallback chain (S114):
          1. Local EMOS (station-specific, ≥20 pairs per bucket)
          2. Global EMOS (pooled from all stations)
          3. Simple bias offset (a=bias, b=1, sigma=None)
          4. Identity (a=0, b=1, sigma=None) = no correction
        """
        station_emos = self._emos.get(station_id)
        if station_emos:
            bucket = int(lead_time_hours // 6) * 6
            params = station_emos.get(bucket)
            if params is not None:
                return params

        # S114 Item 3: Fall back to global EMOS baseline
        if self._global_emos is not None:
            return self._global_emos

        # Fall back to simple bias offset (backward compat)
        bias = self._get_bias_offset(station_id, lead_time_hours)
        return (bias, 1.0, None)

    def load_emos_calibration(
        self,
        emos_data: Dict[str, Dict[int, Tuple[float, float, Optional[float]]]],
    ) -> None:
        """Load EMOS (a, b, sigma) parameters from external source (DB or backtest).

        Called after load_calibration() once ≥20 resolved pairs per bucket exist.
        EMOS takes precedence over simple bias offset in _get_emos_params().
        """
        self._emos = emos_data
        logger.info("weather_emos_calibration_loaded", stations=len(emos_data))

    def load_global_emos(
        self,
        global_params: Tuple[float, float, float],
    ) -> None:
        """S114: Load global EMOS baseline (pooled from all stations).

        Used as fallback for cold stations with no local EMOS data.
        """
        self._global_emos = global_params

    def compute_nbm_benchmark(
        self,
        nbm_high: float,
        buckets: list,
        market_prices: Dict[str, float],
        lead_time_hours: float = 48.0,
        disagree_threshold: float = 0.15,
    ) -> Dict[str, Dict]:
        """P2: Compute NBM CDF per bucket and flag high-conviction disagreements.

        NBM provides a calibrated point forecast (MAE 0.8-1.5°F at day 1-3).
        We model it as N(nbm_high, sigma) where sigma scales with lead time:
          - Day 1 (≤24h):  sigma = 1.5°F
          - Day 2 (24-48h): sigma = 2.5°F
          - Day 3 (48-72h): sigma = 3.5°F
          - Day 4+ (>72h):  sigma = 5.0°F

        Returns {market_id: {"nbm_prob": float, "market_price": float,
                 "nbm_edge": float, "high_conviction": bool}}
        for buckets where |nbm_prob - market_price| >= disagree_threshold.
        """
        # Lead-time-dependent sigma (NBM MAE grows with forecast range)
        if lead_time_hours <= 24.0:
            sigma = 1.5
        elif lead_time_hours <= 48.0:
            sigma = 2.5
        elif lead_time_hours <= 72.0:
            sigma = 3.5
        else:
            sigma = 5.0

        # Compute NBM-implied probabilities using normal CDF
        nbm_probs: Dict[str, float] = {}
        for b in buckets:
            p = self._normal_cdf_bucket(nbm_high, sigma, b)
            nbm_probs[b.market_id] = max(0.001, min(0.999, p))

        # Normalize
        total = sum(nbm_probs.values())
        if total > 0.01 and abs(total - 1.0) > 0.01:
            for mid in nbm_probs:
                nbm_probs[mid] /= total

        # Compare against market prices, flag disagreements
        signals: Dict[str, Dict] = {}
        for market_id, nbm_prob in nbm_probs.items():
            mkt_price = market_prices.get(market_id, 0.0)
            if mkt_price <= 0.0 or mkt_price >= 1.0:
                continue
            nbm_edge = nbm_prob - mkt_price
            if abs(nbm_edge) >= disagree_threshold:
                signals[market_id] = {
                    "nbm_prob": round(nbm_prob, 4),
                    "market_price": round(mkt_price, 4),
                    "nbm_edge": round(nbm_edge, 4),
                    "high_conviction": True,
                }
        return signals

    @staticmethod
    def apply_climate_prior(
        loc: float,
        scale: float,
        clim_mean: float,
        clim_std: float,
        lead_time_hours: float,
    ) -> Tuple[float, float]:
        """Blend ensemble (loc, scale) toward climate normal based on lead time.

        At short lead times (≤72h), ensemble forecasts are skilled and no blending
        is needed. At longer lead times, model skill degrades and the forecast
        should be pulled toward climatology to prevent overconfident long-range bets.

        Blend schedule:
          ≤72h:   weight = 0.0 (pure ensemble)
          72-168h: weight ramps linearly from 0.0 to 0.4
          ≥168h:  weight = 0.4 (40% climatology, 60% ensemble)

        Returns (blended_loc, blended_scale).
        """
        if lead_time_hours <= 72.0:
            return (loc, scale)

        # Linear ramp from 0.0 at 72h to 0.4 at 168h
        w = min(0.4, 0.4 * (lead_time_hours - 72.0) / (168.0 - 72.0))

        blended_loc = (1.0 - w) * loc + w * clim_mean
        # S155: Mixture variance with cross-term for mean separation.
        # Missing w*(1-w)*(m1-m2)² caused underestimated spread when
        # ensemble and climatology disagree at long lead times.
        blended_scale = max(
            ((1.0 - w) * scale ** 2 + w * clim_std ** 2
             + w * (1.0 - w) * (loc - clim_mean) ** 2) ** 0.5,
            0.5,  # Floor (degrees Fahrenheit)
        )

        return (blended_loc, blended_scale)
