#!/usr/bin/env python3
"""Tests for fit_calibrator's PURE core (row building / time split / fit+eval / refusal).

Run:  python -m esports_silo.scripts.test_fit_calibrator   (exit 0 = all pass)
"""
from __future__ import annotations

import random

try:
    from . import fit_calibrator as fc
    from ..signal.sharp_consensus import PlattCalibrator
except ImportError:
    import fit_calibrator as fc  # type: ignore
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from signal.sharp_consensus import PlattCalibrator  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def test_build_training_rows():
    flat = [
        {"match_id": "m1", "book": "pinnacle", "team_a_odds": 1.8, "team_b_odds": 2.1,
         "line_time": "t", "winner": "team_a", "start_time": "2026-01-02T00:00:00Z", "game": "cs2"},
        {"match_id": "m1", "book": "singbet", "team_a_odds": 1.82, "team_b_odds": 2.05,
         "line_time": "t", "winner": "team_a", "start_time": "2026-01-02T00:00:00Z", "game": "cs2"},
        {"match_id": "m2", "book": "pinnacle", "team_a_odds": 3.0, "team_b_odds": 1.4,
         "line_time": "t", "winner": "team_b", "start_time": "2026-01-01T00:00:00Z", "game": "cs2"},
        {"match_id": "m3", "book": "pinnacle", "team_a_odds": 2.0, "team_b_odds": 1.9,
         "line_time": "t", "winner": None, "start_time": "2026-01-03T00:00:00Z", "game": "cs2"},
    ]
    rows = fc.build_training_rows(flat)
    check("unlabelled match dropped", all(r["match_id"] != "m3" for r in rows))
    check("two books grouped under one match",
          next(r for r in rows if r["match_id"] == "m1")["book_odds"].__len__() == 2)
    check("outcome mapping team_a->1 team_b->0",
          next(r for r in rows if r["match_id"] == "m1")["outcome"] == 1
          and next(r for r in rows if r["match_id"] == "m2")["outcome"] == 0)
    check("time-sorted (oldest first)", [r["match_id"] for r in rows] == ["m2", "m1"])


def test_time_split():
    rows = [{"start_time": f"2026-01-{d:02d}", "match_id": str(d)} for d in range(1, 11)]
    train, hold = fc.time_split(rows, 0.2)
    check("80/20 split sizes", len(train) == 8 and len(hold) == 2)
    check("holdout is the NEWEST slice (never shuffled)",
          [r["match_id"] for r in hold] == ["9", "10"])
    try:
        fc.time_split(rows, 1.5)
        check("bad frac raises", False)
    except ValueError:
        check("bad frac raises", True)


def _synth_rows(n=600, seed=7):
    """Synthetic labelled matches: books quote raw (vig-kept) odds around a true prob."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        true_p = rng.random()
        books = []
        for name in ("pinnacle", "singbet"):
            pa = min(0.97, max(0.03, true_p + rng.uniform(-0.03, 0.03)))
            margin = rng.uniform(1.03, 1.06)  # vig KEPT in the quotes
            books.append({"book": name, "team_a_odds": margin / pa,
                          "team_b_odds": margin / (1 - pa), "line_time": "t"})
        rows.append({"match_id": f"m{i}", "game": "cs2",
                     "start_time": f"2026-01-01T{i % 24:02d}:{i % 60:02d}:00Z",
                     "outcome": 1 if rng.random() < true_p else 0, "book_odds": books})
    rows.sort(key=lambda r: str(r["start_time"]))
    return rows


def test_fit_and_eval():
    cal, rep = fc.fit_and_eval(_synth_rows(), holdout_frac=0.2)
    check("fits on enough synthetic data", cal.fitted and rep["fitted"])
    check("train/holdout counted", rep["n_train"] + rep["n_holdout"] == rep["n_rows"])
    h = rep["holdout"]
    check("holdout evaluated", h["n"] > 0 and h["model_brier"] is not None)
    check("calibration beats raw vig-kept score on synthetic (brier_vs_raw_score > 0)",
          h["brier_vs_raw_score"] is not None and h["brier_vs_raw_score"] > 0)
    check("holdout ECE sane on synthetic", h["model_ece"] is not None and h["model_ece"] < 0.10)
    check("baseline honestly labelled", "NOT Polymarket" in rep["baseline_note"])
    check("no refusal on a good fit", fc.refuse_reasons(cal, rep) == [])

    # tiny sample -> refuses to fit -> refusal reasons non-empty
    cal2, rep2 = fc.fit_and_eval(_synth_rows(20), holdout_frac=0.2)
    check("tiny sample refuses fit", not cal2.fitted and fc.refuse_reasons(cal2, rep2))


def test_artifact_roundtrip():
    cal, rep = fc.fit_and_eval(_synth_rows(), holdout_frac=0.2)
    art = fc.make_artifact(cal, rep, ["pinnacle"], ["cs2"], "2026-07-03T00:00:00+00:00")
    check("artifact carries model_version", art["model_version"] == fc.MODEL_VERSION)
    check("artifact halt note present", "no_bet" in art["halt_note"])
    loaded = PlattCalibrator.from_dict(art["calibrator"])
    check("roundtrip: loaded calibrator fitted", loaded.fitted and loaded.n == cal.n)
    for s in (0.3, 0.5, 0.8):
        if abs((loaded.predict(s) or 0) - (cal.predict(s) or 1)) > 1e-12:
            check("roundtrip: identical predictions", False)
            break
    else:
        check("roundtrip: identical predictions", True)
    try:
        PlattCalibrator.from_dict({"w": "garbage"})
        check("corrupt artifact raises (never silently unfitted)", False)
    except ValueError:
        check("corrupt artifact raises (never silently unfitted)", True)


def test_refusal_on_miscalibration():
    cal, rep = fc.fit_and_eval(_synth_rows(), holdout_frac=0.2)
    rep_bad = dict(rep, holdout=dict(rep["holdout"], model_ece=0.25))
    reasons = fc.refuse_reasons(cal, rep_bad)
    check("high holdout ECE refused", any("ECE" in r for r in reasons))
    rep_empty = dict(rep, holdout=dict(rep["holdout"], n=0))
    check("empty holdout refused", any("holdout" in r for r in fc.refuse_reasons(cal, rep_empty)))


if __name__ == "__main__":
    import sys
    test_build_training_rows()
    test_time_split()
    test_fit_and_eval()
    test_artifact_roundtrip()
    test_refusal_on_miscalibration()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
else:
    test_build_training_rows()
    test_time_split()
    test_fit_and_eval()
    test_artifact_roundtrip()
    test_refusal_on_miscalibration()
