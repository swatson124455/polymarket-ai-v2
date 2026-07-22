"""c12 — shared prediction_log calibrators must exclude WB nowcast rows.

DEFECT (2026-07-21): every calibration/quality reader below pools
`prediction_log` across ALL bots with no model_name filter, so WeatherBot's
`weather_nowcast_peak` rows — a shadow signal with a near-constant ~0.44 prior
that resolves rarely and in bursts — fed the curves the prediction engine and
MirrorBot consume. WB already excludes them from its OWN paths (c9
weather_bot.py `_update_city_brier`; c11 scripts/calibration_check.py x3); the
shared copies were missed.

These tests assert the SQL text of each pooled reader, in BOTH the top-level
module and the byte-identical WB-vendored mirror. Source assertions (not live
queries) match the established idiom for SQL guards in this repo — see
tests/unit/test_calibration_check.py::…_sql and the signal_ingestion source
tests in tests/unit/test_batch_e_infrastructure.py.
"""
import ast
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CALIBRATION_COPIES = [
    "base_engine/features/calibration.py",
    "bots/weather/engine/base_engine/features/calibration.py",
]
DATABASE_COPIES = [
    "base_engine/data/database.py",
    "bots/weather/engine/base_engine/data/database.py",
]

# Every pooled prediction_log reader that feeds a fitted model or a quality
# window. P16 completeness: this list IS the enumeration — a new pooled reader
# must be added here (or deliberately exempted) when it is written.
CALIBRATION_FUNCS = [
    "fit_from_prediction_log",   # FavoriteLongshotCalibrator + FocalTemperatureCalibrator
    "_fit_category",             # DomainCalibrator
]
DATABASE_FUNCS = [
    "get_recent_performance_from_prediction_log",
    "get_recent_brier_from_prediction_log",
    "get_recent_resolved_predictions",
    "get_model_live_performance",
    "get_recent_resolved_for_blend",
]

PREDICATE = "NOT LIKE '%nowcast%'"


def _sources_named(path, name):
    """Every function/method body in `path` named `name` (there may be >1 —
    fit_from_prediction_log exists on two calibrator classes)."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            seg = ast.get_source_segment(src, node)
            if seg:
                out.append(seg)
    return out


@pytest.mark.parametrize("rel", CALIBRATION_COPIES)
@pytest.mark.parametrize("func", CALIBRATION_FUNCS)
def test_calibration_readers_exclude_nowcast(rel, func):
    path = os.path.join(REPO, rel)
    bodies = _sources_named(path, func)
    assert bodies, f"{rel}: no function named {func} — did it move/rename?"
    for body in bodies:
        assert "prediction_log" in body, f"{rel}:{func} no longer reads prediction_log"
        assert PREDICATE in body, (
            f"c12 REGRESSION — {rel}:{func} pools prediction_log without the "
            f"nowcast exclusion; WB nowcast rows will contaminate the fit."
        )


@pytest.mark.parametrize("rel", DATABASE_COPIES)
@pytest.mark.parametrize("func", DATABASE_FUNCS)
def test_database_pooled_readers_exclude_nowcast(rel, func):
    path = os.path.join(REPO, rel)
    bodies = _sources_named(path, func)
    assert bodies, f"{rel}: no method named {func} — did it move/rename?"
    for body in bodies:
        assert PREDICATE in body, (
            f"c12 REGRESSION — {rel}:{func} pools prediction_log without the "
            f"nowcast exclusion."
        )


@pytest.mark.parametrize("rel", CALIBRATION_COPIES + DATABASE_COPIES)
def test_predicate_is_null_safe(rel):
    """A bare `model_name NOT LIKE …` drops NULL-model rows (NULL propagates
    through NOT LIKE). Every occurrence must be COALESCE-wrapped so a future
    NULL model_name is KEPT, not silently excluded from calibration."""
    src = open(os.path.join(REPO, rel), encoding="utf-8").read()
    for line in src.splitlines():
        if PREDICATE in line and "#" not in line.split(PREDICATE)[0]:
            assert "COALESCE(" in line, (
                f"{rel}: nowcast predicate is not NULL-safe -> {line.strip()}"
            )


def test_both_copies_have_identical_predicate_counts():
    """The vendored mirror must not drift from the top-level module."""
    def count(rel):
        return open(os.path.join(REPO, rel), encoding="utf-8").read().count(PREDICATE)

    assert count(CALIBRATION_COPIES[0]) == count(CALIBRATION_COPIES[1])
    assert count(DATABASE_COPIES[0]) == count(DATABASE_COPIES[1])
