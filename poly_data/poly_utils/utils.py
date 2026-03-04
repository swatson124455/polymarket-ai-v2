"""
Market loading and missing token handling.
Extended with resolution, liquidity, category for update_missing_tokens.
"""
import csv

_PFX = "[poly_data] "
import json
import os
import time
from typing import List

import pandas as pd
import requests

PLATFORM_WALLETS = [
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
]

HEADERS = [
    "createdAt",
    "id",
    "question",
    "answer1",
    "answer2",
    "neg_risk",
    "market_slug",
    "token1",
    "token2",
    "condition_id",
    "volume",
    "ticker",
    "closedTime",
    "resolved",
    "resolution",
    "liquidity",
    "category",
]


def get_markets(
    main_file: str = "markets.csv",
    missing_file: str = "missing_markets.csv",
) -> pd.DataFrame:
    """
    Load and combine markets from both files, deduplicate, and sort by createdAt.
    Returns combined DataFrame sorted by creation date.
    """
    dfs = []
    dtype_overrides = {"token1": str, "token2": str}
    if os.path.exists(main_file):
        main_df = pd.read_csv(main_file, dtype=dtype_overrides, low_memory=False)
        dfs.append(main_df)
        print(f"Loaded {len(main_df)} markets from {main_file}")
    if os.path.exists(missing_file):
        missing_df = pd.read_csv(missing_file, dtype=dtype_overrides, low_memory=False)
        dfs.append(missing_df)
        print(f"{_PFX}Loaded {len(missing_df)} markets from {missing_file}")
    if not dfs:
        print(f"{_PFX}No market files found!")
        return pd.DataFrame()
    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=["id"], keep="first")
    combined = combined.sort_values("createdAt").reset_index(drop=True)
    print(f"{_PFX}Combined total: {len(combined)} unique markets (sorted by createdAt)")
    return combined


def _parse_resolution(market: dict) -> tuple:
    """Extract resolved and resolution from Gamma response."""
    resolved = (
        market.get("resolved")
        or market.get("isResolved")
        or market.get("closed", False)
    )
    resolution = (
        market.get("resolution")
        or market.get("outcome")
        or market.get("resolutionPrice")
    )
    if resolution is not None:
        rv = str(resolution).strip().upper()
        resolution = rv if rv in ("YES", "NO") else rv
    if resolution is None and resolved:
        prices = market.get("outcomePrices")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except json.JSONDecodeError:
                prices = None
        if prices and len(prices) >= 2:
            p0 = float(prices[0]) if prices[0] else 0
            p1 = float(prices[1]) if prices[1] else 0
            resolution = "YES" if p0 > p1 else "NO"
    return (bool(resolved), resolution or "")


def _parse_category(market: dict) -> str:
    cat = market.get("category", "")
    if not cat and market.get("events"):
        ev = market["events"][0] if market["events"] else {}
        cat = ev.get("category", "") or ev.get("groupItemTitle", "")
    if isinstance(cat, list):
        cat = cat[0] if cat else ""
    return str(cat)[:100] if cat else ""


def update_missing_tokens(
    missing_token_ids: List[str],
    csv_filename: str = "missing_markets.csv",
) -> None:
    """
    Fetch market data for missing token IDs and save to separate CSV file.
    Extended with resolved, resolution, liquidity, category.
    """
    if not missing_token_ids:
        print(f"{_PFX}No missing tokens to fetch")
        return

    print(f"{_PFX}Fetching {len(missing_token_ids)} missing tokens...")
    file_exists = os.path.exists(csv_filename)
    new_markets = []
    processed_market_ids = set()

    if file_exists:
        try:
            existing = pd.read_csv(csv_filename)
            processed_market_ids = set(existing["id"].dropna().astype(str))
            print(f"{_PFX}Found {len(processed_market_ids)} existing markets in {csv_filename}")
        except Exception as e:
            print(f"{_PFX}Error reading existing file: {e}")

    for token_id in missing_token_ids:
        retry_count = 0
        max_retries = 3
        while retry_count < max_retries:
            try:
                response = requests.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"clob_token_ids": token_id},
                    timeout=30,
                )
                if response.status_code == 429:
                    print(f"{_PFX}Rate limited - waiting 10 seconds...")
                    time.sleep(10)
                    continue
                if response.status_code != 200:
                    print(f"{_PFX}API error {response.status_code} for token {token_id}")
                    retry_count += 1
                    time.sleep(2)
                    continue

                markets = response.json()
                if not markets:
                    print(f"{_PFX}No market found for token {token_id}")
                    break

                market = markets[0]
                market_id = market.get("id", "")
                if market_id in processed_market_ids:
                    print(f"{_PFX}Market {market_id} already exists - skipping")
                    break

                clob_str = market.get("clobTokenIds", "[]")
                clob_tokens = json.loads(clob_str) if isinstance(clob_str, str) else clob_str
                if len(clob_tokens) < 2:
                    print(f"{_PFX}Invalid token data for {token_id}")
                    break

                token1, token2 = clob_tokens[0], clob_tokens[1]
                outcomes_str = market.get("outcomes", "[]")
                outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
                answer1 = outcomes[0] if len(outcomes) > 0 else "YES"
                answer2 = outcomes[1] if len(outcomes) > 1 else "NO"
                neg_risk = market.get("negRiskAugmented", False) or market.get("negRiskOther", False)
                ticker = ""
                if market.get("events") and len(market.get("events", [])) > 0:
                    ticker = market["events"][0].get("ticker", "")
                question_text = market.get("question", "") or market.get("title", "")
                resolved, resolution = _parse_resolution(market)
                liquidity = float(market.get("liquidity", 0) or 0)
                category = _parse_category(market)

                row = [
                    market.get("createdAt", ""),
                    market_id,
                    question_text,
                    answer1,
                    answer2,
                    neg_risk,
                    market.get("slug", ""),
                    token1,
                    token2,
                    market.get("conditionId", ""),
                    market.get("volume", ""),
                    ticker,
                    market.get("closedTime", ""),
                    resolved,
                    resolution,
                    liquidity,
                    category,
                ]
                new_markets.append(row)
                processed_market_ids.add(market_id)
                print(f"{_PFX}Successfully fetched market {market_id} for token {token_id}")
                break
            except Exception as e:
                print(f"{_PFX}Error fetching token {token_id}: {e}")
                retry_count += 1
                time.sleep(2)
        if retry_count >= max_retries:
            print(f"{_PFX}Failed to fetch token {token_id} after {max_retries} retries")
        time.sleep(0.5)

    if not new_markets:
        print(f"{_PFX}No new markets to add")
        return

    mode = "a" if file_exists else "w"
    with open(csv_filename, mode, newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        if not file_exists:
            writer.writerow(HEADERS)
        writer.writerows(new_markets)

    print(f"{_PFX}Added {len(new_markets)} new markets to {csv_filename}")
    print(f"{_PFX}Total markets now in file: {len(processed_market_ids)}")
