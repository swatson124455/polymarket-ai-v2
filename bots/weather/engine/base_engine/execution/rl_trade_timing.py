"""
RL Trade Timing Agent — Tabular Q-learning with Prioritized Experience Replay and adaptive drift detection.

Learns WHEN to trade by observing paper trade outcomes. Sits as an optional pre-filter
in OrderGateway: given current market conditions, should we trade now, wait, or skip?

Design rationale:
- 324 discrete states x 3 actions = 972 Q-values (~8KB). Tabular Q-learning converges
  faster and uses far less memory than deep RL (SB3 = 100-300MB per agent).
- Prioritized Experience Replay (PER) ensures high-error transitions are replayed more often.
- ADWIN-inspired adaptive drift detection catches regime shifts in the reward distribution.

Memory footprint: ~250KB total (Q-table + visits + replay buffer + SumTree).
"""
import asyncio
import math
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from structlog import get_logger

logger = get_logger()

# Actions
TRADE_NOW = 0
WAIT = 1
SKIP = 2
ACTION_NAMES = {TRADE_NOW: "TRADE_NOW", WAIT: "WAIT", SKIP: "SKIP"}

# State dimensions: confidence(3) x spread(3) x volatility(3) x regime(3) x hour_bucket(4) = 324
_DIM_SIZES = (3, 3, 3, 3, 4)
_N_STATES = math.prod(_DIM_SIZES)  # 324
_N_ACTIONS = 3


# ---------------------------------------------------------------------------
# SumTree for Prioritized Experience Replay
# ---------------------------------------------------------------------------

class _SumTree:
    """
    Binary sum tree for O(log n) priority-based sampling.
    Leaf nodes store priorities; internal nodes store sums.
    """

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.tree = np.zeros(2 * capacity)
        self.data: List[Optional[Any]] = [None] * capacity
        self.write_idx = 0
        self.size = 0

    @property
    def total(self) -> float:
        return float(self.tree[1])

    def add(self, priority: float, data: Any) -> None:
        idx = self.write_idx + self.capacity
        self.data[self.write_idx] = data
        self._update(idx, priority)
        self.write_idx = (self.write_idx + 1) % self.capacity
        self.size = min(self.size + 1, self.capacity)

    def _update(self, idx: int, priority: float) -> None:
        change = priority - self.tree[idx]
        self.tree[idx] = priority
        while idx > 1:
            idx //= 2
            self.tree[idx] += change

    def get(self, cumsum: float) -> Tuple[int, float, Any]:
        """Retrieve leaf by cumulative sum. Returns (leaf_idx, priority, data)."""
        idx = 1
        while idx < self.capacity:
            left = 2 * idx
            right = left + 1
            if cumsum <= self.tree[left]:
                idx = left
            else:
                cumsum -= self.tree[left]
                idx = right
        data_idx = idx - self.capacity
        return data_idx, float(self.tree[idx]), self.data[data_idx]

    def update_priority(self, data_idx: int, priority: float) -> None:
        tree_idx = data_idx + self.capacity
        self._update(tree_idx, priority)


# ---------------------------------------------------------------------------
# Prioritized Experience Replay Buffer
# ---------------------------------------------------------------------------

class PrioritizedReplayBuffer:
    """
    PER buffer using SumTree. Replays high-error transitions more often.

    Args:
        capacity: Max transitions to store (default 2000)
        alpha: Prioritization exponent (0=uniform, 1=full priority). Default 0.6
        epsilon: Small constant added to priorities to prevent zero sampling
    """

    def __init__(self, capacity: int = 2000, alpha: float = 0.6, epsilon: float = 0.01):
        self.capacity = capacity
        self.alpha = alpha
        self.epsilon = epsilon
        self.tree = _SumTree(capacity)
        self._max_priority = 1.0
        self._beta = 0.4  # Importance sampling annealing start
        self._beta_increment = 0.001  # Per-sample annealing

    def add(self, transition: Tuple, td_error: Optional[float] = None) -> None:
        """Add transition with priority based on TD error."""
        if td_error is not None:
            priority = (abs(td_error) + self.epsilon) ** self.alpha
        else:
            priority = self._max_priority
        self.tree.add(priority, transition)

    def sample(self, batch_size: int = 32) -> Tuple[List[Tuple], np.ndarray, List[int]]:
        """
        Sample batch proportional to priority.

        Returns:
            transitions: List of (state, action, reward, next_state) tuples
            is_weights: Importance sampling weights (for bias correction)
            indices: Data indices for priority update
        """
        if self.tree.size == 0:
            return [], np.array([]), []

        batch_size = min(batch_size, self.tree.size)
        transitions = []
        indices = []
        priorities = []

        segment = self.tree.total / batch_size
        self._beta = min(1.0, self._beta + self._beta_increment)

        for i in range(batch_size):
            low = segment * i
            high = segment * (i + 1)
            cumsum = np.random.uniform(low, high)
            data_idx, priority, data = self.tree.get(cumsum)
            if data is not None:
                transitions.append(data)
                indices.append(data_idx)
                priorities.append(max(priority, 1e-8))

        if not transitions:
            return [], np.array([]), []

        # Importance sampling weights
        probs = np.array(priorities) / max(self.tree.total, 1e-8)
        is_weights = (self.tree.size * probs) ** (-self._beta)
        is_weights /= max(is_weights.max(), 1e-8)  # Normalize

        return transitions, is_weights, indices

    def update_priorities(self, indices: List[int], td_errors: np.ndarray) -> None:
        """Update priorities after Q-table updates."""
        for idx, td_error in zip(indices, td_errors):
            priority = (abs(td_error) + self.epsilon) ** self.alpha
            self._max_priority = max(self._max_priority, priority)
            self.tree.update_priority(idx, priority)

    @property
    def size(self) -> int:
        return self.tree.size


# ---------------------------------------------------------------------------
# Adaptive Reward Tracker (ADWIN-inspired)
# ---------------------------------------------------------------------------

class AdaptiveRewardTracker:
    """
    ADWIN-inspired adaptive windowing for regime shift detection.
    Detects when the reward distribution changes (gradual or sudden).

    Uses a two-window comparison: if the difference in means between
    two sub-windows exceeds a statistical threshold, drift is flagged.
    """

    def __init__(self, delta: float = 0.002, min_window: int = 30):
        self.delta = delta
        self.min_window = min_window
        self._window: List[float] = []
        self._sum = 0.0
        self._sum_sq = 0.0
        self._baseline_mean: Optional[float] = None
        self._baseline_std: Optional[float] = None
        # DDM-style sudden drift detection
        self._error_rate = 0.0
        self._error_rate_min = float("inf")
        self._error_rate_std = 0.0
        self._n_updates = 0

    def update(self, reward: float) -> Dict[str, Any]:
        """
        Feed new reward observation. Returns drift report.

        Returns:
            {"drift_detected": bool, "drift_type": "gradual"|"sudden"|None,
             "window_size": int, "current_mean": float}
        """
        self._window.append(reward)
        self._sum += reward
        self._sum_sq += reward * reward
        self._n_updates += 1

        report: Dict[str, Any] = {
            "drift_detected": False,
            "drift_type": None,
            "window_size": len(self._window),
            "current_mean": self._sum / max(len(self._window), 1),
        }

        if len(self._window) < self.min_window:
            return report

        # Set baseline from first full window
        if self._baseline_mean is None:
            self._baseline_mean = self._sum / len(self._window)
            variance = max(self._sum_sq / len(self._window) - self._baseline_mean ** 2, 0.0)
            self._baseline_std = max(math.sqrt(variance), 0.01)
            return report

        current_mean = self._sum / len(self._window)

        # DDM-style sudden drift: error rate spike > 3 sigma
        is_negative = 1.0 if reward < 0 else 0.0
        self._error_rate = self._error_rate + (is_negative - self._error_rate) / self._n_updates
        error_std = math.sqrt(max(self._error_rate * (1 - self._error_rate) / max(self._n_updates, 1), 1e-10))

        if self._error_rate + error_std < self._error_rate_min + self._error_rate_std:
            self._error_rate_min = self._error_rate
            self._error_rate_std = error_std

        if self._error_rate > self._error_rate_min + 3 * self._error_rate_std and self._n_updates > self.min_window:
            report["drift_detected"] = True
            report["drift_type"] = "sudden"
            return report

        # ADWIN-style gradual drift: mean shift beyond threshold
        if self._baseline_std > 0:
            z_score = abs(current_mean - self._baseline_mean) / self._baseline_std
            if z_score > 2.5:
                report["drift_detected"] = True
                report["drift_type"] = "gradual"
                # Shrink window to adapt
                half = len(self._window) // 2
                self._window = self._window[half:]
                self._sum = sum(self._window)
                self._sum_sq = sum(r * r for r in self._window)
                self._baseline_mean = current_mean
                _var = max(self._sum_sq / len(self._window) - current_mean ** 2, 0.0) if len(self._window) > 0 else 0.0
                self._baseline_std = max(math.sqrt(_var), 0.01)
                return report

        # Keep window bounded
        if len(self._window) > 500:
            removed = self._window.pop(0)
            self._sum -= removed
            self._sum_sq -= removed * removed

        return report

    def reset(self) -> None:
        """Reset tracker for new regime."""
        self._window.clear()
        self._sum = 0.0
        self._sum_sq = 0.0
        self._baseline_mean = None
        self._baseline_std = None
        self._error_rate = 0.0
        self._error_rate_min = float("inf")
        self._error_rate_std = 0.0
        self._n_updates = 0


# ---------------------------------------------------------------------------
# RL Trade Timing Agent
# ---------------------------------------------------------------------------

class RLTradeTimingAgent:
    """
    Tabular Q-learning agent for trade timing decisions.
    Features: PER replay, ADWIN-inspired drift detection, UCB exploration.

    State space: 324 discrete states (confidence x spread x volatility x regime x hour)
    Action space: 3 (TRADE_NOW, WAIT, SKIP)
    """

    def __init__(
        self,
        learning_rate: float = 0.1,
        discount_factor: float = 0.95,
        epsilon_start: float = 0.3,
        epsilon_min: float = 0.05,
        epsilon_decay_trades: int = 500,
        replay_buffer_size: int = 2000,
        replay_batch_size: int = 32,
    ):
        self.lr = learning_rate
        self._lr_base = learning_rate
        self.gamma = discount_factor
        self.epsilon = epsilon_start
        self._epsilon_start = epsilon_start
        self.epsilon_min = epsilon_min
        self.epsilon_decay_trades = epsilon_decay_trades
        self.replay_batch_size = replay_batch_size

        # Q-table and visit counts
        self.q_table = np.zeros((_N_STATES, _N_ACTIONS), dtype=np.float64)
        self.visit_counts = np.zeros((_N_STATES, _N_ACTIONS), dtype=np.int64)
        self._total_visits = 0

        # Prioritized Experience Replay
        self.replay_buffer = PrioritizedReplayBuffer(capacity=replay_buffer_size)

        # Adaptive drift detection
        self.drift_tracker = AdaptiveRewardTracker()

        # Track pending decisions (market_id -> (state_idx, action, timestamp))
        self._pending: Dict[str, Tuple[int, int, float]] = {}

        # Concurrency lock
        self._lock = asyncio.Lock()

        # Stats
        self._total_trades = 0
        self._total_reward = 0.0
        self._drift_events = 0
        self._actions_taken = {TRADE_NOW: 0, WAIT: 0, SKIP: 0}

    async def decide(self, market_state: Dict[str, Any]) -> Tuple[int, float]:
        """
        Decide whether to trade now, wait, or skip.

        Args:
            market_state: Dict with keys: confidence, spread, volatility, regime, hour

        Returns:
            (action, q_value) where action is 0=TRADE_NOW, 1=WAIT, 2=SKIP
        """
        async with self._lock:
            try:
                state_idx = self._discretize_state(market_state)
            except Exception:
                return TRADE_NOW, 0.0  # Graceful degradation

            # Epsilon-greedy with UCB exploration bonus
            if np.random.random() < self.epsilon:
                action = np.random.randint(_N_ACTIONS)
            else:
                q_values = self.q_table[state_idx].copy()

                # UCB exploration bonus for unvisited states
                if self._total_visits > 0:
                    for a in range(_N_ACTIONS):
                        visits = max(self.visit_counts[state_idx, a], 1)
                        ucb_bonus = 1.0 * math.sqrt(math.log(self._total_visits + 1) / visits)
                        q_values[a] += ucb_bonus

                action = int(np.argmax(q_values))

            q_value = float(self.q_table[state_idx, action])

            # Store pending decision for outcome tracking
            market_id = market_state.get("market_id", "")
            if market_id:
                self._pending[market_id] = (state_idx, action, time.time())

            self._actions_taken[action] = self._actions_taken.get(action, 0) + 1
            return action, q_value

    def record_outcome(
        self, state_idx: int, action: int, reward: float, next_state_idx: Optional[int] = None
    ) -> None:
        """
        Update Q-table from observed outcome. Store in replay buffer. Batch replay.

        Q(s,a) += lr * (reward + gamma * max(Q(s')) - Q(s,a))
        """
        if next_state_idx is None:
            next_state_idx = state_idx  # Terminal or same state

        # TD error
        td_target = reward + self.gamma * float(np.max(self.q_table[next_state_idx]))
        td_error = td_target - self.q_table[state_idx, action]

        # Q-table update
        self.q_table[state_idx, action] += self.lr * td_error

        # Update visit counts
        self.visit_counts[state_idx, action] += 1
        self._total_visits += 1

        # Store in replay buffer
        transition = (state_idx, action, reward, next_state_idx)
        self.replay_buffer.add(transition, td_error)

        # Drift detection
        self._check_drift(reward)

        # Stats
        self._total_trades += 1
        self._total_reward += reward

        # Epsilon decay
        if self.epsilon_decay_trades > 0:
            decay_progress = min(self._total_trades / self.epsilon_decay_trades, 1.0)
            self.epsilon = self._epsilon_start - (self._epsilon_start - self.epsilon_min) * decay_progress

        # Batch replay every 10 updates
        if self._total_trades % 10 == 0 and self.replay_buffer.size >= self.replay_batch_size:
            self._replay_batch()

    def record_outcome_from_trade(self, market_id: str, pnl: float) -> None:
        """
        Convenience method: look up stored state/action for market_id, compute reward, update Q-table.

        Called by PaperTradingEngine callback on SELL execution.
        """
        if market_id not in self._pending:
            return

        state_idx, action, _ = self._pending.pop(market_id)

        # Reward scaling: map PnL to [-2, +2] range
        if action == TRADE_NOW:
            reward = np.clip(pnl / 5.0, -2.0, 2.0)
        elif action == WAIT:
            reward = 0.3 if pnl < 0 else -0.3  # Good if would have lost
        else:  # SKIP
            reward = 0.2 if pnl < 0 else -0.4  # Good if avoided loss

        self.record_outcome(state_idx, action, reward)

    def _replay_batch(self) -> None:
        """Sample from PER buffer and update Q-values."""
        transitions, is_weights, indices = self.replay_buffer.sample(self.replay_batch_size)

        if not transitions:
            return

        td_errors = np.zeros(len(transitions))

        for i, (s, a, r, s_next) in enumerate(transitions):
            td_target = r + self.gamma * float(np.max(self.q_table[s_next]))
            td_error = td_target - self.q_table[s, a]
            # IS-weighted update
            self.q_table[s, a] += self.lr * is_weights[i] * td_error
            td_errors[i] = td_error

        # Update priorities
        self.replay_buffer.update_priorities(indices, td_errors)

    def _check_drift(self, reward: float) -> None:
        """Feed reward to AdaptiveRewardTracker. Respond to drift events."""
        report = self.drift_tracker.update(reward)

        if not report["drift_detected"]:
            return

        self._drift_events += 1
        drift_type = report["drift_type"]

        if drift_type == "gradual":
            # Increase learning rate temporarily (2x), increase exploration
            self.lr = min(self._lr_base * 2.0, 0.5)
            self.epsilon = min(self.epsilon + 0.1, 0.5)
            logger.info(
                "RL drift (gradual): boosting lr=%.3f, epsilon=%.3f",
                self.lr, self.epsilon,
            )
        elif drift_type == "sudden":
            # Partial Q-table reset: zero out low-visit states
            median_visits = max(np.median(self.visit_counts[self.visit_counts > 0]), 1)
            mask = self.visit_counts < median_visits
            self.q_table[mask] = 0.0
            self.visit_counts[mask] = 0

            # Warm-start from replay buffer
            if self.replay_buffer.size >= self.replay_batch_size:
                for _ in range(5):  # 5 rounds of replay
                    self._replay_batch()

            # Reset drift tracker for new regime
            self.drift_tracker.reset()
            self.lr = self._lr_base
            logger.warning(
                "RL drift (sudden): partial Q-table reset, %d states cleared",
                int(mask.sum()),
            )

    def _discretize_state(self, market_state: Dict[str, Any]) -> int:
        """
        Convert continuous market features to discrete state index (0-323).

        State = confidence(3) x spread(3) x volatility(3) x regime(3) x hour_bucket(4)
        """
        # Confidence: low(0) / medium(1) / high(2)
        conf = float(market_state.get("confidence", 0.5))
        if conf < 0.6:
            d0 = 0
        elif conf < 0.75:
            d0 = 1
        else:
            d0 = 2

        # Spread: tight(0) / normal(1) / wide(2)
        spread = float(market_state.get("spread", 0.03))
        if spread < 0.02:
            d1 = 0
        elif spread < 0.05:
            d1 = 1
        else:
            d1 = 2

        # Volatility: low(0) / medium(1) / high(2)
        vol = float(market_state.get("volatility", 0.02))
        if vol < 0.01:
            d2 = 0
        elif vol < 0.03:
            d2 = 1
        else:
            d2 = 2

        # Regime: calm(0) / volatile(1) / trending(2)
        regime = str(market_state.get("regime", "CALM")).upper()
        if regime in ("VOLATILE", "HIGH_VOLATILITY"):
            d3 = 1
        elif regime in ("TRENDING", "MOMENTUM"):
            d3 = 2
        else:
            d3 = 0  # CALM / UNKNOWN / default

        # Hour bucket: asia(0) / europe(1) / us_morning(2) / us_afternoon(3)
        hour = int(market_state.get("hour", 12))
        if hour < 8:
            d4 = 0  # Asia/Pacific
        elif hour < 14:
            d4 = 1  # Europe
        elif hour < 19:
            d4 = 2  # US morning
        else:
            d4 = 3  # US afternoon/evening

        # Flatten to single index
        idx = ((d0 * _DIM_SIZES[1] + d1) * _DIM_SIZES[2] + d2) * _DIM_SIZES[3] + d3
        idx = idx * _DIM_SIZES[4] + d4

        return max(0, min(idx, _N_STATES - 1))

    def save(self, path: Path) -> None:
        """Save Q-table, visit counts, replay buffer, and stats to pickle file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "q_table": self.q_table,
                "visit_counts": self.visit_counts,
                "total_visits": self._total_visits,
                "total_trades": self._total_trades,
                "total_reward": self._total_reward,
                "drift_events": self._drift_events,
                "actions_taken": self._actions_taken,
                "epsilon": self.epsilon,
                "lr": self.lr,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(path, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.info(
                "RL agent saved: %d trades, epsilon=%.3f, reward=%.2f",
                self._total_trades, self.epsilon, self._total_reward,
            )
        except Exception as e:
            logger.debug("Failed to save RL agent: %s", e)

    def load(self, path: Path) -> bool:
        """Load Q-table from pickle file. Returns False if not found."""
        try:
            if not path.exists():
                logger.debug("No RL checkpoint found at %s", path)
                return False
            with open(path, "rb") as f:
                payload = pickle.load(f)
            self.q_table = payload.get("q_table", self.q_table)
            self.visit_counts = payload.get("visit_counts", self.visit_counts)
            self._total_visits = payload.get("total_visits", 0)
            self._total_trades = payload.get("total_trades", 0)
            self._total_reward = payload.get("total_reward", 0.0)
            self._drift_events = payload.get("drift_events", 0)
            self._actions_taken = payload.get("actions_taken", self._actions_taken)
            self.epsilon = payload.get("epsilon", self.epsilon)
            self.lr = payload.get("lr", self.lr)
            logger.info(
                "RL agent loaded: %d trades, epsilon=%.3f",
                self._total_trades, self.epsilon,
            )
            return True
        except Exception as e:
            logger.debug("Failed to load RL agent: %s", e)
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Return exploration stats for dashboard/monitoring."""
        non_zero_states = int(np.count_nonzero(self.visit_counts.sum(axis=1)))
        return {
            "total_trades": self._total_trades,
            "total_reward": round(self._total_reward, 2),
            "epsilon": round(self.epsilon, 4),
            "learning_rate": round(self.lr, 4),
            "states_explored": non_zero_states,
            "states_total": _N_STATES,
            "exploration_pct": round(non_zero_states / _N_STATES * 100, 1),
            "drift_events": self._drift_events,
            "replay_buffer_size": self.replay_buffer.size,
            "actions_taken": {ACTION_NAMES.get(k, str(k)): v for k, v in self._actions_taken.items()},
            "q_table_mean": round(float(np.mean(self.q_table)), 4),
            "q_table_std": round(float(np.std(self.q_table)), 4),
        }

    def cql_offline_pretrain(
        self,
        offline_data: List[Tuple[int, int, float, int]],
        cql_alpha: float = 1.0,
        n_epochs: int = 10,
    ) -> Dict[str, Any]:
        """
        Conservative Q-Learning (CQL) offline pre-training from logged decisions.
        Penalizes Q-values of actions NOT in the dataset to avoid overestimation.

        Args:
            offline_data: List of (state, action, reward, next_state) tuples
            cql_alpha: CQL regularization strength (higher = more conservative)
            n_epochs: Number of training epochs over the dataset

        Returns:
            Training stats dict
        """
        if not offline_data:
            return {"epochs": 0, "transitions": 0}

        total_cql_loss = 0.0
        total_td_loss = 0.0

        for epoch in range(n_epochs):
            np.random.shuffle(offline_data)
            for state, action, reward, next_state in offline_data:
                if not (0 <= state < _N_STATES and 0 <= next_state < _N_STATES):
                    continue
                if not (0 <= action < _N_ACTIONS):
                    continue

                # Standard Q-learning TD update
                best_next = np.max(self.q_table[next_state])
                td_target = reward + self.gamma * best_next
                td_error = td_target - self.q_table[state, action]
                self.q_table[state, action] += self.lr * td_error
                total_td_loss += abs(td_error)

                # CQL penalty: push down Q-values of all actions, push up dataset action
                # This prevents overestimation of unseen (state, action) pairs
                logsumexp_q = np.log(np.sum(np.exp(self.q_table[state])) + 1e-10)
                for a in range(_N_ACTIONS):
                    if a == action:
                        # Push UP the Q-value for the logged action
                        self.q_table[state, a] += self.lr * cql_alpha * 0.1
                    else:
                        # Push DOWN Q-values for non-logged actions (conservative)
                        self.q_table[state, a] -= self.lr * cql_alpha * (
                            np.exp(self.q_table[state, a]) / max(np.sum(np.exp(self.q_table[state])), 1e-10)
                        )

                self.visit_counts[state, action] += 1
                self._total_visits += 1

        logger.info(
            "CQL offline pre-training complete",
            epochs=n_epochs,
            transitions=len(offline_data),
            avg_td_loss=round(total_td_loss / max(len(offline_data) * n_epochs, 1), 4),
            states_explored=int(np.count_nonzero(self.visit_counts.sum(axis=1))),
        )

        return {
            "epochs": n_epochs,
            "transitions": len(offline_data),
            "states_explored": int(np.count_nonzero(self.visit_counts.sum(axis=1))),
        }
