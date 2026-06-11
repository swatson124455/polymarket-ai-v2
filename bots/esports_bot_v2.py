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

import asyncio
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
# Item 2: in-memory pending-prediction roll-over window. Items not
# traded within this many hours of being queued are pruned at the next
# _execute_trades. 4h is generous enough to survive a deploy-cycle
# (~5.5 min cold-start) plus a normal scan interval; short enough that
# market prices used at sizing time are still meaningful.
_PENDING_STALE_HOURS = float(os.getenv("ESPORTS_V2_PENDING_STALE_HOURS", "4.0"))
# Item 6: CS2 tier-1 league filter applied at predict-emit. Per S214 audit
# §10.4, CS2 has ~1.8% match rate dominantly because Polymarket does not
# create markets for tier-2/3 CS2 events; the matcher correctly rejects
# them, but every prediction still floods the esports_unmatched_predictions
# table. Filtering at predict-emit eliminates the noise without losing
# tradeable signal. LoL is intentionally NOT filtered — its 22.7% match
# rate spans many tiers and the gap is alias-mapping, not tier.
# Default explicitly excludes bare "Major" (would substring-match any
# tournament with "major" in the name). Each entry is treated as a
# case-insensitive substring against (match.tournament or match.league).
_TIER1_LEAGUES_CS2 = [
    s.strip() for s in os.getenv(
        "ESPORTS_V2_TIER1_LEAGUES_CS2",
        "BLAST,IEM,PGL Major,ESL Pro League",
    ).split(",") if s.strip()
]
_SNAPSHOT_DIR = Path(os.getenv("ESPORTS_V2_SNAPSHOT_DIR", "data/snapshots"))
# S181 #3: fail-open prediction_log write for cross-bot observability parity
# with MB/WB (mirror_bot.py:2810, weather_bot.py:881). Flip to false in .env +
# restart to disable without a code revert. Writes are strictly additive — not
# safety-critical — so fail-open is appropriate.
_PREDICTION_LOG_ENABLED = os.getenv("EB_V2_PREDICTION_LOG_ENABLED", "true").lower() in ("true", "1", "yes")
# Instantiate EsportsMarketService in _initialize() and wire it into the
# scanner so find_markets_for_match() has a data source. Without this flag
# enabled (or without the wiring), the scanner's Strategy 1 (market_service)
# and Strategy 2 (polymarket_client fallback) both short-circuit and the
# scanner returns [] on every call — the A4 passthrough fix is then dormant
# because it has no input to project over. Default on; flip to false in .env
# + restart to disable without a code revert.
_MARKET_SERVICE_ENABLED = os.getenv("ESPORTS_V2_MARKET_SERVICE_ENABLED", "true").lower() in ("true", "1", "yes")


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
        # S237 (matched=0 / zero-trades root fix): matches predicted while no
        # Polymarket market existed yet, kept for re-check on later scans. The
        # bot predicts up to _UPCOMING_HOURS (48h) ahead, but Polymarket lists
        # the head-to-head market hours-to-days later (verified 2026-05-31:
        # every H2H market matching an unmatched team-pair had created_at AFTER
        # the prediction's event_time). The one-shot prediction guard (line
        # ~629) then skipped the match forever, so the late-created market was
        # never traded. _recheck_awaiting_markets() re-runs the matcher for
        # these until a market appears (queued once + removed) or the match ages
        # out of the lookahead window. In-memory only: cross-restart recovery
        # (re-seed from esports_predictions) is a documented follow-up; on
        # restart these are dropped — never double-traded, since only NEW
        # in-session predictions are ever added here.
        # match_id -> {"match", "pipeline_result", "game", "created_at"}
        self._awaiting_market: Dict[str, dict] = {}
        # Per-scan funnel counters logged at _execute_trades entry; reset in
        # _predict_upcoming_matches. Defensive init in case execute is called
        # before predict (shouldn't happen by scan_and_trade contract).
        self._scan_counters: Dict[str, int] = {
            "upcoming_seen": 0,
            "singletons": 0,
            "matched": 0,
            "queued": 0,
        }
        # S195 Day 2: heavy init (snapshot/DB rebuild + pipeline fit) runs as
        # a background task so the BaseEngine 120s startup-hold is not blocked
        # by the cold-fit (~5.5 min). scan_and_trade() gates on this task
        # being done before predicting.
        self._warmup_task: Optional[asyncio.Task[None]] = None

        # S235: scan-stall self-watchdog state. EsportsBotV2 is a sibling of
        # EsportsBot (both extend BaseBot); the watchdog added to EsportsBot in
        # S233 never covered V2 — the primary trader. _scan_start_mono is
        # refreshed at the top of every scan_and_trade(); the watchdog SIGTERMs
        # the process (systemd Restart=always) if it stalls past threshold.
        self._scan_start_mono: float = 0.0
        self._stall_watchdog_task: Optional[asyncio.Task[None]] = None

        # Config
        self._games = [g.strip() for g in _GAMES]
        self._dry_run = _DRY_RUN

    async def start(self):
        """Override BaseBot.start() to spin warmup concurrent with scan loop."""
        await self._lightweight_init()
        if self._pandascore is not None:
            self._warmup_task = asyncio.create_task(
                self._heavy_warmup(), name="esports_bot_v2_warmup",
            )
        await super().start()
        # S235: launch the scan-stall watchdog AFTER super().start() (which sets
        # running=True and starts the scan loop). Not gated on running — see
        # _scan_stall_watchdog for why.
        self._stall_watchdog_task = asyncio.create_task(self._scan_stall_watchdog())
        self._stall_watchdog_task.add_done_callback(self._task_error_handler)

    async def stop(self) -> None:
        """S235: cancel the scan-stall watchdog, then run BaseBot cleanup."""
        if self._stall_watchdog_task and not self._stall_watchdog_task.done():
            self._stall_watchdog_task.cancel()
            try:
                await self._stall_watchdog_task
            except asyncio.CancelledError:
                pass
        await super().stop()

    async def _scan_stall_watchdog(self) -> None:
        """S235: Recover a hung scan loop by exiting for a systemd restart.

        Mirrors EsportsBot._scan_stall_watchdog (added S233) — which only ever
        covered V1. EsportsBotV2 is the primary trader and had no backstop: a
        scan loop wedged on a corrupted asyncpg connection (shared-DB
        contention) sat dead with no recovery (observed 2026-05-30, ~13h: both
        esports bots stale, the V1 watchdog never armed, V2 had none).

        Watches self._scan_start_mono (refreshed at the top of scan_and_trade).
        If no scan has started within the stall threshold — and at least one
        has, so cold-start is safe — it SIGTERMs its own process so systemd
        (Restart=always) restarts with a clean DB pool.

        Loops unconditionally (NOT `while self.running`): the failure modes it
        recovers from leave it disarmed if gated on running (startup race before
        super().start() sets running=True; base_bot setting running=False after
        max scan failures). Only intended exit is cancellation from stop().
        Purely time-based: it wraps/cancels no DB operation — client-side
        cancellation of a DB await is exactly what corrupts asyncpg (RULE ZERO
        rule 6 / S162), i.e. the failure we recover from.
        """
        from config.settings import settings
        _interval = float(getattr(settings, "ESPORTS_STALL_WATCHDOG_INTERVAL_S", 60.0))
        _threshold = float(getattr(settings, "ESPORTS_STALL_RESTART_THRESHOLD_S", 900.0))
        # 2026-06-11 startup grace (cycle-breaker) — mirrors EsportsBot.
        # The restart churn was self-sustaining: force-exit → cold start
        # (training + conn-open burst) → PgBouncer client_login_timeout kills
        # nascent conns → scan #1 grinds 90s-bounded dead-conn timeouts past
        # the 900s threshold → force-exit → repeat (~25min cadence). Suppress
        # the force-exit until uptime > threshold+grace; genuine wedges still
        # die at ~35min (vs the 18.75h hangs this watchdog exists for).
        _grace = float(getattr(settings, "ESPORTS_STALL_STARTUP_GRACE_S", 1200.0))
        self._stall_watchdog_armed_mono = time.monotonic()
        logger.info(
            "esports_v2_scan_stall_watchdog_armed",
            interval_s=_interval,
            threshold_s=_threshold,
            startup_grace_s=_grace,
        )
        while True:
            await asyncio.sleep(_interval)
            _start_mono = getattr(self, "_scan_start_mono", 0.0) or 0.0
            if _start_mono <= 0.0:
                continue  # no scan has started yet — not armed (cold-start safe)
            _age = time.monotonic() - _start_mono
            if _age > _threshold:
                _uptime = time.monotonic() - (
                    getattr(self, "_stall_watchdog_armed_mono", 0.0) or 0.0
                )
                if _uptime < _threshold + _grace:
                    logger.warning(
                        "esports_v2_scan_stall_within_startup_grace",
                        scan_age_s=round(_age, 1),
                        uptime_s=round(_uptime, 1),
                        grace_until_uptime_s=round(_threshold + _grace, 1),
                        detail=("stall detected but process is still inside the "
                                "cold-start grace window — suppressing force-exit "
                                "so warmup can finish and the conn pool can heal"),
                    )
                    continue
                logger.critical(
                    "esports_v2_scan_stall_self_restart",
                    scan_age_s=round(_age, 1),
                    threshold_s=_threshold,
                    detail=("scan loop has not started a new cycle within "
                            "threshold (likely wedged DB pool) — force-exiting "
                            "for systemd restart"),
                )
                # WI-21b: name the wedged await in the journal before exiting.
                try:
                    from bots.esports_bot import _log_stalled_task_stacks
                    _log_stalled_task_stacks()
                except Exception:
                    pass
                # S235: os._exit, NOT os.kill(SIGTERM). The watchdog only fires
                # when the process is already wedged, so SIGTERM's graceful
                # shutdown handler hangs on the SAME wedged pool — and systemd
                # does NOT force-kill a self-sent SIGTERM (TimeoutStopSec applies
                # only to `systemctl stop`), so the process sticks in
                # shutdown-limbo and never restarts (observed 2026-05-30 02:04
                # UTC: both watchdogs fired, process hung 5min+, PID unchanged).
                # os._exit bypasses all handlers → immediate exit → systemd
                # Restart=always restarts with a clean pool. State is DB-backed
                # (State Persistence), so a forced exit loses nothing.
                import sys  # flush the critical log first — os._exit does no
                sys.stdout.flush()  # buffer flushing and stdout→journald is
                sys.stderr.flush()  # block-buffered, so the line would be lost
                os._exit(1)
                return  # unreachable in prod (os._exit never returns); kept so
                #         unit tests that mock os._exit exit the loop deterministically

    async def _initialize(self) -> None:
        """Back-compat shim: synchronous init for callers that still expect
        full readiness on return. Equivalent to lightweight init followed by
        an immediate await of the heavy warmup. Tests and any external code
        that called the pre-S195-Day-2 _initialize() get the same semantics.
        """
        if self._initialized:
            return
        await self._lightweight_init()
        if self._pandascore is None:
            return
        await self._heavy_warmup()

    async def _lightweight_init(self) -> None:
        """Fast init: PandaScore client, market service, scanner.

        No DB-heavy work. Returns in seconds so super().start() can enter
        the scan loop within the BaseEngine startup-hold window.
        """
        if self._pandascore is not None:
            return
        from esports.data.pandascore_client import PandaScoreClient
        from config.settings import settings
        api_key = getattr(settings, "PANDASCORE_API_KEY", None)
        if not api_key:
            logger.error("PANDASCORE_API_KEY not set — cannot start EsportsBotV2")
            return

        self._pandascore = PandaScoreClient(api_key=api_key)
        await self._pandascore.init()

        # Initialize market service + scanner. The service's background
        # refresh keeps the markets table fresh and provides the scanner
        # with paired-token market dicts. Constructor injection mirrors
        # EsportsLiveBot._initialize() at bots/esports_live_bot.py:107-118.
        try:
            from esports.markets.esports_market_scanner import EsportsMarketScanner
            db = getattr(self.base_engine, "db", None)
            _poly_client = getattr(self.base_engine, "client", None)

            if _MARKET_SERVICE_ENABLED:
                try:
                    from esports.markets.esports_market_service import EsportsMarketService
                    self._market_service = EsportsMarketService(
                        db=db, polymarket_client=_poly_client,
                    )
                    self._market_service.start_background_refresh()
                    logger.info("esports_v2_market_service_initialized")
                except Exception as exc:
                    logger.warning(
                        "esports_v2_market_service_init_failed", error=str(exc),
                    )
                    self._market_service = None

            self._market_scanner = EsportsMarketScanner(
                db=db,
                polymarket_client=_poly_client,
                market_service=self._market_service,
            )
        except Exception as e:
            logger.warning(f"Market scanner init failed: {e}")

        # Signal exposure-restored to base_engine's startup-hold gate.
        # EB v2 has no in-memory daily-exposure counter to restore (unlike
        # MirrorBot's paper_trades SUM or WeatherBot's group/city exposure),
        # so there's nothing to recover — but the base_engine still gates
        # ready_to_trade on this flag (base_engine.py:1272). Pre-fix the
        # flag never fired, the 120s startup_hold watchdog fired on every
        # restart, the bot entered degraded mode, and `missing=['exposure_restored']`
        # showed up in journal at every cold start. _heavy_warmup runs
        # async in background and gates predictions via _warmup_complete()
        # so signaling exposure-restored here doesn't risk premature trading.
        if getattr(self, "base_engine", None) is not None:
            self.base_engine.mark_exposure_restored()

    async def _heavy_warmup(self) -> None:
        """Snapshot load / DB rebuild / pipeline fit — the slow cold path.

        Runs as a background task (kicked off from start()) so a 5.5-min
        cold fit no longer pushes total init past the 120s BaseEngine
        startup-hold. scan_and_trade() refuses to predict until this
        task is .done() and exception-free.
        """
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
        _pipeline_path = _SNAPSHOT_DIR / "pipeline.skops"
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

    def _warmup_complete(self) -> bool:
        """Return True iff heavy warmup finished successfully.

        Fail-loud contract: if the warmup task raised, this method re-raises
        on the next call so the scan loop surfaces the failure rather than
        silently scanning with an unfit model.
        """
        if self._initialized:
            return True
        task = self._warmup_task
        if task is None or not task.done():
            return False
        if task.cancelled():
            logger.warning("esports_bot_v2_warmup_cancelled")
            return False
        exc = task.exception()
        if exc is not None:
            logger.error(
                "esports_bot_v2_warmup_failed",
                error=str(exc), error_type=type(exc).__name__,
            )
            raise exc
        return self._initialized

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
        """Main scan cycle. Called by BaseBot._scan_loop() every interval.

        Gates on _warmup_complete() — during the cold-start window (snapshot
        load / DB rebuild / pipeline fit), this returns early so the scan
        loop ticks but does not attempt to predict against an unfit model.
        Fails loud if the warmup task ended with an exception.

        Latency instrumentation: BaseBot brackets this call with `scan_start`
        / `scan_done` marks on `self._latency_tracker`. Marking after each
        of the three phases produces a `Latency breakdown` log with
        per-phase elapsed-ms keys (scan_start>resolve_done = resolve time,
        resolve_done>predict_done = predict time, predict_done>scan_done =
        execute time) — needed because the scan was logging only the
        end-to-end elapsed and a 16–21s steady-state was unattributable.
        """
        # S235: feed the scan-stall watchdog at scan-loop entry — before the
        # warmup gate, so a healthy loop merely warming up keeps the watchdog
        # fed, while a wedged loop freezes this timestamp past threshold.
        self._scan_start_mono = time.monotonic()
        if not self._warmup_complete():
            logger.info("esports_bot_v2_scan_skipped_warmup_in_progress")
            return

        # 1. Process resolved matches (ratings update + Phase 2 writes)
        await self._resolve_finished_matches()
        self.mark_latency("resolve_done")

        # 2. Predict upcoming matches (Phase 1 writes)
        await self._predict_upcoming_matches()
        self.mark_latency("predict_done")

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

                # Phase 2 write: resolve any predictions for this match
                # (shadow OR live — both want the same actual_winner result;
                # filtering by mode here would silently strand live-mode
                # predictions once Item 4 mode-parameterization lands).
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
                self._pipeline.save(_SNAPSHOT_DIR / "pipeline.skops")
                self._matches_since_retrain = 0
                self._last_retrain_time = now

    async def _predict_upcoming_matches(self) -> None:
        """Fetch upcoming matches, generate predictions, Phase 1 writes."""
        if not self._pandascore:
            return

        db = getattr(self.base_engine, "db", None)
        if not db:
            return

        # Item 2: do NOT clear _pending_predictions — items roll over across
        # scans within the stale window so deploy-induced restarts and mid-scan
        # exceptions don't strand work that's about to be traded. Pruning of
        # traded/stale items happens in _execute_trades.
        self._scan_counters = {"upcoming_seen": 0, "singletons": 0, "matched": 0, "queued": 0}
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # S237: before predicting new matches, re-check matches predicted on a
        # prior scan that had no Polymarket market yet — their H2H market may
        # have been created since. Runs after the counter reset so its matched/
        # queued increments are reflected in the funnel log emitted by
        # _execute_trades.
        await self._recheck_awaiting_markets(now)

        for game in self._games:
            try:
                upcoming = await self._pandascore.get_upcoming_matches(game, hours_ahead=_UPCOMING_HOURS)
            except Exception as e:
                logger.warning(f"PandaScore get_upcoming_matches failed game={game}: {e}")
                continue

            self._scan_counters["upcoming_seen"] += len(upcoming)

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

                # Item 6: CS2 tier filter — only emit predictions for matches
                # in tier-1 leagues where Polymarket actually creates markets.
                # Tier-2/3 CS2 events historically have no markets and just
                # flood esports_unmatched_predictions. LoL gap is alias not
                # tier, so this filter is CS2-only by intent.
                if game == "cs2":
                    tournament_text = (
                        (getattr(match, "tournament", None) or "")
                        + " "
                        + (getattr(match, "league", None) or "")
                    ).strip().lower()
                    if not tournament_text:
                        # No tournament info → can't classify as tier-1 → skip.
                        # Explicit branch so future relaxation of this rule
                        # is a deliberate change, not a silent edge-case.
                        continue
                    if not any(tag.lower() in tournament_text for tag in _TIER1_LEAGUES_CS2):
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

                if pipeline_result.get("is_singleton"):
                    self._scan_counters["singletons"] += 1

                # Find Polymarket market (both price and market_id). S181 #3:
                # captures market_id in addition to price so the prediction_log
                # write below can reference it. _get_market_price kept unchanged
                # for signature stability (no other current callers).
                market_info = await self._find_polymarket_for_match(match, game)
                market_price = market_info.get("price") if market_info else None
                market_id = market_info.get("market_id") if market_info else None

                if market_price is not None:
                    self._scan_counters["matched"] += 1

                # Override edge AND recompute Kelly sizing with the real
                # market price. Earlier code only overrode market_price and
                # edge but left pipeline_result["stake"] computed against the
                # default-stub market_price=0.5 from inside pipeline.predict().
                # That meant the queueing condition (singleton + edge>=0.05)
                # would pass with the real price while stake was set against
                # the stub price — coincidentally non-zero for high-prob
                # predictions due to MAX_BET_USD cap, but the underlying
                # Kelly math was wrong. S213 root-cause fix: re-run sizing.
                if market_price is not None:
                    pipeline_result["market_price"] = market_price
                    sizing = self._pipeline.compute_sizing(
                        p_model=pipeline_result["p_model"],
                        is_singleton=pipeline_result["is_singleton"],
                        market_price=market_price,
                    )
                    pipeline_result["edge"] = sizing["edge"]
                    pipeline_result["kelly_fraction"] = sizing["kelly_fraction"]
                    pipeline_result["stake"] = sizing["stake"]

                # Phase 1 write: INSERT prediction (actual_winner=NULL).
                # Item 4: mode reflects the bot's actual run mode so eval
                # queries can split shadow vs live cleanly post-flip. Pre-S215
                # the writer hardcoded "shadow" — eval pipeline would have
                # silently conflated the two once dry_run flipped.
                pred_record = build_prediction_record(
                    match_id=match_id,
                    game=game,
                    team_a=match.team_a,
                    team_b=match.team_b,
                    pipeline_result=pipeline_result,
                    market_price=market_price,
                    mode="live" if not self._dry_run else "shadow",
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

                # Queue for trading if singleton with edge AND a real Polymarket
                # market was found. S237: the `market_price is not None` guard is
                # new. Previously a market-less prediction whose stub-price edge
                # happened to be >= 0.05 would queue here and then be silently
                # skipped in _execute_trades (market_price is None → continue) —
                # a dead queue slot. It now routes to _awaiting_market for
                # re-check when its market is later created.
                if market_price is not None and pipeline_result.get("is_singleton") and pipeline_result.get("edge", 0) >= 0.05:
                    # Item 8: cache the inner market dict (with paired tokens)
                    # so _execute_trades doesn't re-query the matcher. Mitigates
                    # the cache-eviction route-mismatch risk where the trade
                    # could land on a different market than the prediction
                    # was sized against.
                    cached_market = market_info.get("market") if market_info else None
                    self._pending_predictions.append({
                        "match": match,
                        "pipeline_result": pipeline_result,
                        "market_price": market_price,
                        "pred_record": pred_record,
                        "created_at": now,
                        "traded_at": None,
                        "market_info": cached_market,
                    })
                    self._scan_counters["queued"] += 1
                elif market_price is None and pipeline_result.get("is_singleton"):
                    # S237 root fix for matched=0/zero-trades. The match is now
                    # in _predicted_match_ids, so without this it is skipped
                    # forever (line ~629) and the H2H market that Polymarket
                    # creates hours-to-days later is never traded. Stash the
                    # tradeable-structure (singleton) prediction for
                    # _recheck_awaiting_markets() to pick up once a market
                    # appears. Keyed by match_id — idempotent re-stash.
                    self._awaiting_market[match_id] = {
                        "match": match,
                        "pipeline_result": pipeline_result,
                        "game": game,
                        "created_at": now,
                    }

    async def _recheck_awaiting_markets(self, now: datetime) -> None:
        """S237: re-run the matcher for matches predicted while no Polymarket
        market existed yet, and queue them once a market appears.

        Root cause this fixes (matched=0 / zero-trades): the bot predicts up to
        _UPCOMING_HOURS (48h) ahead, but Polymarket lists the head-to-head match
        market hours-to-days later. The one-shot prediction guard (line ~629)
        then skips the match on every later scan, so the late-created market is
        never picked up. Verified 2026-05-31: every H2H market matching an
        unmatched team-pair had created_at AFTER the prediction's event_time.

        Each _awaiting_market entry is re-checked every scan until either its
        market appears (then queued once + removed) or it ages past the lookahead
        window (then dropped — the match has started). The matcher's own 120s
        result cache bounds the DB cost. In-memory only: dropped on restart, so a
        match predicted-but-unmatched before a restart is not recovered (the
        guard at line ~633 skips it). That is deliberate — the predict loop only
        ever adds NEW in-session predictions here, so a forced restart can never
        cause a double-trade. Cross-restart recovery (re-seed from
        esports_predictions) is a documented follow-up.

        Per-entry failures are logged and skipped, never propagated: this is an
        auxiliary pass and must not abort the core resolve/predict/execute scan.
        """
        if not self._awaiting_market:
            return
        from datetime import timedelta
        window = timedelta(hours=_UPCOMING_HOURS)
        for match_id in list(self._awaiting_market.keys()):
            try:
                entry = self._awaiting_market[match_id]
                # Aged out of the lookahead window — the match has (nearly)
                # started; a market appearing now is moot. Stop watching.
                if (now - entry["created_at"]) > window:
                    del self._awaiting_market[match_id]
                    continue
                match = entry["match"]
                game = entry["game"]
                pipeline_result = entry["pipeline_result"]
                market_info = await self._find_polymarket_for_match(match, game)
                market_price = market_info.get("price") if market_info else None
                if market_price is None:
                    continue  # still no market — keep watching on the next scan
                # Market appeared. Recompute sizing against the real price
                # (mirrors the predict-loop `if market_price is not None` block)
                # and apply the same singleton + edge queue gate. Either way the
                # market now exists, so stop watching this match.
                del self._awaiting_market[match_id]
                self._scan_counters["matched"] += 1
                pipeline_result["market_price"] = market_price
                sizing = self._pipeline.compute_sizing(
                    p_model=pipeline_result["p_model"],
                    is_singleton=pipeline_result["is_singleton"],
                    market_price=market_price,
                )
                pipeline_result["edge"] = sizing["edge"]
                pipeline_result["kelly_fraction"] = sizing["kelly_fraction"]
                pipeline_result["stake"] = sizing["stake"]
                if pipeline_result.get("is_singleton") and pipeline_result.get("edge", 0) >= 0.05:
                    cached_market = market_info.get("market") if market_info else None
                    self._pending_predictions.append({
                        "match": match,
                        "pipeline_result": pipeline_result,
                        "market_price": market_price,
                        # Shadow prediction already written at predict time; the
                        # trade path does not read pred_record (see _execute_trades).
                        "pred_record": None,
                        "created_at": now,
                        "traded_at": None,
                        "market_info": cached_market,
                    })
                    self._scan_counters["queued"] += 1
                    logger.info(
                        "esports_v2_awaiting_market_queued",
                        match_id=match_id,
                        team_a=match.team_a,
                        team_b=match.team_b,
                        game=game,
                        market_price=round(float(market_price), 4),
                        edge=round(float(pipeline_result.get("edge", 0.0)), 4),
                    )
            except Exception as e:
                logger.warning(
                    "esports_v2_recheck_awaiting_failed",
                    match_id=match_id,
                    error=str(e),
                )

    async def _execute_trades(self) -> None:
        """Place paper trades for singletons with sufficient edge.

        Item 2 invariants:
          - Items with traded_at set are skipped (idempotency).
          - Items past the stale window are pruned (not retried at outdated prices).
          - place_order success path sets traded_at; exceptions leave it None
            so the next scan retries (deploy-restart resilience).
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale = timedelta(hours=_PENDING_STALE_HOURS)
        # Prune: drop already-traded items + anything past the stale window.
        # Items missing created_at (defensive — pre-Item-2 holdovers) are also pruned.
        self._pending_predictions = [
            item for item in self._pending_predictions
            if item.get("traded_at") is None
               and item.get("created_at") is not None
               and (now - item["created_at"]) <= stale
        ]
        logger.info(
            "esports_v2_scan_funnel",
            pending=len(self._pending_predictions),
            upcoming_seen=self._scan_counters.get("upcoming_seen", 0),
            singletons=self._scan_counters.get("singletons", 0),
            matched=self._scan_counters.get("matched", 0),
            queued=self._scan_counters.get("queued", 0),
        )
        for item in self._pending_predictions:
            match = item["match"]
            result = item["pipeline_result"]
            market_price = item["market_price"]

            if market_price is None:
                continue  # No Polymarket market found

            # Determine trade side — Bug A2 fix (eb/main, 2026-05-24):
            # Select side by edge direction (p_model vs market_price), NOT by
            # p_model > 0.5. Pre-fix bug: when p_model and market_price were on
            # the same side of 0.5 but the model was less extreme than the market
            # (e.g., p_model=0.55, market_price=0.65), code picked YES (because
            # p_model > 0.5) but EV-positive bet was NO. Sizing (which used the
            # same wrong selector) returned Kelly=0 → stake=0 → continue at line
            # below → silent no-trade. Five days of 0 esports_v2_trade_attempt
            # logs traced here. MUST stay in sync with the side-selection branch
            # in esports_v2/model/pipeline.py:compute_sizing — both use
            # `p_model > market_price` now.
            p_model = result["p_model"]
            if p_model > market_price:
                side = "YES"
                price = market_price
            else:
                side = "NO"
                price = 1.0 - market_price

            stake = result.get("stake", 0)
            if stake <= 0:
                # Observability for the zero-stake silent-skip class
                # (Bug A2 post-fix audit gap, 2026-05-25). Emit so a
                # regression that silently re-zeroes stakes is visible
                # in journalctl. Path should be rare post-A2.
                logger.info(
                    "esports_v2_zero_stake_skip",
                    side=side,
                    p_model=round(float(p_model), 4),
                    market_price=round(float(market_price), 4),
                    edge=round(float(result.get("edge", 0.0)), 4),
                    stake=round(float(stake), 4),
                    team_a=match.team_a,
                    team_b=match.team_b,
                )
                continue

            # Item 8: use the market dict cached at predict time. The previous
            # _find_market_info call here was a redundant matcher round-trip
            # against the same scanner with a 120s TTL; if eviction happened
            # between predict and execute, the trade could land on a different
            # market than the one used for sizing.
            market_info = item.get("market_info")
            if not market_info:
                continue

            token_id = market_info.get("yes_token_id") if side == "YES" else market_info.get("no_token_id")
            if not token_id:
                continue

            # Branch B prep: per-attempt diagnostic + post-attempt rejection
            # log. S215 EB CLOSE §2.4 noted the risk_manager rejection log
            # lacks a side= field, so NO-side Bug A investigation had no
            # inline context. These two logs add it at the EB v2 layer —
            # grep `side=NO` in journalctl surfaces the trade-attempt chain.
            _edge = float(result.get("edge", 0.0))
            _market_id_str = str(market_info.get("id", market_info.get("condition_id", "")))
            logger.info(
                "esports_v2_trade_attempt",
                side=side,
                p_model=round(float(p_model), 4),
                market_price=round(float(market_price), 4),
                effective_price=round(float(price), 4),
                edge=round(_edge, 4),
                stake_usd=round(float(stake), 2),
                market_id=_market_id_str,
                team_a=match.team_a,
                team_b=match.team_b,
            )
            try:
                order_result = await self.place_order(
                    market_id=_market_id_str,
                    token_id=token_id,
                    side=side,
                    size=stake,
                    price=price,
                    confidence=result["p_model"],
                    prediction=(1.0 - result["p_model"]) if side == "NO" else result["p_model"],
                )
                if isinstance(order_result, dict) and not order_result.get("success", False):
                    logger.info(
                        "esports_v2_trade_rejected",
                        side=side,
                        p_model=round(float(p_model), 4),
                        effective_price=round(float(price), 4),
                        edge=round(_edge, 4),
                        error=str(order_result.get("error", "unknown")),
                    )
                # Item 2: idempotency — set inside success path. Rejection paths
                # also reach here (place_order returns dict on rejection without
                # raising), so the prediction won't be retried at the same price.
                # The stale window provides eventual cleanup. Only true exceptions
                # leave traded_at unset → retried at next scan.
                item["traded_at"] = now
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

        Item 8: also requires paired tokens (yes_token_id + no_token_id) so the
        cached market dict on _pending_predictions is sufficient for routing the
        trade at execute time — eliminating the second matcher round-trip that
        previously happened in _execute_trades via _find_market_info. Markets
        passing the price+market_id filter but lacking paired tokens are logged
        once (Protocol 10: silent-loop emission must be observable).

        Returns dict with keys {market_id, price, market} where the inner
        `market` dict has the paired tokens (used by _execute_trades).
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
            if not markets:
                return None
            missing_yes = 0
            missing_no = 0
            for m in markets:
                price = m.get("yes_price")
                if price is None or not (0.03 < price < 0.97):
                    continue
                mid = m.get("market_id")
                if mid is None:
                    continue
                yes_tok = m.get("yes_token_id")
                no_tok = m.get("no_token_id")
                if yes_tok and no_tok:
                    return {"market_id": str(mid), "price": float(price), "market": m}
                if not yes_tok:
                    missing_yes += 1
                if not no_tok:
                    missing_no += 1
            if missing_yes or missing_no:
                logger.warning(
                    "esports_v2_market_info_no_token_pair",
                    match_id=str(match.match_id),
                    game=game,
                    markets_returned=len(markets),
                    missing_yes=missing_yes,
                    missing_no=missing_no,
                )
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
                self._pipeline.save(_SNAPSHOT_DIR / "pipeline.skops")
        except Exception as e:
            logger.warning(f"Snapshot save on shutdown failed: {e}")

    async def stop(self):
        """Graceful shutdown: save snapshot, close PandaScore + market service.

        Item 2: log predictions_stranded_at_shutdown if the queue contains
        untraded items still within the stale window. These are work the bot
        was about to do but didn't — invisible to operators without this log.
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale = timedelta(hours=_PENDING_STALE_HOURS)
        stranded = [
            item for item in self._pending_predictions
            if item.get("traded_at") is None
               and item.get("created_at") is not None
               and (now - item["created_at"]) <= stale
        ]
        if stranded:
            logger.warning(
                "predictions_stranded_at_shutdown",
                count=len(stranded),
                sample_match_ids=[
                    str(item["match"].match_id) for item in stranded[:5]
                ],
            )
        await self.flush_state()
        if self._pandascore:
            await self._pandascore.close()
        if self._market_service:
            try:
                await self._market_service.close()
            except Exception as exc:
                logger.debug("esports_v2_market_service_close_failed", error=str(exc))
        await super().stop()
