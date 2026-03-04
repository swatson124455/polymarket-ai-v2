import asyncio, sys
sys.path.insert(0, '/opt/polymarket-ai-v2')
import os; os.chdir('/opt/polymarket-ai-v2')

async def main():
    from dotenv import load_dotenv
    load_dotenv('/opt/polymarket-ai-v2/.env')
    import warnings; warnings.filterwarnings('ignore')
    import structlog
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(40))

    from base_engine.data.database import Database
    from base_engine.learning.learning_engine import LearningEngine
    from base_engine.prediction.prediction_engine import PredictionEngine

    db = Database()
    await db.init()
    le = LearningEngine(db)
    await le.init()
    pe = PredictionEngine(db, le)
    await pe.init()

    # Check what get_all_tradeable_markets returns
    from base_engine.data.unified_market_service import UnifiedMarketService
    ums = UnifiedMarketService(db, None, None)
    markets = await ums.get_all_tradeable_markets(min_liquidity=100.0)

    print("get_all_tradeable_markets returned " + str(len(markets)) + " markets")
    print("")
    print("Top 15 markets (as returned, unsliced):")
    for i, m in enumerate(markets[:15]):
        yes_p = float(m.get('yes_price') or 0)
        no_p = float(m.get('no_price') or 0)
        vol = float(m.get('volume') or m.get('volumeNum') or 0)
        liq = float(m.get('liquidity') or 0)
        q = str(m.get('question') or m.get('title') or '')[:60]
        print(str(i+1) + ". id=" + str(m.get('id')) + " YES=" + str(round(yes_p,4)) + " NO=" + str(round(no_p,4)) + " vol=" + str(round(vol)) + " liq=" + str(round(liq)) + " | " + q)

    print("")
    print("Distribution of YES prices in returned 50 markets:")
    if markets:
        prices = [float(m.get('yes_price') or 0) for m in markets[:50]]
        below5 = sum(1 for p in prices if p < 0.05)
        above95 = sum(1 for p in prices if p > 0.95)
        tradeable = sum(1 for p in prices if 0.05 <= p <= 0.95)
        print("  below 5%: " + str(below5))
        print("  tradeable (5-95%): " + str(tradeable))
        print("  above 95%: " + str(above95))

    # Check pe.predict signature
    import inspect
    sig = inspect.signature(pe.predict)
    print("")
    print("PredictionEngine.predict() signature: " + str(sig))

    await db.close()

asyncio.run(main())
