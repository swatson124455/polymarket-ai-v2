"""
ONNX Model Compiler for Esports XGBoost Models.

Exports trained XGBoost models to ONNX format for faster inference.
Falls back gracefully to native XGBoost predict if ONNX not available.

Usage::
    compiler = OnnxCompiler()
    onnx_path = compiler.export_xgboost(model, n_features=8, save_path="model.onnx")
    # Later:
    session = compiler.load_session("model.onnx")
    probs = compiler.predict_proba(session, features_array)
"""
from __future__ import annotations

import os
from typing import Any, Optional

import numpy as np
from structlog import get_logger

logger = get_logger()


class OnnxCompiler:
    """Convert XGBoost models to ONNX and run compiled inference."""

    def export_xgboost(
        self, xgb_model, n_features: int, save_path: str
    ) -> Optional[str]:
        """Convert an XGBoost model to ONNX format and save to disk.

        Args:
            xgb_model: Trained XGBClassifier instance.
            n_features: Number of input features the model expects.
            save_path: Filesystem path to write the .onnx file.

        Returns:
            The save_path on success, or None on failure / missing deps.
        """
        try:
            from onnxmltools import convert_xgboost
            from onnxmltools.convert.common.data_types import FloatTensorType

            initial_type = [("float_input", FloatTensorType([None, n_features]))]
            onnx_model = convert_xgboost(xgb_model, initial_types=initial_type)

            import onnx

            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            onnx.save(onnx_model, save_path)
            logger.info("onnx_export_success", path=save_path, n_features=n_features)
            return save_path
        except ImportError:
            logger.debug("onnx_export_skipped: onnxmltools not installed")
            return None
        except Exception as e:
            logger.warning("onnx_export_failed", error=str(e))
            return None

    def load_session(self, onnx_path: str) -> Optional[Any]:
        """Load an ONNX InferenceSession from disk.

        Args:
            onnx_path: Path to an .onnx file.

        Returns:
            An ``ort.InferenceSession`` on success, or None if onnxruntime
            is not installed or the file does not exist.
        """
        try:
            import onnxruntime as ort

            if not os.path.exists(onnx_path):
                return None
            session = ort.InferenceSession(
                onnx_path, providers=["CPUExecutionProvider"]
            )
            return session
        except ImportError:
            return None
        except Exception as e:
            logger.warning("onnx_load_failed", path=onnx_path, error=str(e))
            return None

    def predict_proba(self, session, X: np.ndarray) -> np.ndarray:
        """Run ONNX inference.

        Args:
            session: An ``ort.InferenceSession`` returned by :meth:`load_session`.
            X: Feature array of shape ``(n_samples, n_features)``.

        Returns:
            Probability array of shape ``(n_samples, 2)`` for binary classification.
        """
        input_name = session.get_inputs()[0].name
        result = session.run(None, {input_name: X.astype(np.float32)})
        # ONNX XGBoost outputs: [labels, probabilities]
        probs = result[1]  # shape (n, 2) for binary classification
        return np.array(probs, dtype=np.float32)
