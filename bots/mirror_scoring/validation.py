"""Counterfactual validation harness — the kill criterion.

Uses mirror_rejected_signals (migration 073; verified live 2026-07-02:
23 rejection sites, resolution backfilled) as an out-of-sample set the
engine never trained on: signals MB did NOT take, with outcomes.

Protocol:
  1. Score traders on trades data with resolution <= cutoff (q_score).
  2. Pull each scored trader's rejected signals with event_time > cutoff
     and a backfilled resolution (first signal per (trader, market) — the
     mirrorable one).
  3. Counterfactual hold-to-resolution edge per signal: o - price
     (price is the whale's RTDS print — stated caveat: no MB fill model,
     so this validates RANKING, not absolute P&L).
  4. KILL CRITERION: admitted traders' cluster-robust mean counterfactual
     edge must exceed non-admitted traders' by a margin whose one-sided
     wild-bootstrap p < ALPHA. If not, the engine's ranking carries no
     out-of-sample information — DO NOT wire it to anything. Report FAIL.

A PASS clears the UNVERIFIED label on the ranking only; sizing weights
remain shadow until a separate fill-modeled validation exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import text

from bots.mirror_scoring.config import ScoringConfig
from bots.mirror_scoring import stats as S
from bots.mirror_scoring.q_score import TraderScore

_REJECTED_SQL = """
SELECT DISTINCT ON (r.trader_address, r.market_id)
       r.trader_address, r.market_id, r.token_id, r.side, r.price,
       r.event_time, r.resolution,
       m.yes_token_id, m.no_token_id
FROM mirror_rejected_signals r
LEFT JOIN markets m ON m.condition_id = r.market_id
WHERE r.resolution IN ('YES', 'NO')
  AND r.price IS NOT NULL AND r.price > :pmin AND r.price < :pmax
  AND r.event_time > :cutoff
  AND r.trader_address = ANY(:traders)
ORDER BY r.trader_address, r.market_id, r.event_time ASC
"""


@dataclass
class ValidationReport:
    passed: bool
    n_admitted_signals: int
    n_other_signals: int
    admitted_edge: float
    other_edge: float
    spread: float
    p_value: float
    detail: str
    # F1 (review 2026-07-06): rejected signals dropped because the same
    # (trader, market) also fed that trader's admission score. Nonzero is
    # normal; this makes the contamination that WOULD have occurred visible.
    n_excluded_overlap: int = 0


def _signal_edge(row: dict) -> float | None:
    """Counterfactual hold-to-resolution edge of one rejected signal."""
    res, side = row.get("resolution"), str(row.get("side") or "").upper()
    tok = row.get("token_id")
    if res not in ("YES", "NO"):
        return None
    if tok and row.get("yes_token_id") and tok == row["yes_token_id"]:
        won = res == "YES"
    elif tok and row.get("no_token_id") and tok == row["no_token_id"]:
        won = res == "NO"
    elif side in ("YES", "NO"):
        won = res == side
    else:
        return None
    return (1.0 if won else 0.0) - float(row["price"])


async def validate_ranking(
    db, scores: list[TraderScore], cutoff: datetime, cfg: ScoringConfig
) -> ValidationReport:
    admitted = {t.trader for t in scores if t.admitted}
    others = {t.trader for t in scores if not t.admitted}
    all_traders = list(admitted | others)
    if not admitted or not others:
        return ValidationReport(
            passed=False, n_admitted_signals=0, n_other_signals=0,
            admitted_edge=float("nan"), other_edge=float("nan"),
            spread=float("nan"), p_value=1.0,
            detail="need both admitted and non-admitted traders to compare",
        )
    async with db.get_session() as s:
        # The universe/rejected scans are unbounded and can exceed the 30s
        # bot-tier statement_timeout every session gets at __aenter__ (the
        # 2026-07-02 audit's confirmed run-blocker). Extend for THIS
        # transaction only — same pattern as database.py:3670.
        await s.execute(text("SET LOCAL statement_timeout = '300s'"))
        rows = (await s.execute(text(_REJECTED_SQL), {
            "pmin": cfg.PRICE_MIN, "pmax": cfg.PRICE_MAX,
            "cutoff": cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff,
            "traders": all_traders,
        })).fetchall()

    # F1: a (trader, market) that fed the trader's admission score must not
    # also "validate" it — the same whale print/outcome on both sides makes
    # the kill criterion circular (biased toward false PASS). Exclude and count.
    scored_pairs = {
        (t.trader, cid) for t in scores for cid in getattr(t, "condition_ids", [])
    }

    diffs, edges_a, edges_o, clusters = [], [], [], []
    n_excluded = 0
    for r in rows:
        d = dict(r._mapping)
        edge = _signal_edge(d)
        if edge is None:
            continue
        if (d["trader_address"], d["market_id"]) in scored_pairs:
            n_excluded += 1
            continue
        is_admitted = d["trader_address"] in admitted
        (edges_a if is_admitted else edges_o).append(edge)
        # For the test statistic: signed edge, + for admitted, - for others,
        # clustered by market so shared events don't inflate confidence.
        diffs.append(edge if is_admitted else -edge)
        clusters.append(d["market_id"])

    if not edges_a or not edges_o:
        return ValidationReport(
            passed=False, n_admitted_signals=len(edges_a),
            n_other_signals=len(edges_o),
            admitted_edge=float("nan"), other_edge=float("nan"),
            spread=float("nan"), p_value=1.0,
            detail="insufficient post-cutoff resolved signals on one side",
            n_excluded_overlap=n_excluded,
        )

    a_mean, o_mean = float(np.mean(edges_a)), float(np.mean(edges_o))
    spread = a_mean - o_mean
    p = S.wild_cluster_bootstrap_p(
        np.array(diffs) - float(np.mean(diffs)) + spread / 2.0,
        np.array(clusters), n_boot=cfg.N_BOOT, seed=cfg.BOOT_SEED,
    ) if spread > 0 else 1.0
    passed = spread > 0 and p < cfg.ALPHA
    return ValidationReport(
        passed=passed, n_admitted_signals=len(edges_a),
        n_other_signals=len(edges_o), admitted_edge=a_mean,
        other_edge=o_mean, spread=spread, p_value=p,
        detail=("PASS: admitted ranking carries out-of-sample signal"
                if passed else
                "FAIL: no out-of-sample separation — do not wire to orders"),
        n_excluded_overlap=n_excluded,
    )
