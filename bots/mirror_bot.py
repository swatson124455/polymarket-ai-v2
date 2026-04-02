import asyncio
import math as _math
import time as _time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

_math_isfinite = _math.isfinite

from structlog import get_logger

from bots.base_bot import BaseBot
from base_engine.base_engine import BaseEngine
from config.settings import settings

logger = get_logger()


class MirrorBot(BaseBot):
    """
    MirrorBot - Real-time copy trading from top N elite traders via RTDS WebSocket.

    Architecture (S96+): RTDS global trade feed → EliteWatchlist O(1) lookup →
    _execute_mirror_trade() with 16 rejection gates → paper trading engine.
    Scan loop handles housekeeping only (exits, reaping, stats).

    Features:
    - RTDS real-time copy trading (sub-100ms latency)
    - Multi-factor confidence: category win rate + price edge + whale conviction
    - Opposing-side guard + same-side dedup (prevents hedged/duplicate positions)
    - Graduated stop-loss (15%→10%→5% by hold duration) + 96h force exit
    - Reliability-weighted sizing via EliteReliabilityTracker (Bayesian Beta)
    - Daily + category + per-market exposure caps
    """

    # Defaults (overridable via settings)
    MAX_TRACKED_TRADES: int = 10_000
    MAX_CONCURRENT_POSITIONS: int = 50
    MAX_DAILY_EXPOSURE_PCT: float = 0.15

    def __init__(self, base_engine: BaseEngine):
        super().__init__("MirrorBot", base_engine)
        self.elite_traders: List[Dict] = []
        self.mirrored_trades: OrderedDict = OrderedDict()
        self.min_confidence: float = getattr(settings, "MIRROR_MIN_CONFIDENCE", 0.45)
        self._reliability_tracker = None

        # Exit tracking: "market_id:token_id" -> position metadata
        self._open_positions: Dict[str, Dict[str, Any]] = {}

        # Daily exposure tracking
        self._daily_exposure: float = 0.0
        self._daily_reset_date: Optional[str] = None

        # Market metadata cache: market_id -> (category, time_to_res, expiry_monotonic)
        self._market_meta_cache: Dict[str, Tuple[str, str, float]] = {}
        self._MARKET_META_TTL = 3600  # 1 hour (categories/end_dates don't change during trading)

        # M1: Per-category exposure tracking (USD deployed per category)
        self._category_exposure: Dict[str, float] = {}

        # Periodic elite refresh (avoid stale list)
        self._scan_count: int = 0
        self._elite_refresh_every_n_scans: int = 480  # S96: ~6h at 45s interval (was 40/~30min)

        # Wire elite reliability if available
        try:
            from base_engine.learning.elite_reliability import EliteReliabilityTracker

            if base_engine.db:
                # S150: regime_start filters out pre-S146 data from trader WR calculations.
                # Prevents contamination from old broken gates (no NO dampener, crypto enabled, etc.)
                _regime = getattr(settings, "MIRROR_REGIME_START", None) or None
                self._reliability_tracker = EliteReliabilityTracker(
                    db=base_engine.db,
                    lookback_days=getattr(settings, "ELITE_LOOKBACK_DAYS", 365),
                    regime_start=_regime,
                )
        except Exception as e:
            logger.debug("elite reliability tracker init failed: %s", e)


        # C1: YES/NO resolution cache. Key: "market_id:token_id" → "YES"/"NO".
        # Avoids repeated DB queries for the same token across scan cycles.
        self._token_side_cache: Dict[str, str] = {}

        # Startup state restoration flag — run once on first scan.
        self._state_restored: bool = False
        # M4: Startup leader reconciliation — run on scan 3 (after watchlist initialized)
        self._recon_done: bool = False

        # Deprecation flag: MIRROR_MAX_DAILY_EXPOSURE_PCT fallback warning (log once)
        self._deprecation_warned: bool = False

        # S91: Tier 0 pre-trade filters (in-memory, <0.01ms)
        self._market_blocklist: set = set()  # market_ids to reject instantly
        self._entered_market_sides: set = set()  # {(market_id, side)} for opposing-side guard across restarts
        self._market_cooldown: Dict[str, float] = {}  # market_id -> cooldown_expiry_monotonic
        # S137 C7: Market-maker detection — same trader YES+NO same market within 24h = liquidity
        # provision, not directional signal. Key: "{trader}:{market}:{side}" → monotonic timestamp.
        self._trader_market_sides: Dict[str, float] = {}

        # S99: Portfolio circuit breaker — pause entries when unrealized P&L < threshold
        self._circuit_breaker_until: float = 0.0  # monotonic time when pause expires
        # S99b: Post-reset cooldown — prevent burst of trades after daily exposure reset
        self._daily_reset_cooldown: float = 0.0

        # S113 P2: Multi-whale consensus counter — tracks how many unique whales
        # attempted the same (market_id, side) even though same-side dedup blocks re-entry.
        # S153: Added TTL (30-min) to prevent stale consensus bonuses.
        self._whale_consensus: Dict[str, int] = {}  # legacy — kept for backward compat reads
        self._whale_consensus_ts: Dict[str, tuple] = {}  # "market_id:side" -> (count, mono_time)

        # Session 82: Calibration stack (FTS + Le2026 + conformal)
        self._calibration_stack = None
        self._calibration_fitted: bool = False
        self._calibration_fit_date: Optional[str] = None  # Session 83: track fit date for daily re-fit
        try:
            from bots.mirror_calibration import MirrorCalibrationStack
            self._calibration_stack = MirrorCalibrationStack(db=base_engine.db)
        except Exception as e:
            logger.debug("MirrorCalibrationStack init skipped: %s", e)

        # Session 82: Adaptive safety constraints (Pearl-inspired)
        self._adaptive_safety = None
        try:
            from bots.mirror_adaptive_safety import MirrorAdaptiveSafety
            self._adaptive_safety = MirrorAdaptiveSafety(db=base_engine.db)
        except Exception as e:
            logger.debug("MirrorAdaptiveSafety init skipped: %s", e)

        # S124: ML trade selector (three-way shadow race: XGBoost / Q-learning / combo)
        self._ml_selector = None
        self._ml_selector_loaded: bool = False
        try:
            from bots.mirror_ml_selector import MirrorMLSelector
            self._ml_selector = MirrorMLSelector()
        except Exception as e:
            logger.debug("MirrorMLSelector init skipped: %s", e)

        # Real-time WebSocket copy trading via EliteWatchlist + RTDS global feed
        self._watchlist = None
        self._watchlist_started: bool = False
        self._rtds_ws = None
        self._rtds_started: bool = False
        # S99b: Stale dispatch detection — reconnect if RTDS feed silently hangs
        self._prev_rtds_dispatched: int = 0
        self._rtds_stale_count: int = 0
        if getattr(settings, "WATCHLIST_ENABLED", False):
            try:
                from bots.elite_watchlist import EliteWatchlist
                self._watchlist = EliteWatchlist(base_engine.client, base_engine.db, self)
            except Exception as e:
                logger.warning("EliteWatchlist init failed: %s", e)

    def _on_bg_task_done(self, task, name):
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("bg_task_failed", task_name=name, error=str(exc))

    async def _get_token_side(self, market_id: str, token_id: str) -> str:
        """
        C1: Resolve a Polymarket token_id to 'YES' or 'NO' using the markets table.
        Polymarket Data API returns side='BUY'/'SELL'; place_order() requires 'YES'/'NO'.
        Caches results in _token_side_cache to avoid repeated DB queries.
        Returns 'YES' as fallback if market not found.
        """
        cache_key = f"{market_id}:{token_id}"
        if cache_key in self._token_side_cache:
            return self._token_side_cache[cache_key]
        try:
            db = getattr(self.base_engine, "db", None)
            if db and getattr(db, "session_factory", None):
                from sqlalchemy import text as _text
                async with db.get_session() as session:
                    row = await session.execute(
                        _text(
                            "SELECT yes_token_id, no_token_id FROM markets "
                            "WHERE condition_id = :mid OR id::text = :mid LIMIT 1"
                        ),
                        {"mid": str(market_id)},
                    )
                    r = row.fetchone()
                    if r:
                        resolved = "YES" if str(token_id) == str(r[0]) else "NO"
                        self._token_side_cache[cache_key] = resolved
                        return resolved
        except Exception as e:
            logger.debug("_get_token_side failed for %s: %s", str(market_id)[:16], e)
        return "YES"  # Fallback: assume YES token

    # M4: _update_consensus_threshold deleted — dead code (zero callers) with logic bug.
    # Bug: docstring said "3+ consecutive" but code adjusted on every single trade.

    # ── Startup State Restoration ───────────────────────────────────

    async def _restore_state_on_startup(self) -> None:
        """
        Reload _daily_exposure and _open_positions from DB after restart.

        Without this, every restart zeroes out the daily spend counter and clears
        all open position tracking — causing overspend and lost stop-loss coverage.

        _daily_exposure: seeded from today's paper_trades (YES/NO entries).
        _open_positions: rebuilt from open positions table (YES/NO rows only).
          traders set restored from positions.trader_addresses (migration 035).
          Exit-mirroring active immediately for positions that were persisted.
        """
        if self._state_restored:
            return
        self._state_restored = True

        db = getattr(self.base_engine, "db", None)
        if db is None or not getattr(db, "session_factory", None):
            return

        from sqlalchemy import text as _text
        try:
            async with db.get_session() as session:
                # 1. Seed _daily_exposure from today's trade_events (ENTRY - EXIT).
                row = await session.execute(
                    _text(
                        "SELECT "
                        "  COALESCE(SUM(CASE WHEN event_type = 'ENTRY' "
                        "    THEN CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION) ELSE 0 END), 0) "
                        "  - COALESCE(SUM(CASE WHEN event_type = 'EXIT' "
                        "    THEN CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION) ELSE 0 END), 0) "
                        "FROM trade_events "
                        "WHERE bot_name = :bot AND event_time >= CURRENT_DATE"
                    ),
                    {"bot": self.bot_name},
                )
                spent_today = float(row.scalar() or 0.0)
                self._daily_exposure = spent_today
                logger.info(
                    "MirrorBot startup: seeded _daily_exposure=%.2f from today's trade_events",
                    spent_today,
                )

                # S119: Seed _category_exposure from today's ENTRY events.
                # Without this, mid-day restarts reset category exposure to 0,
                # allowing the bot to exceed the $40k category cap.
                try:
                    _cat_rows = await session.execute(
                        _text(
                            "SELECT COALESCE(event_data->>'category', '') AS cat, "
                            "  SUM(CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION)) AS spent "
                            "FROM trade_events "
                            "WHERE bot_name = :bot AND event_type = 'ENTRY' AND event_time >= CURRENT_DATE "
                            "GROUP BY COALESCE(event_data->>'category', '')"
                        ),
                        {"bot": self.bot_name},
                    )
                    for _cr in _cat_rows.fetchall():
                        _cat_name = str(_cr[0] or "")
                        if _cat_name:
                            self._category_exposure[_cat_name] = float(_cr[1] or 0.0)
                    if self._category_exposure:
                        logger.info("MirrorBot startup: seeded _category_exposure from trade_events: %s",
                                    {k: round(v, 0) for k, v in self._category_exposure.items()})
                except Exception as _cat_err:
                    logger.debug("Category exposure seed failed (non-critical): %s", _cat_err)

                # 2. Rebuild _open_positions from positions table (YES/NO only).
                # trader_addresses column added by migration 035 — falls back to '{}' on older rows.
                rows = await session.execute(
                    _text(
                        "SELECT market_id, token_id, side, size, entry_price, "
                        "       COALESCE(current_price, entry_price) AS current_price, opened_at, "
                        "       COALESCE(trader_addresses, '{}') AS trader_addresses "
                        "FROM positions "
                        "WHERE (bot_id = :bot OR source_bot = :bot) "
                        "  AND status = 'open' AND side IN ('YES', 'NO')"
                    ),
                    {"bot": self.bot_name},
                )
                restored = 0
                for r in rows.fetchall():
                    pos_key = f"{r.market_id}:{r.token_id}"
                    if pos_key not in self._open_positions:
                        ts = r.opened_at.isoformat() if r.opened_at else datetime.now(timezone.utc).isoformat()
                        self._open_positions[pos_key] = {
                            "side": r.side,
                            "size": float(r.size or 0.0),
                            "entry_price": float(r.entry_price or 0.5),
                            "current_price": float(r.current_price or r.entry_price or 0.5),
                            "traders": set() if not r.trader_addresses or r.trader_addresses in ('{}', '[]', '') else set(r.trader_addresses),
                            "timestamp": ts,
                        }
                        restored += 1
                # S150: Enrich positions with entry_confidence for edge decay.
                # Query trade_events for the most recent ENTRY confidence per market.
                # Safe default 0.55 (min_confidence) if not found — means no decay bonus.
                if self._open_positions:
                    _mids = [pk.split(":", 1)[0] for pk in self._open_positions]
                    _conf_rows = await session.execute(
                        _text(
                            "SELECT DISTINCT ON (market_id) market_id, confidence "
                            "FROM trade_events "
                            "WHERE bot_name = :bot AND event_type = 'ENTRY' "
                            "  AND market_id = ANY(:mids) "
                            "ORDER BY market_id, event_time DESC"
                        ),
                        {"bot": self.bot_name, "mids": _mids},
                    )
                    _conf_map = {cr.market_id: float(cr.confidence or 0.55) for cr in _conf_rows.fetchall()}
                    _enriched = 0
                    for pk, pos in self._open_positions.items():
                        mid = pk.split(":", 1)[0]
                        pos["entry_confidence"] = _conf_map.get(mid, 0.55)
                        if mid in _conf_map:
                            _enriched += 1
                    logger.info("mirror_entry_confidence_restored", enriched=_enriched, total=len(self._open_positions))

                # S144: Count stale-price positions (current_price == entry_price).
                _stale = sum(
                    1 for p in self._open_positions.values()
                    if abs(float(p.get("current_price", 0) or 0) - float(p.get("entry_price", 0) or 0)) < 1e-6
                )
                logger.info(
                    "MirrorBot startup: restored %d open positions from DB (stale_price=%d)",
                    restored, _stale,
                )
        except Exception as exc:
            logger.warning("MirrorBot _restore_state_on_startup failed: %s", exc)

        # S144: Immediately sync fresh prices from DB so stop-loss has accurate
        # current_price right after restart, not 45s later on first scan cycle.
        if self._open_positions:
            try:
                _stale_before = sum(
                    1 for p in self._open_positions.values()
                    if abs(float(p.get("current_price", 0) or 0) - float(p.get("entry_price", 0) or 0)) < 1e-6
                )
                await self._sync_prices_from_db()
                _stale_after = sum(
                    1 for p in self._open_positions.values()
                    if abs(float(p.get("current_price", 0) or 0) - float(p.get("entry_price", 0) or 0)) < 1e-6
                )
                logger.info("mirror_startup_price_sync stale_before=%d stale_after=%d",
                            _stale_before, _stale_after)
            except Exception as _ps_err:
                logger.warning("mirror_startup_price_sync failed: %s", _ps_err)

        # S117: Build _entered_market_sides from ALL trade_events ENTRY records.
        # Prevents opposing-side entries on markets where the first side already resolved.
        # The in-memory _open_positions guard only catches currently-open positions.
        try:
            async with db.get_session() as session:
                _ms_rows = await session.execute(
                    _text(
                        "SELECT DISTINCT market_id, side FROM trade_events "
                        "WHERE bot_name = :bot AND event_type = 'ENTRY' AND side IN ('YES', 'NO')"
                    ),
                    {"bot": self.bot_name},
                )
                for _mr in _ms_rows.fetchall():
                    self._entered_market_sides.add((_mr.market_id, _mr.side))
                logger.info("mirror_entered_sides_restored n=%d", len(self._entered_market_sides))
        except Exception as _exc:
            logger.warning("mirror_entered_sides_restore failed: %s", _exc)

        # S90: Clean opposing YES/NO pairs — mark smaller side for exit
        try:
            _markets_seen: dict = {}
            for pk in list(self._open_positions.keys()):
                mid = pk.split(":")[0]
                _markets_seen.setdefault(mid, []).append(pk)

            _pairs_cleaned = 0
            for mid, pkeys in _markets_seen.items():
                if len(pkeys) < 2:
                    continue
                sides: dict = {}
                for pk in pkeys:
                    s = str(self._open_positions[pk].get("side", "")).upper()
                    sides.setdefault(s, []).append(pk)
                if "YES" in sides and "NO" in sides:
                    yes_total = sum(self._open_positions[k].get("size", 0) for k in sides["YES"])
                    no_total = sum(self._open_positions[k].get("size", 0) for k in sides["NO"])
                    to_exit = sides["NO"] if no_total <= yes_total else sides["YES"]
                    for pk in to_exit:
                        self._open_positions[pk]["traders"] = set()
                        _pairs_cleaned += 1
                    logger.warning(
                        "mirror_opposing_pair_marked_for_exit market=%s exit_side=%s exit_size=%.2f",
                        mid[:16],
                        "NO" if no_total <= yes_total else "YES",
                        min(yes_total, no_total),
                    )
            if _pairs_cleaned:
                logger.info("mirror_startup_opposing_pairs=%d marked for exit", _pairs_cleaned)
        except Exception as _e:
            logger.debug("mirror opposing pair cleanup failed: %s", _e)

        # S92: Pre-populate _token_side_cache + _market_meta_cache from DB on startup
        # Eliminates 10-500ms DB queries on first RTDS trade per market.
        try:
            if db and getattr(db, "session_factory", None):
                from sqlalchemy import text as _text
                import time as _time
                async with db.get_session() as session:
                    # Token side cache: bulk-load all markets with YES/NO tokens
                    tk_rows = await session.execute(
                        _text(
                            "SELECT condition_id, yes_token_id, no_token_id "
                            "FROM markets WHERE yes_token_id IS NOT NULL "
                            "AND no_token_id IS NOT NULL LIMIT 5000"
                        )
                    )
                    _tk_count = 0
                    for tk in tk_rows.fetchall():
                        cid = str(tk[0]) if tk[0] else None
                        yes_tid = str(tk[1])
                        no_tid = str(tk[2])
                        if cid:
                            self._token_side_cache[f"{cid}:{yes_tid}"] = "YES"
                            self._token_side_cache[f"{cid}:{no_tid}"] = "NO"
                            _tk_count += 1
                    logger.info("S92: pre-populated _token_side_cache with %d markets (%d entries)",
                                _tk_count, len(self._token_side_cache))

                    # Market meta cache: bulk-load categories for traded markets
                    _now_mono = _time.monotonic()
                    meta_rows = await session.execute(
                        _text(
                            "SELECT m.id, m.category, m.end_date_iso "
                            "FROM markets m "
                            "INNER JOIN traded_markets tm ON tm.market_id = CAST(m.id AS TEXT) "
                            "WHERE tm.status = 'open' OR tm.resolved = FALSE "
                            "LIMIT 2000"
                        )
                    )
                    _meta_count = 0
                    for mr in meta_rows.fetchall():
                        mid_str = str(mr[0])
                        cat = str(mr[1] or "")
                        ttr = ""
                        end_raw = mr[2]
                        if end_raw:
                            h = self.hours_until_resolution({"end_date_iso": end_raw})
                            if h is not None:
                                if h < 24:
                                    ttr = "hours"
                                elif h < 168:
                                    ttr = "days"
                                else:
                                    ttr = "weeks"
                        self._market_meta_cache[mid_str] = (cat, ttr, _now_mono + self._MARKET_META_TTL)
                        _meta_count += 1
                    logger.info("S92: pre-populated _market_meta_cache with %d markets", _meta_count)
        except Exception as _cache_err:
            logger.debug("S92: cache pre-population failed (non-critical): %s", _cache_err)

        # S117: Pre-load reliability cache for instant F1 on startup
        if self._reliability_tracker:
            try:
                # S150: Relaxed from 24h→72h. Category WR data moves slowly (resolutions
                # take hours/days). A 63h-old cache is far better than empty — empty cache
                # means cat_n=0 → confidence=0.50 → zero entries. Live refresh updates it.
                loaded = await self._reliability_tracker.load_from_cache(max_age_hours=72)
                if loaded:
                    logger.info("reliability_cache_loaded_on_startup")
            except Exception:
                pass

        # M5: Restore dedup dict from Redis
        await self._restore_dedup_from_redis()

    async def _reconcile_leader_positions(self) -> None:
        """M4: Check if tracked leaders still hold positions we're mirroring.

        On restart, leaders may have exited while the bot was down.
        Positions where ALL tracked leaders have exited are flagged as orphans
        and queued for exit on the next scan cycle.
        """
        if self._recon_done or not self._open_positions:
            self._recon_done = True
            return
        self._recon_done = True

        _orphans: List[str] = []
        _checked = 0
        _rate_limit = 0

        try:
            async with self.base_engine.client:
                for pos_key, pos in list(self._open_positions.items()):
                    traders = pos.get("traders", set())
                    if not traders:
                        continue

                    market_id = pos_key.split(":")[0]
                    _all_exited = True

                    for addr in list(traders)[:3]:  # Cap at 3 leaders per position
                        if _rate_limit >= 50:  # Max 50 API calls per reconciliation
                            _all_exited = False  # Can't confirm — keep position
                            break
                        try:
                            activity = await self.base_engine.client.get_user_activity(
                                user_address=addr, limit=20, offset=0,
                            )
                            _rate_limit += 1
                            _checked += 1

                            # Check if leader still holds this market
                            _still_holds = False
                            for trade in (activity or []):
                                if trade.get("marketId") == market_id:
                                    if str(trade.get("side", "")).upper() != "SELL":
                                        _still_holds = True
                                    break

                            if _still_holds:
                                _all_exited = False
                                break
                        except Exception:
                            _all_exited = False  # Can't verify — keep position
                            break

                    if _all_exited and traders:
                        _orphans.append(pos_key)

        except Exception as e:
            logger.warning("mirror_leader_recon failed: %s", e)

        if _orphans:
            logger.info("mirror_leader_recon_orphans", orphans=len(_orphans),
                        checked=_checked, positions=[k[:20] for k in _orphans[:5]])
            # Queue orphans for exit — they'll be closed in the next _check_and_execute_exits call
            for pos_key in _orphans:
                if pos_key in self._open_positions:
                    self._open_positions[pos_key]["traders"] = set()  # Clear traders → triggers exit
        elif _checked > 0:
            logger.info("mirror_leader_recon_clean", checked=_checked)

    # ── Main Scan Loop ──────────────────────────────────────────────

    async def scan_and_trade(self):
        """Main scan: refresh elites, check exits, collect consensus trades, execute."""
        self._scan_start_mono = _time.monotonic()  # S115: for shadow fill latency tracking
        self._scan_count += 1
        self._cap_logged_this_scan = False

        # Restore _daily_exposure + _open_positions from DB on first scan after restart.
        await self._restore_state_on_startup()

        # Session 82: Fit calibration stack on first scan; Session 83: re-fit daily
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._calibration_fit_date and self._calibration_fit_date != _today:
            self._calibration_fitted = False  # Reset for daily re-fit
        if self._calibration_stack and not self._calibration_fitted:
            try:
                _cal_results = await self._calibration_stack.fit()
                self._calibration_fitted = True
                self._calibration_fit_date = _today
                if _cal_results:
                    logger.info("MirrorBot calibration stack fitted", results=_cal_results)
            except Exception as e:
                logger.debug("MirrorBot calibration fit failed: %s", e)

        # S124: Load ML selector models on first scan
        if self._ml_selector and not self._ml_selector_loaded:
            try:
                _ml_status = self._ml_selector.load_all()
                self._ml_selector_loaded = True
                logger.info("mirror_ml_selector_loaded", **_ml_status)
            except Exception as e:
                logger.debug("mirror_ml_selector load failed: %s", e)

        # Session 82: Refresh adaptive safety metrics periodically
        if self._adaptive_safety:
            try:
                await self._adaptive_safety.refresh(self._scan_count)
            except Exception as e:
                logger.debug("MirrorBot adaptive safety refresh failed: %s", e)

        # M4/B4: Leader reconciliation — run periodically in background to avoid
        # blocking the scan loop for 30s while Gamma API calls complete.
        # BUG-12 fix: run every 100 scans (~75 min) instead of once on scan 3.
        # RACE-1 fix: store task ref, check for errors, only mark done on success.
        _recon_interval = 100
        _should_recon = (self._scan_count == 3 and not self._recon_done) or (
            self._recon_done and self._scan_count % _recon_interval == 0)
        if _should_recon and not getattr(self, '_recon_task_pending', False):
            self._recon_task_pending = True

            async def _bg_recon():
                try:
                    await asyncio.wait_for(self._reconcile_leader_positions(), timeout=60.0)
                    self._recon_done = True
                except asyncio.TimeoutError:
                    logger.warning("mirror_leader_recon timed out (60s, background)")
                except Exception as e:
                    logger.warning("mirror_leader_recon error: %s", e, exc_info=True)
                finally:
                    self._recon_task_pending = False

            asyncio.create_task(_bg_recon())

        # M5: Periodic dedup flush to Redis (every 100 scans ~75 min)
        if self._scan_count % 100 == 0:
            try:
                await self._save_dedup_to_redis()
            except Exception:
                pass

        # Refresh elites on first scan or periodically
        # P3-2: Wrap with 10s timeout — elite refresh DB query can block scan 30s+ under pool pressure
        if not self.elite_traders or self._scan_count % self._elite_refresh_every_n_scans == 0:
            try:
                await asyncio.wait_for(self._update_elite_traders(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("MirrorBot elite refresh timed out (10s) — continuing with stale list")
            except Exception as _elite_err:
                logger.debug("MirrorBot elite refresh failed: %s", _elite_err)
            # S115: Refresh reliability tracker independently — elite timeout must not kill F1
            if self._reliability_tracker:
                try:
                    # S150: Increased from 30s→120s. The get_user_resolution_counts_by_category()
                    # query is expensive (full trades scan + 2 JOINs + GROUP BY). 30s was too tight
                    # and caused reliability cache to stay empty → cat_n=0 → confidence=0.50 → zero entries.
                    await asyncio.wait_for(self._reliability_tracker.refresh(), timeout=120.0)
                except asyncio.TimeoutError:
                    logger.warning("MirrorBot reliability refresh timed out (120s)")
                except Exception as _rel_err:
                    logger.warning("Elite reliability refresh failed: %s", _rel_err)

        # Start WebSocket watchlist on first scan (register handler once)
        if self._watchlist and not self._watchlist_started:
            try:
                await self._watchlist.refresh_watchlist()
                ws_mgr = getattr(self.base_engine, "ws_manager", None)
                if ws_mgr:
                    ws_mgr.register_handler("last_trade_price", self._watchlist.on_trade_event)
                    ws_mgr.register_handler("trade", self._watchlist.on_trade_event)
                self._watchlist_started = True
                logger.info("MirrorBot: WebSocket watchlist started")
            except Exception as e:
                logger.warning("MirrorBot: watchlist start failed: %s", e)

        # Start RTDS global trade feed (all trades on platform, not per-market)
        if self._watchlist and self._watchlist_started and not self._rtds_started:
            try:
                from base_engine.data.rtds_websocket import RTDSWebSocket
                _rtds_url = getattr(settings, "RTDS_WS_URL", "wss://ws-live-data.polymarket.com")
                _rtds_ping = int(getattr(settings, "RTDS_PING_INTERVAL", 5))
                # S137 C15: Pass recv_timeout from settings (default 25s, was hardcoded 120s)
                _rtds_recv_timeout = int(getattr(settings, "RTDS_RECV_TIMEOUT", 25))
                self._rtds_ws = RTDSWebSocket(
                    handler=self._watchlist.on_rtds_trade,
                    ws_url=_rtds_url,
                    ping_interval=_rtds_ping,
                    recv_timeout=_rtds_recv_timeout,
                )
                await self._rtds_ws.connect()
                self._rtds_started = True
                logger.info("MirrorBot: RTDS global trade feed connected")
            except Exception as e:
                logger.warning("MirrorBot: RTDS connect failed: %s", e)

        # Daily watchlist refresh (once per UTC day)
        if self._watchlist and self._watchlist_started and self._watchlist.needs_refresh():
            try:
                await self._watchlist.refresh_watchlist()
            except Exception as e:
                logger.debug("MirrorBot: watchlist refresh failed: %s", e)

        # Log watchlist + RTDS stats every 10 scans (independent of refresh)
        if self._watchlist and self._watchlist_started and self._scan_count % 10 == 0:
            _ws = self._watchlist.get_stats()
            _rtds_info = {}
            if self._rtds_ws:
                _rtds_info = {
                    "rtds_events_total": self._rtds_ws._events_total,
                    "rtds_dispatched": self._rtds_ws._events_dispatched,
                }
            logger.info(
                "MirrorBot watchlist stats",
                watchlist_size=_ws["watchlist_size"],
                events_received=_ws["events_received"],
                events_matched=_ws["events_matched"],
                copies_attempted=_ws["copies_attempted"],
                copies_executed=_ws["copies_executed"],
                copies_yes=_ws.get("copies_yes", 0),
                copies_no=_ws.get("copies_no", 0),
                copies_sell=_ws.get("copies_sell", 0),
                **_rtds_info,
            )

        # Reset daily exposure at UTC day boundary
        self._check_daily_reset()

        # S85: Reap positions on resolved markets (every 20 scans)
        # S135: Also reconcile exited positions that are still status='open' in DB
        if self._scan_count % 20 == 1:
            await self._reap_resolved_positions()
            await self._reconcile_exited_positions()

        # Check for exits from tracked positions
        if self._open_positions and getattr(settings, "MIRROR_EXIT_ENABLED", True):
            await self._check_and_execute_exits()

        # Prune deduplication set if oversized
        self._prune_mirrored_trades()

        # S96: Consensus scan-path removed — RTDS handles all entries in real-time.
        # Scan loop is now: stop-loss exits + periodic housekeeping only.
        _rtds_dispatched = getattr(self._rtds_ws, "_events_dispatched", 0) if self._rtds_ws else 0
        logger.info(
            "MirrorBot scan: elites=%d open_positions=%d rtds_dispatched=%d",
            len(self.elite_traders), len(self._open_positions), _rtds_dispatched,
        )

        # S99b: Stale dispatch detection — reconnect if RTDS feed silently hangs
        if self._rtds_ws and self._rtds_started:
            if _rtds_dispatched == self._prev_rtds_dispatched:
                self._rtds_stale_count += 1
            else:
                self._rtds_stale_count = 0
            self._prev_rtds_dispatched = _rtds_dispatched
            # S137 C15: Watchdog threshold 120 → 60s. With recv_timeout=25s, 60s
            # means the reconnect loop itself is stuck — indicates deeper failure.
            if self._rtds_stale_count >= 4 and self._rtds_ws.last_recv_age > 60:
                logger.warning(
                    "rtds_stale_dispatch: %d scans unchanged, last_recv %.0fs ago — reconnecting",
                    self._rtds_stale_count, self._rtds_ws.last_recv_age,
                )
                try:
                    await self._rtds_ws.disconnect()
                    await self._rtds_ws.connect()
                    self._rtds_stale_count = 0
                    logger.info("rtds_stale_reconnected")
                except Exception as e:
                    logger.warning("rtds_stale_reconnect_failed: %s", e)

    # ── Market metadata cache (category + time-to-resolution) ──────

    async def _get_market_meta(self, market_id: str) -> Tuple[str, str]:
        """Cached lookup of market category and time-to-resolution string."""
        import time as _time
        now = _time.monotonic()
        cached = self._market_meta_cache.get(market_id)
        if cached:
            cat, ttr, expiry = cached
            if now < expiry:
                return cat, ttr
        # Fetch from DB, fall back to CLOB API for markets outside top-500
        category, time_to_res = "", ""
        try:
            db = getattr(self.base_engine, "db", None)
            if db and getattr(db, "session_factory", None):
                from sqlalchemy import text as _text
                async with db.get_session() as session:
                    row = await session.execute(
                        _text("SELECT category, end_date_iso FROM markets WHERE condition_id = :mid OR id::text = :mid LIMIT 1"),
                        {"mid": market_id},
                    )
                    r = row.fetchone()
                    if r:
                        category = str(r[0] or "")
                        end_raw = r[1]
                        if end_raw:
                            h = self.hours_until_resolution({"end_date_iso": end_raw})
                            if h is not None:
                                if h < 24:
                                    time_to_res = "hours"
                                elif h < 168:
                                    time_to_res = "days"
                                else:
                                    time_to_res = "weeks"
            # S112: CLOB API fallback — whales trade markets outside top-500 ingestion
            if not category:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=5.0) as _hc:
                        resp = await _hc.get(f"https://clob.polymarket.com/markets/{market_id}")
                        if resp.status_code == 200:
                            clob = resp.json()
                            q = clob.get("question") or ""
                            if q:
                                from base_engine.data.data_ingestion import _infer_category
                                category = _infer_category(q)
                                logger.info("mirror_clob_category_resolve",
                                            market=market_id[:16], category=category,
                                            question=q[:60])
                                # S113: Persist to market_categories for tracker F1
                                _tokens = clob.get("tokens") or []
                                _yes_tid = ""
                                _no_tid = ""
                                _resolved = bool(clob.get("closed"))
                                _resolution = None
                                for _ti, _tok in enumerate(_tokens):
                                    if _tok.get("outcome", "").upper() == "YES":
                                        _yes_tid = _tok.get("token_id", "")
                                    elif _tok.get("outcome", "").upper() == "NO":
                                        _no_tid = _tok.get("token_id", "")
                                    if _tok.get("winner"):
                                        _resolution = "YES" if _ti == 0 else "NO"
                                asyncio.create_task(self._persist_market_category(
                                    market_id, category, q,
                                    _yes_tid, _no_tid, _resolved, _resolution))
                except Exception as _clob_err:
                    logger.debug("CLOB category fallback failed for %s: %s",
                                 market_id[:16], _clob_err)
        except Exception as e:
            logger.debug("Market meta lookup failed for %s: %s", market_id, e)
        self._market_meta_cache[market_id] = (category, time_to_res, now + self._MARKET_META_TTL)
        return category, time_to_res

    async def _persist_market_category(
        self, condition_id: str, category: str, question: str,
        yes_token_id: str, no_token_id: str,
        resolved: bool, resolution: Optional[str],
    ) -> None:
        """S113: Persist CLOB-resolved category to market_categories table.

        Fire-and-forget from _get_market_meta() CLOB fallback.
        Populates data needed by elite_reliability tracker for F1.
        """
        db = getattr(self.base_engine, "db", None)
        if db is None:
            return
        try:
            await db.upsert_market_category(
                condition_id=condition_id,
                category=category,
                question=question,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                resolved=resolved,
                resolution=resolution,
            )
        except Exception as exc:
            logger.debug("_persist_market_category failed: %s", exc)

    # S98: _collect_and_aggregate_elite_trades + _parse_and_validate_trade deleted
    # RTDS is sole entry path — consensus scan no longer used

    # ── Exit Monitoring ─────────────────────────────────────────────

    async def _sync_prices_from_db(self):
        """B2: Sync current_price and size from positions table into _open_positions.

        position_manager updates positions.current_price every 10s from market data.
        Without this sync, stop-loss uses stale entry prices and never fires.
        Also syncs size from DB so stop-loss uses current values, not stale entry prices.
        """
        if not self._open_positions or not self.base_engine or not self.base_engine.db:
            return
        try:
            from sqlalchemy import text
            async with self.base_engine.db.get_session() as session:
                rows = await session.execute(text(
                    "SELECT market_id, token_id, current_price, size "
                    "FROM positions "
                    "WHERE COALESCE(source_bot, bot_id) = 'MirrorBot' "
                    "  AND status = 'open'"
                ))
                for r in rows.fetchall():
                    pos_key = f"{r.market_id}:{r.token_id}"
                    if pos_key in self._open_positions:
                        if r.current_price is not None:
                            self._open_positions[pos_key]["current_price"] = float(r.current_price)
                        if r.size is not None and float(r.size) > 0:
                            self._open_positions[pos_key]["size"] = float(r.size)
        except Exception as e:
            logger.debug("mirror_sync_prices_from_db failed: %s", e)

    async def _check_and_execute_exits(self):
        """Mirror exits when tracked traders close their positions."""
        if not self._open_positions:
            return

        # B2: Sync DB prices into in-memory dict so stop-loss sees real prices
        await self._sync_prices_from_db()

        # S141: Overlay RTDS live prices for positions where DB price is stale.
        # position_manager only updates markets in the initial WebSocket subscription (~500).
        # RTDS sees ALL global trades, so we get real-time prices for any active market.
        if self._watchlist:
            _rtds_updated = 0
            for _pk, _pdata in self._open_positions.items():
                _tok = _pk.split(":", 1)[1] if ":" in _pk else ""
                if not _tok:
                    continue
                _rtds_p = self._watchlist.get_rtds_price(_tok, max_age_s=300.0)
                if _rtds_p is not None:
                    _old_cp = float(_pdata.get("current_price", 0) or 0)
                    _ep = float(_pdata.get("entry_price", 0) or 0)
                    # Only override if DB price is stale (stuck at entry_price).
                    # If position_manager already updated current_price, trust it.
                    if abs(_old_cp - _ep) < 1e-6:
                        _pdata["current_price"] = _rtds_p
                        _rtds_updated += 1
            if _rtds_updated:
                logger.info("mirror_rtds_price_overlay", updated=_rtds_updated,
                            total=len(self._open_positions))

        positions_to_close: List[tuple] = []  # (pos_key, exit_event_data)

        # S99: Stop-loss (with graduated tightening) + take-profit + circuit breaker
        # S137 C10: Graduation REVERSED — tight early (kill losers fast), loose late (near-res noise).
        # Old defaults: -15% (0-48h), -10% (48-72h), -5% (72h+) — backwards.
        # New defaults: -10% (0-48h), -12% (48-72h), -15% (72h+) + near-resolution -5%.
        _base_stop_pct = float(getattr(settings, "MIRROR_STOP_LOSS_PCT", 0.15))  # 72h+ stop
        _tp_pct = float(getattr(settings, "MIRROR_TAKE_PROFIT_PCT", 0.25))
        _stop_24h = float(getattr(settings, "MIRROR_STOP_LOSS_TIGHTEN_24H", -0.06))  # S146: 0-24h tightest
        _stop_48h = float(getattr(settings, "MIRROR_STOP_LOSS_TIGHTEN_48H", -0.12))  # 24-48h tight
        _stop_72h = float(getattr(settings, "MIRROR_STOP_LOSS_TIGHTEN_72H", -0.15))  # 48-72h medium
        _near_res_hours = float(getattr(settings, "MIRROR_STOP_LOSS_NEAR_RES_HOURS", 24.0))
        _near_res_stop = abs(float(getattr(settings, "MIRROR_STOP_LOSS_NEAR_RES_PCT", -0.05)))
        # S137 C11: Resolution-relative max-hold — exit when held > MIRROR_MAX_HOLD_FRACTION of total
        # market duration. Replaces fixed 96h which is wrong for 7-day and 30-day markets alike.
        _max_hold_frac = float(getattr(settings, "MIRROR_MAX_HOLD_FRACTION", 0.80))
        _force_exit_hours = float(getattr(settings, "MIRROR_FORCE_EXIT_HOURS", 96))  # fallback if no TTR
        _now_utc = datetime.now(timezone.utc)
        _total_unrealized = 0.0

        for _pos_key, _pos in list(self._open_positions.items()):
            _entry = float(_pos.get("entry_price", 0.5) or 0.5)
            _current = float(_pos.get("current_price", _entry) or _entry)
            # Prices are token-specific — (current - entry) is correct for BOTH YES and NO
            _pnl_pct = (_current - _entry) / max(_entry, 1e-6)
            _size = float(_pos.get("size", 0) or 0)
            _total_unrealized += (_current - _entry) * _size

            # S99: Take-profit — capture the move, free capital
            if _pnl_pct >= _tp_pct:
                logger.info("mirror_take_profit", market=_pos_key, pnl_pct=f"{_pnl_pct:.2%}")
                positions_to_close.append((_pos_key, {
                    "exit_reason": "take_profit",
                    "pnl_pct": round(_pnl_pct, 4),
                }))
                continue

            # S99: Graduated exit pressure — tighten stop-loss by hold duration
            _ts_str = _pos.get("timestamp")
            _hours_held = 0.0
            if _ts_str:
                try:
                    _opened = datetime.fromisoformat(_ts_str)
                    if _opened.tzinfo is None:
                        _opened = _opened.replace(tzinfo=timezone.utc)
                    _hours_held = (_now_utc - _opened).total_seconds() / 3600.0
                except (ValueError, TypeError):
                    pass

            # S137 C11: Compute TTR from market index (live data, not stale meta string).
            # _market_meta_cache[1] = "hours"/"days"/"weeks" string — not usable for comparison.
            _pos_market_id = _pos_key.split(":", 1)[0]
            _pos_md = self.base_engine.get_market_from_index(_pos_market_id)
            _ttr_hours: Optional[float] = None
            if _pos_md:
                _pos_end = _pos_md.get("end_date_iso")
                if _pos_end:
                    _ttr_hours = self.hours_until_resolution({"end_date_iso": _pos_end})

            # S140: Absolute force-exit — always fires regardless of TTR.
            # S137 had this as elif (only when TTR=None), so long-dated markets
            # with hold_frac < 0.80 sat forever, blocking 480 opposing-side entries.
            if _hours_held >= _force_exit_hours:
                logger.info("mirror_force_exit", market=_pos_key, hours=round(_hours_held, 1),
                            pnl_pct=f"{_pnl_pct:.2%}")
                positions_to_close.append((_pos_key, {
                    "exit_reason": "force_exit",
                    "pnl_pct": round(_pnl_pct, 4),
                    "hours_held": round(_hours_held, 1),
                }))
                continue

            # S137 C11: Resolution-relative max-hold — if we've held >80% of total duration, exit.
            # Catches medium-dated markets BEFORE the absolute cutoff.
            if _ttr_hours is not None and _hours_held > 0:
                _total_duration = _hours_held + _ttr_hours
                _hold_frac = _hours_held / max(_total_duration, 1.0)
                if _hold_frac >= _max_hold_frac:
                    logger.info("mirror_max_hold_fraction_exit", market=_pos_key,
                                hold_frac=round(_hold_frac, 3), hours_held=round(_hours_held, 1),
                                ttr_hours=round(_ttr_hours, 1), pnl_pct=f"{_pnl_pct:.2%}")
                    positions_to_close.append((_pos_key, {
                        "exit_reason": "max_hold_fraction",
                        "pnl_pct": round(_pnl_pct, 4),
                        "hours_held": round(_hours_held, 1),
                        "hold_frac": round(_hold_frac, 3),
                    }))
                    continue

            # S137 C10 / S146: Graduated stop-loss — 4-tier, tight early and loose late.
            # S146: Added 24h tier. Tightened all thresholds for 40% WR regime.
            # Near-resolution override: < 24h left → -3% to avoid being stuck at resolution.
            if _ttr_hours is not None and _ttr_hours < _near_res_hours:
                _effective_stop = _near_res_stop  # near-resolution override
            elif _hours_held >= 72:
                _effective_stop = _base_stop_pct  # 72h+ (market noise dominates)
            elif _hours_held >= 48:
                _effective_stop = abs(_stop_72h)   # 48-72h medium
            elif _hours_held >= 24:
                _effective_stop = abs(_stop_48h)   # 24-48h tight
            else:
                _effective_stop = abs(_stop_24h)   # S146: 0-24h tightest (kill losers fast)

            # S150: Edge decay — original confidence decays -0.02 per day held.
            # If decayed confidence drops below 0.50 (breakeven territory),
            # halve the stop-loss threshold for tighter exits on stale positions.
            _entry_conf = float(_pos.get("entry_confidence", 0.55) or 0.55)
            _days_held = _hours_held / 24.0
            _decayed_conf = _entry_conf - 0.02 * _days_held
            if _decayed_conf < 0.50:
                _effective_stop *= 0.50
                logger.debug("mirror_edge_decay_tighten", market=_pos_key,
                             entry_conf=round(_entry_conf, 3),
                             decayed_conf=round(_decayed_conf, 3),
                             days_held=round(_days_held, 1),
                             tightened_stop=round(_effective_stop, 4))

            if _pnl_pct <= -_effective_stop:
                logger.info("MirrorBot autonomous stop-loss", market=_pos_key,
                            pnl_pct=f"{_pnl_pct:.2%}", hours_held=round(_hours_held, 1),
                            threshold=f"-{_effective_stop:.0%}")
                positions_to_close.append((_pos_key, {
                    "exit_reason": "stop_loss",
                    "pnl_pct": round(_pnl_pct, 4),
                    "hours_held": round(_hours_held, 1),
                    "threshold": round(_effective_stop, 4),
                }))

        # S99: Circuit breaker — if total unrealized P&L breaches threshold, pause entries
        _cb_threshold_pct = float(getattr(settings, "MIRROR_CIRCUIT_BREAKER_THRESHOLD", -0.20))
        _cb_pause_min = float(getattr(settings, "MIRROR_CIRCUIT_BREAKER_PAUSE_MINUTES", 15))
        # S119 FIX: fallback was $3k — should match MIRROR_TOTAL_CAPITAL ($20k)
        _fallback_capital = float(getattr(settings, "MIRROR_TOTAL_CAPITAL", 20000))
        _capital = float(getattr(self.bankroll, 'capital', _fallback_capital) or _fallback_capital) if self.bankroll else _fallback_capital
        _cb_threshold_usd = _capital * _cb_threshold_pct  # negative number
        if _total_unrealized <= _cb_threshold_usd and _total_unrealized < 0:
            self._circuit_breaker_until = _time.monotonic() + (_cb_pause_min * 60)
            logger.warning("mirror_circuit_breaker_tripped",
                           unrealized=round(_total_unrealized, 2),
                           threshold=round(_cb_threshold_usd, 2),
                           pause_minutes=_cb_pause_min)

        # Execute the exits
        # S147: Deduplicate by pos_key, keeping first exit reason (priority order preserved)
        _seen_keys: set = set()
        _deduped_exits: list = []
        for _pk, _ed in positions_to_close:
            if _pk not in _seen_keys:
                _seen_keys.add(_pk)
                _deduped_exits.append((_pk, _ed))
        for pos_key, _exit_event_data in _deduped_exits:
            pos = self._open_positions.get(pos_key)
            if not pos:
                continue

            # Exit by selling our position — SELL bypasses risk price bounds in order_gateway
            # (buying the opposite token was treated as a new entry, blocked at extreme prices).
            exit_side = "SELL"
            try:
                market_id, token_id = pos_key.split(":", 1)
                exit_price = self.validate_price(
                    pos.get("current_price", pos["entry_price"]),
                    market_id,
                )
                if exit_price is None:
                    exit_price = pos["entry_price"]

                # B2b: Use DB size as fallback if in-memory size is zero
                exit_size = pos["size"]
                if exit_size <= 0:
                    try:
                        from sqlalchemy import text as _t
                        async with self.base_engine.db.get_session() as _s:
                            _r = await _s.execute(_t(
                                "SELECT size FROM positions "
                                "WHERE market_id = :mid AND token_id = :tid "
                                "  AND COALESCE(source_bot, bot_id) = 'MirrorBot' "
                                "  AND status = 'open'"
                            ), {"mid": market_id, "tid": token_id})
                            _row = _r.fetchone()
                            if _row and _row.size:
                                exit_size = float(_row.size)
                                logger.info("mirror_exit_size_from_db", market=market_id[:20], size=exit_size)
                    except Exception:
                        pass
                if exit_size <= 0:
                    logger.warning("mirror_exit_skip_zero_size", market=market_id[:20])
                    continue

                order = await self.place_order(
                    market_id=market_id,
                    token_id=token_id,
                    side=exit_side,
                    size=exit_size,
                    price=exit_price,
                    confidence=0.80,
                    event_data=_exit_event_data,
                )
                if order.get("success"):
                    logger.info(
                        "Mirror exit executed",
                        market=market_id,
                        exit_side=exit_side,
                        original_side=pos["side"],
                        size=f"{pos['size']:.2f}",
                    )
                    # BUG-13 fix: use actual exit_size; S133: use entry_price (matches increment)
                    _exit_cost = exit_size * pos.get("entry_price", exit_price)
                    self._daily_exposure = max(0.0, self._daily_exposure - _exit_cost)
                    # M1: Decrement category exposure on exit
                    _pos_cat = pos.get("category", "")
                    if _pos_cat:
                        self._category_exposure[_pos_cat] = max(
                            0.0, self._category_exposure.get(_pos_cat, 0.0) - _exit_cost
                        )
                    del self._open_positions[pos_key]
                    # S135: Mark position closed in DB so it doesn't reload on restart
                    # Use _sql alias (not _t) — _t is a local var in this function due to the
                    # conditional import at L909 (Python 3.13 scoping: local for entire function).
                    # S141: Retry position close to prevent ghost exits (D6).
                    # Without retry, pool exhaustion causes silent failure → position
                    # reloads on restart → duplicate EXIT events accumulate.
                    from sqlalchemy import text as _sql
                    for _close_attempt in range(3):
                        try:
                            async with self.base_engine.db.get_session() as _cs:
                                await _cs.execute(_sql(
                                    "UPDATE positions SET status = 'closed' "
                                    "WHERE market_id = :mid AND token_id = :tid "
                                    "  AND COALESCE(source_bot, bot_id) = 'MirrorBot' "
                                    "  AND status = 'open'"
                                ), {"mid": market_id, "tid": token_id})
                                await _cs.commit()
                            break
                        except Exception as _db_err:
                            if _close_attempt < 2:
                                await asyncio.sleep(0.5)
                            else:
                                logger.warning("mirror_exit_db_close_failed market=%s attempt=%d: %s",
                                               market_id[:20], _close_attempt + 1, _db_err)
            except Exception as e:
                logger.warning("Failed to execute mirror exit for %s: %s", pos_key, e)

    async def _reap_resolved_positions(self) -> None:
        """S85: Delete positions on markets that have already resolved.

        Without this, positions accumulate forever — resolved markets keep
        phantom positions in the DB and in-memory, inflating exposure and
        blocking new trades via the 200-position cap.
        """
        try:
            db = getattr(self.base_engine, "db", None)
            if not db or not db.session_factory:
                return
            from sqlalchemy import text as _text
            async with db.get_session() as session:
                result = await session.execute(_text(
                    "DELETE FROM positions "
                    "WHERE (bot_id = :bot OR source_bot = :bot) "
                    "  AND is_paper = true "
                    "  AND market_id IN ("
                    "    SELECT CAST(id AS TEXT) FROM markets WHERE resolution IN ('YES','NO')"
                    "  ) "
                    "RETURNING market_id, token_id"
                ), {"bot": self.bot_name})
                reaped = result.fetchall()
                await session.commit()
                if reaped:
                    _reaped_usd = 0.0
                    for row in reaped:
                        pos_key = f"{row[0]}:{row[1]}"
                        _pos = self._open_positions.pop(pos_key, None)
                        # S113 P7: Decrement daily exposure for resolved positions —
                        # without this, resolved positions inflate _daily_exposure
                        # and block new trades via the daily cap.
                        if _pos:
                            _pos_cost = _pos.get("size", 0.0) * _pos.get("entry_price", 0.0)
                            _reaped_usd += _pos_cost
                            # Also decrement category exposure
                            _pos_cat = _pos.get("category", "")
                            if _pos_cat:
                                self._category_exposure[_pos_cat] = max(
                                    0.0, self._category_exposure.get(_pos_cat, 0.0) - _pos_cost
                                )
                    if _reaped_usd > 0:
                        self._daily_exposure = max(0.0, self._daily_exposure - _reaped_usd)
                    logger.info("mirror_reap_resolved: removed %d stale positions, freed $%.2f daily exposure",
                                len(reaped), _reaped_usd)
        except Exception as exc:
            logger.warning("mirror_reap_resolved failed: %s", exc)

    async def _reconcile_exited_positions(self) -> None:
        """S135: Close DB positions that have EXIT trade_events but still status='open'.

        Without this, positions exited via stop-loss or trader-SELL before S135
        remain as zombies in the DB and reload on every restart.
        """
        try:
            db = getattr(self.base_engine, "db", None)
            if not db or not db.session_factory:
                return
            from sqlalchemy import text as _text
            async with db.get_session() as session:
                result = await session.execute(_text(
                    "UPDATE positions SET status = 'closed' "
                    "WHERE (bot_id = 'MirrorBot' OR source_bot = 'MirrorBot') "
                    "  AND status = 'open' "
                    "  AND market_id IN ("
                    "    SELECT te.market_id FROM trade_events te "
                    "    WHERE te.bot_name = 'MirrorBot' "
                    "      AND te.event_type = 'EXIT'"
                    "  ) "
                    "RETURNING market_id, token_id"
                ), {})
                closed = result.fetchall()
                await session.commit()
                if closed:
                    for row in closed:
                        pos_key = f"{row[0]}:{row[1]}"
                        _pos = self._open_positions.pop(pos_key, None)
                        if _pos:
                            _pos_cost = _pos.get("size", 0.0) * _pos.get("entry_price", 0.0)
                            self._daily_exposure = max(0.0, self._daily_exposure - _pos_cost)
                            _pos_cat = _pos.get("category", "")
                            if _pos_cat:
                                self._category_exposure[_pos_cat] = max(
                                    0.0, self._category_exposure.get(_pos_cat, 0.0) - _pos_cost
                                )
                    logger.info("mirror_reconcile_exited: closed %d zombie positions in DB", len(closed))
        except Exception as exc:
            logger.warning("mirror_reconcile_exited failed: %s", exc)

    # ── Position & Exposure Tracking ────────────────────────────────

    async def _persist_trader_to_position(self, trade_info: Dict) -> None:
        """Append trader_address to positions.trader_addresses for restart recovery."""
        db = getattr(self.base_engine, "db", None)
        if db is None:
            return
        from sqlalchemy import text as _text
        try:
            async with db.get_session() as session:
                await session.execute(
                    _text(
                        "UPDATE positions SET trader_addresses = "
                        "  array_append(COALESCE(trader_addresses, '{}'), :addr) "
                        "WHERE (bot_id = :bot OR source_bot = :bot) "
                        "  AND market_id = :mid AND token_id = :tid "
                        "  AND status = 'open'"
                    ),
                    {
                        "addr": trade_info["trader_address"],
                        "bot": self.bot_name,
                        "mid": trade_info["market_id"],
                        "tid": trade_info["token_id"],
                    },
                )
                await session.commit()
        except Exception as exc:
            logger.warning("MirrorBot: failed to persist trader address: %s", exc)

    def _can_open_position(self, price: float, category: str = "") -> bool:
        """Check concurrent position + daily exposure + category + price limits.

        Returns False with a specific INFO log identifying WHICH limit was hit.
        """
        # S99b: Post-reset cooldown — spread trades after midnight reset
        if _time.monotonic() < self._daily_reset_cooldown:
            return False

        # S99b: Hard reject at 5/95, gray zone 5-7 / 93-95 gets 0.25x sizing (Option C)
        _hard_min = float(getattr(settings, "MIRROR_HARD_MIN_PRICE", 0.05))
        _hard_max = float(getattr(settings, "MIRROR_HARD_MAX_PRICE", 0.95))
        if price < _hard_min or price > _hard_max:
            logger.debug("mirror_price_bounds: %.3f outside [%.2f, %.2f], skipping",
                         price, _hard_min, _hard_max)
            return False

        # S99: Circuit breaker — pause entries when portfolio is bleeding
        if _time.monotonic() < self._circuit_breaker_until:
            logger.info("mirror_circuit_breaker: paused until breaker expires")
            return False

        # Session 82: Adaptive safety overrides static max_positions when enabled.
        # Gate on MIRROR_ADAPTIVE_SAFETY + _fitted to avoid reading wrong settings in tests.
        if (self._adaptive_safety
                and getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False)
                and self._adaptive_safety._fitted):
            max_positions = self._adaptive_safety.get_adjusted_max_positions()
        else:
            max_positions = getattr(
                settings, "MIRROR_MAX_CONCURRENT_POSITIONS", self.MAX_CONCURRENT_POSITIONS
            )
        if len(self._open_positions) >= max_positions:
            if not getattr(self, '_cap_logged_this_scan', False):
                logger.info("Mirror POSITION CAP: %d/%d positions, skipping",
                            len(self._open_positions), max_positions)
                self._cap_logged_this_scan = True
            return False

        # Daily cap: read bankroll.max_daily_usd directly (avoids capital*0.15 mismatch).
        # Fallback: MIRROR_MAX_DAILY_EXPOSURE_PCT * TOTAL_CAPITAL for test/mock scenarios.
        if self.bankroll:
            _max_daily_usd = self.bankroll.max_daily_usd
        else:
            # DEPRECATED: MIRROR_MAX_DAILY_EXPOSURE_PCT — use BotBankrollManager config instead.
            if not self._deprecation_warned:
                logger.warning(
                    "MIRROR_MAX_DAILY_EXPOSURE_PCT is deprecated — "
                    "configure bankroll.max_daily_usd in BotBankrollManager instead"
                )
                self._deprecation_warned = True
            max_exposure_pct = getattr(settings, "MIRROR_MAX_DAILY_EXPOSURE_PCT", self.MAX_DAILY_EXPOSURE_PCT)
            _max_daily_usd = float(getattr(settings, "TOTAL_CAPITAL", 10000.0)) * max_exposure_pct
        if self._daily_exposure >= _max_daily_usd:
            logger.info("Mirror DAILY CAP: $%.0f/$%.0f exposure, skipping",
                        self._daily_exposure, _max_daily_usd)
            return False

        # M1: Per-category exposure cap — prevent concentration in one category
        if category:
            _cat_max_usd = float(getattr(settings, "MIRROR_MAX_CATEGORY_EXPOSURE_USD", 4000))
            _cat_current = self._category_exposure.get(category, 0.0)
            if _cat_current >= _cat_max_usd:
                logger.info("Mirror CATEGORY CAP: %s $%.0f/$%.0f, skipping",
                            category, _cat_current, _cat_max_usd)
                return False

        return True

    def _check_daily_reset(self):
        """Reset daily exposure counter at UTC day boundary."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            logger.info("Daily P&L reset", previous_pnl=round(self._daily_exposure, 2))
            self._daily_exposure = 0.0
            self._category_exposure.clear()
            self._whale_consensus.clear()  # S113 P2: reset consensus counter daily
            self._whale_consensus_ts.clear()  # S153: TTL version
            self._daily_reset_date = today
            # S99b: 60s cooldown to prevent burst of 30+ trades at midnight
            self._daily_reset_cooldown = _time.monotonic() + 60

    # ── Deduplication ───────────────────────────────────────────────

    def _prune_mirrored_trades(self):
        """Cap deduplication dict to prevent unbounded memory growth."""
        max_tracked = getattr(
            settings, "MIRROR_MAX_TRACKED_TRADES", self.MAX_TRACKED_TRADES
        )
        if len(self.mirrored_trades) > max_tracked:
            old_len = len(self.mirrored_trades)
            # Keep the newest half — OrderedDict preserves insertion order
            keep_count = old_len // 2
            drop_count = old_len - keep_count
            for _ in range(drop_count):
                self.mirrored_trades.popitem(last=False)  # remove oldest
            logger.debug(
                "Pruned mirrored_trades from %d to %d",
                old_len,
                len(self.mirrored_trades),
            )

    # ── M5: Dedup Redis Persistence ────────────────────────────────

    async def _save_dedup_to_redis(self) -> None:
        """Flush mirrored_trades keys to Redis for restart recovery."""
        cache = getattr(self.base_engine, "cache", None)
        if not cache or not getattr(cache, "redis", None):
            return
        try:
            keys = list(self.mirrored_trades.keys())[-5000:]  # Keep newest 5k
            await cache.set("mirrorbot:dedup", keys, ttl=86400)
            logger.info("mirror_dedup_saved", n_keys=len(keys))
        except Exception as e:
            logger.debug("mirror_dedup_save failed: %s", e)

    async def _restore_dedup_from_redis(self) -> None:
        """Restore mirrored_trades from Redis on startup."""
        cache = getattr(self.base_engine, "cache", None)
        if not cache or not getattr(cache, "redis", None):
            return
        try:
            keys = await cache.get("mirrorbot:dedup")
            if keys and isinstance(keys, list):
                for k in keys:
                    self.mirrored_trades[k] = None
                logger.info("mirror_dedup_restored", n_keys=len(keys))
        except Exception as e:
            logger.debug("mirror_dedup_restore failed: %s", e)

    # ── Elite Trader Management ─────────────────────────────────────

    async def _update_elite_traders(self):
        """Fetch elite traders from DB with API fallback."""
        try:
            if self.base_engine.db and self.base_engine.db.session_factory:
                self.elite_traders = await self.base_engine.db.get_elite_traders(
                    limit=settings.TOP_TRADER_COUNT
                )
            else:
                logger.warning(
                    "Database unavailable, using API fallback for elite traders"
                )
                async with self.base_engine.client:
                    top_users = await self.base_engine.client.get_top_users(
                        limit=settings.TOP_TRADER_COUNT
                    )
                    self.elite_traders = [
                        {"address": u.get("address")}
                        for u in top_users
                        if u.get("address")
                    ]
        except Exception as e:
            # M2: Keep stale list on error (clearing causes ~30min blackout until next refresh)
            logger.warning("Failed to update elite traders — retaining stale list", error=str(e))

        # S119: reliability refresh removed — scan loop at line 516 already refreshes
        # independently with 30s timeout (S115 separated elite + reliability refresh).

    # ── Opportunity Hook (unused — RTDS is sole entry path since S96) ────

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """MirrorBot uses consensus-based scan, not per-market analysis."""
        return None

    # ── Trade Execution ─────────────────────────────────────────────

    async def _execute_mirror_trade(
        self,
        market_id: str,
        token_id: str,
        side: str,
        price: float,
        confidence: float,
        trader_address: str,
        category: Optional[str] = None,
        source: str = "consensus",
        whale_trade_usd: float = 0.0,
    ) -> bool:
        """Execute a mirror trade with reliability weighting and exposure caps."""
        # ── S91: Tier 0 in-memory filters (<0.01ms) ─────────────────────
        # Short-circuit garbage trades before any DB/cache/API hit.
        _is_sell = str(side).upper() == "SELL"

        if not _is_sell:
            # S146: Hard price floor — reject absurd prices that slip through RTDS/WebSocket
            # gates. 0.001 entry at line 455 should be blocked but defense-in-depth here.
            # Also rejects near-resolved markets where token price has collapsed.
            if price < 0.03 or price > 0.97:
                logger.info("mirror_price_floor_blocked", price=round(price, 4),
                            market=str(market_id)[:16])
                return False

            # S132→S153: Whale trade size — weighted factor (was binary BLOCK < $5).
            # Larger trades carry more conviction; small trades penalized but not killed.
            # Ramp: $0→0.50, $12.5→0.75, $25+→1.0.
            pass  # _wf_whale computed below in split scoring block

            # S133→S153: Per-trader WR — weighted factor (was binary BLOCK < 35%).
            # 25%→0.0 (hard block), 35%→0.50, 45%+→1.0. Only activates after 20+ resolved.
            pass  # _wf_trader_wr computed below in split scoring block

            # Market blocklist — closed/expired/speed markets
            if market_id in self._market_blocklist:
                return False

            # Per-market cooldown — prevent re-entry on same signal
            _cooldown_secs = int(getattr(settings, "MIRROR_MARKET_COOLDOWN_SECONDS", 1800))
            if _cooldown_secs > 0:
                _cd_exp = self._market_cooldown.get(market_id, 0)
                if _time.monotonic() < _cd_exp:
                    return False

        # ── S137 C16: Tier 1 — pure-in-memory gates, NO DB/cache ──────
        # These run before category resolve (_get_market_meta DB/cache call).
        # High rejection rate (dup signals, opposing hedges, MM liquidity providers)
        # means most bad trades are rejected before any external I/O.

        # S137 C7: Market-maker detection — same trader both sides within 24h = MM.
        if not _is_sell and trader_address:
            _mm_window = 86400.0  # 24h in seconds
            _side_upper_mm = str(side).upper()
            _opposite_mm = "NO" if _side_upper_mm == "YES" else "YES"
            _opp_key = f"{trader_address}:{market_id}:{_opposite_mm}"
            _opp_ts = self._trader_market_sides.get(_opp_key, 0.0)
            if _opp_ts > 0 and (_time.monotonic() - _opp_ts) < _mm_window:
                logger.info("mirror_market_maker_blocked",
                            trader=trader_address[:10], market=str(market_id)[:16],
                            side=_side_upper_mm, prior_opposite=_opposite_mm)
                return False
            # Record this side — prune entries older than 25h to bound memory
            _now_mm = _time.monotonic()
            self._trader_market_sides[f"{trader_address}:{market_id}:{_side_upper_mm}"] = _now_mm
            # Prune: remove entries older than 25h (only on every ~1000 adds to amortize cost)
            if len(self._trader_market_sides) > 5000:
                _cutoff = _now_mm - 90000.0  # 25h
                self._trader_market_sides = {
                    k: v for k, v in self._trader_market_sides.items() if v > _cutoff
                }

        # Opposing-side dedup: reject BUY if we already hold OR ever entered the opposite side.
        # Different elite traders can take YES vs NO on the same market — opening both
        # creates a hedged position that bleeds fees with zero edge.
        # S117: Also checks _entered_market_sides (survives restarts via trade_events query).
        if not _is_sell:
            _side_upper = str(side).upper()
            _opposite = "NO" if _side_upper == "YES" else "YES"
            # Check 1: in-memory open positions (fast path)
            _market_prefix = f"{market_id}:"
            for _pk, _pv in self._open_positions.items():
                if _pk.startswith(_market_prefix) and str(_pv.get("side", "")).upper() == _opposite:
                    logger.info(
                        "mirror_opposing_side_blocked market=%s side=%s existing=%s",
                        str(market_id)[:16], side, _opposite,
                    )
                    return False
            # Check 2: historical entries (catches resolved positions missed after restart)
            if (market_id, _opposite) in self._entered_market_sides:
                logger.info(
                    "mirror_opposing_side_blocked_historical market=%s side=%s prior_entry=%s",
                    str(market_id)[:16], side, _opposite,
                )
                return False

        # S109 Same-side dedup: reject BUY if we already hold the SAME side on this market.
        # Multiple RTDS whale signals for same market should NOT create duplicate positions.
        # DATA: 455 markets had 2-9x duplicate entries, 716 excess ENTRY events.
        if not _is_sell:
            _side_upper = str(side).upper()
            _market_prefix = f"{market_id}:"
            for _pk, _pv in self._open_positions.items():
                if _pk.startswith(_market_prefix) and str(_pv.get("side", "")).upper() == _side_upper:
                    # S113→S153: Track multi-whale consensus with TTL
                    _cons_key = f"{market_id}:{_side_upper}"
                    _existing_cons = self._whale_consensus_ts.get(_cons_key)
                    if _existing_cons and (_time.monotonic() - _existing_cons[1]) < 1800:
                        self._whale_consensus_ts[_cons_key] = (_existing_cons[0] + 1, _time.monotonic())
                    else:
                        self._whale_consensus_ts[_cons_key] = (1, _time.monotonic())
                    # Legacy compat
                    self._whale_consensus[_cons_key] = self._whale_consensus_ts[_cons_key][0]
                    # Also record the whale in the position's traders set
                    self._open_positions[_pk]["traders"].add(trader_address)
                    logger.debug(
                        "mirror_same_side_blocked market=%s side=%s consensus=%d",
                        str(market_id)[:16], side,
                        self._whale_consensus[_cons_key],
                    )
                    return False

        # ── S137 C16: Tier 2 — category-dependent gates (DB/cache call) ──

        # Resolve category early (needed for M1 category cap + M3 domain tracking)
        if not category:
            try:
                _meta_cat, _ = await self._get_market_meta(str(market_id))
                category = _meta_cat or ""
            except Exception:
                category = ""

        # Category blocklist — skip bot-dominated speed markets (e.g., 15-min crypto)
        if not _is_sell and category:
            _cat_bl = getattr(settings, "MIRROR_CATEGORY_BLOCKLIST", "")
            if _cat_bl:
                _cat_lower = category.lower()
                for _bl in _cat_bl.lower().split(","):
                    _bl = _bl.strip()
                    if _bl and _bl in _cat_lower:
                        self._market_blocklist.add(market_id)  # cache for future fast-reject
                        logger.info("mirror_category_blocked", category=category,
                                    market_id=str(market_id)[:16])
                        return False

        # S137→S153: Category expertise — weighted factor (was binary BLOCK < 40%).
        # When cat_n < 10: factor=1.0 (no penalty — great overall WR + new to category is fine).
        # When cat_n >= 10: ramp 30%→0.2, 40%→0.6, 55%+→1.0.
        pass  # _wf_cat_expertise computed below in split scoring block

        # S85 FIX: Enforce position cap for ALL paths (consensus + RTDS).
        # Previously only consensus checked _can_open_position(); RTDS bypassed it,
        # allowing 686 positions past the 200 cap.
        if not _is_sell and not self._can_open_position(price, category=category):
            return False

        if _is_sell:
            pos_key = f"{market_id}:{token_id}"
            if pos_key not in self._open_positions:
                logger.debug(
                    "MirrorBot: skipping SELL (no position to close) market=%s",
                    str(market_id)[:16],
                )
                return False
            # Use ACTUAL position size — Kelly sizing gives fresh max-bet (wrong for exits)
            _pos = self._open_positions[pos_key]
            _exit_size = _pos.get("size", 0.0)
            if _exit_size <= 0:
                logger.info("MirrorBot: SELL position size=0, skipping market=%s", str(market_id)[:16])
                return False
            order = await self.place_order(
                market_id=market_id,
                token_id=token_id,
                side=side,
                size=_exit_size,
                price=price,
                confidence=confidence,
            )
            if order.get("success"):
                _exit_usd = _exit_size * price
                self._daily_exposure = max(0.0, self._daily_exposure - _exit_usd)
                # M1: Decrement category exposure on exit
                if category:
                    self._category_exposure[category] = max(
                        0.0, self._category_exposure.get(category, 0.0) - _exit_usd
                    )
                del self._open_positions[pos_key]
                # S135: Mark position closed in DB so it doesn't reload on restart
                try:
                    from sqlalchemy import text as _st
                    async with self.base_engine.db.get_session() as _cs:
                        await _cs.execute(_st(
                            "UPDATE positions SET status = 'closed' "
                            "WHERE market_id = :mid AND token_id = :tid "
                            "  AND COALESCE(source_bot, bot_id) = 'MirrorBot' "
                            "  AND status = 'open'"
                        ), {"mid": market_id, "tid": token_id})
                        await _cs.commit()
                except Exception as _db_err:
                    logger.warning("mirror_sell_db_close_failed market=%s: %s", str(market_id)[:16], _db_err)
                logger.info(
                    "MirrorBot: SELL exit executed market=%s size=%.2f",
                    str(market_id)[:16], _exit_size,
                )
            return bool(order.get("success"))

        # FIX: Use CURRENT market price, not the trader's historical fill price.
        # The trader may have traded hours ago at a different price. Entering at their
        # stale price produces fake P&L (buying at yesterday's prices, selling at today's).
        _market_data = self.base_engine.get_market_from_index(str(market_id))
        # S150: Fallback fetch for RTDS markets not yet in index.
        # Without market data, ttr_h=None (kills +0.02 TTR boost) and no spread/volume checks.
        # Only fires for trades that already passed the $25+ whale gate — low volume.
        if not _market_data:
            try:
                _fetched = await self.base_engine.get_market(str(market_id))
                if _fetched and isinstance(_fetched, dict):
                    self.base_engine.update_market_index([_fetched])
                    _market_data = _fetched
                    logger.debug("mirror_market_fallback_fetch", market=str(market_id)[:16])
            except Exception:
                pass  # proceed without — TTR stays None, confidence slightly lower
        _old_price = price  # S91: preserve trader's fill price for slippage check
        _spread = None  # S133: captured for event_data logging
        _vol_check = 0.0  # S153: default for no-market-data path
        _near_res_h = None  # S153: default for no-market-data path
        if _market_data:
            # S99: Reject inactive/closed markets — prevents 400s from CLOB
            if not _market_data.get("active", True):
                logger.info("mirror_market_inactive", market=str(market_id)[:16])
                self._market_blocklist.add(market_id)
                return False

            # S133→S153: Spread — weighted factor (was binary BLOCK > 0.08).
            # Ramp: 0→1.0, 0.08→0.68, 0.15→0.40, 0.25→0.0 (hard block at extreme).
            _yes_p = float(_market_data.get("yes_price", 0) or 0)
            _no_p = float(_market_data.get("no_price", 0) or 0)
            if _yes_p > 0 and _no_p > 0:
                _spread = _yes_p + _no_p - 1.0
            else:
                _spread = None
            # _wf_spread computed below in split scoring block

            # S137→S153: Volume — weighted factor (was binary BLOCK < $5K).
            # Ramp: $0→0.50, $7.5K→0.75, $15K+→1.0.
            _vol_24h = float(_market_data.get("volume_24h") or 0)
            _liq = float(_market_data.get("liquidity") or 0)
            _vol_check = _vol_24h if _vol_24h > 0 else _liq
            # _wf_volume computed below in split scoring block

            # S137→S153: NO heavy favorite — weighted factor (was binary BLOCK NO > 0.75).
            # Ramp: 0.60→1.0, 0.75→0.50, 0.90→0.0. YES side always 1.0.
            # _wf_no_fav computed below in split scoring block

            # S99→S153: Near-resolution — weighted factor (was binary BLOCK < 1h).
            # Ramp: 0h→0.30, 2.7h→0.53, 8h+→1.0.
            _end_date = _market_data.get("end_date_iso")
            _near_res_h = None
            if _end_date:
                _near_res_h = self.hours_until_resolution({"end_date_iso": _end_date})
            # _wf_near_res computed below in split scoring block

            _side_upper = str(side).upper()
            if _side_upper in ("YES", "NO"):
                _current = float(_market_data.get(f"{_side_upper.lower()}_price", 0) or 0)
                if 0.01 <= _current <= 0.99:
                    price = _current
                    if abs(_old_price - price) > 0.05:
                        logger.info("mirror_price_corrected", market=str(market_id)[:16],
                                    trader_price=round(_old_price, 4), market_price=round(price, 4))

        # R4→S153: Price direction — weighted factor (was binary BLOCK > 5%).
        # Ramp: 0%→1.0, 5%→0.67, 15%→0.0.
        _price_dir_pct = 0.0
        if _old_price > 0.01 and not _is_sell:
            _price_dir_pct = max(0.0, (price - _old_price) / _old_price)
        # _wf_price_dir computed below in split scoring block

        # S91→S153: Slippage — weighted factor (was binary BLOCK > 5%).
        # Ramp: 0%→1.0, 5%→0.67, 15%→0.0.
        _slip_pct = 0.0
        if _old_price > 0.01:
            _slip_pct = abs(price - _old_price) / _old_price
        # _wf_slippage computed below in split scoring block

        # Apply elite reliability multiplier
        reliability_mult = 1.0
        _eq_n = 0  # S142: init here so Baker-McHale block (post-sizing) is always bound
        if self._reliability_tracker:
            try:
                lr = self._reliability_tracker.likelihood_ratio(trader_address, side, category=category)
                # S152: LR gate DISABLED — Beta(6,10) prior demands 4 net wins before
                # LR≥1.0, blocking traders who are actually profitable. Review in next
                # handoff with post-S146 resolution data to pick a better prior.
                # Was: if lr < 1.0: return False
                # S132: Cap at 1.0 — data shows rel_mult>1.05 is anti-signal
                # (37.1% WR, -$113K). Only use reliability to PENALIZE, never amplify.
                reliability_mult = min(lr, 1.0)
                # R2: Sample-size ramp — don't trust high LR on tiny samples.
                # 0 trades → 0x, 25 trades → 0.5x, 50+ trades → 1.0x (no change).
                _eq_n = self._reliability_tracker.total_trade_count(trader_address)
                _sample_ramp = min(1.0, _eq_n / 50)
                # S154: Floor ramp during cold-start so gate-approved traders can trade
                # at reduced size. Without this, reliability_mult=0.0 zeroes all sizes
                # even though the gate validated the trader via efficiency prior.
                # Three layers of conservatism compound: gate threshold, kelly edge cap,
                # and this 15% floor. None is zero.
                _cs_floor = float(getattr(settings, "MIRROR_COLD_START_SIZE_FLOOR", 0.15))
                if _eq_n < 5 and _sample_ramp < _cs_floor:
                    _sample_ramp = _cs_floor
                reliability_mult *= _sample_ramp
            except Exception as e:
                logger.debug("elite reliability lookup failed: %s", e)

        # ── S110: Multi-factor confidence (replaces flat 0.55 base) ──────
        # Factor 1: Category-specific Bayesian base.
        # Uses per-whale per-category win rate with shrinkage toward 0.50.
        # Low sample count → confidence stays near 0.50 (replaces domain drift).
        if self._reliability_tracker:
            try:
                if category:
                    _cat_wr = self._reliability_tracker.mean(
                        trader_address, side, category=category)
                    _cat_n = self._reliability_tracker.category_trade_count(
                        trader_address, category)
                    _shrinkage = _cat_n / (_cat_n + 20)  # pseudocount=20
                    _base = 0.50 + _shrinkage * (_cat_wr - 0.50)
                    # Safety net: cap unfamiliar categories (double-conservative)
                    if _cat_n < 10:
                        _base = min(_base, 0.52)
                else:
                    # No category: use overall trader WR — better signal than flat 0.5.
                    # Data: category lookup fails for 61% of markets (long-tail / pre-ingestion).
                    # Flat 0.5 base means formula can never exceed gate=0.50; overall WR
                    # gives real signal from trader's resolved track record.
                    _cat_wr = self._reliability_tracker.overall_win_rate(trader_address)
                    _cat_n = self._reliability_tracker.total_trade_count(trader_address)
                    _shrinkage = min(1.0, _cat_n / 50)  # ramp: 0 trades→0x, 50+→1.0x
                    _base = 0.50 + _shrinkage * (_cat_wr - 0.50)
            except Exception:
                _base = 0.50
                _cat_wr = 0.50
                _cat_n = 0
        else:
            _base = 0.50
            _cat_wr = 0.50
            _cat_n = 0

        # Factor 2: Price-implied edge.
        # S132 DATA: Contrarian boost was anti-signal (32.9% WR, -$84K).
        # Neutral trades (46.6% WR) outperform both contrarian and consensus.
        # Zeroed out — confidence should not depend on price direction.
        _price_dev = abs(price - 0.50)
        _side_upper = str(side).upper()
        _is_contrarian = ((_side_upper == "YES" and price < 0.45)
                          or (_side_upper == "NO" and price < 0.45))
        _price_adj = 0.0

        # Factor 3: Trade size conviction — whale betting larger than usual = higher conviction.
        # Uses whale_trade_usd from RTDS payload vs whale's avg trade size from watchlist.
        _conv_adj = 0.0
        if whale_trade_usd > 0 and self._watchlist:
            _wdata = getattr(self._watchlist, "_watchlist_data", {})
            _wd = _wdata.get(trader_address.lower(), {})
            _whale_vol = _wd.get("vol", 0)
            _whale_n = _wd.get("num_trades", 0)
            if _whale_vol > 0 and _whale_n > 0:
                _avg_trade = _whale_vol / _whale_n
                _size_ratio = whale_trade_usd / max(_avg_trade, 1.0)
                if _size_ratio > 2.0:
                    _conv_adj = 0.04  # big position for this whale
                elif _size_ratio < 0.3:
                    _conv_adj = -0.03  # small/exploratory

        # Factor 4: S137 C12 — Time-to-resolution adjustment.
        # Optimal copy-trade window: 12–48h. Too short = late/dangerous; too long = noisy.
        # <12h (but >4h gate already passed): risky last-minute entry → -0.05
        # 12-48h: sweet spot → +0.02
        # 48-168h: neutral → 0.0
        # >168h (7d+): uncertain, lots of noise → -0.02
        _ttr_adj = 0.0
        _ttr_h = None
        if _market_data:
            _md_end = _market_data.get("end_date_iso")
            if _md_end:
                try:
                    _ttr_h = self.hours_until_resolution({"end_date_iso": _md_end})
                except Exception:
                    _ttr_h = None
        if _ttr_h is not None:
            if _ttr_h < 12:
                _ttr_adj = -0.05
            elif _ttr_h <= 48:
                _ttr_adj = 0.02
            elif _ttr_h > 168:
                _ttr_adj = -0.02

        # S146 Factor 5: Copy-track adjustment — trader's demonstrated copyability.
        # Proven copy-winners get a confidence boost; proven copy-losers get penalized.
        # Thin data (< threshold trades): neutral (no adjustment until we have signal).
        _copy_adj = 0.0
        _min_for_tier = int(getattr(settings, "MIRROR_COPY_MIN_TRADES_FOR_TIER", 20))
        if self._watchlist:
            _cp = self._watchlist.get_copy_perf(trader_address)
            if _cp and _cp["trades"] >= _min_for_tier:
                if _cp["copy_wr"] > 55.0:
                    _copy_adj = 0.03
                elif _cp["copy_wr"] < 40.0:
                    _copy_adj = -0.05

        # ── S153: Split scoring — gate score (trust metric) + kelly probability (sizing) ──
        # Old single-confidence path preserved behind MIRROR_USE_SPLIT_SCORING toggle.
        _raw_upstream = confidence

        # S154: Compute old confidence BEFORE try block — needed as fallback if split scoring fails.
        _old_confidence = max(0.35, min(0.75, _base + _price_adj + _conv_adj + _ttr_adj + _copy_adj))

        # S154: Fail-open — any exception in split scoring falls back to old confidence path.
        # Without this, a bug in any factor formula crashes the entire evaluate method.
        # Hard-blocks (return False) inside the try are NOT caught — return is not an exception.
        _split_scoring_ok = True
        try:
            # --- Compute all weighted factors (S153: was binary gates) ---
            # Whale trade size: $0→0.50, $12.5→0.75, $25+→1.0
            _wf_whale = min(1.0, 0.50 + 0.50 * (whale_trade_usd / 25.0)) if whale_trade_usd > 0 else 0.50

            # Trader WR: 25%→0.0, 35%→0.50, 45%+→1.0. Only when n>=20.
            _wf_trader_wr = 1.0
            if self._reliability_tracker:
                _bl_min_resolved = int(getattr(settings, "MIRROR_TRADER_MIN_RESOLVED", 20))
                _bl_total = self._reliability_tracker.total_trade_count(trader_address)
                if _bl_total >= _bl_min_resolved:
                    _bl_wr = self._reliability_tracker.overall_win_rate(trader_address)
                    _wf_trader_wr = max(0.0, min(1.0, (_bl_wr - 0.25) / 0.20))
                    # S154: Hard-block at WR<=25% with 20+ trades — actively destructive trader.
                    # Same pattern as _wf_spread and _wf_no_fav hard-blocks.
                    if _wf_trader_wr <= 0.0:
                        logger.info("mirror_trader_wr_hard_block", wr=round(_bl_wr, 3),
                                    n=_bl_total, trader=trader_address[:10],
                                    market=str(market_id)[:16])
                        return False

            # Category expertise: no penalty when cat_n<10 (new to category ≠ bad).
            # When cat_n>=10: 30%→0.2, 42.5%→0.7, 55%+→1.0.
            _wf_cat_expertise = 1.0
            if category and self._reliability_tracker:
                try:
                    _ce_count = int(self._reliability_tracker.category_trade_count(trader_address, category))
                except (TypeError, ValueError):
                    _ce_count = 0
                if _ce_count >= 10:
                    try:
                        _ce_wr = float(self._reliability_tracker.category_win_rate(trader_address, category))
                    except (TypeError, ValueError):
                        _ce_wr = 0.50
                    _wf_cat_expertise = max(0.20, min(1.0, (_ce_wr - 0.30) / 0.25))

            # Spread: 0→1.0, 0.125→0.50, 0.25→0.0. Hard block at extreme.
            _wf_spread = 1.0
            if _spread is not None and _spread > 0:
                _wf_spread = max(0.0, min(1.0, 1.0 - _spread / 0.25))
                if _wf_spread <= 0.0:
                    logger.info("mirror_spread_hard_block", spread=round(_spread, 3),
                                market=str(market_id)[:16])
                    return False

            # Volume: $0→0.50, $7.5K→0.75, $15K+→1.0
            _wf_volume = min(1.0, 0.50 + 0.50 * (_vol_check / 15000.0)) if _market_data else 1.0

            # NO heavy favorite: 0.60→1.0, 0.75→0.50, 0.90→0.0. YES=1.0.
            _wf_no_fav = 1.0
            if _side_upper == "NO" and _market_data:
                _nfp = float(_market_data.get("no_price", 0) or 0)
                if _nfp > 0.60:
                    _wf_no_fav = max(0.0, 1.0 - (_nfp - 0.60) / 0.30)
                    if _wf_no_fav <= 0.0:
                        logger.info("mirror_no_fav_hard_block", no_price=round(_nfp, 3),
                                    market=str(market_id)[:16])
                        return False

            # Near-resolution: 0h→0.30, 2.7h→0.53, 8h+→1.0
            _wf_near_res = 1.0
            if _near_res_h is not None:
                _wf_near_res = min(1.0, 0.30 + 0.70 * (_near_res_h / 8.0))

            # Price direction: 0%→1.0, 5%→0.67, 15%→0.0
            _wf_price_dir = max(0.0, 1.0 - _price_dir_pct / 0.15) if _price_dir_pct > 0 else 1.0

            # Slippage: 0%→1.0, 5%→0.67, 15%→0.0
            _wf_slippage = max(0.0, 1.0 - _slip_pct / 0.15) if _slip_pct > 0 else 1.0

            # --- Gate Score: weighted composite trust metric ---
            _efficiency = 0.0
            if self._watchlist:
                try:
                    _wd_gate = getattr(self._watchlist, "_watchlist_data", {})
                    if isinstance(_wd_gate, dict):
                        _efficiency = float(_wd_gate.get(trader_address.lower(), {}).get("efficiency", 0.0) or 0.0)
                except (TypeError, ValueError, AttributeError):
                    _efficiency = 0.0

            _decay_hl = float(getattr(settings, "MIRROR_GATE_DECAY_HALF_LIFE", 20))
            _decay_w = _math.exp(-0.693 * _eq_n / max(_decay_hl, 1))
            _cold_prior = float(getattr(settings, "MIRROR_GATE_COLD_START_PRIOR", 0.53))
            _eff_prior = min(0.65, 0.50 + _efficiency * 0.50) if _efficiency > 0 else _cold_prior
            _gate_base = _decay_w * _eff_prior + (1.0 - _decay_w) * _base

            # Additive adjustments (amplified for gate)
            _gate_base += _conv_adj * 1.5 + _ttr_adj * 1.2 + _copy_adj * 1.2

            # Consensus bonus with 30-min TTL
            _cons_key = f"{market_id}:{_side_upper}"
            _consensus_count = 1
            _cons_entry = self._whale_consensus_ts.get(_cons_key)
            if _cons_entry:
                _c_count, _c_time = _cons_entry
                if (_time.monotonic() - _c_time) < 1800:
                    _consensus_count = _c_count
                else:
                    del self._whale_consensus_ts[_cons_key]
                    self._whale_consensus.pop(_cons_key, None)  # S153: clean legacy too
            _gate_base += min(0.09, 0.03 * max(0, _consensus_count - 1))

            # Reliability applied to gate base — cold-start aware.
            # S154: During cold-start (n<5), efficiency prior IS the signal. The sample
            # ramp in reliability_mult zeros it out (0 trades → 0x), which defeats the
            # efficiency prior. Use a floor of decay_w (1.0 at n=0, decays as data grows)
            # so gate_base preserves the efficiency prior until data takes over.
            _gate_rel = max(reliability_mult, _decay_w) if _eq_n < 5 else min(reliability_mult, 1.0)
            _gate_base *= _gate_rel
            _gate_base = max(0.20, min(0.85, _gate_base))

            # Geometric mean of active factors, blended into gate_base
            _factors = [_wf_whale, _wf_trader_wr, _wf_cat_expertise, _wf_spread,
                        _wf_volume, _wf_no_fav, _wf_near_res, _wf_price_dir, _wf_slippage]
            _active_factors = [f for f in _factors if f < 0.99]
            if _active_factors:
                _geo = _math.prod(_active_factors) ** (1.0 / len(_active_factors))
            else:
                _geo = 1.0
            _factor_w = float(getattr(settings, "MIRROR_GATE_FACTOR_WEIGHT", 0.30))
            gate_score = _gate_base * ((1.0 - _factor_w) + _factor_w * _geo)

            # --- Kelly Probability: market-anchored, conservative ---
            _copy_tier = 2
            if self._watchlist:
                _copy_tier = self._watchlist.get_copy_tier(trader_address)
            if _eq_n >= 5:
                _trader_edge = max(0.0, _base - 0.50) * min(1.0, (_eq_n - 5) / 45.0)
            else:
                # S154: Cold-start — derive edge from same efficiency prior the gate trusts.
                # Flat 0.01 ignored leaderboard signal, producing dust sizes through BM.
                # _eff_prior=0.55 → 0.030, _eff_prior=0.65 → 0.090 (capped by MAX_KELLY_EDGE).
                # _eff_prior=0.53 (unknown) → 0.018 (tiny but non-zero — correct for unknowns).
                # Multiply by _decay_w so edge naturally fades as _eq_n→5 and warm path takes over.
                _eff_edge = max(0.0, _eff_prior - 0.50)
                _trader_edge = max(0.01, _eff_edge * _decay_w * 0.60)
            _kelly_copy_bonus = 0.02 if (_copy_tier == 1 and _copy_adj > 0) else 0.0
            _max_edge = float(getattr(settings, "MIRROR_MAX_KELLY_EDGE", 0.05))
            kelly_prob = min(price + _max_edge, price + _trader_edge + _kelly_copy_bonus)
            kelly_prob = max(price + 0.005, min(0.95, kelly_prob))

        except Exception:
            logger.exception("split_scoring_error, falling back to old confidence")
            _split_scoring_ok = False
            gate_score = _old_confidence
            kelly_prob = _old_confidence
            _wf_whale = _wf_trader_wr = _wf_cat_expertise = 1.0
            _wf_spread = _wf_volume = _wf_no_fav = 1.0
            _wf_near_res = _wf_price_dir = _wf_slippage = 1.0
            _geo = 1.0
            _decay_w = 1.0
            _eff_prior = 0.53
            _consensus_count = 1
            _factor_w = 0.30
            _gate_base = _old_confidence

        # --- Shadow/live toggle ---
        _split_live = getattr(settings, "MIRROR_USE_SPLIT_SCORING", False)
        if not isinstance(_split_live, bool):
            _split_live = False
        # S154: Force old path if split scoring threw an exception
        if not _split_scoring_ok:
            _split_live = False
        if _split_live:
            confidence = kelly_prob
        else:
            confidence = _old_confidence

        logger.info("mirror_multifactor", trader=trader_address[:10],
                    category=category or "", cat_wr=round(_cat_wr, 3),
                    cat_n=_cat_n, base=round(_base, 3),
                    price_adj=round(_price_adj, 3),
                    conv_adj=round(_conv_adj, 3),
                    ttr_adj=round(_ttr_adj, 3),
                    copy_adj=round(_copy_adj, 3),
                    ttr_h=round(_ttr_h, 1) if _ttr_h is not None else None,
                    whale_usd=round(whale_trade_usd, 0),
                    upstream=round(_raw_upstream, 3),
                    final=round(confidence, 3),
                    rel_mult=round(reliability_mult, 3))
        logger.info("mirror_split_scoring",
                    gate_score=round(gate_score, 3),
                    kelly_prob=round(kelly_prob, 3),
                    old_conf=round(_old_confidence, 3),
                    gate_base=round(_gate_base, 3),
                    eff_prior=round(_eff_prior, 3),
                    decay_w=round(_decay_w, 3),
                    geo_mean=round(_geo, 3),
                    factor_w=round(_factor_w, 3),
                    consensus=_consensus_count,
                    wf_whale=round(_wf_whale, 2),
                    wf_trader_wr=round(_wf_trader_wr, 2),
                    wf_cat=round(_wf_cat_expertise, 2),
                    wf_spread=round(_wf_spread, 2),
                    wf_volume=round(_wf_volume, 2),
                    wf_no_fav=round(_wf_no_fav, 2),
                    wf_near_res=round(_wf_near_res, 2),
                    wf_price_dir=round(_wf_price_dir, 2),
                    wf_slippage=round(_wf_slippage, 2),
                    split_live=_split_live,
                    trader=trader_address[:10],
                    market=str(market_id)[:16])

        # S124: ML selector — score trade with all three strategies (shadow + optional live gate)
        # Fail-open: any ML error must NOT block the trade (log + continue)
        _ml_scores = None
        if self._ml_selector and self._ml_selector.loaded:
            try:
                _ml_features = {
                    "conf_base": _base,
                    "conf_price_adj": _price_adj,
                    "conf_conv_adj": _conv_adj,
                    "rel_mult": reliability_mult,
                    "price": price,
                    "whale_trade_usd": whale_trade_usd,
                    "category_encoded": self._ml_selector.encode_category(category),
                    "consensus": _consensus_count,  # S153: from TTL-aware source
                    "hour_utc": float(datetime.now(timezone.utc).hour),
                    "side_is_no": 1.0 if _side_upper == "NO" else 0.0,
                    "price_extremity": abs(price - 0.50),
                    "conf_composite": confidence,
                }
                _ml_scores = self._ml_selector.score_trade(_ml_features)

                # Live gate: only block when MIRROR_USE_ML_SELECTOR=true
                if getattr(settings, "MIRROR_USE_ML_SELECTOR", False) and self._ml_selector.should_block(_ml_scores):
                    logger.info("mirror_ml_rejected",
                                strategy=getattr(settings, "MIRROR_ML_STRATEGY", "xgb"),
                                xgb=_ml_scores.get("ml_score_xgb"),
                                ql=_ml_scores.get("ml_score_ql"),
                                confidence=round(confidence, 3),
                                market=str(market_id)[:16])
                    return False
            except Exception as e:
                logger.warning("mirror_ml_selector_error", error=str(e), market=str(market_id)[:16])
                # Fail-open: _ml_scores stays None, trade proceeds without ML gate

        # PERF: MirrorBot is a pure trader-mirroring strategy — confidence comes from
        # elite trader consensus + reliability weighting, not from market signals.
        # Signal enhancements (Google Trends, WS orderflow) add 700-2000ms of network
        # latency per trade and are noise for this strategy. Skipped via settings flag.
        # Set MIRROR_SKIP_SIGNAL_ENHANCEMENTS=false to re-enable if needed.
        if not getattr(settings, "MIRROR_SKIP_SIGNAL_ENHANCEMENTS", True):
            try:
                _market_data = self.base_engine.get_market_from_index(str(market_id)) or {}
                confidence = await self.apply_signal_enhancements(
                    market_id, token_id, side, confidence, _market_data
                )
            except Exception as e:
                logger.debug("MirrorBot: signal enhancements failed (using raw confidence): %s", e)

        # Session 82: Apply calibration stack (FTS + Le2026 domain bias) to confidence.
        # Gated by MIRROR_USE_CALIBRATION=true. When off, confidence passes through unchanged.
        # S121: Always compute shadow calibrated score for dual-ledger comparison.
        _conf_cal_shadow = None
        if self._calibration_stack:
            # Calibrate confidence (domain + horizon aware)
            # S137 C14: Use actual computed _ttr_h (hours_until_resolution) / 24 instead of
            # rough bucket mapping {"hours": 0.5, "days": 3.0, "weeks": 21.0} which mapped
            # all "days"-TTR markets into the same 0-7d bucket regardless of actual TTR.
            _ttr_days = _ttr_h / 24.0 if _ttr_h is not None else None
            _cat = category or ""
            if not _cat and market_id:
                try:
                    _meta_cat, _ = await self._get_market_meta(str(market_id))
                    _cat = _meta_cat or ""
                except Exception:
                    pass
            # S121: Shadow ledger — always compute calibrated score (does not affect trade)
            _conf_cal_shadow = self._calibration_stack.shadow_calibrate(
                confidence, category=_cat, ttr_days=_ttr_days,
            )
            # Live calibration — only modifies confidence when MIRROR_USE_CALIBRATION=true
            _raw_conf = confidence
            confidence = self._calibration_stack.calibrate_confidence(
                confidence, category=_cat, ttr_days=_ttr_days,
            )
            if abs(_raw_conf - confidence) > 0.01:
                logger.info("mirror_calibrated", raw=round(_raw_conf, 3), cal=round(confidence, 3))


        # S103 Bug Fix: Enforce min_confidence AFTER all adjustments (domain drift,
        # calibration). Without this gate, self.min_confidence was dead code — trades
        # executed at 38% confidence despite configured threshold.
        # DATA: <40% = 9% WR (-$157/pos), 40-50% = 18% WR (-$53/pos), 50%+ = profitable.
        # S153: Split scoring gate check
        if _split_live:
            _gate_threshold = float(getattr(settings, "MIRROR_GATE_THRESHOLD", 0.52))
            if gate_score < _gate_threshold:
                logger.info("mirror_gate_blocked",
                            gate_score=round(gate_score, 3),
                            threshold=_gate_threshold,
                            kelly_prob=round(kelly_prob, 3),
                            trader=str(trader_address)[:16],
                            market=str(market_id)[:16])
                return False
        else:
            if confidence < self.min_confidence:
                # S148: Shadow-watch 0.50–0.55 band
                if confidence >= 0.50:
                    logger.info("mirror_shadow_conf_band",
                                confidence=round(confidence, 3),
                                gate_score=round(gate_score, 3),
                                side=side, price=round(price, 4),
                                trader=str(trader_address)[:16],
                                market=str(market_id)[:16])
                else:
                    logger.info("mirror_low_confidence", confidence=round(confidence, 3),
                                min_required=self.min_confidence, market=str(market_id)[:16])
                return False

        # S48 FIX: Use per-bot BotBankrollManager (Session 47) instead of deprecated
        # risk_manager.calculate_position_size() which divides Kelly by KELLY_ACTIVE_BOTS.
        # calculate_bot_position_size() returns shares (USD / price).
        # Session 82: Pass conformal_interval for conservative Kelly sizing when available.
        size = await self.calculate_bot_position_size(
            confidence=confidence,
            price=price,
            conformal_interval=None,
        )
        size *= reliability_mult

        # S146: Copy-P&L tiered sizing — scale by trader's demonstrated copyability.
        # Tier 1 (copy-profitable, n>=20): 1.0x. Tier 2 (thin data): 0.50x. Tier 3 (copy-unprofitable): 0.25x.
        # All 300 traders stay on watchlist — tiers control capital, not membership.
        _copy_tier = 2  # default: learning mode (also computed in S153 block above)
        if self._watchlist:
            _copy_tier = self._watchlist.get_copy_tier(trader_address)
        if _copy_tier == 2:
            _tier_mult = float(getattr(settings, "MIRROR_COPY_TIER2_MULT", 0.50))
            size *= _tier_mult
        elif _copy_tier == 3:
            _tier_mult = float(getattr(settings, "MIRROR_COPY_TIER3_MULT", 0.25))
            size *= _tier_mult
            logger.info("mirror_copy_tier3", trader=trader_address[:10],
                        mult=_tier_mult, market=str(market_id)[:16])
        # Tier 1: no multiplier (1.0x)

        # S142: Baker-McHale edge-uncertainty shrinkage.
        # Kelly oversizes when the edge estimate is uncertain (few resolved trades).
        # Formula: k = edge² / (edge² + var)  where var = p*(1-p)/n (binomial SE²).
        # Reference: Baker & McHale (2013), Decision Analysis — shrink toward 0 when
        # standard error of the edge is large relative to the estimated edge itself.
        # S150: Lowered from n>=5 to n>=3 — apply earlier skepticism on thin-data traders.
        # At n=3-4, BM shrinkage heavily penalises uncertain edges; sample ramp (n/50) still
        # limits absolute size. Combined effect: don't overbet on luck.
        if _eq_n >= 3:
            _bm_edge = max(0.0, confidence - price)
            _bm_edge_sq = _bm_edge * _bm_edge
            _bm_var = confidence * (1.0 - confidence) / _eq_n  # binomial SE²
            _bm_denom = _bm_edge_sq + _bm_var
            if _bm_denom > 0:
                _bm_k = _bm_edge_sq / _bm_denom
                size *= _bm_k
                if _bm_k < 0.90:  # only log meaningful shrinkage
                    logger.info("mirror_bm_shrinkage", trader=trader_address[:10],
                                n=_eq_n, edge=round(_bm_edge, 3), k=round(_bm_k, 3),
                                market=str(market_id)[:16])

        # S124: NaN/inf guard — defense-in-depth against corrupted Kelly output
        if not _math_isfinite(size) or size < 0:
            size = 0.0

        # S142/S146: Dynamic NO-side dampener — price-tiered (replaces flat multiplier).
        # Data: NO = -$139K (87% of losses). Low NO-token prices = highest taker
        # slippage ratio on the platform; near-zero upside relative to execution cost.
        # S146: Added minimum edge gate — NO trades must show 5% edge (confidence - price).
        # S146: Raised hard-block floor from 0.10 to 0.20 (sub-20c NO has unreliable price feeds).
        # Master gate: MIRROR_NO_SIDE_DAMPENER must be < 1.0 to activate.
        if _side_upper == "NO":
            _no_master = float(getattr(settings, "MIRROR_NO_SIDE_DAMPENER", 0.3))
            if _no_master < 1.0:
                # S146: Hard-block floor — configurable (was hardcoded 0.10)
                _no_block_floor = float(getattr(settings, "MIRROR_NO_BLOCK_FLOOR", 0.20))
                if price < _no_block_floor:
                    logger.info("mirror_no_dynamic_blocked", no_price=round(price, 3),
                                reason=f"sub-{int(_no_block_floor*100)}c",
                                market=str(market_id)[:16])
                    return False
                # S146: Minimum edge gate — NO must show positive edge to enter.
                _no_min_edge = float(getattr(settings, "MIRROR_NO_MIN_EDGE", 0.05))
                _no_edge = confidence - price
                if _no_edge < _no_min_edge:
                    logger.info("mirror_no_edge_rejected", no_price=round(price, 3),
                                confidence=round(confidence, 3),
                                edge=round(_no_edge, 3), min_edge=_no_min_edge,
                                market=str(market_id)[:16])
                    return False
                elif price < 0.25:
                    _no_dampener = 0.15  # very cheap NO — high risk taker position
                elif price < 0.40:
                    _no_dampener = 0.30  # speculative
                elif price < 0.60:
                    _no_dampener = 0.50  # balanced market
                else:
                    _no_dampener = 0.75  # NO is market consensus (range 0.60–0.75)
                size *= _no_dampener
                logger.info("mirror_no_dynamic_dampened", no_price=round(price, 3),
                            dampener=_no_dampener, market=str(market_id)[:16])

        # S99b Option C: Dampen sizing in gray zone (5-7¢ / 93-95¢)
        # S119: Set to 1.0 (no-op) for data collection. Re-evaluate ~Mar 29.
        _soft_min = float(getattr(settings, "MIRROR_MIN_PRICE", 0.07))
        _soft_max = float(getattr(settings, "MIRROR_MAX_PRICE", 0.93))
        if price < _soft_min or price > _soft_max:
            _dampen = float(getattr(settings, "MIRROR_EXTREME_PRICE_DAMPENER", 1.0))
            size *= _dampen
            if _dampen < 1.0:
                logger.info("mirror_price_dampened: %.3f in gray zone, size *= %.2f", price, _dampen)

        # S110: Favorite dampener — reduce sizing on heavy favorites (low edge)
        # S119: Set to 1.0 (no-op) for data collection. Re-evaluate ~Mar 29.
        _fav_thresh = float(getattr(settings, "MIRROR_FAVORITE_PRICE_THRESHOLD", 0.70))
        if price > _fav_thresh:
            _fav_damp = float(getattr(settings, "MIRROR_FAVORITE_DAMPENER", 1.0))
            size *= _fav_damp
            if _fav_damp < 1.0:
                logger.info("mirror_favorite_dampened: price=%.3f, size *= %.2f",
                            price, _fav_damp)

        # S150: Adaptive bet-size multiplier — per-trade size reduction during drawdowns.
        # Complements daily cap mult (which limits total daily exposure) by also shrinking
        # each individual trade. Uses gentler decay (-4.0) than position limits (-8.0).
        if (self._adaptive_safety
                and getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False)
                and self._adaptive_safety._fitted):
            _bet_mult = self._adaptive_safety.get_adjusted_bet_size_mult()
            if _bet_mult < 1.0:
                size *= _bet_mult
                logger.info("mirror_adaptive_bet_size", mult=round(_bet_mult, 3),
                            market=str(market_id)[:16])

        # M9: Cap per-market exposure — percentage-based with absolute safety cap
        _capital = float(getattr(self.bankroll, 'capital', 0) or 0) if self.bankroll else float(getattr(settings, "MIRROR_TOTAL_CAPITAL", 20000))
        _capital = _capital or float(getattr(settings, "MIRROR_TOTAL_CAPITAL", 20000))
        _pct_cap = _capital * float(getattr(settings, "MIRROR_MAX_PER_MARKET_PCT", 0.05))
        _abs_cap = float(getattr(settings, "MIRROR_MAX_PER_MARKET", 400))
        max_per_market_usd = min(_pct_cap, _abs_cap)
        max_per_market_shares = max_per_market_usd / price if price > 0 else 0
        size = min(size, max_per_market_shares)

        # Cap by remaining daily exposure: read bankroll.max_daily_usd directly (matching _can_open_position fix)
        if self.bankroll:
            _max_daily_usd = self.bankroll.max_daily_usd
        else:
            max_daily_pct = getattr(settings, "MIRROR_MAX_DAILY_EXPOSURE_PCT", self.MAX_DAILY_EXPOSURE_PCT)
            _max_daily_usd = float(getattr(settings, "TOTAL_CAPITAL", 10000.0)) * max_daily_pct
        # Session 83: Apply adaptive safety daily cap multiplier (0.5-1.15x based on performance)
        if (self._adaptive_safety
                and getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False)
                and self._adaptive_safety._fitted):
            _max_daily_usd *= self._adaptive_safety.get_adjusted_daily_cap_mult()
        remaining_daily_usd = max(0.0, _max_daily_usd - self._daily_exposure)
        remaining_daily_shares = remaining_daily_usd / price if price > 0 else 0
        size = min(size, remaining_daily_shares)

        if size <= 0:
            logger.info("Mirror trade size zero after limits (per_mkt=$%.0f daily_rem=$%.0f), skipping",
                        max_per_market_usd, remaining_daily_usd)
            return False

        # S91: Min trade USD — skip dust trades (testing, rebalancing, airdrop farming)
        _min_trade_usd = float(getattr(settings, "MIRROR_MIN_TRADE_USD", 10.0))
        _trade_value_usd = size * price
        if _trade_value_usd < _min_trade_usd:
            logger.info("mirror_dust_skipped", trade_usd=round(_trade_value_usd, 2),
                        min_usd=_min_trade_usd, market_id=str(market_id)[:16])
            return False

        # Session 82: Tag RTDS trades so order_gateway can skip liquidity check (saves 100-300ms).
        if source == "rtds":
            self._current_correlation_id = f"rtds:{trader_address[:10]}"
        else:
            self._current_correlation_id = None

        # S113 P5: Persist trade context in event_data for retroactive analysis
        _event_data = {
            "category": category or "",
            "source": source,
            "whale_trade_usd": round(whale_trade_usd, 2),
            "conf_base": round(_base, 3),
            "conf_price_adj": round(_price_adj, 3),
            "conf_conv_adj": round(_conv_adj, 3),
            "conf_upstream": round(_raw_upstream, 3),
            "conf_cal_shadow": round(_conf_cal_shadow, 3) if _conf_cal_shadow is not None else None,
            "rel_mult": round(reliability_mult, 3),
            "trader": trader_address,  # S146: full address for copy-P&L attribution (was [:10])
            "consensus": _consensus_count,  # S153: from TTL-aware dict
            "scan_start_mono": getattr(self, "_scan_start_mono", None),  # S115
            "spread": round(_spread, 3) if _spread is not None else None,  # S133
            "copy_tier": _copy_tier,  # S146: 1=profitable, 2=thin, 3=unprofitable
            # S153: Split scoring data for retroactive analysis
            "gate_score": round(gate_score, 3),
            "kelly_prob": round(kelly_prob, 3),
            "gate_decay_w": round(_decay_w, 3),
            "eff_prior": round(_eff_prior, 3),
            "geo_mean": round(_geo, 3),
        }
        # S124: Merge ML selector scores into event_data for shadow ledger analysis
        if _ml_scores:
            _event_data.update(_ml_scores)

        # S145: Populate signal meta BEFORE place_order so auto-store picks it up
        self._pending_signal_meta[str(market_id)] = {
            "signal_direction": side,
            "signal_confidence": round(confidence, 4),
            "signal_source": f"mirror_rtds_{trader_address[:8]}",
            "signal_multiplier": round(reliability_mult, 4) if reliability_mult else None,
            "order_flow_direction": side,
            "order_flow_multiplier": None,
            "trends_signal": category,
            "trends_multiplier": None,
        }

        order = await self.place_order(
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            confidence=confidence,
            event_data=_event_data,
        )

        if order.get("success") and not order.get("idempotent"):
            _trade_usd = size * price
            self._daily_exposure += _trade_usd  # Track exposure in USD (skip idempotent dedup'd orders)

            # S91: Set per-market cooldown to prevent re-entry on same signal
            _cd_secs = int(getattr(settings, "MIRROR_MARKET_COOLDOWN_SECONDS", 1800))
            if _cd_secs > 0:
                self._market_cooldown[market_id] = _time.monotonic() + _cd_secs

            # M1: Track per-category exposure
            if category:
                self._category_exposure[category] = self._category_exposure.get(category, 0.0) + _trade_usd

            # Update position tracking with actual size
            # S133: Create entry if missing — without this, new trades have no exit monitoring
            pos_key = f"{market_id}:{token_id}"
            if pos_key in self._open_positions:
                self._open_positions[pos_key]["size"] += size
            else:
                self._open_positions[pos_key] = {
                    "side": side,
                    "size": size,
                    "entry_price": price,
                    "traders": {trader_address},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "category": category,
                    "entry_confidence": confidence,  # S150: for edge decay in exit eval
                }

            # S117: Track entry for opposing-side guard across restarts
            self._entered_market_sides.add((market_id, str(side).upper()))

            logger.info(
                "Mirror trade executed",
                market=market_id,
                side=side,
                trader=trader_address[:10],
                confidence=f"{confidence:.2%}",
                entry_confidence=round(confidence, 3),
                size=f"{size:.2f}",
                open_positions=len(self._open_positions),
                daily_exposure=f"{self._daily_exposure:.2f}",
                category=category,
            )

            # S145: Signal storage now handled automatically by place_order()
            return True
        return False