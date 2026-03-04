"""
Run full poly_data pipeline: markets → goldsky → fetch missing markets → process trades.
Extended with resolution, liquidity, category for bot learning.
"""
import os
import sys

# Run from poly_data directory so relative paths work
_script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(_script_dir)
sys.path.insert(0, _script_dir)

from update_utils.update_markets import update_markets
from update_utils.update_goldsky import update_goldsky
from update_utils.process_live import process_live
from poly_utils.utils import get_markets, update_missing_tokens

_PFX = "[poly_data] "
MAX_MISSING_TOKENS_PER_RUN = int(os.environ.get("POLY_DATA_MAX_MISSING_TOKENS", "300"))


def _fetch_missing_markets_for_goldsky_tokens() -> None:
    """Extract token IDs from Goldsky orderFilled that aren't in markets, fetch those markets."""
    import pandas as pd

    goldsky_path = "goldsky/orderFilled.csv"
    if not os.path.exists(goldsky_path):
        print(f"{_PFX}No {goldsky_path} - skipping missing markets fetch")
        return

    df = pd.read_csv(goldsky_path, dtype={"makerAssetId": str, "takerAssetId": str}, nrows=500000)
    all_tokens = set()
    for _, row in df.iterrows():
        m = str(row.get("makerAssetId", "") or "").strip()
        t = str(row.get("takerAssetId", "") or "").strip()
        if m and m != "0":
            all_tokens.add(m)
        if t and t != "0":
            all_tokens.add(t)

    markets_df = get_markets()
    if markets_df.empty:
        known = set()
    else:
        t1 = set(markets_df["token1"].dropna().astype(str).str.strip())
        t2 = set(markets_df["token2"].dropna().astype(str).str.strip())
        known = t1 | t2

    missing = list(all_tokens - known)
    if len(missing) > MAX_MISSING_TOKENS_PER_RUN:
        print(f"{_PFX}WARNING: {len(missing)} new markets found, cap is {MAX_MISSING_TOKENS_PER_RUN}. "
              f"Skipping {len(missing) - MAX_MISSING_TOKENS_PER_RUN}. Run update_all.py again.")
    missing = missing[:MAX_MISSING_TOKENS_PER_RUN]
    if not missing:
        print(f"{_PFX}All Goldsky tokens already in markets")
        return
    print(f"{_PFX}Fetching {len(missing)} missing markets for tokens not in markets.csv")
    update_missing_tokens(missing)


if __name__ == "__main__":
    print(f"{_PFX}Updating markets (extended: resolution, liquidity, category)")
    update_markets()
    print(f"\n{_PFX}Updating goldsky (order-filled events)")
    update_goldsky()
    print(f"\n{_PFX}Fetching missing markets for Goldsky tokens")
    _fetch_missing_markets_for_goldsky_tokens()
    print(f"\n{_PFX}Processing live trades")
    process_live()
    print(f"\n{_PFX}Done. Run scripts/import_poly_data_to_db.py to import into PostgreSQL.")
