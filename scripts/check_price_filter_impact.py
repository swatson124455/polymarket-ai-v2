"""
Diagnostic: Check how many trades would be dropped by the 0-1 price filter.
Run on poly_data/processed/trades.csv before import to measure data loss.
"""
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

_PFX = "[price_filter] "
TRADES_PATH = _project_root / "poly_data" / "processed" / "trades.csv"


def main() -> None:
    import pandas as pd

    if not TRADES_PATH.exists():
        print(f"{_PFX}File not found: {TRADES_PATH}")
        print(f"{_PFX}Run poly_data update_all.py first.")
        sys.exit(1)

    df = pd.read_csv(TRADES_PATH)
    total = len(df)
    if total == 0:
        print(f"{_PFX}No rows in {TRADES_PATH}")
        sys.exit(0)

    if "price" not in df.columns:
        print(f"{_PFX}Column 'price' not found. Columns: {list(df.columns)}")
        sys.exit(1)

    out_of_range = (df["price"] < 0) | (df["price"] > 1)
    count_out = out_of_range.sum()
    pct = 100.0 * count_out / total if total else 0
    min_p = float(df["price"].min()) if "price" in df.columns else float("nan")
    max_p = float(df["price"].max()) if "price" in df.columns else float("nan")

    print(f"{_PFX}Price filter diagnostic: {TRADES_PATH}")
    print(f"{_PFX}Total rows: {total:,}")
    print(f"{_PFX}Out of range [0,1]: {count_out:,} ({pct:.2f}%)")
    print(f"{_PFX}Min price: {min_p}, Max price: {max_p}")

    if count_out > 0 and pct > 0.1:
        print(f"{_PFX}WARNING: >0.1% of trades would be dropped. Verify filter is correct.")
    elif count_out > 0:
        print(f"{_PFX}Info: {count_out} trades outside [0,1] will be skipped by import.")
    else:
        print(f"{_PFX}OK: All prices in [0,1].")


if __name__ == "__main__":
    main()
