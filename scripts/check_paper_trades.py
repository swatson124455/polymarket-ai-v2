"""Check paper trade history and identify trading blockers."""
import asyncio
import io
import os
import sys
import time

os.environ["SIMULATION_MODE"] = "true"
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings


def check_settings():
    s = Settings()
    print("=== SETTINGS ===")
    print(f"  SIMULATION_MODE: {s.SIMULATION_MODE}")
    print(f"  LIVE_TRADING: {s.LIVE_TRADING}")
    print(f"  ENSEMBLE_MIN_CONFIDENCE: {s.ENSEMBLE_MIN_CONFIDENCE}")
    print(f"  MIN_CONFIDENCE_THRESHOLD: {getattr(s, 'MIN_CONFIDENCE_THRESHOLD', 'N/A')}")
    print(f"  RISK_MIN_EDGE_PCT: {s.RISK_MIN_EDGE_PCT}")
    print(f"  RISK_MAX_TOTAL_EXPOSURE_USD: {getattr(s, 'RISK_MAX_TOTAL_EXPOSURE_USD', 500.0)}")
    print(f"  RISK_DAILY_LOSS_LIMIT_USD: {getattr(s, 'RISK_DAILY_LOSS_LIMIT_USD', 50.0)}")
    print(f"  BOT_ENABLED_ENSEMBLE: {getattr(s, 'BOT_ENABLED_ENSEMBLE', 'N/A')}")
    print(f"  BOT_ENABLED_ARBITRAGE: {getattr(s, 'BOT_ENABLED_ARBITRAGE', 'N/A')}")
    print(f"  BOT_ENABLED_WEATHER: {getattr(s, 'BOT_ENABLED_WEATHER', 'N/A')}")
    print(f"  BOT_ENABLED_MIRROR: {getattr(s, 'BOT_ENABLED_MIRROR', 'N/A')}")
    print()


def check_model_cache():
    print("=== MODEL CACHE ===")
    cache_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "model_cache.pkl",
    )
    if os.path.exists(cache_path):
        size = os.path.getsize(cache_path)
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        print(f"  Path: {cache_path}")
        print(f"  Size: {size:,} bytes")
        print(f"  Age: {age_hours:.1f} hours")
        if age_hours > 24:
            print("  WARNING: Model cache is STALE (>24h)")
        else:
            print("  OK: Model cache is fresh")
    else:
        print("  WARNING: No model cache — models not trained yet!")
    print()


async def check_db():
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    try:
        await db.init()
        print("=== DATABASE ===")
        print("  Connection: OK")
    except Exception as e:
        print(f"=== DATABASE ===")
        print(f"  Connection FAILED: {e}")
        return

    try:
        async with db.get_session() as s:
            # Paper trades
            r = await s.execute(text("SELECT COUNT(*) FROM paper_trades"))
            total = r.scalar()
            print(f"  Paper trades: {total}")

            if total > 0:
                r2 = await s.execute(text(
                    "SELECT pt.id, pt.bot_name, pt.market_id, pt.side, pt.size, pt.price, "
                    "pt.created_at, m.question "
                    "FROM paper_trades pt "
                    "LEFT JOIN markets m ON m.condition_id = pt.market_id OR m.id = pt.market_id "
                    "ORDER BY pt.created_at DESC LIMIT 15"
                ))
                rows = r2.fetchall()
                print(f"\n=== PAPER TRADES (recent {len(rows)}) ===")
                for row in rows:
                    q = str(row[7] or row[2] or "?")[:55].encode("ascii", "replace").decode("ascii")
                    size_val = float(row[4]) if row[4] else 0
                    price_val = float(row[5]) if row[5] else 0
                    print(f"  [{row[1] or '?':12s}] {row[3] or '?':4s} ${size_val:8.2f} @ {price_val:.4f} | {row[6]} | {q}")

            # Open positions
            r3 = await s.execute(text(
                "SELECT id, market_id, bot_name, side, size, entry_price, status, created_at "
                "FROM positions WHERE status = 'open' LIMIT 10"
            ))
            rows3 = r3.fetchall()
            print(f"\n=== OPEN POSITIONS ({len(rows3)}) ===")
            for row in rows3:
                mid = str(row[1])[:25]
                size_val = float(row[4]) if row[4] else 0
                price_val = float(row[5]) if row[5] else 0
                print(f"  bot={row[2]} side={row[3]} size=${size_val:.2f} entry={price_val:.4f} | {mid}")

            # Prediction log
            r4 = await s.execute(text("SELECT COUNT(*) FROM prediction_log"))
            pred_count = r4.scalar()
            print(f"\n=== PREDICTION LOG: {pred_count} entries ===")

            if pred_count > 0:
                # Get column names
                r_cols = await s.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'prediction_log' ORDER BY ordinal_position"
                ))
                cols = [row[0] for row in r_cols.fetchall()]
                print(f"  Columns: {cols}")

                r5 = await s.execute(text(
                    "SELECT * FROM prediction_log ORDER BY created_at DESC LIMIT 5"
                ))
                rows5 = r5.fetchall()
                for row in rows5:
                    print(f"  {row}")

            # System state
            print(f"\n=== SYSTEM STATE ===")
            try:
                r6 = await s.execute(text("SELECT key, value FROM system_state"))
                rows6 = r6.fetchall()
                if rows6:
                    for row in rows6:
                        print(f"  {row[0]}: {row[1]}")
                else:
                    print("  (empty)")
            except Exception as e:
                print(f"  Error: {e}")

            # Is main.py running? Check sync_log for recent activity
            print(f"\n=== RECENT ACTIVITY ===")
            try:
                r7 = await s.execute(text(
                    "SELECT id, status, started_at, completed_at "
                    "FROM sync_log ORDER BY started_at DESC LIMIT 3"
                ))
                rows7 = r7.fetchall()
                if rows7:
                    for row in rows7:
                        print(f"  sync: status={row[1]} started={row[2]} completed={row[3]}")
                else:
                    print("  No sync_log entries (main.py may not be running)")
            except Exception as e:
                print(f"  sync_log error: {e}")

    except Exception as e:
        print(f"  Query error: {e}")
        import traceback
        traceback.print_exc()

    try:
        await db.close()
    except Exception:
        pass


if __name__ == "__main__":
    check_settings()
    check_model_cache()
    asyncio.run(check_db())
