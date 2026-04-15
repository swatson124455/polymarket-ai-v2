"""
Async DB operations for EsportsBot v2 shadow/paper mode.

All functions take an async SQLAlchemy session and use text() for raw SQL.
This shares the connection pool with BaseBot's existing database module.
Scoped to esports_matches + esports_predictions tables (migration 072).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def load_historical_matches(session, games: List[str]) -> List[dict]:
    """
    Load all matches from esports_matches, sorted by match_date ASC.

    Returns dicts with keys matching RawMatch fields for Trinity rebuild.
    """
    placeholders = ", ".join(f":g{i}" for i in range(len(games)))
    params = {f"g{i}": g for i, g in enumerate(games)}

    result = await session.execute(
        text(f"""
            SELECT match_id, game, event_name, event_tier,
                   team_a, team_b, winner, score_a, score_b,
                   best_of, match_date, is_lan, source
            FROM esports_matches
            WHERE game IN ({placeholders})
            ORDER BY match_date ASC
        """),
        params,
    )
    rows = result.fetchall()
    matches = []
    for r in rows:
        matches.append({
            "match_id": r[0],
            "game": r[1],
            "event_name": r[2],
            "event_tier": r[3],
            "team_a": r[4],
            "team_b": r[5],
            "winner": r[6],
            "score_a": r[7],
            "score_b": r[8],
            "best_of": r[9],
            "match_date": str(r[10]) if r[10] else None,
            "is_lan": r[11],
            "source": r[12],
        })
    logger.info(f"shadow_db_loaded_matches count={len(matches)} games={games}")
    return matches


async def match_exists(session, match_id: str) -> bool:
    """Check if match_id exists in esports_matches."""
    result = await session.execute(
        text("SELECT 1 FROM esports_matches WHERE match_id = :mid LIMIT 1"),
        {"mid": match_id},
    )
    return result.fetchone() is not None


async def insert_match(session, match: dict) -> None:
    """Insert a match into esports_matches. Skips on conflict (match_id PK)."""
    # Parse match_date string to datetime (asyncpg requires datetime, not str)
    params = dict(match)
    md = params.get("match_date")
    if isinstance(md, str):
        from datetime import datetime
        try:
            params["match_date"] = datetime.fromisoformat(md.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            params["match_date"] = None

    await session.execute(
        text("""
            INSERT INTO esports_matches (
                match_id, game, event_name, event_tier,
                team_a, team_b, winner, score_a, score_b,
                best_of, match_date, is_lan, source
            ) VALUES (
                :match_id, :game, :event_name, :event_tier,
                :team_a, :team_b, :winner, :score_a, :score_b,
                :best_of, :match_date, :is_lan, :source
            ) ON CONFLICT (match_id) DO NOTHING
        """),
        params,
    )


async def prediction_exists(session, match_id: str, mode: str = "shadow") -> bool:
    """Check if we already have a prediction for this match + mode."""
    result = await session.execute(
        text("""
            SELECT 1 FROM esports_predictions
            WHERE match_id = :mid AND mode = :mode
            LIMIT 1
        """),
        {"mid": match_id, "mode": mode},
    )
    return result.fetchone() is not None


async def insert_prediction(session, pred: dict) -> None:
    """
    Phase 1 write: INSERT prediction with actual_winner=NULL, correct=NULL.
    """
    await session.execute(
        text("""
            INSERT INTO esports_predictions (
                match_id, game, predicted_winner, p_model, p_raw,
                conformal_set, is_singleton, market_price, pinnacle_odds,
                edge, kelly_fraction, actual_winner, correct,
                mode, model_version
            ) VALUES (
                :match_id, :game, :predicted_winner, :p_model, :p_raw,
                :conformal_set, :is_singleton, :market_price, :pinnacle_odds,
                :edge, :kelly_fraction, :actual_winner, :correct,
                :mode, :model_version
            )
        """),
        pred,
    )
    logger.info(
        f"shadow_prediction_inserted match_id={pred['match_id']} "
        f"p_model={pred['p_model']:.3f} singleton={pred['is_singleton']}"
    )


async def resolve_prediction(
    session, match_id: str, actual_winner: str, correct: bool
) -> int:
    """
    Phase 2 write: UPDATE actual_winner and correct for shadow predictions.

    Returns number of rows updated.
    """
    result = await session.execute(
        text("""
            UPDATE esports_predictions
            SET actual_winner = :winner, correct = :correct
            WHERE match_id = :mid AND mode = 'shadow' AND actual_winner IS NULL
        """),
        {"mid": match_id, "winner": actual_winner, "correct": correct},
    )
    n = result.rowcount
    if n > 0:
        logger.info(
            f"shadow_prediction_resolved match_id={match_id} "
            f"winner={actual_winner} correct={correct}"
        )
    return n


async def get_unresolved_match_ids(session) -> List[str]:
    """Get match_ids of shadow predictions where actual_winner IS NULL."""
    result = await session.execute(
        text("""
            SELECT DISTINCT match_id FROM esports_predictions
            WHERE mode = 'shadow' AND actual_winner IS NULL
        """)
    )
    return [r[0] for r in result.fetchall()]


async def get_shadow_stats(session) -> Dict:
    """
    Compute current shadow gate metrics from esports_predictions.

    Returns dict with counts, accuracy, Brier, CLV — or empty if no data.
    """
    result = await session.execute(
        text("""
            SELECT
                COUNT(*) AS n_total,
                COUNT(*) FILTER (WHERE is_singleton = true) AS n_singletons,
                COUNT(*) FILTER (WHERE actual_winner IS NOT NULL) AS n_resolved,
                COUNT(*) FILTER (WHERE correct = true AND is_singleton = true) AS n_correct_sing,
                COUNT(*) FILTER (WHERE actual_winner IS NOT NULL AND is_singleton = true) AS n_resolved_sing,
                AVG(CASE
                    WHEN actual_winner IS NOT NULL THEN
                        POWER(p_model - CASE WHEN correct THEN 1.0 ELSE 0.0 END, 2)
                END) AS brier,
                AVG(CASE
                    WHEN actual_winner IS NOT NULL AND market_price IS NOT NULL THEN
                        ABS(p_model) - market_price
                END) AS clv_mean
            FROM esports_predictions
            WHERE mode = 'shadow'
        """)
    )
    row = result.fetchone()
    if not row or row[0] == 0:
        return {"n_total": 0}

    n_resolved_sing = row[4] or 0
    accuracy_sing = (row[3] / n_resolved_sing) if n_resolved_sing > 0 else None

    return {
        "n_total": row[0],
        "n_singletons": row[1],
        "n_resolved": row[2],
        "accuracy_singletons": accuracy_sing,
        "brier": float(row[5]) if row[5] is not None else None,
        "clv_polymarket_mean": float(row[6]) if row[6] is not None else None,
    }
