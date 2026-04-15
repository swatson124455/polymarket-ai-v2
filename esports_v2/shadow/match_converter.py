"""
Type conversions between v1 PandaScore EsportsMatch and v2 data types.

Bridges the async PandaScoreClient output (EsportsMatch) with the v2
rating/prediction pipeline (RawMatch, TrinityPrediction, feature records).
"""
from __future__ import annotations

from typing import Dict, Optional

from esports_v2.data.normalizer import RawMatch
from esports_v2.data.pandascore_loader import _classify_tier, _is_lan_event
from esports_v2.ratings.trinity import TrinityPrediction


def esports_match_to_raw(match) -> RawMatch:
    """
    Convert v1 EsportsMatch (from PandaScoreClient) to v2 RawMatch.

    Args:
        match: pandascore_client.EsportsMatch dataclass.

    Returns:
        RawMatch suitable for normalizer and Trinity processing.
    """
    # Determine winner from scores
    winner = None
    if match.status == "finished":
        if match.score_a > match.score_b:
            winner = match.team_a
        elif match.score_b > match.score_a:
            winner = match.team_b

    event_name = match.tournament or match.league or ""

    return RawMatch(
        match_id=f"ps_{match.match_id}",
        game=match.game,
        event_name=event_name or None,
        event_tier=_classify_tier(event_name),
        team_a=match.team_a,
        team_b=match.team_b,
        winner=winner,
        score_a=match.score_a if match.score_a != 0 or match.status == "finished" else None,
        score_b=match.score_b if match.score_b != 0 or match.status == "finished" else None,
        best_of=match.best_of,
        match_date=match.scheduled_at,
        is_lan=_is_lan_event(event_name),
        source="pandascore",
        raw_data={},
    )


def esports_match_to_db_row(match) -> dict:
    """Convert v1 EsportsMatch to dict for esports_matches INSERT."""
    event_name = match.tournament or match.league or ""
    winner = None
    if match.status == "finished":
        if match.score_a > match.score_b:
            winner = match.team_a
        elif match.score_b > match.score_a:
            winner = match.team_b

    return {
        "match_id": f"ps_{match.match_id}",
        "game": match.game,
        "event_name": event_name or None,
        "event_tier": _classify_tier(event_name),
        "team_a": match.team_a,
        "team_b": match.team_b,
        "winner": winner,
        "score_a": match.score_a,
        "score_b": match.score_b,
        "best_of": match.best_of,
        "match_date": match.scheduled_at,
        "is_lan": _is_lan_event(event_name),
        "source": "pandascore_live",
    }


def build_feature_record(raw: RawMatch, prediction: TrinityPrediction) -> dict:
    """
    Build feature record for pipeline.predict(). Same structure as
    walk_forward._build_record() but WITHOUT the "actual" key
    (outcome unknown at prediction time).
    """
    return {
        "match_id": raw.match_id,
        "game": raw.game,
        "team_a": raw.team_a,
        "team_b": raw.team_b,
        "match_date": raw.match_date,
        "event_name": raw.event_name,
        "event_tier": raw.event_tier,
        "is_lan": raw.is_lan,
        "best_of": raw.best_of,
        "score_a": raw.score_a,
        "score_b": raw.score_b,
        # Trinity features (same 5 keys as backtest)
        **prediction.to_feature_dict(),
        "high_agreement": prediction.high_agreement,
        "should_abstain": prediction.should_abstain,
    }


def build_prediction_record(
    match_id: str,
    game: str,
    team_a: str,
    team_b: str,
    pipeline_result: dict,
    market_price: Optional[float],
) -> dict:
    """
    Build dict for esports_predictions INSERT (Phase 1 write).

    predicted_winner determined by p_model: >0.5 = team_a, else team_b.
    actual_winner and correct are NULL (filled at resolution).
    """
    p_model = pipeline_result["p_model"]
    predicted_winner = team_a if p_model > 0.5 else team_b

    edge = abs(p_model - market_price) if market_price is not None else None

    conformal_set = pipeline_result.get("conformal_set")
    if isinstance(conformal_set, list):
        conformal_set = [str(c) for c in conformal_set]

    return {
        "match_id": match_id,
        "game": game,
        "predicted_winner": predicted_winner,
        "p_model": p_model,
        "p_raw": pipeline_result.get("p_raw"),
        "conformal_set": conformal_set,
        "is_singleton": pipeline_result.get("is_singleton"),
        "market_price": market_price,
        "pinnacle_odds": None,
        "edge": edge,
        "kelly_fraction": pipeline_result.get("kelly_fraction"),
        "actual_winner": None,
        "correct": None,
        "mode": "shadow",
        "model_version": "v2-trinity",
    }
