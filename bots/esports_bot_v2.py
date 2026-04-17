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

from structlog import get_logger
logger = get_logger()

# Defaults (overridable via env)
_GAMES = os.getenv("ESPORTS_V2_GAMES", "cs2,lol").split(",")
_DRY_RUN = os.getenv("ESPORTS_V2_DRY_RUN", "false").lower() in ("true", "1", "yes")
_RETRAIN_EVERY = int(os.getenv("ESPORTS_V2_RETRAIN_EVERY", "50"))
_RETRAIN_MIN_INTERVAL = int(os.getenv("ESPORTS_V2_RETRAIN_MIN_INTERVAL", "3600"))
_UPCOMING_HOURS = int(os.getenv("ESPORTS_V2_UPCOMING_HOURS", "48"))
_PAST_DAYS = int(os.getenv("ESPORTS_V2_PAST_DAYS", "7"))
_STALE_DAYS = int(os.getenv("ESPORTS_V2_STALE_DAYS", "45"))
_SNAPSHOT_DIR = Path(os.getenv("ESPORTS_V2_SNAPSHOT_DIR", "data/snapshots"))
# S181 #3: fail-open prediction_log write for cross-bot observability parity
# with MB/WB (mirror_bot.py:2810, weather_bot.py:881). Flip to false in .env +
# restart to disable without a code revert. Writes are strictly additive — not
# safety-critical — so fail-open is appropriate.
_PREDICTION_LOG_ENABLED = os.getenv("EB_V2_PREDICTION_LOG_ENABLED", "true").lower() in ("true", "1", "yes")


class EsportsBotV2(BaseBot):
    """EsportsBot v2 — Trinity ratings + XGBoost + Venn-ABERS + MAPIE."""

    def __init__(self, base_engine: BaseEngine):
        super().__init__("EsportsBotV2", base_engine)
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

    async def start(self):
        """Override BaseBot.start() to run initialization before scan loop."""
        await self._initialize()
        await super().start()

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
        if snapshot_loaded:
            # Snapshot restored Trinity ratings. Still need to build training
            # records and fit the pipeline (XGBoost/Venn-ABERS/conformal).
            # Use restored Trinity's predict() (not process_match) for features.
            await self._build_training_records_from_db()
        else:
            await self._rebuild_from_db()

        # S177: Try loading cached pipeline snapshot (skips 5.5-min fit)
        _pipeline_path = _SNAPSHOT_DIR / "pipeline.joblib"
        pipeline_loaded = self._pipeline.load(_pipeline_path)

        # Fit pipeline on training records if snapshot missing/stale/incompatible
        if not pipeline_loaded and len(self._training_records) >= 50:
            self._pipeline.fit(self._training_records)
            self._pipeline.save(_pipeline_path)
            self._last_retrain_time = time.monotonic()
            logger.info(f"Pipeline fitted on {len(self._training_records)} records")
        elif pipeline_loaded:
            self._last_retrain_time = time.monotonic()

        self._initialized = True
        logger.info(
            "esports_bot_v2_initialized",
            games=self._games,
            matches=self._trinity.match_count,
            training_records=len(self._training_records),
            snapshot_loaded=snapshot_loaded,
            dry_run=self._dry_run,
        )

    async def _build_training_records_from_db(self) -> None:
        """Build training records using restored Trinity (predict only, no rating updates)."""
        db = getattr(self.base_engine, "db", None)
        if not db:
            return

        async with db.get_session() as session:
            matches = await shadow_db.load_historical_matches(session, self._games)

        logger.info("building_training_records", match_count=len(matches))
        t0 = time.monotonic()

        for m in matches:
            raw = RawMatch(
                match_id=m["match_id"], game=m["game"],
                event_name=m.get("event_name"), event_tier=m.get("event_tier"),
                team_a=m["team_a"], team_b=m["team_b"],
                winner=m.get("winner"),
                score_a=m.get("score_a"), score_b=m.get("score_b"),
                best_of=m.get("best_of"), match_date=m.get("match_date"),
                is_lan=m.get("is_lan", False), source=m.get("source", "db"),
            )
            if not raw.winner:
                continue
            # Use predict() — ratings are already loaded from snapshot
            prediction = self._trinity.predict(raw.team_a, raw.team_b, raw.game)
            record = build_feature_record(raw, prediction)
            record["actual"] = 1 if raw.winner == raw.team_a else 0
            self._training_records.append(record)

        elapsed = time.monotonic() - t0
        logger.info("training_records_built", count=len(self._training_records), elapsed_s=round(elapsed, 1))

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
        logger.info("trinity_rebuilt", elapsed_s=round(elapsed, 1), matches=len(matches))

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Not used — EsportsBotV2 handles analysis inline in scan_and_trade."""
        return None

    async def scan_and_trade(self) -> None:
        """Main scan cycle. Called by BaseBot._scan_loop() every interval."""
        if not self._initialized:
            logger.warning("EsportsBotV2: not initialized, skipping scan")
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

                # Update Trinity ratings (only if winner is known)
                raw = esports_match_to_raw(match)
                if raw.winner is None:
                    self._processed_match_ids.add(match_id)
                    continue
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
                self._pipeline.save(_SNAPSHOT_DIR / "pipeline.joblib")
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

                # Skip if already predicted (in-memory cache + DB fallback)
                if match_id in self._predicted_match_ids:
                    continue
                # DB check for predictions from prior process lifetimes
                async with db.get_session() as session:
                    if await shadow_db.prediction_exists(session, match_id):
                        self._predicted_match_ids.add(match_id)
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

                # Find Polymarket market (both price and market_id). S181 #3:
                # captures market_id in addition to price so the prediction_log
                # write below can reference it. _get_market_price kept unchanged
                # for signature stability (no other current callers).
                market_info = await self._find_polymarket_for_match(match, game)
                market_price = market_info.get("price") if market_info else None
                market_id = market_info.get("market_id") if market_info else None

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
                        # Ensure match exists in esports_matches (FK requirement)
                        # Upcoming matches inserted with winner=NULL
                        row = esports_match_to_db_row(match)
                        await shadow_db.insert_match(session, row)
                        await shadow_db.insert_prediction(session, pred_record)
                        await session.commit()
                        self._predicted_match_ids.add(match_id)

                # S181 #3: cross-bot observability parity with MB/WB. Writes a
                # row to prediction_log (in addition to the shadow-schema write
                # above) so gate_score_expectancy, Venn-ABERS, and drift detectors
                # see EB v2 predictions. Only writes when a Polymarket market was
                # found (market_id + market_price not None) — no meaningful
                # prediction_log row without a market reference. Fail-silent like
                # MB/WB; the shadow write above is the source-of-truth path.
                if _PREDICTION_LOG_ENABLED and market_id is not None and market_price is not None:
                    try:
                        await db.insert_prediction_log(
                            market_id=market_id,
                            predicted_prob=pipeline_result["p_model"],
                            market_price=market_price,
                            model_name=f"esports_v2_{game}",
                            bot_name="EsportsBotV2",
                            confidence=float(pipeline_result.get("edge", 0.0)),
                        )
                    except Exception as _pl_err:
                        logger.debug("esports_v2_prediction_log_failed", error=str(_pl_err))

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

    async def _find_polymarket_for_match(self, match, game: str) -> Optional[Dict[str, Any]]:
        """S181 #3: sibling to _get_market_price that returns the full market dict
        (market_id + price + other fields) instead of just the price. Used by
        _generate_predictions to capture market_id for the prediction_log write.

        Returns dict with keys {market_id, price, ...} or None if no market found.
        Internally mirrors _get_market_price's filter logic (0.03 < price < 0.97).
        """
        if not self._market_scanner:
            logger.debug("market_dict_skip_no_scanner", match_id=match.match_id)
            return None
        try:
            markets = await self._market_scanner.find_markets_for_match(
                match_id=str(match.match_id),
                game=game,
                team_names=[match.team_a, match.team_b],
            )
            if markets:
                for m in markets:
                    price = m.get("yes_price")
                    if price is not None and 0.03 < price < 0.97:
                        mid = m.get("market_id")
                        if mid is not None:
                            return {"market_id": str(mid), "price": float(price), "market": m}
            return None
        except Exception as e:
            logger.debug("market_dict_lookup_failed", match_id=match.match_id, error=str(e))
            return None

    async def _get_market_price(self, match, game: str) -> Optional[float]:
        """Find Polymarket market price for this match. Returns None if not found."""
        if not self._market_scanner:
            logger.debug("market_price_skip_no_scanner", match_id=match.match_id)
            return None
        try:
            markets = await self._market_scanner.find_markets_for_match(
                match_id=str(match.match_id),
                game=game,
                team_names=[match.team_a, match.team_b],
            )
            if markets:
                for m in markets:
                    price = m.get("yes_price")
                    if price is not None and 0.03 < price < 0.97:
                        logger.info(
                            "market_price_found",
                            match_id=match.match_id,
                            team_a=match.team_a,
                            team_b=match.team_b,
                            price=price,
                            market_question=str(m.get("question", ""))[:60],
                        )
                        return price
            logger.debug(
                "market_price_not_found",
                match_id=match.match_id,
                team_a=match.team_a,
                team_b=match.team_b,
                markets_returned=len(markets) if markets else 0,
            )
        except Exception as e:
            logger.warning("market_price_lookup_error", match_id=match.match_id, error=str(e))
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
        from esports_v2.ratings.elo import EloRating
        from esports_v2.ratings.glicko2 import Glicko2Rating
        from esports_v2.ratings.openskill_engine import PlayerRating

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
            "openskill_players": {},
            "openskill_rosters": {},
        }

        for game in self._trinity.get_games():
            snapshot["elo"][game] = {
                k: v.to_dict() for k, v in self._trinity.get_elo_ratings(game).items()
            }
            snapshot["glicko"][game] = {
                k: v.to_dict() for k, v in self._trinity.get_glicko_ratings(game).items()
            }
            snapshot["openskill_players"][game] = {
                k: v.to_dict() for k, v in self._trinity.get_openskill_ratings(game).items()
            }
            snapshot["openskill_rosters"][game] = self._trinity._get_openskill(game).get_all_rosters()

        path = _SNAPSHOT_DIR / "trinity_snapshot.json"
        with open(path, "w") as f:
            json.dump(snapshot, f, default=str)
        logger.info(
            "trinity_snapshot_saved",
            path=str(path),
            matches=self._trinity.match_count,
            games=list(snapshot["elo"].keys()),
        )

    async def _load_snapshot(self) -> bool:
        """
        Load Trinity snapshot and fully restore rating engine state.

        Returns True if snapshot loaded successfully (no DB rebuild needed).
        Returns False if missing/corrupt (caller should rebuild from DB).
        """
        from esports_v2.ratings.elo import EloRating
        from esports_v2.ratings.glicko2 import Glicko2Rating
        from esports_v2.ratings.openskill_engine import PlayerRating

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

            # Restore Elo ratings per game
            for game, ratings in snapshot.get("elo", {}).items():
                engine = self._trinity._get_elo(game)
                for team, rd in ratings.items():
                    engine.set_rating(team, EloRating.from_dict(rd))

            # Restore Glicko-2 ratings per game
            for game, ratings in snapshot.get("glicko", {}).items():
                engine = self._trinity._get_glicko(game)
                for team, rd in ratings.items():
                    engine.set_rating(team, Glicko2Rating.from_dict(rd))

            # Restore OpenSkill player ratings + rosters per game
            for game, ratings in snapshot.get("openskill_players", {}).items():
                engine = self._trinity._get_openskill(game)
                for player, rd in ratings.items():
                    engine.set_player_rating(player, PlayerRating.from_dict(rd))
            for game, rosters in snapshot.get("openskill_rosters", {}).items():
                engine = self._trinity._get_openskill(game)
                for team, roster in rosters.items():
                    engine.set_roster(team, roster)

            # Set match count on Trinity
            self._trinity._match_count = snapshot.get("match_count", 0)

            logger.info(
                "trinity_snapshot_restored",
                matches=self._trinity.match_count,
                processed_ids=len(self._processed_match_ids),
                teams_tracked=len(self._team_last_match),
            )
            return True

        except Exception as e:
            logger.warning(f"Snapshot load failed: {e} — rebuilding from DB")
            return False

    async def flush_state(self) -> None:
        """Save snapshot on graceful shutdown."""
        try:
            await self._save_snapshot()
            # S177: Also save pipeline for fast restart
            if self._pipeline.is_fitted:
                self._pipeline.save(_SNAPSHOT_DIR / "pipeline.joblib")
        except Exception as e:
            logger.warning(f"Snapshot save on shutdown failed: {e}")

    async def stop(self):
        """Graceful shutdown: save snapshot, close PandaScore client."""
        await self.flush_state()
        if self._pandascore:
            await self._pandascore.close()
        await super().stop()
