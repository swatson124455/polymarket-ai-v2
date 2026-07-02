"""The acceptance gate: PRECISE-model pass required (operator decision 2026-07-02).

A candidate rule is ADMITTED only if, on signals filled by the PRECISE model:
  * at least MIN_PRECISE_MARKETS distinct markets were replayed, and
  * bootstrap P(mean edge > 0) >= P_EDGE_MIN, resampling MARKETS (clusters),
    never individual signals — per-signal resampling would pseudo-replicate
    correlated legs.
The COARSE model result is reported alongside as a breadth check; it can
DEMOTE confidence (flag divergence) but can never admit a rule by itself.

Skeptical prior (MB_REBUILD_PLAN.md §2): the expected outcome is FAIL — the
old strategy died of unmeasured edge; the gate exists to make that death
impossible to repeat silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from bots.mirror_backtest.fill_models import COARSE, PRECISE
from bots.mirror_backtest.replay import ReplayReport


@dataclass
class GateConfig:
    P_EDGE_MIN: float = 0.95          # required bootstrap P(edge>0), precise model
    MIN_PRECISE_MARKETS: int = 30     # minimum distinct precise-filled markets
    N_BOOT: int = 2000
    SEED: int = 20260702
    COARSE_DIVERGENCE_WARN: float = 0.05  # |precise-coarse| mean-edge gap to flag


@dataclass
class GateResult:
    admitted: bool
    p_edge_precise: float
    mean_edge_precise: float
    n_precise_markets: int
    p_edge_coarse: float
    mean_edge_coarse: float
    n_coarse_markets: int
    reasons: list = field(default_factory=list)
    label: str = "UNVERIFIED"   # data-tier claims stay UNVERIFIED until M0-DB


def _per_market_edges(report: ReplayReport, model: str) -> np.ndarray:
    """Mean net edge per market (cluster aggregation) for one fill model."""
    buckets: dict[str, list[float]] = {}
    for r in report.replayed:
        if r.model == model:
            buckets.setdefault(r.market_id, []).append(r.edge_net)
    return np.array([float(np.mean(v)) for v in buckets.values()])


def _boot_p_edge(edges: np.ndarray, n_boot: int, seed: int) -> tuple[float, float]:
    """(P(mean>0), mean) via market-level percentile bootstrap. Degenerate
    inputs (n<2 or zero variance) return P=0.0 — no evidence, no pass."""
    n = edges.size
    if n < 2:
        return 0.0, float(edges.mean()) if n else float("nan")
    if float(edges.var()) == 0.0:
        return 0.0, float(edges.mean())
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    means = edges[idx].mean(axis=1)
    return float((means > 0).mean()), float(edges.mean())


def evaluate_gate(report: ReplayReport, cfg: GateConfig | None = None) -> GateResult:
    cfg = cfg or GateConfig()
    prec = _per_market_edges(report, PRECISE)
    coar = _per_market_edges(report, COARSE)
    p_prec, mu_prec = _boot_p_edge(prec, cfg.N_BOOT, cfg.SEED)
    p_coar, mu_coar = _boot_p_edge(coar, cfg.N_BOOT, cfg.SEED + 1)

    reasons: list[str] = []
    if prec.size < cfg.MIN_PRECISE_MARKETS:
        reasons.append(
            f"insufficient_precise_markets ({prec.size} < {cfg.MIN_PRECISE_MARKETS})"
        )
    if p_prec < cfg.P_EDGE_MIN:
        reasons.append(f"p_edge_precise {p_prec:.3f} < {cfg.P_EDGE_MIN}")
    if (np.isfinite(mu_prec) and np.isfinite(mu_coar)
            and abs(mu_prec - mu_coar) > cfg.COARSE_DIVERGENCE_WARN):
        reasons.append("coarse_divergence_warn")  # non-blocking flag

    blocking = [r for r in reasons if r != "coarse_divergence_warn"]
    return GateResult(
        admitted=not blocking,
        p_edge_precise=p_prec, mean_edge_precise=mu_prec,
        n_precise_markets=int(prec.size),
        p_edge_coarse=p_coar, mean_edge_coarse=mu_coar,
        n_coarse_markets=int(coar.size),
        reasons=reasons,
    )
