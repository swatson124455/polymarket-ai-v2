"""
Tests for RL Trade Timing Agent — Tabular Q-learning with PER and adaptive drift detection.
Validates PrioritizedReplayBuffer, AdaptiveRewardTracker, and RLTradeTimingAgent.
"""
import asyncio
import math
import pickle
import tempfile
from pathlib import Path

import numpy as np
import pytest

from base_engine.execution.rl_trade_timing import (
    TRADE_NOW,
    WAIT,
    SKIP,
    _N_STATES,
    _N_ACTIONS,
    _SumTree,
    PrioritizedReplayBuffer,
    AdaptiveRewardTracker,
    RLTradeTimingAgent,
)


# ── Helpers ─────────────────────────────────────────────────────────

def _make_state(**overrides) -> dict:
    """Create a default market state dict for agent.decide()."""
    base = {
        "confidence": 0.7,
        "spread": 0.03,
        "volatility": 0.02,
        "regime": "calm",
        "hour": 15,
        "market_id": "test_market_123",
    }
    base.update(overrides)
    return base


# ── PrioritizedReplayBuffer Tests ───────────────────────────────────

class TestPrioritizedReplayBuffer:
    """Tests for PER buffer with SumTree."""

    def test_add_and_size(self):
        """Buffer tracks size correctly after adds."""
        buf = PrioritizedReplayBuffer(capacity=100)
        assert buf.size == 0
        for i in range(5):
            buf.add((i, 0, 0.1, i), td_error=0.5)
        assert buf.size == 5

    def test_sample_returns_correct_shape(self):
        """Sample returns transitions, IS weights, and indices."""
        buf = PrioritizedReplayBuffer(capacity=100)
        for i in range(20):
            buf.add((i % _N_STATES, i % _N_ACTIONS, float(i) * 0.1, (i + 1) % _N_STATES), td_error=float(i + 1))

        transitions, is_weights, indices = buf.sample(batch_size=8)
        assert len(transitions) == 8
        assert len(is_weights) == 8
        assert len(indices) == 8
        # IS weights should be positive
        assert np.all(is_weights > 0)

    def test_capacity_overflow_wraps(self):
        """Buffer wraps around when exceeding capacity."""
        buf = PrioritizedReplayBuffer(capacity=10)
        for i in range(25):
            buf.add((i % _N_STATES, 0, 0.1, 0))
        assert buf.size == 10  # Capped at capacity

    def test_update_priorities(self):
        """Priority updates change sampling distribution."""
        buf = PrioritizedReplayBuffer(capacity=50)
        # Add 10 transitions with equal priority
        for i in range(10):
            buf.add((i, 0, 0.1, i), td_error=1.0)

        # Update first transition to very high priority
        buf.update_priorities([0], np.array([100.0]))

        # Sample many times — index 0 should appear more often
        counts = {0: 0}
        for _ in range(200):
            _, _, indices = buf.sample(1)
            if indices and indices[0] == 0:
                counts[0] += 1

        # With high priority, index 0 should be sampled much more than 10% (1/10 uniform)
        assert counts[0] > 30, f"High-priority item should be sampled often, got {counts[0]}/200"


# ── AdaptiveRewardTracker Tests ─────────────────────────────────────

class TestAdaptiveRewardTracker:
    """Tests for ADWIN-inspired drift detection."""

    def test_no_drift_stable_rewards(self):
        """Stable reward stream should not trigger drift."""
        tracker = AdaptiveRewardTracker(min_window=20)
        for _ in range(100):
            report = tracker.update(0.1 + np.random.normal(0, 0.01))
        assert not report["drift_detected"]

    def test_gradual_drift_detected(self):
        """Significant mean shift should trigger gradual drift."""
        tracker = AdaptiveRewardTracker(min_window=20, delta=0.002)
        # Stable phase
        for _ in range(50):
            tracker.update(0.1)
        # Shifted phase (large mean shift)
        detected = False
        for _ in range(100):
            report = tracker.update(2.0)  # 20x larger
            if report["drift_detected"] and report["drift_type"] == "gradual":
                detected = True
                break
        assert detected, "Gradual drift should be detected on large mean shift"

    def test_reset_clears_state(self):
        """Reset should clear all internal state."""
        tracker = AdaptiveRewardTracker(min_window=10)
        for _ in range(50):
            tracker.update(1.0)
        tracker.reset()
        assert tracker._n_updates == 0
        assert tracker._baseline_mean is None
        assert len(tracker._window) == 0


# ── SumTree Tests ───────────────────────────────────────────────────

class TestSumTree:
    """Tests for the SumTree data structure."""

    def test_total_matches_sum_of_priorities(self):
        """Total property should equal sum of all leaf priorities."""
        tree = _SumTree(capacity=16)
        priorities = [1.0, 2.0, 3.0, 4.0]
        for i, p in enumerate(priorities):
            tree.add(p, f"item_{i}")
        assert abs(tree.total - sum(priorities)) < 1e-6

    def test_get_returns_valid_data(self):
        """Sampling by cumsum should return stored data."""
        tree = _SumTree(capacity=8)
        tree.add(1.0, "alpha")
        tree.add(2.0, "beta")
        tree.add(3.0, "gamma")
        # cumsum=0.5 should hit first item (priority=1.0)
        _, _, data = tree.get(0.5)
        assert data in ("alpha", "beta", "gamma")


# ── RLTradeTimingAgent Tests ────────────────────────────────────────

class TestRLTradeTimingAgent:
    """Tests for the main Q-learning agent."""

    @pytest.mark.asyncio
    async def test_decide_returns_valid_action(self):
        """decide() returns (action, q_value) with valid action."""
        agent = RLTradeTimingAgent()
        action, q_val = await agent.decide(_make_state())
        assert action in (TRADE_NOW, WAIT, SKIP)
        assert isinstance(q_val, float)

    def test_q_table_shape(self):
        """Q-table has correct shape (324 states x 3 actions)."""
        agent = RLTradeTimingAgent()
        assert agent.q_table.shape == (_N_STATES, _N_ACTIONS)
        assert agent.visit_counts.shape == (_N_STATES, _N_ACTIONS)

    def test_record_outcome_updates_q_table(self):
        """record_outcome should modify Q-values."""
        agent = RLTradeTimingAgent(learning_rate=0.5)
        # All zeros initially
        assert np.all(agent.q_table == 0.0)

        # Record a positive outcome
        agent.record_outcome(state_idx=0, action=TRADE_NOW, reward=1.0, next_state_idx=0)

        # Q-value should now be non-zero
        assert agent.q_table[0, TRADE_NOW] != 0.0
        assert agent.visit_counts[0, TRADE_NOW] == 1

    @pytest.mark.asyncio
    async def test_record_outcome_from_trade_with_pending(self):
        """record_outcome_from_trade uses stored pending decision."""
        agent = RLTradeTimingAgent(learning_rate=0.5, epsilon_start=0.0)

        # Simulate a decide() call to create a pending entry
        action, _ = await agent.decide(_make_state(market_id="mkt_001"))

        # Now record outcome
        agent.record_outcome_from_trade("mkt_001", pnl=0.5)

        # Pending should be cleared
        assert "mkt_001" not in agent._pending
        # Q-table should have been updated
        assert agent._total_trades == 1

    def test_record_outcome_from_trade_unknown_market_no_crash(self):
        """record_outcome_from_trade with unknown market_id is a no-op."""
        agent = RLTradeTimingAgent()
        # Should not raise
        agent.record_outcome_from_trade("unknown_market", pnl=0.5)
        assert agent._total_trades == 0

    def test_epsilon_decays_over_trades(self):
        """Epsilon should decrease from start toward min over decay_trades."""
        agent = RLTradeTimingAgent(epsilon_start=0.3, epsilon_min=0.05, epsilon_decay_trades=100)
        initial_eps = agent.epsilon

        # Simulate many outcomes
        for i in range(100):
            agent.record_outcome(state_idx=i % _N_STATES, action=i % _N_ACTIONS, reward=0.1)

        assert agent.epsilon < initial_eps
        assert agent.epsilon >= agent.epsilon_min - 1e-6

    def test_discretize_state_boundaries(self):
        """State discretization covers all expected boundaries."""
        agent = RLTradeTimingAgent()

        # Low confidence, tight spread, low vol, calm, asia hour
        idx1 = agent._discretize_state(_make_state(
            confidence=0.5, spread=0.01, volatility=0.005, regime="calm", hour=3
        ))
        assert 0 <= idx1 < _N_STATES

        # High confidence, wide spread, high vol, trending, US afternoon
        idx2 = agent._discretize_state(_make_state(
            confidence=0.9, spread=0.08, volatility=0.05, regime="trending", hour=20
        ))
        assert 0 <= idx2 < _N_STATES

        # Different states should produce different indices
        assert idx1 != idx2

    def test_discretize_state_all_regimes(self):
        """All regime strings map to valid states."""
        agent = RLTradeTimingAgent()
        for regime in ("calm", "volatile", "trending", "HIGH_VOLATILITY", "MOMENTUM", "unknown"):
            idx = agent._discretize_state(_make_state(regime=regime))
            assert 0 <= idx < _N_STATES

    def test_save_load_roundtrip(self):
        """Save and load preserve Q-table and stats."""
        agent = RLTradeTimingAgent(learning_rate=0.5)

        # Train a bit
        for i in range(20):
            agent.record_outcome(i % _N_STATES, i % _N_ACTIONS, float(i) * 0.1)

        q_before = agent.q_table.copy()
        trades_before = agent._total_trades

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            tmp_path = Path(f.name)

        agent.save(tmp_path)

        # Load into fresh agent
        agent2 = RLTradeTimingAgent()
        assert agent2._total_trades == 0
        loaded = agent2.load(tmp_path)

        assert loaded is True
        np.testing.assert_array_equal(q_before, agent2.q_table)
        assert agent2._total_trades == trades_before

        # Cleanup
        tmp_path.unlink(missing_ok=True)

    def test_load_nonexistent_returns_false(self):
        """Loading from nonexistent path returns False."""
        agent = RLTradeTimingAgent()
        assert agent.load(Path("/nonexistent/path/rl.pkl")) is False

    def test_get_stats_structure(self):
        """get_stats returns expected keys."""
        agent = RLTradeTimingAgent()
        stats = agent.get_stats()

        expected_keys = {
            "total_trades", "total_reward", "epsilon", "learning_rate",
            "states_explored", "states_total", "exploration_pct",
            "drift_events", "replay_buffer_size", "actions_taken",
            "q_table_mean", "q_table_std",
        }
        assert expected_keys.issubset(stats.keys())
        assert stats["states_total"] == _N_STATES

    def test_replay_improves_learning(self):
        """Replay should further update Q-values beyond direct updates."""
        agent = RLTradeTimingAgent(
            learning_rate=0.3, replay_buffer_size=100, replay_batch_size=8
        )

        # Add many high-reward transitions to state 0 / TRADE_NOW
        for _ in range(50):
            agent.record_outcome(0, TRADE_NOW, 2.0, next_state_idx=0)

        q_after_direct = agent.q_table[0, TRADE_NOW]

        # Force additional replay rounds
        for _ in range(10):
            agent._replay_batch()

        q_after_replay = agent.q_table[0, TRADE_NOW]

        # Q-value should be at least as high (replay reinforces positive signal)
        assert q_after_replay >= q_after_direct - 0.01

    @pytest.mark.asyncio
    async def test_concurrent_decide_is_safe(self):
        """Multiple concurrent decide() calls don't corrupt state."""
        agent = RLTradeTimingAgent()
        tasks = [
            agent.decide(_make_state(market_id=f"mkt_{i}"))
            for i in range(20)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 20
        for action, q_val in results:
            assert action in (TRADE_NOW, WAIT, SKIP)
