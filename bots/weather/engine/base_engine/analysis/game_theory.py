"""
Game-theoretic elements for Polymarket bots.

Only mechanisms that produce measurable edge. Uses:
- get_recent_trades_for_market, get_price_at (database)
- Order book snapshots (OrderBookTracker / client)
- Config: TIMING_JITTER_PCT, TIMING_SKIP_PROB, TIMING_BURST_PROB (optional)
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()


# --- Element 3: Strategic Timer (no new data; implement immediately) ---


class StrategicTimer:
    """
    Adds jitter and skip/burst to scan intervals so bot behavior is harder to exploit.
    """

    def __init__(
        self,
        base_interval_seconds: float,
        jitter_pct: float = 0.3,
        skip_probability: float = 0.05,
        burst_probability: float = 0.02,
    ):
        self.base_interval = base_interval_seconds
        self.jitter_pct = jitter_pct
        self.skip_probability = skip_probability
        self.burst_probability = burst_probability

    def next_interval(self) -> float:
        if random.random() < self.skip_probability:
            return self.base_interval * 2
        jitter = self.base_interval * self.jitter_pct
        interval = self.base_interval + random.uniform(-jitter, jitter)
        return max(float(interval), 5.0)

    def should_burst(self) -> bool:
        return random.random() < self.burst_probability


# --- Element 6: Order book analyzer (uses existing snapshot shape) ---


class OrderBookAnalyzer:
    """
    Extract depth ratio, spread, and cliff info from order book snapshot.
    Expects snapshot with bids/asks as list of dicts with price/size or (price, size).
    """

    def analyze(self, book: Dict[str, Any]) -> Dict[str, Any]:
        if not book or book.get("error"):
            return {}
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return {}
        best_bid = float(bids[0].get("price", bids[0][0]) if isinstance(bids[0], dict) else bids[0][0])
        best_ask = float(asks[0].get("price", asks[0][0]) if isinstance(asks[0], dict) else asks[0][0])
        spread = best_ask - best_bid
        midpoint = (best_bid + best_ask) / 2

        def _size(level: Any) -> float:
            if isinstance(level, dict):
                return float(level.get("size", level.get("amount", 0)) or 0)
            return float(level[1]) if len(level) > 1 else 0

        bid_depth = sum(_size(b) for b in bids[:5])
        ask_depth = sum(_size(a) for a in asks[:5])
        depth_ratio = bid_depth / max(ask_depth, 0.01)

        bid_cliff = self._detect_cliff(bids, _size, direction="down")
        ask_cliff = self._detect_cliff(asks, _size, direction="up")
        spread_pct = spread / midpoint if midpoint > 0 else 0

        return {
            "spread": spread,
            "spread_pct": spread_pct,
            "depth_ratio": depth_ratio,
            "bid_cliff_price": bid_cliff,
            "ask_cliff_price": ask_cliff,
            "midpoint": midpoint,
            "total_depth": bid_depth + ask_depth,
        }

    def _detect_cliff(
        self,
        levels: List[Any],
        size_fn: Any,
        direction: str,
    ) -> Optional[float]:
        for i in range(1, len(levels)):
            curr = size_fn(levels[i])
            prev = size_fn(levels[i - 1])
            if prev > 0 and curr < prev * 0.5:
                pr = levels[i].get("price", levels[i][0]) if isinstance(levels[i], dict) else levels[i][0]
                return float(pr)
        return None


# --- Element 4: Cascade detector (uses get_recent_trades_for_market) ---


class CascadeDetector:
    """
    Detect information cascades: same-direction runs, monotonic price, short intervals.
    """

    def __init__(self, db: Any, threshold: float = 0.6):
        self.db = db
        self._threshold = threshold

    async def detect(self, market_id: str, window_hours: int = 6) -> Dict[str, Any]:
        if not self.db or not getattr(self.db, "get_recent_trades_for_market", None):
            return {"cascade_active": False}
        trades = await self.db.get_recent_trades_for_market(market_id, hours=window_hours)
        if len(trades) < 5:
            return {"cascade_active": False}
        runs = self._find_directional_runs(trades)
        if not runs:
            return {"cascade_active": False}
        longest = max(runs, key=lambda r: len(r["trades"]))
        run_trades = longest["trades"]
        if len(run_trades) < 4:
            return {"cascade_active": False}

        unique_addresses = len({t.get("user_address") for t in run_trades})
        address_ratio = unique_addresses / len(run_trades)
        direction = longest["direction"]
        if direction == "YES":
            prices_ok = all(
                run_trades[i].get("price", 0) >= run_trades[i - 1].get("price", 0)
                for i in range(1, len(run_trades))
            )
        else:
            prices_ok = all(
                run_trades[i].get("price", 1) <= run_trades[i - 1].get("price", 1)
                for i in range(1, len(run_trades))
            )
        intervals = []
        for i in range(1, len(run_trades)):
            t0 = run_trades[i - 1].get("timestamp")
            t1 = run_trades[i].get("timestamp")
            if t0 and t1:
                try:
                    delta = (t1 - t0).total_seconds() if hasattr(t1 - t0, "total_seconds") else 0
                except TypeError:
                    delta = 0
                intervals.append(delta)
        avg_interval = sum(intervals) / len(intervals) if intervals else 999999.0
        time_compressed = avg_interval < 600
        sizes = [t.get("size") or 0 for t in run_trades]
        mean_sz = sum(sizes) / len(sizes) if sizes else 0
        var_sz = sum((x - mean_sz) ** 2 for x in sizes) / len(sizes) if sizes else 0
        size_cv = (var_sz ** 0.5 / mean_sz) if mean_sz > 0 else 999
        size_uniform = size_cv < 1.0

        cascade_score = (
            (0.3 if address_ratio > 0.7 else 0)
            + (0.3 if prices_ok else 0)
            + (0.2 if time_compressed else 0)
            + (0.2 if size_uniform else 0)
        )
        price_move = (run_trades[-1].get("price") or 0) - (run_trades[0].get("price") or 0)
        return {
            "cascade_active": cascade_score > self._threshold,
            "cascade_score": cascade_score,
            "direction": direction,
            "run_length": len(run_trades),
            "unique_traders": unique_addresses,
            "price_move": price_move,
            "avg_interval_seconds": avg_interval,
        }

    def _find_directional_runs(self, trades: List[Dict]) -> List[Dict]:
        if not trades:
            return []
        runs = []
        side_key = "side"
        current = {"direction": (trades[0].get(side_key) or "YES").upper(), "trades": [trades[0]]}
        for t in trades[1:]:
            s = (t.get(side_key) or "YES").upper()
            if s == current["direction"]:
                current["trades"].append(t)
            else:
                runs.append(current)
                current = {"direction": s, "trades": [t]}
        runs.append(current)
        return runs


# --- Element 1: Persuasion detector (splash-and-followers, price reversion) ---


class PersuasionDetector:
    """
    Detects when large trades look like strategic signals (splash + followers, then reversion).
    """

    def __init__(self, db: Any):
        self.db = db

    async def analyze_trade_pattern(
        self, market_id: str, lookback_hours: int = 24
    ) -> Dict[str, Any]:
        if not self.db or not getattr(self.db, "get_recent_trades_for_market", None):
            return {"persuasion_score": 0.0, "confidence": 0}
        trades = await self.db.get_recent_trades_for_market(market_id, hours=lookback_hours)
        if len(trades) < 5:
            return {"persuasion_score": 0.0, "confidence": 0}

        sizes = [t.get("size") or 0 for t in trades]
        try:
            large_threshold = float(sorted(sizes)[min(int(len(sizes) * 0.9), len(sizes) - 1)])
        except (IndexError, TypeError):
            large_threshold = max(sizes) * 0.9 if sizes else 0
        large_trades = [t for t in trades if (t.get("size") or 0) > large_threshold]

        splash_score = 0
        for lt in large_trades:
            ts = lt.get("timestamp")
            if not ts:
                continue
            two_h = ts + timedelta(hours=2) if hasattr(ts, "__add__") else None
            followers = [
                t
                for t in trades
                if t.get("user_address") != lt.get("user_address")
                and (t.get("size") or 0) < large_threshold
                and t.get("side") == lt.get("side")
            ]
            if two_h is not None:
                followers = [t for t in followers if t.get("timestamp") and ts <= t["timestamp"] <= two_h]
            if len(followers) > 3:
                splash_score += 1

        reversion_score = 0
        if large_trades and getattr(self.db, "get_price_at", None):
            t0 = large_trades[0].get("timestamp")
            if t0:
                before = t0 - timedelta(hours=1) if hasattr(t0, "__sub__") else None
                after = t0 + timedelta(hours=6) if hasattr(t0, "__add__") else None
                price_before = await self.db.get_price_at(market_id, before) if before else None
                price_after = await self.db.get_price_at(market_id, after) if after else None
                current = await self.db.get_price_at(market_id, datetime.now(timezone.utc))
                if price_before is not None and price_after is not None and current is not None:
                    initial_move = price_after - price_before
                    current_move = current - price_before
                    if abs(initial_move) > 0.05 and abs(current_move) < abs(initial_move) * 0.5:
                        reversion_score = 1

        persuasion_score = min(splash_score * 0.4 + reversion_score * 0.6, 1.0)
        return {
            "persuasion_score": persuasion_score,
            "splash_trades": len(large_trades),
            "follower_pattern": splash_score > 0,
            "price_reverted": reversion_score > 0,
            "confidence": min(len(trades) / 20, 1.0),
        }


# --- Element 2: Adverse selection tracker (in-memory fill history + DB price_at) ---


class AdverseSelectionTracker:
    """
    Tracks whether our fills are followed by price moving against us (adversely selected).
    """

    MAX_FILL_HISTORY = 10_000

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self.fill_history: List[Dict[str, Any]] = []

    async def restore_fills_from_db(self, limit: int = 500) -> int:
        """
        Restore fill history from fill_analysis table on startup.
        Prevents data loss across restarts (Phase 6: fill analysis persistence).
        Returns number of fills restored.
        """
        if not self.db or not hasattr(self.db, "load_recent_fills"):
            return 0
        try:
            rows = await self.db.load_recent_fills(limit=limit)
            restored = 0
            for row in rows:
                # Skip duplicates (same market_id + fill_time)
                existing = any(
                    f.get("market_id") == row.get("market_id")
                    and f.get("fill_time") == row.get("fill_time")
                    and f.get("fill_price") == row.get("fill_price")
                    for f in self.fill_history
                )
                if not existing:
                    self.fill_history.append(row)
                    restored += 1
            logger.debug("Restored %d fills from DB (total in memory: %d)", restored, len(self.fill_history))
            return restored
        except Exception as e:
            logger.debug("restore_fills_from_db failed: %s", e)
            return 0

    def record_fill(
        self,
        market_id: str,
        side: str,
        fill_price: float,
        fill_time: datetime,
        order_type: str = "market",
        source_bot: str = "",
    ) -> None:
        self.fill_history.append({
            "market_id": market_id,
            "side": side.upper(),
            "fill_price": fill_price,
            "fill_time": fill_time,
            "order_type": order_type,
            "source_bot": source_bot,
            "post_fill_price": None,
        })
        if len(self.fill_history) > self.MAX_FILL_HISTORY:
            self.fill_history = self.fill_history[-self.MAX_FILL_HISTORY:]

    # Window for post-fill price (fill_analysis columns price_30s/adverse_move_30s use this; names are legacy)
    POST_FILL_WINDOW_MINUTES = 30

    async def update_post_fill_prices(self) -> None:
        """Set post_fill_price for each fill at POST_FILL_WINDOW_MINUTES after fill (for adverse_move in fill_analysis)."""
        if not self.db or not getattr(self.db, "get_price_at", None):
            return
        for fill in self.fill_history:
            if fill.get("post_fill_price") is not None:
                continue
            t = fill.get("fill_time")
            if not t:
                continue
            after = t + timedelta(minutes=self.POST_FILL_WINDOW_MINUTES) if hasattr(t, "__add__") else None
            if after:
                post = await self.db.get_price_at(fill["market_id"], after)
                if post is not None:
                    fill["post_fill_price"] = post

    async def persist_fills_to_db(self) -> int:
        """
        Persist in-memory fills to fill_analysis table (P2A-10).
        Only persists fills that have post-fill prices (at POST_FILL_WINDOW_MINUTES after fill).
        Table columns price_30s / adverse_move_30s are named for legacy reasons; both use 30-minute window.
        Returns number of rows written.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return 0
        count = 0
        try:
            from sqlalchemy import text
            for fill in self.fill_history:
                if fill.get("post_fill_price") is None or fill.get("_persisted"):
                    continue
                adverse_30s = None
                if fill.get("post_fill_price") is not None:
                    side = (fill.get("side") or "YES").upper()
                    if side == "YES":
                        adverse_30s = -(fill["post_fill_price"] - fill["fill_price"])
                    else:
                        adverse_30s = -(fill["fill_price"] - fill["post_fill_price"])
                async with self.db.get_session() as session:
                    await session.execute(text("""
                        INSERT INTO fill_analysis
                        (market_id, source_bot, fill_price, fill_side, fill_time,
                         price_30s, adverse_move_30s)
                        VALUES (:mid, :bot, :fp, :side, :ft, :p30, :am30)
                    """), {
                        "mid": fill.get("market_id"),
                        "bot": fill.get("source_bot", ""),
                        "fp": fill["fill_price"],
                        "side": fill.get("side", "YES"),
                        "ft": fill["fill_time"],
                        "p30": fill.get("post_fill_price"),
                        "am30": adverse_30s,
                    })
                    await session.commit()
                fill["_persisted"] = True
                count += 1
        except Exception as e:
            logger.debug("fill_analysis persist failed: %s", e)
        return count

    def compute_adverse_selection_score(self, lookback_days: int = 7) -> Dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        recent = [
            f
            for f in self.fill_history
            if f.get("fill_time") and f["fill_time"] > cutoff and f.get("post_fill_price") is not None
        ]
        if not recent:
            return {"score": 0, "n_fills": 0}
        adverse_moves = []
        for fill in recent:
            side = (fill.get("side") or "YES").upper()
            post = fill["post_fill_price"]
            fill_p = fill["fill_price"]
            if side == "YES":
                move = post - fill_p
                adverse_moves.append(-move)
            else:
                move = fill_p - post
                adverse_moves.append(-move)
        avg_adverse = sum(adverse_moves) / len(adverse_moves)
        pct_adverse = sum(1 for m in adverse_moves if m > 0) / len(adverse_moves)
        return {
            "score": avg_adverse,
            "n_fills": len(recent),
            "pct_adverse": pct_adverse,
            "avg_adverse_magnitude": (
                sum(m for m in adverse_moves if m > 0) / max(sum(1 for m in adverse_moves if m > 0), 1)
                if any(m > 0 for m in adverse_moves)
                else 0
            ),
        }


# --- Element 7: Smart limit order price (no new data) ---


class SmartOrderPlacer:
    """
    Compute limit price from predicted prob and urgency (market order = urgency 1).
    """

    def compute_limit_price(
        self,
        predicted_prob: float,
        current_price: float,
        side: str,
        urgency: float = 0.5,
    ) -> float:
        side = (side or "YES").upper()
        if side == "YES":
            max_price = predicted_prob * 0.95
            min_price = current_price * 0.90
        else:
            max_price = (1 - predicted_prob) * 0.95
            min_price = (1 - current_price) * 0.90
        limit = min_price + (max_price - min_price) * urgency
        return round(limit, 4)

    def determine_urgency(
        self,
        market: Dict[str, Any],
        signal: Dict[str, Any],
    ) -> float:
        edge = abs(signal.get("edge", 0))
        edge_factor = min(edge / 0.15, 1.0)
        vol = signal.get("recent_volatility", 0)
        vol_factor = min(vol / 0.05, 1.0)
        hours_left = market.get("hours_until_resolution")
        time_factor = 0.5
        if hours_left is not None:
            if hours_left < 24:
                time_factor = 0.9
            elif hours_left < 168:
                time_factor = 0.6
            else:
                time_factor = 0.3
        return edge_factor * 0.4 + vol_factor * 0.3 + time_factor * 0.3


# --- Element 8: Minimax regret position sizing ---


class MinimaxPositioner:
    """
    Scale position by model confidence to bound worst-case regret.
    """

    def compute_hedged_position(
        self,
        predicted_prob: float,
        market_price: float,
        model_confidence: float,
    ) -> Dict[str, Any]:
        kelly_side = "YES" if predicted_prob > market_price else "NO"
        edge = abs(predicted_prob - market_price)
        if kelly_side == "YES":
            b = (1 - market_price) / max(market_price, 0.01)
            kelly_fraction = max((b * predicted_prob - (1 - predicted_prob)) / b, 0)
        else:
            b = market_price / max(1 - market_price, 0.01)
            kelly_fraction = max((b * (1 - predicted_prob) - predicted_prob) / b, 0)
        adjusted_fraction = kelly_fraction * model_confidence
        return {
            "side": kelly_side,
            "kelly_fraction": kelly_fraction,
            "adjusted_fraction": adjusted_fraction,
            "max_loss_if_wrong": adjusted_fraction * market_price,
            "max_regret_if_skip": kelly_fraction * edge * model_confidence,
        }
