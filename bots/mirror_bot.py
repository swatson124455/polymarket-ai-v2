import asyncio
import math
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from structlog import get_logger

from bots.base_bot import BaseBot
from base_engine.base_engine import BaseEngine
from config.settings import settings

logger = get_logger()


class MirrorBot(BaseBot):
    """
    MirrorBot - Mirrors trades from top N elite traders (TOP_TRADER_COUNT).

    Consensus: aggregates elite trades per (market_id, token_id, side) and mirrors
    only when >= MIRROR_MIN_CONSENSUS elites agree on the same side.

    Features:
    - Trade deduplication with automatic pruning (capped set)
    - Exit mirroring: closes positions when source traders exit
    - Reliability-weighted sizing via EliteReliabilityTracker
    - Daily exposure + concurrent position limits
    - Single client session per scan cycle (no per-trader reconnect)
    """

    # Defaults (overridable via settings)
    MAX_TRACKED_TRADES: int = 10_000
    MAX_CONCURRENT_POSITIONS: int = 50
    MAX_DAILY_EXPOSURE_PCT: float = 0.15

    def __init__(self, base_engine: BaseEngine):
        super().__init__("MirrorBot", base_engine)
        self.elite_traders: List[Dict] = []
        self.mirrored_trades: OrderedDict = OrderedDict()
        self.min_confidence: float = getattr(settings, "MIRROR_MIN_CONFIDENCE", 0.50)
        self._reliability_tracker = None

        # Exit tracking: "market_id:token_id" -> position metadata
        self._open_positions: Dict[str, Dict[str, Any]] = {}

        # Daily exposure tracking
        self._daily_exposure: float = 0.0
        self._daily_reset_date: Optional[str] = None

        # Market metadata cache: market_id -> (category, time_to_res, expiry_monotonic)
        self._market_meta_cache: Dict[str, Tuple[str, str, float]] = {}
        self._MARKET_META_TTL = 300  # 5 minutes

        # Signal enhancement cache: "market_id:side" -> (confidence_multiplier, expiry_monotonic)
        # Avoids calling 3 external services per trade when the same market appears 10-30x per scan.
        self._signal_cache: Dict[str, Tuple[float, float]] = {}
        self._SIGNAL_CACHE_TTL = 60.0  # seconds

        # Per-trader activity cache: addr -> (activity_list, expiry_monotonic)
        # Skips the API call for traders whose activity hasn't changed within the TTL window.
        # At TTL=90s and scan_interval=~15s: ~50/500 traders expire per scan → ~2s vs 14s.
        self._trader_activity_cache: Dict[str, Tuple[List, float]] = {}
        self._TRADER_CACHE_TTL: float = float(getattr(settings, "MIRROR_TRADER_CACHE_TTL", 90))

        # Periodic elite refresh (avoid stale list)
        self._scan_count: int = 0
        self._elite_refresh_every_n_scans: int = 40  # ~30 min at 45s interval

        # Wire elite reliability if available
        try:
            from base_engine.learning.elite_reliability import EliteReliabilityTracker

            if base_engine.db:
                self._reliability_tracker = EliteReliabilityTracker(
                    db=base_engine.db,
                    lookback_days=getattr(settings, "ELITE_LOOKBACK_DAYS", 365),
                )
        except Exception as e:
            logger.debug("elite reliability tracker init failed: %s", e)

        # R5b: Per-category adaptive consensus threshold.
        # Key: category string (e.g. "politics", "crypto") → consensus_min int.
        # Loaded from bot_category_params on first scan.
        # Falls back to MIRROR_MIN_CONSENSUS (global default) for unknown categories.
        self._category_consensus_min: Dict[str, int] = {}
        self._db_consensus_loaded: bool = False

        # C1: YES/NO resolution cache. Key: "market_id:token_id" → "YES"/"NO".
        # Avoids repeated DB queries for the same token across scan cycles.
        self._token_side_cache: Dict[str, str] = {}

        # Startup state restoration flag — run once on first scan.
        self._state_restored: bool = False

        # Deprecation flag: MIRROR_MAX_DAILY_EXPOSURE_PCT fallback warning (log once)
        self._deprecation_warned: bool = False

    def _on_bg_task_done(self, task, name):
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("bg_task_failed", task_name=name, error=str(exc))

    # ── R5b: Adaptive consensus threshold per category ──────────────

    async def _load_consensus_from_db(self) -> None:
        """Load per-category consensus thresholds from bot_category_params on startup."""
        if self._db_consensus_loaded:
            return
        self._db_consensus_loaded = True
        try:
            db = getattr(self.base_engine, "db", None)
            if db is None:
                return
            from sqlalchemy import text
            # M3: Use bot_category_params (not bot_market_params.market_id which is a UUID column)
            sql = text(
                "SELECT category, param_value FROM bot_category_params "
                "WHERE bot_name = :bot AND param_name = 'consensus_min'"
            )
            async with db.get_session() as session:
                result = await session.execute(sql, {"bot": self.bot_name})
                rows = result.fetchall()
            for row in rows:
                self._category_consensus_min[str(row.category)] = max(2, int(row.param_value))
            if rows:
                logger.info("R5b: Loaded %d consensus thresholds from DB for MirrorBot", len(rows))
        except Exception as exc:
            logger.debug("R5b: _load_consensus_from_db failed (non-critical): %s", exc)

    def _get_consensus_min(self, category: str) -> int:
        """Return per-category consensus threshold, falling back to global setting."""
        global_min = getattr(settings, "MIRROR_MIN_CONSENSUS", 2)
        return self._category_consensus_min.get((category or "").lower(), global_min)

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
                # 1. Seed _daily_exposure from today's YES/NO paper_trades entries.
                row = await session.execute(
                    _text(
                        "SELECT COALESCE(SUM(size * price), 0.0) FROM paper_trades "
                        "WHERE bot_name = :bot AND side IN ('YES', 'NO') "
                        "AND created_at >= CURRENT_DATE"
                    ),
                    {"bot": self.bot_name},
                )
                spent_today = float(row.scalar() or 0.0)
                self._daily_exposure = spent_today
                logger.info(
                    "MirrorBot startup: seeded _daily_exposure=%.2f from today's paper_trades",
                    spent_today,
                )

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
                            "traders": set(r.trader_addresses or []),
                            "timestamp": ts,
                        }
                        restored += 1
                logger.info(
                    "MirrorBot startup: restored %d open positions from DB",
                    restored,
                )
        except Exception as exc:
            logger.warning("MirrorBot _restore_state_on_startup failed: %s", exc)

    # ── Main Scan Loop ──────────────────────────────────────────────

    async def scan_and_trade(self):
        """Main scan: refresh elites, check exits, collect consensus trades, execute."""
        self._scan_count += 1

        # Restore _daily_exposure + _open_positions from DB on first scan after restart.
        await self._restore_state_on_startup()

        # R5b: Load per-category consensus thresholds from DB on first scan.
        await self._load_consensus_from_db()

        # Refresh elites on first scan or periodically
        # P3-2: Wrap with 10s timeout — elite refresh DB query can block scan 30s+ under pool pressure
        if not self.elite_traders or self._scan_count % self._elite_refresh_every_n_scans == 0:
            try:
                await asyncio.wait_for(self._update_elite_traders(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("MirrorBot elite refresh timed out (10s) — continuing with stale list")
            except Exception as _elite_err:
                logger.debug("MirrorBot elite refresh failed: %s", _elite_err)

        # Reset daily exposure at UTC day boundary
        self._check_daily_reset()

        # Check for exits from tracked positions
        if self._open_positions and getattr(settings, "MIRROR_EXIT_ENABLED", True):
            await self._check_and_execute_exits()

        # Prune deduplication set if oversized
        self._prune_mirrored_trades()

        # Collect and filter trades by consensus
        consensus_trades = await self._collect_and_aggregate_elite_trades()

        # P5b: Diagnostic — log elite count and consensus trades for visibility
        _buy_ct = sum(1 for t in consensus_trades if str(t.get("side", "")).upper() != "SELL")
        _sell_ct = len(consensus_trades) - _buy_ct
        logger.info(
            "MirrorBot scan: elites=%d consensus_trades=%d (buy=%d sell=%d) open_positions=%d",
            len(self.elite_traders), len(consensus_trades), _buy_ct, _sell_ct,
            len(self._open_positions),
        )

        for trade_info in consensus_trades:
            if not self._can_open_position(trade_info.get("price", 0.5)):
                continue  # _can_open_position() logs the specific reason

            try:
                executed = await self._execute_mirror_trade(
                    market_id=trade_info["market_id"],
                    token_id=trade_info["token_id"],
                    side=trade_info["side"],
                    price=trade_info["price"],
                    confidence=trade_info["confidence"],
                    trader_address=trade_info["trader_address"],
                    category=trade_info.get("category"),
                )
                if executed:
                    self.mirrored_trades[trade_info["trade_id"]] = None
                    self._track_open_position(trade_info)
                    await self._persist_trader_to_position(trade_info)
            except Exception as e:
                logger.warning("Error mirroring consensus trade", error=str(e))
                continue

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
        # Fetch from DB or API
        category, time_to_res = "", ""
        try:
            db = getattr(self.base_engine, "db", None)
            if db and getattr(db, "session_factory", None):
                from sqlalchemy import text as _text
                async with db.get_session() as session:
                    row = await session.execute(
                        _text("SELECT category, end_date_iso FROM markets WHERE id = :mid LIMIT 1"),
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
        except Exception as e:
            logger.debug("Market meta lookup failed for %s: %s", market_id, e)
        self._market_meta_cache[market_id] = (category, time_to_res, now + self._MARKET_META_TTL)
        return category, time_to_res

    # ── Trade Collection & Consensus ────────────────────────────────

    async def _collect_and_aggregate_elite_trades(self) -> List[Dict[str, Any]]:
        """
        Collect recent trades from all elites inside a single client session,
        aggregate by (market_id, token_id, side), return trades with consensus.

        M1: Parallelized elite fetches with asyncio.gather + Semaphore(5)
        to reduce scan time from ~7s (sequential) to ~1-2s (5 concurrent).
        """
        min_consensus = getattr(settings, "MIRROR_MIN_CONSENSUS", 2)
        max_delay = getattr(settings, "MIRROR_MAX_DELAY_MINUTES", 30)
        max_concurrent = getattr(settings, "MIRROR_MAX_CONCURRENT_FETCHES", 20)

        groups: Dict[Tuple[str, str, str], List[Dict]] = defaultdict(list)
        sem = asyncio.Semaphore(max_concurrent)
        import time as _time
        # S48: Waterfall counters for diagnosing 0-trade scans
        _wf_raw = 0        # Raw trades fetched
        _wf_parsed = 0     # Passed parse + freshness
        _wf_conf = 0       # Passed confidence gate
        _wf_rel = 0        # Passed reliability gate (final)
        _wf_api_fail = 0   # API failures
        _wf_cache_hit = 0  # Served from per-trader cache (no API call)

        async def _fetch_one_elite(trader: Dict) -> List[Dict]:
            """Fetch and process trades for a single elite trader.

            Per-trader cache: skips the API call if activity was fetched within
            MIRROR_TRADER_CACHE_TTL seconds (default 90s). Semaphore only gates
            the API call itself — processing runs concurrently outside the lock.
            """
            nonlocal _wf_raw, _wf_parsed, _wf_conf, _wf_rel, _wf_api_fail, _wf_cache_hit
            addr = trader.get("address")
            if not addr:
                return []

            # Check per-trader cache before acquiring semaphore
            _now = _time.monotonic()
            _cached = self._trader_activity_cache.get(addr)
            if _cached and _now < _cached[1]:
                activity = _cached[0]
                _wf_cache_hit += 1
            else:
                async with sem:
                    try:
                        activity = await self.base_engine.client.get_user_activity(
                            user_address=addr,
                            limit=25,
                            offset=0,
                        )
                    except Exception as e:
                        logger.info("get_user_activity failed for %s: %s", addr[:10], e)
                        _wf_api_fail += 1
                        return []
                # Store in cache outside semaphore (no need to hold lock during write)
                self._trader_activity_cache[addr] = (activity or [], _now + self._TRADER_CACHE_TTL)

            # Process activity outside semaphore — DB/cache calls, not rate-limited
            items = []
            _wf_raw += len(activity) if activity else 0
            for trade in activity:
                parsed = self._parse_and_validate_trade(
                    trade, addr, max_delay
                )
                if parsed is None:
                    continue
                _wf_parsed += 1

                _cat, _ttr = await self._get_market_meta(str(parsed["market_id"]))
                # S48 FIX: Use elite trader's own win_rate as confidence, not
                # learning engine's bet-type confidence (which returns ~0.03 with
                # only 242 resolved labels). Elites have >= 55% win rate by definition.
                _elite_wr = float(trader.get("win_rate", 0) or 0)
                confidence = _elite_wr if _elite_wr > 0 else 0.55
                if confidence < self.min_confidence:
                    continue
                _wf_conf += 1

                # C1: Resolve BUY→YES/NO using markets table (place_order requires YES/NO).
                # SELL stays as SELL (exit signal handled separately in _check_and_execute_exits).
                _raw_side = str(parsed.get("side", "BUY")).upper()
                if _raw_side == "SELL":
                    _resolved_side = "SELL"
                else:
                    _resolved_side = await self._get_token_side(
                        str(parsed["market_id"]), str(parsed["token_id"])
                    )

                # Reliability gate: skip traders with poor Bayesian win rate
                # Uses per-category Beta when enough data exists; falls back to overall.
                if self._reliability_tracker and self._reliability_tracker._cache:
                    _alpha, _beta = self._reliability_tracker._get_beta(addr, _resolved_side, category=_cat)
                    if _alpha + _beta > 2:  # Only filter if we have actual data (not just prior)
                        _mean_rel = _alpha / (_alpha + _beta)
                        if _mean_rel < getattr(settings, "MIRROR_MIN_RELIABILITY", 0.45):
                            logger.info("MirrorBot reliability gate: addr=%s mean=%.3f", addr[:10], _mean_rel)
                            continue
                _wf_rel += 1

                items.append({
                    "trade_id": parsed["trade_id"],
                    "market_id": parsed["market_id"],
                    "token_id": parsed["token_id"],
                    "side": _resolved_side,  # C1: YES/NO (not BUY/SELL)
                    "price": parsed["price"],
                    "confidence": confidence,
                    "trader_address": addr,
                    "category": _cat,  # P2-2: propagate so _get_consensus_min() uses per-category threshold
                })
            return items

        try:
            # Single client session for all traders — no per-trader reconnect
            async with self.base_engine.client:
                # Parallel fetch: up to max_concurrent elites at once
                results = await asyncio.gather(
                    *[_fetch_one_elite(trader) for trader in self.elite_traders],
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, Exception):
                        logger.debug("Elite fetch task failed: %s", result)
                        continue
                    for item in result:
                        key = (
                            str(item["market_id"]),
                            str(item["token_id"]),
                            str(item["side"]),
                        )
                        groups[key].append(item)
        except Exception as e:
            logger.error("Failed to collect elite trades", error=str(e))
            return []

        # S48: Log waterfall diagnostic
        if _wf_raw > 0 or _wf_api_fail > 0:
            logger.info(
                "MirrorBot waterfall: raw=%d parsed=%d conf_pass=%d rel_pass=%d "
                "groups=%d api_fail=%d cache_hits=%d/%d (min_conf=%.2f)",
                _wf_raw, _wf_parsed, _wf_conf, _wf_rel,
                len(groups), _wf_api_fail, _wf_cache_hit, len(self.elite_traders),
                self.min_confidence,
            )

        # Consensus filter: require min unique elites agreeing.
        # R5b: Use per-category threshold when available, otherwise global min_consensus.
        result = []
        _max_unique = 0
        _groups_checked = 0
        for key, items in groups.items():
            unique_traders = {t["trader_address"] for t in items}
            _n = len(unique_traders)
            _max_unique = max(_max_unique, _n)
            _groups_checked += 1
            best = max(items, key=lambda t: t["confidence"])
            # Determine per-category consensus requirement
            _category = (best.get("category") or "").lower()
            _required = self._get_consensus_min(_category)
            if _n < _required:
                continue
            result.append(best)

        if _groups_checked > 0 and not result:
            logger.info(
                "MirrorBot consensus: %d groups checked, max_unique_traders=%d, "
                "required=%d — 0 passed",
                _groups_checked, _max_unique,
                getattr(settings, "MIRROR_MIN_CONSENSUS", 2),
            )

        return result

    def _parse_and_validate_trade(
        self,
        trade: Dict,
        addr: str,
        max_delay_minutes: int,
    ) -> Optional[Dict]:
        """Parse a raw trade dict; return normalised dict or None if invalid/stale/duplicate."""
        if trade.get("type") != "trade":
            return None

        trade_id = trade.get("id")
        if trade_id in self.mirrored_trades:
            return None

        market_id = trade.get("marketId")
        token_id = trade.get("tokenId")
        side = trade.get("side")
        if not all([market_id, token_id, side]):
            return None

        price = self.validate_price(trade.get("price", 0), str(market_id))
        if price is None:
            return None

        # Freshness check
        try:
            ts = trade.get("timestamp")
            if ts is not None:
                if isinstance(ts, (int, float)):
                    trade_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                elif isinstance(ts, str):
                    trade_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if trade_dt.tzinfo is None:
                        trade_dt = trade_dt.replace(tzinfo=timezone.utc)
                else:
                    trade_dt = None
                if trade_dt:
                    age_min = (datetime.now(timezone.utc) - trade_dt).total_seconds() / 60
                    if age_min > max_delay_minutes:
                        return None
                    # Hot-trade filter: mid-market prices reprice within minutes of news
                    _is_mid = 0.20 <= price <= 0.80
                    _hot_max_s = getattr(settings, "MIRROR_HOT_TRADE_MAX_SECONDS", 300)
                    if _is_mid and (age_min * 60) > _hot_max_s:
                        return None  # Market has likely already repriced
        except Exception as e:
            logger.info("trade freshness check failed: %s", e)
            return None

        return {
            "trade_id": trade_id,
            "market_id": market_id,
            "token_id": token_id,
            "side": side,
            "price": price,
        }

    # ── Exit Monitoring ─────────────────────────────────────────────

    async def _check_and_execute_exits(self):
        """Mirror exits when tracked traders close their positions."""
        if not self._open_positions:
            return

        positions_to_close: List[str] = []

        # Autonomous stop-loss and max hold time (no network call needed)
        _stop_pct = getattr(settings, "MIRROR_STOP_LOSS_PCT", 0.15)
        _max_hold_h = getattr(settings, "MIRROR_MAX_HOLD_HOURS", 72)
        _now_utc = datetime.now(timezone.utc)
        for _pos_key, _pos in list(self._open_positions.items()):
            _entry = float(_pos.get("entry_price", 0.5) or 0.5)
            _current = float(_pos.get("current_price", _entry) or _entry)
            _side = (_pos.get("side") or "YES").upper()
            # C2: positions now store YES/NO (post-C1); remove stale "BUY" check
            _pnl_pct = (_current - _entry) / max(_entry, 1e-6) if _side == "YES" else (_entry - _current) / max(_entry, 1e-6)
            if _pnl_pct <= -_stop_pct:
                logger.info("MirrorBot autonomous stop-loss", market=_pos_key, pnl_pct=f"{_pnl_pct:.2%}")
                positions_to_close.append(_pos_key)
                continue
            try:
                _opened_str = _pos.get("timestamp")
                if _opened_str:
                    _opened_at = datetime.fromisoformat(_opened_str)
                    if _opened_at.tzinfo is None:
                        _opened_at = _opened_at.replace(tzinfo=timezone.utc)
                    if (_now_utc - _opened_at).total_seconds() / 3600 >= _max_hold_h:
                        logger.info("MirrorBot max hold time exit", market=_pos_key,
                                    hold_h=f"{(_now_utc - _opened_at).total_seconds()/3600:.1f}h")
                        positions_to_close.append(_pos_key)
            except Exception as e:
                logger.warning("MirrorBot exit: timestamp parse failed for %s: %s", _pos_key, e)

        # Gather all trader addresses we're tracking
        tracked_traders: set = set()
        for pos_data in self._open_positions.values():
            tracked_traders.update(pos_data.get("traders", set()))

        try:
            async with self.base_engine.client:
                for addr in tracked_traders:
                    try:
                        activity = await self.base_engine.client.get_user_activity(
                            user_address=addr,
                            limit=50,
                            offset=0,
                        )
                    except Exception as e:
                        logger.warning("MirrorBot exit: activity fetch failed for %s: %s", addr[:10], e)
                        continue

                    for trade in activity:
                        if trade.get("type") != "trade":
                            continue
                        market_id = trade.get("marketId")
                        token_id = trade.get("tokenId")
                        side = trade.get("side")
                        if not all([market_id, token_id, side]):
                            continue

                        pos_key = f"{market_id}:{token_id}"
                        if pos_key not in self._open_positions:
                            continue

                        pos = self._open_positions[pos_key]
                        # C2: pos_key match already confirms same market+token;
                        # trader's SELL of same token = exit regardless of our stored side (YES/NO)
                        is_exit = side.upper() == "SELL"
                        if is_exit and addr in pos.get("traders", set()):
                            positions_to_close.append(pos_key)
        except Exception as e:
            logger.debug("Exit check failed: %s", e)

        # Execute the exits
        for pos_key in set(positions_to_close):
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

                order = await self.place_order(
                    market_id=market_id,
                    token_id=token_id,
                    side=exit_side,
                    size=pos["size"],
                    price=exit_price,
                    confidence=0.80,
                )
                if order.get("success"):
                    logger.info(
                        "Mirror exit executed",
                        market=market_id,
                        exit_side=exit_side,
                        original_side=pos["side"],
                        size=f"{pos['size']:.2f}",
                    )
                    # M1: Decrement daily exposure on exit (was never decremented, causing monotonic fill)
                    _exit_cost = pos["size"] * pos.get("current_price", pos["entry_price"])
                    self._daily_exposure = max(0.0, self._daily_exposure - _exit_cost)
                    del self._open_positions[pos_key]
            except Exception as e:
                logger.warning("Failed to execute mirror exit for %s: %s", pos_key, e)

    # ── Position & Exposure Tracking ────────────────────────────────

    def _track_open_position(self, trade_info: Dict):
        """Record a newly opened mirror position for exit monitoring."""
        pos_key = f"{trade_info['market_id']}:{trade_info['token_id']}"
        if pos_key in self._open_positions:
            self._open_positions[pos_key]["traders"].add(trade_info["trader_address"])
            # N1: Refresh max-hold timer on each new trader entry (not just first entry)
            self._open_positions[pos_key]["timestamp"] = datetime.now(timezone.utc).isoformat()
        else:
            self._open_positions[pos_key] = {
                "side": trade_info["side"],
                "size": 0.0,  # Updated by _execute_mirror_trade after sizing
                "entry_price": trade_info["price"],
                "traders": {trade_info["trader_address"]},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

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

    def _can_open_position(self, price: float) -> bool:
        """Check concurrent position + daily exposure limits.

        Returns False with a specific INFO log identifying WHICH limit was hit.
        """
        max_positions = getattr(
            settings, "MIRROR_MAX_CONCURRENT_POSITIONS", self.MAX_CONCURRENT_POSITIONS
        )
        if len(self._open_positions) >= max_positions:
            logger.info("Mirror POSITION CAP: %d/%d positions, skipping",
                        len(self._open_positions), max_positions)
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

        return True

    def _check_daily_reset(self):
        """Reset daily exposure counter at UTC day boundary."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self._daily_exposure = 0.0
            self._daily_reset_date = today

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

        # Refresh elite reliability posteriors
        if self._reliability_tracker:
            try:
                await self._reliability_tracker.refresh()
            except Exception as e:
                # N2: Raised from debug to warning — DB failures are not silent in production
                logger.warning("Elite reliability refresh failed: %s", e)

    # ── Opportunity Hook (unused by mirror — consensus in scan) ────

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
    ) -> bool:
        """Execute a mirror trade with reliability weighting and exposure caps."""
        # S48 FIX: Skip SELL consensus trades unless we hold that position.
        # Elite SELLs mean they're closing positions — can't mirror if we never opened.
        _is_sell = str(side).upper() == "SELL"
        if _is_sell:
            pos_key = f"{market_id}:{token_id}"
            if pos_key not in self._open_positions:
                logger.info(
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
                self._daily_exposure = max(0.0, self._daily_exposure - _exit_size * price)
                del self._open_positions[pos_key]
                logger.info(
                    "MirrorBot: SELL exit executed market=%s size=%.2f",
                    str(market_id)[:16], _exit_size,
                )
            return bool(order.get("success"))

        # FIX: Use CURRENT market price, not the trader's historical fill price.
        # The trader may have traded hours ago at a different price. Entering at their
        # stale price produces fake P&L (buying at yesterday's prices, selling at today's).
        _market_data = self.base_engine.get_market_from_index(str(market_id))
        if _market_data:
            _side_upper = str(side).upper()
            if _side_upper in ("YES", "NO"):
                _current = float(_market_data.get(f"{_side_upper.lower()}_price", 0) or 0)
                if 0.01 <= _current <= 0.99:
                    _old_price = price
                    price = _current
                    if abs(_old_price - price) > 0.05:
                        logger.info("mirror_price_corrected", market=str(market_id)[:16],
                                    trader_price=round(_old_price, 4), market_price=round(price, 4))

        # Apply elite reliability multiplier
        reliability_mult = 1.0
        if self._reliability_tracker:
            try:
                lr = self._reliability_tracker.likelihood_ratio(trader_address, side, category=category)
                if lr < 1.0:
                    logger.info(
                        "Skipping unreliable trader %s (LR=%.2f)",
                        trader_address[:10],
                        lr,
                    )
                    return False
                reliability_mult = min(lr, 2.0)  # Cap at 2x
            except Exception as e:
                logger.debug("elite reliability lookup failed: %s", e)

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

        # S48 FIX: Use per-bot BotBankrollManager (Session 47) instead of deprecated
        # risk_manager.calculate_position_size() which divides Kelly by KELLY_ACTIVE_BOTS.
        # calculate_bot_position_size() returns shares (USD / price).
        size = await self.calculate_bot_position_size(
            confidence=confidence,
            price=price,
        )
        size *= reliability_mult

        # Cap per-market exposure (convert USD cap to shares for correct comparison)
        max_per_market_usd = float(getattr(settings, "MIRROR_MAX_PER_MARKET", 400))
        max_per_market_shares = max_per_market_usd / price if price > 0 else 0
        size = min(size, max_per_market_shares)

        # Cap by remaining daily exposure: read bankroll.max_daily_usd directly (matching _can_open_position fix)
        if self.bankroll:
            _max_daily_usd = self.bankroll.max_daily_usd
        else:
            max_daily_pct = getattr(settings, "MIRROR_MAX_DAILY_EXPOSURE_PCT", self.MAX_DAILY_EXPOSURE_PCT)
            _max_daily_usd = float(getattr(settings, "TOTAL_CAPITAL", 10000.0)) * max_daily_pct
        remaining_daily_usd = max(0.0, _max_daily_usd - self._daily_exposure)
        remaining_daily_shares = remaining_daily_usd / price if price > 0 else 0
        size = min(size, remaining_daily_shares)

        if size <= 0:
            logger.info("Mirror trade size zero after limits (per_mkt=$%.0f daily_rem=$%.0f), skipping",
                        max_per_market_usd, remaining_daily_usd)
            return False

        order = await self.place_order(
            market_id=market_id,
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            confidence=confidence,
        )

        if order.get("success") and not order.get("idempotent"):
            self._daily_exposure += size * price  # Track exposure in USD (skip idempotent dedup'd orders)

            # Update position tracking with actual size
            pos_key = f"{market_id}:{token_id}"
            if pos_key in self._open_positions:
                self._open_positions[pos_key]["size"] += size

            logger.info(
                "Mirror trade executed",
                market=market_id,
                side=side,
                trader=trader_address[:10],
                confidence=f"{confidence:.2%}",
                size=f"{size:.2f}",
                open_positions=len(self._open_positions),
                daily_exposure=f"{self._daily_exposure:.2f}",
            )

            # R2: Store signal context for ML training (fire-and-forget).
            _trade_id = order.get("trade_id") or order.get("order_id")
            if _trade_id:
                _t = asyncio.create_task(
                    self.store_pending_trade_signals(str(_trade_id), str(market_id))
                )
                _t.add_done_callback(lambda t: self._on_bg_task_done(t, "store_trade_signals"))

            return True
        return False