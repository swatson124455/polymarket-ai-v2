"""Tests for esports ONNX model compiler."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest


class TestOnnxCompilerInit:
    """OnnxCompiler instantiation."""

    def test_onnx_compiler_init(self):
        from esports.models.onnx_compiler import OnnxCompiler

        compiler = OnnxCompiler()
        assert compiler is not None


class TestOnnxCompilerGracefulFallback:
    """Graceful degradation when ONNX packages are not installed."""

    def test_export_graceful_without_onnxmltools(self):
        from esports.models.onnx_compiler import OnnxCompiler

        compiler = OnnxCompiler()

        with patch.dict("sys.modules", {"onnxmltools": None}):
            result = compiler.export_xgboost(
                xgb_model=object(), n_features=8, save_path="/tmp/fake.onnx"
            )
        assert result is None

    def test_load_session_graceful_without_onnxruntime(self):
        from esports.models.onnx_compiler import OnnxCompiler

        compiler = OnnxCompiler()

        with patch.dict("sys.modules", {"onnxruntime": None}):
            result = compiler.load_session("/tmp/nonexistent.onnx")
        assert result is None


class TestOnnxCompilerRoundtrip:
    """End-to-end export + load + predict (requires onnxruntime + onnxmltools)."""

    def test_predict_proba_shape(self):
        ort = pytest.importorskip("onnxruntime")
        pytest.importorskip("onnxmltools")
        pytest.importorskip("xgboost")

        import tempfile
        import os
        from xgboost import XGBClassifier
        from esports.models.onnx_compiler import OnnxCompiler

        # Train a tiny model
        rng = np.random.RandomState(42)
        X_train = rng.rand(50, 4).astype(np.float32)
        y_train = (X_train[:, 0] > 0.5).astype(np.int32)

        model = XGBClassifier(n_estimators=5, max_depth=2, verbosity=0)
        model.fit(X_train, y_train)

        compiler = OnnxCompiler()

        with tempfile.TemporaryDirectory() as tmpdir:
            onnx_path = os.path.join(tmpdir, "test_model.onnx")
            result_path = compiler.export_xgboost(model, n_features=4, save_path=onnx_path)
            assert result_path == onnx_path
            assert os.path.exists(onnx_path)

            session = compiler.load_session(onnx_path)
            assert session is not None

            X_test = rng.rand(10, 4).astype(np.float32)
            probs = compiler.predict_proba(session, X_test)

            # Shape must be (n_samples, 2) for binary classification
            assert probs.shape == (10, 2)
            assert probs.dtype == np.float32

    def test_export_and_predict_roundtrip(self):
        ort = pytest.importorskip("onnxruntime")
        pytest.importorskip("onnxmltools")
        pytest.importorskip("xgboost")

        import tempfile
        import os
        from xgboost import XGBClassifier
        from esports.models.onnx_compiler import OnnxCompiler

        # Train a tiny model
        rng = np.random.RandomState(123)
        X_train = rng.rand(100, 6).astype(np.float32)
        y_train = ((X_train[:, 0] + X_train[:, 1]) > 1.0).astype(np.int32)

        model = XGBClassifier(n_estimators=10, max_depth=3, verbosity=0)
        model.fit(X_train, y_train)

        compiler = OnnxCompiler()

        with tempfile.TemporaryDirectory() as tmpdir:
            onnx_path = os.path.join(tmpdir, "roundtrip.onnx")
            compiler.export_xgboost(model, n_features=6, save_path=onnx_path)

            session = compiler.load_session(onnx_path)
            assert session is not None

            X_test = rng.rand(20, 6).astype(np.float32)

            # Native XGBoost probabilities
            native_probs = model.predict_proba(X_test)

            # ONNX probabilities
            onnx_probs = compiler.predict_proba(session, X_test)

            # Must match within tolerance
            np.testing.assert_allclose(onnx_probs, native_probs, atol=1e-5)
