#!/usr/bin/env python3
"""Self-contained tests for bet decision + Kelly — stdlib asserts only.

Run:  python -m esports_silo.betting.test_decision   (exit 0 = all pass)
Emphasis: the price-deferring rule (no bet without proven skill), Kelly math, hard caps,
and the SILO_ENTRY_HALT safety. Tests force SILO_ENTRY_HALT=false so the rule path is reachable.
"""
from __future__ import annotations

import os

os.environ["SILO_ENTRY_HALT"] = "false"   # must be set BEFORE importing the module
os.environ.setdefault("MAX_BET_USD", "300")
os.environ.setdefault("KELLY_FRACTION", "0.25")
os.environ.setdefault("MAX_BET_FRACTION", "0.05")

try:
    from . import decision as d
except ImportError:
    import decision as d  # type: ignore

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# --- Kelly fraction math -------------------------------------------------------------
# p=0.7, price=0.55 -> f* = (0.7-0.55)/(1-0.55) = 0.15/0.45 = 0.3333
check("kelly f* = edge/(1-price)", approx(d.kelly_fraction(0.7, 0.55), 0.15 / 0.45))
check("no edge -> 0 (p<=price)", d.kelly_fraction(0.5, 0.55) == 0.0)
check("p==price -> 0", d.kelly_fraction(0.55, 0.55) == 0.0)
check("invalid price 0 -> 0", d.kelly_fraction(0.7, 0.0) == 0.0)
check("invalid price 1 -> 0", d.kelly_fraction(0.7, 1.0) == 0.0)
check("None inputs -> 0", d.kelly_fraction(None, 0.5) == 0.0)
check("f* clamped <= 1", d.kelly_fraction(0.99, 0.01) <= 1.0)

# --- THE PRICE-DEFERRING RULE: no bet without proven skill ---------------------------
big_edge = d.decide(0.90, 0.50, 1000.0, skill_proven=False, calibrated=True)
check("huge edge but skill NOT proven -> no_bet", big_edge.action == "no_bet")
check("reason cites price-deferring", "not proven" in big_edge.reason.lower()
      or "deferring" in big_edge.reason.lower())

# same edge WITH proven skill -> a bet
proven = d.decide(0.90, 0.50, 1000.0, skill_proven=True, calibrated=True)
check("same edge, skill proven -> bet_a", proven.action == "bet_a" and proven.side == "team_a")
check("stake > 0 and within USD cap", 0 < proven.stake_usd <= 300.0)

# --- calibration gate + price validity -----------------------------------------------
check("uncalibrated -> no_bet", d.decide(None, 0.5, 1000.0, skill_proven=True,
                                         calibrated=False).action == "no_bet")
check("no market price -> no_bet", d.decide(0.7, None, 1000.0, skill_proven=True,
                                            calibrated=True).action == "no_bet")

# --- side selection: model favors team_b ---------------------------------------------
# p_model=0.3 (team_a unlikely), price 0.5 -> team_b YES at 0.5 has edge
bet_b = d.decide(0.30, 0.50, 1000.0, skill_proven=True, calibrated=True)
check("model on team_b -> bet_b", bet_b.action == "bet_b" and bet_b.side == "team_b")

# --- no edge either side -> no_bet ---------------------------------------------------
flat = d.decide(0.50, 0.50, 1000.0, skill_proven=True, calibrated=True)
check("no edge either side -> no_bet", flat.action == "no_bet")

# --- hard caps -----------------------------------------------------------------------
# quarter-Kelly of f*=0.333 on $1000 = 0.0833*1000=$83.3, but MAX_BET_FRACTION 5% caps at $50
capped = d.decide(0.70, 0.55, 1000.0, skill_proven=True, calibrated=True)
check("MAX_BET_FRACTION caps stake at 5% bankroll", capped.stake_usd <= 50.0 + 1e-9)
# huge bankroll -> MAX_BET_USD cap bites
big = d.decide(0.80, 0.50, 1_000_000.0, skill_proven=True, calibrated=True)
check("MAX_BET_USD hard cap", big.stake_usd <= 300.0)

# --- SILO_ENTRY_HALT safety (re-import with halt on) ---------------------------------
# CONFIG is a frozen singleton read at config-import time, so reload config THEN decision.
import importlib
os.environ["SILO_ENTRY_HALT"] = "true"
try:
    import esports_silo.config as _cfg
    importlib.reload(_cfg)   # package mode: re-read SILO_ENTRY_HALT into a fresh CONFIG
except Exception:
    pass                     # loose-file mode: decision._C reads env on reload below
d_halt = importlib.reload(d)
halted = d_halt.decide(0.90, 0.50, 1000.0, skill_proven=True, calibrated=True)
check("SILO_ENTRY_HALT forces no_bet even with proven skill+edge", halted.action == "no_bet")
check("halt reason cited", "HALT" in halted.reason.upper())
os.environ["SILO_ENTRY_HALT"] = "false"
try:
    import esports_silo.config as _cfg
    importlib.reload(_cfg)
except Exception:
    pass
importlib.reload(d)  # restore for any later runs

if __name__ == "__main__":
    import sys
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
