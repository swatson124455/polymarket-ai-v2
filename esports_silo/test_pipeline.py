#!/usr/bin/env python3
"""Self-contained tests for the end-to-end pipeline — stdlib asserts only.

Run:  python -m esports_silo.test_pipeline   (exit 0 = all pass)
"""
from __future__ import annotations

import os
import random

os.environ["SILO_ENTRY_HALT"] = "false"   # exercise the decision path; set before imports

try:
    from . import pipeline as pl
    from .signal.sharp_consensus import SharpSignal
except ImportError:
    import pipeline as pl  # type: ignore
    from signal.sharp_consensus import SharpSignal  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def _fitted_signal():
    rng = random.Random(3)
    hist, ys = [], []
    for _ in range(400):
        tp = rng.random()
        bset = [{"book": b, "team_a_odds": rng.uniform(1.02, 1.06) / max(0.03, min(0.97, tp)),
                 "team_b_odds": rng.uniform(1.02, 1.06) / max(0.03, min(0.97, 1 - tp))}
                for b in ("pinnacle", "singbet", "thunderpick")]
        hist.append(bset)
        ys.append(1 if rng.random() < tp else 0)
    return SharpSignal().fit(hist, ys)


match = {"match_id": "m1", "team_a": "T1", "team_b": "Gen.G", "start_time": "2026-07-01T10:00:00+00:00"}
odds = [{"book": "pinnacle", "team_a_odds": 1.5, "team_b_odds": 2.6},
        {"book": "singbet", "team_a_odds": 1.55, "team_b_odds": 2.5},
        {"book": "thunderpick", "team_a_odds": 1.48, "team_b_odds": 2.7}]

# --- uncalibrated signal -> p_model None, decision no_bet ------------------------------
rec0 = pl.build_prediction(match, odds, 0.55, SharpSignal(), skill_proven=True, bankroll=1000.0)
check("uncalibrated -> p_model None", rec0.p_model is None and rec0.calibrated is False)
check("uncalibrated -> no_bet", rec0.decision == "no_bet")
check("record carries schema fields", rec0.model_version == pl.MODEL_VERSION
      and rec0.signal_source == pl.SIGNAL_SOURCE and rec0.event_time == match["start_time"])
check("n_books counted through pipeline", rec0.n_books == 3)

# --- fitted signal, skill NOT proven -> still no_bet (price-deferring) ----------------
sig = _fitted_signal()
rec1 = pl.build_prediction(match, odds, 0.55, sig, skill_proven=False, bankroll=1000.0)
check("fitted -> p_model present", rec1.p_model is not None and rec1.calibrated)
check("edge computed as diagnostic", rec1.edge is not None)
check("skill not proven -> no_bet regardless of edge", rec1.decision == "no_bet")

# --- fitted + skill proven -> a real decision, and stake within cap ------------------
rec2 = pl.build_prediction(match, odds, 0.55, sig, skill_proven=True, bankroll=1000.0)
check("fitted + proven -> decision made", rec2.decision in ("bet_a", "bet_b", "no_bet"))
check("stake within cap", 0.0 <= rec2.stake_usd <= 300.0)

# --- process_market: link then predict -----------------------------------------------
market = {"market_id": "0xabc", "question": "T1 vs Gen.G — Match Winner",
          "yes_price": 0.60, "start_time": match["start_time"]}
candidates = [match, {"match_id": "m2", "team_a": "T1", "team_b": "KT", "start_time": match["start_time"]}]
odds_by = {"m1": odds}
rec3 = pl.process_market(market, candidates, odds_by, {}, sig, skill_proven=False, bankroll=1000.0)
check("process_market links to right match", rec3 is not None and rec3.match_id == "m1")
check("process_market carries market_id", rec3.market_id == "0xabc")
check("process_market uses market price", rec3.market_price == 0.60)

# unlinkable market -> None (no forced prediction)
noise = {"market_id": "x", "question": "Some unrelated politics market", "yes_price": 0.5}
check("unlinkable market -> None", pl.process_market(noise, candidates, odds_by, {}, sig,
      skill_proven=True, bankroll=1000.0) is None)

# --- no odds for the linked match -> handled gracefully ------------------------------
rec4 = pl.process_market(market, candidates, {}, {}, sig, skill_proven=True, bankroll=1000.0)
check("no odds -> p_model None, no crash, no_bet", rec4 is not None
      and rec4.p_model is None and rec4.decision == "no_bet")

if __name__ == "__main__":
    import sys
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
