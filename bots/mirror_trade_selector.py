"""
MirrorBot Offline RL Trade Selector — Session 82 Scaffold.

Uses d3rlpy IQL (Implicit Q-Learning) to learn which elite trades to copy
from historical paper_trades data. IQL is chosen per the blueprint because
it handles mixed-quality training data without Q-function maximization.

STATUS: SCAFFOLD — not wired to live trading. Requires:
  1. pip install d3rlpy (not in requirements.txt yet)
  2. 500+ resolved MirrorBot trades for meaningful training
  3. MIRROR_USE_RL_SELECTOR=true env var to activate

Architecture:
  State:  (trader_rank, efficiency, category_onehot, price, ttr_days, position_count)
  Action: binary (0=skip, 1=copy)
  Reward: log(wealth_ratio) with calibration penalty (log-wealth reward from blueprint)

Reference: d3rlpy IQL — extracts policies from mixed-quality data without
Q-function maximization. Computationally cheapest offline RL algorithm,
enabling rapid retraining as new events appear.
"""
import math
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()

# Feature dimensions
STATE_DIM = 10  # trader_rank, efficiency, price, ttr_days, pos_count, 5x category onehot
ACTION_DIM = 2  # skip=0, copy=1
CATEGORY_MAP = {"politics": 0, "crypto": 1, "sports": 2, "weather": 3, "other": 4}


def _encode_state(
    trader_rank: int,
    efficiency: float,
    price: float,
    ttr_days: float,
    position_count: int,
    category: str = "",
) -> List[float]:
    """Encode trade context into fixed-size state vector."""
    # Normalize features to [0, 1] range
    rank_norm = min(1.0, trader_rank / 1000.0)
    eff_norm = min(1.0, max(0.0, efficiency))
    price_norm = max(0.0, min(1.0, price))
    ttr_norm = min(1.0, max(0.0, ttr_days / 365.0))
    pos_norm = min(1.0, position_count / 200.0)

    # Category one-hot (5 dims)
    cat_onehot = [0.0] * 5
    cat_idx = CATEGORY_MAP.get(category.lower().strip(), 4)
    cat_onehot[cat_idx] = 1.0

    return [rank_norm, eff_norm, price_norm, ttr_norm, pos_norm] + cat_onehot


def compute_log_wealth_reward(
    entry_price: float,
    exit_price: float,
    size_usd: float,
    side: str,
    calibration_penalty: float = 0.0,
    lambda_cal: float = 0.2,
) -> float:
    """
    Log-wealth reward with calibration-aware shaping.

    R_t = log(W_{t+1} / W_t) - lambda * CalibrationPenalty_t

    Kelly (1956) and Breiman (1961) proved maximizing E[log(wealth)]
    maximizes long-term geometric growth rate.

    Args:
        entry_price: Price at which position was opened.
        exit_price: Price at which position was closed (or resolved).
        size_usd: Position size in USD.
        side: "YES" or "NO".
        calibration_penalty: Rolling Brier score for this trade.
        lambda_cal: Weight for calibration penalty (0.1-0.3 recommended).

    Returns:
        Scalar reward (can be negative).
    """
    if size_usd <= 0 or entry_price <= 0 or exit_price <= 0:
        return 0.0

    # P&L depends on side
    if side.upper() == "YES":
        pnl_ratio = (exit_price - entry_price) / entry_price
    else:
        pnl_ratio = (entry_price - exit_price) / entry_price

    # Wealth ratio: W_{t+1}/W_t = 1 + (size/portfolio) * pnl_ratio
    # Simplified: assume size is fraction of portfolio
    wealth_ratio = max(1e-6, 1.0 + pnl_ratio)

    r = math.log(wealth_ratio) - lambda_cal * calibration_penalty
    return r


class MirrorTradeSelector:
    """
    Offline RL trade selector using d3rlpy IQL.

    Scaffold implementation — training and inference methods are defined
    but d3rlpy import is deferred and guarded.
    """

    def __init__(self, db: Any = None):
        self._db = db
        self._model = None
        self._fitted = False

    async def build_dataset(self, n_days: int = 90) -> Optional[Any]:
        """
        Build offline RL dataset from resolved MirrorBot paper_trades.

        Returns d3rlpy MDPDataset or None if insufficient data or d3rlpy not installed.
        """
        if not self._db or not getattr(self._db, "session_factory", None):
            return None

        try:
            import d3rlpy  # noqa: F401
        except ImportError:
            logger.info("mirror_rl: d3rlpy not installed — scaffold only")
            return None

        try:
            from sqlalchemy import text
            import numpy as np

            async with self._db.get_session() as session:
                rows = await session.execute(text(
                    "SELECT pt.price, pt.confidence, pt.realized_pnl, "
                    "  pt.side, pt.size, "
                    "  LOWER(COALESCE(m.category, 'other')) AS category, "
                    "  EXTRACT(EPOCH FROM (m.end_date_iso - pt.created_at)) / 86400.0 AS ttr_days "
                    "FROM paper_trades pt "
                    "JOIN markets m ON pt.market_id = CAST(m.id AS TEXT) "
                    "WHERE pt.bot_name = 'MirrorBot' "
                    "  AND pt.realized_pnl IS NOT NULL "
                    "  AND pt.side IN ('YES', 'NO') "
                    "  AND LOWER(pt.side) != 'sell' "
                    "  AND pt.created_at > NOW() - INTERVAL '" + str(int(n_days)) + " days' "
                    "ORDER BY pt.created_at"
                ))
                data = rows.fetchall()

            if len(data) < 50:
                logger.info("mirror_rl: insufficient trades (%d/50)", len(data))
                return None

            # Build episodes
            observations = []
            actions = []
            rewards = []
            terminals = []

            for row in data:
                entry_price = float(row[0] or 0.5)
                confidence = float(row[1] or 0.55)
                pnl = float(row[2])
                side = str(row[3])
                size = float(row[4] or 0)
                category = str(row[5] or "other")
                ttr_days = float(row[6]) if row[6] is not None else 30.0

                state = _encode_state(
                    trader_rank=500,  # Placeholder (not stored in paper_trades)
                    efficiency=0.01,  # Placeholder
                    price=entry_price,
                    ttr_days=ttr_days,
                    position_count=50,  # Placeholder
                    category=category,
                )
                observations.append(state)
                actions.append([1])  # All historical trades were "copy" actions
                reward = compute_log_wealth_reward(
                    entry_price=entry_price,
                    exit_price=entry_price + (pnl / max(size, 1)),
                    size_usd=size * entry_price,
                    side=side,
                )
                rewards.append(reward)
                terminals.append(1.0)  # Each trade is a single-step episode

            obs_arr = np.array(observations, dtype=np.float32)
            act_arr = np.array(actions, dtype=np.float32)
            rew_arr = np.array(rewards, dtype=np.float32)
            term_arr = np.array(terminals, dtype=np.float32)

            dataset = d3rlpy.dataset.MDPDataset(
                observations=obs_arr,
                actions=act_arr,
                rewards=rew_arr,
                terminals=term_arr,
            )
            logger.info("mirror_rl: dataset built", n_episodes=len(data))
            return dataset

        except Exception as e:
            logger.warning("mirror_rl: dataset build failed", error=str(e))
            return None

    async def train(self, n_days: int = 90, n_steps: int = 10000) -> bool:
        """Train IQL model on historical data. Returns True if trained."""
        try:
            import d3rlpy
        except ImportError:
            return False

        dataset = await self.build_dataset(n_days)
        if dataset is None:
            return False

        try:
            self._model = d3rlpy.algos.IQLConfig(
                batch_size=64,
                learning_rate=3e-4,
                expectile=0.7,  # IQL expectile for implicit Q-learning
            ).create(device="cpu:0")

            self._model.fit(dataset, n_steps=n_steps)
            self._fitted = True
            logger.info("mirror_rl: IQL trained", n_steps=n_steps)
            return True

        except Exception as e:
            logger.warning("mirror_rl: training failed", error=str(e))
            return False

    def should_copy(
        self,
        trader_rank: int,
        efficiency: float,
        price: float,
        ttr_days: float,
        position_count: int,
        category: str = "",
    ) -> bool:
        """Predict whether to copy this trade. Returns True if model recommends copy."""
        if not self._fitted or self._model is None:
            return True  # Default: copy everything (existing behavior)

        try:
            import numpy as np
            state = _encode_state(trader_rank, efficiency, price, ttr_days, position_count, category)
            obs = np.array([state], dtype=np.float32)
            action = self._model.predict(obs)
            return int(action[0]) == 1
        except Exception:
            return True  # Fallback: copy
