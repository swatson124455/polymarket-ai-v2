"""
Fetch markets from Polymarket Gamma API with extended fields for bot learning.
Extended from warproxxx/poly_data: adds resolved, resolution, liquidity, category.
"""
import csv

_PFX = "[poly_data] "
import json
import os
import time
from typing import List

import requests

BASE_URL = "https://gamma-api.polymarket.com/markets"

# Extended headers for bot learning (resolution, liquidity, category)
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


def count_csv_lines(csv_filename: str) -> int:
    """Count the number of data lines in CSV (excluding header)."""
    if not os.path.exists(csv_filename):
        return 0
    try:
        with open(csv_filename, "r", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            next(reader, None)
            return sum(1 for row in reader if row)
    except Exception as e:
        print(f"{_PFX}Error reading CSV: {e}")
        return 0


def _parse_resolution(market: dict) -> tuple:
    """Extract resolved flag and resolution (YES/NO) from Gamma API response."""
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
    # Infer from outcomePrices when closed but resolution missing
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
    return (bool(resolved), resolution)


def _parse_liquidity(market: dict) -> float:
    """Extract liquidity as float."""
    val = market.get("liquidity", 0) or 0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _parse_category(market: dict) -> str:
    """Extract category from market or events."""
    cat = market.get("category", "")
    if not cat and market.get("events"):
        ev = market["events"][0] if market["events"] else {}
        cat = ev.get("category", "") or ev.get("groupItemTitle", "")
    if isinstance(cat, list):
        cat = cat[0] if cat else ""
    return str(cat)[:100] if cat else ""


def update_markets(
    csv_filename: str = "markets.csv",
    batch_size: int = 500,
    max_new_markets: int | None = None,
) -> None:
    """
    Fetch markets ordered by creation date and save to CSV.
    Automatically resumes from the correct offset based on existing CSV lines.
    Extended with resolved, resolution, liquidity, category for bot learning.

    Args:
        csv_filename: Output CSV path.
        batch_size: Markets per API request.
        max_new_markets: Stop after fetching this many new markets. 0 or None = no limit.
            Set via POLY_DATA_MAX_MARKETS env var (e.g. 5000 for incremental updates).
    """
    if max_new_markets is None:
        max_new_markets = int(os.environ.get("POLY_DATA_MAX_MARKETS", "0")) or None
    if max_new_markets == 0:
        max_new_markets = None

    current_offset = count_csv_lines(csv_filename)
    file_exists = os.path.exists(csv_filename) and current_offset > 0

    if file_exists:
        print(f"{_PFX}Found {current_offset} existing records. Resuming from offset {current_offset}")
        mode = "a"
    else:
        print(f"{_PFX}Creating new CSV file: {csv_filename}")
        mode = "w"

    if max_new_markets:
        print(f"{_PFX}Limit: fetch at most {max_new_markets} new markets (POLY_DATA_MAX_MARKETS)")

    total_fetched = 0

    with open(csv_filename, mode, newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        if mode == "w":
            writer.writerow(HEADERS)

        while True:
            print(f"{_PFX}Fetching batch at offset {current_offset}...")
            try:
                params = {
                    "order": "createdAt",
                    "ascending": "true",
                    "limit": batch_size,
                    "offset": current_offset,
                }
                response = requests.get(BASE_URL, params=params, timeout=30)

                if response.status_code == 500:
                    print(f"{_PFX}Server error (500) - retrying in 5 seconds...")
                    time.sleep(5)
                    continue
                if response.status_code == 429:
                    print(f"{_PFX}Rate limited (429) - waiting 10 seconds...")
                    time.sleep(10)
                    continue
                if response.status_code != 200:
                    print(f"{_PFX}API error {response.status_code}: {response.text}")
                    print(f"{_PFX}Retrying in 3 seconds...")
                    time.sleep(3)
                    continue

                markets = response.json()
                if not markets:
                    print(f"{_PFX}No more markets found at offset {current_offset}. Completed!")
                    break

                batch_count = 0
                for market in markets:
                    try:
                        outcomes_str = market.get("outcomes", "[]")
                        outcomes = (
                            json.loads(outcomes_str)
                            if isinstance(outcomes_str, str)
                            else outcomes_str
                        )
                        answer1 = outcomes[0] if len(outcomes) > 0 else ""
                        answer2 = outcomes[1] if len(outcomes) > 1 else ""

                        clob_str = market.get("clobTokenIds", "[]")
                        clob_tokens = (
                            json.loads(clob_str) if isinstance(clob_str, str) else clob_str
                        )
                        token1 = clob_tokens[0] if len(clob_tokens) > 0 else ""
                        token2 = clob_tokens[1] if len(clob_tokens) > 1 else ""

                        neg_risk = market.get("negRiskAugmented", False) or market.get(
                            "negRiskOther", False
                        )
                        question_text = market.get("question", "") or market.get("title", "")
                        ticker = ""
                        if market.get("events") and len(market.get("events", [])) > 0:
                            ticker = market["events"][0].get("ticker", "")

                        resolved, resolution = _parse_resolution(market)
                        liquidity = _parse_liquidity(market)
                        category = _parse_category(market)

                        row = [
                            market.get("createdAt", ""),
                            market.get("id", ""),
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
                            resolution or "",
                            liquidity,
                            category,
                        ]
                        writer.writerow(row)
                        batch_count += 1
                    except (ValueError, KeyError, json.JSONDecodeError) as e:
                        print(f"{_PFX}Error processing market {market.get('id', 'unknown')}: {e}")
                        continue

                total_fetched += batch_count
                current_offset += batch_count
                print(
                    f"{_PFX}Processed {batch_count} markets. Total new: {total_fetched}. Next offset: {current_offset}"
                )

                if max_new_markets and total_fetched >= max_new_markets:
                    print(f"{_PFX}Reached limit of {max_new_markets} new markets. Stopping.")
                    break

                if len(markets) < batch_size:
                    print(
                        f"{_PFX}Received only {len(markets)} markets (less than batch size). Reached end."
                    )
                    break

            except requests.exceptions.RequestException as e:
                print(f"{_PFX}Network error: {e}")
                print(f"{_PFX}Retrying in 5 seconds...")
                time.sleep(5)
                continue
            except Exception as e:
                print(f"{_PFX}Unexpected error: {e}")
                print(f"{_PFX}Retrying in 3 seconds...")
                time.sleep(3)
                continue

    print(f"\n{_PFX}Completed! Fetched {total_fetched} new markets.")
    print(f"{_PFX}Data saved to: {csv_filename}")
    print(f"{_PFX}Total records: {current_offset}")
