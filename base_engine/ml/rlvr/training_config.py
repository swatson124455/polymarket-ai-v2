"""
RLVR training configuration — Brier score reward function and hyperparameters.

The Brier score reward incentivizes well-calibrated probability estimates:
  reward = 1.0 - (prediction - outcome)^2

This is used with Modified GRPO (Group Relative Policy Optimization) or
ReMax to train the forecasting model.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


def brier_score_reward(prediction: float, outcome: float) -> float:
    """
    Brier score reward: higher is better (inverted Brier score).

    Args:
        prediction: Model's probability estimate [0, 1].
        outcome: Actual outcome (0 or 1).

    Returns:
        Reward in [0, 1] where 1.0 is perfect prediction.
    """
    prediction = max(0.0, min(1.0, prediction))
    return 1.0 - (prediction - outcome) ** 2


def batch_brier_reward(predictions: List[float], outcomes: List[float]) -> float:
    """Average Brier score reward over a batch."""
    if not predictions or len(predictions) != len(outcomes):
        return 0.0
    return sum(brier_score_reward(p, o) for p, o in zip(predictions, outcomes)) / len(predictions)


@dataclass
class RLVRTrainingConfig:
    """Configuration for RLVR training pipeline."""

    # Model
    base_model: str = "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"
    model_revision: str = "main"

    # Training
    learning_rate: float = 1e-5
    num_epochs: int = 3
    batch_size: int = 8
    gradient_accumulation_steps: int = 4
    max_seq_length: int = 2048
    warmup_ratio: float = 0.1

    # GRPO / ReMax
    optimization_method: str = "grpo"  # "grpo" or "remax"
    group_size: int = 7  # Number of samples per group for GRPO
    kl_penalty_weight: float = 0.1
    clip_range: float = 0.2

    # Reward
    reward_function: str = "brier_score"

    # Ensemble inference
    ensemble_runs: int = 7
    aggregation: str = "median"  # "median" or "mean"

    # Guard-rails
    max_output_tokens: int = 512
    gibberish_threshold: float = 0.3  # Max fraction of non-alphanumeric chars
    early_stop_patience: int = 3

    # Data
    min_training_samples: int = 1000
    synthetic_question_ratio: float = 0.5  # 50% synthetic, 50% real resolved questions
    categories: List[str] = field(default_factory=lambda: ["crypto", "politics", "sports", "economics"])
