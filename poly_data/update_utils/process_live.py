"""
Process raw orderFilled events into structured trades.
Windows-compatible: uses pandas instead of polars/tail.
"""
import os

_PFX = "[poly_data] "
import sys
import warnings

warnings.filterwarnings("ignore")

# Allow imports from poly_data root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from poly_utils.utils import get_markets

PROCESSED_FILE = "processed/trades.csv"


def get_processed_df(df: pd.DataFrame) -> pd.DataFrame:
    """Transform orderFilled rows into structured trades with market_id, price, side."""
    markets_df = get_markets()
    if markets_df.empty:
        return pd.DataFrame()

    markets_df = markets_df.rename(columns={"id": "market_id"})
    markets_long = markets_df[["market_id", "token1", "token2"]].melt(
        id_vars="market_id",
        value_vars=["token1", "token2"],
        var_name="side",
        value_name="asset_id",
    )

    df = df.copy()
    df["nonusdc_asset_id"] = df.apply(
        lambda r: r["makerAssetId"] if r["makerAssetId"] != "0" else r["takerAssetId"],
        axis=1,
    )

    merged = df.merge(
        markets_long,
        left_on="nonusdc_asset_id",
        right_on="asset_id",
        how="left",
    )

    merged["makerAsset"] = merged.apply(
        lambda r: "USDC" if r["makerAssetId"] == "0" else r["side"],
        axis=1,
    )
    merged["takerAsset"] = merged.apply(
        lambda r: "USDC" if r["takerAssetId"] == "0" else r["side"],
        axis=1,
    )

    merged["makerAmountFilled"] = merged["makerAmountFilled"] / 1e6
    merged["takerAmountFilled"] = merged["takerAmountFilled"] / 1e6

    merged["taker_direction"] = merged["takerAsset"].map(lambda x: "BUY" if x == "USDC" else "SELL")
    merged["maker_direction"] = merged["takerAsset"].map(lambda x: "SELL" if x == "USDC" else "BUY")

    merged["nonusdc_side"] = merged.apply(
        lambda r: r["makerAsset"] if r["makerAsset"] != "USDC" else r["takerAsset"],
        axis=1,
    )
    merged["usd_amount"] = merged.apply(
        lambda r: r["takerAmountFilled"] if r["takerAsset"] == "USDC" else r["makerAmountFilled"],
        axis=1,
    )
    merged["token_amount"] = merged.apply(
        lambda r: r["takerAmountFilled"] if r["takerAsset"] != "USDC" else r["makerAmountFilled"],
        axis=1,
    )
    merged["price"] = merged.apply(
        lambda r: (
            r["takerAmountFilled"] / r["makerAmountFilled"]
            if r["takerAsset"] == "USDC"
            else r["makerAmountFilled"] / r["takerAmountFilled"]
        ),
        axis=1,
    )

    out = merged[
        [
            "timestamp",
            "market_id",
            "maker",
            "taker",
            "nonusdc_side",
            "maker_direction",
            "taker_direction",
            "price",
            "usd_amount",
            "token_amount",
            "transactionHash",
        ]
    ]
    return out


def process_live() -> None:
    """Process new orderFilled events into trades. Resumes from last processed row."""
    os.makedirs("processed", exist_ok=True)
    print(f"{_PFX}{'='*60}")
    print(f"{_PFX}Processing Live Trades")
    print(f"{_PFX}{'='*60}")

    last_processed = {}
    if os.path.exists(PROCESSED_FILE):
        print(f"{_PFX}Found existing processed file: {PROCESSED_FILE}")
        full = pd.read_csv(PROCESSED_FILE)
        if len(full) > 0:
            last = full.iloc[-1]
            last_processed["timestamp"] = pd.to_datetime(last["timestamp"])
            last_processed["transactionHash"] = last["transactionHash"]
            last_processed["maker"] = last["maker"]
            last_processed["taker"] = last["taker"]
            print(f"{_PFX}Resuming from: {last_processed['timestamp']}")
            print(f"{_PFX}Last hash: {str(last_processed['transactionHash'])[:16]}...")
    else:
        print(f"{_PFX}No existing processed file found - processing from beginning")

    print(f"\n{_PFX}Reading: goldsky/orderFilled.csv")
    if not os.path.exists("goldsky/orderFilled.csv"):
        print(f"{_PFX}Error: goldsky/orderFilled.csv not found. Run update_goldsky first.")
        return

    df = pd.read_csv(
        "goldsky/orderFilled.csv",
        dtype={"makerAssetId": str, "takerAssetId": str},
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    print(f"{_PFX}Loaded {len(df):,} rows")

    if last_processed:
        mask_ts = df["timestamp"] == last_processed["timestamp"]
        mask_hash = df["transactionHash"] == last_processed["transactionHash"]
        mask_maker = df["maker"] == last_processed["maker"]
        mask_taker = df["taker"] == last_processed["taker"]
        same = df[mask_ts & mask_hash & mask_maker & mask_taker]
        if len(same) > 0:
            last_idx = same.index[-1]
            df_process = df.loc[df.index > last_idx].copy()
        else:
            df_process = df.copy()
    else:
        df_process = df.copy()

    print(f"{_PFX}Processing {len(df_process):,} new rows...")
    if df_process.empty:
        print(f"{_PFX}No new rows to process.")
        print(f"{_PFX}{'='*60}")
        return

    new_df = get_processed_df(df_process)
    new_df = new_df.dropna(subset=["market_id"])
    if new_df.empty:
        print(f"{_PFX}No new trades after processing (markets may not match).")
        print(f"{_PFX}{'='*60}")
        return

    os.makedirs("processed", exist_ok=True)
    if not os.path.isfile(PROCESSED_FILE):
        new_df.to_csv(PROCESSED_FILE, index=False)
        print(f"{_PFX}Created new file: {PROCESSED_FILE}")
    else:
        new_df.to_csv(PROCESSED_FILE, index=False, mode="a", header=False)
        print(f"{_PFX}Appended {len(new_df):,} rows to {PROCESSED_FILE}")

    print(f"{_PFX}{'='*60}")
    print(f"{_PFX}Processing complete!")
    print(f"{_PFX}{'='*60}")


if __name__ == "__main__":
    process_live()
