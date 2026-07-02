#!/usr/bin/env python3
"""esports_silo — paper execution (item #8). Paper trading IS production: everything real
except the final submit, which logs at $0 instead of hitting the CLOB.

A decision becomes a paper order ONLY when it is an actual bet; a `no_bet` (the default while
halted / unproven) produces no order. The order records the intent + the entry price for
execution realism and later resolution — it deliberately carries NO P&L field (Cmd 1): the
system is judged on skill (`resolution.skill_report`), not realized dollars.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PaperOrder:
    match_id: Optional[str]
    market_id: Optional[str]
    side: Optional[str]            # 'team_a' | 'team_b' | None
    stake_usd: float               # intended stake (paper)
    entry_price: Optional[float]   # team_a=YES price at decision (team_b uses 1-price)
    status: str                    # 'paper_filled' | 'no_order'
    real_execution_usd: float = 0.0  # ALWAYS 0 in paper mode (the only paper/live difference)
    note: str = ""


def execute_paper(rec: Any) -> PaperOrder:
    """Turn a pipeline PredictionRecord (or dict) into a paper order, or a no-order.

    The ONLY difference from live is that nothing is submitted to the CLOB — real_execution_usd
    stays 0. Fill confirmation / order-state tracking would attach here identically in live mode.
    """
    def g(k, d=None):
        return rec.get(k, d) if isinstance(rec, dict) else getattr(rec, k, d)

    decision = g("decision", "no_bet")
    if decision not in ("bet_a", "bet_b"):
        return PaperOrder(
            match_id=g("match_id"), market_id=g("market_id"), side=None, stake_usd=0.0,
            entry_price=g("market_price"), status="no_order", note=str(g("reason", "")))

    price = g("market_price")
    side = g("side") or ("team_a" if decision == "bet_a" else "team_b")
    # team_b buys the NO token; its entry price is (1 - team_a_price).
    entry = price if side == "team_a" else (1.0 - price if price is not None else None)
    return PaperOrder(
        match_id=g("match_id"), market_id=g("market_id"), side=side,
        stake_usd=float(g("stake_usd", 0.0) or 0.0), entry_price=entry,
        status="paper_filled", real_execution_usd=0.0,
        note=f"PAPER — {side} @ {entry} (no CLOB submit)")
