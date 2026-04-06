"""
EliteWatchlist — Real-time WebSocket copy trading.

Maintains a set of top trader addresses from Polymarket's per-category leaderboards.
When a MarketTradeEvent arrives via WebSocket with a matching user.address,
triggers instant copy through MirrorBot._execute_mirror_trade().

S155: Restructured from single OVERALL leaderboard to per-category diversified
selection with ROI ranking and hard quotas. See PLAN_WATCHLIST_RESTRUCTURE.

Data source: Polymarket Data API per-category leaderboards (ROI-ranked).
Refresh: daily (enrichment is heavier than old OVERALL-only pull).
Detection: O(1) set lookup on every WebSocket trade event.
"""
import asyncio
import math
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple, Any, TYPE_CHECKING

from structlog import get_logger
from config.settings import settings

if TYPE_CHECKING:
    from bots.mirror_bot import MirrorBot

logger = get_logger()

# Max dedup entries to prevent unbounded memory growth
_MAX_SEEN_TX = 50_000

# Polymarket Data API leaderboard endpoint (no auth required)
_LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
_LEADERBOARD_PAGE_SIZE = 50  # API max per request
_LEADERBOARD_MAX_OFFSET = 1000  # API caps offset at 1000 → max 1050 traders

# S155: Per-category watchlist constants
_LEADERBOARD_CATEGORIES = ["SPORTS", "POLITICS", "FINANCE", "ECONOMICS", "CULTURE", "WEATHER", "TECH"]
_LEADERBOARD_TIME_PERIODS = ["MONTH", "ALL"]
# FINANCE and ECONOMICS are merged into a single quota group
_CATEGORY_GROUP_MAP = {"FINANCE": "FINANCE_ECON", "ECONOMICS": "FINANCE_ECON"}
_DEFAULT_QUOTAS = {"SPORTS": 40, "POLITICS": 22, "FINANCE_ECON": 15, "CULTURE": 10, "WEATHER": 8, "TECH": 5}
_CATEGORY_MIN_TRADES = {"SPORTS": 30, "POLITICS": 20, "FINANCE_ECON": 15, "CULTURE": 15, "WEATHER": 10, "TECH": 10}
_CATEGORY_MIN_VOLUME = {"SPORTS": 25_000, "POLITICS": 25_000, "FINANCE_ECON": 25_000, "CULTURE": 25_000, "WEATHER": 15_000, "TECH": 15_000}


class EliteWatchlist:
    """Maintains top trader watchlist and handles real-time WebSocket trade events."""

    def __init__(
        self,
        client: Any,
        db: Any,
        mirror_bot: "MirrorBot",
    ):
        self._client = client
        self._db = db
        self._mirror_bot = mirror_bot

        # Core watchlist state
        self._watchlist_addresses: Set[str] = set()
        self._watchlist_data: Dict[str, Dict] = {}  # addr -> {pnl, vol, efficiency, ...}

        # Dedup by transaction_hash (capped OrderedDict)
        self._seen_tx: OrderedDict = OrderedDict()

        # M2: Leader activity tracking — last RTDS trade timestamp per trader
        self._last_trade_time: Dict[str, float] = {}  # addr_lower -> monotonic time

        # M6: Wash detection — track buy/sell round-trips per trader per market
        # Key: (addr_lower, market_id) -> list of (side, monotonic_time)
        self._trader_market_trades: Dict[Tuple[str, str], list] = {}
        self._wash_flagged: Dict[str, float] = {}  # addr_lower -> monotonic time flagged (48h expiry)

        # S156: Track persist tasks so they aren't garbage-collected + log errors
        self._persist_tasks: set = set()

        # Refresh tracking
        self._last_refresh: float = 0.0
        self._last_refresh_date: Optional[str] = None  # "YYYY-MM-DD" for daily check
        self._running: bool = False

        # S141: RTDS price cache — latest trade price per token_id from ALL global trades.
        # Used by MirrorBot exit logic as fallback when position_manager prices are stale.
        # Bounded by active Polymarket token count (~10K max).
        self._rtds_price_cache: Dict[str, Tuple[float, float]] = {}  # token_id → (price, mono_time)

        # S146: Copy-P&L per trader — tier assignment based on OUR realized P&L when copying them.
        # Populated during refresh_watchlist() from trade_events DB query.
        # tier: 1=copy-profitable (full size), 2=thin data (50%), 3=copy-unprofitable (25%)
        self._copy_tiers: Dict[str, int] = {}  # addr_lower → tier (1/2/3)
        self._copy_perf: Dict[str, Dict] = {}  # addr_lower → {trades, wins, copy_wr, copy_pnl}

        # S155: Per-category watchlist state (shadow or production)
        self._sunset_traders: Dict[str, float] = {}  # addr_lower → expiry monotonic time
        self._category_watchlist_data: Dict[str, Dict] = {}  # shadow: per-category data
        self._category_watchlist_addresses: Set[str] = set()  # shadow: per-category addresses
        self._shadow_last_comparison: Optional[Dict] = None

        # Stats
        self._events_received: int = 0
        self._events_matched: int = 0
        self._copies_attempted: int = 0
        self._copies_executed: int = 0
        self._copies_yes: int = 0
        self._copies_no: int = 0
        self._copies_sell: int = 0

    @staticmethod
    def _persist_task_done(task):
        """S156: Log errors from _persist_trader_to_position tasks (was silent no-op)."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("persist_trader_to_position_failed", error=str(exc))

    # ── Leaderboard Fetch ─────────────────────────────────────────

    async def _fetch_monthly_leaderboard(self, limit: int = 1000) -> List[Dict]:
        """Fetch top traders from Polymarket monthly leaderboard via Data API.

        Endpoint: GET /v1/leaderboard?timePeriod=MONTH&orderBy=PNL&limit=50&offset=N
        Returns: [{proxyWallet, userName, pnl, vol, rank}, ...]
        Max 50 per page, max offset 1000 → up to 1050 traders.
        """
        import aiohttp

        out: List[Dict] = []
        seen: Set[str] = set()
        offset = 0
        effective_limit = min(limit, _LEADERBOARD_MAX_OFFSET + _LEADERBOARD_PAGE_SIZE)

        try:
            async with aiohttp.ClientSession() as session:
                while len(out) < effective_limit and offset <= _LEADERBOARD_MAX_OFFSET:
                    params = {
                        "timePeriod": "MONTH",
                        "orderBy": "PNL",
                        "category": "OVERALL",
                        "limit": _LEADERBOARD_PAGE_SIZE,
                        "offset": offset,
                    }
                    try:
                        async with session.get(_LEADERBOARD_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            if resp.status != 200:
                                logger.warning("leaderboard_fetch_failed", status=resp.status, offset=offset)
                                break
                            data = await resp.json()
                    except Exception as e:
                        logger.warning("leaderboard_fetch_error", offset=offset, error=str(e))
                        break

                    if not data or not isinstance(data, list) or len(data) == 0:
                        break

                    for u in data:
                        if not isinstance(u, dict):
                            continue
                        addr = u.get("proxyWallet") or u.get("address")
                        if not addr or addr in seen:
                            continue
                        seen.add(addr)
                        pnl = float(u.get("pnl", 0) or 0)
                        vol = float(u.get("vol", 0) or 0)
                        out.append({
                            "address": addr,
                            "pnl": pnl,
                            "vol": vol,
                            "rank": int(u.get("rank", 0) or 0),
                            "userName": u.get("userName", ""),
                        })
                        if len(out) >= effective_limit:
                            break

                    if len(data) < _LEADERBOARD_PAGE_SIZE:
                        break  # Last page
                    offset += _LEADERBOARD_PAGE_SIZE
                    await asyncio.sleep(0.15)  # Rate limit courtesy
        except Exception as e:
            logger.warning("leaderboard_session_error", error=str(e))

        return out

    # ── S155: Per-Category Pipeline ──────────────────────────────

    async def _fetch_category_leaderboard(
        self, category: str, time_period: str, limit: int = 100
    ) -> List[Dict]:
        """Fetch top traders for a specific category and time period."""
        import aiohttp

        out: List[Dict] = []
        seen: Set[str] = set()
        offset = 0
        effective_limit = min(limit, _LEADERBOARD_MAX_OFFSET + _LEADERBOARD_PAGE_SIZE)

        try:
            async with aiohttp.ClientSession() as session:
                while len(out) < effective_limit and offset <= _LEADERBOARD_MAX_OFFSET:
                    params = {
                        "timePeriod": time_period,
                        "orderBy": "PNL",
                        "category": category,
                        "limit": _LEADERBOARD_PAGE_SIZE,
                        "offset": offset,
                    }
                    try:
                        async with session.get(
                            _LEADERBOARD_URL, params=params,
                            timeout=aiohttp.ClientTimeout(total=15),
                        ) as resp:
                            if resp.status != 200:
                                break
                            data = await resp.json()
                    except Exception:
                        break

                    if not data or not isinstance(data, list) or len(data) == 0:
                        break

                    for u in data:
                        if not isinstance(u, dict):
                            continue
                        addr = u.get("proxyWallet") or u.get("address")
                        if not addr or addr in seen:
                            continue
                        seen.add(addr)
                        pnl = float(u.get("pnl", 0) or 0)
                        vol = float(u.get("vol", 0) or 0)
                        out.append({
                            "address": addr,
                            "pnl": pnl,
                            "vol": vol,
                            "rank": int(u.get("rank", 0) or 0),
                            "userName": u.get("userName", ""),
                            "_source_category": category,
                            "_source_period": time_period,
                        })
                        if len(out) >= effective_limit:
                            break

                    if len(data) < _LEADERBOARD_PAGE_SIZE:
                        break
                    offset += _LEADERBOARD_PAGE_SIZE
                    await asyncio.sleep(0.15)
        except Exception as e:
            logger.warning("category_leaderboard_error", category=category, period=time_period, error=str(e))

        return out

    async def _stage1_category_pulls(self) -> Dict[str, List[Dict]]:
        """Stage 1: Fetch top 100 per category×period, filter by vol+ROI.

        Returns dict keyed by category group (e.g. "FINANCE_ECON") → list of trader dicts.
        """
        _min_roi = float(getattr(settings, "WATCHLIST_MIN_ROI", 0.03))
        pools: Dict[str, List[Dict]] = {}

        for cat in _LEADERBOARD_CATEGORIES:
            group = _CATEGORY_GROUP_MAP.get(cat, cat)
            min_vol = _CATEGORY_MIN_VOLUME.get(group, 25_000)

            for period in _LEADERBOARD_TIME_PERIODS:
                raw = await self._fetch_category_leaderboard(cat, period, limit=100)
                for t in raw:
                    vol = t["vol"]
                    pnl = t["pnl"]
                    if vol < min_vol:
                        continue
                    roi = pnl / vol
                    if roi < _min_roi:
                        continue
                    t["roi"] = roi
                    t["_group"] = group
                    pools.setdefault(group, []).append(t)

        # Log stage 1 results
        _counts = {g: len(ts) for g, ts in pools.items()}
        logger.info("watchlist_stage1_pulls", candidates=_counts, total=sum(_counts.values()))
        return pools

    def _stage2_dedup(self, pools: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        """Stage 2: Dedup traders across categories by priority order.

        Since PNL/vol are OVERALL numbers regardless of category query,
        ROI is identical across categories. Assign each trader to the first
        category where they appear (priority: SPORTS > POLITICS > ... > TECH).
        """
        seen_addrs: Set[str] = set()
        deduped: Dict[str, List[Dict]] = {}

        # Process groups in priority order
        group_order = ["SPORTS", "POLITICS", "FINANCE_ECON", "CULTURE", "WEATHER", "TECH"]
        for group in group_order:
            candidates = pools.get(group, [])
            clean = []
            for t in candidates:
                addr_lower = t["address"].lower()
                if addr_lower in seen_addrs:
                    continue
                seen_addrs.add(addr_lower)
                clean.append(t)
            if clean:
                deduped[group] = clean

        _counts = {g: len(ts) for g, ts in deduped.items()}
        logger.info("watchlist_stage2_dedup", unique=_counts, total=sum(_counts.values()))
        return deduped

    async def _stage3_enrichment(self, pools: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        """Stage 3: Enrich top candidates via closed-positions endpoint.

        Computes trade count and profit factor. Filters by category-specific
        min trades and profit factor thresholds.
        """
        _min_pf = float(getattr(settings, "WATCHLIST_MIN_PROFIT_FACTOR", 1.2))
        quotas = self._parse_quotas()
        enriched: Dict[str, List[Dict]] = {}

        for group, candidates in pools.items():
            quota = quotas.get(group, 0)
            if quota == 0:
                continue
            min_trades = _CATEGORY_MIN_TRADES.get(group, 15)

            # Sort by ROI descending, take top quota*1.5 for enrichment
            sorted_cands = sorted(candidates, key=lambda x: x.get("roi", 0), reverse=True)
            enrich_limit = int(quota * 1.5) + 1
            sorted_cands = sorted_cands[:enrich_limit]

            passed = []
            for t in sorted_cands:
                addr = t["address"]
                try:
                    positions = await self._client.get_closed_positions(addr, limit=100)
                except Exception as _enrich_err:
                    logger.warning("enrichment_api_failed", trader=addr[:10], error=str(_enrich_err))
                    positions = []

                trade_count = len(positions) if positions else 0
                # Compute profit factor from closed positions
                gross_win = sum(float(p.get("pnl", 0) or 0) for p in positions if float(p.get("pnl", 0) or 0) > 0)
                gross_loss = abs(sum(float(p.get("pnl", 0) or 0) for p in positions if float(p.get("pnl", 0) or 0) < 0))
                profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

                t["_trade_count"] = trade_count
                t["_profit_factor"] = profit_factor

                # Filter: trade count must meet category minimum
                if trade_count < min_trades:
                    continue
                # Filter: ALL-period candidates must meet profit factor threshold
                if t.get("_source_period") == "ALL" and profit_factor < _min_pf:
                    continue

                passed.append(t)
                await asyncio.sleep(0.05)  # Rate limit courtesy

            if passed:
                enriched[group] = passed

        _counts = {g: len(ts) for g, ts in enriched.items()}
        logger.info("watchlist_stage3_enrichment", qualified=_counts, total=sum(_counts.values()))
        return enriched

    def _stage4_rank_and_select(
        self, enriched: Dict[str, List[Dict]]
    ) -> Tuple[Set[str], Dict[str, Dict]]:
        """Stage 4: Rank by ROI within each category, enforce quotas, build watchlist."""
        quotas = self._parse_quotas()
        new_addresses: Set[str] = set()
        new_data: Dict[str, Dict] = {}

        for group, candidates in enriched.items():
            quota = quotas.get(group, 0)
            if quota == 0:
                continue

            # Rank by ROI descending, take top N per quota
            ranked = sorted(candidates, key=lambda x: x.get("roi", 0), reverse=True)[:quota]

            for t in ranked:
                addr = t["address"]
                addr_lower = addr.lower()
                pnl = t["pnl"]
                vol = t["vol"]
                roi = t.get("roi", pnl / vol if vol > 0 else 0)

                # S155: Scale ROI into efficiency for gate score eff_prior formula
                # eff_prior = min(0.65, 0.50 + efficiency * 0.50)
                # 5% ROI → eff 0.25 → eff_prior 0.625
                # 15% ROI → eff 0.75 → eff_prior 0.65 (capped)
                efficiency = min(1.0, roi * 5.0)

                new_addresses.add(addr_lower)
                new_data[addr_lower] = {
                    "address": addr,
                    "pnl": pnl,
                    "vol": vol,
                    "efficiency": efficiency,
                    "roi": roi,
                    "num_trades": t.get("_trade_count", 0),
                    "rank": t.get("rank", 0),
                    "userName": t.get("userName", ""),
                    "category": group,
                    "_profit_factor": t.get("_profit_factor", 0),
                }

        _breakdown = {}
        for d in new_data.values():
            cat = d.get("category", "unknown")
            _breakdown[cat] = _breakdown.get(cat, 0) + 1
        logger.info("watchlist_stage4_selected", breakdown=_breakdown, total=len(new_addresses))
        return new_addresses, new_data

    def _parse_quotas(self) -> Dict[str, int]:
        """Parse WATCHLIST_CATEGORY_QUOTAS setting into dict."""
        raw = getattr(settings, "WATCHLIST_CATEGORY_QUOTAS", "")
        if not raw:
            return dict(_DEFAULT_QUOTAS)
        try:
            result = {}
            for pair in raw.split(","):
                pair = pair.strip()
                if ":" not in pair:
                    continue
                k, v = pair.split(":", 1)
                result[k.strip()] = int(v.strip())
            return result if result else dict(_DEFAULT_QUOTAS)
        except Exception:
            return dict(_DEFAULT_QUOTAS)

    def _update_sunset_list(self, old_addrs: Set[str], new_addrs: Set[str]) -> None:
        """S155: Add removed traders to sunset list for exit-only monitoring."""
        removed = old_addrs - new_addrs
        sunset_days = int(getattr(settings, "WATCHLIST_SUNSET_DAYS", 7))
        expiry = time.monotonic() + sunset_days * 86400
        for addr in removed:
            self._sunset_traders[addr] = expiry

        # Prune expired entries
        now = time.monotonic()
        expired = [a for a, exp in self._sunset_traders.items() if exp <= now]
        for a in expired:
            del self._sunset_traders[a]

        if removed:
            logger.info("watchlist_sunset_update", added=len(removed), total=len(self._sunset_traders),
                        expired=len(expired))

    # ── Watchlist Refresh ─────────────────────────────────────────

    async def refresh_watchlist(self) -> int:
        """Rebuild watchlist from monthly leaderboard. Returns count of traders added."""
        _size = getattr(settings, "WATCHLIST_SIZE", 1000)

        # Primary: monthly leaderboard (no auth needed)
        raw_traders = await self._fetch_monthly_leaderboard(limit=_size)

        # Fallback: get_top_users (Gamma→Data API all-time) if monthly returned nothing
        if not raw_traders:
            try:
                raw_traders = await self._client.get_top_users(limit=_size)
            except Exception as e:
                logger.warning("watchlist_refresh_fallback_failed", error=str(e))

        # Last resort: DB elite users
        if not raw_traders:
            try:
                raw_traders = await self._fetch_from_db(_size)
            except Exception as e:
                logger.warning("watchlist_refresh_db_fallback_failed", error=str(e))

        if not raw_traders:
            logger.warning("watchlist_refresh: no traders from any source")
            return len(self._watchlist_addresses)

        # Build watchlist with profit/volume efficiency scoring
        new_addresses: Set[str] = set()
        new_data: Dict[str, Dict] = {}

        for t in raw_traders:
            addr = t.get("address") or t.get("proxyWallet")
            if not addr or not isinstance(addr, str):
                continue

            pnl = float(t.get("pnl", t.get("totalProfit", t.get("total_profit", 0))) or 0)
            vol = float(t.get("vol", t.get("totalVolume", t.get("total_volume", 0))) or 0)

            # Efficiency: profit / volume ratio (0 if no volume)
            efficiency = pnl / vol if vol > 0 else 0.0

            addr_lower = addr.lower()
            new_addresses.add(addr_lower)
            _num_trades = int(t.get("totalTrades", t.get("numTrades", t.get("total_trades", 0))) or 0)
            new_data[addr_lower] = {
                "address": addr,
                "pnl": pnl,
                "vol": vol,
                "efficiency": efficiency,
                "num_trades": _num_trades,
                "rank": t.get("rank", 0),
                "userName": t.get("userName", ""),
            }

        # S113 F3: Supplement num_trades from DB — Data API doesn't return totalTrades,
        # causing F3 (trade size conviction) guard to always fail (_whale_n=0).
        if self._db and new_data:
            try:
                _addrs_for_counts = [a for a, d in new_data.items() if d.get("num_trades", 0) == 0]
                if _addrs_for_counts:
                    _counts = await asyncio.wait_for(
                        self._db.get_user_trade_counts(_addrs_for_counts, lookback_days=30),
                        timeout=15.0,
                    )
                    _supplemented = 0
                    for row in _counts:
                        _addr_l = (row["user_address"] or "").strip().lower()
                        if _addr_l in new_data:
                            new_data[_addr_l]["num_trades"] = row["num_trades"]
                            _supplemented += 1
                    if _supplemented:
                        logger.info("watchlist_num_trades_supplemented", count=_supplemented,
                                    total=len(_addrs_for_counts))
            except Exception as _nt_err:
                logger.debug("watchlist num_trades supplement failed: %s", _nt_err)

        # M2: Apply inactivity decay — demote leaders who haven't traded recently
        _now_mono = time.monotonic()
        _inactive_14d = 14 * 86400  # 14 days in seconds
        _inactive_21d = 21 * 86400
        _removed_inactive = 0
        _decayed_inactive = 0
        for addr_lower in list(new_addresses):
            _last = self._last_trade_time.get(addr_lower)
            if _last is None:
                continue  # No RTDS data yet — keep as-is
            _age_s = _now_mono - _last
            if _age_s >= _inactive_21d:
                new_addresses.discard(addr_lower)
                new_data.pop(addr_lower, None)
                _removed_inactive += 1
            elif _age_s >= _inactive_14d:
                if addr_lower in new_data:
                    new_data[addr_lower]["efficiency"] *= 0.5
                    _decayed_inactive += 1
        if _removed_inactive or _decayed_inactive:
            logger.info("leader_inactivity", removed=_removed_inactive, decayed=_decayed_inactive)

        # S146: Query copy-P&L per trader from trade_events and assign tiers.
        # Tier 1 = copy-profitable (full sizing), Tier 2 = thin data (learning),
        # Tier 3 = copy-unprofitable (probation). All 300 stay — tiers control capital only.
        _min_for_tier = int(getattr(settings, "MIRROR_COPY_MIN_TRADES_FOR_TIER", 20))
        _new_copy_tiers: Dict[str, int] = {}
        _new_copy_perf: Dict[str, Dict] = {}
        _tier_counts = {1: 0, 2: 0, 3: 0}
        if self._db and new_addresses:
            try:
                from sqlalchemy import text
                # S150: Apply regime_start to exclude pre-S146 data from copy-tier scoring.
                # Without this, traders get penalized for losses under old broken gates.
                # S159: Parameterized regime_start — was f-string interpolation
                _regime = getattr(settings, "MIRROR_REGIME_START", None) or None
                _params: Dict[str, Any] = {}
                if _regime:
                    _entry_time_filter = "AND event_time >= :regime_start"
                    _exit_time_filter = "AND te.event_time >= :regime_start"
                    _params["regime_start"] = _regime
                else:
                    _entry_time_filter = "AND event_time >= NOW() - INTERVAL '30 days'"
                    _exit_time_filter = ""
                async with self._db.get_session(timeout=15) as session:
                    _result = await session.execute(text(f"""
                        WITH entry_trader AS (
                            SELECT DISTINCT ON (market_id)
                                market_id, event_data->>'trader' AS trader
                            FROM trade_events
                            WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'
                                AND event_data->>'trader' IS NOT NULL
                                {_entry_time_filter}
                            ORDER BY market_id, event_time ASC
                        )
                        SELECT
                            LOWER(et.trader) AS trader,
                            COUNT(*) AS trades,
                            COUNT(*) FILTER (WHERE te.realized_pnl > 0) AS wins,
                            ROUND(SUM(te.realized_pnl)::numeric, 2) AS copy_pnl
                        FROM trade_events te
                        JOIN entry_trader et ON et.market_id = te.market_id
                        WHERE te.bot_name = 'MirrorBot'
                            AND te.event_type IN ('EXIT', 'RESOLUTION')
                            {_exit_time_filter}
                        GROUP BY LOWER(et.trader)
                    """), _params)
                    # Build prefix→full_addr lookup for matching truncated (10-char)
                    # trader addresses from historical event_data against full watchlist addrs.
                    # New entries store full addresses; old entries are [:10] truncated.
                    _prefix_map: Dict[str, str] = {}  # "0xabcd1234" → full_addr
                    for _full in new_addresses:
                        _prefix_map[_full[:10].lower()] = _full
                    for row in _result:
                        _raw_addr = str(row[0] or "").strip().lower()
                        if not _raw_addr:
                            continue
                        # Match: full address in watchlist, or prefix match for truncated
                        _matched_addr = None
                        if _raw_addr in new_addresses:
                            _matched_addr = _raw_addr  # full address match
                        elif len(_raw_addr) <= 10:
                            _matched_addr = _prefix_map.get(_raw_addr)  # prefix match
                        if not _matched_addr:
                            continue
                        _trades = int(row[1])
                        _wins = int(row[2])
                        _pnl = float(row[3] or 0)
                        _wr = round(100.0 * _wins / _trades, 1) if _trades > 0 else 0.0
                        # Accumulate if same trader matched from multiple prefixes
                        if _matched_addr in _new_copy_perf:
                            _existing = _new_copy_perf[_matched_addr]
                            _existing["trades"] += _trades
                            _existing["wins"] += _wins
                            _existing["copy_pnl"] += _pnl
                            _existing["copy_wr"] = round(100.0 * _existing["wins"] / max(_existing["trades"], 1), 1)
                        else:
                            _new_copy_perf[_matched_addr] = {
                                "trades": _trades, "wins": _wins,
                                "copy_wr": _wr, "copy_pnl": _pnl,
                            }
                        _p = _new_copy_perf[_matched_addr]
                        if _p["trades"] >= _min_for_tier:
                            _tier = 1 if _p["copy_pnl"] > 0 else 3
                        else:
                            _tier = 2
                        _new_copy_tiers[_matched_addr] = _tier
                    # Recount tiers after all rows processed
                    _tier_counts = {1: 0, 2: 0, 3: 0}
                    for _t in _new_copy_tiers.values():
                        _tier_counts[_t] = _tier_counts.get(_t, 0) + 1
                logger.info("watchlist_copy_scoring",
                            tier1=_tier_counts[1], tier2=_tier_counts[2], tier3=_tier_counts[3],
                            total_scored=sum(_tier_counts.values()))
            except Exception as _cp_err:
                logger.warning("watchlist_copy_scoring_failed: %s", _cp_err)

        # Assign tier 2 (learning) to watchlist members with no copy data
        for _addr in new_addresses:
            if _addr not in _new_copy_tiers:
                _new_copy_tiers[_addr] = 2

        self._copy_tiers = _new_copy_tiers
        self._copy_perf = _new_copy_perf
        self._watchlist_addresses = new_addresses
        self._watchlist_data = new_data
        # S158: Prune _last_trade_time for traders no longer on watchlist
        _active_addrs = {a.lower() for a in new_addresses}
        _stale_ltt = [k for k in self._last_trade_time if k not in _active_addrs]
        for _k in _stale_ltt:
            del self._last_trade_time[_k]
        # S159: Prune _rtds_price_cache entries older than 1h (dead markets)
        _rtds_cutoff = time.monotonic() - 3600
        _stale_rtds = [k for k, v in self._rtds_price_cache.items() if v[1] < _rtds_cutoff]
        if _stale_rtds:
            for _k in _stale_rtds:
                del self._rtds_price_cache[_k]
            logger.debug("rtds_price_cache_pruned", removed=len(_stale_rtds),
                         remaining=len(self._rtds_price_cache))
        self._last_refresh = time.monotonic()
        self._last_refresh_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Log top 5 by efficiency for visibility
        _top5 = sorted(new_data.values(), key=lambda x: x["efficiency"], reverse=True)[:5]
        _top5_str = ", ".join(f"{t['userName'] or t['address'][:10]}({t['efficiency']:.3f})" for t in _top5)

        logger.info(
            "watchlist_refresh",
            source="monthly_leaderboard",
            total_fetched=len(raw_traders),
            watchlist_size=len(new_addresses),
            top5_efficiency=_top5_str,
        )
        return len(new_addresses)

    def needs_refresh(self) -> bool:
        """S96: Check if watchlist needs refresh (every 6h, was daily)."""
        if not self._last_refresh:
            return True
        return (time.monotonic() - self._last_refresh) >= 21600  # 6 hours

    async def _fetch_from_db(self, limit: int) -> list:
        """Last resort fallback: fetch elite users from DB."""
        if not self._db or not getattr(self._db, "session_factory", None):
            return []
        from sqlalchemy import text
        async with self._db.get_session() as session:
            result = await session.execute(
                text(
                    "SELECT address, win_rate, total_trades, total_volume, total_profit "
                    "FROM users WHERE is_elite = TRUE "
                    "ORDER BY total_profit DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
            rows = result.fetchall()
            return [
                {
                    "address": r[0],
                    "pnl": float(r[4] or 0),
                    "vol": float(r[3] or 0),
                    "totalTrades": int(r[2] or 0),
                }
                for r in rows
            ]

    # ── WebSocket Trade Handler ───────────────────────────────────

    async def on_trade_event(self, data: Dict[str, Any]) -> None:
        """WebSocket trade event handler. Called by WebSocketManager for last_trade_price events.

        Event shape (Polymarket Market Channel):
        {
            "event_type": "last_trade_price",
            "market": "0x...",      # condition_id
            "asset_id": "123...",   # token_id
            "price": "0.65",
            "size": "100",
            "side": "BUY",
            "outcome": "Yes",
            "user": {"address": "0x...", "username": "..."},
            "transaction_hash": "0x...",
            "timestamp": "..."
        }
        """
        self._events_received += 1

        # S117: Block trades until MirrorBot state is restored from DB
        if not getattr(self._mirror_bot, '_state_restored', False):
            return

        # 1. Extract user address
        user = data.get("user")
        if not user or not isinstance(user, dict):
            return
        addr = user.get("address")
        if not addr:
            return

        # 2. O(1) watchlist lookup
        addr_lower = addr.lower()
        if addr_lower not in self._watchlist_addresses:
            return

        self._events_matched += 1

        # 3. Dedup by transaction_hash
        tx_hash = data.get("transaction_hash")
        if tx_hash:
            if tx_hash in self._seen_tx:
                return
            self._seen_tx[tx_hash] = None
            # Cap dedup set
            while len(self._seen_tx) > _MAX_SEEN_TX:
                self._seen_tx.popitem(last=False)

        # 4. Parse trade fields
        market_id = data.get("market")  # condition_id
        token_id = data.get("asset_id")
        if not market_id or not token_id:
            return

        try:
            price = float(data.get("price", 0))
            size = float(data.get("size", 0))
        except (TypeError, ValueError):
            return

        if price <= 0.01 or price >= 0.99:
            return
        if size <= 0:
            return

        # 5. Resolve side to YES/NO
        raw_side = str(data.get("side", "BUY")).upper()
        outcome = str(data.get("outcome", "")).capitalize()

        if raw_side == "SELL":
            resolved_side = "SELL"
        elif outcome in ("Yes", "Up"):
            resolved_side = "YES"
        elif outcome in ("No", "Down"):
            resolved_side = "NO"
        else:
            # Fallback: use MirrorBot's token resolution
            resolved_side = await self._mirror_bot._get_token_side(market_id, token_id)

        # 5b. M6: Wash detection — track buy/sell cycling per trader per market
        _wash_key = (addr_lower, str(market_id))
        _now_mono = time.monotonic()
        if _wash_key not in self._trader_market_trades:
            self._trader_market_trades[_wash_key] = []
        _trades = self._trader_market_trades[_wash_key]
        _trades.append((resolved_side, _now_mono))
        # Prune trades older than 24h
        _cutoff = _now_mono - 86400
        self._trader_market_trades[_wash_key] = [t for t in _trades if t[1] > _cutoff]
        _trades = self._trader_market_trades[_wash_key]
        # BUG-3 fix: prune empty keys to prevent unbounded dict growth
        if not _trades:
            del self._trader_market_trades[_wash_key]
        # Count round-trips (entry+exit pairs within 1h window)
        # BUG-1 fix: O(n log n) via sorted sells + bisect instead of O(n²)
        # BUG-2 fix: count unique sell-matched entries to avoid overcounting
        _round_trips = 0
        _entries = sorted([t[1] for t in _trades if t[0] in ("YES", "NO")])
        _exits = sorted([t[1] for t in _trades if t[0] == "SELL"])
        if _entries and _exits:
            import bisect
            _used_exits = set()
            for _e_time in _entries:
                # Find exits within [_e_time - 3600, _e_time + 3600]
                _lo = bisect.bisect_left(_exits, _e_time - 3600)
                _hi = bisect.bisect_right(_exits, _e_time + 3600)
                for _si in range(_lo, _hi):
                    if _si not in _used_exits:
                        _used_exits.add(_si)
                        _round_trips += 1
                        break
        if _round_trips >= 3 and addr_lower not in self._wash_flagged:
            self._wash_flagged[addr_lower] = time.monotonic()
            logger.warning("wash_trader_flagged", trader=addr[:10],
                           market=str(market_id)[:16], round_trips=_round_trips)
        # S159: 48h expiry — longer than MM detector's 24h window so the two don't overlap
        _wash_ts = self._wash_flagged.get(addr_lower)
        if _wash_ts is not None:
            if time.monotonic() - _wash_ts > 172800:  # 48h
                del self._wash_flagged[addr_lower]
            else:
                return  # Skip wash traders entirely

        # 6. Check position + daily limits
        if resolved_side != "SELL" and not self._mirror_bot._can_open_position(price):
            return

        # 7. Confidence from efficiency score
        # Efficient traders (high pnl/vol) get slightly higher confidence → larger Kelly size.
        # Base confidence 0.55 (all monthly top-1k are proven profitable).
        # Efficiency bonus: +0.05 for top-tier efficiency (capped at 0.70).
        trader_data = self._watchlist_data.get(addr_lower, {})
        _efficiency = trader_data.get("efficiency", 0)
        # Clamp efficiency bonus: 0 to 0.15 (maps ~0-30% efficiency to 0-0.15 confidence boost)
        _eff_bonus = min(0.15, max(0.0, _efficiency * 0.5))
        confidence = min(0.70, 0.55 + _eff_bonus)

        # 8. Execute copy trade
        # S112: Pass whale's trade USD for F3 conviction signal
        _whale_trade_usd = size * price  # size=shares from RTDS, price=fill price
        self._copies_attempted += 1
        _start = time.monotonic()
        # S115: Set scan_start_mono on MirrorBot for RTDS fast-path latency tracking
        self._mirror_bot._scan_start_mono = _start
        try:
            executed = await self._mirror_bot._execute_mirror_trade(
                market_id=market_id,
                token_id=token_id,
                side=resolved_side,
                price=price,
                confidence=confidence,
                trader_address=addr,
                category=None,
                source="rtds",
                whale_trade_usd=_whale_trade_usd,
            )
            _latency_ms = (time.monotonic() - _start) * 1000

            if executed:
                self._copies_executed += 1
                # Side distribution tracking
                if resolved_side == "YES":
                    self._copies_yes += 1
                elif resolved_side == "NO":
                    self._copies_no += 1
                elif resolved_side == "SELL":
                    self._copies_sell += 1
                # Track the position
                self._mirror_bot.mirrored_trades[tx_hash or f"ws_{market_id}_{token_id}_{addr[:10]}"] = None
                if resolved_side != "SELL":
                    # M1: Include category for per-category exposure tracking
                    _cat = ""
                    _meta = self._mirror_bot._market_meta_cache.get(str(market_id))
                    if _meta:
                        _cat = _meta[0]  # (category, ttr, expiry)
                    # M2 fix: _track_open_position() was deleted in S134; inline the dict creation
                    # matching the pattern in _execute_mirror_trade() (L1799-1806).
                    # RTDS path has no final size yet — use 0.0; _execute_mirror_trade will
                    # increment it once the actual order fills.
                    _pos_key = f"{market_id}:{token_id}"
                    if _pos_key not in self._mirror_bot._open_positions:
                        self._mirror_bot._open_positions[_pos_key] = {
                            "side": resolved_side,
                            "size": 0.0,
                            "entry_price": price,
                            "traders": {addr},
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "category": _cat,
                        }
                    # S156: Store task ref + error-logging callback (was fire-and-forget
                    # with a no-op lambda that silently discarded exceptions).
                    # Not awaited in hot path — shaves ~50-200ms off copy latency.
                    _t = asyncio.create_task(self._mirror_bot._persist_trader_to_position({
                        "market_id": market_id,
                        "token_id": token_id,
                        "trader_address": addr,
                    }))
                    _t.add_done_callback(self._persist_task_done)
                    self._persist_tasks.add(_t)
                    _t.add_done_callback(self._persist_tasks.discard)

                logger.info(
                    "mirror_instant_copy",
                    trader=addr[:10],
                    market=str(market_id)[:16],
                    side=resolved_side,
                    price=round(price, 4),
                    latency_ms=round(_latency_ms, 1),
                    confidence=round(confidence, 3),
                    efficiency=round(_efficiency, 4),
                    trader_pnl=round(trader_data.get("pnl", 0), 0),
                    copy_tier=self._copy_tiers.get(addr_lower, 2),  # S146
                )
            else:
                logger.debug(
                    "mirror_instant_copy_skipped",
                    trader=addr[:10],
                    market=str(market_id)[:16],
                    side=resolved_side,
                    reason="execute_returned_false",
                )
        except Exception as e:
            logger.warning("mirror_instant_copy_error", trader=addr[:10], error=str(e))

    # ── RTDS Global Trade Handler ────────────────────────────────

    async def on_rtds_trade(self, data: Dict[str, Any]) -> None:
        """Handle RTDS global trade event. Maps RTDS fields to internal format.

        RTDS event shape (activity/trades):
        {
            "asset": "123...",         # token_id
            "conditionId": "0x...",    # market condition_id
            "outcome": "Yes",
            "price": 0.65,
            "proxyWallet": "0x...",    # trader address
            "side": "BUY",
            "size": 100,
            "slug": "...",
            "timestamp": 1234567890
        }
        """
        # S117: Block RTDS trades until MirrorBot state is restored from DB
        if not getattr(self._mirror_bot, '_state_restored', False):
            return

        # S141: Cache price from ALL RTDS trades (not just elite) for position price updates.
        # This runs before the watchlist filter so we get prices for every active market.
        _rtds_token = data.get("asset")
        _rtds_price = data.get("price")
        if _rtds_token and _rtds_price is not None:
            try:
                self._rtds_price_cache[_rtds_token] = (float(_rtds_price), time.monotonic())
            except (ValueError, TypeError):
                pass

        # Fast-reject: check proxyWallet against watchlist before any processing
        addr = data.get("proxyWallet")
        if not addr or addr.lower() not in self._watchlist_addresses:
            return

        # Dedup: prefer real transactionHash from RTDS, fall back to composite key
        _dedup_key = data.get("transactionHash") or \
            f"rtds_{addr}_{data.get('asset')}_{data.get('price')}_{data.get('size')}_{data.get('side')}"
        if _dedup_key in self._seen_tx:
            return
        self._seen_tx[_dedup_key] = None
        while len(self._seen_tx) > _MAX_SEEN_TX:
            self._seen_tx.popitem(last=False)

        # M2: Track last trade time per leader for inactivity detection
        self._last_trade_time[addr.lower()] = time.monotonic()

        # Map RTDS fields → internal format used by on_trade_event
        # transaction_hash=None: dedup already handled above, skip on_trade_event's dedup check
        mapped = {
            "user": {"address": addr},
            "asset_id": data.get("asset"),
            "market": data.get("conditionId"),
            "price": str(data.get("price", "")),
            "size": str(data.get("size", "")),
            "side": data.get("side", "BUY"),
            "outcome": data.get("outcome", ""),
            "transaction_hash": None,
        }
        await self.on_trade_event(mapped)

    def get_stats(self) -> Dict[str, Any]:
        """Return watchlist stats for diagnostics."""
        return {
            "watchlist_size": len(self._watchlist_addresses),
            "events_received": self._events_received,
            "events_matched": self._events_matched,
            "copies_attempted": self._copies_attempted,
            "copies_executed": self._copies_executed,
            "copies_yes": self._copies_yes,
            "copies_no": self._copies_no,
            "copies_sell": self._copies_sell,
            "seen_tx_count": len(self._seen_tx),
            "last_refresh_date": self._last_refresh_date,
            "last_refresh_ago_s": round(time.monotonic() - self._last_refresh, 0) if self._last_refresh else None,
            "rtds_price_cache_size": len(self._rtds_price_cache),
        }

    def get_copy_tier(self, trader_address: str) -> int:
        """S146: Return copy-P&L tier for a trader (1=profitable, 2=thin data, 3=unprofitable)."""
        return self._copy_tiers.get(trader_address.lower(), 2)

    def get_copy_perf(self, trader_address: str) -> Optional[Dict]:
        """S146: Return copy performance dict for a trader, or None if no data."""
        return self._copy_perf.get(trader_address.lower())

    def get_rtds_price(self, token_id: str, max_age_s: float = 300.0) -> Optional[float]:
        """S141: Return cached RTDS price for a token, or None if stale/missing.

        Args:
            token_id: CLOB token ID.
            max_age_s: Maximum age in seconds (default 5 min). Older prices are discarded.
        """
        entry = self._rtds_price_cache.get(token_id)
        if entry is None:
            return None
        price, mono_time = entry
        if time.monotonic() - mono_time > max_age_s:
            return None
        return price
