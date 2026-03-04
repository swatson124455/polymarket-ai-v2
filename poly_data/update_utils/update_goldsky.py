"""
Scrape order-filled events from Goldsky subgraph.
Windows-compatible: uses pandas instead of tail/head for cursor resume.
"""
import json
import os

_PFX = "[poly_data] "
import time
from datetime import datetime, timezone

import pandas as pd
from flatten_json import flatten
from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport

RUNTIME_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
COLUMNS_TO_SAVE = [
    "timestamp",
    "maker",
    "makerAssetId",
    "makerAmountFilled",
    "taker",
    "takerAssetId",
    "takerAmountFilled",
    "transactionHash",
]
QUERY_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn"

# Ensure goldsky dir exists (handles cwd edge cases)
def _ensure_goldsky_dir():
    os.makedirs("goldsky", exist_ok=True)

CURSOR_FILE = "goldsky/cursor_state.json"


def save_cursor(timestamp: int, last_id: str | None, sticky_timestamp: int | None = None) -> None:
    """Save cursor state to file for efficient resume."""
    state = {
        "last_timestamp": timestamp,
        "last_id": last_id,
        "sticky_timestamp": sticky_timestamp,
    }
    with open(CURSOR_FILE, "w") as f:
        json.dump(state, f)


def get_latest_cursor_safe() -> tuple[int, str | None, int | None] | None:
    """
    Get cursor state with explicit error handling. Re-raises on corruption.
    Returns (timestamp, last_id, sticky_timestamp) or None if file not found.
    """
    if not os.path.isfile(CURSOR_FILE):
        return None

    try:
        with open(CURSOR_FILE, "r") as f:
            state = json.load(f)
    except FileNotFoundError:
        # Race: file removed between isfile check and open. Match "file not found" path.
        return None
    except json.JSONDecodeError as e:
        print(f"{_PFX}ERROR: Corrupt cursor file {CURSOR_FILE}: {e}")
        raise RuntimeError(f"Corrupt cursor file. Delete {CURSOR_FILE} and re-run.") from e

    timestamp = state.get("last_timestamp", 0)
    last_id = state.get("last_id")
    sticky_timestamp = state.get("sticky_timestamp")

    if not isinstance(timestamp, (int, float)) or timestamp < 0:
        raise ValueError(f"Invalid cursor timestamp: {timestamp}")
    now_ts = datetime.now(timezone.utc).timestamp()
    if timestamp > now_ts + 3600:
        raise ValueError(f"Cursor timestamp {timestamp} is in future")

    if sticky_timestamp is not None and last_id is None:
        print(f"{_PFX}Warning: Invalid cursor state (sticky_timestamp without last_id), clearing")
        sticky_timestamp = None

    if timestamp > 0:
        readable = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"{_PFX}\nResuming from cursor: timestamp {timestamp} ({readable}), id: {last_id}, sticky: {sticky_timestamp}")
    return timestamp, last_id, sticky_timestamp


def get_latest_cursor() -> tuple[int, str | None, int | None]:
    """
    Get the latest cursor state for efficient resume.
    Uses get_latest_cursor_safe; falls back to CSV when cursor file missing.
    """
    try:
        result = get_latest_cursor_safe()
        if result is not None:
            return result
    except (RuntimeError, ValueError):
        raise

    cache_file = "goldsky/orderFilled.csv"
    if not os.path.isfile(cache_file):
        print(f"{_PFX}No existing file found, starting from beginning of time (timestamp 0)")
        return 0, None, None

    try:
        df = pd.read_csv(cache_file)
        if len(df) > 0 and "timestamp" in df.columns:
            last_timestamp = int(df.iloc[-1]["timestamp"])
            readable = datetime.fromtimestamp(last_timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"{_PFX}\nResuming from CSV (no cursor file): timestamp {last_timestamp} ({readable})")
            return last_timestamp - 1, None, None
    except Exception as e:
        print(f"{_PFX}\nError reading CSV: {e}")

    print(f"{_PFX}Falling back to beginning of time (timestamp 0)")
    return 0, None, None


def scrape(at_once: int = 1000) -> None:
    """Scrape orderFilledEvents from Goldsky subgraph."""
    print(f"{_PFX}Query URL: {QUERY_URL}")
    print(f"{_PFX}Runtime timestamp: {RUNTIME_TIMESTAMP}")
    last_timestamp, last_id, sticky_timestamp = get_latest_cursor()
    count = 0
    total_records = 0
    output_file = "goldsky/orderFilled.csv"
    print(f"\n{_PFX}Starting scrape for orderFilledEvents")
    print(f"{_PFX}Output file: {output_file}")
    print(f"{_PFX}Saving columns: {COLUMNS_TO_SAVE}")

    while True:
        if sticky_timestamp is not None:
            where_clause = f'timestamp: "{sticky_timestamp}", id_gt: "{last_id}"'
        else:
            where_clause = f'timestamp_gt: "{last_timestamp}"'

        q_string = f"""
        query MyQuery {{
            orderFilledEvents(orderBy: timestamp, orderDirection: asc, first: {at_once}, where: {{{where_clause}}}) {{
                fee
                id
                maker
                makerAmountFilled
                makerAssetId
                orderHash
                taker
                takerAmountFilled
                takerAssetId
                timestamp
                transactionHash
            }}
        }}
        """
        query = gql(q_string)
        transport = RequestsHTTPTransport(url=QUERY_URL, verify=True, retries=3)
        client = Client(transport=transport)

        try:
            res = client.execute(query)
        except Exception as e:
            print(f"{_PFX}\nQuery error: {e}")
            print(f"{_PFX}Retrying in 5 seconds...")
            time.sleep(5)
            continue

        events = res.get("orderFilledEvents") or []
        if not events:
            if sticky_timestamp is not None:
                last_timestamp = sticky_timestamp
                sticky_timestamp = None
                last_id = None
                continue
            print(f"{_PFX}No more data for orderFilledEvents")
            break

        df = pd.DataFrame([flatten(x) for x in events]).reset_index(drop=True)
        df = df.sort_values(["timestamp", "id"], ascending=True).reset_index(drop=True)

        batch_last_timestamp = int(df.iloc[-1]["timestamp"])
        batch_last_id = df.iloc[-1]["id"]
        batch_first_timestamp = int(df.iloc[0]["timestamp"])
        readable = datetime.fromtimestamp(batch_last_timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        if len(df) >= at_once:
            if batch_first_timestamp == batch_last_timestamp:
                sticky_timestamp = batch_last_timestamp
                last_id = batch_last_id
                print(f"{_PFX}\nBatch {count + 1}: Timestamp {batch_last_timestamp} ({readable}), Records: {len(df)} [STICKY - continuing at same timestamp]")
            else:
                sticky_timestamp = batch_last_timestamp
                last_id = batch_last_id
                print(f"{_PFX}\nBatch {count + 1}: Timestamps {batch_first_timestamp}-{batch_last_timestamp} ({readable}), Records: {len(df)} [STICKY - ensuring complete timestamp]")
        else:
            if sticky_timestamp is not None:
                last_timestamp = sticky_timestamp
                sticky_timestamp = None
                last_id = None
                print(f"{_PFX}\nBatch {count + 1}: Timestamp {batch_last_timestamp} ({readable}), Records: {len(df)} [STICKY COMPLETE]")
            else:
                last_timestamp = batch_last_timestamp
                print(f"{_PFX}\nBatch {count + 1}: Last timestamp {batch_last_timestamp} ({readable}), Records: {len(df)}")

        count += 1
        total_records += len(df)
        df = df.drop_duplicates(subset=["id"])
        df_to_save = df[COLUMNS_TO_SAVE].copy()

        if os.path.isfile(output_file):
            df_to_save.to_csv(output_file, index=None, mode="a", header=False)
        else:
            df_to_save.to_csv(output_file, index=None)

        save_cursor(last_timestamp, last_id, sticky_timestamp)

        if len(df) < at_once and sticky_timestamp is None:
            break

    if os.path.isfile(CURSOR_FILE):
        os.remove(CURSOR_FILE)
    print(f"{_PFX}\nFinished scraping orderFilledEvents")
    print(f"{_PFX}\nTotal new records: {total_records}")
    print(f"{_PFX}\nOutput file: {output_file}")


def update_goldsky() -> None:
    """Run scraping for orderFilledEvents."""
    _ensure_goldsky_dir()
    print(f"{_PFX}\n\n{'='*50}")
    print(f"\n{_PFX}{'='*50}")
    print(f"{_PFX}Starting to scrape orderFilledEvents")
    print(f"{_PFX}Runtime: {RUNTIME_TIMESTAMP}")
    print(f"{_PFX}{'='*50}")
    try:
        scrape()
        print(f"{_PFX}Successfully completed orderFilledEvents")
    except Exception as e:
        print(f"{_PFX}Error scraping orderFilledEvents: {str(e)}")
        raise
