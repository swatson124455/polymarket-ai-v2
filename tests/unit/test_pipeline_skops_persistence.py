"""S195 Day 2: skops persistence + .joblib transition fallback for EsportsPipeline.

Pins three behaviours:
  1. save → skops format, load round-trips identical predictions
  2. .skops absent + .joblib present → legacy fallback loads, next save writes .skops
  3. Trusted-type whitelist rejects unknown types at load time
"""
from __future__ import annotations

import time
from pathlib import Path

import joblib
import numpy as np
import pytest

from esports_v2.model.pipeline import EsportsPipeline


def _make_records(n: int = 60) -> list[dict]:
    rng = np.random.default_rng(42)
    out = []
    for i in range(n):
        out.append({
            "p_elo": float(rng.random()),
            "p_glicko": float(rng.random()),
            "p_openskill": float(rng.random()),
            "trinity_spread": float(rng.random() * 0.2),
            "trinity_mean": 0.5,
            "event_tier": "a_tier",
            "is_lan": bool(i % 2),
            "best_of": 3,
            "game": "cs2" if i % 2 else "lol",
            "actual": i % 2,
        })
    return out


@pytest.fixture
def fitted_pipeline() -> EsportsPipeline:
    p = EsportsPipeline()
    p.fit(_make_records())
    return p


def test_save_writes_skops_then_load_round_trips_predictions(
    fitted_pipeline: EsportsPipeline, tmp_path: Path
) -> None:
    rec = {
        "p_elo": 0.6, "p_glicko": 0.55, "p_openskill": 0.58,
        "trinity_spread": 0.05, "trinity_mean": 0.58,
        "event_tier": "a_tier", "is_lan": True, "best_of": 3,
        "game": "cs2",
    }
    pre = fitted_pipeline.predict(rec)

    path = tmp_path / "pipeline.skops"
    fitted_pipeline.save(path)
    assert path.exists()

    fresh = EsportsPipeline()
    assert fresh.load(path) is True

    post = fresh.predict(rec)
    assert post["p_raw"] == pytest.approx(pre["p_raw"], rel=1e-6)
    assert post["p_model"] == pytest.approx(pre["p_model"], rel=1e-6)


def test_load_falls_back_to_legacy_joblib_when_skops_missing(
    fitted_pipeline: EsportsPipeline, tmp_path: Path
) -> None:
    legacy_path = tmp_path / "pipeline.joblib"
    state = {
        "xgb": fitted_pipeline._xgb,
        "calibrator": fitted_pipeline._calibrator,
        "conformal": fitted_pipeline._conformal,
        "saved_at": time.time(),
    }
    joblib.dump(state, legacy_path)

    fresh = EsportsPipeline()
    skops_path = tmp_path / "pipeline.skops"
    assert not skops_path.exists()

    assert fresh.load(skops_path) is True

    fresh.save(skops_path)
    assert skops_path.exists()


def test_load_returns_false_when_neither_path_exists(tmp_path: Path) -> None:
    fresh = EsportsPipeline()
    assert fresh.load(tmp_path / "absent.skops") is False


def test_load_returns_false_when_snapshot_is_stale(
    fitted_pipeline: EsportsPipeline, tmp_path: Path
) -> None:
    path = tmp_path / "pipeline.skops"
    fitted_pipeline.save(path)
    stale = time.time() - (EsportsPipeline.STALENESS_SECONDS + 60)
    import os
    os.utime(path, (stale, stale))

    fresh = EsportsPipeline()
    assert fresh.load(path) is False


def test_skops_load_rejects_untrusted_types(
    fitted_pipeline: EsportsPipeline, tmp_path: Path
) -> None:
    """If a future state contains a type not in _SKOPS_TRUSTED_TYPES, load
    must refuse rather than silently exec arbitrary __reduce__. The detector
    for "untrusted type at load time" lives inside skops.io.load — we just
    confirm the contract by passing an empty trusted list and asserting load
    fails.
    """
    import skops.io as sio
    state = {
        "xgb": fitted_pipeline._xgb,
        "calibrator": fitted_pipeline._calibrator,
        "conformal": fitted_pipeline._conformal,
        "saved_at": time.time(),
    }
    path = tmp_path / "pipeline.skops"
    sio.dump(state, str(path))
    with pytest.raises(Exception):
        sio.load(str(path), trusted=[])
