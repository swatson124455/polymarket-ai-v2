"""Stage-2 pipeline: what MB actually captures, per admitted trader.

For each first-mirrorable entry of a Stage-1-admitted trader:
  p_delta : price of the SAME token at t + DELTA_SECONDS, staleness-bounded
            (price_lookup.price_at) — no sample => entry uncovered
  o_x     : outcome under MB's replayed exit ladder over the token's price
            series (exit_replay.replay_exit)
  e~_i    : o_x - p_delta - FEE_ROUNDTRIP
  L_net   : weighted cluster mean with corrected SE, minus t-bound
  rho     : covered fraction; rho < RHO_MIN => trader is WATCH-ONLY —
            Q-only traders never enter the sizeable pool (audited: the
            laxer metric must not become a regime arbitrage)

Fill weights w_i from orderbook_snapshots are deliberately NOT implemented
in this version: snapshots cover only ~top-200 markets by an unreliable
volume ranking, and pre-print book state overstates fillable size on
informed flow (audit f3). Until shadow_fills-calibrated weights exist,
w_i = 1 and results carry the 'unweighted_fills' caveat — conservative
labeling, not silent optimism.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from bots.mirror_scoring.config import ScoringConfig
from bots.mirror_scoring import stats as S
from bots.mirror_scoring.estimand import Entry
from bots.mirror_scoring.exit_replay import ExitParams, replay_exit
from bots.mirror_scoring.price_lookup import price_at, price_series


@dataclass
class TailScore:
    trader: str
    n_total: int
    n_covered: int
    rho: float
    mu_net: float
    se_net: float
    l_net: float               # lower-bounded net edge per $ (the gate)
    delta_exit: float          # mean(o_x - o_res): exit policy vs hold
    watch_only: bool
    caveats: list = field(default_factory=list)
    label: str = "UNVERIFIED"
    # A3 (docs/ASSUMPTION_AUDIT_2026-07-06.md): the ladder replayed above is
    # the DEAD bot's never-validated exit policy. These fields score the same
    # entries under plain hold-to-resolution so the two policies can be
    # compared before anything gates on either. Which policy v3 ships is an
    # OPEN strategy decision — do not gate Stage-2 on l_net alone.
    mu_net_hold: float = float("nan")
    se_net_hold: float = float("nan")
    l_net_hold: float = float("-inf")


async def score_tailability(
    db,
    trader: str,
    entries: list[Entry],
    resolved_time: dict[str, datetime],
    cfg: ScoringConfig,
    exit_params: ExitParams | None = None,
) -> TailScore:
    """Compute L_net for one trader's resolved first-entries."""
    ep = exit_params or ExitParams()
    usable = [e for e in entries if e.edge_res is not None
              and e.condition_id in resolved_time and e.token_id]
    e_net, e_hold, e_dx, clusters = [], [], [], []
    covered = 0
    for ent in usable:
        t_fill = ent.timestamp + timedelta(seconds=cfg.DELTA_SECONDS)
        got = await price_at(db, ent.token_id, t_fill, cfg.PRICE_STALENESS_MAX_S)
        if got is None:
            continue
        covered += 1
        p_delta = got[0]
        res_t = resolved_time[ent.condition_id]
        series = await price_series(db, ent.token_id, t_fill, res_t)
        rr = replay_exit(
            entry_price=p_delta, entry_time=t_fill, price_series=series,
            resolution_time=res_t, resolution_outcome=float(ent.o_res),
            trader_sell_times=[ent.exit_time] if ent.exit_time else None,
            params=ep,
        )
        e_net.append(rr.o_x - p_delta - cfg.FEE_ROUNDTRIP)
        # A3: same entry under plain hold-to-resolution — the comparison
        # policy the dead bot's ladder must beat before it gates anything.
        e_hold.append(float(ent.o_res) - p_delta - cfg.FEE_ROUNDTRIP)
        e_dx.append(rr.o_x - float(ent.o_res))
        clusters.append(ent.condition_id)

    n_total = len(usable)
    rho = covered / n_total if n_total else 0.0
    caveats = ["unweighted_fills"]
    if not e_net:
        return TailScore(
            trader=trader, n_total=n_total, n_covered=0, rho=0.0,
            mu_net=float("nan"), se_net=float("nan"), l_net=float("-inf"),
            delta_exit=float("nan"), watch_only=True,
            caveats=caveats + ["no_covered_entries"],
        )

    arr = np.array(e_net)
    cl = np.array(clusters)
    mu, se, C = S.cluster_stats(arr, cl)
    l_net = S.t_lower_bound(mu, se, C, cfg.ALPHA)
    # A3: identical machinery for the hold-to-resolution policy.
    mu_h, se_h, C_h = S.cluster_stats(np.array(e_hold), cl)
    l_net_hold = S.t_lower_bound(mu_h, se_h, C_h, cfg.ALPHA)
    watch_only = rho < cfg.RHO_MIN or l_net <= 0 or C < cfg.MIN_EVENTS
    if rho < cfg.RHO_MIN:
        caveats.append("low_price_coverage")
    if C < cfg.MIN_EVENTS:
        caveats.append("too_few_covered_events")
    return TailScore(
        trader=trader, n_total=n_total, n_covered=covered, rho=rho,
        mu_net=mu, se_net=se, l_net=l_net,
        delta_exit=float(np.mean(e_dx)), watch_only=watch_only,
        caveats=caveats,
        mu_net_hold=mu_h, se_net_hold=se_h, l_net_hold=l_net_hold,
    )
