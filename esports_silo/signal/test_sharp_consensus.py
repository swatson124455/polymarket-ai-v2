#!/usr/bin/env python3
"""Self-contained tests for the sharp-consensus signal — stdlib asserts only.

Run:  python -m esports_silo.signal.test_sharp_consensus   (exit 0 = all pass)
Emphasis: NO de-vig (vig is kept), the signal refuses to emit a probability until calibrated,
and the calibrator learns a monotone map from OUTCOMES.
"""
from __future__ import annotations

import random

try:
    from . import sharp_consensus as s
except ImportError:
    import sharp_consensus as s  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def approx(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


# --- raw consensus: NO de-vig, vig KEPT ----------------------------------------------
books = [
    {"book": "pinnacle", "team_a_odds": 2.0, "team_b_odds": 2.0},     # raw a=0.5, overround 1.0? 0.5+0.5=1.0
    {"book": "singbet", "team_a_odds": 1.9, "team_b_odds": 2.1},      # raw a=0.5263
    {"book": "thunderpick", "team_a_odds": 2.1, "team_b_odds": 1.9},  # raw a=0.4762
]
c = s.consensus_raw_score(books)
check("consensus uses raw 1/odds_a (not normalised)",
      approx(c.raw_score, (0.5 + 1/1.9 + 1/2.1) / 3))
check("n_books counted", c.n_books == 3)
check("per_book raw implied recorded", approx(c.per_book["singbet"], 1/1.9))
# a vig-bearing book: 1/1.8 + 1/1.8 = 1.111 > 1 -> overround kept, NOT stripped
vig = s.consensus_raw_score([{"book": "b", "team_a_odds": 1.8, "team_b_odds": 1.8}])
check("overround KEPT (>1, no de-vig)", vig.overround_mean is not None and vig.overround_mean > 1.0)
check("raw_score for 1.8 book = 1/1.8 (vig-inflated, not 0.5)", approx(vig.raw_score, 1/1.8))
check("empty books -> raw_score None", s.consensus_raw_score([]).raw_score is None)
check("skips rows without team_a_odds",
      s.consensus_raw_score([{"book": "x"}]).n_books == 0)

# --- signal refuses a probability until calibrated (Cmd 4) ----------------------------
unfit = s.SharpSignal().predict(books, market_price=0.5)
check("uncalibrated -> p_model None", unfit.p_model is None and unfit.calibrated is False)
check("uncalibrated -> raw_score present", unfit.raw_score is not None)
check("uncalibrated -> edge None (no prob to compare)", unfit.edge is None)
check("uncalibrated -> honest note", "NOT a probability" in unfit.note)

# --- calibrator: refuses tiny / single-class samples ---------------------------------
cal = s.PlattCalibrator()
cal.fit([0.5, 0.6], [1, 0])                       # < CALIB_MIN_N
check("calibrator refuses tiny sample", cal.fitted is False)
cal2 = s.PlattCalibrator()
cal2.fit([0.5 + i * 0.001 for i in range(100)], [1] * 100)  # single class
check("calibrator refuses single-class", cal2.fitted is False)

# --- calibrator learns a MONOTONE map from outcomes ----------------------------------
rng = random.Random(7)
scores, outs = [], []
for _ in range(600):
    true_p = rng.random()
    raw = min(0.55, max(0.03, true_p * 1.04))     # vig-inflated raw score, monotone in truth
    scores.append(raw)
    outs.append(1 if rng.random() < true_p else 0)
fit = s.PlattCalibrator().fit(scores, outs)
check("calibrator fits on enough 2-class data", fit.fitted is True and fit.n >= s.CALIB_MIN_N)
lo, hi = fit.predict(0.10), fit.predict(0.52)
check("calibrated prob in [0,1]", 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0)
check("monotone: higher raw score -> higher P(team_a)", hi > lo)
# calibration sanity: a mid raw score maps to a mid-ish probability
mid = fit.predict(0.30)
check("mid score -> plausible mid prob", 0.15 < mid < 0.85)

# --- end-to-end signal with a fitted calibrator --------------------------------------
hist = []
ys = []
for _ in range(400):
    true_p = rng.random()
    bset = []
    for name in ("pinnacle", "singbet", "thunderpick"):
        pa = min(0.97, max(0.03, true_p + rng.uniform(-0.03, 0.03)))
        m = rng.uniform(1.02, 1.06)
        bset.append({"book": name, "team_a_odds": m / pa, "team_b_odds": m / (1 - pa)})
    hist.append(bset)
    ys.append(1 if rng.random() < true_p else 0)
sig = s.SharpSignal().fit(hist, ys)
out = sig.predict(hist[0], market_price=0.5)
check("fitted signal emits a probability", out.calibrated and out.p_model is not None
      and 0.0 <= out.p_model <= 1.0)
check("edge computed as DIAGNOSTIC when both present", out.edge is not None
      and approx(out.edge, out.p_model - 0.5))

# --- Cmd 2 guard: no normalise/devig/overround-STRIP symbol exported ------------------
banned = [n for n in ("devig", "de_vig", "normalize", "normalise", "strip_vig", "shin",
                      "remove_overround") if hasattr(s, n)]
check("no de-vig / normalise symbol exported (Cmd 2)", banned == [])

if __name__ == "__main__":
    import sys
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
