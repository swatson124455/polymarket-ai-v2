"""
Esports DB Helpers — upsert matches, teams, predictions, calibration.

Mirrors sports/data/sports_db.py pattern. All methods accept an optional
db (AsyncSession) parameter injected by the caller.

Usage::
    from esports.data.esports_db import upsert_esports_match, get_calibration
    await upsert_esports_match(db, match_data)
    cal = await get_calibration(db, game="lol", market_type="match_winner")
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from sqlalchemy import text as _text
from structlog import get_logger

logger = get_logger()


async def upsert_esports_team(db, team_data: Dict[str, Any]) -> None:
    """Insert or update an esports team in esports_teams table."""
    if db is None:
        return
    try:
        async with db.get_session() as session:
            await session.execute(
                _text("""
                INSERT INTO esports_teams (external_id, name, game, region, logo_url)
                VALUES (:external_id, :name, :game, :region, :logo_url)
                ON CONFLICT (external_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    region = EXCLUDED.region,
                    logo_url = EXCLUDED.logo_url,
                    updated_at = NOW()
                """),
                {
                    "external_id": str(team_data.get("id", "")),
                    "name": str(team_data.get("name", "")),
                    "game": str(team_data.get("game", "")),
                    "region": str(team_data.get("region", "")),
                    "logo_url": str(team_data.get("logo_url", "")),
                },
            )
            await session.commit()
    except Exception as exc:
        logger.debug("esports_db: upsert_team failed", error=str(exc))


async def upsert_esports_match(db, match_data: Dict[str, Any]) -> None:
    """Insert or update an esports match in esports_matches table."""
    if db is None:
        return
    try:
        async with db.get_session() as session:
            await session.execute(
                _text("""
                INSERT INTO esports_matches
                    (external_id, game, tournament, team_a, team_b, team_a_id, team_b_id,
                     best_of, status, score_a, score_b, scheduled_at)
                VALUES
                    (:external_id, :game, :tournament, :team_a, :team_b, :team_a_id, :team_b_id,
                     :best_of, :status, :score_a, :score_b, :scheduled_at)
                ON CONFLICT (external_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    score_a = EXCLUDED.score_a,
                    score_b = EXCLUDED.score_b,
                    updated_at = NOW()
                """),
                {
                    "external_id": str(match_data.get("match_id", "")),
                    "game": str(match_data.get("game", "")),
                    "tournament": str(match_data.get("tournament", "")),
                    "team_a": str(match_data.get("team_a", "")),
                    "team_b": str(match_data.get("team_b", "")),
                    "team_a_id": str(match_data.get("team_a_id", "")),
                    "team_b_id": str(match_data.get("team_b_id", "")),
                    "best_of": int(match_data.get("best_of", 1)),
                    "status": str(match_data.get("status", "not_started")),
                    "score_a": int(match_data.get("score_a", 0)),
                    "score_b": int(match_data.get("score_b", 0)),
                    "scheduled_at": match_data.get("scheduled_at"),
                },
            )
            await session.commit()
    except Exception as exc:
        logger.debug("esports_db: upsert_match failed", error=str(exc))


async def log_esports_prediction(
    db,
    match_id: str,
    market_id: str,
    game: str,
    model_prob: float,
    market_price: float,
    side: str,
    confidence: float,
    bot_name: str,
) -> None:
    """Log an esports prediction to the esports_predictions table."""
    if db is None:
        return
    try:
        async with db.get_session() as session:
            await session.execute(
                _text("""
                INSERT INTO esports_live_events
                    (match_id, game, event_type, description, confidence, market_side, edge_estimate)
                VALUES
                    (:match_id, :game, 'prediction', :description, :confidence, :side, :edge)
                """),
                {
                    "match_id": match_id,
                    "game": game,
                    "description": f"{bot_name} prediction: model={model_prob:.3f} market={market_price:.3f}",
                    "confidence": confidence,
                    "side": side,
                    "edge": abs(model_prob - market_price),
                },
            )
            await session.commit()
    except Exception as exc:
        logger.debug("esports_db: log_prediction failed", error=str(exc))


async def get_calibration(
    db, game: str, market_type: str = "match_winner"
) -> Optional[Dict[str, Any]]:
    """
    Get calibration data for a (game, market_type) pair.

    Returns dict with: bet_count, correct_count, brier_score, kelly_fraction.
    """
    if db is None:
        return None
    try:
        async with db.get_session() as session:
            result = await session.execute(
                _text("""
                SELECT bet_count, correct_count, brier_score, kelly_fraction
                FROM esports_calibration
                WHERE game = :game AND market_type = :market_type
                """),
                {"game": game, "market_type": market_type},
            )
            row = result.first()
            if row:
                return {
                    "bet_count": row.bet_count,
                    "correct_count": row.correct_count,
                    "brier_score": row.brier_score,
                    "kelly_fraction": row.kelly_fraction,
                }
    except Exception as exc:
        logger.debug("esports_db: get_calibration failed", error=str(exc))
    return None


async def log_prediction(
    db,
    match_id: str,
    game: str,
    market_id: str,
    bot_name: str,
    predicted_prob: float,
    market_price: float,
    side: str,
    edge: float,
) -> None:
    """Log a prediction to esports_prediction_log for accuracy tracking."""
    if db is None:
        logger.warning("esports_db: log_prediction skipped — db is None")
        return
    try:
        async with db.get_session() as session:
            await session.execute(
                _text("""
                INSERT INTO esports_prediction_log
                    (match_id, game, market_id, bot_name, predicted_prob, market_price, side, edge)
                VALUES
                    (:match_id, :game, :market_id, :bot_name, :predicted_prob, :market_price, :side, :edge)
                """),
                {
                    "match_id": match_id,
                    "game": game,
                    "market_id": market_id,
                    "bot_name": bot_name,
                    "predicted_prob": predicted_prob,
                    "market_price": market_price,
                    "side": side,
                    "edge": edge,
                },
            )
            await session.commit()
    except Exception as exc:
        logger.warning("esports_db: log_prediction failed", error=str(exc))


async def resolve_predictions(db, market_id: str, outcome: int) -> int:
    """
    Backfill actual_outcome for all unresolved predictions on a market.

    Args:
        market_id: The resolved market ID.
        outcome: 1 = YES won, 0 = NO won.

    Returns:
        Number of predictions resolved.
    """
    if db is None:
        return 0
    try:
        async with db.get_session() as session:
            result = await session.execute(
                _text("""
                UPDATE esports_prediction_log
                SET actual_outcome = :outcome, resolved_at = NOW()
                WHERE market_id = :market_id AND actual_outcome IS NULL
                """),
                {"market_id": market_id, "outcome": outcome},
            )
            await session.commit()
            return result.rowcount if hasattr(result, "rowcount") else 0
    except Exception as exc:
        logger.debug("esports_db: resolve_predictions failed", error=str(exc))
        return 0


async def get_rolling_accuracy(
    db, game: str, bot_name: str = "", last_n: int = 50
) -> Optional[Dict[str, Any]]:
    """
    Compute rolling accuracy for a game (and optionally a specific bot).

    Returns:
        Dict with: total, correct, accuracy, brier_score. None if no data.
    """
    if db is None:
        return None
    try:
        bot_filter = "AND bot_name = :bot_name" if bot_name else ""
        params: Dict[str, Any] = {"game": game, "limit": last_n}
        if bot_name:
            params["bot_name"] = bot_name

        async with db.get_session() as session:
            result = await session.execute(
                _text(f"""
                SELECT predicted_prob, actual_outcome, side
                FROM esports_prediction_log
                WHERE game = :game AND actual_outcome IS NOT NULL {bot_filter}
                ORDER BY created_at DESC
                LIMIT :limit
                """),
                params,
            )
            rows = result.fetchall()
        if not rows:
            return None

        total = len(rows)
        correct = 0
        brier_sum = 0.0
        for row in rows:
            pred = float(row.predicted_prob)
            actual = int(row.actual_outcome)
            predicted_outcome = 1 if pred > 0.5 else 0
            if predicted_outcome == actual:
                correct += 1
            brier_sum += (pred - actual) ** 2

        return {
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0.0,
            "brier_score": brier_sum / total if total > 0 else 1.0,
        }
    except Exception as exc:
        logger.debug("esports_db: get_rolling_accuracy failed", error=str(exc))
        return None


async def update_prediction_closing_price(
    db, match_id: str, market_id: str, closing_price: float
) -> int:
    """
    Record the market closing price for CLV (Closing Line Value) tracking.

    Called when a match transitions to "running" — the last market price
    before game start is the closing line. CLV = predicted_prob - closing_price.
    Positive CLV means we beat the closing line (real edge signal).

    Returns number of rows updated.
    """
    if db is None:
        return 0
    try:
        async with db.get_session() as session:
            result = await session.execute(
                _text("""
                UPDATE esports_prediction_log
                SET closing_price = :closing_price
                WHERE match_id = :match_id
                  AND market_id = :market_id
                  AND closing_price IS NULL
                """),
                {
                    "match_id": match_id,
                    "market_id": market_id,
                    "closing_price": closing_price,
                },
            )
            await session.commit()
            return result.rowcount if hasattr(result, "rowcount") else 0
    except Exception as exc:
        logger.debug("esports_db: update_closing_price failed", error=str(exc))
        return 0


async def compute_clv_stats(
    db, game: str, days: int = 30
) -> Optional[Dict[str, Any]]:
    """
    Compute Closing Line Value stats for recent predictions.

    CLV = (predicted_prob - closing_price) for YES bets.
    Positive CLV = beating the closing line = evidence of real edge.
    Pinnacle research: CLV has r²=0.997 against outcomes across 397k+ matches.

    Returns:
        Dict with: total, clv_positive_count, avg_clv, clv_hit_rate.
        None if no data.
    """
    if db is None:
        return None
    try:
        async with db.get_session() as session:
            result = await session.execute(
                _text("""
                SELECT predicted_prob, market_price, closing_price, side, actual_outcome
                FROM esports_prediction_log
                WHERE game = :game
                  AND closing_price IS NOT NULL
                  AND created_at > NOW() - INTERVAL ':days days'
                ORDER BY created_at DESC
                """),
                {"game": game, "days": days},
            )
            rows = result.fetchall()
        if not rows:
            return None

        total = 0
        clv_sum = 0.0
        clv_positive = 0

        for row in rows:
            pred_prob = float(row.predicted_prob)
            closing = float(row.closing_price)

            # CLV: did our prediction beat the closing line?
            # For YES bets: CLV = predicted_prob - closing_price
            # For NO bets: CLV = (1 - predicted_prob) - (1 - closing_price) = closing_price - predicted_prob
            if row.side == "YES":
                clv = pred_prob - closing
            else:
                clv = closing - pred_prob

            clv_sum += clv
            if clv > 0:
                clv_positive += 1
            total += 1

        return {
            "total": total,
            "clv_positive_count": clv_positive,
            "avg_clv": clv_sum / total if total > 0 else 0.0,
            "clv_hit_rate": clv_positive / total if total > 0 else 0.0,
        }
    except Exception as exc:
        logger.debug("esports_db: compute_clv_stats failed", error=str(exc))
        return None


async def analyze_edge_decay(
    db, game: str = "", days: int = 30, n_bins: int = 5,
) -> Optional[Dict[str, Any]]:
    """Analyze how prediction edge decays with time-to-resolution.

    Groups resolved predictions by how long before resolution they were made,
    then computes actual edge (profit if you bet at that time) per bin.

    Edge decay tells us:
      - Fast decay → bet immediately when edge is found
      - Slow decay → we have time, can wait for better prices
      - No decay → market is inefficient for extended periods

    Args:
        db: Database session.
        game: Filter by game (empty = all games).
        days: Lookback window.
        n_bins: Number of time bins.

    Returns:
        Dict with bins (each having avg_edge, avg_profit, n_predictions, hours_before_close),
        or None if insufficient data.
    """
    if db is None:
        return None
    try:
        game_filter = "AND game = :game" if game else ""
        params: dict = {"days": days}
        if game:
            params["game"] = game

        async with db.get_session() as session:
            result = await session.execute(
                _text(f"""
                SELECT edge, predicted_prob, market_price, actual_outcome, side, created_at,
                       closing_price
                FROM esports_prediction_log
                WHERE actual_outcome IS NOT NULL
                  AND closing_price IS NOT NULL
                  {game_filter}
                  AND created_at > NOW() - INTERVAL '{days} days'
                ORDER BY created_at ASC
                """),
                params,
            )
            rows = result.fetchall()
        if not rows or len(rows) < 20:
            return None

        # Compute profit per prediction: did the edge convert to actual profit?
        entries = []
        for row in rows:
            edge = float(row.edge)
            pred = float(row.predicted_prob)
            market_p = float(row.market_price)
            actual = int(row.actual_outcome)
            closing = float(row.closing_price)

            # CLV: edge at closing vs edge at prediction time
            clv = abs(pred - market_p) - abs(pred - closing)

            # Actual profit: simplified binary P&L
            if row.side == "YES":
                profit = (1.0 - market_p) if actual == 1 else -market_p
            else:
                profit = market_p if actual == 0 else -(1.0 - market_p)

            entries.append({
                "edge": abs(edge),
                "clv": clv,
                "profit": profit,
            })

        # Sort by edge magnitude and bin
        entries.sort(key=lambda e: e["edge"], reverse=True)
        bin_size = max(1, len(entries) // n_bins)
        bins = []
        for i in range(n_bins):
            start = i * bin_size
            end = start + bin_size if i < n_bins - 1 else len(entries)
            bucket = entries[start:end]
            if not bucket:
                continue
            bins.append({
                "bin": i,
                "n": len(bucket),
                "avg_edge": round(sum(e["edge"] for e in bucket) / len(bucket), 4),
                "avg_clv": round(sum(e["clv"] for e in bucket) / len(bucket), 4),
                "avg_profit": round(sum(e["profit"] for e in bucket) / len(bucket), 4),
                "win_rate": round(
                    sum(1 for e in bucket if e["profit"] > 0) / len(bucket), 4
                ),
            })

        return {
            "game": game or "all",
            "total_predictions": len(entries),
            "days": days,
            "bins": bins,
        }
    except Exception as exc:
        logger.debug("esports_db: analyze_edge_decay failed", error=str(exc))
        return None


async def update_calibration(
    db,
    game: str,
    market_type: str,
    bet_count: int,
    correct_count: int,
    brier_score: float,
    kelly_fraction: float,
) -> None:
    """Upsert calibration row for a (game, market_type) pair."""
    if db is None:
        return
    try:
        async with db.get_session() as session:
            await session.execute(
                _text("""
                INSERT INTO esports_calibration (game, market_type, bet_count, correct_count, brier_score, kelly_fraction)
                VALUES (:game, :market_type, :bet_count, :correct_count, :brier_score, :kelly_fraction)
                ON CONFLICT (game, market_type) DO UPDATE SET
                    bet_count = EXCLUDED.bet_count,
                    correct_count = EXCLUDED.correct_count,
                    brier_score = EXCLUDED.brier_score,
                    kelly_fraction = EXCLUDED.kelly_fraction,
                    updated_at = NOW()
                """),
                {
                    "game": game,
                    "market_type": market_type,
                    "bet_count": bet_count,
                    "correct_count": correct_count,
                    "brier_score": brier_score,
                    "kelly_fraction": kelly_fraction,
                },
            )
            await session.commit()
    except Exception as exc:
        logger.debug("esports_db: update_calibration failed", error=str(exc))
