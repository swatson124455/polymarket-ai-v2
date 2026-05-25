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
from config.settings import settings, is_paper_trading_active

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

        # S156: Exposure lock — protects _daily_exposure and _category_exposure from
        # race conditions between concurrent RTDS callbacks.  The await gap between
        # _can_open_position() (reads) and the post-place_order increment (writes)
        # allows interleaving that can overshoot the daily cap.
        self._exposure_lock = asyncio.Lock()

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

        # S91: Tier 0 pre-trade filters (in-memory, <0.01ms)
        self._market_blocklist: set = set()  # market_ids to reject instantly
        self._entered_market_sides: set = set()  # {(market_id, side)} for opposing-side guard across restarts
        self._last_ems_prune: float = 0.0  # S160: prune resolved markets from _entered_market_sides
        self._market_cooldown: Dict[str, float] = {}  # market_id -> cooldown_expiry_monotonic
        # S137 C7: Market-maker detection — same trader YES+NO same market within 24h = liquidity
        # provision, not directional signal. Key: "{trader}:{market}:{side}" → monotonic timestamp.
        self._trader_market_sides: Dict[str, float] = {}

        # S159 C2: Slippage backoff tracking — .pop() at L1195/L1266 requires these dicts.
        # NOTE: Population logic (incrementing counts, setting backoff times) from S158 plan
        # was never committed — these dicts are inert. Init prevents AttributeError on exit/reap.
        # TODO: Investigate and implement the missing slippage backoff feature.
        self._slippage_fail_count: Dict[str, int] = {}
        self._slippage_backoff: Dict[str, float] = {}

        # S178 7J: ADWIN-U prediction drift detector — lazy-initialized on first check
        self._prediction_drift: Any = None

        # S99: Portfolio circuit breaker — pause entries when unrealized P&L < threshold
        self._circuit_breaker_until: float = 0.0  # monotonic time when pause expires
        self._halt_breaker_unready: bool = False  # P0.10: True when restore failed 3x at startup
        # S99b: Post-reset cooldown — prevent burst of trades after daily exposure reset
        self._daily_reset_cooldown: float = 0.0

        # S113 P2: Multi-whale consensus counter — tracks how many unique whales
        # attempted the same (market_id, side) even though same-side dedup blocks re-entry.
        # S153: Added TTL (30-min) to prevent stale consensus bonuses.
        self._whale_consensus: Dict[str, int] = {}  # legacy — kept for backward compat reads
        self._whale_consensus_ts: Dict[str, tuple] = {}  # "market_id:side" -> (count, mono_time)

        # S168 Phase 6: Active trader-exit signals — when RTDS SELL arrives from a tracked
        # trader, record it here so the exit loop can close positions where tracked traders sold.
        # Key: pos_key ("market_id:token_id") → set of trader addresses that issued SELL.
        # Pruned every 30min alongside _prune_entered_market_sides.
        self._trader_sell_signals: Dict[str, set] = {}

        # S168 Phase 7: Category information efficiency cache.
        # Per-category WR from resolved trade_events. Refreshed every 20 scans.
        # Categories >55% WR → 1.0, 45-55% → 0.5, <45% → 0.0 (hard block).
        self._category_ie_cache: Dict[str, float] = {}
        self._category_ie_last_refresh: float = 0.0

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

        # S164: Init summary — shows which optional modules loaded at INFO level
        logger.info("mirror_optional_modules: reliability=%s calibration=%s adaptive=%s",
                    self._reliability_tracker is not None,
                    self._calibration_stack is not None,
                    self._adaptive_safety is not None)

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
                    # Sequential lookup: condition_id first (indexed), numeric id second
                    row = await session.execute(
                        _text(
                            "SELECT yes_token_id, no_token_id FROM markets "
                            "WHERE condition_id = :mid LIMIT 1"
                        ),
                        {"mid": str(market_id)},
                    )
                    r = row.fetchone()
                    if r is None and str(market_id).isdigit():
                        row = await session.execute(
                            _text(
                                "SELECT yes_token_id, no_token_id FROM markets "
                                "WHERE id = :mid_int LIMIT 1"
                            ),
                            {"mid_int": int(market_id)},
                        )
                        r = row.fetchone()
                    if r:
                        resolved = "YES" if str(token_id) == str(r[0]) else "NO"
                        self._token_side_cache[cache_key] = resolved
                        return resolved
        except Exception as e:
            logger.warning("_get_token_side failed for %s: %s", str(market_id)[:16], e)
        return "YES"  # Fallback: assume YES token

    # M4: _update_consensus_threshold deleted — dead code (zero callers) with logic bug.
    # Bug: docstring said "3+ consecutive" but code adjusted on every single trade.

    # ── S172 D8: Signal Enhancement Override ──────────────────────
    # Return 1.0 (neutral) — disable the +20% boost that amplifies losses
    # when expectancy is negative. Keep -40% reduction active via base class
    # when MIRROR_SKIP_SIGNAL_ENHANCEMENTS=false (currently True/skipped).
    # This override ensures even if the skip flag is flipped, enhancements
    # are neutralized for MirrorBot.

    async def apply_signal_enhancements(
        self,
        market_id: str,
        token_id: str,
        direction: str,
        confidence: float,
        market_data=None,
    ) -> float:
        """S172 D8: Neutral override — no signal enhancement for MirrorBot."""
        return confidence

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
                            "  SUM(CASE WHEN event_type = 'ENTRY' "
                            "    THEN CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION) "
                            "    WHEN event_type = 'EXIT' "
                            "    THEN -CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION) "
                            "    ELSE 0 END) AS spent "
                            "FROM trade_events "
                            "WHERE bot_name = :bot AND event_type IN ('ENTRY', 'EXIT') AND event_time >= CURRENT_DATE "
                            "GROUP BY COALESCE(event_data->>'category', '')"
                        ),
                        {"bot": self.bot_name},
                    )
                    for _cr in _cat_rows.fetchall():
                        _cat_name = str(_cr[0] or "")
                        if _cat_name:
                            _val = float(_cr[1] or 0.0)
                            if _val < 0:
                                logger.warning("category_exposure_negative_clamped", cat=_cat_name, raw=round(_val, 2))
                            self._category_exposure[_cat_name] = max(0.0, _val)
                    if self._category_exposure:
                        logger.info("MirrorBot startup: seeded _category_exposure from trade_events: %s",
                                    {k: round(v, 0) for k, v in self._category_exposure.items()})
                except Exception as _cat_err:
                    logger.debug("Category exposure seed failed (non-critical): %s", _cat_err)

                # 2. Rebuild _open_positions from positions table (YES/NO only).
                # trader_addresses column added by migration 035 — falls back to '{}' on older rows.
                # S228 Bug 11: filter by is_paper to match active trading mode.
                # The positions table mixes paper and live rows (is_paper column is the
                # discriminator). Pre-fix, restore loaded ALL rows indiscriminately, so
                # live-mode exits tried to SELL paper-derived positions via real CLOB.
                # Surfaced S228 live flip #4 (2026-05-24): paper position 0xba7ab705…
                # triggered SELL signal under live mode → CLOB rejected with "not
                # enough balance / allowance: balance: 0". Schema column existed since
                # at least S85 (see _reap_resolved_positions at line 1616); restore
                # wiring was the gap — same root-cause class as S227 Bug 7 (V2 adapter
                # built but not wired). is_paper_trading_active() lives in
                # config/settings.py per S83 paper-is-production rule (bot source
                # avoids the direct SIMULATION mode name).
                _is_paper_mode = is_paper_trading_active()
                rows = await session.execute(
                    _text(
                        "SELECT market_id, token_id, side, size, entry_price, "
                        "       COALESCE(current_price, entry_price) AS current_price, opened_at, "
                        "       COALESCE(trader_addresses, '{}') AS trader_addresses "
                        "FROM positions "
                        "WHERE (bot_id = :bot OR source_bot = :bot) "
                        "  AND status = 'open' AND side IN ('YES', 'NO') "
                        "  AND is_paper = :is_paper"
                    ),
                    {"bot": self.bot_name, "is_paper": _is_paper_mode},
                )
                logger.info(
                    "mirror_restore_filter_applied",
                    simulation_mode=_is_paper_mode,
                    expected_is_paper=_is_paper_mode,
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

                    # S161: Enrich positions with category from ENTRY event_data.
                    # Without category, exits can't decrement _category_exposure → drift.
                    _cat_rows = await session.execute(
                        _text(
                            "SELECT DISTINCT ON (market_id) market_id, "
                            "       event_data->>'category' AS cat "
                            "FROM trade_events "
                            "WHERE bot_name = :bot AND event_type = 'ENTRY' "
                            "  AND market_id = ANY(:mids) "
                            "ORDER BY market_id, event_time DESC"
                        ),
                        {"bot": self.bot_name, "mids": _mids},
                    )
                    _cat_map = {}
                    for _cr in _cat_rows.fetchall():
                        _cn = str(_cr.cat or "")
                        if _cn:
                            _cat_map[_cr.market_id] = _cn
                    _cat_enriched = 0
                    for pk, pos in self._open_positions.items():
                        mid = pk.split(":", 1)[0]
                        _cat_val = _cat_map.get(mid, "")
                        if _cat_val:
                            pos["category"] = _cat_val
                            _cat_enriched += 1
                    if _cat_enriched:
                        logger.info("mirror_category_restored", enriched=_cat_enriched, total=len(self._open_positions))

                # S144: Count stale-price positions (current_price == entry_price).
                _stale = sum(
                    1 for p in self._open_positions.values()
                    if abs(float(p.get("current_price", 0) or 0) - float(p.get("entry_price", 0) or 0)) < 1e-6
                )
                logger.info(
                    "MirrorBot startup: restored %d open positions from DB (stale_price=%d)",
                    restored, _stale,
                )
            # S160: Only mark restored after all DB work succeeds — enables retry on failure.
            self._state_restored = True
            # S194: Notify base_engine that exposure restoration has completed.
            # Prior to S194, this wasn't wired — base_engine._exposure_restored
            # stayed False and the 120s startup-hold watchdog forced degraded mode
            # on every MB restart. MB's "exposure" for this semantic is the
            # paper_trades SUM computed inside _restore_state_on_startup (CLAUDE.md
            # State Persistence Decision Tree — MirrorBot net-counter pattern).
            if getattr(self, "base_engine", None) is not None:
                self.base_engine.mark_exposure_restored()
        except Exception as exc:
            logger.warning("MirrorBot _restore_state_on_startup failed, will retry next scan: %s", exc)
            # S194: retry-on-failure preserves the ability to recover in subsequent
            # scans. Do NOT mark exposure_restored here — watchdog's 120s fallback
            # handles the degraded case so the bot can still trade.

        # S168 Phase 8: Restore DB-authoritative state (cooldowns, circuit breaker).
        # OUTSIDE the main try block — failure here must NOT block core state restore.
        # P0.10: circuit breaker restore retries 3x (1s/4s/16s). Persistent failure
        # sets _halt_breaker_unready; _can_open_position() refuses all trades until
        # operator sets MIRROR_BREAKER_BYPASS=true and restarts.
        if getattr(settings, "MIRROR_BREAKER_BYPASS", False):
            logger.info("mirror_circuit_breaker_restore_bypassed: MIRROR_BREAKER_BYPASS=true")
        else:
            for _attempt, _delay in enumerate([0, 1, 4, 16]):
                if _delay:
                    await asyncio.sleep(_delay)
                try:
                    await self._restore_circuit_breaker()
                    break
                except Exception as _cb_err:
                    if _attempt < 3:
                        logger.warning(
                            "mirror_circuit_breaker_restore_retry",
                            attempt=_attempt + 1,
                            error=str(_cb_err),
                        )
                    else:
                        logger.critical(
                            "mirror_halt_breaker_unready",
                            attempts=4,
                            error=str(_cb_err),
                        )
                        self._halt_breaker_unready = True
        try:
            await self._restore_cooldowns()
        except Exception as _s8_err:
            logger.debug("mirror_cooldown_restore_skipped: %s", _s8_err)

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
                        "SELECT DISTINCT te.market_id, te.side FROM trade_events te "
                        "JOIN markets m ON m.condition_id = te.market_id "
                        "WHERE te.bot_name = :bot AND te.event_type = 'ENTRY' "
                        "AND te.side IN ('YES', 'NO') AND m.resolved = false"
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
            except Exception as e:
                logger.warning("reliability_cache_load_failed", error=str(e))

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
                logger.warning("MirrorBot calibration fit failed: %s", e)

        # Session 82: Refresh adaptive safety metrics periodically
        if self._adaptive_safety:
            try:
                await self._adaptive_safety.refresh(self._scan_count)
            except Exception as e:
                logger.warning("MirrorBot adaptive safety refresh failed: %s", e)

        # S168 Phase 7: Refresh category IE cache every 15min. Skip first 3 scans
        # to avoid DB pressure during startup restore sequence.
        import time as _time_ie
        if self._scan_count >= 3 and (_time_ie.monotonic() - self._category_ie_last_refresh) > 900:
            try:
                await self._refresh_category_ie()
                self._category_ie_last_refresh = _time_ie.monotonic()
            except Exception as _ie_err:
                logger.debug("mirror_category_ie_refresh_failed: %s", _ie_err)

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

            self._bg_recon_task = asyncio.create_task(_bg_recon())
            self._bg_recon_task.add_done_callback(self._task_error_handler)

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
                await asyncio.wait_for(self._update_elite_traders(), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("MirrorBot elite refresh timed out (30s) — continuing with stale list")
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

        # S178 7J: Periodic prediction drift check (every ~8 min at 30s scan interval)
        if self._scan_count % 15 == 5:
            await self._check_prediction_drift()

        # S85: Reap positions on resolved markets (every 20 scans)
        # S135: Also reconcile exited positions that are still status='open' in DB
        # S160: Prune _entered_market_sides for resolved markets (every 30min)
        if self._scan_count % 20 == 1:
            await self._reap_resolved_positions()
            await self._reconcile_exited_positions()
            import time as _time_mod
            if self._entered_market_sides and (_time_mod.monotonic() - self._last_ems_prune) >= 1800:
                self._last_ems_prune = _time_mod.monotonic()
                _ems_db = getattr(self.base_engine, "db", None)
                if _ems_db:
                    await self._prune_entered_market_sides(_ems_db)
                # S168 Phase 6: Prune sell signals for positions no longer open
                _stale_sell_keys = [k for k in self._trader_sell_signals if k not in self._open_positions]
                for _sk in _stale_sell_keys:
                    del self._trader_sell_signals[_sk]
                if _stale_sell_keys:
                    logger.debug("mirror_sell_signals_pruned", count=len(_stale_sell_keys))
                # S168 Phase 8: Cleanup expired state rows (5s timeout guard)
                await self._cleanup_expired_state()

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
                        _text("SELECT category, end_date_iso FROM markets WHERE condition_id = :mid LIMIT 1"),
                        {"mid": market_id},
                    )
                    r = row.fetchone()
                    if r is None and str(market_id).isdigit():
                        row = await session.execute(
                            _text("SELECT category, end_date_iso FROM markets WHERE id = :mid_int LIMIT 1"),
                            {"mid_int": int(market_id)},
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
                                _pmc_task = asyncio.create_task(self._persist_market_category(
                                    market_id, category, q,
                                    _yes_tid, _no_tid, _resolved, _resolution))
                                _pmc_task.add_done_callback(self._task_error_handler)
                except Exception as _clob_err:
                    logger.warning("CLOB category fallback failed for %s: %s",
                                   market_id[:16], _clob_err)
        except Exception as e:
            logger.warning("Market meta lookup failed for %s: %s", market_id, e)
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
            logger.warning("_persist_market_category failed: %s", exc)

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
            # S161: Build set of tokens with market_prices_latest coverage (one query).
            # Positions NOT in this set have no PM price feed — RTDS is the only source.
            _mpl_covered_tokens: set = set()
            try:
                _db = getattr(self.base_engine, "db", None)
                if _db and getattr(_db, "session_factory", None):
                    from sqlalchemy import text as _mpl_text
                    async with _db.get_session() as _mpl_s:
                        _mpl_rows = await _mpl_s.execute(
                            _mpl_text("SELECT DISTINCT token_id FROM market_prices_latest")
                        )
                        _mpl_covered_tokens = {str(r[0]) for r in _mpl_rows.fetchall() if r[0]}
            except Exception as e:
                logger.debug("mirror_mpl_covered_query_fail: %s", e)

            _rtds_updated = 0
            _rtds_persisted = 0
            for _pk, _pdata in self._open_positions.items():
                _tok = _pk.split(":", 1)[1] if ":" in _pk else ""
                if not _tok:
                    continue
                _rtds_p = self._watchlist.get_rtds_price(_tok, max_age_s=300.0)
                if _rtds_p is not None:
                    _old_cp = float(_pdata.get("current_price", 0) or 0)
                    _ep = float(_pdata.get("entry_price", 0) or 0)
                    # S161: Apply RTDS when (a) price stuck at entry (no PM update ever),
                    # OR (b) token has no market_prices_latest coverage (PM can't reach it).
                    # Do NOT unconditionally apply — RTDS last-trade can be worse than
                    # PM's fresh midpoint for tokens PM actively covers.
                    if abs(_old_cp - _ep) < 1e-6 or _tok not in _mpl_covered_tokens:
                        _pdata["current_price"] = _rtds_p
                        _rtds_updated += 1
                        # S159: Persist RTDS price to DB so _sync_prices_from_db
                        # doesn't overwrite with stale value next cycle.  Also seed
                        # market_prices_latest so position_manager can find it.
                        _mid = _pk.split(":", 1)[0] if ":" in _pk else ""
                        try:
                            _db = getattr(self.base_engine, "db", None)
                            if _db and getattr(_db, "session_factory", None):
                                from sqlalchemy import text as _sa_text
                                async with _db.get_session() as _rtds_s:
                                    await _rtds_s.execute(_sa_text(
                                        "UPDATE positions SET current_price = :price "
                                        "WHERE token_id = :tid AND status = 'open' "
                                        "AND COALESCE(source_bot, bot_id) = 'MirrorBot'"
                                    ), {"price": _rtds_p, "tid": _tok})
                                    await _rtds_s.execute(_sa_text(
                                        "INSERT INTO market_prices_latest "
                                        "(token_id, market_id, price, timestamp) "
                                        "VALUES (:token_id, :market_id, :price, :timestamp) "
                                        "ON CONFLICT (token_id) DO UPDATE SET "
                                        "  price = EXCLUDED.price, "
                                        "  market_id = EXCLUDED.market_id, "
                                        "  timestamp = EXCLUDED.timestamp "
                                        "WHERE EXCLUDED.timestamp > "
                                        "  market_prices_latest.timestamp"
                                    ), {"token_id": _tok, "market_id": _mid,
                                        "price": _rtds_p,
                                        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None)})
                                    await _rtds_s.commit()
                                    _rtds_persisted += 1
                        except Exception as e:
                            logger.warning("mirror_rtds_persist_fail: %s", e)
            if _rtds_updated:
                logger.info("mirror_rtds_price_overlay", updated=_rtds_updated,
                            persisted=_rtds_persisted,
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

            # S172 D7: Shared inviolable hard stop — fires BEFORE any bot-specific logic.
            _stop_check = self.base_engine.risk_manager.check_hard_stop_loss(
                bot_name=self.bot_name,
                pnl_pct=_pnl_pct,
            )
            if _stop_check["should_exit"]:
                logger.info("mirror_shared_hard_stop", market=_pos_key,
                            pnl_pct=f"{_pnl_pct:.2%}",
                            reason=_stop_check["reason"])
                positions_to_close.append((_pos_key, {
                    "exit_reason": _stop_check["reason"],
                    "pnl_pct": round(_pnl_pct, 4),
                }))
                continue

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

            # S168 Phase 6: Active trader-exit — if a tracked trader issued SELL via RTDS,
            # close the position on the next scan cycle. Only fires after 0.5h hold (safety
            # guard against freshly-entered positions where traders set isn't populated yet).
            # Skip positions already closed by the immediate RTDS SELL path (L1815+).
            if _hours_held > 0.5 and _pos_key in self._trader_sell_signals:
                _sell_traders = self._trader_sell_signals[_pos_key]
                _pos_traders = _pos.get("traders")
                # Check if ANY tracked trader on this position has sold
                if isinstance(_pos_traders, set) and _sell_traders & _pos_traders:
                    logger.info("mirror_trader_sell_exit",
                                pos_key=_pos_key,
                                sell_traders=len(_sell_traders & _pos_traders),
                                hours_held=round(_hours_held, 1))
                    positions_to_close.append((_pos_key, {
                        "exit_reason": "trader_sell_exit",
                        "pnl_pct": round(_pnl_pct, 4),
                        "hours_held": round(_hours_held, 1),
                    }))
                    continue

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

            # S158: Grace period — skip stop-loss evaluation during post-entry reversion window.
            # Data: 46% of 0-30min stops recover above entry price (whale impact reversion).
            # 2h grace lets reversion complete; near-resolution override still fires immediately.
            _grace_h = float(getattr(settings, "MIRROR_STOP_LOSS_GRACE_HOURS", 2.0))
            if _hours_held < _grace_h and (_ttr_hours is None or _ttr_hours >= _near_res_hours):
                continue  # skip stop-loss, but NOT take-profit/force-exit (already evaluated above)

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
            # S168 Phase 8: Persist to DB so it survives restart
            import time as _cb_time
            asyncio.ensure_future(self._save_circuit_breaker(_cb_time.time() + _cb_pause_min * 60))
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
        _now_mono = _time.monotonic()
        _zero_keys_to_remove: list = []  # S166: force-closed zero-size positions
        for pos_key, _exit_event_data in _deduped_exits:
            pos = self._open_positions.get(pos_key)
            if not pos:
                continue

            # S160: Slippage backoff — skip positions in exponential backoff after repeated slippage failures.
            _backoff_until = self._slippage_backoff.get(pos_key, 0.0)
            if _now_mono < _backoff_until:
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
                    # S166: Zero-size positions can never exit normally.  Leaving
                    # them in _open_positions permanently blocks the market via
                    # opposing-side guard and wastes a position cap slot.
                    # Force-close: remove from memory + close in DB.
                    logger.warning("mirror_force_close_zero_size",
                                   market=market_id[:20], token=token_id[:20])
                    _zero_keys_to_remove.append(pos_key)
                    try:
                        from sqlalchemy import text as _zs_sql
                        async with self.base_engine.db.get_session() as _zs:
                            await _zs.execute(_zs_sql(
                                "UPDATE positions SET status = 'closed' "
                                "WHERE market_id = :mid AND token_id = :tid "
                                "  AND COALESCE(source_bot, bot_id) = 'MirrorBot' "
                                "  AND status = 'open'"
                            ), {"mid": market_id, "tid": token_id})
                            await _zs.commit()
                    except Exception as _zs_err:
                        logger.warning("mirror_force_close_zero_size_db_fail: %s", _zs_err)
                    continue

                # current_price is mid from position_manager; exit fills against live top-of-book bid — re-read so we can detect stale-mid drift and abort false hard_stops.
                _decision_price = exit_price
                _live_bid: Optional[float] = None
                _tracker = getattr(self.base_engine, "orderbook_tracker", None)
                if _tracker is not None:
                    try:
                        _book = await _tracker.snapshot_order_book(token_id, condition_id=market_id)
                        _bids = (_book or {}).get("bids") or []
                        if _bids:
                            _bid_px = float(_bids[0].get("price", 0))
                            # Polymarket tick prices are [0.01, 0.99]; outside this band = format mismatch or API shape change.
                            if 0.005 <= _bid_px <= 0.999:
                                _live_bid = _bid_px
                            elif _bid_px > 0:
                                logger.warning("mirror_exit_live_bid_implausible",
                                               market=market_id[:20], raw_bid=_bid_px)
                    except Exception as _re_err:
                        logger.warning("mirror_exit_price_revalidate_failed",
                                       market=market_id[:20], error=str(_re_err)[:200])

                if _live_bid is not None and _decision_price > 0:
                    _drift = (_live_bid - _decision_price) / _decision_price
                    if abs(_drift) > 0.05:
                        logger.warning("mirror_exit_price_drift",
                                       market=market_id[:20],
                                       decision_price=round(_decision_price, 4),
                                       live_bid=round(_live_bid, 4),
                                       drift_pct=round(_drift * 100, 2),
                                       exit_reason=_exit_event_data.get("exit_reason", "unknown"))
                        # A.5: Both hard_stop_loss and graduated stop_loss fire on a -threshold pnl_pct
                        # computed from pos['current_price'] (the stale mid). Re-check each against the
                        # live bid so a stale-mid-driven false trigger aborts before SELL fires.
                        # Other exits (force_exit, take_profit, near-resolution) gate on time/price-target,
                        # not on a current_price-derived pnl threshold, so don't need re-validation.
                        _exit_reason = _exit_event_data.get("exit_reason")
                        if _exit_reason == "hard_stop_loss":
                            _entry = float(pos.get("entry_price", 0) or 0)
                            if _entry > 0:
                                _live_pnl_pct = (_live_bid - _entry) / _entry
                                _live_stop = self.base_engine.risk_manager.check_hard_stop_loss(
                                    bot_name=self.bot_name,
                                    pnl_pct=_live_pnl_pct,
                                )
                                if not _live_stop.get("should_exit"):
                                    logger.warning("mirror_hard_stop_aborted_stale_price",
                                                   market=market_id[:20],
                                                   entry=round(_entry, 4),
                                                   decision_price=round(_decision_price, 4),
                                                   live_bid=round(_live_bid, 4),
                                                   live_pnl_pct=round(_live_pnl_pct, 4))
                                    pos["current_price"] = _live_bid
                                    await self._persist_refreshed_exit_price(token_id, market_id, _live_bid)
                                    continue
                        elif _exit_reason == "stop_loss":
                            # Graduated stop_loss stores its computed effective threshold in event_data
                            # at the evaluation site (mirror_bot.py:1278). Read it back rather than
                            # recompute — avoids re-deriving _effective_stop from entry_confidence + hours_held.
                            _entry = float(pos.get("entry_price", 0) or 0)
                            _eff_stop = float(_exit_event_data.get("threshold", 0.30))
                            if _entry > 0:
                                _live_pnl_pct = (_live_bid - _entry) / _entry
                                if _live_pnl_pct > -_eff_stop:
                                    logger.warning("mirror_stop_loss_aborted_stale_price",
                                                   market=market_id[:20],
                                                   entry=round(_entry, 4),
                                                   decision_price=round(_decision_price, 4),
                                                   live_bid=round(_live_bid, 4),
                                                   live_pnl_pct=round(_live_pnl_pct, 4),
                                                   effective_stop=round(_eff_stop, 4))
                                    pos["current_price"] = _live_bid
                                    await self._persist_refreshed_exit_price(token_id, market_id, _live_bid)
                                    continue

                _exit_event_data = dict(_exit_event_data)
                _exit_event_data["decision_price"] = round(_decision_price, 6)
                if _live_bid is not None:
                    _exit_event_data["pre_exec_live_bid"] = round(_live_bid, 6)

                # S228 Bug 11C: pre-flight CTF balance check for live mode SELLs.
                # Self-driven exits (TP/SL/illiquidity/force_exit) on a position
                # the deposit wallet doesn't actually hold would burn 3
                # OrderGateway retries on "balance: 0" rejections. Skip cleanly.
                if not await self._live_sell_balance_guard(
                    market_id=market_id, token_id=token_id,
                    size=exit_size, side=exit_side,
                ):
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
                    # S156: Use actual filled quantity — partial fills leave remainder
                    _filled_exit = float(order.get("filled", exit_size))
                    logger.info(
                        "Mirror exit executed",
                        market=market_id,
                        exit_side=exit_side,
                        original_side=pos["side"],
                        size=f"{_filled_exit:.2f}",
                        requested=f"{exit_size:.2f}",
                    )
                    _exit_cost = _filled_exit * pos.get("entry_price", exit_price)
                    # S156: Decrement under lock (matches entry reservation pattern)
                    async with self._exposure_lock:
                        self._daily_exposure = max(0.0, self._daily_exposure - _exit_cost)
                        # M1: Decrement category exposure on exit
                        _pos_cat = pos.get("category", "")
                        if _pos_cat:
                            self._category_exposure[_pos_cat] = max(
                                0.0, self._category_exposure.get(_pos_cat, 0.0) - _exit_cost
                            )
                    # Partial fill: decrement position size, full fill: delete
                    if _filled_exit >= exit_size - 0.01:
                        del self._open_positions[pos_key]
                        self._slippage_fail_count.pop(pos_key, None)
                        self._slippage_backoff.pop(pos_key, None)
                    else:
                        _remaining = max(0.0, pos["size"] - _filled_exit)
                        self._open_positions[pos_key]["size"] = _remaining
                        # S156: Warn at WARNING level — operator visibility for orphaned residuals.
                        # Position stays in _open_positions and will be re-evaluated for exit
                        # on next scan cycle (stop-loss, force-exit, take-profit all check size).
                        logger.warning("mirror_partial_exit_residual",
                                       market=market_id[:20],
                                       filled=round(_filled_exit, 2),
                                       remaining=round(_remaining, 2),
                                       exit_reason=_exit_event_data.get("exit_reason", "unknown"))
                    # S135/S160: Mark position closed on full fill, update size on partial.
                    # Was unconditional close — partial fills lost residual shares on restart.
                    # S141: Retry to prevent ghost exits (pool exhaustion → silent failure).
                    from sqlalchemy import text as _sql
                    for _close_attempt in range(3):
                        try:
                            async with self.base_engine.db.get_session() as _cs:
                                if _filled_exit >= exit_size - 0.01:
                                    await _cs.execute(_sql(
                                        "UPDATE positions SET status = 'closed' "
                                        "WHERE market_id = :mid AND token_id = :tid "
                                        "  AND COALESCE(source_bot, bot_id) = 'MirrorBot' "
                                        "  AND status = 'open'"
                                    ), {"mid": market_id, "tid": token_id})
                                else:
                                    await _cs.execute(_sql(
                                        "UPDATE positions SET size = :sz "
                                        "WHERE market_id = :mid AND token_id = :tid "
                                        "  AND COALESCE(source_bot, bot_id) = 'MirrorBot' "
                                        "  AND status = 'open'"
                                    ), {"mid": market_id, "tid": token_id, "sz": _remaining})
                                await _cs.commit()
                            break
                        except Exception as _db_err:
                            if _close_attempt < 2:
                                await asyncio.sleep(0.5)
                            else:
                                logger.warning("mirror_exit_db_close_failed market=%s attempt=%d: %s",
                                               market_id[:20], _close_attempt + 1, _db_err)
                else:
                    # S160: Track slippage failures for exponential backoff.
                    # After 3 consecutive slippage failures, back off 100s→200s→400s (cap 900s).
                    _fail_code = order.get("fail_code", "")
                    if _fail_code == "slippage":
                        _count = self._slippage_fail_count.get(pos_key, 0) + 1
                        self._slippage_fail_count[pos_key] = _count
                        if _count >= 3:
                            _backoff_secs = min(900.0, 100.0 * (2 ** (_count - 3)))
                            self._slippage_backoff[pos_key] = _now_mono + _backoff_secs
                            logger.info("mirror_slippage_backoff", market=market_id[:20],
                                        count=_count, backoff_s=round(_backoff_secs))
                    else:
                        # Non-slippage failure: clear slippage streak
                        self._slippage_fail_count.pop(pos_key, None)
            except Exception as e:
                logger.warning("Failed to execute mirror exit for %s: %s", pos_key, e)

        # S166: Remove force-closed zero-size positions from in-memory dict.
        # Done outside the loop to avoid mutating _open_positions during iteration.
        for _zk in _zero_keys_to_remove:
            self._open_positions.pop(_zk, None)
            self._slippage_fail_count.pop(_zk, None)
            self._slippage_backoff.pop(_zk, None)

    async def _persist_refreshed_exit_price(
        self,
        token_id: str,
        market_id: str,
        new_price: float,
    ) -> None:
        """A.5: Persist a price refresh decision from the exit re-validation path.

        Writes new_price to both positions.current_price and market_prices_latest
        via the S141 timestamp-guard UPSERT pattern, so position_manager's next
        10s sync doesn't pull the stale mid back over our refresh.

        Best-effort: any DB failure logs `mirror_exit_price_refresh_db_failed`
        but never raises. Caller already updated `pos['current_price']` in memory,
        so the next scan cycle still sees the refreshed value even if the DB
        persistence path is degraded.

        Used by both the hard_stop_loss and graduated stop_loss abort branches
        in `_check_and_execute_exits` — extracted to a single helper so the
        UPSERT pattern has one source of truth.
        """
        _db = getattr(self.base_engine, "db", None)
        if not (_db and getattr(_db, "session_factory", None)):
            return
        try:
            from sqlalchemy import text as _refresh_sql
            async with _db.get_session() as _rs:
                await _rs.execute(_refresh_sql(
                    "UPDATE positions SET current_price = :p "
                    "WHERE token_id = :tid AND status = 'open' "
                    "  AND COALESCE(source_bot, bot_id) = 'MirrorBot'"
                ), {"p": new_price, "tid": token_id})
                await _rs.execute(_refresh_sql(
                    "INSERT INTO market_prices_latest "
                    "(token_id, market_id, price, timestamp) "
                    "VALUES (:tid, :mid, :p, :ts) "
                    "ON CONFLICT (token_id) DO UPDATE SET "
                    "  price = EXCLUDED.price, "
                    "  market_id = EXCLUDED.market_id, "
                    "  timestamp = EXCLUDED.timestamp "
                    "WHERE EXCLUDED.timestamp > market_prices_latest.timestamp"
                ), {"tid": token_id, "mid": market_id, "p": new_price,
                    "ts": datetime.now(timezone.utc).replace(tzinfo=None)})
                await _rs.commit()
        except Exception as _rd_err:
            logger.warning("mirror_exit_price_refresh_db_failed",
                           market=market_id[:20], error=str(_rd_err)[:200])

    async def _live_sell_balance_guard(
        self,
        market_id: str,
        token_id: str,
        size: float,
        side: str = "SELL",
    ) -> bool:
        """S228 Bug 11C: skip SELLs when deposit wallet doesn't hold the
        outcome token in sufficient quantity.

        Defense-in-depth against future regressions of S228 Bug 11A
        (restore-filter wiring) and any other path that could route a
        SELL signal for an unowned token. Without this, the order
        reaches CLOB and produces "not enough balance / allowance:
        balance: 0", costing 3 OrderGateway retries per attempt.

        Returns True to proceed, False to skip. Defers (returns True)
        in paper mode, when RPC/wallet config is missing, or when the
        balance check itself fails — the goal is "don't make the
        problem worse than the existing failure surface."
        """
        if is_paper_trading_active():
            return True
        try:
            from base_engine.execution.clob_adapter import check_ctf_balance
            bal = await check_ctf_balance(token_id=str(token_id))
        except Exception as _e:
            logger.debug("sell_balance_guard_skipped: check failed: %s", _e)
            return True
        if bal is None:
            return True  # config missing or RPC down — defer to existing logic
        # S228 Bug 11C rounding: on-chain CTF is 6-decimal; DB stores the
        # higher-precision REQUESTED size from order placement (e.g., 8.695652)
        # while the FILLED on-chain balance is 6-decimal exact (e.g., 8.690000).
        # Sub-cent gaps are 6-decimal rounding artifacts, not phantom positions.
        # Block only when the gap is more than 0.01 tokens (~1 cent at $1
        # trades). The proper fix is to write filled size (not requested) at
        # position-write time — see AGENT_HANDOFF for Bug 12 trace — but until
        # that lands, the epsilon prevents the guard from blocking legitimate
        # exits while still catching genuinely-unbacked SELL attempts.
        _SELL_EPSILON_TOKENS = 0.01
        if bal < float(size) - _SELL_EPSILON_TOKENS:
            logger.warning(
                "mirror_sell_balance_guard_reject",
                market_id=str(market_id)[:20],
                token_id=str(token_id)[:30],
                side=side,
                requested_size=round(float(size), 6),
                available_balance=round(bal, 6),
                gap=round(float(size) - bal, 6),
                note="deposit wallet outcome-token balance insufficient (gap exceeds 0.01 token epsilon)",
            )
            return False
        return True

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
                    "    SELECT condition_id FROM markets WHERE resolved = TRUE AND condition_id IS NOT NULL"
                    "  ) "
                    "RETURNING market_id, token_id"
                ), {"bot": self.bot_name})
                reaped = result.fetchall()
                await session.commit()
                if reaped:
                    _reaped_usd = 0.0
                    _reaped_cats: dict = {}
                    for row in reaped:
                        pos_key = f"{row[0]}:{row[1]}"
                        _pos = self._open_positions.pop(pos_key, None)
                        # S158: Clean slippage backoff for reaped positions
                        self._slippage_fail_count.pop(pos_key, None)
                        self._slippage_backoff.pop(pos_key, None)
                        # S113 P7: Decrement daily exposure for resolved positions —
                        # without this, resolved positions inflate _daily_exposure
                        # and block new trades via the daily cap.
                        if _pos:
                            _pos_cost = _pos.get("size", 0.0) * _pos.get("entry_price", 0.0)
                            _reaped_usd += _pos_cost
                            _pos_cat = _pos.get("category", "")
                            if _pos_cat:
                                _reaped_cats[_pos_cat] = _reaped_cats.get(_pos_cat, 0.0) + _pos_cost
                    # S156: Decrement under lock
                    if _reaped_usd > 0:
                        async with self._exposure_lock:
                            self._daily_exposure = max(0.0, self._daily_exposure - _reaped_usd)
                            for _rc_cat, _rc_cost in _reaped_cats.items():
                                self._category_exposure[_rc_cat] = max(
                                    0.0, self._category_exposure.get(_rc_cat, 0.0) - _rc_cost
                                )
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
                    _recon_usd = 0.0
                    _recon_cats: dict = {}
                    for row in closed:
                        pos_key = f"{row[0]}:{row[1]}"
                        _pos = self._open_positions.pop(pos_key, None)
                        if _pos:
                            _pos_cost = _pos.get("size", 0.0) * _pos.get("entry_price", 0.0)
                            _recon_usd += _pos_cost
                            _pos_cat = _pos.get("category", "")
                            if _pos_cat:
                                _recon_cats[_pos_cat] = _recon_cats.get(_pos_cat, 0.0) + _pos_cost
                    # S156: Decrement under lock
                    if _recon_usd > 0:
                        async with self._exposure_lock:
                            self._daily_exposure = max(0.0, self._daily_exposure - _recon_usd)
                            for _rc_cat, _rc_cost in _recon_cats.items():
                                self._category_exposure[_rc_cat] = max(
                                    0.0, self._category_exposure.get(_rc_cat, 0.0) - _rc_cost
                                )
                    logger.info("mirror_reconcile_exited: closed %d zombie positions in DB", len(closed))
        except Exception as exc:
            logger.warning("mirror_reconcile_exited failed: %s", exc)

    async def _prune_entered_market_sides(self, db) -> None:
        """S160: Remove (market_id, side) pairs for resolved markets.

        Resolved markets can't be traded again, so the opposing-side guard
        is useless for them.  Active markets with closed positions KEEP
        their guard — prevents re-entering the opposite side within a
        market's lifetime.
        """
        if not self._entered_market_sides:
            return
        try:
            from sqlalchemy import text as _p_text
            _market_ids = list({mid for mid, _ in self._entered_market_sides})
            async with db.get_session(timeout=15) as _p_sess:
                _rows = await _p_sess.execute(
                    _p_text(
                        "SELECT condition_id FROM markets "
                        "WHERE condition_id = ANY(:ids) AND resolved = true"
                    ),
                    {"ids": _market_ids},
                )
                _resolved = {r[0] for r in _rows.fetchall()}
            if _resolved:
                _before = len(self._entered_market_sides)
                self._entered_market_sides = {
                    (mid, side) for mid, side in self._entered_market_sides
                    if mid not in _resolved
                }
                _pruned = _before - len(self._entered_market_sides)
                if _pruned:
                    logger.info("mirror_ems_pruned",
                                pruned=_pruned, remaining=len(self._entered_market_sides))
        except Exception as _exc:
            logger.debug("mirror_ems_prune_failed", error=str(_exc))

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

    def _market_quality_score(
        self,
        volume_24h: float = 0.0,
        spread: Optional[float] = None,
        hours_to_resolution: Optional[float] = None,
        category: str = "",
    ) -> float:
        """S168 Phase 7: Composite market quality score (0.0-1.0).

        Geometric mean of:
          - Volume: log scale $0→0.0, $5K→0.50, $20K+→1.0
          - Spread: linear 0→1.0, 0.05→0.67, 0.15→0.0
          - TTR: log scale <2h→0.30, 8h→0.70, 24h+→1.0
          - Category IE: from _category_ie_cache (refreshed every 20 scans)
        """
        import math as _m

        # Volume component
        if volume_24h <= 0:
            _f_vol = 0.0
        elif volume_24h >= 20000:
            _f_vol = 1.0
        else:
            _f_vol = _m.log1p(volume_24h) / _m.log1p(20000)

        # Spread component
        if spread is None or spread <= 0:
            _f_spread = 1.0  # no spread data = optimistic
        elif spread >= 0.15:
            _f_spread = 0.0
        else:
            _f_spread = max(0.0, 1.0 - spread / 0.15)

        # TTR component
        if hours_to_resolution is None:
            _f_ttr = 0.70  # unknown = moderate
        elif hours_to_resolution < 2:
            _f_ttr = 0.30
        elif hours_to_resolution >= 24:
            _f_ttr = 1.0
        else:
            _f_ttr = 0.30 + 0.70 * _m.log1p(hours_to_resolution - 2) / _m.log1p(22)

        # Category IE component (from cache, refreshed on 20-scan cadence)
        _f_cat = 0.50  # default: unknown category = neutral
        _cat_ie = getattr(self, "_category_ie_cache", {})
        if category and _cat_ie:
            _cat_lower = category.lower().strip()
            if _cat_lower in _cat_ie:
                _f_cat = _cat_ie[_cat_lower]

        _factors = [f for f in [_f_vol, _f_spread, _f_ttr, _f_cat] if f > 0]
        if not _factors:
            return 0.0
        return _m.prod(_factors) ** (1.0 / len(_factors))

    async def _save_circuit_breaker(self, resume_at_wall: float) -> None:
        """S168 Phase 8: Persist circuit breaker to DB so it survives restart."""
        try:
            from sqlalchemy import text
            import json
            _ttl_secs = max(0, resume_at_wall - __import__("time").time())
            async with self.base_engine.db.get_session() as _s:
                await _s.execute(text(
                    "INSERT INTO mirror_state (key, value_json, expires_at, updated_at)"
                    " VALUES ('circuit_breaker', :val, NOW() + :ttl * INTERVAL '1 second', NOW())"
                    " ON CONFLICT (key) DO UPDATE SET"
                    " value_json = EXCLUDED.value_json,"
                    " expires_at = EXCLUDED.expires_at,"
                    " updated_at = NOW()"
                ), {"val": json.dumps({"resume_at": resume_at_wall}), "ttl": _ttl_secs})
                await _s.commit()
        except Exception as _e:
            logger.debug("mirror_save_circuit_breaker_failed: %s", _e)

    async def _restore_circuit_breaker(self) -> None:
        """S168 Phase 8: Restore circuit breaker from DB on startup.

        Raises on DB failure — caller (P0.10 retry loop) handles retries and HALT.
        """
        from sqlalchemy import text
        import time as _t
        async with self.base_engine.db.get_session() as _s:
            _r = await _s.execute(text(
                "SELECT value_json FROM mirror_state"
                " WHERE key = 'circuit_breaker' AND expires_at > NOW()"
            ))
            _row = _r.fetchone()
            if _row and _row[0]:
                _val = _row[0] if isinstance(_row[0], dict) else __import__("json").loads(_row[0])
                _wall_resume = float(_val.get("resume_at", 0))
                _remaining = _wall_resume - _t.time()
                if _remaining > 0:
                    self._circuit_breaker_until = _t.monotonic() + _remaining
                    logger.info("mirror_circuit_breaker_restored", remaining_s=round(_remaining, 0))

    async def _save_cooldown(self, market_id: str, ttl_secs: float) -> None:
        """S168 Phase 8: Persist market cooldown to DB."""
        try:
            from sqlalchemy import text
            import json
            _key = f"cooldown:{market_id}"
            async with self.base_engine.db.get_session() as _s:
                await _s.execute(text(
                    "INSERT INTO mirror_state (key, value_json, expires_at, updated_at)"
                    " VALUES (:key, :val, NOW() + :ttl * INTERVAL '1 second', NOW())"
                    " ON CONFLICT (key) DO UPDATE SET"
                    " value_json = EXCLUDED.value_json,"
                    " expires_at = EXCLUDED.expires_at,"
                    " updated_at = NOW()"
                ), {"key": _key, "val": json.dumps({"market_id": market_id}), "ttl": ttl_secs})
                await _s.commit()
        except Exception as _e:
            logger.debug("mirror_save_cooldown_failed: %s market=%s", _e, market_id[:16])

    async def _restore_cooldowns(self) -> None:
        """S168 Phase 8: Restore market cooldowns from DB on startup."""
        try:
            from sqlalchemy import text
            import time as _t
            async with self.base_engine.db.get_session() as _s:
                _r = await _s.execute(text(
                    "SELECT key, EXTRACT(EPOCH FROM (expires_at - NOW())) AS remaining"
                    " FROM mirror_state"
                    " WHERE key LIKE 'cooldown:%' AND expires_at > NOW()"
                ))
                _count = 0
                for _row in _r.fetchall():
                    _market_id = _row[0].replace("cooldown:", "", 1)
                    _remaining = float(_row[1])
                    if _remaining > 0:
                        self._market_cooldown[_market_id] = _t.monotonic() + _remaining
                        _count += 1
                if _count > 0:
                    logger.info("mirror_cooldowns_restored", count=_count)
        except Exception as _e:
            logger.debug("mirror_restore_cooldowns_failed: %s", _e)

    async def _cleanup_expired_state(self) -> None:
        """S168 Phase 8: Delete expired rows from mirror_state. 5s timeout guard."""
        try:
            from sqlalchemy import text
            async with self.base_engine.db.get_session() as _s:
                await _s.execute(text("SET LOCAL statement_timeout = '5000'"))
                await _s.execute(text(
                    "DELETE FROM mirror_state WHERE expires_at IS NOT NULL AND expires_at < NOW()"
                ))
                await _s.commit()
        except Exception as _e:
            logger.debug("mirror_cleanup_state_failed: %s", _e)

    async def _check_prediction_drift(self) -> None:
        """S178 7J: Periodic ADWIN-U drift check on prediction_log.realized_edge."""
        if self._prediction_drift is None:
            _db = getattr(self.base_engine, "db", None)
            if _db:
                try:
                    from base_engine.learning.prediction_drift import PredictionDriftDetector
                    self._prediction_drift = PredictionDriftDetector(_db, bot_name="MirrorBot")
                except Exception:
                    return
        if self._prediction_drift:
            try:
                report = await self._prediction_drift.check()
                if report.get("drift_detected"):
                    logger.warning(
                        "prediction_drift_detected",
                        drift_type=report.get("drift_type"),
                        window_size=report.get("window_size"),
                        current_mean=round(report.get("current_mean", 0), 4),
                        baseline_mean=round(report.get("baseline_mean") or 0, 4),
                        new_observations=report.get("new_observations"),
                    )
            except Exception as _e:
                logger.debug("prediction_drift_check_failed", error=str(_e))

    async def _refresh_category_ie(self) -> None:
        """S168 Phase 7: Refresh category information efficiency from trade_events.
        Categories >55% WR → 1.0, 45-55% → 0.5, <45% → 0.0."""
        try:
            from sqlalchemy import text
            # S194: MIRROR_REGIME_START is now Optional[datetime] (was str). Handle both
            # for safety against partial deploys / older settings versions, but treat
            # datetime as the canonical post-S194 type.
            _regime = getattr(settings, "MIRROR_REGIME_START", None)
            if _regime is None:
                # No regime filter — skip the time-bounded refresh; the unbounded
                # query is too expensive to run by default.
                return
            if isinstance(_regime, str):
                from datetime import datetime as _dt
                _since = _dt.fromisoformat(_regime)
            else:
                _since = _regime  # already a datetime per S194 settings change
            async with self.base_engine.db.get_session() as _s:
                _r = await _s.execute(text(
                    "SELECT LOWER(COALESCE(event_data->>'category', 'unknown')) AS cat,"
                    " COUNT(*) AS n,"
                    " COUNT(CASE WHEN realized_pnl > 0 THEN 1 END) AS wins"
                    " FROM trade_events"
                    " WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
                    "   AND event_time >= :since AND realized_pnl IS NOT NULL"
                    " GROUP BY LOWER(COALESCE(event_data->>'category', 'unknown'))"
                ), {"since": _since})
                _rows = _r.fetchall()
            _new_cache: Dict[str, float] = {}
            for _cat, _n, _wins in _rows:
                if _n < 10:
                    continue
                _wr = _wins / _n
                if _wr > 0.55:
                    _new_cache[_cat] = 1.0
                elif _wr >= 0.45:
                    _new_cache[_cat] = 0.50
                else:
                    _new_cache[_cat] = 0.0
            self._category_ie_cache = _new_cache
            logger.info("mirror_category_ie_refreshed", categories=len(_new_cache),
                        cache={k: v for k, v in sorted(_new_cache.items())})
        except Exception as _e:
            logger.warning("mirror_category_ie_refresh_error: %s", _e)

    def _can_open_position(self, price: float, category: str = "") -> bool:
        """Check concurrent position + daily exposure + category + price limits.

        Returns False with a specific INFO log identifying WHICH limit was hit.
        """
        # P0.10: Refuse new positions if circuit breaker state could not be restored.
        # Clear by setting MIRROR_BREAKER_BYPASS=true in env and restarting.
        if self._halt_breaker_unready:
            logger.warning("mirror_halt_breaker_unready: refusing trade — restart with MIRROR_BREAKER_BYPASS=true to override")
            return False

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

        # S157: Daily exposure + category caps moved INSIDE _exposure_lock (L2448-2462).
        # Pre-lock reads of _daily_exposure were racy — two concurrent RTDS callbacks
        # could both pass stale checks and overshoot the cap.
        # Only non-racy checks remain here (position count, opposing-side guard above).

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
            # S158: Prune expired market cooldowns (entries accumulate, never removed)
            _now_mono = _time.monotonic()
            self._market_cooldown = {k: v for k, v in self._market_cooldown.items() if v > _now_mono}

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
            logger.warning("mirror_dedup_save failed: %s", e)

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

    # ── Rejection Instrumentation (S172 7B Phase A) ─────────────────
    async def _log_rejection(
        self,
        trader_address: str,
        market_id: str,
        rejection_reason: str,
        rejection_stage: str,
        token_id: Optional[str] = None,
        side: Optional[str] = None,
        price: Optional[float] = None,
        whale_trade_usd: Optional[float] = None,
        signal_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """S172 7B Phase A: log a rejected whale signal for counterfactual PnL analysis.

        Called at every mirror_bot.py rejection site (22 total, see
        docs/7B_wallet_overhaul_design.md §A2). Writes to mirror_rejected_signals
        (migration 073). Pure additive instrumentation — MUST NOT block the trade
        path; any failure is swallowed and logged at warning level.

        Args:
            trader_address: Full 42-char hex wallet address. Never truncated —
                Phase B's counterfactual ranking collides on 10-hex prefixes.
            market_id: Polymarket market identifier.
            rejection_reason: The specific gate/reason code, matching the
                existing logger.info emitter name (e.g. "mirror_whale_too_small").
            rejection_stage: One of "pre_gate" | "gate" | "post_gate".
                Matches §A2 stage buckets. watchlist/pre_watchlist stages
                live in elite_watchlist.py and are out of scope per S187 §2.1.
            token_id / side / price / whale_trade_usd: optional structured context.
            signal_metadata: optional site-specific context as a dict
                (e.g. {"gate_score": 0.41, "threshold": 0.52}). Phase B's
                retune script expects structured keys; do NOT pass full RTDS
                event_data (row size balloons, GIN indexing becomes expensive).

        Returns: None. Never raises — instrumentation failure must not block trading.
        """
        _db = getattr(self.base_engine, "db", None)
        if _db is None:
            return
        try:
            await _db.insert_mirror_rejected_signal(
                trader_address=trader_address,
                market_id=market_id,
                rejection_reason=rejection_reason,
                rejection_stage=rejection_stage,
                token_id=token_id,
                side=side,
                price=price,
                whale_trade_usd=whale_trade_usd,
                metadata=signal_metadata,
            )
        except Exception as _rej_err:
            # Match insert_prediction_log pattern: surfaced but never propagated.
            logger.warning("mirror_rejection_log_failed",
                           error=str(_rej_err),
                           trader=(trader_address[:10] if trader_address else None),
                           market_id=market_id,
                           reason=rejection_reason,
                           stage=rejection_stage)

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
                await self._log_rejection(
                    trader_address=trader_address, market_id=str(market_id),
                    rejection_reason="mirror_price_floor_blocked",
                    rejection_stage="pre_gate",
                    token_id=token_id, side=side, price=price,
                    whale_trade_usd=whale_trade_usd,
                    signal_metadata={"price_floor": 0.03, "price_ceiling": 0.97},
                )
                return False

            # S173 Day 2: Trader wallet blacklist — block specific addresses.
            # RC diagnostic MB-3: 5 worst wallets account for concentrated losses, all active.
            _trader_bl = getattr(settings, "MIRROR_TRADER_BLACKLIST", "")
            if _trader_bl and trader_address:
                _blocked_wallets = set(a.strip().lower() for a in _trader_bl.split(",") if a.strip())
                if trader_address.lower()[:10] in _blocked_wallets or trader_address.lower() in _blocked_wallets:
                    logger.info("mirror_trader_blacklisted",
                                trader=trader_address[:10],
                                market=str(market_id)[:16])
                    await self._log_rejection(
                        trader_address=trader_address, market_id=str(market_id),
                        rejection_reason="mirror_trader_blacklisted",
                        rejection_stage="pre_gate",
                        token_id=token_id, side=side, price=price,
                        whale_trade_usd=whale_trade_usd,
                        signal_metadata={"blacklist_source": "MIRROR_TRADER_BLACKLIST"},
                    )
                    return False

            # S173 Day 2: Minimum whale trade size hard gate.
            # RC diagnostic MB-4: small whale trades ($0-25) dominate losses while
            # trades above $100 are profitable. Hard floor before soft scoring.
            _min_whale_usd = float(getattr(settings, "MIRROR_MIN_WHALE_TRADE_USD", 100.0))
            if whale_trade_usd > 0 and whale_trade_usd < _min_whale_usd:
                logger.info("mirror_whale_too_small",
                            whale_usd=round(whale_trade_usd, 2),
                            min_usd=_min_whale_usd,
                            market=str(market_id)[:16])
                await self._log_rejection(
                    trader_address=trader_address, market_id=str(market_id),
                    rejection_reason="mirror_whale_too_small",
                    rejection_stage="pre_gate",
                    token_id=token_id, side=side, price=price,
                    whale_trade_usd=whale_trade_usd,
                    signal_metadata={"min_usd": _min_whale_usd},
                )
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
                await self._log_rejection(
                    trader_address=trader_address, market_id=str(market_id),
                    rejection_reason="mirror_market_blocklist",
                    rejection_stage="pre_gate",
                    token_id=token_id, side=side, price=price,
                    whale_trade_usd=whale_trade_usd,
                    signal_metadata={"source": "in_memory_market_blocklist",
                                     "blocklist_size": len(self._market_blocklist)},
                )
                return False

            # Per-market cooldown — prevent re-entry on same signal
            # S172 D8: Increased to 24h (86400s) from 30min (1800s). Re-entry disabled
            # until expectancy is measured (see Phase 7G analytical review).
            _cooldown_secs = int(getattr(settings, "MIRROR_MARKET_COOLDOWN_SECONDS", 86400))
            if _cooldown_secs > 0:
                _cd_exp = self._market_cooldown.get(market_id, 0)
                if _time.monotonic() < _cd_exp:
                    await self._log_rejection(
                        trader_address=trader_address, market_id=str(market_id),
                        rejection_reason="mirror_market_cooldown",
                        rejection_stage="pre_gate",
                        token_id=token_id, side=side, price=price,
                        whale_trade_usd=whale_trade_usd,
                        signal_metadata={"cooldown_secs": _cooldown_secs,
                                         "remaining_s": max(0.0, _cd_exp - _time.monotonic())},
                    )
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
                await self._log_rejection(
                    trader_address=trader_address, market_id=str(market_id),
                    rejection_reason="mirror_market_maker_blocked",
                    rejection_stage="pre_gate",
                    token_id=token_id, side=side, price=price,
                    whale_trade_usd=whale_trade_usd,
                    signal_metadata={"mm_window_s": _mm_window,
                                     "prior_opposite_side": _opposite_mm,
                                     "seconds_since_opposite": _time.monotonic() - _opp_ts},
                )
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
                    await self._log_rejection(
                        trader_address=trader_address, market_id=str(market_id),
                        rejection_reason="mirror_opposing_side_blocked",
                        rejection_stage="pre_gate",
                        token_id=token_id, side=side, price=price,
                        whale_trade_usd=whale_trade_usd,
                        signal_metadata={"opposite_side": _opposite,
                                         "source": "in_memory_open_positions"},
                    )
                    return False
            # Check 2: historical entries (catches resolved positions missed after restart)
            if (market_id, _opposite) in self._entered_market_sides:
                logger.info(
                    "mirror_opposing_side_blocked_historical market=%s side=%s prior_entry=%s",
                    str(market_id)[:16], side, _opposite,
                )
                await self._log_rejection(
                    trader_address=trader_address, market_id=str(market_id),
                    rejection_reason="mirror_opposing_side_blocked_historical",
                    rejection_stage="pre_gate",
                    token_id=token_id, side=side, price=price,
                    whale_trade_usd=whale_trade_usd,
                    signal_metadata={"opposite_side": _opposite,
                                     "source": "entered_market_sides"},
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
                    await self._log_rejection(
                        trader_address=trader_address, market_id=str(market_id),
                        rejection_reason="mirror_same_side_blocked",
                        rejection_stage="pre_gate",
                        token_id=token_id, side=side, price=price,
                        whale_trade_usd=whale_trade_usd,
                        signal_metadata={"consensus_count": self._whale_consensus[_cons_key],
                                         "note": "dedup_signal_not_true_reject"},
                    )
                    return False

        # ── S137 C16: Tier 2 — category-dependent gates (DB/cache call) ──

        # Resolve category early (needed for M1 category cap + M3 domain tracking)
        if not category:
            try:
                _meta_cat, _ = await self._get_market_meta(str(market_id))
                category = _meta_cat or ""
            except Exception as e:
                logger.warning("mirror_category_meta_fail: %s", e)
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
                        await self._log_rejection(
                            trader_address=trader_address, market_id=str(market_id),
                            rejection_reason="mirror_category_blocked",
                            rejection_stage="pre_gate",
                            token_id=token_id, side=side, price=price,
                            whale_trade_usd=whale_trade_usd,
                            signal_metadata={"category": category,
                                             "blocklist_match": _bl},
                        )
                        return False

        # S137→S153: Category expertise — weighted factor (was binary BLOCK < 40%).
        # When cat_n < 10: factor=1.0 (no penalty — great overall WR + new to category is fine).
        # When cat_n >= 10: ramp 30%→0.2, 40%→0.6, 55%+→1.0.
        pass  # _wf_cat_expertise computed below in split scoring block

        # S85 FIX: Enforce position cap for ALL paths (consensus + RTDS).
        # Previously only consensus checked _can_open_position(); RTDS bypassed it,
        # allowing 686 positions past the 200 cap.
        if not _is_sell and not self._can_open_position(price, category=category):
            await self._log_rejection(
                trader_address=trader_address, market_id=str(market_id),
                rejection_reason="mirror_can_open_position_false",
                rejection_stage="pre_gate",
                token_id=token_id, side=side, price=price,
                whale_trade_usd=whale_trade_usd,
                signal_metadata={"category": category,
                                 "open_positions": len(self._open_positions)},
            )
            return False

        if _is_sell:
            pos_key = f"{market_id}:{token_id}"
            # S168 Phase 6: Record SELL signal for active trader-exit check in exit loop.
            # Even if we close the position immediately below via RTDS SELL, record the
            # signal so the exit loop doesn't redundantly attempt a second close.
            if pos_key in self._open_positions and trader_address:
                if pos_key not in self._trader_sell_signals:
                    self._trader_sell_signals[pos_key] = set()
                self._trader_sell_signals[pos_key].add(trader_address)
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
            # S228 Bug 11C: pre-flight CTF balance check for live mode SELLs.
            # RTDS-driven exits on a position the deposit wallet doesn't actually
            # hold would burn 3 OrderGateway retries on "balance: 0" rejections.
            if not await self._live_sell_balance_guard(
                market_id=market_id, token_id=token_id,
                size=_exit_size, side=side,
            ):
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
                # S156: Use actual filled quantity for partial fill correctness
                _filled_sell = float(order.get("filled", _exit_size))
                _exit_usd = _filled_sell * float(_pos.get("entry_price", price))
                # S156: Decrement under lock
                async with self._exposure_lock:
                    self._daily_exposure = max(0.0, self._daily_exposure - _exit_usd)
                    # M1: Decrement category exposure on exit
                    if category:
                        self._category_exposure[category] = max(
                            0.0, self._category_exposure.get(category, 0.0) - _exit_usd
                        )
                if _filled_sell >= _exit_size - 0.01:
                    del self._open_positions[pos_key]
                else:
                    _rem = max(0.0, _pos["size"] - _filled_sell)
                    self._open_positions[pos_key]["size"] = _rem
                    logger.warning("mirror_partial_exit_residual",
                                   market=str(market_id)[:20],
                                   filled=round(_filled_sell, 2),
                                   remaining=round(_rem, 2),
                                   exit_reason="rtds_sell")
                # S135/S158: Mark position closed on full fill, update size on partial fill.
                # Was unconditional close — partial fills lost residual shares on restart.
                try:
                    from sqlalchemy import text as _st
                    async with self.base_engine.db.get_session() as _cs:
                        if _filled_sell >= _exit_size - 0.01:
                            await _cs.execute(_st(
                                "UPDATE positions SET status = 'closed' "
                                "WHERE market_id = :mid AND token_id = :tid "
                                "  AND COALESCE(source_bot, bot_id) = 'MirrorBot' "
                                "  AND status = 'open'"
                            ), {"mid": market_id, "tid": token_id})
                        else:
                            await _cs.execute(_st(
                                "UPDATE positions SET size = :sz "
                                "WHERE market_id = :mid AND token_id = :tid "
                                "  AND COALESCE(source_bot, bot_id) = 'MirrorBot' "
                                "  AND status = 'open'"
                            ), {"mid": market_id, "tid": token_id, "sz": _rem})
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
            # S161: Three-tier fallback for markets not in scan-loop index:
            # 1. Gamma API by numeric ID (original — works when market_id is numeric)
            # 2. Local DB by condition_id (NEW — handles 0x hashes from RTDS)
            # 3. 2s retry on DB (transient failure recovery)

            # Tier 1: Gamma API (existing — fails with 422 on condition_id)
            try:
                _fetched = await self.base_engine.get_market(str(market_id))
                if _fetched and isinstance(_fetched, dict):
                    self.base_engine.update_market_index([_fetched])
                    _market_data = _fetched
                    logger.debug("mirror_market_fallback_fetch", market=str(market_id)[:16])
            except Exception:
                pass

            # Tier 2: Local DB by condition_id (S161 — RTDS sends 0x hashes, Gamma 422s on them)
            if not _market_data:
                try:
                    _db = getattr(self.base_engine, "db", None)
                    if _db and getattr(_db, "session_factory", None):
                        from sqlalchemy import text as _text
                        async with _db.get_session() as _sess:
                            _row = await _sess.execute(
                                _text(
                                    "SELECT id, condition_id, question, category, "
                                    "yes_price, no_price, yes_token_id, no_token_id, "
                                    "active, end_date_iso, volume "
                                    "FROM markets WHERE condition_id = :cid LIMIT 1"
                                ),
                                {"cid": str(market_id)},
                            )
                            _r = _row.fetchone()
                            if _r:
                                _market_data = dict(_r._mapping)
                                self.base_engine.update_market_index([_market_data])
                                logger.debug("mirror_market_db_fallback", market=str(market_id)[:16])
                except Exception as _e:
                    logger.debug("mirror_market_db_fallback_err", market=str(market_id)[:16],
                                 err=str(_e)[:80])

            # Tier 3: 2s retry on DB (transient connection/pool failure)
            if not _market_data:
                import asyncio as _aio
                await _aio.sleep(2)
                try:
                    _db = getattr(self.base_engine, "db", None)
                    if _db and getattr(_db, "session_factory", None):
                        from sqlalchemy import text as _text
                        async with _db.get_session() as _sess:
                            _row = await _sess.execute(
                                _text(
                                    "SELECT id, condition_id, question, category, "
                                    "yes_price, no_price, yes_token_id, no_token_id, "
                                    "active, end_date_iso, volume "
                                    "FROM markets WHERE condition_id = :cid LIMIT 1"
                                ),
                                {"cid": str(market_id)},
                            )
                            _r = _row.fetchone()
                            if _r:
                                _market_data = dict(_r._mapping)
                                self.base_engine.update_market_index([_market_data])
                                logger.info("mirror_market_data_retry_success",
                                            market=str(market_id)[:16])
                except Exception:
                    pass

        # S160 BUG-4: Hard-block if market data unavailable after all fallbacks.
        # Without market data, price stays at whale's historical fill (possibly hours old).
        # Better to skip than enter at a stale price that produces fake P&L.
        if not _market_data:
            logger.info("mirror_market_data_retry_fail", market=str(market_id)[:16],
                        stale_price=round(price, 4))
            return False
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

            # S168 Phase 7: Market quality scoring — pre-entry composite filter.
            # Geometric mean of volume, spread, TTR, and category IE.
            _mq_threshold = float(getattr(settings, "MIRROR_MARKET_QUALITY_THRESHOLD", 0.30))
            if _mq_threshold > 0:
                _mq_score = self._market_quality_score(
                    volume_24h=_vol_check,
                    spread=_spread,
                    hours_to_resolution=_near_res_h,
                    category=category,
                )
                if _mq_score < _mq_threshold:
                    logger.info("mirror_market_quality_blocked",
                                score=round(_mq_score, 3), threshold=_mq_threshold,
                                volume=round(_vol_check, 0),
                                spread=round(_spread, 3) if _spread else None,
                                ttr_h=round(_near_res_h, 1) if _near_res_h else None,
                                market=str(market_id)[:16])
                    return False

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
                # S164: Apply floor to FINAL reliability_mult, not just _sample_ramp.
                # Under Beta(6,10) the LR=0.6 erodes the ramp floor (0.20×0.6=0.12),
                # pushing all cold-start trades below the $25 dust gate.  Flooring
                # the product instead of the ramp restores S157 intent regardless of
                # the prior chosen for unknown traders.
                reliability_mult *= _sample_ramp
                _cs_floor = float(getattr(settings, "MIRROR_COLD_START_SIZE_FLOOR", 0.20))
                if _eq_n < 5 and reliability_mult < _cs_floor:
                    reliability_mult = _cs_floor
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

        # Factor 2: Price-implied edge — ZEROED.
        # S132 DATA: Contrarian boost was anti-signal (32.9% WR, -$84K).
        # Neutral trades (46.6% WR) outperform both contrarian and consensus.
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
                        await self._log_rejection(
                            trader_address=trader_address, market_id=str(market_id),
                            rejection_reason="mirror_trader_wr_hard_block",
                            rejection_stage="gate",
                            token_id=token_id, side=side, price=price,
                            whale_trade_usd=whale_trade_usd,
                            signal_metadata={"trader_wr": _bl_wr, "trader_n": _bl_total,
                                             "min_resolved": _bl_min_resolved},
                        )
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
                    await self._log_rejection(
                        trader_address=trader_address, market_id=str(market_id),
                        rejection_reason="mirror_spread_hard_block",
                        rejection_stage="gate",
                        token_id=token_id, side=side, price=price,
                        whale_trade_usd=whale_trade_usd,
                        signal_metadata={"spread": _spread, "spread_hard_block_threshold": 0.25},
                    )
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
                        await self._log_rejection(
                            trader_address=trader_address, market_id=str(market_id),
                            rejection_reason="mirror_no_fav_hard_block",
                            rejection_stage="gate",
                            token_id=token_id, side=side, price=price,
                            whale_trade_usd=whale_trade_usd,
                            signal_metadata={"no_price": _nfp,
                                             "no_fav_threshold": 0.90},
                        )
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
                # S156: Validate all factors are real floats in [0,1] before geometric mean.
                # Log raw values if any factor has bypassed bounds (NaN, complex, negative).
                _sanitized = []
                for _fi, _fv in enumerate(_active_factors):
                    if not isinstance(_fv, (int, float)) or _fv != _fv:  # complex, NaN, or non-numeric
                        logger.warning("mirror_factor_anomaly", index=_fi, value=repr(_fv),
                                       all_factors=[repr(f) for f in _active_factors],
                                       trader=trader_address[:10])
                        _sanitized.append(0.5)  # neutral fallback
                    else:
                        _sanitized.append(max(0.0, min(1.0, float(_fv))))
                try:
                    _geo = _math.prod(_sanitized) ** (1.0 / len(_sanitized))
                except (TypeError, ValueError, OverflowError) as _geo_err:
                    logger.warning("mirror_geo_mean_failed", error=str(_geo_err),
                                   factors=[repr(f) for f in _active_factors],
                                   trader=trader_address[:10])
                    _geo = 0.0
            else:
                _geo = 1.0
            _factor_w = float(getattr(settings, "MIRROR_GATE_FACTOR_WEIGHT", 0.30))
            gate_score = _gate_base * ((1.0 - _factor_w) + _factor_w * _geo)

            # --- Kelly Probability: YES=price-anchored, NO=WR-anchored (S168) ---
            _copy_tier = 2
            if self._watchlist:
                _copy_tier = self._watchlist.get_copy_tier(trader_address)
            if _eq_n >= 5:
                _trader_edge = max(0.0, _base - 0.50) * min(1.0, (_eq_n - 5) / 45.0)
            else:
                # S154: Cold-start — derive edge from same efficiency prior the gate trusts.
                _eff_edge = max(0.0, _eff_prior - 0.50)
                _trader_edge = max(0.01, _eff_edge * _decay_w * 0.60)
            _kelly_copy_bonus = 0.02 if (_copy_tier == 1 and _copy_adj > 0) else 0.0
            _max_edge = float(getattr(settings, "MIRROR_MAX_KELLY_EDGE", 0.05))

            if _side_upper == "NO":
                # S168: NO-side uses trader's WR (_base) as win probability, not price + edge.
                # The price-anchored formula structurally suppresses NO edge because cheap NO
                # tokens (0.30-0.40) produce kelly_prob ~ 0.42-0.45, edge ~ 0.02-0.05, which
                # fails the 0.05 gate. Same class of bug as EsportsBot (commit f2b92bf, S158).
                _no_max_edge = float(getattr(settings, "MIRROR_NO_MAX_KELLY_EDGE", 0.10))
                if _eq_n >= 5:
                    # Warm: _base IS the trader's per-side NO WR (shrunk toward 0.50)
                    kelly_prob = max(price + 0.005, min(price + _no_max_edge, _base + _kelly_copy_bonus))
                else:
                    # Cold-start: decay-attenuated ramp from price toward efficiency prior.
                    # Matches YES cold-start design — prevents full-sizing on zero data.
                    _cold_no_edge = max(0.0, _eff_prior - price) * _decay_w * 0.60
                    kelly_prob = max(price + 0.005, min(price + _no_max_edge,
                        price + _cold_no_edge + _kelly_copy_bonus))
            else:
                # YES: price-anchored (existing formula, completely unchanged)
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
                logger.warning("MirrorBot: signal enhancements failed (using raw confidence): %s", e)

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
                except Exception as e:
                    logger.warning("mirror_calibration_category_fail: %s", e)
            # S121: Shadow ledger — always compute calibrated score (does not affect trade)
            _conf_cal_shadow = self._calibration_stack.shadow_calibrate(
                confidence, category=_cat, ttr_days=_ttr_days, side=_side_upper,
            )
            # Live calibration — only modifies confidence when MIRROR_USE_CALIBRATION=true
            # S168: NO-side bypasses calibration (Phase 1 OOS: NO T=1.0, no improvement)
            _raw_conf = confidence
            confidence = self._calibration_stack.calibrate_confidence(
                confidence, category=_cat, ttr_days=_ttr_days, side=_side_upper,
            )
            if abs(_raw_conf - confidence) > 0.01:
                logger.info("mirror_calibrated", raw=round(_raw_conf, 3), cal=round(confidence, 3))


        # S177: Log prediction BEFORE gate check so gate-blocked signals still
        # accumulate prediction_log data.  Without this, MB is in a deadlock:
        # gate-blocked → no prediction_log → cat_n stays 0 → gate scores can't
        # improve → permanently gate-blocked.  Moved from line 3062 (post-gate).
        _db = getattr(self.base_engine, "db", None)
        if _db and not _is_sell:
            try:
                await _db.insert_prediction_log(
                    market_id=market_id,
                    predicted_prob=kelly_prob,
                    market_price=price,
                    model_name=f"mirror_split_{source}",
                    bot_name="MirrorBot",
                    confidence=gate_score,
                )
            except Exception as _pl_err:
                logger.debug("mirror_prediction_log_failed", error=str(_pl_err))

        # S103 Bug Fix: Enforce min_confidence AFTER all adjustments (domain drift,
        # calibration). Without this gate, self.min_confidence was dead code — trades
        # executed at 38% confidence despite configured threshold.
        # DATA: <40% = 9% WR (-$157/pos), 40-50% = 18% WR (-$53/pos), 50%+ = profitable.
        # S153: Split scoring gate check
        if _split_live:
            # S158: NO-side gets lower gate threshold — 71.4% WR vs 45.4% YES.
            # Existing NO gates (min_edge, block_floor, dampener) already filter heavily.
            if _side_upper == "NO":
                _gate_threshold = float(getattr(settings, "MIRROR_GATE_THRESHOLD_NO", 0.50))
            else:
                _gate_threshold = float(getattr(settings, "MIRROR_GATE_THRESHOLD", 0.52))
            if gate_score < _gate_threshold:
                logger.info("mirror_gate_blocked",
                            gate_score=round(gate_score, 3),
                            threshold=_gate_threshold,
                            kelly_prob=round(kelly_prob, 3),
                            trader=str(trader_address)[:16],
                            market=str(market_id)[:16])
                await self._log_rejection(
                    trader_address=trader_address, market_id=str(market_id),
                    rejection_reason="mirror_gate_blocked",
                    rejection_stage="gate",
                    token_id=token_id, side=side, price=price,
                    whale_trade_usd=whale_trade_usd,
                    signal_metadata={"gate_score": gate_score,
                                     "threshold": _gate_threshold,
                                     "kelly_prob": kelly_prob,
                                     "category": category,
                                     "scoring_mode": "split"},
                )
                return False
        else:
            if confidence < self.min_confidence:
                # S148: Shadow-watch 0.50–0.55 band
                _low_conf_reason = "mirror_shadow_conf_band" if confidence >= 0.50 else "mirror_low_confidence"
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
                await self._log_rejection(
                    trader_address=trader_address, market_id=str(market_id),
                    rejection_reason=_low_conf_reason,
                    rejection_stage="gate",
                    token_id=token_id, side=side, price=price,
                    whale_trade_usd=whale_trade_usd,
                    signal_metadata={"confidence": confidence,
                                     "min_required": self.min_confidence,
                                     "gate_score": gate_score,
                                     "category": category,
                                     "scoring_mode": "legacy"},
                )
                return False

        # S172 D8: Flat sizing — Kelly fraction is negative (-0.068), so Kelly-based
        # sizing produces $0 or negative bets. Use flat USD amount until accuracy
        # exceeds 52% over 200+ trades with p<0.05, then re-introduce quarter-Kelly.
        # Default $30 — clears MIRROR_MIN_TRADE_USD ($25 dust gate) after risk
        # budget deductions (typical ~0.88x → $26.40 > $25).
        # Plan originally said $1-2 but that's below dust gate and spread-dominated.
        # S218: Flat-size clamped to bankroll.max_bet_usd. Symmetric to S217 dust
        # gate (max(floor, min(ceiling, cap))) — flat is the upper bound, cap is
        # the policy ceiling. Without this, MIRROR_FLAT_POSITION_SIZE_USD=$30
        # bypasses max_bet_usd=$1 (shadow-live cap) and orders go through at
        # ~$4 instead of $1 after downstream dampeners. bankroll=None at this
        # site is a structural failure (production guarantees bankroll exists).
        if self.bankroll is None:
            raise RuntimeError("mirror_sizing_bankroll_uninitialized")
        _cap_usd = float(self.bankroll.max_bet_usd)
        _flat_usd_cfg = float(getattr(settings, "MIRROR_FLAT_POSITION_SIZE_USD", 30.0))
        _flat_usd = min(_flat_usd_cfg, _cap_usd)
        if _flat_usd > 0 and price > 0:
            size = _flat_usd / price
        else:
            size = 0.0
        # Log Kelly-equivalent for comparison (don't use it for sizing)
        _kelly_size = await self.calculate_bot_position_size(
            confidence=confidence,
            price=price,
            conformal_interval=None,
            category=category,
        )
        logger.debug("mirror_flat_vs_kelly",
                      flat_usd=round(_flat_usd, 2),
                      flat_usd_cfg=round(_flat_usd_cfg, 2),
                      cap_usd=round(_cap_usd, 2),
                      flat_shares=round(size, 4),
                      kelly_shares=round(_kelly_size, 4),
                      price=round(price, 4))

        # S146: Copy-P&L tiered sizing — compute tier (used by risk budget below).
        _copy_tier = 2  # default: learning mode (also computed in S153 block above)
        if self._watchlist:
            _copy_tier = self._watchlist.get_copy_tier(trader_address)

        # S142: Baker-McHale edge-uncertainty shrinkage — compute k (used by risk budget).
        _bm_k = 1.0
        if _eq_n >= 3:
            _bm_edge = max(0.0, confidence - price)
            _bm_edge_sq = _bm_edge * _bm_edge
            _bm_var = confidence * (1.0 - confidence) / _eq_n  # binomial SE²
            _bm_denom = _bm_edge_sq + _bm_var
            if _bm_denom > 0:
                _bm_k = _bm_edge_sq / _bm_denom

        # S168: Unified risk budget — replaces 4 independent multiplicative penalties.
        # Old: size *= reliability × tier × bm_k × adaptive. Compounded to dust.
        # New: additive deductions with floor. Each factor's weight from Phase 1 data:
        #   rel_mult: Q1=52.9% WR (moderate signal, 77% of trades) → weight 0.30
        #   copy_tier: all tiers ~52-53% WR (near-zero differentiation) → weight 0.10/0.15
        #   baker_mchale: function of confidence/n (moderate) → weight 0.20
        #   adaptive_safety: drawdown protection (safety, not prediction) → weight 0.15
        _risk_floor = float(getattr(settings, "MIRROR_RISK_BUDGET_FLOOR", 0.15))
        _deduction = 0.0
        _deduction += (1.0 - reliability_mult) * 0.30       # max 0.30 deduction
        if _copy_tier == 2:
            _deduction += 0.10                                # thin data → mild penalty
        elif _copy_tier == 3:
            _deduction += 0.15                                # copy-unprofitable → moderate
        _deduction += (1.0 - _bm_k) * 0.20                  # max 0.20 deduction

        _bet_mult = 1.0
        if (self._adaptive_safety
                and getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False)
                and self._adaptive_safety._fitted):
            _bet_mult = self._adaptive_safety.get_adjusted_bet_size_mult()
        if _bet_mult < 1.0:
            _deduction += (1.0 - _bet_mult) * 0.15          # max 0.15 deduction

        _risk_mult = max(_risk_floor, 1.0 - _deduction)
        size *= _risk_mult
        logger.info("mirror_risk_budget",
                    risk_mult=round(_risk_mult, 3),
                    deduction=round(_deduction, 3),
                    rel=round(reliability_mult, 3),
                    tier=_copy_tier,
                    bm_k=round(_bm_k, 3),
                    adaptive=round(_bet_mult, 3),
                    floor=_risk_floor,
                    trader=trader_address[:10],
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
                    await self._log_rejection(
                        trader_address=trader_address, market_id=str(market_id),
                        rejection_reason="mirror_no_dynamic_blocked",
                        rejection_stage="post_gate",
                        token_id=token_id, side=side, price=price,
                        whale_trade_usd=whale_trade_usd,
                        signal_metadata={"no_price": price,
                                         "no_block_floor": _no_block_floor},
                    )
                    return False
                # S146: Minimum edge gate — NO must show positive edge to enter.
                _no_min_edge = float(getattr(settings, "MIRROR_NO_MIN_EDGE", 0.05))
                _no_edge = confidence - price
                if _no_edge < _no_min_edge:
                    logger.info("mirror_no_edge_rejected", no_price=round(price, 3),
                                confidence=round(confidence, 3),
                                edge=round(_no_edge, 3), min_edge=_no_min_edge,
                                market=str(market_id)[:16])
                    await self._log_rejection(
                        trader_address=trader_address, market_id=str(market_id),
                        rejection_reason="mirror_no_edge_rejected",
                        rejection_stage="post_gate",
                        token_id=token_id, side=side, price=price,
                        whale_trade_usd=whale_trade_usd,
                        signal_metadata={"no_price": price,
                                         "confidence": confidence,
                                         "edge": _no_edge,
                                         "min_edge": _no_min_edge},
                    )
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

        # S168: Adaptive bet-size multiplier moved into unified risk budget above.

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
            await self._log_rejection(
                trader_address=trader_address, market_id=str(market_id),
                rejection_reason="mirror_size_zero_after_limits",
                rejection_stage="post_gate",
                token_id=token_id, side=side, price=price,
                whale_trade_usd=whale_trade_usd,
                signal_metadata={"max_per_market_usd": max_per_market_usd,
                                 "remaining_daily_usd": remaining_daily_usd,
                                 "daily_exposure": self._daily_exposure},
            )
            return False

        # S218: Final-clamp invariant. size*price MUST NOT exceed bankroll.max_bet_usd
        # regardless of which sizing branch produced the value. Defense-in-depth
        # paired with the S218 source-clamp at the flat-sizing site above. Under
        # normal operation this is a silent no-op — the source clamp + monotonic-
        # non-increasing dampener chain guarantees size*price ≤ _cap_usd. If this
        # log ever fires it means a new sizing path was added that bypasses the
        # source clamp; treat the log as a regression signal, not a normal event.
        _size_usd = size * price
        if _size_usd > _cap_usd:
            size = _cap_usd / price
            logger.warning("mirror_size_capped_at_invariant",
                           pre_cap_usd=round(_size_usd, 2),
                           cap_usd=round(_cap_usd, 2),
                           market_id=str(market_id)[:16])

        # S217: Dust floor coupled to bankroll cap to prevent cap/floor drift.
        # Historical bug: max_bet_usd=$1 with hardcoded $25 floor silent-throttled
        # MB for 6+ days. Fix expresses the constraint relationship in code:
        # floor = max(abs_floor, min(ceiling, cap)).
        #   - abs_floor protects against degenerate cap=$0 (default $0.10)
        #   - ceiling = fee-economics floor for full-cap trades (default $25)
        #   - cap = self.bankroll.max_bet_usd (operator policy)
        # bankroll=None at this point is a structural failure — raise, don't default.
        _abs_floor = float(getattr(settings, "MIRROR_DUST_ABS_FLOOR_USD", 0.10))
        _ceiling = float(getattr(settings, "MIRROR_DUST_CEILING_USD", 25.0))
        if self.bankroll is None:
            raise RuntimeError("mirror_dust_gate_bankroll_uninitialized")
        _cap = float(self.bankroll.max_bet_usd)
        _min_trade_usd = max(_abs_floor, min(_ceiling, _cap))
        _trade_value_usd = size * price
        if _trade_value_usd < _min_trade_usd:
            # S227: Clamp size UP to floor instead of rejecting. Operator spec —
            # dust gate's only legitimate purpose is ensuring the order meets
            # CLOB minimum; downstream dampener chain (NO-side ×0.75, favorable,
            # risk_budget) shrinks max_bet=$1 trades below the $1 floor, making
            # entries mathematically blocked. Rejection here was the wrong
            # semantic. Order then flows to place_order → CLOB; Polymarket
            # acceptance becomes the authoritative halt. Kelly intent preserved
            # via mirror_force_min_clamped shadow event (original_size +
            # original_trade_usd captures what dampened sizing would have done).
            _original_size = size
            _original_trade_usd = _trade_value_usd
            if price > 0:
                size = _min_trade_usd / price
                _trade_value_usd = size * price
            logger.info("mirror_force_min_clamped",
                        original_size=round(_original_size, 4),
                        original_trade_usd=round(_original_trade_usd, 2),
                        clamped_size=round(size, 4),
                        clamped_trade_usd=round(_trade_value_usd, 2),
                        floor_usd=_min_trade_usd,
                        price=round(price, 4),
                        market_id=str(market_id)[:16])

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
            "whale_fill_price": round(_old_price, 4),  # S158: whale's RTDS fill price (before market override)
            "bot_entry_price": round(price, 4),         # S158: bot's actual entry (current market midpoint)
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
            "gate_score": round(float(gate_score), 3),
            "kelly_prob": round(float(kelly_prob), 3),
            "gate_decay_w": round(float(_decay_w), 3),
            "eff_prior": round(float(_eff_prior), 3),
            "geo_mean": round(float(_geo), 3),
        }
        # S168: ML selector removed (was shadow-only, MIRROR_USE_ML_SELECTOR=false)

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

        # S156: Reserve exposure under lock BEFORE place_order() to close the
        # await gap between _can_open_position() (reads) and increment (writes).
        # Two concurrent RTDS callbacks can both pass the cap check and both place
        # orders, overshooting the daily cap by N * trade_size.
        _trade_usd = size * price

        # S228 Bug 11B: BUY-side capital guard for live mode. Without this, a
        # BUY signal that exceeds deposit-wallet pUSD balance reaches CLOB and
        # wastes 3 OrderGateway retries on the same "balance: 0" rejection.
        # self.bankroll.capital is the wallet-refreshed pUSD balance (S226
        # Bug 6) — O(1) cached read, no extra RPC per signal. Defense-in-depth
        # to Bug 11A's restore-filter fix. Skip in paper mode (no real capital
        # movement) and when bankroll is missing. Placed BEFORE the exposure
        # lock to avoid serializing concurrent whale-signal handlers on this
        # cheap-but-not-free check. Mode detection via helper per S83 paper-
        # is-production rule (bot source avoids the direct mode name).
        if not is_paper_trading_active():
            _live_capital = getattr(self.bankroll, "capital", None) if self.bankroll else None
            if _live_capital is not None and _live_capital > 0 and _trade_usd > _live_capital:
                logger.warning(
                    "mirror_buy_capital_guard_reject",
                    trader_address=trader_address,
                    market_id=str(market_id),
                    side=side,
                    trade_usd=round(_trade_usd, 2),
                    available_pusd=round(float(_live_capital), 2),
                    note="deposit wallet pUSD balance insufficient for BUY",
                )
                await self._log_rejection(
                    trader_address=trader_address, market_id=str(market_id),
                    rejection_reason="mirror_buy_capital_guard_reject",
                    rejection_stage="post_gate",
                    token_id=token_id, side=side, price=price,
                    whale_trade_usd=whale_trade_usd,
                    signal_metadata={"trade_usd": _trade_usd,
                                     "available_pusd": float(_live_capital)},
                )
                return False

        async with self._exposure_lock:
            # Re-verify daily cap under lock (stale check at L1337 is pre-lock)
            if self.bankroll:
                _lock_max = self.bankroll.max_daily_usd
            else:
                _lock_max = float(getattr(settings, "TOTAL_CAPITAL", 10000.0)) * getattr(
                    settings, "MIRROR_MAX_DAILY_EXPOSURE_PCT", self.MAX_DAILY_EXPOSURE_PCT)
            # S162: Apply adaptive safety multiplier (matching sizing path at ~L2615)
            if (self._adaptive_safety
                    and getattr(settings, "MIRROR_ADAPTIVE_SAFETY", False)
                    and self._adaptive_safety._fitted):
                _lock_max *= self._adaptive_safety.get_adjusted_daily_cap_mult()
            if self._daily_exposure + _trade_usd > _lock_max:
                logger.info("mirror_exposure_lock_reject: $%.0f + $%.0f > cap $%.0f",
                            self._daily_exposure, _trade_usd, _lock_max)
                await self._log_rejection(
                    trader_address=trader_address, market_id=str(market_id),
                    rejection_reason="mirror_exposure_lock_reject",
                    rejection_stage="post_gate",
                    token_id=token_id, side=side, price=price,
                    whale_trade_usd=whale_trade_usd,
                    signal_metadata={"daily_exposure": self._daily_exposure,
                                     "trade_usd": _trade_usd,
                                     "lock_max": _lock_max},
                )
                return False
            # S162: Category cap enforcement (S157 planned but never implemented)
            if category:
                _cat_max_raw = getattr(settings, "MIRROR_MAX_CATEGORY_EXPOSURE_USD", None)
                _cat_max = float(_cat_max_raw) if isinstance(_cat_max_raw, (int, float, str)) else 40000.0
                _cat_cur = self._category_exposure.get(category, 0.0)
                if _cat_cur + _trade_usd > _cat_max:
                    logger.info("mirror_category_cap_reject: %s $%.0f + $%.0f > cap $%.0f",
                                category, _cat_cur, _trade_usd, _cat_max)
                    await self._log_rejection(
                        trader_address=trader_address, market_id=str(market_id),
                        rejection_reason="mirror_category_cap_reject",
                        rejection_stage="post_gate",
                        token_id=token_id, side=side, price=price,
                        whale_trade_usd=whale_trade_usd,
                        signal_metadata={"category": category,
                                         "category_exposure": _cat_cur,
                                         "trade_usd": _trade_usd,
                                         "category_cap": _cat_max},
                    )
                    return False
            # Reserve atomically
            self._daily_exposure += _trade_usd
            if category:
                self._category_exposure[category] = self._category_exposure.get(category, 0.0) + _trade_usd

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
            # Exposure already reserved above — no double-increment
            # S166: Correct exposure for partial fills.  Entry reserved
            # size * price, but only _filled_size was actually filled.
            # Without this, the unfilled portion stays inflated in
            # _daily_exposure and _category_exposure until daily reset.
            _filled_size = float(order.get("filled", size))
            _unfilled_usd = (size - _filled_size) * price
            if _unfilled_usd > 0.01:
                async with self._exposure_lock:
                    self._daily_exposure = max(0.0, self._daily_exposure - _unfilled_usd)
                    if category:
                        self._category_exposure[category] = max(
                            0.0, self._category_exposure.get(category, 0.0) - _unfilled_usd)

            # S91: Set per-market cooldown to prevent re-entry on same signal
            _cd_secs = int(getattr(settings, "MIRROR_MARKET_COOLDOWN_SECONDS", 1800))
            if _cd_secs > 0:
                self._market_cooldown[market_id] = _time.monotonic() + _cd_secs
                # S168 Phase 8: Persist to DB
                asyncio.ensure_future(self._save_cooldown(str(market_id), float(_cd_secs)))

            # Update position tracking with ACTUAL filled size (not requested size)
            # S156: Use order["filled"] to prevent in-memory vs paper engine size divergence.
            # Partial fills mean order["filled"] < requested size. Using requested size
            # inflates _open_positions, causing "Insufficient position" errors on exit.
            # _filled_size already computed above (S166 partial fill correction)
            pos_key = f"{market_id}:{token_id}"
            if pos_key in self._open_positions:
                self._open_positions[pos_key]["size"] += _filled_size
            else:
                self._open_positions[pos_key] = {
                    "side": side,
                    "size": _filled_size,
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

        # S156: Revert reserved exposure on order failure (idempotent dedup also reverts)
        async with self._exposure_lock:
            self._daily_exposure = max(0.0, self._daily_exposure - _trade_usd)
            if category:
                self._category_exposure[category] = max(
                    0.0, self._category_exposure.get(category, 0.0) - _trade_usd)
        return False