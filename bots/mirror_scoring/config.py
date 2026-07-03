"""Governed knob block for the v3 scoring engine.

Every value here is a behavioral parameter of a trading-adjacent system.
Config Tuning Protocol tiers (CLAUDE.md):
  - Tier 1 (threshold tuning): ALPHA, BH_Q, KELLY_LAMBDA, KELLY_CAP,
    MIN_ADVERSE_EVENTS, MIN_EVENTS, F_MIN, RHO_MIN.
  - Tier 2 (universe gating): DELTA_SECONDS, PRICE_STALENESS_MAX_S,
    HOLDOUT_CUTOFF_ISO (changes who is scoreable/admittable).
Change any value => state expected impact + rollback in the commit.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScoringConfig:
    # ── Statistical gates ────────────────────────────────────────────────
    ALPHA: float = 0.05                # one-sided level for bounds/p-values
    MIN_EVENTS: int = 12               # min clusters C for any inference
    MIN_ADVERSE_EVENTS: int = 2        # losing events required to be SIZEABLE
    BH_Q: float = 0.10                 # FDR target across the trader universe
    N_BOOT: int = 999                  # wild-cluster-bootstrap replicates
    BOOT_SEED: int = 20260702          # deterministic bootstrap

    # ── Persistence split (event-blocked calendar holdout) ───────────────
    # Events assigned to train/test by RESOLUTION timestamp; the whole event
    # goes to one side (no straddling). Cutoff is operator-set per run.
    HOLDOUT_CUTOFF_ISO: Optional[str] = None  # None => median resolution time

    # ── Tailability ──────────────────────────────────────────────────────
    DELTA_SECONDS: int = 60            # MB detection+execution latency
    PRICE_STALENESS_MAX_S: int = 30    # max age of the price sample at t+Delta
    RHO_MIN: float = 0.60              # min coverage; below => watch-only
    F_MIN: float = 0.40                # min dollar-weighted deployable fraction
    FEE_ROUNDTRIP: float = 0.02        # flat cost floor per $ (UNVERIFIED vs
                                       # empirical shadow_fills; calibrate there)

    # ── Sizing (shadow weights only — BotBankrollManager owns real sizing) ─
    KELLY_LAMBDA: float = 0.25         # fractional Kelly multiplier
    KELLY_CAP: float = 0.02            # hard cap on per-EVENT weight
    VAR_FLOOR_FROM_PRICE: bool = True  # floor Var_c at pbar*(1-pbar)

    # ── Universe ─────────────────────────────────────────────────────────
    MIN_TRADES_PER_TRADER: int = 20    # raw rows before estimand selection
    PRICE_MIN: float = 0.02            # drop dust prints
    PRICE_MAX: float = 0.98

    # ── Reporting ────────────────────────────────────────────────────────
    REPORT_DIR: str = "scoring_reports"
    UNVERIFIED_LABEL: str = "UNVERIFIED"  # Forbidden Pattern 8 compliance

    extra: dict = field(default_factory=dict)
