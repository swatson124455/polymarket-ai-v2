#!/usr/bin/env python3
"""Tests for skill_report.build_records (pure) + runner.load_signal (file-based).

Run:  python -m esports_silo.scripts.test_skill_report   (exit 0 = all pass)
"""
from __future__ import annotations

import json
import os
import tempfile

try:
    from . import skill_report as sr
    from ..run import runner
    from ..scripts import fit_calibrator as fc
except ImportError:
    import skill_report as sr  # type: ignore
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from run import runner  # type: ignore
    import fit_calibrator as fc  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def test_build_records():
    preds = [
        {"match_id": "m1", "market_id": "x1", "p_model": 0.7, "market_price": 0.6,
         "winner": "team_a", "game": "cs2"},
        {"match_id": "m2", "market_id": "x2", "p_model": 0.4, "market_price": 0.5,
         "winner": "team_b", "game": "lol"},
        {"match_id": "m3", "market_id": "x3", "p_model": 0.5, "market_price": 0.5,
         "winner": None, "game": "cs2"},          # unresolved — must be dropped
    ]
    closing = {"m1": {"team_a_odds": 1.55, "team_b_odds": 2.6}}
    recs = sr.build_records(preds, closing)
    check("unresolved dropped", len(recs) == 2)
    check("outcome team_a->1", recs[0]["outcome"] == 1 and recs[1]["outcome"] == 0)
    check("closing odds attached when present", recs[0].get("team_a_odds") == 1.55)
    check("no closing odds -> key absent (not None-polluted)", "team_a_odds" not in recs[1])
    check("market_price carried for the gate", recs[0]["market_price"] == 0.6)


def _write_artifact(d, path):
    with open(path, "w") as f:
        json.dump(d, f)


def test_load_signal():
    cal, rep = fc.fit_and_eval(fc_synth(), holdout_frac=0.2)
    good = fc.make_artifact(cal, rep, ["pinnacle"], ["cs2"], "2026-07-03T00:00:00+00:00")
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "cal.json")

        check("missing artifact -> None (skip, not crash)",
              runner.load_signal(os.path.join(td, "nope.json")) is None)

        _write_artifact(good, p)
        sig = runner.load_signal(p)
        check("good artifact -> fitted signal", sig is not None and sig.calibrator.fitted)
        out = sig.predict([{"book": "pinnacle", "team_a_odds": 1.8, "team_b_odds": 2.1}])
        check("loaded signal emits a probability", out.calibrated and out.p_model is not None)

        _write_artifact(dict(good, model_version="something_else_v9"), p)
        check("model_version mismatch refused", runner.load_signal(p) is None)

        unfitted = dict(good, calibrator=dict(good["calibrator"], fitted=False))
        _write_artifact(unfitted, p)
        check("unfitted calibrator refused (Cmd 4)", runner.load_signal(p) is None)

        _write_artifact(dict(good, calibrator={"w": "garbage"}), p)
        check("corrupt calibrator refused, not crashed", runner.load_signal(p) is None)

        with open(p, "w") as f:
            f.write("not json{{{")
        check("unparseable file refused, not crashed", runner.load_signal(p) is None)


def fc_synth():
    import random
    rng = random.Random(11)
    rows = []
    for i in range(400):
        true_p = rng.random()
        pa = min(0.97, max(0.03, true_p + rng.uniform(-0.03, 0.03)))
        margin = rng.uniform(1.03, 1.06)
        rows.append({"match_id": f"m{i}", "game": "cs2",
                     "start_time": f"2026-01-01T{i % 24:02d}:{i % 60:02d}:00Z",
                     "outcome": 1 if rng.random() < true_p else 0,
                     "book_odds": [{"book": "pinnacle", "team_a_odds": margin / pa,
                                    "team_b_odds": margin / (1 - pa), "line_time": "t"}]})
    rows.sort(key=lambda r: str(r["start_time"]))
    return rows


if __name__ == "__main__":
    import sys
    test_build_records()
    test_load_signal()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
else:
    test_build_records()
    test_load_signal()
