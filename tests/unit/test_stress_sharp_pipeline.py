"""CI wrapper for the sharp-line pipeline stress harness.

Runs the synthetic ground-truth stress suite (quick config: 400 matches, no
scale stage) end-to-end through the REAL production chain. All scenarios must
pass — see esports_v2/scripts/stress_sharp_pipeline.py for what S1..S7 assert
(label integrity, no look-ahead, calibration + planted-edge recovery,
orientation flip blind-spot/damage, sibling veto, garbage resilience).
"""
from esports_v2.scripts.stress_sharp_pipeline import run


def test_stress_suite_quick_passes(capsys):
    rc = run(n_matches=400, seed=20260710, scale_lines=0)
    out = capsys.readouterr().out
    assert rc == 0, f"stress suite failed:\n{out}"
    assert "STRESS RESULT: PASS" in out
