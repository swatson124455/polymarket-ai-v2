"""
FinRL Integration — Tier 5 #46

Replace tabular Q-learning with proper Deep RL when sufficient data exists.
FinRL provides pre-built financial trading environments and DRL agents.

Integration point: rl_trade_timing.py uses tabular Q-learning (250KB).
This adapter provides a DRL upgrade path using Stable-Baselines3 via FinRL.

Dependencies: finrl, stable-baselines3, gymnasium (optional).
Install: pip install finrl stable-baselines3

When to upgrade from tabular to DRL:
  - 10K+ logged trade decisions (currently ~hundreds)
  - State space grows beyond 1000 discrete states
  - Need continuous action space (position sizing, not just trade/wait/skip)
"""
from typing import Dict, List, Optional, Any
from structlog import get_logger

logger = get_logger()


class FinRLAdapter:
    """
    FinRL-based Deep RL adapter for trade timing.

    Provides PPO/A2C/SAC agents that learn continuous trading policies
    from historical data. Falls back to tabular Q-learning when FinRL
    is not installed.

    State: [price, volume, spread, position, capital, features...]
    Action: Continuous [-1, 1] → mapped to (sell_pct, hold, buy_pct)
    Reward: Risk-adjusted PnL (Sharpe-like)
    """

    def __init__(self, state_dim: int = 20, model_name: str = "ppo"):
        self._state_dim = state_dim
        self._model_name = model_name.lower()
        self._model = None
        self._env = None
        self._available = False
        self._check_availability()

    def _check_availability(self):
        """Check if FinRL dependencies are available."""
        try:
            import stable_baselines3
            self._available = True
            logger.info("FinRL/SB3 available: DRL trade timing enabled")
        except ImportError:
            logger.info("stable-baselines3 not installed — DRL adapter disabled (using tabular Q-learning)")

    @property
    def is_available(self) -> bool:
        return self._available

    def create_env(self, market_data: List[Dict]) -> Any:
        """
        Create a Gymnasium environment from market data.

        Args:
            market_data: List of market snapshots with price, volume, etc.

        Returns:
            Gymnasium env or None
        """
        if not self._available:
            return None

        try:
            import gymnasium as gym
            from gymnasium import spaces
            import numpy as np

            class TradeTimingEnv(gym.Env):
                """Custom Gym env for prediction market trade timing."""

                def __init__(self, data, state_dim):
                    super().__init__()
                    self.data = data
                    self.state_dim = state_dim
                    self.observation_space = spaces.Box(
                        low=-np.inf, high=np.inf,
                        shape=(state_dim,), dtype=np.float32,
                    )
                    # Continuous: -1 (full sell) to +1 (full buy)
                    self.action_space = spaces.Box(
                        low=-1.0, high=1.0, shape=(1,), dtype=np.float32,
                    )
                    self.current_step = 0
                    self.position = 0.0
                    self.capital = 10000.0
                    self.entry_price = 0.0

                def reset(self, seed=None, options=None):
                    super().reset(seed=seed)
                    self.current_step = 0
                    self.position = 0.0
                    self.capital = 10000.0
                    self.entry_price = 0.0
                    return self._get_obs(), {}

                def step(self, action):
                    action_val = float(action[0])
                    reward = 0.0

                    if self.current_step < len(self.data):
                        md = self.data[self.current_step]
                        price = float(md.get("price", 0.5))

                        # Execute action
                        if action_val > 0.3 and self.position == 0:
                            # Buy
                            size = min(100, self.capital * 0.1)
                            self.position = size
                            self.entry_price = price
                            self.capital -= size * price
                        elif action_val < -0.3 and self.position > 0:
                            # Sell
                            pnl = self.position * (price - self.entry_price)
                            self.capital += self.position * price
                            reward = pnl / max(self.entry_price * self.position, 1)
                            self.position = 0.0

                    self.current_step += 1
                    done = self.current_step >= len(self.data)
                    truncated = False

                    return self._get_obs(), reward, done, truncated, {}

                def _get_obs(self):
                    import numpy as np
                    obs = np.zeros(self.state_dim, dtype=np.float32)
                    if self.current_step < len(self.data):
                        md = self.data[self.current_step]
                        obs[0] = float(md.get("price", 0.5))
                        obs[1] = float(md.get("volume", 0)) / 100000
                        obs[2] = float(md.get("spread", 0.02))
                        obs[3] = self.current_step / max(len(self.data), 1)
                        obs[4] = self.capital / 10000
                        obs[5] = self.position / 1000
                    return obs

            env = TradeTimingEnv(market_data, self._state_dim)
            self._env = env
            return env

        except Exception as e:
            logger.warning("FinRL env creation failed: %s", e)
            return None

    def train(
        self,
        market_data: List[Dict],
        total_timesteps: int = 50000,
    ) -> Dict[str, Any]:
        """
        Train a DRL agent on market data.

        Args:
            market_data: Training data
            total_timesteps: Total training steps

        Returns:
            Training metrics
        """
        if not self._available:
            return {"error": "FinRL/SB3 not available"}

        env = self.create_env(market_data)
        if env is None:
            return {"error": "Environment creation failed"}

        try:
            if self._model_name == "ppo":
                from stable_baselines3 import PPO
                self._model = PPO("MlpPolicy", env, verbose=0)
            elif self._model_name == "a2c":
                from stable_baselines3 import A2C
                self._model = A2C("MlpPolicy", env, verbose=0)
            elif self._model_name == "sac":
                from stable_baselines3 import SAC
                self._model = SAC("MlpPolicy", env, verbose=0)
            else:
                from stable_baselines3 import PPO
                self._model = PPO("MlpPolicy", env, verbose=0)

            self._model.learn(total_timesteps=total_timesteps)
            logger.info("FinRL training complete: %s, %d steps", self._model_name, total_timesteps)

            return {
                "model": self._model_name,
                "timesteps": total_timesteps,
                "status": "trained",
            }

        except Exception as e:
            logger.warning("FinRL training failed: %s", e)
            return {"error": str(e)}

    def predict(self, observation: List[float]) -> float:
        """
        Get action from trained model.

        Args:
            observation: State vector

        Returns:
            Action value (-1 to 1)
        """
        if not self._available or self._model is None:
            return 0.0  # HOLD

        try:
            import numpy as np
            obs = np.array(observation, dtype=np.float32)
            action, _ = self._model.predict(obs, deterministic=True)
            return float(action[0])
        except Exception:
            return 0.0

    def save(self, path: str) -> bool:
        """Save trained model."""
        if self._model is None:
            return False
        try:
            self._model.save(path)
            return True
        except Exception:
            return False

    def load(self, path: str) -> bool:
        """Load trained model."""
        if not self._available:
            return False
        try:
            if self._model_name == "ppo":
                from stable_baselines3 import PPO
                self._model = PPO.load(path)
            elif self._model_name == "a2c":
                from stable_baselines3 import A2C
                self._model = A2C.load(path)
            elif self._model_name == "sac":
                from stable_baselines3 import SAC
                self._model = SAC.load(path)
            return True
        except Exception:
            return False
