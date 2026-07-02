#!/usr/bin/env python3
"""esports_silo — resolution + skill lifecycle (item #8). **SKILL, NOT P&L (Cmd 1).**

When a match settles, its predictions become scorable. This module joins predictions to the
resolved `matches.winner`, turns each into a (p_model, market_price, outcome) record, and hands
them to `eval/skill_metrics` — the ONLY verdict on the signal is forecasting skill vs the market
(Brier, calibration, closing-line, skill-vs-market). No realized-dollar figure is computed or
returned here; a bet-hit-rate is offered ONLY as an execution diagnostic, never as evidence.

Pure functions over records (dicts or PredictionRecord-like objects); the thin DB loader at the
bottom runs on the box.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from ..eval.skill_metrics import evaluate, SkillReport
except ImportError:
    from eval.skill_metrics import evaluate, SkillReport  # type: ignore


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from either a dict or a dataclass/attr object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def resolve(predictions: List[Any], winner_by_match: Dict[str, Optional[str]]
            ) -> List[Dict[str, Any]]:
    """Join predictions to settled winners → scorable records.

    Keeps only predictions whose match has a resolved winner in {'team_a','team_b'} AND a
    non-None p_model (an uncalibrated/no-signal row is not scorable). `outcome` = 1 if team_a
    won else 0 — the SAME YES axis as `p_model` and `market_price` (orientation kept clean).
    """
    out: List[Dict[str, Any]] = []
    for p in predictions:
        mid = _get(p, "match_id")
        w = winner_by_match.get(str(mid)) if mid is not None else None
        p_model = _get(p, "p_model")
        if w not in ("team_a", "team_b") or p_model is None:
            continue
        out.append({
            "match_id": str(mid),
            "p_model": float(p_model),
            "market_price": _get(p, "market_price"),
            "outcome": 1 if w == "team_a" else 0,
            "decision": _get(p, "decision", "no_bet"),
            "side": _get(p, "side"),
            "game": _get(p, "game"),
        })
    return out


def skill_report(resolved: List[Dict[str, Any]]) -> SkillReport:
    """The verdict on the signal: forecasting skill vs the market (P&L-free, Cmd 1)."""
    return evaluate(resolved)


def bet_hit_rate(resolved: List[Dict[str, Any]]) -> Dict[str, Any]:
    """DIAGNOSTIC ONLY (not evidence, Cmd 1): among rows we actually bet, how often the bet
    side matched the winner. Says nothing about edge or profit — a high hit-rate on low-edge
    favourites is not skill. Use `skill_report` for the real verdict.
    """
    bets = [r for r in resolved if r.get("decision") in ("bet_a", "bet_b")]
    if not bets:
        return {"n_bets": 0, "hit_rate": None}
    hits = 0
    for r in bets:
        won_team_a = r["outcome"] == 1
        if (r["decision"] == "bet_a" and won_team_a) or (r["decision"] == "bet_b" and not won_team_a):
            hits += 1
    return {"n_bets": len(bets), "hits": hits, "hit_rate": hits / len(bets),
            "note": "diagnostic only — NOT evidence (Cmd 1); judge on skill_report"}


# ======================================================================================
# thin DB layer (operator's box)
# ======================================================================================
async def load_predictions_with_winners(conn, model_version: Optional[str] = None
                                         ) -> List[Dict[str, Any]]:
    """Predictions joined to their match winner (resolved only) — ready for resolve()/skill."""
    q = """SELECT p.match_id, p.market_id, p.p_model, p.market_price, p.decision,
                  m.winner, m.game
             FROM predictions p JOIN matches m ON p.match_id = m.match_id
            WHERE m.winner IN ('team_a','team_b')"""
    args: List[Any] = []
    if model_version:
        q += " AND p.model_version = $1"
        args.append(model_version)
    rows = [dict(r) for r in await conn.fetch(q, *args)]
    winner_by_match = {str(r["match_id"]): r["winner"] for r in rows}
    return resolve(rows, winner_by_match)
