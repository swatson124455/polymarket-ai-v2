"""Tests for maker_preflight's offline-testable parts. The network stages run
only on the VPS with the operator wallet; what IS testable here is the wiring
that must not silently rot: the scoring stage's cancel-shape probe must load
the ENGINE'S OWN _cancel_shortfall (not a drifted copy) and that parser must
behave as the engine's suite proves it does."""
import importlib.util
import pathlib


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(
        name, pathlib.Path(__file__).resolve().parents[1] / "scripts" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pf = _load("maker_preflight", "maker_preflight.py")


def test_engine_cancel_shortfall_loads_the_real_parser():
    fn = pf.engine_cancel_shortfall()
    mle = _load("mle_ref", "maker_live_engine.py")
    # behavioral identity with the engine's parser on the canonical shapes
    for resp, req in ((None, ["a"]), ({"canceled": ["a"]}, ["a"]),
                      ({"canceled": []}, ["a"]),
                      ({"not_canceled": {"a": "x"}}, ["a"]),
                      ({"canceled": ["a", "b"]}, ["a", "b", "c"])):
        assert fn(resp, req) == mle._cancel_shortfall(resp, req)


def test_cancel_shape_probe_verdicts():
    fn = pf.engine_cancel_shortfall()
    assert fn({"canceled": ["oid1"]}, ["oid1"]) == []          # proven
    assert fn({"success": True}, ["oid1"])                     # shape unproven
    assert fn("OK", ["oid1"])                                  # non-dict unproven
