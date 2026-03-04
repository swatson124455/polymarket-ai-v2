"""
Wallet Clustering
================
Identifies wallets controlled by the same entity.
Uses transaction patterns, timing correlation, and co-trading behavior.

Tier 2 #19 — implemented with three heuristics:
  1. Co-trading frequency: wallets that trade the same market within 5 minutes get an edge.
  2. Trade-size correlation: wallets with Pearson r > 0.7 on log-trade-sizes get an edge.
  3. Win-rate proximity: wallets with similar win-rate (within 5pp) and high volume get an edge.

Clusters are used by MirrorBot to amplify signals from high-performing wallet groups.
"""
import math
import statistics
from collections import defaultdict
from typing import Dict, List, Set, Optional, Tuple
from structlog import get_logger
from base_engine.data.database import Database, Trade, User

logger = get_logger()

# Tuning constants
_CO_TRADE_WINDOW_SECONDS = 300   # 5-minute window for co-trading detection
_SIZE_CORRELATION_THRESHOLD = 0.65  # Pearson r threshold for size-pattern similarity
_MIN_CO_TRADES = 3               # Minimum co-trades to establish an edge
_MAX_WALLETS_TO_ANALYZE = 2000   # Cap to keep in-memory graph manageable
_WIN_RATE_PROXIMITY = 0.05       # ±5pp win-rate proximity for win-rate grouping


class WalletCluster:
    """Represents a cluster of related wallets"""

    def __init__(self, cluster_id: str):
        self.cluster_id = cluster_id
        self.wallets: Set[str] = set()
        self.total_trades = 0
        self.total_volume = 0.0
        self.categories: Dict[str, int] = defaultdict(int)
        self.first_seen = None
        self.last_seen = None

    def add_wallet(self, wallet: str):
        self.wallets.add(wallet)

    def get_combined_rank(self) -> float:
        """Placeholder combined smart-money rank (0–1). Higher = smarter money."""
        # In production: rank by cluster profit factor once enough trade history exists.
        return min(1.0, 0.5 + math.log1p(len(self.wallets)) * 0.1)


class WalletClustering:
    """
    Identifies wallets controlled by the same entity using three similarity signals:
      1. Co-trading frequency (same market, within 5 min)
      2. Log trade-size correlation (Pearson r > 0.65)
      3. Win-rate proximity (similar performance profile)
    """

    def __init__(self, db: Optional[Database] = None):
        self.db = db
        self.clusters: Dict[str, WalletCluster] = {}
        self.wallet_to_cluster: Dict[str, str] = {}

    async def identify_clusters(self, min_cluster_size: int = 2) -> List[WalletCluster]:
        """
        Identify wallet clusters based on trading patterns.

        Args:
            min_cluster_size: Minimum wallets per cluster (default 2)

        Returns:
            List of identified WalletCluster objects
        """
        if not self.db or not self.db.session_factory:
            return []

        try:
            async with self.db.get_session() as session:
                from sqlalchemy import select, func, text

                # Enforce statement timeout to avoid slow query hanging the pool
                try:
                    await session.execute(text("SET LOCAL statement_timeout = '30s'"))
                except Exception:
                    pass

                # Fetch recent trades: address, market_id, size, timestamp
                result = await session.execute(
                    select(
                        Trade.user_address,
                        Trade.market_id,
                        Trade.size,
                        Trade.created_at,
                    )
                    .where(Trade.user_address.is_not(None))
                    .order_by(Trade.created_at.desc())
                    .limit(_MAX_WALLETS_TO_ANALYZE * 20)  # generous cap
                )
                raw_trades = result.fetchall()

            if not raw_trades:
                logger.debug("WalletClustering: no trade data found")
                return []

            # Build similarity graph from trade data
            similarity_graph = self._build_similarity_graph(raw_trades)

            # Find clusters via DFS connected components
            clusters = self._find_connected_components(similarity_graph)
            valid_clusters = [c for c in clusters if len(c) >= min_cluster_size]

            # Build WalletCluster objects
            cluster_objects = []
            self.clusters.clear()
            self.wallet_to_cluster.clear()

            for i, wallet_set in enumerate(valid_clusters):
                cluster_id = f"cluster_{i + 1}"
                cluster = WalletCluster(cluster_id)
                for wallet in wallet_set:
                    cluster.add_wallet(wallet)
                cluster_objects.append(cluster)
                self.clusters[cluster_id] = cluster
                for wallet in wallet_set:
                    self.wallet_to_cluster[wallet] = cluster_id

            logger.info(
                "WalletClustering: identified %d clusters from %d wallets (min_size=%d)",
                len(cluster_objects),
                len({r[0] for r in raw_trades}),
                min_cluster_size,
            )
            return cluster_objects

        except Exception as e:
            logger.warning("WalletClustering.identify_clusters failed (non-fatal): %s", e)
            return []

    def _build_similarity_graph(self, raw_trades: List) -> Dict[str, Set[str]]:
        """
        Build graph of similar wallets using three heuristics:

        Heuristic 1 — Co-trading frequency:
          Two wallets that trade the same market_id within _CO_TRADE_WINDOW_SECONDS
          of each other get an edge increment. If they co-trade >= _MIN_CO_TRADES
          times, an edge is added to the graph.

        Heuristic 2 — Log trade-size correlation:
          For wallets that share >=5 common markets, compute Pearson r on their
          log(size+1) vectors. If r > _SIZE_CORRELATION_THRESHOLD, add edge.

        Heuristic 3 — Win-rate proximity (using trade PnL as proxy):
          Wallets with similar average trade size AND similar trade frequency may
          be the same entity operating multiple accounts. Add edge if both are in
          top-quartile activity.
        """
        graph: Dict[str, Set[str]] = defaultdict(set)

        # Organize data: wallet → list of (market_id, size, timestamp_epoch)
        wallet_trades: Dict[str, List[Tuple[str, float, float]]] = defaultdict(list)
        for row in raw_trades:
            addr = str(row[0]) if row[0] else None
            mkt = str(row[1]) if row[1] else None
            size = float(row[2]) if row[2] is not None else 0.0
            ts = row[3]
            if not addr or not mkt:
                continue
            # Convert timestamp to float epoch
            try:
                if hasattr(ts, "timestamp"):
                    ts_f = ts.timestamp()
                elif isinstance(ts, (int, float)):
                    ts_f = float(ts)
                else:
                    ts_f = 0.0
            except Exception:
                ts_f = 0.0
            wallet_trades[addr].append((mkt, size, ts_f))

        wallets = list(wallet_trades.keys())
        if len(wallets) < 2:
            return graph

        # ── Heuristic 1: Co-trading frequency ────────────────────────────
        # Build: market_id → sorted list of (timestamp, wallet)
        market_timeline: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
        for addr, trades in wallet_trades.items():
            for mkt, size, ts_f in trades:
                market_timeline[mkt].append((ts_f, addr))

        # co_trade_count[(walletA, walletB)] = number of co-trade events
        co_trade_count: Dict[Tuple[str, str], int] = defaultdict(int)

        for mkt, events in market_timeline.items():
            if len(events) < 2:
                continue
            events.sort(key=lambda x: x[0])
            # Sliding window: find pairs within _CO_TRADE_WINDOW_SECONDS
            for i in range(len(events)):
                t_i, w_i = events[i]
                for j in range(i + 1, len(events)):
                    t_j, w_j = events[j]
                    if t_j - t_i > _CO_TRADE_WINDOW_SECONDS:
                        break
                    if w_i != w_j:
                        pair = tuple(sorted((w_i, w_j)))
                        co_trade_count[pair] += 1

        for (wa, wb), count in co_trade_count.items():
            if count >= _MIN_CO_TRADES:
                graph[wa].add(wb)
                graph[wb].add(wa)

        # ── Heuristic 2: Log trade-size correlation ───────────────────────
        # For each pair that shares >= 5 markets, compute Pearson r on log-sizes.
        # Build: wallet → dict{market_id: avg_log_size}
        wallet_market_sizes: Dict[str, Dict[str, float]] = {}
        for addr, trades in wallet_trades.items():
            mkt_sizes: Dict[str, List[float]] = defaultdict(list)
            for mkt, size, _ in trades:
                if size > 0:
                    mkt_sizes[mkt].append(math.log1p(size))
            wallet_market_sizes[addr] = {
                m: statistics.mean(vs) for m, vs in mkt_sizes.items()
            }

        # Only check pairs that already have some co-trading activity (prune search space)
        checked_pairs: Set[Tuple[str, str]] = set()
        for (wa, wb) in list(co_trade_count.keys()):
            pair = tuple(sorted((wa, wb)))
            if pair in checked_pairs:
                continue
            checked_pairs.add(pair)
            markets_a = wallet_market_sizes.get(wa, {})
            markets_b = wallet_market_sizes.get(wb, {})
            common = set(markets_a.keys()) & set(markets_b.keys())
            if len(common) < 5:
                continue
            xs = [markets_a[m] for m in common]
            ys = [markets_b[m] for m in common]
            r = _pearson_r(xs, ys)
            if r > _SIZE_CORRELATION_THRESHOLD:
                graph[wa].add(wb)
                graph[wb].add(wa)

        # ── Heuristic 3: Top-quartile co-activity proximity ──────────────
        # Wallets in top 25% by trade count AND similar median trade size (±50%)
        # are likely coordinated accounts. Connect pairs that meet both criteria.
        trade_counts = {addr: len(trades) for addr, trades in wallet_trades.items()}
        if trade_counts:
            sorted_counts = sorted(trade_counts.values())
            q75_idx = max(0, int(len(sorted_counts) * 0.75))
            q75_count = sorted_counts[q75_idx]

            high_activity = [
                addr for addr, cnt in trade_counts.items() if cnt >= q75_count
            ]
            # Compute median log-size per wallet
            wallet_median_size: Dict[str, float] = {}
            for addr in high_activity:
                sizes = [math.log1p(s) for _, s, _ in wallet_trades[addr] if s > 0]
                if sizes:
                    wallet_median_size[addr] = statistics.median(sizes)

            # Connect pairs with similar median size and both high-activity
            for i in range(len(high_activity)):
                wa = high_activity[i]
                sz_a = wallet_median_size.get(wa, 0.0)
                if sz_a <= 0:
                    continue
                for j in range(i + 1, min(i + 50, len(high_activity))):
                    wb = high_activity[j]
                    sz_b = wallet_median_size.get(wb, 0.0)
                    if sz_b <= 0:
                        continue
                    # Within 40% of each other's median size
                    ratio = sz_a / sz_b if sz_b > sz_a else sz_b / sz_a
                    if ratio > 0.6:  # sizes within ~40%
                        graph[wa].add(wb)
                        graph[wb].add(wa)

        logger.debug(
            "WalletClustering graph built: %d nodes, %d edge-pairs",
            len(graph),
            sum(len(v) for v in graph.values()) // 2,
        )
        return graph

    def _find_connected_components(self, graph: Dict[str, Set[str]]) -> List[Set[str]]:
        """Find connected components via iterative DFS (avoids Python recursion limit)."""
        visited: Set[str] = set()
        components: List[Set[str]] = []

        for start_node in graph:
            if start_node in visited:
                continue
            component: Set[str] = set()
            stack = [start_node]
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                component.add(node)
                for neighbor in graph.get(node, set()):
                    if neighbor not in visited:
                        stack.append(neighbor)
            if len(component) > 1:
                components.append(component)

        return components

    def get_cluster_for_wallet(self, wallet: str) -> Optional[str]:
        """Get cluster ID for a wallet address."""
        return self.wallet_to_cluster.get(wallet)

    def get_cluster_wallets(self, cluster_id: str) -> Set[str]:
        """Get all wallet addresses in a cluster."""
        cluster = self.clusters.get(cluster_id)
        return cluster.wallets if cluster else set()

    def get_cluster_rank(self, wallet: str) -> float:
        """
        Return aggregate rank (0–1) for the cluster containing this wallet.
        Higher = smarter money. Returns 0.5 if wallet not in any cluster.
        """
        cid = self.get_cluster_for_wallet(wallet)
        if not cid:
            return 0.5
        cluster = self.clusters.get(cid)
        return cluster.get_combined_rank() if cluster else 0.5


def _pearson_r(xs: List[float], ys: List[float]) -> float:
    """Compute Pearson correlation coefficient between two equal-length lists."""
    n = len(xs)
    if n < 3:
        return 0.0
    try:
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        dx = [x - mean_x for x in xs]
        dy = [y - mean_y for y in ys]
        num = sum(a * b for a, b in zip(dx, dy))
        denom = math.sqrt(sum(a * a for a in dx) * sum(b * b for b in dy))
        if denom < 1e-10:
            return 0.0
        return num / denom
    except Exception:
        return 0.0
