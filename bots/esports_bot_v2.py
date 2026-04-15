"""
EsportsBot v2 — Paper trading bot with shadow prediction logging.

Extends BaseBot. Combines 5v2-C (shadow predictions) and 5v2-D (paper trading)
in a single bot. Predictions logged to esports_predictions (mode='shadow'),
trades executed via BaseBot.place_order() in SIMULATION_MODE.

Architecture:
  Startup: Load Trinity snapshot (or rebuild from DB) → fit pipeline
  Scan loop (120s):
    1. Resolve finished matches → update ratings → Phase 2 writes
    2. Predict upcoming matches → Phase 1 writes
    3. Execute trades for singletons with edge (unless dry-run)
    4. Check existing positions for exits

Two-phase write:
  Phase 1: INSERT prediction at prediction time (actual_winner=NULL)
  Phase 2: UPDATE with actual_winner + correct when match resolves
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from bots.base_bot import BaseBot
from base_engine.base_engine import BaseEngine
from esports_v2.data.normalizer import RawMatch, raw_to_match_result
from esports_v2.model.pipeline import EsportsPipeline
from esports_v2.ratings.trinity import Trinity
from esports_v2.shadow.match_converter import (
    build_feature_record,
    build_prediction_record,
    esports_match_to_db_row,
    esports_match_to_raw,
)
from esports_v2.shadow import db as shadow_db

logger = logging.getLogger(__name__)

# Defaults (overridable via env)
_GAMES = os.getenv("ESPORTS_V2_GAMES", "cs2,lol").split(",")
_DRY_RUN = os.getenv("ESPORTS_V2_DRY_RUN", "false").lower() in ("true", "1", "yes")
_RETRAIN_EVERY = int(os.getenv("ESPORTS_V2_RETRAIN_EVERY", "50"))
_RETRAIN_MIN_INTERVAL = int(os.getenv("ESPORTS_V2_RETRAIN_MIN_INTERVAL", "3600"))
_UPCOMING_HOURS = int(os.getenv("ESPORTS_V2_UPCOMING_HOURS", "48"))
_PAST_DAYS = int(os.getenv("ESPORTS_V2_PAST_DAYS", "7"))
_STALE_DAYS = int(os.getenv("ESPORTS_V2_STALE_DAYS", "45"))
_SNAPSHOT_DIR = Path(os.getenv("ESPORTS_V2_SNAPSHOT_DIR", "data/snapshots"))


class EsportsBotV2(BaseBot):
    """EsportsBot v2 — Trinity ratings + XGBoost + Venn-ABERS + MAPIE."""

    def __init__(self, bot_name: str, base_engine: BaseEngine):
        super().__init__(bot_name, base_engine)
        self._trinity = Trinity()
        self._pipeline = EsportsPipeline()
        self._pandascore = None  # initialized in _initialize()
        self._market_scanner = None
        self._market_service = None

        # In-memory state
        self._training_records: List[dict] = []
        self._predicted_match_ids: Set[str] = set()
        self._processed_match_ids: Set[str] = set()
        self._team_last_match: Dict[str, datetime] = {}  # team → last match date
        self._matches_since_retrain = 0
        self._last_retrain_time = 0.0
        self._initialized = False
        self._pending_predictions: List[dict] = []  # predictions awaiting trade execution

        # Config
        self._games = [g.strip() for g in _GAMES]
        self._dry_run = _DRY_RUN

    async def _initialize(self) -> None:
        """Load historical data, rebuild Trinity, fit pipeline."""
        if self._initialized:
            return

        # Initialize PandaScore client
        from esports.data.pandascore_client import PandaScoreClient
        from config.settings import settings
        api_key = getattr(settings, "PANDASCORE_API_KEY", None)
        if not api_key:
            logger.error("PANDASCORE_API_KEY not set — cannot start EsportsBotV2")
            return

        self._pandascore = PandaScoreClient(api_key=api_key)
        await self._pandascore.init()

        # Initialize market scanner
        try:
            from esports.markets.esports_market_scanner import EsportsMarketScanner
            db = getattr(self.base_engine, "db", None)
            self._market_scanner = EsportsMarketScanner(db=db)
        except Exception as e:
            logger.warning(f"Market scanner init failed: {e}")

        # Try loading snapshot, fall back to full DB rebuild
        snapshot_loaded = await self._load_snapshot()
        if not snapshot_loaded:
            await self._rebuild_from_db()

        self._initialized = True
        logger.info(
            f"EsportsBotV2 initialized: "
            f"games={self._games} matches={self._trinity.match_count} "
            f"training_records={len(self._training_records)} "
            f"dry_run={self._dry_run}"
        )

    async def _rebuild_from_db(self) -> None:
        """Full Trinity rebuild from esports_matches table."""
        db = getattr(self.base_engine, "db", None)
        if not db:
            logger.error("No DB available — cannot rebuild Trinity")
            return

        async with db.get_session() as session:
            matches = await shadow_db.load_historical_matches(session, self._games)

        logger.info(f"Rebuilding Trinity from {len(matches)} historical matches")
        t0 = time.monotonic()

        for m in matches:
            raw = RawMatch(
                match_id=m["match_id"],
                game=m["game"],
                event_name=m.get("event_name"),
                event_tier=m.get("event_tier"),
                team_a=m["team_a"],
                team_b=m["team_b"],
                winner=m.get("winner"),
                score_a=m.get("score_a"),
                score_b=m.get("score_b"),
                best_of=m.get("best_of"),
                match_date=m.get("match_date"),
                is_lan=m.get("is_lan", False),
                source=m.get("source", "db"),
            )
            mr = raw_to_match_result(raw)
            prediction = self._trinity.process_match(mr)
            self._processed_match_ids.add(raw.match_id)

            # Track team freshness
            if raw.match_date:
                try:
                    dt = datetime.fromisoformat(raw.match_date.replace("Z", "+00:00"))
                    if dt.tzinfo:
                        dt = dt.replace(tzinfo=None)
                    self._team_last_match[raw.team_a] = max(
                        self._team_last_match.get(raw.team_a, datetime.min), dt
                    )
                    self._team_last_match[raw.team_b] = max(
                        self._team_last_match.get(raw.team_b, datetime.min), dt
                    )
                except (ValueError, TypeError):
                    pass

            # Build training record (has "actual" for pipeline.fit)
            record = build_feature_record(raw, prediction)
            record["actual"] = 1 if raw.winner == raw.team_a else 0
            self._training_records.append(record)

        elapsed = time.monotonic() - t0
        logger.info(f"Trinity rebuilt in {elapsed:.1f}s ({len(matches)} matches)")

        # Fit pipeline on accumulated training data
        if len(self._training_records) >= 50:
            self._pipeline.fit(self._training_records)
            self._last_retrain_time = time.monotonic()
            logger.info(f"Pipeline fitted on {len(self._training_records)} records")
        else:
            logger.warning(f"Only {len(self._training_records)} records — pipeline underfit")

    async def scan_and_trade(self) -> None:
        """Main scan cycle. Called by BaseBot._scan_loop() every interval."""
        if not self._initialized:
            await self._initialize()
            if not self._initialized:
                return

        # 1. Process resolved matches (ratings update + Phase 2 writes)
        await self._resolve_finished_matches()

        # 2. Predict upcoming matches (Phase 1 writes)
        await self._predict_upcoming_matches()

        # 3. Execute trades for singletons with edge
        if not self._dry_run:
            await self._execute_trades()

    async def _resolve_finished_matches(self) -> None:
        """Fetch recently finished matches, update ratings, resolve predictions."""
        if not self._pandascore:
            return

        db = getattr(self.base_engine, "db", None)
        if not db:
            return

        new_matches = 0
        for game in self._games:
            try:
                past = await self._pandascore.get_past_matches(game, days_back=_PAST_DAYS)
            except Exception as e:
                logger.warning(f"PandaScore get_past_matches failed game={game}: {e}")
                continue

            for match in past:
                match_id = f"ps_{match.match_id}"
                if match_id in self._processed_match_ids:
                    continue

                # Insert into esports_matches
                row = esports_match_to_db_row(match)
                async with db.get_session() as session:
                    await shadow_db.insert_match(session, row)
                    await session.commit()

                # Update Trinity ratings
                raw = esports_match_to_raw(match)
                mr = raw_to_match_result(raw)
                prediction = self._trinity.process_match(mr)
                self._processed_match_ids.add(match_id)

                # Track team freshness
                if raw.match_date:
                    try:
                        dt = datetime.fromisoformat(raw.match_date.replace("Z", "+00:00"))
                        if dt.tzinfo:
                            dt = dt.replace(tzinfo=None)
                        self._team_last_match[raw.team_a] = max(
                            self._team_last_match.get(raw.team_a, datetime.min), dt
                        )
                        self._team_last_match[raw.team_b] = max(
                            self._team_last_match.get(raw.team_b, datetime.min), dt
                        )
                    except (ValueError, TypeError):
                        pass

                # Build training record for future retrains
                record = build_feature_record(raw, prediction)
                record["actual"] = 1 if raw.winner == raw.team_a else 0
                self._training_records.append(record)

                new_matches += 1

                # Phase 2 write: resolve any shadow predictions
                winner = raw.winner
                if winner:
                    async with db.get_session() as session:
                        # Check which team won for correct determination
                        # Need to compare against each prediction's predicted_winner
                        from sqlalchemy import text
                        result = await session.execute(
                            text("""
                                UPDATE esports_predictions
                                SET actual_winner = :winner,
                                    correct = (predicted_winner = :winner)
                                WHERE match_id = :mid
                                  AND mode = 'shadow'
                                  AND actual_winner IS NULL
                            """),
                            {"mid": match_id, "winner": winner},
                        )
                        if result.rowcount > 0:
                            logger.info(
                                f"shadow_resolved match={match_id} winner={winner} "
                                f"rows={result.rowcount}"
                            )
                        await session.commit()

        if new_matches > 0:
            self._matches_since_retrain += new_matches
            logger.info(
                f"Processed {new_matches} new matches. "
                f"matches_since_retrain={self._matches_since_retrain}"
            )

            # Retrain check: threshold AND minimum interval
            now = time.monotonic()
            if (
                self._matches_since_retrain >= _RETRAIN_EVERY
                and (now - self._last_retrain_time) >= _RETRAIN_MIN_INTERVAL
                and len(self._training_records) >= 50
            ):
                logger.info(f"Retraining pipeline ({self._matches_since_retrain} new matches)")
                self._pipeline.fit(self._training_records)
                self._matches_since_retrain = 0
                self._last_retrain_time = now

    async def _predict_upcoming_matches(self) -> None:
        """Fetch upcoming matches, generate predictions, Phase 1 writes."""
        if not self._pandascore:
            return

        db = getattr(self.base_engine, "db", None)
        if not db:
            return

        self._pending_predictions.clear()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for game in self._games:
            try:
                upcoming = await self._pandascore.get_upcoming_matches(game, hours_ahead=_UPCOMING_HOURS)
            except Exception as e:
                logger.warning(f"PandaScore get_upcoming_matches failed game={game}: {e}")
                continue

            for match in upcoming:
                match_id = f"ps_{match.match_id}"

                # Skip if already predicted
                if match_id in self._predicted_match_ids:
                    continue

                # Skip if missing teams
                if not match.team_a or not match.team_b:
                    continue

                # Stale rating guard: both teams must have recent matches
                if not self._teams_are_fresh(match.team_a, match.team_b, now):
                    continue

                # Get Trinity prediction (predict only, don't update ratings)
                trinity_pred = self._trinity.predict(match.team_a, match.team_b, game)

                # Skip if Trinity says abstain
                if trinity_pred.should_abstain:
                    continue

                # Build feature record and run pipeline
                raw = esports_match_to_raw(match)
                record = build_feature_record(raw, trinity_pred)
                pipeline_result = self._pipeline.predict(record)

                # Find Polymarket market price
                market_price = await self._get_market_price(match, game)

                # Override edge with Polymarket price if available
                if market_price is not None:
                    pipeline_result["market_price"] = market_price
                    pipeline_result["edge"] = abs(pipeline_result["p_model"] - market_price)

                # Phase 1 write: INSERT prediction (actual_winner=NULL)
                pred_record = build_prediction_record(
                    match_id=match_id,
                    game=game,
                    team_a=match.team_a,
                    team_b=match.team_b,
                    pipeline_result=pipeline_result,
                    market_price=market_price,
                )

                async with db.get_session() as session:
                    already = await shadow_db.prediction_exists(session, match_id)
                    if not already:
                        await shadow_db.insert_prediction(session, pred_record)
                        await session.commit()
                        self._predicted_match_ids.add(match_id)

                # Queue for trading if singleton with edge
                if pipeline_result.get("is_singleton") and pipeline_result.get("edge", 0) >= 0.05:
                    self._pending_predictions.append({
                        "match": match,
                        "pipeline_result": pipeline_result,
                        "market_price": market_price,
                        "pred_record": pred_record,
                    })

    async def _execute_trades(self) -> None:
        """Place paper trades for singletons with sufficient edge."""
        for item in self._pending_predictions:
            match = item["match"]
            result = item["pipeline_result"]
            market_price = item["market_price"]

            if market_price is None:
                continue  # No Polymarket market found

            # Determine trade side
            p_model = result["p_model"]
            if p_model > 0.5:
                side = "YES"
                price = market_price
            else:
                side = "NO"
                price = 1.0 - market_price

            stake = result.get("stake", 0)
            if stake <= 0:
                continue

            # Find market + token for trading
            market_info = await self._find_market_info(match, match.game)
            if not market_info:
                continue

            token_id = market_info.get("yes_token_id") if side == "YES" else market_info.get("no_token_id")
            if not token_id:
                continue

            try:
                await self.place_order(
                    market_id=str(market_info.get("id", market_info.get("condition_id", ""))),
                    token_id=token_id,
                    side=side,
                    size=stake,
                    price=price,
                    confidence=result["p_model"],
                    prediction=result["p_model"],
                )
            except Exception as e:
                logger.warning(f"Trade failed for {match.team_a} vs {match.team_b}: {e}")

    def _teams_are_fresh(self, team_a: str, team_b: str, now: datetime) -> bool:
        """Check both teams have a match within the stale threshold."""
        from datetime import timedelta
        cutoff = now - timedelta(days=_STALE_DAYS)
        a_fresh = self._team_last_match.get(team_a, datetime.min) >= cutoff
        b_fresh = self._team_last_match.get(team_b, datetime.min) >= cutoff
        return a_fresh and b_fresh

    async def _get_market_price(self, match, game: str) -> Optional[float]:
        """Find Polymarket market price for this match. Returns None if not found."""
        if not self._market_scanner:
            return None
        try:
            markets = await self._market_scanner.find_markets_for_match(
                match_id=str(match.match_id),
                game=game,
                team_names=[match.team_a, match.team_b],
            )
            if markets:
                # Use first match_winner type market
                for m in markets:
                    price = m.get("yes_price")
                    if price is not None and 0.03 < price < 0.97:
                        return price
        except Exception as e:
            logger.debug(f"Market price lookup failed: {e}")
        return None

    async def _find_market_info(self, match, game: str) -> Optional[dict]:
        """Find full market info (id, tokens) for trading."""
        if not self._market_scanner:
            return None
        try:
            markets = await self._market_scanner.find_markets_for_match(
                match_id=str(match.match_id),
                game=game,
                team_names=[match.team_a, match.team_b],
            )
            if markets:
                for m in markets:
                    if m.get("yes_token_id") and m.get("no_token_id"):
                        return m
        except Exception as e:
            logger.debug(f"Market info lookup failed: {e}")
        return None

    # ── Snapshot persistence ──────────────────────────────────────

    async def _save_snapshot(self) -> None:
        """Serialize Trinity ratings + metadata to JSON for fast restart."""
        _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "match_count": self._trinity.match_count,
            "processed_ids": list(self._processed_match_ids),
            "team_last_match": {
                k: v.isoformat() for k, v in self._team_last_match.items()
            },
            "elo": {},
            "glicko": {},
            "openskill": {},
        }

        for game in self._trinity.get_games():
            snapshot["elo"][game] = {
                k: v.to_dict() for k, v in self._trinity.get_elo_ratings(game).items()
            }
            snapshot["glicko"][game] = {
                k: v.to_dict() for k, v in self._trinity.get_glicko_ratings(game).items()
            }
            snapshot["openskill"][game] = {
                k: v.to_dict() for k, v in self._trinity.get_openskill_ratings(game).items()
            }

        path = _SNAPSHOT_DIR / "trinity_snapshot.json"
        with open(path, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)
        logger.info(f"Trinity snapshot saved: {path} ({self._trinity.match_count} matches)")

    async def _load_snapshot(self) -> bool:
        """Load Trinity snapshot. Returns False if missing or stale."""
        path = _SNAPSHOT_DIR / "trinity_snapshot.json"
        if not path.exists():
            logger.info("No Trinity snapshot found — will rebuild from DB")
            return False

        try:
            with open(path, "r") as f:
                snapshot = json.load(f)

            # Restore processed IDs
            self._processed_match_ids = set(snapshot.get("processed_ids", []))

            # Restore team freshness
            for team, ts in snapshot.get("team_last_match", {}).items():
                try:
                    self._team_last_match[team] = datetime.fromisoformat(ts)
                except (ValueError, TypeError):
                    pass

            # TODO: Restore rating engine state from snapshot
            # For now, we still rebuild from DB but use the processed_ids
            # to skip already-known matches during incremental load
            logger.info(
                f"Snapshot loaded: {snapshot.get('match_count', 0)} matches, "
                f"{len(self._processed_match_ids)} processed IDs"
            )

            # Still need to rebuild Trinity from DB (full restore of rating
            # engine state from JSON is deferred — requires engine-specific
            # from_dict() methods on Elo/Glicko/OpenSkill)
            await self._rebuild_from_db()
            return True

        except Exception as e:
            logger.warning(f"Snapshot load failed: {e} — rebuilding from DB")
            return False

    async def flush_state(self) -> None:
        """Save snapshot on graceful shutdown."""
        try:
            await self._save_snapshot()
        except Exception as e:
            logger.warning(f"Snapshot save on shutdown failed: {e}")

    async def stop(self):
        """Graceful shutdown: save snapshot, close PandaScore client."""
        await self.flush_state()
        if self._pandascore:
            await self._pandascore.close()
        await super().stop()
