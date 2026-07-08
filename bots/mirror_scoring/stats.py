"""Statistical core for the v3 scoring engine.

Every estimator here exists because a specific naive version was shown to
lose money (see session audit 2026-07-02):
  - cluster-robust SEs        : naive iid SE passed 31% of zero-edge traders
  - weighted cluster SE       : unweighted residuals are ~10% anti-conservative
                                when liquidity clusters by event
  - Jeffreys binomial bound   : spotless favorite books degenerate the t-bound
                                (SE=0 auto-pass, 35% at p=0.95/C=20)
  - min-adverse-events gate   : same failure mode, structural backstop
  - wild cluster bootstrap    : CR1 t is anti-conservative at small C
  - BH-FDR                    : naive top-K was up to 63% false discoveries
  - EB shrinkage              : post-selection edges inflated ~2.16x
  - event-level Kelly + cap   : per-trade variance under-states event risk by
                                the design effect 1+rho(m-1)

All functions are pure (numpy in, numbers out) — no DB, no I/O.
"""

from __future__ import annotations

import numpy as np
from scipy import stats as sps


# ── Cluster-robust means and bounds ──────────────────────────────────────


def cluster_stats(e: np.ndarray, clusters: np.ndarray) -> tuple[float, float, int]:
    """Unweighted mean with CR1 (Liang-Zeger) cluster-robust SE.

    Returns (mean, se_cr, C). se_cr is nan when C < 2.
    """
    e = np.asarray(e, dtype=float)
    clusters = np.asarray(clusters)
    n = e.size
    if n == 0:
        return float("nan"), float("nan"), 0
    ebar = float(e.mean())
    uniq = np.unique(clusters)
    C = int(uniq.size)
    if C < 2:
        return ebar, float("nan"), C
    ss = 0.0
    for c in uniq:
        ss += float((e[clusters == c] - ebar).sum()) ** 2
    se = np.sqrt((C / (C - 1)) * ss) / n
    return ebar, float(se), C


def weighted_cluster_stats(
    e: np.ndarray, w: np.ndarray, clusters: np.ndarray
) -> tuple[float, float, int]:
    """Weighted mean mu = sum(w*e)/sum(w) with the CORRECT cluster-robust SE.

    SE = (1/W) * sqrt( C/(C-1) * sum_c ( sum_{i in c} w_i*(e_i - mu) )^2 )

    Residuals are weighted and centered at the WEIGHTED mean (the audit
    showed unweighted residuals are anti-conservative exactly when
    liquidity clusters by event).
    """
    e = np.asarray(e, dtype=float)
    w = np.asarray(w, dtype=float)
    clusters = np.asarray(clusters)
    W = float(w.sum())
    if e.size == 0 or W <= 0:
        return float("nan"), float("nan"), 0
    mu = float((w * e).sum() / W)
    uniq = np.unique(clusters)
    C = int(uniq.size)
    if C < 2:
        return mu, float("nan"), C
    ss = 0.0
    for c in uniq:
        m = clusters == c
        ss += float((w[m] * (e[m] - mu)).sum()) ** 2
    se = np.sqrt((C / (C - 1)) * ss) / W
    return mu, float(se), C


def t_lower_bound(mean: float, se: float, C: int, alpha: float = 0.05) -> float:
    """One-sided lower confidence bound with t on C-1 df.

    Degenerate SE (~0 or nan, e.g. a spotless record) returns -inf: a record
    with no observable variance carries no distributional evidence and must
    NOT auto-pass. Uses an absolute epsilon because a zero-variance record
    yields float dust (~1e-18), not exact 0. Use jeffreys_edge_lb for the
    evidence such a record does carry.
    """
    if C < 2 or not np.isfinite(se) or se <= 1e-12:
        return float("-inf")
    return float(mean - sps.t.ppf(1 - alpha, df=C - 1) * se)


# ── Exact binomial bound (kills the favorite-seller hole) ────────────────


def jeffreys_edge_lb(
    event_wins: int, n_events: int, avg_price: float, alpha: float = 0.05
) -> float:
    """Jeffreys lower bound on event win-rate minus average entry price.

    Works on EVENT-level outcomes (one win/loss per cluster) so correlated
    legs can't manufacture sample size. Handles spotless records correctly:
    20/20 wins at p=0.95 gives a bound BELOW 0.95 => no admission on a
    short clean favorite record.
    """
    if n_events <= 0:
        return float("-inf")
    q_lo = sps.beta.ppf(alpha, event_wins + 0.5, n_events - event_wins + 0.5)
    return float(q_lo - avg_price)


def adverse_event_count(event_outcomes: np.ndarray) -> int:
    """Number of losing events (outcome 0). Sizeability requires >= cfg.MIN_ADVERSE_EVENTS."""
    arr = np.asarray(event_outcomes, dtype=float)
    return int((arr < 0.5).sum())


# ── Wild cluster bootstrap (Webb weights) ────────────────────────────────

_WEBB = np.array([-np.sqrt(1.5), -1.0, -np.sqrt(0.5), np.sqrt(0.5), 1.0, np.sqrt(1.5)])


def wild_cluster_bootstrap_p(
    e: np.ndarray,
    clusters: np.ndarray,
    w: np.ndarray | None = None,
    n_boot: int = 999,
    seed: int = 0,
) -> float:
    """One-sided p-value for H0: mean <= 0, wild cluster bootstrap-t with
    Webb 6-point weights. Valid at small C where CR1 t is anti-conservative.
    """
    e = np.asarray(e, dtype=float)
    clusters = np.asarray(clusters)
    if w is None:
        mu, se, C = cluster_stats(e, clusters)
    else:
        w = np.asarray(w, dtype=float)
        mu, se, C = weighted_cluster_stats(e, w, clusters)
    if C < 2 or not np.isfinite(se):
        return 1.0
    if se <= 1e-12:
        # Degenerate (spotless record): no distributional evidence.
        return 1.0
    t_obs = mu / se
    uniq = np.unique(clusters)
    resid = e - mu  # impose H0 by centering
    rng = np.random.default_rng(seed)
    exceed = 0
    for _ in range(n_boot):
        g = rng.choice(_WEBB, size=uniq.size)
        gmap = dict(zip(uniq.tolist(), g))
        e_b = np.array([gmap[c] for c in clusters.tolist()]) * resid
        if w is None:
            mu_b, se_b, _ = cluster_stats(e_b, clusters)
        else:
            mu_b, se_b, _ = weighted_cluster_stats(e_b, w, clusters)
        if np.isfinite(se_b) and se_b > 0 and (mu_b / se_b) >= t_obs:
            exceed += 1
    return float((1 + exceed) / (1 + n_boot))


def two_sample_group_permutation_p(
    values: np.ndarray,
    group_ids: np.ndarray,
    in_group_a: np.ndarray,
    n_perm: int = 999,
    seed: int = 0,
) -> tuple[float, float]:
    """Trader-block PERMUTATION test for H0: group A's typical member has no
    higher mean than group B's. Returns (one_sided_p, observed_spread).

    Why permutation, not the prior percentile bootstrap (review 2026-07-08):
    the bootstrap (two_sample_cluster_bootstrap_p, now removed) never imposed
    the null — it re-confirmed the sample spread, so it fired on PURE NOISE
    ~27-38% of the time (measured), and the placebo calibration check caught it
    (4/20 shuffles passed at the 2026-04-25 cutoff). A permutation test imposes
    the null by construction: relabel which whole GROUPS (traders) are "A",
    keeping |A| fixed, and ask how often a random relabel beats the observed
    spread. False-positive rate is then ~ALPHA by design (verified ~2.5-6% on
    null data), with ~93% power at a +0.15 edge.

    TWO deliberate choices:
      * permute whole TRADERS (a trader's signals move as one block) so
        within-trader correlation is respected — the correct exchangeable unit.
      * TRADER-EQUAL-WEIGHT statistic (mean of per-trader mean edges, not
        pooled-signal mean) so one high-volume whale cannot carry the verdict
        (stress-tested: a single lucky 1500-signal whale among 14 noise traders
        fools the volume-weighted stat 66% of the time, this one 14%).

    Missing a whole group, <2 groups, or empty input -> (1.0, nan).
    """
    values = np.asarray(values, dtype=float)
    gids = np.asarray(group_ids)
    a = np.asarray(in_group_a, dtype=bool)
    if values.size == 0 or not a.any() or a.all():
        return 1.0, float("nan")
    uniq, first = np.unique(gids, return_index=True)
    T = int(uniq.size)
    grp_is_a = a[first]                     # group-A membership per trader
    k = int(grp_is_a.sum())
    if T < 2 or k == 0 or k == T:
        return 1.0, float("nan")
    order = {g: i for i, g in enumerate(uniq.tolist())}
    idx = np.fromiter((order[g] for g in gids.tolist()), dtype=int, count=gids.size)
    gsum = np.zeros(T)
    gcnt = np.zeros(T)
    np.add.at(gsum, idx, values)
    np.add.at(gcnt, idx, 1.0)
    gmean = gsum / np.where(gcnt > 0, gcnt, 1.0)   # per-trader mean edge

    def spread(mask: np.ndarray) -> float:
        return float(gmean[mask].mean() - gmean[~mask].mean())

    obs = spread(grp_is_a)
    rng = np.random.default_rng(seed)
    ge = 0
    for _ in range(n_perm):
        perm = np.zeros(T, dtype=bool)
        perm[rng.choice(T, size=k, replace=False)] = True
        if spread(perm) >= obs:
            ge += 1
    return float((1 + ge) / (1 + n_perm)), obs


# ── Selection across the universe ────────────────────────────────────────


def bh_fdr(pvals: np.ndarray, q: float = 0.10) -> np.ndarray:
    """Benjamini-Hochberg step-up. Returns boolean admitted mask.

    Audit note: BH held even under adversarial negative cross-trader
    correlation (opposite sides of shared events); do NOT swap for BY.
    """
    p = np.asarray(pvals, dtype=float)
    m = p.size
    if m == 0:
        return np.zeros(0, dtype=bool)
    order = np.argsort(p)
    thresh = 0.0
    for rank, idx in enumerate(order, start=1):
        if p[idx] <= q * rank / m:
            thresh = p[idx]
    return p <= thresh if thresh > 0 else np.zeros(m, dtype=bool)


def empirical_bayes_shrink(
    edges: np.ndarray,
    ses: np.ndarray,
    pool_edges: np.ndarray | None = None,
    pool_ses: np.ndarray | None = None,
) -> np.ndarray:
    """Shrink per-trader edges toward the cross-trader mean.

    tau^2 estimated by method of moments: max(0, var(edges) - mean(se^2)).
    Counters the ~2.16x winner's-curse inflation of post-selection edges.

    F5 (review 2026-07-06): grand mean and tau^2 must be estimated on the FULL
    scored pool (pool_edges/pool_ses), not on the shrink targets themselves —
    shrinking selected winners toward the winners' own mean shrinks toward a
    selection-inflated target and under-corrects the very bias this exists to
    counter. Omitting the pool falls back to the legacy self-pool behavior.
    """
    edges = np.asarray(edges, dtype=float)
    ses = np.asarray(ses, dtype=float)
    if edges.size == 0:
        return edges
    pe = edges if pool_edges is None else np.asarray(pool_edges, dtype=float)
    ps = ses if pool_ses is None else np.asarray(pool_ses, dtype=float)
    pf = np.isfinite(pe) & np.isfinite(ps)
    if pf.sum() < 2:
        return edges
    grand = float(pe[pf].mean())
    tau2 = max(0.0, float(pe[pf].var(ddof=1)) - float((ps[pf] ** 2).mean()))
    finite = np.isfinite(edges) & np.isfinite(ses)
    out = edges.copy()
    shrink = tau2 / (tau2 + ses[finite] ** 2) if tau2 > 0 else np.zeros(int(finite.sum()))
    out[finite] = grand + shrink * (edges[finite] - grand)
    return out


# ── Sizing (event-level Kelly, capped) ───────────────────────────────────


def event_returns(
    e: np.ndarray, w: np.ndarray | None, clusters: np.ndarray
) -> np.ndarray:
    """Per-event per-dollar aggregates R_c = sum_c(w*e)/sum_c(w)."""
    e = np.asarray(e, dtype=float)
    clusters = np.asarray(clusters)
    w = np.ones_like(e) if w is None else np.asarray(w, dtype=float)
    out = []
    for c in np.unique(clusters):
        m = clusters == c
        wc = float(w[m].sum())
        out.append(float((w[m] * e[m]).sum() / wc) if wc > 0 else 0.0)
    return np.array(out)


def kelly_event_weight(
    shrunk_edge: float,
    ev_returns: np.ndarray,
    avg_price: float,
    n_adverse: int,
    lam: float = 0.25,
    cap: float = 0.02,
    min_adverse: int = 2,
    var_floor_from_price: bool = True,
) -> float:
    """Fractional Kelly on EVENT-level variance with a hard per-event cap.

    Zero if: no positive shrunk edge, or fewer than min_adverse losing
    events (tail-risk-seller backstop). Variance floored at pbar(1-pbar)
    so a low-variance clean record cannot ride the cap.
    """
    if not np.isfinite(shrunk_edge) or shrunk_edge <= 0:
        return 0.0
    if n_adverse < min_adverse:
        return 0.0
    ev = np.asarray(ev_returns, dtype=float)
    var_c = float(ev.var(ddof=1)) if ev.size >= 2 else float("nan")
    if var_floor_from_price and np.isfinite(avg_price):
        floor = max(avg_price * (1.0 - avg_price), 1e-4)
        var_c = max(var_c, floor) if np.isfinite(var_c) else floor
    if not np.isfinite(var_c) or var_c <= 0:
        return 0.0
    return float(min(lam * shrunk_edge / var_c, cap))
