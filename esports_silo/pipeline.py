#!/usr/bin/env python3
"""esports_silo — end-to-end prediction pipeline (item #7 wiring).

Ties the built pieces into one per-market pass, all PURE (testable without DB/network); the
thin DB read/write helpers at the bottom run on the operator's box.

  market (question + price)  --matcher #3-->  a specific match
        + that match's raw sharp odds  --signal #5-->  calibrated P(team_a)
        + the market price             --decision #6-->  bet_a | bet_b | no_bet
                                       -->  a `predictions` row (event_time = match start)

Nothing here fits the calibrator, clears quarantine, or lifts the halt — it only assembles a
prediction from already-built components. `edge` is written as a DIAGNOSTIC (schema note),
never a trigger. Until the calibrator is fitted (forward data) and skill is proven, `p_model`
is None and every decision is `no_bet` (D2/Cmd 4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from .signal.sharp_consensus import SharpSignal, SignalOutput
    from .betting.decision import decide, Decision
    from .markets.match_matcher import link_market
except ImportError:  # loose-file fallback
    from signal.sharp_consensus import SharpSignal, SignalOutput  # type: ignore
    from betting.decision import decide, Decision  # type: ignore
    from markets.match_matcher import link_market  # type: ignore

MODEL_VERSION = "sharp_consensus_v1"
SIGNAL_SOURCE = "sharp_consensus"


@dataclass
class PredictionRecord:
    """One row destined for the `predictions` table, plus the decision it carries."""
    match_id: Optional[str]
    market_id: Optional[str]
    model_version: str
    signal_source: str
    p_model: Optional[float]
    market_price: Optional[float]
    edge: Optional[float]           # DIAGNOSTIC only (schema note)
    decision: str                   # 'bet_a' | 'bet_b' | 'no_bet'
    event_time: Any                 # match start_time — look-ahead anchor
    # non-persisted context (handy for logging / paper execution)
    side: Optional[str] = None
    stake_usd: float = 0.0
    n_books: int = 0
    calibrated: bool = False
    reason: str = ""


def build_prediction(match: Dict[str, Any], book_odds: List[Dict[str, Any]],
                     market_price: Optional[float], signal: SharpSignal,
                     *, skill_proven: bool, bankroll: float,
                     market_id: Optional[str] = None) -> PredictionRecord:
    """Signal → decision → a `predictions` record for one (match, market)."""
    out: SignalOutput = signal.predict(book_odds, market_price=market_price)
    dec: Decision = decide(out.p_model, market_price, bankroll,
                           skill_proven=skill_proven, calibrated=out.calibrated)
    return PredictionRecord(
        match_id=str(match.get("match_id")) if match.get("match_id") is not None else None,
        market_id=market_id,
        model_version=MODEL_VERSION,
        signal_source=SIGNAL_SOURCE,
        p_model=out.p_model,
        market_price=market_price,
        edge=out.edge,
        decision=dec.action,
        event_time=match.get("start_time"),
        side=dec.side,
        stake_usd=dec.stake_usd,
        n_books=out.n_books,
        calibrated=out.calibrated,
        reason=dec.reason,
    )


def process_market(market: Dict[str, Any], candidate_matches: List[Dict[str, Any]],
                   odds_by_match: Dict[str, List[Dict[str, Any]]],
                   alias_map: Dict[str, List[str]], signal: SharpSignal,
                   *, skill_proven: bool, bankroll: float
                   ) -> Optional[PredictionRecord]:
    """Full pass for one Polymarket market: link → gather odds → predict.

    Returns None (with no side effect) when the market can't be linked to a match — a market
    with no matching two-team match is not forced into a prediction (Cmd 4: unsure = out).
    """
    link = link_market(market.get("question", ""), candidate_matches, alias_map).link
    if link is None:
        return None
    book_odds = odds_by_match.get(link.match_id, [])
    match = {"match_id": link.match_id, "team_a": link.team_a, "team_b": link.team_b,
             "start_time": market.get("start_time")}
    # find the linked match's start_time from the candidates if present
    for cm in candidate_matches:
        if str(cm.get("match_id")) == link.match_id:
            match["start_time"] = cm.get("start_time", match["start_time"])
            break
    return build_prediction(match, book_odds, market.get("yes_price"), signal,
                            skill_proven=skill_proven, bankroll=bankroll,
                            market_id=str(market.get("market_id")) if market.get("market_id") else None)


# ======================================================================================
# thin DB layer (operator's box; the assembly above is DB-free and tested)
# ======================================================================================
async def write_prediction(conn, rec: PredictionRecord) -> None:
    """INSERT a prediction row. (predictions is a log — one row per decision.)"""
    await conn.execute(
        """INSERT INTO predictions
             (match_id, market_id, model_version, signal_source, p_model, market_price,
              edge, decision, event_time)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        rec.match_id, rec.market_id, rec.model_version, rec.signal_source,
        rec.p_model, rec.market_price, rec.edge, rec.decision, rec.event_time,
    )
