#!/usr/bin/env python3
"""Self-contained tests for skill_metrics — stdlib asserts only (no pytest needed).

Run:  python -m esports_silo.eval.test_skill_metrics   (exit 0 = all pass)
Covers: scorer correctness on hand-computable cases, edge/empty handling, the RAW
closing-line (no de-vig), the market-skill sign convention, and the P&L-free invariant.
"""
from __future__ import annotations

import math

try:
    from . import skill_metrics as m
except ImportError:  # allow running as a loose file
    import skill_metrics as m  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def approx(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


# --- Brier: hand-computed ------------------------------------------------------------
# preds: (0.0,y=0)->0, (1.0,y=1)->0, (0.5,y=1)->0.25, (0.5,y=0)->0.25  => mean 0.125
b = m.brier([{"p_model": 0.0, "outcome": 0}, {"p_model": 1.0, "outcome": 1},
             {"p_model": 0.5, "outcome": 1}, {"p_model": 0.5, "outcome": 0}])
check("brier hand value 0.125", approx(b, 0.125))
check("brier perfect = 0", approx(m.brier([{"p_model": 1.0, "outcome": 1},
                                           {"p_model": 0.0, "outcome": 0}]), 0.0))
check("brier empty -> None", m.brier([]) is None)

# --- log loss: single perfect-ish and a 0.5 case -------------------------------------
ll = m.log_loss([{"p_model": 0.5, "outcome": 1}])
check("log_loss(0.5) = ln2", approx(ll, math.log(2)))
check("log_loss clamps (no inf on p=1,y=0)", m.log_loss([{"p_model": 1.0, "outcome": 0}]) < 40)

# --- accuracy ------------------------------------------------------------------------
acc = m.accuracy([{"p_model": 0.9, "outcome": 1}, {"p_model": 0.1, "outcome": 0},
                  {"p_model": 0.9, "outcome": 0}])
check("accuracy 2/3", approx(acc, 2 / 3))
check("accuracy tie p=0.5 counts wrong", approx(
    m.accuracy([{"p_model": 0.5, "outcome": 1}]), 0.0))

# --- ECE: perfectly calibrated extreme bins -> 0 -------------------------------------
e, bins = m.ece([{"p_model": 1.0, "outcome": 1}] * 10 + [{"p_model": 0.0, "outcome": 0}] * 10)
check("ece perfectly calibrated ~0", approx(e, 0.0, 1e-9))
# a systematically overconfident set -> ECE > 0
e2, _ = m.ece([{"p_model": 0.9, "outcome": 0}] * 10)
check("ece overconfident > 0", e2 is not None and e2 > 0.5)

# --- discrimination / AUC ------------------------------------------------------------
perfect = [{"p_model": 0.8, "outcome": 1}, {"p_model": 0.7, "outcome": 1},
           {"p_model": 0.3, "outcome": 0}, {"p_model": 0.2, "outcome": 0}]
check("AUC perfect separation = 1.0", approx(m.auc(perfect), 1.0))
check("discrimination positive when aligned", (m.discrimination(perfect) or 0) > 0.9)
check("AUC ties = 0.5", approx(m.auc([{"p_model": 0.5, "outcome": 1},
                                      {"p_model": 0.5, "outcome": 0}]), 0.5))
check("AUC one-class -> None", m.auc([{"p_model": 0.7, "outcome": 1}]) is None)

# --- RAW closing line: NO de-vig / NO normalisation ----------------------------------
# odds 2.0 -> raw implied 0.5 exactly (NOT normalised against the other side)
check("raw_implied_prob(2.0)=0.5", approx(m.raw_implied_prob(2.0), 0.5))
check("raw_implied_prob(1.25)=0.8", approx(m.raw_implied_prob(1.25), 0.8))
# a two-way book with overround: 1/1.5 + 1/2.5 = 0.6667+0.4 = 1.0667 (sum>1 => vig kept, not stripped)
check("raw implied keeps vig (sum>1)",
      approx(m.raw_implied_prob(1.5) + m.raw_implied_prob(2.5), 1.0 / 1.5 + 1.0 / 2.5))
cl = m.closing_line_agreement([{"p_model": 0.5, "outcome": 1, "team_a_odds": 2.0},
                               {"p_model": 0.8, "outcome": 1, "team_a_odds": 1.25}])
check("closing-line n counts odds rows", cl["n"] == 2)
check("closing-line mean_delta ~0 when p==raw", approx(cl["mean_delta"], 0.0))
check("closing-line skips rows w/o odds",
      m.closing_line_agreement([{"p_model": 0.5, "outcome": 1}])["n"] == 0)

# --- market skill sign: model better -> brier_skill > 0 ------------------------------
# model nails it (p=outcome), market is at 0.5 -> market_brier 0.25, model_brier 0 -> skill +0.25
rep = m.evaluate([{"p_model": 1.0, "outcome": 1, "market_price": 0.5, "game": "cs2"},
                  {"p_model": 0.0, "outcome": 0, "market_price": 0.5, "game": "cs2"}])
check("brier_skill > 0 when model beats market", (rep.brier_skill or 0) > 0.2)
check("n_with_market counted", rep.n_with_market == 2)
check("per_game present", "cs2" in rep.per_game and rep.per_game["cs2"]["n"] == 2)

# model WORSE than market -> brier_skill < 0 (this is the failure the harness must catch)
worse = m.evaluate([{"p_model": 0.5, "outcome": 1, "market_price": 1.0},
                    {"p_model": 0.5, "outcome": 0, "market_price": 0.0}])
check("brier_skill < 0 when model loses to market", (worse.brier_skill or 0) < 0)

# --- skill gate: needs n, needs to beat market, needs calibration --------------------
ok_small, why = rep.passes_skill_gate()
check("gate fails on tiny n", ok_small is False and any("SKILL_MIN_N" in w for w in why))

# --- input hygiene: NaN/out-of-range/None dropped, never crash -----------------------
dirty = [{"p_model": 1.3, "outcome": 1}, {"p_model": None, "outcome": 0},
         {"p_model": 0.5, "outcome": 2}, {"p_model": 0.6, "outcome": 1}]
check("invalid rows filtered (only 1 valid)", m.brier(dirty) is not None and m.evaluate(dirty).n == 1)

# --- P&L-FREE INVARIANT: the module exposes no P&L/CLV/de-vig surface ----------------
banned = [n for n in ("pnl", "profit", "roi", "yield", "stake", "clv", "devig", "shin", "pinnacle_prob")
          if hasattr(m, n) or hasattr(m, "compute_" + n)]
check("no P&L / CLV / de-vig symbols exported (Cmd 1 & 2)", banned == [])

if __name__ == "__main__":
    import sys
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
