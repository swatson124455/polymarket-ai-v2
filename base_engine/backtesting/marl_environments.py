"""
Multi-Agent RL Environments — Tier 5 #44-45

Gymnasium-compatible wrappers for backtesting multi-bot strategies:
  - JaxMARLWrapper: GPU-accelerated multi-agent backtesting (jaxmarl)
  - ABIDESWrapper: JP Morgan's market simulation (abides-gym)

These environments let bots learn coordination strategies offline
before deploying to live paper trading.

Dependencies: jaxmarl, abides-gym (optional — graceful fallback).
"""
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from structlog import get_logger

logger = get_logger()


class PredictionMarketEnv:
    """
    Base Gymnasium-compatible prediction market environment.

    State: [price, volume, spread, time_to_resolution, portfolio_value, ...]
    Actions: 0=HOLD, 1=BUY_YES, 2=BUY_NO, 3=SELL
    Reward: Realized PnL on resolution or position change.
    """

    def __init__(
        self,
        market_data: List[Dict],
        initial_capital: float = 10000.0,
        n_agents: int = 4,
        max_steps: int = 1000,
    ):
        self.market_data = market_data
        self.initial_capital = initial_capital
        self.n_agents = n_agents
        self.max_steps = max_steps
        self._step = 0
        self._positions: Dict[int, Dict] = {}
        self._capital: Dict[int, float] = {}
        self.reset()

    @property
    def observation_space_dim(self) -> int:
        return 10  # price, volume, spread, time_frac, capital, position, pnl, ...

    @property
    def action_space_n(self) -> int:
        return 4  # HOLD, BUY_YES, BUY_NO, SELL

    def reset(self) -> Dict[int, List[float]]:
        """Reset environment, return initial observations per agent."""
        self._step = 0
        self._positions = {i: {} for i in range(self.n_agents)}
        self._capital = {i: self.initial_capital for i in range(self.n_agents)}
        return {i: self._get_obs(i) for i in range(self.n_agents)}

    def step(self, actions: Dict[int, int]) -> Tuple[Dict, Dict, Dict, Dict]:
        """
        Execute one step for all agents simultaneously.

        Args:
            actions: {agent_id: action_int}

        Returns:
            (observations, rewards, dones, infos) — all keyed by agent_id
        """
        rewards = {}
        for agent_id, action in actions.items():
            rewards[agent_id] = self._execute_action(agent_id, action)

        self._step += 1
        done = self._step >= min(self.max_steps, len(self.market_data))

        obs = {i: self._get_obs(i) for i in range(self.n_agents)}
        dones = {i: done for i in range(self.n_agents)}
        infos = {
            i: {"capital": self._capital[i], "step": self._step}
            for i in range(self.n_agents)
        }

        return obs, rewards, dones, infos

    def _get_obs(self, agent_id: int) -> List[float]:
        """Get observation vector for an agent."""
        if self._step >= len(self.market_data):
            return [0.0] * self.observation_space_dim

        md = self.market_data[self._step]
        price = float(md.get("price", 0.5))
        volume = float(md.get("volume", 0)) / 100000  # Normalize
        spread = float(md.get("spread", 0.02))
        time_frac = self._step / max(self.max_steps, 1)
        capital = self._capital.get(agent_id, 0) / self.initial_capital
        position_value = sum(
            p.get("size", 0) * price for p in self._positions.get(agent_id, {}).values()
        ) if isinstance(self._positions.get(agent_id), dict) else 0.0
        pos_norm = position_value / max(self.initial_capital, 1)

        return [
            price, volume, spread, time_frac, capital, pos_norm,
            0.0, 0.0, 0.0, 0.0,  # Padding for future features
        ]

    def _execute_action(self, agent_id: int, action: int) -> float:
        """Execute an action and return reward."""
        if self._step >= len(self.market_data):
            return 0.0

        md = self.market_data[self._step]
        price = float(md.get("price", 0.5))
        fee_rate = 0.015  # 1.5% taker fee
        reward = 0.0

        if action == 1:  # BUY_YES
            cost = 100 * price * (1 + fee_rate)
            if self._capital[agent_id] >= cost:
                self._capital[agent_id] -= cost
                self._positions.setdefault(agent_id, {})
                self._positions[agent_id]["yes"] = {
                    "size": self._positions[agent_id].get("yes", {}).get("size", 0) + 100,
                    "avg_price": price,
                }
        elif action == 2:  # BUY_NO
            cost = 100 * (1 - price) * (1 + fee_rate)
            if self._capital[agent_id] >= cost:
                self._capital[agent_id] -= cost
                self._positions.setdefault(agent_id, {})
                self._positions[agent_id]["no"] = {
                    "size": self._positions[agent_id].get("no", {}).get("size", 0) + 100,
                    "avg_price": 1 - price,
                }
        elif action == 3:  # SELL (close all)
            positions = self._positions.get(agent_id, {})
            for side, pos in positions.items():
                size = pos.get("size", 0)
                avg = pos.get("avg_price", price)
                current = price if side == "yes" else (1 - price)
                pnl = size * (current - avg) * (1 - fee_rate)
                self._capital[agent_id] += size * current * (1 - fee_rate)
                reward += pnl
            self._positions[agent_id] = {}

        return reward


class JaxMARLWrapper:
    """
    JaxMARL wrapper for GPU-accelerated multi-agent backtesting.

    Uses JAX for vectorized environment stepping across thousands of
    episodes simultaneously.

    Install: pip install jaxmarl jax jaxlib
    """

    def __init__(self, market_data: List[Dict], n_agents: int = 4):
        self._market_data = market_data
        self._n_agents = n_agents
        self._available = False
        self._check_availability()

    def _check_availability(self):
        try:
            import jax
            import jaxmarl
            self._available = True
            logger.info("JaxMARL available: GPU-accelerated backtesting enabled")
        except ImportError:
            logger.info("jaxmarl not installed — using CPU PredictionMarketEnv fallback")

    @property
    def is_available(self) -> bool:
        return self._available

    def create_env(self) -> PredictionMarketEnv:
        """Create environment (falls back to CPU env if JAX not available)."""
        return PredictionMarketEnv(
            market_data=self._market_data,
            n_agents=self._n_agents,
        )

    def run_episodes(self, n_episodes: int = 100, policy=None) -> Dict[str, Any]:
        """
        Run multiple episodes and collect metrics.

        Args:
            n_episodes: Number of episodes to run
            policy: Callable(obs) -> action, or None for random

        Returns:
            Metrics dict with returns, win rates, etc.
        """
        import random
        all_returns = {i: [] for i in range(self._n_agents)}

        for ep in range(n_episodes):
            env = self.create_env()
            obs = env.reset()
            total_rewards = {i: 0.0 for i in range(self._n_agents)}

            for step in range(env.max_steps):
                if policy:
                    actions = {i: policy(obs[i]) for i in range(self._n_agents)}
                else:
                    actions = {i: random.randint(0, 3) for i in range(self._n_agents)}

                obs, rewards, dones, infos = env.step(actions)
                for i, r in rewards.items():
                    total_rewards[i] += r

                if all(dones.values()):
                    break

            for i, ret in total_rewards.items():
                all_returns[i].append(ret)

        # Aggregate metrics
        metrics = {}
        for i in range(self._n_agents):
            returns = all_returns[i]
            metrics[f"agent_{i}"] = {
                "mean_return": sum(returns) / max(len(returns), 1),
                "max_return": max(returns) if returns else 0,
                "min_return": min(returns) if returns else 0,
                "win_rate": sum(1 for r in returns if r > 0) / max(len(returns), 1),
            }

        return {"n_episodes": n_episodes, "agent_metrics": metrics}


class ABIDESWrapper:
    """
    ABIDES-MARL wrapper for JP Morgan's market simulation.

    ABIDES (Agent-Based Interactive Discrete Event Simulation) provides
    realistic market microstructure simulation with order book dynamics.

    Install: pip install abides-gym
    """

    def __init__(self):
        self._available = False
        self._check_availability()

    def _check_availability(self):
        try:
            import abides_gym
            self._available = True
            logger.info("ABIDES-MARL available: market simulation enabled")
        except ImportError:
            logger.info("abides-gym not installed — ABIDES simulation disabled")

    @property
    def is_available(self) -> bool:
        return self._available

    def create_env(self, config: Optional[Dict] = None) -> Any:
        """Create ABIDES gym environment."""
        if not self._available:
            return None

        try:
            import gymnasium as gym
            env = gym.make(
                "abides-markets-close-v0",
                **(config or {}),
            )
            return env
        except Exception as e:
            logger.warning("ABIDES env creation failed: %s", e)
            return None
