"""
Run historical data ingestion (markets + prices). Single entry point for data pipeline.
Usage:
  python scripts/run_ingestion_standalone.py --pull-all [--markets N] [--days N] [--prices N]
  python scripts/run_ingestion_standalone.py --validate-only   # Pre-checks
  python scripts/run_ingestion_standalone.py --top N          # Markets only
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Fix Windows UnicodeEncodeError when logging market data with special chars
if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding and "utf" not in (sys.stdout.encoding or "").lower():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is on path when run as script
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
os.chdir(_project_root)

# Load env before importing app code
from dotenv import load_dotenv
load_dotenv(_project_root / ".env")


async def run_markets_only(top_markets: int) -> int:
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient
    from base_engine.data.data_ingestion import DataIngestionService
    from base_engine.data.database_lock import acquire_lock

    db = Database()
    await db.init()
    if not db.session_factory:
        return -1
    client = PolymarketClient()
    service = DataIngestionService(client=client, db=db)
    async with acquire_lock(db, "ingestion"):
        return await service.ingest_all_markets(
            progress_callback=lambda _: None,
            top_markets_count=top_markets,
        )


async def _clear_stuck_sync(db) -> None:
    """Clear any stuck 'running' sync_log entries so ingestion can proceed."""
    if db and hasattr(db, "clear_stuck_sync_running"):
        n = await db.clear_stuck_sync_running(component="data_ingestion")
        if n > 0:
            print(f"[INFO] Cleared {n} stuck sync_log entry(ies)", file=sys.stderr)


async def run_pull_all(markets: int, days: int, prices: int) -> dict:
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient
    from base_engine.data.data_ingestion import DataIngestionService
    from base_engine.data.database_lock import acquire_lock

    db = Database()
    await db.init()
    if not db.session_factory:
        return {"success": False, "error": "Database not initialized (DATABASE_URL?)"}
    await _clear_stuck_sync(db)
    client = PolymarketClient()
    service = DataIngestionService(client=client, db=db)
    async with acquire_lock(db, "ingestion"):
        return await service.ingest_everything(
            top_markets_count=markets,
            days_back=days,
            max_markets_prices=prices,
            progress_callback=lambda _: None,
        )


async def run_historical_prices(
    market_ids: list,
    from_ts: int,
    to_ts: int,
    max_markets: int,
) -> dict:
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient
    from base_engine.data.data_ingestion import DataIngestionService

    db = Database()
    await db.init()
    if not db.session_factory:
        return {"success": False, "error": "Database not initialized (DATABASE_URL?)"}
    client = PolymarketClient()
    service = DataIngestionService(client=client, db=db)
    return await service.ingest_historical_prices(
        market_ids=market_ids if market_ids else None,
        from_timestamp=from_ts,
        to_timestamp=to_ts,
        max_markets=max_markets,
    )


async def run_validate_only() -> dict:
    """Run pre-ingestion checks only; no fetch, no DB writes."""
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient
    from base_engine.data.data_ingestion import DataIngestionService

    db = Database()
    await db.init()
    client = PolymarketClient()
    service = DataIngestionService(client=client, db=db)
    ok, err = await service._pre_ingestion_checks()
    if ok:
        return {"success": True, "message": "Pre-ingestion checks passed"}
    return {"success": False, "error": err or "Pre-ingestion checks failed"}


async def run_dry_run(mode: str, **kwargs) -> dict:
    """Run pre-ingestion checks and a minimal API fetch; no DB writes."""
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient
    from base_engine.data.data_ingestion import DataIngestionService

    db = Database()
    await db.init()
    client = PolymarketClient()
    service = DataIngestionService(client=client, db=db)
    ok, err = await service._pre_ingestion_checks()
    if not ok:
        return {"success": False, "error": err, "dry_run": True}
    # Minimal fetch to prove API is reachable
    try:
        async with client:
            markets = await client.get_markets(limit=5, offset=0, active=True)
        count = len(markets) if isinstance(markets, list) else 0
    except Exception as e:
        return {"success": False, "error": f"API fetch failed: {e}", "dry_run": True}
    return {
        "success": True,
        "message": f"DRY RUN: Pre-checks OK; would run {mode}; no DB writes",
        "dry_run": True,
        "api_sample_count": count,
    }


async def run_backfill(days: int, markets_batch: int, prices_batch: int, max_market_batches: int) -> dict:
    from base_engine.data.database import Database
    from base_engine.data.polymarket_client import PolymarketClient
    from base_engine.data.data_ingestion import DataIngestionService
    from base_engine.data.database_lock import acquire_lock

    db = Database()
    await db.init()
    if not db.session_factory:
        return {"success": False, "error": "Database not initialized (DATABASE_URL?)"}
    await _clear_stuck_sync(db)
    client = PolymarketClient()
    service = DataIngestionService(client=client, db=db)
    async with acquire_lock(db, "ingestion"):
        return await service.run_backfill(
            days_back=days,
            markets_batch_size=markets_batch,
            prices_markets_per_batch=prices_batch,
            max_market_batches=max_market_batches,
            progress_callback=lambda _: None,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=500, help="Top N markets (markets-only mode)")
    parser.add_argument("--pull-all", action="store_true", help="Run markets + historical prices (primary command)")
    parser.add_argument("--markets", type=int, default=1000, help="Markets to ingest (pull-all); align with DAILY_INGESTION_MARKETS_COUNT")
    parser.add_argument("--days", type=int, default=365, help="Days back for prices (pull-all)")
    parser.add_argument("--prices", type=int, default=1000, help="Markets to get prices for (pull-all); align with DAILY_INGESTION_PRICES_MARKETS")
    parser.add_argument("--backfill", action="store_true", help="One-time backfill: markets (active+closed) then 1-year prices in batches")
    parser.add_argument("--backfill-days", type=int, default=365, help="Days back for backfill prices")
    parser.add_argument("--backfill-markets-batch", type=int, default=100, help="Markets per market batch (backfill)")
    parser.add_argument("--backfill-prices-batch", type=int, default=50, help="Markets per price batch (backfill)")
    parser.add_argument("--backfill-market-batches", type=int, default=1, help="Number of market ingestion batches (backfill)")
    parser.add_argument("--historical", action="store_true", help="Historical prices only")
    parser.add_argument("--market-ids", type=str, default="", help="Comma-separated market IDs (historical)")
    parser.add_argument("--from-ts", type=int, default=0, help="From timestamp (historical)")
    parser.add_argument("--to-ts", type=int, default=0, help="To timestamp (historical)")
    parser.add_argument("--max-markets", type=int, default=50, help="Max markets (historical)")
    parser.add_argument("--dry-run", action="store_true", help="Run pre-checks + minimal API fetch; no DB writes")
    parser.add_argument("--validate-only", action="store_true", help="Run pre-ingestion checks only; no fetch, no DB writes")
    parser.add_argument("--clear-stuck", action="store_true", help="Clear stuck sync_log entries and exit")
    args = parser.parse_args()
    try:
        if args.clear_stuck:
            async def _do_clear():
                from base_engine.data.database import Database
                db = Database()
                await db.init()
                n = await db.clear_stuck_sync_running(component="data_ingestion") if db.session_factory else 0
                return {"cleared": n}
            result = asyncio.run(_do_clear())
            print(json.dumps(result))
            sys.exit(0)
        if args.validate_only:
            result = asyncio.run(run_validate_only())
            print(json.dumps(result))
            sys.exit(0 if result.get("success") else 1)
        if args.dry_run:
            mode = "backfill" if args.backfill else "pull-all" if args.pull_all else "markets-only"
            result = asyncio.run(run_dry_run(mode))
            print(json.dumps(result))
            sys.exit(0 if result.get("success") else 1)
        if args.backfill:
            result = asyncio.run(run_backfill(
                days=args.backfill_days,
                markets_batch=args.backfill_markets_batch,
                prices_batch=args.backfill_prices_batch,
                max_market_batches=args.backfill_market_batches,
            ))
            print(json.dumps(result))
            ok = result.get("success", False)
            if not ok:
                print(f"ERROR: {result.get('error', 'Unknown error')}", file=sys.stderr)
                sys.exit(1)
            markets_ingested = result.get("markets_ingested", 0) or 0
            if markets_ingested <= 0:
                print("ERROR: Phase 1 ingested 0 markets", file=sys.stderr)
                sys.exit(1)
            sys.exit(0)
        if args.historical:
            market_ids = [x.strip() for x in args.market_ids.split(",") if x.strip()] or None
            result = asyncio.run(run_historical_prices(
                market_ids=market_ids or [],
                from_ts=args.from_ts,
                to_ts=args.to_ts,
                max_markets=args.max_markets,
            ))
            print(json.dumps(result))
            sys.exit(0 if result.get("success") else 1)
        elif args.pull_all:
            result = asyncio.run(run_pull_all(args.markets, args.days, args.prices))
            print(json.dumps(result))
            ok = result.get("success", False)
            if not ok:
                print(f"ERROR: {result.get('error', 'Unknown error')}", file=sys.stderr)
                sys.exit(1)
            phase1 = result.get("phase1_count", 0) or 0
            if phase1 <= 0:
                print("ERROR: Phase 1 ingested 0 markets", file=sys.stderr)
                sys.exit(1)
            sys.exit(0)
        else:
            count = asyncio.run(run_markets_only(args.top))
            print(count)
            sys.exit(0 if count >= 0 else 1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
