"""Stage-1 pipeline: trader Quality scores from entries + resolutions only.

Per trader:
  estimand  : first BUY per condition_id (estimand.select_first_entries)
  edge      : e_i = o_res - p_i, one cluster per condition_id
  gates     : C >= MIN_EVENTS; Jeffreys edge LB > 0 (kills spotless-favorite
              degeneracy); train sign check; test wild-bootstrap p -> BH-FDR
              across the universe (single alpha spend — the double-hard-LB
              gate was audited at 0.6-6% power and rejected)
  sizing    : EB-shrunk edge -> event-level fractional Kelly, per-event cap,
              zero unless >= MIN_ADVERSE_EVENTS losing events
All numbers this module emits are shadow-only and labeled UNVERIFIED until
validation.py's counterfactual harness passes (Forbidden Pattern 8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
from sqlalchemy import text

from bots.mirror_scoring.config import ScoringConfig
from bots.mirror_scoring import stats as S
from bots.mirror_scoring.estimand import (
    Entry, select_first_entries, pair_exits, split_events_by_resolution,
)

# Trades + resolution + token mapping for user-attributed rows.
# Read-only; mirrors the join pattern used by mirror_bot.py:1954.
_UNIVERSE_SQL = """
SELECT t.user_address, t.token_id, t.side, t.price, t.size, t.timestamp,
       COALESCE(m.condition_id, t.market_id) AS condition_id,
       m.resolution, m.resolved_at, m.yes_token_id, m.no_token_id
FROM trades t
JOIN markets m
  ON (m.condition_id = t.market_id OR CAST(m.id AS TEXT) = t.market_id)
WHERE t.user_address IS NOT NULL AND t.user_address <> ''
  AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
  AND t.price > :pmin AND t.price < :pmax
"""


@dataclass
class TraderScore:
    trader: str
    n_entries: int
    n_events: int
    n_adverse: int
    avg_price: float
    edge_mean: float
    edge_se: float
    edge_lb_t: float            # t lower bound (may be -inf when degenerate)
    edge_lb_jeffreys: float     # exact binomial LB on event win-rate - pbar
    p_holdout: float            # one-sided wild-bootstrap p on the TEST half
    train_edge: float
    test_edge: float
    admitted: bool = False      # set by BH across the universe
    edge_shrunk: float = 0.0    # set post-selection (EB shrinkage)
    kelly_weight: float = 0.0   # shadow sizing weight (0 if not sizeable)
    watch_only_reasons: list = field(default_factory=list)
    label: str = "UNVERIFIED"   # cleared only by validation.py pass


def score_trader(
    entries: list[Entry], cfg: ScoringConfig, cutoff: datetime,
    resolved_at: dict[str, datetime],
) -> Optional[TraderScore]:
    """Score one trader from resolved first-entries. None if unscoreable."""
    scored = [e for e in entries if e.edge_res is not None]
    if len(scored) == 0:
        return None
    e = np.array([x.edge_res for x in scored])
    clusters = np.array([x.condition_id for x in scored])
    prices = np.array([x.price for x in scored])
    outcomes = np.array([x.o_res for x in scored])

    mean, se, C = S.cluster_stats(e, clusters)
    if C < cfg.MIN_EVENTS:
        return None

    # Event-level outcomes: an event "won" if its aggregate edge > 0 is wrong
    # (mixed tokens); use per-event mean outcome vs mean price instead.
    ev_out, ev_price = [], []
    for c in np.unique(clusters):
        m = clusters == c
        ev_out.append(float(outcomes[m].mean()))
        ev_price.append(float(prices[m].mean()))
    ev_out_arr = np.array(ev_out)
    n_adverse = S.adverse_event_count(ev_out_arr)
    avg_price = float(np.mean(ev_price))
    wins = int((ev_out_arr >= 0.5).sum())

    lb_t = S.t_lower_bound(mean, se, C, cfg.ALPHA)
    lb_j = S.jeffreys_edge_lb(wins, C, avg_price, cfg.ALPHA)

    train, test = split_events_by_resolution(scored, cutoff, resolved_at)
    tr_e = np.array([x.edge_res for x in train]) if train else np.array([])
    te_e = np.array([x.edge_res for x in test]) if test else np.array([])
    tr_c = np.array([x.condition_id for x in train]) if train else np.array([])
    te_c = np.array([x.condition_id for x in test]) if test else np.array([])
    train_edge = float(tr_e.mean()) if tr_e.size else float("nan")
    test_edge = float(te_e.mean()) if te_e.size else float("nan")

    # Persistence: train sign check + test p-value (feeds BH). Missing halves
    # fail closed (p=1).
    if tr_e.size == 0 or te_e.size == 0 or train_edge <= 0:
        p_holdout = 1.0
    else:
        p_holdout = S.wild_cluster_bootstrap_p(
            te_e, te_c, n_boot=cfg.N_BOOT, seed=cfg.BOOT_SEED
        )

    ts = TraderScore(
        trader=scored[0].trader, n_entries=len(scored), n_events=C,
        n_adverse=n_adverse, avg_price=avg_price, edge_mean=mean,
        edge_se=se, edge_lb_t=lb_t, edge_lb_jeffreys=lb_j,
        p_holdout=p_holdout, train_edge=train_edge, test_edge=test_edge,
    )
    if lb_j <= 0:
        ts.watch_only_reasons.append("jeffreys_lb_nonpositive")
    if n_adverse < cfg.MIN_ADVERSE_EVENTS:
        ts.watch_only_reasons.append("insufficient_adverse_events")
    return ts


def select_and_size(
    scores: list[TraderScore],
    entries_by_trader: dict[str, list[Entry]],
    cfg: ScoringConfig,
) -> list[TraderScore]:
    """BH-FDR admission, then EB shrinkage + event-level Kelly sizing.

    Alpha is spent ONCE, at admission (audit flaw b: a second hard lower
    bound empties the watchlist). The BH pool is filtered only by the
    structural favorite-seller backstops that carry no statistical power
    cost: >= MIN_ADVERSE_EVENTS losing events AND a non-degenerate SE (a
    spotless/zero-variance record has a meaningless p-value). Jeffreys is
    NOT an admission gate — it guards SIZEABILITY only (audit flaw f1).
    """
    pool = [
        t for t in scores
        if t.n_adverse >= cfg.MIN_ADVERSE_EVENTS
        and np.isfinite(t.edge_se) and t.edge_se > 1e-12
    ]
    if pool:
        admitted = S.bh_fdr(np.array([t.p_holdout for t in pool]), cfg.BH_Q)
        for t, a in zip(pool, admitted):
            t.admitted = bool(a)
    adm = [t for t in scores if t.admitted]
    if adm:
        shrunk = S.empirical_bayes_shrink(
            np.array([t.edge_mean for t in adm]),
            np.array([t.edge_se for t in adm]),
        )
        for t, s_edge in zip(adm, shrunk):
            t.edge_shrunk = float(s_edge)
            # Sizeability guard: Jeffreys must clear the favorite hurdle,
            # else the trader is admitted (watch) but sized at zero.
            if t.edge_lb_jeffreys <= 0:
                t.watch_only_reasons.append("jeffreys_lb_nonpositive_size_zero")
                continue
            ent = [e for e in entries_by_trader.get(t.trader, [])
                   if e.edge_res is not None]
            ev = S.event_returns(
                np.array([e.edge_res for e in ent]), None,
                np.array([e.condition_id for e in ent]),
            )
            t.kelly_weight = S.kelly_event_weight(
                t.edge_shrunk, ev, t.avg_price, t.n_adverse,
                lam=cfg.KELLY_LAMBDA, cap=cfg.KELLY_CAP,
                min_adverse=cfg.MIN_ADVERSE_EVENTS,
                var_floor_from_price=cfg.VAR_FLOOR_FROM_PRICE,
            )
    return scores


async def run_universe(db, cfg: ScoringConfig) -> list[TraderScore]:
    """Pull resolved user-attributed trades, score every trader, select, size."""
    async with db.get_session() as s:
        rows = (await s.execute(
            text(_UNIVERSE_SQL), {"pmin": cfg.PRICE_MIN, "pmax": cfg.PRICE_MAX}
        )).fetchall()
    by_trader: dict[str, list[dict]] = {}
    resolved_at: dict[str, datetime] = {}
    for r in rows:
        d = dict(r._mapping)
        by_trader.setdefault(d["user_address"], []).append(d)
        if d.get("resolved_at") is not None:
            resolved_at[d["condition_id"]] = d["resolved_at"]

    if cfg.HOLDOUT_CUTOFF_ISO:
        cutoff = datetime.fromisoformat(cfg.HOLDOUT_CUTOFF_ISO)
    else:
        times = sorted(resolved_at.values())
        if not times:
            return []
        cutoff = times[len(times) // 2]

    scores: list[TraderScore] = []
    entries_by_trader: dict[str, list[Entry]] = {}
    for trader, trows in by_trader.items():
        if len(trows) < cfg.MIN_TRADES_PER_TRADER:
            continue
        entries = pair_exits(select_first_entries(trows), trows)
        entries_by_trader[trader] = entries
        ts = score_trader(entries, cfg, cutoff, resolved_at)
        if ts is not None:
            scores.append(ts)
    return select_and_size(scores, entries_by_trader, cfg)
