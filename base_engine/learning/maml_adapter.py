"""
MAML (Model-Agnostic Meta-Learning) Adapter — Tier 5 #47

Few-shot adaptation to new market regimes using learn2learn.
When a regime shift is detected (via MarketRegimeDetector), MAML enables
rapid fine-tuning of the prediction model with just a few examples.

Dependencies: learn2learn, torch (optional — graceful fallback).
Install: pip install learn2learn torch

Integration point: meta_learning.py → prediction_engine.py regime adaptation.
"""
from typing import Dict, List, Optional, Any, Tuple
from structlog import get_logger

logger = get_logger()


class MAMLAdapter:
    """
    MAML wrapper using learn2learn for few-shot regime adaptation.

    Trains a base model that can be quickly adapted to new market regimes
    with just 5-10 gradient steps on a small support set.

    Falls back gracefully when learn2learn/torch not installed.
    """

    def __init__(
        self,
        input_dim: int = 20,
        hidden_dim: int = 64,
        lr: float = 0.01,
        meta_lr: float = 0.001,
        adaptation_steps: int = 5,
    ):
        self._input_dim = input_dim
        self._hidden_dim = hidden_dim
        self._lr = lr
        self._meta_lr = meta_lr
        self._adaptation_steps = adaptation_steps
        self._maml = None
        self._model = None
        self._available = False
        self._init_maml()

    def _init_maml(self):
        """Initialize MAML with learn2learn."""
        try:
            import torch
            import torch.nn as nn
            import learn2learn as l2l

            # Simple prediction network
            class PredictionNet(nn.Module):
                def __init__(self, input_dim, hidden_dim):
                    super().__init__()
                    self.net = nn.Sequential(
                        nn.Linear(input_dim, hidden_dim),
                        nn.ReLU(),
                        nn.Dropout(0.1),
                        nn.Linear(hidden_dim, hidden_dim // 2),
                        nn.ReLU(),
                        nn.Linear(hidden_dim // 2, 1),
                        nn.Sigmoid(),
                    )

                def forward(self, x):
                    return self.net(x).squeeze(-1)

            self._model = PredictionNet(self._input_dim, self._hidden_dim)
            self._maml = l2l.algorithms.MAML(self._model, lr=self._lr)
            self._optimizer = torch.optim.Adam(self._maml.parameters(), lr=self._meta_lr)
            self._loss_fn = nn.BCELoss()
            self._available = True
            logger.info("MAMLAdapter initialized: input=%d, hidden=%d", self._input_dim, self._hidden_dim)
        except ImportError:
            logger.info("learn2learn or torch not installed — MAMLAdapter disabled")
        except Exception as e:
            logger.warning("MAMLAdapter init failed: %s", e)

    @property
    def is_available(self) -> bool:
        return self._available

    def meta_train(
        self,
        tasks: List[Tuple[Any, Any, Any, Any]],
        n_epochs: int = 100,
    ) -> Dict[str, Any]:
        """
        Meta-train on a set of tasks (one task = one market regime).

        Args:
            tasks: List of (support_X, support_y, query_X, query_y) tuples.
                   Each task represents a different market regime.
            n_epochs: Number of meta-training epochs.

        Returns:
            Training metrics dict.
        """
        if not self._available:
            return {"error": "MAML not available"}

        import torch
        losses = []

        for epoch in range(n_epochs):
            epoch_loss = 0.0
            for support_X, support_y, query_X, query_y in tasks:
                # Convert to tensors
                s_X = torch.FloatTensor(support_X)
                s_y = torch.FloatTensor(support_y)
                q_X = torch.FloatTensor(query_X)
                q_y = torch.FloatTensor(query_y)

                # Clone model for inner loop adaptation
                learner = self._maml.clone()

                # Inner loop: adapt on support set
                for _ in range(self._adaptation_steps):
                    support_pred = learner(s_X)
                    support_loss = self._loss_fn(support_pred, s_y)
                    learner.adapt(support_loss)

                # Outer loop: evaluate on query set
                query_pred = learner(q_X)
                query_loss = self._loss_fn(query_pred, q_y)
                epoch_loss += query_loss.item()

                # Meta-gradient step
                self._optimizer.zero_grad()
                query_loss.backward()
                self._optimizer.step()

            avg_loss = epoch_loss / max(len(tasks), 1)
            losses.append(avg_loss)

            if (epoch + 1) % 20 == 0:
                logger.info("MAML epoch %d/%d, loss=%.4f", epoch + 1, n_epochs, avg_loss)

        return {
            "epochs": n_epochs,
            "final_loss": losses[-1] if losses else None,
            "loss_history": losses,
            "n_tasks": len(tasks),
        }

    def adapt_to_regime(
        self,
        support_X: Any,
        support_y: Any,
    ) -> Optional[Any]:
        """
        Quickly adapt the meta-learned model to a new regime.

        Args:
            support_X: Small support set features (5-20 examples)
            support_y: Support set labels

        Returns:
            Adapted learner (callable) or None
        """
        if not self._available:
            return None

        import torch

        s_X = torch.FloatTensor(support_X)
        s_y = torch.FloatTensor(support_y)

        learner = self._maml.clone()
        for _ in range(self._adaptation_steps):
            pred = learner(s_X)
            loss = self._loss_fn(pred, s_y)
            learner.adapt(loss)

        return learner

    def predict_adapted(self, learner: Any, X: Any) -> Optional[Any]:
        """Make predictions with an adapted learner."""
        if not self._available or learner is None:
            return None

        import torch
        with torch.no_grad():
            X_t = torch.FloatTensor(X)
            predictions = learner(X_t).numpy()
        return predictions.tolist()
