"""
Import poly_data CSVs into polymarket-ai-v2 PostgreSQL.
Run after: cd poly_data && python update_all.py

Usage:
  python scripts/import_poly_data_to_db.py [--poly-data-dir PATH] [--markets-only] [--trades-only]
"""
import argparse
import asyncio
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
_PFX = "[poly_data:import] "
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
os.chdir(_project_root)

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")


def _naive_utc(dt):
    """Strip timezone to naive UTC for PostgreSQL."""
    if dt is None:
        return None
    if hasattr(dt, "tzinfo") and dt.tzinfo:
        return dt.replace(tzinfo=None)
    return dt


async def import_markets(db, poly_dir: Path) -> int:
    """Import markets from markets.csv and missing_markets.csv."""
    import pandas as pd

    dfs = []
    dtype_overrides = {
        "token1": str,
        "token2": str,
        "neg_risk": object,
        "resolved": object,
        "liquidity": object,
        "volume": object,
        "closedTime": object,
    }
    for fname in ["markets.csv", "missing_markets.csv"]:
        path = poly_dir / fname
        if path.exists():
            df = pd.read_csv(path, dtype=dtype_overrides, low_memory=False)
            dfs.append(df)
    if not dfs:
        print(f"{_PFX}No market CSVs found")
        return 0

    combined = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["id"], keep="first")

    markets_data = []
    for _, row in combined.iterrows():
        closed_time = row.get("closedTime")
        if pd.notna(closed_time) and closed_time:
            try:
                end_dt = pd.to_datetime(closed_time, utc=True)
                end_dt = _naive_utc(end_dt.to_pydatetime())
            except Exception:
                end_dt = None
        else:
            end_dt = None

        resolved = bool(row.get("resolved", False))
        resolution = row.get("resolution")
        if pd.notna(resolution) and resolution:
            resolution = str(resolution).strip().upper()
            if resolution not in ("YES", "NO"):
                resolution = None
        else:
            resolution = None

        try:
            vol = float(row.get("volume", 0) or 0)
        except (TypeError, ValueError):
            vol = 0.0
        try:
            liq = float(row.get("liquidity", 0) or 0)
        except (TypeError, ValueError):
            liq = 0.0

        slug = str(row.get("market_slug", "") or "").strip()
        if not slug and row.get("id"):
            slug = str(row["id"])

        md = {
            "id": str(row["id"]).strip(),
            "condition_id": str(row.get("condition_id", "") or ""),
            "question": str(row.get("question", "") or ""),
            "description": "",
            "slug": slug or str(row["id"]),
            "category": str(row.get("category", "") or "")[:100],
            "resolution_source": "",
            "end_date_iso": end_dt,
            "image": None,
            "active": not resolved,
            "liquidity": liq,
            "volume": vol,
            "resolved": resolved,
            "resolution": resolution,
            "resolved_at": end_dt if resolved else None,
            "yes_token_id": str(row.get("token1", "") or "").strip() or None,
            "no_token_id": str(row.get("token2", "") or "").strip() or None,
            "yes_price": None,
            "no_price": None,
            "outcome_prices": None,
        }
        if md["yes_token_id"] or md["no_token_id"]:
            markets_data.append(md)

    if not markets_data:
        print(f"{_PFX}No valid markets to import")
        return 0

    await db.bulk_insert_markets(markets_data)
    print(f"{_PFX}Imported {len(markets_data)} markets")
    return len(markets_data)


async def import_trades(db, poly_dir: Path) -> int:
    """Import trades from processed/trades.csv. Maps poly_data format to DB format."""
    import pandas as pd

    trades_path = poly_dir / "processed" / "trades.csv"
    if not trades_path.exists():
        print(f"{_PFX}No processed/trades.csv found. Run poly_data update_all.py first.")
        return 0

    # Load markets for token_id mapping (nonusdc_side -> token1/token2)
    markets_path = poly_dir / "markets.csv"
    missing_path = poly_dir / "missing_markets.csv"
    mdfs = []
    for p in [markets_path, missing_path]:
        if p.exists():
            mdfs.append(pd.read_csv(p, dtype={"token1": str, "token2": str}))
    if not mdfs:
        print(f"{_PFX}No markets.csv for token mapping")
        return 0

    markets_df = pd.concat(mdfs).drop_duplicates(subset=["id"], keep="first")
    market_lookup = {}
    for _, m in markets_df.iterrows():
        mid = str(m["id"])
        t1 = str(m.get("token1", "") or "")
        t2 = str(m.get("token2", "") or "")
        entry = {"token1": t1, "token2": t2, "id": mid}
        market_lookup[mid] = entry
        cid = str(m.get("condition_id", "") or "").strip()
        if cid and cid not in market_lookup:
            market_lookup[cid] = entry
        slug = str(m.get("market_slug", "") or m.get("slug", "") or "").strip()
        if slug and slug not in market_lookup:
            market_lookup[slug] = entry

    df = pd.read_csv(trades_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    total_rows = len(df)

    trades_data = []
    seen_ids = set()
    user_addresses = set()
    skipped_no_market = 0
    skipped_bad_data = 0
    unmapped_market_ids: set = set()

    for _, row in df.iterrows():
        market_id = str(row.get("market_id", "") or "").strip()
        if not market_id:
            unmapped_market_ids.add("(empty)")
            skipped_no_market += 1
            continue
        # Phase 3 PD1: id_resolver fallback when market_lookup (CSV) misses
        if market_id not in market_lookup and db and getattr(db, "session_factory", None):
            try:
                from base_engine.data.id_resolver import resolve_market_id
                canonical = await resolve_market_id(db, market_id)
                if canonical:
                    markets = await db.get_markets_with_token_ids([canonical])
                    if markets:
                        m = markets[0]
                        market_lookup[market_id] = {
                            "id": m["id"],
                            "token1": m.get("yes_token_id"),
                            "token2": m.get("no_token_id"),
                        }
            except Exception:
                pass
        if market_id not in market_lookup:
            unmapped_market_ids.add(market_id)
            skipped_no_market += 1
            continue

        tokens = market_lookup[market_id]
        canonical_id = tokens.get("id", market_id)
        nonusdc = str(row.get("nonusdc_side", "") or "")
        token_id = tokens.get("token1") if nonusdc == "token1" else tokens.get("token2")
        if not token_id:
            skipped_bad_data += 1
            continue

        # side: token1=YES, token2=NO (convention)
        side = "YES" if nonusdc == "token1" else "NO"

        # user_address: the one who traded the outcome (had non-USDC)
        maker = str(row.get("maker", "") or "")
        taker = str(row.get("taker", "") or "")
        user_address = maker if nonusdc == "maker_direction" else taker
        # Actually: maker has makerAsset, taker has takerAsset. The one with non-USDC is the outcome trader.
        maker_dir = str(row.get("maker_direction", "") or "")
        taker_dir = str(row.get("taker_direction", "") or "")
        # If maker bought (BUY) the outcome, maker is outcome trader. If taker bought, taker is.
        # maker_direction/taker_direction: BUY = bought outcome, SELL = sold outcome
        # The outcome trader is the one who had the token side. So who traded the token?
        # In a fill: one side is USDC, one is token. Maker provides one side, taker the other.
        # The one who received/sold the token is the outcome trader. Simpler: use maker as primary (poly_data docs say filter by maker for user trades)
        user_address = maker
        user_addresses.add(user_address)

        price = float(row.get("price", 0) or 0)
        if price < 0 or price > 1:
            skipped_bad_data += 1
            continue
        size = float(row.get("token_amount", 0) or 0)
        if size < 0:
            skipped_bad_data += 1
            continue

        ts = row["timestamp"]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        ts = _naive_utc(ts)

        tx_hash = str(row.get("transactionHash", "") or "")
        raw = f"{tx_hash}|{maker}|{taker}|{ts}"
        trade_id = hashlib.sha256(raw.encode()).hexdigest()[:64]
        if trade_id in seen_ids:
            continue
        seen_ids.add(trade_id)

        trades_data.append({
            "id": trade_id,
            "market_id": canonical_id,
            "token_id": token_id,
            "user_address": user_address,
            "bot_id": None,
            "side": side,
            "size": size,
            "price": price,
            "pnl": None,
            "entry_time": None,
            "exit_time": None,
            "timestamp": ts,
        })

    if not trades_data:
        print(f"{_PFX}No valid trades to import")
        return 0

    if skipped_no_market or skipped_bad_data:
        print(f"{_PFX}Skipped: {skipped_no_market} (market not in lookup), {skipped_bad_data} (bad price/size)")
        if unmapped_market_ids:
            sample = list(unmapped_market_ids)[:10]
            print(f"{_PFX}Skipped {skipped_no_market} trades with unmapped market_id. Sample: {sample}. Run update_all.py again (cap: 300).")
    print(f"{_PFX}Importing {len(trades_data)} trades (from {total_rows} rows in trades.csv)")
    await db.bulk_insert_trades(trades_data)
    print(f"{_PFX}Imported {len(trades_data)} trades")
    if total_rows > 0 and len(trades_data) < 0.9 * total_rows:
        print(f"{_PFX}WARNING: Imported {len(trades_data)} of {total_rows} rows ({100*len(trades_data)/total_rows:.1f}%). Check unmapped markets and price filter.")

    # Ensure users exist for elite_detector
    from base_engine.data.database import User
    async with db.get_session() as session:
        for addr in user_addresses:
            if addr:
                await session.merge(User(address=addr))
        await session.commit()

    return len(trades_data)


async def derive_ohlc_from_trades(db) -> int:
    """
    Derive 1-hour OHLC candles from trades table and insert into market_prices.
    Provides fallback when legacy CLOB ingestion is down. Idempotent (ON CONFLICT DO NOTHING).
    """
    from sqlalchemy import text

    async with db.get_session() as session:
        # Aggregate trades by (market_id, token_id, hour), use close price (last trade of hour)
        ohlc_sql = text("""
            WITH hourly AS (
                SELECT
                    market_id,
                    token_id,
                    side,
                    date_trunc('hour', timestamp) AS hour_ts,
                    array_agg(price ORDER BY timestamp, id) AS prices
                FROM trades
                WHERE price IS NOT NULL AND price BETWEEN 0 AND 1
                GROUP BY market_id, token_id, side, date_trunc('hour', timestamp)
            ),
            hourly_with_close AS (
                SELECT market_id, token_id, side, hour_ts,
                    prices[array_length(prices, 1)] AS close_price
                FROM hourly
            )
            INSERT INTO market_prices (market_id, token_id, price, side, timestamp, partition_month)
            SELECT
                market_id,
                token_id,
                close_price,
                side,
                hour_ts,
                to_char(hour_ts, 'YYYY-MM')
            FROM hourly_with_close
            ON CONFLICT (market_id, token_id, timestamp) DO NOTHING
        """)
        try:
            await session.execute(ohlc_sql)
            await session.commit()
            print(f"{_PFX}OHLC derived from trades -> market_prices (idempotent)")
            return 1  # Success indicator
        except Exception as e:
            await session.rollback()
            if "uq_market_prices" in str(e).lower() or "unique" in str(e).lower():
                print(f"{_PFX}OHLC skip: unique constraint missing? Run schema/add_market_prices_unique_constraint.sql: {e}")
            else:
                raise
    return 0


async def main():
    parser = argparse.ArgumentParser(description="Import poly_data CSVs to PostgreSQL")
    parser.add_argument(
        "--poly-data-dir",
        type=Path,
        default=_project_root / "poly_data",
        help="Path to poly_data directory (default: polymarket-ai-v2/poly_data)",
    )
    parser.add_argument("--markets-only", action="store_true", help="Import only markets")
    parser.add_argument("--trades-only", action="store_true", help="Import only trades")
    parser.add_argument("--skip-ohlc", action="store_true", help="Skip OHLC derivation from trades")
    parser.add_argument(
        "--fetch-historical-prices",
        action="store_true",
        dest="fetch_historical_prices",
        default=True,
        help="When trades=0 and markets>0, fetch historical prices for training fallback (default: True)",
    )
    parser.add_argument(
        "--no-fetch-historical-prices",
        action="store_false",
        dest="fetch_historical_prices",
        help="Skip historical price fetch when trades=0",
    )
    args = parser.parse_args()

    poly_dir = args.poly_data_dir.resolve()
    if not poly_dir.exists():
        print(f"{_PFX}poly_data dir not found: {poly_dir}")
        print(f"{_PFX}Run: cd poly_data && python update_all.py")
        sys.exit(1)

    from base_engine.data.database import Database

    db = Database()
    await db.init()
    if not db.session_factory:
        print(f"{_PFX}Database not initialized. Set DATABASE_URL.")
        sys.exit(1)

    total_m = 0
    total_t = 0
    ohlc_count = 0
    prices_fetched = 0
    if not args.trades_only:
        total_m = await import_markets(db, poly_dir)
    if not args.markets_only:
        total_t = await import_trades(db, poly_dir)
        if total_t > 0 and not args.skip_ohlc:
            ohlc_count = await derive_ohlc_from_trades(db)

    # When no trades but we have markets: fetch historical prices for training fallback
    if (
        args.fetch_historical_prices
        and total_t == 0
        and total_m > 0
        and not args.trades_only
    ):
        print(f"{_PFX}Trades=0; fetching historical prices for training fallback...")
        try:
            from base_engine.data.polymarket_client import PolymarketClient
            from base_engine.data.data_ingestion import DataIngestionService

            client = PolymarketClient()
            service = DataIngestionService(client=client, db=db)
            to_ts = int(datetime.now(timezone.utc).timestamp())
            from_ts = to_ts - (365 * 24 * 60 * 60)
            async with client:
                # CRITICAL: use_resolved_for_training_fallback=True - get_markets_for_price_ingestion
                # returns active=TRUE only. Training fallback needs RESOLVED markets with prices.
                result = await service.ingest_historical_prices(
                    market_ids=None,
                    from_timestamp=from_ts,
                    to_timestamp=to_ts,
                    max_markets=min(100, total_m),
                    use_resolved_for_training_fallback=True,
                )
            diag = result.get("diagnostics", {})
            prices_fetched = diag.get("prices_ingested", 0) or 0
            if prices_fetched > 0:
                print(f"{_PFX}Fetched {prices_fetched} historical prices for training")
            else:
                print(f"{_PFX}Historical price fetch completed (0 new prices; may need CLOB API access)")
        except Exception as e:
            print(f"{_PFX}Historical price fetch failed (non-fatal): {e}")

    ohlc_msg = "derived" if ohlc_count else ("skipped" if args.skip_ohlc and not args.markets_only else "-")
    print(f"\n{_PFX}Done. Markets: {total_m}, Trades: {total_t}, OHLC: {ohlc_msg}, Prices: {prices_fetched}")
    print(f"{_PFX}Run update_elite_status and retrain for bot learning.")


if __name__ == "__main__":
    asyncio.run(main())
