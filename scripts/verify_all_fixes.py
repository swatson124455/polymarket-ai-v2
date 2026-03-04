#!/usr/bin/env python3
"""
Verify all data pipeline and training fixes.
Run after changes to: poly_data, import script, dashboard, prediction engine.
Usage: python scripts/verify_all_fixes.py
"""
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS = [
    "tests/unit/test_poly_data_fixes.py",
    "tests/unit/test_prediction_price_fallback.py",
    "tests/unit/test_ingestion_historical_price_flow.py",
]


def main() -> int:
    """Run verification tests. Returns 0 on success, 1 on failure."""
    print("=" * 60)
    print("Verifying all fixes (Poly Data, import, prediction fallback)")
    print("=" * 60)
    cmd = [
        sys.executable, "-m", "pytest",
        *TESTS,
        "-v",
        "--no-cov",
        "-x",  # Stop on first failure
    ]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode == 0:
        print("\n[OK] All fixes verified.")
    else:
        print("\n[FAIL] Verification failed.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
