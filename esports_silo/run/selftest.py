#!/usr/bin/env python3
"""Run every esports_silo unit-test module (stdlib only; no pytest). Exit 0 = all green.

Run from the repo root:  python -m esports_silo.run.selftest
"""
from __future__ import annotations

import subprocess
import sys

MODULES = [
    "esports_silo.eval.test_skill_metrics",
    "esports_silo.markets.test_match_matcher",
    "esports_silo.collectors.test_polymarket_collector",
    "esports_silo.collectors.test_sampling",
    "esports_silo.collectors.test_results_collector",
    "esports_silo.scripts.test_probe_microstructure",
    "esports_silo.scripts.test_probe_historical_odds",
    "esports_silo.signal.test_sharp_consensus",
    "esports_silo.betting.test_decision",
    "esports_silo.test_pipeline",
    "esports_silo.execution.test_execution",
]


def main() -> int:
    fails = []
    for m in MODULES:
        r = subprocess.run([sys.executable, "-m", m], capture_output=True, text=True)
        ok = r.returncode == 0
        print(f"  {'PASS' if ok else 'FAIL'}  {m}")
        if not ok:
            fails.append(m)
            sys.stdout.write(r.stdout[-600:])
    print(f"\n{len(MODULES) - len(fails)}/{len(MODULES)} test modules green")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
