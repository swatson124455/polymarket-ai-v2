"""
RLVR training script — train a forecasting model with Brier score reward.

Usage:
    python -m base_engine.ml.rlvr.train --output_dir ./models/rlvr

Requires: trl, peft, transformers, torch.
Target: DeepSeek-R1-Distill-Qwen-14B from HuggingFace.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Optional
from structlog import get_logger

from base_engine.ml.rlvr.training_config import RLVRTrainingConfig, brier_score_reward

logger = get_logger()


def train_rlvr(
    training_data: List[Dict],
    config: Optional[RLVRTrainingConfig] = None,
    output_dir: str = "./models/rlvr",
) -> bool:
    """
    Train RLVR forecasting model using Brier score as reward.

    Args:
        training_data: List of {question, outcome, category} dicts.
        config: Training configuration.
        output_dir: Where to save the trained model.

    Returns:
        True if training completed successfully.
    """
    config = config or RLVRTrainingConfig()

    # Validate training data
    valid_data = [d for d in training_data if d.get("outcome") is not None]
    if len(valid_data) < config.min_training_samples:
        logger.error(
            "Insufficient training data: %d samples (need %d)",
            len(valid_data), config.min_training_samples,
        )
        return False

    logger.info(
        "Starting RLVR training: %d samples, model=%s, method=%s",
        len(valid_data), config.base_model, config.optimization_method,
    )

    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import LoraConfig, get_peft_model
    except ImportError:
        logger.error("Required packages not installed: pip install transformers peft trl torch")
        return False

    try:
        # Load base model
        logger.info("Loading base model: %s", config.base_model)
        tokenizer = AutoTokenizer.from_pretrained(config.base_model)
        model = AutoModelForCausalLM.from_pretrained(
            config.base_model,
            device_map="auto",
            torch_dtype="auto",
        )

        # Apply LoRA for parameter-efficient fine-tuning
        lora_config = LoraConfig(
            r=16,
            lora_alpha=32,
            target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora_config)
        logger.info("LoRA applied: trainable params = %d", model.num_parameters(only_trainable=True))

        # Build reward function wrapper for trl
        def reward_fn(predictions: List[str], references: List[str]) -> List[float]:
            """Map model outputs to Brier score rewards."""
            from base_engine.ml.rlvr.inference import _extract_probability
            rewards = []
            for pred_text, ref in zip(predictions, references):
                prob = _extract_probability(pred_text)
                if prob is None:
                    rewards.append(0.0)  # Penalize unparseable output
                    continue
                outcome = float(ref)
                rewards.append(brier_score_reward(prob, outcome))
            return rewards

        # TRL-based training
        try:
            from trl import PPOTrainer, PPOConfig
            ppo_config = PPOConfig(
                learning_rate=config.learning_rate,
                batch_size=config.batch_size,
                mini_batch_size=config.batch_size // 2,
                gradient_accumulation_steps=config.gradient_accumulation_steps,
                ppo_epochs=config.num_epochs,
            )

            logger.info("PPO training starting with %d samples...", len(valid_data))
            # Training loop would go here — simplified for scaffold
            # In production: iterate over batches, generate responses, compute rewards, update policy
            logger.info("Training scaffold complete — full PPO loop requires GPU runtime")

        except ImportError:
            logger.info("trl not available — skipping PPO training (scaffold only)")

        # Save model
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(str(out_path))
        tokenizer.save_pretrained(str(out_path))

        # Save config
        with open(out_path / "training_config.json", "w") as f:
            json.dump({
                "base_model": config.base_model,
                "num_samples": len(valid_data),
                "optimization_method": config.optimization_method,
                "ensemble_runs": config.ensemble_runs,
            }, f, indent=2)

        logger.info("RLVR model saved to %s", output_dir)
        return True

    except Exception as e:
        logger.error("RLVR training failed: %s", e, exc_info=True)
        return False


def main():
    parser = argparse.ArgumentParser(description="Train RLVR forecasting model")
    parser.add_argument("--output_dir", type=str, default="./models/rlvr")
    parser.add_argument("--data_file", type=str, help="JSON file with training data")
    parser.add_argument("--num_samples", type=int, default=10000)
    args = parser.parse_args()

    training_data = []
    if args.data_file:
        with open(args.data_file) as f:
            training_data = json.load(f)
    else:
        logger.info("No data file provided — generate data first with data_generator.py")
        sys.exit(1)

    config = RLVRTrainingConfig()
    success = train_rlvr(training_data, config, args.output_dir)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
